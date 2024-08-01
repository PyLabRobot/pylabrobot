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
    print("Aspirating:")
    header = f"{'pip#':<5} {'vol(ul)':<8} {'resource':<20} {'offset':<16} {'flowrate':<10} {'blowout':<10} {'liq_height':<10}  " #{'liquids':<20}" #TODO: add liquids
    for key in backend_kwargs.keys():
      header += f"{key:<15} "[-16:]
    print(header)
    for o, p in zip(ops, use_channels):
      flow_rate = o.flow_rate if o.flow_rate is not None else 'none'
      row = (
        f"  p{p}: {o.volume:<8} "
        f"{o.resource.name[-20:]:<20} "
        f"{f'{round(o.offset.x, 1)},{round(o.offset.y, 1)},{round(o.offset.z, 1)}':<16} "
        f"{flow_rate:<10} "
        f"{o.blow_out_air_volume if o.blow_out_air_volume is not None else 'none':<10} "
        f"{o.liquid_height if o.liquid_height is not None else 'none':<10} "
        # f"{o.liquids if o.liquids is not None else 'none'}"
      )
      for key, value in backend_kwargs.items():
        row += f" {value:<15}"
      print(row)


  async def dispense(self, ops: List[Dispense], use_channels: List[int], **backend_kwargs):
    print("Dispensing:")
    header = f"{'pip#':<5} {'vol(ul)':<8} {'resource':<20} {'offset':<16} {'flowrate':<10} {'blowout':<10} {'liq_height':<10}  " #{'liquids':<20}" #TODO: add liquids
    for key in backend_kwargs.keys():
      header += f"{key:<15} "[-16:]
    print(header)
    for o, p in zip(ops, use_channels):
      flow_rate = o.flow_rate if o.flow_rate is not None else 'none'
      row = (
        f"  p{p}: {o.volume:<8} "
        f"{o.resource.name[-20:]:<20} "
        f"{f'{round(o.offset.x, 1)},{round(o.offset.y, 1)},{round(o.offset.z, 1)}':<16} "
        f"{flow_rate:<10} "
        f"{o.blow_out_air_volume if o.blow_out_air_volume is not None else 'none':<10} "
        f"{o.liquid_height if o.liquid_height is not None else 'none':<10} "
        # f"{o.liquids if o.liquids is not None else 'none'}"
      )
      for key, value in backend_kwargs.items():
        row += f" {value:<15}"
      print(row)

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
