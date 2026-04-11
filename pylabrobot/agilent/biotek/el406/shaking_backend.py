"""EL406 shake/soak backend.

Provides the shake operation and its command builder.
This is a direct port of the legacy EL406ShakeStepsMixin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.shaking.backend import ShakerBackend
from pylabrobot.io.binary import Writer
from pylabrobot.resources import Plate

from .driver import EL406Driver
from .helpers import plate_to_wire_byte
from .protocol import build_framed_message

INTENSITY_TO_BYTE: dict[str, int] = {
  "Variable": 0x01,
  "Slow": 0x02,
  "Medium": 0x03,
  "Fast": 0x04,
}

logger = logging.getLogger(__name__)


Intensity = Literal["Variable", "Slow", "Medium", "Fast"]


def validate_intensity(intensity: Intensity) -> None:
  if intensity not in {"Slow", "Medium", "Fast", "Variable"}:
    raise ValueError(
      f"intensity must be one of {sorted({'Slow', 'Medium', 'Fast', 'Variable'})}, got {intensity!r}"
    )


class EL406ShakingBackend(ShakerBackend):
  """Shaking backend for the BioTek EL406.

  The EL406 shake is a single fire-and-forget command with duration baked in.
  It does not support continuous start/stop shaking or plate locking.
  """

  @dataclass
  class ShakeParams(BackendParams):
    """EL406-specific shake parameters.

    Attributes:
      intensity: Shake intensity - "Variable", "Slow" (3.5 Hz),
                 "Medium" (5 Hz), or "Fast" (8 Hz).
      soak_duration: Soak duration in seconds after shaking (0-3599). 0 to disable.
      move_home_first: Move carrier to home position before shaking (default True).
    """

    intensity: Intensity = "Medium"
    soak_duration: int = 0
    move_home_first: bool = True

  def __init__(self, driver: EL406Driver) -> None:
    self._driver = driver

  # -- ShakerBackend interface --

  @property
  def supports_locking(self) -> bool:
    return False

  async def lock_plate(self):
    raise NotImplementedError("EL406 does not support plate locking.")

  async def unlock_plate(self):
    raise NotImplementedError("EL406 does not support plate locking.")

  # -- Shake --

  MAX_SHAKE_DURATION = 3599  # 59:59 max (mm:ss format, mm max=59)
  MAX_SOAK_DURATION = 3599  # 59:59 max (mm:ss format, mm max=59)

  async def shake(
    self,
    speed: float,
    duration: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Shake the plate with optional soak period.

    The ``speed`` parameter is accepted to satisfy the generic shaker interface
    but is ignored — the EL406 uses discrete intensity levels, not RPM. Use
    :class:`ShakeParams` for EL406-specific control.

    Durations are in whole seconds (GUI uses mm:ss picker, max 59:59 each).
    A duration of 0 disables shake. A soak_duration of 0 disables soak.

    Note: The GUI forces move_home_first=True when total time exceeds 60s
    to prevent manifold drip contamination. Our default of True matches this.

    The plate is read from the driver (set by assigning to the device's plate_holder).

    Args:
      speed: Ignored (EL406 uses intensity levels, not RPM).
      duration: Shake duration in seconds (0-3599). 0 to disable shake.
      backend_params: EL406-specific parameters (:class:`ShakeParams`).

    Raises:
      ValueError: If parameters are invalid.
    """
    if not isinstance(backend_params, self.ShakeParams):
      backend_params = self.ShakeParams()

    dur = int(duration)
    plate = self._driver.plate
    intensity = backend_params.intensity
    soak_duration = backend_params.soak_duration
    move_home_first = backend_params.move_home_first

    if dur < 0 or dur > self.MAX_SHAKE_DURATION:
      raise ValueError(f"Invalid duration {dur}. Must be 0-{self.MAX_SHAKE_DURATION}.")
    if soak_duration < 0 or soak_duration > self.MAX_SOAK_DURATION:
      raise ValueError(
        f"Invalid soak_duration {soak_duration}. Must be 0-{self.MAX_SOAK_DURATION}."
      )
    if dur == 0 and soak_duration == 0:
      raise ValueError("At least one of duration or soak_duration must be > 0.")
    validate_intensity(intensity)

    shake_enabled = dur > 0

    logger.info(
      "Shake: %ds, %s intensity, move_home=%s, soak=%ds",
      dur,
      intensity,
      move_home_first,
      soak_duration,
    )

    data = self._build_shake_command(
      plate=plate,
      shake_duration=dur,
      soak_duration=soak_duration,
      intensity=intensity,
      shake_enabled=shake_enabled,
      move_home_first=move_home_first,
    )
    framed_command = build_framed_message(command=0xA3, data=data)
    total_timeout = dur + soak_duration + self._driver.timeout
    async with self._driver.batch():
      await self._driver._send_step_command(framed_command, timeout=total_timeout)

  # =========================================================================
  # COMMAND BUILDERS
  # =========================================================================

  def _build_shake_command(
    self,
    plate: Plate,
    shake_duration: int = 0,
    soak_duration: int = 0,
    intensity: Intensity = "Medium",
    shake_enabled: bool = True,
    move_home_first: bool = True,
  ) -> bytes:
    """Build shake command bytes.

    Byte structure (12 bytes):
      [0]      Plate type
      [1]      move_home_first: 0x00 or 0x01
      [2-3]    Shake duration in total seconds (16-bit LE)
      [4]      Intensity: 0x01=Variable, 0x02=Slow, 0x03=Medium, 0x04=Fast
      [5]      Reserved: 0x00
      [6-7]    Soak duration in total seconds (16-bit LE)
      [8-11]   Padding (4 bytes)

    Args:
      plate: PLR Plate resource.
      shake_duration: Shake duration in seconds.
      soak_duration: Soak duration in seconds.
      intensity: Shake intensity ("Variable", "Slow", "Medium", "Fast").
      shake_enabled: Whether shake is enabled. When False, shake_duration is not encoded.
      move_home_first: Move carrier to home position before shaking (default True).

    Returns:
      Command bytes (12 bytes).
    """
    shake_total_seconds = int(shake_duration) if shake_enabled else 0

    return (
      Writer()
      .u8(plate_to_wire_byte(plate))                    # [0] Plate type
      .u8(0x01 if move_home_first else 0x00)           # [1] move_home_first
      .u16(shake_total_seconds)                        # [2-3] Shake duration (seconds)
      .u8(INTENSITY_TO_BYTE.get(intensity, 0x03))      # [4] Intensity
      .u8(0x00)                                        # [5] Reserved
      .u16(int(soak_duration))                         # [6-7] Soak duration (seconds)
      .raw_bytes(b'\x00' * 4)                          # [8-11] Padding
      .finish()
    )  # fmt: skip
