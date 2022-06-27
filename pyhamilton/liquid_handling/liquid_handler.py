import copy
import inspect
import json
import logging
import typing
from pyhamilton.liquid_handling.resources.abstract.carrier import TipCarrier
from pyhamilton.liquid_handling.resources.ml_star import tip_types

import pyhamilton.utils.file_parsing as file_parser

# from .backends import LiquidHandlerBackend
from .backends import STAR as LiquidHandlerBackend
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


# TODO: move to util
def _pad_string(item: typing.Union[str, int], desired_length: int, left=False) -> str:
  """ Pad a string or integer with spaces to the desired length.

  Args:
    item: string or integer to pad
    desired_length: length to pad to
    left: pad to the left instead of the right

  Returns:
    padded string
  """

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
  """

  def __init__(self, backend: LiquidHandlerBackend):
    """ Initialize a LiquidHandler.

    Args:
      backend: Backend to use.
    """

    self.backend = backend
    self._resources = {}
    self._tip_types = {}

  def setup(self):
    """ Prepare the robot for use.

    TODO: probably after the layout is defined.
    """

    assert len(self._resources) > 0, "no resources found"

    self.backend.setup()
    initialized = self.backend.request_instrument_initialization_status()
    if not initialized:
      logger.info("Running backend initialization procedure.")

      # initialization procedure
      # TODO: before layout...
      self.backend.pre_initialize_instrument()

      # TODO: after layout..., need tip types
      self.backend.initialize_iswap()

      # Spread PIP channels command = JE ? (Spread PIP channels)

      #C0DIid0201xp08000&yp4050 3782 3514 3246 2978 2710 2442 2175tp2450tz1220te2450tm1&tt04ti0
      self.backend.initialize_pipetting_channels( # spreads channels
        x_positions=[8000],
        # dy = 268
        y_positions=[4050, 3782, 3514, 3246, 2978, 2710, 2442, 2175],
        begin_of_tip_deposit_process=2450,
        end_of_tip_deposit_process=1220,
        z_position_at_end_of_a_command=3600,
        tip_pattern=[1], # [1] * 8
        tip_type="04", # TODO: get from tip types
        discarding_method=0
      )

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

    self._resources[resource.name] = resource

  def unassign_resource(self, name: str):
    """ Unassign an assigned resource.

    Raises:
      KeyError: If the resource is not currently assigned to this liquid handler.
    """

    del self._resources[name]

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
    print(_pad_string("Rail", 9) + _pad_string("Resource", 27) + \
          _pad_string("Type", 20) + "Coordinates (mm)")
    print("=" * 95)

    def print_resource(resource):
      rails = LiquidHandler._rails_for_x_coordinate(resource.location.x)
      rail_label = _pad_string(f"({rails})", 4)
      print(f"{rail_label} ├── {_pad_string(resource.name, 27)}"
            f"{_pad_string(resource.__class__.__name__, 20)}"
            f"{resource.location}")

      if isinstance(resource, Carrier):
        for subresource in resource.get_items():
          if subresource is None:
            print("     │   ├── <empty>")
          else:
            # Get subresource using `self.get_resource` to update it with the new location.
            subresource = self.get_resource(subresource.name)
            print(f"     │   ├── {_pad_string(subresource.name, 27-4)}"
                  f"{_pad_string(subresource.__class__.__name__, 20)}"
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
      print(resource.serialize())
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
          if subresource_dict["resource"] is None:
            continue
          subtype = subresource_dict["resource"]["type"]
          if subtype in resource_classes: # properties pre-defined
            subresource_klass = getattr(resources, subtype)
            subresource = subresource_klass(name=subresource_dict["resource"]["name"])
          else: # read properties explicitly
            subresource = subresource_klass(
              name=subresource_dict["resource"]["name"],
              size_x=subresource_dict["resource"]["size_x"],
              size_y=subresource_dict["resource"]["size_y"],
              size_z=subresource_dict["resource"]["size_z"]
            )
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

  def define_tip_type(self, tip_type: TipType):
    """ Define a new tip type.

    Sends a command to the robot to define a new tip type and save the tip type table index for
    future reference.

    Args:
      tip_type: Tip type name.

    Returns:
      Tip type table index.

    Raises:
      ValueError: If the tip type is already defined.
    """

    if tip_type in self._tip_types:
      raise ValueError(f"Tip type {tip_type} already defined.")

    ttti = len(self._tip_types) + 1
    if ttti > 99:
      raise ValueError("Too many tip types defined.")

    # TODO: look up if there are other tip types with the same properties, and use that ID.
    self.backend.define_tip_needle(
      tip_type_table_index=ttti,
      filter=tip_type.has_filter,
      tip_length=tip_type.tip_length * 10, # in 0.1mm
      maximum_tip_volume=tip_type.tip_length * 10, # in 0.1ul
      tip_type=tip_type.tip_type_id,
      pick_up_method=tip_type.pick_up_method
    )
    self._tip_types[tip_type] = ttti
    return ttti

  def get_tip_type_table_index(self, tip_type: TipType) -> int:
    """ Get tip type table index.

    Args:
      tip_type: Tip type.

    Returns:
      Tip type ID.
    """

    return self._tip_types[tip_type]

  def get_or_assign_tip_type_index(self, tip_type: TipType) -> int:
    """ Get a tip type table index for the tip_type if it is defined, otherwise define it and then
    return it.

    Args:
      tip_type: Tip type.

    Returns:
      Tip type ID.
    """

    if tip_type not in self._tip_types:
      self.define_tip_type(tip_type)
    return self.get_tip_type_table_index(tip_type)

  def pickup_tips(
    self,
    resource: typing.Union[str, Tips],
    pattern: typing.List[bool]
  ):
    """ Pick up tips from a resource.

    Args:
      resource: Resource name or resource object.
      pattern: List of boolean values indicating which sites to pick up.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Tips):
      resource = resource.name
    resource = self.get_resource(resource)

    assert 0 < len(pattern) <= 8, "Must have 0 < len(pattern) <= 8."

    # yp5298 5208 5118 5028 4938 4848 4758 4668 ( 20210715.trc),
    # matches 210701_menny_on_deck_turb_general_300uLtips_0002/discrete-turb/assets/deck.lay

    # TODO: what does ' C0TPid0205xp06579 06579 06579 06579 00000&yp2418 2328 2238 2148 0000&tm1 1 1 1 0&tt05tp2264tz2164th2450td1' do?

    # Get x positions.
    tip_pattern = [1, 0] # 1 where x_pos is relevant., probably want to use 2D array and set to 1 where child list is >0
    x_positions = []
    # TODO: loop over lists to get x positions.
    i = 1
    x_positions.append(int(resource.location.x * 10) + 90*i)

    # Get y positions of tips.
    y_positions = []
    for i, p in enumerate(pattern):
      if p:
        # TODO: what is -90?
        y_positions.append(int(resource.location.y * 10) - i*90)

    # TODO: Must have leading zero if len != 8?
    if len(y_positions) < 8:
      y_positions.append(0)

    ttti = self.get_or_assign_tip_type_index(resource.tip_type)

    return self.backend.pick_up_tip(
      x_positions=x_positions,
      y_positions=y_positions,
      tip_pattern=tip_pattern,
      tip_type=ttti,
      begin_tip_pick_up_process=2244,
      end_tip_pick_up_process=2164,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      pick_up_method=0
    )

  def discard_tips(
    self,
    resource: typing.Union[str, Tips],
    pattern: typing.List[bool]
  ):
    """ Discard tips from a resource.

    Args:
      resource: Resource name or resource object.
      pattern: List of boolean values indicating which sites to drop.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Tips):
      resource = resource.name
    resource = self.get_resource(resource)

    assert 0 < len(pattern) <= 8, "Must have 0 < len(pattern) <= 8."

    # Get x positions.
    x_positions = []
    i = 1
    x_positions.append(int(resource.location.x * 10) + 90*i)
    # TODO: loop over lists to get x positions.

    # Get y positions of tips.
    y_positions = []
    for i, p in enumerate(pattern):
      if p:
        # TODO: what is -90?
        y_positions.append(int(resource.location.y * 10) - i*90)

    # TODO: Must have leading zero if len != 8?
    if len(y_positions) < 8:
      y_positions.append(0)
      x_positions.append(0)
    tip_pattern = [(1 if x != 0 else 0) for x in x_positions]

    ttti = self.get_or_assign_tip_type_index(resource.tip_type)

    return self.backend.discard_tip(
      x_positions=x_positions,
      y_positions=y_positions,
      tip_pattern=tip_pattern,
      tip_type=ttti,
      begin_tip_deposit_process=2244,
      end_tip_deposit_process=2164,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      discarding_method=0
    )

  def aspirate(
    self,
    resource: typing.Union[str, Plate],
    volumes: typing.List[typing.List[float]],
    liquid_class: LiquidClass = StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    **kwargs
  ):
    """ Aspirate liquid from a resource.

    Args:
      resource: Resource name or resource object.
      volumes: List of lists of volumes to aspirate. The outer list is for rows, the inner list is
        for columns.
      liquid_class: Liquid class of aspirated liquid. This is used to correct the volumes and to
        update default parameters for aspiration where those are not overwritten by `kwargs`.
      kwargs: Keyword arguments for `LiquidHandler.aspirate_pip`. Where there is no value for a
        keyword argument, the default value is used. See `LiquidHandler.aspirate_pip` for details.
        Each keyword argument for a list parameter (again, see `LiquidHandler.aspirate_pip`) must
        have the same length as the list of volumes, or length 1, in which case the value is
        applied to all channels. Non-list parameters are applied to all channels by Hamilton.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Plate):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    # Get x and y positions.
    x_positions = []
    y_positions = []

    for i, vol in enumerate(volumes):
      if vol > 0:
        # TODO: what is -90?
        y_positions.append(int(resource.location.y * 10) - i*90)
        x_positions.append(int(resource.location.x * 10))

    # TODO: Must have leading zero if len != 8?
    if len(y_positions) < 8:
      y_positions.append(0)
      x_positions.append(0)
    tip_pattern = [(1 if x != 0 else 0) for x in x_positions]

    # Correct volumes for liquid class. Then multiply by 10 to get to units of 0.1uL.
    corrected_volumes = []
    for i, vol in enumerate(volumes):
      if vol > 0:
        # TODO: Remove this when we have the new liquid class.
        # corrected_volumes.append(int(liquid_class.compute_corrected_volume(vol) * 10))
        corrected_volumes.append(int(vol * 1.072 * 10))
    # TODO: Must have leading zero if len != 8?
    # if len(corrected_volumes) < 8:
      # corrected_volumes.append(0)

    num_wells = len(corrected_volumes) #- 1

    # Set default values for command parameters.
    cmd_kwargs = dict( # pylint: disable=use-dict-literal
      # aspiration_type=0,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      min_z_endpos=2450,
      lld_search_height=[2321] * num_wells, # TODO: is this necessary? + [2450],
      clot_detection_height=[0] * num_wells, # TODO: is this necessary? + [0],
      liquid_surface_no_lld=[1881] * num_wells, # TODO: is this necessary? + [2450],
      pull_out_distance_transport_air=[100],
      second_section_height=[32] * num_wells, # TODO: is this necessary? + [0],
      second_section_ratio=[6180] * num_wells, # TODO: is this necessary? + [0],
      minimum_height=[1871] * num_wells, # TODO: is this necessary? + [0],
      immersion_depth=[0],
      immersion_depth_direction=[0],
      surface_following_distance=[0],
      aspiration_speed=[1000],
      transport_air_volume=[0],
      blow_out_air_volume=[0],
      pre_wetting_volume=[0],
      lld_mode=[0],
      gamma_lld_sensitivity=[1],
      dp_lld_sensitivity=[1],
      aspirate_position_above_z_touch_off=[0],
      detection_height_difference_for_dual_lld=[0],
      swap_speed=[20],
      settling_time=[10],
      homogenization_volume=[0],
      homogenization_cycles=[0],
      homogenization_position_from_liquid_surface=[0],
      homogenization_speed=[1000],
      homogenization_surface_following_distance=[0],
      limit_curve_index=[0],

      use_2nd_section_aspiration=[0],
      retract_height_over_2nd_section_to_empty_tip=[0],
      dispensation_speed_during_emptying_tip=[500],
      dosing_drive_speed_during_2nd_section_search=[500],
      z_drive_speed_during_2nd_section_search=[300],
      cup_upper_edge=[0],
      ratio_liquid_rise_to_tip_deep_in=[0],
      immersion_depth_2nd_section=[0] * num_wells
    )

    # Update kwargs with liquid class properties.
    cmd_kwargs.update(liquid_class.aspirate_kwargs)
    # TODO: Update wrong liquid class properties, should be fixed with new liquid class.
    cmd_kwargs.update({
      "blow_out_air_volume": [0],
    })

    # Update kwargs with user properties.
    cmd_kwargs.update(kwargs)

    # Make sure each parameter which is a list of the same length as the number of wells. If the
    # length of the parameter is 1, then duplicate it for each well. If the length of the parameter
    # is not 1, then make sure it is the same length as the number of wells. If the length of the
    # parameter is not 1 and not the same length as the number of wells, then raise an error.
    for param_name, param_value in cmd_kwargs.items():
      if isinstance(param_value, list):
        if len(param_value) == 1:
          cmd_kwargs[param_name] = [param_value[0]] * num_wells
        elif len(param_value) != num_wells:
          raise ValueError(f"The {param_name} parameter must be a list of the same length as the "
                            "number of wells or a single value.")

    return self.backend.aspirate_pip(
      tip_pattern=tip_pattern,
      x_positions=x_positions,
      y_positions=y_positions,
      aspiration_volumes=corrected_volumes,
      **cmd_kwargs,
    )

  def dispense(
    self,
    resource: typing.Union[str, Plate],
    volumes: typing.List[typing.List[float]],
    liquid_class: LiquidClass = StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    **kwargs
  ):
    """ Dispense liquid from a resource.

    Args:
      resource: Resource name or resource object.
      volumes: List of lists of volumes to dispense. The outer list is for rows, the inner list is
        for columns.
      liquid_class: Liquid class of dispensed liquid. This is used to correct the volumes and to
        update default parameters for dispension where those are not overwritten by `kwargs`.
      kwargs: Keyword arguments for `LiquidHandler.dispense_pip`. Where there is no value for a
        keyword argument, the default value is used. See `LiquidHandler.aspirate_pip` for details.
        Each keyword argument for a list parameter (again, see `LiquidHandler.aspirate_pip`) must
        have the same length as the list of volumes, or length 1, in which case the value is
        applied to all channels. Non-list parameters are applied to all channels by Hamilton.
    """

    # Get resource using `get_resource` to adjust location.
    if isinstance(resource, Plate):
      resource = resource.name
    resource = self.get_resource(resource)
    if not resource:
      raise ValueError(f"Resource with name {resource} not found.")

    # Get x and y positions.
    x_positions = []
    y_positions = []

    for i, vol in enumerate(volumes):
      if vol > 0:
        # TODO: what is -90?
        y_positions.append(int(resource.location.y * 10) - i*90)
        x_positions.append(int(resource.location.x * 10))

    # TODO: Must have leading zero if len != 8?
    if len(y_positions) < 8:
      y_positions.append(0)
      x_positions.append(0)
    tip_pattern = [(1 if x != 0 else 0) for x in x_positions]

    # Correct volumes for liquid class. Then multiply by 10 to get to units of 0.1uL.
    corrected_volumes = []
    for i, vol in enumerate(volumes):
      if vol > 0:
        corrected_volumes.append(int(liquid_class.compute_corrected_volume(vol) * 10))
    # TODO: Must have leading zero if len != 8?
    # if len(corrected_volumes) < 8:
      # corrected_volumes.append(0)

    num_wells = len(corrected_volumes) #- 1

    # Set default values for command parameters.
    cmd_kwargs = dict( # pylint: disable=use-dict-literal
      minimum_traverse_height_at_beginning_of_a_command=2450,
      min_z_endpos=2450,
      lld_search_height=[2321] * num_wells, # TODO: is this necessary? + [2450],
      liquid_surface_no_lld=[1881] * num_wells, # TODO: is this necessary? + [2450],
      pull_out_distance_transport_air=[100],
      second_section_height=[32] * num_wells, # TODO: is this necessary? + [0],
      second_section_ratio=[6180] * num_wells, # TODO: is this necessary? + [0],
      minimum_height=[1871] * num_wells, # TODO: is this necessary? + [0],
      immersion_depth=[0],
      immersion_depth_direction=[0],
      surface_following_distance=[0],
      dispense_speed=[1200],
      cut_off_speed=[50],
      stop_back_volume=[0],
      transport_air_volume=[0],
      blow_out_air_volume=[0],
      lld_mode=[0],
      side_touch_off_distance=0,
      dispense_position_above_z_touch_off=[0],
      gamma_lld_sensitivity=[1],
      dp_lld_sensitivity=[1],
      swap_speed=[20],
      settling_time=[10],
      mix_volume=[0],
      mix_cycles=[0],
      mix_position_from_liquid_surface=[0],
      mix_speed=[10],
      mix_surface_following_distance=[0],
      limit_curve_index=[0]
    )

    # Update kwargs with liquid class properties.
    cmd_kwargs.update(liquid_class.dispense_kwargs)

    # Update kwargs with user properties.
    cmd_kwargs.update(kwargs)

    # Make sure each parameter which is a list of the same length as the number of wells. If the
    # length of the parameter is 1, then duplicate it for each well. If the length of the parameter
    # is not 1, then make sure it is the same length as the number of wells. If the length of the
    # parameter is not 1 and not the same length as the number of wells, then raise an error.
    for param_name, param_value in cmd_kwargs.items():
      if isinstance(param_value, list):
        if len(param_value) == 1:
          cmd_kwargs[param_name] = [param_value[0]] * num_wells
        elif len(param_value) != num_wells:
          raise ValueError(f"The {param_name} parameter must be a list of the same length as the "
                            "number of wells or a single value.")

    return self.backend.dispense_pip(
      tip_pattern=tip_pattern,
      x_positions=x_positions,
      y_positions=y_positions,
      dispense_volumes=corrected_volumes,
      **cmd_kwargs
    )
