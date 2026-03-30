"""Legacy wrapper -- delegates to per-mount PIPBackends.

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

  Internally creates two per-mount PIPBackends (left + right) that share
  a single :class:`OpentronsOT2Driver`.
  """

  def __init__(self, host: str, port: int = 31950):
    super().__init__()
    from pylabrobot.opentrons.ot2.driver import OpentronsOT2Driver
    from pylabrobot.opentrons.ot2.pip_backend import OpentronsOT2PIPBackend

    self._ot2_driver = OpentronsOT2Driver(host=host, port=port)
    self._left_pip = OpentronsOT2PIPBackend(self._ot2_driver, mount="left")
    self._right_pip = OpentronsOT2PIPBackend(self._ot2_driver, mount="right")

  def _pip_for_channel(self, channel: int):
    if channel == 0:
      return self._left_pip
    return self._right_pip

  @property
  def host(self) -> str:
    return self._ot2_driver.host

  @property
  def port(self) -> int:
    return self._ot2_driver.port

  def serialize(self) -> dict:
    return {**LiquidHandlerBackend.serialize(self), "host": self.host, "port": self.port}

  def set_deck(self, deck: Deck):
    super().set_deck(deck)
    assert isinstance(deck, OTDeck)
    self._left_pip.set_deck(deck)
    self._right_pip.set_deck(deck)

  async def setup(self, skip_home: bool = False):
    await super().setup()
    await self._ot2_driver.setup()
    await self._left_pip._on_setup()
    await self._right_pip._on_setup()
    if not skip_home:
      await self.home()

  async def stop(self):
    await self._left_pip._on_stop()
    await self._right_pip._on_stop()
    await self._ot2_driver.stop()

  async def home(self):
    await self._ot2_driver.home()

  @property
  def num_channels(self) -> int:
    return self._left_pip.num_channels + self._right_pip.num_channels

  # -- PIP delegation (legacy uses channel 0=left, 1=right) --

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    pip = self._select_pip_for_tip(ops[0].tip, with_tip=False)
    await pip.pick_up_tips([self._pickup_to_new(op) for op in ops], [0])

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    pip = self._select_pip_for_tip(ops[0].tip, with_tip=True)
    await pip.drop_tips([self._drop_to_new(op) for op in ops], [0])

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    pip = self._select_pip_for_volume(ops[0].volume)
    await pip.aspirate([self._aspiration_to_new(op) for op in ops], [0])

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    pip = self._select_pip_for_volume(ops[0].volume)
    await pip.dispense([self._dispense_to_new(op) for op in ops], [0])

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return self._pip_for_channel(channel_idx).can_pick_up_tip(0, tip)

  async def move_pipette_head(self, location, speed=None, minimum_z_height=None,
                               pipette_id=None, force_direct=False):
    pip = self._left_pip if pipette_id in ("left", self._ot2_driver.left_pipette and self._ot2_driver.left_pipette.get("pipetteId")) else self._right_pip
    pip._move_to(location)

  # -- unsupported --

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

  # -- pipette selection (legacy channel logic) --

  def _select_pip_for_tip(self, tip: Tip, with_tip: bool):
    """Select the per-mount backend that matches the tip, preferring left."""
    if self._left_pip.can_pick_up_tip(0, tip) and with_tip == self._left_pip._has_tip:
      return self._left_pip
    if self._right_pip.can_pick_up_tip(0, tip) and with_tip == self._right_pip._has_tip:
      return self._right_pip
    from pylabrobot.legacy.liquid_handling.errors import NoChannelError
    raise NoChannelError("No pipette channel of right type available.")

  def _select_pip_for_volume(self, volume: float):
    """Select the per-mount backend that can handle the volume, preferring left."""
    if self._left_pip._pipette is not None and self._left_pip._max_volume >= volume and self._left_pip._has_tip:
      return self._left_pip
    if self._right_pip._pipette is not None and self._right_pip._max_volume >= volume and self._right_pip._has_tip:
      return self._right_pip
    from pylabrobot.legacy.liquid_handling.errors import NoChannelError
    raise NoChannelError("No pipette channel of right type with tip available.")

  # -- type conversions --

  def _pickup_to_new(self, op: Pickup):
    from pylabrobot.capabilities.liquid_handling.standard import Pickup as NewPickup
    return NewPickup(resource=op.resource, offset=op.offset, tip=op.tip)

  def _drop_to_new(self, op: Drop):
    from pylabrobot.capabilities.liquid_handling.standard import TipDrop as NewTipDrop
    return NewTipDrop(resource=op.resource, offset=op.offset, tip=op.tip)

  def _aspiration_to_new(self, op: SingleChannelAspiration):
    from pylabrobot.capabilities.liquid_handling.standard import Aspiration as NewAspiration, Mix
    mix = Mix(volume=op.mix.volume, repetitions=op.mix.repetitions, flow_rate=op.mix.flow_rate) if op.mix else None
    return NewAspiration(resource=op.resource, offset=op.offset, tip=op.tip, volume=op.volume,
      flow_rate=op.flow_rate, liquid_height=op.liquid_height,
      blow_out_air_volume=op.blow_out_air_volume, mix=mix)

  def _dispense_to_new(self, op: SingleChannelDispense):
    from pylabrobot.capabilities.liquid_handling.standard import Dispense as NewDispense, Mix
    mix = Mix(volume=op.mix.volume, repetitions=op.mix.repetitions, flow_rate=op.mix.flow_rate) if op.mix else None
    return NewDispense(resource=op.resource, offset=op.offset, tip=op.tip, volume=op.volume,
      flow_rate=op.flow_rate, liquid_height=op.liquid_height,
      blow_out_air_volume=op.blow_out_air_volume, mix=mix)

  # -- expose internals for test/legacy compatibility --

  def get_ot_name(self, plr_resource_name: str) -> str:
    return self._ot2_driver.get_ot_name(plr_resource_name)

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
    return self._left_pip._has_tip

  @left_pipette_has_tip.setter
  def left_pipette_has_tip(self, value):
    self._left_pip._has_tip = value

  @property
  def right_pipette_has_tip(self):
    return self._right_pip._has_tip

  @right_pipette_has_tip.setter
  def right_pipette_has_tip(self, value):
    self._right_pip._has_tip = value

  @property
  def ot_api_version(self):
    return self._ot2_driver.ot_api_version

  @ot_api_version.setter
  def ot_api_version(self, value):
    self._ot2_driver.ot_api_version = value

  @property
  def traversal_height(self):
    return self._left_pip.traversal_height

  @traversal_height.setter
  def traversal_height(self, value):
    self._left_pip.traversal_height = value
    self._right_pip.traversal_height = value

  pipette_name2volume = {
    "p10_single": 10, "p10_multi": 10, "p20_single_gen2": 20, "p20_multi_gen2": 20,
    "p50_single": 50, "p50_multi": 50, "p300_single": 300, "p300_multi": 300,
    "p300_single_gen2": 300, "p300_multi_gen2": 300, "p1000_single": 1000,
    "p1000_single_gen2": 1000, "p300_single_gen3": 300, "p1000_single_gen3": 1000,
  }

  def _get_pickup_pipette(self, ops):
    pip = self._select_pip_for_tip(ops[0].tip, with_tip=False)
    return pip._pipette_id

  def _get_drop_pipette(self, ops):
    pip = self._select_pip_for_tip(ops[0].tip, with_tip=True)
    return pip._pipette_id

  def _get_liquid_pipette(self, ops):
    pip = self._select_pip_for_volume(ops[0].volume)
    return pip._pipette_id

  def _set_tip_state(self, pipette_id, has_tip):
    if self._ot2_driver.left_pipette and pipette_id == self._ot2_driver.left_pipette["pipetteId"]:
      self._left_pip._has_tip = has_tip
      return
    if self._ot2_driver.right_pipette and pipette_id == self._ot2_driver.right_pipette["pipetteId"]:
      self._right_pip._has_tip = has_tip
      return
    raise ValueError(f"Unknown pipette_id {pipette_id!r}")
