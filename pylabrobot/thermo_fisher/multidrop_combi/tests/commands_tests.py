import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.thermo_fisher.multidrop_combi.peristaltic_dispensing_backend import (
  MultidropCombiPeristalticDispensingBackend,
  _ul_to_tenths,
)
from pylabrobot.thermo_fisher.multidrop_combi.enums import DispensingOrder, PrimeMode, EmptyMode
from pylabrobot.resources import Plate, Well, create_ordered_items_2d
from pylabrobot.resources.well import CrossSectionType, WellBottomType


def _make_backend() -> MultidropCombiPeristalticDispensingBackend:
  """Create a backend with a mock driver."""
  driver = MagicMock()
  driver.send_command = AsyncMock(return_value=[])
  driver.send_abort_signal = AsyncMock()
  driver.acknowledge_error = AsyncMock()
  backend = MultidropCombiPeristalticDispensingBackend(driver=driver)
  return backend


def _make_plate() -> Plate:
  return Plate(
    name="test_plate",
    size_x=127.76,
    size_y=85.48,
    size_z=14.2,
    model="test",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.0,
      size_y=6.0,
      size_z=10.67,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      max_volume=360.0,
    ),
  )


class VolumeConversionTests(unittest.TestCase):
  def test_ul_to_tenths(self):
    self.assertEqual(_ul_to_tenths(1.0), 10)
    self.assertEqual(_ul_to_tenths(50.0), 500)
    self.assertEqual(_ul_to_tenths(0.1), 1)
    self.assertEqual(_ul_to_tenths(10000.0), 100000)

  def test_ul_to_tenths_rounding(self):
    self.assertEqual(_ul_to_tenths(1.06), 11)
    self.assertEqual(_ul_to_tenths(1.04), 10)


class DispenseTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = _make_backend()
    self.plate = _make_plate()

  async def test_dispense_bare(self):
    await self.backend.dispense(plate=self.plate, volumes={1: 10.0})
    calls = [c[0][0] for c in self.backend._driver.send_command.call_args_list]  # type: ignore[attr-defined]
    self.assertEqual(calls, ["SCV 1 100", "DIS"])

  async def test_dispense_per_column(self):
    await self.backend.dispense(plate=self.plate, volumes={1: 10.0, 3: 20.0})
    calls = [c[0][0] for c in self.backend._driver.send_command.call_args_list]  # type: ignore[attr-defined]
    self.assertEqual(calls, ["SCV 1 100", "SCV 3 200", "DIS"])

  async def test_dispense_with_all_params(self):
    params = MultidropCombiPeristalticDispensingBackend.DispenseParams(
      plate_type=3,
      cassette_type=0,
      pump_speed=75,
      dispensing_height=2500,
      dispensing_order=DispensingOrder.COLUMN_WISE,
    )
    await self.backend.dispense(plate=self.plate, volumes={1: 50.0}, backend_params=params)
    calls = [c[0][0] for c in self.backend._driver.send_command.call_args_list]  # type: ignore[attr-defined]
    # Order: plate_type → cassette_type → pump_speed → dispensing_height → dispensing_order → volumes → DIS
    self.assertEqual(calls, ["SPL 3", "SCT 0", "SPS 75", "SDH 2500", "SDO 1", "SCV 1 500", "DIS"])

  async def test_dispense_order(self):
    params = MultidropCombiPeristalticDispensingBackend.DispenseParams(
      dispensing_order=DispensingOrder.ROW_WISE,
    )
    await self.backend.dispense(plate=self.plate, volumes={1: 10.0}, backend_params=params)
    calls = [c[0][0] for c in self.backend._driver.send_command.call_args_list]  # type: ignore[attr-defined]
    self.assertEqual(calls, ["SDO 0", "SCV 1 100", "DIS"])


class PrimeTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = _make_backend()
    self.plate = _make_plate()

  async def test_prime_standard(self):
    await self.backend.prime(plate=self.plate, volume=50.0)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "PRI 500")

  async def test_prime_continuous(self):
    params = MultidropCombiPeristalticDispensingBackend.PrimeParams(mode=PrimeMode.CONTINUOUS)
    await self.backend.prime(plate=self.plate, volume=50.0, backend_params=params)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "PRI 500 1")

  async def test_prime_duration_not_supported(self):
    with self.assertRaises(ValueError):
      await self.backend.prime(plate=self.plate, duration=10)

  async def test_prime_volume_required(self):
    with self.assertRaises(ValueError):
      await self.backend.prime(plate=self.plate)


class PurgeTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = _make_backend()
    self.plate = _make_plate()

  async def test_purge_standard(self):
    await self.backend.purge(plate=self.plate, volume=100.0)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "EMP 1000")

  async def test_purge_continuous(self):
    params = MultidropCombiPeristalticDispensingBackend.PurgeParams(mode=EmptyMode.CONTINUOUS)
    await self.backend.purge(plate=self.plate, volume=100.0, backend_params=params)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "EMP 1000 1")

  async def test_purge_duration_not_supported(self):
    with self.assertRaises(ValueError):
      await self.backend.purge(plate=self.plate, duration=10)

  async def test_purge_volume_required(self):
    with self.assertRaises(ValueError):
      await self.backend.purge(plate=self.plate)


class ShakeTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = _make_backend()

  async def test_shake(self):
    await self.backend.shake(time=5.0, distance=3, speed=10)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "SHA 500 3 10")


class DeviceSpecificTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = _make_backend()

  async def test_move_plate_out(self):
    await self.backend.move_plate_out()
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "POU")

  async def test_set_cassette_type(self):
    await self.backend.set_cassette_type(cassette_type=1)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "SCT 1")

  async def test_abort(self):
    await self.backend.abort()
    self.backend._driver.send_abort_signal.assert_awaited_once()  # type: ignore[attr-defined]

  async def test_set_dispense_offset(self):
    await self.backend.set_dispense_offset(x_offset=100, y_offset=-50)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "SOF 100 -50")

  async def test_set_predispense_volume(self):
    await self.backend.set_predispense_volume(volume=10.0)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "SPV 100")

  async def test_define_plate(self):
    await self.backend.define_plate(
      column_positions=12,
      row_positions=8,
      rows=8,
      columns=12,
      height=1420,
      max_volume=360.0,
    )
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "PLA 12 8 8 12 1420 3600 0 0")

  async def test_define_plate_with_offsets(self):
    await self.backend.define_plate(
      column_positions=12,
      row_positions=8,
      rows=8,
      columns=12,
      height=1420,
      max_volume=360.0,
      x_offset=100,
      y_offset=-50,
    )
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "PLA 12 8 8 12 1420 3600 100 -50")

  async def test_start_protocol_bare(self):
    await self.backend.start_protocol()
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "BGN")

  async def test_start_protocol_with_plate_type(self):
    await self.backend.start_protocol(plate_type=3)
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "BGN 3")

  async def test_start_protocol_with_name(self):
    await self.backend.start_protocol(plate_type=3, protocol_name="MyProtocol")
    args = self.backend._driver.send_command.call_args  # type: ignore[attr-defined]
    self.assertEqual(args[0][0], "BGN 3 MyProtocol")


class ParameterValidationTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = _make_backend()
    self.plate = _make_plate()

  async def test_prime_volume_too_low(self):
    with self.assertRaises(ValueError):
      await self.backend.prime(plate=self.plate, volume=0.0)

  async def test_prime_volume_too_high(self):
    with self.assertRaises(ValueError):
      await self.backend.prime(plate=self.plate, volume=20000.0)

  async def test_purge_volume_too_low(self):
    with self.assertRaises(ValueError):
      await self.backend.purge(plate=self.plate, volume=0.0)

  async def test_shake_distance_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.shake(time=5.0, distance=0, speed=10)
    with self.assertRaises(ValueError):
      await self.backend.shake(time=5.0, distance=6, speed=10)

  async def test_shake_speed_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.shake(time=5.0, distance=3, speed=0)
    with self.assertRaises(ValueError):
      await self.backend.shake(time=5.0, distance=3, speed=21)

  async def test_dispense_plate_type_out_of_range(self):
    params = MultidropCombiPeristalticDispensingBackend.DispenseParams(plate_type=-1)
    with self.assertRaises(ValueError):
      await self.backend.dispense(plate=self.plate, volumes={1: 10.0}, backend_params=params)
    params = MultidropCombiPeristalticDispensingBackend.DispenseParams(plate_type=30)
    with self.assertRaises(ValueError):
      await self.backend.dispense(plate=self.plate, volumes={1: 10.0}, backend_params=params)

  async def test_dispense_column_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.dispense(plate=self.plate, volumes={0: 10.0})
    with self.assertRaises(ValueError):
      await self.backend.dispense(plate=self.plate, volumes={49: 10.0})

  async def test_dispense_height_out_of_range(self):
    params = MultidropCombiPeristalticDispensingBackend.DispenseParams(dispensing_height=499)
    with self.assertRaises(ValueError):
      await self.backend.dispense(plate=self.plate, volumes={1: 10.0}, backend_params=params)

  async def test_dispense_pump_speed_out_of_range(self):
    params = MultidropCombiPeristalticDispensingBackend.DispenseParams(pump_speed=0)
    with self.assertRaises(ValueError):
      await self.backend.dispense(plate=self.plate, volumes={1: 10.0}, backend_params=params)

  async def test_cassette_type_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.set_cassette_type(cassette_type=4)

  async def test_dispense_offset_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.set_dispense_offset(x_offset=301, y_offset=0)
    with self.assertRaises(ValueError):
      await self.backend.set_dispense_offset(x_offset=0, y_offset=-301)

  async def test_purge_volume_too_high(self):
    with self.assertRaises(ValueError):
      await self.backend.purge(plate=self.plate, volume=20000.0)

  async def test_predispense_volume_too_low(self):
    with self.assertRaises(ValueError):
      await self.backend.set_predispense_volume(volume=0.0)

  async def test_predispense_volume_too_high(self):
    with self.assertRaises(ValueError):
      await self.backend.set_predispense_volume(volume=20000.0)

  async def test_on_setup_calls_acknowledge_error(self):
    await self.backend._on_setup()
    self.backend._driver.acknowledge_error.assert_awaited_once()  # type: ignore[attr-defined]
