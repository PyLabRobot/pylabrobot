import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

# Every reply is framed between STX (0x02) and ETX (0x03). A command is answered
# with an ACK frame (a lone 0x06), a command-echo frame ("<cmd>OK"), and then a
# result frame (a "Status..." word, or "CommandIgnore" when the command is a
# no-op or refused). Motions run asynchronously: the status word is "StatusBUSY"
# while moving, then changes to the operation-specific terminal state.
STX = b"\x02"
ETX = b"\x03"
ACK = "\x06"

# Single-character commands the decapper understands.
STATUS = "a"
DECAP_START = "h"
RECAP_START = "i"
WASTE = "b"
OPEN_TRAY = "f"
CLOSE_TRAY = "g"
HOME_ALL = "Z"
RESET = "z"
STANDBY = "j"
READY = "k"

COMMAND_IGNORE = "CommandIgnore"

# Fault descriptions as reported by the instrument firmware.
ERROR_MESSAGES = {
  "StatusNotOK": (
    "Status is not ok. The device is in an error state. Check whether the plate is already "
    "decapped (if so, recap and initialize again); otherwise clear the error on the device and "
    "cycle the power."
  ),
  "NeedToRecap": "Decap operation is already done. Select the recap operation on the device.",
  "NeedToDecap": "Recap operation is already executed.",
  "DecapWasNotSuccesful": (
    "Decapping was not successful. Fix the error on the device manually and check whether any "
    "tubes are still on the head."
  ),
  "RecapWasNotSuccesful": "Recapping was not successful. Fix the error on the device manually.",
  "StoreWasNotSuccesful": "Storing was not successful. Fix the error on the device manually.",
  "OpenTrayWasNotSuccesful": (
    "Opening the tray was not successful. Fix the error on the device manually."
  ),
  "CloseTrayWasNotSuccesful": (
    "Closing the tray was not successful. Fix the error on the device manually."
  ),
  "HomeNotSuccesful": (
    "Device was not able to reach the home position. The device is in an error state. "
    "Restart the device."
  ),
  "CannotReset": (
    "Cannot send the reset command to the device. The device is in an error state. "
    "Reset the device and try again."
  ),
  "ResetNotSuccesful": (
    "Reset did not complete. The device is in an error state. Reset the device and try again."
  ),
  "CannotGoInStandbyMode": "Cannot go to standby mode. Check the errors on the device.",
  "StatusManual": (
    "The device is in manual recovery mode. Inspect the rack and cap head, then complete the "
    "appropriate recovery from the instrument touchscreen before sending another motion command."
  ),
  "CommandIgnore": "Command was ignored by the device.",
  "NoAck": "Device did not acknowledge the command.",
}

# Legacy serial firmware generally reports only a generic frame such as
# "DecapERROR", but some firmware may include a numeric error code in a reply.
#
# Source: Azenta IntelliXcap User Manual, part 319430 Rev. E, pp. 88-91:
# https://web.azenta.com/hubfs/azenta-files/resources/manuals-guides/319430-IXC-User-Manual.pdf
ERROR_CODE_MESSAGES: Dict[int, str] = {
  100: (
    "M1 top switch not detected during homing sequence. Could get overwritten by other error "
    "codes within higher level sequencing logic, so it is most likely during startup."
  ),
  101: "M2 initial homing failure. Likely to override other M1 homing error codes.",
  102: "M1 top switch stuck closed during homing sequence.",
  103: "M1 top switch second trigger not detected during homing sequence.",
  104: "M4 homing error: top switch not detected.",
  105: (
    "M3 top switch not detected during homing sequence. Could get overwritten by other error "
    "codes within higher level sequencing logic, so it is most likely during startup."
  ),
  106: "M3 stop switch stuck closed during homing sequence.",
  107: "M3 top switch second trigger not detected during homing sequence.",
  108: "M3 initial homing failure. Likely to override other M3 homing error codes.",
  109: (
    "M2 top switch not detected during homing sequence. Could get overwritten by other error "
    "codes within higher level sequencing logic, so it is most likely during startup."
  ),
  110: "M2 top switch stuck closed during homing sequence.",
  111: "M2 top switch second trigger not detected during homing sequence.",
  112: "Door close failure.",
  113: (
    "M1 moved to M1_SAFETY_LOW_POS (S33): no light curtain trigger was detected while "
    "scanning for caps."
  ),
  114: "Invalid tube height detected.",
  115: "Door open failure.",
  116: "Door close failure at start of sequence.",
  117: (
    "M1 moved to M1_SAFETY_LOW_POS (S33): no light curtain trigger was detected while "
    "scanning for caps."
  ),
  118: "Invalid tube height detected.",
  119: "Open door failure.",
  120: "Open door failure on entry to manual mode.",
  121: "Door close failure.",
  122: "M3 limit switch timeout on cartridge eject.",
  123: "Door open failure at end of cartridge eject sequence.",
  124: "Door close failure at end of cartridge eject sequence.",
  125: "M1 failed to reach the waste position within S4 during the auto-waste sequence.",
  133: "M1 homing error.",
  134: "Open door failure.",
  135: ("Cap detected at valid height. This may be an informational device-state code."),
  136: "Maximum decap attempts exceeded (S46).",
  137: "Maximum recap attempts exceeded (S45).",
  138: (
    "M3 bottom switch closed while the motor was still moving; extended-stage lead-screw "
    "protection activated."
  ),
  139: "Open tray failure; no cartridge detected after initial homing.",
  140: (
    "Cartridge-ejected notification; or the door should be up but the top switch was not detected."
  ),
  141: "The door should be down but the bottom switch was not detected.",
  142: "Unexpected object on tray during cartridge eject.",
  143: "Cartridge not detected during cartridge load sequence.",
  144: (
    "Cartridge detection height incorrect during cartridge load sequence: detected height "
    "was less than S73 - S59."
  ),
  145: "Light curtain calibration max retries exceeded.",
  146: "Light curtain calibration max retries exceeded.",
  147: "Light curtain calibration max retries exceeded.",
  148: "Tray open failure.",
  150: "M3 homing error during auto-waste sequence.",
  151: "Tray close failure.",
  152: "Tube detected after decap retry; caps were screwed back on.",
  153: "Close tray failure; M3 homing error.",
  154: "Close tray failure.",
  155: "Open door failure.",
  156: "M1 homing error.",
  157: "M2 homing error.",
  158: "M3 homing error.",
  159: "M2 homing error.",
  160: "Door close failure at end of sequence; tray open failure.",
  161: "M4 homing error.",
  164: "Tray open failure.",
  165: "Sequence-state error; the same firmware logic may report error 167.",
  166: "M2 homing error during tray decap-quit.",
  167: "Door-open or tray-close failure during decap-quit.",
  200: "Light curtain communications failure: no Modbus data received.",
  201: "Light curtain signal failure; check wiring between controller and light curtain.",
  202: (
    "Conflicting limit switches: top and bottom switches both appear closed. This usually "
    "indicates a power-supply failure or a faulty switch."
  ),
  238: "Emergency stop engaged or motor voltage low.",
}


def get_error_message(code: int) -> str:
  """Return the documented meaning of an IntelliXcap error code.

  Some codes have multiple meanings because their interpretation depends on
  the firmware sequence that reported them.
  """
  message = ERROR_CODE_MESSAGES.get(code)
  if message is None:
    return "Unknown IntelliXcap error code."
  return message


class FluidXError(Exception):
  """Exceptions raised by a FluidX IntelliXcap 96 decapper."""

  def __init__(
    self,
    title: str,
    message: Optional[str] = None,
    error_code: Optional[int] = None,
  ) -> None:
    self.title = title
    self.message = message
    self.error_code = error_code

  @classmethod
  def from_error_code(cls, code: int, detail: Optional[str] = None) -> "FluidXError":
    """Build an exception from a numeric IntelliXcap error code."""
    meaning = get_error_message(code)
    message = f"{meaning} {detail}" if detail else meaning
    return cls(
      title=f"IntelliXcap error {code}",
      message=message,
      error_code=code,
    )

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


def _fault(key: str, detail: Optional[str] = None) -> FluidXError:
  """Build a FluidXError carrying the firmware's own description for ``key``."""
  return FluidXError(title=ERROR_MESSAGES.get(key, key), message=detail)


def _error_code(frames: List[str]) -> Optional[int]:
  """Extract a known three-digit error code from serial reply frames."""
  for frame in frames:
    for value in re.findall(r"(?<!\d)(\d{3})(?!\d)", frame):
      code = int(value)
      if code in ERROR_CODE_MESSAGES:
        return code
  return None


class FluidXIntelliXcap96:
  """FluidX IntelliXcap 96 automated screw-cap decapper.

  A benchtop instrument that decaps and recaps a 96-format rack of screw-cap
  tubes in a single stroke. It holds one nest; a plate mover loads the rack, the
  decapper unscrews all 96 caps (``decap``), holds them, and screws them back on
  (``recap``). Held caps can also be released into a separately positioned cap
  carrier (``waste``), and the loading tray opened and closed. ``waste`` does
  not verify that a carrier is present: remove the tube rack and position the
  correct carrier before using it. If the rack is left beneath the head, the
  released caps can fall back onto the tubes without being properly recapped.

  Serial settings:
    9600 baud, 8 data bits, no parity, 1 stop bit, no handshake. Replies are
    framed between STX (0x02) and ETX (0x03).

  Tube type and volume are not sent over the serial protocol. The installed
  IntelliCartridge and its firmware profile define the supported tube/cap
  geometry and motion settings. Fit and configure the cartridge specified for
  the exact tube family; volume alone (for example, 0.5 mL) is not sufficient
  to select a compatible cartridge.

  Single-character commands, each written followed by ETX:
    a   request status
    h   start decapping
    i   start recapping
    b   release held caps into a separately positioned carrier
    f   open the tray
    g   close the tray
    Z   home all axes
    z   reset
    j   enter standby
    k   leave standby (ready)
  A command is answered with an ACK frame (0x06), a ``<cmd>OK`` echo frame, and a
  result frame. The status word is ``StatusOK`` when idle, ``StatusBUSY`` while a
  motion runs, ``StatusSLEEP`` in standby, and carries ``ERROR``/``Recap``/
  ``Decap`` for a fault or a pending inverse operation. A refused or no-op
  command answers with ``CommandIgnore``. Motions complete when the status word
  returns from ``StatusBUSY`` to ``StatusOK``.

  A fault latches the device in ``StatusError`` (e.g. running a decap with no
  rack loaded). This is cleared only by homing -- the reset command is ignored.
  With ``auto_recover`` enabled (the default), an operation issued while the
  device is latched in error homes to recover and then proceeds.

  Verified against hardware: connection, status, tray open/close, home,
  standby/ready, the decap error/recovery path, and decap/recap with a loaded
  0.5 mL rack, including release of held caps with ``waste``.

  See the Azenta IntelliXcap user manual for the required carrier and physical
  setup:
  https://web.azenta.com/hubfs/azenta-files/resources/manuals-guides/319430-IXC-User-Manual.pdf
  """

  def __init__(
    self,
    port: str,
    timeout: float = 5.0,
    command_delay: float = 0.3,
    frame_gap: float = 0.5,
    poll_interval: float = 1.0,
    auto_recover: bool = True,
    recover_timeout: float = 30.0,
  ) -> None:
    """
    Args:
      port: serial port the decapper is connected to.
      timeout: serial read/write timeout in seconds.
      command_delay: pause after writing a command before reading its reply.
      frame_gap: how long to wait for another reply frame before concluding the
        reply is complete.
      poll_interval: pause between status polls while a motion runs.
      auto_recover: when an operation finds the device latched in StatusError,
        home it to clear the error and continue. A latched error is only cleared
        by homing (reset is a no-op on this firmware). Disable to make a latched
        error raise instead.
      recover_timeout: timeout in seconds for the recovery home.
    """
    self.command_delay = command_delay
    self.frame_gap = frame_gap
    self.poll_interval = poll_interval
    self.auto_recover = auto_recover
    self.recover_timeout = recover_timeout
    self.io = Serial(
      human_readable_device_name="FluidX IntelliXcap 96",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=timeout,
    )

  async def setup(self) -> None:
    await self.io.setup()
    status = await self.request_status()
    up = status.upper()
    if "BUSY" in up:
      # At connect there is no motion in flight, so a persistent StatusBUSY means
      # the instrument is locked out. By far the most common cause is an engaged
      # e-stop; the safety guard/hood and other interlocks do the same. There is
      # no command to read the e-stop directly, so surface the likely cause here.
      logger.error(
        "[IntelliXcap96 %s] reports StatusBUSY at connect and will ignore commands. "
        "Check the E-STOP first (most common cause), then the safety guard/hood and "
        "interlocks, and retry.",
        self.io.port,
      )
      raise FluidXError(
        title="Decapper is not ready (StatusBUSY)",
        message=(
          "The device reports BUSY and ignores commands. This is almost always an engaged "
          "E-STOP; also check the safety guard/hood and interlocks, then retry."
        ),
      )
    if "MANUAL" in up:
      raise _fault("StatusManual", status)
    if "ERROR" in up:
      raise _fault("StatusNotOK", status)
    logger.info("[IntelliXcap96 %s] connected: %s", self.io.port, status)

  async def stop(self) -> None:
    """Close the serial connection."""
    await self.io.stop()

  # === Framed command layer ===

  async def _send(self, command: str) -> None:
    """Discard pending input, write a command with its terminator, then pace."""
    await self.io.reset_input_buffer()
    await self.io.write(command.encode("ascii") + ETX)
    await asyncio.sleep(self.command_delay)

  async def _read_frame(self) -> Optional[str]:
    """Read one STX..ETX frame and return its payload, or None if none arrives."""
    while True:
      byte = await self.io.read(1)
      if byte == b"":
        return None
      if byte == STX:
        break
    buf = bytearray()
    while True:
      byte = await self.io.read(1)
      if byte in (b"", ETX):
        break
      buf += byte
    return buf.decode("ascii", errors="replace")

  async def send_command(self, command: str) -> List[str]:
    """Send a raw command and collect every reply frame until the reply goes quiet."""
    await self._send(command)
    frames: List[str] = []
    with self.io.temporary_timeout(self.frame_gap):
      while True:
        frame = await self._read_frame()
        if frame is None:
          break
        frames.append(frame)
    logger.debug("[IntelliXcap96] %r -> %r", command, frames)
    return frames

  @staticmethod
  def _status_frame(frames: List[str]) -> Optional[str]:
    return next((f for f in frames if f.upper().startswith("STATUS")), None)

  def _require_accepted(
    self, command: str, frames: List[str], name: str, idempotent: bool = False
  ) -> bool:
    """Check a command's reply. Return True if it started a motion.

    Raises if the device did not ack and echo the command. A ``CommandIgnore``
    reply means the command was a no-op (the device is already in the requested
    state): for an ``idempotent`` command that is success and returns False (no
    motion to wait for); otherwise it is raised.
    """
    if ACK not in frames or f"{command}OK" not in frames:
      raise _fault("NoAck", f"{name}: {frames!r}")
    if any(COMMAND_IGNORE in f for f in frames):
      if idempotent:
        return False
      raise _fault("CommandIgnore", f"{name}: device already in that state or not ready")
    return True

  async def _wait_for_idle(
    self,
    timeout: float,
    name: str,
    fail_key: str,
    terminal_statuses: Tuple[str, ...] = ("StatusOK",),
  ) -> None:
    """Poll status until it reaches an expected idle state.

    ``fail_key`` names the firmware error message to raise if the status word
    reports an error while waiting. ``terminal_statuses`` accounts for
    operation state retained while the instrument is idle: hardware reports
    ``StatusRECAP`` after decapping and after tray motion with caps held.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    expected = {status.upper() for status in terminal_statuses}
    while loop.time() < deadline:
      frames = await self.send_command(STATUS)
      if any("ERROR" in f.upper() for f in frames):
        code = _error_code(frames)
        if code is not None:
          raise FluidXError.from_error_code(code, detail=f"{name}: {frames!r}")
        raise _fault(
          fail_key,
          f"{name}: {frames!r}. Check the instrument error log for the numeric error code.",
        )
      status = self._status_frame(frames)
      if status is not None and status.upper() in expected:
        return
      await asyncio.sleep(self.poll_interval)
    raise FluidXError(
      title=f"{name} timed out",
      message=f"did not reach {terminal_statuses!r} within {timeout}s",
    )

  # === Status ===

  async def request_status(self) -> str:
    """Poll the device and return its status word (e.g. ``StatusOK``)."""
    frames = await self.send_command(STATUS)
    status = self._status_frame(frames)
    if status is None:
      raise FluidXError(title="No status reply", message=repr(frames))
    return status

  # === Operations ===

  async def open_tray(self, timeout: float = 15.0) -> None:
    """Open the loading tray."""
    await self._ensure_ready()
    frames = await self.send_command(OPEN_TRAY)
    if self._require_accepted(OPEN_TRAY, frames, "Opening the tray", idempotent=True):
      await self._wait_for_idle(
        timeout,
        "Opening the tray",
        "OpenTrayWasNotSuccesful",
        ("StatusOK", "StatusRECAP", "StatusDECAP"),
      )
    logger.info("[IntelliXcap96 %s] tray open", self.io.port)

  async def close_tray(self, timeout: float = 15.0) -> None:
    """Close the loading tray."""
    await self._ensure_ready()
    frames = await self.send_command(CLOSE_TRAY)
    if self._require_accepted(CLOSE_TRAY, frames, "Closing the tray", idempotent=True):
      await self._wait_for_idle(
        timeout,
        "Closing the tray",
        "CloseTrayWasNotSuccesful",
        ("StatusOK", "StatusRECAP", "StatusDECAP"),
      )
    logger.info("[IntelliXcap96 %s] tray closed", self.io.port)

  async def _home_sequence(self, timeout: float, name: str) -> None:
    """Send the home command and wait for it to finish. Also clears a latched error."""
    frames = await self.send_command(HOME_ALL)
    self._require_accepted(HOME_ALL, frames, name)
    await self._wait_for_idle(timeout, name, "HomeNotSuccesful")

  async def _ensure_ready(self) -> str:
    """Return the current status, first clearing a latched error by homing.

    A ``StatusError`` is only cleared by homing (the reset command is ignored on
    this firmware). With ``auto_recover`` enabled, an operation that finds the
    device latched in error homes to recover and then proceeds; otherwise the
    latched error is raised.
    """
    status = await self.request_status()
    up = status.upper()
    if "MANUAL" in up:
      raise _fault("StatusManual", status)
    if "ERROR" not in up:
      return status
    if not self.auto_recover:
      raise _fault("StatusNotOK", status)
    logger.warning(
      "[IntelliXcap96 %s] latched in StatusError; homing to recover before continuing.",
      self.io.port,
    )
    await self._home_sequence(self.recover_timeout, "Homing (error recovery)")
    status = await self.request_status()
    if "ERROR" in status.upper():
      raise _fault("StatusNotOK", "error persisted after recovery home")
    return status

  async def reset_error(self, timeout: Optional[float] = None) -> None:
    """Recover from ``StatusError`` or ``StatusMANUAL`` by homing all axes.

    The firmware reset command does not clear these states. Hardware testing
    confirmed that the home-all command transitions ``StatusMANUAL`` through
    ``StatusBUSY`` to ``StatusOK``. Call this only after inspecting the rack and
    cap head and confirming that axis motion is safe.

    This method is a no-op when the instrument is not in an error or manual
    recovery state.

    Args:
      timeout: maximum recovery time in seconds. Defaults to
        ``recover_timeout`` configured on this instance.
    """
    status = await self.request_status()
    up = status.upper()
    if "ERROR" not in up and "MANUAL" not in up:
      return
    await self._home_sequence(
      self.recover_timeout if timeout is None else timeout,
      "Resetting error",
    )
    logger.info("[IntelliXcap96 %s] error reset by homing", self.io.port)

  async def home(self, timeout: float = 30.0) -> None:
    """Home all axes. Also clears a latched StatusError."""
    await self._home_sequence(timeout, "Homing")
    logger.info("[IntelliXcap96 %s] homed", self.io.port)

  async def decap(self, timeout: float = 60.0) -> None:
    """Unscrew and hold all 96 caps.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self._ensure_ready()).upper()
    if "RECAP" in status:
      raise _fault("NeedToRecap")
    frames = await self.send_command(DECAP_START)
    self._require_accepted(DECAP_START, frames, "Decapping")
    await self._wait_for_idle(
      timeout,
      "Decapping",
      "DecapWasNotSuccesful",
      ("StatusRECAP",),
    )
    logger.info("[IntelliXcap96 %s] decap complete", self.io.port)

  async def recap(self, timeout: float = 60.0) -> None:
    """Screw the held caps back on.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self._ensure_ready()).upper()
    if "RECAP" not in status:
      raise _fault("NeedToDecap")
    frames = await self.send_command(RECAP_START)
    self._require_accepted(RECAP_START, frames, "Recapping")
    await self._wait_for_idle(
      timeout,
      "Recapping",
      "RecapWasNotSuccesful",
      ("StatusOK", "StatusDECAP"),
    )
    logger.info("[IntelliXcap96 %s] recap complete", self.io.port)

  async def waste(self, timeout: float = 60.0) -> None:
    """Release the currently held caps into a separately positioned cap carrier.

    This is irreversible. Before calling, remove the sample-tube rack and
    position the correct cap carrier/collection vessel as specified in the
    Azenta IntelliXcap user manual. The instrument does not detect or verify the
    carrier. If the tube rack remains beneath the head, released caps can fall
    back onto the tube openings and look recapped even though they may not be
    threaded or torqued.

    User manual:
    https://web.azenta.com/hubfs/azenta-files/resources/manuals-guides/319430-IXC-User-Manual.pdf

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self._ensure_ready()).upper()
    if "RECAP" not in status:
      raise _fault("NeedToDecap", "waste requires caps held after decapping")
    frames = await self.send_command(WASTE)
    self._require_accepted(WASTE, frames, "Wasting caps")
    await self._wait_for_idle(
      timeout,
      "Wasting caps",
      "StoreWasNotSuccesful",
      ("StatusOK", "StatusDECAP"),
    )
    logger.info("[IntelliXcap96 %s] waste complete", self.io.port)

  async def standby(self, timeout: float = 15.0) -> None:
    """Put the decapper into standby (sleep) mode."""
    frames = await self.send_command(STANDBY)
    self._require_accepted(STANDBY, frames, "Entering standby")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
      if "SLEEP" in (await self.request_status()).upper():
        logger.info("[IntelliXcap96 %s] standby", self.io.port)
        return
      await asyncio.sleep(self.poll_interval)
    raise _fault("CannotGoInStandbyMode", "standby timed out")

  async def ready(self, timeout: float = 30.0) -> None:
    """Wake the decapper from standby if it is asleep."""
    if "SLEEP" not in (await self.request_status()).upper():
      return
    frames = await self.send_command(READY)
    self._require_accepted(READY, frames, "Waking from standby")
    await self._wait_for_idle(timeout, "Waking from standby", "StatusNotOK")
    logger.info("[IntelliXcap96 %s] ready", self.io.port)

  async def reset(self, settle_time: float = 5.0) -> None:
    """Reset the device and wait for it to settle.

    The reset command is a no-op on this firmware (it answers ``CommandIgnore``)
    and does not clear a latched error. Use :meth:`home` to recover from a
    ``StatusError``; operations do this automatically when ``auto_recover`` is
    enabled.

    Args:
      settle_time: time in seconds to wait after the reset command.
    """
    if "ERROR" in (await self.request_status()).upper():
      raise _fault("CannotReset")
    await self.send_command(RESET)
    await asyncio.sleep(settle_time)
    if "ERROR" in (await self.request_status()).upper():
      raise _fault("ResetNotSuccesful")
    logger.info("[IntelliXcap96 %s] reset complete", self.io.port)
