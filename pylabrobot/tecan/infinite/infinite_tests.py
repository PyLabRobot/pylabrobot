"""Tests for the new Tecan Infinite 200 PRO architecture."""

import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.io.usb import USB
from pylabrobot.resources import Coordinate, Plate, Well, create_ordered_items_2d
from pylabrobot.tecan.infinite.driver import TecanInfiniteDriver
from pylabrobot.tecan.infinite.protocol import (
  _AbsorbanceRunDecoder,
  _FluorescenceRunDecoder,
  _LuminescenceRunDecoder,
  _absorbance_od_calibrated,
  _consume_leading_ascii_frame,
  frame_command,
  is_terminal_frame,
)


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


# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------


class TestProtocol(unittest.TestCase):
  def test_frame_command(self):
    framed = frame_command("A")
    self.assertEqual(framed, b"\x02A\x03\x00\x00\x01\x40\x0d")

  def test_consume_leading_ascii_frame(self):
    buffer = bytearray(frame_command("ST") + b"XYZ")
    consumed, text = _consume_leading_ascii_frame(buffer)
    self.assertTrue(consumed)
    self.assertEqual(text, "ST")
    self.assertEqual(buffer, bytearray(b"XYZ"))

  def test_terminal_frames(self):
    self.assertTrue(is_terminal_frame("ST"))
    self.assertTrue(is_terminal_frame("+"))
    self.assertTrue(is_terminal_frame("-"))
    self.assertTrue(is_terminal_frame("BY#T5000"))
    self.assertFalse(is_terminal_frame("OK"))


class TestDecoders(unittest.TestCase):
  def test_absorbance_decoder(self):
    decoder = _AbsorbanceRunDecoder(1)
    cal_len, cal_blob = _abs_calibration_blob(6000, 0, 1000, 0, 1000)
    decoder.feed_bin(cal_len, cal_blob)
    self.assertIsNotNone(decoder.calibration)
    data_len, data_blob = _abs_data_blob(6000, 500, 1000)
    decoder.feed_bin(data_len, data_blob)
    self.assertTrue(decoder.done)
    od = _absorbance_od_calibrated(decoder.calibration, [(500, 1000)])
    self.assertAlmostEqual(od, 0.3010299956639812)

  def test_fluorescence_decoder(self):
    decoder = _FluorescenceRunDecoder(1)
    cal_len, cal_blob = _flr_calibration_blob(4850, 0, 0, 1000)
    decoder.feed_bin(cal_len, cal_blob)
    data_len, data_blob = _flr_data_blob(4850, 5200, 500, 1000)
    decoder.feed_bin(data_len, data_blob)
    self.assertTrue(decoder.done)
    self.assertEqual(decoder.intensities[0], 500)

  def test_luminescence_decoder(self):
    decoder = _LuminescenceRunDecoder(1)
    data_len, data_blob = _lum_data_blob(0, 42)
    decoder.feed_bin(data_len, data_blob)
    self.assertTrue(decoder.done)
    self.assertEqual(decoder.measurements[0].intensity, 42)


# ---------------------------------------------------------------------------
# Driver geometry tests
# ---------------------------------------------------------------------------


class TestDriverGeometry(unittest.TestCase):
  def setUp(self):
    self.driver = TecanInfiniteDriver(counts_per_mm_x=1, counts_per_mm_y=1, counts_per_mm_z=1)
    self.plate = _make_test_plate()

  def test_scan_visit_order_serpentine(self):
    order = self.driver.scan_visit_order(self.plate.get_all_items(), serpentine=True)
    identifiers = [well.get_identifier() for well in order]
    self.assertEqual(identifiers, ["A1", "A2", "A3", "B3", "B2", "B1"])

  def test_scan_visit_order_linear(self):
    order = self.driver.scan_visit_order(self.plate.get_all_items(), serpentine=False)
    identifiers = [well.get_identifier() for well in order]
    self.assertEqual(identifiers, ["A1", "A2", "A3", "B1", "B2", "B3"])

  def test_map_well_to_stage(self):
    stage_x, stage_y = self.driver.map_well_to_stage(self.plate.get_well("A1"))
    self.assertEqual((stage_x, stage_y), (3, 8))
    stage_x, stage_y = self.driver.map_well_to_stage(self.plate.get_well("B1"))
    self.assertEqual((stage_x, stage_y), (3, 16))


# ---------------------------------------------------------------------------
# Backend integration tests
# ---------------------------------------------------------------------------


class TestAbsorbanceBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.mock_usb = AsyncMock(spec=USB)
    self.mock_usb.setup = AsyncMock()
    self.mock_usb.stop = AsyncMock()
    self.mock_usb.write = AsyncMock()
    self.mock_usb.read = AsyncMock(return_value=frame_command("ST"))
    self.plate = _make_test_plate()

  async def test_read_absorbance(self):
    from pylabrobot.tecan.infinite.absorbance_backend import (
      TecanInfiniteAbsorbanceBackend,
      TecanInfiniteAbsorbanceParams,
    )

    driver = TecanInfiniteDriver(counts_per_mm_x=1000, counts_per_mm_y=1000, io=self.mock_usb)
    driver._ready = True
    backend = TecanInfiniteAbsorbanceBackend(driver)

    async def mock_await(decoder, row_count, mode):
      cal_len, cal_blob = _abs_calibration_blob(6000, 0, 1000, 0, 1000)
      decoder.feed_bin(cal_len, cal_blob)
      for _ in range(row_count):
        data_len, data_blob = _abs_data_blob(6000, 500, 1000)
        decoder.feed_bin(data_len, data_blob)

    with patch.object(driver, "_await_measurements", side_effect=mock_await):
      with patch.object(driver, "_await_scan_terminal", new_callable=AsyncMock):
        results = await backend.read_absorbance(
          plate=self.plate, wells=[], wavelength=600,
          backend_params=TecanInfiniteAbsorbanceParams(),
        )

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].wavelength, 600)
    self.assertIsNotNone(results[0].data)
    self.assertAlmostEqual(results[0].data[0][0], 0.3010299956639812)


class TestFluorescenceBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.mock_usb = AsyncMock(spec=USB)
    self.mock_usb.setup = AsyncMock()
    self.mock_usb.stop = AsyncMock()
    self.mock_usb.write = AsyncMock()
    self.mock_usb.read = AsyncMock(return_value=frame_command("ST"))
    self.plate = _make_test_plate()

  async def test_read_fluorescence(self):
    from pylabrobot.tecan.infinite.fluorescence_backend import (
      TecanInfiniteFluorescenceBackend,
      TecanInfiniteFluorescenceParams,
    )

    driver = TecanInfiniteDriver(counts_per_mm_x=1000, counts_per_mm_y=1000, io=self.mock_usb)
    driver._ready = True
    backend = TecanInfiniteFluorescenceBackend(driver)

    async def mock_await(decoder, row_count, mode):
      cal_len, cal_blob = _flr_calibration_blob(4850, 0, 0, 1000)
      decoder.feed_bin(cal_len, cal_blob)
      for _ in range(row_count):
        data_len, data_blob = _flr_data_blob(4850, 5200, 500, 1000)
        decoder.feed_bin(data_len, data_blob)

    with patch.object(driver, "_await_measurements", side_effect=mock_await):
      with patch.object(driver, "_await_scan_terminal", new_callable=AsyncMock):
        results = await backend.read_fluorescence(
          plate=self.plate, wells=[], excitation_wavelength=485,
          emission_wavelength=520, focal_height=20.0,
          backend_params=TecanInfiniteFluorescenceParams(),
        )

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].excitation_wavelength, 485)
    self.assertEqual(results[0].emission_wavelength, 520)


class TestLuminescenceBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.mock_usb = AsyncMock(spec=USB)
    self.mock_usb.setup = AsyncMock()
    self.mock_usb.stop = AsyncMock()
    self.mock_usb.write = AsyncMock()
    self.mock_usb.read = AsyncMock(return_value=frame_command("ST"))
    self.plate = _make_test_plate()

  async def test_read_luminescence(self):
    from pylabrobot.tecan.infinite.luminescence_backend import (
      TecanInfiniteLuminescenceBackend,
      TecanInfiniteLuminescenceParams,
    )

    driver = TecanInfiniteDriver(counts_per_mm_x=1000, counts_per_mm_y=1000, io=self.mock_usb)
    driver._ready = True
    backend = TecanInfiniteLuminescenceBackend(driver)

    async def mock_await(decoder, row_count, mode):
      cal_blob = bytes(14)
      decoder.feed_bin(10, cal_blob)
      for _ in range(row_count):
        data_len, data_blob = _lum_data_blob(0, 1000)
        decoder.feed_bin(data_len, data_blob)

    with patch.object(driver, "_await_measurements", side_effect=mock_await):
      with patch.object(driver, "_await_scan_terminal", new_callable=AsyncMock):
        results = await backend.read_luminescence(
          plate=self.plate, wells=[], focal_height=14.62,
          backend_params=TecanInfiniteLuminescenceParams(),
        )

    self.assertEqual(len(results), 1)
    self.assertIsNotNone(results[0].data)
