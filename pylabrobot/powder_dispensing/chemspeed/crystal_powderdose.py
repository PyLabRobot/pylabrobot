from pylabrobot.powder_dispensing.backend import PowderDispenserBackend

class CrystalPowderdose(PowderDispenserBackend):
  """ A powder dispenser backend for Chemspeed Crystal Powderdose. """

  def __init__(self, arksuite_adress: str) -> None:
    self.arksuite_adress = arksuite_adress

  async def setup(self) -> None:
    raise NotImplementedError("CrystalPowderdose not implemented yet")

  async def stop(self) -> None:
    raise NotImplementedError("CrystalPowderdose not implemented yet")

  def serialize(self) -> dict:
    return {**super().serialize(), "arksuite_adress": self.arksuite_adress}

