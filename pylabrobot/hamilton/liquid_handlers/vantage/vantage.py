"""Vantage device: wires VantageDriver backends to PIP/Head96/IPG capability frontends."""

from typing import Optional

from pylabrobot.capabilities.arms.orientable_arm import OrientableArm
from pylabrobot.capabilities.liquid_handling.head96 import Head96
from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources.hamilton.vantage_decks import VantageDeck

from .chatterbox import VantageChatterboxDriver
from .driver import VantageDriver


class Vantage(Device):
  """Hamilton Vantage liquid handler.

  User-facing device that wires capability frontends (:class:`PIP`, :class:`Head96`,
  :class:`OrientableArm`) to the :class:`VantageDriver`'s backends after hardware
  discovery during :meth:`setup`.

  Usage::

    from pylabrobot.resources.hamilton.vantage_decks import VantageDeck
    from pylabrobot.hamilton.liquid_handlers.vantage import Vantage

    deck = VantageDeck()
    vantage = Vantage(deck=deck)
    await vantage.setup()

    # Use PIP channels:
    await vantage.pip.pick_up_tips(...)

    # When done:
    await vantage.stop()

  For testing without hardware, pass ``chatterbox=True``::

    vantage = Vantage(deck=deck, chatterbox=True)
    await vantage.setup()  # no USB connection needed
  """

  def __init__(self, deck: VantageDeck, chatterbox: bool = False):
    """Initialize the Vantage device.

    Args:
      deck: The deck definition describing the physical layout of the Vantage.
      chatterbox: If True, use the :class:`VantageChatterboxDriver` (mock driver)
        instead of the real :class:`VantageDriver`. Useful for testing, debugging,
        and development without a physical instrument.
    """
    driver = VantageChatterboxDriver() if chatterbox else VantageDriver()
    super().__init__(driver=driver)
    self.driver: VantageDriver = driver
    self.deck = deck
    self.pip: PIP  # set in setup()
    self.head96: Optional[Head96] = None  # set in setup() if installed
    self.ipg: Optional[OrientableArm] = None  # set in setup() if installed

  async def setup(self):
    """Initialize the Vantage hardware and wire up capability frontends.

    Calls :meth:`VantageDriver.setup` to discover and initialize hardware, then
    creates PIP, Head96, and IPG frontend capabilities as appropriate.
    """
    await self.driver.setup()

    # PIP is always present.
    assert self.driver.pip is not None
    self.pip = PIP(backend=self.driver.pip)
    self._capabilities = [self.pip]

    # Head96 only if the hardware has a 96-head installed.
    if self.driver.head96 is not None:
      self.head96 = Head96(backend=self.driver.head96)
      self._capabilities.append(self.head96)

    # IPG only if installed.
    if self.driver.ipg is not None:
      self.ipg = OrientableArm(backend=self.driver.ipg, reference_resource=self.deck)
      self._capabilities.append(self.ipg)

    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True

  async def stop(self):
    """Stop the Vantage device and tear down all capabilities.

    Stops all capability frontends in reverse order, then stops the driver.
    Safe to call if setup was never completed (returns immediately).
    """
    if not self._setup_finished:
      return
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self.driver.stop()
    self._setup_finished = False
    self.head96 = None
    self.ipg = None
