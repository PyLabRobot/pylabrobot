import asyncio
import logging
from typing import Optional, Tuple

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

# Commands and replies are terminated with ETX (0x03), not CR/LF.
ETX = b"\x03"
# Single-byte acknowledgement the firmware returns before a long-running motion.
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

# Descriptions for the fault conditions the firmware reports.
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
  "NoAck": "Device did not acknowledge the command.",
  "CommandIgnore": "Command was ignored by the device.",
}


class FluidXError(Exception):
  """Exceptions raised by a FluidX IntelliXcap 96 decapper."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


def _fault(key: str) -> FluidXError:
  return FluidXError(title=ERROR_MESSAGES.get(key, key))


class FluidXIntelliXcap96:
  """FluidX IntelliXcap 96 automated screw-cap decapper.

  A benchtop instrument that decaps and recaps a 96-format rack of screw-cap
  tubes in a single stroke. It holds one nest; a plate mover loads the rack, the
  decapper unscrews all 96 caps (``decap``), holds them, and screws them back on
  (``recap``). Held caps can also be dropped to the waste bin (``waste``), and
  the loading tray opened and closed.

  Serial settings:
    9600 baud, 8 data bits, no parity, 1 stop bit, no handshake, ETX (0x03)
    terminator.

  Single-character commands (each is written followed by ETX; the reply is read
  back up to the next ETX):
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
  A status reply contains ``OK``/``StatusOK`` when idle, ``StatusSleep`` in
  standby, ``Recap``/``Decap`` for the pending inverse operation, and
  ``StatusError``/``Error`` on a fault. Operation replies report ``...DONE`` on
  success and ``...ERROR`` on failure. Motion commands are acknowledged with a
  single 0x06 byte.

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning is
  emitted at setup.
  """

  def __init__(
    self,
    port: str,
    timeout: float = 5.0,
    command_delay: float = 1.0,
    read_delay: float = 0.5,
    poll_interval: float = 1.0,
  ) -> None:
    """
    Args:
      port: serial port the decapper is connected to.
      timeout: serial read/write timeout in seconds.
      command_delay: pause after writing a command before reading its reply.
      read_delay: pause after reading a reply.
      poll_interval: pause between status polls while an operation runs.
    """
    self.command_delay = command_delay
    self.read_delay = read_delay
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
      "FluidXIntelliXcap96 has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await self.io.setup()
    # Confirm the decapper is reachable and reports a healthy status.
    await self._send(STATUS)
    await self._read_line()
    if "OK" not in await self._read_line():
      raise _fault("StatusNotOK")
    logger.info("[IntelliXcap96 %s] connected", self.io.port)

  async def stop(self) -> None:
    """Close the serial connection."""
    await self.io.stop()

  # === Command layer ===

  async def _send(self, command: str) -> None:
    """Discard any pending input, write a command with its terminator, then pace."""
    await self.io.reset_input_buffer()
    await self.io.write(command.encode("ascii") + ETX)
    logger.debug("[IntelliXcap96] send: %r", command)
    await asyncio.sleep(self.command_delay)

  async def _read_line(self) -> str:
    """Read one ETX-terminated reply and return it stripped."""
    buf = bytearray()
    while True:
      char = await self.io.read(1)
      if char in (b"", ETX):  # terminator or read timeout
        break
      buf += char
    reply = buf.decode("ascii", errors="replace").strip()
    logger.debug("[IntelliXcap96] recv: %r", reply)
    await asyncio.sleep(self.read_delay)
    return reply

  async def request_status(self) -> str:
    """Poll the device and return its raw status reply."""
    await self._send(STATUS)
    return await self._read_line()

  async def send_command(self, command: str) -> str:
    """Send a single raw command and return its reply. Escape hatch for bring-up."""
    await self._send(command)
    return await self._read_line()

  # === Operations ===

  async def decap(self, timeout: float = 60.0) -> None:
    """Unscrew and hold all 96 caps.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = await self.request_status()
    if "Recap" in status:
      raise _fault("NeedToRecap")
    self._raise_on_error(status, "StatusNotOK")

    await self._send(DECAP_START)
    await self._read_line()
    await self._poll_until(
      timeout=timeout,
      done=("RECAP", "DONE"),
      error=("DecapERROR", "StatusError", "Error"),
      fault="DecapWasNotSuccesful",
    )
    logger.info("[IntelliXcap96 %s] decap complete", self.io.port)

  async def recap(self, timeout: float = 50.0) -> None:
    """Screw the held caps back on.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = await self.request_status()
    if "Decap" in status:
      raise _fault("NeedToDecap")
    self._raise_on_error(status, "StatusNotOK")

    await self._send(RECAP_START)
    if ACK not in await self._read_line():
      raise _fault("NoAck")
    await self._poll_until(
      timeout=timeout,
      done=("RecapDONE", "StatusOK"),
      error=("RecapERROR", "StatusError", "Error"),
      fault="RecapWasNotSuccesful",
      ignore=("CommandIgnore",),
    )
    logger.info("[IntelliXcap96 %s] recap complete", self.io.port)

  async def waste(self, timeout: float = 60.0) -> None:
    """Drop the currently held caps into the waste bin.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = await self.request_status()
    self._raise_on_error(status, "StatusNotOK")

    await self._send(WASTE)
    if ACK not in await self._read_line():
      raise _fault("NoAck")
    await self._poll_until(
      timeout=timeout,
      done=("StoreDONE", "StatusOK"),
      error=("StoreERROR", "StatusError", "Error"),
      fault="StoreWasNotSuccesful",
    )
    logger.info("[IntelliXcap96 %s] waste complete", self.io.port)

  async def open_tray(self, timeout: float = 10.0) -> None:
    """Open the loading tray."""
    with self.io.temporary_timeout(timeout):
      await self._move_tray(
        OPEN_TRAY, ("OpenERROR", "StatusError", "Error"), "OpenTrayWasNotSuccesful"
      )
    logger.info("[IntelliXcap96 %s] tray open", self.io.port)

  async def close_tray(self, timeout: float = 10.0) -> None:
    """Close the loading tray."""
    with self.io.temporary_timeout(timeout):
      await self._move_tray(
        CLOSE_TRAY, ("CloseERROR", "StatusError", "Error"), "CloseTrayWasNotSuccesful"
      )
    logger.info("[IntelliXcap96 %s] tray closed", self.io.port)

  async def home(self, timeout: float = 20.0) -> None:
    """Home all axes."""
    with self.io.temporary_timeout(timeout):
      await self._send(STATUS)
      if ACK not in await self._read_line():
        raise _fault("NoAck")
      if "OK" not in await self._read_line():
        raise _fault("StatusNotOK")
      await self._send(HOME_ALL)
      await self._read_line()
      if "Error" in await self._read_line():
        raise _fault("HomeNotSuccesful")
    logger.info("[IntelliXcap96 %s] homed", self.io.port)

  async def reset(self, settle_time: float = 5.0) -> None:
    """Reset the device and wait for it to settle.

    Args:
      settle_time: time in seconds to wait after the reset command.
    """
    if "Error" in await self.request_status():
      raise _fault("CannotReset")
    await self._send(RESET)
    await asyncio.sleep(settle_time)
    await self._read_line()
    if "Error" in await self.request_status():
      raise _fault("ResetNotSuccesful")
    logger.info("[IntelliXcap96 %s] reset complete", self.io.port)

  async def standby(self) -> None:
    """Put the decapper into standby (sleep) mode."""
    status = await self.request_status()
    if "Recap" not in status and ("OK" not in status or "Error" in status):
      raise _fault("StatusNotOK")
    await self._send(STANDBY)
    if "Standby" not in await self._read_line():
      raise _fault("CannotGoInStandbyMode")
    logger.info("[IntelliXcap96 %s] standby", self.io.port)

  async def ready(self) -> None:
    """Wake the decapper from standby if it is asleep."""
    if "StatusSleep" not in await self.request_status():
      return
    await self._send(READY)
    if "Ready" not in await self._read_line():
      raise _fault("StatusNotOK")
    logger.info("[IntelliXcap96 %s] ready", self.io.port)

  # === Operation helpers ===

  async def _move_tray(self, command: str, error: Tuple[str, ...], fault: str) -> None:
    status = await self.request_status()
    self._raise_on_error(status, "StatusNotOK")
    await self._send(command)
    if ACK not in await self._read_line():
      raise _fault("NoAck")
    # The tray move reports two follow-up lines; either may carry the error.
    for _ in range(2):
      reply = await self._read_line()
      if any(token in reply for token in error):
        raise _fault(fault)
      if "CommandIgnore" in reply:
        raise _fault("CommandIgnore")

  async def _poll_until(
    self,
    timeout: float,
    done: Tuple[str, ...],
    error: Tuple[str, ...],
    fault: str,
    ignore: Tuple[str, ...] = (),
  ) -> None:
    """Poll status until a done/error token appears or the timeout elapses.

    Each poll reads three status lines and inspects the last, matching the way
    the firmware streams its progress.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
      await self._send(STATUS)
      reply = ""
      for _ in range(3):
        reply = await self._read_line()
      if any(token in reply for token in error):
        raise _fault(fault)
      if any(token in reply for token in ignore):
        raise _fault("CommandIgnore")
      if any(token in reply for token in done):
        return
      await asyncio.sleep(self.poll_interval)
    raise FluidXError(title="Operation timed out", message=f"no completion within {timeout}s")

  @staticmethod
  def _raise_on_error(status: str, fault: str) -> None:
    if "StatusError" in status or "Error" in status:
      raise _fault(fault)
