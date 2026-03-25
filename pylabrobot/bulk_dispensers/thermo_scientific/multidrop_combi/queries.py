"""Query operations mixin for the Multidrop Combi."""
from __future__ import annotations


class MultidropCombiQueriesMixin:
  """Mixin providing query operations for the Multidrop Combi."""

  _instrument_name: str
  _firmware_version: str
  _serial_number: str

  def get_version(self) -> dict:
    """Return cached instrument identification info.

    Returns:
      Dict with keys: instrument_name, firmware_version, serial_number.
    """
    return {
      "instrument_name": self._instrument_name,
      "firmware_version": self._firmware_version,
      "serial_number": self._serial_number,
    }

  async def report_parameters(self) -> list[str]:
    """Report instrument parameters (REP command).

    Returns:
      List of parameter lines from the instrument.
    """
    return await self._send_command("REP", timeout=10.0)  # type: ignore[attr-defined]

  async def read_error_log(self) -> list[str]:
    """Read the instrument error log (LOG command).

    Returns:
      List of error log lines.
    """
    return await self._send_command("LOG", timeout=10.0)  # type: ignore[attr-defined]

  async def read_cassette_info(self) -> list[str]:
    """Read RFID cassette info (RIR command).

    Returns:
      List of cassette info lines.
    """
    return await self._send_command("RIR", timeout=5.0)  # type: ignore[attr-defined]
