"""The moving mechanism of an arm: the bodies its joints turn between.

A manipulator is a chain of rigid members and powered joints. A `LinkBody` is one of those
members, an ordinary resource with its origin at a corner, carrying both of its joints as
coordinates within it. Geometry hangs off it as children with their own origins - the separation a
robot description draws between a member's frame and its visual geometry - so material may extend
past either joint without entering the kinematics.

The link is the line between the two joints. Nothing stores it: `length` is its only measure.
"""

import math
from typing import Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


class LinkBody(Resource):
  """One of the rigid members an arm is built from, joined to its neighbours by joints.

  Its origin is a corner, as any resource's is, and neither joint is obliged to sit there. It
  turns about `proximal_joint`, so a caller moves one with
  `rotate(z=angle, pivot_coordinate=body.proximal_joint)`.

  A member that ends the chain has no `distal_joint`, because nothing attaches past it. What sits
  at the far end of its span is that subclass's own business, and so is its `length`.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    proximal_joint: Coordinate,
    distal_joint: Optional[Coordinate] = None,
    category: str = "link_body",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: what to call this one.
      size_x: how far the member reaches along X, in mm.
      size_y: how far it reaches along Y, in mm.
      size_z: how far it reaches along Z, in mm.
      proximal_joint: where the joint this member turns on sits within it.
      distal_joint: where the joint the next member turns on sits within it. None on a member that
        ends the chain.
      category: what kind of resource this is.
      model: which member this is.
    """
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, model=model
    )
    self.proximal_joint = proximal_joint
    self.distal_joint = distal_joint

  @property
  def length(self) -> Optional[float]:
    """How long the link is, joint to joint, in mm. None on a member that ends the chain."""
    if self.distal_joint is None:
      return None
    return math.dist(self.distal_joint.vector(), self.proximal_joint.vector())

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "proximal_joint": self.proximal_joint.serialize(),
      "distal_joint": self.distal_joint.serialize() if self.distal_joint is not None else None,
    }
