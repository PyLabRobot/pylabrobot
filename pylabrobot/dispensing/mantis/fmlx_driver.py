"""Low-level Formulatrix (FMLX) protocol driver for the Mantis dispenser.

Handles packet construction, checksum calculation, command sending/receiving,
and asynchronous event dispatch over an FTDI serial link.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any, Callable, Dict, List, Optional, Tuple

from pylabrobot.io.ftdi import FTDI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Packet
# ---------------------------------------------------------------------------


class FmlxPacket:
  """A single FMLX protocol packet (command or response)."""

  HEADER_SIZE = 12
  CHECKSUM_SIZE = 2

  def __init__(
    self,
    opcode: int,
    packet_id: int = 0,
    address: int = 0,
    sequence_id: int = 0,
  ) -> None:
    self.opcode = opcode
    self.packet_id = packet_id
    self.address = address
    self.sequence_id = sequence_id
    self.reserved = 0
    self.data = bytearray()

  # -- payload builders (fluent API) --

  def add_int16(self, value: int) -> "FmlxPacket":
    self.data.extend(struct.pack("<h", value))
    return self

  def add_uint16(self, value: int) -> "FmlxPacket":
    self.data.extend(struct.pack("<H", value))
    return self

  def add_uint32(self, value: int) -> "FmlxPacket":
    self.data.extend(struct.pack("<I", value))
    return self

  def add_bool(self, value: bool) -> "FmlxPacket":
    self.add_uint16(1 if value else 0)
    return self

  def add_double(self, value: float) -> "FmlxPacket":
    self.data.extend(struct.pack("<d", value))
    return self

  def add_string(self, value: str) -> "FmlxPacket":
    encoded = value.encode("utf-16-le")
    self.data.extend(encoded)
    self.data.extend(b"\x00\x00")
    return self

  # -- serialisation --

  @staticmethod
  def calculate_checksum(raw_bytes: bytes) -> int:
    checksum = 0
    for i in range(0, len(raw_bytes), 2):
      val = raw_bytes[i]
      if i + 1 < len(raw_bytes):
        val |= raw_bytes[i + 1] << 8
      checksum ^= val
    return checksum

  def to_bytes(self) -> bytes:
    size_val = self.HEADER_SIZE + len(self.data)
    header = struct.pack(
      "<6H",
      size_val,
      self.sequence_id,
      self.packet_id,
      self.reserved,
      self.address,
      self.opcode,
    )
    content = header + self.data
    checksum = self.calculate_checksum(content)
    return content + struct.pack("<H", checksum)


# ---------------------------------------------------------------------------
# Decoding utilities
# ---------------------------------------------------------------------------


def _decode_bool(data: bytes, offset: int = 0) -> Tuple[bool, int]:
  val = struct.unpack_from("<H", data, offset)[0]
  return bool(val), offset + 2


def _decode_string(data: bytes, offset: int = 0) -> Tuple[str, int]:
  end = offset
  while end < len(data) - 1:
    if data[end] == 0 and data[end + 1] == 0:
      break
    end += 2
  s = data[offset:end].decode("utf-16-le", errors="ignore")
  return s, end + 2


# ---------------------------------------------------------------------------
# Event / opcode name maps
# ---------------------------------------------------------------------------

EVENT_NAMES: Dict[int, str] = {
  512: "MotionStarted",
  513: "MoveDone",
  514: "HomeDone",
  515: "MotorErrorOccured",
  768: "BottlesChanged",
  769: "SensorAlarm",
  784: "SequenceProgress",
  785: "SequenceStopped",
  801: "InputChanged",
}

OPCODE_NAMES_ADDR0: Dict[int, str] = {
  1: "GetVersion",
  2: "GetName",
  10: "GetMotorLimits",
  11: "SetMotorLimits",
  12: "GetMotorCurrents",
  13: "SetMotorCurrents",
  14: "GetMotorConfig",
  15: "SetMotorConfig",
  17: "ClearMotorFaults",
  20: "GetMotorStatus",
  21: "Home",
  22: "MoveAbsolute",
  27: "GetMotorPosition",
  28: "SetMotorPosition",
  41: "WritePPI",
  50: "IsSensorEnabled",
  52: "GetSensorLimits",
  55: "SetExtendedInputMask",
  61: "ClearSequencer",
  62: "StartSequencer",
  65: "QueueWritePPI",
  68: "QueueMoveItem",
  82: "GetFollowingErrorConfig",
}

OPCODE_NAMES_ADDR10: Dict[int, str] = {
  1: "P_GetVersion",
  10: "P_GetTargetPressure",
  11: "P_SetTargetPressure",
  12: "P_GetControllerEnabled",
  13: "P_SetControllerEnabled",
  14: "P_GetPumpOn",
  15: "P_SetPumpOn",
  16: "P_GetStatus",
  20: "P_GetFeedbackSensorParams",
  21: "P_SetFeedbackSensorParams",
  23: "P_SetPidParams",
  25: "P_SetSettlingCriteria",
  30: "P_ReadFeedbackSensor",
  32: "P_SetProportionalValve",
  34: "P_SetSolenoidValve",
  41: "P_GetAux",
  42: "P_SetAux",
}


# ---------------------------------------------------------------------------
# Response / event decoders
# ---------------------------------------------------------------------------


def decode_response(req_opcode: int, req_addr: int, status: int, data: bytes) -> Dict[str, Any]:
  """Decode a raw FMLX response payload into a dictionary."""
  if status < 0:
    return {"error": status, "data": data.hex()}
  try:
    if req_opcode in (1, 2):
      s, _ = _decode_string(data, 0)
      return {"value": s}
    if req_addr == 0:
      if req_opcode in (10, 12, 13):
        cnt = len(data) // 8
        return {"values": struct.unpack(f"<{cnt}d", data)}
      if req_opcode == 14:
        enb, _ = _decode_bool(data, 0)
        vals = struct.unpack_from("<ddd", data, 2)
        usteps = struct.unpack_from("<h", data, 26)[0] if len(data) >= 28 else None
        return {"enabled": enb, "pid": vals, "usteps": usteps}
      if req_opcode == 20:
        return {"status": struct.unpack("<H", data)[0]}
      if req_opcode == 27:
        vals = struct.unpack(f"<{len(data) // 8}d", data)
        keys = ["demand_pos", "actual_pos", "target_pos", "demand_vel", "actual_vel", "accel"]
        return dict(zip(keys, vals))
      if req_opcode == 60:
        s, sz, c = struct.unpack("<Hhh", data)
        return {"state": s, "size": sz, "count": c}
      if req_opcode in (61, 62, 63, 64, 72):
        return {"ret": struct.unpack("<h", data)[0]}
      if req_opcode in (65, 66, 67, 70, 71):
        num, err = struct.unpack("<hh", data)
        return {"num": num, "err": err}
      if req_opcode == 68:
        num, err = struct.unpack("<hH", data)
        return {"num": num, "err": err}
      if req_opcode == 82:
        enb, _ = _decode_bool(data, 0)
        max_err = struct.unpack_from("<d", data, 2)[0]
        return {"enabled": enb, "max_error": max_err}
    elif req_addr == 10:
      if req_opcode in (10, 20, 22, 24, 30):
        cnt = len(data) // 8
        return {"values": struct.unpack(f"<{cnt}d", data)}
      if req_opcode in (12, 14, 16, 31, 33, 41, 43):
        return {"value": struct.unpack("<H", data)[0]}
  except Exception:  # noqa: BLE001
    pass
  return {"data": data.hex()}


def decode_event(event_code: int, addr: int, data: bytes) -> Dict[str, Any]:
  """Decode a raw FMLX event payload into a dictionary."""
  name = EVENT_NAMES.get(event_code, f"Event:{event_code}")
  res: Dict[str, Any] = {"event": name, "code": event_code, "addr": addr}
  try:
    if event_code == 512:
      res["motor"] = struct.unpack("<H", data)[0]
    elif event_code == 513:
      mid, status = struct.unpack_from("<hH", data, 0)
      pva = struct.unpack_from("<ddd", data, 4)
      res.update({"motor": mid, "status": status, "pva": pva})
    elif event_code == 514:
      mid = struct.unpack_from("<h", data, 0)[0]
      vals = struct.unpack_from(f"<{(len(data) - 2) // 8}d", data, 2)
      res.update({"motor": mid, "vals": vals})
    elif event_code == 515:
      mid, err = struct.unpack("<HH", data)
      res.update({"motor": mid, "error": err})
    elif event_code == 768:
      num = struct.unpack_from("<h", data, 0)[0]
      res["masks"] = struct.unpack_from(f"<{num}H", data, 2)
    elif event_code == 784:
      sid, q, a = struct.unpack("<hhh", data)
      res.update({"seq_id": sid, "in_queue": q, "available": a})
    elif event_code == 785:
      sid, unp = struct.unpack("<hh", data)
      res.update({"seq_id": sid, "unprocessed": unp})
    elif event_code == 801:
      iid, on = struct.unpack("<HH", data)
      res.update({"input_id": iid, "on": bool(on)})
  except Exception:  # noqa: BLE001
    res["data"] = data.hex()
  return res


# ---------------------------------------------------------------------------
# FMLX Driver
# ---------------------------------------------------------------------------


class FmlxDriver:
  """Async driver for the Formulatrix FMLX packet protocol over FTDI.

  Args:
    ftdi: A configured (but not yet opened) :class:`pylabrobot.io.ftdi.FTDI` instance.
  """

  def __init__(self, ftdi: FTDI) -> None:
    self._ftdi = ftdi
    self._pkt_counter = 0
    self._seq_counter = 1
    self._pending: Dict[int, Tuple[asyncio.Future, int, int]] = {}
    self._event_waiters: List[Tuple[asyncio.Future, Callable[[Dict], bool]]] = []
    self._buffer = bytearray()
    self._read_task: Optional[asyncio.Task] = None
    self.on_event: Optional[Callable[[Dict[str, Any]], None]] = None

  # -- sequence id management --

  def next_seq_id(self) -> int:
    sid = self._seq_counter
    self._seq_counter = (self._seq_counter % 32767) + 1
    return sid

  # -- high-level queue helpers --

  async def queue_write_ppi(
    self, duration: int, addr: int, values: List[int], timeout: float = 5.0
  ) -> int:
    sid = self.next_seq_id()
    await self.send_command(cmd_queue_write_ppi(sid, duration, addr, values), timeout=timeout)
    return sid

  async def queue_move_item(
    self,
    rel: bool,
    wait: bool,
    pva_triplets: List[List[float]],
    timeout: float = 5.0,
  ) -> int:
    sid = self.next_seq_id()
    await self.send_command(cmd_queue_move_item(sid, rel, wait, pva_triplets), timeout=timeout)
    return sid

  # -- connection lifecycle --

  async def connect(self) -> None:
    await self._ftdi.setup()
    await self._ftdi.set_baudrate(115200)
    await self._ftdi.set_line_property(8, 1, 0)
    await self._ftdi.set_flowctrl(0x100)
    await self._ftdi.usb_purge_rx_buffer()
    await self._ftdi.usb_purge_tx_buffer()
    self._read_task = asyncio.create_task(self._read_loop())

  async def disconnect(self) -> None:
    if self._read_task:
      self._read_task.cancel()
      try:
        await self._read_task
      except asyncio.CancelledError:
        pass
    await self._ftdi.stop()

  # -- read loop --

  async def _read_loop(self) -> None:
    try:
      while True:
        data = await self._ftdi.read(1024)
        if data:
          self._buffer.extend(data)
          self._process_buffer()
        await asyncio.sleep(0.01)
    except asyncio.CancelledError:
      raise
    except Exception:
      logger.exception("FMLX read loop error")

  def _process_buffer(self) -> None:
    while len(self._buffer) >= 14:
      size = struct.unpack_from("<H", self._buffer, 0)[0]
      if size < 12 or size > 526:
        self._buffer.pop(0)
        continue
      if len(self._buffer) < size + 2:
        break

      packet_bytes = bytes(self._buffer[: size + 2])
      del self._buffer[: size + 2]

      header = struct.unpack("<6H", packet_bytes[:12])
      _p_size, _seq_id, pkt_id, _rsrv, addr, opcode = header
      data = packet_bytes[12:_p_size]

      if opcode >= 256:  # event
        decoded = decode_event(opcode, addr, data)
        self._dispatch_event(decoded)
      else:  # response
        if pkt_id in self._pending:
          fut, req_op, req_addr = self._pending.pop(pkt_id)
          decoded = decode_response(req_op, req_addr, opcode, data)
          if not fut.done():
            fut.set_result(decoded)

  def _dispatch_event(self, decoded: Dict[str, Any]) -> None:
    if self.on_event:
      self.on_event(decoded)
    for fut, condition in list(self._event_waiters):
      if not fut.done() and condition(decoded):
        fut.set_result(decoded)

  # -- command send --

  async def send_command(self, packet: FmlxPacket, timeout: float = 5.0) -> Dict[str, Any]:
    """Send a command packet and wait for the response."""
    pkt_id = self._pkt_counter
    self._pkt_counter = (self._pkt_counter + 1) & 0xFFFF
    packet.packet_id = pkt_id

    fut = asyncio.get_running_loop().create_future()
    self._pending[pkt_id] = (fut, packet.opcode, packet.address)

    await self._ftdi.write(packet.to_bytes())

    try:
      return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
      self._pending.pop(pkt_id, None)
      raise

  # -- event waiting --

  async def wait_for_event(
    self, condition: Callable[[Dict], bool], timeout: float = 30.0
  ) -> Dict[str, Any]:
    """Block until an event matching *condition* arrives or *timeout* elapses."""
    fut = asyncio.get_running_loop().create_future()
    waiter = (fut, condition)
    self._event_waiters.append(waiter)
    try:
      return await asyncio.wait_for(fut, timeout)
    finally:
      if waiter in self._event_waiters:
        self._event_waiters.remove(waiter)


# ---------------------------------------------------------------------------
# Dispense device commands (address 0)
# ---------------------------------------------------------------------------


def cmd_get_version() -> FmlxPacket:
  return FmlxPacket(1, address=0)


def cmd_get_name() -> FmlxPacket:
  return FmlxPacket(2, address=0)


def cmd_get_motor_limits(motor_id: int) -> FmlxPacket:
  return FmlxPacket(10, address=0).add_int16(motor_id)


def cmd_set_motor_limits(motor_id: int, lower: float, upper: float) -> FmlxPacket:
  return FmlxPacket(11, address=0).add_int16(motor_id).add_double(lower).add_double(upper)


def cmd_get_motor_currents(motor_id: int) -> FmlxPacket:
  return FmlxPacket(12, address=0).add_int16(motor_id)


def cmd_set_motor_currents(motor_id: int, boost: float, travel: float, hold: float) -> FmlxPacket:
  return (
    FmlxPacket(13, address=0)
    .add_int16(motor_id)
    .add_double(boost)
    .add_double(travel)
    .add_double(hold)
  )


def cmd_get_motor_config(motor_id: int) -> FmlxPacket:
  return FmlxPacket(14, address=0).add_int16(motor_id)


def cmd_set_motor_config(
  motor_id: int, invert: bool, kp: float, ki: float, kd: float, usteps: int
) -> FmlxPacket:
  return (
    FmlxPacket(15, address=0)
    .add_int16(motor_id)
    .add_bool(invert)
    .add_double(kp)
    .add_double(ki)
    .add_double(kd)
    .add_int16(usteps)
  )


def cmd_clear_motor_faults(motor_id: int) -> FmlxPacket:
  return FmlxPacket(17, address=0).add_int16(motor_id)


def cmd_get_motor_status(motor_id: int) -> FmlxPacket:
  return FmlxPacket(20, address=0).add_int16(motor_id)


def cmd_home(
  motor_id: int,
  method: int,
  pos_edge: bool,
  pos_dir: bool,
  slow: float,
  fast: float,
  acc: float,
) -> FmlxPacket:
  return (
    FmlxPacket(21, address=0)
    .add_int16(motor_id)
    .add_int16(method)
    .add_bool(pos_edge)
    .add_bool(pos_dir)
    .add_double(slow)
    .add_double(fast)
    .add_double(acc)
  )


def cmd_move_absolute(motor_id: int, pos: float, vel: float, acc: float) -> FmlxPacket:
  return (
    FmlxPacket(22, address=0).add_int16(motor_id).add_double(pos).add_double(vel).add_double(acc)
  )


def cmd_get_motor_position(motor_id: int) -> FmlxPacket:
  return FmlxPacket(27, address=0).add_int16(motor_id)


def cmd_set_motor_position(motor_id: int, pos: float) -> FmlxPacket:
  return FmlxPacket(28, address=0).add_int16(motor_id).add_double(pos)


def cmd_is_sensor_enabled(sensor_id: int) -> FmlxPacket:
  return FmlxPacket(50, address=0).add_int16(sensor_id)


def cmd_get_sensor_limits(sensor_id: int) -> FmlxPacket:
  return FmlxPacket(52, address=0).add_int16(sensor_id)


def cmd_write_ppi(start_addr: int, values: List[int]) -> FmlxPacket:
  pkt = FmlxPacket(41, address=0).add_int16(start_addr).add_int16(len(values))
  for v in values:
    pkt.add_uint16(v)
  return pkt


def cmd_set_extended_input_mask(mask: int) -> FmlxPacket:
  return FmlxPacket(55, address=0).add_uint32(mask)


def cmd_clear_sequencer() -> FmlxPacket:
  return FmlxPacket(61, address=0)


def cmd_start_sequencer() -> FmlxPacket:
  return FmlxPacket(62, address=0)


def cmd_queue_write_ppi(seq_id: int, duration: int, addr: int, values: List[int]) -> FmlxPacket:
  pkt = (
    FmlxPacket(65, address=0)
    .add_int16(seq_id)
    .add_int16(duration)
    .add_int16(addr)
    .add_int16(len(values))
  )
  for v in values:
    pkt.add_uint16(v)
  return pkt


def cmd_queue_move_item(
  seq_id: int, rel: bool, wait: bool, pva_triplets: List[List[float]]
) -> FmlxPacket:
  pkt = FmlxPacket(68, address=0).add_int16(seq_id).add_bool(rel).add_bool(wait)
  for p, v, a in pva_triplets:
    pkt.add_double(p).add_double(v).add_double(a)
  return pkt


def cmd_get_following_error_config(motor_id: int) -> FmlxPacket:
  return FmlxPacket(82, address=0).add_int16(motor_id)


# ---------------------------------------------------------------------------
# Pressure device commands (address 10)
# ---------------------------------------------------------------------------


def cmd_p_get_version() -> FmlxPacket:
  return FmlxPacket(1, address=10)


def cmd_p_get_target_pressure(sensor_id: int) -> FmlxPacket:
  return FmlxPacket(10, address=10).add_int16(sensor_id)


def cmd_p_set_target_pressure(sensor_id: int, val: float) -> FmlxPacket:
  return FmlxPacket(11, address=10).add_int16(sensor_id).add_double(val)


def cmd_p_get_controller_enabled(ctrl_id: int) -> FmlxPacket:
  return FmlxPacket(12, address=10).add_int16(ctrl_id)


def cmd_p_set_controller_enabled(ctrl_id: int, enabled: bool) -> FmlxPacket:
  return FmlxPacket(13, address=10).add_int16(ctrl_id).add_bool(enabled)


def cmd_p_get_pump_on() -> FmlxPacket:
  return FmlxPacket(14, address=10)


def cmd_p_set_pump_on(enabled: bool) -> FmlxPacket:
  return FmlxPacket(15, address=10).add_bool(enabled)


def cmd_p_get_status(sensor_id: int) -> FmlxPacket:
  return FmlxPacket(16, address=10).add_int16(sensor_id)


def cmd_p_get_feedback_sensor_params(sensor_id: int) -> FmlxPacket:
  return FmlxPacket(20, address=10).add_int16(sensor_id)


def cmd_p_set_feedback_sensor_params(sensor_id: int, scale: float, offset: float) -> FmlxPacket:
  return FmlxPacket(21, address=10).add_int16(sensor_id).add_double(scale).add_double(offset)


def cmd_p_set_pid_params(ctrl_id: int, kp: float, ki: float, kd: float) -> FmlxPacket:
  return FmlxPacket(23, address=10).add_int16(ctrl_id).add_double(kp).add_double(ki).add_double(kd)


def cmd_p_set_settling_criteria(ctrl_id: int, time_ms: float, max_err: float) -> FmlxPacket:
  return FmlxPacket(25, address=10).add_int16(ctrl_id).add_double(time_ms).add_double(max_err)


def cmd_p_read_feedback_sensor(sensor_id: int) -> FmlxPacket:
  return FmlxPacket(30, address=10).add_int16(sensor_id)


def cmd_p_set_proportional_valve(valve_id: int, pwm: int) -> FmlxPacket:
  return FmlxPacket(32, address=10).add_int16(valve_id).add_uint16(pwm)


def cmd_p_set_solenoid_valve(valve_id: int, pwm: int) -> FmlxPacket:
  return FmlxPacket(34, address=10).add_int16(valve_id).add_uint16(pwm)


def cmd_p_get_aux(aux_id: int) -> FmlxPacket:
  return FmlxPacket(41, address=10).add_int16(aux_id)


def cmd_p_set_aux(aux_id: int, enabled: bool) -> FmlxPacket:
  return FmlxPacket(42, address=10).add_int16(aux_id).add_bool(enabled)
