"""STARCover: cover and port control for Hamilton STAR liquid handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from .driver import STARDriver

logger = logging.getLogger(__name__)


class STARCover:
  """Controls the cover and port outputs on a Hamilton STAR.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for the cover control subsystem and delegates I/O to the driver.
  """

  def __init__(self, driver: "STARDriver"):
    self.driver = driver

  # -- lifecycle -------------------------------------------------------------

  async def _on_setup(self, backend_params=None):
    pass

  async def _on_stop(self):
    pass

  # -- commands --------------------------------------------------------------

  async def lock(self):
    """Lock cover (C0:CO)."""
    return await self.driver.send_command(module="C0", command="CO")

  async def unlock(self):
    """Unlock cover (C0:HO)."""
    return await self.driver.send_command(module="C0", command="HO")

  async def disable(self):
    """Disable cover control (C0:CD)."""
    return await self.driver.send_command(module="C0", command="CD")

  async def enable(self):
    """Enable cover control (C0:CE)."""
    return await self.driver.send_command(module="C0", command="CE")

  async def set_output(self, output: int = 1):
    """Set cover output (C0:OS).

    Args:
      output: 1 = cover lock; 2 = reserve out; 3 = reserve out.
    """
    if not 1 <= output <= 3:
      raise ValueError("output must be between 1 and 3")
    return await self.driver.send_command(module="C0", command="OS", on=output)

  async def reset_output(self, output: int = 1):
    """Reset output (C0:QS).

    Args:
      output: 1 = cover lock; 2 = reserve out; 3 = reserve out.
    """
    if not 1 <= output <= 3:
      raise ValueError("output must be between 1 and 3")
    return await self.driver.send_command(module="C0", command="QS", on=output, fmt="#")

  async def is_open(self) -> bool:
    """Request whether the cover is open (C0:QC).

    Returns:
      True if the cover is open.
    """
    resp = await self.driver.send_command(module="C0", command="QC", fmt="qc#")
    return bool(resp["qc"])
