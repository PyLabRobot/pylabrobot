from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.powder_dispensing.backend import (
  PowderDispenserBackend,
)


class CrystalPowderdose(PowderDispenserBackend):
  """A powder dispenser backend for Chemspeed Crystal Powderdose."""

  def __init__(self, arksuite_address: str) -> None:
    self.arksuite_address = arksuite_address

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    await super()._enter_lifespan(stack)
    raise NotImplementedError("CrystalPowderdose not implemented yet")

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "arksuite_address": self.arksuite_address,
    }
