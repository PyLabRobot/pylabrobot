"""Plate clamp tests: the bytes sent, against clamp states recorded from a Mantis v4, and a clamp
on a pressure-board output against recorded pressure-board replies."""

import json
import os
import tempfile
import unittest
from typing import Any, List, Tuple
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.configuration import RECORDING_MANTIS_V3
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features import plate_clamp
from pylabrobot.formulatrix.mantis.driver.features.arm import Arm
from pylabrobot.formulatrix.mantis.driver.features.plate_clamp import PlateClamp
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  DriverTestBase,
  FakeLink,
  make_answers,
)

RELEASE = (12, 31, bytes.fromhex("000000000000e83f000000000000000088130000"))
LOCK_AT_LEVEL_2 = (12, 30, bytes.fromhex("0000000000000000e17a14ae47e1ca3fe8030000"))
STATE_READ = (12, 33, b"")
# The LED bar's commands on board 12: a colour, the animation stopped, the animation started.
LED_BAR_COMMANDS = ((12, 13), (12, 16), (12, 17))
# What a stop sends before the clamp: the sequencer aborted, each motor stopped, then where each
# stopped read by its encoder, as the arm was not brought up.
HALT = [
  (0, 64, b""),
  (0, 24, bytes.fromhex("0000814e1be8b40a8a40")),
  (0, 24, bytes.fromhex("0100814e1be8b40a8a40")),
  (0, 24, bytes.fromhex("0200bb34be4f05c2be40")),
  (0, 84, bytes.fromhex("0000")),
  (0, 84, bytes.fromhex("0100")),
  (0, 84, bytes.fromhex("0200")),
]
# The states the device read after each command, as recorded.
STATES_AFTER_RELEASE_WHILE_RELEASED = ["0600", "0600"]
STATES_AFTER_RELEASE_WHILE_LOCKED = ["0500", "0600"]
STATES_AFTER_LOCK_AT_LEVEL_2 = ["0200"] * 7 + ["0300"]
STATES_AFTER_LOCK_AT_LEVEL_1 = ["0200"] * 14 + ["0300"]
# After power-on the clamp read 0, then 6 after the release stop sent.
STATES_AT_POWER_ON = ["0000"]
STATES_AFTER_RELEASE_AT_POWER_ON = ["0600"]


def answer_states_in_turn(*, link: FakeLink, turns: List[Tuple[int, List[str]]]) -> None:
  """When each board-12 command in ``turns`` is written, answer state reads with its states."""
  write = link.write

  async def write_then_answer_states(data: bytes) -> int:
    _, board, command, _ = parse_frame(frame=data)
    if turns and (board, command) == (12, turns[0][0]):
      _, states = turns.pop(0)
      link.answers[STATE_READ] = [bytes.fromhex(state) for state in states]
    return await write(data)

  link.io.write.side_effect = write_then_answer_states


def clamp_frames(link: FakeLink, since: int) -> List[Tuple[int, int, bytes]]:
  """The clamp's frames since ``since``; the LED bar's are pinned in its own tests."""
  frames = [parse_frame(frame=frame)[1:] for frame in link.written[since:]]
  return [frame for frame in frames if frame[:2] not in LED_BAR_COMMANDS]


class TestPlateClamp(DriverTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.link = FakeLink(answers=make_answers())
    self.driver = await self.set_up_driver(
      link=self.link, declared_configuration_json=RECORDING_MANTIS_V4
    )
    assert isinstance(self.driver.plate_clamp, PlateClamp)
    self.clamp = self.driver.plate_clamp
    self.sent_before = len(self.link.written)

  def sent(self) -> List[Tuple[int, int, bytes]]:
    return clamp_frames(self.link, self.sent_before)

  async def test_unlock(self) -> None:
    answer_states_in_turn(link=self.link, turns=[(31, STATES_AFTER_RELEASE_WHILE_LOCKED)])
    await self.clamp.unlock()
    assert self.sent() == [RELEASE, STATE_READ, STATE_READ]

  async def test_request_locked_reports_clamping_done(self) -> None:
    """Use the motor controller's completed-state readback."""
    self.link.answers[STATE_READ] = [bytes.fromhex("0300"), bytes.fromhex("0600")]
    assert await self.clamp.request_locked()
    assert not await self.clamp.request_locked()

  async def test_clamping_done_never_read_releases_and_raises(self) -> None:
    answer_states_in_turn(
      link=self.link,
      turns=[
        (31, STATES_AFTER_RELEASE_WHILE_RELEASED),
        (30, STATES_AFTER_LOCK_AT_LEVEL_1),
        (31, STATES_AFTER_RELEASE_WHILE_LOCKED),
      ],
    )
    # 9 polls end the wait before the recorded clamping done (the 15th read)
    with mock.patch.object(plate_clamp, "CLAMPING_TIMEOUT", 0.3):
      with pytest.raises(MantisTimeoutError, match="level 1; last state 2"):
        await self.clamp.lock(level=1)
    assert self.sent()[-3:] == [RELEASE, STATE_READ, STATE_READ]

  async def test_cancelled_lock_shields_release_before_propagating(self) -> None:
    clamping_started = anyio.Event()
    write = self.link.write
    turns = [
      (31, list(STATES_AFTER_RELEASE_WHILE_RELEASED)),
      (30, ["0200"] * 30),
      (31, list(STATES_AFTER_RELEASE_WHILE_LOCKED)),
    ]

    async def write_and_signal(data: bytes) -> int:
      _, board, command, _ = parse_frame(frame=data)
      if turns and (board, command) == (12, turns[0][0]):
        _, states = turns.pop(0)
        self.link.answers[STATE_READ] = [bytes.fromhex(state) for state in states]
        if command == 30:
          clamping_started.set()
      return await write(data)

    self.link.io.write.side_effect = write_and_signal

    with anyio.CancelScope() as scope:

      async def cancel_once_clamping() -> None:
        await clamping_started.wait()
        await anyio.sleep(0.05)
        scope.cancel()

      async with anyio.create_task_group() as tg:
        tg.start_soon(cancel_once_clamping)
        await self.clamp.lock(level=1)
    assert scope.cancelled_caught
    assert self.sent()[-3:] == [RELEASE, STATE_READ, STATE_READ]

  async def test_lock_with_no_level_uses_default_lock_level(self) -> None:
    assert self.clamp.default_lock_level == 1
    answer_states_in_turn(
      link=self.link,
      turns=[(31, STATES_AFTER_RELEASE_WHILE_RELEASED), (30, STATES_AFTER_LOCK_AT_LEVEL_2)],
    )
    self.clamp.default_lock_level = 2
    await self.clamp.lock()
    assert self.sent() == [RELEASE, STATE_READ, LOCK_AT_LEVEL_2] + [STATE_READ] * 8

  async def test_unknown_default_lock_level_sends_nothing(self) -> None:
    self.clamp.default_lock_level = 4
    with pytest.raises(ValueError):
      await self.clamp.lock()
    assert self.sent() == []

  async def test_unknown_level_sends_nothing(self) -> None:
    with pytest.raises(ValueError):
      await self.clamp.lock(level=4)
    assert self.sent() == []

  async def test_exit_releases_a_locked_clamp(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = self.make_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
    async with driver.lifespan(skip_arm=True):
      assert driver.plate_clamp is not None
      since = len(link.written)
      answer_states_in_turn(
        link=link,
        turns=[
          (31, STATES_AFTER_RELEASE_WHILE_RELEASED),
          (30, STATES_AFTER_LOCK_AT_LEVEL_2),
          (31, STATES_AFTER_RELEASE_WHILE_LOCKED),
        ],
      )
      await driver.plate_clamp.lock(level=2)
    assert clamp_frames(link, since) == (
      [RELEASE, STATE_READ, LOCK_AT_LEVEL_2]
      + [STATE_READ] * 8
      + HALT
      + [STATE_READ, RELEASE, STATE_READ, STATE_READ]
    )

  async def test_exit_releases_a_clamp_not_actuated_since_power_on(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = self.make_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
    async with driver.lifespan(skip_arm=True):
      since = len(link.written)
      link.answers[STATE_READ] = [bytes.fromhex(state) for state in STATES_AT_POWER_ON]
      answer_states_in_turn(link=link, turns=[(31, STATES_AFTER_RELEASE_AT_POWER_ON)])
    assert clamp_frames(link, since) == HALT + [STATE_READ, RELEASE, STATE_READ]


class TestPlateClampDiscovery(DriverTestBase):
  async def test_lock_with_no_levels_sends_nothing(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link)
    assert isinstance(driver.plate_clamp, PlateClamp)
    driver.plate_clamp.levels = None  # a unit whose folder, declared file and recording have none
    sent_before = len(link.written)
    with pytest.raises(MantisConfigurationError, match="no clamp levels"):
      await driver.plate_clamp.lock(level=1)
    assert len(link.written) == sent_before

  async def test_built_only_when_the_lc3_board_answers(self) -> None:
    for absent_boards, built in (((), True), ((12,), False)):
      self.default_read_timeout = 0.25 if absent_boards else 5.0  # an absent board never answers
      driver = await self.set_up_driver(
        link=FakeLink(answers=make_answers(absent_boards=absent_boards))
      )
      assert (driver.plate_clamp is not None) is built

  async def test_declared_levels_where_board_12_does_not_answer_are_refused(self) -> None:
    self.default_read_timeout = 0.25  # board 12 never answers
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "unit.json")
      with open(path, "w", encoding="utf-8") as file:
        json.dump({"plate_clamp": {"levels": {"1": [0.119, 2000]}}}, file)
      driver = self.make_driver(
        link=FakeLink(answers=make_answers(absent_boards=(12,))), declared_configuration_json=path
      )
      with pytest.raises(MantisConfigurationError, match="board 12 does not answer"):
        async with driver.lifespan(skip_arm=True):
          pass


PNEUMATIC_CLAMP_BLOCK = {"controller": "pressure_regulator", "auxiliary_output": 2}
# Board, command, payload and replies from the pneumatic clamp and its air pump.
VALVE_READ = (10, 41, bytes.fromhex("0200"))
VALVE_ON = (10, 42, bytes.fromhex("02000100"))
VALVE_OFF = (10, 42, bytes.fromhex("02000000"))
PUMP_READ = (10, 14, b"")
PUMP_ON = (10, 15, bytes.fromhex("0100"))
PUMP_OFF = (10, 15, bytes.fromhex("0000"))
REPLY_OFF, REPLY_ON, REPLY_SET = bytes.fromhex("0000"), bytes.fromhex("0100"), b""


class ClampLink(FakeLink):
  """Answer with recorded readbacks for the states selected by accepted commands; an absent
  board refuses at once instead of timing out, so the tests keep the default read timeout."""

  def __init__(self, *, absent_boards: Tuple[int, ...] = (11, 12), pump_on: bool = False):
    """Start released, with the specified air-pump state."""
    super().__init__(answers=make_answers(absent_boards=absent_boards))
    self.absent_boards = absent_boards
    self.answers.update(
      {command: [REPLY_SET] for command in (VALVE_ON, VALVE_OFF, PUMP_ON, PUMP_OFF)}
    )
    self.answers[VALVE_READ] = [REPLY_OFF]
    self.answers[PUMP_READ] = [REPLY_ON if pump_on else REPLY_OFF]

  async def write(self, data: bytes) -> int:
    """Apply the accepted write and expose its control state through the corresponding read."""
    packet_number, board, _, _ = parse_frame(frame=data)
    if board in self.absent_boards:
      self.written.append(data)
      self.incoming.extend(build_frame(packet_number=packet_number, board=board, command=0xFFFF))
      return len(data)
    command = parse_frame(frame=data)[1:]
    for change, read, reply in (
      (VALVE_ON, VALVE_READ, REPLY_ON),
      (VALVE_OFF, VALVE_READ, REPLY_OFF),
      (PUMP_ON, PUMP_READ, REPLY_ON),
      (PUMP_OFF, PUMP_READ, REPLY_OFF),
    ):
      if command == change:
        self.answers[read] = [reply]
    return await super().write(data)


def pressure_commands(link: ClampLink) -> List[Tuple[int, int, bytes]]:
  """Clamp and pump commands, excluding discovery and unrelated reads."""
  return [
    command
    for frame in link.written
    for command in [parse_frame(frame=frame)[1:]]
    if command in (VALVE_READ, VALVE_ON, VALVE_OFF, PUMP_READ, PUMP_ON, PUMP_OFF)
  ]


class TestPneumaticPlateClamp(DriverTestBase):
  """Verify Mantis v3 pneumatic clamp sequencing, air-pump restoration, and shielded cleanup."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Declare the unit's pneumatic output without substituting another unit's clamp levels."""
    await super()._enter_lifespan(stack, **kwargs)
    directory = stack.enter_context(tempfile.TemporaryDirectory())
    self.path = os.path.join(directory, "unit.json")
    with open(self.path, "w", encoding="utf-8") as file:
      json.dump({"plate_clamp": PNEUMATIC_CLAMP_BLOCK}, file)

  async def make_clamp(
    self, *, pump_on: bool = False
  ) -> Tuple[MantisDriver, ClampLink, PlateClamp]:
    """Discover the pneumatic clamp through its pressure-board link."""
    link = ClampLink(pump_on=pump_on)
    driver = await self.set_up_driver(link=link, declared_configuration_json=self.path)
    assert isinstance(driver.plate_clamp, PlateClamp)
    assert driver.plate_clamp.auxiliary_output == 2
    return driver, link, driver.plate_clamp

  async def test_lock_release_and_repeated_calls_match_the_wire(self) -> None:
    """Repeated intents do not cycle the valve or lose ownership of the pump."""
    _, link, clamp = await self.make_clamp()
    await clamp.lock()
    await clamp.lock()
    await clamp.unlock()
    await clamp.unlock()
    assert pressure_commands(link) == [
      PUMP_READ,
      VALVE_READ,
      VALVE_ON,
      VALVE_READ,
      PUMP_READ,
      PUMP_ON,
      PUMP_READ,
      PUMP_READ,
      VALVE_READ,
      VALVE_READ,
      VALVE_OFF,
      VALVE_READ,
      PUMP_READ,
      PUMP_OFF,
      PUMP_READ,
      VALVE_READ,
    ]

  async def test_an_existing_air_supply_is_preserved(self) -> None:
    """A clamp must not shut down an air pump it did not start."""
    link = ClampLink(pump_on=True)
    driver = self.make_driver(link=link, declared_configuration_json=self.path)
    async with driver.lifespan(skip_arm=True):
      assert isinstance(driver.plate_clamp, PlateClamp)
      await driver.plate_clamp.lock()
      await driver.plate_clamp.unlock()
    commands = pressure_commands(link)
    assert PUMP_ON not in commands
    assert PUMP_OFF not in commands
    assert link.answers[PUMP_READ] == [REPLY_ON]
    assert link.answers[VALVE_READ] == [REPLY_OFF]

  async def test_exit_releases_a_held_plate_and_restores_the_pump(self) -> None:
    """Lifespan exit releases the valve before switching off an air supply owned by the clamp."""
    link = ClampLink()
    driver = self.make_driver(link=link, declared_configuration_json=self.path)
    async with driver.lifespan(skip_arm=True):
      assert isinstance(driver.plate_clamp, PlateClamp)
      await driver.plate_clamp.lock()
    assert pressure_commands(link)[-6:] == [
      VALVE_READ,
      VALVE_OFF,
      VALVE_READ,
      PUMP_READ,
      PUMP_OFF,
      PUMP_READ,
    ]
    assert link.answers[VALVE_READ] == [REPLY_OFF]
    assert link.answers[PUMP_READ] == [REPLY_OFF]

  async def test_cancellation_after_pump_write_releases_and_restores(self) -> None:
    """A cancelled write can take effect, so shielded cleanup must release valve and pump."""
    _, link, clamp = await self.make_clamp()
    pump_written = anyio.Event()
    scope = anyio.CancelScope()

    async def write_then_cancel(data: bytes) -> int:
      result = await link.write(data)
      if parse_frame(frame=data)[1:] == PUMP_ON:
        pump_written.set()
        scope.cancel()
        await anyio.lowlevel.checkpoint()
      return result

    link.io.write.side_effect = write_then_cancel
    with scope:
      await clamp.lock()
    assert scope.cancelled_caught
    assert pump_written.is_set()
    assert link.answers[VALVE_READ] == [REPLY_OFF]
    assert link.answers[PUMP_READ] == [REPLY_OFF]
    assert pressure_commands(link).count(PUMP_ON) == 1

  async def test_cancellation_during_unlock_pump_restore_shields_pump_shutoff(self) -> None:
    """Cancelling unlock() after valve release still shuts off a clamp-started air pump."""
    _, link, clamp = await self.make_clamp()
    await clamp.lock()
    valve_released = False
    cancelled = False
    scope = anyio.CancelScope()

    async def cancel_on_pump_read_after_unlock(data: bytes) -> int:
      nonlocal valve_released, cancelled
      cmd = parse_frame(frame=data)[1:]
      result = await link.write(data)
      if cmd == VALVE_OFF:
        valve_released = True
      elif valve_released and not cancelled and cmd == PUMP_READ:
        cancelled = True
        scope.cancel()
        await anyio.lowlevel.checkpoint()
      return result

    link.io.write.side_effect = cancel_on_pump_read_after_unlock
    with scope:
      await clamp.unlock()
    assert scope.cancelled_caught
    assert link.answers[VALVE_READ] == [REPLY_OFF]
    assert link.answers[PUMP_READ] == [REPLY_OFF]
    assert not clamp._started_pump

  async def test_unanswered_pump_write_is_not_retried_and_is_restored(self) -> None:
    """A missing acknowledgement does not mean the pump stayed off."""
    driver, link, clamp = await self.make_clamp()
    driver.default_read_timeout = 0.05  # the pump-on write is never answered
    del link.answers[PUMP_ON]
    with pytest.raises(MantisTimeoutError):
      await clamp.lock()
    assert pressure_commands(link).count(PUMP_ON) == 1
    assert link.answers[VALVE_READ] == [REPLY_OFF]
    assert link.answers[PUMP_READ] == [REPLY_OFF]

  async def test_lock_levels_are_rejected_before_sending(self) -> None:
    """The pneumatic actuator has no adjustable clamping levels."""
    _, link, clamp = await self.make_clamp()
    before = len(link.written)
    with pytest.raises(ValueError, match="no lock levels"):
      await clamp.lock(level=1)
    assert len(link.written) == before

  async def test_no_pressure_board_means_no_pneumatic_clamp(self) -> None:
    """A configuration file alone cannot establish that a controller is fitted."""
    link = ClampLink(absent_boards=(10, 11, 12))
    driver = await self.set_up_driver(link=link, declared_configuration_json=self.path)
    assert driver.plate_clamp is None
    assert pressure_commands(link) == []

  async def test_no_unit_mapping_means_no_pneumatic_clamp(self) -> None:
    """Do not infer an auxiliary output from the generation alone."""
    link = ClampLink()
    driver = await self.set_up_driver(link=link)
    assert driver.plate_clamp is None

  async def test_invalid_output_is_rejected_during_discovery(self) -> None:
    """An auxiliary output other than the supported clamp output must not be actuated."""
    for output in (0, 1, 2.0, "2", True):
      with open(self.path, "w", encoding="utf-8") as file:
        json.dump({"plate_clamp": {**PNEUMATIC_CLAMP_BLOCK, "auxiliary_output": output}}, file)
      link = ClampLink()
      driver = self.make_driver(link=link, declared_configuration_json=self.path)
      with pytest.raises(MantisConfigurationError):
        async with driver.lifespan(skip_arm=True):
          pass
      assert pressure_commands(link) == []

  async def test_request_state_refuses_with_nothing_sent(self) -> None:
    """A valve on a pressure-board output has no clamp state; its readback is the valve."""
    _, link, clamp = await self.make_clamp()
    before = len(link.written)
    with pytest.raises(MantisConfigurationError, match="no clamp state"):
      await clamp.request_state()
    assert len(link.written) == before
    assert not await clamp.request_locked()
    assert pressure_commands(link)[-1:] == [VALVE_READ]

  async def test_discovery_builds_one_arm_and_one_clamp_class_from_both_recordings(self) -> None:
    with open(RECORDING_MANTIS_V3, encoding="utf-8") as file:
      v3_declared = {**json.load(file), "plate_clamp": PNEUMATIC_CLAMP_BLOCK}
    with open(self.path, "w", encoding="utf-8") as file:
      json.dump(v3_declared, file)
    for link, path in (
      (FakeLink(answers=make_answers()), RECORDING_MANTIS_V4),
      (ClampLink(), self.path),
    ):
      driver = await self.set_up_driver(link=link, declared_configuration_json=path)
      assert type(driver.arm) is Arm
      assert type(driver.plate_clamp) is PlateClamp

  async def test_a_pressure_board_output_where_board_12_answers_is_refused(self) -> None:
    link = ClampLink(absent_boards=(11,))
    driver = self.make_driver(link=link, declared_configuration_json=self.path)
    with pytest.raises(MantisConfigurationError, match="board 12 answers"):
      async with driver.lifespan(skip_arm=True):
        pass


class PlateClampInitTests(unittest.TestCase):
  def test_levels_alone_or_an_output_with_its_pump(self) -> None:
    driver, pump = mock.Mock(), mock.Mock()
    self.assertIsNone(PlateClamp(driver=driver).auxiliary_output)
    self.assertIsNone(PlateClamp(driver=driver, levels={1: (0.1, 1000)}).auxiliary_output)
    self.assertEqual(PlateClamp(driver=driver, auxiliary_output=2, pump=pump).auxiliary_output, 2)
    refused = {
      "an output alone": lambda: PlateClamp(driver=driver, auxiliary_output=2),
      "a pump alone": lambda: PlateClamp(driver=driver, pump=pump),
      "levels with an output": lambda: PlateClamp(
        driver=driver, levels={1: (0.1, 1000)}, auxiliary_output=2, pump=pump
      ),
    }
    for name, build in refused.items():
      with self.subTest(arguments=name):
        with self.assertRaises(ValueError):
          build()


if __name__ == "__main__":
  unittest.main()
