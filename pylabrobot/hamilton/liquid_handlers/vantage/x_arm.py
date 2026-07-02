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

  async def move_to(
    self,
    x_position: float = 500.0,
    x_speed: float = 2500.0,
  ) -> None:
    """Move arm to X position (A1XM:XP).

    Args:
      x_position: X position [mm]. Range -5000.0 to 5000.0.
      x_speed: X speed [mm/s]. Range 0.1 to 2500.0.
    """
    if not -5000.0 <= x_position <= 5000.0:
      raise ValueError("x_position must be in range -5000.0 to 5000.0")
    if not 0.1 <= x_speed <= 2500.0:
      raise ValueError("x_speed must be in range 0.1 to 2500.0")

    await self.driver.send_command(
      module="A1XM", command="XP", xp=round(x_position * 10), xv=round(x_speed * 10)
    )

  async def move_to_safe(
    self,
    x_position: float = 500.0,
    x_speed: float = 2500.0,
    xx: int = 1,
  ) -> None:
    """Move arm to X position with all attached components in Z-safety (A1XM:XA).

    Args:
      x_position: X position [mm]. Range -5000.0 to 5000.0.
      x_speed: X speed [mm/s]. Range 0.1 to 2500.0.
      xx: Unknown parameter.
    """
    if not -5000.0 <= x_position <= 5000.0:
      raise ValueError("x_position must be in range -5000.0 to 5000.0")
    if not 0.1 <= x_speed <= 2500.0:
      raise ValueError("x_speed must be in range 0.1 to 2500.0")

    await self.driver.send_command(
      module="A1XM", command="XA", xp=round(x_position * 10), xv=round(x_speed * 10), xx=xx
    )

  async def move_relatively(
    self,
    x_search_distance: float = 0.0,
    x_speed: float = 2500.0,
    xx: int = 1,
  ) -> None:
    """Move arm relatively in X (A1XM:XS).

    Args:
      x_search_distance: X search distance [mm]. Range -5000.0 to 5000.0.
      x_speed: X speed [mm/s]. Range 0.1 to 2500.0.
      xx: Unknown parameter.
    """
    if not -5000.0 <= x_search_distance <= 5000.0:
      raise ValueError("x_search_distance must be in range -5000.0 to 5000.0")
    if not 0.1 <= x_speed <= 2500.0:
      raise ValueError("x_speed must be in range 0.1 to 2500.0")

    await self.driver.send_command(
      module="A1XM", command="XS", xs=round(x_search_distance * 10), xv=round(x_speed * 10), xx=xx
    )

  async def search_teach_signal(
    self,
    x_search_distance: float = 0.0,
    x_speed: float = 2500.0,
    xx: int = 1,
  ) -> None:
    """Search X for teach signal (A1XM:XT).

    Args:
      x_search_distance: X search distance [mm]. Range -5000.0 to 5000.0.
      x_speed: X speed [mm/s]. Range 0.1 to 2500.0.
      xx: Unknown parameter.
    """
    if not -5000.0 <= x_search_distance <= 5000.0:
      raise ValueError("x_search_distance must be in range -5000.0 to 5000.0")
    if not 0.1 <= x_speed <= 2500.0:
      raise ValueError("x_speed must be in range 0.1 to 2500.0")

    await self.driver.send_command(
      module="A1XM", command="XT", xs=round(x_search_distance * 10), xv=round(x_speed * 10), xx=xx
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

  async def set_x_drive_angle_of_alignment(
    self,
    xl: int = 1,
  ) -> None:
    """Set X drive angle of alignment (A1XM:XL).

    Args:
      xl: Alignment parameter. Range 1 to 1.
    """
    if not 1 <= xl <= 1:
      raise ValueError("xl must be in range 1 to 1")

    await self.driver.send_command(module="A1XM", command="XL", xl=xl)

  async def send_message_to_motion_controller(
    self,
    bd: str = "",
  ):
    """Send message to motion controller (A1XM:BD).

    Args:
      bd: Message to send to the motion controller.
    """
    return await self.driver.send_command(module="A1XM", command="BD", bd=bd)

  async def set_any_parameter_within_this_module(
    self,
    xm: int = 0,
    xt: int = 1,
  ):
    """Set any parameter within this module (A1XM:AA).

    Args:
      xm: Parameter index.
      xt: Parameter value.
    """
    return await self.driver.send_command(module="A1XM", command="AA", xm=xm, xt=xt)

  async def request_x_drive_recorded_data(
    self,
    lj: int = 0,
    ln: int = 0,
  ):
    """Request X drive recorded data (A1RM:QL).

    Note: despite being an X-arm method, this sends to the A1RM module.

    Args:
      lj: Data query parameter.
      ln: Data query parameter.
    """
    return await self.driver.send_command(module="A1RM", command="QL", lj=lj, ln=ln)
