"""Biomek's coordinated-move model: how the Master Controller turns one `AM` into each axis's move.

Pure arithmetic, no board access. It reproduces the requests the Master Controller sends for
each `AM`. Per `AM`:

1. Each axis's distance comes from where the Master Controller last put it, not from a position
   read: per axis it keeps `(position, adjusted)` (`cpositions`), the position it last commanded
   and the one it targets for hysteresis. `adjust_delta` adds an axis's hysteresis (only the Span-8
   Z channels have any) to moves in one direction; `update_cpos` keeps the pair afterwards.
2. `get_scaling_factors`: per axis, velocity, acceleration and jerk per count of travel, the
   slowest axis winning: `max(min limit, speed/1000 x max limit) / distance`. Axes are grouped:
   each Span-8 channel's pump (D1-D8) and its Z, while that pump moves, have their own group, so
   eight channels aspirating different volumes each finish together with their own Z; everything
   else is one group, scaled by the slowest axis anywhere.
3. `get_motions_and_duration`: each axis's velocity/acceleration/jerk is its factor x its distance,
   rounded and clamped to its limits; jerk is then corrected (`correct_j`) so the S-curve's phases
   land on the board's 2.5 kHz servo ticks; pumps are rounded to their own steps. The move takes the
   longest axis's time (`motor_move_time`, from the uncorrected jerk), rounded up to the ms.
4. The Move All carries that duration + 5000 ms as its timeout.

Integer division is exact (`Fraction`) until something inexact (a float) enters, so results match
the Master Controller's logged requests to the last count.
"""

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar, Union

from pylabrobot.beckman_coulter.biomek_i7.driver.motors import Axis

Number = Union[int, Fraction, float]
CPos = Tuple[Optional[int], int]
"""An axis's `(position, adjusted)`: the position last commanded (None when unknown, as after a
force-limited move), and the position its next move is planned from."""

MOVE_ALL_MARGIN_MS = 5000
"""Added to the planned duration for the Move All's timeout, as logged."""

PARTITIONED = {f"D{n}": n for n in range(1, 9)}
PARTITIONED_Z = {f"Z{n}": n for n in range(1, 9)}


@dataclass(frozen=True)
class Motion:
  """One axis's move in a coordinated move, in counts; `jerk` None for an axis that
  takes none (trapezoidal profile, pumps)."""

  axis: str
  position: int
  velocity: int
  acceleration: int
  jerk: Optional[int]


# -- exact arithmetic ----------------------------------------------------------------------------


def _inexact(*xs: Number) -> bool:
  return any(isinstance(x, float) for x in xs)


def _min(*xs: Number) -> Number:
  m = min(xs)
  return float(m) if _inexact(*xs) else m


def _max(*xs: Number) -> Number:
  m = max(xs)
  return float(m) if _inexact(*xs) else m


def _div(x: Number, *ys: Number) -> Number:
  """`/`, exact when every argument is."""
  for y in ys:
    x = x / y if _inexact(x, y) else Fraction(x) / Fraction(y)
  return x


def _sqrt(x: Number) -> Number:
  """`sqrt`, exact for an exact perfect square, inexact otherwise."""
  if not isinstance(x, float):
    f = Fraction(x)
    if f >= 0:
      n, d = math.isqrt(f.numerator), math.isqrt(f.denominator)
      if n * n == f.numerator and d * d == f.denominator:
        return Fraction(n, d)
  return math.sqrt(float(x))


def _cbrt(x: Number) -> float:
  """x ** 0.3333333333333333, as the Master Controller computes the cube root."""
  return math.pow(float(x), 0.3333333333333333)


def _exact_round(x: Number) -> int:
  """x rounded half to even, as Python's `round`."""
  return round(x)


def range_(lo: Number, x: Number, hi: Number) -> Number:
  """x clamped to [lo, hi]."""
  return lo if x < lo else (x if x < hi else hi)


def quantize(x: Number, width: float) -> int:
  """x to the nearest multiple of width."""
  return int(round(_div(x, width)) * width)


# -- move times ------------------------------------------------------------------------------------


def _s_curve_phases(d: Number, v_max: Number, a_max: Number, j: Number) -> Tuple[Number, Number]:
  t1a = _min(_sqrt(_div(v_max, j)), _cbrt(_div(d, 2, j)))
  t1b = _div(a_max, j)
  if t1a < t1b:
    return t1a, t1a
  x = _div(a_max, 2, j)
  return t1b, _min(_div(v_max, a_max), _sqrt(x * x + _div(d, a_max)) - x)


def s_curve_move_time(d: Number, v_max: Number, a_max: Number, j: Number) -> Number:
  """Seconds for an S-curve move of d counts."""
  if d > 0 and v_max > 0 and a_max > 0 and j > 0:
    t1, t2 = _s_curve_phases(d, v_max, a_max, j)
    return _div(d, j, t1, t2) + t1 + t2
  if d == 0:
    return 0
  raise ValueError(f"s_curve_move_time: invalid input {d} {v_max} {a_max} {j}")


def trapezoid_move_time(d: Number, v_max: Number, a: Number) -> Number:
  """Seconds for a trapezoidal move of d counts."""
  if d > 0 and v_max > 0 and a > 0:
    t1a, t1b = _div(v_max, a), _sqrt(_div(d, a))
    return t1a + _div(d, v_max) if t1a < t1b else t1b * 2
  if d == 0:
    return 0
  raise ValueError(f"trapezoid_move_time: invalid input {d} {v_max} {a}")


def trapezoid_a_for_s_curve(d: Number, v_max: Number, a_max: Number, j: Number) -> Number:
  """The acceleration a trapezoidal move needs to take as long as the
  S-curve one (so an axis without jerk keeps pace with ones that have it)."""
  if not (d > 0 and v_max > 0 and a_max > 0 and j > 0):
    raise ValueError(f"trapezoid_a_for_s_curve: invalid input {d} {v_max} {a_max} {j}")
  t1, t2 = _s_curve_phases(d, v_max, a_max, j)
  return _div(j * t1 * t2, t1 + t2)


def motor_move_time(d: Number, v: Number, a: Number, j: Optional[Number]) -> Number:
  """Seconds for a motor's move."""
  return s_curve_move_time(d, v, a, j) if j else trapezoid_move_time(d, v, a)


def pump_move_time(d: Number, v_max: Number, a: Number, s: Number, c: Number) -> Number:
  """Seconds for a syringe pump move of d counts, starting at velocity s and
  ending at cutoff velocity c."""
  if d > 0 and v_max > 0 and a > 0:
    s = _min(s, v_max)
    c = _max(s, _min(c, v_max))
    tmp1 = s * s + 2 * a * d
    if tmp1 < c * c:
      return _div(_sqrt(tmp1) - s, a)
    tmp2 = _div(s * s + c * c, 2)
    p = _min(_sqrt(a * d + tmp2), v_max)
    return _div(d, p) + _div(p - s - c + _div(tmp2, p), a)
  if d == 0:
    return 0
  raise ValueError(f"pump_move_time: invalid input {d} {v_max} {a} {s} {c}")


# -- coordination (the Master Controller's coordinated move) -------------------------------------


def correct_j(d: int, v_max: int, a_max: int, j: int, jmin: int, jmax: int) -> int:
  """The jerk sent, adjusted so the S-curve's jerk phase (t1) and, when it reaches its
  maximum acceleration, its acceleration phase (t2) are whole 0.4 ms servo ticks."""

  def q(x: Number) -> float:
    return round(float(x) * 2500.0) * (1 / 2500.0)

  def correct(t1: float, t2: float) -> int:
    if t1 > 0.0 and t2 > 0.0:
      return int(range_(jmin, _exact_round(v_max / (t1 * t2)), jmax))
    return j

  t1a = _div(a_max, j)
  t1b = _sqrt(_div(v_max, j))
  t1c = _cbrt(_div(d, 2, j))
  if t1a <= t1b and t1a <= t1c:
    t2a = _div(v_max, a_max)
    x = _div(t1a, 2)
    t2b = _sqrt(x * x + _div(d, a_max)) - x
    return correct(q(t1a), q(t2a)) if t2a < t2b else j
  if t1b < t1a and t1b < t1c:
    t1 = q(t1b)
    return correct(t1, t1)
  return j


def _partition(axes: Sequence[Axis], deltas: Sequence[int], i: int) -> int:
  name = axes[i].name
  if name in PARTITIONED:
    return PARTITIONED[name]
  if name in PARTITIONED_Z:
    n = PARTITIONED_Z[name]
    for k, a in enumerate(axes):
      if a.name == f"D{n}":
        return 0 if deltas[k] == 0 else n
  return 0


def get_scaling_factors(
  axes: Sequence[Axis], deltas: Sequence[int], fspeed: float
) -> List[Optional[List[Number]]]:
  """Per moving axis, its group's [velocity, acceleration, jerk] per count
  of travel (None for an axis that does not move). Group 0's factors end as the minimum over every
  group; each other group's as the minimum of its own and group 0's (as it was before that)."""
  factors: List[List[Number]] = [[math.inf, math.inf, math.inf] for _ in range(9)]
  out: List[Optional[List[Number]]] = []
  for i, (axis, delta) in enumerate(zip(axes, deltas)):
    if delta == 0:
      out.append(None)
      continue
    d = abs(delta)
    lim = axis.limits
    f = factors[_partition(axes, deltas, i)]
    f[0] = _min(_div(_max(lim.min_velocity, fspeed * lim.max_velocity), d), f[0])
    f[1] = _min(_div(_max(lim.min_acceleration, fspeed * lim.max_acceleration), d), f[1])
    if lim.max_jerk:
      f[2] = _min(_div(_max(lim.min_jerk, fspeed * lim.max_jerk), d), f[2])
    out.append(f)
  f0 = factors[0]
  for fi in factors[1:]:
    for k in range(3):
      fi[k] = _min(f0[k], fi[k])
  for fi in factors[1:]:
    for k in range(3):
      f0[k] = _min(f0[k], fi[k])
  return out


def get_motions_and_duration(
  axes: Sequence[Axis],
  cpositions: Sequence[CPos],
  deltas: Sequence[int],
  fspeed: float,
  cutoff_velocity: int = 0,
) -> Tuple[List[Motion], int]:
  """Each moving axis's motion, and the move's duration in ms (the slowest
  axis's, rounded up). `cutoff_velocity` is the Span-8 pumps' (0 on a Multichannel)."""
  scaling = get_scaling_factors(axes, deltas, fspeed)
  motions: List[Motion] = []
  duration: Number = 0
  for i, (axis, cpos, delta) in enumerate(zip(axes, cpositions, deltas)):
    if delta == 0:
      continue
    d = abs(delta)
    factor = scaling[i]
    assert factor is not None
    k_v, k_a, k_j = factor
    lim = axis.limits

    def cvt(k: Number) -> int:
      return _exact_round(k * d)

    velocity = int(range_(lim.min_velocity, cvt(k_v), lim.max_velocity))
    k_acc = k_a if lim.max_jerk or math.isinf(k_j) else trapezoid_a_for_s_curve(1, k_v, k_a, k_j)
    acceleration = int(range_(lim.min_acceleration, cvt(k_acc), lim.max_acceleration))
    jerk = int(range_(lim.min_jerk, cvt(k_j), lim.max_jerk)) if lim.max_jerk else None
    if cpos[0] is None:
      raise ValueError(f"{axis.name}: position unknown; read it before planning a move")
    position = delta + cpos[0]
    if axis.is_pump:
      pump = axis.pump_limits
      s = pump.min_start_velocity
      velocity, acceleration = quantize(velocity, 4.0), quantize(acceleration, 10000.0)
      if delta > 0 and pump.backlash > 0:
        t = pump_move_time(d + pump.backlash, velocity, acceleration, s, s) + pump_move_time(
          pump.backlash, velocity, acceleration, s, cutoff_velocity
        )
      else:
        t = pump_move_time(d, velocity, acceleration, s, cutoff_velocity)
      motions.append(Motion(axis.name, position, velocity, acceleration, None))
    else:
      sent = (
        correct_j(d, velocity, acceleration, jerk, lim.min_jerk, lim.max_jerk)
        if jerk is not None
        else None
      )
      t = motor_move_time(d, velocity, acceleration, jerk)
      motions.append(Motion(axis.name, position, velocity, acceleration, sent))
    duration = _max(duration, t)
  return motions, math.ceil(1000 * duration)


# -- positions and hysteresis ----------------------------------------------------------------------


def adjust_delta(cpos: CPos, delta: int, hysteresis: int) -> int:
  """The distance an axis travels for a move of `delta` from its adjusted position,
  with its hysteresis added when moving in hysteresis's direction (negative hysteresis: downward
  moves)."""
  if hysteresis == 0 or delta == 0:
    return delta
  if cpos[0] is None:
    raise ValueError("position unknown; read it before planning a move")
  offset = cpos[1] - cpos[0]
  if hysteresis < 0:
    return delta + offset + hysteresis if delta < 0 else delta + offset
  return delta + offset if delta < 0 else delta + offset + hysteresis


def update_cpos(cpos: CPos, position: int, hysteresis: int) -> CPos:
  """An axis's `(position, adjusted)` once it is at `position`."""
  if hysteresis == 0:
    return position, position
  adjusted = cpos[1]
  if hysteresis < 0:
    if position < adjusted + hysteresis:
      return position, position - hysteresis
    return (position, adjusted) if position <= adjusted else (position, position)
  if position < adjusted:
    return position, position
  return (
    (position, adjusted) if position <= adjusted + hysteresis else (position, position - hysteresis)
  )


T = TypeVar("T")


def spawn_order(motions: Sequence[T]) -> List[T]:
  """The order the Master Controller's per-axis requests of one move reach the board: the motions
  reversed, taken two at a time from the end, each pair in order. Motions 1..5 go out as 1, 3 2,
  5 4. Matches every multi-axis move logged."""
  reverse = list(reversed(motions))
  pairs = [reverse[i : i + 2] for i in range(0, len(reverse), 2)]
  return [m for pair in reversed(pairs) for m in pair]


def span_targets(
  cpositions: Mapping[str, CPos], y: Optional[int] = None, span: Optional[int] = None
) -> Dict[str, int]:
  """The Span-8 channels' Y targets for a move given Biomek's `Y` (channel 1) and `S` (the span from
  channel 1 to 8), in counts, as the Master Controller's logged moves place them.
  An omitted `y` is channel 1's adjusted position, an omitted `span` the current span
  (max(Y8 - Y1, 0), adjusted); channel i goes to y + round((i - 1) * span / 7), an exact ratio
  rounded half to even.

  Raises:
    ValueError: for a negative span.
  """
  y1, y8 = cpositions["Y1"][1], cpositions["Y8"][1]
  y = y1 if y is None else y
  span = max(y8 - y1, 0) if span is None else span
  if span < 0:
    raise ValueError(f"span {span} is negative")
  return {f"Y{i}": y + round(Fraction((i - 1) * span, 7)) for i in range(1, 9)}


def plan_move(
  axes: Sequence[Axis],
  cpositions: Mapping[str, CPos],
  targets: Mapping[str, int],
  speed: int = 1000,
  cutoff_velocity: int = 0,
) -> Tuple[List[Motion], int, Dict[str, CPos]]:
  """An absolute coordinated move, as the Master Controller plans an `AM`: each target is
  where the axis's adjusted position should end. Returns the motions (axes already there are left
  out), the Move All timeout in ms (duration + 5000), and every axis's `(position, adjusted)`
  after the move.

  Raises:
    ValueError: for a speed outside 1..1000 or an unknown axis.
  """
  if not 1 <= speed <= 1000:
    raise ValueError(f"speed {speed} is outside 1..1000")
  names = {a.name for a in axes}
  unknown = set(targets) - names
  if unknown:
    raise ValueError(f"no axes named {sorted(unknown)}; have {sorted(names)}")
  cpos = [cpositions[a.name] for a in axes]
  deltas = [
    adjust_delta(c, targets[a.name] - c[1], a.hysteresis) if a.name in targets else 0
    for a, c in zip(axes, cpos)
  ]
  motions, duration = get_motions_and_duration(axes, cpos, deltas, speed / 1000.0, cutoff_velocity)
  after = dict(cpositions)
  for m in motions:
    axis = next(a for a in axes if a.name == m.axis)
    after[m.axis] = update_cpos(cpositions[m.axis], m.position, axis.hysteresis)
  return motions, duration + MOVE_ALL_MARGIN_MS, after
