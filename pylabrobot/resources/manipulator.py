"""The moving mechanism of an arm: the links its joints turn between.

A manipulator is a chain of links and powered joints. A link is one rigid member of that chain,
and nothing else: the material bolted around it hangs off as children of its own, so the shape can
overhang either joint without the kinematics noticing. That is the split every robot description
makes, and it is what lets one length stand for the geometry and another for the part.
"""

from typing import Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation


class Link(Resource):
  """The span between the joint a link turns on and the joint it carries.

  A line, not a body: its length is the distance between two joints and it has no width or depth,
  so the joint it turns on is its own origin and turning it needs nothing taken out. The material
  around it hangs off as children with their own offsets, which is how a robot description keeps a
  link's frame apart from the shape bolted to it - the shape can overhang either joint without the
  kinematics noticing.

  Unrotated it lies along +X.
  """

  def __init__(
    self,
    name: str,
    length: float,
    category: str = "link",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: what to call this one.
      length: joint to joint, in mm.
      category: what kind of resource this is.
      model: which link this is.
    """
    super().__init__(
      name=name, size_x=length, size_y=0.0, size_z=0.0, category=category, model=model
    )

  def turn_to(self, angle: float, about: Optional[Coordinate] = None) -> None:
    """Point the link along `angle`, turning on the joint it is mounted on.

    Absolute, unlike `rotate`, which turns by an amount: a link driven to the same angle twice
    lands in the same place both times. The joint is the link's own origin, so turning does not
    move it and nothing has to be taken out.

    Args:
      angle: the deck angle to point along, in degrees.
      about: where the joint sits, in the frame this link is placed in. Left where it is when None.

    Raises:
      RuntimeError: If the link is not placed and no joint is given.
    """
    if about is not None:
      self.location = about
    if self.location is None:
      raise RuntimeError(f"{self.name} is not on a joint, so there is nothing for it to turn on")
    self.rotation = Rotation(z=angle)
    # `rotation` is a plain attribute, unlike `location`, so nothing hears about it being set.
    # Anything watching the model - a viewer, a collision check - learns of a joint moving here or
    # not at all.
    self._state_updated()
