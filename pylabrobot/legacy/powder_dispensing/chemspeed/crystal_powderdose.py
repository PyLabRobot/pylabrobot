from pylabrobot.legacy.powder_dispensing.backend import (
  PowderDispenserBackend,
)


class CrystalPowderdose(PowderDispenserBackend):
  """A powder dispenser backend for Chemspeed Crystal Powderdose."""

  def __init__(self, arksuite_address: str) -> None:
    self.arksuite_address = arksuite_address

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "arksuite_address": self.arksuite_address,
    }
