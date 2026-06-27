import asyncio
import datetime
import logging
import threading
import time
import warnings
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import (
  Any,
  List,
  Optional,
  Tuple,
  TypeVar,
)

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.io.usb import USB

T = TypeVar("T")

logger = logging.getLogger(__name__)


@dataclass
class HamiltonTask:
  """A command that has been sent, awaiting a response."""

  id_: Optional[int]
  loop: asyncio.AbstractEventLoop
  fut: asyncio.Future
  cmd: str
  timeout_time: float


class HamiltonUSBDriver(Driver, metaclass=ABCMeta):
  """Base class for Hamilton devices that communicate over USB firmware protocol.

  Provides USB I/O, firmware command assembly / parsing, and a background
  thread that continuously reads responses and matches them to pending tasks.
  """

  @abstractmethod
  def __init__(
    self,
    id_product: int,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """
    Args:
      id_product: The USB product ID for the Hamilton device.
      device_address: The USB address of the Hamilton device. Only useful if using more than one
        Hamilton device.
      serial_number: The serial number of the Hamilton device. Only useful if using more than one
        Hamilton device.
      packet_read_timeout: The timeout for reading packets from the Hamilton machine in seconds.
      read_timeout: The timeout for reading from the Hamilton machine in seconds.
    """

    super().__init__()
    self.io = USB(
      human_readable_device_name="Hamilton",
      id_vendor=0x08AF,
      id_product=id_product,
      device_address=device_address,
      write_timeout=write_timeout,
      serial_number=serial_number,
    )
    self.packet_read_timeout = packet_read_timeout
    self.read_timeout = read_timeout

    self.id_ = 0

    self._reading_thread: Optional[threading.Thread] = None
    self._reading_thread_stop = threading.Event()
    self._waiting_tasks: List[HamiltonTask] = []

  def __setattr__(self, name: str, value: Any) -> None:
    if name == "allow_firmware_planning":
      warnings.warn(
        "allow_firmware_planning is deprecated and will be removed in a future version. "
        "The behavior is now always enabled.",
        DeprecationWarning,
        stacklevel=2,
      )
      return
    super().__setattr__(name, value)

  async def setup(self, backend_params: Optional[BackendParams] = None):
    await super().setup(backend_params=backend_params)  # type: ignore[safe-super]
    await self.io.setup()
    self._reading_thread_stop.clear()
    self._reading_thread = threading.Thread(target=self._reading_thread_main, daemon=True)
    self._reading_thread.start()

  async def stop(self):
    self._reading_thread_stop.set()
    if self._reading_thread is not None:
      self._reading_thread.join(timeout=10)
      self._reading_thread = None
    for task in self._waiting_tasks:
      task.loop.call_soon_threadsafe(
        task.fut.set_exception, RuntimeError("Stopping HamiltonUSBDriver.")
      )
    self._waiting_tasks.clear()
    await self.io.stop()

  def serialize(self) -> dict:
    usb_serialized = self.io.serialize()
    del usb_serialized["id_vendor"]
    del usb_serialized["id_product"]
    del usb_serialized["human_readable_device_name"]
    return {**super().serialize(), **usb_serialized}

  @property
  @abstractmethod
  def module_id_length(self) -> int:
    """The length of the module identifier in firmware commands."""

  @property
  def num_channels(self) -> int:
    """The number of pipette channels present on the robot.

    Defaults to 0 for non-liquid-handler devices. Liquid handler subclasses must override.
    """
    return 0

  def _generate_id(self) -> int:
    """continuously generate unique ids 0 <= x < 10000."""
    self.id_ += 1
    return self.id_ % 10000

  def _to_list(self, val: List[T], tip_pattern: List[bool]) -> List[T]:
    """Convert a list of values to a list of values with the correct length.

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
    if len(val) == 0:
      raise ValueError("val must not be empty")
    if len(val) > len(tip_pattern):
      raise ValueError(f"val has more entries ({len(val)}) than tip_pattern ({len(tip_pattern)})")

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
    auto_id: bool,
    tip_pattern: Optional[List[bool]],
    **kwargs,
  ) -> Tuple[str, Optional[int]]:
    """Assemble a firmware command to the Hamilton machine.

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
    if auto_id:
      cmd_id = self._generate_id()
      cmd += f"id{cmd_id:04}"  # id has to be the first param
    else:
      cmd_id = None

    for k, v in kwargs.items():
      if isinstance(v, datetime.datetime):
        v = v.strftime("%Y-%m-%d %h:%M")
      elif isinstance(v, bool):
        v = 1 if v else 0
      elif isinstance(v, list):
        # If this command is 'one-hot' encoded, for the channels, then the list should be the
        # same length as the 'one-hot' encoding key (tip_pattern.) If the list is shorter than
        # that, it will be 'one-hot encoded automatically. Note that this may raise an error if
        # the number of values provided is not the same as the number of channels used.
        if tip_pattern is not None:
          if len(v) != len(tip_pattern):
            # convert one-hot encoded list to int list
            v = self._to_list(v, tip_pattern)
          # list is now of length len(tip_pattern)
        if isinstance(v[0], bool):  # convert bool list to int list
          v = [int(x) for x in v]
        v = " ".join([str(e) for e in v]) + ("&" if len(v) < self.num_channels else "")
      if k.endswith("_"):  # workaround for kwargs named in, as, ...
        k = k[:-1]
      if len(k) != 2:
        raise ValueError("Keyword arguments should be 2 characters long, but got: " + k)
      cmd += f"{k}{v}"

    return cmd, cmd_id

  async def send_command(
    self,
    module: str,
    command: str,
    auto_id=True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait=True,
    fmt: Optional[Any] = None,
    **kwargs,
  ):
    """Send a firmware command to the Hamilton machine.

    Args:
      module: 2 character module identifier (C0 for master, ...)
      command: 2 character command identifier (QM for request status)
      auto_id: auto generate id if True, otherwise use the id in kwargs (or None if not present)
      write_timeout: write timeout in seconds. If None, `self.write_timeout` is used.
      read_timeout: read timeout in seconds. If None, `self.read_timeout` is used.
      wait: If True, wait for a response. If False, return `None` immediately after sending the
        command.
      fmt: A format to use for the response. If `None`, the response is not parsed.
      kwargs: any named parameters. The parameter name should also be 2 characters long. The value
        can be of any size.

    Returns:
      A dictionary containing the parsed response, or None if no response was read within `timeout`.
    """

    cmd, id_ = self._assemble_command(
      module=module,
      command=command,
      tip_pattern=tip_pattern,
      auto_id=auto_id,
      **kwargs,
    )
    resp = await self._write_and_read_command(
      id_=id_,
      cmd=cmd,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
    )
    if resp is not None and fmt is not None:
      return self._parse_response(resp, fmt)
    return resp

  async def _write_and_read_command(
    self,
    id_: Optional[int],
    cmd: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Write a command to the Hamilton machine and read the response."""
    await self.io.write(cmd.encode(), timeout=write_timeout)

    if not wait:
      return None

    # Attempt to read packets until timeout, or when we identify the right id.
    if read_timeout is None:
      read_timeout = self.read_timeout

    loop = asyncio.get_event_loop()
    fut: asyncio.Future[str] = loop.create_future()
    self._start_reading(id_, loop, fut, cmd, read_timeout)
    result = await fut
    return result

  def _start_reading(
    self,
    id_: Optional[int],
    loop: asyncio.AbstractEventLoop,
    fut: asyncio.Future,
    cmd: str,
    timeout: int,
  ) -> None:
    """Submit a task to the reading thread."""

    timeout_time = time.time() + timeout
    self._waiting_tasks.append(
      HamiltonTask(id_=id_, loop=loop, fut=fut, cmd=cmd, timeout_time=timeout_time)
    )

    if self._reading_thread is None or not self._reading_thread.is_alive():
      self._reading_thread_stop.clear()
      self._reading_thread = threading.Thread(target=self._reading_thread_main, daemon=True)
      self._reading_thread.start()

  @abstractmethod
  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Get the id from a firmware response."""

  @abstractmethod
  def check_fw_string_error(self, resp: str):
    """Raise an error if the firmware response is an error response."""

  @abstractmethod
  def _parse_response(self, resp: str, fmt: Any) -> dict:
    """Parse a firmware response."""

  def _reading_thread_main(self) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(self._continuously_read())

  async def _continuously_read(self) -> None:
    """Continuously read from the USB port until stop is requested.

    Tasks are stored in the `self._waiting_tasks` list, and contain a future that will be
    completed when the task is finished. Tasks are submitted to the list using the
    `self._start_reading` method.

    On each iteration, read the USB port. If a response is received, parse it and check if it is
    relevant to any of the tasks. If so, complete the future and remove the task from the
    list. If a task has timed out, complete the future with a `TimeoutError`.
    """

    while not self._reading_thread_stop.is_set():
      for idx in range(len(self._waiting_tasks) - 1, -1, -1):  # reverse order to allow deletion
        task = self._waiting_tasks[idx]
        if time.time() > task.timeout_time:
          logger.warning("Timeout while waiting for response to command %s.", task.cmd)
          task.loop.call_soon_threadsafe(
            task.fut.set_exception,
            TimeoutError(f"Timeout while waiting for response to command {task.cmd}."),
          )
          del self._waiting_tasks[idx]

      if len(self._waiting_tasks) == 0:
        await asyncio.sleep(0.01)
        continue

      try:
        resp = (await self.io.read()).decode("utf-8")
      except TimeoutError:
        continue

      if resp == "":
        continue

      # Parse response.
      try:
        response_id = self.get_id_from_fw_response(resp)
      except ValueError as e:
        logger.warning("Could not parse response: %s (%s)", resp, e)
        continue

      module_and_command = resp[: self.module_id_length + 2]
      for idx in range(len(self._waiting_tasks)):
        task = self._waiting_tasks[idx]
        # if the command has no id, we have to check the command itself
        if response_id == task.id_ or (
          task.id_ is None and task.cmd.startswith(module_and_command)
        ):
          try:
            self.check_fw_string_error(resp)
          except Exception as e:
            task.loop.call_soon_threadsafe(task.fut.set_exception, e)
          else:
            task.loop.call_soon_threadsafe(task.fut.set_result, resp)
          del self._waiting_tasks[idx]
          break

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Send a raw command to the machine."""
    id_index = command.find("id")
    if id_index != -1:
      id_str = command[id_index + 2 : id_index + 6]
      if not id_str.isdigit():
        raise ValueError("Id must be a 4 digit int.")
      id_ = int(id_str)
    else:
      id_ = None

    return await self._write_and_read_command(
      id_=id_,
      cmd=command,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
    )
