"""Tests for the Celigo high-level command layer."""

import struct
import unittest

from pylabrobot.celigo.controller import (
  CeligoController,
  ControllerStatus,
  GalvoType,
  analog_dac_to_volts,
  dac_units_to_volts,
  volts_to_analog_dac,
  volts_to_dac_units,
)
from pylabrobot.celigo.packets import IO_CTLR_CMDS
from pylabrobot.celigo.tests.packets_tests import FakeTransport, make_response


class ScriptedTransport(FakeTransport):
  """FakeTransport that records the opcode/payload of each request it sees."""

  def __init__(self):
    super().__init__()
    self.requests = []  # list of (opcode, payload_bytes)

  def write(self, data: bytes) -> int:
    # parse the TX header: cmd@0, length@5..9; payload after the 11-byte header
    opcode = data[0]
    self.requests.append((opcode, data[11:]))
    return super().write(data)


class TestDacConversions(unittest.TestCase):
  def test_galvo_zero_and_extremes(self):
    self.assertEqual(volts_to_dac_units(0.0), 32768)  # round(32767.5)
    self.assertEqual(volts_to_dac_units(10.0), 65535)
    self.assertEqual(volts_to_dac_units(-10.0), 0)

  def test_galvo_clamped(self):
    self.assertEqual(volts_to_dac_units(999.0), 65535)
    self.assertEqual(volts_to_dac_units(-999.0), 0)

  def test_galvo_roundtrip(self):
    for v in (-7.5, -1.0, 0.0, 2.3, 9.9):
      dac = volts_to_dac_units(v)
      self.assertAlmostEqual(dac_units_to_volts(dac), v, places=3)

  def test_analog_channel_scale(self):
    # 0..5V mapped onto 12-bit full scale
    self.assertEqual(volts_to_analog_dac(0.0, 0.0, 5.0), 0)
    self.assertEqual(volts_to_analog_dac(5.0, 0.0, 5.0), 4095)
    self.assertAlmostEqual(analog_dac_to_volts(2048, 0.0, 5.0), 2.5006, places=3)


class TestControllerCommands(unittest.TestCase):
  def setUp(self):
    self.t = ScriptedTransport()
    self.ctrl = CeligoController(self.t)

  def test_get_status(self):
    payload = struct.pack(">II", int(ControllerStatus.CTLR_BUSY), 5013)
    self.t.queue_response(make_response(IO_CTLR_CMDS.CONTROLLER_STATUS, 2, payload))
    status, ext = self.ctrl.get_status()
    self.assertEqual(status, ControllerStatus.CTLR_BUSY)
    self.assertEqual(ext, 5013)

  def test_move_galvo_payload(self):
    self.t.queue_response(make_response(IO_CTLR_CMDS.MOVE_GALVO, 2))
    self.ctrl.move_galvo(GalvoType.Y, 0.0)
    opcode, payload = self.t.requests[-1]
    self.assertEqual(opcode, int(IO_CTLR_CMDS.MOVE_GALVO))
    galvo, dac, wait, timeout = struct.unpack(">HiHH", payload)
    self.assertEqual(galvo, int(GalvoType.Y))
    self.assertEqual(dac, 32768)  # 0 V
    self.assertEqual((wait, timeout), (0, 0))

  def test_set_analog_out_payload(self):
    self.t.queue_response(make_response(IO_CTLR_CMDS.WRITE_DA_CHANNEL, 2))
    self.ctrl.set_analog_out(channel=1, voltage=5.0, min_voltage=0.0, max_voltage=5.0)
    opcode, payload = self.t.requests[-1]
    self.assertEqual(opcode, int(IO_CTLR_CMDS.WRITE_DA_CHANNEL))
    channel, dac = struct.unpack(">HH", payload)
    self.assertEqual(channel, 1)
    self.assertEqual(dac, 4095)

  def test_get_analog_input(self):
    self.t.queue_response(make_response(IO_CTLR_CMDS.READ_AD_CHANNEL, 2, struct.pack(">H", 2048)))
    v = self.ctrl.get_analog_input(2, 0.0, 5.0)
    self.assertAlmostEqual(v, 2.5006, places=3)

  def test_set_digital_out_bit_uses_set_vs_clear(self):
    self.t.queue_response(make_response(IO_CTLR_CMDS.SET_DIG_PORT_BITS, 2))
    self.ctrl.set_digital_out_bit(3, True)
    opcode, payload = self.t.requests[-1]
    self.assertEqual(opcode, int(IO_CTLR_CMDS.SET_DIG_PORT_BITS))
    self.assertEqual(struct.unpack(">H", payload)[0], 1 << 3)

    self.t.queue_response(make_response(IO_CTLR_CMDS.CLEAR_DIG_PORT_BITS, 3))
    self.ctrl.set_digital_out_bit(3, False)
    opcode, _ = self.t.requests[-1]
    self.assertEqual(opcode, int(IO_CTLR_CMDS.CLEAR_DIG_PORT_BITS))

  def test_read_digital_input_bit(self):
    self.t.queue_response(make_response(IO_CTLR_CMDS.READ_DIG_PORT, 2, struct.pack(">H", 0b1010)))
    self.assertTrue(self.ctrl.read_digital_input(1))
    self.t.queue_response(make_response(IO_CTLR_CMDS.READ_DIG_PORT, 3, struct.pack(">H", 0b1010)))
    self.assertFalse(self.ctrl.read_digital_input(0))

  def test_arm_autofocus_payload(self):
    self.t.queue_response(make_response(IO_CTLR_CMDS.AUTO_FOCUS, 2))
    self.ctrl.arm_autofocus(1000, 800, 16)
    opcode, payload = self.t.requests[-1]
    cur, start, count = struct.unpack(">iiH", payload)
    self.assertEqual((cur, start, count), (1000, 800, 16))

  def test_get_autofocus_positions(self):
    body = struct.pack(">h", 3) + struct.pack(">hhh", 10, 20, 30)
    self.t.queue_response(make_response(IO_CTLR_CMDS.SEND_FOCUS_POINTS, 2, body))
    self.assertEqual(self.ctrl.get_autofocus_positions(), [10, 20, 30])

  def test_send_motor_query_oem_default(self):
    # default path = WLEN/OEM (opcode 47); reply is OEM-wrapped and gets unwrapped.
    inner = "0`12345"  # addr '0', status '`', data
    reply = ("\x02" + inner + "\x03").encode("latin-1")
    body = struct.pack(">H", 0) + struct.pack(">H", len(reply)) + reply
    self.t.queue_response(make_response(IO_CTLR_CMDS.MOTOR_CMD_QUERY_WLEN, 2, body))
    out = self.ctrl.send_motor_query("/1A1000R\r")
    self.assertEqual(out, "/0`12345")  # unwrapped
    opcode, payload = self.t.requests[-1]
    self.assertEqual(opcode, int(IO_CTLR_CMDS.MOTOR_CMD_QUERY_WLEN))
    # OEM frame: STX + addr('1') + '1' + tokens('A1000R') + ETX + xor
    self.assertEqual(payload[0], 0x02)
    self.assertEqual(payload[1:3], b"11")
    self.assertEqual(payload[3:9], b"A1000R")
    self.assertEqual(payload[9], 0x03)

  def test_send_motor_query_dt_legacy(self):
    reply = b"`0"
    body = struct.pack(">H", 0) + struct.pack(">H", len(reply)) + reply
    self.t.queue_response(make_response(IO_CTLR_CMDS.MOTOR_CMD_QUERY, 2, body))
    out = self.ctrl.send_motor_query("/1A1000R", oem_protocol=False)
    self.assertEqual(out, reply.decode("ascii"))
    opcode, payload = self.t.requests[-1]
    self.assertEqual(opcode, int(IO_CTLR_CMDS.MOTOR_CMD_QUERY))
    self.assertEqual(payload, b"/1A1000R\x00")  # ASCII + NUL terminator

  def test_get_motor_configuration(self):
    # 8 UARTs x (1 status byte + 4 slots); put a motor (idx 5) on UART 0 slot 0.
    buf = bytearray()
    for uart in range(8):
      buf.append(0)  # status byte
      for slot in range(4):
        buf.append(5 if (uart == 0 and slot == 0) else 127)
    self.t.queue_response(make_response(IO_CTLR_CMDS.SEND_MOTOR_CONFIG, 2, bytes(buf)))
    motors = self.ctrl.get_motor_configuration()
    self.assertEqual(len(motors), 1)
    self.assertEqual((motors[0].uart_index, motors[0].motor_index), (0, 5))


if __name__ == "__main__":
  unittest.main()
