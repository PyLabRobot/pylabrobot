"""The Vantage: the device, and the factory that builds one."""

import logging
from typing import List, Optional

from pylabrobot.hamilton.transport.text_tcp import DEFAULT_PORT
from pylabrobot.hamilton.vantage.driver.features.ipg import Ipg
from pylabrobot.hamilton.vantage.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.vantage.driver.master import VantageDriver
from pylabrobot.hamilton.vantage.driver.simulator import VantageSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.vantage_decks import VantageDeck
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)


class VantageDevice(Resource):
  """A Hamilton Vantage liquid handler.

  The device is itself a resource and its deck is its child, so everything on the deck is a
  descendant of the device carrying it. The driver, `vantage.driver`, speaks to the machine in its
  own terms and stays reachable whatever is built on top of it.
  """

  def __init__(
    self,
    deck: VantageDeck,
    driver: VantageDriver,
    name: str = "vantage",
    model: Optional[str] = None,
  ):
    """
    Args:
      deck: the deck this device carries. It becomes a child of the device, so everything
        assigned to it is a descendant of this device.
      driver: the driver to drive the machine through.
      name: what to call this device in the resource tree.
      model: which device this is. Defaults to the class name.
    """
    super().__init__(
      name=name,
      size_x=deck.get_absolute_size_x(),
      size_y=deck.get_absolute_size_y(),
      size_z=deck.get_absolute_size_z(),
      category="device",
      model=model if model is not None else self.__class__.__name__,
    )
    self.deck = deck
    self.driver = driver
    if driver.deck is not None and driver.deck is not deck:
      logger.warning("the driver was given another deck; modelling into this device's instead")
    driver.deck = deck
    self.assign_child_resource(deck, location=Coordinate(0, 0, 0))

  # -- session ---------------------------------------------------------------

  async def connect(self) -> None:
    """Open the link to the machine, which moves nothing."""
    await self.driver.connect()

  async def discover(self) -> List[bool]:
    """Read what the machine has, which moves nothing.

    Returns:
      Which channels carry a tip, from the first.
    """
    return await self.driver.discover()

  async def setup(self) -> None:
    """Bring the machine up. Connects, discovers what it has, and initializes it.

    This moves the machine: the arm is pre-initialized if it reports itself down, then the IPG, if
    fitted, is initialized and parked, and then the channels are initialized if they report
    themselves down or carry a tip, which they discard.
    """
    await self.driver.setup()

  async def stop(self) -> None:
    """Close the link. The machine keeps its state."""
    await self.driver.stop()

  # -- what the machine has --------------------------------------------------

  @property
  def pipettes(self) -> Optional[Pipettes]:
    """The pipetting channels, once the machine has been discovered."""
    return self.driver.pipettes

  @property
  def ipg(self) -> Optional[Ipg]:
    """The internal plate gripper, on a machine that has one and has been discovered."""
    return self.driver.ipg

  def __str__(self) -> str:
    return f"{self.name}({self.driver.__class__.__name__}, {self.deck.num_tracks}-track deck)"


def Vantage(
  deck: Optional[VantageDeck] = None,
  simulation: bool = False,
  host: Optional[str] = None,
  port: int = DEFAULT_PORT,
  driver: Optional[VantageDriver] = None,
  name: str = "vantage",
) -> VantageDevice:
  """A Vantage with a 1.3 m deck.

  A machine is reached over USB by default, and over TCP with `host`. One of several on USB is
  chosen by passing `driver=VantageDriver(serial_number=...)`. The default deck and its resources
  take their names from `name`. A supplied deck keeps its existing resource names.

  Args:
    deck: the deck to carry. A 1.3 m `VantageDeck` by default.
    simulation: whether to build a simulated machine, which answers without one being plugged in.
    host: the address of the machine, to reach it over TCP.
    port: the TCP port, with `host`.
    driver: the driver to drive the machine through. Takes precedence over `simulation`, `host`
      and `port`.
    name: what to call the device in the resource tree.

  Raises:
    ValueError: If `host` is combined with `simulation` or `driver`: a machine is reached one way.
  """
  if host is not None and simulation:
    raise ValueError(
      "`host` reaches a machine over TCP, so it cannot be combined with `simulation`"
    )
  if host is not None and driver is not None:
    raise ValueError("`host` builds the driver, so it cannot be combined with `driver`")
  if driver is not None and simulation:
    logger.warning("both a driver and simulation given; driving the driver")

  if deck is None:
    deck = VantageDeck(size=1.3, name=f"{name}_deck")
  if driver is None:
    driver = (
      VantageSimulationDriver(deck=deck)
      if simulation
      else VantageDriver(host=host, port=port, deck=deck)
    )
  return VantageDevice(deck=deck, driver=driver, name=name, model=Vantage.__name__)
