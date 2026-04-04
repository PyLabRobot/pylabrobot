"""Vantage Head96 backend: translates Head96 operations into Vantage firmware commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.head96_backend import Head96Backend
from pylabrobot.capabilities.liquid_handling.standard import (
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  PickupTipRack,
)
from pylabrobot.hamilton.lh.vantage.liquid_classes import get_vantage_liquid_class
from pylabrobot.hamilton.liquid_handlers.liquid_class import HamiltonLiquidClass
from pylabrobot.resources import Coordinate, Plate, TipRack
from pylabrobot.resources.hamilton import HamiltonTip
from pylabrobot.resources.liquid import Liquid

from .errors import VantageFirmwareError, convert_vantage_firmware_error_to_plr_error
from .pip_backend import _get_dispense_mode

if TYPE_CHECKING:
  from .driver import VantageDriver

logger = logging.getLogger("pylabrobot")


def _channel_pattern_to_hex(pattern: List[bool]) -> str:
  """Convert a list of 96 booleans to the hex string expected by the Vantage firmware.

  The Vantage 96-head firmware commands accept a channel mask as a hexadecimal string.
  Each boolean in the list represents one channel of the 96-head (A1 through H12).
  The list is reversed to match firmware bit ordering (LSB first), then converted
  to a hex string with the leading '0x' prefix stripped.

  Args:
    pattern: List of exactly 96 booleans. True = channel active, False = channel inactive.

  Returns:
    Uppercase hexadecimal string (e.g. ``"FFFFFFFFFFFFFFFFFFFFFFFF"`` for all 96 active).

  Raises:
    ValueError: If the list does not contain exactly 96 elements.
  """
  if len(pattern) != 96:
    raise ValueError("channel_pattern must be a list of 96 boolean values")
  channel_pattern_bin_str = reversed(["1" if x else "0" for x in pattern])
  return hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]


class VantageHead96Backend(Head96Backend):
  """Translates Head96 operations into Vantage firmware commands via the driver.

  This backend implements the ``Head96Backend`` interface for the Hamilton Vantage.
  It converts high-level 96-head operations (``pick_up_tips96``, ``drop_tips96``,
  ``aspirate96``, ``dispense96``) into low-level firmware commands on the A1HM module,
  handling coordinate conversion, liquid class resolution, volume correction, and
  Z-height computation.

  Each public method accepts an optional ``BackendParams`` dataclass that exposes
  Vantage-specific parameters. When these parameters are None, sensible defaults
  are computed from resource geometry, liquid classes, and the driver's
  ``traversal_height``.
  """

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver

  async def _on_setup(self):
    """Check Core96 initialization status and initialize if needed."""
    core96_initialized = await self.driver.core96_request_initialization_status()
    if not core96_initialized:
      th = self.driver.traversal_height
      await self.driver.core96_initialize(
        x_position=734.7,
        y_position=268.4,
        minimal_traverse_height_at_begin_of_command=th,
        minimal_height_at_command_end=th,
        end_z_deposit_position=242.0,
      )

  async def _on_stop(self):
    pass

  # -- BackendParams ---------------------------------------------------------

  @dataclass
  class PickUpTipsParams(BackendParams):
    """Vantage-specific parameters for ``pick_up_tips96``.

    Args:
      tip_handling_method: Tip handling method code (0 = normal, 1 = side touch).
        Default 0.
      z_deposit_position: Z deposit position in mm (collar bearing position) for the
        96-head. Default 216.4.
      minimal_traverse_height_at_begin_of_command: Minimum Z clearance in mm before
        lateral movement begins. If None, uses the driver's ``traversal_height``.
        Must be between 0 and 360.0.
      minimal_height_at_command_end: Minimum Z height in mm at the end of the command.
        If None, uses the driver's ``traversal_height``. Must be between 0 and 360.0.
    """

    tip_handling_method: int = 0
    z_deposit_position: float = 216.4
    minimal_traverse_height_at_begin_of_command: Optional[float] = None
    minimal_height_at_command_end: Optional[float] = None

  @dataclass
  class DropTipsParams(BackendParams):
    """Vantage-specific parameters for ``drop_tips96``.

    Args:
      z_deposit_position: Z deposit position in mm (collar bearing position) for the
        96-head. Default 216.4.
      minimal_traverse_height_at_begin_of_command: Minimum Z clearance in mm before
        lateral movement begins. If None, uses the driver's ``traversal_height``.
        Must be between 0 and 360.0.
      minimal_height_at_command_end: Minimum Z height in mm at the end of the command.
        If None, uses the driver's ``traversal_height``. Must be between 0 and 360.0.
    """

    z_deposit_position: float = 216.4
    minimal_traverse_height_at_begin_of_command: Optional[float] = None
    minimal_height_at_command_end: Optional[float] = None

  @dataclass
  class AspirateParams(BackendParams):
    """Vantage-specific parameters for ``aspirate96``.

    Unlike PIP parameters, these are scalar (not per-channel lists) because the 96-head
    operates all channels identically in a single firmware command.

    Args:
      jet: Flag used for liquid class selection. If True, selects a jet-mode liquid
        class (aspirate from above the liquid surface). Default False.
      blow_out: Flag used for liquid class selection. If True, selects a blow-out
        liquid class. Default False.
      hlc: Hamilton liquid class override. If None, auto-detected from tip type and
        liquid.
      type_of_aspiration: Type of aspiration (0 = simple, 1 = sequence,
        2 = cup emptied). Default 0.
      minimal_traverse_height_at_begin_of_command: Minimum Z clearance in mm before
        lateral movement begins. If None, uses the driver's ``traversal_height``.
        Must be between 0 and 360.0.
      minimal_height_at_command_end: Minimum Z height in mm at the end of the command.
        If None, uses the driver's ``traversal_height``. Must be between 0 and 360.0.
      pull_out_distance_to_take_transport_air_in_function_without_lld: Distance in mm
        to pull out for transport air when not using LLD. Default 5.0.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from
        minimum height in mm. Used for conical tubes. Default 0.
      tube_2nd_section_ratio: Tube 2nd section ratio: (bottom diameter * 10000) / top
        diameter. Default 0.
      immersion_depth: Immersion depth in mm. Positive = deeper into liquid.
        Default 0.
      surface_following_distance: Surface following distance during aspiration in mm.
        Default 0.
      transport_air_volume: Transport air volume in uL. If None, uses the liquid class
        default.
      blow_out_air_volume: Blow-out air volume in uL. If None, uses the liquid class
        default.
      pre_wetting_volume: Pre-wetting volume in uL. Default 0.
      lld_mode: LLD mode as integer (0 = OFF, 1 = GAMMA, 2 = PRESSURE, 3 = DUAL,
        4 = Z_TOUCH_OFF). Default 0 (OFF).
      lld_sensitivity: Capacitive LLD sensitivity (1 = high, 4 = low). Default 4.
      swap_speed: Swap speed (on leaving the liquid surface) in mm/s. If None, uses
        the liquid class default.
      settling_time: Settling time in seconds after aspiration completes. If None,
        uses the liquid class default.
      limit_curve_index: TADM limit curve index. Must be between 0 and 999. Default 0.
      tadm_channel_pattern: List of 96 booleans selecting which channels participate in
        TADM monitoring. If None, all 96 channels are active.
      tadm_algorithm_on_off: TADM algorithm (0 = off, 1 = on). Default 0.
      recording_mode: Recording mode for TADM (0 = no recording, 1 = TADM errors only,
        2 = all TADM measurements). Default 0.
      disable_volume_correction: If True, skip liquid-class volume correction.
        Default False.
    """

    jet: bool = False
    blow_out: bool = False
    hlc: Optional[HamiltonLiquidClass] = None
    type_of_aspiration: int = 0
    minimal_traverse_height_at_begin_of_command: Optional[float] = None
    minimal_height_at_command_end: Optional[float] = None
    pull_out_distance_to_take_transport_air_in_function_without_lld: float = 5
    tube_2nd_section_height_measured_from_zm: float = 0
    tube_2nd_section_ratio: float = 0
    immersion_depth: float = 0
    surface_following_distance: float = 0
    transport_air_volume: Optional[float] = None
    blow_out_air_volume: Optional[float] = None
    pre_wetting_volume: float = 0
    lld_mode: int = 0
    lld_sensitivity: int = 4
    swap_speed: Optional[float] = None
    settling_time: Optional[float] = None
    limit_curve_index: int = 0
    tadm_channel_pattern: Optional[List[bool]] = None
    tadm_algorithm_on_off: int = 0
    recording_mode: int = 0
    disable_volume_correction: bool = False

  @dataclass
  class DispenseParams(BackendParams):
    """Vantage-specific parameters for ``dispense96``.

    Unlike PIP parameters, these are scalar (not per-channel lists) because the 96-head
    operates all channels identically in a single firmware command.

    Args:
      jet: Flag used for liquid class selection. If True, selects a jet-mode liquid
        class (dispense from above the liquid surface). Default False.
      blow_out: Flag used for liquid class selection. If True, selects a blow-out
        liquid class. Default False.
      empty: If True, empty the tip completely at a fixed position (firmware mode 4).
        Default False.
      hlc: Hamilton liquid class override. If None, auto-detected from tip type and
        liquid.
      type_of_dispensing_mode: Firmware dispensing mode integer (0 = partial jet,
        1 = blow-out jet, 2 = partial surface, 3 = blow-out surface, 4 = empty at
        fix position). If None, auto-computed from jet/empty/blow_out flags.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from
        minimum height in mm. Used for conical tubes. Default 0.
      tube_2nd_section_ratio: Tube 2nd section ratio: (bottom diameter * 10000) / top
        diameter. Default 0.
      pull_out_distance_to_take_transport_air_in_function_without_lld: Distance in mm
        to pull out for transport air when not using LLD. Default 5.0.
      immersion_depth: Immersion depth in mm. Positive = deeper into liquid.
        Default 0.
      surface_following_distance: Surface following distance during dispensing in mm.
        Default 2.9.
      minimal_traverse_height_at_begin_of_command: Minimum Z clearance in mm before
        lateral movement begins. If None, uses the driver's ``traversal_height``.
        Must be between 0 and 360.0.
      minimal_height_at_command_end: Minimum Z height in mm at the end of the command.
        If None, uses the driver's ``traversal_height``. Must be between 0 and 360.0.
      cut_off_speed: Cut-off speed in uL/s. Speed at which dispensing transitions to
        a slower final phase. Default 250.0.
      stop_back_volume: Stop-back volume in uL. Volume retracted after dispensing to
        prevent dripping. Default 0.
      transport_air_volume: Transport air volume in uL. If None, uses the liquid class
        default.
      blow_out_air_volume: Blow-out air volume in uL. If None, uses the liquid class
        default.
      lld_mode: LLD mode as integer (0 = OFF, 1 = GAMMA, 2 = PRESSURE, 3 = DUAL,
        4 = Z_TOUCH_OFF). Default 0 (OFF).
      lld_sensitivity: Capacitive LLD sensitivity (1 = high, 4 = low). Default 4.
      side_touch_off_distance: Side touch-off distance in mm. The tips move laterally
        by this distance after dispensing to break the droplet. Default 0 (disabled).
      swap_speed: Swap speed (on leaving the liquid surface) in mm/s. If None, uses
        the liquid class default.
      settling_time: Settling time in seconds after dispensing completes. If None,
        uses the liquid class default.
      limit_curve_index: TADM limit curve index. Must be between 0 and 999. Default 0.
      tadm_channel_pattern: List of 96 booleans selecting which channels participate in
        TADM monitoring. If None, all 96 channels are active.
      tadm_algorithm_on_off: TADM algorithm (0 = off, 1 = on). Default 0.
      recording_mode: Recording mode for TADM (0 = no recording, 1 = TADM errors only,
        2 = all TADM measurements). Default 0.
      disable_volume_correction: If True, skip liquid-class volume correction.
        Default False.
    """

    jet: bool = False
    blow_out: bool = False
    empty: bool = False
    hlc: Optional[HamiltonLiquidClass] = None
    type_of_dispensing_mode: Optional[int] = None
    tube_2nd_section_height_measured_from_zm: float = 0
    tube_2nd_section_ratio: float = 0
    pull_out_distance_to_take_transport_air_in_function_without_lld: float = 5.0
    immersion_depth: float = 0
    surface_following_distance: float = 2.9
    minimal_traverse_height_at_begin_of_command: Optional[float] = None
    minimal_height_at_command_end: Optional[float] = None
    cut_off_speed: float = 250.0
    stop_back_volume: float = 0
    transport_air_volume: Optional[float] = None
    blow_out_air_volume: Optional[float] = None
    lld_mode: int = 0
    lld_sensitivity: int = 4
    side_touch_off_distance: float = 0
    swap_speed: Optional[float] = None
    settling_time: Optional[float] = None
    limit_curve_index: int = 0
    tadm_channel_pattern: Optional[List[bool]] = None
    tadm_algorithm_on_off: int = 0
    recording_mode: int = 0
    disable_volume_correction: bool = False

  # -- Head96Backend interface -----------------------------------------------

  async def pick_up_tips96(
    self,
    pickup: PickupTipRack,
    backend_params: Optional[BackendParams] = None,
  ):
    """Pick up tips with the 96-head.

    Converts a PickupTipRack operation into a firmware TP command on module A1HM.
    Registers the tip type and computes the A1 reference position from the tip rack.

    Args:
      pickup: The tip-rack pickup operation.
      backend_params: Optional :class:`VantageHead96Backend.PickUpTipsParams` for
        Vantage-specific overrides.
    """
    if not isinstance(backend_params, VantageHead96Backend.PickUpTipsParams):
      backend_params = VantageHead96Backend.PickUpTipsParams()

    tip_spot_a1 = pickup.resource.get_item("A1")
    prototypical_tip = None
    for tip_spot in pickup.resource.get_all_items():
      if tip_spot.has_tip():
        prototypical_tip = tip_spot.get_tip()
        break
    if prototypical_tip is None:
      raise ValueError("No tips found in the tip rack.")
    assert isinstance(prototypical_tip, HamiltonTip), "Tip type must be HamiltonTip."

    ttti = await self.driver.request_or_assign_tip_type_index(prototypical_tip)
    position = tip_spot_a1.get_absolute_location(x="c", y="c", z="b") + pickup.offset
    th = self.driver.traversal_height

    try:
      await self._core96_tip_pick_up(
        x_position=position.x,
        y_position=position.y,
        tip_type=ttti,
        tip_handling_method=backend_params.tip_handling_method,
        z_deposit_position=backend_params.z_deposit_position + pickup.offset.z,
        minimal_traverse_height_at_begin_of_command=(
          backend_params.minimal_traverse_height_at_begin_of_command
          if backend_params.minimal_traverse_height_at_begin_of_command is not None
          else th
        ),
        minimal_height_at_command_end=(
          backend_params.minimal_height_at_command_end
          if backend_params.minimal_height_at_command_end is not None
          else th
        ),
      )
    except VantageFirmwareError as e:
      plr_error = convert_vantage_firmware_error_to_plr_error(e)
      raise plr_error if plr_error is not None else e

  async def drop_tips96(
    self,
    drop: DropTipRack,
    backend_params: Optional[BackendParams] = None,
  ):
    """Drop tips with the 96-head.

    Converts a DropTipRack operation into a firmware TR command on module A1HM.
    Only TipRack targets are supported.

    Args:
      drop: The tip-rack drop operation.
      backend_params: Optional :class:`VantageHead96Backend.DropTipsParams` for
        Vantage-specific overrides.

    Raises:
      NotImplementedError: If the drop target is not a TipRack.
    """
    if not isinstance(backend_params, VantageHead96Backend.DropTipsParams):
      backend_params = VantageHead96Backend.DropTipsParams()

    if isinstance(drop.resource, TipRack):
      tip_spot_a1 = drop.resource.get_item("A1")
      position = tip_spot_a1.get_absolute_location(x="c", y="c", z="b") + drop.offset
    else:
      raise NotImplementedError(
        f"Only TipRacks are supported for dropping tips on Vantage, got {drop.resource}"
      )

    th = self.driver.traversal_height

    try:
      await self._core96_tip_discard(
        x_position=position.x,
        y_position=position.y,
        z_deposit_position=backend_params.z_deposit_position + drop.offset.z,
        minimal_traverse_height_at_begin_of_command=(
          backend_params.minimal_traverse_height_at_begin_of_command
          if backend_params.minimal_traverse_height_at_begin_of_command is not None
          else th
        ),
        minimal_height_at_command_end=(
          backend_params.minimal_height_at_command_end
          if backend_params.minimal_height_at_command_end is not None
          else th
        ),
      )
    except VantageFirmwareError as e:
      plr_error = convert_vantage_firmware_error_to_plr_error(e)
      raise plr_error if plr_error is not None else e

  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate liquid with the 96-head.

    Converts a multi-head aspiration operation into a firmware DA command on module A1HM.
    Handles plate rotation (0 or 180 degrees around Z), liquid class resolution, volume
    correction, LLD search height computation, and mix parameters.

    Args:
      aspiration: The multi-head aspiration operation (plate or container).
      backend_params: Optional :class:`VantageHead96Backend.AspirateParams` for
        Vantage-specific overrides.

    Raises:
      ValueError: If the plate has an unsupported rotation.
    """
    if not isinstance(backend_params, VantageHead96Backend.AspirateParams):
      backend_params = VantageHead96Backend.AspirateParams()

    # Resolve position and liquid surface.
    if isinstance(aspiration, MultiHeadAspirationPlate):
      plate = aspiration.wells[0].parent
      assert isinstance(plate, Plate)
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = plate.get_well("H12")
      elif rot.z % 360 == 0:
        ref_well = plate.get_well("A1")
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")
      position = (
        ref_well.get_absolute_location(x="c", y="c", z="b")
        + aspiration.offset
        + Coordinate(z=ref_well.material_z_thickness)
      )
      well_bottoms = position.z
      lld_search_height = well_bottoms + ref_well.get_absolute_size_z() + 1.7
    else:
      x_width = (12 - 1) * 9
      y_width = (8 - 1) * 9
      x_position = (aspiration.container.get_absolute_size_x() - x_width) / 2
      y_position = (aspiration.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        aspiration.container.get_absolute_location(z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + aspiration.offset
      )
      well_bottoms = position.z
      lld_search_height = well_bottoms + aspiration.container.get_absolute_size_z() + 1.7

    liquid_height = position.z + (
      aspiration.liquid_height if aspiration.liquid_height is not None else 0
    )

    tip = next(t for t in aspiration.tips if t is not None)
    hlc = backend_params.hlc
    if hlc is None:
      hlc = get_vantage_liquid_class(
        tip_volume=tip.maximal_volume,
        is_core=True,
        is_tip=True,
        has_filter=tip.has_filter,
        liquid=Liquid.WATER,
        jet=backend_params.jet,
        blow_out=backend_params.blow_out,
      )

    if backend_params.disable_volume_correction or hlc is None:
      volume = aspiration.volume
    else:
      volume = hlc.compute_corrected_volume(aspiration.volume)

    transport_air_volume = (
      backend_params.transport_air_volume
      if backend_params.transport_air_volume is not None
      else (hlc.aspiration_air_transport_volume if hlc is not None else 0)
    )
    blow_out_air_volume = (
      backend_params.blow_out_air_volume
      if backend_params.blow_out_air_volume is not None
      else (hlc.aspiration_blow_out_volume if hlc is not None else 0)
    )
    flow_rate = (
      aspiration.flow_rate
      if aspiration.flow_rate is not None
      else (hlc.aspiration_flow_rate if hlc is not None else 250)
    )
    swap_speed = (
      backend_params.swap_speed
      if backend_params.swap_speed is not None
      else (hlc.aspiration_swap_speed if hlc is not None else 100)
    )
    settling_time = (
      backend_params.settling_time
      if backend_params.settling_time is not None
      else (hlc.aspiration_settling_time if hlc is not None else 5)
    )

    th = self.driver.traversal_height

    try:
      await self._core96_aspiration_of_liquid(
        type_of_aspiration=backend_params.type_of_aspiration,
        x_position=position.x,
        y_position=position.y,
        minimal_traverse_height_at_begin_of_command=(
          backend_params.minimal_traverse_height_at_begin_of_command
          if backend_params.minimal_traverse_height_at_begin_of_command is not None
          else th
        ),
        minimal_height_at_command_end=(
          backend_params.minimal_height_at_command_end
          if backend_params.minimal_height_at_command_end is not None
          else th
        ),
        lld_search_height=lld_search_height,
        liquid_surface_at_function_without_lld=liquid_height,
        pull_out_distance_to_take_transport_air_in_function_without_lld=(
          backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
        ),
        minimum_height=well_bottoms,
        tube_2nd_section_height_measured_from_zm=(
          backend_params.tube_2nd_section_height_measured_from_zm
        ),
        tube_2nd_section_ratio=backend_params.tube_2nd_section_ratio,
        immersion_depth=backend_params.immersion_depth,
        surface_following_distance=backend_params.surface_following_distance,
        aspiration_volume=volume,
        aspiration_speed=flow_rate,
        transport_air_volume=transport_air_volume,
        blow_out_air_volume=blow_out_air_volume,
        pre_wetting_volume=backend_params.pre_wetting_volume,
        lld_mode=backend_params.lld_mode,
        lld_sensitivity=backend_params.lld_sensitivity,
        swap_speed=swap_speed,
        settling_time=settling_time,
        mix_volume=aspiration.mix.volume if aspiration.mix is not None else 0,
        mix_cycles=aspiration.mix.repetitions if aspiration.mix is not None else 0,
        mix_position_in_z_direction_from_liquid_surface=0,
        surface_following_distance_during_mixing=0,
        mix_speed=aspiration.mix.flow_rate if aspiration.mix is not None else 2.0,
        limit_curve_index=backend_params.limit_curve_index,
        tadm_channel_pattern=backend_params.tadm_channel_pattern,
        tadm_algorithm_on_off=backend_params.tadm_algorithm_on_off,
        recording_mode=backend_params.recording_mode,
      )
    except VantageFirmwareError as e:
      plr_error = convert_vantage_firmware_error_to_plr_error(e)
      raise plr_error if plr_error is not None else e

  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense liquid with the 96-head.

    Converts a multi-head dispense operation into a firmware DD command on module A1HM.
    Handles plate rotation (0 or 180 degrees around Z), liquid class resolution, volume
    correction, dispensing mode selection, and mix parameters.

    Args:
      dispense: The multi-head dispense operation (plate or container).
      backend_params: Optional :class:`VantageHead96Backend.DispenseParams` for
        Vantage-specific overrides.

    Raises:
      ValueError: If the plate has an unsupported rotation.
    """
    if not isinstance(backend_params, VantageHead96Backend.DispenseParams):
      backend_params = VantageHead96Backend.DispenseParams()

    if isinstance(dispense, MultiHeadDispensePlate):
      plate = dispense.wells[0].parent
      assert isinstance(plate, Plate)
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = plate.get_well("H12")
      elif rot.z % 360 == 0:
        ref_well = plate.get_well("A1")
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")
      position = (
        ref_well.get_absolute_location(x="c", y="c", z="b")
        + dispense.offset
        + Coordinate(z=ref_well.material_z_thickness)
      )
      well_bottoms = position.z
      lld_search_height = well_bottoms + ref_well.get_absolute_size_z() + 1.7
    else:
      x_width = (12 - 1) * 9
      y_width = (8 - 1) * 9
      x_position = (dispense.container.get_absolute_size_x() - x_width) / 2
      y_position = (dispense.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        dispense.container.get_absolute_location(z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + dispense.offset
      )
      well_bottoms = position.z
      lld_search_height = well_bottoms + dispense.container.get_absolute_size_z() + 1.7

    # +10mm offset on dispense liquid height. Ported from legacy. Not present on aspirate or STAR.
    liquid_height = (
      position.z + (dispense.liquid_height if dispense.liquid_height is not None else 0) + 10
    )

    tip = next(t for t in dispense.tips if t is not None)
    hlc = backend_params.hlc
    if hlc is None:
      hlc = get_vantage_liquid_class(
        tip_volume=tip.maximal_volume,
        is_core=True,
        is_tip=True,
        has_filter=tip.has_filter,
        liquid=Liquid.WATER,
        jet=backend_params.jet,
        blow_out=backend_params.blow_out,
      )

    if backend_params.disable_volume_correction or hlc is None:
      volume = dispense.volume
    else:
      volume = hlc.compute_corrected_volume(dispense.volume)

    type_of_dispensing_mode = backend_params.type_of_dispensing_mode
    if type_of_dispensing_mode is None:
      type_of_dispensing_mode = _get_dispense_mode(
        jet=backend_params.jet,
        empty=backend_params.empty,
        blow_out=backend_params.blow_out,
      )

    transport_air_volume = (
      backend_params.transport_air_volume
      if backend_params.transport_air_volume is not None
      else (hlc.dispense_air_transport_volume if hlc is not None else 0)
    )
    blow_out_air_volume = (
      backend_params.blow_out_air_volume
      if backend_params.blow_out_air_volume is not None
      else (hlc.dispense_blow_out_volume if hlc is not None else 0)
    )
    flow_rate = (
      dispense.flow_rate
      if dispense.flow_rate is not None
      else (hlc.dispense_flow_rate if hlc is not None else 250)
    )
    swap_speed = (
      backend_params.swap_speed
      if backend_params.swap_speed is not None
      else (hlc.dispense_swap_speed if hlc is not None else 100)
    )
    settling_time = (
      backend_params.settling_time
      if backend_params.settling_time is not None
      else (hlc.dispense_settling_time if hlc is not None else 5)
    )

    th = self.driver.traversal_height

    try:
      await self._core96_dispensing_of_liquid(
        type_of_dispensing_mode=type_of_dispensing_mode,
        x_position=position.x,
        y_position=position.y,
        minimum_height=well_bottoms,
        tube_2nd_section_height_measured_from_zm=(
          backend_params.tube_2nd_section_height_measured_from_zm
        ),
        tube_2nd_section_ratio=backend_params.tube_2nd_section_ratio,
        lld_search_height=lld_search_height,
        liquid_surface_at_function_without_lld=liquid_height,
        pull_out_distance_to_take_transport_air_in_function_without_lld=(
          backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld
        ),
        immersion_depth=backend_params.immersion_depth,
        surface_following_distance=backend_params.surface_following_distance,
        minimal_traverse_height_at_begin_of_command=(
          backend_params.minimal_traverse_height_at_begin_of_command
          if backend_params.minimal_traverse_height_at_begin_of_command is not None
          else th
        ),
        minimal_height_at_command_end=(
          backend_params.minimal_height_at_command_end
          if backend_params.minimal_height_at_command_end is not None
          else th
        ),
        dispense_volume=volume,
        dispense_speed=flow_rate,
        cut_off_speed=backend_params.cut_off_speed,
        stop_back_volume=backend_params.stop_back_volume,
        transport_air_volume=transport_air_volume,
        blow_out_air_volume=blow_out_air_volume,
        lld_mode=backend_params.lld_mode,
        lld_sensitivity=backend_params.lld_sensitivity,
        side_touch_off_distance=backend_params.side_touch_off_distance,
        swap_speed=swap_speed,
        settling_time=settling_time,
        mix_volume=dispense.mix.volume if dispense.mix is not None else 0,
        mix_cycles=dispense.mix.repetitions if dispense.mix is not None else 0,
        mix_position_in_z_direction_from_liquid_surface=0,
        surface_following_distance_during_mixing=0,
        mix_speed=dispense.mix.flow_rate if dispense.mix is not None else 1.0,
        limit_curve_index=backend_params.limit_curve_index,
        tadm_channel_pattern=backend_params.tadm_channel_pattern,
        tadm_algorithm_on_off=backend_params.tadm_algorithm_on_off,
        recording_mode=backend_params.recording_mode,
      )
    except VantageFirmwareError as e:
      plr_error = convert_vantage_firmware_error_to_plr_error(e)
      raise plr_error if plr_error is not None else e

  # -- firmware commands (A1HM) ----------------------------------------------

  async def _core96_tip_pick_up(
    self,
    x_position: float,
    y_position: float,
    tip_type: int,
    tip_handling_method: int,
    z_deposit_position: float,
    minimal_traverse_height_at_begin_of_command: float,
    minimal_height_at_command_end: float,
  ):
    """Tip pick up using 96 head (A1HM:TP).

    All distances are in mm and are converted to firmware units (0.1mm) internally.
    """
    await self.driver.send_command(
      module="A1HM",
      command="TP",
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      tt=tip_type,
      td=tip_handling_method,
      tz=round(z_deposit_position * 10),
      th=round(minimal_traverse_height_at_begin_of_command * 10),
      te=round(minimal_height_at_command_end * 10),
    )

  async def _core96_tip_discard(
    self,
    x_position: float,
    y_position: float,
    z_deposit_position: float,
    minimal_traverse_height_at_begin_of_command: float,
    minimal_height_at_command_end: float,
  ):
    """Tip discard using 96 head (A1HM:TR).

    All distances are in mm and are converted to firmware units (0.1mm) internally.
    """
    await self.driver.send_command(
      module="A1HM",
      command="TR",
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      tz=round(z_deposit_position * 10),
      th=round(minimal_traverse_height_at_begin_of_command * 10),
      te=round(minimal_height_at_command_end * 10),
    )

  async def _core96_aspiration_of_liquid(
    self,
    type_of_aspiration: int,
    x_position: float,
    y_position: float,
    minimal_traverse_height_at_begin_of_command: float,
    minimal_height_at_command_end: float,
    lld_search_height: float,
    liquid_surface_at_function_without_lld: float,
    pull_out_distance_to_take_transport_air_in_function_without_lld: float,
    minimum_height: float,
    tube_2nd_section_height_measured_from_zm: float,
    tube_2nd_section_ratio: float,
    immersion_depth: float,
    surface_following_distance: float,
    aspiration_volume: float,
    aspiration_speed: float,
    transport_air_volume: float,
    blow_out_air_volume: float,
    pre_wetting_volume: float,
    lld_mode: int,
    lld_sensitivity: int,
    swap_speed: float,
    settling_time: float,
    mix_volume: float,
    mix_cycles: int,
    mix_position_in_z_direction_from_liquid_surface: int,
    surface_following_distance_during_mixing: int,
    mix_speed: float,
    limit_curve_index: int,
    tadm_channel_pattern: Optional[List[bool]],
    tadm_algorithm_on_off: int,
    recording_mode: int,
  ):
    """Aspiration of liquid using 96 head (A1HM:DA).

    All parameters accept standard PLR units (mm, uL, uL/s, seconds) and are converted to
    firmware units internally.
    """
    if tadm_channel_pattern is None:
      tadm_channel_pattern = [True] * 96
    tadm_hex = _channel_pattern_to_hex(tadm_channel_pattern)

    await self.driver.send_command(
      module="A1HM",
      command="DA",
      at=type_of_aspiration,
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      th=round(minimal_traverse_height_at_begin_of_command * 10),
      te=round(minimal_height_at_command_end * 10),
      lp=round(lld_search_height * 10),
      zl=round(liquid_surface_at_function_without_lld * 10),
      po=round(pull_out_distance_to_take_transport_air_in_function_without_lld * 10),
      zx=round(minimum_height * 10),
      zu=round(tube_2nd_section_height_measured_from_zm * 10),
      zr=round(tube_2nd_section_ratio),
      ip=round(immersion_depth * 10),
      fp=round(surface_following_distance * 10),
      av=round(aspiration_volume * 100),
      as_=round(aspiration_speed * 10),
      ta=round(transport_air_volume * 10),
      ba=round(blow_out_air_volume * 100),
      oa=round(pre_wetting_volume * 10),
      lm=lld_mode,
      ll=lld_sensitivity,
      de=round(swap_speed * 10),
      wt=round(settling_time * 10),
      mv=round(mix_volume * 10),
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      mh=surface_following_distance_during_mixing,
      ms=round(mix_speed * 10),
      gi=limit_curve_index,
      cw=tadm_hex,
      gj=tadm_algorithm_on_off,
      gk=recording_mode,
    )

  async def _core96_dispensing_of_liquid(
    self,
    type_of_dispensing_mode: int,
    x_position: float,
    y_position: float,
    minimum_height: float,
    tube_2nd_section_height_measured_from_zm: float,
    tube_2nd_section_ratio: float,
    lld_search_height: float,
    liquid_surface_at_function_without_lld: float,
    pull_out_distance_to_take_transport_air_in_function_without_lld: float,
    immersion_depth: float,
    surface_following_distance: float,
    minimal_traverse_height_at_begin_of_command: float,
    minimal_height_at_command_end: float,
    dispense_volume: float,
    dispense_speed: float,
    cut_off_speed: float,
    stop_back_volume: float,
    transport_air_volume: float,
    blow_out_air_volume: float,
    lld_mode: int,
    lld_sensitivity: int,
    side_touch_off_distance: float,
    swap_speed: float,
    settling_time: float,
    mix_volume: float,
    mix_cycles: int,
    mix_position_in_z_direction_from_liquid_surface: int,
    surface_following_distance_during_mixing: int,
    mix_speed: float,
    limit_curve_index: int,
    tadm_channel_pattern: Optional[List[bool]],
    tadm_algorithm_on_off: int,
    recording_mode: int,
  ):
    """Dispensing of liquid using 96 head (A1HM:DD).

    All parameters accept standard PLR units (mm, uL, uL/s, seconds) and are converted to
    firmware units internally.
    """
    if tadm_channel_pattern is None:
      tadm_channel_pattern = [True] * 96
    tadm_hex = _channel_pattern_to_hex(tadm_channel_pattern)

    await self.driver.send_command(
      module="A1HM",
      command="DD",
      dm=type_of_dispensing_mode,
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      zx=round(minimum_height * 10),
      zu=round(tube_2nd_section_height_measured_from_zm * 10),
      zr=round(tube_2nd_section_ratio),
      lp=round(lld_search_height * 10),
      zl=round(liquid_surface_at_function_without_lld * 10),
      po=round(pull_out_distance_to_take_transport_air_in_function_without_lld * 10),
      ip=round(immersion_depth * 10),
      fp=round(surface_following_distance * 10),
      th=round(minimal_traverse_height_at_begin_of_command * 10),
      te=round(minimal_height_at_command_end * 10),
      dv=round(dispense_volume * 100),
      ds=round(dispense_speed * 10),
      ss=round(cut_off_speed * 10),
      rv=round(stop_back_volume * 10),
      ta=round(transport_air_volume * 10),
      ba=round(blow_out_air_volume * 100),
      lm=lld_mode,
      ll=lld_sensitivity,
      dj=round(side_touch_off_distance * 10),
      de=round(swap_speed * 10),
      wt=round(settling_time * 10),
      mv=round(mix_volume * 10),
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      mh=surface_following_distance_during_mixing,
      ms=round(mix_speed * 10),
      gi=limit_curve_index,
      cw=tadm_hex,
      gj=tadm_algorithm_on_off,
      gk=recording_mode,
    )

  async def _core96_move_to_defined_position(
    self,
    x_position: float = 500.0,
    y_position: float = 500.0,
    z_position: float = 0.0,
    minimal_traverse_height_at_begin_of_command: float = 390.0,
  ):
    """Move 96 head to a defined position (A1HM:DN).

    Args:
      x_position: X position [mm].
      y_position: Y position [mm].
      z_position: Z position [mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height [mm].
    """
    await self.driver.send_command(
      module="A1HM",
      command="DN",
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      zp=round(z_position * 10),
      th=round(minimal_traverse_height_at_begin_of_command * 10),
    )

  async def _core96_wash_tips(
    self,
    x_position: float = 500.0,
    y_position: float = 500.0,
    liquid_surface_at_function_without_lld: float = 390.0,
    minimum_height: float = 390.0,
    surface_following_distance_during_mixing: float = 0.0,
    minimal_traverse_height_at_begin_of_command: float = 390.0,
    mix_volume: float = 0.0,
    mix_cycles: int = 0,
    mix_speed: float = 200.0,
  ):
    """Wash tips on the 96 head (A1HM:DW).

    Args:
      x_position: X position [mm].
      y_position: Y position [mm].
      liquid_surface_at_function_without_lld: Liquid surface without LLD [mm].
      minimum_height: Minimum height (maximum immersion depth) [mm].
      surface_following_distance_during_mixing: Surface following distance during mixing [mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height [mm].
      mix_volume: Mix volume [uL].
      mix_cycles: Number of mix cycles.
      mix_speed: Mix speed [uL/s].
    """
    await self.driver.send_command(
      module="A1HM",
      command="DW",
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      zl=round(liquid_surface_at_function_without_lld * 10),
      zx=round(minimum_height * 10),
      mh=round(surface_following_distance_during_mixing * 10),
      th=round(minimal_traverse_height_at_begin_of_command * 10),
      mv=round(mix_volume * 10),
      mc=mix_cycles,
      ms=round(mix_speed * 10),
    )

  async def _core96_empty_washed_tips(
    self,
    liquid_surface_at_function_without_lld: float = 390.0,
    minimal_height_at_command_end: float = 390.0,
  ):
    """Empty washed tips — end of wash procedure only (A1HM:EE).

    Args:
      liquid_surface_at_function_without_lld: Liquid surface without LLD [mm].
      minimal_height_at_command_end: Minimal height at command end [mm].
    """
    await self.driver.send_command(
      module="A1HM",
      command="EE",
      zl=round(liquid_surface_at_function_without_lld * 10),
      te=round(minimal_height_at_command_end * 10),
    )

  async def _core96_search_for_teach_in_signal_in_x_direction(
    self,
    x_search_distance: float = 0.0,
    x_speed: float = 5.0,
  ):
    """Search for Teach in signal in X direction on the 96 head (A1HM:DL).

    Args:
      x_search_distance: X search distance [mm]. Must be between -5000.0 and 5000.0.
      x_speed: X speed [mm/s]. Must be between 2.0 and 2500.0.
    """
    if not -5000.0 <= x_search_distance <= 5000.0:
      raise ValueError("x_search_distance must be in range -5000.0 to 5000.0")
    if not 2.0 <= x_speed <= 2500.0:
      raise ValueError("x_speed must be in range 2.0 to 2500.0")

    await self.driver.send_command(
      module="A1HM",
      command="DL",
      xs=round(x_search_distance * 10),
      xv=round(x_speed * 10),
    )

  async def _core96_set_any_parameter(self):
    """Set any parameter within the 96 head module (A1HM:AA)."""
    await self.driver.send_command(
      module="A1HM",
      command="AA",
    )

  async def _core96_query_tip_presence(self):
    """Query Tip presence on the 96 head (A1HM:QA)."""
    return await self.driver.send_command(
      module="A1HM",
      command="QA",
    )

  async def _core96_request_position(self):
    """Request position of the 96 head (A1HM:QI)."""
    return await self.driver.send_command(
      module="A1HM",
      command="QI",
    )

  async def _core96_request_tadm_error_status(
    self,
    tadm_channel_pattern: Optional[List[bool]] = None,
  ):
    """Request TADM error status on the 96 head (A1HM:VB).

    Args:
      tadm_channel_pattern: TADM Channel pattern (list of 96 booleans). If None, all 96
        channels are active.
    """
    if tadm_channel_pattern is None:
      tadm_channel_pattern = [True] * 96
    tadm_hex = _channel_pattern_to_hex(tadm_channel_pattern)

    return await self.driver.send_command(
      module="A1HM",
      command="VB",
      cw=tadm_hex,
    )
