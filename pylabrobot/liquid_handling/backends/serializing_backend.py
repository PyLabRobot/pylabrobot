from abc import ABCMeta, abstractmethod
import sys
from typing import Any, Dict, Optional, List

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
)
from pylabrobot.serializer import serialize

if sys.version_info >= (3, 8):
  from typing import TypedDict
else:
  from typing_extensions import TypedDict


class SerializingBackend(LiquidHandlerBackend, metaclass=ABCMeta):
  """ A backend that serializes all commands received, and sends them to `self.send_command` for
  processing. The implementation of `send_command` is left to the subclasses. """

  def __init__(self, num_channels: int):
    LiquidHandlerBackend.__init__(self)
    self._num_channels = num_channels

  @property
  def num_channels(self) -> int:
    return self._num_channels

  @abstractmethod
  async def send_command(
    self,
    command: str,
    data: Optional[Dict[str, Any]] = None
  ) -> Optional[dict]:
    raise NotImplementedError

  async def setup(self):
    await self.send_command(command="setup")
    await super().setup()

  async def stop(self):
    await self.send_command(command="stop")
    await super().stop()

  async def assigned_resource_callback(self, resource: Resource):
    await self.send_command(command="resource_assigned", data={"resource": resource.serialize(),
      "parent_name": (resource.parent.name if resource.parent else None)})

  async def unassigned_resource_callback(self, name: str):
    await self.send_command(command="resource_unassigned", data={"resource_name": name})

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    serialized = [{
      "resource_name": op.resource.name,
      "offset": serialize(op.offset),
      "tip": op.tip.serialize(),
    } for op in ops]
    await self.send_command(
      command="pick_up_tips",
      data={"channels": serialized, "use_channels": use_channels})

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    serialized = [{
      "resource_name": op.resource.name,
      "offset": serialize(op.offset),
      "tip": op.tip.serialize(),
    } for op in ops]
    await self.send_command(
      command="drop_tips",
      data={"channels": serialized, "use_channels": use_channels})

  async def aspirate(self, ops: List[Aspiration], use_channels: List[int]):
    serialized = [{
      "resource_name": op.resource.name,
      "offset": serialize(op.offset),
      "tip": serialize(op.tip),
      "volume": op.volume,
      "flow_rate": serialize(op.flow_rate),
      "liquid_height": serialize(op.liquid_height),
      "blow_out_air_volume": serialize(op.blow_out_air_volume),
      "liquids": serialize(op.liquids),
    } for op in ops]
    await self.send_command(
      command="aspirate",
      data={"channels": serialized, "use_channels": use_channels})

  async def dispense(self, ops: List[Dispense], use_channels: List[int]):
    serialized = [{
      "resource_name": op.resource.name,
      "offset": serialize(op.offset),
      "tip": serialize(op.tip),
      "volume": op.volume,
      "flow_rate": serialize(op.flow_rate),
      "liquid_height": serialize(op.liquid_height),
      "blow_out_air_volume": serialize(op.blow_out_air_volume),
      "liquids": serialize(op.liquids),
    } for op in ops]
    await self.send_command(
      command="dispense",
      data={"channels": serialized, "use_channels": use_channels})

  async def pick_up_tips96(self, pickup: PickupTipRack):
    await self.send_command(command="pick_up_tips96", data={
      "resource_name": pickup.resource.name, "offset": serialize(pickup.offset)})

  async def drop_tips96(self, drop: DropTipRack):
    await self.send_command(command="drop_tips96", data={
      "resource_name": drop.resource.name, "offset": serialize(drop.offset)})

  async def aspirate96(self, aspiration: AspirationPlate):
    await self.send_command(command="aspirate96", data={"aspiration": {
      "resource_name": aspiration.resource.name,
      "offset": serialize(aspiration.offset),
      "volume": aspiration.volume,
      "flow_rate": serialize(aspiration.flow_rate),
      "liquid_height": serialize(aspiration.liquid_height),
      "blow_out_air_volume": serialize(aspiration.blow_out_air_volume),
      "liquids": serialize(aspiration.liquids),
      "tips": [serialize(tip) for tip in aspiration.tips],
    }})

  async def dispense96(self, dispense: DispensePlate):
    await self.send_command(command="dispense96", data={"dispense": {
      "resource_name": dispense.resource.name,
      "offset": serialize(dispense.offset),
      "volume": dispense.volume,
      "flow_rate": serialize(dispense.flow_rate),
      "liquid_height": serialize(dispense.liquid_height),
      "blow_out_air_volume": serialize(dispense.blow_out_air_volume),
      "liquids": serialize(dispense.liquids),
      "tips": [serialize(tip) for tip in dispense.tips],
    }})

  async def move_resource(self, move: Move, **backend_kwargs):
    await self.send_command(command="move", data={"move": {
      "resource_name": move.resource.name,
      "to": serialize(move.destination),
      "intermediate_locations": [serialize(loc) for loc in move.intermediate_locations],
      "resource_offset": serialize(move.resource_offset),
      "destination_offset": serialize(move.destination_offset),
      "pickup_distance_from_top": move.pickup_distance_from_top,
      "get_direction": serialize(move.get_direction),
      "put_direction": serialize(move.put_direction),
    }}, **backend_kwargs)

  async def prepare_for_manual_channel_operation(self):
    await self.send_command(command="prepare_for_manual_channel_operation")

  async def move_channel_x(self, channel: int, x: float):
    await self.send_command(command="move_channel_x", data={"channel": channel, "x": x})

  async def move_channel_y(self, channel: int, y: float):
    await self.send_command(command="move_channel_y", data={"channel": channel, "y": y})

  async def move_channel_z(self, channel: int, z: float):
    await self.send_command(command="move_channel_z", data={"channel": channel, "z": z})


class SerializingSavingBackend(SerializingBackend):
  """ A backend that saves all serialized commands in `self.sent_commands`, wrote for testing. """

  class Command(TypedDict):
    command: str
    data: Optional[Dict[str, Any]]

  async def setup(self):
    self.sent_commands: List[SerializingSavingBackend.Command] = []
    await super().setup()

  async def send_command(self, command: str, data: Optional[Dict[str, Any]] = None):
    self.sent_commands.append({"command": command, "data": data})

  def clear(self):
    self.sent_commands = []

  def get_first_data_for_command(self, command: str) -> Optional[Dict[str, Any]]:
    for sent_command in self.sent_commands:
      if sent_command["command"] == command:
        return sent_command["data"]
    return None
