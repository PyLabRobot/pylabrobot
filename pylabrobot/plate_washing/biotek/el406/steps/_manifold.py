"""EL406 manifold step methods.

Provides manifold_aspirate, manifold_dispense, manifold_wash, manifold_prime,
and manifold_auto_clean operations plus their corresponding command builders.
"""

from __future__ import annotations

import logging
from typing import Literal

from ..constants import (
  MANIFOLD_ASPIRATE_COMMAND,
  MANIFOLD_AUTO_CLEAN_COMMAND,
  MANIFOLD_DISPENSE_COMMAND,
  MANIFOLD_PRIME_COMMAND,
  MANIFOLD_WASH_COMMAND,
)
from ..helpers import (
  INTENSITY_TO_BYTE,
  VALID_TRAVEL_RATES,
  encode_signed_byte,
  encode_volume_16bit,
  get_plate_type_wash_defaults,
  travel_rate_to_byte,
  validate_buffer,
  validate_cycles,
  validate_delay_ms,
  validate_flow_rate,
  validate_intensity,
  validate_offset_xy,
  validate_offset_z,
  validate_travel_rate,
  validate_volume,
)
from ..protocol import build_framed_message
from ._base import EL406StepsBaseMixin

logger = logging.getLogger("pylabrobot.plate_washing.biotek.el406")


class EL406ManifoldStepsMixin(EL406StepsBaseMixin):
  """Mixin for manifold step operations."""

  @staticmethod
  def _validate_manifold_xy(x: int, y: int, label: str) -> None:
    """Validate manifold X/Y offsets (X: -60..60, Y: -40..40)."""
    if not -60 <= x <= 60:
      raise ValueError(f"{label} X offset must be -60..60, got {x}")
    if not -40 <= y <= 40:
      raise ValueError(f"{label} Y offset must be -40..40, got {y}")

  @staticmethod
  def _validate_aspirate_mode_params(
    vacuum_filtration: bool,
    travel_rate: str,
    delay_ms: int,
    vacuum_time_sec: int,
  ) -> tuple[int, int]:
    """Validate aspirate mode-specific params and return (time_value, rate_byte)."""
    if not vacuum_filtration:
      if travel_rate not in VALID_TRAVEL_RATES:
        raise ValueError(
          f"Invalid travel rate '{travel_rate}'. Must be one of: "
          f"{', '.join(repr(r) for r in sorted(VALID_TRAVEL_RATES))}"
        )
      if not 0 <= delay_ms <= 5000:
        raise ValueError(f"Aspirate delay must be 0-5000 ms, got {delay_ms}")
      return (delay_ms, travel_rate_to_byte(travel_rate))

    if not 5 <= vacuum_time_sec <= 999:
      raise ValueError(f"Vacuum filtration time must be 5-999 seconds, got {vacuum_time_sec}")
    return (vacuum_time_sec, travel_rate_to_byte("3"))

  @classmethod
  def _validate_aspirate_offsets(
    cls,
    offset_x: int,
    offset_y: int,
    offset_z: int,
    secondary_aspirate: bool,
    secondary_x: int,
    secondary_y: int,
    secondary_z: int,
  ) -> None:
    """Validate aspirate XYZ offset ranges (primary and secondary)."""
    cls._validate_manifold_xy(offset_x, offset_y, "Aspirate")
    if not 1 <= offset_z <= 210:
      raise ValueError(f"Aspirate Z offset must be 1-210, got {offset_z}")
    if secondary_aspirate:
      cls._validate_manifold_xy(secondary_x, secondary_y, "Secondary")
      if not 1 <= secondary_z <= 210:
        raise ValueError(f"Secondary Z offset must be 1-210, got {secondary_z}")

  def _validate_aspirate_params(
    self,
    vacuum_filtration: bool,
    travel_rate: str,
    delay_ms: int,
    vacuum_time_sec: int,
    offset_x: int,
    offset_y: int,
    offset_z: int | None,
    secondary_aspirate: bool,
    secondary_x: int,
    secondary_y: int,
    secondary_z: int | None,
  ) -> tuple[int, int, int, int]:
    """Validate aspirate parameters and resolve plate-type defaults.

    Returns:
      (offset_z, secondary_z, time_value, rate_byte)
    """
    pt_defaults = get_plate_type_wash_defaults(self.plate_type)
    if offset_z is None:
      offset_z = pt_defaults["aspirate_z"]
    if secondary_z is None:
      secondary_z = pt_defaults["aspirate_z"]

    time_value, rate_byte = self._validate_aspirate_mode_params(
      vacuum_filtration,
      travel_rate,
      delay_ms,
      vacuum_time_sec,
    )
    self._validate_aspirate_offsets(
      offset_x,
      offset_y,
      offset_z,
      secondary_aspirate,
      secondary_x,
      secondary_y,
      secondary_z,
    )
    return (offset_z, secondary_z, time_value, rate_byte)

  @staticmethod
  def _validate_dispense_extras(
    pre_dispense_volume: float,
    pre_dispense_flow_rate: int,
    vacuum_delay_volume: float,
  ) -> None:
    """Validate pre-dispense and vacuum-delay parameters for manifold dispense."""
    if pre_dispense_volume != 0 and not 25 <= pre_dispense_volume <= 3000:
      raise ValueError(
        f"Manifold pre-dispense volume must be 0 (disabled) or 25-3000 µL, "
        f"got {pre_dispense_volume}"
      )
    if not 3 <= pre_dispense_flow_rate <= 11:
      raise ValueError(
        f"Manifold pre-dispense flow rate must be 3-11, got {pre_dispense_flow_rate}"
      )
    if not 0 <= vacuum_delay_volume <= 3000:
      raise ValueError(f"Manifold vacuum delay volume must be 0-3000 µL, got {vacuum_delay_volume}")

  def _validate_dispense_params(
    self,
    volume: float,
    buffer: str,
    flow_rate: int,
    offset_x: int,
    offset_y: int,
    offset_z: int | None,
    pre_dispense_volume: float,
    pre_dispense_flow_rate: int,
    vacuum_delay_volume: float,
  ) -> int:
    """Validate dispense parameters and resolve plate-type defaults.

    Returns:
      Resolved offset_z.
    """
    if offset_z is None:
      pt_defaults = get_plate_type_wash_defaults(self.plate_type)
      offset_z = pt_defaults["dispense_z"]

    if not 25 <= volume <= 3000:
      raise ValueError(f"Manifold dispense volume must be 25-3000 µL, got {volume}")
    validate_buffer(buffer)
    if not 1 <= flow_rate <= 11:
      raise ValueError(f"Manifold dispense flow rate must be 1-11, got {flow_rate}")
    if flow_rate <= 2 and vacuum_delay_volume <= 0:
      raise ValueError(
        f"Flow rates 1-2 (cell wash) require vacuum_delay_volume > 0, "
        f"got flow_rate={flow_rate} with vacuum_delay_volume={vacuum_delay_volume}"
      )
    self._validate_manifold_xy(offset_x, offset_y, "Manifold dispense")
    if not 1 <= offset_z <= 210:
      raise ValueError(f"Manifold dispense Z offset must be 1-210, got {offset_z}")
    self._validate_dispense_extras(pre_dispense_volume, pre_dispense_flow_rate, vacuum_delay_volume)

    return offset_z

  def _resolve_wash_defaults(
    self,
    dispense_volume: float | None,
    dispense_z: int | None,
    aspirate_z: int | None,
    secondary_z: int | None,
    final_secondary_z: int | None,
  ) -> tuple[float, int, int, int, int]:
    """Resolve plate-type-aware defaults for wash parameters."""
    pt_defaults = get_plate_type_wash_defaults(self.plate_type)
    if dispense_volume is None:
      dispense_volume = pt_defaults["dispense_volume"]
    if dispense_z is None:
      dispense_z = pt_defaults["dispense_z"]
    if aspirate_z is None:
      aspirate_z = pt_defaults["aspirate_z"]
    if secondary_z is None:
      secondary_z = pt_defaults["aspirate_z"]
    if final_secondary_z is None:
      final_secondary_z = pt_defaults["aspirate_z"]
    return (dispense_volume, dispense_z, aspirate_z, secondary_z, final_secondary_z)

  @staticmethod
  def _validate_wash_core_params(
    cycles: int,
    buffer: str,
    dispense_volume: float,
    dispense_flow_rate: int,
    dispense_x: int,
    dispense_y: int,
    dispense_z: int,
    aspirate_travel_rate: int,
    aspirate_z: int,
    pre_dispense_flow_rate: int,
    aspirate_delay_ms: int,
    aspirate_x: int,
    aspirate_y: int,
    wash_format: Literal["Plate", "Sector"],
    sector_mask: int,
  ) -> None:
    """Validate core wash dispense/aspirate parameters."""
    validate_cycles(cycles)
    validate_volume(dispense_volume)
    validate_buffer(buffer)
    validate_flow_rate(dispense_flow_rate)
    validate_offset_xy(dispense_x, "Wash dispense X")
    validate_offset_xy(dispense_y, "Wash dispense Y")
    validate_offset_z(dispense_z, "Wash dispense Z")
    validate_travel_rate(aspirate_travel_rate)
    if wash_format not in ("Plate", "Sector"):
      raise ValueError(f"wash_format must be 'Plate' or 'Sector', got '{wash_format}'")
    if not 0 <= sector_mask <= 0xFFFF:
      raise ValueError(f"sector_mask must be 0x0000-0xFFFF, got 0x{sector_mask:04X}")
    validate_offset_z(aspirate_z, "Wash aspirate Z")
    validate_flow_rate(pre_dispense_flow_rate)
    validate_delay_ms(aspirate_delay_ms)
    validate_offset_xy(aspirate_x, "Wash aspirate X")
    validate_offset_xy(aspirate_y, "Wash aspirate Y")

  @staticmethod
  def _validate_wash_final_and_extras(
    final_aspirate_z: int | None,
    final_aspirate_x: int,
    final_aspirate_y: int,
    final_aspirate_delay_ms: int,
    pre_dispense_volume: float,
    vacuum_delay_volume: float,
    soak_duration: int,
    shake_duration: int,
    shake_intensity: str,
  ) -> None:
    """Validate final-aspirate, pre-dispense, soak/shake parameters."""
    if final_aspirate_z is not None:
      validate_offset_z(final_aspirate_z, "Final aspirate Z")
    validate_offset_xy(final_aspirate_x, "Final aspirate X")
    validate_offset_xy(final_aspirate_y, "Final aspirate Y")
    validate_delay_ms(final_aspirate_delay_ms)
    if pre_dispense_volume != 0 and not 25 <= pre_dispense_volume <= 3000:
      raise ValueError(
        f"Wash pre-dispense volume must be 0 (disabled) or 25-3000 uL, "
        f"got {pre_dispense_volume}"
      )
    if not 0 <= vacuum_delay_volume <= 3000:
      raise ValueError(f"Wash vacuum delay volume must be 0-3000 uL, got {vacuum_delay_volume}")
    if not 0 <= soak_duration <= 3599:
      raise ValueError(f"Wash soak duration must be 0-3599 seconds, got {soak_duration}")
    if not 0 <= shake_duration <= 3599:
      raise ValueError(f"Wash shake duration must be 0-3599 seconds, got {shake_duration}")
    validate_intensity(shake_intensity)

  @classmethod
  def _validate_wash_secondary_aspirates(
    cls,
    secondary_aspirate: bool,
    secondary_z: int,
    secondary_x: int,
    secondary_y: int,
    final_secondary_aspirate: bool,
    final_secondary_z: int,
    final_secondary_x: int,
    final_secondary_y: int,
  ) -> None:
    """Validate secondary and final-secondary aspirate offsets."""
    if secondary_aspirate:
      validate_offset_z(secondary_z, "Wash secondary aspirate Z")
      cls._validate_manifold_xy(secondary_x, secondary_y, "Secondary")
    if final_secondary_aspirate:
      validate_offset_z(final_secondary_z, "Final secondary aspirate Z")
      cls._validate_manifold_xy(final_secondary_x, final_secondary_y, "Final secondary")

  @staticmethod
  def _validate_wash_optional_features(
    bottom_wash: bool,
    bottom_wash_volume: float,
    bottom_wash_flow_rate: int,
    pre_dispense_between_cycles_volume: float,
    pre_dispense_between_cycles_flow_rate: int,
  ) -> None:
    """Validate bottom wash and mid-cycle pre-dispense."""
    if bottom_wash:
      if not 25 <= bottom_wash_volume <= 3000:
        raise ValueError(f"Bottom wash volume must be 25-3000 uL, got {bottom_wash_volume}")
      validate_flow_rate(bottom_wash_flow_rate)
    if pre_dispense_between_cycles_volume != 0:
      if not 25 <= pre_dispense_between_cycles_volume <= 3000:
        raise ValueError(
          f"Pre-dispense between cycles volume must be 0 (disabled) or "
          f"25-3000 uL, got {pre_dispense_between_cycles_volume}"
        )
      validate_flow_rate(pre_dispense_between_cycles_flow_rate)

  def _validate_wash_params(
    self,
    cycles: int,
    buffer: str,
    dispense_volume: float | None,
    dispense_flow_rate: int,
    dispense_x: int,
    dispense_y: int,
    dispense_z: int | None,
    aspirate_travel_rate: int,
    aspirate_z: int | None,
    pre_dispense_flow_rate: int,
    aspirate_delay_ms: int,
    aspirate_x: int,
    aspirate_y: int,
    final_aspirate_z: int | None,
    final_aspirate_x: int,
    final_aspirate_y: int,
    final_aspirate_delay_ms: int,
    pre_dispense_volume: float,
    vacuum_delay_volume: float,
    soak_duration: int,
    shake_duration: int,
    shake_intensity: str,
    secondary_aspirate: bool,
    secondary_z: int | None,
    secondary_x: int,
    secondary_y: int,
    final_secondary_aspirate: bool,
    final_secondary_z: int | None,
    final_secondary_x: int,
    final_secondary_y: int,
    bottom_wash: bool,
    bottom_wash_volume: float,
    bottom_wash_flow_rate: int,
    pre_dispense_between_cycles_volume: float,
    pre_dispense_between_cycles_flow_rate: int,
    wash_format: Literal["Plate", "Sector"],
    sector_mask: int,
  ) -> tuple[float, int, int, int, int]:
    """Validate wash parameters and resolve plate-type defaults.

    Returns:
      (dispense_volume, dispense_z, aspirate_z, secondary_z, final_secondary_z)
    """
    (
      dispense_volume,
      dispense_z,
      aspirate_z,
      secondary_z,
      final_secondary_z,
    ) = self._resolve_wash_defaults(
      dispense_volume,
      dispense_z,
      aspirate_z,
      secondary_z,
      final_secondary_z,
    )
    self._validate_wash_core_params(
      cycles,
      buffer,
      dispense_volume,
      dispense_flow_rate,
      dispense_x,
      dispense_y,
      dispense_z,
      aspirate_travel_rate,
      aspirate_z,
      pre_dispense_flow_rate,
      aspirate_delay_ms,
      aspirate_x,
      aspirate_y,
      wash_format,
      sector_mask,
    )
    self._validate_wash_final_and_extras(
      final_aspirate_z,
      final_aspirate_x,
      final_aspirate_y,
      final_aspirate_delay_ms,
      pre_dispense_volume,
      vacuum_delay_volume,
      soak_duration,
      shake_duration,
      shake_intensity,
    )
    self._validate_wash_secondary_aspirates(
      secondary_aspirate,
      secondary_z,
      secondary_x,
      secondary_y,
      final_secondary_aspirate,
      final_secondary_z,
      final_secondary_x,
      final_secondary_y,
    )
    self._validate_wash_optional_features(
      bottom_wash,
      bottom_wash_volume,
      bottom_wash_flow_rate,
      pre_dispense_between_cycles_volume,
      pre_dispense_between_cycles_flow_rate,
    )
    return (dispense_volume, dispense_z, aspirate_z, secondary_z, final_secondary_z)

  async def manifold_aspirate(
    self,
    vacuum_filtration: bool = False,
    travel_rate: str = "3",
    delay_ms: int = 0,
    vacuum_time_sec: int = 30,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int | None = None,
    secondary_aspirate: bool = False,
    secondary_x: int = 0,
    secondary_y: int = 0,
    secondary_z: int | None = None,
  ) -> None:
    """Aspirate liquid from all wells via the wash manifold.

    Two modes based on vacuum_filtration:
    - Normal (vacuum_filtration=False): Uses travel_rate and delay_ms.
    - Vacuum filtration (vacuum_filtration=True): Uses vacuum_time_sec.
      Travel rate is ignored (greyed out in GUI).

    Args:
      vacuum_filtration: Enable vacuum filtration mode.
      travel_rate: Head travel rate. Normal: "1"-"5".
        Cell wash: "1 CW", "2 CW", "3 CW", "4 CW", "6 CW".
        Ignored when vacuum_filtration=True.
      delay_ms: Post-aspirate delay in milliseconds (0-5000). Only used when
        vacuum_filtration=False.
      vacuum_time_sec: Vacuum filtration time in seconds (5-999). Only used when
        vacuum_filtration=True.
      offset_x: X offset in steps (-60 to +60).
      offset_y: Y offset in steps (-40 to +40).
      offset_z: Z offset in steps (1-210). Default None (plate-type-aware:
        29 for 96-well, 22 for 384-well, etc.).
      secondary_aspirate: Enable secondary aspirate (perform a second aspirate
        at a different position). Not available for 1536-well plates.
      secondary_x: Secondary aspirate X offset (-60 to +60).
      secondary_y: Secondary aspirate Y offset (-40 to +40).
      secondary_z: Secondary aspirate Z offset (1-210). Default None
        (plate-type-aware, same as offset_z default).

    Raises:
      ValueError: If parameters are invalid.
    """
    offset_z, secondary_z, time_value, rate_byte = self._validate_aspirate_params(
      vacuum_filtration=vacuum_filtration,
      travel_rate=travel_rate,
      delay_ms=delay_ms,
      vacuum_time_sec=vacuum_time_sec,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
      secondary_aspirate=secondary_aspirate,
      secondary_x=secondary_x,
      secondary_y=secondary_y,
      secondary_z=secondary_z,
    )

    logger.info(
      "Aspirating: vacuum=%s, travel_rate=%s, delay=%d ms",
      vacuum_filtration,
      travel_rate,
      delay_ms,
    )

    data = self._build_aspirate_command(
      vacuum_filtration=vacuum_filtration,
      time_value=time_value,
      travel_rate_byte=rate_byte,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
      secondary_mode=1 if secondary_aspirate else 0,
      secondary_x=secondary_x,
      secondary_y=secondary_y,
      secondary_z=secondary_z,
    )
    framed_command = build_framed_message(MANIFOLD_ASPIRATE_COMMAND, data)
    await self._send_step_command(framed_command)

  async def manifold_dispense(
    self,
    volume: float,
    buffer: str = "A",
    flow_rate: int = 7,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int | None = None,
    pre_dispense_volume: float = 0.0,
    pre_dispense_flow_rate: int = 9,
    vacuum_delay_volume: float = 0.0,
  ) -> None:
    """Dispense liquid to all wells via the wash manifold.

    Args:
      volume: Volume to dispense in µL/well. Range: 25-3000 µL (manifold-dependent:
        96-tube manifolds require ≥50, 192/128-tube manifolds allow ≥25).
      buffer: Buffer valve selection (A, B, C, D).
      flow_rate: Dispense flow rate (1-11, default 7).
        Rates 1-2 are for cell wash mode only (96-tube dual-action manifold)
        and require vacuum_delay_volume > 0.
        Standard range is 3-11.
      offset_x: X offset in steps (-60 to +60).
      offset_y: Y offset in steps (-40 to +40).
      offset_z: Z offset in steps (1-210). Default None (plate-type-aware:
        121 for 96-well, 120 for 384-well, etc.).
      pre_dispense_volume: Pre-dispense volume in µL/tube (0 to disable, 25-3000 when enabled).
      pre_dispense_flow_rate: Pre-dispense flow rate (3-11, default 9).
      vacuum_delay_volume: Delay start of vacuum until volume dispensed in µL/well
        (0 to disable, 0-3000 when enabled). Required for cell wash flow rates 1-2.

    Raises:
      ValueError: If parameters are invalid.
    """
    offset_z = self._validate_dispense_params(
      volume=volume,
      buffer=buffer,
      flow_rate=flow_rate,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
      pre_dispense_volume=pre_dispense_volume,
      pre_dispense_flow_rate=pre_dispense_flow_rate,
      vacuum_delay_volume=vacuum_delay_volume,
    )

    logger.info(
      "Dispensing %.1f uL from buffer %s, flow rate %d",
      volume,
      buffer,
      flow_rate,
    )

    data = self._build_dispense_command(
      volume=volume,
      buffer=buffer,
      flow_rate=flow_rate,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
      pre_dispense_volume=pre_dispense_volume,
      pre_dispense_flow_rate=pre_dispense_flow_rate,
      vacuum_delay_volume=vacuum_delay_volume,
    )
    framed_command = build_framed_message(MANIFOLD_DISPENSE_COMMAND, data)
    await self._send_step_command(framed_command)

  async def manifold_wash(
    self,
    cycles: int = 3,
    buffer: str = "A",
    dispense_volume: float | None = None,
    dispense_flow_rate: int = 7,
    dispense_x: int = 0,
    dispense_y: int = 0,
    dispense_z: int | None = None,
    aspirate_travel_rate: int = 3,
    aspirate_z: int | None = None,
    pre_dispense_flow_rate: int = 9,
    aspirate_delay_ms: int = 0,
    aspirate_x: int = 0,
    aspirate_y: int = 0,
    final_aspirate: bool = True,
    final_aspirate_z: int | None = None,
    final_aspirate_x: int = 0,
    final_aspirate_y: int = 0,
    final_aspirate_delay_ms: int = 0,
    pre_dispense_volume: float = 0.0,
    vacuum_delay_volume: float = 0.0,
    soak_duration: int = 0,
    shake_duration: int = 0,
    shake_intensity: str = "Medium",
    secondary_aspirate: bool = False,
    secondary_z: int | None = None,
    secondary_x: int = 0,
    secondary_y: int = 0,
    final_secondary_aspirate: bool = False,
    final_secondary_z: int | None = None,
    final_secondary_x: int = 0,
    final_secondary_y: int = 0,
    bottom_wash: bool = False,
    bottom_wash_volume: float = 0.0,
    bottom_wash_flow_rate: int = 5,
    pre_dispense_between_cycles_volume: float = 0.0,
    pre_dispense_between_cycles_flow_rate: int = 9,
    wash_format: Literal["Plate", "Sector"] = "Plate",
    sectors: list[int] | None = None,
    move_home_first: bool = False,
  ) -> None:
    """Perform manifold wash cycles.

    Sends a 102-byte MANIFOLD_WASH (0xA4) command that performs repeated
    dispense-aspirate cycles. The wire format contains two dispense sections,
    two aspirate sections, and a final shake/soak section.

    The wash command supports 4 independent coordinate sets:
    - Primary aspirate (aspirate_x/y/z): between-cycle aspirate position
    - Primary secondary (secondary_x/y/z): second aspirate position per cycle
    - Final aspirate (final_aspirate_x/y/z): aspirate after last cycle
    - Final secondary (final_secondary_x/y/z): second position for final aspirate

    Args:
      cycles: Number of wash cycles (1-250). Default 3.
        Encoded at header byte [6].
      buffer: Buffer valve selection (A, B, C, D). Default A.
      dispense_volume: Volume to dispense per cycle in uL. Default None
        (plate-type-aware: 300 for 96-well, 100 for others).
      dispense_flow_rate: Flow rate for dispensing (1-9). Default 7.
      dispense_x: Dispense X offset in steps (-60 to +60). Default 0.
      dispense_y: Dispense Y offset in steps (-40 to +40). Default 0.
      dispense_z: Z offset for dispense in 0.1mm units (1-210). Default None
        (plate-type-aware: 121 for 96-well, 120 for 384-well, etc.).
      aspirate_travel_rate: Travel rate for aspiration (1-9). Default 3.
      aspirate_z: Z offset for aspirate in 0.1mm units (1-210). Default None
        (plate-type-aware: 29 for 96-well, 22 for 384-well, etc.).
      pre_dispense_flow_rate: Pre-dispense flow rate (3-11). Default 9.
        Controls how fast the pre-dispense is delivered.
      aspirate_delay_ms: Post-aspirate delay in milliseconds (0-5000). Default 0.
      aspirate_x: Aspirate X offset in steps (-60 to +60). Default 0.
      aspirate_y: Aspirate Y offset in steps (-40 to +40). Default 0.
      final_aspirate: Enable final aspirate after last cycle. Default True.
        Encoded in header config flags byte [2].
      final_aspirate_z: Z offset for final aspirate (1-210). Default None
        (inherits from aspirate_z). Independent from primary aspirate Z.
      final_aspirate_x: X offset for final aspirate (-60 to +60). Default 0.
      final_aspirate_y: Y offset for final aspirate (-40 to +40). Default 0.
      final_aspirate_delay_ms: Post-aspirate delay for final aspirate in
        milliseconds (0-5000). Default 0. Encoded at Disp1[20-21] (wire
        [27-28]) as 16-bit LE.
      pre_dispense_volume: Pre-dispense volume in uL/tube (0 to disable,
        25-3000 when enabled). Default 0.0.
      vacuum_delay_volume: Vacuum delay volume in uL/well (0 to disable,
        0-3000 when enabled). Cell wash operations only. Default 0.0.
      soak_duration: Soak duration in seconds (0 to disable, 0-3599). Default 0.
      shake_duration: Shake duration in seconds (0 to disable, 0-3599). Default 0.
      shake_intensity: Shake intensity ("Variable", "Slow", "Medium", "Fast").
        Default "Medium".
      secondary_aspirate: Enable secondary aspirate for primary (between-cycle)
        aspirate. Default False.
      secondary_z: Z offset for secondary aspirate in 0.1mm units (1-210).
        Default None (plate-type-aware, same as aspirate_z default).
      secondary_x: Secondary aspirate X offset (-60 to +60). Default 0.
      secondary_y: Secondary aspirate Y offset (-40 to +40). Default 0.
      final_secondary_aspirate: Enable secondary aspirate for final aspirate.
        Default False.
      final_secondary_z: Z offset for final secondary aspirate (1-210).
        Default None (plate-type-aware, same as aspirate_z default).
      final_secondary_x: X offset for final secondary aspirate (-60 to +60).
        Default 0.
      final_secondary_y: Y offset for final secondary aspirate (-40 to +40).
        Default 0.
      bottom_wash: Enable bottom wash. Default False. Encoded in header[1].
      bottom_wash_volume: Bottom wash volume in uL (25-3000). Default 0.0.
      bottom_wash_flow_rate: Bottom wash flow rate (3-11). Default 5.
      pre_dispense_between_cycles_volume: Pre-dispense volume between wash
        cycles in uL (0 to disable, 25-3000 when enabled). Default 0.0.
      pre_dispense_between_cycles_flow_rate: Flow rate for pre-dispense between
        cycles (3-11). Default 9.
      wash_format: Wash format ("Plate" or "Sector"). Default "Plate".
        Encoded at header[3]: Plate=0x00, Sector=0x01.
        384-well plates typically use "Sector" for quadrant-based washing.
      sectors: List of quadrant numbers to wash (1-4). Default None (all 4).
        Example: ``sectors=[1, 2]`` washes quadrants 1 and 2.
        Only used when wash_format="Sector".
      move_home_first: Move carrier to home position before shake/soak.
        Default False. Same as in standalone shake interface.
        Encoded at wire [87] (shake/soak section byte 0).

    Raises:
      ValueError: If parameters are invalid.
    """
    # Convert sectors list to bitmask
    if sectors is not None:
      sector_mask = 0
      for q in sectors:
        if not 1 <= q <= 4:
          raise ValueError(f"Sector/quadrant must be 1-4, got {q}")
        sector_mask |= 1 << (q - 1)
    else:
      sector_mask = 0x0F

    (
      dispense_volume,
      dispense_z,
      aspirate_z,
      secondary_z,
      final_secondary_z,
    ) = self._validate_wash_params(
      cycles=cycles,
      buffer=buffer,
      dispense_volume=dispense_volume,
      dispense_flow_rate=dispense_flow_rate,
      dispense_x=dispense_x,
      dispense_y=dispense_y,
      dispense_z=dispense_z,
      aspirate_travel_rate=aspirate_travel_rate,
      aspirate_z=aspirate_z,
      pre_dispense_flow_rate=pre_dispense_flow_rate,
      aspirate_delay_ms=aspirate_delay_ms,
      aspirate_x=aspirate_x,
      aspirate_y=aspirate_y,
      final_aspirate_z=final_aspirate_z,
      final_aspirate_x=final_aspirate_x,
      final_aspirate_y=final_aspirate_y,
      final_aspirate_delay_ms=final_aspirate_delay_ms,
      pre_dispense_volume=pre_dispense_volume,
      vacuum_delay_volume=vacuum_delay_volume,
      soak_duration=soak_duration,
      shake_duration=shake_duration,
      shake_intensity=shake_intensity,
      secondary_aspirate=secondary_aspirate,
      secondary_z=secondary_z,
      secondary_x=secondary_x,
      secondary_y=secondary_y,
      final_secondary_aspirate=final_secondary_aspirate,
      final_secondary_z=final_secondary_z,
      final_secondary_x=final_secondary_x,
      final_secondary_y=final_secondary_y,
      bottom_wash=bottom_wash,
      bottom_wash_volume=bottom_wash_volume,
      bottom_wash_flow_rate=bottom_wash_flow_rate,
      pre_dispense_between_cycles_volume=pre_dispense_between_cycles_volume,
      pre_dispense_between_cycles_flow_rate=pre_dispense_between_cycles_flow_rate,
      wash_format=wash_format,
      sector_mask=sector_mask,
    )

    logger.info(
      "Manifold wash: %d cycles, %.1f uL, buffer %s, flow %d, "
      "disp_xy=(%d,%d), z_disp=%d, z_asp=%d, pre_disp_flow=%d, "
      "asp_delay=%d, asp_xy=(%d,%d), final_asp=%s, "
      "pre_disp=%.1f, vac_delay=%.1f, soak=%d, shake=%d/%s, "
      "sec_asp=%s, sec_z=%d, sec_xy=(%d,%d), "
      "btm_wash=%s/%.1f/%d, midcyc=%.1f/%d",
      cycles,
      dispense_volume,
      buffer,
      dispense_flow_rate,
      dispense_x,
      dispense_y,
      dispense_z,
      aspirate_z,
      pre_dispense_flow_rate,
      aspirate_delay_ms,
      aspirate_x,
      aspirate_y,
      final_aspirate,
      pre_dispense_volume,
      vacuum_delay_volume,
      soak_duration,
      shake_duration,
      shake_intensity,
      secondary_aspirate,
      secondary_z,
      secondary_x,
      secondary_y,
      bottom_wash,
      bottom_wash_volume,
      bottom_wash_flow_rate,
      pre_dispense_between_cycles_volume,
      pre_dispense_between_cycles_flow_rate,
    )

    data = self._build_wash_composite_command(
      cycles=cycles,
      buffer=buffer,
      dispense_volume=dispense_volume,
      dispense_flow_rate=dispense_flow_rate,
      dispense_x=dispense_x,
      dispense_y=dispense_y,
      dispense_z=dispense_z,
      aspirate_travel_rate=aspirate_travel_rate,
      aspirate_z=aspirate_z,
      pre_dispense_flow_rate=pre_dispense_flow_rate,
      aspirate_delay_ms=aspirate_delay_ms,
      aspirate_x=aspirate_x,
      aspirate_y=aspirate_y,
      final_aspirate=final_aspirate,
      final_aspirate_z=final_aspirate_z,
      final_aspirate_x=final_aspirate_x,
      final_aspirate_y=final_aspirate_y,
      final_aspirate_delay_ms=final_aspirate_delay_ms,
      pre_dispense_volume=pre_dispense_volume,
      vacuum_delay_volume=vacuum_delay_volume,
      soak_duration=soak_duration,
      shake_duration=shake_duration,
      shake_intensity=shake_intensity,
      secondary_aspirate=secondary_aspirate,
      secondary_z=secondary_z,
      secondary_x=secondary_x,
      secondary_y=secondary_y,
      final_secondary_aspirate=final_secondary_aspirate,
      final_secondary_z=final_secondary_z,
      final_secondary_x=final_secondary_x,
      final_secondary_y=final_secondary_y,
      bottom_wash=bottom_wash,
      bottom_wash_volume=bottom_wash_volume,
      bottom_wash_flow_rate=bottom_wash_flow_rate,
      pre_dispense_between_cycles_volume=pre_dispense_between_cycles_volume,
      pre_dispense_between_cycles_flow_rate=pre_dispense_between_cycles_flow_rate,
      wash_format=wash_format,
      sector_mask=sector_mask,
      move_home_first=move_home_first,
    )

    framed_command = build_framed_message(MANIFOLD_WASH_COMMAND, data)
    # Dynamic timeout: base per cycle + shake + soak + buffer
    # Each cycle takes ~10-30s depending on volume/flow/plate type.
    # Use 60s per cycle as generous safety margin to avoid false timeouts.
    wash_timeout = (cycles * 60) + shake_duration + soak_duration + 120
    await self._send_step_command(framed_command, timeout=wash_timeout)

  async def manifold_prime(
    self,
    volume: float,
    buffer: str = "A",
    flow_rate: int = 9,
    low_flow_volume: int = 5,
    submerge_duration: int = 0,
  ) -> None:
    """Prime the manifold fluid lines.

    Fills the wash manifold tubing with liquid from the specified buffer.
    This is typically done at the start of a protocol to ensure the lines
    are filled and ready for dispensing.

    Args:
      volume: Prime volume in mL (not uL!). Range: 5-999 mL.
      buffer: Buffer valve selection (A, B, C, D).
      flow_rate: Flow rate (3-11, default 9).
      low_flow_volume: Low flow path volume in mL (5-999, default 5). Set to 0 to disable.
      submerge_duration: Submerge duration in minutes (0 to disable, 1-1439 when enabled).
        Limit: 00:01-23:59. Wire encoding is total minutes.

    Raises:
      ValueError: If parameters are invalid.
    """
    # Parameter limits
    if not 5 <= volume <= 999:
      raise ValueError(f"Washer prime volume must be 5-999 mL, got {volume}")
    validate_buffer(buffer)
    if not 3 <= flow_rate <= 11:
      raise ValueError(f"Washer prime flow rate must be 3-11, got {flow_rate}")
    if low_flow_volume != 0 and not 5 <= low_flow_volume <= 999:
      raise ValueError(
        f"Low flow path volume must be 0 (disabled) or 5-999 mL, got {low_flow_volume}"
      )
    if submerge_duration != 0 and not 1 <= submerge_duration <= 1439:
      raise ValueError(
        f"Submerge duration must be 0 (disabled) or 1-1439 minutes (00:01-23:59), "
        f"got {submerge_duration}"
      )

    low_flow_enabled = low_flow_volume > 0
    submerge_enabled = submerge_duration > 0

    logger.info(
      "Manifold prime: %.1f mL from buffer %s, flow rate %d, low_flow=%s/%d mL, submerge=%s/%d min",
      volume,
      buffer,
      flow_rate,
      "enabled" if low_flow_enabled else "disabled",
      low_flow_volume,
      "enabled" if submerge_enabled else "disabled",
      submerge_duration,
    )

    data = self._build_manifold_prime_command(
      buffer=buffer,
      volume=volume,
      flow_rate=flow_rate,
      low_flow_volume=low_flow_volume,
      low_flow_enabled=low_flow_enabled,
      submerge_enabled=submerge_enabled,
      submerge_duration=submerge_duration,
    )
    framed_command = build_framed_message(MANIFOLD_PRIME_COMMAND, data)
    # Timeout: base time for priming + submerge duration (in minutes) + buffer
    prime_timeout = self.timeout + (submerge_duration * 60) + 30
    await self._send_step_command(framed_command, timeout=prime_timeout)

  async def manifold_auto_clean(
    self,
    buffer: str = "A",
    duration: int = 1,
  ) -> None:
    """Run a manifold auto-clean cycle.

    Args:
      buffer: Buffer valve to use (A, B, C, or D).
      duration: Cleaning duration in minutes (1-239, i.e. up to 3h59m).

    Raises:
      ValueError: If parameters are invalid.
    """
    validate_buffer(buffer)
    # AutoClean Duration must be 00:01..03:59
    # 03:59 = 3*60 + 59 = 239 minutes
    if not 1 <= duration <= 239:
      raise ValueError(f"AutoClean duration must be 1-239 minutes (00:01-03:59), got {duration}")

    logger.info("Auto-clean: buffer %s, duration %d minutes", buffer, duration)

    data = self._build_auto_clean_command(
      buffer=buffer,
      duration=duration,
    )
    framed_command = build_framed_message(MANIFOLD_AUTO_CLEAN_COMMAND, data)
    # Auto-clean can take 1+ minutes, use longer timeout (duration is in minutes on wire)
    auto_clean_timeout = max(120.0, duration * 60.0 + 30.0)  # At least 2 min, or duration + 30s
    await self._send_step_command(framed_command, timeout=auto_clean_timeout)

  # =========================================================================
  # COMMAND BUILDERS
  # =========================================================================

  def _encode_wash_byte_values(
    self,
    buffer: str,
    dispense_volume: float | None,
    dispense_z: int | None,
    aspirate_z: int | None,
    dispense_x: int,
    dispense_y: int,
    aspirate_x: int,
    aspirate_y: int,
    final_aspirate_z: int | None,
    final_aspirate_x: int,
    final_aspirate_y: int,
    secondary_aspirate: bool,
    secondary_x: int,
    secondary_y: int,
    secondary_z: int | None,
    final_secondary_aspirate: bool,
    final_secondary_x: int,
    final_secondary_y: int,
    final_secondary_z: int | None,
    pre_dispense_volume: float,
    pre_dispense_flow_rate: int,
    vacuum_delay_volume: float,
    aspirate_delay_ms: int,
    final_aspirate_delay_ms: int,
    shake_duration: int,
    shake_intensity: str,
    soak_duration: int,
    dispense_flow_rate: int,
    bottom_wash: bool,
    bottom_wash_volume: float,
    bottom_wash_flow_rate: int,
    pre_dispense_between_cycles_volume: float,
    pre_dispense_between_cycles_flow_rate: int,
  ) -> dict[str, int]:
    """Pre-compute all byte-level wire values for a wash command.

    Returns a dict of named byte values used by the section builders in
    ``_build_wash_composite_command``.
    """
    (
      dispense_volume,
      dispense_z,
      aspirate_z,
      secondary_z,
      final_secondary_z,
    ) = self._resolve_wash_defaults(
      dispense_volume,
      dispense_z,
      aspirate_z,
      secondary_z,
      final_secondary_z,
    )

    vol_low, vol_high = encode_volume_16bit(dispense_volume)

    final_asp_z = final_aspirate_z if final_aspirate_z is not None else aspirate_z

    # Pre-dispense volume (16-bit LE, 0 = disabled)
    pre_disp_int = int(pre_dispense_volume) if pre_dispense_volume > 0 else 0
    # Vacuum delay volume (16-bit LE, 0 = disabled)
    vac_delay_int = int(vacuum_delay_volume) if vacuum_delay_volume > 0 else 0

    # Primary secondary aspirate
    sec_mode_byte = 0x01 if secondary_aspirate else 0x00
    sec_x_byte = encode_signed_byte(secondary_x) if secondary_aspirate else 0x00
    sec_y_byte = encode_signed_byte(secondary_y) if secondary_aspirate else 0x00

    # Final secondary aspirate
    final_sec_mode_byte = 0x01 if final_secondary_aspirate else 0x00
    final_sec_x_byte = encode_signed_byte(final_secondary_x) if final_secondary_aspirate else 0x00
    final_sec_y_byte = encode_signed_byte(final_secondary_y) if final_secondary_aspirate else 0x00
    final_sec_z = final_secondary_z if final_secondary_aspirate else final_asp_z

    # Shake intensity byte (only encode when shake is actually enabled)
    intensity_byte = INTENSITY_TO_BYTE.get(shake_intensity, 0x03) if shake_duration > 0 else 0x00

    # Bottom wash: when enabled, Dispense1 gets bottom wash params
    if bottom_wash:
      bw_vol = int(bottom_wash_volume)
      bw_vol_low = bw_vol & 0xFF
      bw_vol_high = (bw_vol >> 8) & 0xFF
      bw_flow = bottom_wash_flow_rate
    else:
      bw_vol_low = vol_low
      bw_vol_high = vol_high
      bw_flow = dispense_flow_rate

    # Pre-dispense between cycles
    if pre_dispense_between_cycles_volume > 0:
      midcyc_vol = int(pre_dispense_between_cycles_volume)
      midcyc_vol_low = midcyc_vol & 0xFF
      midcyc_vol_high = (midcyc_vol >> 8) & 0xFF
      midcyc_flow = pre_dispense_between_cycles_flow_rate
    else:
      midcyc_vol_low = pre_disp_int & 0xFF
      midcyc_vol_high = (pre_disp_int >> 8) & 0xFF
      midcyc_flow = pre_dispense_flow_rate

    return {
      "buffer_char": ord(buffer.upper()),
      "vol_low": vol_low,
      "vol_high": vol_high,
      "disp_z_low": dispense_z & 0xFF,
      "disp_z_high": (dispense_z >> 8) & 0xFF,
      "asp_z_low": aspirate_z & 0xFF,
      "asp_z_high": (aspirate_z >> 8) & 0xFF,
      "disp_x_byte": encode_signed_byte(dispense_x),
      "disp_y_byte": encode_signed_byte(dispense_y),
      "asp_x_byte": encode_signed_byte(aspirate_x),
      "asp_y_byte": encode_signed_byte(aspirate_y),
      "final_asp_z_low": final_asp_z & 0xFF,
      "final_asp_z_high": (final_asp_z >> 8) & 0xFF,
      "final_asp_x_byte": encode_signed_byte(final_aspirate_x),
      "final_asp_y_byte": encode_signed_byte(final_aspirate_y),
      "sec_mode_byte": sec_mode_byte,
      "sec_x_byte": sec_x_byte,
      "sec_y_byte": sec_y_byte,
      "sec_z_low": secondary_z & 0xFF,
      "sec_z_high": (secondary_z >> 8) & 0xFF,
      "final_sec_mode_byte": final_sec_mode_byte,
      "final_sec_x_byte": final_sec_x_byte,
      "final_sec_y_byte": final_sec_y_byte,
      "final_sec_z_low": final_sec_z & 0xFF,
      "final_sec_z_high": (final_sec_z >> 8) & 0xFF,
      "pre_disp_low": pre_disp_int & 0xFF,
      "pre_disp_high": (pre_disp_int >> 8) & 0xFF,
      "vac_delay_low": vac_delay_int & 0xFF,
      "vac_delay_high": (vac_delay_int >> 8) & 0xFF,
      "asp_delay_low": aspirate_delay_ms & 0xFF,
      "asp_delay_high": (aspirate_delay_ms >> 8) & 0xFF,
      "final_asp_delay_low": final_aspirate_delay_ms & 0xFF,
      "final_asp_delay_high": (final_aspirate_delay_ms >> 8) & 0xFF,
      "shake_dur_low": shake_duration & 0xFF,
      "shake_dur_high": (shake_duration >> 8) & 0xFF,
      "soak_dur_low": soak_duration & 0xFF,
      "soak_dur_high": (soak_duration >> 8) & 0xFF,
      "intensity_byte": intensity_byte,
      "bw_vol_low": bw_vol_low,
      "bw_vol_high": bw_vol_high,
      "bw_flow": bw_flow,
      "midcyc_vol_low": midcyc_vol_low,
      "midcyc_vol_high": midcyc_vol_high,
      "midcyc_flow": midcyc_flow,
    }

  def _build_wash_composite_command(
    self,
    cycles: int = 3,
    buffer: str = "A",
    dispense_volume: float | None = None,
    dispense_flow_rate: int = 7,
    dispense_x: int = 0,
    dispense_y: int = 0,
    dispense_z: int | None = None,
    aspirate_travel_rate: int = 3,
    aspirate_z: int | None = None,
    pre_dispense_flow_rate: int = 9,
    aspirate_delay_ms: int = 0,
    aspirate_x: int = 0,
    aspirate_y: int = 0,
    final_aspirate: bool = True,
    final_aspirate_z: int | None = None,
    final_aspirate_x: int = 0,
    final_aspirate_y: int = 0,
    final_aspirate_delay_ms: int = 0,
    pre_dispense_volume: float = 0.0,
    vacuum_delay_volume: float = 0.0,
    soak_duration: int = 0,
    shake_duration: int = 0,
    shake_intensity: str = "Medium",
    secondary_aspirate: bool = False,
    secondary_z: int | None = None,
    secondary_x: int = 0,
    secondary_y: int = 0,
    final_secondary_aspirate: bool = False,
    final_secondary_z: int | None = None,
    final_secondary_x: int = 0,
    final_secondary_y: int = 0,
    bottom_wash: bool = False,
    bottom_wash_volume: float = 0.0,
    bottom_wash_flow_rate: int = 5,
    pre_dispense_between_cycles_volume: float = 0.0,
    pre_dispense_between_cycles_flow_rate: int = 9,
    wash_format: Literal["Plate", "Sector"] = "Plate",
    sector_mask: int = 0x0F,
    move_home_first: bool = False,
  ) -> bytes:
    """Build 102-byte MANIFOLD_WASH (0xA4) command payload.

    Structure: header(7) + dispense1(22) + final_aspirate(20) + primary_aspirate(19)
               + dispense2(22) + shake_soak(12) = 102 bytes.

    Header [0-6]:
      [0] plate_type (from self.plate_type.value)
      [1] bottom_wash enable
      [2] config flags -- final_aspirate
      [3] wash_format -- 0=Plate, 1=Sector
      [4-5] sector_mask as 16-bit LE
      [6] wash cycles count

    Four coordinate sets for aspirate positions:
    - Primary: aspirate_x/y/z (between-cycle aspirate, wire [49-67])
    - Primary secondary: secondary_x/y/z (wire [55-61])
    - Final: final_aspirate_x/y/z (post-cycle aspirate, wire [29-48])
    - Final secondary: final_secondary_x/y/z (wire [37-41])

    Returns:
      102-byte command payload.
    """
    v = self._encode_wash_byte_values(
      buffer=buffer,
      dispense_volume=dispense_volume,
      dispense_z=dispense_z,
      aspirate_z=aspirate_z,
      dispense_x=dispense_x,
      dispense_y=dispense_y,
      aspirate_x=aspirate_x,
      aspirate_y=aspirate_y,
      final_aspirate_z=final_aspirate_z,
      final_aspirate_x=final_aspirate_x,
      final_aspirate_y=final_aspirate_y,
      secondary_aspirate=secondary_aspirate,
      secondary_x=secondary_x,
      secondary_y=secondary_y,
      secondary_z=secondary_z,
      final_secondary_aspirate=final_secondary_aspirate,
      final_secondary_x=final_secondary_x,
      final_secondary_y=final_secondary_y,
      final_secondary_z=final_secondary_z,
      pre_dispense_volume=pre_dispense_volume,
      pre_dispense_flow_rate=pre_dispense_flow_rate,
      vacuum_delay_volume=vacuum_delay_volume,
      aspirate_delay_ms=aspirate_delay_ms,
      final_aspirate_delay_ms=final_aspirate_delay_ms,
      shake_duration=shake_duration,
      shake_intensity=shake_intensity,
      soak_duration=soak_duration,
      dispense_flow_rate=dispense_flow_rate,
      bottom_wash=bottom_wash,
      bottom_wash_volume=bottom_wash_volume,
      bottom_wash_flow_rate=bottom_wash_flow_rate,
      pre_dispense_between_cycles_volume=pre_dispense_between_cycles_volume,
      pre_dispense_between_cycles_flow_rate=pre_dispense_between_cycles_flow_rate,
    )

    # Header [0-6] (7 bytes)
    config_flags = 0x01 if final_aspirate else 0x00
    bw_flag = 0x01 if bottom_wash else 0x00
    wash_format_byte = {"Plate": 0x00, "Sector": 0x01}[wash_format]
    header = bytes(
      [
        self.plate_type.value,  # [0] Plate type
        bw_flag,  # [1] Bottom wash enable
        config_flags,  # [2] Config flags
        wash_format_byte,  # [3] Wash format: 0=Plate, 1=Sector
        sector_mask & 0xFF,  # [4] Sector mask low byte
        (sector_mask >> 8) & 0xFF,  # [5] Sector mask high byte
        cycles,  # [6] Number of wash cycles
      ]
    )

    # Dispense section 1 [7-28] (22 bytes) — bottom wash or mirror of main
    disp1 = bytes(
      [
        v["buffer_char"],  # [0] Buffer ASCII
        v["bw_vol_low"],
        v["bw_vol_high"],  # [1-2] Volume LE
        v["bw_flow"],  # [3] Flow rate
        v["disp_x_byte"],
        v["disp_y_byte"],  # [4-5] Offset X, Y (signed)
        v["disp_z_low"],
        v["disp_z_high"],  # [6-7] Dispense Z LE
        v["pre_disp_low"],
        v["pre_disp_high"],  # [8-9] Pre-dispense volume LE
        pre_dispense_flow_rate,  # [10] Pre-dispense flow rate
        v["vac_delay_low"],
        v["vac_delay_high"],  # [11-12] Vacuum delay volume LE
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,  # [13-19] Padding (7 bytes)
        v["final_asp_delay_low"],
        v["final_asp_delay_high"],  # [20-21] Final aspirate delay ms LE
      ]
    )

    # Final aspirate section [29-48] (20 bytes)
    asp1 = bytes(
      [
        aspirate_travel_rate,  # [0] Travel rate (propagated)
        0x00,
        0x00,  # [1-2] Delay ms LE (always 0 here)
        v["final_asp_z_low"],
        v["final_asp_z_high"],  # [3-4] Final aspirate Z LE
        v["final_sec_mode_byte"],  # [5] Final secondary mode
        v["final_asp_x_byte"],  # [6] Final aspirate X offset
        v["final_asp_y_byte"],  # [7] Final aspirate Y offset
        v["final_sec_z_low"],
        v["final_sec_z_high"],  # [8-9] Final secondary Z LE
        0x00,  # [10] Reserved
        v["final_sec_x_byte"],  # [11] Final secondary X
        v["final_sec_y_byte"],  # [12] Final secondary Y
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,  # [13-17] Reserved (5 bytes)
        0x00,  # [18] vac_filt (always 0 in wash)
        v["asp_delay_low"],  # [19] aspirate_delay_ms low byte
      ]
    )

    # Primary aspirate section [49-67] (19 bytes)
    asp2 = bytes(
      [
        v["asp_delay_high"],  # [0] aspirate_delay_ms high byte
        aspirate_travel_rate,  # [1] Travel rate
        v["asp_x_byte"],  # [2] Aspirate X offset
        v["asp_y_byte"],  # [3] Aspirate Y offset
        v["asp_z_low"],
        v["asp_z_high"],  # [4-5] Aspirate Z LE
        v["sec_mode_byte"],  # [6] Secondary aspirate mode (0=off, 1=on)
        v["sec_x_byte"],  # [7] Secondary X offset
        v["sec_y_byte"],  # [8] Secondary Y offset
        v["sec_z_low"],
        v["sec_z_high"],  # [9-10] Secondary Z LE
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,  # [11-18] Reserved
      ]
    )

    # Dispense section 2 [68-86] (19 bytes) -- main dispense
    disp2 = bytes(
      [
        v["buffer_char"],  # [0] Buffer ASCII
        v["vol_low"],
        v["vol_high"],  # [1-2] Volume LE (main dispense)
        dispense_flow_rate,  # [3] Flow rate (main)
        v["disp_x_byte"],
        v["disp_y_byte"],  # [4-5] Offset X, Y (signed)
        v["disp_z_low"],
        v["disp_z_high"],  # [6-7] Dispense Z LE
        v["midcyc_vol_low"],
        v["midcyc_vol_high"],  # [8-9] Pre-disp between cycles vol LE
        v["midcyc_flow"],  # [10] Pre-disp between cycles flow rate
        v["vac_delay_low"],
        v["vac_delay_high"],  # [11-12] Vacuum delay volume LE
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,  # [13-18] Padding (6 bytes)
      ]
    )

    # Shake/soak section [87-101] (15 bytes = 11 + 4 trailing)
    move_home_byte = 0x01 if move_home_first else 0x00
    shake_soak = bytes(
      [
        move_home_byte,  # [0] move_home_first
        v["shake_dur_low"],
        v["shake_dur_high"],  # [1-2] shake duration LE (seconds)
        v["intensity_byte"] if shake_duration > 0 else 0x03,
        # [3] shake intensity (default 3=Medium)
        0x00,  # [4] shake type (always 0)
        v["soak_dur_low"],
        v["soak_dur_high"],  # [5-6] soak duration LE (seconds)
        0x00,
        0x00,
        0x00,
        0x00,  # [7-10] padding
        0x00,
        0x00,
        0x00,
        0x00,  # trailing padding (4 bytes)
      ]
    )

    data = header + disp1 + asp1 + asp2 + disp2 + shake_soak

    assert len(data) == 102, f"Wash command should be 102 bytes, got {len(data)}"

    logger.debug("Wash command data (%d bytes): %s", len(data), data.hex())
    return data

  def _build_aspirate_command(
    self,
    vacuum_filtration: bool = False,
    time_value: int = 0,
    travel_rate_byte: int = 3,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int = 30,
    secondary_mode: int = 0,
    secondary_x: int = 0,
    secondary_y: int = 0,
    secondary_z: int = 30,
  ) -> bytes:
    """Build aspirate command bytes.

    Wire format (22 bytes):
      [0]     Plate type (EL406PlateType enum value, e.g. 0x04=96-well)
      [1]     vacuum_filtration: 0 or 1
      [2-3]   time_value: ushort LE. delay_ms when normal, vacuum_time_sec when vacuum.
      [4]     travel_rate: byte from lookup table
      [5]     x_offset: signed byte
      [6]     y_offset: signed byte
      [7-8]   z_offset: short LE
      [9]     secondary_mode: byte (0=None, 1=enabled)
      [10]    secondary_x: signed byte
      [11]    secondary_y: signed byte
      [12-13] secondary_z: short LE
      [14-15] reserved: 0x0000
      [16-17] column mask: 2 bytes (all columns selected: 0xFF 0x0F)
      [18-21] padding: 4 bytes 0x00

    Args:
      vacuum_filtration: Enable vacuum filtration.
      time_value: Delay in ms (normal mode) or time in seconds (vacuum mode).
      travel_rate_byte: Pre-encoded travel rate byte value.
      offset_x: X offset (signed byte).
      offset_y: Y offset (signed byte).
      offset_z: Z offset (unsigned short).
      secondary_mode: Secondary aspirate mode byte (0=None, 1=enabled).
      secondary_x: Secondary X offset (signed byte).
      secondary_y: Secondary Y offset (signed byte).
      secondary_z: Secondary Z offset (unsigned short).

    Returns:
      Command bytes (22 bytes).
    """
    # Column mask: always all columns selected for manifold aspirate
    column_mask_bytes = bytes([0xFF, 0x0F])

    return (
      bytes(
        [
          self.plate_type.value,  # [0]  plate type prefix
          1 if vacuum_filtration else 0,  # [1]  vacuum_filtration
          time_value & 0xFF,  # [2]  time/delay low
          (time_value >> 8) & 0xFF,  # [3]  time/delay high
          travel_rate_byte & 0xFF,  # [4]  travel rate
          encode_signed_byte(offset_x),  # [5]  x offset
          encode_signed_byte(offset_y),  # [6]  y offset
          offset_z & 0xFF,  # [7]  z offset low
          (offset_z >> 8) & 0xFF,  # [8]  z offset high
          secondary_mode & 0xFF,  # [9]  secondary mode
          encode_signed_byte(secondary_x),  # [10] secondary x
          encode_signed_byte(secondary_y),  # [11] secondary y
          secondary_z & 0xFF,  # [12] secondary z low
          (secondary_z >> 8) & 0xFF,  # [13] secondary z high
          0,
          0,  # [14-15] reserved
        ]
      )
      + column_mask_bytes
      + bytes([0, 0, 0, 0])
    )  # [16-17] column mask + [18-21] padding

  def _build_dispense_command(
    self,
    volume: float,
    buffer: str,
    flow_rate: int,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int = 121,
    pre_dispense_volume: float = 0.0,
    pre_dispense_flow_rate: int = 9,
    vacuum_delay_volume: float = 0.0,
  ) -> bytes:
    """Build manifold dispense command bytes.

    Protocol format for manifold dispense:
    Wire format: 20 bytes (19 + plate type prefix)

      [0]      Plate type (EL406PlateType enum value, e.g. 0x04=96-well)
      [1]      Buffer letter: A=0x41, B=0x42, C=0x43, D=0x44 (ASCII char)
      [2-3]    Volume: 2 bytes, LE, in µL (25-3000)
      [4]      Flow rate: 1-11 (1-2 = cell wash, requires vacuum delay)
      [5]      Offset X: signed byte (-60..60)
      [6]      Offset Y: signed byte (-40..40)
      [7-8]    Offset Z: 2 bytes, LE (1-210)
      [9-10]   Pre-dispense volume: 2 bytes, LE (0 if disabled, 25-3000 when enabled)
      [11]     Pre-dispense flow rate: 3-11
      [12-13]  Vacuum delay volume: 2 bytes, LE (0 if disabled, 0-3000)
      [14-19]  Padding: 6 bytes (0x00)

    Note: Pre-dispense is enabled when pre_dispense_volume > 0.
          Vacuum delay is enabled when vacuum_delay_volume > 0.

    Args:
      volume: Dispense volume in µL.
      buffer: Buffer valve (A, B, C, D).
      flow_rate: Flow rate (1-11; 1-2 = cell wash, requires vacuum delay).
      offset_x: X offset (signed, steps, -60..60).
      offset_y: Y offset (signed, steps, -40..40).
      offset_z: Z offset (steps, 1-210).
      pre_dispense_volume: Pre-dispense volume in µL (0 to disable).
      pre_dispense_flow_rate: Pre-dispense flow rate (3-11).
      vacuum_delay_volume: Vacuum delay volume in µL (0 to disable).

    Returns:
      Command bytes (20 bytes).
    """
    vol_low, vol_high = encode_volume_16bit(volume)
    z_low = offset_z & 0xFF
    z_high = (offset_z >> 8) & 0xFF

    # Pre-dispense volume (enabled when > 0)
    pre_disp_vol_int = int(pre_dispense_volume) if pre_dispense_volume > 0 else 0
    pre_disp_low = pre_disp_vol_int & 0xFF
    pre_disp_high = (pre_disp_vol_int >> 8) & 0xFF

    # Vacuum delay volume (enabled when > 0)
    vac_delay_int = int(vacuum_delay_volume) if vacuum_delay_volume > 0 else 0
    vac_delay_low = vac_delay_int & 0xFF
    vac_delay_high = (vac_delay_int >> 8) & 0xFF

    return bytes(
      [
        self.plate_type.value,  # [0] Plate type prefix
        ord(buffer.upper()),  # [1] Buffer as ASCII char
        vol_low,  # [2] Volume low
        vol_high,  # [3] Volume high
        flow_rate,  # [4] Flow rate
        encode_signed_byte(offset_x),  # [5] X offset
        encode_signed_byte(offset_y),  # [6] Y offset
        z_low,  # [7] Z offset low
        z_high,  # [8] Z offset high
        pre_disp_low,  # [9] Pre-dispense volume low
        pre_disp_high,  # [10] Pre-dispense volume high
        pre_dispense_flow_rate,  # [11] Pre-dispense flow rate
        vac_delay_low,  # [12] Vacuum delay volume low
        vac_delay_high,  # [13] Vacuum delay volume high
        0,
        0,
        0,
        0,
        0,
        0,  # [14-19] Padding (6 bytes)
      ]
    )

  def _build_manifold_prime_command(
    self,
    buffer: str,
    volume: float,
    flow_rate: int = 9,
    low_flow_volume: int = 5,
    low_flow_enabled: bool = True,
    submerge_enabled: bool = False,
    submerge_duration: int = 0,
  ) -> bytes:
    """Build manifold prime command bytes.

    Protocol format for manifold prime (13 bytes):

      [0]   Plate type (EL406PlateType enum value, e.g. 0x04=96-well)
      [1]   Buffer letter: A=0x41, B=0x42, C=0x43, D=0x44 (ASCII char)
      [2-3] Volume: 2 bytes, little-endian, in mL
      [4]   Flow rate: 3-11
      [5-6] Low flow volume: 2 bytes, little-endian (in mL, 0 if disabled)
      [7-8] Submerge duration: 2 bytes, little-endian (in minutes, 0 if disabled)
            HH:MM encoded as total minutes: hours*60+minutes
      [9-12] Padding zeros: 4 bytes

    Args:
      buffer: Buffer valve (A, B, C, D).
      volume: Prime volume in mL (not uL!).
      flow_rate: Flow rate (3-11, default 9).
      low_flow_volume: Low flow volume in mL (default 5).
      low_flow_enabled: Enable low flow path (default True).
      submerge_enabled: Enable submerge tips after prime (default False).
      submerge_duration: Submerge duration in minutes (default 0).

    Returns:
      Command bytes (13 bytes).
    """
    vol_low, vol_high = encode_volume_16bit(volume)

    # Low flow volume: 16-bit LE, but only if enabled
    if low_flow_enabled and low_flow_volume > 0:
      lf_low = low_flow_volume & 0xFF
      lf_high = (low_flow_volume >> 8) & 0xFF
    else:
      lf_low = 0
      lf_high = 0

    # Submerge duration: 16-bit LE in minutes, only if enabled
    if submerge_enabled:
      sub_low = submerge_duration & 0xFF
      sub_high = (submerge_duration >> 8) & 0xFF
    else:
      sub_low = 0
      sub_high = 0

    return bytes(
      [
        self.plate_type.value,  # Plate type prefix
        ord(buffer.upper()),  # Buffer as ASCII char
        vol_low,
        vol_high,
        flow_rate,
        lf_low,  # Low flow volume low byte
        lf_high,  # Low flow volume high byte
        sub_low,  # Submerge duration low byte (minutes)
        sub_high,  # Submerge duration high byte (minutes)
        0,
        0,
        0,
        0,  # Padding (4 bytes)
      ]
    )

  def _build_auto_clean_command(
    self,
    buffer: str,
    duration: int = 1,
  ) -> bytes:
    """Build auto-clean command bytes.

    Protocol format for auto-clean (8 bytes):

      [0]   Plate type (EL406PlateType enum value, e.g. 0x04=96-well)
      [1]   Buffer letter: A=0x41, B=0x42, C=0x43, D=0x44 (ASCII char)
      [2-3] Duration: 2 bytes, little-endian (in minutes)
      [4-7] Padding zeros: 4 bytes

    Args:
      buffer: Buffer valve (A, B, C, D).
      duration: Cleaning duration in minutes (1-239).

    Returns:
      Command bytes (8 bytes).
    """
    duration_int = int(duration) & 0xFFFF
    duration_low = duration_int & 0xFF
    duration_high = (duration_int >> 8) & 0xFF

    return bytes(
      [
        self.plate_type.value,  # Plate type prefix
        ord(buffer.upper()),  # Buffer as ASCII char
        duration_low,
        duration_high,
        0,
        0,
        0,
        0,  # Padding (4 bytes)
      ]
    )
