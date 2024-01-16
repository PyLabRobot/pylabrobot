# pylint: disable=unused-argument

from typing import List

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.resources import Resource
from pylabrobot.liquid_handling.standard import (
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  Dispense,
  DispensePlate,
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
    await super().stop()
    print("Stopping the robot.")

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

  async def aspirate96(self, aspiration: AspirationPlate):
    print(f"Aspirating {aspiration.volume} from {aspiration.resource}.")

  async def dispense96(self, dispense: DispensePlate):
    print(f"Dispensing {dispense.volume} to {dispense.resource}.")

  async def move_resource(self, move: Move, **backend_kwargs):
    print(f"Moving {move}.")

  async def position_single_pipetting_channel_in_y_direction(self, idx, position):
    print(f"Moving pipette {idx} to {position} in the y direction.")

  async def core_pick_up_resource(self, resource: Resource, **backend_kwargs):
    print(f"CORE: Picking up {resource}.")
  
  async def core_release_picked_up_resource(self, coordinate, resource: Resource, **backend_kwargs):
    print(f"CORE: Releasing picked up {resource} to {coordinate}.")

  async def _ops_to_fw_positions(self, ops: List[Pickup], use_channels: List[int], **backend_kwargs):
    print(f"Converting {ops} to fw positions.")

  async def iswap_pick_up_resource(self, resource: Resource, **backend_kwargs):
    print(f"ISWAP: Picking up {resource}.")

  async def send_raw_command(self, command: str):
    print(f"Sending raw command {command}.")

  async def iswap_release_picked_up_resource(self, coordinate, resource: Resource, **backend_kwargs):
    print(f"ISWAP: Releasing picked up {resource} to {coordinate}.")