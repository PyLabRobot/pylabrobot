"""STAR PIP backend: translates PIP operations into STAR firmware commands."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.legacy.liquid_handling.liquid_classes.hamilton import (
  HamiltonLiquidClass,
  get_star_liquid_class,
)
from pylabrobot.resources import Tip, TipSpot, Well
from pylabrobot.resources.hamilton import HamiltonTip, TipDropMethod, TipPickupMethod, TipSize
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_backend import (
  STARFirmwareError,
  convert_star_firmware_error_to_plr_error,
)
from pylabrobot.resources.liquid import Liquid

if TYPE_CHECKING:
  from .driver import STARDriver

logger = logging.getLogger("pylabrobot")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ops_to_fw_positions(
  ops: Sequence[Union[Pickup, TipDrop, Aspiration, Dispense]],
  use_channels: List[int],
  num_channels: int,
) -> Tuple[List[int], List[int], List[bool]]:
  """Convert ops + use_channels into firmware x/y positions and tip pattern.

  Uses absolute coordinates (get_absolute_location) so the driver does not
  need a ``deck`` reference.  This mirrors HamiltonLiquidHandler._ops_to_fw_positions
  but is self-contained.
  """
  assert use_channels == sorted(use_channels), "Channels must be sorted."

  x_positions: List[int] = []
  y_positions: List[int] = []
  channels_involved: List[bool] = []

  for i, channel in enumerate(use_channels):
    # Pad unused channels with zeros.
    while channel > len(channels_involved):
      channels_involved.append(False)
      x_positions.append(0)
      y_positions.append(0)
    channels_involved.append(True)

    loc = ops[i].resource.get_absolute_location(x="c", y="c", z="b")
    x_positions.append(round((loc.x + ops[i].offset.x) * 10))
    y_positions.append(round((loc.y + ops[i].offset.y) * 10))

  # Minimum distance check (9mm per channel index difference).
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

  # Trailing padding (STAR firmware expects at least one extra slot when < num_channels).
  if len(x_positions) < num_channels:
    x_positions = x_positions + [0]
    y_positions = y_positions + [0]
    channels_involved = channels_involved + [False]

  return x_positions, y_positions, channels_involved


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LLDMode(enum.Enum):
  """Liquid level detection mode."""

  OFF = 0
  GAMMA = 1
  PRESSURE = 2
  DUAL = 3
  Z_TOUCH_OFF = 4


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

_DEFAULT_TRAVERSAL_HEIGHT = 245.0  # mm (matches legacy _channel_traversal_height)


def _resolve_liquid_classes(
  explicit: Optional[List[Optional[HamiltonLiquidClass]]],
  ops: list,
  jet: Union[bool, List[bool]],
  blow_out: Union[bool, List[bool]],
  is_aspirate: bool,
) -> List[Optional[HamiltonLiquidClass]]:
  """Resolve per-op Hamilton liquid classes.

  If ``explicit`` is None, auto-detect from tip properties for each op.
  If ``explicit`` is a list, use it as-is (None entries stay None, matching legacy behavior).
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
    result.append(get_star_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=False,
      is_tip=True,
      has_filter=tip.has_filter,
      liquid=Liquid.WATER,
      jet=jet[i],
      blow_out=blow_out[i],
    ))

  return result


def _fill(val: Optional[List], default: List) -> List:
  """Return *val* if given, otherwise *default*. Replace per-element None with default."""
  if val is None:
    return default
  if len(val) != len(default):
    raise ValueError(f"Value length must equal num operations ({len(default)}), but is {len(val)}")
  return [v if v is not None else d for v, d in zip(val, default)]


def _dispensing_mode_for_op(empty: bool, jet: bool, blow_out: bool) -> int:
  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  return 3 if blow_out else 2


# ---------------------------------------------------------------------------
# STARPIPBackend
# ---------------------------------------------------------------------------


def _assert_range(values, lo, hi, name):
  """Assert all values in a list are within [lo, hi]."""
  if not all(lo <= v <= hi for v in values):
    raise ValueError(f"{name} values must be between {lo} and {hi}, got {values}")


class STARPIPBackend(PIPBackend):
  """Translates PIP operations into STAR firmware commands via the driver."""

  def __init__(self, driver: STARDriver):
    self._driver = driver

  @property
  def num_channels(self) -> int:
    return self._driver.num_channels

  async def _ensure_iswap_parked(self) -> None:
    """Park the iSWAP if it is installed and not already parked."""
    iswap = getattr(self._driver, 'iswap', None)
    if iswap is not None and hasattr(iswap, 'parked') and not iswap.parked:
      await iswap.park()

  def _ensure_can_reach_position(
    self,
    use_channels: List[int],
    ops: Sequence[Union[Pickup, TipDrop, Aspiration, Dispense]],
    op_name: str,
  ) -> None:
    """Validate that each channel can physically reach its target Y position."""
    if self._driver.extended_conf is None:
      return  # skip validation if config not available (e.g. chatterbox)
    ext = self._driver.extended_conf
    spacings = getattr(self._driver, '_channels_minimum_y_spacing', None)
    if spacings is None:
      spacings = [ext.min_raster_pitch_pip_channels] * self.num_channels

    cant_reach = []
    for channel_idx, op in zip(use_channels, ops):
      loc = op.resource.get_absolute_location(x="c", y="c", z="b") + op.offset
      min_y = ext.left_arm_min_y_position + sum(spacings[channel_idx + 1:])
      max_y = ext.pip_maximal_y_position - sum(spacings[:channel_idx])
      if loc.y < min_y or loc.y > max_y:
        cant_reach.append(channel_idx)

    if cant_reach:
      raise ValueError(
        f"Channels {cant_reach} cannot reach their target positions in '{op_name}' operation.\n"
        "Robots with more than 8 channels have limited Y-axis reach per channel."
      )

  # -- pick_up_tips -----------------------------------------------------------

  @dataclass
  class PickUpTipsParams(BackendParams):
    """STAR-specific parameters for ``pick_up_tips``."""
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    pickup_method: Optional[TipPickupMethod] = None
    begin_tip_pick_up_process: Optional[float] = None
    end_tip_pick_up_process: Optional[float] = None

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    if not isinstance(backend_params, STARPIPBackend.PickUpTipsParams):
      backend_params = STARPIPBackend.PickUpTipsParams()

    await self._ensure_iswap_parked()
    self._ensure_can_reach_position(use_channels, ops, "pick_up_tips")

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    # Tip type registration.
    tips = set()
    for op in ops:
      tip = op.tip
      if not isinstance(tip, HamiltonTip):
        raise TypeError(f"Tip {tip} is not a HamiltonTip.")
      tips.add(tip)
    if len(tips) > 1:
      raise ValueError("Cannot mix tips with different tip types.")
    ham_tip = tips.pop()
    assert isinstance(ham_tip, HamiltonTip)
    ttti = await self._driver.get_or_assign_tip_type_index(ham_tip)

    # Z computations (absolute coordinates).
    max_z = max(
      op.resource.get_absolute_location(x="c", y="c", z="b").z + op.offset.z for op in ops
    )
    max_total_tip_length = max(op.tip.total_tip_length for op in ops)
    max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)

    if ham_tip.tip_size == TipSize.LOW_VOLUME:
      max_tip_length += 2
    elif ham_tip.tip_size != TipSize.STANDARD_VOLUME:
      max_tip_length -= 2

    begin_tip_pick_up_process = (
      round(backend_params.begin_tip_pick_up_process * 10)
      if backend_params.begin_tip_pick_up_process is not None
      else round((max_z + max_total_tip_length) * 10)
    )
    end_tip_pick_up_process = (
      round(backend_params.end_tip_pick_up_process * 10)
      if backend_params.end_tip_pick_up_process is not None
      else round((max_z + max_tip_length) * 10)
    )

    minimum_traverse_height_at_beginning_of_a_command = round(
      (backend_params.minimum_traverse_height_at_beginning_of_a_command or _DEFAULT_TRAVERSAL_HEIGHT)
      * 10
    )

    pickup_method = backend_params.pickup_method or ham_tip.pickup_method

    # Range validation (matches legacy pick_up_tip assertions).
    _assert_range(x_positions, 0, 25000, "x_positions")
    _assert_range(y_positions, 0, 6500, "y_positions")
    assert 0 <= begin_tip_pick_up_process <= 3600, "begin_tip_pick_up_process must be 0-3600"
    assert 0 <= end_tip_pick_up_process <= 3600, "end_tip_pick_up_process must be 0-3600"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600

    try:
      await self._driver.send_command(
        module="C0",
        command="TP",
        tip_pattern=channels_involved,
        read_timeout=max(120, self._driver.read_timeout),
        xp=[f"{x:05}" for x in x_positions],
        yp=[f"{y:04}" for y in y_positions],
        tm=channels_involved,
        tt=f"{ttti:02}",
        tp=f"{begin_tip_pick_up_process:04}",
        tz=f"{end_tip_pick_up_process:04}",
        th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
        td=pickup_method.value,
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise

  # -- drop_tips --------------------------------------------------------------

  @dataclass
  class DropTipsParams(BackendParams):
    """STAR-specific parameters for ``drop_tips``."""
    drop_method: Optional[TipDropMethod] = None
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    z_position_at_end_of_a_command: Optional[float] = None
    begin_tip_deposit_process: Optional[float] = None
    end_tip_deposit_process: Optional[float] = None

  async def drop_tips(
    self,
    ops: List[TipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    if not isinstance(backend_params, STARPIPBackend.DropTipsParams):
      backend_params = STARPIPBackend.DropTipsParams()

    await self._ensure_iswap_parked()
    self._ensure_can_reach_position(use_channels, ops, "drop_tips")

    drop_method = backend_params.drop_method
    if drop_method is None:
      if any(not isinstance(op.resource, TipSpot) for op in ops):
        drop_method = TipDropMethod.PLACE_SHIFT
      else:
        drop_method = TipDropMethod.DROP

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    max_z = max(
      op.resource.get_absolute_location(x="c", y="c", z="b").z + op.offset.z for op in ops
    )

    if backend_params.begin_tip_deposit_process is not None:
      begin_tip_deposit_process = round(backend_params.begin_tip_deposit_process * 10)
    elif drop_method == TipDropMethod.PLACE_SHIFT:
      begin_tip_deposit_process = round((max_z + 59.9) * 10)
    else:
      max_total_tip_length = max(op.tip.total_tip_length for op in ops)
      begin_tip_deposit_process = round((max_z + max_total_tip_length) * 10)

    if backend_params.end_tip_deposit_process is not None:
      end_tip_deposit_process = round(backend_params.end_tip_deposit_process * 10)
    elif drop_method == TipDropMethod.PLACE_SHIFT:
      end_tip_deposit_process = round((max_z + 49.9) * 10)
    else:
      max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)
      end_tip_deposit_process = round((max_z + max_tip_length) * 10)

    minimum_traverse_height_at_beginning_of_a_command = round(
      (backend_params.minimum_traverse_height_at_beginning_of_a_command or _DEFAULT_TRAVERSAL_HEIGHT)
      * 10
    )
    z_position_at_end_of_a_command = round(
      (backend_params.z_position_at_end_of_a_command or _DEFAULT_TRAVERSAL_HEIGHT) * 10
    )

    # Range validation (matches legacy discard_tip assertions).
    _assert_range(x_positions, 0, 25000, "x_positions")
    _assert_range(y_positions, 0, 6500, "y_positions")
    assert 0 <= begin_tip_deposit_process <= 3600, "begin_tip_deposit_process must be 0-3600"
    assert 0 <= end_tip_deposit_process <= 3600, "end_tip_deposit_process must be 0-3600"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    assert 0 <= z_position_at_end_of_a_command <= 3600

    try:
      await self._driver.send_command(
        module="C0",
        command="TR",
        tip_pattern=channels_involved,
        read_timeout=max(120, self._driver.read_timeout),
        xp=[f"{x:05}" for x in x_positions],
        yp=[f"{y:04}" for y in y_positions],
        tm=channels_involved,
        tp=begin_tip_deposit_process,
        tz=end_tip_deposit_process,
        th=minimum_traverse_height_at_beginning_of_a_command,
        te=z_position_at_end_of_a_command,
        ti=drop_method.value,
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise

  # -- aspirate ---------------------------------------------------------------

  @dataclass
  class AspirateParams(BackendParams):
    """STAR-specific parameters for ``aspirate``."""
    hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None
    disable_volume_correction: Optional[List[bool]] = None
    aspiration_type: Optional[List[int]] = None
    jet: Optional[List[bool]] = None
    blow_out: Optional[List[bool]] = None
    lld_search_height: Optional[List[float]] = None
    clot_detection_height: Optional[List[float]] = None
    pull_out_distance_transport_air: Optional[List[float]] = None
    second_section_height: Optional[List[float]] = None
    second_section_ratio: Optional[List[float]] = None
    minimum_height: Optional[List[float]] = None
    immersion_depth: Optional[List[float]] = None
    """Positive = go deeper into liquid, negative = go up out of liquid."""
    surface_following_distance: Optional[List[float]] = None
    transport_air_volume: Optional[List[float]] = None
    pre_wetting_volume: Optional[List[float]] = None
    lld_mode: Optional[List[LLDMode]] = None
    gamma_lld_sensitivity: Optional[List[int]] = None
    dp_lld_sensitivity: Optional[List[int]] = None
    aspirate_position_above_z_touch_off: Optional[List[float]] = None
    detection_height_difference_for_dual_lld: Optional[List[float]] = None
    swap_speed: Optional[List[float]] = None
    settling_time: Optional[List[float]] = None
    mix_position_from_liquid_surface: Optional[List[float]] = None
    mix_surface_following_distance: Optional[List[float]] = None
    limit_curve_index: Optional[List[int]] = None
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    min_z_endpos: Optional[float] = None
    liquid_surface_no_lld: Optional[List[float]] = None
    use_2nd_section_aspiration: Optional[List[bool]] = None
    retract_height_over_2nd_section_to_empty_tip: Optional[List[float]] = None
    dispensation_speed_during_emptying_tip: Optional[List[float]] = None
    dosing_drive_speed_during_2nd_section_search: Optional[List[float]] = None
    z_drive_speed_during_2nd_section_search: Optional[List[float]] = None
    cup_upper_edge: Optional[List[float]] = None
    tadm_algorithm: bool = False
    recording_mode: int = 0
    probe_liquid_height: bool = False
    auto_surface_following_distance: bool = False

  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    if not isinstance(backend_params, STARPIPBackend.AspirateParams):
      backend_params = STARPIPBackend.AspirateParams()

    await self._ensure_iswap_parked()
    self._ensure_can_reach_position(use_channels, ops, "aspirate")

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    n = len(ops)

    # Resolve liquid classes (auto-detect from tip if not provided).
    hlcs = _resolve_liquid_classes(backend_params.hamilton_liquid_classes, ops,
                                   jet=backend_params.jet or False,
                                   blow_out=backend_params.blow_out or False,
                                   is_aspirate=True)

    # Well bottoms (absolute z + material thickness).
    well_bottoms = [
      op.resource.get_absolute_location(x="c", y="c", z="b").z
      + op.offset.z
      + op.resource.material_z_thickness
      for op in ops
    ]

    # LLD search height.
    if backend_params.lld_search_height is None:
      lld_search_height = [
        wb + op.resource.get_absolute_size_z() + (2.7 if isinstance(op.resource, Well) else 5)
        for wb, op in zip(well_bottoms, ops)
      ]
    else:
      lld_search_height = [
        wb + sh for wb, sh in zip(well_bottoms, backend_params.lld_search_height)
      ]

    clot_detection_height = _fill(backend_params.clot_detection_height,
      [hlc.aspiration_clot_retract_height if hlc is not None else 0.0 for hlc in hlcs])
    pull_out_distance_transport_air = _fill(
      backend_params.pull_out_distance_transport_air, [10.0] * n
    )
    second_section_height = _fill(backend_params.second_section_height, [3.2] * n)
    second_section_ratio = _fill(backend_params.second_section_ratio, [618.0] * n)
    minimum_height = _fill(backend_params.minimum_height, well_bottoms)

    immersion_depth_raw = backend_params.immersion_depth or [0.0] * n
    immersion_depth_direction = backend_params.immersion_depth_direction or [
      0 if id_ >= 0 else 1 for id_ in immersion_depth_raw
    ]
    immersion_depth = [
      im * (-1 if immersion_depth_direction[i] else 1)
      for i, im in enumerate(immersion_depth_raw)
    ]

    surface_following_distance = _fill(backend_params.surface_following_distance, [0.0] * n)

    # Volumes (with liquid class correction).
    disable_vc = _fill(backend_params.disable_volume_correction, [False] * n)
    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_vc)
    ]

    # Flow rates (liquid class default).
    flow_rates = [
      op.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 100.0)
      for op, hlc in zip(ops, hlcs)
    ]

    transport_air_volume = _fill(backend_params.transport_air_volume,
      [hlc.aspiration_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs])
    blow_out_air_volumes = [
      op.blow_out_air_volume or (hlc.aspiration_blow_out_volume if hlc is not None else 0.0)
      for op, hlc in zip(ops, hlcs)
    ]
    pre_wetting_volume = _fill(backend_params.pre_wetting_volume, [0.0] * n)
    lld_mode = _fill(
      backend_params.lld_mode, [LLDMode.OFF] * n
    )
    gamma_lld_sensitivity = _fill(backend_params.gamma_lld_sensitivity, [1] * n)
    dp_lld_sensitivity = _fill(backend_params.dp_lld_sensitivity, [1] * n)
    aspirate_position_above_z_touch_off = _fill(
      backend_params.aspirate_position_above_z_touch_off, [0.0] * n
    )
    detection_height_difference_for_dual_lld = _fill(
      backend_params.detection_height_difference_for_dual_lld, [0.0] * n
    )
    swap_speed = _fill(backend_params.swap_speed,
      [hlc.aspiration_swap_speed if hlc is not None else 100.0 for hlc in hlcs])
    settling_time = _fill(backend_params.settling_time,
      [hlc.aspiration_settling_time if hlc is not None else 0.0 for hlc in hlcs])

    # Mix.
    mix_volume = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_position_from_liquid_surface = _fill(
      backend_params.mix_position_from_liquid_surface, [0.0] * n
    )
    mix_speed = [op.mix.flow_rate if op.mix is not None else 100.0 for op in ops]
    mix_surface_following_distance = _fill(
      backend_params.mix_surface_following_distance, [0.0] * n
    )
    limit_curve_index = _fill(backend_params.limit_curve_index, [0] * n)

    # Probe liquid height if requested.
    traverse_height_override = backend_params.minimum_traverse_height_at_beginning_of_a_command
    if backend_params.probe_liquid_height:
      if any(op.liquid_height is not None for op in ops):
        raise ValueError("Cannot use probe_liquid_height when liquid heights are set.")
      liquid_heights = await self._driver.probe_liquid_heights(
        containers=[op.resource for op in ops],
        use_channels=use_channels,
        resource_offsets=[op.offset for op in ops],
        move_to_z_safety_after=False,
      )
      logger.info("Detected liquid heights: %s", liquid_heights)
      traverse_height_override = 100.0
    else:
      liquid_heights = [op.liquid_height or 0.0 for op in ops]

    # Auto surface following distance.
    if backend_params.auto_surface_following_distance:
      if any(op.liquid_height is None for op in ops) and not backend_params.probe_liquid_height:
        raise ValueError(
          "To use auto_surface_following_distance all liquid heights must be set or "
          "probe_liquid_height must be True."
        )
      if any(not op.resource.supports_compute_height_volume_functions() for op in ops):
        raise ValueError(
          "auto_surface_following_distance requires containers with height<->volume functions."
        )
      current_volumes = [
        op.resource.compute_volume_from_height(liquid_heights[i]) for i, op in enumerate(ops)
      ]
      liquid_height_after = [
        op.resource.compute_height_from_volume(current_volumes[i] - op.volume)
        for i, op in enumerate(ops)
      ]
      surface_following_distance = [
        liquid_heights[i] - liquid_height_after[i] for i in range(n)
      ]

    liquid_surfaces_no_lld = backend_params.liquid_surface_no_lld or [
      wb + lh for wb, lh in zip(well_bottoms, liquid_heights)
    ]

    # Check surface following distance doesn't go below minimum height (when LLD is off).
    if any(
      (well_bottoms[i] + liquid_heights[i] - surface_following_distance[i] - minimum_height[i] < -1e-6)
      and lld_mode[i] == LLDMode.OFF
      for i in range(n)
    ):
      raise ValueError(
        f"surface_following_distance would result in a height below minimum_height. "
        f"Well bottom: {well_bottoms}, liquid height: {liquid_heights}, "
        f"surface_following_distance: {surface_following_distance}, minimum_height: {minimum_height}"
      )

    minimum_traverse_height_at_beginning_of_a_command = round(
      (traverse_height_override or _DEFAULT_TRAVERSAL_HEIGHT) * 10
    )
    min_z_endpos = round(
      (backend_params.min_z_endpos or _DEFAULT_TRAVERSAL_HEIGHT) * 10
    )

    # Range validation (matches legacy aspirate_pip assertions, firmware units = real * 10).
    aspiration_types = _fill(backend_params.aspiration_type, [0] * n)
    _assert_range(aspiration_types, 0, 2, "aspiration_type")
    _assert_range(x_positions, 0, 25000, "x_positions")
    _assert_range(y_positions, 0, 6500, "y_positions")
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    assert 0 <= min_z_endpos <= 3600
    _assert_range([round(v * 10) for v in lld_search_height], 0, 3600, "lld_search_height")
    _assert_range([round(v * 10) for v in clot_detection_height], 0, 500, "clot_detection_height")
    _assert_range([round(v * 10) for v in liquid_surfaces_no_lld], 0, 3600, "liquid_surface_no_lld")
    _assert_range([round(v * 10) for v in pull_out_distance_transport_air], 0, 3600, "pull_out_distance_transport_air")
    _assert_range([round(v * 10) for v in second_section_height], 0, 3600, "second_section_height")
    _assert_range([round(v * 10) for v in second_section_ratio], 0, 10000, "second_section_ratio")
    _assert_range([round(v * 10) for v in minimum_height], 0, 3600, "minimum_height")
    _assert_range([round(v * 10) for v in immersion_depth], 0, 3600, "immersion_depth")
    _assert_range(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    _assert_range([round(v * 10) for v in surface_following_distance], 0, 3600, "surface_following_distance")
    _assert_range([round(v * 10) for v in volumes], 0, 12500, "aspiration_volumes")
    _assert_range([round(v * 10) for v in flow_rates], 4, 5000, "aspiration_speed")
    _assert_range([round(v * 10) for v in transport_air_volume], 0, 500, "transport_air_volume")
    _assert_range([round(v * 10) for v in blow_out_air_volumes], 0, 9999, "blow_out_air_volume")
    _assert_range([round(v * 10) for v in pre_wetting_volume], 0, 999, "pre_wetting_volume")
    _assert_range([m.value for m in lld_mode], 0, 4, "lld_mode")
    _assert_range(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    _assert_range(dp_lld_sensitivity, 1, 4, "dp_lld_sensitivity")
    _assert_range([round(v * 10) for v in aspirate_position_above_z_touch_off], 0, 100, "aspirate_position_above_z_touch_off")
    _assert_range([round(v * 10) for v in detection_height_difference_for_dual_lld], 0, 99, "detection_height_difference_for_dual_lld")
    _assert_range([round(v * 10) for v in swap_speed], 3, 1600, "swap_speed")
    _assert_range([round(v * 10) for v in settling_time], 0, 99, "settling_time")
    _assert_range([round(v * 10) for v in mix_volume], 0, 12500, "mix_volume")
    _assert_range(mix_cycles, 0, 99, "mix_cycles")
    _assert_range([round(v * 10) for v in mix_position_from_liquid_surface], 0, 900, "mix_position_from_liquid_surface")
    _assert_range([round(v * 10) for v in mix_speed], 4, 5000, "mix_speed")
    _assert_range([round(v * 10) for v in mix_surface_following_distance], 0, 3600, "mix_surface_following_distance")
    _assert_range(limit_curve_index, 0, 999, "limit_curve_index")
    assert 0 <= backend_params.recording_mode <= 2, "recording_mode must be between 0 and 2"
    # 2nd section aspiration range checks
    _assert_range([round(v * 10) for v in _fill(
      backend_params.retract_height_over_2nd_section_to_empty_tip, [0.0] * n)], 0, 3600,
      "retract_height_over_2nd_section_to_empty_tip")
    _assert_range([round(v * 10) for v in _fill(
      backend_params.dispensation_speed_during_emptying_tip, [50.0] * n)], 4, 5000,
      "dispensation_speed_during_emptying_tip")
    _assert_range([round(v * 10) for v in _fill(
      backend_params.dosing_drive_speed_during_2nd_section_search, [50.0] * n)], 4, 5000,
      "dosing_drive_speed_during_2nd_section_search")
    _assert_range([round(v * 10) for v in _fill(
      backend_params.z_drive_speed_during_2nd_section_search, [30.0] * n)], 3, 1600,
      "z_drive_speed_during_2nd_section_search")
    _assert_range([round(v * 10) for v in _fill(
      backend_params.cup_upper_edge, [0.0] * n)], 0, 3600, "cup_upper_edge")

    try:
      await self._driver.send_command(
        module="C0",
        command="AS",
        tip_pattern=channels_involved,
        read_timeout=max(300, self._driver.read_timeout),
        at=[f"{at:01}" for at in aspiration_types],
        tm=channels_involved,
        xp=[f"{xp:05}" for xp in x_positions],
        yp=[f"{yp:04}" for yp in y_positions],
        th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
        te=f"{min_z_endpos:04}",
        lp=[f"{round(lsh * 10):04}" for lsh in lld_search_height],
        ch=[f"{round(cd * 10):03}" for cd in clot_detection_height],
        zl=[f"{round(ls * 10):04}" for ls in liquid_surfaces_no_lld],
        po=[f"{round(po * 10):04}" for po in pull_out_distance_transport_air],
        zu=[f"{round(sh * 10):04}" for sh in second_section_height],
        zr=[f"{round(sr * 10):05}" for sr in second_section_ratio],
        zx=[f"{round(mh * 10):04}" for mh in minimum_height],
        ip=[f"{round(id_ * 10):04}" for id_ in immersion_depth],
        it=[f"{idd}" for idd in immersion_depth_direction],
        fp=[f"{round(sfd * 10):04}" for sfd in surface_following_distance],
        av=[f"{round(vol * 10):05}" for vol in volumes],
        as_=[f"{round(fr * 10):04}" for fr in flow_rates],
        ta=[f"{round(tav * 10):03}" for tav in transport_air_volume],
        ba=[f"{round(boa * 10):04}" for boa in blow_out_air_volumes],
        oa=[f"{round(pwv * 10):03}" for pwv in pre_wetting_volume],
        lm=[f"{mode.value}" for mode in lld_mode],
        ll=[f"{s}" for s in gamma_lld_sensitivity],
        lv=[f"{s}" for s in dp_lld_sensitivity],
        zo=[f"{round(ap * 10):03}" for ap in aspirate_position_above_z_touch_off],
        ld=[f"{round(dh * 10):02}" for dh in detection_height_difference_for_dual_lld],
        de=[f"{round(ss * 10):04}" for ss in swap_speed],
        wt=[f"{round(st * 10):02}" for st in settling_time],
        mv=[f"{round(v * 10):05}" for v in mix_volume],
        mc=[f"{c:02}" for c in mix_cycles],
        mp=[f"{round(p * 10):03}" for p in mix_position_from_liquid_surface],
        ms=[f"{round(s * 10):04}" for s in mix_speed],
        mh=[f"{round(d * 10):04}" for d in mix_surface_following_distance],
        gi=[f"{i:03}" for i in limit_curve_index],
        gj=backend_params.tadm_algorithm,
        gk=backend_params.recording_mode,
        lk=[1 if x else 0 for x in _fill(backend_params.use_2nd_section_aspiration, [False] * n)],
        ik=[f"{round(x * 10):04}" for x in _fill(
          backend_params.retract_height_over_2nd_section_to_empty_tip, [0.0] * n)],
        sd=[f"{round(x * 10):04}" for x in _fill(
          backend_params.dispensation_speed_during_emptying_tip, [50.0] * n)],
        se=[f"{round(x * 10):04}" for x in _fill(
          backend_params.dosing_drive_speed_during_2nd_section_search, [50.0] * n)],
        sz=[f"{round(x * 10):04}" for x in _fill(
          backend_params.z_drive_speed_during_2nd_section_search, [30.0] * n)],
        io=[f"{round(x * 10):04}" for x in _fill(backend_params.cup_upper_edge, [0.0] * n)],
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise

  # -- dispense ---------------------------------------------------------------

  @dataclass
  class DispenseParams(BackendParams):
    """STAR-specific parameters for ``dispense``."""
    hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None
    disable_volume_correction: Optional[List[bool]] = None
    jet: Optional[List[bool]] = None
    blow_out: Optional[List[bool]] = None
    empty: Optional[List[bool]] = None
    lld_search_height: Optional[List[float]] = None
    liquid_surface_no_lld: Optional[List[float]] = None
    pull_out_distance_transport_air: Optional[List[float]] = None
    second_section_height: Optional[List[float]] = None
    second_section_ratio: Optional[List[float]] = None
    minimum_height: Optional[List[float]] = None
    immersion_depth: Optional[List[float]] = None
    immersion_depth_direction: Optional[List[int]] = None
    surface_following_distance: Optional[List[float]] = None
    cut_off_speed: Optional[List[float]] = None
    stop_back_volume: Optional[List[float]] = None
    transport_air_volume: Optional[List[float]] = None
    lld_mode: Optional[List[LLDMode]] = None
    side_touch_off_distance: float = 0.0
    dispense_position_above_z_touch_off: Optional[List[float]] = None
    gamma_lld_sensitivity: Optional[List[int]] = None
    dp_lld_sensitivity: Optional[List[int]] = None
    swap_speed: Optional[List[float]] = None
    settling_time: Optional[List[float]] = None
    mix_position_from_liquid_surface: Optional[List[float]] = None
    mix_surface_following_distance: Optional[List[float]] = None
    limit_curve_index: Optional[List[int]] = None
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    min_z_endpos: Optional[float] = None
    tadm_algorithm: bool = False
    recording_mode: int = 0
    probe_liquid_height: bool = False
    auto_surface_following_distance: bool = False

  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    if not isinstance(backend_params, STARPIPBackend.DispenseParams):
      backend_params = STARPIPBackend.DispenseParams()

    await self._ensure_iswap_parked()
    self._ensure_can_reach_position(use_channels, ops, "dispense")

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    n = len(ops)

    # Dispensing mode.
    jet = backend_params.jet or [False] * n
    blow_out = backend_params.blow_out or [False] * n
    empty = backend_params.empty or [False] * n
    dispensing_modes = [
      _dispensing_mode_for_op(empty=empty[i], jet=jet[i], blow_out=blow_out[i])
      for i in range(n)
    ]

    # Resolve liquid classes.
    hlcs = _resolve_liquid_classes(backend_params.hamilton_liquid_classes, ops,
                                   jet=jet, blow_out=blow_out, is_aspirate=False)

    # Well bottoms.
    well_bottoms = [
      op.resource.get_absolute_location(x="c", y="c", z="b").z
      + op.offset.z
      + op.resource.material_z_thickness
      for op in ops
    ]

    # LLD search height.
    if backend_params.lld_search_height is None:
      lld_search_height = [
        wb + op.resource.get_absolute_size_z() + (2.7 if isinstance(op.resource, Well) else 5)
        for wb, op in zip(well_bottoms, ops)
      ]
    else:
      lld_search_height = [
        wb + sh for wb, sh in zip(well_bottoms, backend_params.lld_search_height)
      ]

    pull_out_distance_transport_air = _fill(
      backend_params.pull_out_distance_transport_air, [10.0] * n
    )
    second_section_height = _fill(backend_params.second_section_height, [3.2] * n)
    second_section_ratio = _fill(backend_params.second_section_ratio, [618.0] * n)
    minimum_height = _fill(backend_params.minimum_height, well_bottoms)

    immersion_depth_raw = backend_params.immersion_depth or [0.0] * n
    immersion_depth_direction = backend_params.immersion_depth_direction or [
      0 if id_ >= 0 else 1 for id_ in immersion_depth_raw
    ]
    immersion_depth = [
      im * (-1 if immersion_depth_direction[i] else 1)
      for i, im in enumerate(immersion_depth_raw)
    ]

    surface_following_distance = _fill(backend_params.surface_following_distance, [0.0] * n)

    # Volumes (with liquid class correction).
    disable_vc = _fill(backend_params.disable_volume_correction, [False] * n)
    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_vc)
    ]

    # Flow rates (liquid class default).
    flow_rates = [
      op.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 120.0)
      for op, hlc in zip(ops, hlcs)
    ]

    cut_off_speed = _fill(backend_params.cut_off_speed, [5.0] * n)
    stop_back_volume = _fill(backend_params.stop_back_volume,
      [hlc.dispense_stop_back_volume if hlc is not None else 0.0 for hlc in hlcs])
    transport_air_volume = _fill(backend_params.transport_air_volume,
      [hlc.dispense_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs])
    blow_out_air_volumes = [
      op.blow_out_air_volume or (hlc.dispense_blow_out_volume if hlc is not None else 0.0)
      for op, hlc in zip(ops, hlcs)
    ]

    lld_mode = _fill(backend_params.lld_mode, [LLDMode.OFF] * n)
    dispense_position_above_z_touch_off = _fill(
      backend_params.dispense_position_above_z_touch_off, [0.0] * n
    )
    gamma_lld_sensitivity = _fill(backend_params.gamma_lld_sensitivity, [1] * n)
    dp_lld_sensitivity = _fill(backend_params.dp_lld_sensitivity, [1] * n)
    swap_speed = _fill(backend_params.swap_speed,
      [hlc.dispense_swap_speed if hlc is not None else 10.0 for hlc in hlcs])
    settling_time = _fill(backend_params.settling_time,
      [hlc.dispense_settling_time if hlc is not None else 0.0 for hlc in hlcs])

    # Mix.
    mix_volume = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_position_from_liquid_surface = _fill(
      backend_params.mix_position_from_liquid_surface, [0.0] * n
    )
    mix_speed = [op.mix.flow_rate if op.mix is not None else 1.0 for op in ops]
    mix_surface_following_distance = _fill(
      backend_params.mix_surface_following_distance, [0.0] * n
    )
    limit_curve_index = _fill(backend_params.limit_curve_index, [0] * n)

    side_touch_off_distance = round(backend_params.side_touch_off_distance * 10)

    # Probe liquid height if requested.
    traverse_height_override = backend_params.minimum_traverse_height_at_beginning_of_a_command
    if backend_params.probe_liquid_height:
      if any(op.liquid_height is not None for op in ops):
        raise ValueError("Cannot use probe_liquid_height when liquid heights are set.")
      liquid_heights = await self._driver.probe_liquid_heights(
        containers=[op.resource for op in ops],
        use_channels=use_channels,
        resource_offsets=[op.offset for op in ops],
        move_to_z_safety_after=False,
      )
      logger.info("Detected liquid heights: %s", liquid_heights)
      traverse_height_override = 100.0
    else:
      liquid_heights = [op.liquid_height or 0.0 for op in ops]

    # Auto surface following distance.
    if backend_params.auto_surface_following_distance:
      if any(op.liquid_height is None for op in ops) and not backend_params.probe_liquid_height:
        raise ValueError(
          "To use auto_surface_following_distance all liquid heights must be set or "
          "probe_liquid_height must be True."
        )
      if any(not op.resource.supports_compute_height_volume_functions() for op in ops):
        raise ValueError(
          "auto_surface_following_distance requires containers with height<->volume functions."
        )
      current_volumes = [
        op.resource.compute_volume_from_height(liquid_heights[i]) for i, op in enumerate(ops)
      ]
      liquid_height_after = [
        op.resource.compute_height_from_volume(current_volumes[i] + op.volume)
        for i, op in enumerate(ops)
      ]
      surface_following_distance = [
        liquid_height_after[i] - liquid_heights[i] for i in range(n)
      ]

    liquid_surfaces_no_lld = backend_params.liquid_surface_no_lld or [
      wb + lh for wb, lh in zip(well_bottoms, liquid_heights)
    ]

    minimum_traverse_height_at_beginning_of_a_command = round(
      (traverse_height_override or _DEFAULT_TRAVERSAL_HEIGHT) * 10
    )
    min_z_endpos = round(
      (backend_params.min_z_endpos or _DEFAULT_TRAVERSAL_HEIGHT) * 10
    )

    # Range validation (matches legacy dispense_pip assertions, firmware units = real * 10).
    _assert_range(dispensing_modes, 0, 4, "dispensing_mode")
    _assert_range(x_positions, 0, 25000, "x_positions")
    _assert_range(y_positions, 0, 6500, "y_positions")
    _assert_range([round(v * 10) for v in minimum_height], 0, 3600, "minimum_height")
    _assert_range([round(v * 10) for v in lld_search_height], 0, 3600, "lld_search_height")
    _assert_range([round(v * 10) for v in liquid_surfaces_no_lld], 0, 3600, "liquid_surface_no_lld")
    _assert_range([round(v * 10) for v in pull_out_distance_transport_air], 0, 3600, "pull_out_distance_transport_air")
    _assert_range([round(v * 10) for v in immersion_depth], 0, 3600, "immersion_depth")
    _assert_range(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    _assert_range([round(v * 10) for v in surface_following_distance], 0, 3600, "surface_following_distance")
    _assert_range([round(v * 10) for v in second_section_height], 0, 3600, "second_section_height")
    _assert_range([round(v * 10) for v in second_section_ratio], 0, 10000, "second_section_ratio")
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    assert 0 <= min_z_endpos <= 3600
    _assert_range([round(v * 10) for v in volumes], 0, 12500, "dispense_volumes")
    _assert_range([round(v * 10) for v in flow_rates], 4, 5000, "dispense_speed")
    _assert_range([round(v * 10) for v in cut_off_speed], 4, 5000, "cut_off_speed")
    _assert_range([round(v * 10) for v in stop_back_volume], 0, 180, "stop_back_volume")
    _assert_range([round(v * 10) for v in transport_air_volume], 0, 500, "transport_air_volume")
    _assert_range([round(v * 10) for v in blow_out_air_volumes], 0, 9999, "blow_out_air_volume")
    _assert_range([m.value for m in lld_mode], 0, 4, "lld_mode")
    assert 0 <= side_touch_off_distance <= 45, "side_touch_off_distance must be between 0 and 45"
    _assert_range([round(v * 10) for v in dispense_position_above_z_touch_off], 0, 100, "dispense_position_above_z_touch_off")
    _assert_range(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    _assert_range(dp_lld_sensitivity, 1, 4, "dp_lld_sensitivity")
    _assert_range([round(v * 10) for v in swap_speed], 3, 1600, "swap_speed")
    _assert_range([round(v * 10) for v in settling_time], 0, 99, "settling_time")
    _assert_range([round(v * 10) for v in mix_volume], 0, 12500, "mix_volume")
    _assert_range(mix_cycles, 0, 99, "mix_cycles")
    _assert_range([round(v * 10) for v in mix_position_from_liquid_surface], 0, 900, "mix_position_from_liquid_surface")
    _assert_range([round(v * 10) for v in mix_speed], 4, 5000, "mix_speed")
    _assert_range([round(v * 10) for v in mix_surface_following_distance], 0, 3600, "mix_surface_following_distance")
    _assert_range(limit_curve_index, 0, 999, "limit_curve_index")
    assert 0 <= backend_params.recording_mode <= 2, "recording_mode must be between 0 and 2"

    try:
      await self._driver.send_command(
        module="C0",
        command="DS",
        tip_pattern=channels_involved,
        read_timeout=max(300, self._driver.read_timeout),
        dm=[f"{dm:01}" for dm in dispensing_modes],
        tm=[f"{int(t):01}" for t in channels_involved],
        xp=[f"{xp:05}" for xp in x_positions],
        yp=[f"{yp:04}" for yp in y_positions],
        zx=[f"{round(mh * 10):04}" for mh in minimum_height],
        lp=[f"{round(lsh * 10):04}" for lsh in lld_search_height],
        zl=[f"{round(ls * 10):04}" for ls in liquid_surfaces_no_lld],
        po=[f"{round(po * 10):04}" for po in pull_out_distance_transport_air],
        ip=[f"{round(id_ * 10):04}" for id_ in immersion_depth],
        it=[f"{idd:01}" for idd in immersion_depth_direction],
        fp=[f"{round(sfd * 10):04}" for sfd in surface_following_distance],
        zu=[f"{round(sh * 10):04}" for sh in second_section_height],
        zr=[f"{round(sr * 10):05}" for sr in second_section_ratio],
        th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
        te=f"{min_z_endpos:04}",
        dv=[f"{round(vol * 10):05}" for vol in volumes],
        ds=[f"{round(fr * 10):04}" for fr in flow_rates],
        ss=[f"{round(cs * 10):04}" for cs in cut_off_speed],
        rv=[f"{round(sbv * 10):03}" for sbv in stop_back_volume],
        ta=[f"{round(tav * 10):03}" for tav in transport_air_volume],
        ba=[f"{round(boa * 10):04}" for boa in blow_out_air_volumes],
        lm=[f"{mode.value:01}" for mode in lld_mode],
        dj=f"{side_touch_off_distance:02}",
        zo=[f"{round(dp * 10):03}" for dp in dispense_position_above_z_touch_off],
        ll=[f"{s:01}" for s in gamma_lld_sensitivity],
        lv=[f"{s:01}" for s in dp_lld_sensitivity],
        de=[f"{round(ss * 10):04}" for ss in swap_speed],
        wt=[f"{round(st * 10):02}" for st in settling_time],
        mv=[f"{round(v * 10):05}" for v in mix_volume],
        mc=[f"{c:02}" for c in mix_cycles],
        mp=[f"{round(p * 10):03}" for p in mix_position_from_liquid_surface],
        ms=[f"{round(s * 10):04}" for s in mix_speed],
        mh=[f"{round(d * 10):04}" for d in mix_surface_following_distance],
        gi=[f"{i:03}" for i in limit_curve_index],
        gj=backend_params.tadm_algorithm,
        gk=backend_params.recording_mode,
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise

  # -- can_pick_up_tip --------------------------------------------------------

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    return True
