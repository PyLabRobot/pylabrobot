"""Pure CANopen/Elmo protocol layer for the PAA KX2.

Constants, enums, frame dataclasses, and the EMCY decoder — no hardware, no
transport, no ``self``. Everything here is data + pure functions shared by
:class:`~pylabrobot.paa.kx2.kx2.KX2` and the config/kinematics modules.

Split out of the KX2 hardware class so the wire-protocol definitions can be
imported (and unit-tested) without pulling in the CAN transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Tuple


def _u32_le(value: int) -> List[int]:
  return list((value & 0xFFFFFFFF).to_bytes(4, byteorder="little", signed=False))


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


class _ElmoObjectDataType(IntEnum):
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


class _InputLogic(IntEnum):
  """Elmo SimplIQ IL[N] codes. Even = active-low; odd (value+1) = active-high."""
  GeneralPurpose = 0
  StopForward = 2
  StopReverse = 4
  BeginMotion = 6
  SoftStop = 8
  MainHomeEnable = 10
  AuxHomeEnable = 12
  StopUnderControl = 14
  AbortMotion = 16


class JointMoveDirection(IntEnum):
  """Move-direction hint used by the KX2's move primitives.

  Lives in the protocol layer (not the hardware class) because the
  `motor_set_move_direction` primitive consumes it to program Elmo's modulo
  mode register, and both config-reading and kinematics planning reference it.
  """

  Normal = 0
  Clockwise = 1
  Counterclockwise = 2
  ShortestWay = 3


@dataclass
class MotorMoveParam:
  """One axis of a coordinated move, expressed purely in node-ID terms."""

  # CANopen node ID for this axis. Callers pass `int(self.Axis.X)`.
  node_id: int
  position: int
  velocity: int       # encoder counts/sec (drive-internal; converted from mm/s or deg/s)
  acceleration: int   # encoder counts/sec^2
  relative: bool = False
  direction: JointMoveDirection = JointMoveDirection.ShortestWay


@dataclass
class MotorsMovePlan:
  moves: List[MotorMoveParam] = field(default_factory=list)
  move_time: float = 10.0


# Vendor-specific Elmo binary interpreter rides on PDO2 COB-IDs (non-standard).
# Request: RPDO2 = (6 << 7) | node_id  = 0x300 + node_id
# Response: TPDO2 = (5 << 7) | node_id = 0x280 + node_id
_BI_REQUEST_COB_BASE = 0x300
_BI_RESPONSE_COB_BASE = 0x280
_EMCY_COB_BASE = 0x80
_TPDO3_COB_BASE = 0x380
_GROUP_NODE_ID = 10


@dataclass
class EmcyFrame:
  """Parsed CANopen EMCY frame with Elmo manufacturer-specific fields.

  Layout matches the C# `sEmcy` struct (clscanmotor.cs:6966-6978):
  bytes [0..2) ErrCode (u16 LE), [2] ErrReg (u8), [3] ElmoErrCode (u8),
  [4..6) ErrCodeData1 (u16 LE), [6..8) ErrCodeData2 (u16 LE).
  """

  err_code: int
  err_reg: int
  elmo_err_code: int
  data1: int
  data2: int


@dataclass
class _NodeEmcyState:
  """All EMCY state we track for one drive node — single source of truth.

  Three flavors of consumer pull from this:
  - **Streaming runtime** reads ``queue_full`` / ``underflow`` to detect
    IPM backpressure. ``queue_low`` is a proactive warning (drive's IP
    buffer approaching empty); ``underflow`` is post-fact (drive ran the
    buffer dry and short-stopped the trajectory).
  - **Motion-poll path** reads ``move_error`` to raise on the next
    ``motor_check_if_move_done`` after a fault-class EMCY arrived. The
    string is preformatted with axis context.
  - **Diagnostics** read ``last_frame`` for the last raw EMCY received
    from this node.

  Reset (whole struct) on re-enable / fault clear / find_z post-IL-trip.
  """

  queue_low: bool = False
  queue_low_write_pointer: int = 0
  queue_low_read_pointer: int = 0
  queue_full: bool = False
  queue_full_failed_write_pointer: int = 0
  underflow: bool = False
  bad_head_pointer: bool = False
  bad_mode_init_data: bool = False
  motion_terminated: bool = False
  out_of_modulo: bool = False
  move_error: Optional[str] = None
  last_frame: Optional["EmcyFrame"] = None


# Standard CANopen error codes that don't depend on the Elmo byte.
# Source: clscanmotor.cs:1108-1267. The (description, disable_motors) tuple
# corresponds to (str1, flag1) in the C#.
_EMCY_STANDARD: Dict[int, Tuple[str, bool]] = {
  0x8110: ("CAN message lost (corrupted or overrun)", False),
  0x8200: ("Protocol error (unrecognized NMT request)", False),
  0x8210: ("Attempt to access an unconfigured RPDO", False),
  0x8130: ("Heartbeat event", False),
  0x6180: ("Fatal CPU error: stack overflow", False),
  0x6200: ("User program aborted by an error", False),
  0xFF01: ("Request by user program 'emit' function", False),
  0x6300: (
    "Object mapped to an RPDO returned an error during interpretation "
    "or a referenced motion failed to be performed",
    False,
  ),
  0x7300: ("Resolver or Analog Encoder feedback failed", True),
  0x7380: ("Feedback loss: no match between encoder and Hall locations.", True),
  0x8311: (
    "Peak current has been exceeded due to drive malfunction or badly-tuned "
    "current controller",
    True,
  ),
  0x5441: ("E-stop button was pressed", True),
  0x5280: ("ECAM table problem", False),
  0x7381: (
    "Two digital Hall sensors changed at once; only one sensor can be changed "
    "at a time",
    True,
  ),
  0x8480: ("Speed tracking error", True),
  0x8611: ("Position tracking error", True),
  0x6320: ("Cannot start due to inconsistent database", False),
  0x8380: ("Cannot find electrical zero of motor when attempting to start motor", False),
  0x8481: ("Speed limit exceeded", True),
  0x6181: ("CPU exception: fatal exception", False),
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
    "Over-voltage: power supply voltage is too high or servo driver could not "
    "absorb kinetic energy while braking a load",
    True,
  ),
  0x2340: (
    "Short circuit: motor or its wiring may be defective, or drive is faulty",
    True,
  ),
  0x4310: ("Temperature: drive overheating", True),
  0xFF20: ("Safety switch is sensed - Drive in safety state", True),
}


def _decode_emcy(
  frame: EmcyFrame, state: _NodeEmcyState
) -> Tuple[str, bool, bool]:
  """Decode an EMCY frame into (description, disable_motors, suppress_callback).

  Mutates ``state`` for IPM queue events. Mirrors clscanmotor.cs:1070-1267
  one-for-one. ``suppress_callback`` corresponds to the C# `flag2` and is only
  set for the elmo 0x8A interpolation underflow under errCode 0xFF02 — that
  case updates internal state but does not raise the user-visible event.
  """
  err = frame.err_code
  elmo = frame.elmo_err_code

  # DS301 §7.2.7.1: err_code=0 is the "no error / error reset" frame, emitted
  # on bootup and after a fault clears. Suppress the user callback — re-enable
  # is the explicit acknowledgment, not a spontaneous reset frame. The elmo
  # byte is ignored: any frame with err_code=0 is a reset regardless.
  if err == 0:
    return ("Error reset", False, True)

  if err == 0xFF00:
    if elmo == 0x56:
      state.queue_low = True
      state.queue_low_write_pointer = frame.data1
      state.queue_low_read_pointer = frame.data2
      return ("Queue Low", False, False)
    if elmo == 0x5B:
      state.bad_head_pointer = True
      return ("Bad Head Pointer", False, False)
    if elmo == 0x34:
      state.queue_full = True
      state.queue_full_failed_write_pointer = frame.data1
      return ("Queue Full", False, False)
    if elmo == 0x07:
      state.bad_mode_init_data = True
      return ("Bad Mode Init Data", False, False)
    if elmo == 0x08:
      state.motion_terminated = True
      return ("Motion Terminated", False, False)
    if elmo == 0xA6:
      state.out_of_modulo = True
      return ("Out Of Modulo", False, False)
    return (f"Unknown vendor EMCY 0xFF00/0x{elmo:02X}", False, False)

  if err == 0xFF02:
    if elmo == 0x07:
      state.bad_mode_init_data = True
      return ("Bad Mode Init Data", False, False)
    if elmo == 0x08:
      state.motion_terminated = True
      return ("Motion Terminated", False, False)
    if elmo == 0x34:
      state.queue_full = True
      state.queue_full_failed_write_pointer = frame.data1
      return ("Queue Full", False, False)
    if elmo == 0x56:
      state.queue_low = True
      state.queue_low_write_pointer = frame.data1
      state.queue_low_read_pointer = frame.data2
      return ("Queue Low", False, False)
    if elmo == 0x5B:
      state.bad_head_pointer = True
      return ("Bad Head Pointer", False, False)
    if elmo == 0x8A:
      # State update only; user callback suppressed (C# flag2=true). Sets
      # `underflow` (post-fact: drive already ran out of points) — distinct
      # from `queue_low` (proactive: more data needed soon).
      state.underflow = True
      return ("Position Interpolation buffer underflow", False, True)
    if elmo == 0xA6:
      state.out_of_modulo = True
      return ("Out Of Modulo", False, False)
    if elmo == 0xBA:
      state.queue_full = True
      return ("Interpolation queue is full", False, False)
    if elmo == 0xBB:
      return ("Incorrect interpolation sub-mode", False, False)
    return (f"DS402 IP Error 0x{elmo:02X}", False, False)

  std = _EMCY_STANDARD.get(err)
  if std is not None:
    desc, disable = std
    return (desc, disable, False)

  return (f"Unknown EMCY 0x{err:04X}/0x{elmo:02X}", False, False)
