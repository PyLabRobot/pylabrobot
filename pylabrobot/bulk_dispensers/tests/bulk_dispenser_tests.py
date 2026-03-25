import unittest

from pylabrobot.bulk_dispensers import (
  BulkDispenser,
  BulkDispenserBackend,
  BulkDispenserChatterboxBackend,
)


class BulkDispenserSetupTests(unittest.IsolatedAsyncioTestCase):
  """Test setup/stop lifecycle and need_setup_finished guard."""

  def setUp(self):
    self.backend = BulkDispenserChatterboxBackend()
    self.dispenser = BulkDispenser(backend=self.backend)

  async def test_methods_fail_before_setup(self):
    with self.assertRaises(RuntimeError):
      await self.dispenser.dispense()
    with self.assertRaises(RuntimeError):
      await self.dispenser.prime(volume=100.0)
    with self.assertRaises(RuntimeError):
      await self.dispenser.abort()

  async def test_setup_and_stop(self):
    await self.dispenser.setup()
    self.assertTrue(self.dispenser.setup_finished)
    await self.dispenser.stop()
    self.assertFalse(self.dispenser.setup_finished)

  async def test_context_manager(self):
    async with BulkDispenser(backend=BulkDispenserChatterboxBackend()) as d:
      self.assertTrue(d.setup_finished)
    self.assertFalse(d.setup_finished)


class BulkDispenserDelegationTests(unittest.IsolatedAsyncioTestCase):
  """Test that frontend methods delegate to the backend."""

  async def asyncSetUp(self):
    self.backend = unittest.mock.MagicMock(spec=BulkDispenserBackend)
    self.dispenser = BulkDispenser(backend=self.backend)
    self.dispenser._setup_finished = True

  async def test_dispense(self):
    await self.dispenser.dispense()
    self.backend.dispense.assert_awaited_once()

  async def test_prime(self):
    await self.dispenser.prime(volume=50.0)
    self.backend.prime.assert_awaited_once_with(volume=50.0)

  async def test_empty(self):
    await self.dispenser.empty(volume=100.0)
    self.backend.empty.assert_awaited_once_with(volume=100.0)

  async def test_shake(self):
    await self.dispenser.shake(time=5.0, distance=3, speed=10)
    self.backend.shake.assert_awaited_once_with(time=5.0, distance=3, speed=10)

  async def test_move_plate_out(self):
    await self.dispenser.move_plate_out()
    self.backend.move_plate_out.assert_awaited_once()

  async def test_set_plate_type(self):
    await self.dispenser.set_plate_type(plate_type=3)
    self.backend.set_plate_type.assert_awaited_once_with(plate_type=3)

  async def test_set_cassette_type(self):
    await self.dispenser.set_cassette_type(cassette_type=1)
    self.backend.set_cassette_type.assert_awaited_once_with(cassette_type=1)

  async def test_set_column_volume(self):
    await self.dispenser.set_column_volume(column=0, volume=25.0)
    self.backend.set_column_volume.assert_awaited_once_with(column=0, volume=25.0)

  async def test_set_dispensing_height(self):
    await self.dispenser.set_dispensing_height(height=2500)
    self.backend.set_dispensing_height.assert_awaited_once_with(height=2500)

  async def test_set_pump_speed(self):
    await self.dispenser.set_pump_speed(speed=50)
    self.backend.set_pump_speed.assert_awaited_once_with(speed=50)

  async def test_set_dispensing_order(self):
    await self.dispenser.set_dispensing_order(order=1)
    self.backend.set_dispensing_order.assert_awaited_once_with(order=1)

  async def test_abort(self):
    await self.dispenser.abort()
    self.backend.abort.assert_awaited_once()
