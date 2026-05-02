"""NimbusDoor: door control subsystem for Hamilton Nimbus."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .commands import IsDoorLocked, LockDoor, UnlockDoor

if TYPE_CHECKING:
  from .driver import NimbusDriver

logger = logging.getLogger(__name__)


class NimbusDoor:
  """Controls the door on a Hamilton Nimbus.

  Plain helper class (not a CapabilityBackend), following the STARCover pattern.
  Owned by NimbusDriver, exposed via convenience methods on the Nimbus device.
  """

  def __init__(self, driver: "NimbusDriver"):
    self.driver = driver

  async def _on_setup(self):
    """Lock door on setup if available."""
    try:
      if not await self.is_locked():
        await self.lock()
      else:
        logger.info("Door already locked")
    except Exception as e:
      logger.warning(f"Door operations skipped during setup: {e}")

  async def _on_stop(self):
    pass

  async def is_locked(self) -> bool:
    """Check if the door is locked."""
    status = await self.driver.send_command(IsDoorLocked())
    assert status is not None, "IsDoorLocked command returned None"
    return bool(status.locked)

  async def lock(self) -> None:
    """Lock the door."""
    await self.driver.send_command(LockDoor())
    logger.info("Door locked successfully")

  async def unlock(self) -> None:
    """Unlock the door."""
    await self.driver.send_command(UnlockDoor())
    logger.info("Door unlocked successfully")
