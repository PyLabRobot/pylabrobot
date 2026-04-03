"""VantageXArm: X-arm positioning control for Hamilton Vantage liquid handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from .driver import VantageDriver


class VantageXArm:
  """Controls the X-arm on a Hamilton Vantage.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for X-arm positioning and delegates I/O to the driver.
  """

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver

  async def _on_setup(self):
    pass

  async def _on_stop(self):
    pass

  # -- commands (A1XM) -------------------------------------------------------

  async def initialize(self) -> None:
    """Initialize the X-arm (A1XM:XI)."""
    await self.driver.send_command(module="A1XM", command="XI")

  async def move_to_x_position(
    self,
    x_position: int = 5000,
    x_speed: int = 25000,
  ) -> None:
    """Move arm to X position (A1XM:XP).

    Args:
      x_position: X position [0.1mm]. Range -50000 to 50000.
      x_speed: X speed [0.1mm/s]. Range 1 to 25000.
    """
    if not -50000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -50000 to 50000")
    if not 1 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 1 to 25000")

    await self.driver.send_command(module="A1XM", command="XP", xp=x_position, xv=x_speed)

  async def move_to_x_position_safe(
    self,
    x_position: int = 5000,
    x_speed: int = 25000,
    xx: int = 1,
  ) -> None:
    """Move arm to X position with all attached components in Z-safety (A1XM:XA).

    Args:
      x_position: X position [0.1mm].
      x_speed: X speed [0.1mm/s].
      xx: Unknown parameter.
    """
    if not -50000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -50000 to 50000")
    if not 1 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 1 to 25000")

    await self.driver.send_command(module="A1XM", command="XA", xp=x_position, xv=x_speed, xx=xx)

  async def move_relatively(
    self,
    x_search_distance: int = 0,
    x_speed: int = 25000,
    xx: int = 1,
  ) -> None:
    """Move arm relatively in X (A1XM:XS).

    Args:
      x_search_distance: X search distance [0.1mm].
      x_speed: X speed [0.1mm/s].
      xx: Unknown parameter.
    """
    if not -50000 <= x_search_distance <= 50000:
      raise ValueError("x_search_distance must be in range -50000 to 50000")
    if not 1 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 1 to 25000")

    await self.driver.send_command(
      module="A1XM", command="XS", xs=x_search_distance, xv=x_speed, xx=xx
    )

  async def search_teach_signal(
    self,
    x_search_distance: int = 0,
    x_speed: int = 25000,
    xx: int = 1,
  ) -> None:
    """Search X for teach signal (A1XM:XT).

    Args:
      x_search_distance: X search distance [0.1mm].
      x_speed: X speed [0.1mm/s].
      xx: Unknown parameter.
    """
    if not -50000 <= x_search_distance <= 50000:
      raise ValueError("x_search_distance must be in range -50000 to 50000")
    if not 1 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 1 to 25000")

    await self.driver.send_command(
      module="A1XM", command="XT", xs=x_search_distance, xv=x_speed, xx=xx
    )

  async def turn_off(self) -> None:
    """Turn X drive off (A1XM:XO)."""
    await self.driver.send_command(module="A1XM", command="XO")

  async def request_position(self):
    """Request arm X position (A1XM:RX)."""
    return await self.driver.send_command(module="A1XM", command="RX")

  async def request_error_code(self):
    """Request X-arm error code (A1XM:RE)."""
    return await self.driver.send_command(module="A1XM", command="RE")
