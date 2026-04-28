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
from typing import Dict, List, Optional, Tuple, Union

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


class _JointMoveDirection(IntEnum):
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
class _MotorMoveParam:
  """One axis of a coordinated move, expressed purely in node-ID terms."""

  # CANopen node ID for this axis. Backend passes `int(self.Axis.X)`.
  node_id: int
  position: int
  velocity: int       # encoder counts/sec (driver-internal; backend converts from mm/s or deg/s)
  acceleration: int   # encoder counts/sec^2
  relative: bool = False
  direction: _JointMoveDirection = _JointMoveDirection.ShortestWay


@dataclass
class _MotorsMovePlan:
  moves: List[_MotorMoveParam] = field(default_factory=list)
  move_time: float = 10.0


# Vendor-specific Elmo binary interpreter rides on PDO2 COB-IDs (non-standard).
# Request: RPDO2 = (6 << 7) | node_id  = 0x300 + node_id
# Response: TPDO2 = (5 << 7) | node_id = 0x280 + node_id
_BI_REQUEST_COB_BASE = 0x300
_BI_RESPONSE_COB_BASE = 0x280
_GROUP_NODE_ID = 10

logger = logging.getLogger(__name__)


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
    self.EmcyMoveErrorReceived: bool = False

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
      # Elmo binary-interpreter response subscription.
      network.subscribe(_BI_RESPONSE_COB_BASE + nid, self._make_bi_callback(nid))

    logger.info("canopen: connected, nodes=%s", discovered)

    # Unmap TPDO1, map TPDO3 (StatusWord, triggered on MotionComplete) and
    # TPDO4 (DigitalInputs, triggered on edge). TPDO3 is kept mapped to match
    # the C# reference config, but move completion is detected by polling MS
    # (the MotionComplete event is unreliable on short moves).
    for node_id in self.node_id_list:
      await self._can_tpdo_unmap(TPDO.TPDO1, node_id)
      await self._tpdo_map(
        TPDO.TPDO3, node_id, [TPDOMappedObject.StatusWord], TPDOTrigger.MotionComplete
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
      self._network.disconnect()
      self._network = None
      self._nodes = {}

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
    await self._can_sdo_download(node_id, 0x18, tpdo.value - 1, 1, [num1, num2, 0, 0xC0])
    await self._can_sdo_download(node_id, 0x1A, tpdo.value - 1, 0, [0, 0, 0, 0])

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
    await self._can_sdo_download(node_id, 0x14, rpdo_idx, 1, _u32_le(0x80000000 | cob_id_11))
    # Clear mapping count
    await self._can_sdo_download(node_id, 0x16, rpdo_idx, 0, [0, 0, 0, 0])
    # Transmission type
    await self._can_sdo_download(
      node_id, 0x14, rpdo_idx, 2, [int(transmission_type) & 0xFF, 0, 0, 0]
    )
    # Mapped objects
    for i, mo in enumerate(mapped_objects):
      await self._can_sdo_download(node_id, 0x16, rpdo_idx, i + 1, _u32_le(int(mo)))
    # Mapping count
    await self._can_sdo_download(
      node_id, 0x16, rpdo_idx, 0, [len(mapped_objects) & 0xFF, 0, 0, 0]
    )
    # Re-enable (clear bit 31)
    await self._can_sdo_download(node_id, 0x14, rpdo_idx, 1, _u32_le(cob_id_11))

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
    await self._can_sdo_download(node_id, 0x18, tpdo_idx, 1, _u32_le(0xC0000000 | cob_id_11))
    # Clear mapping count
    await self._can_sdo_download(node_id, 0x1A, tpdo_idx, 0, [0, 0, 0, 0])
    # Transmission type
    await self._can_sdo_download(
      node_id, 0x18, tpdo_idx, 2, [int(transmission_type) & 0xFF, 0, 0, 0]
    )
    # Inhibit / delay 100us
    await self._can_sdo_download(node_id, 0x18, tpdo_idx, 3, [delay_100_us & 0xFF, 0, 0, 0])
    # Event timer (ms)
    await self._can_sdo_download(node_id, 0x18, tpdo_idx, 5, [event_timer_ms & 0xFF, 0, 0, 0])
    # Vendor event mask at 0x2F20:<tpdo_num>
    await self._can_sdo_download(node_id, 0x2F, 0x20, int(tpdo) & 0xFF, _u32_le(event_mask))
    # Mapped objects
    for i, mo in enumerate(mapped_objects):
      await self._can_sdo_download(node_id, 0x1A, tpdo_idx, i + 1, _u32_le(int(mo)))
    # Mapping count
    await self._can_sdo_download(
      node_id, 0x1A, tpdo_idx, 0, [len(mapped_objects) & 0xFF, 0, 0, 0]
    )
    # Re-enable (clear bits 30 + 31)
    await self._can_sdo_download(node_id, 0x18, tpdo_idx, 1, _u32_le(cob_id_11))

  # --- SDO -----------------------------------------------------------------

  async def _can_sdo_upload(
    self,
    node_id: int,
    object_byte0: int,
    object_byte1: int,
    sub_index: int,
  ) -> bytes:
    index = (object_byte0 << 8) | object_byte1
    node = self._nodes[node_id]
    # node.sdo.upload is blocking I/O (library handles expedited + segmented
    # transfers + abort codes); run off the event loop.
    return await asyncio.to_thread(node.sdo.upload, index, sub_index)

  async def _can_sdo_download(
    self,
    node_id: int,
    object_byte0: int,
    object_byte1: int,
    sub_index: int,
    data_byte: List[int],
  ) -> None:
    index = (object_byte0 << 8) | object_byte1
    node = self._nodes[node_id]
    await asyncio.to_thread(node.sdo.download, index, sub_index, bytes(data_byte))

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

    obj_byte0 = elmo_object_int >> 8
    obj_byte1 = elmo_object_int & 0xFF
    await self._can_sdo_download(node_id, obj_byte0, obj_byte1, sub_index, data_bytes)

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

  async def motor_get_current_position(self, node_id: int, pu: bool = False) -> int:
    cmd = "PU" if pu else "PX"
    return await self.query_int(node_id, cmd, 0)

  async def motor_get_motion_status(self, node_id: int) -> int:
    return await self.query_int(node_id, "MS", 0)

  async def motor_set_move_direction(
    self, node_id: int, direction: _JointMoveDirection
  ) -> None:
    # Elmo modulo mode register: bit0 enables modulo; bits6..7 encode the
    # direction (0=Normal, 1=CW, 2=CCW, 3=Shortest). Packs to 1 + 64*direction
    # = 1/65/129/193.
    val = 1 + 64 * int(direction)
    await self.can_sdo_download_elmo_object(node_id, 24818, 0, val, _ElmoObjectDataType.UNSIGNED16)

  async def motor_check_if_move_done(self, node_id: int) -> bool:
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

    faults: list[str] = []
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
    """
    if state:
      self.EmcyMoveErrorReceived = False
      # Drives sometimes need several seconds after a fault / power-rail
      # bounce before they accept enable. Spread retries over ~10s rather
      # than blasting 5 in <1s.
      max_attempts = 20
      inter_attempt_sleep_s = 0.5
      for attempt in range(1, max_attempts + 1):
        if not use_ds402:
          await self.write(node_id, "MO", 0, 1)
        else:
          # DS402 enable sequence: Fault -> Shutdown -> Switched On -> Op Enabled.
          # Matches the C# reference (clscanmotor.cs:4495-4509): back-to-back
          # CW writes, a single 100 ms settle at the end, then MO query.
          for cw in (0, 128, 6, 7, 15):
            await self._control_word_set(node_id=node_id, value=cw)
        await asyncio.sleep(0.1)
        left = await self.query_int(node_id, "MO", 0)
        if left == 1:
          break
        logger.warning(
          "motor_enable attempt %d/%d failed for node %d (MO=%s); retrying",
          attempt, max_attempts, node_id, left,
        )
        await asyncio.sleep(inter_attempt_sleep_s)
      else:
        raise CanError(f"Motor failed to enable (node_id = {node_id}) after {max_attempts} attempts")
    else:
      if not use_ds402:
        await self.write(node_id, "MO", 0, 0)
      else:
        # DS402 disable: Op Enabled -> Switched On -> Ready to Switch On.
        # Matches C# (clscanmotor.cs:4540-4543) — back-to-back, no inter-CW sleep.
        await self._control_word_set(node_id=node_id, value=7)
        await self._control_word_set(node_id=node_id, value=6)
      await asyncio.sleep(0.1)
      left = await self.query_int(node_id, "MO", 0)
      if left != 0:
        raise RuntimeError(f"Motor failed to disable (node_id = {node_id})")

  # --- motion primitives --------------------------------------------------

  async def pvt_select_mode(self, enable: bool) -> None:
    """Enable/disable PVT mode on all motion axes via standard SDO writes."""
    if enable:
      if not self._pvt_mode:
        for nid in self.motion_node_ids:
          # 0x60C4 sub 6 = 0 (disable interpolation buffer)
          await self._can_sdo_download(nid, 0x60, 0xC4, 0x06, [0])
          # 0x6060 = 7 (interpolated position mode)
          await self._can_sdo_download(nid, 0x60, 0x60, 0x00, [7])
        self._pvt_mode = True
      else:
        for nid in self.motion_node_ids:
          await self._can_sdo_download(nid, 0x60, 0x60, 0x00, [1])
    else:
      if self._pvt_mode:
        for nid in self.motion_node_ids:
          # 0x6060 = 1 (profile position mode)
          await self._can_sdo_download(nid, 0x60, 0x60, 0x00, [1])
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
    relative_bit = 0x40 if relative else 0
    for i, nid in enumerate(node_ids):
      last = i == (len(node_ids) - 1)
      await self._control_word_set(int(nid), 47 + relative_bit, sync=last)
    for i, nid in enumerate(node_ids):
      last = i == (len(node_ids) - 1)
      await self._control_word_set(int(nid), 47 + 0x10 + relative_bit, sync=last)

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
    await self._can_sdo_download(node_id, 0x60, 0x60, 0x00, [7])
    await self._can_sdo_download(node_id, 0x60, 0x60, 0x00, [1])

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
