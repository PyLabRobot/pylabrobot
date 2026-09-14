import unittest
from typing import Any, List, Optional, Set, Tuple
from unittest.mock import patch

from pylabrobot.hamilton.star.device import RECORDING_STAR
from pylabrobot.hamilton.star.driver.features.autoload import Autoload
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.hamilton import STARDeck

# Where the simulated wheel reads, in mm, when a test wants it standing below its safe Z.
LOWERED = "pylabrobot.hamilton.star.driver.simulator.SIMULATED_AUTOLOAD_Z_POSITION"


class DriveFault(Exception):
  """What a sled drive that stops part way answers with, standing in for the firmware's error."""


async def autoload(failing: Set[str]) -> Tuple[Autoload, List[str]]:
  """The autoload of a simulated device, whose `failing` commands raise once sent.

  Args:
    failing: the commands, as module and command joined (`"I0XP"`), that fail.

  Returns:
    The feature, and every command it sends from here on, in the same form.
  """
  driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
  await driver.setup()
  feature = driver.autoload
  assert feature is not None

  sent: List[str] = []
  answer = driver.send_command

  async def recorded(
    module: str,
    command: str,
    fmt: Optional[Any] = None,
    subsystem: Optional[str] = None,
    **kwargs: Any,
  ):
    sent.append(module + command)
    if module + command in failing:
      raise DriveFault(f"{module}{command} stopped part way")
    return await answer(module=module, command=command, fmt=fmt, subsystem=subsystem, **kwargs)

  driver.send_command = recorded  # type: ignore[assignment]
  return feature, sent


def after(sent: List[str], command: str) -> List[str]:
  """What was sent from `command` on."""
  return sent[sent.index(command) :]


class TestAFailedSledMoveRaisesTheWheel(unittest.IsolatedAsyncioTestCase):
  """A sled move that fails leaves the wheel at its safe Z, and says why the move failed."""

  async def test_a_failed_park_raises_a_wheel_below_safe_z(self):
    feature, sent = await autoload(failing={"I0XP"})

    with patch(LOWERED, 10.0), self.assertRaises(DriveFault):
      await feature.park()

    self.assertIn("C0IV", after(sent, "I0XP"))

  async def test_a_failed_move_along_x_raises_a_wheel_below_safe_z(self):
    feature, sent = await autoload(failing={"I0XA"})
    c = feature.configuration
    low, high = c.x_drive_range_increments
    middle = c.to_deck_frame(c.x_drive_increments_to_mm((low + high) // 2))

    with patch(LOWERED, 10.0), self.assertRaises(DriveFault):
      await feature.move_x(middle)

    self.assertIn("C0IV", after(sent, "I0XA"))

  async def test_a_failed_park_reads_a_wheel_already_at_safe_z_and_leaves_it(self):
    feature, sent = await autoload(failing={"I0XP"})

    with self.assertRaises(DriveFault):
      await feature.park()

    self.assertIn("I0RZ", after(sent, "I0XP"))
    self.assertNotIn("C0IV", after(sent, "I0XP"))

  async def test_a_wheel_whose_height_cannot_be_read_is_raised(self):
    feature, sent = await autoload(failing={"I0XP"})
    feature_read = feature._request_drive_position

    async def unreadable(command: str, digits: int) -> int:
      if command == "RZ" and "I0XP" in sent:
        raise DriveFault("I0RZ unanswered")
      return await feature_read(command, digits)

    with patch.object(feature, "_request_drive_position", unreadable):
      with self.assertRaisesRegex(DriveFault, "I0XP"):
        await feature.park()

    self.assertIn("C0IV", after(sent, "I0XP"))

  async def test_a_wheel_that_will_not_rise_does_not_hide_why_the_move_failed(self):
    feature, sent = await autoload(failing={"I0XP", "C0IV"})

    async def lowered_once_the_move_failed() -> bool:
      return "I0XP" not in sent

    with patch.object(feature, "wheel_is_at_safe_z", lowered_once_the_move_failed):
      with self.assertRaisesRegex(DriveFault, "I0XP"):
        await feature.park()

    self.assertIn("C0IV", after(sent, "I0XP"))

  async def test_a_park_that_succeeds_leaves_the_wheel_alone(self):
    feature, sent = await autoload(failing=set())

    await feature.park()

    self.assertNotIn("C0IV", sent)

  async def test_a_park_raises_a_wheel_below_safe_z_before_moving(self):
    feature, sent = await autoload(failing=set())

    with patch(LOWERED, 10.0):
      await feature.park()

    self.assertLess(sent.index("C0IV"), sent.index("I0XP"))


if __name__ == "__main__":
  unittest.main()
