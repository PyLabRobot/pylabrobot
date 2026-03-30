"""Vantage device: wires VantageDriver backends to PIP/Head96/IPG/LED capability frontends."""

import asyncio
import random
from typing import Optional

from pylabrobot.arms.orientable_arm import OrientableArm
from pylabrobot.capabilities.led_control import LEDControlCapability
from pylabrobot.capabilities.liquid_handling.head96 import Head96Capability
from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources.hamilton import HamiltonDeck

from .chatterbox import VantageChatterboxDriver
from .driver import VantageDriver
from .led_backend import VantageLEDParams


class Vantage(Device):
  """Hamilton Vantage liquid handler.

  User-facing device that wires capability frontends (PIP, Head96, IPG, LED) to the
  VantageDriver's backends after hardware discovery during setup().
  """

  def __init__(
    self,
    deck: HamiltonDeck,
    chatterbox: bool = False,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
    driver = VantageChatterboxDriver() if chatterbox else VantageDriver()
    super().__init__(driver=driver)
    self._driver: VantageDriver = driver
    self.deck = deck
    self._skip_loading_cover = skip_loading_cover
    self._skip_core96 = skip_core96
    self._skip_ipg = skip_ipg
    self.pip: PIP  # set in setup()
    self.head96: Optional[Head96Capability] = None  # set in setup() if installed
    self.iswap: Optional[OrientableArm] = None  # set in setup() if installed (IPG)
    self.led: LEDControlCapability  # set in setup()

  async def setup(self):
    await self._driver.setup(
      skip_loading_cover=self._skip_loading_cover,
      skip_core96=self._skip_core96,
      skip_ipg=self._skip_ipg,
    )

    # PIP is always present.
    self.pip = PIP(backend=self._driver.pip)
    self._capabilities = [self.pip]

    # Head96 only if installed.
    if self._driver.head96 is not None:
      self.head96 = Head96Capability(backend=self._driver.head96)
      self._capabilities.append(self.head96)

    # IPG only if installed.
    if self._driver.ipg is not None:
      self.iswap = OrientableArm(backend=self._driver.ipg, reference_resource=self.deck)
      self._capabilities.append(self.iswap)

    # LED is always present.
    self.led = LEDControlCapability(backend=self._driver.led)
    self._capabilities.append(self.led)

    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True

  async def stop(self):
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self._driver.stop()
    self._setup_finished = False
    self.head96 = None
    self.iswap = None

  async def russian_roulette(self):
    """Dangerous easter egg."""
    sure = input(
      "Are you sure you want to play Russian Roulette? This will turn on the uv-light "
      "with a probability of 1/6. (yes/no) "
    )
    if sure.lower() != "yes":
      print("boring")
      return

    if random.randint(1, 6) == 6:
      await self.led.set_color(
        "on", intensity=100, white=100, red=100, green=0, blue=0,
        backend_params=VantageLEDParams(uv=100),
      )
      print("You lost.")
    else:
      await self.led.set_color("on", intensity=100, white=100, red=0, green=100, blue=0)
      print("You won.")

    await asyncio.sleep(5)
    await self.led.set_color("on", intensity=100, white=100, red=100, green=100, blue=100)
