"""VantageLoadingCover: Loading cover control for Hamilton Vantage liquid handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from pylabrobot.hamilton.liquid_handlers.vantage.driver import VantageDriver

logger = logging.getLogger(__name__)


class VantageLoadingCover:
  """Controls the loading cover on a Hamilton Vantage.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for loading cover control and delegates I/O to the driver.

  Args:
    driver: The VantageDriver instance to send commands through.
  """

  def __init__(self, driver: "VantageDriver"):
    self._driver = driver

  async def set_cover(self, cover_open: bool):
    """Set the loading cover.

    Args:
      cover_open: Whether the cover should be open or closed.
    """
    return await self._driver.send_command(module="I1AM", command="LP", lp=not cover_open)

  async def request_initialization_status(self) -> bool:
    """Request the loading cover initialization status.

    This command was based on the STAR command (QW) and the VStarTranslator log.

    Returns:
      True if the cover module is initialized, False otherwise.
    """
    resp = await self._driver.send_command(module="I1AM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def initialize(self):
    """Initialize the loading cover."""
    return await self._driver.send_command(module="I1AM", command="MI")
