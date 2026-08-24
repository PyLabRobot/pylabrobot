import datetime
import unittest

from pylabrobot.hamilton.protocol.text.framing import (
  assemble_command,
  find_error_fields,
  parse_firmware_version_date,
  parse_fw_string,
  read_id,
  to_list,
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


class TestToList(unittest.TestCase):
  """A per-channel list is given one value per involved channel, and expanded to one per channel."""

  PATTERN = [True, False, True]

  def test_expands_to_one_value_per_channel(self):
    # channels not involved take val[0], which the machine ignores but must still receive
    self.assertEqual(to_list([7, 9], self.PATTERN), [7, 7, 9])

  def test_empty_is_rejected(self):
    with self.assertRaises(ValueError):
      to_list([], self.PATTERN)

  def test_more_values_than_channels_is_rejected(self):
    with self.assertRaises(ValueError):
      to_list([1, 2, 3, 4], self.PATTERN)

  def test_too_few_values_for_the_involved_channels_is_rejected(self):
    with self.assertRaises(ValueError):
      to_list([1], self.PATTERN)

  def test_too_many_values_for_the_involved_channels_is_rejected(self):
    with self.assertRaises(ValueError):
      to_list([1, 2, 3], [True, False, False])


class TestAssembleCommand(unittest.TestCase):
  """Each parameter type is written differently, and none of the conversions is visible from the
  call site."""

  def test_bool_is_written_as_a_digit(self):
    self.assertEqual(
      assemble_command("C0", "TT", id_=2, tt="04", tf=True, tl="0871"), "C0TTid0002tt04tf1tl0871"
    )

  def test_list_is_one_hot_expanded_and_marked_when_short_of_the_channels(self):
    # two values across three channels, of which the third is not involved; the trailing "&" says
    # the list stops short of the machine's channel count
    self.assertEqual(
      assemble_command(
        "C0", "TP", id_=3, tip_pattern=[True, True, False], num_channels=8, xp=[100, 200]
      ),
      "C0TPid0003xp100 200 100&",
    )

  def test_trailing_underscore_is_stripped_so_reserved_words_can_be_parameters(self):
    self.assertEqual(assemble_command("C0", "ZA", id_=4, za="2000", in_="5"), "C0ZAid0004za2000in5")

  def test_parameter_name_must_be_two_characters(self):
    with self.assertRaises(ValueError):
      assemble_command("C0", "ZA", id_=1, zzz="2000")


class TestFindErrorFields(unittest.TestCase):
  """The master reports its own error field and one per failing module; anything meaning
  "no error" is dropped, so an empty result means the reply is clean."""

  MODULES = ("P1", "P2")

  def test_clean_reply_reports_nothing(self):
    self.assertEqual(find_error_fields("C0QMid1111er00/00", 2, "C0", self.MODULES), {})

  def test_the_master_and_a_failing_module_are_both_reported(self):
    self.assertEqual(
      find_error_fields("C0QMid1111er99/00 P199/00", 2, "C0", self.MODULES),
      {"C0": "99/00", "P1": "99/00"},
    )

  def test_a_module_addressed_directly_reports_only_its_own_trace(self):
    self.assertEqual(find_error_fields("P1TPid1111er23", 2, "C0", self.MODULES), {"P1": "23"})


class TestReadId(unittest.TestCase):
  """The identifier is how a reply is matched to the command that asked for it."""

  def test_command_with_and_without_an_id(self):
    self.assertEqual(read_id("C0QMid1111er00/00"), 1111)
    self.assertIsNone(read_id("C0QM"))

  def test_a_malformed_id_is_rejected(self):
    with self.assertRaises(ValueError):
      read_id("C0QMidxx11")
