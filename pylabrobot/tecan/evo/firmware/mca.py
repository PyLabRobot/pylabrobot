"""Firmware command wrapper for the Tecan EVO MCA (Multi-Channel Arm).

Provides typed methods for MCA firmware commands. Bus init (``PIB``/``PIA``),
bus mode (``BMX``) and halt (``BMA``) are inherited from :class:`EVOArm`.
"""

from __future__ import annotations

from typing import List, Optional

from pylabrobot.tecan.evo.firmware.arm_base import EVOArm


class Mca(EVOArm):
  """Firmware commands for the MCA (Multi-Channel Arm)."""

  async def position_absolute(
    self,
    x: Optional[int] = None,
    y: Optional[int] = None,
    z: Optional[int] = None,
  ) -> None:
    """Position absolute for the MCA axes (PAA).

    Each axis is optional; pass ``None`` to leave that axis unchanged (e.g.
    raise Z only before an X/Y move to avoid collisions).

    Args:
      x: absolute x position in 1/10 mm
      y: absolute y position in 1/10 mm
      z: absolute z position in 1/10 mm
    """
    params: List[Optional[int]] = [x, y, z]
    await self.driver.send_command(module=self.module, command="PAA", params=params)
