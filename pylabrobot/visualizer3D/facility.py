"""The space a site's automation stands in, as a `Resource`.

Its frame is what everything in it resolves against; the tree is the only structure above a
device.
"""

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


class Facility(Resource):
  """The space a site's automation stands in, and the frame everything in it resolves against.

  Its children are whatever is on the floor: a device, a shuttle between devices, a bench that is
  not automated at all.
  """

  def __init__(
    self,
    name: str = "facility",
    size_x: float = 10_000.0,
    size_y: float = 10_000.0,
    size_z: float = 3_000.0,
  ):
    """
    Args:
      name: what to call this facility.
      size_x: how wide the space is, in mm.
      size_y: how deep it is, in mm.
      size_z: how tall it is, in mm.
    """
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category="facility")
    # A facility is the frame everything else resolves against, so it sits at its own origin.
    # Without this every absolute lookup below it raises, since the root has nothing to resolve to.
    self.location = Coordinate.zero()
