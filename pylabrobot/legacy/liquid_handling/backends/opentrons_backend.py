"""Legacy wrapper -- delegates to the new Driver + PIPBackend architecture.

Keeps ``LiquidHandler(backend=OpentronsOT2Backend(...), deck=OTDeck())`` working
unchanged while the real implementation lives in :mod:`pylabrobot.opentrons.ot2`.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union, cast

from pylabrobot.legacy.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Coordinate, Deck, Tip
from pylabrobot.resources.opentrons import OTDeck


class OpentronsOT2Backend(LiquidHandlerBackend):
  """Legacy backend for the Opentrons OT-2.

  Internally delegates to :class:`~pylabrobot.opentrons.ot2.driver.OpentronsOT2Driver`
  and :class:`~pylabrobot.opentrons.ot2.pip_backend.OpentronsOT2PIPBackend`.
  """

  def __init__(self, host: str, port: int = 31950):
    super().__init__()
    # Lazy imports to avoid circular dependency: this module is imported by
    # legacy backends __init__ at package init time.
    from pylabrobot.opentrons.ot2.driver import OpentronsOT2Driver
    from pylabrobot.opentrons.ot2.pip_backend import OpentronsOT2PIPBackend

    self._ot2_driver = OpentronsOT2Driver(host=host, port=port)
    self._pip = OpentronsOT2PIPBackend(self._ot2_driver)

  @property
  def host(self) -> str:
    return self._ot2_driver.host

  @property
  def port(self) -> int:
    return self._ot2_driver.port

  def serialize(self) -> dict:
    return {
      **LiquidHandlerBackend.serialize(self),
      "host": self.host,
      "port": self.port,
    }

  def set_deck(self, deck: Deck):
    super().set_deck(deck)
    assert isinstance(deck, OTDeck)
    self._pip.set_deck(deck)

  async def setup(self, skip_home: bool = False):
    await super().setup()
    await self._ot2_driver.setup()
    await self._pip._on_setup()
    if not skip_home:
      await self.home()

  async def stop(self):
    await self._pip._on_stop()
    await self._ot2_driver.stop()

  async def home(self):
    await self._ot2_driver.home()

  @property
  def num_channels(self) -> int:
    return self._pip.num_channels

  # -- PIP delegation --

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    await self._pip.pick_up_tips(
      [self._pickup_to_new(op) for op in ops], use_channels)

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    await self._pip.drop_tips(
      [self._drop_to_new(op) for op in ops], use_channels)

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    await self._pip.aspirate(
      [self._aspiration_to_new(op) for op in ops], use_channels)

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    await self._pip.dispense(
      [self._dispense_to_new(op) for op in ops], use_channels)

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return self._pip.can_pick_up_tip(channel_idx, tip)

  async def prepare_for_manual_channel_operation(self, channel: int):
    self._pip._pipette_id_for_channel(channel)

  async def move_channel_x(self, channel: int, x: float):
    pipette_id, current = self._pip._current_channel_position(channel)
    target = Coordinate(x=x, y=current.y, z=current.z)
    await self._pip._move_pipette_head(
      location=target, minimum_z_height=self._pip.traversal_height, pipette_id=pipette_id)

  async def move_channel_y(self, channel: int, y: float):
    pipette_id, current = self._pip._current_channel_position(channel)
    target = Coordinate(x=current.x, y=y, z=current.z)
    await self._pip._move_pipette_head(
      location=target, minimum_z_height=self._pip.traversal_height, pipette_id=pipette_id)

  async def move_channel_z(self, channel: int, z: float):
    pipette_id, current = self._pip._current_channel_position(channel)
    target = Coordinate(x=current.x, y=current.y, z=z)
    await self._pip._move_pipette_head(
      location=target, minimum_z_height=self._pip.traversal_height, pipette_id=pipette_id)

  async def move_pipette_head(
    self, location: Coordinate, speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    pipette_id: Optional[str] = None, force_direct: bool = False,
  ):
    await self._pip._move_pipette_head(
      location=location, speed=speed, minimum_z_height=minimum_z_height,
      pipette_id=pipette_id, force_direct=force_direct)

  # -- unsupported operations --

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def aspirate96(self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def pick_up_resource(self, pickup: ResourcePickup):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def drop_resource(self, drop: ResourceDrop):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def list_connected_modules(self) -> List[dict]:
    return await self._ot2_driver.list_connected_modules()

  # -- legacy → new type conversions --

  def _pickup_to_new(self, op: Pickup):
    from pylabrobot.capabilities.liquid_handling.standard import Pickup as NewPickup
    return NewPickup(resource=op.resource, offset=op.offset, tip=op.tip)

  def _drop_to_new(self, op: Drop):
    from pylabrobot.capabilities.liquid_handling.standard import TipDrop as NewTipDrop
    return NewTipDrop(resource=op.resource, offset=op.offset, tip=op.tip)

  def _aspiration_to_new(self, op: SingleChannelAspiration):
    from pylabrobot.capabilities.liquid_handling.standard import Aspiration as NewAspiration, Mix
    mix = None
    if op.mix is not None:
      mix = Mix(volume=op.mix.volume, repetitions=op.mix.repetitions, flow_rate=op.mix.flow_rate)
    return NewAspiration(
      resource=op.resource, offset=op.offset, tip=op.tip, volume=op.volume,
      flow_rate=op.flow_rate, liquid_height=op.liquid_height,
      blow_out_air_volume=op.blow_out_air_volume, mix=mix)

  def _dispense_to_new(self, op: SingleChannelDispense):
    from pylabrobot.capabilities.liquid_handling.standard import Dispense as NewDispense, Mix
    mix = None
    if op.mix is not None:
      mix = Mix(volume=op.mix.volume, repetitions=op.mix.repetitions, flow_rate=op.mix.flow_rate)
    return NewDispense(
      resource=op.resource, offset=op.offset, tip=op.tip, volume=op.volume,
      flow_rate=op.flow_rate, liquid_height=op.liquid_height,
      blow_out_air_volume=op.blow_out_air_volume, mix=mix)

  # -- expose internals for test/legacy compatibility --

  def get_ot_name(self, plr_resource_name: str) -> str:
    return self._pip.get_ot_name(plr_resource_name)

  def select_tip_pipette(self, tip: Tip, with_tip: bool) -> Optional[str]:
    return self._pip.select_tip_pipette(tip, with_tip)

  def select_liquid_pipette(self, volume: float) -> Optional[str]:
    return self._pip.select_liquid_pipette(volume)

  def get_pipette_name(self, pipette_id: str) -> str:
    return self._pip.get_pipette_name(pipette_id)

  @property
  def left_pipette(self):
    return self._ot2_driver.left_pipette

  @left_pipette.setter
  def left_pipette(self, value):
    self._ot2_driver.left_pipette = value

  @property
  def right_pipette(self):
    return self._ot2_driver.right_pipette

  @right_pipette.setter
  def right_pipette(self, value):
    self._ot2_driver.right_pipette = value

  @property
  def left_pipette_has_tip(self):
    return self._pip.left_pipette_has_tip

  @left_pipette_has_tip.setter
  def left_pipette_has_tip(self, value):
    self._pip.left_pipette_has_tip = value

  @property
  def right_pipette_has_tip(self):
    return self._pip.right_pipette_has_tip

  @right_pipette_has_tip.setter
  def right_pipette_has_tip(self, value):
    self._pip.right_pipette_has_tip = value

  @property
  def ot_api_version(self):
    return self._ot2_driver.ot_api_version

  @ot_api_version.setter
  def ot_api_version(self, value):
    self._ot2_driver.ot_api_version = value

  @property
  def traversal_height(self):
    return self._pip.traversal_height

  @traversal_height.setter
  def traversal_height(self, value):
    self._pip.traversal_height = value

  pipette_name2volume = {
    "p10_single": 10, "p10_multi": 10, "p20_single_gen2": 20, "p20_multi_gen2": 20,
    "p50_single": 50, "p50_multi": 50, "p300_single": 300, "p300_multi": 300,
    "p300_single_gen2": 300, "p300_multi_gen2": 300, "p1000_single": 1000,
    "p1000_single_gen2": 1000, "p300_single_gen3": 300, "p1000_single_gen3": 1000,
  }

  def _get_pickup_pipette(self, ops):
    return self._pip._get_pickup_pipette(
      [self._pickup_to_new(op) for op in ops])

  def _get_drop_pipette(self, ops):
    return self._pip._get_drop_pipette(
      [self._drop_to_new(op) for op in ops])

  def _get_liquid_pipette(self, ops):
    return self._pip._get_liquid_pipette(ops)

  def _set_tip_state(self, pipette_id, has_tip):
    return self._pip._set_tip_state(pipette_id, has_tip)
