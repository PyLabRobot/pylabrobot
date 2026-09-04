import unittest

from pylabrobot.agilent.vspin import _access2_protocol as protocol
from pylabrobot.io.binary import Writer


class Access2CommandTests(unittest.TestCase):
  def test_status_ftdi_capture(self):
    command = protocol.build_get_status()

    self.assertEqual(command.hex(), "200000")
    self.assertEqual(protocol.build_ftdi_frame(command).hex(), "11050003002000006bd4")

  def test_setup_captures(self):
    commands = (
      (protocol.build_ping(), "110500030014000072b1"),
      (protocol.build_initialize(), "1105000300100000ae71"),
      (protocol.build_read_flash(0, 128), "110500070024040000008000be89"),
      (protocol.build_read_flash(128, 128), "11050007002404008000800063b1"),
      (protocol.build_read_flash(256, 128), "11050007002404000001800089b9"),
      (protocol.build_read_flash(384, 128), "1105000700240400800180005481"),
      (protocol.build_read_flash(512, 64), "110500070024040000024000c6bd"),
      (protocol.build_home(), "1105000300400000f0bf"),
    )
    for command, expected in commands:
      with self.subTest(command=command.hex()):
        self.assertEqual(protocol.build_ftdi_frame(command).hex(), expected)

  def test_motion_captures(self):
    self.assertEqual(
      protocol.build_ftdi_frame(
        protocol.build_move_axis_to_position(
          protocol.AXIS_GRIPPER,
          0,
          protocol.PROFILE_DYNAMIC_EMPTY,
          protocol.SPEED_FAST,
        )
      ).hex(),
      "1105000a004607000100000000020235bf",
    )
    self.assertEqual(
      protocol.build_ftdi_frame(
        protocol.build_move_to_teachpoint(
          protocol.TEACHPOINT_PARK,
          0,
          15,
          protocol.PROFILE_DYNAMIC_EMPTY,
          protocol.SPEED_FAST,
        )
      ).hex(),
      "1105000e00440b00000000000000007041020203c7",
    )

  def test_load_motion_captures(self):
    commands = (
      (
        protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10),
        "1105000e00440b000100004040000020410200a5cb",
      ),
      (
        protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 5.68),
        "1105000a00460700018fc2b540020023dc",
      ),
      (
        protocol.build_move_to_teachpoint(
          protocol.TEACHPOINT_BUCKET_1,
          3,
          10,
          protocol.PROFILE_DYNAMIC_FULL,
        ),
        "1105000e00440b000200004040000020410300ee00",
      ),
      (
        protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 0),
        "1105000a004607000100000000020015fd",
      ),
      (
        protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PARK, 3, 10),
        "1105000e00440b0000000040400000204102007d82",
      ),
    )
    for command, expected in commands:
      with self.subTest(command=command.hex()):
        self.assertEqual(protocol.build_ftdi_frame(command).hex(), expected)

  def test_unload_motion_captures(self):
    commands = (
      (
        protocol.build_move_to_teachpoint(protocol.TEACHPOINT_BUCKET_1, 3, 10),
        "1105000e00440b000200004040000020410200dd31",
      ),
      (
        protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 5.69),
        "1105000a00460700017b14b6400200d57a",
      ),
      (
        protocol.build_move_to_teachpoint(
          protocol.TEACHPOINT_PICK,
          3,
          10,
          protocol.PROFILE_DYNAMIC_FULL,
        ),
        "1105000e00440b00010000404000002041030096fa",
      ),
      (
        protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PARK, 0, 10),
        "1105000e00440b00000000000000002041020056be",
      ),
    )
    for command, expected in commands:
      with self.subTest(command=command.hex()):
        self.assertEqual(protocol.build_ftdi_frame(command).hex(), expected)

  def test_park_and_gripper_jog_captures(self):
    commands = (
      (
        protocol.build_move_to_teachpoint(
          protocol.TEACHPOINT_PARK,
          8,
          15,
          protocol.PROFILE_DYNAMIC_FULL,
        ),
        "1105000e00440b0000000000410000704103007539",
      ),
      (
        protocol.build_jog_axis(protocol.AXIS_GRIPPER, 1),
        "1105000a00420700010000803f02008c64",
      ),
      (
        protocol.build_jog_axis(protocol.AXIS_GRIPPER, -1),
        "1105000a0042070001000080bf0200b73e",
      ),
    )
    for command, expected in commands:
      with self.subTest(command=command.hex()):
        self.assertEqual(protocol.build_ftdi_frame(command).hex(), expected)


class Access2ResponseTests(unittest.TestCase):
  def test_parse_captured_no_plate_sensor_response(self):
    frame = bytes.fromhex("1105000800510500000300000079f1")

    response = protocol.parse_ftdi_reply(frame, protocol.GET_SENSOR_VALUES)

    self.assertEqual(response.response_id, 0x51)
    self.assertEqual(response.result, 0)
    self.assertEqual(protocol.decode_sensor_values(response.data), protocol.SENSOR_NO_PLATE)

  def test_rejects_bad_crc(self):
    frame = bytearray.fromhex("1105000800510500000300000079f1")
    frame[-1] ^= 0xFF

    with self.assertRaisesRegex(protocol.Access2ProtocolError, "CRC mismatch"):
      protocol.parse_ftdi_frame(bytes(frame))

  def test_rejects_truncated_frame(self):
    frame = bytes.fromhex("1105000800510500000300000079f1")

    with self.assertRaisesRegex(protocol.Access2ProtocolError, "bytes, expected"):
      protocol.parse_ftdi_frame(frame[:-1])

  def test_rejects_oversized_ftdi_header(self):
    header = (
      Writer(little_endian=False)
      .u8(protocol.VELOCITY11_HEADER)
      .u8(protocol.VELOCITY11_PACKET_TYPE)
      .u16(protocol.MAX_INNER_FRAME_LENGTH + 1)
      .u8(protocol.VELOCITY11_CHANNEL)
      .finish()
    )

    with self.assertRaisesRegex(protocol.Access2ProtocolError, "exceeds"):
      protocol.parse_ftdi_header(header)

  def test_rejects_wrong_response_id(self):
    inner = Writer().u8(0x52).u16(1).u8(0).finish()

    with self.assertRaisesRegex(protocol.Access2ProtocolError, "response ID"):
      protocol.parse_reply(inner, protocol.GET_SENSOR_VALUES)

  def test_preserves_nonzero_command_result(self):
    inner = Writer().u8(0x51).u16(1).u8(7).finish()

    response = protocol.parse_reply(inner, protocol.GET_SENSOR_VALUES)

    self.assertEqual(response.result, 7)
    self.assertEqual(response.data, b"")

  def test_decode_full_status(self):
    data = (
      Writer()
      .u8(protocol.STATUS_INITIALIZED | protocol.STATUS_HOMED)
      .u8(0x12)
      .u8(1)
      .f32(5.68)
      .u8(2)
      .f32(100.5)
      .u8(3)
      .f32(20.25)
      .finish()
    )

    status = protocol.decode_status(data)

    self.assertTrue(status.initialized)
    self.assertTrue(status.homed)
    self.assertFalse(status.estop_active)
    gripper_position = status.gripper_position
    self.assertIsNotNone(gripper_position)
    assert gripper_position is not None
    self.assertAlmostEqual(gripper_position, 5.68, places=5)
    self.assertEqual(status.y_position, 100.5)
    self.assertEqual(status.z_position, 20.25)
    self.assertEqual(status.axis_status(protocol.AXIS_GRIPPER), 1)
    self.assertEqual(status.axis_status(protocol.AXIS_Y), 2)
    self.assertEqual(status.axis_status(protocol.AXIS_Z), 3)
    gripper_axis_position = status.axis_position(protocol.AXIS_GRIPPER)
    self.assertIsNotNone(gripper_axis_position)
    assert gripper_axis_position is not None
    self.assertAlmostEqual(gripper_axis_position, 5.68, places=5)
    self.assertEqual(status.axis_position(protocol.AXIS_Y), 100.5)
    self.assertEqual(status.axis_position(protocol.AXIS_Z), 20.25)

  def test_rejects_partial_full_status(self):
    with self.assertRaisesRegex(protocol.Access2ProtocolError, "either 4 or at least 17"):
      protocol.decode_status(bytes(16))

  def test_decode_versions(self):
    self.assertEqual(protocol.decode_firmware_version(b"1.2.3\x00\x00"), "1.2.3")
    self.assertEqual(protocol.decode_hardware_version(Writer().i16(-2).finish()), -2)

  def test_hardware_version_requires_signed_word(self):
    with self.assertRaisesRegex(protocol.Access2ProtocolError, "expected at least 2"):
      protocol.decode_hardware_version(b"\x01")
