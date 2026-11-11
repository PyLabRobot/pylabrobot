"""Where a pod is along the shared X rail: Biomek's collision model, as pure functions.

Biomek's rule:

- A pod's X range at a pose is the union of its bridge, its pipettor (widened by the avoidance
  margin) and its gripper.
- The gripper is boxes, each widened by the avoidance margin, in the gripper's own frame:
  - the upper hand, which does not turn;
  - the lower hand with the top of the fingers;
  - the lower fingers;
  - the held labware, if any.

  The fingers open to the grip width, or wider to clear the labware. Every box but the upper
  hand turns by GR about the rotation centre, then all move with the gripper. The rotation
  centre's deck X is the pod's X + `GripperXOffset` - `PadsCenterToRotationCenter`. GR = 0 points
  the fingers to +X (GR ~90 to the front, ~189 at its maximum).
- A move's swept range comes from its start and end poses.
- Two ranges collide when their edges, floored to 1/16 cm cells, overlap or share a cell
  (`grid_overlap`).

Everything is in Biomek's units: deck X in cm, GR in degrees, the grip width GG in cm. A pod's X
is `PodConfiguration.to_value("X", counts)`, and its GR and GG likewise.

Where this differs from Biomek, it says so:
- **Sweeps.** Biomek's swept range is the union of the start and end poses. Here a move's sweep
  follows its coordinated path (`swept_x_range`), so a gripper turning through the other pod's
  side between those poses is caught. This is never looser.
- **Held labware.** Biomek reads its size, and the stack on it, from its labware library. Here the
  caller gives it, from the resource the gripper holds (`HeldLabware`).
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import Obstacle, PodGeometry

Range = Tuple[float, float]
"""An X interval in deck cm, `(lowest, highest)`."""

Box = Tuple[float, float, float, float]
"""A box in the gripper's frame, seen from above: `(x_low, x_high, y_low, y_high)`, cm. x runs
along the fingers, y across them, from the rotation centre."""


@dataclass(frozen=True)
class GripperModel:
  """The gripper's dimensions, in cm, from the instrument file's `GripperModel`, named after its
  keys."""

  upperhand_x_span: float
  upperhand_y_span: float
  upperhand_center_x: float
  upperhand_center_y: float
  lowerhand_fingerwise_span: float
  lowerhand_default_span: float
  finger_fingerwise_offset: float
  finger_extension_span: float
  top_part_fingerwise_span: float
  bottom_part_fingerwise_span: float
  finger_thickness: float
  clipper_thickness: float
  lw_finger_spacing: float
  finger_center_offset: float
  overall_angled_thickness: float
  pads_center_to_rotation_center: float

  @classmethod
  def from_bif(cls, model: Dict[str, Any]) -> "GripperModel":
    """From the declared configuration's `gripper_model` (the `.bif` `GripperModel` dict)."""
    up, low, finger = model["Upperhand"], model["Lowerhand"], model["Finger"]
    return cls(
      upperhand_x_span=up["XSpan"],
      upperhand_y_span=up["YSpan"],
      upperhand_center_x=up["CenterXOffset"],
      upperhand_center_y=up["CenterYOffset"],
      lowerhand_fingerwise_span=low["FingerwiseSpan"],
      lowerhand_default_span=low["YSpanNoFingerMountExtension"],
      finger_fingerwise_offset=finger["FingerwiseOffset"],
      finger_extension_span=finger["ExtensionSpan"],
      top_part_fingerwise_span=finger["TopPart"]["FingerwiseSpan"],
      bottom_part_fingerwise_span=finger["BottomPart"]["FingerwiseSpan"],
      finger_thickness=finger["Thickness"],
      clipper_thickness=finger["ClipperThickness"],
      lw_finger_spacing=finger["LWFingerSpacing"],
      finger_center_offset=finger["FingerCenterOffset"],
      overall_angled_thickness=finger["OverallAngledThickness"],
      pads_center_to_rotation_center=finger["PadsCenterToRotationCenter"],
    )


@dataclass(frozen=True)
class StackedLayer:
  """A layer stacked on what the gripper holds (a lid on a plate), in the gripper's frame, cm: its
  footprint's `length` along the fingers and `width` across them, and where its corner is from
  the corner of the layer below (`dx`, `dy`)."""

  dx: float
  dy: float
  length: float
  width: float


@dataclass(frozen=True)
class HeldLabware:
  """What the gripper holds, in its own frame, cm: `length` along the fingers, `width` between
  them; offsets move it from the centre of the pads. `layers` are what is stacked on it, bottom
  up (as Biomek counts it: the gripped labware is a stack; the bottom layer is centred on the
  pads less `GrippedLabwareOffsets`, each layer above sits at its offset from the corner of the
  one below, and the footprint is every layer's box). The grip width is the bottom layer's.
  `height` is the stack's, from its bottom to its top; `grip_offset` how far above its bottom the
  fingertips are (negative below it)."""

  length: float
  width: float
  offset_x: float = 0.0
  offset_y: float = 0.0
  layers: Tuple[StackedLayer, ...] = ()
  height: float = 0.0
  grip_offset: float = 0.0


@dataclass(frozen=True)
class GripperPose:
  """The gripper's rotation `gr` (degrees) and grip width `gg` (cm), and what it holds."""

  gr: float
  gg: float
  held: Optional[HeldLabware] = None


def bridge_x_range(geometry: PodGeometry, x: float) -> Range:
  low = x - geometry.bridge_to_head_x_offset - geometry.mandrel1_x_offset
  return low, low + geometry.bridge_x_span


def pipettor_x_span(geometry: PodGeometry) -> float:
  """The pipettor's X extent: the pod's `PodXSpan` in the instrument file, 18.606 cm on the
  Multichannel, 4.0 on the Span-8."""
  return geometry.pod_x_span


def pipettor_x_range(geometry: PodGeometry, x: float) -> Range:
  low = x - geometry.mandrel1_x_offset
  margin = geometry.avoidance_margin
  return low - margin, low + pipettor_x_span(geometry) + margin


def union(a: Range, b: Range) -> Range:
  return min(a[0], b[0]), max(a[1], b[1])


def gripper_boxes(
  model: GripperModel, margin: float, gg: float, held: Optional[HeldLabware]
) -> Tuple[List[Box], List[Box]]:
  """The gripper's boxes in its frame, each widened by `margin`. Returns the boxes
  that stay put (the upper hand) and those that turn with GR."""
  m = model
  opening = (m.lw_finger_spacing + m.finger_thickness) * 2 + max(gg, 0.0)
  if held is not None:
    finger = m.lw_finger_spacing + m.clipper_thickness + m.finger_thickness
    opening = max(opening, 2 * finger + held.width)
  finger_y_high = opening / 2 + m.finger_center_offset
  finger_y_low = m.finger_center_offset - opening / 2
  hand_span = max((m.finger_extension_span + m.finger_thickness) * 2 - opening, opening)
  hand_y_low = min(-m.lowerhand_default_span / 2, m.finger_center_offset - hand_span / 2)
  hand_y_high = max(m.lowerhand_default_span / 2, hand_span / 2 + m.finger_center_offset)
  top_end = m.top_part_fingerwise_span - m.lowerhand_fingerwise_span / 2
  top_end += m.finger_fingerwise_offset

  def box(x0: float, x1: float, y0: float, y1: float) -> Box:
    return x0 - margin, x1 + margin, y0 - margin, y1 + margin

  upperhand = box(
    m.upperhand_center_x - m.upperhand_x_span / 2,
    m.upperhand_center_x + m.upperhand_x_span / 2,
    m.upperhand_center_y - m.upperhand_y_span / 2,
    m.upperhand_center_y + m.upperhand_y_span / 2,
  )
  turning = [
    # the lower hand with the top portion of the fingers
    box(
      -m.lowerhand_fingerwise_span / 2,
      top_end,
      min(finger_y_low, hand_y_low),
      max(finger_y_high, hand_y_high),
    ),
    # the lower portion of the fingers
    box(top_end, top_end + m.bottom_part_fingerwise_span, finger_y_low, finger_y_high),
  ]
  if held is not None:
    x0 = m.pads_center_to_rotation_center - held.offset_x - held.length / 2
    y0 = m.finger_center_offset - held.offset_y - held.width / 2
    turning.append(box(x0, x0 + held.length, y0, y0 + held.width))
    for layer in held.layers:
      x0, y0 = x0 + layer.dx, y0 + layer.dy
      turning.append(box(x0, x0 + layer.length, y0, y0 + layer.width))
  return [upperhand], turning


def turned_x_extent(box: Box, start: float, end: float) -> Range:
  """The X a box covers while turning about the origin from `start` to `end` degrees
  (counterclockwise from +X). Each corner's X is r cos(angle + phase); between the ends it peaks
  where that angle crosses a multiple of 180 degrees."""
  low_angle, high_angle = min(start, end), max(start, end)
  xs: List[float] = []
  for x in (box[0], box[1]):
    for y in (box[2], box[3]):
      r, phase = math.hypot(x, y), math.degrees(math.atan2(y, x))
      angles = [low_angle, high_angle]
      k = math.ceil((low_angle + phase) / 180.0)
      while k * 180.0 - phase <= high_angle:
        angles.append(k * 180.0 - phase)
        k += 1
      xs.extend(r * math.cos(math.radians(a + phase)) for a in angles)
  return min(xs), max(xs)


def gripper_x_range(
  geometry: PodGeometry,
  model: GripperModel,
  x: float,
  start: GripperPose,
  end: Optional[GripperPose] = None,
) -> Range:
  """The X the gripper covers with the pod at `x`, at `start`, or turning and opening from
  `start` to `end`."""
  end = end or start
  centre = x + geometry.gripper_x_offset - model.pads_center_to_rotation_center
  margin = geometry.avoidance_margin
  gg = max(start.gg, end.gg)
  low, high = math.inf, -math.inf
  for held in {start.held, end.held}:
    fixed, turning = gripper_boxes(model, margin, gg, held)
    for b in fixed:
      low, high = min(low, centre + b[0]), max(high, centre + b[1])
    for b in turning:
      lo, hi = turned_x_extent(b, start.gr, end.gr)
      low, high = min(low, centre + lo), max(high, centre + hi)
  return low, high


@dataclass(frozen=True)
class Gripper:
  """A gripper on a pod, at a pose: what the X range needs to count it."""

  model: GripperModel
  pose: GripperPose


def pod_x_range(geometry: PodGeometry, x: float, gripper: Optional[Gripper] = None) -> Range:
  """The X a pod takes up with its X at `x` and its gripper at its pose. Without
  a gripper, its bridge and pipettor only."""
  base = union(bridge_x_range(geometry, x), pipettor_x_range(geometry, x))
  if gripper is None:
    return base
  return union(base, gripper_x_range(geometry, gripper.model, x, gripper.pose))


SWEEP_TOLERANCE = 0.02
"""How far, in cm, any point may move between two of the poses a sweep checks; the result is
widened by it, so it is never looser than the continuous move."""


def swept_x_range(
  geometry: PodGeometry,
  start: float,
  end: float,
  gripper_start: Optional[Gripper] = None,
  gripper_end: Optional[Gripper] = None,
) -> Range:
  """The X a pod takes up while moving its X from `start` to `end`, and its
  gripper from one pose to the other.

  A coordinated move (`motion.py`, the Master Controller's `AM`) takes every axis along the same
  profile, scaled to its distance, so the pod passes through X(s) and GR(s), GG(s) together for
  s from 0 to 1. The sweep follows that path, at steps no point can cover more than
  `SWEEP_TOLERANCE` in, widened by it. Biomek checks the start and end poses only; a gripper
  turning through the other pod's side between them is caught here."""
  if gripper_start is None:
    return union(pod_x_range(geometry, start), pod_x_range(geometry, end))
  model = gripper_start.model
  a, b = gripper_start.pose, (gripper_end or gripper_start).pose
  swept = union(
    pod_x_range(geometry, start, gripper_start), pod_x_range(geometry, end, Gripper(model, b))
  )
  if (a.gr, a.gg, a.held) == (b.gr, b.gg, b.held):
    return swept  # the footprint only translates with X: its two ends bound it exactly
  reach = max(
    math.hypot(max(abs(box[0]), abs(box[1])), max(abs(box[2]), abs(box[3])))
    for held in {a.held, b.held}
    for boxes in gripper_boxes(model, geometry.avoidance_margin, max(a.gg, b.gg), held)
    for box in boxes
  )
  # Between two steps the footprint translates (bounded by the steps themselves) and turns and
  # opens, by at most this far:
  travel = reach * math.radians(abs(b.gr - a.gr)) + abs(b.gg - a.gg)
  steps = max(1, math.ceil(travel / SWEEP_TOLERANCE))
  low, high = swept
  for i in range(steps + 1):
    s = i / steps
    pose = GripperPose(a.gr + s * (b.gr - a.gr), a.gg + s * (b.gg - a.gg), a.held)
    x = start + s * (end - start)
    for held in {a.held, b.held}:
      lo, hi = gripper_x_range(geometry, model, x, GripperPose(pose.gr, pose.gg, held))
      low, high = min(low, lo), max(high, hi)
  return low - SWEEP_TOLERANCE, high + SWEEP_TOLERANCE


def overlap(a: Range, b: Range) -> bool:
  """Whether two ranges share any X. Touching is not overlapping."""
  return a[0] < b[1] and b[0] < a[1]


# Biomek compares range edges as whole sixteenths of a cm, rounded down (`floor(x * 16)`).
GRID_PER_CM = 16


def grid_cell(x: float) -> int:
  return math.floor(x * GRID_PER_CM)


def grid_overlap(a: Range, b: Range) -> bool:
  """Biomek's collision test: each edge floored to its 1/16 cm cell; the ranges
  collide unless one's high cell is below the other's low cell. Edges in the same cell collide."""
  return not (grid_cell(a[1]) < grid_cell(b[0]) or grid_cell(b[1]) < grid_cell(a[0]))


def pod_xy_box(geometry: PodGeometry, x: float, y: float) -> Tuple[Range, Range]:
  """The pod's body seen from above at X `x`, Y `y` (mandrel A1 / probe 1), cm: its corner is
  `Mandrel1XOffset` / `Mandrel1YOffset` back from that."""
  x0, y0 = x - geometry.mandrel1_x_offset, y - geometry.mandrel1_y_offset
  return (x0, x0 + geometry.pod_x_span), (y0, y0 + geometry.pod_y_span)


def _open_overlap(low: Optional[float], high: Optional[float], span: Range, buffer: float) -> bool:
  lo = -math.inf if low is None else low - buffer
  hi = math.inf if high is None else high + buffer
  return lo < span[1] and span[0] < hi


@dataclass(frozen=True)
class Clearance:
  """What a pod's travel over an X/Y area must clear: the lowest height (cm) its channels may
  travel at, and the obstacles in the way that no height clears (walls)."""

  height: float
  walls: Tuple[str, ...]


def obstacle_clearance(obstacles: Sequence[Obstacle], x: Range, y: Range) -> Clearance:
  """Over the area `x` by `y` (cm): the top (plus buffer) of every obstacle there with a top and
  no floor (the pump bank), and every obstacle there with no top (a wall, or a ceiling).

  Ceilings (a bottom and no top: the I-beam from Z 37.069 behind Y 11.092, the cameras from Z 32.078
  at the front corners) count as walls. Biomek makes every obstacle a box (an open top at 1e10,
  each face widened by its buffer) and checks the pod's whole part model against
  them, the carriage above the channels included, so a ceiling bounds the pod's body, not its
  channels' bottom; with the channels at a traverse height the body is above every ceiling. None of
  the 12,321 X/Y moves Biomek logged put a pod's body under one (the furthest back was Y 14.27 cm),
  so blocking them refuses nothing Biomek did."""
  height = -math.inf
  walls = []
  for o in obstacles:
    if not (_open_overlap(o.x1, o.x2, x, o.buffer) and _open_overlap(o.y1, o.y2, y, o.buffer)):
      continue
    if o.z2 is None:
      walls.append(o.name)
    elif o.z2 is not None and (o.z1 is None or o.z1 < 0):
      height = max(height, o.z2 + o.buffer)
  return Clearance(height, tuple(walls))
