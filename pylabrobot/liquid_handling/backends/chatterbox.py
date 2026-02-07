from typing import List, Union

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


class LiquidHandlerChatterboxBackend(LiquidHandlerBackend):
  """Chatter box backend for device-free testing. Prints out all operations."""

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
    """Initialize a chatter box backend."""
    super().__init__()
    self._num_channels = num_channels
    self._num_arms = 1
    self._head96_installed = True

  async def setup(self):
    await super().setup()
    print("Setting up the liquid handler.")

  async def stop(self):
    print("Stopping the liquid handler.")

  def serialize(self) -> dict:
    return {**super().serialize(), "num_channels": self.num_channels}

  @property
  def num_channels(self) -> int:
    return self._num_channels

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int], **backend_kwargs):
    print("Picking up tips:")
    header = (
      f"{'pip#':<{LiquidHandlerChatterboxBackend._pip_length}} "
      f"{'resource':<{LiquidHandlerChatterboxBackend._resource_length}} "
      f"{'offset':<{LiquidHandlerChatterboxBackend._offset_length}} "
      f"{'tip type':<{LiquidHandlerChatterboxBackend._tip_type_length}} "
      f"{'max volume (µL)':<{LiquidHandlerChatterboxBackend._max_volume_length}} "
      f"{'fitting depth (mm)':<{LiquidHandlerChatterboxBackend._fitting_depth_length}} "
      f"{'tip length (mm)':<{LiquidHandlerChatterboxBackend._tip_length_length}} "
      # f"{'pickup method':<{ChatterboxBackend._pickup_method_length}} "
      f"{'filter':<{LiquidHandlerChatterboxBackend._filter_length}}"
    )
    print(header)

    for op, channel in zip(ops, use_channels):
      offset = f"{round(op.offset.x, 1)},{round(op.offset.y, 1)},{round(op.offset.z, 1)}"
      row = (
        f"  p{channel}: "
        f"{op.resource.name[-30:]:<{LiquidHandlerChatterboxBackend._resource_length}} "
        f"{offset:<{LiquidHandlerChatterboxBackend._offset_length}} "
        f"{op.tip.__class__.__name__:<{LiquidHandlerChatterboxBackend._tip_type_length}} "
        f"{op.tip.maximal_volume:<{LiquidHandlerChatterboxBackend._max_volume_length}} "
        f"{op.tip.fitting_depth:<{LiquidHandlerChatterboxBackend._fitting_depth_length}} "
        f"{op.tip.total_tip_length:<{LiquidHandlerChatterboxBackend._tip_length_length}} "
        # f"{str(op.tip.pickup_method)[-20:]:<{ChatterboxBackend._pickup_method_length}} "
        f"{'Yes' if op.tip.has_filter else 'No':<{LiquidHandlerChatterboxBackend._filter_length}}"
      )
      print(row)

  async def drop_tips(self, ops: List[Drop], use_channels: List[int], **backend_kwargs):
    print("Dropping tips:")
    header = (
      f"{'pip#':<{LiquidHandlerChatterboxBackend._pip_length}} "
      f"{'resource':<{LiquidHandlerChatterboxBackend._resource_length}} "
      f"{'offset':<{LiquidHandlerChatterboxBackend._offset_length}} "
      f"{'tip type':<{LiquidHandlerChatterboxBackend._tip_type_length}} "
      f"{'max volume (µL)':<{LiquidHandlerChatterboxBackend._max_volume_length}} "
      f"{'fitting depth (mm)':<{LiquidHandlerChatterboxBackend._fitting_depth_length}} "
      f"{'tip length (mm)':<{LiquidHandlerChatterboxBackend._tip_length_length}} "
      # f"{'pickup method':<{ChatterboxBackend._pickup_method_length}} "
      f"{'filter':<{LiquidHandlerChatterboxBackend._filter_length}}"
    )
    print(header)

    for op, channel in zip(ops, use_channels):
      offset = f"{round(op.offset.x, 1)},{round(op.offset.y, 1)},{round(op.offset.z, 1)}"
      row = (
        f"  p{channel}: "
        f"{op.resource.name[-30:]:<{LiquidHandlerChatterboxBackend._resource_length}} "
        f"{offset:<{LiquidHandlerChatterboxBackend._offset_length}} "
        f"{op.tip.__class__.__name__:<{LiquidHandlerChatterboxBackend._tip_type_length}} "
        f"{op.tip.maximal_volume:<{LiquidHandlerChatterboxBackend._max_volume_length}} "
        f"{op.tip.fitting_depth:<{LiquidHandlerChatterboxBackend._fitting_depth_length}} "
        f"{op.tip.total_tip_length:<{LiquidHandlerChatterboxBackend._tip_length_length}} "
        # f"{str(op.tip.pickup_method)[-20:]:<{ChatterboxBackend._pickup_method_length}} "
        f"{'Yes' if op.tip.has_filter else 'No':<{LiquidHandlerChatterboxBackend._filter_length}}"
      )
      print(row)

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    **backend_kwargs,
  ):
    print("Aspirating:")
    header = (
      f"{'pip#':<{LiquidHandlerChatterboxBackend._pip_length}} "
      f"{'vol(ul)':<{LiquidHandlerChatterboxBackend._vol_length}} "
      f"{'resource':<{LiquidHandlerChatterboxBackend._resource_length}} "
      f"{'offset':<{LiquidHandlerChatterboxBackend._offset_length}} "
      f"{'flow rate':<{LiquidHandlerChatterboxBackend._flow_rate_length}} "
      f"{'blowout':<{LiquidHandlerChatterboxBackend._blowout_length}} "
      f"{'lld_z':<{LiquidHandlerChatterboxBackend._lld_z_length}}  "
    )
    for key in backend_kwargs:
      header += f"{key:<{LiquidHandlerChatterboxBackend._kwargs_length}} "[-16:]
    print(header)

    for o, p in zip(ops, use_channels):
      offset = f"{round(o.offset.x, 1)},{round(o.offset.y, 1)},{round(o.offset.z, 1)}"
      row = (
        f"  p{p}: "
        f"{o.volume:<{LiquidHandlerChatterboxBackend._vol_length}} "
        f"{o.resource.name[-20:]:<{LiquidHandlerChatterboxBackend._resource_length}} "
        f"{offset:<{LiquidHandlerChatterboxBackend._offset_length}} "
        f"{str(o.flow_rate):<{LiquidHandlerChatterboxBackend._flow_rate_length}} "
        f"{str(o.blow_out_air_volume):<{LiquidHandlerChatterboxBackend._blowout_length}} "
        f"{str(o.liquid_height):<{LiquidHandlerChatterboxBackend._lld_z_length}} "
      )
      for key, value in backend_kwargs.items():
        if isinstance(value, list) and all(isinstance(v, bool) for v in value):
          value = "".join("T" if v else "F" for v in value)
        if isinstance(value, list):
          value = "".join(map(str, value))
        row += f" {value:<15}"
      print(row)

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    **backend_kwargs,
  ):
    print("Dispensing:")
    header = (
      f"{'pip#':<{LiquidHandlerChatterboxBackend._pip_length}} "
      f"{'vol(ul)':<{LiquidHandlerChatterboxBackend._vol_length}} "
      f"{'resource':<{LiquidHandlerChatterboxBackend._resource_length}} "
      f"{'offset':<{LiquidHandlerChatterboxBackend._offset_length}} "
      f"{'flow rate':<{LiquidHandlerChatterboxBackend._flow_rate_length}} "
      f"{'blowout':<{LiquidHandlerChatterboxBackend._blowout_length}} "
      f"{'lld_z':<{LiquidHandlerChatterboxBackend._lld_z_length}}  "
    )
    for key in backend_kwargs:
      header += f"{key:<{LiquidHandlerChatterboxBackend._kwargs_length}} "[-16:]
    print(header)

    for o, p in zip(ops, use_channels):
      offset = f"{round(o.offset.x, 1)},{round(o.offset.y, 1)},{round(o.offset.z, 1)}"
      row = (
        f"  p{p}: "
        f"{o.volume:<{LiquidHandlerChatterboxBackend._vol_length}} "
        f"{o.resource.name[-20:]:<{LiquidHandlerChatterboxBackend._resource_length}} "
        f"{offset:<{LiquidHandlerChatterboxBackend._offset_length}} "
        f"{str(o.flow_rate):<{LiquidHandlerChatterboxBackend._flow_rate_length}} "
        f"{str(o.blow_out_air_volume):<{LiquidHandlerChatterboxBackend._blowout_length}} "
        f"{str(o.liquid_height):<{LiquidHandlerChatterboxBackend._lld_z_length}} "
      )
      for key, value in backend_kwargs.items():
        if isinstance(value, list) and all(isinstance(v, bool) for v in value):
          value = "".join("T" if v else "F" for v in value)
        if isinstance(value, list):
          value = "".join(map(str, value))
        row += f" {value:<{LiquidHandlerChatterboxBackend._kwargs_length}}"
      print(row)

  async def pick_up_tips96(self, pickup: PickupTipRack, **backend_kwargs):
    print(f"Picking up tips from {pickup.resource.name}.")

  async def drop_tips96(self, drop: DropTipRack, **backend_kwargs):
    print(f"Dropping tips to {drop.resource.name}.")

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    if isinstance(aspiration, MultiHeadAspirationPlate):
      resource = aspiration.wells[0].parent
    else:
      resource = aspiration.container
    print(f"Aspirating {aspiration.volume} from {resource}.")

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    if isinstance(dispense, MultiHeadDispensePlate):
      resource = dispense.wells[0].parent
    else:
      resource = dispense.container
    print(f"Dispensing {dispense.volume} to {resource}.")

  async def pick_up_resource(self, pickup: ResourcePickup):
    print(f"Picking up resource: {pickup}")

  async def move_picked_up_resource(self, move: ResourceMove):
    print(f"Moving picked up resource: {move}")

  async def drop_resource(self, drop: ResourceDrop):
    print(f"Dropping resource: {drop}")

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return True
