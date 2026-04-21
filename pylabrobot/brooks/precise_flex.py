import asyncio
import dataclasses
import logging
import warnings
from abc import ABC
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Literal, Optional

from pylabrobot.brooks.error_codes import ERROR_CODES
from pylabrobot.brooks import kinematics
from pylabrobot.capabilities.arms.backend import (
  CanFreedrive,
  HasJoints,
  OrientableGripperArmBackend,
)
from pylabrobot.capabilities.arms.orientable_arm import OrientableArm
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Device, Driver
from pylabrobot.io.socket import Socket
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


ElbowOrientation = Literal["right", "left"]
Wrist = Literal["cw", "ccw"]


class PFAxis(IntEnum):
  BASE = 1
  SHOULDER = 2
  ELBOW = 3
  WRIST = 4
  GRIPPER = 5
  RAIL = 6


@dataclass
class PreciseFlexGripperLocation(GripperLocation):
  rail: Optional[float] = None
  orientation: Optional[ElbowOrientation] = None
  wrist: Optional[Wrist] = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PreciseFlexError(Exception):
  def __init__(self, replycode: int, message: str):
    self.replycode = replycode
    self.message = message
    if replycode in ERROR_CODES:
      text = ERROR_CODES[replycode]["text"]
      description = ERROR_CODES[replycode]["description"]
      super().__init__(f"PreciseFlexError {replycode}: {text}. {description} - {message}")
    else:
      super().__init__(f"PreciseFlexError {replycode}: {message}")


# ---------------------------------------------------------------------------
# Driver — owns Socket I/O and device lifecycle
# ---------------------------------------------------------------------------


class PreciseFlexDriver(Driver):
  """Driver for PreciseFlex robotic arms.

  Owns the Socket I/O connection and device-level operations (power, attach,
  home, response mode).  Exposes ``send_command`` as the generic wire method.

  Documentation and error codes available at
  https://www2.brooksautomation.com/#Root/Welcome.htm
  """

  def __init__(self, host: str, port: int = 10100, timeout: int = 20) -> None:
    super().__init__()
    self.io = Socket(human_readable_device_name="Precise Flex Arm", host=host, port=port)
    self.timeout = timeout

  # -- communication ---------------------------------------------------------

  async def send_command(self, command: str) -> str:
    await self.io.write(command.encode("utf-8") + b"\n")
    reply = await self.io.readline()
    return self._parse_reply_ensure_successful(reply)

  def _parse_reply_ensure_successful(self, reply: bytes) -> str:
    """Parse reply from Precise Flex.

    Expected format: b'replycode data message\r\n'
    - replycode is an integer at the beginning
    - data is rest of the line (excluding CRLF)
    """
    text = reply.decode().strip()
    if not text:
      raise PreciseFlexError(-1, "Empty reply from device.")
    parts = text.split(" ", 1)
    if len(parts) == 1:
      replycode = int(parts[0])
      data = ""
    else:
      replycode, data = int(parts[0]), parts[1]
    if replycode != 0:
      raise PreciseFlexError(replycode, data)
    return data

  # -- lifecycle -------------------------------------------------------------

  @dataclass
  class SetupParams(BackendParams):
    """PreciseFlex-specific parameters for ``setup``.

    Args:
      skip_home: If True, skip the homing step during setup.
    """

    skip_home: bool = False

  async def setup(self, backend_params: Optional[BackendParams] = None):
    """Initialize the PreciseFlex driver.

    Opens the socket connection, sets response mode to PC, powers on the
    robot, attaches it, and (optionally) homes it.
    """
    if not isinstance(backend_params, PreciseFlexDriver.SetupParams):
      backend_params = PreciseFlexDriver.SetupParams()

    await self.io.setup()
    await self.set_response_mode("pc")
    await self.power_on_robot()
    await self.attach(1)
    if not backend_params.skip_home:
      await self.home()
    logger.info("[PreciseFlex %s] connected: port=%s", self.io._host, self.io._port)

  async def stop(self):
    """Stop the PreciseFlex driver."""
    await self.detach()
    await self.power_off_robot()
    await self.exit()
    await self.io.stop()
    logger.info("[PreciseFlex %s] disconnected: port=%s", self.io._host, self.io._port)

  # -- device-level commands -------------------------------------------------

  async def exit(self) -> None:
    """Close the communications link immediately.

    Note:
      Does not affect any robots that may be active.
    """
    await self.io.write(b"exit\n")

  ResponseMode = Literal["pc", "verbose"]

  async def request_mode(self) -> ResponseMode:
    """Get the current response mode.

    Returns:
      Current mode (0 = PC mode, 1 = verbose mode)
    """
    response = await self.send_command("mode")
    mapping: Dict[int, PreciseFlexDriver.ResponseMode] = {0: "pc", 1: "verbose"}
    return mapping[int(response)]

  async def set_response_mode(self, mode: ResponseMode) -> None:
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
    mapping = {"pc": 0, "verbose": 1}
    await self.send_command(f"mode {mapping[mode]}")

  async def power_on_robot(self):
    """Power on the robot."""
    error: Optional[PreciseFlexError] = None
    for _ in range(3):
      try:
        await self.set_power(True, self.timeout)
      except PreciseFlexError as e:
        logger.warning(f"Error powering on robot, retrying... Attempt {_ + 1}/3. Error: {e}")
        error = e
      else:
        return

    if error:
      raise error
    raise RuntimeError("Failed to power on robot after 3 attempts for unknown reasons.")

  async def power_off_robot(self):
    """Power off the robot."""
    await self.set_power(False)

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

  async def request_power_state(self) -> int:
    """Get the current robot power state.

    Returns:
      Current power state (0 = disabled, 1 = enabled)
    """
    response = await self.send_command("hp")
    return int(response)

  async def attach(self, attach_state: Optional[int] = None) -> int:
    """Attach or release the robot, or get attachment state.

    Args:
      attach_state: If omitted, returns the attachment state.  0 = release the robot; 1 = attach the robot.

    Returns:
      If attach_state is omitted, returns 0 if robot is not attached, -1 if attached.  Otherwise returns 0 on success.

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

  async def home(self) -> None:
    """Home the robot associated with this thread.

    Note:
      Requires power to be enabled.
      Requires robot to be attached.
      Waits until the homing is complete.
    """
    await self.send_command("home")

  async def home_all(self) -> None:
    """Home all robots.

    Note:
      Requires power to be enabled.
      Requires that robots not be attached.
    """
    await self.send_command("homeAll")

  async def _wait_for_eom(self) -> None:
    """Wait for the robot to reach the end of the current motion.

    Waits for the robot to reach the end of the current motion or until it is stopped by
    some other means. Does not reply until the robot has stopped.
    """
    await self.send_command("waitForEom")
    await asyncio.sleep(0.2)

  async def state(self) -> str:
    """Return state of motion.

    This value indicates the state of the currently executing or last completed robot motion.
    For additional information, please see 'Robot.TrajState' in the GPL reference manual.

    Returns:
      str: The current motion state.
    """
    return await self.send_command("state")


# ---------------------------------------------------------------------------
# Arm Backend — protocol translation, capability methods
# ---------------------------------------------------------------------------


def _snap_to_current(
  ik_joints: Dict[int, float], current: Dict[int, float], wrist: Wrist
) -> Dict[int, float]:
  """Shift each rotary joint by 360° multiples toward `current`, then re-enforce
  the wrist-sign half on J4 so the result still matches `wrist`. Avoids
  gratuitous full-turn moves when multiple IK solutions are equivalent.
  """
  out = dict(ik_joints)
  for axis in (PFAxis.SHOULDER, PFAxis.ELBOW, PFAxis.WRIST):
    out[axis] += 360 * round((current[axis] - out[axis]) / 360)
  if wrist == "ccw" and out[PFAxis.WRIST] < 0:
    out[PFAxis.WRIST] += 360
  elif wrist == "cw" and out[PFAxis.WRIST] > 0:
    out[PFAxis.WRIST] -= 360
  return out


class PreciseFlexArmBackend(OrientableGripperArmBackend, HasJoints, CanFreedrive, ABC):
  """Backend for the PreciseFlex robotic arm.

  Default to using Cartesian coordinates; some methods in Brook's TCS
  don't work with Joint coordinates.

  Documentation and error codes available at
  https://www2.brooksautomation.com/#Root/Welcome.htm
  """

  def __init__(
    self,
    driver: PreciseFlexDriver,
    is_dual_gripper: bool = False,
    has_rail: bool = False,
    gripper_length: float = 162.0,
  ) -> None:
    """
    Args:
      gripper_length: wrist-axis → TCP distance in mm. Depends on the mounted
        gripper; 162 mm matches the stock single gripper.
    """
    super().__init__()
    self.driver = driver
    self.profile_index: int = 1
    self.location_index: int = 1
    self._rail_position_index = 1
    self.horizontal_compliance: bool = False
    self.horizontal_compliance_torque: int = 0
    self._has_rail = has_rail
    self._is_dual_gripper = is_dual_gripper
    self._kinematics_params = kinematics.PF400Params(l3=gripper_length)
    if is_dual_gripper:
      warnings.warn(
        "Dual gripper support is experimental and may not work as expected.", UserWarning
      )

  async def _on_setup(self, backend_params: Optional[BackendParams] = None) -> None:
    await super()._on_setup(backend_params=backend_params)
    await self.stop_freedrive_mode()

  async def _request_state(
    self,
  ) -> tuple[Dict[int, float], PreciseFlexGripperLocation]:
    """Single-query snapshot of joint state and the derived Cartesian pose."""
    joints = await self.request_joint_position()
    pose = kinematics.fk(joints, self._kinematics_params)
    # PF400 gripper stays level: pitch=90, roll=-180.
    pose = dataclasses.replace(pose, rotation=Rotation(x=-180, y=90, z=pose.rotation.yaw))
    return joints, pose

  async def _cart_to_joints(self, cart: PreciseFlexGripperLocation) -> Dict[int, float]:
    """Convert a Cartesian location into a full joint dict using our IK.

    Any of cart.orientation, cart.wrist, and cart.rail left as None default
    to the current pose — picks the configuration closest to where the arm
    is now. Fetches current joint state for the gripper and rail axes so
    callers can use the result directly with `_move_j` or `_set_joint_angles`.
    """
    joints, current = await self._request_state()
    cart = dataclasses.replace(
      cart,
      orientation=current.orientation if cart.orientation is None else cart.orientation,
      wrist=current.wrist if cart.wrist is None else cart.wrist,
      rail=current.rail if cart.rail is None else cart.rail,
    )
    ik_joints = _snap_to_current(kinematics.ik(cart, p=self._kinematics_params), joints, cart.wrist)
    joints[PFAxis.BASE] = ik_joints[1]
    joints[PFAxis.SHOULDER] = ik_joints[2]
    joints[PFAxis.ELBOW] = ik_joints[3]
    joints[PFAxis.WRIST] = ik_joints[4]
    joints[PFAxis.RAIL] = cart.rail
    return joints

  # -- high-level motion API -------------------------------------------------

  async def _set_speed(self, speed_percent: float):
    """Set the speed percentage of the arm's movement (0-100)."""
    await self.set_profile_speed(self.profile_index, speed_percent)

  async def _request_speed(self) -> float:
    """Get the current speed percentage of the arm's movement."""
    return await self.request_profile_speed(self.profile_index)

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ):
    """Open the gripper to the specified width."""
    logger.info("[PreciseFlex %s] open_gripper: width_mm=%s", self.driver.io._host, gripper_width)
    await self._set_grip_open_pos(gripper_width)
    await self.driver.send_command("gripper 1")

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ):
    """Close the gripper to the specified width."""
    logger.info("[PreciseFlex %s] close_gripper: width_mm=%s", self.driver.io._host, gripper_width)
    await self._set_grip_close_pos(gripper_width)
    await self.driver.send_command("gripper 2")

  async def halt(self, backend_params: Optional[BackendParams] = None):
    """Stops the current robot immediately but leaves power on."""
    await self.driver.send_command("halt")

  async def move_to_safe(self) -> None:
    """Moves the robot to Safe Position.

    Does not include checks for collision with 3rd party obstacles inside the work volume of the robot.
    """
    await self.driver.send_command("movetosafe")

  async def move_rail(self, position: float) -> None:
    """Move the rail to the specified position.

    Args:
      position: Rail destination in mm.

    Raises:
      RuntimeError: If the arm does not have a rail.
    """
    if not self._has_rail:
      raise RuntimeError("This arm does not have a rail.")
    await self._set_rail_position(self._rail_position_index, position)
    await self._move_rail(station_id=self._rail_position_index)

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Park the arm to its default safe position."""
    await self.move_to_safe()

  # -- JointArmBackend interface (joint-space) --------------------------------

  @dataclass
  class PickUpParams(BackendParams):
    """PreciseFlex arm parameters for plate pickup.

    Args:
      finger_speed_percent: Finger closing speed as a percentage (0-100). Default 50.0.
      grasp_force: Grasp force in Newtons. Default 10.0.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration. Only used for Cartesian moves.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
        Only used for Cartesian moves.
    """

    finger_speed_percent: float = 50.0
    grasp_force: float = 10.0
    orientation: Optional[ElbowOrientation] = None
    wrist: Optional[Wrist] = None
    rail_position: Optional[float] = None

  async def pick_up_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up at the specified joint position."""
    logger.info(
      "[PreciseFlex %s] pick_up: joints=%s, resource_width_mm=%s",
      self.driver.io._host,
      position,
      resource_width,
    )
    if not isinstance(backend_params, self.PickUpParams):
      backend_params = PreciseFlexArmBackend.PickUpParams()
    await self._set_grasp_data(
      plate_width=resource_width,
      finger_speed_percent=backend_params.finger_speed_percent,
      grasp_force=backend_params.grasp_force,
    )
    await self._pick_plate_j(position)

  @dataclass
  class DropParams(BackendParams):
    """PreciseFlex arm parameters for plate drop.

    Args:
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration. Only used for Cartesian moves.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
        Only used for Cartesian moves.
    """

    orientation: Optional[ElbowOrientation] = None
    wrist: Optional[Wrist] = None
    rail_position: Optional[float] = None

  async def drop_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop at the specified joint position."""
    logger.info(
      "[PreciseFlex %s] drop: joints=%s, resource_width_mm=%s",
      self.driver.io._host,
      position,
      resource_width,
    )
    if not isinstance(backend_params, self.DropParams):
      backend_params = PreciseFlexArmBackend.DropParams()
    await self._place_plate_j(position)

  @dataclass
  class MoveToJointPositionParams(BackendParams):
    """PreciseFlex arm parameters for joint-space moves.

    Args:
      speed: Movement speed override. If None, uses the current speed setting.
    """

    speed: Optional[float] = None

  async def move_to_joint_position(
    self,
    position: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move the arm to the specified joint position."""
    if not isinstance(backend_params, self.MoveToJointPositionParams):
      backend_params = PreciseFlexArmBackend.MoveToJointPositionParams()
    if backend_params.speed is not None:
      await self._set_speed(backend_params.speed)
    current = await self.request_joint_position()
    joint_coords = {**current, **position}
    await self._move_j(profile_index=self.profile_index, joint_coords=joint_coords)

  async def request_joint_position(
    self, backend_params: Optional[BackendParams] = None
  ) -> Dict[int, float]:
    """Get the current joint position of the arm."""
    await self.driver._wait_for_eom()
    num_tries = 2
    for _ in range(num_tries):
      data = await self.driver.send_command("wherej")
      parts = data.split()
      if len(parts) > 0:
        break
    else:
      raise PreciseFlexError(-1, "Unexpected response format from wherej command.")
    return self._parse_angles_response(parts)

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> PreciseFlexGripperLocation:
    """Get the current pose using our kinematics model (no firmware `wherec`)."""
    _, pose = await self._request_state()
    return pose

  # -- OrientableArmBackend interface (Cartesian) -----------------------------

  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up at the specified Cartesian location."""
    logger.info(
      "[PreciseFlex %s] pick_up: x=%s, y=%s, z=%s, direction=%s, resource_width_mm=%s",
      self.driver.io._host,
      location.x,
      location.y,
      location.z,
      direction,
      resource_width,
    )
    if not isinstance(backend_params, self.PickUpParams):
      backend_params = PreciseFlexArmBackend.PickUpParams()
    if backend_params.rail_position is not None:
      await self.move_rail(backend_params.rail_position)
    elif self._has_rail:
      raise ValueError(
        "rail_position must be specified for pick_up_at_location when using a rail-equipped arm."
      )
    coords = PreciseFlexGripperLocation(
      location=location,
      rotation=Rotation(z=direction),
      orientation=backend_params.orientation,
      wrist=backend_params.wrist,
    )
    await self._set_grasp_data(
      plate_width=resource_width,
      finger_speed_percent=backend_params.finger_speed_percent,
      grasp_force=backend_params.grasp_force,
    )
    await self._pick_plate_c(cartesian_position=coords)

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop at the specified Cartesian location."""
    logger.info(
      "[PreciseFlex %s] drop: x=%s, y=%s, z=%s, direction=%s, resource_width_mm=%s",
      self.driver.io._host,
      location.x,
      location.y,
      location.z,
      direction,
      resource_width,
    )
    if not isinstance(backend_params, self.DropParams):
      backend_params = PreciseFlexArmBackend.DropParams()
    if backend_params.rail_position is not None:
      await self.move_rail(backend_params.rail_position)
    elif self._has_rail:
      raise ValueError(
        "rail_position must be specified for drop_at_location when using a rail-equipped arm."
      )
    coords = PreciseFlexGripperLocation(
      location=location,
      rotation=Rotation(z=direction),
      orientation=backend_params.orientation,
      wrist=backend_params.wrist,
    )
    await self._place_plate_c(cartesian_position=coords)

  @dataclass
  class MoveToLocationParams(BackendParams):
    """PreciseFlex arm parameters for Cartesian-space moves.

    Args:
      speed: Movement speed override. If None, uses the current speed setting.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
    """

    speed: Optional[float] = None
    orientation: Optional[ElbowOrientation] = None
    wrist: Optional[Wrist] = None
    rail_position: Optional[float] = None

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move the arm to the specified Cartesian location."""
    if not isinstance(backend_params, self.MoveToLocationParams):
      backend_params = PreciseFlexArmBackend.MoveToLocationParams()
    if backend_params.speed is not None:
      await self._set_speed(backend_params.speed)

    if backend_params.rail_position is not None:
      await self.move_rail(backend_params.rail_position)
    elif self._has_rail:
      raise ValueError(
        "Rail position must be specified for move_to_location when using a rail-equipped arm."
      )

    coords = PreciseFlexGripperLocation(
      location=location,
      rotation=Rotation(x=-180, y=90, z=direction),
      orientation=backend_params.orientation,
      wrist=backend_params.wrist,
    )
    joints = await self._cart_to_joints(coords)
    await self._move_j(profile_index=self.profile_index, joint_coords=joints)

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    """(Single Gripper Only) Tests if the gripper is fully closed by checking the end-of-travel sensor.

    Returns:
      For standard gripper: True if the gripper is within 2mm of fully closed, otherwise False.
    """
    if self._is_dual_gripper:
      raise ValueError("IsGripperClosed command is only valid for single gripper robots.")
    response = await self.driver.send_command("IsFullyClosed")
    return int(response) == -1

  async def are_grippers_closed(self) -> tuple[bool, bool]:
    """(Dual Gripper Only) Tests if each gripper is fully closed by checking the end-of-travel sensors."""
    if not self._is_dual_gripper:
      raise ValueError("AreGrippersClosed command is only valid for dual gripper robots.")
    response = await self.driver.send_command("IsFullyClosed")
    ret_int = int(response)
    gripper_1_closed = (ret_int & 1) != 0
    gripper_2_closed = (ret_int & 2) != 0
    return (gripper_1_closed, gripper_2_closed)

  async def start_freedrive_mode(
    self, free_axes: Optional[List[int]] = None, backend_params=None
  ) -> None:
    """Enter freedrive mode, allowing manual movement of the specified joints.

    The robot must be attached to enter free mode.

    Args:
      free_axes: List of joint indices to free. Use [0] for all axes.
    """
    for axis in free_axes or [
      PFAxis.BASE,
      PFAxis.SHOULDER,
      PFAxis.ELBOW,
      PFAxis.WRIST,
      PFAxis.RAIL,
    ]:
      await self.driver.send_command(f"freemode {axis}")

  async def stop_freedrive_mode(self, backend_params=None) -> None:
    """Exit freedrive mode for all axes."""
    await self.driver.send_command("freemode -1")

  # -- internal pick/place helpers -------------------------------------------

  async def _pick_plate_j(self, joint_position: Dict[int, float]):
    """Pick a plate from the specified position using joint coordinates."""
    await self._set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail()
    horizontal_compliance_int = 1 if self.horizontal_compliance else 0
    ret_code = await self.driver.send_command(
      f"pickplate {self.location_index} {horizontal_compliance_int} {self.horizontal_compliance_torque}"
    )
    if ret_code == "0":
      raise PreciseFlexError(-1, "the force-controlled gripper detected no plate present.")

  async def _place_plate_j(self, joint_position: Dict[int, float]):
    """Place a plate at the specified position using joint coordinates."""
    await self._set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail()
    horizontal_compliance_int = 1 if self.horizontal_compliance else 0
    await self.driver.send_command(
      f"placeplate {self.location_index} {horizontal_compliance_int} {self.horizontal_compliance_torque}"
    )

  async def _pick_plate_c(self, cartesian_position: PreciseFlexGripperLocation):
    """Pick a plate at a Cartesian position via IK + joint-space pickplate."""
    joints = await self._cart_to_joints(cartesian_position)
    await self._pick_plate_j(joints)

  async def _place_plate_c(self, cartesian_position: PreciseFlexGripperLocation):
    """Place a plate at a Cartesian position via IK + joint-space placeplate."""
    joints = await self._cart_to_joints(cartesian_position)
    await self._place_plate_j(joints)

  async def _set_grip_detail(self):
    """Configure a default vertical station type for pick/place operations."""
    await self.driver.send_command(f"StationType {self.location_index} 1 0 100 0 10")

  # -- GENERAL COMMANDS ------------------------------------------------------

  async def request_base(self) -> tuple[float, float, float, float]:
    """Get the robot base offset.

    Returns:
      A tuple containing (x_offset, y_offset, z_offset, z_rotation)
    """
    data = await self.driver.send_command("base")
    parts = data.split()
    if len(parts) != 4:
      raise PreciseFlexError(-1, "Unexpected response format from base command.")
    return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))

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
    await self.driver.send_command(f"base {x_offset} {y_offset} {z_offset} {z_rotation}")

  async def request_monitor_speed(self) -> int:
    """Get the global system (monitor) speed.

    Returns:
      Current monitor speed as a percentage (1-100)
    """
    response = await self.driver.send_command("mspeed")
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
    await self.driver.send_command(f"mspeed {speed_percent}")

  async def nop(self) -> None:
    """No operation command.

    Does nothing except return the standard reply. Can be used to see if the link
    is active or to check for exceptions.
    """
    await self.driver.send_command("nop")

  async def request_payload(self) -> int:
    """Get the payload percent value for the current robot.

    Returns:
      Current payload as a percentage of maximum (0-100)
    """
    response = await self.driver.send_command("payload")
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
    await self.driver.send_command(f"payload {payload_percent}")

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
      if isinstance(value, str):
        await self.driver.send_command(
          f'pc {data_id} {unit_number} {sub_unit} {array_index} "{value}"'
        )
      else:
        await self.driver.send_command(
          f"pc {data_id} {unit_number} {sub_unit} {array_index} {value}"
        )
    else:
      if isinstance(value, str):
        await self.driver.send_command(f'pc {data_id} "{value}"')
      else:
        await self.driver.send_command(f"pc {data_id} {value}")

  async def request_parameter(
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
          response = await self.driver.send_command(
            f"pd {data_id} {unit_number} {sub_unit} {array_index}"
          )
        else:
          response = await self.driver.send_command(f"pd {data_id} {unit_number} {sub_unit}")
      else:
        response = await self.driver.send_command(f"pd {data_id} {unit_number}")
    else:
      response = await self.driver.send_command(f"pd {data_id}")
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
    await self.driver.send_command(f"reset {robot_number}")

  async def request_selected_robot(self) -> int:
    """Get the number of the currently selected robot.

    Returns:
      The number of the currently selected robot.
    """
    response = await self.driver.send_command("selectRobot")
    return int(response)

  async def select_robot(self, robot_number: int) -> None:
    """Change the robot associated with this communications link.

    Does not affect the operation or attachment state of the robot. The status thread
    may select any robot or 0. Except for the status thread, a robot may only be
    selected by one thread at a time.

    Args:
      robot_number: The new robot to be connected to this thread (1 to N_ROB) or 0 for none.
    """
    await self.driver.send_command(f"selectRobot {robot_number}")

  async def request_signal(self, signal_number: int) -> int:
    """Get the value of the specified digital input or output signal.

    Args:
      signal_number: The number of the digital signal to get.

    Returns:
      The current signal value.
    """
    response = await self.driver.send_command(f"sig {signal_number}")
    sig_id, sig_val = response.split()
    return int(sig_val)

  async def set_signal(self, signal_number: int, value: int) -> None:
    """Set the specified digital input or output signal.

    Args:
      signal_number: The number of the digital signal to set.
      value: The signal value to set. 0 = off, non-zero = on.
    """
    await self.driver.send_command(f"sig {signal_number} {value}")

  async def request_system_state(self) -> int:
    """Get the global system state code.

    Returns:
      The global system state code. Please see documentation for DataID 234.
    """
    response = await self.driver.send_command("sysState")
    return int(response)

  async def request_tool_transformation_values(
    self,
  ) -> tuple[float, float, float, float, float, float]:
    """Get the current tool transformation values.

    Returns:
      A tuple containing (X, Y, Z, yaw, pitch, roll) for the tool transformation.
    """
    data = await self.driver.send_command("tool")
    if data.startswith("tool: "):
      data = data[6:]
    parts = data.split()
    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format from tool command.")
    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts)
    return (x, y, z, yaw, pitch, roll)

  async def set_tool_transformation_values(
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
    await self.driver.send_command(f"tool {x} {y} {z} {yaw} {pitch} {roll}")

  async def request_version(self) -> str:
    """Get the current version of TCS and any installed plug-ins.

    Returns:
      str: The current version information.
    """
    return await self.driver.send_command("version")

  # -- LOCATION COMMANDS -----------------------------------------------------

  async def _set_joint_angles(
    self,
    location_index: int,
    joint_position: Dict[int, float],
  ) -> None:
    """Set joint angles for stored location, handling rail configuration."""
    if self._has_rail:
      await self.driver.send_command(
        f"locAngles {location_index} "
        f"{joint_position[PFAxis.RAIL]} "
        f"{joint_position[PFAxis.BASE]} "
        f"{joint_position[PFAxis.SHOULDER]} "
        f"{joint_position[PFAxis.ELBOW]} "
        f"{joint_position[PFAxis.WRIST]} "
        f"{joint_position[PFAxis.GRIPPER]}"
      )
    else:
      await self.driver.send_command(
        f"locAngles {location_index} "
        f"{joint_position[PFAxis.BASE]} "
        f"{joint_position[PFAxis.SHOULDER]} "
        f"{joint_position[PFAxis.ELBOW]} "
        f"{joint_position[PFAxis.WRIST]} "
        f"{joint_position[PFAxis.GRIPPER]}"
      )

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
      data = await self.driver.send_command("destC")
    else:
      data = await self.driver.send_command(f"destC {arg1}")
    parts = data.split()
    if len(parts) != 7:
      raise PreciseFlexError(-1, "Unexpected response format from destC command.")
    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts[:6])
    config = int(parts[6])
    return (x, y, z, yaw, pitch, roll, config)

  async def dest_j(self, arg1: int = 0) -> Dict[int, float]:
    """Get the destination or current joint location of the robot.

    Args:
      arg1: Selects return value. Defaults to 0.
      0 = Return current joint location if robot is not moving
      1 = Return target joint location of the previous or current move

    Returns:
      A dict mapping PFAxis to float values.
      If arg1 = 1 or robot is moving, returns the target joint positions.
      If arg1 = 0 and robot is not moving, returns the current joint positions.
    """
    if arg1 == 0:
      data = await self.driver.send_command("destJ")
    else:
      data = await self.driver.send_command(f"destJ {arg1}")
    parts = data.split()
    if not parts:
      raise PreciseFlexError(-1, "Unexpected response format from destJ command.")
    return self._parse_angles_response(parts)

  async def here_j(self, location_index: int) -> None:
    """Record the current position of the selected robot into the specified Location as angles.

    The Location is automatically set to type "angles".

    Args:
      location_index: The station index, from 1 to N_LOC.
    """
    await self.driver.send_command(f"hereJ {location_index}")

  async def here_c(self, location_index: int) -> None:
    """Record the current position of the selected robot into the specified Location as Cartesian.

    The Location object is automatically set to type "Cartesian".
    Can be used to change the pallet origin (index 1,1,1) value.

    Args:
      location_index: The station index, from 1 to N_LOC.
    """
    await self.driver.send_command(f"hereC {location_index}")

  # -- PROFILE COMMANDS ------------------------------------------------------

  async def request_profile_speed(self, profile_index: int) -> float:
    """Get the speed property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed as a percentage. 100 = full speed.
    """
    response = await self.driver.send_command(f"Speed {profile_index}")
    profile, speed = response.split()
    return float(speed)

  async def set_profile_speed(self, profile_index: int, speed_percent: float) -> None:
    """Set the speed property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed_percent: The new speed as a percentage. 100 = full speed.
      Values > 100 may be accepted depending on system configuration.
    """
    await self.driver.send_command(f"Speed {profile_index} {speed_percent}")

  async def request_profile_speed2(self, profile_index: int) -> float:
    """Get the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed2 as a percentage. Used for Cartesian moves.
    """
    response = await self.driver.send_command(f"Speed2 {profile_index}")
    profile, speed2 = response.split()
    return float(speed2)

  async def set_profile_speed2(self, profile_index: int, speed2_percent: float) -> None:
    """Set the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed2_percent: The new speed2 as a percentage. 100 = full speed.
      Used for Cartesian moves. Normally set to 0.
    """
    await self.driver.send_command(f"Speed2 {profile_index} {speed2_percent}")

  async def request_profile_accel(self, profile_index: int) -> float:
    """Get the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration as a percentage. 100 = maximum acceleration.
    """
    response = await self.driver.send_command(f"Accel {profile_index}")
    profile, accel = response.split()
    return float(accel)

  async def set_profile_accel(self, profile_index: int, accel_percent: float) -> None:
    """Set the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      accel_percent: The new acceleration as a percentage. 100 = maximum acceleration.
      Maximum value depends on system configuration.
    """
    await self.driver.send_command(f"Accel {profile_index} {accel_percent}")

  async def request_profile_accel_ramp(self, profile_index: int) -> float:
    """Get the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration ramp time in seconds.
    """
    response = await self.driver.send_command(f"AccRamp {profile_index}")
    profile, accel_ramp = response.split()
    return float(accel_ramp)

  async def set_profile_accel_ramp(self, profile_index: int, accel_ramp_seconds: float) -> None:
    """Set the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      accel_ramp_seconds: The new acceleration ramp time in seconds.
    """
    await self.driver.send_command(f"AccRamp {profile_index} {accel_ramp_seconds}")

  async def request_profile_decel(self, profile_index: int) -> float:
    """Get the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration as a percentage. 100 = maximum deceleration.
    """
    response = await self.driver.send_command(f"Decel {profile_index}")
    profile, decel = response.split()
    return float(decel)

  async def set_profile_decel(self, profile_index: int, decel_percent: float) -> None:
    """Set the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      decel_percent: The new deceleration as a percentage. 100 = maximum deceleration.
      Maximum value depends on system configuration.
    """
    await self.driver.send_command(f"Decel {profile_index} {decel_percent}")

  async def request_profile_decel_ramp(self, profile_index: int) -> float:
    """Get the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration ramp time in seconds.
    """
    response = await self.driver.send_command(f"DecRamp {profile_index}")
    profile, decel_ramp = response.split()
    return float(decel_ramp)

  async def set_profile_decel_ramp(self, profile_index: int, decel_ramp_seconds: float) -> None:
    """Set the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      decel_ramp_seconds: The new deceleration ramp time in seconds.
    """
    await self.driver.send_command(f"DecRamp {profile_index} {decel_ramp_seconds}")

  async def request_profile_in_range(self, profile_index: int) -> float:
    """Get the InRange property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current InRange value (-1 to 100).
      -1 = do not stop at end of motion if blending is possible
      0 = always stop but do not check end point error
      > 0 = wait until close to end point (larger numbers mean less position error allowed)
    """
    response = await self.driver.send_command(f"InRange {profile_index}")
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
    await self.driver.send_command(f"InRange {profile_index} {in_range_value}")

  async def request_profile_straight(self, profile_index: int) -> bool:
    """Get the Straight property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      The current Straight property value.
      True = follow a straight-line path
      False = follow a joint-based path (coordinated axes movement)
    """
    response = await self.driver.send_command(f"Straight {profile_index}")
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
    await self.driver.send_command(f"Straight {profile_index} {straight_int}")

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
    await self.driver.send_command(
      f"Profile {profile} {speed} {speed2} {acceleration} {deceleration} "
      f"{acceleration_ramp} {deceleration_ramp} {in_range} {straight_int}"
    )

  async def request_motion_profile_values(
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
    data = await self.driver.send_command(f"Profile {profile}")
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
      int(parts[8]) != 0,
    )

  # -- RAIL COMMANDS ---------------------------------------------------------

  async def _set_rail_position(self, station_id: int, rail_position: float) -> None:
    """Set the rail position for the specified station.

    Args:
      station_id: The station index.
      rail_position: The rail position in mm.
    """
    await self.driver.send_command(f"Rail {station_id} {rail_position}")

  async def _move_rail(self, station_id: Optional[int] = None, mode: int = 1) -> None:
    """Move the rail to the position stored at the specified station.

    Args:
      station_id: The station index whose rail position to move to.
      mode: Motion mode (0 = normal).
    """
    if station_id is not None:
      await self.driver.send_command(f"MoveRail {station_id} {mode}")
    else:
      await self.driver.send_command(f"MoveRail {mode}")

  # -- MOTION COMMANDS -------------------------------------------------------

  async def _move_to_stored_location(self, location_index: int, profile_index: int) -> None:
    """Move to the location specified by the station index using the specified profile.

    Args:
      location_index: The index of the location to which the robot moves.
      profile_index: The profile index for this move.

    Note:
      Requires that the robot be attached.
    """
    await self.driver.send_command(f"move {location_index} {profile_index}")

  async def _move_to_stored_location_appro(self, location_index: int, profile_index: int) -> None:
    """Approach the location specified by the station index using the specified profile.

    This is similar to `_move_to_stored_location` except that the Z clearance value is included.

    Args:
      location_index: The index of the location to which the robot moves.
      profile_index: The profile index for this move.

    Note:
      Requires that the robot be attached.
    """
    await self.driver.send_command(f"moveAppro {location_index} {profile_index}")

  async def _move_j(self, profile_index: int, joint_coords: Dict[int, float]) -> None:
    """Move the robot using joint coordinates, handling rail configuration."""
    if self._has_rail:
      angles_str = (
        f"{joint_coords[PFAxis.BASE]} "
        f"{joint_coords[PFAxis.SHOULDER]} "
        f"{joint_coords[PFAxis.ELBOW]} "
        f"{joint_coords[PFAxis.WRIST]} "
        f"{joint_coords[PFAxis.GRIPPER]} "
        f"{joint_coords[PFAxis.RAIL]} "
      )
    else:
      angles_str = (
        f"{joint_coords[PFAxis.BASE]} "
        f"{joint_coords[PFAxis.SHOULDER]} "
        f"{joint_coords[PFAxis.ELBOW]} "
        f"{joint_coords[PFAxis.WRIST]} "
        f"{joint_coords[PFAxis.GRIPPER]}"
      )
    await self.driver.send_command(f"moveJ {profile_index} {angles_str}")

  async def release_brake(self, axis: int) -> None:
    """Release the axis brake.

    Overrides the normal operation of the brake. It is important that the brake not be set
    while a motion is being performed. This feature is used to lock an axis to prevent
    motion or jitter.

    Args:
      axis: The number of the axis whose brake should be released.
    """
    await self.driver.send_command(f"releaseBrake {axis}")

  async def set_brake(self, axis: int) -> None:
    """Set the axis brake.

    Overrides the normal operation of the brake. It is important not to set a brake on an
    axis that is moving as it may damage the brake or damage the motor.

    Args:
      axis: The number of the axis whose brake should be set.
    """
    await self.driver.send_command(f"setBrake {axis}")

  async def zero_torque(self, enable: bool, axis_mask: int = 1) -> None:
    """Sets or clears zero torque mode for the selected robot.

    Individual axes may be placed into zero torque mode while the remaining axes are servoing.

    Args:
      enable: If True, enable torque mode for axes specified by axis_mask.  If False, disable torque mode for the entire robot.
      axis_mask: The bit mask specifying the axes to be placed in torque mode when enable is True.  The mask is computed by OR'ing the axis bits: 1 = axis 1, 2 = axis 2, 4 = axis 3, 8 = axis 4, etc.  Ignored when enable is False.
    """
    if enable:
      assert axis_mask > 0, "axis_mask must be greater than 0"
      await self.driver.send_command(f"zeroTorque 1 {axis_mask}")
    else:
      await self.driver.send_command("zeroTorque 0")

  # -- PAROBOT COMMANDS ------------------------------------------------------

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
    await self.driver.send_command(f"ChangeConfig {grip_mode}")

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
    await self.driver.send_command(f"ChangeConfig2 {grip_mode}")

  async def _request_grasp_data(self) -> tuple[float, float, float]:
    """Get the data to be used for the next force-controlled PickPlate command grip operation.

    Returns:
      A tuple containing (plate_width_mm, finger_speed_percent, grasp_force)
    """
    data = await self.driver.send_command("GraspData")
    parts = data.split()
    if len(parts) != 3:
      raise PreciseFlexError(-1, "Unexpected response format from GraspData command.")
    return (float(parts[0]), float(parts[1]), float(parts[2]))

  async def _set_grasp_data(
    self, plate_width: float, finger_speed_percent: float, grasp_force: float
  ) -> None:
    """Set the data to be used for the next force-controlled PickPlate command grip operation.

    This data remains in effect until the next GraspData command or the system is restarted.

    Args:
      plate_width: The plate width in mm.
      finger_speed_percent: The finger speed during grasp where 100 means 100%.
      grasp_force: The gripper squeezing force, in Newtons.
      A positive value indicates the fingers must close to grasp.
      A negative value indicates the fingers must open to grasp.
    """
    await self.driver.send_command(f"GraspData {plate_width} {finger_speed_percent} {grasp_force}")

  async def _request_grip_close_pos(self) -> float:
    """Get the gripper close position for the servoed gripper.

    Returns:
      float: The current gripper close position.
    """
    data = await self.driver.send_command("GripClosePos")
    return float(data)

  async def _set_grip_close_pos(self, close_position: float) -> None:
    """Set the gripper close position for the servoed gripper.

    The close position may be changed by a force-controlled grip operation.

    Args:
      close_position: The new gripper close position.
    """
    await self.driver.send_command(f"GripClosePos {close_position}")

  async def _request_grip_open_pos(self) -> float:
    """Get the gripper open position for the servoed gripper.

    Returns:
      float: The current gripper open position.
    """
    data = await self.driver.send_command("GripOpenPos")
    return float(data)

  async def _set_grip_open_pos(self, open_position: float) -> None:
    """Set the gripper open position for the servoed gripper.

    Args:
      open_position: The new gripper open position.
    """
    await self.driver.send_command(f"GripOpenPos {open_position}")

  # -- parsing helpers -------------------------------------------------------

  def _parse_xyz_response(
    self, parts: List[str]
  ) -> tuple[float, float, float, float, float, float]:
    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format for Cartesian coordinates.")
    return (
      float(parts[0]),
      float(parts[1]),
      float(parts[2]),
      float(parts[3]),
      float(parts[4]),
      float(parts[5]),
    )

  def _parse_angles_response(self, parts: List[str]) -> Dict[int, float]:
    """Parse angle values from a response string.

    For self._has_rail=True:  wire order is [base, shoulder, elbow, wrist, gripper, rail]
    For self._has_rail=False: wire order is [base, shoulder, elbow, wrist, gripper]
    """
    if len(parts) < 3:
      raise PreciseFlexError(-1, "Unexpected response format for angles.")
    if self._has_rail:
      return {
        PFAxis.RAIL: float(parts[5]) if len(parts) > 5 else 0.0,
        PFAxis.BASE: float(parts[0]),
        PFAxis.SHOULDER: float(parts[1]),
        PFAxis.ELBOW: float(parts[2]),
        PFAxis.WRIST: float(parts[3]) if len(parts) > 3 else 0.0,
        PFAxis.GRIPPER: float(parts[4]) if len(parts) > 4 else 0.0,
      }
    return {
      PFAxis.RAIL: 0.0,
      PFAxis.BASE: float(parts[0]),
      PFAxis.SHOULDER: float(parts[1]),
      PFAxis.ELBOW: float(parts[2]) if len(parts) > 2 else 0.0,
      PFAxis.WRIST: float(parts[3]) if len(parts) > 3 else 0.0,
      PFAxis.GRIPPER: float(parts[4]) if len(parts) > 4 else 0.0,
    }


# ---------------------------------------------------------------------------
# Concrete model backends
# ---------------------------------------------------------------------------


class PreciseFlex400(Device):
  """Backend for the PreciseFlex 400 robotic arm."""

  def __init__(
    self, host: str, port: int = 10100, has_rail: bool = False, timeout: int = 20
  ) -> None:
    driver = PreciseFlexDriver(host=host, port=port, timeout=timeout)
    super().__init__(driver=driver)
    self.driver: PreciseFlexDriver = driver
    backend = PreciseFlexArmBackend(driver=driver, has_rail=has_rail)
    self.reference = Resource(name="PreciseFlex400", size_x=200, size_y=200, size_z=200)
    self.arm = OrientableArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]


class PreciseFlex3400Backend(PreciseFlexArmBackend):
  """Backend for the PreciseFlex 3400 robotic arm."""

  def __init__(
    self,
    driver: PreciseFlexDriver,
    has_rail: bool = False,
  ) -> None:
    super().__init__(driver=driver, has_rail=has_rail)
