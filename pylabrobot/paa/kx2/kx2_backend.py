import asyncio
import logging
import math
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
from pylabrobot.capabilities.arms.standard import GripperLocation as GripperPose
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.paa.kx2.kx2_driver import (
  CanError,
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

  class Axis(IntEnum):
    """KX2 axis -> CANopen node-ID mapping.

    Lives here (not in the driver) because the driver is axis-agnostic and
    deals only with node IDs. External code should reference
    ``KX2ArmBackend.Axis``.
    """

    SHOULDER = 1
    Z = 2
    ELBOW = 3
    WRIST = 4
    RAIL = 5
    SERVO_GRIPPER = 6

  def __init__(
    self,
    driver: KX2Driver,
    gripper_length: float = 0.0,
    gripper_z_offset: float = 0.0,
  ) -> None:
    super().__init__()
    self.driver = driver
    self.gripper_length = float(gripper_length)
    self.gripper_z_offset = float(gripper_z_offset)

    self.digital_input_assignment = {}  # TODO: just cache?
    self.AnalogInputAssignment = {}
    self.output_assignment = {}
    self.motor_conversion_factor_ax = {}
    self.max_travel_ax = {}
    self.min_travel_ax = {}
    self.unlimited_travel_ax = {}
    self.absolute_encoder_ax = {}
    self.max_vel_ax = {}
    self.max_accel_ax = {}

    self.g_joint_move_direction = {
      1: JointMoveDirection.Normal,
      2: JointMoveDirection.Normal,
      3: JointMoveDirection.Normal,
      4: JointMoveDirection.Normal,
      6: JointMoveDirection.Normal,
    }

    self.node_id_list = [1, 2, 3, 4, 6]

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    # Driver has already brought CAN up (connect + node discovery + PDO
    # mapping) via Device.setup(). Now read per-drive parameters, enable
    # motion axes, and initialize the servo gripper.
    await self.drive_get_parameters(self.driver.node_id_list)

    await asyncio.sleep(2)

    for axis in MOTION_AXES:
      try:
        await self.driver.motor_enable(node_id=int(axis), state=True, use_ds402=True)
      except Exception as e:
        logger.warning("Error enabling motor on axis %s: %s", axis, e)

    await self.servo_gripper_initialize()

  # -- robot-level homing / estop (moved from driver) ---------------------

  async def get_estop_state(self) -> bool:
    """Return True if the arm is in estop, False otherwise.

    Reads the shoulder drive's SR (status register) via the binary
    interpreter. Bits 14/15 encode the stop/safety state.
    """
    r = await self.driver.query_int(int(self.Axis.SHOULDER), "SR", 1)
    if r != 8438016:
      logger.warning("get_estop_state: SR register unexpected value %d (expected 8438016)", r)
    b14 = (r & 0x4000) == 0x4000
    b15 = (r & 0x8000) == 0x8000
    return (not b14) and (not b15)

  async def _motor_set_homed_status(self, axis: int, status: HomeStatus) -> None:
    await self.driver.write(int(axis), "UI", 3, int(status))

  async def motor_get_homed_status(self, node_id: int) -> HomeStatus:
    return HomeStatus(await self.driver.query_int(int(node_id), "UI", 3))

  async def _motor_reset_encoder_position(self, axis: int, position: float) -> None:
    await self.driver.write(int(axis), "HM", 1, 0)
    await self.driver.write(int(axis), "HM", 3, 0)
    await self.driver.write(int(axis), "HM", 4, 0)
    await self.driver.write(int(axis), "HM", 5, 0)
    # Old code packed `position` as int32 via `int(round(float(str(position))))`;
    # preserve that rounding semantic for callers that pass fractional values.
    await self.driver.write(int(axis), "HM", 2, int(round(position)))
    await self.driver.write(int(axis), "HM", 1, 1)

  async def _motor_hard_stop_search(
    self,
    axis: int,
    srch_vel: int,
    srch_acc: int,
    max_pe: int,
    hs_pe: int,
    timeout: float,
  ) -> None:
    nid = int(axis)
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
    axis: int,
    srch_vel: int,
    srch_acc: int,
    positive_direction: bool,
    timeout: float,
  ) -> tuple:
    nid = int(axis)
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
    axis: int,
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
    nid = int(axis)

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
        node_id=int(self.Axis.SERVO_GRIPPER), state=True, use_ds402=False
      )
    except Exception as e:
      logger.warning(
        "Error enabling servo gripper motor on node %s: %s", self.Axis.SERVO_GRIPPER, e
      )

    await self.servo_gripper_home()

    await self.servo_gripper_close()

  async def servo_gripper_home(self) -> None:
    sg = int(self.Axis.SERVO_GRIPPER)
    await self.driver.write(sg, "PL", 1, self.servo_gripper_peak_current)
    await self.driver.write(sg, "CL", 1, self.servo_gripper_continuous_current)

    await self.home_motor(
      axis=self.Axis.SERVO_GRIPPER,
      hs_offset=self.servo_gripper_home_hard_stop_offset,
      ind_offset=self.servo_gripper_home_index_offset,
      home_pos=self.servo_gripper_home_pos,
      srch_vel=self.servo_gripper_home_search_vel,
      srch_acc=self.servo_gripper_home_search_accel,
      max_pe=self.servo_gripper_home_default_position_error,
      hs_pe=self.servo_gripper_home_hard_stop_position_error,
      offset_vel=self.servo_gripper_home_offset_vel,
      offset_acc=self.servo_gripper_home_offset_accel,
      timeout=self.servo_gripper_home_timeout_msec / 1000,
    )

    await self.servo_gripper_set_default_gripping_force(100)

  async def servo_gripper_set_default_gripping_force(self, max_force_percent: int) -> None:
    max_force_percent = max(10, min(max_force_percent, 100))

    cont_current = self.servo_gripper_continuous_current * max_force_percent / 100.0
    peak_current = self.servo_gripper_peak_current * max_force_percent / 100.0

    sg = int(self.Axis.SERVO_GRIPPER)

    # 1) PL with unscaled peak current
    await self.driver.write(sg, "PL", 1, self.servo_gripper_peak_current)

    # 2) CL with scaled continuous current
    await self.driver.write(sg, "CL", 1, cont_current)

    # 3) PL with scaled peak current
    await self.driver.write(sg, "PL", 1, peak_current)

    self.servo_gripper_force_percent = max_force_percent

  async def get_servo_gripper_max_force(self) -> float:
    """Return current gripping force as percentage of max (0-1)."""
    sg = int(self.Axis.SERVO_GRIPPER)
    cl = await self.driver.query_float(sg, "CL", 1)
    iq = await self.driver.query_float(sg, "IQ", 0)

    if cl == 0:
      return 0.0

    return max(0.0, min(abs(iq / cl), 1.0))

  async def check_plate_gripped(self, num_attempts: int = 5) -> None:
    for _ in range(num_attempts):
      motor_status = await self.driver.query_int(
        int(self.Axis.SERVO_GRIPPER), "MS", 1
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

        current_position = await self.motor_get_current_position(self.Axis.SERVO_GRIPPER)
        closed_position = 1
        if abs(current_position - closed_position) < 2.0 / 625:
          raise RuntimeError(
            "Servo Gripper was able to move all the way to the closed position, which indicates the absence of an object in the gripper.  The closed position value may need to be decreased."
          )

        return

      elif motor_status == 2:
        motor_fault = await self.driver.motor_get_fault(int(self.Axis.SERVO_GRIPPER))
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
      {self.Axis.SERVO_GRIPPER: closed_position},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )

    if check_plate_gripped:
      await self.check_plate_gripped()

  async def servo_gripper_open(self, open_position: float) -> None:
    await self.motors_move_joint(
      {self.Axis.SERVO_GRIPPER: open_position},
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
    z = int(self.Axis.Z)

    # MoveCount -> Z axis, UI index 22
    await self.driver.write(z, "UI", 22, int(move_count))

    # Travel[] -> each node, UF index 5
    # The source looked 1-based for Travel and 0-based for NodeIDList; handle both cleanly.
    if len(travel) == len(self.node_id_list) + 1:
      pairs = zip(self.node_id_list, travel[1:])
    else:
      pairs = zip(self.node_id_list, travel)

    for node_id, dist in pairs:
      await self.driver.write(int(node_id), "UF", 5, float(dist))

    # LastMaintenancePerformed -> Z axis, UF index 6
    await self.driver.write(z, "UF", 6, float(last_maintenance_performed))

    # MaintenanceRequired -> Z axis, UI index 23
    await self.driver.write(z, "UI", 23, 1 if maintenance_required else 0)

    # LastMaintenancePerformedDate -> Z axis, UI index 21
    await self.driver.write(z, "UI", 21, int(last_maintenance_performed_date))

    # Rail (if present)
    if self.robot_on_rail:
      rail = int(self.Axis.RAIL)
      await self.driver.write(rail, "UF", 6, float(last_maintenance_performed_rail))
      await self.driver.write(rail, "UI", 23, 1 if maintenance_required_rail else 0)
      await self.driver.write(rail, "UI", 21, int(last_maintenance_performed_date_rail))

  async def drive_get_parameters(self, node_ids) -> None:
    self.robot_on_rail = False
    self.has_servo_gripper = False

    nodes = (
      [int(b) for b in node_ids]
      if isinstance(node_ids, (bytes, bytearray))
      else [int(x) for x in node_ids]
    )

    def set2d(store: dict, axis: int, ch: int, value: str) -> None:
      store.setdefault(axis, {})[ch] = value

    # Pass 1: identify axes by UI[4]
    uis = set()
    for node in nodes:
      if node == await self.driver.query_int(int(node), "UI", 4):
        uis.add(node)
    for required_axis in MOTION_AXES:
      if required_axis.value not in uis:
        raise CanError(f"Missing required axis with UI[4]={required_axis}")
    if 5 in uis:
      self.robot_on_rail = True
      warnings.warn("Rails has not been tested for KX2 robots.")
    if 6 in uis:
      self.has_servo_gripper = True

    # Pass 2: per-axis parameters
    for axis in nodes:
      logger.debug("Reading parameters for axis %s", axis)

      # UI[5..10] digital inputs
      for ui_idx in range(5, 11):
        ret = await self.driver.query_int(int(axis), "UI", ui_idx)
        ch = (ui_idx - 5) + 1
        if ret == 101:
          set2d(self.digital_input_assignment, axis, ch, "GripperSensor")
          self.GripperSensorAxis = axis
          self.GripperSensorInput = ch
        elif ret == 102:
          set2d(self.digital_input_assignment, axis, ch, "TeachButton")
          self.TeachButtonAxis = axis
          self.TeachButtonInput = ch
        else:
          set2d(
            self.digital_input_assignment,
            axis,
            ch,
            "" if ret <= 0 else f"AuxPin{ret}",
          )

      # UI[11..12] analog inputs
      for ui_idx in range(11, 13):
        ret = await self.driver.query_int(int(axis), "UI", ui_idx)
        ch = (ui_idx - 11) + 1
        set2d(
          self.AnalogInputAssignment,
          axis,
          ch,
          "" if ret <= 0 else f"AuxPin{ret}",
        )

      # UI[13..16] outputs
      for ui_idx in range(13, 17):
        ret = await self.driver.query_int(int(axis), "UI", ui_idx)
        ch = (ui_idx - 13) + 1

        if ret == 101:
          set2d(self.output_assignment, axis, ch, "IndicatorLightRed")
          self.IndicatorLightRedAxis = axis
          self.IndicatorLightRedOutput = ch
        elif ret == 102:
          set2d(self.output_assignment, axis, ch, "IndicatorLightGreen")
          self.IndicatorLightGreenAxis = axis
          self.IndicatorLightGreenOutput = ch
        elif ret == 103:
          set2d(self.output_assignment, axis, ch, "IndicatorLightBlue")
          self.IndicatorLightBlueAxis = axis
          self.IndicatorLightBlueOutput = ch
        elif ret == 104:
          set2d(self.output_assignment, axis, ch, "IndicatorLight")
          self.IndicatorLightAxis = axis
          self.IndicatorLightOutput = ch
        elif ret == 105:
          set2d(self.output_assignment, axis, ch, "Buzzer")
          self.BuzzerAxis = axis
          self.BuzzerOutput = ch
        else:
          set2d(
            self.output_assignment,
            axis,
            ch,
            "" if ret <= 0 else f"AuxPin{ret}",
          )

      # UI[24] drive serial number (queried but not currently stored)
      await self.driver.query_int(int(axis), "UI", 24)

      # UF[1], UF[2] conversion factor
      uf1 = await self.driver.query_float(int(axis), "UF", 1)
      uf2 = await self.driver.query_float(int(axis), "UF", 2)
      if uf1 == 0.0 or uf2 == 0.0:
        raise CanError(f"Invalid Motor Conversion Factor for axis {axis}. UF[1]={uf1}, UF[2]={uf2}")
      self.motor_conversion_factor_ax[axis] = uf1 / uf2

      # XM / travel
      xm1 = await self.driver.query_int(int(axis), "XM", 1)
      xm2 = await self.driver.query_int(int(axis), "XM", 2)
      uf3 = await self.driver.query_float(int(axis), "UF", 3)
      uf4 = await self.driver.query_float(int(axis), "UF", 4)
      vh3 = await self.driver.query_int(int(axis), "VH", 3)
      vl3 = await self.driver.query_int(int(axis), "VL", 3)

      self.max_travel_ax[axis] = uf3
      self.min_travel_ax[axis] = uf4

      if ((xm1 == 0) and (xm2 == 0)) or ((xm1 <= vl3) and (xm2 >= vh3)):
        self.unlimited_travel_ax[axis] = False
      elif (xm1 > vl3) and (xm2 < vh3):
        self.unlimited_travel_ax[axis] = True
        if axis in MOTION_AXES:
          self.g_joint_move_direction[axis] = JointMoveDirection.ShortestWay
      else:
        raise CanError(
          f"Invalid travel limits or modulo settings for axis {axis}. "
          f"VH[3]={vh3}, VL[3]={vl3}, XM[1]={xm1}, XM[2]={xm2}"
        )

      # Encoder socket/type
      ca45 = await self.driver.query_int(int(axis), "CA", 45)
      if not (0 < ca45 <= 4):
        raise CanError(f"Invalid encoder socket number received from axis {axis}. CA[45]={ca45}")

      enc_type = await self.driver.query_int(int(axis), "CA", 40 + ca45)
      if enc_type in (1, 2):
        self.absolute_encoder_ax[axis] = False
      elif enc_type == 24:
        self.absolute_encoder_ax[axis] = True
      else:
        raise CanError(
          f"Unsupported encoder type specified for axis {axis}. CA[4{ca45}]={enc_type}"
        )

      ca46 = await self.driver.query_int(int(axis), "CA", 46)
      if ca45 == ca46:
        num3 = 1.0
      else:
        num3 = await self.driver.query_float(int(axis), "FF", 3)

      denom = self.motor_conversion_factor_ax[axis] * num3  # or 1.0

      sp2 = await self.driver.query_int(int(axis), "SP", 2)
      if sp2 == 100000:
        vh2 = await self.driver.query_int(int(axis), "VH", 2)
        self.max_vel_ax[axis] = vh2 / 1.01 / denom
      else:
        self.max_vel_ax[axis] = sp2 / denom

      sd0 = await self.driver.query_int(int(axis), "SD", 0)
      self.max_accel_ax[axis] = sd0 / 1.01 / denom

    # Robot-level params from shoulder_ax
    shoulder = int(self.Axis.SHOULDER)

    self.base_to_gripper_clearance_z = await self.driver.query_float(shoulder, "UF", 6)
    self.base_to_gripper_clearance_arm = await self.driver.query_float(shoulder, "UF", 7)
    self.wrist_offset = await self.driver.query_float(shoulder, "UF", 8)
    self.elbow_offset = await self.driver.query_float(shoulder, "UF", 9)
    self.elbow_zero_offset = await self.driver.query_float(shoulder, "UF", 10)
    self.MaxLinearVelMMPerSec = await self.driver.query_float(shoulder, "UF", 11)
    self.MaxLinearAccelMMPerSec2 = await self.driver.query_float(shoulder, "UF", 12)
    self.MaxLinearJerkMMPerSec3 = await self.driver.query_float(shoulder, "UF", 13)
    self.MaxRotaryVelDegPerSec = await self.driver.query_float(shoulder, "UF", 14)
    self.MaxRotaryAccelDegPerSec2 = await self.driver.query_float(shoulder, "UF", 15)

    ui17 = await self.driver.query_int(shoulder, "UI", 17)
    self.pvt_time_interval_msec = (
      25 if (ui17 <= 0 or ui17 > 255) else ui17
    )

    # Servo gripper params (only if present)
    sg = int(self.Axis.SERVO_GRIPPER)
    self.servo_gripper_home_pos = int(await self.driver.query_float(sg, "UF", 6))
    self.servo_gripper_home_search_vel = int(await self.driver.query_float(sg, "UF", 7))
    self.servo_gripper_home_search_accel = int(await self.driver.query_float(sg, "UF", 8))
    self.servo_gripper_home_default_position_error = int(await self.driver.query_float(sg, "UF", 9))
    self.servo_gripper_home_hard_stop_position_error = int(await self.driver.query_float(sg, "UF", 10))
    self.servo_gripper_home_hard_stop_offset = int(await self.driver.query_float(sg, "UF", 11))
    self.servo_gripper_home_index_offset = int(await self.driver.query_float(sg, "UF", 12))
    self.servo_gripper_home_offset_vel = int(await self.driver.query_float(sg, "UF", 13))
    self.servo_gripper_home_offset_accel = int(await self.driver.query_float(sg, "UF", 14))
    self.servo_gripper_home_timeout_msec = int(await self.driver.query_float(sg, "UF", 15))
    self.servo_gripper_continuous_current = await self.driver.query_float(sg, "UF", 16)
    self.servo_gripper_peak_current = await self.driver.query_float(sg, "UF", 17)

  def convert_elbow_position_to_angle(self, elbow_pos: float) -> float:
    max_travel = self.max_travel_ax[self.Axis.ELBOW]
    denom = max_travel + self.elbow_zero_offset

    if elbow_pos > max_travel:
      x = (2.0 * max_travel - elbow_pos + self.elbow_zero_offset) / denom
      angle = math.asin(x) * (180.0 / math.pi)
      elbow_angle = 90.0 + angle
    else:
      x = (elbow_pos + self.elbow_zero_offset) / denom
      angle = math.asin(x) * (180.0 / math.pi)
      elbow_angle = angle

    return elbow_angle

  def convert_elbow_angle_to_position(self, elbow_angle_deg: float) -> float:
    elbow_pos = (self.max_travel_ax[self.Axis.ELBOW] + self.elbow_zero_offset) * math.sin(
      elbow_angle_deg * (math.pi / 180.0)
    ) - self.elbow_zero_offset

    if elbow_angle_deg > 90.0:
      elbow_pos = 2.0 * self.max_travel_ax[self.Axis.ELBOW] - elbow_pos

    return elbow_pos

  async def motor_get_current_position(self, axis: "KX2ArmBackend.Axis") -> float:
    raw = await self.driver.motor_get_current_position(node_id=int(axis), pu=self.unlimited_travel_ax[axis])
    c = self.motor_conversion_factor_ax[axis]
    if axis == self.Axis.ELBOW:
      return self.convert_elbow_angle_to_position(elbow_angle_deg=raw / c)
    else:
      if c == 0:
        logger.warning("Axis %s has conversion factor of 0", axis)
        return 0.0
      else:
        return raw / c

  async def read_input(self, axis: int, input_num: int) -> bool:
    return await self.driver.read_input(node_id=axis, input_num=0x10 + input_num)

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
    cmd_pos: Dict["KX2ArmBackend.Axis", float],
    cmd_vel_pct: float,
    cmd_accel_pct: float,
  ) -> Optional[MotorsMovePlan]:
    target = cmd_pos.copy()
    axes = list(target.keys())

    enc_pos: Dict[KX2ArmBackend.Axis, float] = {}
    enc_vel: Dict[KX2ArmBackend.Axis, float] = {}
    enc_accel: Dict[KX2ArmBackend.Axis, float] = {}
    skip_ax: Dict[KX2ArmBackend.Axis, bool] = {}

    # input validation / travel limits / done-wait logic
    if cmd_vel_pct <= 0.0 or cmd_vel_pct > 100.0:
      raise ValueError("CmdVel out of range")
    if cmd_accel_pct <= 0.0 or cmd_accel_pct > 100.0:
      raise ValueError("CmdAccel out of range")

    # Convert elbow cmd from position->angle for planning math
    if self.Axis.ELBOW in axes:
      target[self.Axis.ELBOW] = self.convert_elbow_position_to_angle(target[self.Axis.ELBOW])

    # Clearance check
    if self.Axis.Z in axes:
      if (
        target[self.Axis.Z] < self.min_travel_ax[self.Axis.Z] + self.base_to_gripper_clearance_z
        and target[self.Axis.ELBOW] < self.base_to_gripper_clearance_arm
      ):
        raise ValueError("Base-to-gripper clearance violated")

    # Determine current/start positions
    curr = await self.request_joint_position()

    # Elbow: convert both target and start to angle for distance math
    if self.Axis.ELBOW in curr:
      curr[self.Axis.ELBOW] = self.convert_elbow_position_to_angle(curr[self.Axis.ELBOW])

    # Handle unlimited travel normalization when direction != NORMAL
    for ax in axes:
      if (
        self.unlimited_travel_ax[ax]
        and self.g_joint_move_direction[ax] != JointMoveDirection.Normal
      ):
        target[ax] = self._wrap_to_range(target[ax], self.min_travel_ax[ax], self.max_travel_ax[ax])

    # Distances, skip flags, initial v/a per axis
    dist: Dict[KX2ArmBackend.Axis, float] = {}
    v: Dict[KX2ArmBackend.Axis, float] = {}
    a: Dict[KX2ArmBackend.Axis, float] = {}
    accel_time: Dict[KX2ArmBackend.Axis, float] = {}
    total_time: Dict[KX2ArmBackend.Axis, float] = {}

    for ax in axes:
      if self.unlimited_travel_ax[ax]:
        d = target[ax] - curr[ax]
        span = self.max_travel_ax[ax] - self.min_travel_ax[ax]
        dir_ = self.g_joint_move_direction[ax]

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

      v[ax] = (cmd_vel_pct / 100.0) * self.max_vel_ax[ax]
      a[ax] = (cmd_accel_pct / 100.0) * self.max_accel_ax[ax]

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
      conv = self.motor_conversion_factor_ax[ax]
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
          direction=self.g_joint_move_direction[ax],
        )
        for ax in axes
      ],
      move_time=move_time,
    )

  async def motors_move_joint(
    self,
    cmd_pos: Dict["KX2ArmBackend.Axis", float],
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

  def convert_cartesian_to_joint_position(self, pose: GripperPose) -> Dict["KX2ArmBackend.Axis", float]:
    if pose.rotation.x != 0 or pose.rotation.y != 0:
      raise ValueError("Only Z rotation is supported for KX2")

    # Gripper -> wrist: the incoming pose describes the gripper clamp point;
    # the joint-space math operates on the wrist axis. Rigid offset with the
    # gripper length on the radial axis (governed by world rotation z) and the
    # gripper z offset downward.
    ang = math.radians(pose.rotation.z)
    x = pose.location.x - self.gripper_length * math.sin(ang)
    y = pose.location.y + self.gripper_length * math.cos(ang)
    wrist_z = pose.location.z + self.gripper_z_offset

    joint_position: Dict[KX2ArmBackend.Axis, float] = {}

    # Shoulder axis
    shoulder = -math.degrees(math.atan2(x, y))
    if abs(shoulder + 180.0) < 1e-12:
      shoulder = 180.0

    joint_position[self.Axis.SHOULDER] = shoulder

    # Z axis
    joint_position[self.Axis.Z] = wrist_z

    # Elbow axis
    elbow = (
      math.sqrt(x * x + y * y) - self.wrist_offset - self.elbow_offset - self.elbow_zero_offset
    )
    joint_position[self.Axis.ELBOW] = elbow

    # Wrist axis
    wrist = (pose.rotation.z) - joint_position[self.Axis.SHOULDER]
    joint_position[self.Axis.WRIST] = wrist

    # Wrap wrist into travel range if possible by +/- 360
    w = joint_position[self.Axis.WRIST]
    wmin = self.min_travel_ax[self.Axis.WRIST]
    wmax = self.max_travel_ax[self.Axis.WRIST]
    if (w < wmin - 0.001) and (w + 360.0 <= wmax):
      w += 360.0
    elif (w > wmax + 0.001) and (w - 360.0 >= wmin):
      w -= 360.0
    joint_position[self.Axis.WRIST] = w

    return joint_position

  def convert_joint_position_to_cartesian(
    self, joint_position: Dict["KX2ArmBackend.Axis", float]
  ) -> GripperPose:
    r = (
      self.wrist_offset + self.elbow_offset + self.elbow_zero_offset + joint_position[self.Axis.ELBOW]
    )
    sh_deg = joint_position[self.Axis.SHOULDER]
    sh = math.radians(sh_deg)

    wrist_x = -(r) * math.sin(sh)
    wrist_y = (r) * math.cos(sh)
    wrist_z = joint_position[self.Axis.Z]

    rotation_z = joint_position[self.Axis.WRIST] + sh_deg

    # wrap to [-180, 180]
    if rotation_z > 180.0:
      rotation_z -= 360.0
    if rotation_z < -180.0:
      rotation_z += 360.0

    # Wrist -> gripper: inverse of the gripper -> wrist translation in
    # convert_cartesian_to_joint_position so callers observe the gripper clamp
    # point, symmetric with what they pass in.
    ang = math.radians(rotation_z)
    gripper_x = wrist_x + self.gripper_length * math.sin(ang)
    gripper_y = wrist_y - self.gripper_length * math.cos(ang)
    gripper_z = wrist_z - self.gripper_z_offset

    return GripperPose(
      location=Coordinate(x=gripper_x, y=gripper_y, z=gripper_z),
      rotation=Rotation(z=rotation_z),
    )

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
      await self.driver.motor_emergency_stop(node_id=int(axis))

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError(
      "KX2 does not define a default park pose. Use move_to_joint_position with a "
      "site-specific safe configuration."
    )

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    current_joint_pos = await self.request_joint_position()
    return self.convert_joint_position_to_cartesian(current_joint_pos)

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    await self.motors_move_joint(
      {self.Axis.SERVO_GRIPPER: gripper_width},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    if not isinstance(backend_params, KX2ArmBackend.GripParams):
      backend_params = KX2ArmBackend.GripParams()
    await self.motors_move_joint(
      {self.Axis.SERVO_GRIPPER: gripper_width},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )
    if backend_params.check_plate_gripped:
      await self.check_plate_gripped()

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    pos = await self.motor_get_current_position(self.Axis.SERVO_GRIPPER)
    return abs(pos) < 1.0

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, KX2ArmBackend.CartesianMoveParams):
      backend_params = KX2ArmBackend.CartesianMoveParams()
    pose = GripperLocation(location=location, rotation=Rotation(z=direction))
    joint_pos = self.convert_cartesian_to_joint_position(pose)
    await self.motors_move_joint(
      cmd_pos=joint_pos,
      cmd_vel_pct=backend_params.vel_pct,
      cmd_accel_pct=backend_params.accel_pct,
    )

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
    cmd_pos = {self.Axis(int(k)): float(v) for k, v in position.items()}
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
      self.Axis.SHOULDER: await self.motor_get_current_position(self.Axis.SHOULDER),
      self.Axis.Z: await self.motor_get_current_position(self.Axis.Z),
      self.Axis.ELBOW: await self.motor_get_current_position(self.Axis.ELBOW),
      self.Axis.WRIST: await self.motor_get_current_position(self.Axis.WRIST),
      self.Axis.SERVO_GRIPPER: await self.motor_get_current_position(self.Axis.SERVO_GRIPPER),
    }

  async def start_freedrive_mode(
    self, free_axes: List[int], backend_params: Optional[BackendParams] = None
  ) -> None:
    # KX2 frees all motion axes at once; free_axes is accepted for API parity.
    del free_axes
    for axis in MOTION_AXES:
      await self.driver.motor_enable(node_id=int(axis), state=False, use_ds402=True)

  async def stop_freedrive_mode(self, backend_params: Optional[BackendParams] = None) -> None:
    for axis in MOTION_AXES:
      await self.driver.motor_enable(node_id=int(axis), state=True, use_ds402=True)


# Motion axes = the four coordinated joints. Defined at module scope (after the
# class body so `KX2ArmBackend.Axis` exists) and imported by callers that need
# to iterate "all motion axes".
MOTION_AXES = (
  KX2ArmBackend.Axis.SHOULDER,
  KX2ArmBackend.Axis.Z,
  KX2ArmBackend.Axis.ELBOW,
  KX2ArmBackend.Axis.WRIST,
)
