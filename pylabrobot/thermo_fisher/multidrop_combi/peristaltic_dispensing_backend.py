"""Peristaltic dispensing capability backend for the Multidrop Combi.

All volume parameters at the public interface are in microliters (float).
Internally, volumes are converted to the instrument's native 1/10 uL units.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from pylabrobot.capabilities.bulk_dispensers.peristaltic import PeristalticDispensingBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.thermo_fisher.multidrop_combi.driver import MultidropCombiDriver
from pylabrobot.thermo_fisher.multidrop_combi.enums import (
  DispensingOrder,
  EmptyMode,
  PrimeMode,
)
from pylabrobot.resources import Plate


def _ul_to_tenths(volume_ul: float) -> int:
  """Convert microliters to 1/10 uL integer."""
  return round(volume_ul * 10)


class MultidropCombiPeristalticDispensingBackend(PeristalticDispensingBackend):
  """Translates PeristalticDispensingBackend operations into Multidrop Combi commands."""

  def __init__(self, driver: MultidropCombiDriver):
    super().__init__()
    self._driver = driver

  async def _on_setup(self):
    """Clear any pending instrument errors after the driver connects."""
    try:
      await self._driver.acknowledge_error()
    except Exception:
      pass

  @dataclass
  class DispenseParams(BackendParams):
    """Parameters for the Multidrop Combi dispense command.

    Parameters are sent in the order recommended by the instrument workflow:
    plate_type → cassette_type → pump_speed → dispensing_height → volumes → dispense.

    Args:
      plate_type: Plate type index (0-29). If None, uses current setting.
      cassette_type: Cassette type (0=Standard, 1=Small, 2-3=User-defined). If None, uses current.
      pump_speed: Speed percentage (1-100). If None, uses current setting.
      dispensing_height: Height in 1/100 mm (500-5500). If None, uses current setting.
      dispensing_order: Well traversal order for 384+ well plates (no effect on 96-well).
        ROW_WISE fills across columns within each row (A1→A2→A3→...→B1→B2→...),
        COLUMN_WISE fills down rows within each column (A1→B1→...→H1→A2→B2→...).
        Does not affect per-column volumes. If None, uses current.
    """

    plate_type: Optional[int] = None
    cassette_type: Optional[int] = None
    pump_speed: Optional[int] = None
    dispensing_height: Optional[int] = None
    dispensing_order: Optional[DispensingOrder] = None

  async def dispense(
    self,
    plate: Plate,
    volumes: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid to the plate (DIS command).

    Args:
      plate: Target plate.
      volumes: Mapping of 1-indexed column number to volume in uL.
      backend_params: A DispenseParams instance with device-specific settings.
    """
    if not isinstance(backend_params, self.DispenseParams):
      backend_params = self.DispenseParams()

    # Follow the instrument workflow order:
    # set_plate_type → set_cassette_type → set_pump_speed → set_dispensing_height → set_volumes
    if backend_params.plate_type is not None:
      await self._set_plate_type(backend_params.plate_type)
    if backend_params.cassette_type is not None:
      await self._set_cassette_type(backend_params.cassette_type)
    if backend_params.pump_speed is not None:
      await self._set_pump_speed(backend_params.pump_speed)
    if backend_params.dispensing_height is not None:
      await self._set_dispensing_height(backend_params.dispensing_height)
    if backend_params.dispensing_order is not None:
      await self._set_dispensing_order(backend_params.dispensing_order)
    for col, vol in volumes.items():
      await self._set_column_volume(col, vol)

    await self._driver.send_command("DIS", timeout=120.0)

  @dataclass
  class PrimeParams(BackendParams):
    """Parameters for the Multidrop Combi prime command.

    Args:
      mode: Prime mode (standard, continuous, stop continuous, calibration).
    """

    mode: PrimeMode = PrimeMode.STANDARD

  async def prime(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Prime dispenser hoses (PRI command).

    The Multidrop Combi only supports volume-based priming, not duration.

    Args:
      plate: Target plate.
      volume: Prime volume in microliters.
      duration: Not supported — raises ValueError if provided.
      backend_params: A PrimeParams instance with device-specific settings.
    """
    if duration is not None:
      raise ValueError("Multidrop Combi does not support duration-based priming. Use volume.")
    if volume is None:
      raise ValueError("volume is required for Multidrop Combi priming.")

    if not isinstance(backend_params, self.PrimeParams):
      backend_params = self.PrimeParams()

    vol_tenths = _ul_to_tenths(volume)
    if vol_tenths < 10 or vol_tenths > 100000:
      raise ValueError(f"Prime volume must be 1-10000 uL, got {volume} uL")
    cmd = f"PRI {vol_tenths}"
    if backend_params.mode != PrimeMode.STANDARD:
      cmd += f" {backend_params.mode.value}"
    await self._driver.send_command(cmd, timeout=60.0 + volume / 100.0)

  @dataclass
  class PurgeParams(BackendParams):
    """Parameters for the Multidrop Combi purge (empty) command.

    Args:
      mode: Empty mode (standard or continuous).
    """

    mode: EmptyMode = EmptyMode.STANDARD

  async def purge(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Purge (empty) dispenser hoses (EMP command).

    The Multidrop Combi only supports volume-based purging, not duration.

    Args:
      plate: Target plate.
      volume: Purge volume in microliters.
      duration: Not supported — raises ValueError if provided.
      backend_params: A PurgeParams instance with device-specific settings.
    """
    if duration is not None:
      raise ValueError("Multidrop Combi does not support duration-based purging. Use volume.")
    if volume is None:
      raise ValueError("volume is required for Multidrop Combi purging.")

    if not isinstance(backend_params, self.PurgeParams):
      backend_params = self.PurgeParams()

    vol_tenths = _ul_to_tenths(volume)
    if vol_tenths < 10 or vol_tenths > 100000:
      raise ValueError(f"Purge volume must be 1-10000 uL, got {volume} uL")
    cmd = f"EMP {vol_tenths}"
    if backend_params.mode != EmptyMode.STANDARD:
      cmd += f" {backend_params.mode.value}"
    await self._driver.send_command(cmd, timeout=60.0 + volume / 100.0)

  # --- Multidrop-specific methods ---

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
    await self._driver.send_command(
      f"SHA {time_hundredths} {distance} {speed}", timeout=120.0 + time
    )

  async def move_plate_out(self) -> None:
    """Move plate carrier to loading position (POU command)."""
    await self._driver.send_command("POU", timeout=10.0)

  async def set_cassette_type(self, cassette_type: int) -> None:
    """Set cassette type.

    Args:
      cassette_type: Cassette type (0=Standard, 1=Small, 2-3=User-defined).
    """
    if not 0 <= cassette_type <= 3:
      raise ValueError(f"Cassette type must be 0-3, got {cassette_type}")
    await self._driver.send_command(f"SCT {cassette_type}", timeout=5.0)

  async def abort(self) -> None:
    """Abort the current operation."""
    await self._driver.send_abort_signal()

  async def set_dispense_offset(self, x_offset: int, y_offset: int) -> None:
    """Set X/Y dispense offset.

    Args:
      x_offset: X offset in 1/100 mm (+-300).
      y_offset: Y offset in 1/100 mm (+-300).
    """
    if not -300 <= x_offset <= 300:
      raise ValueError(f"X offset must be +-300, got {x_offset}")
    if not -300 <= y_offset <= 300:
      raise ValueError(f"Y offset must be +-300, got {y_offset}")
    await self._driver.send_command(f"SOF {x_offset} {y_offset}", timeout=5.0)

  async def set_predispense_volume(self, volume: float) -> None:
    """Set predispense volume.

    Args:
      volume: Predispense volume in microliters.
    """
    vol_tenths = _ul_to_tenths(volume)
    if vol_tenths < 10 or vol_tenths > 100000:
      raise ValueError(f"Predispense volume must be 1-10000 uL, got {volume} uL")
    await self._driver.send_command(f"SPV {vol_tenths}", timeout=5.0)

  async def define_plate(
    self,
    column_positions: int,
    row_positions: int,
    rows: int,
    columns: int,
    height: int,
    max_volume: float,
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
      max_volume: Maximum well volume in microliters.
      x_offset: X offset in 1/100 mm.
      y_offset: Y offset in 1/100 mm.
    """
    max_volume_tenths = _ul_to_tenths(max_volume)
    await self._driver.send_command(
      f"PLA {column_positions} {row_positions} {rows} {columns} "
      f"{height} {max_volume_tenths} {x_offset} {y_offset}",
      timeout=5.0,
    )

  async def start_protocol(
    self, plate_type: int | None = None, protocol_name: str | None = None
  ) -> None:
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
    await self._driver.send_command(cmd, timeout=120.0)

  # --- Private configuration methods (called by dispense) ---

  async def _set_plate_type(self, plate_type: int) -> None:
    if not 0 <= plate_type <= 29:
      raise ValueError(f"Plate type must be 0-29, got {plate_type}")
    await self._driver.send_command(f"SPL {plate_type}", timeout=5.0)

  async def _set_cassette_type(self, cassette_type: int) -> None:
    if not 0 <= cassette_type <= 3:
      raise ValueError(f"Cassette type must be 0-3, got {cassette_type}")
    await self._driver.send_command(f"SCT {cassette_type}", timeout=5.0)

  async def _set_column_volume(self, column: int, volume: float) -> None:
    if not 1 <= column <= 48:
      raise ValueError(f"Column must be 1-48, got {column}")
    vol_tenths = _ul_to_tenths(volume)
    await self._driver.send_command(f"SCV {column} {vol_tenths}", timeout=5.0)

  async def _set_dispensing_height(self, height: int) -> None:
    if not 500 <= height <= 5500:
      raise ValueError(f"Dispensing height must be 500-5500, got {height}")
    await self._driver.send_command(f"SDH {height}", timeout=5.0)

  async def _set_pump_speed(self, speed: int) -> None:
    if not 1 <= speed <= 100:
      raise ValueError(f"Pump speed must be 1-100, got {speed}")
    await self._driver.send_command(f"SPS {speed}", timeout=5.0)

  async def _set_dispensing_order(self, order: DispensingOrder) -> None:
    await self._driver.send_command(f"SDO {int(order)}", timeout=5.0)
