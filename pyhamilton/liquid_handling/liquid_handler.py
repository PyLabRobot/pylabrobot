import copy
import typing

from .backends import LiquidHandlerBackend
from .errors import (
  NoTipsException
)
from .resources import Resource, Coordinate, Carrier
# from .liquid_classes import LiquidClass


_RAILS_WIDTH = 22.5 # space between rails (mm)


def _pad_string(item: str, desired_length: int, left=False): # TODO: move to util
  length = None
  if isinstance(item, str):
    length = len(item)
  elif isinstance(item, int):
    length = item // 10
  spaces = max(0, desired_length - length) * " "
  item = str(item)
  return (spaces+item) if left else (item+spaces)


class LiquidHandler:
  """
  Front end for liquid handlers.
  """

  def __init__(self, backend: LiquidHandlerBackend):
    self.backend = backend
    self._resources = {}

  def setup(self):
    """ Prepare the robot for use. """

    self.backend.setup()

  def stop(self):
    self.backend.stop()

  def __enter__(self):
    self.setup()
    return self

  def __exit__(self, *exc):
    self.stop()
    return False

  @staticmethod
  def _x_coordinate_for_rails(rails: int):
    """ Convert a rail identifier (1-30 for STARLet, max 54 for STAR) to an x coordinate. """
    return 100.0 + (rails - 1) * _RAILS_WIDTH

  @staticmethod
  def _rails_for_x_coordinate(x: int):
    """ Convert an x coordinate to a rail identifier (1-30 for STARLet, max 54 for STAR). """
    return int((x - 100.0) / _RAILS_WIDTH) + 1

  def assign_resource(
    self,
    resource: Resource,
    rails: int, # board location, 1..52
    # y: int, # board location, x..y?
    replace: bool = False
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
      rails: The left most real (inclusive) of the deck resource (between and 1-30 for STARLet,
             max 54 for STAR.)
      replace: Replace the resource with the same name that was previously assigned, if it exists.
               If a resource is assigned with the same name and replace is False, a ValueError
               will be raised.
    """

    # Check if resource exists.
    if resource.name in self._resources:
      if replace:
        # unassign first, so we don't have problems with location checking later.
        self.unassign_resource(resource.name)
      else:
        raise ValueError(f"Resource with name '{resource.name}' already defined.")

    # Set resource location.
    resource.location = Coordinate(x=LiquidHandler._x_coordinate_for_rails(rails), y=63, z=100)

    # Check if there is space for this new resource.
    for og_resource in self._resources.values():
      og_x = og_resource.location.x

      # No space if start or end (=x+width) between start and end of current ("og") resource.
      if og_x <= resource.location.x < og_x + og_resource.size_x or \
         og_x <= resource.location.x + resource.size_x < og_x + og_resource.size_x:
        resource.location = None # Revert location.
        raise ValueError(f"Rails {rails} is already occupied by resource '{og_resource.name}'.")

    self._resources[resource.name] = resource

  def unassign_resource(self, name: str):
    """ Unassign an assigned resource.

    Raises:
      KeyError: If the resource is not currently assigned to this liquid handler.
    """

    del self._resources[name]

  def read_layout_from_layfile(self, name: str):
    pass # TODO: this

  def read_layout_from_json(self, name: str):
    pass # TODO: this

  def save_layout_to_json(self):
    pass # TODO: this

  def get_resource(self, name: str) -> typing.Optional[Resource]:
    """ Find a resource in self or contained in a carrier in self.

    Args:
      name: name of the resource.

    Returns:
      A deep copy of resource with name `name`, if it exists, else None. Location will be
      updated to represent the location within the liquid handler.
    """

    for key, resource in self._resources.items():
      if key == name:
        return copy.deepcopy(resource)

      if isinstance(resource, Carrier):
        for subresource in resource.get_items():
          if subresource is not None and subresource.name == name:
            # TODO: Why do we need `+ Coordinate(0, resource.location.y, 0)`??? (=63)
            subresource.location += (resource.location + Coordinate(0, resource.location.y, 0))
            return subresource

    return None

  def summary(self):
    """ Prints a string summary of the deck layout.

    Example output:

    Rail     Resource             Type                Coordinates (mm)
    ==========================================================================================
     (1) ├── tip_car               TIP_CAR_480_A00     (x: 100.000, y: 240.800, z: 164.450)
         │   ├── tips_01           STF_L               (x: 117.900, y: 240.000, z: 100.000)
    """

    if len(self._resources) == 0:
      raise ValueError(
          "This liquid editor does not have any resources yet. "
          "Build a layout first by calling `assign_resource()`. "
          "See the documentation for details. (TODO: link)"
      )

    # Print header.
    print(_pad_string("Rail", 9) + _pad_string("Resource", 22) + \
          _pad_string("Type", 20) + "Coordinates (mm)")
    print("=" * 85)

    def print_resource(resource):
      rails = LiquidHandler._rails_for_x_coordinate(resource.location.x)
      rail_label = _pad_string(f"({rails})", 4)
      print(f"{rail_label} ├── {_pad_string(resource.name, 22)}"
            f"{_pad_string(resource.__class__.__name__, 20)}"
            f"{resource.location}")

      if isinstance(resource, Carrier):
        for subresource in resource.get_items():
          if subresource is None:
            print("     │   ├── <empty>")
          else:
            # Get subresource using `self.get_resource` to update it with the new location.
            subresource = self.get_resource(subresource.name)
            print(f"     │   ├── {_pad_string(subresource.name, 22-4)}"
                  f"{_pad_string(subresource.__class__.__name__, 20)}"
                  f"{subresource.location}")

    # Sort resources by rails, left to right in reality.
    sorted_resources = sorted(self._resources.values(), key=lambda r: r.location.x)

    # Print table body.
    print_resource(sorted_resources[0])
    for resource in sorted_resources[1:]:
      print("     │")
      print_resource(resource)
