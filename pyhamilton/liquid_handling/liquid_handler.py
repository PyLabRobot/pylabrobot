import copy
import functools
import inspect
import json
import logging
import typing

import pyhamilton.utils.file_parsing as file_parser
from pyhamilton import utils

from .backends import LiquidHandlerBackend
from . import resources
from .liquid_classes import (
  LiquidClass,
  StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol
)
from .resources import (
  Resource,
  Coordinate,
  Carrier,
  Plate,
  Tips,
  TipType
)
# from .liquid_classes import LiquidClass

logger = logging.getLogger(__name__) # TODO: get from somewhere else?


_RAILS_WIDTH = 22.5 # space between rails (mm)


class AspirationInfo:
  """ AspirationInfo is a class that contains information about an aspiration.

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate` to store information
  about the aspiration for each individual channel.

  Examples:
    Directly initialize the class:

    >>> aspiration_info = AspirationInfo('A1', 50)
    >>> aspiration_info.position
    'A1'
    >>> aspiration_info.volume
    50

    Instantiate an aspiration info object from a tuple:

    >>> AspirationInfo.from_tuple(('A1', 50))
    AspirationInfo(position='A1', volume=50)

    Instantiate an aspiration info object from a dict:

    >>> AspirationInfo.from_dict({'position': 'A1', 'volume': 50})
    AspirationInfo(position='A1', volume=50)

    Get the corrected volume, using the default liquid class
    (:class:`pyhamilton.liquid_handling.liquid_classes.StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`):

    >>> aspiration_info = AspirationInfo('A1', 100)
    >>> aspiration_info.get_corrected_volume()
    107.2
  """

  def __init__(
    self,
    position: str,
    volume: float,
    liquid_class: LiquidClass = StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol
  ):
    """ Initialize the aspiration info.

    Args:
      position: The position of the aspiration. Positions are formatted as `<row><column>` where
        `<row>` is the row string (`A` for row 1, `B` for row 2, etc.) and `<column>` is the column
        number. For example, `A1` is the top left corner of the resource and `H12` is the bottom
        right.
      volume: The volume of the aspiration.
      liquid_class: The liquid class of the aspiration.
    """

    self.position = position
    self.volume = volume
    self.liquid_class = liquid_class

  @classmethod
  def from_tuple(cls, tuple_):
    """ Create aspiration info from a tuple.

    The tuple should either be in the form (position, volume) or (position, volume, liquid_class).
    In the former case, the liquid class will be set to
    `StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`. (TODO: link to liquid class
    in docs)

    Args:
      tuple: A tuple in the form (position, volume) or (position, volume, liquid_class)

    Returns:
      AspirationInfo object.

    Raises:
      ValueError if the tuple is not in the correct format.
    """

    if len(tuple_) == 2:
      position, volume = tuple_
      return cls(position, volume)
    elif len(tuple_) == 3:
      position, volume, liquid_class = tuple_
      return cls(position, volume, liquid_class)
    else:
      raise ValueError("Invalid tuple length")

  @classmethod
  def from_dict(cls, dict_):
    """ Create aspiration info from a dictionary.

    The dictionary should either be in the form {"position": position, "volume": volume} or
    {"position": position, "volume": volume, "liquid_class": liquid_class}. In the former case,
    the liquid class will be set to
    `StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`.

    Args:
      dict: A dictionary in the form {"position": position, "volume": volume} or
        {"position": position, "volume": volume, "liquid_class": liquid_class}

    Returns:
      AspirationInfo object.

    Raises:
      ValueError: If the dictionary is invalid.
    """

    if "position" in dict_ and "volume" in dict_:
      position = dict_["position"]
      volume = dict_["volume"]
      return cls(
        position=position,
        volume=volume,
        liquid_class=dict_.get("liquid_class",
          StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol))

    raise ValueError("Invalid dictionary")

  def __repr__(self):
    return f"AspirationInfo(position={self.position}, volume={self.volume})"

  def get_corrected_volume(self):
    """ Get the corrected volume.

    The corrected volume is computed based on various properties of a liquid, as defined by the
    :class:`pyhamilton.liquid_handling.liquid_classes.LiquidClass` object.

    Returns:
      The corrected volume.
    """

    return self.liquid_class.compute_corrected_volume(self.volume)

  def serialize(self):
    """ Serialize the aspiration info.

    Returns:
      A dictionary containing the serialized dispense info.
    """

    return {
      "position": self.position,
      "volume": self.volume,
      "liquid_class": self.liquid_class.__class__.__name__
    }


class DispenseInfo:
  """ DispenseInfo is a class that contains information about an dispense.

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate` to store information
  about the dispense for each individual channel.

  Examples:
    Directly initialize the class:

    >>> dispense_info = DispenseInfo('A1', 0.5)
    >>> dispense_info.position
    'A1'
    >>> dispense_info.volume
    0.5

    Instantiate an dispense info object from a tuple:

    >>> DispenseInfo.from_tuple(('A1', 0.5))
    DispenseInfo(position='A1', volume=0.5)

    Instantiate an dispense info object from a dict:

    >>> DispenseInfo.from_dict({'position': 'A1', 'volume': 0.5})
    DispenseInfo(position='A1', volume=0.5)

    Get the corrected volume:

    >>> dispense_info = DispenseInfo('A1', 100)
    >>> dispense_info.get_corrected_volume()
    107.2
  """

  def __init__(
    self,
    position: str,
    volume: float,
    liquid_class: LiquidClass = StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol
  ):
    """ Initialize the dispense info.

    Args:
      position: The position of the dispense. Positions are formatted as `<row><column>` where
        `<row>` is the row string (`A` for row 1, `B` for row 2, etc.) and `<column>` is the column
        number. For example, `A1` is the top left corner of the resource and `H12` is the bottom
        right.
      volume: The volume of the dispense.
      liquid_class: The liquid class of the dispense.
    """

    self.position = position
    self.volume = volume
    self.liquid_class = liquid_class

  @classmethod
  def from_tuple(cls, tuple_):
    """ Create dispense info from a tuple.

    The tuple should either be in the form (position, volume) or (position, volume, liquid_class).
    In the former case, the liquid class will be set to
    `StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`. (TODO: link to liquid class
    in docs)

    Args:
      tuple: A tuple in the form (position, volume) or (position, volume, liquid_class)

    Returns:
      DispenseInfo object.

    Raises:
      ValueError if the tuple is not in the correct format.
    """

    if len(tuple_) == 2:
      position, volume = tuple_
      return cls(position, volume)
    elif len(tuple_) == 3:
      position, volume, liquid_class = tuple_
      return cls(position, volume, liquid_class)
    else:
      raise ValueError("Invalid tuple length")

  @classmethod
  def from_dict(cls, dict):
    """ Create dispense info from a dictionary.

    The dictionary should either be in the form {"position": position, "volume": volume} or
    {"position": position, "volume": volume, "liquid_class": liquid_class}. In the former case,
    the liquid class will be set to
    `StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`.

    Args:
      dict: A dictionary in the form {"position": position, "volume": volume} or
        {"position": position, "volume": volume, "liquid_class": liquid_class}

    Returns:
      DispenseInfo object.

    Raises:
      ValueError: If the dictionary is invalid.
    """

    if "position" in dict and "volume" in dict:
      position = dict["position"]
      volume = dict["volume"]
      return cls(
        position=position,
        volume=volume,
        liquid_class=dict.get("liquid_class",
          StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol))

    raise ValueError("Invalid dictionary")

  def __repr__(self):
    return f"DispenseInfo(position={self.position}, volume={self.volume})"

  def get_corrected_volume(self):
    """ Get the corrected volume.

    The corrected volume is computed based on various properties of a liquid, as defined by the
    :class:`pyhamilton.liquid_handling.liquid_classes.LiquidClass` object.

    Returns:
      The corrected volume.
    """

    return self.liquid_class.compute_corrected_volume(self.volume)

  def serialize(self):
    """ Serialize the dispense info.

    Returns:
      A dictionary containing the serialized dispense info.
    """

    return {
      "position": self.position,
      "volume": self.volume,
      "liquid_class": self.liquid_class.__class__.__name__
    }


class LiquidHandler:
  """
  Front end for liquid handlers.

  This class is the front end for liquid handlers; it provides a high-level interface for
  interacting with liquid handlers. In the background, this class uses the low-level backend (
  defined in `pyhamilton.liquid_handling.backends`) to communicate with the liquid handler.

  This class is responsible for:
    - Parsing and validating the layout.
    - Performing liquid handling operations. This includes:
      - Aspirating from / dispensing liquid to a location.
      - Transporting liquid from one location to another.
      - Picking up tips from and dropping tips into a tip box.
    - Serializing and deserializing the liquid handler deck. Decks are serialized as JSON and can
      be loaded from a JSON or .lay (legacy) file.
    - Static analysis of commands. This includes checking the presence of tips on the head, keeping
      track of the number of tips in the tip box, and checking the volume of liquid in the liquid
      handler.

  Attributes:
    setup_finished: Whether the liquid handler has been setup.
  """

  def __init__(self, backend: LiquidHandlerBackend):
    """ Initialize a LiquidHandler.

    Args:
      backend: Backend to use.
    """

    self.backend = backend
    self._resources = {}
    self.setup_finished = False

  def need_setup_finished(func: typing.Callable): # pylint: disable=no-self-argument
    """ Decorator for methods that require the liquid handler to be set up.

    Checked by verifying `self.setup_finished` is `True`.

    Raises:
      RuntimeError: If the liquid handler is not set up.
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
      if not self.setup_finished:
        raise RuntimeError("The setup has not finished. See `LiquidHandler.setup`.")
      func(self, *args, **kwargs) # pylint: disable=not-callable
    return wrapper

  def setup(self):
    """ Prepare the robot for use. """

    if self.setup_finished:
      raise RuntimeError("The setup has already finished. See `LiquidHandler.stop`.")

    self.backend.setup()

    for resource in self._resources.values():
      self.backend.assigned_resource_callback(resource)

    self.setup_finished = True

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
    rails: typing.Optional[int] = None, # board location, 1..52
    location: typing.Optional[Coordinate] = None,
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
             max 54 for STAR.) Either rails or location must be None, but not both.
      location: The location of the resource relative to the liquid handler. Either rails or
                location must be None, but not both.
      replace: Replace the resource with the same name that was previously assigned, if it exists.
               If a resource is assigned with the same name and replace is False, a ValueError
               will be raised.

    Raises:
      ValueError: If a resource is assigned with the same name and replace is `False`.
    """

    if (rails is not None) == (location is not None):
      raise ValueError("Rails or location must be None.")

    if rails is not None and not 1 <= rails <= 30:
      raise ValueError("Rails must be between 1 and 30.")

    # Check if resource exists.
    if resource.name in self._resources:
      if replace:
        # unassign first, so we don't have problems with location checking later.
        self.unassign_resource(resource.name)
      else:
        raise ValueError(f"Resource with name '{resource.name}' already defined.")

    # Set resource location.
    if rails is not None:
      resource.location = Coordinate(x=LiquidHandler._x_coordinate_for_rails(rails), y=63, z=100)
    else:
      resource.location = location

    if resource.location.x + resource.size_x > LiquidHandler._x_coordinate_for_rails(30):
      raise ValueError(f"Resource with width {resource.size_x} does not fit at rails {rails}.")

    # Check if there is space for this new resource.
    for og_resource in self._resources.values():
      og_x = og_resource.location.x

      # No space if start or end (=x+width) between start and end of current ("og") resource.
      if og_x <= resource.location.x < og_x + og_resource.size_x or \
         og_x <= resource.location.x + resource.size_x < og_x + og_resource.size_x:
        resource.location = None # Revert location.
        raise ValueError(f"Rails {rails} is already occupied by resource '{og_resource.name}'.")

    # If the resource is a Carrier, add callbacks to self.
    if isinstance(resource, Carrier):
      resource.set_check_can_assign_resource_callback(
        self._check_subresource_can_be_assigned_callback())
      resource_assigned_callback = self._subresource_assigned_callback(resource)
      resource.set_resource_assigned_callback(resource_assigned_callback)
      resource_unassigned_callback = self._subresource_assigned_callback(resource)
      resource.set_resource_unassigned_callback(resource_unassigned_callback)

    self._resources[resource.name] = resource

    # Only call the backend if the setup is finished.
    if self.setup_finished:
      self.backend.assigned_resource_callback(resource)

  def _check_subresource_can_be_assigned_callback(self) -> typing.Optional[str]:
    """ Returns the error message for the error that would occur if this resource would be assigned,
    if any. """

    def callback(subresource: Resource):
      if self.get_resource(subresource.name) is not None:
        return f"A resource with name '{subresource.name}' already assigned."
      return None
    return callback

  def _subresource_assigned_callback(self, resource: Resource):
    """
    Returns a callback that can be used to call the `unassinged_resource_callback` and
    `assigned_resource_callback` of the backend.

    Raises a `ValueError` if a resource with the same name is already assigned.
    """

    def callback(subresource: Resource):
      if self.get_resource(resource.name) is not None:
        # If the resource was already assigned, do a reassign in callbacks. Get resource from self
        # to update location.
        resource_ = self.get_resource(resource.name)
        self.backend.unassigned_resource_callback(resource_.name)
        self.backend.assigned_resource_callback(resource_)
    return callback

  def unassign_resource(self, resource: typing.Union[str, Resource]):
    """ Unassign an assigned resource.

    Args:
      resource: The resource to unassign.

    Raises:
      KeyError: If the resource is not currently assigned to this liquid handler.
    """

    if isinstance(resource, Resource):
      resource = resource.name
    del self._resources[resource]
    self.backend.unassigned_resource_callback(resource)

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
        if resource.has_resource(name):
          subresource = copy.deepcopy(resource.get_resource_by_name(name))
          subresource.location += resource.location
          # TODO: Why do we need `+ Coordinate(0, resource.location.y, 0)`??? (=63)
          subresource.location += Coordinate(0, resource.location.y, 0)
          return subresource

    return None

  def summary(self):
    """ Prints a string summary of the deck layout.

    Example:
      Printing a summary of the deck layout:

      >>> lh.summary()
      Rail     Resource                   Type                Coordinates (mm)
      ===============================================================================================
      (1) ├── tip_car                    TIP_CAR_480_A00     (x: 100.000, y: 240.800, z: 164.450)
          │   ├── tips_01                STF_L               (x: 117.900, y: 240.000, z: 100.000)
    """

    if len(self._resources) == 0:
      raise ValueError(
          "This liquid editor does not have any resources yet. "
          "Build a layout first by calling `assign_resource()`. "
          "See the documentation for details. (TODO: link)"
      )

    # Print header.
    print(utils.pad_string("Rail", 9) + utils.pad_string("Resource", 27) + \
          utils.pad_string("Type", 20) + "Coordinates (mm)")
    print("=" * 95)

    def print_resource(resource):
      rails = LiquidHandler._rails_for_x_coordinate(resource.location.x)
      rail_label = utils.pad_string(f"({rails})", 4)
      print(f"{rail_label} ├── {utils.pad_string(resource.name, 27)}"
            f"{utils.pad_string(resource.__class__.__name__, 20)}"
            f"{resource.location}")

      if isinstance(resource, Carrier):
        for subresource in resource.get_items():
          if subresource is None:
            print("     │   ├── <empty>")
          else:
            # Get subresource using `self.get_resource` to update it with the new location.
            subresource = self.get_resource(subresource.name)
            print(f"     │   ├── {utils.pad_string(subresource.name, 27-4)}"
                  f"{utils.pad_string(subresource.__class__.__name__, 20)}"
                  f"{subresource.location}")

    # Sort resources by rails, left to right in reality.
    sorted_resources = sorted(self._resources.values(), key=lambda r: r.location.x)

    # Print table body.
    print_resource(sorted_resources[0])
    for resource in sorted_resources[1:]:
      print("     │")
      print_resource(resource)

  def load_from_lay_file(self, fn: str):
    """ Parse a .lay file (legacy layout definition) and build the layout on this liquid handler.

    Args:
      fn: Filename of .lay file.
    """

    c = None
    with open(fn, "r", encoding="ISO-8859-1") as f:
      c = f.read()

    # Get class names of all defined resources.
    resource_classes = [c[0] for c in inspect.getmembers(resources)]

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
        klass = getattr(resources, class_name)
        resource = klass(name=name)
      else:
        # TODO: replace with real template.
        # logger.warning(
          # "Resource with classname %s not found. Please file an issue at "
          # "https://github.com/pyhamilton/pyhamilton/issues/new?assignees=&"
          # "labels=&template=bug_report.md&title=Class\%20%s\%20not\%20found", class_name)
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

    # Assign child resources to their parents.
    for child in children.values():
      cont = containers[child["container"]]
      cont[5 - child["site"]] = child["resource"]

    # Assign all resources to self.
    for cont in containers.values():
      self.assign_resource(cont, location=cont.location)

  def save(self, fn: str, indent: typing.Optional[int] = None):
    """ Save a deck layout to a JSON file.

    Args:
      fn: File name. Caution: file will be overwritten.
      indent: Same as `json.dump`'s `indent` argument (for json pretty printing).
    """

    serialized_resources = []

    for resource in self._resources.values():
      serialized_resources.append(resource.serialize())

    deck = dict(resources=serialized_resources)

    with open(fn, "w", encoding="utf-8") as f:
      json.dump(deck, f, indent=indent)

  def load_from_json(self, fn: str):
    """ Load deck layout serialized in a layout file.

    Args:
      fn: File name.
    """

    with open(fn, "r", encoding="utf-8") as f:
      content = json.load(f)
    dict_resources = content["resources"]

    # Get class names of all defined resources.
    resource_classes = [c[0] for c in inspect.getmembers(resources)]

    for resource_dict in dict_resources:
      klass_type = resource_dict["type"]
      location = Coordinate.deserialize(resource_dict.pop("location"))
      if klass_type in resource_classes: # properties pre-defined
        klass = getattr(resources, resource_dict["type"])
        resource = klass(name=resource_dict["name"])
      else: # read properties explicitly
        args = dict(
          name=resource_dict["name"],
          size_x=resource_dict["size_x"],
          size_y=resource_dict["size_y"],
          size_z=resource_dict["size_z"]
        )
        if "type" in resource_dict:
          args["type"] = resource_dict["type"]
        subresource = subresource_klass(**args)

      if "sites" in resource_dict:
        for subresource_dict in resource_dict["sites"]:
          if subresource_dict["site"]["resource"] is None:
            continue
          subtype = subresource_dict["site"]["resource"]["type"]
          if subtype in resource_classes: # properties pre-defined
            subresource_klass = getattr(resources, subtype)
            subresource = subresource_klass(name=subresource_dict["site"]["resource"]["name"])
          else: # Custom resources should deserialize the properties they serialized.
            subresource = subresource_klass(**subresource_dict["site"]["resource"])
          resource[subresource_dict["site_id"]] = subresource

      self.assign_resource(resource, location=location)

  def load(self, fn: str, file_format: typing.Optional[str] = None):
    """ Load deck layout serialized in a file, either from a .lay or .json file.

    Args:
      fn: Filename for serialized model file.
      format: file format (`json` or `lay`). If None, file format will be inferred from file name.
    """

    extension = "." + (file_format or fn.split(".")[-1])
    if extension == ".json":
      self.load_from_json(fn)
    elif extension == ".lay":
      self.load_from_lay_file(fn)
    else:
      raise ValueError(f"Unsupported file extension: {extension}")

  def _assert_positions_unique(self, positions: typing.List[str]):
    """ Returns whether all items in `positions` are unique where they are not `None`.

    Args:
      positions: List of positions.
    """

    not_none = [p for p in positions if p is not None]
    if len(not_none) != len(set(not_none)):
      raise ValueError("Positions must be unique.")

  @need_setup_finished
  def pickup_tips(
    self,
    resource: typing.Union[str, Tips],
    channel_1: typing.Optional[str] = None,
    channel_2: typing.Optional[str] = None,
    channel_3: typing.Optional[str] = None,
    channel_4: typing.Optional[str] = None,
    channel_5: typing.Optional[str] = None,
    channel_6: typing.Optional[str] = None,
    channel_7: typing.Optional[str] = None,
    channel_8: typing.Optional[str] = None,
    **backend_kwargs
  ):
    """ Pick up tips from a resource.

    Exampels:
      Pick up all tips in the first column.
      >>> lh.pickup_tips(tips_resource, "A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1")

      Specifying each channel explicitly:
      >>> lh.pickup_tips(
      ...   tips_resource,
      ...   channel_1="A1",
      ...   channel_2="B1",
      ...   channel_3="C1",
      ...   channel_4="D1",
      ...   channel_5="E1",
      ...   channel_6="F1",
      ...   channel_7="G1",
      ...   channel_8="H1"
      ... )

      Pick up tips from the diagonal:
      >>> lh.pickup_tips(tips_resource, "A1", "B2", "C3", "D4", "E5", "F6", "G7", "H8")

    Args:
      resource: Resource name or resource object.
      channel_1: The location where the tip will be picked up. If None, this channel will not pick
        up a tip.
      channel_2: The location where the tip will be picked up. If None, this channel will not pick
        up a tip.
      channel_3: The location where the tip will be picked up. If None, this channel will not pick
        up a tip.
      channel_4: The location where the tip will be picked up. If None, this channel will not pick
        up a tip.
      channel_5: The location where the tip will be picked up. If None, this channel will not pick
        up a tip.
      channel_6: The location where the tip will be picked up. If None, this channel will not pick
        up a tip.
      channel_7: The location where the tip will be picked up. If None, this channel will not pick
        up a tip.
      channel_8: The location where the tip will be picked up. If None, this channel will not pick
        up a tip.
      kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If no channel will pick up a tip, in other words, if all channels are `None`.

      ValueError: If the positions are not unique.
    """

    positions = [channel_1, channel_2, channel_3, channel_4,
                 channel_5, channel_6, channel_7, channel_8]

    self._assert_positions_unique(positions)
    assert any(position is not None for position in positions), "Must have at least one tip to " + \
                                                                "pick up."

    # Get resource using `get_resource` to adjust location.
    if not isinstance(resource, str):
      if isinstance(resource, Tips):
        resource = resource.name
      else:
        raise ValueError("Resource must be a string or a Tips object.")
    resource = self.get_resource(resource)

    assert resource is not None, "Resource not found."

    self.backend.pickup_tips(
      resource,
      channel_1, channel_2, channel_3, channel_4, channel_5, channel_6, channel_7, channel_8,
      **backend_kwargs
    )

  @need_setup_finished
  def discard_tips(
    self,
    resource: typing.Union[str, Tips],
    channel_1: typing.Optional[str] = None,
    channel_2: typing.Optional[str] = None,
    channel_3: typing.Optional[str] = None,
    channel_4: typing.Optional[str] = None,
    channel_5: typing.Optional[str] = None,
    channel_6: typing.Optional[str] = None,
    channel_7: typing.Optional[str] = None,
    channel_8: typing.Optional[str] = None,
    **backend_kwargs
  ):
    """ Discard tips to a resource.

    Args:
      resource: Resource name or resource object.
      channel_1: The location where the tip will be discarded. If None, this channel will not
        discard a tip.
      channel_2: The location where the tip will be discarded. If None, this channel will not
        discard a tip.
      channel_3: The location where the tip will be discarded. If None, this channel will not
        discard a tip.
      channel_4: The location where the tip will be discarded. If None, this channel will not
        discard a tip.
      channel_5: The location where the tip will be discarded. If None, this channel will not
        discard a tip.
      channel_6: The location where the tip will be discarded. If None, this channel will not
        discard a tip.
      channel_7: The location where the tip will be discarded. If None, this channel will not
        discard a tip.
      channel_8: The location where the tip will be discarded. If None, this channel will not
        discard a tip.
      kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If no channel will pick up a tip, in other words, if all channels are `None`.

      ValueError: If the positions are not unique.
    """

    positions = [channel_1, channel_2, channel_3, channel_4,
                 channel_5, channel_6, channel_7, channel_8]

    self._assert_positions_unique(positions)
    assert any(position is not None for position in positions), "Must have at least one tip to " + \
                                                                "pick up."

    # Get resource using `get_resource` to adjust location.
    if not isinstance(resource, str):
      if isinstance(resource, Tips):
        resource = resource.name
      else:
        raise ValueError("Resource must be a string or a Tips object.")
    resource = self.get_resource(resource)

    assert resource is not None, "Resource not found."

    self.backend.discard_tips(
      resource,
      channel_1, channel_2, channel_3, channel_4, channel_5, channel_6, channel_7, channel_8,
      **backend_kwargs
    )

  @need_setup_finished
  def aspirate(
    self,
    resource: typing.Union[str, Resource],
    channel_1: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_2: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_3: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_4: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_5: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_6: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_7: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_8: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    **backend_kwargs
  ):
    """Aspirate liquid from the specified channels.

    Examples:
      Aspirate liquid from the specified channels using a tuple:

      >>> aspirate("plate_01", ('A1', 50), ('B1', 50))

      Aspirate liquid from the specified channels using a dictionary:

      >>> aspirate("plate_02", {'position': 'A1', 'volume': 50}, {'position': 'B1', 'volume': 50})

      Aspirate liquid from the specified channels using an AspirationInfo object:

      >>> aspiration_info_1 = AspirationInfo('A1', 50)
      >>> aspiration_info_2 = AspirationInfo('B1', 50)
      >>> aspirate("plate_01", aspiration_info_1, aspiration_info_2)

    Args:
      resource: Resource name or resource object.
      channel_1: The aspiration info for channel 1.
      channel_2: The aspiration info for channel 2.
      channel_3: The aspiration info for channel 3.
      channel_4: The aspiration info for channel 4.
      channel_5: The aspiration info for channel 5.
      channel_6: The aspiration info for channel 6.
      channel_7: The aspiration info for channel 7.
      channel_8: The aspiration info for channel 8.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the resource could not be found. See :meth:`~LiquidHandler.assign_resource`.

      ValueError: If the aspiration info is invalid, in other words, when all channels are `None`.

      ValueError: If all channels are `None`.
    """

    channels = [channel_1, channel_2, channel_3, channel_4,
                channel_5, channel_6, channel_7, channel_8]

    # Check that there is at least one channel specified
    if not any(channel is not None for channel in channels):
      raise ValueError("No channels specified")

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Plate):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    # Convert the channels to `AspirationInfo` objects
    channels_dict = {}
    for channel_id, channel in enumerate(channels):
      if channel is None:
        channels_dict[f"channel_{channel_id+1}"] = None
      elif isinstance(channel, tuple):
        channels_dict[f"channel_{channel_id+1}"] = AspirationInfo.from_tuple(channel)
      elif isinstance(channel, dict):
        channels_dict[f"channel_{channel_id+1}"] = AspirationInfo.from_dict(channel)
      elif isinstance(channel, AspirationInfo):
        channels_dict[f"channel_{channel_id+1}"] = channel
      else:
        raise ValueError(f"Invalid channel type for channel {channel_id+1}")

    self.backend.aspirate(resource, **channels_dict, **backend_kwargs)

  @need_setup_finished
  def dispense(
    self,
    resource: typing.Union[str, Resource],
    channel_1: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_2: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_3: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_4: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_5: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_6: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_7: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_8: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    **backend_kwargs
  ):
    """Dispense liquid from the specified channels.

    Examples:
      Dispense liquid from the specified channels using a tuple:

      >>> dispense("plate_01", ('A1', 50), ('B1', 50))

      Dispense liquid from the specified channels using a dictionary:

      >>> dispense("plate_02", {'position': 'A1', 'volume': 50}, {'position': 'B1', 'volume': 50})

      Dispense liquid from the specified channels using an DispenseInfo object:

      >>> dispense_info_1 = DispenseInfo('A1', 50)
      >>> dispense_info_2 = DispenseInfo('B1', 50)
      >>> dispense("plate_01", dispense_info_1, dispense_info_2)

    Args:
      resource: Resource name or resource object.
      channel_1: The dispense info for channel 1.
      channel_2: The dispense info for channel 2.
      channel_3: The dispense info for channel 3.
      channel_4: The dispense info for channel 4.
      channel_5: The dispense info for channel 5.
      channel_6: The dispense info for channel 6.
      channel_7: The dispense info for channel 7.
      channel_8: The dispense info for channel 8.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the resource could not be found. See :meth:`~LiquidHandler.assign_resource`.

      ValueError: If the dispense info is invalid, in other words, when all channels are `None`.

      ValueError: If all channels are `None`.
    """

    channels = [channel_1, channel_2, channel_3, channel_4,
                channel_5, channel_6, channel_7, channel_8]

    # Check that there is at least one channel specified
    if not any(channel is not None for channel in channels):
      raise ValueError("No channels specified")

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Plate):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    # Convert the channels to `DispenseInfo` objects
    channels_dict = {}
    for channel_id, channel in enumerate(channels):
      if channel is None:
        channels_dict[f"channel_{channel_id+1}"] = None
      elif isinstance(channel, tuple):
        channels_dict[f"channel_{channel_id+1}"] = AspirationInfo.from_tuple(channel)
      elif isinstance(channel, dict):
        channels_dict[f"channel_{channel_id+1}"] = AspirationInfo.from_dict(channel)
      elif isinstance(channel, AspirationInfo):
        channels_dict[f"channel_{channel_id+1}"] = channel
      else:
        raise ValueError(f"Invalid channel type for channel {channel_id+1}")

    self.backend.dispense(resource, **channels_dict, **backend_kwargs)

  def pickup_tips96(self, resource: typing.Union[str, Resource], **backend_kwargs):
    """ Pick up tips using the CoRe 96 head. This will pick up 96 tips.

    Examples:
      Pick up tips from an entire 96 tips plate:

      >>> lh.pickup_tips96("plate_01")

      Pick up tips from the left half of a 96 well plate:

      >>> lh.pickup_tips96("plate_01")

    Args:
      resource: Resource name or resource object.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Tips):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    self.backend.pickup_tips96(resource, **backend_kwargs)

  def discard_tips96(self, resource: typing.Union[str, Resource], **backend_kwargs):
    """ Discard tips using the CoRe 96 head. This will discard 96 tips.

    Examples:
      Discard tips to an entire 96 tips plate:

      >>> lh.discard_tips96("plate_01")

    Args:
      resource: Resource name or resource object.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Tips):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    self.backend.discard_tips96(resource, **backend_kwargs)

  def aspirate96(
    self,
    resource: typing.Union[str, Resource],
    pattern: typing.Union[typing.List[typing.List[bool]], str],
    volume: float,
    **backend_kwargs
  ):
    """ Aspirate liquid using the CoR96 head in the locations where pattern is `True`.

    Examples:
      Aspirate an entire 96 well plate:

      >>> lh.aspirate96("plate_01", "A1:H12", volume=50)

      Aspirate an entire 96 well plate:

      >>> lh.aspirate96("plate_01", [[True]*12]*8, volume=50)

      Aspirate from the left half of a 96 well plate:

      >>> lh.aspirate96("plate_01", "A1:H6", volume=50)

      Aspirate from the left half of a 96 well plate:

      >>> lh.aspirate96("plate_01", [[True]*6+[False]*6]*8], volume=50)

    Args:
      resource: Resource name or resource object.
      pattern: Either a list of lists of booleans where inner lists represent rows and outer lists
        represent columns, or a string representing a range of positions.
      volume: The volume to aspirate from each well.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Plate):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    # Convert the pattern to a list of lists of booleans
    if isinstance(pattern, str):
      pattern = utils.string_to_pattern(pattern)

    utils.assert_shape(pattern, (8, 12))

    self.backend.aspirate96(resource, pattern, volume, **backend_kwargs)

  def dispense96(
    self,
    resource: typing.Union[str, Resource],
    pattern: typing.Union[typing.List[typing.List[bool]], str],
    volume: float,
    **backend_kwargs
  ):
    """ Dispense liquid using the CoR96 head in the locations where pattern is `True`.

    Examples:
      Dispense an entire 96 well plate:

      >>> dispense96("plate_01", [[True * 12] * 8], volume=50)

      Dispense an entire 96 well plate:

      >>> dispense96("plate_01", "A1:H12", volume=50)

      Dispense from the left half of a 96 well plate:

      >>> dispense96("plate_01", "A1:H6", volume=50)

      Dispense from the left half of a 96 well plate:

      >>> dispense96("plate_01", [[True]*6+[False]*6]*8], volume=50)

    Args:
      resource: Resource name or resource object.
      pattern: Either a list of lists of booleans where inner lists represent rows and outer lists
        represent columns, or a string representing a range of positions.
      volume: The volume to dispense to each well.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Plate):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    # Convert the pattern to a list of lists of booleans
    if isinstance(pattern, str):
      pattern = utils.string_to_pattern(pattern)

    utils.assert_shape(pattern, (8, 12))

    self.backend.dispense96(resource, pattern, volume, **backend_kwargs)
