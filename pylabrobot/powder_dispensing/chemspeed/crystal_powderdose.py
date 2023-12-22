from pylabrobot.powder_dispensing.backend import PowderDispenserBackend

class CrystalPowderdose(PowderDispenserBackend):
  """ A powder dispenser backend for Chemspeed Crystal Powderdose. """

  def __init__(self, arksuite_adress: str) -> None:
    self.arksuite_adress = arksuite_adress

