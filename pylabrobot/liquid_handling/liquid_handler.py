""" Defines LiquidHandler class, the coordinator for liquid handling operations. """

from collections.abc import Iterable
import functools
import inspect
import json
import logging
import numbers
import time
import typing
from typing import Tuple, Union, Optional, List, overload

import pylabrobot.utils.file_parsing as file_parser
from pylabrobot.liquid_handling.resources.abstract import Deck
from pylabrobot import utils

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
  CarrierSite,
  Hotel,
  Lid,
  Plate,
  Tip,
  Tips,
  Well
)
from .standard import Aspiration, Dispense

logger = logging.getLogger(__name__) # TODO: get from somewhere else?


_RAILS_WIDTH = 22.5 # space between rails (mm)


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
    self.setup_finished = False
    self._picked_up_tips = None
    self._picked_up_tips96 = None

    self.deck = Deck(
      resource_assigned_callback=self.resource_assigned_callback,
      resource_unassigned_callback=self.resource_unassigned_callback,
      origin=Coordinate(0, 63, 100)
    )

  def __del__(self):
    # If setup was finished, close automatically to prevent blocking the USB device.
    if self.setup_finished:
      self.stop()

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
    self.setup_finished = True

  def stop(self):
    self.backend.stop()
    self.setup_finished = False

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

    # TODO: most things here should be handled by Deck.

    if (rails is not None) == (location is not None):
      raise ValueError("Rails or location must be None.")

    if rails is not None and not 1 <= rails <= 30:
      raise ValueError("Rails must be between 1 and 30.")

    # Check if resource exists.
    if self.deck.has_resource(resource.name):
      if replace:
        # unassign first, so we don't have problems with location checking later.
        self.unassign_resource(resource.name)
      else:
        raise ValueError(f"Resource with name '{resource.name}' already defined.")

    # Set resource location.
    if rails is not None:
      resource.location = Coordinate(x=LiquidHandler._x_coordinate_for_rails(rails), y=0, z=0)
    else:
      resource.location = location

    if resource.location.x + resource.get_size_x() > LiquidHandler._x_coordinate_for_rails(30) and \
      rails is not None:
      raise ValueError(f"Resource with width {resource.get_size_x()} does not "
                       f"fit at rails {rails}.")

    # Check if there is space for this new resource.
    for og_resource in self.deck.get_resources():
      og_x = og_resource.get_absolute_location().x
      og_y = og_resource.get_absolute_location().y

      # hack parent to get the absolute location.
      resource.parent = self.deck

      # A resource is not allowed to overlap with another resource. Resources overlap when a corner
      # of one resource is inside the boundaries other resource.
      if (og_x <= resource.get_absolute_location().x < og_x + og_resource.get_size_x() or \
         og_x <= resource.get_absolute_location().x + resource.get_size_x() <
           og_x + og_resource.get_size_x()) and\
          (og_y <= resource.get_absolute_location().y < og_y + og_resource.get_size_y() or \
            og_y <= resource.get_absolute_location().y + resource.get_size_y() <
               og_y + og_resource.get_size_y()):
        resource.location = None # Revert location.
        resource.parent = None # Revert parent.
        if rails is not None:
          if not (replace and resource.name == og_resource.name):
            raise ValueError(f"Rails {rails} is already occupied by resource '{og_resource.name}'.")
        else:
          raise ValueError(f"Location {location} is already occupied by resource "
                           f"'{og_resource.name}'.")

    self.deck.assign_child_resource(resource)

  def resource_assigned_callback(self, resource: Resource):
    self.backend.assigned_resource_callback(resource)

  def resource_unassigned_callback(self, resource: Resource):
    self.backend.unassigned_resource_callback(resource.name)

  def unassign_resource(self, resource: typing.Union[str, Resource]):
    """ Unassign an assigned resource.

    Args:
      resource: The resource to unassign.

    Raises:
      KeyError: If the resource is not currently assigned to this liquid handler.
    """

    if isinstance(resource, Resource):
      resource = resource.name

    r = self.deck.get_resource(resource)
    if r is None:
      raise KeyError(f"Resource '{resource}' is not assigned to this liquid handler.")
    r.unassign()

  def get_resource(self, name: str) -> typing.Optional[Resource]:
    """ Find a resource on the deck of this liquid handler. Also see :meth:`~Deck.get_resource`.

    Args:
      name: name of the resource.

    Returns:
      The resource with the given name, or None if not found.
    """

    return self.deck.get_resource(name)

  def summary(self):
    """ Prints a string summary of the deck layout.

    Example:
      Printing a summary of the deck layout:

      >>> lh.summary()
      Rail     Resource                   Type                Coordinates (mm)
      ==============================================================================================
      (1) ├── tip_car                    TIP_CAR_480_A00     (x: 100.000, y: 240.800, z: 164.450)
          │   ├── tips_01                STF_L               (x: 117.900, y: 240.000, z: 100.000)
    """

    if len(self.deck.get_resources()) == 0:
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
      # TODO: print something else if resource is not assigned to a rails.
      rails = LiquidHandler._rails_for_x_coordinate(resource.location.x)
      rail_label = utils.pad_string(f"({rails})", 4)
      print(f"{rail_label} ├── {utils.pad_string(resource.name, 27)}"
            f"{utils.pad_string(resource.__class__.__name__, 20)}"
            f"{resource.get_absolute_location()}")

      if isinstance(resource, Carrier):
        for site in resource.get_sites():
          if site.resource is None:
            print("     │   ├── <empty>")
          else:
            subresource = site.resource
            if isinstance(subresource, (Tips, Plate)):
              location = subresource.get_item("A1").get_absolute_location()
            else:
              location = subresource.get_absolute_location()
            print(f"     │   ├── {utils.pad_string(subresource.name, 27-4)}"
                  f"{utils.pad_string(subresource.__class__.__name__, 20)}"
                  f"{location}")

    # Sort resources by rails, left to right in reality.
    sorted_resources = sorted(self.deck.children, key=lambda r: r.get_absolute_location().x)

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
      self.assign_resource(cont, location=cont.location - Coordinate(0, 63.0, 100)) # TODO(63) fix

  def save(self, fn: str, indent: typing.Optional[int] = None):
    """ Save a deck layout to a JSON file.

    Args:
      fn: File name. Caution: file will be overwritten.
      indent: Same as `json.dump`'s `indent` argument (for json pretty printing).
    """

    serialized = self.deck.serialize()

    serialized = dict(deck=serialized)

    with open(fn, "w", encoding="utf-8") as f:
      json.dump(serialized, f, indent=indent)

  def load_from_json(self, fn: Optional[str] = None, content: Optional[dict] = None):
    """ Load deck layout serialized in JSON. Contents can either be in a layout file or in a
    dictionary.

    Args:
      fn: File name.
      content: Dictionary containing serialized deck layout.
    """

    assert (fn is not None) != (content is not None), "Either fn or content must be provided."

    if content is None:
      with open(fn, "r", encoding="utf-8") as f:
        content = json.load(f)

    # Get class names of all defined resources.
    resource_classes = [c[0] for c in inspect.getmembers(resources)]

    def deserialize_resource(dict_resource):
      """ Deserialize a single resource. """

      # Get class name.
      class_name = dict_resource["type"]
      if class_name in resource_classes:
        klass = getattr(resources, class_name)
        resource = klass.deserialize(dict_resource)
        for child_dict in dict_resource["children"]:
          child_resource = deserialize_resource(child_dict)
          resource.assign_child_resource(child_resource)
        return resource
      else:
        raise ValueError(f"Resource with classname {class_name} not found.")

    deck_dict = content["deck"]
    self.deck = deserialize_resource(deck_dict)
    self.deck.resource_assigned_callback_callback = self.resource_assigned_callback
    self.deck.resource_unassigned_callback_callback = self.resource_unassigned_callback

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

  def _channels_to_standard_tip_form(
    self,
    *channels: Union[Optional[Tip], List[Optional[Tip]]]
  ) -> List[Optional[Tip]]:
    """ Converts channel parameters to standard tip form.

    This will flatten the list of channels into a single list of tips.
    """

    tips = []
    for channel in channels:
      if channel is None:
        tips.append(None)
      elif isinstance(channel, Tip):
        tips.append(channel)
      else:
        tips.extend(channel)
    return tips

  @need_setup_finished
  def pickup_tips(
    self,
    *channels: Union[Tip, List[Tip]],
    **backend_kwargs
  ):
    """ Pick up tips from a resource.

    Exampels:
      Pick up all tips in the first column.

      >>> lh.pickup_tips(tips_resource["A1":"H1"])

      Pick up tips on odd numbered rows.

      >>> lh.pickup_tips(channels=[
      ...   "A1",
      ...   None,
      ...   "C1",
      ...   None,
      ...   "E1",
      ...   None,
      ...   "G1",
      ...   None,
      ... ])

      Pick up tips from the diagonal:

      >>> lh.pickup_tips(tips_resource["A1":"H8"])

      Pick up tips from different tip resources:

      >>> lh.pickup_tips(tips_resource1["A1"], tips_resource2["B2"], tips_resource3["C3"])

    Args:
      channels: Channel parameters. Each channel can be a :class:`Tip` object, a list of
        :class:`Tip` objects. This list will be flattened automatically. Use `None` to indicate
        that no tips should be picked up by this channel.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If no channel will pick up a tip, in other words, if all channels are `None` or
        if the list of channels is empty.

      ValueError: If the positions are not unique.
    """

    channels = self._channels_to_standard_tip_form(*channels)
    if not any(channel is not None for channel in channels):
      raise ValueError("Must specify at least one channel to pick up tips with.")
    self.backend.pickup_tips(*channels, **backend_kwargs)

    # Save the tips that are currently picked up.
    self._picked_up_tips = channels

  @need_setup_finished
  def discard_tips(
    self,
    *channels: Union[Tip, List[Tip]],
    **backend_kwargs
  ):
    """ Discard tips to a resource.

    Args:
      channels: Channel parameters. Each channel can be a :class:`Tip` object, a list of
        :class:`Tip` objects. This list will be flattened automatically. Use `None` to indicate
        that no tips should be discarded up by this channel.
      kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If no channel will pick up a tip, in other words, if all channels are `None` or
        if the list of channels is empty.

      ValueError: If the positions are not unique.
    """

    channels = self._channels_to_standard_tip_form(*channels)
    if not any(channel is not None for channel in channels):
      raise ValueError("Must specify at least one channel to discard tips from.")
    self.backend.discard_tips(*channels, **backend_kwargs)

    self._picked_up_tips = None

  def return_tips(self):
    """ Return all tips that are currently picked up to their original place.

    Examples:
      Return the tips on the head to the tip rack where they were picked up:

      >>> lh.pickup_tips("plate_01")
      >>> lh.return_tips()

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    if self._picked_up_tips is None:
      raise RuntimeError("No tips are currently picked up.")

    self.discard_tips(*self._picked_up_tips)

  @need_setup_finished
  def aspirate(
    self,
    channels: Iterable[Well],
    vols: Union[Iterable[float], numbers.Rational],
    end_delay: float = 0,
    **backend_kwargs
  ):
    """Aspirate liquid from the specified wells.

    Examples:
      Aspirate a constant amount of liquid from the first column:

      >>> lh.aspirate(plate["A1":"H8"], 50)

      Aspirate an linearly increasing amount of liquid from the first column:

      >>> lh.aspirate(plate["A1":"H8"], range(0, 500, 50))

      Aspirate a arbitrary amounts of liquid from the first column:

      >>> lh.aspirate(plate["A1":"H8"], [0, 40, 10, 50, 100, 200, 300, 400])

      Aspirate liquid from wells in different plates:

      >>> lh.aspirate(plate["A1"] + plate2["A1"] + plate3["A1"], 50)

    Args:
      channels: A list of channels to aspirate liquid from. Use `None` to skip a channel.
      vols: A list of volumes to aspirate, one for each channel. Note that the `None` values must
        be in the same position in both lists. If `vols` is a single number, then all channels
        will aspirate that volume.
      end_delay: The delay after the last aspiration in seconds, optional. This is useful for when
        the tips used in the aspiration are dripping.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the aspiration info is invalid, in other words, when all channels are `None`.

      ValueError: If all channels are `None`.
    """

    if len(channels) == 0:
      raise ValueError("No channels specified")

    if isinstance(vols, numbers.Rational):
      vols = [vols] * len(channels)
    channels = [(Aspiration(c, v) if c is not None else None) for c, v in zip(channels, vols)]

    self.backend.aspirate(*channels, **backend_kwargs)

    if end_delay > 0:
      time.sleep(end_delay)

  @overload
  def dispense(
    self,
    *channels: Union[Well, List[Well]],
    vols: List[float], **kwargs) -> None: ...

  @overload
  def dispense(
    self,
    *channels: Union[Tuple[Well, float], Tuple[List[Well], List[float]]],
    **kwargs) -> None: ...

  @need_setup_finished
  def dispense(
    self,
    channels: List[Well],
    vols: List[float] = None,
    end_delay: float = 0,
    **backend_kwargs
  ):
    """ Dispense liquid to the specified channels.

    Examples:
      Dispense a constant amount of liquid to the first column:

      >>> lh.dispense(plate["A1":"H8"], 50)

      Dispense an linearly increasing amount of liquid to the first column:

      >>> lh.dispense(plate["A1":"H8"], range(0, 500, 50))

      Dispense a arbitrary amounts of liquid to the first column:

      >>> lh.dispense(plate["A1":"H8"], [0, 40, 10, 50, 100, 200, 300, 400])

      Dispense liquid to wells in different plates:

      >>> lh.dispense((plate["A1"], 50), (plate2["A1"], 50), (plate3["A1"], 50))

    Args:
      channels: A list of channels to dispense liquid to. If channels is a well or a list of
        wells, then vols must be a list of volumes, otherwise vols must be None. If channels is a
        list of tuples, they must be of length 2, and the first element must be a well or a list of
        wells, and the second element must be a volume or a list of volumes. When a single volume is
        passed with a list of wells, it is used for all wells in the list.
      end_delay: The delay after the last dispense in seconds, optional. This is useful for when
        the tips used in the dispense are dripping.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the dispense info is invalid, in other words, when all channels are `None`.

      ValueError: If all channels are `None`.
    """

    # channels = self._channels_to_standard_form(*channels, vols=vols)
    if isinstance(vols, numbers.Rational):
      vols = [vols] * len(channels)
    channels = [(Dispense(c, v) if c is not None else None) for c, v in zip(channels, vols)]

    self.backend.dispense(*channels, **backend_kwargs)

    if end_delay > 0:
      time.sleep(end_delay)

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

    if isinstance(resource, str):
      resource = self.get_resource(resource)

    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    self.backend.pickup_tips96(resource, **backend_kwargs)

    # Save the tips as picked up.
    self._picked_up_tips96 = resource

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
    if isinstance(resource, str):
      resource = self.get_resource(resource)

    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    self.backend.discard_tips96(resource, **backend_kwargs)

    self._picked_up_tips96 = None

  def return_tips96(self):
    """ Return the tips on the 96 head to the tip rack where they were picked up.]

    Examples:
      Return the tips on the 96 head to the tip rack where they were picked up:

      >>> lh.pickup_tips96("plate_01")
      >>> lh.return_tips96()

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    if self._picked_up_tips96 is None:
      raise RuntimeError("No tips picked up.")

    self.discard_tips96(self._picked_up_tips96)

  def aspirate96(
    self,
    resource: typing.Union[str, Resource],
    volume: float,
    pattern: Optional[Union[List[List[bool]], str]] = None,
    end_delay: float = 0,
    liquid_class: LiquidClass = StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
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
        represent columns, or a string representing a range of positions. Default all.
      volume: The volume to aspirate from each well.
      end_delay: The delay after the last aspiration in seconds, optional. This is useful for when
        the tips used in the aspiration are dripping.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Resource):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    # Convert the pattern to a list of lists of booleans
    if pattern is None:
      pattern = [[True]*12]*8
    elif isinstance(pattern, str):
      pattern = utils.string_to_pattern(pattern)

    utils.assert_shape(pattern, (8, 12))

    self.backend.aspirate96(resource, pattern, volume, liquid_class=liquid_class, **backend_kwargs)

    if end_delay > 0:
      time.sleep(end_delay)

  def dispense96(
    self,
    resource: Union[str, Resource],
    volume: float,
    pattern: Optional[Union[List[List[bool]], str]] = None,
    liquid_class: LiquidClass = StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    end_delay: float = 0,
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
        represent columns, or a string representing a range of positions. Default all.
      volume: The volume to dispense to each well.
      end_delay: The delay after the last dispense in seconds, optional. This is useful for when
        the tips used in the dispense are dripping.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Resource):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    # Convert the pattern to a list of lists of booleans
    if pattern is None:
      pattern = [[True]*12]*8
    elif isinstance(pattern, str):
      pattern = utils.string_to_pattern(pattern)

    utils.assert_shape(pattern, (8, 12))

    self.backend.dispense96(resource, pattern, volume, liquid_class, **backend_kwargs)

    if end_delay > 0:
      time.sleep(end_delay)

  def move_plate(
    self,
    plate: typing.Union[Plate, CarrierSite],
    target: typing.Union[Resource, Coordinate],
    **backend_kwargs
  ):
    """ Move a plate to a new location.

    Examples:
      Move a plate to a new location within the same carrier:

      >>> lh.move_plate(plt_car[0], plt_car[1])

      Move a plate to a new location within a different carrier:

      >>> lh.move_plate(plt_car[0], plt_car2[0])

      Move a plate to an absolute location:

      >>> lh.move_plate(plate_01, Coordinate(100, 100, 100))

    Args:
      plate: The plate to move. Can be either a Plate object or a CarrierSite object.
      target: The location to move the plate to, either a CarrierSite object or a Coordinate.
    """

    # Get plate from `plate` param. # (this could be a `Resource` too)
    if isinstance(plate, CarrierSite):
      if plate.resource is None:
        raise ValueError(f"No resource found at CarrierSite '{plate}'.")
      plate = plate.resource
    elif isinstance(plate, str):
      plate = self.get_resource(plate)
      if not plate:
        raise ValueError(f"Resource with name '{plate}' not found.")

    if isinstance(target, CarrierSite):
      if target.resource is not None:
        raise ValueError(f"There already exists a resource at {target}.")

    # Try to move the physical plate first.
    self.backend.move_plate(plate, target, **backend_kwargs)

    # Move the resource in the layout manager.
    plate.unassign()
    if isinstance(target, Resource):
      target.assign_child_resource(plate)
    elif isinstance(target, Coordinate):
      plate.location = target
      self.deck.assign_child_resource(plate) # Assign "free" objects directly to the deck.
    else:
      raise TypeError(f"Invalid location type: {type(target)}")

  def move_lid(
    self,
    lid: Lid,
    target: typing.Union[Plate, Hotel, CarrierSite],
    **backend_kwargs
  ):
    """ Move a lid to a new location.

    Examples:
      Move a lid to the :class:`~resources.Hotel`:

      >>> lh.move_lid(plate.lid, hotel)

    Args:
      lid: The lid to move. Can be either a Plate object or a Lid object.
      to: The location to move the lid to, either a Resource object or a Coordinate.

    Raises:
      ValueError: If the lid is not assigned to a resource.
    """

    if isinstance(target, CarrierSite):
      if target.resource is None:
        raise ValueError(f"No plate exists at {target}.")

    self.backend.move_lid(lid, target, **backend_kwargs)

    # Move the resource in the layout manager.
    lid.unassign()
    if isinstance(target, Resource):
      target.assign_child_resource(lid)
    elif isinstance(target, Coordinate):
      lid.location = target
      self.deck.assign_child_resource(lid) # Assign "free" objects directly to the deck.
    else:
      raise TypeError(f"Invalid location type: {type(target)}")
