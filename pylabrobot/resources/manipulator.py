"""The moving mechanism of an arm: the links its joints turn between.

A manipulator is a chain of links and powered joints. A link is one rigid member of that chain and
nothing else: geometry is attached as children with their own origins - the separation a robot
description draws between a link's frame and its visual geometry - so material may extend past
either joint without entering the kinematics.
"""

from typing import Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


class Link(Resource):
  """One of the rigid pieces an arm is built from, joined to its neighbours by joints.

  What sets the reach is the distance between a link's joints, not the shape of the piece, so the
  material is attached as children with their own origins and may overhang a joint at either end.

  `joint` is where the joint it turns on sits within the link, which `turn_to` pivots about. It
  needs no particular place: a link is not obliged to put its own origin there.

  Unrotated it lies along +X.
  """

  def __init__(
    self,
    name: str,
    length: float,
    joint: Optional[Coordinate] = None,
    category: str = "link",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: what to call this one.
      length: joint to joint, in mm.
      joint: where the joint this link turns on sits within it. Its own origin when None.
      category: what kind of resource this is.
      model: which link this is.
    """
    super().__init__(
      name=name, size_x=length, size_y=0.0, size_z=0.0, category=category, model=model
    )
    self.joint = joint if joint is not None else Coordinate.zero()

  def turn_to(self, angle: float) -> None:
    """Point the link along `angle`, pivoting on `joint`.

    Args:
      angle: the angle to point along, in its parent's frame, in degrees.

    Raises:
      RuntimeError: If the link has not been placed, so there is nothing for it to turn in.
    """
    if self.location is None:
      raise RuntimeError(f"{self.name} is not placed, so there is nothing for it to turn in")
    self.rotate_to(z=angle, reference=self.joint)
