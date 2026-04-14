import asyncio
import logging
import math
import warnings
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from pylabrobot.capabilities.arms.backend import (
  CanFreedrive,
  HasJoints,
  OrientableGripperArmBackend,
)
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.arms.standard import GripperLocation as GripperPose
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate, Rotation

if TYPE_CHECKING:
  from pylabrobot.paa.kx2.kx2_driver import KX2Driver

logger = logging.getLogger(__name__)


class KX2Axis(IntEnum):
  SHOULDER = 1
  Z = 2
  ELBOW = 3
  WRIST = 4
  RAIL = 5
  SERVO_GRIPPER = 6


MOTION_AXES = (KX2Axis.SHOULDER, KX2Axis.Z, KX2Axis.ELBOW, KX2Axis.WRIST)


def _is_number(s: str) -> bool:
  try:
    float(str(s).strip())
    return True
  except Exception:
    return False


def _to_float(s: str, default: float = 0.0) -> float:
  try:
    return float(str(s).strip())
  except Exception:
    warnings.warn(f"Error converting '{s}' to float, returning default")
    return default


class JointMoveDirection(IntEnum):
  Normal = 0
  Clockwise = 1
  Counterclockwise = 2
  ShortestWay = 3


class HomeStatus(IntEnum):
  NotHomed = 0
  Homed = 1
  InitializedWithoutHoming = 2


class CmdType(IntEnum):
  ValQuery = 1
  ValSet = 2
  Execute = 3


class ValType(IntEnum):
  Int = 1
  Float = 2


class COBType(IntEnum):
  NMT = 0
  EMCY = 1
  SYNC = 1
  TIMESTAMP = 2
  TPDO1 = 3
  RPDO1 = 4
  TPDO2 = 5
  RPDO2 = 6
  TPDO3 = 7
  RPDO3 = 8
  TPDO4 = 9
  RPDO4 = 10
  TSDO = 11
  RSDO = 12
  ERRCTRL = 14
  HEARTBEAT = 14


class RPDO(IntEnum):
  RPDO1 = 1
  RPDO3 = 3
  RPDO4 = 4


class PDOTransmissionType(IntEnum):
  SynchronousAcyclic = 0
  SynchronousCyclic = 1
  EventDrivenManf = 254  # 0xFE
  EventDrivenDev = 255  # 0xFF


class RPDOMappedObject(IntEnum):
  NotMapped = 0
  ControlWord = 0x60400010
  TargetTorque = 0x60710010
  MaxTorque = 0x60720010
  TargetPosition = 0x607A0020
  VelocityOffset = 0x60B10020
  TargetPositionIP = 0x60C10120
  TargetVelocityIP = 0x60C10220
  DigitalOutputs = 0x60FE0020
  TargetVelocity = 0x60FF0020


class TPDO(IntEnum):
  TPDO1 = 1
  TPDO3 = 3
  TPDO4 = 4


class TPDOTrigger(IntEnum):
  MotionComplete = 0
  MainHomingComplete = 1
  AuxiliaryHomingComplete = 2
  MotorShutDownByException = 3
  MotorStarted = 4
  UserProgramEmitCommand = 5
  OSInterpreterExecutionComplete = 6
  MotionStartedEvent = 8
  PDODataChanged = 24
  DigitalInputEvent = 26
  StatusWordEvent = 27
  BinaryInterpreterCommandComplete = 31


class TPDOMappedObject(IntEnum):
  NotMapped = 0
  Timestamp = 0x20410020
  PVTHeadPointer = 0x2F110010
  PVTTailPointer = 0x2F120010
  StatusWord = 0x60410010
  PositionActualValue = 0x60640020
  VelocityDemandValue = 0x606B0020
  VelocityActualValue = 0x606C0020
  TorqueDemandValue = 0x60740010
  TorqueActualValue = 0x60770010
  IPBufferPosition = 0x60C40410
  DigitalInputs = 0x60FD0020


class ElmoObjectDataType(IntEnum):
  UNSIGNED8 = 0
  UNSIGNED16 = 1
  UNSIGNED32 = 2
  UNSIGNED64 = 3
  INTEGER8 = 4
  INTEGER16 = 5
  INTEGER32 = 6
  INTEGER64 = 7
  STR = 8



class CanError(Exception):
  """Custom exception for CAN motor errors."""


@dataclass
class MotorMoveParam:
  axis: "KX2Axis"
  position: int
  velocity: int
  acceleration: int
  relative: bool = False
  direction: JointMoveDirection = JointMoveDirection.ShortestWay


@dataclass
class MotorsMovePlan:
  moves: List[MotorMoveParam]
  move_time: float = 10.0


class KX2ArmBackend(OrientableGripperArmBackend, HasJoints, CanFreedrive):
  """Arm-capability backend for the PAA KX2.

  Owns a :class:`KX2Driver` (low-level CAN transport) and implements the
  capability-based arm interface (``OrientableGripperArmBackend`` +
  ``HasJoints`` + ``CanFreedrive``) directly on top of the drive primitives.
  """

  def __init__(self, driver: "KX2Driver") -> None:
    super().__init__()
    self.driver = driver

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
    # Driver has already brought CAN up (connect + node discovery) via
    # Device.setup(). Now read per-drive parameters, finish CANopen mapping,
    # enable motion axes, and initialize the servo gripper.
    await self.drive_get_parameters(self.driver.node_id_list)
    await self.driver._connect_part_two()

    await asyncio.sleep(2)

    for axis in MOTION_AXES:
      if self.unlimited_travel_ax[axis]:
        self.g_joint_move_direction[axis] = JointMoveDirection.ShortestWay

    for axis in MOTION_AXES:
      try:
        await self.driver._motor_enable(axis=axis, state=True)
      except Exception as e:
        logger.warning("Error enabling motor on axis %s: %s", axis, e)

    await self.servo_gripper_initialize()

  async def servo_gripper_initialize(self):
    try:
      await self.driver._motor_enable(axis=KX2Axis.SERVO_GRIPPER, state=True)
    except Exception as e:
      logger.warning(
        "Error enabling servo gripper motor on node %s: %s", KX2Axis.SERVO_GRIPPER, e
      )

    await self.servo_gripper_home()

    await self.servo_gripper_close()

  async def servo_gripper_home(self) -> None:
    await self.motor_send_command(
      node_id=int(KX2Axis.SERVO_GRIPPER),
      motor_command="PL",
      index=1,
      value=str(self.servo_gripper_peak_current),
      val_type=ValType.Float,
    )

    await self.motor_send_command(
      node_id=int(KX2Axis.SERVO_GRIPPER),
      motor_command="CL",
      index=1,
      value=str(self.servo_gripper_continuous_current),
      val_type=ValType.Float,
    )

    await self.driver._home_motor(
      axis=KX2Axis.SERVO_GRIPPER,
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
    if max_force_percent < 10:
      max_force_percent = 10
    elif max_force_percent > 100:
      max_force_percent = 100

    cont_current = float(self.servo_gripper_continuous_current) * max_force_percent / 100.0
    peak_current = float(self.servo_gripper_peak_current) * max_force_percent / 100.0

    axis = KX2Axis.SERVO_GRIPPER

    # 1) PL with unscaled peak current
    await self.motor_send_command(
      axis, "PL", 1, str(self.servo_gripper_peak_current), val_type=ValType.Float
    )

    # 2) CL with scaled continuous current
    await self.motor_send_command(axis, "CL", 1, str(cont_current), val_type=ValType.Float)

    # 3) PL with scaled peak current
    await self.motor_send_command(axis, "PL", 1, str(peak_current), val_type=ValType.Float)

    self.servo_gripper_force_percent = max_force_percent

  async def get_servo_gripper_max_force(self) -> float:
    """Return current gripping force as percentage of max (0-1)."""
    cl = await self.motor_send_command(
      node_id=KX2Axis.SERVO_GRIPPER,
      motor_command="CL",
      index=1,
    )

    iq = await self.motor_send_command(
      node_id=KX2Axis.SERVO_GRIPPER,
      motor_command="IQ",
      index=0,
    )

    if cl == 0:
      return 0

    return max(0, min(abs(iq / cl), 1))

  async def check_plate_gripped(self, num_attempts: int = 5) -> None:
    for _ in range(num_attempts):
      motor_status = await self.motor_send_command(
        node_id=KX2Axis.SERVO_GRIPPER,
        motor_command="MS",
        index=1,
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

        current_position = await self.motor_get_current_position(KX2Axis.SERVO_GRIPPER)
        closed_position = 1
        if abs(current_position - closed_position) < 2.0 / 625:
          raise RuntimeError(
            "Servo Gripper was able to move all the way to the closed position, which indicates the absence of an object in the gripper.  The closed position value may need to be decreased."
          )

        return

      elif motor_status == 2:
        motor_fault = self.driver.motor_get_fault(KX2Axis.SERVO_GRIPPER)
        if motor_fault is None:
          raise RuntimeError("Error querying whether plate is gripped. Error querying motor fault.")
        raise RuntimeError(
          f"Servo Gripper may not have gripped the plate correctly. Motor fault: '{motor_fault}'"
        )

      asyncio.sleep(0.05)

    raise RuntimeError(
      f"Servo Gripper was unable to confirm that the plate is gripped after {num_attempts} attempts."
    )

  async def servo_gripper_close(self, closed_position: int = 0, check_plate_gripped=True) -> None:
    await self.motors_move_joint(
      {KX2Axis.SERVO_GRIPPER: closed_position},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )

    if check_plate_gripped:
      await self.check_plate_gripped()

  async def servo_gripper_open(self, open_position: float) -> None:
    await self.motors_move_joint(
      {KX2Axis.SERVO_GRIPPER: open_position},
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
    # MoveCount -> Z axis, UI index 22
    await self.motor_send_command(
      node_id=KX2Axis.Z,
      motor_command="UI",
      index=22,
      value=str(int(move_count)),
      val_type=ValType.Int,
      low_priority=False,
    )

    # Travel[] -> each node, UF index 5
    # The source looked 1-based for Travel and 0-based for NodeIDList; handle both cleanly.
    if len(travel) == len(self.node_id_list) + 1:
      pairs = zip(self.node_id_list, travel[1:])
    else:
      pairs = zip(self.node_id_list, travel)

    for node_id, dist in pairs:
      await self.motor_send_command(
        node_id=int(node_id),
        motor_command="UF",
        index=5,
        value=str(float(dist)),
        val_type=ValType.Float,
        low_priority=True,
      )

    # LastMaintenancePerformed -> Z axis, UF index 6
    await self.motor_send_command(
      node_id=KX2Axis.Z,
      motor_command="UF",
      index=6,
      value=str(float(last_maintenance_performed)),
      val_type=ValType.Float,
      low_priority=True,
    )

    # MaintenanceRequired -> Z axis, UI index 23
    await self.motor_send_command(
      node_id=KX2Axis.Z,
      motor_command="UI",
      index=23,
      value="1" if maintenance_required else "0",
      val_type=ValType.Int,
      low_priority=False,
    )

    # LastMaintenancePerformedDate -> Z axis, UI index 21
    await self.motor_send_command(
      node_id=KX2Axis.Z,
      motor_command="UI",
      index=21,
      value=str(int(last_maintenance_performed_date)),
      val_type=ValType.Int,
      low_priority=False,
    )

    # Rail (if present)
    if self.robot_on_rail:
      await self.motor_send_command(
        node_id=KX2Axis.RAIL,
        motor_command="UF",
        index=6,
        value=str(float(last_maintenance_performed_rail)),
        val_type=ValType.Float,
        low_priority=True,
      )

      await self.motor_send_command(
        node_id=KX2Axis.RAIL,
        motor_command="UI",
        index=23,
        value="1" if maintenance_required_rail else "0",
        val_type=ValType.Int,
        low_priority=False,
      )

      await self.motor_send_command(
        node_id=KX2Axis.RAIL,
        motor_command="UI",
        index=21,
        value=str(int(last_maintenance_performed_date_rail)),
        val_type=ValType.Int,
        low_priority=False,
      )

  async def drive_get_parameters(self, node_ids) -> None:  # TODO: list[KX2Axis]
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
    uis = {
      node
      for node in nodes
      if node == await self.motor_send_command(node, "UI", 4, val_type=ValType.Int)
      for node in nodes
    }
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
        ret = await self.motor_send_command(axis, "UI", ui_idx, val_type=ValType.Int)
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
            "" if (not _is_number(ret) or _to_float(ret) <= 0.0) else f"AuxPin{ret}",
          )

      # UI[11..12] analog inputs
      for ui_idx in range(11, 13):
        ret = await self.motor_send_command(axis, "UI", ui_idx, val_type=ValType.Int)
        ch = (ui_idx - 11) + 1
        set2d(
          self.AnalogInputAssignment,
          axis,
          ch,
          "" if (not _is_number(ret) or _to_float(ret) <= 0.0) else f"AuxPin{ret}",
        )

      # UI[13..16] outputs
      for ui_idx in range(13, 17):
        ret = await self.motor_send_command(axis, "UI", ui_idx, val_type=ValType.Int)
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
            "" if (not _is_number(ret) or _to_float(ret) <= 0.0) else f"AuxPin{ret}",
          )

      # UI[24] drive serial number
      ret = await self.motor_send_command(axis, "UI", 24, val_type=ValType.Int)
      if _is_number(ret):
        # self.drive_serial_number[axis] = int(ret)
        pass

      # UF[1], UF[2] conversion factor
      uf1 = await self.motor_send_command(axis, "UF", 1, val_type=ValType.Float)
      uf2 = await self.motor_send_command(axis, "UF", 2, val_type=ValType.Float)
      if (
        not (_is_number(uf1) and _is_number(uf2)) or _to_float(uf1) == 0.0 or _to_float(uf2) == 0.0
      ):
        raise CanError(f"Invalid Motor Conversion Factor for axis {axis}. UF[1]={uf1}, UF[2]={uf2}")
      self.motor_conversion_factor_ax[axis] = _to_float(uf1) / _to_float(uf2)

      # XM / travel
      xm1 = await self.motor_send_command(axis, "XM", 1, val_type=ValType.Int)
      xm2 = await self.motor_send_command(axis, "XM", 2, val_type=ValType.Int)
      uf3 = await self.motor_send_command(axis, "UF", 3, val_type=ValType.Float)
      uf4 = await self.motor_send_command(axis, "UF", 4, val_type=ValType.Float)
      vh3 = await self.motor_send_command(axis, "VH", 3, val_type=ValType.Int)
      vl3 = await self.motor_send_command(axis, "VL", 3, val_type=ValType.Int)

      self.max_travel_ax[axis] = _to_float(uf3)
      self.min_travel_ax[axis] = _to_float(uf4)

      if not all(_is_number(x) for x in (xm1, xm2, vh3, vl3)):
        raise CanError(
          f"Invalid travel limits or modulo settings for axis {axis}. "
          f"VH[3]={vh3}, VL[3]={vl3}, XM[1]={xm1}, XM[2]={xm2}"
        )

      xm1v, xm2v, vh3v, vl3v = map(_to_float, (xm1, xm2, vh3, vl3))
      if ((xm1v == 0.0) and (xm2v == 0.0)) or ((xm1v <= vl3v) and (xm2v >= vh3v)):
        self.unlimited_travel_ax[axis] = False
      elif (xm1v > vl3v) and (xm2v < vh3v):
        self.unlimited_travel_ax[axis] = True
      else:
        raise CanError(
          f"Invalid travel limits or modulo settings for axis {axis}. "
          f"VH[3]={vh3}, VL[3]={vl3}, XM[1]={xm1}, XM[2]={xm2}"
        )

      # Encoder socket/type
      ca45 = await self.motor_send_command(axis, "CA", 45, val_type=ValType.Int)
      ca45v = _to_float(ca45, 0.0)
      if (not _is_number(ca45)) or not (0.0 < ca45v <= 4.0):
        raise CanError(f"Invalid encoder socket number received from axis {axis}. CA[45]={ca45}")

      enc_type = await self.motor_send_command(
        axis, "CA", int(round(40.0 + ca45v)), val_type=ValType.Int
      )
      if enc_type in (1, 2):
        self.absolute_encoder_ax[axis] = False
      elif enc_type == 24:
        self.absolute_encoder_ax[axis] = True
      else:
        raise CanError(
          f"Unsupported encoder type specified for axis {axis}. CA[4{ca45}]={enc_type}"
        )

      ca46 = await self.motor_send_command(axis, "CA", 46, val_type=ValType.Int)
      if ca45 == ca46:
        num3 = 1.0
      else:
        ff3 = await self.motor_send_command(axis, "FF", 3, val_type=ValType.Float)
        num3 = _to_float(ff3, 1.0)

      denom = self.motor_conversion_factor_ax[axis] * num3  # or 1.0

      sp2 = await self.motor_send_command(axis, "SP", 2, val_type=ValType.Int)
      if sp2 == 100000:
        vh2 = await self.motor_send_command(axis, "VH", 2, val_type=ValType.Int)
        self.max_vel_ax[axis] = _to_float(vh2) / 1.01 / denom
      else:
        self.max_vel_ax[axis] = _to_float(sp2) / denom

      sd0 = await self.motor_send_command(axis, "SD", 0, val_type=ValType.Int)
      self.max_accel_ax[axis] = _to_float(sd0) / 1.01 / denom

    # Robot-level params from shoulder_ax
    shoulder = KX2Axis.SHOULDER

    self.base_to_gripper_clearance_z = _to_float(
      await self.motor_send_command(shoulder, "UF", 6, val_type=ValType.Float)
    )
    self.base_to_gripper_clearance_arm = _to_float(
      await self.motor_send_command(shoulder, "UF", 7, val_type=ValType.Float)
    )
    self.wrist_offset = _to_float(
      await self.motor_send_command(shoulder, "UF", 8, val_type=ValType.Float)
    )
    self.elbow_offset = _to_float(
      await self.motor_send_command(shoulder, "UF", 9, val_type=ValType.Float)
    )
    self.elbow_zero_offset = _to_float(
      await self.motor_send_command(shoulder, "UF", 10, val_type=ValType.Float)
    )
    self.MaxLinearVelMMPerSec = _to_float(
      await self.motor_send_command(shoulder, "UF", 11, val_type=ValType.Float)
    )
    self.MaxLinearAccelMMPerSec2 = _to_float(
      await self.motor_send_command(shoulder, "UF", 12, val_type=ValType.Float)
    )
    self.MaxLinearJerkMMPerSec3 = _to_float(
      await self.motor_send_command(shoulder, "UF", 13, val_type=ValType.Float)
    )
    self.MaxRotaryVelDegPerSec = _to_float(
      await self.motor_send_command(shoulder, "UF", 14, val_type=ValType.Float)
    )
    self.MaxRotaryAccelDegPerSec2 = _to_float(
      await self.motor_send_command(shoulder, "UF", 15, val_type=ValType.Float)
    )

    ui17 = await self.motor_send_command(shoulder, "UI", 17, val_type=ValType.Int)
    self.pvt_time_interval_msec = (
      25
      if (not _is_number(ui17) or _to_float(ui17) <= 0.0 or _to_float(ui17) > 255.0)
      else int(_to_float(ui17))
    )

    # Servo gripper params (only if present)
    sg = KX2Axis.SERVO_GRIPPER
    self.servo_gripper_home_pos = int(
      await self.motor_send_command(sg, "UF", 6, val_type=ValType.Float)
    )
    self.servo_gripper_home_search_vel = int(
      await self.motor_send_command(sg, "UF", 7, val_type=ValType.Float)
    )
    self.servo_gripper_home_search_accel = int(
      await self.motor_send_command(sg, "UF", 8, val_type=ValType.Float)
    )
    self.servo_gripper_home_default_position_error = int(
      await self.motor_send_command(sg, "UF", 9, val_type=ValType.Float)
    )
    self.servo_gripper_home_hard_stop_position_error = int(
      await self.motor_send_command(sg, "UF", 10, val_type=ValType.Float)
    )
    self.servo_gripper_home_hard_stop_offset = int(
      await self.motor_send_command(sg, "UF", 11, val_type=ValType.Float)
    )
    self.servo_gripper_home_index_offset = int(
      await self.motor_send_command(sg, "UF", 12, val_type=ValType.Float)
    )
    self.servo_gripper_home_offset_vel = int(
      await self.motor_send_command(sg, "UF", 13, val_type=ValType.Float)
    )
    self.servo_gripper_home_offset_accel = int(
      await self.motor_send_command(sg, "UF", 14, val_type=ValType.Float)
    )
    self.servo_gripper_home_timeout_msec = int(
      await self.motor_send_command(sg, "UF", 15, val_type=ValType.Float)
    )
    self.servo_gripper_continuous_current = _to_float(
      await self.motor_send_command(sg, "UF", 16, val_type=ValType.Float)
    )
    self.servo_gripper_peak_current = _to_float(
      await self.motor_send_command(sg, "UF", 17, val_type=ValType.Float)
    )

  async def get_estop_state(self) -> bool:
    """Return True if in estop, False otherwise."""
    r = await self.motor_send_command(
      node_id=KX2Axis.SHOULDER,
      motor_command="SR",
      index=1,
      value="",
    )
    r = int(r)
    num2 = not (r & 0x4000 == 0x4000)
    num3 = not (r & 0x8000 == 0x8000)
    if r != 8438016:
      logger.warning("get_estop_state: SR register unexpected value %d (expected 8438016)", r)
    return num2 == False and num3 == False

  async def motor_send_command(
    self,
    node_id: int,
    motor_command: str,
    index: int,
    value: str = "",
    val_type: ValType = ValType.Int,
    *,
    low_priority: bool = False,
  ) -> str:
    if isinstance(node_id, KX2Axis):
      node_id = int(node_id)
    logger.debug(
      "motor_send_command node=%d cmd=%s[%d] value=%r val_type=%s",
      node_id, motor_command, index, value, val_type,
    )

    cmd_u = motor_command.upper()
    OS_CMDS = {"VR", "CD", "LS", "DL", "DF", "BH"}

    has_xc = "XC##" in cmd_u
    has_xq = "XQ##" in cmd_u
    use_os = (cmd_u in OS_CMDS) or has_xc or has_xq

    returned_data = ""

    cmd_u = motor_command.upper()

    NO_QUERY_CMDS = {
      "BG",
      "CP",
      "EI",
      "EO",
      "HP",
      "HX",
      "KL",
      "KR",
      "LD",
      "MI",
      "PB",
      "RS",
      "SV",
      "XC##",
    }

    if value == "":
      if (cmd_u in NO_QUERY_CMDS) or ("XQ##" in cmd_u):
        cmd_type = CmdType.Execute
      else:
        cmd_type = CmdType.ValQuery
    else:
      cmd_type = CmdType.ValSet

    if use_os:
      query_flag = not (has_xc or has_xq)

      # OSInterpreter writes into `str` and can also write a long; you can ignore the long if you don't use it.
      s = await self.driver.os_interpreter(
        node_id=(node_id),
        cmd=motor_command,
        query=query_flag,
      )

      if cmd_type == CmdType.ValQuery:
        returned_data = s if s is not None else ""
    else:
      s = await self.driver.binary_interpreter(
        node_id=(node_id),
        cmd=motor_command,
        cmd_index=int(index),
        cmd_type=cmd_type,
        value=value,
        val_type=val_type,
        low_priority=low_priority,
      )

      if cmd_type == CmdType.ValQuery:
        returned_data = s

    return returned_data

  def convert_elbow_position_to_angle(self, elbow_pos: float) -> float:
    max_travel = self.max_travel_ax[KX2Axis.ELBOW]
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
    elbow_pos = (self.max_travel_ax[KX2Axis.ELBOW] + self.elbow_zero_offset) * math.sin(
      elbow_angle_deg * (math.pi / 180.0)
    ) - self.elbow_zero_offset

    if elbow_angle_deg > 90.0:
      elbow_pos = 2.0 * self.max_travel_ax[KX2Axis.ELBOW] - elbow_pos

    return elbow_pos

  async def motor_get_current_position(self, axis: KX2Axis) -> float:
    raw = await self.driver.motor_get_current_position(node_id=axis, pu=self.unlimited_travel_ax[axis])
    c = self.motor_conversion_factor_ax[axis]
    if axis == KX2Axis.ELBOW:
      return self.convert_elbow_angle_to_position(elbow_angle_deg=raw / c)
    else:
      if c == 0:
        logger.warning("Axis %s has conversion factor of 0", axis)
        return 0
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
  def _profile(dist: float, v: float, a: float) -> tuple[float, float, float]:
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
    cmd_pos: Dict["KX2Axis", float],
    cmd_vel_pct: float,
    cmd_accel_pct: float,
  ) -> Optional[MotorsMovePlan]:
    target = cmd_pos.copy()
    axes = list(target.keys())

    enc_pos: Dict[KX2Axis, float] = {}
    enc_vel: Dict[KX2Axis, float] = {}
    enc_accel: Dict[KX2Axis, float] = {}
    # enc_move_dist: Dict[KX2Axis, float] = {}
    skip_ax: Dict[KX2Axis, bool] = {}

    # input validation / travel limits / done-wait logic
    if cmd_vel_pct <= 0.0 or cmd_vel_pct > 100.0:
      raise ValueError("CmdVel out of range")
    if cmd_accel_pct <= 0.0 or cmd_accel_pct > 100.0:
      raise ValueError("CmdAccel out of range")

    # Convert elbow cmd from position->angle for planning math
    if KX2Axis.ELBOW in axes:
      target[KX2Axis.ELBOW] = self.convert_elbow_position_to_angle(target[KX2Axis.ELBOW])

    # Ensure per-axis ready and clamp travel limits like
    # for ax in axes:
    #   if self.unlimited_travel_ax[ax]:
    #     continue
    #   high = self.max_travel_ax[ax]
    #   low = self.min_travel_ax[ax]
    #   print("ax", ax, "target", target[ax], "low", low, "high", high)
    #   if target[ax] > high:
    #     if (target[ax] - high) < 0.1:
    #       target[ax] = high
    #     else:
    #       raise ValueError(f"Axis {ax!r} above max travel")
    #   if target[ax] < low:
    #     if (low - target[ax]) < 0.1:
    #       target[ax] = low
    #     else:
    #       raise ValueError(f"Axis {ax!r} below min travel")

    # Clearance check
    if KX2Axis.Z in axes:
      if (
        target[KX2Axis.Z] < self.min_travel_ax[KX2Axis.Z] + self.base_to_gripper_clearance_z
        and target[KX2Axis.ELBOW] < self.base_to_gripper_clearance_arm
      ):
        raise ValueError("Base-to-gripper clearance violated")

    # Determine current/start positions
    curr = await self.request_joint_position()

    # Elbow: convert both target and start to angle for distance math
    if KX2Axis.ELBOW in curr:
      curr[KX2Axis.ELBOW] = self.convert_elbow_position_to_angle(curr[KX2Axis.ELBOW])

    # Handle unlimited travel normalization when direction != NORMAL
    for ax in axes:
      if (
        self.unlimited_travel_ax[ax]
        and self.g_joint_move_direction[ax] != JointMoveDirection.Normal
      ):
        target[ax] = self._wrap_to_range(target[ax], self.min_travel_ax[ax], self.max_travel_ax[ax])

    # Distances, skip flags, initial v/a per axis
    dist: Dict[KX2Axis, float] = {}
    v: Dict[KX2Axis, float] = {}
    a: Dict[KX2Axis, float] = {}
    accel_time: Dict[KX2Axis, float] = {}
    total_time: Dict[KX2Axis, float] = {}

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
    # keep target in angle-space for elbow during math, then later use conversion factor.
    for ax in axes:
      conv = self.motor_conversion_factor_ax[ax]
      enc_pos[ax] = target[ax] * conv
      # enc_move_dist[ax] = dist[ax] * conv

      if skip_ax[ax]:
        enc_vel[ax] = 1000.0
        enc_accel[ax] = 1000.0
      else:
        enc_vel[ax] = max(v[ax] * abs(conv), 1.0)
        enc_accel[ax] = max(a[ax] * abs(conv), 1.0)

    return MotorsMovePlan(
      moves=[
        MotorMoveParam(
          axis=ax,
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
    cmd_pos: Dict["KX2Axis", float],
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

    await self.driver._motors_move_absolute_execute(plan)

  def convert_cartesian_to_joint_position(self, pose: GripperPose) -> Dict["KX2Axis", float]:
    if pose.rotation.x != 0 or pose.rotation.y != 0:
      raise ValueError("Only Z rotation is supported for KX2")

    joint_position: Dict[KX2Axis, float] = {}

    x, y = (pose.location.x), (pose.location.y)

    # Shoulder axis
    shoulder = -math.degrees(math.atan2(x, y))
    if abs(shoulder + 180.0) < 1e-12:
      shoulder = 180.0

    joint_position[KX2Axis.SHOULDER] = shoulder

    # Z axis
    joint_position[KX2Axis.Z] = pose.location.z

    # Elbow axis
    elbow = (
      math.sqrt(x * x + y * y) - self.wrist_offset - self.elbow_offset - self.elbow_zero_offset
    )
    joint_position[KX2Axis.ELBOW] = elbow

    # Wrist axis
    wrist = (pose.rotation.z) - joint_position[KX2Axis.SHOULDER]
    joint_position[KX2Axis.WRIST] = wrist

    # Wrap wrist into travel range if possible by +/- 360
    w = joint_position[KX2Axis.WRIST]
    wmin = self.min_travel_ax[KX2Axis.WRIST]
    wmax = self.max_travel_ax[KX2Axis.WRIST]
    if (w < wmin - 0.001) and (w + 360.0 <= wmax):
      w += 360.0
    elif (w > wmax + 0.001) and (w - 360.0 >= wmin):
      w -= 360.0
    joint_position[KX2Axis.WRIST] = w

    return joint_position

  def convert_joint_position_to_cartesian(
    self, joint_position: Dict["KX2Axis", float]
  ) -> GripperPose:
    r = (
      self.wrist_offset + self.elbow_offset + self.elbow_zero_offset + joint_position[KX2Axis.ELBOW]
    )
    sh_deg = joint_position[KX2Axis.SHOULDER]
    sh = math.radians(sh_deg)

    location = Coordinate(
      x=(-(r) * math.sin(sh)),
      y=((r) * math.cos(sh)),
      z=(joint_position[KX2Axis.Z]),
    )

    rotation_z = joint_position[KX2Axis.WRIST] + sh_deg

    # wrap to [-180, 180]
    if rotation_z > 180.0:
      rotation_z -= 360.0
    if rotation_z < -180.0:
      rotation_z += 360.0

    return GripperPose(
      location=location,
      rotation=Rotation(z=rotation_z),
    )

  def convert_joint_position_to_tool_coordinate(
    self,
    joint_position: Dict[int, float],
    ref_frame_rotate: float,
    tool_offset: float,
  ) -> List[float]:
    coordinate = self.convert_joint_position_to_cartesian(joint_position)
    tool_coord = [0.0] * (len(joint_position) - 1)

    if tool_offset != 0.0:
      ang = math.radians(coordinate[3] + ref_frame_rotate)
      dx = -tool_offset * math.sin(ang)
      dy = tool_offset * math.cos(ang)
    else:
      dx = 0.0
      dy = 0.0

    tool_coord[0] = coordinate[0] + dx
    tool_coord[1] = coordinate[1] + dy
    tool_coord[2] = coordinate[2]
    tool_coord[3] = coordinate[3] + ref_frame_rotate

    if len(coordinate) > 4:
      tool_coord[4] = coordinate[4]

    return tool_coord

  def convert_tool_coordinate_to_joint_position(
    self,
    tool_coordinate: Sequence[float],
    ref_frame_rotate: float,
    tool_offset: float,
  ) -> Dict[int, float]:
    coordinate = [0.0] * (len(tool_coordinate) + 1)

    if tool_offset != 0.0:
      ang = math.radians(float(tool_coordinate[3]) - ref_frame_rotate)
      dx = -tool_offset * math.sin(ang)
      dy = tool_offset * math.cos(ang)
    else:
      dx = 0.0
      dy = 0.0

    coordinate[0] = float(tool_coordinate[0]) - dx
    coordinate[1] = float(tool_coordinate[1]) - dy
    coordinate[2] = float(tool_coordinate[2])
    coordinate[3] = float(tool_coordinate[3]) - ref_frame_rotate

    if len(tool_coordinate) >= 5:
      coordinate[4] = float(tool_coordinate[4])
    if len(tool_coordinate) >= 6:
      coordinate[5] = float(tool_coordinate[5])

    return self.convert_cartesian_to_joint_position(coordinate)

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
      {KX2Axis.SERVO_GRIPPER: gripper_width},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    if not isinstance(backend_params, KX2ArmBackend.GripParams):
      backend_params = KX2ArmBackend.GripParams()
    await self.motors_move_joint(
      {KX2Axis.SERVO_GRIPPER: gripper_width},
      cmd_vel_pct=100,
      cmd_accel_pct=100,
    )
    if backend_params.check_plate_gripped:
      await self.check_plate_gripped()

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    pos = await self.motor_get_current_position(KX2Axis.SERVO_GRIPPER)
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
    cmd_pos = {KX2Axis(int(k)): float(v) for k, v in position.items()}
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
      KX2Axis.SHOULDER: await self.motor_get_current_position(KX2Axis.SHOULDER),
      KX2Axis.Z: await self.motor_get_current_position(KX2Axis.Z),
      KX2Axis.ELBOW: await self.motor_get_current_position(KX2Axis.ELBOW),
      KX2Axis.WRIST: await self.motor_get_current_position(KX2Axis.WRIST),
      KX2Axis.SERVO_GRIPPER: await self.motor_get_current_position(KX2Axis.SERVO_GRIPPER),
    }

  async def start_freedrive_mode(
    self, free_axes: List[int], backend_params: Optional[BackendParams] = None
  ) -> None:
    # KX2 frees all motion axes at once; free_axes is accepted for API parity.
    del free_axes
    for axis in MOTION_AXES:
      await self.driver._motor_enable(axis=axis, state=False)

  async def stop_freedrive_mode(self, backend_params: Optional[BackendParams] = None) -> None:
    for axis in MOTION_AXES:
      await self.driver._motor_enable(axis=axis, state=True)
