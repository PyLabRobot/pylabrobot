import unittest
from unittest.mock import AsyncMock
from pylabrobot.powder_dispensing.powder_dispenser import PowderDispenser
from pylabrobot.powder_dispensing.backend import PowderDispenserBackend, PowderDispense, DispenseResults
from pylabrobot.resources import Powder, Cos_96_DW_1mL
from typing import List

class MockPowderDispenserBackend(PowderDispenserBackend):
  """ A mock backend for testing. """
  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def dispense(
    self,
    dispense_parameters: List[PowderDispense],
    **backend_kwargs: None) -> List[DispenseResults]:

    results = []

    for dp in dispense_parameters:
      results.append(DispenseResults(actual_amount=dp.amount))

    return results


class TestPowderDispenser(unittest.IsolatedAsyncioTestCase):
  """
  Test class for PowderDispenser.
  """

  async def asyncSetUp(self) -> None:
    self.backend = AsyncMock(spec=MockPowderDispenserBackend)
    self.dispenser = PowderDispenser(name="pd", backend=self.backend, size_x=1, size_y=1, size_z=1)
    await self.dispenser.setup()

  async def test_dispense_single_resource(self):
    plate = Cos_96_DW_1mL(name="test_resource")
    powder = Powder("salt")
    await self.dispenser.dispense(plate["A1"], powder, 0.005)
    self.backend.dispense.assert_called_once()

  async def test_dispense_multiple_resources(self):
    plate = Cos_96_DW_1mL(name="test_resource")
    resources = [plate["A1"], plate["A2"]]
    powders = [Powder("salt"), Powder("salt")]
    amounts = [0.005, 0.010]
    await self.dispenser.dispense(resources, powders, amounts)
    self.assertEqual(self.backend.dispense.call_count, 1)

  async def test_dispense_parameters_handling(self):
    plate = Cos_96_DW_1mL(name="test_resource")
    powder = Powder("salt")
    dispense_parameters = {"param1": "value1"}
    await self.dispenser.dispense(
      plate["A1"], powder, 0.005, dispense_parameters=dispense_parameters
    )
    self.backend.dispense.assert_called_once()

  async def test_assertion_for_mismatched_lengths(self):
    with self.assertRaises(AssertionError):
      plate = Cos_96_DW_1mL(name="test_resource")
      list_of_powders = [Powder("salt"), Powder("salt")]
      await self.dispenser.dispense(plate["A1"], list_of_powders, [0.005])

    with self.assertRaises(AssertionError):
      plate = Cos_96_DW_1mL(name="test_resource")
      await self.dispenser.dispense(
        plate["A1"],
        Powder("salt"),
        [0.005, 0.010],
        dispense_parameters=[{"param": "value"}, {"param": "value"}],
      )


if __name__ == "__main__":
  unittest.main()
