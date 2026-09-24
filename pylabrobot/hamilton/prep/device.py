"""The Prep: the device, and what it knows about its own deck."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from pylabrobot.hamilton.prep.driver.features.calibration import Calibration
from pylabrobot.hamilton.prep.driver.features.core_grippers import CoreGrippers
from pylabrobot.hamilton.prep.driver.features.head8 import Head8
from pylabrobot.hamilton.prep.driver.features.lights import Lights
from pylabrobot.hamilton.prep.driver.features.method import MethodLifecycle
from pylabrobot.hamilton.prep.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.prep.driver.features.x_arm import XArm
from pylabrobot.hamilton.prep.driver.master import PrepDriver
from pylabrobot.hamilton.prep.driver.simulator import PrepSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import PrepDeck
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)

# How big the device is.
PREP_SIZE_X = 497.0
PREP_SIZE_Y = 575.0
PREP_SIZE_Z = 575.0

# Where the deck sits inside the device, from the device's left front bottom corner to the deck's.
# It also places Prep.glb, so it moves with PREP_FIRST_SLOT_LOCATION.
PREP_DECK_LOCATION = Coordinate(29.20, 88.02, 50.0)


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

  async def setup(
    self,
    smart: bool = True,
    force_initialize: bool = False,
    skip_device_initialization: bool = False,
    default_minimum_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ):
    """Bring the device up.

    Args:
      smart: as `PrepDriver.setup` takes it.
      force_initialize: as `PrepDriver.setup` takes it.
      skip_device_initialization: as `PrepDriver.setup` takes it.
      default_minimum_traverse_height: as `PrepDriver.setup` takes it.
      use_v1_aspirate_dispense: as `PrepDriver.setup` takes it.
    """
    await self.driver.setup(
      smart=smart,
      force_initialize=force_initialize,
      skip_device_initialization=skip_device_initialization,
      default_minimum_traverse_height=default_minimum_traverse_height,
      use_v1_aspirate_dispense=use_v1_aspirate_dispense,
    )

  async def stop(self, skip_raise_to_z_safety: bool = False):
    """Put the device down.

    Args:
      skip_raise_to_z_safety: leave the channels where they stand, instead of raising them to Z safety first.
        The next lateral move will crash a channel that is low.
    """
    await self.driver.stop(skip_raise_to_z_safety=skip_raise_to_z_safety)

  # -- what the device carries ------------------------------------------------------------
  # Read through: they do not exist until setup has run.

  @property
  def x_arm(self) -> Optional[XArm]:
    """The X-arm the pipetting channels ride."""
    return self.driver.x_arm

  @property
  def pipettes(self) -> Optional[Pipettes]:
    """The pipetting channels."""
    return self.driver.pipettes

  @property
  def head8(self) -> Optional[Head8]:
    """The 8-channel head, on a device that has one."""
    return self.driver.head8

  @property
  def core_grippers(self) -> Optional[CoreGrippers]:
    """The CoRe gripper."""
    return self.driver.core_grippers

  @property
  def lights(self) -> Optional[Lights]:
    """The deck light, on a device that has one."""
    return self.driver.lights

  @property
  def method(self) -> Optional[MethodLifecycle]:
    """The method lifecycle."""
    return self.driver.method

  @property
  def calibration(self) -> Optional[Calibration]:
    """Calibration."""
    return self.driver.calibration

  # -- error lighting --------------------------------------------------------

  async def signal_error(self, duration: Optional[float] = 8.0, wait: bool = False) -> None:
    """Pulse the deck red, if this device has a light to say it with. Never raises.

    Args:
      duration: how many seconds to pulse for, or None to leave the deck pulsing after the error.
      wait: hold until the pulse is over, for a script that exits the moment it raises.
    """
    await self.driver.signal_error(duration=duration, wait=wait)

  @asynccontextmanager
  async def error_lighting(
    self, duration: Optional[float] = 8.0, wait: bool = False
  ) -> AsyncIterator[None]:
    """Pulse the deck red when something inside raises, and let it raise on.

    Args:
      duration: how many seconds to pulse for, or None to leave the deck pulsing after the error,
        until the light is turned off or set to something else.
      wait: as `signal_error`.
    """
    async with self.driver.error_lighting(duration=duration, wait=wait):
      yield

  # -- session ---------------------------------------------------------------

  def __str__(self) -> str:
    return f"{self.name}({self.driver.__class__.__name__})"


# # # # Complete Prep Device Factory Function, for convenience in building a configuration.


def Prep(
  deck: Optional[PrepDeck] = None,
  simulation: bool = False,
  host: Optional[str] = None,
  port: int = 2000,
  declared_configuration_json: Optional[str] = None,
  firmware_tree_json: Optional[str] = None,
  driver: Optional[PrepDriver] = None,
  name: str = "Prep",
  size_x: float = PREP_SIZE_X,
  size_y: float = PREP_SIZE_Y,
  size_z: float = PREP_SIZE_Z,
  simulate_motion_time: bool = False,
  motion_time_scale: float = 0.25,
  use_two_sessions: bool = True,
) -> PrepDevice:
  """A Prep, on a Prep deck.

  Args:
    deck: the deck. If omitted, builds a deck and components prefixed with `name`.
      A supplied deck keeps its existing resource names.
    simulation: whether to build a simulated device, which answers without one being connected.
    host: the address the Prep answers on. Required unless simulating or given a driver.
    port: the port it answers on.
    declared_configuration_json: path to a declared configuration, passed to the driver this builds.
      Read only when this builds one: a driver given outright brings its own. A simulated device
      defaults to PRPAA1087's recording.
    firmware_tree_json: path to a recorded firmware tree, for a simulated device to have. Defaults to
      MLPrep Runtime V1.2.2's.
    driver: the driver to drive it through, instead of building one.
    name: what to call it.
    size_x: how wide it is, in mm.
    size_y: how deep it is, in mm.
    size_z: how tall it is, in mm.
    simulate_motion_time: for a simulated device, whether each move takes time at all, so a viewer
      shows every step. Ignored for a real device.
    motion_time_scale: the share of the real device's time a simulated move then takes, a quarter
      by default. 1.0 keeps the real time. Ignored for a real device.
    use_two_sessions: for a real device, whether its driver opens a second connection, so each
      channel node can be sent a command beside the other's. Ignored when simulating or given a
      driver.

  Returns:
    The device, on a Prep deck.
  """
  if deck is None:
    deck = PrepDeck(name=f"{name}_deck", name_prefix=name)
  if driver is None:
    if simulation:
      driver = PrepSimulationDriver(
        deck=deck,
        declared_configuration_json=declared_configuration_json,
        firmware_tree_json=firmware_tree_json,
        simulate_motion_time=simulate_motion_time,
        motion_time_scale=motion_time_scale,
      )
    else:
      driver = PrepDriver(
        deck=deck,
        host=host,
        port=port,
        declared_configuration_json=declared_configuration_json,
        use_two_sessions=use_two_sessions,
      )
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
