"""NimbusChatterboxDriver: no-op driver for testing without hardware."""

from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.device import Driver
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.packets import Address

from .driver import NimbusDriver

logger = logging.getLogger(__name__)


class NimbusChatterboxDriver(NimbusDriver):
  """No-op driver for testing Nimbus without hardware.

  Skips TCP I/O, uses canned instrument addresses, and logs commands
  instead of sending them over the wire.
  """

  def __init__(self, num_channels: int = 8):
    # Skip NimbusDriver.__init__ (which creates a Socket) — go straight to Driver.
    Driver.__init__(self)

    self._connected = False
    self.auto_reconnect = False
    self.max_reconnect_attempts = 0
    self._reconnect_attempts = 0
    self._client_id = 1
    self.client_address = Address(2, 1, 65535)
    self._sequence_numbers = {}
    self._discovered_objects = {}

    # Canned instrument addresses
    self._pipette_address = Address(1, 1, 257)
    self._door_lock_address = Address(1, 1, 268)
    self._nimbus_core_address = Address(1, 1, 48896)
    self._num_channels = num_channels

    self.deck = None
    self.io = None  # No socket for chatterbox

  def serialize(self) -> dict:
    return {"type": "NimbusChatterboxDriver", "num_channels": self._num_channels}

  async def setup(self):
    """Set up chatterbox: create PIP backend without TCP connection."""
    from .pip_backend import NimbusPIPBackend

    self._connected = True
    self.pip = NimbusPIPBackend(self)

  async def stop(self):
    self._connected = False

  async def send_command(self, command: HamiltonCommand, timeout: float = 10.0) -> Optional[dict]:
    """Log command instead of sending over TCP. Returns canned responses."""
    logger.info(f"[CHATTERBOX] {command.__class__.__name__}: {command.get_log_params()}")

    # Return canned responses for queries
    from .commands import (
      GetChannelConfiguration,
      GetChannelConfiguration_1,
      IsDoorLocked,
      IsInitialized,
      IsTipPresent,
    )

    if isinstance(command, IsDoorLocked):
      return {"locked": False}
    if isinstance(command, IsInitialized):
      return {"initialized": True}
    if isinstance(command, IsTipPresent):
      return {"tip_present": [0] * self._num_channels}
    if isinstance(command, GetChannelConfiguration_1):
      return {"channels": self._num_channels, "channel_types": [0] * self._num_channels}
    if isinstance(command, GetChannelConfiguration):
      return {"enabled": [False]}

    return None

  async def write(self, data: bytes, timeout: Optional[float] = None):
    pass

  async def read(self, num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
    return b""

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    return b"\x00" * num_bytes
