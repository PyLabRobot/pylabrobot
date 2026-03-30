"""Legacy simulator wrapper -- delegates to the new simulator architecture.

Keeps ``LiquidHandler(backend=OpentronsOT2Simulator(), deck=OTDeck())``
working unchanged.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple, Union

from pylabrobot.legacy.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend
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

logger = logging.getLogger(__name__)


class OpentronsOT2Simulator(LiquidHandlerBackend):
  """Legacy simulator backend for the OT-2.

  Internally delegates to :class:`~pylabrobot.opentrons.ot2.simulator.OpentronsOT2SimulatorDriver`
  and :class:`~pylabrobot.opentrons.ot2.simulator.OpentronsOT2SimulatorPIPBackend`.
  """

  def __init__(
    self,
    left_pipette_name: Optional[str] = "p300_single_gen2",
    right_pipette_name: Optional[str] = "p20_single_gen2",
  ):
    super().__init__()
    # Lazy imports to avoid circular dependency.
    from pylabrobot.opentrons.ot2.simulator import (
      OpentronsOT2SimulatorDriver,
      OpentronsOT2SimulatorPIPBackend,
    )

    self._sim_driver = OpentronsOT2SimulatorDriver(
      left_pipette_name=left_pipette_name,
      right_pipette_name=right_pipette_name,
    )
    self._pip = OpentronsOT2SimulatorPIPBackend(self._sim_driver)
    self._left_pipette_name = left_pipette_name
    self._right_pipette_name = right_pipette_name

  def serialize(self) -> dict:
    return {
      **LiquidHandlerBackend.serialize(self),
      "left_pipette_name": self._left_pipette_name,
      "right_pipette_name": self._right_pipette_name,
    }

  def set_deck(self, deck: Deck):
    super().set_deck(deck)
    assert isinstance(deck, OTDeck)
    self._pip.set_deck(deck)

  async def setup(self, skip_home: bool = False):
    await super().setup()
    await self._sim_driver.setup()
    await self._pip._on_setup()
    if not skip_home:
      await self.home()

  async def stop(self):
    await self._pip._on_stop()
    await self._sim_driver.stop()

  async def home(self):
    await self._sim_driver.home()

  @property
  def num_channels(self) -> int:
    return self._pip.num_channels

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int], **backend_kwargs):
    await self._pip.pick_up_tips(
      [OpentronsOT2Backend._pickup_to_new(self, op) for op in ops], use_channels)

  async def drop_tips(self, ops: List[Drop], use_channels: List[int], **backend_kwargs):
    await self._pip.drop_tips(
      [OpentronsOT2Backend._drop_to_new(self, op) for op in ops], use_channels)

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int], **backend_kwargs):
    await self._pip.aspirate(
      [OpentronsOT2Backend._aspiration_to_new(self, op) for op in ops], use_channels)

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int], **backend_kwargs):
    await self._pip.dispense(
      [OpentronsOT2Backend._dispense_to_new(self, op) for op in ops], use_channels)

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return self._pip.can_pick_up_tip(channel_idx, tip)

  async def move_pipette_head(self, location: Coordinate, speed=None, minimum_z_height=None,
                               pipette_id=None, force_direct=False):
    await self._pip._move_pipette_head(
      location=location, speed=speed, minimum_z_height=minimum_z_height,
      pipette_id=pipette_id, force_direct=force_direct)

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

  # -- expose internals for test compatibility --

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
  def traversal_height(self):
    return self._pip.traversal_height

  @traversal_height.setter
  def traversal_height(self, value):
    self._pip.traversal_height = value

  pipette_name2volume = OpentronsOT2Backend.pipette_name2volume

  def _get_pickup_pipette(self, ops):
    return self._pip._get_pickup_pipette(
      [OpentronsOT2Backend._pickup_to_new(self, op) for op in ops])

  def _get_drop_pipette(self, ops):
    return self._pip._get_drop_pipette(
      [OpentronsOT2Backend._drop_to_new(self, op) for op in ops])

  def _get_liquid_pipette(self, ops):
    return self._pip._get_liquid_pipette(ops)

  def _set_tip_state(self, pipette_id, has_tip):
    return self._pip._set_tip_state(pipette_id, has_tip)
