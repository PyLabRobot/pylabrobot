"""The STAR: the device, and what it knows about its own deck."""

import logging
import os
from typing import Optional

from pylabrobot.hamilton.star.driver.features.autoload import Autoload
from pylabrobot.hamilton.star.driver.features.cover import FrontCover
from pylabrobot.hamilton.star.driver.features.head96 import Head96
from pylabrobot.hamilton.star.driver.features.head384 import Head384
from pylabrobot.hamilton.star.driver.features.iswap import iSWAP
from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.star.driver.features.x_arm import XArm
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import (
  HamiltonSTARDeck,
  STARDeck,
  STARLetDeck,
  STARPlusDeck,
)
from pylabrobot.resources.resource import Resource

# Where the recordings this package ships live, and which one stands for each frame. Only ever
# handed to a simulated device: a physical one is whatever it answers, and a declaration given for
# it is cross-checked against it rather than standing in for it. Recording another device is
# saving a file rather than editing code.
_RECORDINGS = os.path.join(os.path.dirname(__file__), "driver", "recordings")

RECORDING_STAR = os.path.join(_RECORDINGS, "star_legacy_2021_8ch_head96_autoload1D.json")
RECORDING_STARLET = os.path.join(_RECORDINGS, "starlet_legacy_2021_8ch_head96_autoload1D.json")
RECORDING_STARPLUS = os.path.join(_RECORDINGS, "starplus_legacy_2021_8ch_head96.json")

# The same two frames fitted with a 384-head instead. No frame function defaults to these: a
# device that has one is declared with it.
RECORDING_STAR_HEAD384 = os.path.join(_RECORDINGS, "star_legacy_2021_8ch_head384_autoload1D.json")
RECORDING_STARLET_HEAD384 = os.path.join(
  _RECORDINGS, "starlet_legacy_2021_8ch_head384_autoload1D.json"
)

logger = logging.getLogger(__name__)

# How big each device is. Measured on the manufacturer's own 3D models of the three frames,
# with the optional front loader left off - see the factory functions below for what that excludes.
# The envelope is the hood: it sets the width, the depth at the back and the full height, with the
# front door setting the depth at the front. Depth and height come out the same on all three frames,
# which is what says the frames differ in width alone.
STAR_SIZE_X = 1_667.0
STARLET_SIZE_X = 1_130.0
STARPLUS_SIZE_X = 2_163.5
# The left extension housing, measured on a CAD model of the part. It bolts to the left of the
# chassis and stands on the same bench, so it is a resource of its own at a negative x rather than
# something that grows the device: growing it would move the device origin, and with it
# everything measured from that origin including the chassis's own geometry. The same reasoning as
# the autoload's loading tray, which stands in front at a negative y.
#
# It is aligned by its TOP and its BACK, not by the bench and the front face: 48.0 mm shorter than
# the device and 6.8 mm shallower, so hanging it flush at the top leaves it clear of the bench,
# and pushing it flush at the back leaves its front inside the device's.
EXTENSION_HOUSING_SIZE = (265.0, 779.0, 855.0)

# The chassis's own left side panel, which the extension housing REPLACES: the housing has no
# device-facing side, so on a device that has one this panel is not there. Mutually exclusive
# with `left_extension_housing`, and the reason both are resources rather than part of the chassis.
# Its y, z and size are the same on all three frames; only its x differs, so each factory passes
# its own.
SIDE_PANEL_SIZE = (4.0, 726.0, 682.0)
SIDE_PANEL_ORIGIN_YZ = (52.8, 180.5)
# One panel, one place. The manufacturer's three files put it at 5.0, 3.5 and 6.0 - a 2.5 mm spread
# on a part whose geometry is identical in all three to the last decimal, so the spread is how each
# file was drawn rather than how the devices differ. This is the median, and the STARlet's, which
# is the frame the rest of this is measured against.
SIDE_PANEL_X = 5.0
# What it used to be, before there was a part to measure: an unsourced 245.0 that only widened the
# device. Kept as a name because the tests measure the housing against it.
EXTENSION_HOUSING_SIZE_X = EXTENSION_HOUSING_SIZE[0]
MANUAL_SIZE_Y = 785.8
SIZE_Z = 903.0
# The chassis stands on feet, and `SIZE_Z` is the whole envelope with them included. This is how
# tall they are, measured off the flat underside of the base plate - one surface, whose area scales
# with the frame's width - and corroborated by where the chassis footprint jumps from the part of
# the width the feet cover to the full width of the body. The same on all three frames.
FEET_SIZE_Z = 43.0
# What stands clear of the bench: the device without what it stands on.
BODY_SIZE_Z = SIZE_Z - FEET_SIZE_Z
# The loading tray stands 221.2 mm proud of the front face - the manufacturer's models are 1007.0
# deep with a loader fitted against 785.8 without, on all three frames. That is NOT added to the
# device here: the tray is its own resource, `autoload_loading_tray`, and a resource in front
# of its parent is exactly what a negative y describes. Adding it would move the device origin
# and with it everything measured from that origin, including the chassis's own geometry.
AUTOLOAD_TRAY_PROUD_Y = 221.2

# How far behind the device's front face the deck resource's origin sits.
#
# A carrier lands 63.0 mm behind that origin and is 497.0 mm long, and the deck ends at a full-width
# cable duct whose front face is 654.8 mm behind the device's front face. That face is not what
# a carrier stops against: it carries a row of stubs, 3.0 mm long, that reach into the back of the
# carrier to locate it, so the carrier seats 3.0 mm further back than the face alone would allow.
# Hence 654.8 - 497.0 - 63.0 + 3.0.
#
# The duct and the deck's own front edge, 181.3 mm behind the front face, read the same in the CAD
# master and in the manufacturer's chassis models, and the same on all three frames - so STAR and
# STARlet share this figure rather than differing as they used to.
#
# What this replaces: 116.0 on a STAR and 106.0 on a STARlet, derived as `795 - 119 - 560` and
# `790 - 124 - 560` from depths that have since been measured. Those drove every carrier 21.2 mm
# and 11.2 mm respectively into the duct.
DECK_ORIGIN_Y = 97.8

# How far right the deck sits from where a 2021 STAR was measured. Two parts the manufacturer draws
# against the rails both land right of where the measured value puts them - the autoload's 31 track
# guides by 6.215 mm each, and the waste block's left face by 8.700 mm - so the rails themselves sit
# further right than 210 mm from the left face. The guides set the figure: there are thirty-one of
# them, they agree to 0.022 mm, and unlike the waste block nothing here has placed them.
DECK_ORIGIN_X_CORRECTION = 6.215

# Where the deck sits inside the device.
#   x  210 mm from the left face to the first carrier, measured on a 2021 STAR, plus the correction
#      above. The measurement and the manufacturer's own geometry disagree by that much and the
#      geometry is what the models are drawn from.
#   y  see `DECK_ORIGIN_Y`
#   z  the deck work surface sits 100 mm above the deck's origin, which the manual states, so the
#      origin is 100 mm below the surface. The surface itself is measured on the manufacturer's
#      models: the deck plate is 2.5 mm thick, its underside on the platform's top plate, and its
#      TOP - which is what a carrier rests on - is 180.5 mm above the device's base. The same
#      on all three frames. What this replaces is an unsourced 78.5, which put the work surface at
#      178.5 and so seated every carrier 2.0 mm inside the plate it stands on.
STAR_DECK_LOCATION = Coordinate(110.0 + DECK_ORIGIN_X_CORRECTION, DECK_ORIGIN_Y, 80.5)
# The STARlet's x is not measured: its chassis leaves the same 119 mm beyond its deck as the STAR's
# does, so it takes the same value until someone measures one.
STARLET_DECK_LOCATION = Coordinate(110.0 + DECK_ORIGIN_X_CORRECTION, DECK_ORIGIN_Y, 80.5)


class STARDevice(Resource):
  """The complete modelling and control interface for a Hamilton Microlab STAR.

  The device is itself a resource and its deck is its child, so everything on the deck is a
  descendant of the device carrying it: one tree, rooted here.

  Two tiers over one driver. `star.driver` speaks to the device in its own terms - tracks,
  positions, millimetres - and stays reachable whatever is built on top of it. The device adds
  what the driver cannot know: where things are, and so which of them a command is about.
  """

  def __init__(
    self,
    deck: HamiltonSTARDeck,
    simulation: bool = False,
    declared_configuration_json: Optional[str] = None,
    driver: Optional[STARDriver] = None,
    name: str = "Generic STAR Device",
    size_x: Optional[float] = None,
    size_y: Optional[float] = None,
    size_z: Optional[float] = None,
    extension_housing: bool = True,
    left_side_panel_installed: bool = False,
    deck_location: Optional[Coordinate] = None,
    model: Optional[str] = None,
  ):
    """
    Args:
      deck: the deck this device carries. It becomes a child of the device, so everything
        assigned to it is a descendant of this device.
      simulation: whether to build a simulated device, which answers without one being plugged
        in. Superseded by `driver`, which says exactly what to drive.
      declared_configuration_json: path to a declared configuration, passed to the driver this
        builds. Read only when this builds one: a driver given outright brings its own.
      driver: the driver to drive the device through.
      name: what to call this device in the resource tree.
      size_x: how wide the device is, in mm, BEFORE any extension housing. Defaults to the
        deck's own width.
      size_y: how deep it is, in mm. Defaults to the deck's own depth.
      size_z: how tall it is, in mm. Defaults to the deck's own height.
      extension_housing: whether the left extension housing is fitted. It becomes a resource of its
        own, `left_extension_housing`, standing to the LEFT of the chassis at a negative x. It does
        NOT change the device's size: see `EXTENSION_HOUSING_SIZE`.
      left_side_panel_installed: whether the chassis's left side panel is on. It becomes a
        resource of its own, `left_side_panel`, at `SIDE_PANEL_X`. Declared rather than
        discovered: the panel bolts off in seconds and the device does not report it. Passed on
        to the driver, which stops an arm short of a fitted one.
      deck_location: where the deck sits inside it, BEFORE any extension housing. Defaults to the
        device's own origin.
      model: which device this is. Defaults to the class name, which says only that it is a STAR.

    Raises:
      ValueError: If neither a driver nor simulation is given, since there is then nothing to
        drive, or if both the extension housing and the left side panel are declared.
    """
    if driver is None and not simulation:
      raise ValueError("pass a driver, or `simulation=True` to build a simulated one")
    if extension_housing and left_side_panel_installed:
      raise ValueError(
        "an device has the left extension housing or the left side panel, not both: the "
        "housing stands where the panel would be"
      )
    if driver is not None and simulation:
      logger.warning("both a driver and simulation given; driving the driver")

    super().__init__(
      name=name,
      size_x=deck.get_absolute_size_x() if size_x is None else size_x,
      size_y=deck.get_absolute_size_y() if size_y is None else size_y,
      size_z=deck.get_absolute_size_z() if size_z is None else size_z,
      category="device",
      model=model if model is not None else self.__class__.__name__,
    )
    self.extension_housing = extension_housing
    self.left_side_panel_installed = left_side_panel_installed

    self.deck = deck
    if driver is not None:
      if declared_configuration_json is not None:
        logger.warning("a driver was given, so it brings its own declared configuration")
      self.driver: STARDriver = driver
    else:
      # The driver is what reads a declaration. Without one, a simulated device works out from its
      # deck what frame it is on, and says what it assumed.
      self.driver = STARSimulationDriver(
        deck=deck, declared_configuration_json=declared_configuration_json
      )

    if self.driver.deck is not None and self.driver.deck is not deck:
      logger.warning("the driver was given another deck; modelling into this device's instead")

    self.driver.deck = deck
    if self.driver.left_side_panel_installed != left_side_panel_installed:
      logger.warning("the driver was told otherwise about the left side panel; taking this one's")
    self.driver.left_side_panel_installed = left_side_panel_installed
    self.assign_child_resource(
      deck, location=deck_location if deck_location is not None else Coordinate(0, 0, 0)
    )

    if left_side_panel_installed:
      self.assign_child_resource(
        Resource(
          name="left_side_panel",
          size_x=SIDE_PANEL_SIZE[0],
          size_y=SIDE_PANEL_SIZE[1],
          size_z=SIDE_PANEL_SIZE[2],
          category="left_side_panel",
          model="hamilton_star_left_side_panel",
        ),
        location=Coordinate(SIDE_PANEL_X, SIDE_PANEL_ORIGIN_YZ[0], SIDE_PANEL_ORIGIN_YZ[1]),
      )

    if extension_housing:
      # To the left, hung so its top and its back are level with the device's.
      self.assign_child_resource(
        Resource(
          name="left_extension_housing",
          size_x=EXTENSION_HOUSING_SIZE[0],
          size_y=EXTENSION_HOUSING_SIZE[1],
          size_z=EXTENSION_HOUSING_SIZE[2],
          category="left_extension_housing",
          model="hamilton_star_left_extension_housing",
        ),
        location=Coordinate(
          -EXTENSION_HOUSING_SIZE[0],
          self.get_absolute_size_y() - EXTENSION_HOUSING_SIZE[1],
          self.get_absolute_size_z() - EXTENSION_HOUSING_SIZE[2],
        ),
      )

  # -- what the device carries ------------------------------------------------------------
  # Read through: the optional ones do not exist until discovery says what is fitted.

  @property
  def left_x_arm(self) -> Optional[XArm]:
    """The left X-arm, on a device that has one."""
    return self.driver.left_x_arm

  @property
  def right_x_arm(self) -> Optional[XArm]:
    """The right X-arm, on a device that has one."""
    return self.driver.right_x_arm

  @property
  def x_arm(self) -> XArm:
    """The X-arm, on a device that has only one.

    Returns:
      The arm, whichever side it is installed on.

    Raises:
      RuntimeError: If setup has not run.
      ValueError: If the device has more than one arm.
    """
    return self.driver.x_arm

  @property
  def pipettes(self) -> Optional[Pipettes]:
    """The pipetting channels, on a device that has some."""
    return self.driver.pipettes

  @property
  def front_cover(self) -> Optional[FrontCover]:
    """The front cover, on a device whose configuration has its monitoring installed."""
    return self.driver.front_cover

  @property
  def head96(self) -> Optional[Head96]:
    """The 96-head, on a device that has one."""
    return self.driver.head96

  @property
  def head384(self) -> Optional[Head384]:
    """The 384-head, on a device that has one."""
    return self.driver.head384

  @property
  def iswap(self) -> Optional[iSWAP]:
    """The iSWAP, on a device that has one."""
    return self.driver.iswap

  @property
  def autoload(self) -> Optional[Autoload]:
    """The autoload, on a device that has one."""
    return self.driver.autoload

  # -- session ---------------------------------------------------------------

  async def setup(
    self,
    skip_device_initialization: bool = False,
    skip_pipettes: bool = False,
    skip_iswap: bool = False,
    skip_head96: bool = False,
    skip_head384: bool = False,
    skip_autoload: bool = False,
  ):
    """Bring the device up.

    Args:
      skip_device_initialization: as `STARDriver.setup` takes it.
      skip_pipettes: as `STARDriver.setup` takes it.
      skip_iswap: as `STARDriver.setup` takes it.
      skip_head96: as `STARDriver.setup` takes it.
      skip_head384: as `STARDriver.setup` takes it.
      skip_autoload: as `STARDriver.setup` takes it.
    """
    await self.driver.setup(
      skip_device_initialization=skip_device_initialization,
      skip_pipettes=skip_pipettes,
      skip_iswap=skip_iswap,
      skip_head96=skip_head96,
      skip_head384=skip_head384,
      skip_autoload=skip_autoload,
    )

  async def stop(self):
    """Put the device down."""
    await self.driver.stop()

  def __str__(self) -> str:
    return f"{self.name}({self.driver.__class__.__name__}, {self.deck.num_tracks}-track deck)"


# # # # Complete STAR Devices Factory Functions, for convenience in building a configuration.


def STAR(
  deck: Optional[HamiltonSTARDeck] = None,
  simulation: bool = False,
  declared_configuration_json: Optional[str] = None,
  driver: Optional[STARDriver] = None,
  name: str = "Hamilton STAR",
  size_x: float = STAR_SIZE_X,
  size_y: float = MANUAL_SIZE_Y,
  size_z: float = SIZE_Z,
  extension_housing: bool = True,
  left_side_panel_installed: bool = False,
) -> STARDevice:
  """A full-size STAR, on a full-size STAR deck."""
  if deck is None:
    deck = STARDeck()
  if simulation and driver is None and declared_configuration_json is None:
    declared_configuration_json = RECORDING_STAR
  if driver is None:
    driver = (
      STARSimulationDriver(deck=deck, declared_configuration_json=declared_configuration_json)
      if simulation
      else STARDriver(deck=deck, declared_configuration_json=declared_configuration_json)
    )
  return STARDevice(
    deck=deck,
    driver=driver,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    extension_housing=extension_housing,
    left_side_panel_installed=left_side_panel_installed,
    deck_location=STAR_DECK_LOCATION,
    model=STAR.__name__,
  )


def STARLet(
  deck: Optional[HamiltonSTARDeck] = None,
  simulation: bool = False,
  declared_configuration_json: Optional[str] = None,
  driver: Optional[STARDriver] = None,
  name: str = "Hamilton STARlet",
  size_x: float = STARLET_SIZE_X,
  size_y: float = MANUAL_SIZE_Y,
  size_z: float = SIZE_Z,
  extension_housing: bool = True,
  left_side_panel_installed: bool = False,
) -> STARDevice:
  """A STARlet, on a STARlet deck."""
  if deck is None:
    deck = STARLetDeck()
  if simulation and driver is None and declared_configuration_json is None:
    declared_configuration_json = RECORDING_STARLET
  if driver is None:
    driver = (
      STARSimulationDriver(deck=deck, declared_configuration_json=declared_configuration_json)
      if simulation
      else STARDriver(deck=deck, declared_configuration_json=declared_configuration_json)
    )
  return STARDevice(
    deck=deck,
    driver=driver,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    extension_housing=extension_housing,
    left_side_panel_installed=left_side_panel_installed,
    deck_location=STARLET_DECK_LOCATION,
    model=STARLet.__name__,
  )


def STARPlus(
  deck: Optional[HamiltonSTARDeck] = None,
  simulation: bool = False,
  declared_configuration_json: Optional[str] = None,
  driver: Optional[STARDriver] = None,
  name: str = "Hamilton STARplus",
  size_x: float = STARPLUS_SIZE_X,
  size_y: float = MANUAL_SIZE_Y,
  size_z: float = SIZE_Z,
  extension_housing: bool = True,
  left_side_panel_installed: bool = False,
) -> STARDevice:
  """A STARplus, on a STARplus deck.

  The width is measured on the manufacturer's own model, as the other two frames are. Its deck is
  derived, as `STARPlusDeck` works through, because there is no
  STARplus here to read one from, so its 78 rails should be confirmed against a real device.

  Returns:
    The device, on a STARplus deck.
  """
  if deck is None:
    deck = STARPlusDeck()
  if simulation and driver is None and declared_configuration_json is None:
    declared_configuration_json = RECORDING_STARPLUS
  if driver is None:
    driver = (
      STARSimulationDriver(deck=deck, declared_configuration_json=declared_configuration_json)
      if simulation
      else STARDriver(deck=deck, declared_configuration_json=declared_configuration_json)
    )
  return STARDevice(
    deck=deck,
    driver=driver,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    extension_housing=extension_housing,
    left_side_panel_installed=left_side_panel_installed,
    deck_location=STAR_DECK_LOCATION,
    model=STARPlus.__name__,
  )
