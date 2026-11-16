"""Arm tests: the bring-up and the park against a Mantis v4's own startup and close, as recorded.

The recorded startup declared each arm where its unit's encoder correction put it; these tests
declare it with a synthetic correction, so only those values differ from what was recorded. The
offsets declared are the ones the recorded idle targets imply, so the moves are sent as recorded.
"""

import json
import logging
import os
import struct
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
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
from pylabrobot.formulatrix.mantis.driver.features.arm_v3 import V3Arm
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
  DriverTestBase,
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

  def __init__(self, **kwargs: Any):
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


class ArmTestBase(DriverTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.directory = stack.enter_context(tempfile.TemporaryDirectory())

  def declare(self, *, arm_block: Optional[Dict[str, Any]]) -> str:
    path = os.path.join(self.directory, f"declared_{len(os.listdir(self.directory))}.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, **({"arm": arm_block} if arm_block is not None else {})}, f)
    return path

  async def set_up_arm(
    self, *, link: FakeLink, arm_block: Optional[Dict[str, Any]] = SYNTHETIC_ARM_BLOCK
  ) -> MantisDriver:
    driver = await self.set_up_driver(
      link=link, declared_configuration_json=self.declare(arm_block=arm_block)
    )
    assert driver.arm is not None
    self.sent_before = len(link.written)
    return driver

  def sent(self, link: FakeLink) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in link.written[self.sent_before :]]


class TestInitialize(ArmTestBase):
  async def test_initialize_sends_the_recorded_startup(self) -> None:
    link = ArmLink(
      answers=make_answers(),
      events={
        (0, 21, bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])): [
          bytes.fromhex(STARTUP_HOME_DONE)
        ]
      },
    )
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    await driver.arm.initialize()
    driver.default_read_timeout = 0.25  # stop's read of Z's encoder is never answered
    commands = self.sent(link)
    assert [token(command) for command in commands if command[1] != 20] == get_expected_startup()
    assert driver.arm.is_initialized
    for index, (_, command, payload) in enumerate(commands):
      if command == 21:
        # Before each Z home: each motor's following-error check and status, then Z's faults clear.
        assert commands[index - 7 : index - 1] == [
          (0, code, struct.pack("<h", motor)) for motor in (0, 1, 2) for code in (82, 20)
        ]
      if command == 22:
        assert commands[index + 1] == (0, 20, payload[:2])  # read once as it starts

  async def test_a_failure_energising_unpowers_both_arm_motors(self) -> None:
    link = ArmLink(answers=make_answers())
    del link.answers[(0, 19, bytes.fromhex("01000000"))]
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    driver.default_read_timeout = 0.25
    with pytest.raises(MantisTimeoutError):
      await driver.arm.initialize()
    assert self.sent(link)[-2:] == UNPOWER
    assert not driver.arm.is_initialized

  async def test_cancelled_initialize_shields_unpower_of_both_arm_motors(self) -> None:
    link = ArmLink(answers=make_answers())
    for motor in (0, 1):
      link.answers[(0, 19, bytes.fromhex(f"0{motor}000100"))] = [b"\x00\x00"]
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    energised = anyio.Event()
    write = link.write

    async def write_and_signal(data: bytes) -> int:
      _, board, command, payload = parse_frame(frame=data)
      if (board, command, payload) == (0, 19, bytes.fromhex("01000000")):
        energised.set()
      return await write(data)

    link.io.write.side_effect = write_and_signal

    with anyio.CancelScope() as scope:

      async def cancel_once_energised() -> None:
        await energised.wait()
        scope.cancel()

      async with anyio.create_task_group() as tg:
        tg.start_soon(cancel_once_energised)
        await driver.arm.initialize()
    assert scope.cancelled_caught
    assert self.sent(link)[-2:] == UNPOWER
    assert not driver.arm.is_initialized

  async def test_without_the_arm_block_nothing_is_sent(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_arm(link=link, arm_block=None)
    assert driver.arm is not None
    with pytest.raises(MantisConfigurationError, match="no arm calibration"):
      await driver.arm.initialize()
    assert self.sent(link) == []

  async def test_setup_without_the_arm_block_refuses_before_anything_moves(self) -> None:
    self.default_read_timeout = 0.25  # the red colour write is never answered
    link = FakeLink(answers=make_answers())
    driver = self.make_driver(link=link, declared_configuration_json=self.declare(arm_block=None))
    with pytest.raises(MantisConfigurationError, match="no arm calibration"):
      async with driver:
        pass
    commands = [parse_frame(frame=frame)[2] for frame in link.written]
    assert 19 not in commands
    assert 22 not in commands
    assert link.io.exited_count == 1

  async def test_a_head_away_from_idle_refuses_and_unpowers_the_arms(self) -> None:
    link = ArmLink(answers=make_answers())
    for motor, reply in AWAY_POSITION_REPLIES.items():
      link.answers[(0, 27, struct.pack("<h", motor))] = [bytes.fromhex(reply)]
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    driver.default_read_timeout = 0.25  # unpowering the arms is never answered
    with pytest.raises(MantisMotionError, match="moved to idle only from x"):
      await driver.arm.initialize()
    sent = self.sent(link)
    assert 22 not in [command for _, command, _ in sent]
    assert sent[-2:] == UNPOWER
    assert not driver.arm.is_initialized

  async def test_a_motor_error_while_z_homes_fails_at_once_and_unpowers_the_arms(self) -> None:
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    link = ArmLink(
      answers=make_answers(),
      events={(0, 21, home): [bytes.fromhex(RECORDED_MOTOR_ERROR_EVENTS[0])]},
    )
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    driver.default_read_timeout = 0.25  # unpowering the arms is never answered
    with pytest.raises(MantisMotionError, match="reported error 9") as caught:
      await driver.arm.initialize()
    assert (caught.value.motor, caught.value.code) == (0, 9)
    assert self.sent(link)[-2:] == UNPOWER

  async def test_a_motor_that_slipped_stops_z_homing_before_it_starts(self) -> None:
    link = ArmLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    # After power-on, motor 1 read a following error while idle.
    link.answers[(0, 20, b"\x01\x00")] = [bytes.fromhex("8000")]
    with pytest.raises(MantisMotionError, match="motor 1 slipped"):
      await driver.arm._home_z()
    assert 21 not in [command for _, command, _ in self.sent(link)]

  async def test_reads_convert_to_degrees_and_mm(self) -> None:
    link = ArmLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    joint_position = await driver.arm.request_joint_position()
    assert joint_position[0] == pytest.approx(-0.585047 * 1.8, abs=1e-5)
    assert joint_position[1] == pytest.approx(1.76374 * 1.8, abs=1e-4)
    assert joint_position[2] == 0.0
    absolute = await driver.arm.request_absolute_joint_position()
    assert sorted(absolute) == [0, 1]


class TestSetupDisplay(ArmTestBase):
  def startup_link(self, *, answers: Dict[Tuple[int, int, bytes], List[bytes]]) -> ArmLink:
    """Answers the recorded startup, Z reporting its homing done, and ``answers``."""
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    return ArmLink(answers=answers, events={(0, 21, home): [bytes.fromhex(STARTUP_HOME_DONE)]})

  async def test_setup_shows_the_bring_up_in_blue_then_green_then_idle(self) -> None:
    link = self.startup_link(answers=make_led_bar_answers())
    driver = await self.set_up_driver(
      link=link,
      skip_arm=False,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    assert driver.arm is not None
    assert driver.arm.is_initialized
    driver.default_read_timeout = 0.25  # stop's read of Z's encoder is never answered
    frames = [parse_frame(frame=frame)[1:] for frame in link.written]
    moves = [index for index, (_, command, _) in enumerate(frames) if command in (19, 21, 22)]
    assert frames.index(color_write(colors=[BLUE] * 20)) < moves[0]
    assert moves[-1] < frames.index(color_write(colors=[GREEN] * 20))
    assert bar_states(written=link.written) == [BLUE, GREEN, "idle"]

  async def test_a_failed_bring_up_shows_red_then_turns_off(self) -> None:
    link = self.startup_link(answers=make_led_bar_answers())
    for motor, reply in AWAY_POSITION_REPLIES.items():
      link.answers[(0, 27, struct.pack("<h", motor))] = [bytes.fromhex(reply)]
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    with pytest.raises(MantisMotionError, match="moved to idle only from x"):
      async with driver:
        pass
    assert bar_states(written=link.written) == [BLUE, RED, OFF]

  async def test_link_loss_after_arm_init_resets_initialized_on_reentry_with_skip_arm(self) -> None:
    link = self.startup_link(answers=make_led_bar_answers())
    driver = self.make_driver(
      link=link,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    saved_read = link.io.read.side_effect
    async with driver.lifespan(skip_arm=False):
      assert driver.arm is not None
      assert driver.arm.is_initialized
      link.io.read.side_effect = OSError("FTDI cable unplugged")
      with anyio.fail_after(2.0):
        while driver._reader_running:
          await anyio.sleep(0.01)
      link.io.read.side_effect = saved_read
    assert driver.arm is not None
    assert not driver.arm.is_initialized

    written_before_reentry = len(link.written)
    async with driver.lifespan(skip_arm=True):
      assert not driver.arm.is_initialized
    reentry_commands = [parse_frame(frame=f)[1:3] for f in link.written[written_before_reentry:]]
    # Arm._park() Z rise (0, 22) and queued sequencer moves (0, 62) must never run when skip_arm=True!
    assert (0, 22) not in reentry_commands
    assert (0, 62) not in reentry_commands

  async def test_halt_timeout_during_shutdown_still_releases_clamp_and_turns_led_off(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    answers = make_led_bar_answers()
    answers[(12, 33, b"")] = [struct.pack("<H", 3), struct.pack("<H", 6)]
    answers[(12, 31, struct.pack("<ddI", 0.75, 0.0, 5000))] = [b""]
    link = self.startup_link(answers=answers)
    driver = self.make_driver(
      link=link,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    original_move_on_after = anyio.move_on_after
    monkeypatch.setattr(
      master_module.anyio,
      "move_on_after",
      lambda delay: original_move_on_after(0.01 if delay == 130.0 else delay),
    )

    async def hung_halt() -> None:
      await anyio.sleep_forever()

    async with driver.lifespan(skip_arm=True):
      monkeypatch.setattr(driver, "_halt", hung_halt)
      written_before_exit = len(link.written)

    exit_commands = [parse_frame(frame=f)[1:3] for f in link.written[written_before_exit:]]
    assert (12, 31) in exit_commands
    assert (12, 13) in exit_commands

  async def test_a_led_bar_fault_does_not_stop_the_bring_up(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    link = self.startup_link(answers=make_answers())
    del link.answers[color_write(colors=[BLUE] * 20)]
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.led_bar"
    ):
      driver = await self.set_up_driver(
        link=link,
        skip_arm=False,
        declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
      )
    assert any("did not show 'setup'" in rec.getMessage() for rec in caplog.records)
    assert driver.arm is not None
    assert driver.arm.is_initialized
    driver.default_read_timeout = 0.25  # stop's read of Z's encoder is never answered

  async def test_without_show_actions_the_bring_up_shows_only_the_idle_animation(self) -> None:
    link = self.startup_link(answers=make_answers())
    driver = await self.set_up_driver(
      link=link,
      skip_arm=False,
      show_actions=False,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    assert driver.arm is not None
    assert driver.arm.is_initialized
    driver.default_read_timeout = 0.25  # stop's read of Z's encoder is never answered
    assert led_bar_frames(written=link.written) == [START_IDLE_ANIMATION]


class IdleTargetTests(unittest.TestCase):
  def test_idle_targets_follow_the_units_offsets(self) -> None:
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

  def test_the_implied_offsets_give_the_recorded_idle_targets(self) -> None:
    arm = Arm(
      driver=mock.Mock(),
      offsets=SYNTHETIC_OFFSETS,
      homing_position_a=SYNTHETIC_HOMING_POSITION_A,
      encoder_correction={0: [], 1: []},
    )
    targets = arm._get_idle_targets()
    self.assertAlmostEqual(targets[0], RECORDED_IDLE_TARGETS[0], places=12)
    self.assertEqual(targets[1], RECORDED_IDLE_TARGETS[1])

  def test_without_the_arm_block_there_are_no_idle_targets(self) -> None:
    arm = Arm(driver=mock.Mock(), offsets=None, homing_position_a=None, encoder_correction=None)
    with self.assertRaises(MantisConfigurationError):
      arm._get_idle_targets()


class DefaultTests(unittest.TestCase):
  def test_defaults_are_checked_when_assigned(self) -> None:
    arm = Arm(driver=mock.Mock(), offsets=None, homing_position_a=None, encoder_correction=None)
    with self.assertRaises(ValueError):
      arm.default_arm_speed = 0.0
    with self.assertRaises(ValueError):
      arm.default_arm_acceleration = 0.0
    with self.assertRaises(ValueError):
      arm.default_z_speed = 0.0
    with self.assertRaises(ValueError):
      arm.default_z_acceleration = 0.0
    arm.default_z_acceleration = 100.0
    self.assertEqual(arm.default_z_acceleration, 100.0)


def get_away_arm_block() -> Dict[str, Any]:
  """The synthetic arm block, its references putting the last recorded readings at (-57, 0) mm."""
  homing_position_a = {}
  for motor, angle in enumerate(compute_joint_angles(x=-57.0, y=0.0)):
    _, reading = struct.unpack("<Hd", bytes.fromhex(STARTUP_ABSOLUTE_ENCODER_REPLIES[motor][-1]))
    position = compute_device_position(motor=motor, position=angle - SYNTHETIC_OFFSETS[motor])
    homing_position_a[str(motor)] = (reading - position) % 200.0
  return {**SYNTHETIC_ARM_BLOCK, "homing_position_a": homing_position_a}


class TestStop(ArmTestBase):
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

  async def test_exit_halts_then_parks_z_first_then_as_the_recorded_close(self) -> None:
    link = self.close_link()
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    async with driver.lifespan(skip_arm=True):
      assert driver.arm is not None
      since = len(link.written)
      driver.arm._initialized = True
      driver._next_item = 14  # numbered as in the recorded close
    sent = [parse_frame(frame=frame)[1:] for frame in link.written[since:]]
    assert sent[:4] == HALT
    statuses = [(0, 20, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert sent[4:22] == (
      statuses
      + [(11, 30, struct.pack("<h", motor)) for motor in (0, 1)]
      # Z up on its own, read back by its encoder before the arms move.
      + [(0, 22, Z_UP_MOVE), (0, 20, b"\x02\x00")]
      + statuses
      + [(0, 84, b"\x02\x00")]
      + [(0, 61, b""), (0, 62, b"")]
      + [(0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[item])) for item in (14, 15)]
      + [(0, 61, b""), (0, 62, b""), (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[16]))]
    )
    assert sent[22][:2] == (12, 33)
    assert sent[-2:] == TURN_OFF
    assert not driver.arm.is_initialized

  async def test_park_timeout_during_shutdown_aborts_sequencer_and_stops_motors(
    self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
  ) -> None:
    link = self.close_link()
    queued_15 = (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[15]))
    del link.events[queued_15]
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    original_move_on_after = anyio.move_on_after
    halt_scopes: List[anyio.CancelScope] = []

    def track_halt_scope(delay: float) -> anyio.CancelScope:
      scope = original_move_on_after(delay)
      if delay == 130.0:
        halt_scopes.append(scope)
      return scope

    monkeypatch.setattr(master_module.anyio, "move_on_after", track_halt_scope)
    original_write = link.io.write.side_effect

    async def write_and_expire_on_park_item(data: bytes) -> int:
      result: int = await original_write(data)
      if parse_frame(frame=data)[1:] == queued_15 and halt_scopes:
        halt_scopes[-1].deadline = anyio.current_time() + 0.02
      return result

    link.io.write.side_effect = write_and_expire_on_park_item
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.master"):
      async with driver.lifespan(skip_arm=True):
        assert driver.arm is not None
        since = len(link.written)
        driver.arm._initialized = True
        driver._next_item = 14
    assert any("arm halt timed out during shutdown" in rec.getMessage() for rec in caplog.records)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written[since:]]
    assert queued_15 in sent
    after_queued = sent[sent.index(queued_15) + 1 :]
    assert after_queued[:4] == HALT
    assert after_queued[4][:2] == (12, 33)
    assert after_queued[-2:] == TURN_OFF
    assert not driver.arm.is_initialized

  async def test_z_not_up_by_its_encoder_stops_before_the_arms_move(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    link = self.close_link()
    link.answers[(0, 84, b"\x02\x00")] = [bytes.fromhex(Z_DOWN_ENCODER_REPLY)]
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.arm"
    ):
      async with driver.lifespan(skip_arm=True):
        assert driver.arm is not None
        since = len(link.written)
        driver.arm._initialized = True
    assert "Z reads 19.996 mm below its flag" in caplog.text
    commands = [
      command for _, command, _ in (parse_frame(frame=f)[1:] for f in link.written[since:])
    ]
    assert commands.count(22) == 1
    assert 68 not in commands
    assert 33 in commands

  async def test_a_motor_that_did_not_stop_is_not_parked(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    link = self.close_link()
    del link.answers[(0, 24, bytes.fromhex("0100814e1be8b40a8a40"))]
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.arm"
    ):
      async with driver.lifespan(skip_arm=True):
        assert driver.arm is not None
        since = len(link.written)
        driver.default_read_timeout = 0.25
        driver.arm._initialized = True
    assert (
      sum(
        "a motor did not stop; the arm is not parked" in rec.getMessage() for rec in caplog.records
      )
      == 1
    )
    sent = [parse_frame(frame=frame)[1:] for frame in link.written[since:]]
    assert [command for _, command, _ in sent if command == 24] == [24, 24, 24]
    assert not ({22, 30, 68} & {command for _, command, _ in sent})

  async def test_a_head_away_from_idle_is_not_parked_and_the_clamp_is_still_released(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    link = self.close_link()
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=get_away_arm_block())
    )
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.arm"
    ):
      async with driver.lifespan(skip_arm=True):
        assert driver.arm is not None
        since = len(link.written)
        driver.arm._initialized = True
    full_text = caplog.text
    assert "the arm was not parked" in full_text
    assert "moved to idle only from x" in full_text
    commands = [
      command for _, command, _ in (parse_frame(frame=f)[1:] for f in link.written[since:])
    ]
    assert 68 not in commands
    assert 33 in commands

  async def test_a_motor_that_slipped_is_not_parked(self, caplog: pytest.LogCaptureFixture) -> None:
    link = self.close_link()
    link.answers[(0, 20, b"\x01\x00")] = [bytes.fromhex("8000")]
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.arm"
    ):
      async with driver.lifespan(skip_arm=True):
        assert driver.arm is not None
        since = len(link.written)
        driver.arm._initialized = True
    assert any("the arm was not parked" in rec.getMessage() for rec in caplog.records)
    commands = [
      command for _, command, _ in (parse_frame(frame=f)[1:] for f in link.written[since:])
    ]
    assert 30 not in commands
    assert 68 not in commands

  async def test_an_arm_not_brought_up_is_halted_but_not_parked(self) -> None:
    link = self.close_link()
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    async with driver.lifespan(skip_arm=True):
      since = len(link.written)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written[since:]]
    assert sent[:4] == HALT
    assert 68 not in [command for _, command, _ in sent]

  async def test_without_a_led_bar_exit_halts_and_shows_nothing(self) -> None:
    link = self.close_link()
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    async with driver.lifespan(skip_arm=True):
      since = len(link.written)
      driver.led_bar = None
    sent = [parse_frame(frame=frame)[1:] for frame in link.written[since:]]
    assert sent[:4] == HALT
    assert led_bar_frames(written=link.written[since:]) == []


class RaisingLink(ArmLink):
  """As ``ArmLink``, and the write of one frame raises, as an unplugged link does."""

  def __init__(self, *, raises_on: Tuple[int, int, bytes], **kwargs: Any):
    super().__init__(**kwargs)
    self.raises_on = raises_on

  async def write(self, data: bytes) -> int:
    if parse_frame(frame=data)[1:] == self.raises_on:
      self.written.append(data)
      raise OSError("device unplugged")
    return await super().write(data)


class TestMoveFailure(ArmTestBase):
  async def test_a_failed_send_stops_the_motors_sent_a_move_and_logs_where_they_are(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    second = struct.pack("<hd", 1, RECORDED_IDLE_TARGETS[1]) + bytes.fromhex(
      "b4a291808fb3b540814e1be8b40a8a40"
    )
    link = RaisingLink(answers=make_answers(), raises_on=(0, 22, second))
    link.answers[(0, 84, b"\x02\x00")] = [bytes.fromhex(Z_UP_ENCODER_REPLY)]
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.arm"
    ):
      with pytest.raises(OSError, match="device unplugged"):
        await driver.arm._move_motors(targets=RECORDED_IDLE_TARGETS)
    assert [command for command in self.sent(link) if command[1] != 20][1:] == (
      [(0, 22, second)] + HALT[1:3] + [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    )
    assert any("by their encoders" in rec.getMessage() for rec in caplog.records)

  async def test_cancelled_move_shields_motor_stops_and_logs_where_they_stopped(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    link = ArmLink(answers=make_answers())
    link.answers[(0, 84, b"\x02\x00")] = [bytes.fromhex(Z_UP_ENCODER_REPLY)]
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    moving = anyio.Event()
    write = link.write

    async def write_and_keep_moving(data: bytes) -> int:
      res = await write(data)
      _, board, command, payload = parse_frame(frame=data)
      if (board, command) == (0, 22) and payload[:2] == b"\x01\x00":
        link.answers[(0, 20, b"\x01\x00")] = [bytes.fromhex(STATUS_MOVING)] * 50
        moving.set()
      return res

    link.io.write.side_effect = write_and_keep_moving
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.arm"
    ):
      with anyio.CancelScope() as scope:

        async def cancel_once_moving() -> None:
          await moving.wait()
          await anyio.sleep(0.03)
          scope.cancel()

        async with anyio.create_task_group() as tg:
          tg.start_soon(cancel_once_moving)
          await driver.arm._move_motors(targets=RECORDED_IDLE_TARGETS)
    assert scope.cancelled_caught
    non_status = [command for command in self.sent(link) if command[1] != 20]
    assert HALT[1] in non_status
    assert HALT[2] in non_status
    assert any("by their encoders" in rec.getMessage() for rec in caplog.records)


class TestDiscovery(ArmTestBase):
  async def test_board_11_selects_the_arm_implementation(self) -> None:
    for absent_boards, arm_class in (((), Arm), ((11,), V3Arm)):
      self.default_read_timeout = 0.25 if absent_boards else 5.0  # an absent board never answers
      driver = await self.set_up_driver(
        link=FakeLink(answers=make_answers(absent_boards=absent_boards))
      )
      assert type(driver.arm) is arm_class

  async def test_the_arm_block_is_never_taken_from_a_recording(self) -> None:
    path = os.path.join(self.directory, "recording.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, "arm": SYNTHETIC_ARM_BLOCK}, f)
    with mock.patch.object(master_module, "RECORDING_MANTIS_V4", path):
      driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert driver.arm is not None
    assert driver.arm.encoder_correction is None
    assert driver._unit_blocks["rfid"] == RECORDING["rfid"]

  async def test_a_declared_arm_block_is_read_and_saved(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    assert driver.arm.homing_position_a == SYNTHETIC_HOMING_POSITION_A
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "saved.json")
      driver.save_configuration(path=path)
      with open(path, encoding="utf-8") as f:
        assert json.load(f)["arm"] == SYNTHETIC_ARM_BLOCK

  async def test_a_correction_that_does_not_ascend_refuses_setup(self) -> None:
    block = {
      **SYNTHETIC_ARM_BLOCK,
      "encoder_correction": {"0": [[1, 2], [2, 1]], "1": SYNTHETIC_CORRECTION},
    }
    driver = self.make_driver(
      link=FakeLink(answers=make_answers()),
      declared_configuration_json=self.declare(arm_block=block),
    )
    with pytest.raises(MantisConfigurationError, match="measured ascending"):
      async with driver.lifespan(skip_arm=True):
        pass
