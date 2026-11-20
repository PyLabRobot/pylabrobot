"""A pod against itself: its gripper (and what it holds) against its pipettor, as Biomek keeps
them apart, as pure functions.

Biomek models each part of a pod as a box in 3D and refuses a pose where the gripper's boxes and
the pipettor's overlap. Its path planner searches around that; here a move whose path would
overlap is refused before anything is sent (`Pod.move_axes`), and the gripper's operations make
room first (`Pod._make_room_for_gripper`).

The parts, in Biomek's frame (cm; X along the rail, Y towards the front, Z up from the deck):

- the gripper, about its rotation centre (`pod_geometry`: the pod's X + `GripperXOffset` −
  `PadsCenterToRotationCenter`, its GY + `GripperYOffset` − `FingerCenterOffset`), its hand's
  bottom at GZ + `GripperZOffset` + the fingers' height (the deepest point of the finger profile,
  `Finger/Base`), so the fingertips are at GZ + `GripperZOffset`:
  - the upper hand, from the lower hand's top (`Lowerhand/ZSpan`) up without end; it does not turn;
  - the lower hand with the fingers' top part, down to the top part's depth (`TopPart/ZSpan`);
  - the lower fingers, down to the fingertips;
  - what it holds, from its bottom (the fingertips less the grip height) to its top;
  the last three turned by GR; each widened by `AvoidanceMargin`;
- a Span-8's probes: each probe a band in Y halfway to its neighbours (the end ones out to
  `Mandrel1YOffset`), below the LLS module a box `ProbeSize` wide from the end of its tip up to
  `MandrelExtender` above its mandrel's bottom, widened by `AvoidanceMargin` and, in Z, by
  `AdditionalSafeHeight`; above it the probe's upper part, up without end;
- the 96-head: its body (`HeadBodyXSpan` × `HeadBodyYSpan`, mandrel A1 at the pod's X and Y,
  the mandrels centred), from the end of its tips up without end.

A Span-8's tubing bundle (`LLSWireModel`, `span8_bundle_box`) is not one of the pipettor's parts
here: Biomek's own prime approach sweeps the probes past the gripper parked after homing through
that box, so what Biomek tests it against is not yet known. It is computed for drawing.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration

OPEN = 1e10  # a part with no top


class PodCollisionError(ValueError):
  """A move would bring a pod's gripper (or what it holds) into its own pipettor. Raised before
  anything is sent."""


Point = Tuple[float, float]


@dataclass(frozen=True)
class Part:
  """A part of a pod at a pose: a convex outline seen from above (deck cm) and its Z range."""

  name: str
  outline: Tuple[Point, ...]
  z_low: float
  z_high: float


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


def _box(x0: float, x1: float, y0: float, y1: float) -> Tuple[Point, ...]:
  return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _place(outline: Sequence[Point], cx: float, cy: float, degrees: float) -> Tuple[Point, ...]:
  c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
  return tuple((cx + x * c - y * s, cy + x * s + y * c) for x, y in outline)


def finger_depth(model: Dict) -> float:
  """How far below the hand's bottom the fingertips are: the finger profile's deepest point."""
  return -float(min(p["Y"] for p in model["Finger"]["Base"]))


def gripper_parts(config: PodConfiguration, x: float, state: GripperState) -> List[Part]:
  """The gripper's parts with the pod's X at `x`."""
  geo, model = config.geometry, config.gripper_model
  gm = g.GripperModel.from_bif(model)
  margin = geo.avoidance_margin
  cx = x + geo.gripper_x_offset - gm.pads_center_to_rotation_center
  cy = state.gy + geo.gripper_y_offset - gm.finger_center_offset
  depth = finger_depth(model)
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
  return parts


def span8_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  """A Span-8's probes with the pod's X at `x` (probe axes at X)."""
  geo, s = config.geometry, config.settings
  margin, lift = geo.avoidance_margin, geo.additional_safe_height
  half = s["ProbeSize"] / 2
  extender = s.get("MandrelExtender", 0.0)
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


def span8_bundle_box(
  config: PodConfiguration, x: float, state: PipettorState
) -> Optional[BundleBox]:
  """Where a Span-8's tubing bundle hangs with the probe axes at `x`, from `LLSWireModel` (None
  without it):

  - X: from the probes' axis + `WireStartXOffset` to + `AnchorXOffset`;
  - Y: the probes' range, out to halfway towards the anchor's (`AnchorCenterY` ±
    `AnchorHalfLength`);
  - Z: up from the lower of where the bundle leaves the lowest probe (its mandrel's bottom +
    `MandrelExtender` + `FixedTipZOriginToWireOffset`) and the loop's bottom, `WireBottomAtZMax`
    less half as far as that probe is below its highest Z. The lowest probe is the one furthest
    below its own highest Z (`Z1`..`Z8` limits).
  No `AvoidanceMargin`: the bundle is not widened.
  """
  wire = config.settings.get("LLSWireModel")
  if not wire:
    return None
  extender = config.settings.get("MandrelExtender", 0.0)
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


def head96_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  """The 96-head with mandrel A1 at (`x`, `state.ys[0]`), its bottom at `state.zs[0]`."""
  geo, s = config.geometry, config.settings
  margin = geo.avoidance_margin
  body_x, body_y = s["HeadBodyXSpan"], s["HeadBodyYSpan"]
  grid_x = ((geo.mandrels_x or 1) - 1) * geo.mandrel_x_spacing
  grid_y = ((geo.mandrels_y or 1) - 1) * geo.mandrel_y_spacing
  left = x - (body_x - grid_x) / 2
  back = state.ys[0] - (body_y - grid_y) / 2  # A1 is the back row; Y runs to the front
  outline = _box(left - margin, left + body_x + margin, back - margin, back + body_y + margin)
  return [Part("96-head", outline, state.zs[0] - state.tips[0], OPEN)]


def pipettor_parts(config: PodConfiguration, x: float, state: PipettorState) -> List[Part]:
  if config.module_type == "span8":
    return span8_parts(config, x, state)
  return head96_parts(config, x, state)


def _axes(outline: Sequence[Point]) -> List[Point]:
  n = len(outline)
  return [
    (outline[i][1] - outline[(i + 1) % n][1], outline[(i + 1) % n][0] - outline[i][0])
    for i in range(n)
  ]


def overlap(a: Part, b: Part) -> bool:
  """Whether two parts share any volume (touching is clear): Z ranges, then separating axes."""
  if a.z_high <= b.z_low or b.z_high <= a.z_low:
    return False
  for ax, ay in _axes(a.outline) + _axes(b.outline):
    pa = [px * ax + py * ay for px, py in a.outline]
    pb = [px * ax + py * ay for px, py in b.outline]
    if max(pa) <= min(pb) or max(pb) <= min(pa):
      return False
  return True


def collisions(
  config: PodConfiguration, x: float, gripper: GripperState, pipettor: PipettorState
) -> List[Tuple[str, str]]:
  """The (gripper part, pipettor part) pairs that overlap at this pose."""
  gp, pp = gripper_parts(config, x, gripper), pipettor_parts(config, x, pipettor)
  return [(a.name, b.name) for a in gp for b in pp if overlap(a, b)]


# cm: the most any point moves between two checked poses. Every part is widened by
# `AvoidanceMargin` (0.2164 cm), so a contact cannot pass between two steps unseen.
SWEEP_STEP = 0.1


def swept_collisions(
  config: PodConfiguration,
  x: Tuple[float, float],
  gripper: Tuple[GripperState, GripperState],
  pipettor: Tuple[PipettorState, PipettorState],
) -> List[Tuple[str, str]]:
  """The overlaps anywhere along a coordinated move (every axis on the same profile, scaled to its
  distance), checked at steps no point covers more than `SWEEP_STEP` in. The parts are widened by
  `AvoidanceMargin`, more than a step."""
  (g0, g1), (p0, p1) = gripper, pipettor
  # X carries every part alike, so only the other axes change what overlaps.
  reach = config.gripper_model["Finger"]["PadsCenterToRotationCenter"] + 15.0
  travel = max(
    [abs(g1.gy - g0.gy), abs(g1.gz - g0.gz), abs(g1.gg - g0.gg)]
    + [reach * math.radians(abs(g1.gr - g0.gr))]
    + [abs(b - a) for a, b in zip(p0.ys, p1.ys)]
    + [abs(b - a) for a, b in zip(p0.zs, p1.zs)]
  )
  steps = max(1, math.ceil(travel / SWEEP_STEP))
  found: List[Tuple[str, str]] = []
  for i in range(steps + 1):
    t = i / steps

    def lerp(a: float, b: float) -> float:
      return float(a + t * (b - a))

    gs = GripperState(
      lerp(g0.gy, g1.gy), lerp(g0.gz, g1.gz), lerp(g0.gr, g1.gr), lerp(g0.gg, g1.gg), g0.held
    )
    ps = PipettorState(
      tuple(lerp(a, b) for a, b in zip(p0.ys, p1.ys)),
      tuple(lerp(a, b) for a, b in zip(p0.zs, p1.zs)),
      p0.tips,
    )
    for pair in collisions(config, lerp(*x), gs, ps):
      if pair not in found:
        found.append(pair)
    if found:
      return found
  return found
