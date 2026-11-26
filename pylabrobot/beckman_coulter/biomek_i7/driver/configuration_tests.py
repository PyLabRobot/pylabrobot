"""The declared configuration: how it converts, how it is kept, and how discovery checks it against
the boards. On the package's generic configuration, whose physical values are one instrument's."""

import asyncio
import dataclasses
import json
import os
import tempfile
import unittest

from pylabrobot.beckman_coulter.biomek_i7.defaults import GENERIC_CONFIGURATION
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import DeviceConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_configuration,
  generic_driver,
  sealed,
)

MASTER_LOGGER = "pylabrobot.beckman_coulter.biomek_i7.driver.master"


class DeclaredConfigurationTests(unittest.TestCase):
  def setUp(self) -> None:
    self.config = generic_configuration()
    self.left, self.right = self.config.pod("left"), self.config.pod("right")

  def test_unknown_axis_and_side(self) -> None:
    with self.assertRaises(ValueError):
      self.left.to_counts("Z1", 1.0)
    config = generic_configuration()
    del config.pods["right"]
    with self.assertRaisesRegex(ValueError, "no pod declared on the right"):
      config.pod("right")


class KeepingTests(unittest.TestCase):
  def setUp(self) -> None:
    self.folder = tempfile.TemporaryDirectory()
    self.addCleanup(self.folder.cleanup)
    self.path = os.path.join(self.folder.name, "configuration.json")

  def test_save_and_load_round_trip(self) -> None:
    config = generic_configuration()
    config.save(self.path)
    self.assertEqual(DeviceConfiguration.load(self.path).to_dict(), config.to_dict())
    with open(GENERIC_CONFIGURATION, encoding="utf-8") as fh:
      self.assertEqual(json.load(fh), config.to_dict())

  def test_a_file_changed_after_import_is_refused_at_load(self) -> None:
    for change in (
      lambda data: data["pods"]["left"]["conversion"]["X"].update(const=1.0),  # instrument file
      lambda data: data["modules"]["span8"][0].update(hysteresis=7),  # Master Controller record
    ):
      config = generic_configuration()
      data = config.to_dict()
      change(data)
      with open(self.path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
      with self.assertRaisesRegex(ValueError, "not the ones imported"):
        DeviceConfiguration.load(self.path)

  def test_anonymized_keeps_no_serial_numbers_and_no_measured_hysteresis(self) -> None:
    config = generic_configuration()
    config.generic = False
    config.chassis_serial_number = "A" * 32
    config.instrument_serial_number = "B" * 11
    for pod in config.pods.values():
      pod.module_serial_number = "C" * 32
    config.modules["span8"][0]["hysteresis"] = 12
    config.sources = {
      name: {"path": "C:/somewhere", "kind": "file", "read_at": "2026-01-01", "fingerprint": f}
      for name, f in config.fingerprints().items()
    }
    generic = config.anonymized()
    text = json.dumps(generic.to_dict())
    for serial in ("A" * 32, "B" * 11, "C" * 32):
      self.assertNotIn(serial, text)
    self.assertTrue(generic.generic)
    self.assertEqual({a["hysteresis"] for axes in generic.modules.values() for a in axes}, {0})
    self.assertEqual({k for s in generic.sources.values() for k in s}, {"fingerprint"})
    self.assertEqual(generic.unrecorded_changes(), [])
    self.assertEqual(generic.to_dict(), generic_configuration().to_dict())


class DeclaredAgainstFoundTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)

  async def discover_with(self, configuration: DeviceConfiguration) -> BiomekI7Driver:
    driver = generic_driver(configuration=sealed(configuration))
    self.addAsyncCleanup(driver.stop)
    await driver.discover()
    return driver

  async def test_the_generic_instrument_matches(self) -> None:
    driver = generic_driver()
    self.addAsyncCleanup(driver.stop)
    with self.assertNoLogs(MASTER_LOGGER, level="WARNING"):
      await driver.discover()
    assert driver.left_x_arm and driver.left_x_arm.declared
    self.assertEqual(driver.left_x_arm.declared.component, "MC_1")

  async def test_pods_swapped_are_refused(self) -> None:
    config = generic_configuration()
    left, right = config.pods["left"], config.pods["right"]
    config.pods = {
      "left": dataclasses.replace(right, side="left"),
      "right": dataclasses.replace(left, side="right"),
    }
    with self.assertRaisesRegex(ValueError, "span8 pod on the left"):
      await self.discover_with(config)

  async def test_a_pod_declared_but_not_found_is_only_logged(self) -> None:
    config = generic_configuration()
    del config.pods["right"]
    with self.assertLogs(MASTER_LOGGER, level="WARNING") as logs:
      driver = await self.discover_with(config)
    self.assertIn("right pod: declared nothing, found span8", "\n".join(logs.output))
    assert driver.right_x_arm is not None
    self.assertIsNone(driver.right_x_arm.declared)

  async def test_another_instruments_configuration_is_not_checked_on_given_boards(self) -> None:
    # Refused on the instrument's own boards (`serial_numbers_tests.py`); simulated ones are not
    # tied to an instrument.
    config = generic_configuration()
    config.chassis_serial_number = "0" * 32
    driver = await self.discover_with(config)
    self.assertEqual(driver.declared.chassis_serial_number, "0" * 32)

  def test_a_declared_configuration_is_required(self) -> None:
    with self.assertRaisesRegex(ValueError, "no declared configuration"):
      BiomekI7Driver(boards=[])


if __name__ == "__main__":
  unittest.main()
