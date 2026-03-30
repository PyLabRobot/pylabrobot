"""Vantage Head96 backend: translates Head96 operations into Vantage firmware commands."""

from __future__ import annotations

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
from pylabrobot.resources import Coordinate
from pylabrobot.resources.hamilton import HamiltonTip

if TYPE_CHECKING:
  from .driver import VantageDriver


def _dispensing_mode_for_op(jet: bool, empty: bool, blow_out: bool) -> int:
  """Compute firmware dispensing mode from boolean flags.

  Firmware modes:
    0 = Part in jet
    1 = Blow in jet (called "empty" in VENUS liquid editor)
    2 = Part at surface
    3 = Blow at surface (called "empty" in VENUS liquid editor)
    4 = Empty (truly empty)
  """
  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  return 3 if blow_out else 2


def _tadm_channel_pattern_to_hex(pattern: List[bool]) -> str:
  """Convert a list of 96 booleans to the hex string expected by firmware."""
  assert len(pattern) == 96, "channel_pattern must be a list of 96 boolean values"
  pattern_num = sum(2**i if pattern[i] else 0 for i in range(96))
  return hex(pattern_num)[2:].upper()


class VantageHead96Backend(Head96Backend):
  """Translates Head96 operations into Vantage firmware commands via the driver.

  All protocol encoding (parameter formatting, validation, hex encoding) lives here.
  The driver is used only for ``send_command`` I/O.
  """

  def __init__(self, driver: VantageDriver):
    self._driver = driver

  # ---------------------------------------------------------------------------
  # Lifecycle
  # ---------------------------------------------------------------------------

  async def _on_setup(self):
    """Check Core96 initialization status and initialize if needed."""
    initialized = await self.core96_request_initialization_status()
    if not initialized:
      traversal = round(self._driver.traversal_height * 10)
      await self.core96_initialize(
        x_position=7347,  # TODO: get trash location from deck.
        y_position=2684,  # TODO: get trash location from deck.
        minimal_traverse_height_at_begin_of_command=traversal,
        minimal_height_at_command_end=traversal,
        end_z_deposit_position=2420,
      )

  # ---------------------------------------------------------------------------
  # Pick up tips  (ABC implementation)
  # ---------------------------------------------------------------------------

  @dataclass
  class PickUpTips96Params(BackendParams):
    """Vantage-specific parameters for 96-head tip pickup."""

    tip_handling_method: int = 0
    z_deposit_position: float = 216.4
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    minimum_height_at_command_end: Optional[float] = None

  async def pick_up_tips96(
    self, pickup: PickupTipRack, backend_params: Optional[BackendParams] = None
  ):
    """Pick up tips using the 96 head.

    Firmware command: A1HM TP
    """
    if not isinstance(backend_params, VantageHead96Backend.PickUpTips96Params):
      backend_params = VantageHead96Backend.PickUpTips96Params()

    tip_spot_a1 = pickup.resource.get_item("A1")

    prototypical_tip = None
    for tip_spot in pickup.resource.get_all_items():
      if tip_spot.has_tip():
        prototypical_tip = tip_spot.get_tip()
        break
    if prototypical_tip is None:
      raise ValueError("No tips found in the tip rack.")
    if not isinstance(prototypical_tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")

    ttti = await self._driver.request_or_assign_tip_type_index(prototypical_tip)

    position = (
      tip_spot_a1.get_absolute_location() + tip_spot_a1.center() + pickup.offset
    )
    offset_z = pickup.offset.z

    traversal = self._driver.traversal_height

    await self.core96_tip_pick_up(
      x_position=round(position.x * 10),
      y_position=round(position.y * 10),
      tip_type=ttti,
      tip_handling_method=backend_params.tip_handling_method,
      z_deposit_position=round((backend_params.z_deposit_position + offset_z) * 10),
      minimal_traverse_height_at_begin_of_command=round(
        (backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10
      ),
      minimal_height_at_command_end=round(
        (backend_params.minimum_height_at_command_end or traversal) * 10
      ),
    )

  # ---------------------------------------------------------------------------
  # Drop tips  (ABC implementation)
  # ---------------------------------------------------------------------------

  @dataclass
  class DropTips96Params(BackendParams):
    """Vantage-specific parameters for 96-head tip drop."""

    z_deposit_position: float = 216.4
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    minimum_height_at_command_end: Optional[float] = None

  async def drop_tips96(
    self, drop: DropTipRack, backend_params: Optional[BackendParams] = None
  ):
    """Drop tips from the 96 head.

    Firmware command: A1HM TR
    """
    if not isinstance(backend_params, VantageHead96Backend.DropTips96Params):
      backend_params = VantageHead96Backend.DropTips96Params()

    from pylabrobot.resources import TipRack

    if isinstance(drop.resource, TipRack):
      tip_spot_a1 = drop.resource.get_item("A1")
      position = tip_spot_a1.get_absolute_location() + tip_spot_a1.center() + drop.offset
    else:
      raise NotImplementedError(
        "Only TipRacks are supported for dropping tips on Vantage",
        f"got {drop.resource}",
      )

    offset_z = drop.offset.z
    traversal = self._driver.traversal_height

    await self.core96_tip_discard(
      x_position=round(position.x * 10),
      y_position=round(position.y * 10),
      z_deposit_position=round((backend_params.z_deposit_position + offset_z) * 10),
      minimal_traverse_height_at_begin_of_command=round(
        (backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10
      ),
      minimal_height_at_command_end=round(
        (backend_params.minimum_height_at_command_end or traversal) * 10
      ),
    )

  # ---------------------------------------------------------------------------
  # Aspirate  (ABC implementation)
  # ---------------------------------------------------------------------------

  @dataclass
  class Aspirate96Params(BackendParams):
    """Vantage-specific parameters for 96-head aspiration."""

    type_of_aspiration: int = 0
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    minimum_height_at_command_end: Optional[float] = None
    pull_out_distance_to_take_transport_air_in_function_without_lld: float = 5.0
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
    mix_position_in_z_direction_from_liquid_surface: float = 0
    surface_following_distance_during_mixing: float = 0
    limit_curve_index: int = 0
    tadm_channel_pattern: Optional[List[bool]] = None
    tadm_algorithm_on_off: int = 0
    recording_mode: int = 0

  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate using the Core96 head.

    Firmware command: A1HM DA
    """
    if not isinstance(backend_params, VantageHead96Backend.Aspirate96Params):
      backend_params = VantageHead96Backend.Aspirate96Params()

    # Compute position
    if isinstance(aspiration, MultiHeadAspirationPlate):
      plate = aspiration.wells[0].parent
      assert plate is not None, "MultiHeadAspirationPlate well parent must not be None"
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = aspiration.wells[-1]
      elif rot.z % 360 == 0:
        ref_well = aspiration.wells[0]
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")

      position = (
        ref_well.get_absolute_location()
        + ref_well.center()
        + Coordinate(z=ref_well.material_z_thickness)
        + aspiration.offset
      )
      # -1 compared to STAR
      well_bottoms = position.z
      lld_search_height = well_bottoms + ref_well.get_absolute_size_z() + 2.7 - 1
    else:
      # Container (trough): center the head
      x_width = (12 - 1) * 9  # 12 tips in a row, 9 mm between them
      y_width = (8 - 1) * 9  # 8 tips in a column, 9 mm between them
      x_position = (aspiration.container.get_absolute_size_x() - x_width) / 2
      y_position = (aspiration.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        aspiration.container.get_absolute_location(z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + aspiration.offset
      )
      well_bottoms = position.z
      lld_search_height = well_bottoms + aspiration.container.get_absolute_size_z() + 2.7 - 1

    liquid_height = position.z + (aspiration.liquid_height or 0)

    volume = aspiration.volume
    flow_rate = aspiration.flow_rate or 250
    transport_air_volume = backend_params.transport_air_volume or 0
    blow_out_air_volume = aspiration.blow_out_air_volume or backend_params.blow_out_air_volume or 0
    swap_speed = backend_params.swap_speed or 100
    settling_time = backend_params.settling_time or 5

    traversal = self._driver.traversal_height

    await self.core96_aspiration_of_liquid(
      x_position=round(position.x * 10),
      y_position=round(position.y * 10),
      type_of_aspiration=backend_params.type_of_aspiration,
      minimal_traverse_height_at_begin_of_command=round(
        (backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10
      ),
      minimal_height_at_command_end=round(
        (backend_params.minimum_height_at_command_end or traversal) * 10
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
      mix_position_in_z_direction_from_liquid_surface=round(
        backend_params.mix_position_in_z_direction_from_liquid_surface * 10
      ),
      surface_following_distance_during_mixing=round(
        backend_params.surface_following_distance_during_mixing * 10
      ),
      mix_speed=round(aspiration.mix.flow_rate * 10) if aspiration.mix is not None else 20,
      limit_curve_index=backend_params.limit_curve_index,
      tadm_channel_pattern=backend_params.tadm_channel_pattern,
      tadm_algorithm_on_off=backend_params.tadm_algorithm_on_off,
      recording_mode=backend_params.recording_mode,
    )

  # ---------------------------------------------------------------------------
  # Dispense  (ABC implementation)
  # ---------------------------------------------------------------------------

  @dataclass
  class Dispense96Params(BackendParams):
    """Vantage-specific parameters for 96-head dispense."""

    jet: bool = False
    blow_out: bool = False
    empty: bool = False
    type_of_dispensing_mode: Optional[int] = None
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    minimum_height_at_command_end: Optional[float] = None
    tube_2nd_section_height_measured_from_zm: float = 0
    tube_2nd_section_ratio: float = 0
    pull_out_distance_to_take_transport_air_in_function_without_lld: float = 5.0
    immersion_depth: float = 0
    surface_following_distance: float = 2.9
    cut_off_speed: float = 250.0
    stop_back_volume: float = 0
    transport_air_volume: Optional[float] = None
    blow_out_air_volume: Optional[float] = None
    lld_mode: int = 0
    lld_sensitivity: int = 4
    side_touch_off_distance: float = 0
    swap_speed: Optional[float] = None
    settling_time: Optional[float] = None
    mix_position_in_z_direction_from_liquid_surface: float = 0
    surface_following_distance_during_mixing: float = 0
    limit_curve_index: int = 0
    tadm_channel_pattern: Optional[List[bool]] = None
    tadm_algorithm_on_off: int = 0
    recording_mode: int = 0

  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense using the Core96 head.

    Firmware command: A1HM DD
    """
    if not isinstance(backend_params, VantageHead96Backend.Dispense96Params):
      backend_params = VantageHead96Backend.Dispense96Params()

    # Compute position
    if isinstance(dispense, MultiHeadDispensePlate):
      plate = dispense.wells[0].parent
      assert plate is not None, "MultiHeadDispensePlate well parent must not be None"
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = dispense.wells[-1]
      elif rot.z % 360 == 0:
        ref_well = dispense.wells[0]
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")

      position = (
        ref_well.get_absolute_location()
        + ref_well.center()
        + Coordinate(z=ref_well.material_z_thickness)
        + dispense.offset
      )
      # -1 compared to STAR
      well_bottoms = position.z
      lld_search_height = well_bottoms + ref_well.get_absolute_size_z() + 2.7 - 1
    else:
      # Container (trough): center the head
      x_width = (12 - 1) * 9  # 12 tips in a row, 9 mm between them
      y_width = (8 - 1) * 9  # 8 tips in a column, 9 mm between them
      x_position = (dispense.container.get_absolute_size_x() - x_width) / 2
      y_position = (dispense.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        dispense.container.get_absolute_location(z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + dispense.offset
      )
      well_bottoms = position.z
      lld_search_height = well_bottoms + dispense.container.get_absolute_size_z() + 2.7 - 1

    liquid_height = position.z + (dispense.liquid_height or 0) + 10

    volume = dispense.volume
    flow_rate = dispense.flow_rate or 250
    transport_air_volume = backend_params.transport_air_volume or 0
    blow_out_air_volume = dispense.blow_out_air_volume or backend_params.blow_out_air_volume or 0
    swap_speed = backend_params.swap_speed or 100
    settling_time = backend_params.settling_time or 5
    type_of_dispensing_mode = backend_params.type_of_dispensing_mode or _dispensing_mode_for_op(
      jet=backend_params.jet, empty=backend_params.empty, blow_out=backend_params.blow_out
    )

    traversal = self._driver.traversal_height

    await self.core96_dispensing_of_liquid(
      x_position=round(position.x * 10),
      y_position=round(position.y * 10),
      type_of_dispensing_mode=type_of_dispensing_mode,
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
        (backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10
      ),
      minimal_height_at_command_end=round(
        (backend_params.minimum_height_at_command_end or traversal) * 10
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
      mix_position_in_z_direction_from_liquid_surface=round(
        backend_params.mix_position_in_z_direction_from_liquid_surface * 10
      ),
      surface_following_distance_during_mixing=round(
        backend_params.surface_following_distance_during_mixing * 10
      ),
      mix_speed=round(dispense.mix.flow_rate * 10) if dispense.mix is not None else 10,
      limit_curve_index=backend_params.limit_curve_index,
      tadm_channel_pattern=backend_params.tadm_channel_pattern,
      tadm_algorithm_on_off=backend_params.tadm_algorithm_on_off,
      recording_mode=backend_params.recording_mode,
    )

  # ===========================================================================
  # Raw firmware command methods
  # ===========================================================================

  async def core96_request_initialization_status(self) -> bool:
    """Request CoRe96 initialization status.

    This method is inferred from I1AM and A1AM commands ("QW").

    Returns:
      bool: True if initialized, False otherwise.
    """
    resp = await self._driver.send_command(module="A1HM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def core96_initialize(
    self,
    x_position: int = 5000,
    y_position: int = 5000,
    z_position: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3900,
    minimal_height_at_command_end: int = 3900,
    end_z_deposit_position: int = 0,
    tip_type: int = 4,
  ):
    """Initialize 96 head.

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      z_position: Z Position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      end_z_deposit_position: Z deposit position [0.1mm] (collar bearing position). (not documented,
        but present in the log files.)
      tip_type: Tip type (see command TT).
    """

    if not -500000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -500000 to 50000")

    if not 422 <= y_position <= 5921:
      raise ValueError("y_position must be in range 422 to 5921")

    if not 0 <= z_position <= 3900:
      raise ValueError("z_position must be in range 0 to 3900")

    if not 0 <= minimal_traverse_height_at_begin_of_command <= 3900:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3900")

    if not 0 <= minimal_height_at_command_end <= 3900:
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3900")

    if not 0 <= end_z_deposit_position <= 3600:
      raise ValueError("end_z_deposit_position must be in range 0 to 3600")

    if not 0 <= tip_type <= 199:
      raise ValueError("tip_type must be in range 0 to 199")

    return await self._driver.send_command(
      module="A1HM",
      command="DI",
      xp=x_position,
      yp=y_position,
      zp=z_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      tz=end_z_deposit_position,
      tt=tip_type,
    )

  async def core96_aspiration_of_liquid(
    self,
    type_of_aspiration: int = 0,
    x_position: int = 5000,
    y_position: int = 5000,
    minimal_traverse_height_at_begin_of_command: int = 3900,
    minimal_height_at_command_end: int = 3900,
    lld_search_height: int = 0,
    liquid_surface_at_function_without_lld: int = 3900,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    minimum_height: int = 3900,
    tube_2nd_section_height_measured_from_zm: int = 0,
    tube_2nd_section_ratio: int = 0,
    immersion_depth: int = 0,
    surface_following_distance: int = 0,
    aspiration_volume: int = 0,
    aspiration_speed: int = 2000,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 1000,
    pre_wetting_volume: int = 0,
    lld_mode: int = 1,
    lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 5,
    mix_volume: int = 0,
    mix_cycles: int = 0,
    mix_position_in_z_direction_from_liquid_surface: int = 0,
    surface_following_distance_during_mixing: int = 0,
    mix_speed: int = 2000,
    limit_curve_index: int = 0,
    tadm_channel_pattern: Optional[List[bool]] = None,
    tadm_algorithm_on_off: int = 0,
    recording_mode: int = 0,
  ):
    """Aspiration of liquid using the 96 head.

    Args:
      type_of_aspiration: Type of aspiration (0 = simple 1 = sequence 2 = cup emptied).
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
        command [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      lld_search_height: LLD search height [0.1mm].
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      pull_out_distance_to_take_transport_air_in_function_without_lld:
          Pull out distance to take transp. air in function without LLD [0.1mm].
      minimum_height: Minimum height (maximum immersion depth) [0.1mm].
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from zm [0.1mm].
      tube_2nd_section_ratio: Tube 2nd section ratio.
      immersion_depth: Immersion depth [0.1mm].
      surface_following_distance: Surface following distance [0.1mm].
      aspiration_volume: Aspiration volume [0.01ul].
      aspiration_speed: Aspiration speed [0.1ul]/s.
      transport_air_volume: Transport air volume [0.1ul].
      blow_out_air_volume: Blow out air volume [0.01ul].
      pre_wetting_volume: Pre wetting volume [0.1ul].
      lld_mode: LLD Mode (0 = off).
      lld_sensitivity: LLD sensitivity (1 = high, 4 = low).
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s].
      settling_time: Settling time [0.1s].
      mix_volume: Mix volume [0.1ul].
      mix_cycles: Mix cycles.
      mix_position_in_z_direction_from_liquid_surface: Mix position in Z direction from liquid
        surface[0.1mm].
      surface_following_distance_during_mixing: Surface following distance during mixing [0.1mm].
      mix_speed: Mix speed [0.1ul/s].
      limit_curve_index: Limit curve index.
      tadm_channel_pattern: TADM Channel pattern.
      tadm_algorithm_on_off: TADM algorithm on/off (0 = off).
      recording_mode:
          Recording mode (0 = no 1 = TADM errors only 2 = all TADM measurements)
        .
    """

    if not 0 <= type_of_aspiration <= 2:
      raise ValueError("type_of_aspiration must be in range 0 to 2")

    if not -500000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -500000 to 50000")

    if not 422 <= y_position <= 5921:
      raise ValueError("y_position must be in range 422 to 5921")

    if not 0 <= minimal_traverse_height_at_begin_of_command <= 3900:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3900")

    if not 0 <= minimal_height_at_command_end <= 3900:
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3900")

    if not 0 <= lld_search_height <= 3900:
      raise ValueError("lld_search_height must be in range 0 to 3900")

    if not 0 <= liquid_surface_at_function_without_lld <= 3900:
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3900")

    if not 0 <= pull_out_distance_to_take_transport_air_in_function_without_lld <= 3900:
      raise ValueError(
        "pull_out_distance_to_take_transport_air_in_function_without_lld must be in range 0 to 3900"
      )

    if not 0 <= minimum_height <= 3900:
      raise ValueError("minimum_height must be in range 0 to 3900")

    if not 0 <= tube_2nd_section_height_measured_from_zm <= 3900:
      raise ValueError("tube_2nd_section_height_measured_from_zm must be in range 0 to 3900")

    if not 0 <= tube_2nd_section_ratio <= 10000:
      raise ValueError("tube_2nd_section_ratio must be in range 0 to 10000")

    if not -990 <= immersion_depth <= 990:
      raise ValueError("immersion_depth must be in range -990 to 990")

    if not 0 <= surface_following_distance <= 990:
      raise ValueError("surface_following_distance must be in range 0 to 990")

    if not 0 <= aspiration_volume <= 115000:
      raise ValueError("aspiration_volume must be in range 0 to 115000")

    if not 3 <= aspiration_speed <= 5000:
      raise ValueError("aspiration_speed must be in range 3 to 5000")

    if not 0 <= transport_air_volume <= 1000:
      raise ValueError("transport_air_volume must be in range 0 to 1000")

    if not 0 <= blow_out_air_volume <= 115000:
      raise ValueError("blow_out_air_volume must be in range 0 to 115000")

    if not 0 <= pre_wetting_volume <= 11500:
      raise ValueError("pre_wetting_volume must be in range 0 to 11500")

    if not 0 <= lld_mode <= 1:
      raise ValueError("lld_mode must be in range 0 to 1")

    if not 1 <= lld_sensitivity <= 4:
      raise ValueError("lld_sensitivity must be in range 1 to 4")

    if not 3 <= swap_speed <= 1000:
      raise ValueError("swap_speed must be in range 3 to 1000")

    if not 0 <= settling_time <= 99:
      raise ValueError("settling_time must be in range 0 to 99")

    if not 0 <= mix_volume <= 11500:
      raise ValueError("mix_volume must be in range 0 to 11500")

    if not 0 <= mix_cycles <= 99:
      raise ValueError("mix_cycles must be in range 0 to 99")

    if not 0 <= mix_position_in_z_direction_from_liquid_surface <= 990:
      raise ValueError("mix_position_in_z_direction_from_liquid_surface must be in range 0 to 990")

    if not 0 <= surface_following_distance_during_mixing <= 990:
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 990")

    if not 3 <= mix_speed <= 5000:
      raise ValueError("mix_speed must be in range 3 to 5000")

    if not 0 <= limit_curve_index <= 999:
      raise ValueError("limit_curve_index must be in range 0 to 999")

    if tadm_channel_pattern is None:
      tadm_channel_pattern = [True] * 96
    elif len(tadm_channel_pattern) != 96:
      raise ValueError(
        f"tadm_channel_pattern must be of length 96, but is '{len(tadm_channel_pattern)}'"
      )

    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")

    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    return await self._driver.send_command(
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
      cw=_tadm_channel_pattern_to_hex(tadm_channel_pattern),
      gj=tadm_algorithm_on_off,
      gk=recording_mode,
    )

  async def core96_dispensing_of_liquid(
    self,
    type_of_dispensing_mode: int = 0,
    x_position: int = 5000,
    y_position: int = 5000,
    minimum_height: int = 3900,
    tube_2nd_section_height_measured_from_zm: int = 0,
    tube_2nd_section_ratio: int = 0,
    lld_search_height: int = 0,
    liquid_surface_at_function_without_lld: int = 3900,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    immersion_depth: int = 0,
    surface_following_distance: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3900,
    minimal_height_at_command_end: int = 3900,
    dispense_volume: int = 0,
    dispense_speed: int = 2000,
    cut_off_speed: int = 1500,
    stop_back_volume: int = 0,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 1000,
    lld_mode: int = 1,
    lld_sensitivity: int = 1,
    side_touch_off_distance: int = 0,
    swap_speed: int = 100,
    settling_time: int = 5,
    mix_volume: int = 0,
    mix_cycles: int = 0,
    mix_position_in_z_direction_from_liquid_surface: int = 0,
    surface_following_distance_during_mixing: int = 0,
    mix_speed: int = 2000,
    limit_curve_index: int = 0,
    tadm_channel_pattern: Optional[List[bool]] = None,
    tadm_algorithm_on_off: int = 0,
    recording_mode: int = 0,
  ):
    """Dispensing of liquid using the 96 head.

    Args:
      type_of_dispensing_mode: Type of dispensing mode 0 = part in jet 1 = blow in jet 2 = Part at
          surface 3 = Blow at surface 4 = Empty.
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      minimum_height: Minimum height (maximum immersion depth) [0.1mm].
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from zm [0.1mm].
      tube_2nd_section_ratio: Tube 2nd section ratio.
      lld_search_height: LLD search height [0.1mm].
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      pull_out_distance_to_take_transport_air_in_function_without_lld:
          Pull out distance to take transp. air in function without LLD [0.1mm]
        .
      immersion_depth: Immersion depth [0.1mm].
      surface_following_distance: Surface following distance [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
        command [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
      dispense_volume: Dispense volume [0.01ul].
      dispense_speed: Dispense speed [0.1ul/s].
      cut_off_speed: Cut off speed [0.1ul/s].
      stop_back_volume: Stop back volume [0.1ul].
      transport_air_volume: Transport air volume [0.1ul].
      blow_out_air_volume: Blow out air volume [0.01ul].
      lld_mode: LLD Mode (0 = off).
      lld_sensitivity: LLD sensitivity (1 = high, 4 = low).
      side_touch_off_distance: Side touch off distance [0.1mm].
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s].
      settling_time: Settling time [0.1s].
      mix_volume: Mix volume [0.1ul].
      mix_cycles: Mix cycles.
      mix_position_in_z_direction_from_liquid_surface: Mix position in Z direction from liquid
        surface[0.1mm].
      surface_following_distance_during_mixing: Surface following distance during mixing [0.1mm].
      mix_speed: Mix speed [0.1ul/s].
      limit_curve_index: Limit curve index.
      tadm_channel_pattern: TADM Channel pattern.
      tadm_algorithm_on_off: TADM algorithm on/off (0 = off).
      recording_mode:
          Recording mode (0 = no 1 = TADM errors only 2 = all TADM measurements)
        .
    """

    if not 0 <= type_of_dispensing_mode <= 4:
      raise ValueError("type_of_dispensing_mode must be in range 0 to 4")

    if not -500000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -500000 to 50000")

    if not 422 <= y_position <= 5921:
      raise ValueError("y_position must be in range 422 to 5921")

    if not 0 <= minimum_height <= 3900:
      raise ValueError("minimum_height must be in range 0 to 3900")

    if not 0 <= tube_2nd_section_height_measured_from_zm <= 3900:
      raise ValueError("tube_2nd_section_height_measured_from_zm must be in range 0 to 3900")

    if not 0 <= tube_2nd_section_ratio <= 10000:
      raise ValueError("tube_2nd_section_ratio must be in range 0 to 10000")

    if not 0 <= lld_search_height <= 3900:
      raise ValueError("lld_search_height must be in range 0 to 3900")

    if not 0 <= liquid_surface_at_function_without_lld <= 3900:
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3900")

    if not 0 <= pull_out_distance_to_take_transport_air_in_function_without_lld <= 3900:
      raise ValueError(
        "pull_out_distance_to_take_transport_air_in_function_without_lld must be in range 0 to 3900"
      )

    if not -990 <= immersion_depth <= 990:
      raise ValueError("immersion_depth must be in range -990 to 990")

    if not 0 <= surface_following_distance <= 990:
      raise ValueError("surface_following_distance must be in range 0 to 990")

    if not 0 <= minimal_traverse_height_at_begin_of_command <= 3900:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3900")

    if not 0 <= minimal_height_at_command_end <= 3900:
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3900")

    if not 0 <= dispense_volume <= 115000:
      raise ValueError("dispense_volume must be in range 0 to 115000")

    if not 3 <= dispense_speed <= 5000:
      raise ValueError("dispense_speed must be in range 3 to 5000")

    if not 3 <= cut_off_speed <= 5000:
      raise ValueError("cut_off_speed must be in range 3 to 5000")

    if not 0 <= stop_back_volume <= 2000:
      raise ValueError("stop_back_volume must be in range 0 to 2000")

    if not 0 <= transport_air_volume <= 1000:
      raise ValueError("transport_air_volume must be in range 0 to 1000")

    if not 0 <= blow_out_air_volume <= 115000:
      raise ValueError("blow_out_air_volume must be in range 0 to 115000")

    if not 0 <= lld_mode <= 1:
      raise ValueError("lld_mode must be in range 0 to 1")

    if not 1 <= lld_sensitivity <= 4:
      raise ValueError("lld_sensitivity must be in range 1 to 4")

    if not 0 <= side_touch_off_distance <= 30:
      raise ValueError("side_touch_off_distance must be in range 0 to 30")

    if not 3 <= swap_speed <= 1000:
      raise ValueError("swap_speed must be in range 3 to 1000")

    if not 0 <= settling_time <= 99:
      raise ValueError("settling_time must be in range 0 to 99")

    if not 0 <= mix_volume <= 11500:
      raise ValueError("mix_volume must be in range 0 to 11500")

    if not 0 <= mix_cycles <= 99:
      raise ValueError("mix_cycles must be in range 0 to 99")

    if not 0 <= mix_position_in_z_direction_from_liquid_surface <= 990:
      raise ValueError("mix_position_in_z_direction_from_liquid_surface must be in range 0 to 990")

    if not 0 <= surface_following_distance_during_mixing <= 990:
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 990")

    if not 3 <= mix_speed <= 5000:
      raise ValueError("mix_speed must be in range 3 to 5000")

    if not 0 <= limit_curve_index <= 999:
      raise ValueError("limit_curve_index must be in range 0 to 999")

    if tadm_channel_pattern is None:
      tadm_channel_pattern = [True] * 96
    elif len(tadm_channel_pattern) != 96:
      raise ValueError(
        f"tadm_channel_pattern must be of length 96, but is '{len(tadm_channel_pattern)}'"
      )

    if not 0 <= tadm_algorithm_on_off <= 1:
      raise ValueError("tadm_algorithm_on_off must be in range 0 to 1")

    if not 0 <= recording_mode <= 2:
      raise ValueError("recording_mode must be in range 0 to 2")

    return await self._driver.send_command(
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
      cw=_tadm_channel_pattern_to_hex(tadm_channel_pattern),
      gj=tadm_algorithm_on_off,
      gk=recording_mode,
    )

  async def core96_tip_pick_up(
    self,
    x_position: int = 5000,
    y_position: int = 5000,
    tip_type: int = 4,
    tip_handling_method: int = 0,
    z_deposit_position: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3900,
    minimal_height_at_command_end: int = 3900,
  ):
    """Tip Pick up using the 96 head.

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      tip_type: Tip type (see command TT).
      tip_handling_method: Tip handling method.
      z_deposit_position: Z deposit position [0.1mm] (collar bearing position).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
        command [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """

    if not -500000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -500000 to 50000")

    if not 422 <= y_position <= 5921:
      raise ValueError("y_position must be in range 422 to 5921")

    if not 0 <= tip_type <= 199:
      raise ValueError("tip_type must be in range 0 to 199")

    if not 0 <= tip_handling_method <= 2:
      raise ValueError("tip_handling_method must be in range 0 to 2")

    if not 0 <= z_deposit_position <= 3900:
      raise ValueError("z_deposit_position must be in range 0 to 3900")

    if not 0 <= minimal_traverse_height_at_begin_of_command <= 3900:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3900")

    if not 0 <= minimal_height_at_command_end <= 3900:
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3900")

    return await self._driver.send_command(
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

  async def core96_tip_discard(
    self,
    x_position: int = 5000,
    y_position: int = 5000,
    z_deposit_position: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3900,
    minimal_height_at_command_end: int = 3900,
  ):
    """Tip Discard using the 96 head.

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      z_deposit_position: Z deposit position [0.1mm] (collar bearing position).
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
        command [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """

    if not -500000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -500000 to 50000")

    if not 422 <= y_position <= 5921:
      raise ValueError("y_position must be in range 422 to 5921")

    if not 0 <= z_deposit_position <= 3900:
      raise ValueError("z_deposit_position must be in range 0 to 3900")

    if not 0 <= minimal_traverse_height_at_begin_of_command <= 3900:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3900")

    if not 0 <= minimal_height_at_command_end <= 3900:
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3900")

    return await self._driver.send_command(
      module="A1HM",
      command="TR",
      xp=x_position,
      yp=y_position,
      tz=z_deposit_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
    )

  async def core96_move_to_defined_position(
    self,
    x_position: int = 5000,
    y_position: int = 5000,
    z_position: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3900,
  ):
    """Move to defined position using the 96 head.

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      z_position: Z Position [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
       command [0.1mm].
    """

    if not -500000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -500000 to 50000")

    if not 422 <= y_position <= 5921:
      raise ValueError("y_position must be in range 422 to 5921")

    if not 0 <= z_position <= 3900:
      raise ValueError("z_position must be in range 0 to 3900")

    if not 0 <= minimal_traverse_height_at_begin_of_command <= 3900:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3900")

    return await self._driver.send_command(
      module="A1HM",
      command="DN",
      xp=x_position,
      yp=y_position,
      zp=z_position,
      th=minimal_traverse_height_at_begin_of_command,
    )

  async def core96_wash_tips(
    self,
    x_position: int = 5000,
    y_position: int = 5000,
    liquid_surface_at_function_without_lld: int = 3900,
    minimum_height: int = 3900,
    surface_following_distance_during_mixing: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3900,
    mix_volume: int = 0,
    mix_cycles: int = 0,
    mix_speed: int = 2000,
  ):
    """Wash tips on the 96 head.

    Args:
      x_position: X Position [0.1mm].
      y_position: Y Position [0.1mm].
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      minimum_height: Minimum height (maximum immersion depth) [0.1mm].
      surface_following_distance_during_mixing: Surface following distance during mixing [0.1mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of command
        [0.1mm].
      mix_volume: Mix volume [0.1ul].
      mix_cycles: Mix cycles.
      mix_speed: Mix speed [0.1ul/s].
    """

    if not -500000 <= x_position <= 50000:
      raise ValueError("x_position must be in range -500000 to 50000")

    if not 422 <= y_position <= 5921:
      raise ValueError("y_position must be in range 422 to 5921")

    if not 0 <= liquid_surface_at_function_without_lld <= 3900:
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3900")

    if not 0 <= minimum_height <= 3900:
      raise ValueError("minimum_height must be in range 0 to 3900")

    if not 0 <= surface_following_distance_during_mixing <= 990:
      raise ValueError("surface_following_distance_during_mixing must be in range 0 to 990")

    if not 0 <= minimal_traverse_height_at_begin_of_command <= 3900:
      raise ValueError("minimal_traverse_height_at_begin_of_command must be in range 0 to 3900")

    if not 0 <= mix_volume <= 11500:
      raise ValueError("mix_volume must be in range 0 to 11500")

    if not 0 <= mix_cycles <= 99:
      raise ValueError("mix_cycles must be in range 0 to 99")

    if not 3 <= mix_speed <= 5000:
      raise ValueError("mix_speed must be in range 3 to 5000")

    return await self._driver.send_command(
      module="A1HM",
      command="DW",
      xp=x_position,
      yp=y_position,
      zl=liquid_surface_at_function_without_lld,
      zx=minimum_height,
      mh=surface_following_distance_during_mixing,
      th=minimal_traverse_height_at_begin_of_command,
      mv=mix_volume,
      mc=mix_cycles,
      ms=mix_speed,
    )

  async def core96_empty_washed_tips(
    self,
    liquid_surface_at_function_without_lld: int = 3900,
    minimal_height_at_command_end: int = 3900,
  ):
    """Empty washed tips (end of wash procedure only) on the 96 head.

    Args:
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
      minimal_height_at_command_end: Minimal height at command end [0.1mm].
    """

    if not 0 <= liquid_surface_at_function_without_lld <= 3900:
      raise ValueError("liquid_surface_at_function_without_lld must be in range 0 to 3900")

    if not 0 <= minimal_height_at_command_end <= 3900:
      raise ValueError("minimal_height_at_command_end must be in range 0 to 3900")

    return await self._driver.send_command(
      module="A1HM",
      command="EE",
      zl=liquid_surface_at_function_without_lld,
      te=minimal_height_at_command_end,
    )

  async def core96_search_for_teach_in_signal_in_x_direction(
    self,
    x_search_distance: int = 0,
    x_speed: int = 50,
  ):
    """Search for Teach in signal in X direction on the 96 head.

    Args:
      x_search_distance: X search distance [0.1mm].
      x_speed: X speed [0.1mm/s].
    """

    if not -50000 <= x_search_distance <= 50000:
      raise ValueError("x_search_distance must be in range -50000 to 50000")

    if not 20 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 20 to 25000")

    return await self._driver.send_command(
      module="A1HM",
      command="DL",
      xs=x_search_distance,
      xv=x_speed,
    )

  async def core96_set_any_parameter(self):
    """Set any parameter within the 96 head module."""

    return await self._driver.send_command(
      module="A1HM",
      command="AA",
    )

  async def core96_query_tip_presence(self):
    """Query Tip presence on the 96 head."""

    return await self._driver.send_command(
      module="A1HM",
      command="QA",
    )

  async def core96_request_position(self):
    """Request position of the 96 head."""

    return await self._driver.send_command(
      module="A1HM",
      command="QI",
    )

  async def core96_request_tadm_error_status(
    self,
    tadm_channel_pattern: Optional[List[bool]] = None,
  ):
    """Request TADM error status on the 96 head.

    Args:
      tadm_channel_pattern: TADM Channel pattern.
    """

    if tadm_channel_pattern is None:
      tadm_channel_pattern = [True] * 96
    elif len(tadm_channel_pattern) != 96:
      raise ValueError(
        f"tadm_channel_pattern must be of length 96, but is '{len(tadm_channel_pattern)}'"
      )

    return await self._driver.send_command(
      module="A1HM",
      command="VB",
      cw=_tadm_channel_pattern_to_hex(tadm_channel_pattern),
    )
