from typing import List
from unittest.mock import AsyncMock

from pylabrobot.powder_dispensing.backend import (
  DispenseResults,
  PowderDispense,
  PowderDispenserBackend,
)
from pylabrobot.powder_dispensing.powder_dispenser import (
  PowderDispenser,
)
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb, Powder
from pylabrobot.testing.concurrency import AnyioTestBase


class MockPowderDispenserBackend(PowderDispenserBackend):
  """A mock backend for testing."""

  async def dispense(
    self,
    dispense_parameters: List[PowderDispense],
    **backend_kwargs: None,
  ) -> List[DispenseResults]:
    results = []

    for dp in dispense_parameters:
      results.append(DispenseResults(actual_amount=dp.amount))

    return results


class TestPowderDispenser(AnyioTestBase):
  """
  Test class for PowderDispenser.
  """

  async def _enter_lifespan(self, stack):
    await super()._enter_lifespan(stack)
    self.backend = AsyncMock(spec=MockPowderDispenserBackend)
    self.dispenser = PowderDispenser(backend=self.backend)
    await stack.enter_async_context(self.dispenser)

  async def test_dispense_single_resource(self):
    plate = Cor_96_wellplate_360ul_Fb(name="test_resource")
    powder = Powder("salt")
    await self.dispenser.dispense(plate["A1"], powder, 0.005)
    self.backend.dispense.assert_called_once()

  async def test_dispense_multiple_resources(self):
    plate = Cor_96_wellplate_360ul_Fb(name="test_resource")
    resources = plate["A1"] + plate["A2"]
    powders = [Powder("salt"), Powder("salt")]
    amounts = [0.005, 0.010]
    await self.dispenser.dispense(resources, powders, amounts)
    self.assertEqual(self.backend.dispense.call_count, 1)

  async def test_dispense_parameters_handling(self):
    plate = Cor_96_wellplate_360ul_Fb(name="test_resource")
    powder = Powder("salt")
    dispense_parameters = {"param1": "value1"}
    await self.dispenser.dispense(
      plate["A1"],
      powder,
      0.005,
      dispense_parameters=dispense_parameters,
    )
    self.backend.dispense.assert_called_once()

  async def test_assertion_for_mismatched_lengths(self):
    with self.assertRaises(AssertionError):
      plate = Cor_96_wellplate_360ul_Fb(name="test_resource")
      list_of_powders = [Powder("salt"), Powder("salt")]
      await self.dispenser.dispense(plate["A1"], list_of_powders, [0.005])

    with self.assertRaises(AssertionError):
      plate = Cor_96_wellplate_360ul_Fb(name="test_resource")
      await self.dispenser.dispense(
        plate["A1"],
        Powder("salt"),
        [0.005, 0.010],
        dispense_parameters=[{"param": "value"}, {"param": "value"}],
      )
