"""
This file defines interfaces for all supported Hamilton liquid handling robots.
"""
# pylint: disable=invalid-sequence-index, dangerous-default-value

from abc import ABCMeta, abstractmethod
import datetime
import enum
import logging
import re
import typing
from typing import Union, List, Optional

from pylabrobot import utils
from pylabrobot.liquid_handling.resources import (
  Coordinate,
  Hotel,
  Lid,
  Resource,
  Plate,
  Tip,
  Tips,
  TipType,
)
from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.liquid_classes import LiquidClass
from pylabrobot.liquid_handling.standard import Aspiration, Dispense

from .errors import (
  HamiltonFirmwareError
)

logger = logging.getLogger(__name__)
logging.basicConfig()
logger.setLevel(logging.INFO)

try:
  import usb.core
  import usb.util
  USE_USB = True
except ImportError:
  logger.warning("Could not import pyusb, Hamilton interface will not be available.")
  USE_USB = False


class HamiltonLiquidHandler(LiquidHandlerBackend, metaclass=ABCMeta):
  """
  Abstract base class for Hamilton liquid handling robot backends.
  """

  @abstractmethod
  def __init__(self, read_timeout=5):
    """

    Args:
      read_timeout: The timeout for reading packets from the Hamilton machine in seconds.
    """

    super().__init__()

    self.read_timeout = read_timeout
    self.id_ = 0

  def _generate_id(self):
    """ continuously generate unique ids 0 <= x < 10000. """
    self.id_ += 1
    return f"{self.id_ % 10000:04}"

  def _assemble_command(self, module, command, **kwargs) -> str:
    """ Assemble a firmware command to the Hamilton machine.

    Args:
      module: 2 character module identifier (C0 for master, ...)
      command: 2 character command identifier (QM for request status, ...)
      kwargs: any named parameters. the parameter name should also be
              2 characters long. The value can be any size.

    Returns:
      A string containing the assembled command.
    """

    # pylint: disable=redefined-builtin

    cmd = module + command
    id = self._generate_id()
    cmd += f"id{id}" # has to be first param

    for k, v in kwargs.items():
      if type(v) is datetime.datetime:
        v = v.strftime("%Y-%m-%d %h:%M")
      elif type(v) is bool:
        v = 1 if v else 0
      elif type(v) is list:
        if type(v[0]) is bool: # convert bool list to int list
          v = [int(x) for x in v]
        v = " ".join([str(e) for e in v]) + ("&" if len(v) < 8 else "")
      if k.endswith("_"): # workaround for kwargs named in, as, ...
        k = k[:-1]
      cmd += f"{k}{v}"

    return cmd, id

  def _read_packet(self) -> typing.Optional[str]:
    """ Read a packet from the Hamilton machine.

    Returns:
      A string containing the decoded packet, or None if no packet was received.
    """

    try:
      res = self.dev.read(
        self.read_endpoint,
        self.read_endpoint.wMaxPacketSize,
        timeout=int(self.read_timeout * 1000) # timeout in ms
      )
    except usb.core.USBError:
      # No data available (yet), this will give a timeout error. Don't reraise.
      return None
    if res is not None:
      res = bytearray(res).decode("utf-8") # convert res into text
      return res
    return None

  def parse_fw_string(self, resp: str, fmt: str = "") -> typing.Optional[dict]:
    """ Parse a machine command or response string according to a format string.

    The format contains names of parameters (always length 2),
    followed by an arbitrary number of the following, but always
    the same:
    - '&': char
    - '#': decimal
    - '*': hex

    The order of parameters in the format and response string do not
    have to (and often do not) match.

    The identifier parameter (id####) is added automatically.

    TODO: string parsing
    The firmware docs mention strings in the following format: '...'
    However, the length of these is always known (except when reading
    barcodes), so it is easier to convert strings to the right number
    of '&'. With barcode reading the length of the barcode is included
    with the response string. We'll probably do a custom implementation
    for that.

    TODO: spaces
    We should also parse responses where integers are separated by spaces,
    like this: `ua#### #### ###### ###### ###### ######`

    Args:
      resp: The response string to parse.
      fmt: The format string.

    Raises:
      ValueError: if the format string is incompatible with the response.

    Returns:
      A dictionary containing the parsed values.

    Examples:
      Parsing a string containing decimals (`1111`), hex (`0xB0B`) and chars (`'rw'`):

      ```
      >>> parse_fw_string("aa1111bbrwccB0B", "aa####bb&&cc***")
      {'aa': 1111, 'bb': 'rw', 'cc': 2827}
      ```
    """

    # Remove device and cmd identifier from response.
    resp = resp[4:]

    # Parse the parameters in the fmt string.
    info = {}

    def find_param(param):
      name, data = param[0:2], param[2:]
      type_ = {
        "#": "int",
        "*": "hex",
        "&": "str"
      }[data[0]]

      # Build a regex to match this parameter.
      exp = {
        "int": r"[-+]?[\d ]",
        "hex": r"[\da-fA-F ]",
        "str": ".",
      }[type_]
      len_ = len(data.split(" ")[0]) # Get length of first block.
      regex = f"{name}((?:{exp}{ {len_} }"

      if param.endswith(" (n)"):
        regex += " ?)+)"
        is_list = True
      else:
        regex += "))"
        is_list = False

      # Match response against regex, save results in right datatype.
      r = re.search(regex, resp)
      if r is None:
        # Don't raise an error if we are looking for the id parameter.
        # if name == "id":
          # return None
        raise ValueError(f"could not find matches for parameter {name}")

      g = r.groups()
      if len(g) == 0:
        raise ValueError(f"could not find value for parameter {name}")
      m = g[0]

      if is_list:
        m = m.split(" ")

        if type_ == "str":
          info[name] = m
        elif type_ == "int":
          info[name] = [int(m_) for m_ in m if m_ != ""]
        elif type_ == "hex":
          info[name] = [int(m_, base=16) for m_ in m if m_ != ""]
      else:
        if type_ == "str":
          info[name] = m
        elif type_ == "int":
          info[name] = int(m)
        elif type_ == "hex":
          info[name] = int(m, base=16)

    # Find params in string. All params are identified by 2 lowercase chars.
    param = ""
    prevchar = None
    for char in fmt:
      if char.islower() and prevchar != "(":
        if len(param) > 2:
          find_param(param)
          param = ""
      param += char
      prevchar = char
    if param != "":
      find_param(param) # last parameter is not closed by loop.
    if "id" not in info: # auto add id if we don't have it yet.
      find_param("id####")

    return info

  def parse_response(self, resp: str, fmt: str) -> typing.Tuple[dict, dict]:
    """ Parse a response from the Hamilton machine.

    This method uses the `parse_fw_string` method to get the info from the response string.
    Additionally, it finds any errors in the response.

    Args:
      response: A string containing the response from the Hamilton machine.
      fmt: A string containing the format of the response.

    Raises:
      ValueError: if the format string is incompatible with the response.
      HamiltonException: if the response contains an error.

    Returns:
      A dictionary containing the parsed response.
    """

    # Parse errors.
    module = resp[:2]
    if module == "C0":
      # C0 sends errors as er##/##. P1 raises errors as er## where the first group is the error
      # code, and the second group is the trace information.
      # Beyond that, specific errors may be added for individual channels and modules. These
      # are formatted as P1##/## H0##/##, etc. These items are added programmatically as
      # named capturing groups to the regex.

      exp = r"er(?P<C0>[0-9]{2}/[0-9]{2})"
      for module in ["X0", "I0", "W1", "W2", "T1", "T2", "R0", "P1", "P2", "P3", "P4", "P5", "P6",
                    "P7", "P8", "P9", "PA", "PB", "PC", "PD", "PE", "PF", "PG", "H0", "HW", "HU",
                    "HV", "N0", "D0", "NP", "M1"]:
        exp += f" ?(?:{module}(?P<{module}>[0-9]{{2}}/[0-9]{{2}}))?"
      errors = re.search(exp, resp)
    else:
      # Other modules send errors as er##, and do not contain slave errors.
      exp = f"er(?P<{module}>[0-9]{{2}})"
      errors = re.search(exp, resp)

    if errors is not None:
      errors = errors.groupdict()
      errors = {k:v for k,v in errors.items() if v is not None} # filter None elements
      # filter 00 and 00/00 elements, which mean no error.
      errors = {k:v for k,v in errors.items() if v not in ["00", "00/00"]}

    has_error = not (errors is None or len(errors) == 0)
    if has_error:
      he = HamiltonFirmwareError(errors, raw_response=resp)

      # If there is a faulty parameter error, request which parameter that is.
      # TODO: does this work?
      for module_name, error in he.items():
        if error.message == "Unknown parameter":
          vp = self.send_command(module=error.raw_module, command="VP", fmt="vp&&")
          module[module_name] += f" ({vp})"

      raise he

    info = self.parse_fw_string(resp, fmt)

    return info

  def send_command(
    self,
    module: str,
    command: str,
    timeout: int = 16,
    fmt: typing.Optional[str]=None,
    wait = True,
    **kwargs
  ):
    """ Send a firmware command to the Hamilton machine.

    Args:
      module: 2 character module identifier (C0 for master, ...)
      command: 2 character command identifier (QM for request status)
      timeout: timeout in seconds.
      fmt: A string containing the format of the response. If None, the raw response is returned.
      kwargs: any named parameters. the parameter name should also be
              2 characters long. The value can be any size.

    Raises:
      HamiltonFirmwareError: if an error response is received.

    Returns:
      A dictionary containing the parsed response, or None if no response was read within `timeout`.
    """

    cmd, id_ = self._assemble_command(module, command, **kwargs)

    # write command to endpoint
    self.dev.write(self.write_endpoint, cmd)
    logger.info("Sent command: %s", cmd)

    if not wait:
      return

    # Attempt to read packets until timeout, or when we identify the right id. Timeout is
    # approximately equal to the (number of attempts to read packets) * (self.read_timeout).
    attempts = 0
    while attempts < (timeout // self.read_timeout):
      res = self._read_packet()
      if res is None:
        continue

      # Parse preliminary response, there may be more data to read, but the first packet of the
      # response will definitely contain the id of the command we sent. If we find it, we read
      # the rest of the response, if it is not contained in a single packet.
      parsed_response = self.parse_fw_string(res)
      if "id" in parsed_response and f"{parsed_response['id']:04}" == id_:
        # While length of response is the maximum length, there may be more data to read.
        last_packet = res
        while last_packet is not None and len(last_packet) == self.read_endpoint.wMaxPacketSize:
          last_packet = self._read_packet()
          if last_packet is not None:
            res += last_packet

        logger.info("Received response: %s", res)

        # If `fmt` is None, return the raw response.
        if fmt is None:
          return res
        return self.parse_response(res, fmt)

      attempts += 1

class STAR(HamiltonLiquidHandler):
  """
  Interface for the Hamilton STAR.
  """

  def __init__(self, num_channels: int = 8, read_timeout: int = 5, **kwargs):
    """ Create a new STAR interface.

    Args:
      read_timeout: the timeout in seconds for reading packets.
    """

    super().__init__(read_timeout=read_timeout, **kwargs)
    self.dev: Optional[usb.core.Device] = None
    self._tip_types: dict[str, int] = {}
    self.num_channels = num_channels

    self.read_endpoint: Optional[usb.core.Endpoint] = None
    self.write_endpoint: Optional[usb.core.Endpoint] = None

  def setup(self):
    """ setup

    Creates a USB connection and finds read/write interfaces.
    """

    if not USE_USB:
      raise RuntimeError("USB is not enabled. Please install pyusb.")

    if self.dev is not None:
      logging.warning("Already initialized. Please call stop() first.")
      return

    logger.info("Finding Hamilton USB device...")

    self.dev = usb.core.find(idVendor=0x08af)
    if self.dev is None:
      raise ValueError("Hamilton STAR device not found.")

    logger.info("Found Hamilton USB device.")

    # set the active configuration. With no arguments, the first
    # configuration will be the active one
    self.dev.set_configuration()

    cfg = self.dev.get_active_configuration()
    intf = cfg[(0,0)]

    self.write_endpoint = usb.util.find_descriptor(
      intf,
      custom_match = \
      lambda e: \
          usb.util.endpoint_direction(e.bEndpointAddress) == \
          usb.util.ENDPOINT_OUT)

    self.read_endpoint = usb.util.find_descriptor(
      intf,
      custom_match = \
      lambda e: \
          usb.util.endpoint_direction(e.bEndpointAddress) == \
          usb.util.ENDPOINT_IN)

    logger.info("Found endpoints. \nWrite:\n %s \nRead:\n %s", self.write_endpoint,
      self.read_endpoint)

    initialized = self.request_instrument_initialization_status()
    if not initialized:
      logger.info("Running backend initialization procedure.")

      # initialization procedure
      # TODO: before layout...
      self.pre_initialize_instrument()

      # Spread PIP channels command = JE ? (Spread PIP channels)

      #C0DIid0201xp08000&yp4050 3782 3514 3246 2978 2710 2442 2175tp2450tz1220te2450tm1&tt04ti0
      self.initialize_pipetting_channels( # spreads channels
        x_positions=[8000],
        # dy = 268
        y_positions=[4050, 3782, 3514, 3246, 2978, 2710, 2442, 2175],
        begin_of_tip_deposit_process=2450,
        end_of_tip_deposit_process=1220,
        z_position_at_end_of_a_command=3600,
        tip_pattern=[1], # [1] * 8
        tip_type=4, # TODO: get from tip types
        discarding_method=0
      )

    if not self.request_iswap_initialization_status():
      self.initialize_iswap()

    self.park_iswap()

  def stop(self):
    if self.dev is None:
      raise ValueError("USB device was not connected.")
    logging.warning("Closing connection to USB device.")
    usb.util.dispose_resources(self.dev)
    self.dev = None

  # ============== Tip Types ==============

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
    self.define_tip_needle(
      tip_type_table_index=ttti,
      filter=tip_type.has_filter,
      tip_length=int(tip_type.tip_length * 10), # in 0.1mm
      maximum_tip_volume=int(tip_type.maximal_volume * 10), # in 0.1ul
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
      Tip type IDliquid_handling.
    """

    if tip_type not in self._tip_types:
      self.define_tip_type(tip_type)
    return self.get_tip_type_table_index(tip_type)

  # ============== LiquidHandlerBackend methods ==============

  def assigned_resource_callback(self, resource: Resource):
    if isinstance(resource, Tips):
      if resource.tip_type not in self._tip_types:
        self.define_tip_type(resource.tip_type)

  def _channel_positions_to_fw_positions(self, resources: List[Optional[Resource]]) -> \
    typing.Tuple[typing.List[int], typing.List[int], typing.List[bool]]:

    x_positions = [(int(channel.get_absolute_location().x*10) if channel is not None else 0)
                    for channel in resources]
    y_positions = [(int(channel.get_absolute_location().y*10) if channel is not None else 0)
                    for channel in resources]
    channels_involved = [r is not None for r in resources]

    if len(resources) > self.num_channels:
      raise ValueError(f"Too many channels specified: {len(resources)} > {self.num_channels}")

    if len(x_positions) < self.num_channels:
      # We do want to have a trailing zero on x_positions, y_positions, and channels_involved, for
      # some reason, if the length < 8.
      x_positions = x_positions + [0]
      y_positions = y_positions + [0]
      channels_involved = channels_involved + [False]

    return x_positions, y_positions, channels_involved

  def get_ttti(self, tips: List[Tip]):
    """ Get tip type table index for a list of tips. """

    # Remove None values
    tips = [tip for tip in tips if tip is not None]

    # Checks that all tips are of the same type
    tip_types = set(tip.tip_type for tip in tips)
    if len(tip_types) != 1:
      raise ValueError("All tips must be of the same type.")

    return self.get_or_assign_tip_type_index(tip_types.pop())

  def pickup_tips(
    self,
    *channels: List[Optional[Tip]],
    **backend_kwargs
  ):
    """ Pick up tips from a resource. """

    x_positions, y_positions, channels_involved = self._channel_positions_to_fw_positions(channels)
    ttti = self.get_ttti(channels)

    params = {
      "begin_tip_pick_up_process": 2244,
      "end_tip_pick_up_process": 2164,
      "minimum_traverse_height_at_beginning_of_a_command": 2450,
      "pick_up_method": 0
    }
    params.update(backend_kwargs)

    return self.pick_up_tip(
      x_positions=x_positions,
      y_positions=y_positions,
      tip_pattern=channels_involved,
      tip_type=ttti,
      **params
    )

  def discard_tips(
    self,
    *channels: List[Optional[Tip]],
    **backend_kwargs
  ):
    """ Discard tips from a resource. """

    x_positions, y_positions, channels_involved = self._channel_positions_to_fw_positions(channels)
    ttti = self.get_ttti(channels)

    # TODO: should depend on tip carrier/type?
    params = {
      "begin_tip_deposit_process": 1314, #1744, #1970, #2244,
      "end_tip_deposit_process": 1414, # 1644, #1870, #2164,
      "minimum_traverse_height_at_beginning_of_a_command": 2450,
      "discarding_method": 0
    }
    params.update(backend_kwargs)

    return self.discard_tip(
      x_positions=x_positions,
      y_positions=y_positions,
      tip_pattern=channels_involved,
      tip_type=ttti,
      **params
    )

  def aspirate(
    self,
    *channels: Aspiration,
    blow_out_air_volume: float = 0,
    liquid_height: float = 1,
    air_transport_retract_dist: float = 10,
    **backend_kwargs
  ):
    """ Aspirate liquid from the specified channels. """

    resources = [(channel.resource if channel is not None else None) for channel in channels]
    x_positions, y_positions, channels_involved = self._channel_positions_to_fw_positions(resources)

    params = []

    # Correct volumes for liquid class. Then multiply by 10 to get to units of 0.1uL. Also get
    # all other aspiration parameters.
    for channel in channels:
      liquid_surface_no_lld = channel.resource.get_absolute_location().z + (liquid_height or 1)

      params.append({
        "aspiration_volumes": int(channel.get_corrected_volume()*10) if channel is not None else 0,
        "lld_search_height": int((liquid_surface_no_lld+5) * 10), #2321,
        "clot_detection_height": 0,
        "liquid_surface_no_lld": int(liquid_surface_no_lld * 10), #1881,
        "pull_out_distance_transport_air": int(air_transport_retract_dist * 10),
        "second_section_height": 32,
        "second_section_ratio": 6180,
        "minimum_height": int((liquid_surface_no_lld-5) * 10), #1871,
        "immersion_depth": 0,
        "immersion_depth_direction": 0,
        "surface_following_distance": 0,
        "aspiration_speed": 1000,
        "transport_air_volume": 0,
        "blow_out_air_volume": 0, # blow out air volume is handled separately, see below.
        "pre_wetting_volume": 0,
        "lld_mode": 0,
        "gamma_lld_sensitivity": 1,
        "dp_lld_sensitivity": 1,
        "aspirate_position_above_z_touch_off": 0,
        "detection_height_difference_for_dual_lld": 0,
        "swap_speed": 20,
        "settling_time": 10,
        "homogenization_volume": 0,
        "homogenization_cycles": 0,
        "homogenization_position_from_liquid_surface": 0,
        "homogenization_speed": 1000,
        "homogenization_surface_following_distance": 0,
        "limit_curve_index": 0,

        "use_2nd_section_aspiration": 0,
        "retract_height_over_2nd_section_to_empty_tip": 0,
        "dispensation_speed_during_emptying_tip": 500,
        "dosing_drive_speed_during_2nd_section_search": 500,
        "z_drive_speed_during_2nd_section_search": 300,
        "cup_upper_edge": 0,
        "ratio_liquid_rise_to_tip_deep_in": 0,
        "immersion_depth_2nd_section": 0
      })

    cmd_kwargs = {
      "minimum_traverse_height_at_beginning_of_a_command": 2450,
      "min_z_endpos": 2450,
    }

    # Convert the list of dictionaries to a single dictionary where all values for the same key are
    # accumulated in a list with that key.
    for kwargs in params:
      for key, value in kwargs.items():
        if key not in cmd_kwargs:
          cmd_kwargs[key] = []
        cmd_kwargs[key].append(value)

    # Update kwargs with user properties.
    cmd_kwargs.update(backend_kwargs)

    # Unfortunately, `blow_out_air_volume` does not work correctly, so instead we aspirate air
    # manually.
    if blow_out_air_volume is not None and blow_out_air_volume > 0:
      self.aspirate_pip(
        tip_pattern=channels_involved,
        x_positions=x_positions,
        y_positions=y_positions,
        lld_mode=0,
        liquid_surface_no_lld=50,
        aspiration_volumes=blow_out_air_volume
      )

    # Also filter each cmd_kwarg that is a list
    for key, value in cmd_kwargs.items():
      if isinstance(value, list):
        cmd_kwargs[key] = [v for i, v in enumerate(value) if channels_involved[i]]

    return self.aspirate_pip(
      tip_pattern=channels_involved,
      x_positions=x_positions,
      y_positions=y_positions,
      **cmd_kwargs,
    )

  def dispense(
    self,
    *channels: Dispense,
    blow_out_air_volumes: float = 0,
    liquid_height: Optional[float] = None,
    air_transport_retract_dist: float = 10,
    **backend_kwargs
  ):
    """ Dispense liquid from the specified channels. """

    resources = [(channel.resource if channel is not None else None) for channel in channels]
    x_positions, y_positions, channels_involved = self._channel_positions_to_fw_positions(resources)

    params = []

    for channel in channels:
      liquid_surface_no_lld = channel.resource.get_absolute_location().z + (liquid_height or 1)

      params.append({
        "dispensing_mode": 2,
        "dispense_volumes": int(channel.get_corrected_volume()*10) if channel is not None else 0,
        "lld_search_height": 2321,
        "liquid_surface_no_lld": int(liquid_surface_no_lld * 10), #1881,
        "pull_out_distance_transport_air": int(air_transport_retract_dist * 10),
        "second_section_height": 32,
        "second_section_ratio": 6180,
        "minimum_height": 1871,
        "immersion_depth": 0,
        "immersion_depth_direction": 0,
        "surface_following_distance": 0,
        "dispense_speed": 1200,
        "cut_off_speed": 50,
        "stop_back_volume": 0,
        "transport_air_volume": 0,
        "blow_out_air_volume": 0, # blow out air volume is handled separately, see below.
        "lld_mode": 0,
        "dispense_position_above_z_touch_off": 0,
        "gamma_lld_sensitivity": 1,
        "dp_lld_sensitivity": 1,
        "swap_speed": 20,
        "settling_time": 0,
        "mix_volume": 0,
        "mix_cycles": 0,
        "mix_position_from_liquid_surface": 0,
        "mix_speed": 10,
        "mix_surface_following_distance": 0,
        "limit_curve_index": 0
      })

    cmd_kwargs = {
      "minimum_traverse_height_at_beginning_of_a_command": 2450,
      "min_z_endpos": 2450,
      "side_touch_off_distance": 0,
    }

    # Convert the list of dictionaries to a single dictionary where all values for the same key are
    # accumulated in a list with that key.
    for kwargs in params:
      for key, value in kwargs.items():
        if key not in cmd_kwargs:
          cmd_kwargs[key] = []
        cmd_kwargs[key].append(value)

    # Update kwargs with user properties.
    cmd_kwargs.update(backend_kwargs)

    # Do normal dispense first, then blow out air (maybe).
    ret = self.dispense_pip(
      tip_pattern=channels_involved,
      x_positions=x_positions,
      y_positions=y_positions,
      **cmd_kwargs
    )

    # Unfortunately, `blow_out_air_volume` does not work correctly, so instead we dispense air
    # manually.
    if isinstance(blow_out_air_volumes, list):
      boavs = [boav*10 for boav in blow_out_air_volumes if boav is not None and boav > 0] # 0.1ul
    elif blow_out_air_volumes is not None and blow_out_air_volumes > 0:
      boavs = [blow_out_air_volumes*10 for _ in range(8)] # 0.1ul
    else:
      boavs = []
    if len(boavs) > 0:
      self.dispense_pip(
        tip_pattern=channels_involved,
        x_positions=x_positions,
        y_positions=y_positions,
        lld_mode=0,
        # units of 0.1mm, 1cm above
        liquid_surface_no_lld=[int((channels[0][0].get_absolute_location().z + 10) * 10)] * 8,
        dispense_volumes=boavs
      )

    return ret

  def pickup_tips96(self, resource: Tips, **backend_kwargs):
    ttti = self.get_or_assign_tip_type_index(resource.tip_type)
    position = resource.get_item("A1").get_absolute_location()

    cmd_kwargs = dict(
      x_position=int(position.x * 10),
      x_direction=0,
      y_position=int(position.y * 10),
      tip_type=ttti,
      tip_pick_up_method=0,
      z_deposit_position=2164,
      minimum_height_command_end=2450,
      minimum_traverse_height_at_beginning_of_a_command=2450
    )

    cmd_kwargs.update(backend_kwargs)

    return self.pick_up_tips_core96(**cmd_kwargs)

  def discard_tips96(self, resource: Resource, **backend_kwargs):
    position = resource.get_item("A1").get_absolute_location()

    cmd_kwargs = dict(
      x_position=int(position.x * 10),
      x_direction=0,
      y_position=int(position.y * 10),
      z_deposit_position=2164,
      minimum_height_command_end=2450,
      minimum_traverse_height_at_beginning_of_a_command=2450
    )

    cmd_kwargs.update(backend_kwargs)

    return self.discard_tips_core96(**cmd_kwargs)

  def aspirate96(
    self,
    resource: Resource,
    pattern: List[List[bool]],
    volume: float,
    liquid_class: Optional[LiquidClass] = None,
    blow_out_air_volume: float = 0,
    use_lld: bool = False,
    liquid_height: float = 2,
    air_transport_retract_dist: float = 10,
    **backend_kwargs
  ):
    position = resource.get_item("A1").get_absolute_location()

    # flatten pattern array
    pattern = [item for sublist in pattern for item in sublist]

    liquid_height = resource.get_absolute_location().z + liquid_height

    cmd_kwargs = dict(
      x_position=int(position.x * 10),
      x_direction=0,
      y_positions=int(position.y * 10),
      aspiration_type=0,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      minimal_end_height=2450,
      lld_search_height=1999,
      liquid_surface_at_function_without_lld=int(liquid_height * 10), # bleach: 1269, plate: 1879
      pull_out_distance_to_take_transport_air_in_function_without_lld=
        (air_transport_retract_dist * 10),
      maximum_immersion_depth=1269,
      tube_2nd_section_height_measured_from_zm=32,
      tube_2nd_section_ratio=6180,
      immersion_depth=0,
      immersion_depth_direction=0,
      liquid_surface_sink_distance_at_the_end_of_aspiration=0,
      aspiration_volumes=int(liquid_class.compute_corrected_volume(volume)*10),
      aspiration_speed=2500,
      transport_air_volume=50,
      blow_out_air_volume=0,
      pre_wetting_volume=50,
      lld_mode=use_lld,
      gamma_lld_sensitivity=1,
      swap_speed=20,
      settling_time=10,
      homogenization_volume=0,
      homogenization_cycles=0,
      homogenization_position_from_liquid_surface=0,
      surface_following_distance_during_homogenization=0,
      speed_of_homogenization=1200,
      channel_pattern=pattern,
      limit_curve_index=0,
      tadm_algorithm=False,
      recording_mode=0
    )

    cmd_kwargs.update(backend_kwargs)

    # Unfortunately, `blow_out_air_volume` does not work correctly, so instead we aspirate air
    # manually.
    if blow_out_air_volume is not None and blow_out_air_volume > 0:
      aspirate_air_cmd_kwargs = cmd_kwargs.copy()
      aspirate_air_cmd_kwargs.update(dict(
        x_position=int(position.x * 10),
        y_positions=int(position.y * 10),
        lld_mode=0,
        liquid_surface_at_function_without_lld=int((liquid_height + 30) * 10),
        aspiration_volumes=int(blow_out_air_volume * 10)
      ))
      self.aspirate_core_96(**aspirate_air_cmd_kwargs)

    return self.aspirate_core_96(**cmd_kwargs)

  def dispense96(
    self,
    resource: Resource,
    pattern: List[List[bool]],
    volume: float,
    liquid_class: Optional[LiquidClass] = None,
    mix_cycles=0,
    mix_volume=0,
    jet=False,
    blow_out=True, # TODO: do we need this if we can just check if blow_out_air_volume > 0?
    liquid_height: float = 2,
    dispense_mode=3,
    air_transport_retract_dist=10,
    blow_out_air_volume: float = 0,
  ):
    position = resource.get_item("A1").get_absolute_location()

    # flatten pattern array
    pattern = [item for sublist in pattern for item in sublist]

    liquid_height = resource.get_absolute_location().z + liquid_height

    dispense_mode = {
      (True, False): 0,
      (True, True): 1,
      (False, False): 2,
      (False, True): 3,
    }[(jet, blow_out)]

    cmd_kwargs = dict(
      dispensing_mode=dispense_mode,
      x_position=int(position.x * 10),
      x_direction=0,
      y_position=int(position.y * 10),
      minimum_traverse_height_at_beginning_of_a_command=2450,
      minimal_end_height=2450,
      lld_search_height=1999,
      liquid_surface_at_function_without_lld=int(liquid_height*10), # in [0.1mm]
      pull_out_distance_to_take_transport_air_in_function_without_lld=
        int(air_transport_retract_dist*10), # in [0.1mm]
      maximum_immersion_depth=1869,
      tube_2nd_section_height_measured_from_zm=32,
      tube_2nd_section_ratio=6180,
      immersion_depth=0,
      immersion_depth_direction=0,
      liquid_surface_sink_distance_at_the_end_of_dispense=0,
      dispense_volume=int(liquid_class.compute_corrected_volume(volume)*10),
      dispense_speed=1200,
      transport_air_volume=50,
      blow_out_air_volume=0,
      lld_mode=False,
      gamma_lld_sensitivity=1,
      swap_speed=20,
      settling_time=0,
      mixing_volume=int(mix_volume*10), # in [0.1ul]
      mixing_cycles=mix_cycles,
      mixing_position_from_liquid_surface=0,
      surface_following_distance_during_mixing=0,
      speed_of_mixing=1200,
      channel_pattern=pattern,
      limit_curve_index=0,
      tadm_algorithm=False,
      recording_mode=0,
      cut_off_speed=50,
      stop_back_volume=0,
    )

    ret = self.dispense_core_96(**cmd_kwargs)

    # Unfortunately, `blow_out_air_volume` does not work correctly, so instead we dispense air
    # manually.
    if blow_out_air_volume is not None and blow_out_air_volume > 0:
      dispense_air_cmd_kwargs = cmd_kwargs.copy()
      dispense_air_cmd_kwargs.update(dict(
        x_position=int(position.x * 10),
        y_position=int(position.y * 10),
        lld_mode=0,
        liquid_surface_at_function_without_lld=int((liquid_height + 30) * 10),
        dispense_volume=int(blow_out_air_volume * 10),
      ))
      self.dispense_core_96(**dispense_air_cmd_kwargs)

    return ret

  def move_plate(
    self,
    plate: Union[Coordinate, Plate],
    to: Union[Coordinate, Resource],
    pickup_distance_from_top: float = 13.2,
    **backend_kwargs
  ):
    assert isinstance(plate, Plate)

    # Get center of source plate.
    x = plate.get_absolute_location().x + plate.get_size_x()/2
    y = plate.get_absolute_location().y + plate.get_size_y()/2

    # Get the grip height for the plate.
    # grip_height = plate.get_absolute_location().z + plate.one_dot_max - \
    #               plate.dz - pickup_distance_from_top
    grip_height = plate.get_absolute_location().z + plate.get_size_z() - pickup_distance_from_top
    grip_height = int(grip_height * 10)
    x = int(x * 10)
    y = int(y * 10)

    get_cmd_kwargs = dict(
      grip_direction=backend_kwargs.pop("get_grip_direction", 1),
      minimum_traverse_height_at_beginning_of_a_command = 2840,
      z_position_at_the_command_end = 2840,
      grip_strength = 4,
      open_gripper_position = backend_kwargs.pop("get_open_gripper_position", 1300),
      plate_width = 1237, # 127?
      plate_width_tolerance = 20,
      collision_control_level = 0,
      acceleration_index_high_acc = 4,
      acceleration_index_low_acc = 1,
      fold_up_sequence_at_the_end_of_process = True
    )

    self.get_plate(
      x_position=x,
      x_direction=0,
      y_position=y,
      y_direction=0,
      z_position=grip_height,
      z_direction=0,
      **get_cmd_kwargs
    )

    # Move to the destination.
    if isinstance(to, Coordinate):
      to_location = to
    else:
      to_location = to.get_absolute_location()
      to_location = Coordinate(
        x=to_location.x + plate.get_size_x()/2,
        y=to_location.y + plate.get_size_y()/2,
        z=to_location.z + plate.get_size_z() - pickup_distance_from_top
      )

    put_cmd_kwargs = dict(
      grip_direction=backend_kwargs.pop("put_grip_direction", 1),
      minimum_traverse_height_at_beginning_of_a_command=2840,
      z_position_at_the_command_end=2840,
      open_gripper_position=backend_kwargs.get("put_open_gripper_position", 1300), # 127?
      collision_control_level=0,
    )

    self.put_plate(
      x_position=int(to_location.x * 10),
      x_direction=0,
      y_position=int(to_location.y * 10),
      y_direction=0,
      z_position=int(to_location.z * 10),
      z_direction=0,
      **put_cmd_kwargs
    )

  def move_lid(
    self,
    lid: Lid,
    to: typing.Union[Plate, Hotel],
    get_grip_direction: int = 1,
    get_open_gripper_position: int = 1300,
    pickup_distance_from_top: float = 1.2,
    put_grip_direction: int = 1,
    put_open_gripper_position: int = 1300,
    **backend_kwargs
  ):
    assert isinstance(lid, Lid), "lid must be a Lid"

    # Get center of source lid.
    x = lid.get_absolute_location().x + lid.get_size_x()/2
    y = lid.get_absolute_location().y + lid.get_size_y()/2

    # Get the grip height for the plate.
    grip_height = lid.get_absolute_location().z - pickup_distance_from_top

    x = int(x * 10)
    y = int(y * 10)
    grip_height = int(grip_height * 10)

    get_cmd_kwargs = dict(
      grip_direction=get_grip_direction,
      minimum_traverse_height_at_beginning_of_a_command = 2840,
      z_position_at_the_command_end = 2840,
      grip_strength = 4,
      open_gripper_position = get_open_gripper_position,
      plate_width = 1237, # 127?
      plate_width_tolerance = 20,
      collision_control_level = 0,
      acceleration_index_high_acc = 4,
      acceleration_index_low_acc = 1,
      fold_up_sequence_at_the_end_of_process = True
    )

    self.get_plate(
      x_position=x,
      x_direction=0,
      y_position=y,
      y_direction=0,
      z_position=grip_height,
      z_direction=0,
      **get_cmd_kwargs
    )

    # Move to the destination.
    if isinstance(to, Coordinate):
      to_location = to
    else:
      to_location = to.get_absolute_location()
      to_location = Coordinate(
        x=to_location.x + lid.get_size_x()/2,
        y=to_location.y + lid.get_size_y()/2,
        z=to_location.z - pickup_distance_from_top
      )

      # We're gonna place the lid on top of the to resource.
      to_location.z += to.get_size_z()

      try:
        if isinstance(lid.parent, Hotel):
          to_location.z += lid.get_size_z() # beacause it was removed by hotel when location changed
          to_location.z -= to.get_size_z() # the lid.get_size_z() is the height of the lid,
                                               # which will fit on top of the plate, so no need to
                                               # factor in the height of the plate.
      except AttributeError:
        pass

    put_cmd_kwargs = dict(
      grip_direction=put_grip_direction,
      minimum_traverse_height_at_beginning_of_a_command=2840,
      z_position_at_the_command_end=2840,
      open_gripper_position=put_open_gripper_position, # 127?
      collision_control_level=0,
    )

    self.put_plate(
      x_position=int(to_location.x * 10),
      x_direction=0,
      y_position=int(to_location.y * 10),
      y_direction=0,
      z_position=int(to_location.z * 10),
      z_direction=0,
      **put_cmd_kwargs
    )


  # ============== Firmware Commands ==============

  # -------------- 3.2 System general commands --------------

  def pre_initialize_instrument(self):
    """ Pre-initialize instrument """
    resp = self.send_command(module="C0", command="VI")
    return self.parse_response(resp, "")

  class FirmwareTipType(enum.Enum):
    """ Tip type """
    UNDEFINED=0
    LOW_VOLUME=1
    STANDARD_VOLUME=2
    HIGH_VOLUME=3
    CORE_384_HEAD_TIP=4
    XL=5

  class PickUpMethod(enum.Enum):
    """ Tip pick up method """
    OUT_OF_RACK=0
    OUT_OF_WASH_LIQUID=1

  def define_tip_needle(
    self,
    tip_type_table_index: int = 4,
    filter: bool = False,
    tip_length: int = 1950,
    maximum_tip_volume: int = 3500,
    tip_type: FirmwareTipType = FirmwareTipType.STANDARD_VOLUME,
    pick_up_method: PickUpMethod = PickUpMethod.OUT_OF_RACK
  ):
    """ Tip/needle definition.

    TODO: Define default values for type/application/filter.

    Args:
      tip_type_table_index: tip_table_index
      filter: with(out) filter
      tip_length: Tip length [0.1mm]
      maximum_tip_volume: Maximum volume of tip [0.1ul]
                          Note! it's automatically limited to max. channel capacity
      tip_type: Type of tip collar (Tip type identification)
      pick_up_method: pick up method.
                      Attention! The values set here are temporary and apply only until
                      power OFF or RESET. After power ON the default val- ues apply. (see Table 3)
    """

    # pylint: disable=redefined-builtin

    utils.assert_clamp(tip_type_table_index, 0, 99, "tip_type_table_index")
    filter = 1 if filter else 0
    utils.assert_clamp(tip_length, 1, 1999, "tip_length")
    utils.assert_clamp(maximum_tip_volume, 1, 56000, "maximum_tip_volume")

    return self.send_command(
      module="C0",
      command="TT",
      tt=f"{tip_type_table_index:02}",
      tf=filter,
      tl=f"{tip_length:04}",
      tv=f"{maximum_tip_volume:05}",
      tg=tip_type,
      tu=pick_up_method
    )

  # -------------- 3.2.1 System query --------------

  def request_error_code(self):
    """ Request error code

    Here the last saved error messages can be retrieved. The error buffer
    is automatically voided when a new command is started.
    All configured nodes are displayed.

    Returns:
      TODO:
      X0##/##: X0 slave
      ..##/## see node definitions ( chapter 5)
    """

    resp = self.send_command(module="RE", command="RF")
    return resp

  def request_firmware_version(self):
    """ Request firmware version

    Returns: TODO: Rfid0001rf1.0S 2009-06-24 A
    """
    return self.send_command(module="C0", command="RF")

  def request_parameter_value(self):
    """ Request parameter value

    Returns: TODO: Raid1111er00/00yg1200
    """

    return self.send_command(module="C0", command="RA")

  class BoardType(enum.Enum):
    C167CR_SINGLE_PROCESSOR_BOARD = 0
    C167CR_DUAL_PROCESSOR_BOARD = 1
    LPC2468_XE167_DUAL_PROCESSOR_BOARD = 2
    LPC2468_SINGLE_PROCESSOR_BOARD = 5
    UNKNOWN = -1

  def request_electronic_board_type(self):
    """ Request electronic board type

    Returns:
      The board type.
    """

    # pylint: disable=undefined-variable

    resp = self.send_command(module="C0", command="QB")
    try:
      return STAR.BoardType(resp["qb"])
    except ValueError:
      return STAR.BoardType.UNKNOWN

  # TODO: parse response.
  def request_supply_voltage(self):
    """ Request supply voltage

    Request supply voltage (for LDPB only)
    """

    return self.send_command(module="C0", command="MU")

  def request_instrument_initialization_status(self):
    """ Request instrument initialization status """

    return self.send_command(module="C0", command="QW", fmt="qw#")["qw"] == 1

  def request_name_of_last_faulty_parameter(self):
    """ Request name of last faulty parameter

    Returns: TODO:
      Name of last parameter with syntax error
      (optional) received value separated with blank
      (optional) minimal permitted value separated with blank (optional)
      maximal permitted value separated with blank example with min max data:
      Vpid2233er00/00vpth 00000 03500 example without min max data: Vpid2233er00/00vpcd
    """

    return self.send_command(module="C0", command="VP")

  def request_master_status(self):
    """ Request master status

    Returns: TODO: see page 19 (SFCO.0036)
    """

    return self.send_command(module="C0", command="RQ")

  def request_number_of_presence_sensors_installed(self):
    """ Request number of presence sensors installed

    Returns:
      number of sensors installed (1...103)
    """

    return self.send_command(module="C0", command="SR")["sr"]

  def request_eeprom_data_correctness(self):
    """ Request EEPROM data correctness

    Returns: TODO: (SFCO.0149)
    """

    return self.send_command(module="C0", command="QV")

  # -------------- 3.3 Settings --------------

  # -------------- 3.3.1 Volatile Settings --------------


  def set_single_step_mode(
    self,
    single_step_mode: bool = False
  ):
    """ Set Single step mode

    Args:
      single_step_mode: Single Step Mode. Default False.
    """

    return self.send_command(
      module="C0",
      command="AM",
      am=single_step_mode,
    )

  def trigger_next_step(self):
    """ Trigger next step (Single step mode) """

    # TODO: this command has no reply!!!!
    return self.send_command(module="C0", command="NS")

  def halt(self):
    """ Halt

    Intermediate sequences not yet carried out and the commands in
    the command stack are discarded. Sequence already in process is
    completed.
    """

    return self.send_command(module="C0", command="HD")

  def save_all_cycle_counters(self):
    """ Save all cycle counters

    Save all cycle counters of the instrument
    """

    return self.send_command(module="C0", command="AZ")

  def set_not_stop(self, non_stop):
    """ Set not stop mode

    Args:
      non_stop: True if non stop mode should be turned on after command is sent.
    """

    if non_stop:
      # TODO: this command has no reply!!!!
      return self.send_command(module="C0", command="AB")
    else:
      return self.send_command(module="C0", command="AW")

  # -------------- 3.3.2 Non volatile settings (stored in EEPROM) --------------

  def store_installation_data(
    self,
    date: datetime.datetime = datetime.datetime.now(),
    serial_number: str = "0000"
  ):
    """ Store installation data

    Args:
      date: installation date.
    """

    assert len(serial_number) == 4, "serial number must be 4 chars long"

    return self.send_command(
      module="C0",
      command="SI",
      si=date,
      sn=serial_number
    )

  def store_verification_data(
    self,
    verification_subject: int = 0,
    date: datetime.datetime = datetime.datetime.now(),
    verification_status: bool = None
  ):
    """ Store verification data

    Args:
      verification_subject: verification subject. Default 0. Must be between 0 and 24.
      date: verification date.
      verification_status: verification status.
    """

    utils.assert_clamp(verification_subject, 0, 24, "verification_subject")

    return self.send_command(
      module="C0",
      command="AV",
      vo=verification_subject,
      vd=date,
      vs=verification_status,
    )

  def additional_time_stamp(self):
    """ Additional time stamp """

    return self.send_command(module="C0", command="AT")

  def set_x_offset_x_axis_iswap(self, x_offset: int):
    """ Set X-offset X-axis <-> iSWAP

    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      module="C0",
      command="AG",
      x_offset=x_offset
    )

  def set_x_offset_x_axis_core_96_head(self, x_offset: int):
    """ Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      module="C0",
      command="AF",
      x_offset=x_offset
    )

  def set_x_offset_x_axis_core_nano_pipettor_head(self, x_offset: int):
    """ Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return self.send_command(
      module="C0",
      command="AF",
      x_offset=x_offset
    )

  def save_download_date(
    self,
    date: datetime.datetime = datetime.datetime.now()
  ):
    """ Save Download date

    Args:
      date: download date. Default now.
    """

    return self.send_command(
      module="C0",
      command="AO",
      ao=date,
    )

  def save_technical_status_of_assemblies(
    self,
    processor_board: str,
    power_supply: str
  ):
    """ Save technical status of assemblies

    Args:
      processor_board: Processor board. Art.Nr./Rev./Ser.No. (000000/00/0000)
      power_supply: Power supply. Art.Nr./Rev./Ser.No. (000000/00/0000)
    """

    return self.send_command(
      module="C0",
      command="BT",
      qt=processor_board + " " + power_supply,
    )

  def set_instrument_configuration(
    self,
    configuration_data_1: str = None, # TODO: configuration byte
    configuration_data_2: str = None, # TODO: configuration byte
    configuration_data_3: str = None, # TODO: configuration byte
    instrument_size_in_slots_x_range: int = 54,
    auto_load_size_in_slots: int = 54,
    tip_waste_x_position: int = 13400,
    right_x_drive_configuration_byte_1: int = 0,
    right_x_drive_configuration_byte_2: int = 0,
    minimal_iswap_collision_free_position: int = 3500,
    maximal_iswap_collision_free_position: int = 11400,
    left_x_arm_width: int = 3700,
    right_x_arm_width: int = 3700,
    num_pip_channels: int = 0,
    num_xl_channels: int = 0,
    num_robotic_channels: int = 0,
    minimal_raster_pitch_of_pip_channels: int = 90,
    minimal_raster_pitch_of_xl_channels: int = 360,
    minimal_raster_pitch_of_robotic_channels: int = 360,
    pip_maximal_y_position: int = 6065,
    left_arm_minimal_y_position: int = 60,
    right_arm_minimal_y_position: int = 60
  ):
    """ Set instrument configuration

    Args:
      configuration_data_1: configuration data 1.
      configuration_data_2: configuration data 2.
      configuration_data_3: configuration data 3.
      instrument_size_in_slots_x_range: instrument size in slots (X range).
                                          Must be between 10 and 99. Default 54.
      auto_load_size_in_slots: auto load size in slots. Must be between 10
                                and 54. Default 54.
      tip_waste_x_position: tip waste X-position. Must be between 1000 and
                            25000. Default 13400.
      right_x_drive_configuration_byte_1: right X drive configuration byte 1 (see
                                          xl parameter bits). Must be between 0 and 1.
                                          Default 0. # TODO: this.
      right_x_drive_configuration_byte_2: right X drive configuration byte 2 (see
                                          xn parameter bits). Must be between 0 and 1.
                                          Default 0. # TODO: this.
      minimal_iswap_collision_free_position: minimal iSWAP collision free position for
                                            direct X access. For explanation of calculation
                                            see Fig. 4. Must be between 0 and 30000. Default 3500.
      maximal_iswap_collision_free_position: maximal iSWAP collision free position for
                                              direct X access. For explanation of calculation
                                              see Fig. 4. Must be between 0 and 30000. Default 11400
      left_x_arm_width: width of left X arm [0.1 mm]. Must be between 0 and 9999. Default 3700.
      right_x_arm_width: width of right X arm [0.1 mm]. Must be between 0 and 9999. Default 3700.
      num_pip_channels: number of PIP channels. Must be between 0 and 16. Default 0.
      num_xl_channels: number of XL channels. Must be between 0 and 8. Default 0.
      num_robotic_channels: number of Robotic channels. Must be between 0 and 8. Default 0.
      minimal_raster_pitch_of_pip_channels: minimal raster pitch of PIP channels [0.1 mm]. Must
                                            be between 0 and 999. Default 90.
      minimal_raster_pitch_of_xl_channels: minimal raster pitch of XL channels [0.1 mm]. Must be
                                            between 0 and 999. Default 360.
      minimal_raster_pitch_of_robotic_channels: minimal raster pitch of Robotic channels [0.1 mm].
                                                Must be between 0 and 999. Default 360.
      pip_maximal_y_position: PIP maximal Y position [0.1 mm]. Must be between 0 and 9999.
                              Default 6065.
      left_arm_minimal_y_position: left arm minimal Y position [0.1 mm]. Must be between 0 and 9999.
                                    Default 60.
      right_arm_minimal_y_position: right arm minimal Y position [0.1 mm]. Must be between 0
                                    and 9999. Default 60.
    """

    utils.assert_clamp(instrument_size_in_slots_x_range, 10, 99,
      "instrument_size_in_slots_(x_range)")
    utils.assert_clamp(auto_load_size_in_slots, 10, 54, "auto_load_size_in_slots")
    utils.assert_clamp(tip_waste_x_position, 1000, 25000, "tip_waste_x_position")
    utils.assert_clamp(right_x_drive_configuration_byte_1, 0, 1,
      "right_x_drive_configuration_byte_1")
    utils.assert_clamp(right_x_drive_configuration_byte_2, 0, 1,
      "right_x_drive_configuration_byte_2")
    utils.assert_clamp(minimal_iswap_collision_free_position, 0, 30000, \
                  "minimal_iswap_collision_free_position")
    utils.assert_clamp(maximal_iswap_collision_free_position, 0, 30000, \
                  "maximal_iswap_collision_free_position")
    utils.assert_clamp(left_x_arm_width, 0, 9999, "left_x_arm_width")
    utils.assert_clamp(right_x_arm_width, 0, 9999, "right_x_arm_width")
    utils.assert_clamp(num_pip_channels, 0, 16, "num_pip_channels")
    utils.assert_clamp(num_xl_channels, 0, 8, "num_xl_channels")
    utils.assert_clamp(num_robotic_channels, 0, 8, "num_robotic_channels")
    utils.assert_clamp(minimal_raster_pitch_of_pip_channels, 0, 999, \
                  "minimal_raster_pitch_of_pip_channels")
    utils.assert_clamp(minimal_raster_pitch_of_xl_channels, 0, 999, \
                  "minimal_raster_pitch_of_xl_channels")
    utils.assert_clamp(minimal_raster_pitch_of_robotic_channels, 0, 999, \
                  "minimal_raster_pitch_of_robotic_channels")
    utils.assert_clamp(pip_maximal_y_position, 0, 9999, "pip_maximal_y_position")
    utils.assert_clamp(left_arm_minimal_y_position, 0, 9999, "left_arm_minimal_y_position")
    utils.assert_clamp(right_arm_minimal_y_position, 0, 9999, "right_arm_minimal_y_position")

    return self.send_command(
        module="C0",
        command="AK",
        kb=configuration_data_1,
        ka=configuration_data_2,
        ke=configuration_data_3,
        xt=instrument_size_in_slots_x_range,
        xa=auto_load_size_in_slots,
        xw=tip_waste_x_position,
        xr=right_x_drive_configuration_byte_1,
        xo=right_x_drive_configuration_byte_2,
        xm=minimal_iswap_collision_free_position,
        xx=maximal_iswap_collision_free_position,
        xu=left_x_arm_width,
        xv=right_x_arm_width,
        kp=num_pip_channels,
        kc=num_xl_channels,
        kr=num_robotic_channels,
        ys=minimal_raster_pitch_of_pip_channels,
        kl=minimal_raster_pitch_of_xl_channels,
        km=minimal_raster_pitch_of_robotic_channels,
        ym=pip_maximal_y_position,
        yu=left_arm_minimal_y_position,
        yx=right_arm_minimal_y_position,
      )

  def save_pip_channel_validation_status(
    self,
    validation_status: bool = False
  ):
    """ Save PIP channel validation status

    Args:
      validation_status: PIP channel validation status. Default False.
    """

    return self.send_command(
      module="C0",
      command="AJ",
      tq=validation_status,
    )

  def save_xl_channel_validation_status(
    self,
    validation_status: bool = False
  ):
    """ Save XL channel validation status

    Args:
      validation_status: XL channel validation status. Default False.
    """

    return self.send_command(
      module="C0",
      command="AE",
      tx=validation_status,
    )

  # TODO: response
  def configure_node_names(self):
    """ Configure node names """

    return self.send_command(module="C0", command="AJ")

  def set_deck_data(
    self,
    data_index: int = 0,
    data_stream: str = "0"
  ):
    """ set deck data

    Args:
      data_index: data index. Must be between 0 and 9. Default 0.
      data_stream: data stream (12 characters). Default <class 'str'>.
    """

    utils.assert_clamp(data_index, 0, 9, "data_index")
    assert len(data_stream) == 12, "data_stream must be 12 chars"

    return self.send_command(
      module="C0",
      command="DD",
      vi=data_index,
      vj=data_stream,
    )

  # -------------- 3.3.3 Settings query (stored in EEPROM) --------------

  def request_technical_status_of_assemblies(self):
    """ Request Technical status of assemblies """

    # TODO: parse res
    return self.send_command(module="C0", command="QT")

  def request_installation_data(self):
    """ Request installation data """

    # TODO: parse res
    return self.send_command(module="C0", command="RI")

  def request_download_date(self):
    """ Request download date """

    # TODO: parse res
    return self.send_command(module="C0", command="RO")

  def request_verification_data(
    self,
    verification_subject: int = 0
  ):
    """ Request download date

    Args:
      verification_subject: verification subject. Must be between 0 and 24. Default 0.
    """

    utils.assert_clamp(verification_subject, 0, 24, "verification_subject")

    # TODO: parse results.
    return self.send_command(
      module="C0",
      command="RO",
      vo = verification_subject
    )

  def request_additional_timestamp_data(self):
    """ Request additional timestamp data """

    # TODO: parse res
    return self.send_command(module="C0", command="RS")

  def request_pip_channel_validation_status(self):
    """ Request PIP channel validation status """

    # TODO: parse res
    return self.send_command(module="C0", command="RJ")

  def request_xl_channel_validation_status(self):
    """ Request XL channel validation status """

    # TODO: parse res
    return self.send_command(module="C0", command="UJ")

  def request_machine_configuration(self):
    """ Request machine configuration """

    # TODO: parse res
    return self.send_command(module="C0", command="RM")

  def request_extended_configuration(self):
    """ Request extended configuration """

    resp = self.send_command(module="C0", command="QM")
    return self.parse_response(resp, fmt="QMid####ka******ke********xt##xa##xw#####xl**" + \
            "xn**xr**xo**xm#####xx#####xu####xv####kc#kr#ys###kl###km###ym####yu####yx####")

  def request_node_names(self):
    """ Request node names """

    # TODO: parse res
    return self.send_command(module="C0", command="RK")

  def request_deck_data(self):
    """ Request deck data """

    # TODO: parse res
    return self.send_command(module="C0", command="VD")

  # -------------- 3.4 X-Axis control --------------

  # -------------- 3.4.1 Movements --------------

  def position_left_x_arm_(
    self,
    x_position: int = 0
  ):
    """ Position left X-Arm

    Collision risk!

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position_[0.1mm]")

    resp = self.send_command(
      module="C0",
      command="JX",
      xs=x_position,
    )
    return self.parse_response(resp, "")

  def position_right_x_arm_(
    self,
    x_position: int = 0
  ):
    """ Position right X-Arm

    Collision risk!

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position_[0.1mm]")

    resp = self.send_command(
      module="C0",
      command="JX",
      xs=x_position,
    )
    return self.parse_response(resp, "")

  def move_left_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self,
    x_position: int = 0
  ):
    """ Move left X-arm to position with all attached components in Z-safety position

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")

    resp = self.send_command(
      module="C0",
      command="KX",
      xs=x_position,
    )
    return self.parse_response(resp, "")

  def move_right_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self,
    x_position: int = 0
  ):
    """ Move right X-arm to position with all attached components in Z-safety position

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")

    resp = self.send_command(
      module="C0",
      command="KR",
      xs=x_position,
    )
    return self.parse_response(resp, "")

  # -------------- 3.4.2 X-Area reservation for external access --------------

  def occupy_and_provide_area_for_external_access(
    self,
    taken_area_identification_number: int = 0,
    taken_area_left_margin: int = 0,
    taken_area_left_margin_direction: int = 0,
    taken_area_size: int = 0,
    arm_preposition_mode_related_to_taken_areas: int = 0
  ):
    """ Occupy and provide area for external access

    Args:
      taken_area_identification_number: taken area identification number. Must be between 0 and
        9999. Default 0.
      taken_area_left_margin: taken area left margin. Must be between 0 and 99. Default 0.
      taken_area_left_margin_direction: taken area left margin direction. 1 = negative. Must be
        between 0 and 1. Default 0.
      taken_area_size: taken area size. Must be between 0 and 50000. Default 0.
      arm_preposition_mode_related_to_taken_areas: 0) left arm to left & right arm to right.
        1) all arms left.  2) all arms right.
    """

    utils.assert_clamp(taken_area_identification_number, 0, 9999, \
                  "taken_area_identification_number")
    utils.assert_clamp(taken_area_left_margin, 0, 99, "taken_area_left_margin")
    utils.assert_clamp(taken_area_left_margin_direction, 0, 1, "taken_area_left_margin_direction")
    utils.assert_clamp(taken_area_size, 0, 50000, "taken_area_size")
    utils.assert_clamp(arm_preposition_mode_related_to_taken_areas, 0, 2, \
                  "arm_preposition_mode_(related_to_taken_area)s")

    resp = self.send_command(
      module="C0",
      command="BA",
      aq=taken_area_identification_number,
      al=taken_area_left_margin,
      ad=taken_area_left_margin_direction,
      ar=taken_area_size,
      ap=arm_preposition_mode_related_to_taken_areas,
    )
    return self.parse_response(resp, "")

  def release_occupied_area(
    self,
    taken_area_identification_number: int = 0
  ):
    """ Release occupied area

    Args:
      taken_area_identification_number: taken area identification number.
                                        Must be between 0 and 9999. Default 0.
    """

    utils.assert_clamp(taken_area_identification_number, 0, 9999,
      "taken_area_identification_number")

    resp = self.send_command(
      module="C0",
      command="BB",
      aq=taken_area_identification_number,
    )
    return self.parse_response(resp, "")

  def release_all_occupied_areas(self):
    """ Release all occupied areas """

    resp = self.send_command(module="C0", command="BC")
    return resp

  # -------------- 3.4.3 X-query --------------

  def request_left_x_arm_position(self):
    """ Request left X-Arm position """

    resp = self.send_command(module="C0", command="RX")
    return self.parse_response(resp, "rx#####")

  def request_right_x_arm_position(self):
    """ Request right X-Arm position """

    resp = self.send_command(module="C0", command="QX")
    return self.parse_response(resp, "rx#####")

  def request_maximal_ranges_of_x_drives(self):
    """ Request maximal ranges of X drives """

    resp = self.send_command(module="C0", command="RU")
    return self.parse_response(resp, "")

  def request_present_wrap_size_of_installed_arms(self):
    """ Request present wrap size of installed arms """

    resp = self.send_command(module="C0", command="UA")
    return self.parse_response(resp, "")

  def request_left_x_arm_last_collision_type(self):
    """ Request left X-Arm last collision type (after error 27)

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    resp = self.send_command(module="C0", command="XX")
    parsed = self.parse_response(resp, "xq#")
    return parsed["xq"] == 1

  def request_right_x_arm_last_collision_type(self) -> bool:
    """ Request right X-Arm last collision type (after error 27)

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    resp = self.send_command(module="C0", command="XR")
    parsed = self.parse_response(resp, "xq#")
    return parsed["xq"] == 1

  # -------------- 3.5 Pipetting channel commands --------------

  # -------------- 3.5.1 Initialization --------------

  def initialize_pipetting_channels(
    self,
    x_positions: typing.List[int] = [0],
    y_positions: typing.List[int] = [0],
    begin_of_tip_deposit_process: int = 0,
    end_of_tip_deposit_process: int = 0,
    z_position_at_end_of_a_command: int = 3600,
    tip_pattern: typing.List[bool] = [True],
    tip_type: int = 16,
    discarding_method: int = 1
  ):
    """ Initialize pipetting channels

    Initialize pipetting channels (discard tips)

    Args:
      x_positions: X-Position [0.1mm] (discard position). Must be between 0 and 25000. Default 0.
      y_positions: y-Position [0.1mm] (discard position). Must be between 0 and 6500. Default 0.
      begin_of_tip_deposit_process: Begin of tip deposit process (Z-discard range) [0.1mm]. Must be
        between 0 and 3600. Default 0.
      end_of_tip_deposit_process: End of tip deposit process (Z-discard range) [0.1mm]. Must be
        between 0 and 3600. Default 0.
      z-position_at_end_of_a_command: Z-Position at end of a command [0.1mm]. Must be between 0 and
        3600. Default 3600.
      tip_pattern: Tip pattern ( channels involved). Default True.
      tip_type: Tip type (recommended is index of longest tip see command 'TT') [0.1mm]. Must be
        between 0 and 99. Default 16.
      discarding_method: discarding method. 0 = place & shift (tp/ tz = tip cone end height), 1 =
        drop (no shift) (tp/ tz = stop disk height). Must be between 0 and 1. Default 1.
    """

    utils.assert_clamp(x_positions, 0, 25000, "x_positions")
    utils.assert_clamp(y_positions, 0, 6500, "y_positions")
    utils.assert_clamp(begin_of_tip_deposit_process, 0, 3600, "begin_of_tip_deposit_process")
    utils.assert_clamp(end_of_tip_deposit_process, 0, 3600, "end_of_tip_deposit_process")
    utils.assert_clamp(z_position_at_end_of_a_command, 0, 3600, "z_position_at_end_of_a_command")
    utils.assert_clamp(tip_type, 0, 99, "tip_type")
    utils.assert_clamp(discarding_method, 0, 1, "discarding_method")

    return self.send_command(
      module="C0",
      command="DI",
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      tp=f"{begin_of_tip_deposit_process:04}",
      tz=f"{end_of_tip_deposit_process:04}",
      te=f"{z_position_at_end_of_a_command:04}",
      tm=[f"{tm:01}" for tm in tip_pattern],
      tt=f"{tip_type:02}",
      ti=discarding_method,
    )

  # -------------- 3.5.2 Tip handling commands using PIP --------------

  def pick_up_tip(
    self,
    x_positions: int = 0, # TODO: these are probably lists.
    y_positions: int = 0, # TODO: these are probably lists.
    tip_pattern: bool = True,
    tip_type: FirmwareTipType = FirmwareTipType.STANDARD_VOLUME,
    begin_tip_pick_up_process: int = 0,
    end_tip_pick_up_process: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    pick_up_method: int=0 #PickUpMethod = PickUpMethod.OUT_OF_RACK
  ):
    """ Tip Pick-up

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved).
      tip_type: Tip type.
      begin_tip_pick_up_process: Begin of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      end_tip_pick_up_process: End of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      pick_up_method: Pick up method.
    """

    utils.assert_clamp(x_positions, 0, 25000, "x_positions")
    utils.assert_clamp(y_positions, 0, 6500, "y_positions")
    utils.assert_clamp(begin_tip_pick_up_process, 0, 3600, "begin_tip_pick_up_process")
    utils.assert_clamp(end_tip_pick_up_process, 0, 3600, "end_tip_pick_up_process")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="TP",
      fmt="",
      timeout=60,
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tt=f"{tip_type:02}",
      tp=f"{begin_tip_pick_up_process:04}",
      tz=f"{end_tip_pick_up_process:04}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      td=pick_up_method,
    )

  class DiscardingMethod(enum.Enum):
    """ Tip discarding method """
    PLACE_SHIFT = 0
    DROP = 1

  def discard_tip(
    self,
    x_positions: int = 0, # TODO: these are probably lists.
    y_positions: int = 0, # TODO: these are probably lists.
    tip_pattern: bool = True,
    tip_type: FirmwareTipType = FirmwareTipType.STANDARD_VOLUME,
    begin_tip_deposit_process: int = 0,
    end_tip_deposit_process: int = None,
    minimum_traverse_height_at_beginning_of_a_command: int = None,
    discarding_method: DiscardingMethod = DiscardingMethod.DROP
  ):
    """ discard tip

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved). Must be between 0 and 1. Default 1.
      tip_type: Tip type. Must be between 0 and 99. Default 4.
      begin_tip_deposit_process: Begin of tip deposit process (Z- range) [0.1mm]. Must be between
          0 and 3600. Default 0.
      end_tip_deposit_process: End of tip deposit process (Z- range) [0.1mm]. Must be between 0
          and 3600.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
          command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must
          be between 0 and 3600.
      discarding_method: Pick up method Pick up method. 0 = auto selection (see command TT
          parameter tu) 1 = pick up out of rack. 2 = pick up out of wash liquid (slowly). Must be
          between 0 and 2.

    If discarding is PLACE_SHIFT (0), tp/ tz = tip cone end height.
    Otherwise, tp/ tz = stop disk height.
    """

    utils.assert_clamp(x_positions, 0, 25000, "x_positions")
    utils.assert_clamp(y_positions, 0, 6500, "y_positions")
    utils.assert_clamp(begin_tip_deposit_process, 0, 3600, "begin_tip_deposit_process")
    utils.assert_clamp(end_tip_deposit_process, 0, 3600, "end_tip_deposit_process")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="TR",
      fmt="kz### (n)vz### (n)",
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tt=f"{tip_type:02}",
      tp=begin_tip_deposit_process,
      tz=end_tip_deposit_process,
      th=minimum_traverse_height_at_beginning_of_a_command,
      ti=discarding_method,
    )

  # TODO:(command:TW) Tip Pick-up for DC wash procedure

  # -------------- 3.5.3 Liquid handling commands using PIP --------------

  # TODO:(command:DC) Set multiple dispense values using PIP

  def aspirate_pip(
    self,
    aspiration_type: typing.List[int] = [0],
    tip_pattern: typing.List[bool] = [True],
    x_positions: typing.List[int] = [0],
    y_positions: typing.List[int] = [0],
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600,
    lld_search_height: typing.List[int] = [0],
    clot_detection_height: typing.List[int] = [60],
    liquid_surface_no_lld: typing.List[int] = [3600],
    pull_out_distance_transport_air: typing.List[int] = [50],
    second_section_height: typing.List[int] = [0],
    second_section_ratio: typing.List[int] = [0],
    minimum_height: typing.List[int] = [3600],
    immersion_depth: typing.List[int] = [0],
    immersion_depth_direction: typing.List[int] = [0],
    surface_following_distance: typing.List[int] = [0],
    aspiration_volumes: typing.List[int] = [0],
    aspiration_speed: typing.List[int] = [500],
    transport_air_volume: typing.List[int] = [0],
    blow_out_air_volume: typing.List[int] = [200],
    pre_wetting_volume: typing.List[int] = [0],
    lld_mode: typing.List[int] = [1],
    gamma_lld_sensitivity: typing.List[int] = [1],
    dp_lld_sensitivity: typing.List[int] = [1],
    aspirate_position_above_z_touch_off: typing.List[int] = [5],
    detection_height_difference_for_dual_lld: typing.List[int] = [0],
    swap_speed: typing.List[int] = [100],
    settling_time: typing.List[int] = [5],
    homogenization_volume: typing.List[int] = [0],
    homogenization_cycles: typing.List[int] = [0],
    homogenization_position_from_liquid_surface: typing.List[int] = [250],
    homogenization_speed: typing.List[int] = [500],
    homogenization_surface_following_distance: typing.List[int] = [0],
    limit_curve_index: typing.List[int] = [0],
    tadm_algorithm: bool = False,
    recording_mode: int = 0,

    # For second section aspiration only
    use_2nd_section_aspiration: typing.List[bool] = [False],
    retract_height_over_2nd_section_to_empty_tip: typing.List[int] = [60],
    dispensation_speed_during_emptying_tip: typing.List[int] = [468],
    dosing_drive_speed_during_2nd_section_search: typing.List[int] = [468],
    z_drive_speed_during_2nd_section_search: typing.List[int] = [215],
    cup_upper_edge: typing.List[int] = [3600],
    ratio_liquid_rise_to_tip_deep_in: typing.List[int] = [16246],
    immersion_depth_2nd_section: typing.List[int] = [30]
  ):
    """ aspirate pip

    Aspiration of liquid using PIP.

    It's not really clear what second section aspiration is, but it does not seem to be used
    very often. Probably safe to ignore it.

    LLD restrictions!
      - "dP and Dual LLD" are used in aspiration only. During dispensation LLD is set to OFF.
      - "side touch off" turns LLD & "Z touch off" to OFF , is not available for simultaneous
        Asp/Disp. command

    Args:
      aspiration_type: Type of aspiration (0 = simple;1 = sequence; 2 = cup emptied).
                        Must be between 0 and 2. Default 0.
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      min_z_endpos: Minimum z-Position at end of a command [0.1 mm] (refers to all channels
          independent of tip pattern parameter 'tm'). Must be between 0 and 3600. Default 3600.
      lld_search_height: LLD search height [0.1 mm]. Must be between 0 and 3600. Default 0.
      clot_detection_height: Check height of clot detection above current surface (as computed)
          of the liquid [0.1mm]. Must be between 0 and 500. Default 60.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0
          and 3600. Default 3600.
      pull_out_distance_transport_air: pull out distance to take transport air in function
          without LLD [0.1mm]. Must be between 0 and 3600. Default 50.
      second_section_height: Tube 2nd section height measured from "zx" [0.1mm]. Must be
          between 0 and 3600. Default 0.
      second_section_ratio: Tube 2nd section ratio (see Fig. 2 in fw guide). Must be between
          0 and 10000. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1 mm]. Must be between 0 and
          3600. Default 3600.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out
          of liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Surface following distance during aspiration [0.1mm]. Must
          be between 0 and 3600. Default 0.
      aspiration_volumes: Aspiration volume [0.1ul]. Must be between 0 and 12500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 4 and 5000. Default 500.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 9999. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 999. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
            between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and
            4. Default 1.
      dp_lld_sensitivity: delta p LLD sensitivity (1= high, 4=low). Must be between 1 and
            4. Default 1.
      aspirate_position_above_z_touch_off: aspirate position above Z touch off [0.1mm]. Must
            be between 0 and 100. Default 5.
      detection_height_difference_for_dual_lld: Difference in detection height for dual
            LLD [0.1 mm]. Must be between 0 and 99. Default 0.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600.
            Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      homogenization_volume: Homogenization volume [0.1ul]. Must be between 0 and 12500. Default 0
      homogenization_cycles: Number of homogenization cycles. Must be between 0 and 99. Default 0.
      homogenization_position_from_liquid_surface: Homogenization position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 900. Default 250.
      homogenization_speed: Speed of homogenization [0.1ul/s]. Must be between 4 and 5000.
          Default 500.
      homogenization_surface_following_distance: Surface following distance during
          homogenization [0.1mm]. Must be between 0 and 3600. Default 0.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
      use_2nd_section_aspiration: 2nd section aspiration. Default False.
      retract_height_over_2nd_section_to_empty_tip: Retract height over 2nd section to empty
          tip [0.1mm]. Must be between 0 and 3600. Default 60.
      dispensation_speed_during_emptying_tip: Dispensation speed during emptying tip [0.1ul/s]
            Must be between 4 and 5000. Default 468.
      dosing_drive_speed_during_2nd_section_search: Dosing drive speed during 2nd section
          search [0.1ul/s]. Must be between 4 and 5000. Default 468.
      z_drive_speed_during_2nd_section_search: Z drive speed during 2nd section search [0.1mm/s].
          Must be between 3 and 1600. Default 215.
      cup_upper_edge: Cup upper edge [0.1mm]. Must be between 0 and 3600. Default 3600.
      ratio_liquid_rise_to_tip_deep_in: Ratio liquid rise to tip deep in [1/100000]. Must be
          between 0 and 50000. Default 16246.
      immersion_depth_2nd_section: Immersion depth 2nd section [0.1mm]. Must be between 0 and
          3600. Default 30.
    """

    utils.assert_clamp(aspiration_type, 0, 2, "aspiration_type")
    utils.assert_clamp(x_positions, 0, 25000, "x_positions")
    utils.assert_clamp(y_positions, 0, 6500, "y_positions")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(min_z_endpos, 0, 3600, "min_z_endpos")
    utils.assert_clamp(lld_search_height, 0, 3600, "lld_search_height")
    utils.assert_clamp(clot_detection_height, 0, 500, "clot_detection_height")
    utils.assert_clamp(liquid_surface_no_lld, 0, 3600, "liquid_surface_no_lld")
    utils.assert_clamp(pull_out_distance_transport_air, 0, 3600, "pull_out_distance_transport_air")
    utils.assert_clamp(second_section_height, 0, 3600, "second_section_height")
    utils.assert_clamp(second_section_ratio, 0, 10000, "second_section_ratio")
    utils.assert_clamp(minimum_height, 0, 3600, "minimum_height")
    utils.assert_clamp(immersion_depth, 0, 3600, "immersion_depth")
    utils.assert_clamp(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    utils.assert_clamp(surface_following_distance, 0, 3600, "surface_following_distance")
    utils.assert_clamp(aspiration_volumes, 0, 12500, "aspiration_volumes")
    utils.assert_clamp(aspiration_speed, 4, 5000, "aspiration_speed")
    utils.assert_clamp(transport_air_volume, 0, 500, "transport_air_volume")
    utils.assert_clamp(blow_out_air_volume, 0, 9999, "blow_out_air_volume")
    utils.assert_clamp(pre_wetting_volume, 0, 999, "pre_wetting_volume")
    utils.assert_clamp(lld_mode, 0, 4, "lld_mode")
    utils.assert_clamp(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    utils.assert_clamp(dp_lld_sensitivity, 1, 4, "dp_lld_sensitivity")
    utils.assert_clamp(aspirate_position_above_z_touch_off, 0, 100, \
                  "aspirate_position_above_z_touch_off")
    utils.assert_clamp(detection_height_difference_for_dual_lld, 0, 99, \
                  "detection_height_difference_for_dual_lld")
    utils.assert_clamp(swap_speed, 3, 1600, "swap_speed")
    utils.assert_clamp(settling_time, 0, 99, "settling_time")
    utils.assert_clamp(homogenization_volume, 0, 12500, "homogenization_volume")
    utils.assert_clamp(homogenization_cycles, 0, 99, "homogenization_cycles")
    utils.assert_clamp(homogenization_position_from_liquid_surface, 0, 900, \
                  "homogenization_position_from_liquid_surface")
    utils.assert_clamp(homogenization_speed, 4, 5000, "homogenization_speed")
    utils.assert_clamp(homogenization_surface_following_distance, 0, 3600, \
                  "homogenization_surface_following_distance")
    utils.assert_clamp(limit_curve_index, 0, 999, "limit_curve_index")
    utils.assert_clamp(recording_mode, 0, 2, "recording_mode")
    utils.assert_clamp(retract_height_over_2nd_section_to_empty_tip, 0, 3600, \
                  "retract_height_over_2nd_section_to_empty_tip")
    utils.assert_clamp(dispensation_speed_during_emptying_tip, 4, 5000, \
                  "dispensation_speed_during_emptying_tip")
    utils.assert_clamp(dosing_drive_speed_during_2nd_section_search, 4, 5000, \
                  "dosing_drive_speed_during_2nd_section_search")
    utils.assert_clamp(z_drive_speed_during_2nd_section_search, 3, 1600, \
                  "z_drive_speed_during_2nd_section_search")
    utils.assert_clamp(cup_upper_edge, 0, 3600, "cup_upper_edge")
    utils.assert_clamp(ratio_liquid_rise_to_tip_deep_in, 0, 50000,
      "ratio_liquid_rise_to_tip_deep_in")
    utils.assert_clamp(immersion_depth_2nd_section, 0, 3600, "immersion_depth_2nd_section")

    resp = self.send_command(
      module="C0",
      command="AS",
      fmt="",
      timeout=60,
      at=[f"{at:01}" for at in aspiration_type],
      tm=tip_pattern,
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{min_z_endpos:04}",
      lp=[f"{lp:04}" for lp in lld_search_height],
      ch=[f"{ch:03}" for ch in clot_detection_height],
      zl=[f"{zl:04}" for zl in liquid_surface_no_lld],
      po=[f"{po:04}" for po in pull_out_distance_transport_air],
      zu=[f"{zu:04}" for zu in second_section_height],
      zr=[f"{zr:05}" for zr in second_section_ratio],
      zx=[f"{zx:04}" for zx in minimum_height],
      ip=[f"{ip:04}" for ip in immersion_depth],
      it=[f"{it}"    for it in immersion_depth_direction],
      fp=[f"{fp:04}" for fp in surface_following_distance],
      av=[f"{av:05}" for av in aspiration_volumes],
      as_=[f"{as_:04}" for as_ in aspiration_speed],
      ta=[f"{ta:03}" for ta in transport_air_volume],
      ba=[f"{ba:04}" for ba in blow_out_air_volume],
      oa=[f"{oa:03}" for oa in pre_wetting_volume],
      lm=[f"{lm}"    for lm in lld_mode],
      ll=[f"{ll}"    for ll in gamma_lld_sensitivity],
      lv=[f"{lv}"    for lv in dp_lld_sensitivity],
      zo=[f"{zo:03}" for zo in aspirate_position_above_z_touch_off],
      ld=[f"{ld:02}" for ld in detection_height_difference_for_dual_lld],
      de=[f"{de:04}" for de in swap_speed],
      wt=[f"{wt:02}" for wt in settling_time],
      mv=[f"{mv:05}" for mv in homogenization_volume],
      mc=[f"{mc:02}" for mc in homogenization_cycles],
      mp=[f"{mp:03}" for mp in homogenization_position_from_liquid_surface],
      ms=[f"{ms:04}" for ms in homogenization_speed],
      mh=[f"{mh:04}" for mh in homogenization_surface_following_distance],
      gi=[f"{gi:03}" for gi in limit_curve_index],
      gj=tadm_algorithm,
      gk=recording_mode,

      lk=[1 if lk else 0 for lk in use_2nd_section_aspiration],
      ik=[f"{ik:04}" for ik in retract_height_over_2nd_section_to_empty_tip],
      sd=[f"{sd:04}" for sd in dispensation_speed_during_emptying_tip],
      se=[f"{se:04}" for se in dosing_drive_speed_during_2nd_section_search],
      sz=[f"{sz:04}" for sz in z_drive_speed_during_2nd_section_search],
      io=[f"{io:04}" for io in cup_upper_edge],
      il=[f"{il:05}" for il in ratio_liquid_rise_to_tip_deep_in],
      in_=[f"{in_:04}" for in_ in immersion_depth_2nd_section],
    )
    return resp

  def dispense_pip(
    self,
    dispensing_mode: typing.List[int] = [0],
    tip_pattern: typing.List[bool] = True,
    x_positions: typing.List[int] = [0],
    y_positions: typing.List[int] = [0],
    minimum_height: typing.List[int] = [3600],
    lld_search_height: typing.List[int] = [0],
    liquid_surface_no_lld: typing.List[int] = [3600],
    pull_out_distance_transport_air: typing.List[int] = [50],
    immersion_depth: typing.List[int] = [0],
    immersion_depth_direction: typing.List[int] = [0],
    surface_following_distance: typing.List[int] = [0],
    second_section_height: typing.List[int] = [0],
    second_section_ratio: typing.List[int] = [0],
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600, #
    dispense_volumes: typing.List[int] = [0],
    dispense_speed: typing.List[int] = [500],
    cut_off_speed: typing.List[int] = [250],
    stop_back_volume: typing.List[int] = [0],
    transport_air_volume: typing.List[int] = [0],
    blow_out_air_volume: typing.List[int] = [200],
    lld_mode: typing.List[int] = [1],
    side_touch_off_distance: int = 1,
    dispense_position_above_z_touch_off: typing.List[int] = [5],
    gamma_lld_sensitivity: typing.List[int] = [1],
    dp_lld_sensitivity: typing.List[int] = [1],
    swap_speed: typing.List[int] = [100],
    settling_time: typing.List[int] = [5],
    mix_volume: typing.List[int] = [0],
    mix_cycles: typing.List[int] = [0],
    mix_position_from_liquid_surface: typing.List[int] = [250],
    mix_speed: typing.List[int] = [500],
    mix_surface_following_distance: typing.List[int] = [0],
    limit_curve_index: typing.List[int] = [0],
    tadm_algorithm: bool = False,
    recording_mode: int = 0
  ):
    """ dispense pip

    Dispensing of liquid using PIP.

    LLD restrictions!
      - "dP and Dual LLD" are used in aspiration only. During dispensation LLD is set to OFF.
      - "side touch off" turns LLD & "Z touch off" to OFF , is not available for simultaneous
        Asp/Disp. command

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode
        1 = Blow out in jet mode 2 = Partial volume at surface
        3 = Blow out at surface 4 = Empty tip at fix position.
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1 mm]. Must be between 0 and
        3600. Default 3600.
      lld_search_height: LLD search height [0.1 mm]. Must be between 0 and 3600. Default 0.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0 and
        3600. Default 3600.
      pull_out_distance_transport_air: pull out distance to take transport air in function without
        LLD [0.1mm]. Must be between 0 and 3600. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
        liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Surface following distance during aspiration [0.1mm]. Must be
        between 0 and 3600. Default 0.
      second_section_height: Tube 2nd section height measured from "zx" [0.1mm]. Must be between
        0 and 3600. Default 0.
      second_section_ratio: Tube 2nd section ratio (see Fig. 2 in fw guide). Must be between 0 and
        10000. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 3600. Default 3600.
      min_z_endpos: Minimum z-Position at end of a command [0.1 mm] (refers to all channels
        independent of tip pattern parameter 'tm'). Must be between 0 and 3600.  Default 3600.
      dispense_volumes: Dispense volume [0.1ul]. Must be between 0 and 12500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 4 and 5000. Default 500.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 4 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul]. Must be between 0 and 180. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 9999. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between 0
        and 4. Default 1.
      side_touch_off_distance: side touch off distance [0.1 mm] (0 = OFF). Must be between 0 and 45.
        Default 1.
      dispense_position_above_z_touch_off: dispense position above Z touch off [0.1 s] (0 = OFF)
        Turns LLD & Z touch off to OFF if ON!. Must be between 0 and 100. Default 5.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
        Default 1.
      dp_lld_sensitivity: delta p LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
        Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600.
        Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: Mix volume [0.1ul]. Must be between 0 and 12500. Default 0.
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: Mix position in Z- direction from liquid surface (LLD or
        absolute terms) [0.1mm]. Must be between 0 and 900.  Default 250.
      mix_speed: Speed of mixing [0.1ul/s]. Must be between 4 and 5000. Default 500.
      mix_surface_following_distance: Surface following distance during mixing [0.1mm]. Must be
        between 0 and 3600. Default 0.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
        be between 0 and 2. Default 0.
    """

    utils.assert_clamp(dispensing_mode, 0, 4, "dispensing_mode")
    utils.assert_clamp(x_positions, 0, 25000, "x_positions")
    utils.assert_clamp(y_positions, 0, 6500, "y_positions")
    utils.assert_clamp(minimum_height, 0, 3600, "minimum_height")
    utils.assert_clamp(lld_search_height, 0, 3600, "lld_search_height")
    utils.assert_clamp(liquid_surface_no_lld, 0, 3600, "liquid_surface_no_lld")
    utils.assert_clamp(pull_out_distance_transport_air, 0, 3600, "pull_out_distance_transport_air")
    utils.assert_clamp(immersion_depth, 0, 3600, "immersion_depth")
    utils.assert_clamp(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    utils.assert_clamp(surface_following_distance, 0, 3600, "surface_following_distance")
    utils.assert_clamp(second_section_height, 0, 3600, "second_section_height")
    utils.assert_clamp(second_section_ratio, 0, 10000, "second_section_ratio")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(min_z_endpos, 0, 3600, "min_z_endpos")
    utils.assert_clamp(dispense_volumes, 0, 12500, "dispense_volume")
    utils.assert_clamp(dispense_speed, 4, 5000, "dispense_speed")
    utils.assert_clamp(cut_off_speed, 4, 5000, "cut_off_speed")
    utils.assert_clamp(stop_back_volume, 0, 180, "stop_back_volume")
    utils.assert_clamp(transport_air_volume, 0, 500, "transport_air_volume")
    utils.assert_clamp(blow_out_air_volume, 0, 9999, "blow_out_air_volume")
    utils.assert_clamp(lld_mode, 0, 4, "lld_mode")
    utils.assert_clamp(side_touch_off_distance, 0, 45, "side_touch_off_distance")
    utils.assert_clamp(dispense_position_above_z_touch_off, 0, 100, \
                  "dispense_position_above_z_touch_off")
    utils.assert_clamp(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    utils.assert_clamp(dp_lld_sensitivity, 1, 4, "dp_lld_sensitivity")
    utils.assert_clamp(swap_speed, 3, 1600, "swap_speed")
    utils.assert_clamp(settling_time, 0, 99, "settling_time")
    utils.assert_clamp(mix_volume, 0, 12500, "mix_volume")
    utils.assert_clamp(mix_cycles, 0, 99, "mix_cycles")
    utils.assert_clamp(mix_position_from_liquid_surface, 0, 900, "mix_position_from_liquid_surface")
    utils.assert_clamp(mix_speed, 4, 5000, "mix_speed")
    utils.assert_clamp(mix_surface_following_distance, 0, 3600, "mix_surface_following_distance")
    utils.assert_clamp(limit_curve_index, 0, 999, "limit_curve_index")
    utils.assert_clamp(recording_mode, 0, 2, "recording_mode")

    return self.send_command(
      module="C0",
      command="DS",
      timeout=60,
      fmt="",
      dm=[f"{dm:01}" for dm in dispensing_mode],
      tm=[f"{tm:01}" for tm in tip_pattern],
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      zx=[f"{zx:04}" for zx in minimum_height],
      lp=[f"{lp:04}" for lp in lld_search_height],
      zl=[f"{zl:04}" for zl in liquid_surface_no_lld],
      po=[f"{po:04}" for po in pull_out_distance_transport_air],
      ip=[f"{ip:04}" for ip in immersion_depth],
      it=[f"{it:01}" for it in immersion_depth_direction],
      fp=[f"{fp:04}" for fp in surface_following_distance],
      zu=[f"{zu:04}" for zu in second_section_height],
      zr=[f"{zr:05}" for zr in second_section_ratio],
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{min_z_endpos:04}",
      dv=[f"{dv:05}" for dv in dispense_volumes],
      ds=[f"{ds:04}" for ds in dispense_speed],
      ss=[f"{ss:04}" for ss in cut_off_speed],
      rv=[f"{rv:03}" for rv in stop_back_volume],
      ta=[f"{ta:03}" for ta in transport_air_volume],
      ba=[f"{ba:04}" for ba in blow_out_air_volume],
      lm=[f"{lm:01}" for lm in lld_mode],
      dj=f"{side_touch_off_distance:02}", #
      zo=[f"{zo:03}" for zo in dispense_position_above_z_touch_off],
      ll=[f"{ll:01}" for ll in gamma_lld_sensitivity],
      lv=[f"{lv:01}" for lv in dp_lld_sensitivity],
      de=[f"{de:04}" for de in swap_speed],
      wt=[f"{wt:02}" for wt in settling_time],
      mv=[f"{mv:05}" for mv in mix_volume],
      mc=[f"{mc:02}" for mc in mix_cycles],
      mp=[f"{mp:03}" for mp in mix_position_from_liquid_surface],
      ms=[f"{ms:04}" for ms in mix_speed],
      mh=[f"{mh:04}" for mh in mix_surface_following_distance],
      gi=[f"{gi:03}" for gi in limit_curve_index],
      gj=tadm_algorithm, #
      gk=recording_mode, #
    )

  # TODO:(command:DA) Simultaneous aspiration & dispensation of liquid

  # TODO:(command:DF) Dispense on fly using PIP (Partial volume in jet mode)

  # TODO:(command:LW) DC Wash procedure using PIP

  # -------------- 3.5.5 CoRe gripper commands --------------

  # TODO:(command) All CoRe gripper commands
  # TODO:(command:ZT)
  # TODO:(command:ZS)
  # TODO:(command:ZP)
  # TODO:(command:ZR)
  # TODO:(command:ZM)
  # TODO:(command:ZO)
  # TODO:(command:ZB)

  # -------------- 3.5.6 Adjustment & movement commands --------------

  # TODO:(command:JY) Position all pipetting channels in Y-direction

  # TODO:(command:JZ) Position all pipetting channels in Z-direction

  def position_single_pipetting_channel_in_y_direction(
    self,
    pipetting_channel_index: int = 1,
    y_position: int = 0
  ):
    """ Position single pipetting channel in Y-direction

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
      y_position: y position [0.1mm]. Must be between 0 and 6500. Default 0.
    """

    utils.assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")
    utils.assert_clamp(y_position, 0, 6500, "y_position")

    return self.send_command(
      module="C0",
      command="KY",
      pn=pipetting_channel_index,
      yj=y_position,
    )

  def position_single_pipetting_channel_in_z_direction(
    self,
    pipetting_channel_index: int = 1,
    z_position: int = 0
  ):
    """ Position single pipetting channel in Z-direction

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
      z_position: y position [0.1mm]. Must be between 0 and 6500. Default 0.
    """

    utils.assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")
    utils.assert_clamp(z_position, 0, 6500, "z_position")

    return self.send_command(
      module="C0",
      command="KZ",
      pn=pipetting_channel_index,
      zp=z_position,
    )

  # TODO:(command:XL) Search for Teach in signal using pipetting channel n in X-direction

  def spread_pip_channels(self):
    """ Spread PIP channels """

    return self.send_command(module="C0", command="JE", fmt="")

  def move_all_pipetting_channels_to_defined_position(
    self,
    tip_pattern: bool = True,
    x_positions: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_command: int = 3600,
    z_endpos: int = 0
  ):
    """ Move all pipetting channels to defined position

    Args:
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_traverse_height_at_beginning_of_command: Minimum traverse height at beginning of a
                                                       command 0.1mm] (refers to all channels
                                                       independent of tip pattern parameter 'tm').
                                                       Must be between 0 and 3600. Default 3600.
      z_endpos: Z-Position at end of a command [0.1 mm] (refers to all channels independent of tip
                pattern parameter 'tm'). Must be between 0 and 3600. Default 0.
    """

    utils.assert_clamp(x_positions, 0, 25000, "x_positions")
    utils.assert_clamp(y_positions, 0, 6500, "y_positions")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_command")
    utils.assert_clamp(z_endpos, 0, 3600, "z_endpos")

    return self.send_command(
      module="C0",
      command="JM",
      tm=tip_pattern,
      xp=x_positions,
      yp=y_positions,
      th=minimum_traverse_height_at_beginning_of_command,
      zp=z_endpos,
    )

  # TODO:(command:JR): teach rack using pipetting channel n

  def position_max_free_y_for_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Position all pipetting channels so that there is maximum free Y range for channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
    """

    utils.assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")

    return self.send_command(
      module="C0",
      command="KZ",
      pn=pipetting_channel_index,
    )

  def move_all_channels_in_z_safety(self):
    """ Move all pipetting channels in Z-safety position """

    resp = self.send_command(module="C0", command="ZA")
    return self.parse_response(resp, "")

  # -------------- 3.5.7 PIP query --------------

  # TODO:(command:RY): Request Y-Positions of all pipetting channels

  def request_y_pos_channel_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Request Y-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
    """

    utils.assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")

    resp = self.send_command(
      module="C0",
      command="RB",
      pn=pipetting_channel_index,
    )
    return self.parse_response(resp, "rb####")

  # TODO:(command:RZ): Request Z-Positions of all pipetting channels

  def request_z_pos_channel_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Request Z-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.

    Returns:
      Z-Position of channel n [0.1mm]. Taking into account tip presence and length.
    """

    utils.assert_clamp(pipetting_channel_index, 1, 16, "pipetting_channel_index")

    resp = self.send_command(
      module="C0",
      command="RD",
      pn=pipetting_channel_index,
    )
    return self.parse_response(resp, "rd####")

  def request_tip_presence(self):
    """ Request query tip presence on each channel

    Returns:
      0 = no tip, 1 = Tip in gripper (for each channel)
    """

    resp = self.send_command(module="C0", command="RT")
    return self.parse_response(resp, "rt# (n)")

  def request_pip_height_last_lld(self):
    """ Request PIP height of last LLD

    Returns:
      LLD height of all channels
    """

    resp = self.send_command(module="C0", command="RL")
    return self.parse_response(resp, "lh#### (n)")

  def request_tadm_status(self):
    """ Request PIP height of last LLD

    Returns:
      TADM channel status 0 = off, 1 = on
    """

    resp = self.send_command(module="C0", command="QS")
    return self.parse_response(resp, "qs# (n)")

  # TODO:(command:FS) Request PIP channel dispense on fly status
  # TODO:(command:VE) Request PIP channel 2nd section aspiration data

  # -------------- 3.6 XL channel commands --------------

  # TODO: all XL channel commands

  # -------------- 3.6.1 Initialization XL --------------

  # TODO:(command:LI)

  # -------------- 3.6.2 Tip handling commands using XL --------------

  # TODO:(command:LP)
  # TODO:(command:LR)

  # -------------- 3.6.3 Liquid handling commands using XL --------------

  # TODO:(command:LA)
  # TODO:(command:LD)
  # TODO:(command:LB)
  # TODO:(command:LC)

  # -------------- 3.6.4 Wash commands using XL channel --------------

  # TODO:(command:LE)
  # TODO:(command:LF)

  # -------------- 3.6.5 XL CoRe gripper commands --------------

  # TODO:(command:LT)
  # TODO:(command:LS)
  # TODO:(command:LU)
  # TODO:(command:LV)
  # TODO:(command:LM)
  # TODO:(command:LO)
  # TODO:(command:LG)

  # -------------- 3.6.6 Adjustment & movement commands CP --------------

  # TODO:(command:LY)
  # TODO:(command:LZ)
  # TODO:(command:LH)
  # TODO:(command:LJ)
  # TODO:(command:XM)
  # TODO:(command:LL)
  # TODO:(command:LQ)
  # TODO:(command:LK)
  # TODO:(command:UE)

  # -------------- 3.6.7 XL channel query --------------

  # TODO:(command:UY)
  # TODO:(command:UB)
  # TODO:(command:UZ)
  # TODO:(command:UD)
  # TODO:(command:UT)
  # TODO:(command:UL)
  # TODO:(command:US)
  # TODO:(command:UF)

  # -------------- 3.7 Tube gripper commands --------------

  # TODO: all tube gripper commands

  # -------------- 3.7.1 Movements --------------

  # TODO:(command:FC)
  # TODO:(command:FD)
  # TODO:(command:FO)
  # TODO:(command:FT)
  # TODO:(command:FU)
  # TODO:(command:FJ)
  # TODO:(command:FM)
  # TODO:(command:FW)

  # -------------- 3.7.2 Tube gripper query --------------

  # TODO:(command:FQ)
  # TODO:(command:FN)

  # -------------- 3.8 Imaging channel commands --------------

  # TODO: all imaging commands

  # -------------- 3.8.1 Movements --------------

  # TODO:(command:IC)
  # TODO:(command:ID)
  # TODO:(command:IM)
  # TODO:(command:IJ)

  # -------------- 3.8.2 Imaging channel query --------------

  # TODO:(command:IN)

  # -------------- 3.9 Robotic channel commands --------------

  # -------------- 3.9.1 Initialization --------------

  # TODO:(command:OI)

  # -------------- 3.9.2 Cap handling commands --------------

  # TODO:(command:OP)
  # TODO:(command:OQ)

  # -------------- 3.9.3 Adjustment & movement commands --------------

  # TODO:(command:OY)
  # TODO:(command:OZ)
  # TODO:(command:OH)
  # TODO:(command:OJ)
  # TODO:(command:OX)
  # TODO:(command:OM)
  # TODO:(command:OF)
  # TODO:(command:OG)

  # -------------- 3.9.4 Robotic channel query --------------

  # TODO:(command:OA)
  # TODO:(command:OB)
  # TODO:(command:OC)
  # TODO:(command:OD)
  # TODO:(command:OT)

  # -------------- 3.10 CoRe 96 Head commands --------------

  # -------------- 3.10.1 Initialization --------------

  def initialize_core_96_head(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 5743,
    z_deposit_position: int = 3425,
    z_position_at_the_command_end: int = 3425
  ):
    """ Initialize CoRe 96 Head

    Initialize CoRe 96 Head. Dependent to configuration initialization change.

    Args:
      x_position: X-Position [0.1mm] (discard position of tip A1). Must be between 0 and 30000.
        Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm] (discard position of tip A1 ). Must be between 1054 and 5743.
        Default 5743.
      z_deposit_position_[0.1mm]: Z- deposit position [0.1mm] (collar bearing position). Must be
        between 0 and 3425. Default 3425.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0 and
        3425. Default 3425.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 1054, 5743, "y_position")
    utils.assert_clamp(z_deposit_position, 0, 3425, "z_deposit_position")
    utils.assert_clamp(z_position_at_the_command_end, 0, 3425, "z_position_at_the_command_end")

    return self.send_command(
      module="C0",
      command="EI",
      xs=x_position,
      xd=x_direction,
      yh=y_position,
      za=z_deposit_position,
      ze=z_position_at_the_command_end,
    )

  def move_core_96_to_safe_position(self):
    """ Move CoRe 96 Head to Z save position """

    resp = self.send_command(module="C0", command="EV")
    return self.parse_response(resp, "")

  # -------------- 3.10.2 Tip handling using CoRe 96 Head --------------

  def pick_up_tips_core96(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 5600,
    tip_type: FirmwareTipType = FirmwareTipType.STANDARD_VOLUME,
    tip_pick_up_method: int = 2,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425
  ):
    """ Pick up tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_type: Tip type.
      tip_pick_up_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96 tip
                          wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
                          0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 1080, 5600, "y_position")
    utils.assert_clamp(z_deposit_position, 0, 3425, "z_deposit_position")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(minimum_height_command_end, 0, 3425, "minimum_height_command_end")

    return self.send_command(
      module="C0",
      command="EP",
      fmt="",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      tt=f"{tip_type:02}",
      wu=tip_pick_up_method,
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}",
    )

  def discard_tips_core96(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 5600,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425
  ):
    """ Discard tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_type: Tip type.
      tip_pick_up_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96
                          tip wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
                          0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 1080, 5600, "y_position")
    utils.assert_clamp(z_deposit_position, 0, 3425, "z_deposit_position")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(minimum_height_command_end, 0, 3425, "minimum_height_command_end")

    return self.send_command(
      module="C0",
      command="ER",
      fmt="",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}"
    )

  # -------------- 3.10.3 Liquid handling using CoRe 96 Head --------------

  def aspirate_core_96(
    self,
    aspiration_type: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimal_end_height: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    maximum_immersion_depth: int = 3425,
    tube_2nd_section_height_measured_from_zm: int = 0,
    tube_2nd_section_ratio: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_aspiration: int = 0,
    aspiration_volumes: int = 0,
    aspiration_speed: int = 1000,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    pre_wetting_volume: int = 0,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 5,
    homogenization_volume: int = 0,
    homogenization_cycles: int = 0,
    homogenization_position_from_liquid_surface: int = 250,
    surface_following_distance_during_homogenization: int = 0,
    speed_of_homogenization: int = 1000,
    channel_pattern: typing.List[bool] = [True] * 96,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0
  ):
    """ aspirate CoRe 96

    Aspiration of liquid using CoRe 96

    Args:
      aspiration_type: Type of aspiration (0 = simple; 1 = sequence; 2 = cup emptied). Must be
          between 0 and 2. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_positions: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3425. Default 3425.
      minimal_end_height: Minimal height at command end [0.1mm]. Must be between 0 and 3425.
          Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
          Must be between 0 and 3425. Default 3425.
      pull_out_distance_to_take_transport_air_in_function_without_lld: pull out distance to take
          transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      maximum_immersion_depth: Minimum height (maximum immersion depth) [0.1mm]. Must be between
          0 and 3425. Default 3425.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from "zm" [0.1mm]
           Must be between 0 and 3425. Default 0.
      tube_2nd_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000.
          Default 3425.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      liquid_surface_sink_distance_at_the_end_of_aspiration: Liquid surface sink distance at
          the end of aspiration [0.1mm]. Must be between 0 and 990. Default 0.
      aspiration_volumes: Aspiration volume [0.1ul]. Must be between 0 and 11500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 11500. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between
          0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      homogenization_volume: Homogenization volume [0.1ul]. Must be between 0 and 11500. Default 0.
      homogenization_cycles: Number of homogenization cycles. Must be between 0 and 99. Default 0.
      homogenization_position_from_liquid_surface: Homogenization position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      surface_following_distance_during_homogenization: surface following distance during
          homogenization [0.1mm]. Must be between 0 and 990. Default 0.
      speed_of_homogenization: Speed of homogenization [0.1ul/s]. Must be between 3 and 5000.
          Default 1000.
      todo: TODO: 24 hex chars. Must be between 4 and 5000.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement.
          Must be between 0 and 2. Default 0.
    """

    utils.assert_clamp(aspiration_type, 0, 2, "aspiration_type")
    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_positions, 1080, 5600, "y_positions")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(minimal_end_height, 0, 3425, "minimal_end_height")
    utils.assert_clamp(lld_search_height, 0, 3425, "lld_search_height")
    utils.assert_clamp(liquid_surface_at_function_without_lld, 0, 3425, \
                  "liquid_surface_at_function_without_lld")
    utils.assert_clamp(pull_out_distance_to_take_transport_air_in_function_without_lld, 0, 3425, \
                  "pull_out_distance_to_take_transport_air_in_function_without_lld")
    utils.assert_clamp(maximum_immersion_depth, 0, 3425, "maximum_immersion_depth")
    utils.assert_clamp(tube_2nd_section_height_measured_from_zm, 0, 3425, \
                  "tube_2nd_section_height_measured_from_zm")
    utils.assert_clamp(tube_2nd_section_ratio, 0, 10000, "tube_2nd_section_ratio")
    utils.assert_clamp(immersion_depth, 0, 3600, "immersion_depth")
    utils.assert_clamp(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    utils.assert_clamp(liquid_surface_sink_distance_at_the_end_of_aspiration, 0, 990, \
                  "liquid_surface_sink_distance_at_the_end_of_aspiration")
    utils.assert_clamp(aspiration_volumes, 0, 11500, "aspiration_volumes")
    utils.assert_clamp(aspiration_speed, 3, 5000, "aspiration_speed")
    utils.assert_clamp(transport_air_volume, 0, 500, "transport_air_volume")
    utils.assert_clamp(blow_out_air_volume, 0, 11500, "blow_out_air_volume")
    utils.assert_clamp(pre_wetting_volume, 0, 11500, "pre_wetting_volume")
    utils.assert_clamp(lld_mode, 0, 4, "lld_mode")
    utils.assert_clamp(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    utils.assert_clamp(swap_speed, 3, 1000, "swap_speed")
    utils.assert_clamp(settling_time, 0, 99, "settling_time")
    utils.assert_clamp(homogenization_volume, 0, 11500, "homogenization_volume")
    utils.assert_clamp(homogenization_cycles, 0, 99, "homogenization_cycles")
    utils.assert_clamp(homogenization_position_from_liquid_surface, 0, 990, \
                  "homogenization_position_from_liquid_surface")
    utils.assert_clamp(surface_following_distance_during_homogenization, 0, 990, \
                  "surface_following_distance_during_homogenization")
    utils.assert_clamp(speed_of_homogenization, 3, 5000, "speed_of_homogenization")
    utils.assert_clamp(limit_curve_index, 0, 999, "limit_curve_index")

    utils.assert_clamp(recording_mode, 0, 2, "recording_mode")

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern = ["1" if x else "0" for x in channel_pattern]
    channel_pattern = hex(int("".join(channel_pattern), 2)).upper()[2:]

    return self.send_command(
      module="C0",
      command="EA",
      fmt="",
      aa=aspiration_type,
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_positions:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimal_end_height:04}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_at_function_without_lld:04}",
      pp=f"{pull_out_distance_to_take_transport_air_in_function_without_lld:04}",
      zm=f"{maximum_immersion_depth:04}",
      zv=f"{tube_2nd_section_height_measured_from_zm:04}",
      zq=f"{tube_2nd_section_ratio:05}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{liquid_surface_sink_distance_at_the_end_of_aspiration:03}",
      af=f"{aspiration_volumes:05}",
      ag=f"{aspiration_speed:04}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      wv=f"{pre_wetting_volume:05}",
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{homogenization_volume:05}",
      hc=f"{homogenization_cycles:02}",
      hp=f"{homogenization_position_from_liquid_surface:03}",
      mj=f"{surface_following_distance_during_homogenization:03}",
      hs=f"{speed_of_homogenization:04}",
      cw=channel_pattern,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  def dispense_core_96(
    self,
    dispensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    tube_2nd_section_height_measured_from_zm: int = 0,
    tube_2nd_section_ratio: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    maximum_immersion_depth: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_dispense: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimal_end_height: int = 3425,
    dispense_volume: int = 0,
    dispense_speed: int = 5000,
    cut_off_speed: int = 250,
    stop_back_volume: int = 0,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    side_touch_off_distance: int = 0,
    swap_speed: int = 100,
    settling_time: int = 5,
    mixing_volume: int = 0,
    mixing_cycles: int = 0,
    mixing_position_from_liquid_surface: int = 250,
    surface_following_distance_during_mixing: int = 0,
    speed_of_mixing: int = 1000,
    channel_pattern: typing.List[bool] = [[True]*12]*8,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0
  ):
    """ dispense CoRe 96

    Dispensing of liquid using CoRe 96

    Args:
      dispensing_mode: Type of dispsensing mode 0 = Partial volume in jet mode 1 = Blow out
          in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix
          position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      maximum_immersion_depth: Minimum height (maximum immersion depth) [0.1mm]. Must be between
          0 and 3425. Default 3425.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from
          "zm" [0.1mm]. Must be between 0 and 3425. Default 0.
      tube_2nd_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000.
          Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
          Must be between 0 and 3425. Default 3425.
      pull_out_distance_to_take_transport_air_in_function_without_lld: pull out distance to take
          transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      liquid_surface_sink_distance_at_the_end_of_dispense: Liquid surface sink elevation at
          the end of aspiration [0.1mm]. Must be between 0 and 990. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimal traverse height at begin of
          command [0.1mm]. Must be between 0 and 3425. Default 3425.
      minimal_end_height: Minimal height at command end [0.1mm]. Must be between 0 and 3425.
          Default 3425.
      dispense_volume: Dispense volume [0.1ul]. Must be between 0 and 11500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 3 and 5000. Default 5000.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 3 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul/s]. Must be between 0 and 999. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
          between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      side_touch_off_distance: side touch off distance [0.1 mm] 0 = OFF ( > 0 = ON & turns LLD off)
        Must be between 0 and 45. Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mixing_volume: Homogenization volume [0.1ul]. Must be between 0 and 11500. Default 0.
      mixing_cycles: Number of mixing cycles. Must be between 0 and 99. Default 0.
      mixing_position_from_liquid_surface: Homogenization position in Z- direction from liquid
          surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      surface_following_distance_during_mixing: surface following distance during mixing [0.1mm].
          Must be between 0 and 990. Default 0.
      speed_of_mixing: Speed of mixing [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      channel_pattern: list of 96 boolean values
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
    """

    utils.assert_clamp(dispensing_mode, 0, 4, "dispensing_mode")
    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 1080, 5600, "y_position")
    utils.assert_clamp(maximum_immersion_depth, 0, 3425, "maximum_immersion_depth")
    utils.assert_clamp(tube_2nd_section_height_measured_from_zm, 0, 3425, \
                  "tube_2nd_section_height_measured_from_zm")
    utils.assert_clamp(tube_2nd_section_ratio, 0, 10000, "tube_2nd_section_ratio")
    utils.assert_clamp(lld_search_height, 0, 3425, "lld_search_height")
    utils.assert_clamp(liquid_surface_at_function_without_lld, 0, 3425, \
                  "liquid_surface_at_function_without_lld")
    utils.assert_clamp(pull_out_distance_to_take_transport_air_in_function_without_lld, 0, 3425, \
                  "pull_out_distance_to_take_transport_air_in_function_without_lld")
    utils.assert_clamp(immersion_depth, 0, 3600, "immersion_depth")
    utils.assert_clamp(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    utils.assert_clamp(liquid_surface_sink_distance_at_the_end_of_dispense, 0, 990, \
                  "liquid_surface_sink_distance_at_the_end_of_dispense")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(minimal_end_height, 0, 3425, "minimal_end_height")
    utils.assert_clamp(dispense_volume, 0, 11500, "dispense_volume")
    utils.assert_clamp(dispense_speed, 3, 5000, "dispense_speed")
    utils.assert_clamp(cut_off_speed, 3, 5000, "cut_off_speed")
    utils.assert_clamp(stop_back_volume, 0, 999, "stop_back_volume")
    utils.assert_clamp(transport_air_volume, 0, 500, "transport_air_volume")
    utils.assert_clamp(blow_out_air_volume, 0, 11500, "blow_out_air_volume")
    utils.assert_clamp(lld_mode, 0, 4, "lld_mode")
    utils.assert_clamp(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    utils.assert_clamp(side_touch_off_distance, 0, 45, "side_touch_off_distance")
    utils.assert_clamp(swap_speed, 3, 1000, "swap_speed")
    utils.assert_clamp(settling_time, 0, 99, "settling_time")
    utils.assert_clamp(mixing_volume, 0, 11500, "mixing_volume")
    utils.assert_clamp(mixing_cycles, 0, 99, "mixing_cycles")
    utils.assert_clamp(mixing_position_from_liquid_surface, 0, 990, \
                  "mixing_position_from_liquid_surface")
    utils.assert_clamp(surface_following_distance_during_mixing, 0, 990, \
                  "surface_following_distance_during_mixing")
    utils.assert_clamp(speed_of_mixing, 3, 5000, "speed_of_mixing")
    utils.assert_clamp(limit_curve_index, 0, 999, "limit_curve_index")
    utils.assert_clamp(recording_mode, 0, 2, "recording_mode")

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern = ["1" if x else "0" for x in channel_pattern]
    channel_pattern = hex(int("".join(channel_pattern), 2)).upper()[2:]

    return self.send_command(
      module="C0",
      command="ED",
      fmt="",
      da=dispensing_mode,
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      zm=f"{maximum_immersion_depth:04}",
      zv=f"{tube_2nd_section_height_measured_from_zm:04}",
      zq=f"{tube_2nd_section_ratio:05}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_at_function_without_lld:04}",
      pp=f"{pull_out_distance_to_take_transport_air_in_function_without_lld:04}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{liquid_surface_sink_distance_at_the_end_of_dispense:03}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimal_end_height:04}",
      df=f"{dispense_volume:05}",
      dg=f"{dispense_speed:04}",
      es=f"{cut_off_speed:04}",
      ev=f"{stop_back_volume:03}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      ej=f"{side_touch_off_distance:02}",
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mixing_volume:05}",
      hc=f"{mixing_cycles:02}",
      hp=f"{mixing_position_from_liquid_surface:03}",
      mj=f"{surface_following_distance_during_mixing:03}",
      hs=f"{speed_of_mixing:04}",
      cw=channel_pattern,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  # -------------- 3.10.4 Adjustment & movement commands --------------

  def move_core_96_head_to_defined_position(
    self,
    dispsensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    z_position: int = 0,
    minimum_height_at_beginning_of_a_command: int = 3425
  ):
    """ Move CoRe 96 Head to defined position

    Args:
      dispsensing_mode: Type of dispsensing mode 0 = Partial volume in jet mode 1 = Blow out
                        in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty
                        tip at fix position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm]. Must be between 1080 and 5600. Default 0.
      z_position: Z-Position [0.1mm]. Must be between 0 and 5600. Default 0.
      minimum_height_at_beginning_of_a_command: Minimum height at beginning of a command 0.1mm]
                        (refers to all channels independent of tip pattern parameter 'tm'). Must be
                        between 0 and 3425. Default 3425.
    """

    utils.assert_clamp(dispsensing_mode, 0, 4, "dispsensing_mode")
    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 1080, 5600, "y_position")
    utils.assert_clamp(y_position, 0, 5600, "z_position")
    utils.assert_clamp(minimum_height_at_beginning_of_a_command, 0, 3425, \
                  "minimum_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="EM",
      dm=dispsensing_mode,
      xs=x_position,
      xd=x_direction,
      yh=y_position,
      za=z_position,
      zh=minimum_height_at_beginning_of_a_command,
    )

  # -------------- 3.10.5 Wash procedure commands using CoRe 96 Head --------------

  # TODO:(command:EG) Washing tips using CoRe 96 Head
  # TODO:(command:EU) Empty washed tips (end of wash procedure only)

  # -------------- 3.10.6 Query CoRe 96 Head --------------

  def request_tip_presence_in_core_96_head(self):
    """ Request Tip presence in CoRe 96 Head

    Returns:
      qh: 0 = no tips, 1 = Tips are picked up
    """

    resp = self.send_command(module="C0", command="QH")
    return self.parse_response(resp, "qh#")

  def request_position_of_core_96_head(self):
    """ Request position of CoRe 96 Head (A1 considered to tip length)

    Returns:
      xs: A1 X direction [0.1mm]
      xd: X direction 0 = positive 1 = negative
      yh: A1 Y direction [0.1mm]
      za: Z height [0.1mm]
    """

    resp = self.send_command(module="C0", command="QI")
    return self.parse_response(resp, "xs#####xd#hy####za####")

  def request_core_96_head_channel_tadm_status(self):
    """ Request CoRe 96 Head channel TADM Status

    Returns:
      qx: TADM channel status 0 = off 1 = on
    """

    resp = self.send_command(module="C0", command="VC")
    return self.parse_response(resp, "qx#")

  def request_core_96_head_channel_tadm_error_status(self):
    """ Request CoRe 96 Head channel TADM error status

    Returns:
      vb: error pattern 0 = no error
    """

    resp = self.send_command(module="C0", command="VB")
    return self.parse_response(resp, "vb" + "&" * 24)

  # -------------- 3.11 384 Head commands --------------

  # -------------- 3.11.1 Initialization --------------

  # -------------- 3.11.2 Tip handling using 384 Head --------------

  # -------------- 3.11.3 Liquid handling using 384 Head --------------

  # -------------- 3.11.4 Adjustment & movement commands --------------

  # -------------- 3.11.5 Wash procedure commands using 384 Head --------------

  # -------------- 3.11.6 Query 384 Head --------------

  # -------------- 3.12 Nano pipettor commands --------------

  # TODO: all nano pipettor commands

  # -------------- 3.12.1 Initialization --------------

  # TODO:(commandL:NI)
  # TODO:(commandL:NV)
  # TODO:(commandL:NP)

  # -------------- 3.12.2 Nano pipettor liquid handling commands --------------

  # TODO:(commandL:NA)
  # TODO:(commandL:ND)
  # TODO:(commandL:NF)

  # -------------- 3.12.3 Nano pipettor wash & clean commands --------------

  # TODO:(commandL:NW)
  # TODO:(commandL:NU)

  # -------------- 3.12.4 Nano pipettor adjustment & movements --------------

  # TODO:(commandL:NM)
  # TODO:(commandL:NT)

  # -------------- 3.12.5 Nano pipettor query --------------

  # TODO:(commandL:QL)
  # TODO:(commandL:QN)
  # TODO:(commandL:RN)
  # TODO:(commandL:QQ)
  # TODO:(commandL:QR)
  # TODO:(commandL:QO)
  # TODO:(commandL:RR)
  # TODO:(commandL:QU)

  # -------------- 3.13 Auto load commands --------------

  # -------------- 3.13.1 Initialization --------------

  def initialize_auto_load(self):
    """ Initialize Auto load module """

    resp = self.send_command(module="C0", command="II")
    return self.parse_response(resp, "")

  def move_auto_load_to_z_save_position(self):
    """ Move auto load to Z save position """

    resp = self.send_command(module="C0", command="IV")
    return self.parse_response(resp, "")

  # -------------- 3.13.2 Carrier handling --------------

  # TODO:(command:CI) Identify carrier (determine carrier type)

  def request_single_carrier_presence(
    self,
    carrier_position: int
  ):
    """ Request single carrier presence

    Args:
      carrier_position: Carrier position (slot number)

    Returns:
      True if present, False otherwise
    """

    utils.assert_clamp(carrier_position, 1, 54, "carrier_position")

    resp = self.send_command(
      module="C0",
      command="CT",
      cp=carrier_position
    )
    return self.parse_response(resp, "ct#")["ct"] == 1

  # TODO:(command:CA) Push out carrier to loading tray (after identification CI)

  # TODO:(command:CR) Unload carrier

  # TODO:(command:CL) Load carrier

  def set_loading_indicators(
    self,
    bit_pattern: typing.List[bool],
    blink_pattern: typing.List[bool]
  ):
    """ Set loading indicators (LEDs)

    The docs here are a little weird because 2^54 < 7FFFFFFFFFFFFF.

    Args:
      bit_pattern: On if True, off otherwise
      blink_pattern: Blinking if True, steady otherwise
    """

    assert len(bit_pattern) == 54, "bit pattern must be length 54"
    assert len(blink_pattern) == 54, "bit pattern must be length 54"

    bit_pattern   = hex(int("".join(["1" if x else "0" for x in bit_pattern]), base=2))
    blink_pattern = hex(int("".join(["1" if x else "0" for x in blink_pattern]), base=2))

    resp = self.send_command(
      module="C0",
      command="CP",
      cl=bit_pattern,
      cb=blink_pattern
    )
    return self.parse_response(resp, "")

  # TODO:(command:CS) Check for presence of carriers on loading tray

  def set_barcode_type(
    self,
    ISBT_Standard: bool = True,
    code128: bool = True,
    code39: bool = True,
    codebar: bool = True,
    code2_5: bool = True,
    UPC_AE: bool = True,
    EAN8: bool = True
  ):
    """ Set bar code type: which types of barcodes will be scanned for.

    Args:
      ISBT_Standard: ISBT_Standard. Default True.
      code128: Code128. Default True.
      code39: Code39. Default True.
      codebar: Codebar. Default True.
      code2_5: Code2_5. Default True.
      UPC_AE: UPC_AE. Default True.
      EAN8: EAN8. Default True.
    """

    # pylint: disable=invalid-name

    # Encode values into bit pattern. Last bit is always one.
    bt = ""
    for t in [ISBT_Standard, code128, code39, codebar, code2_5, UPC_AE, EAN8, True]:
      bt += "1" if t else "0"

    # Convert bit pattern to hex.
    bt = hex(int(bt), base=2)

    resp = self.send_command(
      module="C0",
      command="CB",
      bt=bt
    )
    return self.parse_response(resp, "")

  # TODO:(command:CW) Unload carrier finally

  def set_carrier_monitoring(
    self,
    should_monitor: bool = False
  ):
    """ Set carrier monitoring

    Args:
      should_monitor: whether carrier should be monitored.

    Returns:
      True if present, False otherwise
    """

    resp = self.send_command(
      module="C0",
      command="CU",
      cu=should_monitor
    )
    return self.parse_response(resp, "")

  # TODO:(command:CN) Take out the carrier to identification position

  # -------------- 3.13.3 Auto load query --------------

  # TODO:(command:RC) Query presence of carrier on deck

  def request_auto_load_slot_position(self):
    """ Request auto load slot position.

    Returns:
      slot position (0..54)
    """

    resp = self.send_command(module="C0", command="QA")
    return self.parse_response(resp, "qa##")

  # TODO:(command:CQ) Request auto load module type

  # -------------- 3.14 G1-3/ CR Needle Washer commands --------------

  # TODO: All needle washer commands

  # TODO:(command:WI)
  # TODO:(command:WI)
  # TODO:(command:WS)
  # TODO:(command:WW)
  # TODO:(command:WR)
  # TODO:(command:WC)
  # TODO:(command:QF)

  # -------------- 3.15 Pump unit commands --------------

  def request_pump_settings(
    self,
    pump_station: int = 1
  ):
    """ Set carrier monitoring

    Args:
      carrier_position: pump station number (1..3)

    Returns:
      0 = CoRe 96 wash station (single chamber)
      1 = DC wash station (single chamber rev 02 ) 2 = ReReRe (single chamber)
      3 = CoRe 96 wash station (dual chamber)
      4 = DC wash station (dual chamber)
      5 = ReReRe (dual chamber)
    """

    utils.assert_clamp(pump_station, 1, 3, "pump_station")

    resp = self.send_command(
      module="C0",
      command="ET",
      ep=pump_station
    )
    return self.parse_response(resp, "et#")

  # -------------- 3.15.1 DC Wash commands (only for revision up to 01) --------------

  # TODO:(command:FA) Start DC wash procedure
  # TODO:(command:FB) Stop DC wash procedure
  # TODO:(command:FP) Prime DC wash station

  # -------------- 3.15.2 Single chamber pump unit only --------------

  # TODO:(command:EW) Start circulation (single chamber only)
  # TODO:(command:EC) Check circulation (single chamber only)
  # TODO:(command:ES) Stop circulation (single chamber only)
  # TODO:(command:EF) Prime (single chamber only)
  # TODO:(command:EE) Drain & refill (single chamber only)
  # TODO:(command:EB) Fill (single chamber only)
  # TODO:(command:QE) Request single chamber pump station prime status

  # -------------- 3.15.3 Dual chamber pump unit only --------------

  def initialize_dual_pump_station_valves(
    self,
    pump_station: int = 1
  ):
    """ Initialize pump station valves (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
    """

    utils.assert_clamp(pump_station, 1, 3, "pump_station")

    resp = self.send_command(
      module="C0",
      command="EJ",
      ep=pump_station
    )
    return self.parse_response(resp, "")

  def fill_selected_dual_chamber(
    self,
    pump_station: int = 1,
    drain_before_refill: bool = False,
    wash_fluid: int = 1,
    chamber: int = 2,
    waste_chamber_suck_time_after_sensor_change: int = 0
  ):
    """ Initialize pump station valves (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
      drain_before_refill: drain chamber before refill. Default False.
      wash_fluid: wash fluid (1 or 2)
      chamber: chamber (1 or 2)
      drain_before_refill: waste chamber suck time after sensor change [s] (for error handling only)
    """

    utils.assert_clamp(pump_station, 1, 3, "pump_station")
    utils.assert_clamp(wash_fluid, 1, 2, "wash_fluid")
    utils.assert_clamp(chamber, 1, 2, "chamber")

    # wash fluid <-> chamber connection
    # 0 = wash fluid 1 <-> chamber 2
    # 1 = wash fluid 1 <-> chamber 1
    # 2 = wash fluid 2 <-> chamber 1
    # 3 = wash fluid 2 <-> chamber 2
    connection = {
      (1, 2): 0,
      (1, 1): 1,
      (2, 1): 2,
      (2, 2): 3
    }[wash_fluid, chamber]

    return self.send_command(
      module="C0",
      command="EH",
      fmt="",
      ep=pump_station,
      ed=drain_before_refill,
      ek=connection,
      eu=f"{waste_chamber_suck_time_after_sensor_change:02}",
      wait=False
    )

  # TODO:(command:EK) Drain selected chamber

  def drain_dual_chamber_system(
    self,
    pump_station: int = 1
  ):
    """ Drain system (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
    """

    utils.assert_clamp(pump_station, 1, 3, "pump_station")

    return self.send_command(
      module="C0",
      command="EL",
      fmt="",
      ep=pump_station
    )

  # TODO:(command:QD) Request dual chamber pump station prime status

  # -------------- 3.16 Incubator commands --------------

  # TODO: all incubator commands
  # TODO:(command:HC)
  # TODO:(command:HI)
  # TODO:(command:HF)
  # TODO:(command:RP)

  # -------------- 3.17 iSWAP commands --------------

  # -------------- 3.17.1 Pre & Initialization commands --------------

  def initialize_iswap(self):
    """ Initialize iSWAP (for standalone configuration only) """

    resp = self.send_command(module="C0", command="FI")
    return self.parse_response(resp, "")

  def position_components_for_free_iswap_y_range(self):
    """ Position all components so that there is maximum free Y range for iSWAP """

    resp = self.send_command(module="C0", command="FY")
    return self.parse_response(resp, "")

  def move_iswap_x_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in X-direction

    Args:
      step_size: X Step size [0.1mm] Between 0 and 999. Default 0.
      direction: X direction. 0 = positive 1 = negative
    """

    resp = self.send_command(
      module="C0",
      command="GX",
      gx=step_size,
      xd=direction
    )
    return self.parse_response(resp, "")

  def move_iswap_y_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in Y-direction

    Args:
      step_size: Y Step size [0.1mm] Between 0 and 999. Default 0.
      direction: Y direction. 0 = positive 1 = negative
    """

    resp = self.send_command(
      module="C0",
      command="GY",
      gx=step_size,
      xd=direction
    )
    return self.parse_response(resp, "")

  def move_iswap_z_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in Z-direction

    Args:
      step_size: Z Step size [0.1mm] Between 0 and 999. Default 0.
      direction: Z direction. 0 = positive 1 = negative
    """

    resp = self.send_command(
      module="C0",
      command="GZ",
      gx=step_size,
      xd=direction
    )
    return self.parse_response(resp, "")

  def open_not_initialized_gripper(self):
    """ Open not initialized gripper """

    resp = self.send_command(module="C0", command="GI")
    return self.parse_response(resp, "")

  def open_gripper(
    self,
    open_position: int = 1320
  ):
    """ Open gripper

    Args:
      open_position: Open position [0.1mm] (0.1 mm = 16 increments) The gripper moves to pos + 20.
                     Must be between 0 and 9999. Default 860.
    """

    utils.assert_clamp(open_position, 0, 9999, "open_position")

    resp = self.send_command(
      module="C0",
      command="GF",
      go=f"{open_position:04}"
    )
    return self.parse_response(resp, "")

  def close_gripper(
    self,
    grip_strength: int = 5,
    plate_width: int = 0,
    plate_width_tolerance: int = 0
  ):
    """ Close gripper

    The gripper should be at the position gb+gt+20 before sending this command.

    Args:
      grip_strength: Grip strength. 0 = low . 9 = high. Default 5.
      plate_width: Plate width [0.1mm]
                   (gb should be > min. Pos. + stop ramp + gt -> gb > 760 + 5 + g )
      plate_width_tolerance: Plate width tolerance [0.1mm]. Must be between 0 and 99. Default 20.
    """

    resp = self.send_command(
      module="C0",
      command="GC",
      gw=grip_strength,
      gb=plate_width,
      gt=plate_width_tolerance
    )
    return self.parse_response(resp, "")

  # -------------- 3.17.2 Stack handling commands CP --------------

  def park_iswap(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 2840
  ):
    """ Close gripper

    The gripper should be at the position gb+gt+20 before sending this command.

    Args:
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
                of a command [0.1mm]. Must be between 0 and 3600. Default 3600.
    """

    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="PG",
      fmt="",
      th=minimum_traverse_height_at_beginning_of_a_command
    )

  def get_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_the_command_end: int = 3600,
    grip_strength: int = 5,
    open_gripper_position: int = 860,
    plate_width: int = 860,
    plate_width_tolerance: int = 860,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
    fold_up_sequence_at_the_end_of_process: bool = True
  ):
    """ Get plate using iswap.

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y,
            4 =negative X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
            a command 0.1mm]. Must be between 0 and 3600. Default 3600.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0
            and 3600. Default 3600.
      grip_strength: Grip strength 0 = low .. 9 = high. Must be between 1 and 9. Default 5.
      open_gripper_position: Open gripper position [0.1mm]. Must be between 0 and 9999.
            Default 860.
      plate_width: plate width [0.1mm]. Must be between 0 and 9999. Default 860.
      plate_width_tolerance: plate width tolerance [0.1mm]. Must be between 0 and 99. Default 860.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4. Default 1.
      fold_up_sequence_at_the_end_of_process: fold up sequence at the end of process. Default True.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 0, 6500, "y_position")
    utils.assert_clamp(y_direction, 0, 1, "y_direction")
    utils.assert_clamp(z_position, 0, 3600, "z_position")
    utils.assert_clamp(z_direction, 0, 1, "z_direction")
    utils.assert_clamp(grip_direction, 1, 4, "grip_direction")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(z_position_at_the_command_end, 0, 3600, "z_position_at_the_command_end")
    utils.assert_clamp(grip_strength, 1, 9, "grip_strength")
    utils.assert_clamp(open_gripper_position, 0, 9999, "open_gripper_position")
    utils.assert_clamp(plate_width, 0, 9999, "plate_width")
    utils.assert_clamp(plate_width_tolerance, 0, 99, "plate_width_tolerance")
    utils.assert_clamp(collision_control_level, 0, 1, "collision_control_level")
    utils.assert_clamp(acceleration_index_high_acc, 0, 4, "acceleration_index_high_acc")
    utils.assert_clamp(acceleration_index_low_acc, 0, 4, "acceleration_index_low_acc")

    return self.send_command(
      module="C0",
      command="PP",
      fmt="",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      gr=grip_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{z_position_at_the_command_end:04}",
      gw=grip_strength,
      go=f"{open_gripper_position:04}",
      gb=f"{plate_width:04}",
      gt=f"{plate_width_tolerance:02}",
      ga=collision_control_level,
      # xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}",
      gc=fold_up_sequence_at_the_end_of_process,
    )

  def put_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_the_command_end: int = 3600,
    open_gripper_position: int = 860,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1
  ):
    """ put plate

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y, 4 = negative
            X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
            command 0.1mm]. Must be between 0 and 3600. Default 3600.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0 and
            3600. Default 3600.
      open_gripper_position: Open gripper position [0.1mm]. Must be between 0 and 9999. Default
            860.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
            Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4.
            Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4.
            Default 1.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 0, 6500, "y_position")
    utils.assert_clamp(y_direction, 0, 1, "y_direction")
    utils.assert_clamp(z_position, 0, 3600, "z_position")
    utils.assert_clamp(z_direction, 0, 1, "z_direction")
    utils.assert_clamp(grip_direction, 1, 4, "grip_direction")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(z_position_at_the_command_end, 0, 3600, "z_position_at_the_command_end")
    utils.assert_clamp(open_gripper_position, 0, 9999, "open_gripper_position")
    utils.assert_clamp(collision_control_level, 0, 1, "collision_control_level")
    utils.assert_clamp(acceleration_index_high_acc, 0, 4, "acceleration_index_high_acc")
    utils.assert_clamp(acceleration_index_low_acc, 0, 4, "acceleration_index_low_acc")

    return self.send_command(
      module="C0",
      command="PR",
      fmt="",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{z_position_at_the_command_end:04}",
      gr=grip_direction,
      go=f"{open_gripper_position:04}",
      ga=collision_control_level,
      # xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
    )

  def move_plate_to_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1
  ):
    """ Move plate to position.

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y, 4 = negative
            X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
            command 0.1mm]. Must be between 0 and 3600. Default 3600.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
            Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4. Default 1.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 0, 6500, "y_position")
    utils.assert_clamp(y_direction, 0, 1, "y_direction")
    utils.assert_clamp(z_position, 0, 3600, "z_position")
    utils.assert_clamp(z_direction, 0, 1, "z_direction")
    utils.assert_clamp(grip_direction, 1, 4, "grip_direction")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(collision_control_level, 0, 1, "collision_control_level")
    utils.assert_clamp(acceleration_index_high_acc, 0, 4, "acceleration_index_high_acc")
    utils.assert_clamp(acceleration_index_low_acc, 0, 4, "acceleration_index_low_acc")

    return self.send_command(
      module="C0",
      command="PM",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      gr=grip_direction,
      th=minimum_traverse_height_at_beginning_of_a_command,
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
    )

  def collapse_gripper_arm(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    fold_up_sequence_at_the_end_of_process: bool = True
  ):
    """ Collapse gripper arm

    Args:
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
                                                         command 0.1mm]. Must be between 0 and 3600.
                                                         Default 3600.
      fold_up_sequence_at_the_end_of_process: fold up sequence at the end of process. Default True.
    """

    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")

    return self.send_command(
      module="C0",
      command="PN",
      th=minimum_traverse_height_at_beginning_of_a_command,
      gc=fold_up_sequence_at_the_end_of_process,
    )

  # -------------- 3.17.3 Hotel handling commands --------------

  # TODO:(command:PO) Get plate from hotel
  # TODO:(command:PI) Put plate to hotel

  # -------------- 3.17.4 Barcode commands --------------

  # TODO:(command:PB) Read barcode using iSWAP

  # -------------- 3.17.5 Teach in commands --------------

  def prepare_iswap_teaching(
  self,
  x_position: int = 0,
  x_direction: int = 0,
  y_position: int = 0,
  y_direction: int = 0,
  z_position: int = 0,
  z_direction: int = 0,
  location: int = 0,
  hotel_depth: int = 0,
  minimum_traverse_height_at_beginning_of_a_command: int = 3600,
  collision_control_level: int = 1,
  acceleration_index_high_acc: int = 4,
  acceleration_index_low_acc: int = 1
):
    """ Prepare iSWAP teaching

    Prepare for teaching with iSWAP

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      location: location. 0 = Stack 1 = Hotel. Must be between 0 and 1. Default 0.
      hotel_depth: Hotel depth [0.1mm]. Must be between 0 and 3000. Default 13000.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
                                                         a command 0.1mm]. Must be between 0 and
                                                         3600. Default 3600.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4.
                                   Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4.
                                  Default 1.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 0, 6500, "y_position")
    utils.assert_clamp(y_direction, 0, 1, "y_direction")
    utils.assert_clamp(z_position, 0, 3600, "z_position")
    utils.assert_clamp(z_direction, 0, 1, "z_direction")
    utils.assert_clamp(location, 0, 1, "location")
    utils.assert_clamp(hotel_depth, 0, 3000, "hotel_depth")
    utils.assert_clamp(minimum_traverse_height_at_beginning_of_a_command, 0, 3600, \
                  "minimum_traverse_height_at_beginning_of_a_command")
    utils.assert_clamp(collision_control_level, 0, 1, "collision_control_level")
    utils.assert_clamp(acceleration_index_high_acc, 0, 4, "acceleration_index_high_acc")
    utils.assert_clamp(acceleration_index_low_acc, 0, 4, "acceleration_index_low_acc")

    return self.send_command(
      module="C0",
      command="PT",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      hh=location,
      hd=hotel_depth,
      th=minimum_traverse_height_at_beginning_of_a_command,
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
    )

  def get_logic_iswap_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    location: int = 0,
    hotel_depth: int = 1300,
    grip_direction: int = 1,
    collision_control_level: int = 1
  ):
    """ Get logic iSWAP position

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      location: location. 0 = Stack 1 = Hotel. Must be between 0 and 1. Default 0.
      hotel_depth: Hotel depth [0.1mm]. Must be between 0 and 3000. Default 1300.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y,
                      4 = negative X. Must be between 1 and 4. Default 1.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
    """

    utils.assert_clamp(x_position, 0, 30000, "x_position")
    utils.assert_clamp(x_direction, 0, 1, "x_direction")
    utils.assert_clamp(y_position, 0, 6500, "y_position")
    utils.assert_clamp(y_direction, 0, 1, "y_direction")
    utils.assert_clamp(z_position, 0, 3600, "z_position")
    utils.assert_clamp(z_direction, 0, 1, "z_direction")
    utils.assert_clamp(location, 0, 1, "location")
    utils.assert_clamp(hotel_depth, 0, 3000, "hotel_depth")
    utils.assert_clamp(grip_direction, 1, 4, "grip_direction")
    utils.assert_clamp(collision_control_level, 0, 1, "collision_control_level")

    return self.send_command(
      module="C0",
      command="PC",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      hh=location,
      hd=hotel_depth,
      gr=grip_direction,
      ga=collision_control_level,
    )

  # -------------- 3.17.6 iSWAP query --------------

  def request_iswap_in_parking_position(self):
    """ Request iSWAP in parking position

    Returns:
      0 = gripper is not in parking position
      1 = gripper is in parking position
    """

    resp = self.send_command(module="C0", command="RG")
    return self.parse_response(resp, "rg#")

  def request_plate_in_iswap(self):
    """ Request plate in iSWAP

    Returns:
      0 = plate not holding
      1 = plate holding
    """

    resp = self.send_command(module="C0", command="QP")
    return self.parse_response(resp, "rg#")

  def request_iswap_position(self):
    """ Request iSWAP position ( grip center )

    Returns:
      xs: Hotel center in X direction [0.1mm]
      xd: X direction 0 = positive 1 = negative
      yj: Gripper center in Y direction [0.1mm]
      yd: Y direction 0 = positive 1 = negative
      zj: Gripper Z height (gripping height) [0.1mm]
      zd: Z direction 0 = positive 1 = negative
    """

    resp = self.send_command(module="C0", command="QG")
    return self.parse_response(resp, "xs#####xd#yj####yd#zj####zd#")

  def request_iswap_initialization_status(self) -> bool:
    """ Request iSWAP initialization status

    Returns:
      True if iSWAP is fully initialized
    """

    resp = self.send_command(module="R0", command="QW", fmt="qw#")
    return resp["qw"] == 1
