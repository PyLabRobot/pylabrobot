"""A basic simulator backend for the Opentrons OT-2.

Implements the same interface as OpentronsOT2Backend but without any hardware
communication. Useful for testing protocols offline.
"""

import logging
from typing import List, Optional

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend
from pylabrobot.liquid_handling.standard import (
  Drop,
  Pickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)

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

    self.left_pipette = (
      {"name": left_pipette_name, "pipetteId": "sim-left"} if left_pipette_name else None
    )
    self.right_pipette = (
      {"name": right_pipette_name, "pipetteId": "sim-right"} if right_pipette_name else None
    )
    self.left_pipette_has_tip = False
    self.right_pipette_has_tip = False

  def serialize(self) -> dict:
    return {
      **LiquidHandlerBackend.serialize(self),
      "left_pipette_name": self.left_pipette["name"] if self.left_pipette else None,
      "right_pipette_name": self.right_pipette["name"] if self.right_pipette else None,
    }

  async def setup(self, skip_home: bool = False):
    await LiquidHandlerBackend.setup(self)
    left = self.left_pipette["name"] if self.left_pipette else None
    right = self.right_pipette["name"] if self.right_pipette else None
    logger.info("OpentronsOT2Simulator setup: left=%s, right=%s", left, right)
    if not skip_home:
      await self.home()

  async def home(self):
    logger.info("Homing (simulated).")

  async def stop(self):
    self.left_pipette_has_tip = False
    self.right_pipette_has_tip = False
    self.left_pipette = None
    self.right_pipette = None
    logger.info("OpentronsOT2Simulator stopped.")

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
