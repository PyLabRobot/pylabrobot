"""The PreciseFlex linear rail: the axis that carries the whole arm, driven through the controller.

Reached as `driver.rail`, on an arm that has one; None on one that does not.
"""

import dataclasses
from typing import TYPE_CHECKING, Optional

from pylabrobot.events import evented_operation

if TYPE_CHECKING:
  from ..master import PreciseFlexDriver


@dataclasses.dataclass(frozen=True)
class PreciseFlexRailConfiguration:
  """The rail axis's facts, read at setup. A value is None when the controller reports none."""

  soft_limit_range: tuple
  hard_limit_range: Optional[tuple] = None
  max_speed: Optional[float] = None
  max_acceleration: Optional[float] = None
  max_deceleration: Optional[float] = None


class PreciseFlexRail:
  """The linear rail a PreciseFlex arm rides on (`Axis.RAIL`).

  Reached as `driver.rail`. Sends through the driver's `send_command`. A rail move goes through a
  stored station: its rail position is set, then the rail moves to it.
  """

  def __init__(self, driver: "PreciseFlexDriver") -> None:
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver
    self.configuration: Optional[PreciseFlexRailConfiguration] = None
    self._rail_position_index = 1

  # -- stations: a rail move goes to a stored station's rail position ------------------------------

  async def _unchecked_fw_set_rail_position(self, station_id: int, rail_position: float) -> None:
    """Set the rail position for the specified station.

    Args:
      station_id: The station index.
      rail_position: The rail position in mm.
    """
    await self._driver.send_command(f"Rail {station_id} {rail_position}")

  async def _unchecked_fw_move_rail(self, station_id: Optional[int] = None, mode: int = 1) -> None:
    """Move the rail to the position stored at the specified station.

    Args:
      station_id: The station index whose rail position to move to.
      mode: Motion mode (0 = normal).
    """
    if station_id is not None:
      await self._driver.send_command(f"MoveRail {station_id} {mode}")
    else:
      await self._driver.send_command(f"MoveRail {mode}")

  # -- rail motion ---------------------------------------------------------------------------------

  @evented_operation(
    "precise_flex.move_rail",
    lambda self, rail_position: {
      "device": self._driver._controller_reference(),
      "rail_position": float(rail_position),
    },
  )
  async def move_rail(self, rail_position: float) -> None:
    """Move the rail to the specified position.

    Args:
      rail_position: Rail destination in mm.
    """
    await self._driver.arm._wait_for_eom()
    await self._unchecked_fw_set_rail_position(self._rail_position_index, rail_position)
    await self._unchecked_fw_move_rail(station_id=self._rail_position_index)
