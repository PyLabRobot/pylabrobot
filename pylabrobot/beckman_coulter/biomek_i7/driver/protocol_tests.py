"""ACE messages, from the protocol as `protocol.py` states it.

Every command has one exact frame: what the Master Controller sent for it, as Biomek's `ace_io` log
(Log.db3) records it with its decoding, rebuilt here from that decoding. The reads the Master
Controller never sends are built from their layout. The other tests check that a message's fields
come back out of its bytes as they went in.
"""

import random
import struct
import unittest
from typing import Callable, Dict, List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import (
  AceError,
  InterlockTrippedError,
  MotionError,
  NotConfiguredError,
  NotEnabledError,
  NotHomedError,
)

# A motor's and a syringe pump's motor table, as the Master Controller's log shows it loading them
# ("Configure Y1 Minimum Velocity:23 ...", "Configure D1 Minimum Start Velocity:200 ...").
MOTOR_TABLE = bytes.fromhex(
  "FF170000004370010074000000F0F20200BB01000080E29E00000000004F3107000200034925000078000000"
  "640000005E01000066660000EE02000001C4098A020000D0070000"
)
PUMP_TABLE = bytes.fromhex(
  "C8000000A00F000014000000A05A0000C8000000302A00001027000050C3000000000000C05D00005800"
)

# (device, opcode) -> (the request, its sequence, the frame's bytes, what the log says it is).
# The reads the Master Controller never sends (`FIRMWARE_READS`) were never logged; their frames
# follow the request layout.
FRAMES: Dict[Tuple[int, int], Tuple[Callable[[], p.Request], int, str, str]] = {
  (0x00, 0x00): (p.get_module_id, 0x01, "0000000100", "Get Module ID"),
  (0x00, 0x02): (p.get_module_serial_number, 0x03, "0000000302", "Get Module Serial Number"),
  (0x00, 0x03): (p.get_module_hardware_version, 0x02, "0000000203", "Get Module Hardware Version"),
  (0x00, 0x08): (p.get_module_device_count, 0x06, "0000000608", "Get Module Device Count"),
  (0x00, 0x0F): (p.stop_module_execution, 0x75, "000000750F", "Stop Module Execution"),
  (0x00, 0x11): (
    p.get_module_application_image_length,
    0x05,
    "0000000511",
    "Get Module Application Image Length",
  ),
  (0x00, 0x12): (
    p.get_module_application_image_crc,
    0x04,
    "0000000412",
    "Get Module Application Image CRC",
  ),
  (0x02, 0x02): (
    lambda: p.get_board_serial_number(6),
    0x0B,
    "0206000B02",
    "Get Board 6 Serial Number",
  ),
  (0x02, 0x03): (
    lambda: p.get_board_hardware_version(1),
    0x07,
    "0201000703",
    "Get Board 1 Hardware Version",
  ),
  (0x02, 0x04): (
    lambda: p.get_board_bootloader_version(7),
    0x31,
    "0207003104",
    "Get Board 7 Bootloader Version",
  ),
  (0x02, 0x10): (
    lambda: p.get_board_application_version(1),
    0x08,
    "0201000810",
    "Get Board 1 Application Version",
  ),
  (0x02, 0x11): (
    lambda: p.get_board_application_image_length(6),
    0x0D,
    "0206000D11",
    "Get Board 6 Application Image Length",
  ),
  (0x02, 0x12): (
    lambda: p.get_board_application_image_crc(6),
    0x0C,
    "0206000C12",
    "Get Board 6 Application Image CRC",
  ),
  (0x01, 0x10): (p.get_interlock_status, 0x09, "0101000910", "(never logged)"),
  (0x01, 0x11): (p.notify_on_interlock_trip, 0x28, "010100281101", "Notify On Interlock Trip TRUE"),
  (0x01, 0x50): (p.trip_interlock, 0xEA, "010100EA50", "Trip Interlock"),
  (0x01, 0x51): (p.rearm_interlock, 0xE1, "010100E151", "Re-Arm Interlock"),
  (0x10, 0x0F): (p.stop_motion_service, 0x67, "100000670F", "Stop Motion Service"),
  (0x10, 0x50): (lambda: p.move_all(5000), 0x28, "100000285088130000", "Move All Timeout:5000"),
  (0x11, 0x10): (
    lambda: p.configure(p.Device.MOTOR, 2, MOTOR_TABLE),
    0x2F,
    "1102002F10" + MOTOR_TABLE.hex(),
    "Configure Y1 Minimum Velocity:23 ...",
  ),
  (0x11, 0x11): (lambda: p.get_state(p.Device.MOTOR, 3), 0x07, "1103000711", "(never logged)"),
  (0x11, 0x12): (
    lambda: p.set_state_enabled(p.Device.MOTOR, 3),
    0x76,
    "110300761200",
    "Set Z State ENABLED",
  ),
  (0x11, 0x21): (
    lambda: p.get_homed_state(p.Device.MOTOR, 1),
    0x3C,
    "1101003C21",
    "Get X Homed State",
  ),
  (0x11, 0x24): (
    lambda: p.get_current_position(p.Device.MOTOR, 7),
    0x80,
    "1107008024",
    "Get GZ Current Position",
  ),
  (0x11, 0x31): (lambda: p.get_control_effort(0x13), 0x96, "1113009631", "Get GG Control Effort"),
  (0x11, 0x40): (
    lambda: p.set_motor_move_parameters(
      3, position=-1000, velocity=24320, acceleration=128000, jerk=698880
    ),
    0xAB,
    "110300AB40005F000000F4010000AA0A000000000018FCFFFF0000",
    "Set Z Move Parameters Position:-1000 Velocity:24320 Acceleration:128000 Jerk:698880",
  ),
  (0x11, 0x50): (lambda: p.move(3, 30000), 0xAC, "110300AC5030750000", "Move Z Timeout:30000"),
  (0x11, 0x51): (lambda: p.home(p.Device.MOTOR, 3), 0xAD, "110300AD51", "Home Z"),
  (0x12, 0x10): (
    lambda: p.configure(p.Device.PUMP, 1, PUMP_TABLE),
    0x3F,
    "1201003F10" + PUMP_TABLE.hex(),
    "Configure D1 Minimum Start Velocity:200 ...",
  ),
  (0x12, 0x11): (lambda: p.get_state(p.Device.PUMP, 1), 0x07, "1201000711", "(never logged)"),
  (0x12, 0x12): (
    lambda: p.set_state_enabled(p.Device.PUMP, 8),
    0x6B,
    "1208006B1200",
    "Set D8 State ENABLED",
  ),
  (0x12, 0x21): (
    lambda: p.get_homed_state(p.Device.PUMP, 1),
    0xDB,
    "120100DB21",
    "Get D1 Homed State",
  ),
  (0x12, 0x24): (
    lambda: p.get_current_position(p.Device.PUMP, 8),
    0x88,
    "1208008824",
    "Get D8 Current Position",
  ),
  (0x12, 0x40): (
    lambda: p.set_pump_move_parameters(
      2,
      position=0,
      start_velocity=200,
      max_velocity=23200,
      cutoff_velocity=200,
      acceleration=50000,
      valve="input",
    ),
    0x72,
    "1202007240C8000000A05A0000C800000050C300000000000000",
    "Set D2 Move Parameters Position:0 Valve:INPUT Start Velocity:200 Maximum Velocity:23200 "
    "Cutoff Velocity:200 Acceleration:50000",
  ),
  (0x12, 0x51): (lambda: p.home(p.Device.PUMP, 1), 0x60, "1201006051", "Home D1"),
  (0x13, 0x10): (p.get_tip_status, 0x95, "1301009510", "Get Tip Status"),
  (0x13, 0x41): (
    lambda: p.set_lls_cd_sensitivity(False, 3500),
    0xA2,
    "130100A24100AC0D",
    "Set LLS Sensitivity 3500",
  ),
  (0x20, 0x08): (p.get_io_service_device_count, 0x0A, "2000000A08", "Get IO Service Device Count"),
  (0x21, 0x01): (
    lambda: p.get_input_output_name(p.Device.DIGITAL_INPUT, 1),
    0x1E,
    "2101001E01",
    "Get Digital Input 1 Name",
  ),
  (0x21, 0x10): (
    lambda: p.read_input_output(p.Device.DIGITAL_INPUT, 1),
    0x20,
    "2101002010",
    "Read Digital Input 1",
  ),
  (0x21, 0x11): (
    lambda: p.notify_on_change(p.Device.DIGITAL_INPUT, 1),
    0x1F,
    "2101001F1101",
    "Notify On Digital Input 1 Change TRUE",
  ),
  (0x22, 0x01): (
    lambda: p.get_input_output_name(p.Device.ANALOG_INPUT, 9),
    0x0B,
    "2209000B01",
    "Get Analog Input 9 Name",
  ),
  (0x22, 0x10): (
    lambda: p.read_input_output(p.Device.ANALOG_INPUT, 1),
    0x65,
    "2201006510",
    "Read Analog Input 1",
  ),
  (0x22, 0x12): (
    lambda: p.get_analog_input_units(9),
    0x0C,
    "2209000C12",
    "Get Analog Input 9 Units",
  ),
  (0x28, 0x01): (
    lambda: p.get_input_output_name(p.Device.DIGITAL_OUTPUT, 1),
    0x22,
    "2801002201",
    "Get Digital Output 1 Name",
  ),
  (0x28, 0x10): (
    lambda: p.read_input_output(p.Device.DIGITAL_OUTPUT, 1),
    0x24,
    "2801002410",
    "Read Digital Output 1",
  ),
  (0x28, 0x11): (
    lambda: p.notify_on_change(p.Device.DIGITAL_OUTPUT, 1),
    0x23,
    "280100231101",
    "Notify On Digital Output 1 Change TRUE",
  ),
  (0x2D, 0x10): (
    lambda: p.set_status_display("running"),
    0xE2,
    "2D0100E21003",
    "Set Status Display RUNNING",
  ),
  (0xAF, 0xF7): (p.get_accuframe_offsets, 0x32, "AF010032F7", "Get AccuFrame Offsets"),
  (0xFE, 0x18): (p.list_legacy_alp_devices, 0x26, "FE00002618", "List Legacy ALP Devices"),
  (0xFF, 0x10): (
    lambda: p.send_digital_io_box(b"\x02\x01"),
    0xE0,
    "FF0100E0100201",
    "Send Digital I/O Box 00 WRITE 01",
  ),
}


def decode(message: bytes) -> Tuple[int, int, int, int, bytes]:
  """A request's header and parameters, by the layout in `protocol.py`'s docstring."""
  device, index, zero, sequence, opcode = message[:5]
  assert zero == 0
  return device, index, sequence, opcode, message[5:]


class ExactFrameTests(unittest.TestCase):
  def test_every_command_has_its_frame(self) -> None:
    """A command added to `COMMANDS` needs its frame here."""
    self.assertEqual(set(FRAMES), set(p.COMMANDS))

  def test_each_request_is_the_logged_frame(self) -> None:
    for key, (build, sequence, frame_hex, logged) in FRAMES.items():
      with self.subTest(command=p.COMMANDS[key], logged=logged):
        request = build()
        self.assertEqual((request.device, request.opcode), key)
        self.assertEqual(request.encode(sequence), bytes.fromhex(frame_hex))

  def test_reads_never_logged_only_read(self) -> None:
    for key, name in p.FIRMWARE_READS.items():
      self.assertTrue(name.startswith("Get "), name)
      self.assertEqual(p.COMMANDS[key], name)

  def test_status_display_error_is_the_logged_frame(self) -> None:
    # Log.db3, after a failed move: `2D 01 00 seq 10 05`.
    self.assertEqual(p.set_status_display("error").encode(0x2E), bytes.fromhex("2D01002E1005"))

  def test_only_moves_and_homes_answer_twice(self) -> None:
    two_stage = {key for key in FRAMES if FRAMES[key][0]().two_stage}
    self.assertEqual(two_stage, {(0x10, 0x50), (0x11, 0x50), (0x11, 0x51), (0x12, 0x51)})


class RoundTripTests(unittest.TestCase):
  """Build, then parse: every field comes back out as it went in."""

  def setUp(self) -> None:
    self.random = random.Random(7)

  def test_header(self) -> None:
    for key in FRAMES:
      for _ in range(5):
        index, sequence = self.random.randrange(256), self.random.randrange(1, 256)
        params = bytes(self.random.randrange(256) for _ in range(self.random.randrange(4)))
        message = p.Request(key[0], index, key[1], params).encode(sequence)
        self.assertEqual(decode(message), (key[0], index, sequence, key[1], params))

  def test_motor_move_parameters(self) -> None:
    for _ in range(50):
      values = dict(
        velocity=self.random.randrange(2**32),
        acceleration=self.random.randrange(2**32),
        jerk=self.random.randrange(2**32),
        force=self.random.randrange(2**32),
        position=self.random.randrange(-(2**31), 2**31),
        sensor=self.random.randrange(256),
        trigger=self.random.randrange(256),
      )
      axis = self.random.randrange(1, 32)
      device, index, _, opcode, params = decode(
        p.set_motor_move_parameters(axis, **values).encode(1)
      )
      self.assertEqual((device, index, opcode), (p.Device.MOTOR, axis, 0x40))
      fields = struct.unpack("<IIIIiBB", params)
      self.assertEqual(fields, tuple(values[k] for k in values))

  def test_pump_move_parameters_with_each_valve(self) -> None:
    for valve, code in p.VALVE_POSITIONS.items():
      values = [self.random.randrange(2**32) for _ in range(4)]
      position = self.random.randrange(-(2**31), 2**31)
      request = p.set_pump_move_parameters(
        3,
        position,
        values[0],
        values[1],
        values[2],
        values[3],
        valve,  # type: ignore[arg-type]
      )
      _, index, _, opcode, params = decode(request.encode(1))
      self.assertEqual((request.device, index, opcode), (p.Device.PUMP, 3, 0x40))
      self.assertEqual(struct.unpack("<IIIIiB", params), (*values, position, code))

  def test_timeouts_status_display_and_notify(self) -> None:
    for timeout in (0, 5000, 30000, 2**32 - 1):
      self.assertEqual(struct.unpack("<I", decode(p.move_all(timeout).encode(1))[4]), (timeout,))
      self.assertEqual(struct.unpack("<I", decode(p.move(2, timeout).encode(1))[4]), (timeout,))
    for state, code in p.STATUS_DISPLAY_STATES.items():
      self.assertEqual(decode(p.set_status_display(state).encode(1))[4], bytes([code]))  # type: ignore[arg-type]
    for notify in (True, False):
      self.assertEqual(decode(p.notify_on_interlock_trip(1, notify).encode(1))[4], bytes([notify]))
      self.assertEqual(
        decode(p.notify_on_change(p.Device.DIGITAL_INPUT, 1, notify).encode(1))[4], bytes([notify])
      )

  def test_configure_carries_its_table_unchanged(self) -> None:
    for table in (MOTOR_TABLE, PUMP_TABLE):
      self.assertEqual(decode(p.configure(p.Device.MOTOR, 1, table).encode(1))[4], table)

  def test_lls_cd_sensitivity(self) -> None:
    for clot in (False, True):
      for value in (0, 1, 3500, 4095):
        params = decode(p.set_lls_cd_sensitivity(clot, value).encode(1))[4]
        self.assertEqual(struct.unpack("<BH", params), (int(clot), value))
    for value in (-1, 4096):
      with self.assertRaises(ValueError):
        p.set_lls_cd_sensitivity(False, value)

  def test_reply_fields(self) -> None:
    for _ in range(50):
      header = [self.random.randrange(256) for _ in range(5)]
      header[2] = 0
      status = self.random.choice(list(p.Status))
      data = bytes(self.random.randrange(256) for _ in range(self.random.randrange(8)))
      reply = p.Reply.parse(bytes(header) + bytes([status]) + data)
      self.assertEqual(
        (reply.device, reply.index, reply.sequence, reply.opcode, reply.status, reply.data),
        (header[0], header[1], header[3], header[4], status, data),
      )
      self.assertEqual(reply.key, (header[0], header[1], header[3], header[4]))
      self.assertEqual(reply.unsolicited, header[3] == 0)

  def test_numbers(self) -> None:
    for _ in range(50):
      s32 = self.random.randrange(-(2**31), 2**31)
      u32 = self.random.randrange(2**32)
      self.assertEqual(p.parse_s32(struct.pack("<i", s32)), s32)
      self.assertEqual(p.parse_u32(struct.pack("<I", u32)), u32)
      xyz = tuple(
        struct.unpack("<f", struct.pack("<f", self.random.uniform(-10, 10)))[0] for _ in range(3)
      )
      self.assertEqual(p.parse_accuframe_offsets(struct.pack("<fff", *xyz)), xyz)
      self.assertEqual(p.parse_float(struct.pack("<f", xyz[0])), xyz[0])

  def test_device_counts_and_hardware_versions(self) -> None:
    counts = {0x02: 6, 0x11: 21, 0x12: 8, 0x22: 300}
    payload = b"".join(bytes([d]) + n.to_bytes(2, "little") for d, n in counts.items())
    self.assertEqual(p.parse_device_count(payload), counts)
    with self.assertRaises(ValueError):
      p.parse_device_count(payload[:-1])
    version = p.parse_hardware_version(bytes([0xE3, 4]))
    self.assertEqual((version.type_code, version.revision, str(version)), (0xE3, 4, "AccuFrame 4"))
    self.assertEqual(str(p.parse_hardware_version(bytes([0x7A, 1]))), "hardware 0x7A 1")


class RefusalTests(unittest.TestCase):
  def test_unknown_commands_are_refused(self) -> None:
    with self.assertRaises(ValueError):
      p.Request(p.Device.BOARD, 1, 0x05)  # not a logged opcode: other commands stay unsendable

  def test_index_and_sequence_ranges(self) -> None:
    with self.assertRaises(ValueError):
      p.Request(p.Device.MOTOR, 256, 0x24)
    for sequence in (0, 256):
      with self.assertRaises(ValueError):
        p.get_module_id().encode(sequence)


class ReplyTests(unittest.TestCase):
  """Replies as the boards send them (the header, status, then data)."""

  def test_module_id(self) -> None:
    seen: List[Tuple[object, object]] = []
    for message in ("000000010002008000", "00000001000200FD01", "00000001000200FB02"):
      module = p.parse_module_id(
        p.Reply.parse(bytes.fromhex(message)).check("Get Module ID").payload
      )
      seen.append((module.type, module.side))
    self.assertEqual(seen, [("chassis", None), ("multichannel", "left"), ("span8", "right")])
    unknown = p.parse_module_id(bytes([0x42, 0x07]))
    self.assertEqual((unknown.type, unknown.side), (None, None))

  def test_strings(self) -> None:
    reply = p.Reply.parse(bytes.fromhex("0000000302020053494D554C41544544") + b"0" * 4)
    self.assertEqual(p.parse_string(reply.payload), "SIMULATED0000")
    reply = p.Reply.parse(bytes.fromhex("02010008100200302E322E34"))
    self.assertEqual(p.parse_string(reply.payload), "0.2.4")

  def test_interlock_status(self) -> None:
    self.assertEqual(p.parse_interlock_status(b"\x00"), "armed")
    self.assertEqual(p.parse_interlock_status(b"\xff"), "tripped")
    with self.assertRaisesRegex(ValueError, "not been seen"):
      p.parse_interlock_status(b"\x01")
    # The unsolicited trip event carries the same byte.
    event = p.Reply.parse(bytes.fromhex("01010000100300FF"))
    self.assertEqual((event.device, event.opcode), p.INTERLOCK_TRIPPED)
    self.assertEqual(p.parse_interlock_status(event.payload), "tripped")

  def test_axis_state(self) -> None:
    for code, state in [(0x00, "ENABLED"), (0x01, "DISABLED"), (0xFE, "NOT_CONFIGURED"),
                        (0xFF, "FAULT")]:  # fmt: skip
      self.assertEqual(p.parse_axis_state(bytes([code])).name, state)
    with self.assertRaises(ValueError):
      p.parse_axis_state(b"\x02")

  def test_tip_status(self) -> None:
    states = p.parse_tip_status(bytes([0, 2, 1, 2, 0, 0, 2, 9]))
    self.assertEqual(
      states, ["loaded", "unloaded", "unknown", "unloaded", "loaded", "loaded", "unloaded", None]
    )

  def test_motion_complete(self) -> None:
    sensed = p.Reply.parse(
      bytes([0x11, 9, 0, 5, 0x50, 0x02, 0, p.LLS_LIQUID_SENSED]) + struct.pack("<i", -1234)
    )
    self.assertEqual(
      p.parse_motion_complete(sensed), p.MotionComplete(0x11, 9, "liquid-sensed", -1234)
    )
    not_sensed = p.Reply.parse(bytes([0x11, 9, 0, 5, 0x50, 0x02, 0, p.LLS_LIQUID_NOT_SENSED]))
    self.assertEqual(
      p.parse_motion_complete(not_sensed), p.MotionComplete(0x11, 9, "liquid-not-sensed")
    )
    plain = p.Reply.parse(bytes([0x11, 9, 0, 5, 0x50, 0x02, 0]))
    self.assertEqual(p.parse_motion_complete(plain), p.MotionComplete(0x11, 9, "done"))

  def test_emcy(self) -> None:
    emcy = p.parse_emcy(bytes.fromhex("007700100116040100"))
    self.assertEqual(
      (emcy.node, emcy.error_code, emcy.error_register, emcy.device_error, emcy.source),
      (0x77, 0x1000, 0x01, 0x0416, 0x01),
    )

  def test_success_has_no_error(self) -> None:
    # Get Module ID's DONE; a Move's ACCEPTED and DONE; the host-alive NOTICE (sequence 0).
    for message in ("000000010002" + "008000", "110300AC5001" + "00", "110300AC5002" + "00",
                    "0000000040FE" + "00"):  # fmt: skip
      self.assertIsNone(p.Reply.parse(bytes.fromhex(message)).error_code, message)

  def test_errors_raise_their_kind(self) -> None:
    cases = {
      "1103000712" + "0030": NotConfiguredError,
      "1103000751" + "0031": NotEnabledError,
      "1103000740" + "0032": NotHomedError,
      "1003000750" + "000F": InterlockTrippedError,
      "1003000750" + "0238": MotionError,
      "110300075002" + "3C0001": MotionError,
      "1103000750" + "0077": AceError,
    }
    for message, kind in cases.items():
      with self.subTest(message=message):
        reply = p.Reply.parse(bytes.fromhex(message.replace(" ", "")))
        with self.assertRaises(kind):
          reply.raise_for_error("request")

  def test_device_error_follows_the_ace_error(self) -> None:
    # A Move whose axis left its error window: DONE, ACE error 0x3C, device error 0x0100.
    reply = p.Reply.parse(bytes.fromhex("110300075002" + "3C0001"))
    self.assertEqual((reply.error_code, reply.device_error), (0x3C, 0x0100))
    with self.assertRaisesRegex(MotionError, r"device error 0x100: motion error window exceeded"):
      reply.raise_for_error("Move Z")


class SagianCanTests(unittest.TestCase):
  """Forwarded SAGIAN CAN messages, worded as the Master Controller's log words them."""

  # Every distinct pattern in the Master Controller's logs (2026-07-27 .. 10-27), with its words.
  LOGGED = {
    "00010500000101f7280fbb40": "TO_NODE 05:00 READ SUCCESS F7 28 0F BB 40",
    "00010500000001f0": "TO_NODE 05:00 ACTION SUCCESS F0",
    "0001050000020101": "TO_NODE 05:00 WRITE SUCCESS 01",
    "000105000003016801": "TO_NODE 05:00 EVENT SUCCESS 68 01",
    "00012300040300": "TO_NODE 23:04 EVENT REQUEST",
    "00012a00080300": "TO_NODE 2A:08 EVENT REQUEST",
  }

  def test_logged_messages_read_as_the_master_controller_reads_them(self) -> None:
    for data, words in self.LOGGED.items():
      with self.subTest(data=data):
        self.assertEqual(p.describe_sagian_can(bytes.fromhex(data)), (words, True))

  def test_any_other_status_is_not_called_normal(self) -> None:
    text, normal = p.describe_sagian_can(bytes.fromhex("00010500000102"))
    self.assertFalse(normal)
    self.assertIn("status 0x02", text)
    self.assertFalse(p.describe_sagian_can(bytes([0, 1]))[1])


class RoutineUnsolicitedTests(unittest.TestCase):
  """Unsolicited messages every session sees, as the instrument sent them (2026-11-09)."""

  @staticmethod
  def reply(message: str) -> p.Reply:
    return p.Reply.parse(bytes.fromhex(message))

  def test_a_drive_clearing_its_fault_is_routine(self) -> None:
    text = p.describe_routine_unsolicited(self.reply("00000000 31ff 007e0000000000000000"))
    self.assertEqual(text, "drive node 0x7E: fault cleared (EMCY 0000)")

  def test_a_drive_reporting_the_interlock_is_routine(self) -> None:
    for source in ("01", "02"):
      with self.subTest(source=source):
        text = p.describe_routine_unsolicited(
          self.reply(f"0000000031ff007700100116 04{source}0000")
        )
        self.assertEqual(text, "drive node 0x77: interlock active (EMCY 1000)")

  def test_a_move_the_interlock_ended_is_routine(self) -> None:
    text = p.describe_routine_unsolicited(self.reply("11120000 50fe 0f1604"))
    self.assertEqual(text, "device 0x11 index 18: move ended, interlock tripped")

  def test_other_messages_are_shown(self) -> None:
    for message in (
      "00000000 31ff 007e0010010704010000",  # another drive error: interlock tripped (0407)
      "11120000 50fe 3c0001",  # a move ended by its error window
      "01010000 1003 00ff",  # the chassis: the interlock tripped
      "21010000 1003 0001",  # a digital input changed (the door)
      "00000000 31ff 007e",  # too short to read
    ):
      with self.subTest(message=message):
        self.assertIsNone(p.describe_routine_unsolicited(self.reply(message.replace(" ", ""))))


if __name__ == "__main__":
  unittest.main()
