"""PrepChatterboxClient: minimal client for tests without TCP hardware."""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Union

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.introspection import (
  HamiltonIntrospection,
  MethodInfo,
  ObjectInfo,
)
from pylabrobot.hamilton.transport.tcp.packets import Address

from . import prep_commands as PrepCmd
from .client import (
  MLPREP_OBJECT_PATH,
  MPH_OBJECT_PATH,
  PIPETTOR_OBJECT_PATH,
  PrepClient,
)
from .info import PrepInstrumentInfo
from .prep_commands import PrepCommand

logger = logging.getLogger(__name__)

# Channel v2 support probe expects pipettor interface 1 to expose these method IDs.
_V2_PIPETTING_METHOD_IDS = frozenset(range(38, 44))
# PrepHead8._probe_v2_support expects MPH interface 1 to expose these method IDs.
_V2_MPH_METHOD_IDS = frozenset(range(29, 35))


class _PrepChatterboxIntrospection(HamiltonIntrospection):
  """Offline introspection: v2 probe succeeds when ``use_v1_aspirate_dispense`` is False."""

  def __init__(
    self,
    *args,
    stub_methods_fn: Callable[[Address, int], Optional[List[MethodInfo]]],
    **kwargs,
  ):
    super().__init__(*args, **kwargs)
    self._stub_methods_fn = stub_methods_fn

  async def methods_for_interface(
    self, address: Union[Address, str], interface_id: int
  ) -> List[MethodInfo]:
    addr = await self._resolve_target_address(address)
    stubs = self._stub_methods_fn(addr, interface_id)
    if stubs is not None:
      return stubs
    return await super().methods_for_interface(address, interface_id)


class PrepChatterboxInstrumentInfo(PrepInstrumentInfo):
  """Offline info: uses canned :class:`~prep_commands.InstrumentConfig` from the chatterbox client."""

  async def _on_setup(self) -> None:
    d = self._driver
    assert isinstance(d, PrepChatterboxClient)
    self._config = d._canned_config


class PrepChatterboxClient(PrepClient):
  """Skips TCP; uses canned addresses so Prep channels can be exercised offline.

  Canned firmware state (num_channels, has_mph, traverse height) lives on the
  chatterbox client — :class:`PrepChatterboxInstrumentInfo` reads it for ``info.config``.

  Default ``use_v1_aspirate_dispense=False`` matches hardware: introspection stubs
  report v2 aspirate/dispense commands on the pipettor. Pass
  ``use_v1_aspirate_dispense=True`` for a thinner v1-only offline path.
  """

  def __init__(
    self,
    num_channels: int = 2,
    has_mph: bool = True,
    default_traverse_height: float = 180.0,
    use_v1_aspirate_dispense: bool = False,
  ):
    super().__init__(host="chatterbox", port=2000)
    self._canned_config = PrepCmd.InstrumentConfig(
      deck_bounds=None,
      has_enclosure=False,
      safe_speeds_enabled=True,
      deck_sites=(),
      waste_sites=(),
      default_traverse_height=default_traverse_height,
      num_channels=num_channels,
      has_mph=has_mph,
    )
    self._pipettor_addr: Optional[Address] = None
    self._mph_addr: Optional[Address] = None
    self._use_v1_aspirate_dispense: bool = use_v1_aspirate_dispense

  @property
  def introspection(self) -> HamiltonIntrospection:
    if self._introspection_impl is None:

      def _stub_methods(addr: Address, interface_id: int) -> Optional[List[MethodInfo]]:
        if interface_id == 1 and not self._use_v1_aspirate_dispense:
          if self._pipettor_addr is not None and addr == self._pipettor_addr:
            return [
              MethodInfo(interface_id=1, call_type=0, method_id=mid, name=f"v2_stub_{mid}")
              for mid in sorted(_V2_PIPETTING_METHOD_IDS)
            ]
          if self._mph_addr is not None and addr == self._mph_addr:
            return [
              MethodInfo(interface_id=1, call_type=0, method_id=mid, name=f"v2_mph_stub_{mid}")
              for mid in sorted(_V2_MPH_METHOD_IDS)
            ]
        return None

      self._introspection_impl = _PrepChatterboxIntrospection(
        registry=self._registry,
        global_object_addresses=self._global_object_addresses,
        send_discovery_command=self.send_discovery_command,
        send_query=self.send_query,
        stub_methods_fn=_stub_methods,
      )
    return self._introspection_impl

  async def setup(self):
    # Seed the introspection registry with every firmware path the codebase
    # may touch. The seed list is derived from the command aggregate
    # (PrepCommand._ALL_PATHS) plus PrepInstrumentInfo._paths — new commands
    # with new firmware_path values get chatterbox parity for free. Addresses
    # are assigned deterministically in sorted-path order so they're stable
    # across runs.
    seed_paths = sorted(PrepCommand._ALL_PATHS | set(PrepInstrumentInfo._paths.values()))
    for idx, path in enumerate(seed_paths):
      leaf = path.rsplit(".", 1)[-1]
      addr = Address(1, 1, 256 + idx)
      self.registry.register(
        path,
        ObjectInfo(name=leaf, version="", method_count=0, subobject_count=0, address=addr),
      )
    self._pipettor_addr = await self.resolve_path(PIPETTOR_OBJECT_PATH)
    self._mlprep_address = await self.resolve_path(MLPREP_OBJECT_PATH)
    if self._canned_config.has_mph:
      self._mph_addr = await self.resolve_path(MPH_OBJECT_PATH)

  async def stop(self):
    self._pipettor_addr = None
    self._mph_addr = None
    self._mlprep_address = None
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
    # Exercise the JIT resolve path so that missing firmware paths surface
    # the same error offline as they would against hardware.
    from .prep_commands import _UNRESOLVED, PrepCommand

    if isinstance(command, PrepCommand) and command.dest == _UNRESOLVED:
      path = type(command).firmware_path
      if path is None:
        raise RuntimeError(
          f"{type(command).__name__} has no firmware_path declared and no "
          "explicit dest= supplied at construction."
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
    logger.info("[Prep chatterbox] %s", command.__class__.__name__)
    if return_raw:
      return (b"",)
    return None
