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
  Move,
  GripDirection,
)
from pylabrobot.resources.coordinate import Coordinate


class ChatterBoxBackend(LiquidHandlerBackend):
  """ Chatter box backend for 'How to Open Source' """

  def __init__(self, num_channels: int = 8):
    """ Initialize a chatter box backend. """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

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
    plate = aspiration.wells[0].parent
    print(f"Aspirating {aspiration.volume} from {plate}.")

  async def dispense96(self, dispense: DispensePlate):
    plate = dispense.wells[0].parent
    print(f"Dispensing {dispense.volume} to {plate}.")

  async def move_resource(self, move: Move, **backend_kwargs):
    print(f"Moving {move}.")

  async def get_core(self, p1: int, p2: int):
    print(f"Getting core from {p1} to {p2}")

  async def iswap_pick_up_resource(self, resource: Resource, grip_direction: GripDirection, **backend_kwargs):
    print(f"Pick up resource {resource.name} with {grip_direction}.")

  async def iswap_move_picked_up_resource(self, location: Coordinate, resource: Resource, **backend_kwargs):
    print(f"Move picked up resource {resource.name} to {location}")

  async def iswap_release_picked_up_resource(self, location: Coordinate, resource: Resource, grip_direction: GripDirection, **backend_kwargs):
    print(f"Release picked up resource {resource.name} at {location} with {grip_direction}.")

  async def send_command(self, module, command, *args, **kwargs):
    print(f"Sending command: {module}{command} with args {args} and kwargs {kwargs}.")

  async def send_raw_command(self, command: str, **kwargs):
    print(f"Sending raw command: {command}")