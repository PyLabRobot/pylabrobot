"""Tests for AllMotion EZStepper command formatting and response parsing."""

import unittest

from pylabrobot.celigo import ezstepper
from pylabrobot.celigo.ezstepper import (
  EZCommand,
  EZStepperError,
  EZStepperQuery,
  motor_designation,
  multi_command,
  parse_response,
  single_command,
)


class TestMotorDesignation(unittest.TestCase):
  def test_single_digit_axes(self):
    self.assertEqual(motor_designation(1), "1")
    self.assertEqual(motor_designation(9), "9")

  def test_zero_and_high(self):
    self.assertEqual(motor_designation(0), "0")
    self.assertEqual(motor_designation(10), ":")  # chr(48+10)


class TestCommandStrings(unittest.TestCase):
  def test_move_absolute(self):
    self.assertEqual(ezstepper.move_absolute(1, 10000), "/1A10000R\r")

  def test_home(self):
    self.assertEqual(ezstepper.home(3), "/3Z0R\r")

  def test_relative_positive_and_negative(self):
    self.assertEqual(ezstepper.move_relative(2, 500), "/2P500R\r")
    self.assertEqual(ezstepper.move_relative(2, -500), "/2D500R\r")

  def test_set_velocity(self):
    self.assertEqual(ezstepper.set_velocity(1, 250000), "/1V250000R\r")

  def test_query_has_no_run(self):
    # query encoder position: "?8", no trailing R
    self.assertEqual(ezstepper.query_encoder_position(1), "/1?8\r")

  def test_terminate_has_no_run(self):
    self.assertEqual(ezstepper.terminate(1), "/1T\r")

  def test_multi_command(self):
    s = multi_command(
      [
        (EZCommand.SET_VELOCITY, 100000),
        (EZCommand.SET_ACCELERATION, 1000),
        (EZCommand.MOVE_ABSOLUTE, 5000),
      ],
      axis_index=1,
    )
    self.assertEqual(s, "/1V100000L1000A5000R\r")

  def test_encoder_query_value(self):
    self.assertEqual(int(EZStepperQuery.ENCODER_POSITION), 8)


class TestValidation(unittest.TestCase):
  def test_relative_zero_rejected(self):
    with self.assertRaises(ValueError):
      ezstepper.move_relative(1, 0)

  def test_velocity_out_of_range(self):
    with self.assertRaises(ValueError):
      single_command(EZCommand.SET_VELOCITY, 0, 1)
    with self.assertRaises(ValueError):
      single_command(EZCommand.SET_VELOCITY, 20_000_000, 1)

  def test_polarity_must_be_binary(self):
    with self.assertRaises(ValueError):
      single_command(EZCommand.SET_POLARITY, 2, 1)

  def test_query_cannot_combine(self):
    with self.assertRaises(ValueError):
      multi_command([(EZCommand.QUERY, 8), (EZCommand.MOVE_ABSOLUTE, 1)], 1)


class TestNegativeAbsoluteAndTokens(unittest.TestCase):
  def test_absolute_can_be_negative(self):
    # filter wheel uses negative absolute targets, e.g. "A-1980" (confirmed in capture)
    self.assertEqual(ezstepper.move_absolute(4, -1980), "/4A-1980R\r")

  def test_relative_still_rejects_negative(self):
    # relative moves carry magnitude only; negative is expressed via D
    self.assertEqual(ezstepper.move_relative(1, -500), "/1D500R\r")

  def test_new_tokens_present(self):
    self.assertEqual(EZCommand.SET_OVERLOAD_TIMEOUT.name, "SET_OVERLOAD_TIMEOUT")
    self.assertEqual(ezstepper.CODE[EZCommand.SET_OVERLOAD_TIMEOUT], "au")
    self.assertEqual(ezstepper.CODE[EZCommand.SET_COURSE_CORRECTION], "aC")
    self.assertEqual(ezstepper.CODE[EZCommand.SET_FINE_CORRECTION], "ac")
    self.assertEqual(ezstepper.CODE[EZCommand.SET_INTEGRATION_PERIOD], "x")


class TestOemFraming(unittest.TestCase):
  def test_to_oem_packet_layout(self):
    pkt = ezstepper.to_oem_packet("/1A1000R\r")
    self.assertEqual(pkt[0], 0x02)  # STX
    self.assertEqual(pkt[1:3], b"11")  # addr '1' + device '1'
    self.assertEqual(pkt[3:9], b"A1000R")  # tokens
    self.assertEqual(pkt[9], 0x03)  # ETX
    # last byte = XOR over everything before it
    expected = 0
    for b in pkt[:-1]:
      expected ^= b
    self.assertEqual(pkt[-1], expected)

  def test_oem_packet_y_axis(self):
    # multi-token move like the captured "21h50V3150L3150A3793R"
    cmd = ezstepper.multi_command(
      [
        (EZCommand.SET_HOLD_CURRENT, 50),
        (EZCommand.SET_VELOCITY, 3150),
        (EZCommand.SET_ACCELERATION, 3150),
        (EZCommand.MOVE_ABSOLUTE, 3793),
      ],
      axis_index=2,
    )
    self.assertEqual(cmd, "/2h50V3150L3150A3793R\r")
    pkt = ezstepper.to_oem_packet(cmd)
    self.assertEqual(pkt[:3], b"\x0221")  # STX + addr '2' + '1'

  def test_from_oem_response_unwraps(self):
    self.assertEqual(ezstepper.from_oem_response("\x020`123\x03q"), "/0`123")

  def test_from_oem_response_passthrough(self):
    # not OEM-framed -> returned unchanged
    self.assertEqual(ezstepper.from_oem_response("/0`5"), "/0`5")

  def test_oem_roundtrip_parse(self):
    pkt = ezstepper.to_oem_packet("/1?8R\r")
    self.assertEqual(pkt[0], 0x02)
    # a wrapped reply unwraps then parses as a normal status response
    unwrapped = ezstepper.from_oem_response("\x020`4491\x03z")
    resp = ezstepper.parse_response(unwrapped)
    self.assertTrue(resp.ready)
    self.assertEqual(resp.data, "4491")


class TestResponseParsing(unittest.TestCase):
  def test_ready_no_error(self):
    # "`" = 0x60 = 0x40 base + 0x20 ready, low nibble 0 = no error
    r = parse_response("/0`12345\x03")
    self.assertTrue(r.ready)
    self.assertEqual(r.error, EZStepperError.NO_ERROR)
    self.assertEqual(r.data, "12345")
    self.assertTrue(r.ok)

  def test_busy_no_error(self):
    # "@" = 0x40 = busy, no error
    r = parse_response("/0@\x03")
    self.assertFalse(r.ready)
    self.assertEqual(r.error, EZStepperError.NO_ERROR)

  def test_error_code_low_nibble(self):
    # 0x60 | 0x03 = 0x63 = "c" -> ready, BadOperand(3)
    r = parse_response("/0c\x03")
    self.assertTrue(r.ready)
    self.assertEqual(r.error, EZStepperError.BAD_OPERAND)
    self.assertFalse(r.ok)

  def test_data_trimmed_at_terminator(self):
    r = parse_response("/0`777\r")
    self.assertEqual(r.data, "777")


if __name__ == "__main__":
  unittest.main()
