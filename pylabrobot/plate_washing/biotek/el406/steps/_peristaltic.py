"""EL406 peristaltic pump step methods.

Provides peristaltic_prime, peristaltic_dispense, and peristaltic_purge operations
plus their corresponding command builders.
"""

from __future__ import annotations

import logging
from typing import Literal

from ..constants import (
  PERISTALTIC_DISPENSE_COMMAND,
  PERISTALTIC_PRIME_COMMAND,
  PERISTALTIC_PURGE_COMMAND,
)
from ..helpers import (
  cassette_to_byte,
  columns_to_column_mask,
  encode_column_mask,
  encode_quadrant_mask_inverted,
  encode_signed_byte,
  encode_volume_16bit,
  plate_type_default_z,
  plate_type_max_columns,
  plate_type_max_rows,
  plate_type_well_count,
  validate_num_pre_dispenses,
  validate_peristaltic_flow_rate,
  validate_volume,
)
from ..protocol import build_framed_message
from ._base import EL406StepsBaseMixin

logger = logging.getLogger("pylabrobot.plate_washing.biotek.el406")

PERISTALTIC_FLOW_RATE_MAP: dict[str, int] = {"Low": 0, "Medium": 1, "High": 2}


class EL406PeristalticStepsMixin(EL406StepsBaseMixin):
  """Mixin for peristaltic pump step operations."""

  def _validate_peristaltic_well_selection(
    self,
    columns: list[int] | None,
    rows: list[int] | None,
  ) -> list[int] | None:
    """Validate column/row selection and return column mask."""
    max_cols = plate_type_max_columns(self.plate_type)
    if columns is not None:
      for col in columns:
        if col < 1 or col > max_cols:
          raise ValueError(f"Column {col} out of range for plate type (1-{max_cols}).")

    max_rows = plate_type_max_rows(self.plate_type)
    if rows is not None:
      for row in rows:
        if row < 1 or row > max_rows:
          raise ValueError(f"Row {row} out of range for plate type (1-{max_rows}).")

    return columns_to_column_mask(columns, plate_wells=plate_type_well_count(self.plate_type))

  def _validate_peristaltic_dispense_params(
    self,
    volume: float,
    flow_rate: Literal["Low", "Medium", "High"],
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
      raise ValueError(f"Peri-pump dispense volume must be 1-3000 µL, got {volume}")
    validate_peristaltic_flow_rate(flow_rate)
    if not -125 <= offset_x <= 125:
      raise ValueError(f"Peri-pump dispense X-axis offset must be -125..125, got {offset_x}")
    if not -40 <= offset_y <= 40:
      raise ValueError(f"Peri-pump dispense Y-axis offset must be -40..40, got {offset_y}")

    if offset_z is None:
      offset_z = plate_type_default_z(self.plate_type)
    if not 1 <= offset_z <= 1500:
      raise ValueError(f"Peri-pump dispense Z-axis offset must be 1..1500, got {offset_z}")

    validate_volume(pre_dispense_volume, allow_zero=True)

    column_mask = self._validate_peristaltic_well_selection(columns, rows)

    return (offset_z, PERISTALTIC_FLOW_RATE_MAP[flow_rate], column_mask)

  async def peristaltic_prime(
    self,
    volume: float | None = None,
    duration: int | None = None,
    flow_rate: Literal["Low", "Medium", "High"] = "High",
    cassette: Literal["Any", "1uL", "5uL", "10uL"] = "Any",
  ) -> None:
    """Prime the peristaltic fluid lines.

    Specify either ``volume`` (uL/tube) or ``duration`` (seconds), not both.
    If neither is given, defaults to volume mode with 1000 uL.

    Note: Peristaltic prime has no buffer selection.
    Use ``manifold_prime()`` for buffer-specific priming.

    Args:
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
        raise ValueError("volume must be 1-3000 µL (GUI limit)")
      prime_volume = volume
      prime_duration = 0

    validate_peristaltic_flow_rate(flow_rate)

    logger.info(
      "Peristaltic prime: %.1f uL, flow rate %s, cassette %s", prime_volume, flow_rate, cassette
    )

    data = self._build_peristaltic_prime_command(
      volume=prime_volume,
      duration=prime_duration,
      flow_rate=PERISTALTIC_FLOW_RATE_MAP[flow_rate],
      reverse=True,
      cassette=cassette,
      pump=1,
    )
    framed_command = build_framed_message(PERISTALTIC_PRIME_COMMAND, data)
    # Timeout: duration (if specified) + buffer for volume-based priming
    prime_timeout = self.timeout + prime_duration + 30
    await self._send_step_command(framed_command, timeout=prime_timeout)

  async def peristaltic_dispense(
    self,
    volume: float,
    flow_rate: Literal["Low", "Medium", "High"] = "High",
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int | None = None,
    pre_dispense_volume: float = 10.0,
    num_pre_dispenses: int = 2,
    cassette: Literal["Any", "1uL", "5uL", "10uL"] = "Any",
    columns: list[int] | None = None,
    rows: list[int] | None = None,
  ) -> None:
    """Dispense liquid using the peristaltic pump.

    Args:
      volume: Dispense volume in microliters (1-3000).
      flow_rate: Flow rate ("Low", "Medium", or "High").
      offset_x: X offset in 0.1mm units (-125 to 125).
      offset_y: Y offset in 0.1mm units (-40 to 40).
      offset_z: Z offset in 0.1mm units (1-1500). Default depends on plate type:
        336 for 96/384-well, 254 for 1536-well.
      pre_dispense_volume: Pre-dispense volume in µL (0 to disable).
      num_pre_dispenses: Number of pre-dispenses (default 2).
      cassette: Cassette type ("Any", "1uL", "5uL", "10uL").
      columns: List of 1-indexed column numbers to dispense to, or None for all.
        For 96-well: 1-12, for 384-well: 1-24, for 1536-well: 1-48.
      rows: List of 1-indexed row group numbers, or None for all.
        For 96-well: only 1 (no selection). For 384-well: 1-2. For 1536-well: 1-4.

    Raises:
      ValueError: If parameters are invalid.
    """
    validate_num_pre_dispenses(num_pre_dispenses)
    offset_z, flow_rate_enum, column_mask = self._validate_peristaltic_dispense_params(
      volume=volume,
      flow_rate=flow_rate,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
      pre_dispense_volume=pre_dispense_volume,
      columns=columns,
      rows=rows,
    )

    logger.info(
      "Peristaltic dispense: %.1f uL, flow rate %s, cassette %s",
      volume,
      flow_rate,
      cassette,
    )

    data = self._build_peristaltic_dispense_command(
      volume=volume,
      flow_rate=flow_rate_enum,
      cassette=cassette,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
      pre_dispense_volume=pre_dispense_volume,
      num_pre_dispenses=num_pre_dispenses,
      column_mask=column_mask,
      rows=rows,
      pump=1,
    )
    framed_command = build_framed_message(PERISTALTIC_DISPENSE_COMMAND, data)
    await self._send_step_command(framed_command)

  async def peristaltic_purge(
    self,
    volume: float | None = None,
    duration: int | None = None,
    flow_rate: Literal["Low", "Medium", "High"] = "High",
    cassette: Literal["Any", "1uL", "5uL", "10uL"] = "Any",
  ) -> None:
    """Purge the fluid lines using the peristaltic pump.

    Specify either ``volume`` (uL/tube) or ``duration`` (seconds), not both.

    PERISTALTIC_PURGE uses the same data format as PERISTALTIC_PRIME
    (both send identical data bytes).

    Args:
      volume: Purge volume in microliters.
      duration: Fixed duration in seconds (alternative to volume).
      flow_rate: Flow rate ("Low", "Medium", or "High").
      cassette: Cassette type ("Any", "1uL", "5uL", "10uL").

    Raises:
      ValueError: If both volume and duration are specified, or if neither is given.
    """
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
      assert volume is not None  # guaranteed by the mutual-exclusion check above
      if not 1 <= volume <= 3000:
        raise ValueError("volume must be 1-3000 µL (GUI limit)")
      purge_volume = volume
      purge_duration = 0

    validate_peristaltic_flow_rate(flow_rate)

    logger.info(
      "Peristaltic purge: %.1f uL, flow rate %s, cassette %s",
      purge_volume,
      flow_rate,
      cassette,
    )

    # Reuse peristaltic_prime builder since data format is identical
    data = self._build_peristaltic_prime_command(
      volume=purge_volume,
      duration=purge_duration,
      flow_rate=PERISTALTIC_FLOW_RATE_MAP[flow_rate],
      reverse=True,
      cassette=cassette,
      pump=1,
    )
    framed_command = build_framed_message(PERISTALTIC_PURGE_COMMAND, data)
    # Timeout: duration (if specified) + buffer for volume-based purging
    purge_timeout = self.timeout + purge_duration + 30
    await self._send_step_command(framed_command, timeout=purge_timeout)

  # =========================================================================
  # COMMAND BUILDERS
  # =========================================================================

  def _build_peristaltic_prime_command(
    self,
    volume: float,
    duration: int = 0,
    flow_rate: int = 2,
    reverse: bool = True,
    cassette: str = "Any",
    pump: int = 1,
  ) -> bytes:
    """Build peristaltic prime command bytes.

    Protocol format (11 bytes):
    Example: 04 2c 01 00 00 02 01 00 01 00 00

      [0]     Plate type (EL406PlateType enum value, e.g. 0x04=96-well)
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
    vol_low, vol_high = encode_volume_16bit(volume)
    dur_low = duration & 0xFF
    dur_high = (duration >> 8) & 0xFF
    cassette_byte = cassette_to_byte(cassette)

    return bytes(
      [
        self.plate_type.value,  # Plate type prefix
        vol_low,
        vol_high,
        dur_low,  # Duration low byte (0 in volume mode)
        dur_high,  # Duration high byte
        flow_rate,  # Flow rate (0=Low, 1=Medium, 2=High)
        1 if reverse else 0,  # Reverse/submerge
        cassette_byte,  # Cassette type
        pump & 0xFF,  # Pump (1=Primary, 2=Secondary)
        0,
        0,  # Padding
      ]
    )

  def _build_peristaltic_dispense_command(
    self,
    volume: float,
    flow_rate: int,
    cassette: str = "Any",
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

      [0]     Plate type (EL406PlateType enum value, e.g. 0x04=96-well)
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
      pre_dispense_volume: Pre-dispense volume in µL.
      num_pre_dispenses: Number of pre-dispenses (default 2).
      column_mask: List of column indices (0-47) or None for all columns.
      rows: List of row numbers (1-4) or None for all rows.
      pump: Pump (1=Primary, 2=Secondary).

    Returns:
      Command bytes (24 bytes).
    """
    vol_low, vol_high = encode_volume_16bit(volume)
    z_low = offset_z & 0xFF
    z_high = (offset_z >> 8) & 0xFF
    pre_disp_low, pre_disp_high = encode_volume_16bit(pre_dispense_volume)
    cassette_byte = cassette_to_byte(cassette)
    # Pass correct num_row_groups based on plate type
    num_row_groups = plate_type_max_rows(self.plate_type)
    row_mask_byte = encode_quadrant_mask_inverted(rows, num_row_groups=num_row_groups)

    # Encode column mask (6 bytes)
    column_mask_bytes = encode_column_mask(column_mask)

    return (
      bytes(
        [
          self.plate_type.value,  # Plate type prefix
          vol_low,
          vol_high,
          flow_rate,  # Flow rate (0=Low, 1=Medium, 2=High)
          cassette_byte,  # Cassette type
          encode_signed_byte(offset_x),  # Offset X
          encode_signed_byte(offset_y),  # Offset Y
          z_low,
          z_high,
          pre_disp_low,
          pre_disp_high,
          num_pre_dispenses,  # Number of pre-dispenses
        ]
      )
      + column_mask_bytes
      + bytes(
        [
          row_mask_byte,  # Row mask (inverted: 0=selected)
          pump & 0xFF,  # Pump (1=Primary, 2=Secondary)
          0,
          0,
          0,
          0,  # Padding (4 bytes)
        ]
      )
    )
