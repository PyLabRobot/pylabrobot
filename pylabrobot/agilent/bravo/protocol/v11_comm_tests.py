import struct
import unittest

from pylabrobot.agilent.bravo.errors import BravoError, ErrorType, RabbitErrorCode
from pylabrobot.agilent.bravo.protocol.commands import (
  DEFAULT_COMMAND_TIMEOUT,
  MAX_COMMAND_RETRIES,
  CommandID,
)
from pylabrobot.agilent.bravo.protocol.v11_comm import V11DeviceComm
from pylabrobot.agilent.bravo.transport import Transport


class BufferedTransport(Transport):
  """A Transport backed by a fixed byte buffer, for driving V11DeviceComm.

  Records every ``receive_exact`` call's ``(num_bytes, timeout)`` so a test
  can assert exactly what the comm layer asked for -- in particular, that the
  timeout it forwards is in seconds.
  """

  def __init__(self, buffer: bytes = b"", connected: bool = True):
    self._buffer = bytearray(buffer)
    self.sent: list = []
    self.receive_exact_calls: list = []
    self._connected = connected

  def send(self, data: bytes) -> None:
    self.sent.append(data)

  def receive(self, timeout: float = 2.0) -> bytes:
    return b""

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    self.receive_exact_calls.append((num_bytes, timeout))
    if len(self._buffer) < num_bytes:
      raise TimeoutError(
        f"BufferedTransport: only {len(self._buffer)} of {num_bytes} bytes available"
      )
    chunk = bytes(self._buffer[:num_bytes])
    del self._buffer[:num_bytes]
    return chunk

  @property
  def is_connected(self) -> bool:
    return self._connected


def _v11_response_frame(error_code: int, data: bytes = b"") -> bytes:
  payload = bytes([error_code]) + data
  return struct.pack("<H", len(payload)) + payload


class V11DeviceCommSuccessTests(unittest.TestCase):
  def test_send_command_returns_response_data(self):
    frame = _v11_response_frame(RabbitErrorCode.NONE, b"\x01\x02\x03")
    transport = BufferedTransport(frame)
    comm = V11DeviceComm(transport)

    response = comm.send_command(CommandID.PING_DEVICE, timeout=1.0)

    self.assertEqual(response, b"\x01\x02\x03")
    self.assertEqual(len(transport.sent), 1)

  def test_sent_frame_has_length_prefixed_command_and_data(self):
    frame = _v11_response_frame(RabbitErrorCode.NONE)
    transport = BufferedTransport(frame)
    comm = V11DeviceComm(transport)

    comm.send_command(CommandID.PREPARE_MOVE, data=b"\xaa\xbb", timeout=1.0)

    sent = transport.sent[0]
    length, command_id = struct.unpack_from("<HB", sent, 0)
    self.assertEqual(length, 3)  # 1 command byte + 2 data bytes
    self.assertEqual(command_id, CommandID.PREPARE_MOVE)
    self.assertEqual(sent[3:], b"\xaa\xbb")


class V11DeviceCommErrorTests(unittest.TestCase):
  def test_nonzero_error_code_raises_mapped_bravo_error(self):
    frame = _v11_response_frame(RabbitErrorCode.ROBOT_DISABLE)
    transport = BufferedTransport(frame)
    comm = V11DeviceComm(transport)

    with self.assertRaises(BravoError) as ctx:
      comm.send_command(CommandID.STOP, timeout=1.0)
    self.assertEqual(ctx.exception.error_type, ErrorType.ROBOT_DISABLE)

  def test_zero_length_response_raises_no_response(self):
    frame = struct.pack("<H", 0)
    transport = BufferedTransport(frame)
    comm = V11DeviceComm(transport)

    with self.assertRaises(BravoError) as ctx:
      comm.send_command(CommandID.PING_DEVICE, timeout=1.0)
    self.assertEqual(ctx.exception.error_type, ErrorType.NO_RESPONSE)

  def test_disconnected_transport_retries_then_raises(self):
    transport = BufferedTransport(b"", connected=False)
    comm = V11DeviceComm(transport)

    with self.assertRaises(BravoError) as ctx:
      comm.send_command(CommandID.PING_DEVICE, timeout=0.05)
    self.assertEqual(ctx.exception.error_type, ErrorType.NO_RESPONSE)
    self.assertIn(f"{MAX_COMMAND_RETRIES}", str(ctx.exception))


class V11DeviceCommTimeoutUnitTests(unittest.TestCase):
  def test_timeout_is_forwarded_to_transport_unchanged_in_seconds(self):
    # The timeout the caller passed in must reach every receive_exact call
    # unchanged -- both the length-prefix read and the payload read. Priming
    # only the 2-byte length prefix (claiming a payload that never arrives)
    # forces the payload read to actually run and time out, rather than
    # letting the header read alone fail and leaving the payload path
    # unexercised.
    transport = BufferedTransport(struct.pack("<H", 4))
    comm = V11DeviceComm(transport)

    with self.assertRaises(BravoError):
      comm.send_command(CommandID.PING_DEVICE, timeout=0.2)

    self.assertIn((2, 0.2), transport.receive_exact_calls)
    self.assertIn((4, 0.2), transport.receive_exact_calls)
    for _num_bytes, timeout in transport.receive_exact_calls:
      self.assertEqual(timeout, 0.2)

  def test_default_timeout_is_the_module_constant_in_seconds(self):
    transport = BufferedTransport(struct.pack("<H", 4))
    comm = V11DeviceComm(transport)

    with self.assertRaises(BravoError):
      comm.send_command(CommandID.PING_DEVICE)

    self.assertIn((2, DEFAULT_COMMAND_TIMEOUT), transport.receive_exact_calls)
    self.assertIn((4, DEFAULT_COMMAND_TIMEOUT), transport.receive_exact_calls)
    for _num_bytes, timeout in transport.receive_exact_calls:
      self.assertEqual(timeout, DEFAULT_COMMAND_TIMEOUT)
      self.assertEqual(timeout, 2.0)


if __name__ == "__main__":
  unittest.main()
