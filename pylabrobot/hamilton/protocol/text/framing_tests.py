import datetime
import unittest

from pylabrobot.hamilton.protocol.text.framing import (
  parse_firmware_version_date,
  parse_fw_string,
)


class TestParseFirmwareString(unittest.TestCase):
  """The format string names each field and how wide it is; one character per type."""

  def test_identifier_is_parsed_without_being_named(self):
    self.assertEqual(parse_fw_string("C0QMid1111", ""), {"id": 1111})
    self.assertEqual(parse_fw_string("C0QMid1111", "id####"), {"id": 1111})

  def test_field_types(self):
    self.assertEqual(parse_fw_string("C0QMid1112aaabc", "aa&&&"), {"id": 1112, "aa": "abc"})
    self.assertEqual(parse_fw_string("C0QMid1112aa-21", "aa##"), {"id": 1112, "aa": -21})
    self.assertEqual(parse_fw_string("C0QMid1113pqABC", "pq***"), {"id": 1113, "pq": 0xABC})

  def test_repeated_field_reads_as_a_list(self):
    self.assertEqual(parse_fw_string("C0RTid0001rt1 0 1", "rt# (n)"), {"id": 1, "rt": [1, 0, 1]})

  def test_missing_field_raises(self):
    with self.assertRaises(ValueError):
      parse_fw_string("C0QMid1111", "aa####")


class TestParseFirmwareVersionDate(unittest.TestCase):
  """Two layouts are in circulation, and the module reports which one it uses by how it writes
  the date rather than by saying so."""

  def test_both_layouts(self):
    self.assertEqual(
      parse_firmware_version_date("C0RFid0001rf7.6S 2021-11-05"), datetime.date(2021, 11, 5)
    )
    self.assertEqual(
      parse_firmware_version_date("H0RFid0001rf5.0S i 2021-10-22 (H0 XE167)"),
      datetime.date(2021, 10, 22),
    )


class TestOldNamesStillImport(unittest.TestCase):
  """The functions moved out of the USB transport; the names they were reached by still work."""

  def test_transport_and_backend_re_export_the_same_object(self):
    from pylabrobot.hamilton.transport.usb.protocol import (
      parse_star_firmware_version_date,
      parse_star_fw_string,
    )
    from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_backend import (
      parse_star_fw_string as from_backend,
    )

    self.assertIs(parse_star_fw_string, parse_fw_string)
    self.assertIs(from_backend, parse_fw_string)
    self.assertIs(parse_star_firmware_version_date, parse_firmware_version_date)
