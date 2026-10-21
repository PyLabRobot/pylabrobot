import unittest
from unittest.mock import AsyncMock, call, patch

from pylabrobot.io.usb import USB
from pylabrobot.legacy.plate_reading.tecan import (
  ExperimentalTecanInfinite200ProBackend,
  TecanInfiniteResponseError,
)
from pylabrobot.legacy.plate_reading.tecan.infinite_backend import (
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
      name_prefix="plate",
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


class TestTecanInfiniteDecoders(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalTecanInfinite200ProBackend()
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


class TestTecanInfiniteScanGeometry(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = ExperimentalTecanInfinite200ProBackend(counts_per_mm_x=1, counts_per_mm_y=1)
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


class TestTecanInfiniteAscii(unittest.IsolatedAsyncioTestCase):
  def test_frame_command(self):
    framed = ExperimentalTecanInfinite200ProBackend._frame_command("A")
    self.assertEqual(framed, b"\x02A\x03\x00\x00\x01\x40\x0d")

  def test_consume_leading_ascii_frame(self):
    buffer = bytearray(ExperimentalTecanInfinite200ProBackend._frame_command("ST") + b"XYZ")
    consumed, text = _consume_leading_ascii_frame(buffer)
    self.assertTrue(consumed)
    self.assertEqual(text, "ST")
    self.assertEqual(buffer, bytearray(b"XYZ"))

  def test_terminal_frames(self):
    self.assertTrue(ExperimentalTecanInfinite200ProBackend._is_terminal_frame("ST"))
    self.assertTrue(ExperimentalTecanInfinite200ProBackend._is_terminal_frame("+"))
    self.assertTrue(ExperimentalTecanInfinite200ProBackend._is_terminal_frame("-"))
    self.assertFalse(ExperimentalTecanInfinite200ProBackend._is_terminal_frame("BY#T5000"))
    self.assertFalse(ExperimentalTecanInfinite200ProBackend._is_terminal_frame("OK"))

  def test_timed_busy_timeout(self):
    self.assertEqual(ExperimentalTecanInfinite200ProBackend._timed_busy_timeout("BY#T5000"), 5)
    self.assertEqual(ExperimentalTecanInfinite200ProBackend._timed_busy_timeout("BY#T5001"), 6)
    self.assertEqual(ExperimentalTecanInfinite200ProBackend._timed_busy_timeout("+BY#T0"), 1)
    self.assertIsNone(ExperimentalTecanInfinite200ProBackend._timed_busy_timeout("BY#A5000"))


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
      "pylabrobot.legacy.plate_reading.tecan.infinite_backend.USB",
      return_value=self.mock_usb,
    )
    self.mock_usb_class = patcher.start()
    self.addCleanup(patcher.stop)

    self.backend = ExperimentalTecanInfinite200ProBackend(
      counts_per_mm_x=1000, counts_per_mm_y=1000
    )
    self.plate = _make_test_plate()
    self.plate.location = Coordinate.zero()

  def _frame(self, command: str) -> bytes:
    """Helper to frame a command."""
    return ExperimentalTecanInfinite200ProBackend._frame_command(command)

  async def test_timed_command_waits_for_standby_before_next_command(self):
    self.mock_usb.read.side_effect = [
      self._frame("BY#T5000"),
      self._frame("ST"),
      self._frame("+"),
    ]

    mode_responses = await self.backend._send_command("MODE FI.TOP")
    clear_responses = await self.backend._send_command("EXCITATION CLEAR")

    self.assertEqual(mode_responses, ["BY#T5000", "ST"])
    self.assertEqual(clear_responses, ["+"])
    self.assertEqual(self.mock_usb.read.await_args_list[1], call(timeout=30, size=128))
    self.assertEqual(self.mock_usb.read.await_count, 3)

  async def test_timed_command_uses_longer_advertised_timeout(self):
    self.mock_usb.read.side_effect = [
      self._frame("BY#T80000"),
      self._frame("ST"),
    ]

    responses = await self.backend._send_command("INIT FORCE")

    self.assertEqual(responses, ["BY#T80000", "ST"])
    self.assertEqual(self.mock_usb.read.await_args_list[1], call(timeout=80, size=128))

  async def test_required_terminal_response_does_not_accept_busy_frame(self):
    self.mock_usb.read.return_value = self._frame("BY#T5000")

    with self.assertRaisesRegex(TimeoutError, "terminal response frame"):
      await self.backend._read_command_response(max_iterations=1, recover_on_timeout=False)

  async def test_clear_mode_timeout_does_not_reinitialize_or_continue(self):
    self.mock_usb.read.side_effect = TimeoutError("clear response timed out")

    with self.assertRaisesRegex(TecanInfiniteResponseError, "outcome could not be confirmed"):
      await self.backend._clear_mode_settings(excitation=True)

    self.mock_usb.write.assert_awaited_once_with(self._frame("EXCITATION CLEAR"))
    self.mock_usb.stop.assert_not_awaited()
    self.mock_usb.setup.assert_not_awaited()

  async def test_clear_mode_rejects_device_error(self):
    self.mock_usb.read.return_value = self._frame("-")

    with self.assertRaisesRegex(TecanInfiniteResponseError, "did not confirm"):
      await self.backend._clear_mode_settings(excitation=True)

    self.mock_usb.write.assert_awaited_once_with(self._frame("EXCITATION CLEAR"))

  async def test_open(self):
    self.backend._ready = True
    self.mock_usb.read.side_effect = [
      self._frame("BY#T5000"),
      self._frame("ST"),
      self._frame("OUT"),
    ]

    await self.backend.open()

    self.mock_usb.write.assert_has_calls(
      [
        call(self._frame("ABSOLUTE MTP,OUT")),
        call(self._frame("?ABSOLUTE MTP,POS")),
      ]
    )

  async def test_close(self):
    self.backend._ready = True
    self.mock_usb.read.side_effect = [
      self._frame("BY#T5000"),
      self._frame("ST"),
      self._frame("IN"),
    ]

    await self.backend.close(self.plate)

    self.mock_usb.write.assert_has_calls(
      [
        call(self._frame("ABSOLUTE MTP,IN")),
        call(self._frame("?ABSOLUTE MTP,POS")),
      ]
    )

  async def test_transport_timeout_does_not_reinitialize_indeterminate_hardware(self):
    self.mock_usb.read.side_effect = [self._frame("BY#T5000"), TimeoutError("move timed out")]

    with self.assertRaisesRegex(TecanInfiniteResponseError, "transport may not be OUT"):
      await self.backend.open()

    self.mock_usb.stop.assert_not_awaited()
    self.mock_usb.setup.assert_not_awaited()

  async def test_transport_initial_timeout_does_not_reinitialize_indeterminate_hardware(self):
    self.mock_usb.read.side_effect = TimeoutError("initial response timed out")

    with self.assertRaisesRegex(TecanInfiniteResponseError, "was not received"):
      await self.backend.open()

    self.mock_usb.stop.assert_not_awaited()
    self.mock_usb.setup.assert_not_awaited()

  async def test_request_plate_position_reads_transport_position(self):
    self.mock_usb.read.return_value = self._frame("IN")

    position = await self.backend.request_plate_position()

    self.assertEqual(position, "IN")
    self.mock_usb.write.assert_awaited_once_with(self._frame("?ABSOLUTE MTP,POS"))

  async def test_request_plate_sensor_state_returns_reader_states(self):
    for response in ("FREE", "TAKEN", "UNDEFINED"):
      with self.subTest(response=response):
        self.mock_usb.read.return_value = self._frame(response)
        self.assertEqual(await self.backend.request_plate_sensor_state(), response)

    self.assertEqual(
      self.mock_usb.write.await_args_list,
      [call(self._frame("?SENSOR PLATEPOS"))] * 3,
    )

  async def test_request_plate_sensor_state_rejects_unknown_sensor_state(self):
    self.mock_usb.read.return_value = self._frame("NOT_READY")

    with self.assertRaisesRegex(TecanInfiniteResponseError, "unknown sensor state"):
      await self.backend.request_plate_sensor_state()

  async def test_temperature_queries_convert_tenths_of_a_degree(self):
    self.mock_usb.read.side_effect = [self._frame("218"), self._frame("370"), self._frame("ON")]

    current = await self.backend.request_current_temperature()
    target = await self.backend.request_target_temperature()
    status = await self.backend.request_temperature_status()

    self.assertEqual(current, 21.8)
    self.assertEqual(target, 37.0)
    self.assertEqual(status, "ON")
    self.mock_usb.write.assert_has_awaits(
      [
        call(self._frame("?TEMPERATURE PLATE,CURRENT")),
        call(self._frame("?TEMPERATURE PLATE,TARGET")),
        call(self._frame("?TEMPERATURE PLATE,STATUS")),
      ]
    )

  async def test_temperature_query_rejects_non_numeric_response(self):
    self.mock_usb.read.return_value = self._frame("MSG001: warming")

    with self.assertRaisesRegex(TecanInfiniteResponseError, "tenths of a degree Celsius"):
      await self.backend.request_current_temperature()

  async def test_set_temperature_applies_target_enables_control_and_verifies_both(self):
    self.mock_usb.read.side_effect = [
      self._frame("ST"),
      self._frame("370"),
      self._frame("ST"),
      self._frame("ON"),
    ]

    await self.backend.set_temperature(37.0)

    self.mock_usb.write.assert_has_awaits(
      [
        call(self._frame("TEMPERATURE PLATE,TARGET=370")),
        call(self._frame("?TEMPERATURE PLATE,TARGET")),
        call(self._frame("TEMPERATURE PLATE,STATUS=ON")),
        call(self._frame("?TEMPERATURE PLATE,STATUS")),
      ]
    )

  async def test_set_temperature_rejects_invalid_values_before_io(self):
    invalid_cases = [
      (-0.1, "0 to 42 degrees Celsius"),
      (42.1, "0 to 42 degrees Celsius"),
      (37.05, "0.1 degree Celsius increments"),
    ]

    for temperature, message in invalid_cases:
      with self.subTest(temperature=temperature):
        with self.assertRaisesRegex(ValueError, message):
          await self.backend.set_temperature(temperature)

    self.mock_usb.write.assert_not_awaited()

  async def test_set_temperature_rejects_readback_mismatch(self):
    self.mock_usb.read.side_effect = [self._frame("ST"), self._frame("369")]

    with self.assertRaisesRegex(TecanInfiniteResponseError, "requested target 37.0"):
      await self.backend.set_temperature(37.0)

  async def test_set_temperature_stops_after_device_rejects_target(self):
    self.mock_usb.read.return_value = self._frame("-")

    with self.assertRaisesRegex(TecanInfiniteResponseError, "did not confirm"):
      await self.backend.set_temperature(37.0)

    self.mock_usb.write.assert_awaited_once_with(self._frame("TEMPERATURE PLATE,TARGET=370"))

  async def test_stop_temperature_control_verifies_disabled_state(self):
    self.mock_usb.read.side_effect = [self._frame("ST"), self._frame("OFF")]

    await self.backend.stop_temperature_control()

    self.mock_usb.write.assert_has_awaits(
      [
        call(self._frame("TEMPERATURE PLATE,STATUS=OFF")),
        call(self._frame("?TEMPERATURE PLATE,STATUS")),
      ]
    )

  async def test_shaking_queries_decode_device_values(self):
    self.mock_usb.read.side_effect = [
      self._frame("ORBITAL"),
      self._frame("2"),
      self._frame("1500"),
    ]

    self.assertEqual(await self.backend.request_shaking_mode(), "ORBITAL")
    self.assertEqual(await self.backend.request_shaking_duration(), 2)
    self.assertEqual(await self.backend.request_shaking_amplitude(), 1.5)

  async def test_shaking_queries_report_unconfigured_values_as_unavailable(self):
    self.mock_usb.read.side_effect = [self._frame("-1"), self._frame("-1")]

    self.assertIsNone(await self.backend.request_shaking_duration())
    self.assertIsNone(await self.backend.request_shaking_amplitude())

  async def test_shake_configures_readbacks_and_waits_for_completion(self):
    self.mock_usb.read.side_effect = [
      self._frame("ST"),
      self._frame("ORBITAL"),
      self._frame("ST"),
      self._frame("1000"),
      self._frame("ST"),
      self._frame("2"),
      self._frame("BY#T2000"),
      self._frame("ST"),
    ]

    await self.backend.shake(duration=2, mode="ORBITAL", amplitude=1.0)

    self.mock_usb.write.assert_has_awaits(
      [
        call(self._frame("SHAKING MODE=ORBITAL")),
        call(self._frame("?SHAKING MODE")),
        call(self._frame("SHAKING AMPLITUDE=1000")),
        call(self._frame("?SHAKING AMPLITUDE")),
        call(self._frame("SHAKING TIME=2")),
        call(self._frame("?SHAKING TIME")),
        call(self._frame("SHAKING ON")),
      ]
    )
    self.assertEqual(self.mock_usb.read.await_args_list[-1], call(timeout=7, size=128))

  async def test_shake_accepts_coalesced_busy_and_completion_frames(self):
    self.mock_usb.read.side_effect = [
      self._frame("ST"),
      self._frame("LINEAR"),
      self._frame("ST"),
      self._frame("1500"),
      self._frame("ST"),
      self._frame("1"),
      self._frame("BY#T1000") + self._frame("ST"),
    ]

    await self.backend.shake(duration=1, mode="LINEAR", amplitude=1.5)

    self.assertEqual(self.mock_usb.read.await_count, 7)

  async def test_shake_timeout_does_not_reinitialize_indeterminate_hardware(self):
    self.mock_usb.read.side_effect = [
      self._frame("ST"),
      self._frame("ORBITAL"),
      self._frame("ST"),
      self._frame("1000"),
      self._frame("ST"),
      self._frame("1"),
      self._frame("BY#T1000"),
      TimeoutError("reader did not report completion"),
    ]

    with self.assertRaisesRegex(TecanInfiniteResponseError, "reader may still be shaking"):
      await self.backend.shake(duration=1)

    self.mock_usb.stop.assert_not_awaited()
    self.mock_usb.setup.assert_not_awaited()

  async def test_shake_initial_timeout_does_not_reinitialize_indeterminate_hardware(self):
    self.mock_usb.read.side_effect = [
      self._frame("ST"),
      self._frame("ORBITAL"),
      self._frame("ST"),
      self._frame("1000"),
      self._frame("ST"),
      self._frame("1"),
      TimeoutError("initial response timed out"),
    ]

    with self.assertRaisesRegex(TecanInfiniteResponseError, "reader may still be shaking"):
      await self.backend.shake(duration=1)

    self.mock_usb.stop.assert_not_awaited()
    self.mock_usb.setup.assert_not_awaited()

  async def test_control_readback_timeout_does_not_reinitialize_indeterminate_hardware(self):
    self.mock_usb.read.side_effect = [self._frame("ST"), TimeoutError("readback timed out")]

    with self.assertRaisesRegex(TecanInfiniteResponseError, "state is indeterminate"):
      await self.backend.set_temperature(25.0)

    self.mock_usb.stop.assert_not_awaited()
    self.mock_usb.setup.assert_not_awaited()

  async def test_normal_read_timeout_retains_transport_recovery(self):
    self.mock_usb.read.side_effect = TimeoutError("transport timed out")

    with patch.object(self.backend, "_recover_transport", new_callable=AsyncMock) as recover:
      with self.assertRaisesRegex(TimeoutError, "transport timed out"):
        await self.backend._read_packet(128)

    recover.assert_awaited_once_with()

  async def test_shake_rejects_invalid_parameters_before_io(self):
    invalid_cases = [
      {"duration": 0},
      {"duration": 1000},
      {"duration": True},
      {"duration": 1, "mode": "SIDEWAYS"},
      {"duration": 1, "amplitude": 0.5},
      {"duration": 1, "amplitude": 1.2},
      {"duration": 1, "amplitude": 6.5},
    ]

    for parameters in invalid_cases:
      with self.subTest(parameters=parameters):
        with self.assertRaises(ValueError):
          await self.backend.shake(**parameters)  # type: ignore[arg-type]

    self.mock_usb.write.assert_not_awaited()

  async def test_request_instrument_status_normalizes_known_states_and_retains_raw_reply(self):
    cases = [
      ("ST", "standby"),
      ("PD", "power_down"),
      ("PU", "power_up"),
      ("PA", "parked"),
      ("BY", "busy"),
      ("BY#T5000", "busy"),
      ("BY%C50", "unknown"),
      ("BY%50", "busy"),
      ("BY$reading", "busy"),
      ("+BY#C2", "busy_in_background"),
      ("MSG001: service requested", "message"),
      ("NEW_STATUS", "unknown"),
    ]

    for raw, expected_state in cases:
      with self.subTest(raw=raw):
        self.mock_usb.read.return_value = self._frame(raw)
        status = await self.backend.request_instrument_status()
        self.assertEqual(status.state, expected_state)
        self.assertEqual(status.raw, raw)

  async def test_request_is_busy_sends_one_status_query(self):
    self.mock_usb.read.return_value = self._frame("BY#C4")

    self.assertTrue(await self.backend.request_is_busy())

    self.mock_usb.write.assert_awaited_once_with(self._frame("QQ"))

  async def test_request_is_busy_rejects_indeterminate_status(self):
    for response in ("MSG001: service requested", "NEW_STATUS"):
      with self.subTest(response=response):
        self.mock_usb.read.return_value = self._frame(response)
        with self.assertRaisesRegex(TecanInfiniteResponseError, "known busy or non-busy state"):
          await self.backend.request_is_busy()

  async def test_state_query_raises_device_error_with_raw_reply(self):
    self.mock_usb.read.return_value = self._frame("ERR123: sensor failed")

    with self.assertRaises(TecanInfiniteResponseError) as raised:
      await self.backend.request_plate_sensor_state()

    self.assertEqual(raised.exception.command, "?SENSOR PLATEPOS")
    self.assertEqual(raised.exception.responses, ("ERR123: sensor failed",))

  async def test_state_query_requires_exactly_one_response_frame(self):
    for response in (b"", self._frame("IN") + self._frame("ST")):
      with self.subTest(response=response):
        self.mock_usb.read.return_value = response
        with self.assertRaisesRegex(TecanInfiniteResponseError, "exactly one frame"):
          await self.backend.request_plate_position()

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
        call(self._frame("ABSOLUTE MTP,X=3000,Y=8000")),
        call(self._frame("SCAN DIRECTION=ALTUP")),
        call(self._frame("SCANX 3000,23000,3")),
        call(self._frame("ABSOLUTE MTP,Y=16000")),
        call(self._frame("ABSOLUTE MTP,X=23000,Y=16000")),
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

  async def test_read_absorbance_uses_late_pending_calibration(self):
    self.backend._ready = True
    terminal_calls = 0

    async def mock_await(decoder, row_count, mode):
      for _ in range(row_count):
        data_len, data_blob = _abs_data_blob(6000, 500, 1000)
        decoder.feed_bin(data_len, data_blob)

    async def mock_terminal(_saw_terminal):
      nonlocal terminal_calls
      terminal_calls += 1
      if terminal_calls == 2:
        cal_len, cal_blob = _abs_calibration_blob(6000, 0, 1000, 0, 1000)
        self.backend._pending_bin_events.append((cal_len, cal_blob))

    with patch.object(self.backend, "_await_measurements", side_effect=mock_await):
      with patch.object(self.backend, "_await_scan_terminal", side_effect=mock_terminal):
        result = await self.backend.read_absorbance(self.plate, [], wavelength=600)

    self.assertAlmostEqual(result[0]["data"][0][0], 0.3010299956639812)

  async def test_read_absorbance_subset_prepositions_to_masked_row_start(self):
    self.backend._ready = True
    wells = self.plate.get_wells(["A2", "A3", "B1", "B2"])

    async def mock_await(decoder, row_count, mode):
      cal_len, cal_blob = _abs_calibration_blob(6000, 0, 1000, 0, 1000)
      if decoder.calibration is None:
        decoder.feed_bin(cal_len, cal_blob)
      for _ in range(row_count):
        data_len, data_blob = _abs_data_blob(6000, 500, 1000)
        decoder.feed_bin(data_len, data_blob)

    with patch.object(self.backend, "_await_measurements", side_effect=mock_await):
      with patch.object(self.backend, "_await_scan_terminal", new_callable=AsyncMock):
        result = await self.backend.read_absorbance(self.plate, wells, wavelength=600)

    self.mock_usb.write.assert_has_calls(
      [
        call(self._frame("ABSOLUTE MTP,Y=8000")),
        call(self._frame("ABSOLUTE MTP,X=13000,Y=8000")),
        call(self._frame("SCAN DIRECTION=ALTUP")),
        call(self._frame("SCANX 13000,23000,2")),
        call(self._frame("ABSOLUTE MTP,Y=16000")),
        call(self._frame("ABSOLUTE MTP,X=13000,Y=16000")),
        call(self._frame("SCAN DIRECTION=ALTUP")),
        call(self._frame("SCANX 13000,3000,2")),
      ]
    )
    self.assertIsNone(result[0]["data"][0][0])
    self.assertAlmostEqual(result[0]["data"][0][1], 0.3010299956639812)
    self.assertAlmostEqual(result[0]["data"][0][2], 0.3010299956639812)
    self.assertAlmostEqual(result[0]["data"][1][0], 0.3010299956639812)
    self.assertAlmostEqual(result[0]["data"][1][1], 0.3010299956639812)
    self.assertIsNone(result[0]["data"][1][2])

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
          self.plate, [], excitation_wavelength=485, emission_wavelength=520
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
        call(self._frame("ABSOLUTE MTP,X=3000,Y=8000")),
        call(self._frame("SCAN DIRECTION=UP")),
        call(self._frame("SCANX 3000,23000,3")),
        call(self._frame("ABSOLUTE MTP,Y=16000")),
        call(self._frame("ABSOLUTE MTP,X=23000,Y=16000")),
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
        call(self._frame("ABSOLUTE MTP,X=3000,Y=8000")),
        call(self._frame("SCAN DIRECTION=UP")),
        call(self._frame("SCANX 3000,23000,3")),
        call(self._frame("ABSOLUTE MTP,Y=16000")),
        call(self._frame("ABSOLUTE MTP,X=3000,Y=16000")),
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

  async def test_read_luminescence_defaults_focal_height_to_20mm(self):
    """Test that read_luminescence defaults focal height to 20 mm."""
    self.backend._ready = True

    async def mock_await(decoder, row_count, mode):
      cal_blob = bytes(14)
      decoder.feed_bin(10, cal_blob)
      for _ in range(row_count):
        data_len, data_blob = _lum_data_blob(0, 1000)
        decoder.feed_bin(data_len, data_blob)

    with patch.object(self.backend, "_await_measurements", side_effect=mock_await):
      with patch.object(self.backend, "_await_scan_terminal", new_callable=AsyncMock):
        await self.backend.read_luminescence(self.plate, [])

    self.mock_usb.write.assert_any_call(self._frame("POSITION LUM,Z=20000"))
