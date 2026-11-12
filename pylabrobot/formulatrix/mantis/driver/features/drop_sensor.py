"""The drop sensor (v4): a sensor over the waste station that sees each drop of liquid, not air.

It reports each drop on input 0 of board 0, as an input-changed event that turns on, then off
about 50 ms later; the driver hands each one turning on to it. Setup enables it on board 12, as in
the recorded v4 startup; a prime counts what it sees.
"""

import logging
import struct
import time
from typing import TYPE_CHECKING

from ..framing import LC3_BOARD

if TYPE_CHECKING:
  from ..master import MantisDriver

logger = logging.getLogger(__name__)

# The input of board 0 the sensor reports on.
DROP_SENSOR_INPUT = 0
# Seconds between two drops past which the run of consecutive drops starts again: drops of a steady
# flow come about 160 ms apart; a longer gap is air between them.
DROP_GAP = 0.5


class DropSensor:
  """The drop sensor, built when board 12 answers on a v4.

  ``drop_detected`` and ``consecutive_drops`` count the drops seen since the last ``reset``.
  """

  def __init__(self, *, driver: "MantisDriver"):
    """
    Args:
      driver: the driver it sends through, and which hands it the sensor's input events.
    """
    self._driver = driver
    self._drop_detected = False
    self._consecutive_drops = 0
    self._last_drop_time = 0.0

  async def _unchecked_fw_set_drop_detection(self, *, value: int) -> None:
    """Board 12, command 53, sent once: 1 enables the sensor's input, as in the recorded v4."""
    await self._driver._send_command(
      board=LC3_BOARD, command=53, payload=struct.pack("<I", value), attempts=1
    )

  async def initialize(self) -> None:
    """Enable the sensor's input, once, as in the recorded v4 startup."""
    await self._unchecked_fw_set_drop_detection(value=1)

  @property
  def drop_detected(self) -> bool:
    """Whether a drop was seen since the last ``reset``."""
    return self._drop_detected

  @property
  def consecutive_drops(self) -> int:
    """The drops seen in a row, each within ``DROP_GAP`` of the one before, since ``reset``."""
    return self._consecutive_drops

  def reset(self) -> None:
    """Forget every drop seen; a prime resets the sensor before it pumps."""
    self._drop_detected = False
    self._consecutive_drops = 0
    self._last_drop_time = 0.0

  def _update_drop_count(self) -> None:
    """Count one drop: the driver calls it on each input event that turns the sensor's input on.

    A drop within ``DROP_GAP`` of the last one extends the run; a later one starts it again at 1.
    """
    now = time.monotonic()
    if self._consecutive_drops > 0 and now - self._last_drop_time <= DROP_GAP:
      self._consecutive_drops += 1
    else:
      self._consecutive_drops = 1
    self._last_drop_time = now
    self._drop_detected = True
