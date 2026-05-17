import unittest

from pylabrobot.byonoy.backend import (
  ABS1_ERROR_NAMES,
  ABS96_ERROR_NAMES,
  Abs1StatusError,
  Abs96StatusError,
  ByonoyDevice,
  ByonoyDriver,
  LUM96_PRESET_S,
  _GENERIC_ERROR_NAMES,
  _LED_EFFECT_CODES,
  encode_well_bitmask,
)


class EncodeWellBitmaskTests(unittest.TestCase):
  def test_all_false_is_zero_filled(self):
    self.assertEqual(encode_well_bitmask([False] * 96), b"\x00" * 12)

  def test_all_true_is_all_ones(self):
    self.assertEqual(encode_well_bitmask([True] * 96), b"\xff" * 12)

  def test_a1_only_sets_byte0_bit0(self):
    bools = [False] * 96
    bools[0] = True
    self.assertEqual(encode_well_bitmask(bools), b"\x01" + b"\x00" * 11)

  def test_h12_only_sets_byte11_bit7(self):
    bools = [False] * 96
    bools[95] = True
    self.assertEqual(encode_well_bitmask(bools), b"\x00" * 11 + b"\x80")

  def test_bits_7_and_8_cross_byte_boundary(self):
    bools = [False] * 96
    bools[7] = True  # byte 0, bit 7 → 0x80
    bools[8] = True  # byte 1, bit 0 → 0x01
    self.assertEqual(encode_well_bitmask(bools), b"\x80\x01" + b"\x00" * 10)

  def test_first_column_has_8_bits_set(self):
    bools = [False] * 96
    for r in range(8):
      bools[r * 12] = True
    result = encode_well_bitmask(bools)
    self.assertEqual(sum(bin(b).count("1") for b in result), 8)

  def test_custom_n_size(self):
    # bit 0 + bit 2 → 0x05
    self.assertEqual(encode_well_bitmask([True, False, True], n=3), b"\x05")

  def test_length_mismatch_raises(self):
    with self.assertRaises(ValueError):
      encode_well_bitmask([True] * 95)
    with self.assertRaises(ValueError):
      encode_well_bitmask([True] * 96, n=24)


class IntegrationModePresetTests(unittest.TestCase):
  def test_preset_values_match_vendor(self):
    self.assertEqual(LUM96_PRESET_S["rapid"], 0.1)
    self.assertEqual(LUM96_PRESET_S["sensitive"], 2.0)
    self.assertEqual(LUM96_PRESET_S["ultra_sensitive"], 20.0)

  def test_custom_is_not_a_preset(self):
    # "custom" is a Literal value but has no preset — read_luminescence
    # requires the caller to set integration_time explicitly.
    self.assertNotIn("custom", LUM96_PRESET_S)


class ErrorTableTests(unittest.TestCase):
  def test_generic_table_has_only_no_error(self):
    self.assertEqual(_GENERIC_ERROR_NAMES, {0: "NO_ERROR"})

  def test_abs96_table_round_trips(self):
    self.assertEqual(ABS96_ERROR_NAMES[0], "NO_ERROR")
    self.assertEqual(ABS96_ERROR_NAMES[1], "ERROR_CALIB")
    self.assertEqual(ABS96_ERROR_NAMES[Abs96StatusError.ERROR_NO_ACK], "ERROR_NO_ACK")

  def test_abs1_flag_bit_values(self):
    # AbsOne is a bit-flag enum; verify bit positions match the vendor header.
    self.assertEqual(Abs1StatusError.AMBIENT_LIGHT.value, 1)
    self.assertEqual(Abs1StatusError.MIN_LIGHT.value, 2)
    self.assertEqual(Abs1StatusError.USB.value, 4)
    self.assertEqual(Abs1StatusError.HARDWARE.value, 8)
    self.assertEqual(Abs1StatusError.NOISE_LIMIT.value, 128)

  def test_abs1_supports_combined_flags(self):
    combined = Abs1StatusError.AMBIENT_LIGHT | Abs1StatusError.HARDWARE
    self.assertEqual(combined.value, 9)

  def test_abs1_table_includes_all_enum_members(self):
    for member in Abs1StatusError:
      self.assertIn(member.value, ABS1_ERROR_NAMES)


class DescribeErrorCodeTests(unittest.TestCase):
  """describe_error_code() is pure — bypass __init__ to avoid HID setup."""

  def _make_driver(self, error_names):
    drv = ByonoyDriver.__new__(ByonoyDriver)
    drv._ERROR_NAMES = error_names
    return drv

  def test_known_code_returns_name(self):
    drv = self._make_driver(ABS96_ERROR_NAMES)
    self.assertEqual(drv.describe_error_code(1), "ERROR_CALIB")
    self.assertEqual(drv.describe_error_code(0), "NO_ERROR")

  def test_unknown_code_falls_back_to_hex(self):
    drv = self._make_driver(ABS96_ERROR_NAMES)
    self.assertEqual(drv.describe_error_code(0xAB), "errorCode=0xab")

  def test_generic_table_only_knows_no_error(self):
    # Lum96 uses the generic table — anything non-zero is the hex sentinel.
    drv = self._make_driver(_GENERIC_ERROR_NAMES)
    self.assertEqual(drv.describe_error_code(0), "NO_ERROR")
    self.assertEqual(drv.describe_error_code(7), "errorCode=0x07")
    self.assertEqual(drv.describe_error_code(255), "errorCode=0xff")


class LedEffectCodeTests(unittest.TestCase):
  def test_codes_cover_all_effect_literals(self):
    self.assertEqual(
      set(_LED_EFFECT_CODES),
      {"solid", "progress", "cylon", "rainbow", "blinking", "breathing"},
    )

  def test_solid_is_zero(self):
    self.assertEqual(_LED_EFFECT_CODES["solid"], 0x00)

  def test_codes_are_unique(self):
    self.assertEqual(len(set(_LED_EFFECT_CODES.values())), len(_LED_EFFECT_CODES))


class ByonoyDeviceEnumTests(unittest.TestCase):
  def test_distinct_values(self):
    self.assertNotEqual(ByonoyDevice.ABSORBANCE_96, ByonoyDevice.LUMINESCENCE_96)


if __name__ == "__main__":
  unittest.main()
