"""Control points: how a filter plate is processed, step by step, for the whole plate.

Hamilton's HSL library takes them as text, "type, value, duration" separated by semicolons, for
example ``"pressure, 40, 3; flow, 20, 60; idle, 0, 14"``. The type is flow (standard L/min),
pressure (psig) or idle; the value is for the whole plate; the duration is in seconds. Before
sending, Hamilton's driver divides the value by the manifold's number of ports (96 or 24), and
refuses a value per well outside the range the firmware reports. This module does the same.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, List, Literal, Optional, Tuple, cast

from pylabrobot.hamilton.mpe2.commands import ControlPoint, ControlPointRanges, ControlPointType

_TYPES = {
  "flow": ControlPointType.FLOW,
  "pressure": ControlPointType.PRESSURE,
  "idle": ControlPointType.NONE,
}

# Hamilton's own check, from MPEFirmware.RetrieveControlPoints, after whitespace is removed and a
# trailing ";" added.
_HAMILTON_FORMAT = re.compile(r"^([A-Za-z]+,\d*\.?\d*,\d*\.?\d*;)+$")


StepType = Literal["flow", "pressure", "idle"]


@dataclass(frozen=True)
class PlateStep:
  """One control point for the whole plate.

  Args:
    type: "flow", "pressure" or "idle".
    value: for the whole plate, in the units Hamilton's software uses: psi (gauge) for pressure,
      standard L/min for flow; 0 for idle.
    duration: how long the step lasts, in seconds.
  """

  type: StepType
  value: float
  duration: float

  def __post_init__(self):
    """Refuse an unknown type, a negative or infinite value, or no duration."""
    if self.type not in _TYPES:
      raise ValueError(f"A control point's type is flow, pressure or idle, not {self.type!r}.")
    if not (math.isfinite(self.value) and math.isfinite(self.duration)):
      raise ValueError(
        f"A control point's value and duration are finite ({self.value}, {self.duration})."
      )
    if self.value < 0:
      raise ValueError(f"A control point's value can't be negative ({self.value}).")
    if self.duration <= 0:
      raise ValueError(f"A control point lasts longer than 0 s ({self.duration}).")


def parse_control_points(text: str) -> List[PlateStep]:
  """Hamilton's control point text as ``PlateStep``s, for steps copied from an HSL method::

    parse_control_points("pressure,5,5;pressure,15,5")  # type, value, seconds; ';' between

  Args:
    text: the control points, as Hamilton's HSL library and VENUS take them.

  Raises:
    TypeError: ``text`` is not text.
    ValueError: it is not Hamilton's format.
  """
  if not isinstance(text, str):
    raise TypeError(f"Hamilton's control points are text, not {type(text).__name__}.")
  compact = re.sub(r"\s+", "", text)
  if not compact.endswith(";"):
    compact += ";"
  if not _HAMILTON_FORMAT.match(compact):
    raise ValueError(f"Control points are 'type, value, duration' separated by ';', not {text!r}.")
  steps = []
  for part in compact.rstrip(";").split(";"):
    kind, value_text, duration_text = part.split(",")
    steps.append(
      PlateStep(cast(StepType, kind.lower()), float(value_text or 0), float(duration_text or 0))
    )
  return steps


def to_firmware(
  steps: Iterable[PlateStep], number_of_ports: int, ranges: ControlPointRanges
) -> Tuple[ControlPoint, ...]:
  """The control points per well, as the firmware takes them, each checked against its range."""
  if number_of_ports <= 0:
    raise ValueError(f"The manifold reports {number_of_ports} ports.")
  out = []
  for i, step in enumerate(steps, start=1):
    per_well = step.value / number_of_ports
    kind = _TYPES[step.type]
    limits: Optional[Tuple[float, float]] = None
    if kind == ControlPointType.PRESSURE:
      limits = (ranges.min_pressure_per_well, ranges.max_pressure_per_well)
    elif kind == ControlPointType.FLOW:
      limits = (ranges.min_flow_per_well, ranges.max_flow_per_well)
    if limits is not None and not (limits[0] <= per_well <= limits[1]):
      low, high = limits
      raise ValueError(
        f"Control point {i}: {step.type} {step.value} for the plate is {per_well:.4g} per well on "
        f"a {number_of_ports}-port manifold; the MPE2 takes {low:.4g} to {high:.4g} per well."
      )
    out.append(ControlPoint(duration=step.duration, type=kind, value=per_well))
  return tuple(out)


def total_duration(steps: Iterable[PlateStep]) -> float:
  """How long the steps take together, in seconds."""
  return sum(step.duration for step in steps)


def ramp(
  to: float,
  duration: float,
  start: float = 0.0,
  steps: Optional[int] = None,
  type: Literal["pressure", "flow"] = "pressure",
) -> List[PlateStep]:
  """A slow rise (or fall) of pressure or flow, as small control points one after the other.

  A process step that starts at its full pressure opens the valve at once, and the gas supply's
  pressure dips; rising in small steps asks less of the supply at a time.
  These are ordinary control points: the firmware and its limits are as always. Each step lasts at
  least 1 s: the firmware runs each control point for at least about 1 s, so shorter steps would
  keep the gas on longer than asked. A ramp softens the dip as the valve opens, not the supply's
  sag once at the level. Put the step to hold after it::

    steps = ramp(to=30, duration=5) + [PlateStep("pressure", 30, 10)]

  Args:
    to: the value to reach, for the whole plate (psi, or standard L/min for flow).
    duration: how long the rise takes, in seconds.
    start: the value it rises from; the first step is one increment above it.
    steps: how many control points, each at least 1 s long; one per whole second by default.
    type: "pressure" or "flow".
  """
  if type not in ("pressure", "flow"):
    raise ValueError(f"A ramp is of pressure or flow, not {type!r}.")
  if not all(math.isfinite(v) for v in (duration, to, start)):
    raise ValueError(f"A ramp's duration and values are finite ({duration}, {start}, {to}).")
  if duration <= 0:
    raise ValueError(f"A ramp lasts longer than 0 s ({duration}).")
  if to < 0 or start < 0:
    raise ValueError("A ramp's values can't be negative.")
  count = int(duration) if steps is None else steps
  if count < 2:
    raise ValueError(
      f"A ramp has at least 2 steps of at least 1 s each ({duration} s in {count} steps): the"
      " firmware runs each control point for about 1 s at least."
    )
  step_time = duration / count
  if step_time < 1.0:
    raise ValueError(
      f"A ramp's steps last at least 1 s ({duration} s in {count} steps is {step_time:.2f} s each):"
      " the firmware runs each control point for at least about 1 s."
    )
  return [PlateStep(type, start + (to - start) * i / count, step_time) for i in range(1, count + 1)]
