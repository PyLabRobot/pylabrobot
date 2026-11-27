"""The drop sensor: a v4's sensor at the waste that reports each liquid drop falling through it.

Setup enables it through the LC3 board (board 12, command 53), as every captured v4 startup does.
Each drop then turns board 0's input ``DROP_SENSOR_INPUT`` on, then off about 50 ms later
(``INPUT_CHANGED_EVENT``); the driver counts each turn on. A prime reads the count.
"""

import struct
from typing import TYPE_CHECKING

from ..framing import LC3_BOARD

if TYPE_CHECKING:
  from ..master import MantisDriver

# The input a drop turns on: board 0's input 0, in every captured prime.
DROP_SENSOR_INPUT = 0


class DropSensor:
  """The drop sensor, built on a v4 whose board 12 answers."""

  def __init__(self, *, driver: "MantisDriver"):
    """
    Args:
      driver: the driver it sends through, and which hands it the input events.
    """
    self._driver = driver
    # The drops seen since it was built or reset: each turn on of DROP_SENSOR_INPUT.
    self._drop_count = 0
    # The prime pump cycles in a row, up to the last, that each saw a drop; a prime sets it.
    self._consecutive_run = 0

  async def _unchecked_fw_enable_drop_detection(self) -> None:
    """Enable the sensor (board 12, command 53, a 32-bit 1), sent once, as every captured v4
    startup sends it."""
    await self._driver._send_command(
      board=LC3_BOARD, command=53, payload=struct.pack("<I", 1), attempts=1
    )

  def _count_input_changed(self, *, input_number: int, on: bool) -> None:
    """Count a drop if ``input_number`` is ``DROP_SENSOR_INPUT`` and it turned on.

    Args:
      input_number: the input an input-changed event names.
      on: whether it turned on.
    """
    if input_number == DROP_SENSOR_INPUT and on:
      self._drop_count += 1

  def _get_drop_count(self) -> int:
    """The drops seen since the sensor was built or reset."""
    return self._drop_count

  @property
  def drop_detected(self) -> bool:
    """Whether the sensor saw a drop since it was built or reset."""
    return self._get_drop_count() > 0

  @property
  def consecutive_run(self) -> int:
    """The prime pump cycles in a row, up to the last, that each saw a drop in a drop-detection
    prime; 0 after a reset."""
    return self._consecutive_run

  def reset(self) -> None:
    """Forget the drops seen and the run; every prime resets the sensor first."""
    self._drop_count = 0
    self._consecutive_run = 0
