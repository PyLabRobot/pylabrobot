"""Pneumatic clamp commands and cleanup against recorded pressure-board replies."""

import asyncio
import json
import os
import tempfile
from typing import List, Tuple

from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError, MantisTimeoutError
from pylabrobot.formulatrix.mantis.driver.features.pneumatic_plate_clamp import PneumaticPlateClamp
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestCase, FakeLink, make_answers

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

  def __init__(self, *, absent_boards=(11, 12), pump_on=False):
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


class PneumaticPlateClampTests(DriverTestCase):
  def setUp(self):
    """Declare the unit's pneumatic output without substituting another unit's clamp levels."""
    super().setUp()
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.path = os.path.join(directory.name, "unit.json")
    with open(self.path, "w", encoding="utf-8") as file:
      json.dump({"plate_clamp": PNEUMATIC_CLAMP_BLOCK}, file)

  async def make_clamp(self, *, pump_on=False):
    """Discover the pneumatic clamp through its pressure-board link."""
    link = ClampLink(pump_on=pump_on)
    driver = await self.set_up_driver(link=link, declared_configuration_json=self.path)
    assert isinstance(driver.plate_clamp, PneumaticPlateClamp)
    return driver, link, driver.plate_clamp

  async def test_lock_release_and_repeated_calls_match_the_wire(self):
    """Repeated intents do not cycle the valve or lose ownership of the pump."""
    _, link, clamp = await self.make_clamp()
    await clamp.lock()
    await clamp.lock()
    await clamp.unlock()
    await clamp.unlock()
    self.assertEqual(
      pressure_commands(link),
      [
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
      ],
    )

  async def test_an_existing_air_supply_is_preserved(self):
    """A clamp must not shut down an air pump it did not start."""
    driver, link, clamp = await self.make_clamp(pump_on=True)
    await clamp.lock()
    await clamp.unlock()
    await driver.stop()
    commands = pressure_commands(link)
    self.assertNotIn(PUMP_ON, commands)
    self.assertNotIn(PUMP_OFF, commands)
    self.assertEqual(link.answers[PUMP_READ], [REPLY_ON])
    self.assertEqual(link.answers[VALVE_READ], [REPLY_OFF])

  async def test_stop_releases_a_held_plate_and_restores_the_pump(self):
    """Shutdown releases the valve before switching off an air supply owned by the clamp."""
    driver, link, clamp = await self.make_clamp()
    await clamp.lock()
    await driver.stop()
    self.assertEqual(
      pressure_commands(link)[-6:],
      [VALVE_READ, VALVE_OFF, VALVE_READ, PUMP_READ, PUMP_OFF, PUMP_READ],
    )
    self.assertEqual(link.answers[VALVE_READ], [REPLY_OFF])
    self.assertEqual(link.answers[PUMP_READ], [REPLY_OFF])

  async def test_cancellation_after_pump_write_releases_and_restores(self):
    """A cancelled write can take effect, so cleanup must already own the pump change."""
    _, link, clamp = await self.make_clamp()

    async def write_then_cancel(data):
      """Accept the pump-on write before cancelling the operation."""
      result = await link.write(data)
      if parse_frame(frame=data)[1:] == PUMP_ON:
        raise asyncio.CancelledError()
      return result

    link.io.write.side_effect = write_then_cancel
    with self.assertRaises(asyncio.CancelledError):
      await clamp.lock()
    self.assertEqual(link.answers[VALVE_READ], [REPLY_OFF])
    self.assertEqual(link.answers[PUMP_READ], [REPLY_OFF])
    self.assertEqual(pressure_commands(link).count(PUMP_ON), 1)

  async def test_unanswered_pump_write_is_not_retried_and_is_restored(self):
    """A missing acknowledgement does not mean the pump stayed off."""
    driver, link, clamp = await self.make_clamp()
    driver.default_read_timeout = 0.25  # the pump-on write is never answered
    del link.answers[PUMP_ON]
    with self.assertRaises(MantisTimeoutError):
      await clamp.lock()
    self.assertEqual(pressure_commands(link).count(PUMP_ON), 1)
    self.assertEqual(link.answers[VALVE_READ], [REPLY_OFF])
    self.assertEqual(link.answers[PUMP_READ], [REPLY_OFF])

  async def test_lock_levels_are_rejected_before_sending(self):
    """The pneumatic actuator has no adjustable clamping levels."""
    _, link, clamp = await self.make_clamp()
    before = len(link.written)
    with self.assertRaisesRegex(ValueError, "no lock levels"):
      await clamp.lock(level=1)
    self.assertEqual(len(link.written), before)

  async def test_no_pressure_board_means_no_pneumatic_clamp(self):
    """A configuration file alone cannot establish that a controller is fitted."""
    link = ClampLink(absent_boards=(10, 11, 12))
    driver = await self.set_up_driver(link=link, declared_configuration_json=self.path)
    self.assertIsNone(driver.plate_clamp)
    self.assertEqual(pressure_commands(link), [])

  async def test_no_unit_mapping_means_no_pneumatic_clamp(self):
    """Do not infer an auxiliary output from the generation alone."""
    link = ClampLink()
    driver = await self.set_up_driver(link=link)
    self.assertIsNone(driver.plate_clamp)

  async def test_invalid_output_is_rejected_during_discovery(self):
    """An auxiliary output other than the supported clamp output must not be actuated."""
    for output in (0, 1, 2.0, "2", True):
      with self.subTest(output=output):
        with open(self.path, "w", encoding="utf-8") as file:
          json.dump({"plate_clamp": {**PNEUMATIC_CLAMP_BLOCK, "auxiliary_output": output}}, file)
        link = ClampLink()
        driver = self.make_driver(link=link, declared_configuration_json=self.path)
        with self.assertRaises(MantisConfigurationError):
          await driver.setup(skip_arm=True)
        self.assertEqual(pressure_commands(link), [])
