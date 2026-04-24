"""A basic simulator backend for the Opentrons OT-2.

Implements the same interface as OpentronsOT2Backend but without any hardware
communication. Useful for testing protocols offline.
"""

import logging
from typing import Dict, List, Optional, Tuple

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend
from pylabrobot.liquid_handling.standard import (
  Drop,
  Pickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Coordinate

logger = logging.getLogger(__name__)


class OpentronsOT2Simulator(OpentronsOT2Backend):
  """Simulator backend for the Opentrons OT-2.

  Mimics the behavior of OpentronsOT2Backend (two pipette mounts, single-channel
  operations only, no 96-head or robotic arm) without requiring hardware or the
  ``ot_api`` library.

  Example:
    >>> from pylabrobot.liquid_handling import LiquidHandler
    >>> from pylabrobot.liquid_handling.backends import OpentronsOT2Simulator
    >>> from pylabrobot.resources.opentrons import OTDeck
    >>> lh = LiquidHandler(backend=OpentronsOT2Simulator(), deck=OTDeck())
    >>> await lh.setup()
  """

  def __init__(
    self,
    left_pipette_name: Optional[str] = "p300_single_gen2",
    right_pipette_name: Optional[str] = "p20_single_gen2",
  ):
    """Initialize the simulator.

    Args:
      left_pipette_name: Name of the pipette mounted on the left (e.g. ``"p300_single_gen2"``).
        Set to ``None`` for no left pipette.
      right_pipette_name: Name of the pipette mounted on the right (e.g. ``"p20_single_gen2"``).
        Set to ``None`` for no right pipette.
    """
    # Skip OpentronsOT2Backend.__init__ (requires ot_api); call grandparent directly.
    LiquidHandlerBackend.__init__(self)

    pv = OpentronsOT2Backend.pipette_name2volume
    if left_pipette_name is not None and left_pipette_name not in pv:
      raise ValueError(f"Unknown left pipette: {left_pipette_name}")
    if right_pipette_name is not None and right_pipette_name not in pv:
      raise ValueError(f"Unknown right pipette: {right_pipette_name}")

    self._left_pipette_name = left_pipette_name
    self._right_pipette_name = right_pipette_name
    self._setup_pipettes()

  def _setup_pipettes(self):
    self.left_pipette = (
      {"name": self._left_pipette_name, "pipetteId": "sim-left"}
      if self._left_pipette_name
      else None
    )
    self.right_pipette = (
      {"name": self._right_pipette_name, "pipetteId": "sim-right"}
      if self._right_pipette_name
      else None
    )
    self.left_pipette_has_tip = False
    self.right_pipette_has_tip = False
    self.traversal_height = 120
    self._positions: Dict[str, Coordinate] = {}
    if self.left_pipette is not None:
      self._positions["sim-left"] = Coordinate.zero()
    if self.right_pipette is not None:
      self._positions["sim-right"] = Coordinate.zero()

  def serialize(self) -> dict:
    return {
      **LiquidHandlerBackend.serialize(self),
      "left_pipette_name": self._left_pipette_name,
      "right_pipette_name": self._right_pipette_name,
    }

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, *, skip_home: bool = False):
    await super()._enter_lifespan(stack, skip_home=skip_home)
    self._setup_pipettes()
    logger.info(
      "OpentronsOT2Simulator setup: left=%s, right=%s",
      self._left_pipette_name,
      self._right_pipette_name,
    )

    def cleanup():
      self.left_pipette = None
      self.right_pipette = None
      self.left_pipette_has_tip = False
      self.right_pipette_has_tip = False
      logger.info("OpentronsOT2Simulator stopped.")

    stack.callback(cleanup)

  async def home(self):
    logger.info("Homing (simulated).")

  def _current_channel_position(self, channel: int) -> Tuple[str, Coordinate]:
    pipette_id = self._pipette_id_for_channel(channel)
    return pipette_id, self._positions.get(pipette_id, Coordinate.zero())

  async def move_pipette_head(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    pipette_id: Optional[str] = None,
    force_direct: bool = False,
  ):
    if self.left_pipette is not None and pipette_id == "left":
      pipette_id = self.left_pipette["pipetteId"]
    elif self.right_pipette is not None and pipette_id == "right":
      pipette_id = self.right_pipette["pipetteId"]
    if pipette_id is None:
      raise ValueError("No pipette id given or left/right pipette not available.")
    self._positions[pipette_id] = location
    logger.info("Moved %s to %s (simulated).", pipette_id, location)

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int], **backend_kwargs):
    pipette_id = self._get_pickup_pipette(ops)
    self._set_tip_state(pipette_id, True)
    logger.info("Picked up tip from %s with pipette %s", ops[0].resource.name, pipette_id)

  async def drop_tips(self, ops: List[Drop], use_channels: List[int], **backend_kwargs):
    pipette_id = self._get_drop_pipette(ops)
    self._set_tip_state(pipette_id, False)
    logger.info("Dropped tip to %s with pipette %s", ops[0].resource.name, pipette_id)

  async def aspirate(
    self, ops: List[SingleChannelAspiration], use_channels: List[int], **backend_kwargs
  ):
    self._get_liquid_pipette(ops)
    logger.info("Aspirated %.2f µL from %s", ops[0].volume, ops[0].resource.name)

  async def dispense(
    self, ops: List[SingleChannelDispense], use_channels: List[int], **backend_kwargs
  ):
    self._get_liquid_pipette(ops)
    logger.info("Dispensed %.2f µL to %s", ops[0].volume, ops[0].resource.name)
