"""The moving mechanism of an arm: the links its joints turn between.

A manipulator is a chain of links and powered joints. A link is one rigid member of that chain and
nothing else: geometry is attached as children with their own origins - the separation a robot
description draws between a link's frame and its visual geometry - so material may extend past
either joint without entering the kinematics.
"""

from typing import Optional

from pylabrobot.resources.resource import Resource


class Link(Resource):
  """One of the rigid pieces an arm is built from, joined to its neighbours by joints.

  It turns about its own origin, and unrotated it lies along +X, so the joint at its far end is at
  `length`.
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
