"""Legacy. Use pylabrobot.brooks instead."""

import warnings
from abc import ABC
from typing import Dict, List, Optional, Union

from pylabrobot.brooks import precise_flex as _new_module
from pylabrobot.io.socket import Socket
from pylabrobot.legacy.arms.backend import (
  AccessPattern,
  HorizontalAccess,
  SCARABackend,
  VerticalAccess,
)
from pylabrobot.legacy.arms.precise_flex.coords import ElbowOrientation, PreciseFlexCartesianCoords
from pylabrobot.resources import Coordinate, Rotation


PreciseFlexError = _new_module.PreciseFlexError


def _to_new_coords(
  position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
) -> Union[_new_module.PreciseFlexCartesianCoords, Dict[int, float]]:
  """Convert legacy CartesianCoords to new module's CartesianCoords."""
  if isinstance(position, PreciseFlexCartesianCoords):
    orientation = None
    if position.orientation is not None:
      orientation = _new_module.ElbowOrientation(position.orientation.value)
    return _new_module.PreciseFlexCartesianCoords(
      location=position.location,
      rotation=position.rotation,
      orientation=orientation,
    )
  return position


def _to_new_access(access: Optional[AccessPattern]) -> Optional[_new_module.AccessPattern]:
  """Convert legacy AccessPattern to new module's AccessPattern."""
  if access is None:
    return None
  if isinstance(access, VerticalAccess):
    return _new_module.VerticalAccess(
      approach_height_mm=access.approach_height_mm,
      clearance_mm=access.clearance_mm,
      gripper_offset_mm=access.gripper_offset_mm,
    )
  if isinstance(access, HorizontalAccess):
    return _new_module.HorizontalAccess(
      approach_distance_mm=access.approach_distance_mm,
      clearance_mm=access.clearance_mm,
      lift_height_mm=access.lift_height_mm,
      gripper_offset_mm=access.gripper_offset_mm,
    )
  return None


def _from_new_coords(
  position: _new_module.PreciseFlexCartesianCoords,
) -> PreciseFlexCartesianCoords:
  """Convert new module's CartesianCoords to legacy CartesianCoords."""
  orientation = None
  if position.orientation is not None:
    orientation = ElbowOrientation(position.orientation.value)
  return PreciseFlexCartesianCoords(
    location=position.location,
    rotation=position.rotation,
    orientation=orientation,
  )


class PreciseFlexBackend(SCARABackend, ABC):
  """Legacy. Use pylabrobot.brooks.PreciseFlexBackend instead."""

  def __init__(
    self,
    host: str,
    port: int = 10100,
    is_dual_gripper: bool = False,
    has_rail: bool = False,
    timeout=20,
  ) -> None:
    super().__init__()
    self._new: _new_module.PreciseFlexBackend  # set by subclasses
    # Keep these for any legacy code that accesses them directly
    self.io = Socket(human_readable_device_name="Precise Flex Arm", host=host, port=port)
    self.profile_index: int = 1
    self.location_index: int = 1
    self.horizontal_compliance: bool = False
    self.horizontal_compliance_torque: int = 0
    self.timeout = timeout
    self._has_rail = has_rail
    self._is_dual_gripper = is_dual_gripper
    if is_dual_gripper:
      warnings.warn(
        "Dual gripper support is experimental and may not work as expected.", UserWarning
      )

  def _convert_to_cartesian_space(
    self, position: tuple[float, float, float, float, float, float, Optional[ElbowOrientation]]
  ) -> PreciseFlexCartesianCoords:
    if len(position) != 7:
      raise ValueError(
        "Position must be a tuple of 7 values (x, y, z, yaw, pitch, roll, orientation)."
      )
    orientation = ElbowOrientation(position[6])
    return PreciseFlexCartesianCoords(
      location=Coordinate(position[0], position[1], position[2]),
      rotation=Rotation(position[5], position[4], position[3]),
      orientation=orientation,
    )

  def _convert_to_cartesian_array(
    self, position: PreciseFlexCartesianCoords
  ) -> tuple[float, float, float, float, float, float, int]:
    orientation_int = self._convert_orientation_enum_to_int(position.orientation)
    arr = (
      position.location.x,
      position.location.y,
      position.location.z,
      position.rotation.yaw,
      position.rotation.pitch,
      position.rotation.roll,
      orientation_int,
    )
    return arr

  async def setup(self, skip_home: bool = False):
    await self._new.setup(skip_home=skip_home)

  async def stop(self):
    await self._new.stop()

  async def set_speed(self, speed_percent: float):
    await self._new._set_speed(speed_percent)

  async def get_speed(self) -> float:
    return await self._new._get_speed()

  async def open_gripper(self, gripper_width: float):
    await self._new.open_gripper(gripper_width)

  async def close_gripper(self, gripper_width: float):
    await self._new.close_gripper(gripper_width)

  async def halt(self):
    await self._new.halt()

  async def home(self) -> None:
    await self._new.home()

  async def move_to_safe(self) -> None:
    await self._new.move_to_safe()

  def _convert_orientation_int_to_enum(self, orientation_int: int) -> Optional[ElbowOrientation]:
    if orientation_int == 1:
      return ElbowOrientation.RIGHT
    if orientation_int == 2:
      return ElbowOrientation.LEFT
    return None

  def _convert_orientation_enum_to_int(self, orientation: Optional[ElbowOrientation]) -> int:
    if orientation == ElbowOrientation.LEFT:
      return 2
    if orientation == ElbowOrientation.RIGHT:
      return 1
    return 0

  async def home_all(self) -> None:
    await self._new.home_all()

  async def attach(self, attach_state: Optional[int] = None) -> int:
    return await self._new.attach(attach_state)

  async def detach(self):
    await self._new.detach()

  async def power_on_robot(self):
    await self._new.power_on_robot()

  async def power_off_robot(self):
    await self._new.power_off_robot()

  async def approach(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
  ):
    await self._new.approach(_to_new_coords(position), _to_new_access(access))

  async def pick_up_resource(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    plate_width: float,
    access: Optional[AccessPattern] = None,
    finger_speed_percent: float = 50.0,
    grasp_force: float = 10.0,
  ):
    converted = _to_new_coords(position)
    params = _new_module.PreciseFlexBackend.PickUpParams(
      access=_to_new_access(access),
      finger_speed_percent=finger_speed_percent,
      grasp_force=grasp_force,
    )
    if isinstance(converted, dict):
      await self._new.pick_up_at_joint_position(converted, plate_width, backend_params=params)
    else:
      await self._new.pick_up_at_location(
        converted.location, converted.rotation.z, plate_width, backend_params=params
      )

  async def drop_resource(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
  ):
    converted = _to_new_coords(position)
    params = _new_module.PreciseFlexBackend.DropParams(access=_to_new_access(access))
    if isinstance(converted, dict):
      await self._new.drop_at_joint_position(converted, resource_width=0, backend_params=params)
    else:
      await self._new.drop_at_location(
        converted.location, converted.rotation.z, resource_width=0, backend_params=params
      )

  async def move_to(self, position: Union[PreciseFlexCartesianCoords, Dict[int, float]]):
    converted = _to_new_coords(position)
    if isinstance(converted, dict):
      await self._new.move_to_joint_position(converted)
    else:
      await self._new.move_to_location(converted.location, converted.rotation)

  async def get_joint_position(self) -> Dict[int, float]:
    return await self._new.get_joint_position()

  async def get_cartesian_position(self) -> PreciseFlexCartesianCoords:
    result = await self._new.get_cartesian_position()
    return _from_new_coords(result)

  async def send_command(self, command: str) -> str:
    return await self._new.send_command(command)

  def _parse_reply_ensure_successful(self, reply: bytes) -> str:
    return self._new._parse_reply_ensure_successful(reply)

  async def is_gripper_closed(self) -> bool:
    return await self._new.is_gripper_closed()

  async def are_grippers_closed(self) -> tuple[bool, bool]:
    return await self._new.are_grippers_closed()

  async def freedrive_mode(self, free_axes: List[int]) -> None:
    await self._new.freedrive_mode(free_axes)

  async def end_freedrive_mode(self) -> None:
    await self._new.end_freedrive_mode()

  async def set_base(
    self, x_offset: float, y_offset: float, z_offset: float, z_rotation: float
  ) -> None:
    await self._new.set_base(x_offset, y_offset, z_offset, z_rotation)

  async def get_base(self) -> tuple[float, float, float, float]:
    return await self._new.get_base()

  async def exit(self) -> None:
    await self._new.exit()

  async def get_power_state(self) -> int:
    return await self._new.get_power_state()

  async def set_power(self, enable: bool, timeout: int = 0) -> None:
    await self._new.set_power(enable, timeout)

  async def get_mode(self):
    return await self._new.get_mode()

  async def set_response_mode(self, mode) -> None:
    await self._new.set_response_mode(mode)

  async def get_monitor_speed(self) -> int:
    return await self._new.get_monitor_speed()

  async def set_monitor_speed(self, speed_percent: int) -> None:
    await self._new.set_monitor_speed(speed_percent)

  async def nop(self) -> None:
    await self._new.nop()

  async def get_payload(self) -> int:
    return await self._new.get_payload()

  async def set_payload(self, payload_percent: int) -> None:
    await self._new.set_payload(payload_percent)

  async def set_parameter(self, data_id, value, unit_number=None, sub_unit=None, array_index=None):
    await self._new.set_parameter(data_id, value, unit_number, sub_unit, array_index)

  async def get_parameter(self, data_id, unit_number=None, sub_unit=None, array_index=None):
    return await self._new.get_parameter(data_id, unit_number, sub_unit, array_index)

  async def reset(self, robot_number: int) -> None:
    await self._new.reset(robot_number)

  async def get_selected_robot(self) -> int:
    return await self._new.get_selected_robot()

  async def select_robot(self, robot_number: int) -> None:
    await self._new.select_robot(robot_number)

  async def get_signal(self, signal_number: int) -> int:
    return await self._new.get_signal(signal_number)

  async def set_signal(self, signal_number: int, value: int) -> None:
    await self._new.set_signal(signal_number, value)

  async def get_system_state(self) -> int:
    return await self._new.get_system_state()

  async def get_tool_transformation_values(self):
    return await self._new.get_tool_transformation_values()

  async def set_tool_transformation_values(self, x, y, z, yaw, pitch, roll):
    await self._new.set_tool_transformation_values(x, y, z, yaw, pitch, roll)

  async def get_version(self) -> str:
    return await self._new.get_version()

  async def get_location_angles(self, location_index):
    return await self._new.get_location_angles(location_index)

  async def set_joint_angles(self, location_index, joint_position):
    await self._new.set_joint_angles(location_index, joint_position)

  async def get_location_xyz(self, location_index):
    return await self._new.get_location_xyz(location_index)

  async def set_location_xyz(self, location_index, cartesian_position):
    await self._new.set_location_xyz(location_index, _to_new_coords(cartesian_position))

  async def get_location_z_clearance(self, location_index):
    return await self._new.get_location_z_clearance(location_index)

  async def set_location_z_clearance(self, location_index, z_clearance, z_world=None):
    await self._new.set_location_z_clearance(location_index, z_clearance, z_world)

  async def get_location_config(self, location_index):
    return await self._new.get_location_config(location_index)

  async def set_location_config(self, location_index, config_value):
    await self._new.set_location_config(location_index, config_value)

  async def dest_c(self, arg1=0):
    return await self._new.dest_c(arg1)

  async def dest_j(self, arg1=0):
    return await self._new.dest_j(arg1)

  async def here_j(self, location_index):
    await self._new.here_j(location_index)

  async def here_c(self, location_index):
    await self._new.here_c(location_index)

  async def get_profile_speed(self, profile_index):
    return await self._new.get_profile_speed(profile_index)

  async def set_profile_speed(self, profile_index, speed_percent):
    await self._new.set_profile_speed(profile_index, speed_percent)

  async def get_profile_speed2(self, profile_index):
    return await self._new.get_profile_speed2(profile_index)

  async def set_profile_speed2(self, profile_index, speed2_percent):
    await self._new.set_profile_speed2(profile_index, speed2_percent)

  async def get_profile_accel(self, profile_index):
    return await self._new.get_profile_accel(profile_index)

  async def set_profile_accel(self, profile_index, accel_percent):
    await self._new.set_profile_accel(profile_index, accel_percent)

  async def get_profile_accel_ramp(self, profile_index):
    return await self._new.get_profile_accel_ramp(profile_index)

  async def set_profile_accel_ramp(self, profile_index, accel_ramp_seconds):
    await self._new.set_profile_accel_ramp(profile_index, accel_ramp_seconds)

  async def get_profile_decel(self, profile_index):
    return await self._new.get_profile_decel(profile_index)

  async def set_profile_decel(self, profile_index, decel_percent):
    await self._new.set_profile_decel(profile_index, decel_percent)

  async def get_profile_decel_ramp(self, profile_index):
    return await self._new.get_profile_decel_ramp(profile_index)

  async def set_profile_decel_ramp(self, profile_index, decel_ramp_seconds):
    await self._new.set_profile_decel_ramp(profile_index, decel_ramp_seconds)

  async def get_profile_in_range(self, profile_index):
    return await self._new.get_profile_in_range(profile_index)

  async def set_profile_in_range(self, profile_index, in_range_value):
    await self._new.set_profile_in_range(profile_index, in_range_value)

  async def get_profile_straight(self, profile_index):
    return await self._new.get_profile_straight(profile_index)

  async def set_profile_straight(self, profile_index, straight_mode):
    await self._new.set_profile_straight(profile_index, straight_mode)

  async def set_motion_profile_values(
    self,
    profile,
    speed,
    speed2,
    acceleration,
    deceleration,
    acceleration_ramp,
    deceleration_ramp,
    in_range,
    straight,
  ):
    await self._new.set_motion_profile_values(
      profile,
      speed,
      speed2,
      acceleration,
      deceleration,
      acceleration_ramp,
      deceleration_ramp,
      in_range,
      straight,
    )

  async def get_motion_profile_values(self, profile):
    return await self._new.get_motion_profile_values(profile)

  async def move_to_stored_location(self, location_index, profile_index):
    await self._new.move_to_stored_location(location_index, profile_index)

  async def move_to_stored_location_appro(self, location_index, profile_index):
    await self._new.move_to_stored_location_appro(location_index, profile_index)

  async def move_extra_axis(self, axis1_position, axis2_position=None):
    await self._new.move_extra_axis(axis1_position, axis2_position)

  async def move_one_axis(self, axis_number, destination_position, profile_index):
    await self._new.move_one_axis(axis_number, destination_position, profile_index)

  async def move_c(self, profile_index, cartesian_coords):
    await self._new.move_c(profile_index, _to_new_coords(cartesian_coords))

  async def move_j(self, profile_index, joint_coords):
    await self._new.move_j(profile_index, joint_coords)

  async def release_brake(self, axis):
    await self._new.release_brake(axis)

  async def set_brake(self, axis):
    await self._new.set_brake(axis)

  async def state(self):
    return await self._new.state()

  async def wait_for_eom(self):
    await self._new.wait_for_eom()

  async def zero_torque(self, enable, axis_mask=1):
    await self._new.zero_torque(enable, axis_mask)

  async def change_config(self, grip_mode=0):
    await self._new.change_config(grip_mode)

  async def change_config2(self, grip_mode=0):
    await self._new.change_config2(grip_mode)

  async def get_grasp_data(self):
    return await self._new.get_grasp_data()

  async def set_grasp_data(self, plate_width, finger_speed_percent, grasp_force):
    await self._new.set_grasp_data(plate_width, finger_speed_percent, grasp_force)

  async def _get_grip_close_pos(self):
    return await self._new._get_grip_close_pos()

  async def _set_grip_close_pos(self, close_position):
    await self._new._set_grip_close_pos(close_position)

  async def _get_grip_open_pos(self):
    return await self._new._get_grip_open_pos()

  async def _set_grip_open_pos(self, open_position):
    await self._new._set_grip_open_pos(open_position)

  async def move_rail(self, station_id=None, mode=0, rail_destination=None):
    await self._new.move_rail(station_id, mode, rail_destination)

  async def get_pallet_index(self, station_id):
    return await self._new.get_pallet_index(station_id)

  async def set_pallet_index(
    self, station_id, pallet_index_x=0, pallet_index_y=0, pallet_index_z=0
  ):
    await self._new.set_pallet_index(station_id, pallet_index_x, pallet_index_y, pallet_index_z)

  async def get_pallet_origin(self, station_id):
    return await self._new.get_pallet_origin(station_id)

  async def set_pallet_origin(self, station_id, cartesian_coords):
    await self._new.set_pallet_origin(station_id, _to_new_coords(cartesian_coords))

  async def get_pallet_x(self, station_id):
    return await self._new.get_pallet_x(station_id)

  async def set_pallet_x(self, station_id, x_position_count, world_x, world_y, world_z):
    await self._new.set_pallet_x(station_id, x_position_count, world_x, world_y, world_z)

  async def get_pallet_y(self, station_id):
    return await self._new.get_pallet_y(station_id)

  async def set_pallet_y(self, station_id, y_position_count, world_x, world_y, world_z):
    await self._new.set_pallet_y(station_id, y_position_count, world_x, world_y, world_z)

  async def get_pallet_z(self, station_id):
    return await self._new.get_pallet_z(station_id)

  async def set_pallet_z(self, station_id, z_position_count, world_x, world_y, world_z):
    await self._new.set_pallet_z(station_id, z_position_count, world_x, world_y, world_z)

  async def pick_plate_station(
    self, station_id, horizontal_compliance=False, horizontal_compliance_torque=0
  ):
    return await self._new.pick_plate_station(
      station_id, horizontal_compliance, horizontal_compliance_torque
    )

  async def place_plate_station(
    self, station_id, horizontal_compliance=False, horizontal_compliance_torque=0
  ):
    await self._new.place_plate_station(
      station_id, horizontal_compliance, horizontal_compliance_torque
    )

  async def get_rail_position(self, station_id):
    return await self._new.get_rail_position(station_id)

  async def set_rail_position(self, station_id, rail_position):
    await self._new.set_rail_position(station_id, rail_position)

  async def teach_plate_station(self, station_id, z_clearance=50.0):
    await self._new.teach_plate_station(station_id, z_clearance)

  async def get_station_type(self, station_id):
    return await self._new.get_station_type(station_id)

  async def set_station_type(
    self, station_id, access_type, location_type, z_clearance, z_above, z_grasp_offset
  ):
    await self._new.set_station_type(
      station_id, access_type, location_type, z_clearance, z_above, z_grasp_offset
    )

  async def home_all_if_no_plate(self):
    return await self._new.home_all_if_no_plate()

  async def _grasp_plate(self, plate_width_mm, finger_speed_percent, grasp_force):
    return await self._new._grasp_plate(plate_width_mm, finger_speed_percent, grasp_force)

  async def _release_plate(self, open_width_mm, finger_speed_percent, in_range=0.0):
    await self._new._release_plate(open_width_mm, finger_speed_percent, in_range)

  async def set_active_gripper(self, gripper_id, spin_mode=0, profile_index=None):
    await self._new.set_active_gripper(gripper_id, spin_mode, profile_index)

  async def get_active_gripper(self):
    return await self._new.get_active_gripper()

  async def pick_plate_from_stored_position(
    self, position_id, horizontal_compliance=False, horizontal_compliance_torque=0
  ):
    await self._new.pick_plate_from_stored_position(
      position_id, horizontal_compliance, horizontal_compliance_torque
    )

  async def place_plate_to_stored_position(
    self, position_id, horizontal_compliance=False, horizontal_compliance_torque=0
  ):
    await self._new.place_plate_to_stored_position(
      position_id, horizontal_compliance, horizontal_compliance_torque
    )

  async def teach_position(self, position_id, z_clearance=50.0):
    await self._new.teach_position(position_id, z_clearance)

  def _parse_xyz_response(self, parts):
    return self._new._parse_xyz_response(parts)

  def _parse_angles_response(self, parts):
    return self._new._parse_angles_response(parts)
