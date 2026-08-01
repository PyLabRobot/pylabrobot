"""EL406 syringe pump step methods.

Provides syringe_dispense and syringe_prime operations
plus their corresponding command builders.
"""

from __future__ import annotations

import logging
from typing import Literal

from pylabrobot.io.binary import Writer
from pylabrobot.resources import Plate

from ..helpers import (
  plate_to_wire_byte,
  plate_well_count,
)
from ..protocol import build_framed_message, columns_to_column_mask, encode_column_mask
from ._base import EL406StepsBaseMixin

logger = logging.getLogger(__name__)

Syringe = Literal["A", "B", "Both"]


def syringe_to_byte(syringe: Syringe) -> int:
  syringe_upper = syringe.upper()
  if syringe_upper == "A":
    return 0
  if syringe_upper == "B":
    return 1
  if syringe_upper == "BOTH":
    return 2
  raise ValueError(f"Invalid syringe: {syringe}")


def validate_syringe(syringe: Syringe) -> None:
  if syringe.upper() not in {"A", "B", "BOTH"}:
    raise ValueError(f"Invalid syringe '{syringe}'. Must be one of: A, B, BOTH")


def validate_syringe_flow_rate(flow_rate: int) -> None:
  if not 1 <= flow_rate <= 5:
    raise ValueError(f"Syringe flow rate must be 1-5, got {flow_rate}")


def validate_syringe_volume(volume: float) -> None:
  if not 80 <= volume <= 9999:
    raise ValueError(f"Syringe volume must be 80-9999 uL, got {volume}")


def validate_pump_delay(delay: int) -> None:
  if not 0 <= delay <= 5000:
    raise ValueError(f"Pump delay must be 0-5000 ms, got {delay}")


def validate_submerge_duration(duration: int) -> None:
  if not 0 <= duration <= 1439:
    raise ValueError(f"Submerge duration must be 0-1439 minutes, got {duration}")


class EL406SyringeStepsMixin(EL406StepsBaseMixin):
  """Mixin for syringe pump step operations."""

  async def syringe_dispense(
    self,
    plate: Plate,
    volume: float,
    syringe: Syringe = "A",
    flow_rate: int = 2,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int = 336,
    pump_delay: float = 0.0,
    pre_dispense: bool = False,
    pre_dispense_volume: float = 0.0,
    num_pre_dispenses: int = 2,
    columns: list[int] | None = None,
  ) -> None:
    """Dispense liquid using the syringe pump.

    Args:
      plate: PLR Plate resource.
      volume: Dispense volume in microliters per well.
        Volume range depends on plate type:
        - 96-well: 10-3000 uL
        - 384-well: 5-1500 uL
        - 1536-well: 3-3000 uL
      syringe: Syringe selection — "A", "B", or "Both".
      flow_rate: Flow rate (1-5). Maximum rate depends on volume and plate type.
        For 96-well: rate 1 for 10+ uL, rate 2 for 20+ uL, rate 3 for 50+ uL,
        rate 4 for 60+ uL, rate 5 for 80+ uL.
        For 384-well: rate 1 for 5+ uL, rate 2 for 10+ uL, rate 3 for 25+ uL,
        rate 4 for 30+ uL, rate 5 for 40+ uL.
        For 1536-well: all rates for 3+ uL.
      offset_x: X offset (signed, 0.1mm units).
      offset_y: Y offset (signed, 0.1mm units).
      offset_z: Z offset (0.1mm units, default 336 for 96-well, 254 for 1536-well).
      pump_delay: Post-dispense delay in seconds (0-5). Wire resolution: 1 ms.
      pre_dispense: Whether to enable pre-dispense mode.
      pre_dispense_volume: Pre-dispense volume in uL/tube (only used if pre_dispense=True).
      num_pre_dispenses: Number of pre-dispenses (default 2).
      columns: List of 1-indexed column numbers to dispense to, or None for all columns.
        For 96-well: 1-12, for 384-well: 1-24, for 1536-well: 1-48.

    Raises:
      ValueError: If parameters are invalid.
    """
    # Convert PLR units (seconds) to wire units (ms)
    pump_delay_ms = round(pump_delay * 1000)

    if volume <= 0:
      raise ValueError(f"volume must be positive, got {volume}")
    validate_syringe(syringe)
    validate_syringe_flow_rate(flow_rate)
    validate_pump_delay(pump_delay_ms)

    column_mask = columns_to_column_mask(columns, plate_wells=plate_well_count(plate))

    logger.info(
      "Syringe dispense: %.1f uL from syringe %s, flow rate %d",
      volume,
      syringe,
      flow_rate,
    )

    data = self._build_syringe_dispense_command(
      plate=plate,
      volume=volume,
      syringe=syringe,
      flow_rate=flow_rate,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
      pump_delay_ms=pump_delay_ms,
      pre_dispense=pre_dispense,
      pre_dispense_volume=pre_dispense_volume,
      num_pre_dispenses=num_pre_dispenses,
      column_mask=column_mask,
    )
    framed_command = build_framed_message(command=0xA1, data=data)
    async with self.batch(plate):
      await self._send_step_command(framed_command)

  async def syringe_prime(
    self,
    plate: Plate,
    syringe: Literal["A", "B"] = "A",
    volume: float = 5000.0,
    flow_rate: int = 5,
    refills: int = 2,
    pump_delay: float = 0.0,
    submerge_tips: bool = True,
    submerge_duration: float = 0.0,
  ) -> None:
    """Prime the syringe pump system.

    Fills the syringe tubing by drawing and expelling liquid.

    Args:
      plate: PLR Plate resource.
      syringe: Syringe selection — "A" or "B".
      volume: Volume to prime in microliters (80-9999).
      flow_rate: Flow rate (1-5).
      refills: Number of prime cycles (1-255).
      pump_delay: Delay between cycles in seconds (0-5). Wire resolution: 1 ms.
      submerge_tips: Submerge tips in fluid after prime (default True).
      submerge_duration: Submerge duration in seconds (0-86340, i.e. up to 23:59).
                         0 to disable submerge time. Only encoded when submerge_tips=True.
                         Wire resolution: 60 s (1 minute).

    Raises:
      ValueError: If parameters are invalid.
    """
    # Convert to wire units: seconds → milliseconds, seconds → minutes
    pump_delay_ms = round(pump_delay * 1000)
    if submerge_duration != 0 and submerge_duration % 60 != 0:
      raise ValueError(
        f"Submerge duration must be a multiple of 60 seconds (device resolution is 1 minute), "
        f"got {submerge_duration}"
      )
    submerge_duration_min = round(submerge_duration / 60)

    validate_syringe(syringe)
    validate_syringe_volume(volume)
    validate_syringe_flow_rate(flow_rate)
    validate_pump_delay(pump_delay_ms)
    validate_submerge_duration(submerge_duration_min)
    if not 1 <= refills <= 255:
      raise ValueError(f"refills must be 1-255, got {refills}")

    logger.info(
      "Syringe prime: syringe %s, %.1f uL, flow rate %d, %d refills",
      syringe,
      volume,
      flow_rate,
      refills,
    )

    data = self._build_syringe_prime_command(
      plate=plate,
      volume=volume,
      syringe=syringe,
      flow_rate=flow_rate,
      refills=refills,
      pump_delay_ms=pump_delay_ms,
      submerge_tips=submerge_tips,
      submerge_duration_min=submerge_duration_min,
    )
    framed_command = build_framed_message(command=0xA2, data=data)
    # Timeout: base for priming + submerge duration + buffer
    prime_timeout = self.timeout + submerge_duration + 30
    async with self.batch(plate):
      await self._send_step_command(framed_command, timeout=prime_timeout)

  # =========================================================================
  # COMMAND BUILDERS
  # =========================================================================

  def _build_syringe_dispense_command(
    self,
    plate: Plate,
    volume: float,
    syringe: Syringe,
    flow_rate: int,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int = 336,
    pump_delay_ms: int = 0,
    pre_dispense: bool = False,
    pre_dispense_volume: float = 0.0,
    num_pre_dispenses: int = 2,
    column_mask: list[int] | None = None,
  ) -> bytes:
    """Build syringe dispense command bytes.

    Wire format (26 bytes):
      [0]     Plate type (wire byte, e.g. 0x04=96-well)
      [1]     Syringe: A=0, B=1, Both=2
      [2-3]   Volume: 2 bytes, little-endian, in uL
      [4]     Flow rate: 1-5
      [5]     Offset X: signed byte
      [6]     Offset Y: signed byte
      [7-8]   Offset Z: 2 bytes, little-endian
      [9-10]  Pump delay: 2 bytes, little-endian, in ms
      [11-12] Pre-dispense volume: 2 bytes, little-endian (0 if pre_dispense=False)
      [13]    Number of pre-dispenses (default 2)
      [14-19] Column mask: 6 bytes (48 bits packed)
      [20]    Bottle selection (A→0, B→2, Both→4)
      [21-25] Padding (5 bytes)

    Args:
      volume: Dispense volume in microliters.
      syringe: Syringe selection (A, B, Both).
      flow_rate: Flow rate (1-5).
      offset_x: X offset (signed, 0.1mm units).
      offset_y: Y offset (signed, 0.1mm units).
      offset_z: Z offset (0.1mm units).
      pump_delay_ms: Post-dispense delay in milliseconds.
      pre_dispense: Whether to enable pre-dispense mode.
      pre_dispense_volume: Pre-dispense volume in uL/tube (only used if pre_dispense=True).
      num_pre_dispenses: Number of pre-dispenses (default 2).
      column_mask: List of column indices (0-47) or None for all columns.

    Returns:
      Command bytes (26 bytes).
    """
    pre_disp_vol_int = int(pre_dispense_volume) if pre_dispense else 0
    bottle_byte = {"A": 0, "B": 2, "BOTH": 4}.get(syringe.upper(), 0)

    return (
      Writer()
      .u8(plate_to_wire_byte(plate))             # [0] Plate type
      .u8(syringe_to_byte(syringe))            # [1] Syringe
      .u16(int(volume))                        # [2-3] Volume (LE)
      .u8(flow_rate)                           # [4] Flow rate
      .i8(offset_x)                            # [5] Offset X
      .i8(offset_y)                            # [6] Offset Y
      .u16(offset_z)                           # [7-8] Offset Z (LE)
      .u16(pump_delay_ms)                      # [9-10] Pump delay (LE)
      .u16(pre_disp_vol_int)                   # [11-12] Pre-dispense vol (LE)
      .u8(num_pre_dispenses)                   # [13] Num pre-dispenses
      .raw_bytes(encode_column_mask(column_mask))  # [14-19] Column mask
      .u8(bottle_byte)                         # [20] Bottle selection
      .raw_bytes(b'\x00' * 5)                  # [21-25] Padding
      .finish()
    )  # fmt: skip

  def _build_syringe_prime_command(
    self,
    plate: Plate,
    volume: float,
    syringe: Literal["A", "B"],
    flow_rate: int,
    refills: int = 2,
    pump_delay_ms: int = 0,
    submerge_tips: bool = True,
    submerge_duration_min: int = 0,
  ) -> bytes:
    """Build syringe prime command bytes.

    Protocol format (13 bytes):
      [0]    Plate type (wire byte, e.g. 0x04=96-well)
      [1]    Syringe: A=0, B=1
      [2-3]  Volume: 2 bytes, little-endian, in uL
      [4]    Flow rate: 1-5
      [5]    Refills: byte (number of prime cycles)
      [6-7]  Pump delay: 2 bytes, little-endian, in ms
      [8]    Submerge tips (0 or 1) — "Submerge tips in fluid after prime"
      [9-10] Submerge duration in minutes (LE uint16). 0 if submerge_tips=False.
      [11]   Bottle: derived from syringe (A->0, B->2)
      [12]   Padding

    Args:
      volume: Prime volume in microliters.
      syringe: Syringe selection (A, B).
      flow_rate: Flow rate (1-5).
      refills: Number of prime cycles.
      pump_delay_ms: Delay between cycles in milliseconds (default 0).
      submerge_tips: Submerge tips in fluid after prime (default True).
      submerge_duration_min: Submerge duration in minutes (0-1439). Only encoded
                             when submerge_tips=True.

    Returns:
      Command bytes (13 bytes).
    """
    sub_total = submerge_duration_min if (submerge_tips and submerge_duration_min > 0) else 0
    bottle_byte = {"A": 0, "B": 2}.get(syringe.upper(), 0)

    return (
      Writer()
      .u8(plate_to_wire_byte(plate))             # [0] Plate type
      .u8(syringe_to_byte(syringe))            # [1] Syringe (A=0, B=1)
      .u16(int(volume))                        # [2-3] Volume (LE)
      .u8(flow_rate)                           # [4] Flow rate
      .u8(refills & 0xFF)                      # [5] Refills
      .u16(pump_delay_ms)                      # [6-7] Pump delay (LE)
      .u8(1 if submerge_tips else 0)           # [8] Submerge tips
      .u16(sub_total)                          # [9-10] Submerge duration (LE, minutes)
      .u8(bottle_byte)                         # [11] Bottle selection
      .u8(0x00)                                # [12] Padding
      .finish()
    )  # fmt: skip
