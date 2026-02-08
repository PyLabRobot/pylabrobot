import asyncio
import math
import struct
import time
import warnings
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import can

from pylabrobot.arms.standard import GripperPose
from pylabrobot.resources import Coordinate, Rotation


class KX2Axis(IntEnum):
  SHOULDER = 1
  Z = 2
  ELBOW = 3
  WRIST = 4
  RAIL = 5
  SERVO_GRIPPER = 6


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


def _u32_le(value: int) -> List[int]:
  return list((value & 0xFFFFFFFF).to_bytes(4, byteorder="little", signed=False))


class JointMoveDirection(IntEnum):
  Normal = 0
  Clockwise = 1
  Counterclockwise = 2
  ShortestWay = 3


class HomeStatus(IntEnum):
  NotHomed = 0
  Homed = 1
  InitializedWithoutHoming = 2


class InputLogic(IntEnum):
  GeneralPurpose = 0
  EnableForwardOnly = 1
  EnableReverseOnly = 2


class EventType(IntEnum):
  MotorPositionChanged = 1
  MotionStatusReceived = 2
  MotorEnabledStatusReceived = 3
  DigitalInputChangedState = 4
  CANEmergencyMessageReceived = 5
  MoveError = 6
  MotorMoveDone = 7
  MotorsMoveDone = 8
  MotorsMovePathDone = 9


class MoveType(IntEnum):
  MotorMove = 0
  MotorsMoveAbsolute = 1
  MotorsMovePath = 2


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


class ElmoObject(IntEnum):
  DeviceType = 0x1000
  ErrorRegister = 0x1001
  ManfStatusRegister = 0x1002
  ErrorField = 0x1003
  CommCyclePeriod = 0x1006
  ManfDeviceName = 0x1008
  ManfHWVersion = 0x1009
  ManfSWVersion = 0x100A
  NodeID = 0x100B
  StoreParameters = 0x1010
  RestoreParameters = 0x1011
  CustomerHeartbeatTime = 0x1016
  ProducerHeartbeatTime = 0x1017
  IdentityObject = 0x1018
  OSCommand = 0x1023
  OSCommandMode = 0x1024
  ErrorBehavior = 0x1029
  RPDO1CommParam = 0x1400
  RPDO2CommParam = 0x1401
  RPDO3CommParam = 0x1402
  RPDO4CommParam = 0x1403
  RPDO1Mapping = 0x1600
  RPDO2Mapping = 0x1601
  RPDO3Mapping = 0x1602
  RPDO4Mapping = 0x1603
  TPDO1CommParam = 0x1800
  TPDO2CommParam = 0x1801
  TPDO3CommParam = 0x1802
  TPDO4CommParam = 0x1803
  TPDO1Mapping = 0x1A00
  TPDO2Mapping = 0x1A01
  TPDO3Mapping = 0x1A02
  TPDO4Mapping = 0x1A03
  FastReference = 0x2005
  BinaryInterpreterInput = 0x2012
  BinaryInterpreterOutput = 0x2013
  FilteredRMSCurrent = 0x201B
  HomeOnBlockLimitParams = 0x2020
  RecorderData = 0x2030
  TimeStamp = 0x2040
  DriveParamsChecksum = 0x2060
  AdditionalPositionRangeLimit = 0x207B
  ExtendedErrorCode = 0x2081
  CANControllerStatus = 0x2082
  SerialEncoderStatus = 0x2084
  ExtraStatusRegister = 0x2085
  STOStatusRegister = 0x2086
  PALVersion = 0x2087
  AuxPositionActualValue = 0x20A0
  SocketAdditionalFunction = 0x20B0
  AbsoluteSensorFunctions = 0x20FC
  DigitalInputs = 0x20FD
  DigitalInputLowByte = 0x2201
  ExtendedInputs = 0x2202
  Application = 0x2203
  AnalogInputs = 0x2205
  Supply5VDC = 0x2206
  DigitalOutputs = 0x22A0
  ExtendedOutputs = 0x22A1
  DriveTemperature = 0x22A2
  DriveTemperature2 = 0x22A3
  MotorTemperature = 0x22A4
  GainSchedulingIndex = 0x2E00
  TorqueWindow = 0x2E06
  TorqueWindowTime = 0x2E07
  HomeOnTouchProbe = 0x2E10
  GantryYawOffset = 0x2E15
  UserInteger = 0x2F00
  UserFloat = 0x2F01
  GetCtrlBoardType = 0x2F05
  TPDOAsyncEvents = 0x2F20
  EmergencyEvents = 0x2F21
  DS402Config = 0x2F41
  ThresholdParam = 0x2F45
  CANEncoderRange = 0x2F70
  ExtrapolationCyclesTimeout = 0x2F75
  ElmoParamBG = 0x3020
  ElmoParamCA = 0x3034
  ElmoParamCL = 0x303F
  ElmoParamDC = 0x3050
  ElmoParamEC = 0x306A
  ElmoParamER = 0x3079
  ElmoParamHL = 0x30C1
  ElmoParamHP = 0x30C5
  ElmoParamHT = 0x30C9
  ElmoParamIB = 0x30D1
  ElmoParamID = 0x30D3
  ElmoParamIF = 0x30D5
  ElmoParamIL = 0x30DB
  ElmoParamIP = 0x30DF
  ElmoParamIQ = 0x30E0
  ElmoParamJV = 0x30FF
  ElmoParamKI = 0x310C
  ElmoParamKL = 0x310F
  ElmoParamKP = 0x3113
  ElmoParamKV = 0x3119
  ElmoParamLL = 0x3129
  ElmoParamMC = 0x313A
  ElmoParamMO = 0x3146
  ElmoParamMS = 0x314A
  ElmoParamOB = 0x316D
  ElmoParamOL = 0x3177
  ElmoParamOP = 0x317B
  ElmoParamPA = 0x3186
  ElmoParamPE = 0x318A
  ElmoParamPL = 0x3191
  ElmoParamPP = 0x3195
  ElmoParamPR = 0x3197
  ElmoParamPS = 0x3198
  ElmoParamPX = 0x319D
  ElmoParamSD = 0x31D7
  ElmoParamSF = 0x31D9
  ElmoParamSR = 0x31E5
  ElmoParamSV = 0x31E9
  ElmoParamSW = 0x31EA
  ElmoParamTC = 0x31F0
  ElmoParamTM = 0x31FA
  ElmoParamUC = 0x320D
  ElmoParamUI = 0x3210
  ElmoParamVE = 0x3226
  ElmoParamVH = 0x3229
  ElmoParamVL = 0x322D


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


@dataclass
class EventData:
  event_type: EventType = EventType.MotorPositionChanged
  pending: bool = False
  node_id: int = 0
  cmr_msg_type: str = ""
  cmr_data_type: str = ""
  cmr_data: str = ""
  mpc_position: Optional[List[int]] = None  # Array
  msr_status: bool = False
  msr_status_word: int = 0
  mesr_status: int = 0
  dics_state: Optional[List[bool]] = None  # Array
  cemr_emcy_msg: Any = None  # sEmcy
  cemr_description: str = ""
  cemr_disable_motors: bool = False
  me_error_code: int = 0
  me_index: int = 0
  mmd_all_moves_done: bool = False


@dataclass
class ErrCtrl:
  data_byte: Optional[List[int]] = None  # Array


@dataclass
class PVT_EMCY_QueueLow:
  state: bool = False
  write_pointer: int = 0
  read_pointer: int = 0


@dataclass
class PVT_EMCY_QueueFull:
  state: bool = False
  failed_write_pointer: int = 0


@dataclass
class PVT_EMCY:
  queue_low: PVT_EMCY_QueueLow = field(default_factory=PVT_EMCY_QueueLow)
  queue_full: PVT_EMCY_QueueFull = field(default_factory=PVT_EMCY_QueueFull)
  bad_head_pointer: bool = False
  bad_mode_init_data: bool = False
  motion_terminated: bool = False
  out_of_modulo: bool = False


@dataclass
class Emcy:
  err_code: int = 0
  err_reg: int = 0
  elmo_err_code: int = 0
  err_code_data1: int = 0
  err_code_data2: int = 0


@dataclass
class Query:
  node_id: int = 0
  object_byte0: int = 0
  object_byte1: int = 0
  sub_index: int = 0
  msg_type: str = ""  # "SDODI" "CHR" "STAT" "SDOUA" "SDOSU"
  msg_index: int = 0


@dataclass
class CAN_Msg:
  cob: COBType = COBType.NMT
  node_id: int = 0
  byte0: int = 0
  byte1: int = 0
  byte2: int = 0
  byte3: int = 0
  byte4: int = 0
  byte5: int = 0
  byte6: int = 0
  byte7: int = 0
  execute: bool = False
  data_length: int = 8
  error_msg: str = ""
  pending: bool = False
  time_stamp: int = 0
  fut: Optional[asyncio.Future] = None


@dataclass
class NodeInputConfig:
  logic: InputLogic = InputLogic.GeneralPurpose
  logic_high: bool = True


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


class KX2Can:
  def __init__(self, has_rail: bool = False, has_servo_gripper: bool = True) -> None:
    self.connecting: bool = False
    self.grp_id: int = 0

    # Error control
    self.err_ctrl: List[ErrCtrl] = [ErrCtrl() for _ in range(8)]

    self.move_error_code: int = 0

    self.pvt_time_interval_msec: int = 0
    self.pvt_stop: bool = False

    self.pvt_emcy: List[PVT_EMCY] = [PVT_EMCY() for _ in range(128)]
    self._pvt_mode: bool = False

    self._can_device: Optional[can.BusABC] = None

    # Threading flags (for asyncio tasks)
    self._can_write_task_running = False
    self._can_read_task_running = True

    # Store task references to prevent garbage collection
    self._read_task: Optional[asyncio.Task] = None
    self._write_task: Optional[asyncio.Task] = None
    self.b_pvt_thread_started = False

    # Can message queues
    self._can_msg_queue_hp: asyncio.Queue[CAN_Msg] = asyncio.Queue()
    self._can_msg_queue_lp: asyncio.Queue[CAN_Msg] = asyncio.Queue()

    self._waiting_moves: Dict[KX2Axis, asyncio.Future] = {}

    self.node_id_list = [1, 2, 3, 4]
    if has_rail:
      self.node_id_list.append(5)
    if has_servo_gripper:
      self.node_id_list.append(6)

    self.input_state: Dict[int, int] = {i: 0 for i in range(len(self.node_id_list))}

    # initialize based on maximum possible node_id (assume 127 + 1 + 1, so 129), 5, 1
    self.tpdo_mapped_object: List[List[List[TPDOMappedObject]]] = [
      [[TPDOMappedObject.NotMapped for _ in range(1)] for _ in range(5)] for _ in range(129)
    ]
    self.node_input_config: List[List[NodeInputConfig]] = [
      [NodeInputConfig() for _ in range(7)] for _ in range(129)
    ]

    # Wait buffers
    self._os_query_wait_buffer: List[Tuple[Query, asyncio.Future]] = []
    self._os_query_wait_buffer_lock = asyncio.Lock()

    self._query_wait_buffer: List[Tuple[Query, asyncio.Future]] = []
    self._query_wait_buffer_lock = asyncio.Lock()

  @property
  def can_device(self) -> can.BusABC:
    if self._can_device is None:
      raise CanError("CAN device is not initialized.")
    return self._can_device

  async def _can_write_task(self):
    self._can_write_task_running = True

    while self._can_write_task_running:
      message: Optional[CAN_Msg] = None
      try:
        message = self._can_msg_queue_hp.get_nowait()
      except asyncio.QueueEmpty:
        try:
          message = self._can_msg_queue_lp.get_nowait()
        except asyncio.QueueEmpty:  # No messages to send
          await asyncio.sleep(0.001)
          continue

      if message.fut is not None and message.fut.done():
        continue  # Skip if future is already done

      msg_id = (message.cob.value << 7) + message.node_id
      data_bytes = [
        message.byte0,
        message.byte1,
        message.byte2,
        message.byte3,
        message.byte4,
        message.byte5,
        message.byte6,
        message.byte7,
      ][: message.data_length]

      try:
        can_msg = can.Message(
          arbitration_id=msg_id,
          data=data_bytes,
          is_extended_id=False,  # Assuming standard IDs for CANopen
        )

        await asyncio.to_thread(self.can_device.send, can_msg)
        if message.fut and not message.fut.done():
          message.fut.set_result(None)
      except Exception as e:
        error_msg = f"CAN Write Error: {e}"
        print(f"CAN Write Error: {e}")
        if message.fut and not message.fut.done():
          message.fut.set_exception(CanError(error_msg))

  async def _process_emcy_message(self, node_id: int, message: can.Message) -> None:
    print("EMCY received!!")

    data = message.data
    if not data or len(data) < 8:
      print(f"EMCY malformed: expected 8 bytes, got {0 if not data else len(data)}")
      return

    def u16_le(i: int) -> int:
      return int(data[i]) | (int(data[i + 1]) << 8)

    emcy = Emcy()
    emcy.err_code = u16_le(0)
    emcy.err_reg = int(data[2])
    emcy.elmo_err_code = int(data[3])
    emcy.err_code_data1 = u16_le(4)
    emcy.err_code_data2 = u16_le(6)

    self.last_emcy = emcy

    desc = ""
    disable_motors = False
    suppress_event = False

    err = emcy.err_code
    elmo = emcy.elmo_err_code

    # Simple (err_code -> (description, disable_motors))
    ERR_MAP: dict[int, tuple[str, bool]] = {
      0x8110: ("CAN message lost (corrupted or overrun)", False),
      0x8200: ("Protocol error (unrecognized NMT request)", False),
      0x8210: ("Attempt to access an unconfigured RPDO", False),
      0x8130: ("Heartbeat event", False),
      0x6180: ("Fatal CPU error: stack overflow", False),
      0x6181: ("CPU exception: fatal exception", False),
      0x6200: ("User program aborted by an error", False),
      0xFF01: ("Request by user program 'emit' function", False),
      0x6300: (
        "Object mapped to an RPDO returned an error during interpretation or a referenced motion failed to be performed",
        False,
      ),
      0x7300: ("Resolver or Analog Encoder feedback failed", True),
      0x7380: ("Feedback loss: no match between encoder and Hall locations.", True),
      0x8311: (
        "Peak current has been exceeded due to drive malfunction or badly-tuned current controller",
        True,
      ),
      0x5441: ("E-stop button was pressed", True),
      0x5280: ("ECAM table problem", False),
      0x7381: (
        "Two digital Hall sensors changed at once; only one sensor can be changed at a time",
        True,
      ),
      0x8480: ("Speed tracking error", True),
      0x8611: ("Position tracking error", True),
      0x6320: ("Cannot start due to inconsistent database", False),
      0x8380: ("Cannot find electrical zero of motor when attempting to start motor", False),
      0x8481: ("Speed limit exceeded", True),
      0x5281: ("Timing error", False),
      0x7121: ("Motor stuck: motor powered but not moving", True),
      0x8680: ("Position limit exceeded", True),
      0x8381: ("Cannot tune current offsets", False),
      0xFF10: ("Cannot start motor", False),
      0x5400: ("Cannot start motor", False),
      0x3120: (
        "Under-voltage: power supply is shut down or it has too high an output impedance",
        True,
      ),
      0x3310: (
        "Over-voltage: power supply voltage is too high or servo driver could not absorb kinetic energy while braking a load",
        True,
      ),
      0x2340: ("Short circuit: motor or its wiring may be defective, or drive is faulty", True),
      0x4310: ("Temperature: drive overheating", True),
      0xFF20: ("Safety switch is sensed - Drive in safety state", True),
    }

    if err == 0xFF00:
      if elmo == 0x56:
        st = self.pvt_emcy[node_id]
        st.queue_low.state = True
        st.queue_low.write_pointer = emcy.err_code_data1
        st.queue_low.read_pointer = emcy.err_code_data2
        desc = "Queue Low"
      elif elmo == 0x5B:
        self.pvt_emcy[node_id].bad_head_pointer = True
        desc = "Bad Head Pointer"
      elif elmo == 0x34:
        st = self.pvt_emcy[node_id]
        st.queue_full.state = True
        st.queue_full.failed_write_pointer = emcy.err_code_data1
        desc = "Queue Full"
      elif elmo == 0x07:
        self.pvt_emcy[node_id].bad_mode_init_data = True
        desc = "Bad Mode Init Data"
      elif elmo == 0x08:
        self.pvt_emcy[node_id].motion_terminated = True
        desc = "Motion Terminated"
      elif elmo == 0xA6:
        self.pvt_emcy[node_id].out_of_modulo = True
        desc = "Out Of Modulo"
      else:
        desc = f"DS402 IP Error {elmo}"

    elif err == 0xFF02:
      if elmo == 0x07:
        self.pvt_emcy[node_id].bad_mode_init_data = True
        desc = "Bad Mode Init Data"
      elif elmo == 0x08:
        self.pvt_emcy[node_id].motion_terminated = True
        desc = "Motion Terminated"
      elif elmo == 0x34:
        st = self.pvt_emcy[node_id]
        st.queue_full.state = True
        st.queue_full.failed_write_pointer = emcy.err_code_data1
        desc = "Queue Full"
      elif elmo == 0x56:
        st = self.pvt_emcy[node_id]
        st.queue_low.state = True
        st.queue_low.write_pointer = emcy.err_code_data1
        st.queue_low.read_pointer = emcy.err_code_data2
        desc = "Queue Low"
      elif elmo == 0x5B:
        self.pvt_emcy[node_id].bad_head_pointer = True
        desc = "Bad Head Pointer"
      elif elmo == 0x8A:
        self.pvt_emcy[node_id].queue_low.state = True
        desc = "Position Interpolation buffer underflow"
        suppress_event = True
      elif elmo == 0xA6:
        self.pvt_emcy[node_id].out_of_modulo = True
        desc = "Out Of Modulo"
      elif elmo == 0xBA:
        self.pvt_emcy[node_id].queue_full.state = True
        desc = "Interpolation queue is full"
      elif elmo == 0xBB:
        desc = "Incorrect interpolation sub-mode"
      else:
        desc = f"DS402 IP Error {elmo}"

    else:
      mapped = ERR_MAP.get(err)
      if mapped:
        desc, disable_motors = mapped
      else:
        desc = f"Unknown EMCY err={err} elmo={elmo}"

    if disable_motors:
      for fut in self._waiting_moves.values():
        if not fut.done():
          fut.set_exception(CanError(f"Motor move error on node {node_id}: {desc}"))

    if not suppress_event:
      event = EventData()
      event.event_type = EventType.CANEmergencyMessageReceived
      event.node_id = node_id
      event.cemr_description = desc
      event.cemr_emcy_msg = emcy
      event.cemr_disable_motors = disable_motors
      print(event)

    print(emcy)
    print(
      f"EMCY node={node_id} desc='{desc}' disable_motors={disable_motors} suppress_event={suppress_event}"
    )

  async def _process_tpdo_message(self, node_id: int, message: can.Message, response_type: int):
    tpdo_index = {0: TPDO.TPDO1, 4: TPDO.TPDO3, 6: TPDO.TPDO4}[response_type - 3]

    if self.node_id_list is None or node_id not in self.node_id_list:
      return

    if self.tpdo_mapped_object is None:
      return

    num2 = 0

    node_idx = self.node_id_list.index(node_id)

    for i in range(len(self.tpdo_mapped_object[node_id][tpdo_index])):
      mapped = self.tpdo_mapped_object[node_id][tpdo_index][i]

      # read 16-bit or 32-bit value depending on flags
      num4_: Optional[int] = None
      if mapped.value & 0x10 == 0x10:  # 16 bit
        num4_ = (message.data[num2 + 1] << 8) | message.data[num2]
        num2 += 2
      if mapped.value & 0x20 == 0x20:  # 32 bit
        num4_ = (
          (message.data[num2 + 3] << 24)
          | (message.data[num2 + 2] << 16)
          | (message.data[num2 + 1] << 8)
          | message.data[num2]
        )
        num2 += 4
      if num4_ is None:
        # print("Failed to read TPDO mapped object value, probably fine....")
        # raise CanError("Failed to read TPDO mapped object value.")
        continue
      num4 = num4_

      if mapped == TPDOMappedObject.StatusWord:
        if KX2Axis(node_id) in self._waiting_moves:
          self._waiting_moves[KX2Axis(node_id)].set_result(None)

        event_data = EventData(
          event_type=EventType.MotionStatusReceived,
          node_id=node_id,
          msr_status=True,
          msr_status_word=int(num4 & 0xFFFF),  # short
        )
        await self._raise_an_event(event_data)
      elif mapped == TPDOMappedObject.PositionActualValue:
        pass  # we don't need to mirror the encoder position on the client side
      elif mapped == TPDOMappedObject.DigitalInputs:
        # temp array for digital inputs (indices 16..21 used)
        num_array = [0] * 22  # 0..21

        # clear bits 16..21??
        for i in range(16, 21 + 1):
          num_array[i] = 0

        # set bits from num4
        if num4 & (1 << 16):
          num_array[16] = 1
        if num4 & (1 << 17):
          num_array[17] = 1
        if num4 & (1 << 18):
          num_array[18] = 1
        if num4 & (1 << 19):
          num_array[19] = 1
        if num4 & (1 << 20):
          num_array[20] = 1
        if num4 & (1 << 21):
          num_array[21] = 1

        # num6: packed 6-bit value
        num6 = (
          num_array[16]
          + num_array[17] * 2
          + num_array[18] * 4
          + num_array[19] * 8
          + num_array[20] * 16
          + num_array[21] * 32
        )

        if self.input_state[node_idx] != num6:
          # check for transitions that imply move done
          for index4 in range(1, 7):  # 1..6
            # previous bit for this input (0/1)
            prev_bit = 1 if (self.input_state[node_idx] & (1 << (index4 - 1))) else 0
            new_bit = num_array[15 + index4]

            cfg = self.node_input_config[node_idx][index4]
            logic = cfg.logic

            edge = prev_bit != new_bit
            logic_enabled = logic in (
              InputLogic.EnableForwardOnly,
              InputLogic.EnableReverseOnly,
            )

            if edge and logic_enabled and new_bit == 1:
              self._waiting_moves[KX2Axis(node_id)].set_result(None)
              print(f"Digital input {index4} enabled motor move done for node {node_id}")

          self.input_state[node_idx] = num6

          # index 0 unused, 1..6 valid
          dics_state = [False] * 7
          dics_state[1] = num_array[16] > 0
          dics_state[2] = num_array[17] > 0
          dics_state[3] = num_array[18] > 0
          dics_state[4] = num_array[19] > 0
          dics_state[5] = num_array[20] > 0
          dics_state[6] = num_array[21] > 0

          # raise digital input change event
          event_data = EventData(
            event_type=EventType.DigitalInputChangedState,
            node_id=node_id,
            dics_state=dics_state,
          )

          await self._raise_an_event(event_data)

  async def _process_binary_interpreter_response(self, node_id: int, message: can.Message) -> None:
    data = message.data
    if not data or len(data) < 8:
      raise CanError("Invalid binary interpreter response data.")

    msg_type = chr(data[0]) + chr(data[1])

    # Build index: lower 8 bits from DATA[2], upper 6 bits from DATA[3]
    msg_index = ((data[3] & 0b0011_1111) << 8) | data[2]

    is_int = (data[3] & 0x80) == 0

    if is_int:
      raw = bytes(data[4:8])
      value_int = struct.unpack("<i", raw)[0]
      value_str = str(value_int)
    else:
      raw = bytes(data[4:8])
      (value_float,) = struct.unpack("<f", raw)
      value_str = str(value_float)

    async with self._query_wait_buffer_lock:
      for query, fut in self._get_queries(node_id):
        if query.msg_index == msg_index and query.msg_type == msg_type:
          fut.set_result(value_str)

      # Delete all completed futures for this node_id
      self._query_wait_buffer = [
        (query, fut)
        for query, fut in self._query_wait_buffer
        if query.node_id != node_id or not fut.done()
      ]

  async def _process_sdo_response(self, node_id: int, message: can.Message) -> None:
    data = message.data
    if not data:
      return

    cmd = data[0]
    length = len(data)

    async with self._os_query_wait_buffer_lock:
      # ---- 1) SDO abort (0x80) ----
      if cmd == 0x80 and length >= 8:
        abort_code = data[4] | (data[5] << 8) | (data[6] << 16) | (data[7] << 24)

        msg = self.get_sdo_abort_message(abort_code)

        for query, fut in self._get_os_queries(node_id):
          if (
            query.object_byte0 == data[2]
            and query.object_byte1 == data[1]
            and query.sub_index == data[3]
          ):
            fut.set_exception(CanError(msg))

      # ---- 2) SDO download initiate response (0x60) -> "SDODI" ----
      elif cmd == 0x60 and length >= 4:
        for query, fut in self._get_os_queries(node_id):
          if (
            query.object_byte0 == data[2]
            and query.object_byte1 == data[1]
            and query.sub_index == data[3]
            and query.msg_type == "SDODI"
          ):
            fut.set_result("SDODI")

      # ---- 3) "CHR" response: 0x20/0x30 and index bytes zero ----
      elif cmd in (0x20, 0x30) and length >= 4 and data[1] == 0 and data[2] == 0 and data[3] == 0:
        for query, fut in self._get_os_queries(node_id):
          if query.msg_type == "CHR":
            fut.set_result("CHR")

      # ---- 4) "STAT" response ----
      elif length >= 5 and cmd == 0x4F and data[1] == 0x23 and data[2] == 0x10 and data[3] == 2:
        stat_value = data[4]
        for query, fut in self._get_os_queries(node_id):
          if query.msg_type == "STAT":
            fut.set_result(f"STAT{stat_value}")

      # ---- 5) "VAL" response ----
      elif length >= 4 and cmd == 0x43 and data[1] == 0x23 and data[2] == 0x10 and data[3] == 3:
        if length > 4:
          tail = ",".join(str(b) for b in data[4:length])
          val_response = f"VAL,{tail}"
        else:
          val_response = "VAL"

        for query, fut in self._get_os_queries(node_id):
          if query.msg_type == "VAL":
            fut.set_result(val_response)

      # ---- 6) SDO upload (expedited) "SDOUA" ----
      elif (cmd & 0x40) == 0x40 and length >= 4:
        # Decode bits according to CANopen-style layout:
        # n = number of unused bytes in 4-byte data field
        # e = (usually) expedited bit
        n = (cmd >> 2) & 0x03
        e = (cmd >> 1) & 0x01
        # s = cmd & 0x01  # not needed here

        used = max(0, min(4 - n, length - 4))
        raw_bytes = data[4 : 4 + used]

        for query, fut in self._get_os_queries(node_id):
          if (
            query.msg_type == "SDOUA"
            and query.object_byte0 == data[2]
            and query.object_byte1 == data[1]
            and query.sub_index == data[3]
          ):
            if e == 0:
              # Numeric value: sum(data[i] * 256^(i-1))
              value = 0
              for i, b in enumerate(raw_bytes):
                value |= int(b) << (8 * i)
              fut.set_result(f"SDOUAN{value}")
            else:
              # String value: concatenate chars from data bytes
              text = "".join(chr(b) for b in raw_bytes)
              fut.set_result(f"SDOUAE{text}")

      # ---- 7) SDO segmented upload "SDOSU" ----
      elif (cmd & 0x20) == 0 and (cmd & 0x40) == 0 and (cmd & 0x80) == 0 and length >= 1:
        # Again, follow CANopen-ish semantics:
        # toggle bit, n = number of unused bytes (among 7), last-segment bit.
        toggle = (cmd >> 4) & 0x01
        n = (cmd >> 1) & 0x07
        last = cmd & 0x01

        used = max(0, min(7 - n, length - 1))
        payload = data[1 : 1 + used]
        payload_str = "".join(chr(b) for b in payload)

        for query, fut in self._get_os_queries(node_id):
          if query.msg_type == "SDOSU":
            prefix = "SDOSUC" if last == 0 else "SDOSUD"
            fut.set_result(f"{prefix}{toggle}{payload_str}")

      # ---- Cleanup: drop finished futures from buffer ----
      self._os_query_wait_buffer = [
        (query, fut)
        for query, fut in self._os_query_wait_buffer
        if query.node_id != node_id or not fut.done()
      ]

  async def _process_motor_drive_restarted(self, node_id: int, message: can.Message):
    self.err_ctrl[node_id].data_byte = list(message.data)
    if not self.connecting:
      event = EventData(
        event_type=EventType.CANEmergencyMessageReceived,
        node_id=node_id,
        cemr_description="Node Guarding error. Motor drive restarted spontaneously.",
        cemr_disable_motors=True,
      )
      await self._raise_an_event(event)

      await self.can_write(cob=COBType.NMT, node_id=0, byte0=0x01, byte1=node_id)
      await self.can_tpdo_unmap(tpdo=TPDO.TPDO1, node_id=node_id)
      await self.can_tpdo3_map(node_id=node_id)
      await self.can_tpdo4_map(node_id=node_id)

  async def _can_read_task(
    self,
  ):
    while self._can_read_task_running:
      try:
        message = await asyncio.to_thread(self.can_device.recv, timeout=1.0)
      except Exception as e:
        print(f"CAN Read Error: {e}")
        raise CanError(f"CAN Read Error: {e}")

      if message is None:  # timeout
        continue

      response_type = message.arbitration_id >> 7
      response_type_c = round(message.arbitration_id / 128)
      assert (
        response_type == response_type_c
      ), f"Response type calculation mismatch: {response_type} != {response_type_c}"
      node_id = message.arbitration_id & 0x7F
      node_id_c = message.arbitration_id - (response_type * 128)
      assert node_id == node_id_c, f"Node index calculation mismatch: {node_id} != {node_id_c}"

      if response_type == 0:
        print("NMT message received, ignoring")
      elif response_type == 1:
        await self._process_emcy_message(node_id=node_id, message=message)
      elif response_type in {3, 7, 9}:
        await self._process_tpdo_message(
          node_id=node_id, message=message, response_type=response_type
        )
      elif response_type == 5:
        await self._process_binary_interpreter_response(node_id=node_id, message=message)
      elif response_type == 11:
        await self._process_sdo_response(node_id=node_id, message=message)
      elif response_type == 14:
        await self._process_motor_drive_restarted(node_id=node_id, message=message)
      else:
        print(f"Unknown CAN message type received: {response_type}")

  async def can_write(
    self,
    cob: COBType,
    node_id: int,
    byte0: int = 0,
    byte1: int = 0,
    byte2: int = 0,
    byte3: int = 0,
    byte4: int = 0,
    byte5: int = 0,
    byte6: int = 0,
    byte7: int = 0,
    execute: bool = False,
    data_length: int = 8,
    low_priority: bool = False,
    time_stamp: int = 0,
  ) -> None:
    """Queues a CAN message for transmission."""

    fut = asyncio.get_event_loop().create_future()

    msg_entry = CAN_Msg(
      cob=cob,
      node_id=node_id,
      byte0=byte0,
      byte1=byte1,
      byte2=byte2,
      byte3=byte3,
      byte4=byte4,
      byte5=byte5,
      byte6=byte6,
      byte7=byte7,
      execute=execute,
      data_length=data_length,
      error_msg="",
      pending=True,  # Still use pending for internal state if needed elsewhere, but fut is primary
      time_stamp=time_stamp,
      fut=fut,
    )

    if low_priority:
      await self._can_msg_queue_lp.put(msg_entry)
    else:
      await self._can_msg_queue_hp.put(msg_entry)

    timeout_sec = 5.0  # 5000ms
    try:
      await asyncio.wait_for(fut, timeout=timeout_sec)
    except asyncio.TimeoutError:
      raise CanError(
        f"Failed to send CAN message {cob.name},{node_id},... Low Priority = {low_priority} (timeout)"
      )
    except Exception as e:
      raise CanError(str(e))

  async def connect(
    self,
    baud_rate: int = 500000,
  ) -> None:
    # Clean up previous connection if re-connecting
    if self._read_task is not None and not self._read_task.done():
      self._can_read_task_running = False
      self._read_task.cancel()
      try:
        await self._read_task
      except (asyncio.CancelledError, Exception):
        pass
    if self._write_task is not None and not self._write_task.done():
      self._can_write_task_running = False
      self._write_task.cancel()
      try:
        await self._write_task
      except (asyncio.CancelledError, Exception):
        pass
    if self._can_device is not None:
      self._can_device.shutdown()
      self._can_device = None

    self.sending_sv_command = False
    self.move_error_code = 0
    self.pvt_time_interval_msec = 0
    self.err_ctrl = [ErrCtrl() for _ in range(8)]

    # Determine max_node_id for array sizing if dynamic behavior is needed
    max_node_id = 6

    # Resize buffers based on max_node_id
    buffer_size = max_node_id + 1 if max_node_id > 0 else 8

    self.node_input_config = [[NodeInputConfig() for _ in range(7)] for _ in range(buffer_size + 1)]
    for i in range(buffer_size + 1):
      for j in range(1, 7):
        self.node_input_config[i][j].logic = InputLogic.GeneralPurpose
        self.node_input_config[i][j].logic_high = True

    # Initialize python-can bus
    self._can_device = can.Bus(
      interface="pcan",  # Or 'usbcan', 'kvaser', etc. based on setup
      channel=None,  # e.g., 'PCAN_USBBUS1' or int 0 for default
      bitrate=baud_rate,
      is_extended_id=False,
    )

    # Start asyncio tasks (store references to prevent GC)
    self._can_read_task_running = True
    self._can_write_task_running = False
    self._read_task = asyncio.create_task(self._can_read_task())
    self._write_task = asyncio.create_task(self._can_write_task())

    await asyncio.sleep(0.01)

    # --- CANopen Initialization Sequence ---
    self.connecting = True
    # NMT Reset Nodes (0x80)
    await self.can_write(cob=COBType.NMT, node_id=0, byte0=0x82)

    await asyncio.sleep(0.5)

    # NMT Start Nodes (0x01)
    await self.can_write(cob=COBType.NMT, node_id=0, byte0=0x01)
    await asyncio.sleep(0.1)
    self.connecting = False

    discovered_nodes = [
      i for i in range(len(self.err_ctrl)) if self.err_ctrl[i].data_byte is not None
    ]
    if discovered_nodes != self.node_id_list:
      raise CanError(
        f"Node IDs on CAN bus do not match expected list: {discovered_nodes} != {self.node_id_list}"
      )

  async def connect_part_two(self):
    """After setting up the threads and can connection, and receiving node IDs, map the things. this is flag=True"""

    max_node_id = max(self.node_id_list)

    self.tpdo_mapped_object = [
      [[TPDOMappedObject.NotMapped for _ in range(1)] for _ in range(5)]
      for _ in range(max_node_id + 2)
    ]

    for node_id in self.node_id_list:
      await self.can_tpdo_unmap(TPDO.TPDO1, node_id)

      await self.can_tpdo3_map(node_id)
      await self.can_tpdo4_map(node_id)

    # Configure Elmo objects if group_node_id is set
    # This section involves can_sdo_download_ElmoObject which needs to be translated first.
    for axis in KX2Backend.MOTION_AXES:
      await self.can_sdo_download_elmo_object(
        node_id=int(axis),
        elmo_object_int=24768,
        sub_index=0,
        data="-1",
        data_type=ElmoObjectDataType.INTEGER16,
      )

      await self.can_sdo_download_elmo_object(
        node_id=int(axis),
        elmo_object_int=24772,
        sub_index=2,
        data="16",
        data_type=ElmoObjectDataType.UNSIGNED32,
      )

      await self.can_sdo_download_elmo_object(
        node_id=int(axis),
        elmo_object_int=24772,
        sub_index=3,
        data="0",
        data_type=ElmoObjectDataType.UNSIGNED8,
      )

      await self.can_sdo_download_elmo_object(
        node_id=int(axis),
        elmo_object_int=24772,
        sub_index=5,
        data="8",
        data_type=ElmoObjectDataType.UNSIGNED8,
      )

      await self.can_sdo_download_elmo_object(
        node_id=int(axis),
        elmo_object_int=24770,
        sub_index=2,
        data="-3",
        data_type=ElmoObjectDataType.INTEGER8,
      )

      await self.can_sdo_download_elmo_object(
        node_id=int(axis),
        elmo_object_int=24669,
        sub_index=0,
        data="1",
        data_type=ElmoObjectDataType.INTEGER16,
      )

    for axis in KX2Backend.MOTION_AXES:
      await self.can_rpdo1_map(int(axis))
      await self.can_rpdo3_map(int(axis))

    # PVT Mode setup
    self._pvt_mode = True
    await self.pvt_select_mode(False)

  async def disconnect(self) -> None:
    self._can_read_task_running = False
    self._can_write_task_running = False

    if self._can_device is not None:
      self._can_device.shutdown()
      self._can_device = None

  async def _raise_an_event(self, event_data: EventData):
    print(f"Raising event: {event_data}")

    # TODO: on move error / emergency, we should set the indicator light and disable motors

  # --- helper methods and core functionalities ---

  def get_sdo_abort_message(self, code: int) -> str:
    sdo_default_abort_message = f"Unknown error {code:#010x}."
    return {
      0x05030000: "Toggle bit not alternated.",
      0x05040001: "Invalid or unknown client/server command specifier.",
      0x05040002: "Invalid block size.",
      0x05040003: "Invalid sequence number in SDO block upload.",
      0x05040005: "Out of memory.",
      0x06010000: "Unsupported access to an object.",
      0x06010001: "Attempt to read a write-only object.",
      0x06010002: "Attempt to write a read-only object.",
      0x06020000: "Object does not exist in object dictionary.",
      0x06040041: "Object cannot be mapped to PDO.",
      0x06040042: "Number and length of objects to be mapped exceeds PDO length.",
      0x06040043: "General parameter incompatibility.",
      0x06060000: "Access failed due to hardware error.",
      0x06070012: "Data type does not match. Service parameter too long.",
      0x06090011: "Sub-index does not exist.",
      0x06090030: "Value range of parameter exceeded (only for write access).",
      0x06090031: "Value of parameter written too high.",
      0x06090032: "Value of parameter written too low.",
      0x06090036: "Maximum value is less than minimum value.",
      0x08000000: "General error. Use the EC command to retrieve the actual error.",
      0x08000020: "Data cannot be transferred to or stored in application.",
      0x08000022: "Data cannot be transferred to or stored in application due to present device state.",
      0x08000024: "There is no data available to transmit.",
    }.get(code, sdo_default_abort_message)

  async def _add_os_query_wait_buffer(self, query: Query):
    """Adds a new query to the OS query wait buffer and returns a Future for awaiting the response.
    The Future will be set by the reading thread when the response is received.
    """
    fut = asyncio.get_event_loop().create_future()
    async with self._os_query_wait_buffer_lock:
      self._os_query_wait_buffer.append((query, fut))
    return fut

  def _get_os_queries(self, node_id: int) -> List[Tuple[Query, asyncio.Future]]:
    if not self._os_query_wait_buffer_lock.locked():
      raise RuntimeError("Lock must be held to access OS queries.")
    return [
      (query, fut)
      for query, fut in self._os_query_wait_buffer
      if not fut.done() and query.node_id == node_id
    ]

  async def _add_query_wait_buffer(self, query):
    """Adds a new query to the query wait buffer and returns a Future for awaiting the response.
    The Future will be set by the reading thread when the response is received.
    """
    fut = asyncio.get_event_loop().create_future()
    async with self._query_wait_buffer_lock:
      self._query_wait_buffer.append((query, fut))
    return fut

  def _get_queries(self, node_id: int) -> List[Tuple[Query, asyncio.Future]]:
    if not self._query_wait_buffer_lock.locked():
      raise RuntimeError("Lock must be held to access queries.")
    return [
      (query, fut)
      for query, fut in self._query_wait_buffer
      if not fut.done() and query.node_id == node_id
    ]

  async def can_sdo_upload(
    self,
    node_id: int,
    object_byte0: int,
    object_byte1: int,
    sub_index: int,
  ) -> bytes:
    """can_sdo_upload (read). Sends an SDO Upload request and waits for the response."""

    query = Query(
      node_id=node_id,
      object_byte0=object_byte0,
      object_byte1=object_byte1,
      sub_index=sub_index,
      msg_type="SDOUA",  # SDO Upload Acknowledge
    )
    fut = await self._add_os_query_wait_buffer(query)

    # Command for SDO_UPLOAD_INITIATE (0x40)
    await self.can_write(
      cob=COBType.RSDO,
      node_id=node_id,
      byte0=0x40,
      byte1=object_byte1,
      byte2=object_byte0,
      byte3=sub_index,
    )

    # Wait for response
    resp = await asyncio.wait_for(fut, timeout=1.0)

    if "SDOUA" not in resp:
      raise CanError(
        f"Failed to receive Initiate SDO Upload acknowledgement from motor drive {node_id}"
      )

    if "ABORT" in resp:
      abort_code_start = resp.index("ABORT")
      abort_msg = resp[abort_code_start + 4 :]
      raise CanError(
        f"SDO command {hex(object_byte0 << 8 | object_byte1)} sub-index {sub_index}. Abort code received from motor drive {node_id}. {abort_msg}"
      )

    # At this point, `resp` should contain SDOUANxxx or SDOUAExxx or actual data
    # This is where segmented transfer or direct values are extracted.
    # This part is highly complex and depends on `SDOUA` command.
    # TODO: more work here.

    # Simplified conversion from query.response to data_byte_ref
    if "SDOUAN" in resp:
      val_str = resp[len("SDOUAN") :]
      try:
        return int(val_str).to_bytes(8, "little")[:4]  # Max 4 bytes for expedited
      except ValueError:
        raise CanError(
          f"Failed to receive numeric response to {hex(object_byte0 << 8 | object_byte1)} sub-index {sub_index} from motor drive {node_id}"
        )
    if "SDOUAE" in resp:
      data_str = resp[len("SDOUAE") :]
      return [ord(c) for c in data_str]
    raise CanError(f"Failed to interpret SDO Upload response: {resp}")

  async def can_sdo_download(
    self,
    node_id: int,
    object_byte0: int,
    object_byte1: int,
    sub_index: int,
    data_byte: List[int],
  ) -> None:
    """
    can_sdo_download (write).
    Sends an SDO message and waits for a response.
    """
    if len(data_byte) <= 4:  # Expedited SDO Download
      query = Query(
        node_id=node_id,
        object_byte0=object_byte0,
        object_byte1=object_byte1,
        sub_index=sub_index,
        msg_type="SDODI",  # SDO Download Initiate Acknowledge
      )
      fut = await self._add_os_query_wait_buffer(query)

      # 0x21: initiate download, data length indicated by n = 4 - len(data) in bytes
      # If len 1 => 0x2F, len 2 => 0x2B, len 3 => 0x27, len 4 => 0x23
      cmd_byte = 0x23 | ((4 - len(data_byte)) << 2) | 0x01  # 0x01 is probably a fixed bit
      filled_data = list(data_byte) + [0] * (4 - len(data_byte))  # Pad with zeros to 4 bytes

      await self.can_write(
        COBType.RSDO,
        node_id,
        byte0=cmd_byte,
        byte1=object_byte1,
        byte2=object_byte0,
        byte3=sub_index,
        byte4=filled_data[0],
        byte5=filled_data[1],
        byte6=filled_data[2],
        byte7=filled_data[3],
      )

      resp = await asyncio.wait_for(fut, timeout=1.0)

      if "SDODI" not in resp:
        raise CanError(
          f"Failed to receive Expedited SDO Download acknowledgement from motor drive {node_id}"
        )

      if "ABORT" in resp:
        abort_code_start = resp.index("ABORT")
        abort_msg = resp[abort_code_start + 5 :]
        raise CanError(
          f"SDO command {hex(object_byte0 << 8 | object_byte1)} sub-index {sub_index} abort code received from motor drive {node_id}. {abort_msg}"
        )

    else:  # Segmented SDO Download (more than 4 bytes)
      # This is much more involved, involving multiple CAN messages for data transfer.
      query = Query(
        node_id=node_id,
        object_byte0=object_byte0,
        object_byte1=object_byte1,
        sub_index=sub_index,
        msg_type="SDODI",  # SDO Download Initiate Acknowledge
      )
      fut = await self._add_os_query_wait_buffer(query)

      # Initiate Segmented SDO Download (0x21): Object, Subindex, Data size (total len in bytes)
      # Data payload for this initiate message: object bytes, subindex, and total data len.
      # `CAN_Write(eCOBType.RSDO, NodeID, (byte) 33, ObjectByte1, ObjectByte0, SubIndex)`
      # Byte0=0x21 (initiate segment download), then object/subindex for the SDO.
      # The data length is sent with this message if known.
      # For `segmented` method, the total size is determined.

      # Byte0 = (byte)33 -- (CSID = 001, E=0, S=0) -- initiate Segmented SDO Download
      # Followed by Object and Subindex
      await self.can_write(
        COBType.RSDO, node_id, byte0=0x21, byte1=object_byte1, byte2=object_byte0, byte3=sub_index
      )  # Missing length

      # Wait for response on initiated segmented download.
      resp = await asyncio.wait_for(fut, timeout=1.0)  # 1000ms timeout

      if "SDODI" not in resp:
        raise CanError(
          f"Failed to receive Segmented SDO Download acknowledgement from motor drive {node_id}"
        )

      if "ABORT" in resp:
        abort_code_start = resp.index("ABORT")
        abort_msg = resp[abort_code_start + 5 :]
        raise CanError(
          f"SDO command {hex(object_byte0 << 8 | object_byte1)} sub-index {sub_index} abort code received from motor drive {node_id}. {abort_msg}"
        )

      # Token byte toggling and sending segments. This is a loops over data_byte_ref.
      toggle_bit = 0
      bytes_sent = 0
      while bytes_sent < len(data_byte):
        query_frag = Query(
          node_id=node_id,
          object_byte0=object_byte0,
          object_byte1=object_byte1,
          sub_index=sub_index,
          msg_type="CHR",
        )
        fut_frag = await self._add_os_query_wait_buffer(query_frag)

        remaining_data = len(data_byte) - bytes_sent
        segment_len = min(remaining_data, 7)  # Max 7 bytes payload for SDO segment

        # `num1` is toggle bit, `num10` is len_indicator. Last bit set if last fragment.
        # Base is 0x00, toggle bit is 0x10. N=num_bytes_not_used.
        # If last segment: add 0x01.
        cmd_sdo_seg = (toggle_bit << 4) | ((7 - segment_len) << 1)
        if bytes_sent + segment_len >= len(data_byte):  # This is the last segment
          cmd_sdo_seg |= 0x01  # Set last segment bit (C=1)

        segment_data = data_byte[bytes_sent : bytes_sent + segment_len]

        # Pad segment_data to 7 bytes for can_write
        padded_segment = segment_data + [0] * (7 - len(segment_data))

        await self.can_write(
          COBType.RSDO,
          node_id,
          byte0=cmd_sdo_seg,
          byte1=padded_segment[0],
          byte2=padded_segment[1],
          byte3=padded_segment[2],
          byte4=padded_segment[3],
          byte5=padded_segment[4],
          byte6=padded_segment[5],
          byte7=padded_segment[6],
          data_length=segment_len + 1,
        )  # cmd_sdo_seg itself + data

        resp = await asyncio.wait_for(fut_frag, timeout=1.0)  # 1000ms timeout

        if "CHR" not in resp:
          raise CanError(
            f"Failed to receive OS Character Transfer acknowledgement for segment from drive {node_id}"
          )

        bytes_sent += segment_len
        toggle_bit = 1 - toggle_bit  # Toggle

  async def os_interpreter(
    self,
    node_id: int,
    cmd: str,
    *,
    query: bool = False,
  ) -> str:
    def _abort_detail(resp_txt: str) -> Optional[str]:
      i = resp_txt.upper().find("ABORT")
      if i < 0:
        return None
      return resp_txt[i + 5 :].strip()

    async def send_and_wait(
      *,
      object_byte0: int,
      object_byte1: int,
      sub_index: int,
      msg_type: str,
      write_kwargs: dict,
      timeout_s: float = 1.0,
    ) -> str:
      q = Query(
        node_id=node_id,
        object_byte0=object_byte0,
        object_byte1=object_byte1,
        sub_index=sub_index,
        msg_type=msg_type,
      )
      fut = await self._add_os_query_wait_buffer(query=q)
      await self.can_write(**write_kwargs)
      return await asyncio.wait_for(fut, timeout=timeout_s)

    # --- 1) "Evaluate Immediately mode" acknowledge (SDODI), write: 35,36,0x10
    try:
      r = await send_and_wait(
        object_byte0=0x10,
        object_byte1=0x24,  # 36
        sub_index=0,
        msg_type="SDODI",
        write_kwargs=dict(
          cob=COBType.RSDO,
          node_id=node_id,
          byte0=0x23,  # 35
          byte1=0x24,  # 36
          byte2=0x10,
        ),
      )
    except Exception as e:
      raise CanError(
        f"Failed to send OS evaluate-immediately command to motor drive {node_id}: {e}"
      )

    if "SDODI" not in r:
      raise CanError(
        f"Failed to receive OS Evaluate Immediately mode acknowledgement from motor drive {node_id}"
      )

    ab = _abort_detail(r)
    if ab is not None:
      raise CanError(
        f"OS Interpreter command {cmd} abort code received from motor drive {node_id}. {ab}"
      )

    # --- 2) Initiate segmented SDO download, sub=1, write: 33,35,0x10,1
    r = await send_and_wait(
      object_byte0=0x10,
      object_byte1=0x23,  # 35
      sub_index=1,
      msg_type="SDODI",
      write_kwargs=dict(
        cob=COBType.RSDO,
        node_id=node_id,
        byte0=0x21,  # 33
        byte1=0x23,  # 35
        byte2=0x10,
        byte3=0x01,
      ),
    )

    if "SDODI" not in r:
      raise CanError(
        f"Failed to receive Segmented SDO Download acknowledgement from motor drive {node_id}"
      )

    ab = _abort_detail(r)
    if ab is not None:
      raise CanError(
        f"OS Interpreter command {cmd} abort code received from motor drive {node_id}. {ab}"
      )

    # --- 3) Send command as 7-byte segments with CANopen toggle/unused/last bits.
    cmd_bytes = cmd.encode("ascii", errors="strict")
    chunks: List[bytes] = [cmd_bytes[i : i + 7] for i in range(0, len(cmd_bytes), 7)]
    if not chunks:
      chunks = [b""]

    toggle = 1

    for idx, chunk in enumerate(chunks):
      toggle = 0 if toggle != 0 else 1  # flip each segment
      unused = 7 - len(chunk)
      last = idx == len(chunks) - 1

      # Byte0: [toggle in bit4] + [unused in bits1-3] + [last in bit0]
      byte0 = (toggle << 4) | ((unused & 0x07) << 1) | (1 if last else 0)

      padded = chunk + (b"0" * unused)
      b1, b2, b3, b4, b5, b6, b7 = (padded[i] for i in range(7))

      r = await send_and_wait(
        object_byte0=0,
        object_byte1=0,
        sub_index=0,
        msg_type="CHR",
        write_kwargs=dict(
          cob=COBType.RSDO,
          node_id=node_id,
          byte0=byte0,
          byte1=b1,
          byte2=b2,
          byte3=b3,
          byte4=b4,
          byte5=b5,
          byte6=b6,
          byte7=b7,
        ),
      )

      if "CHR" not in r:
        raise CanError(
          f"Failed to receive OS Character Transfer acknowledgement from motor drive {node_id}"
        )

      ab = _abort_detail(r)
      if ab is not None:
        raise CanError(
          f"OS Interpreter command {cmd} abort code received from motor drive {node_id}. {ab}"
        )

    # --- 4) Read OS command status (STAT), sub=2, write: 0x40,35,0x10,2
    r = await send_and_wait(
      object_byte0=0x10,
      object_byte1=0x23,  # 35
      sub_index=2,
      msg_type="STAT",
      write_kwargs=dict(
        cob=COBType.RSDO,
        node_id=node_id,
        byte0=0x40,
        byte1=0x23,
        byte2=0x10,
        byte3=0x02,
      ),
    )

    if "STAT" not in r:
      raise CanError(f"Failed to receive OS command status from motor drive {node_id}")

    ab = _abort_detail(r)
    if ab is not None:
      raise CanError(
        f"OS Interpreter command {cmd} abort code received from motor drive {node_id}. {ab}"
      )

    # If not querying a response, done.
    if not query:
      return ""

    # --- 5) Initiate SDO upload (SDOUA), sub=3, write: 0x40,35,0x10,3
    r = await send_and_wait(
      object_byte0=0x10,
      object_byte1=0x23,
      sub_index=3,
      msg_type="SDOUA",
      write_kwargs=dict(
        cob=COBType.RSDO,
        node_id=node_id,
        byte0=0x40,
        byte1=0x23,
        byte2=0x10,
        byte3=0x03,
      ),
    )

    if "SDOUA" not in r:
      raise CanError(
        f"Failed to receive Initiate SDO Upload acknowledgement from motor drive {node_id}"
      )

    ab = _abort_detail(r)
    if ab is not None:
      raise CanError(
        f"OS Interpreter command {cmd} abort code received from motor drive {node_id}. {ab}"
      )

    # expects r like: "SDOUA" + ("N" or "E") + payload
    response_out = ""

    if len(r) >= 6 and r.startswith("SDOUA"):
      kind = r[5:6]  # 6th char (1-based)
      payload = r[6:]  # everything after that char

      if kind == "E":
        response_out = payload
        return response_out

      if kind == "N":
        # if non-numeric => error
        if not payload.strip().lstrip("+-").isdigit():
          raise CanError(f"Failed to receive a response to '{cmd}' from motor drive {node_id}")
        # else fall through to segmented upload reading below

    # --- 6) Segmented SDO upload (SDOSU)
    seg_idx = 0
    response_out = ""

    while True:
      # client request byte0 toggles between 0x60 and 0x70
      req0 = 0x60 if (seg_idx % 2) == 0 else 0x70

      r = await send_and_wait(
        object_byte0=0,
        object_byte1=0,
        sub_index=0,
        msg_type="SDOSU",
        write_kwargs=dict(
          cob=COBType.RSDO,
          node_id=node_id,
          byte0=req0,
        ),
      )

      if "SDOSU" not in r:
        raise CanError(f"Failed to receive OS response value from motor drive {node_id}")

      ab = _abort_detail(r)
      if ab is not None:
        raise CanError(
          f"OS Interpreter command {cmd} abort code received from motor drive {node_id}. {ab}"
        )

      if len(r) <= 7:
        raise CanError(f"Failed to extract data response for '{cmd}' from motor drive {node_id}")

      # r: "SDOSU" + (pos6: 'C'/'D') + (pos7: toggle bit '0'/'1') + data...
      cd_flag = r[5:6]
      toggle_bit = r[6:7]
      data_part = r[7:]

      response_out += data_part

      if (req0 == 0x60 and toggle_bit == "1") or (req0 == 0x70 and toggle_bit == "0"):
        raise CanError(f"Toggle bit mismatch in response for '{cmd}' from motor drive {node_id}")

      if cd_flag == "C":
        seg_idx += 1
        continue

      if cd_flag == "D":
        return response_out

      raise CanError(f"Failed to receive data response for '{cmd}' from motor drive {node_id}")

  async def can_sync(self) -> None:
    await self.can_write(cob=COBType.SYNC, node_id=0)

  async def can_sdo_download_elmo_object(
    self,
    node_id: int,
    elmo_object_int: int,
    sub_index: int,
    data: str,
    data_type: ElmoObjectDataType,
  ) -> None:
    """Wrapper around can_sdo_download for Elmo specific objects, handling data type conversions."""
    data_bytes: List[int] = []

    if data_type == ElmoObjectDataType.UNSIGNED8:
      data_bytes = list(int(data).to_bytes(1, "little"))
    elif data_type == ElmoObjectDataType.UNSIGNED16:
      data_bytes = list(int(data).to_bytes(2, "little"))
    elif data_type == ElmoObjectDataType.UNSIGNED32:
      data_bytes = list(int(float(data)).to_bytes(4, "little"))
    elif data_type == ElmoObjectDataType.UNSIGNED64:
      data_bytes = list(int(data).to_bytes(8, "little"))
    elif data_type == ElmoObjectDataType.INTEGER8:
      data_bytes = list(int(data).to_bytes(1, "little", signed=True))
    elif data_type == ElmoObjectDataType.INTEGER16:
      data_bytes = list(int(data).to_bytes(2, "little", signed=True))
    elif data_type == ElmoObjectDataType.INTEGER32:
      data_bytes = list(int(float(data)).to_bytes(4, "little", signed=True))
    elif data_type == ElmoObjectDataType.INTEGER64:
      data_bytes = list(int(data).to_bytes(8, "little", signed=True))
    elif data_type == ElmoObjectDataType.STR:
      data_bytes = [ord(c) for c in data]
    else:
      raise CanError(f"Unsupported data type for SDO Write: {data_type.name}")

    obj_byte0 = elmo_object_int >> 8
    obj_byte1 = elmo_object_int & 0xFF
    await self.can_sdo_download(node_id, obj_byte0, obj_byte1, sub_index, data_bytes)

  async def can_sdo_upload_elmo_object(
    self,
    node_id: int,
    elmo_object_int: int,
    sub_index: int,
    data_type: ElmoObjectDataType,
  ) -> str:
    """Wrapper around can_sdo_upload for Elmo specific objects, handling data type conversions."""

    obj_byte0 = elmo_object_int >> 8
    obj_byte1 = elmo_object_int & 0xFF
    data_bytes = await self.can_sdo_upload(node_id, obj_byte0, obj_byte1, sub_index)

    if len(data_bytes) == 0:
      return ""
    if data_type == ElmoObjectDataType.UNSIGNED8:
      return str(int.from_bytes(data_bytes[:1], "little", signed=False))
    if data_type == ElmoObjectDataType.UNSIGNED16:
      return str(int.from_bytes(data_bytes[:2], "little", signed=False))
    if data_type == ElmoObjectDataType.UNSIGNED32:
      return str(int.from_bytes(data_bytes[:4], "little", signed=False))
    if data_type == ElmoObjectDataType.UNSIGNED64:
      return str(int.from_bytes(data_bytes[:8], "little", signed=False))
    if data_type == ElmoObjectDataType.INTEGER16:
      return str(int.from_bytes(data_bytes[:2], "little", signed=True))
    if data_type == ElmoObjectDataType.INTEGER32:
      return str(int.from_bytes(data_bytes[:4], "little", signed=True))
    if data_type == ElmoObjectDataType.INTEGER64:
      return str(int.from_bytes(data_bytes[:8], "little", signed=True))
    if data_type == ElmoObjectDataType.STR:
      return "".join([chr(b) for b in data_bytes])
    raise CanError(f"Unsupported data type for SDO Read conversion: {data_type.name}")

  # --- PDO Mapping methods ---

  async def can_tpdo_unmap(self, tpdo: TPDO, node_id: int):
    cob_type_int = {
      TPDO.TPDO1: COBType.TPDO1.value,
      TPDO.TPDO3: COBType.TPDO3.value,
      TPDO.TPDO4: COBType.TPDO4.value,
    }[tpdo]

    if not (0 <= node_id <= 0x7F):
      raise ValueError(f"node_id must be 0..127, got {node_id}")

    node_id = node_id & 0x7F
    num1 = ((cob_type_int & 0x01) << 7) | node_id
    num2 = (cob_type_int >> 1) & 0x07

    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=24,
      object_byte1=tpdo.value - 1,
      sub_index=1,
      data_byte=[
        num1,
        num2,
        0,
        0xC0,
      ],
    )

    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=26,
      object_byte1=tpdo.value - 1,
      sub_index=0,
      data_byte=[0, 0, 0, 0],
    )

    for index in range(len(self.tpdo_mapped_object[node_id][tpdo])):
      self.tpdo_mapped_object[node_id][tpdo][index] = TPDOMappedObject.NotMapped

  async def can_tpdo3_map(self, node_id: int) -> None:
    mapped_objects = [TPDOMappedObject.StatusWord]
    # EventTimerMs=0, Delay100us=0, TransmissionType=EventDrivenDev
    await self.can_tpdo_map(
      tpdo=TPDO.TPDO3,
      node_id=node_id,
      mapped_objects=mapped_objects,
      event_trigger=TPDOTrigger.MotionComplete,
    )

  async def can_tpdo4_map(self, node_id: int) -> None:
    mapped_objects = [TPDOMappedObject.DigitalInputs]
    await self.can_tpdo_map(
      tpdo=TPDO.TPDO4,
      node_id=node_id,
      mapped_objects=mapped_objects,
      event_trigger=TPDOTrigger.DigitalInputEvent,
    )

  async def can_rpdo1_map(self, node_id: int) -> None:
    mapped_objects = [RPDOMappedObject.ControlWord]
    await self.can_rpdo_map(
      rpdo=RPDO.RPDO1,
      node_id=node_id,
      mapped_objects=mapped_objects,
      transmission_type=PDOTransmissionType.SynchronousCyclic,
    )

  async def can_rpdo3_map(self, node_id: int) -> None:
    mapped_objects = [RPDOMappedObject.TargetPositionIP, RPDOMappedObject.TargetVelocityIP]
    await self.can_rpdo_map(
      rpdo=RPDO.RPDO3,
      node_id=node_id,
      mapped_objects=mapped_objects,
      transmission_type=PDOTransmissionType.EventDrivenDev,
    )

  async def can_rpdo_map(
    self,
    rpdo: RPDO,
    node_id: int,
    mapped_objects: List[RPDOMappedObject],
    transmission_type: PDOTransmissionType,
  ) -> None:
    """Maps RPDOs for incoming messages."""

    rpdo_num = int(rpdo)  # expects 1..4
    rpdo_idx = (rpdo_num - 1) & 0xFF  # original passes (byte)(RPDO - 1)

    # Map RPDO -> COB function code (4-bit) used to build the 11-bit COB-ID
    # (original decompilation only showed 1,3,4; RPDO2 is included here)
    if rpdo == RPDO.RPDO1:
      cob_type = COBType.RPDO1
    elif rpdo == RPDO.RPDO3:
      cob_type = COBType.RPDO3
    elif rpdo == RPDO.RPDO4:
      cob_type = COBType.RPDO4
    else:
      raise ValueError(f"Unsupported RPDO: {rpdo!r}")

    # CANopen 11-bit COB-ID: (function_code << 7) | node_id(7 bits)
    cob_id_11 = ((int(cob_type) & 0x0F) << 7) | (node_id & 0x7F)

    # 1) Disable PDO (set bit 31) while configuring: 0x80000000 | cob_id
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x14,
      object_byte1=rpdo_idx,
      sub_index=0x01,
      data_byte=_u32_le(0x80000000 | cob_id_11),
    )

    # 2) Clear mapping count (sub 0) at 0x1600 + rpdo_idx
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x16,
      object_byte1=rpdo_idx,
      sub_index=0x00,
      data_byte=[0, 0, 0, 0],
    )

    # 3) Set transmission type (sub 2) at 0x1400 + rpdo_idx
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x14,
      object_byte1=rpdo_idx,
      sub_index=0x02,
      data_byte=[int(transmission_type) & 0xFF, 0, 0, 0],
    )

    # 4) Write each mapped object (sub 1..n) at 0x1600 + rpdo_idx
    for i, mo in enumerate(mapped_objects):
      await self.can_sdo_download(
        node_id=node_id,
        object_byte0=0x16,
        object_byte1=rpdo_idx,
        sub_index=(i + 1) & 0xFF,
        data_byte=_u32_le(int(mo)),
      )

    # 5) Set mapping count (sub 0)
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x16,
      object_byte1=rpdo_idx,
      sub_index=0x00,
      data_byte=[len(mapped_objects) & 0xFF, 0, 0, 0],
    )

    # 6) Enable PDO (clear bit 31): write cob_id only
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x14,
      object_byte1=rpdo_idx,
      sub_index=0x01,
      data_byte=_u32_le(cob_id_11),
    )

  async def can_tpdo_map(
    self,
    tpdo: TPDO,
    node_id: int,
    mapped_objects: List[TPDOMappedObject],
    event_trigger: TPDOTrigger,
    event_timer_ms: int = 0,
    delay_100_us: int = 0,
    transmission_type: PDOTransmissionType = PDOTransmissionType.EventDrivenDev,
  ) -> None:
    """Maps TPDOs for outgoing messages."""

    tpdo_num = int(tpdo)  # expects 1..4
    tpdo_idx = (tpdo_num - 1) & 0xFF  # (byte)(TPDO - 1)

    if tpdo == TPDO.TPDO1:
      cob_type = COBType.TPDO1
    elif tpdo == TPDO.TPDO3:
      cob_type = COBType.TPDO3
    elif tpdo == TPDO.TPDO4:
      cob_type = COBType.TPDO4
    else:
      raise ValueError(f"Unsupported TPDO: {tpdo!r}")

    # CANopen 11-bit COB-ID
    cob_id_11 = ((int(cob_type) & 0x0F) << 7) | (node_id & 0x7F)

    # Event trigger mask: 2**EventTrigger, stored as u32
    event_mask = 1 << int(event_trigger)

    # 1) Disable TPDO while configuring: 0xC0000000 | cob_id
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x18,
      object_byte1=tpdo_idx,
      sub_index=0x01,
      data_byte=_u32_le(0xC0000000 | cob_id_11),
    )

    # 2) Clear mapping count: 0x1A00 + tpdo_idx, sub 0
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x1A,
      object_byte1=tpdo_idx,
      sub_index=0x00,
      data_byte=[0, 0, 0, 0],
    )

    # 3) Transmission type: 0x1800 + tpdo_idx, sub 2
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x18,
      object_byte1=tpdo_idx,
      sub_index=0x02,
      data_byte=[int(transmission_type) & 0xFF, 0, 0, 0],
    )

    # 4) Inhibit time / delay (100us units): sub 3
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x18,
      object_byte1=tpdo_idx,
      sub_index=0x03,
      data_byte=[delay_100_us & 0xFF, 0, 0, 0],
    )

    # 5) Event timer (ms): sub 5
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x18,
      object_byte1=tpdo_idx,
      sub_index=0x05,
      data_byte=[event_timer_ms & 0xFF, 0, 0, 0],
    )

    # 6) Write event trigger mask to 0x2F20 sub = TPDO (matches can_sdo_download(NodeID, 0x2F, 0x20, TPDO, ...))
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x2F,
      object_byte1=0x20,
      sub_index=tpdo_num & 0xFF,
      data_byte=_u32_le(event_mask),
    )

    # 7) Write mapped objects into 0x1A00 + tpdo_idx, sub 1..n
    for i, mo in enumerate(mapped_objects):
      await self.can_sdo_download(
        node_id=node_id,
        object_byte0=0x1A,
        object_byte1=tpdo_idx,
        sub_index=(i + 1) & 0xFF,
        data_byte=_u32_le(int(mo)),
      )

    # 8) Set mapping count (sub 0)
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x1A,
      object_byte1=tpdo_idx,
      sub_index=0x00,
      data_byte=[len(mapped_objects) & 0xFF, 0, 0, 0],
    )

    # 9) Re-enable TPDO: write 0x40000000 | cob_id
    await self.can_sdo_download(
      node_id=node_id,
      object_byte0=0x18,
      object_byte1=tpdo_idx,
      sub_index=0x01,
      data_byte=_u32_le(0x40000000 | cob_id_11),
    )

    # 10) Mirror the original side-effect: store mapping in self.tpdo_mapped_object
    self.tpdo_mapped_object[node_id][tpdo] = mapped_objects

  async def pvt_select_mode(self, enable: bool) -> None:
    """Enables or disables PVT mode on all motors in the group."""

    if enable:
      if not self._pvt_mode:
        for axis in KX2Backend.MOTION_AXES:
          await self.can_sdo_download(
            node_id=int(axis),
            object_byte0=0x60,
            object_byte1=0xC4,
            sub_index=0x06,
            data_byte=[0],
          )
          await self.can_sdo_download(
            node_id=int(axis),
            object_byte0=0x60,
            object_byte1=0x60,
            sub_index=0x00,
            data_byte=[7],
          )
        self._pvt_mode = True
      else:
        for axis in KX2Backend.MOTION_AXES:
          await self.can_sdo_download(
            node_id=int(axis),
            object_byte0=0x60,
            object_byte1=0x60,
            sub_index=0x00,
            data_byte=[1],
          )
          await self.can_sdo_download(
            node_id=int(axis),
            object_byte0=0x60,
            object_byte1=0xC4,
            sub_index=0x06,
            data_byte=[0],
          )
          await self.can_sdo_download(
            node_id=int(axis),
            object_byte0=0x60,
            object_byte1=0x60,
            sub_index=0x00,
            data_byte=[7],
          )
    elif self._pvt_mode:
      for axis in KX2Backend.MOTION_AXES:
        await self.can_sdo_download(
          node_id=int(axis),
          object_byte0=0x60,
          object_byte1=0x60,
          sub_index=0x00,
          data_byte=[1],
        )
      self._pvt_mode = False

  async def binary_interpreter(
    self,
    node_id: int,
    cmd: str,
    cmd_index: int,
    cmd_type: CmdType,
    value: str = "0",
    val_type: ValType = ValType.Int,
    low_priority: bool = False,
  ) -> Union[str, float]:
    timeout = 10.0 if cmd.upper() == "SV" else 1.0

    is_float = val_type == ValType.Float
    is_query = cmd_type == CmdType.ValQuery
    is_execute = cmd_type == CmdType.Execute

    # Helper: build command bytes
    def _build_bytes() -> tuple[int, int, int, int, int, int, int, int]:
      # byte0, byte1: ASCII of first and last char of cmd
      byte0 = ord(cmd[0])
      byte1 = ord(cmd[-1])

      # Encode cmd_index into 14 bits:
      # - low 8 bits -> byte2
      # - high 6 bits -> lower bits of byte3
      byte2 = cmd_index & 0xFF
      byte3 = (cmd_index >> 8) & 0x3F  # keep only 6 bits

      # Set flags in bits 6 and 7 of byte3
      if is_query:
        byte3 |= 0x40  # bit 6
      if is_float:
        byte3 |= 0x80  # bit 7

      # Encode value
      if is_float:
        byte4, byte5, byte6, byte7 = struct.pack("<f", float(value))
      else:
        byte4, byte5, byte6, byte7 = struct.pack("<i", int(round(float(value))))

      return (
        byte0 & 0xFF,
        byte1 & 0xFF,
        byte2 & 0xFF,
        byte3 & 0xFF,
        byte4 & 0xFF,
        byte5 & 0xFF,
        byte6 & 0xFF,
        byte7 & 0xFF,
      )

    # Helper: numeric comparison with ±1% tolerance like the original
    def _float_matches(expected_str: str, actual_str: str) -> bool:
      try:
        expected = float(expected_str)
        actual = float(actual_str)
      except ValueError:
        return False
      if actual == 0.0:
        return expected == 0.0
      ratio = expected / actual
      return expected == actual or (0.99 < ratio < 1.01)

    max_attempts = 1

    # Single-node path (NodeID != 10)
    if node_id != 10:
      for attempt in range(1, max_attempts + 1):
        if value == "":
          value = "0"

        (
          byte0,
          byte1,
          byte2,
          byte3,
          byte4,
          byte5,
          byte6,
          byte7,
        ) = _build_bytes()

        # Build a minimal query object compatible with your buffer/reader
        # We don't know your exact type, so SimpleNamespace gives us attributes.
        query = Query(
          node_id=node_id,
          msg_index=cmd_index,
          msg_type=cmd,
        )

        fut = await self._add_query_wait_buffer(query)

        await self.can_write(
          COBType.RPDO2,
          node_id,
          byte0,
          byte1,
          byte2,
          byte3,
          byte4,
          byte5,
          byte6,
          byte7,
          execute=is_execute,
          low_priority=low_priority,
          data_length=4 if is_execute else 8,
        )

        try:
          resp = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
          if attempt == max_attempts:
            raise CanError(
              f"Timeout waiting for response to {cmd}[{cmd_index}] from node {node_id}"
            )
          # retry
          continue

        # Query: just return the response
        if is_query:
          value = str(resp)
          return float(value) if is_float else int(float(value))

        # Execute: only care that we got *some* response
        if is_execute:
          if resp == "" and attempt == max_attempts:
            raise CanError(
              f"No response for execute command {cmd}[{cmd_index}] from node {node_id}"
            )
          if resp != "":
            return float(value) if is_float else int(float(value))
          # else retry
          continue

        # Write: verify echoed value
        if is_float:
          ok = _float_matches(value, str(resp))
        else:
          ok = (int(float(resp))) == (int(float(value)))

        if ok:
          return float(value) if is_float else int(float(value))

        if attempt == max_attempts:
          raise CanError(
            f"Unexpected CAN response: attempted to send {cmd}[{cmd_index}]={value}, "
            f"but received response={resp} from node {node_id}"
          )
        # else retry

      # Should never get here
      raise CanError("Internal error in binary_interpreter (single-node)")

    # Group path (NodeID == 10)
    grp_ids = [int(axis) for axis in KX2Backend.MOTION_AXES]

    for attempt in range(1, max_attempts + 1):
      if value == "":
        value = "0"

      (
        byte0,
        byte1,
        byte2,
        byte3,
        byte4,
        byte5,
        byte6,
        byte7,
      ) = _build_bytes()

      # One query per node in group
      queries = []
      futures = []
      for gid in grp_ids:
        q = Query(
          node_id=gid,
          msg_index=cmd_index,
          msg_type=cmd,
        )
        queries.append(q)
        fut = await self._add_query_wait_buffer(q)
        futures.append(fut)

      await self.can_write(
        COBType.RPDO2,
        node_id,  # broadcast/group node (10)
        byte0,
        byte1,
        byte2,
        byte3,
        byte4,
        byte5,
        byte6,
        byte7,
        execute=is_execute,
        low_priority=low_priority,
        data_length=4 if is_execute else 8,
      )

      try:
        # Wait for *all* group responses
        resps = await asyncio.wait_for(
          asyncio.gather(*futures, return_exceptions=False),
          timeout=timeout,
        )
      except asyncio.TimeoutError:
        if attempt == max_attempts:
          raise CanError(
            f"Timeout waiting for group response to {cmd}[{cmd_index}] " f"from nodes {grp_ids}"
          )
        # retry
        continue

      # Query: concatenate responses with commas
      if is_query:
        if any(r == "" for r in resps):
          if attempt == max_attempts:
            raise CanError(
              f"Incomplete group query response for {cmd}[{cmd_index}] " f"from nodes {grp_ids}"
            )
          # retry
          continue

        value = ",".join(str(r) for r in resps)
        return float(value) if is_float else int(float(value))

      # Execute: just require all responses non-empty
      if is_execute:
        if all(r != "" for r in resps):
          return float(value) if is_float else int(float(value))
        if attempt == max_attempts:
          missing_nodes = [gid for gid, r in zip(grp_ids, resps) if r == ""]
          raise CanError(
            f"No execute response from nodes {missing_nodes} " f"for {cmd}[{cmd_index}]"
          )
        # retry
        continue

      # Write: verify each node's echoed value
      mismatch_node = None
      mismatch_resp = None

      for gid, resp in zip(grp_ids, resps):
        if is_float:
          ok = _float_matches(value, str(resp))
        else:
          ok = str(resp) == str(value)

        if not ok:
          mismatch_node = gid
          mismatch_resp = resp
          break

      if mismatch_node is None:
        # everyone matched
        return float(value) if is_float else int(float(value))

      if attempt == max_attempts:
        raise CanError(
          f"Unexpected CAN response: attempted to send {cmd}[{cmd_index}]={value}, "
          f"but received response={mismatch_resp} from node {mismatch_node}"
        )
      # else retry

    # Should never get here
    raise CanError("Internal error in binary_interpreter (group)")

  # --- Functions ---

  async def configure_input_logic(
    self,
    node_id: int,
    input_num: int,
    logic_high: bool,
    logic_type: InputLogic,
  ) -> None:
    val_to_set = logic_type.value
    if logic_high:
      val_to_set += 1

    right = await self.binary_interpreter(node_id, "IL", input_num, CmdType.ValQuery)

    if val_to_set != right:
      await self.binary_interpreter(node_id, "IL", input_num, CmdType.ValSet, str(val_to_set))
      await asyncio.sleep(0.25)

  async def read_input(self, node_id: int, input_num: int) -> bool:
    """Returns the State (bool)."""
    left = await self.binary_interpreter(node_id, "IB", input_num, CmdType.ValQuery)
    return left == 1

  async def read_output(self, node_id: int, output_num: int) -> bool:
    """Returns the State."""
    expression = await self.binary_interpreter(node_id, "OP", 0, CmdType.ValQuery)
    val = int(expression)

    mask = int(round(math.pow(2, output_num - 1)))
    return (val & mask) == mask

  async def set_output(self, node_id: int, output_num: int, state: bool) -> str:
    val = "1" if state else "0"
    return await self.binary_interpreter(node_id, "OB", output_num, CmdType.ValSet, val)

  async def motor_get_current_position(self, node_id: int, pu: bool = False) -> int:
    cmd = "PU" if pu else "PX"
    val_str = await self.binary_interpreter(int(node_id), cmd, 0, CmdType.ValQuery)
    return int(round(float(val_str)))

  async def motor_get_motion_status(self, node_id: int) -> int:
    val = await self.binary_interpreter(node_id, "MS", 0, CmdType.ValQuery)
    return int(round(float(val)))

  async def motor_enable(self, axis: KX2Axis, state: bool) -> None:
    if not isinstance(axis, KX2Axis):
      raise

    flag = not (axis in KX2Backend.MOTION_AXES or int(axis) == self.grp_id)

    if state:
      self.EmcyMoveErrorReceived = False
      if flag:
        await self.binary_interpreter(axis, "MO", 0, CmdType.ValSet, "1")
      else:
        # Standard DS402 Enable Sequence
        await self.control_word_set(node_id=int(axis), value=0)
        await self.control_word_set(node_id=int(axis), value=128)
        await self.control_word_set(node_id=int(axis), value=6)
        await self.control_word_set(node_id=int(axis), value=7)
        await self.control_word_set(node_id=int(axis), value=15)

      await asyncio.sleep(0.1)

      left = await self.binary_interpreter(
        node_id=int(axis), cmd="MO", cmd_index=0, cmd_type=CmdType.ValQuery, val_type=ValType.Int
      )
      if left != 1:
        raise CanError(f"Motor failed to enable (axis = {axis})")
    else:
      if flag:
        try:
          await self.binary_interpreter(
            node_id=int(axis), cmd="MO", cmd_index=0, cmd_type=CmdType.ValSet, value="0"
          )
        except Exception as e:
          pass
      else:
        await self.control_word_set(node_id=int(axis), value=7)
        await self.control_word_set(node_id=int(axis), value=6)

      await asyncio.sleep(0.1)
      left = await self.binary_interpreter(
        node_id=int(axis), cmd="MO", cmd_index=0, cmd_type=CmdType.ValQuery
      )
      if left != 0:
        raise RuntimeError(f"Motor failed to disable (axis = {axis}")

  async def motor_set_move_direction(self, node_id: int, direction: JointMoveDirection) -> None:
    val_str = "1"
    if direction == JointMoveDirection.Clockwise:
      val_str = "65"
    elif direction == JointMoveDirection.Counterclockwise:
      val_str = "129"
    elif direction == JointMoveDirection.ShortestWay:
      val_str = "193"

    await self.can_sdo_download_elmo_object(
      node_id, 24818, 0, val_str, ElmoObjectDataType.UNSIGNED16
    )

  async def motor_emergency_stop(self, node_id: int) -> None:
    await self.binary_interpreter(node_id, "MO", 0, CmdType.ValSet, "0")

  async def user_program_run(
    self,
    axis: KX2Axis,
    user_function: str,
    params=None,
    timeout_sec: int = 0,
    wait_until_done: bool = False,
  ) -> int:
    """
    Runs a user program on `axis` and optionally waits for completion.

    Returns:
      last_line_completed (0 if unknown / not provided by controller)
    Raises:
      CanError on any controller/protocol/runtime failure or timeout.
    """
    if not isinstance(axis, int):
      raise ValueError("axis must be int")
    if axis < 0 or axis > 255:
      raise ValueError("axis must be in [0, 255]")

    node_id = int(axis)

    # PS query
    ps = int(await self.binary_interpreter(node_id, "PS", 0, CmdType.ValQuery))

    if ps == -2:
      raise CanError(f"Axis {axis}: controller reported PS=-2 (not ready / unavailable)")

    # If not idle (-1), request idle by setting UI[1]=0 and wait up to 3s for PS=-1
    if ps != -1:
      await self.binary_interpreter(
        node_id,
        "UI",
        1,
        CmdType.ValSet,
        value=0,  # don't stringify bytes; pass normal ints
        val_type=ValType.Int,
      )

      t0 = time.monotonic()
      while (time.monotonic() - t0) < 3.0:
        ps = int(await self.binary_interpreter(node_id, "PS", 0, CmdType.ValQuery))
        if ps == -1:
          break
        await asyncio.sleep(0.01)
      else:
        raise CanError(f"Axis {axis}: did not reach idle state (PS=-1) within 3s (last PS={ps})")

    # Build "(a,b,c)" argument list
    arg_str = ""
    if params:
      parts = [str(p) for p in params]
      print(parts)
      if parts:
        arg_str = f"({','.join(parts)})"
      print(arg_str)

    # Arm UI[1]=1 then execute XQ
    await self.binary_interpreter(
      node_id,
      "UI",
      1,
      CmdType.ValSet,
      value="1",
      val_type=ValType.Int,
    )

    cmd = f"XQ##{user_function}{arg_str}"
    print(cmd)
    await self.os_interpreter(node_id, cmd, query=False)

    last_line_completed = 0

    if wait_until_done:
      # Wait while PS==1 and UI[1]==1, or until timeout
      t0 = time.monotonic()
      ps = 1
      ui1 = 1
      while ps == 1 and ui1 == 1 and (time.monotonic() - t0) < float(timeout_sec):
        ps = int(await self.binary_interpreter(node_id, "PS", 0, CmdType.ValQuery))
        ui1 = int(await self.binary_interpreter(node_id, "UI", 1, CmdType.ValQuery))
        await asyncio.sleep(0.01)

      # Grab UI[2] (last line completed) after wait loop (matches original behavior)
      expr_raw = await self.binary_interpreter(node_id, "UI", 2, CmdType.ValQuery)
      try:
        last_line_completed = int(str(expr_raw).strip())
      except Exception:
        last_line_completed = 0

      # UI[1] should be "0" on successful completion
      if ui1 != 0:
        raise CanError(
          f"Axis {axis}: user program ended with UI[1]={ui1} (expected 0), last_line={last_line_completed}"
        )

      # Timeout condition: still stuck in running state
      if ps == 1 and ui1 == 1:
        raise CanError(
          f"Axis {axis}: timeout waiting for '{user_function}' after {timeout_sec}s, last_line={last_line_completed}"
        )

    return 0

  async def home_motor(
    self,
    axis: "KX2Axis",
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
    left = await self.binary_interpreter(int(axis), "CA", 41, CmdType.ValQuery)
    if left == 24:
      raise RuntimeError("Error 43")

    try:
      await self.motor_hard_stop_search(axis, srch_vel, srch_acc, max_pe, hs_pe, timeout)
    except Exception as e:
      # Check fault
      fault = await self.motor_get_fault(axis)
      if fault is not None:
        raise RuntimeError(fault)
      raise e

    await self.motor_enable(axis=axis, state=True)

    await self.motors_move_absolute_execute(
      plan=MotorsMovePlan(
        moves=[
          MotorMoveParam(
            axis=KX2Axis(axis),
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
    await self.motor_index_search(axis, abs(srch_vel), srch_acc, is_positive, timeout)

    await self.motors_move_absolute_execute(
      plan=MotorsMovePlan(
        moves=[
          MotorMoveParam(
            axis=KX2Axis(axis),
            position=ind_offset,
            velocity=offset_vel,
            acceleration=offset_acc,
            relative=False,
            direction=JointMoveDirection.ShortestWay,
          )
        ]
      )
    )
    await self.motor_reset_encoder_position(axis, home_pos)
    await self.motor_set_homed_status(axis, HomeStatus.Homed)

  async def motor_hard_stop_search(
    self, axis: KX2Axis, srch_vel: int, srch_acc: int, max_pe: int, hs_pe: int, timeout: float
  ) -> None:
    await self.binary_interpreter(int(axis), "ER", 3, CmdType.ValSet, str(max_pe * 10))
    await self.binary_interpreter(int(axis), "AC", 0, CmdType.ValSet, str(srch_acc))
    await self.binary_interpreter(int(axis), "DC", 0, CmdType.ValSet, str(srch_acc))
    # Clear homing params
    for i in [3, 4, 5, 2]:
      await self.binary_interpreter(int(axis), "HM", i, CmdType.ValSet, "0")

    await self.binary_interpreter(
      node_id=int(axis), cmd="JV", cmd_index=0, cmd_type=CmdType.ValSet, value=str(srch_vel)
    )

    try:
      params = [str(int(hs_pe)), str(int(timeout * 1000))]
      try:
        last_line = await self.user_program_run(axis, "Home", params, int(timeout), True)
        if last_line in [1, 2, 3]:
          raise RuntimeError(f"Homing Script Error {34 + last_line}")
      except Exception as e:
        # Re-raise unless specific handling needed
        raise e

      curr_pos = await self.motor_get_current_position(axis)

      await self.binary_interpreter(
        node_id=int(axis), cmd="PA", cmd_index=0, cmd_type=CmdType.ValSet, value=str(curr_pos)
      )
      await self.binary_interpreter(
        node_id=int(axis), cmd="SP", cmd_index=0, cmd_type=CmdType.ValSet, value=str(srch_vel)
      )
      await self.binary_interpreter(
        node_id=int(axis), cmd="AC", cmd_index=0, cmd_type=CmdType.ValSet, value=str(srch_acc)
      )
      await self.binary_interpreter(
        node_id=int(axis), cmd="DC", cmd_index=0, cmd_type=CmdType.ValSet, value=str(srch_acc)
      )
    finally:
      # Stop any lingering motion/scripts
      await asyncio.sleep(0.3)
      await self.binary_interpreter(int(axis), "BG", 0, CmdType.Execute, value="0")
      await asyncio.sleep(0.3)

      # Restore Error Range to normal safety limits
      await self.binary_interpreter(int(axis), "ER", 3, CmdType.ValSet, str(int(max_pe)))

      # Force Motor Off to kill any zombie scripts holding the state machine
      # if self.move_error_code != 0: # Only if we had an error
      #   await self.binary_interpreter(int(axis), "MO", 0, eCmdType.ValSet, "0")

  async def motor_index_search(
    self, axis: KX2Axis, srch_vel: int, srch_acc: int, positive_direction: bool, timeout: float
  ) -> Tuple[int, int]:
    """Returns (OneRevolution, CapturedPosition)."""
    await self.binary_interpreter(int(axis), "HM", 1, CmdType.ValSet, "0")

    rev = await self.binary_interpreter(int(axis), "CA", 18, CmdType.ValQuery)
    one_revolution = int(float(rev))
    if not positive_direction:
      one_revolution *= -1

    await self.binary_interpreter(int(axis), "PR", 1, CmdType.ValSet, str(one_revolution))
    await self.binary_interpreter(int(axis), "SP", 0, CmdType.ValSet, str(srch_vel))
    await self.binary_interpreter(int(axis), "AC", 0, CmdType.ValSet, str(srch_acc))
    await self.binary_interpreter(int(axis), "DC", 0, CmdType.ValSet, str(srch_acc))

    await self.binary_interpreter(int(axis), "HM", 3, CmdType.ValSet, "3")  # Index only
    await self.binary_interpreter(int(axis), "HM", 4, CmdType.ValSet, "0")
    await self.binary_interpreter(int(axis), "HM", 5, CmdType.ValSet, "0")
    await self.binary_interpreter(int(axis), "HM", 2, CmdType.ValSet, "0")
    await self.binary_interpreter(int(axis), "HM", 1, CmdType.ValSet, "1")  # Arm

    self._waiting_moves = {axis: asyncio.get_event_loop().create_future()}
    await self.binary_interpreter(int(axis), "BG", 0, CmdType.Execute)
    await self._wait_for_moves_done(timeout)

    left = await self.binary_interpreter(int(axis), "HM", 1, CmdType.ValQuery)
    if left != 0:
      raise RuntimeError("Homing Failure: Failed to finish index pulse search.")

    cap = await self.binary_interpreter(int(axis), "HM", 7, CmdType.ValQuery)
    captured_position = int(float(cap))

    return one_revolution, captured_position

  async def motor_reset_encoder_position(self, axis: KX2Axis, position: float) -> None:
    await self.binary_interpreter(int(axis), "HM", 1, CmdType.ValSet, "0")
    await self.binary_interpreter(int(axis), "HM", 3, CmdType.ValSet, "0")
    await self.binary_interpreter(int(axis), "HM", 4, CmdType.ValSet, "0")
    await self.binary_interpreter(int(axis), "HM", 5, CmdType.ValSet, "0")
    await self.binary_interpreter(int(axis), "HM", 2, CmdType.ValSet, str(position))
    await self.binary_interpreter(int(axis), "HM", 1, CmdType.ValSet, "1")

  async def motor_set_homed_status(self, axis: KX2Axis, status: HomeStatus) -> None:
    val = "0"
    if status == HomeStatus.Homed:
      val = "1"
    elif status == HomeStatus.InitializedWithoutHoming:
      val = "2"
    await self.binary_interpreter(int(axis), "UI", 3, CmdType.ValSet, val)

  async def motor_get_homed_status(self, node_id: int) -> HomeStatus:
    left = await self.binary_interpreter(node_id, "UI", 3, CmdType.ValQuery)
    if left == 1:
      return HomeStatus.Homed
    if left == 2:
      return HomeStatus.InitializedWithoutHoming
    return HomeStatus.NotHomed

  async def motor_check_if_move_done(self, node_id: int) -> bool:
    """Returns Done status. Raises error on fault."""
    ms_val = await self.binary_interpreter(node_id, "MS", 0, CmdType.ValQuery)

    if ms_val == 0:
      return True
    if ms_val == 1:
      mo_val = await self.binary_interpreter(node_id, "MO", 0, CmdType.ValQuery)
      if mo_val == 1:
        return True
      fault = await self.motor_get_fault(node_id)
      if fault is not None:
        raise RuntimeError(f"Motor Fault: {fault}")
      raise RuntimeError("Motor Fault (Unknown)")
    if ms_val == 2:
      return False

    return False

  async def motor_get_fault(self, axis: KX2Axis) -> Optional[str]:
    val = await self.binary_interpreter(int(axis), "MF", 0, CmdType.ValQuery)
    if val == 0:
      return None
    assert isinstance(val, int)

    faults: list[str] = []

    # Simple bit flags
    bit_msgs = {
      0x0001: "Motor Hall sensor feedback angle not found yet.",
      0x0004: "Feedback loss: no match between encoder and Hall location.",
      0x0008: "The peak current has been exceeded.",
      0x0010: "Inhibit.",
      0x0040: "Two digital Hall sensors were changed at the same time.",
      0x0080: "Speed tracking error.",
      0x0100: "Position tracking error.",
      0x0200: "Inconsistent drive database.",
      0x0400: "Too large a difference in ECAM table.",
      0x0800: "CAN heartbeat failure.",
      0x1000: "Servo drive fault.",
      0x010000: "Failed to find the electrical zero of the motor during startup.",
      0x020000: "Speed limit exceeded.",
      0x040000: "Drive CPU stack overflow.",
      0x080000: "Drive CPU exception.",
      0x200000: "Motor stuck.",
      0x400000: "Position limit exceeded.",
      0x20000000: "Cannot start motor.",
    }

    for bit, msg in bit_msgs.items():
      if val & bit:
        faults.append(msg)

    # 0x2000/0x4000/0x8000 triad
    b13 = bool(val & 0x2000)
    b14 = bool(val & 0x4000)
    b15 = bool(val & 0x8000)

    if (not b15) and (not b14) and b13:
      faults.append("Power supply under voltage.")
    if (not b15) and b14 and (not b13):
      faults.append("Power supply over voltage.")
    if b15 and (not b14) and b13:
      faults.append("Motor lead short circuit or faulty drive.")
    if b15 and b14 and (not b13):
      faults.append("Drive overheated.")

    if len(faults) == 0:
      return f"Unknown fault code: {val} (0x{val:08X})"
    return "  ".join(faults)

  async def control_word_set(self, node_id: int, value: int, sync: bool = True) -> None:
    val_bytes = value.to_bytes(2, byteorder="little")
    await self.can_write(COBType.RPDO1, node_id, val_bytes[0], val_bytes[1], data_length=2)
    if sync:
      await self.can_sync()

  async def _wait_for_moves_done(self, timeout: float) -> None:
    async def wait_for_move_done(axis: KX2Axis) -> None:
      try:
        await asyncio.wait_for(self._waiting_moves[axis], timeout=timeout)
      except asyncio.TimeoutError:
        pass

      # if not set on time, make a query
      await self.motor_check_if_move_done(axis)

    await asyncio.gather(*(wait_for_move_done(axis) for axis in self._waiting_moves.keys()))

  async def _motors_move_start(
    self,
    axes: List[KX2Axis],
    *,
    relative: bool = False,
  ) -> None:
    # Create futures to wait on
    self._waiting_moves = {ax: asyncio.get_event_loop().create_future() for ax in axes}

    # send control word 47 to group; SYNC only on last
    relative_bit = 0x40 if relative else 0
    for i, nid in enumerate(axes):
      last = i == (len(axes) - 1)
      await self.control_word_set(nid, 47 + relative_bit, sync=last)

    # send control word 63 to group; SYNC only on last
    for i, nid in enumerate(axes):
      last = i == (len(axes) - 1)
      await self.control_word_set(nid, 47 + 0x10 + relative_bit, sync=last)

  async def motors_move_absolute_execute(self, plan: MotorsMovePlan) -> None:
    await self.pvt_select_mode(False)

    # Send per-axis parameters
    for move in plan.moves:
      await self.motor_set_move_direction(move.axis.value, move.direction)

      await self.can_sdo_download_elmo_object(
        node_id=move.axis.value,
        elmo_object_int=24698,
        sub_index=0,
        data=str(int(move.position)),
        data_type=ElmoObjectDataType.INTEGER32,
      )

      await self.can_sdo_download_elmo_object(
        node_id=move.axis.value,
        elmo_object_int=24705,
        sub_index=0,
        data=str(int(move.velocity)),
        data_type=ElmoObjectDataType.UNSIGNED32,
      )

      acc = max(int(move.acceleration), 100)
      await self.can_sdo_download_elmo_object(
        node_id=move.axis.value,
        elmo_object_int=24707,
        sub_index=0,
        data=str(acc),
        data_type=ElmoObjectDataType.UNSIGNED32,
      )
      await self.can_sdo_download_elmo_object(
        node_id=move.axis.value,
        elmo_object_int=24708,
        sub_index=0,
        data=str(acc),
        data_type=ElmoObjectDataType.UNSIGNED32,
      )

    await self._motors_move_start([move.axis for move in plan.moves])
    await self._wait_for_moves_done(timeout=plan.move_time + 2)


class KX2Backend:
  MOTION_AXES = (KX2Axis.SHOULDER, KX2Axis.Z, KX2Axis.ELBOW, KX2Axis.WRIST)

  def __init__(self):
    self.can = KX2Can()

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

  async def initialize(self):
    await self.can.connect()  # just to get the node IDs
    await self.drive_get_parameters(self.can.node_id_list)
    await self.can.connect_part_two()

    await asyncio.sleep(2)

    for axis in KX2Backend.MOTION_AXES:
      if self.unlimited_travel_ax[axis]:
        self.g_joint_move_direction[axis] = JointMoveDirection.ShortestWay

    for axis in KX2Backend.MOTION_AXES:
      try:
        await self.can.motor_enable(axis=axis, state=True)
      except Exception as e:
        print(f"Error enabling motor on axis {axis}: {e}")

    await self.servo_gripper_initialize()

  async def servo_gripper_initialize(self):
    try:
      await self.can.motor_enable(axis=KX2Axis.SERVO_GRIPPER, state=True)
    except Exception as e:
      print(f"Error enabling servo gripper motor on node {KX2Axis.SERVO_GRIPPER}: {e}")

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

    await self.can.home_motor(
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
      print(f"Servo Gripper Motor Status: {motor_status}")

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
        motor_fault = self.can.motor_get_fault(KX2Axis.SERVO_GRIPPER)
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
    for required_axis in KX2Backend.MOTION_AXES:
      if required_axis.value not in uis:
        raise CanError(f"Missing required axis with UI[4]={required_axis}")
    if 5 in uis:
      self.robot_on_rail = True
      warnings.warn("Rails has not been tested for KX2 robots.")
    if 6 in uis:
      self.has_servo_gripper = True

    # Pass 2: per-axis parameters
    for axis in nodes:
      print()
      print("axis", axis)

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
    if not r == 8438016:
      print("!!! not the same")
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
      print("node_id not int:", node_id, type(node_id))
      node_id = int(node_id)
    print(
      "motor send command",
      node_id,
      motor_command,
      index,
      value,
      val_type == ValType.Float,
      f"({val_type})",
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
      s = await self.can.os_interpreter(
        node_id=(node_id),
        cmd=motor_command,
        query=query_flag,
      )

      if cmd_type == CmdType.ValQuery:
        returned_data = s if s is not None else ""
    else:
      s = await self.can.binary_interpreter(
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
    raw = await self.can.motor_get_current_position(node_id=axis, pu=self.unlimited_travel_ax[axis])
    c = self.motor_conversion_factor_ax[axis]
    if axis == KX2Axis.ELBOW:
      return self.convert_elbow_angle_to_position(elbow_angle_deg=raw / c)
    else:
      if c == 0:
        print("node", axis, "has conversion factor of 0")
        return 0
      else:
        return raw / c

  async def read_input(self, axis: int, input_num: int) -> bool:
    return await self.can.read_input(node_id=axis, input_num=0x10 + input_num)

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
    for ax in axes:
      if self.unlimited_travel_ax[ax]:
        continue
      high = self.max_travel_ax[ax]
      low = self.min_travel_ax[ax]
      if target[ax] > high:
        if (target[ax] - high) < 0.1:
          target[ax] = high
        else:
          raise ValueError(f"Axis {ax!r} above max travel")
      if target[ax] < low:
        if (low - target[ax]) < 0.1:
          target[ax] = low
        else:
          raise ValueError(f"Axis {ax!r} below min travel")

    # Clearance check
    if KX2Axis.Z in axes:
      if (
        target[KX2Axis.Z] < self.min_travel_ax[KX2Axis.Z] + self.base_to_gripper_clearance_z
        and target[KX2Axis.ELBOW] < self.base_to_gripper_clearance_arm
      ):
        raise ValueError("Base-to-gripper clearance violated")

    # Determine current/start positions
    curr = await self.get_joint_position()

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
    plan = await self.calculate_move_abs_all_axes(
      cmd_pos=cmd_pos,
      cmd_vel_pct=cmd_vel_pct,
      cmd_accel_pct=cmd_accel_pct,
    )

    if plan is None:  # if every axis is skipped, exit
      return

    await self.can.motors_move_absolute_execute(plan)

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

  async def get_joint_position(self) -> Dict["KX2Axis", float]:
    return {
      KX2Axis.SHOULDER: await self.motor_get_current_position(KX2Axis.SHOULDER),
      KX2Axis.Z: await self.motor_get_current_position(KX2Axis.Z),
      KX2Axis.ELBOW: await self.motor_get_current_position(KX2Axis.ELBOW),
      KX2Axis.WRIST: await self.motor_get_current_position(KX2Axis.WRIST),
      KX2Axis.SERVO_GRIPPER: await self.motor_get_current_position(KX2Axis.SERVO_GRIPPER),
    }

  async def get_cartesian_position(self) -> GripperPose:
    current_joint_pos = await self.get_joint_position()
    cartesian = self.convert_joint_position_to_cartesian(current_joint_pos)
    return cartesian

  async def move_to_cartesian_position(
    self,
    pose: GripperPose,
    vel_pct: float = 100,
    accel_pct: float = 100,
  ) -> None:
    joint_pos = self.convert_cartesian_to_joint_position(pose)
    await self.motors_move_joint(
      cmd_pos=joint_pos,
      cmd_vel_pct=vel_pct,
      cmd_accel_pct=accel_pct,
    )

  async def activate_free_mode(self) -> None:
    for axis in KX2Backend.MOTION_AXES:
      await self.can.motor_enable(axis=axis, state=False)

  async def deactivate_free_mode(self) -> None:
    for axis in KX2Backend.MOTION_AXES:
      await self.can.motor_enable(axis=axis, state=True)
