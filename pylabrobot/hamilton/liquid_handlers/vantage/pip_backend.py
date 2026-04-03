"""Vantage PIP backend: translates PIP operations into Vantage firmware commands."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple, Union, cast

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.hamilton.lh.vantage.liquid_classes import get_vantage_liquid_class
from pylabrobot.hamilton.liquid_handlers.liquid_class import HamiltonLiquidClass
from pylabrobot.resources import Resource, Well
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
  """Liquid level detection mode."""

  OFF = 0
  GAMMA = 1
  PRESSURE = 2
  DUAL = 3
  Z_TOUCH_OFF = 4


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _get_dispense_mode(jet: bool, empty: bool, blow_out: bool) -> int:
  """Compute firmware dispensing mode from boolean flags.

  Firmware modes:
    0 = Partial volume in jet mode
    1 = Blow out in jet mode (labelled "empty" in VENUS)
    2 = Partial volume at surface
    3 = Blow out at surface (labelled "empty" in VENUS)
    4 = Empty tip at fix position
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
  """Convert ops + use_channels into firmware x/y positions and tip pattern.

  Uses absolute coordinates so the driver does not need a ``deck`` reference.
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
  """Resolve per-op Hamilton liquid classes. Auto-detect from tip if explicit is None."""
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
  """Translates PIP operations into Vantage firmware commands via the driver."""

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver

  async def _on_setup(self):
    pass

  async def _on_stop(self):
    pass

  @property
  def num_channels(self) -> int:
    return self.driver.num_channels

  # -- BackendParams dataclasses ---------------------------------------------

  @dataclass
  class PickUpTipsParams(BackendParams):
    """Vantage-specific parameters for ``pick_up_tips``."""

    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None

  @dataclass
  class DropTipsParams(BackendParams):
    """Vantage-specific parameters for ``drop_tips``."""

    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None

  @dataclass
  class AspirateParams(BackendParams):
    """Vantage-specific parameters for ``aspirate``."""

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
    """Vantage-specific parameters for ``dispense``."""

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
        begin_z_deposit_position=[round((max_z + max_total_tip_length) * 10)] * len(ops),
        end_z_deposit_position=[round((max_z + max_tip_length) * 10)] * len(ops),
        minimal_traverse_height_at_begin_of_command=[round(t * 10) for t in mth or [th]] * len(ops),
        minimal_height_at_command_end=[round(t * 10) for t in mhe or [th]] * len(ops),
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
        begin_z_deposit_position=[round((max_z + 10) * 10)] * len(ops),
        end_z_deposit_position=[round(max_z * 10)] * len(ops),
        minimal_traverse_height_at_begin_of_command=[round(t * 10) for t in mth or [th]] * len(ops),
        minimal_height_at_command_end=[round(t * 10) for t in mhe or [th]] * len(ops),
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
        minimal_traverse_height_at_begin_of_command=[round(t * 10) for t in mth or [th]] * len(ops),
        minimal_height_at_command_end=[round(t * 10) for t in mhe or [th]] * len(ops),
        lld_search_height=[round(ls * 10) for ls in lld_search_heights],
        clot_detection_height=[
          round(cdh * 10) for cdh in backend_params.clot_detection_height or [0] * len(ops)
        ],
        liquid_surface_at_function_without_lld=[round(lsn * 10) for lsn in liquid_surfaces_no_lld],
        pull_out_distance_to_take_transport_air_in_function_without_lld=[
          round(pod * 10)
          for pod in backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
          or [10.9] * len(ops)
        ],
        tube_2nd_section_height_measured_from_zm=[
          round(t * 10)
          for t in backend_params.tube_2nd_section_height_measured_from_zm or [0] * len(ops)
        ],
        tube_2nd_section_ratio=[
          round(t * 10) for t in backend_params.tube_2nd_section_ratio or [0] * len(ops)
        ],
        minimum_height=[round(wb * 10) for wb in backend_params.minimum_height or well_bottoms],
        immersion_depth=[round(d * 10) for d in backend_params.immersion_depth or [0] * len(ops)],
        surface_following_distance=[
          round(d * 10) for d in backend_params.surface_following_distance or [0] * len(ops)
        ],
        aspiration_volume=[round(vol * 100) for vol in volumes],
        aspiration_speed=[round(fr * 10) for fr in flow_rates],
        transport_air_volume=[
          round(tav * 10)
          for tav in backend_params.transport_air_volume
          or [hlc.aspiration_air_transport_volume if hlc is not None else 0 for hlc in hlcs]
        ],
        blow_out_air_volume=[round(bav * 100) for bav in blow_out_air_volumes],
        pre_wetting_volume=[
          round(pwv * 100) for pwv in backend_params.pre_wetting_volume or [0] * len(ops)
        ],
        lld_mode=backend_params.lld_mode or [0] * len(ops),
        lld_sensitivity=backend_params.lld_sensitivity or [4] * len(ops),
        pressure_lld_sensitivity=backend_params.pressure_lld_sensitivity or [4] * len(ops),
        aspirate_position_above_z_touch_off=[
          round(apz * 10)
          for apz in backend_params.aspirate_position_above_z_touch_off or [0.5] * len(ops)
        ],
        swap_speed=[round(ss * 10) for ss in backend_params.swap_speed or [2] * len(ops)],
        settling_time=[round(st * 10) for st in backend_params.settling_time or [1] * len(ops)],
        mix_volume=[round(op.mix.volume * 100) if op.mix is not None else 0 for op in ops],
        mix_cycles=[op.mix.repetitions if op.mix is not None else 0 for op in ops],
        mix_position_in_z_direction_from_liquid_surface=[0] * len(ops),
        mix_speed=[round(op.mix.flow_rate * 10) if op.mix is not None else 2500 for op in ops],
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
        minimum_height=[round(wb * 10) for wb in backend_params.minimum_height or well_bottoms],
        lld_search_height=[round(sh * 10) for sh in lld_search_heights],
        liquid_surface_at_function_without_lld=[round(ls * 10) for ls in liquid_surfaces_no_lld],
        pull_out_distance_to_take_transport_air_in_function_without_lld=[
          round(pod * 10)
          for pod in backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
          or [5.0] * len(ops)
        ],
        immersion_depth=[round(d * 10) for d in backend_params.immersion_depth or [0] * len(ops)],
        surface_following_distance=[
          round(d * 10) for d in backend_params.surface_following_distance or [2.1] * len(ops)
        ],
        tube_2nd_section_height_measured_from_zm=[
          round(t * 10)
          for t in backend_params.tube_2nd_section_height_measured_from_zm or [0] * len(ops)
        ],
        tube_2nd_section_ratio=[
          round(t * 10) for t in backend_params.tube_2nd_section_ratio or [0] * len(ops)
        ],
        minimal_traverse_height_at_begin_of_command=[round(t * 10) for t in mth or [th]] * len(ops),
        minimal_height_at_command_end=[round(t * 10) for t in mhe or [th]] * len(ops),
        dispense_volume=[round(vol * 100) for vol in volumes],
        dispense_speed=[round(fr * 10) for fr in flow_rates],
        cut_off_speed=[round(cs * 10) for cs in backend_params.cut_off_speed or [250] * len(ops)],
        stop_back_volume=[
          round(sbv * 100) for sbv in backend_params.stop_back_volume or [0] * len(ops)
        ],
        transport_air_volume=[
          round(tav * 10)
          for tav in backend_params.transport_air_volume
          or [hlc.dispense_air_transport_volume if hlc is not None else 0 for hlc in hlcs]
        ],
        blow_out_air_volume=[round(boav * 100) for boav in blow_out_air_volumes],
        lld_mode=backend_params.lld_mode or [0] * len(ops),
        side_touch_off_distance=round(backend_params.side_touch_off_distance * 10),
        dispense_position_above_z_touch_off=[
          round(dpz * 10)
          for dpz in backend_params.dispense_position_above_z_touch_off or [0.5] * len(ops)
        ],
        lld_sensitivity=backend_params.lld_sensitivity or [1] * len(ops),
        pressure_lld_sensitivity=backend_params.pressure_lld_sensitivity or [1] * len(ops),
        swap_speed=[round(ss * 10) for ss in backend_params.swap_speed or [1] * len(ops)],
        settling_time=[round(st * 10) for st in backend_params.settling_time or [0] * len(ops)],
        mix_volume=[round(op.mix.volume * 100) if op.mix is not None else 0 for op in ops],
        mix_cycles=[op.mix.repetitions if op.mix is not None else 0 for op in ops],
        mix_position_in_z_direction_from_liquid_surface=[0] * len(ops),
        mix_speed=[round(op.mix.flow_rate * 100) if op.mix is not None else 10 for op in ops],
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
    presences = await self.driver.query_tip_presence()
    return [bool(p) for p in presences]

  # -- firmware commands (A1PM) ----------------------------------------------

  async def _pip_tip_pick_up(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: List[bool],
    tip_type: List[int],
    begin_z_deposit_position: List[int],
    end_z_deposit_position: List[int],
    minimal_traverse_height_at_begin_of_command: List[int],
    minimal_height_at_command_end: List[int],
    tip_handling_method: List[int],
    blow_out_air_volume: List[int],
  ):
    """Tip pick up (A1PM:TP)."""
    await self.driver.send_command(
      module="A1PM",
      command="TP",
      tip_pattern=tip_pattern,
      xp=x_position,
      yp=y_position,
      tm=tip_pattern,
      tt=tip_type,
      tp=begin_z_deposit_position,
      tz=end_z_deposit_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      ba=blow_out_air_volume,
      td=tip_handling_method,
    )

  async def _pip_tip_discard(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: List[bool],
    begin_z_deposit_position: List[int],
    end_z_deposit_position: List[int],
    minimal_traverse_height_at_begin_of_command: List[int],
    minimal_height_at_command_end: List[int],
    tip_handling_method: List[int],
    ts: int = 0,
  ):
    """Tip discard (A1PM:TR)."""
    await self.driver.send_command(
      module="A1PM",
      command="TR",
      tip_pattern=tip_pattern,
      xp=x_position,
      yp=y_position,
      tp=begin_z_deposit_position,
      tz=end_z_deposit_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      tm=tip_pattern,
      ts=ts,
      td=tip_handling_method,
    )

  async def _pip_aspirate(
    self,
    x_position: List[int],
    y_position: List[int],
    type_of_aspiration: List[int],
    tip_pattern: List[bool],
    minimal_traverse_height_at_begin_of_command: List[int],
    minimal_height_at_command_end: List[int],
    lld_search_height: List[int],
    clot_detection_height: List[int],
    liquid_surface_at_function_without_lld: List[int],
    pull_out_distance_to_take_transport_air_in_function_without_lld: List[int],
    tube_2nd_section_height_measured_from_zm: List[int],
    tube_2nd_section_ratio: List[int],
    minimum_height: List[int],
    immersion_depth: List[int],
    surface_following_distance: List[int],
    aspiration_volume: List[int],
    aspiration_speed: List[int],
    transport_air_volume: List[int],
    blow_out_air_volume: List[int],
    pre_wetting_volume: List[int],
    lld_mode: List[int],
    lld_sensitivity: List[int],
    pressure_lld_sensitivity: List[int],
    aspirate_position_above_z_touch_off: List[int],
    swap_speed: List[int],
    settling_time: List[int],
    mix_volume: List[int],
    mix_cycles: List[int],
    mix_position_in_z_direction_from_liquid_surface: List[int],
    mix_speed: List[int],
    surface_following_distance_during_mixing: List[int],
    capacitive_mad_supervision_on_off: List[int],
    pressure_mad_supervision_on_off: List[int],
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
  ):
    """Aspiration of liquid (A1PM:DA)."""
    await self.driver.send_command(
      module="A1PM",
      command="DA",
      tip_pattern=tip_pattern,
      at=type_of_aspiration,
      tm=tip_pattern,
      xp=x_position,
      yp=y_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      lp=lld_search_height,
      ch=clot_detection_height,
      zl=liquid_surface_at_function_without_lld,
      po=pull_out_distance_to_take_transport_air_in_function_without_lld,
      zu=tube_2nd_section_height_measured_from_zm,
      zr=tube_2nd_section_ratio,
      zx=minimum_height,
      ip=immersion_depth,
      fp=surface_following_distance,
      av=aspiration_volume,
      as_=aspiration_speed,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      oa=pre_wetting_volume,
      lm=lld_mode,
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      zo=aspirate_position_above_z_touch_off,
      de=swap_speed,
      wt=settling_time,
      mv=mix_volume,
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      ms=mix_speed,
      mh=surface_following_distance_during_mixing,
      la=[0] * len(x_position),
      lb=capacitive_mad_supervision_on_off,
      lc=pressure_mad_supervision_on_off,
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index or [0] * len(x_position),
      gk=recording_mode,
    )

  async def _pip_dispense(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: List[bool],
    type_of_dispensing_mode: List[int],
    minimum_height: List[int],
    lld_search_height: List[int],
    liquid_surface_at_function_without_lld: List[int],
    pull_out_distance_to_take_transport_air_in_function_without_lld: List[int],
    immersion_depth: List[int],
    surface_following_distance: List[int],
    tube_2nd_section_height_measured_from_zm: List[int],
    tube_2nd_section_ratio: List[int],
    minimal_traverse_height_at_begin_of_command: List[int],
    minimal_height_at_command_end: List[int],
    dispense_volume: List[int],
    dispense_speed: List[int],
    cut_off_speed: List[int],
    stop_back_volume: List[int],
    transport_air_volume: List[int],
    blow_out_air_volume: List[int],
    lld_mode: List[int],
    side_touch_off_distance: int,
    dispense_position_above_z_touch_off: List[int],
    lld_sensitivity: List[int],
    pressure_lld_sensitivity: List[int],
    swap_speed: List[int],
    settling_time: List[int],
    mix_volume: List[int],
    mix_cycles: List[int],
    mix_position_in_z_direction_from_liquid_surface: List[int],
    mix_speed: List[int],
    surface_following_distance_during_mixing: List[int],
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
  ):
    """Dispensing of liquid (A1PM:DD)."""
    await self.driver.send_command(
      module="A1PM",
      command="DD",
      tip_pattern=tip_pattern,
      dm=type_of_dispensing_mode,
      tm=tip_pattern,
      xp=x_position,
      yp=y_position,
      zx=minimum_height,
      lp=lld_search_height,
      zl=liquid_surface_at_function_without_lld,
      po=pull_out_distance_to_take_transport_air_in_function_without_lld,
      ip=immersion_depth,
      fp=surface_following_distance,
      zu=tube_2nd_section_height_measured_from_zm,
      zr=tube_2nd_section_ratio,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      dv=[f"{vol:04}" for vol in dispense_volume],
      ds=dispense_speed,
      ss=cut_off_speed,
      rv=stop_back_volume,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      lm=lld_mode,
      dj=side_touch_off_distance,
      zo=dispense_position_above_z_touch_off,
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      de=swap_speed,
      wt=settling_time,
      mv=mix_volume,
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      ms=mix_speed,
      mh=surface_following_distance_during_mixing,
      la=[0] * len(x_position),
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index or [0] * len(x_position),
      gk=recording_mode,
    )
