import unittest
from typing import Any, List, Optional, Set, Tuple
from unittest.mock import AsyncMock, patch

from pylabrobot.hamilton.star.device import (
  RECORDING_STAR,
  RECORDING_STAR_HEAD384,
  RECORDING_STARLET,
  RECORDING_STARLET_HEAD384,
)
from pylabrobot.hamilton.star.driver.features.autoload import Autoload, AutoloadConfiguration
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.hamilton import PLT_CAR_L5AC_A00, STARDeck

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
      await feature.move_to_x_position(middle)

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


class TestDiscovery(unittest.IsolatedAsyncioTestCase):
  """Discovery reads what a saved configuration has to carry for a device to be simulated from it."""

  async def test_it_reads_the_initialization_track_and_the_adjustment(self):
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    sent: List[str] = []
    answer = driver.send_command

    async def recorded(module: str, command: str, **kwargs: Any):
      sent.append(module + command)
      return await answer(module=module, command=command, **kwargs)

    driver.send_command = recorded  # type: ignore[assignment]
    await driver.setup()

    self.assertIn("I0QX", sent)
    self.assertIn("I0RJ", sent)

  async def test_a_device_declared_without_them_still_sets_up(self):
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    declared = driver.simulated_autoload
    declared.initialization_track = None
    declared.adjustment_date = None
    declared.adjusted = None

    await driver.setup()

    feature = driver.autoload
    assert feature is not None
    self.assertIsNone(feature.configuration.initialization_track)
    self.assertIsNone(feature.configuration.adjusted)


class TestLoadCarrier(unittest.IsolatedAsyncioTestCase):
  """The command that reads a carrier's barcode is the one that pulls it in off the tray."""

  async def test_a_carrier_is_pulled_in_whether_or_not_its_barcode_is_wanted(self):
    feature, _ = await autoload(failing=set())
    deck = feature._driver.deck
    assert deck is not None
    carrier = PLT_CAR_L5AC_A00(name="carrier")
    deck.assign_child_resource(carrier, track=20)

    on_the_tray = AsyncMock(return_value=True)
    for wanted in (True, False):
      pulled_in = AsyncMock(return_value="read")
      with patch.object(
        feature, "sense_carrier_presence_on_single_loading_tray_track", on_the_tray
      ):
        with patch.object(feature, "load_carrier_from_tray_and_scan_carrier_barcode", pulled_in):
          loaded = await feature.load_carrier(
            carrier, carrier_barcode_reading=wanted, park_after=False
          )

      with self.subTest(carrier_barcode_reading=wanted):
        pulled_in.assert_awaited_once()
        self.assertEqual(loaded["carrier_barcode"], "read" if wanted else None)


class TestTheWheelsSafeZTolerance(unittest.IsolatedAsyncioTestCase):
  """The drive answers its hardware counter, which rests a step or two past where it was sent."""

  async def test_a_wheel_a_couple_of_steps_low_is_at_its_safe_z(self):
    feature, _ = await autoload(failing=set())
    with patch(LOWERED, feature.configuration.z_drive_increments_to_mm(2)):
      self.assertTrue(await feature.wheel_is_at_safe_z())
      low = await feature._driver.features_below_safe_z()
      self.assertFalse([entry for entry in low if "autoload" in entry])

  async def test_a_wheel_further_down_than_the_tolerance_is_not(self):
    feature, _ = await autoload(failing=set())
    with patch(LOWERED, 2.0):
      self.assertFalse(await feature.wheel_is_at_safe_z())
      self.assertIn(
        "autoload wheel below its safe Z", await feature._driver.features_below_safe_z()
      )


class TestTheRecordingsSled(unittest.IsolatedAsyncioTestCase):
  """Every shipped recording with an autoload places its sled where the configuration does."""

  async def test_the_reference_point_is_the_configurations(self):
    default = AutoloadConfiguration().reference_point_from_sled_left_edge
    for recording in (
      RECORDING_STAR,
      RECORDING_STAR_HEAD384,
      RECORDING_STARLET,
      RECORDING_STARLET_HEAD384,
    ):
      driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=recording)
      with self.subTest(recording=recording):
        self.assertEqual(driver.simulated_autoload.reference_point_from_sled_left_edge, default)


if __name__ == "__main__":
  unittest.main()
