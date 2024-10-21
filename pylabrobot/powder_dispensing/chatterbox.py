from typing import List

from pylabrobot.powder_dispensing.backend import (
  PowderDispenserBackend,
  PowderDispense,
  DispenseResults
)


class PowderDispenserChatterboxBackend(PowderDispenserBackend):
  """ Chatter box backend for device-free testing. Prints out all operations. """

  async def setup(self) -> None:
    print("Setting up the powder dispenser.")

  async def stop(self) -> None:
    print("Stopping the powder dispenser.")

  async def dispense(
    self,
    dispense_parameters: List[PowderDispense],
    **backend_kwargs
  ) -> List[DispenseResults]:
    print(f"Dispensing {len(dispense_parameters)} powders.")
    return [DispenseResults(actual_amount=dispense.amount) for dispense in dispense_parameters]
