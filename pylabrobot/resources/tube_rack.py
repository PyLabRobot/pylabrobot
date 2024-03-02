from typing import List, Optional

from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.tube import Tube


class TubeRack(ItemizedResource[Tube]):
  """ Tube rack resource. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    items: List[List[Tube]],
    model: Optional[str] = None,
  ):
    """ Initialize a TubeRack resource.

    Args:
      name: Name of the tube rack.
      size_x: Size of the tube rack in the x direction.
      size_y: Size of the tube rack in the y direction.
      size_z: Size of the tube rack in the z direction.
      items: List of lists of wells.
      model: Model of the tube rack.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      items=items,
      model=model)

  def disable_volume_trackers(self) -> None:
    """ Disable volume tracking for all wells in the plate. """

    for tube in self.get_all_items():
      tube.tracker.disable()

  def enable_volume_trackers(self) -> None:
    """ Enable volume tracking for all wells in the plate. """

    for tube in self.get_all_items():
      tube.tracker.enable()
