import datetime
from abc import ABCMeta, abstractmethod
from typing import (
  Any,
  List,
  Optional,
  Tuple,
  TypeVar,
)

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.events import emit_event
from pylabrobot.hamilton.protocol.text.framing import to_list
from pylabrobot.hamilton.protocol.text.router import ReplyRouter, USBReplyReader
from pylabrobot.io.usb import USB

T = TypeVar("T")


class HamiltonUSBDriver(HasLifespan, metaclass=ABCMeta):
  """Base class for Hamilton devices that communicate over USB firmware protocol.

  Provides USB I/O, firmware command assembly / parsing, and scoped reply routing.
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
    self._reply_router = ReplyRouter(
      io=self.io,
      module_id_length=self.module_id_length,
      parse_id=self.get_id_from_fw_response,
      raise_for_error=self.check_fw_string_error,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      reader=USBReplyReader(self.io),
    )

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
  ) -> None:
    """Own the USB transport and one reply reader for this lifespan."""
    await super()._enter_lifespan(stack)
    self._reply_router.io = self.io
    self._reply_router.reader = USBReplyReader(self.io)
    await stack.enter_async_context(self.io.lifespan())
    await stack.enter_async_context(self._reply_router.lifespan())

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
    return self._reply_router.next_id()

  def _to_list(self, val: List[T], tip_pattern: List[bool]) -> List[T]:
    """One-hot encode a per-channel list; see `pylabrobot.hamilton.protocol.text.framing`."""
    return to_list(val, tip_pattern)

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
    event_data = {
      "transport": "hamilton_usb",
      "driver": type(self).__name__,
      "module": module,
      "command": command,
      "command_id": id_,
      "raw_command": cmd,
    }
    emit_event("firmware.command.started", **event_data)
    try:
      resp = await self._write_and_read_command(
        id_=id_,
        cmd=cmd,
        write_timeout=write_timeout,
        read_timeout=read_timeout,
        wait=wait,
      )
      result = self._parse_response(resp, fmt) if resp is not None and fmt is not None else resp
    except BaseException as error:
      emit_event(
        "firmware.command.failed",
        **event_data,
        error_type=type(error).__name__,
        error_message=str(error),
      )
      raise
    emit_event("firmware.command.completed", **event_data, response=resp)
    return result

  async def _write_and_read_command(
    self,
    id_: Optional[int],
    cmd: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Write a command to the Hamilton machine and read the response."""
    if not self.is_active:
      raise RuntimeError("HamiltonUSBDriver requires an active lifespan.")
    return await self._reply_router.send(
      cmd=cmd,
      id_=id_,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
    )

  @abstractmethod
  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Get the id from a firmware response."""

  @abstractmethod
  def check_fw_string_error(self, resp: str):
    """Raise an error if the firmware response is an error response."""

  @abstractmethod
  def _parse_response(self, resp: str, fmt: Any) -> dict:
    """Parse a firmware response."""

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Send a raw command to the machine."""
    if not self.is_active:
      raise RuntimeError("HamiltonUSBDriver requires an active lifespan.")
    return await self._reply_router.send_raw(
      command=command,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
    )
