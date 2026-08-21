"""Agile controller for the Bravo SRT, a gripperless four-axis variant.

The Bravo SRT speaks the same wire protocol as the Agile 7612 generation:
V11 framing with the command byte before the length, CRC-8/MAXIM, 10-byte
Agile packets carried in ``CMD_DIRECT_AGILE_COMMAND`` frames, the 17-byte
``CMD_PREPARE_MOVE`` payload, and the same controller-verify exchange.

It differs from the Agile 7612 in what hardware it drives:

- No gripper: only X, Y, Z, W. :attr:`AgileSrtController.has_gripper` is
  False, and the gripper-related :class:`~.base.BravoController` methods
  raise :class:`NotImplementedError` naming the model.
- Four servo controllers indexed 0-3 (X, Y, Z, W), not the Agile 7612's two
  (0 and 4). Homing servo headers are 0x00/0x10/0x20/0x30.
- Homing order is Z, W, X, Y.
- The ``home_complete_register`` field in ``CMD_PREPARE_MOVE`` is encoded
  as ``0x01nn``.
- The W (pipettor) axis needs a pump-parameter pre-configuration block
  before its homing servo configuration, plus a distinct register-0xA0
  value.

The homing routine here emits a byte-exact frame sequence for a cold,
un-homed start; keep every constant exact when touching it, since even a
structurally-equivalent rewrite can produce a checksum byte the firmware
silently rejects. Jog and the Agile 7612's sensor-adaptive per-axis homers
are not reused here -- their move parameters and servo constants are tuned
for different hardware.
"""

from __future__ import annotations

import logging
import struct
from typing import NamedTuple, NoReturn, Optional, Union

from ..errors import BravoError, ErrorType
from ..protocol.agile_7612_crc import crc8_maxim
from ..protocol.commands import CommandID
from ..types import Axis, GripperDetectionState, SpeedLevel
from .agile import _axis_bit
from .agile_7612 import (
  _HOME_REG_ENABLE,
  _HOME_REG_HOMED,
  _SERVO_A3_INITIAL,
  _SERVO_A3_SWAPPED,
  _SERVO_A4_INITIAL,
  _SERVO_A4_RESET,
  _SERVO_A4_SWAPPED,
  Agile7612Controller,
  _home_reg_register,
)
from .base import JogParams

logger = logging.getLogger(__name__)

_SRT_AXES: frozenset[Axis] = frozenset({"x", "y", "z", "w"})
_SRT_HOME_ORDER: tuple[Axis, ...] = ("z", "w", "x", "y")
_SRT_HOME_TIMEOUT = 60.0  # seconds


def _f32(hex4: str) -> float:
  """Decode a little-endian float32 from a 4-byte hex string.

  Storing homing move parameters as their raw encoded form guarantees that
  re-packing them reproduces the exact bytes the firmware expects, with no
  float round-tripping error.

  Args:
    hex4: An 8-character hex string encoding 4 bytes.

  Returns:
    The decoded float.
  """
  return float(struct.unpack("<f", bytes.fromhex(hex4))[0])


# Per-axis homing parameters for a cold-start home.
#   a0       : register-0xA0 servo value (7 bytes)
#   axis_byte: value placed in the AE/B0 servo registers (local axis index + 1)
#   pos      : homing move distance, ticks (positive magnitude)
#   v_fast   : fast (search) phase velocity
#   v_slow   : slow (precision approach) phase velocity
#   accel    : move acceleration
#   depart   : direction sign that moves the axis away from its home sensor
#
# The phase pattern is not fixed -- it is chosen at runtime from the
# home-sensor state (register 0x10): on-sensor picks a 2-phase pattern
# (depart, then slow approach back); off-sensor picks a 3-phase pattern
# (approach, depart overshoot, slow approach), matching the Agile 7612
# per-axis homers.
#
# The homing-complete (0x52) marker is sent with an empty data field.
_SRT_HOMING: dict[Axis, dict] = {
  "z": dict(
    a0=bytes.fromhex("7ae147aeff1000"),
    axis_byte=3,
    pos=_f32("0024744b"),
    v_fast=_f32("00008041"),
    v_slow=_f32("cdcccc3f"),
    accel=_f32("0ad7233e"),
    depart=1,
  ),
  "w": dict(
    a0=bytes.fromhex("40f9096b001000"),
    axis_byte=4,
    pos=_f32("e016814b"),
    v_fast=_f32("295c8741"),
    v_slow=_f32("7593d83f"),
    accel=_f32("c4422d3e"),
    depart=1,
  ),
  "x": dict(
    a0=bytes.fromhex("60c1762bfd1000"),
    axis_byte=1,
    pos=_f32("803c404a"),
    v_fast=_f32("cef77b41"),
    v_slow=_f32("0c93c93f"),
    accel=_f32("f301013d"),
    depart=-1,
  ),
  "y": dict(
    a0=bytes.fromhex("60c1762bfd1000"),
    axis_byte=2,
    pos=_f32("803c404a"),
    v_fast=_f32("cef77b41"),
    v_slow=_f32("0c93c93f"),
    accel=_f32("f301013d"),
    depart=-1,
  ),
}


class _PumpStep(NamedTuple):
  """One entry in the W pump-parameter pre-config block.

  Attributes:
    kind: ``"reg"`` for a servo register write, or ``"op"`` for a
      header-0x00 axis-bitmask operation.
    reg_or_byte7: The servo register address (``"reg"``) or the subtype
      byte placed at packet offset 7 (``"op"``).
    hex_data: The 7-byte register value, as hex. Empty for ``"op"``.
  """

  kind: str
  reg_or_byte7: int
  hex_data: str = ""


# W pump-parameter pre-config block, sent twice during a cold init.
_SRT_W_PUMP_BLOCK: tuple[_PumpStep, ...] = (
  _PumpStep("reg", 0x39, "7e9000000d1000"),
  _PumpStep("reg", 0x3A, "695000000c1000"),
  _PumpStep("reg", 0x75, "70000000001000"),
  _PumpStep("reg", 0x76, "90000000001000"),
  _PumpStep("reg", 0x7C, "40000000fe1000"),
  _PumpStep("reg", 0x77, "40000000031000"),
  _PumpStep("reg", 0x78, "70000000001000"),
  _PumpStep("reg", 0x79, "90000000001000"),
  _PumpStep("reg", 0x7D, "40000000fe1000"),
  _PumpStep("reg", 0x7A, "40000000031000"),
  _PumpStep("op", 0x55),
  _PumpStep("reg", 0x44, "00000000001000"),
  _PumpStep("reg", 0xD8, "4b000000071000"),
  _PumpStep("reg", 0xDA, "40000000001000"),
  _PumpStep("reg", 0xDE, "64000000061000"),
  _PumpStep("reg", 0xE2, "00000000001000"),
  _PumpStep("reg", 0x1F, "00000000001000"),
  _PumpStep("op", 0x54),
  _PumpStep("reg", 0x23, "64000000081000"),
  _PumpStep("reg", 0x04, "64000000061000"),
  _PumpStep("reg", 0x03, "66666666fe1000"),
  _PumpStep("reg", 0x02, "77777d0fff1000"),
)


class AgileSrtController(Agile7612Controller):
  """Agile 7612-protocol controller for the gripperless Bravo SRT."""

  has_gripper = False
  model_name = "Bravo SRT"

  def _no_gripper(self, operation: str) -> NoReturn:
    """Raise, naming this model, for an operation that needs a gripper.

    Args:
      operation: The name of the unsupported operation.

    Raises:
      NotImplementedError: Always.
    """
    raise NotImplementedError(f"{self.model_name} has no gripper; {operation} is not available.")

  # =================================================================
  # Homing
  # =================================================================

  def _home_reg_for_axis(self, axis: Axis) -> int:
    """Return the ``home_complete_register`` field for ``CMD_PREPARE_MOVE``.

    The SRT encodes this field as ``0x01nn``, where ``nn`` is the same
    per-axis register the Agile 7612 generation uses directly.
    """
    return 0x0100 | _home_reg_register(axis)

  def home_axes(self, axes: list[Axis], *, force: bool = False) -> None:
    """Home the given axes in safety order: Z, W, X, Y.

    Args:
      axes: The axes to home. Must be a subset of X, Y, Z, W.
      force: Unused. Homing always runs unconditionally for the requested
        axes.

    Raises:
      BravoError: If ``axes`` includes G or Zg, which this SRT has no
        hardware for.
    """
    unsupported = sorted({a for a in axes} - _SRT_AXES)
    if unsupported:
      raise BravoError(
        ErrorType.COULD_NOT_HOME,
        custom_text=(
          f"{self.model_name} has no {', '.join(unsupported)} axis (this SRT has no gripper)."
        ),
      )
    requested = set(axes)
    # Clear faults on the X/Y/Z controllers before homing (header 0x00,
    # axis bitmask, byte 7 = 0x31).
    for axis in ("x", "y", "z"):
      if axis in requested:
        self._srt_axis_op(0x31, axis)
    for axis in _SRT_HOME_ORDER:
      if axis in requested:
        logger.info("SRT homing %s", axis)
        self._srt_home_axis(axis)

  def _srt_axis_op(self, byte7: int, axis: Axis, data: bytes = b"") -> None:
    """Send a header-0x00 axis-bitmask op (fault reset / trigger / marker).

    Args:
      byte7: The subtype byte to place at packet offset 7.
      axis: The axis this op targets.
      data: Up to 5 bytes to place at packet offset 2.
    """
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = _axis_bit(axis)
    for i, b in enumerate(data[:5]):
      raw[2 + i] = b
    raw[7] = byte7 & 0xFF
    raw[9] = crc8_maxim(raw, 9)
    self._send_agile(bytes(raw), axis)

  def _srt_servo_config(self, axis: Axis) -> None:
    """Write the six homing servo registers (A0, AD, AE, AF, B0, BD)."""
    spec = _SRT_HOMING[axis]
    ab = spec["axis_byte"]
    ae_b0 = bytes.fromhex("40000000") + bytes([ab]) + bytes.fromhex("1000")
    for reg, data in (
      (0xA0, spec["a0"]),
      (0xAD, bytes.fromhex("488000000c1000")),
      (0xAE, ae_b0),
      (0xAF, bytes.fromhex("00000000001000")),
      (0xB0, ae_b0),
      (0xBD, bytes.fromhex("00000000001000")),
    ):
      self._agile_7612_servo_write(reg, data, axis)

  def _srt_w_pump_preconfig(self) -> None:
    """Write the W pump-parameter pre-config block, sent twice as the firmware expects."""
    for _ in range(2):
      for step in _SRT_W_PUMP_BLOCK:
        if step.kind == "reg":
          self._agile_7612_servo_write(step.reg_or_byte7, bytes.fromhex(step.hex_data), "w")
        else:
          self._srt_axis_op(step.reg_or_byte7, "w")

  def _srt_home_move(self, axis: Axis, position: float, velocity: float, accel: float) -> None:
    """Send one ``CMD_PREPARE_MOVE`` homing-search phase and wait for it to settle.

    Args:
      axis: The axis to move.
      position: The move distance, in ticks, relative to the axis's
        current position.
      velocity: The move velocity, in ticks/ms.
      accel: The move acceleration, in ticks/ms^2.
    """
    comm = self._require_connected()
    info = self._move_info_cls(
      axis=axis,
      position=position,
      velocity=velocity,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=self._home_reg_for_axis(axis),
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=_SRT_HOME_TIMEOUT)

  def _srt_read_home_sensor(self, axis: Axis) -> bool:
    """Read register 0x10 to check whether an axis is on its home sensor.

    The home-sensor state selects the homing phase pattern. The bit per
    axis is X=0x01, Y=0x02, Z=0x04, W=0x08 (the same as :func:`_axis_bit`).
    The SRT's axis configuration leaves ``home_flag_bitmask`` at its zero
    default, so that field cannot be used here.

    Args:
      axis: The axis to read.

    Returns:
      True if the axis is currently on its home sensor.
    """
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
    except BravoError:
      logger.warning("SRT homing %s: 0x10 read failed; assuming off-sensor", axis)
      return False
    if len(resp) < 3:
      return False
    on_sensor = bool(resp[2] & _axis_bit(axis))
    logger.info(
      "SRT homing %s: 0x10 sensor byte=0x%02X -> %s",
      axis,
      resp[2],
      "on sensor (2-phase)" if on_sensor else "off sensor (3-phase)",
    )
    return on_sensor

  def _srt_home_axis(self, axis: Axis) -> None:
    """Home one SRT axis from a cold, un-homed start.

    Reads register 0x4A, enables the home register, writes the homing
    servo configuration, reads the home-sensor state (register 0x10) to
    pick the phase pattern, runs the search/approach move phases (each
    preceded by an A3/A4 servo set, with the final precision phase using
    the swapped set), latches the homing-complete marker, and writes the
    home register HOMED.

    Phase pattern depends on the home-sensor state:

    - On sensor: 2-phase -- depart fast, then slow approach back.
    - Off sensor: 3-phase -- approach fast, depart overshoot, slow
      approach.

    W additionally needs the pump-parameter pre-config block first.

    Args:
      axis: The axis to home.
    """
    spec = _SRT_HOMING[axis]

    if axis == "w":
      self._srt_w_pump_preconfig()
      self._srt_safe_agile_read(0x4A, axis)
      self._srt_axis_op(0x30, axis)

    self._srt_safe_agile_read(0x4A, axis)
    self._srt_safe_write_home_reg(axis, _HOME_REG_ENABLE)
    self._srt_servo_config(axis)

    depart = spec["depart"]
    if self._srt_read_home_sensor(axis):
      moves = [(depart, "fast"), (-depart, "slow")]
    else:
      moves = [(-depart, "fast"), (depart, "fast"), (-depart, "slow")]

    for idx, (sign, speed) in enumerate(moves):
      is_final = idx == len(moves) - 1
      if is_final:
        self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
        self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
      else:
        self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
        self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      velocity = spec["v_slow"] if speed == "slow" else spec["v_fast"]
      self._srt_home_move(axis, sign * spec["pos"], velocity, spec["accel"])

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      self._srt_axis_op(0x52, axis)  # homing-complete marker (empty data)
    except BravoError:
      pass
    self._srt_safe_write_home_reg(axis, _HOME_REG_HOMED)

    self._homed[axis] = True
    self._capture_home_position(axis)
    logger.info("Axis %s homed", axis)

  def _srt_safe_agile_read(self, register: int, axis: Axis) -> None:
    """Read a register, discarding any error.

    Args:
      register: The register to read.
      axis: The axis to read it from.
    """
    try:
      self._agile_7612_agile_read(register, axis)
    except BravoError:
      pass

  def _srt_safe_write_home_reg(self, axis: Axis, data: bytes) -> None:
    """Write an axis's home-complete register, discarding any error.

    Args:
      axis: The axis whose register to write.
      data: The 7-byte register value.
    """
    try:
      self._agile_7612_write_home_reg(axis, data)
    except BravoError:
      pass

  def _agile_7612_fault_reset_ctrl2(self) -> None:
    """Do nothing: this SRT has no controller 2 (no G/Zg)."""
    return

  # =================================================================
  # Jog -- not yet implemented for the SRT
  # =================================================================

  def jog(self, params: JogParams) -> float:
    """Execute a force-controlled jog move.

    Raises:
      BravoError: Always. Force-controlled jog is not implemented for the
        Bravo SRT (its move parameters and servo constants are tuned for
        the Agile 7612, not this hardware).
    """
    raise BravoError(
      ErrorType.COULD_NOT_MOVE_TO_POSITION,
      custom_text=f"Jog is not yet implemented for the {self.model_name}.",
    )

  # =================================================================
  # Gripper -- this SRT has none
  # =================================================================

  def detect_gripper(self) -> GripperDetectionState:
    """Return whether the gripper accessory is currently detected.

    Raises:
      NotImplementedError: Always. This SRT has no gripper.
    """
    self._no_gripper("detect_gripper")

  def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
    """Close the gripper jaws to the given position.

    Raises:
      NotImplementedError: Always. This SRT has no gripper.
    """
    self._no_gripper("grip")

  def open_gripper(self, position: Optional[float] = None) -> None:
    """Open the gripper jaws.

    Raises:
      NotImplementedError: Always. This SRT has no gripper.
    """
    self._no_gripper("open_gripper")

  def is_plate_in_gripper(self) -> bool:
    """Return whether a plate is currently held in the gripper.

    Raises:
      NotImplementedError: Always. This SRT has no gripper.
    """
    self._no_gripper("is_plate_in_gripper")

  def scan_stack_with_gripper(
    self,
    *,
    start_zg: float,
    end_zg: float,
    speed: SpeedLevel,
    transient: float = 0.0,
  ) -> dict[str, Union[float, bool, None]]:
    """Scan the Zg axis between two heights until the plate sensor detects a stack top.

    Raises:
      NotImplementedError: Always. This SRT has no gripper.
    """
    self._no_gripper("scan_stack_with_gripper")
