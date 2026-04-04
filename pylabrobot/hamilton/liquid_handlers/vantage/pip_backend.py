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

    try:
      await self._pip_tip_pick_up(
        x_position=x_positions,
        y_position=y_positions,
        tip_pattern=tip_pattern,
        tip_type=ttti,
        begin_z_deposit_position=[max_z + max_total_tip_length] * len(ops),
        end_z_deposit_position=[max_z + max_tip_length] * len(ops),
        minimal_traverse_height_at_begin_of_command=list(mth or [th]) * len(ops),
        minimal_height_at_command_end=list(mhe or [th]) * len(ops),
        tip_handling_method=[1] * len(ops),
        blow_out_air_volume=[0] * len(ops),
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

    try:
      await self._pip_tip_discard(
        x_position=x_positions,
        y_position=y_positions,
        tip_pattern=channels_involved,
        begin_z_deposit_position=[max_z + 10] * len(ops),
        end_z_deposit_position=[max_z] * len(ops),
        minimal_traverse_height_at_begin_of_command=list(mth or [th]) * len(ops),
        minimal_height_at_command_end=list(mhe or [th]) * len(ops),
        tip_handling_method=[0] * len(ops),
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
      wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)
    ]
    lld_search_heights = backend_params.lld_search_height or [
      wb + op.resource.get_absolute_size_z() + (1.7 if isinstance(op.resource, Well) else 5)
      for wb, op in zip(well_bottoms, ops)
    ]

    flow_rates = [
      op.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 100)
      for op, hlc in zip(ops, hlcs)
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume or (hlc.dispense_blow_out_volume if hlc is not None else 0)
      for op, hlc in zip(ops, hlcs)
    ]

    th = self.driver.traversal_height
    mth = backend_params.minimal_traverse_height_at_begin_of_command
    mhe = backend_params.minimal_height_at_command_end

    try:
      await self._pip_aspirate(
        x_position=x_positions,
        y_position=y_positions,
        type_of_aspiration=backend_params.type_of_aspiration or [0] * len(ops),
        tip_pattern=channels_involved,
        minimal_traverse_height_at_begin_of_command=list(mth or [th]) * len(ops),
        minimal_height_at_command_end=list(mhe or [th]) * len(ops),
        lld_search_height=lld_search_heights,
        clot_detection_height=list(backend_params.clot_detection_height or [0] * len(ops)),
        liquid_surface_at_function_without_lld=liquid_surfaces_no_lld,
        pull_out_distance_to_take_transport_air_in_function_without_lld=list(
          backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
          or [10.9] * len(ops)
        ),
        tube_2nd_section_height_measured_from_zm=list(
          backend_params.tube_2nd_section_height_measured_from_zm or [0] * len(ops)
        ),
        tube_2nd_section_ratio=list(backend_params.tube_2nd_section_ratio or [0] * len(ops)),
        minimum_height=list(backend_params.minimum_height or well_bottoms),
        immersion_depth=list(backend_params.immersion_depth or [0] * len(ops)),
        surface_following_distance=list(
          backend_params.surface_following_distance or [0] * len(ops)
        ),
        aspiration_volume=volumes,
        aspiration_speed=flow_rates,
        transport_air_volume=list(
          backend_params.transport_air_volume
          or [hlc.aspiration_air_transport_volume if hlc is not None else 0 for hlc in hlcs]
        ),
        blow_out_air_volume=blow_out_air_volumes,
        pre_wetting_volume=list(backend_params.pre_wetting_volume or [0] * len(ops)),
        lld_mode=backend_params.lld_mode or [0] * len(ops),
        lld_sensitivity=backend_params.lld_sensitivity or [4] * len(ops),
        pressure_lld_sensitivity=backend_params.pressure_lld_sensitivity or [4] * len(ops),
        aspirate_position_above_z_touch_off=list(
          backend_params.aspirate_position_above_z_touch_off or [0.5] * len(ops)
        ),
        swap_speed=list(backend_params.swap_speed or [2] * len(ops)),
        settling_time=list(backend_params.settling_time or [1] * len(ops)),
        mix_volume=[op.mix.volume if op.mix is not None else 0 for op in ops],
        mix_cycles=[op.mix.repetitions if op.mix is not None else 0 for op in ops],
        mix_position_in_z_direction_from_liquid_surface=[0] * len(ops),
        mix_speed=[op.mix.flow_rate if op.mix is not None else 250 for op in ops],
        surface_following_distance_during_mixing=[0] * len(ops),
        capacitive_mad_supervision_on_off=(
          backend_params.capacitive_mad_supervision_on_off or [0] * len(ops)
        ),
        pressure_mad_supervision_on_off=(
          backend_params.pressure_mad_supervision_on_off or [0] * len(ops)
        ),
        tadm_algorithm_on_off=backend_params.tadm_algorithm_on_off,
        limit_curve_index=backend_params.limit_curve_index or [0] * len(ops),
        recording_mode=backend_params.recording_mode,
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
    liquid_surfaces_no_lld = [wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)]
    lld_search_heights = backend_params.lld_search_height or [
      wb + op.resource.get_absolute_size_z() + (1.7 if isinstance(op.resource, Well) else 5)
      for wb, op in zip(well_bottoms, ops)
    ]

    flow_rates = [
      op.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 100)
      for op, hlc in zip(ops, hlcs)
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume or (hlc.dispense_blow_out_volume if hlc is not None else 0)
      for op, hlc in zip(ops, hlcs)
    ]

    type_of_dispensing_mode = backend_params.type_of_dispensing_mode or [
      _get_dispense_mode(jet=jet[i], empty=empty[i], blow_out=blow_out[i]) for i in range(len(ops))
    ]

    th = self.driver.traversal_height
    mth = backend_params.minimal_traverse_height_at_begin_of_command
    mhe = backend_params.minimal_height_at_command_end

    try:
      await self._pip_dispense(
        x_position=x_positions,
        y_position=y_positions,
        tip_pattern=channels_involved,
        type_of_dispensing_mode=type_of_dispensing_mode,
        minimum_height=list(backend_params.minimum_height or well_bottoms),
        lld_search_height=lld_search_heights,
        liquid_surface_at_function_without_lld=liquid_surfaces_no_lld,
        pull_out_distance_to_take_transport_air_in_function_without_lld=list(
          backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
          or [5.0] * len(ops)
        ),
        immersion_depth=list(backend_params.immersion_depth or [0] * len(ops)),
        surface_following_distance=list(
          backend_params.surface_following_distance or [2.1] * len(ops)
        ),
        tube_2nd_section_height_measured_from_zm=list(
          backend_params.tube_2nd_section_height_measured_from_zm or [0] * len(ops)
        ),
        tube_2nd_section_ratio=list(backend_params.tube_2nd_section_ratio or [0] * len(ops)),
        minimal_traverse_height_at_begin_of_command=list(mth or [th]) * len(ops),
        minimal_height_at_command_end=list(mhe or [th]) * len(ops),
        dispense_volume=volumes,
        dispense_speed=flow_rates,
        cut_off_speed=list(backend_params.cut_off_speed or [250] * len(ops)),
        stop_back_volume=list(backend_params.stop_back_volume or [0] * len(ops)),
        transport_air_volume=list(
          backend_params.transport_air_volume
          or [hlc.dispense_air_transport_volume if hlc is not None else 0 for hlc in hlcs]
        ),
        blow_out_air_volume=blow_out_air_volumes,
        lld_mode=backend_params.lld_mode or [0] * len(ops),
        side_touch_off_distance=backend_params.side_touch_off_distance,
        dispense_position_above_z_touch_off=list(
          backend_params.dispense_position_above_z_touch_off or [0.5] * len(ops)
        ),
        lld_sensitivity=backend_params.lld_sensitivity or [1] * len(ops),
        pressure_lld_sensitivity=backend_params.pressure_lld_sensitivity or [1] * len(ops),
        swap_speed=list(backend_params.swap_speed or [1] * len(ops)),
        settling_time=list(backend_params.settling_time or [0] * len(ops)),
        mix_volume=[op.mix.volume if op.mix is not None else 0 for op in ops],
        mix_cycles=[op.mix.repetitions if op.mix is not None else 0 for op in ops],
        mix_position_in_z_direction_from_liquid_surface=[0] * len(ops),
        mix_speed=[op.mix.flow_rate if op.mix is not None else 1 for op in ops],
        surface_following_distance_during_mixing=[0] * len(ops),
        tadm_algorithm_on_off=backend_params.tadm_algorithm_on_off,
        limit_curve_index=backend_params.limit_curve_index or [0] * len(ops),
        recording_mode=backend_params.recording_mode,
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

  async def _pip_tip_pick_up(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: List[bool],
    tip_type: List[int],
    begin_z_deposit_position: List[float],
    end_z_deposit_position: List[float],
    minimal_traverse_height_at_begin_of_command: List[float],
    minimal_height_at_command_end: List[float],
    tip_handling_method: List[int],
    blow_out_air_volume: List[float],
  ):
    """Tip pick up (A1PM:TP).

    Args:
      x_position: X positions in 0.1mm (firmware units, from _ops_to_fw_positions).
      y_position: Y positions in 0.1mm (firmware units, from _ops_to_fw_positions).
      begin_z_deposit_position: Begin Z deposit position in mm.
      end_z_deposit_position: End Z deposit position in mm.
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command in mm.
      minimal_height_at_command_end: Minimal height at command end in mm.
      blow_out_air_volume: Blow out air volume in uL.
    """
    # Convert from PLR standard units to firmware units right before send_command.
    fw_begin_z = [round(z * 10) for z in begin_z_deposit_position]
    fw_end_z = [round(z * 10) for z in end_z_deposit_position]
    fw_th = [round(h * 10) for h in minimal_traverse_height_at_begin_of_command]
    fw_te = [round(h * 10) for h in minimal_height_at_command_end]
    fw_ba = [round(v * 100) for v in blow_out_air_volume]

    await self.driver.send_command(
      module="A1PM",
      command="TP",
      xp=x_position,
      yp=y_position,
      tm=tip_pattern,
      tt=tip_type,
      tp=fw_begin_z,
      tz=fw_end_z,
      th=fw_th,
      te=fw_te,
      ba=fw_ba,
      td=tip_handling_method,
    )

  async def _pip_tip_discard(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: List[bool],
    begin_z_deposit_position: List[float],
    end_z_deposit_position: List[float],
    minimal_traverse_height_at_begin_of_command: List[float],
    minimal_height_at_command_end: List[float],
    tip_handling_method: List[int],
    TODO_TR_2: int = 0,
  ):
    """Tip discard (A1PM:TR).

    Args:
      x_position: X positions in 0.1mm (firmware units, from _ops_to_fw_positions).
      y_position: Y positions in 0.1mm (firmware units, from _ops_to_fw_positions).
      begin_z_deposit_position: Begin Z deposit position in mm.
      end_z_deposit_position: End Z deposit position in mm.
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command in mm.
      minimal_height_at_command_end: Minimal height at command end in mm.
      TODO_TR_2: Unknown firmware parameter (maps to firmware key ``ts``).
    """
    # Convert from PLR standard units to firmware units right before send_command.
    fw_begin_z = [round(z * 10) for z in begin_z_deposit_position]
    fw_end_z = [round(z * 10) for z in end_z_deposit_position]
    fw_th = [round(h * 10) for h in minimal_traverse_height_at_begin_of_command]
    fw_te = [round(h * 10) for h in minimal_height_at_command_end]

    await self.driver.send_command(
      module="A1PM",
      command="TR",
      xp=x_position,
      yp=y_position,
      tp=fw_begin_z,
      tz=fw_end_z,
      th=fw_th,
      te=fw_te,
      tm=tip_pattern,
      ts=TODO_TR_2,
      td=tip_handling_method,
    )

  async def _pip_aspirate(
    self,
    x_position: List[int],
    y_position: List[int],
    type_of_aspiration: List[int],
    tip_pattern: List[bool],
    minimal_traverse_height_at_begin_of_command: List[float],
    minimal_height_at_command_end: List[float],
    lld_search_height: List[float],
    clot_detection_height: List[float],
    liquid_surface_at_function_without_lld: List[float],
    pull_out_distance_to_take_transport_air_in_function_without_lld: List[float],
    tube_2nd_section_height_measured_from_zm: List[float],
    tube_2nd_section_ratio: List[float],
    minimum_height: List[float],
    immersion_depth: List[float],
    surface_following_distance: List[float],
    aspiration_volume: List[float],
    aspiration_speed: List[float],
    transport_air_volume: List[float],
    blow_out_air_volume: List[float],
    pre_wetting_volume: List[float],
    lld_mode: List[int],
    lld_sensitivity: List[int],
    pressure_lld_sensitivity: List[int],
    aspirate_position_above_z_touch_off: List[float],
    swap_speed: List[float],
    settling_time: List[float],
    mix_volume: List[float],
    mix_cycles: List[int],
    mix_position_in_z_direction_from_liquid_surface: List[int],
    mix_speed: List[float],
    surface_following_distance_during_mixing: List[int],
    capacitive_mad_supervision_on_off: List[int],
    pressure_mad_supervision_on_off: List[int],
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
    TODO_DA_5: Optional[List[int]] = None,
  ):
    """Aspiration of liquid (A1PM:DA).

    All distances are in mm, volumes in uL, speeds in uL/s, times in seconds.
    Conversion to firmware units (0.1mm, 0.01uL, 0.1uL/s, 0.1s) happens internally.

    Args:
      x_position: X positions in 0.1mm (firmware units, from _ops_to_fw_positions).
      y_position: Y positions in 0.1mm (firmware units, from _ops_to_fw_positions).
      minimal_traverse_height_at_begin_of_command: mm.
      minimal_height_at_command_end: mm.
      lld_search_height: mm.
      clot_detection_height: mm.
      liquid_surface_at_function_without_lld: mm.
      pull_out_distance_to_take_transport_air_in_function_without_lld: mm.
      tube_2nd_section_height_measured_from_zm: mm.
      tube_2nd_section_ratio: ratio (multiplied by 10 for firmware).
      minimum_height: mm.
      immersion_depth: mm.
      surface_following_distance: mm.
      aspiration_volume: uL.
      aspiration_speed: uL/s.
      transport_air_volume: uL.
      blow_out_air_volume: uL.
      pre_wetting_volume: uL.
      aspirate_position_above_z_touch_off: mm.
      swap_speed: mm/s.
      settling_time: seconds.
      mix_volume: uL.
      mix_speed: uL/s.
      TODO_DA_5: Unknown firmware parameter (maps to firmware key ``la``). Defaults to all zeros.
    """
    # Convert from PLR standard units to firmware units right before send_command.
    # Distances: mm -> 0.1mm (x10)
    fw_th = [round(v * 10) for v in minimal_traverse_height_at_begin_of_command]
    fw_te = [round(v * 10) for v in minimal_height_at_command_end]
    fw_lp = [round(v * 10) for v in lld_search_height]
    fw_ch = [round(v * 10) for v in clot_detection_height]
    fw_zl = [round(v * 10) for v in liquid_surface_at_function_without_lld]
    fw_po = [round(v * 10) for v in pull_out_distance_to_take_transport_air_in_function_without_lld]
    fw_zu = [round(v * 10) for v in tube_2nd_section_height_measured_from_zm]
    fw_zx = [round(v * 10) for v in minimum_height]
    fw_ip = [round(v * 10) for v in immersion_depth]
    fw_fp = [round(v * 10) for v in surface_following_distance]
    fw_zo = [round(v * 10) for v in aspirate_position_above_z_touch_off]
    # tube_2nd_section_ratio: ratio x10 for firmware
    fw_zr = [round(v * 10) for v in tube_2nd_section_ratio]
    # Volumes: uL -> 0.01uL (x100)
    fw_av = [round(v * 100) for v in aspiration_volume]
    fw_ba = [round(v * 100) for v in blow_out_air_volume]
    fw_oa = [round(v * 100) for v in pre_wetting_volume]
    fw_mv = [round(v * 100) for v in mix_volume]
    # Transport air volume: uL -> 0.1uL (x10)
    fw_ta = [round(v * 10) for v in transport_air_volume]
    # Speeds: uL/s -> 0.1uL/s (x10)
    fw_as = [round(v * 10) for v in aspiration_speed]
    fw_ms = [round(v * 10) for v in mix_speed]
    # swap_speed: mm/s -> 0.1mm/s (x10)
    fw_de = [round(v * 10) for v in swap_speed]
    # settling_time: s -> 0.1s (x10)
    fw_wt = [round(v * 10) for v in settling_time]

    await self.driver.send_command(
      module="A1PM",
      command="DA",
      at=type_of_aspiration,
      tm=tip_pattern,
      xp=x_position,
      yp=y_position,
      th=fw_th,
      te=fw_te,
      lp=fw_lp,
      ch=fw_ch,
      zl=fw_zl,
      po=fw_po,
      zu=fw_zu,
      zr=fw_zr,
      zx=fw_zx,
      ip=fw_ip,
      fp=fw_fp,
      av=fw_av,
      as_=fw_as,
      ta=fw_ta,
      ba=fw_ba,
      oa=fw_oa,
      lm=lld_mode,
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      zo=fw_zo,
      de=fw_de,
      wt=fw_wt,
      mv=fw_mv,
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      ms=fw_ms,
      mh=surface_following_distance_during_mixing,
      la=TODO_DA_5 if TODO_DA_5 is not None else [0] * len(type_of_aspiration),
      lb=capacitive_mad_supervision_on_off,
      lc=pressure_mad_supervision_on_off,
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index or [0] * len(type_of_aspiration),
      gk=recording_mode,
    )

  async def _pip_dispense(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: List[bool],
    type_of_dispensing_mode: List[int],
    minimum_height: List[float],
    lld_search_height: List[float],
    liquid_surface_at_function_without_lld: List[float],
    pull_out_distance_to_take_transport_air_in_function_without_lld: List[float],
    immersion_depth: List[float],
    surface_following_distance: List[float],
    tube_2nd_section_height_measured_from_zm: List[float],
    tube_2nd_section_ratio: List[float],
    minimal_traverse_height_at_begin_of_command: List[float],
    minimal_height_at_command_end: List[float],
    dispense_volume: List[float],
    dispense_speed: List[float],
    cut_off_speed: List[float],
    stop_back_volume: List[float],
    transport_air_volume: List[float],
    blow_out_air_volume: List[float],
    lld_mode: List[int],
    side_touch_off_distance: float,
    dispense_position_above_z_touch_off: List[float],
    lld_sensitivity: List[int],
    pressure_lld_sensitivity: List[int],
    swap_speed: List[float],
    settling_time: List[float],
    mix_volume: List[float],
    mix_cycles: List[int],
    mix_position_in_z_direction_from_liquid_surface: List[int],
    mix_speed: List[float],
    surface_following_distance_during_mixing: List[int],
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
    TODO_DD_2: Optional[List[int]] = None,
  ):
    """Dispensing of liquid (A1PM:DD).

    All distances are in mm, volumes in uL, speeds in uL/s, times in seconds.
    Conversion to firmware units (0.1mm, 0.01uL, 0.1uL/s, 0.1s) happens internally.

    Args:
      x_position: X positions in 0.1mm (firmware units, from _ops_to_fw_positions).
      y_position: Y positions in 0.1mm (firmware units, from _ops_to_fw_positions).
      minimum_height: mm.
      lld_search_height: mm.
      liquid_surface_at_function_without_lld: mm.
      pull_out_distance_to_take_transport_air_in_function_without_lld: mm.
      immersion_depth: mm.
      surface_following_distance: mm.
      tube_2nd_section_height_measured_from_zm: mm.
      tube_2nd_section_ratio: ratio (multiplied by 10 for firmware).
      minimal_traverse_height_at_begin_of_command: mm.
      minimal_height_at_command_end: mm.
      dispense_volume: uL.
      dispense_speed: uL/s.
      cut_off_speed: uL/s.
      stop_back_volume: uL.
      transport_air_volume: uL.
      blow_out_air_volume: uL.
      side_touch_off_distance: mm.
      dispense_position_above_z_touch_off: mm.
      swap_speed: mm/s.
      settling_time: seconds.
      mix_volume: uL.
      mix_speed: uL/s.
      TODO_DD_2: Unknown firmware parameter (maps to firmware key ``la``). Defaults to all zeros.
    """
    # Convert from PLR standard units to firmware units right before send_command.
    # Distances: mm -> 0.1mm (x10)
    fw_zx = [round(v * 10) for v in minimum_height]
    fw_lp = [round(v * 10) for v in lld_search_height]
    fw_zl = [round(v * 10) for v in liquid_surface_at_function_without_lld]
    fw_po = [round(v * 10) for v in pull_out_distance_to_take_transport_air_in_function_without_lld]
    fw_ip = [round(v * 10) for v in immersion_depth]
    fw_fp = [round(v * 10) for v in surface_following_distance]
    fw_zu = [round(v * 10) for v in tube_2nd_section_height_measured_from_zm]
    fw_th = [round(v * 10) for v in minimal_traverse_height_at_begin_of_command]
    fw_te = [round(v * 10) for v in minimal_height_at_command_end]
    fw_zo = [round(v * 10) for v in dispense_position_above_z_touch_off]
    fw_dj = round(side_touch_off_distance * 10)
    # tube_2nd_section_ratio: ratio x10 for firmware
    fw_zr = [round(v * 10) for v in tube_2nd_section_ratio]
    # Volumes: uL -> 0.01uL (x100)
    fw_dv = [round(v * 100) for v in dispense_volume]
    fw_rv = [round(v * 100) for v in stop_back_volume]
    fw_ba = [round(v * 100) for v in blow_out_air_volume]
    fw_mv = [round(v * 100) for v in mix_volume]
    # Transport air volume: uL -> 0.1uL (x10)
    fw_ta = [round(v * 10) for v in transport_air_volume]
    # Speeds: uL/s -> 0.1uL/s (x10)
    fw_ds = [round(v * 10) for v in dispense_speed]
    fw_ss = [round(v * 10) for v in cut_off_speed]
    fw_ms = [round(v * 10) for v in mix_speed]
    # swap_speed: mm/s -> 0.1mm/s (x10)
    fw_de = [round(v * 10) for v in swap_speed]
    # settling_time: s -> 0.1s (x10)
    fw_wt = [round(v * 10) for v in settling_time]

    await self.driver.send_command(
      module="A1PM",
      command="DD",
      dm=type_of_dispensing_mode,
      tm=tip_pattern,
      xp=x_position,
      yp=y_position,
      zx=fw_zx,
      lp=fw_lp,
      zl=fw_zl,
      po=fw_po,
      ip=fw_ip,
      fp=fw_fp,
      zu=fw_zu,
      zr=fw_zr,
      th=fw_th,
      te=fw_te,
      dv=[f"{vol:04}" for vol in fw_dv],
      ds=fw_ds,
      ss=fw_ss,
      rv=fw_rv,
      ta=fw_ta,
      ba=fw_ba,
      lm=lld_mode,
      dj=fw_dj,
      zo=fw_zo,
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      de=fw_de,
      wt=fw_wt,
      mv=fw_mv,
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      ms=fw_ms,
      mh=surface_following_distance_during_mixing,
      la=TODO_DD_2 if TODO_DD_2 is not None else [0] * len(type_of_dispensing_mode),
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index or [0] * len(type_of_dispensing_mode),
      gk=recording_mode,
    )

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

    # Convert from PLR standard units to firmware units.
    # Distances: mm -> 0.1mm (x10)
    fw_th = [round(v * 10) for v in minimal_traverse_height_at_begin_of_command]
    fw_te = [round(v * 10) for v in minimal_height_at_command_end]
    fw_lp = [round(v * 10) for v in lld_search_height]
    fw_ch = [round(v * 10) for v in clot_detection_height]
    fw_zl = [round(v * 10) for v in liquid_surface_at_function_without_lld]
    fw_po = [round(v * 10) for v in pull_out_distance_to_take_transport_air_in_function_without_lld]
    fw_zx = [round(v * 10) for v in minimum_height]
    fw_ip = [round(v * 10) for v in immersion_depth]
    fw_fp = [round(v * 10) for v in surface_following_distance]
    fw_zu = [round(v * 10) for v in tube_2nd_section_height_measured_from_zm]
    fw_zo = [round(v * 10) for v in aspirate_position_above_z_touch_off]
    fw_mp = [round(v * 10) for v in mix_position_in_z_direction_from_liquid_surface]
    fw_mh = [round(v * 10) for v in surface_following_distance_during_mixing]
    # Volumes: uL -> 0.01uL (x100)
    fw_av = [round(v * 100) for v in aspiration_volume]
    fw_ar = [round(v * 100) for v in TODO_DM_3]
    fw_dv = [round(v * 100) for v in dispense_volume]
    fw_ba = [round(v * 100) for v in blow_out_air_volume]
    # Speeds: uL/s -> 0.1uL/s (x10)
    fw_as = [round(v * 10) for v in aspiration_speed]
    fw_ds = [round(v * 10) for v in dispense_speed]
    fw_ss = [round(v * 10) for v in cut_off_speed]
    fw_ms = [round(v * 10) for v in mix_speed]
    # stop_back_volume: uL -> 0.1uL (x10)
    fw_rv = [round(v * 10) for v in stop_back_volume]
    # transport_air_volume: uL -> 0.1uL (x10)
    fw_ta = [round(v * 10) for v in transport_air_volume]
    # pre_wetting_volume: uL -> 0.1uL (x10)
    fw_oa = [round(v * 10) for v in pre_wetting_volume]
    # mix_volume: uL -> 0.1uL (x10)
    fw_mv = [round(v * 10) for v in mix_volume]
    # swap_speed: mm/s -> 0.1mm/s (x10)
    fw_de = [round(v * 10) for v in swap_speed]
    # settling_time: s -> 0.1s (x10)
    fw_wt = [round(v * 10) for v in settling_time]

    return await self.driver.send_command(
      module="A1PM",
      command="DM",
      at=type_of_aspiration,
      dm=type_of_dispensing_mode,
      tm=tip_pattern,
      dd=TODO_DM_1,
      xp=x_position,
      yp=y_position,
      th=fw_th,
      te=fw_te,
      lp=fw_lp,
      ch=fw_ch,
      zl=fw_zl,
      po=fw_po,
      zx=fw_zx,
      ip=fw_ip,
      fp=fw_fp,
      zu=fw_zu,
      zr=tube_2nd_section_ratio,
      av=fw_av,
      ar=fw_ar,
      as_=fw_as,
      dv=fw_dv,
      ds=fw_ds,
      ss=fw_ss,
      rv=fw_rv,
      ta=fw_ta,
      ba=fw_ba,
      oa=fw_oa,
      lm=lld_mode,
      zo=fw_zo,
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      de=fw_de,
      wt=fw_wt,
      mv=fw_mv,
      mc=mix_cycles,
      mp=fw_mp,
      ms=fw_ms,
      mh=fw_mh,
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
