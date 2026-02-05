"""EL406 syringe pump step methods.

Provides syringe_dispense and syringe_prime operations
plus their corresponding command builders.
"""

from __future__ import annotations

import logging
from typing import Literal

from ..constants import (
  SYRINGE_DISPENSE_COMMAND,
  SYRINGE_PRIME_COMMAND,
)
from ..helpers import (
  columns_to_column_mask,
  encode_column_mask,
  encode_signed_byte,
  encode_volume_16bit,
  plate_type_well_count,
  syringe_to_byte,
  validate_num_pre_dispenses,
  validate_offset_xy,
  validate_offset_z,
  validate_pump_delay,
  validate_submerge_duration,
  validate_syringe,
  validate_syringe_flow_rate,
  validate_syringe_volume,
  validate_volume,
)
from ..protocol import build_framed_message
from ._base import EL406StepsBaseMixin

logger = logging.getLogger("pylabrobot.plate_washing.biotek.el406")


class EL406SyringeStepsMixin(EL406StepsBaseMixin):
  """Mixin for syringe pump step operations."""

  async def syringe_dispense(
    self,
    volume: float,
    syringe: Literal["A", "B", "Both"] = "A",
    flow_rate: int = 2,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int = 336,
    pump_delay: int = 0,
    pre_dispense: bool = False,
    pre_dispense_volume: float = 0.0,
    num_pre_dispenses: int = 2,
    columns: list[int] | None = None,
  ) -> None:
    """Dispense liquid using the syringe pump.

    Args:
      volume: Dispense volume in microliters per well.
        Volume range depends on plate type:
        - 96-well: 10-3000 µL
        - 384-well: 5-1500 µL
        - 1536-well: 3-3000 µL
      syringe: Syringe selection — "A", "B", or "Both".
      flow_rate: Flow rate (1-5). Maximum rate depends on volume and plate type.
        For 96-well: rate 1 for 10+ µL, rate 2 for 20+ µL, rate 3 for 50+ µL,
        rate 4 for 60+ µL, rate 5 for 80+ µL.
        For 384-well: rate 1 for 5+ µL, rate 2 for 10+ µL, rate 3 for 25+ µL,
        rate 4 for 30+ µL, rate 5 for 40+ µL.
        For 1536-well: all rates for 3+ µL.
      offset_x: X offset (signed, 0.1mm units).
      offset_y: Y offset (signed, 0.1mm units).
      offset_z: Z offset (0.1mm units, default 336 for 96-well, 254 for 1536-well).
      pump_delay: Post-dispense delay in milliseconds (0-5000).
      pre_dispense: Whether to enable pre-dispense mode.
      pre_dispense_volume: Pre-dispense volume in µL/tube (only used if pre_dispense=True).
      num_pre_dispenses: Number of pre-dispenses (default 2).
      columns: List of 1-indexed column numbers to dispense to, or None for all columns.
        For 96-well: 1-12, for 384-well: 1-24, for 1536-well: 1-48.

    Raises:
      ValueError: If parameters are invalid.
    """
    validate_volume(volume)
    validate_syringe(syringe)
    validate_syringe_flow_rate(flow_rate)
    validate_offset_xy(offset_x, "offset_x")
    validate_offset_xy(offset_y, "offset_y")
    validate_offset_z(offset_z, "offset_z")
    validate_pump_delay(pump_delay)
    validate_num_pre_dispenses(num_pre_dispenses)

    column_mask = columns_to_column_mask(
      columns, plate_wells=plate_type_well_count(self.plate_type)
    )

    logger.info(
      "Syringe dispense: %.1f uL from syringe %s, flow rate %d",
      volume,
      syringe,
      flow_rate,
    )

    data = self._build_syringe_dispense_command(
      volume=volume,
      syringe=syringe,
      flow_rate=flow_rate,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
      pump_delay=pump_delay,
      pre_dispense=pre_dispense,
      pre_dispense_volume=pre_dispense_volume,
      num_pre_dispenses=num_pre_dispenses,
      column_mask=column_mask,
    )
    framed_command = build_framed_message(SYRINGE_DISPENSE_COMMAND, data)
    await self._send_step_command(framed_command)

  async def syringe_prime(
    self,
    syringe: Literal["A", "B"] = "A",
    volume: float = 5000.0,
    flow_rate: int = 5,
    refills: int = 2,
    pump_delay: int = 0,
    submerge_tips: bool = True,
    submerge_duration: int = 0,
  ) -> None:
    """Prime the syringe pump system.

    Fills the syringe tubing by drawing and expelling liquid.

    Args:
      syringe: Syringe selection — "A" or "B".
      volume: Volume to prime in microliters (80-9999).
      flow_rate: Flow rate (1-5).
      refills: Number of prime cycles (1-255).
      pump_delay: Delay between cycles in milliseconds (0-5000).
      submerge_tips: Submerge tips in fluid after prime (default True).
      submerge_duration: Submerge duration in minutes (0-1439, i.e. up to 23:59).
                         0 to disable submerge time. Only encoded when submerge_tips=True.

    Raises:
      ValueError: If parameters are invalid.
    """
    validate_syringe(syringe)
    validate_syringe_volume(volume)
    validate_syringe_flow_rate(flow_rate)
    validate_pump_delay(pump_delay)
    validate_submerge_duration(submerge_duration)
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
      volume=volume,
      syringe=syringe,
      flow_rate=flow_rate,
      refills=refills,
      pump_delay=pump_delay,
      submerge_tips=submerge_tips,
      submerge_duration=submerge_duration,
    )
    framed_command = build_framed_message(SYRINGE_PRIME_COMMAND, data)
    # Timeout: base for priming + submerge duration (in minutes) + buffer
    prime_timeout = self.timeout + (submerge_duration * 60) + 30
    await self._send_step_command(framed_command, timeout=prime_timeout)

  # =========================================================================
  # COMMAND BUILDERS
  # =========================================================================

  def _build_syringe_dispense_command(
    self,
    volume: float,
    syringe: str,
    flow_rate: int,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int = 336,
    pump_delay: int = 0,
    pre_dispense: bool = False,
    pre_dispense_volume: float = 0.0,
    num_pre_dispenses: int = 2,
    column_mask: list[int] | None = None,
  ) -> bytes:
    """Build syringe dispense command bytes.

    Wire format (26 bytes):
      [0]     Plate type (EL406PlateType enum value, e.g. 0x04=96-well)
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
      pump_delay: Post-dispense delay in milliseconds.
      pre_dispense: Whether to enable pre-dispense mode.
      pre_dispense_volume: Pre-dispense volume in µL/tube (only used if pre_dispense=True).
      num_pre_dispenses: Number of pre-dispenses (default 2).
      column_mask: List of column indices (0-47) or None for all columns.

    Returns:
      Command bytes (26 bytes).
    """
    vol_low, vol_high = encode_volume_16bit(volume)
    z_low = offset_z & 0xFF
    z_high = (offset_z >> 8) & 0xFF
    delay_low = pump_delay & 0xFF
    delay_high = (pump_delay >> 8) & 0xFF

    # Pre-dispense volume: only encode if pre-dispense is enabled
    if pre_dispense:
      pre_disp_vol_int = int(pre_dispense_volume)
    else:
      pre_disp_vol_int = 0
    pre_disp_vol_low = pre_disp_vol_int & 0xFF
    pre_disp_vol_high = (pre_disp_vol_int >> 8) & 0xFF

    # Encode column mask (6 bytes)
    column_mask_bytes = encode_column_mask(column_mask)

    # Bottle selection based on syringe: A→0, B→2, Both→4
    _SYRINGE_TO_BOTTLE = {"A": 0, "B": 2, "BOTH": 4}
    bottle_byte = _SYRINGE_TO_BOTTLE.get(syringe.upper(), 0)

    return (
      bytes(
        [
          self.plate_type.value,  # Plate type prefix
          syringe_to_byte(syringe),
          vol_low,
          vol_high,
          flow_rate,
          encode_signed_byte(offset_x),
          encode_signed_byte(offset_y),
          z_low,
          z_high,
          delay_low,
          delay_high,
          pre_disp_vol_low,
          pre_disp_vol_high,
          num_pre_dispenses,  # Number of pre-dispenses (default 2)
        ]
      )
      + column_mask_bytes
      + bytes(
        [
          bottle_byte,  # Bottle selection
          0,
          0,
          0,
          0,
          0,  # Padding (5 bytes)
        ]
      )
    )

  def _build_syringe_prime_command(
    self,
    volume: float,
    syringe: str,
    flow_rate: int,
    refills: int = 2,
    pump_delay: int = 0,
    submerge_tips: bool = True,
    submerge_duration: int = 0,
  ) -> bytes:
    """Build syringe prime command bytes.

    Protocol format (13 bytes):
      [0]    Plate type (EL406PlateType enum value, e.g. 0x04=96-well)
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
      pump_delay: Delay between cycles in milliseconds (default 0).
      submerge_tips: Submerge tips in fluid after prime (default True).
      submerge_duration: Submerge duration in minutes (0-1439). Only encoded
                         when submerge_tips=True.

    Returns:
      Command bytes (13 bytes).
    """
    vol_low, vol_high = encode_volume_16bit(volume)
    delay_low = pump_delay & 0xFF
    delay_high = (pump_delay >> 8) & 0xFF

    # Submerge time: only encode when submerge_tips is enabled
    if submerge_tips and submerge_duration > 0:
      sub_total = submerge_duration & 0xFFFF
    else:
      sub_total = 0
    sub_low = sub_total & 0xFF
    sub_high = (sub_total >> 8) & 0xFF

    # Bottle selection: A→0, B→2
    _SYRINGE_TO_BOTTLE = {"A": 0, "B": 2}
    bottle_byte = _SYRINGE_TO_BOTTLE.get(syringe.upper(), 0)

    return bytes(
      [
        self.plate_type.value,  # Plate type prefix
        syringe_to_byte(syringe),  # Syringe index (A=0, B=1)
        vol_low,
        vol_high,
        flow_rate,
        refills & 0xFF,
        delay_low,
        delay_high,
        1 if submerge_tips else 0,
        sub_low,  # Submerge duration low byte (minutes)
        sub_high,  # Submerge duration high byte (minutes)
        bottle_byte,  # Bottle selection
        0,  # Padding
      ]
    )
