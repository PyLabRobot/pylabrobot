"""Agile 7612 Bravo controller — Agile protocol with Agile-7612-specific wire encoding.

Supported hardware:
  - Bravo 16050-02 (firmware 5.4.6)
  - Bravo 16060-02 (firmware 5.4.7)

These models use the same Agile V11 protocol as legacy Bravos but with
different framing, CRC, and a different TCP port. The controller does not
report a model name — it is distinguished from legacy Agile by the TCP
port (7612 vs 10000) and the unique-value register (0x2A55 vs 0xAA55).

Differences from standard AgileController:
  - V11 frame byte order: [cmd][length] instead of [length][cmd]
  - CRC-8/MAXIM instead of CRC-8/SMBUS
  - AgileMoveInfo: 17 bytes (u16 home_complete_register) instead of 19
  - TCP port 7612 instead of 10000
  - Unique-value register: 0x2A55 instead of 0xAA55
  - No move_go / servo_enable / get_group_a_status Agile commands
  - 2-phase host-driven homing with per-axis servo config
  - Force-controlled jog via PREPARE_JOG (0xAA) + 0x80-header trigger
  - Servo write header = local_axis_index * 0x10 (not always 0x20)
"""

from __future__ import annotations

import logging
import struct
import time

import pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_7612_packet as _agile_7612_packet
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile import (
  AgileController,
  _CONTROLLER_2_ID,
  _CONTROLLER_1_AXES,
  _CONTROLLER_2_AXES,
  _axis_bit,
  _local_axis_index,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.base import (
  AxisMoveInfo,
  JogParams,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_packet import (
  AGILE_PACKET_SIZE,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import CommandID
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_7612_commands import (
  Agile7612MoveInfo,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_7612_crc import crc8_maxim
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.v11_agile_7612_comm import (
  V11Agile7612DeviceComm,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.transport.tcp import TCPTransport
from pylabrobot.liquid_handling.backends.agilent.bravo.types import (
  Axis,
  DeviceStateFlag,
  GripperDetectionState,
  HeadType,
  SpeedLevel,
)

logger = logging.getLogger(__name__)

_AGILE_7612_TCP_PORT = 7612
_HOMING_DISTANCE_MM = 10_000.0
_STOP_RETRIES = 3
_STOP_RETRY_DELAY_S = 0.200

# Firmware park position (in mm from home sensor) for each axis.
# All axes park at firmware 0 (home sensor) except Zg which parks at -20mm
# (nesting/docking position, hardcoded in _home_zg).
_FIRMWARE_PARK_MM: dict[int, float] = {Axis.Zg.value: -20.0}

# Per-axis servo register 0xA0 values (from VWorks v2 captures)
_HOMING_SERVO_REG_A0: dict[int, bytes] = {
  0: bytes.fromhex("60c1762bfd1000"),  # X
  1: bytes.fromhex("60c1762bfd1000"),  # Y
  2: bytes.fromhex("7ae147aeff1000"),  # Z
  3: bytes.fromhex("7ae147aeff1000"),  # W (assumed same as Z)
  4: bytes.fromhex("489122ebff1000"),  # G
  5: bytes.fromhex("78f1e7d5fe1000"),  # Zg
}

# Servo register values: initial, between-phase swap, and post-phase reset
_SERVO_A3_INITIAL = bytes.fromhex("40000000011000")
_SERVO_A4_INITIAL = bytes.fromhex("00000000001000")
_SERVO_A3_SWAPPED = bytes.fromhex("00000000001000")  # A3 gets A4's initial value
_SERVO_A4_SWAPPED = bytes.fromhex("40000000011000")  # A4 gets A3's initial value
_SERVO_A4_RESET = bytes.fromhex("00000000001000")  # A4 reset after phase 2

# Home register enable/update values (from VWorks v2 captures)
_HOME_REG_ENABLE = bytes.fromhex("00000000001000")
_HOME_REG_HOMED = bytes.fromhex("40000000011000")


def _homing_servo_registers(axis: Axis) -> list[tuple[int, bytes]]:
  """Build per-axis servo register values for homing."""
  local_idx = _local_axis_index(axis)
  axis_byte = local_idx + 1
  reg_a0 = _HOMING_SERVO_REG_A0.get(axis.value, bytes.fromhex("7ae147aeff1000"))
  ae_data = bytearray.fromhex("40000000001000")
  ae_data[4] = axis_byte
  b0_data = bytearray.fromhex("40000000001000")
  b0_data[4] = axis_byte
  return [
    (0xA0, reg_a0),
    (0xAD, bytes.fromhex("488000000c1000")),
    (0xAE, bytes(ae_data)),
    (0xAF, bytes.fromhex("00000000001000")),
    (0xB0, bytes(b0_data)),
    (0xBD, bytes.fromhex("00000000001000")),
  ]


def _home_reg_register(axis: Axis) -> int:
  """Get the Agile register number for this axis's home_complete_register.
  X/G=0x5E, Y/Zg=0x5F, Z=0x60, W=0x61."""
  mapping = {0: 0x5E, 1: 0x5F, 2: 0x60, 3: 0x61, 4: 0x5E, 5: 0x5F}
  return mapping.get(axis.value, 0x5E)


class Agile7612Controller(AgileController):
  """Agile controller for Agile 7612 Bravo hardware (firmware 5.x, port 7612)."""

  def __init__(self, profile=None) -> None:
    super().__init__()
    self._agile_pkt = _agile_7612_packet
    self._move_info_cls = Agile7612MoveInfo
    self._profile = profile
    self._home_raw: dict[int, float] = {}
    self._tracked_position: dict[int, float] = {}

    if profile is not None and hasattr(profile, "axes"):
      for axis in Axis:
        ax_cfg = profile.axes.get(axis.name)
        if ax_cfg is not None and hasattr(ax_cfg, "ticks_per_eng_unit"):
          tpu = float(ax_cfg.ticks_per_eng_unit)
          if tpu > 0:
            self._ticks_per_unit[axis] = tpu

  def close(self) -> None:
    super().close()
    self._home_raw.clear()
    self._tracked_position.clear()

  # =================================================================
  # Connection & verification
  # =================================================================

  _AGILE_7612_VERIFY_HEADER = 0x09
  _AGILE_7612_VERIFY_REGISTER = 0x90
  _AGILE_7612_UNIQUE_VALUE = 0x2A55

  def _verify_controller(self, controller_id: int) -> bool:
    raw = bytearray(AGILE_PACKET_SIZE)
    raw[0] = self._AGILE_7612_VERIFY_HEADER
    raw[1] = self._AGILE_7612_VERIFY_REGISTER
    raw[9] = crc8_maxim(raw, 9)
    axis_index = 4 if controller_id == _CONTROLLER_2_ID else 0
    try:
      comm = self._require_connected()
      payload = bytes(raw) + struct.pack("<B", axis_index)
      response = comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload)
      if len(response) < AGILE_PACKET_SIZE:
        return False
      value = struct.unpack_from("<H", response, 2)[0]
      if value != self._AGILE_7612_UNIQUE_VALUE:
        logger.error("Controller %d unique-value mismatch: 0x%04X", controller_id, value)
        return False
      logger.info("Controller %d verified (0x%04X)", controller_id, value)
      return True
    except BravoError as exc:
      logger.error("Controller %d verification failed: %s", controller_id, exc)
      return False

  def get_diagnostics(self) -> dict:
    if self._comm is None:
      return {"connected": False}
    return {
      "connected": True,
      "command_counts": dict(getattr(self._comm, "command_counts", {})),
      "errors": list(getattr(self._comm, "error_log", [])),
      "error_count": len(getattr(self._comm, "error_log", [])),
    }

  def open_tcp(self, address: str) -> None:
    logger.info("Opening Agile 7612 TCP connection to %s:%d", address, _AGILE_7612_TCP_PORT)
    transport = TCPTransport(address, port=_AGILE_7612_TCP_PORT)
    self._comm = V11Agile7612DeviceComm(transport)
    try:
      self._comm.connect()
      self._post_connect()
    except Exception:
      self._comm = None
      raise

  def open_serial(self, port: str) -> None:
    raise BravoError(
      ErrorType.COULD_NOT_CONNECT,
      custom_text="Agile 7612 Bravo does not support serial; use Ethernet",
    )

  # =================================================================
  # STOP command
  # =================================================================

  def stop(self) -> None:
    comm = self._require_connected()
    for attempt in range(1, _STOP_RETRIES + 1):
      try:
        comm.send_command(CommandID.STOP, timeout_ms=1000)
        logger.info("STOP acknowledged on attempt %d", attempt)
        return
      except (BravoError, TimeoutError):
        if attempt < _STOP_RETRIES:
          time.sleep(_STOP_RETRY_DELAY_S)
    logger.warning("STOP not acknowledged after %d attempts", _STOP_RETRIES)

  # =================================================================
  # Agile packet helpers
  # =================================================================

  def _send_agile(self, packet: bytes, axis: Axis | None = None, timeout_ms: int = 2000) -> bytes:
    comm = self._require_connected()
    if axis is not None:
      axis_index = axis.value
    else:
      cid = packet[1] if len(packet) > 1 else 0
      axis_index = 4 if cid == 1 else 0
    payload = packet + struct.pack("<B", axis_index)
    return comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload, timeout_ms)

  def _agile_7612_agile_read(self, register: int, axis: Axis) -> bytes:
    local_idx = _local_axis_index(axis)
    header = 0x01 + (local_idx * 0x10)
    raw = bytearray(10)
    raw[0] = header
    raw[1] = register & 0xFF
    raw[9] = crc8_maxim(raw, 9)
    return self._send_agile(bytes(raw), axis)

  def _agile_7612_ext_read(self, register: int, axis: Axis) -> bytes:
    """Extended register read using header 0x09."""
    raw = bytearray(10)
    raw[0] = 0x09
    raw[1] = register & 0xFF
    raw[9] = crc8_maxim(raw, 9)
    return self._send_agile(bytes(raw), axis)

  def _agile_7612_status_read(self, register: int, axis_index: int) -> bytes:
    """Read controller status. Register goes in byte[7], not byte[1]."""
    raw = bytearray(10)
    raw[0] = 0x00
    raw[7] = register & 0xFF
    raw[9] = crc8_maxim(raw, 9)
    comm = self._require_connected()
    payload = bytes(raw) + struct.pack("<B", axis_index)
    return comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload)

  def _agile_7612_servo_write(self, register: int, data: bytes, axis: Axis) -> None:
    """Write to servo register. Header = local_axis_index * 0x10."""
    header = _local_axis_index(axis) * 0x10
    raw = bytearray(10)
    raw[0] = header
    raw[1] = register & 0xFF
    for i, b in enumerate(data[:6]):
      raw[2 + i] = b
    raw[8] = data[6] if len(data) > 6 else 0
    raw[9] = crc8_maxim(raw, 9)
    self._send_agile(bytes(raw), axis)

  def _agile_7612_write_home_reg(self, axis: Axis, data: bytes) -> None:
    """Write to the axis's home_complete_register using header=0x01.

    VWorks uses header 0x01 (REG_GET_AX0) with data for these registers,
    NOT the servo write header (local_axis_index * 0x10).
    """
    reg = _home_reg_register(axis)
    raw = bytearray(10)
    raw[0] = 0x01
    raw[1] = reg & 0xFF
    for i, b in enumerate(data[:6]):
      raw[2 + i] = b
    raw[8] = data[6] if len(data) > 6 else 0
    raw[9] = crc8_maxim(raw, 9)
    self._send_agile(bytes(raw), axis)

  def _agile_7612_fault_reset_ctrl2(self) -> None:
    """Post-move fault reset on controller 2 (VWorks pattern)."""
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = 0x01
    raw[7] = 0x31
    raw[9] = crc8_maxim(raw, 9)
    try:
      comm = self._require_connected()
      payload = bytes(raw) + struct.pack("<B", 4)
      comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload)
    except BravoError:
      self._drain_tcp_buffer()

  def _drain_tcp_buffer(self) -> None:
    """Discard stale bytes in the TCP receive buffer after a comm error."""
    if self._comm is not None and hasattr(self._comm._transport, "drain_pending"):
      self._comm._transport.drain_pending()

  # =================================================================
  # Unsupported commands
  # =================================================================

  _UNSUPPORTED_COMMANDS = frozenset(
    {
      CommandID.CLEAR_MOTOR_POWER_FAULT,
      CommandID.QUERY_MOTOR_POWER,
      CommandID.GET_POSITION,
      CommandID.DETECT_SMART_HEAD,
      CommandID.READ_AD_WEIGH_PAD,
    }
  )

  def send_command(self, command_id: int, data: bytes = b"", timeout_ms: int = 2000) -> bytes:
    cid = CommandID(command_id) if isinstance(command_id, int) else command_id
    if cid in self._UNSUPPORTED_COMMANDS:
      logger.debug("Agile7612: skipping unsupported command 0x%02X", command_id)
      return b""
    return super().send_command(command_id, data, timeout_ms)

  # =================================================================
  # Position reading
  # =================================================================

  _CTRL2_EFFECTIVE_TPU = {
    Axis.G: 126.8 * (944.882 / 787.402),
    Axis.Zg: 126.8,
  }

  # Position register resolution multiplier per axis.
  # The register changes by (ticks_sent × multiplier) for each move.
  # Empirically measured from position_probe.py:
  #   X: 16× (8× register resolution × 2 from old decode)
  #   Y: 16×
  #   Z: 8× (4× register resolution × 2)
  #   W: TBD (using 8× as default until measured)
  _CTRL1_POSITION_SCALE = {
    Axis.X: 16.0,
    Axis.Y: 16.0,
    Axis.Z: 8.0,
    Axis.W: 8.0,
  }

  def _read_raw_position(self, axis: Axis) -> float:
    """Read raw position register and return value in engineering units."""
    response = self._agile_7612_agile_read(0x07, axis)
    if len(response) < 10:
      raise BravoError(ErrorType.COULD_NOT_READ_POSITION, axis=axis)
    raw_be_u16 = struct.unpack_from(">H", response, 2)[0]
    if axis in _CONTROLLER_1_AXES:
      scale = self._CTRL1_POSITION_SCALE.get(axis, 8.0)
      tpu = self._ticks_per_unit.get(axis, 314.96)
      return float(raw_be_u16) / (tpu * scale / 2.0)
    else:
      sign = -1.0 if (raw_be_u16 & 0x8000) else 1.0
      magnitude = raw_be_u16 & 0x7FFF
      eff_tpu = self._CTRL2_EFFECTIVE_TPU.get(axis, 126.8)
      return sign * float(magnitude) * 2.0 / eff_tpu

  def get_position(self, axis: Axis) -> float:
    if axis.value in self._tracked_position:
      return self._tracked_position[axis.value]
    raw = self._read_raw_position(axis)
    if axis.value in self._home_raw:
      home_offset = self.get_park_position(axis)
      return (raw - self._home_raw[axis.value]) + home_offset
    return raw

  def get_all_positions(self) -> dict[str, float]:
    out: dict[str, float] = {}
    for axis in Axis:
      try:
        out[axis.name] = self.get_position(axis)
      except Exception as exc:
        logger.debug("get_all_positions: %s read failed: %s", axis.name, exc)
    return out

  def _capture_home_position(self, axis: Axis) -> None:
    """Set tracked position to the park/home position after homing."""
    park = self.get_park_position(axis)
    self._tracked_position[axis.value] = park
    try:
      self._home_raw[axis.value] = self._read_raw_position(axis)
    except BravoError:
      pass
    logger.info("Home position %s: tracked=%.3f", axis.label, park)

  # =================================================================
  # Motion — PREPARE_MOVE + trigger (Fix 4: jog uses this, not PREPARE_JOG)
  # =================================================================

  _MOVE_POLL_INTERVAL_S = 0.050
  _STATUS_REG_GENERAL = 0x90
  _STATUS_SETTLED = 0xB0
  _TRIGGER_SUBTYPE = 0x38
  _JOG_TRIGGER_HEADER = 0x80
  _JOG_TRIGGER_SUBTYPE = 0x36

  def _home_reg_for_axis(self, axis: Axis) -> int:
    if self._profile is not None and hasattr(self._profile, "axes"):
      ax_cfg = self._profile.axes.get(axis.name)
      if ax_cfg is not None:
        return int(getattr(ax_cfg, "home_complete_register", 0) or 0)
    return 0

  def _agile_7612_move_go(self, axes: list[Axis]) -> None:
    """Trigger pending moves per-axis: header=0x00, byte[1]=axis_bitmask, byte[7]=0x38.

    VWorks sends separate triggers per axis (not combined bitmasks),
    using the actual axis_index for routing.
    """
    comm = self._require_connected()
    for axis in axes:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = self._TRIGGER_SUBTYPE
      raw[9] = crc8_maxim(raw, 9)
      comm.send_command(CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", axis.value))

  def _agile_7612_jog_trigger(self, axis: Axis) -> None:
    """Trigger force-controlled jog: header=0x80, byte[7]=0x36."""
    raw = bytearray(10)
    raw[0] = self._JOG_TRIGGER_HEADER
    raw[2] = 0x40
    raw[6] = 0x05
    raw[7] = self._JOG_TRIGGER_SUBTYPE
    raw[9] = crc8_maxim(raw, 9)
    comm = self._require_connected()
    comm.send_command(CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", axis.value))

  def _speed_for_level(self, axis: Axis, level: SpeedLevel) -> tuple[float, float]:
    if self._profile is not None and hasattr(self._profile, "axes"):
      ax_cfg = self._profile.axes.get(axis.name)
      if ax_cfg is not None and hasattr(ax_cfg, "speeds") and level in ax_cfg.speeds:
        sp = ax_cfg.speeds[level]
        return (float(sp.velocity), float(sp.acceleration))
    return (50.0, 100.0)

  def _default_vel_accel(self, axis: Axis) -> tuple[float, float]:
    return self._speed_for_level(axis, SpeedLevel.SAFE)

  def move(self, moves: list[AxisMoveInfo], wait: bool = True, timeout_ms: int = 30_000) -> None:
    """Execute motion via PREPARE_MOVE + trigger."""
    if not moves:
      return
    for m in moves:
      if not self._homed[m.axis.value]:
        raise BravoError(
          ErrorType.COULD_NOT_MOVE_TO_POSITION,
          custom_text=(f"{m.axis.name} axis not initialized; home the axis before issuing a move."),
        )
    comm = self._require_connected()
    for m in moves:
      self._validate_target(m)
    for m in moves:
      vel = m.velocity
      accel = m.acceleration
      if vel == 0.0:
        vel, accel = self._default_vel_accel(m.axis)
      if m.absolute:
        origin = self._move_origin(m.axis)
        firmware_mm = m.position - origin
      else:
        firmware_mm = m.position
      info = self._move_info_cls(
        axis=m.axis,
        position=self._to_ticks(m.axis, firmware_mm),
        velocity=self._vel_to_ticks_per_ms(m.axis, vel),
        acceleration=self._accel_to_ticks_per_ms2(m.axis, accel),
        absolute_move=m.absolute,
        check_for_homed=True,
        home_complete_register=self._home_reg_for_axis(m.axis),
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([m.axis for m in moves])
    if wait:
      self._agile_7612_wait_for_settled([m.axis for m in moves], timeout_ms)
    for m in moves:
      if m.absolute:
        self._tracked_position[m.axis.value] = m.position
      elif m.axis.value in self._tracked_position:
        self._tracked_position[m.axis.value] += m.position
    self._agile_7612_fault_reset_ctrl2()

  def _move_origin(self, axis: Axis) -> float:
    """Engineering position corresponding to firmware 0 ticks.

    Mirrors Darwin's calibration_offset + hardware_min in to_normalized().
    After homing, firmware 0 = home sensor. The engineering position there
    is homing_offset minus whatever firmware park offset the homing method
    used (0 for most axes, -20mm for Zg).
    """
    park_offset = self.get_park_position(axis)
    firmware_park = _FIRMWARE_PARK_MM.get(axis.value, 0.0)
    return park_offset - firmware_park

  def _validate_target(self, m: AxisMoveInfo) -> None:
    if self._profile is None or not hasattr(self._profile, "axes"):
      return
    ax_cfg = self._profile.axes.get(m.axis.name)
    if ax_cfg is None or not hasattr(ax_cfg, "range"):
      return
    lo = ax_cfg.range.min_pos
    hi = ax_cfg.range.max_pos
    if m.absolute:
      target = m.position
    else:
      current = self.get_position(m.axis)
      target = current + m.position
    if not (lo <= target <= hi):
      raise BravoError(
        ErrorType.COULD_NOT_MOVE_TO_POSITION,
        custom_text=(
          f"Move target {target:.4f} mm on {m.axis.name} is outside "
          f"software limits [{lo:.4f}, {hi:.4f}]."
        ),
      )

  def _agile_7612_wait_for_settled(self, axes: list[Axis], timeout_ms: int = 30_000) -> None:
    """Poll status until the specified axes are settled.

    Only checks the status bytes for axes that are actually moving,
    not all 4 bytes. Uninitialized axes (like W before homing) can
    show 0x80 permanently, which would block settle detection if we
    checked all bytes.
    """
    # Map axes to their status byte positions (local_axis_index within controller)
    ctrl1_positions = []
    ctrl2_positions = []
    for axis in axes:
      local = _local_axis_index(axis)
      if axis in _CONTROLLER_1_AXES:
        ctrl1_positions.append(local)
      else:
        ctrl2_positions.append(local)

    deadline = time.monotonic() + timeout_ms / 1000.0
    self._require_connected()  # raise early if disconnected
    poll_count = 0
    while time.monotonic() < deadline:
      try:
        all_settled = True
        stuck_info = []
        # Poll controller 1 — only check bytes for our axes
        if ctrl1_positions:
          resp1 = self._agile_7612_status_read(self._STATUS_REG_GENERAL, 0)
          if len(resp1) < 6:
            all_settled = False
            stuck_info.append("ctrl1: short response")
          else:
            for pos in ctrl1_positions:
              b = resp1[2 + pos]
              if b != 0x00 and (b & 0xF0) != self._STATUS_SETTLED:
                all_settled = False
                stuck_info.append(f"ctrl1[{pos}]=0x{b:02X}")
        # Poll controller 2 — only check bytes for our axes
        if ctrl2_positions:
          resp2 = self._agile_7612_status_read(self._STATUS_REG_GENERAL, 4)
          if len(resp2) < 6:
            all_settled = False
            stuck_info.append("ctrl2: short response")
          else:
            for pos in ctrl2_positions:
              b = resp2[2 + pos]
              if b != 0x00 and (b & 0xF0) != self._STATUS_SETTLED:
                all_settled = False
                stuck_info.append(f"ctrl2[{pos}]=0x{b:02X}")
        if all_settled:
          return
        poll_count += 1
        if poll_count % 50 == 0:
          elapsed = timeout_ms / 1000.0 - (deadline - time.monotonic())
          logger.warning(
            "Settle wait %.1fs axes=%s stuck: %s",
            elapsed,
            [a.name for a in axes],
            ", ".join(stuck_info),
          )
      except (BravoError, TimeoutError, ConnectionError):
        pass
      time.sleep(self._MOVE_POLL_INTERVAL_S)
    # Final status dump before raising
    stuck_info_final = []
    try:
      if ctrl1_positions:
        resp1 = self._agile_7612_status_read(self._STATUS_REG_GENERAL, 0)
        stuck_info_final.append(f"ctrl1={resp1.hex() if resp1 else 'None'}")
      if ctrl2_positions:
        resp2 = self._agile_7612_status_read(self._STATUS_REG_GENERAL, 4)
        stuck_info_final.append(f"ctrl2={resp2.hex() if resp2 else 'None'}")
    except Exception:
      pass
    logger.warning(
      "Settle TIMEOUT axes=%s final_status: %s",
      [a.name for a in axes],
      ", ".join(stuck_info_final),
    )
    raise BravoError(
      ErrorType.MOVE_TIMEOUT, custom_text=f"Timed out: {[a.label for a in axes]} ({timeout_ms}ms)"
    )

  # =================================================================
  # Homing — 2-phase with between-phase servo swaps (Fixes 1,2,3,5,6)
  # =================================================================

  def _homing_vel_accel(self, axis: Axis) -> tuple[float, float]:
    vel_mms, accel_mms2 = self._speed_for_level(axis, SpeedLevel.HOMING)
    return (
      self._vel_to_ticks_per_ms(axis, vel_mms),
      self._accel_to_ticks_per_ms2(axis, accel_mms2),
    )

  def _homing_depart_direction(self, axis: Axis) -> int:
    """Direction AWAY from the home sensor (depart direction).

    flag=True (sensor at positive end): depart = negative (-1)
    flag=False (sensor at negative end): depart = positive (+1)
    """
    if self._profile is not None and hasattr(self._profile, "axes"):
      ax_cfg = self._profile.axes.get(axis.name)
      if ax_cfg is not None and getattr(ax_cfg, "home_in_positive_direction", False):
        return -1
    return 1

  def _home_sensor_bitmask(self, axis: Axis) -> int:
    """Bitmask for this axis's sensor flag in the register 0x10 byte.

    Each Controller 1 axis has its own bit: X=0x01, Y=0x02, Z=0x04, W=0x08.
    Controller 2 axes: G=0x01, Zg=0x02.
    """
    if self._profile is not None and hasattr(self._profile, "axes"):
      ax_cfg = self._profile.axes.get(axis.name)
      if ax_cfg is not None:
        return int(getattr(ax_cfg, "home_flag_bitmask", 0) or 0)
    _DEFAULT_BITMASK = {0: 1, 1: 2, 2: 4, 3: 8, 4: 1, 5: 2}
    return _DEFAULT_BITMASK.get(axis.value, 4)

  def _agile_7612_servo_config_for_homing(self, axis: Axis) -> None:
    for reg, data in _homing_servo_registers(axis):
      try:
        self._agile_7612_servo_write(reg, data, axis)
      except BravoError as exc:
        logger.warning("Homing servo 0x%02X failed: %s", reg, exc)

  def _agile_7612_home_single_axis(self, axis: Axis) -> None:
    """Home one axis using VWorks 3-phase sequence.

    Robust homing with direction search:
      0. Try depart direction first (small move with short timeout)
      1. If that settles quickly, axis was near sensor — continue with 3-phase
      2. If it times out, try approach direction (axis was far from sensor)
      3. 3-phase: search + reverse + slow search
      4. Post: reset A4, SERVO_CHECK, write home_complete HOMED
      5. Park: absolute move to position 0
    """
    comm = self._require_connected()
    vel, accel = self._homing_vel_accel(axis)
    depart_dir = self._homing_depart_direction(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 314.96)

    logger.info(
      "Agile7612 homing %s: vel=%.4f accel=%.6f depart=%d", axis.label, vel, accel, depart_dir
    )

    # Clear faults before homing
    self.reset_faults([axis])

    # Pre-homing register read
    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass

    # Write home_complete_register to enable homing mode
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    # Servo pre-configuration (A0-BD + A3 + A4)
    self._agile_7612_servo_config_for_homing(axis)

    # Post-config extended register read
    try:
      self._agile_7612_ext_read(0x10, axis)
    except BravoError:
      pass

    # Phase 1: Try depart direction first (short timeout).
    # If axis is at/near sensor, this causes a sensor-off transition
    # and settles quickly. If axis is far from sensor, this times out.
    phase1_settled = False
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks * depart_dir,
      velocity=vel,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    try:
      self._agile_7612_wait_for_settled([axis], timeout_ms=15_000)
      phase1_settled = True
      logger.info("Homing %s: phase 1 depart settled", axis.label)
    except BravoError:
      logger.info("Homing %s: phase 1 depart timed out, trying approach", axis.label)

    if not phase1_settled:
      # Axis was far from sensor. Try approach direction instead.
      try:
        self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
        self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      except BravoError:
        pass
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks * (-depart_dir),
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)
      logger.info("Homing %s: phase 1 approach settled", axis.label)

    # Re-confirm A3/A4 with initial values
    try:
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
    except BravoError:
      pass

    # Phase 2: Reverse direction (fast)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks * (-depart_dir if phase1_settled else depart_dir),
      velocity=vel,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    # Swap A4/A3 between phases 2 and 3
    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
      self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    except BravoError:
      pass

    # Phase 3: Same direction as phase 1 (slow, vel/10)
    phase3_dir = depart_dir if phase1_settled else (-depart_dir)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks * phase3_dir,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    # Post-phase: reset A4
    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass

    # Homing-complete marker: header=0x00, byte[1]=bitmask, byte[7]=0x52
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass

    # Update home_complete_register with homed flag
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    # Post-homing: absolute move to position 0
    try:
      info = self._move_info_cls(
        axis=axis,
        position=0.0,
        velocity=vel,
        acceleration=accel,
        absolute_move=True,
        check_for_homed=True,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=30_000)
    except BravoError:
      pass

    self._homed[axis.value] = True
    self._capture_home_position(axis)
    logger.info("Axis %s homed", axis.label)

  # =================================================================
  # Per-axis homing methods
  # =================================================================
  # Each axis gets its own method so that VWorks-matched byte sequences
  # can be hardcoded per-axis without shared direction logic.
  # Stubs currently delegate to _agile_7612_home_single_axis(); replace with
  # capture-matched implementations one axis at a time.

  def _home_x(self) -> None:
    """Home X axis — VWorks-matched sequence from x_axis captures.

    Direction is determined by reading register 0x10 (via header 0x09)
    after servo config. The firmware returns a byte indicating sensor state:
      0x7F = on/past sensor → 2-phase (negative fast, positive slow)
      0x7E/0x7C/other = off sensor → 3-phase (positive fast, negative fast, positive slow)
    """
    comm = self._require_connected()
    axis = Axis.X
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 314.96)

    # Pre-homing: read reg 0x60 (VWorks does this for re-home, not fresh init)
    try:
      self._agile_7612_agile_read(0x60, axis)
    except BravoError:
      pass

    # Pre-homing register reads
    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    # Servo config (A0, AD, AE, AF, B0, BD)
    self._agile_7612_servo_config_for_homing(axis)

    # Read register 0x10 — response byte determines homing direction.
    # 0x7F = axis is on/past sensor → start negative (2-phase)
    # Anything else = axis is off sensor → start positive (3-phase)
    on_sensor = False
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        sensor_byte = resp[2]
        on_sensor = bool(sensor_byte & self._home_sensor_bitmask(axis))
        logger.info(
          "Agile7612 homing X: reg 0x10 sensor byte=0x%02X → %s",
          sensor_byte,
          "on sensor" if on_sensor else "off sensor",
        )
    except BravoError:
      logger.warning("Agile7612 homing X: reg 0x10 read failed, defaulting to 3-phase")

    if on_sensor:
      logger.info("Agile7612 homing X: 2-phase (negative fast, positive slow)")

      # Phase 1: NEGATIVE fast (depart from sensor)
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)
    else:
      logger.info("Agile7612 homing X: 3-phase (positive fast, negative fast, positive slow)")

      # Phase 1: POSITIVE fast (search toward sensor)
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

      # Phase 2: NEGATIVE fast (depart from sensor)
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    # Final phase: POSITIVE slow (precision edge find)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    # Post-homing: A4 reset, SERVO_CHECK marker, read home_complete
    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    self._homed[axis.value] = True
    self._capture_home_position(axis)
    logger.info("Axis X homed")

  def _home_y(self) -> None:
    """Home Y axis — VWorks-matched sequence from y_axis captures.

    Identical pattern to X: register 0x10 byte[2] determines direction.
    0x7F = on sensor → 2-phase, anything else → 3-phase.
    """
    comm = self._require_connected()
    axis = Axis.Y
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 314.96)

    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    on_sensor = False
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        sensor_byte = resp[2]
        on_sensor = bool(sensor_byte & self._home_sensor_bitmask(axis))
        logger.info(
          "Agile7612 homing Y: reg 0x10 sensor byte=0x%02X → %s",
          sensor_byte,
          "on sensor" if on_sensor else "off sensor",
        )
    except BravoError:
      logger.warning("Agile7612 homing Y: reg 0x10 read failed, defaulting to 3-phase")

    if on_sensor:
      logger.info("Agile7612 homing Y: 2-phase (negative fast, positive slow)")

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)
    else:
      logger.info("Agile7612 homing Y: 3-phase (positive fast, negative fast, positive slow)")

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    self._homed[axis.value] = True
    self._capture_home_position(axis)
    logger.info("Axis Y homed")

  def _home_z(self) -> None:
    """Home Z axis — VWorks-matched sequence from z_axis captures.

    Z has home_in_positive_direction=False (sensor at top/negative end).
    Directions are FLIPPED vs X/Y:
      On sensor: POSITIVE first (depart downward), NEGATIVE slow (approach upward)
      Off sensor: NEGATIVE first (approach upward), POSITIVE, NEGATIVE slow
    Post-homing: absolute move to position 0 (park at top).
    """
    comm = self._require_connected()
    axis = Axis.Z
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 1600.0)

    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    on_sensor = False
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        sensor_byte = resp[2]
        on_sensor = bool(sensor_byte & self._home_sensor_bitmask(axis))
        logger.info(
          "Agile7612 homing Z: reg 0x10 sensor byte=0x%02X → %s",
          sensor_byte,
          "on sensor" if on_sensor else "off sensor",
        )
    except BravoError:
      logger.warning("Agile7612 homing Z: reg 0x10 read failed, defaulting to 3-phase")

    if on_sensor:
      logger.info("Agile7612 homing Z: 2-phase (positive fast, negative slow)")

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)
    else:
      logger.info("Agile7612 homing Z: 3-phase (negative fast, positive fast, negative slow)")

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    # Final phase: NEGATIVE slow (precision edge find — approach sensor upward)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=-large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    # Post-homing: absolute move to park position 0
    # VWorks sends this trigger with controller_base routing (ax=0), not axis.value
    info = self._move_info_cls(
      axis=axis,
      position=0.0,
      velocity=vel,
      acceleration=accel,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    ctrl_base = 4 if axis in _CONTROLLER_2_AXES else 0
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = _axis_bit(axis)
    raw[7] = 0x38
    raw[9] = crc8_maxim(raw, 9)
    comm.send_command(CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", ctrl_base))
    self._agile_7612_wait_for_settled([axis], timeout_ms=30_000)

    self._homed[axis.value] = True
    self._capture_home_position(axis)
    logger.info("Axis Z homed")

  def _home_w(self) -> None:
    """Home W axis (plunger) — VWorks-matched sequence from w_axis captures.

    Controller 1, home_in_positive_direction=False (sensor at negative end, like Z).
    Uses register 0x10 for direction detection (Controller 1 → 0x7F = on sensor).
    Post-homing: absolute move to position 0.
    Note: calibration registers (40+ reads) are init-only, not part of regular homing.
    """
    comm = self._require_connected()
    axis = Axis.W
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 448.0)

    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    on_sensor = False
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        sensor_byte = resp[2]
        on_sensor = bool(sensor_byte & self._home_sensor_bitmask(axis))
        logger.info(
          "Agile7612 homing W: reg 0x10 sensor byte=0x%02X → %s",
          sensor_byte,
          "on sensor" if on_sensor else "off sensor",
        )
    except BravoError:
      logger.warning("Agile7612 homing W: reg 0x10 read failed, defaulting to 3-phase")

    if on_sensor:
      logger.info("Agile7612 homing W: 2-phase (positive fast, negative slow)")

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)
    else:
      logger.info("Agile7612 homing W: 3-phase (negative fast, positive fast, negative slow)")

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    # Final phase: NEGATIVE slow (approach sensor)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=-large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    # Post-homing: absolute move to park position 0
    info = self._move_info_cls(
      axis=axis,
      position=0.0,
      velocity=vel,
      acceleration=accel,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    ctrl_base = 4 if axis in _CONTROLLER_2_AXES else 0
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = _axis_bit(axis)
    raw[7] = 0x38
    raw[9] = crc8_maxim(raw, 9)
    comm.send_command(CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", ctrl_base))
    self._agile_7612_wait_for_settled([axis], timeout_ms=30_000)

    self._homed[axis.value] = True
    self._capture_home_position(axis)
    logger.info("Axis W homed")

  def _home_g(self) -> None:
    """Home G axis (gripper) — VWorks-matched sequence from g_axis captures.

    Controller 2, home_in_positive_direction=False (sensor at negative end).
    All captures show 2-phase (positive fast, negative slow) regardless of
    starting position. Pre-move and post-move to G=0 with fault reset.
    """
    comm = self._require_connected()
    axis = Axis.G
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 944.882)

    # Pre-move: read home_complete, move G to 0, fault reset
    try:
      self._agile_7612_agile_read(0x5E, axis)
    except BravoError:
      pass
    try:
      info = self._move_info_cls(
        axis=axis,
        position=0.0,
        velocity=vel,
        acceleration=accel,
        absolute_move=True,
        check_for_homed=True,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=30_000)
    except BravoError as exc:
      logger.warning("G homing: pre-move to 0 failed: %s", exc)
    self._agile_7612_fault_reset_ctrl2()

    # Homing preamble
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    # Read register 0x10 (VWorks does this but always uses 2-phase for G).
    # Controller 2 returns different sensor values than Controller 1
    # (0x7C/0x74 instead of 0x7F), and VWorks ignores them for direction.
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        logger.info("Agile7612 homing G: reg 0x10 byte=0x%02X (ignored — always 2-phase)", resp[2])
    except BravoError:
      pass

    # G always uses 2-phase: POSITIVE fast (depart), NEGATIVE slow (approach)
    logger.info("Agile7612 homing G: 2-phase (positive fast, negative slow)")

    self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks,
      velocity=vel,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    # Final phase: NEGATIVE slow (approach sensor)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=-large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    # Post-move: read home_complete, move G to 0, fault reset
    try:
      self._agile_7612_agile_read(0x5E, axis)
    except BravoError:
      pass
    try:
      info = self._move_info_cls(
        axis=axis,
        position=0.0,
        velocity=vel,
        acceleration=accel,
        absolute_move=True,
        check_for_homed=True,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout_ms=30_000)
    except BravoError as exc:
      logger.warning("G homing: post-move to 0 failed: %s", exc)
    self._agile_7612_fault_reset_ctrl2()

    self._homed[axis.value] = True
    self._capture_home_position(axis)
    logger.info("Axis G homed")

  def _home_zg(self) -> None:
    """Home Zg axis — VWorks-matched sequence from zg_axis captures.

    Controller 2, home_in_positive_direction=False (sensor at top).
    Includes G pre-move (move G to 0 before Zg homing) and post-homing
    park move to -20mm. Directions flipped vs X/Y (same as Z).
    """
    comm = self._require_connected()
    axis = Axis.Zg
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 787.402)

    # G pre-move: read G home reg, move G to 0, fault reset
    try:
      self._agile_7612_agile_read(0x5E, Axis.G)
    except BravoError:
      pass
    try:
      g_home_reg = self._home_reg_for_axis(Axis.G)
      g_vel, g_accel = self._homing_vel_accel(Axis.G)
      info = self._move_info_cls(
        axis=Axis.G,
        position=0.0,
        velocity=g_vel,
        acceleration=g_accel,
        absolute_move=True,
        check_for_homed=True,
        home_complete_register=g_home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([Axis.G])
      self._agile_7612_wait_for_settled([Axis.G], timeout_ms=30_000)
    except BravoError as exc:
      logger.warning("Zg homing: G pre-move failed: %s", exc)
    self._agile_7612_fault_reset_ctrl2()

    # Zg homing preamble
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    # Read register 0x10 (VWorks does this but always uses 2-phase for Zg).
    # Controller 2 returns different sensor values than Controller 1.
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        logger.info("Agile7612 homing Zg: reg 0x10 byte=0x%02X (ignored — always 2-phase)", resp[2])
    except BravoError:
      pass

    # Zg always uses 2-phase: POSITIVE fast (depart down), NEGATIVE slow (approach up)
    logger.info("Agile7612 homing Zg: 2-phase (positive fast, negative slow)")

    self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks,
      velocity=vel,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    # Final phase: NEGATIVE slow (approach sensor upward)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=-large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout_ms=60_000)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    # Post-homing: park move to -20mm (-15748 ticks at 787.402 tpu)
    park_ticks = -20.0 * self._ticks_per_unit.get(axis, 787.402)
    info = self._move_info_cls(
      axis=axis,
      position=park_ticks,
      velocity=vel,
      acceleration=accel,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    ctrl_base = 4 if axis in _CONTROLLER_2_AXES else 0
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = _axis_bit(axis)
    raw[7] = 0x38
    raw[9] = crc8_maxim(raw, 9)
    comm.send_command(CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", ctrl_base))
    self._agile_7612_wait_for_settled([axis], timeout_ms=30_000)

    self._homed[axis.value] = True
    self._capture_home_position(axis)
    logger.info("Axis Zg homed")

  def _home_xy_parallel(self) -> None:
    """Home X and Y with interleaved per-axis commands (VWorks pattern).

    VWorks configures and triggers each axis BEFORE configuring the next,
    because servo config on Controller 1 is shared state.
    """
    comm = self._require_connected()
    xy_axes = [Axis.X, Axis.Y]

    # Phase 1: Setup + trigger each axis individually, then wait for all
    for axis in xy_axes:
      try:
        self._agile_7612_agile_read(0x4A, axis)
      except BravoError:
        pass
      try:
        self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
      except BravoError:
        pass
      self._agile_7612_servo_config_for_homing(axis)
      try:
        self._agile_7612_ext_read(0x10, axis)
      except BravoError:
        pass
      vel, accel = self._homing_vel_accel(axis)
      direction = self._homing_depart_direction(axis)
      home_reg = self._home_reg_for_axis(axis)
      info = self._move_info_cls(
        axis=axis,
        position=_HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 314.96) * direction,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled(xy_axes, timeout_ms=60_000)

    # Phase 2: Between-phase swap + slow retract, per axis then wait
    for axis in xy_axes:
      try:
        self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
        self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
      except BravoError:
        pass
      vel, accel = self._homing_vel_accel(axis)
      direction = self._homing_depart_direction(axis)
      home_reg = self._home_reg_for_axis(axis)
      info = self._move_info_cls(
        axis=axis,
        position=-_HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 314.96) * direction,
        velocity=vel / 10.0,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled(xy_axes, timeout_ms=60_000)

    # Post-homing for each
    for axis in xy_axes:
      try:
        self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
      except BravoError:
        pass
      try:
        raw = bytearray(10)
        raw[0] = 0x00
        raw[1] = _axis_bit(axis)
        raw[7] = 0x52
        raw[9] = crc8_maxim(raw, 9)
        self._send_agile(bytes(raw), axis)
      except BravoError:
        pass
      try:
        self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
      except BravoError:
        pass
      self._homed[axis.value] = True
      self._capture_home_position(axis)
      logger.info("Axis %s homed", axis.label)

  def _agile_7612_move_axis_to_zero(self, axis: Axis) -> None:
    """Move axis to absolute position 0 with chk_home=True."""
    vel_mms, accel_mms2 = self._speed_for_level(axis, SpeedLevel.SLOW)
    self.move(
      [
        AxisMoveInfo(
          axis=axis, position=0.0, velocity=vel_mms, acceleration=accel_mms2, absolute=True
        )
      ]
    )

  def home_axes(self, axes: list[Axis]) -> None:
    """Home axes with VWorks-matched sequence.

    Ordering: Z first (safety), then Zg, G, X+Y parallel, W last.
    Each axis dispatches to its own _home_* method.
    Fault flags are cleared first (matches VWorks/InitializeTask pattern).
    """
    self.reset_faults(list(Axis))
    ctrl1_main = [a for a in axes if a in (Axis.X, Axis.Y, Axis.Z, Axis.W)]
    ctrl2_axes = [a for a in axes if a in (Axis.G, Axis.Zg)]

    if Axis.Z in ctrl1_main:
      self._home_z()

    if Axis.Zg in ctrl2_axes:
      self._home_zg()

    if Axis.G in ctrl2_axes:
      self._home_g()

    xy_axes = [a for a in ctrl1_main if a in (Axis.X, Axis.Y)]
    if Axis.X in xy_axes:
      self._home_x()
    if Axis.Y in xy_axes:
      self._home_y()

    if Axis.W in ctrl1_main:
      self._home_w()

  # =================================================================
  # Force-controlled jog (ONLY for tip pickup, NOT UI jog)
  # =================================================================

  def jog(self, params: JogParams) -> float:
    """Force-controlled jog via PREPARE_JOG (0xAA) + 0x80 trigger.

    Used ONLY for tip pickup — Z descends with force limit until tips engage.
    UI jogs go through move() via bravo.jog_axis() which uses AxisMoveInfo.
    """
    axis = params.axis
    if not self._homed[axis.value]:
      raise BravoError(
        ErrorType.COULD_NOT_MOVE_TO_POSITION,
        custom_text=f"{axis.name} axis not initialized; home before jogging.",
      )
    comm = self._require_connected()

    current_pos = self.get_position(axis)
    if current_pos >= params.max_position:
      logger.warning(
        "jog: %s already at %.3f, past max_position %.3f -- skipping",
        axis.name,
        current_pos,
        params.max_position,
      )
      return current_pos

    home_reg = self._home_reg_for_axis(axis)
    try:
      self._agile_7612_servo_write(0x23, bytes(7), axis)
    except BravoError:
      pass
    payload = struct.pack("<Bf", axis.value, params.peak_current)
    payload += struct.pack(">H", home_reg)
    payload += struct.pack("<B", 0x01)
    comm.send_command(CommandID.PREPARE_JOG, payload)
    self._agile_7612_jog_trigger(axis)
    self._agile_7612_wait_for_settled([axis], timeout_ms=30_000)
    self._tracked_position.pop(axis.value, None)
    final_pos = self.get_position(axis)

    if params.tolerance > 0 and final_pos > params.max_position + params.tolerance:
      logger.warning(
        "jog: %s final position %.3f exceeds max_position %.3f + tolerance %.3f",
        axis.name,
        final_pos,
        params.max_position,
        params.tolerance,
      )

    try:
      comm.send_command(CommandID.QUERY_JOG_STATUS, timeout_ms=1000)
    except (BravoError, TimeoutError):
      pass
    return final_pos

  def tip_force_jog(self, axis: Axis, peak_current: float, max_position: float) -> float:
    """Experimental force-controlled jog for tip pickup testing.

    Separate from jog() so experiments don't affect the working jog.
    Returns the max_position value (actual hardware position is unreliable
    due to u16 register wrapping on Controller 1 axes).
    """
    if not self._homed[axis.value]:
      raise BravoError(
        ErrorType.COULD_NOT_MOVE_TO_POSITION,
        custom_text=f"{axis.name} axis not initialized; home before jogging.",
      )
    comm = self._require_connected()

    current_pos = self.get_position(axis)
    if current_pos >= max_position:
      logger.warning(
        "tip_force_jog: %s at %.3f, past max %.3f -- skipping",
        axis.name,
        current_pos,
        max_position,
      )
      return current_pos

    home_reg = self._home_reg_for_axis(axis)
    _APPROACH_MM = 8.0

    # Two-stage descent:
    # 1. Regular move to (max_position - approach) at safe speed
    # 2. Force-controlled jog for the final approach mm
    approach_target = max_position - _APPROACH_MM
    if approach_target > current_pos:
      logger.info("tip_force_jog: approaching Z=%.1f before force jog", approach_target)
      self.move(
        [
          AxisMoveInfo(
            axis=axis,
            position=approach_target,
            **(
              dict(zip(("velocity", "acceleration"), self._speed_for_level(axis, SpeedLevel.SAFE)))
            ),
          )
        ],
        wait=True,
      )

    vel = self._vel_to_ticks_per_ms(axis, 10.0)
    accel = self._accel_to_ticks_per_ms2(axis, 100.0)
    target_ticks = self._to_ticks(axis, max_position)

    logger.warning(
      "tip_force_jog: %s force jog %.1f -> %.1f mm (%.0f ticks), peak=%.3fA",
      axis.name,
      approach_target,
      max_position,
      target_ticks,
      peak_current,
    )

    info = self._move_info_cls(
      axis=axis,
      position=target_ticks,
      velocity=vel,
      acceleration=accel,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])

    time.sleep(0.4)

    try:
      self._agile_7612_servo_write(0x02, bytes.fromhex("4ccccccc001000"), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_servo_write(0x23, bytes(7), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_servo_write(0x23, bytes.fromhex("00000000001000"), axis)
    except BravoError:
      pass

    payload = struct.pack("<Bf", axis.value, peak_current)
    payload += struct.pack(">H", home_reg)
    payload += struct.pack("<B", 0x01)
    comm.send_command(CommandID.PREPARE_JOG, payload)
    self._agile_7612_jog_trigger(axis)

    time.sleep(0.3)
    prev_raw = None
    stable_count = 0
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
      try:
        raw = self._read_raw_position(axis)
        if prev_raw is not None and abs(raw - prev_raw) < 0.5:
          stable_count += 1
          if stable_count >= 3:
            break
        else:
          stable_count = 0
        prev_raw = raw
      except (BravoError, TimeoutError, ConnectionError):
        pass
      time.sleep(0.1)

    self._tracked_position[axis.value] = max_position

    try:
      comm.send_command(CommandID.QUERY_JOG_STATUS, timeout_ms=1000)
    except (BravoError, TimeoutError):
      pass
    return max_position

  # =================================================================
  # Gripper — retry logic
  # =================================================================

  _GRIP_RETRIES = 4
  _GRIP_RETRY_DELAYS = [0.2, 0.3, 0.4, 0.6]
  _DETECT_GRIPPER_RETRIES = 4
  _DETECT_GRIPPER_DELAYS = [0.2, 0.3, 0.6, 1.0]

  def detect_gripper(self) -> "GripperDetectionState":
    comm = self._require_connected()
    for attempt in range(self._DETECT_GRIPPER_RETRIES):
      try:
        data = comm.send_command(CommandID.DETECT_GRIPPER, timeout_ms=2000)
        if len(data) >= 1 and data[0] != 0:
          return GripperDetectionState(data[0])
      except (BravoError, TimeoutError):
        pass
      if attempt < self._DETECT_GRIPPER_RETRIES - 1:
        time.sleep(self._DETECT_GRIPPER_DELAYS[attempt])
    return GripperDetectionState.NOT_DETECTED

  def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
    comm = self._require_connected()
    vel, accel = self._speed_for_level(Axis.G, speed)
    home_reg = self._home_reg_for_axis(Axis.G)
    info = self._move_info_cls(
      axis=Axis.G,
      position=self._to_ticks(Axis.G, position),
      velocity=self._vel_to_ticks_per_ms(Axis.G, vel),
      acceleration=self._accel_to_ticks_per_ms2(Axis.G, accel),
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([Axis.G])
    grip_stalled = False
    try:
      self._agile_7612_wait_for_settled([Axis.G], timeout_ms=3_000)
    except BravoError:
      grip_stalled = True
    self._tracked_position[Axis.G.value] = position
    self._agile_7612_fault_reset_ctrl2()
    if not grip_stalled:
      raise BravoError(
        ErrorType.COULD_NOT_MOVE_TO_POSITION,
        custom_text="Gripper closed to target without resistance — no plate detected",
      )

  # =================================================================
  # Motor control
  # =================================================================

  def get_park_position(self, axis: Axis) -> float:
    if self._profile is not None and hasattr(self._profile, "axes"):
      ax_cfg = self._profile.axes.get(axis.name)
      if ax_cfg is not None:
        return float(getattr(ax_cfg, "homing_offset", 0) or 0)
    return 0.0

  def open_gripper(self, position: float | None = None) -> None:
    comm = self._require_connected()
    target = 0.0 if position is None else float(position)
    vel, accel = self._speed_for_level(Axis.G, SpeedLevel.SAFE)
    home_reg = self._home_reg_for_axis(Axis.G)
    info = self._move_info_cls(
      axis=Axis.G,
      position=self._to_ticks(Axis.G, target),
      velocity=self._vel_to_ticks_per_ms(Axis.G, vel),
      acceleration=self._accel_to_ticks_per_ms2(Axis.G, accel),
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([Axis.G])
    self._agile_7612_wait_for_settled([Axis.G], timeout_ms=30_000)
    self._tracked_position[Axis.G.value] = target
    self._agile_7612_fault_reset_ctrl2()

  def enable_motor(self, axis: Axis) -> None:
    logger.debug("Agile7612: enable_motor(%s) no-op", axis.label)

  def disable_motor(self, axis: Axis) -> None:
    logger.debug("Agile7612: disable_motor(%s) no-op", axis.label)

  def is_motor_enabled(self, axis: Axis) -> bool:
    return self._homed[axis.value]

  def _is_estop_engaged(self) -> bool:
    try:
      state = self.query_state()
      return bool(state & DeviceStateFlag.ROBOT_DISABLE)
    except Exception:
      return False

  def recover(self, axes: list[Axis] | None = None) -> dict[Axis, str]:
    if axes is None:
      axes = list(Axis)
    if self._is_estop_engaged():
      raise BravoError(
        ErrorType.ROBOT_DISABLE,
        custom_text="Cannot recover: E-stop still engaged. Release E-stop and retry.",
      )
    self.reset_faults(axes)
    for a in axes:
      self._homed[a.value] = False
    self._home_raw.clear()
    self._tracked_position.clear()
    return {a: "enabled" for a in axes}

  def read_plate_sensor(self, transient_ms: int = 0) -> bool:
    logger.debug("Agile7612: read_plate_sensor() not supported, returning False")
    return False

  def scan_stack_with_gripper(
    self,
    *,
    start_zg: float,
    end_zg: float,
    speed: SpeedLevel,
    transient_ms: int = 0,
  ) -> dict[str, float | bool | None]:
    raise BravoError(
      ErrorType.DARWIN_GENERIC,
      custom_text="scan_stack_with_gripper is not supported on Agile 7612 hardware",
    )

  def set_head_type(self, head_type: HeadType) -> None:
    self._head_type = head_type
    logger.info("Agile7612: head type set to %s", head_type)

  def read_head_identification(self) -> dict:
    return {"eeprom_byte": None, "adc_counts": 0, "has_smart_head": False}

  def reset_faults(self, axes: list[Axis]) -> None:
    for axis in axes:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x31
      raw[9] = crc8_maxim(raw, 9)
      try:
        self._send_agile(bytes(raw), axis)
      except BravoError:
        pass

  def detect_smart_head(self) -> bool:
    return False

  def read_smart_head_type(self) -> int:
    return 0

  def clear_go_button(self) -> None:
    try:
      super().clear_go_button()
    except (BravoError, TimeoutError):
      logger.debug("Agile7612: clear_go_button not acknowledged")

  def read_head_adc(self) -> int:
    return 0
