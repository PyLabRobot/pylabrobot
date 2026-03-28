"""Firmware command wrapper for the Tecan EVO LiHa (Liquid Handling Arm).

Provides typed methods for all LiHa firmware commands (plunger control,
valve positioning, Z-axis movement, liquid detection, tip handling).
"""

from __future__ import annotations

from typing import List, Optional

from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware.arm_base import EVOArm


class LiHa(EVOArm):
  """Firmware commands for the LiHa (Liquid Handling Arm)."""

  async def initialize_plunger(self, tips: int) -> None:
    """Initializes plunger and valve drive.

    Args:
      tips: binary coded tip select
    """
    await self.interface.send_command(module=self.module, command="PID", params=[tips])

  async def report_z_param(self, param: int) -> List[int]:
    """Report current parameters for z-axis.

    Args:
      param: 0=position, 1=accel, 2=fast_speed, 3=init_speed, 4=init_offset,
             5=range, 6=encoder_deviation, 9=slow_speed, 10=scale, 11=target, 12=travel
    """
    resp: List[int] = (
      await self.interface.send_command(module=self.module, command="RPZ", params=[param])
    )["data"]
    return resp

  async def report_number_tips(self) -> int:
    """Report number of tips on arm."""
    resp: List[int] = (
      await self.interface.send_command(module=self.module, command="RNT", params=[1])
    )["data"]
    return resp[0]

  async def position_absolute_all_axis(self, x: int, y: int, ys: int, z: List[int]) -> None:
    """Position absolute for all LiHa axes.

    Args:
      x: absolute x position in 1/10 mm
      y: absolute y position in 1/10 mm
      ys: absolute y spacing in 1/10 mm (90-380)
      z: absolute z position in 1/10 mm for each channel

    Raises:
      TecanError: if moving to the target position causes a collision
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
      module=self.module, command="PAA", params=list([x, y, ys] + z)
    )
    EVOArm._pos_cache[self.module] = x

  async def position_valve_logical(self, param: List[Optional[int]]) -> None:
    """Position valve logical for each channel.

    Args:
      param: 0=outlet, 1=inlet, 2=bypass
    """
    await self.interface.send_command(module=self.module, command="PVL", params=param)

  async def set_end_speed_plunger(self, speed: List[Optional[int]]) -> None:
    """Set end speed for plungers.

    Args:
      speed: half steps/sec per channel (5-6000)
    """
    await self.interface.send_command(module=self.module, command="SEP", params=speed)

  async def move_plunger_relative(self, rel: List[Optional[int]]) -> None:
    """Move plunger relative (positive=aspirate, negative=dispense).

    Args:
      rel: full steps per channel (-3150 to 3150)
    """
    await self.interface.send_command(module=self.module, command="PPR", params=rel)

  async def set_stop_speed_plunger(self, speed: List[Optional[int]]) -> None:
    """Set stop speed for plungers.

    Args:
      speed: half steps/sec per channel (50-2700)
    """
    await self.interface.send_command(module=self.module, command="SPP", params=speed)

  async def set_detection_mode(self, proc: int, sense: int) -> None:
    """Set liquid detection mode.

    Args:
      proc: detection procedure (7 = double detection sequential)
      sense: conductivity (1 = high)
    """
    await self.interface.send_command(module=self.module, command="SDM", params=[proc, sense])

  async def set_search_speed(self, speed: List[Optional[int]]) -> None:
    """Set search speed for liquid search commands.

    Args:
      speed: 1/10 mm/s per channel (1-1500)
    """
    await self.interface.send_command(module=self.module, command="SSL", params=speed)

  async def set_search_retract_distance(self, dist: List[Optional[int]]) -> None:
    """Set z-axis retract distance for liquid search commands.

    Args:
      dist: 1/10 mm per channel
    """
    await self.interface.send_command(module=self.module, command="SDL", params=dist)

  async def set_search_submerge(self, dist: List[Optional[int]]) -> None:
    """Set submerge for liquid search commands.

    Args:
      dist: 1/10 mm per channel (-1000 to z_range)
    """
    await self.interface.send_command(module=self.module, command="SBL", params=dist)

  async def set_search_z_start(self, z: List[Optional[int]]) -> None:
    """Set z-start for liquid search commands.

    Args:
      z: 1/10 mm per channel
    """
    await self.interface.send_command(module=self.module, command="STL", params=z)

  async def set_search_z_max(self, z: List[Optional[int]]) -> None:
    """Set z-max for liquid search commands.

    Args:
      z: 1/10 mm per channel
    """
    await self.interface.send_command(module=self.module, command="SML", params=z)

  async def set_z_travel_height(self, z: List[int]) -> None:
    """Set z-travel height.

    Args:
      z: travel heights in 1/10 mm per channel
    """
    await self.interface.send_command(module=self.module, command="SHZ", params=z)

  async def move_detect_liquid(self, channels: int, zadd: List[Optional[int]]) -> None:
    """Move tip, detect liquid, submerge.

    Args:
      channels: binary coded tip select
      zadd: distance to travel downwards in 1/10 mm per channel
    """
    await self.interface.send_command(
      module=self.module,
      command="MDT",
      params=[channels] + [None] * 3 + zadd,
    )

  async def set_slow_speed_z(self, speed: List[Optional[int]]) -> None:
    """Set slow speed for z.

    Args:
      speed: 1/10 mm/s per channel (1-4000)
    """
    await self.interface.send_command(module=self.module, command="SSZ", params=speed)

  async def set_tracking_distance_z(self, rel: List[Optional[int]]) -> None:
    """Set z-axis relative tracking distance for aspirate/dispense.

    Args:
      rel: 1/10 mm per channel (-2100 to 2100)
    """
    await self.interface.send_command(module=self.module, command="STZ", params=rel)

  async def move_tracking_relative(self, rel: List[Optional[int]]) -> None:
    """Move tracking relative (synchronous Z and plunger movement).

    Args:
      rel: full steps per channel (-3150 to 3150)
    """
    await self.interface.send_command(module=self.module, command="MTR", params=rel)

  async def move_absolute_z(self, z: List[Optional[int]]) -> None:
    """Position absolute with slow speed z-axis.

    Args:
      z: absolute position in 1/10 mm per channel
    """
    await self.interface.send_command(module=self.module, command="MAZ", params=z)

  async def get_disposable_tip(self, tips: int, z_start: int, z_search: int) -> None:
    """Pick up disposable tips.

    Args:
      tips: binary coded tip select
      z_start: position in 1/10 mm where searching begins
      z_search: search distance in 1/10 mm
    """
    await self.interface.send_command(
      module=self.module,
      command="AGT",
      params=[tips, z_start, z_search, 0],
    )

  async def discard_disposable_tip_high(self, tips: int) -> None:
    """Discard tips at Z-axis initialization height.

    Args:
      tips: binary coded tip select
    """
    await self.interface.send_command(module=self.module, command="ADT", params=[tips])

  async def drop_disposable_tip(self, tips: int, discard_height: int) -> None:
    """Discard tips at variable height.

    Args:
      tips: binary coded tip select
      discard_height: 0=above tip rack, 1=in tip rack
    """
    await self.interface.send_command(
      module=self.module, command="AST", params=[tips, discard_height]
    )
