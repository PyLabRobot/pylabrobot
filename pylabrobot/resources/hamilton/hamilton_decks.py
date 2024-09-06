from __future__ import annotations

from abc import ABCMeta, abstractmethod
import logging
from typing import Optional, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.carrier import CarrierSite
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.ml_star.mfx_modules import MFXModule


logger = logging.getLogger("pylabrobot")


_RAILS_WIDTH = 22.5 # space between rails (mm)

STARLET_NUM_RAILS=30
STARLET_SIZE_X=1360
STARLET_SIZE_Y=653.5
STARLET_SIZE_Z=900

STAR_NUM_RAILS=55
STAR_SIZE_X=1900
STAR_SIZE_Y=653.5
STAR_SIZE_Z=900

def _rails_for_x_coordinate(x: float) -> int:
  """ Convert an x coordinate to a rail identifier. """
  return int((x - 100.0) / _RAILS_WIDTH) + 1


class HamiltonDeck(Deck, metaclass=ABCMeta):
  """ Hamilton decks. Currently only STARLet, STAR and Vantage are supported. """

  def __init__(
    self,
    num_rails: int,
    size_x: float,
    size_y: float,
    size_z: float,
    name: str = "deck",
    category: str = "deck",
    origin: Coordinate = Coordinate.zero(),
  ):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      origin=origin)
    self.num_rails = num_rails
    self.register_did_assign_resource_callback(self._check_save_z_height)

  @abstractmethod
  def rails_to_location(self, rails: int) -> Coordinate:
    """ Convert a rail identifier to an absolute (x, y, z) coordinate. """

  def serialize(self) -> dict:
    """ Serialize this deck. """
    return {
      **super().serialize(),
      "num_rails": self.num_rails,
      "no_trash": True # data encoded as child. (not very pretty to have this key though...)
    }

  def _check_save_z_height(self, resource: Resource):
    """" Check for this resource, and all its children, that the z location is not too high. """

    # TODO: maybe these are parameters per HamiltonDeck that we can take as attributes.
    Z_MOVEMENT_LIMIT = 245
    Z_GRAB_LIMIT = 285

    def check_z_height(resource: Resource):
      z_top = resource.get_absolute_location(z="top").z

      if z_top > Z_MOVEMENT_LIMIT:
        logger.warning("Resource '%s' is very high on the deck: %s mm. Be careful when "
                        "traversing the deck.", resource.name, z_top)

      if z_top > Z_GRAB_LIMIT:
        logger.warning("Resource '%s' is very high on the deck: %s mm. Be careful when "
                        "grabbing this resource.", resource.name, z_top)

      for child in resource.children:
        check_z_height(child)

    check_z_height(resource)


  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = False,
    rails: Optional[int] = None,
    replace=False
  ):
    """ Assign a new deck resource.

    The identifier will be the Resource.name, which must be unique amongst previously assigned
    resources.

    Note that some resources, such as tips on a tip carrier or plates on a plate carrier must
    be assigned directly to the tip or plate carrier respectively. See TipCarrier and PlateCarrier
    for details.

    Based on the rails argument, the absolute (x, y, z) coordinates will be computed.

    Args:
      resource: A Resource to assign to this liquid handler.
      location: The location of the resource relative to the liquid handler. Either rails or
        location must be `None`, but not both.
      reassign: If True, reassign the resource if it is already assigned. If False, raise a
        `ValueError` if the resource is already assigned.
      rails: The left most real (inclusive) of the deck resource (between and 1-30 for STARLet,
        max 55 for STAR.) Either rails or location must be None, but not both.
      location: The location of the resource relative to the liquid handler. Either rails or
        location must be None, but not both.
      replace: Replace the resource with the same name that was previously assigned, if it exists.
        If a resource is assigned with the same name and replace is False, a ValueError
        will be raised.

    Raises:
      ValueError: If a resource is assigned with the same name and replace is `False`.
    """

    # TODO: many things here should be moved to Resource and Deck, instead of just STARLetDeck

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
      resource_location = self.rails_to_location(rails)
    elif location is not None:
      resource_location = location
    else:
      resource_location = None # unknown resource location

    if resource_location is not None: # collision detection
      if resource_location.x + resource.get_absolute_size_x() > \
          self.rails_to_location(self.num_rails + 1).x and \
        rails is not None:
        raise ValueError(f"Resource with width {resource.get_absolute_size_x()} does not "
                        f"fit at rails {rails}.")

      # Check if there is space for this new resource.
      for og_resource in self.children:
        og_x = cast(Coordinate, og_resource.location).x
        og_y = cast(Coordinate, og_resource.location).y

        # A resource is not allowed to overlap with another resource. Resources overlap when a
        # corner of one resource is inside the boundaries of another resource.
        if any([
          og_x <= resource_location.x < og_x + og_resource.get_absolute_size_x(),
          og_x < resource_location.x + resource.get_absolute_size_x() \
            < og_x + og_resource.get_absolute_size_x()
          ]) and any(
            [
              og_y <= resource_location.y < og_y + og_resource.get_absolute_size_y(),
              og_y < resource_location.y + resource.get_absolute_size_y() \
                < og_y + og_resource.get_absolute_size_y()
            ]
          ):
          raise ValueError(f"Location {resource_location} is already occupied by resource "
                            f"'{og_resource.name}'.")

    return super().assign_child_resource(resource, location=resource_location, reassign=reassign)

  def summary(self) -> str:
    """ Return a summary of the deck.

    Example:
      Printing a summary of the deck layout:

      >>> print(deck.summary())
      Rail     Resource                   Type                Coordinates (mm)
      =============================================================================================
      (1)  ├── tip_car                    TIP_CAR_480_A00     (x: 100.000, y: 240.800, z: 164.450)
           │   ├── tip_rack_01            STF_L               (x: 117.900, y: 240.000, z: 100.000)
    """

    if len(self.get_all_resources()) == 0:
      raise ValueError(
        "This liquid editor does not have any resources yet. "
        "Build a layout first by calling `assign_child_resource()`. "
      )

    # don't print these
    exclude_categories = {"well", "tube", "tip_spot", "carrier_site", "plate_carrier_site"}

    def find_longest_child_name(resource: Resource, depth=0, depth_weight=4):
      """ DFS to find longest child name, and depth of that child, excluding excluded categories """
      l, d = (len(resource.name), depth) if resource.category not in exclude_categories else (0, 0)
      new_depth = depth + 1 if resource.category not in exclude_categories else depth
      return max([(l + d*depth_weight)] +
                 [find_longest_child_name(c, new_depth) for c in resource.children])


    def find_longest_type_name(resource: Resource):
      """ DFS to find the longest type name """
      l = len(resource.__class__.__name__) if resource.category not in exclude_categories else 0
      return max([l] + [find_longest_type_name(child) for child in resource.children])

    # Calculate the maximum lengths of the resource name and type for proper alignment
    max_name_length = find_longest_child_name(self)
    max_type_length = find_longest_type_name(self)

    # Find column lengths
    rail_column_length = 6
    name_column_length = max(max_name_length + 4, 30) # 4 per depth (by find_longest_child), 4 extra
    type_column_length = max_type_length + 3 - 4
    location_column_length = 30

    # Print header
    summary_ = (
      "Rail".ljust(rail_column_length) +
      "Resource".ljust(name_column_length) +
      "Type".ljust(type_column_length) +
      "Coordinates (mm)".ljust(location_column_length) +
      "\n"
    )
    total_length = rail_column_length + name_column_length + type_column_length + \
      location_column_length
    summary_ += "=" * total_length + "\n"

    def make_tree_part(depth: int) -> str:
      tree_part = "├── "
      for _ in range(depth):
        tree_part = "│   " + tree_part
      return tree_part

    def print_empty_spot_line(depth=0) -> str:
      r_summary = " " * rail_column_length
      tree_part = make_tree_part(depth)
      r_summary += (tree_part + "<empty>").ljust(name_column_length)
      return r_summary

    def print_resource_line(resource: Resource, depth=0) -> str:
      r_summary = ""

      # Print rail
      if depth == 0:
        rails = _rails_for_x_coordinate(resource.get_absolute_location().x)
        r_summary += f"({rails})".ljust(rail_column_length)
      else:
        r_summary += " " * rail_column_length

      # Print resource name
      tree_part = make_tree_part(depth)
      r_summary += (tree_part + resource.name).ljust(name_column_length)

      # Print resource type
      r_summary += resource.__class__.__name__.ljust(type_column_length)

      # Print resource location
      location = resource.get_absolute_location()
      r_summary += str(location).ljust(location_column_length)

      return r_summary

    def print_tree(resource: Resource, depth=0):
      r_summary = print_resource_line(resource, depth=depth)

      if isinstance(resource, MFXModule) and len(resource.children) == 0:
        r_summary += "\n"
        r_summary += print_empty_spot_line(depth=depth+1)

      for child in resource.children:
        if isinstance(child, CarrierSite):
          r_summary += "\n"
          if child.resource is not None:
            r_summary += print_tree(child.resource, depth=depth+1)
          else:
            r_summary += print_empty_spot_line(depth=depth+1)
        elif child.category not in exclude_categories:
          r_summary += "\n"
          r_summary += print_tree(child, depth=depth+1)

      return r_summary

    # Sort resources by rails, left to right in reality.
    sorted_resources = sorted(self.children, key=lambda r: r.get_absolute_location().x)

    # Print table body.
    summary_ += print_tree(sorted_resources[0]) + "\n"
    for resource in sorted_resources[1:]:
      summary_ += "      │\n"
      summary_ += print_tree(resource)
      summary_ += "\n"

    # Truncate trailing whitespace from each line
    summary_ = "\n".join([line.rstrip() for line in summary_.split("\n")])

    return summary_


class HamiltonSTARDeck(HamiltonDeck): # pylint: disable=invalid-name
  """ Base class for a Hamilton STAR(let) deck. """

  def __init__(
    self,
    num_rails: int,
    size_x: float,
    size_y: float,
    size_z: float,
    name="deck",
    category: str = "deck",
    origin: Coordinate = Coordinate.zero(),
    no_trash: bool = False,
  ) -> None:
    """ Create a new STAR(let) deck of the given size. """

    super().__init__(
      num_rails=num_rails,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      name=name,
      category=category,
      origin=origin)

    # assign trash area
    if not no_trash:
      trash_x = size_x - 560 # only tested on STARLet, assume STAR is same distance from right max..

      self.assign_child_resource(
        resource=Trash("trash", size_x=0, size_y=241.2, size_z=0),
        location=Coordinate(x=trash_x, y=190.6, z=137.1)) # z I am not sure about

      # got this location from a .lay file, but will probably need to be adjusted by the user.
      self._trash96 = Trash("trash_core96", size_x=82.6, size_y=122.4, size_z=0) # size of tiprack
      self.assign_child_resource(
        resource=self._trash96,
        location=Coordinate(x=-232.1, y=110.3, z=189.0)) # 165.0 -> 189.0

  def rails_to_location(self, rails: int) -> Coordinate:
    x = 100.0 + (rails - 1) * _RAILS_WIDTH
    return Coordinate(x=x, y=63, z=100)

  def get_trash_area96(self) -> Trash:
    return self._trash96


def STARLetDeck( # pylint: disable=invalid-name
  origin: Coordinate = Coordinate.zero(),
) -> HamiltonSTARDeck:
  """ Create a new STARLet deck.

  Sizes from `HAMILTON\\Config\\ML_Starlet.dck`
  """

  return HamiltonSTARDeck(
    num_rails=STARLET_NUM_RAILS,
    size_x=STARLET_SIZE_X,
    size_y=STARLET_SIZE_Y,
    size_z=STARLET_SIZE_Z,
    origin=origin)


def STARDeck( # pylint: disable=invalid-name
  origin: Coordinate = Coordinate.zero(),
) -> HamiltonSTARDeck:
  """ Create a new STAR deck.

  Sizes from `HAMILTON\\Config\\ML_STAR2.dck`
  """

  return HamiltonSTARDeck(
    num_rails=STAR_NUM_RAILS,
    size_x=STAR_SIZE_X,
    size_y=STAR_SIZE_Y,
    size_z=STAR_SIZE_Z,
    origin=origin)
