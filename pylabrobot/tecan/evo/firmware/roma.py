"""Firmware command wrapper for the Tecan EVO RoMa (Robotic Manipulator Arm).

Provides typed methods for RoMa firmware commands (vector positioning,
gripper control, speed configuration).
"""

from __future__ import annotations

from typing import List, Optional

from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware.arm_base import EVOArm


class RoMa(EVOArm):
  """Firmware commands for the RoMa (Robotic Manipulator Arm)."""

  async def report_z_param(self, param: int) -> int:
    """Report current parameter for z-axis.

    Args:
      param: 0=current position, 5=actual machine range
    """
    resp: List[int] = (
      await self.interface.send_command(module=self.module, command="RPZ", params=[param])
    )["data"]
    return resp[0]

  async def report_r_param(self, param: int) -> int:
    """Report current parameter for r-axis (rotation).

    Args:
      param: 0=current position, 5=actual machine range
    """
    resp: List[int] = (
      await self.interface.send_command(module=self.module, command="RPR", params=[param])
    )["data"]
    return resp[0]

  async def report_g_param(self, param: int) -> int:
    """Report current parameter for g-axis (gripper).

    Args:
      param: 0=current position, 5=actual machine range
    """
    resp: List[int] = (
      await self.interface.send_command(module=self.module, command="RPG", params=[param])
    )["data"]
    return resp[0]

  async def set_smooth_move_x(self, mode: int) -> None:
    """Set X-axis smooth move mode.

    Args:
      mode: 0=active (recalculate accel/speed by distance), 1=use SFX parameters directly
    """
    await self.interface.send_command(module=self.module, command="SSM", params=[mode])

  async def set_fast_speed_x(self, speed: Optional[int], accel: Optional[int] = None) -> None:
    """Set fast speed and acceleration for X-axis.

    Args:
      speed: 1/10 mm/s
      accel: 1/10 mm/s^2
    """
    await self.interface.send_command(module=self.module, command="SFX", params=[speed, accel])

  async def set_fast_speed_y(self, speed: Optional[int], accel: Optional[int] = None) -> None:
    """Set fast speed and acceleration for Y-axis.

    Args:
      speed: 1/10 mm/s
      accel: 1/10 mm/s^2
    """
    await self.interface.send_command(module=self.module, command="SFY", params=[speed, accel])

  async def set_fast_speed_z(self, speed: Optional[int], accel: Optional[int] = None) -> None:
    """Set fast speed and acceleration for Z-axis.

    Args:
      speed: 1/10 mm/s
      accel: 1/10 mm/s^2
    """
    await self.interface.send_command(module=self.module, command="SFZ", params=[speed, accel])

  async def set_fast_speed_r(self, speed: Optional[int], accel: Optional[int] = None) -> None:
    """Set fast speed and acceleration for R-axis (rotation).

    Args:
      speed: 1/10 deg/s
      accel: 1/10 deg/s^2
    """
    await self.interface.send_command(module=self.module, command="SFR", params=[speed, accel])

  async def set_vector_coordinate_position(
    self,
    v: int,
    x: int,
    y: int,
    z: int,
    r: int,
    g: Optional[int],
    speed: int,
    tw: int = 0,
  ) -> None:
    """Set vector coordinate positions into table.

    Args:
      v: vector index (1-100)
      x: absolute x in 1/10 mm
      y: absolute y in 1/10 mm
      z: absolute z in 1/10 mm
      r: absolute r in 1/10 deg
      g: absolute gripper in 1/10 mm (optional)
      speed: 0=slow, 1=fast
      tw: target window class (set with STW)

    Raises:
      TecanError: if movement would cause collision with another arm
    """
    cur_x = EVOArm._pos_cache.setdefault(self.module, await self.report_x_param(0))
    for module, pos in EVOArm._pos_cache.items():
      if module == self.module:
        continue
      if cur_x < x and cur_x < pos < x:
        raise TecanError("Invalid command (collision)", self.module, 2)
      if cur_x > x and cur_x > pos > x:
        raise TecanError("Invalid command (collision)", self.module, 2)
      if abs(pos - x) < 1500:
        raise TecanError("Invalid command (collision)", self.module, 2)

    await self.interface.send_command(
      module=self.module,
      command="SAA",
      params=[v, x, y, z, r, g, speed, 0, tw],
    )

  async def action_move_vector_coordinate_position(self) -> None:
    """Start coordinate movement built by the vector table."""
    await self.interface.send_command(module=self.module, command="AAC")
    EVOArm._pos_cache[self.module] = await self.report_x_param(0)

  async def position_absolute_g(self, g: int) -> None:
    """Move gripper to absolute position.

    Args:
      g: absolute position in 1/10 mm
    """
    await self.interface.send_command(module=self.module, command="PAG", params=[g])

  async def set_gripper_params(self, speed: int, pwm: int, cur: Optional[int] = None) -> None:
    """Set gripper parameters.

    Args:
      speed: search speed in 1/10 mm/s
      pwm: pulse width modification limit
      cur: max current (optional)
    """
    await self.interface.send_command(module=self.module, command="SGG", params=[speed, pwm, cur])

  async def grip_plate(self, pos: int) -> None:
    """Grip plate at current X/Y/Z/R position.

    Args:
      pos: target position — plate must be found between current and target
    """
    await self.interface.send_command(module=self.module, command="AGR", params=[pos])

  async def set_target_window_class(self, wc: int, x: int, y: int, z: int, r: int, g: int) -> None:
    """Set drive parameters for AAC command.

    Args:
      wc: window class (1-100)
      x: target window for x-axis in 1/10 mm
      y: target window for y-axis in 1/10 mm
      z: target window for z-axis in 1/10 mm
      r: target window for r-axis in 1/10 deg
      g: target window for g-axis in 1/10 mm
    """
    await self.interface.send_command(module=self.module, command="STW", params=[wc, x, y, z, r, g])
