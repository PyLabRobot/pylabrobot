"""PreciseFlex driver: the controller link, its lifecycle, and the configuration."""

import asyncio
import json
import logging
import warnings
from typing import (
  Any,
  ClassVar,
  Dict,
  Literal,
  Optional,
)

from pylabrobot.brooks.precise_flex import kinematics
from pylabrobot.brooks.precise_flex.driver.configuration import (
  DEVICE_FEATURE_CONFIGURATIONS,
  Axis,
  PreciseFlexConfiguration,
  to_jsonable,
)
from pylabrobot.brooks.precise_flex.kinematics import JointState
from pylabrobot.events import emit_event, evented_operation
from pylabrobot.io.socket import Socket

from ..confirmed_firmware_versions import (
  SUPPORTED_ROBOT_TYPES,
  is_confirmed,
  is_supported_model,
  suggest_entry,
)
from ..data_ids import DataID, PowerState, _parse_scalar
from ..tcs_modules import missing_required_modules
from .errors import PreciseFlexError
from .features.arm import PreciseFlexArm, PreciseFlexArmConfiguration
from .features.gripper import PreciseFlexGripper, PreciseFlexGripperConfiguration
from .features.rail import PreciseFlexRail, PreciseFlexRailConfiguration

logger = logging.getLogger(__name__)


class PreciseFlexDriver:
  """Driver for PreciseFlex robotic arms.

  Owns the Socket I/O connection and device-level operations (power, attach,
  home, response mode).  Exposes ``send_command`` as the generic wire method.

  Documentation and error codes available at
  https://www2.brooksautomation.com/#Root/Welcome.htm
  """

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
    parking_position: Optional[JointState] = None,
  ) -> None:
    """
    Args:
      gripper_length: wrist-axis → TCP distance in mm. Used as the fallback /
        override; when ``read_kinematics_from_device`` is True (the default) the
        link lengths and tool length are read from the controller at setup and
        this value is only used if that read fails.
      gripper_z_offset: vertical offset in mm from the wrist plate to the tool tip.
        Depends on the mounted gripper. Always taken from here (not on the controller).
      read_kinematics_from_device: when True, read l1/l2 and the tool length from
        the controller at setup and use them for kinematics; the constructor's
        ``gripper_length`` then acts only as a fallback. Set False to force the
        constructor values regardless of what the controller reports.
      recover_out_of_range: when True (the default), an out-of-range axis (its current position
        outside its soft limit - a state the controller rejects every commanded move for, -1012) is
        driven back into range once via ``recover_axes_within_limits``, the same way at both moments
        it matters: at setup, and before a commanded move (which then retries). If it is still out
        of range after that, ``OutOfRangeOfMotionError`` propagates (no loop). Set False to forbid
        this autonomous motion - an out-of-range axis then raises instead, carrying recovery
        instructions. Every recovery is logged.
      closed_gripper_position: firmware-unit value (passed to ``GripClosePos`` /
        ``GripOpenPos``) at which the jaws are at the narrow end of
        :attr:`PreciseFlexGripper.jaw_width_range`.
        Depends on the mounted gripper. The conversion mm → firmware units is
        linear with slope 1: ``units = closed_gripper_position + (width_mm -
        jaw_width_range[0])``.
      parking_position: initial value for the public, runtime-settable ``parking_position`` that
        ``park()`` moves to. Leave None (the default) and setup fills the generic default RIGHT pose
        (planar fold, Z column at 3/4 of the discovered travel); reassign it any time to park
        elsewhere. While unset (no configuration), ``park()`` falls back to ``movetosafe``.
    """
    super().__init__()
    self.io = Socket(human_readable_device_name="Precise Flex Arm", host=host, port=port)
    self.timeout = timeout
    # Serializes each request->reply exchange over the single shared controller socket; the
    # rationale (and why it is kept though uncontended today) is in _locked_exchange.
    self._io_lock = asyncio.Lock()
    # Built only on an arm that has a rail; discovery decides at setup.
    self.rail: Optional[PreciseFlexRail] = PreciseFlexRail(self) if has_rail else None
    self.arm = PreciseFlexArm(self)
    self.gripper = PreciseFlexGripper(
      self, closed_gripper_position=closed_gripper_position, is_dual_gripper=is_dual_gripper
    )
    self._kinematics_params = kinematics.PF400Params(
      gripper_length=gripper_length, gripper_z_offset=gripper_z_offset
    )
    self._read_kinematics_from_device = read_kinematics_from_device
    self._recover_out_of_range = recover_out_of_range
    # Device configuration, resolved once at setup; None until then. Set before parking_position so
    # its validating setter can check assignments against the soft limits once they are known.
    self._configuration: Optional[PreciseFlexConfiguration] = None
    # Public and runtime-settable (validated on assignment); setup fills the default RIGHT pose when
    # this is left None.
    self.arm.parking_position = parking_position
    if is_dual_gripper:
      warnings.warn(
        "Dual gripper support is experimental and may not work as expected.", UserWarning
      )

  # -- communication ---------------------------------------------------------

  def _controller_reference(self) -> Dict[str, object]:
    """The controller's identity, as structured execution events name it."""
    return {
      "name": "precise_flex",
      "type": type(self).__name__,
      "host": self.io._host,
      "port": self.io._port,
    }

  async def _locked_exchange(self, command: str) -> str:
    """Write one command and read its one reply line, with the lock held.

    The controller takes one connection (port 10100 refuses a second), and a reply is matched to its
    command only by order, so two interleaved exchanges would read each other's replies. The lock is
    held per exchange, not across a move's wait; the interrupt halt bypasses it (see
    ``_wait_for_eom``). The newline is added here; the reply is decoded and stripped.
    """
    async with self._io_lock:
      await self.io.write(command.encode("utf-8") + b"\n")
      return (await self.io.readline()).decode("utf-8").strip()

  def _ensure_successful(self, reply: str) -> str:
    """Return the data of a ``<code> <data>`` reply, raising unless the code is 0.

    Args:
      reply: one decoded, stripped reply line.

    Returns:
      The line with its leading reply code removed.

    Raises:
      PreciseFlexError: on an empty reply or a non-zero reply code.
    """
    if not reply:
      raise PreciseFlexError(-1, "Empty reply from device.")
    code, _, data = reply.partition(" ")
    replycode = int(code)
    if replycode != 0:
      raise PreciseFlexError(replycode, data)
    return data

  async def send_command(self, command: str) -> str:
    """Send a command and return the accepted ``<code> <data>`` payload.

    Writes the command and reads one reply line (as one locked exchange), then applies the standard
    acceptance gate (``_ensure_successful``): a non-zero reply code raises, otherwise the data
    payload is returned. A reply in a different grammar goes through the same ``_locked_exchange``
    and is parsed by the caller instead.

    The exchange is wrapped in the firmware-command events so a command keeps its enclosing
    operation context.

    Args:
      command: the command line, without its line ending.

    Raises:
      PreciseFlexError: on an empty reply or a non-zero reply code.
    """
    event_data = {
      "device": self._controller_reference(),
      "command": command,
    }
    emit_event("precise_flex.firmware_command.started", **event_data)
    try:
      result = self._ensure_successful(await self._locked_exchange(command))
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

  # -- lifecycle -------------------------------------------------------------

  @evented_operation(
    "precise_flex.setup",
    lambda self, skip_home=False: {
      "device": self._controller_reference(),
      "skip_home": skip_home,
    },
  )
  async def setup(self, skip_home: bool = False):
    """Bring the arm fully up: ``connect``, ``initialize``, then ``home`` unless skipped.

    ``initialize`` reads the configuration, and setup raises if that fails. Axes outside their
    limits are then dealt with.

    Args:
      skip_home: If True, skip the homing step during setup.
    """
    await self.connect()
    await self.initialize()
    if not skip_home:
      await self.home()
    await self.arm._handle_out_of_range_axes()

  async def connect(self) -> None:
    """Open the link and agree the response protocol. Powers nothing, moves nothing."""
    await self.io.setup()
    await self.set_response_mode("pc")
    logger.debug("[PreciseFlex %s] connected: port=%s", self.io._host, self.io._port)

  async def discover(self) -> "PreciseFlexConfiguration":
    """Read the controller's configuration and adopt it. No motion.

    Raises if the read fails: without it IK solves for another arm and the gripper has no limits.

    Returns:
      The configuration, also kept as ``configuration``.
    """
    self._configuration = await self._request_configuration()
    self._adopt_configuration(self._configuration)
    if self.arm.parking_position is None:
      self.arm.parking_position = self.arm.PARKING_POSITION_RIGHT
    self._log_configuration_summary(self._configuration)
    self._assess_configuration(self._configuration)
    return self._configuration

  async def initialize(self) -> "PreciseFlexConfiguration":
    """Raise high power, take control, end any freedrive, and read the configuration (`discover`).

    Moves nothing. Homing is ``home()``, deliberately separate: it sweeps the arm through its
    whole envelope, which is not something to do just to bring it up.

    Returns:
      The configuration ``discover`` read.
    """
    await self.power_on_robot()
    await self.attach(1)
    await self.arm.stop_freedrive_mode()
    return await self.discover()

  async def disconnect(self) -> None:
    """Hand the arm back and close the links. Moves nothing.

    Drops high power as well as releasing the arm, because ``initialize`` raised it.
    """
    await self.detach()
    await self.power_off_robot()
    await self._exit()
    await self.io.stop()
    logger.info("[PreciseFlex %s] disconnected: port=%s", self.io._host, self.io._port)

  @evented_operation(
    "precise_flex.stop",
    lambda self: {"device": self._controller_reference()},
  )
  async def stop(self):
    """Hand the arm back and close the link (``disconnect``)."""
    await self.disconnect()

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
    mapping: Dict[int, "PreciseFlexDriver.ResponseMode"] = {0: "pc", 1: "verbose"}
    return mapping[int(response)]

  async def set_response_mode(self, mode: ResponseMode) -> None:
    """Set the response mode.

    Args:
      mode: ``"pc"`` or ``"verbose"``; sent as 0 or 1.

    Raises:
      ValueError: If ``mode`` is neither.

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
      attach_state: If omitted, returns the attachment state.  0 = release the robot; 1 = attach the
        robot.

    Returns:
      If attach_state is omitted, the number of the robot attached (1 on a single-robot
      controller), or 0 if none is. Otherwise 0 on success.

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
    lambda self: {"device": self._controller_reference()},
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

  @evented_operation(
    "precise_flex.power_on",
    lambda self: {"device": self._controller_reference()},
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
    "precise_flex.power_off",
    lambda self: {"device": self._controller_reference()},
  )
  async def power_off_robot(self):
    """Power off the robot."""
    await self.set_power(False)

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

    Raises:
      ValueError: If only some of `unit_number`, `sub_unit` and `array_index` are given; ``pc``
        takes all three or none.

    Note:
      Updated values are not saved in flash unless a save-to-flash operation
      is performed (see DataID 901).
    """
    given = [argument is not None for argument in (unit_number, sub_unit, array_index)]
    if any(given) and not all(given):
      raise ValueError("unit_number, sub_unit and array_index are given together or not at all")
    if all(given):
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
    """Change one joint's element of a per-axis parameter array (``pc``); the rest are untouched.

    Args:
      data_id: the per-axis DataID to change.
      axis: which joint's element to write; sent as the controller's 1-based index (gripper is 5).
      value: the new value for that element.
      robot_number: unit number, the robot (1 - N_ROB).

    Note:
      Volatile until a save-to-flash (DataID 901); a power cycle restores the flashed value.
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
  def _has_rail(self) -> bool:
    return self.rail is not None

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
    derived from the joint set, the axis mask, and the camera count.
    """
    soft_limits = await self.arm.request_joint_limits()
    axis_mask = await self.request_axis_mask()
    robot_name = await self.request_robot_name()
    # The version command reports the TCS app version then its loaded modules.
    tcs_version, *modules = (seg.strip() for seg in (await self.request_version()).split(","))
    # A vision gripper is its cameras: counted through the controller, where its vision module is
    # loaded. An arm that will not say has none.
    camera_count = 0
    if any("intelliguide" in module.lower() for module in modules):
      try:
        camera_count = max(0, int(await self._locked_exchange("VToolProperty System CameraCount")))
      except Exception as exc:
        logger.warning(
          "[PreciseFlex %s] the controller did not say how many cameras it has: %s",
          self.io._host,
          exc,
        )

    # Combine the per-axis 100% references with the global percent caps into the
    # effective per-joint maxima, so consumers get usable limits, not raw factors.
    reference_speed = await self.arm.request_reference_speed()
    reference_acceleration = await self.arm.request_reference_acceleration()
    speed_percent = await self.arm.request_max_speed_percent()
    acceleration_percent = await self.arm.request_max_acceleration_percent()
    deceleration_percent = await self.arm.request_max_deceleration_percent()

    # Link and tool lengths are read from the controller, so any 400 variant is right; the
    # constructor's are used if the read fails or is switched off.
    kinematics_source: Literal["device", "provided", "default"]
    if self._read_kinematics_from_device:
      try:
        kinematic_params = await self.arm.request_kinematic_parameters()
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

    manufacturer = await self.request_manufacturer()
    controller_model = await self.request_controller_model()
    hardware_version = await self.request_hardware_version()
    gpl_version = await self.request_gpl_version()
    controller_serial = await self.request_controller_serial()
    robot_type = await self.request_robot_type()
    num_axes = await self.request_axis_count()
    extra_axes = await self.request_extra_axis_count()
    hard_limits = await self.arm.request_joint_limits(hard=True)
    max_joint_speed = {a: v * speed_percent / 100 for a, v in reference_speed.items()}
    max_joint_acceleration = {
      a: v * acceleration_percent / 100 for a, v in reference_acceleration.items()
    }
    max_joint_deceleration = {
      a: v * deceleration_percent / 100 for a, v in reference_acceleration.items()
    }
    max_cartesian_speed = (await self.arm.request_reference_cartesian_speed()) * speed_percent / 100
    max_cartesian_acceleration = (
      (await self.arm.request_reference_cartesian_acceleration()) * acceleration_percent / 100
    )
    power_state = await self.request_system_state()

    arm_axes = (Axis.BASE, Axis.SHOULDER, Axis.ELBOW, Axis.WRIST)
    rail = None
    if Axis.RAIL in soft_limits:
      rail = PreciseFlexRailConfiguration(
        soft_limit_range=soft_limits[Axis.RAIL],
        hard_limit_range=hard_limits.get(Axis.RAIL),
        max_speed=max_joint_speed.get(Axis.RAIL),
        max_acceleration=max_joint_acceleration.get(Axis.RAIL),
        max_deceleration=max_joint_deceleration.get(Axis.RAIL),
      )
    return PreciseFlexConfiguration(
      manufacturer=manufacturer,
      controller_model=controller_model,
      hardware_version=hardware_version,
      gpl_version=gpl_version,
      controller_serial=controller_serial,
      robot_name=robot_name,
      robot_type=robot_type,
      tcs_version=tcs_version,
      modules=tuple(modules),
      num_axes=num_axes,
      extra_axes=extra_axes,
      axis_mask=axis_mask,
      arm=PreciseFlexArmConfiguration(
        soft_limits={a: v for a, v in soft_limits.items() if a in arm_axes},
        hard_limits={a: v for a, v in hard_limits.items() if a in arm_axes},
        max_joint_speed={a: v for a, v in max_joint_speed.items() if a in arm_axes},
        max_joint_acceleration={a: v for a, v in max_joint_acceleration.items() if a in arm_axes},
        max_joint_deceleration={a: v for a, v in max_joint_deceleration.items() if a in arm_axes},
        max_cartesian_speed=max_cartesian_speed,
        max_cartesian_acceleration=max_cartesian_acceleration,
        kinematics=kinematic_params,
        kinematics_source=kinematics_source,
      ),
      gripper=PreciseFlexGripperConfiguration(
        soft_limit_range=soft_limits[Axis.GRIPPER],
        hard_limit_range=hard_limits[Axis.GRIPPER],
        max_speed=max_joint_speed[Axis.GRIPPER],
        max_acceleration=max_joint_acceleration[Axis.GRIPPER],
        max_deceleration=max_joint_deceleration[Axis.GRIPPER],
        is_dual_gripper=bool(axis_mask & 0x80),
      ),
      rail=rail,
      camera_count=camera_count,
      _power_state=power_state,
    )

  def _adopt_configuration(self, config: "PreciseFlexConfiguration") -> None:
    """Adopt the discovered configuration as the source of truth for later commands.

    The gripper width limits come from the gripper-axis soft limits, IK/FK use the
    device link lengths, and the rail / dual-gripper command paths follow the axes
    the controller actually reports.
    """
    self.arm.configuration = config.arm
    self.gripper._adopt_configuration(config.gripper)
    self._kinematics_params = config.arm.kinematics
    self.rail = (self.rail or PreciseFlexRail(self)) if config.has_rail else None
    if self.rail is not None:
      self.rail.configuration = config.rail

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
        "CONFIRMED_FIRMWARE_VERSIONS in "
        "pylabrobot/brooks/precise_flex/confirmed_firmware_versions.py "
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
        (config.gripper.is_dual_gripper, "dual gripper"),
        (config.has_vision_gripper, "vision gripper"),
      )
      if present
    ]
    gripper_note = (", " + ", ".join(grippers)) if grippers else ""
    logger.info(
      "[%s] Connected on %s:%s\n  Firmware: GPL %s, TCS %s\n"
      "  Configuration: %s, robot_type %s, %s%s\n"
      "  Capabilities: %s reach (l1=%.1f, l2=%.1f mm), modules: %s",
      config.robot_name or config.controller_model or "PreciseFlexDriver",
      io._host,
      io._port,
      config.gpl_version,
      config.tcs_version,
      config.controller_model,
      config.robot_type,
      axes,
      gripper_note,
      config.arm.reach_class,
      config.arm.kinematics.l1,
      config.arm.kinematics.l2,
      ", ".join(config.modules),
    )

  # -- configuration system -----------------------------------------------------------------

  def _saved_configuration(self) -> Dict[str, Any]:
    """Every configuration this device holds, shaped as the device is.

    The device's own configuration, then whatever is fitted to it, each under its feature's name.

    Returns:
      What `save_configuration` writes.

    Raises:
      RuntimeError: If nothing has been read off the device yet.
    """
    device = to_jsonable(self.configuration)
    saved: Dict[str, Any] = {
      "device": {n: v for n, v in device.items() if n not in DEVICE_FEATURE_CONFIGURATIONS}
    }
    for name, feature in (("arm", self.arm), ("gripper", self.gripper), ("rail", self.rail)):
      if feature is not None and feature.configuration is not None:
        saved[name] = to_jsonable(feature.configuration)
    return saved

  def save_configuration(self, path: str, indent: Optional[int] = 2) -> None:
    """Write what this device reported to a file, to be declared from later.

    Args:
      path: where to write it.
      indent: how far to indent the JSON, or None to write it on one line.

    Raises:
      RuntimeError: If nothing has been read off the device yet.
    """
    saved = self._saved_configuration()  # before the file is opened, so a refusal leaves none
    with open(path, "w", encoding="utf-8") as f:
      json.dump(saved, f, indent=indent)

  # -- homing & range recovery --------------------------------------------------------------

  async def _is_robot_homed(self) -> bool:
    """Whether all axes are homed (DataID 2800).

    Homing is lost on every power cycle (incremental encoders), and until it is redone
    the controller blocks commanded motion (-1021) and reports unreliable positions.
    """
    return _parse_scalar(await self.request_parameter(DataID.ROBOT_HOMED)) == 1.0

  @evented_operation(
    "precise_flex.recover_from_fault",
    lambda self: {"device": self._controller_reference()},
  )
  async def recover_from_fault(self) -> None:
    """Recover after a collision / fault that stopped the arm and dropped power, leaving it usable.

    A collision trips an envelope error (``-3100`` hard / ``-3122`` soft, see
    :func:`~pylabrobot.brooks.precise_flex.driver.errors.is_collision`); the servo stops the arm
    itself and high power drops. This re-enables power, re-attaches, and re-homes. It does **not**
    drive the arm to any pose; confirm the obstacle is removed before calling.

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

  # -- deprecated: moved to the arm, gripper and rail ---------------------------------------------

  async def request_joint_position(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_joint_state``."""
    warnings.warn(
      "`request_joint_position` is deprecated, use `arm.request_joint_state`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_joint_state(*args, **kwargs)

  async def request_state(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_state``."""
    warnings.warn(
      "`request_state` is deprecated, use `arm.request_state`.", DeprecationWarning, stacklevel=2
    )
    return await self.arm.request_state(*args, **kwargs)

  async def request_gripper_pose(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_gripper_pose``."""
    warnings.warn(
      "`request_gripper_pose` is deprecated, use `arm.request_gripper_pose`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_gripper_pose(*args, **kwargs)

  async def move_to_joint_position(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.move_to_joint_state``."""
    warnings.warn(
      "`move_to_joint_position` is deprecated, use `arm.move_to_joint_state`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "position" in kwargs:
      kwargs["joint_state"] = kwargs.pop("position")
    if "speed_pct" in kwargs:
      kwargs["speed_percent"] = kwargs.pop("speed_pct")
    return await self.arm.move_to_joint_state(*args, **kwargs)

  async def move_to_location(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.move_to_location``."""
    warnings.warn(
      "`move_to_location` is deprecated, use `arm.move_to_location`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "speed_pct" in kwargs:
      kwargs["speed_percent"] = kwargs.pop("speed_pct")
    return await self.arm.move_to_location(*args, **kwargs)

  async def move_through_cartesian_poses(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.move_through_cartesian_poses``."""
    warnings.warn(
      "`move_through_cartesian_poses` is deprecated, use `arm.move_through_cartesian_poses`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "speed_pct" in kwargs:
      kwargs["speed_percent"] = kwargs.pop("speed_pct")
    return await self.arm.move_through_cartesian_poses(*args, **kwargs)

  async def recover_axes_within_limits(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.recover_axes_within_limits``."""
    warnings.warn(
      "`recover_axes_within_limits` is deprecated, use `arm.recover_axes_within_limits`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "speed_pct" in kwargs:
      kwargs["speed_percent"] = kwargs.pop("speed_pct")
    return await self.arm.recover_axes_within_limits(*args, **kwargs)

  async def dest_c(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_destination_joint_state``."""
    warnings.warn(
      "`dest_c` is deprecated: the controller's own Cartesian destination is no longer public. "
      "`arm.request_destination_joint_state` gives the destination, in joints.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "arg1" in kwargs:
      kwargs["mode"] = kwargs.pop("arg1")
    return await self.arm._unchecked_fw_request_cartesian_destination(*args, **kwargs)

  async def dest_j(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_destination_joint_state``."""
    warnings.warn(
      "`dest_j` is deprecated, use `arm.request_destination_joint_state`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "arg1" in kwargs:
      kwargs["mode"] = kwargs.pop("arg1")
    return await self.arm.request_destination_joint_state(*args, **kwargs)

  async def here_j(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_station_to_current_joint_state``."""
    warnings.warn(
      "`here_j` is deprecated, use `arm.set_station_to_current_joint_state`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "location_index" in kwargs:
      kwargs["station_index"] = kwargs.pop("location_index")
    return await self.arm.set_station_to_current_joint_state(*args, **kwargs)

  async def here_c(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_station_to_current_joint_state``."""
    warnings.warn(
      "`here_c` is deprecated: storing a station as the controller's own Cartesian is no longer "
      "public. `arm.set_station_to_current_joint_state` stores the same position, in joints.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "location_index" in kwargs:
      kwargs["station_index"] = kwargs.pop("location_index")
    return await self.arm._unchecked_fw_set_station_to_current_cartesian_location(*args, **kwargs)

  async def move_gripper(self, width: float, force_sensing: bool = False) -> Any:
    """Deprecated: use ``gripper.move_to_jaw_position``."""
    warnings.warn(
      "`move_gripper` is deprecated, use `gripper.move_to_jaw_position`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.gripper.move_to_jaw_position(width, force_sensing=force_sensing)

  async def move_gripper_joint_position(self, position: float, force_sensing: bool = False) -> Any:
    """Deprecated: use ``gripper.move_to_jaw_position_firmware_units``."""
    warnings.warn(
      "`move_gripper_joint_position` is deprecated, use "
      "`gripper.move_to_jaw_position_firmware_units`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.gripper.move_to_jaw_position_firmware_units(
      position, force_sensing=force_sensing
    )

  async def is_gripper_closed(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``gripper.sense_fully_closed``."""
    warnings.warn(
      "`is_gripper_closed` is deprecated, use `gripper.sense_fully_closed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.gripper.sense_fully_closed(*args, **kwargs)

  async def are_grippers_closed(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``gripper.sense_each_fully_closed``."""
    warnings.warn(
      "`are_grippers_closed` is deprecated, use `gripper.sense_each_fully_closed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.gripper.sense_each_fully_closed(*args, **kwargs)

  async def move_rail(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``rail.move_rail``."""
    warnings.warn(
      "`move_rail` is deprecated, use `rail.move_rail`.", DeprecationWarning, stacklevel=2
    )
    return await self.arm._require_rail().move_rail(*args, **kwargs)

  @property
  def min_gripper_width(self) -> float:
    """Deprecated: use ``gripper.jaw_width_range``."""
    warnings.warn(
      "`min_gripper_width` is deprecated, use `gripper.jaw_width_range`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.gripper.jaw_width_range[0]

  @min_gripper_width.setter
  def min_gripper_width(self, value: float) -> None:
    warnings.warn(
      "`min_gripper_width` is deprecated, use `gripper.jaw_width_range`.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.gripper.jaw_width_range = (value, self.gripper.jaw_width_range[1])

  @property
  def max_gripper_width(self) -> float:
    """Deprecated: use ``gripper.jaw_width_range``."""
    warnings.warn(
      "`max_gripper_width` is deprecated, use `gripper.jaw_width_range`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.gripper.jaw_width_range[1]

  @max_gripper_width.setter
  def max_gripper_width(self, value: float) -> None:
    warnings.warn(
      "`max_gripper_width` is deprecated, use `gripper.jaw_width_range`.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.gripper.jaw_width_range = (self.gripper.jaw_width_range[0], value)

  @property
  def closed_gripper_position(self) -> float:
    """Deprecated: use ``gripper.closed_gripper_position``."""
    warnings.warn(
      "`closed_gripper_position` is deprecated, use `gripper.closed_gripper_position`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.gripper.closed_gripper_position

  @closed_gripper_position.setter
  def closed_gripper_position(self, value: float) -> None:
    warnings.warn(
      "`closed_gripper_position` is deprecated, use `gripper.closed_gripper_position`.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.gripper.closed_gripper_position = value

  async def request_monitor_speed(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_monitor_speed``."""
    warnings.warn(
      "`request_monitor_speed` is deprecated, use `arm.request_monitor_speed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_monitor_speed(*args, **kwargs)

  async def set_monitor_speed(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_monitor_speed``."""
    warnings.warn(
      "`set_monitor_speed` is deprecated, use `arm.set_monitor_speed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "speed_pct" in kwargs:
      kwargs["speed_percent"] = kwargs.pop("speed_pct")
    return await self.arm.set_monitor_speed(*args, **kwargs)

  async def request_payload(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_payload``."""
    warnings.warn(
      "`request_payload` is deprecated, use `arm.request_payload`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_payload(*args, **kwargs)

  async def set_payload(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_payload``."""
    warnings.warn(
      "`set_payload` is deprecated, use `arm.set_payload`.", DeprecationWarning, stacklevel=2
    )
    if "payload_pct" in kwargs:
      kwargs["payload_percent"] = kwargs.pop("payload_pct")
    return await self.arm.set_payload(*args, **kwargs)

  async def request_profile_speed(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_profile_speed``."""
    warnings.warn(
      "`request_profile_speed` is deprecated, use `arm.request_profile_speed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_profile_speed(*args, **kwargs)

  async def set_profile_speed(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_profile_speed``."""
    warnings.warn(
      "`set_profile_speed` is deprecated, use `arm.set_profile_speed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "speed_pct" in kwargs:
      kwargs["speed_percent"] = kwargs.pop("speed_pct")
    return await self.arm.set_profile_speed(*args, **kwargs)

  async def request_profile_speed2(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_profile_speed2``."""
    warnings.warn(
      "`request_profile_speed2` is deprecated, use `arm.request_profile_speed2`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_profile_speed2(*args, **kwargs)

  async def set_profile_speed2(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_profile_speed2``."""
    warnings.warn(
      "`set_profile_speed2` is deprecated, use `arm.set_profile_speed2`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "speed2_pct" in kwargs:
      kwargs["speed2_percent"] = kwargs.pop("speed2_pct")
    return await self.arm.set_profile_speed2(*args, **kwargs)

  async def request_profile_acceleration(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_profile_acceleration``."""
    warnings.warn(
      "`request_profile_acceleration` is deprecated, use `arm.request_profile_acceleration`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_profile_acceleration(*args, **kwargs)

  async def set_profile_acceleration(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_profile_acceleration``."""
    warnings.warn(
      "`set_profile_acceleration` is deprecated, use `arm.set_profile_acceleration`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "acceleration_pct" in kwargs:
      kwargs["acceleration_percent"] = kwargs.pop("acceleration_pct")
    return await self.arm.set_profile_acceleration(*args, **kwargs)

  async def request_profile_acceleration_ramp(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_profile_acceleration_ramp``."""
    warnings.warn(
      "`request_profile_acceleration_ramp` is deprecated, use "
      "`arm.request_profile_acceleration_ramp`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_profile_acceleration_ramp(*args, **kwargs)

  async def set_profile_acceleration_ramp(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_profile_acceleration_ramp``."""
    warnings.warn(
      "`set_profile_acceleration_ramp` is deprecated, use `arm.set_profile_acceleration_ramp`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.set_profile_acceleration_ramp(*args, **kwargs)

  async def request_profile_deceleration(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_profile_deceleration``."""
    warnings.warn(
      "`request_profile_deceleration` is deprecated, use `arm.request_profile_deceleration`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_profile_deceleration(*args, **kwargs)

  async def set_profile_deceleration(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_profile_deceleration``."""
    warnings.warn(
      "`set_profile_deceleration` is deprecated, use `arm.set_profile_deceleration`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "deceleration_pct" in kwargs:
      kwargs["deceleration_percent"] = kwargs.pop("deceleration_pct")
    return await self.arm.set_profile_deceleration(*args, **kwargs)

  async def request_profile_deceleration_ramp(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_profile_deceleration_ramp``."""
    warnings.warn(
      "`request_profile_deceleration_ramp` is deprecated, use "
      "`arm.request_profile_deceleration_ramp`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_profile_deceleration_ramp(*args, **kwargs)

  async def set_profile_deceleration_ramp(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_profile_deceleration_ramp``."""
    warnings.warn(
      "`set_profile_deceleration_ramp` is deprecated, use `arm.set_profile_deceleration_ramp`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.set_profile_deceleration_ramp(*args, **kwargs)

  async def request_profile_in_range(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_profile_in_range``."""
    warnings.warn(
      "`request_profile_in_range` is deprecated, use `arm.request_profile_in_range`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_profile_in_range(*args, **kwargs)

  async def set_profile_in_range(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_profile_in_range``."""
    warnings.warn(
      "`set_profile_in_range` is deprecated, use `arm.set_profile_in_range`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.set_profile_in_range(*args, **kwargs)

  async def request_profile_straight(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_profile_straight``."""
    warnings.warn(
      "`request_profile_straight` is deprecated, use `arm.request_profile_straight`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_profile_straight(*args, **kwargs)

  async def set_profile_straight(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_profile_straight``."""
    warnings.warn(
      "`set_profile_straight` is deprecated, use `arm.set_profile_straight`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.set_profile_straight(*args, **kwargs)

  async def request_motion_profile_values(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_motion_profile_values``."""
    warnings.warn(
      "`request_motion_profile_values` is deprecated, use `arm.request_motion_profile_values`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_motion_profile_values(*args, **kwargs)

  async def set_motion_profile_values(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_motion_profile_values``."""
    warnings.warn(
      "`set_motion_profile_values` is deprecated, use `arm.set_motion_profile_values`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "speed_pct" in kwargs:
      kwargs["speed_percent"] = kwargs.pop("speed_pct")
    if "speed2_pct" in kwargs:
      kwargs["speed2_percent"] = kwargs.pop("speed2_pct")
    if "acceleration_pct" in kwargs:
      kwargs["acceleration_percent"] = kwargs.pop("acceleration_pct")
    if "deceleration_pct" in kwargs:
      kwargs["deceleration_percent"] = kwargs.pop("deceleration_pct")
    return await self.arm.set_motion_profile_values(*args, **kwargs)

  @property
  def profile_index(self) -> int:
    """Deprecated: use ``arm.profile_index``."""
    warnings.warn(
      "`profile_index` is deprecated, use `arm.profile_index`.", DeprecationWarning, stacklevel=2
    )
    return self.arm.profile_index

  @profile_index.setter
  def profile_index(self, value: int) -> None:
    warnings.warn(
      "`profile_index` is deprecated, use `arm.profile_index`.", DeprecationWarning, stacklevel=2
    )
    self.arm.profile_index = value

  async def release_brake(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.release_brake``."""
    warnings.warn(
      "`release_brake` is deprecated, use `arm.release_brake`.", DeprecationWarning, stacklevel=2
    )
    return await self.arm.release_brake(*args, **kwargs)

  async def set_brake(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.reengage_brake``."""
    warnings.warn(
      "`set_brake` is deprecated, use `arm.reengage_brake`.", DeprecationWarning, stacklevel=2
    )
    return await self.arm.reengage_brake(*args, **kwargs)

  async def zero_torque(self, enable: bool, axis_mask: int = 1) -> None:
    """Deprecated: use ``arm.start_zero_torque`` or ``arm.stop_zero_torque``."""
    warnings.warn(
      "`zero_torque` is deprecated, use `arm.start_zero_torque` or `arm.stop_zero_torque`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if enable:
      await self.arm.start_zero_torque(axis_mask)
    else:
      await self.arm.stop_zero_torque()

  async def start_freedrive_mode(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.start_freedrive_mode``."""
    warnings.warn(
      "`start_freedrive_mode` is deprecated, use `arm.start_freedrive_mode`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.start_freedrive_mode(*args, **kwargs)

  async def stop_freedrive_mode(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.stop_freedrive_mode``."""
    warnings.warn(
      "`stop_freedrive_mode` is deprecated, use `arm.stop_freedrive_mode`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.stop_freedrive_mode(*args, **kwargs)

  async def halt(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.halt``."""
    warnings.warn("`halt` is deprecated, use `arm.halt`.", DeprecationWarning, stacklevel=2)
    return await self.arm.halt(*args, **kwargs)

  async def change_config(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.change_elbow_orientation``."""
    warnings.warn(
      "`change_config` is deprecated, use `arm.change_elbow_orientation`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.change_elbow_orientation(*args, **kwargs)

  async def change_config2(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.change_elbow_orientation_by_algorithm``."""
    warnings.warn(
      "`change_config2` is deprecated, use `arm.change_elbow_orientation_by_algorithm`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.change_elbow_orientation_by_algorithm(*args, **kwargs)

  async def request_joint_limits(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_joint_limits``."""
    warnings.warn(
      "`request_joint_limits` is deprecated, use `arm.request_joint_limits`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_joint_limits(*args, **kwargs)

  async def request_reference_speed(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_reference_speed``."""
    warnings.warn(
      "`request_reference_speed` is deprecated, use `arm.request_reference_speed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_reference_speed(*args, **kwargs)

  async def request_reference_acceleration(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_reference_acceleration``."""
    warnings.warn(
      "`request_reference_acceleration` is deprecated, use `arm.request_reference_acceleration`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_reference_acceleration(*args, **kwargs)

  async def request_link_lengths(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_link_lengths``."""
    warnings.warn(
      "`request_link_lengths` is deprecated, use `arm.request_link_lengths`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_link_lengths(*args, **kwargs)

  async def request_tool_length(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_tool_length``."""
    warnings.warn(
      "`request_tool_length` is deprecated, use `arm.request_tool_length`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_tool_length(*args, **kwargs)

  async def request_kinematic_parameters(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_kinematic_parameters``."""
    warnings.warn(
      "`request_kinematic_parameters` is deprecated, use `arm.request_kinematic_parameters`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_kinematic_parameters(*args, **kwargs)

  async def request_reference_cartesian_speed(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_reference_cartesian_speed``."""
    warnings.warn(
      "`request_reference_cartesian_speed` is deprecated, use "
      "`arm.request_reference_cartesian_speed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_reference_cartesian_speed(*args, **kwargs)

  async def request_reference_cartesian_acceleration(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_reference_cartesian_acceleration``."""
    warnings.warn(
      "`request_reference_cartesian_acceleration` is deprecated, use "
      "`arm.request_reference_cartesian_acceleration`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_reference_cartesian_acceleration(*args, **kwargs)

  async def request_max_speed_percent(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_max_speed_percent``."""
    warnings.warn(
      "`request_max_speed_percent` is deprecated, use `arm.request_max_speed_percent`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_max_speed_percent(*args, **kwargs)

  async def request_max_acceleration_percent(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_max_acceleration_percent``."""
    warnings.warn(
      "`request_max_acceleration_percent` is deprecated, use "
      "`arm.request_max_acceleration_percent`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_max_acceleration_percent(*args, **kwargs)

  async def request_max_deceleration_percent(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_max_deceleration_percent``."""
    warnings.warn(
      "`request_max_deceleration_percent` is deprecated, use "
      "`arm.request_max_deceleration_percent`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_max_deceleration_percent(*args, **kwargs)

  async def request_base(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_base``."""
    warnings.warn(
      "`request_base` is deprecated, use `arm.request_base`.", DeprecationWarning, stacklevel=2
    )
    return await self.arm.request_base(*args, **kwargs)

  async def set_base(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.set_base``."""
    warnings.warn("`set_base` is deprecated, use `arm.set_base`.", DeprecationWarning, stacklevel=2)
    return await self.arm.set_base(*args, **kwargs)

  async def request_tool_transformation_values(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.request_tool_transformation_values``."""
    warnings.warn(
      "`request_tool_transformation_values` is deprecated, use "
      "`arm.request_tool_transformation_values`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.request_tool_transformation_values(*args, **kwargs)

  async def pick_up_at_joint_position(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.pick_up_at_joint_state``."""
    warnings.warn(
      "`pick_up_at_joint_position` is deprecated, use `arm.pick_up_at_joint_state`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "position" in kwargs:
      kwargs["joint_state"] = kwargs.pop("position")
    if "finger_speed_pct" in kwargs:
      kwargs["finger_speed_percent"] = kwargs.pop("finger_speed_pct")
    return await self.arm.pick_up_at_joint_state(*args, **kwargs)

  async def drop_at_joint_position(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.drop_at_joint_state``."""
    warnings.warn(
      "`drop_at_joint_position` is deprecated, use `arm.drop_at_joint_state`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "position" in kwargs:
      kwargs["joint_state"] = kwargs.pop("position")
    return await self.arm.drop_at_joint_state(*args, **kwargs)

  async def pick_up_at_location(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.pick_up_at_location``."""
    warnings.warn(
      "`pick_up_at_location` is deprecated, use `arm.pick_up_at_location`.",
      DeprecationWarning,
      stacklevel=2,
    )
    if "finger_speed_pct" in kwargs:
      kwargs["finger_speed_percent"] = kwargs.pop("finger_speed_pct")
    return await self.arm.pick_up_at_location(*args, **kwargs)

  async def drop_at_location(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.drop_at_location``."""
    warnings.warn(
      "`drop_at_location` is deprecated, use `arm.drop_at_location`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.arm.drop_at_location(*args, **kwargs)

  async def park(self, *args: Any, **kwargs: Any) -> Any:
    """Deprecated: use ``arm.park``."""
    warnings.warn("`park` is deprecated, use `arm.park`.", DeprecationWarning, stacklevel=2)
    return await self.arm.park(*args, **kwargs)

  @property
  def parking_position(self) -> Optional[JointState]:
    """Deprecated: use ``arm.parking_position``."""
    warnings.warn(
      "`parking_position` is deprecated, use `arm.parking_position`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.arm.parking_position

  @parking_position.setter
  def parking_position(self, value: Optional[JointState]) -> None:
    warnings.warn(
      "`parking_position` is deprecated, use `arm.parking_position`.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.arm.parking_position = value

  @property
  def location_index(self) -> int:
    """Deprecated: use ``arm.station_index``."""
    warnings.warn(
      "`location_index` is deprecated, use `arm.station_index`.", DeprecationWarning, stacklevel=2
    )
    return self.arm.station_index

  @location_index.setter
  def location_index(self, value: int) -> None:
    warnings.warn(
      "`location_index` is deprecated, use `arm.station_index`.", DeprecationWarning, stacklevel=2
    )
    self.arm.station_index = value

  @property
  def horizontal_compliance(self) -> bool:
    """Deprecated: use ``arm.horizontal_compliance``."""
    warnings.warn(
      "`horizontal_compliance` is deprecated, use `arm.horizontal_compliance`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.arm.horizontal_compliance

  @horizontal_compliance.setter
  def horizontal_compliance(self, value: bool) -> None:
    warnings.warn(
      "`horizontal_compliance` is deprecated, use `arm.horizontal_compliance`.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.arm.horizontal_compliance = value

  @property
  def horizontal_compliance_torque(self) -> int:
    """Deprecated: use ``arm.horizontal_compliance_torque``."""
    warnings.warn(
      "`horizontal_compliance_torque` is deprecated, use `arm.horizontal_compliance_torque`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.arm.horizontal_compliance_torque

  @horizontal_compliance_torque.setter
  def horizontal_compliance_torque(self, value: int) -> None:
    warnings.warn(
      "`horizontal_compliance_torque` is deprecated, use `arm.horizontal_compliance_torque`.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.arm.horizontal_compliance_torque = value

  # deprecated: use ``PreciseFlexArm.PARKING_POSITION_*``
  PARKING_POSITION_BACK: ClassVar[JointState] = PreciseFlexArm.PARKING_POSITION_BACK
  PARKING_POSITION_RIGHT: ClassVar[JointState] = PreciseFlexArm.PARKING_POSITION_RIGHT
  PARKING_POSITION_FRONT: ClassVar[JointState] = PreciseFlexArm.PARKING_POSITION_FRONT


class PreciseFlex(PreciseFlexDriver):
  """Deprecated: use ``PreciseFlexDriver``."""

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    warnings.warn(
      "`PreciseFlex` is deprecated, use `PreciseFlexDriver`.", DeprecationWarning, stacklevel=2
    )
    super().__init__(*args, **kwargs)
