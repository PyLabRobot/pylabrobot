"""The drawer: carries a plate in and out of the reader."""

import logging
from typing import TYPE_CHECKING, Optional

from pylabrobot.resources import Coordinate, PlateHolder

if TYPE_CHECKING:
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)


class Drawer:
  """The drawer (DRAWER command family, 0x03).

  Reached as `driver.drawer`. With `plate_holder`, `location_in` and `location_out` set, every move
  and every `sense_drawer_open` updates the plate holder to where the drawer is.

  Attributes:
    drawer_open: whether the drawer was out when last sensed; None until it is first sensed.
  """

  # Seconds a move may take: about 2.5x the slowest close measured (7.9 s); an open takes 4.2 s.
  default_move_timeout: float = 20.0
  # Seconds between status polls while the drawer moves; as the driver's `default_poll_interval`.
  default_poll_interval: float = 0.25

  def __init__(self, driver: "CLARIOstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver
    self.plate_holder: Optional[PlateHolder] = None
    self.location_in: Optional[Coordinate] = None
    self.location_out: Optional[Coordinate] = None
    self.drawer_open: Optional[bool] = None

  def _update_location(self, drawer_open: bool) -> None:
    """Move the plate holder to where the drawer is; does nothing until all three are set."""
    if self.plate_holder is None or self.location_in is None or self.location_out is None:
      return
    self.plate_holder.location = self.location_out if drawer_open else self.location_in

  async def sense_drawer_open(self) -> bool:
    """Sense whether the drawer is out, and update the plate holder to match."""
    drawer_open = (await self._driver.request_status()).drawer_open
    self.drawer_open = drawer_open
    self._update_location(drawer_open)
    return drawer_open

  async def sense_plate_present(self) -> bool:
    """Sense whether the reader detects a plate."""
    return (await self._driver.request_status()).plate_detected

  async def _unchecked_fw_open(self) -> None:
    """Send DRAWER open."""
    await self._driver.send_command(
      self._driver.CommandFamily.DRAWER,
      self._driver.Command.DRAWER_OPEN,
      parameters=b"\x00\x00\x00\x00",
    )

  async def _unchecked_fw_close(self) -> None:
    """Send DRAWER close."""
    await self._driver.send_command(
      self._driver.CommandFamily.DRAWER,
      self._driver.Command.DRAWER_CLOSE,
      parameters=b"\x00\x00\x00\x00",
    )

  async def _move(self, open_drawer: bool) -> None:
    """Move the drawer, wait until it is where it was told to go, then sense where it is.

    A move is done when the reader is idle and `drawer_open` reads the target: `busy` alone is
    shared with measurements, and `drawer_open` reaches the target before the drawer comes to rest.

    Raises:
      TimeoutError: if the drawer is not there within `default_move_timeout` seconds.
    """
    try:
      if open_drawer:
        await self._unchecked_fw_open()
      else:
        await self._unchecked_fw_close()
      await self._driver._wait_until_ready(
        timeout=self.default_move_timeout,
        poll_interval=self.default_poll_interval,
        until=lambda status: not status.busy and status.drawer_open == open_drawer,
      )
    finally:
      # A move that stopped part way left the drawer anywhere; a failed read must not replace
      # the move's exception.
      try:
        await self.sense_drawer_open()
      except Exception:
        logger.warning("could not sense whether the drawer is open; its model is stale")

  async def open(self) -> None:
    """Move the drawer out (about 4 s)."""
    await self._move(open_drawer=True)
    logger.info("drawer open")

  async def close(self) -> None:
    """Move the drawer in (about 8 s)."""
    await self._move(open_drawer=False)
    logger.info("drawer closed")
