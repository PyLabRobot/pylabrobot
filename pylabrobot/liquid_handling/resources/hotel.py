from typing import Optional

from pylabrobot.liquid_handling.resources.abstract.coordinate import Coordinate
from pylabrobot.liquid_handling.resources.abstract.resource import Resource


class Hotel(Resource):
  """ A place to store resources in a
  `stack <https://en.wikipedia.org/wiki/Stack_(abstract_data_type)>`_. The most common use case is
  to store :class:`~abstract.Plate` or :class:`~abstract.Lid` resources.

  .. DANGER::
    Note that :meth:`getting the absolute location
    <pyhamilton.liquid_handling.resources.abstract.Resource.get_absolute_location>` of resources in
    the hotel is not supported.

  Examples:
    Creating a hotel and adding it to the liquid handler.

    >>> hotel = Hotel(name="Hotel", size_x=100, size_y=100, size_z=100,
    ...               location=Coordinate(0, 0, 0))
    >>> lh.add_resource(hotel)
    >>> hotel.assign_child_resource(plate)

    :meth:`Moving <pyhamilton.liquid_handling.LiquidHandler.move_plate>` a plate to the hotel.

    >>> lh.move_plate(plate, hotel)

    :meth:`Moving <pyhamilton.liquid_handling.LiquidHandler.move_lid>` a lid to the hotel.

    >>> lh.move_lid(plate.lid, hotel)

    Getting a plate from the hotel and moving it to a
    :class:`~abstract.PlateCarrier`.

    >>> lh.move_plate(hotel.get_top_item(), plt_car[0])
  """

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    location: Coordinate = Coordinate(None, None, None)):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z,
      location=location, category="hotel")

  def get_top_item(self) -> Optional[Resource]:
    """ Get the top item in the hotel. """
    if len(self.children) > 0:
      return self.children[-1]
    return None

  def get_height(self) -> float:
    return sum(child.get_size_z() for child in self.children) - \
           sum(child.stack_height for child in self.children if hasattr(child, "stack_height"))

  def get_size_z(self) -> float:
    return super().get_size_z() + self.get_height()

  def assign_child_resource(self, resource: Resource, **kwargs):
    resource.location = Coordinate(0, 0, self.get_height())
    return super().assign_child_resource(resource, **kwargs)

  def unassign_child_resource(self, resource: Resource):
    if resource != self.children[-1]:
      raise ValueError("Resource is not the top item in the hotel, cannot unassign")
    resource.location = Coordinate(0, 0, 0) # reset location to 0, 0, 0
    return super().unassign_child_resource(resource)
