"""V3 arm initialization against recorded device replies, with synthetic home references."""

import asyncio
import json
import os
import struct
import tempfile
from typing import Dict, List, Tuple
from unittest import mock

from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError, MantisMotionError
from pylabrobot.formulatrix.mantis.driver.features import arm_v3 as arm_v3_module
from pylabrobot.formulatrix.mantis.driver.features.arm_v3 import V3Arm
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestCase, FakeLink

SYNTHETIC_V3_ARM_BLOCK = {
  "offsets": {"0": 0.0, "1": 0.0},
  "home_positions": {"0": -90.0, "1": 216.0},
  "motors": {
    "0": {
      "method": 3,
      "positive_edge": True,
      "positive_direction": False,
      "speed": 100.0,
      "acceleration": 2500.0,
    },
    "1": {
      "method": 3,
      "positive_edge": True,
      "positive_direction": True,
      "speed": 100.0,
      "acceleration": 2500.0,
    },
    "2": {
      "method": 0,
      "positive_edge": True,
      "positive_direction": False,
      "speed": 15.0,
      "acceleration": 400.0,
    },
  },
  "retract": -50.0,
  "backoff": 20.0,
  "jog_speed": 10000.0,
  "jog_acceleration": 1500.0,
  "motor_delay": 0.2,
  "post_home_positions": [[-38.403, 11.677], [-45.77, 47.82], [15.0, 31.177]],
}
# Replies from a complete v3 setup and stop. Repeated status words are stored as (word, count).
RECORDED_STATUS_RUNS = {
  0: [
    ("0000", 1),
    ("0100", 3),
    ("0001", 1),
    ("0100", 2),
    ("0001", 7),
    ("0100", 5),
    ("0000", 2),
    ("0100", 8),
    ("0000", 1),
    ("0100", 1),
    ("0101", 1),
    ("0001", 2),
    ("0100", 5),
    ("0000", 1),
    ("0300", 103),
    ("0400", 28),
    ("0500", 13),
    ("0400", 1),
    ("0500", 1),
    ("0400", 28),
    ("0500", 14),
    ("0400", 1),
    ("0500", 9),
    ("0400", 14),
  ],
  1: [
    ("0000", 1),
    ("0100", 3),
    ("0001", 1),
    ("0100", 8),
    ("0000", 1),
    ("0100", 6),
    ("0000", 1),
    ("0100", 4),
    ("0001", 5),
    ("0100", 2),
    ("0101", 1),
    ("0001", 1),
    ("0100", 5),
    ("0000", 1),
    ("0300", 129),
    ("0400", 2),
    ("0500", 13),
    ("0400", 1),
    ("0500", 28),
    ("0400", 1),
    ("0500", 3),
    ("0400", 12),
    ("0500", 20),
    ("0400", 3),
  ],
  2: [("0000", 1), ("0300", 143), ("0400", 2), ("0300", 3), ("0400", 7)],
}
# board, command, request payload, reply payload; "-" is an empty payload.
RECORDED_EXCHANGES = """
0:1:-:34002e00320030002e0036000000
10:1:-:31002e0030002e0035000000
0:10:0000:000000000040afc0000000000040af40
0:10:0100:000000000040afc0000000000040af40
0:10:0200:00000000004055c000000000008aa140
10:20:0000:00000040ed05873f000000a0aaa62ac0
10:20:0100:00000040ed05873f000000a0aaa62ac0
10:20:0200:00000000d432833f00000020af8741c0
0:17:0000:-
0:17:0100:-
0:28:00000000000000000000:-
0:28:01000000000000000000:-
0:22:0000721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40:0000
0:22:0100721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40:0000
0:17:0000:-
0:17:0100:-
0:28:00000000000000000000:-
0:28:01000000000000000000:-
0:22:0000721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40:0000
0:22:0100721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40:0000
0:17:0000:-
0:17:0100:-
0:27:0000:0000000000c03940000000000000000000000000000000000000000000c039400000000000000000000000000088c340
0:22:0000e4388ee3386e4240b4a291808fb3b540814e1be8b40a8a40:0000
0:27:0100:000000a01cc73bc000000000000000000000000000000000000000a01cc73bc0000000808fb3b540000000e0b40a8a40
0:22:0100398ee3d8aaaa30c0b4a291808fb3b540814e1be8b40a8a40:0000
0:28:00000000000000000000:-
0:28:01000000000000000000:-
0:17:0200:-
0:21:0200000001000000d343867e1e874d4064ea130f737482407d2ae7ab04c2ce40:0000
0:17:0000:-
0:17:0100:-
0:28:00000000000000000000:-
0:28:01000000000000000000:-
0:22:0000721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40:0000
0:22:0100721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40:0000
0:17:0000:-
0:17:0100:-
0:28:00000000000000000000:-
0:28:01000000000000000000:-
0:22:0000721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40:0000
0:22:0100721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40:0000
0:17:0000:-
0:17:0100:-
0:27:0000:000000000000f83f00000000000000000000000000000000000000000000f83f0000000000000000000000000088c340
0:22:00008ee3388ee3382940b4a291808fb3b540814e1be8b40a8a40:0000
0:27:0100:000000000000f8bf00000000000000000000000000000000000000000000f8bf0000000000000000000000000088c340
0:22:01008ee3388ee3382340b4a291808fb3b540814e1be8b40a8a40:0000
0:28:00000000000000000000:-
0:28:01000000000000000000:-
0:17:0200:-
0:21:0200000001000000d343867e1e874d4064ea130f737482407d2ae7ab04c2ce40:0000
0:17:0100:-
0:21:01000300010001001c7ba7a066391640e359d148c0c74b4025e09b5793b39540:0000
0:17:0000:-
0:21:00000300010000001c7ba7a066391640e359d148c0c74b4025e09b5793b39540:0000
0:22:000000000000000000000000000000407f400000000000005940:0000
0:28:000000000000000049c0:-
0:22:010000000000000000000000000000407f400000000000005940:0000
0:28:01000000000000005e40:-
0:22:0200000000000000000036496c9706c2ae407d2ae7ab04c2ce40:0000
0:22:000040877b10d60349c0e359d148c0c74b4025e09b5793b39540:0000
0:22:0100545f88b1d4794940e359d148c0c74b4025e09b5793b39540:0000
0:22:0200000000000000000036496c9706c2ae407d2ae7ab04c2ce40:0000
0:22:0000042e990e845733c0e359d148c0c74b4025e09b5793b39540:0000
0:22:0100ff8a9491d13f4740e359d148c0c74b4025e09b5793b39540:0000
0:22:0200000000000000000036496c9706c2ae407d2ae7ab04c2ce40:0000
0:22:0000aaaaea83a1a3063fe359d148c0c74b4025e09b5793b39540:0000
0:22:01008ee3d883a1a3063fe359d148c0c74b4025e09b5793b39540:0000
0:27:0000:000000000000073f00000000000000000000000000000000000000000000073f00000040c0c74b400000006093b39540
0:27:0100:000000000000083f00000000000000000000000000000000000000000000073f00000040c0c74b400000006093b39540
0:27:0200:0000000000000000000000000000000000000000000000000000000000000000000000a006c2ae40000000a004c2ce40
0:27:0000:000000000000073f00000000000000000000000000000000000000000000073f00000040c0c74b400000006093b39540
0:84:0000:000000008099a93f
0:27:0100:000000000000083f00000000000000000000000000000000000000000000073f00000040c0c74b400000006093b39540
0:84:0100:00000000a047d1bf
0:27:0200:0000000000000000000000000000000000000000000000000000000000000000000000a006c2ae40000000a004c2ce40
0:84:0200:000000000000d0bf
0:64:-:0000
0:24:0000814e1be8b40a8a40:0000
0:24:0100814e1be8b40a8a40:0000
0:24:0200bb34be4f05c2be40:0000
""".split()


def make_v3_answers():
  """Play each recorded reply list in order, repeating its final reply."""
  answers: Dict[Tuple[int, int, bytes], List[bytes]] = {}
  for exchange in RECORDED_EXCHANGES:
    board, command, request, reply = exchange.split(":")
    key = int(board), int(command), bytes.fromhex(request.replace("-", ""))
    answers.setdefault(key, []).append(bytes.fromhex(reply.replace("-", "")))
  for motor, runs in RECORDED_STATUS_RUNS.items():
    answers[0, 20, struct.pack("<h", motor)] = [
      bytes.fromhex(word) for word, count in runs for _ in range(count)
    ]
  return answers


def motion_commands(link):
  """Commands that change a motor, leaving status polling out of comparisons."""
  return [
    (command, payload.hex())
    for _, board, command, payload in map(lambda frame: parse_frame(frame=frame), link.written)
    if board == 0 and command in (17, 21, 22, 24, 28)
  ]


class V3Link(FakeLink):
  """The recorded v3 link; boards 11 and 12, which a v3 lacks, refuse at once instead of timing
  out, so the tests keep the default read timeout."""

  async def write(self, data: bytes) -> int:
    packet_number, board, _, _ = parse_frame(frame=data)
    if board not in (11, 12):
      return await super().write(data)
    self.written.append(data)
    self.incoming.extend(build_frame(packet_number=packet_number, board=board, command=0xFFFF))
    return len(data)


class V3ArmTests(DriverTestCase):
  def setUp(self):
    """Keep recorded status polls, without waiting between them."""
    super().setUp()
    patcher = mock.patch.object(arm_v3_module, "MOTOR_STATUS_POLL_INTERVAL", 0)
    patcher.start()
    self.addCleanup(patcher.stop)
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.path = os.path.join(self.directory.name, "unit.json")
    with open(self.path, "w", encoding="utf-8") as file:
      json.dump({"arm": SYNTHETIC_V3_ARM_BLOCK}, file)

  async def make_arm(self, *, answers=None, calibrated=True):
    """Discover through the recorded transport, deferring initialization for the test."""
    link = V3Link(answers=make_v3_answers() if answers is None else answers)
    driver = await self.set_up_driver(
      link=link, declared_configuration_json=self.path if calibrated else None
    )
    self.assertIsInstance(driver.arm, V3Arm)
    return driver, link

  async def test_setup_and_stop_follow_the_recorded_v3_sequence(self):
    """Seat twice, use live backoffs, home Z twice, then arms 1/0, and walk to idle."""
    link = V3Link(answers=make_v3_answers())
    driver = await self.set_up_driver(
      link=link, skip_arm=False, declared_configuration_json=self.path
    )
    assert driver.arm is not None
    self.assertTrue(driver.arm.is_initialized)
    position = await driver.arm.request_head_position()
    self.assertAlmostEqual(position.x, 15.0, places=3)
    self.assertAlmostEqual(position.y, 31.177, places=3)
    self.assertAlmostEqual(position.z, 0.0, places=3)
    with self.assertRaisesRegex(NotImplementedError, "no absolute"):
      await driver.arm.request_absolute_joint_position()
    await driver.stop()
    expected = []
    for exchange in RECORDED_EXCHANGES:
      board, command, request, _ = exchange.split(":")
      if int(board) == 0 and int(command) in (17, 21, 22, 24, 28):
        expected.append((int(command), request))
    self.assertEqual(motion_commands(link), expected)
    self.assertFalse(driver.arm.is_initialized)

  async def test_missing_calibration_refuses_motion_and_pose(self):
    """Discovery remains usable without another unit's calibration being substituted."""
    driver, link = await self.make_arm(calibrated=False)
    with self.assertRaisesRegex(MantisConfigurationError, "no v3 arm calibration"):
      await driver.arm.initialize()
    self.assertEqual(motion_commands(link), [])
    with self.assertRaisesRegex(MantisMotionError, "must be initialized"):
      await driver.arm.request_head_position()

  async def test_invalid_route_refuses_before_any_motor_command(self):
    """A calibrated route outside a discovered motor limit is rejected before retracting."""
    driver, link = await self.make_arm()
    driver.configuration.motor_limits[0] = (-1.0, 1.0)
    with self.assertRaisesRegex(MantisConfigurationError, "outside its limits"):
      await driver.arm.initialize()
    self.assertEqual(motion_commands(link), [])

  async def test_failure_to_seat_stops_every_motor(self):
    """A retract that never encounters a stop must not proceed to home Z."""
    answers = make_v3_answers()
    for motor in range(3):
      answers[0, 20, struct.pack("<h", motor)] = [bytes.fromhex("0000")]
    driver, link = await self.make_arm(answers=answers)
    driver.arm.default_prehome_passes = 2
    with self.assertRaisesRegex(MantisMotionError, "did not seat"):
      await driver.arm.initialize()
    commands = motion_commands(link)
    self.assertNotIn(21, [command for command, _ in commands])
    self.assertEqual(
      [payload[:4] for command, payload in commands if command == 24], ["0000", "0100", "0200"]
    )
    self.assertFalse(driver.arm.is_initialized)

  async def test_missing_homed_flag_stops_before_homing_the_arms(self):
    """An accepted home command alone is not proof that Z homed."""
    answers = make_v3_answers()
    answers[0, 20, bytes.fromhex("0200")] = [bytes.fromhex("0000")]
    driver, link = await self.make_arm(answers=answers)
    with self.assertRaisesRegex(MantisMotionError, "motor 2 did not home"):
      await driver.arm.initialize()
    commands = motion_commands(link)
    self.assertEqual([payload[:4] for command, payload in commands if command == 21], ["0200"])
    self.assertEqual(
      [payload[:4] for command, payload in commands if command == 24], ["0000", "0100", "0200"]
    )
    self.assertFalse(driver.arm.is_initialized)

  async def test_cancellation_after_seating_stops_every_motor(self):
    """Cancellation after moving propagates only after all three stops have been sent."""
    driver, link = await self.make_arm()
    with mock.patch.object(driver.arm, "_home_axes", side_effect=asyncio.CancelledError):
      with self.assertRaises(asyncio.CancelledError):
        await driver.arm.initialize()
    commands = motion_commands(link)
    self.assertEqual(
      [payload[:4] for command, payload in commands if command == 24], ["0000", "0100", "0200"]
    )
    self.assertFalse(driver.arm.is_initialized)

  async def test_saved_configuration_keeps_the_v3_calibration(self):
    """A saved configuration carries the complete home frame and route into another setup."""
    driver, _ = await self.make_arm()
    saved = os.path.join(self.directory.name, "saved.json")
    driver.save_configuration(path=saved)
    with open(saved, encoding="utf-8") as file:
      self.assertEqual(json.load(file)["arm"], SYNTHETIC_V3_ARM_BLOCK)
