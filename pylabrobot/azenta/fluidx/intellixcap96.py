import asyncio
import logging
from typing import List, Optional

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

# Every reply is framed between STX (0x02) and ETX (0x03). A command is answered
# with an ACK frame (a lone 0x06), a command-echo frame ("<cmd>OK"), and then a
# result frame (a "Status..." word, or "CommandIgnore" when the command is a
# no-op or refused). Motions run asynchronously: the status word is "StatusBUSY"
# while moving and returns to "StatusOK" when done.
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
  "ResetNotSuccesful": (
    "Reset did not complete. The device is in an error state. Reset the device and try again."
  ),
  "CannotGoInStandbyMode": "Cannot go to standby mode. Check the errors on the device.",
  "CommandIgnore": "Command was ignored by the device.",
  "NoAck": "Device did not acknowledge the command.",
}


class FluidXError(Exception):
  """Exceptions raised by a FluidX IntelliXcap 96 decapper."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


def _fault(key: str, detail: Optional[str] = None) -> FluidXError:
  """Build a FluidXError carrying the firmware's own description for ``key``."""
  return FluidXError(title=ERROR_MESSAGES.get(key, key), message=detail)


class FluidXIntelliXcap96:
  """FluidX IntelliXcap 96 automated screw-cap decapper.

  A benchtop instrument that decaps and recaps a 96-format rack of screw-cap
  tubes in a single stroke. It holds one nest; a plate mover loads the rack, the
  decapper unscrews all 96 caps (``decap``), holds them, and screws them back on
  (``recap``). Held caps can also be dropped to the waste bin (``waste``), and
  the loading tray opened and closed.

  Serial settings:
    9600 baud, 8 data bits, no parity, 1 stop bit, no handshake. Replies are
    framed between STX (0x02) and ETX (0x03).

  Single-character commands, each written followed by ETX:
    a   request status
    h   start decapping
    i   start recapping
    b   drop held caps to waste
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

  Verified against hardware: connection, status, tray open/close, home, and
  standby/ready. Decap, recap, and waste engage the cap head and are NOT yet
  hardware-verified; a warning is emitted at setup.
  """

  def __init__(
    self,
    port: str,
    timeout: float = 5.0,
    command_delay: float = 0.3,
    frame_gap: float = 0.5,
    poll_interval: float = 1.0,
  ) -> None:
    """
    Args:
      port: serial port the decapper is connected to.
      timeout: serial read/write timeout in seconds.
      command_delay: pause after writing a command before reading its reply.
      frame_gap: how long to wait for another reply frame before concluding the
        reply is complete.
      poll_interval: pause between status polls while a motion runs.
    """
    self.command_delay = command_delay
    self.frame_gap = frame_gap
    self.poll_interval = poll_interval
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
    logger.warning(
      "FluidXIntelliXcap96: connection, status, tray, home, and standby/ready are "
      "hardware-verified, but decap, recap, and waste are NOT yet verified in PyLabRobot. "
      "Please make a PR to remove this message once you have verified them on your hardware."
    )
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

  async def _command(self, command: str) -> List[str]:
    """Send a command and collect every reply frame until the reply goes quiet."""
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

  def _require_accepted(self, command: str, frames: List[str], name: str) -> None:
    """Raise unless the device acked and echoed the command without ignoring it."""
    if ACK not in frames or f"{command}OK" not in frames:
      raise _fault("NoAck", f"{name}: {frames!r}")
    if any(COMMAND_IGNORE in f for f in frames):
      raise _fault("CommandIgnore", f"{name}: device already in that state or not ready")

  async def _wait_for_idle(self, timeout: float, name: str, fail_key: str) -> None:
    """Poll status until it returns to StatusOK, raising on an error or timeout.

    ``fail_key`` names the firmware error message to raise if the status word
    reports an error while waiting.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
      frames = await self._command(STATUS)
      if any("ERROR" in f.upper() for f in frames):
        raise _fault(fail_key, f"{name}: {frames!r}")
      status = self._status_frame(frames)
      if status is not None and "OK" in status.upper():
        return
      await asyncio.sleep(self.poll_interval)
    raise FluidXError(title=f"{name} timed out", message=f"not idle within {timeout}s")

  # === Status ===

  async def request_status(self) -> str:
    """Poll the device and return its status word (e.g. ``StatusOK``)."""
    frames = await self._command(STATUS)
    status = self._status_frame(frames)
    if status is None:
      raise FluidXError(title="No status reply", message=repr(frames))
    return status

  async def send_command(self, command: str) -> List[str]:
    """Send a raw single command and return its reply frames. Escape hatch."""
    return await self._command(command)

  # === Operations ===

  async def open_tray(self, timeout: float = 15.0) -> None:
    """Open the loading tray."""
    frames = await self._command(OPEN_TRAY)
    self._require_accepted(OPEN_TRAY, frames, "Opening the tray")
    await self._wait_for_idle(timeout, "Opening the tray", "OpenTrayWasNotSuccesful")
    logger.info("[IntelliXcap96 %s] tray open", self.io.port)

  async def close_tray(self, timeout: float = 15.0) -> None:
    """Close the loading tray."""
    frames = await self._command(CLOSE_TRAY)
    self._require_accepted(CLOSE_TRAY, frames, "Closing the tray")
    await self._wait_for_idle(timeout, "Closing the tray", "CloseTrayWasNotSuccesful")
    logger.info("[IntelliXcap96 %s] tray closed", self.io.port)

  async def home(self, timeout: float = 30.0) -> None:
    """Home all axes."""
    frames = await self._command(HOME_ALL)
    self._require_accepted(HOME_ALL, frames, "Homing")
    await self._wait_for_idle(timeout, "Homing", "HomeNotSuccesful")
    logger.info("[IntelliXcap96 %s] homed", self.io.port)

  async def decap(self, timeout: float = 60.0) -> None:
    """Unscrew and hold all 96 caps.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self.request_status()).upper()
    if "RECAP" in status:
      raise _fault("NeedToRecap")
    if "ERROR" in status:
      raise _fault("StatusNotOK", status)
    frames = await self._command(DECAP_START)
    self._require_accepted(DECAP_START, frames, "Decapping")
    await self._wait_for_idle(timeout, "Decapping", "DecapWasNotSuccesful")
    logger.info("[IntelliXcap96 %s] decap complete", self.io.port)

  async def recap(self, timeout: float = 60.0) -> None:
    """Screw the held caps back on.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self.request_status()).upper()
    if "DECAP" in status:
      raise _fault("NeedToDecap")
    if "ERROR" in status:
      raise _fault("StatusNotOK", status)
    frames = await self._command(RECAP_START)
    self._require_accepted(RECAP_START, frames, "Recapping")
    await self._wait_for_idle(timeout, "Recapping", "RecapWasNotSuccesful")
    logger.info("[IntelliXcap96 %s] recap complete", self.io.port)

  async def waste(self, timeout: float = 60.0) -> None:
    """Drop the currently held caps into the waste bin.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self.request_status()).upper()
    if "ERROR" in status:
      raise _fault("StatusNotOK", status)
    frames = await self._command(WASTE)
    self._require_accepted(WASTE, frames, "Wasting caps")
    await self._wait_for_idle(timeout, "Wasting caps", "StoreWasNotSuccesful")
    logger.info("[IntelliXcap96 %s] waste complete", self.io.port)

  async def standby(self, timeout: float = 15.0) -> None:
    """Put the decapper into standby (sleep) mode."""
    frames = await self._command(STANDBY)
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
    frames = await self._command(READY)
    self._require_accepted(READY, frames, "Waking from standby")
    await self._wait_for_idle(timeout, "Waking from standby", "StatusNotOK")
    logger.info("[IntelliXcap96 %s] ready", self.io.port)

  async def reset(self, settle_time: float = 5.0) -> None:
    """Reset the device and wait for it to settle.

    A reset is a no-op when the device is already idle (the firmware answers
    ``CommandIgnore``); it clears a recoverable error state.

    Args:
      settle_time: time in seconds to wait after the reset command.
    """
    await self._command(RESET)
    await asyncio.sleep(settle_time)
    status = (await self.request_status()).upper()
    if "ERROR" in status:
      raise _fault("ResetNotSuccesful", status)
    logger.info("[IntelliXcap96 %s] reset complete", self.io.port)
