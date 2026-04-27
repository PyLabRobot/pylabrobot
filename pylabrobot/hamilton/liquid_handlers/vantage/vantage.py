"""Vantage device: wires VantageDriver backends to PIP/Head96/IPG capability frontends."""

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from pylabrobot.capabilities.arms.arm import GripperArm
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

    deck = VantageDeck(size=1.3)
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

  async def setup(
    self,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
    """Initialize the Vantage hardware and wire up capability frontends.

    Calls :meth:`VantageDriver.setup` to discover and initialize hardware, then
    creates PIP, Head96, and IPG frontend capabilities as appropriate.

    Args:
      skip_loading_cover: If True, skip loading cover initialization.
      skip_core96: If True, skip Core 96-head initialization.
      skip_ipg: If True, skip IPG (Integrated Plate Gripper) initialization.
    """
    await self.driver.setup(
      skip_loading_cover=skip_loading_cover,
      skip_core96=skip_core96,
      skip_ipg=skip_ipg,
    )

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

    Tears down each capability frontend that was added during :meth:`setup`
    in reverse order, then stops the driver. Runs unconditionally so that a
    ``setup()`` that raised partway through still releases the USB connection
    and any backends that finished their own ``_on_setup``.
    """
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self.driver.stop()
    self._setup_finished = False
    self.head96 = None
    self.ipg = None

  # -- CoRe grippers ---------------------------------------------------------

  @asynccontextmanager
  async def core_grippers(
    self,
    front_channel: int = 7,
    traversal_height: float = 245.0,
  ) -> AsyncIterator[GripperArm]:
    """Context manager that picks up CoRe gripper tools on enter and returns them on exit.

    Usage::

      async with vantage.core_grippers(front_channel=7) as arm:
        await arm.move_resource(plate, destination)

    Args:
      front_channel: The front (higher-numbered) PIP channel to mount the gripper on.
        The back channel is ``front_channel - 1``. Default 7 (channels 6+7).
      traversal_height: Minimum Z clearance in mm for safe lateral movement. Default 245.0.

    Raises:
      NotImplementedError: Not yet ported from legacy Vantage backend — CoRe gripper tool
        pickup firmware command has not been reverse-engineered.
    """
    raise NotImplementedError(
      "CoRe gripper tool pickup on Vantage has not been reverse-engineered yet. "
      "On the STAR this is C0:ZT; the Vantage equivalent is unknown. "
      "If you figure out the command, please contribute it."
    )
    # Once pickup is implemented, the pattern should be:
    #   1. Park IPG if out (shared X drive)
    #   2. Pick up gripper tools (unknown firmware command)
    #   3. yield GripperArm(VantageCoreGripper(driver), deck, grip_axis="y")
    #   4. In finally: return tools via discard_tool (A1PM:DJ)
    yield  # unreachable, but needed for asynccontextmanager typing
