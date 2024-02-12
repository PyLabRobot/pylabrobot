from abc import ABCMeta, abstractmethod
import asyncio
import datetime
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypeVar, cast

from pylabrobot.liquid_handling.backends.USBBackend import USBBackend
from pylabrobot.liquid_handling.standard import PipettingOp
from pylabrobot.resources import TipSpot, Well
from pylabrobot.resources.ml_star import HamiltonTip, TipPickupMethod, TipSize

T = TypeVar("T")

logger = logging.getLogger("pylabrobot")


class HamiltonFirmwareError(Exception, metaclass=ABCMeta):
  """ Base class for all Hamilton backend errors, raised by firmware. """


class HamiltonLiquidHandler(USBBackend, metaclass=ABCMeta):
  """
  Abstract base class for Hamilton liquid handling robot backends.
  """

  @abstractmethod
  def __init__(
    self,
    id_product: int,
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
      id_product=id_product)

    self.id_ = 0

    self._reading_thread: Optional[threading.Thread] = None
    self._waiting_tasks: Dict[int,
      Tuple[asyncio.AbstractEventLoop, asyncio.Future, str, float]] = {}
    self._tth2tti: dict[int, int] = {} # hash to tip type index

  async def stop(self):
    self._waiting_tasks.clear()
    await super().stop()

  @property
  @abstractmethod
  def module_id_length(self) -> int:
    """ The length of the module identifier in firmware commands. """

  def _generate_id(self) -> int:
    """ continuously generate unique ids 0 <= x < 10000. """
    self.id_ += 1
    return self.id_ % 10000

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
        # this value will be ignored, so just use a value we know is valid
        result.append(val[0])
    if arg_index < len(val):
      raise ValueError(f"Too many values for tip pattern {tip_pattern}: {val}")
    return result

  def _assemble_command(
    self,
    module: str,
    command: str,
    tip_pattern: Optional[List[bool]],
    **kwargs) -> Tuple[str, int]:
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
    cmd_id = self._generate_id()
    cmd += f"id{cmd_id:04}" # id has to be the first param

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

    return cmd, cmd_id

  async def send_command(
    self,
    module: str,
    command: str,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait = True,
    fmt: Optional[Any] = None,
    **kwargs
  ):
    """ Send a firmware command to the Hamilton machine.

    Args:
      module: 2 character module identifier (C0 for master, ...)
      command: 2 character command identifier (QM for request status)
      write_timeout: write timeout in seconds. If None, `self.write_timeout` is used.
      read_timeout: read timeout in seconds. If None, `self.read_timeout` is used.
      wait: If True, wait for a response. If False, return `None` immediately after sending the
        command.
      fmt: A format to use for the response. If `None`, the response is not parsed.
      kwargs: any named parameters. The parameter name should also be 2 characters long. The value
        can be of any size.

    Raises:
      HamiltonFirmwareError: if an error response is received.

    Returns:
      A dictionary containing the parsed response, or None if no response was read within `timeout`.
    """

    cmd, id_ = self._assemble_command(module=module, command=command, tip_pattern=tip_pattern,
      **kwargs)
    resp = await self._write_and_read_command(id_=id_, cmd=cmd, write_timeout=write_timeout,
                    read_timeout=read_timeout, wait=wait)
    if resp is not None and fmt is not None:
      return self._parse_response(resp, fmt)
    return resp

  async def _write_and_read_command(
    self,
    id_: int,
    cmd: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True
  ) -> Optional[str]:
    """ Write a command to the Hamilton machine and read the response. """
    self.write(cmd, timeout=write_timeout)

    if not wait:
      return None

    # Attempt to read packets until timeout, or when we identify the right id.
    if read_timeout is None:
      read_timeout = self.read_timeout

    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    self._start_reading(id_, loop, fut, cmd, read_timeout)
    result = await fut
    return cast(str, result) # Futures are generic in Python 3.9, but not in 3.8, so we need cast.

  def _start_reading(
    self,
    id_: int,
    loop: asyncio.AbstractEventLoop,
    fut: asyncio.Future,
    cmd: str,
    timeout: int) -> None:
    """ Submit a task to the reading thread. Starts reading thread if it is not already running. """

    timeout_time = time.time() + timeout
    self._waiting_tasks[id_] = (loop, fut, cmd, timeout_time)

    # Start reading thread if it is not already running.
    if len(self._waiting_tasks) == 1:
      self._reading_thread = threading.Thread(target=self._continuously_read)
      self._reading_thread.start()

  @abstractmethod
  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """ Get the id from a firmware response. """

  @abstractmethod
  def check_fw_string_error(self, resp: str):
    """ Raise an error if the firmware response is an error response. """

  @abstractmethod
  def _parse_response(self, resp: str, fmt: Any) -> dict:
    """ Parse a firmware response. """

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
      for id_, (loop, fut, cmd, timeout_time) in self._waiting_tasks.items():
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

      logger.info("Received response: %s", resp)

      # Parse response.
      try:
        response_id = self.get_id_from_fw_response(resp)
      except ValueError as e:
        if resp != "":
          logger.warning("Could not parse response: %s (%s)", resp, e)
        continue

      for id_, (loop, fut, cmd, timeout_time) in self._waiting_tasks.items():
        if response_id == id_:
          try:
            self.check_fw_string_error(resp)
          except Exception as e: # pylint: disable=broad-exception-caught
            loop.call_soon_threadsafe(fut.set_exception, e)
          else:
            loop.call_soon_threadsafe(fut.set_result, resp)
          del self._waiting_tasks[id_]
          break

    self._reading_thread = None
    logger.debug("Reading thread stopped.")

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
      offset = ops[i].offset

      x_pos = ops[i].resource.get_absolute_location().x
      if offset is None or isinstance(ops[i].resource, (TipSpot, Well)):
        x_pos += ops[i].resource.center().x
      if offset is not None:
        x_pos += offset.x
      x_positions.append(int(x_pos*10))

      y_pos = ops[i].resource.get_absolute_location().y
      if offset is None or isinstance(ops[i].resource, (TipSpot, Well)):
        y_pos += ops[i].resource.center().y
      if offset is not None:
        y_pos += offset.y
      y_positions.append(int(y_pos*10))

    # check that the minimum d between any two y positions is >9mm
    # O(n^2) search is not great but this is most readable, and the max size is 16, so it's fine.
    for channel_idx1, (x1, y1) in enumerate(zip(x_positions, y_positions)):
      for channel_idx2, (x2, y2) in enumerate(zip(x_positions, y_positions)):
        if channel_idx1 == channel_idx2:
          continue
        if not channels_involved[channel_idx1] or not channels_involved[channel_idx2]:
          continue
        if x1 != x2: # channels not on the same column -> will be two operations on the machine
          continue
        if abs(y1 - y2) < 90:
          raise ValueError(f"Minimum distance between two y positions is <9mm: {y1}, {y2}"
                           f" (channel {channel_idx1} and {channel_idx2})")

    if len(ops) > self.num_channels:
      raise ValueError(f"Too many channels specified: {len(ops)} > {self.num_channels}")

    if len(x_positions) < self.num_channels:
      # We do want to have a trailing zero on x_positions, y_positions, and channels_involved, for
      # some reason, if the length < 8.
      x_positions = x_positions + [0]
      y_positions = y_positions + [0]
      channels_involved = channels_involved + [False]

    return x_positions, y_positions, channels_involved

  @abstractmethod
  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod
  ):
    """ Tip/needle definition in firmware. """

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
        has_filter=tip.has_filter,
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

  async def get_ttti(self, tips: List[HamiltonTip]) -> List[int]:
    """ Get tip type table index for a list of tips.

    Ensure that for all non-None tips, they have the same tip type, and return the tip type table
    index for that tip type.
    """

    return [await self.get_or_assign_tip_type_index(tip) for tip in tips]
