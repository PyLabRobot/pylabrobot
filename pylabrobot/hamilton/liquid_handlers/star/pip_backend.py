"""STAR PIP backend: translates PIP operations into STAR firmware commands."""

from __future__ import annotations

import enum
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Sequence, Tuple, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.capabilities.liquid_handling.utils import (
  get_tight_single_resource_liquid_op_offsets,
  get_wide_single_resource_liquid_op_offsets,
)
from pylabrobot.legacy.liquid_handling.liquid_classes.hamilton import (
  HamiltonLiquidClass,
  get_star_liquid_class,
)
from pylabrobot.resources import Resource, Tip, TipSpot, Well
from pylabrobot.resources.hamilton import HamiltonTip, TipDropMethod, TipPickupMethod, TipSize
from pylabrobot.resources.liquid import Liquid

from .errors import (
  STARFirmwareError,
  convert_star_firmware_error_to_plr_error,
)
from .pip_channel import PIPChannel

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
  if use_channels != sorted(use_channels):
    raise ValueError("Channels must be sorted.")

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
    result.append(
      get_star_liquid_class(
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

  def __init__(self, driver: STARDriver, traversal_height: float = 245.0):
    self.driver = driver
    self.traversal_height = traversal_height
    self.channels: List[PIPChannel] = []

  async def _on_setup(self):
    self.channels = [PIPChannel(self.driver, i) for i in range(self.num_channels)]

  @contextmanager
  def use_traversal_height(self, height: float):
    """Temporarily override the traversal height for all PIP operations."""
    original = self.traversal_height
    self.traversal_height = height
    try:
      yield
    finally:
      self.traversal_height = original

  @property
  def num_channels(self) -> int:
    return self.driver.num_channels

  def _ensure_can_reach_position(
    self,
    use_channels: List[int],
    ops: Sequence[Union[Pickup, TipDrop, Aspiration, Dispense]],
    op_name: str,
  ) -> None:
    """Validate that each channel can physically reach its target Y position."""
    if self.driver.extended_conf is None:
      return  # skip validation if config not available (e.g. chatterbox)
    ext = self.driver.extended_conf
    spacings = self.driver._channels_minimum_y_spacing
    if not spacings:
      spacings = [ext.min_raster_pitch_pip_channels] * self.num_channels

    cant_reach = []
    for channel_idx, op in zip(use_channels, ops):
      loc = op.resource.get_absolute_location(x="c", y="c", z="b") + op.offset
      min_y = ext.left_arm_min_y_position + sum(spacings[channel_idx + 1 :])
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

    await self.driver.ensure_iswap_parked()
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
    if not isinstance(ham_tip, HamiltonTip):
      raise TypeError(f"Expected HamiltonTip, got {type(ham_tip).__name__}")
    ttti = await self.driver.request_or_assign_tip_type_index(ham_tip)

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
      (backend_params.minimum_traverse_height_at_beginning_of_a_command or self.traversal_height)
      * 10
    )

    pickup_method = backend_params.pickup_method or ham_tip.pickup_method

    # Range validation (matches legacy pick_up_tip assertions).
    _assert_range(x_positions, 0, 25000, "x_positions")
    _assert_range(y_positions, 0, 6500, "y_positions")
    if not 0 <= begin_tip_pick_up_process <= 3600:
      raise ValueError("begin_tip_pick_up_process must be 0-3600")
    if not 0 <= end_tip_pick_up_process <= 3600:
      raise ValueError("end_tip_pick_up_process must be 0-3600")
    if not 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600:
      raise ValueError("minimum_traverse_height_at_beginning_of_a_command must be 0-3600")

    try:
      await self.driver.send_command(
        module="C0",
        command="TP",
        tip_pattern=channels_involved,
        read_timeout=max(120, self.driver.read_timeout),
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

    await self.driver.ensure_iswap_parked()
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
      (backend_params.minimum_traverse_height_at_beginning_of_a_command or self.traversal_height)
      * 10
    )
    z_position_at_end_of_a_command = round(
      (backend_params.z_position_at_end_of_a_command or self.traversal_height) * 10
    )

    # Range validation (matches legacy discard_tip assertions).
    _assert_range(x_positions, 0, 25000, "x_positions")
    _assert_range(y_positions, 0, 6500, "y_positions")
    if not 0 <= begin_tip_deposit_process <= 3600:
      raise ValueError("begin_tip_deposit_process must be 0-3600")
    if not 0 <= end_tip_deposit_process <= 3600:
      raise ValueError("end_tip_deposit_process must be 0-3600")
    if not 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600:
      raise ValueError("minimum_traverse_height_at_beginning_of_a_command must be 0-3600")
    if not 0 <= z_position_at_end_of_a_command <= 3600:
      raise ValueError("z_position_at_end_of_a_command must be 0-3600")

    try:
      await self.driver.send_command(
        module="C0",
        command="TR",
        tip_pattern=channels_involved,
        read_timeout=max(120, self.driver.read_timeout),
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

    await self.driver.ensure_iswap_parked()
    self._ensure_can_reach_position(use_channels, ops, "aspirate")

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    n = len(ops)

    # Resolve liquid classes (auto-detect from tip if not provided).
    hlcs = _resolve_liquid_classes(
      backend_params.hamilton_liquid_classes,
      ops,
      jet=backend_params.jet or False,
      blow_out=backend_params.blow_out or False,
      is_aspirate=True,
    )

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

    clot_detection_height = _fill(
      backend_params.clot_detection_height,
      [hlc.aspiration_clot_retract_height if hlc is not None else 0.0 for hlc in hlcs],
    )
    pull_out_distance_transport_air = _fill(
      backend_params.pull_out_distance_transport_air, [10.0] * n
    )
    second_section_height = _fill(backend_params.second_section_height, [3.2] * n)
    second_section_ratio = _fill(backend_params.second_section_ratio, [618.0] * n)
    minimum_height = _fill(backend_params.minimum_height, well_bottoms)

    immersion_depth_raw = backend_params.immersion_depth or [0.0] * n
    immersion_depth_direction = [0 if id_ >= 0 else 1 for id_ in immersion_depth_raw]
    immersion_depth = [abs(im) for im in immersion_depth_raw]

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

    transport_air_volume = _fill(
      backend_params.transport_air_volume,
      [hlc.aspiration_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs],
    )
    blow_out_air_volumes = [
      op.blow_out_air_volume or (hlc.aspiration_blow_out_volume if hlc is not None else 0.0)
      for op, hlc in zip(ops, hlcs)
    ]
    pre_wetting_volume = _fill(backend_params.pre_wetting_volume, [0.0] * n)
    lld_mode = _fill(backend_params.lld_mode, [LLDMode.OFF] * n)
    gamma_lld_sensitivity = _fill(backend_params.gamma_lld_sensitivity, [1] * n)
    dp_lld_sensitivity = _fill(backend_params.dp_lld_sensitivity, [1] * n)
    aspirate_position_above_z_touch_off = _fill(
      backend_params.aspirate_position_above_z_touch_off, [0.0] * n
    )
    detection_height_difference_for_dual_lld = _fill(
      backend_params.detection_height_difference_for_dual_lld, [0.0] * n
    )
    swap_speed = _fill(
      backend_params.swap_speed,
      [hlc.aspiration_swap_speed if hlc is not None else 100.0 for hlc in hlcs],
    )
    settling_time = _fill(
      backend_params.settling_time,
      [hlc.aspiration_settling_time if hlc is not None else 0.0 for hlc in hlcs],
    )

    # Mix.
    mix_volume = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_position_from_liquid_surface = _fill(
      backend_params.mix_position_from_liquid_surface, [0.0] * n
    )
    mix_speed = [op.mix.flow_rate if op.mix is not None else 100.0 for op in ops]
    mix_surface_following_distance = _fill(backend_params.mix_surface_following_distance, [0.0] * n)
    limit_curve_index = _fill(backend_params.limit_curve_index, [0] * n)

    # Probe liquid height if requested.
    traverse_height_override = backend_params.minimum_traverse_height_at_beginning_of_a_command
    if backend_params.probe_liquid_height:
      if any(op.liquid_height is not None for op in ops):
        raise ValueError("Cannot use probe_liquid_height when liquid heights are set.")
      liquid_heights = await self.driver.probe_liquid_heights(
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
      surface_following_distance = [liquid_heights[i] - liquid_height_after[i] for i in range(n)]

    liquid_surfaces_no_lld = backend_params.liquid_surface_no_lld or [
      wb + lh for wb, lh in zip(well_bottoms, liquid_heights)
    ]

    # Check surface following distance doesn't go below minimum height (when LLD is off).
    if any(
      (
        well_bottoms[i] + liquid_heights[i] - surface_following_distance[i] - minimum_height[i]
        < -1e-6
      )
      and lld_mode[i] == LLDMode.OFF
      for i in range(n)
    ):
      raise ValueError(
        f"surface_following_distance would result in a height below minimum_height. "
        f"Well bottom: {well_bottoms}, liquid height: {liquid_heights}, "
        f"surface_following_distance: {surface_following_distance}, minimum_height: {minimum_height}"
      )

    minimum_traverse_height_at_beginning_of_a_command = round(
      (traverse_height_override or self.traversal_height) * 10
    )
    min_z_endpos = round((backend_params.min_z_endpos or self.traversal_height) * 10)

    # Range validation (matches legacy aspirate_pip assertions, firmware units = real * 10).
    aspiration_types = _fill(backend_params.aspiration_type, [0] * n)
    _assert_range(aspiration_types, 0, 2, "aspiration_type")
    _assert_range(x_positions, 0, 25000, "x_positions")
    _assert_range(y_positions, 0, 6500, "y_positions")
    if not 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600:
      raise ValueError("minimum_traverse_height_at_beginning_of_a_command must be 0-3600")
    if not 0 <= min_z_endpos <= 3600:
      raise ValueError("min_z_endpos must be 0-3600")
    _assert_range([round(v * 10) for v in lld_search_height], 0, 3600, "lld_search_height")
    _assert_range([round(v * 10) for v in clot_detection_height], 0, 500, "clot_detection_height")
    _assert_range([round(v * 10) for v in liquid_surfaces_no_lld], 0, 3600, "liquid_surface_no_lld")
    _assert_range(
      [round(v * 10) for v in pull_out_distance_transport_air],
      0,
      3600,
      "pull_out_distance_transport_air",
    )
    _assert_range([round(v * 10) for v in second_section_height], 0, 3600, "second_section_height")
    _assert_range([round(v * 10) for v in second_section_ratio], 0, 10000, "second_section_ratio")
    _assert_range([round(v * 10) for v in minimum_height], 0, 3600, "minimum_height")
    _assert_range([round(v * 10) for v in immersion_depth], 0, 3600, "immersion_depth")
    _assert_range(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    _assert_range(
      [round(v * 10) for v in surface_following_distance], 0, 3600, "surface_following_distance"
    )
    _assert_range([round(v * 10) for v in volumes], 0, 12500, "aspiration_volumes")
    _assert_range([round(v * 10) for v in flow_rates], 4, 5000, "aspiration_speed")
    _assert_range([round(v * 10) for v in transport_air_volume], 0, 500, "transport_air_volume")
    _assert_range([round(v * 10) for v in blow_out_air_volumes], 0, 9999, "blow_out_air_volume")
    _assert_range([round(v * 10) for v in pre_wetting_volume], 0, 999, "pre_wetting_volume")
    _assert_range([m.value for m in lld_mode], 0, 4, "lld_mode")
    _assert_range(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    _assert_range(dp_lld_sensitivity, 1, 4, "dp_lld_sensitivity")
    _assert_range(
      [round(v * 10) for v in aspirate_position_above_z_touch_off],
      0,
      100,
      "aspirate_position_above_z_touch_off",
    )
    _assert_range(
      [round(v * 10) for v in detection_height_difference_for_dual_lld],
      0,
      99,
      "detection_height_difference_for_dual_lld",
    )
    _assert_range([round(v * 10) for v in swap_speed], 3, 1600, "swap_speed")
    _assert_range([round(v * 10) for v in settling_time], 0, 99, "settling_time")
    _assert_range([round(v * 10) for v in mix_volume], 0, 12500, "mix_volume")
    _assert_range(mix_cycles, 0, 99, "mix_cycles")
    _assert_range(
      [round(v * 10) for v in mix_position_from_liquid_surface],
      0,
      900,
      "mix_position_from_liquid_surface",
    )
    _assert_range([round(v * 10) for v in mix_speed], 4, 5000, "mix_speed")
    _assert_range(
      [round(v * 10) for v in mix_surface_following_distance],
      0,
      3600,
      "mix_surface_following_distance",
    )
    _assert_range(limit_curve_index, 0, 999, "limit_curve_index")
    if not 0 <= backend_params.recording_mode <= 2:
      raise ValueError("recording_mode must be between 0 and 2")
    # 2nd section aspiration range checks
    _assert_range(
      [
        round(v * 10)
        for v in _fill(backend_params.retract_height_over_2nd_section_to_empty_tip, [0.0] * n)
      ],
      0,
      3600,
      "retract_height_over_2nd_section_to_empty_tip",
    )
    _assert_range(
      [
        round(v * 10)
        for v in _fill(backend_params.dispensation_speed_during_emptying_tip, [50.0] * n)
      ],
      4,
      5000,
      "dispensation_speed_during_emptying_tip",
    )
    _assert_range(
      [
        round(v * 10)
        for v in _fill(backend_params.dosing_drive_speed_during_2nd_section_search, [50.0] * n)
      ],
      4,
      5000,
      "dosing_drive_speed_during_2nd_section_search",
    )
    _assert_range(
      [
        round(v * 10)
        for v in _fill(backend_params.z_drive_speed_during_2nd_section_search, [30.0] * n)
      ],
      3,
      1600,
      "z_drive_speed_during_2nd_section_search",
    )
    _assert_range(
      [round(v * 10) for v in _fill(backend_params.cup_upper_edge, [0.0] * n)],
      0,
      3600,
      "cup_upper_edge",
    )

    try:
      await self.driver.send_command(
        module="C0",
        command="AS",
        tip_pattern=channels_involved,
        read_timeout=max(300, self.driver.read_timeout),
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
        ik=[
          f"{round(x * 10):04}"
          for x in _fill(backend_params.retract_height_over_2nd_section_to_empty_tip, [0.0] * n)
        ],
        sd=[
          f"{round(x * 10):04}"
          for x in _fill(backend_params.dispensation_speed_during_emptying_tip, [50.0] * n)
        ],
        se=[
          f"{round(x * 10):04}"
          for x in _fill(backend_params.dosing_drive_speed_during_2nd_section_search, [50.0] * n)
        ],
        sz=[
          f"{round(x * 10):04}"
          for x in _fill(backend_params.z_drive_speed_during_2nd_section_search, [30.0] * n)
        ],
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

    await self.driver.ensure_iswap_parked()
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
      _dispensing_mode_for_op(empty=empty[i], jet=jet[i], blow_out=blow_out[i]) for i in range(n)
    ]

    # Resolve liquid classes.
    hlcs = _resolve_liquid_classes(
      backend_params.hamilton_liquid_classes, ops, jet=jet, blow_out=blow_out, is_aspirate=False
    )

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
      im * (-1 if immersion_depth_direction[i] else 1) for i, im in enumerate(immersion_depth_raw)
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
    stop_back_volume = _fill(
      backend_params.stop_back_volume,
      [hlc.dispense_stop_back_volume if hlc is not None else 0.0 for hlc in hlcs],
    )
    transport_air_volume = _fill(
      backend_params.transport_air_volume,
      [hlc.dispense_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs],
    )
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
    swap_speed = _fill(
      backend_params.swap_speed,
      [hlc.dispense_swap_speed if hlc is not None else 10.0 for hlc in hlcs],
    )
    settling_time = _fill(
      backend_params.settling_time,
      [hlc.dispense_settling_time if hlc is not None else 0.0 for hlc in hlcs],
    )

    # Mix.
    mix_volume = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    mix_position_from_liquid_surface = _fill(
      backend_params.mix_position_from_liquid_surface, [0.0] * n
    )
    mix_speed = [op.mix.flow_rate if op.mix is not None else 1.0 for op in ops]
    mix_surface_following_distance = _fill(backend_params.mix_surface_following_distance, [0.0] * n)
    limit_curve_index = _fill(backend_params.limit_curve_index, [0] * n)

    side_touch_off_distance = round(backend_params.side_touch_off_distance * 10)

    # Probe liquid height if requested.
    traverse_height_override = backend_params.minimum_traverse_height_at_beginning_of_a_command
    if backend_params.probe_liquid_height:
      if any(op.liquid_height is not None for op in ops):
        raise ValueError("Cannot use probe_liquid_height when liquid heights are set.")
      liquid_heights = await self.driver.probe_liquid_heights(
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
      surface_following_distance = [liquid_height_after[i] - liquid_heights[i] for i in range(n)]

    liquid_surfaces_no_lld = backend_params.liquid_surface_no_lld or [
      wb + lh for wb, lh in zip(well_bottoms, liquid_heights)
    ]

    minimum_traverse_height_at_beginning_of_a_command = round(
      (traverse_height_override or self.traversal_height) * 10
    )
    min_z_endpos = round((backend_params.min_z_endpos or self.traversal_height) * 10)

    # Range validation (matches legacy dispense_pip assertions, firmware units = real * 10).
    _assert_range(dispensing_modes, 0, 4, "dispensing_mode")
    _assert_range(x_positions, 0, 25000, "x_positions")
    _assert_range(y_positions, 0, 6500, "y_positions")
    _assert_range([round(v * 10) for v in minimum_height], 0, 3600, "minimum_height")
    _assert_range([round(v * 10) for v in lld_search_height], 0, 3600, "lld_search_height")
    _assert_range([round(v * 10) for v in liquid_surfaces_no_lld], 0, 3600, "liquid_surface_no_lld")
    _assert_range(
      [round(v * 10) for v in pull_out_distance_transport_air],
      0,
      3600,
      "pull_out_distance_transport_air",
    )
    _assert_range([round(v * 10) for v in immersion_depth], 0, 3600, "immersion_depth")
    _assert_range(immersion_depth_direction, 0, 1, "immersion_depth_direction")
    _assert_range(
      [round(v * 10) for v in surface_following_distance], 0, 3600, "surface_following_distance"
    )
    _assert_range([round(v * 10) for v in second_section_height], 0, 3600, "second_section_height")
    _assert_range([round(v * 10) for v in second_section_ratio], 0, 10000, "second_section_ratio")
    if not 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600:
      raise ValueError("minimum_traverse_height_at_beginning_of_a_command must be 0-3600")
    if not 0 <= min_z_endpos <= 3600:
      raise ValueError("min_z_endpos must be 0-3600")
    _assert_range([round(v * 10) for v in volumes], 0, 12500, "dispense_volumes")
    _assert_range([round(v * 10) for v in flow_rates], 4, 5000, "dispense_speed")
    _assert_range([round(v * 10) for v in cut_off_speed], 4, 5000, "cut_off_speed")
    _assert_range([round(v * 10) for v in stop_back_volume], 0, 180, "stop_back_volume")
    _assert_range([round(v * 10) for v in transport_air_volume], 0, 500, "transport_air_volume")
    _assert_range([round(v * 10) for v in blow_out_air_volumes], 0, 9999, "blow_out_air_volume")
    _assert_range([m.value for m in lld_mode], 0, 4, "lld_mode")
    if not 0 <= side_touch_off_distance <= 45:
      raise ValueError("side_touch_off_distance must be between 0 and 45")
    _assert_range(
      [round(v * 10) for v in dispense_position_above_z_touch_off],
      0,
      100,
      "dispense_position_above_z_touch_off",
    )
    _assert_range(gamma_lld_sensitivity, 1, 4, "gamma_lld_sensitivity")
    _assert_range(dp_lld_sensitivity, 1, 4, "dp_lld_sensitivity")
    _assert_range([round(v * 10) for v in swap_speed], 3, 1600, "swap_speed")
    _assert_range([round(v * 10) for v in settling_time], 0, 99, "settling_time")
    _assert_range([round(v * 10) for v in mix_volume], 0, 12500, "mix_volume")
    _assert_range(mix_cycles, 0, 99, "mix_cycles")
    _assert_range(
      [round(v * 10) for v in mix_position_from_liquid_surface],
      0,
      900,
      "mix_position_from_liquid_surface",
    )
    _assert_range([round(v * 10) for v in mix_speed], 4, 5000, "mix_speed")
    _assert_range(
      [round(v * 10) for v in mix_surface_following_distance],
      0,
      3600,
      "mix_surface_following_distance",
    )
    _assert_range(limit_curve_index, 0, 999, "limit_curve_index")
    if not 0 <= backend_params.recording_mode <= 2:
      raise ValueError("recording_mode must be between 0 and 2")

    try:
      await self.driver.send_command(
        module="C0",
        command="DS",
        tip_pattern=channels_involved,
        read_timeout=max(300, self.driver.read_timeout),
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

  # -- multi-channel PIP operations ------------------------------------------

  async def spread_pip_channels(self):
    """Spread PIP channels (C0:JE)."""
    return await self.driver.send_command(module="C0", command="JE")

  async def move_all_channels_in_z_safety(self):
    """Move all pipetting channels to Z-safety position (C0:ZA)."""
    return await self.driver.send_command(module="C0", command="ZA")

  async def position_max_free_y_for_n(self, pipetting_channel_index: int):
    """Position all pipetting channels so that there is maximum free Y range for channel n (C0:JP).

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 0 and num_channels - 1.
    """
    if self.driver.iswap is not None and not self.driver.iswap.parked:
      await self.driver.iswap.park()

    if not 0 <= pipetting_channel_index < self.num_channels:
      raise ValueError("pipetting_channel_index must be between 0 and num_channels - 1")
    # convert Python's 0-based indexing to Hamilton firmware's 1-based indexing
    pipetting_channel_index_fw = pipetting_channel_index + 1

    return await self.driver.send_command(
      module="C0",
      command="JP",
      pn=f"{pipetting_channel_index_fw:02}",
    )

  async def move_all_pipetting_channels_to_defined_position(
    self,
    tip_pattern: bool = True,
    x_positions: float = 0.0,
    y_positions: float = 0.0,
    minimum_traverse_height_at_beginning_of_command: float = 360.0,
    z_endpos: float = 0.0,
  ):
    """Move all pipetting channels to defined position (C0:JM).

    Args:
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [mm]. Must be between 0 and 2500. Default 0.
      y_positions: y positions [mm]. Must be between 0 and 650. Default 0.
      minimum_traverse_height_at_beginning_of_command: Minimum traverse height at beginning of a
        command [mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 360. Default 360.
      z_endpos: Z-Position at end of a command [mm] (refers to all channels independent of tip
        pattern parameter 'tm'). Must be between 0 and 360. Default 0.
    """

    if self.driver.iswap is not None and not self.driver.iswap.parked:
      await self.driver.iswap.park()

    if not 0 <= x_positions <= 2500:
      raise ValueError("x_positions must be between 0 and 2500")
    if not 0 <= y_positions <= 650:
      raise ValueError("y_positions must be between 0 and 650")
    if not 0 <= minimum_traverse_height_at_beginning_of_command <= 360:
      raise ValueError("minimum_traverse_height_at_beginning_of_command must be between 0 and 360")
    if not 0 <= z_endpos <= 360:
      raise ValueError("z_endpos must be between 0 and 360")

    return await self.driver.send_command(
      module="C0",
      command="JM",
      tm=tip_pattern,
      xp=round(x_positions * 10),
      yp=round(y_positions * 10),
      th=round(minimum_traverse_height_at_beginning_of_command * 10),
      zp=round(z_endpos * 10),
    )

  async def get_channels_y_positions(self) -> Dict[int, float]:
    """Get the Y position of all channels in mm (C0:RY)."""
    resp = await self.driver.send_command(
      module="C0",
      command="RY",
      fmt="ry#### (n)",
    )
    y_positions = [round(y / 10, 2) for y in resp["ry"]]

    # sometimes there is (likely) a floating point error and channels are reported to be
    # less than their minimum spacing apart (typically 9 mm). (When you set channels using
    # position_channels_in_y_direction, it will raise an error.) The minimum y is 6mm,
    # so we fix that first (in case that value is misreported). Then, we traverse the
    # list in reverse and enforce pairwise minimum spacing.
    if self.driver.extended_conf is not None:
      min_y = self.driver.extended_conf.left_arm_min_y_position
    else:
      min_y = 6.0

    if y_positions[-1] < min_y - 0.2:
      raise RuntimeError(
        "Channels are reported to be too close to the front of the machine. "
        f"The known minimum is {min_y}, which will be fixed automatically for "
        f"{min_y - 0.2}<y<{min_y}. "
        f"Reported values: {y_positions}."
      )
    elif min_y - 0.2 <= y_positions[-1] < min_y:
      y_positions[-1] = min_y

    for i in range(len(y_positions) - 2, -1, -1):
      spacing = self.driver._min_spacing_between(i, i + 1)
      if y_positions[i] - y_positions[i + 1] < spacing:
        y_positions[i] = y_positions[i + 1] + spacing

    return {channel_idx: y for channel_idx, y in enumerate(y_positions)}

  async def position_channels_in_y_direction(self, ys: Dict[int, float], make_space: bool = True):
    """Position all channels simultaneously in the Y direction (C0:JY).

    Args:
      ys: A dictionary mapping channel index to the desired Y position in mm. The channel index is
        0-indexed from the back.
      make_space: If True, the channels will be moved to ensure they respect each channel pair's
        minimum Y spacing and are in descending order, after the channels in ``ys`` have been put
        at the desired locations. Note that an error may still be raised, if there is insufficient
        space to move the channels or if the requested locations are not valid. Set this to False
        if you want to avoid inadvertently moving other channels.
    """

    if self.driver.iswap is not None and not self.driver.iswap.parked:
      await self.driver.iswap.park()

    # check that the locations of channels after the move will respect pairwise minimum
    # spacing and be in descending order
    channel_locations = await self.get_channels_y_positions()

    for channel_idx, y in ys.items():
      channel_locations[channel_idx] = y

    if make_space:
      use_channels = list(ys.keys())
      back_channel = min(use_channels)
      front_channel = max(use_channels)

      # Position channels in between used channels
      for intermediate_ch in range(back_channel + 1, front_channel):
        if intermediate_ch not in ys:
          channel_locations[intermediate_ch] = channel_locations[
            intermediate_ch - 1
          ] - self.driver._min_spacing_between(intermediate_ch - 1, intermediate_ch)

      # For the channels to the back of `back_channel`, make sure the space between them is
      # >=min_spacing. We start with the channel closest to `back_channel`, and make sure the
      # channel behind it is at least min_spacing away, updating if needed.
      for channel_idx in range(back_channel, 0, -1):
        spacing = self.driver._min_spacing_between(channel_idx - 1, channel_idx)
        if (channel_locations[channel_idx - 1] - channel_locations[channel_idx]) < spacing:
          channel_locations[channel_idx - 1] = channel_locations[channel_idx] + spacing

      # Similarly for the channels to the front of `front_channel`, make sure they are all
      # spaced >= min_spacing apart.
      for channel_idx in range(front_channel, self.driver.num_channels - 1):
        spacing = self.driver._min_spacing_between(channel_idx, channel_idx + 1)
        if (channel_locations[channel_idx] - channel_locations[channel_idx + 1]) < spacing:
          channel_locations[channel_idx + 1] = channel_locations[channel_idx] - spacing

    # Quick checks before movement.
    if channel_locations[0] > 650:
      raise ValueError("Channel 0 would hit the back of the robot")

    if channel_locations[self.driver.num_channels - 1] < 6:
      raise ValueError("Channel N would hit the front of the robot")

    for i in range(len(channel_locations) - 1):
      required = self.driver._min_spacing_between(i, i + 1)
      actual = channel_locations[i] - channel_locations[i + 1]
      if round(actual * 1000) < round(required * 1000):  # compare in um to avoid float issues
        raise ValueError(
          f"Channels {i} and {i + 1} must be at least {required}mm apart, "
          f"but are {actual:.2f}mm apart."
        )

    yp = " ".join([f"{round(y * 10):04}" for y in channel_locations.values()])
    return await self.driver.send_command(
      module="C0",
      command="JY",
      yp=yp,
    )

  async def get_channels_z_positions(self) -> Dict[int, float]:
    """Get the Z position of all channels in mm (C0:RZ)."""
    resp = await self.driver.send_command(
      module="C0",
      command="RZ",
      fmt="rz#### (n)",
    )
    return {channel_idx: round(z / 10, 2) for channel_idx, z in enumerate(resp["rz"])}

  async def position_channels_in_z_direction(self, zs: Dict[int, float]):
    """Position channels in the Z direction (C0:JZ).

    Args:
      zs: A dictionary mapping channel index to the desired Z position in mm.
    """
    channel_locations = await self.get_channels_z_positions()

    for channel_idx, z in zs.items():
      channel_locations[channel_idx] = z

    return await self.driver.send_command(
      module="C0",
      command="JZ",
      zp=[f"{round(z * 10):04}" for z in channel_locations.values()],
    )

  async def initialize_pip(self):
    """Wrapper around initialize_pipetting_channels firmware command.

    Computes Y positions and calls initialize_pipetting_channels with default parameters.
    """
    dy_01mm = (4050 - 2175) // (self.num_channels - 1)  # integer division in 0.1mm, matching legacy
    y_positions = [round((4050 - i * dy_01mm) / 10, 1) for i in range(self.num_channels)]

    tip_waste_x = 0.0
    if self.driver.extended_conf is not None:
      tip_waste_x = self.driver.extended_conf.tip_waste_x_position

    await self.initialize_pipetting_channels(
      x_positions=[tip_waste_x],
      y_positions=y_positions,
      begin_of_tip_deposit_process=self.traversal_height,
      end_of_tip_deposit_process=122.0,
      z_position_at_end_of_a_command=360.0,
      tip_pattern=[True] * self.num_channels,
      tip_type=4,
      discarding_method=0,
    )

  async def initialize_pipetting_channels(
    self,
    x_positions: Optional[List[float]] = None,
    y_positions: Optional[List[float]] = None,
    begin_of_tip_deposit_process: float = 0.0,
    end_of_tip_deposit_process: float = 0.0,
    z_position_at_end_of_a_command: float = 360.0,
    tip_pattern: Optional[List[bool]] = None,
    tip_type: int = 16,
    discarding_method: int = 1,
  ):
    """Initialize pipetting channels (discard tips) (C0:DI).

    Args:
      x_positions: X-Position [mm] (discard position). Must be between 0 and 2500. Default [0].
      y_positions: y-Position [mm] (discard position). Must be between 0 and 650. Default [0].
      begin_of_tip_deposit_process: Begin of tip deposit process (Z-discard range) [mm]. Must be
        between 0 and 360. Default 0.
      end_of_tip_deposit_process: End of tip deposit process (Z-discard range) [mm]. Must be
        between 0 and 360. Default 0.
      z_position_at_end_of_a_command: Z-Position at end of a command [mm]. Must be between 0 and
        360. Default 360.
      tip_pattern: Tip pattern (channels involved). Default [True].
      tip_type: Tip type (recommended is index of longest tip see command 'TT'). Must be
        between 0 and 99. Default 16.
      discarding_method: discarding method. 0 = place & shift (tp/ tz = tip cone end height), 1 =
        drop (no shift) (tp/ tz = stop disk height). Must be between 0 and 1. Default 1.
    """

    if x_positions is None:
      x_positions = [0.0]
    if y_positions is None:
      y_positions = [0.0]
    if tip_pattern is None:
      tip_pattern = [True]

    # Convert mm to 0.1mm for firmware
    x_positions_fw = [round(x * 10) for x in x_positions]
    y_positions_fw = [round(y * 10) for y in y_positions]
    begin_fw = round(begin_of_tip_deposit_process * 10)
    end_fw = round(end_of_tip_deposit_process * 10)
    z_end_fw = round(z_position_at_end_of_a_command * 10)

    if not all(0 <= xp <= 25000 for xp in x_positions_fw):
      raise ValueError("x_positions must be between 0 and 2500 mm")
    if not all(0 <= yp <= 6500 for yp in y_positions_fw):
      raise ValueError("y_positions must be between 0 and 650 mm")
    if not 0 <= begin_fw <= 3600:
      raise ValueError("begin_of_tip_deposit_process must be between 0 and 360 mm")
    if not 0 <= end_fw <= 3600:
      raise ValueError("end_of_tip_deposit_process must be between 0 and 360 mm")
    if not 0 <= z_end_fw <= 3600:
      raise ValueError("z_position_at_end_of_a_command must be between 0 and 360 mm")
    if not 0 <= tip_type <= 99:
      raise ValueError("tip_type must be between 0 and 99")
    if not 0 <= discarding_method <= 1:
      raise ValueError("discarding_method must be between 0 and 1")

    return await self.driver.send_command(
      module="C0",
      command="DI",
      read_timeout=120,
      xp=[f"{xp:05}" for xp in x_positions_fw],
      yp=[f"{yp:04}" for yp in y_positions_fw],
      tp=f"{begin_fw:04}",
      tz=f"{end_fw:04}",
      te=f"{z_end_fw:04}",
      tm=[f"{tm:01}" for tm in tip_pattern],
      tt=f"{tip_type:02}",
      ti=discarding_method,
    )

  # -- single-channel movement ------------------------------------------------

  async def move_channel_z(self, channel: int, z: float):
    """Move a single channel in the Z direction (mm).

    Args:
      channel: 0-indexed channel index.
      z: Target Z position in mm.
    """
    if not 0 <= channel < self.driver.num_channels:
      raise ValueError(f"channel must be between 0 and {self.driver.num_channels - 1}")
    if not 0 <= z <= 334.7:
      raise ValueError("z must be between 0 and 334.7 mm")

    return await self.driver.send_command(
      module="C0",
      command="KZ",
      pn=f"{channel + 1:02}",
      zj=f"{round(z * 10):04}",
    )

  # -- foil piercing ----------------------------------------------------------

  def _get_maximum_minimum_spacing_between_channels(self, use_channels: List[int]) -> float:
    """Get the maximum of the set of minimum spacing requirements between the channels being used."""
    sorted_channels = sorted(use_channels)
    return max(
      self.driver._min_spacing_between(hi, lo)
      for hi, lo in zip(sorted_channels[1:], sorted_channels[:-1])
    )

  async def pierce_foil(
    self,
    wells: Union[Well, List[Well]],
    piercing_channels: List[int],
    hold_down_channels: List[int],
    move_inwards: float,
    deck: Resource,
    spread: Literal["wide", "tight"] = "wide",
    one_by_one: bool = False,
    distance_from_bottom: float = 20.0,
  ):
    """Pierce the foil of the media source plate at the specified column. Throw away the tips
    after piercing because there will be a bit of foil stuck to the tips. Use this method
    before aspirating from a foil-sealed plate to make sure the tips are clean and the
    aspirations are accurate.

    Args:
      wells: Well or wells in the plate to pierce the foil. If multiple wells, they must be on one
        column.
      piercing_channels: The channels to use for piercing the foil.
      hold_down_channels: The channels to use for holding down the plate when moving up the
        piercing channels.
      move_inwards: mm to move inwards when stepping off the foil.
      deck: The deck resource, used to compute absolute positions of wells.
      spread: The spread of the piercing channels in the well.
      one_by_one: If True, the channels will pierce the foil one by one. If False, all channels
        will pierce the foil simultaneously.
      distance_from_bottom: mm above the cavity bottom to position the piercing channels.
    """

    x: float
    ys: List[float]
    z: float

    # if only one well is given, but in a list, convert to Well so we fall into single-well logic.
    if isinstance(wells, list) and len(wells) == 1:
      wells = wells[0]

    if isinstance(wells, Well):
      well = wells
      x, y, z = well.get_location_wrt(deck, "c", "c", "cavity_bottom")

      if spread == "wide":
        offsets = get_wide_single_resource_liquid_op_offsets(
          resource=well,
          num_channels=len(piercing_channels),
          min_spacing=self._get_maximum_minimum_spacing_between_channels(piercing_channels),
        )
      else:
        offsets = get_tight_single_resource_liquid_op_offsets(
          well, num_channels=len(piercing_channels)
        )
      ys = [y + offset.y for offset in offsets]
    else:
      if len(set(w.get_location_wrt(deck).x for w in wells)) != 1:
        raise ValueError("Wells must be on the same column")
      absolute_center = wells[0].get_location_wrt(deck, "c", "c", "cavity_bottom")
      x = absolute_center.x
      ys = [well.get_location_wrt(deck, x="c", y="c").y for well in wells]
      z = absolute_center.z

    await self.driver.left_x_arm.move_to(x)

    await self.position_channels_in_y_direction(
      {channel: y for channel, y in zip(piercing_channels, ys)}
    )

    zs = [z + distance_from_bottom for _ in range(len(piercing_channels))]
    if one_by_one:
      for channel in piercing_channels:
        await self.move_channel_z(channel, z + distance_from_bottom)
    else:
      await self.position_channels_in_z_direction(
        {channel: z for channel, z in zip(piercing_channels, zs)}
      )

    await self.step_off_foil(
      [wells] if isinstance(wells, Well) else wells,
      back_channel=hold_down_channels[0],
      front_channel=hold_down_channels[1],
      move_inwards=move_inwards,
      deck=deck,
    )

  async def step_off_foil(
    self,
    wells: Union[Well, List[Well]],
    front_channel: int,
    back_channel: int,
    deck: Resource,
    move_inwards: float = 2,
    move_height: float = 15,
  ):
    """Hold down a plate by placing two channels on the edges of a plate that is sealed with foil
    while moving up the channels that are still within the foil. This is useful when, for
    example, aspirating from a plate that is sealed: without holding it down, the tips might get
    stuck in the plate and move it up when retracting. Putting plates on the edge prevents this.

    Args:
      wells: Wells in the plate to hold down. (x-coordinate of channels will be at center of wells).
        Must be sorted from back to front.
      front_channel: The channel to place on the front of the plate.
      back_channel: The channel to place on the back of the plate.
      deck: The deck resource, used to compute absolute positions of wells and plates.
      move_inwards: mm to move inwards (backward on the front channel; frontward on the back).
      move_height: mm to move upwards after piercing the foil. front_channel and back_channel will
        hold the plate down.
    """

    if front_channel <= back_channel:
      raise ValueError(
        "front_channel should be in front of back_channel. Channels are 0-indexed from the back."
      )

    if isinstance(wells, Well):
      wells = [wells]

    plates = set(well.parent for well in wells)
    if len(plates) != 1:
      raise ValueError("All wells must be in the same plate")
    plate = plates.pop()
    if plate is None:
      raise ValueError("Wells must have a parent plate")

    z_location = plate.get_location_wrt(deck, z="top").z

    if plate.get_absolute_rotation().z % 360 == 0:
      back_location = plate.get_location_wrt(deck, y="b")
      front_location = plate.get_location_wrt(deck, y="f")
    elif plate.get_absolute_rotation().z % 360 == 90:
      back_location = plate.get_location_wrt(deck, x="r")
      front_location = plate.get_location_wrt(deck, x="l")
    elif plate.get_absolute_rotation().z % 360 == 180:
      back_location = plate.get_location_wrt(deck, y="f")
      front_location = plate.get_location_wrt(deck, y="b")
    elif plate.get_absolute_rotation().z % 360 == 270:
      back_location = plate.get_location_wrt(deck, x="l")
      front_location = plate.get_location_wrt(deck, x="r")
    else:
      raise ValueError("Plate rotation must be a multiple of 90 degrees")

    try:
      # Then move all channels in the y-space simultaneously.
      await self.position_channels_in_y_direction(
        {
          front_channel: front_location.y + move_inwards,
          back_channel: back_location.y - move_inwards,
        }
      )

      await self.move_channel_z(front_channel, z_location)
      await self.move_channel_z(back_channel, z_location)
    finally:
      # Move channels that are lower than the `front_channel` and `back_channel` to
      # the just above the foil, in case the foil pops up.
      zs = await self.get_channels_z_positions()
      indices = [channel_idx for channel_idx, z in zs.items() if z < z_location]
      idx = {
        idx: z_location + move_height for idx in indices if idx not in (front_channel, back_channel)
      }
      await self.position_channels_in_z_direction(idx)

      # After that, all channels are clear to move up.
      await self.move_all_channels_in_z_safety()
