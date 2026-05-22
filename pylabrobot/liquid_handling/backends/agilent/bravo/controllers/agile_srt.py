"""Agile SRT Bravo controller — Agile 7612 wire protocol, SRT hardware.

Supported hardware:
  - Bravo SRT (firmware 5.4.3)

The Bravo SRT speaks the same wire protocol as the Agile 7612: V11 framing
``[cmd][length][data]``, CRC-8/MAXIM, 10-byte Agile packets carried in 0xA1
frames as ``[packet][axis_index]``, the 17-byte PREPARE_MOVE struct, TCP port
7612, and the same controller-verify exchange (header 0x09, register 0x90,
unique value 0x2A55). Decoded from ``captures/bravo_srt.pcapng`` and
``captures/bravo_srt_cold_init.pcapng``.

Differences from Agile 7612:

  - This SRT has no gripper. It has four axes — X, Y, Z, W — and no G/Zg.
    ``HAS_GRIPPER = False`` keeps G/Zg out of the home/park axis list.
  - Four servo controllers indexed 0,1,2,3 (X,Y,Z,W), not the Agile 7612's
    two (0 and 4). Homing servo headers are 0x00/0x10/0x20/0x30.
  - Homing order is Z, W, X, Y.
  - The home_complete_register field in PREPARE_MOVE is encoded 0x01nn.
  - The W (pipettor) axis needs a pump-parameter pre-config block before its
    homing servo config, and a distinct register-0xA0 value.

The homing routine here is reverse-engineered byte-for-byte from
``captures/bravo_srt_cold_init.pcapng`` (VWorks homing the SRT from a cold,
un-homed state). Jog and the Agile 7612 sensor-adaptive homers are NOT reused —
their move parameters and servo constants are tuned for different hardware.
"""

from __future__ import annotations

import logging
import struct

from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile import _axis_bit
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile_7612 import (
    Agile7612Controller,
    _HOME_REG_ENABLE,
    _HOME_REG_HOMED,
    _SERVO_A3_INITIAL,
    _SERVO_A3_SWAPPED,
    _SERVO_A4_INITIAL,
    _SERVO_A4_RESET,
    _SERVO_A4_SWAPPED,
    _home_reg_register,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.base import JogParams
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.agile_7612_crc import crc8_maxim
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import CommandID
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.v11_agile_7612_comm import V11Agile7612DeviceComm
from pylabrobot.liquid_handling.backends.agilent.bravo.transport.tcp import TCPTransport
from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis

logger = logging.getLogger(__name__)

_AGILE_SRT_TCP_PORT = 7612
_SRT_AXES = frozenset({Axis.X, Axis.Y, Axis.Z, Axis.W})
_SRT_HOME_ORDER = (Axis.Z, Axis.W, Axis.X, Axis.Y)
_SRT_HOME_TIMEOUT_MS = 60_000


def _f32(hex4: str) -> float:
    """Decode a little-endian float32 from a 4-byte hex string.

    Storing homing move parameters this way guarantees that re-packing them
    reproduces the exact bytes VWorks sent in the capture.
    """
    return struct.unpack("<f", bytes.fromhex(hex4))[0]


# Per-axis homing parameters, reverse-engineered from bravo_srt_cold_init.pcapng.
#   a0       : register-0xA0 servo value (7 bytes)
#   axis_byte: value placed in AE/B0 servo registers (local axis index + 1)
#   pos      : homing move distance, ticks (positive magnitude)
#   v_fast   : fast (search) phase velocity
#   v_slow   : slow (precision approach) phase velocity
#   accel    : move acceleration
#   depart   : direction sign that moves the axis AWAY from its home sensor
#
# The phase pattern is NOT fixed — it is chosen at runtime from the home-sensor
# state (register 0x10): on-sensor -> 2-phase (depart, then slow approach back);
# off-sensor -> 3-phase (approach, depart overshoot, slow approach). This mirrors
# the firmware/VWorks behaviour and the inherited Agile 7612 homing.
#
# The homing-complete (0x52) marker is sent with an empty data field, matching
# the Agile 7612 homing and the SRT's own Z/W markers in the cold-init capture.
# VWorks carried scenario-specific data bytes in the X/Y markers; replaying
# those (captured from a different home scenario) is unsafe — the data is not
# replayed.
_SRT_HOMING: dict[Axis, dict] = {
    Axis.Z: dict(
        a0=bytes.fromhex("7ae147aeff1000"), axis_byte=3,
        pos=_f32("0024744b"), v_fast=_f32("00008041"), v_slow=_f32("cdcccc3f"),
        accel=_f32("0ad7233e"), depart=+1,
    ),
    Axis.W: dict(
        a0=bytes.fromhex("40f9096b001000"), axis_byte=4,
        pos=_f32("e016814b"), v_fast=_f32("295c8741"), v_slow=_f32("7593d83f"),
        accel=_f32("c4422d3e"), depart=+1,
    ),
    Axis.X: dict(
        a0=bytes.fromhex("60c1762bfd1000"), axis_byte=1,
        pos=_f32("803c404a"), v_fast=_f32("cef77b41"), v_slow=_f32("0c93c93f"),
        accel=_f32("f301013d"), depart=-1,
    ),
    Axis.Y: dict(
        a0=bytes.fromhex("60c1762bfd1000"), axis_byte=2,
        pos=_f32("803c404a"), v_fast=_f32("cef77b41"), v_slow=_f32("0c93c93f"),
        accel=_f32("f301013d"), depart=-1,
    ),
}

# W pump-parameter pre-config block (cold-init capture frames 51-72, sent
# twice). Entries: ("reg", register, 7-byte-hex) servo writes, or
# ("op", byte7) header-0x00 axis-bitmask operations.
_SRT_W_PUMP_BLOCK = (
    ("reg", 0x39, "7e9000000d1000"), ("reg", 0x3A, "695000000c1000"),
    ("reg", 0x75, "70000000001000"), ("reg", 0x76, "90000000001000"),
    ("reg", 0x7C, "40000000fe1000"), ("reg", 0x77, "40000000031000"),
    ("reg", 0x78, "70000000001000"), ("reg", 0x79, "90000000001000"),
    ("reg", 0x7D, "40000000fe1000"), ("reg", 0x7A, "40000000031000"),
    ("op", 0x55),
    ("reg", 0x44, "00000000001000"), ("reg", 0xD8, "4b000000071000"),
    ("reg", 0xDA, "40000000001000"), ("reg", 0xDE, "64000000061000"),
    ("reg", 0xE2, "00000000001000"), ("reg", 0x1F, "00000000001000"),
    ("op", 0x54),
    ("reg", 0x23, "64000000081000"), ("reg", 0x04, "64000000061000"),
    ("reg", 0x03, "66666666fe1000"), ("reg", 0x02, "77777d0fff1000"),
)


class AgileSrtController(Agile7612Controller):
    """Agile controller for Bravo SRT hardware (firmware 5.4.3, port 7612)."""

    # This SRT has no gripper — only X, Y, Z, W. Consulted by Bravo.home() so
    # G/Zg are never added to the home/park axis list. (The profile loader
    # always materialises all six AxisConfig entries from defaults, so the
    # absence of a gripper cannot be expressed via profile.axes.)
    HAS_GRIPPER = False

    def open_tcp(self, address: str) -> None:
        logger.info("Opening Agile SRT TCP connection to %s:%d", address, _AGILE_SRT_TCP_PORT)
        transport = TCPTransport(address, port=_AGILE_SRT_TCP_PORT)
        self._comm = V11Agile7612DeviceComm(transport)
        try:
            self._comm.connect()
            self._post_connect()
        except Exception:
            self._comm = None
            raise

    def open_serial(self, port: str) -> None:
        raise BravoError(ErrorType.COULD_NOT_CONNECT,
                         custom_text="Bravo SRT does not support serial; use Ethernet")

    # =================================================================
    # Homing
    # =================================================================

    def _home_reg_for_axis(self, axis: Axis) -> int:
        """home_complete_register field for PREPARE_MOVE — SRT encodes 0x01nn."""
        return 0x0100 | _home_reg_register(axis)

    def home_axes(self, axes: list[Axis]) -> None:
        unsupported = sorted({a.name for a in axes} - {a.name for a in _SRT_AXES})
        if unsupported:
            raise BravoError(
                ErrorType.COULD_NOT_HOME,
                custom_text=(
                    f"Bravo SRT has no {', '.join(unsupported)} axis "
                    "(this SRT has no gripper)."
                ),
            )
        requested = set(axes)
        # Clear faults on the X/Y/Z controllers before homing (cold-init
        # capture frames 8-10: header 0x00, axis bitmask, byte7=0x31).
        for axis in (Axis.X, Axis.Y, Axis.Z):
            if axis in requested:
                self._srt_axis_op(0x31, axis)
        for axis in _SRT_HOME_ORDER:
            if axis in requested:
                logger.info("SRT homing %s", axis.label)
                self._srt_home_axis(axis)

    def _srt_axis_op(self, byte7: int, axis: Axis, data: bytes = b"") -> None:
        """Send a header-0x00 axis-bitmask op (fault reset / trigger / marker)."""
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
        """Write the W pump-parameter pre-config block (sent twice by VWorks)."""
        for _ in range(2):
            for entry in _SRT_W_PUMP_BLOCK:
                if entry[0] == "reg":
                    _, reg, hex_data = entry
                    self._agile_7612_servo_write(reg, bytes.fromhex(hex_data), Axis.W)
                else:
                    self._srt_axis_op(entry[1], Axis.W)

    def _srt_home_move(self, axis: Axis, position: float, velocity: float,
                       accel: float) -> None:
        comm = self._require_connected()
        info = self._move_info_cls(
            axis=axis, position=position, velocity=velocity, acceleration=accel,
            absolute_move=False, check_for_homed=False,
            home_complete_register=self._home_reg_for_axis(axis),
        )
        comm.send_command(CommandID.PREPARE_MOVE, info.pack())
        self._agile_7612_move_go([axis])
        self._agile_7612_wait_for_settled([axis], timeout_ms=_SRT_HOME_TIMEOUT_MS)

    def _srt_read_home_sensor(self, axis: Axis) -> bool:
        """Read register 0x10 — True if the axis is currently on its home sensor.

        The home-sensor state selects the homing phase pattern. The bit per
        axis is X=0x01, Y=0x02, Z=0x04, W=0x08 (== _axis_bit). The profile's
        home_flag_bitmask is 0 for the SRT, so it cannot be used here.
        """
        try:
            resp = self._agile_7612_ext_read(0x10, axis)
        except BravoError:
            logger.warning("SRT homing %s: 0x10 read failed; assuming off-sensor",
                            axis.label)
            return False
        if len(resp) < 3:
            return False
        on_sensor = bool(resp[2] & _axis_bit(axis))
        logger.info("SRT homing %s: 0x10 sensor byte=0x%02X -> %s",
                    axis.label, resp[2],
                    "on sensor (2-phase)" if on_sensor else "off sensor (3-phase)")
        return on_sensor

    def _srt_home_axis(self, axis: Axis) -> None:
        """Home one SRT axis — sequence decoded from bravo_srt_cold_init.pcapng.

        Read 0x4A, enable the home register, write homing servo config, read
        the home-sensor state (0x10) to pick the phase pattern, run the
        search/approach move phases (each preceded by an A3/A4 servo set, the
        final precision phase using the swapped set), latch the homing-complete
        marker, and write the home register HOMED.

        Phase pattern depends on the home-sensor state:
          - on sensor : 2-phase — depart fast, then slow approach back.
          - off sensor: 3-phase — approach fast, depart overshoot, slow approach.

        W additionally needs the pump-parameter pre-config block first.
        """
        spec = _SRT_HOMING[axis]

        if axis is Axis.W:
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

        self._homed[axis.value] = True
        self._capture_home_position(axis)
        logger.info("Axis %s homed", axis.label)

    def _srt_safe_agile_read(self, register: int, axis: Axis) -> None:
        try:
            self._agile_7612_agile_read(register, axis)
        except BravoError:
            pass

    def _srt_safe_write_home_reg(self, axis: Axis, data: bytes) -> None:
        try:
            self._agile_7612_write_home_reg(axis, data)
        except BravoError:
            pass

    def _agile_7612_fault_reset_ctrl2(self) -> None:
        """No-op on the SRT — there is no controller 2 (no G/Zg)."""
        return

    # =================================================================
    # Jog — not yet reverse-engineered for the SRT
    # =================================================================

    def jog(self, params: JogParams) -> float:
        raise BravoError(
            ErrorType.COULD_NOT_MOVE_TO_POSITION,
            custom_text="Jog is not yet implemented for the Bravo SRT.",
        )
