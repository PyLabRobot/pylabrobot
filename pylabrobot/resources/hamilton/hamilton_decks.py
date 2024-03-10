from __future__ import annotations

from abc import ABCMeta, abstractmethod
import inspect
import logging
from typing import Optional, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.carrier import Carrier
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.trash import Trash
import pylabrobot.utils.file_parsing as file_parser


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

def _rails_for_x_coordinate(x: int):
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
      if resource_location.x + resource.get_size_x() > \
          self.rails_to_location(self.num_rails + 1).x and \
        rails is not None:
        raise ValueError(f"Resource with width {resource.get_size_x()} does not "
                        f"fit at rails {rails}.")

      # Check if there is space for this new resource.
      for og_resource in self.children:
        og_x = cast(Coordinate, og_resource.location).x
        og_y = cast(Coordinate, og_resource.location).y

        # A resource is not allowed to overlap with another resource. Resources overlap when a
        # corner of one resource is inside the boundaries of another resource.
        if any([
          og_x <= resource_location.x < og_x + og_resource.get_size_x(),
          og_x < resource_location.x + resource.get_size_x() < og_x + og_resource.get_size_x()
          ]) and any(
            [
              og_y <= resource_location.y < og_y + og_resource.get_size_y(),
              og_y < resource_location.y + resource.get_size_y() < og_y + og_resource.get_size_y()
            ]
          ):
          raise ValueError(f"Location {resource_location} is already occupied by resource "
                            f"'{og_resource.name}'.")

    return super().assign_child_resource(resource, location=resource_location, reassign=reassign)

  @classmethod
  def load_from_lay_file(cls, fn: str) -> HamiltonDeck:
    """ Parse a .lay file (legacy layout definition) and build the layout on this deck.

    Args:
      fn: Filename of .lay file.

    Examples:

      Loading from a lay file:

      >>> from pylabrobot.resources.hamilton import HamiltonDeck
      >>> deck = HamiltonSTARDeck.load_from_lay_file("deck.lay")
    """

    # pylint: disable=import-outside-toplevel, cyclic-import
    import pylabrobot.resources as resources_module

    c = None
    with open(fn, "r", encoding="ISO-8859-1") as f:
      c = f.read()

    deck_type = file_parser.find_string("Deck", c)

    num_rails = {"ML_Starlet.dck": STARLET_NUM_RAILS, "ML_STAR2.deck": STAR_NUM_RAILS}[deck_type]
    size_x = {"ML_Starlet.dck": STARLET_SIZE_X, "ML_STAR2.deck": STAR_SIZE_X}[deck_type]
    size_y = {"ML_Starlet.dck": STARLET_SIZE_Y, "ML_STAR2.deck": STAR_SIZE_Y}[deck_type]
    size_z = {"ML_Starlet.dck": STARLET_SIZE_Z, "ML_STAR2.deck": STAR_SIZE_Z}[deck_type]

    deck = cls(num_rails=num_rails,
      size_x=size_x, size_y=size_y, size_z=size_z,
      origin=Coordinate.zero())

    # Get class names of all defined resources.
    resource_classes = [c[0] for c in inspect.getmembers(resources_module)]

    # Get number of items on deck.
    num_items = file_parser.find_int("Labware.Cnt", c)

    # Collect all items on deck.

    containers = {}
    children = {}

    for i in range(1, num_items+1):
      name = file_parser.find_string(f"Labware.{i}.Id", c)

      # get class name (generated from file name)
      file_name = file_parser.find_string(f"Labware.{i}.File", c).split("\\")[-1]
      class_name = None
      if ".rck" in file_name:
        class_name = file_name.split(".rck")[0]
      elif ".tml" in file_name:
        class_name = file_name.split(".tml")[0]

      if class_name in resource_classes:
        klass = getattr(resources_module, class_name)
        resource = klass(name=name)
      else:
        logger.warning(
          "Resource with classname %s not found. Please file an issue at "
          "https://github.com/pylabrobot/pylabrobot/issues/new?assignees=&labels="
          "&title=Deserialization%%3A%%20Class%%20%s%%20not%%20found", class_name, class_name)
        continue

      # get location props
      # 'default' template means resource are placed directly on the deck, otherwise it
      # contains the name of the containing resource.
      if file_parser.find_string(f"Labware.{i}.Template", c) == "default":
        x = file_parser.find_float(f"Labware.{i}.TForm.3.X", c)
        y = file_parser.find_float(f"Labware.{i}.TForm.3.Y", c)
        z = file_parser.find_float(f"Labware.{i}.ZTrans", c)
        resource.location = Coordinate(x=x, y=y, z=z)
        containers[name] = resource
      else:
        children[name] = {
          "container": file_parser.find_string(f"Labware.{i}.Template", c),
          "site": file_parser.find_int(f"Labware.{i}.SiteId", c),
          "resource": resource}

    # Assign all containers to the deck.
    for cont in containers.values():
      deck.assign_child_resource(cont, location=cont.location)

    # Assign child resources to their parents.
    for child in children.values():
      cont = containers[child["container"]]
      cont[5 - child["site"]] = child["resource"]

    return deck

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

    def parse_resource(resource):
      # TODO: print something else if resource is not assigned to a rails.
      rails = _rails_for_x_coordinate(resource.location.x)
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

  def rails_to_location(self, rails: int) -> Coordinate:
    x = 100.0 + (rails - 1) * _RAILS_WIDTH
    return Coordinate(x=x, y=63, z=100)


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
