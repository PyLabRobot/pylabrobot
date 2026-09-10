"""End-effectors: what is fitted at an arm's mechanical interface, and the parts they are made of.

An end-effector - equally a tool, or end-of-arm tooling - is what an arm carries at its wrist
flange so that it can do its task. Its tool centre point is the point a move is programmed
against, stated as an offset from that flange, and it belongs to the tool rather than to the arm:
fit a different one and the point moves with it.

`MechanicalGripper` spans that offset, flange to grip centre, which is why it is a `Link`.
"""

from typing import Optional, Tuple, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import Link, bolt_on
from pylabrobot.resources.resource import Resource


class Finger(Resource):
  """One jaw of a gripper: what closes onto a resource, carrying the pad that touches it.

  A body and its pad, and no more than that yet. Two things it will carry once there is something
  to read them from: which of its faces makes contact, so a grip can be stated against the surface
  that holds rather than against the finger's own corner, and what the finger senses, since a
  gripper that reports force reports it per finger.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    category: str = "finger",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, model=model
    )
    self.pad: Optional[Resource] = None
    """What meets the resource, when the finger has one bolted to it."""


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
    body: Tuple[float, float, float, float, float],
    finger: Tuple[float, float, float, float, float],
    pad: Tuple[float, float, float, float, float],
    jaw_range: Tuple[float, float],
    jaw_width: Optional[float] = None,
    category: str = "mechanical_gripper",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: what to call this one.
      length: the joint it turns on to the grip centre, in mm.
      body: the body's size, how far along the link it starts, and how far above it stands, in mm.
      finger: the same for one finger. There are two, either side of the span.
      pad: the same for the pad on a finger's end, measured from the joint as the rest are.
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

    self.body = bolt_on(self, "body", body)
    self.fingers = [
      cast(Finger, bolt_on(self, f"finger_{side}", finger, of=Finger)) for side in ("left", "right")
    ]
    for on in self.fingers:
      on.pad = bolt_on(on, "pad", (pad[0], pad[1], pad[2], pad[3] - finger[3], pad[4] - finger[4]))
      # A pad is fixed to its finger, centred in the finger's thickness, so it sits the same way
      # on both of them. `bolt_on` centres material across a link, and a finger is not a link: its
      # own origin is a corner, so centring there leaves one pad inside the jaws and the other
      # outside them.
      where = cast(Coordinate, on.pad.location)
      on.pad.location = Coordinate(where.x, (finger[1] - pad[1]) / 2, where.z)
    self.pads = [cast(Resource, on.pad) for on in self.fingers]
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
    """Stand the fingers either side of the span, as far apart as the jaws are open."""
    for finger, side in zip(self.fingers, (1.0, -1.0)):
      here = cast(Coordinate, finger.location)
      finger.location = Coordinate(
        here.x, side * self._jaw_width / 2.0 - finger.get_size_y() / 2.0, here.z
      )

  def serialize(self) -> dict:
    return {**super().serialize(), "jaw_range": list(self.jaw_range)}
