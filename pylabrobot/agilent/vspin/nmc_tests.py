import unittest

from pylabrobot.agilent.vspin import _nmc
from pylabrobot.io.binary import Writer


def _response(status: int, data: bytes) -> bytes:
  return bytes([status]) + data + bytes([(status + sum(data)) & 0xFF])


class NMCCommandTests(unittest.TestCase):
  def test_known_setup_commands(self):
    self.assertEqual(_nmc.build_set_address(1).hex(), "aa002101ff21")
    self.assertEqual(
      _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID).hex(),
      "aa01132034",
    )
    self.assertEqual(
      _nmc.build_define_status(
        _nmc.PIC_SERVO_ADDRESS,
        _nmc.SEND_POSITION
        | _nmc.SEND_ANALOG
        | _nmc.SEND_VELOCITY
        | _nmc.SEND_AUXILIARY
        | _nmc.SEND_HOME,
      ).hex(),
      "aa01121f32",
    )
    self.assertEqual(_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS).hex(), "aa010e0f")
    self.assertEqual(_nmc.build_no_op(_nmc.PIC_IO_ADDRESS).hex(), "aa020e10")

  def test_known_io_output_command(self):
    self.assertEqual(
      _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0x0600).hex(),
      "aa022600062e",
    )

  def test_known_baud_and_reset_commands(self):
    self.assertEqual(_nmc.build_set_baud(57600).hex(), "aaff1a142d")
    self.assertEqual(_nmc.build_hard_reset().hex(), "aaff0f0e")

  def test_known_gain_and_servo_state_commands(self):
    position_gains = _nmc.ServoGains(
      proportional=200,
      derivative=1200,
      integral=150,
      integration_limit=15,
      output_limit=75,
      current_limit=0,
      position_error_limit=4000,
      servo_rate=5,
      deadband=0,
    )
    self.assertEqual(
      _nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, position_gains).hex(),
      "aa01e6c800b00496000f004b00a00f050007",
    )
    self.assertEqual(
      _nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF).hex(),
      "aa0117021a",
    )
    self.assertEqual(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS).hex(), "aa010b0c")
    self.assertEqual(_nmc.build_reset_position(_nmc.PIC_SERVO_ADDRESS).hex(), "aa010001")
    self.assertEqual(_nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28).hex(), "aa01192842")

  def test_known_position_trajectory(self):
    mode = (
      _nmc.LOAD_POSITION
      | _nmc.LOAD_VELOCITY
      | _nmc.LOAD_ACCELERATION
      | _nmc.ENABLE_SERVO
      | _nmc.START_NOW
    )
    command = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      mode,
      position=0,
      velocity=0x28F5C3,
      acceleration=0x1AD7,
    )
    self.assertEqual(command.hex(), "aa01d49700000000c3f52800d71a00003d")

  def test_known_deceleration_trajectory(self):
    mode = (
      _nmc.LOAD_VELOCITY
      | _nmc.LOAD_ACCELERATION
      | _nmc.ENABLE_SERVO
      | _nmc.VELOCITY_MODE
      | _nmc.START_NOW
    )
    command = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      mode,
      velocity=0,
      acceleration=732,
    )
    self.assertEqual(command.hex(), "aa0194b600000000dc02000029")

  def test_trajectory_requires_fields_selected_by_mode(self):
    with self.assertRaisesRegex(ValueError, "velocity is required"):
      _nmc.build_load_trajectory(
        _nmc.PIC_SERVO_ADDRESS,
        _nmc.LOAD_VELOCITY,
      )
    with self.assertRaisesRegex(ValueError, "LOAD_POSITION is not set"):
      _nmc.build_load_trajectory(
        _nmc.PIC_SERVO_ADDRESS,
        0,
        position=1,
      )
    with self.assertRaisesRegex(ValueError, "signed 32-bit"):
      _nmc.build_load_trajectory(
        _nmc.PIC_SERVO_ADDRESS,
        _nmc.LOAD_POSITION,
        position=2**31,
      )

  def test_command_validation(self):
    with self.assertRaisesRegex(ValueError, "address"):
      _nmc.build_command(33, _nmc.CMD_NO_OP)
    with self.assertRaisesRegex(ValueError, "four bits"):
      _nmc.build_command(1, 16)
    with self.assertRaisesRegex(ValueError, "at most 15"):
      _nmc.build_command(1, _nmc.CMD_SET_GAIN, b"x" * 16)


class NMCResponseTests(unittest.TestCase):
  def test_status_data_lengths(self):
    servo_mask = (
      _nmc.SEND_POSITION
      | _nmc.SEND_ANALOG
      | _nmc.SEND_VELOCITY
      | _nmc.SEND_AUXILIARY
      | _nmc.SEND_HOME
    )
    self.assertEqual(_nmc.servo_status_data_length(servo_mask), 12)
    self.assertEqual(
      _nmc.io_status_data_length(_nmc.SEND_INPUTS | _nmc.SEND_ANALOG_1),
      3,
    )

  def test_parse_captured_servo_status(self):
    mask = (
      _nmc.SEND_POSITION
      | _nmc.SEND_ANALOG
      | _nmc.SEND_VELOCITY
      | _nmc.SEND_AUXILIARY
      | _nmc.SEND_HOME
    )
    status = _nmc.parse_servo_status(
      bytes.fromhex("11222500004f000018e0050000a4"),
      mask,
    )
    self.assertEqual(status.status, 0x11)
    self.assertEqual(status.position, 0x2522)
    self.assertEqual(status.analog, 0x4F)
    self.assertEqual(status.velocity, 0)
    self.assertEqual(status.auxiliary, 0x18)
    self.assertEqual(status.home_position, 0x05E0)

  def test_parse_signed_servo_fields_and_module_id(self):
    mask = (
      _nmc.SEND_POSITION
      | _nmc.SEND_VELOCITY
      | _nmc.SEND_HOME
      | _nmc.SEND_MODULE_ID
      | _nmc.SEND_POSITION_ERROR
    )
    data = (
      Writer()
      .i32(-123456)
      .i16(-321)
      .i32(-4000)
      .u8(_nmc.PIC_SERVO_MODULE_TYPE)
      .u8(12)
      .i16(-7)
      .finish()
    )
    status = _nmc.parse_servo_status(_response(0x09, data), mask)
    self.assertEqual(status.position, -123456)
    self.assertEqual(status.velocity, -321)
    self.assertEqual(status.home_position, -4000)
    self.assertEqual(status.module_type, _nmc.PIC_SERVO_MODULE_TYPE)
    self.assertEqual(status.module_version, 12)
    self.assertEqual(status.position_error, -7)

  def test_parse_io_status(self):
    mask = _nmc.SEND_INPUTS | _nmc.SEND_ANALOG_1
    status = _nmc.parse_io_status(_response(0x09, bytes.fromhex("341256")), mask)
    self.assertEqual(status.inputs, 0x1234)
    self.assertEqual(status.analog_1, 0x56)

  def test_response_rejects_wrong_length(self):
    with self.assertRaisesRegex(_nmc.NMCProtocolError, "expected 3"):
      _nmc.parse_response(b"\x01\x01", expected_data_length=1)

  def test_response_rejects_bad_checksum(self):
    with self.assertRaisesRegex(_nmc.NMCProtocolError, "checksum mismatch"):
      _nmc.parse_response(b"\x01\x02\x00", expected_data_length=1)

  def test_response_rejects_module_checksum_error(self):
    with self.assertRaisesRegex(_nmc.NMCProtocolError, "rejected"):
      _nmc.parse_response(b"\x02\x02", expected_data_length=0)


class VSpinTrajectoryMathTests(unittest.TestCase):
  def test_rcf_rpm_round_trip(self):
    rpm = _nmc.rcf_to_rpm(500)
    self.assertAlmostEqual(rpm, 2114.774672189068, places=9)
    self.assertAlmostEqual(_nmc.rpm_to_rcf(rpm), 500, places=9)

  def test_reference_500g_80_percent_case(self):
    rpm = _nmc.rcf_to_rpm(500)
    self.assertEqual(_nmc.rpm_to_nmc_velocity(rpm), 9_461_343)
    self.assertEqual(_nmc.acceleration_to_nmc(0.8), 732)
    self.assertAlmostEqual(_nmc.acceleration_rpm_per_second(0.8), 320.0)
    self.assertAlmostEqual(
      _nmc.acceleration_counts_per_second_squared(0.8),
      42_666.666666666664,
    )
    self.assertAlmostEqual(_nmc.predicted_ramp_time(rpm, 0.8), 6.6086708495779)
    self.assertEqual(_nmc.acceleration_distance(rpm, 0.8), 931_723)
    self.assertEqual(_nmc.spin_target_distance(rpm, 60, 0.8), 20_191_493)

  def test_servo_rate_scales_velocity_and_acceleration(self):
    self.assertEqual(
      _nmc.rpm_to_nmc_velocity(100, servo_rate=5),
      int(_nmc.NMC_VELOCITY_PER_RPM * 100 * 5),
    )
    self.assertEqual(
      _nmc.acceleration_to_nmc(1, servo_rate=5),
      int(_nmc.NMC_ACCELERATION_AT_FULL_SCALE * 25),
    )

  def test_nearest_encoder_position_uses_shortest_path(self):
    self.assertEqual(_nmc.nearest_encoder_position(7900, 100), 8100)
    self.assertEqual(_nmc.nearest_encoder_position(100, 7900), -100)
    self.assertEqual(_nmc.nearest_encoder_position(-100, 100), 100)

  def test_math_validation(self):
    for acceleration in (0, -0.1, 1.1):
      with self.assertRaisesRegex(ValueError, "acceleration"):
        _nmc.acceleration_to_nmc(acceleration)
    with self.assertRaisesRegex(ValueError, "rotor_radius"):
      _nmc.rcf_to_rpm(500, rotor_radius=0)
    with self.assertRaisesRegex(ValueError, "duration"):
      _nmc.spin_target_distance(1000, -1, 0.8)


if __name__ == "__main__":
  unittest.main()
