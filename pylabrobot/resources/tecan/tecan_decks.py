from typing import Optional, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.carrier import Carrier
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.tecan.tecan_resource import TecanResource
from pylabrobot.resources.tecan.wash import (
  Wash_Station,
  Wash_Station_Cleaner_shallow,
  Wash_Station_Waste,
  Wash_Station_Cleaner_deep
)


_RAILS_WIDTH = 25

EVO100_NUM_RAILS = 30
EVO100_SIZE_X = 940
EVO100_SIZE_Y = 780
EVO100_SIZE_Z = 765

EVO150_NUM_RAILS = 45
EVO150_SIZE_X = 1315
EVO150_SIZE_Y = 780
EVO150_SIZE_Z = 765

EVO200_NUM_RAILS = 69
EVO200_SIZE_X = 1915
EVO200_SIZE_Y = 780
EVO200_SIZE_Z = 765


class TecanDeck(Deck):
  """ Tecan decks """

  def __init__(
    self,
    num_rails: int,
    size_x: float,
    size_y: float,
    size_z: float,
    name: str = "deck",
    category: str = "deck",
    origin: Coordinate = Coordinate(0, 0, 0),
  ):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      origin=origin)
    self.num_rails = num_rails

    wash = Wash_Station(name="wash_station")
    wash[0] = Wash_Station_Cleaner_deep(name="wash_clean_deep")
    wash[1] = Wash_Station_Waste(name="wash_waste")
    wash[2] = Wash_Station_Cleaner_shallow(name="wash_clean_shallow")
    self.assign_child_resource(wash, rails=1)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "num_rails": self.num_rails
    }

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = False,
    rails: Optional[int] = None,
    replace=False
  ):
    """ Assign a new deck resource. """

    if rails is not None and not 1 <= rails <= self.num_rails:
      raise ValueError(f"Rails must be between 1 and {self.num_rails}.")

    # Check if resource exists.
    if self.has_resource(resource.name):
      if replace:
        # unassign first, so we don't have problems with location checking later.
        cast(Resource, self.get_resource(resource.name)).unassign()
      else:
        raise ValueError(f"Resource with name '{resource.name}' already defined.")

    if rails is not None:
      resource_location = self._coordinate_for_rails(rails, resource)
    elif location is not None:
      resource_location = location
    else:
      resource_location = None # unknown resource location

    if resource_location is not None:
      if resource_location.x + resource.get_size_x() > self.get_size_x() and \
        rails is not None:
        raise ValueError(f"Resource with width {resource.get_size_x()} does not "
                        f"fit at rails {rails}.")

      # Check if there is space for this new resource.
      for og_resource in self.children:
        og_x = cast(Coordinate, og_resource.location).x
        og_y = cast(Coordinate, og_resource.location).y

        # A resource is not allowed to overlap with another resource. Resources overlap when a
        # corner of one resource is inside the boundaries other resource.
        if (og_x <= resource_location.x < og_x + og_resource.get_size_x() or \
          og_x <= resource_location.x + resource.get_size_x() <
            og_x + og_resource.get_size_x()) and \
            (og_y <= resource_location.y < og_y + og_resource.get_size_y() or \
              og_y <= resource_location.y + resource.get_size_y() <
                og_y + og_resource.get_size_y()):
          raise ValueError(f"Location {resource_location} is already occupied by resource "
                            f"'{og_resource.name}'.")

    return super().assign_child_resource(resource, location=resource_location)

  def _coordinate_for_rails(self, rails: int, resource: Resource):
    """ Convert a rail identifier and resource to a location. """

    if not isinstance(resource, TecanResource):
      raise ValueError(f"Resource {resource} is not a Tecan resource.")

    return Coordinate(
      (rails - 1) * _RAILS_WIDTH - resource.off_x + 100,
      resource.off_y + 345 - resource.get_size_y(), 0) # TODO: verify

  def _rails_for_x_coordinate(self, x: float):
    """ Convert an x coordinate to a rail identifier. """

    return round((x + _RAILS_WIDTH - 101) / _RAILS_WIDTH) + 1

  def summary(self) -> str:
    """ Return a summary of the deck.

    Example:
      Printing a summary of the deck layout:

      >>> print(deck.summary())
      Rail     Resource                   Type                Coordinates (mm)
      ==============================================================================================
      (1) ├── tip_car                    TIP_CAR_480_A00     (x: 100.000, y: 240.800, z: 164.450)
          │   ├── tip_rack_01            STF_L               (x: 117.900, y: 240.000, z: 100.000)
    """

    if len(self.get_all_resources()) == 0:
      raise ValueError(
          "This liquid editor does not have any resources yet. "
          "Build a layout first by calling `assign_child_resource()`. "
      )

    # Print header.
    summary_ = "Rail" + " " * 5 + "Resource" + " " * 19 +  "Type" + " " * 16 + "Coordinates (mm)\n"
    summary_ += "=" * 95 + "\n"

    def parse_resource(resource): # pylint: disable=invalid-name
      # TODO: print something else if resource is not assigned to a rails.
      rails = self._rails_for_x_coordinate(resource.location.x)
      rail_label = f"({rails})" if rails is not None else "     "
      r_summary = f"{rail_label:4} ├── {resource.name:27}" + \
            f"{resource.__class__.__name__:20}" + \
            f"{resource.get_absolute_location()}\n"

      if isinstance(resource, Carrier):
        for site in resource.get_sites():
          if site.resource is None:
            r_summary += "     │   ├── <empty>\n"
          else:
            subresource = site.resource
            if isinstance(subresource, (TipRack, Plate)):
              location = subresource.get_item("A1").get_absolute_location() + \
                subresource.get_item("A1").center()
            else:
              location = subresource.get_absolute_location()
            r_summary += f"     │   ├── {subresource.name:23}" + \
                  f"{subresource.__class__.__name__:20}" + \
                  f"{location}\n"

      return r_summary

    # Sort resources by rails, left to right in reality.
    sorted_resources = sorted(self.children, key=lambda r: r.get_absolute_location().x)

    # Print table body.
    summary_ += parse_resource(sorted_resources[0])
    for resource in sorted_resources[1:]:
      summary_ += "     │\n"
      summary_ += parse_resource(resource)

    return summary_

# pylint: disable=invalid-name
def EVO100Deck(origin: Coordinate = Coordinate(0, 0, 0)) -> TecanDeck:
  """ EVO100 deck.

  Sizes from operating manual
  """

  return TecanDeck(
      num_rails=EVO100_NUM_RAILS,
      size_x=EVO100_SIZE_X,
      size_y=EVO100_SIZE_Y,
      size_z=EVO100_SIZE_Z,
      origin=origin)


# pylint: disable=invalid-name
def EVO150Deck(origin: Coordinate = Coordinate(0, 0, 0)) -> TecanDeck:
  """ EVO150 deck.

  Sizes from operating manual
  """

  return TecanDeck(
      num_rails=EVO150_NUM_RAILS,
      size_x=EVO150_SIZE_X,
      size_y=EVO150_SIZE_Y,
      size_z=EVO150_SIZE_Z,
      origin=origin)


# pylint: disable=invalid-name
def EVO200Deck(origin: Coordinate = Coordinate(0, 0, 0)) -> TecanDeck:
  """ EVO200 deck.

  Sizes from operating manual
  """

  return TecanDeck(
      num_rails=EVO200_NUM_RAILS,
      size_x=EVO200_SIZE_X,
      size_y=EVO200_SIZE_Y,
      size_z=EVO200_SIZE_Z,
      origin=origin)
