"""Agile controller implementation for the Bravo liquid handler.

Ported from CBravoAgileController.cpp -- communicates with a Rabbit
microcontroller via serial or TCP, which forwards 10-byte Agile packets
to one or two motor controllers on the Agile bus.

Controller 1 (ID 0): X, Y, Z, W axes
Controller 2 (ID 1): G, Zg axes (gripper module)
"""

from __future__ import annotations

import logging
import struct
import time

from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.base import (
  AxisMoveInfo,
  BravoController,
  FirmwareVersion,
  JogParams,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import (
  AgileMoveInfo,
  AgileJogInfo,
  CommandID,
  EEPROMAddress,
  GripperParams,
  LightCommandData,
  SmartHeadEEPROMData,
)
import pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_packet as _default_agile_packet
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_packet import (
  AGILE_PACKET_SIZE,
  AgileRegister,
  AgileReply,
  UNIQUE_VALUE_EXPECTED,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.v11_comm import V11DeviceComm
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType
from pylabrobot.liquid_handling.backends.agilent.bravo.transport.serial import SerialTransport
from pylabrobot.liquid_handling.backends.agilent.bravo.transport.tcp import TCPTransport
from pylabrobot.liquid_handling.backends.agilent.bravo.types import (
  Axis,
  DeviceStateFlag,
  GripperDetectionState,
  GRIP_POSITION_TOLERANCE,
  NUM_AXES_WITH_GRIPPER,
  OPEN_GRIPPER_POSITION,
  SpeedLevel,
  TICKS_PER_MM,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Controller mapping
# ---------------------------------------------------------------------------

_CONTROLLER_1_ID = 0
_CONTROLLER_2_ID = 1

_CONTROLLER_1_AXES = frozenset({Axis.X, Axis.Y, Axis.Z, Axis.W})
_CONTROLLER_2_AXES = frozenset({Axis.G, Axis.Zg})

_DEFAULT_W_TICKS_PER_UL = 48.0

# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

_MOVE_POLL_INTERVAL_S = 0.010
_HOME_POLL_INTERVAL_S = 0.050
_DEFAULT_MOVE_TIMEOUT_MS = 30_000
_DEFAULT_HOME_TIMEOUT_MS = 60_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _controller_for_axis(axis: Axis) -> int:
  """Return the Agile bus controller ID that owns *axis*."""
  return _CONTROLLER_2_ID if axis in _CONTROLLER_2_AXES else _CONTROLLER_1_ID


def _local_axis_index(axis: Axis) -> int:
  """Return the 0-based axis index within its controller."""
  return axis.value - 4 if axis in _CONTROLLER_2_AXES else axis.value


def _axis_bit(axis: Axis) -> int:
  """Return a single-bit mask for *axis* within its controller."""
  return 1 << _local_axis_index(axis)


def _parse_version_string(version_str: str) -> tuple[int, ...]:
  """Parse ``'2.0.0'`` into ``(2, 0, 0)``."""
  try:
    return tuple(int(x) for x in version_str.strip().split("."))
  except (ValueError, AttributeError):
    return (0, 0, 0)


# ---------------------------------------------------------------------------
# AgileController
# ---------------------------------------------------------------------------


class AgileController(BravoController):
  """Hardware controller for the Bravo liquid handler via the Agile protocol.

  Ported from ``CBravoAgileController`` (C++).  The Rabbit microcontroller
  sits between the PC and the Agile motor controllers; it accepts V11
  framed commands and relays 10-byte Agile packets.
  """

  def __init__(self) -> None:
    self._comm: V11DeviceComm | None = None
    self._last_error: BravoError | None = None
    self._firmware_version = FirmwareVersion()
    self._firmware_tuple: tuple[int, ...] = (0, 0, 0)
    self._homed: list[bool] = [False] * NUM_AXES_WITH_GRIPPER

    self._ticks_per_unit: dict[Axis, float] = {
      **TICKS_PER_MM,
      Axis.W: _DEFAULT_W_TICKS_PER_UL,
    }

    self._agile_pkt = _default_agile_packet
    self._move_info_cls = AgileMoveInfo

  # -----------------------------------------------------------------
  # Internal: firmware gate
  # -----------------------------------------------------------------

  @property
  def _fw_at_least_2(self) -> bool:
    """True when firmware >= 2.0.0 (axis-index routing required)."""
    return self._firmware_tuple >= (2, 0, 0)

  # -----------------------------------------------------------------
  # Internal: communication primitives
  # -----------------------------------------------------------------

  def _require_connected(self) -> V11DeviceComm:
    if self._comm is None or not self._comm.is_connected:
      raise BravoError(ErrorType.COULD_NOT_CONNECT)
    return self._comm

  def _set_error(self, error: BravoError) -> None:
    self._last_error = error
    logger.error("Bravo error: %s", error)

  def _send_agile(
    self,
    packet: bytes,
    axis: Axis | None = None,
    timeout_ms: int = 2000,
  ) -> bytes:
    """Send a 10-byte Agile packet via ``CMD_DIRECT_AGILE_COMMAND``.

    For firmware >= 2.0.0 an axis-index byte is appended so the Rabbit
    can route the packet to the correct controller.

    Ref: ``CBravoAgileController::SendAgileCommand()``
    """
    comm = self._require_connected()
    payload = packet
    if self._fw_at_least_2 and axis is not None:
      payload = packet + struct.pack("<B", axis.value)
    return comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload, timeout_ms)

  def _send_agile_parsed(
    self,
    packet: bytes,
    axis: Axis | None = None,
    timeout_ms: int = 2000,
  ) -> AgileReply:
    """Send an Agile packet and return a validated ``AgileReply``."""
    response = self._send_agile(packet, axis, timeout_ms)
    logger.debug("Agile response: %d bytes", len(response))
    if len(response) < AGILE_PACKET_SIZE:
      raise BravoError(ErrorType.INVALID_AGILE_RESPONSE)
    reply = self._agile_pkt.AgileReply.from_packet(response[:AGILE_PACKET_SIZE])
    if not reply.crc_valid:
      raise BravoError(ErrorType.AGILE_RABBIT_CRC)
    return reply

  # -----------------------------------------------------------------
  # Internal: unit conversion
  # -----------------------------------------------------------------

  def _to_ticks(self, axis: Axis, value: float) -> float:
    """Engineering units (mm or uL) -> encoder ticks."""
    return value * self._ticks_per_unit[axis]

  def _from_ticks(self, axis: Axis, ticks: float) -> float:
    """Encoder ticks -> engineering units (mm or uL)."""
    return ticks / self._ticks_per_unit[axis]

  def _vel_to_ticks_per_ms(self, axis: Axis, mm_per_s: float) -> float:
    """mm/s (or uL/s) -> ticks/ms."""
    return (mm_per_s * self._ticks_per_unit[axis]) / 1000.0

  def _accel_to_ticks_per_ms2(self, axis: Axis, mm_per_s2: float) -> float:
    """mm/s^2 (or uL/s^2) -> ticks/ms^2."""
    return (mm_per_s2 * self._ticks_per_unit[axis]) / 1_000_000.0

  # -----------------------------------------------------------------
  # Internal: controller verification
  # -----------------------------------------------------------------

  def _verify_controller(self, controller_id: int) -> bool:
    """Read the unique-value register to confirm the controller is alive.

    Ref: ``CBravoAgileController::VerifyController()``
    """
    pkt = self._agile_pkt.register_get(controller_id, AgileRegister.UNIQUE_VALUE)
    try:
      reply = self._send_agile_parsed(pkt)
      value = reply.get_register_value()
      if value != UNIQUE_VALUE_EXPECTED:
        logger.error(
          "Controller %d unique-value mismatch: 0x%04X (expected 0x%04X)",
          controller_id,
          value,
          UNIQUE_VALUE_EXPECTED,
        )
        return False
      logger.debug("Controller %d verified", controller_id)
      return True
    except BravoError as exc:
      logger.error("Controller %d verification failed: %s", controller_id, exc)
      return False

  # -----------------------------------------------------------------
  # Internal: motion polling
  # -----------------------------------------------------------------

  def _wait_for_in_position(
    self,
    axes: list[Axis],
    timeout_ms: int = _DEFAULT_MOVE_TIMEOUT_MS,
  ) -> None:
    """Poll ``GetGroupAStatus`` until every target axis is settled.

    The status word has one trajectory-active bit per local axis.  When
    all bits for our target axes read zero the move is complete.

    Ref: ``CBravoAgileController`` in-position polling loop.
    """
    c1_mask = 0
    c2_mask = 0
    for axis in axes:
      if axis in _CONTROLLER_1_AXES:
        c1_mask |= _axis_bit(axis)
      else:
        c2_mask |= _axis_bit(axis)

    deadline = time.monotonic() + timeout_ms / 1000.0

    while time.monotonic() < deadline:
      all_settled = True

      if c1_mask:
        pkt = self._agile_pkt.get_group_a_status(_CONTROLLER_1_ID)
        reply = self._send_agile_parsed(pkt)
        if reply.get_register_value() & c1_mask:
          all_settled = False

      if c2_mask:
        pkt = self._agile_pkt.get_group_a_status(_CONTROLLER_2_ID)
        reply = self._send_agile_parsed(pkt)
        if reply.get_register_value() & c2_mask:
          all_settled = False

      if all_settled:
        logger.debug(
          "All axes in position: %s",
          [a.label for a in axes],
        )
        return

      time.sleep(_MOVE_POLL_INTERVAL_S)

    raise BravoError(
      ErrorType.MOVE_TIMEOUT,
      custom_text=(f"Timed out waiting for axes {[a.label for a in axes]} ({timeout_ms} ms)"),
    )

  # -----------------------------------------------------------------
  # Internal: post-connect handshake
  # -----------------------------------------------------------------

  def _post_connect(self) -> None:
    """Query firmware version and verify controller communication.

    Ref: ``CBravoAgileController::PostConnect()``
    """
    try:
      self._firmware_version = self.get_firmware_version()
      self._firmware_tuple = _parse_version_string(
        self._firmware_version.master,
      )
      logger.info(
        "Connected — firmware master=%s sub1=%s sub2=%s",
        self._firmware_version.master,
        self._firmware_version.sub1,
        self._firmware_version.sub2,
      )
    except BravoError as exc:
      logger.warning("Could not query firmware version: %s", exc)

    if not self._verify_controller(_CONTROLLER_1_ID):
      raise BravoError(
        ErrorType.CONTROLLER_UNIDENTIFIED,
        custom_text="Controller 1 verification failed",
      )
    logger.debug("Post-connect handshake complete")

  # =================================================================
  # BravoController interface — Connection
  # =================================================================

  def open_serial(self, port: str) -> None:
    """Open an RS-232 connection to the Bravo.

    Ref: ``CBravoAgileController::OpenSerialConnection()``
    """
    logger.info("Opening serial connection on %s", port)
    transport = SerialTransport(port, 115200, hardware_flow_control=True)
    self._comm = V11DeviceComm(transport)
    try:
      self._comm.connect()
      self._post_connect()
    except Exception:
      self._comm = None
      raise

  def open_tcp(self, address: str) -> None:
    """Open a TCP connection to the Bravo.

    Ref: ``CBravoAgileController::OpenTCPConnection()``
    """
    logger.info("Opening TCP connection to %s", address)
    transport = TCPTransport(address)
    self._comm = V11DeviceComm(transport)
    try:
      self._comm.connect()
      self._post_connect()
    except Exception:
      self._comm = None
      raise

  def close(self) -> None:
    if self._comm is not None:
      logger.info("Closing connection")
      self._comm.disconnect()
      self._comm = None
    self._homed = [False] * NUM_AXES_WITH_GRIPPER

  def ping(self) -> bool:
    """Ping the Rabbit microcontroller.

    Ref: ``CBravoAgileController::PingDevice()``
    """
    try:
      comm = self._require_connected()
      comm.send_command(CommandID.PING_DEVICE, timeout_ms=1000)
      return True
    except (BravoError, ConnectionError, TimeoutError):
      return False

  @property
  def is_connected(self) -> bool:
    return self._comm is not None and self._comm.is_connected

  # =================================================================
  # BravoController interface — Firmware
  # =================================================================

  def get_firmware_version(self) -> FirmwareVersion:
    """Query firmware version strings from the Rabbit.

    The response contains up to three null-terminated ASCII strings
    (master, sub-controller 1, sub-controller 2).

    Ref: ``CBravoAgileController::GetFirmwareVersion()``
    """
    comm = self._require_connected()
    try:
      data = comm.send_command(CommandID.QUERY_VERSION)
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_QUERY_FIRMWARE) from exc

    parts = data.split(b"\x00")
    strings = [p.decode("ascii", errors="replace") for p in parts if p]

    version = FirmwareVersion(
      master=strings[0] if len(strings) > 0 else "",
      sub1=strings[1] if len(strings) > 1 else "",
      sub2=strings[2] if len(strings) > 2 else "",
    )
    self._firmware_version = version
    self._firmware_tuple = _parse_version_string(version.master)
    return version

  # =================================================================
  # BravoController interface — Motion
  # =================================================================

  def move(
    self,
    moves: list[AxisMoveInfo],
    wait: bool = True,
    timeout_ms: int = _DEFAULT_MOVE_TIMEOUT_MS,
  ) -> None:
    """Execute a coordinated multi-axis move.

    Protocol:
      1. Convert each ``AxisMoveInfo`` to ticks and send
         ``CMD_PREPARE_MOVE`` (0xA2).
      2. Group axes by controller; send ``MoveGo`` Agile packets via
         ``CMD_DIRECT_AGILE_COMMAND`` (0xA1).
      3. If *wait*, poll ``GetGroupAStatus`` until all axes are settled.

    Ref: ``CBravoAgileController::MoveToPosition()``
    """
    comm = self._require_connected()

    # Phase 1 — prepare each axis
    for m in moves:
      info = self._move_info_cls(
        axis=m.axis,
        position=self._to_ticks(m.axis, m.position),
        velocity=self._vel_to_ticks_per_ms(m.axis, m.velocity),
        acceleration=self._accel_to_ticks_per_ms2(m.axis, m.acceleration),
        absolute_move=m.absolute,
      )
      logger.debug(
        "Prepare move: %s pos=%.1f ticks vel=%.4f ticks/ms accel=%.6f ticks/ms² abs=%s",
        m.axis.label,
        info.position,
        info.velocity,
        info.acceleration,
        info.absolute_move,
      )
      try:
        comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      except BravoError as exc:
        self._set_error(exc)
        raise

    # Phase 2 — MoveGo per controller
    c1_mask = 0
    c2_mask = 0
    for m in moves:
      if m.axis in _CONTROLLER_1_AXES:
        c1_mask |= _axis_bit(m.axis)
      else:
        c2_mask |= _axis_bit(m.axis)

    try:
      if c1_mask:
        pkt = self._agile_pkt.move_go(_CONTROLLER_1_ID, c1_mask)
        self._send_agile(pkt)
        logger.debug("MoveGo controller 1 mask=0x%02X", c1_mask)
      if c2_mask:
        pkt = self._agile_pkt.move_go(_CONTROLLER_2_ID, c2_mask)
        self._send_agile(pkt)
        logger.debug("MoveGo controller 2 mask=0x%02X", c2_mask)
    except BravoError as exc:
      self._set_error(exc)
      raise

    # Phase 3 — wait
    if wait:
      self._wait_for_in_position([m.axis for m in moves], timeout_ms)

  def home_axes(self, axes: list[Axis]) -> None:
    """Home one or more axes.

    Protocol:
      1. Enable servo for each axis.
      2. Clear home-flag registers so the controllers know to re-home.
      3. Send ``MoveGo`` to kick off the homing sequence.
      4. Poll the home-flag register until it goes non-zero (homed).

    Ref: ``CBravoAgileController::HomeAxes()``
    """
    self._require_connected()

    # Phase 1 — servo enable + clear home flag
    for axis in axes:
      cid = _controller_for_axis(axis)
      local = _local_axis_index(axis)

      pkt = self._agile_pkt.servo_enable(cid, local)
      self._send_agile(pkt, axis)
      logger.debug("Servo enabled: %s (cid=%d local=%d)", axis.label, cid, local)

      pkt = self._agile_pkt.register_set_value(cid, AgileRegister.HOME_FLAG, 0)
      self._send_agile(pkt, axis)

    # Phase 2 — MoveGo per controller
    c1_mask = 0
    c2_mask = 0
    for axis in axes:
      if axis in _CONTROLLER_1_AXES:
        c1_mask |= _axis_bit(axis)
      else:
        c2_mask |= _axis_bit(axis)

    if c1_mask:
      pkt = self._agile_pkt.move_go(_CONTROLLER_1_ID, c1_mask)
      self._send_agile(pkt)
    if c2_mask:
      pkt = self._agile_pkt.move_go(_CONTROLLER_2_ID, c2_mask)
      self._send_agile(pkt)

    # Phase 3 — poll home-flag registers
    deadline = time.monotonic() + _DEFAULT_HOME_TIMEOUT_MS / 1000.0
    pending = set(axes)

    while pending and time.monotonic() < deadline:
      for axis in list(pending):
        cid = _controller_for_axis(axis)
        pkt = self._agile_pkt.register_get(cid, AgileRegister.HOME_FLAG)
        try:
          reply = self._send_agile_parsed(pkt, axis)
          if reply.get_register_value() != 0:
            self._homed[axis.value] = True
            pending.discard(axis)
            logger.info("Axis %s homed", axis.label)
        except BravoError:
          pass

      if pending:
        time.sleep(_HOME_POLL_INTERVAL_S)

    if pending:
      error = BravoError(
        ErrorType.COULD_NOT_HOME,
        custom_text=f"Homing timed out for: {[a.label for a in pending]}",
      )
      self._set_error(error)
      raise error

  def jog(self, params: JogParams) -> float:
    """Execute a force-controlled jog move and return the final position.

    Ref: ``CBravoAgileController::MoveJog()``
    """
    comm = self._require_connected()

    info = AgileJogInfo(
      axis=params.axis,
      velocity=self._vel_to_ticks_per_ms(params.axis, params.velocity),
      acceleration=self._accel_to_ticks_per_ms2(params.axis, params.acceleration),
      max_position=self._to_ticks(params.axis, params.max_position),
      tolerance=self._to_ticks(params.axis, params.tolerance),
      peak_current=params.peak_current,
    )

    logger.debug("Preparing jog: %s", params.axis.label)
    try:
      comm.send_command(CommandID.PREPARE_JOG, info.pack())
    except BravoError as exc:
      self._set_error(exc)
      raise

    cid = _controller_for_axis(params.axis)
    pkt = self._agile_pkt.move_go(cid, _axis_bit(params.axis))
    self._send_agile(pkt, params.axis)

    self._wait_for_in_position([params.axis])
    return self.get_position(params.axis)

  def get_position(self, axis: Axis) -> float:
    """Read current position of *axis* in engineering units (mm or uL).

    Ref: ``CBravoAgileController::GetPosition()``
    """
    comm = self._require_connected()
    try:
      data = comm.send_command(
        CommandID.GET_POSITION,
        struct.pack("<B", axis.value),
      )
      if len(data) < 4:
        raise BravoError(ErrorType.COULD_NOT_READ_POSITION, axis=axis)
      ticks = struct.unpack_from("<f", data, 0)[0]
      position = self._from_ticks(axis, ticks)
      logger.debug(
        "Position %s: %.3f eng  (%.1f ticks)",
        axis.label,
        position,
        ticks,
      )
      return position
    except BravoError as exc:
      self._set_error(exc)
      raise

  def is_axis_homed(self, axis: Axis) -> bool:
    return self._homed[axis.value]

  def get_park_position(self, axis: Axis) -> float:
    # Agile does not currently expose per-axis park positions in this port.
    # Keep the historical parked-at-zero behavior until controller-specific
    # park metadata is available.
    return 0.0

  # =================================================================
  # BravoController interface — Motor control
  # =================================================================

  def enable_motor(self, axis: Axis) -> None:
    """Enable the servo drive for *axis*.

    Ref: ``CBravoAgileController::EnableMotor()``
    """
    cid = _controller_for_axis(axis)
    pkt = self._agile_pkt.servo_enable(cid, _local_axis_index(axis))
    try:
      self._send_agile(pkt, axis)
      logger.debug("Motor enabled: %s", axis.label)
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_ENABLE_MOTOR, axis=axis) from exc

  def disable_motor(self, axis: Axis) -> None:
    """Disable the servo drive for *axis*.

    Ref: ``CBravoAgileController::DisableMotor()``
    """
    cid = _controller_for_axis(axis)
    pkt = self._agile_pkt.servo_disable(cid, _local_axis_index(axis))
    try:
      self._send_agile(pkt, axis)
      logger.debug("Motor disabled: %s", axis.label)
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_DISABLE_MOTOR, axis=axis) from exc

  def reset_faults(self, axes: list[Axis]) -> None:
    """Clear fault flags on the specified axes.

    Ref: ``CBravoAgileController::ResetFaults()``
    """
    c1_mask = 0
    c2_mask = 0
    for axis in axes:
      if axis in _CONTROLLER_1_AXES:
        c1_mask |= _axis_bit(axis)
      else:
        c2_mask |= _axis_bit(axis)

    if c1_mask:
      pkt = self._agile_pkt.reset_faults(_CONTROLLER_1_ID, c1_mask)
      self._send_agile(pkt)
    if c2_mask:
      pkt = self._agile_pkt.reset_faults(_CONTROLLER_2_ID, c2_mask)
      self._send_agile(pkt)

    logger.debug("Faults reset: %s", [a.label for a in axes])

  # =================================================================
  # BravoController interface — Device state
  # =================================================================

  def query_state(self) -> DeviceStateFlag:
    """Query device-state flags from the Rabbit.

    Returns a bitmask of ``DeviceStateFlag`` (robot-disable, motor-power,
    go-button, robot-disable-button).

    Ref: ``CBravoAgileController::QueryState()``
    """
    comm = self._require_connected()
    try:
      data = comm.send_command(CommandID.QUERY_STATE)
      if len(data) < 1:
        raise BravoError(ErrorType.COULD_NOT_QUERY_STATE)
      return DeviceStateFlag(data[0])
    except BravoError as exc:
      self._set_error(exc)
      raise

  def is_go_button_pressed(self) -> bool:
    """Check whether the front-panel Go button is pressed.

    Ref: ``CBravoAgileController::IsGoButtonPressed()``
    """
    comm = self._require_connected()
    try:
      data = comm.send_command(CommandID.GO_BUTTON_PRESSED)
      return len(data) >= 1 and data[0] != 0
    except BravoError as exc:
      self._set_error(exc)
      raise

  def clear_go_button(self) -> None:
    """Clear the Go-button latch.

    Ref: ``CBravoAgileController::ClearGoButton()``
    """
    comm = self._require_connected()
    comm.send_command(CommandID.CLEAR_GO_BUTTON)

  # =================================================================
  # BravoController interface — Lights
  # =================================================================

  def set_light(self, command: LightCommandData) -> None:
    """Set an indicator light on the Bravo chassis.

    Ref: ``CBravoAgileController::SetLight()``
    """
    comm = self._require_connected()
    try:
      comm.send_command(CommandID.SET_LIGHT, command.pack())
      logger.debug("Light set: %s", command)
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_SET_LIGHT) from exc

  def clear_lights(self) -> None:
    """Turn off all indicator lights.

    Ref: ``CBravoAgileController::ClearLights()``
    """
    comm = self._require_connected()
    comm.send_command(CommandID.CLEAR_LIGHTS)
    logger.debug("Lights cleared")

  # =================================================================
  # BravoController interface — Head detection
  # =================================================================

  def read_head_adc(self) -> int:
    """Read the ADC value from the weigh-pad / head-detection resistor.

    Ref: ``CBravoAgileController::ReadADWeighPad()``
    """
    comm = self._require_connected()
    try:
      data = comm.send_command(CommandID.READ_AD_WEIGH_PAD)
      if len(data) < 2:
        raise BravoError(ErrorType.COULD_NOT_DETECT_HEAD)
      adc = struct.unpack_from("<H", data, 0)[0]
      logger.debug("Head ADC value: %d", adc)
      return adc
    except BravoError as exc:
      self._set_error(exc)
      raise

  def detect_smart_head(self) -> bool:
    """Detect whether a smart head (PIC / EEPROM) is present.

    Returns ``True`` when the Rabbit receives an ACK from the PIC on the
    head's I2C bus.

    Ref: ``CBravoAgileController::DetectSmartHead()``
    """
    comm = self._require_connected()
    try:
      data = comm.send_command(CommandID.DETECT_SMART_HEAD)
      present = len(data) >= 1 and data[0] == 0x01
      logger.debug("Smart head detected: %s", present)
      return present
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_DETECT_SMART_HEAD) from exc

  def read_smart_head_type(self) -> int:
    """Read the head-type code from the smart-head EEPROM at address 0x01.

    Ref: ``CBravoAgileController::GetSmartHeadType()``
    """
    comm = self._require_connected()
    request = SmartHeadEEPROMData(
      address=EEPROMAddress.HEAD_TYPE,
      length=1,
    )
    try:
      data = comm.send_command(CommandID.GET_EEPROM_DATA, request.pack())
      result = SmartHeadEEPROMData.unpack(data)
      head_code = result.data[0] if result.data else 0
      logger.debug("Smart head type code: %d", head_code)
      return head_code
    except BravoError as exc:
      self._set_error(exc)
      raise

  # =================================================================
  # BravoController interface — Gripper
  # =================================================================

  def detect_gripper(self) -> GripperDetectionState:
    """Detect whether a gripper module is attached.

    Ref: ``CBravoAgileController::DetectGripper()``
    """
    comm = self._require_connected()
    try:
      data = comm.send_command(CommandID.DETECT_GRIPPER)
      if len(data) < 1:
        return GripperDetectionState.NOT_YET_DETECTED
      state = GripperDetectionState(data[0])
      logger.debug("Gripper detection: %s", state.name)
      return state
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_DETECT_GRIPPER) from exc

  def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
    """Execute a grip operation at *position* mm.

    Ref: ``CBravoAgileController::Grip()``
    """
    comm = self._require_connected()

    tpu = self._ticks_per_unit.get(Axis.G, TICKS_PER_MM[Axis.G])
    current = 0.5 if speed == SpeedLevel.FAST else 0.3
    params = GripperParams(
      grip_current=current,
      grip_velocity=self._vel_to_ticks_per_ms(Axis.G, 10.0),
      grip_acceleration=self._accel_to_ticks_per_ms2(Axis.G, 100.0),
      target_position=self._to_ticks(Axis.G, position),
      position_tolerance=float(GRIP_POSITION_TOLERANCE),
      max_gripper_current=0.5,
      original_max_pos_error=1000.0,
      original_velocity=self._vel_to_ticks_per_ms(Axis.G, 20.0),
      original_acceleration=self._accel_to_ticks_per_ms2(Axis.G, 200.0),
      ticks_per_eng_unit=tpu,
    )

    try:
      comm.send_command(CommandID.GRIP, params.pack())
      logger.debug("Grip executed: position=%.3f mm speed=%s", position, speed.name)
    except BravoError as exc:
      self._set_error(exc)
      raise

  def open_gripper(self, position: float | None = None) -> None:
    """Open the gripper to its fully-open position.

    Ref: ``CBravoAgileController::OpenGripper()``
    """
    self.move(
      [
        AxisMoveInfo(
          axis=Axis.G,
          position=OPEN_GRIPPER_POSITION if position is None else float(position),
          velocity=20.0,
          acceleration=200.0,
          absolute=True,
        ),
      ]
    )

  def is_plate_in_gripper(self) -> bool:
    """Return ``True`` if the gripper position indicates a plate is held.

    Ref: ``CBravoAgileController::IsPlateInGripper()``
    """
    try:
      pos_ticks = self._to_ticks(Axis.G, self.get_position(Axis.G))
      open_ticks = self._to_ticks(Axis.G, OPEN_GRIPPER_POSITION)
      return abs(pos_ticks - open_ticks) > GRIP_POSITION_TOLERANCE
    except BravoError:
      return False

  # =================================================================
  # BravoController interface — Generic command dispatch
  # =================================================================

  def send_command(
    self,
    command_id: int,
    data: bytes = b"",
    timeout_ms: int = 2000,
  ) -> bytes:
    """Send a raw V11 command for extensibility and diagnostics.

    Ref: ``CBravoAgileController::SendCommand()``
    """
    comm = self._require_connected()
    return comm.send_command(CommandID(command_id), data, timeout_ms)

  # =================================================================
  # BravoController interface — Last error
  # =================================================================

  @property
  def last_error(self) -> BravoError | None:
    return self._last_error

  # =================================================================
  # Configuration
  # =================================================================

  def set_w_axis_scale(self, ticks_per_ul: float) -> None:
    """Set the W (plunger) axis encoder scale for the installed head.

    Different head types use different syringe volumes and thus different
    ticks-per-uL ratios.  Call this after head detection.
    """
    self._ticks_per_unit[Axis.W] = ticks_per_ul
    logger.info("W-axis scale set to %.2f ticks/uL", ticks_per_ul)

  @property
  def firmware_version(self) -> FirmwareVersion:
    """Most recently queried firmware version (cached)."""
    return self._firmware_version
