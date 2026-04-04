"""Hamilton Nimbus backend implementation (legacy wrapper).

This module provides the NimbusBackend class for controlling Hamilton Nimbus
instruments via TCP communication using the Hamilton protocol.

The implementation delegates to the v1b1 modules:
- Command classes: pylabrobot.hamilton.liquid_handlers.nimbus.commands
- PIP operations: pylabrobot.hamilton.liquid_handlers.nimbus.pip_backend
- Door control: pylabrobot.hamilton.liquid_handlers.nimbus.door
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

# Re-exported for backward compatibility (tests import these from this module)
from pylabrobot.hamilton.liquid_handlers.nimbus.commands import (  # noqa: F401
  Aspirate,
  Dispense,
  DisableADC,
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
from pylabrobot.hamilton.liquid_handlers.nimbus.door import NimbusDoor
from pylabrobot.hamilton.liquid_handlers.nimbus.pip_backend import (
  NimbusPIPAspirateParams,
  NimbusPIPBackend,
  NimbusPIPDispenseParams,
  NimbusPIPDropTipsParams,
  NimbusPIPPickUpTipsParams,
)
from pylabrobot.hamilton.tcp.introspection import HamiltonIntrospection
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp_backend import HamiltonTCPBackend
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
from pylabrobot.resources.hamilton import HamiltonTip, TipSize  # noqa: F401
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

logger = logging.getLogger(__name__)


class NimbusBackend(HamiltonTCPBackend):
  """Backend for Hamilton Nimbus liquid handling instruments.

  This backend uses TCP communication with the Hamilton protocol to control
  Nimbus instruments. It delegates pipetting operations and door control to
  the v1b1 implementation while maintaining the legacy API.

  Attributes:
    _door_lock_available: Whether door lock is available on this instrument.
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
    """Initialize Nimbus backend.

    Args:
      host: Hamilton instrument IP address
      port: Hamilton instrument port (default: 2000)
      read_timeout: Read timeout in seconds
      write_timeout: Write timeout in seconds
      auto_reconnect: Enable automatic reconnection
      max_reconnect_attempts: Maximum reconnection attempts
    """
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
    )

    self._num_channels: Optional[int] = None
    self._pipette_address: Optional[Address] = None
    self._door_lock_address: Optional[Address] = None
    self._nimbus_core_address: Optional[Address] = None
    self._is_initialized: Optional[bool] = None
    self._channel_configurations: Optional[Dict[int, Dict[int, bool]]] = None

    self._channel_traversal_height: float = 146.0  # Default traversal height in mm

    # v1b1 delegates (created in setup)
    self._pip_backend: Optional[NimbusPIPBackend] = None
    self._door: Optional[NimbusDoor] = None

  async def setup(self, unlock_door: bool = False, force_initialize: bool = False):
    """Set up the Nimbus backend.

    This method:
    1. Establishes TCP connection and performs protocol initialization
    2. Discovers instrument objects
    3. Queries channel configuration to get num_channels
    4. Queries tip presence
    5. Queries initialization status
    6. Locks door if available
    7. Conditionally initializes NimbusCore with InitializeSmartRoll (only if not initialized)
    8. Optionally unlocks door after initialization

    Args:
      unlock_door: If True, unlock door after initialization (default: False)
      force_initialize: If True, force initialization even if already initialized
    """
    # Call parent setup (TCP connection, Protocol 7 init, Protocol 3 registration)
    await super().setup()

    # Discover instrument objects
    await self._discover_instrument_objects()

    # Ensure required objects are discovered
    if self._pipette_address is None:
      raise RuntimeError("Pipette object not discovered. Cannot proceed with setup.")
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore root object not discovered. Cannot proceed with setup.")

    # Query channel configuration to get num_channels
    try:
      config = await self.send_command(GetChannelConfiguration_1(self._nimbus_core_address))
      assert config is not None, "GetChannelConfiguration_1 command returned None"
      self._num_channels = config["channels"]
      logger.info(f"Channel configuration: {config['channels']} channels")
    except Exception as e:
      logger.error(f"Failed to query channel configuration: {e}")
      raise

    # Create v1b1 PIP backend delegate
    self._pip_backend = NimbusPIPBackend(
      driver=self,  # type: ignore[arg-type]  # legacy backend duck-types as driver
      deck=self.deck if isinstance(self.deck, NimbusDeck) else None,
      address=self._pipette_address,
      num_channels=self._num_channels,
      traversal_height=self._channel_traversal_height,
    )

    # Create v1b1 door delegate
    if self._door_lock_address is not None:
      self._door = NimbusDoor(
        driver=self,  # type: ignore[arg-type]
        address=self._door_lock_address,
      )

    # Query tip presence
    try:
      tip_present = await self.request_tip_presence()
      logger.info(f"Tip presence: {tip_present}")
    except Exception as e:
      logger.warning(f"Failed to query tip presence: {e}")

    # Query initialization status
    try:
      init_status = await self.send_command(IsInitialized(self._nimbus_core_address))
      assert init_status is not None, "IsInitialized command returned None"
      self._is_initialized = init_status.get("initialized", False)
      logger.info(f"Instrument initialized: {self._is_initialized}")
    except Exception as e:
      logger.error(f"Failed to query initialization status: {e}")
      raise

    # Lock door if available
    if self._door is not None:
      try:
        if not await self.is_door_locked():
          await self.lock_door()
        else:
          logger.info("Door already locked")
      except RuntimeError:
        logger.warning("Door lock operations skipped (not available or not set up)")
      except Exception as e:
        logger.warning(f"Failed to lock door: {e}")

    # Conditional initialization - only if not already initialized
    if not self._is_initialized or force_initialize:
      # Set channel configuration for each channel
      try:
        for channel in range(1, self.num_channels + 1):
          await self.send_command(
            SetChannelConfiguration(
              dest=self._pipette_address,
              channel=channel,
              indexes=[1, 3, 4],
              enables=[True, False, False, False],
            )
          )
        logger.info(f"Channel configuration set for {self.num_channels} channels")
      except Exception as e:
        logger.error(f"Failed to set channel configuration: {e}")
        raise

      # Initialize NimbusCore with InitializeSmartRoll using waste positions
      try:
        all_channels = list(range(self.num_channels))
        (
          x_positions_full,
          y_positions_full,
          begin_tip_deposit_process_full,
          end_tip_deposit_process_full,
          z_position_at_end_of_a_command_full,
          roll_distances_full,
        ) = self._pip_backend._build_waste_position_params(use_channels=all_channels)

        await self.send_command(
          InitializeSmartRoll(
            dest=self._nimbus_core_address,
            x_positions=x_positions_full,
            y_positions=y_positions_full,
            begin_tip_deposit_process=begin_tip_deposit_process_full,
            end_tip_deposit_process=end_tip_deposit_process_full,
            z_position_at_end_of_a_command=z_position_at_end_of_a_command_full,
            roll_distances=roll_distances_full,
          )
        )
        logger.info("NimbusCore initialized with InitializeSmartRoll successfully")
        self._is_initialized = True
      except Exception as e:
        logger.error(f"Failed to initialize NimbusCore with InitializeSmartRoll: {e}")
        raise
    else:
      logger.info("Instrument already initialized, skipping initialization")

    # Unlock door if requested
    if unlock_door and self._door is not None:
      try:
        await self.unlock_door()
      except RuntimeError:
        logger.warning("Door unlock requested but not available or not set up")
      except Exception as e:
        logger.warning(f"Failed to unlock door: {e}")

  async def _discover_instrument_objects(self):
    """Discover instrument-specific objects using introspection."""
    introspection = HamiltonIntrospection(self)

    root_objects = self._discovered_objects.get("root", [])
    if not root_objects:
      logger.warning("No root objects discovered")
      return

    nimbus_core_addr = root_objects[0]
    self._nimbus_core_address = nimbus_core_addr

    try:
      core_info = await introspection.get_object(nimbus_core_addr)

      for i in range(core_info.subobject_count):
        try:
          sub_addr = await introspection.get_subobject_address(nimbus_core_addr, i)
          sub_info = await introspection.get_object(sub_addr)

          if sub_info.name == "Pipette":
            self._pipette_address = sub_addr
            logger.info(f"Found Pipette at {sub_addr}")

          if sub_info.name == "DoorLock":
            self._door_lock_address = sub_addr
            logger.info(f"Found DoorLock at {sub_addr}")

        except Exception as e:
          logger.debug(f"Failed to get subobject {i}: {e}")

    except Exception as e:
      logger.warning(f"Failed to discover instrument objects: {e}")

    if self._door_lock_address is None:
      logger.info("DoorLock not available on this instrument")

  def _fill_by_channels(self, values, use_channels, default):
    """Delegate to PIP backend."""
    assert self._pip_backend is not None, "Call setup() first."
    return self._pip_backend._fill_by_channels(values, use_channels, default)

  @property
  def num_channels(self) -> int:
    """The number of channels that the robot has."""
    if self._num_channels is None:
      raise RuntimeError("num_channels not set. Call setup() first to query from instrument.")
    return self._num_channels

  def set_minimum_channel_traversal_height(self, traversal_height: float):
    """Set the minimum traversal height for the channels."""
    if not 0 < traversal_height < 146:
      raise ValueError(f"Traversal height must be between 0 and 146 mm (got {traversal_height})")
    self._channel_traversal_height = traversal_height
    if self._pip_backend is not None:
      self._pip_backend.traversal_height = traversal_height

  async def park(self):
    """Park the instrument."""
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore address not discovered. Call setup() first.")
    try:
      await self.send_command(Park(self._nimbus_core_address))
      logger.info("Instrument parked successfully")
    except Exception as e:
      logger.error(f"Failed to park instrument: {e}")
      raise

  def _ensure_door(self) -> NimbusDoor:
    """Get or lazily create the door delegate."""
    if self._door is not None:
      return self._door
    if self._door_lock_address is not None:
      self._door = NimbusDoor(driver=self, address=self._door_lock_address)  # type: ignore[arg-type]
      return self._door
    raise RuntimeError(
      "Door lock is not available on this instrument or setup() has not been called."
    )

  async def is_door_locked(self) -> bool:
    """Check if the door is locked."""
    return await self._ensure_door().is_locked()

  async def lock_door(self) -> None:
    """Lock the door."""
    await self._ensure_door().lock()

  async def unlock_door(self) -> None:
    """Unlock the door."""
    await self._ensure_door().unlock()

  async def stop(self):
    """Stop the backend and close connection."""
    await HamiltonTCPBackend.stop(self)

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Request tip presence on each channel."""
    if self._pip_backend is None:
      # Fallback for calls during setup before pip_backend is created
      if self._pipette_address is None:
        raise RuntimeError("Pipette address not discovered. Call setup() first.")
      tip_status = await self.send_command(IsTipPresent(self._pipette_address))
      assert tip_status is not None, "IsTipPresent command returned None"
      return [bool(v) for v in tip_status.get("tip_present", [])]
    return await self._pip_backend.request_tip_presence()

  # -- Pipetting operations: delegate to NimbusPIPBackend ---------------------

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
  ):
    """Pick up tips from the specified resource.

    Args:
      ops: List of Pickup operations, one per channel
      use_channels: List of channel indices to use
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm
        (optional, defaults to _channel_traversal_height)
    """
    assert self._pip_backend is not None, "Call setup() first."
    await self._pip_backend.pick_up_tips(
      ops=ops,  # type: ignore[arg-type]
      use_channels=use_channels,
      backend_params=NimbusPIPPickUpTipsParams(
        minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
      ),
    )

  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    default_waste: bool = False,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_end_of_a_command: Optional[float] = None,
    roll_distance: Optional[float] = None,
  ):
    """Drop tips to the specified resource.

    Args:
      ops: List of Drop operations, one per channel
      use_channels: List of channel indices to use
      default_waste: For DropTips command, if True, drop to default waste
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm
      z_position_at_end_of_a_command: Z final position in mm (absolute)
      roll_distance: Roll distance in mm (defaults to 9.0 mm for waste positions)
    """
    assert self._pip_backend is not None, "Call setup() first."
    await self._pip_backend.drop_tips(
      ops=ops,  # type: ignore[arg-type]
      use_channels=use_channels,
      backend_params=NimbusPIPDropTipsParams(
        minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
        default_waste=default_waste,
        z_position_at_end_of_a_command=z_position_at_end_of_a_command,
        roll_distance=roll_distance,
      ),
    )

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
    """Aspirate liquid from the specified resource.

    Args:
      ops: List of SingleChannelAspiration operations, one per channel
      use_channels: List of channel indices to use
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm
      adc_enabled: Enable ADC (Automatic Drip Control)
      lld_mode: LLD mode per channel (0=OFF, 1=cLLD, 2=pLLD, 3=DUAL)
      lld_search_height: Relative offset from well bottom for LLD search (mm)
      immersion_depth: Depth to submerge into liquid (mm)
      surface_following_distance: Distance to follow liquid surface (mm)
      gamma_lld_sensitivity: Gamma LLD sensitivity per channel (1-4)
      dp_lld_sensitivity: DP LLD sensitivity per channel (1-4)
      settling_time: Settling time per channel (s), default 1.0
      transport_air_volume: Transport air volume per channel (uL), default 5.0
      pre_wetting_volume: Pre-wetting volume per channel (uL)
      swap_speed: Swap speed on leaving liquid per channel (uL/s), default 20.0
      mix_position_from_liquid_surface: Mix position from surface per channel (mm)
      limit_curve_index: Limit curve index per channel
      tadm_enabled: TADM enabled flag
    """
    assert self._pip_backend is not None, "Call setup() first."
    await self._pip_backend.aspirate(
      ops=ops,  # type: ignore[arg-type]
      use_channels=use_channels,
      backend_params=NimbusPIPAspirateParams(
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
      ),
    )

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
    """Dispense liquid to the specified resource.

    Args:
      ops: List of SingleChannelDispense operations, one per channel
      use_channels: List of channel indices to use
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm
      adc_enabled: Enable ADC (Automatic Drip Control)
      lld_mode: LLD mode per channel (0=OFF, 1=cLLD, 2=pLLD, 3=DUAL)
      lld_search_height: Relative offset from well bottom for LLD search (mm)
      immersion_depth: Depth to submerge into liquid (mm)
      surface_following_distance: Distance to follow liquid surface (mm)
      gamma_lld_sensitivity: Gamma LLD sensitivity per channel (1-4)
      settling_time: Settling time per channel (s), default 1.0
      transport_air_volume: Transport air volume per channel (uL), default 5.0
      swap_speed: Swap speed on leaving liquid per channel (uL/s), default 20.0
      mix_position_from_liquid_surface: Mix position from surface per channel (mm)
      limit_curve_index: Limit curve index per channel
      tadm_enabled: TADM enabled flag
      cut_off_speed: Cut off speed per channel (uL/s), default 25.0
      stop_back_volume: Stop back volume per channel (uL)
      side_touch_off_distance: Side touch off distance (mm)
      dispense_offset: Dispense offset per channel (mm)
    """
    assert self._pip_backend is not None, "Call setup() first."
    await self._pip_backend.dispense(
      ops=ops,  # type: ignore[arg-type]
      use_channels=use_channels,
      backend_params=NimbusPIPDispenseParams(
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
      ),
    )

  # -- Unimplemented abstract methods ----------------------------------------

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

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    """Check if the tip can be picked up by the specified channel."""
    assert self._pip_backend is not None, "Call setup() first."
    return self._pip_backend.can_pick_up_tip(channel_idx, tip)
