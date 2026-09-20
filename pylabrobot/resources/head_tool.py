from typing import Optional

from pylabrobot.serializer import serialize

from .coordinate import Coordinate
from .resource import Resource


class HeadTool(Resource):
  """A tool that a liquid handling channel picks up and carries.

  Attributes:
    fitting_depth: the overlap between the tool and the channel, in mm.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    fitting_depth: float,
    category: Optional[str] = None,
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
  ):
    """Initialize a tool with an optional pickup location relative to its origin."""
    if not isinstance(name, str):
      raise TypeError("HeadTool name must be a string.")
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    self.fitting_depth = fitting_depth
    self.pick_up_location = pick_up_location

  def __eq__(self, other: object) -> bool:
    """Compare resource fields and the tool's fit and pickup location."""
    return (
      isinstance(other, HeadTool)
      and super().__eq__(other)
      and self.fitting_depth == other.fitting_depth
      and self.pick_up_location == other.pick_up_location
    )

  def __hash__(self) -> int:
    """Hash by the immutable resource name, which equal tools must share."""
    return hash(self.name)

  def serialize(self) -> dict:
    """Serialize the tool's resource fields, fitting depth, and pickup location."""
    return {
      **super().serialize(),
      "fitting_depth": self.fitting_depth,
      "pick_up_location": serialize(self.pick_up_location),
    }
