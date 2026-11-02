"""The drawer: carries a plate in and out of the reader."""

import logging
from typing import TYPE_CHECKING, Optional

from pylabrobot.resources import PlateHolder

if TYPE_CHECKING:
  from ..master import VANTAstarDriver

logger = logging.getLogger(__name__)


class MovePlateMode:
  """PlateCommand byte 1."""

  PLATE_IN = 0x00
  PLATE_OUT = 0x01


class Drawer:
  """The drawer (PlateCommand).

  Reached as ``driver.drawer``.

  Attributes:
    drawer_open: whether the drawer was out when last sensed; None until it is first sensed.
    plate_holder: the plate holder the drawer carries (set by ``VANTAstarDevice``); the measurement
      features read the plate from it.
  """

  # Seconds a move may take: an open takes about 7 s, a close about 10 s.
  default_move_timeout: float = 20.0

  def __init__(self, driver: "VANTAstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver
    self.drawer_open: Optional[bool] = None
    self.plate_holder: Optional[PlateHolder] = None

  async def sense_drawer_open(self) -> bool:
    """Sense whether the drawer is out."""
    self.drawer_open = (await self._driver.request_status()).drawer_open
    return self.drawer_open

  async def sense_plate_present(self) -> bool:
    """Sense whether the reader detects a plate.  It detects one only with the drawer in: with the
    drawer out it reports none, also with a plate on the drawer."""
    return (await self._driver.request_status()).plate_detected

  async def _move(self, open_drawer: bool) -> None:
    """Move the drawer and wait until it is where it was told to go and the reader is idle.

    Raises:
      TimeoutError: if the drawer is not there within ``default_move_timeout`` seconds.
      CommandRefusedError: if the reader refuses the move.
    """
    mode = MovePlateMode.PLATE_OUT if open_drawer else MovePlateMode.PLATE_IN
    await self._driver._move_plate(mode)
    status = await self._driver._wait_until_ready(
      timeout=self.default_move_timeout,
      poll_interval=self._driver.default_poll_interval,
      until=lambda s: not s.busy and s.drawer_open == open_drawer,
    )
    self.drawer_open = status.drawer_open

  async def open(self) -> None:
    """Move the drawer out (about 7 s)."""
    await self._move(open_drawer=True)
    logger.info("drawer open")

  async def close(self) -> None:
    """Move the drawer in (about 10 s)."""
    await self._move(open_drawer=False)
    logger.info("drawer closed")
