"""Arm tests: the bring-up and the park against a Mantis v4's own startup and close, as recorded.

The recorded startup declared each arm where its unit's encoder correction put it; these tests
declare it with a synthetic correction, so only those values differ from what was recorded. The
offsets declared are the ones the recorded idle targets imply, so the moves are sent as recorded.
"""

import json
import math
import os
import struct
import tempfile
import unittest
from typing import Dict, List, Mapping, Optional, Tuple, Union
from unittest import mock

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver import master as master_module
from pylabrobot.formulatrix.mantis.driver.arm_kinematics import (
  compute_absolute_device_position,
  compute_device_position,
  compute_device_rate,
  compute_joint_angles,
  compute_position,
)
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisKinematicsError,
  MantisMotionError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features import arm as arm_module
from pylabrobot.formulatrix.mantis.driver.features.arm import (
  POST_HOMING_POSITION,
  Arm,
  MantisArmConfiguration,
)
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
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  RECORDED_MOTOR_ERROR_EVENTS,
  DriverTestCase,
  FakeLink,
  make_answers,
)
from pylabrobot.formulatrix.mantis.driver.oem_config_tests import (
  SYNTHETIC_APPROACH_PATHS_BLOCK,
  SYNTHETIC_IDLE_ROUTES,
  SYNTHETIC_SAFE_XY,
  SYNTHETIC_STAGE_BLOCK,
  SYNTHETIC_WASH_ROUTE,
  SYNTHETIC_WASTE_ROUTE,
  SYNTHETIC_ZONES_BLOCK,
)
from pylabrobot.formulatrix.mantis.resource_model import (
  PLATE_HOLDER_LOCATION,
  PLATE_HOLDER_SIZE_Y,
)
from pylabrobot.resources import Coordinate

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


# The synthetic stage's reference height, mm.
REFERENCE_HEIGHT = SYNTHETIC_STAGE_BLOCK["reference_height"]


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

  def declare(self, *, arm_block, **blocks) -> str:
    path = os.path.join(self.directory, f"declared_{len(os.listdir(self.directory))}.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, **({"arm": arm_block} if arm_block is not None else {}), **blocks}, f)
    return path

  async def set_up_arm(self, *, link: FakeLink, arm_block=SYNTHETIC_ARM_BLOCK, **blocks):
    driver = await self.set_up_driver(
      link=link, declared_configuration_json=self.declare(arm_block=arm_block, **blocks)
    )
    assert driver.arm is not None
    # Runs before the driver's stop: an arm a test brought up is not parked when it ends.
    self.addCleanup(setattr, driver.arm, "_initialized", False)
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
    driver.default_read_timeout = 0.25  # stop's read of Z's encoder is never answered
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
    driver.default_read_timeout = 0.25
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
    self.default_read_timeout = 0.25  # the red colour write is never answered
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
    driver.default_read_timeout = 0.25  # unpowering the arms is never answered
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
    driver.default_read_timeout = 0.25  # unpowering the arms is never answered
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
    joint_state = await driver.arm.request_joint_state()
    self.assertAlmostEqual(joint_state[0], -0.585047 * 1.8, places=5)
    self.assertAlmostEqual(joint_state[1], 1.76374 * 1.8, places=4)
    self.assertEqual(joint_state[2], 0.0)
    absolute = await driver.arm.request_absolute_joint_state()
    self.assertEqual(sorted(absolute), [0, 1])


class SetupDisplayTests(ArmTestCase):
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
    driver.default_read_timeout = 0.25  # stop's read of Z's encoder is never answered
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
    driver.default_read_timeout = 0.25  # stop's read of Z's encoder is never answered

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
    driver.default_read_timeout = 0.25  # stop's read of Z's encoder is never answered
    self.assertEqual(led_bar_frames(written=link.written), [START_IDLE_ANIMATION])


class IdleTargetTests(unittest.TestCase):
  def test_idle_targets_follow_the_units_offsets(self):
    for offsets in ({0: 0.0, 1: 0.0}, {0: 1.0, 1: -2.0}):
      with self.subTest(offsets=offsets):
        arm = Arm(
          driver=mock.Mock(),
          configuration=MantisArmConfiguration(
            offsets=offsets,
            homing_position_a=SYNTHETIC_HOMING_POSITION_A,
            encoder_correction={0: [], 1: []},
          ),
        )
        for motor, target in arm._get_idle_targets().items():
          expected = IDLE_ANGLES[motor] - offsets[motor]
          self.assertAlmostEqual(target, compute_device_position(motor=motor, position=expected))

  def test_the_implied_offsets_give_the_recorded_idle_targets(self):
    arm = Arm(
      driver=mock.Mock(),
      configuration=MantisArmConfiguration(
        offsets=SYNTHETIC_OFFSETS,
        homing_position_a=SYNTHETIC_HOMING_POSITION_A,
        encoder_correction={0: [], 1: []},
      ),
    )
    targets = arm._get_idle_targets()
    self.assertAlmostEqual(targets[0], RECORDED_IDLE_TARGETS[0], places=12)
    self.assertEqual(targets[1], RECORDED_IDLE_TARGETS[1])

  def test_without_the_arm_block_there_are_no_idle_targets(self):
    arm = Arm(driver=mock.Mock(), configuration=MantisArmConfiguration())
    with self.assertRaises(MantisConfigurationError):
      arm._get_idle_targets()


class DefaultTests(unittest.TestCase):
  def test_defaults_are_checked_when_assigned(self):
    arm = Arm(driver=mock.Mock(), configuration=MantisArmConfiguration())
    for name in ("default_xy_speed", "default_xy_acceleration", "default_z_speed"):
      with self.subTest(name=name):
        with self.assertRaises(ValueError):
          setattr(arm, name, 0.0)
    # The xy defaults are mm/s and mm/s², None the route profile.
    arm.default_xy_speed = 100.0
    arm.default_xy_speed = None
    self.assertIsNone(arm.default_xy_speed)
    # Z's speed and acceleration stay within the recorded moves'.
    for name, value in (("default_z_speed", 56.0), ("default_z_acceleration", 100.0)):
      with self.subTest(name=name):
        with self.assertRaises(ValueError):
          setattr(arm, name, value)
    arm.default_z_acceleration = 300.0
    self.assertEqual(arm.default_z_acceleration, 300.0)


def get_arm_block_at(*, x: float, y: float) -> Dict:
  """The synthetic arm block, its references putting the last recorded readings at (x, y) mm."""
  homing_position_a = {}
  for motor, angle in enumerate(compute_joint_angles(x=x, y=y)):
    _, reading = struct.unpack("<Hd", bytes.fromhex(STARTUP_ABSOLUTE_ENCODER_REPLIES[motor][-1]))
    position = compute_device_position(motor=motor, position=angle - SYNTHETIC_OFFSETS[motor])
    homing_position_a[str(motor)] = (reading - position) % 200.0
  return {**SYNTHETIC_ARM_BLOCK, "homing_position_a": homing_position_a}


def get_away_arm_block() -> Dict:
  """The synthetic arm block, its references putting the last recorded readings at (-57, 0) mm."""
  return get_arm_block_at(x=-57.0, y=0.0)


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
      # Where the controller holds each motor, read first: whether the head is on an approach path.
      link.answers[(0, 27, key)] = [bytes.fromhex(STARTUP_POSITION_REPLIES[motor][-1])]
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
    positions = [(0, 27, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    self.assertEqual(
      sent[4:25],
      statuses
      + positions
      + [(11, 30, struct.pack("<h", motor)) for motor in (0, 1)]
      # Z up on its own, read back by its encoder before the arms move.
      + [(0, 22, Z_UP_MOVE), (0, 20, b"\x02\x00")]
      + statuses
      + [(0, 84, b"\x02\x00")]
      + [(0, 61, b""), (0, 62, b"")]
      + [(0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[item])) for item in (14, 15)]
      + [(0, 61, b""), (0, 62, b""), (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[16]))],
    )
    self.assertEqual(sent[25][:2], (12, 33))
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
    driver.default_read_timeout = 0.25
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
        await driver.arm._move_motors(
          targets=RECORDED_IDLE_TARGETS, profiles=driver.arm._get_route_profiles()
        )
    self.assertEqual(
      [command for command in self.sent(link) if command[1] != 20][1:],
      [(0, 22, second)] + HALT[1:3] + [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)],
    )
    self.assertTrue(any("by their encoders" in line for line in logs.output))


class DiscoveryTests(ArmTestCase):
  async def test_built_only_when_board_11_answers(self):
    for absent_boards, built in (((), True), ((11,), False)):
      with self.subTest(absent_boards=absent_boards):
        self.default_read_timeout = 0.25 if absent_boards else 5.0  # an absent board never answers
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
    self.assertIsNone(driver.arm.configuration.encoder_correction)
    self.assertEqual(driver._unit_blocks["rfid"], RECORDING["rfid"])

  async def test_a_declared_arm_block_is_read_and_saved(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    self.assertEqual(driver.arm.configuration.homing_position_a, SYNTHETIC_HOMING_POSITION_A)
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


# A move's reply (command 22), and the profiles every recorded move sent: each arm motor's speed and
# acceleration, then Z's, in device units.
RECORDED_MOVE_REPLY = "0000"
ARM_PROFILE = bytes.fromhex("b4a291808fb3b540814e1be8b40a8a40")
# The same, as numbers: each arm motor's speed and acceleration in device units.
ARM_PROFILE_SPEED, ARM_PROFILE_ACCELERATION = struct.unpack("<dd", ARM_PROFILE)
Z_PROFILE = bytes.fromhex("4cd431fab7eaa040bb34be4f05c2be40")
# A Mantis v4's own move along its waste approach path, queued: its first three items, a line naming no axis,
# the ready position with Z held, and Z alone to the safe height.
RECORDED_WASTE_HOLD = (
  "710900000100000000000000000000000000000000000000000000000000000000000000000000000000000000"
  "000000000000000000000000000000000000000000000000000000000000000000"
)
RECORDED_WASTE_READY = (
  "720900000100f7ad61abb2dae2bfb4a291808fb3b540814e1be8b40a8a408ff05452040fd0bfb4a291808fb3b540"
  "814e1be8b40a8a40000000000000000000000000000000000000000000000000"
)
RECORDED_WASTE_SAFE_HEIGHT = (
  "730900000100000000000000000000000000000000000000000000000000000000000000000000000000000000"
  "000000000000000000fa7c3e9fcfe74ec04cd431fab7eaa040bb34be4f05c2be40"
)
# Z's encoder at the shutdown height, below a traverse height at its flag, then at its flag.
Z_RISING_ENCODER_REPLIES = [Z_DOWN_ENCODER_REPLY, Z_UP_ENCODER_REPLY]


def get_expected_move(*, motor: int, position: float) -> bytes:
  """A move's payload (command 22) at the recorded profile."""
  profile = Z_PROFILE if motor == 2 else ARM_PROFILE
  return struct.pack("<hd", motor, position) + profile


def get_arm_positions(*, x: float, y: float) -> Dict[int, float]:
  """Each arm motor's target for the head at (x, y), device units, with the synthetic offsets."""
  angles = compute_joint_angles(x=x, y=y)
  return {
    motor: compute_device_position(motor=motor, position=angles[motor] - SYNTHETIC_OFFSETS[motor])
    for motor in (0, 1)
  }


def get_route_moves(*, route) -> List[bytes]:
  """The moves (command 22) along a route, each motor a line names in motor order."""
  moves = []
  for x, y, z in route:
    if x is not None:
      moves += [
        get_expected_move(motor=motor, position=position)
        for motor, position in get_arm_positions(x=x, y=y).items()
      ]
    if z is not None:
      moves.append(
        get_expected_move(motor=2, position=compute_device_position(motor=2, position=z))
      )
  return moves


def get_queued_move(*, item: int, targets: Dict[int, float]) -> bytes:
  """A queued move's payload (command 68) at the recorded profiles; a motor not named is held."""
  payload = struct.pack("<hHH", item, False, True)
  for motor in (0, 1, 2):
    if motor in targets:
      payload += struct.pack("<d", targets[motor]) + (Z_PROFILE if motor == 2 else ARM_PROFILE)
    else:
      payload += bytes(24)
  return payload


class RouteLink(ArmLink):
  """As ``ArmLink``, and answers every move, queued move and sequencer command as recorded; the
  position reads (command 27) and encoder reads (command 84) by motor as given."""

  def __init__(
    self,
    *,
    positions: Optional[Mapping[int, Union[str, List[str]]]] = None,
    encoders: Optional[Mapping[int, Union[str, List[str]]]] = None,
    **kwargs,
  ):
    super().__init__(**kwargs)
    for command in (61, 62, 64):
      self.answers[(0, command, b"")] = [bytes.fromhex("0000")]
    for command, read in ((27, positions), (84, encoders)):
      for motor, replies in (read or {}).items():
        replies = [replies] if isinstance(replies, str) else replies
        self.answers[(0, command, struct.pack("<h", motor))] = [
          bytes.fromhex(reply) for reply in replies
        ]

  def set_absolute_readings(self, *, index: int) -> None:
    """Answer every absolute encoder read with the startup's reading at ``index``."""
    for motor in (0, 1):
      self.answers[(11, 30, struct.pack("<h", motor))] = [
        bytes.fromhex(STARTUP_ABSOLUTE_ENCODER_REPLIES[motor][index])
      ]

  async def write(self, data: bytes) -> int:
    _, board, command, payload = parse_frame(frame=data)
    if (board, command) == (0, 22):
      self.answers.setdefault((0, 22, payload), [bytes.fromhex(RECORDED_MOVE_REPLY)])
    if (board, command) == (0, 68):
      self.answers.setdefault((0, 68, payload), [bytes.fromhex(CLOSE_QUEUED_MOVE_REPLY)])
    return await super().write(data)


def tokens(commands: List[Tuple[int, int, bytes]], *, without=(20,)) -> List[str]:
  return [token(command) for command in commands if command[1] not in without]


class ZoneTests(ArmTestCase):
  async def set_up_routes(self, *, link: FakeLink, arm_block=SYNTHETIC_ARM_BLOCK, **blocks):
    blocks = {
      "zones": SYNTHETIC_ZONES_BLOCK,
      "approach_paths": SYNTHETIC_APPROACH_PATHS_BLOCK,
      **blocks,
    }
    return await self.set_up_arm(link=link, arm_block=arm_block, **blocks)

  async def test_from_the_front_zone_the_recorded_startup_is_sent(self):
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    link = ArmLink(
      answers=make_answers(), events={(0, 21, home): [bytes.fromhex(STARTUP_HOME_DONE)]}
    )
    driver = await self.set_up_routes(link=link)
    await driver.arm.initialize()
    self.assertEqual(tokens(self.sent(link)), get_expected_startup())

  async def test_from_another_zone_the_arms_follow_its_routes(self):
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    link = RouteLink(
      answers=make_answers(),
      events={(0, 21, home): [bytes.fromhex(STARTUP_HOME_DONE)]},
      positions=AWAY_POSITION_REPLIES,
    )
    driver = await self.set_up_routes(link=link)
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.features.arm", level="INFO") as logs:
      await driver.arm.initialize()
    self.assertTrue(any("in the LeftMiddle zone" in line for line in logs.output))
    sent = self.sent(link)
    moves = [payload for _, command, payload in sent if command == 22]
    safe_xy = [[*SYNTHETIC_SAFE_XY["LeftMiddle"], None]]
    idle = SYNTHETIC_IDLE_ROUTES["LeftMiddle"]
    self.assertEqual(moves, get_route_moves(route=safe_xy + idle + idle))
    # The route to safe XY, Z homed, the routes to idle, Z homed again.
    self.assertEqual([command for _, command, _ in sent if command in (21, 22)][2:3], [21])
    self.assertEqual([command for _, command, _ in sent][-1], 20)
    self.assertTrue(driver.arm.is_initialized)

  async def test_without_zones_a_head_away_from_idle_still_refuses(self):
    link = RouteLink(answers=make_answers(), positions=AWAY_POSITION_REPLIES)
    driver = await self.set_up_arm(link=link)
    driver.default_read_timeout = 0.25  # unpowering the arms is never answered
    with self.assertRaisesRegex(MantisMotionError, "moved to idle only from x"):
      await driver.arm.initialize()
    self.assertNotIn(22, [command for _, command, _ in self.sent(link)])

  async def test_recover_from_fault_stops_clears_and_recovers_as_recorded(self):
    """The stops and the clears (Z, then the arms) in the order recorded after a motor error,
    each stop at the default deceleration; the recovery after them as the startup's."""
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    link = RouteLink(
      answers=make_answers(), events={(0, 21, home): [bytes.fromhex(STARTUP_HOME_DONE)]}
    )
    for motor in (0, 1):  # the startup's readings after its first declaration
      link.answers[(11, 30, struct.pack("<h", motor))] = [
        bytes.fromhex(reply) for reply in STARTUP_ABSOLUTE_ENCODER_REPLIES[motor][4:]
      ]
    driver = await self.set_up_routes(link=link)
    driver.arm._initialized = True
    await driver.arm.recover_from_fault()
    startup = get_expected_startup()
    tail = startup[startup.index("0:17:0200") + 1 :]
    self.assertEqual(
      tokens(self.sent(link)),
      [token(command) for command in HALT[1:]] + ["0:17:0200", "0:17:0000", "0:17:0100"] + tail,
    )
    self.assertTrue(driver.arm.is_initialized)

  async def test_recover_from_fault_refuses_an_arm_not_brought_up(self):
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_routes(link=link)
    with self.assertRaisesRegex(MantisMotionError, "not brought up"):
      await driver.arm.recover_from_fault()
    self.assertEqual(self.sent(link), [])

  async def test_the_head_is_classified_from_its_forward_kinematics(self):
    driver = await self.set_up_routes(link=RouteLink(answers=make_answers()))
    arm = driver.arm
    for zone, (x, y) in SYNTHETIC_SAFE_XY.items():
      with self.subTest(zone=zone):
        if zone in ("Front", "Unknown"):
          continue
        angles = compute_joint_angles(x=x, y=y)
        joint_state = {motor: angles[motor] - SYNTHETIC_OFFSETS[motor] for motor in (0, 1)}
        _, route = arm._get_routes(joint_state=joint_state)
        self.assertEqual(route, [tuple(route_line) for route_line in SYNTHETIC_IDLE_ROUTES[zone]])
    self.assertEqual(arm._get_zone(x=15.0, y=20.0), "Unknown")


# A stage that only moves the wellplate frame by its origin, so where a point lands is worked by hand.
TRANSLATED_STAGE_BLOCK = {
  **SYNTHETIC_STAGE_BLOCK,
  "transformation_matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
  "offset_x": [0.0, 0.0, 0.0],
  "offset_y": [0.0, 0.0, 0.0],
}
# A deck point over the plate holder, 14.3 mm right of and 11.28 mm in front of its back-left
# corner, and where the translated stage puts it in the arm frame.
DECK_POINT = (
  PLATE_HOLDER_LOCATION.x + 14.3,
  PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - 11.28,
)
ARM_POINT = (14.3 - 50.0, 11.28 + 40.0)
# The arm motors' targets for DECK_POINT, and Z's position (mm down) for the nozzle at the
# reference height above the deck: the translated stage's origin z.
DECK_POINT_ARM_TARGETS = get_arm_positions(x=ARM_POINT[0], y=ARM_POINT[1])
REFERENCE_HEIGHT_Z = 51.0


class MoveTests(ArmTestCase):
  async def set_up_moves(self, *, link: FakeLink, **blocks):
    driver = await self.set_up_arm(link=link, **{"stage": TRANSLATED_STAGE_BLOCK, **blocks})
    driver.arm._initialized = True
    return driver

  def set_traverse_at_flag(self, *, driver) -> None:
    """The traverse height at Z's flag, where its encoder is recorded; the deck frame's conversion
    is tested on its own."""
    driver.arm._get_traverse_z_joint_position = (  # type: ignore[method-assign]
      lambda **_: 0.0
    )

  async def test_move_to_xy_position_moves_the_arms_in_one_queued_move_z_held(self):
    queued = get_queued_move(item=15, targets=DECK_POINT_ARM_TARGETS)
    link = RouteLink(
      answers=make_answers(),
      encoders={2: Z_UP_ENCODER_REPLY},
      events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]},
    )
    driver = await self.set_up_moves(link=link)
    driver._next_item = 15
    self.set_traverse_at_flag(driver=driver)
    await driver.arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1])
    # Where it stands, then Z by its encoder at the traverse height already: no rise.
    self.assertEqual(
      tokens(self.sent(link)),
      [
        "0:27:0000",
        "0:27:0100",
        "0:27:0200",
        "0:84:0200",
        "0:61:",
        "0:62:",
        f"0:68:{queued.hex()}",
      ],
    )

  async def test_a_move_across_from_below_the_traverse_height_rises_first(self):
    queued = get_queued_move(item=15, targets=DECK_POINT_ARM_TARGETS)
    link = RouteLink(
      answers=make_answers(),
      encoders={2: Z_RISING_ENCODER_REPLIES},
      events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]},
    )
    driver = await self.set_up_moves(link=link)
    driver._next_item = 15
    self.set_traverse_at_flag(driver=driver)
    await driver.arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1])
    # Z read down, up alone to the traverse height and read back there before the arms move.
    self.assertEqual(
      tokens(self.sent(link)),
      ["0:27:0000", "0:27:0100", "0:27:0200", "0:84:0200", f"0:22:{Z_UP_MOVE.hex()}"]
      + ["0:84:0200", "0:61:", "0:62:", f"0:68:{queued.hex()}"],
    )

  async def test_move_to_z_position_moves_z_alone_to_the_height_above_the_deck(self):
    target = compute_device_position(motor=2, position=REFERENCE_HEIGHT_Z)
    queued = get_queued_move(item=15, targets={2: target})
    link = RouteLink(
      answers=make_answers(), events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]}
    )
    driver = await self.set_up_moves(link=link)
    driver._next_item = 15
    await driver.arm.move_to_z_position(z=REFERENCE_HEIGHT)
    self.assertEqual(
      tokens(self.sent(link)),
      ["0:27:0000", "0:27:0100", "0:27:0200", "0:61:", "0:62:", f"0:68:{queued.hex()}"],
    )

  async def test_moves_out_of_range_are_refused_before_anything_is_sent(self):
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_moves(link=link)
    arm = driver.arm
    with self.assertRaisesRegex(ValueError, "Z speed must be from 1.0 to 55.0"):
      await arm.move_to_z_position(z=REFERENCE_HEIGHT, speed=60.0)
    with self.assertRaisesRegex(ValueError, "Z acceleration must be from 200.0 to 500.0"):
      await arm.move_to_z_position(z=REFERENCE_HEIGHT, acceleration=1000.0)
    with self.assertRaisesRegex(ValueError, "acceleration must be finite and above 0"):
      await arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1], acceleration=0.0)
    with self.assertRaises(MantisKinematicsError):
      await arm.move_to_xy_position(x=500.0, y=500.0)
    with self.assertRaisesRegex(ValueError, "the motors are"):
      await arm.move_to_joint_state(joint_state={3: 0.0})
    with self.assertRaisesRegex(ValueError, "outside its limits"):
      await arm.move_to_joint_state(joint_state={0: 8000.0})
    self.assertEqual(self.sent(link), [])

  async def test_a_height_outside_zs_limits_moves_nothing(self):
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_moves(link=link)
    with self.assertRaisesRegex(ValueError, "outside its limits"):
      await driver.arm.move_to_z_position(z=-100.0)
    self.assertEqual(tokens(self.sent(link)), ["0:27:0000", "0:27:0100", "0:27:0200"])

  async def test_moves_refuse_an_arm_not_brought_up(self):
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_arm(link=link, stage=TRANSLATED_STAGE_BLOCK)
    with self.assertRaisesRegex(MantisMotionError, "not brought up"):
      await driver.arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1])
    with self.assertRaisesRegex(MantisMotionError, "not brought up"):
      await driver.arm.move_to_z_position(z=REFERENCE_HEIGHT)
    with self.assertRaisesRegex(MantisMotionError, "not brought up"):
      await driver.arm.park()
    self.assertEqual(self.sent(link), [])

  async def test_without_the_stage_block_the_deck_frame_moves_send_nothing(self):
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    driver.arm._initialized = True
    with self.assertRaisesRegex(MantisConfigurationError, "no stage transform"):
      await driver.arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1])
    with self.assertRaisesRegex(MantisConfigurationError, "no stage transform"):
      await driver.arm.move_to_z_position(z=REFERENCE_HEIGHT)
    with self.assertRaisesRegex(MantisConfigurationError, "no stage transform"):
      await driver.arm.request_pose()
    self.assertEqual(self.sent(link), [])

  async def test_a_joint_move_holds_the_motors_it_does_not_name(self):
    held_z_up = bytes.fromhex(CLOSE_QUEUED_MOVES[13])  # the arms held, Z to 0
    queued = struct.pack("<h", 15) + held_z_up[2:]
    link = RouteLink(
      answers=make_answers(), events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]}
    )
    driver = await self.set_up_moves(link=link)
    driver._next_item = 15
    await driver.arm.move_to_joint_state(joint_state={2: 0.0})
    # Where it stands is read first (on an approach path, it would leave along it).
    self.assertEqual(
      tokens(self.sent(link)),
      ["0:27:0000", "0:27:0100", "0:27:0200", "0:61:", "0:62:", f"0:68:{queued.hex()}"],
    )

  async def test_a_queued_move_not_played_in_time_aborts_the_sequencer(self):
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_moves(link=link)
    with mock.patch.object(arm_module, "MOVE_TIMEOUT", 0.05):
      with self.assertRaises(MantisTimeoutError):
        await driver.arm.move_to_joint_state(joint_state={2: 0.0})
    commands = [command for _, command, _ in self.sent(link)]
    self.assertEqual(commands[:7], [27, 27, 27, 61, 62, 68, 64])
    self.assertEqual(commands[7:], [84, 84, 84])  # where the motors stopped, logged

  async def test_move_to_location_rises_moves_across_then_lowers(self):
    arms = get_queued_move(item=15, targets=DECK_POINT_ARM_TARGETS)
    z = get_queued_move(
      item=16, targets={2: compute_device_position(motor=2, position=REFERENCE_HEIGHT_Z)}
    )
    link = RouteLink(
      answers=make_answers(),
      encoders={2: Z_RISING_ENCODER_REPLIES},
      events={
        (0, 68, arms): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])],
        (0, 68, z): [bytes.fromhex(CLOSE_ITEM_PLAYED[16])],
      },
    )
    driver = await self.set_up_moves(link=link)
    driver._next_item = 15
    self.set_traverse_at_flag(driver=driver)
    await driver.arm.move_to_location(location=Coordinate(*DECK_POINT, REFERENCE_HEIGHT))
    self.assertEqual(
      tokens(self.sent(link)),
      ["0:27:0000", "0:27:0100", "0:27:0200", "0:84:0200", f"0:22:{Z_UP_MOVE.hex()}"]
      + ["0:84:0200", "0:61:", "0:62:", f"0:68:{arms.hex()}", "0:61:", "0:62:", f"0:68:{z.hex()}"],
    )


def make_staged_arm(*, stage: Dict, **kwargs) -> Arm:
  """An arm with a mocked driver and a stage block, as a driver builds it."""
  return Arm(
    driver=mock.Mock(),
    configuration=MantisArmConfiguration(
      offsets=kwargs.get("offsets"),
      homing_position_a=kwargs.get("homing_position_a"),
      encoder_correction=kwargs.get("encoder_correction"),
      stage_origin=tuple(stage["origin"]),
      stage_transformation_matrix=stage["transformation_matrix"],
      stage_offset_x=tuple(stage["offset_x"]),
      stage_offset_y=tuple(stage["offset_y"]),
      arm_type_rotation_degree=stage["arm_type_rotation_degree"],
      arm_type_translation=tuple(stage["arm_type_translation"]),
      stage_reference_height=stage["reference_height"],
    ),
  )


class DeckFrameTests(unittest.IsolatedAsyncioTestCase):
  def test_off_the_plate_holder_a_rigid_stage_is_fitted_exactly(self):
    # A rigid stage turns and moves the wellplate frame; the resource model only moves it, so the
    # fit is that turn, and a point anywhere on the deck lands where the stage would put it.
    stage = {
      **TRANSLATED_STAGE_BLOCK,
      "arm_type_rotation_degree": 1.5,
      "arm_type_translation": [2.0, -3.0, 0.0],
    }
    arm = make_staged_arm(stage=stage)
    turn, _, _ = arm._get_deck_to_arm_transform()
    self.assertAlmostEqual(math.degrees(turn), 1.5, places=9)
    angle = math.radians(1.5)
    for x, y in ((0.0, 0.0), (180.0, 150.0), (-40.0, 120.0)):
      with self.subTest(x=x, y=y):
        # The point in the wellplate frame, then turned and moved as the stage does.
        holder_x = x - PLATE_HOLDER_LOCATION.x
        holder_y = PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - y
        expected = (
          holder_x * math.cos(angle) - holder_y * math.sin(angle) + 2.0 - 50.0,
          holder_x * math.sin(angle) + holder_y * math.cos(angle) - 3.0 + 40.0,
        )
        arm_x, arm_y = arm._get_arm_frame_xy(x=x, y=y)
        self.assertAlmostEqual(arm_x, expected[0], places=9)
        self.assertAlmostEqual(arm_y, expected[1], places=9)
        deck_x, deck_y = arm._get_deck_frame_xy(x=arm_x, y=arm_y)
        self.assertAlmostEqual(deck_x, x, places=9)
        self.assertAlmostEqual(deck_y, y, places=9)

  def test_over_the_plate_holder_a_point_reads_back_where_it_was_sent(self):
    # The projective stage is inverted exactly there, not through the fit.
    arm = make_staged_arm(stage=SYNTHETIC_STAGE_BLOCK)
    for holder_x, holder_y in ((0.0, 0.0), (14.3, 11.28), (127.0, 85.0), (64.0, 40.0)):
      x = PLATE_HOLDER_LOCATION.x + holder_x
      y = PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - holder_y
      with self.subTest(x=x, y=y):
        arm_x, arm_y = arm._get_arm_frame_xy(x=x, y=y)
        deck_x, deck_y = arm._get_deck_frame_xy(x=arm_x, y=arm_y)
        self.assertAlmostEqual(deck_x, x, places=9)
        self.assertAlmostEqual(deck_y, y, places=9)

  def test_an_xy_speed_scales_each_motor_by_its_turn(self):
    # 100 mm/s and 500 mm/s² over the straight line: each motor at its turn over the distance, so
    # both end together; the motor that does not turn keeps the route profile.
    arm = Arm(
      driver=mock.Mock(),
      configuration=MantisArmConfiguration(
        offsets={0: 0.0, 1: 0.0},
        homing_position_a=SYNTHETIC_HOMING_POSITION_A,
        encoder_correction={0: [], 1: []},
      ),
    )
    start_angles = compute_joint_angles(x=15.0, y=31.177)
    target_angles = compute_joint_angles(x=-20.0, y=70.0)
    start = {0: start_angles[0], 1: start_angles[1], 2: 0.0}
    target = {0: target_angles[0], 1: target_angles[1]}
    distance = math.hypot(-20.0 - 15.0, 70.0 - 31.177)
    profiles = arm._get_arm_profiles(
      start=start, target=target, xy_speed=100.0, xy_acceleration=500.0
    )
    for motor in (0, 1):
      turn = abs(target[motor] - start[motor])
      with self.subTest(motor=motor):
        expected = (
          compute_device_rate(motor=motor, rate=100.0 * turn / distance),
          compute_device_rate(motor=motor, rate=500.0 * turn / distance),
        )
        for value, expected_value in zip(profiles[motor], expected):
          self.assertAlmostEqual(value, expected_value, places=9)
    route = arm._get_arm_profiles(start=None, target=target, xy_speed=None, xy_acceleration=None)
    self.assertEqual(route[0], (ARM_PROFILE_SPEED, ARM_PROFILE_ACCELERATION))

  def test_over_the_plate_holder_the_stage_transform_places_the_nozzle(self):
    # The holder's back-left corner is the wellplate frame's origin.
    arm = make_staged_arm(stage=SYNTHETIC_STAGE_BLOCK)
    corner = arm._get_arm_frame_xy(
      x=PLATE_HOLDER_LOCATION.x, y=PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y
    )
    self.assertAlmostEqual(
      corner[0], (-50.0 * 1.001 + 40.0 * 0.001 - 0.08) / (-50.0 * -2e-05 + 40.0 * -3e-05 + 1.004)
    )
    self.assertAlmostEqual(
      corner[1], (-50.0 * -0.004 + 40.0 * 0.9995 + 0.09) / (-50.0 * -2e-05 + 40.0 * -3e-05 + 1.004)
    )

  def test_a_height_above_the_deck_takes_the_reference_height_and_the_tilt(self):
    # 10 mm right of and 20 mm in front of the holder's back-left corner, 5 mm above the deck:
    # 51 + (9.665 - 5) - 0.0015 * 10 + 0.0025 * 20 = 55.7 mm down.
    arm = make_staged_arm(stage=SYNTHETIC_STAGE_BLOCK)
    z = arm._get_z_joint_position(
      x=PLATE_HOLDER_LOCATION.x + 10.0,
      y=PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - 20.0,
      z=5.0,
    )
    self.assertAlmostEqual(z, 55.7)

  async def test_request_pose_places_the_nozzle_in_the_deck_frame(self):
    arm = make_staged_arm(
      stage=TRANSLATED_STAGE_BLOCK,
      offsets=SYNTHETIC_OFFSETS,
      homing_position_a=SYNTHETIC_HOMING_POSITION_A,
      encoder_correction={motor: SYNTHETIC_CORRECTION for motor in (0, 1)},
    )
    joints = {
      **{motor: IDLE_ANGLES[motor] - SYNTHETIC_OFFSETS[motor] for motor in (0, 1)},
      2: 10.0,
    }
    with mock.patch.object(arm, "request_joint_state", mock.AsyncMock(return_value=joints)):
      pose = await arm.request_pose()
    # The translated stage puts the wellplate frame's origin at (-50, 40) in the arm frame.
    x, y = POST_HOMING_POSITION
    self.assertAlmostEqual(pose.nozzle_location.x, PLATE_HOLDER_LOCATION.x + x + 50.0)
    self.assertAlmostEqual(
      pose.nozzle_location.y, PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - (y - 40.0)
    )
    # Z 10 mm down from its flag: 51 + 9.665 - 10 above the deck.
    self.assertAlmostEqual(pose.nozzle_location.z, 50.665)
    self.assertEqual(
      (pose.head_joint_location.x, pose.head_joint_location.y),
      (pose.nozzle_location.x, pose.nozzle_location.y),
    )
    self.assertEqual(pose.joints, joints)


# Each arm motor's encoder (command 84) as it stands at the recorded idle targets, in device units.
IDLE_ENCODER_REPLIES = {
  motor: struct.pack("<d", RECORDED_IDLE_TARGETS[motor]).hex() for motor in (0, 1)
}


class PoseTests(ArmTestCase):
  async def set_up_pose(self, *, link: FakeLink):
    driver = await self.set_up_arm(link=link)
    driver.arm._initialized = True
    driver.arm._pose_observer = mock.Mock()
    driver._next_item = 15
    return driver

  @staticmethod
  def make_z_up_link() -> RouteLink:
    """A link that plays Z's queued move to its flag, the arms held, as item 15."""
    queued = struct.pack("<h", 15) + bytes.fromhex(CLOSE_QUEUED_MOVES[13])[2:]
    return RouteLink(
      answers=make_answers(),
      events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]},
      encoders={**IDLE_ENCODER_REPLIES, 2: Z_DOWN_ENCODER_REPLY},
    )

  async def test_a_move_passes_on_the_pose_its_encoders_read_after_it(self):
    link = self.make_z_up_link()
    driver = await self.set_up_pose(link=link)
    await driver.arm.move_to_joint_state(joint_state={2: 0.0})
    sent = [command for command in self.sent(link) if command[1] not in (20, 27)]
    self.assertEqual(sent[3:], [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)])
    pose = driver.arm._pose_observer.call_args.kwargs
    self.assertAlmostEqual(pose["theta_1"], IDLE_ANGLES[0])
    self.assertAlmostEqual(pose["theta_2"], IDLE_ANGLES[1])
    self.assertAlmostEqual(pose["z"], -19.996, places=3)

  async def test_a_failed_move_passes_on_where_it_stopped_with_no_more_reads(self):
    second = struct.pack("<hd", 1, RECORDED_IDLE_TARGETS[1]) + ARM_PROFILE
    link = RaisingLink(answers=make_answers(), raises_on=(0, 22, second))
    for motor, reply in {**IDLE_ENCODER_REPLIES, 2: Z_UP_ENCODER_REPLY}.items():
      link.answers[(0, 84, struct.pack("<h", motor))] = [bytes.fromhex(reply)]
    driver = await self.set_up_pose(link=link)
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.features.arm", level="WARNING"):
      with self.assertRaisesRegex(OSError, "device unplugged"):
        await driver.arm._move_motors(
          targets=RECORDED_IDLE_TARGETS, profiles=driver.arm._get_route_profiles()
        )
    self.assertEqual([command for _, command, _ in self.sent(link)].count(84), 3)
    driver.arm._pose_observer.assert_called_once()

  async def test_a_failed_model_update_only_warns(self):
    link = self.make_z_up_link()
    driver = await self.set_up_pose(link=link)
    driver.arm._pose_observer.side_effect = RuntimeError("model")
    with self.assertLogs(
      "pylabrobot.formulatrix.mantis.driver.features.arm", level="WARNING"
    ) as logs:
      await driver.arm.move_to_joint_state(joint_state={2: 0.0})
    self.assertTrue(any("pose was not passed on" in line for line in logs.output))


class ParkTests(ArmTestCase):
  async def set_up_park(self, *, link: RouteLink, zones=True, traverse_at_flag=True):
    """An arm away from idle; the traverse height at Z's flag, where its encoder is recorded."""
    blocks = {"zones": SYNTHETIC_ZONES_BLOCK} if zones else {}
    link.set_absolute_readings(index=-1)
    driver = await self.set_up_arm(link=link, arm_block=get_away_arm_block(), **blocks)
    driver.arm._initialized = True
    if traverse_at_flag:
      # The traverse height at Z's flag, where its encoder is recorded; the deck frame's conversion
      # is tested on its own.
      driver.arm._get_traverse_z_joint_position = (  # type: ignore[method-assign]
        lambda **_: 0.0
      )
    return driver

  async def test_park_follows_the_zones_route_to_idle(self):
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_park(link=link)
    await driver.arm.park()
    sent = self.sent(link)
    self.assertEqual(
      [(board, command) for board, command, _ in sent[:9]],
      [(0, 27)] * 3 + [(0, 20), (0, 20), (0, 20), (11, 30), (11, 30), (0, 84)],
    )
    moves = [payload for _, command, payload in sent if command == 22]
    self.assertEqual(moves, get_route_moves(route=SYNTHETIC_IDLE_ROUTES["LeftMiddle"]))

  async def test_a_head_below_the_traverse_height_rises_first(self):
    link = RouteLink(answers=make_answers(), encoders={2: Z_RISING_ENCODER_REPLIES})
    driver = await self.set_up_park(link=link)
    await driver.arm.park()
    sent = self.sent(link)
    moves = [payload for _, command, payload in sent if command == 22]
    self.assertEqual(moves[0], Z_UP_MOVE)
    self.assertEqual(moves[1:], get_route_moves(route=SYNTHETIC_IDLE_ROUTES["LeftMiddle"]))
    rise = sent.index((0, 22, moves[0]))
    self.assertEqual([command for _, command, _ in sent[rise:] if command != 20][:2], [22, 84])

  async def test_a_rise_that_does_not_reach_the_traverse_height_is_not_parked(self):
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_park(link=link, traverse_at_flag=False)
    with self.assertRaisesRegex(MantisMotionError, "the arms are not moved"):
      await driver.arm.park()
    self.assertEqual([command for _, command, _ in self.sent(link)].count(22), 1)

  async def test_routes_keep_the_recorded_profile_whatever_the_defaults(self):
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_park(link=link)
    driver.arm.default_xy_speed = 100.0
    driver.arm.default_z_speed = 30.0
    await driver.arm.park()
    moves = [payload for _, command, payload in self.sent(link) if command == 22]
    self.assertEqual(moves, get_route_moves(route=SYNTHETIC_IDLE_ROUTES["LeftMiddle"]))

  async def test_without_zones_a_head_away_from_idle_is_not_parked(self):
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_park(link=link, zones=False)
    with self.assertRaisesRegex(MantisMotionError, "moved to idle only from x"):
      await driver.arm.park()
    self.assertNotIn(22, [command for _, command, _ in self.sent(link)])

  async def test_stop_routes_to_idle_before_the_recorded_close(self):
    link = RouteLink(
      answers=make_answers(),
      encoders={2: Z_UP_ENCODER_REPLY},
      events={
        (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[item])): [bytes.fromhex(CLOSE_ITEM_PLAYED[item])]
        for item in (15, 16)
      },
    )
    driver = await self.set_up_park(link=link)
    driver._next_item = 14  # numbered as in the recorded close
    await driver.stop()
    commands = [command for _, command, _ in self.sent(link) if command in (22, 61, 62, 68, 84)]
    route = get_route_moves(route=SYNTHETIC_IDLE_ROUTES["LeftMiddle"])
    # Z up on its own and read back by its encoder before the recorded close's queued moves.
    self.assertEqual(commands, [84] + [22] * len(route) + [22, 84, 61, 62, 68, 68, 61, 62, 68])

  async def test_an_idle_target_outside_its_limits_moves_nothing(self):
    # An offset 7300 degrees larger puts motor 0's idle target below its limit, -4000.
    offsets = {str(motor): offset for motor, offset in SYNTHETIC_OFFSETS.items()}
    offsets["0"] += 7300.0
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_arm(link=link, arm_block={**SYNTHETIC_ARM_BLOCK, "offsets": offsets})
    driver.arm._initialized = True
    with self.assertRaisesRegex(ValueError, "motor 0's target -40[0-9.]+ is outside its limits"):
      await driver.arm._park()
    self.assertEqual(self.sent(link), [])


class ApproachPathTests(ArmTestCase):
  async def set_up_approach_paths(self, *, link: RouteLink, at_idle=True, **blocks):
    link.set_absolute_readings(index=-1)
    link.answers[(0, 84, b"\x02\x00")] = [bytes.fromhex(Z_UP_ENCODER_REPLY)]
    blocks = {"approach_paths": SYNTHETIC_APPROACH_PATHS_BLOCK, **blocks}
    arm_block = get_arm_block_at(x=15.0, y=31.177) if at_idle else get_away_arm_block()
    driver = await self.set_up_arm(link=link, arm_block=arm_block, **blocks)
    driver.arm._initialized = True
    return driver

  def get_expected_waste_moves(self, *, first_item: int) -> List[bytes]:
    moves = []
    for item, (x, y, z) in enumerate(SYNTHETIC_WASTE_ROUTE, start=first_item):
      targets: Dict[int, float] = {}
      if x is not None and y is not None:
        targets = get_arm_positions(x=x, y=y)
      if z is not None:
        targets[2] = compute_device_position(motor=2, position=z)
      moves.append(get_queued_move(item=item, targets=targets))
    return moves

  async def test_from_idle_the_waste_path_is_queued_line_by_line(self):
    first_item = 16 - len(SYNTHETIC_WASTE_ROUTE) + 1
    expected = self.get_expected_waste_moves(first_item=first_item)
    link = RouteLink(
      answers=make_answers(), events={(0, 68, expected[-1]): [bytes.fromhex(CLOSE_ITEM_PLAYED[16])]}
    )
    driver = await self.set_up_approach_paths(link=link)
    driver._next_item = first_item
    await driver.arm.move_to_waste()
    queued = [payload for _, command, payload in self.sent(link) if command == 68]
    self.assertEqual(queued, expected)
    # The line naming no axis and Z alone at the safe height, queued as recorded.
    self.assertEqual(queued[0][2:], bytes.fromhex(RECORDED_WASTE_HOLD)[2:])
    self.assertEqual(queued[2][2:], bytes.fromhex(RECORDED_WASTE_SAFE_HEIGHT)[2:])
    # The ready position, within what the synthetic offsets give.
    recorded = struct.unpack("<hHH9d", bytes.fromhex(RECORDED_WASTE_READY))
    sent = struct.unpack("<hHH9d", queued[1])
    self.assertEqual(sent[1:3] + sent[4:6] + sent[7:], recorded[1:3] + recorded[4:6] + recorded[7:])
    for index in (3, 6):
      self.assertAlmostEqual(sent[index], recorded[index], places=9)

  async def test_without_an_approach_path_nothing_moves(self):
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_approach_paths(
      link=link, approach_paths={"wash_1": SYNTHETIC_WASH_ROUTE}
    )
    with self.assertRaisesRegex(MantisConfigurationError, "no approach path to the waste"):
      await driver.arm.move_to_waste()
    with self.assertRaisesRegex(MantisConfigurationError, "no approach path to the wash_2"):
      await driver.arm.move_to_wash(position=2)
    with self.assertRaisesRegex(ValueError, "position must be one of"):
      await driver.arm.move_to_wash(position=3)
    self.assertEqual(self.sent(link), [])

  async def test_leaving_the_waste_plays_its_path_backwards(self):
    # From the waste's last line back to its start, line by line: a line naming the arms and Z
    # moves both in one item; a line naming nothing is skipped.
    way_out = [
      (-60.0, 10.0, 30.0),
      (None, None, 30.0),
      (-50.0, 20.0, -1.57),
      (None, None, -1.57),
      (15.0, 30.0, -1.57),
      (None, None, -1.57),
      (15.0, 30.0, None),
    ]
    first_item = 16 - len(way_out) + 1
    expected = []
    for item, (x, y, z) in enumerate(way_out, start=first_item):
      targets = get_arm_positions(x=x, y=y) if x is not None and y is not None else {}
      if z is not None:
        targets[2] = compute_device_position(motor=2, position=z)
      expected.append(get_queued_move(item=item, targets=targets))
    link = RouteLink(
      answers=make_answers(), events={(0, 68, expected[-1]): [bytes.fromhex(CLOSE_ITEM_PLAYED[16])]}
    )
    driver = await self.set_up_approach_paths(link=link)
    driver._next_item = first_item
    await driver.arm._leave_approach_path(
      name="waste", index=len(SYNTHETIC_WASTE_ROUTE) - 1, until=0
    )
    self.assertEqual(
      [payload for _, command, payload in self.sent(link) if command == 68], expected
    )

  def test_the_head_is_placed_on_a_path_by_where_it_stands(self):
    arm = Arm(
      driver=mock.Mock(),
      configuration=MantisArmConfiguration(
        offsets={0: 0.0, 1: 0.0},
        homing_position_a=SYNTHETIC_HOMING_POSITION_A,
        encoder_correction={0: [], 1: []},
        approach_paths={
          name: [(line[0], line[1], line[2]) for line in path]
          for name, path in SYNTHETIC_APPROACH_PATHS_BLOCK.items()
        },
      ),
    )

    def joints_at(x: float, y: float, z: float) -> Dict[int, float]:
      angles = compute_joint_angles(x=x, y=y)
      return {0: angles[0], 1: angles[1], 2: z}

    # At the waste's bottom, at its last line; at idle, or level with a route line above the safe
    # height, in the open.
    self.assertEqual(arm._get_place(joint_state=joints_at(-60.0, 10.0, 35.0)), ("waste", 8))
    self.assertIsNone(arm._get_place(joint_state=joints_at(15.0, 31.177, 0.0)))
    self.assertIsNone(arm._get_place(joint_state=joints_at(-50.0, 20.0, -1.57)))
    # Off a route line by more than the tolerance, in the open too.
    self.assertIsNone(arm._get_place(joint_state=joints_at(-60.0, 11.0, 35.0)))


class StageDiscoveryTests(ArmTestCase):
  async def test_the_stage_block_is_never_taken_from_a_recording(self):
    path = os.path.join(self.directory, "recording.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, "stage": SYNTHETIC_STAGE_BLOCK}, f)
    with mock.patch.object(master_module, "RECORDING_MANTIS_V4", path):
      driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert driver.arm is not None
    self.assertIsNone(driver.arm.configuration.stage_origin)

  async def test_a_declared_stage_block_is_read_and_saved(self):
    driver = await self.set_up_arm(
      link=FakeLink(answers=make_answers()), stage=SYNTHETIC_STAGE_BLOCK
    )
    self.assertEqual(driver.arm.configuration.stage_origin, (-50.0, 40.0, 51.0))
    self.assertEqual(
      driver.arm.configuration.stage_transformation_matrix[2], (-2e-05, -3e-05, 1.004)
    )
    path = os.path.join(self.directory, "saved.json")
    driver.save_configuration(path=path)
    with open(path, encoding="utf-8") as f:
      self.assertEqual(json.load(f)["stage"], SYNTHETIC_STAGE_BLOCK)

  async def test_a_malformed_stage_block_refuses_setup(self):
    for block in (
      {**SYNTHETIC_STAGE_BLOCK, "origin": [1.0, 2.0]},
      {**SYNTHETIC_STAGE_BLOCK, "transformation_matrix": [[1.0, 0.0, 0.0]]},
      {key: value for key, value in SYNTHETIC_STAGE_BLOCK.items() if key != "offset_y"},
    ):
      with self.subTest(block=sorted(block)):
        driver = self.make_driver(
          link=FakeLink(answers=make_answers()),
          declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK, stage=block),
        )
        with self.assertRaises(MantisConfigurationError):
          await driver.setup(skip_arm=True)


class RouteDiscoveryTests(ArmTestCase):
  async def test_routes_are_never_taken_from_a_recording(self):
    path = os.path.join(self.directory, "recording.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, "zones": SYNTHETIC_ZONES_BLOCK}, f)
    with mock.patch.object(master_module, "RECORDING_MANTIS_V4", path):
      driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert driver.arm is not None
    self.assertIsNone(driver.arm.configuration.zone_rectangles)

  async def test_declared_routes_are_read_and_saved(self):
    driver = await self.set_up_arm(
      link=FakeLink(answers=make_answers()),
      zones=SYNTHETIC_ZONES_BLOCK,
      approach_paths=SYNTHETIC_APPROACH_PATHS_BLOCK,
    )
    self.assertEqual(driver.arm.configuration.approach_paths["wash_1"][0], (None, None, -1.57))
    path = os.path.join(self.directory, "saved.json")
    driver.save_configuration(path=path)
    with open(path, encoding="utf-8") as f:
      saved = json.load(f)
    self.assertEqual(saved["zones"], SYNTHETIC_ZONES_BLOCK)
    self.assertEqual(saved["approach_paths"], SYNTHETIC_APPROACH_PATHS_BLOCK)

  async def test_a_malformed_route_block_refuses_setup(self):
    for blocks in (
      {"zones": {**SYNTHETIC_ZONES_BLOCK, "rectangles": {"Front": [0, 1, 0, 1]}}},
      {"zones": {**SYNTHETIC_ZONES_BLOCK, "safe_xy_routes": SYNTHETIC_IDLE_ROUTES}},
      {"approach_paths": {"wash_3": SYNTHETIC_WASTE_ROUTE}},
    ):
      with self.subTest(blocks=list(blocks)):
        driver = self.make_driver(
          link=FakeLink(answers=make_answers()),
          declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK, **blocks),
        )
        with self.assertRaises(MantisConfigurationError):
          await driver.setup(skip_arm=True)


if __name__ == "__main__":
  unittest.main()
