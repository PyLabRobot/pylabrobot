from abc import ABC, abstractmethod
from typing import Optional, Union

from pylabrobot.arms.backend import ArmBackend
from pylabrobot.arms.coords import CartesianCoords, ElbowOrientation, JointCoords
from pylabrobot.arms.precise_flex.precise_flex_api import PreciseFlexBackendApi


class CoordsConverter(ABC):
  @abstractmethod
  def convert_to_joint_space(
    self, position: tuple[float, float, float, float, float, float]
  ) -> JointCoords:
    """Convert a tuple of joint angles to a JointSpace object."""
    ...

  @abstractmethod
  def convert_to_cartesian_space(
    self, position: tuple[float, float, float, float, float, float, Optional[ElbowOrientation]]
  ) -> CartesianCoords:
    ...

  @abstractmethod
  def convert_to_joints_array(
    self, position: JointCoords
  ) -> tuple[float, float, float, float, float, float]:
    ...

  @abstractmethod
  def convert_to_cartesian_array(
    self, position: CartesianCoords
  ) -> tuple[float, float, float, float, float, float, int]:
    """Convert a CartesianSpace object to a list of cartesian coordinates."""
    ...

  @abstractmethod
  def _convert_orientation_enum_to_int(self, orientation: Optional[ElbowOrientation]) -> int:
    """Convert an ElbowOrientation enum to an integer."""
    ...


class PreciseFlex400SpaceConverter(CoordsConverter):
  def convert_to_joint_space(
    self, position: tuple[float, float, float, float, float, float]
  ) -> JointCoords:
    """Convert a tuple of joint angles to a JointCoords object."""
    if len(position) != 6:
      raise ValueError("Position must be a tuple of 6 joint angles.")
    return JointCoords(0, position[0], position[1], position[2], position[3], 0)

  def convert_to_cartesian_space(
    self, position: tuple[float, float, float, float, float, float, Optional[ElbowOrientation]]
  ) -> CartesianCoords:
    """Convert a tuple of cartesian coordinates to a CartesianCoords object."""
    if len(position) != 7:
      raise ValueError(
        "Position must be a tuple of 7 values (x, y, z, yaw, pitch, roll, orientation)."
      )
    orientation = ElbowOrientation(position[6])
    return CartesianCoords(
      position[0], position[1], position[2], position[3], position[4], position[5], orientation
    )

  def convert_to_joints_array(
    self, position: JointCoords
  ) -> tuple[float, float, float, float, float, float]:
    """Convert a JointSpace object to a list of joint angles."""
    joints = (
      position.base,
      position.shoulder,
      position.elbow,
      position.wrist,
      0,
      0,
    )  # PF400 has 4 joints, last two are fixed
    return joints

  def convert_to_cartesian_array(
    self, position: CartesianCoords
  ) -> tuple[float, float, float, float, float, float, int]:
    """Convert a CartesianSpace object to a list of cartesian coordinates."""
    orientation_int = self._convert_orientation_enum_to_int(position.orientation)
    arr = (
      position.x,
      position.y,
      position.z,
      position.yaw,
      position.pitch,
      position.roll,
      orientation_int,
    )
    return arr

  def _convert_orientation_enum_to_int(self, orientation: Optional[ElbowOrientation]) -> int:
    """Convert an ElbowOrientation enum to an integer."""
    if orientation is None:
      return 0
    elif orientation == ElbowOrientation.LEFT:
      return 1
    elif orientation == ElbowOrientation.RIGHT:
      return 2
    else:
      raise ValueError("Invalid ElbowOrientation value.")


class PreciseFlex3400SpaceConverter(CoordsConverter):
  def convert_to_joint_space(
    self, position: tuple[float, float, float, float, float, float]
  ) -> JointCoords:
    """Convert a tuple of joint angles to a JointCoords object."""
    if len(position) != 6:
      raise ValueError("Position must be a tuple of 6 joint angles.")
    return JointCoords(0, position[0], position[1], position[2], position[3], position[4])

  def convert_to_cartesian_space(
    self, position: tuple[float, float, float, float, float, float, Optional[ElbowOrientation]]
  ) -> CartesianCoords:
    """Convert a tuple of cartesian coordinates to a CartesianCoords object."""
    if len(position) != 7:
      raise ValueError(
        "Position must be a tuple of 7 values (x, y, z, yaw, pitch, roll, orientation)."
      )
    orientation = ElbowOrientation(position[6])
    return CartesianCoords(
      position[0], position[1], position[2], position[3], position[4], position[5], orientation
    )

  def convert_to_joints_array(
    self, position: JointCoords
  ) -> tuple[float, float, float, float, float, float]:
    """Convert a JointSpace object to a list of joint angles."""
    joints = (
      position.base,
      position.shoulder,
      position.elbow,
      position.wrist,
      position.gripper,
      0,
    )  # PF400 has 5 joints, last is fixed
    return joints

  def convert_to_cartesian_array(
    self, position: CartesianCoords
  ) -> tuple[float, float, float, float, float, float, int]:
    """Convert a CartesianSpace object to a list of cartesian coordinates."""
    orientation_int = self._convert_orientation_enum_to_int(position.orientation)
    arr = (
      position.x,
      position.y,
      position.z,
      position.yaw,
      position.pitch,
      position.roll,
      orientation_int,
    )
    return arr

  def _convert_orientation_enum_to_int(self, orientation: Optional[ElbowOrientation]) -> int:
    """Convert an ElbowOrientation enum to an integer."""
    if orientation is None:
      return 0
    elif orientation == ElbowOrientation.RIGHT:
      return 1
    elif orientation == ElbowOrientation.LEFT:
      return 2
    else:
      raise ValueError("Invalid ElbowOrientation value.")


class CoordsConverterFactory:
  @staticmethod
  def create_coords_converter(model: str) -> CoordsConverter:
    """Factory method to create a CoordsConverter based on the robot model."""
    if model == "pf400":
      return PreciseFlex400SpaceConverter()
    elif model == "pf3400":
      return PreciseFlex3400SpaceConverter()
    else:
      raise ValueError(f"Unsupported robot model: {model}")


class PreciseFlexBackend(ArmBackend, ABC):
  """Backend for the PreciseFlex robotic arm  - Default to using Cartesian coordinates, some methods in Brook's TCS don't work with Joint coordinates."""

  def __init__(self, model: str, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__()
    self.api = PreciseFlexBackendApi(host=host, port=port, timeout=timeout)
    self.space_converter = CoordsConverterFactory.create_coords_converter(model)
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
    await self.api.exit()
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

  def _convert_orientation_int_to_enum(self, orientation_int: int) -> Optional[ElbowOrientation]:
    if orientation_int == 1:
      return ElbowOrientation.LEFT
    elif orientation_int == 2:
      return ElbowOrientation.RIGHT
    else:
      return None

  def _convert_orientation_enum_to_int(self, orientation: Optional[ElbowOrientation]) -> int:
    if orientation == ElbowOrientation.LEFT:
      return 1
    elif orientation == ElbowOrientation.RIGHT:
      return 2
    else:
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

  async def approach(self, position: Union[CartesianCoords, JointCoords], approach_height: float):
    """Move the arm to a position above the specified coordinates by a certain distance."""
    if type(position) == JointCoords:
      joints = self.space_converter.convert_to_joints_array(position)
      await self._approach_j(joints, approach_height)
    elif type(position) == CartesianCoords:
      xyz = self.space_converter.convert_to_cartesian_array(position)
      await self._approach_c(xyz[:-1], approach_height, xyz[-1])
    else:
      raise ValueError("Position must be of type JointSpace or CartesianSpace.")

  async def pick_plate(self, position: Union[CartesianCoords, JointCoords], approach_height: float):
    """Pick a plate from the specified position."""
    if type(position) == JointCoords:
      raise ValueError("pick_plate only supports CartesianCoords for PreciseFlex.")
    elif type(position) == CartesianCoords:
      xyz = self.space_converter.convert_to_cartesian_array(position)
      await self._pick_plate_c(xyz[:-1], approach_height, xyz[-1])
    else:
      raise ValueError("Position must be of type JointSpace or CartesianSpace.")

  async def place_plate(self, position: Union[CartesianCoords, JointCoords], approach_height: float):
    """Place a plate at the specified position."""
    if type(position) == JointCoords:
      raise ValueError("place_plate only supports CartesianCoords for PreciseFlex.")
    elif type(position) == CartesianCoords:
      xyz = self.space_converter.convert_to_cartesian_array(position)
      await self._place_plate_c(xyz[:-1], approach_height, xyz[-1])
    else:
      raise ValueError("Position must be of type JointSpace or CartesianSpace.")

  async def move_to(self, position: Union[CartesianCoords, JointCoords]):
    """Move the arm to a specified position in 3D space."""
    if type(position) == JointCoords:
      joints = self.space_converter.convert_to_joints_array(position)
      await self._move_to_j(joints)
    elif type(position) == CartesianCoords:
      xyz = self.space_converter.convert_to_cartesian_array(position)
      await self._move_to_c(xyz[:-1], xyz[-1])
    else:
      raise ValueError("Position must be of type JointSpace or CartesianSpace.")

  async def get_joint_position(self) -> JointCoords:
    """Get the current position of the arm in 3D space."""
    position_j = await self._get_position_j()
    return self.space_converter.convert_to_joint_space(position_j)

  async def get_cartesian_position(self) -> CartesianCoords:
    """Get the current position of the arm in 3D space."""
    position_c = await self._get_position_c()
    return self.space_converter.convert_to_cartesian_space(position_c)

  async def _approach_j(
    self, joint_position: tuple[float, float, float, float, float, float], approach_height: float
  ):
    """Move the arm to a position above the specified coordinates by a certain distance."""
    await self.api.set_location_angles(self.location_index, *joint_position)
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.move_appro(self.location_index, self.profile_index)

  async def _pick_plate_j(
    self, joint_position: tuple[float, float, float, float, float, float], approach_height: float
  ):
    """Pick a plate from the specified position."""
    await self.api.set_location_angles(self.location_index, *joint_position)
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.pick_plate(
      self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque
    )

  async def _place_plate_j(
    self, joint_position: tuple[float, float, float, float, float, float], approach_height: float
  ):
    """Place a plate at the specified position."""
    await self.api.set_location_angles(self.location_index, *joint_position)
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.place_plate(
      self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque
    )

  async def _move_to_j(self, joint_position: tuple[float, float, float, float, float, float]):
    """Move the arm to a specified position in 3D space."""
    await self.api.move_j(self.location_index, *joint_position)

  async def _get_position_j(self) -> tuple[float, float, float, float, float, float]:
    """Get the current position of the arm in 3D space."""
    return await self.api.where_j()

  async def _approach_c(
    self,
    cartesian_position: tuple[float, float, float, float, float, float],
    approach_height: float,
    orientation: int = 0,
  ):
    """Move the arm to a position above the specified coordinates by a certain distance."""
    await self.api.set_location_xyz(self.location_index, *cartesian_position)
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.set_location_config(self.location_index, orientation)
    await self.api.move_appro(self.location_index, self.profile_index)

  async def _pick_plate_c(
    self,
    cartesian_position: tuple[float, float, float, float, float, float],
    approach_height: float,
    orientation: int = 0,
  ):
    """Pick a plate from the specified position."""
    await self.api.set_location_xyz(self.location_index, *cartesian_position)
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.set_location_config(self.location_index, orientation)
    await self.api.pick_plate(
      self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque
    )

  async def _place_plate_c(
    self,
    cartesian_position: tuple[float, float, float, float, float, float],
    approach_height: float,
    orientation: int = 0,
  ):
    """Place a plate at the specified position."""
    await self.api.set_location_xyz(self.location_index, *cartesian_position)
    await self.api.set_location_z_clearance(self.location_index, approach_height)
    await self.api.set_location_config(self.location_index, orientation)
    await self.api.place_plate(
      self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque
    )

  async def _move_to_c(
    self, cartesian_position: tuple[float, float, float, float, float, float], orientation: int = 0
  ):
    """Move the arm to a specified position in 3D space."""
    await self.api.move_c(self.profile_index, *cartesian_position, config=orientation)

  async def _get_position_c(
    self,
  ) -> tuple[float, float, float, float, float, float, Optional[ElbowOrientation]]:
    """Get the current position of the arm in 3D space."""
    position = await self.api.where_c()
    return (*position[:6], self._convert_orientation_int_to_enum(position[6]))
