# pylint: disable=unused-argument

from pylabrobot.liquid_handling.backends.chatterbox_backend import ChatterBoxBackend
from pylabrobot.resources import Resource
from pylabrobot.liquid_handling.standard import (
  GripDirection,
)
from pylabrobot.resources.coordinate import Coordinate


class STARChatterBoxBackend(ChatterBoxBackend):
  """ Chatter box backend for 'STAR' """

  def __init__(self, num_channels: int = 8):
    """ Initialize a chatter box backend. """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

  async def get_core(self, p1: int, p2: int):
    print(f"Getting core from {p1} to {p2}")

  async def iswap_pick_up_resource(
    self,
    resource: Resource,
    grip_direction: GripDirection,
    **backend_kwargs
  ):
    print(f"Pick up resource {resource.name} with {grip_direction}.")

  async def iswap_move_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    **backend_kwargs
  ):
    print(f"Move picked up resource {resource.name} to {location}")

  async def iswap_release_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    grip_direction: GripDirection,
    **backend_kwargs
  ):
    print(f"Release picked up resource {resource.name} at {location} with {grip_direction}.")

  async def send_command(self, module, command, *args, **kwargs):
    print(f"Sending command: {module}{command} with args {args} and kwargs {kwargs}.")

  async def send_raw_command(self, command: str, **kwargs):
    print(f"Sending raw command: {command}")
