"""NimbusChatterboxDriver: prints commands instead of sending them over TCP."""

from __future__ import annotations

import logging
from typing import Any, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.hamilton.tcp.commands import TCPCommand
from pylabrobot.hamilton.tcp.introspection import ObjectInfo
from pylabrobot.hamilton.tcp.packets import Address

from .commands import NimbusCommand, _UNRESOLVED
from .driver import NimbusDriver, NimbusSetupParams

logger = logging.getLogger(__name__)

_CHATTERBOX_NIMBUS_CORE = Address(1, 1, 48896)
_CHATTERBOX_PIPETTE = Address(1, 1, 257)
_CHATTERBOX_DOOR_LOCK = Address(1, 1, 268)

_CHATTERBOX_PATH_TO_ADDR = {
  "NimbusCORE": _CHATTERBOX_NIMBUS_CORE,
  "NimbusCORE.Pipette": _CHATTERBOX_PIPETTE,
  "NimbusCORE.DoorLock": _CHATTERBOX_DOOR_LOCK,
}


class NimbusChatterboxDriver(NimbusDriver):
  """Chatterbox driver for Nimbus. Simulates commands for testing without hardware.

  Inherits NimbusDriver but overrides setup/stop/send_command to skip TCP
  and use canned addresses and responses instead.
  """

  def __init__(self, num_channels: int = 8):
    super().__init__(host="chatterbox", port=2000)
    self._num_channels = num_channels

  async def setup(self, backend_params: Optional[BackendParams] = None):
    if backend_params is None:
      params = NimbusSetupParams()
    elif isinstance(backend_params, NimbusSetupParams):
      params = backend_params
    else:
      raise TypeError(
        "NimbusChatterboxDriver.setup expected NimbusSetupParams | None for backend_params, "
        f"got {type(backend_params).__name__}"
      )
    del params

    # Seed introspection registry with canned addresses (skip TCP connection entirely)
    self._nimbus_core_address = _CHATTERBOX_NIMBUS_CORE
    seed_paths = sorted(NimbusCommand._ALL_PATHS | set(_CHATTERBOX_PATH_TO_ADDR))
    for idx, path in enumerate(seed_paths):
      leaf = path.rsplit(".", 1)[-1]
      addr = _CHATTERBOX_PATH_TO_ADDR.get(path, Address(1, 1, 1024 + idx))
      self.registry.register(
        path,
        ObjectInfo(name=leaf, version="", method_count=0, subobject_count=0, address=addr),
      )

  async def stop(self):
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

    from .commands import (
      ChannelConfiguration,
      GetChannelConfiguration,
      IsCoreGripperPlateGripped,
      IsCoreGripperToolHeld,
      IsDoorLocked,
      IsInitialized,
      IsTipPresent,
      NimbusChannelConfigWire,
    )

    if isinstance(command, ChannelConfiguration):
      # 8× Channel300uL, alternating Left/Right rails
      configs = [
        NimbusChannelConfigWire(
          channel_type=1,  # Channel300uL
          rail=i % 2,  # 0=Left, 1=Right alternating
          previous_neighbor_spacing=0,
          next_neighbor_spacing=0,
          can_address=i,
        )
        for i in range(self._num_channels)
      ]
      return ChannelConfiguration.Response(configurations=configs)
    if isinstance(command, IsInitialized):
      return IsInitialized.Response(initialized=True)
    if isinstance(command, IsTipPresent):
      return IsTipPresent.Response(tip_present=[False] * self._num_channels)
    if isinstance(command, IsDoorLocked):
      return IsDoorLocked.Response(locked=True)
    if isinstance(command, GetChannelConfiguration):
      return GetChannelConfiguration.Response(enabled=[False])
    if isinstance(command, IsCoreGripperToolHeld):
      return IsCoreGripperToolHeld.Response(gripped=False, tip_type=[])
    if isinstance(command, IsCoreGripperPlateGripped):
      return IsCoreGripperPlateGripped.Response(gripped=False)
    if return_raw:
      return (b"",)
    return None
