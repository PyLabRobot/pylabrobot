"""PreciseFlex driver - owns the socket I/O connection and device lifecycle."""

import asyncio
import dataclasses
import logging
import time
import warnings
from typing import Callable, ClassVar, Dict, List, Literal, NamedTuple, Optional, Sequence

from pylabrobot.brooks.precise_flex import kinematics
from pylabrobot.brooks.precise_flex.config import Axis, PreciseFlexConfiguration, StationAccess
from pylabrobot.brooks.precise_flex.kinematics import JointPose
from pylabrobot.events import coordinate_reference, emit_event, evented_operation
from pylabrobot.io.socket import Socket
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation

from .confirmed_firmware_versions import (
  SUPPORTED_ROBOT_TYPES,
  is_confirmed,
  is_supported_model,
  suggest_entry,
)
from .data_ids import DataID, PowerState
from .errors import OutOfRangeOfMotionError, PreciseFlexError
from .interrupt import halt_and_resync, halt_on_interrupt
from .kinematics import ElbowOrientation, PreciseFlexCartesianPose, Wrist
from .tcs_modules import missing_required_modules

logger = logging.getLogger(__name__)


# InRange sentinel that lets the controller blend through waypoints instead of stopping at each one.
BLEND_IN_RANGE = -1


def _controller_reference(controller: "PreciseFlex") -> dict[str, object]:
  """Return the stable controller identity used by structured execution events."""
  return {
    "name": "precise_flex",
    "type": type(controller).__name__,
    "host": controller.io._host,
    "port": controller.io._port,
  }


def _joint_pose_reference(position: JointPose) -> dict[str, float]:
  """Convert an axis-keyed joint pose into a JSON-friendly target description."""
  return {
    (axis.name.lower() if isinstance(axis, Axis) else str(axis)): float(value)
    for axis, value in position.items()
  }


def _cartesian_target_reference(
  location: Coordinate,
  direction: float,
  *,
  orientation: Optional["ElbowOrientation"] = None,
  wrist: Optional["Wrist"] = None,
  rail_position: Optional[float] = None,
) -> dict[str, object]:
  """Describe a Cartesian controller target without serializing a full pose object."""
  return {
    "location": coordinate_reference(location),
    "direction": float(direction),
    "orientation": orientation,
    "wrist": wrist,
    "rail_position": rail_position,
  }


class MotionProfile(NamedTuple):
  """A controller motion profile, as reported by ``Profile <n>`` (field order matches the wire)."""

  profile: int
  speed: float
  speed2: float
  acceleration: float
  deceleration: float
  acceleration_ramp: float
  deceleration_ramp: float
  in_range: float  # -1 (BLEND_IN_RANGE) to 100; -1 blends, 0 stops, >0 enforces position accuracy
  straight: bool  # True = straight-line path, False = joint-based path


def _parse_scalar(response: str) -> float:
  """Parse the first numeric field of a DataID reply.

  Some scalar DataIDs come back zero-padded (e.g. robot type as ``12, 0, 0, ...``)
  and Cartesian references carry several components; take the leading value.
  """
  return float(response.split(",")[0])


def _parse_per_axis(response: str) -> Dict[Axis, float]:
  """Parse a comma-separated per-axis DataID reply into an {Axis: value} map."""
  values = [float(v) for v in response.split(",")]
  return {Axis(i + 1): values[i] for i in range(min(len(values), len(Axis)))}


def _zip_axis_ranges(
  low: Dict[Axis, float], high: Dict[Axis, float]
) -> Dict[Axis, tuple[float, float]]:
  """Combine min and max per-axis maps into an {Axis: (min, max)} map."""
  return {axis: (low[axis], high[axis]) for axis in low.keys() & high.keys()}


def _snap_to_current(ik_joints: JointPose, current: JointPose, wrist: Optional[Wrist]) -> JointPose:
  """Shift each rotary joint by 360° multiples toward `current`, then re-enforce
  the wrist-sign half on J4 so the result still matches `wrist`. Avoids
  gratuitous full-turn moves when multiple IK solutions are equivalent.
  """
  out = dict(ik_joints)
  for axis in (Axis.SHOULDER, Axis.ELBOW, Axis.WRIST):
    out[axis] += 360 * round((current[axis] - out[axis]) / 360)
  if wrist == "ccw" and out[Axis.WRIST] < 0:
    out[Axis.WRIST] += 360
  elif wrist == "cw" and out[Axis.WRIST] > 0:
    out[Axis.WRIST] -= 360
  return out


class PreciseFlex:
  """Driver for PreciseFlex robotic arms.

  Owns the Socket I/O connection and device-level operations (power, attach,
  home, response mode).  Exposes ``send_command`` as the generic wire method.

  Documentation and error codes available at
  https://www2.brooksautomation.com/#Root/Welcome.htm
  """

  # Validated parked orientations: planar folds differing only in which way the arm faces, named for
  # the direction the gripper points (BACK / RIGHT / FRONT). The Z column (Axis.BASE) is omitted on
  # purpose - ``park()`` fills it from the discovered travel (3/4 of it) so one orientation works on
  # any reach; set Axis.BASE yourself to override. The gripper and rail are left untouched so parking
  # never drops a held plate or assumes a rail. Assign one to ``parking_position`` to change the park.
  PARKING_POSITION_BACK: ClassVar[JointPose] = {
    Axis.SHOULDER: 90.0,
    Axis.ELBOW: 180.0,
    Axis.WRIST: 90.0,
  }
  PARKING_POSITION_RIGHT: ClassVar[JointPose] = {
    Axis.SHOULDER: 0.0,
    Axis.ELBOW: 180.0,
    Axis.WRIST: 180.0,
  }
  PARKING_POSITION_FRONT: ClassVar[JointPose] = {
    Axis.SHOULDER: -90.0,
    Axis.ELBOW: 180.0,
    Axis.WRIST: 270.0,
  }

  def __init__(
    self,
    host: str,
    gripper_length: float,
    gripper_z_offset: float,
    closed_gripper_position: float,
    port: int = 10100,
    timeout: int = 20,
    is_dual_gripper: bool = False,
    has_rail: bool = False,
    read_kinematics_from_device: bool = True,
    recover_out_of_range: bool = True,
    parking_position: Optional[JointPose] = None,
  ) -> None:
    """
    Args:
      gripper_length: wrist-axis → TCP distance in mm. Used as the fallback /
        override; when ``read_kinematics_from_device`` is True (the default) the
        link lengths and tool length are read from the controller at setup and
        this value is only used if that read fails.
      gripper_z_offset: vertical offset in mm from the wrist plate to the tool tip.
        Depends on the mounted gripper; the concrete Device wrapper supplies a
        model-appropriate default. Always taken from here (not on the controller).
      read_kinematics_from_device: when True, read l1/l2 and the tool length from
        the controller at setup and use them for kinematics; the constructor's
        ``gripper_length`` then acts only as a fallback. Set False to force the
        constructor values regardless of what the controller reports.
      recover_out_of_range: when True (the default), an out-of-range axis (its current position
        outside its soft limit - a state the controller rejects every commanded move for, -1012) is
        driven back into range once via ``recover_axes_within_limits``, the same way at both moments
        it matters: at setup, and before a commanded move (which then retries). If it is still out of
        range after that, ``OutOfRangeOfMotionError`` propagates (no loop). Set False to forbid this
        autonomous motion - an out-of-range axis then raises instead, carrying recovery instructions.
        Every recovery is logged.
      closed_gripper_position: firmware-unit value (passed to ``GripClosePos`` /
        ``GripOpenPos``) at which the jaws are at :attr:`min_gripper_width`.
        Depends on the mounted gripper. The conversion mm → firmware units is
        linear with slope 1: ``units = closed_gripper_position + (width_mm -
        min_gripper_width)``.
      parking_position: initial value for the public, runtime-settable ``parking_position`` that
        ``park()`` moves to. Leave None (the default) and setup fills the generic default RIGHT pose
        (planar fold, Z column at 3/4 of the discovered travel); reassign it any time to park
        elsewhere. While unset (no configuration), ``park()`` falls back to ``movetosafe``.
    """
    super().__init__()
    self.io = Socket(human_readable_device_name="Precise Flex Arm", host=host, port=port)
    self.timeout = timeout
    self.profile_index: int = 1
    self.location_index: int = 1
    self._rail_position_index = 1
    self.horizontal_compliance: bool = False
    self.horizontal_compliance_torque: int = 0
    self._has_rail = has_rail
    self._is_dual_gripper = is_dual_gripper
    self.closed_gripper_position = closed_gripper_position
    self._kinematics_params = kinematics.PF400Params(
      gripper_length=gripper_length, gripper_z_offset=gripper_z_offset
    )
    self._read_kinematics_from_device = read_kinematics_from_device
    self._recover_out_of_range = recover_out_of_range
    # Device configuration, resolved once at setup; None until then. Set before parking_position so its
    # validating setter can check assignments against the soft limits once they are known.
    self._configuration: Optional[PreciseFlexConfiguration] = None
    # Public and runtime-settable (validated on assignment); setup fills the default RIGHT pose when
    # this is left None.
    self.parking_position = parking_position
    if is_dual_gripper:
      warnings.warn(
        "Dual gripper support is experimental and may not work as expected.", UserWarning
      )

  # -- communication ---------------------------------------------------------

  async def send_command(self, command: str) -> str:
    """Send one firmware command while retaining its enclosing operation context."""
    event_data = {
      "device": _controller_reference(self),
      "command": command,
    }
    emit_event("precise_flex.firmware_command.started", **event_data)
    try:
      await self.io.write(command.encode("utf-8") + b"\n")
      reply = await self.io.readline()
      result = self._parse_reply_ensure_successful(reply)
    except BaseException as error:
      emit_event(
        "precise_flex.firmware_command.failed",
        **event_data,
        error_type=type(error).__name__,
        error_message=str(error),
      )
      raise
    emit_event("precise_flex.firmware_command.completed", **event_data, response=result)
    return result

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

  @evented_operation(
    "precise_flex.setup",
    lambda self, skip_home=False: {
      "device": _controller_reference(self),
      "skip_home": skip_home,
    },
  )
  async def setup(self, skip_home: bool = False):
    """Bring the arm fully up: link, control, and (unless skipped) home.

    Args:
      skip_home: If True, skip the homing step during setup.
    """
    await self.connect()
    await self.initialize()
    if not skip_home:
      await self.home()
    await self._handle_out_of_range_axes()

  async def connect(self) -> None:
    """Open the link and agree the response protocol. Powers nothing, moves nothing."""
    await self.io.setup()
    await self.set_response_mode("pc")
    logger.debug("[PreciseFlex %s] connected: port=%s", self.io._host, self.io._port)

  async def initialize(self) -> None:
    """Raise high power, take control, and adopt the controller's own configuration.

    Moves nothing. Homing is ``home()``, deliberately separate: it sweeps the arm
    through its whole envelope, which is not something to do just to bring it up.
    """
    await self.power_on_robot()
    await self.attach(1)
    await self.stop_freedrive_mode()
    await self._discover_configuration()

  async def _discover_configuration(self) -> None:
    """Adopt what the controller reports, so the class defaults are not used blind.

    The link lengths land here, so skipping this leaves IK solving for the wrong arm.
    """
    try:
      self._configuration = await self._request_configuration()
    except Exception as exc:  # discovery is best-effort
      logger.warning(
        "[PreciseFlex %s] could not read configuration, using defaults: %s",
        self.io._host,
        exc,
      )
      return
    self._adopt_configuration(self._configuration)
    if self.parking_position is None:
      self.parking_position = self.PARKING_POSITION_RIGHT
    self._log_configuration_summary(self._configuration)
    self._assess_configuration(self._configuration)

  @evented_operation(
    "precise_flex.stop",
    lambda self: {"device": _controller_reference(self)},
  )
  async def stop(self):
    """Stop the PreciseFlex driver."""
    await self.disconnect()

  async def disconnect(self) -> None:
    """Hand the arm back and close the link. Moves nothing.

    Drops high power as well as releasing the link, because ``initialize`` raised it.
    """
    await self.detach()
    await self.power_off_robot()
    await self._exit()
    await self.io.stop()
    logger.info("[PreciseFlex %s] disconnected: port=%s", self.io._host, self.io._port)

  # -- device-level commands -------------------------------------------------

  async def _exit(self) -> None:
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
    mapping: Dict[int, "PreciseFlex.ResponseMode"] = {0: "pc", 1: "verbose"}
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

  async def request_system_state(self) -> int:
    """Controller power/system-state word (the ``sysState`` command, == DataID 234).

    See :class:`~pylabrobot.brooks.precise_flex.data_ids.PowerState` for the values;
    ``PowerState.OFF_HARD_ESTOP`` (15) means a hard E-stop is engaged, ``PowerState.ON_ATTACHED``
    (21) is the normal running state. Read-only, so it detects an E-stop without provoking an error.
    """
    return int(await self.send_command("sysState"))

  @evented_operation(
    "precise_flex.power_on",
    lambda self: {"device": _controller_reference(self)},
  )
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

  @evented_operation(
    "precise_flex.recover_from_fault",
    lambda self: {"device": _controller_reference(self)},
  )
  async def recover_from_fault(self) -> None:
    """Recover after a collision / fault that stopped the arm and dropped power, leaving it usable.

    A collision trips an envelope error (``-3100`` hard / ``-3122`` soft, see
    :func:`~pylabrobot.brooks.precise_flex.errors.is_collision`); the servo stops the arm itself and
    high power drops. This re-enables power, re-attaches, and re-homes (which only cycles the gripper
    when the other axes are already homed - absolute encoders retain them - so it does not sweep the
    arm), leaving it ready to move. It does **not** drive the arm to any pose; confirm the obstacle is
    removed before calling.

    The envelope error auto-clears, so no explicit clear is needed; a latched fatal that blocks
    power-on is surfaced by ``power_on_robot`` for the operator to reset (DataID 247) or reboot.

    Raises:
      PreciseFlexError: if a hard E-stop is engaged (release the button first) or power cannot be
        re-enabled.
    """
    if await self.request_system_state() == PowerState.OFF_HARD_ESTOP:
      raise PreciseFlexError(
        -1028, "hard E-Stop engaged - release the E-stop button before recovering"
      )
    await self.power_on_robot()
    await self.attach(1)
    await self.home()

  @evented_operation(
    "precise_flex.power_off",
    lambda self: {"device": _controller_reference(self)},
  )
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

  @evented_operation(
    "precise_flex.home",
    lambda self: {"device": _controller_reference(self)},
  )
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

  async def _wait_for_eom(
    self, poll_interval: float = 0.05, settle: float = 0.02, timeout: float = 60.0
  ) -> None:
    """Wait (non-blocking) until the arm has stopped moving, keeping the connection responsive.

    Polls the live joint position (``wherej``) and returns once it stops changing between samples
    (every axis moving less than ``settle``) - i.e. end of motion. It returns promptly when the arm
    is already stationary, including when it was stopped short of its last commanded target (after a
    halt/interrupt or a hand-move), so it never hangs waiting to reach a target that will not be
    reached.

    This deliberately avoids the firmware ``waitForEom``: that command parks the controller's single
    command interpreter and makes it ignore everything else on the connection - including ``halt`` -
    until the move ends (hardware-verified). Polling instead leaves the connection free between
    samples, so a user interrupt can stop the move mid-flight via ``halt`` and other controller
    commands (status, vision, barcode) can run during motion.

    Raises:
      TimeoutError: if the arm never settles within ``timeout`` seconds.
      OperationInterrupted: on a user interrupt (the arm is halted and the connection kept).
    """

    def _floats(reply: str) -> list[float]:
      return [float(x) for x in reply.split()]

    # On interrupt, `halt` stops the move on the now-free connection and we resync; the connection is
    # kept open. Hardware-verified: a clean halt keeps power, attach, and the link (only a collision
    # trips -3122 and drops power, which needs explicit recovery).
    async with halt_on_interrupt(lambda: halt_and_resync(self.io, b"halt")):
      previous = _floats(await self.send_command("wherej"))
      deadline = time.monotonic() + timeout
      while True:
        await asyncio.sleep(poll_interval)
        current = _floats(await self.send_command("wherej"))
        if all(abs(c - p) < settle for c, p in zip(current, previous)):
          return  # stopped moving
        if time.monotonic() > deadline:
          raise TimeoutError(f"motion did not settle within {timeout:.0f}s (current={current})")
        previous = current

  async def request_state(self) -> str:
    """Return state of motion.

    This value indicates the state of the currently executing or last completed robot motion.
    For additional information, please see 'Robot.TrajState' in the GPL reference manual.

    Returns:
      str: The current motion state.
    """
    return await self.send_command("state")

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

  def _parse_angles_response(self, parts: List[str]) -> JointPose:
    """Parse angle values from a response string.

    For self._has_rail=True:  wire order is [base, shoulder, elbow, wrist, gripper, rail]
    For self._has_rail=False: wire order is [base, shoulder, elbow, wrist, gripper]
    """
    if len(parts) < 3:
      raise PreciseFlexError(-1, "Unexpected response format for angles.")
    if self._has_rail:
      return {
        Axis.RAIL: float(parts[5]) if len(parts) > 5 else 0.0,
        Axis.BASE: float(parts[0]),
        Axis.SHOULDER: float(parts[1]),
        Axis.ELBOW: float(parts[2]),
        Axis.WRIST: float(parts[3]) if len(parts) > 3 else 0.0,
        Axis.GRIPPER: float(parts[4]) if len(parts) > 4 else 0.0,
      }
    return {
      Axis.RAIL: 0.0,
      Axis.BASE: float(parts[0]),
      Axis.SHOULDER: float(parts[1]),
      Axis.ELBOW: float(parts[2]) if len(parts) > 2 else 0.0,
      Axis.WRIST: float(parts[3]) if len(parts) > 3 else 0.0,
      Axis.GRIPPER: float(parts[4]) if len(parts) > 4 else 0.0,
    }

  # -- raw parameters -----------------------------------------------------------------------

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
          response = await self.send_command(f"pd {data_id} {unit_number} {sub_unit} {array_index}")
        else:
          response = await self.send_command(f"pd {data_id} {unit_number} {sub_unit}")
      else:
        response = await self.send_command(f"pd {data_id} {unit_number}")
    else:
      response = await self.send_command(f"pd {data_id}")
    return response

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
        await self.send_command(f'pc {data_id} {unit_number} {sub_unit} {array_index} "{value}"')
      else:
        await self.send_command(f"pc {data_id} {unit_number} {sub_unit} {array_index} {value}")
    else:
      if isinstance(value, str):
        await self.send_command(f'pc {data_id} "{value}"')
      else:
        await self.send_command(f"pc {data_id} {value}")

  async def set_axis_parameter(
    self,
    data_id: int,
    axis: Axis,
    value,
    robot_number: int = 1,
  ) -> None:
    """Change one joint's element of a per-axis parameter array (``pc``).

    Per-axis DataIDs (motor current limits, hard-stop homing envelope, joint limits)
    hold one value per joint; this writes a single joint's element and leaves the rest
    untouched. ``axis`` is the controller's 1-based array index (``Axis.GRIPPER`` -> 5),
    cast to int at the wire boundary; reads of the same DataID come back in this order
    (see ``_parse_per_axis``).

    Args:
      data_id: the per-axis DataID to change.
      axis: which joint's element to write.
      value: the new value for that element.
      robot_number: unit number, the robot (1 - N_ROB).

    Note:
      Volatile until a save-to-flash (DataID 901); a power cycle otherwise restores the
      flashed value.
    """
    await self.set_parameter(
      data_id, value, unit_number=robot_number, sub_unit=0, array_index=int(axis)
    )

  async def nop(self) -> None:
    """No operation command.

    Does nothing except return the standard reply. Can be used to see if the link
    is active or to check for exceptions.
    """
    await self.send_command("nop")

  # -- digital I/O --------------------------------------------------------------------------

  async def request_signal(self, signal_number: int) -> int:
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

  # -- motion primitives --------------------------------------------------------------------

  async def _move_j(self, profile_index: int, joint_coords: JointPose) -> None:
    """Move the robot using joint coordinates, handling rail configuration. Raw moveJ - the
    out-of-range guard lives in the caller (``_guarded_move_j``), not in this primitive."""
    if self._has_rail:
      angles_str = (
        f"{joint_coords[Axis.BASE]} "
        f"{joint_coords[Axis.SHOULDER]} "
        f"{joint_coords[Axis.ELBOW]} "
        f"{joint_coords[Axis.WRIST]} "
        f"{joint_coords[Axis.GRIPPER]} "
        f"{joint_coords[Axis.RAIL]} "
      )
    else:
      angles_str = (
        f"{joint_coords[Axis.BASE]} "
        f"{joint_coords[Axis.SHOULDER]} "
        f"{joint_coords[Axis.ELBOW]} "
        f"{joint_coords[Axis.WRIST]} "
        f"{joint_coords[Axis.GRIPPER]}"
      )
    await self.send_command(f"moveJ {profile_index} {angles_str}")

  async def _move_one_axis(self, axis: Axis, position: float) -> None:
    """Move a single axis to an absolute position (firmware ``MoveOneAxis``).

    Used for recovery: the controller blocks a normal move while an axis is out of
    range, but allows a single-axis move heading back into range. Does not wait for
    the motion to complete.
    """
    await self.send_command(f"MoveOneAxis {int(axis)} {position} {self.profile_index}")

  async def _move_to_stored_location(self, location_index: int, profile_index: int) -> None:
    """Move to the location specified by the station index using the specified profile.

    Args:
      location_index: The index of the location to which the robot moves.
      profile_index: The profile index for this move.

    Note:
      Requires that the robot be attached.
    """
    await self.send_command(f"move {location_index} {profile_index}")

  async def _move_to_stored_location_appro(self, location_index: int, profile_index: int) -> None:
    """Approach the location specified by the station index using the specified profile.

    This is similar to `_move_to_stored_location` except that the Z clearance value is included.

    Args:
      location_index: The index of the location to which the robot moves.
      profile_index: The profile index for this move.

    Note:
      Requires that the robot be attached.
    """
    await self.send_command(f"moveAppro {location_index} {profile_index}")

  async def _set_joint_angles(
    self,
    location_index: int,
    joint_position: JointPose,
  ) -> None:
    """Set joint angles for stored location, handling rail configuration."""
    if self._has_rail:
      await self.send_command(
        f"locAngles {location_index} "
        f"{joint_position[Axis.RAIL]} "
        f"{joint_position[Axis.BASE]} "
        f"{joint_position[Axis.SHOULDER]} "
        f"{joint_position[Axis.ELBOW]} "
        f"{joint_position[Axis.WRIST]} "
        f"{joint_position[Axis.GRIPPER]}"
      )
    else:
      await self.send_command(
        f"locAngles {location_index} "
        f"{joint_position[Axis.BASE]} "
        f"{joint_position[Axis.SHOULDER]} "
        f"{joint_position[Axis.ELBOW]} "
        f"{joint_position[Axis.WRIST]} "
        f"{joint_position[Axis.GRIPPER]}"
      )

  async def _cart_to_joints(self, cart: PreciseFlexCartesianPose) -> JointPose:
    """Convert a Cartesian location into a full joint dict using our IK.

    Any of cart.orientation, cart.wrist, and cart.rail_position left as None
    default to the current pose - picks the configuration closest to where the
    arm is now. Fetches current joint state for the gripper and rail axes so
    callers get a complete joint dict, ready for `_guarded_move_j`.
    """
    joints, current = await self._request_state()
    cart = dataclasses.replace(
      cart,
      orientation=current.orientation if cart.orientation is None else cart.orientation,
      wrist=current.wrist if cart.wrist is None else cart.wrist,
      rail_position=current.rail_position if cart.rail_position is None else cart.rail_position,
    )
    ik_joints = _snap_to_current(kinematics.ik(cart, p=self._kinematics_params), joints, cart.wrist)
    # IK only solves the arm axes; gripper and rail keep their current values.
    for axis in (Axis.BASE, Axis.SHOULDER, Axis.ELBOW, Axis.WRIST):
      joints[axis] = ik_joints[axis]
    if cart.rail_position is not None:
      joints[Axis.RAIL] = cart.rail_position
    return joints

  # -- speed & motion profiles --------------------------------------------------------------

  async def request_monitor_speed(self) -> int:
    """Get the global system (monitor) speed.

    Returns:
      Current monitor speed as a percentage (0-100)
    """
    response = await self.send_command("mspeed")
    return int(response)

  async def set_monitor_speed(self, speed_pct: int) -> None:
    """Set the global system (monitor) speed.

    Args:
      speed_pct: Speed percentage between 0 and 100, where 100 means full speed.

    Raises:
      ValueError: If speed_pct is not between 0 and 100.
    """
    if not 0 <= speed_pct <= 100:
      raise ValueError(f"speed_pct must be between 0 and 100, got {speed_pct}")
    await self.send_command(f"mspeed {speed_pct}")

  async def request_payload(self) -> int:
    """Get the payload percent value for the current robot.

    Returns:
      Current payload as a percentage of maximum (0-100)
    """
    response = await self.send_command("payload")
    return int(response)

  async def set_payload(self, payload_pct: int) -> None:
    """Set the payload percent of maximum for the currently selected or attached robot.

    Args:
      payload_pct: Payload percentage from 0 to 100 indicating the percent of the maximum payload the robot is carrying.

    Raises:
      ValueError: If payload_pct is not between 0 and 100.

    Note:
      If the robot is moving, waits for the robot to stop before setting a value.
    """
    if not (0 <= payload_pct <= 100):
      raise ValueError("Payload percent must be between 0 and 100")
    await self.send_command(f"payload {payload_pct}")

  async def request_profile_speed(self, profile_index: int) -> float:
    """Get the speed property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed as a percentage. 100 = full speed.
    """
    response = await self.send_command(f"Speed {profile_index}")
    profile, speed = response.split()
    return float(speed)

  async def set_profile_speed(self, profile_index: int, speed_pct: float) -> None:
    """Set the speed property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed_pct: The new speed as a percentage (0-100). 100 = full speed.

    Raises:
      ValueError: If speed_pct is not between 0 and 100.
    """
    if not 0 <= speed_pct <= 100:
      raise ValueError(f"speed_pct must be between 0 and 100, got {speed_pct}")
    await self.send_command(f"Speed {profile_index} {speed_pct}")

  async def request_profile_speed2(self, profile_index: int) -> float:
    """Get the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current speed2 as a percentage. Used for Cartesian moves.
    """
    response = await self.send_command(f"Speed2 {profile_index}")
    profile, speed2 = response.split()
    return float(speed2)

  async def set_profile_speed2(self, profile_index: int, speed2_pct: float) -> None:
    """Set the speed2 property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      speed2_pct: The new speed2 as a percentage (0-100). 100 = full speed.
        Used for Cartesian moves. Normally set to 0.

    Raises:
      ValueError: If speed2_pct is not between 0 and 100.
    """
    if not 0 <= speed2_pct <= 100:
      raise ValueError(f"speed2_pct must be between 0 and 100, got {speed2_pct}")
    await self.send_command(f"Speed2 {profile_index} {speed2_pct}")

  async def request_profile_acceleration(self, profile_index: int) -> float:
    """Get the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration as a percentage. 100 = maximum acceleration.
    """
    response = await self.send_command(f"Accel {profile_index}")
    profile, acceleration = response.split()
    return float(acceleration)

  async def set_profile_acceleration(self, profile_index: int, acceleration_pct: float) -> None:
    """Set the acceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      acceleration_pct: The new acceleration as a percentage (0-100). 100 = maximum acceleration.

    Raises:
      ValueError: If acceleration_pct is not between 0 and 100.
    """
    if not 0 <= acceleration_pct <= 100:
      raise ValueError(f"acceleration_pct must be between 0 and 100, got {acceleration_pct}")
    await self.send_command(f"Accel {profile_index} {acceleration_pct}")

  async def request_profile_acceleration_ramp(self, profile_index: int) -> float:
    """Get the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current acceleration ramp time in seconds.
    """
    response = await self.send_command(f"AccRamp {profile_index}")
    profile, acceleration_ramp = response.split()
    return float(acceleration_ramp)

  async def set_profile_acceleration_ramp(
    self, profile_index: int, acceleration_ramp_seconds: float
  ) -> None:
    """Set the acceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      acceleration_ramp_seconds: The new acceleration ramp time in seconds.
    """
    await self.send_command(f"AccRamp {profile_index} {acceleration_ramp_seconds}")

  async def request_profile_deceleration(self, profile_index: int) -> float:
    """Get the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration as a percentage. 100 = maximum deceleration.
    """
    response = await self.send_command(f"Decel {profile_index}")
    profile, deceleration = response.split()
    return float(deceleration)

  async def set_profile_deceleration(self, profile_index: int, deceleration_pct: float) -> None:
    """Set the deceleration property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      deceleration_pct: The new deceleration as a percentage (0-100). 100 = maximum deceleration.

    Raises:
      ValueError: If deceleration_pct is not between 0 and 100.
    """
    if not 0 <= deceleration_pct <= 100:
      raise ValueError(f"deceleration_pct must be between 0 and 100, got {deceleration_pct}")
    await self.send_command(f"Decel {profile_index} {deceleration_pct}")

  async def request_profile_deceleration_ramp(self, profile_index: int) -> float:
    """Get the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to query.

    Returns:
      float: The current deceleration ramp time in seconds.
    """
    response = await self.send_command(f"DecRamp {profile_index}")
    profile, deceleration_ramp = response.split()
    return float(deceleration_ramp)

  async def set_profile_deceleration_ramp(
    self, profile_index: int, deceleration_ramp_seconds: float
  ) -> None:
    """Set the deceleration ramp property of the specified profile.

    Args:
      profile_index: The profile index to modify.
      deceleration_ramp_seconds: The new deceleration ramp time in seconds.
    """
    await self.send_command(f"DecRamp {profile_index} {deceleration_ramp_seconds}")

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

  async def request_profile_straight(self, profile_index: int) -> bool:
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

  async def request_motion_profile_values(self, profile: int) -> MotionProfile:
    """
    Get the current motion profile values for the specified profile index on the PreciseFlex robot.

    Args:
      profile: Profile index to get values for.

    Returns:
      A :class:`MotionProfile` with the profile's speed, acceleration, ramps, InRange and path mode.
    """
    data = await self.send_command(f"Profile {profile}")
    parts = data.split(" ")
    if len(parts) != 9:
      raise PreciseFlexError(-1, "Unexpected response format from device.")
    return MotionProfile(
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

  async def set_motion_profile_values(
    self,
    profile: int,
    speed_pct: float,
    speed2_pct: float,
    acceleration_pct: float,
    deceleration_pct: float,
    acceleration_ramp: float,
    deceleration_ramp: float,
    in_range: float,
    straight: bool,
  ):
    """
    Set motion profile values for the specified profile index on the PreciseFlex robot.

    Args:
      profile: Profile index to set values for.
      speed_pct: Percentage of maximum speed (0-100). 100 = full speed.
      speed2_pct: Secondary speed setting (0-100), typically for Cartesian moves. Normally 0.
      acceleration_pct: Percentage of maximum acceleration (0-100). 100 = full acceleration.
      deceleration_pct: Percentage of maximum deceleration (0-100). 100 = full deceleration.
      acceleration_ramp: Acceleration ramp time in seconds.
      deceleration_ramp: Deceleration ramp time in seconds.
      in_range: InRange value, from -1 to 100. -1 = allow blending, 0 = stop without checking, >0 = enforce position accuracy.
      straight: If True, follow a straight-line path (-1). If False, follow a joint-based path (0).
    """
    if not 0 <= speed_pct <= 100:
      raise ValueError(f"speed_pct must be between 0 and 100, got {speed_pct}")
    if not 0 <= speed2_pct <= 100:
      raise ValueError(f"speed2_pct must be between 0 and 100, got {speed2_pct}")
    if not 0 <= acceleration_pct <= 100:
      raise ValueError(f"acceleration_pct must be between 0 and 100, got {acceleration_pct}")
    if not 0 <= deceleration_pct <= 100:
      raise ValueError(f"deceleration_pct must be between 0 and 100, got {deceleration_pct}")
    if acceleration_ramp < 0:
      raise ValueError("acceleration_ramp must be >= 0 (seconds).")
    if deceleration_ramp < 0:
      raise ValueError("deceleration_ramp must be >= 0 (seconds).")
    if not (-1 <= in_range <= 100):
      raise ValueError("InRange must be between -1 and 100.")
    straight_int = -1 if straight else 0
    await self.send_command(
      f"Profile {profile} {speed_pct} {speed2_pct} {acceleration_pct} {deceleration_pct} "
      f"{acceleration_ramp} {deceleration_ramp} {in_range} {straight_int}"
    )

  async def _set_speed(self, speed_pct: float):
    """Set the speed percentage of the arm's movement (0-100)."""
    await self.set_profile_speed(self.profile_index, speed_pct)

  async def _request_speed(self) -> float:
    """Get the current speed percentage of the arm's movement."""
    return await self.request_profile_speed(self.profile_index)

  # -- brakes, torque & freedrive -----------------------------------------------------------

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

  async def zero_torque(self, enable: bool, axis_mask: int = 1) -> None:
    """Sets or clears zero torque mode for the selected robot.

    Individual axes may be placed into zero torque mode while the remaining axes are servoing.

    Args:
      enable: If True, enable torque mode for axes specified by axis_mask.  If False, disable torque mode for the entire robot.
      axis_mask: The bit mask specifying the axes to be placed in torque mode when enable is True.  The mask is computed by OR'ing the axis bits: 1 = axis 1, 2 = axis 2, 4 = axis 3, 8 = axis 4, etc.  Ignored when enable is False.
    """
    if enable:
      assert axis_mask > 0, "axis_mask must be greater than 0"
      await self.send_command(f"zeroTorque 1 {axis_mask}")
    else:
      await self.send_command("zeroTorque 0")

  @evented_operation(
    "precise_flex.start_freedrive",
    lambda self, free_axes=None: {
      "device": _controller_reference(self),
      "free_axes": [int(axis) for axis in free_axes] if free_axes is not None else None,
    },
  )
  async def start_freedrive_mode(self, free_axes: Optional[List[int]] = None) -> None:
    """Enter freedrive mode, allowing manual movement of the specified joints.

    The robot must be attached to enter free mode.

    Args:
      free_axes: List of joint indices to free. Use [0] for all axes.
    """
    if free_axes is None:
      # Default to the positioning axes that exist; include the rail only when
      # fitted - freemode on an absent axis returns -2800 on a no-rail arm. The
      # cached configuration is the source of truth for the installed axes; fall
      # back to the constructor hint before setup has resolved it.
      has_rail = self._configuration.has_rail if self._configuration is not None else self._has_rail
      free_axes = [Axis.BASE, Axis.SHOULDER, Axis.ELBOW, Axis.WRIST]
      if has_rail:
        free_axes.append(Axis.RAIL)
    for axis in free_axes:
      await self.send_command(f"freemode {axis}")

  @evented_operation(
    "precise_flex.stop_freedrive",
    lambda self: {"device": _controller_reference(self)},
  )
  async def stop_freedrive_mode(self) -> None:
    """Exit freedrive mode for all axes."""
    await self.send_command("freemode -1")

  @evented_operation(
    "precise_flex.halt",
    lambda self: {"device": _controller_reference(self)},
  )
  async def halt(self):
    """Stops the current robot immediately but leaves power on."""
    await self.send_command("halt")

  # -- gripper primitives -------------------------------------------------------------------

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

  async def _request_grip_close_pos(self) -> float:
    """Get the gripper close position for the servoed gripper.

    Returns:
      float: The current gripper close position.
    """
    data = await self.send_command("GripClosePos")
    return float(data)

  async def _set_grip_close_pos(self, close_position: float) -> None:
    """Set the gripper close position for the servoed gripper.

    The close position may be changed by a force-controlled grip operation.

    Args:
      close_position: The new gripper close position.
    """
    await self.send_command(f"GripClosePos {close_position}")

  async def _request_grip_open_pos(self) -> float:
    """Get the gripper open position for the servoed gripper.

    Returns:
      float: The current gripper open position.
    """
    data = await self.send_command("GripOpenPos")
    return float(data)

  async def _set_grip_open_pos(self, open_position: float) -> None:
    """Set the gripper open position for the servoed gripper.

    Args:
      open_position: The new gripper open position.
    """
    await self.send_command(f"GripOpenPos {open_position}")

  async def _request_grasp_data(self) -> tuple[float, float, float]:
    """Get the data to be used for the next force-controlled PickPlate command grip operation.

    Returns:
      A tuple containing (plate_width_mm, finger_speed_pct, grasp_force)
    """
    data = await self.send_command("GraspData")
    parts = data.split()
    if len(parts) != 3:
      raise PreciseFlexError(-1, "Unexpected response format from GraspData command.")
    return (float(parts[0]), float(parts[1]), float(parts[2]))

  async def _set_grasp_data(
    self, plate_width: float, finger_speed_pct: float, grasp_force: float
  ) -> None:
    """Set the data to be used for the next force-controlled PickPlate command grip operation.

    This data remains in effect until the next GraspData command or the system is restarted.

    Args:
      plate_width: The plate width in mm.
      finger_speed_pct: The finger speed during grasp as a percentage (0-100). 100 = full speed.
      grasp_force: The gripper squeezing force, in Newtons.
      A positive value indicates the fingers must close to grasp.
      A negative value indicates the fingers must open to grasp.

    Raises:
      ValueError: If finger_speed_pct is not between 0 and 100.
    """
    if not 0 <= finger_speed_pct <= 100:
      raise ValueError(f"finger_speed_pct must be between 0 and 100, got {finger_speed_pct}")
    await self.send_command(f"GraspData {plate_width} {finger_speed_pct} {grasp_force}")

  async def _set_grip_detail(self, access: Optional[StationAccess] = None):
    """Tell the controller how to reach the pick/place station and back out of it."""
    access = access or StationAccess()
    await self.send_command(
      f"StationType {self.location_index} {1 if access.approach == 'vertical' else 0} 0 "
      f"{access.clearance} {access.z_above} {access.grasp_offset}"
    )

  def _mm_to_firmware_units(self, width_mm: float) -> float:
    """Convert a jaw width (mm) to the firmware's native position unit.

    Anchored at :attr:`closed_gripper_position`, which is the firmware value
    when the jaws are at :attr:`min_gripper_width`. Slope is 1 (1 mm = 1 unit).
    """
    return self.closed_gripper_position + (width_mm - self.min_gripper_width)

  # -- rail primitives ----------------------------------------------------------------------

  async def _set_rail_position(self, station_id: int, rail_position: float) -> None:
    """Set the rail position for the specified station.

    Args:
      station_id: The station index.
      rail_position: The rail position in mm.
    """
    await self.send_command(f"Rail {station_id} {rail_position}")

  async def _move_rail(self, station_id: Optional[int] = None, mode: int = 1) -> None:
    """Move the rail to the position stored at the specified station.

    Args:
      station_id: The station index whose rail position to move to.
      mode: Motion mode (0 = normal).
    """
    if station_id is not None:
      await self.send_command(f"MoveRail {station_id} {mode}")
    else:
      await self.send_command(f"MoveRail {mode}")

  # -- identity & status reads --------------------------------------------------------------

  async def request_manufacturer(self) -> str:
    return (await self.request_parameter(DataID.MANUFACTURER)).strip()

  async def request_controller_model(self) -> str:
    return (await self.request_parameter(DataID.CONTROLLER_MODEL)).strip()

  async def request_hardware_version(self) -> str:
    return (await self.request_parameter(DataID.HARDWARE_VERSION)).strip()

  async def request_gpl_version(self) -> str:
    """Controller firmware/runtime version (distinct from ``request_version``, the TCS app)."""
    return (await self.request_parameter(DataID.GPL_VERSION)).strip()

  async def request_controller_serial(self) -> str:
    return (await self.request_parameter(DataID.CONTROLLER_SERIAL)).strip()

  async def request_robot_name(self) -> str:
    return (await self.request_parameter(DataID.ROBOT_NAME)).strip()

  async def request_robot_type(self) -> int:
    """Built-in kinematic model id (PF400 = 12)."""
    return int(_parse_scalar(await self.request_parameter(DataID.ROBOT_TYPE)))

  async def request_axis_count(self) -> int:
    """Number of servoed axes."""
    return int(_parse_scalar(await self.request_parameter(DataID.NUM_AXES)))

  async def request_extra_axis_count(self) -> int:
    """Number of non-servoed (extra) axes."""
    return int(_parse_scalar(await self.request_parameter(DataID.EXTRA_AXES)))

  async def request_axis_mask(self) -> int:
    """Capability/option bit field (rail, dual gripper, ...)."""
    return int(_parse_scalar(await self.request_parameter(DataID.AXIS_MASK)))

  async def request_version(self) -> str:
    """Get the current version of TCS and any installed plug-ins.

    Returns:
      str: The current version information.
    """
    return await self.send_command("version")

  # -- kinematics & reference limits --------------------------------------------------------

  async def request_joint_limits(self, hard: bool = False) -> Dict[Axis, tuple[float, float]]:
    """Per-axis travel limits as {Axis: (min, max)}.

    Returns the soft limits by default; pass ``hard=True`` for the hard limits.
    """
    min_id = DataID.HARD_LIMIT_MIN if hard else DataID.SOFT_LIMIT_MIN
    max_id = DataID.HARD_LIMIT_MAX if hard else DataID.SOFT_LIMIT_MAX
    return _zip_axis_ranges(
      _parse_per_axis(await self.request_parameter(min_id)),
      _parse_per_axis(await self.request_parameter(max_id)),
    )

  async def request_reference_speed(self) -> Dict[Axis, float]:
    """Per-axis rated speed at 100%; J1/J5 in mm/s, J2-J4 in deg/s."""
    return _parse_per_axis(await self.request_parameter(DataID.REFERENCE_SPEED))

  async def request_reference_acceleration(self) -> Dict[Axis, float]:
    """Per-axis rated acceleration at 100%."""
    return _parse_per_axis(await self.request_parameter(DataID.REFERENCE_ACCEL))

  async def request_link_lengths(self) -> tuple[float, float]:
    """(l1, l2) SCARA link lengths in mm: shoulder->elbow, elbow->wrist."""
    per_axis = _parse_per_axis(await self.request_parameter(DataID.LINK_LENGTHS))
    return per_axis[Axis.SHOULDER], per_axis[Axis.ELBOW]

  async def request_tool_length(self) -> float:
    """Wrist->TCP distance in mm (z of the tool-offset transform)."""
    values = [float(v) for v in (await self.request_parameter(DataID.TOOL_OFFSET)).split(",")]
    return values[2]

  async def request_kinematic_parameters(self) -> "kinematics.PF400Params":
    """Build PF400Params from the controller's stored geometry.

    Link lengths and tool length come from the device; gripper_z_offset is not on
    the controller, so it is carried over from the constructor params.
    """
    l1, l2 = await self.request_link_lengths()
    return dataclasses.replace(
      self._kinematics_params,
      l1=l1,
      l2=l2,
      gripper_length=await self.request_tool_length(),
    )

  async def request_reference_cartesian_speed(self) -> float:
    """Rated Cartesian (translational) speed at 100%, in mm/s."""
    return _parse_scalar(await self.request_parameter(DataID.REFERENCE_CARTESIAN_SPEED))

  async def request_reference_cartesian_acceleration(self) -> float:
    """Rated Cartesian (translational) acceleration at 100%, in mm/s^2."""
    return _parse_scalar(await self.request_parameter(DataID.REFERENCE_CARTESIAN_ACCEL))

  async def request_max_speed_percent(self) -> float:
    """Global cap on the speed percentage (one value, applies to all joints)."""
    return _parse_scalar(await self.request_parameter(DataID.MAX_SPEED_PERCENT))

  async def request_max_acceleration_percent(self) -> float:
    """Global cap on the acceleration percentage (one value, applies to all joints)."""
    return _parse_scalar(await self.request_parameter(DataID.MAX_ACCEL_PERCENT))

  async def request_max_deceleration_percent(self) -> float:
    """Global cap on the deceleration percentage (one value, applies to all joints)."""
    return _parse_scalar(await self.request_parameter(DataID.MAX_DECEL_PERCENT))

  # -- tool & base frame --------------------------------------------------------------------

  async def request_base(self) -> tuple[float, float, float, float]:
    """Get the robot base offset.

    Returns:
      A tuple containing (x_offset, y_offset, z_offset, z_rotation)
    """
    data = await self.send_command("base")
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
    await self.send_command(f"base {x_offset} {y_offset} {z_offset} {z_rotation}")

  async def request_tool_transformation_values(
    self,
  ) -> tuple[float, float, float, float, float, float]:
    """Get the current tool transformation values.

    Returns:
      A tuple containing (X, Y, Z, yaw, pitch, roll) for the tool transformation.
    """
    data = await self.send_command("tool")
    if data.startswith("tool: "):
      data = data[6:]
    parts = data.split()
    if len(parts) != 6:
      raise PreciseFlexError(-1, "Unexpected response format from tool command.")
    x, y, z, yaw, pitch, roll = self._parse_xyz_response(parts)
    return (x, y, z, yaw, pitch, roll)

  async def _set_tool_transformation_values(
    self, x: float, y: float, z: float, yaw: float, pitch: float, roll: float
  ) -> None:
    """Set the robot tool transformation (private).

    Private because the client kinematics read the tool once at setup into the frozen configuration;
    changing it live desyncs `request_gripper_pose` from the controller's `wherec` until the
    configuration is rebuilt. The robot must be attached to set the tool, and setting it pauses any
    robot motion in progress.

    Args:
      x: Tool X coordinate.
      y: Tool Y coordinate.
      z: Tool Z coordinate.
      yaw: Tool yaw rotation.
      pitch: Tool pitch rotation.
      roll: Tool roll rotation.
    """
    await self.send_command(f"tool {x} {y} {z} {yaw} {pitch} {roll}")

  # -- robot selection ----------------------------------------------------------------------

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

  async def request_selected_robot(self) -> int:
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

  # -- configuration discovery & adoption ---------------------------------------------------

  @property
  def configuration(self) -> "PreciseFlexConfiguration":
    """The device configuration resolved at setup. Raises before setup()."""
    if self._configuration is None:
      raise RuntimeError("Configuration is not available until setup() has run.")
    return self._configuration

  async def _request_configuration(self) -> "PreciseFlexConfiguration":
    """Read the controller's identity, axes, limits, kinematics, and envelope.

    Read-only (no motion, no homing required), so it is safe to call at setup.
    Link lengths and tool length are read from the controller; per-arm flags are
    derived from the joint set, the axis mask, and the model name.
    """
    soft_limits = await self.request_joint_limits()
    axis_mask = await self.request_axis_mask()
    robot_name = await self.request_robot_name()
    name_tokens = robot_name.split()
    suffix = name_tokens[-1].upper().lstrip("0123456789") if name_tokens else ""
    # The version command reports the TCS app version then its loaded modules.
    tcs_version, *modules = (seg.strip() for seg in (await self.request_version()).split(","))

    # Combine the per-axis 100% references with the global percent caps into the
    # effective per-joint maxima, so consumers get usable limits, not raw factors.
    reference_speed = await self.request_reference_speed()
    reference_acceleration = await self.request_reference_acceleration()
    speed_pct = await self.request_max_speed_percent()
    acceleration_pct = await self.request_max_acceleration_percent()
    deceleration_pct = await self.request_max_deceleration_percent()

    # Kinematics: read the link/tool geometry from the controller by default, so
    # the driver is correct for whichever 400 variant is plugged in; fall back to
    # the constructor params if the read fails or the override is set.
    kinematics_source: Literal["device", "provided", "default"]
    if self._read_kinematics_from_device:
      try:
        kinematic_params = await self.request_kinematic_parameters()
        kinematics_source = "device"
      except Exception as exc:
        logger.warning(
          "[PreciseFlex %s] could not read kinematics, using constructor params: %s",
          self.io._host,
          exc,
        )
        kinematic_params = self._kinematics_params
        kinematics_source = "default"
    else:
      kinematic_params = self._kinematics_params
      kinematics_source = "provided"
    reach_class = kinematics._classify_pf400_reach((kinematic_params.l1, kinematic_params.l2))
    if reach_class == "unknown":
      logger.warning(
        "[PreciseFlex %s] link lengths l1=%.1f l2=%.1f match neither the standard %s nor "
        "extended %s PF400 arm; the arm's device-stored link lengths may have been changed",
        self.io._host,
        kinematic_params.l1,
        kinematic_params.l2,
        kinematics.ARM_LINKS_STANDARD,
        kinematics.ARM_LINKS_EXTENDED,
      )

    return PreciseFlexConfiguration(
      manufacturer=await self.request_manufacturer(),
      controller_model=await self.request_controller_model(),
      hardware_version=await self.request_hardware_version(),
      gpl_version=await self.request_gpl_version(),
      controller_serial=await self.request_controller_serial(),
      robot_name=robot_name,
      robot_type=await self.request_robot_type(),
      tcs_version=tcs_version,
      modules=tuple(modules),
      num_axes=await self.request_axis_count(),
      extra_axes=await self.request_extra_axis_count(),
      axis_mask=axis_mask,
      soft_limits=soft_limits,
      hard_limits=await self.request_joint_limits(hard=True),
      max_joint_speed={a: v * speed_pct / 100 for a, v in reference_speed.items()},
      max_joint_acceleration={
        a: v * acceleration_pct / 100 for a, v in reference_acceleration.items()
      },
      max_joint_deceleration={
        a: v * deceleration_pct / 100 for a, v in reference_acceleration.items()
      },
      max_cartesian_speed=(await self.request_reference_cartesian_speed()) * speed_pct / 100,
      max_cartesian_acceleration=(await self.request_reference_cartesian_acceleration())
      * acceleration_pct
      / 100,
      power_state=await self.request_system_state(),
      kinematics=kinematic_params,
      kinematics_source=kinematics_source,
      has_rail=Axis.RAIL in soft_limits,
      is_dual_gripper=bool(axis_mask & 0x80),
      is_vision_gripper=suffix[:1] == "V",
      reach_class=reach_class,
    )

  async def _request_state(
    self,
  ) -> tuple[JointPose, PreciseFlexCartesianPose]:
    """Single-query snapshot of joint state and the derived Cartesian pose."""
    joints = await self.request_joint_position()
    pose = kinematics.fk(joints, self._kinematics_params)
    # PF400 gripper stays level: pitch=90, roll=-180.
    pose = dataclasses.replace(pose, rotation=Rotation(x=-180, y=90, z=pose.rotation.yaw))
    return joints, pose

  def _adopt_configuration(self, config: "PreciseFlexConfiguration") -> None:
    """Adopt the discovered configuration as the source of truth for later commands.

    The gripper width limits come from the gripper-axis soft limits, IK/FK use the
    device link lengths, and the rail / dual-gripper command paths follow the axes
    the controller actually reports.
    """
    gmin, gmax = config.gripper_width_range
    self._gripper_soft_min, self._gripper_soft_max = gmin, gmax
    self.min_gripper_width, self.max_gripper_width = gmin, gmax
    self._kinematics_params = config.kinematics
    self._has_rail = config.has_rail
    self._is_dual_gripper = config.is_dual_gripper

  def _assess_configuration(self, config: "PreciseFlexConfiguration") -> None:
    """Warn about an unsupported model, a missing TCS module, or an untested combo.

    The kinematics is the PreciseFlex 400 geometry, so a different model would get
    wrong joint targets; a missing module (e.g. PARobot) is the usual ``-2805``
    cause; an unlisted full configuration is allowed but flagged for reporting.
    """
    host = self.io._host
    if not is_supported_model(config.robot_type):
      logger.warning(
        "[PreciseFlex %s] robot_type %s is not a model this driver's kinematics "
        "supports (%s); move_to/work_envelope may be wrong.",
        host,
        config.robot_type,
        ", ".join(SUPPORTED_ROBOT_TYPES.values()),
      )
    for module, provides, project in missing_required_modules(config.modules):
      logger.warning(
        "[PreciseFlex %s] the '%s' module (%s) is not loaded; install the '%s' TCS "
        "project (obtain it from Brooks Automation) and restart it.",
        host,
        module,
        provides,
        project,
      )
    if not is_confirmed(config.robot_type, config.gpl_version, config.tcs_version, config.modules):
      logger.info(
        "[PreciseFlex %s] this software stack has not been tested with this driver. "
        "If the arm works correctly, please add the following entry to "
        "CONFIRMED_FIRMWARE_VERSIONS in pylabrobot/brooks/confirmed_firmware_versions.py "
        "and open a pull request so other users benefit:\n%s",
        host,
        suggest_entry(config.robot_type, config.gpl_version, config.tcs_version, config.modules),
      )

  def _log_configuration_summary(self, config: "PreciseFlexConfiguration") -> None:
    """Log a single structured summary of the discovered device: name, connection,
    firmware, this unit's configuration, and the resulting capabilities."""
    io = self.io
    axes = f"{config.num_axes} axes" + (" + rail" if config.has_rail else "")
    grippers = [
      label
      for present, label in (
        (config.is_dual_gripper, "dual gripper"),
        (config.is_vision_gripper, "vision gripper"),
      )
      if present
    ]
    gripper_note = (", " + ", ".join(grippers)) if grippers else ""
    logger.info(
      "[%s] Connected on %s:%s\n"
      "  Firmware: GPL %s, TCS %s\n"
      "  Configuration: %s, robot_type %s, %s%s\n"
      "  Capabilities: %s reach (l1=%.1f, l2=%.1f mm), modules: %s",
      config.robot_name or config.controller_model or "PreciseFlex",
      io._host,
      io._port,
      config.gpl_version,
      config.tcs_version,
      config.controller_model,
      config.robot_type,
      axes,
      gripper_note,
      config.reach_class,
      config.kinematics.l1,
      config.kinematics.l2,
      ", ".join(config.modules),
    )

  # -- homing & range recovery --------------------------------------------------------------

  # Axes auto-recovered when out of range, in a deliberately safe order: the
  # gripper jaw first (no arm motion), then the Z column (vertical clearance), then
  # the rotary links shoulder -> elbow (smallest swept volume last to first).
  # The wrist is intentionally absent: rotating it back to +/-180 can self-collide, so
  # it needs the other links first driven to minimal clearance from the origin - a
  # maneuver not implemented here. The rail (gross lateral travel) is likewise left out.
  # TODO: clearance-aware wrist recovery (and rail). An out-of-range wrist or rail is
  # left for the setup post-condition to raise on.
  _RECOVERY_ORDER = (Axis.GRIPPER, Axis.BASE, Axis.SHOULDER, Axis.ELBOW)

  async def recover_axes_within_limits(
    self, speed_pct: float = 20.0, max_distance: Optional[float] = 5.0
  ) -> Dict[Axis, float]:
    """Bring out-of-range axes back inside their soft limits, one axis at a time.

    While an axis is outside its soft limit the controller rejects every commanded
    coordinated move (-1012), and homing does not help on the absolute rotary axes.
    A single-axis move is the documented exception: it may move an axis toward the
    in-range region. Each recoverable offender is driven to just inside its nearest
    soft limit, slowly, waiting for each to finish, in :attr:`_RECOVERY_ORDER`.

    Args:
      speed_pct: Profile speed for the recovery moves (default 20%, deliberately slow).
      max_distance: only move an axis that is out of range by at most this much (deg
        for the rotary axes, mm for base/gripper). An axis further out is left in place:
        a large unattended single-axis sweep risks a collision, so it is left for the
        caller to recover manually (e.g. by freedriving). Pass None to move regardless.

    Returns:
      The axes moved, as ``axis -> recovered target``. Empty when nothing recoverable
      is out of range or the configuration was not discovered. The wrist and rail are
      never auto-recovered (see :attr:`_RECOVERY_ORDER`).
    """
    outside = self._axes_outside_soft_limits(await self.request_joint_position())
    if not outside:
      return {}
    prior_speed = await self._request_speed()
    await self._set_speed(speed_pct)
    recovered: Dict[Axis, float] = {}
    try:
      for axis in self._RECOVERY_ORDER:
        if axis not in outside:
          continue
        value, (lo, hi) = outside[axis]
        above = value > hi  # which limit is violated; both moves below hinge on this
        overshoot = (value - hi) if above else (lo - value)
        if max_distance is not None and overshoot > max_distance:
          continue  # too far out to move unattended; left for the post-condition to raise
        # Land just inside the violated limit, toward the in-range region. Clamp the
        # 1-unit margin to half the range so the target stays within [lo, hi] even if
        # the range is narrower than the margin (degenerate, but keeps direction sound).
        margin = min(1.0, (hi - lo) / 2.0)
        target = (hi - margin) if above else (lo + margin)
        logger.warning(
          "[PreciseFlex %s] recovering %s from %s into soft limit [%s, %s] -> %s",
          self.io._host,
          axis.name,
          value,
          lo,
          hi,
          target,
        )
        await self._move_one_axis(axis, target)
        await self._wait_for_eom()
        recovered[axis] = target
    finally:
      await self._set_speed(prior_speed)  # don't leave the profile at the slow recovery speed
    return recovered

  async def _is_robot_homed(self) -> bool:
    """Whether all axes are homed (DataID 2800).

    Homing is lost on every power cycle (incremental encoders), and until it is redone
    the controller blocks commanded motion (-1021) and reports unreliable positions.
    """
    return _parse_scalar(await self.request_parameter(DataID.ROBOT_HOMED)) == 1.0

  async def _handle_out_of_range_axes(self) -> None:
    """Warn about every out-of-range axis, then correct what is recoverable, or raise.

    An axis out of range (its current position outside its soft limit) makes the arm unusable - the
    controller rejects every commanded move with -1012. Setup logs the full set first (either way),
    then, with ``recover_out_of_range`` on (the default), drives each recoverable offender back into
    range. If recovery is off or leaves any axis out, setup raises with explicit recovery steps
    rather than leaving a dead arm.

    No-op until the robot is homed: an unhomed incremental axis reads a meaningless ~0
    (so the check would false-positive), and the controller blocks the recovery move with
    -1021 anyway. Homing is the prerequisite, so the check waits for it.
    """
    if not await self._is_robot_homed():
      logger.warning(
        "[PreciseFlex %s] robot not homed; skipping the out-of-range check until it is "
        "(home() first - unhomed positions are unreliable and commanded moves are blocked).",
        self.io._host,
      )
      return

    outside = self._axes_outside_soft_limits(await self.request_joint_position())
    if not outside:
      return
    logger.warning(
      "[PreciseFlex %s] axes out of soft limit at setup: %s",
      self.io._host,
      self._fmt_axes(outside),
    )
    if self._recover_out_of_range:
      await self.recover_axes_within_limits()
      outside = self._axes_outside_soft_limits(await self.request_joint_position())
    if outside:
      raise OutOfRangeOfMotionError(
        f"axis outside its soft limit after setup: {self._fmt_axes(outside)}. The controller rejects all "
        f"commanded moves in this state. Recover with recover_axes_within_limits(), or freedrive "
        f"the axis back into range manually (required for the wrist, or when an axis is far past "
        f"its limit).",
        axes=outside,
      )

  def _axes_outside_soft_limits(self, joints: JointPose) -> Dict[Axis, tuple]:
    """Axes whose value lies outside their soft limit, as ``axis -> (value, (lo, hi))``.

    Iterates the soft-limit set (keyed by :class:`Axis`) and looks each axis up in
    ``joints`` so the comparison stays Axis-typed. Empty until the configuration has
    been discovered.
    """
    if self._configuration is None:
      return {}
    outside: Dict[Axis, tuple] = {}
    for axis, (lo, hi) in self._configuration.soft_limits.items():
      value = joints.get(axis)
      if value is not None and not (lo <= value <= hi):
        outside[axis] = (value, (lo, hi))
    return outside

  @staticmethod
  def _fmt_axes(axes: Dict[Axis, tuple]) -> str:
    """Format ``{axis: (value, (lo, hi))}`` for logs/errors, e.g.
    ``BASE at 0.959 (soft limit (1.5, 401.5))``."""
    return "; ".join(
      f"{axis.name} at {value} (soft limit {limit})" for axis, (value, limit) in axes.items()
    )

  def _assert_within_soft_limits(self, current: JointPose, target: JointPose) -> None:
    """Guard a commanded move. The controller rejects every move with -1012 while an axis is out of
    range - whether that is the *current* pose or the commanded *target*. They are distinct failures
    with distinct types:

    - an axis whose *current* position is out of range is a recoverable arm *state* (e.g. it lost
      power and drifted past its limit) -> ``OutOfRangeOfMotionError``, which the caller can recover
      and retry. Homing will not fix it (the rotary axes are absolute); call
      ``recover_axes_within_limits()`` to drive it back into range.
    - an axis whose *target* is out of range is a bad request (freedrive can hand-move an axis past a
      soft limit, so a taught pose can land outside the commandable envelope) -> ``ValueError``;
      re-teach the pose.

    No-op until the configuration is discovered.
    """
    out_of_range = self._axes_outside_soft_limits(current)
    if out_of_range:
      raise OutOfRangeOfMotionError(
        f"axis out of range: {self._fmt_axes(out_of_range)}. The controller rejects every commanded "
        f"move while an axis is out of range. Homing will not recover it (the rotary axes are "
        f"absolute); call recover_axes_within_limits() to drive it back into range.",
        axes=out_of_range,
      )
    for axis, (value, limit) in self._axes_outside_soft_limits(target).items():
      raise ValueError(
        f"{axis.name} target {value} is outside its soft limit {limit}; the controller "
        f"would reject the move (-1012). Re-teach this pose within the envelope."
      )

  async def _guarded_move_j(self, build_target: Callable[[JointPose], JointPose]) -> None:
    """The single guarded path to the raw ``_move_j`` primitive: read the live pose, check it and
    the target against the soft limits, send the move, and on out-of-range recover once and retry.
    Both ``move_to_joint_position`` (a partial spec merged over the live pose) and
    ``move_to_location`` (a full pose from IK) funnel through here, so no commanded move reaches
    ``_move_j`` unchecked.

    ``build_target`` maps the freshly-read pose to the full target joints - the only part that
    differs between the two callers. It re-runs each attempt, so a recovery move that shifts an
    unspecified axis is reflected in the next merge.

    When an axis is out of range the controller blocks the move (-1012). With ``recover_out_of_range``
    set, this drives the offending axes back into range once (``recover_axes_within_limits``) and
    retries; otherwise the ``OutOfRangeOfMotionError`` propagates. Recovery uses ``_move_one_axis``, a
    different primitive, so it cannot recurse here.
    """

    async def attempt() -> None:
      current = await self.request_joint_position()
      target = build_target(current)
      self._assert_within_soft_limits(current, target)
      await self._move_j(profile_index=self.profile_index, joint_coords=target)

    try:
      await attempt()
    except OutOfRangeOfMotionError as exc:
      if not self._recover_out_of_range:
        raise
      host = self.io._host
      logger.warning(
        "[PreciseFlex %s] commanded move blocked - %s; auto-recovery on -> recovering and retrying",
        host,
        self._fmt_axes(exc.axes),
      )
      await self.recover_axes_within_limits()
      try:
        await attempt()  # re-reads the live pose - recovery just moved an axis
      except OutOfRangeOfMotionError as exc2:
        logger.error(
          "[PreciseFlex %s] auto-recovery did not clear %s - freedrive/manual recovery needed",
          host,
          self._fmt_axes(exc2.axes),
        )
        raise
      logger.info("[PreciseFlex %s] out-of-range axes recovered; move retried successfully", host)

  # -- joint-space motion -------------------------------------------------------------------

  async def request_joint_position(self) -> JointPose:
    """Get the current joint position of the arm."""
    await self._wait_for_eom()
    num_tries = 2
    for _ in range(num_tries):
      data = await self.send_command("wherej")
      parts = data.split()
      if len(parts) > 0:
        break
    else:
      raise PreciseFlexError(-1, "Unexpected response format from wherej command.")
    return self._parse_angles_response(parts)

  @evented_operation(
    "precise_flex.move_to_joint_position",
    lambda self, position, speed_pct=None: {
      "device": _controller_reference(self),
      "target_joint_position": _joint_pose_reference(position),
      "speed_pct": speed_pct,
    },
  )
  async def move_to_joint_position(
    self,
    position: JointPose,
    speed_pct: Optional[float] = None,
  ) -> None:
    """Move the arm to the specified joint position. A partial spec is merged over the live pose;
    the move is guarded against out-of-range axes (see ``_guarded_move_j``).

    Args:
      position: Target joint pose. Omitted axes keep their live values.
      speed_pct: Movement speed override as a percentage (0-100). If None, uses the current
        speed setting.
    """
    if speed_pct is not None:
      await self._set_speed(speed_pct)
    await self._guarded_move_j(lambda current: {**current, **position})

  async def move_one_axis(
    self,
    axis: Axis,
    position: float,
    speed_pct: Optional[float] = None,
  ) -> None:
    """Move one axis to an absolute position, leaving every other axis where it is.

    Guarded like any other commanded move. Not to be confused with ``_move_one_axis``,
    which skips the guard on purpose so it can recover an axis the controller has
    already blocked.

    Args:
      axis: The axis to move.
      position: Absolute target for that axis.
      speed_pct: Movement speed override as a percentage (0-100). If None, uses the
        current speed setting.
    """
    await self.move_to_joint_position({axis: position}, speed_pct=speed_pct)

  async def move_one_axis_relative(
    self,
    axis: Axis,
    distance: float,
    speed_pct: Optional[float] = None,
  ) -> None:
    """Shift one axis by ``distance`` from where it is now, leaving the others alone.

    The offset is applied to the pose read inside the guarded move rather than to a
    position read beforehand, so it cannot act on a stale reading. If the first
    attempt is blocked and recovery shifts the axis, the retry offsets from the
    recovered position, which is what a relative move should mean.

    Args:
      axis: The axis to move.
      distance: Signed offset to apply to that axis, in the axis's own units.
      speed_pct: Movement speed override as a percentage (0-100). If None, uses the
        current speed setting.
    """
    if speed_pct is not None:
      await self._set_speed(speed_pct)
    await self._guarded_move_j(lambda current: {**current, axis: current[axis] + distance})

  async def request_gripper_pose(self) -> PreciseFlexCartesianPose:
    """Get the current pose using our kinematics model (no firmware `wherec`)."""
    _, pose = await self._request_state()
    return pose

  # -- cartesian motion ---------------------------------------------------------------------

  @evented_operation(
    "precise_flex.move_to_location",
    lambda self, location, direction, speed_pct=None, orientation=None, wrist=None, rail_position=None: {
      "device": _controller_reference(self),
      "target": _cartesian_target_reference(
        location,
        direction,
        orientation=orientation,
        wrist=wrist,
        rail_position=rail_position,
      ),
      "speed_pct": speed_pct,
    },
  )
  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    speed_pct: Optional[float] = None,
    orientation: Optional[ElbowOrientation] = None,
    wrist: Optional[Wrist] = None,
    rail_position: Optional[float] = None,
  ) -> None:
    """Move the arm to the specified Cartesian location. The IK target is guarded against
    out-of-range axes (see ``_guarded_move_j``).

    Args:
      location: Target Cartesian location.
      direction: Approach direction, applied as the pose's z rotation in degrees.
      speed_pct: Movement speed override as a percentage (0-100). If None, uses the current
        speed setting.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration.
      wrist: Wrist configuration. If None, the robot picks the closest configuration.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
    """
    if speed_pct is not None:
      await self._set_speed(speed_pct)

    if rail_position is not None:
      await self.move_rail(rail_position)
    elif self._has_rail:
      raise ValueError(
        "Rail position must be specified for move_to_location when using a rail-equipped arm."
      )

    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(x=-180, y=90, z=direction),
      orientation=orientation,
      wrist=wrist,
    )
    joints = await self._cart_to_joints(coords)
    await self._guarded_move_j(lambda _current: joints)

  async def _plan_cartesian_pose_route(
    self, poses: Sequence[PreciseFlexCartesianPose]
  ) -> List[JointPose]:
    """Plan a Cartesian pose route into joint targets, snapshotting state once.

    Unlike :meth:`_cart_to_joints`, this does not query the controller for every waypoint: it
    reads the current state once and resolves each waypoint's IK from the previous waypoint's
    result. Omitted pose fields inherit from the previous pose so IK branch selection remains
    continuous across the route.
    """
    prev_joints, prev_pose = await self._request_state()
    targets: List[JointPose] = []
    for pose in poses:
      cart = dataclasses.replace(
        pose,
        orientation=prev_pose.orientation if pose.orientation is None else pose.orientation,
        wrist=prev_pose.wrist if pose.wrist is None else pose.wrist,
        # PF400 IK expects a shoulder/reference rail position even on rail-less arms.
        # Mirror _cart_to_joints(): omitted pose fields inherit from the previous pose.
        rail_position=prev_pose.rail_position if pose.rail_position is None else pose.rail_position,
      )
      ik_joints = _snap_to_current(
        kinematics.ik(cart, p=self._kinematics_params),
        prev_joints,
        cart.wrist,
      )
      # IK only solves the arm axes; gripper and rail keep the previous values.
      target = dict(prev_joints)
      for axis in (Axis.BASE, Axis.SHOULDER, Axis.ELBOW, Axis.WRIST):
        target[axis] = ik_joints[axis]
      if self._has_rail and cart.rail_position is not None:
        target[Axis.RAIL] = cart.rail_position

      self._assert_within_soft_limits(prev_joints, target)
      targets.append(target)
      prev_joints = target
      prev_pose = cart
    return targets

  @evented_operation(
    "precise_flex.move_through_cartesian_poses",
    lambda self, poses, speed_pct=None, blend=True: {
      "device": _controller_reference(self),
      "waypoint_count": len(poses),
      "start_target": (
        _cartesian_target_reference(
          poses[0].location,
          poses[0].rotation.z,
          orientation=poses[0].orientation,
          wrist=poses[0].wrist,
          rail_position=poses[0].rail_position,
        )
        if poses
        else None
      ),
      "end_target": (
        _cartesian_target_reference(
          poses[-1].location,
          poses[-1].rotation.z,
          orientation=poses[-1].orientation,
          wrist=poses[-1].wrist,
          rail_position=poses[-1].rail_position,
        )
        if poses
        else None
      ),
      "speed_pct": speed_pct,
      "blend": blend,
    },
  )
  async def move_through_cartesian_poses(
    self,
    poses: Sequence[PreciseFlexCartesianPose],
    speed_pct: Optional[float] = None,
    blend: bool = True,
  ) -> None:
    """Move through a sequence of Cartesian poses using one planned IK route.

    The standard Cartesian move path snapshots the current state for each waypoint,
    which waits for end-of-motion between moves. For taught air-transit routes, this
    method snapshots state once, plans each subsequent IK target from the previous
    planned target, queues the joint moves, and waits only after the final waypoint.

    This is a PreciseFlex-specific primitive: intermediate waypoints may be blended
    by the controller and should not be used for operations that require an exact
    stop, gripper action, or physical contact at every pose.

    Args:
      poses: Cartesian waypoints to move through, in order.
      speed_pct: Movement speed override as a percentage (0-100). If None, uses the
        current speed setting.
      blend: When True, temporarily set the active motion profile's ``InRange`` value to
        ``-1`` so the controller may blend through intermediate waypoints instead of stopping
        at each one. The original profile is restored after the final waypoint is reached.
    """
    if not poses:
      return
    if speed_pct is not None:
      await self._set_speed(speed_pct)

    targets = await self._plan_cartesian_pose_route(poses)

    profile_index = self.profile_index
    original_profile = None
    should_restore_profile = False
    if blend:
      original_profile = await self.request_motion_profile_values(profile_index)
      should_restore_profile = original_profile.in_range != BLEND_IN_RANGE
      if should_restore_profile:
        await self.set_motion_profile_values(*original_profile._replace(in_range=BLEND_IN_RANGE))

    try:
      for target in targets:
        await self._move_j(profile_index=profile_index, joint_coords=target)
    finally:
      # Let queued motion settle before returning or restoring the profile - restoring InRange
      # mid-move would change the in-flight blend.
      try:
        await self._wait_for_eom()
      finally:
        if should_restore_profile and original_profile is not None:
          await self.set_motion_profile_values(*original_profile)

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

  async def dest_j(self, arg1: int = 0) -> JointPose:
    """Get the destination or current joint location of the robot.

    Args:
      arg1: Selects return value. Defaults to 0.
      0 = Return current joint location if robot is not moving
      1 = Return target joint location of the previous or current move

    Returns:
      A dict mapping Axis to float values.
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
    return self._parse_angles_response(parts)

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

  # -- gripper ------------------------------------------------------------------------------

  # Physical jaw range for the PF400 servoed gripper. Overridden at setup from the
  # gripper-axis soft limits (DataIDs 16078/16077, Axis.GRIPPER) when discoverable.
  min_gripper_width: float = 60.0
  max_gripper_width: float = 145.0
  # Gripper-axis soft limits (GripOpenPos/GripClosePos units), read at setup; None until then.
  _gripper_soft_min: Optional[float] = None
  _gripper_soft_max: Optional[float] = None

  @evented_operation(
    "precise_flex.move_gripper",
    lambda self, width, force_sensing=False: {
      "device": _controller_reference(self),
      "width": float(width),
      "force_sensing": force_sensing,
    },
  )
  async def move_gripper(
    self,
    width: float,
    force_sensing: bool = False,
  ):
    """Move the PreciseFlex gripper jaws.

    ``force_sensing=False`` drives to the open position (``gripper 1``);
    ``force_sensing=True`` drives to the close position with force feedback
    (``gripper 2``), which may stop short of ``width`` on contact.

    Not interruptible: the ``gripper`` firmware command blocks the controller's command interpreter
    until the jaws finish (hardware-verified, like ``waitForEom``), so a user interrupt cannot halt it
    mid-travel - it is intentionally not wrapped by the motion-wait interrupt guard. The move is short
    and force-limited, so this is a documented limitation rather than a hazard.
    """
    logger.info(
      "[PreciseFlex %s] move_gripper: width_mm=%s force_sensing=%s",
      self.io._host,
      width,
      force_sensing,
    )
    units = self._mm_to_firmware_units(width)
    if (
      self._gripper_soft_min is not None
      and self._gripper_soft_max is not None
      and not (self._gripper_soft_min <= units <= self._gripper_soft_max)
    ):
      raise ValueError(
        f"gripper width {width} mm maps to firmware units {units:.1f}, outside the gripper "
        f"axis range [{self._gripper_soft_min}, {self._gripper_soft_max}] - check "
        f"closed_gripper_position (currently {self.closed_gripper_position})."
      )
    if force_sensing:
      await self._set_grip_close_pos(units)
      await self.send_command("gripper 2")
    else:
      await self._set_grip_open_pos(units)
      await self.send_command("gripper 1")

  @evented_operation(
    "precise_flex.move_gripper_joint_position",
    lambda self, position, force_sensing=False: {
      "device": _controller_reference(self),
      "gripper_joint_position": float(position),
      "force_sensing": force_sensing,
    },
  )
  async def move_gripper_joint_position(
    self,
    position: float,
    force_sensing: bool = False,
  ) -> None:
    """Move the gripper to a controller-native joint position.

    This is the counterpart to :meth:`move_gripper` for integrations with
    taught joint-space routes. The caller owns the joint calibration.
    """
    if force_sensing:
      await self._set_grip_close_pos(position)
      await self.send_command("gripper 2")
    else:
      await self._set_grip_open_pos(position)
      await self.send_command("gripper 1")

  async def is_gripper_closed(self) -> bool:
    """(Single Gripper Only) Tests if the gripper is fully closed by checking the end-of-travel sensor.

    Returns:
      For standard gripper: True if the gripper is within 2mm of fully closed, otherwise False.
    """
    if self._is_dual_gripper:
      raise ValueError("IsGripperClosed command is only valid for single gripper robots.")
    response = await self.send_command("IsFullyClosed")
    return int(response) == -1

  async def are_grippers_closed(self) -> tuple[bool, bool]:
    """(Dual Gripper Only) Tests if each gripper is fully closed by checking the end-of-travel sensors."""
    if not self._is_dual_gripper:
      raise ValueError("AreGrippersClosed command is only valid for dual gripper robots.")
    response = await self.send_command("IsFullyClosed")
    ret_int = int(response)
    gripper_1_closed = (ret_int & 1) != 0
    gripper_2_closed = (ret_int & 2) != 0
    return (gripper_1_closed, gripper_2_closed)

  # -- rail ---------------------------------------------------------------------------------

  @evented_operation(
    "precise_flex.move_rail",
    lambda self, rail_position: {
      "device": _controller_reference(self),
      "rail_position": float(rail_position),
    },
  )
  async def move_rail(self, rail_position: float) -> None:
    """Move the rail to the specified position.

    Args:
      rail_position: Rail destination in mm.

    Raises:
      RuntimeError: If the arm does not have a rail.
    """
    if not self._has_rail:
      raise RuntimeError("This arm does not have a rail.")
    await self._set_rail_position(self._rail_position_index, rail_position)
    await self._move_rail(station_id=self._rail_position_index)

  # -- pick & place -------------------------------------------------------------------------

  @evented_operation(
    "precise_flex.pick_up_at_joint_position",
    lambda self, position, resource_width, finger_speed_pct=50.0, grasp_force=10.0: {
      "device": _controller_reference(self),
      "target_joint_position": _joint_pose_reference(position),
      "resource_width": float(resource_width),
      "finger_speed_pct": float(finger_speed_pct),
      "grasp_force": float(grasp_force),
    },
  )
  async def pick_up_at_joint_position(
    self,
    position: JointPose,
    resource_width: float,
    finger_speed_pct: float = 50.0,
    grasp_force: float = 10.0,
    access: Optional[StationAccess] = None,
  ) -> None:
    """Pick up at the specified joint position.

    Args:
      position: Joint pose to pick from.
      resource_width: Width of the resource to grasp, in mm.
      finger_speed_pct: Finger closing speed as a percentage (0-100).
      grasp_force: Grasp force in Newtons.
    """
    logger.info(
      "[PreciseFlex %s] pick_up: joints=%s, resource_width_mm=%s",
      self.io._host,
      position,
      resource_width,
    )
    await self._set_grasp_data(
      plate_width=resource_width,
      finger_speed_pct=finger_speed_pct,
      grasp_force=grasp_force,
    )
    await self._pick_plate_j(position, access)

  @evented_operation(
    "precise_flex.drop_at_joint_position",
    lambda self, position, resource_width: {
      "device": _controller_reference(self),
      "target_joint_position": _joint_pose_reference(position),
      "resource_width": float(resource_width),
    },
  )
  async def drop_at_joint_position(
    self,
    position: JointPose,
    resource_width: float,
    access: Optional[StationAccess] = None,
  ) -> None:
    """Drop at the specified joint position.

    Args:
      position: Joint pose to drop at.
      resource_width: Width of the held resource, in mm.
    """
    logger.info(
      "[PreciseFlex %s] drop: joints=%s, resource_width_mm=%s",
      self.io._host,
      position,
      resource_width,
    )
    await self._place_plate_j(position, access)

  @evented_operation(
    "precise_flex.pick_up_at_location",
    lambda self, location, direction, resource_width, finger_speed_pct=50.0, grasp_force=10.0, orientation=None, wrist=None, rail_position=None: {
      "device": _controller_reference(self),
      "target": _cartesian_target_reference(
        location,
        direction,
        orientation=orientation,
        wrist=wrist,
        rail_position=rail_position,
      ),
      "resource_width": float(resource_width),
      "finger_speed_pct": float(finger_speed_pct),
      "grasp_force": float(grasp_force),
    },
  )
  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    finger_speed_pct: float = 50.0,
    grasp_force: float = 10.0,
    orientation: Optional[ElbowOrientation] = None,
    wrist: Optional[Wrist] = None,
    rail_position: Optional[float] = None,
    access: Optional[StationAccess] = None,
  ) -> None:
    """Pick up at the specified Cartesian location.

    Args:
      location: Cartesian location to pick from.
      direction: Approach direction, applied as the pose's z rotation in degrees.
      resource_width: Width of the resource to grasp, in mm.
      finger_speed_pct: Finger closing speed as a percentage (0-100).
      grasp_force: Grasp force in Newtons.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration.
      wrist: Wrist configuration. If None, the robot picks the closest configuration.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
      access: How the arm reaches the station and backs out of it. Defaults to a
        vertical approach with 100 mm clearance and 10 mm of allowance for the plate.
    """
    logger.info(
      "[PreciseFlex %s] pick_up: x=%s, y=%s, z=%s, direction=%s, resource_width_mm=%s",
      self.io._host,
      location.x,
      location.y,
      location.z,
      direction,
      resource_width,
    )
    if rail_position is not None:
      await self.move_rail(rail_position)
    elif self._has_rail:
      raise ValueError(
        "rail_position must be specified for pick_up_at_location when using a rail-equipped arm."
      )
    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(z=direction),
      orientation=orientation,
      wrist=wrist,
    )
    await self._set_grasp_data(
      plate_width=resource_width,
      finger_speed_pct=finger_speed_pct,
      grasp_force=grasp_force,
    )
    await self._pick_plate_c(cartesian_position=coords, access=access)

  @evented_operation(
    "precise_flex.drop_at_location",
    lambda self, location, direction, resource_width, orientation=None, wrist=None, rail_position=None: {
      "device": _controller_reference(self),
      "target": _cartesian_target_reference(
        location,
        direction,
        orientation=orientation,
        wrist=wrist,
        rail_position=rail_position,
      ),
      "resource_width": float(resource_width),
    },
  )
  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    orientation: Optional[ElbowOrientation] = None,
    wrist: Optional[Wrist] = None,
    rail_position: Optional[float] = None,
    access: Optional[StationAccess] = None,
  ) -> None:
    """Drop at the specified Cartesian location.

    Args:
      location: Cartesian location to drop at.
      direction: Approach direction, applied as the pose's z rotation in degrees.
      resource_width: Width of the held resource, in mm.
      orientation: Elbow orientation (``"lefty"`` or ``"righty"``). If None, the robot
        picks the closest configuration.
      wrist: Wrist configuration. If None, the robot picks the closest configuration.
      rail_position: Linear rail position in mm. Required when the arm has a rail.
      access: How the arm reaches the station and backs out of it. Defaults to a
        vertical approach with 100 mm clearance and 10 mm of allowance for the plate.
    """
    logger.info(
      "[PreciseFlex %s] drop: x=%s, y=%s, z=%s, direction=%s, resource_width_mm=%s",
      self.io._host,
      location.x,
      location.y,
      location.z,
      direction,
      resource_width,
    )
    if rail_position is not None:
      await self.move_rail(rail_position)
    elif self._has_rail:
      raise ValueError(
        "rail_position must be specified for drop_at_location when using a rail-equipped arm."
      )
    coords = PreciseFlexCartesianPose(
      location=location,
      rotation=Rotation(z=direction),
      orientation=orientation,
      wrist=wrist,
    )
    await self._place_plate_c(cartesian_position=coords, access=access)

  async def _pick_plate_j(
    self, joint_position: JointPose, access: Optional[StationAccess] = None
  ):
    """Pick a plate from the specified position using joint coordinates."""
    await self._set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail(access)
    horizontal_compliance_int = 1 if self.horizontal_compliance else 0
    ret_code = await self.send_command(
      f"pickplate {self.location_index} {horizontal_compliance_int} {self.horizontal_compliance_torque}"
    )
    if ret_code == "0":
      raise PreciseFlexError(-1, "the force-controlled gripper detected no plate present.")

  async def _place_plate_j(
    self, joint_position: JointPose, access: Optional[StationAccess] = None
  ):
    """Place a plate at the specified position using joint coordinates."""
    await self._set_joint_angles(self.location_index, joint_position)
    await self._set_grip_detail(access)
    horizontal_compliance_int = 1 if self.horizontal_compliance else 0
    await self.send_command(
      f"placeplate {self.location_index} {horizontal_compliance_int} {self.horizontal_compliance_torque}"
    )

  async def _pick_plate_c(
    self, cartesian_position: PreciseFlexCartesianPose,
    access: Optional[StationAccess] = None,
  ):
    """Pick a plate at a Cartesian position via IK + joint-space pickplate."""
    joints = await self._cart_to_joints(cartesian_position)
    await self._pick_plate_j(joints, access)

  async def _place_plate_c(
    self, cartesian_position: PreciseFlexCartesianPose,
    access: Optional[StationAccess] = None,
  ):
    """Place a plate at a Cartesian position via IK + joint-space placeplate."""
    joints = await self._cart_to_joints(cartesian_position)
    await self._place_plate_j(joints, access)

  # -- parking ------------------------------------------------------------------------------

  @property
  def parking_position(self) -> Optional[JointPose]:
    """The pose ``park()`` moves to. Assign one of the ``PARKING_POSITION_BACK/RIGHT/FRONT`` class
    constants or any JointPose; the assignment is validated (keys must be ``Axis`` members, values must
    be within the soft limits once the configuration is known). None until setup, where it defaults to
    ``PARKING_POSITION_RIGHT``. A pose that omits ``Axis.BASE`` has its Z filled at park time."""
    return self._parking_position

  @parking_position.setter
  def parking_position(self, position: Optional[JointPose]) -> None:
    if position is not None:
      self._validate_parking_position(position)
    self._parking_position: Optional[JointPose] = dict(position) if position is not None else None

  @evented_operation(
    "precise_flex.park",
    lambda self: {"device": _controller_reference(self)},
  )
  async def park(self) -> None:
    """Move to ``self.parking_position``; defaults at setup, reassignable at runtime.

    ``parking_position`` is filled at setup with ``PARKING_POSITION_RIGHT`` (a planar fold facing
    right, Z column at 3/4 of its discovered travel); assign one of the ``PARKING_POSITION_*`` class
    constants or any JointPose to park elsewhere. Falls back to the firmware ``movetosafe`` while it is
    unset. No collision checks against 3rd-party obstacles.
    """
    if self.parking_position is not None:
      await self.move_to_joint_position(
        position=self._parking_pose_with_default_z(self.parking_position)
      )
    else:
      await self.send_command("movetosafe")

  async def move_to_safe(self) -> None:
    """Run the controller's own retraction to its taught safe position.

    This is the firmware ``movetosafe``: a sequence of safe moves the controller plans itself, not a
    single joint target. The pose and the route live in the controller, so neither can be read back
    or checked against the soft limits from here. ``park()`` is the counterpart this driver can
    reason about. No collision checks against 3rd-party obstacles.
    """
    await self.send_command("movetosafe")

  def _validate_parking_position(self, position: JointPose) -> None:
    """Reject anything that is not a JointPose of in-range axes (limits checked once known)."""
    if not isinstance(position, dict) or not position:
      raise ValueError(f"parking_position must be a non-empty JointPose, got {position!r}")
    for axis, value in position.items():
      if not isinstance(axis, Axis):
        raise ValueError(f"parking_position keys must be Axis members, got {axis!r}")
      if not isinstance(value, (int, float)):
        raise ValueError(f"parking_position[{axis.name}] must be a number, got {value!r}")
      if self._configuration is not None:
        lo, hi = self._configuration.soft_limits[axis]
        if not lo <= value <= hi:
          raise ValueError(
            f"parking_position[{axis.name}]={value} is outside the soft limits [{lo}, {hi}]"
          )

  def _parking_pose_with_default_z(self, position: JointPose) -> JointPose:
    """Fill the Z column (``Axis.BASE``) at 3/4 of the discovered travel when the pose omits it."""
    if Axis.BASE in position or self._configuration is None:
      return position
    _, z_max = self._configuration.z_range
    return {Axis.BASE: 0.75 * z_max, **position}
