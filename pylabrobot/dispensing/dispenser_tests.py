"""Tests for the Dispenser front-end."""

import unittest
from unittest.mock import AsyncMock

from pylabrobot.dispensing.backend import DispenserBackend
from pylabrobot.dispensing.dispenser import Dispenser
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb


class MockDispenserBackend(DispenserBackend):
  """Mock backend for testing."""

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def dispense(self, ops, **backend_kwargs) -> None:
    pass


class TestDispenser(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.backend = AsyncMock(spec=MockDispenserBackend)
    self.dispenser = Dispenser(backend=self.backend)
    await self.dispenser.setup()

  async def asyncTearDown(self) -> None:
    await self.dispenser.stop()

  async def test_dispense_single_well(self):
    plate = Cor_96_wellplate_360ul_Fb(name="test_plate")
    await self.dispenser.dispense(plate["A1"][0], volume=5.0, chip=3)
    self.backend.dispense.assert_called_once()
    ops = self.backend.dispense.call_args[0][0]
    self.assertEqual(len(ops), 1)
    self.assertEqual(ops[0].volume, 5.0)
    self.assertEqual(ops[0].chip, 3)

  async def test_dispense_multiple_wells(self):
    plate = Cor_96_wellplate_360ul_Fb(name="test_plate")
    wells = plate["A1"] + plate["A2"] + plate["B1"]
    await self.dispenser.dispense(wells, volume=10.0)
    self.backend.dispense.assert_called_once()
    ops = self.backend.dispense.call_args[0][0]
    self.assertEqual(len(ops), 3)
    for op in ops:
      self.assertEqual(op.volume, 10.0)
      self.assertIsNone(op.chip)

  async def test_dispense_negative_volume_raises(self):
    plate = Cor_96_wellplate_360ul_Fb(name="test_plate")
    with self.assertRaises(ValueError):
      await self.dispenser.dispense(plate["A1"][0], volume=-1.0)

  async def test_dispense_zero_volume_raises(self):
    plate = Cor_96_wellplate_360ul_Fb(name="test_plate")
    with self.assertRaises(ValueError):
      await self.dispenser.dispense(plate["A1"][0], volume=0.0)

  async def test_dispense_before_setup_raises(self):
    backend = AsyncMock(spec=MockDispenserBackend)
    dispenser = Dispenser(backend=backend)
    plate = Cor_96_wellplate_360ul_Fb(name="test_plate")
    with self.assertRaises(RuntimeError):
      await dispenser.dispense(plate["A1"][0], volume=5.0)

  async def test_backend_kwargs_forwarded(self):
    plate = Cor_96_wellplate_360ul_Fb(name="test_plate")
    await self.dispenser.dispense(plate["A1"][0], volume=5.0, custom_param="value")
    self.backend.dispense.assert_called_once()
    kwargs = self.backend.dispense.call_args[1]
    self.assertEqual(kwargs["custom_param"], "value")


if __name__ == "__main__":
  unittest.main()
