"""Hamilton Nimbus backend — legacy wrapper.

This module preserves the original NimbusBackend class name and import path
but internally delegates to the new Device/Driver/CapabilityBackend architecture:
  - NimbusDriver (TCP I/O, connection lifecycle, device-level ops)
  - NimbusPIPBackend (protocol translation for liquid handling)

Command classes and helpers are re-exported from the new commands module
for backwards compatibility.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pylabrobot.capabilities.liquid_handling.standard import (
  Aspiration,
  Dispense as NewDispense,
  Pickup as NewPickup,
  TipDrop,
)
from pylabrobot.hamilton.liquid_handlers.nimbus.commands import (  # noqa: F401 — re-export
  Aspirate,
  DisableADC,
  Dispense,
  DropTips,
  DropTipsRoll,
  EnableADC,
  GetChannelConfiguration,
  GetChannelConfiguration_1,
  InitializeSmartRoll,
  IsDoorLocked,
  IsInitialized,
  IsTipPresent,
  LockDoor,
  NimbusTipType,
  Park,
  PickupTips,
  PreInitializeSmart,
  SetChannelConfiguration,
  UnlockDoor,
  _get_default_flow_rate,
  _get_tip_type_from_tip,
)
from pylabrobot.hamilton.liquid_handlers.nimbus.driver import NimbusDriver
from pylabrobot.hamilton.liquid_handlers.nimbus.pip_backend import (
  AspirateParams,
  DispenseParams,
  DropTipsParams,
  NimbusPIPBackend,
  PickUpTipsParams,
)
from pylabrobot.legacy.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.standard import (
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

logger = logging.getLogger(__name__)


class NimbusBackend(LiquidHandlerBackend):
  """Legacy wrapper for Hamilton Nimbus liquid handler.

  Internally creates NimbusDriver + NimbusPIPBackend and delegates all calls.
  Preserves the original API surface for backwards compatibility.
  """

  def __init__(
    self,
    host: str,
    port: int = 2000,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
  ):
    self._nimbus_driver = NimbusDriver(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
    )
    self._pip: Optional[NimbusPIPBackend] = None
    self._pending_traversal_height: Optional[float] = None
    super().__init__()

  # ====================================================================
  # Proxy properties for backwards compatibility with tests that access
  # internal state directly.
  # ====================================================================

  @property
  def io(self):
    return self._nimbus_driver.io

  @property
  def num_channels(self) -> int:
    return self._nimbus_driver.num_channels

  @property
  def _num_channels(self):
    return self._nimbus_driver._num_channels

  @_num_channels.setter
  def _num_channels(self, value):
    self._nimbus_driver._num_channels = value
    # Eagerly create PIP backend when num_channels is set (for tests that skip setup)
    if value is not None and self._pip is None:
      self._pip = NimbusPIPBackend(self._nimbus_driver)
      if self._pending_traversal_height is not None:
        self._pip._channel_traversal_height = self._pending_traversal_height

  @property
  def _pipette_address(self):
    return self._nimbus_driver._pipette_address

  @_pipette_address.setter
  def _pipette_address(self, value):
    self._nimbus_driver._pipette_address = value

  @property
  def _door_lock_address(self):
    return self._nimbus_driver._door_lock_address

  @_door_lock_address.setter
  def _door_lock_address(self, value):
    self._nimbus_driver._door_lock_address = value

  @property
  def _nimbus_core_address(self):
    return self._nimbus_driver._nimbus_core_address

  @_nimbus_core_address.setter
  def _nimbus_core_address(self, value):
    self._nimbus_driver._nimbus_core_address = value

  @property
  def _channel_traversal_height(self):
    if self._pip is not None:
      return self._pip._channel_traversal_height
    if self._pending_traversal_height is not None:
      return self._pending_traversal_height
    return 146.0

  @_channel_traversal_height.setter
  def _channel_traversal_height(self, value):
    if self._pip is not None:
      self._pip._channel_traversal_height = value
    self._pending_traversal_height = value

  @property
  def _is_initialized(self):
    if self._pip is not None:
      return self._pip._is_initialized
    return None

  @_is_initialized.setter
  def _is_initialized(self, value):
    if self._pip is not None:
      self._pip._is_initialized = value

  @property
  def _deck(self):
    return self._nimbus_driver.deck

  @_deck.setter
  def _deck(self, value):
    self._nimbus_driver.deck = value

  @property
  def deck(self):
    return self._nimbus_driver.deck

  @deck.setter
  def deck(self, value):
    self._nimbus_driver.deck = value

  @property
  def send_command(self):
    return self._nimbus_driver.send_command

  @send_command.setter
  def send_command(self, value):
    self._nimbus_driver.send_command = value

  async def setup(self, unlock_door: bool = False, force_initialize: bool = False):
    # Wire deck reference from legacy LiquidHandlerBackend
    self._nimbus_driver.deck = self.deck
    await self._nimbus_driver.setup()
    self._pip = self._nimbus_driver.pip
    self._pip._unlock_door_after_init = unlock_door
    self._pip._force_initialize = force_initialize
    if self._pending_traversal_height is not None:
      self._pip._channel_traversal_height = self._pending_traversal_height
    await self._pip._on_setup()

  async def stop(self):
    if self._pip is not None:
      await self._pip._on_stop()
    await self._nimbus_driver.stop()

  def _fill_by_channels(self, values, use_channels, default=0):
    """Proxy to pip backend's _fill_by_channels."""
    if self._pip is not None:
      return self._pip._fill_by_channels(values, use_channels, default)
    # Fallback for pre-setup usage
    if len(values) != len(use_channels):
      raise ValueError(
        f"values and channels must have same length (got {len(values)} vs {len(use_channels)})"
      )
    out = [default] * self.num_channels
    for ch, v in zip(use_channels, values):
      out[ch] = v
    return out

  def set_minimum_channel_traversal_height(self, traversal_height: float):
    if not 0 < traversal_height < 146:
      raise ValueError(f"Traversal height must be between 0 and 146 mm (got {traversal_height})")
    if self._pip is not None:
      self._pip.set_minimum_channel_traversal_height(traversal_height)
    else:
      # Store for later when pip is created
      self._pending_traversal_height = traversal_height

  # ====================================================================
  # Device-level operations (delegate to driver)
  # ====================================================================

  async def park(self):
    await self._nimbus_driver.park()

  async def is_door_locked(self) -> bool:
    return await self._nimbus_driver.is_door_locked()

  async def lock_door(self) -> None:
    await self._nimbus_driver.lock_door()

  async def unlock_door(self) -> None:
    await self._nimbus_driver.unlock_door()

  # ====================================================================
  # PIP operations (delegate to pip backend with type conversion)
  # ====================================================================

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
  ):
    assert self._pip is not None
    # Legacy Pickup and new Pickup are structurally identical
    new_ops = [NewPickup(resource=op.resource, offset=op.offset, tip=op.tip) for op in ops]
    params = PickUpTipsParams(
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
    )
    await self._pip.pick_up_tips(new_ops, use_channels, backend_params=params)

  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    default_waste: bool = False,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_end_of_a_command: Optional[float] = None,
    roll_distance: Optional[float] = None,
  ):
    assert self._pip is not None
    new_ops = [TipDrop(resource=op.resource, offset=op.offset, tip=op.tip) for op in ops]
    params = DropTipsParams(
      default_waste=default_waste,
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
      z_position_at_end_of_a_command=z_position_at_end_of_a_command,
      roll_distance=roll_distance,
    )
    await self._pip.drop_tips(new_ops, use_channels, backend_params=params)

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    adc_enabled: bool = False,
    lld_mode: Optional[List[int]] = None,
    lld_search_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    dp_lld_sensitivity: Optional[List[int]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    pre_wetting_volume: Optional[List[float]] = None,
    swap_speed: Optional[List[float]] = None,
    mix_position_from_liquid_surface: Optional[List[float]] = None,
    limit_curve_index: Optional[List[int]] = None,
    tadm_enabled: bool = False,
  ):
    assert self._pip is not None
    new_ops = [
      Aspiration(
        resource=op.resource,
        offset=op.offset,
        tip=op.tip,
        volume=op.volume,
        flow_rate=op.flow_rate,
        liquid_height=op.liquid_height,
        blow_out_air_volume=op.blow_out_air_volume,
        mix=op.mix,
      )
      for op in ops
    ]
    params = AspirateParams(
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
      adc_enabled=adc_enabled,
      lld_mode=lld_mode,
      lld_search_height=lld_search_height,
      immersion_depth=immersion_depth,
      surface_following_distance=surface_following_distance,
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      dp_lld_sensitivity=dp_lld_sensitivity,
      settling_time=settling_time,
      transport_air_volume=transport_air_volume,
      pre_wetting_volume=pre_wetting_volume,
      swap_speed=swap_speed,
      mix_position_from_liquid_surface=mix_position_from_liquid_surface,
      limit_curve_index=limit_curve_index,
      tadm_enabled=tadm_enabled,
    )
    await self._pip.aspirate(new_ops, use_channels, backend_params=params)

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    adc_enabled: bool = False,
    lld_mode: Optional[List[int]] = None,
    lld_search_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    swap_speed: Optional[List[float]] = None,
    mix_position_from_liquid_surface: Optional[List[float]] = None,
    limit_curve_index: Optional[List[int]] = None,
    tadm_enabled: bool = False,
    cut_off_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    side_touch_off_distance: float = 0.0,
    dispense_offset: Optional[List[float]] = None,
  ):
    assert self._pip is not None
    new_ops = [
      NewDispense(
        resource=op.resource,
        offset=op.offset,
        tip=op.tip,
        volume=op.volume,
        flow_rate=op.flow_rate,
        liquid_height=op.liquid_height,
        blow_out_air_volume=op.blow_out_air_volume,
        mix=op.mix,
      )
      for op in ops
    ]
    params = DispenseParams(
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
      adc_enabled=adc_enabled,
      lld_mode=lld_mode,
      lld_search_height=lld_search_height,
      immersion_depth=immersion_depth,
      surface_following_distance=surface_following_distance,
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      settling_time=settling_time,
      transport_air_volume=transport_air_volume,
      swap_speed=swap_speed,
      mix_position_from_liquid_surface=mix_position_from_liquid_surface,
      limit_curve_index=limit_curve_index,
      tadm_enabled=tadm_enabled,
      cut_off_speed=cut_off_speed,
      stop_back_volume=stop_back_volume,
      side_touch_off_distance=side_touch_off_distance,
      dispense_offset=dispense_offset,
    )
    await self._pip.dispense(new_ops, use_channels, backend_params=params)

  async def request_tip_presence(self) -> List[Optional[bool]]:
    assert self._pip is not None
    return await self._pip.request_tip_presence()

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    if self._pip is not None:
      return self._pip.can_pick_up_tip(channel_idx, tip)
    return True

  # ====================================================================
  # Stubs for unimplemented operations
  # ====================================================================

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("pick_up_tips96 not yet implemented")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("drop_tips96 not yet implemented")

  async def aspirate96(self, aspiration: MultiHeadAspirationPlate | MultiHeadAspirationContainer):
    raise NotImplementedError("aspirate96 not yet implemented")

  async def dispense96(self, dispense: MultiHeadDispensePlate | MultiHeadDispenseContainer):
    raise NotImplementedError("dispense96 not yet implemented")

  async def pick_up_resource(self, pickup: ResourcePickup):
    raise NotImplementedError("pick_up_resource not yet implemented")

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError("move_picked_up_resource not yet implemented")

  async def drop_resource(self, drop: ResourceDrop):
    raise NotImplementedError("drop_resource not yet implemented")

  def serialize(self) -> dict:
    return self._nimbus_driver.serialize()

  @property
  def _client_id(self):
    return self._nimbus_driver._client_id

  @_client_id.setter
  def _client_id(self, value):
    self._nimbus_driver._client_id = value
