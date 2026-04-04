"""EL406 peristaltic pump step methods.

Provides peristaltic_prime, peristaltic_dispense, and peristaltic_purge operations
plus their corresponding command builders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Literal, Optional

from pylabrobot.capabilities.bulk_dispensers.peristaltic.backend import PeristalticDispensingBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.io.binary import Writer
from pylabrobot.resources import Plate

from .driver import EL406Driver
from .helpers import (
  plate_default_z,
  plate_max_columns,
  plate_max_row_groups,
  plate_to_wire_byte,
  plate_well_count,
)
from .protocol import build_framed_message, columns_to_column_mask, encode_column_mask

logger = logging.getLogger(__name__)

PeristalticFlowRate = Literal["Low", "Medium", "High"]
Cassette = Literal["Any", "1uL", "5uL", "10uL"]

PERISTALTIC_FLOW_RATE_MAP: dict[str, int] = {"Low": 0, "Medium": 1, "High": 2}


def cassette_to_byte(cassette: Cassette) -> int:
  mapping = {"ANY": 0, "1UL": 1, "5UL": 2, "10UL": 3}
  key = cassette.upper()
  if key not in mapping:
    raise ValueError(f"Invalid cassette '{cassette}'. Must be one of: Any, 1uL, 5uL, 10uL")
  return mapping[key]


def encode_quadrant_mask_inverted(
  rows: list[int] | None,
  num_row_groups: int = 4,
) -> int:
  """Encode row/quadrant selection as inverted bitmask.

  The protocol uses INVERTED encoding for the quadrant/row mask byte:
  0 = selected, 1 = deselected. This is the opposite of the well mask.

  Args:
    rows: List of row numbers (1 to num_row_groups) to select, or None for all.
      If None, returns 0x00 (all selected in inverted encoding).
    num_row_groups: Number of valid row groups for this plate type (1, 2, or 4).

  Returns:
    Single byte with inverted bit encoding (only lower num_row_groups bits used).

  Raises:
    ValueError: If any row number is out of range.
  """
  if rows is None:
    return 0x00

  max_mask = (1 << num_row_groups) - 1
  mask = max_mask
  for row in rows:
    if row < 1 or row > num_row_groups:
      raise ValueError(f"Row number {row} out of range. Must be 1-{num_row_groups}.")
    mask &= ~(1 << (row - 1))

  return mask & 0xFF


def validate_peristaltic_flow_rate(flow_rate: PeristalticFlowRate) -> None:
  if flow_rate not in PERISTALTIC_FLOW_RATE_MAP:
    raise ValueError(
      f"flow_rate must be one of {sorted(PERISTALTIC_FLOW_RATE_MAP)}, got {flow_rate!r}"
    )


class EL406PeristalticDispensingBackend(PeristalticDispensingBackend):
  """Peristaltic dispensing backend for the BioTek EL406."""

  @dataclass
  class DispenseParams(BackendParams):
    """Parameters for peristaltic dispense.

    Attributes:
      flow_rate: Flow rate ("Low", "Medium", or "High").
      offset_x: X offset in mm (-12.5 to 12.5).
      offset_y: Y offset in mm (-4.0 to 4.0).
      offset_z: Z offset in mm (0.1-150.0). Default depends on plate type:
        33.6 for 96/384-well, 25.4 for 1536-well.
      pre_dispense_volume: Pre-dispense volume in uL (0 to disable).
      num_pre_dispenses: Number of pre-dispenses (default 2).
      cassette: Cassette type ("Any", "1uL", "5uL", "10uL").
      columns: List of 1-indexed column numbers to dispense to, or None for all.
        For 96-well: 1-12, for 384-well: 1-24, for 1536-well: 1-48.
      rows: List of 1-indexed row group numbers, or None for all.
        For 96-well: only 1 (no selection). For 384-well: 1-2. For 1536-well: 1-4.
    """

    flow_rate: PeristalticFlowRate = "High"
    offset_x: float = 0.0
    offset_y: float = 0.0
    offset_z: Optional[float] = None
    pre_dispense_volume: float = 10.0
    num_pre_dispenses: int = 2
    cassette: Cassette = "Any"
    columns: list[int] | None = None
    rows: list[int] | None = None

  def __init__(self, driver: EL406Driver) -> None:
    self._driver = driver

  async def dispense(
    self,
    plate: Plate,
    volumes: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, self.DispenseParams):
      backend_params = self.DispenseParams()

    # Group consecutive columns with the same volume, in ascending order
    groups: list[tuple[float, list[int]]] = []
    for col in sorted(volumes.keys()):
      vol = volumes[col]
      if groups and groups[-1][0] == vol:
        groups[-1][1].append(col)
      else:
        groups.append((vol, [col]))

    for vol, cols in groups:
      params = self.DispenseParams(
        flow_rate=backend_params.flow_rate,
        offset_x=backend_params.offset_x,
        offset_y=backend_params.offset_y,
        offset_z=backend_params.offset_z,
        pre_dispense_volume=backend_params.pre_dispense_volume,
        num_pre_dispenses=backend_params.num_pre_dispenses,
        cassette=backend_params.cassette,
        columns=cols,
        rows=backend_params.rows,
      )
      await self._peristaltic_dispense(plate, volume=vol, params=params)

  @dataclass
  class PrimeParams(BackendParams):
    """Parameters for peristaltic prime and purge.

    Attributes:
      flow_rate: Flow rate ("Low", "Medium", or "High").
      cassette: Cassette type ("Any", "1uL", "5uL", "10uL").
    """

    flow_rate: PeristalticFlowRate = "High"
    cassette: Cassette = "Any"

  async def prime(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, self.PrimeParams):
      backend_params = self.PrimeParams()
    await self._peristaltic_prime(plate, volume=volume, duration=duration, params=backend_params)

  async def purge(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, self.PrimeParams):
      backend_params = self.PrimeParams()
    await self._peristaltic_purge(plate, volume=volume, duration=duration, params=backend_params)

  def _validate_peristaltic_well_selection(
    self,
    plate: Plate,
    columns: list[int] | None,
    rows: list[int] | None,
  ) -> list[int] | None:
    """Validate column/row selection and return column mask."""
    max_cols = plate_max_columns(plate)
    if columns is not None:
      for col in columns:
        if col < 1 or col > max_cols:
          raise ValueError(f"Column {col} out of range for plate type (1-{max_cols}).")

    max_rows = plate_max_row_groups(plate)
    if rows is not None:
      for row in rows:
        if row < 1 or row > max_rows:
          raise ValueError(f"Row {row} out of range for plate type (1-{max_rows}).")

    return columns_to_column_mask(columns, plate_wells=plate_well_count(plate))

  def _validate_peristaltic_dispense_params(
    self,
    plate: Plate,
    volume: float,
    flow_rate: PeristalticFlowRate,
    offset_x: int,
    offset_y: int,
    offset_z: int | None,
    pre_dispense_volume: float,
    columns: list[int] | None,
    rows: list[int] | None,
  ) -> tuple[int, int, list[int] | None]:
    """Validate peristaltic dispense parameters and resolve defaults.

    Returns:
      (offset_z, flow_rate_enum, column_mask)
    """
    if not 1 <= volume <= 3000:
      raise ValueError(f"Peri-pump dispense volume must be 1-3000 uL, got {volume}")
    validate_peristaltic_flow_rate(flow_rate)
    if not -125 <= offset_x <= 125:
      raise ValueError(f"Peri-pump dispense X-axis offset must be -125..125, got {offset_x}")
    if not -40 <= offset_y <= 40:
      raise ValueError(f"Peri-pump dispense Y-axis offset must be -40..40, got {offset_y}")

    if offset_z is None:
      offset_z = plate_default_z(plate)
    if not 1 <= offset_z <= 1500:
      raise ValueError(f"Peri-pump dispense Z-axis offset must be 1..1500, got {offset_z}")

    if pre_dispense_volume < 0:
      raise ValueError(f"pre_dispense_volume must be non-negative, got {pre_dispense_volume}")

    column_mask = self._validate_peristaltic_well_selection(plate, columns, rows)

    return (offset_z, PERISTALTIC_FLOW_RATE_MAP[flow_rate], column_mask)

  async def _peristaltic_prime(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    params: Optional[PrimeParams] = None,
  ) -> None:
    """Prime the peristaltic fluid lines.

    Specify either ``volume`` (uL/tube) or ``duration`` (seconds), not both.
    If neither is given, defaults to volume mode with 1000 uL.

    Note: Peristaltic prime has no buffer selection.
    Use ``manifold_prime()`` for buffer-specific priming.

    Args:
      plate: PLR Plate resource.
      volume: Volume to prime in microliters.
      duration: Fixed duration in seconds (alternative to volume).
      flow_rate: Flow rate ("Low", "Medium", or "High").
      cassette: Cassette type ("Any", "1uL", "5uL", "10uL").

    Raises:
      ValueError: If both volume and duration are specified, or if parameters are invalid.
    """
    if volume is not None and duration is not None:
      raise ValueError("Specify either volume or duration, not both.")

    if duration is not None:
      if not 1 <= duration <= 300:
        raise ValueError("duration must be 1-300 seconds")
      prime_volume = 0.0
      prime_duration = duration
    else:
      if volume is None:
        volume = 1000.0
      if not 1 <= volume <= 3000:
        raise ValueError("volume must be 1-3000 uL (GUI limit)")
      prime_volume = volume
      prime_duration = 0

    if params is None:
      params = self.PrimeParams()

    validate_peristaltic_flow_rate(params.flow_rate)

    logger.info(
      "Peristaltic prime: %.1f uL, flow rate %s, cassette %s",
      prime_volume,
      params.flow_rate,
      params.cassette,
    )

    data = self._build_peristaltic_prime_command(
      plate=plate,
      volume=prime_volume,
      duration=prime_duration,
      flow_rate=PERISTALTIC_FLOW_RATE_MAP[params.flow_rate],
      reverse=True,
      cassette=params.cassette,
      pump=1,
    )
    framed_command = build_framed_message(command=0x90, data=data)
    # Timeout: duration (if specified) + buffer for volume-based priming
    prime_timeout = self._driver.timeout + prime_duration + 30
    async with self._driver.batch():
      await self._driver._send_step_command(framed_command, timeout=prime_timeout)

  async def _peristaltic_dispense(
    self,
    plate: Plate,
    volume: float,
    params: Optional[DispenseParams] = None,
  ) -> None:
    """Dispense liquid using the peristaltic pump.

    Args:
      plate: PLR Plate resource.
      volume: Dispense volume in microliters (1-3000).
      params: Dispense parameters (flow rate, offsets, cassette, column/row selection).
    """
    if params is None:
      params = self.DispenseParams()

    # Convert mm → 0.1mm steps for wire protocol
    offset_x_steps = round(params.offset_x * 10)
    offset_y_steps = round(params.offset_y * 10)
    offset_z_steps = round(params.offset_z * 10) if params.offset_z is not None else None

    offset_z_steps, flow_rate_enum, column_mask = self._validate_peristaltic_dispense_params(
      plate=plate,
      volume=volume,
      flow_rate=params.flow_rate,
      offset_x=offset_x_steps,
      offset_y=offset_y_steps,
      offset_z=offset_z_steps,
      pre_dispense_volume=params.pre_dispense_volume,
      columns=params.columns,
      rows=params.rows,
    )

    logger.info(
      "Peristaltic dispense: %.1f uL, flow rate %s, cassette %s",
      volume,
      params.flow_rate,
      params.cassette,
    )

    data = self._build_peristaltic_dispense_command(
      plate=plate,
      volume=volume,
      flow_rate=flow_rate_enum,
      cassette=params.cassette,
      offset_x=offset_x_steps,
      offset_y=offset_y_steps,
      offset_z=offset_z_steps,
      pre_dispense_volume=params.pre_dispense_volume,
      num_pre_dispenses=params.num_pre_dispenses,
      column_mask=column_mask,
      rows=params.rows,
      pump=1,
    )
    framed_command = build_framed_message(command=0x8F, data=data)
    async with self._driver.batch():
      await self._driver._send_step_command(framed_command)

  async def _peristaltic_purge(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    params: Optional[PrimeParams] = None,
  ) -> None:
    """Purge the fluid lines using the peristaltic pump.

    Specify either ``volume`` (uL/tube) or ``duration`` (seconds), not both.

    PERISTALTIC_PURGE uses the same data format as PERISTALTIC_PRIME
    (both send identical data bytes).

    Args:
      plate: PLR Plate resource.
      volume: Purge volume in microliters.
      duration: Fixed duration in seconds (alternative to volume).
      params: Prime/purge parameters (flow rate, cassette).
    """
    if params is None:
      params = self.PrimeParams()

    if volume is not None and duration is not None:
      raise ValueError("Specify either volume or duration, not both.")
    if volume is None and duration is None:
      raise ValueError("Either volume or duration must be specified.")

    if duration is not None:
      if not 1 <= duration <= 300:
        raise ValueError("duration must be 1-300 seconds")
      purge_volume = 0.0
      purge_duration = duration
    else:
      assert volume is not None
      if not 1 <= volume <= 3000:
        raise ValueError("volume must be 1-3000 uL (GUI limit)")
      purge_volume = volume
      purge_duration = 0

    validate_peristaltic_flow_rate(params.flow_rate)

    logger.info(
      "Peristaltic purge: %.1f uL, flow rate %s, cassette %s",
      purge_volume,
      params.flow_rate,
      params.cassette,
    )

    data = self._build_peristaltic_prime_command(
      plate=plate,
      volume=purge_volume,
      duration=purge_duration,
      flow_rate=PERISTALTIC_FLOW_RATE_MAP[params.flow_rate],
      reverse=True,
      cassette=params.cassette,
      pump=1,
    )
    framed_command = build_framed_message(command=0x91, data=data)
    purge_timeout = self._driver.timeout + purge_duration + 30
    async with self._driver.batch():
      await self._driver._send_step_command(framed_command, timeout=purge_timeout)

  # =========================================================================
  # COMMAND BUILDERS
  # =========================================================================

  def _build_peristaltic_prime_command(
    self,
    plate: Plate,
    volume: float,
    duration: int = 0,
    flow_rate: int = 2,
    reverse: bool = True,
    cassette: Cassette = "Any",
    pump: int = 1,
  ) -> bytes:
    """Build peristaltic prime command bytes.

    Protocol format (11 bytes):
    Example: 04 2c 01 00 00 02 01 00 01 00 00

      [0]     Plate type (wire byte, e.g. 0x04=96-well)
      [1-2]   Volume (LE) — 0x0000 when using duration mode
      [3-4]   Duration in seconds (LE) — 0x0000 when using volume mode
      [5]     Flow rate enum (0=Low, 1=Medium, 2=High)
      [6]     Reverse/submerge (0 or 1)
      [7]     Cassette type (Any: 0, 1uL: 1, 5uL: 2, 10uL: 3)
      [8]     Pump (Primary: 1, Secondary: 2)
      [9-10]  Padding (0x0000)

    Args:
      volume: Prime volume in microliters (0 when using duration mode).
      duration: Fixed duration in seconds (0 when using volume mode).
      flow_rate: Flow rate (0=Low, 1=Medium, 2=High).
      reverse: Whether to reverse/submerge after prime.
      cassette: Cassette type ("Any", "1uL", "5uL", "10uL").
      pump: Pump (1=Primary, 2=Secondary).

    Returns:
      Command bytes (11 bytes).
    """
    return (
      Writer()
      .u8(plate_to_wire_byte(plate))            # [0] Plate type
      .u16(int(volume))                       # [1-2] Volume (LE)
      .u16(duration)                          # [3-4] Duration (LE)
      .u8(flow_rate)                          # [5] Flow rate
      .u8(1 if reverse else 0)                # [6] Reverse/submerge
      .u8(cassette_to_byte(cassette))         # [7] Cassette type
      .u8(pump & 0xFF)                        # [8] Pump
      .raw_bytes(b'\x00' * 2)                 # [9-10] Padding
      .finish()
    )  # fmt: skip

  def _build_peristaltic_dispense_command(
    self,
    plate: Plate,
    volume: float,
    flow_rate: int,
    cassette: Cassette = "Any",
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int = 336,
    pre_dispense_volume: float = 0.0,
    num_pre_dispenses: int = 2,
    column_mask: list[int] | None = None,
    rows: list[int] | None = None,
    pump: int = 1,
  ) -> bytes:
    """Build peristaltic dispense command bytes.

    Protocol format (24 bytes):
    Example: 04 0a 00 02 00 00 00 50 01 0a 00 02 ff ff ff ff ff ff 00 01 00 00 00 00

      [0]     Plate type (wire byte, e.g. 0x04=96-well)
      [1-2]   Volume (LE)
      [3]     Flow rate (0=Low, 1=Med, 2=High)
      [4]     Cassette type (Any: 0, 1uL: 1, 5uL: 2, 10uL: 3)
      [5]     Offset X (signed byte)
      [6]     Offset Y (signed byte)
      [7-8]   Offset Z (LE)
      [9-10]  Pre-dispense volume (LE, 0 if disabled)
      [11]    Num pre-dispenses
      [12-17] Column mask (48 bits packed, normal: 1=selected)
      [18]    Row mask (4 bits packed, INVERTED: 0=selected, 1=deselected)
      [19]    Pump (Primary: 1, Secondary: 2)
      [20-23] Padding

    Args:
      volume: Dispense volume in microliters.
      flow_rate: Flow rate (0=Low, 1=Medium, 2=High).
      cassette: Cassette type ("Any", "1uL", "5uL", "10uL").
      offset_x: X offset (signed, 0.1mm units).
      offset_y: Y offset (signed, 0.1mm units).
      offset_z: Z offset (0.1mm units).
      pre_dispense_volume: Pre-dispense volume in uL.
      num_pre_dispenses: Number of pre-dispenses (default 2).
      column_mask: List of column indices (0-47) or None for all columns.
      rows: List of row numbers (1-4) or None for all rows.
      pump: Pump (1=Primary, 2=Secondary).

    Returns:
      Command bytes (24 bytes).
    """
    num_row_groups = plate_max_row_groups(plate)

    return (
      Writer()
      .u8(plate_to_wire_byte(plate))                                       # [0] Plate type
      .u16(int(volume))                                                  # [1-2] Volume (LE)
      .u8(flow_rate)                                                     # [3] Flow rate
      .u8(cassette_to_byte(cassette))                                    # [4] Cassette type
      .i8(offset_x)                                                      # [5] Offset X
      .i8(offset_y)                                                      # [6] Offset Y
      .u16(offset_z)                                                     # [7-8] Offset Z (LE)
      .u16(int(pre_dispense_volume))                                     # [9-10] Pre-dispense vol
      .u8(num_pre_dispenses)                                             # [11] Num pre-dispenses
      .raw_bytes(encode_column_mask(column_mask))                        # [12-17] Column mask
      .u8(encode_quadrant_mask_inverted(rows, num_row_groups=num_row_groups))  # [18] Row mask
      .u8(pump & 0xFF)                                                   # [19] Pump
      .raw_bytes(b'\x00' * 4)                                            # [20-23] Padding
      .finish()
    )  # fmt: skip
