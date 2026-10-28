"""The Vantage driver, responsible for
- carrying the connection and the transport,
- firmware protocol generation and parsing,
- orchestrating higher level tasks.
"""

import logging
from typing import Any, Dict, List, Optional, cast, overload

from pylabrobot.events import emit_event
from pylabrobot.hamilton.protocol.text.framing import assemble_channel_command, read_id
from pylabrobot.hamilton.protocol.text.router import ReplyRouter
from pylabrobot.hamilton.transport.text_tcp import (
  DEFAULT_PORT,
  HamiltonTextTCP,
  resolve_host,
)
from pylabrobot.hamilton.vantage.driver.configuration import DeviceConfiguration, ipg_is_fitted
from pylabrobot.hamilton.vantage.driver.errors import (
  VantageFirmwareError,
  check_fw_string_error,
  get_id_from_fw_response,
  is_spontaneous_message,
  parse_vantage_fw_string,
)
from pylabrobot.hamilton.vantage.driver.features.ipg import Ipg
from pylabrobot.hamilton.vantage.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.vantage.driver.lock import _FirmwareLock
from pylabrobot.io.io import IOBase
from pylabrobot.io.usb import USB
from pylabrobot.resources.hamilton.vantage_decks import VantageDeck

logger = logging.getLogger(__name__)

ID_VENDOR = 0x08AF
# Every Vantage reports this product id, so which machine is meant is told apart by its serial
# number or its USB address instead.
ID_PRODUCT = 0x8003

# How many characters a module is addressed with: `A1PM`.
MODULE_ID_LENGTH = 4

# How long to wait for the reply to the arm's pre-initialization, in seconds.
ARM_PRE_INITIALIZE_READ_TIMEOUT = 300


class VantageDriver:
  """Interface for the Hamilton Vantage.

  The machine is reached over USB, or over TCP when `host` is given. A command is a line of text
  addressed to a module (`A1AM` the arm, `A1PM` the pipetting channels), and its reply is matched
  to it by the id it carries.
  """

  def __init__(
    self,
    io: Optional[IOBase] = None,
    *,
    host: Optional[str] = None,
    port: int = DEFAULT_PORT,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    deck: Optional[VantageDeck] = None,
    read_timeout: int = 120,
    write_timeout: int = 30,
    packet_read_timeout: int = 3,
  ):
    """Create a Vantage interface.

    Nothing is opened until `setup`.

    Args:
      io: an already-built link to use instead of opening one from the arguments below.
      host: reach the machine over TCP instead of USB, by IP address, by a name the system can
        resolve, or by the name of a machine that answers the discovery broadcast. Resolved by
        `setup`.
      port: the TCP port, with `host`.
      device_address: the USB device address of the Vantage. Only useful if using more than one
        Vantage over USB.
      serial_number: the serial number of the Vantage. Only useful if using more than one Vantage
        over USB.
      deck: the deck to reflect the machine into. Optional: without one the driver still drives
        the machine, and nothing about where things are is modelled.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
      packet_read_timeout: timeout in seconds for reading a single packet.

    Raises:
      ValueError: If `host` is combined with `io`, `device_address` or `serial_number`: a machine
        is reached one way or the other.
    """
    if host is not None and (
      io is not None or device_address is not None or serial_number is not None
    ):
      raise ValueError(
        "`host` reaches the machine over TCP, so it cannot be combined with `io`, "
        "`device_address` or `serial_number`, which describe a USB link"
      )

    self._host = host
    self._port = port
    self._read_timeout = read_timeout
    self._write_timeout = write_timeout
    self._packet_read_timeout = packet_read_timeout

    self._io: Optional[IOBase] = None
    self._replies: Optional[ReplyRouter] = None
    if host is None:
      self._attach(
        io
        or USB(
          human_readable_device_name="Hamilton Vantage",
          id_vendor=ID_VENDOR,
          id_product=ID_PRODUCT,
          device_address=device_address,
          serial_number=serial_number,
          packet_read_timeout=packet_read_timeout,
          read_timeout=read_timeout,
          write_timeout=write_timeout,
        )
      )

    # One command at a time per module. Read-only requests are exempt, see `send_command`.
    self._lock = _FirmwareLock()

    self._connected = False

    # The deck to reflect the machine into, or None to drive it without a resource model.
    self.deck = deck

    self.configuration: Optional[DeviceConfiguration] = None

    # Built by `discover`, for the channels the machine turns out to have.
    self.pipettes: Optional[Pipettes] = None

    # Built by `discover`, and only if the machine has an internal plate gripper.
    self.ipg: Optional[Ipg] = None

  # ----------------------------------------
  # Connection and lifecycle
  # ----------------------------------------

  def _attach(self, io: IOBase) -> None:
    """Take a link to carry the commands, and the router that matches its replies."""
    self._io = io
    self._replies = ReplyRouter(
      io=io,
      module_id_length=MODULE_ID_LENGTH,
      parse_id=get_id_from_fw_response,
      raise_for_error=check_fw_string_error,
      is_spontaneous=is_spontaneous_message,
      packet_read_timeout=self._packet_read_timeout,
      read_timeout=self._read_timeout,
      # A socket belongs to one event loop; USB reads in an executor.
      read_on_owner_loop=isinstance(io, HamiltonTextTCP),
    )

  @property
  def io(self) -> IOBase:
    """The link to the machine.

    Raises:
      RuntimeError: If the driver reaches the machine by `host` and `setup` has not resolved it.
    """
    if self._io is None:
      raise RuntimeError(f"the link to {self._host!r} is built by `setup`")
    return self._io

  @property
  def _router(self) -> ReplyRouter:
    """The reply router of the link, which exists once the link has been built."""
    if self._replies is None:
      raise RuntimeError(f"the link to {self._host!r} is built by `setup`")
    return self._replies

  async def setup(self):
    """Connect to the machine, find out what it has, and bring it up.

    This moves the machine: the arm is pre-initialized if it reports itself down, then the IPG, if
    fitted, is initialized and parked, and the channels are initialized if they report themselves
    down or carry a tip. `discover` is the read-only part.

    Repeatable. A repeated setup goes over the link the first one opened, and initialization
    does nothing on a machine that is already up. A setup that fails part way closes the link.
    """
    logger.warning(
      "The Vantage driver is untested on hardware: check each motion before relying on it."
    )
    await self.connect()
    try:
      tip_presence = await self.discover()
      await self.initialize(tip_presence)
    except BaseException:
      await self.stop()
      raise

  async def connect(self) -> None:
    """Open the link to the machine, which moves nothing, and start reading its replies.

    `setup` does this first. Call it alone to read a machine with `discover` without bringing
    it up. Repeatable: a link already open is left as it is.
    """
    if self._connected:
      return
    logger.debug("Connecting to Vantage on %s ...", self._describe_link())
    await self._open()
    self._connected = True

  async def discover(self) -> List[bool]:
    """Read what the machine has: its channels, its arm and whether an IPG is fitted. Read-only:
    nothing moves.

    The IPG is fitted if the arm type names one or the stored node names include one. Either is
    enough, because an IPG that is fitted and not noticed can be driven into.

    Returns:
      Which channels carry a tip, from the first.

    Raises:
      RuntimeError: If the channels do not answer as `A1PM`, answer with no channel, or the arm
        answers neither question that tells whether an IPG is fitted.
    """
    pipettes = self.pipettes or Pipettes(self)
    try:
      tip_presence = await pipettes.request_tip_presence()
    except VantageFirmwareError as e:
      raise RuntimeError(
        "the channels did not answer `A1PM QA`, so the machine cannot be set up. This driver "
        "supports machines whose pipetting channels are addressed as `A1PM`; one whose channels "
        f"are addressed otherwise is not supported. The machine said: {e.errors}"
      ) from e
    if not tip_presence:
      raise RuntimeError("the machine reports no pipetting channel")

    arm_type: Optional[int] = None
    node_names: Optional[List[str]] = None
    try:
      arm_type = await self.request_arm_type()
    except VantageFirmwareError as e:
      logger.warning("the arm did not report its type: %s", e.errors)
    try:
      node_names = await self.request_node_names()
    except VantageFirmwareError as e:
      logger.warning("the arm did not report its node names: %s", e.errors)
    if arm_type is None and node_names is None:
      raise RuntimeError(
        "the arm answered neither `A1AM QU` nor `A1AM QN`, so whether an internal plate gripper "
        "is fitted is not known, and the machine is not set up: an IPG that is not accounted for "
        "can be driven into"
      )

    ipg_installed = ipg_is_fitted(arm_type or 0, node_names or [])
    self.configuration = DeviceConfiguration(
      num_channels=len(tip_presence), arm_type=arm_type or 0, ipg_installed=ipg_installed
    )
    self.pipettes = pipettes
    self.ipg = (self.ipg or Ipg(self)) if ipg_installed else None
    return tip_presence

  async def initialize(self, tip_presence: Optional[List[bool]] = None) -> None:
    """Bring the arm, the IPG and the channels up, doing nothing for what already is. Moves the
    machine.

    The arm comes first, because the IPG and the channels refuse to initialize before the arm has
    (the machine answers error 16). If an IPG is fitted, setup refuses before anything moves if it
    holds a plate. After the arm, the IPG is initialized if it reports itself down and parked if
    it is not parked, and only then do the channels move: they are initialized if they report
    themselves down or carry a tip, which they discard.

    Args:
      tip_presence: which channels carry a tip, as `discover` read it. If None, it is read again.

    Raises:
      RuntimeError: If the machine has not been discovered, or the IPG holds a plate. Nothing
        has moved then.
    """
    if self.pipettes is None:
      raise RuntimeError("the channels are found by `discover`, which has not run")
    if self.ipg is not None and await self.ipg.request_plate_presence():
      raise RuntimeError(
        "the IPG holds a plate, so the machine is not set up: bringing it up would move the "
        "plate. Take the plate out of the gripper first"
      )
    if not await self.request_arm_initialization_status():
      await self.pre_initialize_arm()
    if self.ipg is not None:
      if not await self.ipg.request_initialization_status():
        await self.ipg.initialize()
      if not await self.ipg.request_parking_status():
        await self.ipg.park()
    if tip_presence is None:
      tip_presence = await self.pipettes.request_tip_presence()
    if not await self.pipettes.request_initialization_status() or any(tip_presence):
      await self.pipettes.initialize()

  async def _open(self):
    """Open the link, resolving the host first when there is one, and start reading replies."""
    if self._host is not None:
      self._attach(
        HamiltonTextTCP(
          await resolve_host(self._host),
          self._port,
          write_timeout=self._write_timeout,
        )
      )
    await self.io.setup()
    self._router.start()

  async def _close(self):
    """Stop reading replies and close the link."""
    self._router.stop()
    await self.io.stop()

  async def stop(self):
    """Close the link.

    The machine keeps its state; only this driver lets go of it.

    Repeatable. A machine already let go of is left alone.
    """
    if not self._connected:
      logger.debug("the link is already closed")
      return
    self._connected = False
    await self._close()

  @property
  def connected(self) -> bool:
    """Whether the link is open. Commands can be sent only while it is."""
    return self._connected

  def _describe_link(self) -> str:
    """How the machine is reached, in whatever terms its link is addressed by."""
    if self._io is None:
      return f"HamiltonTextTCP ({self._host}:{self._port})"
    fields = self._io.serialize()
    link = type(self._io).__name__
    vendor, product = fields.get("id_vendor"), fields.get("id_product")
    if vendor is not None and product is not None:
      link += f" {vendor:#06x}:{product:#06x}"
    named = [
      f"{label} {fields[key]}"
      for label, key in (
        ("address", "device_address"),
        ("serial", "serial_number"),
        ("port", "port"),
      )
      if fields.get(key)
    ]
    return link + (f" ({', '.join(named)})" if named else "")

  @property
  def num_channels(self) -> int:
    """The number of pipetting channels the machine has.

    Raises:
      RuntimeError: If the machine has not been discovered.
    """
    if self.configuration is None:
      raise RuntimeError("the number of channels is read from the machine by `discover`")
    return self.configuration.num_channels

  # ----------------------------------------
  # Low-level I/O
  # ----------------------------------------

  @overload
  async def send_command(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    *,
    fmt: Dict[str, str],
    **kwargs: Any,
  ) -> Dict[str, Any]: ...

  @overload
  async def send_command(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    fmt: None = None,
    **kwargs: Any,
  ) -> str: ...

  async def send_command(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    fmt: Optional[Dict[str, str]] = None,
    **kwargs,
  ):
    """Assemble a firmware command, send it, and parse the reply if a format is given.

    A module runs one command at a time, so a command waits for the one before it to the same
    module. Commands to different modules go together. Read-only `R` and `Q` commands take no
    lock: a module answers them while a command is in flight.

    Args:
      module: the module to address the command to, such as `A1PM`.
      command: the two-letter command code.
      auto_id: whether to give the command an id, which its reply is matched to. A reply without
        one cannot be parsed with `fmt`.
      tip_pattern: which channels a list parameter is for, when it holds one value per channel
        involved. If None, each list holds one value per channel it names.
      write_timeout: seconds to wait for the command to be written.
      read_timeout: seconds to wait for the reply.
      fmt: the parameters to read out of the reply, by name and type, as
        `parse_vantage_fw_string` takes them.

    Returns:
      The parsed reply when a format was given, and the raw reply otherwise.

    Raises:
      RuntimeError: If the link is not open.
      VantageFirmwareError: If the machine reports an error.
    """
    arguments = dict(
      auto_id=auto_id,
      tip_pattern=tip_pattern,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      fmt=fmt,
      **kwargs,
    )
    if command[0] in ("R", "Q"):
      return await self._send(module, command, **arguments)
    async with self._lock.module(module):
      return await self._send(module, command, **arguments)

  async def _send(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    fmt: Optional[Dict[str, str]] = None,
    **kwargs,
  ):
    """Assemble, send and parse, without coordinating against anything else in flight."""
    self._require_connection()
    id_ = self._router.next_id() if auto_id else None
    # The count is only read when there is a list to terminate, so a command that carries none
    # can be sent before the machine has been discovered.
    carries_a_list = any(isinstance(value, list) for value in kwargs.values())
    cmd = assemble_channel_command(
      module=module,
      command=command,
      id_=id_,
      tip_pattern=tip_pattern,
      num_channels=self.num_channels if carries_a_list else 0,
      **kwargs,
    )
    event_data = {
      "transport": "hamilton_text_tcp" if isinstance(self.io, HamiltonTextTCP) else "hamilton_usb",
      "driver": type(self).__name__,
      "module": module,
      "command": command,
      "command_id": id_,
      "raw_command": cmd,
    }
    emit_event("firmware.command.started", **event_data)
    try:
      resp = cast(str, await self._transmit(cmd, id_, write_timeout, read_timeout))
      result = parse_vantage_fw_string(resp, fmt) if fmt is not None else resp
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

  async def _transmit(
    self,
    cmd: str,
    id_: Optional[int],
    write_timeout: Optional[int],
    read_timeout: Optional[int],
    wait: bool = True,
  ) -> Optional[str]:
    """Put an assembled command on the link and return the reply that answers it.

    The one place a command leaves the driver, so a simulated machine can answer here.

    Args:
      cmd: the command, as it is written.
      id_: the id the command carries, which its reply is recognised by.
      write_timeout: seconds to wait for the command to be written.
      read_timeout: seconds to wait for the reply.
      wait: whether to wait for the reply. If not, None is returned once the command is written.

    Raises:
      VantageFirmwareError: If the machine reports an error.
    """
    return await self._router.send(
      cmd=cmd, id_=id_, write_timeout=write_timeout, read_timeout=read_timeout, wait=wait
    )

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Send a raw command to the machine.

    Args:
      command: the command exactly as it is to be written.
      write_timeout: seconds to wait for the command to be written.
      read_timeout: seconds to wait for the reply.
      wait: whether to wait for the reply.

    Returns:
      Whatever the machine answered, or None when nothing was waited for.

    Raises:
      RuntimeError: If the link is not open.
    """
    self._require_connection()
    return await self._transmit(command, read_id(command), write_timeout, read_timeout, wait)

  def _require_connection(self) -> None:
    """Raise unless the link is open. A command may not be sent into a closed link."""
    if not self._connected:
      raise RuntimeError("not connected to a machine; call `setup` first")

  # ----------------------------------------
  # The arm
  # ----------------------------------------

  async def request_arm_initialization_status(self) -> bool:
    """Whether the arm is initialized.

    Returns:
      True if the arm module reports itself initialized, False otherwise.
    """
    resp = await self.send_command(module="A1AM", command="QW", fmt={"qw": "int"})
    return bool(resp["qw"] == 1)

  async def request_arm_type(self) -> int:
    """Which arm the machine has: the arm type, as the arm module reports it."""
    resp = await self.send_command(module="A1AM", command="QU", fmt={"kt": "int"})
    return int(resp["kt"])

  async def request_node_names(self) -> List[str]:
    """The names of the modules the arm has stored, in installation order. The IPG is `R0`."""
    resp = await self.send_command(module="A1AM", command="QN", fmt={"xn": "str"})
    return str(resp["xn"]).split()

  async def pre_initialize_arm(self) -> None:
    """Initialize the arm module. Moves the arm."""
    await self.send_command(
      module="A1AM", command="MI", read_timeout=ARM_PRE_INITIALIZE_READ_TIMEOUT
    )
