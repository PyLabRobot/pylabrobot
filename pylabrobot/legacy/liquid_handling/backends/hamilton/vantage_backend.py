import sys
import warnings
from typing import Dict, List, Optional, Sequence, Union

from pylabrobot.capabilities.liquid_handling.standard import (
  Aspiration as NewAspiration,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  Dispense as NewDispense,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  DropTipRack as NewDropTipRack,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  MultiHeadAspirationContainer as NewMultiHeadAspirationContainer,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  MultiHeadAspirationPlate as NewMultiHeadAspirationPlate,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  MultiHeadDispenseContainer as NewMultiHeadDispenseContainer,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  MultiHeadDispensePlate as NewMultiHeadDispensePlate,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  Pickup as NewPickup,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  PickupTipRack as NewPickupTipRack,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  TipDrop as NewTipDrop,
)
from pylabrobot.hamilton.liquid_handlers.vantage.head96_backend import VantageHead96Backend
from pylabrobot.hamilton.liquid_handlers.vantage.ipg import IPGBackend
from pylabrobot.hamilton.liquid_handlers.vantage.pip_backend import VantagePIPBackend
from pylabrobot.legacy.liquid_handling.backends.hamilton.base import (
  HamiltonLiquidHandler,
)
from pylabrobot.legacy.liquid_handling.liquid_classes.hamilton import (
  HamiltonLiquidClass,
)
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  DropTipRack,
  GripDirection,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import (
  Coordinate,
  Resource,
  Tip,
)
from pylabrobot.resources.hamilton import (
  TipPickupMethod,
  TipSize,
)

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


# Re-export from the new implementation. These used to be defined inline here.
from pylabrobot.hamilton.liquid_handlers.vantage.errors import (  # noqa: F401
  VantageFirmwareError,
  core96_errors,
  ipg_errors,
  pip_errors,
  vantage_response_string_to_error,
)
from pylabrobot.hamilton.liquid_handlers.vantage.fw_parsing import (  # noqa: F401
  parse_vantage_fw_string,
)


def _get_dispense_mode(jet: bool, empty: bool, blow_out: bool) -> Literal[0, 1, 2, 3, 4]:
  """from docs:
  0 = part in jet
  1 = blow in jet (called "empty" in VENUS liquid editor)
  2 = Part at surface
  3 = Blow at surface (called "empty" in VENUS liquid editor)
  4 = Empty (truly empty)
  """

  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  else:
    return 3 if blow_out else 2


class VantageBackend(HamiltonLiquidHandler):
  """A Hamilton Vantage liquid handler."""

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 60,
    write_timeout: int = 30,
  ):
    """Create a new STAR interface.

    Args:
      device_address: the USB device address of the Hamilton Vantage. Only useful if using more than
        one Hamilton machine over USB.
      serial_number: the serial number of the Hamilton Vantage.
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
      id_product=0x8003,
      serial_number=serial_number,
    )

    from pylabrobot.hamilton.liquid_handlers.vantage.driver import VantageDriver

    self.driver = VantageDriver(
      device_address=device_address,
      serial_number=serial_number,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

    self._iswap_parked: Optional[bool] = None
    self._num_channels: Optional[int] = None
    self._setup_done = False

  # -- property accessors for new-arch subsystems ----------------------------

  @property
  def _vantage_pip(self):
    """Typed access to the Vantage PIP backend."""
    return self.driver.pip

  @property
  def _vantage_head96(self):
    """Typed access to the Head96 backend."""
    assert self.driver.head96 is not None, "96-head is not installed"
    return self.driver.head96

  @property
  def _vantage_ipg(self):
    """Typed access to the IPG backend."""
    assert self.driver.ipg is not None, "IPG is not installed"
    return self.driver.ipg

  @property
  def _vantage_x_arm(self):
    """Typed access to the X-arm."""
    assert self.driver.x_arm is not None, "X-arm is not available"
    return self.driver.x_arm

  @property
  def _vantage_loading_cover(self):
    """Typed access to the loading cover."""
    assert self.driver.loading_cover is not None, "Loading cover is not available"
    return self.driver.loading_cover

  @property
  def _vantage_core_gripper(self):
    """Typed access to the VantageCoreGripper backend."""
    from pylabrobot.hamilton.liquid_handlers.vantage.core import VantageCoreGripper

    if not hasattr(self, "_core_gripper_instance"):
      self._core_gripper_instance = VantageCoreGripper(driver=self.driver)
    return self._core_gripper_instance

  @property
  def _write_and_read_command(self):
    return self.driver._write_and_read_command

  @_write_and_read_command.setter
  def _write_and_read_command(self, value):
    self.driver._write_and_read_command = value  # type: ignore[method-assign]

  # -- HamiltonLiquidHandler abstract methods (delegate to driver) -----------

  @property
  def module_id_length(self) -> int:
    return 4

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Get the id from a firmware response."""
    parsed = parse_vantage_fw_string(resp, {"id": "int"})
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def check_fw_string_error(self, resp: str):
    """Raise an error if the firmware response is an error response."""

    if "er" in resp and "er0" not in resp:
      error = vantage_response_string_to_error(resp)
      raise error

  def _parse_response(self, resp: str, fmt: Dict[str, str]) -> dict:
    """Parse a firmware response."""
    return parse_vantage_fw_string(resp, fmt)

  # -- send_command delegation -----------------------------------------------

  async def send_command(
    self,
    module,
    command,
    auto_id=True,
    tip_pattern=None,
    write_timeout=None,
    read_timeout=None,
    wait=True,
    fmt=None,
    **kwargs,
  ):
    return await self.driver.send_command(
      module=module,
      command=command,
      auto_id=auto_id,
      tip_pattern=tip_pattern,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
      fmt=fmt,
      **kwargs,
    )

  # -- lifecycle (delegate to driver) ----------------------------------------

  async def setup(
    self,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
    """Creates a USB connection and finds read/write interfaces."""

    # Let the driver own the USB connection and perform hardware discovery.
    await self.driver.setup(
      skip_loading_cover=skip_loading_cover,
      skip_core96=skip_core96,
      skip_ipg=skip_ipg,
    )

    # Sync legacy state from driver.
    self.id_ = 0
    self._num_channels = self.driver.num_channels
    self._setup_done = True

  async def stop(self):
    await self.driver.stop()
    self._setup_done = False

  @property
  def setup_done(self) -> bool:
    return self._setup_done

  @property
  def num_channels(self) -> int:
    """The number of channels on the robot."""
    if self._num_channels is None:
      raise RuntimeError("num_channels is not set.")
    return self._num_channels

  @property
  def _traversal_height(self) -> float:
    return self.driver.traversal_height

  def set_minimum_traversal_height(self, traversal_height: float):
    """Deprecated: use ``VantageDriver.set_minimum_traversal_height``."""
    self.driver.set_minimum_traversal_height(traversal_height)

  # ============== LiquidHandlerBackend methods ==============

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
  ):
    """Deprecated: use ``VantagePIPBackend.pick_up_tips``."""
    new_ops = [NewPickup(resource=op.resource, offset=op.offset, tip=op.tip) for op in ops]
    await self._vantage_pip.pick_up_tips(
      new_ops,
      use_channels,
      backend_params=VantagePIPBackend.PickUpTipsParams(
        minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command,
        minimal_height_at_command_end=minimal_height_at_command_end,
      ),
    )

  # @need_iswap_parked
  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
  ):
    """Deprecated: use ``VantagePIPBackend.drop_tips``."""
    new_ops = [NewTipDrop(resource=op.resource, offset=op.offset, tip=op.tip) for op in ops]
    await self._vantage_pip.drop_tips(
      new_ops,
      use_channels,
      backend_params=VantagePIPBackend.DropTipsParams(
        minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command,
        minimal_height_at_command_end=minimal_height_at_command_end,
      ),
    )

  def _assert_valid_resources(self, resources: Sequence[Resource]) -> None:
    """Assert that resources are in a valid location for pipetting."""
    for resource in resources:
      if resource.get_location_wrt(self.deck).z < 100:
        raise ValueError(
          f"Resource {resource} is too low: {resource.get_location_wrt(self.deck).z} < 100"
        )

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    jet: Optional[List[bool]] = None,
    blow_out: Optional[List[bool]] = None,
    hlcs: Optional[List[Optional[HamiltonLiquidClass]]] = None,
    type_of_aspiration: Optional[List[int]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
    lld_search_height: Optional[List[float]] = None,
    clot_detection_height: Optional[List[float]] = None,
    liquid_surface_at_function_without_lld: Optional[List[float]] = None,
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[float]] = None,
    tube_2nd_section_height_measured_from_zm: Optional[List[float]] = None,
    tube_2nd_section_ratio: Optional[List[float]] = None,
    minimum_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    pre_wetting_volume: Optional[List[float]] = None,
    lld_mode: Optional[List[int]] = None,
    lld_sensitivity: Optional[List[int]] = None,
    pressure_lld_sensitivity: Optional[List[int]] = None,
    aspirate_position_above_z_touch_off: Optional[List[float]] = None,
    swap_speed: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    mix_volume: Optional[List[float]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_in_z_direction_from_liquid_surface: Optional[List[float]] = None,
    mix_speed: Optional[List[float]] = None,
    surface_following_distance_during_mixing: Optional[List[float]] = None,
    TODO_DA_5: Optional[List[int]] = None,
    capacitive_mad_supervision_on_off: Optional[List[int]] = None,
    pressure_mad_supervision_on_off: Optional[List[int]] = None,
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
    disable_volume_correction: Optional[List[bool]] = None,
  ):
    """Deprecated: use ``VantagePIPBackend.aspirate``."""
    # Legacy mix kwargs are not supported; raise early.
    if mix_volume is not None or mix_cycles is not None or mix_speed is not None:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of "
        "LiquidHandler.dispense instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )

    new_ops = [
      NewAspiration(
        resource=op.resource,
        offset=op.offset,
        tip=op.tip,
        volume=op.volume,
        flow_rate=op.flow_rate,
        liquid_height=op.liquid_height,
        blow_out_air_volume=op.blow_out_air_volume,
        mix=op.mix,
      )
      for op in ops
    ]
    # TODO_DA_5, mix_position_in_z_direction_from_liquid_surface,
    # surface_following_distance_during_mixing have no BackendParams equivalent; dropped.
    await self._vantage_pip.aspirate(
      new_ops,
      use_channels,
      backend_params=VantagePIPBackend.AspirateParams(
        jet=jet,
        blow_out=blow_out,
        hlcs=hlcs,
        type_of_aspiration=type_of_aspiration,
        minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command,
        minimal_height_at_command_end=minimal_height_at_command_end,
        lld_search_height=lld_search_height,
        clot_detection_height=clot_detection_height,
        liquid_surface_at_function_without_lld=liquid_surface_at_function_without_lld,
        pull_out_distance_to_take_transport_air_in_function_without_lld=(
          pull_out_distance_to_take_transport_air_in_function_without_lld
        ),
        tube_2nd_section_height_measured_from_zm=tube_2nd_section_height_measured_from_zm,
        tube_2nd_section_ratio=tube_2nd_section_ratio,
        minimum_height=minimum_height,
        immersion_depth=immersion_depth,
        surface_following_distance=surface_following_distance,
        transport_air_volume=transport_air_volume,
        pre_wetting_volume=pre_wetting_volume,
        lld_mode=lld_mode,
        lld_sensitivity=lld_sensitivity,
        pressure_lld_sensitivity=pressure_lld_sensitivity,
        aspirate_position_above_z_touch_off=aspirate_position_above_z_touch_off,
        swap_speed=swap_speed,
        settling_time=settling_time,
        capacitive_mad_supervision_on_off=capacitive_mad_supervision_on_off,
        pressure_mad_supervision_on_off=pressure_mad_supervision_on_off,
        tadm_algorithm_on_off=tadm_algorithm_on_off,
        limit_curve_index=limit_curve_index,
        recording_mode=recording_mode,
        disable_volume_correction=disable_volume_correction,
      ),
    )

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    jet: Optional[List[bool]] = None,
    blow_out: Optional[List[bool]] = None,  # "empty" in the VENUS liquid editor
    empty: Optional[List[bool]] = None,  # truly "empty", does not exist in liquid editor, dm4
    hlcs: Optional[List[Optional[HamiltonLiquidClass]]] = None,
    type_of_dispensing_mode: Optional[List[int]] = None,
    minimum_height: Optional[List[float]] = None,
    pull_out_distance_to_take_transport_air_in_function_without_lld: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    tube_2nd_section_height_measured_from_zm: Optional[List[float]] = None,
    tube_2nd_section_ratio: Optional[List[float]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
    lld_search_height: Optional[List[float]] = None,
    cut_off_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    lld_mode: Optional[List[int]] = None,
    side_touch_off_distance: float = 0,
    dispense_position_above_z_touch_off: Optional[List[float]] = None,
    lld_sensitivity: Optional[List[int]] = None,
    pressure_lld_sensitivity: Optional[List[int]] = None,
    swap_speed: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    mix_volume: Optional[List[float]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_in_z_direction_from_liquid_surface: Optional[List[float]] = None,
    mix_speed: Optional[List[float]] = None,
    surface_following_distance_during_mixing: Optional[List[float]] = None,
    TODO_DD_2: Optional[List[int]] = None,
    tadm_algorithm_on_off: int = 0,
    limit_curve_index: Optional[List[int]] = None,
    recording_mode: int = 0,
    disable_volume_correction: Optional[List[bool]] = None,
  ):
    """Deprecated: use ``VantagePIPBackend.dispense``."""
    # Legacy mix kwargs are not supported; raise early.
    if mix_volume is not None or mix_cycles is not None or mix_speed is not None:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of "
        "LiquidHandler.dispense instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )

    new_ops = [
      NewDispense(
        resource=op.resource,
        offset=op.offset,
        tip=op.tip,
        volume=op.volume,
        flow_rate=op.flow_rate,
        liquid_height=op.liquid_height,
        blow_out_air_volume=op.blow_out_air_volume,
        mix=op.mix,
      )
      for op in ops
    ]
    # TODO_DD_2, mix_position_in_z_direction_from_liquid_surface,
    # surface_following_distance_during_mixing have no BackendParams equivalent; dropped.
    await self._vantage_pip.dispense(
      new_ops,
      use_channels,
      backend_params=VantagePIPBackend.DispenseParams(
        jet=jet,
        blow_out=blow_out,
        empty=empty,
        hlcs=hlcs,
        type_of_dispensing_mode=type_of_dispensing_mode,
        minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command,
        minimal_height_at_command_end=minimal_height_at_command_end,
        lld_search_height=lld_search_height,
        minimum_height=minimum_height,
        pull_out_distance_to_take_transport_air_in_function_without_lld=(
          pull_out_distance_to_take_transport_air_in_function_without_lld
        ),
        immersion_depth=immersion_depth,
        surface_following_distance=surface_following_distance,
        tube_2nd_section_height_measured_from_zm=tube_2nd_section_height_measured_from_zm,
        tube_2nd_section_ratio=tube_2nd_section_ratio,
        cut_off_speed=cut_off_speed,
        stop_back_volume=stop_back_volume,
        transport_air_volume=transport_air_volume,
        lld_mode=lld_mode,
        side_touch_off_distance=side_touch_off_distance,
        dispense_position_above_z_touch_off=dispense_position_above_z_touch_off,
        lld_sensitivity=lld_sensitivity,
        pressure_lld_sensitivity=pressure_lld_sensitivity,
        swap_speed=swap_speed,
        settling_time=settling_time,
        tadm_algorithm_on_off=tadm_algorithm_on_off,
        limit_curve_index=limit_curve_index,
        recording_mode=recording_mode,
        disable_volume_correction=disable_volume_correction,
      ),
    )

  async def pick_up_tips96(
    self,
    pickup: PickupTipRack,
    tip_handling_method: int = 0,
    z_deposit_position: float = 216.4,
    minimal_traverse_height_at_begin_of_command: Optional[float] = None,
    minimal_height_at_command_end: Optional[float] = None,
  ):
    """Deprecated: use ``VantageHead96Backend.pick_up_tips96``."""
    new_pickup = NewPickupTipRack(resource=pickup.resource, offset=pickup.offset, tips=pickup.tips)
    await self._vantage_head96.pick_up_tips96(
      new_pickup,
      backend_params=VantageHead96Backend.PickUpTipsParams(
        tip_handling_method=tip_handling_method,
        z_deposit_position=z_deposit_position,
        minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command,
        minimal_height_at_command_end=minimal_height_at_command_end,
      ),
    )

  async def drop_tips96(
    self,
    drop: DropTipRack,
    z_deposit_position: float = 216.4,
    minimal_traverse_height_at_begin_of_command: Optional[float] = None,
    minimal_height_at_command_end: Optional[float] = None,
  ):
    """Deprecated: use ``VantageHead96Backend.drop_tips96``."""
    new_drop = NewDropTipRack(resource=drop.resource, offset=drop.offset)
    await self._vantage_head96.drop_tips96(
      new_drop,
      backend_params=VantageHead96Backend.DropTipsParams(
        z_deposit_position=z_deposit_position,
        minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command,
        minimal_height_at_command_end=minimal_height_at_command_end,
      ),
    )

  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    jet: bool = False,
    blow_out: bool = False,
    hlc: Optional[HamiltonLiquidClass] = None,
    type_of_aspiration: int = 0,
    minimal_traverse_height_at_begin_of_command: Optional[float] = None,
    minimal_height_at_command_end: Optional[float] = None,
    pull_out_distance_to_take_transport_air_in_function_without_lld: float = 5,
    tube_2nd_section_height_measured_from_zm: float = 0,
    tube_2nd_section_ratio: float = 0,
    immersion_depth: float = 0,
    surface_following_distance: float = 0,
    transport_air_volume: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    pre_wetting_volume: float = 0,
    lld_mode: int = 0,
    lld_sensitivity: int = 4,
    swap_speed: Optional[float] = None,
    settling_time: Optional[float] = None,
    mix_volume: float = 0,
    mix_cycles: int = 0,
    mix_position_in_z_direction_from_liquid_surface: float = 0,
    surface_following_distance_during_mixing: float = 0,
    mix_speed: float = 0,
    limit_curve_index: int = 0,
    tadm_channel_pattern: Optional[List[bool]] = None,
    tadm_algorithm_on_off: int = 0,
    recording_mode: int = 0,
    disable_volume_correction: bool = False,
  ):
    """Deprecated: use ``VantageHead96Backend.aspirate96``."""
    # Legacy mix kwargs are not supported; raise early.
    if mix_volume != 0 or mix_cycles != 0 or mix_speed != 0:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of "
        "LiquidHandler.dispense96 instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )

    # Convert legacy type to capability type.
    if isinstance(aspiration, MultiHeadAspirationPlate):
      new_asp = NewMultiHeadAspirationPlate(
        wells=aspiration.wells,
        offset=aspiration.offset,
        tips=aspiration.tips,
        volume=aspiration.volume,
        flow_rate=aspiration.flow_rate,
        liquid_height=aspiration.liquid_height,
        blow_out_air_volume=aspiration.blow_out_air_volume,
        mix=aspiration.mix,
      )
    else:
      new_asp = NewMultiHeadAspirationContainer(
        container=aspiration.container,
        offset=aspiration.offset,
        tips=aspiration.tips,
        volume=aspiration.volume,
        flow_rate=aspiration.flow_rate,
        liquid_height=aspiration.liquid_height,
        blow_out_air_volume=aspiration.blow_out_air_volume,
        mix=aspiration.mix,
      )

    await self._vantage_head96.aspirate96(
      new_asp,
      backend_params=VantageHead96Backend.AspirateParams(
        jet=jet,
        blow_out=blow_out,
        hlc=hlc,
        type_of_aspiration=type_of_aspiration,
        minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command,
        minimal_height_at_command_end=minimal_height_at_command_end,
        pull_out_distance_to_take_transport_air_in_function_without_lld=(
          pull_out_distance_to_take_transport_air_in_function_without_lld
        ),
        tube_2nd_section_height_measured_from_zm=tube_2nd_section_height_measured_from_zm,
        tube_2nd_section_ratio=tube_2nd_section_ratio,
        immersion_depth=immersion_depth,
        surface_following_distance=surface_following_distance,
        transport_air_volume=transport_air_volume,
        blow_out_air_volume=blow_out_air_volume,
        pre_wetting_volume=pre_wetting_volume,
        lld_mode=lld_mode,
        lld_sensitivity=lld_sensitivity,
        swap_speed=swap_speed,
        settling_time=settling_time,
        limit_curve_index=limit_curve_index,
        tadm_channel_pattern=tadm_channel_pattern,
        tadm_algorithm_on_off=tadm_algorithm_on_off,
        recording_mode=recording_mode,
        disable_volume_correction=disable_volume_correction,
      ),
    )

  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    jet: bool = False,
    blow_out: bool = False,  # "empty" in the VENUS liquid editor
    empty: bool = False,  # truly "empty", does not exist in liquid editor, dm4
    hlc: Optional[HamiltonLiquidClass] = None,
    type_of_dispensing_mode: Optional[int] = None,
    tube_2nd_section_height_measured_from_zm: float = 0,
    tube_2nd_section_ratio: float = 0,
    pull_out_distance_to_take_transport_air_in_function_without_lld: float = 5.0,
    immersion_depth: float = 0,
    surface_following_distance: float = 2.9,
    minimal_traverse_height_at_begin_of_command: Optional[float] = None,
    minimal_height_at_command_end: Optional[float] = None,
    cut_off_speed: float = 250.0,
    stop_back_volume: float = 0,
    transport_air_volume: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    lld_mode: int = 0,
    lld_sensitivity: int = 4,
    side_touch_off_distance: float = 0,
    swap_speed: Optional[float] = None,
    settling_time: Optional[float] = None,
    mix_volume: float = 0,
    mix_cycles: int = 0,
    mix_position_in_z_direction_from_liquid_surface: float = 0,
    surface_following_distance_during_mixing: float = 0,
    mix_speed: Optional[float] = None,
    limit_curve_index: int = 0,
    tadm_channel_pattern: Optional[List[bool]] = None,
    tadm_algorithm_on_off: int = 0,
    recording_mode: int = 0,
    disable_volume_correction: bool = False,
  ):
    """Deprecated: use ``VantageHead96Backend.dispense96``."""
    # Legacy mix kwargs are not supported; raise early.
    if mix_volume != 0 or mix_cycles != 0 or mix_speed is not None:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of "
        "LiquidHandler.dispense96 instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )

    # Convert legacy type to capability type.
    if isinstance(dispense, MultiHeadDispensePlate):
      new_disp = NewMultiHeadDispensePlate(
        wells=dispense.wells,
        offset=dispense.offset,
        tips=dispense.tips,
        volume=dispense.volume,
        flow_rate=dispense.flow_rate,
        liquid_height=dispense.liquid_height,
        blow_out_air_volume=dispense.blow_out_air_volume,
        mix=dispense.mix,
      )
    else:
      new_disp = NewMultiHeadDispenseContainer(
        container=dispense.container,
        offset=dispense.offset,
        tips=dispense.tips,
        volume=dispense.volume,
        flow_rate=dispense.flow_rate,
        liquid_height=dispense.liquid_height,
        blow_out_air_volume=dispense.blow_out_air_volume,
        mix=dispense.mix,
      )

    await self._vantage_head96.dispense96(
      new_disp,
      backend_params=VantageHead96Backend.DispenseParams(
        jet=jet,
        blow_out=blow_out,
        empty=empty,
        hlc=hlc,
        type_of_dispensing_mode=type_of_dispensing_mode,
        tube_2nd_section_height_measured_from_zm=tube_2nd_section_height_measured_from_zm,
        tube_2nd_section_ratio=tube_2nd_section_ratio,
        pull_out_distance_to_take_transport_air_in_function_without_lld=(
          pull_out_distance_to_take_transport_air_in_function_without_lld
        ),
        immersion_depth=immersion_depth,
        surface_following_distance=surface_following_distance,
        minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command,
        minimal_height_at_command_end=minimal_height_at_command_end,
        cut_off_speed=cut_off_speed,
        stop_back_volume=stop_back_volume,
        transport_air_volume=transport_air_volume,
        blow_out_air_volume=blow_out_air_volume,
        lld_mode=lld_mode,
        lld_sensitivity=lld_sensitivity,
        side_touch_off_distance=side_touch_off_distance,
        swap_speed=swap_speed,
        settling_time=settling_time,
        limit_curve_index=limit_curve_index,
        tadm_channel_pattern=tadm_channel_pattern,
        tadm_algorithm_on_off=tadm_algorithm_on_off,
        recording_mode=recording_mode,
        disable_volume_correction=disable_volume_correction,
      ),
    )

  async def pick_up_resource(
    self,
    pickup: ResourcePickup,
    grip_strength: int = 81,
    plate_width_tolerance: float = 2.0,
    acceleration_index: int = 4,
    z_clearance_height: float = 0,
    hotel_depth: float = 0,
    minimal_height_at_command_end: float = 284.0,
  ):
    """Deprecated: use ``IPGBackend.pick_up_at_location``."""
    center = pickup.resource.get_absolute_location(x="c", y="c", z="b") + pickup.offset
    grip_height = center.z + pickup.resource.get_absolute_size_z() - pickup.pickup_distance_from_top
    resource_width = pickup.resource.get_absolute_size_x()

    direction_map = {
      GripDirection.FRONT: 0,
      GripDirection.RIGHT: 90,
      GripDirection.BACK: 180,
      GripDirection.LEFT: 270,
    }
    direction = direction_map[pickup.direction]

    await self._vantage_ipg.pick_up_at_location(
      location=Coordinate(x=center.x, y=center.y, z=grip_height),
      direction=direction,
      resource_width=resource_width,
      backend_params=IPGBackend.PickUpParams(
        grip_strength=grip_strength,
        plate_width_tolerance=plate_width_tolerance,
        acceleration_index=acceleration_index,
        z_clearance_height=z_clearance_height,
        hotel_depth=hotel_depth,
        minimal_height_at_command_end=minimal_height_at_command_end,
      ),
    )

  async def move_picked_up_resource(self, move: ResourceMove):
    """Deprecated: use ``IPGBackend.move_to_location``.

    You probably want to use :meth:`move_resource`, which allows you to pick up and move a resource
    with a single command.
    """
    grip_height = (
      move.location.z
      + move.resource.get_absolute_size_z()
      - move.pickup_distance_from_top
      + move.offset.z
    )
    await self._vantage_ipg.move_to_location(
      location=Coordinate(
        x=move.location.x + move.offset.x,
        y=move.location.y + move.offset.y,
        z=grip_height,
      ),
      direction=0,  # direction not used for movement
    )

  async def drop_resource(
    self,
    drop: ResourceDrop,
    z_clearance_height: float = 0,
    press_on_distance: int = 5,
    hotel_depth: float = 0,
    minimal_height_at_command_end: float = 284.0,
  ):
    """Deprecated: use ``IPGBackend.drop_at_location``."""
    center = drop.destination + drop.resource.center() + drop.offset
    grip_height = center.z + drop.resource.get_absolute_size_z() - drop.pickup_distance_from_top
    resource_width = drop.resource.get_absolute_size_x()

    direction_map = {
      GripDirection.FRONT: 0,
      GripDirection.RIGHT: 90,
      GripDirection.BACK: 180,
      GripDirection.LEFT: 270,
    }
    direction = direction_map[drop.direction]

    await self._vantage_ipg.drop_at_location(
      location=Coordinate(x=center.x, y=center.y, z=grip_height),
      direction=direction,
      resource_width=resource_width,
      backend_params=IPGBackend.DropParams(
        z_clearance_height=z_clearance_height,
        press_on_distance=press_on_distance / 10,
        hotel_depth=hotel_depth,
        minimal_height_at_command_end=minimal_height_at_command_end,
      ),
    )

  async def prepare_for_manual_channel_operation(self, channel: int):
    """Deprecated: use ``vantage.driver.pip.expose_channel_n()``."""
    return await self.expose_channel_n(channel_index=channel + 1)

  async def move_channel_x(self, channel: int, x: float):
    """Deprecated: use ``vantage.driver.x_arm.move_to()``."""
    return await self._vantage_x_arm.move_to(x)

  async def move_channel_y(self, channel: int, y: float):
    """Deprecated: use ``vantage.driver.pip.position_single_channel_in_y_direction()``."""
    return await self._vantage_pip.position_single_channel_in_y_direction(channel + 1, y)

  async def move_channel_z(self, channel: int, z: float):
    """Deprecated: use ``vantage.driver.pip.position_single_channel_in_z_direction()``."""
    return await self._vantage_pip.position_single_channel_in_z_direction(channel + 1, z)

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    """Deprecated: use ``VantagePIPBackend.can_pick_up_tip``."""
    return self._vantage_pip.can_pick_up_tip(channel_idx, tip)

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
    """Deprecated: use ``VantageDriver.set_led_color``."""
    return await self.driver.set_led_color(
      mode, intensity, white, red, green, blue, uv, blink_interval
    )

  async def set_loading_cover(self, cover_open: bool):
    """Deprecated: use ``VantageLoadingCover.set_cover``."""
    return await self._vantage_loading_cover.set_cover(cover_open)

  async def loading_cover_request_initialization_status(self) -> bool:
    """Deprecated: use ``VantageLoadingCover.request_initialization_status``."""
    return await self._vantage_loading_cover.request_initialization_status()

  async def loading_cover_initialize(self):
    """Deprecated: use ``VantageLoadingCover.initialize``."""
    return await self._vantage_loading_cover.initialize()

  async def arm_request_instrument_initialization_status(
    self,
  ) -> bool:
    """Deprecated: use ``VantageDriver.arm_request_instrument_initialization_status``."""
    return await self.driver.arm_request_instrument_initialization_status()

  async def arm_pre_initialize(self):
    """Deprecated: use ``VantageDriver.arm_pre_initialize``."""
    return await self.driver.arm_pre_initialize()

  async def pip_request_initialization_status(self) -> bool:
    """Deprecated: use ``VantageDriver.pip_request_initialization_status``."""
    return await self.driver.pip_request_initialization_status()

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
    """Deprecated: use ``VantageDriver.pip_initialize``.

    Note: this legacy method accepts values in 0.1mm and converts to mm for the new API.
    """
    return await self.driver.pip_initialize(
      x_position=[v / 10 for v in x_position],
      y_position=[v / 10 for v in y_position],
      begin_z_deposit_position=[v / 10 for v in begin_z_deposit_position]
      if begin_z_deposit_position is not None
      else None,
      end_z_deposit_position=[v / 10 for v in end_z_deposit_position]
      if end_z_deposit_position is not None
      else None,
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end]
      if minimal_height_at_command_end is not None
      else None,
      tip_pattern=tip_pattern,
      tip_type=tip_type,
      ts=TODO_DI_2,
    )

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod,
  ):
    """Deprecated: use ``VantageDriver.define_tip_needle``."""
    return await self.driver.define_tip_needle(
      tip_type_table_index, has_filter, tip_length, maximum_tip_volume, tip_size, pickup_method
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
    """Deprecated: use ``VantagePIPBackend._pip_aspirate``."""

    if type_of_aspiration is None:
      type_of_aspiration = [0] * self.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    if lld_search_height is None:
      lld_search_height = [0] * self.num_channels
    if clot_detection_height is None:
      clot_detection_height = [60] * self.num_channels
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * self.num_channels
    if pull_out_distance_to_take_transport_air_in_function_without_lld is None:
      pull_out_distance_to_take_transport_air_in_function_without_lld = [50] * self.num_channels
    if tube_2nd_section_height_measured_from_zm is None:
      tube_2nd_section_height_measured_from_zm = [0] * self.num_channels
    if tube_2nd_section_ratio is None:
      tube_2nd_section_ratio = [0] * self.num_channels
    if minimum_height is None:
      minimum_height = [3600] * self.num_channels
    if immersion_depth is None:
      immersion_depth = [0] * self.num_channels
    if surface_following_distance is None:
      surface_following_distance = [0] * self.num_channels
    if aspiration_volume is None:
      aspiration_volume = [0] * self.num_channels
    if aspiration_speed is None:
      aspiration_speed = [500] * self.num_channels
    if transport_air_volume is None:
      transport_air_volume = [0] * self.num_channels
    if blow_out_air_volume is None:
      blow_out_air_volume = [0] * self.num_channels
    if pre_wetting_volume is None:
      pre_wetting_volume = [0] * self.num_channels
    if lld_mode is None:
      lld_mode = [1] * self.num_channels
    if lld_sensitivity is None:
      lld_sensitivity = [1] * self.num_channels
    if pressure_lld_sensitivity is None:
      pressure_lld_sensitivity = [1] * self.num_channels
    if aspirate_position_above_z_touch_off is None:
      aspirate_position_above_z_touch_off = [5] * self.num_channels
    if swap_speed is None:
      swap_speed = [100] * self.num_channels
    if settling_time is None:
      settling_time = [5] * self.num_channels
    if mix_volume is None:
      mix_volume = [0] * self.num_channels
    if mix_cycles is None:
      mix_cycles = [0] * self.num_channels
    if mix_position_in_z_direction_from_liquid_surface is None:
      mix_position_in_z_direction_from_liquid_surface = [250] * self.num_channels
    if mix_speed is None:
      mix_speed = [500] * self.num_channels
    if surface_following_distance_during_mixing is None:
      surface_following_distance_during_mixing = [0] * self.num_channels
    if TODO_DA_5 is None:
      TODO_DA_5 = [0] * self.num_channels
    if capacitive_mad_supervision_on_off is None:
      capacitive_mad_supervision_on_off = [0] * self.num_channels
    if pressure_mad_supervision_on_off is None:
      pressure_mad_supervision_on_off = [0] * self.num_channels
    if limit_curve_index is None:
      limit_curve_index = [0] * self.num_channels

    return await self._vantage_pip._pip_aspirate(
      x_position=x_position,
      y_position=y_position,
      type_of_aspiration=type_of_aspiration,
      tip_pattern=tip_pattern,
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
      lld_search_height=[v / 10 for v in lld_search_height],
      clot_detection_height=[v / 10 for v in clot_detection_height],
      liquid_surface_at_function_without_lld=[
        v / 10 for v in liquid_surface_at_function_without_lld
      ],
      pull_out_distance_to_take_transport_air_in_function_without_lld=[
        v / 10 for v in pull_out_distance_to_take_transport_air_in_function_without_lld
      ],
      tube_2nd_section_height_measured_from_zm=[
        v / 10 for v in tube_2nd_section_height_measured_from_zm
      ],
      tube_2nd_section_ratio=[v / 10 for v in tube_2nd_section_ratio],
      minimum_height=[v / 10 for v in minimum_height],
      immersion_depth=[v / 10 for v in immersion_depth],
      surface_following_distance=[v / 10 for v in surface_following_distance],
      aspiration_volume=[v / 100 for v in aspiration_volume],
      aspiration_speed=[v / 10 for v in aspiration_speed],
      transport_air_volume=[v / 10 for v in transport_air_volume],
      blow_out_air_volume=[v / 100 for v in blow_out_air_volume],
      pre_wetting_volume=[v / 100 for v in pre_wetting_volume],
      lld_mode=lld_mode,
      lld_sensitivity=lld_sensitivity,
      pressure_lld_sensitivity=pressure_lld_sensitivity,
      aspirate_position_above_z_touch_off=[v / 10 for v in aspirate_position_above_z_touch_off],
      swap_speed=[v / 10 for v in swap_speed],
      settling_time=[v / 10 for v in settling_time],
      mix_volume=[v / 100 for v in mix_volume],
      mix_cycles=mix_cycles,
      mix_position_in_z_direction_from_liquid_surface=mix_position_in_z_direction_from_liquid_surface,
      mix_speed=[v / 10 for v in mix_speed],
      surface_following_distance_during_mixing=surface_following_distance_during_mixing,
      capacitive_mad_supervision_on_off=capacitive_mad_supervision_on_off,
      pressure_mad_supervision_on_off=pressure_mad_supervision_on_off,
      tadm_algorithm_on_off=tadm_algorithm_on_off,
      limit_curve_index=limit_curve_index,
      recording_mode=recording_mode,
      TODO_DA_5=TODO_DA_5,
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
    """Deprecated: use ``VantagePIPBackend._pip_dispense``."""

    if type_of_dispensing_mode is None:
      type_of_dispensing_mode = [0] * self.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    if minimum_height is None:
      minimum_height = [3600] * self.num_channels
    if lld_search_height is None:
      lld_search_height = [0] * self.num_channels
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * self.num_channels
    if pull_out_distance_to_take_transport_air_in_function_without_lld is None:
      pull_out_distance_to_take_transport_air_in_function_without_lld = [50] * self.num_channels
    if immersion_depth is None:
      immersion_depth = [0] * self.num_channels
    if surface_following_distance is None:
      surface_following_distance = [0] * self.num_channels
    if tube_2nd_section_height_measured_from_zm is None:
      tube_2nd_section_height_measured_from_zm = [0] * self.num_channels
    if tube_2nd_section_ratio is None:
      tube_2nd_section_ratio = [0] * self.num_channels
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    if dispense_volume is None:
      dispense_volume = [0] * self.num_channels
    if dispense_speed is None:
      dispense_speed = [500] * self.num_channels
    if cut_off_speed is None:
      cut_off_speed = [250] * self.num_channels
    if stop_back_volume is None:
      stop_back_volume = [0] * self.num_channels
    if transport_air_volume is None:
      transport_air_volume = [0] * self.num_channels
    if blow_out_air_volume is None:
      blow_out_air_volume = [0] * self.num_channels
    if lld_mode is None:
      lld_mode = [1] * self.num_channels
    if dispense_position_above_z_touch_off is None:
      dispense_position_above_z_touch_off = [5] * self.num_channels
    if lld_sensitivity is None:
      lld_sensitivity = [1] * self.num_channels
    if pressure_lld_sensitivity is None:
      pressure_lld_sensitivity = [1] * self.num_channels
    if swap_speed is None:
      swap_speed = [100] * self.num_channels
    if settling_time is None:
      settling_time = [5] * self.num_channels
    if mix_volume is None:
      mix_volume = [0] * self.num_channels
    if mix_cycles is None:
      mix_cycles = [0] * self.num_channels
    if mix_position_in_z_direction_from_liquid_surface is None:
      mix_position_in_z_direction_from_liquid_surface = [250] * self.num_channels
    if mix_speed is None:
      mix_speed = [500] * self.num_channels
    if surface_following_distance_during_mixing is None:
      surface_following_distance_during_mixing = [0] * self.num_channels
    if TODO_DD_2 is None:
      TODO_DD_2 = [0] * self.num_channels
    if limit_curve_index is None:
      limit_curve_index = [0] * self.num_channels

    return await self._vantage_pip._pip_dispense(
      x_position=x_position,
      y_position=y_position,
      tip_pattern=tip_pattern,
      type_of_dispensing_mode=type_of_dispensing_mode,
      minimum_height=[v / 10 for v in minimum_height],
      lld_search_height=[v / 10 for v in lld_search_height],
      liquid_surface_at_function_without_lld=[
        v / 10 for v in liquid_surface_at_function_without_lld
      ],
      pull_out_distance_to_take_transport_air_in_function_without_lld=[
        v / 10 for v in pull_out_distance_to_take_transport_air_in_function_without_lld
      ],
      immersion_depth=[v / 10 for v in immersion_depth],
      surface_following_distance=[v / 10 for v in surface_following_distance],
      tube_2nd_section_height_measured_from_zm=[
        v / 10 for v in tube_2nd_section_height_measured_from_zm
      ],
      tube_2nd_section_ratio=[v / 10 for v in tube_2nd_section_ratio],
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
      dispense_volume=[v / 100 for v in dispense_volume],
      dispense_speed=[v / 10 for v in dispense_speed],
      cut_off_speed=[v / 10 for v in cut_off_speed],
      stop_back_volume=[v / 100 for v in stop_back_volume],
      transport_air_volume=[v / 10 for v in transport_air_volume],
      blow_out_air_volume=[v / 100 for v in blow_out_air_volume],
      lld_mode=lld_mode,
      side_touch_off_distance=side_touch_off_distance / 10,
      dispense_position_above_z_touch_off=[v / 10 for v in dispense_position_above_z_touch_off],
      lld_sensitivity=lld_sensitivity,
      pressure_lld_sensitivity=pressure_lld_sensitivity,
      swap_speed=[v / 10 for v in swap_speed],
      settling_time=[v / 10 for v in settling_time],
      mix_volume=[v / 100 for v in mix_volume],
      mix_cycles=mix_cycles,
      mix_position_in_z_direction_from_liquid_surface=mix_position_in_z_direction_from_liquid_surface,
      mix_speed=[v / 10 for v in mix_speed],
      surface_following_distance_during_mixing=surface_following_distance_during_mixing,
      tadm_algorithm_on_off=tadm_algorithm_on_off,
      limit_curve_index=limit_curve_index,
      recording_mode=recording_mode,
      TODO_DD_2=TODO_DD_2,
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
    """Deprecated: delegates to VantagePIPBackend.simultaneous_aspiration_dispensation_of_liquid."""
    n = self.num_channels
    if type_of_aspiration is None:
      type_of_aspiration = [0] * n
    if type_of_dispensing_mode is None:
      type_of_dispensing_mode = [0] * n
    if tip_pattern is None:
      tip_pattern = [False] * n
    if TODO_DM_1 is None:
      TODO_DM_1 = [0] * n
    if y_position is None:
      y_position = [3000] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * n
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * n
    if lld_search_height is None:
      lld_search_height = [0] * n
    if clot_detection_height is None:
      clot_detection_height = [60] * n
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * n
    if pull_out_distance_to_take_transport_air_in_function_without_lld is None:
      pull_out_distance_to_take_transport_air_in_function_without_lld = [50] * n
    if minimum_height is None:
      minimum_height = [3600] * n
    if immersion_depth is None:
      immersion_depth = [0] * n
    if surface_following_distance is None:
      surface_following_distance = [0] * n
    if tube_2nd_section_height_measured_from_zm is None:
      tube_2nd_section_height_measured_from_zm = [0] * n
    if tube_2nd_section_ratio is None:
      tube_2nd_section_ratio = [0] * n
    if aspiration_volume is None:
      aspiration_volume = [0] * n
    if TODO_DM_3 is None:
      TODO_DM_3 = [0] * n
    if aspiration_speed is None:
      aspiration_speed = [500] * n
    if dispense_volume is None:
      dispense_volume = [0] * n
    if dispense_speed is None:
      dispense_speed = [500] * n
    if cut_off_speed is None:
      cut_off_speed = [250] * n
    if stop_back_volume is None:
      stop_back_volume = [0] * n
    if transport_air_volume is None:
      transport_air_volume = [0] * n
    if blow_out_air_volume is None:
      blow_out_air_volume = [0] * n
    if pre_wetting_volume is None:
      pre_wetting_volume = [0] * n
    if lld_mode is None:
      lld_mode = [1] * n
    if aspirate_position_above_z_touch_off is None:
      aspirate_position_above_z_touch_off = [5] * n
    if lld_sensitivity is None:
      lld_sensitivity = [1] * n
    if pressure_lld_sensitivity is None:
      pressure_lld_sensitivity = [1] * n
    if swap_speed is None:
      swap_speed = [100] * n
    if settling_time is None:
      settling_time = [5] * n
    if mix_volume is None:
      mix_volume = [0] * n
    if mix_cycles is None:
      mix_cycles = [0] * n
    if mix_position_in_z_direction_from_liquid_surface is None:
      mix_position_in_z_direction_from_liquid_surface = [250] * n
    if mix_speed is None:
      mix_speed = [500] * n
    if surface_following_distance_during_mixing is None:
      surface_following_distance_during_mixing = [0] * n
    if TODO_DM_5 is None:
      TODO_DM_5 = [0] * n
    if capacitive_mad_supervision_on_off is None:
      capacitive_mad_supervision_on_off = [0] * n
    if pressure_mad_supervision_on_off is None:
      pressure_mad_supervision_on_off = [0] * n
    if limit_curve_index is None:
      limit_curve_index = [0] * n

    return await self._vantage_pip.simultaneous_aspiration_dispensation_of_liquid(
      x_position=x_position,
      y_position=y_position,  # x_position and y_position are already in 0.1mm (firmware units)
      type_of_aspiration=type_of_aspiration,
      type_of_dispensing_mode=type_of_dispensing_mode,
      tip_pattern=tip_pattern,
      TODO_DM_1=TODO_DM_1,
      # distances: 0.1mm -> mm (/10)
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
      lld_search_height=[v / 10 for v in lld_search_height],
      clot_detection_height=[v / 10 for v in clot_detection_height],
      liquid_surface_at_function_without_lld=[
        v / 10 for v in liquid_surface_at_function_without_lld
      ],
      pull_out_distance_to_take_transport_air_in_function_without_lld=[
        v / 10 for v in pull_out_distance_to_take_transport_air_in_function_without_lld
      ],
      minimum_height=[v / 10 for v in minimum_height],
      immersion_depth=[v / 10 for v in immersion_depth],
      surface_following_distance=[v / 10 for v in surface_following_distance],
      tube_2nd_section_height_measured_from_zm=[
        v / 10 for v in tube_2nd_section_height_measured_from_zm
      ],
      tube_2nd_section_ratio=tube_2nd_section_ratio,
      # volumes: 0.01uL -> uL (/100)
      aspiration_volume=[v / 100 for v in aspiration_volume],
      TODO_DM_3=[v / 100 for v in TODO_DM_3],
      dispense_volume=[v / 100 for v in dispense_volume],
      blow_out_air_volume=[v / 100 for v in blow_out_air_volume],
      # speeds: 0.1uL/s -> uL/s (/10)
      aspiration_speed=[v / 10 for v in aspiration_speed],
      dispense_speed=[v / 10 for v in dispense_speed],
      cut_off_speed=[v / 10 for v in cut_off_speed],
      mix_speed=[v / 10 for v in mix_speed],
      # volumes: 0.1uL -> uL (/10)
      stop_back_volume=[v / 10 for v in stop_back_volume],
      transport_air_volume=[v / 10 for v in transport_air_volume],
      pre_wetting_volume=[v / 10 for v in pre_wetting_volume],
      mix_volume=[v / 10 for v in mix_volume],
      lld_mode=lld_mode,
      # distance: 0.1mm -> mm (/10)
      aspirate_position_above_z_touch_off=[v / 10 for v in aspirate_position_above_z_touch_off],
      lld_sensitivity=lld_sensitivity,
      pressure_lld_sensitivity=pressure_lld_sensitivity,
      # swap_speed: 0.1mm/s -> mm/s (/10)
      swap_speed=[v / 10 for v in swap_speed],
      # settling_time: 0.1s -> s (/10)
      settling_time=[v / 10 for v in settling_time],
      mix_cycles=mix_cycles,
      # distance: 0.1mm -> mm (/10)
      mix_position_in_z_direction_from_liquid_surface=[
        v / 10 for v in mix_position_in_z_direction_from_liquid_surface
      ],
      surface_following_distance_during_mixing=[
        v / 10 for v in surface_following_distance_during_mixing
      ],
      TODO_DM_5=TODO_DM_5,
      capacitive_mad_supervision_on_off=capacitive_mad_supervision_on_off,
      pressure_mad_supervision_on_off=pressure_mad_supervision_on_off,
      tadm_algorithm_on_off=tadm_algorithm_on_off,
      limit_curve_index=limit_curve_index,
      recording_mode=recording_mode,
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
    """Deprecated: delegates to VantagePIPBackend.dispense_on_fly."""
    n = self.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * n
    if y_position is None:
      y_position = [3000] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * n
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * n
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * n
    if dispense_volume is None:
      dispense_volume = [0] * n
    if dispense_speed is None:
      dispense_speed = [500] * n
    if cut_off_speed is None:
      cut_off_speed = [250] * n
    if stop_back_volume is None:
      stop_back_volume = [0] * n
    if transport_air_volume is None:
      transport_air_volume = [0] * n
    if limit_curve_index is None:
      limit_curve_index = [0] * n

    return await self._vantage_pip.dispense_on_fly(
      y_position=[v / 10 for v in y_position],
      tip_pattern=tip_pattern,
      first_shoot_x_pos=first_shoot_x_pos / 10,
      dispense_on_fly_pos_command_end=dispense_on_fly_pos_command_end / 10,
      x_acceleration_distance_before_first_shoot=x_acceleration_distance_before_first_shoot / 10,
      space_between_shoots=space_between_shoots / 100,
      x_speed=x_speed / 10,
      number_of_shoots=number_of_shoots,
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
      liquid_surface_at_function_without_lld=[
        v / 10 for v in liquid_surface_at_function_without_lld
      ],
      dispense_volume=[v / 100 for v in dispense_volume],
      dispense_speed=[v / 10 for v in dispense_speed],
      cut_off_speed=[v / 10 for v in cut_off_speed],
      stop_back_volume=[v / 10 for v in stop_back_volume],
      transport_air_volume=[v / 10 for v in transport_air_volume],
      tadm_algorithm_on_off=tadm_algorithm_on_off,
      limit_curve_index=limit_curve_index,
      recording_mode=recording_mode,
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
    """Deprecated: delegates to VantagePIPBackend.nano_pulse_dispense."""
    n = self.num_channels
    if TODO_DB_0 is None:
      TODO_DB_0 = [1] * n
    if y_position is None:
      y_position = [3000] * n
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * n
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * n
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
      TODO_DB_11 = [0] * n
    if TODO_DB_12 is None:
      TODO_DB_12 = [1] * n

    return await self._vantage_pip.nano_pulse_dispense(
      x_position=x_position,
      y_position=[v / 10 for v in y_position],
      TODO_DB_0=TODO_DB_0,
      liquid_surface_at_function_without_lld=[
        v / 10 for v in liquid_surface_at_function_without_lld
      ],
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
      TODO_DB_1=TODO_DB_1,
      TODO_DB_2=TODO_DB_2,
      TODO_DB_3=TODO_DB_3,
      TODO_DB_4=TODO_DB_4,
      TODO_DB_5=TODO_DB_5,
      TODO_DB_6=TODO_DB_6,
      TODO_DB_7=TODO_DB_7,
      TODO_DB_8=TODO_DB_8,
      TODO_DB_9=TODO_DB_9,
      TODO_DB_10=TODO_DB_10,
      TODO_DB_11=[v / 10 for v in TODO_DB_11],
      TODO_DB_12=TODO_DB_12,
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
    """Deprecated: delegates to VantagePIPBackend.wash_tips."""
    n = self.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * n
    if y_position is None:
      y_position = [3000] * n
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * n
    if liquid_surface_at_function_without_lld is None:
      liquid_surface_at_function_without_lld = [3600] * n
    if aspiration_volume is None:
      aspiration_volume = [0] * n
    if aspiration_speed is None:
      aspiration_speed = [500] * n
    if dispense_speed is None:
      dispense_speed = [500] * n
    if swap_speed is None:
      swap_speed = [100] * n
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * n

    return await self._vantage_pip.wash_tips(
      x_position=x_position,
      y_position=[v / 10 for v in y_position],
      tip_pattern=tip_pattern,
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      liquid_surface_at_function_without_lld=[
        v / 10 for v in liquid_surface_at_function_without_lld
      ],
      aspiration_volume=[v / 100 for v in aspiration_volume],
      aspiration_speed=[v / 10 for v in aspiration_speed],
      dispense_speed=[v / 10 for v in dispense_speed],
      swap_speed=[v / 10 for v in swap_speed],
      soak_time=soak_time,
      wash_cycles=wash_cycles,
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
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
    """Deprecated: use ``VantagePIPBackend._pip_tip_pick_up``."""

    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    if tip_type is None:
      tip_type = [4] * self.num_channels
    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0] * self.num_channels
    if end_z_deposit_position is None:
      end_z_deposit_position = [0] * self.num_channels
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    if blow_out_air_volume is None:
      blow_out_air_volume = [0] * self.num_channels
    if tip_handling_method is None:
      tip_handling_method = [0] * self.num_channels

    return await self._vantage_pip._pip_tip_pick_up(
      x_position=x_position,
      y_position=y_position,
      tip_pattern=tip_pattern,
      tip_type=tip_type,
      begin_z_deposit_position=[v / 10 for v in begin_z_deposit_position],
      end_z_deposit_position=[v / 10 for v in end_z_deposit_position],
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
      tip_handling_method=tip_handling_method,
      blow_out_air_volume=[v / 100 for v in blow_out_air_volume],
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
    """Deprecated: use ``VantagePIPBackend._pip_tip_discard``."""

    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0] * self.num_channels
    if end_z_deposit_position is None:
      end_z_deposit_position = [0] * self.num_channels
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    if tip_handling_method is None:
      tip_handling_method = [0] * self.num_channels

    return await self._vantage_pip._pip_tip_discard(
      x_position=x_position,
      y_position=y_position,
      tip_pattern=tip_pattern,
      begin_z_deposit_position=[v / 10 for v in begin_z_deposit_position],
      end_z_deposit_position=[v / 10 for v in end_z_deposit_position],
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
      tip_handling_method=tip_handling_method,
      TODO_TR_2=TODO_TR_2,
    )

  async def search_for_teach_in_signal_in_x_direction(
    self,
    channel_index: int = 1,
    x_search_distance: int = 0,
    x_speed: int = 270,
  ):
    """Deprecated: delegates to VantagePIPBackend.search_for_teach_in_signal_in_x_direction."""
    return await self._vantage_pip.search_for_teach_in_signal_in_x_direction(
      channel_index=channel_index,
      x_search_distance=x_search_distance / 10,
      x_speed=x_speed / 10,
    )

  async def position_all_channels_in_y_direction(
    self,
    y_position: List[int],
  ):
    """Deprecated: delegates to VantagePIPBackend.position_all_channels_in_y_direction."""
    if y_position is None:
      y_position = [3000] * self.num_channels
    return await self._vantage_pip.position_all_channels_in_y_direction(
      y_position=[v / 10 for v in y_position],
    )

  async def position_all_channels_in_z_direction(
    self,
    z_position: Optional[List[int]] = None,
  ):
    """Deprecated: delegates to VantagePIPBackend.position_all_channels_in_z_direction."""
    if z_position is None:
      z_position = [0] * self.num_channels
    return await self._vantage_pip.position_all_channels_in_z_direction(
      z_position=[v / 10 for v in z_position],
    )

  async def position_single_channel_in_y_direction(
    self,
    channel_index: int = 1,
    y_position: int = 3000,
  ):
    """Deprecated: delegates to VantagePIPBackend.position_single_channel_in_y_direction."""
    return await self._vantage_pip.position_single_channel_in_y_direction(
      channel_index=channel_index,
      y_position=y_position / 10,
    )

  async def position_single_channel_in_z_direction(
    self,
    channel_index: int = 1,
    z_position: int = 0,
  ):
    """Deprecated: delegates to VantagePIPBackend.position_single_channel_in_z_direction."""
    return await self._vantage_pip.position_single_channel_in_z_direction(
      channel_index=channel_index,
      z_position=z_position / 10,
    )

  async def move_to_defined_position(
    self,
    x_position: List[int],
    y_position: List[int],
    tip_pattern: Optional[List[bool]] = None,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
    z_position: Optional[List[int]] = None,
  ):
    """Deprecated: delegates to VantagePIPBackend.move_to_defined_position."""
    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    if y_position is None:
      y_position = [3000] * self.num_channels
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    if z_position is None:
      z_position = [0] * self.num_channels

    return await self._vantage_pip.move_to_defined_position(
      x_position=x_position,
      y_position=[v / 10 for v in y_position],
      tip_pattern=tip_pattern,
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      z_position=[v / 10 for v in z_position],
    )

  async def teach_rack_using_channel_n(
    self,
    channel_index: int = 1,
    gap_center_x_direction: int = 0,
    gap_center_y_direction: int = 3000,
    gap_center_z_direction: int = 0,
    minimal_height_at_command_end: Optional[List[int]] = None,
  ):
    """Deprecated: delegates to VantagePIPBackend.teach_rack_using_channel_n."""
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels

    return await self._vantage_pip.teach_rack_using_channel_n(
      channel_index=channel_index,
      gap_center_x_direction=gap_center_x_direction / 10,
      gap_center_y_direction=gap_center_y_direction / 10,
      gap_center_z_direction=gap_center_z_direction / 10,
      minimal_height_at_command_end=[v / 10 for v in minimal_height_at_command_end],
    )

  async def expose_channel_n(
    self,
    channel_index: int = 1,
  ):
    """Deprecated: delegates to VantagePIPBackend.expose_channel_n."""
    return await self._vantage_pip.expose_channel_n(
      channel_index=channel_index,
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
    """Deprecated: delegates to VantagePIPBackend.calculates_check_sums_and_compares_them_with_the_value_saved_in_flash_eprom."""
    if tip_type is None:
      tip_type = [4] * self.num_channels
    if TODO_DC_2 is None:
      TODO_DC_2 = [0] * self.num_channels
    if z_deposit_position is None:
      z_deposit_position = [0] * self.num_channels
    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels

    return await self._vantage_pip.calculates_check_sums_and_compares_them_with_the_value_saved_in_flash_eprom(
      TODO_DC_0=TODO_DC_0 / 10,
      TODO_DC_1=TODO_DC_1 / 10,
      tip_type=tip_type,
      TODO_DC_2=[v / 10 for v in TODO_DC_2],
      z_deposit_position=[v / 10 for v in z_deposit_position],
      minimal_traverse_height_at_begin_of_command=[
        v / 10 for v in minimal_traverse_height_at_begin_of_command
      ],
      first_pip_channel_node_no=first_pip_channel_node_no,
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
    """Deprecated: delegates to VantageCoreGripper.discard_tool. Use that instead."""

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels

    return await self._vantage_core_gripper.discard_tool(
      x_position=gripper_tool_x_position / 10,
      first_gripper_tool_y_pos=first_gripper_tool_y_pos / 10,
      first_pip_channel_node_no=first_pip_channel_node_no,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command[0]
      / 10,
      minimal_height_at_command_end=minimal_height_at_command_end[0] / 10,
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
    """Deprecated: delegates to VantageCoreGripper._grip_plate. Use that instead."""

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels

    return await self._vantage_core_gripper._grip_plate(
      x_position=plate_center_x_direction / 10,
      y_position=plate_center_y_direction / 10,
      z_position=plate_center_z_direction / 10,
      z_speed=z_speed / 10,
      open_gripper_position=open_gripper_position / 10,
      plate_width=plate_width / 10,
      acceleration_index=acceleration_index,
      grip_strength=grip_strength,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command[0]
      / 10,
      minimal_height_at_command_end=minimal_height_at_command_end[0] / 10,
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
    """Deprecated: delegates to VantageCoreGripper._put_plate. Use that instead."""

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels

    return await self._vantage_core_gripper._put_plate(
      x_position=plate_center_x_direction / 10,
      y_position=plate_center_y_direction / 10,
      z_position=plate_center_z_direction / 10,
      press_on_distance=press_on_distance / 10,
      z_speed=z_speed / 10,
      open_gripper_position=open_gripper_position / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command[0]
      / 10,
      minimal_height_at_command_end=minimal_height_at_command_end[0] / 10,
    )

  async def move_to_position(
    self,
    plate_center_x_direction: int = 0,
    plate_center_y_direction: int = 3000,
    plate_center_z_direction: int = 0,
    z_speed: int = 1287,
    minimal_traverse_height_at_begin_of_command: Optional[List[int]] = None,
  ):
    """Deprecated: delegates to VantageCoreGripper._move_to_position. Use that instead."""

    if minimal_traverse_height_at_begin_of_command is None:
      minimal_traverse_height_at_begin_of_command = [3600] * self.num_channels

    return await self._vantage_core_gripper._move_to_position(
      x_position=plate_center_x_direction / 10,
      y_position=plate_center_y_direction / 10,
      z_position=plate_center_z_direction / 10,
      z_speed=z_speed / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command[0]
      / 10,
    )

  async def release_object(
    self,
    first_pip_channel_node_no: int = 1,
  ):
    """Deprecated: delegates to VantageCoreGripper.open_gripper. Use that instead."""

    return await self._vantage_core_gripper.open_gripper(0)

  async def set_any_parameter_within_this_module(self):
    """Deprecated: delegates to VantagePIPBackend.set_any_parameter_within_this_module."""
    return await self._vantage_pip.set_any_parameter_within_this_module()

  async def request_y_positions_of_all_channels(self):
    """Deprecated: delegates to VantagePIPBackend.request_y_positions_of_all_channels."""
    return await self._vantage_pip.request_y_positions_of_all_channels()

  async def request_y_position_of_channel_n(self, channel_index: int = 1):
    """Deprecated: delegates to VantagePIPBackend.request_y_position_of_channel_n."""
    return await self._vantage_pip.request_y_position_of_channel_n(
      channel_index=channel_index,
    )

  async def request_z_positions_of_all_channels(self):
    """Deprecated: delegates to VantagePIPBackend.request_z_positions_of_all_channels."""
    return await self._vantage_pip.request_z_positions_of_all_channels()

  async def request_z_position_of_channel_n(self, channel_index: int = 1):
    """Deprecated: delegates to VantagePIPBackend.request_z_position_of_channel_n."""
    return await self._vantage_pip.request_z_position_of_channel_n(
      channel_index=channel_index,
    )

  async def query_tip_presence(self) -> List[bool]:
    """Deprecated: use ``VantageDriver.query_tip_presence``."""
    return await self.driver.query_tip_presence()

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Deprecated: use ``VantageDriver.query_tip_presence``."""
    return list(await self.query_tip_presence())

  async def request_height_of_last_lld(self):
    """Deprecated: delegates to VantagePIPBackend.request_height_of_last_lld."""
    return await self._vantage_pip.request_height_of_last_lld()

  async def request_channel_dispense_on_fly_status(self):
    """Deprecated: delegates to VantagePIPBackend.request_channel_dispense_on_fly_status."""
    return await self._vantage_pip.request_channel_dispense_on_fly_status()

  async def core96_request_initialization_status(self) -> bool:
    """Deprecated: use ``VantageDriver.core96_request_initialization_status``."""
    return await self.driver.core96_request_initialization_status()

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
    """Deprecated: use ``VantageDriver.core96_initialize``.

    Note: this legacy method accepts values in 0.1mm and converts to mm for the new API.
    """
    return await self.driver.core96_initialize(
      x_position / 10,
      y_position / 10,
      z_position / 10,
      minimal_traverse_height_at_begin_of_command / 10,
      minimal_height_at_command_end / 10,
      end_z_deposit_position / 10,
      tip_type,
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
    """Deprecated: use ``VantageHead96Backend._core96_aspiration_of_liquid``."""
    return await self._vantage_head96._core96_aspiration_of_liquid(
      type_of_aspiration=type_of_aspiration,
      x_position=x_position / 10,
      y_position=y_position / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command / 10,
      minimal_height_at_command_end=minimal_height_at_command_end / 10,
      lld_search_height=lld_search_height / 10,
      liquid_surface_at_function_without_lld=liquid_surface_at_function_without_lld / 10,
      pull_out_distance_to_take_transport_air_in_function_without_lld=pull_out_distance_to_take_transport_air_in_function_without_lld
      / 10,
      minimum_height=minimum_height / 10,
      tube_2nd_section_height_measured_from_zm=tube_2nd_section_height_measured_from_zm / 10,
      tube_2nd_section_ratio=tube_2nd_section_ratio / 10,
      immersion_depth=immersion_depth / 10,
      surface_following_distance=surface_following_distance / 10,
      aspiration_volume=aspiration_volume / 100,
      aspiration_speed=aspiration_speed / 10,
      transport_air_volume=transport_air_volume / 10,
      blow_out_air_volume=blow_out_air_volume / 100,
      pre_wetting_volume=pre_wetting_volume / 100,
      lld_mode=lld_mode,
      lld_sensitivity=lld_sensitivity,
      swap_speed=swap_speed / 10,
      settling_time=settling_time / 10,
      mix_volume=mix_volume / 100,
      mix_cycles=mix_cycles,
      mix_position_in_z_direction_from_liquid_surface=mix_position_in_z_direction_from_liquid_surface,
      surface_following_distance_during_mixing=surface_following_distance_during_mixing,
      mix_speed=mix_speed / 10,
      limit_curve_index=limit_curve_index,
      tadm_channel_pattern=tadm_channel_pattern,
      tadm_algorithm_on_off=tadm_algorithm_on_off,
      recording_mode=recording_mode,
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
    """Deprecated: use ``VantageHead96Backend._core96_dispensing_of_liquid``."""
    return await self._vantage_head96._core96_dispensing_of_liquid(
      type_of_dispensing_mode=type_of_dispensing_mode,
      x_position=x_position / 10,
      y_position=y_position / 10,
      minimum_height=minimum_height / 10,
      tube_2nd_section_height_measured_from_zm=tube_2nd_section_height_measured_from_zm / 10,
      tube_2nd_section_ratio=tube_2nd_section_ratio / 10,
      lld_search_height=lld_search_height / 10,
      liquid_surface_at_function_without_lld=liquid_surface_at_function_without_lld / 10,
      pull_out_distance_to_take_transport_air_in_function_without_lld=pull_out_distance_to_take_transport_air_in_function_without_lld
      / 10,
      immersion_depth=immersion_depth / 10,
      surface_following_distance=surface_following_distance / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command / 10,
      minimal_height_at_command_end=minimal_height_at_command_end / 10,
      dispense_volume=dispense_volume / 100,
      dispense_speed=dispense_speed / 10,
      cut_off_speed=cut_off_speed / 10,
      stop_back_volume=stop_back_volume / 100,
      transport_air_volume=transport_air_volume / 10,
      blow_out_air_volume=blow_out_air_volume / 100,
      lld_mode=lld_mode,
      lld_sensitivity=lld_sensitivity,
      side_touch_off_distance=side_touch_off_distance / 10,
      swap_speed=swap_speed / 10,
      settling_time=settling_time / 10,
      mix_volume=mix_volume / 100,
      mix_cycles=mix_cycles,
      mix_position_in_z_direction_from_liquid_surface=mix_position_in_z_direction_from_liquid_surface,
      surface_following_distance_during_mixing=surface_following_distance_during_mixing,
      mix_speed=mix_speed / 10,
      limit_curve_index=limit_curve_index,
      tadm_channel_pattern=tadm_channel_pattern,
      tadm_algorithm_on_off=tadm_algorithm_on_off,
      recording_mode=recording_mode,
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
    """Deprecated: use ``VantageHead96Backend._core96_tip_pick_up``."""
    return await self._vantage_head96._core96_tip_pick_up(
      x_position=x_position / 10,
      y_position=y_position / 10,
      tip_type=tip_type,
      tip_handling_method=tip_handling_method,
      z_deposit_position=z_deposit_position / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command / 10,
      minimal_height_at_command_end=minimal_height_at_command_end / 10,
    )

  async def core96_tip_discard(
    self,
    x_position: int = 5000,
    y_position: int = 5000,
    z_deposit_position: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3900,
    minimal_height_at_command_end: int = 3900,
  ):
    """Deprecated: use ``VantageHead96Backend._core96_tip_discard``."""
    return await self._vantage_head96._core96_tip_discard(
      x_position=x_position / 10,
      y_position=y_position / 10,
      z_deposit_position=z_deposit_position / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command / 10,
      minimal_height_at_command_end=minimal_height_at_command_end / 10,
    )

  async def core96_move_to_defined_position(
    self,
    x_position: int = 5000,
    y_position: int = 5000,
    z_position: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 3900,
  ):
    """Deprecated: use ``VantageHead96Backend._core96_move_to_defined_position``."""
    return await self._vantage_head96._core96_move_to_defined_position(
      x_position=x_position / 10,
      y_position=y_position / 10,
      z_position=z_position / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command / 10,
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
    """Deprecated: use ``VantageHead96Backend._core96_wash_tips``."""
    return await self._vantage_head96._core96_wash_tips(
      x_position=x_position / 10,
      y_position=y_position / 10,
      liquid_surface_at_function_without_lld=liquid_surface_at_function_without_lld / 10,
      minimum_height=minimum_height / 10,
      surface_following_distance_during_mixing=surface_following_distance_during_mixing / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command / 10,
      mix_volume=mix_volume / 10,
      mix_cycles=mix_cycles,
      mix_speed=mix_speed / 10,
    )

  async def core96_empty_washed_tips(
    self,
    liquid_surface_at_function_without_lld: int = 3900,
    minimal_height_at_command_end: int = 3900,
  ):
    """Deprecated: use ``VantageHead96Backend._core96_empty_washed_tips``."""
    return await self._vantage_head96._core96_empty_washed_tips(
      liquid_surface_at_function_without_lld=liquid_surface_at_function_without_lld / 10,
      minimal_height_at_command_end=minimal_height_at_command_end / 10,
    )

  async def core96_search_for_teach_in_signal_in_x_direction(
    self,
    x_search_distance: int = 0,
    x_speed: int = 50,
  ):
    """Deprecated: use ``VantageHead96Backend._core96_search_for_teach_in_signal_in_x_direction``."""
    return await self._vantage_head96._core96_search_for_teach_in_signal_in_x_direction(
      x_search_distance=x_search_distance / 10,
      x_speed=x_speed / 10,
    )

  async def core96_set_any_parameter(self):
    """Deprecated: use ``VantageHead96Backend._core96_set_any_parameter``."""
    return await self._vantage_head96._core96_set_any_parameter()

  async def core96_query_tip_presence(self):
    """Deprecated: use ``VantageHead96Backend._core96_query_tip_presence``."""
    return await self._vantage_head96._core96_query_tip_presence()

  async def core96_request_position(self):
    """Deprecated: use ``VantageHead96Backend._core96_request_position``."""
    return await self._vantage_head96._core96_request_position()

  async def core96_request_tadm_error_status(
    self,
    tadm_channel_pattern: Optional[List[bool]] = None,
  ):
    """Deprecated: use ``VantageHead96Backend._core96_request_tadm_error_status``."""
    return await self._vantage_head96._core96_request_tadm_error_status(
      tadm_channel_pattern=tadm_channel_pattern,
    )

  async def ipg_request_initialization_status(self) -> bool:
    """Deprecated: use ``IPGBackend.request_initialization_status``."""
    return await self._vantage_ipg.request_initialization_status()

  async def ipg_initialize(self):
    """Deprecated: use ``IPGBackend.initialize``."""
    return await self._vantage_ipg.initialize()

  async def ipg_park(self):
    """Deprecated: use ``IPGBackend.park``."""
    return await self._vantage_ipg.park()

  async def ipg_expose_channel_n(self):
    """Deprecated: use ``IPGBackend.expose_channel_n``."""
    return await self._vantage_ipg.expose_channel_n()

  async def ipg_release_object(self):
    """Deprecated: use ``IPGBackend.open_gripper``."""
    return await self._vantage_ipg.open_gripper(0)

  async def ipg_search_for_teach_in_signal_in_x_direction(
    self,
    x_search_distance: int = 0,
    x_speed: int = 50,
  ):
    """Deprecated: use ``IPGBackend.search_for_teach_in_signal_in_x_direction``.

    Note: this legacy method accepts values in 0.1mm and converts to mm for the new API.

    Args:
      x_search_distance: X search distance [0.1mm].
      x_speed: X speed [0.1mm/s].
    """
    return await self._vantage_ipg.search_for_teach_in_signal_in_x_direction(
      x_search_distance=x_search_distance / 10,
      x_speed=x_speed / 10,
    )

  async def ipg_grip_plate(
    self,
    x_position: int = 5000,
    y_position: int = 5600,
    z_position: int = 3600,
    grip_strength: int = 100,
    open_gripper_position: int = 860,
    plate_width: int = 800,
    plate_width_tolerance: int = 20,
    acceleration_index: int = 4,
    z_clearance_height: int = 50,
    hotel_depth: int = 0,
    minimal_height_at_command_end: int = 3600,
  ):
    """Deprecated: use ``IPGBackend.grip_plate``."""
    return await self._vantage_ipg.grip_plate(
      x_position=x_position / 10,
      y_position=y_position / 10,
      z_position=z_position / 10,
      grip_strength=grip_strength,
      open_gripper_position=open_gripper_position / 10,
      plate_width=plate_width / 10,
      plate_width_tolerance=plate_width_tolerance / 10,
      acceleration_index=acceleration_index,
      z_clearance_height=z_clearance_height / 10,
      hotel_depth=hotel_depth / 10,
      minimal_height_at_command_end=minimal_height_at_command_end / 10,
    )

  async def ipg_put_plate(
    self,
    x_position: int = 5000,
    y_position: int = 5600,
    z_position: int = 3600,
    open_gripper_position: int = 860,
    z_clearance_height: int = 50,
    press_on_distance: int = 5,
    hotel_depth: int = 0,
    minimal_height_at_command_end: int = 3600,
  ):
    """Deprecated: use ``IPGBackend.put_plate``."""
    return await self._vantage_ipg.put_plate(
      x_position=x_position / 10,
      y_position=y_position / 10,
      z_position=z_position / 10,
      open_gripper_position=open_gripper_position / 10,
      z_clearance_height=z_clearance_height / 10,
      press_on_distance=press_on_distance / 10,
      hotel_depth=hotel_depth / 10,
      minimal_height_at_command_end=minimal_height_at_command_end / 10,
    )

  async def ipg_prepare_gripper_orientation(
    self,
    grip_orientation: int = 32,
    minimal_traverse_height_at_begin_of_command: int = 3600,
  ):
    """Deprecated: use ``IPGBackend.prepare_gripper_orientation``."""
    return await self._vantage_ipg.prepare_gripper_orientation(
      grip_orientation=grip_orientation,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command / 10,
    )

  async def ipg_move_to_defined_position(
    self,
    x_position: int = 5000,
    y_position: int = 5600,
    z_position: int = 3600,
    minimal_traverse_height_at_begin_of_command: int = 3600,
  ):
    """Deprecated: use ``IPGBackend.move_to_defined_position``."""
    return await self._vantage_ipg.move_to_defined_position(
      x_position=x_position / 10,
      y_position=y_position / 10,
      z_position=z_position / 10,
      minimal_traverse_height_at_begin_of_command=minimal_traverse_height_at_begin_of_command / 10,
    )

  async def ipg_set_any_parameter_within_this_module(self):
    """Deprecated: use ``IPGBackend.set_any_parameter_within_this_module``."""
    return await self._vantage_ipg.set_any_parameter_within_this_module()

  async def ipg_get_parking_status(self) -> bool:
    """Deprecated: use ``IPGBackend.get_parking_status``."""
    return await self._vantage_ipg.get_parking_status()

  async def ipg_query_tip_presence(self):
    """Deprecated: use ``IPGBackend.query_tip_presence``."""
    return await self._vantage_ipg.query_tip_presence()

  async def ipg_request_access_range(self, grip_orientation: int = 32):
    """Deprecated: use ``IPGBackend.request_access_range``.

    Args:
      grip_orientation: Grip orientation (1-44).
    """
    return await self._vantage_ipg.request_access_range(
      grip_orientation=grip_orientation,
    )

  async def ipg_request_position(self, grip_orientation: int = 32):
    """Deprecated: use ``IPGBackend.request_position``.

    Args:
      grip_orientation: Grip orientation (1-44).
    """
    return await self._vantage_ipg.request_position(
      grip_orientation=grip_orientation,
    )

  async def ipg_request_actual_angular_dimensions(self):
    """Deprecated: use ``IPGBackend.request_actual_angular_dimensions``."""
    return await self._vantage_ipg.request_actual_angular_dimensions()

  async def ipg_request_configuration(self):
    """Deprecated: use ``IPGBackend.request_configuration``."""
    return await self._vantage_ipg.request_configuration()

  async def x_arm_initialize(self):
    """Deprecated: use ``VantageXArm.initialize``."""
    return await self._vantage_x_arm.initialize()

  async def x_arm_move_to_x_position(
    self,
    x_position: int = 5000,
    x_speed: int = 25000,
    TODO_XI_1: int = 1,
  ):
    """Deprecated: use ``VantageXArm.move_to``.

    Note: this legacy method accepts values in 0.1mm and converts to mm for the new API.
    """
    return await self._vantage_x_arm.move_to(
      x_position=x_position / 10, x_speed=x_speed / 10
    )

  async def x_arm_move_to_x_position_with_all_attached_components_in_z_safety_position(
    self,
    x_position: int = 5000,
    x_speed: int = 25000,
    TODO_XA_1: int = 1,
  ):
    """Deprecated: use ``VantageXArm.move_to_safe``.

    Note: this legacy method accepts values in 0.1mm and converts to mm for the new API.
    """
    return await self._vantage_x_arm.move_to_safe(
      x_position=x_position / 10, x_speed=x_speed / 10, xx=TODO_XA_1
    )

  async def x_arm_move_arm_relatively_in_x(
    self,
    x_search_distance: int = 0,
    x_speed: int = 25000,
    TODO_XS_1: int = 1,
  ):
    """Deprecated: use ``VantageXArm.move_relatively``.

    Note: this legacy method accepts values in 0.1mm and converts to mm for the new API.
    """
    return await self._vantage_x_arm.move_relatively(
      x_search_distance=x_search_distance / 10, x_speed=x_speed / 10, xx=TODO_XS_1
    )

  async def x_arm_search_x_for_teach_signal(
    self,
    x_search_distance: int = 0,
    x_speed: int = 25000,
    TODO_XT_1: int = 1,
  ):
    """Deprecated: use ``VantageXArm.search_teach_signal``.

    Note: this legacy method accepts values in 0.1mm and converts to mm for the new API.
    """
    return await self._vantage_x_arm.search_teach_signal(
      x_search_distance=x_search_distance / 10, x_speed=x_speed / 10, xx=TODO_XT_1
    )

  async def x_arm_set_x_drive_angle_of_alignment(
    self,
    TODO_XL_1: int = 1,
  ):
    """Deprecated: use ``VantageXArm.set_x_drive_angle_of_alignment``."""
    return await self._vantage_x_arm.set_x_drive_angle_of_alignment(xl=TODO_XL_1)

  async def x_arm_turn_x_drive_off(self):
    """Deprecated: use ``VantageXArm.turn_off``."""
    return await self._vantage_x_arm.turn_off()

  async def x_arm_send_message_to_motion_controller(
    self,
    TODO_BD_1: str = "",
  ):
    """Deprecated: use ``VantageXArm.send_message_to_motion_controller``."""
    return await self._vantage_x_arm.send_message_to_motion_controller(bd=TODO_BD_1)

  async def x_arm_set_any_parameter_within_this_module(
    self,
    TODO_AA_1: int = 0,
    TODO_AA_2: int = 1,
  ):
    """Deprecated: use ``VantageXArm.set_any_parameter_within_this_module``."""
    return await self._vantage_x_arm.set_any_parameter_within_this_module(
      xm=TODO_AA_1, xt=TODO_AA_2
    )

  async def x_arm_request_arm_x_position(self):
    """Deprecated: use ``VantageXArm.request_position``."""
    return await self._vantage_x_arm.request_position()

  async def x_arm_request_error_code(self):
    """Deprecated: use ``VantageXArm.request_error_code``."""
    return await self._vantage_x_arm.request_error_code()

  async def x_arm_request_x_drive_recorded_data(
    self,
    TODO_QL_1: int = 0,
    TODO_QL_2: int = 0,
  ):
    """Deprecated: use ``VantageXArm.request_x_drive_recorded_data``."""
    return await self._vantage_x_arm.request_x_drive_recorded_data(lj=TODO_QL_1, ln=TODO_QL_2)

  async def disco_mode(self):
    """Deprecated: use ``VantageDriver.disco_mode``."""
    await self.driver.disco_mode()

  async def russian_roulette(self):
    """Deprecated: use ``VantageDriver.russian_roulette``."""
    await self.driver.russian_roulette()


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class Vantage(VantageBackend):
  def __init__(self, *args, **kwargs):
    warnings.warn(
      "`Vantage` is deprecated and will be removed in a future release. "
      "Please use `VantageBackend` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    super().__init__(*args, **kwargs)
