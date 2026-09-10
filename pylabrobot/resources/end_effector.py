"""End-effectors: what is fitted at an arm's mechanical interface, and the parts they are made of.

An end-effector - equally a tool, or end-of-arm tooling - is what an arm carries at its wrist
flange so that it can do its task. Its tool centre point is the point a move is programmed
against, stated as an offset from that flange, and it belongs to the tool rather than to the arm:
fit a different one and the point moves with it.

`MechanicalGripper` spans that offset, flange to grip centre, which is why it is a `Link`.
"""

from typing import Optional, Tuple, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import Link
from pylabrobot.resources.resource import Resource


class MechanicalGripper(Link):
  """A gripper that holds by closing two fingers on what it takes.

  A link, because on an arm that is what it is: it spans the joint it turns on to the point it
  grips at, which is `tool_center_point`. Its body, its two fingers and the pad on each are material
  bolted to that span. How far apart the fingers stand is state rather than shape, so `jaw_width`
  moves them.
  """

  def __init__(
    self,
    name: str,
    length: float,
    body: Coordinate,
    body_location: Coordinate,
    finger: Coordinate,
    finger_location: Coordinate,
    pad: Coordinate,
    pad_location: Coordinate,
    jaw_range: Tuple[float, float],
    jaw_width: Optional[float] = None,
    category: str = "mechanical_gripper",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: what to call this one.
      length: the joint it turns on to the grip centre, in mm.
      body: how big the body is, in mm.
      body_location: where it sits, from the joint this gripper turns on.
      finger: how big one finger is, in mm. There are two, either side of the span.
      finger_location: where a finger sits along and above the span. Its Y is `jaw_width`'s, so
        what stands here for it is not used.
      pad: how big the pad on a finger's end is, in mm.
      pad_location: where it sits, from the finger it is fixed to.
      jaw_range: how far apart the fingers stand, closed and open, in mm.
      jaw_width: how far apart they stand to begin with, in mm. Where a gripper is known to come
        up at a particular width - the one it homes at, say - that is what to build it at, so the
        model does not start out claiming a width nothing has read. Open, when not given.
    """
    super().__init__(name=name, length=length, category=category, model=model)
    self.jaw_range = jaw_range
    self._jaw_width = jaw_range[1] if jaw_width is None else jaw_width
    low, high = jaw_range
    if not low <= self._jaw_width <= high:
      raise ValueError(f"the jaws open {low} to {high} mm, so cannot start at {self._jaw_width}")

    self.body = Resource(
      name=f"{name}_body",
      size_x=body.x,
      size_y=body.y,
      size_z=body.z,
      category="body",
      model=f"{model}_body" if model else None,
    )
    self.assign_child_resource(self.body, location=body_location)

    # A finger has a size and no place of its own: `jaw_width` decides where it stands, and
    # `_place_the_fingers` is what puts it there.
    self.fingers = [
      Resource(
        name=f"{name}_finger_{side}",
        size_x=finger.x,
        size_y=finger.y,
        size_z=finger.z,
        category="finger",
        model=f"{model}_finger" if model else None,
      )
      for side in ("left", "right")
    ]
    for jaw in self.fingers:
      self.assign_child_resource(
        jaw, location=Coordinate(finger_location.x, 0.0, finger_location.z)
      )

    self.pads = []
    for jaw in self.fingers:
      face = Resource(
        name=f"{jaw.name}_pad",
        size_x=pad.x,
        size_y=pad.y,
        size_z=pad.z,
        category="pad",
        model=f"{jaw.model}_pad" if jaw.model else None,
      )
      jaw.assign_child_resource(face, location=pad_location)
      self.pads.append(face)

    self._place_the_fingers()

  @property
  def tool_center_point(self) -> Coordinate:
    """The tool center point: where this tool is programmed against, as an offset from where it is
    mounted.

    A gripper's far joint carries nothing, so what sits there is the point it grips at.

    In PyLabRobot a tool center point is always this offset - a property of the tool, which changes
    when a different one is fitted and not when the arm moves. Robot controllers also use the term
    for where that point currently is in the robot's frame; here that is a location, and something
    an arm answers rather than a tool.

    Returns:
      The grip centre, from the joint this gripper turns on.
    """
    return self.far_joint

  @property
  def jaw_width(self) -> float:
    """How far apart the fingers stand, in mm."""
    return self._jaw_width

  @jaw_width.setter
  def jaw_width(self, width: float) -> None:
    low, high = self.jaw_range
    if not low <= width <= high:
      raise ValueError(f"the jaws open {low} to {high} mm, not {width}")
    self._jaw_width = width
    self._place_the_fingers()

  def _place_the_fingers(self) -> None:
    """Stand the fingers either side of the span, as far apart as the jaws are open.

    A finger is the one part of a gripper whose Y is not fixed: the jaw width owns it outright.
    """
    for finger, side in zip(self.fingers, (1.0, -1.0)):
      here = cast(Coordinate, finger.location)
      finger.location = Coordinate(
        here.x, side * self._jaw_width / 2.0 - finger.get_size_y() / 2.0, here.z
      )

  def serialize(self) -> dict:
    return {**super().serialize(), "jaw_range": list(self.jaw_range)}
