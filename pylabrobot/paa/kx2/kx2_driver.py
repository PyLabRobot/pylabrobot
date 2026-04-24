"""Low-level CAN transport + CANopen/DS402 drive primitives for the PAA KX2.

Uses the `canopen` library (python-can bus + CANopen SDO/PDO/NMT/EMCY).
Paired with :class:`KX2ArmBackend` in ``kx2_backend.py`` via the standard
``Device`` + ``Driver`` + capability-backend split.

This module is purely a CAN transport + Elmo interpreter layer. It knows only
CANopen node IDs (ints). All axis-level / robot-topology concepts (axis names,
motion-axis tuples, home status, move plans, joint-move direction, homing
sequences) live in ``kx2_backend``.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Tuple, Union

import canopen

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver


def _u32_le(value: int) -> List[int]:
  return list((value & 0xFFFFFFFF).to_bytes(4, byteorder="little", signed=False))


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


class JointMoveDirection(IntEnum):
  """Move-direction hint used by the driver's move primitives.

  Lives here (not in the backend) because the driver's
  `_motor_set_move_direction` primitive consumes it to program Elmo's modulo
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
  velocity: int
  acceleration: int
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

  # --- lifecycle -----------------------------------------------------------

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Bring up the CAN bus, reset & start all nodes, discover them."""
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

  async def stop(self) -> None:
    if self._network is not None:
      self._network.disconnect()
      self._network = None
      self._nodes = {}

  # --- drive init (called by KX2ArmBackend._on_setup after setup()) --------

  async def connect_part_two(self) -> None:
    """Configure PDO mapping + Elmo DS402 parameters after the CAN bus is up.

    Unmap TPDO1, map TPDO3 (StatusWord, triggered on MotionComplete) and
    TPDO4 (DigitalInputs, triggered on edge). TPDO3 is kept mapped to match
    the C# reference config, but move completion is detected by polling MS
    (the MotionComplete event is unreliable on short moves). Then program
    Elmo vendor objects that set interpolation config, and finally map
    RPDO1 (ControlWord) and RPDO3 (interpolated target position+velocity)
    per motion axis.
    """
    assert self._network is not None

    for node_id in self.node_id_list:
      await self._can_tpdo_unmap(TPDO.TPDO1, node_id)
      await self._tpdo_map(
        TPDO.TPDO3, node_id, [TPDOMappedObject.StatusWord], TPDOTrigger.MotionComplete
      )
      await self._tpdo_map(
        TPDO.TPDO4, node_id, [TPDOMappedObject.DigitalInputs], TPDOTrigger.DigitalInputEvent
      )

    for nid in self.motion_node_ids:
      await self._can_sdo_download_elmo_object(
        nid, 24768, 0, "-1", ElmoObjectDataType.INTEGER16
      )
      await self._can_sdo_download_elmo_object(
        nid, 24772, 2, "16", ElmoObjectDataType.UNSIGNED32
      )
      await self._can_sdo_download_elmo_object(
        nid, 24772, 3, "0", ElmoObjectDataType.UNSIGNED8
      )
      await self._can_sdo_download_elmo_object(
        nid, 24772, 5, "8", ElmoObjectDataType.UNSIGNED8
      )
      await self._can_sdo_download_elmo_object(
        nid, 24770, 2, "-3", ElmoObjectDataType.INTEGER8
      )
      await self._can_sdo_download_elmo_object(
        nid, 24669, 0, "1", ElmoObjectDataType.INTEGER16
      )

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
    await self._pvt_select_mode(False)

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

  async def _can_sdo_upload_elmo_object(
    self,
    node_id: int,
    elmo_object_int: int,
    sub_index: int,
    data_type: ElmoObjectDataType,
  ) -> str:
    obj_byte0 = elmo_object_int >> 8
    obj_byte1 = elmo_object_int & 0xFF
    data_bytes = await self._can_sdo_upload(node_id, obj_byte0, obj_byte1, sub_index)

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
      return "".join(chr(b) for b in data_bytes)
    raise CanError(f"Unsupported data type for SDO Read conversion: {data_type.name}")

  async def _can_sdo_download_elmo_object(
    self,
    node_id: int,
    elmo_object_int: int,
    sub_index: int,
    data: str,
    data_type: ElmoObjectDataType,
  ) -> None:
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
    if is_int:
      (val,) = struct.unpack("<i", data[4:8])
      value_str = str(val)
    else:
      (val,) = struct.unpack("<f", data[4:8])
      value_str = str(val)

    fut = self._pending_bi.pop((node_id, msg_type, msg_index), None)
    if fut is not None and not fut.done():
      fut.set_result(value_str)

  async def _binary_interpreter(
    self,
    node_id: int,
    cmd: str,
    cmd_index: int,
    cmd_type: CmdType,
    value: str = "0",
    val_type: ValType = ValType.Int,
    low_priority: bool = False,
  ) -> Union[str, float]:
    del low_priority  # request priority is not meaningful over canopen.Network.send_message
    if self._network is None:
      raise CanError("binary_interpreter called before setup()")
    if value == "":
      value = "0"

    timeout = 10.0 if cmd.upper() == "SV" else 1.0
    is_float = val_type == ValType.Float
    is_query = cmd_type == CmdType.ValQuery
    is_execute = cmd_type == CmdType.Execute

    # -- build the 8-byte request --
    byte0 = ord(cmd[0]) & 0xFF
    byte1 = ord(cmd[-1]) & 0xFF
    byte2 = cmd_index & 0xFF
    byte3 = (cmd_index >> 8) & 0x3F
    if is_query:
      byte3 |= 0x40
    if is_float:
      byte3 |= 0x80
    if is_float:
      val_bytes = struct.pack("<f", float(value))
    else:
      val_bytes = struct.pack("<i", int(round(float(value))))
    payload = bytes([byte0, byte1, byte2, byte3]) + val_bytes
    send_len = 4 if is_execute else 8
    data_to_send = payload[:send_len]

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

    # -- dispatch: single node vs. group --
    target_nodes = (
      list(self.motion_node_ids) if node_id == _GROUP_NODE_ID else [node_id]
    )

    futures: List[asyncio.Future] = []
    for nid in target_nodes:
      key = (nid, cmd, cmd_index)
      # If a stale pending future exists, drop it.
      old = self._pending_bi.pop(key, None)
      if old is not None and not old.done():
        old.cancel()
      fut = self._loop.create_future() if self._loop else asyncio.get_event_loop().create_future()
      self._pending_bi[key] = fut
      futures.append(fut)

    self._network.send_message(_BI_REQUEST_COB_BASE + node_id, data_to_send)

    try:
      resps = await asyncio.wait_for(asyncio.gather(*futures), timeout=timeout)
    except asyncio.TimeoutError:
      for nid in target_nodes:
        self._pending_bi.pop((nid, cmd, cmd_index), None)
      raise CanError(
        f"Timeout waiting for response to {cmd}[{cmd_index}] from node {node_id}"
      )

    # -- interpret responses --
    if is_query:
      if node_id == _GROUP_NODE_ID:
        value = ",".join(str(r) for r in resps)
      else:
        value = str(resps[0])
      return float(value) if is_float else int(float(value))

    if is_execute:
      if any(r == "" for r in resps):
        missing = [nid for nid, r in zip(target_nodes, resps) if r == ""]
        raise CanError(f"No execute response from nodes {missing} for {cmd}[{cmd_index}]")
      return float(value) if is_float else int(float(value))

    # Write: verify each echoed value matches the one we sent.
    for nid, resp in zip(target_nodes, resps):
      if is_float:
        ok = _float_matches(value, str(resp))
      else:
        ok = int(float(resp)) == int(float(value))
      if not ok:
        raise CanError(
          f"Unexpected CAN response: sent {cmd}[{cmd_index}]={value}, "
          f"got {resp} from node {nid}"
        )
    return float(value) if is_float else int(float(value))

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
    reply = await asyncio.to_thread(node.sdo.upload, 0x1023, 3)
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
    return await self._os_interpreter(int(node_id), "VR", query=True)

  # --- DS402 / motor control ----------------------------------------------

  async def motor_emergency_stop(self, node_id: int) -> None:
    await self._binary_interpreter(node_id, "MO", 0, CmdType.ValSet, "0")

  async def motor_get_current_position(self, node_id: int, pu: bool = False) -> int:
    cmd = "PU" if pu else "PX"
    val_str = await self._binary_interpreter(int(node_id), cmd, 0, CmdType.ValQuery)
    return int(round(float(val_str)))

  async def motor_get_motion_status(self, node_id: int) -> int:
    val = await self._binary_interpreter(node_id, "MS", 0, CmdType.ValQuery)
    return int(round(float(val)))

  async def _motor_set_move_direction(
    self, node_id: int, direction: JointMoveDirection
  ) -> None:
    # 0=Normal, 1=Clockwise, 2=Counterclockwise, 3=ShortestWay.
    dir_int = int(direction)
    if dir_int == 1:
      val_str = "65"
    elif dir_int == 2:
      val_str = "129"
    elif dir_int == 3:
      val_str = "193"
    else:
      val_str = "1"
    await self._can_sdo_download_elmo_object(
      node_id, 24818, 0, val_str, ElmoObjectDataType.UNSIGNED16
    )

  async def motor_check_if_move_done(self, node_id: int) -> bool:
    ms_val = await self._binary_interpreter(node_id, "MS", 0, CmdType.ValQuery)
    if ms_val == 0:
      return True
    if ms_val == 1:
      mo_val = await self._binary_interpreter(node_id, "MO", 0, CmdType.ValQuery)
      if mo_val == 1:
        return True
      fault = await self.motor_get_fault(node_id)
      if fault is not None:
        raise RuntimeError(f"Motor Fault: {fault}")
      raise RuntimeError("Motor Fault (Unknown)")
    if ms_val == 2:
      return False
    return False

  async def motor_get_fault(self, node_id: int) -> Optional[str]:
    val = await self._binary_interpreter(int(node_id), "MF", 0, CmdType.ValQuery)
    if val == 0:
      return None
    assert isinstance(val, int)

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
    node_id = int(node_id)

    if state:
      self.EmcyMoveErrorReceived = False
      max_attempts = 5
      for attempt in range(1, max_attempts + 1):
        if not use_ds402:
          await self._binary_interpreter(node_id, "MO", 0, CmdType.ValSet, "1")
        else:
          # DS402 enable sequence: Fault -> Shutdown -> Switched On -> Op Enabled.
          # Matches the C# reference (clscanmotor.cs:4495-4509): back-to-back
          # CW writes, a single 100 ms settle at the end, then MO query.
          for cw in (0, 128, 6, 7, 15):
            await self._control_word_set(node_id=node_id, value=cw)
        await asyncio.sleep(0.1)
        left = await self._binary_interpreter(
          node_id=node_id, cmd="MO", cmd_index=0, cmd_type=CmdType.ValQuery
        )
        if left == 1:
          break
        logger.warning(
          "motor_enable attempt %d/%d failed for node %d (MO=%s); retrying",
          attempt, max_attempts, node_id, left,
        )
      else:
        raise CanError(f"Motor failed to enable (node_id = {node_id}) after {max_attempts} attempts")
    else:
      if not use_ds402:
        try:
          await self._binary_interpreter(
            node_id=node_id, cmd="MO", cmd_index=0, cmd_type=CmdType.ValSet, value="0"
          )
        except Exception:
          pass
      else:
        # DS402 disable: Op Enabled -> Switched On -> Ready to Switch On.
        # Matches C# (clscanmotor.cs:4540-4543) — back-to-back, no inter-CW sleep.
        await self._control_word_set(node_id=node_id, value=7)
        await self._control_word_set(node_id=node_id, value=6)
      await asyncio.sleep(0.1)
      left = await self._binary_interpreter(
        node_id=node_id, cmd="MO", cmd_index=0, cmd_type=CmdType.ValQuery
      )
      if left != 0:
        raise RuntimeError(f"Motor failed to disable (node_id = {node_id})")

  # --- motion primitives --------------------------------------------------

  async def _pvt_select_mode(self, enable: bool) -> None:
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

  async def _wait_for_moves_done(
    self, node_ids: List[int], timeout: float
  ) -> None:
    # Poll MS every 30ms after a 50ms warm-up. The warm-up avoids reading
    # MS=0 in the window between CW=63 and motion actually starting.
    assert self._loop is not None

    async def _poll_axis(nid: int) -> None:
      deadline = self._loop.time() + timeout
      await asyncio.sleep(0.05)
      while self._loop.time() < deadline:
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

  async def _motors_move_start(
    self, node_ids: List[int], *, relative: bool = False
  ) -> None:
    relative_bit = 0x40 if relative else 0
    for i, nid in enumerate(node_ids):
      last = i == (len(node_ids) - 1)
      await self._control_word_set(int(nid), 47 + relative_bit, sync=last)
    for i, nid in enumerate(node_ids):
      last = i == (len(node_ids) - 1)
      await self._control_word_set(int(nid), 47 + 0x10 + relative_bit, sync=last)

  async def motors_move_absolute_execute(self, plan: MotorsMovePlan) -> None:
    await self._pvt_select_mode(False)

    for move in plan.moves:
      nid = int(move.node_id)
      await self._motor_set_move_direction(nid, move.direction)
      # 0x607A = Target Position (24698 decimal)
      await self._can_sdo_download_elmo_object(
        nid, 24698, 0, str(int(move.position)), ElmoObjectDataType.INTEGER32,
      )
      # 0x6081 = Profile Velocity (24705 decimal)
      await self._can_sdo_download_elmo_object(
        nid, 24705, 0, str(int(move.velocity)), ElmoObjectDataType.UNSIGNED32,
      )
      acc = max(int(move.acceleration), 100)
      # 0x6083 = Profile Acceleration (24707 decimal)
      await self._can_sdo_download_elmo_object(
        nid, 24707, 0, str(acc), ElmoObjectDataType.UNSIGNED32,
      )
      # 0x6084 = Profile Deceleration (24708 decimal)
      await self._can_sdo_download_elmo_object(
        nid, 24708, 0, str(acc), ElmoObjectDataType.UNSIGNED32,
      )

    node_ids = [int(move.node_id) for move in plan.moves]
    await self._motors_move_start(node_ids)
    await self._wait_for_moves_done(node_ids, timeout=plan.move_time + 2)

  async def _user_program_run(
    self,
    node_id: int,
    user_function: str,
    params=None,
    timeout_sec: int = 0,
    wait_until_done: bool = False,
  ) -> int:
    if not isinstance(node_id, int):
      raise ValueError("node_id must be int")
    if node_id < 0 or node_id > 255:
      raise ValueError("node_id must be in [0, 255]")
    node_id = int(node_id)

    ps = int(await self._binary_interpreter(node_id, "PS", 0, CmdType.ValQuery))
    if ps == -2:
      raise CanError(f"Node {node_id}: controller reported PS=-2 (not ready / unavailable)")

    if ps != -1:
      await self._binary_interpreter(node_id, "UI", 1, CmdType.ValSet, value="0")
      t0 = time.monotonic()
      while (time.monotonic() - t0) < 3.0:
        ps = int(await self._binary_interpreter(node_id, "PS", 0, CmdType.ValQuery))
        if ps == -1:
          break
        await asyncio.sleep(0.01)
      else:
        raise CanError(f"Node {node_id}: did not reach idle state (PS=-1) within 3s (last PS={ps})")

    arg_str = ""
    if params:
      parts = [str(p) for p in params]
      if parts:
        arg_str = f"({','.join(parts)})"

    await self._binary_interpreter(node_id, "UI", 1, CmdType.ValSet, value="1")

    cmd = f"XQ##{user_function}{arg_str}"
    logger.debug("_user_program_run: %s", cmd)
    await self._os_interpreter(node_id, cmd, query=False)

    last_line_completed = 0
    if wait_until_done:
      t0 = time.monotonic()
      ps = 1
      ui1 = 1
      while ps == 1 and ui1 == 1 and (time.monotonic() - t0) < float(timeout_sec):
        ps = int(await self._binary_interpreter(node_id, "PS", 0, CmdType.ValQuery))
        ui1 = int(await self._binary_interpreter(node_id, "UI", 1, CmdType.ValQuery))
        await asyncio.sleep(0.01)

      expr_raw = await self._binary_interpreter(node_id, "UI", 2, CmdType.ValQuery)
      try:
        last_line_completed = int(str(expr_raw).strip())
      except Exception:
        last_line_completed = 0

      if ui1 != 0:
        raise CanError(
          f"Node {node_id}: user program ended with UI[1]={ui1} (expected 0), "
          f"last_line={last_line_completed}"
        )
      if ps == 1 and ui1 == 1:
        raise CanError(
          f"Node {node_id}: timeout waiting for '{user_function}' after {timeout_sec}s, "
          f"last_line={last_line_completed}"
        )

    return 0

  # --- I/O -----------------------------------------------------------------

  async def read_input(self, node_id: int, input_num: int) -> bool:
    left = await self._binary_interpreter(node_id, "IB", input_num, CmdType.ValQuery)
    return left == 1

  async def _read_output(self, node_id: int, output_num: int) -> bool:
    expression = await self._binary_interpreter(node_id, "OP", 0, CmdType.ValQuery)
    val = int(expression)
    mask = 1 << (output_num - 1)
    return (val & mask) == mask

  async def _set_output(self, node_id: int, output_num: int, state: bool) -> Union[str, float]:
    val = "1" if state else "0"
    return await self._binary_interpreter(node_id, "OB", output_num, CmdType.ValSet, val)
