"""Pneumatic clamp commands and cleanup against recorded pressure-board replies."""

import json
import os
import tempfile
from typing import Any, List, Tuple

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError, MantisTimeoutError
from pylabrobot.formulatrix.mantis.driver.features.pneumatic_plate_clamp import PneumaticPlateClamp
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestBase, FakeLink, make_answers

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
  ) -> Tuple[MantisDriver, ClampLink, PneumaticPlateClamp]:
    """Discover the pneumatic clamp through its pressure-board link."""
    link = ClampLink(pump_on=pump_on)
    driver = await self.set_up_driver(link=link, declared_configuration_json=self.path)
    assert isinstance(driver.plate_clamp, PneumaticPlateClamp)
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
      assert isinstance(driver.plate_clamp, PneumaticPlateClamp)
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
      assert isinstance(driver.plate_clamp, PneumaticPlateClamp)
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
