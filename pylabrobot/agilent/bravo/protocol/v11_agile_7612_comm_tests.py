import struct
import unittest

from pylabrobot.agilent.bravo.errors import BravoError, ErrorType, RabbitErrorCode
from pylabrobot.agilent.bravo.protocol.commands import CommandID
from pylabrobot.agilent.bravo.protocol.v11_agile_7612_comm import V11Agile7612DeviceComm
from pylabrobot.agilent.bravo.protocol.v11_comm_tests import BufferedTransport


def _agile_7612_response_frame(command_id: int, error_code: int, data: bytes = b"") -> bytes:
  payload = bytes([error_code]) + data
  return struct.pack("<B", command_id) + struct.pack("<H", len(payload)) + payload


class V11Agile7612CommFrameOrderTests(unittest.TestCase):
  def test_sent_frame_puts_command_before_length(self):
    frame = _agile_7612_response_frame(CommandID.PING_DEVICE, RabbitErrorCode.NONE)
    transport = BufferedTransport(frame)
    comm = V11Agile7612DeviceComm(transport)

    comm.send_command(CommandID.PING_DEVICE, data=b"\x01\x02", timeout=1.0)

    sent = transport.sent[0]
    command_id, length = struct.unpack_from("<BH", sent, 0)
    self.assertEqual(command_id, CommandID.PING_DEVICE)
    self.assertEqual(length, 2)
    self.assertEqual(sent[3:], b"\x01\x02")

  def test_response_returns_data_with_error_byte_stripped(self):
    frame = _agile_7612_response_frame(CommandID.GET_POSITION, RabbitErrorCode.NONE, b"\xde\xad")
    transport = BufferedTransport(frame)
    comm = V11Agile7612DeviceComm(transport)

    response = comm.send_command(CommandID.GET_POSITION, timeout=1.0)
    self.assertEqual(response, b"\xde\xad")

  def test_error_code_raises_and_logs(self):
    frame = _agile_7612_response_frame(CommandID.STOP, RabbitErrorCode.MOTOR_POWER_FAULT)
    transport = BufferedTransport(frame)
    comm = V11Agile7612DeviceComm(transport)

    with self.assertRaises(BravoError) as ctx:
      comm.send_command(CommandID.STOP, timeout=1.0)
    self.assertEqual(ctx.exception.error_type, ErrorType.MOTOR_POWER)
    self.assertEqual(len(comm.error_log), 1)
    self.assertEqual(comm.error_log[0]["cmd"], "STOP")

  def test_command_counts_tracked_by_name(self):
    frame = _agile_7612_response_frame(CommandID.PING_DEVICE, RabbitErrorCode.NONE)
    transport = BufferedTransport(frame)
    comm = V11Agile7612DeviceComm(transport)

    comm.send_command(CommandID.PING_DEVICE, timeout=1.0)
    self.assertEqual(comm.command_counts["PING_DEVICE"], 1)


class V11Agile7612CommTimeoutUnitTests(unittest.TestCase):
  def test_timeout_is_forwarded_to_transport_unchanged_in_seconds(self):
    # Priming only the 3-byte [cmd][length] header (claiming a payload that
    # never arrives) forces the payload read to actually run and time out,
    # rather than letting the header read alone fail and leaving the
    # payload path unexercised.
    transport = BufferedTransport(struct.pack("<BH", CommandID.PING_DEVICE, 4))
    comm = V11Agile7612DeviceComm(transport)

    with self.assertRaises(BravoError):
      comm.send_command(CommandID.PING_DEVICE, timeout=0.15)

    self.assertIn((3, 0.15), transport.receive_exact_calls)
    self.assertIn((4, 0.15), transport.receive_exact_calls)
    for _num_bytes, timeout in transport.receive_exact_calls:
      self.assertEqual(timeout, 0.15)


if __name__ == "__main__":
  unittest.main()
