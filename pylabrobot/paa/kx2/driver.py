"""Low-level CAN transport + CANopen/DS402 drive primitives for the PAA KX2.

Uses the `canopen` library (python-can bus + CANopen SDO/PDO/NMT/EMCY).
Paired with :class:`KX2ArmBackend` in ``arm_backend.py`` via the standard
``Device`` + ``Driver`` + capability-backend split.

This module is purely a CAN transport + Elmo interpreter layer. It knows only
CANopen node IDs (ints). All axis-level / robot-topology concepts (axis names,
motion-axis tuples, home status, move plans, joint-move direction, homing
sequences) live in ``arm_backend``.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Optional, Tuple, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver

try:
  import canopen

  _HAS_CANOPEN = True
except ImportError as _e:
  _HAS_CANOPEN = False
  _CANOPEN_IMPORT_ERROR = _e


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
  """Move-direction hint used by the driver's move primitives.

  Lives here (not in the backend) because the driver's
  `motor_set_move_direction` primitive consumes it to program Elmo's modulo
  mode register. Backend-side planning also uses it, but the canonical
  definition is the driver's.
  """

  Normal = 0
  Clockwise = 1
  Counterclockwise = 2
  ShortestWay = 3


@dataclass
class MotorMoveParam:
  """One axis of a coordinated move, expressed purely in node-ID terms."""

  # CANopen node ID for this axis. Backend passes `int(self.Axis.X)`.
  node_id: int
  position: int
  velocity: int       # encoder counts/sec (driver-internal; backend converts from mm/s or deg/s)
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

logger = logging.getLogger(__name__)


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
class _PvtEmcyState:
  """Per-node PVT-mode queue state, mirrored from `sPVT_EMCY` in clscanmotor.cs:6943-6964."""

  queue_low: bool = False
  queue_low_write_pointer: int = 0
  queue_low_read_pointer: int = 0
  queue_full: bool = False
  queue_full_failed_write_pointer: int = 0
  bad_head_pointer: bool = False
  bad_mode_init_data: bool = False
  motion_terminated: bool = False
  out_of_modulo: bool = False


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
  frame: EmcyFrame, state: _PvtEmcyState
) -> Tuple[str, bool, bool]:
  """Decode an EMCY frame into (description, disable_motors, suppress_callback).

  Mutates ``state`` for PVT queue events. Mirrors clscanmotor.cs:1070-1267
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
      # State update only; user callback suppressed (C# flag2=true).
      state.queue_low = True
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


class KX2Driver(Driver):
  """CANopen-library-backed KX2 drive transport.

  Uses `canopen.Network` for bus ownership + NMT, `node.sdo` for SDO traffic,
  raw SDO writes to 0x14xx/0x16xx/0x18xx/0x1Axx for PDO mapping, and
  `network.send_message` / `network.subscribe` for the vendor-specific Elmo
  binary interpreter (non-standard, rides on TPDO2/RPDO2 COB-IDs).

  Pure CAN transport + Elmo interpreter primitives — takes node IDs (ints),
  knows nothing about robot topology. Axis-level concepts live in
  :class:`KX2ArmBackend`.
  """

  def __init__(
    self,
    has_rail: bool = False,
    has_servo_gripper: bool = True,
    interface: str = "pcan",
    channel: Optional[str] = None,
    bitrate: int = 500000,
  ) -> None:
    # The non-default topologies (rail-mounted KX2, gripper-less KX2)
    # have shim code paths in this driver and the backend, but neither
    # has been exercised against real hardware. KX2ArmBackend._on_setup
    # also calls servo_gripper_initialize unconditionally. Refuse the
    # configuration up front rather than letting users hit cryptic
    # failures downstream.
    if has_rail or not has_servo_gripper:
      raise NotImplementedError(
        "KX2 has only been tested with the default 4-axis arm + servo "
        "gripper topology (has_rail=False, has_servo_gripper=True). "
        "Other configurations have shim code paths but the setup / "
        "homing layer needs work — see KX2ArmBackend._on_setup and "
        "servo_gripper_initialize."
      )

    super().__init__()
    self._interface = interface
    self._channel = channel
    self._bitrate = bitrate

    self.has_rail = has_rail
    self.has_servo_gripper = has_servo_gripper

    self.node_id_list: List[int] = [1, 2, 3, 4]
    if has_rail:
      self.node_id_list.append(5)
    if has_servo_gripper:
      self.node_id_list.append(6)

    # Motion axes = shoulder/Z/elbow/wrist. Driver only knows node IDs;
    # axis-level names live in the backend's KX2ArmBackend.Axis enum.
    self.motion_node_ids: List[int] = [1, 2, 3, 4]

    self._network: Optional[canopen.Network] = None
    self._nodes: Dict[int, canopen.RemoteNode] = {}
    self._loop: Optional[asyncio.AbstractEventLoop] = None

    # Pending binary-interpreter response futures keyed by
    # (node_id, msg_type, msg_index). Set from the canopen listener thread
    # via loop.call_soon_threadsafe; only the event-loop thread touches
    # this dict directly.
    self._pending_bi: Dict[Tuple[int, str, int], asyncio.Future] = {}

    self._pvt_mode: bool = False

    # EMCY (CANopen Emergency, COB-ID 0x80 + node_id) state. Subscribed in
    # setup(); fires on the canopen listener thread, marshalled into the
    # asyncio loop via _make_emcy_callback.
    self.emcy_move_error_received: bool = False
    self.emcy_move_error: str = ""
    self.emcy_move_error_node_id: Optional[int] = None
    self.last_emcy: Optional[EmcyFrame] = None
    self._pvt_emcy: Dict[int, _PvtEmcyState] = {}
    self._emcy_callbacks: List[
      Callable[[int, EmcyFrame, str, bool], None]
    ] = []

    # StatusWord (0x6041) push-cache from TPDO3, COB-ID 0x380+node_id. The
    # canopen listener thread parses the 2-byte SW out of each TPDO3 frame and
    # marshals (sw, set_event) onto the asyncio loop. _wait_setpoint_ack reads
    # the cache + waits on the event instead of polling 0x6041 via SDO.
    self._statusword: Dict[int, int] = {}
    self._statusword_event: Dict[int, asyncio.Event] = {}

  @property
  def loop(self) -> asyncio.AbstractEventLoop:
    """Event loop captured in setup(). Raises if accessed before setup()."""
    if self._loop is None:
      raise RuntimeError("KX2Driver event loop not initialized; call setup() first.")
    return self._loop

  # --- lifecycle -----------------------------------------------------------

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Bring up the CAN bus, reset/start nodes, and configure PDO mapping."""
    if not _HAS_CANOPEN:
      raise ImportError(
        "canopen is not installed. Install with `pip install pylabrobot[canopen]` "
        f"(import error: {_CANOPEN_IMPORT_ERROR})"
      )
    if self._network is not None:
      await self.stop()

    self._loop = asyncio.get_running_loop()

    network = canopen.Network()
    network.connect(interface=self._interface, channel=self._channel, bitrate=self._bitrate)
    self._network = network

    # Subscribe to EMCY before Start All Nodes — bootup / fault frames
    # emitted between NMT start and per-node setup would otherwise be lost
    # (canopen's listener doesn't buffer pre-subscribe messages). Mirrors
    # the C# event handler at KX2RobotControl.cs:15384-15425 /
    # clscanmotor.cs:1057-1284.
    for nid in self.node_id_list:
      self._pvt_emcy[nid] = _PvtEmcyState()
      network.subscribe(_EMCY_COB_BASE + nid, self._make_emcy_callback(nid))

    # Reset all nodes, then start scanner so bootup messages populate it,
    # then start all nodes.
    network.nmt.send_command(0x82)
    await asyncio.sleep(0.5)
    network.scanner.search()
    network.nmt.send_command(0x01)
    await asyncio.sleep(0.5)

    discovered = sorted(network.scanner.nodes)
    if discovered != self.node_id_list:
      raise CanError(
        f"Node IDs on CAN bus do not match expected list: "
        f"{discovered} != {self.node_id_list}"
      )

    for nid in self.node_id_list:
      node = network.add_node(nid, canopen.ObjectDictionary())
      # canopen's default SDO response timeout is 0.3s, which is tight for
      # drives that queue vendor objects (Elmo 0x20xx/0x30xx). Match the 1s
      # the legacy driver used for its own futures.
      node.sdo.RESPONSE_TIMEOUT = 1.0
      self._nodes[nid] = node
      # Elmo binary-interpreter response subscription. BI traffic only
      # happens after explicit user commands, so subscribing here is fine.
      network.subscribe(_BI_RESPONSE_COB_BASE + nid, self._make_bi_callback(nid))

    logger.info("canopen: connected, nodes=%s", discovered)

    # TPDO3 push for StatusWord (0x6041) so _wait_setpoint_ack can wait on
    # the bit-12 transition without an SDO round-trip per poll. Subscribe
    # before remapping so we don't lose the first frame the drive emits
    # when the new event-trigger arms. 1 ms inhibit (10 * 100 us) caps
    # bus traffic — SW changes happen at the ~1-2 ms servo cycle, so the
    # inhibit doesn't lose edges in practice and keeps the bus quiet
    # during PVT streaming if anyone resurrects the IPM runtime.
    for nid in self.motion_node_ids:
      self._statusword_event[nid] = asyncio.Event()
      network.subscribe(_TPDO3_COB_BASE + nid, self._make_tpdo3_callback(nid))

    # Unmap TPDO1, map TPDO3 (StatusWord, triggered on any SW change) and
    # TPDO4 (DigitalInputs, triggered on edge).
    for node_id in self.node_id_list:
      await self._can_tpdo_unmap(TPDO.TPDO1, node_id)
      await self._tpdo_map(
        TPDO.TPDO3, node_id, [TPDOMappedObject.StatusWord],
        TPDOTrigger.StatusWordEvent, delay_100_us=10,
      )
      await self._tpdo_map(
        TPDO.TPDO4, node_id, [TPDOMappedObject.DigitalInputs], TPDOTrigger.DigitalInputEvent
      )

    # Elmo vendor objects: interpolation config for PVT mode.
    for nid in self.motion_node_ids:
      await self.can_sdo_download_elmo_object(nid, 24768, 0, -1, _ElmoObjectDataType.INTEGER16)
      await self.can_sdo_download_elmo_object(nid, 24772, 2, 16, _ElmoObjectDataType.UNSIGNED32)
      await self.can_sdo_download_elmo_object(nid, 24772, 3, 0, _ElmoObjectDataType.UNSIGNED8)
      await self.can_sdo_download_elmo_object(nid, 24772, 5, 8, _ElmoObjectDataType.UNSIGNED8)
      await self.can_sdo_download_elmo_object(nid, 24770, 2, -3, _ElmoObjectDataType.INTEGER8)
      await self.can_sdo_download_elmo_object(nid, 24669, 0, 1, _ElmoObjectDataType.INTEGER16)

    # RPDO1 = ControlWord (for DS402 enable), RPDO3 = interpolated target.
    for nid in self.motion_node_ids:
      await self._rpdo_map(
        RPDO.RPDO1, nid, [RPDOMappedObject.ControlWord],
        PDOTransmissionType.SynchronousCyclic,
      )
      await self._rpdo_map(
        RPDO.RPDO3, nid,
        [RPDOMappedObject.TargetPositionIP, RPDOMappedObject.TargetVelocityIP],
        PDOTransmissionType.EventDrivenDev,
      )

    self._pvt_mode = True
    await self.pvt_select_mode(False)

  async def stop(self) -> None:
    if self._network is not None:
      # Drop _loop first so racing listener-thread _cb()s no-op at their
      # `if self._loop is None: return` guard before they try to schedule
      # onto a torn-down loop. Network reference clears after disconnect.
      self._loop = None
      self._network.disconnect()
      self._network = None
      self._nodes = {}
      self._pvt_emcy = {}
      # Clear callbacks too: _on_setup re-registers on each retry, so leaving
      # them would compound N copies of the same handler across attempts.
      self._emcy_callbacks = []
      self.emcy_move_error_received = False
      self.emcy_move_error = ""
      self.emcy_move_error_node_id = None
      self.last_emcy = None
      self._statusword = {}
      self._statusword_event = {}

  # --- PDO configuration (pure SDO writes; no library-PDO machinery) ------

  async def _can_tpdo_unmap(self, tpdo: TPDO, node_id: int) -> None:
    cob_type_int = {
      TPDO.TPDO1: COBType.TPDO1.value,
      TPDO.TPDO3: COBType.TPDO3.value,
      TPDO.TPDO4: COBType.TPDO4.value,
    }[tpdo]
    node_id &= 0x7F
    num1 = ((cob_type_int & 0x01) << 7) | node_id
    num2 = (cob_type_int >> 1) & 0x07
    await self._can_sdo_download(node_id, 0x1800 + tpdo.value - 1, 1, [num1, num2, 0, 0xC0])
    await self._can_sdo_download(node_id, 0x1A00 + tpdo.value - 1, 0, [0, 0, 0, 0])

  async def _rpdo_map(
    self,
    rpdo: RPDO,
    node_id: int,
    mapped_objects: List[RPDOMappedObject],
    transmission_type: PDOTransmissionType,
  ) -> None:
    rpdo_idx = (int(rpdo) - 1) & 0xFF
    cob_type = {
      RPDO.RPDO1: COBType.RPDO1, RPDO.RPDO3: COBType.RPDO3, RPDO.RPDO4: COBType.RPDO4,
    }[rpdo]
    cob_id_11 = ((int(cob_type) & 0x0F) << 7) | (node_id & 0x7F)

    # Disable PDO (bit 31 set)
    await self._can_sdo_download(node_id, 0x1400 + rpdo_idx, 1, _u32_le(0x80000000 | cob_id_11))
    # Clear mapping count
    await self._can_sdo_download(node_id, 0x1600 + rpdo_idx, 0, [0, 0, 0, 0])
    # Transmission type
    await self._can_sdo_download(
      node_id, 0x1400 + rpdo_idx, 2, [int(transmission_type) & 0xFF, 0, 0, 0]
    )
    # Mapped objects
    for i, mo in enumerate(mapped_objects):
      await self._can_sdo_download(node_id, 0x1600 + rpdo_idx, i + 1, _u32_le(int(mo)))
    # Mapping count
    await self._can_sdo_download(
      node_id, 0x1600 + rpdo_idx, 0, [len(mapped_objects) & 0xFF, 0, 0, 0]
    )
    # Re-enable (clear bit 31)
    await self._can_sdo_download(node_id, 0x1400 + rpdo_idx, 1, _u32_le(cob_id_11))

  async def _tpdo_map(
    self,
    tpdo: TPDO,
    node_id: int,
    mapped_objects: List[TPDOMappedObject],
    event_trigger: TPDOTrigger,
    event_timer_ms: int = 0,
    delay_100_us: int = 0,
    transmission_type: PDOTransmissionType = PDOTransmissionType.EventDrivenDev,
  ) -> None:
    tpdo_idx = (int(tpdo) - 1) & 0xFF
    cob_type = {
      TPDO.TPDO1: COBType.TPDO1, TPDO.TPDO3: COBType.TPDO3, TPDO.TPDO4: COBType.TPDO4,
    }[tpdo]
    cob_id_11 = ((int(cob_type) & 0x0F) << 7) | (node_id & 0x7F)
    event_mask = 1 << int(event_trigger)

    # Disable TPDO (bit 30 + 31)
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 1, _u32_le(0xC0000000 | cob_id_11))
    # Clear mapping count
    await self._can_sdo_download(node_id, 0x1A00 + tpdo_idx, 0, [0, 0, 0, 0])
    # Transmission type
    await self._can_sdo_download(
      node_id, 0x1800 + tpdo_idx, 2, [int(transmission_type) & 0xFF, 0, 0, 0]
    )
    # Inhibit / delay 100us
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 3, [delay_100_us & 0xFF, 0, 0, 0])
    # Event timer (ms)
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 5, [event_timer_ms & 0xFF, 0, 0, 0])
    # Vendor event mask at 0x2F20:<tpdo_num>
    await self._can_sdo_download(node_id, 0x2F20, int(tpdo) & 0xFF, _u32_le(event_mask))
    # Mapped objects
    for i, mo in enumerate(mapped_objects):
      await self._can_sdo_download(node_id, 0x1A00 + tpdo_idx, i + 1, _u32_le(int(mo)))
    # Mapping count
    await self._can_sdo_download(
      node_id, 0x1A00 + tpdo_idx, 0, [len(mapped_objects) & 0xFF, 0, 0, 0]
    )
    # Re-enable (clear bits 30 + 31)
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 1, _u32_le(cob_id_11))

  # --- SDO -----------------------------------------------------------------

  async def _can_sdo_upload(
    self, node_id: int, index: int, sub_index: int,
  ) -> bytes:
    # node.sdo.upload is blocking I/O (library handles expedited + segmented
    # transfers + abort codes); run off the event loop.
    return await asyncio.to_thread(self._nodes[node_id].sdo.upload, index, sub_index)

  async def _can_sdo_download(
    self, node_id: int, index: int, sub_index: int, data: List[int],
  ) -> None:
    await asyncio.to_thread(
      self._nodes[node_id].sdo.download, index, sub_index, bytes(data),
    )

  async def can_sdo_download_elmo_object(
    self,
    node_id: int,
    elmo_object_int: int,
    sub_index: int,
    data: Union[int, float],
    data_type: _ElmoObjectDataType,
  ) -> None:
    # Byte width + signedness derived from data_type; float inputs truncate to int.
    _SDO_ELMO_PACK = {
      _ElmoObjectDataType.UNSIGNED8:  (1, False),
      _ElmoObjectDataType.UNSIGNED16: (2, False),
      _ElmoObjectDataType.UNSIGNED32: (4, False),
      _ElmoObjectDataType.UNSIGNED64: (8, False),
      _ElmoObjectDataType.INTEGER8:   (1, True),
      _ElmoObjectDataType.INTEGER16:  (2, True),
      _ElmoObjectDataType.INTEGER32:  (4, True),
      _ElmoObjectDataType.INTEGER64:  (8, True),
    }
    spec = _SDO_ELMO_PACK.get(data_type)
    if spec is None:
      raise CanError(f"Unsupported data type for SDO Write: {data_type.name}")
    width, signed = spec
    data_bytes = list(int(data).to_bytes(width, "little", signed=signed))
    await self._can_sdo_download(node_id, elmo_object_int, sub_index, data_bytes)

  # --- EMCY (CANopen Emergency, COB-ID 0x80 + node_id) --------------------

  def add_emcy_callback(
    self, cb: Callable[[int, EmcyFrame, str, bool], None]
  ) -> None:
    """Register a callback fired on every (non-suppressed) EMCY frame.

    Callback signature: ``cb(node_id, frame, description, disable_motors)``.
    Always invoked on the asyncio loop thread captured in :meth:`setup`.
    Exceptions raised by the callback are logged and swallowed so one bad
    handler can't poison the rest.
    """
    self._emcy_callbacks.append(cb)

  def clear_emcy_state(self, node_id: Optional[int] = None) -> None:
    """Clear sticky EMCY error fields after a recovery / re-enable.

    If ``node_id`` is given, also resets the per-node PVT queue state for
    that node. Mirrors clscanmotor.cs:668-669, 3454, 4481.
    """
    self.emcy_move_error_received = False
    self.emcy_move_error = ""
    self.emcy_move_error_node_id = None
    if node_id is not None and node_id in self._pvt_emcy:
      self._pvt_emcy[node_id] = _PvtEmcyState()

  def _make_emcy_callback(self, node_id: int):
    """Return a `canopen.Network.subscribe` callback bound to a specific node."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      # Fires on canopen's listener thread. Marshal decoding into the loop.
      if self._loop is None:
        return
      self._loop.call_soon_threadsafe(self._dispatch_emcy, node_id, bytes(data))

    return _cb

  def _dispatch_emcy(self, node_id: int, data: bytes) -> None:
    if len(data) < 8:
      logger.warning("EMCY frame too short from node %d: %s", node_id, data.hex())
      return
    err_code, err_reg, elmo_err, d1, d2 = struct.unpack("<HBBHH", data[:8])
    frame = EmcyFrame(err_code, err_reg, elmo_err, d1, d2)
    self.last_emcy = frame

    state = self._pvt_emcy.setdefault(node_id, _PvtEmcyState())
    desc, disable_motors, suppress = _decode_emcy(frame, state)
    # Tier the level so PVT housekeeping (queue-low/underflow) doesn't drown
    # ops logs while real faults stay loud. Unknown codes warn so we notice.
    if disable_motors:
      level = logging.ERROR
    elif desc.startswith(("Unknown EMCY", "Unknown vendor EMCY", "DS402 IP Error")):
      level = logging.WARNING
    elif suppress:
      level = logging.DEBUG
    else:
      level = logging.INFO
    logger.log(
      level,
      "EMCY node=%d code=0x%04X reg=0x%02X elmo=0x%02X d1=0x%04X d2=0x%04X: %s",
      node_id, err_code, err_reg, elmo_err, d1, d2, desc,
    )

    if disable_motors:
      self.emcy_move_error_received = True
      self.emcy_move_error = desc
      self.emcy_move_error_node_id = node_id

    if suppress:
      return

    for cb in list(self._emcy_callbacks):
      try:
        cb(node_id, frame, desc, disable_motors)
      except Exception:
        logger.exception("EMCY user callback raised; continuing")

  def _make_tpdo3_callback(self, node_id: int):
    """Return a callback that decodes TPDO3 (StatusWord) and signals waiters."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      if self._loop is None:
        return
      if len(data) < 2:
        # StatusWord is 2 bytes — a shorter frame means the drive's TPDO3
        # mapping is wrong or a different sender is squatting on the COB-ID.
        # _wait_setpoint_ack would silently fall back to SDO probing forever.
        logger.warning(
          "TPDO3 frame too short from node %d: %s (expected >=2 bytes)",
          node_id, bytes(data).hex(),
        )
        return
      sw = int.from_bytes(bytes(data[:2]), "little")
      self._loop.call_soon_threadsafe(self._dispatch_statusword, node_id, sw)

    return _cb

  def _dispatch_statusword(self, node_id: int, sw: int) -> None:
    self._statusword[node_id] = sw
    ev = self._statusword_event.get(node_id)
    if ev is not None:
      ev.set()

  # --- Elmo binary interpreter (vendor protocol on TPDO2/RPDO2) ------------

  def _make_bi_callback(self, node_id: int):
    """Return a `canopen.Network.subscribe` callback bound to a specific node."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      # Fires on canopen's listener thread. Marshal decoding into the loop.
      if self._loop is None:
        return
      self._loop.call_soon_threadsafe(self._dispatch_bi_response, node_id, bytes(data))

    return _cb

  def _dispatch_bi_response(self, node_id: int, data: bytes) -> None:
    if len(data) < 8:
      logger.warning("Binary interpreter response too short from node %d: %s", node_id, data.hex())
      return
    msg_type = chr(data[0]) + chr(data[1])
    msg_index = ((data[3] & 0x3F) << 8) | data[2]
    is_int = (data[3] & 0x80) == 0
    fmt = "<i" if is_int else "<f"
    (val,) = struct.unpack(fmt, data[4:8])

    fut = self._pending_bi.pop((node_id, msg_type, msg_index), None)
    if fut is not None and not fut.done():
      fut.set_result(val)  # native int or float, no stringification

  async def _send_bi(
    self,
    node_id: int,
    cmd: str,
    cmd_index: int,
    *,
    is_query: bool,
    is_execute: bool,
    is_float: bool,
    value: Union[int, float] = 0,
  ) -> List[Union[int, float]]:
    """Frame + send an 8-byte binary-interpreter request; await one response
    per target node. Each response is decoded to its native type (int or
    float) by :meth:`_dispatch_bi_response`.
    """
    if self._network is None:
      raise CanError("binary interpreter called before setup()")

    timeout = 10.0 if cmd.upper() == "SV" else 1.0

    byte0 = ord(cmd[0]) & 0xFF
    byte1 = ord(cmd[-1]) & 0xFF
    byte2 = cmd_index & 0xFF
    byte3 = (cmd_index >> 8) & 0x3F
    if is_query:
      byte3 |= 0x40
    if is_float:
      byte3 |= 0x80

    val_bytes = (
      struct.pack("<f", float(value)) if is_float
      else struct.pack("<i", int(value))
    )
    payload = bytes([byte0, byte1, byte2, byte3]) + val_bytes
    data_to_send = payload[:4] if is_execute else payload

    targets = (
      list(self.motion_node_ids) if node_id == _GROUP_NODE_ID else [node_id]
    )

    futures: List[asyncio.Future] = []
    for nid in targets:
      key = (nid, cmd, cmd_index)
      # If a stale pending future exists for the same (node, cmd, index), drop it.
      old = self._pending_bi.pop(key, None)
      if old is not None and not old.done():
        old.cancel()
      fut = self.loop.create_future()
      self._pending_bi[key] = fut
      futures.append(fut)

    self._network.send_message(_BI_REQUEST_COB_BASE + node_id, data_to_send)

    try:
      return await asyncio.wait_for(asyncio.gather(*futures), timeout=timeout)
    except asyncio.TimeoutError:
      for nid in targets:
        self._pending_bi.pop((nid, cmd, cmd_index), None)
      raise CanError(
        f"Timeout waiting for response to {cmd}[{cmd_index}] from node {node_id}"
      )

  async def query_int(self, node_id: int, cmd: str, cmd_index: int) -> int:
    """Query an int-typed Elmo parameter. Returns the drive's current value."""
    if node_id == _GROUP_NODE_ID:
      raise CanError("Group queries are not supported")
    resps = await self._send_bi(
      node_id, cmd, cmd_index, is_query=True, is_execute=False, is_float=False,
    )
    return int(resps[0])

  async def query_float(self, node_id: int, cmd: str, cmd_index: int) -> float:
    """Query a float-typed Elmo parameter. Returns the drive's current value."""
    if node_id == _GROUP_NODE_ID:
      raise CanError("Group queries are not supported")
    resps = await self._send_bi(
      node_id, cmd, cmd_index, is_query=True, is_execute=False, is_float=True,
    )
    return float(resps[0])

  async def write(
    self, node_id: int, cmd: str, cmd_index: int, value: Union[int, float],
  ) -> None:
    """Write an Elmo parameter. The type of ``value`` selects int vs float
    framing on the wire. The drive echoes the accepted value back, which we
    verify — a mismatch raises :class:`CanError`.
    """
    is_float = isinstance(value, float)
    resps = await self._send_bi(
      node_id, cmd, cmd_index,
      is_query=False, is_execute=False, is_float=is_float, value=value,
    )
    targets = (
      list(self.motion_node_ids) if node_id == _GROUP_NODE_ID else [node_id]
    )
    for nid, resp in zip(targets, resps):
      if is_float:
        # Elmo stores floats as float32; the echo may drift slightly relative
        # to our float64 input — accept within ~1% ratio.
        exp, act = float(value), float(resp)
        ok = exp == act or (act != 0.0 and 0.99 < exp / act < 1.01)
      else:
        ok = int(resp) == int(value)
      if not ok:
        raise CanError(
          f"Unexpected CAN response: sent {cmd}[{cmd_index}]={value}, "
          f"got {resp} from node {nid}"
        )

  async def execute(self, node_id: int, cmd: str, cmd_index: int = 0) -> None:
    """Fire-and-forget execute (e.g. ``BG``). Awaits the drive's response so
    the caller sees the command completed on the wire, but no echo-check."""
    await self._send_bi(
      node_id, cmd, cmd_index, is_query=False, is_execute=True, is_float=False,
    )

  async def _os_interpreter(
    self,
    node_id: int,
    cmd: str,
    *,
    query: bool = False,
  ) -> str:
    """Run an OS interpreter command via standard CiA-301 OS Command objects.

    Uses 0x1024 (OS Command Mode) + 0x1023 (OSCommand record) — the library
    handles the expedited vs. segmented SDO choice and toggle-bit dance
    automatically, replacing ~260 lines of hand-rolled segmented SDO in the
    legacy driver.
    """
    if node_id not in self._nodes:
      raise CanError(f"os_interpreter: unknown node {node_id}")
    node = self._nodes[node_id]

    # 0x1024:0 = OS Command Mode. Elmo/legacy code sets this to 0 ("evaluate
    # immediately") before each command.
    await asyncio.to_thread(node.sdo.download, 0x1024, 0, bytes([0]))

    # 0x1023:1 = OSCommand.Command. ASCII-encoded; library segments if >4 bytes.
    await asyncio.to_thread(node.sdo.download, 0x1023, 1, cmd.encode("ascii"))

    # 0x1023:2 = OSCommand.Status (U8). This is the CiA-301 OS-command lifecycle
    # byte, not an error flag:
    #   0x00 no reply yet / no error   0x01 command is being executed
    #   0x02 completed, no reply       0x03 completed with reply
    #   0xFF no command
    # For async `XQ##` dispatches the drive returns 0x01 immediately, which is
    # expected — the caller (e.g. `user_program_run`) polls PS/UI afterward for
    # completion. SDO abort codes surface as `SdoAbortedError` from the upload
    # itself; we don't need to inspect the byte. Log at debug for diagnostics.
    status_bytes = await asyncio.to_thread(node.sdo.upload, 0x1023, 2)
    logger.debug(
      "os_interpreter node=%d cmd=%r status=0x%02X",
      node_id, cmd, int.from_bytes(status_bytes[:1], "little"),
    )

    if not query:
      return ""

    # 0x1023:3 = OSCommand.Reply (DOMAIN / string). Library handles segmented.
    reply: bytes = await asyncio.to_thread(node.sdo.upload, 0x1023, 3)
    return reply.decode("ascii", errors="replace").rstrip("\x00").rstrip()

  # --- raw CANopen sends (SYNC + RPDO1 controlword) -----------------------

  async def _can_sync(self) -> None:
    if self._network is None:
      raise CanError("_can_sync called before setup()")
    # SYNC object (0x080), no data.
    self._network.send_message(0x80, b"")

  async def _control_word_set(self, node_id: int, value: int, sync: bool = True) -> None:
    if self._network is None:
      raise CanError("_control_word_set called before setup()")
    val_bytes = value.to_bytes(2, byteorder="little")
    # RPDO1 COB-ID = (4 << 7) | node_id = 0x200 + node_id
    self._network.send_message(0x200 + node_id, val_bytes)
    if sync:
      await self._can_sync()

  async def request_drive_version(self, node_id: int) -> str:
    """Query Elmo drive firmware version (VR) via the OS interpreter."""
    return await self._os_interpreter(node_id, "VR", query=True)

  # --- DS402 / motor control ----------------------------------------------

  async def motor_emergency_stop(self, node_id: int) -> None:
    await self.write(node_id, "MO", 0, 0)

  async def motor_is_enabled(self, node_id: int) -> bool:
    """Return True if the motor is energized (Elmo MO=1).

    Faulted drives report MO=0 — use motor_get_fault to distinguish a
    plain disable from a fault.
    """
    return await self.query_int(node_id, "MO", 0) == 1

  async def motor_get_current_position(self, node_id: int, pu: bool = False) -> int:
    cmd = "PU" if pu else "PX"
    return await self.query_int(node_id, cmd, 0)

  async def motor_get_motion_status(self, node_id: int) -> int:
    return await self.query_int(node_id, "MS", 0)

  async def motor_set_move_direction(
    self, node_id: int, direction: JointMoveDirection
  ) -> None:
    # Elmo modulo mode register: bit0 enables modulo; bits6..7 encode the
    # direction (0=Normal, 1=CW, 2=CCW, 3=Shortest). Packs to 1 + 64*direction
    # = 1/65/129/193.
    val = 1 + 64 * int(direction)
    await self.can_sdo_download_elmo_object(node_id, 24818, 0, val, _ElmoObjectDataType.UNSIGNED16)

  async def motor_check_if_move_done(self, node_id: int) -> bool:
    # E-stop and some fault paths leave MS pinned at 2 ("stopping in
    # progress") indefinitely, so gating fault-surfacing on ms==1 misses
    # them — the poll loop times out before ever consulting EMCY state.
    # Check sticky EMCY first so any fatal frame raises on the next poll
    # iteration regardless of MS.
    if self.emcy_move_error_received and self.emcy_move_error:
      nid = self.emcy_move_error_node_id
      prefix = f"Axis {nid} " if nid is not None else ""
      raise RuntimeError(f"Motor Fault: {prefix}{self.emcy_move_error}")
    ms_val = await self.query_int(node_id, "MS", 0)
    if ms_val == 0:
      return True
    if ms_val == 1:
      mo_val = await self.query_int(node_id, "MO", 0)
      if mo_val == 1:
        return True
      fault = await self.motor_get_fault(node_id)
      if fault is not None:
        raise RuntimeError(f"Motor Fault: {fault}")
      raise RuntimeError("Motor Fault (Unknown)")
    return False

  async def motor_get_fault(self, node_id: int) -> Optional[str]:
    val = await self.query_int(node_id, "MF", 0)
    if val == 0:
      return None
    # Elmo MF register: most faults are independent single bits. Bits 13/14/15
    # are different — they form a 3-bit selector (b15<<2 | b14<<1 | b13) where
    # only four combinations are real faults; the rest mean nothing.
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
    triplet_msgs = {
      0b001: "Power supply under voltage.",                # b13 only
      0b010: "Power supply over voltage.",                 # b14 only
      0b101: "Motor lead short circuit or faulty drive.",  # b13 + b15
      0b110: "Drive overheated.",                          # b14 + b15
    }
    faults = [msg for bit, msg in bit_msgs.items() if val & bit]
    triplet = (val >> 13) & 0b111
    if triplet in triplet_msgs:
      faults.append(triplet_msgs[triplet])
    if not faults:
      return f"Unknown fault code: {val} (0x{val:08X})"
    return "  ".join(faults)

  async def motor_enable(self, node_id: int, state: bool, *, use_ds402: bool) -> None:
    """Enable or disable a single drive.

    - ``use_ds402=True``: DS402 controlword sequence over RPDO1 (Fault ->
      Shutdown -> Switched On -> Op Enabled on enable; reverse on disable).
      Used for the four motion axes (shoulder/Z/elbow/wrist).
    - ``use_ds402=False``: vendor binary-interpreter ``MO=1/0``. Used for the
      rail and the servo gripper.

    Caller picks the path; the driver does not know about robot topology.

    Drives sometimes need several seconds after a fault / power-rail bounce
    before they accept enable, and disable can lag past a single 100 ms
    settle for the same reason — the retry budget covers both directions
    so a slow drive doesn't leave the arm half-enabled mid-freedrive.
    """
    if state:
      # Clear sticky EMCY state from any prior fault on this drive so the
      # post-enable motion path doesn't re-surface stale errors. Mirrors
      # clscanmotor.cs:4481 ("EmcyMoveErrorReceived = false" before re-enable)
      # plus the per-axis PVT-queue clear at clscanmotor.cs:4050-4051.
      self.clear_emcy_state(node_id=node_id)

    want = 1 if state else 0
    max_attempts = 20
    inter_attempt_sleep_s = 0.5
    for attempt in range(1, max_attempts + 1):
      if not use_ds402:
        await self.write(node_id, "MO", 0, want)
      elif state:
        # DS402 enable sequence: Fault -> Shutdown -> Switched On -> Op Enabled.
        # Matches the C# reference (clscanmotor.cs:4495-4509): back-to-back
        # CW writes, a single 100 ms settle at the end, then MO query.
        for cw in (0, 128, 6, 7, 15):
          await self._control_word_set(node_id=node_id, value=cw)
      else:
        # DS402 disable: Op Enabled -> Switched On -> Ready to Switch On.
        # Matches C# (clscanmotor.cs:4540-4543) — back-to-back, no inter-CW sleep.
        await self._control_word_set(node_id=node_id, value=7)
        await self._control_word_set(node_id=node_id, value=6)
      await asyncio.sleep(0.1)
      mo = await self.query_int(node_id, "MO", 0)
      if mo == want:
        return
      logger.warning(
        "motor_enable(state=%s) attempt %d/%d failed for node %d (MO=%s); retrying",
        state, attempt, max_attempts, node_id, mo,
      )
      await asyncio.sleep(inter_attempt_sleep_s)
    verb = "enable" if state else "disable"
    raise CanError(f"Motor failed to {verb} (node_id = {node_id}) after {max_attempts} attempts")

  # --- motion primitives --------------------------------------------------

  async def _set_op_mode(self, node_id: int, mode: int, timeout_s: float = 0.05) -> None:
    """Write 0x6060 (modes_of_operation) and poll 0x6061 (modes_of_operation_display)
    until the drive acknowledges. CiA 402 §6.2: 0x6060 is the request, 0x6061 is
    the actual mode — issuing a move (0x607A or 0x60C1 write) before the drive
    flips reads the actual mode races the mode change. Drives typically ack in
    <5 ms; timeout is generous so a busy bus doesn't false-fail.

    See https://www.stober.jp/manual/manual-commissioning-instruction-cia402-443080-01-en.pdf
    for a CiA 402 commissioning reference (object table, mode codes, state machine).
    """
    await self._can_sdo_download(node_id, 0x6060, 0x00, [mode])
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
      raw = await self._can_sdo_upload(node_id, 0x6061, 0x00)
      actual = struct.unpack("<b", raw[:1])[0] if raw else None  # INTEGER8
      if actual == mode:
        return
      if asyncio.get_event_loop().time() >= deadline:
        raise CanError(
          f"node {node_id}: 0x6061 modes_of_operation_display = {actual}, "
          f"expected {mode} after {timeout_s * 1000:.0f}ms — drive didn't ack mode change"
        )
      await asyncio.sleep(0.005)

  async def pvt_select_mode(self, enable: bool) -> None:
    """Enable/disable PVT mode on all motion axes via standard SDO writes."""
    if enable:
      if not self._pvt_mode:
        for nid in self.motion_node_ids:
          # 0x60C4 sub 6 = 0 (disable interpolation buffer)
          await self._can_sdo_download(nid, 0x60C4, 0x06, [0])
          await self._set_op_mode(nid, 7)  # interpolated position mode
        self._pvt_mode = True
      else:
        # Re-arm: drop to PPM, reset the interpolation-buffer pointer, climb
        # back into IPM. Mirrors C# PVTSelectMode true-and-already-in-PVT
        # (clscanmotor.cs:6014-6031). Skipping any of the three steps leaves
        # the drive in the wrong mode or with a stale IP buffer.
        for nid in self.motion_node_ids:
          await self._set_op_mode(nid, 1)
          await self._can_sdo_download(nid, 0x60C4, 0x06, [0])
          await self._set_op_mode(nid, 7)
    else:
      if self._pvt_mode:
        for nid in self.motion_node_ids:
          await self._set_op_mode(nid, 1)  # profile position mode
        self._pvt_mode = False

  async def wait_for_moves_done(
    self, node_ids: List[int], timeout: float
  ) -> None:
    # Poll MS every 30ms after a 50ms warm-up. The warm-up avoids reading
    # MS=0 in the window between CW=63 and motion actually starting.
    assert self._loop is not None
    loop = self._loop

    async def _poll_axis(nid: int) -> None:
      deadline = loop.time() + timeout
      await asyncio.sleep(0.05)
      while loop.time() < deadline:
        try:
          if await self.motor_check_if_move_done(int(nid)):
            return
        except CanError:
          pass
        await asyncio.sleep(0.03)
      # Final authoritative check; propagates CanError / motor-fault.
      if not await self.motor_check_if_move_done(int(nid)):
        raise CanError(f"Node {nid} move did not complete within {timeout}s")

    await asyncio.gather(*(_poll_axis(n) for n in node_ids))

  async def motors_move_start(
    self, node_ids: List[int], *, relative: bool = False
  ) -> None:
    # CiA 402 Profile Position Mode trigger handshake. Per drive:
    #   1. CW bit 4 = 0 (new_setpoint cleared) -- bit 5 stays high so the
    #      drive treats the trigger as "change set immediately".
    #   2. wait SW bit 12 (setpoint_ack) low -- drive ack of step 1.
    #   3. CW bit 4 = 1 -- rising edge latches 0x607A; motion starts.
    #   4. wait SW bit 12 high -- drive ack of step 3. If it doesn't go
    #      high, the rising edge was missed (RPDO/SDO race or drive busy)
    #      and we retry the cycle.
    # Without (4) the failure rate is ~5-10% on this Elmo firmware: bit 4
    # falls and rises within milliseconds and the drive doesn't always see
    # the edge. Polling bit 12 high is the only authoritative confirmation
    # that the new setpoint was actually latched.
    relative_bit = 0x40 if relative else 0
    cw_low = 47 + relative_bit
    cw_high = 47 + 0x10 + relative_bit
    for nid in node_ids:
      nid = int(nid)
      # Auto-recover from prior disable (post-E-stop, post-find_z IL halt,
      # post-freedrive). A disabled drive never raises SW bit 12, so the
      # PPM trigger spins all 10 attempts before failing. One MO read per
      # axis is cheap; the heavy DS402 cycle only runs when actually needed.
      if not await self.motor_is_enabled(nid):
        logger.warning("node %d: re-enabling before motion (was disabled)", nid)
        await self.motor_enable(node_id=nid, state=True, use_ds402=True)
      await self._trigger_new_setpoint(nid, cw_low, cw_high)

  async def _trigger_new_setpoint(
    self,
    node_id: int,
    cw_low: int,
    cw_high: int,
    *,
    max_attempts: int = 10,
  ) -> None:
    """Run the CiA 402 PPM new-setpoint handshake on one drive.

    Each attempt: drop CW bit 4, wait SW bit 12 low, set CW bit 4, wait
    SW bit 12 high. Retries up to ``max_attempts`` if bit 12 doesn't go
    high (= drive missed the rising edge). Raises on persistent failure
    rather than letting motion silently drop."""
    for attempt in range(1, max_attempts + 1):
      await self._control_word_set(node_id, cw_low, sync=True)
      cleared = await self._wait_setpoint_ack(node_id, want_high=False)
      if not cleared:
        logger.debug(
          "node %d: setpoint_ack didn't clear (attempt %d/%d)",
          node_id, attempt, max_attempts,
        )
        continue
      await self._control_word_set(node_id, cw_high, sync=True)
      raised = await self._wait_setpoint_ack(node_id, want_high=True)
      if raised:
        if attempt > 1:
          logger.debug(
            "node %d: new setpoint accepted on attempt %d", node_id, attempt
          )
        return
      logger.debug(
        "node %d: setpoint_ack didn't go high (attempt %d/%d); retrying",
        node_id, attempt, max_attempts,
      )
    raise CanError(
      f"node {node_id}: drive did not accept new PPM setpoint after "
      f"{max_attempts} attempts (SW bit 12 never went high after CW bit 4 "
      f"rising edge)"
    )

  async def _wait_setpoint_ack(
    self, node_id: int, *, want_high: bool, timeout: float = 0.05
  ) -> bool:
    """Wait until 0x6041 bit 12 matches ``want_high`` (or timeout).

    TPDO3 maps StatusWord with the StatusWordEvent trigger; the canopen
    listener thread parses each frame into self._statusword[node_id] and
    signals self._statusword_event[node_id]. We wait on the event, with
    a 5 ms grace before falling back to an SDO probe — covers the case
    where the drive's event-trigger config didn't take and TPDO3 is
    silent. 50 ms total is plenty: bit 12 flips within a servo cycle
    (~1-2 ms) once the drive sees the edge.
    """
    assert self._loop is not None
    ev = self._statusword_event.get(node_id)
    deadline = self._loop.time() + timeout
    while self._loop.time() < deadline:
      sw = self._statusword.get(node_id)
      if sw is not None and bool(sw & (1 << 12)) == want_high:
        return True
      if ev is None:
        # No subscription (drive outside motion_node_ids); SDO poll only.
        raw = await self._can_sdo_upload(node_id, 0x6041, 0x00)
        sw = int.from_bytes(raw[:2], "little")
        if bool(sw & (1 << 12)) == want_high:
          return True
        await asyncio.sleep(0.001)
        continue
      ev.clear()
      try:
        remaining = max(0.0, deadline - self._loop.time())
        await asyncio.wait_for(ev.wait(), timeout=min(remaining, 0.005))
      except asyncio.TimeoutError:
        # TPDO3 didn't fire within 5 ms — probe via SDO and update the
        # cache so subsequent waits start from the latest known SW.
        raw = await self._can_sdo_upload(node_id, 0x6041, 0x00)
        sw = int.from_bytes(raw[:2], "little")
        self._statusword[node_id] = sw
    return False

  async def user_program_run(
    self,
    node_id: int,
    user_function: str,
    params: Optional[List[Union[int, float]]] = None,
    timeout_sec: int = 0,
    wait_until_done: bool = False,
  ) -> int:
    if node_id < 0 or node_id > 255:
      raise ValueError("node_id must be in [0, 255]")

    ps = await self.query_int(node_id, "PS", 0)
    if ps == -2:
      raise CanError(f"Node {node_id}: controller reported PS=-2 (not ready / unavailable)")

    if ps != -1:
      await self.write(node_id, "UI", 1, 0)
      t0 = time.monotonic()
      while (time.monotonic() - t0) < 3.0:
        ps = await self.query_int(node_id, "PS", 0)
        if ps == -1:
          break
        await asyncio.sleep(0.01)
      else:
        raise CanError(f"Node {node_id}: did not reach idle state (PS=-1) within 3s (last PS={ps})")

    arg_str = f"({','.join(str(p) for p in params)})" if params else ""

    await self.write(node_id, "UI", 1, 1)

    cmd = f"XQ##{user_function}{arg_str}"
    logger.debug("user_program_run: %s", cmd)
    await self._os_interpreter(node_id, cmd, query=False)

    last_line_completed = 0
    if wait_until_done:
      t0 = time.monotonic()
      ps = 1
      ui1 = 1
      while ps == 1 and ui1 == 1 and (time.monotonic() - t0) < timeout_sec:
        ps = await self.query_int(node_id, "PS", 0)
        ui1 = await self.query_int(node_id, "UI", 1)
        await asyncio.sleep(0.01)

      last_line_completed = await self.query_int(node_id, "UI", 2)

      if ps == 1 and ui1 == 1:
        raise CanError(
          f"Node {node_id}: timeout waiting for '{user_function}' after {timeout_sec}s, "
          f"last_line={last_line_completed}"
        )
      if ui1 != 0:
        raise CanError(
          f"Node {node_id}: user program ended with UI[1]={ui1} (expected 0), "
          f"last_line={last_line_completed}"
        )

    return 0

  # --- I/O -----------------------------------------------------------------

  async def read_input(self, node_id: int, input_num: int) -> bool:
    return await self.query_int(node_id, "IB", input_num) == 1

  async def read_output(self, node_id: int, output_num: int) -> bool:
    val = await self.query_int(node_id, "OP", 0)
    mask = 1 << (output_num - 1)
    return (val & mask) == mask

  async def set_output(self, node_id: int, output_num: int, state: bool) -> None:
    await self.write(node_id, "OB", output_num, 1 if state else 0)

  async def motor_stop(self, node_id: int, settle: float = 0.1) -> None:
    """Controlled halt of one axis (port of C# MotorStop, clscanmotor.cs:5517).

    Sends CW=271 (Op Enabled + Halt — controlled deceleration, no power drop),
    waits `settle` seconds for the drive to come to rest, then writes 0x6060 = 7
    then = 1 to clear the post-halt status-word state. Used after an IL-induced
    auto-halt so the next move doesn't see a hung MS register.

    The C# version polls a TPDO-event flag with a 2.5s timeout. We can't reuse
    `wait_for_moves_done` here because MS never goes to 0 after a halt — the
    poll would just burn the full timeout. Drive deceleration is sub-100ms for
    the search velocities used here, so a fixed sleep is fine.
    """
    await self._control_word_set(node_id, 271)
    await asyncio.sleep(settle)
    await self._can_sdo_download(node_id, 0x6060, 0x00, [7])
    await self._can_sdo_download(node_id, 0x6060, 0x00, [1])

  async def read_input_logic(self, node_id: int, input_num: int) -> int:
    return await self.query_int(node_id, "IL", input_num)

  async def configure_input_logic(
    self, node_id: int, input_num: int, logic: int, logic_high: bool = False,
  ) -> None:
    """Set IL[input_num]: drive auto-acts on input edges (e.g. halt motion).

    Pass an `_InputLogic` member or raw int for `logic`. With `StopForward` the
    drive halts the motor itself the instant the input trips during forward
    motion — no software in the loop. Skips the write if value already matches;
    settles 250ms after a real change (Elmo IL needs time to apply).
    """
    value = int(logic) + (1 if logic_high else 0)
    if await self.read_input_logic(node_id, input_num) == value:
      return
    await self.write(node_id, "IL", input_num, value)
    await asyncio.sleep(0.25)
