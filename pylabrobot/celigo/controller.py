"""High-level command layer for the Celigo USB-IO controller.

All payload fields are big-endian, written after the 11-byte packet header (see
:mod:`.packets`). The class wraps a byte :class:`~pylabrobot.celigo.packets.Transport`
(e.g. :class:`~pylabrobot.celigo.transport.FtdiTransport`) and a sequence counter.
"""

from __future__ import annotations

import enum
import struct
import time
from dataclasses import dataclass
from typing import List, Tuple

from pylabrobot.celigo import ezstepper
from pylabrobot.celigo.packets import (
  IO_CTLR_CMDS,
  Sequencer,
  Transport,
  USBIOError,
  transact,
)

# DAC characteristics.
DAC_MAX_VOLTAGE = 10.0
DAC_MIN_VOLTAGE = -10.0
DAC_ZERO_VOLTS = 32767.5  # 16-bit DAC midpoint
DAC_PER_VOLT = 3276.75
ANALOG_DAC_FULL_SCALE = 4095.0  # 12-bit per-channel analog DACs


class GalvoType(enum.IntEnum):
  X = 0
  Y = 1


class LaserType(enum.IntEnum):
  LASER_1 = 0
  LASER_2 = 1


class ControllerStatus(enum.IntFlag):
  """``CONTROLLER_STATUS`` bit flags returned by :meth:`CeligoController.get_status`."""

  EMPTY = 0
  CTLR_BUSY = 1
  CTLR_ERROR = 2
  INTERLOCK_SW_OPEN = 4
  CONTROLLER_FAIL = 8


class SignalDiagnosticCommand(enum.IntEnum):
  NO_OPERATION = 0
  SET_TRIGGER = 1
  CLEAR_TRIGGER = 2
  PULSE_TRIGGER = 3
  READ_BUSY = 4
  READ_INTEGRATION = 5
  READ_ENCODER = 6


# ``EXT_STAT_WORD`` values needed to interpret motor-query responses.
EXT_NO_CTLR_ERROR = 0
EXT_NO_MOTOR_NUMBER = 5011
EXT_BAD_MOTOR_NUMBER = 5012
EXT_MOTOR_COM_ERROR = 5025


def volts_to_dac_units(volts: float) -> int:
  """16-bit galvo DAC: clamp to +/-10 V then map about the midpoint."""
  clamped = max(DAC_MIN_VOLTAGE, min(volts, DAC_MAX_VOLTAGE))
  return int(min(65535.0, max(0.0, round(clamped * DAC_PER_VOLT + DAC_ZERO_VOLTS))))


def dac_units_to_volts(dac: int) -> float:
  """Inverse of :func:`volts_to_dac_units`."""
  return (dac - DAC_ZERO_VOLTS) / DAC_PER_VOLT


def volts_to_analog_dac(volts: float, min_voltage: float, max_voltage: float) -> int:
  """12-bit per-channel analog DAC.

  Values outside ``[min, max]`` are not clamped; the result is masked to 16 bits.
  """
  scaled = (volts - min_voltage) / (max_voltage - min_voltage) * ANALOG_DAC_FULL_SCALE
  return int(scaled) & 0xFFFF


def analog_dac_to_volts(dac: int, min_voltage: float, max_voltage: float) -> float:
  """Inverse of :func:`volts_to_analog_dac`."""
  return dac / ANALOG_DAC_FULL_SCALE * (max_voltage - min_voltage) + min_voltage


@dataclass
class MotorConnection:
  """A motor discovered by :meth:`CeligoController.get_motor_configuration` (UART index + motor index)."""

  uart_index: int
  motor_index: int


class CeligoController:
  """Issues USB-IO board commands over a byte transport.

  ``transport`` must already be open.
  """

  def __init__(self, transport: Transport, sequencer: "Sequencer | None" = None):
    self.transport = transport
    self._seq = sequencer or Sequencer()

  def _transact(self, cmd: IO_CTLR_CMDS, payload: bytes = b"") -> bytes:
    return transact(self.transport, cmd, self._seq.next(), payload)

  # -- controller status / lifecycle -----------------------------------------

  def get_status(self) -> Tuple[ControllerStatus, int]:
    """Read controller status -> (status flags, extended status word)."""
    resp = self._transact(IO_CTLR_CMDS.CONTROLLER_STATUS)
    status, ext = struct.unpack_from(">II", resp, 0)
    return ControllerStatus(status), ext

  def is_busy(self) -> bool:
    status, _ = self.get_status()
    return bool(status & ControllerStatus.CTLR_BUSY)

  def wait_for_ready(self, timeout_ms: int = 5000, poll_ms: int = 10) -> bool:
    """Poll :meth:`get_status` until not busy or timeout."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while self.is_busy():
      if time.monotonic() >= deadline:
        return False
      time.sleep(poll_ms / 1000.0)
    return True

  def reset_controller(self) -> None:
    self._transact(IO_CTLR_CMDS.RESET_CONTROLLER)

  def abort_command(self) -> None:
    self._transact(IO_CTLR_CMDS.ABORT_CMD)
    time.sleep(0.050)  # settle 50 ms after abort

  # -- galvo ------------------------------------------------------------------

  def move_galvo(
    self,
    galvo: GalvoType,
    voltage: float,
    wait_for_ready: bool = False,
    timeout_ms: int = 0,
  ) -> bool:
    """Move a galvo axis. Payload = uint16 galvo, int32 dac, uint16 wait, uint16 timeout.

    .. warning::
       The ``MOVE_GALVO`` payload layout is UNVERIFIED. There are two candidate layouts:
       (A) ``[galvo:u16][dac:i32][wait:u16][timeout:u16]`` (10 bytes, current implementation)
       and (B) ``[chan:u16][dac:u16][rate:u32]`` (8 bytes). Verify the correct layout before
       relying on galvo positioning.
    """
    dac = volts_to_dac_units(voltage)
    payload = struct.pack(">HiHH", int(galvo), dac, 1 if wait_for_ready else 0, timeout_ms)
    resp = self._transact(IO_CTLR_CMDS.MOVE_GALVO, payload)
    if wait_for_ready and len(resp) >= 2:
      return bool(struct.unpack_from(">H", resp, 0)[0] == 0)
    return True

  # -- analog IO --------------------------------------------------------------

  def set_analog_out(
    self, channel: int, voltage: float, min_voltage: float, max_voltage: float
  ) -> None:
    """Set an analog output. Payload = uint16 channel, uint16 dac12 (illumination intensity)."""
    dac = volts_to_analog_dac(voltage, min_voltage, max_voltage)
    self._transact(IO_CTLR_CMDS.WRITE_DA_CHANNEL, struct.pack(">HH", channel, dac))

  def write_dac_raw(self, channel: int, value: int) -> None:
    """Write ``WRITE_DA_CHANNEL`` with a raw 12-bit DAC count (e.g. brightfield = 3276)."""
    self._transact(IO_CTLR_CMDS.WRITE_DA_CHANNEL, struct.pack(">HH", channel, value & 0xFFFF))

  def read_dac_raw(self, channel: int) -> int:
    """Read ``GET_ANALOG_OUT_VALUE`` raw count for a channel (response = echo + value)."""
    resp = self._transact(IO_CTLR_CMDS.GET_ANALOG_OUT_VALUE, struct.pack(">H", channel))
    _echo, value = struct.unpack_from(">HH", resp, 0)
    return int(value)

  def get_analog_out(self, channel: int, min_voltage: float, max_voltage: float) -> float:
    """Read an analog output channel. Response = uint16 (echo) + uint16 dac."""
    resp = self._transact(IO_CTLR_CMDS.GET_ANALOG_OUT_VALUE, struct.pack(">H", channel))
    _echo, dac = struct.unpack_from(">HH", resp, 0)
    return analog_dac_to_volts(dac, min_voltage, max_voltage)

  def get_analog_input(self, channel: int, min_voltage: float, max_voltage: float) -> float:
    """Read an analog input channel. Response = uint16 dac (e.g. HWAF sensor)."""
    resp = self._transact(IO_CTLR_CMDS.READ_AD_CHANNEL, struct.pack(">H", channel))
    (dac,) = struct.unpack_from(">H", resp, 0)
    return analog_dac_to_volts(dac, min_voltage, max_voltage)

  # -- digital IO -------------------------------------------------------------

  def read_digital_inputs(self) -> int:
    """Read all digital inputs -> raw 16-bit input port value."""
    resp = self._transact(IO_CTLR_CMDS.READ_DIG_PORT)
    return int(struct.unpack_from(">H", resp, 0)[0])

  def read_digital_outputs(self) -> int:
    """Read all digital outputs -> raw 16-bit output port value."""
    resp = self._transact(IO_CTLR_CMDS.GET_DIG_OUT_VALUE)
    return int(struct.unpack_from(">H", resp, 0)[0])

  def read_digital_input(self, bit_index: int) -> bool:
    return bool(self.read_digital_inputs() & (1 << bit_index))

  def get_digital_out_bit(self, bit_index: int) -> bool:
    return bool(self.read_digital_outputs() & (1 << bit_index))

  def set_digital_out_bit(self, bit_index: int, value: bool) -> None:
    mask = (1 << bit_index) & 0xFFFF
    cmd = IO_CTLR_CMDS.SET_DIG_PORT_BITS if value else IO_CTLR_CMDS.CLEAR_DIG_PORT_BITS
    self._transact(cmd, struct.pack(">H", mask))

  # -- autofocus --------------------------------------------------------------

  def arm_autofocus(self, current_encoder: int, start_encoder: int, capture_count: int) -> None:
    """Arm the autofocus sweep. Payload = int32 current, int32 start, uint16 count."""
    payload = struct.pack(">iiH", current_encoder, start_encoder, capture_count)
    self._transact(IO_CTLR_CMDS.AUTO_FOCUS, payload)

  def get_autofocus_positions(self) -> List[int]:
    """Retrieve autofocus positions. Response = int16 count, then count x int16."""
    resp = self._transact(IO_CTLR_CMDS.SEND_FOCUS_POINTS)
    (count,) = struct.unpack_from(">h", resp, 0)
    positions: List[int] = []
    offset = 2
    for _ in range(count):
      positions.append(struct.unpack_from(">h", resp, offset)[0])
      offset += 2
    return positions

  def signal_diagnostics(self, operation: SignalDiagnosticCommand) -> int:
    """Send a signal diagnostics command. Payload = int16 op, response = int32."""
    resp = self._transact(IO_CTLR_CMDS.SIGNAL_DIAGNOSTICS, struct.pack(">h", int(operation)))
    return int(struct.unpack_from(">i", resp, 0)[0])

  # -- motors (AllMotion EZStepper, tunneled) ---------------------------------

  def send_motor_query(self, command: str, oem_protocol: bool = True) -> str:
    """Send an EZStepper command string and return the device reply.

    Uses the WLEN/OEM path: opcode ``MOTOR_CMD_QUERY_WLEN`` (47) with the command
    wrapped as ``STX+addr+'1'+tokens+ETX+xor`` (:func:`ezstepper.to_oem_packet`). Set
    ``oem_protocol=False`` for the legacy DT path (opcode 44, ASCII+NUL).

    Response framing is the same either way: uint16 ext-status, then (on
    ``NO_CTLR_ERROR``) uint16 length + that many ASCII bytes; for OEM the ASCII is
    unwrapped via :func:`ezstepper.from_oem_response`. Raises :class:`USBIOError` on a
    motor-number or comm error.
    """
    if oem_protocol:
      payload = ezstepper.to_oem_packet(command)
      opcode = IO_CTLR_CMDS.MOTOR_CMD_QUERY_WLEN
    else:
      payload = command.encode("ascii") + b"\x00"
      opcode = IO_CTLR_CMDS.MOTOR_CMD_QUERY
    if len(payload) > 512:
      raise ValueError("Motor command strings must be < 512 bytes.")
    resp = self._transact(opcode, payload)
    (ext,) = struct.unpack_from(">H", resp, 0)
    if ext in (EXT_NO_MOTOR_NUMBER, EXT_BAD_MOTOR_NUMBER):
      raise USBIOError(f"Invalid motor number (status {ext}) for command {command!r}")
    if ext == EXT_MOTOR_COM_ERROR:
      raise USBIOError(f"Motor communication error for command {command!r}")
    if ext != EXT_NO_CTLR_ERROR:
      raise USBIOError(f"Unexpected motor status {ext} for command {command!r}")
    (length,) = struct.unpack_from(">H", resp, 2)
    reply = resp[4 : 4 + length].decode("latin-1")
    return ezstepper.from_oem_response(reply) if oem_protocol else reply

  def get_motor_configuration(self) -> List[MotorConnection]:
    """Query motor configuration: 8 UARTs x (1 status byte + 4 motor slots).

    A slot value of 127 means "empty"; anything else is a present motor index.
    """
    resp = self._transact(IO_CTLR_CMDS.SEND_MOTOR_CONFIG)
    motors: List[MotorConnection] = []
    offset = 0
    for uart in range(8):
      offset += 1  # per-UART status byte
      for _ in range(4):
        slot = resp[offset]
        offset += 1
        if slot != 127:
          motors.append(MotorConnection(uart_index=uart, motor_index=slot))
    return motors

  # -- barcode ----------------------------------------------------------------

  def send_barcode_command(self, command: str) -> None:
    """Send ASCII bytes to the barcode reader."""
    self._transact(IO_CTLR_CMDS.SEND_BARCODE_MSG, command.encode("ascii"))

  def read_barcode_response(self) -> str:
    """Read the ASCII response from the barcode reader."""
    resp = self._transact(IO_CTLR_CMDS.READ_BARCODE_MSG)
    return resp.decode("ascii", errors="replace")

  # -- axis helpers (EZStepper string commands via send_motor_query) ----------

  def send_ezstepper(self, command: str) -> "ezstepper.EZStepperResponse":
    """Send a built EZStepper command string and parse the reply."""
    return ezstepper.parse_response(self.send_motor_query(command))

  def home_axis(self, axis_index: int, argument: int = 0) -> "ezstepper.EZStepperResponse":
    return self.send_ezstepper(ezstepper.home(axis_index, argument))

  def move_axis_absolute(self, axis_index: int, position: int) -> "ezstepper.EZStepperResponse":
    return self.send_ezstepper(ezstepper.move_absolute(axis_index, position))

  def move_axis_relative(self, axis_index: int, steps: int) -> "ezstepper.EZStepperResponse":
    return self.send_ezstepper(ezstepper.move_relative(axis_index, steps))

  def get_encoder_position(self, axis_index: int) -> int:
    """Query an axis encoder position (``?8``) and return it as an int."""
    resp = self.send_ezstepper(ezstepper.query_encoder_position(axis_index))
    return int(resp.data)
