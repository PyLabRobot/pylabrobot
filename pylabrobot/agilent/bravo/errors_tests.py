import unittest

from pylabrobot.agilent.bravo.errors import (
  BravoError,
  ErrorType,
  RabbitErrorCode,
  rabbit_error_to_bravo_error,
)


class BravoErrorTests(unittest.TestCase):
  def test_default_message_for_error_type(self):
    err = BravoError(ErrorType.ROBOT_DISABLE)
    self.assertEqual(str(err), "Robot safety interlock is active (E-stop).")

  def test_message_includes_axis_label(self):
    err = BravoError(ErrorType.NOT_HOMED, axis="zg")
    self.assertEqual(str(err), "Axis is not homed. (Zg-axis)")

  def test_custom_text_overrides_default_message(self):
    err = BravoError(ErrorType.NOT_HOMED, axis="x", custom_text="totally custom")
    self.assertEqual(str(err), "totally custom")

  def test_repr_includes_error_type_and_axis(self):
    err = BravoError(ErrorType.NOT_HOMED, axis="zg")
    self.assertEqual(repr(err), "BravoError(error_type=NOT_HOMED, axis=Zg)")

  def test_repr_omits_absent_axis_and_custom_text(self):
    err = BravoError(ErrorType.NO_ERROR)
    self.assertEqual(repr(err), "BravoError(error_type=NO_ERROR)")

  def test_is_a_real_exception(self):
    with self.assertRaises(BravoError):
      raise BravoError(ErrorType.COULD_NOT_CONNECT)


class RabbitErrorConversionTests(unittest.TestCase):
  def test_none_maps_to_no_error(self):
    err = rabbit_error_to_bravo_error(RabbitErrorCode.NONE)
    self.assertEqual(err.error_type, ErrorType.NO_ERROR)

  def test_not_homed_range_maps_to_correct_axis(self):
    cases = {
      0x20: "x",
      0x21: "y",
      0x22: "z",
      0x23: "w",
      0x24: "g",
      0x25: "zg",
    }
    for code, axis in cases.items():
      err = rabbit_error_to_bravo_error(code)
      self.assertEqual(err.error_type, ErrorType.NOT_HOMED)
      self.assertEqual(err.axis, axis)

  def test_known_code_maps_to_mapped_error_type(self):
    err = rabbit_error_to_bravo_error(RabbitErrorCode.ROBOT_DISABLE)
    self.assertEqual(err.error_type, ErrorType.ROBOT_DISABLE)

  def test_unknown_code_falls_back(self):
    err = rabbit_error_to_bravo_error(0x7F)
    self.assertEqual(err.error_type, ErrorType.UNKNOWN_RABBIT_ERROR)


class ErrorTypeValueTests(unittest.TestCase):
  def test_invalid_tip_type_is_37(self):
    # 36 is deliberately skipped in the source numbering; pin this value so
    # an accidental renumber (e.g. inserting a member above it) is caught.
    self.assertEqual(ErrorType.INVALID_TIP_TYPE, 37)


if __name__ == "__main__":
  unittest.main()
