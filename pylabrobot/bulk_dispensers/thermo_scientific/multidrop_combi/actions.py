"""Control operations mixin for the Multidrop Combi."""

from __future__ import annotations

from typing import Optional

from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.errors import (
  MultidropCombiCommunicationError,
)
from pylabrobot.io.serial import Serial


class MultidropCombiActionsMixin:
  """Mixin providing control operations for the Multidrop Combi."""

  io: Optional[Serial]

  async def abort(self) -> None:
    """Send ESC character to abort the current operation."""
    if self.io is None:
      raise MultidropCombiCommunicationError("Not connected to instrument", operation="abort")
    await self.io.write(b"\x1b")

  async def restart(self) -> None:
    """Restart the instrument (equivalent to power cycle)."""
    await self._send_command("RST", timeout=10.0)  # type: ignore[attr-defined]

  async def acknowledge_error(self) -> None:
    """Clear instrument error state."""
    await self._send_command("EAK", timeout=5.0)  # type: ignore[attr-defined]
