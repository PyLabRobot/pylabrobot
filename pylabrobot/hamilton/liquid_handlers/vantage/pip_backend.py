"""Vantage PIP backend: translates PIP operations into Vantage firmware commands."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple, Union, cast

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.hamilton.lh.vantage.liquid_classes import get_vantage_liquid_class
from pylabrobot.hamilton.liquid_handlers.liquid_class import HamiltonLiquidClass
from pylabrobot.resources import Resource, Tip, Well
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.liquid import Liquid

from .errors import VantageFirmwareError, convert_vantage_firmware_error_to_plr_error

if TYPE_CHECKING:
  from .driver import VantageDriver

logger = logging.getLogger("pylabrobot")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LLDMode(enum.Enum):
  """Liquid level detection mode for Vantage PIP channels.

  Controls how the pipetting channel detects the liquid surface inside a container.

  Attributes:
    OFF: No liquid level detection. The channel moves to a fixed Z position.
    GAMMA: Capacitive (gamma) liquid level detection. Detects the liquid surface by
      measuring capacitance changes at the tip.
    PRESSURE: Pressure-based liquid level detection. Detects the liquid surface by
      monitoring pressure changes during descent.
    DUAL: Dual LLD mode combining both capacitive and pressure detection for higher
      reliability. Available for aspiration only.
    Z_TOUCH_OFF: Z touch-off mode. The channel descends until mechanical contact is
      detected. Turns off capacitive and pressure LLD.
  """

  OFF = 0
  GAMMA = 1
  PRESSURE = 2
  DUAL = 3
  Z_TOUCH_OFF = 4


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _get_dispense_mode(jet: bool, empty: bool, blow_out: bool) -> int:
  """Compute firmware dispensing mode integer from boolean flags.

  The Vantage firmware uses a single integer to encode the dispensing strategy.
  This function maps the three orthogonal boolean flags to that integer.

  Firmware modes:
    0 = Partial volume in jet mode (dispense from above the liquid surface)
    1 = Blow out in jet mode (labelled "empty" in VENUS; full tip evacuation from above)
    2 = Partial volume at surface (dispense while tracking the liquid surface)
    3 = Blow out at surface (labelled "empty" in VENUS; full evacuation at surface)
    4 = Empty tip at fix position (complete tip emptying at a fixed Z)

  Args:
    jet: If True, dispense in jet mode (tip above the liquid surface).
      If False, dispense at the liquid surface.
    empty: If True, empty the tip completely at a fixed position (overrides jet/blow_out).
    blow_out: If True, perform a blow-out after dispensing. Combined with jet to
      select between jet blow-out (mode 1) and surface blow-out (mode 3).

  Returns:
    Integer firmware dispensing mode (0-4).
  """
  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  return 3 if blow_out else 2


def _ops_to_fw_positions(
  ops: Sequence[Union[Pickup, TipDrop, Aspiration, Dispense]],
  use_channels: List[int],
  num_channels: int,
) -> Tuple[List[int], List[int], List[bool]]:
  """Convert operations and channel assignments into firmware x/y positions and tip pattern.

  Translates PLR operation objects (with absolute resource coordinates) into the
  parallel arrays of x positions, y positions, and a boolean tip pattern that the
  Vantage firmware expects. Unused channel slots are zero-padded. A minimum 9mm
  Y-spacing check is enforced between channels sharing the same X position.

  Uses absolute coordinates (``get_absolute_location``) so the driver does not need
  a ``deck`` reference.

  Args:
    ops: Sequence of operations (Pickup, TipDrop, Aspiration, or Dispense).
    use_channels: Sorted list of 0-indexed channel indices assigned to each operation.
    num_channels: Total number of PIP channels on the instrument.

  Returns:
    Tuple of (x_positions, y_positions, channels_involved) where positions are in
    firmware units (0.1mm) and channels_involved is a boolean tip pattern.

  Raises:
    ValueError: If channels are not sorted, if too many channels are specified,
      or if two channels on the same X are closer than 9mm in Y.
  """
  if use_channels != sorted(use_channels):
    raise ValueError("Channels must be sorted.")

  x_positions: List[int] = []
  y_positions: List[int] = []
  channels_involved: List[bool] = []

  for i, channel in enumerate(use_channels):
    while channel > len(channels_involved):
      channels_involved.append(False)
      x_positions.append(0)
      y_positions.append(0)
    channels_involved.append(True)

    loc = ops[i].resource.get_absolute_location(x="c", y="c", z="b")
    x_positions.append(round((loc.x + ops[i].offset.x) * 10))
    y_positions.append(round((loc.y + ops[i].offset.y) * 10))

  # Minimum distance check (9mm).
  for idx1, (x1, y1) in enumerate(zip(x_positions, y_positions)):
    for idx2, (x2, y2) in enumerate(zip(x_positions, y_positions)):
      if idx1 == idx2:
        continue
      if not channels_involved[idx1] or not channels_involved[idx2]:
        continue
      if x1 != x2:
        continue
      if y1 != y2 and abs(y1 - y2) < 90:
        raise ValueError(
          f"Minimum distance between two y positions is <9mm: {y1}, {y2}"
          f" (channel {idx1} and {idx2})"
        )

  if len(ops) > num_channels:
    raise ValueError(f"Too many channels specified: {len(ops)} > {num_channels}")

  # Trailing padding.
  if len(x_positions) < num_channels:
    x_positions = x_positions + [0]
    y_positions = y_positions + [0]
    channels_involved = channels_involved + [False]

  return x_positions, y_positions, channels_involved


def _resolve_liquid_classes(
  explicit: Optional[List[Optional[HamiltonLiquidClass]]],
  ops: list,
  jet: Union[bool, List[bool]],
  blow_out: Union[bool, List[bool]],
) -> List[Optional[HamiltonLiquidClass]]:
  """Resolve per-operation Hamilton liquid classes for the Vantage.

  If ``explicit`` is provided, returns it as-is (None entries are preserved, matching
  legacy behavior). Otherwise, auto-detects a liquid class for each operation from the
  tip's properties (volume, filter, size) using ``get_vantage_liquid_class``.

  Args:
    explicit: User-provided list of liquid class overrides. Pass None to auto-detect.
    ops: List of aspiration or dispense operations (must have ``.tip`` attributes).
    jet: Per-channel or uniform flag selecting jet vs surface mode for liquid class lookup.
    blow_out: Per-channel or uniform flag selecting blow-out mode for liquid class lookup.

  Returns:
    List of resolved liquid classes (one per operation). Entries may be None if the
    tip is not a HamiltonTip or if no matching liquid class is found.
  """
  n = len(ops)
  if isinstance(jet, bool):
    jet = [jet] * n
  if isinstance(blow_out, bool):
    blow_out = [blow_out] * n

  if explicit is not None:
    return list(explicit)

  result: List[Optional[HamiltonLiquidClass]] = []
  for i, op in enumerate(ops):
    tip = op.tip
    if not isinstance(tip, HamiltonTip):
      result.append(None)
      continue
    result.append(
      get_vantage_liquid_class(
        tip_volume=tip.maximal_volume,
        is_core=False,
        is_tip=True,
        has_filter=tip.has_filter,
        liquid=Liquid.WATER,
        jet=jet[i],
        blow_out=blow_out[i],
      )
    )
  return result


# ---------------------------------------------------------------------------
# VantagePIPBackend
# ---------------------------------------------------------------------------


class VantagePIPBackend(PIPBackend):
  """Translates PIP (pipetting) operations into Vantage firmware commands via the driver.

  This backend implements the ``PIPBackend`` interface for the Hamilton Vantage. It converts
  high-level ``pick_up_tips``, ``drop_tips``, ``aspirate``, and ``dispense`` calls into
  low-level firmware commands on the A1PM module, handling coordinate conversion, liquid
  class resolution, volume correction, and Z-height computation.

  Each public method accepts an optional ``BackendParams`` dataclass that exposes
  Vantage-specific parameters (traverse heights, LLD settings, liquid class overrides,
  etc.). When these parameters are None, sensible defaults are computed from resource
  geometry, liquid classes, and the driver's ``traversal_height``.
  """

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver

  async def _on_setup(self):
    """Check PIP initialization status and initialize channels if needed."""
    tip_presences = await self.driver.query_tip_presence()
    pip_initialized = await self.driver.pip_request_initialization_status()
    if not pip_initialized or any(tip_presences):
      # FIXME: hardcoded for 8 channels. Will break on 4/12/16-channel Vantages.
      # Pre-existing limitation from legacy.
      default_y_positions = [389.1, 362.3, 335.5, 308.7, 281.9, 255.1, 228.3, 201.6]
      n = self.driver.num_channels
      th = self.driver.traversal_height
      await self.driver.pip_initialize(
        x_position=[709.5] * n,
        y_position=default_y_positions,
        begin_z_deposit_position=[th] * n,
        end_z_deposit_position=[123.5] * n,
        minimal_height_at_command_end=[th] * n,
        tip_pattern=[True] * n,
        tip_type=[1] * n,
        TODO_DI_2=70,
      )

  async def _on_stop(self):
    pass

  @property
  def num_channels(self) -> int:
    return self.driver.num_channels

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    return True

  # -- BackendParams dataclasses ---------------------------------------------

  @dataclass
  class PickUpTipsParams(BackendParams):
    """Vantage-specific parameters for ``pick_up_tips``.

    All per-channel list parameters accept ``None`` to use sensible defaults (derived
    from the driver's ``traversal_height``). When provided, lists must have one entry
    per channel involved in the operation.

    Args:
      minimal_traverse_height_at_begin_of_command: Minimum Z clearance in mm before
        lateral movement begins, per channel. If None, uses the driver's
        ``traversal_height``. Must be between 0 and 360.0.
      minimal_height_at_command_end: Minimum Z height in mm at the end of the command,
        per channel. If None, uses the driver's ``traversal_height``. Must be between
        0 and 360.0.
    """

    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None

  @dataclass
  class DropTipsParams(BackendParams):
    """Vantage-specific parameters for ``drop_tips``.

    All per-channel list parameters accept ``None`` to use sensible defaults (derived
    from the driver's ``traversal_height``). When provided, lists must have one entry
    per channel involved in the operation.

    Args:
      minimal_traverse_height_at_begin_of_command: Minimum Z clearance in mm before
        lateral movement begins, per channel. If None, uses the driver's
        ``traversal_height``. Must be between 0 and 360.0.
      minimal_height_at_command_end: Minimum Z height in mm at the end of the command,
        per channel. If None, uses the driver's ``traversal_height``. Must be between
        0 and 360.0.
    """

    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None

  @dataclass
  class AspirateParams(BackendParams):
    """Vantage-specific parameters for ``aspirate``.

    All per-channel list parameters accept ``None`` to use sensible defaults (typically
    derived from liquid classes or container geometry). When provided, lists must have
    one entry per channel involved in the operation.

    Args:
      jet: Per-channel flag used for liquid class selection. If True, selects a jet-mode
        liquid class. If None, defaults to [False] for all channels.
      blow_out: Per-channel flag used for liquid class selection. If True, selects a
        blow-out liquid class. If None, defaults to [False] for all channels.
      hlcs: Per-channel Hamilton liquid class overrides. If None, auto-detected from
        tip type and liquid. None entries in the list are preserved.
      type_of_aspiration: Type of aspiration per channel (0 = simple, 1 = sequence,
        2 = cup emptied). If None, defaults to [0] for all channels.
      minimal_traverse_height_at_begin_of_command: Minimum Z clearance in mm before
        lateral movement begins, per channel. If None, uses the driver's
        ``traversal_height``. Must be between 0 and 360.0.
      minimal_height_at_command_end: Minimum Z height in mm at the end of the command,
        per channel. If None, uses the driver's ``traversal_height``. Must be between
        0 and 360.0.
      lld_search_height: LLD search height in mm (absolute Z). If None, auto-computed
        from well geometry (well bottom + well height + 1.7mm for wells, +5mm for other
        resources).
      clot_detection_height: Clot detection height in mm above the liquid surface per
        channel. If None, defaults to [0] for all channels.
      liquid_surface_at_function_without_lld: Absolute liquid surface position in mm
        when not using LLD, per channel. If None, computed from well bottom + liquid
        height.
      pull_out_distance_to_take_transport_air_in_function_without_lld: Distance in mm
        to pull out for transport air when not using LLD, per channel. If None,
        defaults to 10.9mm.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from
        minimum height in mm, per channel. Used for conical tubes. If None, defaults
        to [0].
      tube_2nd_section_ratio: Tube 2nd section ratio: (bottom diameter * 10000) / top
        diameter, per channel. If None, defaults to [0].
      minimum_height: Minimum height (maximum immersion depth) in mm, per channel.
        If None, uses the well bottom position.
      immersion_depth: Immersion depth in mm, per channel. Positive = deeper into
        liquid. If None, defaults to [0].
      surface_following_distance: Surface following distance during aspiration in mm,
        per channel. If None, defaults to [0].
      transport_air_volume: Transport air volume in uL, per channel. If None, uses
        the liquid class default.
      pre_wetting_volume: Pre-wetting volume in uL, per channel. If None, defaults
        to [0].
      lld_mode: LLD mode per channel as integer (0 = OFF, 1 = GAMMA, 2 = PRESSURE,
        3 = DUAL, 4 = Z_TOUCH_OFF). If None, defaults to [0] (OFF).
      lld_sensitivity: Capacitive LLD sensitivity per channel (1 = high, 4 = low).
        If None, defaults to [4].
      pressure_lld_sensitivity: Pressure LLD sensitivity per channel (1 = high,
        4 = low). If None, defaults to [4].
      aspirate_position_above_z_touch_off: Aspirate position above Z touch off in mm,
        per channel. If None, defaults to [0.5].
      swap_speed: Swap speed (on leaving the liquid surface) in mm/s, per channel.
        If None, defaults to [2].
      settling_time: Settling time in seconds after aspiration completes, per channel.
        If None, defaults to [1.0].
      capacitive_mad_supervision_on_off: Capacitive MAD (Monitored Air Displacement)
        supervision per channel (0 = off, 1 = on). If None, defaults to [0].
      pressure_mad_supervision_on_off: Pressure MAD supervision per channel
        (0 = off, 1 = on). If None, defaults to [0].
      tadm_algorithm_on_off: TADM (Total Air Displacement Monitoring) algorithm
        (0 = off, 1 = on). Applies to all channels. Default 0.
      limit_curve_index: TADM limit curve index per channel. If None, defaults to [0].
        Must be between 0 and 999.
      recording_mode: Recording mode for TADM (0 = no recording, 1 = TADM errors only,
        2 = all TADM measurements). Applies to all channels. Default 0.
      disable_volume_correction: Per-channel flag to disable liquid-class volume
        correction. If None, defaults to [False] for all channels.
    """

    jet: Optional[List[bool]] = None
    blow_out: Optional[List[bool]] = None
    hlcs: Optional[List[Optional[HamiltonLiquidClass]]] = None
    type_of_aspiration: Optional[List[int]] = None
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None
    lld_search_height: Optional[List[float]] = None
    clot_detection_height: Optional[List[float]] = None
    liquid_surface_at_function_without_lld: Optional[List[float]] = None
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[float]] = None
    tube_2nd_section_height_measured_from_zm: Optional[List[float]] = None
    tube_2nd_section_ratio: Optional[List[float]] = None
    minimum_height: Optional[List[float]] = None
    immersion_depth: Optional[List[float]] = None
    surface_following_distance: Optional[List[float]] = None
    transport_air_volume: Optional[List[float]] = None
    pre_wetting_volume: Optional[List[float]] = None
    lld_mode: Optional[List[int]] = None
    lld_sensitivity: Optional[List[int]] = None
    pressure_lld_sensitivity: Optional[List[int]] = None
    aspirate_position_above_z_touch_off: Optional[List[float]] = None
    swap_speed: Optional[List[float]] = None
    settling_time: Optional[List[float]] = None
    mix_position_in_z_direction_from_liquid_surface: Optional[List[float]] = None
    surface_following_distance_during_mixing: Optional[List[float]] = None
    TODO_DA_5: Optional[List[int]] = None
    capacitive_mad_supervision_on_off: Optional[List[int]] = None
    pressure_mad_supervision_on_off: Optional[List[int]] = None
    tadm_algorithm_on_off: int = 0
    limit_curve_index: Optional[List[int]] = None
    recording_mode: int = 0
    disable_volume_correction: Optional[List[bool]] = None

  @dataclass
  class DispenseParams(BackendParams):
    """Vantage-specific parameters for ``dispense``.

    All per-channel list parameters accept ``None`` to use sensible defaults (typically
    derived from liquid classes or container geometry). When provided, lists must have
    one entry per channel involved in the operation.

    Args:
      jet: Per-channel flag used for liquid class selection. If True, selects a jet-mode
        liquid class (dispense from above the liquid surface). If None, defaults to
        [False] for all channels.
      blow_out: Per-channel flag used for liquid class selection. If True, selects a
        blow-out liquid class. If None, defaults to [False] for all channels.
      empty: Per-channel flag to empty the tip completely at a fixed position
        (firmware mode 4). If None, defaults to [False] for all channels.
      hlcs: Per-channel Hamilton liquid class overrides. If None, auto-detected from
        tip type and liquid. None entries in the list are preserved.
      type_of_dispensing_mode: Firmware dispensing mode per channel (0 = partial jet,
        1 = blow-out jet, 2 = partial surface, 3 = blow-out surface, 4 = empty at fix
        position). If None, auto-computed from jet/empty/blow_out flags.
      minimal_traverse_height_at_begin_of_command: Minimum Z clearance in mm before
        lateral movement begins, per channel. If None, uses the driver's
        ``traversal_height``. Must be between 0 and 360.0.
      minimal_height_at_command_end: Minimum Z height in mm at the end of the command,
        per channel. If None, uses the driver's ``traversal_height``. Must be between
        0 and 360.0.
      lld_search_height: LLD search height in mm (absolute Z), per channel. If None,
        auto-computed from well geometry (well bottom + well height + 1.7mm for wells,
        +5mm for other resources).
      minimum_height: Minimum height (maximum immersion depth) in mm, per channel.
        If None, uses the well bottom position.
      pull_out_distance_to_take_transport_air_in_function_without_lld: Distance in mm
        to pull out for transport air when not using LLD, per channel. If None,
        defaults to 5.0mm.
      immersion_depth: Immersion depth in mm, per channel. Positive = deeper into
        liquid. If None, defaults to [0].
      surface_following_distance: Surface following distance during dispense in mm,
        per channel. If None, defaults to [2.1].
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from
        minimum height in mm, per channel. Used for conical tubes. If None, defaults
        to [0].
      tube_2nd_section_ratio: Tube 2nd section ratio: (bottom diameter * 10000) / top
        diameter, per channel. If None, defaults to [0].
      cut_off_speed: Cut-off speed in uL/s, per channel. Speed at which dispensing
        transitions to a slower final phase. If None, defaults to [250].
      stop_back_volume: Stop-back volume in uL, per channel. Volume retracted after
        dispensing to prevent dripping. If None, defaults to [0].
      transport_air_volume: Transport air volume in uL, per channel. If None, uses
        the liquid class default.
      lld_mode: LLD mode per channel as integer (0 = OFF, 1 = GAMMA, 2 = PRESSURE,
        3 = DUAL, 4 = Z_TOUCH_OFF). If None, defaults to [0] (OFF).
      side_touch_off_distance: Side touch-off distance in mm. The tip moves laterally
        by this distance after dispensing to break the droplet. Default 0 (disabled).
      dispense_position_above_z_touch_off: Dispense position above Z touch off in mm,
        per channel. If None, defaults to [0.5].
      lld_sensitivity: Capacitive LLD sensitivity per channel (1 = high, 4 = low).
        If None, defaults to [1].
      pressure_lld_sensitivity: Pressure LLD sensitivity per channel (1 = high,
        4 = low). If None, defaults to [1].
      swap_speed: Swap speed (on leaving the liquid surface) in mm/s, per channel.
        If None, defaults to [1].
      settling_time: Settling time in seconds after dispensing completes, per channel.
        If None, defaults to [0].
      tadm_algorithm_on_off: TADM (Total Air Displacement Monitoring) algorithm
        (0 = off, 1 = on). Applies to all channels. Default 0.
      limit_curve_index: TADM limit curve index per channel. If None, defaults to [0].
        Must be between 0 and 999.
      recording_mode: Recording mode for TADM (0 = no recording, 1 = TADM errors only,
        2 = all TADM measurements). Applies to all channels. Default 0.
      disable_volume_correction: Per-channel flag to disable liquid-class volume
        correction. If None, defaults to [False] for all channels.
    """

    jet: Optional[List[bool]] = None
    blow_out: Optional[List[bool]] = None
    empty: Optional[List[bool]] = None
    hlcs: Optional[List[Optional[HamiltonLiquidClass]]] = None
    type_of_dispensing_mode: Optional[List[int]] = None
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None
    lld_search_height: Optional[List[float]] = None
    minimum_height: Optional[List[float]] = None
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[float]] = None
    immersion_depth: Optional[List[float]] = None
    surface_following_distance: Optional[List[float]] = None
    tube_2nd_section_height_measured_from_zm: Optional[List[float]] = None
    tube_2nd_section_ratio: Optional[List[float]] = None
    cut_off_speed: Optional[List[float]] = None
    stop_back_volume: Optional[List[float]] = None
    transport_air_volume: Optional[List[float]] = None
    lld_mode: Optional[List[int]] = None
    side_touch_off_distance: float = 0
    dispense_position_above_z_touch_off: Optional[List[float]] = None
    lld_sensitivity: Optional[List[int]] = None
    pressure_lld_sensitivity: Optional[List[int]] = None
    swap_speed: Optional[List[float]] = None
    settling_time: Optional[List[float]] = None
    mix_position_in_z_direction_from_liquid_surface: Optional[List[float]] = None
    surface_following_distance_during_mixing: Optional[List[float]] = None
    TODO_DD_2: Optional[List[int]] = None
    tadm_algorithm_on_off: int = 0
    limit_curve_index: Optional[List[int]] = None
    recording_mode: int = 0
    disable_volume_correction: Optional[List[bool]] = None

  # -- PIPBackend interface: pick_up_tips ------------------------------------

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Pick up tips with the PIP channels.

    Converts high-level Pickup operations into a firmware TP command on module A1PM.
    Handles tip type registration, Z-height computation from tip geometry, and
    coordinate conversion to firmware units.

    Args:
      ops: List of Pickup operations, one per channel.
      use_channels: Sorted list of 0-indexed channel indices to use.
      backend_params: Optional :class:`VantagePIPBackend.PickUpTipsParams` for
        Vantage-specific overrides.
    """
    if not isinstance(backend_params, VantagePIPBackend.PickUpTipsParams):
      backend_params = VantagePIPBackend.PickUpTipsParams()

    x_positions, y_positions, tip_pattern = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    tips = [cast(HamiltonTip, op.resource.get_tip()) for op in ops]
    ttti = [await self.driver.request_or_assign_tip_type_index(tip) for tip in tips]

    max_z = max(op.resource.get_absolute_location(z="b").z + op.offset.z for op in ops)
    max_total_tip_length = max(op.tip.total_tip_length for op in ops)
    max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)

    # Tip size adjustments (from legacy, confirmed by experiments).
    proto_tip = self.driver._get_hamilton_tip([op.resource for op in ops])
    if proto_tip.tip_size == TipSize.LOW_VOLUME:
      max_tip_length += 2
    elif proto_tip.tip_size != TipSize.STANDARD_VOLUME:
      max_tip_length -= 2

    th = self.driver.traversal_height
    mth = backend_params.minimal_traverse_height_at_begin_of_command
    mhe = backend_params.minimal_height_at_command_end

    begin_z_deposit_position = [max_z + max_total_tip_length] * len(ops)
    end_z_deposit_position = [max_z + max_tip_length] * len(ops)
    minimal_traverse_height_at_begin_of_command = mth if mth is not None else [th] * len(ops)
    minimal_height_at_command_end = mhe if mhe is not None else [th] * len(ops)
    tip_handling_method = [1] * len(ops)
    blow_out_air_volume = [0] * len(ops)

    if not all(0 <= x <= 50000 for x in x_positions):
      raise ValueError("x_position must be in range 0 to 50000")
    if not all(0 <= x <= 6500 for x in y_positions):
      raise ValueError("y_position must be in range 0 to 6500")
    if not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")
    if not all(0 <= x <= 199 for x in ttti):
      raise ValueError("tip_type must be in range 0 to 199")
    if not all(0 <= x <= 360.0 for x in begin_z_deposit_position):
      raise ValueError("begin_z_deposit_position must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in end_z_deposit_position):
      raise ValueError("end_z_deposit_position must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 360.0")
    if not all(0 <= x <= 1250.0 for x in blow_out_air_volume):
      raise ValueError("blow_out_air_volume must be in range 0 to 1250.0")
    if not all(0 <= x <= 9 for x in tip_handling_method):
      raise ValueError("tip_handling_method must be in range 0 to 9")

    try:
      await self.driver.send_command(
        module="A1PM",
        command="TP",
        xp=x_positions,
        yp=y_positions,
        tm=tip_pattern,
        tt=ttti,
        tp=[round(z * 10) for z in begin_z_deposit_position],
        tz=[round(z * 10) for z in end_z_deposit_position],
        th=[round(h * 10) for h in minimal_traverse_height_at_begin_of_command],
        te=[round(h * 10) for h in minimal_height_at_command_end],
        ba=[round(v * 100) for v in blow_out_air_volume],
        td=tip_handling_method,
      )
    except VantageFirmwareError as e:
      plr_error = convert_vantage_firmware_error_to_plr_error(e)
      raise plr_error if plr_error is not None else e

  # -- PIPBackend interface: drop_tips ---------------------------------------

  async def drop_tips(
    self,
    ops: List[TipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Drop tips from the PIP channels.

    Converts high-level TipDrop operations into a firmware TR command on module A1PM.

    Args:
      ops: List of TipDrop operations, one per channel.
      use_channels: Sorted list of 0-indexed channel indices to use.
      backend_params: Optional :class:`VantagePIPBackend.DropTipsParams` for
        Vantage-specific overrides.
    """
    if not isinstance(backend_params, VantagePIPBackend.DropTipsParams):
      backend_params = VantagePIPBackend.DropTipsParams()

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    max_z = max(op.resource.get_absolute_location(z="b").z + op.offset.z for op in ops)
    th = self.driver.traversal_height
    mth = backend_params.minimal_traverse_height_at_begin_of_command
    mhe = backend_params.minimal_height_at_command_end

    begin_z_deposit_position = [max_z + 10] * len(ops)
    end_z_deposit_position = [max_z] * len(ops)
    minimal_traverse_height_at_begin_of_command = mth if mth is not None else [th] * len(ops)
    minimal_height_at_command_end = mhe if mhe is not None else [th] * len(ops)
    tip_handling_method = [0] * len(ops)
    TODO_TR_2 = 0

    if not all(0 <= x <= 50000 for x in x_positions):
      raise ValueError("x_position must be in range 0 to 50000")
    if not all(0 <= x <= 6500 for x in y_positions):
      raise ValueError("y_position must be in range 0 to 6500")
    if not all(0 <= x <= 360.0 for x in begin_z_deposit_position):
      raise ValueError("begin_z_deposit_position must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in end_z_deposit_position):
      raise ValueError("end_z_deposit_position must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 360.0")
    if not all(0 <= x <= 1 for x in channels_involved):
      raise ValueError("tip_pattern must be in range 0 to 1")
    if not -1000 <= TODO_TR_2 <= 1000:
      raise ValueError("TODO_TR_2 must be in range -1000 to 1000")
    if not all(0 <= x <= 9 for x in tip_handling_method):
      raise ValueError("tip_handling_method must be in range 0 to 9")

    try:
      await self.driver.send_command(
        module="A1PM",
        command="TR",
        xp=x_positions,
        yp=y_positions,
        tp=[round(z * 10) for z in begin_z_deposit_position],
        tz=[round(z * 10) for z in end_z_deposit_position],
        th=[round(h * 10) for h in minimal_traverse_height_at_begin_of_command],
        te=[round(h * 10) for h in minimal_height_at_command_end],
        tm=channels_involved,
        ts=TODO_TR_2,
        td=tip_handling_method,
      )
    except VantageFirmwareError as e:
      plr_error = convert_vantage_firmware_error_to_plr_error(e)
      raise plr_error if plr_error is not None else e

  # -- safety checks ----------------------------------------------------------

  @staticmethod
  def _assert_valid_resources(resources: List[Resource]) -> None:
    """Assert that resources are not too low for safe pipetting."""
    for resource in resources:
      if resource.get_absolute_location(z="b").z < 100:
        raise ValueError(
          f"Resource {resource} is too low: {resource.get_absolute_location(z='b').z} < 100"
        )

  # -- PIPBackend interface: aspirate ----------------------------------------

  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate liquid with the PIP channels.

    Converts high-level Aspiration operations into a firmware DA command on module A1PM.
    Handles liquid class resolution, volume correction, Z-height computation, LLD
    configuration, and mix parameters.

    Args:
      ops: List of Aspiration operations, one per channel.
      use_channels: Sorted list of 0-indexed channel indices to use.
      backend_params: Optional :class:`VantagePIPBackend.AspirateParams` for
        Vantage-specific overrides.
    """
    if not isinstance(backend_params, VantagePIPBackend.AspirateParams):
      backend_params = VantagePIPBackend.AspirateParams()

    self._assert_valid_resources([op.resource for op in ops])

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    jet = backend_params.jet or [False] * len(ops)
    blow_out = backend_params.blow_out or [False] * len(ops)
    hlcs = _resolve_liquid_classes(backend_params.hlcs, ops, jet, blow_out)

    # Volume correction.
    disable_vc = backend_params.disable_volume_correction or [False] * len(ops)
    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_vc)
    ]

    well_bottoms = [
      op.resource.get_absolute_location(z="b").z + op.offset.z + op.resource.material_z_thickness
      for op in ops
    ]
    liquid_surfaces_no_lld = backend_params.liquid_surface_at_function_without_lld or [
      wb + (op.liquid_height if op.liquid_height is not None else 0)
      for wb, op in zip(well_bottoms, ops)
    ]
    lld_search_heights = backend_params.lld_search_height or [
      wb + op.resource.get_absolute_size_z() + (1.7 if isinstance(op.resource, Well) else 5)
      for wb, op in zip(well_bottoms, ops)
    ]

    flow_rates = [
      op.flow_rate
      if op.flow_rate is not None
      else (hlc.aspiration_flow_rate if hlc is not None else 100)
      for op, hlc in zip(ops, hlcs)
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume
      if op.blow_out_air_volume is not None
      else (hlc.dispense_blow_out_volume if hlc is not None else 0)
      for op, hlc in zip(ops, hlcs)
    ]

    th = self.driver.traversal_height
    mth = backend_params.minimal_traverse_height_at_begin_of_command
    mhe = backend_params.minimal_height_at_command_end

    # Flatten all aspirate parameters into local names for guards + send_command.
    type_of_aspiration = backend_params.type_of_aspiration or [0] * len(ops)
    minimal_traverse_height_at_begin_of_command = mth if mth is not None else [th] * len(ops)
    minimal_height_at_command_end = mhe if mhe is not None else [th] * len(ops)
    clot_detection_height = list(backend_params.clot_detection_height or [0] * len(ops))
    pull_out_distance_to_take_transport_air_in_function_without_lld = list(
      backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
      or [10.9] * len(ops)
    )
    tube_2nd_section_height_measured_from_zm = list(
      backend_params.tube_2nd_section_height_measured_from_zm or [0] * len(ops)
    )
    tube_2nd_section_ratio = list(backend_params.tube_2nd_section_ratio or [0] * len(ops))
    minimum_height = list(backend_params.minimum_height or well_bottoms)
    immersion_depth = list(backend_params.immersion_depth or [0] * len(ops))
    surface_following_distance = list(backend_params.surface_following_distance or [0] * len(ops))
    transport_air_volume = list(
      backend_params.transport_air_volume
      or [hlc.aspiration_air_transport_volume if hlc is not None else 0 for hlc in hlcs]
    )
    pre_wetting_volume = list(backend_params.pre_wetting_volume or [0] * len(ops))
    lld_mode = backend_params.lld_mode or [0] * len(ops)
    lld_sensitivity = backend_params.lld_sensitivity or [4] * len(ops)
    pressure_lld_sensitivity = backend_params.pressure_lld_sensitivity or [4] * len(ops)
    aspirate_position_above_z_touch_off = list(
      backend_params.aspirate_position_above_z_touch_off or [0.5] * len(ops)
    )
    swap_speed = list(backend_params.swap_speed or [2] * len(ops))
    settling_time = list(backend_params.settling_time or [1] * len(ops))
    mix_volume = [op.mix.volume if op.mix is not None else 0 for op in ops]
    mix_cycles = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_position_in_z_direction_from_liquid_surface = (
      list(backend_params.mix_position_in_z_direction_from_liquid_surface)
      if backend_params.mix_position_in_z_direction_from_liquid_surface is not None
      else [0] * len(ops)
    )
    mix_speed = [op.mix.flow_rate if op.mix is not None else 250 for op in ops]
    surface_following_distance_during_mixing = (
      list(backend_params.surface_following_distance_during_mixing)
      if backend_params.surface_following_distance_during_mixing is not None
      else [0] * len(ops)
    )
    TODO_DA_5 = backend_params.TODO_DA_5
    capacitive_mad_supervision_on_off = (
      backend_params.capacitive_mad_supervision_on_off or [0] * len(ops)
    )
    pressure_mad_supervision_on_off = (
      backend_params.pressure_mad_supervision_on_off or [0] * len(ops)
    )
    tadm_algorithm_on_off = backend_params.tadm_algorithm_on_off
    limit_curve_index = backend_params.limit_curve_index or [0] * len(ops)
    recording_mode = backend_params.recording_mode

    if not all(0 <= x <= 2 for x in type_of_aspiration):
      raise ValueError("type_of_aspiration must be in range 0 to 2")
    if not all(0 <= x <= 1 for x in channels_involved):
      raise ValueError("tip_pattern must be in range 0 to 1")
    if not all(0 <= x <= 50000 for x in x_positions):
      raise ValueError("x_position must be in range 0 to 50000")
    if not all(0 <= x <= 6500 for x in y_positions):
      raise ValueError("y_position must be in range 0 to 6500")
    if not all(0 <= x <= 360.0 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in lld_search_heights):
      raise ValueError("lld_search_height must be in range 0 to 360.0")
    if not all(0 <= x <= 50.0 for x in clot_detection_height):
      raise ValueError("clot_detection_height must be in range 0 to 50.0")
    if not all(0 <= x <= 360.0 for x in liquid_surfaces_no_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 360.0")
    if not all(
      0 <= x <= 360.0 for x in pull_out_distance_to_take_transport_air_in_function_without_lld
    ):
      raise ValueError(
        "pull_out_distance_to_take_transport_air_in_function_without_lld must be in range 0 to 360.0"
      )
    if not all(0 <= x <= 360.0 for x in tube_2nd_section_height_measured_from_zm):
      raise ValueError("tube_2nd_section_height_measured_from_zm must be in range 0 to 360.0")
    if not all(0 <= x <= 10000 for x in tube_2nd_section_ratio):
      raise ValueError("tube_2nd_section_ratio must be in range 0 to 10000")
    if not all(0 <= x <= 360.0 for x in minimum_height):
      raise ValueError("minimum_height must be in range 0 to 360.0")
    if not all(-360.0 <= x <= 360.0 for x in immersion_depth):
      raise ValueError("immersion_depth must be in range -360.0 to 360.0")
    if not all(0 <= x <= 360.0 for x in surface_following_distance):
      raise ValueError("surface_following_distance must be in range 0 to 360.0")
    if not all(0 <= x <= 1250.0 for x in volumes):
      raise ValueError("aspiration_volume must be in range 0 to 1250.0")
    if not all(1.0 <= x <= 1000.0 for x in flow_rates):
      raise ValueError("aspiration_speed must be in range 1.0 to 1000.0")
    if not all(0 <= x <= 50.0 for x in transport_air_volume):
      raise ValueError("transport_air_volume must be in range 0 to 50.0")
    if not all(0 <= x <= 1250.0 for x in blow_out_air_volumes):
      raise ValueError("blow_out_air_volume must be in range 0 to 1250.0")
    if not all(0 <= x <= 99.9 for x in pre_wetting_volume):
      raise ValueError("pre_wetting_volume must be in range 0 to 99.9")
    if not all(0 <= x <= 4 for x in lld_mode):
      raise ValueError("lld_mode must be in range 0 to 4")
    if not all(1 <= x <= 4 for x in lld_sensitivity):
      raise ValueError("lld_sensitivity must be in range 1 to 4")
    if not all(1 <= x <= 4 for x in pressure_lld_sensitivity):
      raise ValueError("pressure_lld_sensitivity must be in range 1 to 4")
    if not all(0 <= x <= 10.0 for x in aspirate_position_above_z_touch_off):
      raise ValueError("aspirate_position_above_z_touch_off must be in range 0 to 10.0")
    if not all(0.3 <= x <= 160.0 for x in swap_speed):
      raise ValueError("swap_speed must be in range 0.3 to 160.0")
    if not all(0 <= x <= 9.9 for x in settling_time):
      raise ValueError("settling_time must be in range 0 to 9.9")
    if not all(0 <= x <= 1250.0 for x in mix_volume):
      raise ValueError("mix_volume must be in range 0 to 1250.0")
    if not all(0 <= x <= 99 for x in mix_cycles):
      raise ValueError("mix_cycles must be in range 0 to 99")
    if not all(0 <= x <= 90.0 for x in mix_position_in_z_direction_from_liquid_surface):
      raise ValueError("mix_position_in_z_direction_from_liquid_surface must be in range 0 to 90.0")
    if not all(1.0 <= x <= 1000.0 for x in mix_speed):
      raise ValueError("mix_speed must be in range 1.0 to 1000.0")
    if not all(0 <= x <= 360.0 for x in surface_following_distance_during_mixing):
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 360.0")
    if TODO_DA_5 is not None and not all(0 <= x <= 1 for x in TODO_DA_5):
      raise ValueError("TODO_DA_5 must be in range 0 to 1")
    if not all(0 <= x <= 1 for x in capacitive_mad_supervision_on_off):
      raise ValueError("capacitive_mad_supervision_on_off must be in range 0 to 1")
    if not all(0 <= x <= 1 for x in pressure_mad_supervision_on_off):
      raise ValueError("pressure_mad_supervision_on_off must be in range 0 to 1")
    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")
    if not all(0 <= x <= 999 for x in limit_curve_index):
      raise ValueError("limit_curve_index must be in range 0 to 999")
    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    try:
      await self.driver.send_command(
        module="A1PM",
        command="DA",
        at=type_of_aspiration,
        tm=channels_involved,
        xp=x_positions,
        yp=y_positions,
        th=[round(v * 10) for v in minimal_traverse_height_at_begin_of_command],
        te=[round(v * 10) for v in minimal_height_at_command_end],
        lp=[round(v * 10) for v in lld_search_heights],
        ch=[round(v * 10) for v in clot_detection_height],
        zl=[round(v * 10) for v in liquid_surfaces_no_lld],
        po=[round(v * 10) for v in pull_out_distance_to_take_transport_air_in_function_without_lld],
        zu=[round(v * 10) for v in tube_2nd_section_height_measured_from_zm],
        zr=[round(v) for v in tube_2nd_section_ratio],
        zx=[round(v * 10) for v in minimum_height],
        ip=[round(v * 10) for v in immersion_depth],
        fp=[round(v * 10) for v in surface_following_distance],
        av=[round(v * 100) for v in volumes],
        as_=[round(v * 10) for v in flow_rates],
        ta=[round(v * 10) for v in transport_air_volume],
        ba=[round(v * 100) for v in blow_out_air_volumes],
        oa=[round(v * 10) for v in pre_wetting_volume],
        lm=lld_mode,
        ll=lld_sensitivity,
        lv=pressure_lld_sensitivity,
        zo=[round(v * 10) for v in aspirate_position_above_z_touch_off],
        de=[round(v * 10) for v in swap_speed],
        wt=[round(v * 10) for v in settling_time],
        mv=[round(v * 10) for v in mix_volume],
        mc=mix_cycles,
        mp=[round(v * 10) for v in mix_position_in_z_direction_from_liquid_surface],
        ms=[round(v * 10) for v in mix_speed],
        mh=[round(v * 10) for v in surface_following_distance_during_mixing],
        la=TODO_DA_5 if TODO_DA_5 is not None else [0] * len(type_of_aspiration),
        lb=capacitive_mad_supervision_on_off,
        lc=pressure_mad_supervision_on_off,
        gj=tadm_algorithm_on_off,
        gi=limit_curve_index,
        gk=recording_mode,
      )
    except VantageFirmwareError as e:
      plr_error = convert_vantage_firmware_error_to_plr_error(e)
      raise plr_error if plr_error is not None else e

  # -- PIPBackend interface: dispense ----------------------------------------

  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense liquid with the PIP channels.

    Converts high-level Dispense operations into a firmware DD command on module A1PM.
    Handles liquid class resolution, volume correction, dispensing mode selection,
    Z-height computation, and mix parameters.

    Args:
      ops: List of Dispense operations, one per channel.
      use_channels: Sorted list of 0-indexed channel indices to use.
      backend_params: Optional :class:`VantagePIPBackend.DispenseParams` for
        Vantage-specific overrides.
    """
    if not isinstance(backend_params, VantagePIPBackend.DispenseParams):
      backend_params = VantagePIPBackend.DispenseParams()

    self._assert_valid_resources([op.resource for op in ops])

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    jet = backend_params.jet or [False] * len(ops)
    empty = backend_params.empty or [False] * len(ops)
    blow_out = backend_params.blow_out or [False] * len(ops)
    hlcs = _resolve_liquid_classes(backend_params.hlcs, ops, jet, blow_out)

    # Volume correction.
    disable_vc = backend_params.disable_volume_correction or [False] * len(ops)
    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_vc)
    ]

    well_bottoms = [
      op.resource.get_absolute_location(z="b").z + op.offset.z + op.resource.material_z_thickness
      for op in ops
    ]
    liquid_surfaces_no_lld = [
      wb + (op.liquid_height if op.liquid_height is not None else 0)
      for wb, op in zip(well_bottoms, ops)
    ]
    lld_search_heights = backend_params.lld_search_height or [
      wb + op.resource.get_absolute_size_z() + (1.7 if isinstance(op.resource, Well) else 5)
      for wb, op in zip(well_bottoms, ops)
    ]

    flow_rates = [
      op.flow_rate
      if op.flow_rate is not None
      else (hlc.dispense_flow_rate if hlc is not None else 100)
      for op, hlc in zip(ops, hlcs)
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume
      if op.blow_out_air_volume is not None
      else (hlc.dispense_blow_out_volume if hlc is not None else 0)
      for op, hlc in zip(ops, hlcs)
    ]

    type_of_dispensing_mode = backend_params.type_of_dispensing_mode or [
      _get_dispense_mode(jet=jet[i], empty=empty[i], blow_out=blow_out[i]) for i in range(len(ops))
    ]

    th = self.driver.traversal_height
    mth = backend_params.minimal_traverse_height_at_begin_of_command
    mhe = backend_params.minimal_height_at_command_end

    # Flatten all dispense parameters into local names for guards + send_command.
    minimum_height = list(backend_params.minimum_height or well_bottoms)
    pull_out_distance_to_take_transport_air_in_function_without_lld = list(
      backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
      or [5.0] * len(ops)
    )
    immersion_depth = list(backend_params.immersion_depth or [0] * len(ops))
    surface_following_distance = list(backend_params.surface_following_distance or [2.1] * len(ops))
    tube_2nd_section_height_measured_from_zm = list(
      backend_params.tube_2nd_section_height_measured_from_zm or [0] * len(ops)
    )
    tube_2nd_section_ratio = list(backend_params.tube_2nd_section_ratio or [0] * len(ops))
    minimal_traverse_height_at_begin_of_command = mth if mth is not None else [th] * len(ops)
    minimal_height_at_command_end = mhe if mhe is not None else [th] * len(ops)
    cut_off_speed = list(backend_params.cut_off_speed or [250] * len(ops))
    stop_back_volume = list(backend_params.stop_back_volume or [0] * len(ops))
    transport_air_volume = list(
      backend_params.transport_air_volume
      or [hlc.dispense_air_transport_volume if hlc is not None else 0 for hlc in hlcs]
    )
    lld_mode = backend_params.lld_mode or [0] * len(ops)
    side_touch_off_distance = backend_params.side_touch_off_distance
    dispense_position_above_z_touch_off = list(
      backend_params.dispense_position_above_z_touch_off or [0.5] * len(ops)
    )
    lld_sensitivity = backend_params.lld_sensitivity or [1] * len(ops)
    pressure_lld_sensitivity = backend_params.pressure_lld_sensitivity or [1] * len(ops)
    swap_speed = list(backend_params.swap_speed or [1] * len(ops))
    settling_time = list(backend_params.settling_time or [0] * len(ops))
    mix_volume = [op.mix.volume if op.mix is not None else 0 for op in ops]
    mix_cycles = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_position_in_z_direction_from_liquid_surface = (
      list(backend_params.mix_position_in_z_direction_from_liquid_surface)
      if backend_params.mix_position_in_z_direction_from_liquid_surface is not None
      else [0] * len(ops)
    )
    mix_speed = [op.mix.flow_rate if op.mix is not None else 1 for op in ops]
    surface_following_distance_during_mixing = (
      list(backend_params.surface_following_distance_during_mixing)
      if backend_params.surface_following_distance_during_mixing is not None
      else [0] * len(ops)
    )
    TODO_DD_2 = backend_params.TODO_DD_2
    tadm_algorithm_on_off = backend_params.tadm_algorithm_on_off
    limit_curve_index = backend_params.limit_curve_index or [0] * len(ops)
    recording_mode = backend_params.recording_mode

    if not all(0 <= x <= 4 for x in type_of_dispensing_mode):
      raise ValueError("type_of_dispensing_mode must be in range 0 to 4")
    if not all(0 <= x <= 1 for x in channels_involved):
      raise ValueError("tip_pattern must be in range 0 to 1")
    if not all(0 <= x <= 50000 for x in x_positions):
      raise ValueError("x_position must be in range 0 to 50000")
    if not all(0 <= x <= 6500 for x in y_positions):
      raise ValueError("y_position must be in range 0 to 6500")
    if not all(0 <= x <= 360.0 for x in minimum_height):
      raise ValueError("minimum_height must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in lld_search_heights):
      raise ValueError("lld_search_height must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in liquid_surfaces_no_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 360.0")
    if not all(
      0 <= x <= 360.0 for x in pull_out_distance_to_take_transport_air_in_function_without_lld
    ):
      raise ValueError(
        "pull_out_distance_to_take_transport_air_in_function_without_lld must be in range 0 to 360.0"
      )
    if not all(-360.0 <= x <= 360.0 for x in immersion_depth):
      raise ValueError("immersion_depth must be in range -360.0 to 360.0")
    if not all(0 <= x <= 360.0 for x in surface_following_distance):
      raise ValueError("surface_following_distance must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in tube_2nd_section_height_measured_from_zm):
      raise ValueError("tube_2nd_section_height_measured_from_zm must be in range 0 to 360.0")
    if not all(0 <= x <= 10000 for x in tube_2nd_section_ratio):
      raise ValueError("tube_2nd_section_ratio must be in range 0 to 10000")
    if not all(0 <= x <= 360.0 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 360.0")
    if not all(0 <= x <= 1250.0 for x in volumes):
      raise ValueError("dispense_volume must be in range 0 to 1250.0")
    if not all(1.0 <= x <= 1000.0 for x in flow_rates):
      raise ValueError("dispense_speed must be in range 1.0 to 1000.0")
    if not all(1.0 <= x <= 1000.0 for x in cut_off_speed):
      raise ValueError("cut_off_speed must be in range 1.0 to 1000.0")
    if not all(0 <= x <= 18.0 for x in stop_back_volume):
      raise ValueError("stop_back_volume must be in range 0 to 18.0")
    if not all(0 <= x <= 50.0 for x in transport_air_volume):
      raise ValueError("transport_air_volume must be in range 0 to 50.0")
    if not all(0 <= x <= 1250.0 for x in blow_out_air_volumes):
      raise ValueError("blow_out_air_volume must be in range 0 to 1250.0")
    if not all(0 <= x <= 4 for x in lld_mode):
      raise ValueError("lld_mode must be in range 0 to 4")
    if not 0 <= side_touch_off_distance <= 4.5:
      raise ValueError("side_touch_off_distance must be in range 0 to 4.5")
    if not all(0 <= x <= 10.0 for x in dispense_position_above_z_touch_off):
      raise ValueError("dispense_position_above_z_touch_off must be in range 0 to 10.0")
    if not all(1 <= x <= 4 for x in lld_sensitivity):
      raise ValueError("lld_sensitivity must be in range 1 to 4")
    if not all(1 <= x <= 4 for x in pressure_lld_sensitivity):
      raise ValueError("pressure_lld_sensitivity must be in range 1 to 4")
    if not all(0.3 <= x <= 160.0 for x in swap_speed):
      raise ValueError("swap_speed must be in range 0.3 to 160.0")
    if not all(0 <= x <= 9.9 for x in settling_time):
      raise ValueError("settling_time must be in range 0 to 9.9")
    if not all(0 <= x <= 1250.0 for x in mix_volume):
      raise ValueError("mix_volume must be in range 0 to 1250.0")
    if not all(0 <= x <= 99 for x in mix_cycles):
      raise ValueError("mix_cycles must be in range 0 to 99")
    if not all(0 <= x <= 90.0 for x in mix_position_in_z_direction_from_liquid_surface):
      raise ValueError("mix_position_in_z_direction_from_liquid_surface must be in range 0 to 90.0")
    if not all(1.0 <= x <= 1000.0 for x in mix_speed):
      raise ValueError("mix_speed must be in range 1.0 to 1000.0")
    if not all(0 <= x <= 360.0 for x in surface_following_distance_during_mixing):
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 360.0")
    if TODO_DD_2 is not None and not all(0 <= x <= 1 for x in TODO_DD_2):
      raise ValueError("TODO_DD_2 must be in range 0 to 1")
    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")
    if not all(0 <= x <= 999 for x in limit_curve_index):
      raise ValueError("limit_curve_index must be in range 0 to 999")
    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    try:
      await self.driver.send_command(
        module="A1PM",
        command="DD",
        dm=type_of_dispensing_mode,
        tm=channels_involved,
        xp=x_positions,
        yp=y_positions,
        zx=[round(v * 10) for v in minimum_height],
        lp=[round(v * 10) for v in lld_search_heights],
        zl=[round(v * 10) for v in liquid_surfaces_no_lld],
        po=[round(v * 10) for v in pull_out_distance_to_take_transport_air_in_function_without_lld],
        ip=[round(v * 10) for v in immersion_depth],
        fp=[round(v * 10) for v in surface_following_distance],
        zu=[round(v * 10) for v in tube_2nd_section_height_measured_from_zm],
        zr=[round(v) for v in tube_2nd_section_ratio],
        th=[round(v * 10) for v in minimal_traverse_height_at_begin_of_command],
        te=[round(v * 10) for v in minimal_height_at_command_end],
        dv=[f"{round(v * 100):04}" for v in volumes],
        ds=[round(v * 10) for v in flow_rates],
        ss=[round(v * 10) for v in cut_off_speed],
        rv=[round(v * 10) for v in stop_back_volume],
        ta=[round(v * 10) for v in transport_air_volume],
        ba=[round(v * 100) for v in blow_out_air_volumes],
        lm=lld_mode,
        dj=round(side_touch_off_distance * 10),
        zo=[round(v * 10) for v in dispense_position_above_z_touch_off],
        ll=lld_sensitivity,
        lv=pressure_lld_sensitivity,
        de=[round(v * 10) for v in swap_speed],
        wt=[round(v * 10) for v in settling_time],
        mv=[round(v * 10) for v in mix_volume],
        mc=mix_cycles,
        mp=[round(v * 10) for v in mix_position_in_z_direction_from_liquid_surface],
        ms=[round(v * 10) for v in mix_speed],
        mh=[round(v * 10) for v in surface_following_distance_during_mixing],
        la=TODO_DD_2 if TODO_DD_2 is not None else [0] * len(type_of_dispensing_mode),
        gj=tadm_algorithm_on_off,
        gi=limit_curve_index,
        gk=recording_mode,
      )
    except VantageFirmwareError as e:
      plr_error = convert_vantage_firmware_error_to_plr_error(e)
      raise plr_error if plr_error is not None else e

  # -- tip presence ----------------------------------------------------------

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Query whether each PIP channel currently has a tip attached.

    Returns:
      List of booleans, one per channel. True if a tip is present, False otherwise.
    """
    presences = await self.driver.query_tip_presence()
    return [bool(p) for p in presences]

  # -- firmware commands (A1PM) ----------------------------------------------

  # -- positioning / query commands (A1PM) -----------------------------------

  async def search_for_teach_in_signal_in_x_direction(
    self,
    channel_index: int = 1,
    x_search_distance: float = 0,
    x_speed: float = 27.0,
  ):
    """Search for teach-in signal in X direction (A1PM:DL).

    Args:
      channel_index: Channel index (1-based, 1..16).
      x_search_distance: X search distance in mm.
      x_speed: X speed in mm/s.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="DL",
      pn=channel_index,
      xs=round(x_search_distance * 10),
      xv=round(x_speed * 10),
    )

  async def position_all_channels_in_y_direction(
    self,
    y_position: List[float],
  ):
    """Position all channels in Y direction (A1PM:DY).

    Args:
      y_position: Y positions in mm, one per channel.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="DY",
      yp=[round(v * 10) for v in y_position],
    )

  async def position_all_channels_in_z_direction(
    self,
    z_position: List[float],
  ):
    """Position all channels in Z direction (A1PM:DZ).

    Args:
      z_position: Z positions in mm, one per channel.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="DZ",
      zp=[round(v * 10) for v in z_position],
    )

  async def position_single_channel_in_y_direction(
    self,
    channel_index: int = 1,
    y_position: float = 300.0,
  ):
    """Position single channel in Y direction (A1PM:DV).

    Args:
      channel_index: Channel index (1-based, 1..16).
      y_position: Y position in mm.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="DV",
      pn=channel_index,
      yj=round(y_position * 10),
    )

  async def position_single_channel_in_z_direction(
    self,
    channel_index: int = 1,
    z_position: float = 0,
  ):
    """Position single channel in Z direction (A1PM:DU).

    Args:
      channel_index: Channel index (1-based, 1..16).
      z_position: Z position in mm.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="DU",
      pn=channel_index,
      zj=round(z_position * 10),
    )

  async def move_to_defined_position(
    self,
    x_position: List[int],
    y_position: List[float],
    tip_pattern: Optional[List[bool]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    z_position: Optional[List[float]] = None,
  ):
    """Move to defined position (A1PM:DN).

    Args:
      x_position: X positions in 0.1mm (firmware units).
      y_position: Y positions in mm, one per channel.
      tip_pattern: Channels involved (True = involved).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        in mm, one per channel.
      z_position: Z positions in mm, one per channel.
    """
    n = self.driver.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [360.0] * n
    if z_position is None:
      z_position = [0.0] * n

    return await self.driver.send_command(
      module="A1PM",
      command="DN",
      tm=tip_pattern,
      xp=x_position,
      yp=[round(v * 10) for v in y_position],
      th=[round(v * 10) for v in minimal_traverse_height_at_begin_of_command],
      zp=[round(v * 10) for v in z_position],
    )

  async def teach_rack_using_channel_n(
    self,
    channel_index: int = 1,
    gap_center_x_direction: float = 0,
    gap_center_y_direction: float = 300.0,
    gap_center_z_direction: float = 0,
    minimal_height_at_command_end: Optional[List[float]] = None,
  ):
    """Teach rack using channel n (A1PM:DT).

    Attention! Channels not involved must first be taken out of measurement range.

    Args:
      channel_index: Channel index (1-based, 1..16).
      gap_center_x_direction: Gap center X direction in mm.
      gap_center_y_direction: Gap center Y direction in mm.
      gap_center_z_direction: Gap center Z direction in mm.
      minimal_height_at_command_end: Minimal height at command end in mm, one per channel.
    """
    n = self.driver.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [360.0] * n

    return await self.driver.send_command(
      module="A1PM",
      command="DT",
      pn=channel_index,
      xa=round(gap_center_x_direction * 10),
      yj=round(gap_center_y_direction * 10),
      zj=round(gap_center_z_direction * 10),
      te=[round(v * 10) for v in minimal_height_at_command_end],
    )

  async def expose_channel_n(
    self,
    channel_index: int = 1,
  ):
    """Expose channel n (A1PM:DQ).

    Args:
      channel_index: Channel index (1-based, 1..16).
    """
    return await self.driver.send_command(
      module="A1PM",
      command="DQ",
      pn=channel_index,
    )

  async def calculates_check_sums_and_compares_them_with_the_value_saved_in_flash_eprom(
    self,
    TODO_DC_0: float = 0,
    TODO_DC_1: float = 300.0,
    tip_type: Optional[List[int]] = None,
    TODO_DC_2: Optional[List[float]] = None,
    z_deposit_position: Optional[List[float]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    first_pip_channel_node_no: int = 1,
  ):
    """Calculates check sums and compares them with the value saved in Flash EPROM (A1PM:DC).

    Args:
      TODO_DC_0: Unknown parameter, in mm.
      TODO_DC_1: Unknown parameter, in mm.
      tip_type: Tip type (see command TT).
      TODO_DC_2: Unknown parameter, in mm.
      z_deposit_position: Z deposit position in mm (collar bearing position).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        in mm.
      first_pip_channel_node_no: First (lower) pip channel node no. (0 = disabled).
    """
    n = self.driver.num_channels
    if tip_type is None:
      tip_type = [4] * n
    if TODO_DC_2 is None:
      TODO_DC_2 = [0.0] * n
    if z_deposit_position is None:
      z_deposit_position = [0.0] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [360.0] * n

    return await self.driver.send_command(
      module="A1PM",
      command="DC",
      xa=round(TODO_DC_0 * 10),
      yj=round(TODO_DC_1 * 10),
      tt=tip_type,
      tp=[round(v * 10) for v in TODO_DC_2],
      tz=[round(v * 10) for v in z_deposit_position],
      th=[round(v * 10) for v in minimal_traverse_height_at_begin_of_command],
      pa=first_pip_channel_node_no,
    )

  async def set_any_parameter_within_this_module(self):
    """Set any parameter within this module (A1PM:AA)."""
    return await self.driver.send_command(
      module="A1PM",
      command="AA",
    )

  async def request_y_positions_of_all_channels(self) -> Dict:
    """Request Y positions of all channels (A1PM:RY).

    Returns:
      Parsed firmware response dict.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="RY",
    )

  async def request_y_position_of_channel_n(self, channel_index: int = 1) -> Dict:
    """Request Y position of channel n (A1PM:RB).

    Args:
      channel_index: Channel index (1-based).

    Returns:
      Parsed firmware response dict.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="RB",
      pn=channel_index,
    )

  async def request_z_positions_of_all_channels(self) -> Dict:
    """Request Z positions of all channels (A1PM:RZ).

    Returns:
      Parsed firmware response dict.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="RZ",
    )

  async def request_z_position_of_channel_n(self, channel_index: int = 1) -> Dict:
    """Request Z position of channel n (A1PM:RD).

    Args:
      channel_index: Channel index (1-based).

    Returns:
      Parsed firmware response dict.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="RD",
      pn=channel_index,
    )

  async def request_height_of_last_lld(self) -> Dict:
    """Request height of last LLD (A1PM:RL).

    Returns:
      Parsed firmware response dict.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="RL",
    )

  async def request_channel_dispense_on_fly_status(self) -> Dict:
    """Request channel dispense on fly status (A1PM:QF).

    Returns:
      Parsed firmware response dict.
    """
    return await self.driver.send_command(
      module="A1PM",
      command="QF",
    )

  # -- advanced PIP commands (A1PM) ------------------------------------------

  async def simultaneous_aspiration_dispensation_of_liquid(
    self,
    x_position: List[int],
    y_position: List[int],
    type_of_aspiration: Optional[List[int]] = None,
    type_of_dispensing_mode: Optional[List[int]] = None,
    tip_pattern: Optional[List[bool]] = None,
    TODO_DM_1: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
    lld_search_height: Optional[List[float]] = None,
    clot_detection_height: Optional[List[float]] = None,
    liquid_surface_at_function_without_lld: Optional[List[float]] = None,
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[float]] = None,
    minimum_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    tube_2nd_section_height_measured_from_zm: Optional[List[float]] = None,
    tube_2nd_section_ratio: Optional[List[int]] = None,
    aspiration_volume: Optional[List[float]] = None,
    TODO_DM_3: Optional[List[float]] = None,
    aspiration_speed: Optional[List[float]] = None,
    dispense_volume: Optional[List[float]] = None,
    dispense_speed: Optional[List[float]] = None,
    cut_off_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    blow_out_air_volume: Optional[List[float]] = None,
    pre_wetting_volume: Optional[List[float]] = None,
    lld_mode: Optional[List[int]] = None,
    aspirate_position_above_z_touch_off: Optional[List[float]] = None,
    lld_sensitivity: Optional[List[int]] = None,
    pressure_lld_sensitivity: Optional[List[int]] = None,
    swap_speed: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    mix_volume: Optional[List[float]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_in_z_direction_from_liquid_surface: Optional[List[float]] = None,
    mix_speed: Optional[List[float]] = None,
    surface_following_distance_during_mixing: Optional[List[float]] = None,
    TODO_DM_5: Optional[List[int]] = None,
    capacitive_mad_supervision_on_off: Optional[List[int]] = None,
    pressure_mad_supervision_on_off: Optional[List[int]] = None,
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
  ):
    """Simultaneous aspiration and dispensation of liquid (A1PM:DM).

    All distances are in mm, volumes in uL, speeds in uL/s (or mm/s for swap_speed),
    times in seconds. Conversion to firmware units happens internally.

    Args:
      x_position: X positions in 0.1mm (firmware units).
      y_position: Y positions in 0.1mm (firmware units).
      type_of_aspiration: Type of aspiration (0 = simple, 1 = sequence, 2 = cup emptied).
      type_of_dispensing_mode: Dispensing mode (0..4).
      tip_pattern: Channels involved.
      TODO_DM_1: Unknown firmware parameter.
      minimal_traverse_height_at_begin_of_command: mm.
      minimal_height_at_command_end: mm.
      lld_search_height: mm.
      clot_detection_height: mm.
      liquid_surface_at_function_without_lld: mm.
      pull_out_distance_to_take_transport_air_in_function_without_lld: mm.
      minimum_height: mm.
      immersion_depth: mm.
      surface_following_distance: mm.
      tube_2nd_section_height_measured_from_zm: mm.
      tube_2nd_section_ratio: ratio (raw firmware value, no conversion).
      aspiration_volume: uL.
      TODO_DM_3: uL.
      aspiration_speed: uL/s.
      dispense_volume: uL.
      dispense_speed: uL/s.
      cut_off_speed: uL/s.
      stop_back_volume: uL.
      transport_air_volume: uL.
      blow_out_air_volume: uL.
      pre_wetting_volume: uL.
      lld_mode: LLD mode (0 = off).
      aspirate_position_above_z_touch_off: mm.
      lld_sensitivity: LLD sensitivity (1 = high, 4 = low).
      pressure_lld_sensitivity: Pressure LLD sensitivity (1 = high, 4 = low).
      swap_speed: Swap speed in mm/s.
      settling_time: Settling time in seconds.
      mix_volume: uL.
      mix_cycles: Number of mix cycles.
      mix_position_in_z_direction_from_liquid_surface: mm.
      mix_speed: uL/s.
      surface_following_distance_during_mixing: mm.
      TODO_DM_5: Unknown firmware parameter.
      capacitive_mad_supervision_on_off: 0 = off, 1 = on.
      pressure_mad_supervision_on_off: 0 = off, 1 = on.
      tadm_algorithm_on_off: 0 = off, 1 = on.
      limit_curve_index: TADM limit curve index.
      recording_mode: 0 = no, 1 = TADM errors only, 2 = all.
    """
    n = self.driver.num_channels
    if type_of_aspiration is None:
      type_of_aspiration = [0] * n
    if type_of_dispensing_mode is None:
      type_of_dispensing_mode = [0] * n
    if tip_pattern is None:
      tip_pattern = [False] * n
    if TODO_DM_1 is None:
      TODO_DM_1 = [0] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [360.0] * n
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [360.0] * n
    if lld_search_height is None:
      lld_search_height = [0.0] * n
    if clot_detection_height is None:
      clot_detection_height = [6.0] * n
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [360.0] * n
    if pull_out_distance_to_take_transport_air_in_function_without_lld is None:
      pull_out_distance_to_take_transport_air_in_function_without_lld = [5.0] * n
    if minimum_height is None:
      minimum_height = [360.0] * n
    if immersion_depth is None:
      immersion_depth = [0.0] * n
    if surface_following_distance is None:
      surface_following_distance = [0.0] * n
    if tube_2nd_section_height_measured_from_zm is None:
      tube_2nd_section_height_measured_from_zm = [0.0] * n
    if tube_2nd_section_ratio is None:
      tube_2nd_section_ratio = [0] * n
    if aspiration_volume is None:
      aspiration_volume = [0.0] * n
    if TODO_DM_3 is None:
      TODO_DM_3 = [0.0] * n
    if aspiration_speed is None:
      aspiration_speed = [50.0] * n
    if dispense_volume is None:
      dispense_volume = [0.0] * n
    if dispense_speed is None:
      dispense_speed = [50.0] * n
    if cut_off_speed is None:
      cut_off_speed = [25.0] * n
    if stop_back_volume is None:
      stop_back_volume = [0.0] * n
    if transport_air_volume is None:
      transport_air_volume = [0.0] * n
    if blow_out_air_volume is None:
      blow_out_air_volume = [0.0] * n
    if pre_wetting_volume is None:
      pre_wetting_volume = [0.0] * n
    if lld_mode is None:
      lld_mode = [1] * n
    if aspirate_position_above_z_touch_off is None:
      aspirate_position_above_z_touch_off = [0.5] * n
    if lld_sensitivity is None:
      lld_sensitivity = [1] * n
    if pressure_lld_sensitivity is None:
      pressure_lld_sensitivity = [1] * n
    if swap_speed is None:
      swap_speed = [10.0] * n
    if settling_time is None:
      settling_time = [0.5] * n
    if mix_volume is None:
      mix_volume = [0.0] * n
    if mix_cycles is None:
      mix_cycles = [0] * n
    if mix_position_in_z_direction_from_liquid_surface is None:
      mix_position_in_z_direction_from_liquid_surface = [25.0] * n
    if mix_speed is None:
      mix_speed = [50.0] * n
    if surface_following_distance_during_mixing is None:
      surface_following_distance_during_mixing = [0.0] * n
    if TODO_DM_5 is None:
      TODO_DM_5 = [0] * n
    if capacitive_mad_supervision_on_off is None:
      capacitive_mad_supervision_on_off = [0] * n
    if pressure_mad_supervision_on_off is None:
      pressure_mad_supervision_on_off = [0] * n
    if limit_curve_index is None:
      limit_curve_index = [0] * n

    if not all(0 <= x <= 2 for x in type_of_aspiration):
      raise ValueError("type_of_aspiration must be in range 0 to 2")
    if not all(0 <= x <= 4 for x in type_of_dispensing_mode):
      raise ValueError("type_of_dispensing_mode must be in range 0 to 4")
    if not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")
    if not all(0 <= x <= 1 for x in TODO_DM_1):
      raise ValueError("TODO_DM_1 must be in range 0 to 1")
    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")
    if not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")
    if not all(0 <= x <= 360.0 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in lld_search_height):
      raise ValueError("lld_search_height must be in range 0 to 360.0")
    if not all(0 <= x <= 50.0 for x in clot_detection_height):
      raise ValueError("clot_detection_height must be in range 0 to 50.0")
    if not all(0 <= x <= 360.0 for x in liquid_surface_at_function_without_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 360.0")
    if not all(
      0 <= x <= 360.0 for x in pull_out_distance_to_take_transport_air_in_function_without_lld
    ):
      raise ValueError(
        "pull_out_distance_to_take_transport_air_in_function_without_lld must be in range 0 to 360.0"
      )
    if not all(0 <= x <= 360.0 for x in minimum_height):
      raise ValueError("minimum_height must be in range 0 to 360.0")
    if not all(-360.0 <= x <= 360.0 for x in immersion_depth):
      raise ValueError("immersion_depth must be in range -360.0 to 360.0")
    if not all(0 <= x <= 360.0 for x in surface_following_distance):
      raise ValueError("surface_following_distance must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in tube_2nd_section_height_measured_from_zm):
      raise ValueError("tube_2nd_section_height_measured_from_zm must be in range 0 to 360.0")
    if not all(0 <= x <= 10000 for x in tube_2nd_section_ratio):
      raise ValueError("tube_2nd_section_ratio must be in range 0 to 10000")
    if not all(0 <= x <= 1250.0 for x in aspiration_volume):
      raise ValueError("aspiration_volume must be in range 0 to 1250.0")
    if not all(0 <= x <= 1250.0 for x in TODO_DM_3):
      raise ValueError("TODO_DM_3 must be in range 0 to 1250.0")
    if not all(1.0 <= x <= 1000.0 for x in aspiration_speed):
      raise ValueError("aspiration_speed must be in range 1.0 to 1000.0")
    if not all(0 <= x <= 1250.0 for x in dispense_volume):
      raise ValueError("dispense_volume must be in range 0 to 1250.0")
    if not all(1.0 <= x <= 1000.0 for x in dispense_speed):
      raise ValueError("dispense_speed must be in range 1.0 to 1000.0")
    if not all(1.0 <= x <= 1000.0 for x in cut_off_speed):
      raise ValueError("cut_off_speed must be in range 1.0 to 1000.0")
    if not all(0 <= x <= 18.0 for x in stop_back_volume):
      raise ValueError("stop_back_volume must be in range 0 to 18.0")
    if not all(0 <= x <= 50.0 for x in transport_air_volume):
      raise ValueError("transport_air_volume must be in range 0 to 50.0")
    if not all(0 <= x <= 1250.0 for x in blow_out_air_volume):
      raise ValueError("blow_out_air_volume must be in range 0 to 1250.0")
    if not all(0 <= x <= 99.9 for x in pre_wetting_volume):
      raise ValueError("pre_wetting_volume must be in range 0 to 99.9")
    if not all(0 <= x <= 4 for x in lld_mode):
      raise ValueError("lld_mode must be in range 0 to 4")
    if not all(0 <= x <= 10.0 for x in aspirate_position_above_z_touch_off):
      raise ValueError("aspirate_position_above_z_touch_off must be in range 0 to 10.0")
    if not all(1 <= x <= 4 for x in lld_sensitivity):
      raise ValueError("lld_sensitivity must be in range 1 to 4")
    if not all(1 <= x <= 4 for x in pressure_lld_sensitivity):
      raise ValueError("pressure_lld_sensitivity must be in range 1 to 4")
    if not all(0.3 <= x <= 160.0 for x in swap_speed):
      raise ValueError("swap_speed must be in range 0.3 to 160.0")
    if not all(0 <= x <= 9.9 for x in settling_time):
      raise ValueError("settling_time must be in range 0 to 9.9")
    if not all(0 <= x <= 1250.0 for x in mix_volume):
      raise ValueError("mix_volume must be in range 0 to 1250.0")
    if not all(0 <= x <= 99 for x in mix_cycles):
      raise ValueError("mix_cycles must be in range 0 to 99")
    if not all(0 <= x <= 90.0 for x in mix_position_in_z_direction_from_liquid_surface):
      raise ValueError("mix_position_in_z_direction_from_liquid_surface must be in range 0 to 90.0")
    if not all(1.0 <= x <= 1000.0 for x in mix_speed):
      raise ValueError("mix_speed must be in range 1.0 to 1000.0")
    if not all(0 <= x <= 360.0 for x in surface_following_distance_during_mixing):
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 360.0")
    if not all(0 <= x <= 1 for x in TODO_DM_5):
      raise ValueError("TODO_DM_5 must be in range 0 to 1")
    if not all(0 <= x <= 1 for x in capacitive_mad_supervision_on_off):
      raise ValueError("capacitive_mad_supervision_on_off must be in range 0 to 1")
    if not all(0 <= x <= 1 for x in pressure_mad_supervision_on_off):
      raise ValueError("pressure_mad_supervision_on_off must be in range 0 to 1")
    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")
    if not all(0 <= x <= 999 for x in limit_curve_index):
      raise ValueError("limit_curve_index must be in range 0 to 999")
    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    return await self.driver.send_command(
      module="A1PM",
      command="DM",
      at=type_of_aspiration,
      dm=type_of_dispensing_mode,
      tm=tip_pattern,
      dd=TODO_DM_1,
      xp=x_position,
      yp=y_position,
      th=[round(v * 10) for v in minimal_traverse_height_at_begin_of_command],
      te=[round(v * 10) for v in minimal_height_at_command_end],
      lp=[round(v * 10) for v in lld_search_height],
      ch=[round(v * 10) for v in clot_detection_height],
      zl=[round(v * 10) for v in liquid_surface_at_function_without_lld],
      po=[round(v * 10) for v in pull_out_distance_to_take_transport_air_in_function_without_lld],
      zx=[round(v * 10) for v in minimum_height],
      ip=[round(v * 10) for v in immersion_depth],
      fp=[round(v * 10) for v in surface_following_distance],
      zu=[round(v * 10) for v in tube_2nd_section_height_measured_from_zm],
      zr=tube_2nd_section_ratio,
      av=[round(v * 100) for v in aspiration_volume],
      ar=[round(v * 100) for v in TODO_DM_3],
      as_=[round(v * 10) for v in aspiration_speed],
      dv=[round(v * 100) for v in dispense_volume],
      ds=[round(v * 10) for v in dispense_speed],
      ss=[round(v * 10) for v in cut_off_speed],
      rv=[round(v * 10) for v in stop_back_volume],
      ta=[round(v * 10) for v in transport_air_volume],
      ba=[round(v * 100) for v in blow_out_air_volume],
      oa=[round(v * 10) for v in pre_wetting_volume],
      lm=lld_mode,
      zo=[round(v * 10) for v in aspirate_position_above_z_touch_off],
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      de=[round(v * 10) for v in swap_speed],
      wt=[round(v * 10) for v in settling_time],
      mv=[round(v * 10) for v in mix_volume],
      mc=mix_cycles,
      mp=[round(v * 10) for v in mix_position_in_z_direction_from_liquid_surface],
      ms=[round(v * 10) for v in mix_speed],
      mh=[round(v * 10) for v in surface_following_distance_during_mixing],
      la=TODO_DM_5,
      lb=capacitive_mad_supervision_on_off,
      lc=pressure_mad_supervision_on_off,
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index,
      gk=recording_mode,
    )

  async def dispense_on_fly(
    self,
    y_position: List[float],
    tip_pattern: Optional[List[bool]] = None,
    first_shoot_x_pos: float = 0,
    dispense_on_fly_pos_command_end: float = 0,
    x_acceleration_distance_before_first_shoot: float = 10.0,
    space_between_shoots: float = 9.0,
    x_speed: float = 27.0,
    number_of_shoots: int = 1,
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
    liquid_surface_at_function_without_lld: Optional[List[float]] = None,
    dispense_volume: Optional[List[float]] = None,
    dispense_speed: Optional[List[float]] = None,
    cut_off_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
  ):
    """Dispense on fly (A1PM:DF).

    All distances in mm, volumes in uL, speeds in uL/s (or mm/s for x_speed).

    Args:
      y_position: Y positions in mm.
      tip_pattern: Channels involved.
      first_shoot_x_pos: First shoot X position in mm.
      dispense_on_fly_pos_command_end: Dispense on fly position on command end in mm.
      x_acceleration_distance_before_first_shoot: X acceleration distance before first shoot
        in mm.
      space_between_shoots: Space between shoots (raster pitch) in mm (firmware uses 0.01mm).
      x_speed: X speed in mm/s.
      number_of_shoots: Number of shoots.
      minimal_traverse_height_at_begin_of_command: mm.
      minimal_height_at_command_end: mm.
      liquid_surface_at_function_without_lld: mm.
      dispense_volume: uL.
      dispense_speed: uL/s.
      cut_off_speed: uL/s.
      stop_back_volume: uL.
      transport_air_volume: uL.
      tadm_algorithm_on_off: 0 = off, 1 = on.
      limit_curve_index: TADM limit curve index.
      recording_mode: 0 = no, 1 = TADM errors only, 2 = all.
    """
    n = self.driver.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [360.0] * n
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [360.0] * n
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [360.0] * n
    if dispense_volume is None:
      dispense_volume = [0.0] * n
    if dispense_speed is None:
      dispense_speed = [50.0] * n
    if cut_off_speed is None:
      cut_off_speed = [25.0] * n
    if stop_back_volume is None:
      stop_back_volume = [0.0] * n
    if transport_air_volume is None:
      transport_air_volume = [0.0] * n
    if limit_curve_index is None:
      limit_curve_index = [0] * n

    if not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")
    if not -5000 <= first_shoot_x_pos <= 5000:
      raise ValueError("first_shoot_x_pos must be in range -5000 to 5000")
    if not -5000 <= dispense_on_fly_pos_command_end <= 5000:
      raise ValueError("dispense_on_fly_pos_command_end must be in range -5000 to 5000")
    if not 0 <= x_acceleration_distance_before_first_shoot <= 90:
      raise ValueError("x_acceleration_distance_before_first_shoot must be in range 0 to 90")
    if not 0.01 <= space_between_shoots <= 25.0:
      raise ValueError("space_between_shoots must be in range 0.01 to 25.0")
    if not 2.0 <= x_speed <= 2500.0:
      raise ValueError("x_speed must be in range 2.0 to 2500.0")
    if not 1 <= number_of_shoots <= 48:
      raise ValueError("number_of_shoots must be in range 1 to 48")
    if not all(0 <= x <= 360.0 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 360.0")
    if not all(0 <= x <= 360.0 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 360.0")
    if not all(0 <= x <= 650.0 for x in y_position):
      raise ValueError("y_position must be in range 0 to 650.0")
    if not all(0 <= x <= 360.0 for x in liquid_surface_at_function_without_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 360.0")
    if not all(0 <= x <= 1250.0 for x in dispense_volume):
      raise ValueError("dispense_volume must be in range 0 to 1250.0")
    if not all(1.0 <= x <= 1000.0 for x in dispense_speed):
      raise ValueError("dispense_speed must be in range 1.0 to 1000.0")
    if not all(1.0 <= x <= 1000.0 for x in cut_off_speed):
      raise ValueError("cut_off_speed must be in range 1.0 to 1000.0")
    if not all(0 <= x <= 18.0 for x in stop_back_volume):
      raise ValueError("stop_back_volume must be in range 0 to 18.0")
    if not all(0 <= x <= 50.0 for x in transport_air_volume):
      raise ValueError("transport_air_volume must be in range 0 to 50.0")
    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")
    if not all(0 <= x <= 999 for x in limit_curve_index):
      raise ValueError("limit_curve_index must be in range 0 to 999")
    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    return await self.driver.send_command(
      module="A1PM",
      command="DF",
      tm=tip_pattern,
      xa=round(first_shoot_x_pos * 10),
      xf=round(dispense_on_fly_pos_command_end * 10),
      xh=round(x_acceleration_distance_before_first_shoot * 10),
      xy=round(space_between_shoots * 100),
      xv=round(x_speed * 10),
      xi=number_of_shoots,
      th=[round(v * 10) for v in minimal_traverse_height_at_begin_of_command],
      te=[round(v * 10) for v in minimal_height_at_command_end],
      yp=[round(v * 10) for v in y_position],
      zl=[round(v * 10) for v in liquid_surface_at_function_without_lld],
      dv=[round(v * 100) for v in dispense_volume],
      ds=[round(v * 10) for v in dispense_speed],
      ss=[round(v * 10) for v in cut_off_speed],
      rv=[round(v * 10) for v in stop_back_volume],
      ta=[round(v * 10) for v in transport_air_volume],
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index,
      gk=recording_mode,
    )

  async def nano_pulse_dispense(
    self,
    x_position: List[int],
    y_position: List[float],
    TODO_DB_0: Optional[List[int]] = None,
    liquid_surface_at_function_without_lld: Optional[List[float]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
    TODO_DB_1: Optional[List[int]] = None,
    TODO_DB_2: Optional[List[int]] = None,
    TODO_DB_3: Optional[List[int]] = None,
    TODO_DB_4: Optional[List[int]] = None,
    TODO_DB_5: Optional[List[int]] = None,
    TODO_DB_6: Optional[List[int]] = None,
    TODO_DB_7: Optional[List[int]] = None,
    TODO_DB_8: Optional[List[int]] = None,
    TODO_DB_9: Optional[List[int]] = None,
    TODO_DB_10: Optional[List[int]] = None,
    TODO_DB_11: Optional[List[float]] = None,
    TODO_DB_12: Optional[List[int]] = None,
  ):
    """Nano pulse dispense (A1PM:DB).

    Args:
      x_position: X positions in 0.1mm (firmware units).
      y_position: Y positions in mm.
      TODO_DB_0: Unknown firmware parameter.
      liquid_surface_at_function_without_lld: mm.
      minimal_traverse_height_at_begin_of_command: mm.
      minimal_height_at_command_end: mm.
      TODO_DB_1..TODO_DB_12: Unknown firmware parameters (passed through as-is except
        distance-like TODO_DB_11 which is in mm).
    """
    n = self.driver.num_channels
    if TODO_DB_0 is None:
      TODO_DB_0 = [1] * n
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [360.0] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [360.0] * n
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [360.0] * n
    if TODO_DB_1 is None:
      TODO_DB_1 = [0] * n
    if TODO_DB_2 is None:
      TODO_DB_2 = [0] * n
    if TODO_DB_3 is None:
      TODO_DB_3 = [0] * n
    if TODO_DB_4 is None:
      TODO_DB_4 = [0] * n
    if TODO_DB_5 is None:
      TODO_DB_5 = [0] * n
    if TODO_DB_6 is None:
      TODO_DB_6 = [0] * n
    if TODO_DB_7 is None:
      TODO_DB_7 = [0] * n
    if TODO_DB_8 is None:
      TODO_DB_8 = [0] * n
    if TODO_DB_9 is None:
      TODO_DB_9 = [0] * n
    if TODO_DB_10 is None:
      TODO_DB_10 = [0] * n
    if TODO_DB_11 is None:
      TODO_DB_11 = [0.0] * n
    if TODO_DB_12 is None:
      TODO_DB_12 = [1] * n

    return await self.driver.send_command(
      module="A1PM",
      command="DB",
      tm=TODO_DB_0,
      xp=x_position,
      yp=[round(v * 10) for v in y_position],
      zl=[round(v * 10) for v in liquid_surface_at_function_without_lld],
      th=[round(v * 10) for v in minimal_traverse_height_at_begin_of_command],
      te=[round(v * 10) for v in minimal_height_at_command_end],
      pe=TODO_DB_1,
      pd=TODO_DB_2,
      pf=TODO_DB_3,
      pg=TODO_DB_4,
      ph=TODO_DB_5,
      pj=TODO_DB_6,
      pk=TODO_DB_7,
      pl=TODO_DB_8,
      pp=TODO_DB_9,
      pq=TODO_DB_10,
      pi=[round(v * 10) for v in TODO_DB_11],
      pm=TODO_DB_12,
    )

  async def wash_tips(
    self,
    x_position: List[int],
    y_position: List[float],
    tip_pattern: Optional[List[bool]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    liquid_surface_at_function_without_lld: Optional[List[float]] = None,
    aspiration_volume: Optional[List[float]] = None,
    aspiration_speed: Optional[List[float]] = None,
    dispense_speed: Optional[List[float]] = None,
    swap_speed: Optional[List[float]] = None,
    soak_time: int = 0,
    wash_cycles: int = 0,
    minimal_height_at_command_end: Optional[List[float]] = None,
  ):
    """Wash tips (A1PM:DW).

    All distances in mm, volumes in uL, speeds in uL/s (or mm/s for swap_speed).

    Args:
      x_position: X positions in 0.1mm (firmware units).
      y_position: Y positions in mm.
      tip_pattern: Channels involved.
      minimal_traverse_height_at_begin_of_command: mm.
      liquid_surface_at_function_without_lld: mm.
      aspiration_volume: uL.
      aspiration_speed: uL/s.
      dispense_speed: uL/s.
      swap_speed: mm/s.
      soak_time: Soak time (firmware value, no conversion).
      wash_cycles: Number of wash cycles.
      minimal_height_at_command_end: mm.
    """
    n = self.driver.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [360.0] * n
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [360.0] * n
    if aspiration_volume is None:
      aspiration_volume = [0.0] * n
    if aspiration_speed is None:
      aspiration_speed = [50.0] * n
    if dispense_speed is None:
      dispense_speed = [50.0] * n
    if swap_speed is None:
      swap_speed = [10.0] * n
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [360.0] * n

    return await self.driver.send_command(
      module="A1PM",
      command="DW",
      tm=tip_pattern,
      xp=x_position,
      yp=[round(v * 10) for v in y_position],
      th=[round(v * 10) for v in minimal_traverse_height_at_begin_of_command],
      zl=[round(v * 10) for v in liquid_surface_at_function_without_lld],
      av=[round(v * 100) for v in aspiration_volume],
      as_=[round(v * 10) for v in aspiration_speed],
      ds=[round(v * 10) for v in dispense_speed],
      de=[round(v * 10) for v in swap_speed],
      sa=soak_time,
      dc=wash_cycles,
      te=[round(v * 10) for v in minimal_height_at_command_end],
    )
