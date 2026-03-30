"""STARXArm: X-arm positioning control for Hamilton STAR liquid handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
  from .driver import STARDriver

logger = logging.getLogger(__name__)


class STARXArm:
  """Controls one X-arm (left or right) on a Hamilton STAR.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for X-arm positioning and delegates I/O to the driver.

  Args:
    driver: The STARDriver instance to send commands through.
    side: Which X-arm to control — ``"left"`` or ``"right"``.
  """

  def __init__(self, driver: "STARDriver", side: Literal["left", "right"]):
    self._driver = driver
    self._side = side

  # -- positioning (collision risk) ------------------------------------------

  async def move_to(self, x_position: float = 0.0):
    """Position X-arm (C0:JX for left, C0:JS for right).

    Collision risk! This moves the arm without raising components to Z-safety.

    Args:
      x_position: X-position in mm. Must be between 0 and 3000. Default 0.
    """

    assert 0 <= x_position <= 3000.0, "x_position must be between 0 and 3000 mm"

    cmd = "JX" if self._side == "left" else "JS"
    return await self._driver.send_command(
      module="C0",
      command=cmd,
      xs=f"{round(x_position * 10):05}",
    )

  # -- safe positioning (Z-safety) -------------------------------------------

  async def move_to_safe(self, x_position: float = 0.0):
    """Move X-arm to position with all attached components in Z-safety position
    (C0:KX for left, C0:KR for right).

    Args:
      x_position: X-position in mm. Must be between 0 and 3000. Default 0.
    """

    assert 0 <= x_position <= 3000.0, "x_position must be between 0 and 3000 mm"

    cmd = "KX" if self._side == "left" else "KR"
    return await self._driver.send_command(
      module="C0",
      command=cmd,
      xs=round(x_position * 10),
    )

  # -- position query --------------------------------------------------------

  async def request_position(self) -> float:
    """Request current X-arm position (C0:RX for left, C0:QX for right).

    Returns:
      X-position in mm (firmware value divided by 10).
    """

    cmd = "RX" if self._side == "left" else "QX"
    resp = await self._driver.send_command(module="C0", command=cmd, fmt="rx#####")
    return float(resp["rx"]) / 10

  # -- collision type query --------------------------------------------------

  async def last_collision_type(self) -> bool:
    """Request last collision type after error 27 (C0:XX for left, C0:XR for right).

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    cmd = "XX" if self._side == "left" else "XR"
    resp = await self._driver.send_command(module="C0", command=cmd, fmt="xq#")
    return resp["xq"] == 1
