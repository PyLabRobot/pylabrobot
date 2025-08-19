from pylabrobot.arms.backend import ArmBackend
from pylabrobot.io.tcp import TCP

class PreciseFlexError(Exception):
  def __init__(self, replycode: int, message: str):
    self.replycode = replycode
    super().__init__(f"PreciseFlexError {replycode}: {message}")

class PreciseFlexBackend(ArmBackend):
  """UNTESTED - Backend for the PreciseFlex robotic arm"""
  def __init__(self, host: str, port: int = 10100, timeout=20, profile=1) -> None:
    super().__init__()
    self.host = host
    self.port = port
    self.timeout = timeout
    self.profile = profile
    self.io = TCP(host=self.host, port=self.port)

  async def setup(self):
    """Initialize the PreciseFlex backend."""
    await self.io.setup()
    await self.power_on_robot()
    await self.attach()

  async def stop(self):
    """Stop the PreciseFlex backend."""
    await self.detach()
    await self.power_off_robot()
    await self.exit()
    await self.io.stop()

  async def power_on_robot(self):
    """Power on the robot."""
    await self.send_command(f"hp 1 {self.timeout}")

  async def power_off_robot(self):
    """Power off the robot."""
    await self.send_command("hp 0")

  async def move_to(self, position: tuple[float, float, float]):
    x, y, z = position
    yaw, pitch, roll = 0.0, 0.0, 0.0  # Assuming no rotation for PF400
    await self.send_command(f"movec {x} {y} {z} {yaw} {pitch} {roll}")

  async def get_position(self) -> tuple[float, float, float]:
    data = await self.send_command("wherec")
    x, y, z, yaw, pitch, roll, config = data.split(" ")
    return float(x), float(y), float(z)

  async def set_speed(self, speed: float):
    """Set the speed of the arm's movement."""
    if not (0 <= speed <= 100):
      raise ValueError("Speed must be between 0 and 100.")
    await self.send_command(f"speed {self.profile} {speed}")

  async def get_speed(self) -> float:
    """Get the current speed of the arm's movement."""
    data = await self.send_command(f"speed {self.profile}")
    return float(data)

  def set_profile(self, profile: int):
    """Set the motion profile index."""
    if not isinstance(profile, int) or profile < 0:
      raise ValueError("Profile index must be a non-negative integer.")
    self.profile = profile

  def get_profile(self) -> int:
    """Get the current motion profile index."""
    return self.profile

  async def set_profile_values(self,
                               speed: float,
                               speed2: float,
                               acceleration: float,
                               deceleration: float,
                               acceleration_ramp: float,
                               deceleration_ramp: float,
                               in_range: float,
                               straight: bool):
    """
    Set motion profile values for the specified profile index on the PreciseFlex robot.

    Parameters:
      profile (int): Profile index to set values for.
      speed (float): Percentage of maximum speed. 100 = full speed. Values >100 may be accepted depending on system config.
      speed2 (float): Secondary speed setting, typically for Cartesian moves. Normally 0. Interpreted as a percentage.
      acceleration (float): Percentage of maximum acceleration. 100 = full accel.
      deceleration (float): Percentage of maximum deceleration. 100 = full decel.
      acceleration_ramp (float): Acceleration ramp time in seconds.
      deceleration_ramp (float): Deceleration ramp time in seconds.
      in_range (float): InRange value, from -1 to 100. -1 = allow blending, 0 = stop without checking, >0 = enforce position accuracy.
      straight (bool): If True, follow a straight-line path (-1). If False, follow a joint-based path (0).
    """
    if not (0 <= speed):
      raise ValueError("Speed must be >= 0 (percent).")

    if not (0 <= speed2):
      raise ValueError("Speed2 must be >= 0 (percent).")

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
    await self.send_command(f"Profile {self.profile} {speed} {speed2} {acceleration} {deceleration} {acceleration_ramp} {deceleration_ramp} {in_range} {straight_int}")

  async def free_mode(self, on: bool, axis: int = 0):
    """
    Activates or deactivates free mode.  The robot must be attached to enter free mode.

    Parameters:
      on (bool): If True, enable free mode. If False, disable free mode for all axes.
      axis (int): Axis to apply free mode to. 0 = all axes or > 0 = Free just this axis. Ignored if 'on' parameter is False.
    """
    if not on:
      axis = -1 # means turn off free mode for all axes
    await self.send_command(f"freemode {axis}")

  async def get_profile_values(self) -> dict:
    """
    Get the current motion profile values for the specified profile index on the PreciseFlex robot.

    Returns:
      dict: A dictionary containing the profile values.
    """
    data = await self.send_command(f"Profile {self.profile}")
    parts = data.split(" ")
    if len(parts) != 8:
      raise PreciseFlexError(-1, "Unexpected response format from device.")

    return {
      "speed": float(parts[0]),
      "speed2": float(parts[1]),
      "acceleration": float(parts[2]),
      "deceleration": float(parts[3]),
      "acceleration_ramp": float(parts[4]),
      "deceleration_ramp": float(parts[5]),
      "in_range": float(parts[6]),
      "straight": parts[7] == "-1"
    }

  async def halt(self):
    """Halt the robot immediately."""
    await self.send_command("halt")

  async def attach(self):
    """Attach the robot."""
    await self.send_command("attach 1")

  async def detach(self):
    """Detach the robot."""
    await self.send_command("detach 1")

  async def open_gripper(self):
    """Open the gripper."""
    await self.send_command("gripper 1")

  async def close_gripper(self):
    """Close the gripper."""
    await self.send_command("gripper 0")

  async def pick_plate(self,
                 position_id: int,
                 horizontal_compliance: bool = False,
                 horizontal_compliance_torque: int = 0):
    """Pick an item at the specified position ID.

    Parameters:
      position_id (int): The ID of the position where the plate should be picked.
      horizontal_compliance (bool): enable horizontal compliance while closing the gripper to allow centering around the plate.
      horizontal_compliance_torque (int): The % of the original horizontal holding torque to be retained during compliance. If omitted, 0 is used.
    """
    horizontal_compliance_int = 1 if horizontal_compliance else 0
    ret_code = await self.send_command(f"pickplate {position_id} {horizontal_compliance_int} {horizontal_compliance_torque}")
    if ret_code == "0":
      raise PreciseFlexError(-1, "the force-controlled gripper detected no plate present.")

  async def place_plate(self,
                        position_id: int,
                        horizontal_compliance: bool = False,
                        horizontal_compliance_torque: int = 0):
    """Place an item at the specified position ID.

    Parameters:
      position_id (int): The ID of the position where the plate should be placed.
      horizontal_compliance (bool): enable horizontal compliance during the move to place the plate, to allow centering in the fixture.
      horizontal_compliance_torque (int): The % of the original horizontal holding torque to be retained during compliance.  If omitted, 0 is used.
    """
    horizontal_compliance_int = 1 if horizontal_compliance else 0
    await self.send_command(f"placeplate {position_id} {horizontal_compliance_int} {horizontal_compliance_torque}")

  async def teach_position(self, position_id: int, z_clearance: float = 50.0):
    """ Sets the plate location to the current robot position and configuration.  The location is saved as Cartesian coordinates.

    Parameters:
      position_id (int): The ID of the position to be taught.
      z_clearance (float): Optional.  The Z Clearance value. If omitted, a value of 50 is used.  Z clearance must be high enough to withdraw the gripper.
    """
    await self.send_command(f"teachplate {position_id} {z_clearance}")

  async def exit(self):
    """Closes the communications link immediately."""
    await self.send_command("exit")

  async def home(self):
    """Homes robot."""
    await self.send_command("home")

  async def send_command(self, command: str):
    await self.io.write(command.encode('utf-8') + b'\n')
    reply = await self.io.readline()
    return self._parse_reply_ensure_successful(reply)

  def _parse_reply_ensure_successful(self, reply: bytes) -> str:
    """Parse reply from Precise Flex.

    Expected format: b'replycode data message\r\n'
    - replycode is an integer at the beginning
    - data is rest of the line (excluding CRLF)
    """
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