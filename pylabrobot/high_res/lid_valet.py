import asyncio
import logging
import re
import time
from typing import Dict, Literal, Optional

from pylabrobot.high_res.settings import HighResLidValetSettings
from pylabrobot.io.socket import Socket

logger = logging.getLogger(__name__)


# The controller acknowledges an accepted command with "ACK! " + the command,
# then streams progress until it reports one of these completion sentinels.
ACK = "ACK! "
OK = "OK!"
ERROR = "ERROR!"
ABORTED = "ABORTED!"

# State of a nest reported by the "status" command.
LidValetState = Literal["busy", "open", "has_lid", "error", "unknown"]

MSG_ABORTED = "Operation has been aborted"
MSG_BUSY = "LidValet is busy"
MSG_COMMUNICATION_TIMEOUT = "Timeout waiting for response from LidValet"
MSG_INVALID_RESPONSE = "Invalid response from the instrument"
MSG_HAS_LID = "Suction cup is holding a lid"
MSG_NO_LID = "Suction cup has no lid"
MSG_OPERATION_TIMEOUT = "Timeout waiting for operation to complete"


class HighResLidValetError(Exception):
  """Exceptions raised by a HighRes LidValet."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class HighResLidValet:
  """HighRes LidValet delidder.

  A benchtop delidder that removes and replaces plate lids with vacuum suction
  cups. It has one or more nests, each addressed by a 1-based number; a mover
  presents a lidded plate to a nest, the LidValet lifts the lid into its suction
  cup ("delid"), and later lowers it back onto the plate ("lid").

  The controller is a TCP device server (default ``192.168.127.60:1000``,
  printed on the back of the device) speaking an ASCII command/ACK protocol.
  A command is sent as a newline-terminated ASCII line; the server replies with
  ``ACK! <command> <id>`` and then streams text until it reports ``OK!``
  (success), ``ERROR!`` (failure, with a preceding error line), or ``ABORTED!``.

  Commands (``<n>`` is the 1-based nest number, which the server calls a hotel;
  ``reset`` and ``status`` address every nest at once when ``<n>`` is omitted):

  ::

    reset <n>    home/reset a nest, or every nest
    unlid <n>    lift the lid off the plate into the suction cup (delid)
    lid <n>      lower the held lid back onto the plate
    status <n>   report nest state (BUSY / OPEN / HAS_LID / ERROR)
    home         home the whole system
    wave <c>     synchronously drop and raise the lifts <c> times
    vacon/vacoff <n>      turn the vacuum cycle on a nest on/off
    purgeon/purgeoff <n>  turn the purge cycle on a nest on/off
    clearabort            clear the abort state
    errors/history/settings/version/firmwareversion/detailedversion
    change/save/revert    edit, persist or discard in-memory settings
    savecvm               save the CVM program to the Copley controllers
    list/info/help        what the server accepts; "all" includes maintenance
    reboot                reboot the device

  The nest count is read from the device's ``ACTIVE_HOTELS`` setting at setup.
  """

  def __init__(
    self,
    host: str = "192.168.127.60",
    port: int = 1000,
    ack_timeout: float = 10.0,
    command_timeout: float = 30.0,
    reset_timeout: float = 60.0,
    status_timeout: float = 3.0,
    busy_timeout: float = 30.0,
    status_poll_interval: float = 0.2,
    lid_retries: int = 4,
    lid_retry_delay: float = 1.5,
  ) -> None:
    # None until setup() reads ACTIVE_HOTELS off the device.
    self._num_nests: Optional[int] = None
    self.ack_timeout = ack_timeout
    self.command_timeout = command_timeout
    self.reset_timeout = reset_timeout
    self.status_timeout = status_timeout
    self.busy_timeout = busy_timeout
    self.status_poll_interval = status_poll_interval
    self.lid_retries = lid_retries
    self.lid_retry_delay = lid_retry_delay
    # One command owns the connection from its write until its completion reply.
    self._command_lock = asyncio.Lock()
    self.io = Socket(
      human_readable_device_name="HighRes LidValet",
      host=host,
      port=port,
      read_timeout=ack_timeout,
      write_timeout=ack_timeout,
    )

  async def setup(self) -> None:
    await self.io.setup()
    self._num_nests = (await self.request_settings()).active_hotels
    # Reset every nest, refusing to start with a lid stuck in a suction cup.
    stuck = [
      nest for nest, state in (await self.request_all_states()).items() if state == "has_lid"
    ]
    if stuck:
      raise HighResLidValetError(
        title="Nest {} has lid in suction cup. Please remove".format(
          ", ".join(str(nest) for nest in stuck)
        )
      )
    await self.reset()
    logger.info("[LidValet] connected: num_nests=%d", self.num_nests)

  @property
  def num_nests(self) -> int:
    """How many nests the device has, read from it at setup."""
    if self._num_nests is None:
      raise RuntimeError("nest count unknown; call setup() first")
    return self._num_nests

  async def stop(self) -> None:
    """Ask the server to close this client's connection, then drop the socket."""
    try:
      await self.io.write(b"disconnect\n")
    except Exception:  # the link may already be gone; closing locally still must happen
      logger.debug("[LidValet] disconnect not delivered", exc_info=True)
    await self.io.stop()

  # === Command layer ===

  def _validate_nest(self, nest: int) -> None:
    if not 1 <= nest <= self.num_nests:
      raise ValueError(f"nest must be 1..{self.num_nests}")

  async def _read_chunk(self, timeout: float) -> str:
    """Read whatever is available, returning "" if nothing arrives in time."""
    try:
      data = await self.io.read(4096, timeout=timeout)
    except TimeoutError:
      return ""
    return data.decode("ascii", errors="replace")

  def _parse_error(self, buffer: str) -> str:
    """Extract the error message from a reply that reported ``ERROR!``."""
    before = buffer.split(ERROR, 1)[0]
    for line in reversed(before.splitlines()):
      if "Error" in line:
        return line.strip()
    return before.strip()

  async def send_command(self, command: str, timeout: float) -> str:
    """Send a command and wait for the controller to complete it.

    Returns the accumulated reply text on ``OK!``; raises
    :class:`HighResLidValetError` on ``ERROR!``, ``ABORTED!``, an invalid
    acknowledgement, or a timeout.

    A command occupies the connection until the controller reports completion,
    so concurrent callers are serialized rather than interleaving their replies.
    """
    async with self._command_lock:
      await self.io.write(f"{command}\n".encode("ascii"))
      logger.debug("[LidValet] send: %s", command)

      buffer = await self._read_chunk(self.ack_timeout)
      if f"{ACK}{command}" not in buffer and OK not in buffer:
        if not buffer:
          raise HighResLidValetError(title=MSG_COMMUNICATION_TIMEOUT, message=command)
        raise HighResLidValetError(title=MSG_INVALID_RESPONSE, message=repr(buffer))

      deadline = time.monotonic() + timeout
      while True:
        if ABORTED in buffer:
          raise HighResLidValetError(title=MSG_ABORTED, message=command)
        if ERROR in buffer:
          raise HighResLidValetError(title=self._parse_error(buffer))
        if OK in buffer:
          logger.debug("[LidValet] recv: %s", buffer.strip())
          return buffer
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise HighResLidValetError(title=MSG_OPERATION_TIMEOUT, message=command)
        buffer += await self._read_chunk(min(remaining, self.ack_timeout))

  def _parse_state(self, buffer: str) -> LidValetState:
    if "BUSY" in buffer:
      return "busy"
    if "OPEN" in buffer:
      return "open"
    if "HAS_LID" in buffer:
      return "has_lid"
    # A nest whose last operation failed reports "ERROR (last failed)" until it is reset.
    if "ERROR" in buffer:
      return "error"
    return "unknown"

  # === Status ===

  async def request_state(self, nest: int) -> LidValetState:
    """Query a nest: "busy", "open", "has_lid", "error", or "unknown"."""
    self._validate_nest(nest)
    buffer = await self.send_command(f"status {nest}", self.status_timeout)
    return self._parse_state(buffer)

  async def request_all_states(self) -> Dict[int, LidValetState]:
    """Query every nest in one round trip, keyed by 1-based nest number."""
    buffer = await self.send_command("status", self.status_timeout)
    states: Dict[int, LidValetState] = {}
    for line in buffer.splitlines():
      match = re.match(r"\s*hotel\s+(\d+)\s*:\s*(.+)", line)
      if match:
        states[int(match.group(1))] = self._parse_state(match.group(2))
    return states

  async def request_has_lid(self, nest: int) -> bool:
    """Whether the suction cup at ``nest`` is holding a lid. Raises if busy."""
    state = await self.request_state(nest)
    if state == "busy":
      raise HighResLidValetError(title=MSG_BUSY, message=f"nest {nest}")
    return state == "has_lid"

  async def request_is_busy(self, nest: int) -> bool:
    """Whether ``nest`` is currently busy."""
    return await self.request_state(nest) == "busy"

  async def wait_until_ready(self, nest: int, timeout: Optional[float] = None) -> None:
    """Poll ``nest`` until it is no longer busy."""
    self._validate_nest(nest)
    timeout = self.busy_timeout if timeout is None else timeout
    deadline = time.monotonic() + timeout
    while await self.request_is_busy(nest):
      if time.monotonic() > deadline:
        raise HighResLidValetError(title=MSG_BUSY, message=f"nest {nest}")
      await asyncio.sleep(self.status_poll_interval)

  # === Operations ===

  async def reset(self, nest: Optional[int] = None) -> None:
    """Home/reset a nest, or every nest when ``nest`` is omitted."""
    if nest is None:
      await self.send_command("reset", self.reset_timeout)
      logger.info("[LidValet] all nests reset")
      return
    self._validate_nest(nest)
    await self.send_command(f"reset {nest}", self.reset_timeout)
    logger.info("[LidValet] nest %d reset", nest)

  async def delid(self, nest: int) -> None:
    """Lift the lid off the plate at ``nest`` into the suction cup.

    The suction cup must be empty; raises if it is already holding a lid or if
    the nest is busy.
    """
    self._validate_nest(nest)
    state = await self.request_state(nest)
    if state == "busy":
      raise HighResLidValetError(title=MSG_BUSY, message=f"nest {nest}")
    if state == "has_lid":
      raise HighResLidValetError(title=MSG_HAS_LID, message=f"nest {nest}")
    await self.send_command(f"unlid {nest}", self.command_timeout)
    logger.info("[LidValet] nest %d delidded", nest)

  async def lid(self, nest: int) -> None:
    """Lower the held lid back onto the plate at ``nest``.

    The suction cup must be holding a lid. Retries a few times to ride out a
    transient busy/no-lid state, matching the device server's own behaviour.
    """
    self._validate_nest(nest)
    last_error: Optional[HighResLidValetError] = None
    for attempt in range(self.lid_retries):
      try:
        state = await self.request_state(nest)
        if state == "busy":
          raise HighResLidValetError(title=MSG_BUSY, message=f"nest {nest}")
        if state != "has_lid":
          raise HighResLidValetError(title=MSG_NO_LID, message=f"nest {nest}")
        await self.send_command(f"lid {nest}", self.command_timeout)
        logger.info("[LidValet] nest %d lidded", nest)
        return
      except HighResLidValetError as error:
        last_error = error
        if attempt < self.lid_retries - 1:
          logger.warning("[LidValet] retry lidding nest %d: %s", nest, error)
          await asyncio.sleep(self.lid_retry_delay)
    assert last_error is not None
    raise last_error

  async def home(self) -> None:
    """Home the whole system."""
    await self.send_command("home", self.reset_timeout)
    logger.info("[LidValet] homed")

  async def wave(self, cycles: int = 1) -> None:
    """Drop and raise the lifts ``cycles`` times, synchronously."""
    if cycles < 1:
      raise ValueError("cycles must be at least 1")
    await self.send_command(f"wave {cycles}", self.command_timeout * cycles)
    logger.info("[LidValet] waved %d cycle(s)", cycles)

  # === Pneumatics ===

  async def set_vacuum(self, nest: int, on: bool) -> None:
    """Turn the vacuum cycle on ``nest`` on or off."""
    self._validate_nest(nest)
    await self.send_command(f"{'vacon' if on else 'vacoff'} {nest}", self.command_timeout)
    logger.info("[LidValet] nest %d vacuum %s", nest, "on" if on else "off")

  async def set_purge(self, nest: int, on: bool) -> None:
    """Turn the purge cycle on ``nest`` on or off."""
    self._validate_nest(nest)
    await self.send_command(f"{'purgeon' if on else 'purgeoff'} {nest}", self.command_timeout)
    logger.info("[LidValet] nest %d purge %s", nest, "on" if on else "off")

  # === Diagnostics ===

  def _payload(self, buffer: str) -> str:
    """Strip the ``ACK!``/``OK!`` envelope, leaving the reply body."""
    body = [
      line
      for line in buffer.splitlines()
      if not line.startswith(ACK.strip()) and not line.startswith(OK)
    ]
    return "\n".join(body).strip()

  async def clear_abort(self) -> None:
    """Clear the abort state."""
    await self.send_command("clearabort", self.command_timeout)

  async def request_version(self) -> str:
    """Return the software version report."""
    return self._payload(await self.send_command("version", self.status_timeout))

  async def request_firmware_version(self) -> str:
    """Return the firmware version."""
    return self._payload(await self.send_command("firmwareversion", self.status_timeout))

  async def request_detailed_version(self) -> str:
    """Return version details about all hardware."""
    return self._payload(await self.send_command("detailedversion", self.command_timeout))

  async def request_errors(self, count: Optional[int] = None) -> str:
    """Return the top errors on the device's error stack."""
    command = "errors" if count is None else f"errors {count}"
    return self._payload(await self.send_command(command, self.status_timeout))

  async def request_history(self, count: Optional[int] = None) -> str:
    """Return the last commands sent to the server."""
    command = "history" if count is None else f"history {count}"
    return self._payload(await self.send_command(command, self.status_timeout))

  async def request_command_status(self, command_id: int) -> str:
    """Return the status of a previously issued command."""
    return self._payload(await self.send_command(f"commandstat {command_id}", self.status_timeout))

  async def request_home_offset(self, address: int) -> str:
    """Return the home offset for the motor at ``address`` (a Copley address).

    Unknown addresses are reported by the controller as an error, but address 0
    hangs its command queue outright and takes the device server down with it.
    """
    if address < 1:
      raise ValueError("address must be at least 1; address 0 hangs the controller")
    return self._payload(await self.send_command(f"queryhomeoffset {address}", self.status_timeout))

  # === Settings ===

  async def request_raw_settings(self) -> str:
    """Return the settings file as the device reports it."""
    return self._payload(await self.send_command("settings", self.command_timeout))

  async def request_settings(self) -> HighResLidValetSettings:
    """Return the device's settings as a typed, frozen object."""
    return HighResLidValetSettings.from_lines((await self.request_raw_settings()).splitlines())

  async def change_setting(self, setting: str, value: str) -> None:
    """Change a setting in memory. Lost on restart unless :meth:`save_settings` follows."""
    if " " in value:
      raise ValueError("values with embedded spaces cannot be assigned")
    await self.send_command(f"change {setting} {value}", self.command_timeout)

  async def save_settings(self) -> None:
    """Persist the in-memory settings to permanent memory."""
    await self.send_command("save", self.command_timeout)

  async def revert_settings(self) -> None:
    """Discard in-memory settings changes, restoring the stored values."""
    await self.send_command("revert", self.command_timeout)

  async def save_cvm(self) -> None:
    """Save the CVM program to every Copley motor controller."""
    await self.send_command("savecvm", self.reset_timeout)

  async def reboot(self) -> None:
    """Reboot the device. The connection drops; call :meth:`setup` again afterwards."""
    await self.io.write(b"reboot\n")
    logger.info("[LidValet] reboot requested")

  # === Server introspection ===

  async def list_commands(self, include_maintenance: bool = False) -> str:
    """List the commands the server recognizes."""
    command = "list all" if include_maintenance else "list"
    return self._payload(await self.send_command(command, self.command_timeout))

  async def request_command_info(self, include_maintenance: bool = False) -> str:
    """List the server's commands with their parameter information."""
    command = "info all" if include_maintenance else "info"
    return self._payload(await self.send_command(command, self.command_timeout))

  async def request_command_help(self, command: str) -> str:
    """Return the parameter information for a single command."""
    return self._payload(await self.send_command(f"help {command}", self.status_timeout))
