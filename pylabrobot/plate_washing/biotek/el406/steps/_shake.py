"""EL406 shake/soak step methods.

Provides the shake operation and its command builder.
"""

from __future__ import annotations

import logging
from typing import Literal

from ..constants import (
  SHAKE_SOAK_COMMAND,
)
from ..helpers import (
  INTENSITY_TO_BYTE,
  validate_intensity,
)
from ..protocol import build_framed_message
from ._base import EL406StepsBaseMixin

logger = logging.getLogger("pylabrobot.plate_washing.biotek.el406")


class EL406ShakeStepsMixin(EL406StepsBaseMixin):
  """Mixin for shake/soak step operations."""

  MAX_SHAKE_DURATION = 3599  # 59:59 max (mm:ss format, mm max=59)
  MAX_SOAK_DURATION = 3599  # 59:59 max (mm:ss format, mm max=59)

  async def shake(
    self,
    duration: int = 0,
    intensity: Literal["Variable", "Slow", "Medium", "Fast"] = "Medium",
    soak_duration: int = 0,
    move_home_first: bool = True,
  ) -> None:
    """Shake the plate with optional soak period.

    Durations are in whole seconds (GUI uses mm:ss picker, max 59:59 each).
    A duration of 0 disables shake. A soak_duration of 0 disables soak.

    Note: The GUI forces move_home_first=True when total time exceeds 60s
    to prevent manifold drip contamination. Our default of True matches this.

    Args:
      duration: Shake duration in seconds (0-3599). 0 to disable shake.
      intensity: Shake intensity - "Variable", "Slow" (3.5 Hz),
                 "Medium" (5 Hz), or "Fast" (8 Hz).
      soak_duration: Soak duration in seconds after shaking (0-3599). 0 to disable.
      move_home_first: Move carrier to home position before shaking (default True).

    Raises:
      ValueError: If parameters are invalid.
    """
    if duration < 0 or duration > self.MAX_SHAKE_DURATION:
      raise ValueError(f"Invalid duration {duration}. Must be 0-{self.MAX_SHAKE_DURATION}.")
    if soak_duration < 0 or soak_duration > self.MAX_SOAK_DURATION:
      raise ValueError(
        f"Invalid soak_duration {soak_duration}. Must be 0-{self.MAX_SOAK_DURATION}."
      )
    if duration == 0 and soak_duration == 0:
      raise ValueError("At least one of duration or soak_duration must be > 0.")
    validate_intensity(intensity)

    shake_enabled = duration > 0

    logger.info(
      "Shake: %ds, %s intensity, move_home=%s, soak=%ds",
      duration,
      intensity,
      move_home_first,
      soak_duration,
    )

    data = self._build_shake_command(
      shake_duration=duration,
      soak_duration=soak_duration,
      intensity=intensity,
      shake_enabled=shake_enabled,
      move_home_first=move_home_first,
    )
    framed_command = build_framed_message(SHAKE_SOAK_COMMAND, data)
    total_timeout = duration + soak_duration + self.timeout
    await self._send_step_command(framed_command, timeout=total_timeout)

  # =========================================================================
  # COMMAND BUILDERS
  # =========================================================================

  def _build_shake_command(
    self,
    shake_duration: int = 0,
    soak_duration: int = 0,
    intensity: str = "medium",
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
      shake_duration: Shake duration in seconds.
      soak_duration: Soak duration in seconds.
      intensity: Shake intensity ("Variable", "Slow", "Medium", "Fast").
      shake_enabled: Whether shake is enabled. When False, shake_duration is not encoded.
      move_home_first: Move carrier to home position before shaking (default True).

    Returns:
      Command bytes (12 bytes).
    """
    # Shake duration as 16-bit little-endian total seconds
    # Only encode if shake_enabled=True (sets to 0 when disabled)
    if shake_enabled:
      shake_total_seconds = int(shake_duration)
    else:
      shake_total_seconds = 0
    shake_low = shake_total_seconds & 0xFF
    shake_high = (shake_total_seconds >> 8) & 0xFF

    # Soak duration as 16-bit little-endian total seconds
    soak_total_seconds = int(soak_duration)
    soak_low = soak_total_seconds & 0xFF
    soak_high = (soak_total_seconds >> 8) & 0xFF

    # Map intensity to byte value
    intensity_byte = INTENSITY_TO_BYTE.get(intensity, 0x03)

    byte0 = 0x01 if move_home_first else 0x00

    return bytes(
      [
        self.plate_type.value,  # byte[0]: Plate type prefix
        byte0,  # byte[1]: move_home_first
        shake_low,  # byte[2]: Shake duration (low byte)
        shake_high,  # byte[3]: Shake duration (high byte)
        intensity_byte,  # byte[4]: Frequency/intensity
        0,  # byte[5]: Reserved
        soak_low,  # byte[6]: Soak duration (low byte)
        soak_high,  # byte[7]: Soak duration (high byte)
        0,
        0,
        0,
        0,  # bytes[8-11]: Padding/reserved
      ]
    )
