"""`DeviceConfiguration.from_biomek` end to end, on an instrument file and a Master Controller record
written in the test, and `check_sources` finding what changed in them since."""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from pylabrobot.beckman_coulter.biomek_i7.biomek_files.files_tests_support import (
  write_instrument_file,
  write_record,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
  DeviceConfiguration,
  differences,
)
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  as_instrument,
  generic_configuration,
)


def instrument() -> DeviceConfiguration:
  """The generic configuration as one instrument's: serial numbers, a measured hysteresis."""
  config = as_instrument(generic_configuration())
  config.instrument_serial_number = "TEST0000001"
  for side, pod in config.pods.items():
    pod.settings["HeadSerialNumber"] = f"HEAD-{side}"
  next(a for a in config.modules["span8"] if a["name"] == "Z5")["hysteresis"] = 9
  return config


class ImportTests(unittest.TestCase):
  def setUp(self) -> None:
    folder = tempfile.TemporaryDirectory()
    self.addCleanup(folder.cleanup)
    self.bif = Path(folder.name) / "Biomek_i7.bif"
    self.record = Path(folder.name) / "record.txt"
    self.config = instrument()
    write_instrument_file(self.config, self.bif)
    write_record(self.config, self.record)

  def imported(self) -> DeviceConfiguration:
    return DeviceConfiguration.from_biomek(self.bif, master_controller_record=self.record)

  def test_importing_gives_the_configuration_the_files_were_written_from(self) -> None:
    imported = self.imported()
    expected, got = self.config.to_dict(), imported.to_dict()
    expected.pop("sources")
    sources = got.pop("sources")
    self.assertEqual(differences(expected, got), [])
    self.assertEqual(sources["instrument_file"]["kind"], "bif")
    self.assertEqual(sources["master_controller"]["kind"], "record")
    self.assertEqual(Path(sources["instrument_file"]["path"]), self.bif.resolve())
    self.assertEqual(imported.unrecorded_changes(), [])
    path = self.bif.parent / "configuration.json"
    imported.save(path)
    self.assertEqual(DeviceConfiguration.load(path).to_dict(), imported.to_dict())

  def test_a_travel_window_outside_its_motors_limits_is_refused(self) -> None:
    config = instrument()
    low, high = config.pods["left"].limits["X"]
    config.pods["left"].limits["X"] = (low, high + 1000.0)
    write_instrument_file(config, self.bif)
    with self.assertRaisesRegex(ValueError, "left pod's X travel.*outside the motor's"):
      self.imported()

  def test_unchanged_sources_check_clean(self) -> None:
    check = self.imported().check_sources()
    self.assertEqual((check.changed, check.unavailable), ([], []))

  def test_a_re_measured_hysteresis_is_found(self) -> None:
    imported = self.imported()
    remeasured = instrument()
    next(a for a in remeasured.modules["span8"] if a["name"] == "Z5")["hysteresis"] = 10
    write_record(remeasured, self.record)
    changed = imported.check_sources().changed
    self.assertEqual(len(changed), 1)
    self.assertRegex(changed[0], r"^master_controller: modules/span8\[\d+\]/hysteresis: 9 -> 10$")

  def test_a_re_framed_position_is_found(self) -> None:
    imported = self.imported()
    reframed = instrument()
    position = reframed.deck_positions["P6"]
    corners = dict(position.corners)
    corners[1] = (corners[1][0] + 0.05, corners[1][1], corners[1][2])
    reframed.deck_positions["P6"] = dataclasses.replace(position, corners=corners)
    write_instrument_file(reframed, self.bif)
    changed = imported.check_sources().changed
    self.assertEqual(len(changed), 1)
    self.assertTrue(changed[0].startswith("instrument_file: deck_positions/P6/corners/1[0]"))

  def test_sources_not_found_are_unavailable(self) -> None:
    imported = self.imported()
    self.record.unlink()
    check = imported.check_sources()
    self.assertEqual(check.changed, [])
    self.assertEqual(len(check.unavailable), 1)
    self.assertIn("master_controller", check.unavailable[0])
    check = generic_configuration().check_sources()  # fingerprints only: no paths
    self.assertEqual(len(check.unavailable), 2)


if __name__ == "__main__":
  unittest.main()
