from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.machines.backends.usb import USBBackend

class HamiltonHeatShaker(HeaterShakerBackend, USBBackend):
  """
  Backend for Hamilton Heater Shaker devices connected through
  an HSB
  """

  def __init__(self, id_vendor:int = 2223, id_product:int = 32770) -> None:
    HeaterShakerBackend.__init__(self)
    USBBackend.__init__(self, id_vendor, id_product)

  async def setup(self):
    await USBBackend.setup(self)