import asyncio
import dataclasses
import logging
import math
import time
import warnings
from enum import IntEnum
from typing import Dict, List, Optional, Union

from pylabrobot.capabilities.arms.backend import (
  CanFreedrive,
  HasJoints,
  OrientableGripperArmBackend,
)
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.paa.kx2 import kinematics
from pylabrobot.paa.kx2.config import (
  Axis,
  AxisConfig,
  GripperFingerSide,
  GripperConfig,
  KX2Config,
  ServoGripperConfig,
)
from pylabrobot.paa.kx2.driver import (
  CanError,
  _ElmoObjectDataType,
  _InputLogic,
  _JointMoveDirection,
  KX2Driver,
  _MotorMoveParam,
  _MotorsMovePlan,
)
from pylabrobot.resources import Coordinate, Rotation

logger = logging.getLogger(__name__)


class HomeStatus(IntEnum):
  NotHomed = 0
  Homed = 1
  InitializedWithoutHoming = 2


# Tuple of motion axes — derived from `Axis.is_motion`, kept around because
# iteration sites (setup, halt, freedrive) want a stable ordering.
MOTION_AXES = tuple(a for a in Axis if a.is_motion)


class KX2ArmBackend(OrientableGripperArmBackend, HasJoints, CanFreedrive):
  """Arm-capability backend for the PAA KX2.

  Owns a :class:`KX2Driver` (low-level CAN transport) and implements the
  capability-based arm interface (``OrientableGripperArmBackend`` +
  ``HasJoints`` + ``CanFreedrive``) directly on top of the drive primitives.

  This layer owns all robot-specific procedural logic: the axis -> node-ID
  map, the motion/rail/gripper split for `motor_enable`, homing sequences,
  estop polling, etc. The driver underneath is a pure CAN transport.
  """


  def __init__(
    self,
    driver: KX2Driver,
    gripper_length: float = 0.0,
    gripper_z_offset: float = 0.0,
    gripper_finger_side: GripperFingerSide = "barcode_reader",
  ) -> None:
    super().__init__()
    self.driver = driver
    # Tooling is user-supplied and known at construction; KX2Config (drive-
    # read calibration) doesn't exist until setup runs.
    self._gripper_config = GripperConfig(
      length=float(gripper_length),
      z_offset=float(gripper_z_offset),
      finger_side=gripper_finger_side,
    )
    self._config: Optional[KX2Config] = None
    self._freedrive_axes: List[int] = []

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    # Driver has already brought CAN up (connect + node discovery + PDO
    # mapping) via Device.setup(). Read per-drive config, then enable motion
    # axes and the servo gripper.
    #
    # If anything below this line raises, tear CAN down so a retry can
    # re-init. Otherwise the second setup() trips PcanCanInitializationError
    # because the channel is still half-claimed from the first attempt.
    try:
      self._config = await self._read_config()
      await asyncio.sleep(2)  # let drives settle before motor enables

      # E-stop check: front-load a clear error before motor_enable's retry
      # loop times out with a cryptic message.
      if await self.get_estop_state():
        raise RuntimeError(
          "KX2 setup failed: E-stop is engaged. Twist the red button to "
          "release, then call setup() again. (If the button is out, the "
          "safety-interlock loop or motor-power switch may also be open.)"
        )

      for axis in MOTION_AXES:
        await self.driver.motor_enable(node_id=axis, state=True, use_ds402=True)
      await self.servo_gripper_initialize()
    except BaseException:
      try:
        await self.driver.stop()
      except Exception:
        logger.exception("KX2 setup cleanup: driver.stop() failed; ignoring")
      raise

  # -- robot-level homing / estop (moved from driver) ---------------------

  async def get_estop_state(self) -> bool:
    """Return True if the arm is in estop, False otherwise.

    Reads the shoulder drive's SR (status register) via the binary
    interpreter. Bits 14/15 encode the stop/safety state.
    """
    r = await self.driver.query_int(int(Axis.SHOULDER), "SR", 1)
    if r != 8438016:
      logger.warning("get_estop_state: SR register unexpected value %d (expected 8438016)", r)
    b14 = (r & 0x4000) == 0x4000
    b15 = (r & 0x8000) == 0x8000
    return (not b14) and (not b15)

  async def _motor_set_homed_status(self, axis: Axis, status: HomeStatus) -> None:
    await self.driver.write(axis, "UI", 3, int(status))

  async def motor_get_homed_status(self, axis: Axis) -> HomeStatus:
    return HomeStatus(await self.driver.query_int(axis, "UI", 3))

  async def _motor_reset_encoder_position(self, axis: Axis, position: float) -> None:
    await self.driver.write(axis, "HM", 1, 0)
    await self.driver.write(axis, "HM", 3, 0)
    await self.driver.write(axis, "HM", 4, 0)
    await self.driver.write(axis, "HM", 5, 0)
    # Old code packed `position` as int32 via `int(round(float(str(position))))`;
    # preserve that rounding semantic for callers that pass fractional values.
    await self.driver.write(axis, "HM", 2, int(round(position)))
    await self.driver.write(axis, "HM", 1, 1)

  async def _motor_hard_stop_search(
    self,
    axis: Axis,
    srch_vel: int,
    srch_acc: int,
    max_pe: int,
    hs_pe: int,
    timeout: float,
  ) -> None:
    nid = axis
    await self.driver.write(nid, "ER", 3, max_pe * 10)
    await self.driver.write(nid, "AC", 0, srch_acc)
    await self.driver.write(nid, "DC", 0, srch_acc)
    for i in [3, 4, 5, 2]:
      await self.driver.write(nid, "HM", i, 0)
    await self.driver.write(nid, "JV", 0, srch_vel)

    try:
      params: List[Union[int, float]] = [int(hs_pe), int(timeout * 1000)]
      last_line = await self.driver.user_program_run(
        nid, "Home", params, int(timeout), True
      )
      if last_line in [1, 2, 3]:
        raise RuntimeError(f"Homing Script Error {34 + last_line}")

      curr_pos = await self.driver.motor_get_current_position(nid)
      await self.driver.write(nid, "PA", 0, curr_pos)
      await self.driver.write(nid, "SP", 0, srch_vel)
      await self.driver.write(nid, "AC", 0, srch_acc)
      await self.driver.write(nid, "DC", 0, srch_acc)
    finally:
      await asyncio.sleep(0.3)
      await self.driver.execute(nid, "BG", 0)
      await asyncio.sleep(0.3)
      await self.driver.write(nid, "ER", 3, int(max_pe))

  async def _motor_index_search(
    self,
    axis: Axis,
    srch_vel: int,
    srch_acc: int,
    positive_direction: bool,
    timeout: float,
  ) -> tuple:
    nid = axis
    await self.driver.write(nid, "HM", 1, 0)

    one_revolution = await self.driver.query_int(nid, "CA", 18)
    if not positive_direction:
      one_revolution *= -1

    await self.driver.write(nid, "PR", 1, one_revolution)
    await self.driver.write(nid, "SP", 0, srch_vel)
    await self.driver.write(nid, "AC", 0, srch_acc)
    await self.driver.write(nid, "DC", 0, srch_acc)

    await self.driver.write(nid, "HM", 3, 3)  # index only
    await self.driver.write(nid, "HM", 4, 0)
    await self.driver.write(nid, "HM", 5, 0)
    await self.driver.write(nid, "HM", 2, 0)
    await self.driver.write(nid, "HM", 1, 1)  # arm

    await self.driver.execute(nid, "BG", 0)
    await self.driver.wait_for_moves_done([nid], timeout)

    left = await self.driver.query_int(nid, "HM", 1)
    if left != 0:
      raise RuntimeError("Homing Failure: Failed to finish index pulse search.")

    captured_position = await self.driver.query_int(nid, "HM", 7)
    return one_revolution, captured_position

  async def home_motor(
    self,
    axis: Axis,
    hs_offset: int,
    ind_offset: int,
    home_pos: int,
    srch_vel: int,
    srch_acc: int,
    max_pe: int,
    hs_pe: int,
    offset_vel: int,
    offset_acc: int,
    timeout: float,
  ) -> None:
    nid = axis

    left = await self.driver.query_int(nid, "CA", 41)
    if left == 24:
      raise RuntimeError("Error 43")

    try:
      await self._motor_hard_stop_search(nid, srch_vel, srch_acc, max_pe, hs_pe, timeout)
    except Exception as e:
      fault = await self.driver.motor_get_fault(nid)
      if fault is not None:
        raise RuntimeError(fault)
      raise e

    await self.driver.motor_enable(node_id=nid, state=True, use_ds402=Axis(nid).is_motion)

    await self.motors_move_absolute_execute(
      plan=_MotorsMovePlan(
        moves=[
          _MotorMoveParam(
            node_id=nid,
            position=hs_offset,
            velocity=offset_vel,
            acceleration=offset_acc,
            relative=False,
            direction=_JointMoveDirection.ShortestWay,
          )
        ],
      )
    )

    is_positive = hs_offset > 0
    await self._motor_index_search(nid, abs(srch_vel), srch_acc, is_positive, timeout)

    await self.motors_move_absolute_execute(
      plan=_MotorsMovePlan(
        moves=[
          _MotorMoveParam(
            node_id=nid,
            position=ind_offset,
            velocity=offset_vel,
            acceleration=offset_acc,
            relative=False,
            direction=_JointMoveDirection.ShortestWay,
          )
        ]
      )
    )
    await self._motor_reset_encoder_position(nid, home_pos)
    await self._motor_set_homed_status(nid, HomeStatus.Homed)

  # -- servo gripper ------------------------------------------------------

  async def servo_gripper_initialize(self):
    # Don't swallow motor_enable failures here — homing is the next step
    # and will fault with a confusing "homing failure" error if the motor
    # never came up. Better to surface the real cause.
    await self.driver.motor_enable(
      node_id=int(Axis.SERVO_GRIPPER), state=True, use_ds402=False
    )
    await self.servo_gripper_home()
    await self.servo_gripper_close()

  async def servo_gripper_home(self) -> None:
    sgc = self._cfg.servo_gripper
    if sgc is None:
      raise RuntimeError("Servo gripper not present")
    sg = int(Axis.SERVO_GRIPPER)
    await self.driver.write(sg, "PL", 1, sgc.peak_current)
    await self.driver.write(sg, "CL", 1, sgc.continuous_current)

    await self.home_motor(
      axis=Axis.SERVO_GRIPPER,
      hs_offset=sgc.home_hard_stop_offset,
      ind_offset=sgc.home_index_offset,
      home_pos=sgc.home_pos,
      srch_vel=sgc.home_search_vel,
      srch_acc=sgc.home_search_accel,
      max_pe=sgc.home_default_position_error,
      hs_pe=sgc.home_hard_stop_position_error,
      offset_vel=sgc.home_offset_vel,
      offset_acc=sgc.home_offset_accel,
      timeout=sgc.home_timeout_msec / 1000,
    )

    await self.servo_gripper_set_default_gripping_force(100)

  async def servo_gripper_set_default_gripping_force(self, max_force_percent: int) -> None:
    sgc = self._cfg.servo_gripper
    if sgc is None:
      raise RuntimeError("Servo gripper not present")
    max_force_percent = max(10, min(max_force_percent, 100))

    cont_current = sgc.continuous_current * max_force_percent / 100.0
    peak_current = sgc.peak_current * max_force_percent / 100.0

    sg = int(Axis.SERVO_GRIPPER)

    # 1) PL with unscaled peak current
    await self.driver.write(sg, "PL", 1, sgc.peak_current)

    # 2) CL with scaled continuous current
    await self.driver.write(sg, "CL", 1, cont_current)

    # 3) PL with scaled peak current
    await self.driver.write(sg, "PL", 1, peak_current)

  async def get_servo_gripper_max_force(self) -> float:
    """Return current gripping force as percentage of max (0-1)."""
    sg = int(Axis.SERVO_GRIPPER)
    cl = await self.driver.query_float(sg, "CL", 1)
    iq = await self.driver.query_float(sg, "IQ", 0)

    if cl == 0:
      return 0.0

    return max(0.0, min(abs(iq / cl), 1.0))

  async def check_plate_gripped(self, num_attempts: int = 5) -> None:
    for _ in range(num_attempts):
      motor_status = await self.driver.query_int(
        int(Axis.SERVO_GRIPPER), "MS", 1
      )
      logger.debug("Servo gripper motor status: %s", motor_status)

      if motor_status in {0, 1}:
        max_force_percentage = await self.get_servo_gripper_max_force()
        if max_force_percentage > 90:
          return
        await asyncio.sleep(0.5)
        max_force_percentage = await self.get_servo_gripper_max_force()
        if max_force_percentage > 90:
          return

        current_position = await self.motor_get_current_position(Axis.SERVO_GRIPPER)
        closed_position = 1
        if abs(current_position - closed_position) < 2.0 / 625:
          raise RuntimeError(
            "Servo Gripper was able to move all the way to the closed position, which indicates the absence of an object in the gripper.  The closed position value may need to be decreased."
          )

        return

      elif motor_status == 2:
        motor_fault = await self.driver.motor_get_fault(int(Axis.SERVO_GRIPPER))
        if motor_fault is None:
          raise RuntimeError("Error querying whether plate is gripped. Error querying motor fault.")
        raise RuntimeError(
          f"Servo Gripper may not have gripped the plate correctly. Motor fault: '{motor_fault}'"
        )

      await asyncio.sleep(0.05)

    raise RuntimeError(
      f"Servo Gripper was unable to confirm that the plate is gripped after {num_attempts} attempts."
    )

  async def servo_gripper_close(self, closed_position: int = 0, check_plate_gripped=True) -> None:
    await self.motors_move_joint({Axis.SERVO_GRIPPER: closed_position})

    if check_plate_gripped:
      await self.check_plate_gripped()

  async def servo_gripper_open(self, open_position: float) -> None:
    await self.motors_move_joint({Axis.SERVO_GRIPPER: open_position})

  async def drive_set_move_count_parameters(
    self,
    move_count: int,
    travel: List[float],
    last_maintenance_performed: float,
    maintenance_required: bool,
    last_maintenance_performed_date: int,
    last_maintenance_performed_rail: float,
    maintenance_required_rail: bool,
    last_maintenance_performed_date_rail: int,
  ) -> None:
    z = int(Axis.Z)

    # MoveCount -> Z axis, UI index 22
    await self.driver.write(z, "UI", 22, int(move_count))

    # Travel[] -> each node, UF index 5
    # The source looked 1-based for Travel and 0-based for NodeIDList; handle both cleanly.
    if len(travel) == len(self._cfg.axes) + 1:
      pairs = zip(self._cfg.axes, travel[1:])
    else:
      pairs = zip(self._cfg.axes, travel)

    for node_id, dist in pairs:
      await self.driver.write(node_id, "UF", 5, float(dist))

    # LastMaintenancePerformed -> Z axis, UF index 6
    await self.driver.write(z, "UF", 6, float(last_maintenance_performed))

    # MaintenanceRequired -> Z axis, UI index 23
    await self.driver.write(z, "UI", 23, 1 if maintenance_required else 0)

    # LastMaintenancePerformedDate -> Z axis, UI index 21
    await self.driver.write(z, "UI", 21, int(last_maintenance_performed_date))

    # Rail (if present)
    if self._cfg.robot_on_rail:
      rail = int(Axis.RAIL)
      await self.driver.write(rail, "UF", 6, float(last_maintenance_performed_rail))
      await self.driver.write(rail, "UI", 23, 1 if maintenance_required_rail else 0)
      await self.driver.write(rail, "UI", 21, int(last_maintenance_performed_date_rail))

  async def _read_config(self) -> KX2Config:
    """Read the per-arm configuration from the drives.

    Driver discovery has already populated `node_id_list` with everything
    on the bus; here we just verify the required motion axes are present
    and read each drive's parameters.
    """
    nodes = self.driver.node_id_list
    for required in MOTION_AXES:
      if required not in nodes:
        raise CanError(f"Missing required axis {required}")
    has_rail = Axis.RAIL in nodes
    has_servo_gripper = Axis.SERVO_GRIPPER in nodes
    if has_rail:
      warnings.warn("Rails has not been tested for KX2 robots.")

    axes: Dict[Axis, AxisConfig] = {}
    for nid in nodes:
      axes[Axis(nid)] = await self._read_axis_config(nid)

    sh = int(Axis.SHOULDER)
    return KX2Config(
      wrist_offset=await self.driver.query_float(sh, "UF", 8),
      elbow_offset=await self.driver.query_float(sh, "UF", 9),
      elbow_zero_offset=await self.driver.query_float(sh, "UF", 10),
      axes=axes,
      base_to_gripper_clearance_z=await self.driver.query_float(sh, "UF", 6),
      base_to_gripper_clearance_arm=await self.driver.query_float(sh, "UF", 7),
      robot_on_rail=has_rail,
      servo_gripper=await self._read_servo_gripper_config() if has_servo_gripper else None,
    )

  async def _read_axis_config(self, nid: int) -> AxisConfig:
    logger.debug("Reading parameters for axis %s", nid)

    digital_inputs = await self._read_io_names(nid, 5, 11, _DIGITAL_INPUT_NAMES)
    analog_inputs = await self._read_io_names(nid, 11, 13, {})
    outputs = await self._read_io_names(nid, 13, 17, _OUTPUT_NAMES)

    await self.driver.query_int(nid, "UI", 24)  # serial — read for parity, unused

    uf1 = await self.driver.query_float(nid, "UF", 1)
    uf2 = await self.driver.query_float(nid, "UF", 2)
    if uf1 == 0.0 or uf2 == 0.0:
      raise CanError(f"Invalid motor conversion factor for axis {nid}: UF[1]={uf1}, UF[2]={uf2}")
    motor_conversion_factor = uf1 / uf2

    xm1 = await self.driver.query_int(nid, "XM", 1)
    xm2 = await self.driver.query_int(nid, "XM", 2)
    max_travel = await self.driver.query_float(nid, "UF", 3)
    min_travel = await self.driver.query_float(nid, "UF", 4)
    vh3 = await self.driver.query_int(nid, "VH", 3)
    vl3 = await self.driver.query_int(nid, "VL", 3)

    joint_move_direction = _JointMoveDirection.Normal
    if (xm1 == 0 and xm2 == 0) or (xm1 <= vl3 and xm2 >= vh3):
      unlimited_travel = False
    elif xm1 > vl3 and xm2 < vh3:
      unlimited_travel = True
      if Axis(nid).is_motion:
        joint_move_direction = _JointMoveDirection.ShortestWay
    else:
      raise CanError(
        f"Invalid travel limits or modulo settings for axis {nid}: "
        f"VH[3]={vh3}, VL[3]={vl3}, XM[1]={xm1}, XM[2]={xm2}"
      )

    ca45 = await self.driver.query_int(nid, "CA", 45)
    if not (0 < ca45 <= 4):
      raise CanError(f"Invalid encoder socket for axis {nid}: CA[45]={ca45}")
    enc_type = await self.driver.query_int(nid, "CA", 40 + ca45)
    if enc_type in (1, 2):
      absolute_encoder = False
    elif enc_type == 24:
      absolute_encoder = True
    else:
      raise CanError(f"Unsupported encoder type for axis {nid}: CA[4{ca45}]={enc_type}")

    ca46 = await self.driver.query_int(nid, "CA", 46)
    num3 = 1.0 if ca45 == ca46 else await self.driver.query_float(nid, "FF", 3)
    denom = motor_conversion_factor * num3

    sp2 = await self.driver.query_int(nid, "SP", 2)
    if sp2 == 100000:
      max_vel = await self.driver.query_int(nid, "VH", 2) / 1.01 / denom
    else:
      max_vel = sp2 / denom
    max_accel = await self.driver.query_int(nid, "SD", 0) / 1.01 / denom

    return AxisConfig(
      motor_conversion_factor=motor_conversion_factor,
      max_travel=max_travel,
      min_travel=min_travel,
      unlimited_travel=unlimited_travel,
      absolute_encoder=absolute_encoder,
      max_vel=max_vel,
      max_accel=max_accel,
      joint_move_direction=joint_move_direction,
      digital_inputs=digital_inputs,
      analog_inputs=analog_inputs,
      outputs=outputs,
    )

  async def _read_io_names(
    self, nid: int, start: int, end: int, named: Dict[int, str]
  ) -> Dict[int, str]:
    """Read UI[start..end-1] as a channel -> human name map.

    Channel index is 1-based. Codes in `named` map to fixed labels; positive
    unknowns become "AuxPinN"; non-positive means unassigned.
    """
    out: Dict[int, str] = {}
    for ui_idx in range(start, end):
      code = await self.driver.query_int(nid, "UI", ui_idx)
      ch = ui_idx - start + 1
      if code in named:
        out[ch] = named[code]
      else:
        out[ch] = "" if code <= 0 else f"AuxPin{code}"
    return out

  async def _read_servo_gripper_config(self) -> ServoGripperConfig:
    sg = int(Axis.SERVO_GRIPPER)
    return ServoGripperConfig(
      home_pos=int(await self.driver.query_float(sg, "UF", 6)),
      home_search_vel=int(await self.driver.query_float(sg, "UF", 7)),
      home_search_accel=int(await self.driver.query_float(sg, "UF", 8)),
      home_default_position_error=int(await self.driver.query_float(sg, "UF", 9)),
      home_hard_stop_position_error=int(await self.driver.query_float(sg, "UF", 10)),
      home_hard_stop_offset=int(await self.driver.query_float(sg, "UF", 11)),
      home_index_offset=int(await self.driver.query_float(sg, "UF", 12)),
      home_offset_vel=int(await self.driver.query_float(sg, "UF", 13)),
      home_offset_accel=int(await self.driver.query_float(sg, "UF", 14)),
      home_timeout_msec=int(await self.driver.query_float(sg, "UF", 15)),
      continuous_current=await self.driver.query_float(sg, "UF", 16),
      peak_current=await self.driver.query_float(sg, "UF", 17),
    )

  @property
  def _cfg(self) -> KX2Config:
    if self._config is None:
      raise RuntimeError("KX2 not set up — call setup() first")
    return self._config

  def convert_elbow_position_to_angle(self, elbow_pos: float) -> float:
    max_travel = self._cfg.axes[Axis.ELBOW].max_travel
    denom = max_travel + self._cfg.elbow_zero_offset

    if elbow_pos > max_travel:
      x = (2.0 * max_travel - elbow_pos + self._cfg.elbow_zero_offset) / denom
      angle = math.asin(x) * (180.0 / math.pi)
      elbow_angle = 90.0 + angle
    else:
      x = (elbow_pos + self._cfg.elbow_zero_offset) / denom
      angle = math.asin(x) * (180.0 / math.pi)
      elbow_angle = angle

    return elbow_angle

  def convert_elbow_angle_to_position(self, elbow_angle_deg: float) -> float:
    elbow_pos = (self._cfg.axes[Axis.ELBOW].max_travel + self._cfg.elbow_zero_offset) * math.sin(
      elbow_angle_deg * (math.pi / 180.0)
    ) - self._cfg.elbow_zero_offset

    if elbow_angle_deg > 90.0:
      elbow_pos = 2.0 * self._cfg.axes[Axis.ELBOW].max_travel - elbow_pos

    return elbow_pos

  async def motor_get_current_position(self, axis: Axis) -> float:
    raw = await self.driver.motor_get_current_position(node_id=axis, pu=self._cfg.axes[axis].unlimited_travel)
    c = self._cfg.axes[axis].motor_conversion_factor
    if axis == Axis.ELBOW:
      return self.convert_elbow_angle_to_position(elbow_angle_deg=raw / c)
    else:
      if c == 0:
        logger.warning("Axis %s has conversion factor of 0", axis)
        return 0.0
      else:
        return raw / c

  async def read_input(self, axis: Axis, input_num: int) -> bool:
    return await self.driver.read_input(node_id=axis, input_num=0x10 + input_num)

  # IR breakbeam between the gripper fingers, wired to the Z-drive's IO.
  # True = beam interrupted (object present).
  _PROXIMITY_SENSOR_AXIS: Axis = Axis.Z
  _PROXIMITY_SENSOR_INPUT: int = 4

  async def read_proximity_sensor(self) -> bool:
    return await self.read_input(self._PROXIMITY_SENSOR_AXIS, self._PROXIMITY_SENSOR_INPUT)

  async def wait_for_proximity_sensor(
    self, state: bool = True, timeout: float = 5.0, poll: float = 0.01,
  ) -> bool:
    """Poll until the sensor reads `state`. Returns True on trip, False on timeout."""
    deadline = time.monotonic() + timeout
    while True:
      if await self.read_proximity_sensor() == state:
        return True
      if time.monotonic() >= deadline:
        return False
      await asyncio.sleep(poll)

  async def find_z_with_proximity_sensor(
    self,
    max_descent: float,
    z_start: Optional[float] = None,
    speed: float = 5.0,
    acceleration: float = 50.0,
  ) -> float:
    """Descend Z up to `max_descent`; halt when the IR breakbeam trips.

    `speed` is in mm/s, `acceleration` in mm/s^2. If `z_start` is given,
    first move Z to that height (same speed/accel) and search from there;
    otherwise search from the current Z. Arms IL[4]=StopForward so the Elmo
    drive halts the motor itself on the input edge (sub-ms latency, no
    software in the loop). IL is restored to GeneralPurpose afterwards even
    if the move raises. Returns the Z position where the drive halted;
    raises RuntimeError if the beam never tripped.
    """
    move_params = KX2ArmBackend.JointMoveParams(
      linear_speed=speed, linear_acceleration=acceleration,
    )
    # Pre-flight: force the drive back to Op Enabled. A prior failed search
    # could have left it in Fault/Quick Stop where new moves silently fail
    # (Z barely moves). Idempotent if the drive is already healthy.
    await self.driver.motor_enable(node_id=int(Axis.Z), state=True, use_ds402=True)
    if z_start is not None:
      await self.move_to_joint_position({Axis.Z: z_start}, backend_params=move_params)
    z0 = await self.motor_get_current_position(Axis.Z)
    if await self.read_proximity_sensor():
      raise RuntimeError(
        f"proximity sensor already tripped at start (Z {z0:.2f}); "
        f"clear the gripper or raise z_start before searching"
      )
    await self.driver.configure_input_logic(
      int(self._PROXIMITY_SENSOR_AXIS), self._PROXIMITY_SENSOR_INPUT, _InputLogic.StopForward,
    )
    move_task = asyncio.create_task(
      self.move_to_joint_position({Axis.Z: z0 - max_descent}, backend_params=move_params)
    )
    tripped = False
    try:
      # The drive halts itself via IL the moment the beam breaks. We poll the
      # sensor in parallel so we can stop waiting for "move done" (which never
      # arrives — the drive halted short of target).
      while not move_task.done():
        if await self.read_proximity_sensor():
          tripped = True
          break
        await asyncio.sleep(0.01)
      move_task.cancel()
      try:
        await move_task
      except (asyncio.CancelledError, CanError):
        pass
    finally:
      # Match C# search cleanup (KX2RobotControl.cs:8650-8658): halt motor
      # FIRST, then restore IL. Reverse order would let the drive surge toward
      # the unreached target during the gap.
      try:
        await self.driver.motor_stop(int(Axis.Z))
      except Exception as e:
        logger.warning("find_z: motor_stop failed: %s", e)
      try:
        await self.driver.configure_input_logic(
          int(self._PROXIMITY_SENSOR_AXIS), self._PROXIMITY_SENSOR_INPUT, _InputLogic.GeneralPurpose,
        )
      except Exception as e:
        logger.warning("find_z: IL restore failed: %s", e)
    if not tripped:
      z_end = await self.motor_get_current_position(Axis.Z)
      raise RuntimeError(
        f"proximity sensor never tripped within {max_descent} (Z {z0:.2f} -> {z_end:.2f})"
      )
    return await self.motor_get_current_position(Axis.Z)

  @staticmethod
  def _wrap_to_range(x: float, lo: float, hi: float) -> float:
    span = hi - lo
    if span == 0:
      return lo
    k = math.trunc(x / span)
    x = x - k * span
    if x < lo:
      x += span
    if x == hi:
      x -= span
    return x

  @staticmethod
  def _profile(dist: float, v: float, a: float) -> tuple:
    """
    Returns (v, a, t_acc, t_total) after applying triangular fallback if
    needed. If the distance is short, you cannot reach v before you must
    decelerate.
    """
    if dist <= 0:
      return v, a, 0.0, 0.0
    if a <= 0:
      # degenerate; avoid crash
      return max(v, 1e-9), 1e-9, 0.0, dist / max(v, 1e-9)

    t_acc = v / a
    d_acc = 0.5 * a * t_acc * t_acc

    # triangular?
    if 2.0 * d_acc > dist:
      t_acc = math.sqrt(dist / a)
      v = a * t_acc
      return v, a, t_acc, 2.0 * t_acc

    d_cruise = dist - 2.0 * d_acc
    t_cruise = d_cruise / max(v, 1e-9)
    return v, a, t_acc, t_cruise + 2.0 * t_acc

  async def calculate_move_abs_all_axes(
    self,
    cmd_pos: Dict[Axis, float],
    params: Optional["KX2ArmBackend.JointMoveParams"] = None,
  ) -> Optional[_MotorsMovePlan]:
    if params is None:
      params = KX2ArmBackend.JointMoveParams()
    target = cmd_pos.copy()
    axes = list(target.keys())

    enc_pos: Dict[Axis, float] = {}
    enc_vel: Dict[Axis, float] = {}
    enc_accel: Dict[Axis, float] = {}
    skip_ax: Dict[Axis, bool] = {}

    # input validation / travel limits / done-wait logic
    for name, val in (
      ("linear_speed", params.linear_speed),
      ("linear_acceleration", params.linear_acceleration),
      ("rotary_speed", params.rotary_speed),
      ("rotary_acceleration", params.rotary_acceleration),
    ):
      if val is not None and val <= 0.0:
        raise ValueError(f"{name} must be positive, got {val}")

    # Travel-limit bounds check. Mirrors C# MoveAbsoluteSingleAxisPrivate
    # (KX2RobotControl.cs:4624-4649): snap if within 0.1 of the limit, raise
    # otherwise. Without this, sending an out-of-range target (e.g. gripper
    # width 600 when max_travel ~30) parks the drive trying to reach an
    # unreachable position — MS never returns to 0 and every subsequent
    # command on that axis hangs until full re-setup. Run before the elbow
    # position->angle conversion so max_travel/min_travel are compared in
    # the same space the user passed in.
    for ax in axes:
      ax_cfg = self._cfg.axes[ax]
      if ax_cfg.unlimited_travel:
        continue
      t = target[ax]
      if t > ax_cfg.max_travel:
        if t - ax_cfg.max_travel < 0.1:
          target[ax] = ax_cfg.max_travel
        else:
          raise ValueError(
            f"Axis {ax.name} target {t} exceeds max_travel {ax_cfg.max_travel}"
          )
      elif t < ax_cfg.min_travel:
        if ax_cfg.min_travel - t < 0.1:
          target[ax] = ax_cfg.min_travel
        else:
          raise ValueError(
            f"Axis {ax.name} target {t} below min_travel {ax_cfg.min_travel}"
          )

    # Convert elbow cmd from position->angle for planning math
    if Axis.ELBOW in axes:
      target[Axis.ELBOW] = self.convert_elbow_position_to_angle(target[Axis.ELBOW])

    # Clearance check
    if Axis.Z in axes:
      if (
        target[Axis.Z] < self._cfg.axes[Axis.Z].min_travel + self._cfg.base_to_gripper_clearance_z
        and target[Axis.ELBOW] < self._cfg.base_to_gripper_clearance_arm
      ):
        raise ValueError("Base-to-gripper clearance violated")

    # Determine current/start positions
    curr = await self.request_joint_position()

    # Elbow: convert both target and start to angle for distance math
    if Axis.ELBOW in curr:
      curr[Axis.ELBOW] = self.convert_elbow_position_to_angle(curr[Axis.ELBOW])

    # Handle unlimited travel normalization when direction != NORMAL
    for ax in axes:
      if (
        self._cfg.axes[ax].unlimited_travel
        and self._cfg.axes[ax].joint_move_direction != _JointMoveDirection.Normal
      ):
        target[ax] = self._wrap_to_range(target[ax], self._cfg.axes[ax].min_travel, self._cfg.axes[ax].max_travel)

    # Distances, skip flags, initial v/a per axis
    dist: Dict[Axis, float] = {}
    v: Dict[Axis, float] = {}
    a: Dict[Axis, float] = {}
    accel_time: Dict[Axis, float] = {}
    total_time: Dict[Axis, float] = {}

    for ax in axes:
      if self._cfg.axes[ax].unlimited_travel:
        d = target[ax] - curr[ax]
        span = self._cfg.axes[ax].max_travel - self._cfg.axes[ax].min_travel
        dir_ = self._cfg.axes[ax].joint_move_direction

        if dir_ == _JointMoveDirection.Clockwise and d > 0.01:
          d -= span
        elif dir_ == _JointMoveDirection.Counterclockwise and d < -0.01:
          d += span
        elif dir_ == _JointMoveDirection.ShortestWay:
          if d > 180.0:
            d -= span
          elif d < -180.0:
            d += span

        dist[ax] = abs(d)
      else:
        dist[ax] = abs(target[ax] - curr[ax])

      skip_ax[ax] = abs(dist[ax]) < 0.01

      axis_max_v = self._cfg.axes[ax].max_vel
      axis_max_a = self._cfg.axes[ax].max_accel
      if ax.is_linear:
        chosen_v = params.linear_speed if params.linear_speed is not None else axis_max_v
        chosen_a = (
          params.linear_acceleration if params.linear_acceleration is not None else axis_max_a
        )
      else:
        chosen_v = params.rotary_speed if params.rotary_speed is not None else axis_max_v
        chosen_a = (
          params.rotary_acceleration if params.rotary_acceleration is not None else axis_max_a
        )
      v[ax] = min(chosen_v, axis_max_v)
      a[ax] = min(chosen_a, axis_max_a)

      if not skip_ax[ax] and a[ax] > 0:
        v[ax], a[ax], accel_time[ax], total_time[ax] = self._profile(
          dist[ax], v[ax], a[ax]
        )
      else:
        total_time[ax] = 0.0
        accel_time[ax] = 0.0

    if all(skip_ax[ax] for ax in axes):
      return None  # nothing to do

    # Pick axis with max accel_time among non-skipped; match others to that accel_time
    lead_acc_ax = max(
      (ax for ax in axes if not skip_ax[ax]),
      key=lambda ax: accel_time[ax],
    )
    lead_acc_t = accel_time[lead_acc_ax]

    for ax in axes:
      if ax == lead_acc_ax or skip_ax[ax]:
        continue
      if accel_time[ax] > lead_acc_t:
        v[ax] = lead_acc_t * a[ax]
      elif accel_time[ax] < lead_acc_t:
        a[ax] = v[ax] / max(lead_acc_t, 1e-9)

    # Recompute times after accel sync
    for ax in axes:
      if skip_ax[ax]:
        total_time[ax] = 0.0
        continue
      v[ax], a[ax], _, total_time[ax] = self._profile(dist[ax], v[ax], a[ax])

    # Pick axis with max total_time; scale others to match its total_time
    lead_time_ax = max(axes, key=lambda ax: total_time[ax])
    lead_T = total_time[lead_time_ax]

    for ax in axes:
      if ax == lead_time_ax or skip_ax[ax]:
        continue
      denom = v[ax] * (lead_T - (v[ax] / max(a[ax], 1e-9)))
      if abs(denom) < 1e-12:
        continue
      k = dist[ax] / denom
      v[ax] *= k
      a[ax] *= k

    # Final recompute and final move time
    for ax in axes:
      if skip_ax[ax]:
        total_time[ax] = 0.0
        continue
      v[ax], a[ax], _, total_time[ax] = self._profile(dist[ax], v[ax], a[ax])

    move_time = max(total_time[ax] for ax in axes)

    # Convert back to encoder units (and elbow back to "position" space)
    for ax in axes:
      conv = self._cfg.axes[ax].motor_conversion_factor
      enc_pos[ax] = target[ax] * conv

      if skip_ax[ax]:
        enc_vel[ax] = 1000.0
        enc_accel[ax] = 1000.0
      else:
        enc_vel[ax] = max(v[ax] * abs(conv), 1.0)
        enc_accel[ax] = max(a[ax] * abs(conv), 1.0)

    return _MotorsMovePlan(
      moves=[
        _MotorMoveParam(
          node_id=int(ax),
          position=int(round(enc_pos[ax])),
          velocity=int(round(enc_vel[ax])),
          acceleration=int(round(enc_accel[ax])),
          direction=self._cfg.axes[ax].joint_move_direction,
        )
        for ax in axes
      ],
      move_time=move_time,
    )

  async def motors_move_joint(
    self,
    cmd_pos: Dict[Axis, float],
    params: Optional["KX2ArmBackend.JointMoveParams"] = None,
  ) -> None:
    logger.debug("motors_move_joint cmd_pos=%s", cmd_pos)
    plan = await self.calculate_move_abs_all_axes(cmd_pos=cmd_pos, params=params)

    if plan is None:  # if every axis is skipped, exit
      return

    await self.motors_move_absolute_execute(plan)

  async def motors_move_absolute_execute(self, plan: _MotorsMovePlan) -> None:
    await self.driver.pvt_select_mode(False)

    if logger.isEnabledFor(logging.DEBUG):
      logger.debug(
        "move plan: move_time=%.3fs, %d axes:", plan.move_time, len(plan.moves)
      )
      for move in plan.moves:
        logger.debug(
          "  node=%d pos=%s vel=%s acc=%s dir=%s",
          move.node_id, move.position, move.velocity,
          move.acceleration, move.direction.name,
        )

    for move in plan.moves:
      nid = int(move.node_id)
      await self.driver.motor_set_move_direction(nid, move.direction)
      # 0x607A = Target Position (24698 decimal)
      await self.driver.can_sdo_download_elmo_object(
        nid, 24698, 0, int(move.position), _ElmoObjectDataType.INTEGER32,
      )
      # 0x6081 = Profile Velocity (24705 decimal)
      await self.driver.can_sdo_download_elmo_object(
        nid, 24705, 0, int(move.velocity), _ElmoObjectDataType.UNSIGNED32,
      )
      acc = max(int(move.acceleration), 100)
      # 0x6083 = Profile Acceleration (24707 decimal)
      await self.driver.can_sdo_download_elmo_object(
        nid, 24707, 0, acc, _ElmoObjectDataType.UNSIGNED32
      )
      # 0x6084 = Profile Deceleration (24708 decimal)
      await self.driver.can_sdo_download_elmo_object(
        nid, 24708, 0, acc, _ElmoObjectDataType.UNSIGNED32
      )

    node_ids = [move.node_id for move in plan.moves]
    await self.driver.motors_move_start(node_ids)
    await self.driver.wait_for_moves_done(node_ids, timeout=plan.move_time + 2)

  async def _cart_to_joints(
    self, pose: kinematics.KX2GripperLocation
  ) -> Dict[Axis, float]:
    """Cartesian -> joints with closest-solution semantics.

    If `pose.wrist` is None, fills it with the current joint's wrist sign
    so the arm picks whichever IK solution needs the least motion. Then
    snaps each rotary axis to the nearest 360° multiple of the current
    position, re-enforcing the wrist sign afterward.
    """
    current = {Axis(k): v for k, v in (await self.request_joint_position()).items()}
    # IK needs an explicit cw/ccw; for closest mode fill from the current
    # joint's sign so IK has a valid choice. Snap then runs with the
    # *original* pose.wrist — None disables sign re-enforce so the snap
    # actually picks the closest J4.
    ik_wrist = pose.wrist if pose.wrist is not None else (
      "ccw" if current[Axis.WRIST] >= 0 else "cw"
    )
    resolved = dataclasses.replace(pose, wrist=ik_wrist)
    ik_joints = kinematics.ik(resolved, self._cfg, self._gripper_config)
    return kinematics.snap_to_current(ik_joints, current, pose.wrist)

  # -- capability interface (OrientableGripperArmBackend + HasJoints + CanFreedrive) --

  @dataclasses.dataclass
  class CartesianMoveParams(BackendParams):
    """Per-axis speed/acceleration limits in physical units.

    `linear_*` applies to Z (and rail/gripper if commanded); `rotary_*`
    applies to shoulder/elbow/wrist. `None` falls back to the axis maximum
    read from the drive at setup. Values above the per-axis max are clamped
    silently.
    """
    linear_speed: Optional[float] = None         # mm/s
    linear_acceleration: Optional[float] = None  # mm/s^2
    rotary_speed: Optional[float] = None         # deg/s
    rotary_acceleration: Optional[float] = None  # deg/s^2

  @dataclasses.dataclass
  class JointMoveParams(BackendParams):
    """Per-axis speed/acceleration limits in physical units. Same shape as
    `CartesianMoveParams` — see its docstring."""
    linear_speed: Optional[float] = None
    linear_acceleration: Optional[float] = None
    rotary_speed: Optional[float] = None
    rotary_acceleration: Optional[float] = None

  @dataclasses.dataclass
  class GripParams(BackendParams):
    check_plate_gripped: bool = True

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    # Fire MO=0 on every motion axis concurrently — serial halts let later
    # axes coast for the duration of the earlier SDOs.
    await asyncio.gather(
      *(self.driver.motor_emergency_stop(node_id=axis) for axis in MOTION_AXES)
    )

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError(
      "KX2 does not define a default park pose. Use move_to_joint_position with a "
      "site-specific safe configuration."
    )

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    joints = {Axis(k): v for k, v in (await self.request_joint_position()).items()}
    return kinematics.fk(joints, self._cfg, self._gripper_config)

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    await self.motors_move_joint({Axis.SERVO_GRIPPER: gripper_width})

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    if not isinstance(backend_params, KX2ArmBackend.GripParams):
      backend_params = KX2ArmBackend.GripParams()
    await self.motors_move_joint({Axis.SERVO_GRIPPER: gripper_width})
    if backend_params.check_plate_gripped:
      await self.check_plate_gripped()

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    pos = await self.motor_get_current_position(Axis.SERVO_GRIPPER)
    return abs(pos) < 1.0

  async def move_to_gripper_location(
    self,
    pose: kinematics.KX2GripperLocation,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Cartesian move with optional explicit wrist sign.

    `pose.wrist`: "cw" or "ccw" picks the wrist solution explicitly; None
    falls back to the closest-to-current solution (same as `move_to_location`).
    """
    if not isinstance(backend_params, KX2ArmBackend.CartesianMoveParams):
      backend_params = KX2ArmBackend.CartesianMoveParams()
    joint_pos = await self._cart_to_joints(pose)
    # CartesianMoveParams and JointMoveParams have identical shape (linear/rotary
    # speed + acceleration); copy the limits across to feed motors_move_joint.
    joint_params = KX2ArmBackend.JointMoveParams(
      linear_speed=backend_params.linear_speed,
      linear_acceleration=backend_params.linear_acceleration,
      rotary_speed=backend_params.rotary_speed,
      rotary_acceleration=backend_params.rotary_acceleration,
    )
    await self.motors_move_joint(cmd_pos=joint_pos, params=joint_params)

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    pose = kinematics.KX2GripperLocation(
      location=location, rotation=Rotation(z=direction), wrist=None
    )
    await self.move_to_gripper_location(pose, backend_params=backend_params)

  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    await self.move_to_location(location, direction, backend_params=backend_params)
    await self.close_gripper(resource_width, backend_params=backend_params)

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    await self.move_to_location(location, direction, backend_params=backend_params)
    await self.open_gripper(resource_width, backend_params=backend_params)

  async def move_to_joint_position(
    self,
    position: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, KX2ArmBackend.JointMoveParams):
      backend_params = KX2ArmBackend.JointMoveParams()
    cmd_pos = {Axis(int(k)): float(v) for k, v in position.items()}
    await self.motors_move_joint(cmd_pos=cmd_pos, params=backend_params)

  async def pick_up_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    await self.move_to_joint_position(position, backend_params=backend_params)
    await self.close_gripper(resource_width, backend_params=backend_params)

  async def drop_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    await self.move_to_joint_position(position, backend_params=backend_params)
    await self.open_gripper(resource_width, backend_params=backend_params)

  async def request_joint_position(
    self, backend_params: Optional[BackendParams] = None
  ) -> Dict[int, float]:
    return {
      Axis.SHOULDER: await self.motor_get_current_position(Axis.SHOULDER),
      Axis.Z: await self.motor_get_current_position(Axis.Z),
      Axis.ELBOW: await self.motor_get_current_position(Axis.ELBOW),
      Axis.WRIST: await self.motor_get_current_position(Axis.WRIST),
      Axis.SERVO_GRIPPER: await self.motor_get_current_position(Axis.SERVO_GRIPPER),
    }

  def motion_limits(self) -> "_MotionLimits":
    """Per-axis (max_speed, max_acceleration) read from the drives at setup.

    Linear axes (Z, rail, servo gripper) are mm/s, mm/s^2; rotary axes
    (shoulder, elbow, wrist) are deg/s, deg/s^2. These are the upper bounds
    `JointMoveParams` / `CartesianMoveParams` get clamped to. Returned as a
    dict subclass that renders as a table in Jupyter and plain-text columns
    in a terminal.
    """
    return _MotionLimits(
      {k: (cfg.max_vel, cfg.max_accel) for k, cfg in self._cfg.axes.items()},
    )

  async def start_freedrive_mode(
    self,
    free_axes: Optional[List[int]] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    # Default: free all motion axes (shoulder/Z/elbow/wrist) but never the
    # gripper, so a held plate doesn't drop. Caller can override with an
    # explicit list; [0] means "all motion axes" per CanFreedrive convention.
    if free_axes is None or free_axes == [0]:
      axes: List[int] = [int(a) for a in MOTION_AXES]
    else:
      axes = [int(a) for a in free_axes]
    for axis in axes:
      await self.driver.motor_enable(node_id=axis, state=False, use_ds402=True)
    self._freedrive_axes = axes

  async def stop_freedrive_mode(self, backend_params: Optional[BackendParams] = None) -> None:
    axes = self._freedrive_axes or list(MOTION_AXES)
    for axis in axes:
      await self.driver.motor_enable(node_id=axis, state=True, use_ds402=True)
    self._freedrive_axes = []

  async def very_dangerously_yeet(
    self,
    min_z: float = 400.0,
    bump: float = 1.25,
  ) -> None:
    """Easter egg — swing the arm at firmware-max and open the gripper at
    peak velocity to throw whatever is being held.

    Call from your pickup pose. Sequence: auto-windup wrist to the inward
    angle, swing shoulder 180° at firmware-max, fire gripper open near end
    of cruise (with wrist flick at peak ω for extra tangential velocity),
    return to pickup pose.

    ``bump`` scales VH[2]/SP[2]/SD[0] on shoulder + wrist for the swing's
    duration (restored in finally). 1.0 = stock; 1.25 confirmed safe;
    higher risks tracking-error faults that need Elmo Composer recovery.
    """
    warning = (
      f"WARNING: very_dangerously_yeet: swing the arm at {bump:.2f}x firmware-max "
      "and open the gripper mid-swing. Anything in the gripper will be "
      "thrown. High bump can fault the drive. Type 'y' to continue: "
    )
    # Run the blocking prompt off the loop so the canopen RX listener and
    # any other coroutines stay live while we wait for the operator.
    answer = await asyncio.to_thread(input, warning)
    if answer.strip().lower() != "y":
      raise RuntimeError("very_dangerously_yeet: aborted by user")

    driver = self.driver
    cfg = self._cfg

    z_now = await self.motor_get_current_position(Axis.Z)
    if z_now < min_z:
      raise RuntimeError(
        f"yeet refused: Z={z_now:.0f}mm < min_z={min_z:.0f}mm; raise the arm first"
      )

    # Snapshot + bump VH[2]/SP[2]/SD[0] on swing axes; restore in finally.
    saved_limits: Dict[Axis, dict] = {}
    if bump != 1.0:
      for ax in (Axis.SHOULDER, Axis.WRIST):
        nid = int(ax)
        s = {
          "VH2": await driver.query_int(nid, "VH", 2),
          "SP2": await driver.query_int(nid, "SP", 2),
          "SD0": await driver.query_int(nid, "SD", 0),
          "max_vel": cfg.axes[ax].max_vel,
          "max_accel": cfg.axes[ax].max_accel,
        }
        saved_limits[ax] = s
        new_vh2 = int(s["VH2"] * bump)
        new_sp2 = int(s["SP2"] * bump)
        new_sd0 = int(s["SD0"] * bump)
        await driver.write(nid, "VH", 2, new_vh2)
        await driver.write(nid, "SP", 2, new_sp2)
        await driver.write(nid, "SD", 0, new_sd0)
        conv = abs(cfg.axes[ax].motor_conversion_factor)
        cfg.axes[ax].max_vel = (new_sp2 / 1.01) / conv
        cfg.axes[ax].max_accel = (new_sd0 / 1.01) / conv

    try:
      pickup_pose = await self.request_joint_position()

      # Auto-windup: rotate wrist to the inward angle (opposite of outward).
      wrist_inward = 0.0 if self._gripper_config.finger_side == "barcode_reader" else 180.0
      while wrist_inward - pickup_pose[Axis.WRIST] > 180.0:
        wrist_inward -= 360.0
      while wrist_inward - pickup_pose[Axis.WRIST] < -180.0:
        wrist_inward += 360.0
      await self.motors_move_joint(
        cmd_pos={Axis.WRIST: wrist_inward},
        params=KX2ArmBackend.JointMoveParams(
          linear_speed=_YEET_WINDUP_SPEED,
          linear_acceleration=_YEET_WINDUP_ACC,
          rotary_speed=_YEET_WINDUP_SPEED,
          rotary_acceleration=_YEET_WINDUP_ACC,
        ),
      )

      joints0 = await self.request_joint_position()

      # Outward wrist = kinematic target (180° barcode_reader, 0° proximity).
      wrist_outward = 180.0 if self._gripper_config.finger_side == "barcode_reader" else 0.0
      while wrist_outward - joints0[Axis.WRIST] > 180.0:
        wrist_outward -= 360.0
      while wrist_outward - joints0[Axis.WRIST] < -180.0:
        wrist_outward += 360.0

      sh_move, sh_t_acc, sh_t_total, _ = await _yeet_build_axis_move(
        self, Axis.SHOULDER,
        joints0[Axis.SHOULDER], joints0[Axis.SHOULDER] + _YEET_SHOULDER_SWING_DEG,
      )
      wr_move, wr_t_acc, wr_t_total, _ = await _yeet_build_axis_move(
        self, Axis.WRIST, joints0[Axis.WRIST], wrist_outward,
      )
      sh_plan = _MotorsMovePlan(moves=[sh_move], move_time=sh_t_total)
      wr_plan = _MotorsMovePlan(moves=[wr_move], move_time=wr_t_total)

      # Release fires inside shoulder cruise. Wrist trigger is delayed so its
      # accel ramp finishes at release (peak ω at the gripper offset).
      sh_cruise_dur = max(0.0, sh_t_total - 2 * sh_t_acc)
      release_t = sh_t_acc + sh_cruise_dur * _YEET_RELEASE_FRACTION
      wrist_trigger_t = max(0.0, release_t - wr_t_acc)

      sg = int(Axis.SERVO_GRIPPER)
      sg_cfg = cfg.axes[Axis.SERVO_GRIPPER]
      open_pos = min(_YEET_OPEN_POSITION, sg_cfg.max_travel - _YEET_OPEN_SAFETY_MARGIN)
      gripper_plan = _MotorsMovePlan(moves=[_MotorMoveParam(
        node_id=sg,
        position=int(round(open_pos * sg_cfg.motor_conversion_factor)),
        velocity=int(round(sg_cfg.max_vel * abs(sg_cfg.motor_conversion_factor))),
        acceleration=int(round(sg_cfg.max_accel * abs(sg_cfg.motor_conversion_factor))),
        direction=sg_cfg.joint_move_direction,
      )])

      # Pre-arm so triggers are pure control-word writes (sub-ms), not SDOs.
      await _yeet_arm_plan(driver, sh_plan)
      await _yeet_arm_plan(driver, wr_plan)

      await driver.motors_move_start([int(Axis.SHOULDER)])
      t0 = time.monotonic()
      await asyncio.sleep(max(0.0, wrist_trigger_t - (time.monotonic() - t0)))
      await driver.motors_move_start([int(Axis.WRIST)])

      await asyncio.sleep(max(0.0, release_t - (time.monotonic() - t0)))
      await self.motors_move_absolute_execute(gripper_plan)

      # Settle slack: at higher bump, drives overshoot + ring before asserting
      # target-reached; tight margin trips a CanError even though throw was OK.
      swing_finish_t = max(sh_t_total, wrist_trigger_t + wr_t_total)
      await driver.wait_for_moves_done(
        [int(Axis.SHOULDER), int(Axis.WRIST)], timeout=swing_finish_t + 5,
      )

      await self.motors_move_joint(
        cmd_pos={
          Axis.SHOULDER: pickup_pose[Axis.SHOULDER],
          Axis.WRIST: pickup_pose[Axis.WRIST],
        },
        params=KX2ArmBackend.JointMoveParams(
          linear_speed=_YEET_RETURN_SPEED,
          linear_acceleration=_YEET_RETURN_ACC,
          rotary_speed=_YEET_RETURN_SPEED,
          rotary_acceleration=_YEET_RETURN_ACC,
        ),
      )
    finally:
      for ax, s in saved_limits.items():
        nid = int(ax)
        await driver.write(nid, "VH", 2, s["VH2"])
        await driver.write(nid, "SP", 2, s["SP2"])
        await driver.write(nid, "SD", 0, s["SD0"])
        cfg.axes[ax].max_vel = s["max_vel"]
        cfg.axes[ax].max_accel = s["max_accel"]


class _MotionLimits(Dict[Axis, tuple]):
  """Pretty-printing dict for `KX2ArmBackend.motion_limits()`. Dict access
  still works (`limits[Axis.Z]` -> `(max_speed, max_accel)`); `__repr__`
  formats it as an aligned ASCII table for both terminals and notebooks.
  """

  def __repr__(self) -> str:
    rows = []
    for ax, (v, a) in self.items():
      unit = "mm" if ax.is_linear else "deg"
      rows.append((ax.name, f"{v:.2f} {unit}/s", f"{a:.2f} {unit}/s^2"))
    headers = ("axis", "max speed", "max acceleration")
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(3)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    out.extend(fmt.format(*r) for r in rows)
    return "\n".join(out)

# UI[5..10] code -> digital input role.
_DIGITAL_INPUT_NAMES: Dict[int, str] = {
  101: "ProximitySensor",
  102: "TeachButton",
}

# UI[13..16] code -> output role.
_OUTPUT_NAMES: Dict[int, str] = {
  101: "IndicatorLightRed",
  102: "IndicatorLightGreen",
  103: "IndicatorLightBlue",
  104: "IndicatorLight",
  105: "Buzzer",
}


# === very_dangerously_yeet helpers (easter egg) ============================
# Constants and helpers for KX2ArmBackend.very_dangerously_yeet. Inlined
# here on purpose; do not split into another module.

_YEET_SHOULDER_SWING_DEG = 180.0
_YEET_RELEASE_FRACTION = 0.85
# Gripper open target (mm). Clamped at runtime to drive's max_travel - margin.
_YEET_OPEN_POSITION = 30.0
_YEET_OPEN_SAFETY_MARGIN = 1.0
# Windup speed: arm holds the plate, don't whip.
_YEET_WINDUP_SPEED = 60.0
_YEET_WINDUP_ACC = 120.0
# Return speed: plate is gone, snap back faster than windup.
_YEET_RETURN_SPEED = 240.0
_YEET_RETURN_ACC = 480.0


async def _yeet_build_axis_move(
  backend: "KX2ArmBackend", ax: Axis, cur: float, target: float,
) -> tuple:
  """Per-axis _MotorMoveParam at firmware velocity limit (VH[2]/1.01).
  Returns (move, t_acc, t_total, v_phys)."""
  cfg = backend._cfg
  ax_cfg = cfg.axes[ax]
  conv = ax_cfg.motor_conversion_factor
  vh2 = await backend.driver.query_int(int(ax), "VH", 2)
  v_phys = vh2 / 1.01 / abs(conv)
  a_phys = ax_cfg.max_accel
  direction = ax_cfg.joint_move_direction

  d = target - cur
  span = ax_cfg.max_travel - ax_cfg.min_travel
  if span > 0 and ax_cfg.unlimited_travel:
    if direction == _JointMoveDirection.Clockwise and d > 0.01:
      d -= span
    elif direction == _JointMoveDirection.Counterclockwise and d < -0.01:
      d += span
    elif direction == _JointMoveDirection.ShortestWay:
      if d > 180.0:
        d -= span
      elif d < -180.0:
        d += span
  dist = abs(d)

  if ax_cfg.unlimited_travel and direction != _JointMoveDirection.Normal:
    target = KX2ArmBackend._wrap_to_range(target, ax_cfg.min_travel, ax_cfg.max_travel)

  _, _, t_acc, t_total = KX2ArmBackend._profile(dist, v_phys, a_phys)
  move = _MotorMoveParam(
    node_id=int(ax),
    position=int(round(target * conv)),
    velocity=max(int(round(v_phys * abs(conv))), 1),
    acceleration=max(int(round(a_phys * abs(conv))), 1),
    direction=direction,
  )
  return move, t_acc, t_total, v_phys


async def _yeet_arm_plan(driver: KX2Driver, plan: _MotorsMovePlan) -> None:
  """Pre-load a plan onto the drives without triggering it. Splits SDO
  setup latency from the move start so the timer can be accurate."""
  await driver.pvt_select_mode(False)
  for move in plan.moves:
    nid = int(move.node_id)
    await driver.motor_set_move_direction(nid, move.direction)
    await driver.can_sdo_download_elmo_object(
      nid, 24698, 0, int(move.position), _ElmoObjectDataType.INTEGER32,
    )
    await driver.can_sdo_download_elmo_object(
      nid, 24705, 0, int(move.velocity), _ElmoObjectDataType.UNSIGNED32,
    )
    acc = max(int(move.acceleration), 100)
    await driver.can_sdo_download_elmo_object(
      nid, 24707, 0, acc, _ElmoObjectDataType.UNSIGNED32,
    )
    await driver.can_sdo_download_elmo_object(
      nid, 24708, 0, acc, _ElmoObjectDataType.UNSIGNED32,
    )
