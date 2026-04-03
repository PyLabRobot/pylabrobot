"""VantageLoadingCover: loading cover control for Hamilton Vantage liquid handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from .driver import VantageDriver


class VantageLoadingCover:
  """Controls the loading cover on a Hamilton Vantage.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for loading cover control and delegates I/O to the driver.
  """

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver

  async def _on_setup(self):
    pass

  async def _on_stop(self):
    pass

  # -- commands (I1AM) -------------------------------------------------------

  async def request_initialization_status(self) -> bool:
    """Check if the loading cover module is initialized (I1AM:QW).

    Returns:
      True if initialized, False otherwise.
    """
    resp = await self.driver.send_command(module="I1AM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def initialize(self) -> None:
    """Initialize the loading cover module (I1AM:MI)."""
    await self.driver.send_command(module="I1AM", command="MI")

  async def set_cover(self, cover_open: bool) -> None:
    """Open or close the loading cover (I1AM:LP).

    Args:
      cover_open: True to open, False to close.
    """
    await self.driver.send_command(module="I1AM", command="LP", lp=not cover_open)

  async def open(self) -> None:
    """Open the loading cover."""
    await self.set_cover(cover_open=True)

  async def close(self) -> None:
    """Close the loading cover."""
    await self.set_cover(cover_open=False)
