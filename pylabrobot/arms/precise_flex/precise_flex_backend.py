from pylabrobot.arms.backend import ArmBackend, ElbowOrientation
from pylabrobot.arms.precise_flex.precise_flex_backend_api import PreciseFlexBackendApi


class PreciseFlexBackend(ArmBackend):
  """UNTESTED - Backend for the PreciseFlex robotic arm"""
  def __init__(self, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__()
    self.api = PreciseFlexBackendApi(host=host, port=port, timeout=timeout)
    self.profile_index: int = 1
    self.location_index: int = 1
    self.horizontal_compliance: bool = False
    self.horizontal_compliance_torque: int = 0

  async def setup(self):
    """Initialize the PreciseFlex backend."""
    await self.api.setup()
    await self.set_pc_mode()
    await self.power_on_robot()
    await self.attach()

  async def stop(self):
    """Stop the PreciseFlex backend."""
    await self.detach()
    await self.power_off_robot()
    await self.exit()
    await self.api.stop()

  async def set_speed(self, speed_percent: float):
      """Set the speed percentage of the arm's movement (0-100)."""
      await self.api.set_profile_speed(self.profile_index, speed_percent)

  async def get_speed(self) -> float:
      """Get the current speed percentage of the arm's movement."""
      return await self.api.get_profile_speed(self.profile_index)

  async def open_gripper(self):
    """Open the gripper."""
    await self.api.open_gripper()

  async def close_gripper(self):
    """Close the gripper."""
    await self.api.close_gripper()

  async def is_gripper_closed(self) -> bool:
    """Check if the gripper is currently closed."""
    ret_int = await self.api.is_fully_closed()
    if ret_int == -1:
        return True
    else:
        return False

  async def halt(self):
      """Stop any ongoing movement of the arm."""
      await self.api.halt()

  async def home(self):
    """Homes robot."""
    await self.api.home()

  async def move_to_safe(self):
    """Move the arm to a predefined safe position."""
    await self.api.move_to_safe()

  async def approach_j(self, joint_position: tuple[float, float, float, float, float, float, float], approach_height: float):
    """Move the arm to a position above the specified coordinates by a certain distance."""
    await self.api.set_location_angles(self.location_index, *list(joint_position))
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.move_appro(self.location_index, self.profile_index)

  async def pick_plate_j(self, joint_position: tuple[float, float, float, float, float, float, float], approach_height: float):
    """Pick a plate from the specified position."""
    await self.api.set_location_angles(self.location_index, *list(joint_position))
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.pick_plate(self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque)

  async def place_plate_j(self, joint_position: tuple[float, float, float, float, float, float, float], approach_height: float):
    """Place a plate at the specified position."""
    await self.api.set_location_angles(self.location_index, *list(joint_position))
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.place_plate(self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque)

  async def move_to_j(self, joint_position: tuple[float, float, float, float, float, float, float]):
    """Move the arm to a specified position in 3D space."""
    await self.api.move_j(self.location_index, *list(joint_position))

  async def get_position_j(self) -> tuple[float, float, float, float, float, float, float]:
    """Get the current position of the arm in 3D space."""
    return await self.api.where_j()

  async def approach_c(self, cartesian_position: tuple[float, float, float, float, float, float], approach_height: float, orientation: ElbowOrientation | None = None):
    """Move the arm to a position above the specified coordinates by a certain distance."""
    await self.api.set_location_xyz(self.location_index, *list(cartesian_position))
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    orientation_int = self._convert_orientation_enum_to_int(orientation)
    await self.api.set_location_config(self.location_index, orientation_int)
    await self.api.move_appro(self.location_index, self.profile_index)

  async def pick_plate_c(self, cartesian_position: tuple[float, float, float, float, float, float], approach_height: float, orientation: ElbowOrientation | None = None):
    """Pick a plate from the specified position."""
    await self.api.set_location_xyz(self.location_index, *list(cartesian_position))
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    orientation_int = self._convert_orientation_enum_to_int(orientation)
    await self.api.set_location_config(self.location_index, orientation_int)
    await self.api.pick_plate(self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque)

  async def place_plate_c(self, cartesian_position: tuple[float, float, float, float, float, float], approach_height: float, orientation: ElbowOrientation | None = None):
      """Place a plate at the specified position."""
      await self.api.set_location_xyz(self.location_index, *list(cartesian_position))
      await self.api.set_location_z_clearance(self.location_index, approach_height)
      orientation_int = self._convert_orientation_enum_to_int(orientation)
      await self.api.set_location_config(self.location_index, orientation_int)
      await self.api.place_plate(self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque)

  async def move_to_c(self, cartesian_position: tuple[float, float, float, float, float, float], orientation: ElbowOrientation | None = None):
      """Move the arm to a specified position in 3D space."""
      await self.api.move_c(self.profile_index, *list(cartesian_position), config=self._convert_orientation_enum_to_int(orientation))

  async def get_position_c(self) -> tuple[float, float, float, float, float, float, ElbowOrientation | None]:
    """Get the current position of the arm in 3D space."""
    position = await self.api.where_c()
    return (*position[:6], self._convert_orientation_int_to_enum(position[6]))


  def _convert_orientation_int_to_enum(self, orientation_int: int) -> ElbowOrientation | None:
    match orientation_int:
      case 1:
        return ElbowOrientation.LEFT
      case 2:
        return ElbowOrientation.RIGHT
      case _:
        return None

  def _convert_orientation_enum_to_int(self, orientation: ElbowOrientation | None) -> int:
    match orientation:
      case ElbowOrientation.LEFT:
        return 1
      case ElbowOrientation.RIGHT:
        return 2
      case _:
        return 0

  async def home_all(self):
    """Homes all robots."""
    await self.api.home_all()

  async def attach(self):
    """Attach the robot."""
    await self.api.attach(1)

  async def detach(self):
    """Detach the robot."""
    await self.api.attach(0)

  async def power_on_robot(self):
    """Power on the robot."""
    await self.api.set_power(True, self.api.timeout)

  async def power_off_robot(self):
    """Power off the robot."""
    await self.api.set_power(False)

  async def set_pc_mode(self):
    """Set the controller to PC mode."""
    await self.api.set_mode(0)

  async def select_robot(self, robot_id: int) -> None:
    """Select the specified robot."""
    await self.api.select_robot(robot_id)

  async def version(self) -> str:
    """Get the robot's version."""
    return await self.api.get_version()

  async def exit(self):
    """Exit the PreciseFlex backend."""
    await self.api.exit()
