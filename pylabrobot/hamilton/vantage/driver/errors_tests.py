import unittest

from pylabrobot.hamilton.vantage.driver.errors import (
  VantageFirmwareError,
  check_fw_string_error,
  core96_errors,
  get_id_from_fw_response,
  ipg_errors,
  is_spontaneous_message,
  parse_vantage_fw_string,
  pip_errors,
  vantage_response_string_to_error,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton import vantage_backend as legacy

# Replies as a machine writes them: no padding on the id or the error code.
SUCCESS = "A1AMQWid2er0qw0"
UNKNOWN_COMMAND = 'A1PMQAid1er30et"Command: Unknown command"'
VERSION = 'A1AMRFid1er0rf"D.0.06.00.0065 2025-04-25 (MAG Arm)"'


class TestParsing(unittest.TestCase):
  """Parsing the parameters of a reply."""

  def test_parse_response_params(self):
    parsed = parse_vantage_fw_string("A1PMDAid1111", None)
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_vantage_fw_string("A1PMDAid1111", {"id": "int"})
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_vantage_fw_string('A1PMDAid1112rw"abc"', {"rw": "str"})
    self.assertEqual(parsed, {"id": 1112, "rw": "abc"})

    parsed = parse_vantage_fw_string("A1PMDAid1112rw-21", {"rw": "int"})
    self.assertEqual(parsed, {"id": 1112, "rw": -21})

    parsed = parse_vantage_fw_string("A1PMDAid1113rwABC", {"rw": "hex"})
    self.assertEqual(parsed, {"id": 1113, "rw": int("ABC", base=16)})

    parsed = parse_vantage_fw_string("A1PMDAid1113rw1 -2 +3", {"rw": "[int]"})
    self.assertEqual(parsed, {"id": 1113, "rw": [1, -2, 3]})

    with self.assertRaises(ValueError):
      # should fail with auto-added id.
      parse_vantage_fw_string("A1PMDrwbc", None)

    with self.assertRaises(ValueError):
      parse_vantage_fw_string("A1PMDA", {"id": "int"})

  def test_the_formats_the_caller_passed_are_not_changed(self):
    fmt = {"qw": "int"}
    parse_vantage_fw_string(SUCCESS, fmt)
    self.assertEqual(fmt, {"qw": "int"})

  def test_an_unknown_type_and_a_format_that_is_not_a_dict_are_refused(self):
    with self.assertRaises(ValueError):
      parse_vantage_fw_string(SUCCESS, {"qw": "float"})
    with self.assertRaises(TypeError):
      parse_vantage_fw_string(SUCCESS, ["qw"])  # type: ignore[arg-type]

  def test_a_reply_as_the_machine_writes_it_is_parsed(self):
    self.assertEqual(parse_vantage_fw_string(SUCCESS, {"qw": "int"}), {"qw": 0, "id": 2})
    self.assertEqual(
      parse_vantage_fw_string(VERSION, {"rf": "str"}),
      {"rf": "D.0.06.00.0065 2025-04-25 (MAG Arm)", "id": 1},
    )

  def test_text_in_quotes_is_not_mistaken_for_a_parameter(self):
    reply = 'A1AMRFid1er0rf"firmware id9 er5 qw1"'
    self.assertEqual(
      parse_vantage_fw_string(reply, {"rf": "str"}), {"rf": "firmware id9 er5 qw1", "id": 1}
    )
    self.assertEqual(parse_vantage_fw_string(reply, {"er": "int"}), {"er": 0, "id": 1})

  def test_text_in_quotes_ends_at_its_own_closing_quote(self):
    self.assertEqual(
      parse_vantage_fw_string('A1PMDAid1et"a"xy"b"', {"et": "str"}), {"et": "a", "id": 1}
    )


class TestId(unittest.TestCase):
  """The id of a reply."""

  def test_the_id_is_read_with_or_without_padding(self):
    self.assertEqual(get_id_from_fw_response(SUCCESS), 2)
    self.assertEqual(get_id_from_fw_response("A1AMQWid0007er0qw0"), 7)
    self.assertEqual(get_id_from_fw_response("A1AMQWid9999er0qw0"), 9999)

  def test_a_reply_without_an_id_raises_value_error(self):
    with self.assertRaises(ValueError):
      get_id_from_fw_response("A1AMQWer0qw0")


class TestErrorConversion(unittest.TestCase):
  """Turning an error reply into the errors it reports."""

  def test_parse_error_response(self):
    resp = 'I1AMRQid0000er4et"Slave not available"'
    self.assertEqual(
      vantage_response_string_to_error(resp),
      VantageFirmwareError(errors={"Cover": "Slave not available"}, raw_response=resp),
    )

    resp = 'I1AMLPid215er57et"S-Drive: Drive not initialized"'
    self.assertEqual(
      vantage_response_string_to_error(resp),
      VantageFirmwareError(
        errors={"Cover": "S-Drive: Drive not initialized"},
        raw_response=resp,
      ),
    )

    resp = 'A1HMDAid239er99es"H070"'
    self.assertEqual(
      vantage_response_string_to_error(resp),
      VantageFirmwareError(errors={"Core 96": "No liquid level found"}, raw_response=resp),
    )

    resp = 'A1PMDAid262er99es"P170 P270 P370 P470 P570 P670 P770 P870"'
    self.assertEqual(
      vantage_response_string_to_error(resp),
      VantageFirmwareError(
        errors={f"Pipetting channel {channel}": "No liquid level found" for channel in range(1, 9)},
        raw_response=resp,
      ),
    )

  def test_a_module_that_does_not_know_a_command_is_reported_under_its_own_name(self):
    self.assertEqual(
      vantage_response_string_to_error(UNKNOWN_COMMAND),
      VantageFirmwareError(
        errors={"Pip": "Command: Unknown command"}, raw_response=UNKNOWN_COMMAND
      ),
    )

  def test_an_error_reply_with_no_text_is_still_an_error(self):
    self.assertEqual(
      vantage_response_string_to_error("A1AMQWid2er5").errors, {"Arm": "Unknown error"}
    )

  def test_an_error_reply_with_no_text_is_explained_when_the_code_is_one_all_modules_share(self):
    errors = vantage_response_string_to_error("A1RMDIid6er16").errors
    self.assertEqual(list(errors), ["IPG"])
    self.assertIn("MI", errors["IPG"])
    self.assertIn("main initialization", errors["IPG"])
    self.assertIn("sequence", vantage_response_string_to_error("A1RMDGid2er19").errors["IPG"])

  def test_a_code_nobody_documents_stays_unknown_whichever_module_says_it(self):
    for command in ("A1RMDIid1er5", "A1PMDIid1er5", "A1AMMIid1er5"):
      self.assertEqual(
        list(vantage_response_string_to_error(command).errors.values()), ["Unknown error"]
      )

  def test_the_text_a_machine_gives_wins_over_the_table(self):
    self.assertEqual(
      vantage_response_string_to_error('A1RMDIid6er16et"Not ready"').errors, {"IPG": "Not ready"}
    )

  def test_a_reply_from_a_module_nobody_knows_is_reported_as_unknown(self):
    self.assertEqual(
      vantage_response_string_to_error('Z9ZZQWid2er5et"Broken"').errors,
      {"Unknown module": "Broken"},
    )

  def test_a_drive_code_nobody_knows_is_reported_as_an_unknown_error(self):
    self.assertEqual(
      vantage_response_string_to_error('A1PMDAid1er99es"P1999"').errors,
      {"Pipetting channel 1": "Unknown error"},
    )

  def test_errors_are_equal_when_they_say_the_same_and_can_be_hashed(self):
    a = VantageFirmwareError({"Arm": "x"}, "raw")
    b = VantageFirmwareError({"Arm": "x"}, "raw")
    self.assertEqual(a, b)
    self.assertEqual(len({a, b}), 1)
    self.assertNotEqual(a, VantageFirmwareError({"Arm": "y"}, "raw"))
    self.assertIn("Arm", str(a))


class TestCheckFwStringError(unittest.TestCase):
  """Raising on an error reply, and only on one."""

  def test_replies_without_an_error_do_not_raise(self):
    for reply in (
      SUCCESS,
      VERSION,
      "A1AMQWid0007er0qw0",
      "A1AMQWid0007er00qw0",
      "A1PMQWid3er0qw1",
      "A1AMMIid4er0",
    ):
      check_fw_string_error(reply)

  def test_a_nonzero_error_code_raises_with_what_the_module_said(self):
    with self.assertRaises(VantageFirmwareError) as caught:
      check_fw_string_error(UNKNOWN_COMMAND)
    self.assertEqual(caught.exception.errors, {"Pip": "Command: Unknown command"})
    self.assertEqual(caught.exception.raw_response, UNKNOWN_COMMAND)

    for reply in ('I1AMRQid0000er4et"Slave not available"', 'A1HMDAid239er99es"H070"'):
      with self.assertRaises(VantageFirmwareError):
        check_fw_string_error(reply)

  def test_text_in_quotes_that_looks_like_an_error_is_not_one(self):
    check_fw_string_error('A1AMRFid1er0rf"er5 firmware"')
    # With no error field at all, the quoted text is the only place an `er5` could come from.
    check_fw_string_error('A1AMRFid1rf"er5 firmware"')

  def test_an_error_in_a_reply_with_no_id_is_found(self):
    with self.assertRaises(VantageFirmwareError):
      check_fw_string_error('I1AMRQer4et"Slave not available"')


class TestSpontaneousMessages(unittest.TestCase):
  """The lines a machine sends without being asked."""

  def test_the_two_forms_a_machine_sends_are_recognised(self):
    self.assertTrue(is_spontaneous_message('A1AM__id0sm"Spontan Message"'))
    self.assertTrue(is_spontaneous_message('00A0.A1AM__id0sm"Spontan Message"'))

  def test_an_ordinary_reply_and_an_error_reply_are_not(self):
    for line in (SUCCESS, VERSION, UNKNOWN_COMMAND, "A1PMDAid262er99", "", "A1AM__", "A1AMQW__"):
      self.assertFalse(is_spontaneous_message(line), line)


class TestTables(unittest.TestCase):
  """The error tables are the ones the legacy backend has."""

  def test_the_tables_equal_the_legacy_ones(self):
    self.assertEqual(core96_errors, legacy.core96_errors)
    self.assertEqual(pip_errors, legacy.pip_errors)
    self.assertEqual(ipg_errors, legacy.ipg_errors)


if __name__ == "__main__":
  unittest.main()
