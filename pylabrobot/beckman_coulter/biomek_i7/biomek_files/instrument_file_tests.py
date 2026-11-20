"""What a declared configuration takes from an instrument file, read from one written in the test
from the package's generic configuration."""

import tempfile
import unittest
from pathlib import Path

from pylabrobot.beckman_coulter.biomek_i7.biomek_files.files_tests_support import (
  STATE,
  container,
  instrument_file_tree,
  stream,
  write_instrument_file,
)
from pylabrobot.beckman_coulter.biomek_i7.biomek_files.instrument_file import (
  is_state,
  read_instrument_file,
)
from pylabrobot.beckman_coulter.biomek_i7.biomek_files.variant_tree import ParseError
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_configuration


def configuration():  # type: ignore[no-untyped-def]
  config = generic_configuration()
  config.instrument_serial_number = "TEST0000001"
  for side, pod in config.pods.items():
    pod.settings["HeadSerialNumber"] = f"HEAD-{side}"
  return config


class InstrumentFileTests(unittest.TestCase):
  def setUp(self) -> None:
    folder = tempfile.TemporaryDirectory()
    self.addCleanup(folder.cleanup)
    self.path = Path(folder.name) / "Biomek_i7.bif"
    self.config = configuration()
    write_instrument_file(self.config, self.path)
    self.read = read_instrument_file(self.path)
    self.expected = self.config.to_dict()

  def test_the_pods_come_back_as_written(self) -> None:
    for side, pod in self.expected["pods"].items():
      with self.subTest(side=side):
        # The module serial numbers are the Master Controller record's, not the instrument file's.
        expected = {k: v for k, v in pod.items() if k != "module_serial_number"}
        self.assertEqual(self.read["pods"][side], expected)
    self.assertEqual(self.read["instrument_serial_number"], "TEST0000001")

  def test_the_default_deck_comes_back_as_written(self) -> None:
    self.assertEqual(self.read["deck_positions"], self.expected["deck_positions"])
    self.assertEqual(self.read["deck_layout"], self.expected["deck_layout"])

  def test_runtime_state_is_left_out(self) -> None:
    for side in ("left", "right"):
      for key in STATE:
        self.assertNotIn(key, self.read["pods"][side]["settings"])
    for key in ("RT_Anything", "Tip8", "D3Position", "Z1Position", "GrippedLabware"):
      self.assertTrue(is_state(key), key)
    for key in ("TipLoadForceLimitSlope", "Tips2Load", "ZMargin"):
      self.assertFalse(is_state(key), key)

  def test_settings_kept_as_dictionaries_and_lists(self) -> None:
    for side, pod in self.expected["pods"].items():
      for key, value in pod["settings"].items():
        if isinstance(value, (dict, list)):
          self.assertEqual(self.read["pods"][side]["settings"][key], value, (side, key))

  def test_a_position_without_both_corners_is_left_out(self) -> None:
    tree = instrument_file_tree(self.config)
    positions = tree["Decks"]["Deck1"]["Positions"]
    name = next(iter(positions))
    del positions[name]["Z2"]
    self.path.write_bytes(container(stream(tree)))
    self.assertNotIn(name, read_instrument_file(self.path)["deck_positions"])

  def test_a_file_this_reader_cannot_account_for_is_refused(self) -> None:
    data = stream(instrument_file_tree(self.config))
    self.path.write_bytes(container(data[:4] + data[4:] + b"\x01"))
    with self.assertRaises(ParseError):
      read_instrument_file(self.path)


if __name__ == "__main__":
  unittest.main()
