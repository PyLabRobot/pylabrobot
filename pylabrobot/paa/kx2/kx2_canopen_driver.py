"""canopen-library-backed KX2 driver.

Parallel implementation of :class:`KX2Driver` (the hand-rolled CAN transport in
``kx2_backend.py``). Built side-by-side so the legacy driver stays working
during development. When this class passes the hello-world notebook end-to-end
on real hardware, `kx2.py` will be switched over and the legacy driver deleted.

Public method surface intentionally mirrors ``KX2Driver`` so ``KX2ArmBackend``
can be pointed at either without any other code changes.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Dict, List, Optional, Tuple, Union

import canopen

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.paa.kx2.kx2_backend import (
  MOTION_AXES,
  CanError,
  CmdType,
  ElmoObjectDataType,
  KX2Axis,
  MotorsMovePlan,
  ValType,
)

# Vendor-specific Elmo binary interpreter rides on PDO2 COB-IDs (non-standard).
# Request: RPDO2 = (6 << 7) | node_id  = 0x300 + node_id
# Response: TPDO2 = (5 << 7) | node_id = 0x280 + node_id
_BI_REQUEST_COB_BASE = 0x300
_BI_RESPONSE_COB_BASE = 0x280
_GROUP_NODE_ID = 10

logger = logging.getLogger(__name__)


class KX2CanopenDriver(Driver):
  """KX2 driver built on the `canopen` library.

  Uses `canopen.Network` for bus ownership + NMT, `node.sdo` for SDO traffic,
  `node.tpdo`/`node.rpdo` for PDO mapping, and `network.send_message` /
  `network.subscribe` for the vendor-specific Elmo binary interpreter
  (non-standard, on TPDO2/RPDO2).
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

    self._network: Optional[canopen.Network] = None
    self._nodes: Dict[int, canopen.RemoteNode] = {}
    self._loop: Optional[asyncio.AbstractEventLoop] = None

    # Pending binary-interpreter response futures keyed by
    # (node_id, msg_type, msg_index). Set from the canopen listener thread
    # via loop.call_soon_threadsafe; only the event-loop thread touches
    # this dict directly.
    self._pending_bi: Dict[Tuple[int, str, int], asyncio.Future] = {}

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
      self._nodes[nid] = network.add_node(nid, canopen.ObjectDictionary())
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
    raise NotImplementedError

  # --- SDO -----------------------------------------------------------------

  async def can_sdo_upload(
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

  async def can_sdo_download(
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

  async def can_sdo_upload_elmo_object(
    self,
    node_id: int,
    elmo_object_int: int,
    sub_index: int,
    data_type: ElmoObjectDataType,
  ) -> str:
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
      return "".join(chr(b) for b in data_bytes)
    raise CanError(f"Unsupported data type for SDO Read conversion: {data_type.name}")

  async def can_sdo_download_elmo_object(
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
    await self.can_sdo_download(node_id, obj_byte0, obj_byte1, sub_index, data_bytes)

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
      [int(a) for a in MOTION_AXES] if node_id == _GROUP_NODE_ID else [node_id]
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

  async def os_interpreter(
    self,
    node_id: int,
    cmd: str,
    *,
    query: bool = False,
  ) -> str:
    raise NotImplementedError

  # --- DS402 / motor control ----------------------------------------------

  async def motor_enable(self, axis: KX2Axis, state: bool) -> None:
    raise NotImplementedError

  async def motor_emergency_stop(self, node_id: int) -> None:
    raise NotImplementedError

  async def motor_get_current_position(self, node_id: int, pu: bool = False) -> int:
    raise NotImplementedError

  async def motor_get_fault(self, axis: KX2Axis) -> Optional[str]:
    raise NotImplementedError

  async def home_motor(
    self,
    axis: KX2Axis,
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
    raise NotImplementedError

  async def motors_move_absolute_execute(self, plan: MotorsMovePlan) -> None:
    raise NotImplementedError

  # --- I/O -----------------------------------------------------------------

  async def read_input(self, node_id: int, input_num: int) -> bool:
    raise NotImplementedError
