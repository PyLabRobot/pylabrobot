"""V3 arm initialization against recorded device replies, with synthetic home references."""

import json
import os
import struct
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError, MantisMotionError
from pylabrobot.formulatrix.mantis.driver.features import arm_v3 as arm_v3_module
from pylabrobot.formulatrix.mantis.driver.features.arm_v3 import V3Arm
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestBase, FakeLink

SYNTHETIC_V3_ARM_BLOCK: Dict[str, Any] = {
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


def make_v3_answers() -> Dict[Tuple[int, int, bytes], List[bytes]]:
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


def motion_commands(link: FakeLink) -> List[Tuple[int, str]]:
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


class TestV3Arm(DriverTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Keep recorded status polls, without waiting between them."""
    await super()._enter_lifespan(stack, **kwargs)
    stack.enter_context(mock.patch.object(arm_v3_module, "MOTOR_STATUS_POLL_INTERVAL", 0))
    self.directory = stack.enter_context(tempfile.TemporaryDirectory())
    self.path = os.path.join(self.directory, "unit.json")
    with open(self.path, "w", encoding="utf-8") as file:
      json.dump({"arm": SYNTHETIC_V3_ARM_BLOCK}, file)

  async def make_arm(
    self,
    *,
    answers: Optional[Dict[Tuple[int, int, bytes], List[bytes]]] = None,
    calibrated: bool = True,
  ) -> Tuple[MantisDriver, V3Link]:
    """Discover through the recorded transport, deferring initialization for the test."""
    link = V3Link(answers=make_v3_answers() if answers is None else answers)
    driver = await self.set_up_driver(
      link=link, declared_configuration_json=self.path if calibrated else None
    )
    assert isinstance(driver.arm, V3Arm)
    return driver, link

  async def test_lifespan_follow_the_recorded_v3_sequence(self) -> None:
    """Seat twice, use live backoffs, home Z twice, then arms 1/0, and walk to idle."""
    link = V3Link(answers=make_v3_answers())
    driver = self.make_driver(link=link, declared_configuration_json=self.path)
    async with driver.lifespan(skip_arm=False):
      assert driver.arm is not None
      assert driver.arm.is_initialized
      position = await driver.arm.request_head_position()
      assert position.x == pytest.approx(15.0, abs=1e-3)
      assert position.y == pytest.approx(31.177, abs=1e-3)
      assert position.z == pytest.approx(0.0, abs=1e-3)
      with pytest.raises(NotImplementedError, match="no absolute"):
        await driver.arm.request_absolute_joint_position()
    expected = []
    for exchange in RECORDED_EXCHANGES:
      board, command, request, _ = exchange.split(":")
      if int(board) == 0 and int(command) in (17, 21, 22, 24, 28):
        expected.append((int(command), request))
    assert motion_commands(link) == expected
    assert driver.arm is not None
    assert not driver.arm.is_initialized

  async def test_missing_calibration_refuses_motion_and_pose(self) -> None:
    """Discovery remains usable without another unit's calibration being substituted."""
    driver, link = await self.make_arm(calibrated=False)
    assert driver.arm is not None
    with pytest.raises(MantisConfigurationError, match="no v3 arm calibration"):
      await driver.arm.initialize()
    assert motion_commands(link) == []
    with pytest.raises(MantisMotionError, match="must be initialized"):
      await driver.arm.request_head_position()

  async def test_invalid_route_refuses_before_any_motor_command(self) -> None:
    """A calibrated route outside a discovered motor limit is rejected before retracting."""
    driver, link = await self.make_arm()
    assert driver.arm is not None
    driver.configuration.motor_limits[0] = (-1.0, 1.0)
    with pytest.raises(MantisConfigurationError, match="outside its limits"):
      await driver.arm.initialize()
    assert motion_commands(link) == []

  async def test_failure_to_seat_stops_every_motor(self) -> None:
    """A retract that never encounters a stop must not proceed to home Z."""
    answers = make_v3_answers()
    for motor in range(3):
      answers[0, 20, struct.pack("<h", motor)] = [bytes.fromhex("0000")]
    driver, link = await self.make_arm(answers=answers)
    assert isinstance(driver.arm, V3Arm)
    driver.arm.default_prehome_passes = 2
    with pytest.raises(MantisMotionError, match="did not seat"):
      await driver.arm.initialize()
    commands = motion_commands(link)
    assert 21 not in [command for command, _ in commands]
    assert [payload[:4] for command, payload in commands if command == 24] == [
      "0000",
      "0100",
      "0200",
    ]
    assert not driver.arm.is_initialized

  async def test_missing_homed_flag_stops_before_homing_the_arms(self) -> None:
    """An accepted home command alone is not proof that Z homed."""
    answers = make_v3_answers()
    answers[0, 20, bytes.fromhex("0200")] = [bytes.fromhex("0000")]
    driver, link = await self.make_arm(answers=answers)
    assert driver.arm is not None
    with pytest.raises(MantisMotionError, match="motor 2 did not home"):
      await driver.arm.initialize()
    commands = motion_commands(link)
    assert [payload[:4] for command, payload in commands if command == 21] == ["0200"]
    assert [payload[:4] for command, payload in commands if command == 24] == [
      "0000",
      "0100",
      "0200",
    ]
    assert not driver.arm.is_initialized

  async def test_cancellation_after_seating_stops_every_motor(self) -> None:
    """Level-triggered cancellation after moving propagates only after all three stops are sent."""
    driver, link = await self.make_arm()
    assert driver.arm is not None
    arm = driver.arm
    homing_entered = anyio.Event()

    async def block_home(*, motors: Tuple[int, ...]) -> None:
      homing_entered.set()
      await anyio.Event().wait()

    scope = anyio.CancelScope()

    async def run_init() -> None:
      with scope:
        await arm.initialize()

    with mock.patch.object(arm, "_home_axes", new=block_home):
      async with anyio.create_task_group() as tg:
        tg.start_soon(run_init)
        await homing_entered.wait()
        scope.cancel()
    assert scope.cancelled_caught
    commands = motion_commands(link)
    assert [payload[:4] for command, payload in commands if command == 24] == [
      "0000",
      "0100",
      "0200",
    ]
    assert not arm.is_initialized

  async def test_saved_configuration_keeps_the_v3_calibration(self) -> None:
    """A saved configuration carries the complete home frame and route into another lifespan."""
    driver, _ = await self.make_arm()
    saved = os.path.join(self.directory, "saved.json")
    driver.save_configuration(path=saved)
    with open(saved, encoding="utf-8") as file:
      assert json.load(file)["arm"] == SYNTHETIC_V3_ARM_BLOCK
