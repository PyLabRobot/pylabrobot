# pylint: disable=unused-argument

from typing import Optional

from pylabrobot.liquid_handling.backends.hamilton.STAR import STAR
from pylabrobot.resources import Resource
from pylabrobot.liquid_handling.standard import (
  GripDirection,
)
from pylabrobot.resources.coordinate import Coordinate


class STARChatterBoxBackend(STAR):
  """ Chatter box backend for 'STAR' """

  def __init__(self, num_channels: int = 8):
    """ Initialize a chatter box backend. """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True

  async def setup(self):
    print("Setting up STARChatterBoxBackend.")

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

  async def get_core(self, p1: int, p2: int):
    print(f"Getting core from {p1} to {p2}")

  async def iswap_pick_up_resource(
    self,
    resource: Resource,
    grip_direction: GripDirection,
    pickup_distance_from_top: float,
    offset: Coordinate = Coordinate.zero(),
    minimum_traverse_height_at_beginning_of_a_command: int = 2840,
    z_position_at_the_command_end: int = 2840,
    grip_strength: int = 4,
    plate_width_tolerance: int = 20,
    collision_control_level: int = 0,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
    fold_up_sequence_at_the_end_of_process: bool = True
  ):
    print(f"Pick up resource {resource.name} with {grip_direction}.")

  async def iswap_move_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    grip_direction: GripDirection,
    minimum_traverse_height_at_beginning_of_a_command: int = 2840,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1
  ):
    print(f"Move picked up resource {resource.name} to {location}")

  async def iswap_release_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    offset: Coordinate,
    grip_direction: GripDirection,
    pickup_distance_from_top: float,
    minimum_traverse_height_at_beginning_of_a_command: int = 2840,
    z_position_at_the_command_end: int = 2840,
    collision_control_level: int = 0,
  ):
    print(f"Release picked up resource {resource.name} at {location} with {grip_direction}.")

  async def send_command(self, module, command, *args, **kwargs):
    print(f"Sending command: {module}{command} with args {args} and kwargs {kwargs}.")

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True
  ) -> Optional[str]:
    print(f"Sending raw command: {command}")
    return None
