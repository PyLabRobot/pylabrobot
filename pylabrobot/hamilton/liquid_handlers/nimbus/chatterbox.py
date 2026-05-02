"""NimbusChatterboxDriver: prints commands instead of sending them over TCP."""

from __future__ import annotations

import logging
from typing import Any, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.hamilton.tcp.commands import TCPCommand
from pylabrobot.hamilton.tcp.introspection import ObjectInfo
from pylabrobot.hamilton.tcp.packets import Address

from .commands import NimbusCommand, _UNRESOLVED
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
    path_to_addr = {
      "NimbusCORE": nimbus_core_address,
      "NimbusCORE.Pipette": pipette_address,
      "NimbusCORE.DoorLock": door_address,
    }
    seed_paths = sorted(NimbusCommand._ALL_PATHS | set(path_to_addr))
    for idx, path in enumerate(seed_paths):
      leaf = path.rsplit(".", 1)[-1]
      addr = path_to_addr.get(path, Address(1, 1, 1024 + idx))
      self.registry.register(
        path,
        ObjectInfo(name=leaf, version="", method_count=0, subobject_count=0, address=addr),
      )

    self.pip = NimbusPIPBackend(
      driver=self, deck=params.deck, address=pipette_address, num_channels=self._num_channels
    )
    self.door = NimbusDoor(driver=self)
    if params.require_door_lock and self.door is None:
      raise RuntimeError("DoorLock is required but not available on this instrument.")

  async def stop(self):
    if self.door is not None:
      await self.door._on_stop()
    self.door = None
    self._resolved_interfaces = {}
    self._nimbus_resolved = None
    self._nimbus_core_address = None
    self._invalidate_introspection_session()

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

    if isinstance(command, NimbusCommand) and command.dest == _UNRESOLVED:
      path = type(command).firmware_path
      if path is None:
        raise RuntimeError(
          f"{type(command).__name__} has no firmware_path declared and no "
          "explicit dest= supplied at construction. Polymorphic-dest commands "
          "must pass dest= to send_query or send_command."
        )
      try:
        addr = await self.resolve_path(path)
      except KeyError as exc:
        raise RuntimeError(
          f"Cannot send {type(command).__name__}: firmware path "
          f"{path!r} did not resolve on this instrument ({exc})."
        ) from exc
      command.dest = addr
      command.dest_address = addr

    # Return canned responses for commands that need them
    from .commands import (
      GetChannelConfiguration,
      GetChannelConfiguration_1,
      IsDoorLocked,
      IsInitialized,
      IsTipPresent,
    )

    if isinstance(command, GetChannelConfiguration_1):
      return GetChannelConfiguration_1.Response(channels=self._num_channels, channel_types=[])
    if isinstance(command, IsInitialized):
      return IsInitialized.Response(initialized=True)
    if isinstance(command, IsTipPresent):
      return IsTipPresent.Response(tip_present=[False] * self._num_channels)
    if isinstance(command, IsDoorLocked):
      return IsDoorLocked.Response(locked=True)
    if isinstance(command, GetChannelConfiguration):
      return GetChannelConfiguration.Response(enabled=[False])
    if return_raw:
      return (b"",)
    return None
