"""Nimbus device: wires NimbusDriver backends to PIP capability frontend."""

from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

from .chatterbox import NimbusChatterboxDriver
from .driver import NimbusDriver


class Nimbus(Device):
  """Hamilton Nimbus liquid handler.

  User-facing device that wires the PIP capability frontend to the
  NimbusDriver's PIP backend after hardware discovery during setup().
  """

  def __init__(
    self,
    deck: NimbusDeck,
    host: str = "192.168.1.1",
    port: int = 2000,
    chatterbox: bool = False,
  ):
    driver: NimbusDriver = NimbusChatterboxDriver() if chatterbox else NimbusDriver(host, port)
    super().__init__(driver=driver)
    self._driver: NimbusDriver = driver
    self._driver.deck = deck
    self.deck = deck
    self.pip: PIP  # set in setup()

  async def setup(self):
    await self._driver.setup()

    self.pip = PIP(backend=self._driver.pip)
    self._capabilities = [self.pip]

    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True

  async def stop(self):
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self._driver.stop()
    self._setup_finished = False
