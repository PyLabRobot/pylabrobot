"""Arm tests: the bring-up and the park against a Mantis v4's own startup and close, as recorded.

The recorded startup declared each arm where its unit's encoder correction put it; these tests
declare it with a synthetic correction, so only those values differ from what was recorded. The
offsets declared are the ones the recorded idle targets imply, so the moves are sent as recorded.
"""

import contextlib
import json
import logging
import math
import os
import struct
import tempfile
import unittest
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver import master as master_module
from pylabrobot.formulatrix.mantis.driver.arm_kinematics import (
  MantisArmCoordinate,
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
  CYAN,
  GREEN,
  OFF,
  RED,
  START_IDLE_ANIMATION,
  STOP_ANIMATION,
  VIOLET,
  bar_states,
  color_write,
  led_bar_frames,
  make_led_bar_answers,
)
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  RECORDED_MOTOR_ERROR_EVENTS,
  DriverTestBase,
  FakeLink,
  make_answers,
)
from pylabrobot.formulatrix.mantis.driver.oem_config_tests import (
  SYNTHETIC_APPROACH_PATHS_BLOCK,
  SYNTHETIC_IDLE_ROUTES,
  SYNTHETIC_SAFE_XY,
  SYNTHETIC_STAGE_BLOCK,
  SYNTHETIC_V3_ARM_BLOCK,
  SYNTHETIC_WASH_ROUTE,
  SYNTHETIC_WASTE_ROUTE,
  SYNTHETIC_ZONES_BLOCK,
)
from pylabrobot.formulatrix.mantis.driver.v3_arm_configuration import V3ArmConfiguration
from pylabrobot.formulatrix.mantis.resource_model import (
  PLATE_HOLDER_LOCATION,
  PLATE_HOLDER_SIZE_Y,
)
from pylabrobot.resources import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase

with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
  RECORDING = json.load(f)
# Where the recorded moves to idle (command 22) sent the arm motors, in device units.
RECORDED_IDLE_TARGETS = {
  0: struct.unpack("<d", bytes.fromhex("742631100e96943f"))[0],
  1: struct.unpack("<d", bytes.fromhex("c67d71e5c1efd63f"))[0],
}
# A synthetic arm block: the offsets those targets imply, round references, each encoder reading
# its own ideal value.
IDLE_ANGLES = compute_joint_angles(location=POST_HOMING_POSITION)
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

  def declare(self, *, arm_block: Optional[Dict[str, Any]], **blocks: Any) -> str:
    path = os.path.join(self.directory, f"declared_{len(os.listdir(self.directory))}.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, **({"arm": arm_block} if arm_block is not None else {}), **blocks}, f)
    return path

  async def set_up_arm(
    self,
    *,
    link: FakeLink,
    arm_block: Optional[Dict[str, Any]] = SYNTHETIC_ARM_BLOCK,
    **blocks: Any,
  ) -> MantisDriver:
    driver = await self.set_up_driver(
      link=link, declared_configuration_json=self.declare(arm_block=arm_block, **blocks)
    )
    assert driver.arm is not None
    arm = driver.arm

    def unmark_initialized() -> None:
      arm._initialized = False

    # Pushed after set_up_driver so LIFO runs it before driver shutdown: an arm a test brought up
    # is not parked when the test ends.
    self._stack.callback(unmark_initialized)
    self.sent_before = len(link.written)
    return driver

  def sent(self, link: FakeLink) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in link.written[self.sent_before :]]


@contextlib.contextmanager
def wait_briefly_for_unpower(*, driver: MantisDriver) -> Iterator[None]:
  """Only unpowering the arms (command 19), never answered, waits briefly: a slow runner cannot time
  out a command that is answered."""
  send_command = driver._send_command

  async def send(**kwargs: Any) -> bytes:
    if kwargs["command"] == 19:
      kwargs["timeout"] = 0.25
    return await send_command(**kwargs)

  with mock.patch.object(driver, "_send_command", side_effect=send):
    yield


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
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert self.sent(link)[-5:] == UNPOWER + reads
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

    with anyio.fail_after(10.0):
      with anyio.CancelScope() as scope:

        async def cancel_once_energised() -> None:
          await energised.wait()
          scope.cancel()

        async with anyio.create_task_group() as tg:
          tg.start_soon(cancel_once_energised)
          await driver.arm.initialize()
      await anyio.lowlevel.checkpoint()
    assert scope.cancelled_caught
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert self.sent(link)[-5:] == UNPOWER + reads
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
    with wait_briefly_for_unpower(driver=driver):
      with pytest.raises(MantisMotionError, match="moved to idle only from x"):
        await driver.arm.initialize()
    sent = self.sent(link)
    assert 22 not in [command for _, command, _ in sent]
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert sent[-5:] == UNPOWER + reads
    assert not driver.arm.is_initialized

  async def test_a_motor_error_while_z_homes_fails_at_once_and_unpowers_the_arms(self) -> None:
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    link = ArmLink(
      answers=make_answers(),
      events={(0, 21, home): [bytes.fromhex(RECORDED_MOTOR_ERROR_EVENTS[0])]},
    )
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    with wait_briefly_for_unpower(driver=driver):
      with pytest.raises(MantisMotionError, match="reported error 9") as caught:
        await driver.arm.initialize()
    assert (caught.value.motor, caught.value.code) == (0, 9)
    # Where the motors are, read as Z's home fails, then again after the arms are unpowered.
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    sent = self.sent(link)
    assert sent[-9][:2] == (0, 21)
    assert sent[-8:] == reads + UNPOWER + reads

  async def test_a_motor_that_slipped_stops_z_homing_before_it_starts(self) -> None:
    link = ArmLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    # After power-on, motor 1 read a following error while idle.
    link.answers[(0, 20, b"\x01\x00")] = [bytes.fromhex("8000")]
    with pytest.raises(MantisMotionError, match="motor 1 slipped"):
      await driver.arm._home_z()
    assert 21 not in [command for _, command, _ in self.sent(link)]

  async def test_cancelled_z_home_discards_waiter_and_shields_encoder_reads(self) -> None:
    link = ArmLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    waiting = anyio.Event()
    original_wait = driver._wait_for_event

    async def wait_and_signal(*, waiter: Any, timeout: float) -> bytes:
      """Signal once _home_z is waiting for HOME_DONE_EVENT, then block."""
      waiting.set()
      return await original_wait(waiter=waiter, timeout=timeout)

    with mock.patch.object(driver, "_wait_for_event", side_effect=wait_and_signal):
      with anyio.fail_after(5.0):
        with anyio.CancelScope() as scope:

          async def cancel_once_waiting() -> None:
            """Cancel _home_z while it awaits Z's HOME_DONE_EVENT."""
            await waiting.wait()
            scope.cancel()

          async with anyio.create_task_group() as tg:
            tg.start_soon(cancel_once_waiting)
            await driver.arm._home_z()
        await anyio.lowlevel.checkpoint()
    assert scope.cancelled_caught
    assert driver._event_waiters == []
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert self.sent(link)[-3:] == reads

  async def test_reads_convert_to_degrees_and_mm(self) -> None:
    link = ArmLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    joint_state = await driver.arm.request_joint_state()
    assert joint_state[0] == pytest.approx(-0.585047 * 1.8, abs=1e-5)
    assert joint_state[1] == pytest.approx(1.76374 * 1.8, abs=1e-4)
    assert joint_state[2] == 0.0
    absolute = await driver.arm.request_absolute_joint_state()
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
    # Arm._park() Z rise (0, 22) and sequencer start (0, 62) must not run when skip_arm=True.
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
          driver=mock.Mock(**{"configuration.generation": "v4"}),
          configuration=MantisArmConfiguration(
            offsets=offsets,
            homing_position_a=SYNTHETIC_HOMING_POSITION_A,
            encoder_correction={0: [], 1: []},
          ),
        )
        for motor, target in arm._get_idle_targets().items():
          expected = IDLE_ANGLES[motor] - offsets[motor]
          self.assertAlmostEqual(target, compute_device_position(motor=motor, position=expected))

  def test_the_implied_offsets_give_the_recorded_idle_targets(self) -> None:
    arm = Arm(
      driver=mock.Mock(**{"configuration.generation": "v4"}),
      configuration=MantisArmConfiguration(
        offsets=SYNTHETIC_OFFSETS,
        homing_position_a=SYNTHETIC_HOMING_POSITION_A,
        encoder_correction={0: [], 1: []},
      ),
    )
    targets = arm._get_idle_targets()
    self.assertAlmostEqual(targets[0], RECORDED_IDLE_TARGETS[0], places=12)
    self.assertEqual(targets[1], RECORDED_IDLE_TARGETS[1])

  def test_without_the_arm_block_there_are_no_idle_targets(self) -> None:
    arm = Arm(
      driver=mock.Mock(**{"configuration.generation": "v4"}), configuration=MantisArmConfiguration()
    )
    with self.assertRaises(MantisConfigurationError):
      arm._get_idle_targets()


class DefaultTests(unittest.TestCase):
  def test_defaults_are_checked_when_assigned(self) -> None:
    arm = Arm(
      driver=mock.Mock(**{"configuration.generation": "v4"}), configuration=MantisArmConfiguration()
    )
    with self.assertRaises(ValueError):
      arm.default_xy_speed = 0.0
    with self.assertRaises(ValueError):
      arm.default_xy_acceleration = 0.0
    with self.assertRaises(ValueError):
      arm.default_z_speed = 0.0
    # The xy defaults are mm/s and mm/s², None the route profile.
    arm.default_xy_speed = 100.0
    arm.default_xy_speed = None
    self.assertIsNone(arm.default_xy_speed)
    # Z's speed and acceleration stay within the recorded moves'.
    with self.assertRaises(ValueError):
      arm.default_z_speed = 56.0
    with self.assertRaises(ValueError):
      arm.default_z_acceleration = 100.0
    arm.default_z_acceleration = 300.0
    self.assertEqual(arm.default_z_acceleration, 300.0)


def get_arm_block_at(*, x: float, y: float) -> Dict[str, Any]:
  """The synthetic arm block, its references putting the last recorded readings at (x, y) mm."""
  homing_position_a = {}
  for motor, angle in enumerate(
    compute_joint_angles(location=MantisArmCoordinate(x=x, y=y, z=None))
  ):
    _, reading = struct.unpack("<Hd", bytes.fromhex(STARTUP_ABSOLUTE_ENCODER_REPLIES[motor][-1]))
    position = compute_device_position(motor=motor, position=angle - SYNTHETIC_OFFSETS[motor])
    homing_position_a[str(motor)] = (reading - position) % 200.0
  return {**SYNTHETIC_ARM_BLOCK, "homing_position_a": homing_position_a}


def get_away_arm_block() -> Dict[str, Any]:
  """The synthetic arm block, its references putting the last recorded readings at (-57, 0) mm."""
  return get_arm_block_at(x=-57.0, y=0.0)


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
    positions = [(0, 27, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert sent[4:25] == (
      statuses
      + positions
      + [(11, 30, struct.pack("<h", motor)) for motor in (0, 1)]
      # Z up on its own, read back by its encoder before the arms move.
      + [(0, 22, Z_UP_MOVE), (0, 20, b"\x02\x00")]
      + statuses
      + [(0, 84, b"\x02\x00")]
      + [(0, 61, b""), (0, 62, b"")]
      + [(0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[item])) for item in (14, 15)]
      + [(0, 61, b""), (0, 62, b""), (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[16]))]
    )
    assert sent[25][:2] == (12, 33)
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
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert after_queued[:7] == HALT + reads
    assert after_queued[7][:2] == (12, 33)
    assert after_queued[-2:] == TURN_OFF
    assert not driver.arm.is_initialized

  async def test_exit_with_a_chip_on_the_head_parks_cleans_up_closes_and_raises(self) -> None:
    link = self.close_link()
    driver = self.make_driver(
      link=link, declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK)
    )
    with pytest.raises(MantisMotionError, match="a chip is still on the head"):
      async with driver.lifespan(skip_arm=True):
        assert driver.arm is not None
        since = len(link.written)
        driver.arm._initialized = True
        driver.arm._attached_chip = 1
        driver._next_item = 14  # numbered as in the recorded close
    sent = [parse_frame(frame=frame)[1:] for frame in link.written[since:]]
    assert sent[:4] == HALT
    assert (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[16])) in sent
    assert sent[25][:2] == (12, 33)
    assert sent[-2:] == TURN_OFF
    assert not driver._connected
    assert link.io.exited_count == 1

    link_accepted = self.close_link()
    driver_accepted = self.make_driver(
      link=link_accepted,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    async with driver_accepted.lifespan(skip_arm=True, accept_chip_left_on_head=True):
      assert driver_accepted.arm is not None
      since_accepted = len(link_accepted.written)
      driver_accepted.arm._initialized = True
      driver_accepted.arm._attached_chip = 1
      driver_accepted._next_item = 14
    sent_accepted = [
      parse_frame(frame=frame)[1:] for frame in link_accepted.written[since_accepted:]
    ]
    assert sent_accepted[-2:] == TURN_OFF
    assert not driver_accepted._connected
    assert link_accepted.io.exited_count == 1

  async def test_cancelled_or_uninitialized_exit_with_chip_on_head_does_not_raise_motion_error(
    self,
    caplog: pytest.LogCaptureFixture,
  ) -> None:
    link_cancelled = self.close_link()
    driver_cancelled = self.make_driver(
      link=link_cancelled,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.master"):
      with anyio.CancelScope() as scope:
        async with driver_cancelled.lifespan(skip_arm=True):
          assert driver_cancelled.arm is not None
          since_cancelled = len(link_cancelled.written)
          driver_cancelled.arm._initialized = True
          driver_cancelled.arm._attached_chip = 1
          driver_cancelled._next_item = 14
          scope.cancel()
          await anyio.sleep_forever()
    assert scope.cancelled_caught
    assert "chip from slot 1 still on the head" in caplog.text
    sent_cancelled = [
      parse_frame(frame=frame)[1:] for frame in link_cancelled.written[since_cancelled:]
    ]
    assert sent_cancelled[:4] == HALT
    assert (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[16])) in sent_cancelled
    assert sent_cancelled[25][:2] == (12, 33)
    assert sent_cancelled[-2:] == TURN_OFF
    assert not driver_cancelled._connected
    assert link_cancelled.io.exited_count == 1

    link_skipped = self.close_link()
    driver_skipped = self.make_driver(
      link=link_skipped,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    async with driver_skipped.lifespan(skip_arm=True):
      assert driver_skipped.arm is not None
      assert not driver_skipped.arm.is_initialized
      driver_skipped.arm._attached_chip = 1
    assert not driver_skipped._connected

    link_lost = self.close_link()
    driver_lost = self.make_driver(
      link=link_lost,
      declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK),
    )
    async with driver_lost.lifespan(skip_arm=True):
      assert driver_lost.arm is not None
      driver_lost.arm._initialized = True
      driver_lost.arm._attached_chip = 1
      driver_lost._link_error = OSError("link lost")
    assert not driver_lost._connected

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
    assert "Z reads 20.00 mm below its flag" in caplog.text
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
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert sent[:7] == HALT + reads
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
        await driver.arm._move_motors(
          targets=RECORDED_IDLE_TARGETS, profiles=driver.arm._get_route_profiles()
        )
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
      with anyio.fail_after(10.0):
        with anyio.CancelScope() as scope:

          async def cancel_once_moving() -> None:
            await moving.wait()
            await anyio.sleep(0.03)
            scope.cancel()

          async with anyio.create_task_group() as tg:
            tg.start_soon(cancel_once_moving)
            await driver.arm._move_motors(
              targets=RECORDED_IDLE_TARGETS, profiles=driver.arm._get_route_profiles()
            )
        await anyio.lowlevel.checkpoint()
    assert scope.cancelled_caught
    non_status = [command for command in self.sent(link) if command[1] != 20]
    assert HALT[1] in non_status
    assert HALT[2] in non_status
    assert any("by their encoders" in rec.getMessage() for rec in caplog.records)


class TestDiscovery(ArmTestBase):
  async def test_board_11_selects_the_arm_implementation(self) -> None:
    for absent_boards, generation in (((), "v4"), ((11,), "v3")):
      self.default_read_timeout = 0.25 if absent_boards else 5.0  # an absent board never answers
      driver = await self.set_up_driver(
        link=FakeLink(answers=make_answers(absent_boards=absent_boards))
      )
      assert driver.arm is not None
      assert type(driver.arm) is Arm
      assert driver.arm._get_generation() == generation

  async def test_the_arm_block_is_never_taken_from_a_recording(self) -> None:
    path = os.path.join(self.directory, "recording.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, "arm": SYNTHETIC_ARM_BLOCK}, f)
    with mock.patch.object(master_module, "RECORDING_MANTIS_V4", path):
      driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert driver.arm is not None
    assert driver.arm.configuration.encoder_correction is None
    assert driver._unit_blocks["rfid"] == RECORDING["rfid"]

  async def test_a_declared_arm_block_is_read_and_saved(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    assert driver.arm.configuration.homing_position_a == SYNTHETIC_HOMING_POSITION_A
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
  angles = compute_joint_angles(location=MantisArmCoordinate(x=x, y=y, z=None))
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


class TestZone(ArmTestBase):
  async def set_up_routes(
    self,
    *,
    link: FakeLink,
    arm_block: Optional[Dict[str, Any]] = SYNTHETIC_ARM_BLOCK,
    **blocks: Any,
  ) -> MantisDriver:
    blocks = {
      "zones": SYNTHETIC_ZONES_BLOCK,
      "approach_paths": SYNTHETIC_APPROACH_PATHS_BLOCK,
      **blocks,
    }
    return await self.set_up_arm(link=link, arm_block=arm_block, **blocks)

  async def test_from_the_front_zone_the_recorded_startup_is_sent(self) -> None:
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    link = ArmLink(
      answers=make_answers(), events={(0, 21, home): [bytes.fromhex(STARTUP_HOME_DONE)]}
    )
    driver = await self.set_up_routes(link=link)
    assert driver.arm is not None
    await driver.arm.initialize()
    assert tokens(self.sent(link)) == get_expected_startup()

  async def test_from_another_zone_the_arms_follow_its_routes(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    home = bytes.fromhex(RECORDED_STARTUP[-1].split(":")[2])
    link = RouteLink(
      answers=make_answers(),
      events={(0, 21, home): [bytes.fromhex(STARTUP_HOME_DONE)]},
      positions=AWAY_POSITION_REPLIES,
    )
    driver = await self.set_up_routes(link=link)
    assert driver.arm is not None
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pylabrobot.formulatrix.mantis.driver.features.arm"):
      await driver.arm.initialize()
    assert any("in the LeftMiddle zone" in rec.getMessage() for rec in caplog.records)
    sent = self.sent(link)
    moves = [payload for _, command, payload in sent if command == 22]
    safe_xy = [[*SYNTHETIC_SAFE_XY["LeftMiddle"], None]]
    idle = SYNTHETIC_IDLE_ROUTES["LeftMiddle"]
    assert moves == get_route_moves(route=safe_xy + idle + idle)
    # The route to safe XY, Z homed, the routes to idle, Z homed again.
    assert [command for _, command, _ in sent if command in (21, 22)][2:3] == [21]
    assert [command for _, command, _ in sent][-1] == 20
    assert driver.arm.is_initialized

  async def test_without_zones_a_head_away_from_idle_still_refuses(self) -> None:
    link = RouteLink(answers=make_answers(), positions=AWAY_POSITION_REPLIES)
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    with wait_briefly_for_unpower(driver=driver):
      with pytest.raises(MantisMotionError, match="moved to idle only from x"):
        await driver.arm.initialize()
    assert 22 not in [command for _, command, _ in self.sent(link)]

  async def test_recover_from_fault_stops_clears_and_recovers_as_recorded(self) -> None:
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
    assert driver.arm is not None
    driver.arm._initialized = True
    await driver.arm.recover_from_fault()
    startup = get_expected_startup()
    tail = startup[startup.index("0:17:0200") + 1 :]
    assert tokens(self.sent(link)) == (
      [token(command) for command in HALT[1:]] + ["0:17:0200", "0:17:0000", "0:17:0100"] + tail
    )
    assert driver.arm.is_initialized

  async def test_a_failed_recover_from_fault_unpowers_the_arms_then_reads_where_they_are(
    self,
  ) -> None:
    link = RaisingLink(answers=make_answers(), raises_on=(0, 17, b"\x02\x00"))
    driver = await self.set_up_routes(link=link)
    assert driver.arm is not None
    driver.arm._initialized = True
    with wait_briefly_for_unpower(driver=driver):
      with pytest.raises(OSError, match="device unplugged"):
        await driver.arm.recover_from_fault()
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert self.sent(link)[-6:] == [(0, 17, b"\x02\x00")] + UNPOWER + reads
    assert not driver.arm.is_initialized

  async def test_cancelled_recover_from_fault_shields_unpower_and_encoder_reads(self) -> None:
    link = RouteLink(answers=make_answers())
    for motor in (0, 1):
      link.answers[(0, 19, bytes.fromhex(f"0{motor}000100"))] = [b"\x00\x00"]
    driver = await self.set_up_routes(link=link)
    assert driver.arm is not None
    driver.arm._initialized = True
    cleared = anyio.Event()
    write = link.write

    async def write_and_signal(data: bytes) -> int:
      _, board, command, payload = parse_frame(frame=data)
      if (board, command, payload) == (0, 17, b"\x02\x00"):
        cleared.set()
      return await write(data)

    link.io.write.side_effect = write_and_signal
    with anyio.fail_after(10.0):
      with anyio.CancelScope() as scope:

        async def cancel_once_cleared() -> None:
          await cleared.wait()
          scope.cancel()

        async with anyio.create_task_group() as tg:
          tg.start_soon(cancel_once_cleared)
          await driver.arm.recover_from_fault()
      await anyio.lowlevel.checkpoint()
    assert scope.cancelled_caught
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    assert self.sent(link)[-5:] == UNPOWER + reads
    assert not driver.arm.is_initialized

  async def test_recover_from_fault_refuses_an_arm_not_brought_up(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_routes(link=link)
    assert driver.arm is not None
    with pytest.raises(MantisMotionError, match="not brought up"):
      await driver.arm.recover_from_fault()
    assert self.sent(link) == []

  async def test_the_head_is_classified_from_its_forward_kinematics(self) -> None:
    driver = await self.set_up_routes(link=RouteLink(answers=make_answers()))
    assert driver.arm is not None
    arm = driver.arm
    for zone, (x, y) in SYNTHETIC_SAFE_XY.items():
      if zone in ("Front", "Unknown"):
        continue
      angles = compute_joint_angles(location=MantisArmCoordinate(x=x, y=y, z=None))
      joint_state = {motor: angles[motor] - SYNTHETIC_OFFSETS[motor] for motor in (0, 1)}
      _, route = arm._get_routes(joint_state=joint_state)
      assert route == [
        MantisArmCoordinate(x=rx, y=ry, z=rz) for rx, ry, rz in SYNTHETIC_IDLE_ROUTES[zone]
      ]
    assert arm._get_zone(location=MantisArmCoordinate(x=15.0, y=20.0, z=None)) == "Unknown"


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


class TestMove(ArmTestBase):
  async def set_up_moves(self, *, link: FakeLink, **blocks: Any) -> MantisDriver:
    driver = await self.set_up_arm(link=link, **{"stage": TRANSLATED_STAGE_BLOCK, **blocks})
    assert driver.arm is not None
    driver.arm._initialized = True
    return driver

  def set_traverse_at_flag(self, *, driver: MantisDriver) -> None:
    """The traverse height at Z's flag, where its encoder is recorded; the deck frame's conversion
    is tested on its own."""
    assert driver.arm is not None
    driver.arm._get_traverse_z_joint_position = (  # type: ignore[method-assign]
      lambda **_: 0.0
    )

  async def test_move_to_xy_position_moves_the_arms_in_one_queued_move_z_held(self) -> None:
    queued = get_queued_move(item=15, targets=DECK_POINT_ARM_TARGETS)
    link = RouteLink(
      answers=make_answers(),
      encoders={2: Z_UP_ENCODER_REPLY},
      events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]},
    )
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    driver._next_item = 15
    self.set_traverse_at_flag(driver=driver)
    await driver.arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1])
    # Where it stands, then Z by its encoder at the traverse height already: no rise.
    assert tokens(self.sent(link)) == [
      "0:27:0000",
      "0:27:0100",
      "0:27:0200",
      "0:84:0200",
      "0:61:",
      "0:62:",
      f"0:68:{queued.hex()}",
    ]

  async def test_a_move_across_from_below_the_traverse_height_rises_first(self) -> None:
    queued = get_queued_move(item=15, targets=DECK_POINT_ARM_TARGETS)
    link = RouteLink(
      answers=make_answers(),
      encoders={2: Z_RISING_ENCODER_REPLIES},
      events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]},
    )
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    driver._next_item = 15
    self.set_traverse_at_flag(driver=driver)
    await driver.arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1])
    # Z read down, up alone to the traverse height and read back there before the arms move.
    assert tokens(self.sent(link)) == (
      ["0:27:0000", "0:27:0100", "0:27:0200", "0:84:0200", f"0:22:{Z_UP_MOVE.hex()}"]
      + ["0:84:0200", "0:61:", "0:62:", f"0:68:{queued.hex()}"]
    )

  async def test_move_to_z_position_moves_z_alone_to_the_height_above_the_deck(self) -> None:
    target = compute_device_position(motor=2, position=REFERENCE_HEIGHT_Z)
    queued = get_queued_move(item=15, targets={2: target})
    link = RouteLink(
      answers=make_answers(), events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]}
    )
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    driver._next_item = 15
    await driver.arm.move_to_z_position(z=REFERENCE_HEIGHT)
    assert tokens(self.sent(link)) == [
      "0:27:0000",
      "0:27:0100",
      "0:27:0200",
      "0:61:",
      "0:62:",
      f"0:68:{queued.hex()}",
    ]

  async def test_moves_out_of_range_are_refused_before_anything_is_sent(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    arm = driver.arm
    with pytest.raises(ValueError, match="Z speed must be from 1.0 to 55.0"):
      await arm.move_to_z_position(z=REFERENCE_HEIGHT, speed=60.0)
    with pytest.raises(ValueError, match="Z acceleration must be from 200.0 to 500.0"):
      await arm.move_to_z_position(z=REFERENCE_HEIGHT, acceleration=1000.0)
    with pytest.raises(ValueError, match="acceleration must be finite and above 0"):
      await arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1], acceleration=0.0)
    with pytest.raises(MantisKinematicsError):
      await arm.move_to_xy_position(x=500.0, y=500.0)
    with pytest.raises(ValueError, match="the motors are"):
      await arm.move_to_joint_state(joint_state={3: 0.0})
    with pytest.raises(ValueError, match="outside its limits"):
      await arm.move_to_joint_state(joint_state={0: 8000.0})
    assert self.sent(link) == []

  async def test_a_height_outside_zs_limits_moves_nothing(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    with pytest.raises(ValueError, match="outside its limits"):
      await driver.arm.move_to_z_position(z=-100.0)
    assert tokens(self.sent(link)) == ["0:27:0000", "0:27:0100", "0:27:0200"]

  async def test_moves_refuse_an_arm_not_brought_up(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_arm(link=link, stage=TRANSLATED_STAGE_BLOCK)
    assert driver.arm is not None
    with pytest.raises(MantisMotionError, match="not brought up"):
      await driver.arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1])
    with pytest.raises(MantisMotionError, match="not brought up"):
      await driver.arm.move_to_z_position(z=REFERENCE_HEIGHT)
    with pytest.raises(MantisMotionError, match="not brought up"):
      await driver.arm.park()
    assert self.sent(link) == []

  async def test_without_the_stage_block_the_deck_frame_moves_send_nothing(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    driver.arm._initialized = True
    with pytest.raises(MantisConfigurationError, match="no stage transform"):
      await driver.arm.move_to_xy_position(x=DECK_POINT[0], y=DECK_POINT[1])
    with pytest.raises(MantisConfigurationError, match="no stage transform"):
      await driver.arm.move_to_z_position(z=REFERENCE_HEIGHT)
    with pytest.raises(MantisConfigurationError, match="no stage transform"):
      await driver.arm.request_pose()
    assert self.sent(link) == []

  async def test_is_xy_reachable_takes_the_deck_frame_and_sends_nothing(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    assert driver.arm.is_xy_reachable(x=DECK_POINT[0], y=DECK_POINT[1])
    assert not driver.arm.is_xy_reachable(x=500.0, y=500.0)
    assert self.sent(link) == []

  async def test_is_xy_reachable_without_the_stage_block_raises_and_sends_nothing(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
    with pytest.raises(MantisConfigurationError, match="no stage transform"):
      driver.arm.is_xy_reachable(x=DECK_POINT[0], y=DECK_POINT[1])
    assert self.sent(link) == []

  async def test_a_joint_move_holds_the_motors_it_does_not_name(self) -> None:
    held_z_up = bytes.fromhex(CLOSE_QUEUED_MOVES[13])  # the arms held, Z to 0
    queued = struct.pack("<h", 15) + held_z_up[2:]
    link = RouteLink(
      answers=make_answers(), events={(0, 68, queued): [bytes.fromhex(CLOSE_ITEM_PLAYED[15])]}
    )
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    driver._next_item = 15
    await driver.arm.move_to_joint_state(joint_state={2: 0.0})
    # Where it stands is read first (on an approach path, it would leave along it).
    assert tokens(self.sent(link)) == [
      "0:27:0000",
      "0:27:0100",
      "0:27:0200",
      "0:61:",
      "0:62:",
      f"0:68:{queued.hex()}",
    ]

  async def test_a_queued_move_not_played_in_time_aborts_and_stops(self) -> None:
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    with mock.patch.object(arm_module, "MOVE_TIMEOUT", 0.05):
      with pytest.raises(MantisTimeoutError):
        await driver.arm.move_to_joint_state(joint_state={2: 0.0})
    sent = self.sent(link)
    assert [command for _, command, _ in sent[:6]] == [27, 27, 27, 61, 62, 68]
    # Aborted and every motor stopped, as the recorded halt, so the item playing ends; then where
    # the motors stopped, logged.
    assert sent[6:10] == HALT
    assert [command for _, command, _ in sent[10:]] == [84, 84, 84]

  async def test_a_cancelled_queued_move_aborts_and_stops(self) -> None:
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_moves(link=link)
    assert driver.arm is not None
    waiting = anyio.Event()
    wait_for_queue_item = driver._wait_for_queue_item

    async def wait_and_say_so(*, item: int, timeout: float) -> None:
      waiting.set()
      await wait_for_queue_item(item=item, timeout=timeout)

    with mock.patch.object(driver, "_wait_for_queue_item", side_effect=wait_and_say_so):
      with anyio.fail_after(10.0):
        with anyio.CancelScope() as scope:

          async def cancel_once_waiting() -> None:
            await waiting.wait()
            scope.cancel()

          async with anyio.create_task_group() as tg:
            tg.start_soon(cancel_once_waiting)
            await driver.arm.move_to_joint_state(joint_state={2: 0.0})
        await anyio.lowlevel.checkpoint()
    assert scope.cancelled_caught
    sent = self.sent(link)
    assert sent[6:10] == HALT
    assert [command for _, command, _ in sent[10:]] == [84, 84, 84]

  async def test_move_to_location_rises_moves_across_then_lowers(self) -> None:
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
    assert driver.arm is not None
    driver._next_item = 15
    self.set_traverse_at_flag(driver=driver)
    await driver.arm.move_to_location(location=Coordinate(*DECK_POINT, REFERENCE_HEIGHT))
    # As move_to_xy_position, then move_to_z_position, which reads where the head stands.
    assert tokens(self.sent(link)) == (
      ["0:27:0000", "0:27:0100", "0:27:0200", "0:84:0200", f"0:22:{Z_UP_MOVE.hex()}"]
      + ["0:84:0200", "0:61:", "0:62:", f"0:68:{arms.hex()}"]
      + ["0:27:0000", "0:27:0100", "0:27:0200", "0:61:", "0:62:", f"0:68:{z.hex()}"]
    )


def make_staged_arm(*, stage: Dict[str, Any], **kwargs: Any) -> Arm:
  """An arm with a mocked driver and a stage block, as a driver builds it."""
  return Arm(
    driver=mock.Mock(**{"configuration.generation": "v4"}),
    configuration=MantisArmConfiguration(
      offsets=kwargs.get("offsets"),
      homing_position_a=kwargs.get("homing_position_a"),
      encoder_correction=kwargs.get("encoder_correction"),
      stage_origin=(
        float(stage["origin"][0]),
        float(stage["origin"][1]),
        float(stage["origin"][2]),
      ),
      stage_transformation_matrix=stage["transformation_matrix"],
      stage_offset_x=(
        float(stage["offset_x"][0]),
        float(stage["offset_x"][1]),
        float(stage["offset_x"][2]),
      ),
      stage_offset_y=(
        float(stage["offset_y"][0]),
        float(stage["offset_y"][1]),
        float(stage["offset_y"][2]),
      ),
      arm_type_rotation_degree=stage["arm_type_rotation_degree"],
      arm_type_translation=(
        float(stage["arm_type_translation"][0]),
        float(stage["arm_type_translation"][1]),
        float(stage["arm_type_translation"][2]),
      ),
      stage_reference_height=stage["reference_height"],
    ),
  )


class TestDeckFrame(AnyioTestBase):
  def test_off_the_plate_holder_a_rigid_stage_is_fitted_exactly(self) -> None:
    # A rigid stage turns and moves the wellplate frame; the resource model only moves it, so the
    # fit is that turn, and a point anywhere on the deck lands where the stage would put it.
    stage = {
      **TRANSLATED_STAGE_BLOCK,
      "arm_type_rotation_degree": 1.5,
      "arm_type_translation": [2.0, -3.0, 0.0],
    }
    arm = make_staged_arm(stage=stage)
    turn, _, _ = arm._get_deck_to_arm_transform()
    assert math.degrees(turn) == pytest.approx(1.5, abs=1e-9)
    angle = math.radians(1.5)
    for x, y in ((0.0, 0.0), (180.0, 150.0), (-40.0, 120.0)):
      # The point in the wellplate frame, then turned and moved as the stage does.
      holder_x = x - PLATE_HOLDER_LOCATION.x
      holder_y = PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - y
      expected = (
        holder_x * math.cos(angle) - holder_y * math.sin(angle) + 2.0 - 50.0,
        holder_x * math.sin(angle) + holder_y * math.cos(angle) - 3.0 + 40.0,
      )
      arm_x, arm_y = arm._get_arm_frame_xy(x=x, y=y)
      assert arm_x == pytest.approx(expected[0], abs=1e-9)
      assert arm_y == pytest.approx(expected[1], abs=1e-9)
      deck_x, deck_y = arm._get_deck_frame_xy(x=arm_x, y=arm_y)
      assert deck_x == pytest.approx(x, abs=1e-9)
      assert deck_y == pytest.approx(y, abs=1e-9)
      arm_frame_location = arm._get_arm_frame_location(location=Coordinate(x=x, y=y, z=5.0))
      location = arm._get_deck_frame_location(arm_frame_location=arm_frame_location)
      assert location == Coordinate(x=x, y=y, z=5.0)

  def test_over_the_plate_holder_a_point_reads_back_where_it_was_sent(self) -> None:
    # The projective stage is inverted exactly there, not through the fit.
    arm = make_staged_arm(stage=SYNTHETIC_STAGE_BLOCK)
    for holder_x, holder_y in ((0.0, 0.0), (14.3, 11.28), (127.0, 85.0), (64.0, 40.0)):
      x = PLATE_HOLDER_LOCATION.x + holder_x
      y = PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - holder_y
      arm_frame_location = arm._get_arm_frame_location(location=Coordinate(x=x, y=y, z=5.0))
      location = arm._get_deck_frame_location(arm_frame_location=arm_frame_location)
      assert location.x == pytest.approx(x, abs=1e-9)
      assert location.y == pytest.approx(y, abs=1e-9)
      assert location.z == pytest.approx(5.0, abs=1e-9)

  def test_an_xy_speed_scales_each_motor_by_its_turn(self) -> None:
    # 100 mm/s over the straight line: each motor's speed at its turn over the distance, so both
    # run the move in the same time; 500 mm/s² on both as the motor that turns most needs it.
    arm = Arm(
      driver=mock.Mock(**{"configuration.generation": "v4"}),
      configuration=MantisArmConfiguration(
        offsets={0: 0.0, 1: 0.0},
        homing_position_a=SYNTHETIC_HOMING_POSITION_A,
        encoder_correction={0: [], 1: []},
      ),
    )
    start_angles = compute_joint_angles(location=MantisArmCoordinate(x=15.0, y=31.177, z=None))
    target_angles = compute_joint_angles(location=MantisArmCoordinate(x=-20.0, y=70.0, z=None))
    start = {0: start_angles[0], 1: start_angles[1], 2: 0.0}
    target = {0: target_angles[0], 1: target_angles[1]}
    distance = math.hypot(-20.0 - 15.0, 70.0 - 31.177)
    turns = {motor: abs(target[motor] - start[motor]) for motor in (0, 1)}
    largest = max(turns.values())
    profiles = arm._get_arm_profiles(
      start=start, target=target, xy_speed=100.0, xy_acceleration=500.0
    )
    for motor in (0, 1):
      expected = (
        compute_device_rate(motor=motor, rate=100.0 * turns[motor] / distance),
        compute_device_rate(motor=motor, rate=500.0 * largest / distance),
      )
      for value, expected_value in zip(profiles[motor], expected):
        assert value == pytest.approx(expected_value, abs=1e-9)
    route = arm._get_arm_profiles(start=None, target=target, xy_speed=None, xy_acceleration=None)
    assert route[0] == (ARM_PROFILE_SPEED, ARM_PROFILE_ACCELERATION)
    # Given one, a speed not given is the route's scaled by turn; an acceleration not given is the
    # route's on both motors, never scaled down.
    for given in ({"xy_speed": 100.0}, {"xy_acceleration": 500.0}):
      profiles = arm._get_arm_profiles(
        start=start, target=target, **{"xy_speed": None, "xy_acceleration": None, **given}
      )
      for motor in (0, 1):
        turn = turns[motor]
        expected = (
          100.0 * turn / distance if "xy_speed" in given else 10000.0 * turn / largest,
          500.0 * largest / distance if "xy_acceleration" in given else 1500.0,
        )
        for value, rate in zip(profiles[motor], expected):
          assert value == pytest.approx(compute_device_rate(motor=motor, rate=rate), abs=1e-9)

  def test_a_motor_that_barely_turns_keeps_a_full_acceleration(self) -> None:
    # On the device a queued move at 18.2 device units/s² was refused (error 6): a motor turning
    # 0.25 du against the other's 11.66 never gets a scaled-down acceleration.
    arm = Arm(
      driver=mock.Mock(**{"configuration.generation": "v4"}),
      configuration=MantisArmConfiguration(
        offsets={0: 0.0, 1: 0.0},
        homing_position_a=SYNTHETIC_HOMING_POSITION_A,
        encoder_correction={0: [], 1: []},
      ),
    )
    start_angles = compute_joint_angles(location=MantisArmCoordinate(x=15.0, y=31.177, z=None))
    start = {0: start_angles[0], 1: start_angles[1], 2: 0.0}
    target = {
      0: start[0] + compute_position(motor=0, device_position=0.25),
      1: start[1] + compute_position(motor=1, device_position=11.66),
    }
    for given in ({"xy_speed": 50.0}, {"xy_speed": 50.0, "xy_acceleration": 200.0}, {}):
      profiles = arm._get_arm_profiles(
        start=start, target=target, **{"xy_speed": None, "xy_acceleration": None, **given}
      )
      assert profiles[0][1] == profiles[1][1]
      if "xy_acceleration" not in given:
        assert profiles[0][1] == ARM_PROFILE_ACCELERATION

  def test_over_the_plate_holder_the_stage_transform_places_the_nozzle(self) -> None:
    # The holder's back-left corner is the wellplate frame's origin.
    arm = make_staged_arm(stage=SYNTHETIC_STAGE_BLOCK)
    corner = arm._get_arm_frame_location(
      location=Coordinate(
        x=PLATE_HOLDER_LOCATION.x, y=PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y, z=0.0
      )
    )
    assert corner.x is not None and corner.y is not None
    assert corner.x == pytest.approx(
      (-50.0 * 1.001 + 40.0 * 0.001 - 0.08) / (-50.0 * -2e-05 + 40.0 * -3e-05 + 1.004),
      abs=1e-7,
    )
    assert corner.y == pytest.approx(
      (-50.0 * -0.004 + 40.0 * 0.9995 + 0.09) / (-50.0 * -2e-05 + 40.0 * -3e-05 + 1.004),
      abs=1e-7,
    )

  def test_a_height_above_the_deck_takes_the_reference_height_and_the_tilt(self) -> None:
    # 10 mm right of and 20 mm in front of the holder's back-left corner, 5 mm above the deck:
    # 51 + (9.665 - 5) - 0.0015 * 10 + 0.0025 * 20 = 55.7 mm down.
    arm = make_staged_arm(stage=SYNTHETIC_STAGE_BLOCK)
    arm_frame_location = arm._get_arm_frame_location(
      location=Coordinate(
        x=PLATE_HOLDER_LOCATION.x + 10.0,
        y=PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - 20.0,
        z=5.0,
      )
    )
    assert arm_frame_location.z is not None
    assert arm_frame_location.z == pytest.approx(55.7, abs=1e-7)

  async def test_request_pose_places_the_nozzle_in_the_deck_frame(self) -> None:
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

    async def fake_request_joint_state() -> Dict[int, float]:
      return joints

    with mock.patch.object(arm, "request_joint_state", new=fake_request_joint_state):
      pose = await arm.request_pose()
    # The translated stage puts the wellplate frame's origin at (-50, 40) in the arm frame.
    x, y = POST_HOMING_POSITION.x, POST_HOMING_POSITION.y
    assert x is not None and y is not None
    assert pose.nozzle_location.x == pytest.approx(PLATE_HOLDER_LOCATION.x + x + 50.0, abs=1e-7)
    assert pose.nozzle_location.y == pytest.approx(
      PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - (y - 40.0), abs=1e-7
    )
    # Z 10 mm down from its flag: 51 + 9.665 - 10 above the deck.
    assert pose.nozzle_location.z == pytest.approx(50.665, abs=1e-7)
    assert (pose.head_joint_location.x, pose.head_joint_location.y) == (
      pose.nozzle_location.x,
      pose.nozzle_location.y,
    )
    assert pose.joints == joints


# Each arm motor's encoder (command 84) as it stands at the recorded idle targets, in device units.
IDLE_ENCODER_REPLIES = {
  motor: struct.pack("<d", RECORDED_IDLE_TARGETS[motor]).hex() for motor in (0, 1)
}


class TestPose(ArmTestBase):
  async def set_up_pose(self, *, link: FakeLink) -> MantisDriver:
    driver = await self.set_up_arm(link=link)
    assert driver.arm is not None
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

  async def test_a_move_passes_on_the_pose_its_encoders_read_after_it(self) -> None:
    link = self.make_z_up_link()
    driver = await self.set_up_pose(link=link)
    assert driver.arm is not None
    observer = mock.Mock()
    driver.arm._pose_observer = observer
    await driver.arm.move_to_joint_state(joint_state={2: 0.0})
    sent = [command for command in self.sent(link) if command[1] not in (20, 27)]
    assert sent[3:] == [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    pose = observer.call_args.kwargs
    assert pose["theta_1"] == pytest.approx(IDLE_ANGLES[0], abs=1e-7)
    assert pose["theta_2"] == pytest.approx(IDLE_ANGLES[1], abs=1e-7)
    assert pose["z"] == pytest.approx(-19.996, abs=1e-3)

  async def test_a_failed_move_passes_on_where_it_stopped_with_no_more_reads(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    second = struct.pack("<hd", 1, RECORDED_IDLE_TARGETS[1]) + ARM_PROFILE
    link = RaisingLink(answers=make_answers(), raises_on=(0, 22, second))
    for motor, reply in {**IDLE_ENCODER_REPLIES, 2: Z_UP_ENCODER_REPLY}.items():
      link.answers[(0, 84, struct.pack("<h", motor))] = [bytes.fromhex(reply)]
    driver = await self.set_up_pose(link=link)
    assert driver.arm is not None
    observer = mock.Mock()
    driver.arm._pose_observer = observer
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.arm"
    ):
      with pytest.raises(OSError, match="device unplugged"):
        await driver.arm._move_motors(
          targets=RECORDED_IDLE_TARGETS, profiles=driver.arm._get_route_profiles()
        )
    assert [command for _, command, _ in self.sent(link)].count(84) == 3
    observer.assert_called_once()

  async def test_a_failed_model_update_only_warns(self, caplog: pytest.LogCaptureFixture) -> None:
    link = self.make_z_up_link()
    driver = await self.set_up_pose(link=link)
    assert driver.arm is not None
    driver.arm._pose_observer = mock.Mock(side_effect=RuntimeError("model"))
    caplog.clear()
    with caplog.at_level(
      logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.features.arm"
    ):
      await driver.arm.move_to_joint_state(joint_state={2: 0.0})
    assert any("pose was not passed on" in rec.getMessage() for rec in caplog.records)


class TestPark(ArmTestBase):
  async def set_up_park(
    self, *, link: RouteLink, zones: bool = True, traverse_at_flag: bool = True
  ) -> MantisDriver:
    """An arm away from idle; the traverse height at Z's flag, where its encoder is recorded."""
    blocks = {"zones": SYNTHETIC_ZONES_BLOCK} if zones else {}
    link.set_absolute_readings(index=-1)
    driver = await self.set_up_arm(link=link, arm_block=get_away_arm_block(), **blocks)
    assert driver.arm is not None
    driver.arm._initialized = True
    if traverse_at_flag:
      # The traverse height at Z's flag, where its encoder is recorded; the deck frame's conversion
      # is tested on its own.
      driver.arm._get_traverse_z_joint_position = (  # type: ignore[method-assign]
        lambda **_: 0.0
      )
    return driver

  async def test_park_follows_the_zones_route_to_idle(self) -> None:
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_park(link=link)
    assert driver.arm is not None
    await driver.arm.park(with_model_updates=False)
    sent = self.sent(link)
    assert [(board, command) for board, command, _ in sent[:9]] == (
      [(0, 27)] * 3 + [(0, 20), (0, 20), (0, 20), (11, 30), (11, 30), (0, 84)]
    )
    moves = [payload for _, command, payload in sent if command == 22]
    assert moves == get_route_moves(route=SYNTHETIC_IDLE_ROUTES["LeftMiddle"])

  async def test_a_head_below_the_traverse_height_rises_first(self) -> None:
    link = RouteLink(answers=make_answers(), encoders={2: Z_RISING_ENCODER_REPLIES})
    driver = await self.set_up_park(link=link)
    assert driver.arm is not None
    await driver.arm.park(with_model_updates=False)
    sent = self.sent(link)
    moves = [payload for _, command, payload in sent if command == 22]
    assert moves[0] == Z_UP_MOVE
    assert moves[1:] == get_route_moves(route=SYNTHETIC_IDLE_ROUTES["LeftMiddle"])
    rise = sent.index((0, 22, moves[0]))
    assert [command for _, command, _ in sent[rise:] if command != 20][:2] == [22, 84]

  async def test_a_rise_that_does_not_reach_the_traverse_height_is_not_parked(self) -> None:
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_park(link=link, traverse_at_flag=False)
    assert driver.arm is not None
    with pytest.raises(MantisMotionError, match="the arms are not moved"):
      await driver.arm.park()
    assert [command for _, command, _ in self.sent(link)].count(22) == 1

  async def test_routes_keep_the_recorded_profile_whatever_the_defaults(self) -> None:
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_park(link=link)
    assert driver.arm is not None
    driver.arm.default_xy_speed = 100.0
    driver.arm.default_z_speed = 30.0
    await driver.arm.park(with_model_updates=False)
    moves = [payload for _, command, payload in self.sent(link) if command == 22]
    assert moves == get_route_moves(route=SYNTHETIC_IDLE_ROUTES["LeftMiddle"])

  async def test_without_zones_a_head_away_from_idle_is_not_parked(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_park(link=link, zones=False)
    assert driver.arm is not None
    with pytest.raises(MantisMotionError, match="moved to idle only from x"):
      await driver.arm.park()
    assert 22 not in [command for _, command, _ in self.sent(link)]

  async def test_with_model_updates_the_pose_is_passed_on_after_every_route_step(self) -> None:
    encoders = {**IDLE_ENCODER_REPLIES, 2: Z_UP_ENCODER_REPLY}
    sent = {}
    for with_model_updates in (True, False):
      for observed in (True, False):
        link = RouteLink(answers=make_answers(), encoders=encoders)
        driver = await self.set_up_park(link=link)
        assert driver.arm is not None
        observer = mock.Mock()
        if observed:
          driver.arm._pose_observer = observer
        await driver.arm.park(with_model_updates=with_model_updates)
        sent[with_model_updates, observed] = self.sent(link)
        if observed:
          expected_calls = len(SYNTHETIC_IDLE_ROUTES["LeftMiddle"]) if with_model_updates else 1
          assert observer.call_count == expected_calls
    # Z read before the rise; then, with an observer, the three encoders after every step.
    reads = [84, 84, 84]
    assert [command for _, command, _ in sent[True, True] if command in (22, 84)] == (
      [84] + [22] * 2 + reads + [22] * 3 + reads + [22] * 3 + reads
    )
    assert [command for _, command, _ in sent[False, True] if command in (22, 84)] == (
      [84] + [22] * 8 + reads
    )
    # Without an observer nothing more is read: the frames are the same either way.
    assert sent[True, False] == sent[False, False]

  async def test_stop_routes_to_idle_before_the_recorded_close(self) -> None:
    link = RouteLink(
      answers=make_answers(),
      encoders={2: Z_UP_ENCODER_REPLY},
      events={
        (0, 68, bytes.fromhex(CLOSE_QUEUED_MOVES[item])): [bytes.fromhex(CLOSE_ITEM_PLAYED[item])]
        for item in (15, 16)
      },
    )
    link.set_absolute_readings(index=-1)
    driver = self.make_driver(
      link=link,
      declared_configuration_json=self.declare(
        arm_block=get_away_arm_block(), zones=SYNTHETIC_ZONES_BLOCK
      ),
    )
    async with driver.lifespan(skip_arm=True):
      assert driver.arm is not None
      driver.arm._initialized = True
      driver.arm._get_traverse_z_joint_position = (  # type: ignore[method-assign]
        lambda **_: 0.0
      )
      driver._next_item = 14  # numbered as in the recorded close
      since = len(link.written)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written[since:]]
    commands = [command for _, command, _ in sent if command in (22, 61, 62, 68, 84)]
    route = get_route_moves(route=SYNTHETIC_IDLE_ROUTES["LeftMiddle"])
    # Z up on its own and read back by its encoder before the recorded close's queued moves.
    assert commands == [84] + [22] * len(route) + [22, 84, 61, 62, 68, 68, 61, 62, 68]

  async def test_an_idle_target_outside_its_limits_moves_nothing(self) -> None:
    # An offset 7300 degrees larger puts motor 0's idle target below its limit, -4000.
    offsets = {str(motor): offset for motor, offset in SYNTHETIC_OFFSETS.items()}
    offsets["0"] += 7300.0
    link = RouteLink(answers=make_answers(), encoders={2: Z_UP_ENCODER_REPLY})
    driver = await self.set_up_arm(link=link, arm_block={**SYNTHETIC_ARM_BLOCK, "offsets": offsets})
    assert driver.arm is not None
    driver.arm._initialized = True
    with pytest.raises(ValueError, match=r"motor 0's target -40[0-9.]+ is outside its limits"):
      await driver.arm._park()
    assert self.sent(link) == []


class TestApproachPath(ArmTestBase):
  async def set_up_approach_paths(
    self, *, link: RouteLink, at_idle: bool = True, **blocks: Any
  ) -> MantisDriver:
    link.set_absolute_readings(index=-1)
    link.answers[(0, 84, b"\x02\x00")] = [bytes.fromhex(Z_UP_ENCODER_REPLY)]
    blocks = {"approach_paths": SYNTHETIC_APPROACH_PATHS_BLOCK, **blocks}
    arm_block = get_arm_block_at(x=15.0, y=31.177) if at_idle else get_away_arm_block()
    driver = await self.set_up_arm(link=link, arm_block=arm_block, **blocks)
    assert driver.arm is not None
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

  async def test_from_idle_the_waste_path_is_queued_line_by_line(self) -> None:
    first_item = 16 - len(SYNTHETIC_WASTE_ROUTE) + 1
    expected = self.get_expected_waste_moves(first_item=first_item)
    link = RouteLink(
      answers=make_answers(), events={(0, 68, expected[-1]): [bytes.fromhex(CLOSE_ITEM_PLAYED[16])]}
    )
    driver = await self.set_up_approach_paths(link=link)
    assert driver.arm is not None
    driver._next_item = first_item
    await driver.arm.move_to_waste(with_model_updates=False)
    queued = [payload for _, command, payload in self.sent(link) if command == 68]
    assert queued == expected
    # The line naming no axis and Z alone at the safe height, queued as recorded.
    assert queued[0][2:] == bytes.fromhex(RECORDED_WASTE_HOLD)[2:]
    assert queued[2][2:] == bytes.fromhex(RECORDED_WASTE_SAFE_HEIGHT)[2:]
    # The ready position, within what the synthetic offsets give.
    recorded = struct.unpack("<hHH9d", bytes.fromhex(RECORDED_WASTE_READY))
    sent = struct.unpack("<hHH9d", queued[1])
    assert sent[1:3] + sent[4:6] + sent[7:] == recorded[1:3] + recorded[4:6] + recorded[7:]
    for index in (3, 6):
      assert sent[index] == pytest.approx(recorded[index], abs=1e-9)

  async def test_with_model_updates_each_line_is_played_on_its_own(self) -> None:
    expected = self.get_expected_waste_moves(first_item=1)
    reads = [(0, 84, struct.pack("<h", motor)) for motor in (0, 1, 2)]
    for with_model_updates, observed in ((True, True), (True, False), (False, True)):
      link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
      driver = await self.set_up_approach_paths(link=link)
      assert driver.arm is not None
      driver._next_item = 1
      # The played events are not recorded for these items: each wait returns at once.
      waited_count = 0

      async def fake_wait_for_queue_item(*, item: int, timeout: float) -> None:
        nonlocal waited_count
        waited_count += 1

      driver._wait_for_queue_item = fake_wait_for_queue_item  # type: ignore[method-assign]
      observer = mock.Mock()
      if observed:
        driver.arm._pose_observer = observer
      await driver.arm.move_to_waste(with_model_updates=with_model_updates)
      sent = [frame for frame in self.sent(link) if frame[1] in (61, 62, 68, 84)]
      sent = sent[sent.index((0, 61, b"")) :]  # from the first play, after the parked check
      if with_model_updates:
        after = reads if observed else []
        play = [[(0, 61, b""), (0, 62, b""), (0, 68, move), *after] for move in expected]
        assert sent == [frame for frames in play for frame in frames]
        assert waited_count == len(expected)
      else:
        moves = [(0, 68, move) for move in expected]
        assert sent == [(0, 61, b""), (0, 62, b""), *moves, *reads]
      if observed:
        assert observer.call_count == (len(expected) if with_model_updates else 1)

  async def test_without_an_approach_path_nothing_moves(self) -> None:
    link = RouteLink(answers=make_answers())
    driver = await self.set_up_approach_paths(
      link=link, approach_paths={"wash_1": SYNTHETIC_WASH_ROUTE}
    )
    assert driver.arm is not None
    with pytest.raises(MantisConfigurationError, match="no approach path to the waste"):
      await driver.arm.move_to_waste()
    with pytest.raises(MantisConfigurationError, match="no approach path to the wash_2"):
      await driver.arm.move_to_wash(position=2)
    with pytest.raises(ValueError, match="position must be one of"):
      await driver.arm.move_to_wash(position=3)
    assert self.sent(link) == []

  async def test_leaving_the_waste_plays_its_path_backwards(self) -> None:
    # From the waste's last line back to its start, line by line; a line naming nothing is skipped.
    # A line moving the arms and raising Z is preceded by the arms alone at depth, Z held.
    way_out = [
      (-60.0, 10.0, 30.0),
      (None, None, 30.0),
      (-50.0, 20.0, None),
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
    assert driver.arm is not None
    driver._next_item = first_item
    await driver.arm._leave_approach_path(
      name="waste", index=len(SYNTHETIC_WASTE_ROUTE) - 1, until=0
    )
    assert [payload for _, command, payload in self.sent(link) if command == 68] == expected

  def test_the_head_is_placed_on_a_path_by_where_it_stands(self) -> None:
    arm = Arm(
      driver=mock.Mock(**{"configuration.generation": "v4"}),
      configuration=MantisArmConfiguration(
        offsets={0: 0.0, 1: 0.0},
        homing_position_a=SYNTHETIC_HOMING_POSITION_A,
        encoder_correction={0: [], 1: []},
        approach_paths={
          name: [MantisArmCoordinate(x=line[0], y=line[1], z=line[2]) for line in path]
          for name, path in SYNTHETIC_APPROACH_PATHS_BLOCK.items()
        },
      ),
    )

    def joints_at(x: float, y: float, z: float) -> Dict[int, float]:
      angles = compute_joint_angles(location=MantisArmCoordinate(x=x, y=y, z=None))
      return {0: angles[0], 1: angles[1], 2: z}

    # At the waste's bottom, at its last line; at idle, or level with a route line above the safe
    # height, in the open.
    assert arm._get_place(joint_state=joints_at(-60.0, 10.0, 35.0)) == ("waste", 8)
    assert arm._get_place(joint_state=joints_at(15.0, 31.177, 0.0)) is None
    assert arm._get_place(joint_state=joints_at(-50.0, 20.0, -1.57)) is None
    # Off a route line by more than the tolerance, in the open too.
    assert arm._get_place(joint_state=joints_at(-60.0, 11.0, 35.0)) is None


class TestStageDiscovery(ArmTestBase):
  async def test_the_stage_block_is_never_taken_from_a_recording(self) -> None:
    path = os.path.join(self.directory, "recording.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, "stage": SYNTHETIC_STAGE_BLOCK}, f)
    with mock.patch.object(master_module, "RECORDING_MANTIS_V4", path):
      driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert driver.arm is not None
    assert driver.arm.configuration.stage_origin is None

  async def test_a_declared_stage_block_is_read_and_saved(self) -> None:
    driver = await self.set_up_arm(
      link=FakeLink(answers=make_answers()), stage=SYNTHETIC_STAGE_BLOCK
    )
    assert driver.arm is not None
    assert driver.arm.configuration.stage_origin == (-50.0, 40.0, 51.0)
    assert driver.arm.configuration.stage_transformation_matrix is not None
    assert driver.arm.configuration.stage_transformation_matrix[2] == (-2e-05, -3e-05, 1.004)
    path = os.path.join(self.directory, "saved.json")
    driver.save_configuration(path=path)
    with open(path, encoding="utf-8") as f:
      assert json.load(f)["stage"] == SYNTHETIC_STAGE_BLOCK

  async def test_a_malformed_stage_block_refuses_setup(self) -> None:
    for block in (
      {**SYNTHETIC_STAGE_BLOCK, "origin": [1.0, 2.0]},
      {**SYNTHETIC_STAGE_BLOCK, "transformation_matrix": [[1.0, 0.0, 0.0]]},
      {key: value for key, value in SYNTHETIC_STAGE_BLOCK.items() if key != "offset_y"},
    ):
      driver = self.make_driver(
        link=FakeLink(answers=make_answers()),
        declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK, stage=block),
      )
      with pytest.raises(MantisConfigurationError):
        async with driver.lifespan(skip_arm=True):
          pass


class TestRouteDiscovery(ArmTestBase):
  async def test_routes_are_never_taken_from_a_recording(self) -> None:
    path = os.path.join(self.directory, "recording.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**RECORDING, "zones": SYNTHETIC_ZONES_BLOCK}, f)
    with mock.patch.object(master_module, "RECORDING_MANTIS_V4", path):
      driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert driver.arm is not None
    assert driver.arm.configuration.zone_rectangles is None

  async def test_declared_routes_are_read_and_saved(self) -> None:
    driver = await self.set_up_arm(
      link=FakeLink(answers=make_answers()),
      zones=SYNTHETIC_ZONES_BLOCK,
      approach_paths=SYNTHETIC_APPROACH_PATHS_BLOCK,
    )
    assert driver.arm is not None
    assert driver.arm.configuration.approach_paths is not None
    assert driver.arm.configuration.approach_paths["wash_1"][0] == MantisArmCoordinate(
      x=None, y=None, z=-1.57
    )
    path = os.path.join(self.directory, "saved.json")
    driver.save_configuration(path=path)
    with open(path, encoding="utf-8") as f:
      saved = json.load(f)
    assert saved["zones"] == SYNTHETIC_ZONES_BLOCK
    assert saved["approach_paths"] == SYNTHETIC_APPROACH_PATHS_BLOCK

  async def test_a_malformed_route_block_refuses_setup(self) -> None:
    for blocks in (
      {"zones": {**SYNTHETIC_ZONES_BLOCK, "rectangles": {"Front": [0, 1, 0, 1]}}},
      {"zones": {**SYNTHETIC_ZONES_BLOCK, "safe_xy_routes": SYNTHETIC_IDLE_ROUTES}},
      {"approach_paths": {"wash_3": SYNTHETIC_WASTE_ROUTE}},
    ):
      driver = self.make_driver(
        link=FakeLink(answers=make_answers()),
        declared_configuration_json=self.declare(arm_block=SYNTHETIC_ARM_BLOCK, **blocks),
      )
      with pytest.raises(MantisConfigurationError):
        async with driver.lifespan(skip_arm=True):
          pass


# Replies from a complete v3 setup and stop, by motor; a repeated status word as (word, count). The
# words are the captured cold start's, in its order, one per read of each phase.
CAPTURED_STATUS_RUNS = {
  0: [
    ("0000", 1),
    ("0100", 2),
    ("0001", 1),
    ("0100", 2),
    ("0001", 1),
    ("0100", 2),
    ("0000", 2),
    ("0100", 2),
    ("0001", 1),
    ("0100", 2),
    ("0001", 1),
    ("0100", 2),
    ("0000", 2),
    ("0400", 1),
    ("0500", 2),
    ("0400", 4),
    ("0500", 2),
    ("0400", 2),
    ("0500", 2),
    ("0400", 2),
    ("0500", 2),
    ("0400", 2),
  ],
  1: [
    ("0000", 1),
    ("0100", 2),
    ("0000", 1),
    ("0100", 2),
    ("0000", 1),
    ("0100", 2),
    ("0000", 2),
    ("0100", 2),
    ("0000", 1),
    ("0100", 2),
    ("0000", 1),
    ("0100", 2),
    ("0000", 2),
    ("0400", 3),
    ("0500", 2),
    ("0400", 2),
    ("0500", 2),
    ("0400", 2),
    ("0500", 1),
    ("0400", 3),
    ("0500", 2),
    ("0400", 2),
  ],
  2: [("0000", 8), ("0400", 13), ("0500", 1), ("0400", 12)],
}
# board, command, request payload, reply payload; "-" is an empty payload. Each following-error
# configuration read (command 82) answers as captured: enabled, at most 4.0.
CAPTURED_EXCHANGES = """
0:1:-:34002e00320030002e0036000000
10:1:-:31002e0030002e0035000000
0:10:0000:000000000040afc0000000000040af40
0:10:0100:000000000040afc0000000000040af40
0:10:0200:00000000004055c000000000008aa140
10:20:0000:00000040ed05873f000000a0aaa62ac0
10:20:0100:00000040ed05873f000000a0aaa62ac0
10:20:0200:00000000d432833f00000020af8741c0
0:82:0000:01000000000000001040
0:82:0100:01000000000000001040
0:82:0200:01000000000000001040
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
  """Play each captured reply list in order, repeating its final reply."""
  answers: Dict[Tuple[int, int, bytes], List[bytes]] = {}
  for exchange in CAPTURED_EXCHANGES:
    board, command, request, reply = exchange.split(":")
    key = int(board), int(command), bytes.fromhex(request.replace("-", ""))
    answers.setdefault(key, []).append(bytes.fromhex(reply.replace("-", "")))
  for motor, runs in CAPTURED_STATUS_RUNS.items():
    answers[0, 20, struct.pack("<h", motor)] = [
      bytes.fromhex(word) for word, count in runs for _ in range(count)
    ]
  return answers


def get_motion_commands(*, link: FakeLink) -> List[Tuple[int, str]]:
  """Commands that change a motor, leaving status polling out of comparisons."""
  return [
    (command, payload.hex())
    for _, board, command, payload in (parse_frame(frame=frame) for frame in link.written)
    if board == 0 and command in (17, 21, 22, 24, 28)
  ]


class V3Link(FakeLink):
  """The v3 link; boards 11 and 12, which a v3 lacks, refuse at once instead of timing out, so the
  tests keep the default read timeout. Each home answers with its captured homing done."""

  def __init__(self, *, answers: Dict[Tuple[int, int, bytes], List[bytes]]):
    """
    Args:
      answers: as for ``FakeLink``.
    """
    # Each home's request, and the homing done the captured cold start reported for its motor.
    home_done = {
      "0200000001000000d343867e1e874d4064ea130f737482407d2ae7ab04c2ce40": (
        81,
        "020000000000af90ebbf00000000f470c0bf",
      ),
      "01000300010001001c7ba7a066391640e359d148c0c74b4025e09b5793b39540": (
        174,
        "0100000000000040614000000000c01a0840",
      ),
      "00000300010000001c7ba7a066391640e359d148c0c74b4025e09b5793b39540": (
        174,
        "000000000000000060c00000000028170ac0",
      ),
    }
    super().__init__(
      answers=answers,
      events={
        (0, 21, bytes.fromhex(request)): [
          build_frame(
            packet_number=packet_number, board=0, command=0x0202, payload=bytes.fromhex(payload)
          )
        ]
        for request, (packet_number, payload) in home_done.items()
      },
    )

  async def write(self, data: bytes) -> int:
    packet_number, board, _, _ = parse_frame(frame=data)
    if board not in (11, 12):
      return await super().write(data)
    self.written.append(data)
    self.incoming.extend(build_frame(packet_number=packet_number, board=board, command=0xFFFF))
    return len(data)


class TestV3Arm(DriverTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Keep the status polls, without waiting between them."""
    await super()._enter_lifespan(stack, **kwargs)
    stack.enter_context(mock.patch.object(arm_module, "MOTOR_STATUS_POLL_INTERVAL", 0))
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
    """Discover through the v3 link, leaving the bring-up to the test."""
    link = V3Link(answers=make_v3_answers() if answers is None else answers)
    driver = await self.set_up_driver(
      link=link, declared_configuration_json=self.path if calibrated else None
    )
    assert driver.arm is not None
    assert driver.arm._get_generation() == "v3"
    return driver, link

  async def set_up_arm(self) -> Tuple[MantisDriver, V3Link]:
    """Bring the v3 arm up through the v3 link."""
    link = V3Link(answers=make_v3_answers())
    driver = await self.set_up_driver(
      link=link, skip_arm=False, declared_configuration_json=self.path
    )
    assert driver.arm is not None
    return driver, link

  async def test_setup_and_stop_follow_the_captured_v3_sequence(self) -> None:
    """Seat twice, back off from live positions, home Z twice, then arms 1/0, and walk to idle."""
    link = V3Link(answers=make_v3_answers())
    driver = self.make_driver(link=link, declared_configuration_json=self.path)
    async with driver.lifespan(skip_arm=False):
      assert driver.arm is not None
      assert driver.arm.is_initialized
      joint_state = await driver.arm.request_joint_state()
      head = driver.arm._get_head_position(joint_state=joint_state)
      assert head.x == pytest.approx(15.0, abs=1e-3)
      assert head.y == pytest.approx(31.177, abs=1e-3)
      assert joint_state[2] == pytest.approx(0.0, abs=1e-3)
      with pytest.raises(MantisConfigurationError, match="no absolute"):
        await driver.arm.request_absolute_joint_state()
    expected = []
    for exchange in CAPTURED_EXCHANGES:
      board, command, request, _ = exchange.split(":")
      if int(board) == 0 and int(command) in (17, 21, 22, 24, 28):
        expected.append((int(command), request))
    assert get_motion_commands(link=link) == expected
    assert driver.arm is not None
    assert not driver.arm.is_initialized
    # Each Z home follows a following-error check of every motor, as captured.
    homes = [
      (command, payload[:2].hex())
      for _, board, command, payload in (parse_frame(frame=frame) for frame in link.written)
      if board == 0 and command in (82, 21)
    ]
    precheck = [(82, "0000"), (82, "0100"), (82, "0200")]
    assert homes == (
      precheck + [(21, "0200")] + precheck + [(21, "0200"), (21, "0100"), (21, "0000")]
    )

  async def test_stop_sends_three_stops_and_no_park(self) -> None:
    link = V3Link(answers=make_v3_answers())
    driver = self.make_driver(link=link, declared_configuration_json=self.path)
    async with driver.lifespan(skip_arm=False):
      sent = len(link.written)
    stops = [
      payload
      for _, board, command, payload in (parse_frame(frame=frame) for frame in link.written[sent:])
      if (board, command) == (0, 24)
    ]
    assert [payload[:2].hex() for payload in stops] == ["0000", "0100", "0200"]
    assert 22 not in [
      command for _, _, command, _ in (parse_frame(frame=f) for f in link.written[sent:])
    ]

  async def test_missing_calibration_refuses_motion_and_pose(self) -> None:
    """Discovery remains usable without another unit's calibration being substituted."""
    driver, link = await self.make_arm(calibrated=False)
    assert driver.arm is not None
    with pytest.raises(MantisConfigurationError, match="no v3 arm calibration"):
      await driver.arm.initialize()
    assert get_motion_commands(link=link) == []
    with pytest.raises(MantisMotionError, match="must be initialized"):
      await driver.arm.request_joint_state()

  async def test_invalid_route_refuses_before_any_motor_command(self) -> None:
    """A calibrated route outside a discovered motor limit is rejected before retracting."""
    driver, link = await self.make_arm()
    assert driver.arm is not None
    driver.arm.configuration.motor_limits[0] = (-1.0, 1.0)
    with pytest.raises(ValueError, match="outside its limits"):
      await driver.arm.initialize()
    assert get_motion_commands(link=link) == []

  async def test_failure_to_seat_stops_every_motor(self) -> None:
    """A retract that never meets a stop must not go on to home Z."""
    answers = make_v3_answers()
    for motor in range(3):
      answers[0, 20, struct.pack("<h", motor)] = [bytes.fromhex("0000")]
    driver, link = await self.make_arm(answers=answers)
    assert driver.arm is not None
    with mock.patch.object(arm_module, "SEAT_PASSES", 2):
      with pytest.raises(MantisMotionError, match="did not seat"):
        await driver.arm.initialize()
    commands = get_motion_commands(link=link)
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
    with pytest.raises(MantisMotionError, match="Z does not read homed"):
      await driver.arm.initialize()
    commands = get_motion_commands(link=link)
    assert [payload[:4] for command, payload in commands if command == 21] == ["0200"]
    assert [payload[:4] for command, payload in commands if command == 24] == [
      "0000",
      "0100",
      "0200",
    ]
    assert not driver.arm.is_initialized

  async def test_cancellation_after_seating_stops_every_motor(self) -> None:
    """Cancellation after moving propagates only after all three stops have been sent."""
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

    with mock.patch.object(arm, "_home_motors", new=block_home):
      with anyio.fail_after(10.0):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_init)
          await homing_entered.wait()
          scope.cancel()
        await anyio.lowlevel.checkpoint()
    assert scope.cancelled_caught
    commands = get_motion_commands(link=link)
    assert [payload[:4] for command, payload in commands if command == 24] == [
      "0000",
      "0100",
      "0200",
    ]
    assert not arm.is_initialized

  async def test_a_failed_seat_move_stops_all_three_motors(self) -> None:
    """No captured move answered an error code; a send that fails stands in for one."""
    driver, link = await self.make_arm()
    assert driver.arm is not None
    seat = bytes.fromhex("0000721cc7711cc73bc0b4a291808fb3b540814e1be8b40a8a40")
    write = link.write

    async def write_or_fail_the_seat(data: bytes) -> int:
      if parse_frame(frame=data)[1:] == (0, 22, seat):
        link.written.append(data)
        raise OSError("device unplugged")
      return int(await write(data))

    link.io.write.side_effect = write_or_fail_the_seat
    with pytest.raises(OSError, match="device unplugged"):
      await driver.arm.initialize()
    stopped = {payload[:4] for command, payload in get_motion_commands(link=link) if command == 24}
    assert stopped == {"0000", "0100", "0200"}
    assert not driver.arm.is_initialized

  async def test_moves_after_the_bring_up_are_not_implemented(self) -> None:
    """No v3 has run a move after its bring-up: each refuses before sending anything."""
    driver, link = await self.set_up_arm()
    assert driver.arm is not None
    arm = driver.arm
    moves = {
      "move_to_joint_state": lambda: arm.move_to_joint_state(joint_state={2: 1.0}),
      "move_to_z_position": lambda: arm.move_to_z_position(z=1.0),
      "move_to_xy_position": lambda: arm.move_to_xy_position(x=15.0, y=40.0),
      "move_to_location": lambda: arm.move_to_location(location=Coordinate(x=1.0, y=1.0, z=1.0)),
    }
    sent = len(link.written)
    for move in moves.values():
      with pytest.raises(NotImplementedError, match="only in its bring-up"):
        await move()
    assert len(link.written) == sent

  async def test_park_and_recovery_wait_for_bring_up_then_for_a_v3_run(self) -> None:
    """Before bring-up nothing is known; after it, every move refuses until a v3 has run it."""
    for set_up, error in ((False, MantisMotionError), (True, NotImplementedError)):
      driver, link = await (self.set_up_arm() if set_up else self.make_arm())
      assert driver.arm is not None
      arm = driver.arm
      calls = {
        "park": lambda: arm.park(),
        "move_to_waste": lambda: arm.move_to_waste(),
        "recover_from_fault": lambda: arm.recover_from_fault(),
      }
      sent = len(link.written)
      for call in calls.values():
        with pytest.raises(error):
          await call()
      assert len(link.written) == sent

  async def test_parked_is_read_from_the_motors_after_bring_up(self) -> None:
    """A v3 has no absolute encoders: before bring-up nothing is sent; after it, the motors."""
    driver, link = await self.make_arm()
    assert driver.arm is not None
    sent = len(link.written)
    with pytest.raises(MantisMotionError):
      await driver.arm.request_is_parked()
    assert len(link.written) == sent
    driver, _ = await self.set_up_arm()
    assert driver.arm is not None
    assert await driver.arm.request_is_parked()
    with pytest.raises(MantisConfigurationError):
      await driver.arm.request_absolute_joint_state()

  async def test_a_v3_block_on_a_v4_is_refused(self) -> None:
    driver = mock.Mock(**{"configuration.generation": "v4", "name": "Mantis"})
    with pytest.raises(MantisConfigurationError, match="takes no v3 arm block"):
      Arm(
        driver=driver,
        configuration=MantisArmConfiguration(
          v3_arm_configuration=V3ArmConfiguration.from_block(block=SYNTHETIC_V3_ARM_BLOCK)
        ),
      )

  async def test_saved_configuration_keeps_the_v3_calibration(self) -> None:
    """A saved configuration carries the complete home frame and route into another setup."""
    driver, _ = await self.make_arm()
    saved = os.path.join(self.directory, "saved.json")
    driver.save_configuration(path=saved)
    with open(saved, encoding="utf-8") as file:
      assert json.load(file)["arm"] == SYNTHETIC_V3_ARM_BLOCK


# A unit's path through chip slot 1, in the arm frame, as its chip changer routes give it.
CHIP_SLOT_1_PATH: List[List[Optional[float]]] = [
  [70.394, 63.965, -1.57],
  [80.052, 12.077, -1.57],
  [94.052, -7.923, -1.57],
  [124.052, 2.077, -1.57],
  [124.052, 12.077, -1.57],
  [124.052, 12.077, 10.938],
  [124.052, 12.077, 10.938],
  [122.385, 12.398, 10.561],
  [122.385, 12.398, None],
  [122.385, 12.398, -1.57],
  [89.714, 10.769, -1.57],
]
# The moves a Mantis v4 was sent to pick up slot 1's chip and place it back: each motor's target in
# device units, "-" held, at the recorded profiles; on the unit the recorded idle targets are from.
CAPTURED_CHIP_SLOT_1_PICK_UP = """
-,-,0.0
0.020103664148598502,0.35838363081526514,-0.11811023622047244
-,-,-
-0.5891965243621929,-0.2509165576955263,-
-,-,-61.811023622047244
-0.5891965243621929,-0.2509165576955263,-61.811023622047244
43.109383064569144,-8.651398664407912,-61.811023622047244
56.29303487592246,-44.245804640535354,-61.811023622047244
68.97776972857237,-49.32540175358294,-61.811023622047244
78.33455945880014,-32.777253468679135,-61.811023622047244
76.05956795856261,-29.122652531670656,-61.811023622047244
76.05956795856261,-29.122652531670656,430.62992125984255
76.05956795856261,-29.122652531670656,430.62992125984255
75.13806588014809,-29.569602303281187,415.78740157480314
75.13806588014809,-29.569602303281187,-
75.13806588014809,-29.569602303281187,-61.811023622047244
60.931962501316455,-41.48356515074482,-61.811023622047244
-,-,-
-0.5891965243621929,-0.2509165576955263,-
-,-,-61.811023622047244
-0.5891965243621929,-0.2509165576955263,-61.811023622047244
-,-,0.0
0.020103664148598502,0.35838363081526514,-0.11811023622047244
""".split()
CAPTURED_CHIP_SLOT_1_PLACE = """
-,-,0.0
0.020103664148598502,0.35838363081526514,-0.11811023622047244
-,-,-
-0.5891965243621929,-0.2509165576955263,-
-,-,-61.811023622047244
-0.5891965243621929,-0.2509165576955263,-61.811023622047244
60.931962501316455,-41.48356515074482,-61.811023622047244
75.13806588014809,-29.569602303281187,-61.811023622047244
75.13806588014809,-29.569602303281187,-
75.13806588014809,-29.569602303281187,415.78740157480314
76.05956795856261,-29.122652531670656,430.62992125984255
76.05956795856261,-29.122652531670656,430.62992125984255
76.05956795856261,-29.122652531670656,-61.811023622047244
78.33455945880014,-32.777253468679135,-61.811023622047244
68.97776972857237,-49.32540175358294,-61.811023622047244
56.29303487592246,-44.245804640535354,-61.811023622047244
43.109383064569144,-8.651398664407912,-61.811023622047244
-,-,-
-0.5891965243621929,-0.2509165576955263,-
-,-,-61.811023622047244
-0.5891965243621929,-0.2509165576955263,-61.811023622047244
-,-,0.0
0.020103664148598502,0.35838363081526514,-0.11811023622047244
""".split()
CAPTURED_CHIP_SLOT_WAITS = {"pick_up": (12, 20, 22), "place": (10, 20, 22)}
# The captures send Z at idle after a chip slot as this value, which no unit file holds. PyLabRobot
# sends 0, the flag, as it does for every other move to idle.
CAPTURED_CHIP_SLOT_IDLE_Z = -0.11811023622047244


def parse_captured_moves(*, captured: List[str]) -> List[Dict[int, float]]:
  """Each captured queued move's targets, by motor; a held motor left out."""
  return [
    {motor: float(value) for motor, value in enumerate(move.split(",")) if value != "-"}
    for move in captured
  ]


class TestChipSlot(ArmTestBase):
  async def set_up_chip_slots(self, *, link: RouteLink) -> MantisDriver:
    for key, val in make_led_bar_answers().items():
      link.answers.setdefault(key, val)
    driver = await self.set_up_arm(link=link, approach_paths={"chip_slot_1": CHIP_SLOT_1_PATH})
    assert driver.arm is not None
    driver.arm._initialized = True
    self.waited_items: List[int] = []

    async def fake_request_is_parked(*, tolerance: float = 0.0) -> bool:
      del tolerance
      return True

    async def fake_wait_for_queue_item(*, item: int, timeout: float) -> None:
      self.waited_items.append(item)

    driver.arm.request_is_parked = fake_request_is_parked  # type: ignore[method-assign]
    driver._wait_for_queue_item = fake_wait_for_queue_item  # type: ignore[method-assign]
    driver._next_item = 1
    return driver

  def assert_captured(
    self, *, link: RouteLink, captured: List[str], waits: Tuple[int, ...]
  ) -> None:
    sent = [payload for _, command, payload in self.sent(link) if command == 68]
    expected = parse_captured_moves(captured=captured)
    assert len(sent) == len(expected)
    for index, (payload, targets) in enumerate(zip(sent, expected)):
      if index in (1, len(expected) - 1):  # the move to idle: Z at its flag, see above
        assert targets[2] == CAPTURED_CHIP_SLOT_IDLE_Z
        targets = {**targets, 2: 0.0}
      expected_payload = get_queued_move(item=index + 1, targets=targets)
      assert payload[:6] == expected_payload[:6]
      for offset in range(6, len(payload), 8):
        (sent_value,) = struct.unpack_from("<d", payload, offset)
        (expected_value,) = struct.unpack_from("<d", expected_payload, offset)
        assert sent_value == pytest.approx(expected_value, abs=1e-9)
    assert [(0, 61, b""), (0, 62, b"")] == [
      frame for frame in self.sent(link) if frame[1] in (61, 62)
    ]
    waited = [item - 1 for item in self.waited_items]
    assert tuple(waited) == waits

  async def test_a_pick_up_and_a_placement_queue_the_recorded_moves(self) -> None:
    link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None
    await driver.arm.pick_up_chip(slot=1)
    self.assert_captured(
      link=link,
      captured=CAPTURED_CHIP_SLOT_1_PICK_UP,
      waits=CAPTURED_CHIP_SLOT_WAITS["pick_up"],
    )
    assert driver.arm.attached_chip == 1
    self.sent_before = len(link.written)
    driver._next_item = 1
    self.waited_items.clear()
    await driver.arm.place_chip(slot=1)
    self.assert_captured(
      link=link,
      captured=CAPTURED_CHIP_SLOT_1_PLACE,
      waits=CAPTURED_CHIP_SLOT_WAITS["place"],
    )
    assert driver.arm.attached_chip is None

  async def test_the_led_bar_shows_cyan_for_a_pick_up_and_violet_for_a_placement(self) -> None:
    link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None
    await driver.arm.pick_up_chip(slot=1)
    await driver.arm.return_chip()
    written = link.written[self.sent_before :]
    assert bar_states(written=written) == [CYAN, "idle", VIOLET, "idle"]

  async def test_return_chip_without_a_chip_and_a_second_pick_up_are_refused(self) -> None:
    link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None
    with pytest.raises(MantisMotionError, match="no chip is on the head"):
      await driver.arm.return_chip()
    await driver.arm.pick_up_chip(slot=1)
    with pytest.raises(MantisMotionError, match="the chip from slot 1 is on the head"):
      await driver.arm.pick_up_chip(slot=1)

  async def test_a_placement_into_another_slot_is_refused_and_nothing_moves(self) -> None:
    link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None
    await driver.arm.pick_up_chip(slot=1)
    self.sent_before = len(link.written)
    with pytest.raises(MantisMotionError, match="from slot 1, not 2"):
      await driver.arm.place_chip(slot=2)
    assert self.sent(link) == []
    assert driver.arm.attached_chip == 1

  async def test_without_a_slot_path_nothing_moves(self) -> None:
    link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None
    with pytest.raises(MantisConfigurationError, match="no path into chip slot 2"):
      await driver.arm.pick_up_chip(slot=2)
    assert self.sent(link) == []
    assert driver.arm.attached_chip is None

  async def test_a_head_stopped_inside_a_slot_is_on_its_path(self) -> None:
    # Below the safe height, inside the slot; at it, before and after, in the open.
    link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None
    offsets = driver.arm.configuration.offsets
    assert offsets is not None
    z = 0.0
    for x, y, line_z in CHIP_SLOT_1_PATH:
      z = z if line_z is None else line_z
      assert x is not None and y is not None
      angles = compute_joint_angles(location=MantisArmCoordinate(x=x, y=y, z=None))
      joint_state = {0: angles[0] - offsets[0], 1: angles[1] - offsets[1], 2: z}
      place = driver.arm._get_place(joint_state=joint_state)
      assert (place is not None) == (z > 0)
      if place is not None:
        assert place[0] == "chip_slot_1"

  async def test_cancelled_led_restore_after_chip_pickup_or_placement_preserves_attached_chip(
    self,
    monkeypatch: pytest.MonkeyPatch,
  ) -> None:
    link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None and driver.led_bar is not None
    original_show = driver.led_bar._show

    with anyio.CancelScope() as pickup_scope:

      async def cancel_on_idle(*, key: str) -> None:
        if key == "idle":
          pickup_scope.cancel()
        await original_show(key=key)

      monkeypatch.setattr(driver.led_bar, "_show", cancel_on_idle)
      await driver.arm.pick_up_chip(slot=1)

    assert pickup_scope.cancelled_caught
    assert driver.arm.attached_chip == 1

    with anyio.CancelScope() as place_scope:

      async def cancel_on_place_idle(*, key: str) -> None:
        if key == "idle":
          place_scope.cancel()
        await original_show(key=key)

      monkeypatch.setattr(driver.led_bar, "_show", cancel_on_place_idle)
      await driver.arm.return_chip()

    assert place_scope.cancelled_caught
    assert driver.arm.attached_chip is None

  async def test_with_model_updates_commits_attached_chip_at_latch_and_clears_on_place(
    self,
  ) -> None:
    link = RouteLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None
    arm = driver.arm
    observed_attached: List[Optional[int]] = []

    def record_pose(*, theta_1: float, theta_2: float, z: float) -> None:
      """Record which chip is attached as each segment's pose is reported."""
      del theta_1, theta_2, z
      observed_attached.append(arm.attached_chip)

    arm._pose_observer = record_pose
    await arm.pick_up_chip(slot=1, with_model_updates=True)
    attach_index = CAPTURED_CHIP_SLOT_WAITS["pick_up"][0]
    total_moves = len(CAPTURED_CHIP_SLOT_1_PICK_UP)
    assert observed_attached == [None] * attach_index + [1] * (total_moves - attach_index)
    assert arm.attached_chip == 1

    observed_attached.clear()
    await arm.return_chip(with_model_updates=True)
    assert observed_attached == [1] * len(CAPTURED_CHIP_SLOT_1_PLACE)
    assert arm.attached_chip is None

  async def test_cancelled_pickup_after_latch_move_keeps_attached_chip(self) -> None:
    link = RouteLink(
      answers=make_led_bar_answers(),
      encoders={**IDLE_ENCODER_REPLIES, 2: Z_UP_ENCODER_REPLY},
    )
    driver = await self.set_up_chip_slots(link=link)
    assert driver.arm is not None
    arm = driver.arm
    attach_item = CAPTURED_CHIP_SLOT_WAITS["pick_up"][0] + 1
    second_wait_item = CAPTURED_CHIP_SLOT_WAITS["pick_up"][1] + 1

    async def cancel_after_latch_wait(*, item: int, timeout: float) -> None:
      """Let the attach-line wait complete, then cancel on the pre-idle wait."""
      del timeout
      self.waited_items.append(item)
      if item == second_wait_item:
        scope.cancel()
        await anyio.sleep_forever()

    driver._wait_for_queue_item = cancel_after_latch_wait  # type: ignore[method-assign]
    with anyio.fail_after(5.0):
      with anyio.CancelScope() as scope:
        await arm.pick_up_chip(slot=1)
      await anyio.lowlevel.checkpoint()
    assert scope.cancelled_caught
    assert self.waited_items == [attach_item, second_wait_item]
    assert arm.attached_chip == 1
