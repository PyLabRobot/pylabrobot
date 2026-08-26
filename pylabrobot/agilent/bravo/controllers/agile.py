"""Agile controller for legacy (non-7612) Bravo hardware.

The Rabbit microcontroller sits between the host and the Agile motor
controllers. It accepts V11-framed commands over the transport and relays
10-byte Agile packets to one or two Agile motor controllers over an internal
bus:

- Controller 1 (Agile bus ID 0): X, Y, Z, W axes.
- Controller 2 (Agile bus ID 1): G, Zg axes (gripper module).

Homing, jogging, and coordinated moves are built from the same primitive:
``CMD_PREPARE_MOVE`` (or ``CMD_PREPARE_JOG``) loads a target into the
Rabbit, and an Agile ``MoveGo`` packet (sent via
``CMD_DIRECT_AGILE_COMMAND``) triggers it. A move is not observed complete
until ``GetGroupAStatus`` reports the target axis's trajectory-active bit
clear.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Any, Optional, Protocol, Type

from ..errors import BravoError, ErrorType
from ..protocol import agile_packet
from ..protocol.agile_packet import (
  AGILE_PACKET_SIZE,
  UNIQUE_VALUE_EXPECTED,
  AgileRegister,
)
from ..protocol.commands import (
  AgileJogInfo,
  AgileMoveInfo,
  CommandID,
  EEPROMAddress,
  GripperParams,
  LightCommandData,
  SmartHeadEEPROMData,
)
from ..protocol.v11_comm import V11DeviceComm
from ..transport import Transport
from ..types import (
  ALL_AXES,
  DEFAULT_W_TICKS_PER_UL,
  GRIP_POSITION_TOLERANCE,
  OPEN_GRIPPER_POSITION,
  TICKS_PER_MM,
  Axis,
  DeviceStateFlag,
  GripperDetectionState,
  SpeedLevel,
  axis_code,
  axis_label,
)
from .base import AxisMoveInfo, BravoController, FirmwareVersion, JogParams

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structural types for the swappable packet-codec module and move-info class
# ---------------------------------------------------------------------------
#
# self._agile_pkt is agile_packet (legacy Agile) on this class and
# agile_7612_packet on the Agile 7612 subclass; self._move_info_cls is
# AgileMoveInfo here and Agile7612MoveInfo there. Each pair exports the same
# call surface with a different wire encoding underneath, but the two
# modules and the two dataclasses are not related by inheritance, so a
# concrete type annotation naming one of them would be wrong for the other.
# These Protocols describe the shared shape structurally instead.


class AgileReplyLike(Protocol):
  """The shape of a parsed Agile reply, common to both AgileReply classes."""

  crc_valid: bool

  def get_register_value(self) -> int: ...


class AgilePacketModule(Protocol):
  """The shared call surface of the agile_packet / agile_7612_packet modules."""

  # AgileReply is a class, accessed as self._agile_pkt.AgileReply.from_packet(...):
  # a classmethod reached through a class attribute reached through a module
  # attribute. mypy's structural Protocol matching does not verify a chain
  # this shape, so it is left untyped rather than given a check that cannot
  # actually catch a mismatch; every other attribute below is checked.
  AgileReply: Any

  def register_get(self, controller_id: int, register: int) -> bytes: ...

  def register_set_value(self, controller_id: int, register: int, value: int) -> bytes: ...

  def move_go(self, controller_id: int, axis_mask: int) -> bytes: ...

  def servo_enable(self, controller_id: int, axis: int) -> bytes: ...

  def servo_disable(self, controller_id: int, axis: int) -> bytes: ...

  def reset_faults(self, controller_id: int, axis_mask: int) -> bytes: ...

  def get_group_a_status(self, controller_id: int) -> bytes: ...


class MoveInfoLike(Protocol):
  """The shape of a packed move-command payload, common to both move-info classes."""

  position: float
  velocity: float
  acceleration: float
  absolute_move: bool

  def pack(self) -> bytes: ...


class MoveInfoFactory(Protocol):
  """The constructor shape shared by AgileMoveInfo and Agile7612MoveInfo."""

  def __call__(
    self,
    *,
    axis: Axis,
    position: float,
    velocity: float,
    acceleration: float,
    absolute_move: bool = ...,
    check_for_homed: bool = ...,
    home_complete_register: int = ...,
  ) -> MoveInfoLike: ...


# ---------------------------------------------------------------------------
# Controller mapping
# ---------------------------------------------------------------------------

_CONTROLLER_1_ID = 0
_CONTROLLER_2_ID = 1

_CONTROLLER_1_AXES: frozenset[Axis] = frozenset({"x", "y", "z", "w"})
_CONTROLLER_2_AXES: frozenset[Axis] = frozenset({"g", "zg"})

# ---------------------------------------------------------------------------
# Timing (seconds)
# ---------------------------------------------------------------------------

_MOVE_POLL_INTERVAL = 0.010
_HOME_POLL_INTERVAL = 0.050
_DEFAULT_MOVE_TIMEOUT = 30.0
_DEFAULT_HOME_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _controller_for_axis(axis: Axis) -> int:
  """Return the Agile bus controller ID that owns an axis.

  Args:
    axis: The axis to look up.

  Returns:
    ``1`` for the gripper axes (G, Zg), ``0`` for everything else.
  """
  return _CONTROLLER_2_ID if axis in _CONTROLLER_2_AXES else _CONTROLLER_1_ID


def _local_axis_index(axis: Axis) -> int:
  """Return an axis's 0-based index within its own Agile bus controller.

  Args:
    axis: The axis to look up.

  Returns:
    The axis's local index: 0-3 for X/Y/Z/W on controller 1, 0-1 for G/Zg
    on controller 2.
  """
  code = axis_code(axis)
  return code - 4 if axis in _CONTROLLER_2_AXES else code


def _axis_bit(axis: Axis) -> int:
  """Return a single-bit mask selecting an axis within its controller.

  Args:
    axis: The axis to look up.

  Returns:
    A bitmask with exactly one bit set, at the axis's local index.
  """
  return 1 << _local_axis_index(axis)


def _parse_version_string(version_str: str) -> tuple[int, ...]:
  """Parse a dotted firmware version string into a comparable tuple.

  Args:
    version_str: A version string such as ``"2.0.0"``.

  Returns:
    The parsed ``(major, minor, patch, ...)`` tuple, or ``(0, 0, 0)`` if
    ``version_str`` cannot be parsed.
  """
  try:
    return tuple(int(x) for x in version_str.strip().split("."))
  except (ValueError, AttributeError):
    return (0, 0, 0)


# ---------------------------------------------------------------------------
# AgileController
# ---------------------------------------------------------------------------


class AgileController(BravoController):
  """Hardware controller for legacy Agile-generation Bravo liquid handlers.

  Speaks the V11 command protocol to a Rabbit microcontroller, which relays
  10-byte Agile packets to the motor controllers driving the gantry, head,
  and gripper.

  Attributes:
    has_gripper: Whether this model has a gripper accessory.
    model_name: The human-readable model name, used in diagnostic messages.
  """

  _comm_cls: Type[V11DeviceComm] = V11DeviceComm

  has_gripper = True
  model_name = "Bravo"

  def __init__(self, transport: Transport) -> None:
    """Bind this controller to an already-connected transport.

    Args:
      transport: The transport to communicate over. The caller owns its
        connection lifecycle.
    """
    super().__init__(transport)
    self._comm: V11DeviceComm = self._comm_cls(transport)
    self._last_error: Optional[BravoError] = None
    self._firmware_version = FirmwareVersion()
    self._firmware_tuple: tuple[int, ...] = (0, 0, 0)
    self._homed: dict[Axis, bool] = {axis: False for axis in ALL_AXES}

    self._ticks_per_unit: dict[Axis, float] = {
      **TICKS_PER_MM,
      "w": DEFAULT_W_TICKS_PER_UL,
    }

    # Both are swapped for Agile-7612-generation equivalents by that
    # subclass -- see AgilePacketModule and MoveInfoFactory above.
    self._agile_pkt: AgilePacketModule = agile_packet
    self._move_info_cls: MoveInfoFactory = AgileMoveInfo

  # -----------------------------------------------------------------
  # Internal: firmware gate
  # -----------------------------------------------------------------

  @property
  def _fw_at_least_2(self) -> bool:
    """Whether the connected firmware is 2.0.0 or newer.

    Firmware 2.0.0 and later require an axis-index byte appended to every
    ``CMD_DIRECT_AGILE_COMMAND`` payload so the Rabbit can route it to the
    correct Agile controller.
    """
    return self._firmware_tuple >= (2, 0, 0)

  # -----------------------------------------------------------------
  # Internal: communication primitives
  # -----------------------------------------------------------------

  def _require_connected(self) -> V11DeviceComm:
    """Return the comm layer, raising if the transport is not connected.

    Returns:
      The bound comm layer.

    Raises:
      BravoError: If the transport is not currently connected.
    """
    if not self._comm.is_connected:
      raise BravoError(ErrorType.COULD_NOT_CONNECT)
    return self._comm

  def _set_error(self, error: BravoError) -> None:
    """Record the most recent error and log it.

    Args:
      error: The error to record.
    """
    self._last_error = error
    logger.error("Bravo error: %s", error)

  def _send_agile(
    self,
    packet: bytes,
    axis: Optional[Axis] = None,
    timeout: float = 2.0,
  ) -> bytes:
    """Send a 10-byte Agile packet via ``CMD_DIRECT_AGILE_COMMAND``.

    Args:
      packet: The 10-byte Agile packet to send.
      axis: The axis this packet targets, if any. On firmware 2.0.0 and
        later, its wire code is appended so the Rabbit can route the
        packet to the correct controller.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The raw response payload.
    """
    comm = self._require_connected()
    payload = packet
    if self._fw_at_least_2 and axis is not None:
      payload = packet + struct.pack("<B", axis_code(axis))
    return comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload, timeout)

  def _send_agile_parsed(
    self,
    packet: bytes,
    axis: Optional[Axis] = None,
    timeout: float = 2.0,
  ) -> AgileReplyLike:
    """Send an Agile packet and return a validated, parsed reply.

    Args:
      packet: The 10-byte Agile packet to send.
      axis: The axis this packet targets, if any.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The parsed Agile reply.

    Raises:
      BravoError: If the response is too short, or its checksum is invalid.
    """
    response = self._send_agile(packet, axis, timeout)
    logger.debug("Agile response: %d bytes", len(response))
    if len(response) < AGILE_PACKET_SIZE:
      raise BravoError(ErrorType.INVALID_AGILE_RESPONSE)
    reply: AgileReplyLike = self._agile_pkt.AgileReply.from_packet(response[:AGILE_PACKET_SIZE])
    if not reply.crc_valid:
      raise BravoError(ErrorType.AGILE_RABBIT_CRC)
    return reply

  # -----------------------------------------------------------------
  # Internal: unit conversion
  # -----------------------------------------------------------------

  def _to_ticks(self, axis: Axis, value: float) -> float:
    """Convert an engineering-unit value to encoder ticks.

    Args:
      axis: The axis the value belongs to.
      value: The value, in mm (or uL for the W axis).

    Returns:
      The equivalent value in encoder ticks.
    """
    return value * self._ticks_per_unit[axis]

  def _from_ticks(self, axis: Axis, ticks: float) -> float:
    """Convert an encoder-tick value to engineering units.

    Args:
      axis: The axis the value belongs to.
      ticks: The value, in encoder ticks.

    Returns:
      The equivalent value in mm (or uL for the W axis).
    """
    return ticks / self._ticks_per_unit[axis]

  def _vel_to_ticks_per_ms(self, axis: Axis, mm_per_s: float) -> float:
    """Convert a velocity from mm/s (or uL/s) to ticks/ms.

    Args:
      axis: The axis the velocity belongs to.
      mm_per_s: The velocity, in mm/s (or uL/s for the W axis).

    Returns:
      The equivalent velocity in ticks/ms.
    """
    return (mm_per_s * self._ticks_per_unit[axis]) / 1000.0

  def _accel_to_ticks_per_ms2(self, axis: Axis, mm_per_s2: float) -> float:
    """Convert an acceleration from mm/s^2 (or uL/s^2) to ticks/ms^2.

    Args:
      axis: The axis the acceleration belongs to.
      mm_per_s2: The acceleration, in mm/s^2 (or uL/s^2 for the W axis).

    Returns:
      The equivalent acceleration in ticks/ms^2.
    """
    return (mm_per_s2 * self._ticks_per_unit[axis]) / 1_000_000.0

  # -----------------------------------------------------------------
  # Internal: controller verification
  # -----------------------------------------------------------------

  def _verify_controller(self, controller_id: int) -> bool:
    """Confirm an Agile controller is alive by reading its unique-value register.

    Args:
      controller_id: The Agile bus controller ID to verify.

    Returns:
      True if the controller responds with the expected unique value.
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
    timeout: float = _DEFAULT_MOVE_TIMEOUT,
  ) -> None:
    """Poll ``GetGroupAStatus`` until every target axis has settled.

    Args:
      axes: The axes to wait for.
      timeout: Maximum time to wait, in seconds.

    Raises:
      BravoError: If any axis is still moving when ``timeout`` elapses.
    """
    c1_mask = 0
    c2_mask = 0
    for axis in axes:
      if axis in _CONTROLLER_1_AXES:
        c1_mask |= _axis_bit(axis)
      else:
        c2_mask |= _axis_bit(axis)

    deadline = time.monotonic() + timeout

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
        logger.debug("All axes in position: %s", [axis_label(a) for a in axes])
        return

      time.sleep(_MOVE_POLL_INTERVAL)

    raise BravoError(
      ErrorType.MOVE_TIMEOUT,
      custom_text=(f"Timed out waiting for axes {[axis_label(a) for a in axes]} ({timeout}s)"),
    )

  # =================================================================
  # BravoController interface -- Lifecycle
  # =================================================================

  def initialize(self) -> None:
    """Reset homed state, query firmware version, and verify Agile controller 1 is alive.

    Every axis is marked unhomed first: nothing about a prior connection --
    including one this same controller instance held before a reconnect --
    can be trusted once initialize() runs again, since the physical axes
    may have moved (or been power-cycled) while nothing was connected to
    track them.

    Raises:
      BravoError: If controller 1 does not respond with its expected
        unique value.
    """
    self._homed = {axis: False for axis in ALL_AXES}
    try:
      self._firmware_version = self.get_firmware_version()
      self._firmware_tuple = _parse_version_string(self._firmware_version.master)
      logger.info(
        "Connected -- firmware master=%s sub1=%s sub2=%s",
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

  def ping(self) -> bool:
    """Ping the Rabbit microcontroller."""
    try:
      self._require_connected().send_command(CommandID.PING_DEVICE, timeout=1.0)
      return True
    except (BravoError, ConnectionError, TimeoutError):
      return False

  @property
  def is_connected(self) -> bool:
    return self._comm.is_connected

  # =================================================================
  # BravoController interface -- Firmware
  # =================================================================

  def get_firmware_version(self) -> FirmwareVersion:
    """Query firmware version strings from the Rabbit.

    The response contains up to three null-terminated ASCII strings
    (master, sub-controller 1, sub-controller 2).
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
  # BravoController interface -- Motion
  # =================================================================

  def move(
    self,
    moves: list[AxisMoveInfo],
    wait: bool = True,
    timeout: float = _DEFAULT_MOVE_TIMEOUT,
  ) -> None:
    """Execute a coordinated multi-axis move.

    Converts each move to ticks and sends ``CMD_PREPARE_MOVE``, then groups
    axes by controller and sends a ``MoveGo`` Agile packet via
    ``CMD_DIRECT_AGILE_COMMAND``. If ``wait``, polls ``GetGroupAStatus``
    until all axes have settled.

    Args:
      moves: The per-axis targets to move to together.
      wait: Whether to block until the move finishes.
      timeout: Maximum time to wait for the move to finish, in seconds.
    """
    comm = self._require_connected()

    for m in moves:
      info = self._move_info_cls(
        axis=m.axis,
        position=self._to_ticks(m.axis, m.position),
        velocity=self._vel_to_ticks_per_ms(m.axis, m.velocity),
        acceleration=self._accel_to_ticks_per_ms2(m.axis, m.acceleration),
        absolute_move=m.absolute,
      )
      logger.debug(
        "Prepare move: %s pos=%.1f ticks vel=%.4f ticks/ms accel=%.6f ticks/ms^2 abs=%s",
        axis_label(m.axis),
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

    if wait:
      self._wait_for_in_position([m.axis for m in moves], timeout)

  def home_axes(self, axes: list[Axis], *, force: bool = False) -> None:
    """Home one or more axes.

    Enables the servo for each axis and clears its home-flag register, then
    sends ``MoveGo`` to start the homing sequence, then polls the home-flag
    register until it reports non-zero (homed).

    Args:
      axes: The axes to home.
      force: Unused. Homing always runs unconditionally for the requested
        axes.

    Raises:
      BravoError: If any axis fails to report homed before the timeout.
    """
    self._require_connected()

    for axis in axes:
      cid = _controller_for_axis(axis)
      local = _local_axis_index(axis)

      pkt = self._agile_pkt.servo_enable(cid, local)
      self._send_agile(pkt, axis)
      logger.debug("Servo enabled: %s (cid=%d local=%d)", axis_label(axis), cid, local)

      pkt = self._agile_pkt.register_set_value(cid, AgileRegister.HOME_FLAG, 0)
      self._send_agile(pkt, axis)

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

    deadline = time.monotonic() + _DEFAULT_HOME_TIMEOUT
    pending = set(axes)

    while pending and time.monotonic() < deadline:
      for axis in list(pending):
        cid = _controller_for_axis(axis)
        pkt = self._agile_pkt.register_get(cid, AgileRegister.HOME_FLAG)
        try:
          reply = self._send_agile_parsed(pkt, axis)
          if reply.get_register_value() != 0:
            self._homed[axis] = True
            pending.discard(axis)
            logger.info("Axis %s homed", axis_label(axis))
        except BravoError:
          pass

      if pending:
        time.sleep(_HOME_POLL_INTERVAL)

    if pending:
      error = BravoError(
        ErrorType.COULD_NOT_HOME,
        custom_text=f"Homing timed out for: {[axis_label(a) for a in pending]}",
      )
      self._set_error(error)
      raise error

  def jog(self, params: JogParams) -> float:
    """Execute a force-controlled jog move and return the final position."""
    comm = self._require_connected()

    info = AgileJogInfo(
      axis=params.axis,
      velocity=self._vel_to_ticks_per_ms(params.axis, params.velocity),
      acceleration=self._accel_to_ticks_per_ms2(params.axis, params.acceleration),
      max_position=self._to_ticks(params.axis, params.max_position),
      tolerance=self._to_ticks(params.axis, params.tolerance),
      peak_current=params.peak_current,
    )

    logger.debug("Preparing jog: %s", axis_label(params.axis))
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
    """Read the current position of an axis, in engineering units (mm or uL)."""
    comm = self._require_connected()
    try:
      data = comm.send_command(
        CommandID.GET_POSITION,
        struct.pack("<B", axis_code(axis)),
      )
      if len(data) < 4:
        raise BravoError(ErrorType.COULD_NOT_READ_POSITION, axis=axis)
      ticks = struct.unpack_from("<f", data, 0)[0]
      position = self._from_ticks(axis, ticks)
      logger.debug("Position %s: %.3f eng (%.1f ticks)", axis_label(axis), position, ticks)
      return position
    except BravoError as exc:
      self._set_error(exc)
      raise

  def is_axis_homed(self, axis: Axis) -> bool:
    return self._homed[axis]

  def get_park_position(self, axis: Axis) -> float:
    """Return the axis's park position.

    Returns:
      ``0.0``. Legacy Agile hardware exposes no per-axis park metadata, so
      every axis parks at its firmware zero.
    """
    return 0.0

  # =================================================================
  # BravoController interface -- Motor control
  # =================================================================

  def enable_motor(self, axis: Axis) -> None:
    """Enable the servo drive for an axis."""
    cid = _controller_for_axis(axis)
    pkt = self._agile_pkt.servo_enable(cid, _local_axis_index(axis))
    try:
      self._send_agile(pkt, axis)
      logger.debug("Motor enabled: %s", axis_label(axis))
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_ENABLE_MOTOR, axis=axis) from exc

  def disable_motor(self, axis: Axis) -> None:
    """Disable the servo drive for an axis."""
    cid = _controller_for_axis(axis)
    pkt = self._agile_pkt.servo_disable(cid, _local_axis_index(axis))
    try:
      self._send_agile(pkt, axis)
      logger.debug("Motor disabled: %s", axis_label(axis))
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_DISABLE_MOTOR, axis=axis) from exc

  def reset_faults(self, axes: list[Axis]) -> None:
    """Clear fault flags on the given axes."""
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

    logger.debug("Faults reset: %s", [axis_label(a) for a in axes])

  # =================================================================
  # BravoController interface -- Device state
  # =================================================================

  def query_state(self) -> DeviceStateFlag:
    """Query device-state flags from the Rabbit."""
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
    """Check whether the front-panel Go button is pressed."""
    comm = self._require_connected()
    try:
      data = comm.send_command(CommandID.GO_BUTTON_PRESSED)
      return len(data) >= 1 and data[0] != 0
    except BravoError as exc:
      self._set_error(exc)
      raise

  def clear_go_button(self) -> None:
    """Clear the Go-button latch."""
    self._require_connected().send_command(CommandID.CLEAR_GO_BUTTON)

  # =================================================================
  # BravoController interface -- Lights
  # =================================================================

  def set_light(self, command: LightCommandData) -> None:
    """Set an indicator light on the Bravo chassis."""
    comm = self._require_connected()
    try:
      comm.send_command(CommandID.SET_LIGHT, command.pack())
      logger.debug("Light set: %s", command)
    except BravoError as exc:
      self._set_error(exc)
      raise BravoError(ErrorType.COULD_NOT_SET_LIGHT) from exc

  def clear_lights(self) -> None:
    """Turn off all indicator lights."""
    self._require_connected().send_command(CommandID.CLEAR_LIGHTS)
    logger.debug("Lights cleared")

  # =================================================================
  # BravoController interface -- Head detection
  # =================================================================

  def read_head_adc(self) -> int:
    """Read the ADC value from the weigh-pad / head-detection resistor."""
    comm = self._require_connected()
    try:
      data = comm.send_command(CommandID.READ_AD_WEIGH_PAD)
      if len(data) < 2:
        raise BravoError(ErrorType.COULD_NOT_DETECT_HEAD)
      adc = int(struct.unpack_from("<H", data, 0)[0])
      logger.debug("Head ADC value: %d", adc)
      return adc
    except BravoError as exc:
      self._set_error(exc)
      raise

  def detect_smart_head(self) -> bool:
    """Detect whether a smart head (PIC / EEPROM) is present.

    Returns:
      True when the Rabbit receives an ACK from the PIC on the head's I2C
      bus.
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
    """Read the head-type code from the smart-head EEPROM at address 0x01."""
    comm = self._require_connected()
    request = SmartHeadEEPROMData(address=EEPROMAddress.HEAD_TYPE, length=1)
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
  # BravoController interface -- Gripper
  # =================================================================

  def detect_gripper(self) -> GripperDetectionState:
    """Detect whether a gripper module is attached."""
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
    """Close the gripper jaws to the given position."""
    comm = self._require_connected()

    tpu = self._ticks_per_unit.get("g", TICKS_PER_MM["g"])
    current = 0.5 if speed == "fast" else 0.3
    params = GripperParams(
      grip_current=current,
      grip_velocity=self._vel_to_ticks_per_ms("g", 10.0),
      grip_acceleration=self._accel_to_ticks_per_ms2("g", 100.0),
      target_position=self._to_ticks("g", position),
      position_tolerance=float(GRIP_POSITION_TOLERANCE),
      max_gripper_current=0.5,
      original_max_pos_error=1000.0,
      original_velocity=self._vel_to_ticks_per_ms("g", 20.0),
      original_acceleration=self._accel_to_ticks_per_ms2("g", 200.0),
      ticks_per_eng_unit=tpu,
    )

    try:
      comm.send_command(CommandID.GRIP, params.pack())
      logger.debug("Grip executed: position=%.3f mm speed=%s", position, speed)
    except BravoError as exc:
      self._set_error(exc)
      raise

  def open_gripper(self, position: Optional[float] = None) -> None:
    """Open the gripper jaws."""
    self.move(
      [
        AxisMoveInfo(
          axis="g",
          position=OPEN_GRIPPER_POSITION if position is None else float(position),
          velocity=20.0,
          acceleration=200.0,
          absolute=True,
        ),
      ]
    )

  def is_plate_in_gripper(self) -> bool:
    """Return whether the gripper's jaw position indicates a plate is held."""
    try:
      pos_ticks = self._to_ticks("g", self.get_position("g"))
      open_ticks = self._to_ticks("g", OPEN_GRIPPER_POSITION)
      return abs(pos_ticks - open_ticks) > GRIP_POSITION_TOLERANCE
    except BravoError:
      return False

  # =================================================================
  # BravoController interface -- Generic command dispatch
  # =================================================================

  def send_command(self, command_id: int, data: bytes = b"", timeout: float = 2.0) -> bytes:
    """Send a raw V11 command, for extensibility beyond this interface."""
    return self._require_connected().send_command(CommandID(command_id), data, timeout)

  # =================================================================
  # BravoController interface -- Last error
  # =================================================================

  @property
  def last_error(self) -> Optional[BravoError]:
    return self._last_error

  # =================================================================
  # Configuration
  # =================================================================

  def set_w_axis_scale(self, ticks_per_ul: float) -> None:
    """Set the W (plunger) axis encoder scale for the installed head.

    Different head types use different syringe volumes and thus different
    ticks-per-uL ratios. Call this after head detection.

    Args:
      ticks_per_ul: The W axis's encoder ticks per microlitre.
    """
    self._ticks_per_unit["w"] = ticks_per_ul
    logger.info("W-axis scale set to %.2f ticks/uL", ticks_per_ul)

  @property
  def firmware_version(self) -> FirmwareVersion:
    """The most recently queried firmware version."""
    return self._firmware_version
