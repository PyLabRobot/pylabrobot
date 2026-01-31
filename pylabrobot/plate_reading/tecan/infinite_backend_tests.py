import unittest
from unittest.mock import AsyncMock, call, patch

from pylabrobot.io.usb import USB
from pylabrobot.plate_reading.tecan.infinite_backend import (
  InfiniteScanConfig,
  TecanInfinite200ProBackend,
  _absorbance_od_calibrated,
  _AbsorbanceRunDecoder,
  _consume_leading_ascii_frame,
  _FluorescenceRunDecoder,
  _LuminescenceRunDecoder,
)
from pylabrobot.resources import Coordinate, Plate, Well, create_ordered_items_2d
from pylabrobot.resources.tecan.plates import Plate_384_Well


def _pack_u16(words):
  return b"".join(int(word).to_bytes(2, "big") for word in words)


def _bin_blob(payload):
  payload_len = len(payload)
  trailer = b"\x00\x00\x00\x00"
  return payload_len, payload + trailer


def _abs_calibration_blob(ex_decitenth, meas_dark, meas_bright, ref_dark, ref_bright):
  header = _pack_u16([0, ex_decitenth])
  item = (0).to_bytes(4, "big") + _pack_u16([0, 0, meas_dark, meas_bright, 0, ref_dark, ref_bright])
  return _bin_blob(header + item)


def _abs_data_blob(ex_decitenth, meas, ref):
  payload = _pack_u16([0, ex_decitenth, 0, 0, 0, meas, ref])
  return _bin_blob(payload)


def _flr_calibration_blob(ex_decitenth, meas_dark, ref_dark, ref_bright):
  words = [ex_decitenth, 0, 0, 0, 0, meas_dark, 0, ref_dark, ref_bright]
  return _bin_blob(_pack_u16(words))


def _flr_data_blob(ex_decitenth, em_decitenth, meas, ref):
  words = [0, ex_decitenth, em_decitenth, 0, 0, 0, meas, ref]
  return _bin_blob(_pack_u16(words))


def _lum_data_blob(em_decitenth: int, intensity: int):
  payload = bytearray(14)
  payload[0:2] = (0).to_bytes(2, "big")
  payload[2:4] = int(em_decitenth).to_bytes(2, "big")
  payload[10:14] = int(intensity).to_bytes(4, "big", signed=True)
  return _bin_blob(bytes(payload))


def _make_test_plate():
  plate = Plate(
    "plate",
    size_x=30,
    size_y=20,
    size_z=10,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=3,
      num_items_y=2,
      dx=1,
      dy=2,
      dz=0,
      item_dx=10,
      item_dy=8,
      size_x=4,
      size_y=4,
      size_z=5,
    ),
  )
  plate.location = Coordinate.zero()
  return plate


def _egg_grid():
  return [
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      9,
      31,
      46,
      42,
      7,
      2,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      1,
      24,
      69,
      100,
      137,
      142,
      70,
      24,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      24,
      77,
      128,
      135,
      123,
      68,
      52,
      26,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      4,
      60,
      104,
      114,
      86,
      72,
      48,
      2,
      2,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      9,
      75,
      122,
      82,
      71,
      99,
      69,
      4,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      3,
      64,
      132,
      148,
      61,
      75,
      137,
      86,
      17,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      23,
      98,
      160,
      87,
      92,
      139,
      133,
      65,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      4,
      53,
      100,
      93,
      104,
      125,
      146,
      46,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      33,
      73,
      103,
      128,
      143,
      164,
      169,
      61,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      4,
      60,
      93,
      113,
      90,
      107,
      124,
      137,
      118,
      7,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      3,
      64,
      97,
      98,
      63,
      94,
      95,
      135,
      121,
      8,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      36,
      100,
      118,
      119,
      126,
      140,
      154,
      65,
      1,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      3,
      40,
      98,
      141,
      150,
      121,
      61,
      6,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      8,
      75,
      88,
      12,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      45,
      53,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
    [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      9,
      11,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ],
  ]


class TestTecanInfiniteDecoders(unittest.TestCase):
  def setUp(self):
    self.backend = TecanInfinite200ProBackend()
    self.plate = Plate_384_Well(name="plate")
    self.grid = _egg_grid()
    self.max_intensity = max(max(row) for row in self.grid)
    self.scan_wells = self.backend._scan_visit_order(self.plate.get_all_items(), serpentine=True)

  def _assert_matrix(self, actual, expected):
    self.assertEqual(len(actual), len(expected))
    for row_actual, row_expected in zip(actual, expected):
      self.assertEqual(len(row_actual), len(row_expected))
      for value, exp in zip(row_actual, row_expected):
        self.assertAlmostEqual(value or 0.0, exp)

  def _run_decoder_case(self, decoder, build_packet, extract_actual):
    expected_values = []
    for well in self.scan_wells:
      intensity = self.grid[well.get_row()][well.get_column()]
      payload_len, blob, expected = build_packet(intensity)
      decoder.feed_bin(payload_len, blob)
      expected_values.append(expected)
    self.assertTrue(decoder.done)
    actual_values = extract_actual(decoder)
    matrix = self.backend._format_plate_result(self.plate, self.scan_wells, actual_values)
    expected = self.backend._format_plate_result(self.plate, self.scan_wells, expected_values)
    self._assert_matrix(matrix, expected)

  def test_decode_absorbance_pattern(self):
    wavelength = 600
    reference = 10000
    max_absorbance = 1.0
    decoder = _AbsorbanceRunDecoder(len(self.scan_wells))
    cal_len, cal_blob = _abs_calibration_blob(
      wavelength * 10,
      meas_dark=0,
      meas_bright=1000,
      ref_dark=0,
      ref_bright=1000,
    )
    decoder.feed_bin(cal_len, cal_blob)
    cal = decoder.calibration
    assert cal is not None

    def build_packet(intensity):
      target = 0.0
      if self.max_intensity:
        target = (intensity / self.max_intensity) * max_absorbance
      sample = max(1, int(round(reference / (10**target))))
      payload_len, blob = _abs_data_blob(wavelength * 10, sample, reference)
      expected = _absorbance_od_calibrated(cal, [(sample, reference)])
      return payload_len, blob, expected

    def extract_actual(decoder):
      return [
        _absorbance_od_calibrated(cal, [(meas.sample, meas.reference)])
        for meas in decoder.measurements
      ]

    self._run_decoder_case(decoder, build_packet, extract_actual)

  def test_decode_fluorescence_pattern(self):
    excitation = 485
    emission = 520
    decoder = _FluorescenceRunDecoder(len(self.scan_wells))
    cal_len, cal_blob = _flr_calibration_blob(
      excitation * 10, meas_dark=0, ref_dark=0, ref_bright=1000
    )
    decoder.feed_bin(cal_len, cal_blob)

    def build_packet(intensity):
      payload_len, blob = _flr_data_blob(excitation * 10, emission * 10, intensity, 1000)
      return payload_len, blob, intensity

    def extract_actual(decoder):
      return decoder.intensities

    self._run_decoder_case(decoder, build_packet, extract_actual)

  def test_decode_luminescence_pattern(self):
    decoder = _LuminescenceRunDecoder(len(self.scan_wells))

    def build_packet(intensity):
      payload_len, blob = _lum_data_blob(0, intensity)
      return payload_len, blob, intensity

    def extract_actual(decoder):
      return [measurement.intensity for measurement in decoder.measurements]

    self._run_decoder_case(decoder, build_packet, extract_actual)


class TestTecanInfiniteScanGeometry(unittest.TestCase):
  def setUp(self):
    self.backend = TecanInfinite200ProBackend(
      scan_config=InfiniteScanConfig(counts_per_mm_x=1, counts_per_mm_y=1)
    )
    self.plate = _make_test_plate()

  def test_scan_visit_order_serpentine(self):
    order = self.backend._scan_visit_order(self.plate.get_all_items(), serpentine=True)
    identifiers = [well.get_identifier() for well in order]
    self.assertEqual(identifiers, ["A1", "A2", "A3", "B3", "B2", "B1"])

  def test_scan_visit_order_linear(self):
    order = self.backend._scan_visit_order(self.plate.get_all_items(), serpentine=False)
    identifiers = [well.get_identifier() for well in order]
    self.assertEqual(identifiers, ["A1", "A2", "A3", "B1", "B2", "B3"])

  def test_scan_range_serpentine(self):
    setattr(self.backend, "_map_well_to_stage", lambda well: (well.get_column(), well.get_row()))
    row_index, row_wells = self.backend._group_by_row(self.plate.get_all_items())[0]
    start_x, end_x, count = self.backend._scan_range(row_index, row_wells, serpentine=True)
    self.assertEqual((start_x, end_x, count), (0, 2, 3))
    row_index, row_wells = self.backend._group_by_row(self.plate.get_all_items())[1]
    start_x, end_x, count = self.backend._scan_range(row_index, row_wells, serpentine=True)
    self.assertEqual((start_x, end_x, count), (2, 0, 3))

  def test_map_well_to_stage(self):
    stage_x, stage_y = self.backend._map_well_to_stage(self.plate.get_well("A1"))
    self.assertEqual((stage_x, stage_y), (3, 8))
    stage_x, stage_y = self.backend._map_well_to_stage(self.plate.get_well("B1"))
    self.assertEqual((stage_x, stage_y), (3, 16))


class TestTecanInfiniteAscii(unittest.TestCase):
  def test_frame_command(self):
    framed = TecanInfinite200ProBackend._frame_command("A")
    self.assertEqual(framed, b"\x02A\x03\x00\x00\x01\x40\x0d")

  def test_consume_leading_ascii_frame(self):
    buffer = bytearray(TecanInfinite200ProBackend._frame_command("ST") + b"XYZ")
    consumed, text = _consume_leading_ascii_frame(buffer)
    self.assertTrue(consumed)
    self.assertEqual(text, "ST")
    self.assertEqual(buffer, bytearray(b"XYZ"))

  def test_terminal_frames(self):
    self.assertTrue(TecanInfinite200ProBackend._is_terminal_frame("ST"))
    self.assertTrue(TecanInfinite200ProBackend._is_terminal_frame("+"))
    self.assertTrue(TecanInfinite200ProBackend._is_terminal_frame("-"))
    self.assertTrue(TecanInfinite200ProBackend._is_terminal_frame("BY#T5000"))
    self.assertFalse(TecanInfinite200ProBackend._is_terminal_frame("OK"))


class TestTecanInfiniteCommands(unittest.IsolatedAsyncioTestCase):
  """Tests that verify correct commands are sent to the device."""

  def setUp(self):
    self.mock_usb = AsyncMock(spec=USB)
    self.mock_usb.setup = AsyncMock()
    self.mock_usb.stop = AsyncMock()
    self.mock_usb.write = AsyncMock()
    # Default to returning terminal response
    self.mock_usb.read = AsyncMock(return_value=self._frame("ST"))

    patcher = patch(
      "pylabrobot.plate_reading.tecan.infinite_backend.USB",
      return_value=self.mock_usb,
    )
    self.mock_usb_class = patcher.start()
    self.addCleanup(patcher.stop)

    self.backend = TecanInfinite200ProBackend(
      scan_config=InfiniteScanConfig(counts_per_mm_x=1000, counts_per_mm_y=1000)
    )
    self.plate = _make_test_plate()
    self.plate.location = Coordinate.zero()

  def _frame(self, command: str) -> bytes:
    """Helper to frame a command."""
    return TecanInfinite200ProBackend._frame_command(command)

  async def test_open(self):
    self.backend._ready = True

    await self.backend.open()

    self.mock_usb.write.assert_has_calls(
      [
        call(self._frame("ABSOLUTE MTP,OUT")),
        call(self._frame("BY#T5000")),
      ]
    )

  async def test_close(self):
    self.backend._ready = True

    await self.backend.close(self.plate)

    self.mock_usb.write.assert_has_calls(
      [
        call(self._frame("ABSOLUTE MTP,IN")),
        call(self._frame("BY#T5000")),
      ]
    )

  async def test_read_absorbance_commands(self):
    """Test that read_absorbance sends the correct configuration commands."""
    self.backend._ready = True

    async def mock_await(decoder, row_count, mode):
      cal_len, cal_blob = _abs_calibration_blob(6000, 0, 1000, 0, 1000)
      decoder.feed_bin(cal_len, cal_blob)
      for _ in range(row_count):
        data_len, data_blob = _abs_data_blob(6000, 500, 1000)
        decoder.feed_bin(data_len, data_blob)

    with patch.object(self.backend, "_await_measurements", side_effect=mock_await):
      with patch.object(self.backend, "_await_scan_terminal", new_callable=AsyncMock):
        await self.backend.read_absorbance(self.plate, [], wavelength=600)

    self.mock_usb.write.assert_has_calls(
      [
        # _begin_run
        call(self._frame("KEYLOCK ON")),
        # _configure_absorbance
        call(self._frame("MODE ABS")),
        call(self._frame("EXCITATION CLEAR")),
        call(self._frame("TIME CLEAR")),
        call(self._frame("GAIN CLEAR")),
        call(self._frame("READS CLEAR")),
        call(self._frame("POSITION CLEAR")),
        call(self._frame("MIRROR CLEAR")),
        call(self._frame("EXCITATION 0,ABS,6000,90,0")),
        call(self._frame("EXCITATION 1,ABS,6000,90,0")),
        call(self._frame("READS 0,NUMBER=25")),
        call(self._frame("READS 1,NUMBER=25")),
        call(self._frame("TIME 0,READDELAY=0")),
        call(self._frame("TIME 1,READDELAY=0")),
        call(self._frame("SCAN DIRECTION=ALTUP")),
        call(self._frame("#RATIO LABELS")),
        call(self._frame("BEAM DIAMETER=700")),
        call(self._frame("RATIO LABELS=1")),
        call(self._frame("PREPARE REF")),
        # row scans (2 rows in test plate)
        call(self._frame("ABSOLUTE MTP,Y=8000")),
        call(self._frame("SCAN DIRECTION=ALTUP")),
        call(self._frame("SCANX 3000,23000,3")),
        call(self._frame("ABSOLUTE MTP,Y=16000")),
        call(self._frame("SCAN DIRECTION=ALTUP")),
        call(self._frame("SCANX 23000,3000,3")),
        # _end_run
        call(self._frame("TERMINATE")),
        call(self._frame("CHECK MTP.STEPLOSS")),
        call(self._frame("CHECK ABS.STEPLOSS")),
        call(self._frame("KEYLOCK OFF")),
        call(self._frame("ABSOLUTE MTP,IN")),
      ]
    )

  async def test_read_fluorescence_commands(self):
    """Test that read_fluorescence sends the correct configuration commands."""
    self.backend._ready = True

    async def mock_await(decoder, row_count, mode):
      cal_len, cal_blob = _flr_calibration_blob(4850, 0, 0, 1000)
      decoder.feed_bin(cal_len, cal_blob)
      for _ in range(row_count):
        data_len, data_blob = _flr_data_blob(4850, 5200, 500, 1000)
        decoder.feed_bin(data_len, data_blob)

    with patch.object(self.backend, "_await_measurements", side_effect=mock_await):
      with patch.object(self.backend, "_await_scan_terminal", new_callable=AsyncMock):
        await self.backend.read_fluorescence(
          self.plate, [], excitation_wavelength=485, emission_wavelength=520, focal_height=20.0
        )

    # Fluorescence config is sent twice (UI behavior)
    fl_config_commands = [
      call(self._frame("MODE FI.TOP")),
      call(self._frame("EXCITATION CLEAR")),
      call(self._frame("EMISSION CLEAR")),
      call(self._frame("TIME CLEAR")),
      call(self._frame("GAIN CLEAR")),
      call(self._frame("READS CLEAR")),
      call(self._frame("POSITION CLEAR")),
      call(self._frame("MIRROR CLEAR")),
      call(self._frame("EXCITATION 0,FI,4850,50,0")),
      call(self._frame("EMISSION 0,FI,5200,200,0")),
      call(self._frame("TIME 0,INTEGRATION=20")),
      call(self._frame("TIME 0,LAG=0")),
      call(self._frame("TIME 0,READDELAY=0")),
      call(self._frame("GAIN 0,VALUE=100")),
      call(self._frame("POSITION 0,Z=20000")),
      call(self._frame("BEAM DIAMETER=3000")),
      call(self._frame("SCAN DIRECTION=UP")),
      call(self._frame("RATIO LABELS=1")),
      call(self._frame("READS 0,NUMBER=25")),
      call(self._frame("EXCITATION 1,FI,4850,50,0")),
      call(self._frame("EMISSION 1,FI,5200,200,0")),
      call(self._frame("TIME 1,INTEGRATION=20")),
      call(self._frame("TIME 1,LAG=0")),
      call(self._frame("TIME 1,READDELAY=0")),
      call(self._frame("GAIN 1,VALUE=100")),
      call(self._frame("POSITION 1,Z=20000")),
      call(self._frame("READS 1,NUMBER=25")),
    ]

    self.mock_usb.write.assert_has_calls(
      [
        # _begin_run
        call(self._frame("KEYLOCK ON")),
        # _configure_fluorescence (sent twice)
        *fl_config_commands,
        *fl_config_commands,
        call(self._frame("PREPARE REF")),
        # row scans (2 rows in test plate)
        call(self._frame("ABSOLUTE MTP,Y=8000")),
        call(self._frame("SCAN DIRECTION=UP")),
        call(self._frame("SCANX 3000,23000,3")),
        call(self._frame("ABSOLUTE MTP,Y=16000")),
        call(self._frame("SCAN DIRECTION=UP")),
        call(self._frame("SCANX 23000,3000,3")),
        # _end_run
        call(self._frame("TERMINATE")),
        call(self._frame("CHECK MTP.STEPLOSS")),
        call(self._frame("CHECK FI.TOP.STEPLOSS")),
        call(self._frame("CHECK FI.STEPLOSS.Z")),
        call(self._frame("KEYLOCK OFF")),
        call(self._frame("ABSOLUTE MTP,IN")),
      ]
    )

  async def test_read_luminescence_commands(self):
    """Test that read_luminescence sends the correct configuration commands."""
    self.backend._ready = True

    async def mock_await(decoder, row_count, mode):
      cal_blob = bytes(14)
      decoder.feed_bin(10, cal_blob)
      for _ in range(row_count):
        data_len, data_blob = _lum_data_blob(0, 1000)
        decoder.feed_bin(data_len, data_blob)

    with patch.object(self.backend, "_await_measurements", side_effect=mock_await):
      with patch.object(self.backend, "_await_scan_terminal", new_callable=AsyncMock):
        await self.backend.read_luminescence(self.plate, [], focal_height=14.62)

    self.mock_usb.write.assert_has_calls(
      [
        # _begin_run
        call(self._frame("KEYLOCK ON")),
        # _configure_luminescence
        call(self._frame("MODE LUM")),
        call(self._frame("CHECK LUM.FIBER")),
        call(self._frame("CHECK LUM.LID")),
        call(self._frame("CHECK LUM.STEPLOSS")),
        call(self._frame("MODE LUM")),
        call(self._frame("EMISSION CLEAR")),
        call(self._frame("TIME CLEAR")),
        call(self._frame("GAIN CLEAR")),
        call(self._frame("READS CLEAR")),
        call(self._frame("POSITION CLEAR")),
        call(self._frame("MIRROR CLEAR")),
        call(self._frame("POSITION LUM,Z=14620")),
        call(self._frame("TIME 0,INTEGRATION=3000000")),
        call(self._frame("READS 0,NUMBER=25")),
        call(self._frame("SCAN DIRECTION=UP")),
        call(self._frame("RATIO LABELS=1")),
        call(self._frame("EMISSION 1,EMPTY,0,0,0")),
        call(self._frame("TIME 1,INTEGRATION=1000000")),
        call(self._frame("TIME 1,READDELAY=0")),
        call(self._frame("READS 1,NUMBER=25")),
        call(self._frame("#EMISSION ATTENUATION")),
        call(self._frame("PREPARE REF")),
        # row scans (2 rows, non-serpentine so both scan left-to-right)
        call(self._frame("ABSOLUTE MTP,Y=8000")),
        call(self._frame("SCAN DIRECTION=UP")),
        call(self._frame("SCANX 3000,23000,3")),
        call(self._frame("ABSOLUTE MTP,Y=16000")),
        call(self._frame("SCAN DIRECTION=UP")),
        call(self._frame("SCANX 3000,23000,3")),
        # _end_run
        call(self._frame("TERMINATE")),
        call(self._frame("CHECK MTP.STEPLOSS")),
        call(self._frame("CHECK LUM.STEPLOSS")),
        call(self._frame("KEYLOCK OFF")),
        call(self._frame("ABSOLUTE MTP,IN")),
      ]
    )
