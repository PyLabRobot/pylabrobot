import asyncio
from abc import ABC
from typing import Dict, Iterable, List, Literal, Optional, Union

from pylabrobot.arms.backend import (
  AccessPattern,
  HorizontalAccess,
  SCARABackend,
  VerticalAccess,
)
from pylabrobot.arms.precise_flex.coords import ElbowOrientation, PreciseFlexCartesianCoords
from pylabrobot.arms.precise_flex.error_codes import ERROR_CODES
from pylabrobot.arms.precise_flex.joints import PreciseFlexJointCoords
from pylabrobot.io.socket import Socket
from pylabrobot.resources import Coordinate, Rotation


class PreciseFlexError(Exception):
  def __init__(self, replycode: int, message: str):
    self.replycode = replycode
    self.message = message

    # Map error codes to text and descriptions
    error_info = ERROR_CODES
    if replycode in error_info:
      text = error_info[replycode]["text"]
      description = error_info[replycode]["description"]
      super().__init__(f"PreciseFlexError {replycode}: {text}. {description} - {message}")
    else:
      super().__init__(f"PreciseFlexError {replycode}: {message}")


class PreciseFlexBackend(SCARABackend, ABC):
  """Backend for the PreciseFlex robotic arm  - Default to using Cartesian coordinates, some methods in Brook's TCS don't work with Joint coordinates.

  Documentation and error codes available at https://www2.brooksautomation.com/#Root/Welcome.htm
  """

  def __init__(self, has_rail: bool, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__()
    self.io = Socket(host=host, port=port)
    self.profile_index: int = 1
    self.location_index: int = 1
    self.horizontal_compliance: bool = False
    self.horizontal_compliance_torque: int = 0
    self.timeout = timeout
    self._has_rail = has_rail

  def convert_to_joint_space(self, position: Iterable[float]) -> PreciseFlexJointCoords:
    """Convert joint list to PreciseFlexJointCoords.

    Args:
      position: List of 6 floats (always padded to 6). position[0] must be 0.0 if robot has no rail.
    """

    position = list(position)

    if len(position) < 6:
      raise ValueError("Position must have 6 joint angles for robot with rail.")

    if not self._has_rail and position[0] != 0.0:
      raise ValueError("Position[0] (rail) must be 0.0 for robot without rail.")

    return PreciseFlexJointCoords(
      rail=position[0],
      base=position[1],
      shoulder=position[2],
      elbow=position[3],
      wrist=position[4],
      gripper=position[5],
    )

  def convert_to_cartesian_space(
    self, position: tuple[float, float, float, float, float, float, Optional[ElbowOrientation]]
  ) -> PreciseFlexCartesianCoords:
    """Convert a tuple of cartesian coordinates to a CartesianCoords object."""
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

  def convert_to_cartesian_array(
    self, position: PreciseFlexCartesianCoords
  ) -> tuple[float, float, float, float, float, float, int]:
    """Convert a CartesianCoords object to a list of cartesian coordinates."""
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

  async def setup(self):
    """Initialize the PreciseFlex backend."""
    await self.io.setup()
    await self.set_mode("pc")
    await self.power_on_robot()
    await self.attach(1)

  async def stop(self):
    """Stop the PreciseFlex backend."""
    await self.detach()
    await self.power_off_robot()
    await self.exit()
    await self.io.stop()

  async def set_speed(self, speed_percent: float):
    """Set the speed percentage of the arm's movement (0-100)."""
    await self.set_profile_speed(self.profile_index, speed_percent)

  async def get_speed(self) -> float:
    """Get the current speed percentage of the arm's movement."""
    return await self.get_profile_speed(self.profile_index)

  async def open_gripper(self):
    await self.send_command("gripper 1")

  async def close_gripper(self):
    await self.send_command("gripper 2")

  async def is_gripper_closed(self) -> bool:
    ret_int = await self.is_fully_closed()
    return ret_int == -1

  async def halt(self):
    """Stops the current robot immediately but leaves power on."""
    await self.send_command("halt")

  async def home(self) -> None:
    """Home the robot associated with this thread.

    Note:
      Requires power to be enabled.
      Requires robot to be attached.
      Waits until the homing is complete.
    """
    await self.send_command("home")

  async def move_to_safe(self) -> None:
    """Moves the robot to Safe Position.

    Does not include checks for collision with 3rd party obstacles inside the work volume of the robot.
    """
    await self.send_command("movetosafe")

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
    """Home all robots.

    Note:
      Requires power to be enabled.
      Requires that robots not be attached.
    """
    await self.send_command("homeAll")

  async def attach(self, attach_state: Optional[int] = None) -> int:
    """Attach or release the robot, or get attachment state.

    Args:
      attach_state: If omitted, returns the attachment state.
        0 = release the robot
        1 = attach the robot

    Returns:
      If attach_state is omitted, returns 0 if robot is not attached, -1 if attached.
         Otherwise returns 0 on success.

    Note:
      The robot must be attached to allow motion commands.
    """
    if attach_state is None:
      response = await self.send_command("attach")
      return int(response)
    await self.send_command(f"attach {attach_state}")
    return 0

  async def detach(self):
    """Detach the robot."""
    await self.attach(0)

  async def power_on_robot(self):
    """Power on the robot."""
    await self.set_power(True, self.timeout)

  async def power_off_robot(self):
    """Power off the robot."""
    await self.set_power(False)

  async def version(self) -> str:
    """Get the robot's version."""
    return await self.get_version()

  async def approach(
    self,
    position: Union[PreciseFlexCartesianCoords, Iterable[float]],
    access: Optional[AccessPattern] = None,
  ):
    """Move the arm to an approach position (offset from target).

    Args:
      position: Target position (CartesianCoords or Iterable[float])
      access: Access pattern defining how to approach the target. Defaults to VerticalAccess() if not specified.

    Example:
      # Simple vertical approach (default)
      await backend.approach(position)

      # Horizontal hotel-style approach
      await backend.approach(
        position,
        HorizontalAccess(
          approach_distance_mm=50,
          clearance_mm=50,
          lift_height_mm=100
        )
      )
    """
    if access is None:
      access = VerticalAccess()

    if isinstance(position, list):
      joint_position = self.convert_to_joint_space(position)
      await self._approach_j(joint_position, access)
    elif isinstance(position, PreciseFlexCartesianCoords):
      await self._approach_c(position, access)
    else:
      raise ValueError("Position must be of type Iterable[float] or CartesianCoords.")

  async def pick_plate(
    self,
    position: Union[PreciseFlexCartesianCoords, Iterable[float]],
    access: Optional[AccessPattern] = None,
  ):
    """Pick a plate from the specified position.

    Args:
      position: Target position for pickup (CartesianCoords only, PreciseFlexJointCoords not supported)
      access: How to access the location (VerticalAccess or HorizontalAccess).
              Defaults to VerticalAccess() if not specified.

    Raises:
      ValueError: If position is not CartesianCoords

    Example:
      # Simple vertical pick (default)
      await backend.pick_plate(position)

      # Vertical pick with custom clearance
      await backend.pick_plate(position, VerticalAccess(clearance_mm=150))

      # Horizontal hotel-style pick
      await backend.pick_plate(
        position,
        HorizontalAccess(
          approach_distance_mm=50,
          clearance_mm=50,
          lift_height_mm=100
        )
      )
    """
    if access is None:
      access = VerticalAccess()

    if not isinstance(position, PreciseFlexCartesianCoords):
      raise ValueError("pick_plate only supports CartesianCoords for PreciseFlex.")
    await self._pick_plate_c(cartesian_position=position, access=access)

  async def place_plate(
    self,
    position: Union[PreciseFlexCartesianCoords, Iterable[float]],
    access: Optional[AccessPattern] = None,
  ):
    """Place a plate at the specified position.

    Args:
      position: Target position for placement (CartesianCoords only, PreciseFlexJointCoords not supported)
      access: How to access the location (VerticalAccess or HorizontalAccess).
              Defaults to VerticalAccess() if not specified.

    Raises:
      ValueError: If position is not CartesianCoords

    Example:
      # Simple vertical place (default)
      await backend.place_plate(position)

      # Vertical place with custom clearance
      await backend.place_plate(position, VerticalAccess(clearance_mm=150))

      # Horizontal hotel-style place
      await backend.place_plate(
        position,
        HorizontalAccess(
          approach_distance_mm=50,
          clearance_mm=50,
          lift_height_mm=100
        )
      )
    """
    if access is None:
      access = VerticalAccess()

    if not isinstance(position, PreciseFlexCartesianCoords):
      raise ValueError("place_plate only supports CartesianCoords for PreciseFlex.")
    await self._place_plate_c(cartesian_position=position, access=access)

  async def move_to(self, position: Union[PreciseFlexCartesianCoords, Iterable[float]]):
    """Move the arm to a specified position in 3D space.

    Args:
      position: Either CartesianCoords or a 6-element list [rail, base, shoulder, elbow, wrist, gripper]
    """
    if isinstance(position, list):
      if len(position) < 6:
        raise ValueError(
          "Joint list must have 6 elements: [rail, base, shoulder, elbow, wrist, gripper]"
        )
      joint_coords = PreciseFlexJointCoords(
        rail=position[0],
        base=position[1],
        shoulder=position[2],
        elbow=position[3],
        wrist=position[4],
        gripper=position[5],
      )
      await self.move_j(profile_index=self.profile_index, joint_coords=joint_coords)
    elif isinstance(position, PreciseFlexCartesianCoords):
      await self.move_c(profile_index=self.profile_index, cartesian_coords=position)
    else:
      raise ValueError("Position must be of type JointSpace or CartesianCoords.")

  async def get_joint_position(self) -> PreciseFlexJointCoords:
    """Get the current position of the arm in 3D space."""
    data = await self.send_command("wherej")
    parts = data.split()

    if not parts:
      # In case of incomplete response, wait for EOM and try to read again
      await self.wait_for_eom()
      data = await self.send_command("wherej")
      parts = data.split()
      if not parts:
        raise PreciseFlexError(-1, "Unexpected response format from wherej command.")

    axes = list(self._parse_angles_response(parts))
    return self.convert_to_joint_space(axes)

  async def get_cartesian_position(self) -> PreciseFlexCartesianCoords:
    """Get the current position of the arm in 3D space."""
    data = await self.send_command("wherec")
    parts = data.split()

    if len(parts) != 7:
      # In case of incomplete response, wait for EOM and try to read again
      await self.wait_for_eom()
      data = await self.send_command("wherec")
      parts = data.split()
      if len(parts) != 7:
        raise PreciseFlexError(-1, "Unexpected response format from wherec command.")

    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts[0:6])
    config = int(parts[6])

    # return (x, y, z, yaw, pitch, roll, config)
    enum_thing = self._convert_orientation_int_to_enum(config)

    return self.convert_to_cartesian_space(position=(x, y, z, yaw, pitch, roll, enum_thing))

  async def send_command(self, command: str) -> str:
    await self.io.write(command.encode("utf-8") + b"\n")
    reply = await self.io.readline()

    print(f"Sent command: {command}, Received reply: {reply!r}")

    return self._parse_reply_ensure_successful(reply)

  def _parse_reply_ensure_successful(self, reply: bytes) -> str:
    """Parse reply from Precise Flex.

    Expected format: b'replycode data message\r\n'
    - replycode is an integer at the beginning
    - data is rest of the line (excluding CRLF)
    """
    print("REPLY: ", reply)
    text = reply.decode().strip()  # removes \r\n
    if not text:
      raise PreciseFlexError(-1, "Empty reply from device.")

    parts = text.split(" ", 1)
    if len(parts) == 1:
      replycode = int(parts[0])
      data = ""
    else:
      replycode, data = int(parts[0]), parts[1]

    if replycode != 0:
      # if error is reported, the data part generally contains the error message
      raise PreciseFlexError(replycode, data)

    return data

  async def _approach_j(self, joint_position: PreciseFlexJointCoords, access: AccessPattern):
    """Move the arm to a position above the specified coordinates.

    The approach behavior depends on the access pattern:
    - VerticalAccess: Approaches from above using approach_height_mm
    - HorizontalAccess: Approaches from the side using approach_distance_mm
    """
    await self.set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail(access)
    await self.move_to_stored_location_appro(self.location_index, self.profile_index)

  async def _pick_plate_j(self, joint_position: PreciseFlexJointCoords, access: AccessPattern):
    """Pick a plate from the specified position using joint coordinates."""
    await self.set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail(access)
    await self.pick_plate_from_stored_position(
      self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque
    )

  async def _place_plate_j(self, joint_position: PreciseFlexJointCoords, access: AccessPattern):
    """Place a plate at the specified position using joint coordinates."""
    await self.set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail(access)
    await self.place_plate_to_stored_position(
      self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque
    )

  async def _approach_c(
    self,
    cartesian_position: PreciseFlexCartesianCoords,
    access: AccessPattern,
  ):
    """Move the arm to a position above the specified coordinates.

    The approach behavior depends on the access pattern:
    - VerticalAccess: Approaches from above using approach_height_mm
    - HorizontalAccess: Approaches from the side using approach_distance_mm
    """
    await self.set_location_xyz(self.location_index, cartesian_position)
    await self._set_grip_detail(access)
    orientation_int = self._convert_orientation_enum_to_int(cartesian_position.orientation)
    await self.set_location_config(self.location_index, orientation_int)
    await self.move_to_stored_location_appro(self.location_index, self.profile_index)

  async def _pick_plate_c(
    self,
    cartesian_position: PreciseFlexCartesianCoords,
    access: AccessPattern,
  ):
    """Pick a plate from the specified position using Cartesian coordinates."""
    await self.set_location_xyz(self.location_index, cartesian_position)
    await self._set_grip_detail(access)
    orientation_int = self._convert_orientation_enum_to_int(cartesian_position.orientation)
    orientation_int |= 0x1000  # GPL_Single: restrict wrist to ±180°
    await self.set_location_config(self.location_index, orientation_int)
    await self.pick_plate_from_stored_position(
      self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque
    )

  async def _place_plate_c(
    self,
    cartesian_position: PreciseFlexCartesianCoords,
    access: AccessPattern,
  ):
    """Place a plate at the specified position using Cartesian coordinates."""
    await self.set_location_xyz(self.location_index, cartesian_position)
    await self._set_grip_detail(access)
    orientation_int = self._convert_orientation_enum_to_int(cartesian_position.orientation)
    orientation_int |= 0x1000  # GPL_Single: restrict wrist to ±180°
    await self.set_location_config(self.location_index, orientation_int)
    await self.place_plate_to_stored_position(
      self.location_index, self.horizontal_compliance, self.horizontal_compliance_torque
    )

  async def _set_grip_detail(self, access: AccessPattern):
    """Configure station type for pick/place operations based on access pattern.

    Calls TCS set_station_type command to configure how the robot interprets
    clearance values and performs approach/retract motions.

    Args:
      access: Access pattern (VerticalAccess or HorizontalAccess) defining
              how to approach and retract from the location.
    """
    if isinstance(access, VerticalAccess):
      # Vertical access: access_type=1, z_clearance is vertical distance
      await self.set_station_type(
        station_id=self.location_index,
        access_type=1,
        location_type=0,
        z_clearance=access.clearance_mm,
        z_above=0,
        z_grasp_offset=access.gripper_offset_mm,
      )
    elif isinstance(access, HorizontalAccess):
      # Horizontal access: access_type=0, z_clearance is horizontal distance
      await self.set_station_type(
        station_id=self.location_index,
        access_type=0,
        location_type=0,
        z_clearance=access.clearance_mm,
        z_above=access.lift_height_mm,
        z_grasp_offset=access.gripper_offset_mm,
      )
    else:
      raise ValueError("Access pattern must be VerticalAccess or HorizontalAccess.")

  # region GENERAL COMMANDS

  async def get_base(self) -> tuple[float, float, float, float]:
    """Get the robot base offset.

    Returns:
      A tuple containing (x_offset, y_offset, z_offset, z_rotation)
    """
    data = await self.send_command("base")
    parts = data.split()
    if len(parts) != 4:
      raise PreciseFlexError(-1, "Unexpected response format from base command.")

    x_offset = float(parts[0])
    y_offset = float(parts[1])
    z_offset = float(parts[2])
    z_rotation = float(parts[3])

    return (x_offset, y_offset, z_offset, z_rotation)

  async def set_base(
    self, x_offset: float, y_offset: float, z_offset: float, z_rotation: float
  ) -> None:
    """Set the robot base offset.

    Args:
      x_offset: Base X offset
      y_offset: Base Y offset
      z_offset: Base Z offset
      z_rotation: Base Z rotation

    Note:
      The robot must be attached to set the base.
      Setting the base pauses any robot motion in progress.
    """
    await self.send_command(f"base {x_offset} {y_offset} {z_offset} {z_rotation}")

  async def exit(self) -> None:
    """Close the communications link immediately.

    Note:
      Does not affect any robots that may be active.
    """
    await self.io.write(b"exit\n")

  async def get_power_state(self) -> int:
    """Get the current robot power state.

    Returns:
      Current power state (0 = disabled, 1 = enabled)
    """
    response = await self.send_command("hp")
    return int(response)

  async def set_power(self, enable: bool, timeout: int = 0) -> None:
    """Enable or disable robot high power.

    Args:
      enable: True to enable power, False to disable
      timeout: Wait timeout for power to come on.
        0 or omitted = do not wait for power to come on
        > 0 = wait this many seconds for power to come on
        -1 = wait indefinitely for power to come on

    Raises:
      PreciseFlexError: If power does not come on within the specified timeout.
    """
    power_state = 1 if enable else 0
    if timeout == 0:
      await self.send_command(f"hp {power_state}")
    else:
      await self.send_command(f"hp {power_state} {timeout}")

  Mode = Literal["pc", "verbose"]

  async def get_mode(self) -> Mode:
    """Get the current response mode.

    Returns:
      Current mode (0 = PC mode, 1 = verbose mode)
    """
    response = await self.send_command("mode")
    mapping: Dict[int, PreciseFlexBackend.Mode] = {
      0: "pc",
      1: "verbose",
    }
    return mapping[int(response)]

  async def set_mode(self, mode: Mode) -> None:
    """Set the response mode.

    Args:
      mode: Response mode to set.
      0 = Select PC mode
      1 = Select verbose mode

    Note:
      When using serial communications, the mode change does not take effect
      until one additional command has been processed.
    """
    if mode not in ["pc", "verbose"]:
      raise ValueError("Mode must be 'pc' or 'verbose'")
    mapping = {
      "pc": 0,
      "verbose": 1,
    }
    await self.send_command(f"mode {mapping[mode]}")

  async def get_monitor_speed(self) -> int:
    """Get the global system (monitor) speed.

    Returns:
      Current monitor speed as a percentage (1-100)
    """
    response = await self.send_command("mspeed")
    return int(response)

  async def set_monitor_speed(self, speed_percent: int) -> None:
    """Set the global system (monitor) speed.

    Args:
      speed_percent: Speed percentage between 1 and 100, where 100 means full speed.

    Raises:
      ValueError: If speed_percent is not between 1 and 100.
    """
    if not (1 <= speed_percent <= 100):
      raise ValueError("Speed percent must be between 1 and 100")
    await self.send_command(f"mspeed {speed_percent}")

  async def nop(self) -> None:
    """No operation command.

    Does nothing except return the standard reply. Can be used to see if the link
    is active or to check for exceptions.
    """
    await self.send_command("nop")

  async def get_payload(self) -> int:
    """Get the payload percent value for the current robot.

    Returns:
      Current payload as a percentage of maximum (0-100)
    """
    response = await self.send_command("payload")
    return int(response)

  async def set_payload(self, payload_percent: int) -> None:
    """Set the payload percent of maximum for the currently selected or attached robot.

    Args:
      payload_percent: Payload percentage from 0 to 100 indicating the percent of the maximum payload the robot is carrying.

    Raises:
      ValueError: If payload_percent is not between 0 and 100.

    Note:
      If the robot is moving, waits for the robot to stop before setting a value.
    """
    if not (0 <= payload_percent <= 100):
      raise ValueError("Payload percent must be between 0 and 100")
    await self.send_command(f"payload {payload_percent}")

  async def set_parameter(
    self,
    data_id: int,
    value,
    unit_number: Optional[int] = None,
    sub_unit: Optional[int] = None,
    array_index: Optional[int] = None,
  ) -> None:
    """Change a value in the controller's parameter database.

    Args:
      data_id: DataID of parameter.
      value: New parameter value. If string, will be quoted automatically.
      unit_number: Unit number, usually the robot number (1 - N_ROB).
      sub_unit: Sub-unit, usually 0.
      array_index: Array index.

    Note:
      Updated values are not saved in flash unless a save-to-flash operation
      is performed (see DataID 901).
    """
    if unit_number is not None and sub_unit is not None and array_index is not None:
      # 5 argument format
      if isinstance(value, str):
        await self.send_command(f'pc {data_id} {unit_number} {sub_unit} {array_index} "{value}"')
      else:
        await self.send_command(f"pc {data_id} {unit_number} {sub_unit} {array_index} {value}")
    else:
      # 2 argument format
      if isinstance(value, str):
        await self.send_command(f'pc {data_id} "{value}"')
      else:
        await self.send_command(f"pc {data_id} {value}")

  async def get_parameter(
    self,
    data_id: int,
    unit_number: Optional[int] = None,
    sub_unit: Optional[int] = None,
    array_index: Optional[int] = None,
  ) -> str:
    """Get the value of a numeric parameter database item.

    Args:
      data_id: DataID of parameter.
      unit_number: Unit number, usually the robot number (1-NROB).
      sub_unit: Sub-unit, usually 0.
      array_index: Array index.

    Returns:
      str: The numeric value of the specified database parameter.
    """
    if unit_number is not None:
      if sub_unit is not None:
        if array_index is not None:
          response = await self.send_command(f"pd {data_id} {unit_number} {sub_unit} {array_index}")
        else:
          response = await self.send_command(f"pd {data_id} {unit_number} {sub_unit}")
      else:
        response = await self.send_command(f"pd {data_id} {unit_number}")
    else:
      response = await self.send_command(f"pd {data_id}")
    return response

  async def reset(self, robot_number: int) -> None:
    """Reset the threads associated with the specified robot.

    Stops and restarts the threads for the specified robot. Any TCP/IP connections
    made by these threads are broken. This command can only be sent to the status thread.

    Args:
      robot_number: The number of the robot thread to reset, from 1 to N_ROB. Must not be zero.

    Raises:
      ValueError: If robot_number is zero or negative.
    """
    if robot_number <= 0:
      raise ValueError("Robot number must be greater than zero")
    await self.send_command(f"reset {robot_number}")

  async def get_selected_robot(self) -> int:
    """Get the number of the currently selected robot.

    Returns:
      The number of the currently selected robot.
    """
    response = await self.send_command("selectRobot")
    return int(response)

  async def select_robot(self, robot_number: int) -> None:
    """Change the robot associated with this communications link.

    Does not affect the operation or attachment state of the robot. The status thread
    may select any robot or 0. Except for the status thread, a robot may only be
    selected by one thread at a time.

    Args:
      robot_number: The new robot to be connected to this thread (1 to N_ROB) or 0 for none.
    """
    await self.send_command(f"selectRobot {robot_number}")

  async def get_signal(self, signal_number: int) -> int:
    """Get the value of the specified digital input or output signal.

    Args:
      signal_number: The number of the digital signal to get.

    Returns:
      The current signal value.
    """
    response = await self.send_command(f"sig {signal_number}")
    sig_id, sig_val = response.split()
    return int(sig_val)

  async def set_signal(self, signal_number: int, value: int) -> None:
    """Set the specified digital input or output signal.

    Args:
      signal_number: The number of the digital signal to set.
      value: The signal value to set. 0 = off, non-zero = on.
    """
    await self.send_command(f"sig {signal_number} {value}")

  async def get_system_state(self) -> int:
    """Get the global system state code.

    Returns:
      The global system state code. Please see documentation for DataID 234.
    """
    response = await self.send_command("sysState")
    return int(response)

  async def get_tool(self) -> tuple[float, float, float, float, float, float]:
    """Get the current tool transformation values.

    Returns:
      A tuple containing (X, Y, Z, yaw, pitch, roll) for the tool transformation.
    """
    data = await self.send_command("tool")
    # Remove "tool:" prefix if present
    if data.startswith("tool: "):
      data = data[6:]

    parts = data.split()
    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format from tool command.")

    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts)

    return (x, y, z, yaw, pitch, roll)

  async def set_tool(
    self, x: float, y: float, z: float, yaw: float, pitch: float, roll: float
  ) -> None:
    """Set the robot tool transformation.

    The robot must be attached to set the tool. Setting the tool pauses any robot motion in progress.

    Args:
      x: Tool X coordinate.
      y: Tool Y coordinate.
      z: Tool Z coordinate.
      yaw: Tool yaw rotation.
      pitch: Tool pitch rotation.
      roll: Tool roll rotation.
    """
    await self.send_command(f"tool {x} {y} {z} {yaw} {pitch} {roll}")

  async def get_version(self) -> str:
    """Get the current version of TCS and any installed plug-ins.

    Returns:
      str: The current version information.
    """
    return await self.send_command("version")

  # region LOCATION COMMANDS

  async def get_location_angles(
    self, location_index: int
  ) -> tuple[int, int, float, float, float, float, float, float]:
    """Get the angle values for the specified station index.

    Args:
      location_index: The station index, from 1 to N_LOC.

    Returns:
      A tuple containing (type_code, station_index, angle1, angle2, angle3, angle4, angle5, angle6)

    Raises:
      PreciseFlexError: If attempting to get angles from a Cartesian location.
    """
    data = await self.send_command(f"locAngles {location_index}")
    parts = data.split(" ")

    type_code = int(parts[0])
    if type_code != 1:
      raise PreciseFlexError(-1, "Location is not of angles type.")

    station_index = int(parts[1])
    angle1, angle2, angle3, angle4, angle5, angle6 = self._parse_angles_response(parts[2:])

    return (type_code, station_index, angle1, angle2, angle3, angle4, angle5, angle6)

  async def set_joint_angles(
    self,
    location_index: int,
    joint_position: PreciseFlexJointCoords,
  ) -> None:
    """Set joint angles for stored location, handling rail configuration."""
    if self._has_rail:
      await self.send_command(
        f"locAngles {location_index} "
        f"{joint_position.rail} "
        f"{joint_position.base} "
        f"{joint_position.shoulder} "
        f"{joint_position.elbow} "
        f"{joint_position.wrist} "
        f"{joint_position.gripper}"
      )
    else:
      # Exclude rail for robots without rail
      await self.send_command(
        f"locAngles {location_index} "
        f"{joint_position.base} "
        f"{joint_position.shoulder} "
        f"{joint_position.elbow} "
        f"{joint_position.wrist} "
        f"{joint_position.gripper}"
      )

  async def get_location_xyz(
    self, location_index: int
  ) -> tuple[int, int, float, float, float, float, float, float]:
    """Get the Cartesian position values for the specified station index.

    Args:
      location_index: The station index, from 1 to N_LOC.

    Returns:
      A tuple containing (type_code, station_index, X, Y, Z, yaw, pitch, roll)

    Raises:
      PreciseFlexError: If attempting to get Cartesian position from an angles type location.
    """
    data = await self.send_command(f"locXyz {location_index}")
    parts = data.split(" ")

    type_code = int(parts[0])
    if type_code != 0:
      raise PreciseFlexError(-1, "Location is not of Cartesian type.")

    if len(parts) != 8:
      raise PreciseFlexError(-1, "Unexpected response format from locXyz command.")

    station_index = int(parts[1])
    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts[2:8])

    return (type_code, station_index, x, y, z, yaw, pitch, roll)

  async def set_location_xyz(
    self,
    location_index: int,
    cartesian_position: PreciseFlexCartesianCoords,
  ) -> None:
    """Set the Cartesian position values for the specified station index.

    Args:
      location_index: The station index, from 1 to N_LOC.
      x: X coordinate.
      y: Y coordinate.
      z: Z coordinate.
      yaw: Yaw rotation. Defaults to 0.0.
      pitch: Pitch rotation. Defaults to 0.0.
      roll: Roll rotation. Defaults to 0.0.
    """
    await self.send_command(
      f"locXyz {location_index} "
      f"{cartesian_position.location.x} "
      f"{cartesian_position.location.y} "
      f"{cartesian_position.location.z} "
      f"{cartesian_position.rotation.yaw} "
      f"{cartesian_position.rotation.pitch} "
      f"{cartesian_position.rotation.roll}"
    )

  async def get_location_z_clearance(self, location_index: int) -> tuple[int, float, bool]:
    """Get the ZClearance and ZWorld properties for the specified location.

    Args:
      location_index: The station index, from 1 to N_LOC.

    Returns:
      A tuple containing (station_index, z_clearance, z_world)
    """
    data = await self.send_command(f"locZClearance {location_index}")
    parts = data.split(" ")

    if len(parts) != 3:
      raise PreciseFlexError(-1, "Unexpected response format from locZClearance command.")

    station_index = int(parts[0])
    z_clearance = float(parts[1])
    z_world = True if float(parts[2]) != 0 else False

    return (station_index, z_clearance, z_world)

  async def set_location_z_clearance(
    self, location_index: int, z_clearance: float, z_world: Optional[bool] = None
  ) -> None:
    """Set the ZClearance and ZWorld properties for the specified location.

    Args:
      location_index: The station index, from 1 to N_LOC.
      z_clearance: The new ZClearance property value.
      z_world (float, optional): The new ZWorld property value. If omitted, only ZClearance is set.
    """
    if z_world is None:
      await self.send_command(f"locZClearance {location_index} {z_clearance}")
    else:
      z_world_int = 1 if z_world else 0
      await self.send_command(f"locZClearance {location_index} {z_clearance} {z_world_int}")

  async def get_location_config(self, location_index: int) -> tuple[int, int]:
    """Get the Config property for the specified location.

    Args:
      location_index: The station index, from 1 to N_LOC.

    Returns:
      A tuple containing (station_index, config_value)
      config_value is a bit mask where:
      - 0 = None (no configuration specified)
      - 0x01 = GPL_Righty (right shouldered configuration)
      - 0x02 = GPL_Lefty (left shouldered configuration)
      - 0x04 = GPL_Above (elbow above the wrist)
      - 0x08 = GPL_Below (elbow below the wrist)
      - 0x10 = GPL_Flip (wrist pitched up)
      - 0x20 = GPL_NoFlip (wrist pitched down)
      - 0x1000 = GPL_Single (restrict wrist axis to +/- 180 degrees)
      Values can be combined using bitwise OR.
    """
    data = await self.send_command(f"locConfig {location_index}")
    parts = data.split(" ")

    if len(parts) != 2:
      raise PreciseFlexError(-1, "Unexpected response format from locConfig command.")

    station_index = int(parts[0])
    config_value = int(parts[1])

    return (station_index, config_value)

  async def set_location_config(self, location_index: int, config_value: int) -> None:
    """Set the Config property for the specified location.

    Args:
      location_index: The station index, from 1 to N_LOC.
      config_value: The new Config property value as a bit mask where:
      - 0 = None (no configuration specified)
      - 0x01 = GPL_Righty (right shouldered configuration)
      - 0x02 = GPL_Lefty (left shouldered configuration)
      - 0x04 = GPL_Above (elbow above the wrist)
      - 0x08 = GPL_Below (elbow below the wrist)
      - 0x10 = GPL_Flip (wrist pitched up)
      - 0x20 = GPL_NoFlip (wrist pitched down)
      - 0x1000 = GPL_Single (restrict wrist axis to +/- 180 degrees)
      Values can be combined using bitwise OR.

    Raises:
      ValueError: If config_value contains invalid bits or conflicting configurations.
    """
    # Define valid bit masks
    GPL_RIGHTY = 0x01
    GPL_LEFTY = 0x02
    GPL_ABOVE = 0x04
    GPL_BELOW = 0x08
    GPL_FLIP = 0x10
    GPL_NOFLIP = 0x20
    GPL_SINGLE = 0x1000

    # All valid bits
    ALL_VALID_BITS = (
      GPL_RIGHTY | GPL_LEFTY | GPL_ABOVE | GPL_BELOW | GPL_FLIP | GPL_NOFLIP | GPL_SINGLE
    )

    # Check for invalid bits
    if config_value & ~ALL_VALID_BITS:
      raise ValueError(f"Invalid config bits specified: 0x{config_value:X}")

    # Check for conflicting configurations
    if (config_value & GPL_RIGHTY) and (config_value & GPL_LEFTY):
      raise ValueError("Cannot specify both GPL_Righty and GPL_Lefty")

    if (config_value & GPL_ABOVE) and (config_value & GPL_BELOW):
      raise ValueError("Cannot specify both GPL_Above and GPL_Below")

    if (config_value & GPL_FLIP) and (config_value & GPL_NOFLIP):
      raise ValueError("Cannot specify both GPL_Flip and GPL_NoFlip")

    await self.send_command(f"locConfig {location_index} {config_value}")

  async def dest_c(self, arg1: int = 0) -> tuple[float, float, float, float, float, float, int]:
    """Get the destination or current Cartesian location of the robot.

    Args:
      arg1: Selects return value. Defaults to 0.
      0 = Return current Cartesian location if robot is not moving
      1 = Return target Cartesian location of the previous or current move

    Returns:
      A tuple containing (X, Y, Z, yaw, pitch, roll, config)
      If arg1 = 1 or robot is moving, returns the target location.
      If arg1 = 0 and robot is not moving, returns the current location.
    """
    if arg1 == 0:
      data = await self.send_command("destC")
    else:
      data = await self.send_command(f"destC {arg1}")

    parts = data.split()
    if len(parts) != 7:
      raise PreciseFlexError(-1, "Unexpected response format from destC command.")

    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts[:6])
    config = int(parts[6])

    return (x, y, z, yaw, pitch, roll, config)

  async def dest_j(self, arg1: int = 0) -> tuple[float, float, float, float, float, float]:
    """Get the destination or current joint location of the robot.

    Args:
      arg1: Selects return value. Defaults to 0.
      0 = Return current joint location if robot is not moving
      1 = Return target joint location of the previous or current move

    Returns:
      A list containing [axis1, axis2, ..., axisn]
      If arg1 = 1 or robot is moving, returns the target joint positions.
      If arg1 = 0 and robot is not moving, returns the current joint positions.
    """
    if arg1 == 0:
      data = await self.send_command("destJ")
    else:
      data = await self.send_command(f"destJ {arg1}")

    parts = data.split()
    if not parts:
      raise PreciseFlexError(-1, "Unexpected response format from destJ command.")

    # Ensure we have exactly 6 elements, padding with 0.0 if necessary
    angle1, angle2, angle3, angle4, angle5, angle6 = self._parse_angles_response(parts)

    return (angle1, angle2, angle3, angle4, angle5, angle6)

  async def here_j(self, location_index: int) -> None:
    """Record the current position of the selected robot into the specified Location as angles.

    The Location is automatically set to type "angles".

    Args:
      location_index: The station index, from 1 to N_LOC.
    """
    await self.send_command(f"hereJ {location_index}")

  async def here_c(self, location_index: int) -> None:
    """Record the current position of the selected robot into the specified Location as Cartesian.

    The Location object is automatically set to type "Cartesian".
    Can be used to change the pallet origin (index 1,1,1) value.

    Args:
      location_index: The station index, from 1 to N_LOC.
    """
    await self.send_command(f"hereC {location_index}")

  # region PROFILE COMMANDS

  async def get_profile_speed(self, profile_index: int) -> float:
    """Get the speed property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed as a percentage. 100 = full speed.
    """
    response = await self.send_command(f"Speed {profile_index}")
    profile, speed = response.split()
    return float(speed)

  async def set_profile_speed(self, profile_index: int, speed_percent: float) -> None:
    """Set the speed property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed_percent: The new speed as a percentage. 100 = full speed.
      Values > 100 may be accepted depending on system configuration.
    """
    await self.send_command(f"Speed {profile_index} {speed_percent}")

  async def get_profile_speed2(self, profile_index: int) -> float:
    """Get the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed2 as a percentage. Used for Cartesian moves.
    """
    response = await self.send_command(f"Speed2 {profile_index}")
    profile, speed2 = response.split()
    return float(speed2)

  async def set_profile_speed2(self, profile_index: int, speed2_percent: float) -> None:
    """Set the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed2_percent: The new speed2 as a percentage. 100 = full speed.
      Used for Cartesian moves. Normally set to 0.
    """
    await self.send_command(f"Speed2 {profile_index} {speed2_percent}")

  async def get_profile_accel(self, profile_index: int) -> float:
    """Get the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration as a percentage. 100 = maximum acceleration.
    """
    response = await self.send_command(f"Accel {profile_index}")
    profile, accel = response.split()
    return float(accel)

  async def set_profile_accel(self, profile_index: int, accel_percent: float) -> None:
    """Set the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      accel_percent: The new acceleration as a percentage. 100 = maximum acceleration.
      Maximum value depends on system configuration.
    """
    await self.send_command(f"Accel {profile_index} {accel_percent}")

  async def get_profile_accel_ramp(self, profile_index: int) -> float:
    """Get the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration ramp time in seconds.
    """
    response = await self.send_command(f"AccRamp {profile_index}")
    profile, accel_ramp = response.split()
    return float(accel_ramp)

  async def set_profile_accel_ramp(self, profile_index: int, accel_ramp_seconds: float) -> None:
    """Set the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      accel_ramp_seconds: The new acceleration ramp time in seconds.
    """
    await self.send_command(f"AccRamp {profile_index} {accel_ramp_seconds}")

  async def get_profile_decel(self, profile_index: int) -> float:
    """Get the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration as a percentage. 100 = maximum deceleration.
    """
    response = await self.send_command(f"Decel {profile_index}")
    profile, decel = response.split()
    return float(decel)

  async def set_profile_decel(self, profile_index: int, decel_percent: float) -> None:
    """Set the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      decel_percent: The new deceleration as a percentage. 100 = maximum deceleration.
      Maximum value depends on system configuration.
    """
    await self.send_command(f"Decel {profile_index} {decel_percent}")

  async def get_profile_decel_ramp(self, profile_index: int) -> float:
    """Get the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration ramp time in seconds.
    """
    response = await self.send_command(f"DecRamp {profile_index}")
    profile, decel_ramp = response.split()
    return float(decel_ramp)

  async def set_profile_decel_ramp(self, profile_index: int, decel_ramp_seconds: float) -> None:
    """Set the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      decel_ramp_seconds: The new deceleration ramp time in seconds.
    """
    await self.send_command(f"DecRamp {profile_index} {decel_ramp_seconds}")

  async def get_profile_in_range(self, profile_index: int) -> float:
    """Get the InRange property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current InRange value (-1 to 100).
      -1 = do not stop at end of motion if blending is possible
      0 = always stop but do not check end point error
      > 0 = wait until close to end point (larger numbers mean less position error allowed)
    """
    response = await self.send_command(f"InRange {profile_index}")
    profile, in_range = response.split()
    return float(in_range)

  async def set_profile_in_range(self, profile_index: int, in_range_value: float) -> None:
    """Set the InRange property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      in_range_value: The new InRange value from -1 to 100.
      -1 = do not stop at end of motion if blending is possible
      0 = always stop but do not check end point error
      > 0 = wait until close to end point (larger numbers mean less position error allowed)

    Raises:
      ValueError: If in_range_value is not between -1 and 100.
    """
    if not (-1 <= in_range_value <= 100):
      raise ValueError("InRange value must be between -1 and 100")
    await self.send_command(f"InRange {profile_index} {in_range_value}")

  async def get_profile_straight(self, profile_index: int) -> bool:
    """Get the Straight property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      The current Straight property value.
      True = follow a straight-line path
      False = follow a joint-based path (coordinated axes movement)
    """
    response = await self.send_command(f"Straight {profile_index}")
    profile, straight = response.split()

    return straight == "True"

  async def set_profile_straight(self, profile_index: int, straight_mode: bool) -> None:
    """Set the Straight property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      straight_mode: The path type to use.
      True = follow a straight-line path
      False = follow a joint-based path (robot axes move in coordinated manner)

    Raises:
      ValueError: If straight_mode is not True or False.
    """
    straight_int = 1 if straight_mode else 0
    await self.send_command(f"Straight {profile_index} {straight_int}")

  async def set_motion_profile_values(
    self,
    profile: int,
    speed: float,
    speed2: float,
    acceleration: float,
    deceleration: float,
    acceleration_ramp: float,
    deceleration_ramp: float,
    in_range: float,
    straight: bool,
  ):
    """
    Set motion profile values for the specified profile index on the PreciseFlex robot.

    Args:
      profile: Profile index to set values for.
      speed: Percentage of maximum speed. 100 = full speed. Values >100 may be accepted depending on system config.
      speed2: Secondary speed setting, typically for Cartesian moves. Normally 0. Interpreted as a percentage.
      acceleration: Percentage of maximum acceleration. 100 = full accel.
      deceleration: Percentage of maximum deceleration. 100 = full decel.
      acceleration_ramp: Acceleration ramp time in seconds.
      deceleration_ramp: Deceleration ramp time in seconds.
      in_range: InRange value, from -1 to 100. -1 = allow blending, 0 = stop without checking, >0 = enforce position accuracy.
      straight: If True, follow a straight-line path (-1). If False, follow a joint-based path (0).
    """
    if not (0 <= speed):
      raise ValueError("Speed must be > 0 (percent).")

    if not (0 <= speed2):
      raise ValueError("Speed2 must be > 0 (percent).")

    if not (0 <= acceleration <= 100):
      raise ValueError("Acceleration must be between 0 and 100 (percent).")

    if not (0 <= deceleration <= 100):
      raise ValueError("Deceleration must be between 0 and 100 (percent).")

    if acceleration_ramp < 0:
      raise ValueError("Acceleration ramp must be >= 0 (seconds).")

    if deceleration_ramp < 0:
      raise ValueError("Deceleration ramp must be >= 0 (seconds).")

    if not (-1 <= in_range <= 100):
      raise ValueError("InRange must be between -1 and 100.")

    straight_int = -1 if straight else 0
    await self.send_command(
      f"Profile {profile} {speed} {speed2} {acceleration} {deceleration} {acceleration_ramp} {deceleration_ramp} {in_range} {straight_int}"
    )

  async def get_motion_profile_values(
    self, profile: int
  ) -> tuple[int, float, float, float, float, float, float, float, bool]:
    """
    Get the current motion profile values for the specified profile index on the PreciseFlex robot.

    Args:
      profile: Profile index to get values for.

    Returns:
      A tuple containing (profile, speed, speed2, acceleration, deceleration, acceleration_ramp, deceleration_ramp, in_range, straight)
        - profile: Profile index
        - speed: Percentage of maximum speed
        - speed2: Secondary speed setting
        - acceleration: Percentage of maximum acceleration
        - deceleration: Percentage of maximum deceleration
        - acceleration_ramp: Acceleration ramp time in seconds
        - deceleration_ramp: Deceleration ramp time in seconds
        - in_range: InRange value (-1 to 100)
        - straight: True if straight-line path, False if joint-based path
    """
    data = await self.send_command(f"Profile {profile}")
    parts = data.split(" ")
    if len(parts) != 9:
      raise PreciseFlexError(-1, "Unexpected response format from device.")

    return (
      int(parts[0]),
      float(parts[1]),
      float(parts[2]),
      float(parts[3]),
      float(parts[4]),
      float(parts[5]),
      float(parts[6]),
      float(parts[7]),
      False if int(parts[8]) == 0 else True,
    )

  # region MOTION COMMANDS
  async def move_to_stored_location(self, location_index: int, profile_index: int) -> None:
    """Move to the location specified by the station index using the specified profile.

    Args:
      location_index: The index of the location to which the robot moves.
      profile_index: The profile index for this move.

    Note:
      Requires that the robot be attached.
    """
    await self.send_command(f"move {location_index} {profile_index}")

  async def move_to_stored_location_appro(self, location_index: int, profile_index: int) -> None:
    """Approach the location specified by the station index using the specified profile.

    This is similar to `move_to_stored_location` except that the Z clearance value is included.

    Args:
      location_index: The index of the location to which the robot moves.
      profile_index: The profile index for this move.

    Note:
      Requires that the robot be attached.
    """
    await self.send_command(f"moveAppro {location_index} {profile_index}")

  async def move_extra_axis(
    self, axis1_position: float, axis2_position: Optional[float] = None
  ) -> None:
    """Post a move for one or two extra axes during the next Cartesian motion.

    Does not cause the robot to move at this time. Only some kinematic modules support extra axes.

    Args:
      axis1_position: The destination position for the 1st extra axis.
      axis2_position (float, optional): The destination position for the 2nd extra axis, if any.

    Note:
      Requires that the robot be attached.
    """
    if axis2_position is None:
      await self.send_command(f"moveExtraAxis {axis1_position}")
    else:
      await self.send_command(f"moveExtraAxis {axis1_position} {axis2_position}")

  async def move_one_axis(
    self, axis_number: int, destination_position: float, profile_index: int
  ) -> None:
    """Move a single axis to the specified position using the specified profile.

    Args:
      axis_number: The number of the axis to move.
      destination_position: The destination position for this axis.
      profile_index: The index of the profile to use during this motion.

    Note:
      Requires that the robot be attached.
    """
    await self.send_command(f"moveOneAxis {axis_number} {destination_position} {profile_index}")

  async def move_c(
    self,
    profile_index: int,
    cartesian_coords: PreciseFlexCartesianCoords,
  ) -> None:
    """Move the robot to the Cartesian location specified by the arguments.

    Args:
      profile_index: The profile index to use for this motion.
      x: X coordinate.
      y: Y coordinate.
      z: Z coordinate.
      yaw: Yaw rotation.
      pitch: Pitch rotation.
      roll: Roll rotation.
      config: If specified, sets the Config property for the location.

    Note:
      Requires that the robot be attached.
    """

    cmd = (
      f"moveC {profile_index} "
      f"{cartesian_coords.location.x} "
      f"{cartesian_coords.location.y} "
      f"{cartesian_coords.location.z} "
      f"{cartesian_coords.rotation.yaw} "
      f"{cartesian_coords.rotation.pitch} "
      f"{cartesian_coords.rotation.roll} "
    )

    if cartesian_coords.orientation is not None:
      config_int = self._convert_orientation_enum_to_int(cartesian_coords.orientation)
      config_int |= 0x1000  # GPL_Single: restrict wrist to ±180°
      cmd += f"{config_int}"

    await self.send_command(cmd)

  async def move_j(self, profile_index: int, joint_coords: PreciseFlexJointCoords) -> None:
    """Move the robot using joint coordinates, handling rail configuration."""
    if self._has_rail:
      angles_str = (
        f"{joint_coords.rail} "
        f"{joint_coords.base} "
        f"{joint_coords.shoulder} "
        f"{joint_coords.elbow} "
        f"{joint_coords.wrist} "
        f"{joint_coords.gripper}"
      )
    else:
      # Exclude rail for robots without rail
      angles_str = (
        f"{joint_coords.base} "
        f"{joint_coords.shoulder} "
        f"{joint_coords.elbow} "
        f"{joint_coords.wrist} "
        f"{joint_coords.gripper}"
      )
    await self.send_command(f"moveJ {profile_index} {angles_str}")

  async def release_brake(self, axis: int) -> None:
    """Release the axis brake.

    Overrides the normal operation of the brake. It is important that the brake not be set
    while a motion is being performed. This feature is used to lock an axis to prevent
    motion or jitter.

    Args:
      axis: The number of the axis whose brake should be released.
    """
    await self.send_command(f"releaseBrake {axis}")

  async def set_brake(self, axis: int) -> None:
    """Set the axis brake.

    Overrides the normal operation of the brake. It is important not to set a brake on an
    axis that is moving as it may damage the brake or damage the motor.

    Args:
      axis: The number of the axis whose brake should be set.
    """
    await self.send_command(f"setBrake {axis}")

  async def state(self) -> str:
    """Return state of motion.

    This value indicates the state of the currently executing or last completed robot motion.
    For additional information, please see 'Robot.TrajState' in the GPL reference manual.

    Returns:
      str: The current motion state.
    """
    return await self.send_command("state")

  async def wait_for_eom(self) -> None:
    """Wait for the robot to reach the end of the current motion.

    Waits for the robot to reach the end of the current motion or until it is stopped by
    some other means. Does not reply until the robot has stopped.
    """
    await self.send_command("waitForEom")
    await asyncio.sleep(0.2)  # Small delay to ensure command is fully processed

  async def zero_torque(self, enable: bool, axis_mask: int = 1) -> None:
    """Sets or clears zero torque mode for the selected robot.

    Individual axes may be placed into zero torque mode while the remaining axes are servoing.

    Args:
      enable: If True, enable torque mode for axes specified by axis_mask.
            If False, disable torque mode for the entire robot.
      axis_mask: The bit mask specifying the axes to be placed in torque mode when enable is True.
              The mask is computed by OR'ing the axis bits:
              1 = axis 1, 2 = axis 2, 4 = axis 3, 8 = axis 4, etc.
              Ignored when enable is False.
    """

    if enable:
      assert axis_mask > 0, "axis_mask must be greater than 0"
      await self.send_command(f"zeroTorque 1 {axis_mask}")
    else:
      await self.send_command("zeroTorque 0")

  # region PAROBOT COMMANDS

  async def change_config(self, grip_mode: int = 0) -> None:
    """Change Robot configuration from Righty to Lefty or vice versa using customizable locations.

    Uses customizable locations to avoid hitting robot during change.
    Does not include checks for collision inside work volume of the robot.
    Can be customized by user for their work cell configuration.

    Args:
      grip_mode: Gripper control mode.
      0 = do not change gripper (default)
      1 = open gripper
      2 = close gripper
    """
    await self.send_command(f"ChangeConfig {grip_mode}")

  async def change_config2(self, grip_mode: int = 0) -> None:
    """Change Robot configuration from Righty to Lefty or vice versa using algorithm.

    Uses an algorithm to avoid hitting robot during change.
    Does not include checks for collision inside work volume of the robot.
    Can be customized by user for their work cell configuration.

    Args:
      grip_mode: Gripper control mode.
      0 = do not change gripper (default)
      1 = open gripper
      2 = close gripper
    """
    await self.send_command(f"ChangeConfig2 {grip_mode}")

  async def get_grasp_data(self) -> tuple[float, float, float]:
    """Get the data to be used for the next force-controlled PickPlate command grip operation.

    Returns:
      A tuple containing (plate_width_mm, finger_speed_percent, grasp_force_newtons)
    """
    data = await self.send_command("GraspData")
    parts = data.split()

    if len(parts) != 3:
      raise PreciseFlexError(-1, "Unexpected response format from GraspData command.")

    plate_width = float(parts[0])
    finger_speed = float(parts[1])
    grasp_force = float(parts[2])

    return (plate_width, finger_speed, grasp_force)

  async def set_grasp_data(
    self, plate_width_mm: float, finger_speed_percent: float, grasp_force_newtons: float
  ) -> None:
    """Set the data to be used for the next force-controlled PickPlate command grip operation.

    This data remains in effect until the next GraspData command or the system is restarted.

    Args:
      plate_width_mm: The plate width in mm.
      finger_speed_percent: The finger speed during grasp where 100 means 100%.
      grasp_force_newtons: The gripper squeezing force, in Newtons.
      A positive value indicates the fingers must close to grasp.
      A negative value indicates the fingers must open to grasp.
    """
    await self.send_command(
      f"GraspData {plate_width_mm} {finger_speed_percent} {grasp_force_newtons}"
    )

  async def get_grip_close_pos(self) -> float:
    """Get the gripper close position for the servoed gripper.

    Returns:
      float: The current gripper close position.
    """
    data = await self.send_command("GripClosePos")
    return float(data)

  async def set_grip_close_pos(self, close_position: float) -> None:
    """Set the gripper close position for the servoed gripper.

    The close position may be changed by a force-controlled grip operation.

    Args:
      close_position: The new gripper close position.
    """
    await self.send_command(f"GripClosePos {close_position}")

  async def get_grip_open_pos(self) -> float:
    """Get the gripper open position for the servoed gripper.

    Returns:
      float: The current gripper open position.
    """
    data = await self.send_command("GripOpenPos")
    return float(data)

  async def set_grip_open_pos(self, open_position: float) -> None:
    """Set the gripper open position for the servoed gripper.

    Args:
      open_position: The new gripper open position.
    """
    await self.send_command(f"GripOpenPos {open_position}")

  async def gripper(self, grip_mode: int) -> None:
    """Opens or closes the servoed or digital-output-controlled gripper.

    Args:
      grip_mode: Grip mode.
      1 = Open gripper
      2 = Close gripper
    """
    if grip_mode not in [1, 2]:
      raise ValueError("Grip mode must be 1 (open) or 2 (close)")
    await self.send_command(f"Gripper {grip_mode}")

  async def move_rail(
    self, station_id: Optional[int] = None, mode: int = 0, rail_destination: Optional[float] = None
  ) -> None:
    """Moves the optional linear rail.

    The rail may be moved immediately or simultaneously with the next pick or place motion.
    The location may be associated with the station or specified explicitly.

    Args:
      station_id: The destination station ID. Only used if rail_destination is omitted.
      mode: Mode of operation.
      0 or omitted = cancel any pending MoveRail
      1 = Move rail immediately
      2 = Move rail during next pick or place
      rail_destination (float, optional): If specified, use this value as the rail destination
      rather than the station location.
    """
    if rail_destination is not None:
      await self.send_command(f"MoveRail {station_id or ''} {mode} {rail_destination}")
    elif station_id is not None:
      await self.send_command(f"MoveRail {station_id} {mode}")
    else:
      await self.send_command(f"MoveRail {mode}")

  async def get_pallet_index(self, station_id: int) -> tuple[int, int, int, int]:
    """Get the current pallet index values for the specified station.

    Args:
      station_id: Station ID, from 1 to N_LOC.

    Returns:
      A tuple containing (station_id, pallet_index_x, pallet_index_y, pallet_index_z)
    """
    data = await self.send_command(f"PalletIndex {station_id}")
    parts = data.split()

    if len(parts) != 4:
      raise PreciseFlexError(-1, "Unexpected response format from PalletIndex command.")

    station_id = int(parts[0])
    pallet_index_x = int(parts[1])
    pallet_index_y = int(parts[2])
    pallet_index_z = int(parts[3])

    return (station_id, pallet_index_x, pallet_index_y, pallet_index_z)

  async def set_pallet_index(
    self, station_id: int, pallet_index_x: int = 0, pallet_index_y: int = 0, pallet_index_z: int = 0
  ) -> None:
    """Set the pallet index value from 1 to n of the station used by subsequent pick or place.

    If an index argument is 0 or omitted, the corresponding index is not changed.
    Negative values generate an error.

    Args:
      station_id: Station ID, from 1 to N_LOC.
      pallet_index_x: Pallet index X. If 0 or omitted, X index is not changed.
      pallet_index_y: Pallet index Y. If 0 or omitted, Y index is not changed.
      pallet_index_z: Pallet index Z. If 0 or omitted, Z index is not changed.

    Raises:
      ValueError: If any index value is negative.
    """
    if pallet_index_x < 0:
      raise ValueError("Pallet index X cannot be negative")
    if pallet_index_y < 0:
      raise ValueError("Pallet index Y cannot be negative")
    if pallet_index_z < 0:
      raise ValueError("Pallet index Z cannot be negative")

    await self.send_command(
      f"PalletIndex {station_id} {pallet_index_x} {pallet_index_y} {pallet_index_z}"
    )

  async def get_pallet_origin(
    self, station_id: int
  ) -> tuple[int, float, float, float, float, float, float, int]:
    """Get the current pallet origin data for the specified station.

    Args:
      station_id: Station ID, from 1 to N_LOC.

    Returns:
      A tuple containing (station_id, x, y, z, yaw, pitch, roll, config)
    """
    data = await self.send_command(f"PalletOrigin {station_id}")
    parts = data.split()

    if len(parts) != 8:
      raise PreciseFlexError(-1, "Unexpected response format from PalletOrigin command.")

    station_id = int(parts[0])
    x = float(parts[1])
    y = float(parts[2])
    z = float(parts[3])
    yaw = float(parts[4])
    pitch = float(parts[5])
    roll = float(parts[6])
    config = int(parts[7])

    return (station_id, x, y, z, yaw, pitch, roll, config)

  async def set_pallet_origin(
    self,
    station_id: int,
    x: float,
    y: float,
    z: float,
    yaw: float,
    pitch: float,
    roll: float,
    config: Optional[int] = None,
  ) -> None:
    """Define the origin of a pallet reference frame.

    Specifies the world location and orientation of the (1,1,1) pallet position.
    Must be followed by a PalletX command.

    The orientation and configuration specified here determines the world orientation
    of the robot during all pick or place operations using this pallet.

    Args:
      station_id: Station ID, from 1 to N_LOC.
      x: World location X coordinate.
      y: World location Y coordinate.
      z: World location Z coordinate.
      yaw: Yaw rotation.
      pitch: Pitch rotation.
      roll: Roll rotation.
      config: The configuration flags for this location.
    """
    if config is None:
      await self.send_command(f"PalletOrigin {station_id} {x} {y} {z} {yaw} {pitch} {roll}")
    else:
      await self.send_command(
        f"PalletOrigin {station_id} {x} {y} {z} {yaw} {pitch} {roll} {config}"
      )

  async def get_pallet_x(self, station_id: int) -> tuple[int, int, float, float, float]:
    """Get the current pallet X data for the specified station.

    Args:
      station_id: Station ID, from 1 to N_LOC.

    Returns:
      A tuple containing (station_id, x_position_count, world_x, world_y, world_z)
    """
    data = await self.send_command(f"PalletX {station_id}")
    parts = data.split()

    if len(parts) != 5:
      raise PreciseFlexError(-1, "Unexpected response format from PalletX command.")

    station_id = int(parts[0])
    x_position_count = int(parts[1])
    world_x = float(parts[2])
    world_y = float(parts[3])
    world_z = float(parts[4])

    return (station_id, x_position_count, world_x, world_y, world_z)

  async def set_pallet_x(
    self, station_id: int, x_position_count: int, world_x: float, world_y: float, world_z: float
  ) -> None:
    """Define the last point on the pallet X axis.

    Specifies the world location of the (n,1,1) pallet position, where n is the x_position_count value.
    Must follow a PalletOrigin command.

    Args:
      station_id: Station ID, from 1 to N_LOC.
      x_position_count: X position count.
      world_x: World location X coordinate.
      world_y: World location Y coordinate.
      world_z: World location Z coordinate.
    """
    await self.send_command(
      f"PalletX {station_id} {x_position_count} {world_x} {world_y} {world_z}"
    )

  async def get_pallet_y(self, station_id: int) -> tuple[int, int, float, float, float]:
    """Get the current pallet Y data for the specified station.

    Args:
      station_id: Station ID, from 1 to N_LOC.

    Returns:
      A tuple containing (station_id, y_position_count, world_x, world_y, world_z)
    """
    data = await self.send_command(f"PalletY {station_id}")
    parts = data.split()

    if len(parts) != 5:
      raise PreciseFlexError(-1, "Unexpected response format from PalletY command.")

    station_id = int(parts[0])
    y_position_count = int(parts[1])
    world_x = float(parts[2])
    world_y = float(parts[3])
    world_z = float(parts[4])

    return (station_id, y_position_count, world_x, world_y, world_z)

  async def set_pallet_y(
    self, station_id: int, y_position_count: int, world_x: float, world_y: float, world_z: float
  ) -> None:
    """Define the last point on the pallet Y axis.

    Specifies the world location of the (1,n,1) pallet position, where n is the y_position_count value.
    If this command is executed, a 2 or 3-dimensional pallet is assumed.
    Must follow a PalletX command.

    Args:
      station_id: Station ID, from 1 to N_LOC.
      y_position_count: Y position count.
      world_x: World location X coordinate.
      world_y: World location Y coordinate.
      world_z: World location Z coordinate.
    """
    await self.send_command(
      f"PalletY {station_id} {y_position_count} {world_x} {world_y} {world_z}"
    )

  async def get_pallet_z(self, station_id: int) -> tuple[int, int, float, float, float]:
    """Get the current pallet Z data for the specified station.

    Args:
      station_id: Station ID, from 1 to N_LOC.

    Returns:
      A tuple containing (station_id, z_position_count, world_x, world_y, world_z)
    """
    data = await self.send_command(f"PalletZ {station_id}")
    parts = data.split()

    if len(parts) != 5:
      raise PreciseFlexError(-1, "Unexpected response format from PalletZ command.")

    station_id = int(parts[0])
    z_position_count = int(parts[1])
    world_x = float(parts[2])
    world_y = float(parts[3])
    world_z = float(parts[4])

    return (station_id, z_position_count, world_x, world_y, world_z)

  async def set_pallet_z(
    self, station_id: int, z_position_count: int, world_x: float, world_y: float, world_z: float
  ) -> None:
    """Define the last point on the pallet Z axis.

    Specifies the world location of the (1,1,n) pallet position, where n is the z_position_count value.
    If this command is executed, a 3-dimensional pallet is assumed.
    Must follow a PalletX and PalletY command.

    Args:
      station_id: Station ID, from 1 to N_LOC.
      z_position_count: Z position count.
      world_x: World location X coordinate.
      world_y: World location Y coordinate.
      world_z: World location Z coordinate.
    """
    await self.send_command(
      f"PalletZ {station_id} {z_position_count} {world_x} {world_y} {world_z}"
    )

  async def pick_plate_station(
    self,
    station_id: int,
    horizontal_compliance: bool = False,
    horizontal_compliance_torque: int = 0,
  ) -> bool:
    """Moves to a predefined position or pallet location and picks up plate.

    If the arm must change configuration, it automatically goes through the Park position.
    At the conclusion of this routine, the arm is left gripping the plate and stopped at the nest approach position.
    Use Teach function to teach station pick point.

    Args:
      station_id: Station ID, from 1 to Max.
      horizontal_compliance: If True, enable horizontal compliance while closing the gripper to allow centering around the plate.
      horizontal_compliance_torque: The % of the original horizontal holding torque to be retained during compliance. If omitted, 0 is used.

    Returns:
      bool: True if the plate was successfully grasped or force control was not used.
        False if the force-controlled gripper detected no plate present.
    """
    horizontal_compliance_int = 1 if horizontal_compliance else 0
    ret_code = await self.send_command(
      f"PickPlate {station_id} {horizontal_compliance_int} {horizontal_compliance_torque}"
    )
    return ret_code != "0"

  async def place_plate_station(
    self,
    station_id: int,
    horizontal_compliance: bool = False,
    horizontal_compliance_torque: int = 0,
  ) -> None:
    """Moves to a predefined position or pallet location and places a plate.

    If the arm must change configuration, it automatically goes through the Park position.
    At the conclusion of this routine, the arm is left gripping the plate and stopped at the nest approach position.
    Use Teach function to teach station place point.

    Args:
    station_id: Station ID, from 1 to Max.
    horizontal_compliance: If True, enable horizontal compliance during the move to place the plate, to allow centering in the fixture.
    horizontal_compliance_torque: The % of the original horizontal holding torque to be retained during compliance. If omitted, 0 is used.
    """
    horizontal_compliance_int = 1 if horizontal_compliance else 0
    await self.send_command(
      f"PlacePlate {station_id} {horizontal_compliance_int} {horizontal_compliance_torque}"
    )

  async def get_rail_position(self, station_id: int) -> float:
    """Get the position of the optional rail axis that is associated with a station.

    Args:
    station_id: Station ID, from 1 to Max.

    Returns:
    float: The current rail position for the specified station.
    """
    data = await self.send_command(f"Rail {station_id}")
    return float(data)

  async def set_rail_position(self, station_id: int, rail_position: float) -> None:
    """Set the position of the optional rail axis that is associated with a station.

    The station rail data is loaded and saved by the LoadFile and StoreFile commands.

    Args:
    station_id: Station ID, from 1 to Max.
    rail_position: The new rail position.
    """
    await self.send_command(f"Rail {station_id} {rail_position}")

  async def teach_plate_station(self, station_id: int, z_clearance: float = 50.0) -> None:
    """Sets the plate location to the current robot position and configuration.

    The location is saved as Cartesian coordinates. Z clearance must be high enough to withdraw the gripper.
    If this station is a pallet, the pallet indices must be set to 1, 1, 1. The pallet frame is not changed,
    only the location relative to the pallet.

    Args:
    station_id: Station ID, from 1 to Max.
    z_clearance: The Z Clearance value. If omitted, a value of 50 is used. If specified and non-zero, this value is used.
    """
    await self.send_command(f"TeachPlate {station_id} {z_clearance}")

  async def get_station_type(self, station_id: int) -> tuple[int, int, int, float, float, float]:
    """Get the station configuration for the specified station ID.

    Args:
      station_id: Station ID, from 1 to Max.

    Returns:
      A tuple containing (station_id, access_type, location_type, z_clearance, z_above, z_grasp_offset)
      - station_id: The station ID
      - access_type: 0 = horizontal, 1 = vertical
      - location_type: 0 = normal single, 1 = pallet (1D, 2D, 3D)
      - z_clearance: ZClearance value in mm
      - z_above: ZAbove value in mm
      - z_grasp_offset: ZGrasp offset
    """
    data = await self.send_command(f"StationType {station_id}")
    parts = data.split()

    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format from StationType command.")

    station_id = int(parts[0])
    access_type = int(parts[1])
    location_type = int(parts[2])
    z_clearance = float(parts[3])
    z_above = float(parts[4])
    z_grasp_offset = float(parts[5])

    return (station_id, access_type, location_type, z_clearance, z_above, z_grasp_offset)

  async def set_station_type(
    self,
    station_id: int,
    access_type: int,
    location_type: int,
    z_clearance: float,
    z_above: float,
    z_grasp_offset: float,
  ) -> None:
    """Set the station configuration for the specified station ID.

    Args:
      station_id: Station ID, from 1 to Max.
      access_type: The station access type.
      0 = horizontal (for "hotel" carriers accessed by horizontal move)
      1 = vertical (for stacks or tube racks accessed with vertical motion)
      location_type: The location type.
      0 = normal single location
      1 = pallet (1D, 2D, or 3D regular arrays requiring column, row, and layer index)
      z_clearance: ZClearance value in mm. The horizontal or vertical distance
      from the final location used when approaching or departing from a station.
      z_above: ZAbove value in mm. The vertical offset used with horizontal
      access when approaching or departing from the location.
      z_grasp_offset: ZGrasp offset. Added to ZClearance when an object is
      being held to compensate for the part in the gripper.

    Raises:
      ValueError: If access_type or location_type are not valid values.
    """
    if access_type not in [0, 1]:
      raise ValueError("Access type must be 0 (horizontal) or 1 (vertical)")

    if location_type not in [0, 1]:
      raise ValueError("Location type must be 0 (normal single) or 1 (pallet)")

    await self.send_command(
      f"StationType {station_id} {access_type} {location_type} {z_clearance} {z_above} {z_grasp_offset}"
    )

  # region SSGRIP COMMANDS

  async def home_all_if_no_plate(self) -> int:
    """Tests if the gripper is holding a plate. If not, enable robot power and home all robots.

    Returns:
      -1 if no plate detected and the command succeeded, 0 if a plate was detected.
    """
    response = await self.send_command("HomeAll_IfNoPlate")
    return int(response)

  async def grasp_plate(
    self, plate_width_mm: float, finger_speed_percent: int, grasp_force_newtons: float
  ) -> int:
    """Grasps a plate with limited force.

    A plate can be grasped by opening or closing the gripper. The actual commanded gripper
    width generated by this function is a few mm smaller (or larger) than plate_width_mm
    to permit the servos PID loop to generate the gripping force.

    Args:
      plate_width_mm: Plate width in mm. Should be accurate to within about 1 mm.
      finger_speed_percent: Percent speed to close fingers. 1 to 100.
      grasp_force_newtons: Maximum gripper squeeze force in Newtons.
        A positive value indicates the fingers must close to grasp.
        A negative value indicates the fingers must open to grasp.

    Returns:
      -1 if the plate has been grasped, 0 if the final gripping force indicates no plate.

    Raises:
      ValueError: If finger_speed_percent is not between 1 and 100.
    """
    if not (1 <= finger_speed_percent <= 100):
      raise ValueError("Finger speed percent must be between 1 and 100")

    response = await self.send_command(
      f"GraspPlate {plate_width_mm} {finger_speed_percent} {grasp_force_newtons}"
    )
    return int(response)

  async def release_plate(
    self, open_width_mm: float, finger_speed_percent: int, in_range: float = 0.0
  ) -> None:
    """Releases the plate after a GraspPlate command.

    Opens (or closes) the gripper to the specified width and cancels the force limit
    once the plate is released to avoid applying an excessive force to the plate.

    Args:
      open_width_mm: Open width in mm.
      finger_speed_percent: Percent speed to open fingers. 1 to 100.
      in_range: Optional. The standard InRange profile property for the gripper open move.
        If omitted, a zero value is assumed.

    Raises:
      ValueError: If finger_speed_percent is not between 1 and 100.
    """
    if not (1 <= finger_speed_percent <= 100):
      raise ValueError("Finger speed percent must be between 1 and 100")

    await self.send_command(f"ReleasePlate {open_width_mm} {finger_speed_percent} {in_range}")

  async def is_fully_closed(self) -> int:
    """Tests if the gripper is fully closed by checking the end-of-travel sensor.

    Returns:
      For standard gripper: -1 if the gripper is within 2mm of fully closed, otherwise 0.
          For dual gripper: A bitmask of the closed state of each gripper where gripper 1 is bit 0
          and gripper 2 is bit 1. A bit being set to 1 represents the corresponding gripper being closed.
    """
    response = await self.send_command("IsFullyClosed")
    return int(response)

  async def set_active_gripper(
    self, gripper_id: int, spin_mode: int = 0, profile_index: Optional[int] = None
  ) -> None:
    """(Dual Gripper Only) Sets the currently active gripper and modifies the tool reference frame.

    Args:
      gripper_id: Gripper ID, either 1 or 2. Determines which gripper is set to active.
      spin_mode: Optional spin mode.
        0 or omitted = do not rotate the gripper 180deg immediately.
        1 = Rotate gripper 180deg immediately.
      profile_index: Profile Index to use for spin motion.

    Raises:
      ValueError: If gripper_id is not 1 or 2, or if spin_mode is not 0 or 1.
    """
    if gripper_id not in [1, 2]:
      raise ValueError("Gripper ID must be 1 or 2")

    if spin_mode not in [0, 1]:
      raise ValueError("Spin mode must be 0 or 1")

    if profile_index is not None:
      await self.send_command(f"SetActiveGripper {gripper_id} {spin_mode} {profile_index}")
    else:
      await self.send_command(f"SetActiveGripper {gripper_id} {spin_mode}")

  async def get_active_gripper(self) -> int:
    """(Dual Gripper Only) Returns the currently active gripper.

    Returns:
      1 if Gripper A is active, 2 if Gripper B is active.
    """
    response = await self.send_command("GetActiveGripper")
    return int(response)

  async def free_mode(self, on: bool, axis: int = 0):
    """
    Activates or deactivates free mode.  The robot must be attached to enter free mode.

    Args:
      on: If True, enable free mode. If False, disable free mode for all axes.
      axis: Axis to apply free mode to. 0 = all axes or > 0 = Free just this axis. Ignored if 'on' parameter is False.
    """
    if not on:
      axis = -1  # means turn off free mode for all axes
    await self.send_command(f"freemode {axis}")

  async def pick_plate_from_stored_position(
    self,
    position_id: int,
    horizontal_compliance: bool = False,
    horizontal_compliance_torque: int = 0,
  ):
    """Pick an item at the specified position ID.

    Args:
      position_id: The ID of the position where the plate should be picked.
      horizontal_compliance: enable horizontal compliance while closing the gripper to allow centering around the plate.
      horizontal_compliance_torque: The % of the original horizontal holding torque to be retained during compliance. If omitted, 0 is used.
    """
    horizontal_compliance_int = 1 if horizontal_compliance else 0
    ret_code = await self.send_command(
      f"pickplate {position_id} {horizontal_compliance_int} {horizontal_compliance_torque}"
    )
    if ret_code == "0":
      raise PreciseFlexError(-1, "the force-controlled gripper detected no plate present.")

  async def place_plate_to_stored_position(
    self,
    position_id: int,
    horizontal_compliance: bool = False,
    horizontal_compliance_torque: int = 0,
  ):
    """Place an item at the specified position ID.

    Args:
      position_id: The ID of the position where the plate should be placed.
      horizontal_compliance: enable horizontal compliance during the move to place the plate, to allow centering in the fixture.
      horizontal_compliance_torque: The % of the original horizontal holding torque to be retained during compliance.  If omitted, 0 is used.
    """
    horizontal_compliance_int = 1 if horizontal_compliance else 0
    await self.send_command(
      f"placeplate {position_id} {horizontal_compliance_int} {horizontal_compliance_torque}"
    )

  async def teach_position(self, position_id: int, z_clearance: float = 50.0):
    """Sets the plate location to the current robot position and configuration.  The location is saved as Cartesian coordinates.

    Args:
      position_id: The ID of the position to be taught.
      z_clearance: Optional.  The Z Clearance value. If omitted, a value of 50 is used.  Z clearance must be high enough to withdraw the gripper.
    """
    await self.send_command(f"teachplate {position_id} {z_clearance}")

  def _parse_xyz_response(
    self, parts: List[str]
  ) -> tuple[float, float, float, float, float, float]:
    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format for Cartesian coordinates.")

    x = float(parts[0])
    y = float(parts[1])
    z = float(parts[2])
    yaw = float(parts[3])
    pitch = float(parts[4])
    roll = float(parts[5])

    return (x, y, z, yaw, pitch, roll)

  def _parse_angles_response(
    self, parts: List[str]
  ) -> tuple[float, float, float, float, float, float]:
    """
    For self._has_rail=True:  [rail, base, shoulder, elbow, wrist, gripper]
    For self._has_rail=False: [base, shoulder, elbow, wrist, gripper, 0.0(padding)]
    """

    if len(parts) < 3:
      raise PreciseFlexError(-1, "Unexpected response format for angles.")

    if self._has_rail:
      return (
        float(parts[0]),
        float(parts[1]),
        float(parts[2]),
        float(parts[3]) if len(parts) > 3 else 0.0,
        float(parts[4]) if len(parts) > 4 else 0.0,
        float(parts[5]) if len(parts) > 5 else 0.0,
      )

    return (
      0.0,
      float(parts[0]),
      float(parts[1]),
      float(parts[2]) if len(parts) > 2 else 0.0,
      float(parts[3]) if len(parts) > 3 else 0.0,
      float(parts[4]) if len(parts) > 4 else 0.0,
    )
