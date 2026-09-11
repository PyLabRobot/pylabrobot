"""End-effectors: what is fitted at an arm's mechanical interface, and the parts they are made of.

An end-effector - equally a tool, or end-of-arm tooling - is what an arm carries at its wrist
flange so that it can do its task. Its tool centre point is the point a move is programmed
against, stated as an offset from that flange, and it belongs to the tool rather than to the arm:
fit a different one and the point moves with it.

`MechanicalGripper` spans that offset, flange to grip centre, which is why it is a `Link`.
"""

from typing import Optional, Sequence, Tuple, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import Link
from pylabrobot.resources.resource import Resource


class MechanicalGripper(Link):
  """A gripper that holds by closing two fingers on what it takes.

  A link: it spans the joint it turns on to the point it grips at, which is `tool_center_point`.
  Its body, its two fingers and a pad on each are material bolted to that span. The gap between
  the fingers is state rather than shape, so `jaw_width` moves them.
  """

  def __init__(
    self,
    name: str,
    length: float,
    body: Resource,
    body_location: Coordinate,
    fingers: Sequence[Resource],
    finger_location: Coordinate,
    jaw_range: Tuple[float, float],
    pads: Optional[Sequence[Resource]] = None,
    pad_location: Optional[Coordinate] = None,
    jaw_width: Optional[float] = None,
    category: str = "mechanical_gripper",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: what to call this one.
      length: the joint it turns on to the grip centre, in mm.
      body: the material around the span.
      body_location: where it sits, from the joint this gripper turns on.
      fingers: the two jaws, either side of the span.
      finger_location: where a finger sits along and above the span. Its Y is `jaw_width`'s.
      jaw_range: the gap between the fingers, closed and open, in mm.
      pads: what each finger meets the resource with, in the same order as `fingers`. A gripper
        whose fingers meet it themselves has none.
      pad_location: where a pad sits, from the finger it is fixed to. Given with `pads`.
      jaw_width: the gap to begin with, in mm. Open, when not given.
    """
    super().__init__(name=name, length=length, category=category, model=model)
    if len(fingers) != 2:
      raise ValueError(f"a gripper has two fingers, not {len(fingers)}")
    if (pads is None) != (pad_location is None):
      raise ValueError("pads and pad_location go together: give both, or neither")
    # Zipping a short list against a long one would drop material without saying so.
    if pads is not None and len(pads) != len(fingers):
      raise ValueError(f"a gripper has a pad on each finger, not {len(pads)} on {len(fingers)}")
    self.jaw_range = jaw_range

    self.body = body
    self.assign_child_resource(body, location=body_location)
    self.fingers = list(fingers)
    for jaw in self.fingers:
      self.assign_child_resource(jaw, location=finger_location)

    self.pads = list(pads) if pads is not None else []
    for jaw, face in zip(self.fingers, self.pads):
      jaw.assign_child_resource(face, location=cast(Coordinate, pad_location))

    # Through the setter, which is where a width is checked and the fingers are stood apart.
    self.jaw_width = jaw_range[1] if jaw_width is None else jaw_width

  @property
  def tool_center_point(self) -> Coordinate:
    """Where this tool is programmed against, as an offset from where it is mounted.

    Returns:
      The grip centre, which the fingers reach past.
    """
    return Coordinate(self.get_size_x(), 0.0, 0.0)

  @property
  def jaw_width(self) -> float:
    """The gap between the fingers' facing surfaces, in mm: what fits between them."""
    return self._jaw_width

  @jaw_width.setter
  def jaw_width(self, width: float) -> None:
    low, high = self.jaw_range
    if not low <= width <= high:
      raise ValueError(f"the jaws open {low} to {high} mm, not {width}")
    self._jaw_width = width
    self._place_the_fingers()

  def _place_the_fingers(self) -> None:
    """Stand the fingers either side of the span, leaving `jaw_width` of gap between them."""
    for finger, side in zip(self.fingers, (1.0, -1.0)):
      here = cast(Coordinate, finger.location)
      # A resource sits at its lowest-y corner: the facing surface on the +Y side, the back of the
      # finger on the -Y side.
      facing = side * self._jaw_width / 2.0
      finger.location = Coordinate(
        here.x, facing if side > 0 else facing - finger.get_size_y(), here.z
      )

  def serialize(self) -> dict:
    return {**super().serialize(), "jaw_range": list(self.jaw_range)}
