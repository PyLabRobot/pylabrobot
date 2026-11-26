"""Biomek i7 pods and their bridges.

Each pod hangs from a bridge that spans the deck from front to back and rides the X rails; the pod
travels along its bridge in Y, and its head in Z. On this unit pod 1 carries the 96-channel
Multichannel head and pod 2 the Span-8, each with a gripper.

Sized in centimetres. The spans the collision model uses (BridgeXSpan,
BridgeToHeadXOffset, PodXSpan, PodYSpan) are the instrument's own when its declared configuration
is given (`pod_model`), so the model's X extents are the ones the rail guard computes
(`driver/pod_geometry.py`); otherwise the pod type's defaults under
`HardwareSetupInfo/PodTypes/<MC|Span8>`. A value marked `assumed` has no source yet.

Along X, as Biomek's collision model places them: the bridge's left edge is BridgeToHeadXOffset left of the
pipettor's (`bridge_x_range`, `pipettor_x_range` without the avoidance margin), and the pipettor
is the carriage, PodXSpan wide.

A pod is its carriage: its frame is the carriage's front left bottom corner, and it follows the
pod's Y along its bridge. A Multichannel's head (`heads.head96`) hangs below it and follows Z in
it (`place_head96`). The parts with axes of their own are the bridge's: a Span-8's probes
(`heads.span8_channel`, each following its own Y and Z) and the gripper (`gripper_model`, a
`MechanicalGripper` posed from GY, GZ, GR and GG), from which what it holds hangs.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodGeometry
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation

from . import heads
from .units import MM_PER_CM, cm

# Both pod types. Biomek_i7.bif HardwareSetupInfo/PodTypes/<MC|Span8>: BridgeXSpan,
# BridgeToHeadXOffset
BRIDGE_X_SPAN = 12.7
BRIDGE_TO_HEAD_X_OFFSET = 2.0

# Pod 1, the 96-channel Multichannel pod. PodTypes/MC: PodXSpan, PodYSpan. Its head is
# `heads.head96`.
POD_96_X_SPAN = 18.606
POD_96_Y_SPAN = 11.43

# Pod 2, the Span-8 pod. PodTypes/Span8: PodXSpan, PodYSpan. Its probes are `heads.span8_channel`.
POD_SPAN8_X_SPAN = 4.0
POD_SPAN8_Y_SPAN = 10.0

# assumed: the bridge's section and the carriage's height. Not yet sourced. The gripper is the
# instrument file's model (`gripper_model`).
BRIDGE_HEIGHT = 10.0
POD_BODY_HEIGHT = 25.0
# How long the viewer's mark at mandrel A1 is, front to back, mm. Not a Biomek value.
A1_MARK_LENGTH = 9.0

_COLORS: Dict[str, str] = {
  "x_arm": "#6b7a85",
  "pod": "#c9ced3",
  "head_body": "#3d4f63",
  "head_flange": "#8a96a3",
}


# Model names, namespaced: the viewer draws a resource from `<model>.glb` under the package.
MODEL_GRIPPER_FINGER = "beckman_coulter_biomek_i7_gripper_finger"


def _part(
  name: str,
  size_x: float,
  size_y: float,
  size_z: float,
  category: str,
  model: Optional[str] = None,
) -> Resource:
  """A box of a pod, every length in Biomek's centimetres."""
  part = Resource(
    name=name,
    size_x=cm(size_x),
    size_y=cm(size_y),
    size_z=cm(size_z),
    category=category,
    model=model,
  )
  if category in _COLORS:
    # A field the viewer reads off the resource; `Resource` does not declare it.
    part.appearance = {"color": _COLORS[category]}  # type: ignore[attr-defined]
  return part


Outline = List[Tuple[float, float]]


def finger_outline(model: Dict[str, Any]) -> Outline:
  """A finger seen from the side, from the instrument file's `Finger/Base` (cm): x along the
  fingers from the rotation centre, y up from the hand's bottom. A bar under the hand
  (`TopPart`), a strut angled down and out, and the pad block at the tip (`BottomPart` spans the
  strut and the block). Biomek's collision boxes take the same spans: the bar ends at
  `TopPart/FingerwiseSpan` - `Lowerhand/FingerwiseSpan` / 2 + `FingerwiseOffset`."""
  return [(float(p["X"]), float(p["Y"])) for p in model["Finger"]["Base"]]


def pad_block(outline: Outline) -> Tuple[float, float, float, float]:
  """The block at the finger's tip that meets the labware, from the outline (cm):
  `(x_low, x_high, y_low, y_high)`. Its bottom is the outline's lowest edge, its top the lower of
  the two corners next to that edge."""
  bottom = min(y for _, y in outline)
  ends = [i for i, (_, y) in enumerate(outline) if y == bottom]
  n = len(outline)
  beside = [outline[(i + d) % n][1] for i in ends for d in (-1, 1)]
  top = min(y for y in beside if y != bottom)
  xs = [outline[i][0] for i in ends]
  return min(xs), max(xs), bottom, top


@dataclass
class GripperModel:
  """A pod's gripper in the resource tree, from the instrument file's `GripperModel` (cm), posed by
  `pose`: the upper hand, which does not turn, and the gripper (`MechanicalGripper`), which turns
  about the rotation centre with the lower hand (its body) and the fingers, and holds what the
  gripper holds at the pads (its tool centre point). Both are children of what the gripper is posed
  in (the pod's bridge)."""

  upper_hand: Resource
  gripper: MechanicalGripper
  model: Dict[str, Any]

  @property
  def hand(self) -> MechanicalGripper:
    """What turns and what the held labware hangs from: the gripper."""
    return self.gripper

  @property
  def fingers(self) -> Tuple[Resource, Resource]:
    return self.gripper.fingers[0], self.gripper.fingers[1]

  @property
  def depth(self) -> float:
    """How far below the hand's bottom the fingertips are, cm."""
    return -min(y for _, y in finger_outline(self.model))

  def pose(self, centre: Coordinate, gr: float, gg: float) -> None:
    """Put the rotation centre at `centre` (mm, the hand's bottom; in the parent's frame), turned
    by GR `gr` degrees (Biomek's: counterclockwise with Y to the front, so clockwise here), the
    pads `gg` cm apart (held to the jaws' range)."""
    m = self.model
    up = m["Upperhand"]
    gripper = self.gripper
    turn = -gr
    gripper.rotate_to(z=turn)
    gripper.location = centre - gripper.proximal_joint.rotated(Rotation(z=turn))
    low, high = gripper.jaw_range
    width = cm(gg + 2 * m["Finger"]["LWFingerSpacing"])
    gripper.jaw_width = min(max(width, low), high)
    self.upper_hand.location = centre + Coordinate(
      cm(up["CenterXOffset"] - up["XSpan"] / 2),
      -cm(up["CenterYOffset"] + up["YSpan"] / 2),
      cm(m["Lowerhand"]["ZSpan"]),
    )

  def pads(self) -> Coordinate:
    """The pads' centre in the gripper's frame (mm): `PadsCenterToRotationCenter` along the fingers
    from the rotation centre, `FingerCenterOffset` across (to the front in Biomek's frame), at the
    fingertips: the tool centre point."""
    return self.gripper.proximal_joint + self.gripper.tool_center_point


def gripper_model(
  name: str, model: Dict[str, Any], gg_range: Tuple[float, float] = (0.0, 20.0)
) -> GripperModel:
  """The gripper of the instrument file's `GripperModel` dict: `<name>` (a `MechanicalGripper`,
  with `<name>_lower_hand`, `<name>_finger_0` / `_1` and their pads `<name>_pad_0` / `_1`) and
  `<name>_upper_hand`. `gg_range` is GG's travel (cm, the declared limits): the gap between the
  pads, which the jaws' range follows.

  The gripper's origin is the lower hand's corner; it turns about `proximal_joint`, the rotation
  centre at the hand's bottom. A finger is its outline's bounds (`finger_outline`), `Thickness`
  across, its plate `LWFingerSpacing` back from the pad face; each pad is the outline's pad block
  (`pad_block`), standing that spacing into the gap, so the jaws' width is GG + 2 x the spacing.
  """
  up, low, finger = model["Upperhand"], model["Lowerhand"], model["Finger"]
  upper = _part(f"{name}_upper_hand", up["XSpan"], up["YSpan"], up["ZSpan"], "body")
  body = _part(
    f"{name}_lower_hand",
    low["FingerwiseSpan"],
    low["YSpanNoFingerMountExtension"],
    low["ZSpan"],
    "body",
  )
  outline = finger_outline(model)
  x0, x1 = min(x for x, _ in outline), max(x for x, _ in outline)
  y0, y1 = min(y for _, y in outline), max(y for _, y in outline)
  thickness, spacing = finger["Thickness"], finger["LWFingerSpacing"]
  fingers = [
    _part(f"{name}_finger_{i}", x1 - x0, thickness, y1 - y0, "finger", MODEL_GRIPPER_FINGER)
    for i in range(2)
  ]
  px0, px1, py0, py1 = pad_block(outline)
  pads = [_part(f"{name}_pad_{i}", px1 - px0, spacing, py1 - py0, "pad") for i in range(2)]
  joint = Coordinate(cm(low["FingerwiseSpan"] / 2), cm(low["YSpanNoFingerMountExtension"] / 2), 0)
  gripper = MechanicalGripper(
    name=name,
    proximal_joint=joint,
    tool_center_point=Coordinate(
      cm(finger["PadsCenterToRotationCenter"]), -cm(finger["FingerCenterOffset"]), cm(y0)
    ),
    body=body,
    body_location=Coordinate.zero(),
    fingers=fingers,
    # Along the fingers from the rotation centre, the fingertips at the outline's bottom.
    finger_location=Coordinate(joint.x + cm(x0), 0, joint.z + cm(y0)),
    jaw_range=(cm(gg_range[0] + 2 * spacing), cm(gg_range[1] + 2 * spacing)),
    pads=pads,
    # Into the gap: the first finger's face is its low side, the second's its high side.
    pad_location=Coordinate(cm(px0 - x0), -cm(spacing), cm(py0 - y0)),
  )
  gripper.pads[1].location = Coordinate(cm(px0 - x0), cm(thickness), cm(py0 - y0))
  gm = GripperModel(upper, gripper, model)
  gm.pose(Coordinate.zero(), 90.0, gg_range[0])
  return gm


def pod_96(
  name: str = "left_pod",
  pod_x_span: float = POD_96_X_SPAN,
  pod_y_span: float = POD_96_Y_SPAN,
  settings: Optional[Mapping[str, Any]] = None,
  geometry: Optional[PodGeometry] = None,
) -> Resource:
  """The Multichannel pod: its carriage (cm spans), with the 96-channel head (`heads.head96`,
  `<name>_head`) hanging below it. The carriage follows Y along its bridge; the head follows Z in
  it (`place_head96`)."""
  # The pod body (category `pod`): an ordinary child of its bridge, as the STAR's head is of its
  # X-arm, so the viewer carries it along with the bridge.
  pod = _part(name, pod_x_span, pod_y_span, POD_BODY_HEIGHT, "pod")
  head = heads.head96(f"{name}_head", settings, geometry, pod_y_span)
  at = heads.head96_location(settings)
  pod.assign_child_resource(head, location=Coordinate(at.x, at.y, -head.get_size_z()))
  return pod


def place_head96(pod: Resource, z: float) -> None:
  """Put a Multichannel pod's head with its bottom (the mandrels' ends) at `z` mm in the pod's
  frame (the carriage's bottom is 0)."""
  head = pod.get_resource(f"{pod.name}_head")
  loc = head.location
  assert loc is not None
  head.location = Coordinate(loc.x, loc.y, z)


def pod_span8(
  name: str = "right_pod",
  pod_x_span: float = POD_SPAN8_X_SPAN,
  pod_y_span: float = POD_SPAN8_Y_SPAN,
) -> Resource:
  """The Span-8 pod: its carriage (cm spans). Its probes are channels of their own on the bridge
  (`span8_channels`), since each moves in Y and Z by itself."""
  return _part(name, pod_x_span, pod_y_span, POD_BODY_HEIGHT, "pod")


def span8_channels(bridge: Resource) -> List[Resource]:
  """A Span-8 bridge's probes' channels, probe 1 first."""
  found = [c for c in bridge.children if c.category == "pipette_channel"]
  return sorted(found, key=lambda c: int(c.name.rsplit("_", 1)[1]))


def bridge(
  name: str,
  pod: Resource,
  length: float,
  pod_y: float,
  bridge_x_span: float = BRIDGE_X_SPAN,
  bridge_to_head_x_offset: float = BRIDGE_TO_HEAD_X_OFFSET,
) -> Resource:
  """A bridge `length` mm long front to back, carrying `pod` at `pod_y` mm from its front end.

  Args:
    name: what to call the bridge.
    pod: the pod it carries, hung below it.
    length: how far it spans, in mm: the deck's depth.
    pod_y: where the pod's front edge is along the bridge, in mm.
    bridge_x_span: the bridge's width, cm (BridgeXSpan).
    bridge_to_head_x_offset: how far right of the bridge's left edge the pod's carriage begins,
      cm (BridgeToHeadXOffset).
  """
  rail = Resource(
    name=name,
    size_x=cm(bridge_x_span),
    size_y=length,
    size_z=cm(BRIDGE_HEIGHT),
    # The viewer draws an x_arm as an open frame riding the rails, so the deck shows through it.
    category="x_arm",
  )
  rail.appearance = {"color": _COLORS["x_arm"]}  # type: ignore[attr-defined]
  rail.assign_child_resource(
    pod, location=Coordinate(cm(bridge_to_head_x_offset), pod_y, -pod.get_size_z())
  )
  return rail


def pod_model(
  side: str,
  module_type: str,
  length: float,
  geometry: Optional[PodGeometry] = None,
  settings: Optional[Mapping[str, Any]] = None,
) -> Tuple[Resource, Resource]:
  """A pod and the bridge carrying it, named `<side>_pod` and `<side>_bridge`: from the pod's
  declared `geometry` when given, else the pod type's defaults. Returns (bridge, pod).

  With the geometry, both declare the `reference_point` the viewer marks: the point the pod's X
  names, which is mandrel A1 on a Multichannel and probe 1 on a Span-8 (`targeting.py`). It is
  `BridgeToHeadXOffset + Mandrel1XOffset` right of the bridge's left edge, the offset
  `pod_geometry.bridge_x_range` takes off the X, so the line and the rail guard cannot disagree
  (the rail rule reproduces every logged move to within its 1/16 cm cells). The bridge's line
  runs its whole depth. On a Multichannel, whose model also follows Y, the pod marks mandrel A1
  itself, a short line at A1's Y (`Mandrel1YOffset` in front of the carriage's back edge); a
  Span-8's probes have a Y each, so its pod marks the X only.

  A Multichannel's head is the pod's (`pod_96`); a Span-8's probes, `<side>_pod_channel_1` ..
  `_8` (`heads.span8_channel`, sized from `settings`), are the bridge's, at its reference line.
  """
  if module_type not in ("multichannel", "span8"):
    raise ValueError(f"no model for a {module_type!r} pod; have ['multichannel', 'span8']")
  x_span = geometry.pod_x_span if geometry is not None else None
  y_span = geometry.pod_y_span if geometry is not None else None
  if module_type == "multichannel":
    pod = pod_96(
      f"{side}_pod",
      x_span or POD_96_X_SPAN,
      y_span or POD_96_Y_SPAN,
      settings=settings,
      geometry=geometry,
    )
  else:
    pod = pod_span8(f"{side}_pod", x_span or POD_SPAN8_X_SPAN, y_span or POD_SPAN8_Y_SPAN)
  if geometry is None:
    rail = bridge(f"{side}_bridge", pod, length, 0.0)
    if module_type == "span8":
      _add_span8_channels(rail, pod, BRIDGE_TO_HEAD_X_OFFSET, 1.0, 1.0, settings)
    return rail, pod
  rail = bridge(
    f"{side}_bridge",
    pod,
    length,
    0.0,
    geometry.bridge_x_span,
    geometry.bridge_to_head_x_offset,
  )
  a1_x = cm(geometry.mandrel1_x_offset)
  rail.reference_point = Coordinate(cm(geometry.bridge_to_head_x_offset) + a1_x, 0, 0)  # type: ignore[attr-defined]
  if module_type == "multichannel":
    a1_y = cm(geometry.pod_y_span - geometry.mandrel1_y_offset)
    pod.reference_point = {  # type: ignore[attr-defined]
      "x": a1_x,
      "y": a1_y,
      "z": 0.0,
      "y_range": [a1_y - A1_MARK_LENGTH / 2, a1_y + A1_MARK_LENGTH / 2],
    }
  else:
    pod.reference_point = Coordinate(a1_x, 0, 0)  # type: ignore[attr-defined]
    _add_span8_channels(
      rail,
      pod,
      geometry.bridge_to_head_x_offset,
      geometry.mandrel1_x_offset,
      geometry.mandrel1_y_offset,
      settings,
    )
  return rail, pod


def _add_span8_channels(
  rail: Resource,
  pod: Resource,
  to_head_x: float,
  m1x: float,
  m1y: float,
  settings: Optional[Mapping[str, Any]],
) -> None:
  """Hang a Span-8's probes from its bridge, at their narrowest span below the carriage: probe 1
  `Mandrel1YOffset` forward of the carriage's back edge, each next one `ProbeSize` further forward
  (deck y down), each channel spanning the pod in X. The device moves them from there
  (`place_span8_channel`)."""
  pod_loc = pod.location
  assert pod_loc is not None
  pitch = cm(float(dict(settings or {}).get("ProbeSize", heads.PROBE_SIZE)))
  back = pod_loc.y + pod.get_size_y() - cm(m1y)
  for i in range(1, heads.PROBES + 1):
    channel = heads.span8_channel(
      f"{pod.name}_channel_{i}", settings, m1x, pod.get_size_x() / MM_PER_CM
    )
    ref = channel.reference_point  # type: ignore[attr-defined]
    rail.assign_child_resource(
      channel,
      location=Coordinate(
        cm(to_head_x + m1x) - ref.x,
        back - (i - 1) * pitch - ref.y,
        pod_loc.z - channel.get_size_z(),
      ),
    )


def place_span8_channel(channel: Resource, x: float, y: float, z: float) -> None:
  """Put a Span-8 channel's probe end at `x`, `y`, `z` (mm, in its bridge's frame)."""
  ref = channel.reference_point  # type: ignore[attr-defined]
  channel.location = Coordinate(x - ref.x, y - ref.y, z - ref.z)
