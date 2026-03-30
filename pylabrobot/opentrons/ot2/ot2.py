"""OpentronsOT2 -- Device frontend for the Opentrons OT-2."""

from __future__ import annotations

from typing import List

from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources.opentrons import OTDeck

from .driver import OpentronsOT2Driver
from .pip_backend import OpentronsOT2PIPBackend


class OpentronsOT2(Device):
  """User-facing device for the Opentrons OT-2 liquid handling robot.

  Example::

      ot2 = OpentronsOT2(host="192.168.1.100", deck=OTDeck())
      await ot2.setup()
      await ot2.pip.pick_up_tips(...)
      await ot2.pip.aspirate(...)
      await ot2.home()
      await ot2.stop()
  """

  def __init__(self, host: str, port: int = 31950, deck: OTDeck | None = None):
    driver = OpentronsOT2Driver(host=host, port=port)
    super().__init__(driver=driver)
    self._driver: OpentronsOT2Driver = driver

    self._pip_backend = OpentronsOT2PIPBackend(driver)
    if deck is not None:
      self._pip_backend.set_deck(deck)

    self.pip = PIP(backend=self._pip_backend)
    self._capabilities = [self.pip]

  @property
  def deck(self) -> OTDeck | None:
    return self._pip_backend._deck

  def set_deck(self, deck: OTDeck):
    self._pip_backend.set_deck(deck)

  async def home(self):
    await self._driver.home()

  async def list_connected_modules(self) -> List[dict]:
    return await self._driver.list_connected_modules()

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "host": self._driver.host,
      "port": self._driver.port,
    }

  @classmethod
  def deserialize(cls, data: dict) -> OpentronsOT2:
    data_copy = data.copy()
    data_copy.pop("type", None)
    return cls(**data_copy)
