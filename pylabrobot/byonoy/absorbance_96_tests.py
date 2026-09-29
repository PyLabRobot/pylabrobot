import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from pylabrobot.byonoy import byonoy_a96a_illumination_unit, byonoy_a96a_parking_unit
from pylabrobot.byonoy.absorbance_96 import ByonoyAbsorbance96
from pylabrobot.io.binary import Writer
from pylabrobot.io.hid import HID
from pylabrobot.resources import Coordinate, Lid, Plate, Well


class CalibrationErrorOverrideTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.reader = ByonoyAbsorbance96()
    self.io = MagicMock(spec=HID)
    self.io.write = AsyncMock()
    self.io.read = AsyncMock()
    self.reader.io = self.io

  @staticmethod
  def _field_response(value: int, field: int = 0x800E, flags: int = 0xC1) -> bytes:
    """Build the firmware's integer data-field reply."""
    return Writer().u16(0x0200).u16(field).u8(flags).u32(value).finish().ljust(64, b"\x00")

  @staticmethod
  def _ack(code: int = 0, report: int = 0x0210) -> bytes:
    """Build a write acknowledgement."""
    return Writer().u16(0x0020).u16(report).u16(code).finish().ljust(62, b"\x00") + b"\x00\x40"

  async def test_enable_and_disable_write_integer_and_verify_readback(self):
    for enabled in (True, False):
      with self.subTest(enabled=enabled):
        self.io.read.side_effect = [
          self._ack(),
          self._field_response(int(enabled)),
          self._field_response(int(enabled)),
        ]
        self.io.write.reset_mock()
        await self.reader.set_ignore_errors_in_calibration(enabled)
        write_packet = self.io.write.await_args_list[0].args[0]
        expected = b"\x10\x02\x0e\x80\x00" + int(enabled).to_bytes(4, "little")
        self.assertEqual(write_packet, expected.ljust(62, b"\x00") + b"\x00\x40")
        self.assertEqual(self.io.write.await_count, 2)
        self.assertEqual(await self.reader.request_ignore_errors_in_calibration(), enabled)

  async def test_rejected_write_raises_without_claiming_success(self):
    self.io.read.side_effect = [self._ack(report=0x0040), self._ack(code=1)]
    with self.assertRaisesRegex(RuntimeError, "0x0001"):
      await self.reader.set_ignore_errors_in_calibration(True)
    self.assertEqual(self.io.write.await_count, 1)

  async def test_readback_must_match_requested_value(self):
    self.io.read.side_effect = [self._ack(), self._field_response(0)]
    with self.assertRaisesRegex(RuntimeError, "readback"):
      await self.reader.set_ignore_errors_in_calibration(True)

  async def test_getter_rejects_wrong_field_type_or_value(self):
    for response in (
      self._field_response(0, field=0x800D),
      self._field_response(0, flags=0xC3),
      self._field_response(2),
    ):
      with self.subTest(response=response):
        self.io.read.return_value = response
        with self.assertRaises(RuntimeError):
          await self.reader.request_ignore_errors_in_calibration()

  async def test_cannot_change_override_during_measurement(self):
    self.reader._in_flight_trigger = 0x0320
    with self.assertRaisesRegex(RuntimeError, "measurement"):
      await self.reader.set_ignore_errors_in_calibration(True)
    self.io.write.assert_not_awaited()


class SetupCalibrationErrorOverrideTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.reader = ByonoyAbsorbance96()
    self.io = MagicMock(spec=HID)
    self.reader.io = self.io
    patchers = [
      patch.object(self.reader, "initialize_measurements", new_callable=AsyncMock),
      patch.object(self.reader, "request_ignore_errors_in_calibration", new_callable=AsyncMock),
      patch.object(self.reader, "set_ignore_errors_in_calibration", new_callable=AsyncMock),
      patch.object(self.reader, "request_available_absorbance_wavelengths", new_callable=AsyncMock),
    ]
    self.initialize, self.get_override, self.set_override, self.wavelengths = [
      patcher.start() for patcher in patchers
    ]
    for patcher in patchers:
      self.addCleanup(patcher.stop)
    self.get_override.return_value = False
    self.wavelengths.return_value = [600, 450]

  async def test_default_setup_does_not_access_override(self):
    await self.reader.setup()
    self.io.setup.assert_awaited_once()
    self.initialize.assert_awaited_once()
    self.get_override.assert_not_awaited()
    self.set_override.assert_not_awaited()
    self.assertEqual(self.reader.available_wavelengths, [600, 450])

  async def test_override_is_enabled_during_calibration_and_original_value_restored(self):
    async def initialize() -> None:
      self.set_override.assert_awaited_once_with(True)

    self.initialize.side_effect = initialize
    for original in (False, True):
      with self.subTest(original=original):
        self.get_override.return_value = original
        self.set_override.reset_mock()
        await self.reader.setup(ignore_errors_in_calibration=True)
        self.assertEqual(self.set_override.await_args_list, [call(True), call(original)])
        self.assertEqual(self.reader.available_wavelengths, [600, 450])

  async def test_override_is_restored_if_calibration_fails_or_is_cancelled(self):
    for error in (RuntimeError("calibration failed"), asyncio.CancelledError()):
      with self.subTest(error=error):
        self.initialize.side_effect = error
        self.set_override.reset_mock()
        with self.assertRaises(type(error)):
          await self.reader.setup(ignore_errors_in_calibration=True)
        self.assertEqual(self.set_override.await_args_list, [call(True), call(False)])
        self.wavelengths.assert_not_awaited()

  async def test_override_is_restored_if_enabling_it_fails_verification(self):
    self.set_override.side_effect = [RuntimeError("readback mismatch"), None]
    with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
      await self.reader.setup(ignore_errors_in_calibration=True)
    self.assertEqual(self.set_override.await_args_list, [call(True), call(False)])
    self.initialize.assert_not_awaited()


def _plate(size_z: float) -> Plate:
  well = Well(name="well", size_x=8, size_y=8, size_z=size_z - 1)
  well.location = Coordinate(x=10, y=70, z=1)
  return Plate(name="plate", size_x=127.76, size_y=85.48, size_z=size_z, ordered_items={"A1": well})


class IlluminationUnitHeightCheckTests(unittest.TestCase):
  def _check_with(self, plate: Plate) -> None:
    base = byonoy_a96a_parking_unit(name="base")
    base.plate_holder.assign_child_resource(plate)
    base.illumination_unit_holder.check_can_drop_resource_here(
      byonoy_a96a_illumination_unit(name="illumination_unit")
    )

  def test_the_tallest_plate_that_clears_is_allowed_and_the_next_is_not(self):
    self._check_with(_plate(16.0))
    with self.assertRaises(RuntimeError):
      self._check_with(_plate(16.01))

  def test_a_lid_counts_towards_the_height(self):
    plate = _plate(14.0)
    plate.assign_child_resource(
      Lid(name="lid", size_x=127.76, size_y=85.48, size_z=4.0, nesting_z_height=1.0)
    )
    with self.assertRaises(RuntimeError):
      self._check_with(plate)
