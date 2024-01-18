# pylint: disable=unused-argument

from turtle import back
from typing import List

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.resources import Resource
from pylabrobot.liquid_handling.standard import (
  GripDirection,
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
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.well import Well
from typing import Sequence, Tuple
from pylabrobot.liquid_handling.standard import PipettingOp

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

  async def send_raw_command(self, command: str):
    print(f"Sending raw command {command}.")

  async def send_command(self, module, command: str, **backend_kwargs):
    print(f"Sending module {module} command {command} params: {backend_kwargs}.")

  async def iswap_pick_up_resource(self, resource: Resource, g_dir: GripDirection, pickup_height, **backend_kwargs):
    print(f"ISWAP: Picking up {resource} w/ {g_dir} at h: {pickup_height}.")

  async def iswap_release_picked_up_resource(self, coordinate, resource: Resource, **backend_kwargs):
    print(f"ISWAP: Releasing picked up {resource} to {coordinate}.")

  def _ops_to_fw_positions(
    self,
    ops: Sequence[PipettingOp],
    use_channels: List[int]
  ) -> Tuple[List[int], List[int], List[bool]]:
    """ use_channels is a list of channels to use. STAR expects this in one-hot encoding. This is
    method converts that, and creates a matching list of x and y positions. """
    assert use_channels == sorted(use_channels), "Channels must be sorted."

    x_positions: List[int] = []
    y_positions: List[int] = []
    channels_involved: List[bool] = []
    for i, channel in enumerate(use_channels):
      while channel > len(channels_involved):
        channels_involved.append(False)
        x_positions.append(0)
        y_positions.append(0)
      channels_involved.append(True)
      offset = ops[i].offset

      x_pos = ops[i].resource.get_absolute_location().x
      if offset is None or isinstance(ops[i].resource, (TipSpot, Well)):
        x_pos += ops[i].resource.center().x
      if offset is not None:
        x_pos += offset.x
      x_positions.append(int(x_pos*10))

      y_pos = ops[i].resource.get_absolute_location().y
      if offset is None or isinstance(ops[i].resource, (TipSpot, Well)):
        y_pos += ops[i].resource.center().y
      if offset is not None:
        y_pos += offset.y
      y_positions.append(int(y_pos*10))

    if len(ops) > self.num_channels:
      raise ValueError(f"Too many channels specified: {len(ops)} > {self.num_channels}")

    if len(x_positions) < self.num_channels:
      # We do want to have a trailing zero on x_positions, y_positions, and channels_involved, for
      # some reason, if the length < 8.
      x_positions = x_positions + [0]
      y_positions = y_positions + [0]
      channels_involved = channels_involved + [False]

    return x_positions, y_positions, channels_involved