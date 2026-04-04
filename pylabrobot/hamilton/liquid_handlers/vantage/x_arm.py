"""VantageXArm: X-arm positioning control for Hamilton Vantage liquid handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from pylabrobot.hamilton.liquid_handlers.vantage.driver import VantageDriver

logger = logging.getLogger(__name__)


class VantageXArm:
  """Controls the X-arm on a Hamilton Vantage.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for X-arm positioning and delegates I/O to the driver.

  Args:
    driver: The VantageDriver instance to send commands through.
  """

  def __init__(self, driver: "VantageDriver"):
    self._driver = driver

  async def initialize(self):
    """Initialize the X-arm."""
    return await self._driver.send_command(module="A1XM", command="XI")

  async def move_to_x_position(
    self,
    x_position: int = 5000,
    x_speed: int = 25000,
  ):
    """Move arm to X position.

    Args:
      x_position: X Position [0.1mm].
      x_speed: X speed [0.1mm/s].
    """
    if not -50000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -50000 to 50000")
    if not 1 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 1 to 25000")

    return await self._driver.send_command(
      module="A1XM",
      command="XP",
      xp=x_position,
      xv=x_speed,
    )

  async def move_to_x_position_with_all_attached_components_in_z_safety_position(
    self,
    x_position: int = 5000,
    x_speed: int = 25000,
    TODO_XA_1: int = 1,
  ):
    """Move arm to X position with all attached components in Z safety position.

    Args:
      x_position: X Position [0.1mm].
      x_speed: X speed [0.1mm/s].
      TODO_XA_1: (0).
    """
    if not -50000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -50000 to 50000")
    if not 1 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 1 to 25000")
    if not 1 <= TODO_XA_1 <= 25000:
      raise ValueError("TODO_XA_1 must be in range 1 to 25000")

    return await self._driver.send_command(
      module="A1XM",
      command="XA",
      xp=x_position,
      xv=x_speed,
      xx=TODO_XA_1,
    )

  async def move_arm_relatively_in_x(
    self,
    x_search_distance: int = 0,
    x_speed: int = 25000,
    TODO_XS_1: int = 1,
  ):
    """Move arm relatively in X.

    Args:
      x_search_distance: X search distance [0.1mm].
      x_speed: X speed [0.1mm/s].
      TODO_XS_1: (0).
    """
    if not -50000 <= x_search_distance <= 50000:
      raise ValueError("x_search_distance must be in range -50000 to 50000")
    if not 1 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 1 to 25000")
    if not 1 <= TODO_XS_1 <= 25000:
      raise ValueError("TODO_XS_1 must be in range 1 to 25000")

    return await self._driver.send_command(
      module="A1XM",
      command="XS",
      xs=x_search_distance,
      xv=x_speed,
      xx=TODO_XS_1,
    )

  async def search_x_for_teach_signal(
    self,
    x_search_distance: int = 0,
    x_speed: int = 25000,
    TODO_XT_1: int = 1,
  ):
    """Search X for teach signal.

    Args:
      x_search_distance: X search distance [0.1mm].
      x_speed: X speed [0.1mm/s].
      TODO_XT_1: (0).
    """
    if not -50000 <= x_search_distance <= 50000:
      raise ValueError("x_search_distance must be in range -50000 to 50000")
    if not 1 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 1 to 25000")
    if not 1 <= TODO_XT_1 <= 25000:
      raise ValueError("TODO_XT_1 must be in range 1 to 25000")

    return await self._driver.send_command(
      module="A1XM",
      command="XT",
      xs=x_search_distance,
      xv=x_speed,
      xx=TODO_XT_1,
    )

  async def set_x_drive_angle_of_alignment(
    self,
    TODO_XL_1: int = 1,
  ):
    """Set X drive angle of alignment.

    Args:
      TODO_XL_1: (0).
    """
    if not 1 <= TODO_XL_1 <= 1:
      raise ValueError("TODO_XL_1 must be in range 1 to 1")

    return await self._driver.send_command(
      module="A1XM",
      command="XL",
      xl=TODO_XL_1,
    )

  async def turn_x_drive_off(self):
    """Turn X drive off."""
    return await self._driver.send_command(module="A1XM", command="XO")

  async def send_message_to_motion_controller(
    self,
    TODO_BD_1: str = "",
  ):
    """Send message to motion controller.

    Args:
      TODO_BD_1: (0).
    """
    return await self._driver.send_command(
      module="A1XM",
      command="BD",
      bd=TODO_BD_1,
    )

  async def set_any_parameter_within_this_module(
    self,
    TODO_AA_1: int = 0,
    TODO_AA_2: int = 1,
  ):
    """Set any parameter within this module.

    Args:
      TODO_AA_1: (0).
      TODO_AA_2: (0).
    """
    return await self._driver.send_command(
      module="A1XM",
      command="AA",
      xm=TODO_AA_1,
      xt=TODO_AA_2,
    )

  async def request_arm_x_position(self):
    """Request arm X position.

    This returns a list, of which the first value is one that can be used with
    :meth:`move_to_x_position`.
    """
    return await self._driver.send_command(module="A1XM", command="RX")

  async def request_error_code(self):
    """Request X-arm error code."""
    return await self._driver.send_command(module="A1XM", command="RE")

  async def request_x_drive_recorded_data(
    self,
    TODO_QL_1: int = 0,
    TODO_QL_2: int = 0,
  ):
    """Request X drive recorded data.

    Args:
      TODO_QL_1: (0).
      TODO_QL_2: (0).
    """
    return await self._driver.send_command(
      module="A1RM",
      command="QL",
      lj=TODO_QL_1,
      ln=TODO_QL_2,
    )
