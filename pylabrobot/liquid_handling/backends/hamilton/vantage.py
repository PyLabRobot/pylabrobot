# pylint: disable=invalid-name

import sys
from typing import List, Optional, Union, cast

from pylabrobot.liquid_handling.backends.hamilton import HamiltonLiquidHandler
from pylabrobot.liquid_handling.standard import (
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  Dispense,
  DispensePlate,
  Move
)
from pylabrobot.resources import Resource
from pylabrobot.resources.ml_star import HamiltonTip, TipPickupMethod, TipSize


if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


class Vantage(HamiltonLiquidHandler):
  """ A Hamilton Vantage liquid handler. """

  def __init__(
    self,
    device_address: Optional[int] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """ Create a new STAR interface.

    Args:
      device_address: the USB device address of the Hamilton STAR. Only useful if using more than
        one Hamilton machine over USB.
      packet_read_timeout: timeout in seconds for reading a single packet.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
      num_channels: the number of pipette channels present on the robot.
    """

    super().__init__(
      device_address=device_address,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      id_product=0x8003)

    self._iswap_parked: Optional[bool] = None
    self._num_channels: Optional[int] = None

  @property
  def module_id_length(self) -> int:
    return  4

  async def setup(self):
    """ setup

    Creates a USB connection and finds read/write interfaces.
    """

    await super().setup()

    self._num_channels = 8 # TODO: query

    await self.pre_initialize_instrument()

    # TODO: check if already initialized
    await self.pip_initialize(
      x_position=[7095]*self.num_channels,
      y_position=[3891, 3623, 3355, 3087, 2819, 2551, 2283, 2016],
      begin_z_deposit_position=[2450] * self.num_channels,
      end_z_deposit_position=[1235] * self.num_channels,
      minimal_height_at_command_end=[2450] * self.num_channels,
      tip_pattern=[True]*self.num_channels,
      tip_type=[1]*self.num_channels,
      TODO_DI_2=70
    )

    await self.loading_cover_initialize()

  @property
  def num_channels(self) -> int:
    """ The number of channels on the robot. """
    if self._num_channels is None:
      raise RuntimeError("num_channels is not set.")
    return self._num_channels

  # ============== LiquidHandlerBackend methods ==============

  async def assigned_resource_callback(self, resource: Resource):
    print(f"Resource {resource.name} was assigned to the robot.")

  async def unassigned_resource_callback(self, name: str):
    print(f"Resource {name} was unassigned from the robot.")

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    x_positions, y_positions, tip_pattern = \
      self._ops_to_fw_positions(ops, use_channels)

    tips = [cast(HamiltonTip, op.resource.get_tip()) for op in ops]
    ttti = await self.get_ttti(tips)

    max_z = max(op.resource.get_absolute_location().z + \
                 (op.offset.z if op.offset is not None else 0) for op in ops)
    max_total_tip_length = max(op.tip.total_tip_length for op in ops)
    max_tip_length = max((op.tip.total_tip_length-op.tip.fitting_depth) for op in ops)

    # not sure why this is necessary, but it is according to log files and experiments
    if self._get_hamilton_tip([op.resource for op in ops]).tip_size == TipSize.LOW_VOLUME:
      max_tip_length += 2
    elif self._get_hamilton_tip([op.resource for op in ops]).tip_size != TipSize.STANDARD_VOLUME:
      max_tip_length -= 2

    try:
      return await self.pip_tip_pick_up(
        x_position=x_positions,
        y_position=y_positions,
        tip_pattern=tip_pattern,
        tip_type=ttti,
        begin_z_deposit_position=[int((max_z + max_total_tip_length)*10)]*len(ops),
        end_z_deposit_position=[int((max_z + max_tip_length)*10)]*len(ops),
        minimal_traverse_height_at_begin_of_command=[2450]*len(ops),
        minimal_height_at_command_end=[2450]*len(ops),
        tip_handling_method=[1 for _ in tips], # always appears to be 1 # tip.pickup_method.value
        blow_out_air_volume=[0]*len(ops), # Why is this here? Who knows.
      )
    except Exception as e:
      raise e

  # @need_iswap_parked
  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
  ):
    """ Drop tips to a resource. """

    x_positions, y_positions, channels_involved = \
      self._ops_to_fw_positions(ops, use_channels)

    max_z = max(op.resource.get_absolute_location().z + \
                (op.offset.z if op.offset is not None else 0) for op in ops)

    try:
      return await self.pip_tip_discard(
        x_position=x_positions,
        y_position=y_positions,
        tip_pattern=channels_involved,
        begin_z_deposit_position=[int((max_z+10)*10)]*len(ops), # +10
        end_z_deposit_position=[int(max_z*10)]*len(ops),
        minimal_traverse_height_at_begin_of_command=[2450]*len(ops),
        minimal_height_at_command_end=[2450]*len(ops),
        tip_handling_method=[0 for _ in ops], # Always appears to be 0, even in trash.
        # tip_handling_method=[TipDropMethod.DROP.value if isinstance(op.resource, TipSpot) \
        #                      else TipDropMethod.PLACE_SHIFT.value for op in ops],
        TODO_TR_2=0,
      )
    except Exception as e:
      raise e

  async def aspirate(self, ops: List[Aspiration], use_channels: List[int]):
    print(f"Aspirating {ops}.")
    raise NotImplementedError()

  async def dispense(self, ops: List[Dispense], use_channels: List[int]):
    print(f"Dispensing {ops}.")
    raise NotImplementedError()

  async def pick_up_tips96(self, pickup: PickupTipRack):
    print(f"Picking up tips from {pickup.resource.name}.")
    raise NotImplementedError()

  async def drop_tips96(self, drop: DropTipRack):
    print(f"Dropping tips to {drop.resource.name}.")
    raise NotImplementedError()

  async def aspirate96(self, aspiration: AspirationPlate):
    print(f"Aspirating {aspiration.volume} from {aspiration.resource}.")
    raise NotImplementedError()

  async def dispense96(self, dispense: DispensePlate):
    print(f"Dispensing {dispense.volume} to {dispense.resource}.")
    raise NotImplementedError()

  async def move_resource(self, move: Move):
    print(f"Moving {move}.")
    raise NotImplementedError()

  # ============== Firmware Commands ==============

  async def set_led_color(
    self,
    mode: Union[Literal["on"], Literal["off"], Literal["blink"]],
    intensity: int,
    white: int,
    red: int,
    green: int,
    blue: int,
    uv: int,
    blink_interval: Optional[int] = None,
  ):
    """ Set the LED color.

    Args:
      mode: The mode of the LED. One of "on", "off", or "blink".
      intensity: The intensity of the LED. 0-100.
      white: The white color of the LED. 0-100.
      red: The red color of the LED. 0-100.
      green: The green color of the LED. 0-100.
      blue: The blue color of the LED. 0-100.
      uv: The UV color of the LED. 0-100.
      blink_interval: The blink interval in ms. Only used if mode is "blink".
    """

    if blink_interval is not None:
      if mode != "blink":
        raise ValueError("blink_interval is only used when mode is 'blink'.")

    return await self.send_command(
      module="C0AM",
      command="LI",
      li={
        "on": 1,
        "off": 0,
        "blink": 2,
      }[mode],
      os=intensity,
      ok=blink_interval or 750, # default non zero value
      ol=f"{white} {red} {green} {blue} {uv}",
    )

  async def set_loading_cover(self, cover_open: bool):
    """ Set the loading cover.

    Args:
      cover_open: Whether the cover should be open or closed.
    """

    return await self.send_command(
      module="I1AM",
      command="LP",
      lc=not cover_open
    )

  def loading_cover_initialize(self):
    """ Initialize the loading cover. """

    return self.send_command(
      module="I1AM",
      command="MI",
    )

  async def pre_initialize_instrument(self):
    """ Initialize the main instrument. """

    return await self.send_command(module="A1AM", command="MI")

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
    """ Initialize

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

    return await self.send_command(
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

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod
  ):
    """ Tip/needle definition.

    Args:
      tip_type_table_index: tip_table_index
      filter: with(out) filter
      tip_length: Tip length [0.1mm]
      maximum_tip_volume: Maximum volume of tip [0.1ul] Note! it's automatically limited to max.
        channel capacity
      tip_type: Type of tip collar (Tip type identification)
      pickup_method: pick up method.  Attention! The values set here are temporary and apply only
        until power OFF or RESET. After power ON the default values apply. (see Table 3)
    """

    if not 0 <= tip_type_table_index <= 99:
      raise ValueError("tip_type_table_index must be between 0 and 99, but is "
                       f"{tip_type_table_index}")
    if not 0 <= tip_type_table_index <= 99:
      raise ValueError("tip_type_table_index must be between 0 and 99, but is "
                       f"{tip_type_table_index}")
    if not 1 <= tip_length <= 1999:
      raise ValueError("tip_length must be between 1 and 1999, but is "
                       f"{tip_length}")
    if not 1 <= maximum_tip_volume <= 56000:
      raise ValueError("maximum_tip_volume must be between 1 and 56000, but is "
                       f"{maximum_tip_volume}")

    return await self.send_command(
      module="A1AM",
      command="TT",
      ti=f"{tip_type_table_index:02}",
      tf=has_filter,
      tl=f"{tip_length:04}",
      tv=f"{maximum_tip_volume:05}",
      tg=tip_size.value,
      tu=pickup_method.value
    )

  async def aspiration_of_liquid(
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
    """ Aspiration of liquid

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
      recording_mode:
          Recording mode (0 = no 1 = TADM errors only 2 = all TADM measurements)
        .
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
    elif not all(0 <= x <= 3600 for x in
                 pull_out_distance_to_take_transport_air_in_function_without_lld):
      raise ValueError("pull_out_distance_to_take_transport_air_in_function_without_lld must be "
                       "in range 0 to 3600")

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

    return await self.send_command(
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
      ar=TODO_DA_2,
      as_=aspiration_speed,
      ta=transport_air_volume,
      ba=blow_out_air_volume,
      oa=pre_wetting_volume,
      lm=lld_mode,
      ll=lld_sensitivity,
      lv=pressure_lld_sensitivity,
      zo=aspirate_position_above_z_touch_off,
      lg=TODO_DA_4,
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

  async def dispensing_of_liquid(
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
    """ Dispensing of liquid

    Args:
      type_of_dispensing_mode:
          Type of dispensing mode 0 = part in jet 1 = blow in jet 2 = Part at
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
    elif not all(0 <= x <= 3600 for x in
                  pull_out_distance_to_take_transport_air_in_function_without_lld):
      raise ValueError("pull_out_distance_to_take_transport_air_in_function_without_lld must be "
                       "in range 0 to 3600")

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

    return await self.send_command(
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
      dv=dispense_volume,
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
    """ Simultaneous aspiration & dispensation of liquid

    Args:
      type_of_aspiration: Type of aspiration (0 = simple 1 = sequence 2 = cup emptied).
      type_of_dispensing_mode:
          Type of dispensing mode 0 = part in jet 1 = blow in jet 2 = Part at
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
    elif not all(0 <= x <= 3600
      for x in pull_out_distance_to_take_transport_air_in_function_without_lld):
      raise ValueError("pull_out_distance_to_take_transport_air_in_function_without_lld must be "
                       "in range 0 to 3600")

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

    return await self.send_command(
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
    TODO_DF_1: int = 0,
    TODO_DF_2: int = 0,
    TODO_DF_3: int = 100,
    TODO_DF_4: int = 900,
    x_speed: int = 270,
    TODO_DF_5: int = 1,
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
    """ Dispense on fly

    Args:
      tip_pattern: Tip pattern (channels involved). [0 = not involved, 1 = involved].
      TODO_DF_1: (0).
      TODO_DF_2: (0).
      TODO_DF_3: (0).
      TODO_DF_4: (0).
      x_speed: X speed [0.1mm/s].
      TODO_DF_5: (0).
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

    if not -50000 <= TODO_DF_1 <= 50000:
      raise ValueError("TODO_DF_1 must be in range -50000 to 50000")

    if not -50000 <= TODO_DF_2 <= 50000:
      raise ValueError("TODO_DF_2 must be in range -50000 to 50000")

    if not 0 <= TODO_DF_3 <= 900:
      raise ValueError("TODO_DF_3 must be in range 0 to 900")

    if not 1 <= TODO_DF_4 <= 2500:
      raise ValueError("TODO_DF_4 must be in range 1 to 2500")

    if not 20 <= x_speed <= 25000:
      raise ValueError("x_speed must be in range 20 to 25000")

    if not 1 <= TODO_DF_5 <= 48:
      raise ValueError("TODO_DF_5 must be in range 1 to 48")

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

    return await self.send_command(
      module="A1PM",
      command="DF",
      tm=tip_pattern,
      xa=TODO_DF_1,
      xf=TODO_DF_2,
      xh=TODO_DF_3,
      xy=TODO_DF_4,
      xv=x_speed,
      xi=TODO_DF_5,
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
    """ Nano pulse dispense

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

    return await self.send_command(
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
    """ Wash tips

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

    return await self.send_command(
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
    """ Tip Pick up

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

    return await self.send_command(
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
    """ Tip Discard

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

    return await self.send_command(
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

  async def search_for_teach_in_signal_in_x_direction(
    self,
    channel_index: int = 1,
    x_search_distance: int = 0,
    x_speed: int = 270,
  ):
    """ Search for Teach in signal in X direction

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

    return await self.send_command(
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
    """ Position all channels in Y direction

    Args:
      y_position: Y Position [0.1mm].
    """

    if y_position is None:
      y_position = [3000] * self.num_channels
    elif not all(0 <= x <= 6500 for x in y_position):
      raise ValueError("y_position must be in range 0 to 6500")

    return await self.send_command(
      module="A1PM",
      command="DY",
      yp=y_position,
    )

  async def position_all_channels_in_z_direction(
    self,
    z_position: Optional[List[int]] = None,
  ):
    """ Position all channels in Z direction

    Args:
      z_position: Z Position [0.1mm].
    """

    if z_position is None:
      z_position = [0] * self.num_channels
    elif not all(0 <= x <= 3600 for x in z_position):
      raise ValueError("z_position must be in range 0 to 3600")

    return await self.send_command(
      module="A1PM",
      command="DZ",
      zp=z_position,
    )

  async def position_single_channel_in_y_direction(
    self,
    channel_index: int = 1,
    y_position: int = 3000,
  ):
    """ Position single channel in Y direction

    Args:
      channel_index: Channel index.
      y_position: Y Position [0.1mm].
    """

    if not 1 <= channel_index <= 16:
      raise ValueError("channel_index must be in range 1 to 16")

    if not 0 <= y_position <= 6500:
      raise ValueError("y_position must be in range 0 to 6500")

    return await self.send_command(
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
    """ Position single channel in Z direction

    Args:
      channel_index: Channel index.
      z_position: Z Position [0.1mm].
    """

    if not 1 <= channel_index <= 16:
      raise ValueError("channel_index must be in range 1 to 16")

    if not 0 <= z_position <= 3600:
      raise ValueError("z_position must be in range 0 to 3600")

    return await self.send_command(
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
    """ Move to defined position

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

    return await self.send_command(
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
    """ Teach rack using channel n

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

    return await self.send_command(
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
    """ Expose channel n

    Args:
      channel_index: Channel index.
    """

    if not 1 <= channel_index <= 16:
      raise ValueError("channel_index must be in range 1 to 16")

    return await self.send_command(
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
    """ Calculates check sums and compares them with the value saved in Flash EPROM

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

    return await self.send_command(
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
    """ Discard CoRe gripper tool

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

    return await self.send_command(
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
    """ Grip plate

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

    return await self.send_command(
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
    """ Put plate

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

    return await self.send_command(
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
    """ Move to position

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

    return await self.send_command(
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
    """ Release object

    Args:
      first_pip_channel_node_no: First (lower) pip. channel node no. (0 = disabled).
    """

    if not 1 <= first_pip_channel_node_no <= 16:
      raise ValueError("first_pip_channel_node_no must be in range 1 to 16")

    return await self.send_command(
      module="A1PM",
      command="DO",
      pa=first_pip_channel_node_no,
    )

  async def set_any_parameter_within_this_module(self):
    """ Set any parameter within this module """

    return await self.send_command(
      module="A1PM",
      command="AA",
    )

  async def request_y_positions_of_all_channels(self):
    """ Request Y Positions of all channels """

    return await self.send_command(
      module="A1PM",
      command="RY",
    )

  async def request_y_position_of_channel_n(self):
    """ Request Y Position of channel n """

    return await self.send_command(
      module="A1PM",
      command="RB",
    )

  async def request_z_positions_of_all_channels(self):
    """ Request Z Positions of all channels """

    return await self.send_command(
      module="A1PM",
      command="RZ",
    )

  async def request_z_position_of_channel_n(self):
    """ Request Z Position of channel n """

    return await self.send_command(
      module="A1PM",
      command="RD",
    )

  async def query_tip_presence(self):
    """ Query Tip presence """

    return await self.send_command(
      module="A1PM",
      command="QA",
    )

  async def request_height_of_last_lld(self):
    """ Request height of last LLD """

    return await self.send_command(
      module="A1PM",
      command="RL",
    )

  async def request_channel_dispense_on_fly_status(self):
    """ Request channel dispense on fly status """

    return await self.send_command(
      module="A1PM",
      command="QF",
    )

