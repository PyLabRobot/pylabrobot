# pylint: disable=unused-argument, inconsistent-quotes

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
  """ Chatter box backend for device-free testing. Prints out all operations. """

  _pip_length = 5
  _vol_length = 8
  _resource_length = 20
  _offset_length = 16
  _flow_rate_length = 10
  _blowout_length = 10
  _lld_z_length = 10
  _kwargs_length = 15
  _tip_type_length = 12
  _max_volume_length = 16
  _fitting_depth_length = 20
  _tip_length_length = 16
  # _pickup_method_length = 20
  _filter_length = 10

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
    print("Picking up tips:")
    header = (
      f"{'pip#':<{ChatterBoxBackend._pip_length}} "
      f"{'resource':<{ChatterBoxBackend._resource_length}} "
      f"{'offset':<{ChatterBoxBackend._offset_length}} "
      f"{'tip type':<{ChatterBoxBackend._tip_type_length}} "
      f"{'max volume (µL)':<{ChatterBoxBackend._max_volume_length}} "
      f"{'fitting depth (mm)':<{ChatterBoxBackend._fitting_depth_length}} "
      f"{'tip length (mm)':<{ChatterBoxBackend._tip_length_length}} "
      # f"{'pickup method':<{ChatterBoxBackend._pickup_method_length}} "
      f"{'filter':<{ChatterBoxBackend._filter_length}}"
    )
    print(header)

    for op, channel in zip(ops, use_channels):
      offset = f"{round(op.offset.x, 1)},{round(op.offset.y, 1)},{round(op.offset.z, 1)}"
      row = (
        f"  p{channel}: "
        f"{op.resource.name[-30:]:<{ChatterBoxBackend._resource_length}} "
        f"{offset:<{ChatterBoxBackend._offset_length}} "
        f"{op.tip.__class__.__name__:<{ChatterBoxBackend._tip_type_length}} "
        f"{op.tip.maximal_volume:<{ChatterBoxBackend._max_volume_length}} "
        f"{op.tip.fitting_depth:<{ChatterBoxBackend._fitting_depth_length}} "
        f"{op.tip.total_tip_length:<{ChatterBoxBackend._tip_length_length}} "
        # f"{str(op.tip.pickup_method)[-20:]:<{ChatterBoxBackend._pickup_method_length}} "
        f"{'Yes' if op.tip.has_filter else 'No':<{ChatterBoxBackend._filter_length}}"
      )
      print(row)

  async def drop_tips(self, ops: List[Drop], use_channels: List[int], **backend_kwargs):
    print("Dropping tips:")
    header = (
      f"{'pip#':<{ChatterBoxBackend._pip_length}} "
      f"{'resource':<{ChatterBoxBackend._resource_length}} "
      f"{'offset':<{ChatterBoxBackend._offset_length}} "
      f"{'tip type':<{ChatterBoxBackend._tip_type_length}} "
      f"{'max volume (µL)':<{ChatterBoxBackend._max_volume_length}} "
      f"{'fitting depth (mm)':<{ChatterBoxBackend._fitting_depth_length}} "
      f"{'tip length (mm)':<{ChatterBoxBackend._tip_length_length}} "
      # f"{'pickup method':<{ChatterBoxBackend._pickup_method_length}} "
      f"{'filter':<{ChatterBoxBackend._filter_length}}"
    )
    print(header)

    for op, channel in zip(ops, use_channels):
      offset = f"{round(op.offset.x, 1)},{round(op.offset.y, 1)},{round(op.offset.z, 1)}"
      row = (
        f"  p{channel}: "
        f"{op.resource.name[-30:]:<{ChatterBoxBackend._resource_length}} "
        f"{offset:<{ChatterBoxBackend._offset_length}} "
        f"{op.tip.__class__.__name__:<{ChatterBoxBackend._tip_type_length}} "
        f"{op.tip.maximal_volume:<{ChatterBoxBackend._max_volume_length}} "
        f"{op.tip.fitting_depth:<{ChatterBoxBackend._fitting_depth_length}} "
        f"{op.tip.total_tip_length:<{ChatterBoxBackend._tip_length_length}} "
        # f"{str(op.tip.pickup_method)[-20:]:<{ChatterBoxBackend._pickup_method_length}} "
        f"{'Yes' if op.tip.has_filter else 'No':<{ChatterBoxBackend._filter_length}}"
      )
      print(row)

  async def aspirate(self, ops: List[Aspiration], use_channels: List[int], **backend_kwargs):
    print("Aspirating:")
    header = (
      f"{'pip#':<{ChatterBoxBackend._pip_length}} "
      f"{'vol(ul)':<{ChatterBoxBackend._vol_length}} "
      f"{'resource':<{ChatterBoxBackend._resource_length}} "
      f"{'offset':<{ChatterBoxBackend._offset_length}} "
      f"{'flow rate':<{ChatterBoxBackend._flow_rate_length}} "
      f"{'blowout':<{ChatterBoxBackend._blowout_length}} "
      f"{'lld_z':<{ChatterBoxBackend._lld_z_length}}  "
      # f"{'liquids':<20}" # TODO: add liquids
    )
    for key in backend_kwargs:
      header += f"{key:<{ChatterBoxBackend._kwargs_length}} "[-16:]
    print(header)

    for o, p in zip(ops, use_channels):
      offset = f"{round(o.offset.x, 1)},{round(o.offset.y, 1)},{round(o.offset.z, 1)}"
      row = (
        f"  p{p}: "
        f"{o.volume:<{ChatterBoxBackend._vol_length}} "
        f"{o.resource.name[-20:]:<{ChatterBoxBackend._resource_length}} "
        f"{offset:<{ChatterBoxBackend._offset_length}} "
        f"{str(o.flow_rate):<{ChatterBoxBackend._flow_rate_length}} "
        f"{str(o.blow_out_air_volume):<{ChatterBoxBackend._blowout_length}} "
        f"{str(o.liquid_height):<{ChatterBoxBackend._lld_z_length}} "
        # f"{o.liquids if o.liquids is not None else 'none'}"
      )
      for key, value in backend_kwargs.items():
        if isinstance(value, list) and all(isinstance(v, bool) for v in value):
          value = ''.join('T' if v else 'F' for v in value)
        if isinstance(value, list):
          value = "".join(map(str, value))
        row += f" {value:<15}"
      print(row)

  async def dispense(self, ops: List[Dispense], use_channels: List[int], **backend_kwargs):
    print("Dispensing:")
    header = (
      f"{'pip#':<{ChatterBoxBackend._pip_length}} "
      f"{'vol(ul)':<{ChatterBoxBackend._vol_length}} "
      f"{'resource':<{ChatterBoxBackend._resource_length}} "
      f"{'offset':<{ChatterBoxBackend._offset_length}} "
      f"{'flow rate':<{ChatterBoxBackend._flow_rate_length}} "
      f"{'blowout':<{ChatterBoxBackend._blowout_length}} "
      f"{'lld_z':<{ChatterBoxBackend._lld_z_length}}  "
      # f"{'liquids':<20}" # TODO: add liquids
    )
    for key in backend_kwargs:
      header += f"{key:<{ChatterBoxBackend._kwargs_length}} "[-16:]
    print(header)

    for o, p in zip(ops, use_channels):
      offset = f"{round(o.offset.x, 1)},{round(o.offset.y, 1)},{round(o.offset.z, 1)}"
      row = (
        f"  p{p}: "
        f"{o.volume:<{ChatterBoxBackend._vol_length}} "
        f"{o.resource.name[-20:]:<{ChatterBoxBackend._resource_length}} "
        f"{offset:<{ChatterBoxBackend._offset_length}} "
        f"{str(o.flow_rate):<{ChatterBoxBackend._flow_rate_length}} "
        f"{str(o.blow_out_air_volume):<{ChatterBoxBackend._blowout_length}} "
        f"{str(o.liquid_height):<{ChatterBoxBackend._lld_z_length}} "
        # f"{o.liquids if o.liquids is not None else 'none'}"
      )
      for key, value in backend_kwargs.items():
        if isinstance(value, list) and all(isinstance(v, bool) for v in value):
          value = ''.join('T' if v else 'F' for v in value)
        if isinstance(value, list):
          value = "".join(map(str, value))
        row += f" {value:<{ChatterBoxBackend._kwargs_length}}"
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
