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
  """Convert a list of 96 booleans to the hex string expected by firmware."""
  if len(pattern) != 96:
    raise ValueError("channel_pattern must be a list of 96 boolean values")
  channel_pattern_bin_str = reversed(["1" if x else "0" for x in pattern])
  return hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]


class VantageHead96Backend(Head96Backend):
  """Translates Head96 operations into Vantage firmware commands via the driver."""

  def __init__(self, driver: "VantageDriver"):
    self.driver = driver

  async def _on_setup(self):
    pass

  async def _on_stop(self):
    pass

  # -- BackendParams ---------------------------------------------------------

  @dataclass
  class PickUpTipsParams(BackendParams):
    tip_handling_method: int = 0
    z_deposit_position: float = 216.4
    minimal_traverse_height_at_begin_of_command: Optional[float] = None
    minimal_height_at_command_end: Optional[float] = None

  @dataclass
  class DropTipsParams(BackendParams):
    z_deposit_position: float = 216.4
    minimal_traverse_height_at_begin_of_command: Optional[float] = None
    minimal_height_at_command_end: Optional[float] = None

  @dataclass
  class AspirateParams(BackendParams):
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
        x_position=round(position.x * 10),
        y_position=round(position.y * 10),
        tip_type=ttti,
        tip_handling_method=backend_params.tip_handling_method,
        z_deposit_position=round((backend_params.z_deposit_position + pickup.offset.z) * 10),
        minimal_traverse_height_at_begin_of_command=round(
          (backend_params.minimal_traverse_height_at_begin_of_command or th) * 10
        ),
        minimal_height_at_command_end=round(
          (backend_params.minimal_height_at_command_end or th) * 10
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
        x_position=round(position.x * 10),
        y_position=round(position.y * 10),
        z_deposit_position=round((backend_params.z_deposit_position + drop.offset.z) * 10),
        minimal_traverse_height_at_begin_of_command=round(
          (backend_params.minimal_traverse_height_at_begin_of_command or th) * 10
        ),
        minimal_height_at_command_end=round(
          (backend_params.minimal_height_at_command_end or th) * 10
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

    liquid_height = position.z + (aspiration.liquid_height or 0)

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

    transport_air_volume = backend_params.transport_air_volume or (
      hlc.aspiration_air_transport_volume if hlc is not None else 0
    )
    blow_out_air_volume = backend_params.blow_out_air_volume or (
      hlc.aspiration_blow_out_volume if hlc is not None else 0
    )
    flow_rate = aspiration.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 250)
    swap_speed = backend_params.swap_speed or (
      hlc.aspiration_swap_speed if hlc is not None else 100
    )
    settling_time = backend_params.settling_time or (
      hlc.aspiration_settling_time if hlc is not None else 5
    )

    th = self.driver.traversal_height

    try:
      await self._core96_aspiration_of_liquid(
        type_of_aspiration=backend_params.type_of_aspiration,
        x_position=round(position.x * 10),
        y_position=round(position.y * 10),
        minimal_traverse_height_at_begin_of_command=round(
          (backend_params.minimal_traverse_height_at_begin_of_command or th) * 10
        ),
        minimal_height_at_command_end=round(
          (backend_params.minimal_height_at_command_end or th) * 10
        ),
        lld_search_height=round(lld_search_height * 10),
        liquid_surface_at_function_without_lld=round(liquid_height * 10),
        pull_out_distance_to_take_transport_air_in_function_without_lld=round(
          backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld * 10
        ),
        minimum_height=round(well_bottoms * 10),
        tube_2nd_section_height_measured_from_zm=round(
          backend_params.tube_2nd_section_height_measured_from_zm * 10
        ),
        tube_2nd_section_ratio=round(backend_params.tube_2nd_section_ratio * 10),
        immersion_depth=round(backend_params.immersion_depth * 10),
        surface_following_distance=round(backend_params.surface_following_distance * 10),
        aspiration_volume=round(volume * 100),
        aspiration_speed=round(flow_rate * 10),
        transport_air_volume=round(transport_air_volume * 10),
        blow_out_air_volume=round(blow_out_air_volume * 100),
        pre_wetting_volume=round(backend_params.pre_wetting_volume * 100),
        lld_mode=backend_params.lld_mode,
        lld_sensitivity=backend_params.lld_sensitivity,
        swap_speed=round(swap_speed * 10),
        settling_time=round(settling_time * 10),
        mix_volume=round(aspiration.mix.volume * 100) if aspiration.mix is not None else 0,
        mix_cycles=aspiration.mix.repetitions if aspiration.mix is not None else 0,
        mix_position_in_z_direction_from_liquid_surface=0,
        surface_following_distance_during_mixing=0,
        mix_speed=round(aspiration.mix.flow_rate * 10) if aspiration.mix is not None else 20,
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

    liquid_height = position.z + (dispense.liquid_height or 0) + 10

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

    transport_air_volume = backend_params.transport_air_volume or (
      hlc.dispense_air_transport_volume if hlc is not None else 0
    )
    blow_out_air_volume = backend_params.blow_out_air_volume or (
      hlc.dispense_blow_out_volume if hlc is not None else 0
    )
    flow_rate = dispense.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 250)
    swap_speed = backend_params.swap_speed or (hlc.dispense_swap_speed if hlc is not None else 100)
    settling_time = backend_params.settling_time or (
      hlc.dispense_settling_time if hlc is not None else 5
    )

    th = self.driver.traversal_height

    try:
      await self._core96_dispensing_of_liquid(
        type_of_dispensing_mode=type_of_dispensing_mode,
        x_position=round(position.x * 10),
        y_position=round(position.y * 10),
        minimum_height=round(well_bottoms * 10),
        tube_2nd_section_height_measured_from_zm=round(
          backend_params.tube_2nd_section_height_measured_from_zm * 10
        ),
        tube_2nd_section_ratio=round(backend_params.tube_2nd_section_ratio * 10),
        lld_search_height=round(lld_search_height * 10),
        liquid_surface_at_function_without_lld=round(liquid_height * 10),
        pull_out_distance_to_take_transport_air_in_function_without_lld=round(
          backend_params.pull_out_distance_to_take_transport_air_in_function_without_lld * 10
        ),
        immersion_depth=round(backend_params.immersion_depth * 10),
        surface_following_distance=round(backend_params.surface_following_distance * 10),
        minimal_traverse_height_at_begin_of_command=round(
          (backend_params.minimal_traverse_height_at_begin_of_command or th) * 10
        ),
        minimal_height_at_command_end=round(
          (backend_params.minimal_height_at_command_end or th) * 10
        ),
        dispense_volume=round(volume * 100),
        dispense_speed=round(flow_rate * 10),
        cut_off_speed=round(backend_params.cut_off_speed * 10),
        stop_back_volume=round(backend_params.stop_back_volume * 100),
        transport_air_volume=round(transport_air_volume * 10),
        blow_out_air_volume=round(blow_out_air_volume * 100),
        lld_mode=backend_params.lld_mode,
        lld_sensitivity=backend_params.lld_sensitivity,
        side_touch_off_distance=round(backend_params.side_touch_off_distance * 10),
        swap_speed=round(swap_speed * 10),
        settling_time=round(settling_time * 10),
        mix_volume=round(dispense.mix.volume * 100) if dispense.mix is not None else 0,
        mix_cycles=dispense.mix.repetitions if dispense.mix is not None else 0,
        mix_position_in_z_direction_from_liquid_surface=0,
        surface_following_distance_during_mixing=0,
        mix_speed=round(dispense.mix.flow_rate * 10) if dispense.mix is not None else 10,
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
    x_position: int,
    y_position: int,
    tip_type: int,
    tip_handling_method: int,
    z_deposit_position: int,
    minimal_traverse_height_at_begin_of_command: int,
    minimal_height_at_command_end: int,
  ):
    """Tip pick up using 96 head (A1HM:TP)."""
    await self.driver.send_command(
      module="A1HM",
      command="TP",
      xp=x_position,
      yp=y_position,
      tt=tip_type,
      td=tip_handling_method,
      tz=z_deposit_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
    )

  async def _core96_tip_discard(
    self,
    x_position: int,
    y_position: int,
    z_deposit_position: int,
    minimal_traverse_height_at_begin_of_command: int,
    minimal_height_at_command_end: int,
  ):
    """Tip discard using 96 head (A1HM:TR)."""
    await self.driver.send_command(
      module="A1HM",
      command="TR",
      xp=x_position,
      yp=y_position,
      tz=z_deposit_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
    )

  async def _core96_aspiration_of_liquid(
    self,
    type_of_aspiration: int,
    x_position: int,
    y_position: int,
    minimal_traverse_height_at_begin_of_command: int,
    minimal_height_at_command_end: int,
    lld_search_height: int,
    liquid_surface_at_function_without_lld: int,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int,
    minimum_height: int,
    tube_2nd_section_height_measured_from_zm: int,
    tube_2nd_section_ratio: int,
    immersion_depth: int,
    surface_following_distance: int,
    aspiration_volume: int,
    aspiration_speed: int,
    transport_air_volume: int,
    blow_out_air_volume: int,
    pre_wetting_volume: int,
    lld_mode: int,
    lld_sensitivity: int,
    swap_speed: int,
    settling_time: int,
    mix_volume: int,
    mix_cycles: int,
    mix_position_in_z_direction_from_liquid_surface: int,
    surface_following_distance_during_mixing: int,
    mix_speed: int,
    limit_curve_index: int,
    tadm_channel_pattern: Optional[List[bool]],
    tadm_algorithm_on_off: int,
    recording_mode: int,
  ):
    """Aspiration of liquid using 96 head (A1HM:DA)."""
    if tadm_channel_pattern is None:
      tadm_channel_pattern = [True] * 96
    tadm_hex = _channel_pattern_to_hex(tadm_channel_pattern)

    await self.driver.send_command(
      module="A1HM",
      command="DA",
      at=type_of_aspiration,
      xp=x_position,
      yp=y_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      lp=lld_search_height,
      zl=liquid_surface_at_function_without_lld,
      po=pull_out_distance_to_take_transport_air_in_function_without_lld,
      zx=minimum_height,
      zu=tube_2nd_section_height_measured_from_zm,
      zr=tube_2nd_section_ratio,
      ip=immersion_depth,
      fp=surface_following_distance,
      av=aspiration_volume,
      as_=aspiration_speed,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      oa=pre_wetting_volume,
      lm=lld_mode,
      ll=lld_sensitivity,
      de=swap_speed,
      wt=settling_time,
      mv=mix_volume,
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      mh=surface_following_distance_during_mixing,
      ms=mix_speed,
      gi=limit_curve_index,
      cw=tadm_hex,
      gj=tadm_algorithm_on_off,
      gk=recording_mode,
    )

  async def _core96_dispensing_of_liquid(
    self,
    type_of_dispensing_mode: int,
    x_position: int,
    y_position: int,
    minimum_height: int,
    tube_2nd_section_height_measured_from_zm: int,
    tube_2nd_section_ratio: int,
    lld_search_height: int,
    liquid_surface_at_function_without_lld: int,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int,
    immersion_depth: int,
    surface_following_distance: int,
    minimal_traverse_height_at_begin_of_command: int,
    minimal_height_at_command_end: int,
    dispense_volume: int,
    dispense_speed: int,
    cut_off_speed: int,
    stop_back_volume: int,
    transport_air_volume: int,
    blow_out_air_volume: int,
    lld_mode: int,
    lld_sensitivity: int,
    side_touch_off_distance: int,
    swap_speed: int,
    settling_time: int,
    mix_volume: int,
    mix_cycles: int,
    mix_position_in_z_direction_from_liquid_surface: int,
    surface_following_distance_during_mixing: int,
    mix_speed: int,
    limit_curve_index: int,
    tadm_channel_pattern: Optional[List[bool]],
    tadm_algorithm_on_off: int,
    recording_mode: int,
  ):
    """Dispensing of liquid using 96 head (A1HM:DD)."""
    if tadm_channel_pattern is None:
      tadm_channel_pattern = [True] * 96
    tadm_hex = _channel_pattern_to_hex(tadm_channel_pattern)

    await self.driver.send_command(
      module="A1HM",
      command="DD",
      dm=type_of_dispensing_mode,
      xp=x_position,
      yp=y_position,
      zx=minimum_height,
      zu=tube_2nd_section_height_measured_from_zm,
      zr=tube_2nd_section_ratio,
      lp=lld_search_height,
      zl=liquid_surface_at_function_without_lld,
      po=pull_out_distance_to_take_transport_air_in_function_without_lld,
      ip=immersion_depth,
      fp=surface_following_distance,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      dv=dispense_volume,
      ds=dispense_speed,
      ss=cut_off_speed,
      rv=stop_back_volume,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      lm=lld_mode,
      ll=lld_sensitivity,
      dj=side_touch_off_distance,
      de=swap_speed,
      wt=settling_time,
      mv=mix_volume,
      mc=mix_cycles,
      mp=mix_position_in_z_direction_from_liquid_surface,
      mh=surface_following_distance_during_mixing,
      ms=mix_speed,
      gi=limit_curve_index,
      cw=tadm_hex,
      gj=tadm_algorithm_on_off,
      gk=recording_mode,
    )
