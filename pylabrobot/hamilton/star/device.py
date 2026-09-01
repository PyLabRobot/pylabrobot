"""The STAR: the instrument, and what it knows about its own deck."""

import logging
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

logger = logging.getLogger(__name__)

# How big each instrument is. Measured on the manufacturer's own 3D models of the three frames,
# with the optional front loader left off - see the factory functions below for what that excludes.
# The envelope is the hood: it sets the width, the depth at the back and the full height, with the
# front door setting the depth at the front. Depth and height come out the same on all three frames,
# which is what says the frames differ in width alone.
STAR_SIZE_X = 1_667.0
STARLET_SIZE_X = 1_130.0
STARPLUS_SIZE_X = 2_163.5
# The left extension housing, measured on a CAD model of the part. It bolts to the left of the
# chassis and stands on the same bench, so it is a resource of its own at a negative x rather than
# something that grows the instrument: growing it would move the instrument origin, and with it
# everything measured from that origin including the chassis's own geometry. The same reasoning as
# the autoload's loading tray, which stands in front at a negative y.
#
# It is aligned by its TOP and its BACK, not by the bench and the front face: 48.0 mm shorter than
# the instrument and 6.8 mm shallower, so hanging it flush at the top leaves it clear of the bench,
# and pushing it flush at the back leaves its front inside the instrument's.
EXTENSION_HOUSING_SIZE = (265.0, 779.0, 855.0)

# The chassis's own left side panel, which the extension housing REPLACES: the housing has no
# machine-facing side, so on a machine that has one this panel is not there. Mutually exclusive
# with `left_extension_housing`, and the reason both are resources rather than part of the chassis.
# Its y, z and size are the same on all three frames; only its x differs, so each factory passes
# its own.
SIDE_PANEL_SIZE = (4.0, 726.0, 682.0)
SIDE_PANEL_ORIGIN_YZ = (52.8, 180.5)
# One panel, one place. The manufacturer's three files put it at 5.0, 3.5 and 6.0 - a 2.5 mm spread
# on a part whose geometry is identical in all three to the last decimal, so the spread is how each
# file was drawn rather than how the machines differ. This is the median, and the STARlet's, which
# is the frame the rest of this is measured against.
SIDE_PANEL_X = 5.0
# What it used to be, before there was a part to measure: an unsourced 245.0 that only widened the
# instrument. Kept as a name because `STAR_with_extension_housing` reads it.
EXTENSION_HOUSING_SIZE_X = EXTENSION_HOUSING_SIZE[0]
MANUAL_SIZE_Y = 785.8
SIZE_Z = 903.0
# The chassis stands on feet, and `SIZE_Z` is the whole envelope with them included. This is how
# tall they are, measured off the flat underside of the base plate - one surface, whose area scales
# with the frame's width - and corroborated by where the chassis footprint jumps from the part of
# the width the feet cover to the full width of the body. The same on all three frames.
FEET_SIZE_Z = 43.0
# What stands clear of the bench: the machine without what it stands on.
BODY_SIZE_Z = SIZE_Z - FEET_SIZE_Z
# The loading tray stands 221.2 mm proud of the front face - the manufacturer's models are 1007.0
# deep with a loader fitted against 785.8 without, on all three frames. That is NOT added to the
# instrument here: the tray is its own resource, `autoload_loading_tray`, and a resource in front
# of its parent is exactly what a negative y describes. Adding it would move the instrument origin
# and with it everything measured from that origin, including the chassis's own geometry.
AUTOLOAD_TRAY_PROUD_Y = 221.2

# How far behind the instrument's front face the deck resource's origin sits.
#
# A carrier lands 63.0 mm behind that origin and is 497.0 mm long, and the deck ends at a full-width
# cable duct whose front face is 654.8 mm behind the instrument's front face. That face is not what
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

# Where the deck sits inside the instrument.
#   x  210 mm from the left face to the first carrier, measured on a 2021 STAR, plus the correction
#      above. The measurement and the manufacturer's own geometry disagree by that much and the
#      geometry is what the models are drawn from.
#   y  see `DECK_ORIGIN_Y`
#   z  the deck work surface sits 100 mm above the deck's origin, which the manual states, so the
#      origin is 100 mm below the surface. The surface itself is measured on the manufacturer's
#      models: the deck plate is 2.5 mm thick, its underside on the platform's top plate, and its
#      TOP - which is what a carrier rests on - is 180.5 mm above the instrument's base. The same
#      on all three frames. What this replaces is an unsourced 78.5, which put the work surface at
#      178.5 and so seated every carrier 2.0 mm inside the plate it stands on.
STAR_DECK_LOCATION = Coordinate(110.0 + DECK_ORIGIN_X_CORRECTION, DECK_ORIGIN_Y, 80.5)
# The STARlet's x is not measured: its chassis leaves the same 119 mm beyond its deck as the STAR's
# does, so it takes the same value until someone measures one.
STARLET_DECK_LOCATION = Coordinate(110.0 + DECK_ORIGIN_X_CORRECTION, DECK_ORIGIN_Y, 80.5)


class STARDevice(Resource):
  """The complete modelling and control interface for a Hamilton Microlab STAR.

  The instrument is itself a resource and its deck is its child, so everything on the deck is a
  descendant of the machine carrying it: one tree, rooted here.

  Two tiers over one driver. `star.driver` speaks to the machine in its own terms - tracks,
  positions, millimetres - and stays reachable whatever is built on top of it. The device adds
  what the driver cannot know: where things are, and so which of them a command is about.
  """

  def __init__(
    self,
    deck: HamiltonSTARDeck,
    simulation: bool = False,
    driver: Optional[STARDriver] = None,
    name: str = "Generic STAR Device",
    size_x: Optional[float] = None,
    size_y: Optional[float] = None,
    size_z: Optional[float] = None,
    extension_housing: bool = False,
    autoload: bool = False,
    side_panel_x: Optional[float] = None,
    deck_location: Optional[Coordinate] = None,
    model: Optional[str] = None,
  ):
    """
    Args:
      deck: the deck this instrument carries. It becomes a child of the instrument, so everything
        assigned to it is a descendant of this device.
      simulation: whether to build a simulated instrument, which answers without one being plugged
        in. Superseded by `driver`, which says exactly what to drive.
      driver: the driver to drive the instrument through.
      name: what to call this instrument in the resource tree.
      size_x: how wide the instrument is, in mm, BEFORE any extension housing. Defaults to the
        deck's own width.
      size_y: how deep it is, in mm. Defaults to the deck's own depth.
      size_z: how tall it is, in mm. Defaults to the deck's own height.
      extension_housing: whether the left extension housing is fitted. It becomes a resource of its
        own, `left_extension_housing`, standing to the LEFT of the chassis at a negative x. It does
        NOT change the instrument's size: see `EXTENSION_HOUSING_SIZE`.
      autoload: whether an autoload is fitted. Recorded on the instrument as `autoload_fitted`,
        and does NOT change its size: the tray and the sled are their own resources standing in
        front of the chassis, so the instrument's box stays the chassis's own extent. Whether an
        autoload can be DRIVEN is discovered from the machine, through
        `configuration.autoload_installed`.
      side_panel_x: how far the chassis's left side panel sits from the instrument's left face.
        Given, and no extension housing fitted, the panel becomes a resource of its own. None
        leaves it out, which is what a machine whose panel has not been measured gets.
      deck_location: where the deck sits inside it, BEFORE any extension housing. Defaults to the
        instrument's own origin.
      model: which machine this is. Defaults to the class name, which says only that it is a STAR.

    Raises:
      ValueError: If neither a driver nor simulation is given, since there is then nothing to
        drive.
    """
    if driver is None and not simulation:
      raise ValueError("pass a driver, or `simulation=True` to build a simulated one")
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
    self.autoload_fitted = autoload

    self.deck = deck
    self.driver = driver if driver is not None else STARSimulationDriver(deck=deck)

    if self.driver.deck is not None and self.driver.deck is not deck:
      logger.warning("the driver was given another deck; modelling into this instrument's instead")

    self.driver.deck = deck
    self.assign_child_resource(
      deck, location=deck_location if deck_location is not None else Coordinate(0, 0, 0)
    )

    if side_panel_x is not None and not extension_housing:
      self.assign_child_resource(
        Resource(
          name="left_side_panel",
          size_x=SIDE_PANEL_SIZE[0],
          size_y=SIDE_PANEL_SIZE[1],
          size_z=SIDE_PANEL_SIZE[2],
          category="left_side_panel",
          model="hamilton_star_left_side_panel",
        ),
        location=Coordinate(side_panel_x, SIDE_PANEL_ORIGIN_YZ[0], SIDE_PANEL_ORIGIN_YZ[1]),
      )

    if extension_housing:
      # To the left, hung so its top and its back are level with the instrument's.
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

  # -- what the instrument carries ------------------------------------------------------------
  # Read through: the optional ones do not exist until discovery says what is fitted.

  @property
  def left_x_arm(self) -> Optional[XArm]:
    """The left X-arm, on a machine that has one."""
    return self.driver.left_x_arm

  @property
  def right_x_arm(self) -> Optional[XArm]:
    """The right X-arm, on a machine that has one."""
    return self.driver.right_x_arm

  @property
  def x_arm(self) -> XArm:
    """The X-arm, on a machine that has only one.

    Raises:
      RuntimeError: If setup has not run.
      ValueError: If the machine has more than one arm.
    """
    return self.driver.x_arm

  @property
  def pipettes(self) -> Optional[Pipettes]:
    """The pipetting channels, on a machine that has some."""
    return self.driver.pipettes

  @property
  def front_cover(self) -> Optional[FrontCover]:
    """The front cover, on a machine whose configuration has its monitoring installed."""
    return self.driver.front_cover

  @property
  def head96(self) -> Optional[Head96]:
    """The 96-head, on a machine that has one."""
    return self.driver.head96

  @property
  def head384(self) -> Optional[Head384]:
    """The 384-head, on a machine that has one."""
    return self.driver.head384

  @property
  def iswap(self) -> Optional[iSWAP]:
    """The iSWAP, on a machine that has one."""
    return self.driver.iswap

  @property
  def autoload(self) -> Optional[Autoload]:
    """The autoload, on a machine that has one."""
    return self.driver.autoload

  # -- session ---------------------------------------------------------------

  async def setup(self):
    """Bring the instrument up."""
    await self.driver.setup()

  async def stop(self):
    """Put the instrument down."""
    await self.driver.stop()

  def __str__(self) -> str:
    return f"{self.name}({self.driver.__class__.__name__}, {self.deck.num_rails}-track deck)"


# # # # Complete STAR Devices Factory Functions, for convenience in building a configuration.


def STAR(
  deck: Optional[HamiltonSTARDeck] = None,
  simulation: bool = False,
  driver: Optional[STARDriver] = None,
  name: str = "Hamilton STAR",
  size_x: float = STAR_SIZE_X,
  size_y: float = MANUAL_SIZE_Y,
  size_z: float = SIZE_Z,
  extension_housing: bool = False,
  autoload: bool = False,
) -> STARDevice:
  """A full-size STAR, on a full-size STAR deck."""
  return STARDevice(
    deck=deck if deck is not None else STARDeck(),
    simulation=simulation,
    driver=driver,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    extension_housing=extension_housing,
    autoload=autoload,
    side_panel_x=SIDE_PANEL_X,
    deck_location=STAR_DECK_LOCATION,
    model=STAR.__name__,
  )


def STAR_with_extension_housing(
  deck: Optional[HamiltonSTARDeck] = None,
  simulation: bool = False,
  driver: Optional[STARDriver] = None,
  name: str = "Hamilton STAR (left extension housing)",
  size_x: float = STAR_SIZE_X,
  size_y: float = MANUAL_SIZE_Y,
  size_z: float = SIZE_Z,
) -> STARDevice:
  """A full-size STAR with the extension housing on its left, on a full-size STAR deck.

  The same machine as `STAR(extension_housing=True)`, which is now the way to ask for one.
  """
  return STAR(
    deck=deck,
    simulation=simulation,
    driver=driver,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    extension_housing=True,
  )


def STARLet(
  deck: Optional[HamiltonSTARDeck] = None,
  simulation: bool = False,
  driver: Optional[STARDriver] = None,
  name: str = "Hamilton STARlet",
  size_x: float = STARLET_SIZE_X,
  size_y: float = MANUAL_SIZE_Y,
  size_z: float = SIZE_Z,
  extension_housing: bool = False,
  autoload: bool = False,
) -> STARDevice:
  """A STARlet, on a STARlet deck."""
  return STARDevice(
    deck=deck if deck is not None else STARLetDeck(),
    simulation=simulation,
    driver=driver,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    extension_housing=extension_housing,
    autoload=autoload,
    side_panel_x=SIDE_PANEL_X,
    deck_location=STARLET_DECK_LOCATION,
    model=STARLet.__name__,
  )


def STARPlus(
  deck: Optional[HamiltonSTARDeck] = None,
  simulation: bool = False,
  driver: Optional[STARDriver] = None,
  name: str = "Hamilton STARplus",
  size_x: float = STARPLUS_SIZE_X,
  size_y: float = MANUAL_SIZE_Y,
  size_z: float = SIZE_Z,
  extension_housing: bool = False,
  autoload: bool = False,
) -> STARDevice:
  """A STARplus, on a STARplus deck.

  The width is measured on the manufacturer's own model, as the other two frames are. Its deck is
  derived rather than read off a configuration file - see `STARPLUS_NUM_RAILS` - because there is no
  STARplus here to read one from, so its 78 rails should be confirmed against a real machine.
  """
  return STARDevice(
    deck=deck if deck is not None else STARPlusDeck(),
    simulation=simulation,
    driver=driver,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    extension_housing=extension_housing,
    autoload=autoload,
    side_panel_x=SIDE_PANEL_X,
    deck_location=STAR_DECK_LOCATION,
    model=STARPlus.__name__,
  )
