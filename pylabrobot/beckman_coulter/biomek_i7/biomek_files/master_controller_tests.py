"""What a declared configuration takes from the Master Controller's instrument record, read from a
record written in the test from the package's generic configuration, as text and as a database."""

import tempfile
import unittest
from pathlib import Path

from pylabrobot.beckman_coulter.biomek_i7.biomek_files.files_tests_support import (
  record_text,
  write_database,
  write_record,
)
from pylabrobot.beckman_coulter.biomek_i7.biomek_files.master_controller import (
  Symbol,
  parse_record,
  read_master_controller,
)
from pylabrobot.beckman_coulter.biomek_i7.biomek_files.master_controller import (
  write_record as save_record,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import DeviceConfiguration
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  as_instrument,
  generic_configuration,
)


def instrument(chassis: str = "simulated00100000000000000000000") -> DeviceConfiguration:
  """The generic configuration as an instrument's, with a measured hysteresis on a Span-8 Z."""
  config = as_instrument(generic_configuration())
  config.chassis_serial_number = chassis
  z2 = next(a for a in config.modules["span8"] if a["name"] == "Z2")
  z2["hysteresis"] = 11
  return config


class RecordTextTests(unittest.TestCase):
  def test_values_of_each_kind(self) -> None:
    parsed = parse_record('#(<arm> "ab\\"c" left 12 -3 #t #f (1 2) #vu8(0 255 7))')
    self.assertEqual(
      parsed, [["<arm>", 'ab\\"c', "left", 12, -3, True, False, [1, 2], b"\x00\xff\x07"]]
    )
    self.assertIsInstance(parsed[0][2], Symbol)
    self.assertNotIsInstance(parsed[0][1], Symbol)

  def test_text_that_is_not_a_record_is_refused(self) -> None:
    for text in ("#(1 2", ")", '#(1 "unclosed)'):
      with self.subTest(text=text), self.assertRaises(ValueError):
        parse_record(text)


class ReadTests(unittest.TestCase):
  def setUp(self) -> None:
    folder = tempfile.TemporaryDirectory()
    self.addCleanup(folder.cleanup)
    self.folder = Path(folder.name)
    self.config = instrument()

  def test_the_record_gives_the_motor_tables_and_serial_numbers(self) -> None:
    path = self.folder / "record.txt"
    write_record(self.config, path)
    read = read_master_controller(master_controller_record=path)
    self.assertEqual(
      read["modules"], self.config.modules
    )  # pumps' Configure packed from the record
    self.assertEqual(
      read["chassis_serial_number"], "SIMULATED00100000000000000000000"
    )  # upper case
    self.assertEqual(
      read["module_serial_numbers"],
      {side: pod.module_serial_number for side, pod in self.config.pods.items()},
    )

  def test_a_database_gives_the_same(self) -> None:
    db = self.folder / "Log.db3"
    write_database([("one", record_text(self.config))], db)
    record = self.folder / "record.txt"
    write_record(self.config, record)
    self.assertEqual(
      read_master_controller(master_controller_db=db),
      read_master_controller(master_controller_record=record),
    )

  def test_an_instrument_is_chosen_by_its_chassis(self) -> None:
    db = self.folder / "Log.db3"
    other = instrument(chassis="B" * 32)
    write_database([("a", record_text(self.config)), ("b", record_text(other))], db)
    with self.assertRaisesRegex(ValueError, "2 Master Controller instrument records.*pass"):
      read_master_controller(master_controller_db=db)
    chosen = read_master_controller(master_controller_db=db, chassis_serial_number="b" * 32)
    self.assertEqual(chosen["chassis_serial_number"], "B" * 32)
    with self.assertRaisesRegex(ValueError, "0 Master Controller instrument records for chassis"):
      read_master_controller(master_controller_db=db, chassis_serial_number="0" * 32)

  def test_one_source_exactly(self) -> None:
    path = self.folder / "record.txt"
    write_record(self.config, path)
    with self.assertRaisesRegex(ValueError, "one of"):
      read_master_controller()
    with self.assertRaisesRegex(ValueError, "one of"):
      read_master_controller(master_controller_db=path, master_controller_record=path)

  def test_records_this_reader_does_not_know_are_refused(self) -> None:
    text = record_text(self.config)
    for bad, message in (
      ("#(<something> 1)", "not a Master Controller instrument record"),
      (text.replace(" span8 #(", " span96 #("), "span96 arm"),
      (text.replace(" span8 #(", " mc #("), "two multichannel arms"),
    ):
      with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
        save_record(bad, self.folder / "record.txt")


if __name__ == "__main__":
  unittest.main()
