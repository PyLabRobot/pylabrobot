"""Wire protocol for the Celigo USB-IO controller board.

The board is an FTDI-based motion/IO controller. Every exchange is a request packet
followed by a response packet, all multi-byte fields **big-endian**.

Request (TX) layout::

    offset 0 : command opcode            (1 byte, IO_CTLR_CMDS)
    offset 1 : sequence number           (int32)
    offset 5 : total packet length       (int32, includes the 11-byte header)
    offset 9 : fletcher16 A, B           (2 bytes, computed over the first 9 bytes)
    offset 11: payload                   (big-endian fields)

Response (RX) layout::

    offset 0 : ack status                (1 byte, ACK_STATE; 0 == OK)
    offset 1 : command opcode echo       (1 byte)
    offset 2 : sequence number echo      (int32)
    offset 6 : payload length            (int32)
    offset 10: fletcher16 A, B           (2 bytes, computed over the first 10 bytes)
    offset 12: payload                   (big-endian fields, ``payload length`` bytes)

Note the asymmetry: the TX header is 11 bytes and has no ack byte; the RX header is
12 bytes and prepends the ack byte. The TX checksum covers only the 9-byte header
prefix (the payload is not checksummed on the way out); the RX checksum covers the
10-byte header prefix.
"""

import enum
import struct
import threading
from typing import Protocol


class IO_CTLR_CMDS(enum.IntEnum):
  """Command opcodes (``IO_CTLR_CMDS``), byte 0 of every packet."""

  NO_OPERATION = 0
  LOAD_FIRING_TABLE = 1
  MOTOR_CMD = 2
  ABORT_CMD = 3
  MOTOR_RESPONSE = 4
  MOTOR_TX_STATE = 5
  FIRE_GALVO_GRID = 6
  MOVE_GALVO = 7
  MOVE_GALVOS = 8
  SEND_MOTOR_CONFIG = 9
  WRITE_EEPROM = 10
  READ_EEPROM = 11
  SEND_GALVO_INFO = 12
  TARGETED_FIRE = 13
  WRITE_DIG_PORT = 14
  READ_DIG_PORT = 15
  SET_DIG_PORT_BITS = 16
  CLEAR_DIG_PORT_BITS = 17
  WRITE_DA_CHANNEL = 18
  WRITE_ALL_DA_CHANNELS = 19
  READ_AD_CHANNEL = 20
  READ_ALL_AD_CHANNELS = 21
  SEND_CONFIG = 22
  CONTROLLER_STATUS = 23
  FIRE_LASER = 24
  RESET_CONTROLLER = 25
  TX_LASER_COMM = 26
  CALIBRATE_GALVO = 27
  GET_GALVO_CAL_DATA = 28
  SET_GALVO_WINDOW = 29
  ARM_GALVO_CAPTURE = 30
  GET_GALVO_POS_DATA = 31
  RX_LASER_COMM = 32
  WAIT_GALVO_READY = 33
  GET_DIG_OUT_VALUE = 34
  GET_ANALOG_OUT_VALUE = 35
  AUTO_FOCUS = 36
  SEND_FOCUS_POINTS = 37
  CLR_PULSE_CATCHER = 38
  SEND_PULSE_DATA = 39
  FACTORY_TEST = 40
  SET_TRACE = 41
  TRIGGERED_ACQUISITION = 42
  SIGNAL_DIAGNOSTICS = 43
  MOTOR_CMD_QUERY = 44
  SEND_BARCODE_MSG = 45
  READ_BARCODE_MSG = 46
  MOTOR_CMD_QUERY_WLEN = 47


class ACK_STATE(enum.IntEnum):
  """Response status byte (``ACK_STATE``), byte 0 of every response packet."""

  ACK_OK = 0
  NACK_INVALID_CHKSUM = 1
  NACK_INVALID_COMMAND = 2
  NACK_CMD_READ_FAILED = 3
  NACK_CMD_REJECT = 4
  NACK_INVALID_PARAMETER = 5


_ACK_MESSAGES = {
  ACK_STATE.NACK_INVALID_CHKSUM: "Invalid command checksum",
  ACK_STATE.NACK_INVALID_COMMAND: "Invalid command",
  ACK_STATE.NACK_CMD_READ_FAILED: "Command read failed",
  ACK_STATE.NACK_CMD_REJECT: "Command rejected",
  ACK_STATE.NACK_INVALID_PARAMETER: "Invalid parameter",
}

TX_HEADER_SIZE = 11
RX_HEADER_SIZE = 12

# ack codes that trigger a retry with an RX/TX purge between attempts.
_RETRYABLE = frozenset({ACK_STATE.NACK_INVALID_CHKSUM, ACK_STATE.NACK_CMD_READ_FAILED})


class USBIOError(Exception):
  """Raised when the controller NACKs a command or a response is malformed."""

  def __init__(self, message: str, ack: "ACK_STATE | None" = None):
    super().__init__(message)
    self.ack = ack


def fletcher16(data: bytes, length: int) -> "tuple[int, int]":
  """Fletcher-16 checksum algorithm.

  Seeds are 0xFF/0xFF, processed in blocks of 21 bytes with a fold after each block,
  and a final fold. The two single-byte check values are returned as ``(checkA, checkB)``.
  """
  s1 = 0xFF
  s2 = 0xFF
  i = 0
  remaining = length
  while remaining > 0:
    block = 21 if remaining > 21 else remaining
    remaining -= block
    while block > 0:
      s1 = (s1 + data[i]) & 0xFFFF
      i += 1
      s2 = (s2 + s1) & 0xFFFF
      block -= 1
    s1 = (s1 & 0xFF) + (s1 >> 8)
    s2 = (s2 & 0xFF) + (s2 >> 8)
  s1 = (s1 & 0xFF) + (s1 >> 8)
  s2 = (s2 & 0xFF) + (s2 >> 8)
  return s1 & 0xFF, s2 & 0xFF


def build_tx_packet(cmd: IO_CTLR_CMDS, sequence: int, payload: bytes = b"") -> bytes:
  """Serialize a request packet (11-byte header + payload)."""
  total_length = TX_HEADER_SIZE + len(payload)
  header = bytearray(TX_HEADER_SIZE)
  header[0] = int(cmd)
  struct.pack_into(">i", header, 1, sequence)
  struct.pack_into(">i", header, 5, total_length)
  check_a, check_b = fletcher16(header, 9)
  header[9] = check_a
  header[10] = check_b
  return bytes(header) + payload


class Transport(Protocol):
  """Minimal byte transport the packet layer needs (an open FTDI device)."""

  def write(self, data: bytes) -> int: ...

  def read(self, n: int) -> bytes: ...

  def purge(self) -> None: ...


class Sequencer:
  """Thread-safe monotonic sequence-number source."""

  def __init__(self, start: int = 1):
    self._value = start
    self._lock = threading.Lock()

  def next(self) -> int:
    with self._lock:
      self._value += 1
      return self._value


def _read_exact(transport: Transport, n: int) -> bytes:
  buf = transport.read(n)
  if len(buf) != n:
    raise USBIOError(f"Short read: expected {n} bytes, got {len(buf)}")
  return buf


def transact(
  transport: Transport,
  cmd: IO_CTLR_CMDS,
  sequence: int,
  payload: bytes = b"",
  retries: int = 3,
) -> bytes:
  """Send a command and return the response payload (``b""`` if there is none).

  Attempts up to ``retries`` times, purging the transport on a retryable NACK
  (checksum or read failure) before retrying. Raises :class:`USBIOError` on a
  non-retryable NACK, a checksum failure, or a command/sequence mismatch.
  """
  tx = build_tx_packet(cmd, sequence, payload)
  last_error: "USBIOError | None" = None
  for attempt in range(retries):
    transport.write(tx)
    try:
      return _read_response(transport, cmd, sequence)
    except USBIOError as exc:
      last_error = exc
      if exc.ack in _RETRYABLE and attempt < retries - 1:
        transport.purge()
        continue
      raise
  assert last_error is not None
  raise last_error


def _read_response(transport: Transport, cmd: IO_CTLR_CMDS, sequence: int) -> bytes:
  header = _read_exact(transport, RX_HEADER_SIZE)
  ack = header[0]
  echo_cmd = header[1]
  echo_seq = struct.unpack_from(">i", header, 2)[0]
  payload_length = struct.unpack_from(">i", header, 6)[0]
  check_a, check_b = header[10], header[11]

  expect_a, expect_b = fletcher16(header, 10)
  if (check_a, check_b) != (expect_a, expect_b):
    raise USBIOError(
      f"Response checksum failure for command {cmd.name}, sequence {sequence}",
      ack=ACK_STATE.NACK_INVALID_CHKSUM,
    )

  if ack != ACK_STATE.ACK_OK:
    ack_state = ACK_STATE(ack) if ack in ACK_STATE._value2member_map_ else None
    if ack_state is None:
      message = f"Unknown ack state: {ack}"
    else:
      message = _ACK_MESSAGES.get(ack_state, f"Unknown ack state: {ack}")
    raise USBIOError(f"{message} (command {cmd.name})", ack=ack_state)

  if echo_cmd != int(cmd):
    raise USBIOError(f"Unexpected command in response. Expected {cmd.name}, got {echo_cmd}")
  if echo_seq != sequence:
    raise USBIOError(f"Unexpected sequence number. Expected {sequence}, got {echo_seq}")

  if payload_length == 0:
    return b""
  return _read_exact(transport, payload_length)
