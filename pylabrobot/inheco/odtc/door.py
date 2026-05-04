"""ODTC door (motorized lid) backend.

The ODTC door is controlled via OpenDoor / CloseDoor SiLA commands.
The device firmware (IOdtcCommands) exposes no door-state query — only
the two actuator commands. State is therefore tracked locally.

State model:
- None  = unknown (initial, or after (re)connect — physical state may have changed)
- True  = open  (set after a successful open() call this session)
- False = closed (set after a successful close() call this session)

Query odtc.door.backend.is_open to read state; raises DoorStateUnknownError
if neither open() nor close() has been called since the last setup().
"""

from __future__ import annotations

from typing import Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.loading_tray.backend import LoadingTrayBackend

from .driver import ODTCDriver


class DoorStateUnknownError(RuntimeError):
  """Door state is unknown: neither open() nor close() has been called this session.

  State is reset to unknown whenever the device connection is (re)established,
  because the physical door position may have changed while disconnected.
  Call ``odtc.door.open()`` or ``odtc.door.close()`` to establish known state.
  """


class ODTCDoorBackend(LoadingTrayBackend):
  """LoadingTrayBackend for the ODTC motorized door.

  Wraps the OpenDoor / CloseDoor SiLA commands and tracks door state locally
  (the ODTC firmware provides no state-query command).
  """

  def __init__(self, driver: ODTCDriver) -> None:
    self._driver = driver
    self._is_open: Optional[bool] = None

  async def _on_setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Reset door state to unknown on every (re)connect."""
    self._is_open = None

  @property
  def is_open(self) -> bool:
    """Return True if door is open, False if closed.

    Raises:
      DoorStateUnknownError: If neither open() nor close() has been called
        since the last setup(). Call one of those first to establish state.
    """
    if self._is_open is None:
      raise DoorStateUnknownError(
        "Door state is unknown. Call odtc.door.open() or odtc.door.close() first."
      )
    return self._is_open

  async def open(self, backend_params: Optional[BackendParams] = None) -> None:
    """Open the door. Updates tracked state to open on success."""
    await self._driver.send_command("OpenDoor")
    self._is_open = True

  async def close(self, backend_params: Optional[BackendParams] = None) -> None:
    """Close the door. Updates tracked state to closed on success."""
    await self._driver.send_command("CloseDoor")
    self._is_open = False
