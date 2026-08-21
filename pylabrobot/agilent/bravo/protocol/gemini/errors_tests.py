import unittest

from pylabrobot.agilent.bravo.errors import BravoError, ErrorType
from pylabrobot.agilent.bravo.protocol.gemini.enums import CommandNAKTypes
from pylabrobot.agilent.bravo.protocol.gemini.errors import (
  GeminiTimeoutError,
  MultipacketError,
  NAKError,
  nak_to_bravo_error,
)


class GeminiTimeoutErrorTests(unittest.TestCase):
  def test_stores_timeout_in_seconds(self):
    # This is the unit the caller passed in -- seconds, not milliseconds.
    err = GeminiTimeoutError("Gemini GET timeout", timeout=5.0)
    self.assertEqual(err.timeout, 5.0)

  def test_timeout_defaults_to_none(self):
    err = GeminiTimeoutError("no timeout given")
    self.assertIsNone(err.timeout)

  def test_is_a_gemini_protocol_error(self):
    with self.assertRaises(GeminiTimeoutError):
      raise GeminiTimeoutError("boom", timeout=1.0)


class NAKErrorTests(unittest.TestCase):
  def test_known_nak_included_in_message(self):
    err = NAKError(CommandNAKTypes.OUT_OF_RANGE, sub_command=30, dest_node=4, dest_dev=1)
    self.assertIn("OUT_OF_RANGE", str(err))
    self.assertIn("4.1", str(err))
    self.assertIn("subcmd=30", str(err))
    self.assertEqual(err.nak, CommandNAKTypes.OUT_OF_RANGE)

  def test_unknown_nak_code(self):
    err = NAKError(0x7F)
    self.assertIsNone(err.nak)
    self.assertIn("UNKNOWN_NAK_127", str(err))


class MultipacketErrorTests(unittest.TestCase):
  def test_message_includes_device_and_count(self):
    err = MultipacketError(
      nak_code=CommandNAKTypes.INSTR_TBL_FULL, error_device_addr=0x44, num_exchanges=12
    )
    self.assertIn("INSTR_TBL_FULL", str(err))
    self.assertIn("0x44", str(err))
    self.assertIn("12", str(err))


class NakToBravoErrorTests(unittest.TestCase):
  def test_known_code_maps_to_expected_error_type(self):
    err = nak_to_bravo_error(CommandNAKTypes.OUT_OF_RANGE)
    self.assertIsInstance(err, BravoError)
    self.assertEqual(err.error_type, ErrorType.INVALID_DEST)

  def test_unknown_code_falls_back_to_darwin_generic(self):
    err = nak_to_bravo_error(0x7F)
    self.assertEqual(err.error_type, ErrorType.DARWIN_GENERIC)

  def test_custom_text_preserves_nak_name(self):
    err = nak_to_bravo_error(CommandNAKTypes.MOVE_IN_PROGRESS, sub_command=30, extra="node 4.0")
    self.assertIn("MOVE_IN_PROGRESS", str(err))
    self.assertIn("subcmd=30", str(err))
    self.assertIn("node 4.0", str(err))


if __name__ == "__main__":
  unittest.main()
