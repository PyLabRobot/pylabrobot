import asyncio
import dataclasses
import logging
import math
import time
import warnings
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional

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
  KX2Config,
  ServoGripperConfig,
)
from pylabrobot.paa.kx2.driver import (
  CanError,
  InputLogic,
  JointMoveDirection,
  KX2Driver,
  MotorMoveParam,
  MotorsMovePlan,
)
from pylabrobot.resources import Coordinate, Rotation

logger = logging.getLogger(__name__)


class HomeStatus(IntEnum):
  NotHomed = 0
  Homed = 1
  InitializedWithoutHoming = 2


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
    # Tooling offsets are user-supplied; everything else on the config is
    # filled in from the drives during setup.
    self._gripper_length = float(gripper_length)
    self._gripper_z_offset = float(gripper_z_offset)
    self._gripper_finger_side: GripperFingerSide = gripper_finger_side
    self._config: Optional[KX2Config] = None

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    # Driver has already brought CAN up (connect + node discovery + PDO
    # mapping) via Device.setup(). Now read per-drive parameters, enable
    # motion axes, and initialize the servo gripper.
    await self.drive_get_parameters(self.driver.node_id_list)

    await asyncio.sleep(2)

    for axis in MOTION_AXES:
      try:
        await self.driver.motor_enable(node_id=axis, state=True, use_ds402=True)
      except Exception as e:
        logger.warning("Error enabling motor on axis %s: %s", axis, e)

    await self.servo_gripper_initialize()

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
      params = [int(hs_pe), int(timeout * 1000)]
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

    await self.driver.motor_enable(node_id=nid, state=True, use_ds402=nid in MOTION_AXES)

    await self.driver.motors_move_absolute_execute(
      plan=MotorsMovePlan(
        moves=[
          MotorMoveParam(
            node_id=nid,
            position=hs_offset,
            velocity=offset_vel,
            acceleration=offset_acc,
            relative=False,
            direction=JointMoveDirection.ShortestWay,
          )
        ],
      )
    )

    is_positive = hs_offset > 0
    await self._motor_index_search(nid, abs(srch_vel), srch_acc, is_positive, timeout)

    await self.driver.motors_move_absolute_execute(
      plan=MotorsMovePlan(
        moves=[
          MotorMoveParam(
            node_id=nid,
            position=ind_offset,
            velocity=offset_vel,
            acceleration=offset_acc,
            relative=False,
            direction=JointMoveDirection.ShortestWay,
          )
        ]
      )
    )
    await self._motor_reset_encoder_position(nid, home_pos)
    await self._motor_set_homed_status(nid, HomeStatus.Homed)

  # -- servo gripper ------------------------------------------------------

  async def servo_gripper_initialize(self):
    try:
      await self.driver.motor_enable(
        node_id=int(Axis.SERVO_GRIPPER), state=True, use_ds402=False
      )
    except Exception as e:
      logger.warning(
        "Error enabling servo gripper motor on node %s: %s", Axis.SERVO_GRIPPER, e
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
    await self.motors_move_joint(
      {Axis.SERVO_GRIPPER: closed_position},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )

    if check_plate_gripped:
      await self.check_plate_gripped()

  async def servo_gripper_open(self, open_position: float) -> None:
    await self.motors_move_joint(
      {Axis.SERVO_GRIPPER: open_position},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )

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

  async def drive_get_parameters(self, node_ids) -> None:
    nodes = (
      [int(b) for b in node_ids]
      if isinstance(node_ids, (bytes, bytearray))
      else [int(x) for x in node_ids]
    )

    # Pass 1: identify axes by UI[4]
    uis = set()
    for node in nodes:
      if node == await self.driver.query_int(int(node), "UI", 4):
        uis.add(node)
    for required_axis in MOTION_AXES:
      if required_axis.value not in uis:
        raise CanError(f"Missing required axis with UI[4]={required_axis}")
    robot_on_rail = 5 in uis
    has_servo_gripper = 6 in uis
    if robot_on_rail:
      warnings.warn("Rails has not been tested for KX2 robots.")

    # Pass 2: per-axis parameters
    axes: Dict[int, AxisConfig] = {}
    for axis in nodes:
      logger.debug("Reading parameters for axis %s", axis)

      # UI[5..10] digital inputs
      digital_inputs: Dict[int, str] = {}
      for ui_idx in range(5, 11):
        ret = await self.driver.query_int(axis, "UI", ui_idx)
        ch = (ui_idx - 5) + 1
        if ret == 101:
          digital_inputs[ch] = "ProximitySensor"
        elif ret == 102:
          digital_inputs[ch] = "TeachButton"
        else:
          digital_inputs[ch] = "" if ret <= 0 else f"AuxPin{ret}"

      # UI[11..12] analog inputs
      analog_inputs: Dict[int, str] = {}
      for ui_idx in range(11, 13):
        ret = await self.driver.query_int(axis, "UI", ui_idx)
        ch = (ui_idx - 11) + 1
        analog_inputs[ch] = "" if ret <= 0 else f"AuxPin{ret}"

      # UI[13..16] outputs
      outputs: Dict[int, str] = {}
      for ui_idx in range(13, 17):
        ret = await self.driver.query_int(axis, "UI", ui_idx)
        ch = (ui_idx - 13) + 1
        if ret == 101:
          outputs[ch] = "IndicatorLightRed"
        elif ret == 102:
          outputs[ch] = "IndicatorLightGreen"
        elif ret == 103:
          outputs[ch] = "IndicatorLightBlue"
        elif ret == 104:
          outputs[ch] = "IndicatorLight"
        elif ret == 105:
          outputs[ch] = "Buzzer"
        else:
          outputs[ch] = "" if ret <= 0 else f"AuxPin{ret}"

      # UI[24] drive serial number (queried but not currently stored)
      await self.driver.query_int(axis, "UI", 24)

      # UF[1], UF[2] conversion factor
      uf1 = await self.driver.query_float(axis, "UF", 1)
      uf2 = await self.driver.query_float(axis, "UF", 2)
      if uf1 == 0.0 or uf2 == 0.0:
        raise CanError(f"Invalid Motor Conversion Factor for axis {axis}. UF[1]={uf1}, UF[2]={uf2}")
      motor_conversion_factor = uf1 / uf2

      # XM / travel
      xm1 = await self.driver.query_int(axis, "XM", 1)
      xm2 = await self.driver.query_int(axis, "XM", 2)
      max_travel = await self.driver.query_float(axis, "UF", 3)
      min_travel = await self.driver.query_float(axis, "UF", 4)
      vh3 = await self.driver.query_int(axis, "VH", 3)
      vl3 = await self.driver.query_int(axis, "VL", 3)

      joint_move_direction = JointMoveDirection.Normal
      if (xm1 == 0 and xm2 == 0) or (xm1 <= vl3 and xm2 >= vh3):
        unlimited_travel = False
      elif xm1 > vl3 and xm2 < vh3:
        unlimited_travel = True
        if axis in MOTION_AXES:
          joint_move_direction = JointMoveDirection.ShortestWay
      else:
        raise CanError(
          f"Invalid travel limits or modulo settings for axis {axis}. "
          f"VH[3]={vh3}, VL[3]={vl3}, XM[1]={xm1}, XM[2]={xm2}"
        )

      # Encoder socket/type
      ca45 = await self.driver.query_int(axis, "CA", 45)
      if not (0 < ca45 <= 4):
        raise CanError(f"Invalid encoder socket number received from axis {axis}. CA[45]={ca45}")
      enc_type = await self.driver.query_int(axis, "CA", 40 + ca45)
      if enc_type in (1, 2):
        absolute_encoder = False
      elif enc_type == 24:
        absolute_encoder = True
      else:
        raise CanError(
          f"Unsupported encoder type specified for axis {axis}. CA[4{ca45}]={enc_type}"
        )

      ca46 = await self.driver.query_int(axis, "CA", 46)
      if ca45 == ca46:
        num3 = 1.0
      else:
        num3 = await self.driver.query_float(axis, "FF", 3)

      denom = motor_conversion_factor * num3

      sp2 = await self.driver.query_int(axis, "SP", 2)
      if sp2 == 100000:
        vh2 = await self.driver.query_int(axis, "VH", 2)
        max_vel = vh2 / 1.01 / denom
      else:
        max_vel = sp2 / denom

      sd0 = await self.driver.query_int(axis, "SD", 0)
      max_accel = sd0 / 1.01 / denom

      axes[axis] = AxisConfig(
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

    # Robot-level params from shoulder.
    shoulder = int(Axis.SHOULDER)
    base_to_gripper_clearance_z = await self.driver.query_float(shoulder, "UF", 6)
    base_to_gripper_clearance_arm = await self.driver.query_float(shoulder, "UF", 7)
    wrist_offset = await self.driver.query_float(shoulder, "UF", 8)
    elbow_offset = await self.driver.query_float(shoulder, "UF", 9)
    elbow_zero_offset = await self.driver.query_float(shoulder, "UF", 10)

    servo_gripper: Optional[ServoGripperConfig] = None
    if has_servo_gripper:
      sg = int(Axis.SERVO_GRIPPER)
      servo_gripper = ServoGripperConfig(
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

    self._config = KX2Config(
      wrist_offset=wrist_offset,
      elbow_offset=elbow_offset,
      elbow_zero_offset=elbow_zero_offset,
      gripper_length=self._gripper_length,
      gripper_z_offset=self._gripper_z_offset,
      gripper_finger_side=self._gripper_finger_side,
      axes=axes,
      base_to_gripper_clearance_z=base_to_gripper_clearance_z,
      base_to_gripper_clearance_arm=base_to_gripper_clearance_arm,
      robot_on_rail=robot_on_rail,
      servo_gripper=servo_gripper,
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
    vel_pct: float = 5.0,
    accel_pct: float = 5.0,
  ) -> float:
    """Descend Z up to `max_descent`; halt when the IR breakbeam trips.

    If `z_start` is given, first move Z to that height (same vel/accel) and
    search from there; otherwise search from the current Z. Arms IL[4]=
    StopForward so the Elmo drive halts the motor itself on the input edge
    (sub-ms latency, no software in the loop). IL is restored to GeneralPurpose
    afterwards even if the move raises. Returns the Z position where the drive
    halted; raises RuntimeError if the beam never tripped.
    """
    move_params = KX2ArmBackend.JointMoveParams(vel_pct=vel_pct, accel_pct=accel_pct)
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
      int(self._PROXIMITY_SENSOR_AXIS), self._PROXIMITY_SENSOR_INPUT, InputLogic.StopForward,
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
          int(self._PROXIMITY_SENSOR_AXIS), self._PROXIMITY_SENSOR_INPUT, InputLogic.GeneralPurpose,
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
    Returns (v, a, t_total) after applying triangular fallback if needed.
    If the distance is short, you cannot reach v before you must decelerate.
    """
    if dist <= 0:
      return v, a, 0.0
    if a <= 0:
      # degenerate; avoid crash
      return max(v, 1e-9), 1e-9, dist / max(v, 1e-9)

    t_acc = v / a
    d_acc = 0.5 * a * t_acc * t_acc

    # triangular?
    if 2.0 * d_acc > dist:
      d_acc = dist / 2.0
      t_acc = math.sqrt(2.0 * d_acc / a)
      v = a * t_acc
      t_total = 2.0 * t_acc
      return v, a, t_total

    d_cruise = dist - 2.0 * d_acc
    t_cruise = d_cruise / max(v, 1e-9)
    t_total = t_cruise + 2.0 * t_acc
    return v, a, t_total

  async def calculate_move_abs_all_axes(
    self,
    cmd_pos: Dict[Axis, float],
    cmd_vel_pct: float,
    cmd_accel_pct: float,
  ) -> Optional[MotorsMovePlan]:
    target = cmd_pos.copy()
    axes = list(target.keys())

    enc_pos: Dict[Axis, float] = {}
    enc_vel: Dict[Axis, float] = {}
    enc_accel: Dict[Axis, float] = {}
    skip_ax: Dict[Axis, bool] = {}

    # input validation / travel limits / done-wait logic
    if cmd_vel_pct <= 0.0 or cmd_vel_pct > 100.0:
      raise ValueError("CmdVel out of range")
    if cmd_accel_pct <= 0.0 or cmd_accel_pct > 100.0:
      raise ValueError("CmdAccel out of range")

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
        and self._cfg.axes[ax].joint_move_direction != JointMoveDirection.Normal
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

        if dir_ == JointMoveDirection.Clockwise and d > 0.01:
          d -= span
        elif dir_ == JointMoveDirection.Counterclockwise and d < -0.01:
          d += span
        elif dir_ == JointMoveDirection.ShortestWay:
          if d > 180.0:
            d -= span
          elif d < -180.0:
            d += span

        dist[ax] = abs(d)
      else:
        dist[ax] = abs(target[ax] - curr[ax])

      skip_ax[ax] = abs(dist[ax]) < 0.01

      v[ax] = (cmd_vel_pct / 100.0) * self._cfg.axes[ax].max_vel
      a[ax] = (cmd_accel_pct / 100.0) * self._cfg.axes[ax].max_accel

      if not skip_ax[ax] and a[ax] > 0:
        accel_time[ax] = v[ax] / a[ax]
        v[ax], a[ax], total_time[ax] = self._profile(dist[ax], v[ax], a[ax])
        accel_time[ax] = v[ax] / max(a[ax], 1e-9)
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
      v[ax], a[ax], total_time[ax] = self._profile(dist[ax], v[ax], a[ax])

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
      v[ax], a[ax], total_time[ax] = self._profile(dist[ax], v[ax], a[ax])

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

    return MotorsMovePlan(
      moves=[
        MotorMoveParam(
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
    cmd_vel_pct: float,
    cmd_accel_pct: float,
  ) -> None:
    logger.debug("motors_move_joint cmd_pos=%s", cmd_pos)
    plan = await self.calculate_move_abs_all_axes(
      cmd_pos=cmd_pos,
      cmd_vel_pct=cmd_vel_pct,
      cmd_accel_pct=cmd_accel_pct,
    )

    if plan is None:  # if every axis is skipped, exit
      return

    await self.driver.motors_move_absolute_execute(plan)

  async def _cart_to_joints(
    self, pose: kinematics.KX2GripperLocation
  ) -> Dict[Axis, float]:
    """Cartesian -> joints with closest-solution semantics.

    If `pose.wrist` is None, fills it with the current joint's wrist sign
    so the arm picks whichever IK solution needs the least motion. Then
    snaps each rotary axis to the nearest 360° multiple of the current
    position, re-enforcing the wrist sign afterward.
    """
    current = await self.request_joint_position()
    current_int = {int(k): v for k, v in current.items()}
    # IK needs an explicit cw/ccw; for closest mode fill from the current
    # joint's sign so IK has a valid choice. Snap then runs with the
    # *original* pose.wrist — None disables sign re-enforce so the snap
    # actually picks the closest J4.
    ik_wrist = pose.wrist if pose.wrist is not None else (
      "ccw" if current_int[Axis.WRIST] >= 0 else "cw"
    )
    resolved = dataclasses.replace(pose, wrist=ik_wrist)
    ik_joints = kinematics.ik(resolved, self._cfg)
    snapped = kinematics.snap_to_current(ik_joints, current_int, pose.wrist)
    return {Axis(k): v for k, v in snapped.items()}

  # -- capability interface (OrientableGripperArmBackend + HasJoints + CanFreedrive) --

  @dataclass
  class CartesianMoveParams(BackendParams):
    vel_pct: float = 100.0
    accel_pct: float = 100.0

  @dataclass
  class JointMoveParams(BackendParams):
    vel_pct: float = 100.0
    accel_pct: float = 100.0

  @dataclass
  class GripParams(BackendParams):
    check_plate_gripped: bool = True

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    for axis in MOTION_AXES:
      await self.driver.motor_emergency_stop(node_id=axis)

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError(
      "KX2 does not define a default park pose. Use move_to_joint_position with a "
      "site-specific safe configuration."
    )

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    return kinematics.fk(await self.request_joint_position(), self._cfg)

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    await self.motors_move_joint(
      {Axis.SERVO_GRIPPER: gripper_width},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    if not isinstance(backend_params, KX2ArmBackend.GripParams):
      backend_params = KX2ArmBackend.GripParams()
    await self.motors_move_joint(
      {Axis.SERVO_GRIPPER: gripper_width},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )
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
    await self.motors_move_joint(
      cmd_pos=joint_pos,
      cmd_vel_pct=backend_params.vel_pct,
      cmd_accel_pct=backend_params.accel_pct,
    )

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
    await self.motors_move_joint(
      cmd_pos=cmd_pos,
      cmd_vel_pct=backend_params.vel_pct,
      cmd_accel_pct=backend_params.accel_pct,
    )

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

  async def start_freedrive_mode(
    self, free_axes: List[int], backend_params: Optional[BackendParams] = None
  ) -> None:
    # KX2 frees all motion axes at once; free_axes is accepted for API parity.
    del free_axes
    for axis in MOTION_AXES:
      await self.driver.motor_enable(node_id=axis, state=False, use_ds402=True)

  async def stop_freedrive_mode(self, backend_params: Optional[BackendParams] = None) -> None:
    for axis in MOTION_AXES:
      await self.driver.motor_enable(node_id=axis, state=True, use_ds402=True)


MOTION_AXES = (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)
