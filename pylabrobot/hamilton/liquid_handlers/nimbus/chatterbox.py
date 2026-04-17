"""NimbusChatterboxDriver: prints commands instead of sending them over TCP."""

from __future__ import annotations

import logging
from typing import Any, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.hamilton.tcp.commands import TCPCommand
from pylabrobot.hamilton.tcp.packets import Address

from .door import NimbusDoor
from .driver import NimbusDriver, NimbusResolvedInterfaces, NimbusSetupParams

logger = logging.getLogger(__name__)


class NimbusChatterboxDriver(NimbusDriver):
  """Chatterbox driver for Nimbus. Simulates commands for testing without hardware.

  Inherits NimbusDriver but overrides setup/stop/send_command to skip TCP
  and use canned addresses and responses instead.
  """

  def __init__(self, num_channels: int = 8):
    # Pass dummy host — Socket is created but never opened
    super().__init__(host="chatterbox", port=2000)
    self._num_channels = num_channels

  async def setup(self, backend_params: Optional[BackendParams] = None):
    from .pip_backend import NimbusPIPBackend

    if backend_params is None:
      params = NimbusSetupParams()
    elif isinstance(backend_params, NimbusSetupParams):
      params = backend_params
    else:
      raise TypeError(
        "NimbusChatterboxDriver.setup expected NimbusSetupParams | None for backend_params, "
        f"got {type(backend_params).__name__}"
      )

    # Use canned addresses (skip TCP connection entirely)
    pipette_address = Address(1, 1, 257)
    nimbus_core_address = Address(1, 1, 48896)
    self._nimbus_core_address = nimbus_core_address
    door_address = Address(1, 1, 268)
    self._resolved_interfaces = {
      "nimbus_core": nimbus_core_address,
      "pipette": pipette_address,
      "door_lock": door_address,
    }
    self._nimbus_resolved = NimbusResolvedInterfaces.from_resolution_map(self._resolved_interfaces)

    self.pip = NimbusPIPBackend(
      driver=self, deck=params.deck, address=pipette_address, num_channels=self._num_channels
    )
    self.door = NimbusDoor(driver=self, address=door_address)
    if params.require_door_lock and self.door is None:
      raise RuntimeError("DoorLock is required but not available on this instrument.")

  async def stop(self):
    if self.door is not None:
      await self.door._on_stop()
    self.door = None
    self._resolved_interfaces = {}
    self._nimbus_resolved = None

  async def send_command(
    self,
    command: TCPCommand,
    ensure_connection: bool = True,
    return_raw: bool = False,
    raise_on_error: bool = True,
    read_timeout: Optional[float] = None,
  ) -> Any:
    del ensure_connection, raise_on_error, read_timeout
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
    if return_raw:
      return (b"",)
    return None
