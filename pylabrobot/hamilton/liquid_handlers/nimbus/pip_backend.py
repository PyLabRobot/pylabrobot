"""NimbusPIPBackend: translates PIP operations into Nimbus TCP commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple, TypeVar, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import (
  Aspiration,
  Dispense as DispenseOp,
  Pickup,
  TipDrop,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.common import fill_in_defaults
from pylabrobot.resources import Tip
from pylabrobot.resources.container import Container
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck
from pylabrobot.resources.trash import Trash

from .commands import (
  Aspirate as AspirateCmd,
  DisableADC,
  Dispense as DispenseCmd,
  DropTips,
  DropTipsRoll,
  EnableADC,
  GetChannelConfiguration,
  InitializeSmartRoll,
  IsInitialized,
  IsTipPresent,
  PickupTips,
  SetChannelConfiguration,
  _get_default_flow_rate,
  _get_tip_type_from_tip,
)

if TYPE_CHECKING:
  from .driver import NimbusDriver

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ============================================================================
# Backend params dataclasses
# ============================================================================


@dataclass
class PickUpTipsParams(BackendParams):
  minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None


@dataclass
class DropTipsParams(BackendParams):
  default_waste: bool = False
  minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
  z_position_at_end_of_a_command: Optional[float] = None
  roll_distance: Optional[float] = None


@dataclass
class AspirateParams(BackendParams):
  minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
  adc_enabled: bool = False
  lld_mode: Optional[List[int]] = None
  lld_search_height: Optional[List[float]] = None
  immersion_depth: Optional[List[float]] = None
  surface_following_distance: Optional[List[float]] = None
  gamma_lld_sensitivity: Optional[List[int]] = None
  dp_lld_sensitivity: Optional[List[int]] = None
  settling_time: Optional[List[float]] = None
  transport_air_volume: Optional[List[float]] = None
  pre_wetting_volume: Optional[List[float]] = None
  swap_speed: Optional[List[float]] = None
  mix_position_from_liquid_surface: Optional[List[float]] = None
  limit_curve_index: Optional[List[int]] = None
  tadm_enabled: bool = False


@dataclass
class DispenseParams(BackendParams):
  minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
  adc_enabled: bool = False
  lld_mode: Optional[List[int]] = None
  lld_search_height: Optional[List[float]] = None
  immersion_depth: Optional[List[float]] = None
  surface_following_distance: Optional[List[float]] = None
  gamma_lld_sensitivity: Optional[List[int]] = None
  settling_time: Optional[List[float]] = None
  transport_air_volume: Optional[List[float]] = None
  swap_speed: Optional[List[float]] = None
  mix_position_from_liquid_surface: Optional[List[float]] = None
  limit_curve_index: Optional[List[int]] = None
  tadm_enabled: bool = False
  cut_off_speed: Optional[List[float]] = None
  stop_back_volume: Optional[List[float]] = None
  side_touch_off_distance: float = 0.0
  dispense_offset: Optional[List[float]] = None


# ============================================================================
# NimbusPIPBackend
# ============================================================================


class NimbusPIPBackend(PIPBackend):
  """PIP backend for Hamilton Nimbus instruments.

  Translates PIPBackend abstract operations into Nimbus TCP protocol commands
  via the NimbusDriver.
  """

  def __init__(
    self,
    driver: NimbusDriver,
    force_initialize: bool = False,
    unlock_door_after_init: bool = False,
  ):
    self._driver = driver
    self._force_initialize = force_initialize
    self._unlock_door_after_init = unlock_door_after_init

    self._channel_traversal_height: float = 146.0  # Default traversal height in mm
    self._channel_configurations: Optional[Dict[int, Dict[int, bool]]] = None
    self._is_initialized: Optional[bool] = None

  # ====================================================================
  # Lifecycle
  # ====================================================================

  async def _on_setup(self):
    """Capability-specific initialization after driver connects.

    1. Lock door if available
    2. Query initialization status
    3. Conditionally initialize (SetChannelConfiguration + InitializeSmartRoll)
    4. Optionally unlock door
    5. Query tip presence
    """
    # Lock door if available
    if self._driver._door_lock_address is not None:
      try:
        if not await self._driver.is_door_locked():
          await self._driver.lock_door()
        else:
          logger.info("Door already locked")
      except RuntimeError:
        logger.warning("Door lock operations skipped (not available or not set up)")
      except Exception as e:
        logger.warning(f"Failed to lock door: {e}")

    # Query initialization status
    try:
      init_status = await self._driver.send_command(
        IsInitialized(self._driver._nimbus_core_address)
      )
      assert init_status is not None, "IsInitialized command returned None"
      self._is_initialized = init_status.get("initialized", False)
      logger.info(f"Instrument initialized: {self._is_initialized}")
    except Exception as e:
      logger.error(f"Failed to query initialization status: {e}")
      raise

    # Conditional initialization
    if not self._is_initialized or self._force_initialize:
      # Set channel configuration for each channel
      try:
        for channel in range(1, self.num_channels + 1):
          await self._driver.send_command(
            SetChannelConfiguration(
              dest=self._driver._pipette_address,
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
        ) = self._build_waste_position_params(use_channels=all_channels)

        await self._driver.send_command(
          InitializeSmartRoll(
            dest=self._driver._nimbus_core_address,
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
    if self._unlock_door_after_init and self._driver._door_lock_address is not None:
      try:
        await self._driver.unlock_door()
      except RuntimeError:
        logger.warning("Door unlock requested but not available or not set up")
      except Exception as e:
        logger.warning(f"Failed to unlock door: {e}")

    # Query tip presence
    try:
      tip_present = await self.request_tip_presence()
      logger.info(f"Tip presence: {tip_present}")
    except Exception as e:
      logger.warning(f"Failed to query tip presence: {e}")

  async def _on_stop(self):
    pass

  # ====================================================================
  # Properties
  # ====================================================================

  @property
  def num_channels(self) -> int:
    return self._driver.num_channels

  def set_minimum_channel_traversal_height(self, traversal_height: float):
    """Set the minimum traversal height for the channels."""
    if not 0 < traversal_height < 146:
      raise ValueError(f"Traversal height must be between 0 and 146 mm (got {traversal_height})")
    self._channel_traversal_height = traversal_height

  # ====================================================================
  # Helpers
  # ====================================================================

  def _fill_by_channels(self, values: List[T], use_channels: List[int], default: T) -> List[T]:
    """Returns a full-length list of size `num_channels` where positions in `channels`
    are filled from `values` in order; all others are `default`."""
    if len(values) != len(use_channels):
      raise ValueError(
        f"values and channels must have same length (got {len(values)} vs {len(use_channels)})"
      )
    out = [default] * self.num_channels
    for ch, v in zip(use_channels, values):
      out[ch] = v
    return out

  def _get_deck(self) -> NimbusDeck:
    """Get the NimbusDeck reference from the driver."""
    if self._driver.deck is None or not isinstance(self._driver.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")
    return self._driver.deck

  def _compute_ops_xy_locations(
    self, ops: Sequence[Union[Pickup, TipDrop, Aspiration, DispenseOp]], use_channels: List[int]
  ) -> Tuple[List[int], List[int]]:
    """Compute X and Y positions in Hamilton coordinates for the given operations."""
    deck = self._get_deck()

    x_positions_mm: List[float] = []
    y_positions_mm: List[float] = []

    for op in ops:
      abs_location = op.resource.get_location_wrt(deck)
      final_location = abs_location + op.offset
      hamilton_coord = deck.to_hamilton_coordinate(final_location)
      x_positions_mm.append(hamilton_coord.x)
      y_positions_mm.append(hamilton_coord.y)

    x_positions = [round(x * 100) for x in x_positions_mm]
    y_positions = [round(y * 100) for y in y_positions_mm]

    x_positions_full = self._fill_by_channels(x_positions, use_channels, default=0)
    y_positions_full = self._fill_by_channels(y_positions, use_channels, default=0)

    return x_positions_full, y_positions_full

  def _compute_tip_handling_parameters(
    self,
    ops: Sequence[Union[Pickup, TipDrop]],
    use_channels: List[int],
    use_fixed_offset: bool = False,
    fixed_offset_mm: float = 10.0,
  ):
    """Calculate Z positions for tip pickup/drop operations.

    Pickup (use_fixed_offset=False): Z based on tip length
    Drop (use_fixed_offset=True): Z based on fixed offset

    Returns: (begin_position, end_position) in 0.01mm units
    """
    deck = self._get_deck()

    z_positions_mm: List[float] = []
    for op in ops:
      abs_location = op.resource.get_location_wrt(deck) + op.offset
      hamilton_coord = deck.to_hamilton_coordinate(abs_location)
      z_positions_mm.append(hamilton_coord.z)

    max_z_hamilton = max(z_positions_mm)

    if use_fixed_offset:
      begin_position_mm = max_z_hamilton + fixed_offset_mm
      end_position_mm = max_z_hamilton
    else:
      max_total_tip_length = max(op.tip.total_tip_length for op in ops)
      max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)
      begin_position_mm = max_z_hamilton + max_total_tip_length
      end_position_mm = max_z_hamilton + max_tip_length

    begin_position = [round(begin_position_mm * 100)] * len(ops)
    end_position = [round(end_position_mm * 100)] * len(ops)

    begin_position_full = self._fill_by_channels(begin_position, use_channels, default=0)
    end_position_full = self._fill_by_channels(end_position, use_channels, default=0)

    return begin_position_full, end_position_full

  def _build_waste_position_params(
    self,
    use_channels: List[int],
    z_position_at_end_of_a_command: Optional[float] = None,
    roll_distance: Optional[float] = None,
  ) -> Tuple[List[int], List[int], List[int], List[int], List[int], List[int]]:
    """Build waste position parameters for InitializeSmartRoll or DropTipsRoll."""
    deck = self._get_deck()

    x_positions_mm: List[float] = []
    y_positions_mm: List[float] = []
    z_positions_mm: List[float] = []

    for channel_idx in use_channels:
      if not hasattr(deck, "waste_type") or deck.waste_type is None:
        raise RuntimeError(
          f"Deck does not have waste_type attribute or waste_type is None. "
          f"Cannot determine waste position name for channel {channel_idx}."
        )
      waste_pos_name = f"{deck.waste_type}_{channel_idx + 1}"
      try:
        waste_pos = deck.get_resource(waste_pos_name)
        abs_location = waste_pos.get_location_wrt(deck)
      except Exception as e:
        raise RuntimeError(
          f"Failed to get waste position {waste_pos_name} for channel {channel_idx}: {e}"
        )

      hamilton_coord = deck.to_hamilton_coordinate(abs_location)
      x_positions_mm.append(hamilton_coord.x)
      y_positions_mm.append(hamilton_coord.y)
      z_positions_mm.append(hamilton_coord.z)

    x_positions = [round(x * 100) for x in x_positions_mm]
    y_positions = [round(y * 100) for y in y_positions_mm]

    max_z_hamilton = max(z_positions_mm)
    waste_z_hamilton = max_z_hamilton

    z_start_absolute_mm = waste_z_hamilton + 4.0
    z_stop_absolute_mm = waste_z_hamilton

    if z_position_at_end_of_a_command is None:
      z_position_at_end_of_a_command = self._channel_traversal_height
    if roll_distance is None:
      roll_distance = 9.0

    begin_tip_deposit_process = [round(z_start_absolute_mm * 100)] * len(use_channels)
    end_tip_deposit_process = [round(z_stop_absolute_mm * 100)] * len(use_channels)
    z_position_at_end_of_a_command_list = [round(z_position_at_end_of_a_command * 100)] * len(
      use_channels
    )
    roll_distances = [round(roll_distance * 100)] * len(use_channels)

    x_positions_full = self._fill_by_channels(x_positions, use_channels, default=0)
    y_positions_full = self._fill_by_channels(y_positions, use_channels, default=0)
    begin_tip_deposit_process_full = self._fill_by_channels(
      begin_tip_deposit_process, use_channels, default=0
    )
    end_tip_deposit_process_full = self._fill_by_channels(
      end_tip_deposit_process, use_channels, default=0
    )
    z_position_at_end_of_a_command_full = self._fill_by_channels(
      z_position_at_end_of_a_command_list, use_channels, default=0
    )
    roll_distances_full = self._fill_by_channels(roll_distances, use_channels, default=0)

    return (
      x_positions_full,
      y_positions_full,
      begin_tip_deposit_process_full,
      end_tip_deposit_process_full,
      z_position_at_end_of_a_command_full,
      roll_distances_full,
    )

  # ====================================================================
  # PIPBackend interface
  # ====================================================================

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    params = backend_params if isinstance(backend_params, PickUpTipsParams) else PickUpTipsParams()

    # Check tip presence before picking up tips
    try:
      tip_present = await self.request_tip_presence()
      channels_with_tips = [
        i for i, present in enumerate(tip_present) if i in use_channels and present
      ]
      if channels_with_tips:
        raise RuntimeError(
          f"Cannot pick up tips: channels {channels_with_tips} already have tips mounted. "
          f"Drop existing tips first."
        )
    except RuntimeError:
      raise
    except Exception as e:
      logger.warning(f"Could not check tip presence before pickup: {e}")

    x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)
    begin_tip_pick_up_process, end_tip_pick_up_process = self._compute_tip_handling_parameters(
      ops, use_channels
    )

    channels_involved = [int(ch in use_channels) for ch in range(self.num_channels)]

    tip_types = [_get_tip_type_from_tip(op.tip) for op in ops]
    tip_types_full = self._fill_by_channels(tip_types, use_channels, default=0)

    traverse_height = params.minimum_traverse_height_at_beginning_of_a_command
    if traverse_height is None:
      traverse_height = self._channel_traversal_height
    traverse_height_units = round(traverse_height * 100)

    command = PickupTips(
      dest=self._driver._pipette_address,
      channels_involved=channels_involved,
      x_positions=x_positions_full,
      y_positions=y_positions_full,
      minimum_traverse_height_at_beginning_of_a_command=traverse_height_units,
      begin_tip_pick_up_process=begin_tip_pick_up_process,
      end_tip_pick_up_process=end_tip_pick_up_process,
      tip_types=tip_types_full,
    )

    await self._driver.send_command(command)
    logger.info(f"Picked up tips on channels {use_channels}")

  async def drop_tips(
    self,
    ops: List[TipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    params = backend_params if isinstance(backend_params, DropTipsParams) else DropTipsParams()

    is_waste_positions = [isinstance(op.resource, Trash) for op in ops]
    all_waste = all(is_waste_positions)
    all_regular = not any(is_waste_positions)

    if not (all_waste or all_regular):
      raise ValueError(
        "Cannot mix waste positions and regular resources in a single drop_tips call."
      )

    channels_involved = [int(ch in use_channels) for ch in range(self.num_channels)]

    traverse_height = params.minimum_traverse_height_at_beginning_of_a_command
    if traverse_height is None:
      traverse_height = self._channel_traversal_height
    traverse_height_units = round(traverse_height * 100)

    command: Union[DropTips, DropTipsRoll]

    if all_waste:
      (
        x_positions_full,
        y_positions_full,
        begin_tip_deposit_process_full,
        end_tip_deposit_process_full,
        z_position_at_end_of_a_command_full,
        roll_distances_full,
      ) = self._build_waste_position_params(
        use_channels=use_channels,
        z_position_at_end_of_a_command=params.z_position_at_end_of_a_command,
        roll_distance=params.roll_distance,
      )

      command = DropTipsRoll(
        dest=self._driver._pipette_address,
        channels_involved=channels_involved,
        x_positions=x_positions_full,
        y_positions=y_positions_full,
        minimum_traverse_height_at_beginning_of_a_command=traverse_height_units,
        begin_tip_deposit_process=begin_tip_deposit_process_full,
        end_tip_deposit_process=end_tip_deposit_process_full,
        z_position_at_end_of_a_command=z_position_at_end_of_a_command_full,
        roll_distances=roll_distances_full,
      )
    else:
      x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)
      begin_tip_deposit_process, end_tip_deposit_process = self._compute_tip_handling_parameters(
        ops, use_channels, use_fixed_offset=True
      )

      z_end = params.z_position_at_end_of_a_command
      if z_end is None:
        z_end = traverse_height
      z_position_at_end_of_a_command_list = [round(z_end * 100)] * len(ops)
      z_position_at_end_of_a_command_full = self._fill_by_channels(
        z_position_at_end_of_a_command_list, use_channels, default=0
      )

      command = DropTips(
        dest=self._driver._pipette_address,
        channels_involved=channels_involved,
        x_positions=x_positions_full,
        y_positions=y_positions_full,
        minimum_traverse_height_at_beginning_of_a_command=traverse_height_units,
        begin_tip_deposit_process=begin_tip_deposit_process,
        end_tip_deposit_process=end_tip_deposit_process,
        z_position_at_end_of_a_command=z_position_at_end_of_a_command_full,
        default_waste=params.default_waste,
      )

    await self._driver.send_command(command)
    logger.info(f"Dropped tips on channels {use_channels}")

  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    params = backend_params if isinstance(backend_params, AspirateParams) else AspirateParams()

    n = len(ops)
    deck = self._get_deck()

    channels_involved = [0] * self.num_channels
    for channel_idx in use_channels:
      if channel_idx >= self.num_channels:
        raise ValueError(f"Channel index {channel_idx} exceeds num_channels {self.num_channels}")
      channels_involved[channel_idx] = 1

    # ADC control
    if params.adc_enabled:
      await self._driver.send_command(EnableADC(self._driver._pipette_address, channels_involved))
    else:
      await self._driver.send_command(DisableADC(self._driver._pipette_address, channels_involved))

    # Query channel configuration
    if self._channel_configurations is None:
      self._channel_configurations = {}
    for channel_idx in use_channels:
      channel_num = channel_idx + 1
      try:
        config = await self._driver.send_command(
          GetChannelConfiguration(
            self._driver._pipette_address,
            channel=channel_num,
            indexes=[2],
          )
        )
        assert config is not None
        enabled = config["enabled"][0] if config["enabled"] else False
        if channel_num not in self._channel_configurations:
          self._channel_configurations[channel_num] = {}
        self._channel_configurations[channel_num][2] = enabled
      except Exception as e:
        logger.warning(f"Failed to get channel configuration for channel {channel_num}: {e}")

    # Compute positions
    x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)

    traverse_height = params.minimum_traverse_height_at_beginning_of_a_command
    if traverse_height is None:
      traverse_height = self._channel_traversal_height
    traverse_height_units = round(traverse_height * 100)

    # Calculate well bottoms
    well_bottoms = []
    for op in ops:
      abs_location = op.resource.get_location_wrt(deck) + op.offset
      if isinstance(op.resource, Container):
        abs_location.z += op.resource.material_z_thickness
      hamilton_coord = deck.to_hamilton_coordinate(abs_location)
      well_bottoms.append(hamilton_coord.z)

    liquid_heights_mm = [wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)]

    lld_search_height = params.lld_search_height
    if lld_search_height is None:
      lld_search_height = [op.resource.get_absolute_size_z() for op in ops]

    minimum_heights_mm = well_bottoms.copy()

    volumes = [op.volume for op in ops]
    flow_rates: List[float] = [
      op.flow_rate if op.flow_rate is not None else _get_default_flow_rate(op.tip, is_aspirate=True)
      for op in ops
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume if op.blow_out_air_volume is not None else 40.0 for op in ops
    ]

    mix_volume: List[float] = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles: List[int] = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_speed: List[float] = [
      op.mix.flow_rate
      if op.mix is not None
      else (
        op.flow_rate
        if op.flow_rate is not None
        else _get_default_flow_rate(op.tip, is_aspirate=True)
      )
      for op in ops
    ]

    # Fill in defaults for advanced parameters
    lld_mode = fill_in_defaults(params.lld_mode, [0] * n)
    immersion_depth = fill_in_defaults(params.immersion_depth, [0.0] * n)
    surface_following_distance = fill_in_defaults(params.surface_following_distance, [0.0] * n)
    gamma_lld_sensitivity = fill_in_defaults(params.gamma_lld_sensitivity, [0] * n)
    dp_lld_sensitivity = fill_in_defaults(params.dp_lld_sensitivity, [0] * n)
    settling_time = fill_in_defaults(params.settling_time, [1.0] * n)
    transport_air_volume = fill_in_defaults(params.transport_air_volume, [5.0] * n)
    pre_wetting_volume = fill_in_defaults(params.pre_wetting_volume, [0.0] * n)
    swap_speed = fill_in_defaults(params.swap_speed, [20.0] * n)
    mix_position_from_liquid_surface = fill_in_defaults(
      params.mix_position_from_liquid_surface, [0.0] * n
    )
    limit_curve_index = fill_in_defaults(params.limit_curve_index, [0] * n)

    # Convert units and build full arrays
    aspirate_volumes = [round(vol * 10) for vol in volumes]
    blow_out_air_volumes_units = [round(vol * 10) for vol in blow_out_air_volumes]
    aspiration_speeds = [round(fr * 10) for fr in flow_rates]
    lld_search_height_units = [round(h * 100) for h in lld_search_height]
    liquid_height_units = [round(h * 100) for h in liquid_heights_mm]
    immersion_depth_units = [round(d * 100) for d in immersion_depth]
    surface_following_distance_units = [round(d * 100) for d in surface_following_distance]
    minimum_height_units = [round(z * 100) for z in minimum_heights_mm]
    settling_time_units = [round(t * 10) for t in settling_time]
    transport_air_volume_units = [round(v * 10) for v in transport_air_volume]
    pre_wetting_volume_units = [round(v * 10) for v in pre_wetting_volume]
    swap_speed_units = [round(s * 10) for s in swap_speed]
    mix_volume_units = [round(v * 10) for v in mix_volume]
    mix_speed_units = [round(s * 10) for s in mix_speed]
    mix_position_from_liquid_surface_units = [
      round(p * 100) for p in mix_position_from_liquid_surface
    ]

    aspirate_volumes_full = self._fill_by_channels(aspirate_volumes, use_channels, default=0)
    blow_out_air_volumes_full = self._fill_by_channels(
      blow_out_air_volumes_units, use_channels, default=0
    )
    aspiration_speeds_full = self._fill_by_channels(aspiration_speeds, use_channels, default=0)
    lld_search_height_full = self._fill_by_channels(
      lld_search_height_units, use_channels, default=0
    )
    liquid_height_full = self._fill_by_channels(liquid_height_units, use_channels, default=0)
    immersion_depth_full = self._fill_by_channels(immersion_depth_units, use_channels, default=0)
    surface_following_distance_full = self._fill_by_channels(
      surface_following_distance_units, use_channels, default=0
    )
    minimum_height_full = self._fill_by_channels(minimum_height_units, use_channels, default=0)
    settling_time_full = self._fill_by_channels(settling_time_units, use_channels, default=0)
    transport_air_volume_full = self._fill_by_channels(
      transport_air_volume_units, use_channels, default=0
    )
    pre_wetting_volume_full = self._fill_by_channels(
      pre_wetting_volume_units, use_channels, default=0
    )
    swap_speed_full = self._fill_by_channels(swap_speed_units, use_channels, default=0)
    mix_volume_full = self._fill_by_channels(mix_volume_units, use_channels, default=0)
    mix_cycles_full = self._fill_by_channels(mix_cycles, use_channels, default=0)
    mix_speed_full = self._fill_by_channels(mix_speed_units, use_channels, default=0)
    mix_position_from_liquid_surface_full = self._fill_by_channels(
      mix_position_from_liquid_surface_units, use_channels, default=0
    )
    gamma_lld_sensitivity_full = self._fill_by_channels(
      gamma_lld_sensitivity, use_channels, default=0
    )
    dp_lld_sensitivity_full = self._fill_by_channels(dp_lld_sensitivity, use_channels, default=0)
    limit_curve_index_full = self._fill_by_channels(limit_curve_index, use_channels, default=0)
    lld_mode_full = self._fill_by_channels(lld_mode, use_channels, default=0)

    aspirate_type = [0] * self.num_channels
    clot_detection_height = [0] * self.num_channels
    min_z_endpos = traverse_height_units
    mix_surface_following_distance = [0] * self.num_channels
    tube_section_height = [0] * self.num_channels
    tube_section_ratio = [0] * self.num_channels
    lld_height_difference = [0] * self.num_channels
    recording_mode = 0

    command = AspirateCmd(
      dest=self._driver._pipette_address,
      aspirate_type=aspirate_type,
      channels_involved=channels_involved,
      x_positions=x_positions_full,
      y_positions=y_positions_full,
      minimum_traverse_height_at_beginning_of_a_command=traverse_height_units,
      lld_search_height=lld_search_height_full,
      liquid_height=liquid_height_full,
      immersion_depth=immersion_depth_full,
      surface_following_distance=surface_following_distance_full,
      minimum_height=minimum_height_full,
      clot_detection_height=clot_detection_height,
      min_z_endpos=min_z_endpos,
      swap_speed=swap_speed_full,
      blow_out_air_volume=blow_out_air_volumes_full,
      pre_wetting_volume=pre_wetting_volume_full,
      aspirate_volume=aspirate_volumes_full,
      transport_air_volume=transport_air_volume_full,
      aspiration_speed=aspiration_speeds_full,
      settling_time=settling_time_full,
      mix_volume=mix_volume_full,
      mix_cycles=mix_cycles_full,
      mix_position_from_liquid_surface=mix_position_from_liquid_surface_full,
      mix_surface_following_distance=mix_surface_following_distance,
      mix_speed=mix_speed_full,
      tube_section_height=tube_section_height,
      tube_section_ratio=tube_section_ratio,
      lld_mode=lld_mode_full,
      gamma_lld_sensitivity=gamma_lld_sensitivity_full,
      dp_lld_sensitivity=dp_lld_sensitivity_full,
      lld_height_difference=lld_height_difference,
      tadm_enabled=params.tadm_enabled,
      limit_curve_index=limit_curve_index_full,
      recording_mode=recording_mode,
    )

    await self._driver.send_command(command)
    logger.info(f"Aspirated on channels {use_channels}")

  async def dispense(
    self,
    ops: List[DispenseOp],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    params = backend_params if isinstance(backend_params, DispenseParams) else DispenseParams()

    n = len(ops)
    deck = self._get_deck()

    channels_involved = [0] * self.num_channels
    for channel_idx in use_channels:
      if channel_idx >= self.num_channels:
        raise ValueError(f"Channel index {channel_idx} exceeds num_channels {self.num_channels}")
      channels_involved[channel_idx] = 1

    # ADC control
    if params.adc_enabled:
      await self._driver.send_command(EnableADC(self._driver._pipette_address, channels_involved))
    else:
      await self._driver.send_command(DisableADC(self._driver._pipette_address, channels_involved))

    # Query channel configuration
    if self._channel_configurations is None:
      self._channel_configurations = {}
    for channel_idx in use_channels:
      channel_num = channel_idx + 1
      try:
        config = await self._driver.send_command(
          GetChannelConfiguration(
            self._driver._pipette_address,
            channel=channel_num,
            indexes=[2],
          )
        )
        assert config is not None
        enabled = config["enabled"][0] if config["enabled"] else False
        if channel_num not in self._channel_configurations:
          self._channel_configurations[channel_num] = {}
        self._channel_configurations[channel_num][2] = enabled
      except Exception as e:
        logger.warning(f"Failed to get channel configuration for channel {channel_num}: {e}")

    # Compute positions
    x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)

    traverse_height = params.minimum_traverse_height_at_beginning_of_a_command
    if traverse_height is None:
      traverse_height = self._channel_traversal_height
    traverse_height_units = round(traverse_height * 100)

    # Calculate well bottoms
    well_bottoms = []
    for op in ops:
      abs_location = op.resource.get_location_wrt(deck) + op.offset
      if isinstance(op.resource, Container):
        abs_location.z += op.resource.material_z_thickness
      hamilton_coord = deck.to_hamilton_coordinate(abs_location)
      well_bottoms.append(hamilton_coord.z)

    liquid_heights_mm = [wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)]

    lld_search_height = params.lld_search_height
    if lld_search_height is None:
      lld_search_height = [op.resource.get_absolute_size_z() for op in ops]

    minimum_heights_mm = well_bottoms.copy()

    volumes = [op.volume for op in ops]
    flow_rates: List[float] = [
      op.flow_rate
      if op.flow_rate is not None
      else _get_default_flow_rate(op.tip, is_aspirate=False)
      for op in ops
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume if op.blow_out_air_volume is not None else 40.0 for op in ops
    ]

    mix_volume: List[float] = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles: List[int] = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_speed: List[float] = [
      op.mix.flow_rate
      if op.mix is not None
      else (
        op.flow_rate
        if op.flow_rate is not None
        else _get_default_flow_rate(op.tip, is_aspirate=False)
      )
      for op in ops
    ]

    # Fill in defaults for advanced parameters
    lld_mode = fill_in_defaults(params.lld_mode, [0] * n)
    immersion_depth = fill_in_defaults(params.immersion_depth, [0.0] * n)
    surface_following_distance = fill_in_defaults(params.surface_following_distance, [0.0] * n)
    gamma_lld_sensitivity = fill_in_defaults(params.gamma_lld_sensitivity, [0] * n)
    settling_time = fill_in_defaults(params.settling_time, [1.0] * n)
    transport_air_volume = fill_in_defaults(params.transport_air_volume, [5.0] * n)
    swap_speed = fill_in_defaults(params.swap_speed, [20.0] * n)
    mix_position_from_liquid_surface = fill_in_defaults(
      params.mix_position_from_liquid_surface, [0.0] * n
    )
    limit_curve_index = fill_in_defaults(params.limit_curve_index, [0] * n)
    cut_off_speed = fill_in_defaults(params.cut_off_speed, [25.0] * n)
    stop_back_volume = fill_in_defaults(params.stop_back_volume, [0.0] * n)
    dispense_offset = fill_in_defaults(params.dispense_offset, [0.0] * n)

    # Convert units
    dispense_volumes = [round(vol * 10) for vol in volumes]
    blow_out_air_volumes_units = [round(vol * 10) for vol in blow_out_air_volumes]
    dispense_speeds = [round(fr * 10) for fr in flow_rates]
    lld_search_height_units = [round(h * 100) for h in lld_search_height]
    liquid_height_units = [round(h * 100) for h in liquid_heights_mm]
    immersion_depth_units = [round(d * 100) for d in immersion_depth]
    surface_following_distance_units = [round(d * 100) for d in surface_following_distance]
    minimum_height_units = [round(z * 100) for z in minimum_heights_mm]
    settling_time_units = [round(t * 10) for t in settling_time]
    transport_air_volume_units = [round(v * 10) for v in transport_air_volume]
    swap_speed_units = [round(s * 10) for s in swap_speed]
    mix_volume_units = [round(v * 10) for v in mix_volume]
    mix_speed_units = [round(s * 10) for s in mix_speed]
    mix_position_from_liquid_surface_units = [
      round(p * 100) for p in mix_position_from_liquid_surface
    ]
    cut_off_speed_units = [round(s * 10) for s in cut_off_speed]
    stop_back_volume_units = [round(v * 10) for v in stop_back_volume]
    dispense_offset_units = [round(o * 100) for o in dispense_offset]
    side_touch_off_distance_units = round(params.side_touch_off_distance * 100)

    # Build full arrays
    dispense_volumes_full = self._fill_by_channels(dispense_volumes, use_channels, default=0)
    blow_out_air_volumes_full = self._fill_by_channels(
      blow_out_air_volumes_units, use_channels, default=0
    )
    dispense_speeds_full = self._fill_by_channels(dispense_speeds, use_channels, default=0)
    lld_search_height_full = self._fill_by_channels(
      lld_search_height_units, use_channels, default=0
    )
    liquid_height_full = self._fill_by_channels(liquid_height_units, use_channels, default=0)
    immersion_depth_full = self._fill_by_channels(immersion_depth_units, use_channels, default=0)
    surface_following_distance_full = self._fill_by_channels(
      surface_following_distance_units, use_channels, default=0
    )
    minimum_height_full = self._fill_by_channels(minimum_height_units, use_channels, default=0)
    settling_time_full = self._fill_by_channels(settling_time_units, use_channels, default=0)
    transport_air_volume_full = self._fill_by_channels(
      transport_air_volume_units, use_channels, default=0
    )
    swap_speed_full = self._fill_by_channels(swap_speed_units, use_channels, default=0)
    mix_volume_full = self._fill_by_channels(mix_volume_units, use_channels, default=0)
    mix_cycles_full = self._fill_by_channels(mix_cycles, use_channels, default=0)
    mix_speed_full = self._fill_by_channels(mix_speed_units, use_channels, default=0)
    mix_position_from_liquid_surface_full = self._fill_by_channels(
      mix_position_from_liquid_surface_units, use_channels, default=0
    )
    gamma_lld_sensitivity_full = self._fill_by_channels(
      gamma_lld_sensitivity, use_channels, default=0
    )
    limit_curve_index_full = self._fill_by_channels(limit_curve_index, use_channels, default=0)
    lld_mode_full = self._fill_by_channels(lld_mode, use_channels, default=0)
    cut_off_speed_full = self._fill_by_channels(cut_off_speed_units, use_channels, default=0)
    stop_back_volume_full = self._fill_by_channels(stop_back_volume_units, use_channels, default=0)
    dispense_offset_full = self._fill_by_channels(dispense_offset_units, use_channels, default=0)

    dispense_type = [0] * self.num_channels
    min_z_endpos = traverse_height_units
    mix_surface_following_distance = [0] * self.num_channels
    tube_section_height = [0] * self.num_channels
    tube_section_ratio = [0] * self.num_channels
    recording_mode = 0

    command = DispenseCmd(
      dest=self._driver._pipette_address,
      dispense_type=dispense_type,
      channels_involved=channels_involved,
      x_positions=x_positions_full,
      y_positions=y_positions_full,
      minimum_traverse_height_at_beginning_of_a_command=traverse_height_units,
      lld_search_height=lld_search_height_full,
      liquid_height=liquid_height_full,
      immersion_depth=immersion_depth_full,
      surface_following_distance=surface_following_distance_full,
      minimum_height=minimum_height_full,
      min_z_endpos=min_z_endpos,
      swap_speed=swap_speed_full,
      transport_air_volume=transport_air_volume_full,
      dispense_volume=dispense_volumes_full,
      stop_back_volume=stop_back_volume_full,
      blow_out_air_volume=blow_out_air_volumes_full,
      dispense_speed=dispense_speeds_full,
      cut_off_speed=cut_off_speed_full,
      settling_time=settling_time_full,
      mix_volume=mix_volume_full,
      mix_cycles=mix_cycles_full,
      mix_position_from_liquid_surface=mix_position_from_liquid_surface_full,
      mix_surface_following_distance=mix_surface_following_distance,
      mix_speed=mix_speed_full,
      side_touch_off_distance=side_touch_off_distance_units,
      dispense_offset=dispense_offset_full,
      tube_section_height=tube_section_height,
      tube_section_ratio=tube_section_ratio,
      lld_mode=lld_mode_full,
      gamma_lld_sensitivity=gamma_lld_sensitivity_full,
      tadm_enabled=params.tadm_enabled,
      limit_curve_index=limit_curve_index_full,
      recording_mode=recording_mode,
    )

    await self._driver.send_command(command)
    logger.info(f"Dispensed on channels {use_channels}")

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    if self._driver._num_channels is not None and channel_idx >= self._driver._num_channels:
      return False
    return True

  async def request_tip_presence(self) -> List[Optional[bool]]:
    if self._driver._pipette_address is None:
      raise RuntimeError("Pipette address not discovered. Call setup() first.")
    tip_status = await self._driver.send_command(IsTipPresent(self._driver._pipette_address))
    assert tip_status is not None, "IsTipPresent command returned None"
    tip_present = tip_status.get("tip_present", [])
    return [bool(v) for v in tip_present]
