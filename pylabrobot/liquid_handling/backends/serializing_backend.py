from abc import ABCMeta, abstractmethod
from typing import Any, Dict, List, Optional, Union

from pylabrobot.liquid_handling.backends.backend import (
  LiquidHandlerBackend,
)
from pylabrobot.liquid_handling.standard import (
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
from pylabrobot.resources import Tip
from pylabrobot.serializer import serialize


class SerializingBackend(LiquidHandlerBackend, metaclass=ABCMeta):
  """A backend that serializes all commands received, and sends them to `self.send_command` for
  processing. The implementation of `send_command` is left to the subclasses."""

  def __init__(self, num_channels: int):
    LiquidHandlerBackend.__init__(self)
    self._num_channels = num_channels
    self._num_arms = 1
    self._head96_installed = True

  @property
  def num_channels(self) -> int:
    return self._num_channels

  @abstractmethod
  async def send_command(
    self, command: str, data: Optional[Dict[str, Any]] = None
  ) -> Optional[dict]:
    raise NotImplementedError

  async def setup(self):
    await super().setup()
    await self.send_command(command="setup")

  async def stop(self):
    await self.send_command(command="stop")

  def serialize(self) -> dict:
    return {**super().serialize(), "num_channels": self.num_channels}

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    serialized = [
      {
        "resource_name": op.resource.name,
        "offset": serialize(op.offset),
        "tip": op.tip.serialize(),
      }
      for op in ops
    ]
    await self.send_command(
      command="pick_up_tips",
      data={"channels": serialized, "use_channels": use_channels},
    )

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    serialized = [
      {
        "resource_name": op.resource.name,
        "offset": serialize(op.offset),
        "tip": op.tip.serialize(),
      }
      for op in ops
    ]
    await self.send_command(
      command="drop_tips",
      data={"channels": serialized, "use_channels": use_channels},
    )

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    serialized = [
      {
        "resource_name": op.resource.name,
        "offset": serialize(op.offset),
        "tip": serialize(op.tip),
        "volume": op.volume,
        "flow_rate": serialize(op.flow_rate),
        "liquid_height": serialize(op.liquid_height),
        "blow_out_air_volume": serialize(op.blow_out_air_volume),
        "mix": serialize(op.mix),
      }
      for op in ops
    ]
    await self.send_command(
      command="aspirate",
      data={"channels": serialized, "use_channels": use_channels},
    )

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    serialized = [
      {
        "resource_name": op.resource.name,
        "offset": serialize(op.offset),
        "tip": serialize(op.tip),
        "volume": op.volume,
        "flow_rate": serialize(op.flow_rate),
        "liquid_height": serialize(op.liquid_height),
        "blow_out_air_volume": serialize(op.blow_out_air_volume),
        "mix": serialize(op.mix),
      }
      for op in ops
    ]
    await self.send_command(
      command="dispense",
      data={"channels": serialized, "use_channels": use_channels},
    )

  async def pick_up_tips96(self, pickup: PickupTipRack):
    await self.send_command(
      command="pick_up_tips96",
      data={
        "resource_name": pickup.resource.name,
        "offset": serialize(pickup.offset),
      },
    )

  async def drop_tips96(self, drop: DropTipRack):
    await self.send_command(
      command="drop_tips96",
      data={
        "resource_name": drop.resource.name,
        "offset": serialize(drop.offset),
      },
    )

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    data = {
      "aspiration": {
        "offset": serialize(aspiration.offset),
        "volume": aspiration.volume,
        "flow_rate": serialize(aspiration.flow_rate),
        "liquid_height": serialize(aspiration.liquid_height),
        "blow_out_air_volume": serialize(aspiration.blow_out_air_volume),
        "tips": [serialize(tip) for tip in aspiration.tips],
      }
    }
    if isinstance(aspiration, MultiHeadAspirationPlate):
      data["aspiration"]["well_names"] = [well.name for well in aspiration.wells]
    else:
      data["aspiration"]["trough"] = aspiration.container.name
    await self.send_command(command="aspirate96", data=data)

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    data = {
      "dispense": {
        "offset": serialize(dispense.offset),
        "volume": dispense.volume,
        "flow_rate": serialize(dispense.flow_rate),
        "liquid_height": serialize(dispense.liquid_height),
        "blow_out_air_volume": serialize(dispense.blow_out_air_volume),
        "tips": [serialize(tip) for tip in dispense.tips],
      }
    }
    if isinstance(dispense, MultiHeadDispensePlate):
      data["dispense"]["well_names"] = [well.name for well in dispense.wells]
    else:
      data["dispense"]["trough"] = dispense.container.name
    await self.send_command(command="dispense96", data=data)

  async def pick_up_resource(self, pickup: ResourcePickup, **backend_kwargs):
    await self.send_command(
      command="pick_up_resource",
      data={
        "resource_name": pickup.resource.name,
        "offset": serialize(pickup.offset),
        "pickup_distance_from_top": pickup.pickup_distance_from_top,
        "direction": serialize(pickup.direction),
      },
      **backend_kwargs,
    )

  async def move_picked_up_resource(self, move: ResourceMove, **backend_kwargs):
    await self.send_command(
      command="move_picked_up_resource",
      data={
        "resource_name": move.resource.name,
        "location": serialize(move.location),
        "gripped_direction": serialize(move.gripped_direction),
      },
      **backend_kwargs,
    )

  async def drop_resource(self, drop: ResourceDrop, **backend_kwargs):
    await self.send_command(
      command="drop_resource",
      data={
        "resource_name": drop.resource.name,
        "destination": serialize(drop.destination),
        "offset": serialize(drop.offset),
        "pickup_distance_from_top": drop.pickup_distance_from_top,
        "pickup_direction": serialize(drop.pickup_direction),
        "drop_direction": serialize(drop.direction),
        "rotation": drop.rotation,
      },
      **backend_kwargs,
    )

  async def prepare_for_manual_channel_operation(self, channel: int):
    await self.send_command(
      command="prepare_for_manual_channel_operation",
      data={"channel": channel},
    )

  async def move_channel_x(self, channel: int, x: float):
    await self.send_command(command="move_channel_x", data={"channel": channel, "x": x})

  async def move_channel_y(self, channel: int, y: float):
    await self.send_command(command="move_channel_y", data={"channel": channel, "y": y})

  async def move_channel_z(self, channel: int, z: float):
    await self.send_command(command="move_channel_z", data={"channel": channel, "z": z})

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return True
