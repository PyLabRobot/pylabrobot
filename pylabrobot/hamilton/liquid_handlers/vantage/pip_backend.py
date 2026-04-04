"""Vantage PIP backend: translates PIP operations into Vantage firmware commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.legacy.liquid_handling.liquid_classes.hamilton import (
  HamiltonLiquidClass,
  get_vantage_liquid_class,
)
from pylabrobot.resources import Tip, Well
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.liquid import Liquid

if TYPE_CHECKING:
  from .driver import VantageDriver


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

  # Trailing padding (Vantage firmware expects at least one extra slot when < num_channels).
  if len(x_positions) < num_channels:
    x_positions = x_positions + [0]
    y_positions = y_positions + [0]
    channels_involved = channels_involved + [False]

  return x_positions, y_positions, channels_involved


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _resolve_liquid_classes(
  explicit: Optional[List[Optional[HamiltonLiquidClass]]],
  ops: list,
  jet: Union[bool, List[bool]],
  blow_out: Union[bool, List[bool]],
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
    result.append(get_vantage_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=False,
      is_tip=True,
      has_filter=tip.has_filter,
      liquid=Liquid.WATER,
      jet=jet[i],
      blow_out=blow_out[i],
    ))

  return result


def _dispensing_mode_for_op(empty: bool, jet: bool, blow_out: bool) -> int:
  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  return 3 if blow_out else 2


# ---------------------------------------------------------------------------
# VantagePIPBackend
# ---------------------------------------------------------------------------


class VantagePIPBackend(PIPBackend):
  """Translates PIP operations into Vantage firmware commands via the driver."""

  def __init__(self, driver: VantageDriver, tip_presences: List[bool]):
    self._driver = driver
    self._tip_presences = tip_presences

  @property
  def num_channels(self) -> int:
    return self._driver.num_channels

  async def _on_setup(self) -> None:
    """Initialize PIP channels if not already initialized."""
    pip_channels_initialized = await self.pip_request_initialization_status()
    if not pip_channels_initialized or any(self._tip_presences):
      traversal = self._driver.traversal_height
      await self.pip_initialize(
        x_position=[7095] * self.num_channels,
        y_position=[3891, 3623, 3355, 3087, 2819, 2551, 2283, 2016][:self.num_channels],
        begin_z_deposit_position=[int(traversal * 10)] * self.num_channels,
        end_z_deposit_position=[1235] * self.num_channels,
        minimal_height_at_command_end=[int(traversal * 10)] * self.num_channels,
        tip_pattern=[True] * self.num_channels,
        tip_type=[1] * self.num_channels,
        TODO_DI_2=70,
      )

  # -- pick_up_tips -----------------------------------------------------------

  @dataclass
  class PickUpTipsParams(BackendParams):
    """Vantage-specific parameters for ``pick_up_tips``."""
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None

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
    ttti = [await self._driver.request_or_assign_tip_type_index(ham_tip)] * len(ops)

    max_z = max(
      op.resource.get_absolute_location(x="c", y="c", z="b").z + op.offset.z for op in ops
    )
    max_total_tip_length = max(op.tip.total_tip_length for op in ops)
    max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)

    # not sure why this is necessary, but it is according to log files and experiments
    if ham_tip.tip_size == TipSize.LOW_VOLUME:
      max_tip_length += 2
    elif ham_tip.tip_size != TipSize.STANDARD_VOLUME:
      max_tip_length -= 2

    traversal = self._driver.traversal_height

    await self.pip_tip_pick_up(
      x_position=x_positions,
      y_position=y_positions,
      tip_pattern=tip_pattern,
      tip_type=ttti,
      begin_z_deposit_position=[round((max_z + max_total_tip_length) * 10)] * len(ops),
      end_z_deposit_position=[round((max_z + max_tip_length) * 10)] * len(ops),
      minimal_traverse_height_at_begin_of_command=[
        round(th * 10)
        for th in backend_params.minimal_traverse_height_at_begin_of_command
        or [traversal] * len(ops)
      ],
      minimal_height_at_command_end=[
        round(th * 10)
        for th in backend_params.minimal_height_at_command_end or [traversal] * len(ops)
      ],
      tip_handling_method=[1 for _ in ops],  # always appears to be 1
      blow_out_air_volume=[0] * len(ops),
    )

  # -- drop_tips --------------------------------------------------------------

  @dataclass
  class DropTipsParams(BackendParams):
    """Vantage-specific parameters for ``drop_tips``."""
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None

  async def drop_tips(
    self,
    ops: List[TipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Drop tips to a resource."""
    if not isinstance(backend_params, VantagePIPBackend.DropTipsParams):
      backend_params = VantagePIPBackend.DropTipsParams()

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    max_z = max(
      op.resource.get_absolute_location(x="c", y="c", z="b").z + op.offset.z for op in ops
    )

    traversal = self._driver.traversal_height

    await self.pip_tip_discard(
      x_position=x_positions,
      y_position=y_positions,
      tip_pattern=channels_involved,
      begin_z_deposit_position=[round((max_z + 10) * 10)] * len(ops),
      end_z_deposit_position=[round(max_z * 10)] * len(ops),
      minimal_traverse_height_at_begin_of_command=[
        round(th * 10)
        for th in backend_params.minimal_traverse_height_at_begin_of_command
        or [traversal] * len(ops)
      ],
      minimal_height_at_command_end=[
        round(th * 10)
        for th in backend_params.minimal_height_at_command_end or [traversal] * len(ops)
      ],
      tip_handling_method=[0 for _ in ops],  # Always appears to be 0, even in trash.
      TODO_TR_2=0,
    )

  # -- aspirate ---------------------------------------------------------------

  @dataclass
  class AspirateParams(BackendParams):
    """Vantage-specific parameters for ``aspirate``.

    See :meth:`pip_aspirate` (the firmware command) for parameter documentation. This dataclass
    serves as a wrapper for that command, and will convert operations into the appropriate format.
    This method additionally provides default values based on firmware instructions sent by Venus on
    Vantage, rather than machine default values (which are often not what you want).

    Args:
      jet: Whether to search for a "jet" liquid class.
      blow_out: Whether to search for a "blow out" liquid class. Note that in the VENUS liquid
        editor, the term "empty" is used for this, but in the firmware documentation, "empty" is
        used for a different mode (dm4).
      hamilton_liquid_classes: The Hamilton liquid classes to use. If ``None``, the liquid classes
        will be determined automatically based on the tip and liquid used.
      disable_volume_correction: Whether to disable volume correction for each operation.
    """
    hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None
    disable_volume_correction: Optional[List[bool]] = None
    type_of_aspiration: Optional[List[int]] = None
    jet: Optional[List[bool]] = None
    blow_out: Optional[List[bool]] = None
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
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None

  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate from (a) resource(s).

    See :meth:`pip_aspirate` (the firmware command) for parameter documentation. This method serves
    as a wrapper for that command, and will convert operations into the appropriate format. This
    method additionally provides default values based on firmware instructions sent by Venus on
    Vantage, rather than machine default values (which are often not what you want).

    Args:
      ops: The aspiration operations.
      use_channels: The channels to use.
      backend_params: Vantage-specific aspiration parameters.
    """

    if not isinstance(backend_params, VantagePIPBackend.AspirateParams):
      backend_params = VantagePIPBackend.AspirateParams()

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    n = len(ops)
    jet = backend_params.jet or [False] * n
    blow_out = backend_params.blow_out or [False] * n

    # Resolve liquid classes (auto-detect from tip if not provided).
    hlcs = _resolve_liquid_classes(backend_params.hamilton_liquid_classes, ops,
                                   jet=jet, blow_out=blow_out, )

    # Well bottoms (absolute z + material thickness).
    well_bottoms = [
      op.resource.get_absolute_location(x="c", y="c", z="b").z
      + op.offset.z
      + op.resource.material_z_thickness
      for op in ops
    ]

    # LLD search height. -1 compared to STAR.
    lld_search_heights = backend_params.lld_search_height or [
      wb
      + op.resource.get_absolute_size_z()
      + (2.7 - 1 if isinstance(op.resource, Well) else 5)
      for wb, op in zip(well_bottoms, ops)
    ]

    liquid_surfaces_no_lld = backend_params.liquid_surface_at_function_without_lld or [
      wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)
    ]

    # correct volumes using the liquid class if not disabled
    disable_volume_correction = backend_params.disable_volume_correction or [False] * n
    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_volume_correction)
    ]

    flow_rates = [
      op.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 100)
      for op, hlc in zip(ops, hlcs)
    ]
    blow_out_air_volumes = [
      (op.blow_out_air_volume or (hlc.dispense_blow_out_volume if hlc is not None else 0))
      for op, hlc in zip(ops, hlcs)
    ]

    traversal = self._driver.traversal_height

    await self.pip_aspirate(
      x_position=x_positions,
      y_position=y_positions,
      type_of_aspiration=backend_params.type_of_aspiration or [0] * n,
      tip_pattern=channels_involved,
      minimal_traverse_height_at_begin_of_command=[
        round(th * 10)
        for th in backend_params.minimal_traverse_height_at_begin_of_command or [traversal] * n
      ],
      minimal_height_at_command_end=[
        round(th * 10)
        for th in backend_params.minimal_height_at_command_end or [traversal] * n
      ],
      lld_search_height=[round(ls * 10) for ls in lld_search_heights],
      clot_detection_height=[
        round(cdh * 10) for cdh in backend_params.clot_detection_height or [0] * n
      ],
      liquid_surface_at_function_without_lld=[
        round(lsn * 10) for lsn in liquid_surfaces_no_lld
      ],
      pull_out_distance_to_take_transport_air_in_function_without_lld=[
        round(pod * 10)
        for pod in backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
        or [10.9] * n
      ],
      tube_2nd_section_height_measured_from_zm=[
        round(t2sh * 10)
        for t2sh in backend_params.tube_2nd_section_height_measured_from_zm or [0] * n
      ],
      tube_2nd_section_ratio=[
        round(t2sr * 10) for t2sr in backend_params.tube_2nd_section_ratio or [0] * n
      ],
      minimum_height=[
        round(wb * 10) for wb in backend_params.minimum_height or well_bottoms
      ],
      immersion_depth=[round(id_ * 10) for id_ in backend_params.immersion_depth or [0] * n],
      surface_following_distance=[
        round(sfd * 10) for sfd in backend_params.surface_following_distance or [0] * n
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
        round(pwv * 100) for pwv in backend_params.pre_wetting_volume or [0] * n
      ],
      lld_mode=backend_params.lld_mode or [0] * n,
      lld_sensitivity=backend_params.lld_sensitivity or [4] * n,
      pressure_lld_sensitivity=backend_params.pressure_lld_sensitivity or [4] * n,
      aspirate_position_above_z_touch_off=[
        round(apz * 10)
        for apz in backend_params.aspirate_position_above_z_touch_off or [0.5] * n
      ],
      swap_speed=[round(ss * 10) for ss in backend_params.swap_speed or [2] * n],
      settling_time=[round(st * 10) for st in backend_params.settling_time or [1] * n],
      mix_volume=[
        round(op.mix.volume * 100) if op.mix is not None else 0 for op in ops
      ],
      mix_cycles=[op.mix.repetitions if op.mix is not None else 0 for op in ops],
      mix_position_in_z_direction_from_liquid_surface=[
        round(mp)
        for mp in backend_params.mix_position_in_z_direction_from_liquid_surface or [0] * n
      ],
      mix_speed=[
        round(op.mix.flow_rate * 10) if op.mix is not None else 2500 for op in ops
      ],
      surface_following_distance_during_mixing=[
        round(sfdm * 10)
        for sfdm in backend_params.surface_following_distance_during_mixing or [0] * n
      ],
      TODO_DA_5=backend_params.TODO_DA_5 or [0] * n,
      capacitive_mad_supervision_on_off=(
        backend_params.capacitive_mad_supervision_on_off or [0] * n
      ),
      pressure_mad_supervision_on_off=(
        backend_params.pressure_mad_supervision_on_off or [0] * n
      ),
      tadm_algorithm_on_off=backend_params.tadm_algorithm_on_off or 0,
      limit_curve_index=backend_params.limit_curve_index or [0] * n,
      recording_mode=backend_params.recording_mode or 0,
    )

  # -- dispense ---------------------------------------------------------------

  @dataclass
  class DispenseParams(BackendParams):
    """Vantage-specific parameters for ``dispense``.

    See :meth:`pip_dispense` (the firmware command) for parameter documentation. This dataclass
    serves as a wrapper for that command.

    Args:
      jet: Whether to use jetting for each dispense. Defaults to ``False`` for all. Used for
        determining the dispense mode. True for dispense mode 0 or 1.
      blow_out: Whether to use "blow out" dispense mode for each dispense. Defaults to ``False``
        for all. This is labelled as "empty" in the VENUS liquid editor, but "blow out" in the
        firmware documentation. True for dispense mode 1 or 3.
      empty: Whether to use "empty" dispense mode for each dispense. Defaults to ``False`` for all.
        Truly empty the tip, not available in the VENUS liquid editor, but is in the firmware
        documentation. Dispense mode 4.
      hamilton_liquid_classes: The Hamilton liquid classes to use. If ``None``, the liquid classes
        will be determined automatically based on the tip and liquid used.
      disable_volume_correction: Whether to disable volume correction for each operation.
    """
    hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None
    disable_volume_correction: Optional[List[bool]] = None
    jet: Optional[List[bool]] = None
    blow_out: Optional[List[bool]] = None
    empty: Optional[List[bool]] = None
    type_of_dispensing_mode: Optional[List[int]] = None
    minimum_height: Optional[List[float]] = None
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[float]] = None
    immersion_depth: Optional[List[float]] = None
    surface_following_distance: Optional[List[float]] = None
    tube_2nd_section_height_measured_from_zm: Optional[List[float]] = None
    tube_2nd_section_ratio: Optional[List[float]] = None
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None
    minimal_height_at_command_end: Optional[List[float]] = None
    lld_search_height: Optional[List[float]] = None
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

  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense to (a) resource(s).

    See :meth:`pip_dispense` (the firmware command) for parameter documentation. This method serves
    as a wrapper for that command, and will convert operations into the appropriate format. This
    method additionally provides default values based on firmware instructions sent by Venus on
    Vantage, rather than machine default values (which are often not what you want).

    Args:
      ops: The dispense operations.
      use_channels: The channels to use.
      backend_params: Vantage-specific dispense parameters.
    """

    if not isinstance(backend_params, VantagePIPBackend.DispenseParams):
      backend_params = VantagePIPBackend.DispenseParams()

    x_positions, y_positions, channels_involved = _ops_to_fw_positions(
      ops, use_channels, self.num_channels
    )

    n = len(ops)
    jet = backend_params.jet or [False] * n
    empty = backend_params.empty or [False] * n
    blow_out = backend_params.blow_out or [False] * n

    # Resolve liquid classes.
    hlcs = _resolve_liquid_classes(backend_params.hamilton_liquid_classes, ops,
                                   jet=jet, blow_out=blow_out, )

    # Well bottoms.
    well_bottoms = [
      op.resource.get_absolute_location(x="c", y="c", z="b").z
      + op.offset.z
      + op.resource.material_z_thickness
      for op in ops
    ]

    liquid_surfaces_no_lld = [wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)]

    # LLD search height. -1 compared to STAR.
    lld_search_heights = backend_params.lld_search_height or [
      wb
      + op.resource.get_absolute_size_z()
      + (2.7 - 1 if isinstance(op.resource, Well) else 5)
      for wb, op in zip(well_bottoms, ops)
    ]

    # correct volumes using the liquid class
    disable_volume_correction = backend_params.disable_volume_correction or [False] * n
    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_volume_correction)
    ]

    flow_rates = [
      op.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 100)
      for op, hlc in zip(ops, hlcs)
    ]
    blow_out_air_volumes = [
      (op.blow_out_air_volume or (hlc.dispense_blow_out_volume if hlc is not None else 0))
      for op, hlc in zip(ops, hlcs)
    ]

    type_of_dispensing_mode = backend_params.type_of_dispensing_mode or [
      _dispensing_mode_for_op(jet=jet[i], empty=empty[i], blow_out=blow_out[i])
      for i in range(n)
    ]

    traversal = self._driver.traversal_height

    await self.pip_dispense(
      x_position=x_positions,
      y_position=y_positions,
      tip_pattern=channels_involved,
      type_of_dispensing_mode=type_of_dispensing_mode,
      minimum_height=[
        round(wb * 10) for wb in backend_params.minimum_height or well_bottoms
      ],
      lld_search_height=[round(sh * 10) for sh in lld_search_heights],
      liquid_surface_at_function_without_lld=[round(ls * 10) for ls in liquid_surfaces_no_lld],
      pull_out_distance_to_take_transport_air_in_function_without_lld=[
        round(pod * 10)
        for pod in backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
        or [5.0] * n
      ],
      immersion_depth=[round(id_ * 10) for id_ in backend_params.immersion_depth or [0] * n],
      surface_following_distance=[
        round(sfd * 10) for sfd in backend_params.surface_following_distance or [2.1] * n
      ],
      tube_2nd_section_height_measured_from_zm=[
        round(t2sh * 10)
        for t2sh in backend_params.tube_2nd_section_height_measured_from_zm or [0] * n
      ],
      tube_2nd_section_ratio=[
        round(t2sr * 10) for t2sr in backend_params.tube_2nd_section_ratio or [0] * n
      ],
      minimal_traverse_height_at_begin_of_command=[
        round(mth * 10)
        for mth in backend_params.minimal_traverse_height_at_begin_of_command
        or [traversal] * n
      ],
      minimal_height_at_command_end=[
        round(mh * 10)
        for mh in backend_params.minimal_height_at_command_end or [traversal] * n
      ],
      dispense_volume=[round(vol * 100) for vol in volumes],
      dispense_speed=[round(fr * 10) for fr in flow_rates],
      cut_off_speed=[round(cs * 10) for cs in backend_params.cut_off_speed or [250] * n],
      stop_back_volume=[round(sbv * 100) for sbv in backend_params.stop_back_volume or [0] * n],
      transport_air_volume=[
        round(tav * 10)
        for tav in backend_params.transport_air_volume
        or [hlc.dispense_air_transport_volume if hlc is not None else 0 for hlc in hlcs]
      ],
      blow_out_air_volume=[round(boav * 100) for boav in blow_out_air_volumes],
      lld_mode=backend_params.lld_mode or [0] * n,
      side_touch_off_distance=round(backend_params.side_touch_off_distance * 10),
      dispense_position_above_z_touch_off=[
        round(dpz * 10)
        for dpz in backend_params.dispense_position_above_z_touch_off or [0.5] * n
      ],
      lld_sensitivity=backend_params.lld_sensitivity or [1] * n,
      pressure_lld_sensitivity=backend_params.pressure_lld_sensitivity or [1] * n,
      swap_speed=[round(ss * 10) for ss in backend_params.swap_speed or [1] * n],
      settling_time=[round(st * 10) for st in backend_params.settling_time or [0] * n],
      mix_volume=[round(op.mix.volume * 100) if op.mix is not None else 0 for op in ops],
      mix_cycles=[op.mix.repetitions if op.mix is not None else 0 for op in ops],
      mix_position_in_z_direction_from_liquid_surface=[
        round(mp)
        for mp in backend_params.mix_position_in_z_direction_from_liquid_surface or [0] * n
      ],
      mix_speed=[
        round(op.mix.flow_rate * 10) if op.mix is not None else 10 for op in ops
      ],
      surface_following_distance_during_mixing=[
        round(sfdm * 10)
        for sfdm in backend_params.surface_following_distance_during_mixing or [0] * n
      ],
      TODO_DD_2=backend_params.TODO_DD_2 or [0] * n,
      tadm_algorithm_on_off=backend_params.tadm_algorithm_on_off or 0,
      limit_curve_index=backend_params.limit_curve_index or [0] * n,
      recording_mode=backend_params.recording_mode or 0,
    )

  # -- can_pick_up_tip --------------------------------------------------------

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    return True

  # -- request_tip_presence ---------------------------------------------------

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Request tip presence on each channel.

    Returns:
      A list of length ``num_channels`` where each element is ``True`` if a tip is mounted,
      ``False`` if not, or ``None`` if unknown.
    """
    presences = await self._driver.query_tip_presence()
    result: List[Optional[bool]] = list(presences)
    return result

  # ===========================================================================
  # Firmware command methods (raw protocol)
  # ===========================================================================

  async def pip_request_initialization_status(self) -> bool:
    """Request the pip initialization status.

    This command was based on the STAR command (QW) and the VStarTranslator log. A1PM corresponds
    to all pip channels together.

    Returns:
      True if the pip channels module is initialized, False otherwise.
    """

    resp = await self._driver.send_command(module="A1PM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def pip_initialize(
    self,
    x_position: List[int],
    y_position: List[int],
    begin_z_deposit_position: Optional[List[int]] = None,
    end_z_deposit_position: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
    tip_pattern: Optional[List[bool]] = None,
    tip_type: Optional[List[int]] = None,
    TODO_DI_2: int = 0,
  ):
    """Initialize

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      begin_z_deposit_position: Begin of tip deposit process (Z- discard range) [0.1mm] ??
      end_z_deposit_position: Z deposit position [0.1mm] (collar bearing position).
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      tip_type: Tip type (see command TT).
      TODO_DI_2: Unknown.
    """

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in begin_z_deposit_position):
      raise ValueError("begin_z_deposit_position must be in range 0 to 3600")

    if end_z_deposit_position is None:
      end_z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in end_z_deposit_position):
      raise ValueError("end_z_deposit_position must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if tip_type is None:
      tip_type = [4] * self.num_channels
    elif not all(0 <= x <= 199 for x in tip_type):
      raise ValueError("tip_type must be in range 0 to 199")

    if not -1000 <= TODO_DI_2 <= 1000:
      raise ValueError("TODO_DI_2 must be in range -1000 to 1000")

    return await self._driver.send_command(
      module="A1PM",
      command="DI",
      xp=x_position,
      yp=y_position,
      tp=begin_z_deposit_position,
      tz=end_z_deposit_position,
      te=minimal_height_at_command_end,
      tm=tip_pattern,
      tt=tip_type,
      ts=TODO_DI_2,
    )

  async def pip_aspirate(
    self,
    x_position: List[int],
    y_position: List[int],
    type_of_aspiration: Optional[List[int]] = None,
    tip_pattern: Optional[List[bool]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
    lld_search_height: Optional[List[int]] = None,
    clot_detection_height: Optional[List[int]] = None,
    liquid_surface_at_function_without_lld: Optional[List[int]] = None,
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[int]] = None,
    tube_2nd_section_height_measured_from_zm: Optional[List[int]] = None,
    tube_2nd_section_ratio: Optional[List[int]] = None,
    minimum_height: Optional[List[int]] = None,
    immersion_depth: Optional[List[int]] = None,
    surface_following_distance: Optional[List[int]] = None,
    aspiration_volume: Optional[List[int]] = None,
    TODO_DA_2: Optional[List[int]] = None,
    aspiration_speed: Optional[List[int]] = None,
    transport_air_volume: Optional[List[int]] = None,
    blow_out_air_volume: Optional[List[int]] = None,
    pre_wetting_volume: Optional[List[int]] = None,
    lld_mode: Optional[List[int]] = None,
    lld_sensitivity: Optional[List[int]] = None,
    pressure_lld_sensitivity: Optional[List[int]] = None,
    aspirate_position_above_z_touch_off: Optional[List[int]] = None,
    TODO_DA_4: Optional[List[int]] = None,
    swap_speed: Optional[List[int]] = None,
    settling_time: Optional[List[int]] = None,
    mix_volume: Optional[List[int]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_in_z_direction_from_liquid_surface: Optional[List[int]] = None,
    mix_speed: Optional[List[int]] = None,
    surface_following_distance_during_mixing: Optional[List[int]] = None,
    TODO_DA_5: Optional[List[int]] = None,
    capacitive_mad_supervision_on_off: Optional[List[int]] = None,
    pressure_mad_supervision_on_off: Optional[List[int]] = None,
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
  ):
    """Aspiration of liquid

    Args:
      type_of_aspiration: Type of aspiration (0 = simple 1 = sequence 2 = cup emptied).
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      lld_search_height: LLD search height [0.1mm].
      clot_detection_height: (0).
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      pull_out_distance_to_take_transport_air_in_function_without_lld:
          Pull out distance to take transp. air in function without LLD [0.1mm].
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from zm [0.1mm].
      tube_2nd_section_ratio: Tube 2nd section ratio.
      minimum_height: Minimum height (maximum immersion depth) [0.1mm].
      immersion_depth: Immersion depth [0.1mm].
      surface_following_distance: Surface following distance [0.1mm].
      aspiration_volume: Aspiration volume [0.01ul].
      TODO_DA_2: (0).
      aspiration_speed: Aspiration speed [0.1ul]/s.
      transport_air_volume: Transport air volume [0.1ul].
      blow_out_air_volume: Blow out air volume [0.01ul].
      pre_wetting_volume: Pre wetting volume [0.1ul].
      lld_mode: LLD Mode (0 = off).
      lld_sensitivity: LLD sensitivity (1 = high, 4 = low).
      pressure_lld_sensitivity: Pressure LLD sensitivity (1= high, 4=low).
      aspirate_position_above_z_touch_off: (0).
      TODO_DA_4: (0).
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s].
      settling_time: Settling time [0.1s].
      mix_volume: Mix volume [0.1ul].
      mix_cycles: Mix cycles.
      mix_position_in_z_direction_from_liquid_surface: Mix position in Z direction from liquid
        surface[0.1mm].
      mix_speed: Mix speed [0.1ul/s].
      surface_following_distance_during_mixing: Surface following distance during mixing [0.1mm].
      TODO_DA_5: (0).
      capacitive_mad_supervision_on_off: Capacitive MAD supervision on/off (0 = OFF).
      pressure_mad_supervision_on_off: Pressure MAD supervision on/off (0 = OFF).
      tadm_algorithm_on_off: TADM algorithm on/off (0 = off).
      limit_curve_index: Limit curve index.
      recording_mode: Recording mode (0 = no 1 = TADM errors only 2 = all TADM measurements).
    """

    if type_of_aspiration is None:
      type_of_aspiration = [0] * self.num_channels
    elif not all(0 <= x <= 2 for x in type_of_aspiration):
      raise ValueError("type_of_aspiration must be in range 0 to 2")

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    if lld_search_height is None:
      lld_search_height = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in lld_search_height):
      raise ValueError("lld_search_height must be in range 0 to 3600")

    if clot_detection_height is None:
      clot_detection_height = [60] * self.num_channels
    elif not all(0 <= x <= 500 for x in clot_detection_height):
      raise ValueError("clot_detection_height must be in range 0 to 500")

    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in liquid_surface_at_function_without_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3600")

    if pull_out_distance_to_take_transport_air_in_function_without_lld is None:
      pull_out_distance_to_take_transport_air_in_function_without_lld = [50] * self.num_channels
    elif not all(
      0 <= x <= 3600 for x in pull_out_distance_to_take_transport_air_in_function_without_lld
    ):
      raise ValueError(
        "pull_out_distance_to_take_transport_air_in_function_without_lld must be in range 0 to 3600"
      )

    if tube_2nd_section_height_measured_from_zm is None:
      tube_2nd_section_height_measured_from_zm = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in tube_2nd_section_height_measured_from_zm):
      raise ValueError("tube_2nd_section_height_measured_from_zm must be in range 0 to 3600")

    if tube_2nd_section_ratio is None:
      tube_2nd_section_ratio = [0] * self.num_channels
    elif not all(0 <= x <= 10000 for x in tube_2nd_section_ratio):
      raise ValueError("tube_2nd_section_ratio must be in range 0 to 10000")

    if minimum_height is None:
      minimum_height = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimum_height):
      raise ValueError("minimum_height must be in range 0 to 3600")

    if immersion_depth is None:
      immersion_depth = [0] * self.num_channels
    elif not all(-3600 <= x <= 3600 for x in immersion_depth):
      raise ValueError("immersion_depth must be in range -3600 to 3600")

    if surface_following_distance is None:
      surface_following_distance = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in surface_following_distance):
      raise ValueError("surface_following_distance must be in range 0 to 3600")

    if aspiration_volume is None:
      aspiration_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in aspiration_volume):
      raise ValueError("aspiration_volume must be in range 0 to 125000")

    if TODO_DA_2 is None:
      TODO_DA_2 = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in TODO_DA_2):
      raise ValueError("TODO_DA_2 must be in range 0 to 125000")

    if aspiration_speed is None:
      aspiration_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in aspiration_speed):
      raise ValueError("aspiration_speed must be in range 10 to 10000")

    if transport_air_volume is None:
      transport_air_volume = [0] * self.num_channels
    elif not all(0 <= x <= 500 for x in transport_air_volume):
      raise ValueError("transport_air_volume must be in range 0 to 500")

    if blow_out_air_volume is None:
      blow_out_air_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in blow_out_air_volume):
      raise ValueError("blow_out_air_volume must be in range 0 to 125000")

    if pre_wetting_volume is None:
      pre_wetting_volume = [0] * self.num_channels
    elif not all(0 <= x <= 999 for x in pre_wetting_volume):
      raise ValueError("pre_wetting_volume must be in range 0 to 999")

    if lld_mode is None:
      lld_mode = [1] * self.num_channels
    elif not all(0 <= x <= 4 for x in lld_mode):
      raise ValueError("lld_mode must be in range 0 to 4")

    if lld_sensitivity is None:
      lld_sensitivity = [1] * self.num_channels
    elif not all(1 <= x <= 4 for x in lld_sensitivity):
      raise ValueError("lld_sensitivity must be in range 1 to 4")

    if pressure_lld_sensitivity is None:
      pressure_lld_sensitivity = [1] * self.num_channels
    elif not all(1 <= x <= 4 for x in pressure_lld_sensitivity):
      raise ValueError("pressure_lld_sensitivity must be in range 1 to 4")

    if aspirate_position_above_z_touch_off is None:
      aspirate_position_above_z_touch_off = [5] * self.num_channels
    elif not all(0 <= x <= 100 for x in aspirate_position_above_z_touch_off):
      raise ValueError("aspirate_position_above_z_touch_off must be in range 0 to 100")

    if TODO_DA_4 is None:
      TODO_DA_4 = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DA_4):
      raise ValueError("TODO_DA_4 must be in range 0 to 1")

    if swap_speed is None:
      swap_speed = [100] * self.num_channels
    elif not all(3 <= x <= 1600 for x in swap_speed):
      raise ValueError("swap_speed must be in range 3 to 1600")

    if settling_time is None:
      settling_time = [5] * self.num_channels
    elif not all(0 <= x <= 99 for x in settling_time):
      raise ValueError("settling_time must be in range 0 to 99")

    if mix_volume is None:
      mix_volume = [0] * self.num_channels
    elif not all(0 <= x <= 12500 for x in mix_volume):
      raise ValueError("mix_volume must be in range 0 to 12500")

    if mix_cycles is None:
      mix_cycles = [0] * self.num_channels
    elif not all(0 <= x <= 99 for x in mix_cycles):
      raise ValueError("mix_cycles must be in range 0 to 99")

    if mix_position_in_z_direction_from_liquid_surface is None:
      mix_position_in_z_direction_from_liquid_surface = [250] * self.num_channels
    elif not all(0 <= x <= 900 for x in mix_position_in_z_direction_from_liquid_surface):
      raise ValueError("mix_position_in_z_direction_from_liquid_surface must be in range 0 to 900")

    if mix_speed is None:
      mix_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in mix_speed):
      raise ValueError("mix_speed must be in range 10 to 10000")

    if surface_following_distance_during_mixing is None:
      surface_following_distance_during_mixing = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in surface_following_distance_during_mixing):
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 3600")

    if TODO_DA_5 is None:
      TODO_DA_5 = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DA_5):
      raise ValueError("TODO_DA_5 must be in range 0 to 1")

    if capacitive_mad_supervision_on_off is None:
      capacitive_mad_supervision_on_off = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in capacitive_mad_supervision_on_off):
      raise ValueError("capacitive_mad_supervision_on_off must be in range 0 to 1")

    if pressure_mad_supervision_on_off is None:
      pressure_mad_supervision_on_off = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in pressure_mad_supervision_on_off):
      raise ValueError("pressure_mad_supervision_on_off must be in range 0 to 1")

    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")

    if limit_curve_index is None:
      limit_curve_index = [0] * self.num_channels
    elif not all(0 <= x <= 999 for x in limit_curve_index):
      raise ValueError("limit_curve_index must be in range 0 to 999")

    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    return await self._driver.send_command(
      module="A1PM",
      command="DA",
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
      # ar=TODO_DA_2, # this parameter is not used by VoV
      as_=aspiration_speed,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      oa=pre_wetting_volume,
      lm=lld_mode,
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      zo=aspirate_position_above_z_touch_off,
      # lg=TODO_DA_4,
      de=swap_speed,
      wt=settling_time,
      mv=mix_volume,
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      ms=mix_speed,
      mh=surface_following_distance_during_mixing,
      la=TODO_DA_5,
      lb=capacitive_mad_supervision_on_off,
      lc=pressure_mad_supervision_on_off,
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index,
      gk=recording_mode,
    )

  async def pip_dispense(
    self,
    x_position: List[int],
    y_position: List[int],
    type_of_dispensing_mode: Optional[List[int]] = None,
    tip_pattern: Optional[List[bool]] = None,
    minimum_height: Optional[List[int]] = None,
    lld_search_height: Optional[List[int]] = None,
    liquid_surface_at_function_without_lld: Optional[List[int]] = None,
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[int]] = None,
    immersion_depth: Optional[List[int]] = None,
    surface_following_distance: Optional[List[int]] = None,
    tube_2nd_section_height_measured_from_zm: Optional[List[int]] = None,
    tube_2nd_section_ratio: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
    dispense_volume: Optional[List[int]] = None,
    dispense_speed: Optional[List[int]] = None,
    cut_off_speed: Optional[List[int]] = None,
    stop_back_volume: Optional[List[int]] = None,
    transport_air_volume: Optional[List[int]] = None,
    blow_out_air_volume: Optional[List[int]] = None,
    lld_mode: Optional[List[int]] = None,
    side_touch_off_distance: int = 0,
    dispense_position_above_z_touch_off: Optional[List[int]] = None,
    lld_sensitivity: Optional[List[int]] = None,
    pressure_lld_sensitivity: Optional[List[int]] = None,
    swap_speed: Optional[List[int]] = None,
    settling_time: Optional[List[int]] = None,
    mix_volume: Optional[List[int]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_in_z_direction_from_liquid_surface: Optional[List[int]] = None,
    mix_speed: Optional[List[int]] = None,
    surface_following_distance_during_mixing: Optional[List[int]] = None,
    TODO_DD_2: Optional[List[int]] = None,
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
  ):
    """Dispensing of liquid

    Args:
      type_of_dispensing_mode: Type of dispensing mode 0 = part in jet 1 = blow in jet 2 = Part at
          surface 3 = Blow at surface 4 = Empty.
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      minimum_height: Minimum height (maximum immersion depth) [0.1mm].
      lld_search_height: LLD search height [0.1mm].
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      pull_out_distance_to_take_transport_air_in_function_without_lld:
          Pull out distance to take transp. air in function without LLD [0.1mm]
        .
      immersion_depth: Immersion depth [0.1mm].
      surface_following_distance: Surface following distance [0.1mm].
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from zm [0.1mm].
      tube_2nd_section_ratio: Tube 2nd section ratio.
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      dispense_volume: Dispense volume [0.01ul].
      dispense_speed: Dispense speed [0.1ul/s].
      cut_off_speed: Cut off speed [0.1ul/s].
      stop_back_volume: Stop back volume [0.1ul].
      transport_air_volume: Transport air volume [0.1ul].
      blow_out_air_volume: Blow out air volume [0.01ul].
      lld_mode: LLD Mode (0 = off).
      side_touch_off_distance: Side touch off distance [0.1mm].
      dispense_position_above_z_touch_off: (0).
      lld_sensitivity: LLD sensitivity (1 = high, 4 = low).
      pressure_lld_sensitivity: Pressure LLD sensitivity (1= high, 4=low).
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s].
      settling_time: Settling time [0.1s].
      mix_volume: Mix volume [0.1ul].
      mix_cycles: Mix cycles.
      mix_position_in_z_direction_from_liquid_surface: Mix position in Z direction from liquid
        surface[0.1mm].
      mix_speed: Mix speed [0.1ul/s].
      surface_following_distance_during_mixing: Surface following distance during mixing [0.1mm].
      TODO_DD_2: (0).
      tadm_algorithm_on_off: TADM algorithm on/off (0 = off).
      limit_curve_index: Limit curve index.
      recording_mode:
          Recording mode (0 = no 1 = TADM errors only 2 = all TADM measurements)
        .
    """

    if type_of_dispensing_mode is None:
      type_of_dispensing_mode = [0] * self.num_channels
    elif not all(0 <= x <= 4 for x in type_of_dispensing_mode):
      raise ValueError("type_of_dispensing_mode must be in range 0 to 4")

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if minimum_height is None:
      minimum_height = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimum_height):
      raise ValueError("minimum_height must be in range 0 to 3600")

    if lld_search_height is None:
      lld_search_height = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in lld_search_height):
      raise ValueError("lld_search_height must be in range 0 to 3600")

    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in liquid_surface_at_function_without_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3600")

    if pull_out_distance_to_take_transport_air_in_function_without_lld is None:
      pull_out_distance_to_take_transport_air_in_function_without_lld = [50] * self.num_channels
    elif not all(
      0 <= x <= 3600 for x in pull_out_distance_to_take_transport_air_in_function_without_lld
    ):
      raise ValueError(
        "pull_out_distance_to_take_transport_air_in_function_without_lld must be in range 0 to 3600"
      )

    if immersion_depth is None:
      immersion_depth = [0] * self.num_channels
    elif not all(-3600 <= x <= 3600 for x in immersion_depth):
      raise ValueError("immersion_depth must be in range -3600 to 3600")

    if surface_following_distance is None:
      surface_following_distance = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in surface_following_distance):
      raise ValueError("surface_following_distance must be in range 0 to 3600")

    if tube_2nd_section_height_measured_from_zm is None:
      tube_2nd_section_height_measured_from_zm = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in tube_2nd_section_height_measured_from_zm):
      raise ValueError("tube_2nd_section_height_measured_from_zm must be in range 0 to 3600")

    if tube_2nd_section_ratio is None:
      tube_2nd_section_ratio = [0] * self.num_channels
    elif not all(0 <= x <= 10000 for x in tube_2nd_section_ratio):
      raise ValueError("tube_2nd_section_ratio must be in range 0 to 10000")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    if dispense_volume is None:
      dispense_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in dispense_volume):
      raise ValueError("dispense_volume must be in range 0 to 125000")

    if dispense_speed is None:
      dispense_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in dispense_speed):
      raise ValueError("dispense_speed must be in range 10 to 10000")

    if cut_off_speed is None:
      cut_off_speed = [250] * self.num_channels
    elif not all(10 <= x <= 10000 for x in cut_off_speed):
      raise ValueError("cut_off_speed must be in range 10 to 10000")

    if stop_back_volume is None:
      stop_back_volume = [0] * self.num_channels
    elif not all(0 <= x <= 180 for x in stop_back_volume):
      raise ValueError("stop_back_volume must be in range 0 to 180")

    if transport_air_volume is None:
      transport_air_volume = [0] * self.num_channels
    elif not all(0 <= x <= 500 for x in transport_air_volume):
      raise ValueError("transport_air_volume must be in range 0 to 500")

    if blow_out_air_volume is None:
      blow_out_air_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in blow_out_air_volume):
      raise ValueError("blow_out_air_volume must be in range 0 to 125000")

    if lld_mode is None:
      lld_mode = [1] * self.num_channels
    elif not all(0 <= x <= 4 for x in lld_mode):
      raise ValueError("lld_mode must be in range 0 to 4")

    if not 0 <= side_touch_off_distance <= 45:
      raise ValueError("side_touch_off_distance must be in range 0 to 45")

    if dispense_position_above_z_touch_off is None:
      dispense_position_above_z_touch_off = [5] * self.num_channels
    elif not all(0 <= x <= 100 for x in dispense_position_above_z_touch_off):
      raise ValueError("dispense_position_above_z_touch_off must be in range 0 to 100")

    if lld_sensitivity is None:
      lld_sensitivity = [1] * self.num_channels
    elif not all(1 <= x <= 4 for x in lld_sensitivity):
      raise ValueError("lld_sensitivity must be in range 1 to 4")

    if pressure_lld_sensitivity is None:
      pressure_lld_sensitivity = [1] * self.num_channels
    elif not all(1 <= x <= 4 for x in pressure_lld_sensitivity):
      raise ValueError("pressure_lld_sensitivity must be in range 1 to 4")

    if swap_speed is None:
      swap_speed = [100] * self.num_channels
    elif not all(3 <= x <= 1600 for x in swap_speed):
      raise ValueError("swap_speed must be in range 3 to 1600")

    if settling_time is None:
      settling_time = [5] * self.num_channels
    elif not all(0 <= x <= 99 for x in settling_time):
      raise ValueError("settling_time must be in range 0 to 99")

    if mix_volume is None:
      mix_volume = [0] * self.num_channels
    elif not all(0 <= x <= 12500 for x in mix_volume):
      raise ValueError("mix_volume must be in range 0 to 12500")

    if mix_cycles is None:
      mix_cycles = [0] * self.num_channels
    elif not all(0 <= x <= 99 for x in mix_cycles):
      raise ValueError("mix_cycles must be in range 0 to 99")

    if mix_position_in_z_direction_from_liquid_surface is None:
      mix_position_in_z_direction_from_liquid_surface = [250] * self.num_channels
    elif not all(0 <= x <= 900 for x in mix_position_in_z_direction_from_liquid_surface):
      raise ValueError("mix_position_in_z_direction_from_liquid_surface must be in range 0 to 900")

    if mix_speed is None:
      mix_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in mix_speed):
      raise ValueError("mix_speed must be in range 10 to 10000")

    if surface_following_distance_during_mixing is None:
      surface_following_distance_during_mixing = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in surface_following_distance_during_mixing):
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 3600")

    if TODO_DD_2 is None:
      TODO_DD_2 = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DD_2):
      raise ValueError("TODO_DD_2 must be in range 0 to 1")

    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")

    if limit_curve_index is None:
      limit_curve_index = [0] * self.num_channels
    elif not all(0 <= x <= 999 for x in limit_curve_index):
      raise ValueError("limit_curve_index must be in range 0 to 999")

    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    return await self._driver.send_command(
      module="A1PM",
      command="DD",
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
      dv=[f"{vol:04}" for vol in dispense_volume],  # it appears at least 4 digits are needed
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
      la=TODO_DD_2,
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index,
      gk=recording_mode,
    )

  async def simultaneous_aspiration_dispensation_of_liquid(
    self,
    x_position: List[int],
    y_position: List[int],
    type_of_aspiration: Optional[List[int]] = None,
    type_of_dispensing_mode: Optional[List[int]] = None,
    tip_pattern: Optional[List[bool]] = None,
    TODO_DM_1: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
    lld_search_height: Optional[List[int]] = None,
    clot_detection_height: Optional[List[int]] = None,
    liquid_surface_at_function_without_lld: Optional[List[int]] = None,
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[int]] = None,
    minimum_height: Optional[List[int]] = None,
    immersion_depth: Optional[List[int]] = None,
    surface_following_distance: Optional[List[int]] = None,
    tube_2nd_section_height_measured_from_zm: Optional[List[int]] = None,
    tube_2nd_section_ratio: Optional[List[int]] = None,
    aspiration_volume: Optional[List[int]] = None,
    TODO_DM_3: Optional[List[int]] = None,
    aspiration_speed: Optional[List[int]] = None,
    dispense_volume: Optional[List[int]] = None,
    dispense_speed: Optional[List[int]] = None,
    cut_off_speed: Optional[List[int]] = None,
    stop_back_volume: Optional[List[int]] = None,
    transport_air_volume: Optional[List[int]] = None,
    blow_out_air_volume: Optional[List[int]] = None,
    pre_wetting_volume: Optional[List[int]] = None,
    lld_mode: Optional[List[int]] = None,
    aspirate_position_above_z_touch_off: Optional[List[int]] = None,
    lld_sensitivity: Optional[List[int]] = None,
    pressure_lld_sensitivity: Optional[List[int]] = None,
    swap_speed: Optional[List[int]] = None,
    settling_time: Optional[List[int]] = None,
    mix_volume: Optional[List[int]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_in_z_direction_from_liquid_surface: Optional[List[int]] = None,
    mix_speed: Optional[List[int]] = None,
    surface_following_distance_during_mixing: Optional[List[int]] = None,
    TODO_DM_5: Optional[List[int]] = None,
    capacitive_mad_supervision_on_off: Optional[List[int]] = None,
    pressure_mad_supervision_on_off: Optional[List[int]] = None,
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
  ):
    """Simultaneous aspiration & dispensation of liquid

    Args:
      type_of_aspiration: Type of aspiration (0 = simple 1 = sequence 2 = cup emptied).
      type_of_dispensing_mode: Type of dispensing mode 0 = part in jet 1 = blow in jet 2 = Part at
          surface 3 = Blow at surface 4 = Empty.
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      TODO_DM_1: (0).
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      lld_search_height: LLD search height [0.1mm].
      clot_detection_height: (0).
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      pull_out_distance_to_take_transport_air_in_function_without_lld:
          Pull out distance to take transp. air in function without LLD [0.1mm]
        .
      minimum_height: Minimum height (maximum immersion depth) [0.1mm].
      immersion_depth: Immersion depth [0.1mm].
      surface_following_distance: Surface following distance [0.1mm].
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from zm [0.1mm].
      tube_2nd_section_ratio: Tube 2nd section ratio.
      aspiration_volume: Aspiration volume [0.01ul].
      TODO_DM_3: (0).
      aspiration_speed: Aspiration speed [0.1ul]/s.
      dispense_volume: Dispense volume [0.01ul].
      dispense_speed: Dispense speed [0.1ul/s].
      cut_off_speed: Cut off speed [0.1ul/s].
      stop_back_volume: Stop back volume [0.1ul].
      transport_air_volume: Transport air volume [0.1ul].
      blow_out_air_volume: Blow out air volume [0.01ul].
      pre_wetting_volume: Pre wetting volume [0.1ul].
      lld_mode: LLD Mode (0 = off).
      aspirate_position_above_z_touch_off: (0).
      lld_sensitivity: LLD sensitivity (1 = high, 4 = low).
      pressure_lld_sensitivity: Pressure LLD sensitivity (1= high, 4=low).
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s].
      settling_time: Settling time [0.1s].
      mix_volume: Mix volume [0.1ul].
      mix_cycles: Mix cycles.
      mix_position_in_z_direction_from_liquid_surface: Mix position in Z direction from liquid
        surface[0.1mm].
      mix_speed: Mix speed [0.1ul/s].
      surface_following_distance_during_mixing: Surface following distance during mixing [0.1mm].
      TODO_DM_5: (0).
      capacitive_mad_supervision_on_off: Capacitive MAD supervision on/off (0 = OFF).
      pressure_mad_supervision_on_off: Pressure MAD supervision on/off (0 = OFF).
      tadm_algorithm_on_off: TADM algorithm on/off (0 = off).
      limit_curve_index: Limit curve index.
      recording_mode:
          Recording mode (0 = no 1 = TADM errors only 2 = all TADM measurements)
        .
    """

    if type_of_aspiration is None:
      type_of_aspiration = [0] * self.num_channels
    elif not all(0 <= x <= 2 for x in type_of_aspiration):
      raise ValueError("type_of_aspiration must be in range 0 to 2")

    if type_of_dispensing_mode is None:
      type_of_dispensing_mode = [0] * self.num_channels
    elif not all(0 <= x <= 4 for x in type_of_dispensing_mode):
      raise ValueError("type_of_dispensing_mode must be in range 0 to 4")

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if TODO_DM_1 is None:
      TODO_DM_1 = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DM_1):
      raise ValueError("TODO_DM_1 must be in range 0 to 1")

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    if lld_search_height is None:
      lld_search_height = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in lld_search_height):
      raise ValueError("lld_search_height must be in range 0 to 3600")

    if clot_detection_height is None:
      clot_detection_height = [60] * self.num_channels
    elif not all(0 <= x <= 500 for x in clot_detection_height):
      raise ValueError("clot_detection_height must be in range 0 to 500")

    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in liquid_surface_at_function_without_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3600")

    if pull_out_distance_to_take_transport_air_in_function_without_lld is None:
      pull_out_distance_to_take_transport_air_in_function_without_lld = [50] * self.num_channels
    elif not all(
      0 <= x <= 3600 for x in pull_out_distance_to_take_transport_air_in_function_without_lld
    ):
      raise ValueError(
        "pull_out_distance_to_take_transport_air_in_function_without_lld must be in range 0 to 3600"
      )

    if minimum_height is None:
      minimum_height = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimum_height):
      raise ValueError("minimum_height must be in range 0 to 3600")

    if immersion_depth is None:
      immersion_depth = [0] * self.num_channels
    elif not all(-3600 <= x <= 3600 for x in immersion_depth):
      raise ValueError("immersion_depth must be in range -3600 to 3600")

    if surface_following_distance is None:
      surface_following_distance = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in surface_following_distance):
      raise ValueError("surface_following_distance must be in range 0 to 3600")

    if tube_2nd_section_height_measured_from_zm is None:
      tube_2nd_section_height_measured_from_zm = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in tube_2nd_section_height_measured_from_zm):
      raise ValueError("tube_2nd_section_height_measured_from_zm must be in range 0 to 3600")

    if tube_2nd_section_ratio is None:
      tube_2nd_section_ratio = [0] * self.num_channels
    elif not all(0 <= x <= 10000 for x in tube_2nd_section_ratio):
      raise ValueError("tube_2nd_section_ratio must be in range 0 to 10000")

    if aspiration_volume is None:
      aspiration_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in aspiration_volume):
      raise ValueError("aspiration_volume must be in range 0 to 125000")

    if TODO_DM_3 is None:
      TODO_DM_3 = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in TODO_DM_3):
      raise ValueError("TODO_DM_3 must be in range 0 to 125000")

    if aspiration_speed is None:
      aspiration_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in aspiration_speed):
      raise ValueError("aspiration_speed must be in range 10 to 10000")

    if dispense_volume is None:
      dispense_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in dispense_volume):
      raise ValueError("dispense_volume must be in range 0 to 125000")

    if dispense_speed is None:
      dispense_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in dispense_speed):
      raise ValueError("dispense_speed must be in range 10 to 10000")

    if cut_off_speed is None:
      cut_off_speed = [250] * self.num_channels
    elif not all(10 <= x <= 10000 for x in cut_off_speed):
      raise ValueError("cut_off_speed must be in range 10 to 10000")

    if stop_back_volume is None:
      stop_back_volume = [0] * self.num_channels
    elif not all(0 <= x <= 180 for x in stop_back_volume):
      raise ValueError("stop_back_volume must be in range 0 to 180")

    if transport_air_volume is None:
      transport_air_volume = [0] * self.num_channels
    elif not all(0 <= x <= 500 for x in transport_air_volume):
      raise ValueError("transport_air_volume must be in range 0 to 500")

    if blow_out_air_volume is None:
      blow_out_air_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in blow_out_air_volume):
      raise ValueError("blow_out_air_volume must be in range 0 to 125000")

    if pre_wetting_volume is None:
      pre_wetting_volume = [0] * self.num_channels
    elif not all(0 <= x <= 999 for x in pre_wetting_volume):
      raise ValueError("pre_wetting_volume must be in range 0 to 999")

    if lld_mode is None:
      lld_mode = [1] * self.num_channels
    elif not all(0 <= x <= 4 for x in lld_mode):
      raise ValueError("lld_mode must be in range 0 to 4")

    if aspirate_position_above_z_touch_off is None:
      aspirate_position_above_z_touch_off = [5] * self.num_channels
    elif not all(0 <= x <= 100 for x in aspirate_position_above_z_touch_off):
      raise ValueError("aspirate_position_above_z_touch_off must be in range 0 to 100")

    if lld_sensitivity is None:
      lld_sensitivity = [1] * self.num_channels
    elif not all(1 <= x <= 4 for x in lld_sensitivity):
      raise ValueError("lld_sensitivity must be in range 1 to 4")

    if pressure_lld_sensitivity is None:
      pressure_lld_sensitivity = [1] * self.num_channels
    elif not all(1 <= x <= 4 for x in pressure_lld_sensitivity):
      raise ValueError("pressure_lld_sensitivity must be in range 1 to 4")

    if swap_speed is None:
      swap_speed = [100] * self.num_channels
    elif not all(3 <= x <= 1600 for x in swap_speed):
      raise ValueError("swap_speed must be in range 3 to 1600")

    if settling_time is None:
      settling_time = [5] * self.num_channels
    elif not all(0 <= x <= 99 for x in settling_time):
      raise ValueError("settling_time must be in range 0 to 99")

    if mix_volume is None:
      mix_volume = [0] * self.num_channels
    elif not all(0 <= x <= 12500 for x in mix_volume):
      raise ValueError("mix_volume must be in range 0 to 12500")

    if mix_cycles is None:
      mix_cycles = [0] * self.num_channels
    elif not all(0 <= x <= 99 for x in mix_cycles):
      raise ValueError("mix_cycles must be in range 0 to 99")

    if mix_position_in_z_direction_from_liquid_surface is None:
      mix_position_in_z_direction_from_liquid_surface = [250] * self.num_channels
    elif not all(0 <= x <= 900 for x in mix_position_in_z_direction_from_liquid_surface):
      raise ValueError("mix_position_in_z_direction_from_liquid_surface must be in range 0 to 900")

    if mix_speed is None:
      mix_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in mix_speed):
      raise ValueError("mix_speed must be in range 10 to 10000")

    if surface_following_distance_during_mixing is None:
      surface_following_distance_during_mixing = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in surface_following_distance_during_mixing):
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 3600")

    if TODO_DM_5 is None:
      TODO_DM_5 = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DM_5):
      raise ValueError("TODO_DM_5 must be in range 0 to 1")

    if capacitive_mad_supervision_on_off is None:
      capacitive_mad_supervision_on_off = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in capacitive_mad_supervision_on_off):
      raise ValueError("capacitive_mad_supervision_on_off must be in range 0 to 1")

    if pressure_mad_supervision_on_off is None:
      pressure_mad_supervision_on_off = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in pressure_mad_supervision_on_off):
      raise ValueError("pressure_mad_supervision_on_off must be in range 0 to 1")

    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")

    if limit_curve_index is None:
      limit_curve_index = [0] * self.num_channels
    elif not all(0 <= x <= 999 for x in limit_curve_index):
      raise ValueError("limit_curve_index must be in range 0 to 999")

    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    return await self._driver.send_command(
      module="A1PM",
      command="DM",
      at=type_of_aspiration,
      dm=type_of_dispensing_mode,
      tm=tip_pattern,
      dd=TODO_DM_1,
      xp=x_position,
      yp=y_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      lp=lld_search_height,
      ch=clot_detection_height,
      zl=liquid_surface_at_function_without_lld,
      po=pull_out_distance_to_take_transport_air_in_function_without_lld,
      zx=minimum_height,
      ip=immersion_depth,
      fp=surface_following_distance,
      zu=tube_2nd_section_height_measured_from_zm,
      zr=tube_2nd_section_ratio,
      av=aspiration_volume,
      ar=TODO_DM_3,
      as_=aspiration_speed,
      dv=dispense_volume,
      ds=dispense_speed,
      ss=cut_off_speed,
      rv=stop_back_volume,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      oa=pre_wetting_volume,
      lm=lld_mode,
      zo=aspirate_position_above_z_touch_off,
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      de=swap_speed,
      wt=settling_time,
      mv=mix_volume,
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      ms=mix_speed,
      mh=surface_following_distance_during_mixing,
      la=TODO_DM_5,
      lb=capacitive_mad_supervision_on_off,
      lc=pressure_mad_supervision_on_off,
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index,
      gk=recording_mode,
    )

  async def dispense_on_fly(
    self,
    y_position: List[int],
    tip_pattern: Optional[List[bool]] = None,
    first_shoot_x_pos: int = 0,
    dispense_on_fly_pos_command_end: int = 0,
    x_acceleration_distance_before_first_shoot: int = 100,
    space_between_shoots: int = 900,
    x_speed: int = 270,
    number_of_shoots: int = 1,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
    liquid_surface_at_function_without_lld: Optional[List[int]] = None,
    dispense_volume: Optional[List[int]] = None,
    dispense_speed: Optional[List[int]] = None,
    cut_off_speed: Optional[List[int]] = None,
    stop_back_volume: Optional[List[int]] = None,
    transport_air_volume: Optional[List[int]] = None,
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
  ):
    """Dispense on fly

    Args:
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      first_shoot_x_pos: First shoot X-position [0.1mm]
      dispense_on_fly_pos_command_end: Dispense on fly position on command end [0.1mm]
      x_acceleration_distance_before_first_shoot: X- acceleration distance before first shoot
        [0.1mm] Space between shoots (raster pitch) [0.01mm]
      space_between_shoots: Space between shoots (raster pitch) [0.01mm]
      x_speed: X speed [0.1mm/s].
      number_of_shoots: Number of shoots
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      y_position: Y Position [0.1mm].
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      dispense_volume: Dispense volume [0.01ul].
      dispense_speed: Dispense speed [0.1ul/s].
      cut_off_speed: Cut off speed [0.1ul/s].
      stop_back_volume: Stop back volume [0.1ul].
      transport_air_volume: Transport air volume [0.1ul].
      tadm_algorithm_on_off: TADM algorithm on/off (0 = off).
      limit_curve_index: Limit curve index.
      recording_mode: Recording mode (0 = no 1 = TADM errors only 2 = all TADM measurements).
    """

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if not -50000 <= first_shoot_x_pos <= 50000:
      raise ValueError("first_shoot_x_pos must be in range -50000 to 50000")

    if not -50000 <= dispense_on_fly_pos_command_end <= 50000:
      raise ValueError("dispense_on_fly_pos_command_end must be in range -50000 to 50000")

    if not 0 <= x_acceleration_distance_before_first_shoot <= 900:
      raise ValueError("x_acceleration_distance_before_first_shoot must be in range 0 to 900")

    if not 1 <= space_between_shoots <= 2500:
      raise ValueError("space_between_shoots must be in range 1 to 2500")

    if not 20 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 20 to 25000")

    if not 1 <= number_of_shoots <= 48:
      raise ValueError("number_of_shoots must be in range 1 to 48")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in liquid_surface_at_function_without_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3600")

    if dispense_volume is None:
      dispense_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in dispense_volume):
      raise ValueError("dispense_volume must be in range 0 to 125000")

    if dispense_speed is None:
      dispense_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in dispense_speed):
      raise ValueError("dispense_speed must be in range 10 to 10000")

    if cut_off_speed is None:
      cut_off_speed = [250] * self.num_channels
    elif not all(10 <= x <= 10000 for x in cut_off_speed):
      raise ValueError("cut_off_speed must be in range 10 to 10000")

    if stop_back_volume is None:
      stop_back_volume = [0] * self.num_channels
    elif not all(0 <= x <= 180 for x in stop_back_volume):
      raise ValueError("stop_back_volume must be in range 0 to 180")

    if transport_air_volume is None:
      transport_air_volume = [0] * self.num_channels
    elif not all(0 <= x <= 500 for x in transport_air_volume):
      raise ValueError("transport_air_volume must be in range 0 to 500")

    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")

    if limit_curve_index is None:
      limit_curve_index = [0] * self.num_channels
    elif not all(0 <= x <= 999 for x in limit_curve_index):
      raise ValueError("limit_curve_index must be in range 0 to 999")

    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    return await self._driver.send_command(
      module="A1PM",
      command="DF",
      tm=tip_pattern,
      xa=first_shoot_x_pos,
      xf=dispense_on_fly_pos_command_end,
      xh=x_acceleration_distance_before_first_shoot,
      xy=space_between_shoots,
      xv=x_speed,
      xi=number_of_shoots,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      yp=y_position,
      zl=liquid_surface_at_function_without_lld,
      dv=dispense_volume,
      ds=dispense_speed,
      ss=cut_off_speed,
      rv=stop_back_volume,
      ta=transport_air_volume,
      gj=tadm_algorithm_on_off,
      gi=limit_curve_index,
      gk=recording_mode,
    )

  async def nano_pulse_dispense(
    self,
    x_position: List[int],
    y_position: List[int],
    TODO_DB_0: Optional[List[int]] = None,
    liquid_surface_at_function_without_lld: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
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
    TODO_DB_11: Optional[List[int]] = None,
    TODO_DB_12: Optional[List[int]] = None,
  ):
    """Nano pulse dispense

    Args:
      TODO_DB_0: (0).
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      TODO_DB_1: (0).
      TODO_DB_2: (0).
      TODO_DB_3: (0).
      TODO_DB_4: (0).
      TODO_DB_5: (0).
      TODO_DB_6: (0).
      TODO_DB_7: (0).
      TODO_DB_8: (0).
      TODO_DB_9: (0).
      TODO_DB_10: (0).
      TODO_DB_11: (0).
      TODO_DB_12: (0).
    """

    if TODO_DB_0 is None:
      TODO_DB_0 = [1] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DB_0):
      raise ValueError("TODO_DB_0 must be in range 0 to 1")

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in liquid_surface_at_function_without_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3600")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    if TODO_DB_1 is None:
      TODO_DB_1 = [0] * self.num_channels
    elif not all(0 <= x <= 20000 for x in TODO_DB_1):
      raise ValueError("TODO_DB_1 must be in range 0 to 20000")

    if TODO_DB_2 is None:
      TODO_DB_2 = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DB_2):
      raise ValueError("TODO_DB_2 must be in range 0 to 1")

    if TODO_DB_3 is None:
      TODO_DB_3 = [0] * self.num_channels
    elif not all(0 <= x <= 10000 for x in TODO_DB_3):
      raise ValueError("TODO_DB_3 must be in range 0 to 10000")

    if TODO_DB_4 is None:
      TODO_DB_4 = [0] * self.num_channels
    elif not all(0 <= x <= 100 for x in TODO_DB_4):
      raise ValueError("TODO_DB_4 must be in range 0 to 100")

    if TODO_DB_5 is None:
      TODO_DB_5 = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DB_5):
      raise ValueError("TODO_DB_5 must be in range 0 to 1")

    if TODO_DB_6 is None:
      TODO_DB_6 = [0] * self.num_channels
    elif not all(0 <= x <= 10000 for x in TODO_DB_6):
      raise ValueError("TODO_DB_6 must be in range 0 to 10000")

    if TODO_DB_7 is None:
      TODO_DB_7 = [0] * self.num_channels
    elif not all(0 <= x <= 100 for x in TODO_DB_7):
      raise ValueError("TODO_DB_7 must be in range 0 to 100")

    if TODO_DB_8 is None:
      TODO_DB_8 = [0] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DB_8):
      raise ValueError("TODO_DB_8 must be in range 0 to 1")

    if TODO_DB_9 is None:
      TODO_DB_9 = [0] * self.num_channels
    elif not all(0 <= x <= 10000 for x in TODO_DB_9):
      raise ValueError("TODO_DB_9 must be in range 0 to 10000")

    if TODO_DB_10 is None:
      TODO_DB_10 = [0] * self.num_channels
    elif not all(0 <= x <= 100 for x in TODO_DB_10):
      raise ValueError("TODO_DB_10 must be in range 0 to 100")

    if TODO_DB_11 is None:
      TODO_DB_11 = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in TODO_DB_11):
      raise ValueError("TODO_DB_11 must be in range 0 to 3600")

    if TODO_DB_12 is None:
      TODO_DB_12 = [1] * self.num_channels
    elif not all(0 <= x <= 1 for x in TODO_DB_12):
      raise ValueError("TODO_DB_12 must be in range 0 to 1")

    return await self._driver.send_command(
      module="A1PM",
      command="DB",
      tm=TODO_DB_0,
      xp=x_position,
      yp=y_position,
      zl=liquid_surface_at_function_without_lld,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
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
      pi=TODO_DB_11,
      pm=TODO_DB_12,
    )

  async def wash_tips(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: Optional[List[bool]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    liquid_surface_at_function_without_lld: Optional[List[int]] = None,
    aspiration_volume: Optional[List[int]] = None,
    aspiration_speed: Optional[List[int]] = None,
    dispense_speed: Optional[List[int]] = None,
    swap_speed: Optional[List[int]] = None,
    soak_time: int = 0,
    wash_cycles: int = 0,
    minimal_height_at_command_end: Optional[List[int]] = None,
  ):
    """Wash tips

    Args:
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      aspiration_volume: Aspiration volume [0.01ul].
      aspiration_speed: Aspiration speed [0.1ul]/s.
      dispense_speed: Dispense speed [0.1ul/s].
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s].
      soak_time: (0).
      wash_cycles: (0).
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in liquid_surface_at_function_without_lld):
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3600")

    if aspiration_volume is None:
      aspiration_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in aspiration_volume):
      raise ValueError("aspiration_volume must be in range 0 to 125000")

    if aspiration_speed is None:
      aspiration_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in aspiration_speed):
      raise ValueError("aspiration_speed must be in range 10 to 10000")

    if dispense_speed is None:
      dispense_speed = [500] * self.num_channels
    elif not all(10 <= x <= 10000 for x in dispense_speed):
      raise ValueError("dispense_speed must be in range 10 to 10000")

    if swap_speed is None:
      swap_speed = [100] * self.num_channels
    elif not all(3 <= x <= 1600 for x in swap_speed):
      raise ValueError("swap_speed must be in range 3 to 1600")

    if not 0 <= soak_time <= 3600:
      raise ValueError("soak_time must be in range 0 to 3600")

    if not 0 <= wash_cycles <= 99:
      raise ValueError("wash_cycles must be in range 0 to 99")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DW",
      tm=tip_pattern,
      xp=x_position,
      yp=y_position,
      th=minimal_traverse_height_at_begin_of_command,
      zl=liquid_surface_at_function_without_lld,
      av=aspiration_volume,
      as_=aspiration_speed,
      ds=dispense_speed,
      de=swap_speed,
      sa=soak_time,
      dc=wash_cycles,
      te=minimal_height_at_command_end,
    )

  async def pip_tip_pick_up(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: Optional[List[bool]] = None,
    tip_type: Optional[List[int]] = None,
    begin_z_deposit_position: Optional[List[int]] = None,
    end_z_deposit_position: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
    blow_out_air_volume: Optional[List[int]] = None,
    tip_handling_method: Optional[List[int]] = None,
  ):
    """Tip Pick up

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      tip_type: Tip type (see command TT).
      begin_z_deposit_position: (0).
      end_z_deposit_position: Z deposit position [0.1mm] (collar bearing position).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
       [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      blow_out_air_volume: Blow out air volume [0.01ul].
      tip_handling_method: Tip handling method. (Unconfirmed, but likely: 0 = auto selection (see
        command TT parameter tu), 1 = pick up out of rack, 2 = pick up out of wash liquid (slowly))
    """

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if tip_type is None:
      tip_type = [4] * self.num_channels
    elif not all(0 <= x <= 199 for x in tip_type):
      raise ValueError("tip_type must be in range 0 to 199")

    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in begin_z_deposit_position):
      raise ValueError("begin_z_deposit_position must be in range 0 to 3600")

    if end_z_deposit_position is None:
      end_z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in end_z_deposit_position):
      raise ValueError("end_z_deposit_position must be in range 0 to 3600")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    if blow_out_air_volume is None:
      blow_out_air_volume = [0] * self.num_channels
    elif not all(0 <= x <= 125000 for x in blow_out_air_volume):
      raise ValueError("blow_out_air_volume must be in range 0 to 125000")

    if tip_handling_method is None:
      tip_handling_method = [0] * self.num_channels
    elif not all(0 <= x <= 9 for x in tip_handling_method):
      raise ValueError("tip_handling_method must be in range 0 to 9")

    return await self._driver.send_command(
      module="A1PM",
      command="TP",
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

  async def pip_tip_discard(
    self,
    x_position: List[int],
    y_position: List[int],
    begin_z_deposit_position: Optional[List[int]] = None,
    end_z_deposit_position: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
    tip_pattern: Optional[List[bool]] = None,
    TODO_TR_2: int = 0,
    tip_handling_method: Optional[List[int]] = None,
  ):
    """Tip Discard

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      begin_z_deposit_position: (0).
      end_z_deposit_position: Z deposit position [0.1mm] (collar bearing position).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      TODO_TR_2: (0).
      tip_handling_method: Tip handling method.
    """

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in begin_z_deposit_position):
      raise ValueError("begin_z_deposit_position must be in range 0 to 3600")

    if end_z_deposit_position is None:
      end_z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in end_z_deposit_position):
      raise ValueError("end_z_deposit_position must be in range 0 to 3600")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if not -1000 <= TODO_TR_2 <= 1000:
      raise ValueError("TODO_TR_2 must be in range -1000 to 1000")

    if tip_handling_method is None:
      tip_handling_method = [0] * self.num_channels
    elif not all(0 <= x <= 9 for x in tip_handling_method):
      raise ValueError("tip_handling_method must be in range 0 to 9")

    return await self._driver.send_command(
      module="A1PM",
      command="TR",
      xp=x_position,
      yp=y_position,
      tp=begin_z_deposit_position,
      tz=end_z_deposit_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      tm=tip_pattern,
      ts=TODO_TR_2,
      td=tip_handling_method,
    )

  # ===========================================================================
  # Positioning / teach-in methods
  # ===========================================================================

  async def search_for_teach_in_signal_in_x_direction(
    self,
    channel_index: int = 1,
    x_search_distance: int = 0,
    x_speed: int = 270,
  ):
    """Search for Teach in signal in X direction

    Args:
      channel_index: Channel index.
      x_search_distance: X search distance [0.1mm].
      x_speed: X speed [0.1mm/s].
    """

    if not 1 <= channel_index <= 16:
      raise ValueError("channel_index must be in range 1 to 16")

    if not -50000 <= x_search_distance <= 50000:
      raise ValueError("x_search_distance must be in range -50000 to 50000")

    if not 20 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 20 to 25000")

    return await self._driver.send_command(
      module="A1PM",
      command="DL",
      pn=channel_index,
      xs=x_search_distance,
      xv=x_speed,
    )

  async def position_all_channels_in_y_direction(
    self,
    y_position: List[int],
  ):
    """Position all channels in Y direction

    Args:
      y_position: Y Position [0.1mm].
    """

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    return await self._driver.send_command(
      module="A1PM",
      command="DY",
      yp=y_position,
    )

  async def position_all_channels_in_z_direction(
    self,
    z_position: Optional[List[int]] = None,
  ):
    """Position all channels in Z direction

    Args:
      z_position: Z Position [0.1mm].
    """

    if z_position is None:
      z_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in z_position):
      raise ValueError("z_position must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DZ",
      zp=z_position,
    )

  async def position_single_channel_in_y_direction(
    self,
    channel_index: int = 1,
    y_position: int = 3000,
  ):
    """Position single channel in Y direction

    Args:
      channel_index: Channel index.
      y_position: Y Position [0.1mm].
    """

    if not 1 <= channel_index <= 16:
      raise ValueError("channel_index must be in range 1 to 16")

    if not 0 <= y_position <= 6500:
      raise ValueError("y_position must be in range 0 to 6500")

    return await self._driver.send_command(
      module="A1PM",
      command="DV",
      pn=channel_index,
      yj=y_position,
    )

  async def position_single_channel_in_z_direction(
    self,
    channel_index: int = 1,
    z_position: int = 0,
  ):
    """Position single channel in Z direction

    Args:
      channel_index: Channel index.
      z_position: Z Position [0.1mm].
    """

    if not 1 <= channel_index <= 16:
      raise ValueError("channel_index must be in range 1 to 16")

    if not 0 <= z_position <= 3600:
      raise ValueError("z_position must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DU",
      pn=channel_index,
      zj=z_position,
    )

  async def move_to_defined_position(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: Optional[List[bool]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    z_position: Optional[List[int]] = None,
  ):
    """Move to defined position

    Args:
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      z_position: Z Position [0.1mm].
    """

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    elif not all(0 <= x <= 1 for x in tip_pattern):
      raise ValueError("tip_pattern must be in range 0 to 1")

    if not all(0 <= x <= 50000 for x in x_position):
      raise ValueError("x_position must be in range 0 to 50000")

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if z_position is None:
      z_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in z_position):
      raise ValueError("z_position must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DN",
      tm=tip_pattern,
      xp=x_position,
      yp=y_position,
      th=minimal_traverse_height_at_begin_of_command,
      zp=z_position,
    )

  async def teach_rack_using_channel_n(
    self,
    channel_index: int = 1,
    gap_center_x_direction: int = 0,
    gap_center_y_direction: int = 3000,
    gap_center_z_direction: int = 0,
    minimal_height_at_command_end: Optional[List[int]] = None,
  ):
    """Teach rack using channel n

    Attention! Channels not involved must first be taken out of measurement range.

    Args:
      channel_index: Channel index.
      gap_center_x_direction: Gap center X direction [0.1mm].
      gap_center_y_direction: Gap center Y direction [0.1mm].
      gap_center_z_direction: Gap center Z direction [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """

    if not 1 <= channel_index <= 16:
      raise ValueError("channel_index must be in range 1 to 16")

    if not -50000 <= gap_center_x_direction <= 50000:
      raise ValueError("gap_center_x_direction must be in range -50000 to 50000")

    if not 0 <= gap_center_y_direction <= 6500:
      raise ValueError("gap_center_y_direction must be in range 0 to 6500")

    if not 0 <= gap_center_z_direction <= 3600:
      raise ValueError("gap_center_z_direction must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DT",
      pn=channel_index,
      xa=gap_center_x_direction,
      yj=gap_center_y_direction,
      zj=gap_center_z_direction,
      te=minimal_height_at_command_end,
    )

  async def expose_channel_n(
    self,
    channel_index: int = 1,
  ):
    """Expose channel n

    Args:
      channel_index: Channel index.
    """

    if not 1 <= channel_index <= 16:
      raise ValueError("channel_index must be in range 1 to 16")

    return await self._driver.send_command(
      module="A1PM",
      command="DQ",
      pn=channel_index,
    )

  async def calculates_check_sums_and_compares_them_with_the_value_saved_in_flash_eprom(
    self,
    TODO_DC_0: int = 0,
    TODO_DC_1: int = 3000,
    tip_type: Optional[List[int]] = None,
    TODO_DC_2: Optional[List[int]] = None,
    z_deposit_position: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    first_pip_channel_node_no: int = 1,
  ):
    """Calculates check sums and compares them with the value saved in Flash EPROM

    Args:
      TODO_DC_0: (0).
      TODO_DC_1: (0).
      tip_type: Tip type (see command TT).
      TODO_DC_2: (0).
      z_deposit_position: Z deposit position [0.1mm] (collar bearing position).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      first_pip_channel_node_no: First (lower) pip. channel node no. (0 = disabled).
    """

    if not -50000 <= TODO_DC_0 <= 50000:
      raise ValueError("TODO_DC_0 must be in range -50000 to 50000")

    if not 0 <= TODO_DC_1 <= 6500:
      raise ValueError("TODO_DC_1 must be in range 0 to 6500")

    if tip_type is None:
      tip_type = [4] * self.num_channels
    elif not all(0 <= x <= 199 for x in tip_type):
      raise ValueError("tip_type must be in range 0 to 199")

    if TODO_DC_2 is None:
      TODO_DC_2 = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in TODO_DC_2):
      raise ValueError("TODO_DC_2 must be in range 0 to 3600")

    if z_deposit_position is None:
      z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in z_deposit_position):
      raise ValueError("z_deposit_position must be in range 0 to 3600")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if not 1 <= first_pip_channel_node_no <= 16:
      raise ValueError("first_pip_channel_node_no must be in range 1 to 16")

    return await self._driver.send_command(
      module="A1PM",
      command="DC",
      xa=TODO_DC_0,
      yj=TODO_DC_1,
      tt=tip_type,
      tp=TODO_DC_2,
      tz=z_deposit_position,
      th=minimal_traverse_height_at_begin_of_command,
      pa=first_pip_channel_node_no,
    )

  async def discard_core_gripper_tool(
    self,
    gripper_tool_x_position: int = 0,
    first_gripper_tool_y_pos: int = 3000,
    tip_type: Optional[List[int]] = None,
    begin_z_deposit_position: Optional[List[int]] = None,
    end_z_deposit_position: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    first_pip_channel_node_no: int = 1,
    minimal_height_at_command_end: Optional[List[int]] = None,
  ):
    """Discard CoRe gripper tool

    Args:
      gripper_tool_x_position: (0).
      first_gripper_tool_y_pos: First (lower channel) CoRe gripper tool Y pos. [0.1mm]
      tip_type: Tip type (see command TT).
      begin_z_deposit_position: (0).
      end_z_deposit_position: Z deposit position [0.1mm] (collar bearing position).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      first_pip_channel_node_no: First (lower) pip. channel node no. (0 = disabled).
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """

    if not -50000 <= gripper_tool_x_position <= 50000:
      raise ValueError("gripper_tool_x_position must be in range -50000 to 50000")

    if not 0 <= first_gripper_tool_y_pos <= 6500:
      raise ValueError("first_gripper_tool_y_pos must be in range 0 to 6500")

    if tip_type is None:
      tip_type = [4] * self.num_channels
    elif not all(0 <= x <= 199 for x in tip_type):
      raise ValueError("tip_type must be in range 0 to 199")

    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in begin_z_deposit_position):
      raise ValueError("begin_z_deposit_position must be in range 0 to 3600")

    if end_z_deposit_position is None:
      end_z_deposit_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in end_z_deposit_position):
      raise ValueError("end_z_deposit_position must be in range 0 to 3600")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if not 1 <= first_pip_channel_node_no <= 16:
      raise ValueError("first_pip_channel_node_no must be in range 1 to 16")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DJ",
      xa=gripper_tool_x_position,
      yj=first_gripper_tool_y_pos,
      tt=tip_type,
      tp=begin_z_deposit_position,
      tz=end_z_deposit_position,
      th=minimal_traverse_height_at_begin_of_command,
      pa=first_pip_channel_node_no,
      te=minimal_height_at_command_end,
    )

  # ===========================================================================
  # PIP gripper methods (CoRe gripper on pip channels)
  # ===========================================================================

  async def grip_plate(
    self,
    plate_center_x_direction: int = 0,
    plate_center_y_direction: int = 3000,
    plate_center_z_direction: int = 0,
    z_speed: int = 1287,
    open_gripper_position: int = 860,
    plate_width: int = 800,
    acceleration_index: int = 4,
    grip_strength: int = 30,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
  ):
    """Grip plate

    Args:
      plate_center_x_direction: Plate center X direction [0.1mm].
      plate_center_y_direction: Plate center Y direction [0.1mm].
      plate_center_z_direction: Plate center Z direction [0.1mm].
      z_speed: Z speed [0.1mm/sec].
      open_gripper_position: Open gripper position [0.1mm].
      plate_width: Plate width [0.1mm].
      acceleration_index: Acceleration index.
      grip_strength: Grip strength (0 = low 99 = high).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """

    if not -50000 <= plate_center_x_direction <= 50000:
      raise ValueError("plate_center_x_direction must be in range -50000 to 50000")

    if not 0 <= plate_center_y_direction <= 6500:
      raise ValueError("plate_center_y_direction must be in range 0 to 6500")

    if not 0 <= plate_center_z_direction <= 3600:
      raise ValueError("plate_center_z_direction must be in range 0 to 3600")

    if not 3 <= z_speed <= 1600:
      raise ValueError("z_speed must be in range 3 to 1600")

    if not 0 <= open_gripper_position <= 9999:
      raise ValueError("open_gripper_position must be in range 0 to 9999")

    if not 0 <= plate_width <= 9999:
      raise ValueError("plate_width must be in range 0 to 9999")

    if not 0 <= acceleration_index <= 4:
      raise ValueError("acceleration_index must be in range 0 to 4")

    if not 0 <= grip_strength <= 99:
      raise ValueError("grip_strength must be in range 0 to 99")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DG",
      xa=plate_center_x_direction,
      yj=plate_center_y_direction,
      zj=plate_center_z_direction,
      zy=z_speed,
      yo=open_gripper_position,
      yg=plate_width,
      ai=acceleration_index,
      yw=grip_strength,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
    )

  async def put_plate(
    self,
    plate_center_x_direction: int = 0,
    plate_center_y_direction: int = 3000,
    plate_center_z_direction: int = 0,
    press_on_distance: int = 5,
    z_speed: int = 1287,
    open_gripper_position: int = 860,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
  ):
    """Put plate

    Args:
      plate_center_x_direction: Plate center X direction [0.1mm].
      plate_center_y_direction: Plate center Y direction [0.1mm].
      plate_center_z_direction: Plate center Z direction [0.1mm].
      press_on_distance: Press on distance [0.1mm].
      z_speed: Z speed [0.1mm/sec].
      open_gripper_position: Open gripper position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """

    if not -50000 <= plate_center_x_direction <= 50000:
      raise ValueError("plate_center_x_direction must be in range -50000 to 50000")

    if not 0 <= plate_center_y_direction <= 6500:
      raise ValueError("plate_center_y_direction must be in range 0 to 6500")

    if not 0 <= plate_center_z_direction <= 3600:
      raise ValueError("plate_center_z_direction must be in range 0 to 3600")

    if not 0 <= press_on_distance <= 999:
      raise ValueError("press_on_distance must be in range 0 to 999")

    if not 3 <= z_speed <= 1600:
      raise ValueError("z_speed must be in range 3 to 1600")

    if not 0 <= open_gripper_position <= 9999:
      raise ValueError("open_gripper_position must be in range 0 to 9999")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_height_at_command_end):
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DR",
      xa=plate_center_x_direction,
      yj=plate_center_y_direction,
      zj=plate_center_z_direction,
      zi=press_on_distance,
      zy=z_speed,
      yo=open_gripper_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
    )

  async def move_to_position(
    self,
    plate_center_x_direction: int = 0,
    plate_center_y_direction: int = 3000,
    plate_center_z_direction: int = 0,
    z_speed: int = 1287,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
  ):
    """Move to position

    Args:
      plate_center_x_direction: Plate center X direction [0.1mm].
      plate_center_y_direction: Plate center Y direction [0.1mm].
      plate_center_z_direction: Plate center Z direction [0.1mm].
      z_speed: Z speed [0.1mm/sec].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
    """

    if not -50000 <= plate_center_x_direction <= 50000:
      raise ValueError("plate_center_x_direction must be in range -50000 to 50000")

    if not 0 <= plate_center_y_direction <= 6500:
      raise ValueError("plate_center_y_direction must be in range 0 to 6500")

    if not 0 <= plate_center_z_direction <= 3600:
      raise ValueError("plate_center_z_direction must be in range 0 to 3600")

    if not 3 <= z_speed <= 1600:
      raise ValueError("z_speed must be in range 3 to 1600")

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    elif not all(0 <= x <= 3600 for x in minimal_traverse_height_at_begin_of_command):
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3600")

    return await self._driver.send_command(
      module="A1PM",
      command="DH",
      xa=plate_center_x_direction,
      yj=plate_center_y_direction,
      zj=plate_center_z_direction,
      zy=z_speed,
      th=minimal_traverse_height_at_begin_of_command,
    )

  async def release_object(
    self,
    first_pip_channel_node_no: int = 1,
  ):
    """Release object

    Args:
      first_pip_channel_node_no: First (lower) pip. channel node no. (0 = disabled).
    """

    if not 1 <= first_pip_channel_node_no <= 16:
      raise ValueError("first_pip_channel_node_no must be in range 1 to 16")

    return await self._driver.send_command(
      module="A1PM",
      command="DO",
      pa=first_pip_channel_node_no,
    )

  # ===========================================================================
  # Query methods
  # ===========================================================================

  async def set_any_parameter_within_this_module(self):
    """Set any parameter within this module"""

    return await self._driver.send_command(
      module="A1PM",
      command="AA",
    )

  async def request_y_positions_of_all_channels(self):
    """Request Y Positions of all channels"""

    return await self._driver.send_command(
      module="A1PM",
      command="RY",
    )

  async def request_y_position_of_channel_n(self, channel_index: int = 1):
    """Request Y Position of channel n"""

    return await self._driver.send_command(
      module="A1PM",
      command="RB",
      pn=channel_index,
    )

  async def request_z_positions_of_all_channels(self):
    """Request Z Positions of all channels"""

    return await self._driver.send_command(
      module="A1PM",
      command="RZ",
    )

  async def request_z_position_of_channel_n(self, channel_index: int = 1):
    """Request Z Position of channel n"""

    return await self._driver.send_command(
      module="A1PM",
      command="RD",
      pn=channel_index,
    )

  async def request_height_of_last_lld(self):
    """Request height of last LLD"""

    return await self._driver.send_command(
      module="A1PM",
      command="RL",
    )

  async def request_channel_dispense_on_fly_status(self):
    """Request channel dispense on fly status"""

    return await self._driver.send_command(
      module="A1PM",
      command="QF",
    )
