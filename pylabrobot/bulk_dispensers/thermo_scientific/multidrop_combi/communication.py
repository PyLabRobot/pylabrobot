"""Low-level serial communication mixin for the Multidrop Combi.

Ported from the SiLA implementation's serial_transport.py to use
pylabrobot's async Serial wrapper.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.errors import (
  MultidropCombiCommunicationError,
  MultidropCombiInstrumentError,
)
from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

STATUS_OK = 0

ERROR_DESCRIPTIONS = {
  1: "Internal firmware error",
  2: "Unrecognized command",
  3: "Invalid command arguments",
  4: "Pump position error",
  5: "Plate X position error",
  6: "Plate Y position error",
  7: "Z position error",
  9: "Attempt to reset serial number",
  10: "Nonvolatile parameters lost",
  11: "No more memory for user data",
  12: "Pump or X motor was running",
  13: "X and Z positions conflict",
  14: "Cannot dispense: pump not primed",
  15: "Missing prime vessel",
  16: "Rotor shield not in place",
  17: "Dispense volume for all columns is 0",
  18: "Invalid plate type (bad plate index)",
  19: "Plate has not been defined",
  20: "Invalid rows in plate definition",
  21: "Invalid columns in plate definition",
  22: "Plate height is invalid",
  23: "Plate well volume invalid (too small or too big)",
  24: "Invalid cassette type (bad cassette index)",
  25: "Cassette not defined",
  26: "Invalid volume increment for cassette",
  27: "Invalid maximum volume for cassette",
  28: "Invalid minimum volume for cassette",
  29: "Invalid min/max pump speed for cassette",
  30: "Invalid pump rotor offset in cassette definition",
  32: "Dispensing volume not within cassette limits",
  33: "Invalid selector channel",
  34: "Invalid dispensing speed",
  35: "Dispensing height too low for plate",
  36: "Predispense volume not within cassette limits",
  37: "Invalid dispensing order",
  38: "Invalid X or Y dispensing offset",
  39: "RFID option not present",
  40: "RFID tag not present",
  41: "RFID tag data checksum incorrect",
  43: "Wrong cassette type",
  44: "Protocol/plate in use, cannot modify or delete",
  45: "Protocol/plate/cassette is read-only",
}


class MultidropCombiCommunicationMixin:
  """Mixin providing low-level serial communication for the Multidrop Combi."""

  io: Optional[Serial]
  _command_lock: Optional[asyncio.Lock]

  async def _send_command(self, cmd: str, timeout: float | None = None) -> list[str]:
    """Send a command and return the data lines from the response.

    Args:
      cmd: Command string (e.g. "DIS", "SPL 1", "SCV 0 500").
      timeout: Per-command read timeout in seconds. If None, uses default.

    Returns:
      List of data lines (between echo and END terminator).

    Raises:
      MultidropCombiCommunicationError: If not connected or communication fails.
      MultidropCombiInstrumentError: If instrument returns non-zero status code.
    """
    if self.io is None or self._command_lock is None:
      raise MultidropCombiCommunicationError("Not connected to instrument", operation=cmd)

    assert self.io._ser is not None, "Serial port not open. Did you call setup()?"

    cmd_code = cmd.split()[0]

    async with self._command_lock:
      original_timeout = self.io._ser.timeout
      if timeout is not None:
        self.io._ser.timeout = timeout
      try:
        logger.debug("TX: %r", cmd)
        await self.io.write(f"{cmd}\r".encode("ascii"))

        lines: list[str] = []
        while True:
          raw = await self.io.readline()
          if not raw:
            raise MultidropCombiCommunicationError(
              f"Timeout reading response for {cmd_code}", operation=cmd
            )
          line = raw.decode("ascii", errors="replace").strip()
          logger.debug("RX: %r", line)
          if not line:
            continue
          lines.append(line)

          if line.startswith(cmd_code) and " END " in line:
            break

        # Parse status from END terminator
        end_line = lines[-1]
        parts = end_line.split()
        status_code = int(parts[-1]) if parts[-1].isdigit() else -1

        if status_code != STATUS_OK:
          desc = ERROR_DESCRIPTIONS.get(status_code, "Unknown error")
          logger.error("Command %s failed (status %d). RX lines: %s", cmd_code, status_code, lines)
          raise MultidropCombiInstrumentError(status_code, desc)

        # Return data lines: skip echo (first) and END line (last)
        # The instrument may echo just the command code or the full command
        data_lines = []
        for line in lines[:-1]:
          line_upper = line.strip().upper()
          if line_upper == cmd.strip().upper() or line_upper == cmd_code.upper():
            continue
          data_lines.append(line)

        return data_lines

      except (MultidropCombiCommunicationError, MultidropCombiInstrumentError):
        raise
      except Exception as e:
        raise MultidropCombiCommunicationError(
          f"Communication error during {cmd_code}: {e}",
          operation=cmd,
          original_error=e,
        ) from e
      finally:
        if timeout is not None:
          self.io._ser.timeout = original_timeout

  async def _drain_stale_data(self) -> None:
    """Drain any stale data from the serial buffer."""
    if self.io is None:
      return

    assert self.io._ser is not None

    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    original_timeout = self.io._ser.timeout
    self.io._ser.timeout = 0.3
    drained = 0
    try:
      while True:
        stale = await self.io.readline()
        if not stale:
          break
        drained += 1
        logger.debug("Drained stale data: %r", stale)
    finally:
      self.io._ser.timeout = original_timeout
    if drained:
      logger.info("Drained %d stale lines from serial buffer", drained)

  async def _enter_remote_mode(self) -> dict:
    """Send VER to enter remote control mode and get instrument info.

    Returns:
      Dict with keys: instrument_name, firmware_version, serial_number.
    """
    try:
      lines = await self._send_command("VER", timeout=5.0)
    except Exception as first_err:
      logger.warning("VER failed (%s), sending EAK and retrying...", first_err)
      try:
        await self._send_command("EAK", timeout=5.0)
      except Exception:
        pass
      try:
        lines = await self._send_command("VER", timeout=5.0)
      except Exception as e:
        raise MultidropCombiCommunicationError(
          f"VER command failed: {e}", operation="VER", original_error=e
        ) from e

    info = {
      "instrument_name": "Unknown",
      "firmware_version": "Unknown",
      "serial_number": "Unknown",
    }
    if lines:
      raw = lines[0]
      if raw.upper().startswith("VER "):
        raw = raw[4:]
      parts = raw.split()
      if len(parts) > 0:
        info["instrument_name"] = parts[0]
      if len(parts) > 1:
        info["firmware_version"] = parts[1]
      if len(parts) > 2:
        info["serial_number"] = parts[2]

    return info

  async def _exit_remote_mode(self) -> None:
    """Send QIT to exit remote control mode."""
    try:
      await self._send_command("QIT", timeout=5.0)
    except Exception:
      pass
