import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.commands import (
  MultidropCombiCommandsMixin,
  _ul_to_tenths,
)
from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.enums import PrimeMode


class MockCommandsBackend(MultidropCombiCommandsMixin):
  """Testable class with _send_command mocked."""

  def __init__(self):
    self._send_command = AsyncMock(return_value=[])


class VolumeConversionTests(unittest.TestCase):
  def test_ul_to_tenths(self):
    self.assertEqual(_ul_to_tenths(1.0), 10)
    self.assertEqual(_ul_to_tenths(50.0), 500)
    self.assertEqual(_ul_to_tenths(0.1), 1)
    self.assertEqual(_ul_to_tenths(10000.0), 100000)

  def test_ul_to_tenths_rounding(self):
    self.assertEqual(_ul_to_tenths(1.06), 11)
    self.assertEqual(_ul_to_tenths(1.04), 10)


class CommandFormattingTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = MockCommandsBackend()

  async def test_dispense(self):
    await self.backend.dispense()
    self.backend._send_command.assert_awaited_once()
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "DIS")

  async def test_prime_standard(self):
    await self.backend.prime(volume=50.0)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "PRI 500")

  async def test_prime_continuous(self):
    await self.backend.prime(volume=50.0, mode=PrimeMode.CONTINUOUS)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "PRI 500 1")

  async def test_empty(self):
    await self.backend.empty(volume=100.0)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "EMP 1000")

  async def test_shake(self):
    await self.backend.shake(time=5.0, distance=3, speed=10)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SHA 500 3 10")

  async def test_move_plate_out(self):
    await self.backend.move_plate_out()
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "POU")

  async def test_set_plate_type(self):
    await self.backend.set_plate_type(plate_type=3)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SPL 3")

  async def test_set_cassette_type(self):
    await self.backend.set_cassette_type(cassette_type=1)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SCT 1")

  async def test_set_column_volume(self):
    await self.backend.set_column_volume(column=0, volume=25.0)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SCV 0 250")

  async def test_set_dispensing_height(self):
    await self.backend.set_dispensing_height(height=2500)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SDH 2500")

  async def test_set_pump_speed(self):
    await self.backend.set_pump_speed(speed=50)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SPS 50")

  async def test_set_dispensing_order(self):
    await self.backend.set_dispensing_order(order=1)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SDO 1")

  async def test_set_dispense_offset(self):
    await self.backend.set_dispense_offset(x_offset=100, y_offset=-50)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SOF 100 -50")

  async def test_set_predispense_volume(self):
    await self.backend.set_predispense_volume(volume=10.0)
    args = self.backend._send_command.call_args
    self.assertEqual(args[0][0], "SPV 100")


class ParameterValidationTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = MockCommandsBackend()

  async def test_prime_volume_too_low(self):
    with self.assertRaises(ValueError):
      await self.backend.prime(volume=0.0)

  async def test_prime_volume_too_high(self):
    with self.assertRaises(ValueError):
      await self.backend.prime(volume=20000.0)

  async def test_empty_volume_too_low(self):
    with self.assertRaises(ValueError):
      await self.backend.empty(volume=0.0)

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

  async def test_plate_type_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.set_plate_type(plate_type=-1)
    with self.assertRaises(ValueError):
      await self.backend.set_plate_type(plate_type=30)

  async def test_cassette_type_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.set_cassette_type(cassette_type=4)

  async def test_column_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.set_column_volume(column=49, volume=10.0)

  async def test_dispensing_height_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.set_dispensing_height(height=499)
    with self.assertRaises(ValueError):
      await self.backend.set_dispensing_height(height=5501)

  async def test_pump_speed_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.set_pump_speed(speed=0)
    with self.assertRaises(ValueError):
      await self.backend.set_pump_speed(speed=101)

  async def test_dispensing_order_invalid(self):
    with self.assertRaises(ValueError):
      await self.backend.set_dispensing_order(order=2)

  async def test_dispense_offset_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.backend.set_dispense_offset(x_offset=301, y_offset=0)
    with self.assertRaises(ValueError):
      await self.backend.set_dispense_offset(x_offset=0, y_offset=-301)
