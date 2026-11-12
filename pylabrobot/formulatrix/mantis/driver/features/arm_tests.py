"""Arm tests: the bring-up and the park against a Mantis v4's own startup and close, as recorded.

The recorded startup declared each arm where its unit's encoder correction put it; these tests
declare it with a synthetic correction, so only those values differ from what was recorded. The
offsets declared are the ones the recorded idle targets imply, so the moves are sent as recorded.
"""

import json
import os
import struct
import tempfile
import unittest
from typing import Dict, List, Tuple
from unittest import mock

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver import master as master_module
from pylabrobot.formulatrix.mantis.driver.arm_kinematics import (
  compute_absolute_device_position,
  compute_device_position,
  compute_joint_angles,
  compute_position,
)
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisMotionError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features.arm import POST_HOMING_POSITION, Arm
from pylabrobot.formulatrix.mantis.driver.features.led_bar_tests import (
  BLUE,
  GREEN,
  OFF,
  RED,
  START_IDLE_ANIMATION,
  STOP_ANIMATION,
  bar_states,
  color_write,
  led_bar_frames,
  make_led_bar_answers,
)
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  RECORDED_MOTOR_ERROR_EVENTS,
  DriverTestCase,
  FakeLink,
  make_answers,
)

with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
  RECORDING = json.load(f)
# Where the recorded moves to idle (command 22) sent the arm motors, in device units.
RECORDED_IDLE_TARGETS = {
  0: struct.unpack("<d", bytes.fromhex("742631100e96943f"))[0],
  1: struct.unpack("<d", bytes.fromhex("c67d71e5c1efd63f"))[0],
}
# A synthetic arm block: the offsets those targets imply, round references, each encoder reading
# its own ideal value.
IDLE_ANGLES = compute_joint_angles(x=POST_HOMING_POSITION[0], y=POST_HOMING_POSITION[1])
SYNTHETIC_OFFSETS = {
  motor: IDLE_ANGLES[motor]
  - compute_position(motor=motor, device_position=RECORDED_IDLE_TARGETS[motor])
  for motor in (0, 1)
}
SYNTHETIC_HOMING_POSITION_A = {0: 154.0, 1: 52.0}
SYNTHETIC_CORRECTION = [[float(row), float(row)] for row in range(1, 201)]
SYNTHETIC_ARM_BLOCK = {
  "offsets": {str(motor): value for motor, value in SYNTHETIC_OFFSETS.items()},
  "homing_position_a": {str(motor): value for motor, value in SYNTHETIC_HOMING_POSITION_A.items()},
  "encoder_correction": {"0": SYNTHETIC_CORRECTION, "1": SYNTHETIC_CORRECTION},
}
# A Mantis v4's startup from power-on, parked at idle: every command but status reads, as
# board:command:payload; it declared the arms (28, 85) where its unit's correction put them.
RECORDED_STARTUP = """
0:19:00000000 0:19:01000000 0:17:0000 0:17:0100 11:30:0000 11:30:0000 11:30:0100 11:30:0100
0:28:00000089efecb153e2bf 0:85:00000089efecb153e2bf 0:28:010060768f9aa505fc3f
0:85:010060768f9aa505fc3f 0:17:0000 0:17:0100 11:30:0000 11:30:0100 0:28:00000089efecb153e2bf
0:85:00000089efecb153e2bf 0:28:01004027f9fc4638fc3f 0:85:01004027f9fc4638fc3f 0:17:0000 0:17:0100
11:30:0000 11:30:0100 0:28:00000089efecb153e2bf 0:85:00000089efecb153e2bf 0:28:0100e0ccc437d56afc3f
0:85:0100e0ccc437d56afc3f 0:83:000001000000000000001040 0:83:010001000000000000001040 0:17:0200
0:17:0000 0:17:0100 11:30:0000 11:30:0100 0:28:0000002f37c4b4b8e2bf 0:85:0000002f37c4b4b8e2bf
0:28:01004027f9fc4638fc3f 0:85:01004027f9fc4638fc3f 0:17:0000 0:17:0100 11:30:0000 11:30:0100
0:28:0000002f37c4b4b8e2bf 0:85:0000002f37c4b4b8e2bf 0:28:0100e0ccc437d56afc3f
0:85:0100e0ccc437d56afc3f 0:17:0000 0:17:0100 11:30:0000 11:30:0100 0:28:0000002f37c4b4b8e2bf
0:85:0000002f37c4b4b8e2bf 0:28:01004027f9fc4638fc3f 0:85:01004027f9fc4638fc3f 0:27:0000 0:27:0100
0:27:0200 0:22:0000742631100e96943fb4a291808fb3b540814e1be8b40a8a40
0:22:0100c67d71e5c1efd63fb4a291808fb3b540814e1be8b40a8a40 0:82:0000 0:82:0100 0:82:0200 0:17:0200
0:21:0200000001000000d343867e1e874d4064ea130f737482407d2ae7ab04c2ce40 0:17:0000 0:17:0100 11:30:0000
11:30:0100 0:28:000000c014a61d81893f 0:85:000000c014a61d81893f 0:28:010080b6d75b1576db3f
0:85:010080b6d75b1576db3f 0:17:0000 0:17:0100 11:30:0000 11:30:0100 0:28:000000c014a61d81893f
0:85:000000c014a61d81893f 0:28:0100008083a7cdb2da3f 0:85:0100008083a7cdb2da3f 0:17:0000 0:17:0100
11:30:0000 11:30:0100 0:28:000000c014a61d81893f 0:85:000000c014a61d81893f 0:28:010080ed2b105d39dc3f
0:85:010080ed2b105d39dc3f 0:27:0000 0:27:0100 0:27:0200
0:22:020000000000000000004cd431fab7eaa040bb34be4f05c2be40
0:22:0000742631100e96943fb4a291808fb3b540814e1be8b40a8a40
0:22:0100c67d71e5c1efd63fb4a291808fb3b540814e1be8b40a8a40
0:22:0000742631100e96943fb4a291808fb3b540814e1be8b40a8a40
0:22:0100c67d71e5c1efd63fb4a291808fb3b540814e1be8b40a8a40
0:22:020000000000000000004cd431fab7eaa040bb34be4f05c2be40 0:17:0000 0:17:0100 11:30:0000 11:30:0100
0:28:000000000000000061bd 0:85:000000000000000061bd 0:28:010000a432d6aea5d73f
0:85:010000a432d6aea5d73f 0:17:0000 0:17:0100 11:30:0000 11:30:0100 0:28:000000c016a61d81993f
0:85:000000c016a61d81993f 0:28:0100006dde2167e2d63f 0:85:0100006dde2167e2d63f 0:17:0000 0:17:0100
11:30:0000 11:30:0100 0:28:000000c014a61d81893f 0:85:000000c014a61d81893f 0:28:0100006dde2167e2d63f
0:85:0100006dde2167e2d63f 0:27:0000 0:27:0100 0:27:0200
0:22:020000000000000000004cd431fab7eaa040bb34be4f05c2be40
0:22:0000742631100e96943fb4a291808fb3b540814e1be8b40a8a40
0:22:0100c67d71e5c1efd63fb4a291808fb3b540814e1be8b40a8a40
0:22:0000742631100e96943fb4a291808fb3b540814e1be8b40a8a40
0:22:0100c67d71e5c1efd63fb4a291808fb3b540814e1be8b40a8a40
0:22:020000000000000000004cd431fab7eaa040bb34be4f05c2be40 0:82:0000 0:82:0100 0:82:0200 0:17:0200
0:21:0200000001000000d343867e1e874d4064ea130f737482407d2ae7ab04c2ce40
""".split()
# Its absolute encoder replies by motor, in turn: after power-on the first is not valid.
STARTUP_ABSOLUTE_ENCODER_REPLIES = {
  0: ["00000000000000000000", "010000000000bc3f6340", "010000000000bc3f6340"]
  + ["010000000000bc3f6340", "010000000000583f6340", "010000000000583f6340"]
  + ["010000000000583f6340", "01000000000018526340", "01000000000018526340"]
  + ["01000000000018526340", "010000000000b4516340", "0100000000007c526340"]
  + ["01000000000018526340"],
  1: ["00000000000000000000", "01000000000060fd4a40", "010000000000f0fe4a40"]
  + ["01000000000080004b40", "010000000000f0fe4a40", "01000000000080004b40"]
  + ["010000000000f0fe4a40", "010000000000a0544a40", "01000000000010534a40"]
  + ["01000000000030564a40", "010000000000d04c4a40", "010000000000404b4a40"]
  + ["010000000000404b4a40"],
}
# Its motor position replies by motor, in turn, each time it read where the arm was.
STARTUP_POSITION_REPLIES = {
  0: [
    "000000c0b4b8e2bf00000000000000000000000000000000000000c0b4b8e2bf"
    "00000000000000000000000000407f40",
    "000000002081893f00000000000000000000000000000000000000002081893f"
    "000000808fb3b540000000e0b40a8a40",
  ],
  1: [
    "000000004738fc3f00000000000000000000000000000000000000004738fc3f"
    "00000000000000000000000000407f40",
    "000000005d39dc3f00000000000000000000000000000000000000805c39dc3f"
    "000000808fb3b540000000e0b40a8a40",
    "0000000067e2d63f000000000000000000000000000000000000000067e2d63f"
    "000000808fb3b540000000e0b40a8a40",
  ],
  2: [
    "0000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000407f40",
    "000000000078c0bf00000000000000000000000000000000000000000078c0bf"
    "0000000000000000000000a004c2ce40",
    "0000000000000000000000000000000000000000000000000000000000000000"
    "00000000b8eaa0400000004005c2be40",
  ],
}
# Z reported its homing done.
STARTUP_HOME_DONE = "1e00000069000000000002020200000000004738a1c0000000000078c0bf513d"
# Motor status words it read: moving, idle, Z homing and Z homed.
STATUS_MOVING, STATUS_IDLE, STATUS_HOMED = "0100", "0000", "0400"
# Motor position replies with the head away from idle, at about (-57, 0) mm.
AWAY_POSITION_REPLIES = {
  0: "0000008085a148c000000000000000000000000000000000000000a085a148c0"
  "000000808fb3b540000000e0b40a8a40",
  1: "000000a00ae24f4000000000000000000000000000000000000000800ae24f40"
  "000000808fb3b540000000e0b40a8a40",
  2: "00000000d0e74ec00000000000000000000000000000000000000000d0e74ec0"
  "00000000b8eaa0400000004005c2be40",
}
# The recorded close: queued moves 13 to 16 and the reply to each, the sequencer's clear and start,
# and the progress reports of 15 and 16 played with none left queued.
CLOSE_QUEUED_MOVES = {
  13: "0d000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000"
  "000000000000000000000000000000004cd431fab7eaa040bb34be4f05c2be40",
  14: "0e0000000100742631100e96943fb4a291808fb3b540814e1be8b40a8a40c67d71e5c1efd63fb4a291808fb3b540"
  "814e1be8b40a8a40000000000000000000000000000000000000000000000000",
  15: "0f0000000100742631100e96943fb4a291808fb3b540814e1be8b40a8a40c67d71e5c1efd63fb4a291808fb3b540"
  "814e1be8b40a8a4000000000000000004cd431fab7eaa040bb34be4f05c2be40",
  16: "100000000100742631100e96943fb4a291808fb3b540814e1be8b40a8a40c67d71e5c1efd63fb4a291808fb3b540"
  "814e1be8b40a8a4066b3d96c369b88404cd431fab7eaa040bb34be4f05c2be40",
}
CLOSE_QUEUED_MOVE_REPLY = "c7000000"
CLOSE_ITEM_PLAYED = {
  15: "12000000a5070000000010030f000000c8006004",
  16: "12000000d30700000000100310000000c8000904",
}
HALT = [
  (0, 64, b""),
  (0, 24, bytes.fromhex("0000814e1be8b40a8a40")),
  (0, 24, bytes.fromhex("0100814e1be8b40a8a40")),
  (0, 24, bytes.fromhex("0200bb34be4f05c2be40")),
]
UNPOWER = [(0, 19, bytes.fromhex("00000100")), (0, 19, bytes.fromhex("01000100"))]
# The recorded move of Z alone to its flag (command 22) and its reply.
Z_UP_MOVE = bytes.fromhex("020000000000000000004cd431fab7eaa040bb34be4f05c2be40")
MOVE_REPLY = "0000"
# Z's encoder read (command 84) at its flag, and 20 mm down, at the shutdown height.
Z_UP_ENCODER_REPLY = "000000000000d0bf"
Z_DOWN_ENCODER_REPLY = "00000000009a8840"
# The LED bar turned off: the animation stopped, then all 20 LEDs written.
TURN_OFF = [STOP_ANIMATION, color_write(colors=[OFF] * 20)]


def token(command: Tuple[int, int, bytes]) -> str:
  board, code, payload = command
  return f"{board}:{code}:{payload.hex()}"


def get_expected_startup() -> List[str]:
  """The recorded startup with each arm declared where the synthetic correction puts it."""
  replies = {motor: list(STARTUP_ABSOLUTE_ENCODER_REPLIES[motor]) for motor in (0, 1)}
  declared: Dict[int, float] = {}
  expected = []
  for sent in RECORDED_STARTUP:
    board, command, payload = sent.split(":")
    motor = struct.unpack_from("<h", bytes.fromhex(payload))[0] if payload else 0
    if (board, command) == ("11", "30"):
      valid, reading = struct.unpack("<Hd", bytes.fromhex(replies[motor].pop(0)))
      if valid:
        declared[motor] = compute_absolute_device_position(
          motor=motor,
          reading=reading,
          correction=[(ideal, measured) for ideal, measured in SYNTHETIC_CORRECTION],
          homing_position_a=SYNTHETIC_HOMING_POSITION_A[motor],
        )
    if (board, command) in (("0", "28"), ("0", "85")):
      sent = f"0:{command}:{struct.pack('<hd', motor, declared[motor]).hex()}"
    expected.append(sent)
  return expected


class ArmLink(FakeLink):
  """Answers the startup's commands as recorded; each motor reads moving once after its move
  starts, then idle, and Z homed once it homed."""

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    for sent in get_expected_startup() + RECORDED_STARTUP:
      board, command, payload = sent.split(":")
      reply = "0000" if command in ("19", "21", "22") else ""
      if command in ("17", "19", "21", "22", "28", "83", "85"):
        self.answers[(int(board), int(command), bytes.fromhex(payload))] = [bytes.fromhex(reply)]
    for motor in (0, 1, 2):
      key = struct.pack("<h", motor)
      self.answers[(0, 20, key)] = [bytes.fromhex(STATUS_IDLE)]
      self.answers[(0, 27, key)] = [
        bytes.fromhex(reply) for reply in STARTUP_POSITION_REPLIES[motor]
      ]
      if motor in (0, 1):
        self.answers[(11, 30, key)] = [
          bytes.fromhex(reply) for reply in STARTUP_ABSOLUTE_ENCODER_REPLIES[motor]
        ]
    self.answers[(0, 82, b"\x01\x00")] = [bytes.fromhex("01000000000000001040")]

  async def write(self, data: bytes) -> int:
    _, board, command, payload = parse_frame(frame=data)
    if (board, command) == (0, 22) and payload[:2] != b"\x02\x00":
      self.answers[(0, 20, payload[:2])] = [
        bytes.fromhex(STATUS_MOVING),
        bytes.fromhex(STATUS_IDLE),
      ]
    if (board, command) == (0, 21):
      self.answers[(0, 20, b"\x02\x00")] = [bytes.fromhex(STATUS_HOMED)]
    return await super().write(data)


class ArmTestCase(DriverTestCase):
  def setUp(self):
    super().setUp()
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.directory = directory.name

  def declare(self, *, arm_block) -> str:
    path = os.path.join(self.directory, f"declared_{len(os.listdir(self.directory))}.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, **({"arm": arm_block} if arm_block is not None else {})}, f)
    return path

  async def set_up_arm(self, *, link: FakeLink, arm_block=SYNTHETIC_ARM_BLOCK):
    driver = await self.set_up_driver(
      link=link, declared_configuration_json=self.declare(arm_block=arm_block)
    )
    assert driver.arm is not None
    self.sent_before = len(link.written)
    return driver

  def sent(self, link: FakeLink) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in link.written[self.sent_before :]]


class InitializeTests(ArmTestCase):
  async def test_initialize_sends_the_recorded_startup(self):
    link = ArmLink(
      answers=make_answers(),
      events={
        (0, 21, bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])): [
          bytes.fromhex(STARTUP_HOME_DONE)
        ]
      },
    )
    driver = await self.set_up_arm(link=link)
    await driver.arm.initialize()
    commands = self.sent(link)
    self.assertEqual(
      [token(command) for command in commands if command[1] != 20], get_expected_startup()
    )
    self.assertTrue(driver.arm.is_initialized)
    for index, (_, command, payload) in enumerate(commands):
      if command == 21:
        # Before each Z home: each motor's following-error check and status, then Z's faults clear.
        self.assertEqual(
          commands[index - 7 : index - 1],
          [(0, code, struct.pack("<h", motor)) for motor in (0, 1, 2) for code in (82, 20)],
        )
      if command == 22:
        self.assertEqual(commands[index + 1], (0, 20, payload[:2]))  # read once as it starts

  async def test_a_failure_energising_unpowers_both_arm_motors(self):
    link = ArmLink(answers=make_answers())
    del link.answers[(0, 19, bytes.fromhex("01000000"))]
    driver = await self.set_up_arm(link=link)
    with self.assertRaises(MantisTimeoutError):
      await driver.arm.initialize()
    self.assertEqual(self.sent(link)[-2:], UNPOWER)
    self.assertFalse(driver.arm.is_initialized)

  async def test_without_the_arm_block_nothing_is_sent(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_arm(link=link, arm_block=None)
    with self.assertRaisesRegex(MantisConfigurationError, "no arm calibration"):
      await driver.arm.initialize()
    self.assertEqual(self.sent(link), [])

  async def test_setup_without_the_arm_block_refuses_before_anything_moves(self):
    link = FakeLink(answers=make_answers())
    driver = self.make_driver(link=link, declared_configuration_json=self.declare(arm_block=None))
    with self.assertRaisesRegex(MantisConfigurationError, "no arm calibration"):
      await driver.setup()
    commands = [parse_frame(frame=frame)[2] for frame in link.written]
    self.assertNotIn(19, commands)
    self.assertNotIn(22, commands)
    link.io.stop.assert_awaited()

  async def test_a_head_away_from_idle_refuses_and_unpowers_the_arms(self):
    link = ArmLink(answers=make_answers())
    for motor, reply in AWAY_POSITION_REPLIES.items():
      link.answers[(0, 27, struct.pack("<h", motor))] = [bytes.fromhex(reply)]
    driver = await self.set_up_arm(link=link)
    with self.assertRaisesRegex(MantisMotionError, "moved to idle only from x"):
      await driver.arm.initialize()
    sent = self.sent(link)
    self.assertNotIn(22, [command for _, command, _ in sent])
    self.assertEqual(sent[-2:], UNPOWER)
    self.assertFalse(driver.arm.is_initialized)

  async def test_a_motor_error_while_z_homes_fails_at_once_and_unpowers_the_arms(self):
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    link = ArmLink(
      answers=make_answers(),
      events={(0, 21, home): [bytes.fromhex(RECORDED_MOTOR_ERROR_EVENTS[0])]},
    )
    driver = await self.set_up_arm(link=link)
    with self.assertRaisesRegex(MantisMotionError, "reported error 9") as caught:
      await driver.arm.initialize()
    self.assertEqual((caught.exception.motor, caught.exception.code), (0, 9))
    self.assertEqual(self.sent(link)[-2:], UNPOWER)

  async def test_a_motor_that_slipped_stops_z_homing_before_it_starts(self):
    link = ArmLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    # After power-on, motor 1 read a following error while idle.
    link.answers[(0, 20, b"\x01\x00")] = [bytes.fromhex("8000")]
    with self.assertRaisesRegex(MantisMotionError, "motor 1 slipped"):
      await driver.arm._home_z()
    self.assertNotIn(21, [command for _, command, _ in self.sent(link)])

  async def test_reads_convert_to_degrees_and_mm(self):
    link = ArmLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    joint_position = await driver.arm.request_joint_position()
    self.assertAlmostEqual(joint_position[0], -0.585047 * 1.8, places=5)
    self.assertAlmostEqual(joint_position[1], 1.76374 * 1.8, places=4)
    self.assertEqual(joint_position[2], 0.0)
    absolute = await driver.arm.request_absolute_joint_position()
    self.assertEqual(sorted(absolute), [0, 1])


class SetupDisplayTests(ArmTestCase):
  def make_driver(self, *, link: FakeLink, **kwargs) -> MantisDriver:
    driver = super().make_driver(link=link, **kwargs)
    # every command of the bring-up is answered; a slow test runner must not time one out
    driver.default_read_timeout = 5.0
    return driver

  def startup_link(self, *, answers) -> ArmLink:
    """Answers the recorded startup, Z reporting its homing done, and ``answers``."""
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    return ArmLink(answers=answers, events={(0, 21, home): [bytes.fromhex(STARTUP_HOME_DONE)]})

  async def test_setup_shows_the_bring_up_in_blue_then_green_then_idle(self):
    link = self.startup_link(answers=make_led_bar_answers())
    driver = await self.set_up_driver(
      link=link,
      skip_arm=False,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    assert driver.arm is not None
    self.assertTrue(driver.arm.is_initialized)
    frames = [parse_frame(frame=frame)[1:] for frame in link.written]
    moves = [index for index, (_, command, _) in enumerate(frames) if command in (19, 21, 22)]
    self.assertLess(frames.index(color_write(colors=[BLUE] * 20)), moves[0])
    self.assertLess(moves[-1], frames.index(color_write(colors=[GREEN] * 20)))
    self.assertEqual(bar_states(written=link.written), [BLUE, GREEN, "idle"])

  async def test_a_failed_bring_up_shows_red_then_turns_off(self):
    link = self.startup_link(answers=make_led_bar_answers())
    for motor, reply in AWAY_POSITION_REPLIES.items():
      link.answers[(0, 27, struct.pack("<h", motor))] = [bytes.fromhex(reply)]
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    with self.assertRaisesRegex(MantisMotionError, "moved to idle only from x"):
      await driver.setup()
    self.assertEqual(bar_states(written=link.written), [BLUE, RED, OFF])

  async def test_a_led_bar_fault_does_not_stop_the_bring_up(self):
    link = self.startup_link(answers=make_answers())
    del link.answers[color_write(colors=[BLUE] * 20)]
    with self.assertLogs(
      "pylabrobot.formulatrix.mantis.driver.features.led_bar", level="WARNING"
    ) as logs:
      driver = await self.set_up_driver(
        link=link,
        skip_arm=False,
        declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
      )
    self.assertTrue(any("did not show 'setup'" in line for line in logs.output))
    assert driver.arm is not None
    self.assertTrue(driver.arm.is_initialized)

  async def test_without_show_actions_the_bring_up_shows_only_the_idle_animation(self):
    link = self.startup_link(answers=make_answers())
    driver = await self.set_up_driver(
      link=link,
      skip_arm=False,
      show_actions=False,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    assert driver.arm is not None
    self.assertTrue(driver.arm.is_initialized)
    self.assertEqual(led_bar_frames(written=link.written), [START_IDLE_ANIMATION])


class IdleTargetTests(unittest.TestCase):
  def test_idle_targets_follow_the_units_offsets(self):
    for offsets in ({0: 0.0, 1: 0.0}, {0: 1.0, 1: -2.0}):
      with self.subTest(offsets=offsets):
        arm = Arm(
          driver=mock.Mock(),
          offsets=offsets,
          homing_position_a=SYNTHETIC_HOMING_POSITION_A,
          encoder_correction={0: [], 1: []},
        )
        for motor, target in arm._get_idle_targets().items():
          expected = IDLE_ANGLES[motor] - offsets[motor]
          self.assertAlmostEqual(target, compute_device_position(motor=motor, position=expected))

  def test_the_implied_offsets_give_the_recorded_idle_targets(self):
    arm = Arm(
      driver=mock.Mock(),
      offsets=SYNTHETIC_OFFSETS,
      homing_position_a=SYNTHETIC_HOMING_POSITION_A,
      encoder_correction={0: [], 1: []},
    )
    targets = arm._get_idle_targets()
    self.assertAlmostEqual(targets[0], RECORDED_IDLE_TARGETS[0], places=12)
    self.assertEqual(targets[1], RECORDED_IDLE_TARGETS[1])

  def test_without_the_arm_block_there_are_no_idle_targets(self):
    arm = Arm(driver=mock.Mock(), offsets=None, homing_position_a=None, encoder_correction=None)
    with self.assertRaises(MantisConfigurationError):
      arm._get_idle_targets()


class DefaultTests(unittest.TestCase):
  def test_defaults_are_checked_when_assigned(self):
    from pylabrobot.formulatrix.mantis.driver.features.arm import Arm

    arm = Arm(driver=mock.Mock(), offsets=None, homing_position_a=None, encoder_correction=None)
    for name in ("default_arm_speed", "default_arm_acceleration", "default_z_speed"):
      with self.subTest(name=name):
        with self.assertRaises(ValueError):
          setattr(arm, name, 0.0)
    arm.default_z_acceleration = 100.0
    self.assertEqual(arm.default_z_acceleration, 100.0)


def get_away_arm_block() -> Dict:
  """The synthetic arm block, its references putting the last recorded readings at (-57, 0) mm."""
  homing_position_a = {}
  for motor, angle in enumerate(compute_joint_angles(x=-57.0, y=0.0)):
    _, reading = struct.unpack("<Hd", bytes.fromhex(STARTUP_ABSOLUTE_ENCODER_REPLIES[motor][-1]))
    position = compute_device_position(motor=motor, position=angle - SYNTHETIC_OFFSETS[motor])
    homing_position_a[str(motor)] = (reading - position) % 200.0
  return {**SYNTHETIC_ARM_BLOCK, "homing_position_a": homing_position_a}


class StopTests(ArmTestCase):
  def close_link(self) -> FakeLink:
    """Answers the recorded close; each motor reads idle, each encoder as last in the startup."""
    link = FakeLink(
      answers=make_answers(),
      events={
        (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[item])): [bytes.fromhex(CLOSE_ITEM_PLAYED[item])]
        for item in (15, 16)
      },
    )
    for motor in (0, 1, 2):
      key = struct.pack("<h", motor)
      link.answers[(0, 20, key)] = [bytes.fromhex(STATUS_IDLE)]
      if motor in (0, 1):
        link.answers[(11, 30, key)] = [bytes.fromhex(STARTUP_ABSOLUTE_ENCODER_REPLIES[motor][-1])]
    for request in CLOSE_QUEUED_MOVES.values():
      link.answers[(0, 68, bytes.fromhex(request))] = [bytes.fromhex(CLOSE_QUEUED_MOVE_REPLY)]
    for command in (61, 62):
      link.answers[(0, command, b"")] = [bytes.fromhex("0000")]
    link.answers[(0, 22, Z_UP_MOVE)] = [bytes.fromhex(MOVE_REPLY)]
    link.answers[(0, 84, b"\x02\x00")] = [bytes.fromhex(Z_UP_ENCODER_REPLY)]
    return link

  async def test_stop_halts_then_parks_z_first_then_as_the_recorded_close(self):
    link = self.close_link()
    driver = await self.set_up_arm(link=link)
    driver.arm._initialized = True
    driver._next_item = 14  # numbered as in the recorded close
    await driver.stop()
    sent = self.sent(link)
    self.assertEqual(sent[:4], HALT)
    statuses = [(0, 20, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    self.assertEqual(
      sent[4:22],
      statuses
      + [(11, 30, struct.pack("<h", motor)) for motor in (0, 1)]
      # Z up on its own, read back by its encoder before the arms move.
      + [(0, 22, Z_UP_MOVE), (0, 20, b"\x02\x00")]
      + statuses
      + [(0, 84, b"\x02\x00")]
      + [(0, 61, b""), (0, 62, b"")]
      + [(0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[item])) for item in (14, 15)]
      + [(0, 61, b""), (0, 62, b""), (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[16]))],
    )
    self.assertEqual(sent[22][:2], (12, 33))
    self.assertEqual(sent[-2:], TURN_OFF)
    self.assertFalse(driver.arm.is_initialized)

  async def test_z_not_up_by_its_encoder_stops_before_the_arms_move(self):
    link = self.close_link()
    link.answers[(0, 84, b"\x02\x00")] = [bytes.fromhex(Z_DOWN_ENCODER_REPLY)]
    driver = await self.set_up_arm(link=link)
    driver.arm._initialized = True
    with self.assertLogs(
      "pylabrobot.formulatrix.mantis.driver.features.arm", level="WARNING"
    ) as logs:
      await driver.stop()
    self.assertTrue(any("Z reads 19.996 mm below its flag" in line for line in logs.output))
    commands = [command for _, command, _ in self.sent(link)]
    self.assertEqual(commands.count(22), 1)
    self.assertNotIn(68, commands)
    self.assertIn(33, commands)

  async def test_a_motor_that_did_not_stop_is_not_parked(self):
    link = self.close_link()
    del link.answers[(0, 24, bytes.fromhex("0100814e1be8b40a8a40"))]
    driver = await self.set_up_arm(link=link)
    driver.arm._initialized = True
    with self.assertLogs(
      "pylabrobot.formulatrix.mantis.driver.features.arm", level="WARNING"
    ) as logs:
      await driver.stop()
    self.assertEqual(
      sum("a motor did not stop; the arm is not parked" in line for line in logs.output), 1
    )
    sent = self.sent(link)
    self.assertEqual([command for _, command, _ in sent if command == 24], [24, 24, 24])
    self.assertFalse({22, 30, 68} & {command for _, command, _ in sent})

  async def test_a_head_away_from_idle_is_not_parked_and_the_clamp_is_still_released(self):
    link = self.close_link()
    driver = await self.set_up_arm(link=link, arm_block=get_away_arm_block())
    driver.arm._initialized = True
    with self.assertLogs(
      "pylabrobot.formulatrix.mantis.driver.features.arm", level="WARNING"
    ) as logs:
      await driver.stop()
    self.assertTrue(any("the arm was not parked" in line for line in logs.output))
    self.assertTrue(any("moved to idle only from x" in line for line in logs.output))
    commands = [command for _, command, _ in self.sent(link)]
    self.assertNotIn(68, commands)
    self.assertIn(33, commands)

  async def test_a_motor_that_slipped_is_not_parked(self):
    link = self.close_link()
    link.answers[(0, 20, b"\x01\x00")] = [bytes.fromhex("8000")]
    driver = await self.set_up_arm(link=link)
    driver.arm._initialized = True
    with self.assertLogs(
      "pylabrobot.formulatrix.mantis.driver.features.arm", level="WARNING"
    ) as logs:
      await driver.stop()
    self.assertTrue(any("the arm was not parked" in line for line in logs.output))
    commands = [command for _, command, _ in self.sent(link)]
    self.assertNotIn(30, commands)
    self.assertNotIn(68, commands)

  async def test_an_arm_not_brought_up_is_halted_but_not_parked(self):
    link = self.close_link()
    driver = await self.set_up_arm(link=link)
    await driver.stop()
    self.assertEqual(self.sent(link)[:4], HALT)
    self.assertNotIn(68, [command for _, command, _ in self.sent(link)])

  async def test_without_a_led_bar_stop_halts_and_shows_nothing(self):
    link = self.close_link()
    driver = await self.set_up_arm(link=link)
    driver.led_bar = None
    await driver.stop()
    self.assertEqual(self.sent(link)[:4], HALT)
    self.assertEqual(led_bar_frames(written=link.written[self.sent_before :]), [])


class RaisingLink(ArmLink):
  """As ``ArmLink``, and the write of one frame raises, as an unplugged link does."""

  def __init__(self, *, raises_on: Tuple[int, int, bytes], **kwargs):
    super().__init__(**kwargs)
    self.raises_on = raises_on

  async def write(self, data: bytes) -> int:
    if parse_frame(frame=data)[1:] == self.raises_on:
      self.written.append(data)
      raise OSError("device unplugged")
    return await super().write(data)


class MoveFailureTests(ArmTestCase):
  async def test_a_failed_send_stops_the_motors_sent_a_move_and_logs_where_they_are(self):
    second = struct.pack("<hd", 1, RECORDED_IDLE_TARGETS[1]) + bytes.fromhex(
      "b4a291808fb3b540814e1be8b40a8a40"
    )
    link = RaisingLink(answers=make_answers(), raises_on=(0, 22, second))
    link.answers[(0, 84, b"\x02\x00")] = [bytes.fromhex(Z_UP_ENCODER_REPLY)]
    driver = await self.set_up_arm(link=link)
    with self.assertLogs(
      "pylabrobot.formulatrix.mantis.driver.features.arm", level="WARNING"
    ) as logs:
      with self.assertRaisesRegex(OSError, "device unplugged"):
        await driver.arm._move_motors(targets=RECORDED_IDLE_TARGETS)
    self.assertEqual(
      [command for command in self.sent(link) if command[1] != 20][1:],
      [(0, 22, second)] + HALT[1:3] + [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)],
    )
    self.assertTrue(any("by their encoders" in line for line in logs.output))


class DiscoveryTests(ArmTestCase):
  async def test_built_only_when_board_11_answers(self):
    for absent_boards, built in (((), True), ((11,), False)):
      with self.subTest(absent_boards=absent_boards):
        driver = await self.set_up_driver(
          link=FakeLink(answers=make_answers(absent_boards=absent_boards))
        )
        self.assertEqual(driver.arm is not None, built)

  async def test_the_arm_block_is_never_taken_from_a_recording(self):
    path = os.path.join(self.directory, "recording.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, "arm": SYNTHETIC_ARM_BLOCK}, f)
    with mock.patch.object(master_module, "RECORDING_MANTIS_V4", path):
      driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert driver.arm is not None
    self.assertIsNone(driver.arm.encoder_correction)
    self.assertEqual(driver._unit_blocks["rfid"], RECORDING["rfid"])

  async def test_a_declared_arm_block_is_read_and_saved(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    self.assertEqual(driver.arm.homing_position_a, SYNTHETIC_HOMING_POSITION_A)
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "saved.json")
      driver.save_configuration(path=path)
      with open(path, encoding="utf-8") as f:
        self.assertEqual(json.load(f)["arm"], SYNTHETIC_ARM_BLOCK)

  async def test_a_correction_that_does_not_ascend_refuses_setup(self):
    block = {
      **SYNTHETIC_ARM_BLOCK,
      "encoder_correction": {"0": [[1, 2], [2, 1]], "1": SYNTHETIC_CORRECTION},
    }
    driver = self.make_driver(
      link=FakeLink(answers=make_answers()),
      declared_configuration_json=self.declare(arm_block=block),
    )
    with self.assertRaisesRegex(MantisConfigurationError, "measured ascending"):
      await driver.setup(skip_arm=True)


if __name__ == "__main__":
  unittest.main()
