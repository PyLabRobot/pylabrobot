"""NimbusChatterboxDriver: prints commands instead of sending them over TCP."""

from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

from .door import NimbusDoor
from .driver import NimbusDriver

logger = logging.getLogger(__name__)


class NimbusChatterboxDriver(NimbusDriver):
  """Chatterbox driver for Nimbus. Simulates commands for testing without hardware.

  Inherits NimbusDriver but overrides setup/stop/send_command to skip TCP
  and use canned addresses and responses instead.
  """

  def __init__(self, deck: NimbusDeck, num_channels: int = 8):
    # Pass dummy host — Socket is created but never opened
    super().__init__(deck=deck, host="chatterbox", port=2000)
    self._num_channels = num_channels

  async def setup(self):
    from .pip_backend import NimbusPIPBackend

    # Use canned addresses (skip TCP connection entirely)
    pipette_address = Address(1, 1, 257)
    self._nimbus_core_address = Address(1, 1, 48896)
    door_address = Address(1, 1, 268)

    self.pip = NimbusPIPBackend(
      driver=self, deck=self.deck, address=pipette_address, num_channels=self._num_channels
    )
    self.door = NimbusDoor(driver=self, address=door_address)

  async def stop(self):
    if self.door is not None:
      await self.door._on_stop()
    self.door = None

  async def send_command(self, command: HamiltonCommand, timeout: float = 10.0) -> Optional[dict]:
    logger.info(f"[Chatterbox] {command.__class__.__name__}")

    # Return canned responses for commands that need them
    from .commands import (
      GetChannelConfiguration,
      GetChannelConfiguration_1,
      IsDoorLocked,
      IsInitialized,
      IsTipPresent,
    )

    if isinstance(command, GetChannelConfiguration_1):
      return {"channels": self._num_channels}
    if isinstance(command, IsInitialized):
      return {"initialized": True}
    if isinstance(command, IsTipPresent):
      return {"tip_present": [False] * self._num_channels}
    if isinstance(command, IsDoorLocked):
      return {"locked": True}
    if isinstance(command, GetChannelConfiguration):
      return {"enabled": [False]}
    return None
