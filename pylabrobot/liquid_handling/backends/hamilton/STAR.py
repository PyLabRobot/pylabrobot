"""
This file defines interfaces for all supported Hamilton liquid handling robots.
"""
# pylint: disable=invalid-sequence-index, dangerous-default-value

from abc import ABCMeta, abstractmethod
import asyncio
import datetime
import enum
import functools
import logging
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple, Sequence, TypeVar, Union, cast

from pylabrobot.default import Default, get_value, is_default, is_not_default
import pylabrobot.liquid_handling.backends.hamilton.errors as herrors
from pylabrobot.liquid_handling.backends.hamilton.errors import HamiltonFirmwareError
from pylabrobot.liquid_handling.channel_tip_tracker import  ChannelHasTipError, ChannelHasNoTipError
from pylabrobot.liquid_handling.liquid_classes.hamilton import get_liquid_class
from pylabrobot.liquid_handling.backends.USBBackend import USBBackend
from pylabrobot.liquid_handling.standard import (
  PipettingOp,
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  Dispense,
  DispensePlate,
  GripDirection,
  Move
)
from pylabrobot.resources import (
  Coordinate,
  Plate,
  Resource,
  TipSpot,
  Well
)
from pylabrobot.resources.errors import (
  TipTooLittleVolumeError,
  ContainerTooLittleLiquidError,
  TipSpotHasNoTipError
)
from pylabrobot.resources.ml_star import (
  HamiltonTip,
  TipDropMethod,
  TipPickupMethod,
  TipSize,
)


T = TypeVar("T")


logger = logging.getLogger(__name__)


def need_iswap_parked(method: Callable):
  """Ensure that the iSWAP is in parked position before running command.

  If the iSWAP is not parked, it get's parked before running the command.
  """

  @functools.wraps(method)
  async def wrapper(self, *args, **kwargs):
    if not self.iswap_parked:
      await self.park_iswap()

    result = await method(self, *args, **kwargs) # pylint: disable=not-callable

    return result
  return wrapper


def _fill_in_defaults(val: Optional[List[T]], default: List[T]) -> List[T]:
  """ Util for filling in None lists with the default values. """
  t = type(default[0])
  if val is not None:
    if len(val) != len(default):
      raise ValueError(f"Value length must equal num operations ({len(default)}), but is {val}")
    if not all(isinstance(v, t) for v in val):
      raise ValueError(f"Value must be a list of {t}, but is {val}")
    return val
  return default


class HamiltonLiquidHandler(USBBackend, metaclass=ABCMeta):
  """
  Abstract base class for Hamilton liquid handling robot backends.
  """

  @abstractmethod
  def __init__(
    self,
    device_address: Optional[int] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """

    Args:
      device_address: The USB address of the Hamilton device. Only useful if using more than one
        Hamilton device.
      packet_read_timeout: The timeout for reading packets from the Hamilton machine in seconds.
      read_timeout: The timeout for  from the Hamilton machine in seconds.
      num_channels: the number of pipette channels present on the robot.
    """

    super().__init__(
      address=device_address,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      id_vendor=0x08af,
      id_product=0x8000)

    self.id_ = 0

    self._reading_thread: Optional[threading.Thread] = None
    self._waiting_tasks: Dict[str,
      Tuple[asyncio.AbstractEventLoop, asyncio.Future, str, str, float]] = {}

  def _generate_id(self) -> str:
    """ continuously generate unique ids 0 <= x < 10000. """
    self.id_ += 1
    return f"{self.id_ % 10000:04}"

  def _to_list(self, val: List[T], tip_pattern: List[bool]) -> List[T]:
    """ Convert a list of values to a list of values with the correct length.

    This is roughly one-hot encoding. STAR expects a value for a list parameter at the position
    for the corresponding channel. If `tip_pattern` is False, there, the value itself is ignored,
    but it must be present.

    Args:
      val: A list of values, exactly one for each channel that is involved in the operation.
      tip_pattern: A list of booleans indicating whether a channel is involved in the operation.

    Returns:
      A list of values with the correct length. Each value that is not involved in the operation
      is set to the first value in `val`, which is ignored by STAR.
    """

    # use the default value if a channel is not involved, otherwise use the value in val
    assert len(val) > 0
    assert len(val) <= len(tip_pattern)

    result: List[T] = []
    arg_index = 0
    for channel_involved in tip_pattern:
      if channel_involved:
        if arg_index >= len(val):
          raise ValueError(f"Too few values for tip pattern {tip_pattern}: {val}")
        result.append(val[arg_index])
        arg_index += 1
      else:
        # this value will be ignored, and using the first value silences mypy
        result.append(val[0])
    if arg_index < len(val):
      raise ValueError(f"Too many values for tip pattern {tip_pattern}: {val}")
    return result

  def _assemble_command(
    self,
    module: str,
    command: str,
    tip_pattern: Optional[List[bool]],
    **kwargs) -> Tuple[str, str]:
    """ Assemble a firmware command to the Hamilton machine.

    Args:
      module: 2 character module identifier (C0 for master, ...)
      command: 2 character command identifier (QM for request status, ...)
      tip_pattern: A list of booleans indicating whether a channel is involved in the operation.
        This value will be used to convert the list values in kwargs to the correct length.
      kwargs: any named parameters. the parameter name should also be 2 characters long. The value
        can be any size.

    Returns:
      A string containing the assembled command.
    """

    cmd = module + command
    id_ = self._generate_id()
    cmd += f"id{id_}" # has to be first param

    for k, v in kwargs.items():
      if type(v) is datetime.datetime:
        v = v.strftime("%Y-%m-%d %h:%M")
      elif type(v) is bool:
        v = 1 if v else 0
      elif type(v) is list:
        # If this command is 'one-hot' encoded, for the channels, then the list should be the
        # same length as the 'one-hot' encoding key (tip_pattern.) If the list is shorter than
        # that, it will be 'one-hot encoded automatically. Note that this may raise an error if
        # the number of values provided is not the same as the number of channels used.
        if tip_pattern is not None:
          if len(v) != len(tip_pattern):
            # convert one-hot encoded list to int list
            v = self._to_list(v, tip_pattern)
          # list is now of length len(tip_pattern)
        if type(v[0]) is bool: # convert bool list to int list
          v = [int(x) for x in v]
        v = " ".join([str(e) for e in v]) + ("&" if len(v) < self.num_channels else "")
      if k.endswith("_"): # workaround for kwargs named in, as, ...
        k = k[:-1]
      assert len(k) == 2, "Keyword arguments should be 2 characters long, but got: " + k
      cmd += f"{k}{v}"

    return cmd, id_

  def parse_fw_string(self, resp: str, fmt: str = "") -> dict:
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

  def parse_response(self, resp: str, fmt: str) -> dict:
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
      # filter None elements
      errors_dict = {k:v for k,v in errors.groupdict().items() if v is not None}
      # filter 00 and 00/00 elements, which mean no error.
      errors_dict = {k:v for k,v in errors_dict.items() if v not in ["00", "00/00"]}

    has_error = not (errors is None or len(errors_dict) == 0)
    if has_error:
      he = HamiltonFirmwareError(errors_dict, raw_response=resp)

      # If there is a faulty parameter error, request which parameter that is.
      for module_name, error in he.items():
        if error.message == "Unknown parameter":
          # temp. disabled until we figure out how to handle async in parse response (the
          # background thread does not have an event loop, and I'm not sure if it should.)
          # vp = await self.send_command(module=error.raw_module, command="VP", fmt="vp&&")["vp"]
          # he[module_name].message += f" ({vp})" # pylint: disable=unnecessary-dict-index-lookup

          # pylint: disable=unnecessary-dict-index-lookup
          he[module_name].message += " (call lh.backend.request_name_of_last_faulty_parameter)"

      raise he

    info = self.parse_fw_string(resp, fmt)
    return info

  async def send_command(
    self,
    module: str,
    command: str,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    fmt: str = "",
    wait = True,
    **kwargs
  ):
    """ Send a firmware command to the Hamilton machine.

    Args:
      module: 2 character module identifier (C0 for master, ...)
      command: 2 character command identifier (QM for request status)
      write_timeout: write timeout in seconds. If None, `self.write_timeout` is used.
      read_timeout: read timeout in seconds. If None, `self.read_timeout` is used.
      fmt: A string containing the format of the response. If None, just the id parameter is read.
      wait: If True, wait for a response. If False, return `None` immediately after sending the
        command.
      kwargs: any named parameters. The parameter name should also be 2 characters long. The value
        can be of any size.

    Raises:
      HamiltonFirmwareError: if an error response is received.

    Returns:
      A dictionary containing the parsed response, or None if no response was read within `timeout`.
    """

    cmd, id_ = self._assemble_command(module=module, command=command, tip_pattern=tip_pattern,
      **kwargs)

    self.write(cmd, timeout=write_timeout)

    if not wait:
      return None

    # Attempt to read packets until timeout, or when we identify the right id.
    if read_timeout is None:
      read_timeout = self.read_timeout

    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    self._start_reading(id_, loop, fut, cmd, fmt, read_timeout)
    return await fut

  def _start_reading(
    self,
    id_: str,
    loop: asyncio.AbstractEventLoop,
    fut: asyncio.Future,
    cmd: str,
    fmt: str,
    timeout: int) -> None:
    """ Submit a task to the reading thread. Starts reading thread if it is not already running. """

    timeout_time = time.time() + timeout
    self._waiting_tasks[id_] = (loop, fut, cmd, fmt, timeout_time)

    # Start reading thread if it is not already running.
    if len(self._waiting_tasks) == 1:
      self._reading_thread = threading.Thread(target=self._continuously_read)
      self._reading_thread.start()

  def _continuously_read(self) -> None:
    """ Continuously read from the USB port until all tasks are completed.

    Tasks are stored in the `self._waiting_tasks` dictionary, and contain a future that will be
    completed when the task is finished. Tasks are submitted to the dictionary using the
    `self._start_reading` method.

    On each iteration, read the USB port. If a response is received, parse it and check if it is
    relevant to any of the tasks. If so, complete the future and remove the task from the
    dictionary. If a task has timed out, complete the future with a `TimeoutError`.
    """

    logger.debug("Starting reading thread...")

    while len(self._waiting_tasks) > 0:
      for id_, (loop, fut, cmd, fmt, timeout_time) in self._waiting_tasks.items():
        if time.time() > timeout_time:
          logger.warning("Timeout while waiting for response to command %s.", cmd)
          loop.call_soon_threadsafe(fut.set_exception,
            TimeoutError(f"Timeout while waiting for response to command {cmd}."))
          del self._waiting_tasks[id_]
          break

      try:
        resp = self.read().decode("utf-8")
      except TimeoutError:
        continue

      # Parse response.
      try:
        parsed_response = self.parse_fw_string(resp)
        logger.info("Received response: %s", resp)
      except ValueError:
        logger.warning("Could not parse response: %s", resp)
        continue

      for id_, (loop, fut, cmd, fmt, timeout_time) in self._waiting_tasks.items():
        if "id" in parsed_response and f"{parsed_response['id']:04}" == id_:
          try:
            parsed = self.parse_response(resp, fmt)
          except HamiltonFirmwareError as e:
            loop.call_soon_threadsafe(fut.set_exception, e)
          else:
            if fmt is not None and fmt != "":
              loop.call_soon_threadsafe(fut.set_result, parsed)
            else:
              loop.call_soon_threadsafe(fut.set_result, resp)
          del self._waiting_tasks[id_]
          break

    self._reading_thread = None
    logger.debug("Reading thread stopped.")


class STAR(HamiltonLiquidHandler):
  """
  Interface for the Hamilton STAR.
  """

  def __init__(
    self,
    device_address: Optional[int] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """ Create a new STAR interface.

    Args:
      device_address: the USB device address of the Hamilton STAR. Only useful if using more than
        one Hamilton machine over USB.
      packet_read_timeout: timeout in seconds for reading a single packet.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
      num_channels: the number of pipette channels present on the robot.
    """

    super().__init__(
      device_address=device_address,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout)

    self._tth2tti: dict[int, int] = {} # hash to tip type index
    self._iswap_parked: Optional[bool] = None

    self._num_channels: Optional[int] = None

  @property
  def num_channels(self) -> int:
    """ The number of pipette channels present on the robot. """
    if self._num_channels is None:
      raise RuntimeError("has not loaded num_channels, forgot to call `setup`?")
    return self._num_channels

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "packet_read_timeout": self.packet_read_timeout,
      "read_timeout": self.read_timeout,
      "write_timeout": self.write_timeout,
    }

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

  async def setup(self):
    """ setup

    Creates a USB connection and finds read/write interfaces.
    """

    await super().setup()

    tip_presences = await self.request_tip_presence()
    self._num_channels = len(tip_presences)

    extended_conf = await self.request_extended_configuration()
    left_x_drive_configuration_byte_1 = bin(extended_conf["xl"])
    left_x_drive_configuration_byte_1 = left_x_drive_configuration_byte_1 + \
      "0" * (16 - len(left_x_drive_configuration_byte_1))
    left_x_drive_configuration_byte_1 = left_x_drive_configuration_byte_1[2:]
    self.core96_head_installed = left_x_drive_configuration_byte_1[2] == "1"
    self.iswap_installed = left_x_drive_configuration_byte_1[1] == "1"

    initialized = await self.request_instrument_initialization_status()

    if not initialized:
      logger.info("Running backend initialization procedure.")

      await self.pre_initialize_instrument()

      # if self.core96_head_installed:
      #   self.initialize_core_96_head()

    if not initialized or any(tip_presences):
      dy = (4050 - 2175) // (self.num_channels - 1)
      y_positions = [4050 - i * dy for i in range(self.num_channels)]

      await self.initialize_pipetting_channels(
        x_positions=[extended_conf["xw"]],  # Tip eject waste X position.
        y_positions=y_positions,
        begin_of_tip_deposit_process=2450,
        end_of_tip_deposit_process=1220,
        z_position_at_end_of_a_command=3600,
        tip_pattern=[True], # [True] * 8
        tip_type=4, # TODO: get from tip types
        discarding_method=0
      )

    iswap_initialized = await self.request_iswap_initialization_status()
    if self.iswap_installed and not iswap_initialized:
      await self.initialize_iswap()

      await self.park_iswap()
      self._iswap_parked = True

  async def stop(self):
    self._waiting_tasks.clear()

  # ============== Tip Types ==============

  async def get_or_assign_tip_type_index(self, tip: HamiltonTip) -> int:
    """ Get a tip type table index for the tip.

    If the tip has previously been defined, used that index. Otherwise, define a new tip type.
    """

    tip_type_hash = hash(tip)

    if tip_type_hash not in self._tth2tti:
      ttti = len(self._tth2tti) + 1
      if ttti > 99:
        raise ValueError("Too many tip types defined.")

      await self.define_tip_needle(
        tip_type_table_index=ttti,
        filter=tip.has_filter,
        tip_length=int((tip.total_tip_length - tip.fitting_depth) * 10), # in 0.1mm
        maximum_tip_volume=int(tip.maximal_volume * 10), # in 0.1ul
        tip_size=tip.tip_size,
        pickup_method=tip.pickup_method
      )
      self._tth2tti[tip_type_hash] = ttti

    return self._tth2tti[tip_type_hash]

  def _get_hamilton_tip(self, tip_spots: List[TipSpot]) -> HamiltonTip:
    """ Get the single tip type for all tip spots. If it does not exist or is not a HamiltonTip,
    raise an error. """
    tips = set(tip_spot.get_tip() for tip_spot in tip_spots)
    if len(tips) > 1:
      raise ValueError("Cannot mix tips with different tip types.")
    if len(tips) == 0:
      raise ValueError("No tips specified.")
    tip = tips.pop()
    if not isinstance(tip, HamiltonTip):
      raise ValueError(f"Tip {tip} is not a HamiltonTip.")
    return tip

  async def get_ttti(self, tip_spots: List[TipSpot]) -> int:
    """ Get tip type table index for a list of tips.

    Ensure that for all non-None tips, they have the same tip type, and return the tip type table
    index for that tip type.
    """

    tip = self._get_hamilton_tip(tip_spots)
    return await self.get_or_assign_tip_type_index(tip)

  def _ops_to_fw_positions(
    self,
    ops: Sequence[PipettingOp],
    use_channels: List[int]
  ) -> Tuple[List[int], List[int], List[bool]]:
    """ use_channels is a list of channels to use. STAR expects this in one-hot encoding. This is
    method converts that, and creates a matching list of x and y positions. """
    assert use_channels == sorted(use_channels), "Channels must be sorted."

    x_positions: List[int] = []
    y_positions: List[int] = []
    channels_involved: List[bool] = []
    for i, channel in enumerate(use_channels):
      while channel > len(channels_involved):
        channels_involved.append(False)
        x_positions.append(0)
        y_positions.append(0)
      channels_involved.append(True)
      x_pos = ops[i].resource.get_absolute_location().x
      if ops[i].offset is Default or isinstance(ops[i].resource, (TipSpot, Well)):
        x_pos += ops[i].resource.center().x
      x_pos += get_value(ops[i].offset, Coordinate.zero()).x
      x_positions.append(int(x_pos*10))

      y_pos = ops[i].resource.get_absolute_location().y
      if ops[i].offset is Default or isinstance(ops[i].resource, (TipSpot, Well)):
        y_pos += ops[i].resource.center().y
      y_pos += get_value(ops[i].offset, Coordinate.zero()).y
      y_positions.append(int(y_pos*10))

    if len(ops) > self.num_channels:
      raise ValueError(f"Too many channels specified: {len(ops)} > {self.num_channels}")

    if len(x_positions) < self.num_channels:
      # We do want to have a trailing zero on x_positions, y_positions, and channels_involved, for
      # some reason, if the length < 8.
      x_positions = x_positions + [0]
      y_positions = y_positions + [0]
      channels_involved = channels_involved + [False]

    return x_positions, y_positions, channels_involved

  # ============== LiquidHandlerBackend methods ==============

  @need_iswap_parked
  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
  ):
    """ Pick up tips from a resource. """

    x_positions, y_positions, channels_involved = \
      self._ops_to_fw_positions(ops, use_channels)

    ttti = await self.get_ttti([op.resource for op in ops])

    max_z = max(op.get_absolute_location().z for op in ops)
    max_total_tip_length = max(op.tip.total_tip_length for op in ops)
    max_tip_length = max((op.tip.total_tip_length-op.tip.fitting_depth) for op in ops)

    if self._get_hamilton_tip([op.resource for op in ops]).tip_size != TipSize.STANDARD_VOLUME:
      # not sure why this is necessary, but it is according to log files and experiments
      max_tip_length -= 2

    try:
      tip = ops[0].tip
      assert isinstance(tip, HamiltonTip), "Tip type must be HamiltonTip."
      print(f"Pick up tip {tip}")
      return await self.pick_up_tip(
        x_positions=x_positions,
        y_positions=y_positions,
        tip_pattern=channels_involved,
        tip_type_idx=ttti,
        begin_tip_pick_up_process=int((max_z + max_total_tip_length)*10),
        end_tip_pick_up_process=int((max_z + max_tip_length)*10),
        minimum_traverse_height_at_beginning_of_a_command=2450,
        pickup_method=tip.pickup_method,
      )
    except HamiltonFirmwareError as e:
      tip_already_fitted_errors: List[int] = []
      no_tip_present_errors: List[int] = []
      for i in range(1, self.num_channels+1):
        channel_error = e.error_for_channel(i)
        if channel_error is None:
          continue
        if isinstance(channel_error, herrors.TipAlreadyFittedError):
          tip_already_fitted_errors.append(i-1)
        elif channel_error.trace_information in [75]:
          no_tip_present_errors.append(i-1)
      if len(tip_already_fitted_errors) > 0:
        raise ChannelHasTipError(f"Tip already fitted on channels {tip_already_fitted_errors}") \
          from e
      elif len(no_tip_present_errors) > 0:
        raise TipSpotHasNoTipError("No tip present in locations for channels "
                                  f"{no_tip_present_errors}") from e

      raise e

  @need_iswap_parked
  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    drop_method: Optional[TipDropMethod] = None,
  ):
    """ Drop tips to a resource.

    Args:
      drop_method: The method to use for dropping tips. If None, the default method for dropping to
        tip spots is `DROP`, and everything else is `PLACE_SHIFT`. Note that `DROP` is only the
        default if *all* tips are being dropped to a tip spot.
    """

    if drop_method is None:
      if any(not isinstance(op.resource, TipSpot) for op in ops):
        drop_method = TipDropMethod.PLACE_SHIFT
      else:
        drop_method = TipDropMethod.DROP

    x_positions, y_positions, channels_involved = \
      self._ops_to_fw_positions(ops, use_channels)

    # get highest z position
    max_z = max(op.get_absolute_location().z for op in ops)
    max_total_tip_length = max(op.tip.total_tip_length for op in ops)
    max_tip_length = max((op.tip.total_tip_length-op.tip.fitting_depth) for op in ops)

    try:
      return await self.discard_tip(
        x_positions=x_positions,
        y_positions=y_positions,
        tip_pattern=channels_involved,
        begin_tip_deposit_process=int((max_z + max_total_tip_length)*10),
        end_tip_deposit_process=int((max_z + max_tip_length)*10),
        minimum_traverse_height_at_beginning_of_a_command=2450,
        z_position_at_end_of_a_command=2450,
        discarding_method=drop_method
      )
    except HamiltonFirmwareError as e:
      tip_errors: List[int] = []
      for i in range(1, self.num_channels+1):
        channel_error = e.error_for_channel(i)
        if isinstance(channel_error, herrors.NoTipError):
          tip_errors.append(i-1)

      if len(tip_errors) > 0:
        raise ChannelHasNoTipError(f"No tip present on channels {tip_errors}") from e

      raise e

  def _get_tip_max_volumes(self, ops: Sequence[Union[Aspiration, Dispense]]) -> List[float]:
    """ These tip volumes (mostly with filters) are slightly different form the ones in the
    liquid class mapping, so we need to map them here. If no mapping is found, we use the
    given maximal volume of the tip. """

    return [
      { 360.0: 300.0,
        1065.0: 1000.0,
        1250.0: 1000.0,
        4367.0: 4000.0,
        5420.0: 5000.0,
      }.get(op.tip.maximal_volume, op.tip.maximal_volume) for op in ops]

  class LLDMode(enum.Enum):
    """ Liquid level detection mode. """

    OFF = 0
    GAMMA = 1
    PRESSURE = 2
    DUAL = 3
    Z_TOUCH_OFF = 4

  @need_iswap_parked
  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],

    lld_search_height: Optional[List[int]] = None,
    clot_detection_height: Optional[List[int]] = None,
    pull_out_distance_transport_air: Optional[List[int]] = None,
    second_section_height: Optional[List[int]] = None,
    second_section_ratio: Optional[List[int]] = None,
    minimum_height: Optional[List[int]] = None,
    immersion_depth: Optional[List[int]] = None,
    immersion_depth_direction: Optional[List[int]] = None,
    surface_following_distance: Optional[List[int]] = None,
    transport_air_volume: Optional[List[int]] = None,
    pre_wetting_volume: Optional[List[int]] = None,
    lld_mode: Optional[List[LLDMode]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    dp_lld_sensitivity: Optional[List[int]] = None,
    aspirate_position_above_z_touch_off: Optional[List[int]] = None,
    detection_height_difference_for_dual_lld: Optional[List[int]] = None,
    swap_speed: Optional[List[int]] = None,
    settling_time: Optional[List[int]] = None,
    homogenization_volume: Optional[List[int]] = None,
    homogenization_cycles: Optional[List[int]] = None,
    homogenization_position_from_liquid_surface: Optional[List[int]] = None,
    homogenization_speed: Optional[List[int]] = None,
    homogenization_surface_following_distance: Optional[List[int]] = None,
    limit_curve_index: Optional[List[int]] = None,

    use_2nd_section_aspiration: Optional[List[bool]] = None,
    retract_height_over_2nd_section_to_empty_tip: Optional[List[int]] = None,
    dispensation_speed_during_emptying_tip: Optional[List[int]] = None,
    dosing_drive_speed_during_2nd_section_search: Optional[List[int]] = None,
    z_drive_speed_during_2nd_section_search: Optional[List[int]] = None,
    cup_upper_edge: Optional[List[int]] = None,
    ratio_liquid_rise_to_tip_deep_in: Optional[List[int]] = None,
    immersion_depth_2nd_section: Optional[List[int]] = None,

    minimum_traverse_height_at_beginning_of_a_command: int = 2450,
    min_z_endpos: int = 2450
  ):
    """ Aspirate liquid from the specified channels.

    For all parameters where `None` is the default value, STAR will use the default value, based on
    the aspirations. For all list parameters, the length of the list must be equal to the number of
    operations.

    .. warning:: The parameters in this method, with the exception of `ops` and `use_channels`,
      expect units of tenths of 'millimeters' (i.e. 10 = 1 mm), or tenths of 'microliters' (i.e. 10
      = 1 ul), or tenths of seconds. Speeds are in 0.1ul/s. This is a deviation from the rest of the
      API, which uses SI units. This is because the Hamilton API uses these units.

    Args:
      ops: The aspiration operations to perform.
      use_channels: The channels to use for the operations.
      blow_out_air_volumes: The amount of air to be blown out over all matching dispense operations.
      lld_search_height: The height to start searching for the liquid level when using LLD.
      clot_detection_height: Unknown, but probably the height to search for clots when doing LLD.
      pull_out_distance_transport_air: The distance to pull out when aspirating air, if LLD is
        disabled.
      second_section_height: The height to start the second section of aspiration.
      second_section_ratio: Unknown.
      minimum_height: The minimum height to move to, this is the end of aspiration. The channel
       will move linearly from the liquid surface to this height over the course of the aspiration.
      immersion_depth: Unknown exactly, probably the depth to move into the liquid. Possibly below
        the liquid surface.
      immersion_depth_direction: Unknown.
      surface_following_distance: The distance to follow the liquid surface.
      transport_air_volume: The volume of air to aspirate after the liquid.
      pre_wetting_volume: The volume of liquid to use for pre-wetting.
      lld_mode: The liquid level detection mode to use.
      gamma_lld_sensitivity: The sensitivity of the gamma LLD.
      dp_lld_sensitivity: The sensitivity of the DP LLD.
      aspirate_position_above_z_touch_off: If the LLD mode is Z_TOUCH_OFF, this is the height above
        the bottom of the well (presumably) to aspirate from.
      detection_height_difference_for_dual_lld: Difference between the gamma and DP LLD heights if
        the LLD mode is DUAL.
      swap_speed: Unknown.
      settling_time: The time to wait after homogenization.
      homogenization_volume: The volume to aspirate for homogenization.
      homogenization_cycles: The number of cycles to perform for homogenization.
      homogenization_position_from_liquid_surface: The height to aspirate from for homogenization
        (LLD or absolute terms).
      homogenization_speed: The speed to aspirate at for homogenization.
      homogenization_surface_following_distance: The distance to follow the liquid surface for
        homogenization.
      limit_curve_index: The index of the limit curve to use.

      use_2nd_section_aspiration: Whether to use the second section of aspiration.
      retract_height_over_2nd_section_to_empty_tip: Unknown.
      dispensation_speed_during_emptying_tip: Unknown.
      dosing_drive_speed_during_2nd_section_search: Unknown.
      z_drive_speed_during_2nd_section_search: Unknown.
      cup_upper_edge: Unknown.
      ratio_liquid_rise_to_tip_deep_in: Unknown.
      immersion_depth_2nd_section: The depth to move into the liquid for the second section of
        aspiration.

      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to before
        starting an aspiration.
      min_z_endpos: The minimum height to move to, this is the end of aspiration.
    """

    x_positions, y_positions, channels_involved = \
      self._ops_to_fw_positions(ops, use_channels)

    n = len(ops)

    # convert max volumes
    tip_max_volumes = self._get_tip_max_volumes(ops)

    hamilton_liquid_classes = [
      get_liquid_class(
        tip_volume=int(tmv),
        is_core=False,
        is_tip=True,
        has_filter=op.tip.has_filter,
        liquid_class=op.liquid_class,
        jet=False, # for aspiration
        empty=False # for aspiration
      ) for tmv, op in zip(tip_max_volumes, ops)]

    # correct volumes using the liquid class
    for op, hlc in zip(ops, hamilton_liquid_classes):
      op.volume = hlc.compute_corrected_volume(op.volume) if hlc is not None else op.volume

    well_bottoms = [op.get_absolute_location().z + \
                    (op.offset.z if is_not_default(op.offset) else 0) for op in ops]
    liquid_surfaces_no_lld = [ls + (op.liquid_height if is_not_default(op.liquid_height) else 1)
                              for ls, op in zip(well_bottoms, ops)]
    lld_search_heights = [wb + op.resource.get_size_z() + 5 for wb, op in zip(well_bottoms, ops)]

    aspiration_volumes = [int(op.volume * 10) for op in ops]
    lld_search_height = [int(sh * 10) for sh in lld_search_heights]
    clot_detection_height = _fill_in_defaults(clot_detection_height,
      default=[int(hlc.aspiration_clot_retract_height*10) if hlc is not None else 0
              for hlc in hamilton_liquid_classes])
    pull_out_distance_transport_air = _fill_in_defaults(pull_out_distance_transport_air, [100]*n)
    second_section_height = _fill_in_defaults(second_section_height, [32]*n)
    second_section_ratio = _fill_in_defaults(second_section_ratio, [6180]*n)
    minimum_height = \
      _fill_in_defaults(minimum_height, [int((ls-5) * 10) for ls in liquid_surfaces_no_lld])
    immersion_depth = _fill_in_defaults(immersion_depth, [0]*n)
    immersion_depth_direction = _fill_in_defaults(immersion_depth_direction, [0]*n)
    surface_following_distance = _fill_in_defaults(surface_following_distance, [0]*n)
    flow_rates = [
      get_value(op.flow_rate, default=hlc.aspiration_flow_rate if hlc is not None else 100)
        for op, hlc in zip(ops, hamilton_liquid_classes)]
    aspiration_speed = [int(fr * 10) for fr in flow_rates]
    transport_air_volume = _fill_in_defaults(transport_air_volume,
      default=[int(hlc.aspiration_air_transport_volume*10) if hlc is not None else 0
               for hlc in hamilton_liquid_classes])
    pre_wetting_volume = _fill_in_defaults(pre_wetting_volume, [0]*n)
    lld_mode = _fill_in_defaults(lld_mode, [self.__class__.LLDMode.OFF]*n)
    gamma_lld_sensitivity = _fill_in_defaults(gamma_lld_sensitivity, [1]*n)
    dp_lld_sensitivity = _fill_in_defaults(dp_lld_sensitivity, [1]*n)
    aspirate_position_above_z_touch_off = \
      _fill_in_defaults(aspirate_position_above_z_touch_off, [0]*n)
    detection_height_difference_for_dual_lld = \
      _fill_in_defaults(detection_height_difference_for_dual_lld, [0]*n)
    swap_speed = _fill_in_defaults(swap_speed,
      default=[int(hlc.aspiration_swap_speed*10) if hlc is not None else 0
               for hlc in hamilton_liquid_classes])
    settling_time = _fill_in_defaults(settling_time,
      default=[int(hlc.aspiration_settling_time*10) if hlc is not None else 0
               for hlc in hamilton_liquid_classes])
    homogenization_volume = _fill_in_defaults(homogenization_volume, [0]*n)
    homogenization_cycles = _fill_in_defaults(homogenization_cycles, [0]*n)
    homogenization_position_from_liquid_surface = \
      _fill_in_defaults(homogenization_position_from_liquid_surface, [0]*n)
    homogenization_speed = _fill_in_defaults(homogenization_speed,
        default=[int(hlc.aspiration_mix_flow_rate*10) if hlc is not None else 0
               for hlc in hamilton_liquid_classes])
    homogenization_surface_following_distance = \
      _fill_in_defaults(homogenization_surface_following_distance, [0]*n)
    limit_curve_index = _fill_in_defaults(limit_curve_index, [0]*n)

    use_2nd_section_aspiration = _fill_in_defaults(use_2nd_section_aspiration, [False]*n)
    retract_height_over_2nd_section_to_empty_tip = \
      _fill_in_defaults(retract_height_over_2nd_section_to_empty_tip, [0]*n)
    dispensation_speed_during_emptying_tip = \
      _fill_in_defaults(dispensation_speed_during_emptying_tip, [500]*n)
    dosing_drive_speed_during_2nd_section_search = \
      _fill_in_defaults(dosing_drive_speed_during_2nd_section_search, [500]*n)
    z_drive_speed_during_2nd_section_search = \
      _fill_in_defaults(z_drive_speed_during_2nd_section_search, [300]*n)
    cup_upper_edge = _fill_in_defaults(cup_upper_edge, [0]*n)
    ratio_liquid_rise_to_tip_deep_in = _fill_in_defaults(ratio_liquid_rise_to_tip_deep_in, [0]*n)
    immersion_depth_2nd_section = _fill_in_defaults(immersion_depth_2nd_section, [0]*n)

    try:
      return await self.aspirate_pip(
        aspiration_type=[0 for _ in range(n)],
        tip_pattern=channels_involved,
        x_positions=x_positions,
        y_positions=y_positions,

        aspiration_volumes=aspiration_volumes,
        lld_search_height=lld_search_height,
        clot_detection_height=clot_detection_height,
        liquid_surface_no_lld=[int(ls * 10) for ls in liquid_surfaces_no_lld],
        pull_out_distance_transport_air=pull_out_distance_transport_air,
        second_section_height=second_section_height,
        second_section_ratio=second_section_ratio,
        minimum_height=minimum_height,
        immersion_depth=immersion_depth,
        immersion_depth_direction=immersion_depth_direction,
        surface_following_distance=surface_following_distance,
        aspiration_speed=aspiration_speed,
        transport_air_volume=transport_air_volume,
        blow_out_air_volume=[int(op.blow_out_air_volume*10) for op in ops],
        pre_wetting_volume=pre_wetting_volume,
        lld_mode=[mode.value for mode in lld_mode],
        gamma_lld_sensitivity=gamma_lld_sensitivity,
        dp_lld_sensitivity=dp_lld_sensitivity,
        aspirate_position_above_z_touch_off=aspirate_position_above_z_touch_off,
        detection_height_difference_for_dual_lld=detection_height_difference_for_dual_lld,
        swap_speed=swap_speed,
        settling_time=settling_time,
        homogenization_volume=homogenization_volume,
        homogenization_cycles=homogenization_cycles,
        homogenization_position_from_liquid_surface=homogenization_position_from_liquid_surface,
        homogenization_speed=homogenization_speed,
        homogenization_surface_following_distance=homogenization_surface_following_distance,
        limit_curve_index=limit_curve_index,

        use_2nd_section_aspiration=use_2nd_section_aspiration,
        retract_height_over_2nd_section_to_empty_tip=retract_height_over_2nd_section_to_empty_tip,
        dispensation_speed_during_emptying_tip=dispensation_speed_during_emptying_tip,
        dosing_drive_speed_during_2nd_section_search=dosing_drive_speed_during_2nd_section_search,
        z_drive_speed_during_2nd_section_search=z_drive_speed_during_2nd_section_search,
        cup_upper_edge=cup_upper_edge,
        ratio_liquid_rise_to_tip_deep_in=ratio_liquid_rise_to_tip_deep_in,
        immersion_depth_2nd_section=immersion_depth_2nd_section,

        minimum_traverse_height_at_beginning_of_a_command=\
          minimum_traverse_height_at_beginning_of_a_command,
        min_z_endpos=min_z_endpos,
      )
    except HamiltonFirmwareError as e:
      tll: List[int] = []
      tlv: List[int] = []
      for i in range(1, self.num_channels+1):
        channel_error = e.error_for_channel(i)
        if channel_error is None:
          continue
        if channel_error.trace_information in [70, 71]: # too little / no liquid
          tll.append(i-1)
        elif channel_error.trace_information in [54]: # "Position out of permitted area" = too much
          tlv.append(i-1)

      if len(tll) > 0:
        raise ContainerTooLittleLiquidError(f"There is not enough liquid in containers where the "
                                      f"following channels were trying to aspirate: {tll}") from e
      if len(tlv) > 0:
        raise TipTooLittleVolumeError(f"There is too much liquid in the following channels: {tlv}")\
          from e

      raise e

  @need_iswap_parked
  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int],

    dispensing_mode: Optional[List[int]] = None,
    pull_out_distance_transport_air: Optional[List[int]] = None,
    second_section_height: Optional[List[int]] = None,
    second_section_ratio: Optional[List[int]] = None,
    minimum_height: Optional[List[int]] = None,
    immersion_depth: Optional[List[int]] = None,
    immersion_depth_direction: Optional[List[int]] = None,
    surface_following_distance: Optional[List[int]] = None,
    cut_off_speed: Optional[List[int]] = None,
    stop_back_volume: Optional[List[int]] = None,
    transport_air_volume: Optional[List[int]] = None,
    blow_out_air_volume: Optional[List[int]] = None,
    lld_mode: Optional[List[int]] = None,
    dispense_position_above_z_touch_off: Optional[List[int]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    dp_lld_sensitivity: Optional[List[int]] = None,
    swap_speed: Optional[List[int]] = None,
    settling_time: Optional[List[int]] = None,
    mix_volume: Optional[List[int]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_from_liquid_surface: Optional[List[int]] = None,
    mix_speed: Optional[List[int]] = None,
    mix_surface_following_distance: Optional[List[int]] = None,
    limit_curve_index: Optional[List[int]] = None,

    minimum_traverse_height_at_beginning_of_a_command: int = 2450,
    min_z_endpos: int = 2450,
    side_touch_off_distance: int = 0,
  ):
    """ Dispense liquid from the specified channels.

    For all parameters where `None` is the default value, STAR will use the default value, based on
    the dispenses. For all list parameters, the length of the list must be equal to the number of
    operations.

    .. warning:: The parameters in this method, with the exception of `ops` and `use_channels`,
      expect units of tenths of 'millimeters' (i.e. 10 = 1 mm), or tenths of 'microliters' (i.e. 10
      = 1 ul), or tenths of seconds. Speeds are in 0.1ul/s. This is a deviation from the rest of the
      API, which uses SI units. This is because the Hamilton API uses these units.

    Args:
      ops: The dispense operations to perform.
      use_channels: The channels to use for the dispense operations.
      blow_out_air_volumes: The amount of air to blow out after dispensing. If a single value is
        given, it will be used for all operations.
      dispensing_mode: The dispensing mode to use for each operation.
      pull_out_distance_transport_air: The distance to pull out the tip for aspirating transport air
        if LLD is disabled.
      second_section_height: Unknown.
      second_section_ratio: Unknown.
      minimum_height: The minimum height at the end of the dispense.
      immersion_depth: The distance above or below to liquid level to start dispensing. See the
        `immersion_depth_direction` parameter.
      immersion_depth_direction: (0 = go deeper, 1 = go up out of liquid)
      surface_following_distance: The distance to follow the liquid surface.
      cut_off_speed: Unknown.
      stop_back_volume: Unknown.
      transport_air_volume: The volume of air to dispense before dispensing the liquid.
      blow_out_air_volume: The volume of air to blow out after dispensing.
      lld_mode: The liquid level detection mode to use.
      dispense_position_above_z_touch_off: The height to move after LLD mode found the Z touch off
        position.
      gamma_lld_sensitivity: The gamma LLD sensitivity. (1 = high, 4 = low)
      dp_lld_sensitivity: The dp LLD sensitivity. (1 = high, 4 = low)
      swap_speed: The homogenization speed.
      settling_time: The settling time.
      mix_volume: The volume to use for homogenization.
      mix_cycles: The number of homogenization cycles.
      mix_position_from_liquid_surface: The height to move above the liquid surface for
        homogenization.
      mix_speed: The homogenization speed.
      mix_surface_following_distance: The distance to follow the liquid surface for homogenization.
      limit_curve_index: The limit curve to use for the dispense.
      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to before
        starting a dispense.
      min_z_endpos: The minimum height to move to after a dispense.
      side_touch_off_distance: The distance to move to the side from the well for a dispense.
    """

    x_positions, y_positions, channels_involved = \
      self._ops_to_fw_positions(ops, use_channels)

    n = len(ops)

    # convert max volumes
    tip_max_volumes = self._get_tip_max_volumes(ops)

    hamilton_liquid_classes = [
      get_liquid_class(
        tip_volume=int(tmv),
        is_core=False,
        is_tip=True,
        has_filter=op.tip.has_filter,
        liquid_class=op.liquid_class,
        # jet if liquid height is known, or if we are dispensing to an empty well
        jet=(hasattr(op.resource, "tracker") and op.resource.tracker.get_used_volume() == 0) or
        (is_default(op.liquid_height) or is_not_default(op.liquid_height) and op.liquid_height > 0),
        # dispensing all, get_used_volume includes pending
        empty=op.tip.tracker.get_used_volume() == 0
      ) for tmv, op in zip(tip_max_volumes, ops)]

    # correct volumes using the liquid class
    for op, hlc in zip(ops, hamilton_liquid_classes):
      op.volume = hlc.compute_corrected_volume(op.volume) if hlc is not None else op.volume

    well_bottoms = [op.get_absolute_location().z + \
                    (op.offset.z if is_not_default(op.offset) else 0) for op in ops]
    liquid_surfaces_no_lld = [ls + (op.liquid_height if is_not_default(op.liquid_height) else 1)
                              for ls, op in zip(well_bottoms, ops)]
    lld_search_heights = [wb + op.resource.get_size_z() + 5 for wb, op in zip(well_bottoms, ops)]

    dispensing_mode = [{
      (False, True): 0,
      (True, True): 1,
      (True, False): 2,
      (False, False): 3,
    }[(op.tip.tracker.get_used_volume() == 0, # empty
      (is_default(op.liquid_height) or is_not_default(op.liquid_height) and op.liquid_height > 0) or
      (hasattr(op.resource, "tracker") and op.resource.tracker.get_used_volume() == 0))] # jet
      for op in ops]

    dispense_volumes = [int(op.volume*10) for op in ops]
    pull_out_distance_transport_air = _fill_in_defaults(pull_out_distance_transport_air, [100]*n)
    second_section_height = _fill_in_defaults(second_section_height, [32]*n)
    second_section_ratio = _fill_in_defaults(second_section_ratio, [6180]*n)
    minimum_height = _fill_in_defaults(minimum_height,
      default=[int((ls+5) * 10) for ls in liquid_surfaces_no_lld])
    immersion_depth = _fill_in_defaults(immersion_depth, [0]*n)
    immersion_depth_direction = _fill_in_defaults(immersion_depth_direction, [0]*n)
    surface_following_distance = _fill_in_defaults(surface_following_distance, [0]*n)
    flow_rates = [
      get_value(op.flow_rate, default=hlc.aspiration_flow_rate if hlc is not None else 120)
        for op, hlc in zip(ops, hamilton_liquid_classes)]
    dispense_speed = [int(fr*10) for fr in flow_rates]
    cut_off_speed = _fill_in_defaults(cut_off_speed, [50]*n)
    stop_back_volume = _fill_in_defaults(stop_back_volume,
      default=[int(hlc.dispense_stop_back_volume*10) if hlc is not None else 0
      for hlc in hamilton_liquid_classes])
    transport_air_volume = _fill_in_defaults(transport_air_volume,
      default=[int(hlc.dispense_air_transport_volume*10) if hlc is not None else 0
      for hlc in hamilton_liquid_classes])
    blow_out_air_volume = _fill_in_defaults(blow_out_air_volume,
      default=[int(hlc.dispense_blow_out_volume*10) if hlc is not None else 0
       for hlc in hamilton_liquid_classes])
    lld_mode = _fill_in_defaults(lld_mode, [0]*n)
    dispense_position_above_z_touch_off = _fill_in_defaults(dispense_position_above_z_touch_off,
      default=[0]*n)
    gamma_lld_sensitivity = _fill_in_defaults(gamma_lld_sensitivity, [1]*n)
    dp_lld_sensitivity = _fill_in_defaults(dp_lld_sensitivity, [1]*n)
    swap_speed = _fill_in_defaults(swap_speed,
      default=[int(hlc.dispense_swap_speed*10) if hlc is not None else 0
        for hlc in hamilton_liquid_classes])
    settling_time = _fill_in_defaults(settling_time,
      default=[int(hlc.dispense_settling_time*10) if hlc is not None else 0
        for hlc in hamilton_liquid_classes])
    mix_volume = _fill_in_defaults(mix_volume, [0]*n)
    mix_cycles = _fill_in_defaults(mix_cycles, [0]*n)
    mix_position_from_liquid_surface = _fill_in_defaults(mix_position_from_liquid_surface, [0]*n)
    mix_speed = _fill_in_defaults(mix_speed,
      default=[int(hlc.dispense_mix_flow_rate*10) if hlc is not None else 0
        for hlc in hamilton_liquid_classes])
    mix_surface_following_distance = _fill_in_defaults(mix_surface_following_distance, [0]*n)
    limit_curve_index = _fill_in_defaults(limit_curve_index, [0]*n)

    try:
      ret = await self.dispense_pip(
        tip_pattern=channels_involved,
        x_positions=x_positions,
        y_positions=y_positions,

        dispensing_mode=dispensing_mode,
        dispense_volumes=dispense_volumes,
        lld_search_height=[int(sh*10) for sh in lld_search_heights],
        liquid_surface_no_lld=[int(ls*10) for ls in liquid_surfaces_no_lld],
        pull_out_distance_transport_air=pull_out_distance_transport_air,
        second_section_height=second_section_height,
        second_section_ratio=second_section_ratio,
        minimum_height=minimum_height,
        immersion_depth=immersion_depth,
        immersion_depth_direction=immersion_depth_direction,
        surface_following_distance=surface_following_distance,
        dispense_speed=dispense_speed,
        cut_off_speed=cut_off_speed,
        stop_back_volume=stop_back_volume,
        transport_air_volume=transport_air_volume,
        blow_out_air_volume=[int(op.blow_out_air_volume*10) for op in ops],
        lld_mode=lld_mode,
        dispense_position_above_z_touch_off=dispense_position_above_z_touch_off,
        gamma_lld_sensitivity=gamma_lld_sensitivity,
        dp_lld_sensitivity=dp_lld_sensitivity,
        swap_speed=swap_speed,
        settling_time=settling_time,
        mix_volume=mix_volume,
        mix_cycles=mix_cycles,
        mix_position_from_liquid_surface=mix_position_from_liquid_surface,
        mix_speed=mix_speed,
        mix_surface_following_distance=mix_surface_following_distance,
        limit_curve_index=limit_curve_index,

        minimum_traverse_height_at_beginning_of_a_command=
          minimum_traverse_height_at_beginning_of_a_command,
        min_z_endpos=min_z_endpos,
        side_touch_off_distance=side_touch_off_distance,
      )
    except HamiltonFirmwareError as e:
      tll: List[int] = []
      for i in range(1, self.num_channels+1):
        channel_error = e.error_for_channel(i)
        if channel_error is None:
          continue
        if channel_error.trace_information in [54]: # "Position out of permitted area" = too little
          tll.append(i-1)

      if len(tll) > 0:
        raise \
          TipTooLittleVolumeError(f"There is not enough liquid in the following channels: {tll}") \
          from e

      raise e

    return ret

  @need_iswap_parked
  async def pick_up_tips96(
    self,
    pickup: PickupTipRack,
    tip_pickup_method: int = 0,
    z_deposit_position: int = 2164,
    minimum_height_command_end: int = 2450,
    minimum_traverse_height_at_beginning_of_a_command: int = 2450
  ):
    """ Pick up tips using the 96 head. """
    assert self.core96_head_installed, "96 head must be installed"
    tip_spot_a1 = pickup.resource.get_item("A1")
    tip_a1 = tip_spot_a1.get_tip()
    assert isinstance(tip_a1, HamiltonTip), "Tip type must be HamiltonTip."
    ttti = await self.get_or_assign_tip_type_index(tip_a1)
    position = tip_spot_a1.get_absolute_location() + tip_spot_a1.center() + pickup.offset

    return await self.pick_up_tips_core96(
      x_position=int(position.x * 10),
      x_direction=0,
      y_position=int(position.y * 10),
      tip_type_idx=ttti,
      tip_pickup_method=tip_pickup_method,
      z_deposit_position=z_deposit_position,
      minimum_height_command_end=minimum_height_command_end,
      minimum_traverse_height_at_beginning_of_a_command=
        minimum_traverse_height_at_beginning_of_a_command,
    )

  @need_iswap_parked
  async def drop_tips96(
    self,
    drop: DropTipRack,
    z_deposit_position: int = 2164,
    minimum_height_command_end: int = 2450,
    minimum_traverse_height_at_beginning_of_a_command: int = 2450
  ):
    """ Drop tips from the 96 head. """
    assert self.core96_head_installed, "96 head must be installed"
    tip_a1 = drop.resource.get_item("A1")
    position = tip_a1.get_absolute_location() + tip_a1.center() + drop.offset

    return await self.discard_tips_core96(
      x_position=int(position.x * 10),
      x_direction=0,
      y_position=int(position.y * 10),
      z_deposit_position=z_deposit_position,
      minimum_height_command_end=minimum_height_command_end,
      minimum_traverse_height_at_beginning_of_a_command=
        minimum_traverse_height_at_beginning_of_a_command,
    )

  @need_iswap_parked
  async def aspirate96(
    self,
    aspiration: AspirationPlate,
    blow_out_air_volume: float = 0,
    use_lld: bool = False,
    liquid_height: float = 2,
    air_transport_retract_dist: float = 10,

    aspiration_type: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 2450,
    minimal_end_height: int = 2450,
    lld_search_height: int = 1999,
    maximum_immersion_depth: int = 1269,
    tube_2nd_section_height_measured_from_zm: int = 32,
    tube_2nd_section_ratio: int = 6180,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_aspiration: int = 0,
    transport_air_volume: int = 50,
    pre_wetting_volume: int = 50,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 20,
    settling_time: int = 10,
    homogenization_volume: int = 0,
    homogenization_cycles: int = 0,
    homogenization_position_from_liquid_surface: int = 0,
    surface_following_distance_during_homogenization: int = 0,
    speed_of_homogenization: int = 1200,
    limit_curve_index: int = 0,
  ):
    """ Aspirate using the Core96 head.

    .. warning:: The parameters in this method, with the exception of `ops` and `use_channels`,
      expect units of tenths of 'millimeters' (i.e. 10 = 1 mm), or tenths of 'microliters' (i.e. 10
      = 1 ul), or tenths of seconds. Speeds are in 0.1ul/s. This is a deviation from the rest of the
      API, which uses SI units. This is because the Hamilton API uses these units.

    Args:
      aspiration: The aspiration to perform.
      blow_out_air_volume: The volume of air to blow out after aspiration, in microliters.
      use_lld: If True, use gamma liquid level detection. If False, use liquid height.
      liquid_height: The height of the liquid above the bottom of the well, in millimeters.
      air_transport_retract_dist: The distance to retract after aspirating, in millimeters.

      aspiration_type: The type of aspiration to perform. (0 = simple; 1 = sequence; 2 = cup emptied
        )
      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to before
        starting the command.
      minimal_end_height: The minimum height to move to after the command.
      lld_search_height: The height to search for the liquid level.
      maximum_immersion_depth: The maximum immersion depth.
      tube_2nd_section_height_measured_from_zm: Unknown.
      tube_2nd_section_ratio: Unknown.
      immersion_depth: The immersion depth above or below the liquid level. See
       `immersion_depth_direction`.
      immersion_depth_direction: The direction of the immersion depth. (0 = deeper, 1 = out of
        liquid)
      transport_air_volume: The volume of air to aspirate after the liquid.
      pre_wetting_volume: The volume of liquid to use for pre-wetting.
      gamma_lld_sensitivity: The sensitivity of the gamma liquid level detection.
      swap_speed: unknown.
      settling_time: The time to wait after aspirating.
      homogenization_volume: The volume of liquid to aspirate for homogenization.
      homogenization_cycles: The number of cycles to perform for homogenization.
      homogenization_position_from_liquid_surface: The position of the homogenization from the
        liquid surface.
      surface_following_distance_during_homogenization: The distance to follow the liquid surface
        during homogenization.
      speed_of_homogenization: The speed of homogenization.
      limit_curve_index: The index of the limit curve to use.
    """

    assert self.core96_head_installed, "96 head must be installed"

    assert isinstance(aspiration.resource, Plate), "Only ItemizedResource is supported."
    well_a1 = aspiration.resource.get_item("A1")
    position = well_a1.get_absolute_location() + well_a1.center()

    liquid_height = aspiration.resource.get_absolute_location().z + liquid_height

    flow_rate = int(get_value(aspiration.flow_rate, 250)*10)

    aspiration_volumes = int(aspiration.volume * 10)

    channel_pattern = [True]*12*8

    liquid_surface_at_function_without_lld = int(liquid_height * 10)
    pull_out_distance_to_take_transport_air_in_function_without_lld = \
      int(air_transport_retract_dist * 10)

    # Unfortunately, `blow_out_air_volume` does not work correctly, so instead we aspirate air
    # manually.
    if blow_out_air_volume is not None and blow_out_air_volume > 0:
      await self.aspirate_core_96(
        x_position=int(position.x * 10),
        y_positions=int(position.y * 10),
        lld_mode=0,
        liquid_surface_at_function_without_lld=int((liquid_height + 30) * 10),
        aspiration_volumes=int(blow_out_air_volume * 10)
      )

    return await self.aspirate_core_96(
      x_position=int(position.x * 10),
      x_direction=0,
      y_positions=int(position.y * 10),
      aspiration_type=aspiration_type,

      minimum_traverse_height_at_beginning_of_a_command=
       minimum_traverse_height_at_beginning_of_a_command,
      minimal_end_height=minimal_end_height,
      lld_search_height=lld_search_height,
      liquid_surface_at_function_without_lld=liquid_surface_at_function_without_lld,
      pull_out_distance_to_take_transport_air_in_function_without_lld=
       pull_out_distance_to_take_transport_air_in_function_without_lld,
      maximum_immersion_depth=maximum_immersion_depth,
      tube_2nd_section_height_measured_from_zm=tube_2nd_section_height_measured_from_zm,
      tube_2nd_section_ratio=tube_2nd_section_ratio,
      immersion_depth=immersion_depth,
      immersion_depth_direction=immersion_depth_direction,
      liquid_surface_sink_distance_at_the_end_of_aspiration=
       liquid_surface_sink_distance_at_the_end_of_aspiration,
      aspiration_volumes=aspiration_volumes,
      aspiration_speed=flow_rate,
      transport_air_volume=transport_air_volume,
      blow_out_air_volume=0,
      pre_wetting_volume=pre_wetting_volume,
      lld_mode=int(use_lld),
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      swap_speed=swap_speed,
      settling_time=settling_time,
      homogenization_volume=homogenization_volume,
      homogenization_cycles=homogenization_cycles,
      homogenization_position_from_liquid_surface=
       homogenization_position_from_liquid_surface,
      surface_following_distance_during_homogenization=
       surface_following_distance_during_homogenization,
      speed_of_homogenization=speed_of_homogenization,
      channel_pattern=channel_pattern,
      limit_curve_index=limit_curve_index,
      tadm_algorithm=False,
      recording_mode=0,
    )

  @need_iswap_parked
  async def dispense96(
    self,
    dispense: DispensePlate,
    jet: bool = False,
    blow_out: bool = True, # TODO: do we need this if we can just check if blow_out_air_volume > 0?
    liquid_height: float = 2,
    dispense_mode=3,
    air_transport_retract_dist=10,
    blow_out_air_volume: Optional[float] = None,
    use_lld: bool = False,

    minimum_traverse_height_at_beginning_of_a_command: int = 2450,
    minimal_end_height: int = 2450,
    lld_search_height: int = 1999,
    maximum_immersion_depth: int = 1869,
    tube_2nd_section_height_measured_from_zm: int = 32,
    tube_2nd_section_ratio: int = 6180,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_dispense: int = 0,
    transport_air_volume: int = 50,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 20,
    settling_time: int = 0,
    mixing_volume: int = 0,
    mixing_cycles: int = 0,
    mixing_position_from_liquid_surface: int = 0,
    surface_following_distance_during_mixing: int = 0,
    speed_of_mixing: int = 1200,
    limit_curve_index: int = 0,
    cut_off_speed: int = 50,
    stop_back_volume: int = 0,
  ):
    """ Aspirate using the Core96 head.

    .. warning:: The parameters in this method, with the exception of `ops` and `use_channels`,
      expect units of tenths of 'millimeters' (i.e. 10 = 1 mm), or tenths of 'microliters' (i.e. 10
      = 1 ul), or tenths of seconds. Speeds are in 0.1ul/s. This is a deviation from the rest of the
      API, which uses SI units. This is because the Hamilton API uses these units.

    Args:
      dispense: The Dispense command to execute.
      jet: Whether to use jet dispense mode.
      blow_out: Whether to blow out after dispensing.
      liquid_height: The height of the liquid in the well, in mm. Used if LLD is not used.
      dispense_mode: The dispense mode to use. 0 = Partial volume in jet mode 1 = Blow out in jet
        mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix position
      air_transport_retract_dist: The distance to retract after dispensing, in mm.
      blow_out_air_volume: The volume of air to blow out after dispensing, in ul.
      use_lld: Whether to use gamma LLD.

      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command, in mm.
      minimal_end_height: Minimal end height, in mm.
      lld_search_height: LLD search height, in mm.
      maximum_immersion_depth: Maximum immersion depth, in mm. Equals Minimum height during command.
      tube_2nd_section_height_measured_from_zm: Unknown.
      tube_2nd_section_ratio: Unknown.
      immersion_depth: Immersion depth, in mm. See `immersion_depth_direction`.
      immersion_depth_direction: Immersion depth direction. 0 = go deeper, 1 = go up out of liquid.
      liquid_surface_sink_distance_at_the_end_of_dispense: Unknown.
      transport_air_volume: Transport air volume, to dispense before aspiration.
      gamma_lld_sensitivity: Gamma LLD sensitivity.
      swap_speed: Unknown.
      settling_time: Settling time, in 0.1 seconds.
      mixing_volume: Mixing volume, in ul.
      mixing_cycles: Mixing cycles.
      mixing_position_from_liquid_surface: Mixing position from liquid surface, in mm.
      surface_following_distance_during_mixing: Surface following distance during mixing, in mm.
      speed_of_mixing: Speed of mixing, in 0.1 ul/s.
      limit_curve_index: Limit curve index.
      cut_off_speed: Unknown.
      stop_back_volume: Unknown.
    """

    assert self.core96_head_installed, "96 head must be installed"

    assert isinstance(dispense.resource, Plate), "Only ItemizedResource is supported."
    well_a1 = dispense.resource.get_item("A1")
    position = well_a1.get_absolute_location() + well_a1.center()

    liquid_height = dispense.resource.get_absolute_location().z + liquid_height

    dispense_mode = {
      (True, False): 0,
      (True, True): 1,
      (False, False): 2,
      (False, True): 3,
    }[(jet, blow_out)]

    flow_rate = get_value(dispense.flow_rate, 120)

    liquid_surface_at_function_without_lld: int = int(liquid_height*10)
    pull_out_distance_to_take_transport_air_in_function_without_lld = air_transport_retract_dist*10

    channel_pattern = [True]*12*8

    ret = await self.dispense_core_96(
      dispensing_mode=dispense_mode,
      x_position=int(position.x * 10),
      x_direction=0,
      y_position=int(position.y * 10),

      minimum_traverse_height_at_beginning_of_a_command=
        minimum_traverse_height_at_beginning_of_a_command,
      minimal_end_height=minimal_end_height,
      lld_search_height=lld_search_height,
      liquid_surface_at_function_without_lld=
        liquid_surface_at_function_without_lld,
      pull_out_distance_to_take_transport_air_in_function_without_lld=
        pull_out_distance_to_take_transport_air_in_function_without_lld,
      maximum_immersion_depth=maximum_immersion_depth,
      tube_2nd_section_height_measured_from_zm=tube_2nd_section_height_measured_from_zm,
      tube_2nd_section_ratio=tube_2nd_section_ratio,
      immersion_depth=immersion_depth,
      immersion_depth_direction=immersion_depth_direction,
      liquid_surface_sink_distance_at_the_end_of_dispense=
        liquid_surface_sink_distance_at_the_end_of_dispense,
      dispense_volume=int(dispense.volume * 10),
      dispense_speed=int(flow_rate * 10),
      transport_air_volume=transport_air_volume,
      blow_out_air_volume=0,
      lld_mode=int(use_lld),
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      swap_speed=swap_speed,
      settling_time=settling_time,
      mixing_volume=mixing_volume,
      mixing_cycles=mixing_cycles,
      mixing_position_from_liquid_surface=mixing_position_from_liquid_surface,
      surface_following_distance_during_mixing=surface_following_distance_during_mixing,
      speed_of_mixing=speed_of_mixing,
      channel_pattern=channel_pattern,
      limit_curve_index=limit_curve_index,
      tadm_algorithm=False,
      recording_mode=0,
      cut_off_speed=cut_off_speed,
      stop_back_volume=stop_back_volume,
    )

    # Unfortunately, `blow_out_air_volume` does not work correctly, so instead we dispense air
    # manually.
    if blow_out_air_volume is not None and blow_out_air_volume > 0:
      await self.dispense_core_96(
        x_position=int(position.x * 10),
        y_position=int(position.y * 10),
        lld_mode=0,
        liquid_surface_at_function_without_lld=int((liquid_height + 30) * 10),
        dispense_volume=int(blow_out_air_volume * 10),
      )

    return ret

  async def pick_up_resource(
    self,
    resource: Resource,
    grip_direction: GripDirection,
    pickup_distance_from_top: float,
    offset: Coordinate = Coordinate.zero(),
    minimum_traverse_height_at_beginning_of_a_command: int = 2840,
    z_position_at_the_command_end: int = 2840,
    grip_strength: int = 4,
    plate_width_tolerance: int = 20,
    collision_control_level: int = 0,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
    fold_up_sequence_at_the_end_of_process: bool = True
  ):
    """ Pick up a resource using iSWAP """

    assert self.iswap_installed, "iswap must be installed"

    # Get center of source plate. Also gripping height and plate width.
    center = resource.get_absolute_location() + resource.center() + offset
    grip_height = center.z + resource.get_size_z() - pickup_distance_from_top
    plate_width = resource.get_size_x()
    # plate_width = { # TODO: LH should rotate resources on move_plate
    #   GripDirection.FRONT: resource.get_size_x(),
    #   GripDirection.RIGHT: resource.get_size_y(),
    #   GripDirection.BACK: resource.get_size_x(),
    #   GripDirection.LEFT: resource.get_size_y(),
    # }[grip_direction]

    await self.get_plate(
      x_position=int(center.x * 10),
      x_direction=0,
      y_position=int(center.y * 10),
      y_direction=0,
      z_position=int(grip_height * 10),
      z_direction=0,
      grip_direction={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      minimum_traverse_height_at_beginning_of_a_command=
        minimum_traverse_height_at_beginning_of_a_command,
      z_position_at_the_command_end=z_position_at_the_command_end,
      grip_strength=grip_strength,
      open_gripper_position=int(plate_width*10) + 30,
      plate_width=int(plate_width*10) - 33,
      plate_width_tolerance=plate_width_tolerance,
      collision_control_level=collision_control_level,
      acceleration_index_high_acc=acceleration_index_high_acc,
      acceleration_index_low_acc=acceleration_index_low_acc,
      fold_up_sequence_at_the_end_of_process=fold_up_sequence_at_the_end_of_process
    )

  async def move_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    grip_direction: GripDirection,
    minimum_traverse_height_at_beginning_of_a_command: int = 2840,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1
  ):
    """ After a resource is picked up, move it to a new location but don't release it yet. """

    assert self.iswap_installed, "iswap must be installed"

    center = location + resource.center()

    await self.move_plate_to_position(
      x_position=int(center.x * 10),
      x_direction=0,
      y_position=int(center.y * 10),
      y_direction=0,
      z_position=int((location.z + resource.get_size_z() / 2) * 10),
      z_direction=0,
      grip_direction={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      minimum_traverse_height_at_beginning_of_a_command=
        minimum_traverse_height_at_beginning_of_a_command,
      collision_control_level=collision_control_level,
      acceleration_index_high_acc=acceleration_index_high_acc,
      acceleration_index_low_acc=acceleration_index_low_acc
    )

  async def release_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    offset: Coordinate,
    grip_direction: GripDirection,
    pickup_distance_from_top: float,
    minimum_traverse_height_at_beginning_of_a_command: int = 2840,
    z_position_at_the_command_end: int = 2840,
    collision_control_level: int = 0,
  ):
    """ After a resource is picked up, release it at the specified location. """

    assert self.iswap_installed, "iswap must be installed"

    # Get center of source plate. Also gripping height and plate width.
    center = location + resource.center() + offset
    grip_height = center.z + resource.get_size_z() - pickup_distance_from_top
    plate_width = resource.get_size_x()
    # plate_width = { # TODO: LH should rotate resources on move_plate
    #   GripDirection.FRONT: resource.get_size_x(),
    #   GripDirection.RIGHT: resource.get_size_y(),
    #   GripDirection.BACK: resource.get_size_x(),
    #   GripDirection.LEFT: resource.get_size_y(),
    # }[grip_direction]

    await self.put_plate(
      x_position=int(center.x * 10),
      x_direction=0,
      y_position=int(center.y * 10),
      y_direction=0,
      z_position=int(grip_height * 10),
      z_direction=0,
      grip_direction={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      minimum_traverse_height_at_beginning_of_a_command=
        minimum_traverse_height_at_beginning_of_a_command,
      z_position_at_the_command_end=z_position_at_the_command_end,
      open_gripper_position=int(plate_width*10) + 30,
      collision_control_level=collision_control_level,
    )

  async def move_resource(self, move: Move):
    """ Pick up a resource and move it to a new location.

    Note: this looks like an LH level method, but I am not sure if other robots support such modular
    control over the moves.
    """

    minimum_traverse_height = 284.0
    await self.pick_up_resource(
      resource=move.resource,
      grip_direction=move.get_direction,
      pickup_distance_from_top=move.pickup_distance_from_top,
      offset=move.resource_offset,
      minimum_traverse_height_at_beginning_of_a_command=int(minimum_traverse_height * 10))

    previous_location = move.resource.get_absolute_location() + move.resource_offset
    previous_location.z = minimum_traverse_height - move.resource.get_size_z() / 2
    for location in move.intermediate_locations:
      await self.move_picked_up_resource(
        location=location,
        resource=move.resource,
        grip_direction=move.get_direction,
        minimum_traverse_height_at_beginning_of_a_command=
          int(previous_location.z + move.resource.get_size_z() / 2) * 10, # "minimum" is a scam.
        collision_control_level=1,
        acceleration_index_high_acc=4,
        acceleration_index_low_acc=1)
      previous_location = location

    await self.release_picked_up_resource(
      location=move.to,
      resource=move.resource,
      offset=move.to_offset,
      grip_direction=move.put_direction,
      pickup_distance_from_top=move.pickup_distance_from_top,
      minimum_traverse_height_at_beginning_of_a_command=
        int(previous_location.z + move.resource.get_size_z() / 2) * 10, # "minimum" is a scam.
    )

  async def prepare_for_manual_channel_operation(self):
    """ Prepare for manual operation. """

    await self.position_max_free_y_for_n(pipetting_channel_index=self.num_channels)

  async def move_channel_x(self, channel: int, x: float): # pylint: disable=unused-argument
    """ Move a channel in the x direction. """
    await self.position_left_x_arm_(int(x * 10))

  async def move_channel_y(self, channel: int, y: float):
    """ Move a channel in the y direction. """
    await self.position_single_pipetting_channel_in_y_direction(
      pipetting_channel_index=channel + 1, y_position=int(y * 10))

  async def move_channel_z(self, channel: int, z: float):
    """ Move a channel in the z direction. """
    await self.position_single_pipetting_channel_in_z_direction(
      pipetting_channel_index=channel + 1, z_position=int(z*10))

  # ============== Firmware Commands ==============

  # -------------- 3.2 System general commands --------------

  async def pre_initialize_instrument(self):
    """ Pre-initialize instrument """
    return await self.send_command(module="C0", command="VI")

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod
  ):
    """ Tip/needle definition.

    Args:
      tip_type_table_index: tip_table_index
      filter: with(out) filter
      tip_length: Tip length [0.1mm]
      maximum_tip_volume: Maximum volume of tip [0.1ul]
                          Note! it's automatically limited to max. channel capacity
      tip_type: Type of tip collar (Tip type identification)
      pickup_method: pick up method.
                      Attention! The values set here are temporary and apply only until
                      power OFF or RESET. After power ON the default values apply. (see Table 3)
    """

    # pylint: disable=redefined-builtin

    assert 0 <= tip_type_table_index <= 99, "tip_type_table_index must be between 0 and 99"
    assert 0 <= tip_type_table_index <= 99, "tip_type_table_index must be between 0 and 99"
    assert 1 <= tip_length <= 1999, "tip_length must be between 1 and 1999"
    assert 1 <= maximum_tip_volume <= 56000, "maximum_tip_volume must be between 1 and 56000"

    return await self.send_command(
      module="C0",
      command="TT",
      tt=f"{tip_type_table_index:02}",
      tf=filter,
      tl=f"{tip_length:04}",
      tv=f"{maximum_tip_volume:05}",
      tg=tip_size.value,
      tu=pickup_method.value
    )

  # -------------- 3.2.1 System query --------------

  async def request_error_code(self):
    """ Request error code

    Here the last saved error messages can be retrieved. The error buffer is automatically voided
    when a new command is started. All configured nodes are displayed.

    Returns:
      TODO:
      X0##/##: X0 slave
      ..##/## see node definitions ( chapter 5)
    """

    return await self.send_command(module="C0", command="RE")

  async def request_firmware_version(self):
    """ Request firmware version

    Returns: TODO: Rfid0001rf1.0S 2009-06-24 A
    """

    return await self.send_command(module="C0", command="RF")

  async def request_parameter_value(self):
    """ Request parameter value

    Returns: TODO: Raid1111er00/00yg1200
    """

    return await self.send_command(module="C0", command="RA")

  class BoardType(enum.Enum):
    C167CR_SINGLE_PROCESSOR_BOARD = 0
    C167CR_DUAL_PROCESSOR_BOARD = 1
    LPC2468_XE167_DUAL_PROCESSOR_BOARD = 2
    LPC2468_SINGLE_PROCESSOR_BOARD = 5
    UNKNOWN = -1

  async def request_electronic_board_type(self):
    """ Request electronic board type

    Returns:
      The board type.
    """

    # pylint: disable=undefined-variable

    resp = await self.send_command(module="C0", command="QB", fmt="")
    try:
      return STAR.BoardType(resp["qb"])
    except ValueError:
      return STAR.BoardType.UNKNOWN

  # TODO: parse response.
  async def request_supply_voltage(self):
    """ Request supply voltage

    Request supply voltage (for LDPB only)
    """

    return await self.send_command(module="C0", command="MU")

  async def request_instrument_initialization_status(self) -> bool:
    """ Request instrument initialization status """

    resp = await self.send_command(module="C0", command="QW", fmt="qw#")
    return resp is not None and resp["qw"] == 1

  async def request_name_of_last_faulty_parameter(self):
    """ Request name of last faulty parameter

    Returns: TODO:
      Name of last parameter with syntax error
      (optional) received value separated with blank
      (optional) minimal permitted value separated with blank (optional)
      maximal permitted value separated with blank example with min max data:
      Vpid2233er00/00vpth 00000 03500 example without min max data: Vpid2233er00/00vpcd
    """

    return await self.send_command(module="C0", command="VP", fmt="vp&&")

  async def request_master_status(self):
    """ Request master status

    Returns: TODO: see page 19 (SFCO.0036)
    """

    return await self.send_command(module="C0", command="RQ")

  async def request_number_of_presence_sensors_installed(self):
    """ Request number of presence sensors installed

    Returns:
      number of sensors installed (1...103)
    """

    resp = await self.send_command(module="C0", command="SR")
    return resp["sr"]

  async def request_eeprom_data_correctness(self):
    """ Request EEPROM data correctness

    Returns: TODO: (SFCO.0149)
    """

    return await self.send_command(module="C0", command="QV")

  # -------------- 3.3 Settings --------------

  # -------------- 3.3.1 Volatile Settings --------------


  async def set_single_step_mode(
    self,
    single_step_mode: bool = False
  ):
    """ Set Single step mode

    Args:
      single_step_mode: Single Step Mode. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AM",
      am=single_step_mode,
    )

  async def trigger_next_step(self):
    """ Trigger next step (Single step mode) """

    # TODO: this command has no reply!!!!
    return await self.send_command(module="C0", command="NS")

  async def halt(self):
    """ Halt

    Intermediate sequences not yet carried out and the commands in
    the command stack are discarded. Sequence already in process is
    completed.
    """

    return await self.send_command(module="C0", command="HD")

  async def save_all_cycle_counters(self):
    """ Save all cycle counters

    Save all cycle counters of the instrument
    """

    return await self.send_command(module="C0", command="AZ")

  async def set_not_stop(self, non_stop):
    """ Set not stop mode

    Args:
      non_stop: True if non stop mode should be turned on after command is sent.
    """

    if non_stop:
      # TODO: this command has no reply!!!!
      return await self.send_command(module="C0", command="AB")
    else:
      return await self.send_command(module="C0", command="AW")

  # -------------- 3.3.2 Non volatile settings (stored in EEPROM) --------------

  async def store_installation_data(
    self,
    date: datetime.datetime = datetime.datetime.now(),
    serial_number: str = "0000"
  ):
    """ Store installation data

    Args:
      date: installation date.
    """

    assert len(serial_number) == 4, "serial number must be 4 chars long"

    return await self.send_command(
      module="C0",
      command="SI",
      si=date,
      sn=serial_number
    )

  async def store_verification_data(
    self,
    verification_subject: int = 0,
    date: datetime.datetime = datetime.datetime.now(),
    verification_status: bool = False
  ):
    """ Store verification data

    Args:
      verification_subject: verification subject. Default 0. Must be between 0 and 24.
      date: verification date.
      verification_status: verification status.
    """

    assert 0 <= verification_subject <= 24, "verification_subject must be between 0 and 24"

    return await self.send_command(
      module="C0",
      command="AV",
      vo=verification_subject,
      vd=date,
      vs=verification_status,
    )

  async def additional_time_stamp(self):
    """ Additional time stamp """

    return await self.send_command(module="C0", command="AT")

  async def set_x_offset_x_axis_iswap(self, x_offset: int):
    """ Set X-offset X-axis <-> iSWAP

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(
      module="C0",
      command="AG",
      x_offset=x_offset
    )

  async def set_x_offset_x_axis_core_96_head(self, x_offset: int):
    """ Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(
      module="C0",
      command="AF",
      x_offset=x_offset
    )

  async def set_x_offset_x_axis_core_nano_pipettor_head(self, x_offset: int):
    """ Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(
      module="C0",
      command="AF",
      x_offset=x_offset
    )

  async def save_download_date(
    self,
    date: datetime.datetime = datetime.datetime.now()
  ):
    """ Save Download date

    Args:
      date: download date. Default now.
    """

    return await self.send_command(
      module="C0",
      command="AO",
      ao=date,
    )

  async def save_technical_status_of_assemblies(
    self,
    processor_board: str,
    power_supply: str
  ):
    """ Save technical status of assemblies

    Args:
      processor_board: Processor board. Art.Nr./Rev./Ser.No. (000000/00/0000)
      power_supply: Power supply. Art.Nr./Rev./Ser.No. (000000/00/0000)
    """

    return await self.send_command(
      module="C0",
      command="BT",
      qt=processor_board + " " + power_supply,
    )

  async def set_instrument_configuration(
    self,
    configuration_data_1: Optional[str] = None, # TODO: configuration byte
    configuration_data_2: Optional[str] = None, # TODO: configuration byte
    configuration_data_3: Optional[str] = None, # TODO: configuration byte
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
        xl parameter bits). Must be between 0 and 1.  Default 0. # TODO: this.
      right_x_drive_configuration_byte_2: right X drive configuration byte 2 (see
        xn parameter bits). Must be between 0 and 1.  Default 0. # TODO: this.
      minimal_iswap_collision_free_position: minimal iSWAP collision free position for
        direct X access. For explanation of calculation see Fig. 4. Must be between 0 and 30000.
        Default 3500.
      maximal_iswap_collision_free_position: maximal iSWAP collision free position for
        direct X access. For explanation of calculation see Fig. 4. Must be between 0 and 30000.
        Default 11400
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

    assert 1 <= instrument_size_in_slots_x_range <= 9, \
      "instrument_size_in_slots_x_range must be between 1 and 99"
    assert 1 <= auto_load_size_in_slots <= 54, "auto_load_size_in_slots must be between 1 and 54"
    assert 1000 <= tip_waste_x_position <= 25000, "tip_waste_x_position must be between 1 and 25000"
    assert 0 <= right_x_drive_configuration_byte_1 <= 1, \
      "right_x_drive_configuration_byte_1 must be between 0 and 1"
    assert 0 <= right_x_drive_configuration_byte_2 <= 1, \
      "right_x_drive_configuration_byte_2 must be between 0 and  must1"
    assert 0 <= minimal_iswap_collision_free_position <= 30000, \
      "minimal_iswap_collision_free_position must be between 0 and 30000"
    assert 0 <= maximal_iswap_collision_free_position <= 30000, \
      "maximal_iswap_collision_free_position must be between 0 and 30000"
    assert 0 <= left_x_arm_width <= 9999, "left_x_arm_width must be between 0 and 9999"
    assert 0 <= right_x_arm_width <= 9999, "right_x_arm_width must be between 0 and 9999"
    assert 0 <= num_pip_channels <= 16, "num_pip_channels must be between 0 and 16"
    assert 0 <= num_xl_channels <= 8, "num_xl_channels must be between 0 and 8"
    assert 0 <= num_robotic_channels <= 8, "num_robotic_channels must be between 0 and 8"
    assert 0 <= minimal_raster_pitch_of_pip_channels <= 999, \
     "minimal_raster_pitch_of_pip_channels must be between 0 and 999"
    assert 0 <= minimal_raster_pitch_of_xl_channels <= 999, \
      "minimal_raster_pitch_of_xl_channels must be between 0 and 999"
    assert 0 <= minimal_raster_pitch_of_robotic_channels <= 999, \
      "minimal_raster_pitch_of_robotic_channels must be between 0 and 999"
    assert 0 <= pip_maximal_y_position <= 9999, \
      "pip_maximal_y_position must be between 0 and 9999"
    assert 0 <= left_arm_minimal_y_position <= 9999, \
      "left_arm_minimal_y_position must be between 0 and 9999"
    assert 0 <= right_arm_minimal_y_position <= 9999, \
      "right_arm_minimal_y_position must be between 0 and 9999"

    return await self.send_command(
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

  async def save_pip_channel_validation_status(
    self,
    validation_status: bool = False
  ):
    """ Save PIP channel validation status

    Args:
      validation_status: PIP channel validation status. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AJ",
      tq=validation_status,
    )

  async def save_xl_channel_validation_status(
    self,
    validation_status: bool = False
  ):
    """ Save XL channel validation status

    Args:
      validation_status: XL channel validation status. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AE",
      tx=validation_status,
    )

  # TODO: response
  async def configure_node_names(self):
    """ Configure node names """

    return await self.send_command(module="C0", command="AJ")

  async def set_deck_data(
    self,
    data_index: int = 0,
    data_stream: str = "0"
  ):
    """ set deck data

    Args:
      data_index: data index. Must be between 0 and 9. Default 0.
      data_stream: data stream (12 characters). Default <class 'str'>.
    """

    assert 0 <= data_index <= 9, "data_index must be between 0 and 9"
    assert len(data_stream) == 12, "data_stream must be 12 chars"

    return await self.send_command(
      module="C0",
      command="DD",
      vi=data_index,
      vj=data_stream,
    )

  # -------------- 3.3.3 Settings query (stored in EEPROM) --------------

  async def request_technical_status_of_assemblies(self):
    """ Request Technical status of assemblies """

    # TODO: parse res
    return await self.send_command(module="C0", command="QT")

  async def request_installation_data(self):
    """ Request installation data """

    # TODO: parse res
    return await self.send_command(module="C0", command="RI")

  async def request_download_date(self):
    """ Request download date """

    # TODO: parse res
    return await self.send_command(module="C0", command="RO")

  async def request_verification_data(
    self,
    verification_subject: int = 0
  ):
    """ Request download date

    Args:
      verification_subject: verification subject. Must be between 0 and 24. Default 0.
    """

    assert 0 <= verification_subject <= 24, "verification_subject must be between 0 and 24"

    # TODO: parse results.
    return await self.send_command(
      module="C0",
      command="RO",
      vo = verification_subject
    )

  async def request_additional_timestamp_data(self):
    """ Request additional timestamp data """

    # TODO: parse res
    return await self.send_command(module="C0", command="RS")

  async def request_pip_channel_validation_status(self):
    """ Request PIP channel validation status """

    # TODO: parse res
    return await self.send_command(module="C0", command="RJ")

  async def request_xl_channel_validation_status(self):
    """ Request XL channel validation status """

    # TODO: parse res
    return await self.send_command(module="C0", command="UJ")

  async def request_machine_configuration(self):
    """ Request machine configuration """

    # TODO: parse res
    return await self.send_command(module="C0", command="RM")

  async def request_extended_configuration(self):
    """ Request extended configuration """

    return await self.send_command(module="C0", command="QM",
      fmt="ka******ke********xt##xa##xw#####xl**xn**xr**xo**xm#####xx#####xu####xv####kc#kr#ys###"+\
      "kl###km###ym####yu####yx####")

  async def request_node_names(self):
    """ Request node names """

    # TODO: parse res
    return await self.send_command(module="C0", command="RK")

  async def request_deck_data(self):
    """ Request deck data """

    # TODO: parse res
    return await self.send_command(module="C0", command="VD")

  # -------------- 3.4 X-Axis control --------------

  # -------------- 3.4.1 Movements --------------

  async def position_left_x_arm_(
    self,
    x_position: int = 0
  ):
    """ Position left X-Arm

    Collision risk!

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    assert 0 <= x_position <= 30000, "x_position_ must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="JX",
      xs=f"{x_position:05}",
    )

  async def position_right_x_arm_(
    self,
    x_position: int = 0
  ):
    """ Position right X-Arm

    Collision risk!

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    assert 0 <= x_position <= 30000, "x_position_ must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="JS",
      xs=f"{x_position:05}",
    )

  async def move_left_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self,
    x_position: int = 0
  ):
    """ Move left X-arm to position with all attached components in Z-safety position

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="KX",
      xs=x_position,
    )

  async def move_right_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self,
    x_position: int = 0
  ):
    """ Move right X-arm to position with all attached components in Z-safety position

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="KR",
      xs=x_position,
    )

  # -------------- 3.4.2 X-Area reservation for external access --------------

  async def occupy_and_provide_area_for_external_access(
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

    assert 0 <= taken_area_identification_number <= 9999, \
      "taken_area_identification_number must be between 0 and 9999"
    assert 0 <= taken_area_left_margin <= 99, "taken_area_left_margin must be between 0 and 99"
    assert 0 <= taken_area_left_margin_direction <= 1, \
      "taken_area_left_margin_direction must be between 0 and 1"
    assert 0 <= taken_area_size <= 50000, "taken_area_size must be between 0 and 50000"
    assert 0 <= arm_preposition_mode_related_to_taken_areas <= 2, \
      "arm_preposition_mode_related_to_taken_areas must be between 0 and 2"

    return await self.send_command(
      module="C0",
      command="BA",
      aq=taken_area_identification_number,
      al=taken_area_left_margin,
      ad=taken_area_left_margin_direction,
      ar=taken_area_size,
      ap=arm_preposition_mode_related_to_taken_areas,
    )

  async def release_occupied_area(
    self,
    taken_area_identification_number: int = 0
  ):
    """ Release occupied area

    Args:
      taken_area_identification_number: taken area identification number.
                                        Must be between 0 and 9999. Default 0.
    """

    assert 0 <= taken_area_identification_number <= 999, \
      "taken_area_identification_number must be between 0 and 9999"

    return await self.send_command(
      module="C0",
      command="BB",
      aq=taken_area_identification_number,
    )

  async def release_all_occupied_areas(self):
    """ Release all occupied areas """

    return await self.send_command(module="C0", command="BC")

  # -------------- 3.4.3 X-query --------------

  async def request_left_x_arm_position(self):
    """ Request left X-Arm position """

    return await self.send_command(module="C0", command="RX", fmt="rx#####")

  async def request_right_x_arm_position(self):
    """ Request right X-Arm position """

    return await self.send_command(module="C0", command="QX", fmt="rx#####")

  async def request_maximal_ranges_of_x_drives(self):
    """ Request maximal ranges of X drives """

    return await self.send_command(module="C0", command="RU")

  async def request_present_wrap_size_of_installed_arms(self):
    """ Request present wrap size of installed arms """

    return await self.send_command(module="C0", command="UA")

  async def request_left_x_arm_last_collision_type(self):
    """ Request left X-Arm last collision type (after error 27)

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    resp = await self.send_command(module="C0", command="XX", fmt="xq#")
    return resp["xq"] == 1

  async def request_right_x_arm_last_collision_type(self) -> bool:
    """ Request right X-Arm last collision type (after error 27)

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    resp = await self.send_command(module="C0", command="XR", fmt="xq#")
    return cast(int, resp["xq"]) == 1

  # -------------- 3.5 Pipetting channel commands --------------

  # -------------- 3.5.1 Initialization --------------

  async def initialize_pipetting_channels(
    self,
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    begin_of_tip_deposit_process: int = 0,
    end_of_tip_deposit_process: int = 0,
    z_position_at_end_of_a_command: int = 3600,
    tip_pattern: List[bool] = [True],
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

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert 0 <= begin_of_tip_deposit_process <= 3600, \
      "begin_of_tip_deposit_process must be between 0 and 3600"
    assert 0 <= end_of_tip_deposit_process <= 3600, \
      "end_of_tip_deposit_process must be between 0 and 3600"
    assert 0 <= z_position_at_end_of_a_command <= 3600, \
      "z_position_at_end_of_a_command must be between 0 and 3600"
    assert 0 <= tip_type <= 99, "tip must be between 0 and 99"
    assert 0 <= discarding_method <= 1, "discarding_method must be between 0 and 1"

    return await self.send_command(
      module="C0",
      command="DI",
      read_timeout=120,
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

  async def pick_up_tip(
    self,
    x_positions: List[int],
    y_positions: List[int],
    tip_pattern: List[bool],
    tip_type_idx: int,
    begin_tip_pick_up_process: int = 0,
    end_tip_pick_up_process: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    pickup_method: TipPickupMethod = TipPickupMethod.OUT_OF_RACK
  ):
    """ Tip Pick-up

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved).
      tip_type_idx: Tip type.
      begin_tip_pick_up_process: Begin of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      end_tip_pick_up_process: End of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      pickup_method: Pick up method.
    """

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert 0 <= begin_tip_pick_up_process <= 3600, \
      "begin_tip_pick_up_process must be between 0 and 3600"
    assert 0 <= end_tip_pick_up_process <= 3600, \
      "end_tip_pick_up_process must be between 0 and 3600"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="TP",
      tip_pattern=tip_pattern,
      read_timeout=60,
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tt=f"{tip_type_idx:02}",
      tp=f"{begin_tip_pick_up_process:04}",
      tz=f"{end_tip_pick_up_process:04}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      td=pickup_method.value,
    )

  async def discard_tip(
    self,
    x_positions: List[int],
    y_positions: List[int],
    tip_pattern: List[bool],
    begin_tip_deposit_process: int = 0,
    end_tip_deposit_process: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_end_of_a_command: int = 3600,
    discarding_method: TipDropMethod = TipDropMethod.DROP
  ):
    """ discard tip

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved). Must be between 0 and 1. Default 1.
      begin_tip_deposit_process: Begin of tip deposit process (Z- range) [0.1mm]. Must be between
          0 and 3600. Default 0.
      end_tip_deposit_process: End of tip deposit process (Z- range) [0.1mm]. Must be between 0
          and 3600.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
          command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must
          be between 0 and 3600.
      z-position_at_end_of_a_command: Z-Position at end of a command [0.1mm].
          Must be between 0 and 3600.
      discarding_method: Pick up method Pick up method. 0 = auto selection (see command TT
          parameter tu) 1 = pick up out of rack. 2 = pick up out of wash liquid (slowly). Must be
          between 0 and 2.

    If discarding is PLACE_SHIFT (0), tp/ tz = tip cone end height.
    Otherwise, tp/ tz = stop disk height.
    """

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert 0 <= begin_tip_deposit_process <= 3600, \
      "begin_tip_deposit_process must be between 0 and 3600"
    assert 0 <= end_tip_deposit_process <= 3600, \
      "end_tip_deposit_process must be between 0 and 3600"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= z_position_at_end_of_a_command <= 3600, \
      "z_position_at_end_of_a_command must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="TR",
      tip_pattern=tip_pattern,
      fmt="kz### (n)vz### (n)",
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tp=begin_tip_deposit_process,
      tz=end_tip_deposit_process,
      th=minimum_traverse_height_at_beginning_of_a_command,
      te=z_position_at_end_of_a_command,
      ti=discarding_method.value,
    )

  # TODO:(command:TW) Tip Pick-up for DC wash procedure

  # -------------- 3.5.3 Liquid handling commands using PIP --------------

  # TODO:(command:DC) Set multiple dispense values using PIP

  async def aspirate_pip(
    self,
    aspiration_type: List[int] = [0],
    tip_pattern: List[bool] = [True],
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600,
    lld_search_height: List[int] = [0],
    clot_detection_height: List[int] = [60],
    liquid_surface_no_lld: List[int] = [3600],
    pull_out_distance_transport_air: List[int] = [50],
    second_section_height: List[int] = [0],
    second_section_ratio: List[int] = [0],
    minimum_height: List[int] = [3600],
    immersion_depth: List[int] = [0],
    immersion_depth_direction: List[int] = [0],
    surface_following_distance: List[int] = [0],
    aspiration_volumes: List[int] = [0],
    aspiration_speed: List[int] = [500],
    transport_air_volume: List[int] = [0],
    blow_out_air_volume: List[int] = [200],
    pre_wetting_volume: List[int] = [0],
    lld_mode: List[int] = [1],
    gamma_lld_sensitivity: List[int] = [1],
    dp_lld_sensitivity: List[int] = [1],
    aspirate_position_above_z_touch_off: List[int] = [5],
    detection_height_difference_for_dual_lld: List[int] = [0],
    swap_speed: List[int] = [100],
    settling_time: List[int] = [5],
    homogenization_volume: List[int] = [0],
    homogenization_cycles: List[int] = [0],
    homogenization_position_from_liquid_surface: List[int] = [250],
    homogenization_speed: List[int] = [500],
    homogenization_surface_following_distance: List[int] = [0],
    limit_curve_index: List[int] = [0],
    tadm_algorithm: bool = False,
    recording_mode: int = 0,

    # For second section aspiration only
    use_2nd_section_aspiration: List[bool] = [False],
    retract_height_over_2nd_section_to_empty_tip: List[int] = [60],
    dispensation_speed_during_emptying_tip: List[int] = [468],
    dosing_drive_speed_during_2nd_section_search: List[int] = [468],
    z_drive_speed_during_2nd_section_search: List[int] = [215],
    cup_upper_edge: List[int] = [3600],
    ratio_liquid_rise_to_tip_deep_in: List[int] = [16246],
    immersion_depth_2nd_section: List[int] = [30]
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

    assert all(0 <= x <= 2 for x in aspiration_type), "aspiration_type must be between 0 and 2"
    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= min_z_endpos <= 3600, "min_z_endpos must be between 0 and 3600"
    assert all(0 <= x <= 3600 for x in lld_search_height), \
      "lld_search_height must be between 0 and 3600"
    assert all(0 <= x <= 500 for x in clot_detection_height), \
      "clot_detection_height must be between 0 and 500"
    assert all(0 <= x <= 3600 for x in liquid_surface_no_lld), \
      "liquid_surface_no_lld must be between 0 and 3600"
    assert all(0 <= x <= 3600 for x in pull_out_distance_transport_air), \
      "pull_out_distance_transport_air must be between 0 and 3600"
    assert all(0 <= x <= 3600 for x in second_section_height), \
      "second_section_height must be between 0 and 3600"
    assert all(0 <= x <= 10000 for x in second_section_ratio), \
      "second_section_ratio must be between 0 and 10000"
    assert all(0 <= x <= 3600 for x in minimum_height), "minimum_height must be between 0 and 3600"
    assert all(0 <= x <= 3600 for x in immersion_depth), \
      "immersion_depth must be between 0 and 3600"
    assert all(0 <= x <= 1 for x in immersion_depth_direction), \
      "immersion_depth_direction must be between 0 and 1"
    assert all(0 <= x <= 3600 for x in surface_following_distance), \
      "surface_following_distance must be between 0 and 3600"
    assert all(0 <= x <= 12500 for x in aspiration_volumes), \
      "aspiration_volumes must be between 0 and 12500"
    assert all(4 <= x <= 5000 for x in aspiration_speed), \
      "aspiration_speed must be between 4 and 5000"
    assert all(0 <= x <= 500 for x in transport_air_volume), \
      "transport_air_volume must be between 0 and 500"
    assert all(0 <= x <= 9999 for x in blow_out_air_volume), \
      "blow_out_air_volume must be between 0 and 9999"
    assert all(0 <= x <= 999 for x in pre_wetting_volume), \
      "pre_wetting_volume must be between 0 and 999"
    assert all(0 <= x <= 4 for x in lld_mode), "lld_mode must be between 0 and 4"
    assert all(1 <= x <= 4 for x in gamma_lld_sensitivity), \
      "gamma_lld_sensitivity must be between 1 and 4"
    assert all(1 <= x <= 4 for x in dp_lld_sensitivity), \
      "dp_lld_sensitivity must be between 1 and 4"
    assert all(0 <= x <= 100 for x in aspirate_position_above_z_touch_off), \
      "aspirate_position_above_z_touch_off must be between 0 and 100"
    assert all(0 <= x <= 99 for x in detection_height_difference_for_dual_lld), \
      "detection_height_difference_for_dual_lld must be between 0 and 99"
    assert all(3 <= x <= 1600 for x in swap_speed), "swap_speed must be between 3 and 1600"
    assert all(0 <= x <= 99 for x in settling_time), "settling_time must be between 0 and 99"
    assert all(0 <= x <= 12500 for x in homogenization_volume), \
      "homogenization_volume must be between 0 and 12500"
    assert all(0 <= x <= 99 for x in homogenization_cycles), \
      "homogenization_cycles must be between 0 and 99"
    assert all(0 <= x <= 900 for x in homogenization_position_from_liquid_surface), \
      "homogenization_position_from_liquid_surface must be between 0 and 900"
    assert all(4 <= x <= 5000 for x in homogenization_speed), \
      "homogenization_speed must be between 4 and 5000"
    assert all(0 <= x <= 3600 for x in homogenization_surface_following_distance), \
      "homogenization_surface_following_distance must be between 0 and 3600"
    assert all(0 <= x <= 999 for x in limit_curve_index), \
      "limit_curve_index must be between 0 and 999"
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"
    assert all(0 <= x <= 3600 for x in retract_height_over_2nd_section_to_empty_tip), \
      "retract_height_over_2nd_section_to_empty_tip must be between 0 and 3600"
    assert all(4 <= x <= 5000 for x in dispensation_speed_during_emptying_tip), \
      "dispensation_speed_during_emptying_tip must be between 4 and 5000"
    assert all(4 <= x <= 5000 for x in dosing_drive_speed_during_2nd_section_search), \
      "dosing_drive_speed_during_2nd_section_search must be between 4 and 5000"
    assert all(3 <= x <= 1600 for x in z_drive_speed_during_2nd_section_search), \
      "z_drive_speed_during_2nd_section_search must be between 3 and 1600"
    assert all(0 <= x <= 3600 for x in cup_upper_edge), "cup_upper_edge must be between 0 and 3600"
    assert all(0 <= x <= 5000 for x in ratio_liquid_rise_to_tip_deep_in), \
      "ratio_liquid_rise_to_tip_deep_in must be between 0 and 50000"
    assert all(0 <= x <= 3600 for x in immersion_depth_2nd_section), \
      "immersion_depth_2nd_section must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="AS",
      tip_pattern=tip_pattern,
      read_timeout=60,
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

  async def dispense_pip(
    self,
    tip_pattern: List[bool],
    dispensing_mode: List[int] = [0],
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    minimum_height: List[int] = [3600],
    lld_search_height: List[int] = [0],
    liquid_surface_no_lld: List[int] = [3600],
    pull_out_distance_transport_air: List[int] = [50],
    immersion_depth: List[int] = [0],
    immersion_depth_direction: List[int] = [0],
    surface_following_distance: List[int] = [0],
    second_section_height: List[int] = [0],
    second_section_ratio: List[int] = [0],
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600, #
    dispense_volumes: List[int] = [0],
    dispense_speed: List[int] = [500],
    cut_off_speed: List[int] = [250],
    stop_back_volume: List[int] = [0],
    transport_air_volume: List[int] = [0],
    blow_out_air_volume: List[int] = [200],
    lld_mode: List[int] = [1],
    side_touch_off_distance: int = 1,
    dispense_position_above_z_touch_off: List[int] = [5],
    gamma_lld_sensitivity: List[int] = [1],
    dp_lld_sensitivity: List[int] = [1],
    swap_speed: List[int] = [100],
    settling_time: List[int] = [5],
    mix_volume: List[int] = [0],
    mix_cycles: List[int] = [0],
    mix_position_from_liquid_surface: List[int] = [250],
    mix_speed: List[int] = [500],
    mix_surface_following_distance: List[int] = [0],
    limit_curve_index: List[int] = [0],
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

    assert all(0 <= x <= 4 for x in dispensing_mode), "dispensing_mode must be between 0 and 4"
    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert any(0 <= x <= 3600 for x in minimum_height), \
      "minimum_height must be between 0 and 3600"
    assert any(0 <= x <= 3600 for x in lld_search_height), \
      "lld_search_height must be between 0 and 3600"
    assert any(0 <= x <= 3600 for x in liquid_surface_no_lld), \
      "liquid_surface_no_lld must be between 0 and 3600"
    assert any(0 <= x <= 3600 for x in pull_out_distance_transport_air), \
      "pull_out_distance_transport_air must be between 0 and 3600"
    assert any(0 <= x <= 3600 for x in immersion_depth), \
      "immersion_depth must be between 0 and 3600"
    assert any(0 <= x <= 1 for x in immersion_depth_direction), \
      "immersion_depth_direction must be between 0 and 1"
    assert any(0 <= x <= 3600 for x in surface_following_distance), \
      "surface_following_distance must be between 0 and 3600"
    assert any(0 <= x <= 3600 for x in second_section_height), \
      "second_section_height must be between 0 and 3600"
    assert any(0 <= x <= 10000 for x in second_section_ratio), \
      "second_section_ratio must be between 0 and 10000"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= min_z_endpos <= 3600, "min_z_endpos must be between 0 and 3600"
    assert any(0 <= x <= 12500 for x in dispense_volumes), \
      "dispense_volume must be between 0 and 12500"
    assert any(4 <= x <= 5000 for x in dispense_speed), "dispense_speed must be between 4 and 5000"
    assert any(4 <= x <= 5000 for x in cut_off_speed), "cut_off_speed must be between 4 and 5000"
    assert any(0 <= x <= 180 for x in stop_back_volume), \
      "stop_back_volume must be between 0 and 180"
    assert any(0 <= x <= 500 for x in transport_air_volume), \
      "transport_air_volume must be between 0 and 500"
    assert any(0 <= x <= 9999 for x in blow_out_air_volume), \
      "blow_out_air_volume must be between 0 and 9999"
    assert any(0 <= x <= 4 for x in lld_mode), "lld_mode must be between 0 and 4"
    assert 0 <= side_touch_off_distance <= 45, "side_touch_off_distance must be between 0 and 45"
    assert any(0 <= x <= 100 for x in dispense_position_above_z_touch_off), \
      "dispense_position_above_z_touch_off must be between 0 and 100"
    assert any(1 <= x <= 4 for x in gamma_lld_sensitivity), \
      "gamma_lld_sensitivity must be between 1 and 4"
    assert any(1 <= x <= 4 for x in dp_lld_sensitivity), \
      "dp_lld_sensitivity must be between 1 and 4"
    assert any(3 <= x <= 1600 for x in swap_speed), "swap_speed must be between 3 and 1600"
    assert any(0 <= x <= 99 for x in settling_time), "settling_time must be between 0 and 99"
    assert any(0 <= x <= 12500 for x in mix_volume), "mix_volume must be between 0 and 12500"
    assert any(0 <= x <= 99 for x in mix_cycles), "mix_cycles must be between 0 and 99"
    assert any(0 <= x <= 900 for x in mix_position_from_liquid_surface), \
      "mix_position_from_liquid_surface must be between 0 and 900"
    assert any(4 <= x <= 5000 for x in mix_speed), "mix_speed must be between 4 and 5000"
    assert any(0 <= x <= 3600 for x in mix_surface_following_distance), \
      "mix_surface_following_distance must be between 0 and 3600"
    assert any(0 <= x <= 999 for x in limit_curve_index), \
      "limit_curve_index must be between 0 and 999"
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    return await self.send_command(
      module="C0",
      command="DS",
      tip_pattern=tip_pattern,
      read_timeout=60,
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

  async def position_single_pipetting_channel_in_y_direction(
    self,
    pipetting_channel_index: int,
    y_position: int
  ):
    """ Position single pipetting channel in Y-direction.

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16.
      y_position: y position [0.1mm]. Must be between 0 and 6500.
    """

    assert 1 <= pipetting_channel_index <= self.num_channels, \
      "pipetting_channel_index must be between 1 and self"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"

    return await self.send_command(
      module="C0",
      command="KY",
      pn=f"{pipetting_channel_index:02}",
      yj=f"{y_position:04}",
    )

  async def position_single_pipetting_channel_in_z_direction(
    self,
    pipetting_channel_index: int,
    z_position: int
  ):
    """ Position single pipetting channel in Z-direction.

    Note that this refers to the point of the tip if a tip is mounted!

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16.
      z_position: y position [0.1mm]. Must be between 0 and 3347. The docs say 3600,but empirically
        3347 is the max.
    """

    assert 1 <= pipetting_channel_index <= self.num_channels, \
      "pipetting_channel_index must be between 1 and self.num_channels"
    # docs say 3600, but empirically 3347 is the max
    assert 0 <= z_position <= 3347, "z_position must be between 0 and 3347"

    return await self.send_command(
      module="C0",
      command="KZ",
      pn=f"{pipetting_channel_index:02}",
      zj=f"{z_position:04}",
    )

  async def search_for_teach_in_signal_using_pipetting_channel_n_in_x_direction(
    self,
    pipetting_channel_index: int,
    x_position: int
  ):
    """ Search for Teach in signal using pipetting channel n in X-direction.

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and self.num_channels.
      x_position: x position [0.1mm]. Must be between 0 and 30000.
    """

    assert 1 <= pipetting_channel_index <= self.num_channels, \
      "pipetting_channel_index must be between 1 and self.num_channels"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="XL",
      pn=f"{pipetting_channel_index:02}",
      xs=f"{x_position:05}",
    )

  async def spread_pip_channels(self):
    """ Spread PIP channels """

    return await self.send_command(module="C0", command="JE", fmt="")

  async def move_all_pipetting_channels_to_defined_position(
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
        command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').  Must be
        between 0 and 3600. Default 3600.
      z_endpos: Z-Position at end of a command [0.1 mm] (refers to all channels independent of tip
        pattern parameter 'tm'). Must be between 0 and 3600. Default 0.
    """

    assert 0 <= x_positions <= 25000, "x_positions must be between 0 and 25000"
    assert 0 <= y_positions <= 6500, "y_positions must be between 0 and 6500"
    assert 0 <= minimum_traverse_height_at_beginning_of_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_command must be between 0 and 3600"
    assert 0 <= z_endpos <= 3600, "z_endpos must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="JM",
      tm=tip_pattern,
      xp=x_positions,
      yp=y_positions,
      th=minimum_traverse_height_at_beginning_of_command,
      zp=z_endpos,
    )

  # TODO:(command:JR): teach rack using pipetting channel n

  async def position_max_free_y_for_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Position all pipetting channels so that there is maximum free Y range for channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
    """

    assert 1 <= pipetting_channel_index <= self.num_channels, \
      "pipetting_channel_index must be between 1 and self.num_channels"

    return await self.send_command(
      module="C0",
      command="JP",
      pn=f"{pipetting_channel_index:02}",
    )

  async def move_all_channels_in_z_safety(self):
    """ Move all pipetting channels in Z-safety position """

    return await self.send_command(module="C0", command="ZA")

  # -------------- 3.5.7 PIP query --------------

  # TODO:(command:RY): Request Y-Positions of all pipetting channels

  async def request_y_pos_channel_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Request Y-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.
    """

    assert 1 <= pipetting_channel_index <= 16, "pipetting_channel_index must be between 1 and 16"

    return await self.send_command(
      module="C0",
      command="RB",
      fmt="rb####",
      pn=pipetting_channel_index,
    )

  # TODO:(command:RZ): Request Z-Positions of all pipetting channels

  async def request_z_pos_channel_n(
    self,
    pipetting_channel_index: int = 1
  ):
    """ Request Z-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16. Default 1.

    Returns:
      Z-Position of channel n [0.1mm]. Taking into account tip presence and length.
    """

    assert 1 <= pipetting_channel_index <= 16, "pipetting_channel_index must be between 1 and 16"

    return await self.send_command(
      module="C0",
      command="RD",
      fmt="rd####",
      pn=pipetting_channel_index,
    )

  async def request_tip_presence(self) -> List[int]:
    """ Request query tip presence on each channel

    Returns:
      0 = no tip, 1 = Tip in gripper (for each channel)
    """

    resp = await self.send_command(module="C0", command="RT", fmt="rt# (n)")
    return cast(List[int], resp.get("rt"))

  async def request_pip_height_last_lld(self):
    """ Request PIP height of last LLD

    Returns:
      LLD height of all channels
    """

    return await self.send_command(module="C0", command="RL", fmt="lh#### (n)")

  async def request_tadm_status(self):
    """ Request PIP height of last LLD

    Returns:
      TADM channel status 0 = off, 1 = on
    """

    return await self.send_command(module="C0", command="QS", fmt="qs# (n)")

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

  async def initialize_core_96_head(
    self,
    x_position: int = 2321,
    x_direction: int = 1,
    y_position: int = 1103,
    z_deposit_position: int = 1890,
    z_position_at_the_command_end: int = 2450
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

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1054 <= y_position <= 5743, "y_position must be between 1054 and 5743"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert 0 <= z_position_at_the_command_end <= 3425, \
      "z_position_at_the_command_end must be between 0 and 3425"

    return await self.send_command(
      module="C0",
      command="EI",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position}",
      za=f"{z_deposit_position}",
      ze=f"{z_position_at_the_command_end}",
    )

  async def move_core_96_to_safe_position(self):
    """ Move CoRe 96 Head to Z save position """

    return await self.send_command(module="C0", command="EV")

  # -------------- 3.10.2 Tip handling using CoRe 96 Head --------------

  async def pick_up_tips_core96(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    tip_type_idx: int,
    tip_pickup_method: int = 2,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425
  ):
    """ Pick up tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_size: Tip type.
      tip_pickup_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96 tip
        wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
        0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
        of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    assert 0 <= minimum_height_command_end <= 3425, \
      "minimum_height_command_end must be between 0 and 3425"

    return await self.send_command(
      module="C0",
      command="EP",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      tt=f"{tip_type_idx:02}",
      wu=tip_pickup_method,
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}",
    )

  async def discard_tips_core96(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425
  ):
    """ Drop tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_type: Tip type.
      tip_pickup_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96
        tip wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
        0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
        of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    assert 0 <= minimum_height_command_end <= 3425, \
      "minimum_height_command_end must be between 0 and 3425"

    return await self.send_command(
      module="C0",
      command="ER",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}"
    )

  # -------------- 3.10.3 Liquid handling using CoRe 96 Head --------------

  async def aspirate_core_96(
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
    channel_pattern: List[bool] = [True] * 96,
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

    assert 0 <= aspiration_type <= 2, "aspiration_type must be between 0 and 2"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_positions <= 5600, "y_positions must be between 1080 and 5600"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    assert 0 <= minimal_end_height <= 3425, "minimal_end_height must be between 0 and 3425"
    assert 0 <= lld_search_height <= 3425, "lld_search_height must be between 0 and 3425"
    assert 0 <= liquid_surface_at_function_without_lld <= 3425, \
      "liquid_surface_at_function_without_lld must be between 0 and 3425"
    assert 0 <= pull_out_distance_to_take_transport_air_in_function_without_lld <= 3425, \
      "pull_out_distance_to_take_transport_air_in_function_without_lld must be between 0 and 3425"
    assert 0 <= maximum_immersion_depth <= 3425, \
      "maximum_immersion_depth must be between 0 and 3425"
    assert 0 <= tube_2nd_section_height_measured_from_zm <= 3425, \
      "tube_2nd_section_height_measured_from_zm must be between 0 and 3425"
    assert 0 <= tube_2nd_section_ratio <= 10000, \
      "tube_2nd_section_ratio must be between 0 and 10000"
    assert 0 <= immersion_depth <= 3600, "immersion_depth must be between 0 and 3600"
    assert 0 <= immersion_depth_direction <= 1, "immersion_depth_direction must be between 0 and 1"
    assert 0 <= liquid_surface_sink_distance_at_the_end_of_aspiration <= 990, \
      "liquid_surface_sink_distance_at_the_end_of_aspiration must be between 0 and 990"
    assert 0 <= aspiration_volumes <= 11500, "aspiration_volumes must be between 0 and 11500"
    assert 3 <= aspiration_speed <= 5000, "aspiration_speed must be between 3 and 5000"
    assert 0 <= transport_air_volume <= 500, "transport_air_volume must be between 0 and 500"
    assert 0 <= blow_out_air_volume <= 11500, "blow_out_air_volume must be between 0 and 11500"
    assert 0 <= pre_wetting_volume <= 11500, "pre_wetting_volume must be between 0 and 11500"
    assert 0 <= lld_mode <= 4, "lld_mode must be between 0 and 4"
    assert 1 <= gamma_lld_sensitivity <= 4, "gamma_lld_sensitivity must be between 1 and 4"
    assert 3 <= swap_speed <= 1000, "swap_speed must be between 3 and 1000"
    assert 0 <= settling_time <= 99, "settling_time must be between 0 and 99"
    assert 0 <= homogenization_volume <= 11500, "homogenization_volume must be between 0 and 11500"
    assert 0 <= homogenization_cycles <= 99, "homogenization_cycles must be between 0 and 99"
    assert 0 <= homogenization_position_from_liquid_surface <= 990, \
      "homogenization_position_from_liquid_surface must be between 0 and 990"
    assert 0 <= surface_following_distance_during_homogenization <= 990, \
      "surface_following_distance_during_homogenization must be between 0 and 990"
    assert 3 <= speed_of_homogenization <= 5000, \
      "speed_of_homogenization must be between 3 and 5000"
    assert 0 <= limit_curve_index <= 999, "limit_curve_index must be between 0 and 999"

    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern_bin_str = reversed(["1" if x else "0" for x in channel_pattern])
    channel_pattern_hex = hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]

    return await self.send_command(
      module="C0",
      command="EA",
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
      cw=channel_pattern_hex,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  async def dispense_core_96(
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
    channel_pattern: List[bool] = [True]*12*8,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0
  ):
    """ dispense CoRe 96

    Dispensing of liquid using CoRe 96

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode 1 = Blow out
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

    assert 0 <= dispensing_mode <= 4, "dispensing_mode must be between 0 and 4"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= maximum_immersion_depth <= 3425, \
      "maximum_immersion_depth must be between 0 and 3425"
    assert 0 <= tube_2nd_section_height_measured_from_zm <= 3425, \
      "tube_2nd_section_height_measured_from_zm must be between 0 and 3425"
    assert 0 <= tube_2nd_section_ratio <= 10000, \
      "tube_2nd_section_ratio must be between 0 and 10000"
    assert 0 <= lld_search_height <= 3425, "lld_search_height must be between 0 and 3425"
    assert 0 <= liquid_surface_at_function_without_lld <= 3425, \
      "liquid_surface_at_function_without_lld must be between 0 and 3425"
    assert 0 <= pull_out_distance_to_take_transport_air_in_function_without_lld <= 3425, \
      "pull_out_distance_to_take_transport_air_in_function_without_lld must be between 0 and 3425"
    assert 0 <= immersion_depth <= 3600, "immersion_depth must be between 0 and 3600"
    assert 0 <= immersion_depth_direction <= 1, "immersion_depth_direction must be between 0 and 1"
    assert 0 <= liquid_surface_sink_distance_at_the_end_of_dispense <= 990, \
      "liquid_surface_sink_distance_at_the_end_of_dispense must be between 0 and 990"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    assert 0 <= minimal_end_height <= 3425, "minimal_end_height must be between 0 and 3425"
    assert 0 <= dispense_volume <= 11500, "dispense_volume must be between 0 and 11500"
    assert 3 <= dispense_speed <= 5000, "dispense_speed must be between 3 and 5000"
    assert 3 <= cut_off_speed <= 5000, "cut_off_speed must be between 3 and 5000"
    assert 0 <= stop_back_volume <= 999, "stop_back_volume must be between 0 and 999"
    assert 0 <= transport_air_volume <= 500, "transport_air_volume must be between 0 and 500"
    assert 0 <= blow_out_air_volume <= 11500, "blow_out_air_volume must be between 0 and 11500"
    assert 0 <= lld_mode <= 4, "lld_mode must be between 0 and 4"
    assert 1 <= gamma_lld_sensitivity <= 4, "gamma_lld_sensitivity must be between 1 and 4"
    assert 0 <= side_touch_off_distance <= 45, "side_touch_off_distance must be between 0 and 45"
    assert 3 <= swap_speed <= 1000, "swap_speed must be between 3 and 1000"
    assert 0 <= settling_time <= 99, "settling_time must be between 0 and 99"
    assert 0 <= mixing_volume <= 11500, "mixing_volume must be between 0 and 11500"
    assert 0 <= mixing_cycles <= 99, "mixing_cycles must be between 0 and 99"
    assert 0 <= mixing_position_from_liquid_surface <= 990, \
      "mixing_position_from_liquid_surface must be between 0 and 990"
    assert 0 <= surface_following_distance_during_mixing <= 990, \
      "surface_following_distance_during_mixing must be between 0 and 990"
    assert 3 <= speed_of_mixing <= 5000, "speed_of_mixing must be between 3 and 5000"
    assert 0 <= limit_curve_index <= 999, "limit_curve_index must be between 0 and 999"
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern_bin_str = reversed(["1" if x else "0" for x in channel_pattern])
    channel_pattern_hex = hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]

    return await self.send_command(
      module="C0",
      command="ED",
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
      cw=channel_pattern_hex,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  # -------------- 3.10.4 Adjustment & movement commands --------------

  async def move_core_96_head_to_defined_position(
    self,
    dispensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    z_position: int = 0,
    minimum_height_at_beginning_of_a_command: int = 3425
  ):
    """ Move CoRe 96 Head to defined position

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode 1 = Blow out
        in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix
        position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm]. Must be between 1080 and 5600. Default 0.
      z_position: Z-Position [0.1mm]. Must be between 0 and 5600. Default 0.
      minimum_height_at_beginning_of_a_command: Minimum height at beginning of a command 0.1mm]
        (refers to all channels independent of tip pattern parameter 'tm'). Must be between 0 and
        3425. Default 3425.
    """

    assert 0 <= dispensing_mode <= 4, "dispensing_mode must be between 0 and 4"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= y_position <= 5600, "z_position must be between 0 and 5600"
    assert 0 <= minimum_height_at_beginning_of_a_command <= 3425, \
      "minimum_height_at_beginning_of_a_command must be between 0 and 3425"

    return await self.send_command(
      module="C0",
      command="EM",
      dm=dispensing_mode,
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

  async def request_tip_presence_in_core_96_head(self):
    """ Request Tip presence in CoRe 96 Head

    Returns:
      qh: 0 = no tips, 1 = TipRack are picked up
    """

    return await self.send_command(module="C0", command="QH", fmt="qh#")

  async def request_position_of_core_96_head(self):
    """ Request position of CoRe 96 Head (A1 considered to tip length)

    Returns:
      xs: A1 X direction [0.1mm]
      xd: X direction 0 = positive 1 = negative
      yh: A1 Y direction [0.1mm]
      za: Z height [0.1mm]
    """

    return await self.send_command(module="C0", command="QI", fmt="xs#####xd#hy####za####")

  async def request_core_96_head_channel_tadm_status(self):
    """ Request CoRe 96 Head channel TADM Status

    Returns:
      qx: TADM channel status 0 = off 1 = on
    """

    return await self.send_command(module="C0", command="VC", fmt="qx#")

  async def request_core_96_head_channel_tadm_error_status(self):
    """ Request CoRe 96 Head channel TADM error status

    Returns:
      vb: error pattern 0 = no error
    """

    return await self.send_command(module="C0", command="VB", fmt="vb" + "&" * 24)

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

  async def initialize_auto_load(self):
    """ Initialize Auto load module """

    return await self.send_command(module="C0", command="II")

  async def move_auto_load_to_z_save_position(self):
    """ Move auto load to Z save position """

    return await self.send_command(module="C0", command="IV")

  # -------------- 3.13.2 Carrier handling --------------

  # TODO:(command:CI) Identify carrier (determine carrier type)

  async def request_single_carrier_presence(
    self,
    carrier_position: int
  ):
    """ Request single carrier presence

    Args:
      carrier_position: Carrier position (slot number)

    Returns:
      True if present, False otherwise
    """

    assert 1 <= carrier_position <= 54, "carrier_position must be between 1 and 54"

    resp = await self.send_command(
      module="C0",
      command="CT",
      fmt="ct#",
      cp=carrier_position,
    )
    assert resp is not None
    return resp["ct"] == 1

  # TODO:(command:CA) Push out carrier to loading tray (after identification CI)

  # TODO:(command:CR) Unload carrier

  # TODO:(command:CL) Load carrier

  async def set_loading_indicators(
    self,
    bit_pattern: List[bool],
    blink_pattern: List[bool]
  ):
    """ Set loading indicators (LEDs)

    The docs here are a little weird because 2^54 < 7FFFFFFFFFFFFF.

    Args:
      bit_pattern: On if True, off otherwise
      blink_pattern: Blinking if True, steady otherwise
    """

    assert len(bit_pattern) == 54, "bit pattern must be length 54"
    assert len(blink_pattern) == 54, "bit pattern must be length 54"

    bit_pattern_hex   = hex(int("".join(["1" if x else "0" for x in bit_pattern]), base=2))
    blink_pattern_hex = hex(int("".join(["1" if x else "0" for x in blink_pattern]), base=2))

    return await self.send_command(
      module="C0",
      command="CP",
      cl=bit_pattern_hex,
      cb=blink_pattern_hex
    )

  # TODO:(command:CS) Check for presence of carriers on loading tray

  async def set_barcode_type(
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
    bt_hex = hex(int(bt, base=2))

    return await self.send_command(
      module="C0",
      command="CB",
      bt=bt_hex
    )

  # TODO:(command:CW) Unload carrier finally

  async def set_carrier_monitoring(
    self,
    should_monitor: bool = False
  ):
    """ Set carrier monitoring

    Args:
      should_monitor: whether carrier should be monitored.

    Returns:
      True if present, False otherwise
    """

    return await self.send_command(
      module="C0",
      command="CU",
      cu=should_monitor
    )

  # TODO:(command:CN) Take out the carrier to identification position

  # -------------- 3.13.3 Auto load query --------------

  # TODO:(command:RC) Query presence of carrier on deck

  async def request_auto_load_slot_position(self):
    """ Request auto load slot position.

    Returns:
      slot position (0..54)
    """

    return await self.send_command(module="C0", command="QA", fmt="qa##")

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

  async def request_pump_settings(
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

    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"

    return await self.send_command(
      module="C0",
      command="ET",
      fmt="et#",
      ep=pump_station
    )

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

  async def initialize_dual_pump_station_valves(
    self,
    pump_station: int = 1
  ):
    """ Initialize pump station valves (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
    """

    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"

    return await self.send_command(
      module="C0",
      command="EJ",
      ep=pump_station
    )

  async def fill_selected_dual_chamber(
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

    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"
    assert 1 <= wash_fluid <= 2, "wash_fluid must be between 1 and 2"
    assert 1 <= chamber <= 2, "chamber must be between 1 and 2"

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

    return await self.send_command(
      module="C0",
      command="EH",
      ep=pump_station,
      ed=drain_before_refill,
      ek=connection,
      eu=f"{waste_chamber_suck_time_after_sensor_change:02}",
      wait=False
    )

  # TODO:(command:EK) Drain selected chamber

  async def drain_dual_chamber_system(
    self,
    pump_station: int = 1
  ):
    """ Drain system (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
    """

    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"

    return await self.send_command(
      module="C0",
      command="EL",
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

  async def initialize_iswap(self):
    """ Initialize iSWAP (for standalone configuration only) """

    return await self.send_command(module="C0", command="FI")

  async def position_components_for_free_iswap_y_range(self):
    """ Position all components so that there is maximum free Y range for iSWAP """

    return await self.send_command(module="C0", command="FY")

  async def move_iswap_x_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in X-direction

    Args:
      step_size: X Step size [0.1mm] Between 0 and 999. Default 0.
      direction: X direction. 0 = positive 1 = negative
    """

    return await self.send_command(
      module="C0",
      command="GX",
      gx=step_size,
      xd=direction
    )

  async def move_iswap_y_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in Y-direction

    Args:
      step_size: Y Step size [0.1mm] Between 0 and 999. Default 0.
      direction: Y direction. 0 = positive 1 = negative
    """

    return await self.send_command(
      module="C0",
      command="GY",
      gx=step_size,
      xd=direction
    )

  async def move_iswap_z_direction(
    self,
    step_size: int = 0,
    direction: int = 0
  ):
    """ Move iSWAP in Z-direction

    Args:
      step_size: Z Step size [0.1mm] Between 0 and 999. Default 0.
      direction: Z direction. 0 = positive 1 = negative
    """

    return await self.send_command(
      module="C0",
      command="GZ",
      gx=step_size,
      xd=direction
    )

  async def open_not_initialized_gripper(self):
    """ Open not initialized gripper """

    return await self.send_command(module="C0", command="GI")

  async def open_gripper(
    self,
    open_position: int = 1320
  ):
    """ Open gripper

    Args:
      open_position: Open position [0.1mm] (0.1 mm = 16 increments) The gripper moves to pos + 20.
                     Must be between 0 and 9999. Default 860.
    """

    assert 0 <= open_position <= 9999, "open_position must be between 0 and 9999"

    return await self.send_command(
      module="C0",
      command="GF",
      go=f"{open_position:04}"
    )

  async def close_gripper(
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

    return await self.send_command(
      module="C0",
      command="GC",
      gw=grip_strength,
      gb=plate_width,
      gt=plate_width_tolerance
    )

  # -------------- 3.17.2 Stack handling commands CP --------------

  async def park_iswap(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 2840
  ):
    """ Close gripper

    The gripper should be at the position gb+gt+20 before sending this command.

    Args:
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
                of a command [0.1mm]. Must be between 0 and 3600. Default 3600.
    """

    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"

    command_output = await self.send_command(
      module="C0",
      command="PG",
      th=minimum_traverse_height_at_beginning_of_a_command
    )

    # Once the command has completed successfully, set _iswap_parked to True
    self._iswap_parked = True
    return command_output

  async def get_plate(
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

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= z_position_at_the_command_end <= 3600, \
      "z_position_at_the_command_end must be between 0 and 3600"
    assert 1 <= grip_strength <= 9, "grip_strength must be between 1 and 9"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= plate_width <= 9999, "plate_width must be between 0 and 9999"
    assert 0 <= plate_width_tolerance <= 99, "plate_width_tolerance must be between 0 and 99"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert 0 <= acceleration_index_high_acc <= 4, \
      "acceleration_index_high_acc must be between 0 and 4"
    assert 0 <= acceleration_index_low_acc <= 4, \
      "acceleration_index_low_acc must be between 0 and 4"

    command_output = await self.send_command(
      module="C0",
      command="PP",
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

    # Once the command has completed successfully, set _iswap_parked to false
    self._iswap_parked = False
    return command_output

  async def put_plate(
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

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= z_position_at_the_command_end <= 3600, \
      "z_position_at_the_command_end must be between 0 and 3600"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert 0 <= acceleration_index_high_acc <= 4, \
      "acceleration_index_high_acc must be between 0 and 4"
    assert 0 <= acceleration_index_low_acc <= 4, \
      "acceleration_index_low_acc must be between 0 and 4"

    command_output = await self.send_command(
      module="C0",
      command="PR",
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

    # Once the command has completed successfully, set _iswap_parked to false
    self._iswap_parked = False
    return command_output

  async def move_plate_to_position(
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

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert 0 <= acceleration_index_high_acc <= 4, \
      "acceleration_index_high_acc must be between 0 and 4"
    assert 0 <= acceleration_index_low_acc <= 4, \
      "acceleration_index_low_acc must be between 0 and 4"

    command_output = await self.send_command(
      module="C0",
      command="PM",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      gr=grip_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
    )
    # Once the command has completed successfuly, set _iswap_parked to false
    self._iswap_parked = False
    return command_output

  async def collapse_gripper_arm(
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

    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"

    return await self.send_command(
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

  async def prepare_iswap_teaching(
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
        a command 0.1mm]. Must be between 0 and 3600. Default 3600.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
        Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4. Default 1.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 0 <= location <= 1, "location must be between 0 and 1"
    assert 0 <= hotel_depth <= 3000, "hotel_depth must be between 0 and 3000"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, \
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert 0 <= acceleration_index_high_acc <= 4, \
      "acceleration_index_high_acc must be between 0 and 4"
    assert 0 <= acceleration_index_low_acc <= 4, \
      "acceleration_index_low_acc must be between 0 and 4"

    return await self.send_command(
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

  async def get_logic_iswap_position(
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

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 0 <= location <= 1, "location must be between 0 and 1"
    assert 0 <= hotel_depth <= 3000, "hotel_depth must be between 0 and 3000"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"

    return await self.send_command(
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

  async def request_iswap_in_parking_position(self):
    """ Request iSWAP in parking position

    Returns:
      0 = gripper is not in parking position
      1 = gripper is in parking position
    """

    return await self.send_command(module="C0", command="RG", fmt="rg#")

  async def request_plate_in_iswap(self) -> bool:
    """ Request plate in iSWAP

    Returns:
      True if holding a plate, False otherwise.
    """

    resp = await self.send_command(module="C0", command="QP", fmt="ph#")
    return resp is not None and resp["ph"] == 1

  async def request_iswap_position(self):
    """ Request iSWAP position ( grip center )

    Returns:
      xs: Hotel center in X direction [0.1mm]
      xd: X direction 0 = positive 1 = negative
      yj: Gripper center in Y direction [0.1mm]
      yd: Y direction 0 = positive 1 = negative
      zj: Gripper Z height (gripping height) [0.1mm]
      zd: Z direction 0 = positive 1 = negative
    """

    return await self.send_command(module="C0", command="QG", fmt="xs#####xd#yj####yd#zj####zd#")

  async def request_iswap_initialization_status(self) -> bool:
    """ Request iSWAP initialization status

    Returns:
      True if iSWAP is fully initialized
    """

    resp = await self.send_command(module="R0", command="QW", fmt="qw#")
    return cast(int, resp["qw"]) == 1

  # -------------- 3.18 Cover and port control --------------

  async def lock_cover(self):
    """ Lock cover """

    return await self.send_command(module="C0", command="CO", fmt="")

  async def unlock_cover(self):
    """ Unlock cover """

    return await self.send_command(module="C0", command="HO", fmt="")

  async def disable_cover_control(self):
    """ Disable cover control """

    return await self.send_command(module="C0", command="CD", fmt="")

  async def enable_cover_control(self):
    """ Enable cover control """

    return await self.send_command(module="C0", command="CE", fmt="")

  async def set_cover_output(self, output: int = 0):
    """ Set cover output

    Args:
      output: 1 = cover lock; 2 = reserve out; 3 = reserve out.
    """

    assert 1 <= output <= 3, "output must be between 1 and 3"
    return await self.send_command(module="C0", command="OS", on=output, fmt="")

  async def reset_output(self, output: int = 0):
    """ Reset output

    Returns:
      output: 1 = cover lock; 2 = reserve out; 3 = reserve out.
    """

    assert 1 <= output <= 3, "output must be between 1 and 3"
    return await self.send_command(module="C0", command="QS", on=output, fmt="#")

  async def request_cover_open(self) -> bool:
    """ Request cover open

    Returns: True if the cover is open
    """

    resp = await self.send_command(module="C0", command="QC", fmt="qc#")
    return bool(resp["qc"])
