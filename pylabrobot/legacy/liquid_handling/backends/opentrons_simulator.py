"""Legacy simulator wrapper -- delegates to per-mount simulator backends."""

from __future__ import annotations

import logging
from typing import List, Optional, Union

from pylabrobot.legacy.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend
from pylabrobot.legacy.liquid_handling.standard import (
  Drop, DropTipRack, MultiHeadAspirationContainer, MultiHeadAspirationPlate,
  MultiHeadDispenseContainer, MultiHeadDispensePlate, Pickup, PickupTipRack,
  ResourceDrop, ResourceMove, ResourcePickup, SingleChannelAspiration, SingleChannelDispense,
)
from pylabrobot.resources import Coordinate, Deck, Tip
from pylabrobot.resources.opentrons import OTDeck

logger = logging.getLogger(__name__)


class OpentronsOT2Simulator(LiquidHandlerBackend):
  """Legacy simulator backend for the OT-2, using per-mount PIPBackends."""

  def __init__(self, left_pipette_name: Optional[str] = "p300_single_gen2",
               right_pipette_name: Optional[str] = "p20_single_gen2"):
    super().__init__()
    from pylabrobot.opentrons.ot2.simulator import (
      OpentronsOT2SimulatorDriver, OpentronsOT2SimulatorPIPBackend,
    )
    self._sim_driver = OpentronsOT2SimulatorDriver(
      left_pipette_name=left_pipette_name, right_pipette_name=right_pipette_name)
    self._left_pip = OpentronsOT2SimulatorPIPBackend(self._sim_driver, mount="left")
    self._right_pip = OpentronsOT2SimulatorPIPBackend(self._sim_driver, mount="right")
    self._left_pipette_name = left_pipette_name
    self._right_pipette_name = right_pipette_name

  def serialize(self) -> dict:
    return {**LiquidHandlerBackend.serialize(self),
            "left_pipette_name": self._left_pipette_name,
            "right_pipette_name": self._right_pipette_name}

  def set_deck(self, deck: Deck):
    super().set_deck(deck)
    assert isinstance(deck, OTDeck)
    self._left_pip.set_deck(deck)
    self._right_pip.set_deck(deck)

  async def setup(self, skip_home: bool = False):
    await super().setup()
    await self._sim_driver.setup()
    await self._left_pip._on_setup()
    await self._right_pip._on_setup()
    if not skip_home:
      await self.home()

  async def stop(self):
    await self._left_pip._on_stop()
    await self._right_pip._on_stop()
    await self._sim_driver.stop()

  async def home(self):
    await self._sim_driver.home()

  @property
  def num_channels(self) -> int:
    return self._left_pip.num_channels + self._right_pip.num_channels

  async def pick_up_tips(self, ops, use_channels, **kw):
    pip = self._select_pip_for_tip(ops[0].tip, with_tip=False)
    await pip.pick_up_tips([OpentronsOT2Backend._pickup_to_new(self, op) for op in ops], [0])

  async def drop_tips(self, ops, use_channels, **kw):
    pip = self._select_pip_for_tip(ops[0].tip, with_tip=True)
    await pip.drop_tips([OpentronsOT2Backend._drop_to_new(self, op) for op in ops], [0])

  async def aspirate(self, ops, use_channels, **kw):
    pip = self._select_pip_for_volume(ops[0].volume)
    await pip.aspirate([OpentronsOT2Backend._aspiration_to_new(self, op) for op in ops], [0])

  async def dispense(self, ops, use_channels, **kw):
    pip = self._select_pip_for_volume(ops[0].volume)
    await pip.dispense([OpentronsOT2Backend._dispense_to_new(self, op) for op in ops], [0])

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    pip = self._left_pip if channel_idx == 0 else self._right_pip
    return pip.can_pick_up_tip(0, tip)

  # -- unsupported --
  async def pick_up_tips96(self, pickup): raise NotImplementedError()
  async def drop_tips96(self, drop): raise NotImplementedError()
  async def aspirate96(self, aspiration): raise NotImplementedError()
  async def dispense96(self, dispense): raise NotImplementedError()
  async def pick_up_resource(self, pickup): raise NotImplementedError()
  async def move_picked_up_resource(self, move): raise NotImplementedError()
  async def drop_resource(self, drop): raise NotImplementedError()

  # -- pipette selection --

  def _select_pip_for_tip(self, tip, with_tip):
    if self._left_pip.can_pick_up_tip(0, tip) and with_tip == self._left_pip._has_tip:
      return self._left_pip
    if self._right_pip.can_pick_up_tip(0, tip) and with_tip == self._right_pip._has_tip:
      return self._right_pip
    from pylabrobot.legacy.liquid_handling.errors import NoChannelError
    raise NoChannelError("No pipette channel available.")

  def _select_pip_for_volume(self, volume):
    if self._left_pip._pipette is not None and self._left_pip._max_volume >= volume and self._left_pip._has_tip:
      return self._left_pip
    if self._right_pip._pipette is not None and self._right_pip._max_volume >= volume and self._right_pip._has_tip:
      return self._right_pip
    from pylabrobot.legacy.liquid_handling.errors import NoChannelError
    raise NoChannelError("No pipette channel with tip available.")

  # -- expose internals for test compat --

  @property
  def left_pipette(self):
    return self._sim_driver.left_pipette

  @left_pipette.setter
  def left_pipette(self, value):
    self._sim_driver.left_pipette = value

  @property
  def right_pipette(self):
    return self._sim_driver.right_pipette

  @right_pipette.setter
  def right_pipette(self, value):
    self._sim_driver.right_pipette = value

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
  def traversal_height(self):
    return self._left_pip.traversal_height

  @traversal_height.setter
  def traversal_height(self, value):
    self._left_pip.traversal_height = value
    self._right_pip.traversal_height = value

  pipette_name2volume = OpentronsOT2Backend.pipette_name2volume

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
    if self._sim_driver.left_pipette and pipette_id == self._sim_driver.left_pipette["pipetteId"]:
      self._left_pip._has_tip = has_tip
      return
    if self._sim_driver.right_pipette and pipette_id == self._sim_driver.right_pipette["pipetteId"]:
      self._right_pip._has_tip = has_tip
      return
    raise ValueError(f"Unknown pipette_id {pipette_id!r}")
