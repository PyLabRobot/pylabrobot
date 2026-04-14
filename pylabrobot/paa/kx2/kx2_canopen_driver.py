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
from typing import Dict, List, Optional, Union

import canopen

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.paa.kx2.kx2_backend import (
  CanError,
  CmdType,
  ElmoObjectDataType,
  KX2Axis,
  MotorsMovePlan,
  ValType,
)

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
    raise NotImplementedError

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
