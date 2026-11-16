"""Plate clamp tests: the bytes sent, against clamp states recorded from a Mantis v4."""

from typing import Any, List, Tuple
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features import plate_clamp
from pylabrobot.formulatrix.mantis.driver.features.plate_clamp import PlateClamp
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
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
# What a stop sends before the clamp: the sequencer aborted, each motor stopped.
HALT = [
  (0, 64, b""),
  (0, 24, bytes.fromhex("0000814e1be8b40a8a40")),
  (0, 24, bytes.fromhex("0100814e1be8b40a8a40")),
  (0, 24, bytes.fromhex("0200bb34be4f05c2be40")),
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
