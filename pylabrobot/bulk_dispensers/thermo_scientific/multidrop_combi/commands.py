"""Operational commands mixin for the Multidrop Combi.

All volume parameters at the public interface are in microliters (float).
Internally, volumes are converted to the instrument's native 1/10 uL units.
"""
from __future__ import annotations

from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.enums import (
  EmptyMode,
  PrimeMode,
)

# Per-command timeout constants (seconds)
COMMAND_TIMEOUTS = {
  "SPL": 5.0,
  "SCT": 5.0,
  "SCV": 5.0,
  "SDH": 5.0,
  "SPS": 5.0,
  "SDO": 5.0,
  "SOF": 5.0,
  "SPV": 5.0,
  "PLA": 5.0,
  "EAK": 5.0,
  "POU": 10.0,
  "RST": 10.0,
  "DIS": 120.0,
  "PRI": 60.0,
  "EMP": 60.0,
  "SHA": 120.0,
  "BGN": 120.0,
}


def _ul_to_tenths(volume_ul: float) -> int:
  """Convert microliters to 1/10 uL integer."""
  return round(volume_ul * 10)


class MultidropCombiCommandsMixin:
  """Mixin providing operational commands for the Multidrop Combi."""

  async def dispense(self) -> None:
    """Dispense liquid to the plate (DIS command)."""
    await self._send_command("DIS", timeout=COMMAND_TIMEOUTS["DIS"])  # type: ignore[attr-defined]

  async def prime(self, volume: float, mode: PrimeMode = PrimeMode.STANDARD) -> None:
    """Prime dispenser hoses.

    Args:
      volume: Prime volume in microliters.
      mode: Prime mode (standard, continuous, stop continuous, calibration).
    """
    vol_tenths = _ul_to_tenths(volume)
    if vol_tenths < 10 or vol_tenths > 100000:
      raise ValueError(f"Prime volume must be 1-10000 uL, got {volume} uL")
    cmd = f"PRI {vol_tenths}"
    if mode != PrimeMode.STANDARD:
      cmd += f" {mode.value}"
    timeout = COMMAND_TIMEOUTS["PRI"] + volume / 100.0
    await self._send_command(cmd, timeout=timeout)  # type: ignore[attr-defined]

  async def empty(self, volume: float, mode: EmptyMode = EmptyMode.STANDARD) -> None:
    """Empty dispenser hoses.

    Args:
      volume: Empty volume in microliters.
      mode: Empty mode (standard or continuous).
    """
    vol_tenths = _ul_to_tenths(volume)
    if vol_tenths < 10 or vol_tenths > 100000:
      raise ValueError(f"Empty volume must be 1-10000 uL, got {volume} uL")
    cmd = f"EMP {vol_tenths}"
    if mode != EmptyMode.STANDARD:
      cmd += f" {mode.value}"
    timeout = COMMAND_TIMEOUTS["EMP"] + volume / 100.0
    await self._send_command(cmd, timeout=timeout)  # type: ignore[attr-defined]

  async def shake(self, time: float, distance: int, speed: int) -> None:
    """Shake the plate.

    Args:
      time: Duration in seconds.
      distance: Shake distance in mm (1-5).
      speed: Shake frequency in Hz (1-20).
    """
    if not 1 <= distance <= 5:
      raise ValueError(f"Shake distance must be 1-5 mm, got {distance}")
    if not 1 <= speed <= 20:
      raise ValueError(f"Shake speed must be 1-20 Hz, got {speed}")
    time_hundredths = round(time * 100)
    if time_hundredths < 1:
      raise ValueError(f"Shake time must be > 0, got {time}s")
    timeout = COMMAND_TIMEOUTS["SHA"] + time
    await self._send_command(  # type: ignore[attr-defined]
      f"SHA {time_hundredths} {distance} {speed}", timeout=timeout
    )

  async def move_plate_out(self) -> None:
    """Move plate carrier to loading position (POU command)."""
    await self._send_command(  # type: ignore[attr-defined]
      "POU", timeout=COMMAND_TIMEOUTS["POU"]
    )

  async def set_plate_type(self, plate_type: int) -> None:
    """Set plate type.

    Args:
      plate_type: Plate type index (0-29; 0-9 factory-defined, 10-29 user-defined).
    """
    if not 0 <= plate_type <= 29:
      raise ValueError(f"Plate type must be 0-29, got {plate_type}")
    await self._send_command(  # type: ignore[attr-defined]
      f"SPL {plate_type}", timeout=COMMAND_TIMEOUTS["SPL"]
    )

  async def set_cassette_type(self, cassette_type: int) -> None:
    """Set cassette type.

    Args:
      cassette_type: Cassette type (0=Standard, 1=Small, 2-3=User-defined).
    """
    if not 0 <= cassette_type <= 3:
      raise ValueError(f"Cassette type must be 0-3, got {cassette_type}")
    await self._send_command(  # type: ignore[attr-defined]
      f"SCT {cassette_type}", timeout=COMMAND_TIMEOUTS["SCT"]
    )

  async def set_column_volume(self, column: int, volume: float) -> None:
    """Set dispense volume for a column.

    Args:
      column: Column number (0 = all columns, 1-48 = specific column).
      volume: Volume in microliters.
    """
    if not 0 <= column <= 48:
      raise ValueError(f"Column must be 0-48, got {column}")
    vol_tenths = _ul_to_tenths(volume)
    await self._send_command(  # type: ignore[attr-defined]
      f"SCV {column} {vol_tenths}", timeout=COMMAND_TIMEOUTS["SCV"]
    )

  async def set_dispensing_height(self, height: int) -> None:
    """Set dispensing height.

    Args:
      height: Height in 1/100 mm (500-5500).
    """
    if not 500 <= height <= 5500:
      raise ValueError(f"Dispensing height must be 500-5500, got {height}")
    await self._send_command(  # type: ignore[attr-defined]
      f"SDH {height}", timeout=COMMAND_TIMEOUTS["SDH"]
    )

  async def set_pump_speed(self, speed: int) -> None:
    """Set pump speed as percentage of cassette range.

    Args:
      speed: Speed percentage (1-100).
    """
    if not 1 <= speed <= 100:
      raise ValueError(f"Pump speed must be 1-100, got {speed}")
    await self._send_command(  # type: ignore[attr-defined]
      f"SPS {speed}", timeout=COMMAND_TIMEOUTS["SPS"]
    )

  async def set_dispensing_order(self, order: int) -> None:
    """Set dispensing order.

    Args:
      order: 0 = row-wise, 1 = column-wise.
    """
    if order not in (0, 1):
      raise ValueError(f"Dispensing order must be 0 or 1, got {order}")
    await self._send_command(  # type: ignore[attr-defined]
      f"SDO {order}", timeout=COMMAND_TIMEOUTS["SDO"]
    )

  async def set_dispense_offset(self, x_offset: int, y_offset: int) -> None:
    """Set X/Y dispense offset.

    Args:
      x_offset: X offset in 1/100 mm (±300).
      y_offset: Y offset in 1/100 mm (±300).
    """
    if not -300 <= x_offset <= 300:
      raise ValueError(f"X offset must be ±300, got {x_offset}")
    if not -300 <= y_offset <= 300:
      raise ValueError(f"Y offset must be ±300, got {y_offset}")
    await self._send_command(  # type: ignore[attr-defined]
      f"SOF {x_offset} {y_offset}", timeout=COMMAND_TIMEOUTS["SOF"]
    )

  async def set_predispense_volume(self, volume: float) -> None:
    """Set predispense volume.

    Args:
      volume: Predispense volume in microliters.
    """
    vol_tenths = _ul_to_tenths(volume)
    if vol_tenths < 10 or vol_tenths > 100000:
      raise ValueError(f"Predispense volume must be 1-10000 uL, got {volume} uL")
    await self._send_command(  # type: ignore[attr-defined]
      f"SPV {vol_tenths}", timeout=COMMAND_TIMEOUTS["SPV"]
    )

  async def define_plate(
    self,
    column_positions: int,
    row_positions: int,
    rows: int,
    columns: int,
    height: int,
    max_volume: int,
    x_offset: int = 0,
    y_offset: int = 0,
  ) -> None:
    """Define a remote plate (PLA command).

    Args:
      column_positions: Number of column positions.
      row_positions: Number of row positions.
      rows: Number of rows.
      columns: Number of columns.
      height: Plate height in 1/100 mm.
      max_volume: Maximum well volume in 1/10 uL.
      x_offset: X offset in 1/100 mm.
      y_offset: Y offset in 1/100 mm.
    """
    await self._send_command(  # type: ignore[attr-defined]
      f"PLA {column_positions} {row_positions} {rows} {columns} "
      f"{height} {max_volume} {x_offset} {y_offset}",
      timeout=COMMAND_TIMEOUTS["PLA"],
    )

  async def start_protocol(self, plate_type: int | None = None,
                           protocol_name: str | None = None) -> None:
    """Start a protocol from instrument memory (BGN command).

    Args:
      plate_type: Optional plate type override.
      protocol_name: Optional protocol name.
    """
    cmd = "BGN"
    if plate_type is not None:
      cmd += f" {plate_type}"
    if protocol_name is not None:
      cmd += f" {protocol_name}"
    await self._send_command(cmd, timeout=COMMAND_TIMEOUTS["BGN"])  # type: ignore[attr-defined]
