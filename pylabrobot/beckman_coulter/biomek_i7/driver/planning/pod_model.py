"""A pod's own parts at a pose, as Biomek models them, and the pose they are built from.

`PodModel` holds one pod's configuration and builds its parts (the gripper's, what it holds, the
pipettor's, the pod's body) at a pose (`PodState`), each set of parts built once per pose: a
planner's sweeps and searches ask for the same poses many times. The parts, in Biomek's frame (cm;
X along the rail, Y towards the front, Z up from the deck):

- the gripper, about its rotation centre (`pod_geometry`: the pod's X + `GripperXOffset` -
  `PadsCenterToRotationCenter`, its GY + `GripperYOffset` - `FingerCenterOffset`), its hand's
  bottom at GZ + `GripperZOffset` + the fingers' height (the deepest point of the finger profile,
  `Finger/Base`), so the fingertips are at GZ + `GripperZOffset`:
  - the upper hand, from the lower hand's top (`Lowerhand/ZSpan`) up without end; it does not turn;
  - the lower hand with the fingers' top part, down to the top part's depth (`TopPart/ZSpan`);
  - the lower fingers, down to the fingertips;
  - what it holds, from its bottom (the fingertips less the grip height) to its top;
  the last three turned by GR; each widened by `AvoidanceMargin` on every side, above and below
  too; when planning, each bottom also `AdditionalSafeHeightGripper` lower (`planning_pad`);
- a Span-8's probes: each probe a band in Y halfway to its neighbours (the end ones out to
  `Mandrel1YOffset`), below the LLS module a box `ProbeSize` wide from the end of its tip up to
  `MandrelExtender` above its mandrel's bottom, widened by `AvoidanceMargin` and, in Z, by
  `AdditionalSafeHeight`; above it the probe's upper part, up without end;
- the 96-head: its body (`HeadBodyXSpan` x `HeadBodyYSpan`, mandrel A1 at the pod's X and Y,
  the mandrels centred), from the end of its tips up without end.

A Span-8's tubing bundle (`LLSWireModel`, `span8_bundle_box`, `bundle_part`) is not one of the
pipettor's parts: Biomek tests it against held labware only (nothing else meets it), as the own
check does (`check.own_collisions`).
"""

import functools
import math
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple, cast

from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration

OPEN = 1e10  # a part with no top


Point = Tuple[float, float]
# A part's bounds: x0, x1, y0, y1, z0, z1 (cm).
Bounds = Tuple[float, float, float, float, float, float]


@dataclass(frozen=True)
class Part:
  """A part of a pod at a pose: a convex outline seen from above (deck cm) and its Z range."""

  name: str
  outline: Tuple[Point, ...]
  z_low: float
  z_high: float

  @cached_property
  def extent(self) -> Tuple[float, float, float, float]:
    """Its outline's bounds: x0, x1, y0, y1."""
    xs, ys = [p[0] for p in self.outline], [p[1] for p in self.outline]
    return min(xs), max(xs), min(ys), max(ys)


@dataclass(frozen=True)
class GripperState:
  """The gripper's axes in Biomek's units (cm, degrees) and what it holds."""

  gy: float
  gz: float
  gr: float
  gg: float
  held: Optional[g.HeldLabware] = None


@dataclass(frozen=True)
class PipettorState:
  """The pipettor's axes in Biomek's units: a Span-8's probes' Y (`ys`), mandrel bottoms (`zs`)
  and tip lengths below them, or the 96-head's Y and Z (one each) and its tips' length."""

  ys: Tuple[float, ...]
  zs: Tuple[float, ...]
  tips: Tuple[float, ...]
  # The 96-head's loaded tips: (first column, last column, first row, last row), from 0; None
  # for the whole head.
  tip_block: Optional[Tuple[int, int, int, int]] = None


def _box(x0: float, x1: float, y0: float, y1: float) -> Tuple[Point, ...]:
  return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _place(outline: Sequence[Point], cx: float, cy: float, degrees: float) -> Tuple[Point, ...]:
  c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
  return tuple((cx + x * c - y * s, cy + x * s + y * c) for x, y in outline)


def finger_depth(model: Dict) -> float:
  """How far below the hand's bottom the fingertips are: the finger profile's deepest point."""
  return -float(min(p["Y"] for p in model["Finger"]["Base"]))


def _gripper_parts(
  config: PodConfiguration, x: float, state: GripperState, pad: float
) -> List[Part]:
  """The gripper's parts with the pod's X at `x`, each widened by `AvoidanceMargin` on every
  side (as Biomek builds them: above and below too) and each bottom `pad` (cm) lower."""
  geo, model = config.geometry, config.gripper_model
  gm = PodModel.of(config).gripper_model
  assert gm is not None
  margin = geo.avoidance_margin
  cx = x + geo.gripper_x_offset - gm.pads_center_to_rotation_center
  cy = state.gy + geo.gripper_y_offset - gm.finger_center_offset
  depth = PodModel.of(config).finger_depth
  hand = state.gz + geo.gripper_z_offset + depth  # the hand's bottom
  tips = hand - depth
  hand_top = hand + model["Lowerhand"]["ZSpan"]
  top_part = hand - model["Finger"]["TopPart"]["ZSpan"]
  fixed, turning = g.gripper_boxes(gm, margin, state.gg, state.held)
  parts = [Part("upper hand", _place(_box(*fixed[0]), cx, cy, 0.0), hand_top, OPEN)]
  parts.append(Part("lower hand", _place(_box(*turning[0]), cx, cy, state.gr), top_part, hand_top))
  parts.append(Part("fingers", _place(_box(*turning[1]), cx, cy, state.gr), tips, top_part))
  if state.held is not None:
    bottom = tips - state.held.grip_offset
    for box in turning[2:]:
      outline = _place(_box(*box), cx, cy, state.gr)
      parts.append(Part("held labware", outline, bottom, bottom + state.held.height))
  return [
    Part(
      p.name, p.outline, p.z_low - margin - pad, p.z_high if p.z_high >= OPEN else p.z_high + margin
    )
    for p in parts
  ]


def _span8_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  """A Span-8's probes with the pod's X at `x` (probe axes at X)."""
  geo, s = config.geometry, config.settings
  margin, lift = geo.avoidance_margin, geo.additional_safe_height
  half = s["ProbeSize"] / 2
  extender = s["MandrelExtender"]
  ys = state.ys
  parts = []
  for i, (y, z, tip) in enumerate(zip(ys, state.zs, state.tips)):
    low = y - geo.mandrel1_y_offset if i == 0 else (ys[i - 1] + y) / 2
    high = y + geo.mandrel1_y_offset if i == len(ys) - 1 else (y + ys[i + 1]) / 2
    low, high = min(low, high), max(low, high)
    lower = _box(x - half - margin, x + half + margin, low - margin, high + margin)
    parts.append(Part(f"probe {i + 1}", lower, z - tip - lift, z + extender))
    upper = _box(x - geo.mandrel1_x_offset - margin, x + half + margin, low - margin, high + margin)
    parts.append(Part(f"probe {i + 1} upper part", upper, z + extender, OPEN))
  return parts


@dataclass(frozen=True)
class BundleBox:
  """The space a Span-8's tubing bundle may hang in (Biomek's frame, cm), open upward."""

  x0: float
  x1: float
  y0: float
  y1: float
  z_low: float


def bundle_part(config: PodConfiguration, x: float, state: PipettorState) -> Optional[Part]:
  """A Span-8's tubing bundle as a part (`span8_bundle_box`: no margin, open upward), which Biomek
  tests against held labware only; None for any other pod."""
  box = span8_bundle_box(config, x, state)
  if box is None:
    return None
  return Part("tubing bundle", _box(box.x0, box.x1, box.y0, box.y1), box.z_low, OPEN)


def span8_bundle_box(
  config: PodConfiguration, x: float, state: PipettorState
) -> Optional[BundleBox]:
  """Where a Span-8's tubing bundle hangs with the probe axes at `x`, from `LLSWireModel` (None
  for any other pod):

  - X: from the probes' axis + `WireStartXOffset` to + `AnchorXOffset`;
  - Y: the probes' range, out to halfway towards the anchor's (`AnchorCenterY` ±
    `AnchorHalfLength`);
  - Z: up from the lower of where the bundle leaves the lowest probe (its mandrel's bottom +
    `MandrelExtender` + `FixedTipZOriginToWireOffset`) and the loop's bottom, `WireBottomAtZMax`
    less half as far as that probe is below its highest Z. The lowest probe is the one furthest
    below its own highest Z (`Z1`..`Z8` limits).
  No `AvoidanceMargin`: the bundle is not widened.
  """
  if config.module_type != "span8":
    return None
  wire = config.settings["LLSWireModel"]
  extender = config.settings["MandrelExtender"]
  lowest = []
  for i, z in enumerate(state.zs):
    low, high = config.limits.get(f"Z{i + 1}", config.limits.get("Z", (z, z)))
    z = max(low, min(high, z))
    lowest.append((high - z, z + extender + wire["FixedTipZOriginToWireOffset"]))
  below, leaves = max(lowest)
  y0, y1 = min(state.ys), max(state.ys)
  near, far = (
    wire["AnchorCenterY"] - wire["AnchorHalfLength"],
    wire["AnchorCenterY"] + wire["AnchorHalfLength"],
  )
  return BundleBox(
    x + wire["WireStartXOffset"],
    x + wire["AnchorXOffset"],
    min(y0, (y0 + near) / 2),
    max(y1, (y1 + far) / 2),
    min(leaves, wire["WireBottomAtZMax"] - below / 2),
  )


def _head96_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  """The 96-head with mandrel A1 at (`x`, `state.ys[0]`), its bottom at `state.zs[0]`."""
  geo, s = config.geometry, config.settings
  margin = geo.avoidance_margin
  body_x, body_y = s["HeadBodyXSpan"], s["HeadBodyYSpan"]
  grid_x = ((geo.mandrels_x or 1) - 1) * geo.mandrel_x_spacing
  grid_y = ((geo.mandrels_y or 1) - 1) * geo.mandrel_y_spacing
  left = x - (body_x - grid_x) / 2
  back = state.ys[0] - (body_y - grid_y) / 2  # A1 is the back row; Y runs to the front
  outline = _box(left - margin, left + body_x + margin, back - margin, back + body_y + margin)
  parts = [Part("96-head", outline, state.zs[0], OPEN)]
  if state.tips[0] > 0:
    # The tips as Biomek boxes them: the block of mandrels that carry one, half a pitch around it.
    sx, sy = geo.mandrel_x_spacing, geo.mandrel_y_spacing
    c0, c1, r0, r1 = state.tip_block or (0, (geo.mandrels_x or 1) - 1, 0, (geo.mandrels_y or 1) - 1)
    block = _box(
      x + (c0 - 0.5) * sx - margin,
      x + (c1 + 0.5) * sx + margin,
      state.ys[0] + (r0 - 0.5) * sy - margin,
      state.ys[0] + (r1 + 0.5) * sy + margin,
    )
    parts.append(Part("96-head tips", block, state.zs[0] - state.tips[0], state.zs[0]))
  return parts


def _pipettor_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  if config.module_type == "span8":
    return span8_parts(config, x, state)
  return head96_parts(config, x, state)


def _body_part(config: PodConfiguration, x: float, y: float) -> Part:
  """The pod's body seen from above with mandrel A1 / probe 1 at (`x`, `y`), from below the deck up
  without end: what an obstacle with no top (a wall, a ceiling) must not overlap."""
  (x0, x1), (y0, y1) = g.pod_xy_box(config.geometry, x, y)
  return Part("pod body", _box(x0, x1, y0, y1), -OPEN, OPEN)


def _moving_parts(
  config: PodConfiguration,
  x: float,
  gripper: Optional[GripperState],
  pipettor: PipettorState,
  pad: float,
) -> List[Part]:
  """The pod's parts the world is checked against: the pipettor's (its lower parts reaching
  `AdditionalSafeHeight` + `AvoidanceMargin` below the tips' ends, the Span-8's already reaching the
  first) and the gripper's."""
  geo = config.geometry
  parts = []
  for part in pipettor_parts(config, x, pipettor):
    if part.name.startswith("96-head"):
      drop = geo.additional_safe_height + geo.avoidance_margin
    elif part.name.startswith("probe ") and not part.name.endswith("upper part"):
      drop = geo.avoidance_margin
    else:
      drop = 0.0
    parts.append(Part(part.name, part.outline, part.z_low - drop, part.z_high))
  if gripper is not None:
    parts += gripper_parts(config, x, gripper, pad)
  return parts


class PodState(NamedTuple):
  """A pod's pose in Biomek's units: X, the gripper's (None on a pod without one) and the
  pipettor's."""

  x: float
  gripper: Optional[GripperState]
  pipettor: PipettorState


# How many poses' parts each model keeps.
CACHED_POSES = 4096


class PodModel:
  """One pod's parts at any pose, from its configuration (`of`). The configuration's figures the
  parts read are taken when the model is built; `of` builds a new model when they change."""

  def __init__(self, config: PodConfiguration):
    self.config = config
    model = config.gripper_model
    self.gripper_model: Optional[g.GripperModel] = g.GripperModel.from_bif(model) if model else None
    self.finger_depth = finger_depth(model) if model else 0.0
    self.gripper_parts = functools.lru_cache(CACHED_POSES)(self._gripper_parts)
    self.pipettor_parts = functools.lru_cache(CACHED_POSES)(self._pipettor_parts)

  def _gripper_parts(self, x: float, state: GripperState, pad: float) -> Tuple[Part, ...]:
    return tuple(_gripper_parts(self.config, x, state, pad))

  def _pipettor_parts(self, x: float, state: PipettorState) -> Tuple[Part, ...]:
    return tuple(_pipettor_parts(self.config, x, state))

  @property
  def channel_z_axes(self) -> List[str]:
    """The axes whose height is the channels' bottom: `Z` on a Multichannel, `Z1`-`Z8` on a
    Span-8."""
    return [f"Z{i}" for i in range(1, 9)] if self.config.module_type == "span8" else ["Z"]

  @property
  def pose_axes(self) -> List[str]:
    """The axes a pose is read from: X, the gripper's (where there is one), Y and the channels'."""
    ys = [f"Y{i}" for i in range(1, 9)] if self.config.module_type == "span8" else ["Y"]
    grips = ["GY", "GZ", "GR", "GG"] if self.config.gripper_model else []
    return ["X", *grips, *ys, *self.channel_z_axes]

  def state(
    self,
    counts: Mapping[str, int],
    held: Optional[g.HeldLabware],
    tip_lengths: Mapping[str, float],
    tip_block: Optional[Tuple[int, int, int, int]],
  ) -> PodState:
    """The pose from the position (counts) of each of `pose_axes`, what the gripper holds, the
    channels' tip lengths (cm, by channel Z axis) and the 96-head's loaded tips' block."""
    d = self.config
    channels = self.channel_z_axes
    ys = [f"Y{i}" for i in range(1, 9)] if d.module_type == "span8" else ["Y"]
    v = {n: d.to_value("Y" if n in ys else n, c) for n, c in counts.items()}  # type: ignore[arg-type]
    if d.module_type == "span8":
      # Each probe's Y counts from its own narrowest place (`probe_y`), not probe 1's.
      v.update({n: d.probe_y(i, counts[n]) for i, n in enumerate(ys, 1)})
    gripper = GripperState(v["GY"], v["GZ"], v["GR"], v["GG"], held) if d.gripper_model else None
    pipettor = PipettorState(
      tuple(v[n] for n in ys),
      tuple(v[z] for z in channels),
      tuple(tip_lengths.get(z, 0.0) for z in channels),
      tip_block,
    )
    return PodState(v["X"], gripper, pipettor)

  @staticmethod
  def of(config: PodConfiguration) -> "PodModel":
    """The model of `config`, built once and again only when a figure its parts read changes. It
    is kept on the configuration (`_MODEL`), so the two are freed together."""
    signature = _signature(config)
    found = config.__dict__.get(_MODEL)
    if found is None or found[0] != signature:
      found = (signature, PodModel(config))
      config.__dict__[_MODEL] = found
    return cast(PodModel, found[1])


# The attribute of a `PodConfiguration` its model is kept under: (its signature, the model).
_MODEL = "_pod_model"


def _signature(config: PodConfiguration) -> Tuple[Any, ...]:
  """What the parts read from `config`: its module type, geometry (frozen), gripper model (by
  identity) and the settings the pipettor's parts take."""
  s = config.settings
  return (
    config.module_type,
    config.geometry,
    id(config.gripper_model),
    s.get("ProbeSize"),
    s.get("MandrelExtender"),
    s.get("HeadBodyXSpan"),
    s.get("HeadBodyYSpan"),
  )


def on_quarter(config: PodConfiguration, gr: float) -> bool:
  """Whether the gripper at `gr` (degrees) is on a quarter turn. Biomek holds the angle it was
  sent in degrees, so a quarter turn is exactly one; ours is read back from counts, a quarter
  within a count of it: on a quarter turn when its counts are the quarter's."""
  quarter = round(gr / 90.0) * 90.0
  return config.to_counts("GR", gr) == config.to_counts("GR", quarter)


def planning_pad(config: PodConfiguration) -> float:
  """How far (cm) Biomek's planner pads the gripper below (`gripper_parts`):
  `AdditionalSafeHeightGripper`. Its move checks pad by 0."""
  return config.geometry.additional_safe_height_gripper


def gripper_below(config: PodConfiguration, x: float, state: GripperState) -> float:
  """How far below GZ (cm) the gripper's lowest point is (its fingers, or what it holds): the
  parts' lowest bottom without their `AvoidanceMargin`, the offset Biomek's safe height takes."""
  low = min(part.z_low for part in gripper_parts(config, x, state))
  return state.gz - (low + config.geometry.avoidance_margin)


def gripper_parts(
  config: PodConfiguration, x: float, state: GripperState, pad: float = 0.0
) -> List[Part]:
  """The gripper's parts with the pod's X at `x`. `pad` (cm) lowers every part's bottom: Biomek's planning model pads the gripper below by
  `AdditionalSafeHeightGripper`, its move checks by 0."""
  return list(PodModel.of(config).gripper_parts(x, state, pad))


def pipettor_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  """The pipettor's parts (a Span-8's probes, the 96-head) with the pod's X at `x`."""
  return list(PodModel.of(config).pipettor_parts(x, state))


def span8_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  """A Span-8's probes with the pod's X at `x` (probe axes at X)."""
  return _span8_parts(config, x, state)


def head96_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  """The 96-head with mandrel A1 at (`x`, `state.ys[0]`), its bottom at `state.zs[0]`."""
  return _head96_parts(config, x, state)


def body_part(config: PodConfiguration, x: float, y: float) -> Part:
  """The pod's body seen from above with mandrel A1 / probe 1 at (`x`, `y`), from below the deck up
  without end: what an obstacle with no top (a wall, a ceiling) must not overlap."""
  return _body_part(config, x, y)


def moving_parts(
  config: PodConfiguration,
  x: float,
  gripper: Optional[GripperState],
  pipettor: PipettorState,
  pad: float = 0.0,
) -> List[Part]:
  """The pod's parts the world is checked against: the pipettor's (its lower parts reaching
  `AdditionalSafeHeight` + `AvoidanceMargin` below the tips' ends, the Span-8's already reaching the
  first) and the gripper's (`gripper_parts`, with `pad`)."""
  return _moving_parts(config, x, gripper, pipettor, pad)
