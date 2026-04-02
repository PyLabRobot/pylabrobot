"""EL406 manifold step methods.

Provides manifold_aspirate, manifold_dispense, manifold_wash, manifold_prime,
and manifold_auto_clean operations plus their corresponding command builders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_washing.backend import PlateWashingBackend
from pylabrobot.io.binary import Writer
from pylabrobot.resources import Plate

from .driver import EL406Driver
from .helpers import plate_defaults, plate_to_wire_byte
from .protocol import build_framed_message

Intensity = Literal["Variable", "Slow", "Medium", "Fast"]

INTENSITY_TO_BYTE: dict[str, int] = {
  "Variable": 0x01,
  "Slow": 0x02,
  "Medium": 0x03,
  "Fast": 0x04,
}


def validate_intensity(intensity: Intensity) -> None:
  if intensity not in {"Slow", "Medium", "Fast", "Variable"}:
    raise ValueError(
      f"intensity must be one of {sorted({'Slow', 'Medium', 'Fast', 'Variable'})}, "
      f"got {intensity!r}"
    )


logger = logging.getLogger(__name__)

Buffer = Literal["A", "B", "C", "D"]
TravelRate = Literal["1", "2", "3", "4", "5", "1 CW", "2 CW", "3 CW", "4 CW", "6 CW"]

TRAVEL_RATE_TO_BYTE: dict[str, int] = {
  "1": 1,
  "2": 2,
  "3": 3,
  "4": 4,
  "5": 5,
  "1 CW": 7,
  "2 CW": 8,
  "3 CW": 9,
  "4 CW": 10,
  "6 CW": 6,
}


def travel_rate_to_byte(rate: TravelRate) -> int:
  if rate not in TRAVEL_RATE_TO_BYTE:
    valid = sorted(TRAVEL_RATE_TO_BYTE.keys())
    raise ValueError(
      f"Invalid travel rate '{rate}'. Must be one of: {', '.join(repr(r) for r in valid)}"
    )
  return TRAVEL_RATE_TO_BYTE[rate]


def get_plate_wash_defaults(plate: Plate) -> dict:
  pt = plate_defaults(plate)
  return {
    "dispense_volume": 300.0 if pt["cols"] == 12 else 100.0,
    "dispense_z": pt["dispense_z"],
    "aspirate_z": pt["aspirate_z"],
  }


def validate_buffer(buffer: Buffer) -> None:
  if buffer.upper() not in {"A", "B", "C", "D"}:
    raise ValueError(f"Invalid buffer '{buffer}'. Must be one of: A, B, C, D")


def validate_flow_rate(flow_rate: int) -> None:
  if not 1 <= flow_rate <= 9:
    raise ValueError(f"Invalid flow rate {flow_rate}. Must be between 1 and 9.")


def validate_cycles(cycles: int) -> None:
  if not 1 <= cycles <= 250:
    raise ValueError(f"cycles must be 1-250, got {cycles}")


def validate_delay_ms(delay_ms: int) -> None:
  if not 0 <= delay_ms <= 65535:
    raise ValueError(f"delay_ms must be 0-65535, got {delay_ms}")


def validate_travel_rate(rate: int) -> None:
  if not 1 <= rate <= 9:
    raise ValueError(f"travel_rate must be 1-9, got {rate}")


class EL406PlateWashingBackend(PlateWashingBackend):
  """Manifold plate washing backend for the BioTek EL406.

  Implements the abstract PlateWashingBackend interface and also exposes the
  full EL406-specific manifold API for users who need fine-grained control.
  """

  @dataclass
  class WashParams(BackendParams):
    """Parameters for manifold wash.

    Attributes:
      buffer: Buffer valve selection (A, B, C, D). Default A.
      dispense_flow_rate: Flow rate for dispensing (1-9). Default 7.
      aspirate_travel_rate: Travel rate for aspiration (1-9). Default 3.
      soak_duration: Soak duration in seconds (0 to disable, 0-3599). Default 0.
      shake_duration: Shake duration in seconds (0 to disable, 0-3599). Default 0.
      shake_intensity: Shake intensity ("Variable", "Slow", "Medium", "Fast").
        Default "Medium".
    """

    buffer: Buffer = "A"
    dispense_flow_rate: int = 7
    aspirate_travel_rate: int = 3
    soak_duration: int = 0
    shake_duration: int = 0
    shake_intensity: Intensity = "Medium"

  @dataclass
  class PrimeParams(BackendParams):
    """Parameters for manifold prime.

    Attributes:
      plate: PLR Plate resource.
      volume: Prime volume in uL. Range: 5000-999000 uL.
        Wire resolution: 1000 uL (1 mL).
      buffer: Buffer valve selection (A, B, C, D).
      flow_rate: Flow rate (3-11, default 9).
    """

    plate: Optional[Plate] = None
    volume: float = 10000.0
    buffer: Buffer = "A"
    flow_rate: int = 9

  def __init__(self, driver: EL406Driver) -> None:
    self._driver = driver

  async def aspirate(
    self,
    plate: Plate,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    await self.manifold_aspirate(plate)

  async def dispense(
    self,
    plate: Plate,
    volume: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    await self.manifold_dispense(plate, volume=volume)

  async def wash(
    self,
    plate: Plate,
    cycles: int = 3,
    dispense_volume: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, self.WashParams):
      backend_params = self.WashParams()
    await self.manifold_wash(
      plate,
      cycles=cycles,
      dispense_volume=dispense_volume,
      buffer=backend_params.buffer,
      dispense_flow_rate=backend_params.dispense_flow_rate,
      aspirate_travel_rate=backend_params.aspirate_travel_rate,
      soak_duration=backend_params.soak_duration,
      shake_duration=backend_params.shake_duration,
      shake_intensity=backend_params.shake_intensity,
    )

  async def prime(
    self,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, self.PrimeParams):
      raise NotImplementedError(
        "prime() requires PrimeParams with plate and volume. "
        "Use manifold_prime(plate, volume) directly."
      )
    assert backend_params.plate is not None, "PrimeParams.plate must not be None"
    await self.manifold_prime(
      backend_params.plate,
      volume=backend_params.volume,
      buffer=backend_params.buffer,
      flow_rate=backend_params.flow_rate,
    )

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
    travel_rate: TravelRate,
    delay_ms: int,
    vacuum_time_sec: int,
  ) -> tuple[int, int]:
    """Validate aspirate mode-specific params and return (time_value, rate_byte)."""
    if not vacuum_filtration:
      if travel_rate not in TRAVEL_RATE_TO_BYTE:
        raise ValueError(
          f"Invalid travel rate '{travel_rate}'. Must be one of: "
          f"{', '.join(repr(r) for r in sorted(TRAVEL_RATE_TO_BYTE))}"
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
    plate: Plate,
    vacuum_filtration: bool,
    travel_rate: TravelRate,
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
    pt_defaults = get_plate_wash_defaults(plate)
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
        f"Manifold pre-dispense volume must be 0 (disabled) or 25-3000 uL, "
        f"got {pre_dispense_volume}"
      )
    if not 3 <= pre_dispense_flow_rate <= 11:
      raise ValueError(
        f"Manifold pre-dispense flow rate must be 3-11, got {pre_dispense_flow_rate}"
      )
    if not 0 <= vacuum_delay_volume <= 3000:
      raise ValueError(f"Manifold vacuum delay volume must be 0-3000 uL, got {vacuum_delay_volume}")

  def _validate_dispense_params(
    self,
    plate: Plate,
    volume: float,
    buffer: Buffer,
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
      pt_defaults = get_plate_wash_defaults(plate)
      offset_z = pt_defaults["dispense_z"]

    if not 25 <= volume <= 3000:
      raise ValueError(f"Manifold dispense volume must be 25-3000 uL, got {volume}")
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
    plate: Plate,
    dispense_volume: float | None,
    dispense_z: int | None,
    aspirate_z: int | None,
    secondary_z: int | None,
    final_secondary_z: int | None,
  ) -> tuple[float, int, int, int, int]:
    """Resolve plate-type-aware defaults for wash parameters."""
    pt_defaults = get_plate_wash_defaults(plate)
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

  @classmethod
  def _validate_wash_core_params(
    cls,
    cycles: int,
    buffer: Buffer,
    dispense_volume: float,
    dispense_flow_rate: int,
    dispense_x: int,
    dispense_y: int,
    aspirate_travel_rate: int,
    aspirate_x: int,
    aspirate_y: int,
    pre_dispense_flow_rate: int,
    aspirate_delay_ms: int,
    wash_format: Literal["Plate", "Sector"],
    sector_mask: int,
  ) -> None:
    """Validate core wash dispense/aspirate parameters."""
    validate_cycles(cycles)
    if dispense_volume <= 0:
      raise ValueError(f"dispense_volume must be positive, got {dispense_volume}")
    validate_buffer(buffer)
    validate_flow_rate(dispense_flow_rate)
    cls._validate_manifold_xy(dispense_x, dispense_y, "Wash dispense")
    validate_travel_rate(aspirate_travel_rate)
    cls._validate_manifold_xy(aspirate_x, aspirate_y, "Wash aspirate")
    if wash_format not in ("Plate", "Sector"):
      raise ValueError(f"wash_format must be 'Plate' or 'Sector', got '{wash_format}'")
    if not 0 <= sector_mask <= 0xFFFF:
      raise ValueError(f"sector_mask must be 0x0000-0xFFFF, got 0x{sector_mask:04X}")
    validate_flow_rate(pre_dispense_flow_rate)
    validate_delay_ms(aspirate_delay_ms)

  @classmethod
  def _validate_wash_final_and_extras(
    cls,
    final_aspirate_x: int,
    final_aspirate_y: int,
    final_aspirate_delay_ms: int,
    pre_dispense_volume: float,
    vacuum_delay_volume: float,
    soak_duration: int,
    shake_duration: int,
    shake_intensity: Intensity,
  ) -> None:
    """Validate final-aspirate, pre-dispense, soak/shake parameters."""
    cls._validate_manifold_xy(final_aspirate_x, final_aspirate_y, "Final aspirate")
    validate_delay_ms(final_aspirate_delay_ms)
    if pre_dispense_volume != 0 and not 25 <= pre_dispense_volume <= 3000:
      raise ValueError(
        f"Wash pre-dispense volume must be 0 (disabled) or 25-3000 uL, got {pre_dispense_volume}"
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
    secondary_x: int,
    secondary_y: int,
    final_secondary_aspirate: bool,
    final_secondary_x: int,
    final_secondary_y: int,
  ) -> None:
    """Validate secondary and final-secondary aspirate offsets."""
    if secondary_aspirate:
      cls._validate_manifold_xy(secondary_x, secondary_y, "Secondary")
    if final_secondary_aspirate:
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
    plate: Plate,
    cycles: int,
    buffer: Buffer,
    dispense_volume: float | None,
    dispense_flow_rate: int,
    dispense_x: int,
    dispense_y: int,
    dispense_z: int | None,
    aspirate_travel_rate: int,
    aspirate_z: int | None,
    aspirate_x: int,
    aspirate_y: int,
    pre_dispense_flow_rate: int,
    aspirate_delay_ms: int,
    final_aspirate_x: int,
    final_aspirate_y: int,
    final_aspirate_delay_ms: int,
    pre_dispense_volume: float,
    vacuum_delay_volume: float,
    soak_duration: int,
    shake_duration: int,
    shake_intensity: Intensity,
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
      plate,
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
      aspirate_travel_rate,
      aspirate_x,
      aspirate_y,
      pre_dispense_flow_rate,
      aspirate_delay_ms,
      wash_format,
      sector_mask,
    )
    self._validate_wash_final_and_extras(
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
      secondary_x,
      secondary_y,
      final_secondary_aspirate,
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
    plate: Plate,
    vacuum_filtration: bool = False,
    travel_rate: TravelRate = "3",
    delay: float = 0.0,
    vacuum_time: float = 30.0,
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
    - Normal (vacuum_filtration=False): Uses travel_rate and delay.
    - Vacuum filtration (vacuum_filtration=True): Uses vacuum_time.
      Travel rate is ignored (greyed out in GUI).

    Args:
      plate: PLR Plate resource.
      vacuum_filtration: Enable vacuum filtration mode.
      travel_rate: Head travel rate. Normal: "1"-"5".
        Cell wash: "1 CW", "2 CW", "3 CW", "4 CW", "6 CW".
        Ignored when vacuum_filtration=True.
      delay: Post-aspirate delay in seconds (0-5). Only used when
        vacuum_filtration=False. Wire resolution: 1 ms.
      vacuum_time: Vacuum filtration time in seconds (5-999). Only used when
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
    # Convert PLR units (seconds) to wire units: seconds → milliseconds, seconds → integer seconds
    delay_ms = round(delay * 1000)
    vacuum_time_sec = round(vacuum_time)

    offset_z, secondary_z, time_value, rate_byte = self._validate_aspirate_params(
      plate=plate,
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
      "Aspirating: vacuum=%s, travel_rate=%s, delay=%.3f s",
      vacuum_filtration,
      travel_rate,
      delay,
    )

    data = self._build_aspirate_command(
      plate=plate,
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
    framed_command = build_framed_message(command=0xA5, data=data)
    async with self._driver.batch(plate):
      await self._driver._send_step_command(framed_command)

  async def manifold_dispense(
    self,
    plate: Plate,
    volume: float,
    buffer: Buffer = "A",
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
      plate: PLR Plate resource.
      volume: Volume to dispense in uL/well. Range: 25-3000 uL (manifold-dependent:
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
      pre_dispense_volume: Pre-dispense volume in uL/tube (0 to disable, 25-3000 when enabled).
      pre_dispense_flow_rate: Pre-dispense flow rate (3-11, default 9).
      vacuum_delay_volume: Delay start of vacuum until volume dispensed in uL/well
        (0 to disable, 0-3000 when enabled). Required for cell wash flow rates 1-2.

    Raises:
      ValueError: If parameters are invalid.
    """
    offset_z = self._validate_dispense_params(
      plate=plate,
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
      plate=plate,
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
    framed_command = build_framed_message(command=0xA6, data=data)
    async with self._driver.batch(plate):
      await self._driver._send_step_command(framed_command)

  async def manifold_wash(
    self,
    plate: Plate,
    cycles: int = 3,
    buffer: Buffer = "A",
    dispense_volume: float | None = None,
    dispense_flow_rate: int = 7,
    dispense_x: int = 0,
    dispense_y: int = 0,
    dispense_z: int | None = None,
    aspirate_travel_rate: int = 3,
    aspirate_z: int | None = None,
    pre_dispense_flow_rate: int = 9,
    aspirate_delay: float = 0.0,
    aspirate_x: int = 0,
    aspirate_y: int = 0,
    final_aspirate: bool = True,
    final_aspirate_z: int | None = None,
    final_aspirate_x: int = 0,
    final_aspirate_y: int = 0,
    final_aspirate_delay: float = 0.0,
    pre_dispense_volume: float = 0.0,
    vacuum_delay_volume: float = 0.0,
    soak_duration: int = 0,
    shake_duration: int = 0,
    shake_intensity: Intensity = "Medium",
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
      plate: PLR Plate resource.
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
      aspirate_delay: Post-aspirate delay in seconds (0-65.535). Default 0.
        Wire resolution: 1 ms.
      aspirate_x: Aspirate X offset in steps (-60 to +60). Default 0.
      aspirate_y: Aspirate Y offset in steps (-40 to +40). Default 0.
      final_aspirate: Enable final aspirate after last cycle. Default True.
        Encoded in header config flags byte [2].
      final_aspirate_z: Z offset for final aspirate (1-210). Default None
        (inherits from aspirate_z). Independent from primary aspirate Z.
      final_aspirate_x: X offset for final aspirate (-60 to +60). Default 0.
      final_aspirate_y: Y offset for final aspirate (-40 to +40). Default 0.
      final_aspirate_delay: Post-aspirate delay for final aspirate in
        seconds (0-65.535). Default 0. Wire resolution: 1 ms.
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
    # Convert PLR units (seconds) to wire units (ms)
    aspirate_delay_ms = round(aspirate_delay * 1000)
    final_aspirate_delay_ms = round(final_aspirate_delay * 1000)

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
      plate=plate,
      cycles=cycles,
      buffer=buffer,
      dispense_volume=dispense_volume,
      dispense_flow_rate=dispense_flow_rate,
      dispense_x=dispense_x,
      dispense_y=dispense_y,
      dispense_z=dispense_z,
      aspirate_travel_rate=aspirate_travel_rate,
      aspirate_z=aspirate_z,
      aspirate_x=aspirate_x,
      aspirate_y=aspirate_y,
      pre_dispense_flow_rate=pre_dispense_flow_rate,
      aspirate_delay_ms=aspirate_delay_ms,
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
      "asp_delay=%.3f s, asp_xy=(%d,%d), final_asp=%s, "
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
      aspirate_delay,
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
      plate=plate,
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

    framed_command = build_framed_message(command=0xA4, data=data)
    # Dynamic timeout: base per cycle + shake + soak + buffer
    # Each cycle takes ~10-30s depending on volume/flow/plate type.
    # Use 60s per cycle as generous safety margin to avoid false timeouts.
    wash_timeout = (cycles * 60) + shake_duration + soak_duration + 120
    async with self._driver.batch(plate):
      await self._driver._send_step_command(framed_command, timeout=wash_timeout)

  async def manifold_prime(
    self,
    plate: Plate,
    volume: float,
    buffer: Buffer = "A",
    flow_rate: int = 9,
    low_flow_volume: float = 5000.0,
    submerge_duration: float = 0.0,
  ) -> None:
    """Prime the manifold fluid lines.

    Fills the wash manifold tubing with liquid from the specified buffer.
    This is typically done at the start of a protocol to ensure the lines
    are filled and ready for dispensing.

    Args:
      plate: PLR Plate resource.
      volume: Prime volume in uL. Range: 5000-999000 uL.
        Wire resolution: 1000 uL (1 mL).
      buffer: Buffer valve selection (A, B, C, D).
      flow_rate: Flow rate (3-11, default 9).
      low_flow_volume: Low flow path volume in uL (5000-999000, default 5000).
        Set to 0 to disable. Wire resolution: 1000 uL (1 mL).
      submerge_duration: Submerge duration in seconds (0 to disable, 60-86340 when
        enabled). Wire resolution: 60 s (1 minute).

    Raises:
      ValueError: If parameters are invalid.
    """
    # Validate in PLR units
    if not 5000 <= volume <= 999000:
      raise ValueError(f"Washer prime volume must be 5000-999000 uL, got {volume}")
    validate_buffer(buffer)
    if not 3 <= flow_rate <= 11:
      raise ValueError(f"Washer prime flow rate must be 3-11, got {flow_rate}")
    if low_flow_volume != 0 and not 5000 <= low_flow_volume <= 999000:
      raise ValueError(
        f"Low flow path volume must be 0 (disabled) or 5000-999000 uL, got {low_flow_volume}"
      )
    if submerge_duration != 0 and not 60 <= submerge_duration <= 86340:
      raise ValueError(
        f"Submerge duration must be 0 (disabled) or 60-86340 seconds, got {submerge_duration}"
      )
    if submerge_duration % 60 != 0:
      raise ValueError(
        f"Submerge duration must be a multiple of 60 seconds (device resolution is 1 minute), "
        f"got {submerge_duration}"
      )

    # Convert to wire units: uL → mL, seconds → minutes
    volume_ml = round(volume / 1000)
    low_flow_volume_ml = round(low_flow_volume / 1000)
    submerge_duration_min = round(submerge_duration / 60)

    low_flow_enabled = low_flow_volume > 0
    submerge_enabled = submerge_duration > 0

    logger.info(
      "Manifold prime: %.1f uL from buffer %s, flow rate %d, low_flow=%s/%.0f uL, "
      "submerge=%s/%.0f s",
      volume,
      buffer,
      flow_rate,
      "enabled" if low_flow_enabled else "disabled",
      low_flow_volume,
      "enabled" if submerge_enabled else "disabled",
      submerge_duration,
    )

    data = self._build_manifold_prime_command(
      plate=plate,
      buffer=buffer,
      volume_ml=volume_ml,
      flow_rate=flow_rate,
      low_flow_volume_ml=low_flow_volume_ml,
      low_flow_enabled=low_flow_enabled,
      submerge_enabled=submerge_enabled,
      submerge_duration_min=submerge_duration_min,
    )
    framed_command = build_framed_message(command=0xA7, data=data)
    # Timeout: base time for priming + submerge duration + buffer
    prime_timeout = self._driver.timeout + submerge_duration + 30
    async with self._driver.batch(plate):
      await self._driver._send_step_command(framed_command, timeout=prime_timeout)

  async def manifold_auto_clean(
    self,
    plate: Plate,
    buffer: Buffer = "A",
    duration: float = 60.0,
  ) -> None:
    """Run a manifold auto-clean cycle.

    Args:
      plate: PLR Plate resource.
      buffer: Buffer valve to use (A, B, C, or D).
      duration: Cleaning duration in seconds (60-14340, i.e. up to 3h59m).
        Wire resolution: 60 s (1 minute).

    Raises:
      ValueError: If parameters are invalid.
    """
    validate_buffer(buffer)
    if not 60 <= duration <= 14340:
      raise ValueError(f"AutoClean duration must be 60-14340 seconds, got {duration}")
    if duration % 60 != 0:
      raise ValueError(
        f"AutoClean duration must be a multiple of 60 seconds (device resolution is 1 minute), "
        f"got {duration}"
      )

    # Convert to wire units: seconds → minutes
    duration_min = round(duration / 60)

    logger.info("Auto-clean: buffer %s, duration %.0f s", buffer, duration)

    data = self._build_auto_clean_command(
      plate=plate,
      buffer=buffer,
      duration_min=duration_min,
    )
    framed_command = build_framed_message(command=0xA8, data=data)
    auto_clean_timeout = max(120.0, duration + 30.0)
    async with self._driver.batch(plate):
      await self._driver._send_step_command(framed_command, timeout=auto_clean_timeout)

  # =========================================================================
  # COMMAND BUILDERS
  # =========================================================================

  def _build_wash_composite_command(
    self,
    plate: Plate,
    cycles: int = 3,
    buffer: Buffer = "A",
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
    shake_intensity: Intensity = "Medium",
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
               + dispense2(19) + shake_soak(15) = 102 bytes.

    Header [0-6]:
      [0] plate_type (plate_type.value)
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
    # Resolve plate-type defaults
    (
      dispense_volume,
      dispense_z,
      aspirate_z,
      secondary_z,
      final_secondary_z,
    ) = self._resolve_wash_defaults(
      plate, dispense_volume, dispense_z, aspirate_z, secondary_z, final_secondary_z
    )

    # Derived values
    buffer_char = ord(buffer.upper())
    disp_vol = int(dispense_volume)
    final_asp_z = final_aspirate_z if final_aspirate_z is not None else aspirate_z
    pre_disp = int(pre_dispense_volume) if pre_dispense_volume > 0 else 0
    vac_delay = int(vacuum_delay_volume) if vacuum_delay_volume > 0 else 0
    intensity_byte = INTENSITY_TO_BYTE.get(shake_intensity, 0x03) if shake_duration > 0 else 0x00

    # Secondary aspirate offsets (0 when disabled)
    sec_x = secondary_x if secondary_aspirate else 0
    sec_y = secondary_y if secondary_aspirate else 0
    final_sec_x = final_secondary_x if final_secondary_aspirate else 0
    final_sec_y = final_secondary_y if final_secondary_aspirate else 0
    final_sec_z = final_secondary_z if final_secondary_aspirate else final_asp_z

    # Bottom wash: Dispense1 gets bottom wash params when enabled, else mirrors main
    bw_vol = int(bottom_wash_volume) if bottom_wash else disp_vol
    bw_flow = bottom_wash_flow_rate if bottom_wash else dispense_flow_rate

    # Pre-dispense between cycles: override or fall back to main pre-dispense
    if pre_dispense_between_cycles_volume > 0:
      midcyc_vol = int(pre_dispense_between_cycles_volume)
      midcyc_flow = pre_dispense_between_cycles_flow_rate
    else:
      midcyc_vol = pre_disp
      midcyc_flow = pre_dispense_flow_rate

    w = Writer()

    # --- Header [0-6] (7 bytes) ---
    w.u8(plate_to_wire_byte(plate))  # [0] Plate type
    w.u8(0x01 if bottom_wash else 0x00)  # [1] Bottom wash enable
    w.u8(0x01 if final_aspirate else 0x00)  # [2] Config flags
    w.u8({"Plate": 0x00, "Sector": 0x01}[wash_format])  # [3] Wash format
    w.u16(sector_mask)  # [4-5] Sector mask (LE)
    w.u8(cycles)  # [6] Wash cycles

    # --- Dispense section 1 [7-28] (22 bytes) — bottom wash or mirror of main ---
    w.u8(buffer_char)  # [7] Buffer (ASCII)
    w.u16(bw_vol)  # [8-9] Volume (LE)
    w.u8(bw_flow)  # [10] Flow rate
    w.i8(dispense_x)  # [11] Offset X
    w.i8(dispense_y)  # [12] Offset Y
    w.u16(dispense_z)  # [13-14] Dispense Z (LE)
    w.u16(pre_disp)  # [15-16] Pre-dispense vol (LE)
    w.u8(pre_dispense_flow_rate)  # [17] Pre-dispense flow rate
    w.u16(vac_delay)  # [18-19] Vacuum delay vol (LE)
    w.raw_bytes(b"\x00" * 7)  # [20-26] Padding
    w.u16(final_aspirate_delay_ms)  # [27-28] Final asp delay (LE)

    # --- Final aspirate section [29-48] (20 bytes) ---
    w.u8(aspirate_travel_rate)  # [29] Travel rate
    w.u16(0x0000)  # [30-31] Delay (always 0 here)
    w.u16(final_asp_z)  # [32-33] Final aspirate Z (LE)
    w.u8(0x01 if final_secondary_aspirate else 0x00)  # [34] Final secondary mode
    w.i8(final_aspirate_x)  # [35] Final aspirate X
    w.i8(final_aspirate_y)  # [36] Final aspirate Y
    w.u16(final_sec_z)  # [37-38] Final secondary Z (LE)
    w.u8(0x00)  # [39] Reserved
    w.i8(final_sec_x)  # [40] Final secondary X
    w.i8(final_sec_y)  # [41] Final secondary Y
    w.raw_bytes(b"\x00" * 5)  # [42-46] Reserved
    w.u8(0x00)  # [47] vac_filt (always 0 in wash)
    # aspirate_delay_ms split: low byte here, high byte starts next section
    w.u8(aspirate_delay_ms & 0xFF)  # [48] asp delay low

    # --- Primary aspirate section [49-67] (19 bytes) ---
    w.u8((aspirate_delay_ms >> 8) & 0xFF)  # [49] asp delay high
    w.u8(aspirate_travel_rate)  # [50] Travel rate
    w.i8(aspirate_x)  # [51] Aspirate X
    w.i8(aspirate_y)  # [52] Aspirate Y
    w.u16(aspirate_z)  # [53-54] Aspirate Z (LE)
    w.u8(0x01 if secondary_aspirate else 0x00)  # [55] Secondary mode
    w.i8(sec_x)  # [56] Secondary X
    w.i8(sec_y)  # [57] Secondary Y
    w.u16(secondary_z)  # [58-59] Secondary Z (LE)
    w.raw_bytes(b"\x00" * 8)  # [60-67] Reserved

    # --- Dispense section 2 [68-86] (19 bytes) — main dispense ---
    w.u8(buffer_char)  # [68] Buffer (ASCII)
    w.u16(disp_vol)  # [69-70] Volume (LE)
    w.u8(dispense_flow_rate)  # [71] Flow rate
    w.i8(dispense_x)  # [72] Offset X
    w.i8(dispense_y)  # [73] Offset Y
    w.u16(dispense_z)  # [74-75] Dispense Z (LE)
    w.u16(midcyc_vol)  # [76-77] Mid-cycle vol (LE)
    w.u8(midcyc_flow)  # [78] Mid-cycle flow rate
    w.u16(vac_delay)  # [79-80] Vacuum delay vol (LE)
    w.raw_bytes(b"\x00" * 6)  # [81-86] Padding

    # --- Shake/soak section [87-101] (15 bytes) ---
    w.u8(0x01 if move_home_first else 0x00)  # [87] move_home_first
    w.u16(shake_duration)  # [88-89] Shake duration (LE)
    w.u8(intensity_byte if shake_duration > 0 else 0x03)  # [90] Intensity
    w.u8(0x00)  # [91] Shake type (always 0)
    w.u16(soak_duration)  # [92-93] Soak duration (LE)
    w.raw_bytes(b"\x00" * 4)  # [94-97] Padding
    w.raw_bytes(b"\x00" * 4)  # [98-101] Trailing padding

    data = w.finish()
    assert len(data) == 102, f"Wash command should be 102 bytes, got {len(data)}"

    logger.debug("Wash command data (%d bytes): %s", len(data), data.hex())
    return data

  def _build_aspirate_command(
    self,
    plate: Plate,
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
      [0]     Plate type (wire byte, e.g. 0x04=96-well)
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
      [16-17] unknown: 0xFF0F (possibly column mask?)
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
    return (
      Writer()
      .u8(plate_to_wire_byte(plate))                   # [0] Plate type
      .u8(1 if vacuum_filtration else 0)             # [1] Vacuum filtration
      .u16(time_value)                               # [2-3] Time/delay (LE)
      .u8(travel_rate_byte & 0xFF)                   # [4] Travel rate
      .i8(offset_x)                                  # [5] X offset
      .i8(offset_y)                                  # [6] Y offset
      .u16(offset_z)                                 # [7-8] Z offset (LE)
      .u8(secondary_mode & 0xFF)                     # [9] Secondary mode
      .i8(secondary_x)                               # [10] Secondary X
      .i8(secondary_y)                               # [11] Secondary Y
      .u16(secondary_z)                              # [12-13] Secondary Z (LE)
      .raw_bytes(b'\x00' * 2)                        # [14-15] Reserved
      .raw_bytes(b'\xff\x0f')                        # [16-17] Unknown, possibly column mask
      .raw_bytes(b'\x00' * 4)                        # [18-21] Padding
      .finish()
    )  # fmt: skip

  def _build_dispense_command(
    self,
    plate: Plate,
    volume: float,
    buffer: Buffer,
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

      [0]      Plate type (wire byte, e.g. 0x04=96-well)
      [1]      Buffer letter: A=0x41, B=0x42, C=0x43, D=0x44 (ASCII char)
      [2-3]    Volume: 2 bytes, LE, in uL (25-3000)
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
      volume: Dispense volume in uL.
      buffer: Buffer valve (A, B, C, D).
      flow_rate: Flow rate (1-11; 1-2 = cell wash, requires vacuum delay).
      offset_x: X offset (signed, steps, -60..60).
      offset_y: Y offset (signed, steps, -40..40).
      offset_z: Z offset (steps, 1-210).
      pre_dispense_volume: Pre-dispense volume in uL (0 to disable).
      pre_dispense_flow_rate: Pre-dispense flow rate (3-11).
      vacuum_delay_volume: Vacuum delay volume in uL (0 to disable).

    Returns:
      Command bytes (20 bytes).
    """
    pre_disp_vol = int(pre_dispense_volume) if pre_dispense_volume > 0 else 0
    vac_delay = int(vacuum_delay_volume) if vacuum_delay_volume > 0 else 0

    return (
      Writer()
      .u8(plate_to_wire_byte(plate))   # [0] Plate type
      .u8(ord(buffer.upper()))         # [1] Buffer (ASCII)
      .u16(int(volume))                # [2-3] Volume (LE)
      .u8(flow_rate)                   # [4] Flow rate
      .i8(offset_x)                    # [5] X offset
      .i8(offset_y)                    # [6] Y offset
      .u16(offset_z)                   # [7-8] Z offset (LE)
      .u16(pre_disp_vol)              # [9-10] Pre-dispense volume (LE)
      .u8(pre_dispense_flow_rate)      # [11] Pre-dispense flow rate
      .u16(vac_delay)                  # [12-13] Vacuum delay volume (LE)
      .raw_bytes(b'\x00' * 6)         # [14-19] Padding
      .finish()
    )  # fmt: skip

  def _build_manifold_prime_command(
    self,
    plate: Plate,
    buffer: Buffer,
    volume_ml: float,
    flow_rate: int = 9,
    low_flow_volume_ml: int = 5,
    low_flow_enabled: bool = True,
    submerge_enabled: bool = False,
    submerge_duration_min: int = 0,
  ) -> bytes:
    """Build manifold prime command bytes.

    Protocol format for manifold prime (13 bytes):

      [0]   Plate type (wire byte, e.g. 0x04=96-well)
      [1]   Buffer letter: A=0x41, B=0x42, C=0x43, D=0x44 (ASCII char)
      [2-3] Volume: 2 bytes, little-endian, in mL
      [4]   Flow rate: 3-11
      [5-6] Low flow volume: 2 bytes, little-endian (in mL, 0 if disabled)
      [7-8] Submerge duration: 2 bytes, little-endian (in minutes, 0 if disabled)
            HH:MM encoded as total minutes: hours*60+minutes
      [9-12] Padding zeros: 4 bytes

    Args:
      buffer: Buffer valve (A, B, C, D).
      volume_ml: Prime volume in mL.
      flow_rate: Flow rate (3-11, default 9).
      low_flow_volume_ml: Low flow volume in mL (default 5).
      low_flow_enabled: Enable low flow path (default True).
      submerge_enabled: Enable submerge tips after prime (default False).
      submerge_duration_min: Submerge duration in minutes (default 0).

    Returns:
      Command bytes (13 bytes).
    """
    lf_vol = low_flow_volume_ml if (low_flow_enabled and low_flow_volume_ml > 0) else 0
    sub_dur = submerge_duration_min if submerge_enabled else 0

    return (
      Writer()
      .u8(plate_to_wire_byte(plate))   # [0] Plate type
      .u8(ord(buffer.upper()))         # [1] Buffer (ASCII)
      .u16(int(volume_ml))             # [2-3] Volume (LE, mL)
      .u8(flow_rate)                   # [4] Flow rate
      .u16(lf_vol)                     # [5-6] Low flow volume (LE, mL)
      .u16(sub_dur)                    # [7-8] Submerge duration (LE, minutes)
      .raw_bytes(b'\x00' * 4)         # [9-12] Padding
      .finish()
    )  # fmt: skip

  def _build_auto_clean_command(
    self,
    plate: Plate,
    buffer: Buffer,
    duration_min: int = 1,
  ) -> bytes:
    """Build auto-clean command bytes.

    Protocol format for auto-clean (8 bytes):

      [0]   Plate type (wire byte, e.g. 0x04=96-well)
      [1]   Buffer letter: A=0x41, B=0x42, C=0x43, D=0x44 (ASCII char)
      [2-3] Duration: 2 bytes, little-endian (in minutes)
      [4-7] Padding zeros: 4 bytes

    Args:
      buffer: Buffer valve (A, B, C, D).
      duration_min: Cleaning duration in minutes (1-239).

    Returns:
      Command bytes (8 bytes).
    """
    return (
      Writer()
      .u8(plate_to_wire_byte(plate))   # [0] Plate type
      .u8(ord(buffer.upper()))         # [1] Buffer (ASCII)
      .u16(int(duration_min))          # [2-3] Duration (LE, minutes)
      .raw_bytes(b'\x00' * 4)         # [4-7] Padding
      .finish()
    )  # fmt: skip
