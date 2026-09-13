"""PrepChatterboxClient: minimal client for tests without TCP hardware."""

from __future__ import annotations

import logging
from typing import Callable, List, Optional, Union

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.introspection import (
  HamiltonIntrospection,
  MethodInfo,
  ObjectInfo,
)
from pylabrobot.hamilton.transport.tcp.messages import CommandResponse, HoiParams
from pylabrobot.hamilton.transport.tcp.packets import Address, HarpPacket, HoiPacket, IpPacket
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.hamilton.transport.tcp.session import SessionState, TCPSession
from pylabrobot.hamilton.transport.tcp.wire_types import Str, StructArray
from pylabrobot.io.socket import Socket

from . import prep_commands as PrepCmd
from .client import (
  DISCOVERY_OBJECT_PATHS,
  MLPREP_CPU_OBJECT_PATH,
  MLPREP_OBJECT_PATH,
  MPH_OBJECT_PATH,
  PIPETTOR_OBJECT_PATH,
  PrepClient,
  _ResolvedPrepCommand,
)
from .configuration import DeviceConfiguration
from .prep_commands import PrepCommand

logger = logging.getLogger(__name__)

# Channel v2 support probe expects pipettor interface 1 to expose these method IDs.
_V2_PIPETTING_METHOD_IDS = frozenset(range(38, 44))
# Head8._probe_v2_support expects MPH interface 1 to expose these method IDs.
_V2_MPH_METHOD_IDS = frozenset(range(29, 35))
# Methods the driver finds by name, at the ids MLPrep Runtime V3.0.20 declares them on interface 1.
_MLPREP_NAMED_METHODS = {"IsParked": 34, "IsSpread": 35}
_PIPETTOR_NAMED_METHODS = {"GetTipDefinitionHeld": 13}


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
    self._executor.require_active()
    addr = await self._resolve_target_address(address)
    stubs = self._stub_methods_fn(addr, interface_id)
    if stubs is not None:
      return stubs
    return await super().methods_for_interface(address, interface_id)

  async def ensure_method_table(self, address, *, _supported=None, _object_info=None):
    self._executor.require_active()
    addr = await self._resolve_target_address(address)
    stubs = self._stub_methods_fn(addr, 1)
    if stubs is not None:
      return stubs
    return await super().ensure_method_table(
      address, _supported=_supported, _object_info=_object_info
    )


class PrepChatterboxClient(PrepClient):
  """Skips TCP; uses canned addresses so Prep channels can be exercised offline.

  Canned firmware state lives on the chatterbox client as a :class:`DeviceConfiguration`, and the
  session answers the configuration queries from it, so the driver reads it the way it reads a
  physical device's. A declared configuration is passed as ``configuration``.

  Default ``use_v1_aspirate_dispense=False`` matches hardware: introspection stubs
  report v2 aspirate/dispense commands on the pipettor. Pass
  ``use_v1_aspirate_dispense=True`` for a thinner v1-only offline path.
  """

  def __init__(
    self,
    num_channels: int = 2,
    head8_installed: bool = True,
    default_traverse_height: float = 180.0,
    use_v1_aspirate_dispense: bool = False,
    configuration: Optional[DeviceConfiguration] = None,
  ):
    self._canned_config = (
      configuration
      if configuration is not None
      else DeviceConfiguration(
        has_enclosure=False,
        safe_speeds_enabled=True,
        default_traverse_height=default_traverse_height,
        num_channels=num_channels,
        head8_installed=head8_installed,
      )
    )
    self._pipettor_addr: Optional[Address] = None
    self._mph_addr: Optional[Address] = None
    self._use_v1_aspirate_dispense: bool = use_v1_aspirate_dispense
    super().__init__(host="chatterbox", port=2000)

  def _create_session(self) -> TCPSession:
    """Create an offline session that still encodes requests and decodes responses."""
    session = _PrepChatterboxSession(
      Socket("Prep chatterbox", "chatterbox", 2000), self._canned_config
    )

    def _stub_methods(addr: Address, interface_id: int) -> Optional[List[MethodInfo]]:
      if interface_id != 1:
        return None
      def named(methods: dict) -> List[MethodInfo]:
        return [
          MethodInfo(interface_id=1, call_type=0, method_id=mid, name=name)
          for name, mid in methods.items()
        ]
      stubs: List[MethodInfo] = []
      if self._pipettor_addr is not None and addr == self._pipettor_addr:
        stubs += named(_PIPETTOR_NAMED_METHODS)
        if not self._use_v1_aspirate_dispense:
          stubs += [
            MethodInfo(interface_id=1, call_type=0, method_id=mid, name=f"v2_stub_{mid}")
            for mid in sorted(_V2_PIPETTING_METHOD_IDS)
          ]
      if (
        self._mph_addr is not None
        and addr == self._mph_addr
        and not self._use_v1_aspirate_dispense
      ):
        stubs += [
          MethodInfo(interface_id=1, call_type=0, method_id=mid, name=f"v2_mph_stub_{mid}")
          for mid in sorted(_V2_MPH_METHOD_IDS)
        ]
      if self._mlprep_address is not None and addr == self._mlprep_address:
        stubs += named(_MLPREP_NAMED_METHODS)
      return stubs or None

    session.introspection = _PrepChatterboxIntrospection(
      registry=session.registry,
      global_object_addresses=session.global_object_addresses,
      executor=session,
      stub_methods_fn=_stub_methods,
    )
    return session

  async def setup(self):
    if self._session.state is not SessionState.CLOSED:
      raise RuntimeError("Prep chatterbox already set up - call stop() first")
    self._session = self._create_session()
    self._session.state = SessionState.READY
    self._session.client_address = Address(2, 1, 65535)
    self._session.client_id = 1
    # Seed the introspection registry with every firmware path the codebase
    # may touch. The seed list is derived from the command aggregate
    # (PrepCommand._ALL_PATHS) plus the paths discovery reads — new commands
    # with new firmware_path values get chatterbox parity for free. Addresses
    # are assigned deterministically in sorted-path order so they're stable
    # across runs.
    seed_paths = sorted(PrepCommand._ALL_PATHS | set(DISCOVERY_OBJECT_PATHS))
    for idx, path in enumerate(seed_paths):
      leaf = path.rsplit(".", 1)[-1]
      addr = Address(1, 1, 256 + idx)
      self.registry.register(
        path,
        ObjectInfo(name=leaf, version="", method_count=0, subobject_count=0, address=addr),
      )
    self._pipettor_addr = await self.resolve_path(PIPETTOR_OBJECT_PATH)
    self._mlprep_address = await self.resolve_path(MLPREP_OBJECT_PATH)
    if self._canned_config.head8_installed:
      self._mph_addr = await self.resolve_path(MPH_OBJECT_PATH)

  async def stop(self):
    self._pipettor_addr = None
    self._mph_addr = None
    self._mlprep_address = None
    await super().stop()


# Canned payloads follow the declared response schemas; empty lists mean no simulated geometry.
_CANNED_RESPONSES: dict[type[TCPCommand], HoiParams] = {
  PrepCmd.PrepGetPositions: HoiParams().add([], StructArray()),
  PrepCmd.PrepGetIsInitialized: HoiParams().add(False, PrepCmd.PaddedBool),
  PrepCmd.PrepGetDeckLight: HoiParams()
  .add(0, PrepCmd.PaddedU8)
  .add(0, PrepCmd.PaddedU8)
  .add(0, PrepCmd.PaddedU8)
  .add(0, PrepCmd.PaddedU8),
  PrepCmd.PrepIsParked: HoiParams().add(False, PrepCmd.PaddedBool),
  PrepCmd.PrepIsSpread: HoiParams().add(False, PrepCmd.PaddedBool),
  PrepCmd.PrepGetIsEnclosurePresent: HoiParams().add(False, PrepCmd.PaddedBool),
  PrepCmd.PrepGetSafeSpeedsEnabled: HoiParams().add(False, PrepCmd.PaddedBool),
  PrepCmd.PrepGetDefaultTraverseHeight: HoiParams().add(0, PrepCmd.F32),
  PrepCmd.PrepGetTipAndNeedleDefinitions: HoiParams().add([], StructArray()),
  PrepCmd.PrepGetDeckBounds: HoiParams()
  .add(0, PrepCmd.F32)
  .add(0, PrepCmd.F32)
  .add(0, PrepCmd.F32)
  .add(0, PrepCmd.F32)
  .add(0, PrepCmd.F32)
  .add(0, PrepCmd.F32),
  PrepCmd.PrepGetCalibrationSiteDefinitions: HoiParams().add([], StructArray()),
  PrepCmd.PrepGetDeckSiteDefinitions: HoiParams().add([], StructArray()),
  PrepCmd.PrepGetWasteSiteDefinitions: HoiParams().add([], StructArray()),
  PrepCmd.PrepGetChannelBounds: HoiParams().add([], StructArray()),
  PrepCmd.PrepGetPresentChannels: HoiParams().add([], PrepCmd.EnumArray),
  PrepCmd.PrepCalibrateXAxis: HoiParams().add(0, PrepCmd.F32),
  PrepCmd.PrepCalibrateYAxis: HoiParams().add(0, PrepCmd.F32),
  PrepCmd.PrepCalibrateZAxis: HoiParams().add(0, PrepCmd.F32),
  PrepCmd.PrepCalibrateSqueeze: HoiParams().add(0, PrepCmd.U32),
  PrepCmd.PrepCalibrateSqueezeTips: HoiParams().add([], PrepCmd.U32Array),
  PrepCmd.PrepGetCalibrationValues: HoiParams()
  .add(0, PrepCmd.F32)
  .add(0, PrepCmd.F32)
  .add([], StructArray()),
  PrepCmd.PrepGetChannelHardwareConfiguration: HoiParams().add([], StructArray()),
}


# Replies to the methods the driver finds by name, keyed by object path and the ids stubbed above.
_NAMED_METHOD_RESPONSES: dict[tuple[str, int, int], HoiParams] = {
  (MLPREP_OBJECT_PATH, 1, _MLPREP_NAMED_METHODS["IsParked"]): HoiParams().add(False, PrepCmd.PaddedBool),
  (MLPREP_OBJECT_PATH, 1, _MLPREP_NAMED_METHODS["IsSpread"]): HoiParams().add(False, PrepCmd.PaddedBool),
}


# Where PRPAA1087's channels reported themselves after it initialized on 2026-09-13, as (x, y, z)
# in mm. What a simulated device answers until something moves it.
_INITIALIZED_POSITIONS = {
  PrepCmd.ChannelIndex.RearChannel: (289.489, 365.0148, 167.499),
  PrepCmd.ChannelIndex.FrontChannel: (289.489, 345.0123, 167.4954),
}


class _PrepChatterboxSession(TCPSession):
  """Offline exchange using the same immutable requests and decoders as TCP."""

  def __init__(self, io: Socket, config: DeviceConfiguration) -> None:
    """Use the configured instrument metadata for matching typed status replies."""
    super().__init__(io)
    self._config = config
    self._responses = dict(_CANNED_RESPONSES)
    if config.default_traverse_height is not None:
      self._responses[PrepCmd.PrepGetDefaultTraverseHeight] = HoiParams().add(
        config.default_traverse_height, PrepCmd.F32
      )
    present = [PrepCmd.ChannelIndex.RearChannel, PrepCmd.ChannelIndex.FrontChannel][
      : config.num_channels or 0
    ]
    if config.head8_installed:
      present.append(PrepCmd.ChannelIndex.MPHChannel)
    self._responses[PrepCmd.PrepGetPresentChannels] = HoiParams().add(
      [int(c) for c in present], PrepCmd.EnumArray
    )
    if config.deck_bounds is not None:
      bounds = config.deck_bounds
      params = HoiParams()
      for value in (bounds.min_x, bounds.max_x, bounds.min_y, bounds.max_y, bounds.min_z, bounds.max_z):
        params.add(value, PrepCmd.F32)
      self._responses[PrepCmd.PrepGetDeckBounds] = params
    # Where each pipetting channel is, moved by the moves it is sent.
    self._positions = {
      int(channel): list(_INITIALIZED_POSITIONS[channel])
      for channel in present
      if channel in _INITIALIZED_POSITIONS
    }
    self._responses[PrepCmd.PrepGetSafeSpeedsEnabled] = HoiParams().add(
      config.safe_speeds_enabled, PrepCmd.PaddedBool
    )
    self._responses[PrepCmd.PrepGetIsEnclosurePresent] = HoiParams().add(
      config.has_enclosure, PrepCmd.PaddedBool
    )

  async def exchange(
    self, command: TCPCommand[object], *, read_timeout: Optional[float] = None
  ) -> CommandResponse:
    """Encode one request and return a correlated canned success frame."""
    self.require_ready()
    assert self.client_address is not None
    sequence = self.allocate_sequence(command.dest)
    request_frame = HarpPacket.unpack(
      IpPacket.unpack(command.build(self.client_address, sequence)).payload
    )
    hoi = HoiPacket.unpack(request_frame.payload)
    request = command.request if isinstance(command, _ResolvedPrepCommand) else command
    logger.info("[Prep chatterbox] %s", type(request).__name__)
    payload = self._responses.get(type(request), HoiParams())
    # Which device this is, from the configuration it stands in for: serial (9) and firmware (8).
    if (
      isinstance(request, PrepCmd.PrepProbeRequest)
      and self.registry.path(request.dest) == MLPREP_CPU_OBJECT_PATH
    ):
      answer = {9: self._config.serial_number, 8: self._config.firmware_version}.get(request.command_id)
      if answer is not None:
        payload = HoiParams().add(answer, Str)
    if isinstance(request, PrepCmd.PrepProbeRequest):
      named = _NAMED_METHOD_RESPONSES.get(
        (self.registry.path(request.dest), request.interface_id, request.command_id)
      )
      if named is not None:
        payload = named
    if isinstance(request, (PrepCmd.PrepMoveToPosition, PrepCmd.PrepMoveToPositionViaLane)):
      move = request.move_parameters
      for position in self._positions.values():
        position[0] = move.gantry_x_position
      for axis in move.axis_parameters:
        if int(axis.channel) in self._positions:
          self._positions[int(axis.channel)][1:] = [axis.y_position, axis.z_position]
    if isinstance(request, PrepCmd.PrepMoveZUpToSafe) and self._config.default_traverse_height is not None:
      for channel in request.channels:
        if int(channel) in self._positions:
          self._positions[int(channel)][2] = self._config.default_traverse_height
    if isinstance(request, PrepCmd.PrepGetPositions):
      payload = HoiParams().add(
        [
          PrepCmd.ChannelXYZPositionParameters(
            default_values=False, channel=channel, position_x=x, position_y=y, position_z=z
          )
          for channel, (x, y, z) in sorted(self._positions.items())
        ],
        StructArray(),
      )
    action = (
      Hoi2Action.STATUS_RESPONSE
      if hoi.action_code == Hoi2Action.STATUS_REQUEST
      else Hoi2Action.COMMAND_RESPONSE
    )
    response = HoiPacket(
      interface_id=hoi.interface_id,
      action_id=hoi.action_id,
      params=payload.build(),
      action_code=action,
    )
    harp = HarpPacket(
      src=command.dest,
      dst=self.client_address,
      seq=sequence,
      protocol=2,
      action_code=4,
      payload=response.pack(),
    )
    return CommandResponse.from_bytes(IpPacket(protocol=6, payload=harp.pack()).pack())
