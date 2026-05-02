"""NimbusPIPBackend: translates PIP operations into Nimbus firmware commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence, Tuple, TypeVar, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.hamilton.liquid_handlers.liquid_class import HamiltonLiquidClass
from pylabrobot.hamilton.liquid_handlers.liquid_class_resolver import (
  corrected_volumes_for_ops,
  resolve_hamilton_liquid_classes,
)
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.resources import Tip
from pylabrobot.resources.container import Container
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.trash import Trash

from .channels import ChannelType, NimbusChannelMap
from .commands import (
  Aspirate,
  DisableADC,
  Dispense as DispenseCommand,
  DropTips,
  DropTipsRoll,
  EnableADC,
  GetChannelConfiguration,
  InitializeSmartRoll,
  IsTipPresent,
  PickupTips,
  SetChannelConfiguration,
  _get_default_flow_rate,
  _get_tip_type_from_tip,
)

if TYPE_CHECKING:
  from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

  from .driver import NimbusDriver

_CHANNEL_TYPE_MAX_VOLUME_MAP: dict[ChannelType, float] = {
  ChannelType.NONE: 0.0,
  ChannelType.CHANNEL_300UL: 300.0,
  ChannelType.CHANNEL_1000UL: 1000.0,
  ChannelType.CHANNEL_5000UL: 5000.0,
}

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fill_in_defaults(val: Optional[List[T]], default: List[T]) -> List[T]:
  """If val is None, return default. Otherwise validate length and fill None entries."""
  if val is None:
    return default
  if len(val) != len(default):
    raise ValueError(f"Value length must equal num operations ({len(default)}), but is {len(val)}")
  return [v if v is not None else d for v, d in zip(val, default)]


# ---------------------------------------------------------------------------
# BackendParams dataclasses
# ---------------------------------------------------------------------------


@dataclass
class NimbusPIPPickUpTipsParams(BackendParams):
  minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None


@dataclass
class NimbusPIPDropTipsParams(BackendParams):
  minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
  default_waste: bool = False
  z_position_at_end_of_a_command: Optional[float] = None
  roll_distance: Optional[float] = None


@dataclass
class NimbusPIPAspirateParams(BackendParams):
  hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None
  disable_volume_correction: Optional[List[bool]] = None
  jet: Optional[List[bool]] = None
  blow_out: Optional[List[bool]] = None
  auto_liquid_class_lookup: Optional[Callable[..., Optional[HamiltonLiquidClass]]] = None
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
class NimbusPIPDispenseParams(BackendParams):
  hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None
  disable_volume_correction: Optional[List[bool]] = None
  jet: Optional[List[bool]] = None
  blow_out: Optional[List[bool]] = None
  auto_liquid_class_lookup: Optional[Callable[..., Optional[HamiltonLiquidClass]]] = None
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


def _coerce_nimbus_aspirate_params(
  backend_params: Optional[BackendParams],
) -> NimbusPIPAspirateParams:
  """Use Nimbus params as-is; otherwise copy overlapping fields from any backend params object."""
  if isinstance(backend_params, NimbusPIPAspirateParams):
    return backend_params
  if backend_params is None:
    return NimbusPIPAspirateParams()
  merged = {
    f.name: getattr(backend_params, f.name)
    for f in fields(NimbusPIPAspirateParams)
    if hasattr(backend_params, f.name)
  }
  return replace(NimbusPIPAspirateParams(), **merged)


def _coerce_nimbus_dispense_params(
  backend_params: Optional[BackendParams],
) -> NimbusPIPDispenseParams:
  if isinstance(backend_params, NimbusPIPDispenseParams):
    return backend_params
  if backend_params is None:
    return NimbusPIPDispenseParams()
  merged = {
    f.name: getattr(backend_params, f.name)
    for f in fields(NimbusPIPDispenseParams)
    if hasattr(backend_params, f.name)
  }
  return replace(NimbusPIPDispenseParams(), **merged)


# ---------------------------------------------------------------------------
# NimbusPIPBackend
# ---------------------------------------------------------------------------


class NimbusPIPBackend(PIPBackend):
  """PIP backend for Hamilton Nimbus liquid handlers.

  Translates abstract PIP operations (pick_up_tips, drop_tips, aspirate, dispense)
  into Nimbus-specific Hamilton TCP commands.
  """

  def __init__(
    self,
    driver: "NimbusDriver",
    deck: Optional["NimbusDeck"] = None,
    address: Optional["Address"] = None,
    num_channels: int = 8,
    traversal_height: float = 146.0,
    channel_map: Optional[NimbusChannelMap] = None,
  ):
    self.driver = driver
    self.deck = deck
    self.address = address
    self._num_channels = num_channels
    self.traversal_height = traversal_height
    self.channel_map = channel_map
    self._channel_configurations: Optional[dict] = None

  @property
  def num_channels(self) -> int:
    return self._num_channels

  @property
  def pipette_address(self) -> Address:
    if self.address is None:
      raise RuntimeError("Pipette address not set. Call setup() first.")
    return self.address

  def _ensure_deck(self) -> "NimbusDeck":
    """Return the deck, raising if not set."""
    if self.deck is None:
      raise RuntimeError("Deck must be set for pipetting operations.")
    return self.deck

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    pass

  async def _on_stop(self):
    pass

  async def _initialize_smart_roll(self):
    """Configure channels and initialize SmartRoll with waste positions."""
    self._ensure_deck()
    # Set channel configuration for each channel
    for channel in range(1, self.num_channels + 1):
      await self.driver.send_command(
        SetChannelConfiguration(
          channel=channel,
          indexes=[1, 3, 4],
          enables=[True, False, False, False],
        )
      )
    logger.info(f"Channel configuration set for {self.num_channels} channels")

    # Initialize SmartRoll using waste positions
    all_channels = list(range(self.num_channels))
    (
      x_positions_full,
      y_positions_full,
      begin_tip_deposit_process_full,
      end_tip_deposit_process_full,
      z_position_at_end_of_a_command_full,
      roll_distances_full,
    ) = self._build_waste_position_params(use_channels=all_channels)

    await self.driver.send_command(
      InitializeSmartRoll(
        x_positions=x_positions_full,
        y_positions=y_positions_full,
        begin_tip_deposit_process=begin_tip_deposit_process_full,
        end_tip_deposit_process=end_tip_deposit_process_full,
        z_position_at_end_of_a_command=z_position_at_end_of_a_command_full,
        roll_distances=roll_distances_full,
      )
    )
    logger.info("NimbusCore initialized with InitializeSmartRoll successfully")

  # ---------------------------------------------------------------------------
  # Channel fill helper
  # ---------------------------------------------------------------------------

  def _fill_by_channels(self, values: List[T], use_channels: List[int], default: T) -> List[T]:
    """Returns a full-length list of size `num_channels` where positions in `use_channels`
    are filled from `values` in order; all others are `default`."""
    if len(values) != len(use_channels):
      raise ValueError(
        f"values and channels must have same length (got {len(values)} vs {len(use_channels)})"
      )
    for ch in use_channels:
      if ch < 0 or ch >= self.num_channels:
        raise ValueError(
          f"Channel index {ch} out of range for {self.num_channels}-channel instrument"
        )
    out = [default] * self.num_channels
    for ch, v in zip(use_channels, values):
      out[ch] = v
    return out

  # ---------------------------------------------------------------------------
  # Coordinate helpers
  # ---------------------------------------------------------------------------

  def _compute_ops_xy_locations(
    self, ops: Sequence, use_channels: List[int]
  ) -> Tuple[List[int], List[int]]:
    """Compute X and Y positions in Hamilton coordinates for the given operations."""
    from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    x_positions_mm: List[float] = []
    y_positions_mm: List[float] = []

    for op in ops:
      abs_location = op.resource.get_location_wrt(self.deck)
      final_location = abs_location + op.offset
      hamilton_coord = self.deck.to_hamilton_coordinate(final_location)
      x_positions_mm.append(hamilton_coord.x)
      y_positions_mm.append(hamilton_coord.y)

    x_positions = [round(x * 100) for x in x_positions_mm]
    y_positions = [round(y * 100) for y in y_positions_mm]

    x_positions_full = self._fill_by_channels(x_positions, use_channels, default=0)
    y_positions_full = self._fill_by_channels(y_positions, use_channels, default=0)

    return x_positions_full, y_positions_full

  def _compute_tip_handling_parameters(
    self,
    ops: Sequence,
    use_channels: List[int],
    use_fixed_offset: bool = False,
    fixed_offset_mm: float = 10.0,
  ) -> Tuple[List[int], List[int]]:
    """Calculate Z positions for tip pickup/drop operations.

    Pickup (use_fixed_offset=False): Z based on tip length.
    Drop (use_fixed_offset=True): Z based on fixed offset.

    Returns: (begin_position, end_position) in 0.01mm units, full num_channels arrays.
    """
    from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    z_positions_mm: List[float] = []
    for op in ops:
      abs_location = op.resource.get_location_wrt(self.deck) + op.offset
      hamilton_coord = self.deck.to_hamilton_coordinate(abs_location)
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
    from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    x_positions_mm: List[float] = []
    y_positions_mm: List[float] = []
    z_positions_mm: List[float] = []

    for channel_idx in use_channels:
      if not hasattr(self.deck, "waste_type") or self.deck.waste_type is None:
        raise RuntimeError(
          f"Deck does not have waste_type attribute. "
          f"Cannot determine waste position for channel {channel_idx}."
        )
      waste_pos_name = f"{self.deck.waste_type}_{channel_idx + 1}"
      waste_pos = self.deck.get_resource(waste_pos_name)
      abs_location = waste_pos.get_location_wrt(self.deck)
      hamilton_coord = self.deck.to_hamilton_coordinate(abs_location)

      x_positions_mm.append(hamilton_coord.x)
      y_positions_mm.append(hamilton_coord.y)
      z_positions_mm.append(hamilton_coord.z)

    x_positions = [round(x * 100) for x in x_positions_mm]
    y_positions = [round(y * 100) for y in y_positions_mm]

    max_z_hamilton = max(z_positions_mm)
    z_start_absolute_mm = max_z_hamilton + 4.0
    z_stop_absolute_mm = max_z_hamilton

    if z_position_at_end_of_a_command is None:
      z_position_at_end_of_a_command = self.traversal_height
    if roll_distance is None:
      roll_distance = 9.0

    begin_tip_deposit_process = [round(z_start_absolute_mm * 100)] * len(use_channels)
    end_tip_deposit_process = [round(z_stop_absolute_mm * 100)] * len(use_channels)
    z_position_at_end_list = [round(z_position_at_end_of_a_command * 100)] * len(use_channels)
    roll_distances = [round(roll_distance * 100)] * len(use_channels)

    x_positions_full = self._fill_by_channels(x_positions, use_channels, default=0)
    y_positions_full = self._fill_by_channels(y_positions, use_channels, default=0)
    begin_full = self._fill_by_channels(begin_tip_deposit_process, use_channels, default=0)
    end_full = self._fill_by_channels(end_tip_deposit_process, use_channels, default=0)
    z_end_full = self._fill_by_channels(z_position_at_end_list, use_channels, default=0)
    roll_full = self._fill_by_channels(roll_distances, use_channels, default=0)

    return x_positions_full, y_positions_full, begin_full, end_full, z_end_full, roll_full

  # ---------------------------------------------------------------------------
  # PIPBackend interface
  # ---------------------------------------------------------------------------

  async def request_tip_presence(self) -> List[Optional[bool]]:
    tip_status = await self.driver.send_command(IsTipPresent())
    assert tip_status is not None, "IsTipPresent command returned None"
    tip_present = tip_status.tip_present
    return [bool(v) for v in tip_present]

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    if channel_idx >= self._num_channels:
      return False
    if self.channel_map is not None:
      ch_type = self.channel_map.channel_type(channel_idx)
      max_vol = _CHANNEL_TYPE_MAX_VOLUME_MAP.get(ch_type, 0.0)
      if max_vol > 0.0 and tip.maximal_volume > max_vol:
        return False
    return True

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Pick up tips from the specified resource.

    Z positions are calculated from resource locations and tip properties:
    - begin_tip_pick_up_process: max(resource Z) + max(tip total_tip_length)
    - end_tip_pick_up_process: max(resource Z) + max(tip total_tip_length - fitting_depth)

    Checks tip presence before pickup and raises if channels already have tips.

    Args:
      ops: List of Pickup operations, one per channel.
      use_channels: List of 0-based channel indices to use.
      backend_params: Optional :class:`NimbusPIPPickUpTipsParams`:
        - minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm
          (default: ``self.traversal_height``, typically 146.0 mm).

    Raises:
      RuntimeError: If channels already have tips mounted.
    """
    if not ops:
      return
    self._ensure_deck()
    params = (
      backend_params
      if isinstance(backend_params, NimbusPIPPickUpTipsParams)
      else NimbusPIPPickUpTipsParams()
    )

    # Check tip presence before picking up
    try:
      tip_present = await self.request_tip_presence()
      channels_with_tips = [
        i for i, present in enumerate(tip_present) if i in use_channels and present
      ]
      if channels_with_tips:
        raise RuntimeError(
          f"Cannot pick up tips: channels {channels_with_tips} already have tips mounted."
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
      traverse_height = self.traversal_height
    traverse_height_units = round(traverse_height * 100)

    command = PickupTips(
      channels_involved=channels_involved,
      x_positions=x_positions_full,
      y_positions=y_positions_full,
      minimum_traverse_height_at_beginning_of_a_command=traverse_height_units,
      begin_tip_pick_up_process=begin_tip_pick_up_process,
      end_tip_pick_up_process=end_tip_pick_up_process,
      tip_types=tip_types_full,
    )

    await self.driver.send_command(command)
    logger.info(f"Picked up tips on channels {use_channels}")

  async def drop_tips(
    self,
    ops: List[TipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Drop tips to the specified resource.

    Auto-detects waste positions and uses the appropriate firmware command:
    - If resource is a Trash, uses **DropTipsRoll** (roll-off into waste chute).
    - Otherwise, uses **DropTips** (return tips to a tip rack).

    Z positions are calculated from resource locations:
    - Waste positions: Z start/stop from deck waste coordinates via ``_build_waste_position_params``.
    - Regular resources: Fixed offset (max_z + 10 mm start, max_z stop) -- independent of tip
      length because the tip is already mounted on the pipette.

    Cannot mix waste and regular resources in a single call.

    Args:
      ops: List of TipDrop operations, one per channel.
      use_channels: List of 0-based channel indices to use.
      backend_params: Optional :class:`NimbusPIPDropTipsParams`:
        - minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm
          (default: ``self.traversal_height``).
        - default_waste: For DropTips command, if True the instrument drops to the
          default waste position (default: False).
        - z_position_at_end_of_a_command: Z final position in mm, absolute
          (default: traversal height).
        - roll_distance: Roll distance in mm for DropTipsRoll (default: 9.0 mm).

    Raises:
      ValueError: If operations mix waste and regular resources.
    """
    if not ops:
      return
    self._ensure_deck()
    params = (
      backend_params
      if isinstance(backend_params, NimbusPIPDropTipsParams)
      else NimbusPIPDropTipsParams()
    )

    # Check if resources are waste positions
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
      traverse_height = self.traversal_height
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
      z_position_at_end_list = [round(z_end * 100)] * len(ops)
      z_position_at_end_full = self._fill_by_channels(
        z_position_at_end_list, use_channels, default=0
      )

      command = DropTips(
        channels_involved=channels_involved,
        x_positions=x_positions_full,
        y_positions=y_positions_full,
        minimum_traverse_height_at_beginning_of_a_command=traverse_height_units,
        begin_tip_deposit_process=begin_tip_deposit_process,
        end_tip_deposit_process=end_tip_deposit_process,
        z_position_at_end_of_a_command=z_position_at_end_full,
        default_waste=params.default_waste,
      )

    await self.driver.send_command(command)
    logger.info(f"Dropped tips on channels {use_channels}")

  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate liquid from the specified resource.

    Volumes, flow rates, blow-out air volumes, and mix parameters are taken from the
    ``Aspiration`` operations. Hardware-level parameters are set via ``backend_params``.

    Args:
      ops: List of Aspiration operations, one per channel.
      use_channels: List of 0-based channel indices to use.
      backend_params: Optional :class:`NimbusPIPAspirateParams`:
        - minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm
          (default: ``self.traversal_height``).
        - adc_enabled: Enable Automatic Drip Control (default: False).
        - lld_mode: LLD mode per channel -- 0=OFF, 1=cLLD, 2=pLLD, 3=DUAL (default: [0]*n).
        - lld_search_height: **Relative offset** from well bottom (mm) where LLD search
          starts. The instrument adds this to minimum_height internally.
          If None, defaults to the well's size_z (i.e. search from the top of the well).
        - immersion_depth: Depth to submerge below liquid surface (mm, default: [0.0]*n).
        - surface_following_distance: Distance to follow liquid surface during aspiration
          (mm, default: [0.0]*n).
        - gamma_lld_sensitivity: Gamma LLD sensitivity, 1-4 (default: [0]*n).
        - dp_lld_sensitivity: Differential-pressure LLD sensitivity, 1-4 (default: [0]*n).
        - settling_time: Settling time after aspiration (s, default: [1.0]*n).
        - transport_air_volume: Transport air volume (uL, default: [5.0]*n).
        - pre_wetting_volume: Pre-wetting volume (uL, default: [0.0]*n).
        - swap_speed: Speed when leaving liquid (Z pull-out, mm/s; same semantics as
          STAR/HamiltonLiquidClass). If omitted, uses liquid class per op, else 25 mm/s
          when no liquid class resolves for that op.
        - mix_position_from_liquid_surface: Mix position offset from liquid surface
          (mm, default: [0.0]*n).
        - limit_curve_index: Limit curve index (default: [0]*n).
        - tadm_enabled: Enable TADM (Total Aspiration and Dispense Monitoring)
          (default: False).
    """
    if not ops:
      return
    params = _coerce_nimbus_aspirate_params(backend_params)

    n = len(ops)

    channels_involved = [0] * self.num_channels
    for channel_idx in use_channels:
      channels_involved[channel_idx] = 1

    # ADC control
    if params.adc_enabled:
      await self.driver.send_command(EnableADC(channels_involved=channels_involved))
    else:
      await self.driver.send_command(DisableADC(channels_involved=channels_involved))

    # Query channel configurations
    if self._channel_configurations is None:
      self._channel_configurations = {}
    for channel_idx in use_channels:
      channel_num = channel_idx + 1
      try:
        config = await self.driver.send_command(
          GetChannelConfiguration(channel=channel_num, indexes=[2])
        )
        assert config is not None
        enabled = config.enabled[0] if config.enabled else False
        if channel_num not in self._channel_configurations:
          self._channel_configurations[channel_num] = {}
        self._channel_configurations[channel_num][2] = enabled
      except Exception as e:
        logger.warning(f"Failed to get channel config for channel {channel_num}: {e}")

    # Compute XY positions
    x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)

    # Traverse height
    traverse_height = params.minimum_traverse_height_at_beginning_of_a_command
    if traverse_height is None:
      traverse_height = self.traversal_height
    traverse_height_units = round(traverse_height * 100)

    deck = self._ensure_deck()

    # Well bottoms
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

    hlcs = resolve_hamilton_liquid_classes(
      params.hamilton_liquid_classes,
      list(ops),
      jet=params.jet or False,
      blow_out=params.blow_out or False,
      is_aspirate=True,
      lookup=params.auto_liquid_class_lookup,
    )
    volumes = corrected_volumes_for_ops(ops, hlcs, params.disable_volume_correction)
    flow_rates = [
      op.flow_rate
      if op.flow_rate is not None
      else (
        hlc.aspiration_flow_rate
        if hlc is not None
        else _get_default_flow_rate(op.tip, is_aspirate=True)
      )
      for op, hlc in zip(ops, hlcs)
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume
      if op.blow_out_air_volume is not None
      else (hlc.aspiration_blow_out_volume if hlc is not None else 40.0)
      for op, hlc in zip(ops, hlcs)
    ]

    mix_volume = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_speed = [
      op.mix.flow_rate
      if op.mix is not None
      else (
        op.flow_rate
        if op.flow_rate is not None
        else (
          hlc.aspiration_mix_flow_rate
          if hlc is not None
          else _get_default_flow_rate(op.tip, is_aspirate=True)
        )
      )
      for op, hlc in zip(ops, hlcs)
    ]

    # Advanced parameters (backend lists override liquid-class defaults)
    lld_mode = _fill_in_defaults(params.lld_mode, [0] * n)
    immersion_depth = _fill_in_defaults(params.immersion_depth, [0.0] * n)
    surface_following_distance = _fill_in_defaults(params.surface_following_distance, [0.0] * n)
    gamma_lld_sensitivity = _fill_in_defaults(params.gamma_lld_sensitivity, [0] * n)
    dp_lld_sensitivity = _fill_in_defaults(params.dp_lld_sensitivity, [0] * n)
    settling_time = _fill_in_defaults(
      params.settling_time,
      [hlc.aspiration_settling_time if hlc is not None else 1.0 for hlc in hlcs],
    )
    transport_air_volume = _fill_in_defaults(
      params.transport_air_volume,
      [hlc.aspiration_air_transport_volume if hlc is not None else 5.0 for hlc in hlcs],
    )
    pre_wetting_volume = _fill_in_defaults(
      params.pre_wetting_volume,
      [hlc.aspiration_over_aspirate_volume if hlc is not None else 0.0 for hlc in hlcs],
    )
    swap_speed = _fill_in_defaults(
      params.swap_speed,
      [hlc.aspiration_swap_speed if hlc is not None else 25.0 for hlc in hlcs],
    )
    mix_position_from_liquid_surface = _fill_in_defaults(
      params.mix_position_from_liquid_surface, [0.0] * n
    )
    limit_curve_index = _fill_in_defaults(params.limit_curve_index, [0] * n)

    # Unit conversions
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
    # Nimbus Pipette wire: 0.01 mm/s per U32 element; swap_speed above is mm/s.
    swap_speed_units = [round(s * 100) for s in swap_speed]
    mix_volume_units = [round(v * 10) for v in mix_volume]
    mix_speed_units = [round(s * 10) for s in mix_speed]
    mix_position_from_liquid_surface_units = [
      round(p * 100) for p in mix_position_from_liquid_surface
    ]

    # Build full-channel arrays
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

    # Default values for remaining parameters
    aspirate_type = [0] * self.num_channels
    clot_detection_height = [0] * self.num_channels
    min_z_endpos = traverse_height_units
    mix_surface_following_distance = [0] * self.num_channels
    tube_section_height = [0] * self.num_channels
    tube_section_ratio = [0] * self.num_channels
    lld_height_difference = [0] * self.num_channels
    recording_mode = 0

    command = Aspirate(
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

    await self.driver.send_command(command)
    logger.info(f"Aspirated on channels {use_channels}")

  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense liquid to the specified resource.

    Volumes, flow rates, blow-out air volumes, and mix parameters are taken from the
    ``Dispense`` operations. Hardware-level parameters are set via ``backend_params``.

    Args:
      ops: List of Dispense operations, one per channel.
      use_channels: List of 0-based channel indices to use.
      backend_params: Optional :class:`NimbusPIPDispenseParams`:
        - minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm
          (default: ``self.traversal_height``).
        - adc_enabled: Enable Automatic Drip Control (default: False).
        - lld_mode: LLD mode per channel -- 0=OFF, 1=cLLD, 2=pLLD, 3=DUAL (default: [0]*n).
        - lld_search_height: **Relative offset** from well bottom (mm) where LLD search
          starts. If None, defaults to the well's size_z.
        - immersion_depth: Depth to submerge below liquid surface (mm, default: [0.0]*n).
        - surface_following_distance: Distance to follow liquid surface during dispense
          (mm, default: [0.0]*n).
        - gamma_lld_sensitivity: Gamma LLD sensitivity, 1-4 (default: [0]*n).
        - settling_time: Settling time after dispense (s, default: [1.0]*n).
        - transport_air_volume: Transport air volume (uL, default: [5.0]*n).
        - swap_speed: Speed when leaving liquid (Z pull-out, mm/s; same semantics as
          STAR/HamiltonLiquidClass). If omitted, uses liquid class per op, else 10 mm/s
          when no liquid class resolves for that op.
        - mix_position_from_liquid_surface: Mix position offset from liquid surface
          (mm, default: [0.0]*n).
        - limit_curve_index: Limit curve index (default: [0]*n).
        - tadm_enabled: Enable TADM (default: False).
        - cut_off_speed: Cut-off speed at end of dispense (uL/s, default: [25.0]*n).
        - stop_back_volume: Stop-back volume to prevent dripping (uL, default: [0.0]*n).
        - side_touch_off_distance: Side touch-off distance (mm, default: 0.0).
        - dispense_offset: Dispense Z offset (mm, default: [0.0]*n).
    """
    if not ops:
      return
    params = _coerce_nimbus_dispense_params(backend_params)

    n = len(ops)

    channels_involved = [0] * self.num_channels
    for channel_idx in use_channels:
      channels_involved[channel_idx] = 1

    # ADC control
    if params.adc_enabled:
      await self.driver.send_command(EnableADC(channels_involved=channels_involved))
    else:
      await self.driver.send_command(DisableADC(channels_involved=channels_involved))

    # Query channel configurations
    if self._channel_configurations is None:
      self._channel_configurations = {}
    for channel_idx in use_channels:
      channel_num = channel_idx + 1
      try:
        config = await self.driver.send_command(
          GetChannelConfiguration(channel=channel_num, indexes=[2])
        )
        assert config is not None
        enabled = config.enabled[0] if config.enabled else False
        if channel_num not in self._channel_configurations:
          self._channel_configurations[channel_num] = {}
        self._channel_configurations[channel_num][2] = enabled
      except Exception as e:
        logger.warning(f"Failed to get channel config for channel {channel_num}: {e}")

    # Compute XY positions
    x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)

    # Traverse height
    traverse_height = params.minimum_traverse_height_at_beginning_of_a_command
    if traverse_height is None:
      traverse_height = self.traversal_height
    traverse_height_units = round(traverse_height * 100)

    deck = self._ensure_deck()

    # Well bottoms
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

    hlcs = resolve_hamilton_liquid_classes(
      params.hamilton_liquid_classes,
      list(ops),
      jet=params.jet or False,
      blow_out=params.blow_out or False,
      is_aspirate=False,
      lookup=params.auto_liquid_class_lookup,
    )
    volumes = corrected_volumes_for_ops(ops, hlcs, params.disable_volume_correction)
    flow_rates = [
      op.flow_rate
      if op.flow_rate is not None
      else (
        hlc.dispense_flow_rate
        if hlc is not None
        else _get_default_flow_rate(op.tip, is_aspirate=False)
      )
      for op, hlc in zip(ops, hlcs)
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume
      if op.blow_out_air_volume is not None
      else (hlc.dispense_blow_out_volume if hlc is not None else 40.0)
      for op, hlc in zip(ops, hlcs)
    ]

    mix_volume = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_speed = [
      op.mix.flow_rate
      if op.mix is not None
      else (
        op.flow_rate
        if op.flow_rate is not None
        else (
          hlc.dispense_mix_flow_rate
          if hlc is not None
          else _get_default_flow_rate(op.tip, is_aspirate=False)
        )
      )
      for op, hlc in zip(ops, hlcs)
    ]

    # Advanced parameters (backend lists override liquid-class defaults)
    lld_mode = _fill_in_defaults(params.lld_mode, [0] * n)
    immersion_depth = _fill_in_defaults(params.immersion_depth, [0.0] * n)
    surface_following_distance = _fill_in_defaults(params.surface_following_distance, [0.0] * n)
    gamma_lld_sensitivity = _fill_in_defaults(params.gamma_lld_sensitivity, [0] * n)
    settling_time = _fill_in_defaults(
      params.settling_time,
      [hlc.dispense_settling_time if hlc is not None else 1.0 for hlc in hlcs],
    )
    transport_air_volume = _fill_in_defaults(
      params.transport_air_volume,
      [hlc.dispense_air_transport_volume if hlc is not None else 5.0 for hlc in hlcs],
    )
    swap_speed = _fill_in_defaults(
      params.swap_speed,
      [hlc.dispense_swap_speed if hlc is not None else 10.0 for hlc in hlcs],
    )
    mix_position_from_liquid_surface = _fill_in_defaults(
      params.mix_position_from_liquid_surface, [0.0] * n
    )
    limit_curve_index = _fill_in_defaults(params.limit_curve_index, [0] * n)
    cut_off_speed = _fill_in_defaults(
      params.cut_off_speed,
      [hlc.dispense_stop_flow_rate if hlc is not None else 25.0 for hlc in hlcs],
    )
    stop_back_volume = _fill_in_defaults(
      params.stop_back_volume,
      [hlc.dispense_stop_back_volume if hlc is not None else 0.0 for hlc in hlcs],
    )
    dispense_offset = _fill_in_defaults(params.dispense_offset, [0.0] * n)

    # Unit conversions
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
    # Nimbus Pipette wire: 0.01 mm/s per U32 element; swap_speed above is mm/s.
    swap_speed_units = [round(s * 100) for s in swap_speed]
    mix_volume_units = [round(v * 10) for v in mix_volume]
    mix_speed_units = [round(s * 10) for s in mix_speed]
    mix_position_from_liquid_surface_units = [
      round(p * 100) for p in mix_position_from_liquid_surface
    ]
    cut_off_speed_units = [round(s * 10) for s in cut_off_speed]
    stop_back_volume_units = [round(v * 10) for v in stop_back_volume]
    dispense_offset_units = [round(o * 100) for o in dispense_offset]
    side_touch_off_distance_units = round(params.side_touch_off_distance * 100)

    # Build full-channel arrays
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

    # Default values
    dispense_type = [0] * self.num_channels
    min_z_endpos = traverse_height_units
    mix_surface_following_distance = [0] * self.num_channels
    tube_section_height = [0] * self.num_channels
    tube_section_ratio = [0] * self.num_channels
    recording_mode = 0

    command = DispenseCommand(
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

    await self.driver.send_command(command)
    logger.info(f"Dispensed on channels {use_channels}")
