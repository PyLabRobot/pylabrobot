"""Tests for the PowerWave wire protocol.

The fixtures marked "captured" are real bytes recorded from a PowerWaveX w/
Incubator over RS-232. Fixtures marked "synthetic" are hand-built for
cases the capture does not cover.
"""

import asyncio
import math
import struct
import unittest
import unittest.mock
from typing import Awaitable, Callable, List, Optional, Tuple

from pylabrobot.agilent.biotek.powerwave import PowerWaveX
from pylabrobot.agilent.biotek.powerwave._protocol import (
  ASSAY_DEFINITION_LENGTH,
  ELX_ERRORS,
  ELX_FATAL_ERRORS,
  FLAG_USE_TOTAL_KINETIC_TIME,
  KINETIC_FLAGS,
  KINETIC_SHAKE_FLAGS,
  MAX_WAVELENGTH,
  MIN_WAVELENGTH,
  ORDER_SORTING_FILTER_BANDS,
  OVERRANGE,
  SCAN_WHOLE_STRIP,
  SELF_TEST_ERRORS,
  STATUS_LENGTH,
  Command,
  PowerWaveBusyError,
  PowerWaveChecksumError,
  PowerWaveError,
  PowerWaveFatalError,
  PowerWaveHaltedError,
  PowerWaveNakError,
  PowerWaveParseError,
  PowerWaveReadAbortedError,
  PowerWaveSelfTestError,
  PowerWaveStatusError,
  build_assay_definition,
  configuration_has_incubator,
  count_overrange,
  describe_elx_status,
  encode_assay_wavelengths,
  encode_plate_geometry,
  encode_scan_request,
  encode_strip_range,
  encode_temperature_setpoint,
  encode_wavelength_table,
  is_elx_status,
  parse_device_configuration,
  parse_elx_status,
  parse_plate,
  parse_scan_data,
  parse_scan_wavelength_list,
  parse_self_test_report,
  parse_status,
  parse_temperature,
  parse_uint16,
  parse_wavelength_table,
  plate_checksum,
  scan_checksum,
  scan_wavelengths,
  verify_plate_checksum,
)

# Captured: one wavelength's 96-well plate data block, through and including ^Z.
# The instrument transmitted its checksum byte, 142, immediately after the ^Z.
_CAPTURED_PLATE = (
  b"\r,+0201,+0207,+0142,+0171,+0129,+0188,+0162,+0184,+0161,+0126,+0099,+0122\r\n,+0191,+0202,+"
  b"0219,+0168,+0137,+0156,+0166,+0123,+0144,+0183,+0134,+0145\r\n,+0224,+0214,+0209,+0216,+0218"
  b",+0175,+0202,+0232,+0175,+0194,+0153,+0176\r\n,+0234,+0246,+0215,+0224,+0224,+0190,+0190,+01"
  b"89,+0162,+0155,+0164,+0177\r\n,+0279,+0249,+0256,+0250,+0209,+0236,+0186,+0189,+0179,+0172,+"
  b"0184,+0190\r\n,+0302,+0271,+0238,+0281,+0239,+0242,+0198,+0214,+0220,+0233,+0189,+0225\r\n,+"
  b"0298,+0218,+0239,+0247,+0264,+0254,+0212,+0275,+0268,+0234,+0237,+0270\r\n,+0282,+0259,+0272"
  b",+0260,+0308,+0290,+0246,+0300,+0268,+0251,+0239,+0281\r\n\x1a"
)
_CAPTURED_PLATE_CHECKSUM = 142

#: A 550 nm read of a carrier half covered by a sheet of plastic, captured from a
#: PowerWaveX w/ Incubator (firmware R06.20). The first hardware frame this driver
#: has seen containing overrange wells, and the first containing anything other
#: than 0.000: uncovered columns read 0.000, covered ones saturate, and finite
#: readings run as high as 4.032 OD - above the specified 3.000 limit yet still
#: sent as numbers rather than "*****".
_CAPTURED_OVERRANGE_PLATE = (
  b"\r,+0000,+0000,+0000,+0025,+2682,+3243,+3207,*****,+3567,+0000,+0000,+0000\r\n,+0000,+0000,+011"
  b"9,*****,*****,*****,*****,*****,+3690,*****,+0000,+0000\r\n,+0000,+0000,+0116,*****,*****,****"
  b"*,+4032,+3383,+3874,*****,+3881,+0000\r\n,+0000,+0000,+0062,*****,*****,+3916,+3516,+3450,****"
  b"*,+3685,*****,+0602\r\n,+0000,+0000,+0142,*****,+2250,+2793,+3155,+2722,*****,+3705,+3466,+268"
  b"2\r\n,+0000,+0000,+0093,*****,+2274,+1141,+3127,+4013,+3763,+3433,+3791,+0000\r\n,+0000,+0000,+0"
  b"091,+0728,*****,+1628,+3314,+3350,*****,+3681,+0000,+0000\r\n,+0000,+0000,+0000,+0003,+1959,+3"
  b"281,+3292,+3716,+3623,+0023,+0000,+0000\r\n\x1a"
)
_CAPTURED_OVERRANGE_CHECKSUM = 17


class TestCapturedOverrangePlate(unittest.TestCase):
  """The real overrange capture, which an empty carrier could never produce."""

  def test_checksum_verifies(self):
    self.assertEqual(plate_checksum(_CAPTURED_OVERRANGE_PLATE), _CAPTURED_OVERRANGE_CHECKSUM)
    verify_plate_checksum(_CAPTURED_OVERRANGE_PLATE, _CAPTURED_OVERRANGE_CHECKSUM)

  def test_parses_to_a_full_plate(self):
    grid = parse_plate(_CAPTURED_OVERRANGE_PLATE)
    self.assertEqual(len(grid), 8)
    self.assertTrue(all(len(row) == 12 for row in grid))

  def test_overrange_wells_become_infinite(self):
    grid = parse_plate(_CAPTURED_OVERRANGE_PLATE)
    self.assertEqual(count_overrange(grid), 20)
    self.assertTrue(all(math.isinf(v) for row in grid for v in row if math.isinf(v)))

  def test_finite_readings_span_blank_to_above_the_specified_limit(self):
    grid = parse_plate(_CAPTURED_OVERRANGE_PLATE)
    finite = [v for row in grid for v in row if not math.isinf(v)]
    self.assertEqual(min(finite), 0.0)  # the uncovered columns
    self.assertAlmostEqual(max(finite), 4.032)  # past 3.000 and still a number
    self.assertGreater(sum(1 for v in finite if v > 3.0), 0)


class TestStatusStrings(unittest.TestCase):
  """Status string handling, page B-21."""

  def test_ok(self):
    # Captured: the routine success response.
    self.assertEqual(parse_status(b"\x1e000\x03"), "000")

  def test_instrument_failure_raises(self):
    with self.assertRaises(PowerWaveStatusError) as ctx:
      parse_status(b"\x1e080\x03")
    self.assertEqual(ctx.exception.code, "080")
    self.assertIn("self-test", ctx.exception.message)

  def test_every_documented_error_code_raises(self):
    for code in ("080", "090", "0A0", "0B0", "0C0", "0E0"):
      with self.subTest(code=code):
        with self.assertRaises(PowerWaveStatusError) as ctx:
          parse_status(b"\x1e" + code.encode() + b"\x03")
        self.assertEqual(ctx.exception.code, code)

  def test_unknown_code_still_raises(self):
    with self.assertRaises(PowerWaveStatusError) as ctx:
      parse_status(b"\x1e0F0\x03")
    self.assertEqual(ctx.exception.message, "Unknown status code")

  def test_malformed_raises_parse_error(self):
    for response in (b"", b"\x1e000", b"000\x03", b"\x1e00000\x03", b"\x1e0000"):
      with self.subTest(response=response):
        with self.assertRaises(PowerWaveParseError):
          parse_status(response)


class TestInstrumentConfiguration(unittest.TestCase):
  """Instrument configuration, page B-19."""

  def test_captured_powerwavex_with_incubator(self):
    # Captured: the instrument sends the 16-bit value least significant byte
    # first, so 0x003A arrives as b":\x00".
    self.assertEqual(parse_device_configuration(b":\x00"), "PowerWaveX w/ Incubator")

  def test_plain_powerwavex(self):
    self.assertEqual(parse_device_configuration(b"\x32\x00"), "PowerWaveX")

  def test_unknown_configuration_raises(self):
    with self.assertRaises(PowerWaveParseError):
      parse_device_configuration(b"\xff\xff")

  def test_incubator_detection(self):
    self.assertTrue(configuration_has_incubator("PowerWaveX w/ Incubator"))
    self.assertFalse(configuration_has_incubator("PowerWaveX"))

  def test_uint16_is_little_endian(self):
    # Captured: the minimum kinetic interval read back as 60 seconds.
    self.assertEqual(parse_uint16(b"<\x00"), 60)

  def test_uint16_wrong_length_raises(self):
    with self.assertRaises(PowerWaveParseError):
      parse_uint16(b"\x00")


class TestWavelengthTable(unittest.TestCase):
  """Wavelength setup table, pages B-8 and B-11."""

  def test_captured_filter_wheel(self):
    # Captured: the six filters installed in the instrument, note the trailing comma.
    self.assertEqual(
      parse_wavelength_table(b"550,620,665,520,450,595,"),
      [550, 620, 665, 520, 450, 595],
    )

  def test_unpopulated_positions_are_dropped(self):
    # Synthetic: the manual specifies "000" for an unused filter position.
    self.assertEqual(parse_wavelength_table(b"550,620,000,000,000,000,"), [550, 620])

  def test_wrong_length_raises(self):
    with self.assertRaises(PowerWaveParseError):
      parse_wavelength_table(b"550,620,")

  def test_non_numeric_raises(self):
    with self.assertRaises(PowerWaveParseError):
      parse_wavelength_table(b"55X,620,665,520,450,595,")

  def test_encode_round_trips(self):
    encoded = encode_wavelength_table([550, 620, 665, 520, 450, 595])
    self.assertEqual(encoded, b"550,620,665,520,450,595,")
    self.assertEqual(parse_wavelength_table(encoded), [550, 620, 665, 520, 450, 595])

  def test_encode_pads_unused_positions(self):
    self.assertEqual(encode_wavelength_table([550]), b"550,000,000,000,000,000,")

  def test_encode_rejects_too_many(self):
    with self.assertRaises(ValueError):
      encode_wavelength_table([550, 620, 665, 520, 450, 595, 700])

  def test_encode_rejects_out_of_range(self):
    for bad in (199, 1000):
      with self.subTest(wavelength=bad):
        with self.assertRaises(ValueError):
          encode_wavelength_table([bad])

  def test_encode_rejects_non_integer(self):
    with self.assertRaises(TypeError):
      encode_wavelength_table([550.0])  # type: ignore[list-item]


class TestTemperature(unittest.TestCase):
  """Incubator temperature, pages B-17 and B-18."""

  def test_tenths_of_a_degree(self):
    self.assertAlmostEqual(parse_temperature(struct.pack("<H", 371)), 37.1)

  def test_zero_means_no_incubator(self):
    with self.assertRaises(PowerWaveError):
      parse_temperature(b"\x00\x00")

  def test_captured_setpoint_encoding(self):
    # Captured: setting 37 degrees C wrote a single 0x25 byte, not a 16-bit value.
    self.assertEqual(encode_temperature_setpoint(37), b"%")

  def test_zero_setpoint_is_allowed_and_turns_heaters_off(self):
    self.assertEqual(encode_temperature_setpoint(0), b"\x00")

  def test_out_of_range_raises(self):
    for bad in (21, 51, -1):
      with self.subTest(temperature=bad):
        with self.assertRaises(ValueError):
          encode_temperature_setpoint(bad)

  def test_non_integer_raises(self):
    with self.assertRaises(TypeError):
      encode_temperature_setpoint(37.5)  # type: ignore[arg-type]


class TestAssayDefinition(unittest.TestCase):
  """SET ASSAY DEFINITION table, page B-10."""

  def test_length_is_exactly_170(self):
    table = build_assay_definition([550])
    self.assertEqual(len(table), ASSAY_DEFINITION_LENGTH)

  def test_documented_byte_offsets(self):
    table = build_assay_definition(
      [550, 620],
      read_type="kinetic",
      flags=KINETIC_FLAGS,
      kinetic_interval=60,
      kinetic_read_count=48,
      shake_time=5,
      shake_speed=1,
      total_kinetic_read_time=9999,
    )
    # The manual numbers bytes from 1, so byte N is table[N - 1].
    self.assertEqual(table[0], 0)  # 1: don't care
    self.assertEqual(table[1:7], b"ABCDEF")  # 2-7: assay name
    self.assertEqual(table[7:30], bytes(23))  # 8-30: don't care
    self.assertEqual(table[30], KINETIC_FLAGS)  # 31: flags
    self.assertEqual(table[31], 31)  # 32: scans per well
    self.assertEqual(table[49:55], b"000000")  # 50-55: fixed
    self.assertEqual(table[55], 2)  # 56: read type kinetic
    self.assertEqual(table[59:61], struct.pack("<H", 60))  # 60-61: interval
    self.assertEqual(table[61:63], struct.pack("<H", 48))  # 62-63: read count
    self.assertEqual(table[66:84], b"550620000000000000")  # 67-84: wavelengths
    self.assertEqual(table[162:164], struct.pack("<H", 5))  # 163-164: shake time
    self.assertEqual(table[164], 1)  # 165: shake speed
    self.assertEqual(table[165:167], struct.pack("<H", 9999))  # 166-167: total time
    self.assertEqual(table[167], 0)  # 168: delay before first read
    self.assertEqual(table[168:170], struct.pack("<H", 2))  # 169-170: delay

  def test_read_type_byte(self):
    for read_type, expected in (("endpoint", 0), ("kinetic", 2), ("scan", 3)):
      with self.subTest(read_type=read_type):
        self.assertEqual(build_assay_definition([550], read_type=read_type)[55], expected)

  def test_unknown_read_type_raises(self):
    with self.assertRaises(ValueError):
      build_assay_definition([550], read_type="spectral")

  def test_matches_the_reference_endpoint_table(self):
    """Byte-for-byte equality with an endpoint table verified on real hardware.

    Pinned so that any refactor of :func:`build_assay_definition` cannot silently
    change the wire format.
    """
    wavelengths = [350, 550, 600, 630, 700, 990]
    command_wavelengths = bytearray()
    for this_wavelength in wavelengths:
      command_wavelengths += bytearray(str(this_wavelength).encode("utf-8"))
    for _ in range(6 - len(wavelengths)):
      command_wavelengths += bytearray(str("000").encode("utf-8"))
    expected = (
      bytearray([0])
      + bytearray(b"ABCDEF")
      + bytearray([0] * ((30 - 8) + 1))
      + bytearray([0])
      + bytearray([31])
      + bytearray([0] * (49 - 33 + 1))
      + bytearray(b"000000")
      + bytearray([0])
      + bytearray([0] * (59 - 57 + 1))
      + bytearray(struct.pack("<H", 5))
      + bytearray(struct.pack("<H", 2))
      + bytearray([0] * (66 - 64 + 1))
      + command_wavelengths
      + bytearray([0] * (162 - 85 + 1))
      + bytearray(struct.pack("<H", 3))
      + bytearray([1])
      + bytearray(struct.pack("<H", 1))
      + bytearray([0])
      + bytearray(struct.pack("<H", 2))
    )
    self.assertEqual(build_assay_definition(wavelengths, read_type="endpoint"), bytes(expected))

  def test_matches_the_reference_kinetic_table(self):
    """Byte-for-byte equality with a kinetic table verified on real hardware."""
    wavelengths = [600, 630]
    command_wavelengths = bytearray()
    for this_wavelength in wavelengths:
      command_wavelengths += bytearray(str(this_wavelength).encode("utf-8"))
    for _ in range(6 - len(wavelengths)):
      command_wavelengths += bytearray(str("000").encode("utf-8"))
    expected = (
      bytearray([0])
      + bytearray(b"ABCDEF")
      + bytearray([0] * ((30 - 8) + 1))
      + bytearray([240])
      + bytearray([31])
      + bytearray([0] * (49 - 33 + 1))
      + bytearray(b"000000")
      + bytearray([2])
      + bytearray([0] * (59 - 57 + 1))
      + bytearray(struct.pack("<H", 60))
      + bytearray(struct.pack("<H", 9999))
      + bytearray([0] * (66 - 64 + 1))
      + command_wavelengths
      + bytearray([0] * (162 - 85 + 1))
      + bytearray(struct.pack("<H", 5))
      + bytearray([1])
      + bytearray(struct.pack("<H", 9999))
      + bytearray([0])
      + bytearray(struct.pack("<H", 2))
    )
    built = build_assay_definition(
      wavelengths,
      read_type="kinetic",
      # The reference table sets the total-time bit, so pin it to keep this a
      # byte-for-byte check.
      flags=KINETIC_SHAKE_FLAGS | FLAG_USE_TOTAL_KINETIC_TIME,
      kinetic_interval=60,
      kinetic_read_count=9999,
      shake_time=5,
      shake_speed=1,
      total_kinetic_read_time=9999,
    )
    self.assertEqual(built, bytes(expected))

  def test_kinetic_flags_value(self):
    # A kinetic run does not shake unless asked, and every bit in the shake set is
    # about shaking, so the default byte is empty.
    self.assertEqual(KINETIC_FLAGS, 0)

    # 0x70: shake, shake before every read, continuous shake. The total-time bit
    # is excluded on purpose: with it set the instrument ignores the read count
    # and keeps cycling past the blocks the driver is waiting for.
    self.assertEqual(KINETIC_SHAKE_FLAGS, 0x70)
    self.assertEqual(KINETIC_SHAKE_FLAGS & FLAG_USE_TOTAL_KINETIC_TIME, 0)

  def test_wavelength_encoding_pads_to_eighteen_bytes(self):
    self.assertEqual(encode_assay_wavelengths([550]), b"550000000000000000")
    self.assertEqual(len(encode_assay_wavelengths([550, 620, 665])), 18)

  def test_requires_at_least_one_wavelength(self):
    with self.assertRaises(ValueError):
      build_assay_definition([])

  def test_validation_bounds(self):
    cases = {
      "scans_per_well": (0, 32, 30),  # must be odd, 1-31
      "kinetic_interval": (-1, 10000),
      "kinetic_read_count": (1, 10000),
      "shake_time": (-1, 1000),
      "shake_speed": (-1, 4),
      "total_kinetic_read_time": (0, 10000),
      "delay": (-1, 1000),
    }
    for field, bad_values in cases.items():
      for bad in bad_values:
        with self.subTest(field=field, value=bad):
          with self.assertRaises(ValueError):
            build_assay_definition([550], **{field: bad})  # type: ignore[arg-type]

  def test_assay_name_must_be_six_bytes(self):
    with self.assertRaises(ValueError):
      build_assay_definition([550], assay_name=b"SHORT")


class TestPlateChecksum(unittest.TestCase):
  """Plate data checksum, page B-9."""

  def test_captured_plate_checksum(self):
    self.assertEqual(plate_checksum(_CAPTURED_PLATE), _CAPTURED_PLATE_CHECKSUM)

  def test_verify_accepts_matching_checksum(self):
    verify_plate_checksum(_CAPTURED_PLATE, _CAPTURED_PLATE_CHECKSUM)

  def test_verify_rejects_mismatch(self):
    with self.assertRaises(PowerWaveChecksumError):
      verify_plate_checksum(_CAPTURED_PLATE, _CAPTURED_PLATE_CHECKSUM + 1)

  def test_leading_bytes_before_the_start_code_are_excluded(self):
    """A preceding block's checksum byte arrives ahead of the CR start code.

    The manual says the checksum starts after the data start code, so a leading
    byte must not change the result.
    """
    self.assertEqual(plate_checksum(b"\x8e" + _CAPTURED_PLATE), _CAPTURED_PLATE_CHECKSUM)

  def test_missing_start_code_raises(self):
    with self.assertRaises(PowerWaveParseError):
      plate_checksum(b",+0201,+0207\x1a")


class TestPlateParsing(unittest.TestCase):
  """Plate data parsing, page B-9."""

  def test_captured_plate_shape(self):
    data = parse_plate(_CAPTURED_PLATE)
    self.assertEqual(len(data), 8)
    self.assertTrue(all(len(row) == 12 for row in data))

  def test_captured_plate_values(self):
    data = parse_plate(_CAPTURED_PLATE)
    # "+0201" is +0.201 OD: the instrument omits the decimal point.
    self.assertEqual(
      data[0],
      [0.201, 0.207, 0.142, 0.171, 0.129, 0.188, 0.162, 0.184, 0.161, 0.126, 0.099, 0.122],
    )
    self.assertEqual(
      data[7],
      [0.282, 0.259, 0.272, 0.260, 0.308, 0.290, 0.246, 0.300, 0.268, 0.251, 0.239, 0.281],
    )

  def test_binary_checksum_prefix_does_not_break_parsing(self):
    """Regression: 330 of 332 captured frames begin with a non-UTF-8 checksum byte.

    Decoding the frame as UTF-8 before parsing raises UnicodeDecodeError on
    those, so the parser must work on bytes.
    """
    prefixed = b"\x8e" + _CAPTURED_PLATE
    with self.assertRaises(UnicodeDecodeError):
      prefixed.decode("utf-8")
    self.assertEqual(parse_plate(prefixed), parse_plate(_CAPTURED_PLATE))

  def test_negative_readings(self):
    # Synthetic: the sign character is significant.
    frame = _CAPTURED_PLATE.replace(b",+0201,", b",-0201,", 1)
    self.assertEqual(parse_plate(frame)[0][0], -0.201)

  def test_overrange_wells(self):
    # Synthetic: the capture has no saturated wells, so substitute one.
    frame = _CAPTURED_PLATE.replace(b",+0201,", b",*****,", 1)
    data = parse_plate(frame)
    self.assertTrue(math.isinf(data[0][0]))
    self.assertEqual(data[0][0], OVERRANGE)
    self.assertEqual(count_overrange(data), 1)

  def test_wrong_reading_count_raises(self):
    truncated = _CAPTURED_PLATE.replace(b",+0122\r\n", b"\r\n", 1)
    with self.assertRaises(PowerWaveParseError):
      parse_plate(truncated)

  def test_non_default_plate_geometry(self):
    # Synthetic: 24-well plates are 4x6. One CR LF-terminated line per row, as
    # every captured frame has.
    frame = b"\r" + b"".join(b",+0100" * 6 + b"\r\n" for _ in range(4)) + b"\x1a"
    data = parse_plate(frame, num_rows=4, num_columns=6)
    self.assertEqual(len(data), 4)
    self.assertEqual(len(data[0]), 6)

  def test_rows_must_be_separate_lines(self):
    # All 24 readings on one line is not a 4x6 plate, however many values it has.
    frame = b"\r" + b",+0100" * 24 + b"\r\n\x1a"
    with self.assertRaises(PowerWaveParseError):
      parse_plate(frame, num_rows=4, num_columns=6)

  def test_a_checksum_neutral_edit_is_rejected(self):
    # "," up two and "7" down two leaves the byte sum untouched, so only the row
    # structure catches it. Without that check A2 silently reads 0.205.
    tampered = _CAPTURED_PLATE.replace(b",+0201,+0207", b",+0201.+0205", 1)
    self.assertEqual(plate_checksum(tampered), plate_checksum(_CAPTURED_PLATE))
    with self.assertRaises(PowerWaveParseError):
      parse_plate(tampered)

  def test_structural_damage_is_rejected(self):
    for label, bad in (
      ("no ^Z terminator", _CAPTURED_PLATE[:-1]),
      ("a row short of a reading", _CAPTURED_PLATE.replace(b",+0122\r\n", b"\r\n", 1)),
      ("junk after a reading", _CAPTURED_PLATE.replace(b",+0207", b",+0207Z", 1)),
      ("a row not starting with a comma", _CAPTURED_PLATE.replace(b"\r,+0201", b"\r+0201", 1)),
    ):
      with self.subTest(label=label):
        with self.assertRaises(PowerWaveParseError):
          parse_plate(bad)

  def test_count_overrange_on_clean_plate(self):
    self.assertEqual(count_overrange(parse_plate(_CAPTURED_PLATE)), 0)


class TestCommandBytes(unittest.TestCase):
  """The command bytes must match the ASCII characters the manual names."""

  def test_command_bytes_are_the_documented_characters(self):
    expected = {
      "STORE_PLATE_CARRIER": b"A",
      "PRESENT_PLATE_CARRIER": b"J",
      "CONFIGURE_WAVELENGTHS": b"M",
      "READ_PLATE": b"S",
      "SET_ASSAY_DEFINITION": b"V",
      "GET_WAVELENGTH_TABLE": b"W",
      "HALT": b"X",
      "SET_PLATE_GEOMETRY": b"{",
      "SET_STRIP_RANGE": b"%",
      "GET_MINIMUM_KINETIC_INTERVAL": b"$",
      "SCAN_PLATE": b"&",
      "SELF_TEST": b"*",
      "SET_TEMPERATURE": b"[",
      "GET_TEMPERATURE": b"]",
      "GET_PLATE_BARCODE": b"s",
      "GET_INSTRUMENT_CONFIGURATION": b"}",
      "CLOSE_PLATE_ACCESS_DOOR": b")",
    }
    for name, char in expected.items():
      with self.subTest(command=name):
        self.assertEqual(Command[name].value, char)

  def test_all_documented_commands_are_present(self):
    self.assertEqual(len(Command), 17)


if __name__ == "__main__":
  unittest.main()


class _FakeSerial:
  """A scripted stand-in for :class:`pylabrobot.io.serial.Serial`.

  Serves ``script`` to reads and records every write, so tests can assert on
  command sequencing as well as on parsed results.
  """

  def __init__(self, script: bytes = b"") -> None:
    self._inbox = bytearray(script)
    self.writes: list = []
    self.port = "/dev/ttyUSB0"
    self.setup_called = False
    self.stop_called = False

  async def setup(self) -> None:
    self.setup_called = True

  async def stop(self) -> None:
    self.stop_called = True

  async def write(self, data: bytes) -> None:
    self.writes.append(bytes(data))

  async def read(self, num_bytes: int = 1) -> bytes:
    chunk = bytes(self._inbox[:num_bytes])
    del self._inbox[:num_bytes]
    return chunk

  async def reset_input_buffer(self) -> None:
    pass

  async def reset_output_buffer(self) -> None:
    pass

  def get_read_timeout(self) -> float:
    return 1.0

  @property
  def remaining(self) -> bytes:
    return bytes(self._inbox)


class _FakeSerialWithStaleBytes(_FakeSerial):
  """A fake line holding stale bytes now and its scripted replies until asked.

  A real instrument stays silent until it is given a command, but it may still be
  flushing the tail of a response its last caller never read. Modelling both is
  what makes the stale-byte drain in ``setup()`` testable, since the plain
  :class:`_FakeSerial` answers a read before anything has been written.
  """

  def __init__(self, stale: bytes, script: bytes) -> None:
    super().__init__(stale)
    self._pending = bytearray(script)

  async def write(self, data: bytes) -> None:
    await super().write(data)
    self._inbox.extend(self._pending)
    self._pending.clear()


_ACK = b"\x06"
_NAK = b"\x15"
_STATUS_OK = b"\x1e000\x03"
_CAPTURED_CONFIG = b":\x00"  # PowerWaveX w/ Incubator, low byte first
_CAPTURED_TABLE = b"550,620,665,520,450,595,"


def _reader(script: bytes = b"", *, timeout: float = 0.2, model: Optional[str] = None):
  """Build a PowerWaveX wired to a scripted fake transport."""
  reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=timeout)
  fake = _FakeSerial(script)
  reader.io = fake  # type: ignore[assignment]
  # No real line to settle, so skip the post-assay-definition NAK flush delay.
  reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
  # The scripted transport answers a read before any command is written, so the
  # stale-byte drain in setup() would eat the script. Flush only.
  reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
  # halt() calls the last byte before silence its acknowledgement; a scripted
  # transport falls silent at once, so it need not wait a real second for that.
  reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]
  if model is not None:
    reader._model = model
  return reader, fake


class TestLifecycle(unittest.IsolatedAsyncioTestCase):
  """setup() and stop()."""

  async def test_setup_identifies_instrument(self):
    # '}' -> ACK + 2 config bytes and no status string (page B-19),
    # then 'W' -> ACK + 24 table bytes + status string (page B-11).
    script = _ACK + _CAPTURED_CONFIG + _ACK + _CAPTURED_TABLE + _STATUS_OK
    reader, fake = _reader(script)

    await reader.setup()

    self.assertTrue(fake.setup_called)
    self.assertEqual(fake.writes, [b"}", b"W"])
    self.assertEqual(reader.model, "PowerWaveX w/ Incubator")
    self.assertEqual(reader.wavelength_table, [550, 620, 665, 520, 450, 595])
    self.assertTrue(reader.connected)
    self.assertTrue(reader.supports_heating)
    self.assertFalse(reader.supports_cooling)
    self.assertEqual(fake.remaining, b"")

  async def test_stop_closes(self):
    reader, fake = _reader(_ACK + _STATUS_OK)
    await reader.open()

    await reader.stop()

    self.assertTrue(fake.stop_called)
    self.assertFalse(reader.connected)

  async def test_properties_raise_before_setup(self):
    reader, _ = _reader()
    with self.assertRaises(RuntimeError):
      _ = reader.model
    with self.assertRaises(RuntimeError):
      _ = reader.wavelength_table

  async def test_temperature_range(self):
    reader, _ = _reader()
    self.assertEqual(reader.temperature_range, (22.0, 50.0))


class TestPlateCarrier(unittest.IsolatedAsyncioTestCase):
  """Carrier movement, page B-7."""

  async def test_open_sends_J(self):
    reader, fake = _reader(_ACK + _STATUS_OK)
    await reader.open()
    self.assertEqual(fake.writes, [b"J"])

  async def test_close_sends_A(self):
    reader, fake = _reader(_ACK + _STATUS_OK)
    await reader.close()
    self.assertEqual(fake.writes, [b"A"])

  async def test_open_then_close_round_trip(self):
    reader, fake = _reader(_ACK + _STATUS_OK + _ACK + _STATUS_OK)
    await reader.open()
    await reader.close()
    self.assertEqual(fake.writes, [b"J", b"A"])

  async def test_status_error_propagates(self):
    reader, _ = _reader(_ACK + b"\x1e090\x03")
    with self.assertRaises(PowerWaveStatusError) as ctx:
      await reader.open()
    self.assertEqual(ctx.exception.code, "090")

  async def test_nak_raises(self):
    reader, _ = _reader(_NAK)
    with self.assertRaises(PowerWaveNakError):
      await reader.open()

  async def test_unexpected_reply_raises(self):
    reader, _ = _reader(b"Z")
    with self.assertRaises(PowerWaveError):
      await reader.open()

  async def test_silence_raises_timeout(self):
    reader, _ = _reader(b"")
    with self.assertRaises(TimeoutError):
      await reader.open()


class TestHaltInterruptsAReadInProgress(unittest.IsolatedAsyncioTestCase):
  """HALT has to take the serial port away from a read, not queue behind it.

  Its whole purpose is aborting a run, which means being called while one is
  outstanding - a GUI's stop button. Both used to poll the same bytes, so the
  read would swallow HALT's DLE and HALT would then time out.
  """

  class _SilentThenDLE(_FakeSerial):
    """A line that answers nothing until HALT is sent, then acknowledges it."""

    def __init__(self, preamble: bytes = b""):
      super().__init__(preamble)
      self.halted = False

    async def write(self, data: bytes) -> None:
      await super().write(data)
      if data == b"X":
        self.halted = True
        self._inbox.extend(b"\x10")

    async def read(self, num_bytes: int = 1) -> bytes:
      await asyncio.sleep(0)  # let the other task run
      return await super().read(num_bytes)

  async def test_halt_interrupts_the_read_and_is_acknowledged(self):
    plate = _plate_resource()
    fake = self._SilentThenDLE(_ACK + _STATUS_OK + _ACK + _STATUS_OK + _ACK + _STATUS_OK)
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=30)
    reader.io = fake  # type: ignore[assignment]
    reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._model = "PowerWaveX"
    reader._wavelength_table = [620]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]

    read = asyncio.create_task(reader.read_absorbance(plate, plate.get_all_items(), 620))
    while reader._port_free.is_set():  # wait until the read owns the port
      await asyncio.sleep(0)

    await reader.halt()

    self.assertTrue(fake.halted)

    with self.assertRaises(PowerWaveHaltedError):
      await read

  async def test_halt_owns_the_port_when_it_sends_the_command(self):
    # The read must have let go before X goes out, and halt must hold the port
    # itself from then on, so nothing starts a read into the DLE it waits for.
    plate = _plate_resource()
    fake = self._SilentThenDLE(_ACK + _STATUS_OK + _ACK + _STATUS_OK + _ACK + _STATUS_OK)
    seen: dict = {}

    original_write = fake.write

    async def watching_write(data: bytes) -> None:
      if data == b"X":
        seen["owner_is_halt"] = reader._port_owner is asyncio.current_task()
        seen["port_claimed"] = not reader._port_free.is_set()
      await original_write(data)

    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=30)
    reader.io = fake  # type: ignore[assignment]
    reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._model = "PowerWaveX"
    reader._wavelength_table = [620]
    fake.write = watching_write  # type: ignore[method-assign]

    read = asyncio.create_task(reader.read_absorbance(plate, plate.get_all_items(), 620))
    while reader._port_free.is_set():
      await asyncio.sleep(0)
    await reader.halt()
    with self.assertRaises(PowerWaveHaltedError):
      await read

    self.assertTrue(seen.get("owner_is_halt"), "halt did not own the port when it sent X")
    self.assertTrue(seen.get("port_claimed"), "the port was left free while halt ran")

  async def test_a_second_read_is_refused_while_one_is_running(self):
    plate = _plate_resource()
    fake = self._SilentThenDLE(_ACK + _STATUS_OK + _ACK + _STATUS_OK + _ACK + _STATUS_OK)
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=30)
    reader.io = fake  # type: ignore[assignment]
    reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._model = "PowerWaveX"
    reader._wavelength_table = [620]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]

    read = asyncio.create_task(reader.read_absorbance(plate, plate.get_all_items(), 620))
    while reader._port_free.is_set():
      await asyncio.sleep(0)

    with self.assertRaises(PowerWaveBusyError) as caught:
      await reader.read_absorbance(plate, plate.get_all_items(), 620)
    self.assertIn("using the serial port", str(caught.exception))

    await reader.halt()
    with self.assertRaises(PowerWaveHaltedError):
      await read

  async def test_the_port_is_usable_again_after_a_halt(self):
    plate = _plate_resource()
    fake = self._SilentThenDLE(_ACK + _STATUS_OK + _ACK + _STATUS_OK + _ACK + _STATUS_OK)
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=30)
    reader.io = fake  # type: ignore[assignment]
    reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._model = "PowerWaveX"
    reader._wavelength_table = [620]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]

    read = asyncio.create_task(reader.read_absorbance(plate, plate.get_all_items(), 620))
    while reader._port_free.is_set():
      await asyncio.sleep(0)
    await reader.halt()
    with self.assertRaises(PowerWaveHaltedError):
      await read

    # The halt flag must not linger, or every later read fails immediately.
    self.assertFalse(reader._halt_requested.is_set())
    self.assertTrue(reader._port_free.is_set())


class TestPortOwnership(unittest.IsolatedAsyncioTestCase):
  """Every exchange owns the serial port, not just reads.

  Guarding reads against each other left any other command free to interleave
  with one. A display polling the incubator temperature on a timer would read
  bytes belonging to a read in progress.
  """

  async def test_a_command_is_refused_while_a_read_runs(self):
    plate = _plate_resource()
    fake = TestHaltInterruptsAReadInProgress._SilentThenDLE(
      _ACK + _STATUS_OK + _ACK + _STATUS_OK + _ACK + _STATUS_OK
    )
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=30)
    reader.io = fake  # type: ignore[assignment]
    reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._model = "PowerWaveX w/ Incubator"
    reader._wavelength_table = [620]

    read = asyncio.create_task(reader.read_absorbance(plate, plate.get_all_items(), 620))
    while reader._port_free.is_set():
      await asyncio.sleep(0)

    for label, call in (
      ("temperature", reader.request_current_temperature()),
      ("model", reader.request_model()),
      ("carrier", reader.open()),
      ("setpoint", reader.set_temperature(30)),
    ):
      with self.subTest(command=label):
        with self.assertRaises(PowerWaveBusyError):
          await call

    await reader.halt()
    with self.assertRaises(PowerWaveHaltedError):
      await read

  async def test_the_assay_push_owns_the_port_while_it_flushes(self):
    # _push_assay_definition settles and flushes to discard the stray NAK the
    # instrument emits after accepting a table. Released beforehand, the port
    # read as free for the two seconds that takes, and a command entering the
    # window had its reply flushed away for no visible reason.
    class _RecordingFlush(_FakeSerial):
      def __init__(self, script: bytes):
        super().__init__(script)
        self.owner_while_flushing: object = "never flushed"

      async def reset_input_buffer(self) -> None:
        self.owner_while_flushing = reader._port_owner
        self._inbox.clear()

    fake = _RecordingFlush(_ACK + _STATUS_OK)
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=5)
    reader.io = fake  # type: ignore[assignment]
    reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._model = "PowerWaveX"

    await reader._push_assay_definition([450], read_type="endpoint")

    self.assertIsNotNone(fake.owner_while_flushing)
    self.assertNotEqual(fake.owner_while_flushing, "never flushed")
    self.assertIsNone(reader._port_owner)  # and released once finished

  async def test_a_command_cannot_enter_the_assay_flush_window(self):
    class _SlowFlush(_FakeSerial):
      def __init__(self, script: bytes):
        super().__init__(script)
        self.outcome: str = "flush never ran"

      async def reset_input_buffer(self) -> None:
        # From a separate task: re-entrancy deliberately lets the owning task
        # back in, so only another task can show the window is closed.
        async def intruder():
          try:
            await reader.request_current_temperature()
            return "accepted"
          except PowerWaveBusyError:
            return "refused"

        self.outcome = await asyncio.create_task(intruder())
        self._inbox.clear()

    fake = _SlowFlush(_ACK + _STATUS_OK)
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=5)
    reader.io = fake  # type: ignore[assignment]
    reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._model = "PowerWaveX w/ Incubator"

    await reader._push_assay_definition([450], read_type="endpoint")

    self.assertEqual(fake.outcome, "refused")

  async def test_a_composite_operation_may_claim_the_port_once(self):
    # read_absorbances holds the port and still calls _push_assay_definition and
    # _set_plate_geometry, which claim it too. Re-entrancy within one task is why
    # that is not a deadlock or a spurious busy error.
    plate = _plate_resource()
    reader, fake = _reader(_read_script(), model="PowerWaveX")
    reader._wavelength_table = [620]

    result = (await reader.read_absorbance(plate, plate.get_all_items(), 620))[0]

    self.assertEqual(len(result.data), 8)
    self.assertEqual(fake.remaining, b"")
    self.assertIsNone(reader._port_owner)

  async def test_the_port_is_released_when_an_operation_raises(self):
    plate = _plate_resource()
    reader, _ = _reader(_ACK + _STATUS_OK + _ACK + b"\x1e080\x03", model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveStatusError):
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

    self.assertIsNone(reader._port_owner)
    self.assertTrue(reader._port_free.is_set())


class TestResynchronisationAfterAFailure(unittest.IsolatedAsyncioTestCase):
  """A broken exchange hands the port back clean.

  Nothing records that the stream needs recovery. The operation that broke it
  clears up on its way out, so there is no mode for a later call to be stuck in
  and nothing has to decide when recovery has happened.
  """

  @staticmethod
  def _bad_checksum_script():
    wrong = (_CAPTURED_PLATE_CHECKSUM + 1) % 256
    # 'V', '{', 'S' acknowledged, then a block whose checksum does not match,
    # and the end-of-data marker that the read will never reach.
    return (_ACK + _STATUS_OK) * 3 + _CAPTURED_PLATE + bytes([wrong]) + b"\x1a"

  async def test_the_leftover_terminator_does_not_answer_the_next_command(self):
    plate = _plate_resource()
    reader, fake = _reader(self._bad_checksum_script(), model="PowerWaveX")
    reader._DRAIN_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveChecksumError):
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

    # Without the clean-up this ^Z was still queued, and the next command read it
    # as its acknowledgement: "expected ACK or NAK ... got b'\x1a'".
    self.assertEqual(fake.remaining, b"")

    fake._inbox.extend(_ACK + _CAPTURED_CONFIG)
    self.assertEqual(await reader.request_model(), "PowerWaveX w/ Incubator")

  async def test_the_port_is_free_after_a_failed_exchange(self):
    plate = _plate_resource()
    reader, _ = _reader(self._bad_checksum_script(), model="PowerWaveX")
    reader._DRAIN_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveChecksumError):
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

    self.assertIsNone(reader._port_owner)
    self.assertTrue(reader._port_free.is_set())

  async def test_a_halted_read_is_not_drained_here(self):
    # halt() resynchronises the line itself and is waiting for the release, so
    # draining in this path would sit through the run halt just abandoned.
    drained = []

    class _Watching(TestHaltInterruptsAReadInProgress._SilentThenDLE):
      async def reset_input_buffer(self) -> None:
        drained.append(True)
        await super().reset_input_buffer()

    plate = _plate_resource()
    fake = _Watching(_ACK + _STATUS_OK + _ACK + _STATUS_OK + _ACK + _STATUS_OK)
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=30)
    reader.io = fake  # type: ignore[assignment]
    reader._ASSAY_SETTLE_SECONDS = 0  # type: ignore[misc]
    reader._DRAIN_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._model = "PowerWaveX"
    reader._wavelength_table = [620]

    read = asyncio.create_task(reader.read_absorbance(plate, plate.get_all_items(), 620))
    while reader._port_free.is_set():
      await asyncio.sleep(0)
    await reader.halt()
    with self.assertRaises(PowerWaveHaltedError):
      await read

    self.assertEqual(drained, [])  # the halted read left the line to halt()


class TestSetupFailure(unittest.IsolatedAsyncioTestCase):
  """A session that could not identify the instrument is not a usable session."""

  async def test_a_failed_setup_closes_the_transport(self):
    reader, fake = _reader(b"")  # answers nothing, so identification times out

    with self.assertRaises((TimeoutError, PowerWaveError)):
      await reader.setup()

    self.assertFalse(reader.connected)
    self.assertTrue(fake.stop_called)

  async def test_a_successful_setup_still_connects(self):
    script = _ACK + _CAPTURED_CONFIG + _ACK + _CAPTURED_TABLE + _STATUS_OK
    reader, fake = _reader(script)

    await reader.setup()

    self.assertTrue(reader.connected)
    self.assertFalse(fake.stop_called)


class TestHaltAcknowledgement(unittest.IsolatedAsyncioTestCase):
  """HALT's DLE is told from the data by position, not by its value."""

  class _LeftoversThenDLE(_FakeSerial):
    """A line still streaming an abandoned read when HALT arrives."""

    def __init__(self, leftovers: bytes):
      super().__init__(b"")
      self._leftovers = leftovers

    async def write(self, data: bytes) -> None:
      await super().write(data)
      if data == b"X":
        # Everything still in flight, then the acknowledgement last.
        self._inbox.extend(self._leftovers + b"\x10")

  async def _halt_with(self, leftovers: bytes):
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=5)
    fake = self._LeftoversThenDLE(leftovers)
    reader.io = fake  # type: ignore[assignment]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._model = "PowerWaveX"
    await reader.halt()
    return reader, fake

  async def test_a_checksum_of_0x10_is_not_taken_for_the_acknowledgement(self):
    # A plate checksum is a raw byte, so one in 256 is 0x10. Stopping at the
    # first DLE took such a byte for the acknowledgement and left the real one
    # queued as the reply to whatever ran next.
    block = b"\r,+0100,+0100\r\n\x1a" + bytes([0x10])
    reader, fake = await self._halt_with(block)

    self.assertEqual(fake.remaining, b"")  # nothing left for the next command

  async def test_several_leftover_dle_bytes_are_all_consumed(self):
    leftovers = b"\r,+0100\r\n\x1a" + bytes([0x10]) + b"\r,+0100\r\n\x1a" + bytes([0x10])
    reader, fake = await self._halt_with(leftovers)
    self.assertEqual(fake.remaining, b"")

  async def test_the_homing_gap_before_the_dle_is_waited_out(self):
    # The instrument homes its axes before acknowledging, so the leftovers are
    # followed by a gap and then the DLE. Treating the first quiet moment as the
    # end picks up the end of the leftovers and leaves the DLE queued.
    class _HomingGap(_FakeSerial):
      def __init__(self, leftovers: bytes):
        super().__init__(b"")
        self._leftovers = leftovers
        self._pending_dle = False

      async def write(self, data: bytes) -> None:
        await super().write(data)
        if data == b"X":
          self._inbox.extend(self._leftovers)
          self._pending_dle = True

      async def read(self, num_bytes: int = 1) -> bytes:
        await asyncio.sleep(0)
        if len(self._inbox) == 0 and self._pending_dle:
          self._pending_dle = False  # the gap ends; now it acknowledges
          self._inbox.extend(b"\x10")
          return b""  # one quiet poll first, as the real gap gives
        return await super().read(num_bytes)

    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=5)
    fake = _HomingGap(b"\r,+0100,+0100\r\n\x1a\x42")
    reader.io = fake  # type: ignore[assignment]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._model = "PowerWaveX"

    await reader.halt()

    self.assertEqual(fake.remaining, b"")  # the DLE was waited for, not missed

  async def test_silence_without_a_dle_is_an_error(self):
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=2)
    fake = _FakeSerial(b"\r,+0100\r\n\x1a")  # data, then quiet, never a DLE
    reader.io = fake  # type: ignore[assignment]
    reader._DRAIN_QUIET_SECONDS = 0  # type: ignore[misc]
    reader._HALT_QUIET_SECONDS = 0.01  # type: ignore[misc]
    reader._model = "PowerWaveX"

    with self.assertRaises(TimeoutError) as caught:
      await reader.halt()
    self.assertIn("not acknowledged", str(caught.exception))


class TestHalt(unittest.IsolatedAsyncioTestCase):
  """HALT, page B-12: no ACK, no status string, a single DLE when done."""

  async def test_halt_waits_for_dle(self):
    reader, fake = _reader(b"\x10")
    await reader.halt()
    self.assertEqual(fake.writes, [b"X"])

  async def test_halt_without_dle_times_out(self):
    reader, _ = _reader(b"")
    with self.assertRaises(TimeoutError):
      await reader.halt()


class TestIncubator(unittest.IsolatedAsyncioTestCase):
  """Incubator control, pages B-17 and B-18."""

  async def test_read_temperature(self):
    reader, fake = _reader(
      _ACK + struct.pack("<H", 371) + _STATUS_OK, model="PowerWaveX w/ Incubator"
    )
    self.assertAlmostEqual(await reader.request_current_temperature(), 37.1)
    self.assertEqual(fake.writes, [b"]"])

  async def test_read_temperature_without_incubator_raises(self):
    # 0x0000 means no incubator or an incubator error; the status string follows.
    reader, _ = _reader(_ACK + b"\x00\x00" + b"\x1e0C0\x03", model="PowerWaveX")
    with self.assertRaises(PowerWaveError):
      await reader.request_current_temperature()

  async def test_set_temperature_writes_single_byte(self):
    reader, fake = _reader(_ACK + _STATUS_OK, model="PowerWaveX w/ Incubator")
    await reader.set_temperature(37)
    self.assertEqual(fake.writes, [b"[", b"%"])

  async def test_the_setpoint_is_always_sent(self):
    reader, fake = _reader((_ACK + _STATUS_OK) * 2, model="PowerWaveX w/ Incubator")
    await reader.set_temperature(37)
    await reader.set_temperature(37)
    self.assertEqual(fake.writes, [b"[", b"%", b"[", b"%"])

  async def test_stop_heating_sets_zero(self):
    reader, fake = _reader(_ACK + _STATUS_OK, model="PowerWaveX w/ Incubator")
    await reader.stop_heating()
    self.assertEqual(fake.writes, [b"[", b"\x00"])

  async def test_set_temperature_out_of_range_raises(self):
    reader, fake = _reader(model="PowerWaveX w/ Incubator")
    with self.assertRaises(ValueError):
      await reader.set_temperature(60)
    self.assertEqual(fake.writes, [])

  async def test_set_temperature_without_incubator_raises(self):
    reader, fake = _reader(model="PowerWaveX")
    self.assertFalse(reader.supports_heating)
    with self.assertRaises(NotImplementedError):
      await reader.set_temperature(37)
    self.assertEqual(fake.writes, [])


class TestMinimumKineticInterval(unittest.IsolatedAsyncioTestCase):
  """GET MINIMUM KINETIC INTERVAL, page B-13."""

  async def test_captured_interval(self):
    # Captured: 60 seconds, as b"<\x00" low byte first.
    reader, fake = _reader(_ACK + b"<\x00" + _STATUS_OK)
    self.assertEqual(await reader.request_minimum_kinetic_interval(), 60)
    self.assertEqual(fake.writes, [b"$"])


class TestStatusFraming(unittest.IsolatedAsyncioTestCase):
  """Status string framing robustness."""

  async def test_stale_bytes_before_status_are_discarded(self):
    """A plate data checksum left unread by a previous command precedes the status.

    Captured traffic shows these stray bytes routinely, so the driver skips them
    with a warning rather than failing to parse.
    """
    reader, _ = _reader(_ACK + b"\x8e" + _STATUS_OK)
    with self.assertLogs("pylabrobot.agilent.biotek.powerwave.powerwave", level="WARNING") as logs:
      await reader.open()
    self.assertIn("discarding 1 unexpected bytes", "\n".join(logs.output))


def _plate_resource(name: str = "plate"):
  """A standard 8x12 plate for read tests."""
  from pylabrobot.resources import cellvis_96_wellplate_350uL_Fb

  return cellvis_96_wellplate_350uL_Fb(name=name)


def _read_script(
  num_blocks: int = 1,
  checksum: int = _CAPTURED_PLATE_CHECKSUM,
  strip_range: bool = False,
) -> bytes:
  """Bytes the instrument sends for a push-assay, set-geometry, read sequence.

  'V' -> ACK + status. '{' -> ACK + status, despite the manual documenting no
    status string for it. '%' -> ACK + status, when needed. 'S' -> ACK + status,
    then per wavelength a plate data block terminated by ^Z and followed by a raw
    checksum byte (page B-9), and a single ^Z end-of-data marker closing the whole
    response.
  """
  script = _ACK + _STATUS_OK  # SET ASSAY DEFINITION
  script += _ACK + _STATUS_OK  # SET PLATE GEOMETRY
  if strip_range:
    script += _ACK + _STATUS_OK  # SET STRIP RANGE
  script += _ACK + _STATUS_OK  # READ PLATE
  script += (_CAPTURED_PLATE + bytes([checksum])) * num_blocks
  script += b"\x1a"  # one end-of-data marker for the whole read
  return script


class TestReadAbsorbance(unittest.IsolatedAsyncioTestCase):
  """READ PLATE, page B-9, exercised against a captured plate data block."""

  async def test_single_wavelength_command_sequence(self):
    plate = _plate_resource()
    reader, fake = _reader(_read_script(), model="PowerWaveX")
    reader._wavelength_table = [550, 620, 665, 520, 450, 595]

    results = await reader.read_absorbance(plate, plate.get_all_items(), 620)

    # 'V' + table, then '{' + geometry, then 'S'. No strip range: the whole plate
    # is wanted. No temperature read: this model has no incubator.
    self.assertEqual(fake.writes[0], b"V")
    self.assertEqual(len(fake.writes[1]), 170)
    self.assertEqual(fake.writes[2], b"{")
    self.assertEqual(fake.writes[3], b"\x02")  # 2 = 96 wells, page B-12
    self.assertEqual(fake.writes[4], b"S")
    self.assertEqual(len(fake.writes), 5)
    self.assertEqual(fake.remaining, b"")
    self.assertEqual(fake.remaining, b"")

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].wavelength, 620)
    self.assertIsNone(results[0].temperature)

  async def test_parsed_values_match_the_captured_plate(self):
    plate = _plate_resource()
    reader, _ = _reader(_read_script(), model="PowerWaveX")
    reader._wavelength_table = [620]

    result = (await reader.read_absorbance(plate, plate.get_all_items(), 620))[0]

    self.assertEqual(len(result.data), 8)
    self.assertEqual(len(result.data[0]), 12)
    self.assertAlmostEqual(result.data[0][0], 0.201)  # A1
    self.assertAlmostEqual(result.data[7][11], 0.281)  # H12

  async def test_assay_table_carries_the_requested_wavelength(self):
    plate = _plate_resource()
    reader, fake = _reader(_read_script(), model="PowerWaveX")
    reader._wavelength_table = [630]

    await reader.read_absorbance(plate, plate.get_all_items(), 630)

    table = fake.writes[1]
    self.assertEqual(table[66:84], b"630000000000000000")  # bytes 67-84
    self.assertEqual(table[55], 0)  # byte 56: endpoint

  async def test_six_wavelengths_in_one_pass(self):
    plate = _plate_resource()
    wavelengths = [350, 550, 600, 630, 700, 990]
    reader, fake = _reader(_read_script(num_blocks=6), model="PowerWaveX")
    reader._wavelength_table = wavelengths

    results = await reader.read_absorbances(plate, plate.get_all_items(), wavelengths)

    self.assertEqual([r.wavelength for r in results], wavelengths)
    # One READ PLATE for all six blocks, not six reads.
    self.assertEqual(fake.writes.count(b"S"), 1)
    self.assertEqual(fake.remaining, b"")

  async def test_temperature_is_attached_when_an_incubator_is_present(self):
    plate = _plate_resource()
    script = _read_script() + _ACK + struct.pack("<H", 371) + _STATUS_OK
    reader, fake = _reader(script, model="PowerWaveX w/ Incubator")
    reader._wavelength_table = [620]

    result = (await reader.read_absorbance(plate, plate.get_all_items(), 620))[0]

    self.assertAlmostEqual(result.temperature, 37.1)
    self.assertIn(b"]", fake.writes)

  async def test_checksum_mismatch_raises(self):
    plate = _plate_resource()
    reader, _ = _reader(_read_script(checksum=_CAPTURED_PLATE_CHECKSUM + 1), model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveChecksumError):
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

  async def test_uncalibrated_wavelength_warns_but_reads(self):
    plate = _plate_resource()
    reader, _ = _reader(_read_script(), model="PowerWaveX")
    reader._wavelength_table = [550, 620]

    with self.assertLogs("pylabrobot.agilent.biotek.powerwave.powerwave", level="WARNING") as logs:
      results = await reader.read_absorbance(plate, plate.get_all_items(), 700)

    self.assertIn("not in the calibrated wavelength table", "\n".join(logs.output))
    self.assertEqual(results[0].wavelength, 700)

  async def test_out_of_range_wavelength_raises(self):
    plate = _plate_resource()
    reader, fake = _reader(model="PowerWaveX")
    reader._wavelength_table = [620]

    for bad in (199, 1000):
      with self.subTest(wavelength=bad):
        with self.assertRaises(ValueError):
          await reader.read_absorbance(plate, plate.get_all_items(), bad)
    self.assertEqual(fake.writes, [])

  async def test_no_wells_raises(self):
    plate = _plate_resource()
    reader, fake = _reader(model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(ValueError):
      await reader.read_absorbance(plate, [], 620)
    self.assertEqual(fake.writes, [])

  async def test_unsupported_geometry_raises_before_anything_is_sent(self):
    from pylabrobot.resources import cellvis_24_wellplate_3600uL_Fb

    plate = cellvis_24_wellplate_3600uL_Fb(name="plate24")
    reader, fake = _reader(model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(NotImplementedError) as ctx:
      await reader.read_absorbance(plate, plate.get_all_items(), 620)
    self.assertIn("8x12 and 16x24", str(ctx.exception))
    self.assertEqual(fake.writes, [])

  async def test_wells_from_another_plate_raises(self):
    plate = _plate_resource("a")
    other = _plate_resource("b")
    reader, _ = _reader(model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(ValueError):
      await reader.read_absorbance(plate, other.get_all_items(), 620)

  async def test_overrange_wells_warn(self):
    plate = _plate_resource()
    saturated = _CAPTURED_PLATE.replace(b",+0201,", b",*****,", 1)
    checksum = plate_checksum(saturated)
    # 'V', '{' and 'S', each ACK + status, then the block.
    script = (_ACK + _STATUS_OK) * 3 + saturated + bytes([checksum]) + b"\x1a"
    reader, _ = _reader(script, model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertLogs("pylabrobot.agilent.biotek.powerwave.powerwave", level="WARNING") as logs:
      result = (await reader.read_absorbance(plate, plate.get_all_items(), 620))[0]

    self.assertIn("1 of 96 wells overrange", "\n".join(logs.output))
    self.assertTrue(math.isinf(result.data[0][0]))

  async def test_read_status_error_propagates(self):
    plate = _plate_resource()
    # The assay definition is rejected: "error in assay, scan, or table definition".
    reader, _ = _reader(_ACK + b"\x1e090\x03", model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveStatusError):
      await reader.read_absorbance(plate, plate.get_all_items(), 620)


class TestAssayDefinitionPush(unittest.IsolatedAsyncioTestCase):
  """SET ASSAY DEFINITION over the wire, page B-10."""

  async def test_push_flushes_the_stray_nak(self):
    reader, fake = _reader(_ACK + _STATUS_OK, model="PowerWaveX")

    await reader._push_assay_definition([550])

    self.assertEqual(fake.writes[0], b"V")
    self.assertEqual(len(fake.writes[1]), 170)


class TestPlateGeometryAndStripRange(unittest.IsolatedAsyncioTestCase):
  """SET PLATE GEOMETRY and SET STRIP RANGE, pages B-12 and B-13."""

  async def test_geometry_96_well(self):
    reader, fake = _reader(_ACK + _STATUS_OK, model="PowerWaveX")
    await reader._set_plate_geometry(8, 12)
    self.assertEqual(fake.writes, [b"{", b"\x02"])
    self.assertEqual(fake.remaining, b"")

  async def test_geometry_384_well(self):
    reader, fake = _reader(_ACK + _STATUS_OK, model="PowerWaveX")
    await reader._set_plate_geometry(16, 24)
    self.assertEqual(fake.writes, [b"{", b"\x03"])

  async def test_geometry_consumes_a_status_string(self):
    """The manual documents no status string for '{', but the instrument sends one.

    Verified against a PowerWaveX w/ Incubator. Leaving it unread desynchronises
    every subsequent command.
    """
    reader, fake = _reader(_ACK + _STATUS_OK, model="PowerWaveX")
    await reader._set_plate_geometry(8, 12)
    self.assertEqual(fake.remaining, b"")

  async def test_geometry_rejection_raises(self):
    """A 96-well instrument answers 0A0 to a 384-well geometry request."""
    reader, _ = _reader(_ACK + b"\x1e0A0\x03", model="PowerWaveX")
    with self.assertRaises(PowerWaveStatusError) as ctx:
      await reader._set_plate_geometry(16, 24)
    self.assertEqual(ctx.exception.code, "0A0")

  async def test_reading_a_384_well_plate_on_a_96_well_model_raises(self):
    from pylabrobot.resources import Greiner_384_wellplate_28ul_Fb

    plate = Greiner_384_wellplate_28ul_Fb(name="plate")
    reader, fake = _reader(_ACK + _STATUS_OK + _ACK + b"\x1e0A0\x03", model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveStatusError) as ctx:
      await reader.read_absorbance(plate, plate.get_all_items(), 620)
    self.assertEqual(ctx.exception.code, "0A0")
    self.assertNotIn(b"S", fake.writes)  # refused before READ PLATE

  async def test_unsupported_geometry_raises(self):
    reader, fake = _reader(model="PowerWaveX")
    with self.assertRaises(NotImplementedError):
      await reader._set_plate_geometry(4, 6)
    self.assertEqual(fake.writes, [])

  async def test_strip_range(self):
    reader, fake = _reader(_ACK + _STATUS_OK, model="PowerWaveX")
    await reader._set_strip_range(3, 7)
    self.assertEqual(fake.writes, [b"%", b"\x03\x07"])
    self.assertEqual(fake.remaining, b"")

  async def test_strip_range_bounds(self):
    reader, _ = _reader(model="PowerWaveX")
    for first, last in ((0, 5), (1, 25), (7, 3)):
      with self.subTest(first=first, last=last):
        with self.assertRaises(ValueError):
          await reader._set_strip_range(first, last)


class TestStaleByteDrain(unittest.IsolatedAsyncioTestCase):
  """setup() discards what a crashed previous session left in flight."""

  async def test_stale_bytes_are_discarded_before_the_first_command(self):
    # A session killed part way through a plate read leaves the tail of that
    # response on the wire; it would otherwise be read as the reply to '}'.
    fake = _FakeSerialWithStaleBytes(
      stale=b"\x1a",
      script=_ACK + _CAPTURED_CONFIG + _ACK + _CAPTURED_TABLE + _STATUS_OK,
    )
    reader = PowerWaveX(name="test_reader", port="/dev/ttyUSB0", timeout=0.2)
    reader.io = fake  # type: ignore[assignment]
    reader._DRAIN_QUIET_SECONDS = 0.05  # type: ignore[misc]

    with self.assertLogs("pylabrobot.agilent.biotek.powerwave.powerwave", level="WARNING") as logs:
      await reader.setup()

    self.assertIn("discarded 1 stale bytes", "\n".join(logs.output))
    self.assertEqual(reader.model, "PowerWaveX w/ Incubator")
    self.assertEqual(fake.remaining, b"")

  async def test_a_quiet_line_is_not_waited_on_twice(self):
    reader, _ = _reader(_ACK + _CAPTURED_CONFIG + _ACK + _CAPTURED_TABLE + _STATUS_OK)
    await reader.setup()  # _DRAIN_QUIET_SECONDS is 0 in the fixture: flush only
    self.assertEqual(reader.model, "PowerWaveX w/ Incubator")


class TestAbortedRead(unittest.IsolatedAsyncioTestCase):
  """A read the instrument abandons part way through.

  It halts, shows the fault on its front panel, and stops answering the serial
  port until an operator acknowledges it there, at which point it sends DLE - the
  byte that completes a HALT - where the next block's CR start code belongs.
  Verified on a PowerWaveX w/ Incubator (firmware R06.20) reading an uncalibrated
  205 nm: status 000, then 99 s of silence, then DLE at the instant the panel key
  was pressed, with 0F11 on the display and 080 latched until a self-test ran.

  So both shapes below are the same fault: DLE if someone was at the instrument
  while the read was outstanding, silence if not.
  """

  @staticmethod
  def _script_until_read(blocks=b""):
    # 'V', '{' then 'S', each ACK + status, followed by whatever the read sends.
    return (_ACK + _STATUS_OK) * 3 + blocks

  async def test_abort_with_an_elx_status_reports_the_decoded_fault(self):
    plate = _plate_resource()
    # 0F11: four hex characters + ETX, no RS, which is the ELx status format.
    reader, _ = _reader(self._script_until_read(b"\x10" + b"0F11\x03"), model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveReadAbortedError) as caught:
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

    message = str(caught.exception)
    self.assertIn("abandoned the read", message)
    self.assertIn("signal out of range", message)  # describe_elx_status("0F11")
    self.assertIn("front panel", message)
    self.assertEqual(caught.exception.code, "0F11")

  async def test_abort_with_an_rs_status_reports_the_code(self):
    plate = _plate_resource()
    reader, _ = _reader(self._script_until_read(b"\x10" + b"\x1e080\x03"), model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveReadAbortedError) as caught:
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

    self.assertEqual(caught.exception.code, "080")
    self.assertIn("perform self-test", str(caught.exception))

  async def test_abort_with_no_status_still_explains_itself(self):
    plate = _plate_resource()
    reader, _ = _reader(self._script_until_read(b"\x10"), model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveReadAbortedError) as caught:
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

    self.assertIsNone(caught.exception.code)
    self.assertIn("self_test()", str(caught.exception))

  async def test_abort_part_way_through_counts_the_blocks_received(self):
    plate = _plate_resource()
    good = _CAPTURED_PLATE + bytes([_CAPTURED_PLATE_CHECKSUM])
    reader, _ = _reader(self._script_until_read(good + b"\x10" + b"0F11\x03"), model="PowerWaveX")
    reader._wavelength_table = [600, 630, 660]

    with self.assertRaises(PowerWaveReadAbortedError) as caught:
      await reader.read_absorbances(plate, plate.get_all_items(), [600, 630, 660])

    self.assertIn("after 1 of 3 data blocks", str(caught.exception))

  async def test_scan_abort_is_reported_the_same_way(self):
    plate = _plate_resource()
    # geometry, '&', the request's second ACK, status, both wavelength lists, then DLE
    script = _ACK + _STATUS_OK + _ACK + _ACK + _STATUS_OK + b"\x1a" + b"\x1a"
    script += b"\x10" + b"0F11\x03"
    reader, _ = _reader(script, model="PowerWaveX")

    with self.assertRaises(PowerWaveReadAbortedError) as caught:
      await reader.scan_plate(
        plate, start_wavelength=400, stop_wavelength=420, step=10, well=plate.get_item(0)
      )

    self.assertIn("signal out of range", str(caught.exception))

  async def test_total_silence_is_reported_as_an_abandoned_read(self):
    # A fault can abandon the read without sending DLE first, and can take the
    # serial interface down with it, so nothing arrives at all. Verified on a
    # PowerWaveX w/ Incubator asked to read at 200 nm.
    plate = _plate_resource()
    reader, _ = _reader(self._script_until_read(), model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertRaises(PowerWaveReadAbortedError) as caught:
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

    message = str(caught.exception)
    self.assertIn("sent nothing", message)
    # Recovery is a panel keypress then a self-test, never a power cycle.
    self.assertIn("front panel", message)
    self.assertIn("self_test()", message)
    self.assertIn("No power cycle is needed", message)
    self.assertIsNone(caught.exception.code)

  async def test_a_silent_scan_is_reported_too(self):
    plate = _plate_resource()
    # geometry, '&', the second ACK, status, both wavelength lists, then silence
    script = _ACK + _STATUS_OK + _ACK + _ACK + _STATUS_OK + b"\x1a" + b"\x1a"
    reader, _ = _reader(script, model="PowerWaveX")
    reader._SCAN_SECONDS_PER_WAVELENGTH = 0  # no real monochromator to wait for

    with self.assertRaises(PowerWaveReadAbortedError) as caught:
      await reader.scan_plate(
        plate, start_wavelength=400, stop_wavelength=420, step=10, well=plate.get_item(0)
      )

    self.assertIn("No scan data arrived", str(caught.exception))

  async def test_a_normal_read_is_untouched(self):
    plate = _plate_resource()
    reader, fake = _reader(_read_script(), model="PowerWaveX")
    reader._wavelength_table = [620]

    result = (await reader.read_absorbance(plate, plate.get_all_items(), 620))[0]

    self.assertEqual(len(result.data), 8)
    self.assertEqual(fake.remaining, b"")


class TestEndOfDataMarker(unittest.IsolatedAsyncioTestCase):
  """The ^Z that closes a whole READ PLATE response.

  Undocumented, and verified on a PowerWaveX w/ Incubator (firmware R06.20): a
  one-wavelength endpoint read ends ``^Z <checksum> ^Z``, and a three-cycle
  kinetic read sends three blocks and a single trailing ^Z. Leaving it queued made
  it the reply to the next command.
  """

  async def test_two_reads_in_one_session(self):
    plate = _plate_resource()
    reader, fake = _reader(_read_script() * 2, model="PowerWaveX")
    reader._wavelength_table = [620]

    for _ in range(2):
      result = (await reader.read_absorbance(plate, plate.get_all_items(), 620))[0]
      self.assertEqual(len(result.data), 8)

    self.assertEqual(fake.remaining, b"")

  async def test_marker_is_consumed_not_left_queued(self):
    plate = _plate_resource()
    reader, fake = _reader(_read_script(), model="PowerWaveX")
    reader._wavelength_table = [620]

    await reader.read_absorbance(plate, plate.get_all_items(), 620)

    self.assertEqual(fake.remaining, b"")

  async def test_missing_marker_only_warns(self):
    plate = _plate_resource()
    self.assertEqual(_read_script()[-1:], b"\x1a")
    reader, _ = _reader(_read_script()[:-1], model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertLogs("pylabrobot.agilent.biotek.powerwave.powerwave", level="WARNING") as logs:
      result = (await reader.read_absorbance(plate, plate.get_all_items(), 620))[0]

    self.assertIn("no ^Z end-of-data marker", "\n".join(logs.output))
    self.assertEqual(len(result.data), 8)  # the data itself still parses

  async def test_unexpected_trailing_byte_warns(self):
    plate = _plate_resource()
    reader, _ = _reader(_read_script()[:-1] + b"Z", model="PowerWaveX")
    reader._wavelength_table = [620]

    with self.assertLogs("pylabrobot.agilent.biotek.powerwave.powerwave", level="WARNING") as logs:
      await reader.read_absorbance(plate, plate.get_all_items(), 620)

    self.assertIn("expected a ^Z end-of-data marker", "\n".join(logs.output))


class TestReadWithStripRange(unittest.IsolatedAsyncioTestCase):
  """Reading a subset of wells, which the strip range makes possible."""

  def _two_column_block(self, num_rows: int = 8, num_columns: int = 2) -> bytes:
    """A synthetic plate block sized to a strip range, +0.100 in every well."""
    rows = b"".join(
      b"".join(b",+0100" for _ in range(num_columns)) + b"\r\n" for _ in range(num_rows)
    )
    return b"\r" + rows + b"\x1a"

  async def test_column_subset_sends_strip_range(self):
    plate = _plate_resource()
    block = self._two_column_block()
    script = (_ACK + _STATUS_OK) * 4  # 'V', '{', '%', 'S'
    script += block + bytes([plate_checksum(block)]) + b"\x1a"
    reader, fake = _reader(script, model="PowerWaveX")
    reader._wavelength_table = [620]

    # Columns 3 and 4, every row: exactly what a strip range can express.
    wells = [w for w in plate.get_all_items() if w.get_column() in (2, 3)]
    self.assertEqual(len(wells), 16)

    results = await reader.read_absorbance(plate, wells, 620)

    self.assertEqual(fake.writes[2], b"{")
    self.assertEqual(fake.writes[4], b"%")
    self.assertEqual(fake.writes[5], b"\x03\x04")  # strips 3-4, 1-based
    self.assertEqual(fake.writes[6], b"S")
    self.assertEqual(fake.remaining, b"")

    data = results[0].data
    # Only the read columns carry values; the rest were never measured.
    self.assertEqual(data[0][2], 0.100)
    self.assertEqual(data[7][3], 0.100)
    self.assertIsNone(data[0][0])
    self.assertIsNone(data[0][11])

  async def test_full_plate_sends_no_strip_range(self):
    plate = _plate_resource()
    reader, fake = _reader(_read_script(), model="PowerWaveX")
    reader._wavelength_table = [620]

    await reader.read_absorbance(plate, plate.get_all_items(), 620)

    self.assertNotIn(b"%", fake.writes)

  async def test_partial_column_selection_is_widened_to_whole_strips(self):
    """The instrument cannot select rows, so a ragged selection reads whole columns."""
    plate = _plate_resource()
    block = self._two_column_block()
    script = (_ACK + _STATUS_OK) * 4  # 'V', '{', '%', 'S'
    script += block + bytes([plate_checksum(block)]) + b"\x1a"
    reader, fake = _reader(script, model="PowerWaveX")
    reader._wavelength_table = [620]

    # A1, A2 and B1 only: three wells spanning two columns.
    wells = [plate.get_item(0), plate.get_item(1), plate.get_item(12)]

    with self.assertLogs("pylabrobot.agilent.biotek.powerwave.powerwave", level="INFO") as logs:
      results = await reader.read_absorbance(plate, wells, 620)

    self.assertIn("not a whole block of columns", "\n".join(logs.output))
    self.assertEqual(fake.writes[5], b"\x01\x02")  # widened to strips 1-2
    # The extra wells were measured, so they are reported rather than discarded.
    self.assertEqual(results[0].data[7][0], 0.100)


class TestSelfTest(unittest.IsolatedAsyncioTestCase):
  """SELF-TEST, page B-16. Fixtures are captured from a real instrument."""

  # Captured: the tail of a failing self-test report, followed by its status string.
  _FAILING_TAIL = (
    b"Lambda: 550  Gain:  0.00  Resets: 0\r\n"
    b"Air:            0      0      0      0      0      0      0      0      0\r\n"
    b"\r\n*** SYSTEM TEST FAIL *** ERROR (0201) DETECTED   \x1e000\x03"
  )
  # A real report ends with the pass banner; the earlier fixture left it out,
  # which is what let a verdict-less report count as healthy.
  _PASSING_TAIL = (
    b"Lambda: 550  Gain:  1.23  Resets: 0\r\nNoise Max:  4\r\n"
    b"\r\nSYSTEM TEST PASS        \x1e000\x03"
  )

  async def test_failing_self_test_raises_with_the_decoded_code(self):
    reader, fake = _reader(_ACK + self._FAILING_TAIL, model="PowerWaveX w/ Incubator")

    with self.assertRaises(PowerWaveSelfTestError) as ctx:
      await reader.self_test()

    self.assertEqual(fake.writes, [b"*"])
    self.assertEqual(ctx.exception.code, "0201")
    self.assertEqual(
      ctx.exception.description, "Order sorting filter wheel did not find the home sensor"
    )
    self.assertIn("Lambda: 550", ctx.exception.report)

  async def test_failure_can_be_returned_instead_of_raised(self):
    reader, _ = _reader(_ACK + self._FAILING_TAIL, model="PowerWaveX")
    report = await reader.self_test(raise_on_failure=False)
    self.assertIn("SYSTEM TEST FAIL", report)

  async def test_passing_self_test_returns_the_report(self):
    reader, _ = _reader(_ACK + self._PASSING_TAIL, model="PowerWaveX")
    report = await reader.self_test()
    self.assertIn("Gain:  1.23", report)
    self.assertNotIn("\x1e", report)  # the status string is not part of the report

  async def test_status_string_alone_does_not_indicate_success(self):
    """A failing instrument still answers status "000"; only the body says FAIL."""
    self.assertEqual(parse_status(self._FAILING_TAIL[-STATUS_LENGTH:]), "000")


class TestSelfTestReportParsing(unittest.TestCase):
  """Pure parsing of self-test report bodies."""

  def test_failure_with_code(self):
    self.assertEqual(
      parse_self_test_report("*** SYSTEM TEST FAIL *** ERROR (0201) DETECTED"), "0201"
    )

  def test_failure_without_a_code(self):
    self.assertEqual(parse_self_test_report("*** SYSTEM TEST FAIL ***"), "")

  def test_pass_returns_none(self):
    self.assertIsNone(
      parse_self_test_report("Lambda: 550  Gain: 1.20\nNoise Max: 3\nSYSTEM TEST PASS   ")
    )

  def test_error_text_without_a_fail_banner_is_not_a_failure(self):
    # The code is only a verdict when the FAIL banner declares one.
    self.assertIsNone(parse_self_test_report("see ERROR (0201) in the manual\nSYSTEM TEST PASS"))

  def test_a_report_with_no_verdict_is_refused(self):
    # Absence of a FAIL banner used to mean "passed", so an empty or truncated
    # report looked like a healthy instrument - and the status string beside it
    # says "000" either way, so nothing else would have caught it.
    for label, report in (
      ("empty", ""),
      ("whitespace", "   \r\n  "),
      ("truncated mid-report", "Lambda: 550  Gain:  1.23  Resets: 0\r\nNoise Max:"),
      ("unrelated text", "garbage"),
    ):
      with self.subTest(report=label):
        with self.assertRaises(PowerWaveParseError):
          parse_self_test_report(report)

  def test_d2_error_codes(self):
    self.assertEqual(
      SELF_TEST_ERRORS["0201"], "Order sorting filter wheel did not find the home sensor"
    )
    # Rev C1 calls 0202 the monochromator; D2, which applies to the PowerWaveX,
    # calls it the robot lid lift and has no 0203 at all.
    self.assertEqual(SELF_TEST_ERRORS["0202"], "Robot lid lift did not find the home sensor")
    self.assertNotIn("0203", SELF_TEST_ERRORS)


class TestGeometryAndStripEncoding(unittest.TestCase):
  """Pure encoders for pages B-12 and B-13."""

  def test_geometry_codes(self):
    self.assertEqual(encode_plate_geometry(8, 12), b"\x02")
    self.assertEqual(encode_plate_geometry(16, 24), b"\x03")

  def test_unsupported_geometry(self):
    for rows, columns in ((4, 6), (8, 13), (32, 48)):
      with self.subTest(rows=rows, columns=columns):
        with self.assertRaises(NotImplementedError):
          encode_plate_geometry(rows, columns)

  def test_strip_range_encoding(self):
    self.assertEqual(encode_strip_range(1, 12), b"\x01\x0c")
    self.assertEqual(encode_strip_range(24, 24), b"\x18\x18")


class TestKineticRead(unittest.IsolatedAsyncioTestCase):
  """Kinetic reads, pages B-10 and B-13."""

  def _kinetic_script(self, cycles: int, wavelengths: int, minimum: int = 60) -> bytes:
    # 'V', '{' then '$' (ACK + 2 bytes + status), then 'S', then the blocks.
    script = (_ACK + _STATUS_OK) * 2
    script += _ACK + struct.pack("<H", minimum) + _STATUS_OK
    script += _ACK + _STATUS_OK
    script += (_CAPTURED_PLATE + bytes([_CAPTURED_PLATE_CHECKSUM])) * (cycles * wavelengths)
    script += b"\x1a"  # one end-of-data marker closes the whole run, not each cycle
    return script

  async def test_command_sequence_and_result_count(self):
    plate = _plate_resource()
    reader, fake = _reader(self._kinetic_script(cycles=3, wavelengths=2), model="PowerWaveX")
    reader._wavelength_table = [600, 630]

    results = await reader.read_absorbance_kinetic(
      plate, plate.get_all_items(), [600, 630], interval=60, count=3
    )

    self.assertEqual(fake.writes[0], b"V")
    self.assertEqual(fake.writes[2], b"{")
    self.assertEqual(fake.writes[4], b"$")  # minimum kinetic interval
    self.assertEqual(fake.writes[5], b"S")
    self.assertEqual(fake.remaining, b"")

    # One result per wavelength per cycle, ordered by cycle then wavelength.
    self.assertEqual(len(results), 6)
    self.assertEqual([r.wavelength for r in results], [600, 630, 600, 630, 600, 630])

  async def test_total_read_time_is_rejected(self):
    # Accepting it would promise a count-driven run the instrument will not give:
    # with the total-time bit set it keeps cycling past the blocks we wait for.
    plate = _plate_resource()
    reader, fake = _reader(model="PowerWaveX")
    reader._wavelength_table = [600]

    with self.assertRaises(NotImplementedError):
      await reader.read_absorbance_kinetic(
        plate, plate.get_all_items(), [600], interval=60, count=2, total_read_time=9999
      )

    self.assertEqual(fake.writes, [])  # nothing was sent

  async def test_the_default_run_does_not_shake(self):
    # Shaking a plate between cycles is a decision about the assay, and used to
    # happen on every kinetic read whether or not anyone asked.
    plate = _plate_resource()
    reader, fake = _reader(self._kinetic_script(cycles=2, wavelengths=1), model="PowerWaveX")
    reader._wavelength_table = [600]

    await reader.read_absorbance_kinetic(plate, plate.get_all_items(), [600], interval=60, count=2)

    table = fake.writes[1]
    self.assertEqual(table[30], 0)  # byte 31: no shake bits
    self.assertEqual(table[162:164], b"\x00\x00")  # bytes 163-164: no shake time

  async def test_unknown_shake_speed_is_refused_before_sending(self):
    plate = _plate_resource()
    reader, fake = _reader(b"", model="PowerWaveX")
    reader._wavelength_table = [600]

    with self.assertRaises(ValueError):
      await reader.read_absorbance_kinetic(
        plate,
        plate.get_all_items(),
        [600],
        interval=60,
        count=2,
        shake_time=5,
        shake_speed="turbo",  # type: ignore[arg-type]
      )

    self.assertEqual(fake.writes, [])

  async def test_shaking_is_sent_when_asked_for(self):
    plate = _plate_resource()
    reader, fake = _reader(self._kinetic_script(cycles=2, wavelengths=1), model="PowerWaveX")
    reader._wavelength_table = [600]

    await reader.read_absorbance_kinetic(
      plate,
      plate.get_all_items(),
      [600],
      interval=60,
      count=2,
      shake_time=5,
      shake_speed="fast",
    )

    table = fake.writes[1]
    self.assertEqual(table[30], KINETIC_SHAKE_FLAGS)
    self.assertEqual(table[162:164], struct.pack("<H", 5))
    self.assertEqual(table[164], 2)

  async def test_assay_table_does_not_set_the_total_time_bit(self):
    plate = _plate_resource()
    reader, fake = _reader(self._kinetic_script(cycles=2, wavelengths=1), model="PowerWaveX")
    reader._wavelength_table = [600]

    await reader.read_absorbance_kinetic(plate, plate.get_all_items(), [600], interval=60, count=2)

    self.assertEqual(fake.writes[1][30] & FLAG_USE_TOTAL_KINETIC_TIME, 0)
    self.assertEqual(fake.remaining, b"")

  async def test_assay_table_is_kinetic(self):
    plate = _plate_resource()
    reader, fake = _reader(self._kinetic_script(cycles=2, wavelengths=1), model="PowerWaveX")
    reader._wavelength_table = [600]

    await reader.read_absorbance_kinetic(
      plate, plate.get_all_items(), [600], interval=60, count=2, shake_speed="fast"
    )

    table = fake.writes[1]
    self.assertEqual(table[55], 2)  # byte 56: read type kinetic
    self.assertEqual(table[30], KINETIC_FLAGS)  # byte 31: shake and timing flags
    self.assertEqual(table[59:61], struct.pack("<H", 60))  # bytes 60-61: interval
    self.assertEqual(table[61:63], struct.pack("<H", 2))  # bytes 62-63: read count
    self.assertEqual(table[164], 2)  # byte 165: shake speed

  async def test_cycles_are_timestamped_in_order(self):
    plate = _plate_resource()
    reader, _ = _reader(self._kinetic_script(cycles=3, wavelengths=1), model="PowerWaveX")
    reader._wavelength_table = [600]

    results = await reader.read_absorbance_kinetic(
      plate, plate.get_all_items(), [600], interval=60, count=3
    )

    stamps = [r.timestamp for r in results]
    self.assertEqual(stamps, sorted(stamps))

  async def test_interval_below_the_instrument_minimum_raises(self):
    # The instrument refuses such a series with status 090 rather than running it
    # late, so the driver stops before sending READ PLATE.
    plate = _plate_resource()
    reader, fake = _reader(
      self._kinetic_script(cycles=2, wavelengths=1, minimum=60), model="PowerWaveX"
    )
    reader._wavelength_table = [600]

    with self.assertRaises(ValueError) as caught:
      await reader.read_absorbance_kinetic(
        plate, plate.get_all_items(), [600], interval=20, count=2
      )

    self.assertIn("minimum of 60s", str(caught.exception))
    self.assertNotIn(b"S", fake.writes)  # READ PLATE was never sent

  async def test_interval_at_the_instrument_minimum_is_accepted(self):
    plate = _plate_resource()
    reader, _ = _reader(
      self._kinetic_script(cycles=2, wavelengths=1, minimum=20), model="PowerWaveX"
    )
    reader._wavelength_table = [600]

    results = await reader.read_absorbance_kinetic(
      plate, plate.get_all_items(), [600], interval=20, count=2
    )

    self.assertEqual(len(results), 2)

  async def test_read_count_bounds(self):
    plate = _plate_resource()
    reader, fake = _reader(model="PowerWaveX")
    reader._wavelength_table = [600]

    for count in (1, 10000):
      with self.subTest(count=count):
        with self.assertRaises(ValueError):
          await reader.read_absorbance_kinetic(
            plate, plate.get_all_items(), [600], interval=60, count=count
          )
    self.assertEqual(fake.writes, [])

  async def test_kinetic_honours_a_strip_range(self):
    plate = _plate_resource()
    rows = b"".join(b",+0100" * 2 + b"\r\n" for _ in range(8))
    block = b"\r" + rows + b"\x1a"
    script = (_ACK + _STATUS_OK) * 2  # 'V', '{'
    script += _ACK + _STATUS_OK  # '%'
    script += _ACK + struct.pack("<H", 60) + _STATUS_OK  # '$'
    script += _ACK + _STATUS_OK  # 'S'
    script += (block + bytes([plate_checksum(block)])) * 2
    script += b"\x1a"  # end-of-data marker
    reader, fake = _reader(script, model="PowerWaveX")
    reader._wavelength_table = [600]

    wells = [w for w in plate.get_all_items() if w.get_column() in (0, 1)]
    results = await reader.read_absorbance_kinetic(plate, wells, [600], interval=60, count=2)

    self.assertIn(b"%", fake.writes)
    self.assertEqual(len(results), 2)
    self.assertEqual(results[0].data[0][0], 0.100)
    self.assertIsNone(results[0].data[0][11])
    self.assertEqual(fake.remaining, b"")


class TestElxStatusCodes(unittest.TestCase):
  """The second status format and its error tables, page B-21 onward."""

  def test_format_detection(self):
    self.assertTrue(is_elx_status(b"0000\x03"))
    self.assertTrue(is_elx_status(b"A100\x03"))
    self.assertFalse(is_elx_status(b"\x1e000\x03"))  # the RS format
    self.assertFalse(is_elx_status(b"00000\x03"))  # too long
    self.assertFalse(is_elx_status(b"ZZZZ\x03"))  # not hexadecimal

  def test_parse_status_accepts_both_formats(self):
    self.assertEqual(parse_status(b"\x1e000\x03"), "000")
    self.assertEqual(parse_status(b"0000\x03"), "0000")

  def test_non_fatal_error_raises_status_error(self):
    with self.assertRaises(PowerWaveStatusError) as ctx:
      parse_status(b"2301\x03")
    self.assertEqual(ctx.exception.code, "2301")
    self.assertIn("Lamp control failure", ctx.exception.message)

  def test_fatal_error_raises_fatal_error(self):
    with self.assertRaises(PowerWaveFatalError) as ctx:
      parse_status(b"A100\x03")
    self.assertEqual(ctx.exception.code, "A100")
    self.assertIn("Power-cycle", str(ctx.exception))

  def test_fatal_errors_are_not_mistaken_for_ordinary_ones(self):
    for code in ELX_FATAL_ERRORS:
      with self.subTest(code=code):
        with self.assertRaises(PowerWaveFatalError):
          parse_status(code.encode() + b"\x03")

  def test_table_sizes_match_the_manual(self):
    self.assertEqual(len(ELX_FATAL_ERRORS), 10)
    self.assertEqual(len(ELX_ERRORS), 42)

  def test_templated_motor_digit_expands(self):
    # <motor> replaces the last digit; 0 is the carrier, not "absent".
    self.assertEqual(describe_elx_status("0200"), "Plate carrier did not find the home sensor")
    self.assertEqual(
      describe_elx_status("0203"), "Monochromator did not find opto-sensor transition"
    )
    self.assertEqual(describe_elx_status("2A01"), "Filter wheel did not find middle sensor")

  def test_expansion_agrees_with_the_codes_d2_spells_out(self):
    """Every ERROR nnnn in D2's self-test appendix must also decode as an ELx code.

    The self-test table is the expanded form of these templates, so a code that
    decoded to something unrelated would mean the substitution rules are wrong.
    """
    for code in SELF_TEST_ERRORS:
      with self.subTest(code=code):
        self.assertIsNotNone(describe_elx_status(code))

  def test_test_type_digit_zero_is_dropped(self):
    # <test type> has no '0' meaning, unlike <motor>, so the placeholder goes.
    self.assertEqual(describe_elx_status("2300"), "Lamp control failure")
    self.assertEqual(describe_elx_status("2301"), "Lamp control failure 5V power failed")

  def test_incubator_zones_are_a_bit_field(self):
    self.assertEqual(describe_elx_status("1501"), "Incubator failure: range error, zone(s) 1")
    self.assertEqual(describe_elx_status("1512"), "Incubator failure: thermistor error, zone(s) 2")
    self.assertEqual(
      describe_elx_status("150F"), "Incubator failure: range error, zone(s) 1, 2, 3, 4"
    )

  def test_ad_device_digit(self):
    self.assertEqual(
      describe_elx_status("AA01"),
      "Absorbance measurement A/D converter never saw ready transition",
    )

  def test_unknown_code_returns_none(self):
    self.assertIsNone(describe_elx_status("9999"))

  def test_unknown_code_still_raises(self):
    with self.assertRaises(PowerWaveStatusError) as ctx:
      parse_status(b"9999\x03")
    self.assertIn("Unknown", ctx.exception.message)

  def test_malformed_elx_rejected(self):
    with self.assertRaises(PowerWaveParseError):
      parse_elx_status(b"\x1e000\x03")


class TestUnimplementedCommands(unittest.IsolatedAsyncioTestCase):
  """The three Appendix B commands that are declared but deliberately not sent."""

  async def test_each_raises_not_implemented_without_touching_the_port(self):
    cases: List[Tuple[str, Callable[[PowerWaveX], Awaitable[object]], str]] = [
      ("_configure_wavelengths", lambda reader: reader._configure_wavelengths([550]), "'M'"),
      ("_request_plate_barcode", lambda reader: reader._request_plate_barcode(), "'s'"),
      ("_close_plate_access_door", lambda reader: reader._close_plate_access_door(), "')'"),
    ]
    for name, call, command in cases:
      with self.subTest(method=name):
        reader, fake = _reader(model="PowerWaveX")
        with self.assertRaises(NotImplementedError) as ctx:
          await call(reader)
        self.assertIn(command, str(ctx.exception))
        self.assertEqual(fake.writes, [])

  async def test_door_message_names_the_connected_model(self):
    reader, _ = _reader(model="PowerWaveX w/ Incubator")
    with self.assertRaises(NotImplementedError) as ctx:
      await reader._close_plate_access_door()
    self.assertIn("PowerWaveX w/ Incubator", str(ctx.exception))

  def test_every_documented_command_is_either_used_or_declared(self):
    """No Appendix B command may be silently missing from the driver."""
    import inspect

    from pylabrobot.agilent.biotek.powerwave import powerwave as module

    source = inspect.getsource(module)
    declared = {
      "CONFIGURE_WAVELENGTHS": "_configure_wavelengths",
      "GET_PLATE_BARCODE": "_request_plate_barcode",
      "CLOSE_PLATE_ACCESS_DOOR": "_close_plate_access_door",
    }
    for command in Command:
      with self.subTest(command=command.name):
        used = f"Command.{command.name}" in source
        stubbed = declared.get(command.name, "") in source and command.name in declared
        self.assertTrue(used or stubbed, f"{command.name} is neither sent nor declared")


class TestScanRequestEncoding(unittest.TestCase):
  """The 18-byte SCAN PLATE request, page B-14."""

  def test_layout(self):
    request = encode_scan_request(
      row=3, column=7, start_wavelength=400, stop_wavelength=600, step=10
    )
    self.assertEqual(len(request), 18)
    self.assertEqual(request, b"0307400600010" + b"0" + b"0" + b"00" + b"0")
    self.assertEqual(request[0:2], b"03")  # row
    self.assertEqual(request[2:4], b"07")  # column
    self.assertEqual(request[4:7], b"400")  # start
    self.assertEqual(request[7:10], b"600")  # stop
    self.assertEqual(request[10:13], b"010")  # step

  def test_whole_strip_row_is_zero(self):
    request = encode_scan_request(
      row=SCAN_WHOLE_STRIP, column=1, start_wavelength=300, stop_wavelength=800, step=5
    )
    self.assertEqual(request[0:2], b"00")

  def test_series_and_calibrate_and_shake(self):
    request = encode_scan_request(
      row=1,
      column=1,
      start_wavelength=400,
      stop_wavelength=500,
      step=50,
      series="last",
      calibrate=True,
      shake_time=15,
      shake_speed=2,
    )
    self.assertEqual(request[13:14], b"3")  # series: last
    self.assertEqual(request[14:15], b"1")  # calibrate
    self.assertEqual(request[15:17], b"15")  # shake time
    self.assertEqual(request[17:18], b"2")  # shake speed

  def test_at_least_two_wavelengths(self):
    with self.assertRaises(ValueError):
      encode_scan_request(row=1, column=1, start_wavelength=500, stop_wavelength=500, step=1)
    with self.assertRaises(ValueError):
      encode_scan_request(row=1, column=1, start_wavelength=600, stop_wavelength=500, step=1)
    # step larger than the span would yield a single wavelength
    with self.assertRaises(ValueError):
      encode_scan_request(row=1, column=1, start_wavelength=500, stop_wavelength=510, step=20)

  def test_bounds(self):
    base = dict(row=1, column=1, start_wavelength=400, stop_wavelength=600, step=10)
    for field, bad in [
      ("row", 17),
      ("column", 0),
      ("column", 25),
      ("start_wavelength", 199),
      ("stop_wavelength", 1000),
      ("step", 0),
      ("shake_time", 100),
      ("shake_speed", 4),
    ]:
      with self.subTest(field=field, value=bad):
        with self.assertRaises(ValueError):
          encode_scan_request(**{**base, field: bad})  # type: ignore[arg-type]

  def test_unknown_series(self):
    with self.assertRaises(ValueError):
      encode_scan_request(
        row=1, column=1, start_wavelength=400, stop_wavelength=600, step=10, series="every"
      )

  def test_scan_wavelengths(self):
    self.assertEqual(scan_wavelengths(400, 450, 10), [400, 410, 420, 430, 440, 450])
    # the stop is included only when the step lands on it
    self.assertEqual(scan_wavelengths(400, 425, 10), [400, 410, 420])


class TestOrderSortingFilterBands(unittest.TestCase):
  """The recorded order-sorting filter bands, page-less and unverified.

  Nothing selects these, so the only thing worth testing is that the table itself
  is coherent and still says what the far-UV findings rely on.
  """

  def test_bands_are_contiguous_and_cover_the_whole_range(self):
    edges = sorted(ORDER_SORTING_FILTER_BANDS)
    self.assertEqual(edges[0][0], MIN_WAVELENGTH)
    self.assertEqual(edges[-1][1], MAX_WAVELENGTH)
    for (_, upper), (lower, _) in zip(edges, edges[1:]):
      self.assertEqual(lower, upper + 1)  # no gap, no overlap

  def test_the_far_uv_probes_share_one_band(self):
    # The claim abs_wavelength_range makes: the wheel cannot explain why 205 nm
    # abandons a read while 215 nm does not.
    bands = {
      next(band for band in ORDER_SORTING_FILTER_BANDS if band[0] <= wl <= band[1])
      for wl in (200, 205, 215)
    }
    self.assertEqual(len(bands), 1)
    self.assertEqual(ORDER_SORTING_FILTER_BANDS[bands.pop()], "open (no filter)")


class TestScanResponseParsing(unittest.TestCase):
  """Scan response sections, page B-14."""

  def test_wavelength_list(self):
    self.assertEqual(parse_scan_wavelength_list(b"400\r410\r420\r\x1a"), [400, 410, 420])

  def test_empty_wavelength_list(self):
    # "If no calibration is performed at all, the <^Z> terminator is returned alone"
    self.assertEqual(parse_scan_wavelength_list(b"\x1a"), [])

  def test_non_numeric_wavelength_list(self):
    with self.assertRaises(PowerWaveParseError):
      parse_scan_wavelength_list(b"4X0\r\x1a")

  #: A single-well 400-440 nm scan in 10 nm steps, captured verbatim from a
  #: PowerWaveX w/ Incubator (firmware R06.20) with an empty carrier. The whole
  #: series carries one ^Z and one checksum byte, not one per wavelength.
  CAPTURED_SINGLE_WELL = b"\r400,+0000\r\n410,+0000\r\n420,+0000\r\n430,+0000\r\n440,+0000\r\n\x1a"

  #: The same instrument's whole-strip scan of column 1, 400-420 nm by 10 nm.
  CAPTURED_STRIP = (
    b"\r400,+0000,+0000,+0000,+0000,+0000,+0000,+0000,+0000\r\n"
    b"410,+0000,+0000,+0000,+0000,+0000,+0000,+0000,+0000\r\n"
    b"420,+0000,+0000,+0000,+0000,+0000,+0000,+0000,+0000\r\n\x1a"
  )

  def test_captured_single_well_scan(self):
    wavelengths, data = parse_scan_data(self.CAPTURED_SINGLE_WELL, num_wells=1)
    self.assertEqual(wavelengths, [400, 410, 420, 430, 440])
    self.assertEqual(data, [[0.0]] * 5)
    self.assertEqual(scan_checksum(self.CAPTURED_SINGLE_WELL), 238)  # observed byte

  def test_captured_strip_scan(self):
    wavelengths, data = parse_scan_data(self.CAPTURED_STRIP, num_wells=8)
    self.assertEqual(wavelengths, [400, 410, 420])
    self.assertEqual(data, [[0.0] * 8] * 3)
    self.assertEqual(scan_checksum(self.CAPTURED_STRIP), 70)  # observed byte

  def test_single_well_section(self):
    wavelengths, data = parse_scan_data(b"550,+0201\r\n\x1a", num_wells=1)
    self.assertEqual(wavelengths, [550])
    self.assertEqual(data, [[0.201]])

  def test_strip_section(self):
    section = b"550" + b"".join(b",+0%03d" % (100 + i) for i in range(8)) + b"\r\n\x1a"
    wavelengths, data = parse_scan_data(section, num_wells=8)
    self.assertEqual(wavelengths, [550])
    self.assertEqual(len(data[0]), 8)
    self.assertAlmostEqual(data[0][0], 0.100)
    self.assertAlmostEqual(data[0][7], 0.107)

  def test_section_tolerates_a_leading_cr(self):
    self.assertEqual(
      parse_scan_data(b"\r550,+0201\r\n\x1a", 1), parse_scan_data(b"550,+0201\r\n\x1a", 1)
    )

  def test_negative_and_overrange(self):
    _, data = parse_scan_data(b"550,-0201\r\n\x1a", 1)
    self.assertEqual(data, [[-0.201]])
    _, data = parse_scan_data(b"550,*****\r\n\x1a", 1)
    self.assertTrue(math.isinf(data[0][0]))

  def test_wrong_reading_count(self):
    with self.assertRaises(PowerWaveParseError):
      parse_scan_data(b"550,+0201\r\n\x1a", num_wells=8)

  def test_missing_wavelength(self):
    with self.assertRaises(PowerWaveParseError):
      parse_scan_data(b",+0201\r\n\x1a", 1)

  def test_empty_section(self):
    with self.assertRaises(PowerWaveParseError):
      parse_scan_data(b"\r\x1a", 1)

  def test_lax_frames_are_rejected(self):
    # Each of these was accepted before the line structure was validated. The
    # first reads as 537 nm at -7.450 OD with no comma, terminator or ^Z at all.
    for blob in (
      b"\r537-7450\x1e",
      b"\r838\x1a,26-48533*64",
      b"\r400,+0000ZZZ\r\n\x1a",
      b"\r400,,+0000\r\n\x1a",
    ):
      with self.subTest(blob=blob):
        with self.assertRaises(PowerWaveParseError):
          parse_scan_data(blob, 1)

  def test_an_unterminated_section_is_rejected(self):
    # _read_until(SUB) always supplies the terminator, so its absence means the
    # section was truncated.
    with self.assertRaises(PowerWaveParseError):
      parse_scan_data(b"\r400,+0000\r\n", 1)

  def test_wavelength_list_requires_three_unsigned_digits(self):
    for blob in (b"-1\r\x1a", b"+24\r\x1a", b"4599612\r\x1a", b"40\r\x1a"):
      with self.subTest(blob=blob):
        with self.assertRaises(PowerWaveParseError):
          parse_scan_wavelength_list(blob)

  def test_checksum_ignores_the_start_code(self):
    section = b"550,+0201\r\n\x1a"
    self.assertEqual(scan_checksum(section), sum(section) % 256)
    self.assertEqual(scan_checksum(b"\r" + section), sum(section) % 256)


class TestScanPlate(unittest.IsolatedAsyncioTestCase):
  """Driving a spectral scan end to end."""

  def _scan_script(self, wavelengths, num_wells=1, calibrated=b"", first_pass=b""):
    script = _ACK + _STATUS_OK  # '{' set geometry
    script += _ACK  # '&' command byte
    script += _ACK  # the request, acknowledged again after ETX
    script += _STATUS_OK
    script += calibrated + b"\x1a"  # calibration section
    script += first_pass + b"\x1a"  # first pass section
    # One CR start code, one CR LF-terminated line per wavelength, then a single
    # ^Z and a single checksum byte for the whole series.
    section = b""
    for w in wavelengths:
      section += b"%03d" % w + b"".join(b",+0%03d" % (100 + i) for i in range(num_wells)) + b"\r\n"
    section += b"\x1a"
    script += b"\r" + section + bytes([scan_checksum(section)])
    return script

  async def test_single_well_scan(self):
    plate = _plate_resource()
    reader, fake = _reader(self._scan_script([400, 410, 420]), model="PowerWaveX")

    result = await reader.scan_plate(
      plate, start_wavelength=400, stop_wavelength=420, step=10, well=plate.get_item(0)
    )

    self.assertEqual(fake.writes[0], b"{")
    self.assertEqual(fake.writes[2], b"&")
    self.assertEqual(fake.writes[3][:18], b"010140042001000000")  # A1, 400-420 by 10
    self.assertEqual(fake.writes[3][18:], b"\x03")  # ETX closes the request
    self.assertEqual(fake.remaining, b"")

    self.assertEqual(result.wavelengths, [400, 410, 420])
    self.assertEqual(result.row, 1)
    self.assertEqual(result.column, 1)
    self.assertEqual(len(result.data), 3)
    self.assertEqual(len(result.data[0]), 1)

  async def test_strip_scan_reads_every_row(self):
    plate = _plate_resource()
    reader, fake = _reader(self._scan_script([400, 500], num_wells=8), model="PowerWaveX")

    result = await reader.scan_plate(
      plate, start_wavelength=400, stop_wavelength=500, step=100, column=3
    )

    self.assertEqual(fake.writes[3][:4], b"0003")  # row "00" means whole strip
    self.assertIsNone(result.row)
    self.assertEqual(result.column, 3)
    self.assertEqual(len(result.data[0]), 8)

  async def test_spectrum_helper(self):
    plate = _plate_resource()
    reader, _ = _reader(self._scan_script([400, 410]), model="PowerWaveX")
    result = await reader.scan_plate(
      plate, start_wavelength=400, stop_wavelength=410, step=10, well=plate.get_item(0)
    )
    self.assertEqual(result.spectrum(), [(400, 0.100), (410, 0.100)])

  async def test_calibrated_wavelengths_are_reported(self):
    plate = _plate_resource()
    reader, _ = _reader(self._scan_script([400, 410], calibrated=b"400\r410\r"), model="PowerWaveX")
    result = await reader.scan_plate(
      plate, start_wavelength=400, stop_wavelength=410, step=10, well=plate.get_item(0)
    )
    self.assertEqual(result.calibrated, [400, 410])

  async def test_calibrate_flag_is_sent(self):
    plate = _plate_resource()
    reader, fake = _reader(self._scan_script([400, 410]), model="PowerWaveX")
    await reader.scan_plate(
      plate,
      start_wavelength=400,
      stop_wavelength=410,
      step=10,
      well=plate.get_item(0),
      calibrate=True,
    )
    self.assertEqual(fake.writes[3][14:15], b"1")

  async def test_nak_on_invalid_arguments(self):
    plate = _plate_resource()
    # geometry ok, '&' acked, then the request is NAKed
    reader, _ = _reader(_ACK + _STATUS_OK + _ACK + _NAK, model="PowerWaveX")
    with self.assertRaises(PowerWaveNakError):
      await reader.scan_plate(
        plate, start_wavelength=400, stop_wavelength=410, step=10, well=plate.get_item(0)
      )

  async def test_checksum_mismatch_raises(self):
    plate = _plate_resource()
    script = bytearray(self._scan_script([400, 410]))
    script[-1] = (script[-1] + 1) % 256
    reader, _ = _reader(bytes(script), model="PowerWaveX")
    with self.assertRaises(PowerWaveChecksumError):
      await reader.scan_plate(
        plate, start_wavelength=400, stop_wavelength=410, step=10, well=plate.get_item(0)
      )

  async def test_requires_exactly_one_target(self):
    plate = _plate_resource()
    reader, fake = _reader(model="PowerWaveX")
    for kwargs in ({}, {"well": plate.get_item(0), "column": 1}):
      with self.subTest(kwargs=sorted(kwargs)):
        with self.assertRaises(ValueError):
          await reader.scan_plate(
            plate, start_wavelength=400, stop_wavelength=410, step=10, **kwargs
          )
    self.assertEqual(fake.writes, [])

  async def test_well_must_belong_to_the_plate(self):
    plate = _plate_resource("a")
    other = _plate_resource("b")
    reader, _ = _reader(model="PowerWaveX")
    with self.assertRaises(ValueError):
      await reader.scan_plate(
        plate, start_wavelength=400, stop_wavelength=410, step=10, well=other.get_item(0)
      )

  async def test_strip_scan_refused_on_384_well_plates(self):
    from pylabrobot.resources import Plate

    plate = _plate_resource()
    reader, _ = _reader(model="PowerWaveX")
    # a 16-row plate cannot use row "00" (page B-14)
    with unittest.mock.patch.object(Plate, "num_items_y", property(lambda self: 16)):
      with self.assertRaises(NotImplementedError):
        await reader.scan_plate(plate, start_wavelength=400, stop_wavelength=410, step=10, column=1)

  async def test_every_invalid_scan_field_is_refused_locally(self):
    # The instrument accepts impossible fields and then hangs, so these checks
    # are load-bearing rather than a convenience. Verified against firmware
    # R06.20, which ACKed row 99 / column 99 / 999-200 nm / step 0 with status 000
    # and then stopped responding.
    plate = _plate_resource()
    reader, fake = _reader(model="PowerWaveX")
    base = {"start_wavelength": 400, "stop_wavelength": 500, "step": 10}

    bad = [
      ("row via a bogus column", {"column": 99}),
      ("column 0", {"column": 0}),
      ("step 0", {"column": 1, "step": 0}),
      ("step beyond the span", {"column": 1, "step": 200}),
      ("start above stop", {"column": 1, "start_wavelength": 600}),
      ("start equal to stop", {"column": 1, "stop_wavelength": 400}),
      ("wavelength below the range", {"column": 1, "start_wavelength": 199}),
      ("wavelength above the range", {"column": 1, "stop_wavelength": 1000}),
    ]
    for label, override in bad:
      with self.subTest(label=label):
        with self.assertRaises(ValueError):
          await reader.scan_plate(plate, **{**base, **override})

    self.assertEqual(fake.writes, [])  # nothing reached the instrument

  async def test_invalid_arguments_are_rejected_before_sending(self):
    plate = _plate_resource()
    reader, fake = _reader(model="PowerWaveX")
    with self.assertRaises(ValueError):
      await reader.scan_plate(
        plate, start_wavelength=500, stop_wavelength=500, step=1, well=plate.get_item(0)
      )
    self.assertEqual(fake.writes, [])
