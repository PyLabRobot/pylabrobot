"""Chatterbox backend for device-free testing of dispensers."""

from typing import List

from pylabrobot.dispensing.backend import DispenserBackend
from pylabrobot.dispensing.standard import DispenseOp


class DispenserChatterboxBackend(DispenserBackend):
  """Chatterbox backend for device-free testing. Prints all operations."""

  async def setup(self) -> None:
    print("Setting up the dispenser.")

  async def stop(self) -> None:
    print("Stopping the dispenser.")

  async def dispense(self, ops: List[DispenseOp], **backend_kwargs) -> None:
    for op in ops:
      chip_str = f" (chip {op.chip})" if op.chip is not None else ""
      print(f"Dispensing {op.volume:.2f} µL into {op.resource.name}{chip_str}")
