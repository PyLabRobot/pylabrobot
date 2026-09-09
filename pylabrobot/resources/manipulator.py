"""The moving mechanism of an arm: the links its joints turn between.

A manipulator is a chain of links and powered joints. A link is one rigid member of that chain,
and nothing else: the material bolted around it hangs off as children of its own, so the shape can
overhang either joint without the kinematics noticing. That is the split every robot description
makes, and it is what lets one length stand for the geometry and another for the part.
"""

from typing import Optional, Tuple, Type

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

  @property
  def far_joint(self) -> Coordinate:
    """The joint this link carries, in its own frame.

    Where the next link is placed, since a child is placed in its parent's own frame and the
    parent's rotation is applied on top of that.

    Returns:
      The far joint, from this link's near one.
    """
    return Coordinate(self.get_size_x(), 0.0, 0.0)

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


def bolt_on(
  link: Resource,
  what: str,
  part: Tuple[float, float, float, float, float],
  of: Type[Resource] = Resource,
) -> Resource:
  """Hang material on a link, centred across it and standing where the part says.

  The part is a model in its own right, named for the link it hangs on and what it is: a link is a
  line through its joints and carries no material itself, so anything to be said about the material
  - what it is made of, what it looks like - is said about the part rather than about the link.
  Two parts that are the same thing on either side of a span share the name, because they are one
  model mounted twice: the category is what the part is, where the name distinguishes the copies.
  A link with no model of its own has nothing to name its parts after, and they get none either.

  Args:
    link: the link it is bolted to.
    what: what the part is, which names it and gives it a category.
    part: its size, how far along the link it starts from the joint, and how far above the link
      it stands. A link is a line through the joints, so the material around it is rarely centred
      on it: an arm that steps down to its gripper hangs each part at its own height.
    of: what to make it, for material that is more than a box.

  Returns:
    The part.
  """
  category = what.split("_")[0]
  made = of(
    name=f"{link.name}_{what}",
    size_x=part[0],
    size_y=part[1],
    size_z=part[2],
    category=category,
    model=f"{link.model}_{category}" if link.model else None,
  )
  link.assign_child_resource(made, location=Coordinate(part[3], -part[1] / 2, part[4]))
  return made
