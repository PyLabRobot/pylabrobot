# pylint: disable=unused-argument

from typing import List, Union

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.resources import Resource
from pylabrobot.liquid_handling.standard import (
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  AspirationContainer,
  Dispense,
  DispensePlate,
  DispenseContainer,
  Move
)


class ChatterBoxBackend(LiquidHandlerBackend):
  """ Chatter box backend for 'How to Open Source' """

  def __init__(self, num_channels: int = 8):
    """ Initialize a chatter box backend. """
    super().__init__()
    self._num_channels = num_channels

  async def setup(self):
    await super().setup()
    print("Setting up the robot.")

  async def stop(self):
    print("Stopping the robot.")

  def serialize(self) -> dict:
    return {**super().serialize(), "num_channels": self.num_channels}

  @property
  def num_channels(self) -> int:
    return self._num_channels

  async def assigned_resource_callback(self, resource: Resource):
    print(f"Resource {resource.name} was assigned to the robot.")

  async def unassigned_resource_callback(self, name: str):
    print(f"Resource {name} was unassigned from the robot.")

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int], **backend_kwargs):
    print(f"Picking up tips {ops}.")

  async def drop_tips(self, ops: List[Drop], use_channels: List[int], **backend_kwargs):
    print(f"Dropping tips {ops}.")

  async def aspirate(self, ops: List[Aspiration], use_channels: List[int], **backend_kwargs):
    print(f"Aspirating {ops}.")

  async def dispense(self, ops: List[Dispense], use_channels: List[int], **backend_kwargs):
    print(f"Dispensing {ops}.")

  async def pick_up_tips96(self, pickup: PickupTipRack, **backend_kwargs):
    print(f"Picking up tips from {pickup.resource.name}.")

  async def drop_tips96(self, drop: DropTipRack, **backend_kwargs):
    print(f"Dropping tips to {drop.resource.name}.")

  async def aspirate96(self, aspiration: Union[AspirationPlate, AspirationContainer]):
    if isinstance(aspiration, AspirationPlate):
      resource = aspiration.wells[0].parent
    else:
      resource = aspiration.container
    print(f"Aspirating {aspiration.volume} from {resource}.")

  async def dispense96(self, dispense: Union[DispensePlate, DispenseContainer]):
    if isinstance(dispense, DispensePlate):
      resource = dispense.wells[0].parent
    else:
      resource = dispense.container
    print(f"Dispensing {dispense.volume} to {resource}.")

  async def move_resource(self, move: Move, **backend_kwargs):
    print(f"Moving {move}.")
