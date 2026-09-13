"""The Prep: the device, and what it knows about its own deck."""

import logging
from typing import Optional

from pylabrobot.hamilton.prep.driver.features.calibration import PrepCalibration
from pylabrobot.hamilton.prep.driver.features.channels import PrepChannels
from pylabrobot.hamilton.prep.driver.features.gripper import PrepGripper
from pylabrobot.hamilton.prep.driver.features.head8 import PrepHead8
from pylabrobot.hamilton.prep.driver.features.method import PrepMethodLifecycle
from pylabrobot.hamilton.prep.driver.master import PrepDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import PrepDeck
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)

# How big the device is.
PREP_SIZE_X = 497.0
PREP_SIZE_Y = 575.0
PREP_SIZE_Z = 575.0

# Where the deck sits inside the device, from the device's left front bottom corner to the deck's.
PREP_DECK_LOCATION = Coordinate(25.0, 86.5, 50.0)


class PrepDevice(Resource):
  """The complete modelling and control interface for a Hamilton Prep.

  The device is itself a resource and its deck is its child, so everything on the deck is a
  descendant of the device carrying it: one tree, rooted here.

  Two tiers over one driver. `prep.driver` speaks to the device in its own terms and stays reachable
  whatever is built on top of it. The device adds what the driver cannot know: where things are.
  """

  def __init__(
    self,
    deck: PrepDeck,
    driver: PrepDriver,
    name: str = "Generic Prep Device",
    size_x: Optional[float] = None,
    size_y: Optional[float] = None,
    size_z: Optional[float] = None,
    deck_location: Optional[Coordinate] = None,
    model: Optional[str] = None,
  ):
    """
    Args:
      deck: the deck this device carries. It becomes a child of the device, so everything assigned
        to it is a descendant of this device.
      driver: the driver to drive the device through.
      name: what to call this device in the resource tree.
      size_x: how wide the device is, in mm. Defaults to the deck's own width.
      size_y: how deep it is, in mm. Defaults to the deck's own depth.
      size_z: how tall it is, in mm. Defaults to the deck's own height.
      deck_location: where the deck sits inside it. Defaults to the device's own origin.
      model: which device this is. Defaults to the class name, which says only that it is a Prep.
    """
    super().__init__(
      name=name,
      size_x=deck.get_absolute_size_x() if size_x is None else size_x,
      size_y=deck.get_absolute_size_y() if size_y is None else size_y,
      size_z=deck.get_absolute_size_z() if size_z is None else size_z,
      category="device",
      model=model if model is not None else self.__class__.__name__,
    )

    self.deck = deck
    self.driver = driver
    if self.driver.deck is not deck:
      logger.warning("the driver was given another deck; modelling into this device's instead")
      self.driver.deck = deck
    self.assign_child_resource(
      deck, location=deck_location if deck_location is not None else Coordinate(0, 0, 0)
    )

  # -- what the device carries ------------------------------------------------------------
  # Read through: they do not exist until setup has run.

  @property
  def channels(self) -> Optional[PrepChannels]:
    """The pipetting channels."""
    return self.driver.channels

  @property
  def head8(self) -> Optional[PrepHead8]:
    """The 8-channel head, on a device that has one."""
    return self.driver.head8

  @property
  def gripper(self) -> Optional[PrepGripper]:
    """The CoRe gripper."""
    return self.driver.gripper

  @property
  def method(self) -> Optional[PrepMethodLifecycle]:
    """The method lifecycle."""
    return self.driver.method

  @property
  def calibration(self) -> Optional[PrepCalibration]:
    """Calibration."""
    return self.driver.calibration

  # -- session ---------------------------------------------------------------

  async def setup(
    self,
    smart: bool = True,
    force_initialize: bool = False,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ):
    """Bring the device up.

    Args:
      smart: as `PrepDriver.setup` takes it.
      force_initialize: as `PrepDriver.setup` takes it.
      default_traverse_height: as `PrepDriver.setup` takes it.
      use_v1_aspirate_dispense: as `PrepDriver.setup` takes it.
    """
    await self.driver.setup(
      smart=smart,
      force_initialize=force_initialize,
      default_traverse_height=default_traverse_height,
      use_v1_aspirate_dispense=use_v1_aspirate_dispense,
    )

  async def stop(self):
    """Put the device down."""
    await self.driver.stop()

  def __str__(self) -> str:
    return f"{self.name}({self.driver.__class__.__name__})"


# # # # Complete Prep Device Factory Function, for convenience in building a configuration.


def Prep(
  deck: Optional[PrepDeck] = None,
  simulation: bool = False,
  host: Optional[str] = None,
  port: int = 2000,
  driver: Optional[PrepDriver] = None,
  name: str = "Hamilton Prep",
  size_x: float = PREP_SIZE_X,
  size_y: float = PREP_SIZE_Y,
  size_z: float = PREP_SIZE_Z,
) -> PrepDevice:
  """A Prep, on a Prep deck.

  Args:
    deck: the deck. Defaults to `PrepDeck()`.
    simulation: whether to build a simulated device, which answers without one being connected.
    host: the address the Prep answers on. Required unless simulating or given a driver.
    port: the port it answers on.
    driver: the driver to drive it through, instead of building one.
    name: what to call it.
    size_x: how wide it is, in mm.
    size_y: how deep it is, in mm.
    size_z: how tall it is, in mm.

  Returns:
    The device, on a Prep deck.
  """
  if deck is None:
    deck = PrepDeck()
  if driver is None:
    driver = PrepDriver(deck=deck, chatterbox=simulation, host=host, port=port)
  return PrepDevice(
    deck=deck,
    driver=driver,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    deck_location=PREP_DECK_LOCATION,
    model=Prep.__name__,
  )
