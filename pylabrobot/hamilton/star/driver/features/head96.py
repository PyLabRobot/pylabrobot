"""The 96-head: the block of 96 pipettes that works a whole plate at once."""

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import (
  TYPE_CHECKING,
  Any,
  Awaitable,
  Callable,
  Dict,
  List,
  Literal,
  Optional,
  Sequence,
  Tuple,
  Type,
  Union,
  cast,
)

from pylabrobot.hamilton.liquid_class_resolver import (
  ASPIRATE_CLASS_ATTRIBUTES,
  DISPENSE_CLASS_ATTRIBUTES,
  check_volume_arguments,
  from_class,
)
from pylabrobot.hamilton.liquid_classes import HamiltonLiquidClass
from pylabrobot.hamilton.star.driver.errors import STARFirmwareError
from pylabrobot.hamilton.star.driver.features.head import Head, HeadConfiguration
from pylabrobot.hamilton.star.driver.lld_mode import LLDMode
from pylabrobot.hamilton.star.liquid_classes.mapping import get_star_liquid_class
from pylabrobot.lib.liquid_handling.head_alignment import (
  get_items_under_channels,
  get_shift_with_channel_over,
)
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot, check_tip_racks_available, tip_origin
from pylabrobot.resources.volume_tracker import VolumeTracker, does_volume_tracking
from pylabrobot.resources.well import Well

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)

StopDiscType = Literal["core_i", "core_ii"]
InstrumentType = Literal["legacy", "FM-STAR"]


@dataclass
class Head96Configuration(HeadConfiguration):
  """Device facts for the installed 96-head.

  Ported from the legacy `Head96Information`. What this head adds to `HeadConfiguration` is what
  it reports about itself beyond the shared flags, and the firmware generation that resolves its
  drive windows: the encodings shifted at 2013, and a head reports which side of that it is on.
  """

  module: str = "H0"
  """What a real 96-head reached when it was probed."""
  retract_command: str = "EV"
  initialize_command: str = "EI"
  tip_presence_command: str = "QH"
  position_command: str = "QI"
  y_parameter: str = "yh"
  z_parameter: str = "za"
  z_end_parameter: str = "ze"
  x_offset_parameter: str = "kf"
  head_types: Dict[int, str] = field(
    default_factory=lambda: {
      0: "Low volume head",
      1: "High volume head",
      2: "96 head II",
      3: "96 head TADM",
    }
  )
  # The 2013-or-later widths. A 2008 head writes its accelerations narrower, and
  # `_apply_firmware_generation` swaps these for that head's.
  drive_parameters: Dict[str, int] = field(
    default_factory=lambda: {"yv": 5, "yr": 5, "zv": 5, "zr": 6, "dv": 5, "dr": 6, "sv": 5, "sr": 6}
  )
  # The generation the dispensing and squeezer resolutions below were taken from. A head older
  # than this has different ones, and `_apply_firmware_generation` resolves them.
  first_documented_firmware_year: int = 2010

  # As on the base: what the head reported, standing in front of the derived values below.
  squeezer_drive_speed_firmware_reported: Optional[float] = None
  squeezer_drive_acceleration_firmware_reported: Optional[float] = None

  stop_disc_type: Optional[StopDiscType] = None
  instrument_type: Optional[InstrumentType] = None

  # Encoder resolutions of the 2013-or-later generation; a 2008-era head's differ, and nothing
  # resolves them per generation, so they are left settable.
  dispensing_drive_uL_per_increment: float = 0.019340933  # type: ignore[assignment]
  squeezer_drive_mm_per_increment: float = 0.0002086672009  # type: ignore[assignment]

  # The Y window the master's tip commands accept, in deck mm at head channel A1. Narrower than
  # what the Y drive itself reaches, and narrower than the initialization command's own window, so
  # it is stated here rather than taken from `y_range`.
  tip_command_y_range: Tuple[float, float] = (108.0, 560.0)

  # Where the dispensing drive is sent before tips are collected off a rack, as a piston volume in
  # uL. The device does not lower the drive itself, so a head left with its piston up would
  # mount tips against it.
  dispensing_drive_position_before_rack_pickup: float = 218.19

  # The master's aspirate and dispense fields for this head: volumes in 0.1 uL, speeds in
  # 0.1 uL/s or 0.1 mm/s, distances in 0.1 mm, times in 0.1 s.
  pipetting_volume_range_increments: Tuple[int, int] = (0, 11_500)
  pipetting_speed_range_increments: Tuple[int, int] = (3, 5_000)
  pipetting_distance_range_increments: Tuple[int, int] = (0, 3_425)
  immersion_depth_range_increments: Tuple[int, int] = (0, 3_600)
  surface_following_distance_range_increments: Tuple[int, int] = (0, 990)
  swap_speed_range_increments: Tuple[int, int] = (3, 1_000)
  transport_air_volume_range_increments: Tuple[int, int] = (0, 500)
  stop_back_volume_range_increments: Tuple[int, int] = (0, 999)
  side_touch_off_distance_range_increments: Tuple[int, int] = (0, 45)
  settling_time_range_increments: Tuple[int, int] = (0, 99)
  mix_cycles_range: Tuple[int, int] = (0, 99)
  second_section_ratio_range_increments: Tuple[int, int] = (0, 10_000)
  lld_sensitivity_range: Tuple[int, int] = (1, 4)
  limit_curve_index_range: Tuple[int, int] = (0, 999)

  y_increment_floor: int = 6528
  """The lowest Y the drive accepts, in increments - 102.000 mm exactly.

  Found empirically, not from any document or read: the command's own window starts at 6000, but
  the drive refuses everything below this as outside its permitted area. Bisecting on a 2021 head
  put the edge here, with 6527 refused and 6528 accepted.

  It is hardcoded because nothing on the device reports it. Every parameter the head and the
  master will answer for was read - 499 of them - and none carries this value in any encoding, so a
  head that enforces a different floor has to have it set here. That it lands on a round number of
  millimetres, where a stored adjustment would land anywhere, is the reason to expect it constant
  across heads rather than particular to this one."""

  @property
  def firmware_year(self) -> int:
    """The year the head's firmware was built, which resolves the windows below.

    Returns:
      The year, as the firmware's own date gives it.

    Raises:
      RuntimeError: If the firmware version has not been read.
    """
    if self.firmware_date is None:
      raise RuntimeError("96-head firmware version not read; have you called `star.setup()`?")
    return self.firmware_date.year

  # -- what the head supplies to the shared windows ----------------------------------------------

  @property
  def z_range_increments(self) -> Tuple[int, int]:
    """Z-drive position window in increments; FM-STAR reaches both further down and further up.

    Returns:
      The (lowest, highest) Z position, in increments.
    """
    if self.instrument_type == "FM-STAR":
      return (24200, 76200)
    return (36100, 68500)

  @property
  def y_range_increments(self) -> Tuple[int, int]:
    """Y-drive position window in increments, at channel A1.

    The floor is `y_increment_floor` rather than the 6000 the command documents, because the drive
    refuses everything below it. The 2008 range is as documented and has not been measured.

    Returns:
      The (lowest, highest) Y position, in increments.
    """
    if self.firmware_year >= 2010:
      return (self.y_increment_floor, 36000)
    return (7000, 36200)

  @property
  def y_speed_range_increments(self) -> Tuple[int, int]:
    """Y-drive speed window in increments. The pre-2021 max (25000, the firmware default) is an
    empirical, deck-tested cap; per firmware version the maxima are 20000 (2008) and 40000 (2013+).

    Returns:
      The (lowest, highest) speed, in increments.
    Verify on a pre-2021 head before raising it."""
    return (50, 25000 if self.firmware_year <= 2021 else 40000)

  @property
  def y_acceleration_range_increments(self) -> Tuple[int, int]:
    """Y-drive acceleration window in increments. The min is constant; the max rose from 32000

    Returns:
      The (lowest, highest) acceleration, in increments.
    (2008) to 50000 (2013+), so it tracks firmware like the Y range / speed."""
    return (5000, 50000 if self.firmware_year >= 2010 else 32000)

  # -- windows the dispensing and squeezer drives work in ----------------------------------------

  @property
  def dispensing_drive_range(self) -> Tuple[float, float]:
    """Aspirate/dispense piston volume window (uL); applies to both aspirate and dispense. 2013

    Returns:
      The (lowest, highest) volume, in uL.
    firmware widened the max from 62130 inc."""
    max_inc = 64350 if self.firmware_year >= 2010 else 62130
    return (0.0, self.dispensing_drive_increments_to_uL(max_inc))

  @property
  def dispensing_drive_speed_range(self) -> Tuple[float, float]:
    """Dispensing-drive speed window (uL/s); 2013 firmware widened the max from 52000 inc."""
    min_inc = 5  # firmware dv minimum (00005 increments/second)
    max_inc = 55000 if self.firmware_year >= 2010 else 52000
    return (
      self.dispensing_drive_increments_to_uL(min_inc),
      self.dispensing_drive_increments_to_uL(max_inc),
    )

  @property
  def dispensing_drive_acceleration_range(self) -> Tuple[float, float]:
    """Dispensing-drive acceleration window (uL/s2); its max is the default 2013 firmware raised."""
    max_inc = 900000 if self.firmware_year >= 2010 else 150000
    return (
      self.dispensing_drive_increments_to_uL(5000),
      self.dispensing_drive_increments_to_uL(max_inc),
    )

  @property
  def squeezer_drive_speed_default(self) -> float:
    """Squeezer-drive default speed (mm/s); 2013 firmware raised it."""
    if self.squeezer_drive_speed_firmware_reported is not None:
      return self.squeezer_drive_speed_firmware_reported
    increments = 76000 if self.firmware_year >= 2010 else 16000
    return self.squeezer_drive_increments_to_mm(increments)

  @property
  def squeezer_drive_acceleration_default(self) -> float:
    """Squeezer-drive default acceleration (mm/s2); 2013 firmware raised it."""
    if self.squeezer_drive_acceleration_firmware_reported is not None:
      return self.squeezer_drive_acceleration_firmware_reported
    increments = 300000 if self.firmware_year >= 2010 else 100000
    return self.squeezer_drive_increments_to_mm(increments)


class Head96(Head):
  """The 96-head.

  Reached as `driver.head96`, on a device that has one. It is addressed as `H0`, but the
  commands that move it go to the master, so this feature speaks to both.
  """

  configuration: Head96Configuration

  # cLLD search speed, in mm/s.
  default_clld_search_speed: float = 10.0
  # cLLD search acceleration, in mm/s2.
  default_clld_acceleration: float = 300.0
  # cLLD edge steepness, 0 to 1023.
  default_clld_detection_edge: int = 10
  # cLLD offset after the edge, 0 to 1023.
  default_clld_detection_drop: int = 2
  # How far the head moves after detection, in mm; positive up.
  default_clld_post_detection_distance: float = 2.0
  # Mix: down to just above the well, in mm/s.
  default_mix_descent_speed: float = 80.0
  # Mix: into and out of the well, in mm/s.
  default_mix_swap_speed: float = 5.0
  # Mix: air drawn above the well and expelled there after, in uL.
  default_mix_blow_out_air_volume: float = 5.0
  # Mix: how far above the well top the swap into it starts, in mm.
  mix_swap_start_clearance: float = 5.0
  # Mix under cLLD: how far below the surface found the tips mix, in mm.
  default_mix_position_from_liquid_surface: float = 2.0
  # Aspirate under cLLD: how far below the surface found the tips draw, in mm.
  default_aspirate_immersion_depth: float = 2.0
  # Behind the properties of the same names, which check a value against its range when assigned.
  _default_dispensing_drive_speed: float = 250.0
  _default_dispensing_drive_acceleration: float = 17000.0
  # How far above a container's top a liquid search starts, in mm.
  search_start_clearance: float = 5.0

  def __init__(self, driver: "STARDriver", configuration: Optional[Head96Configuration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the head's device facts. Defaults to `Head96Configuration()`.
    """
    super().__init__(driver, configuration or Head96Configuration())
    # Where the piston stands, in uL, as last read or moved; 0.0 at rest.
    self.piston_position: float = 0.0

  @property
  def default_dispensing_drive_speed(self) -> float:
    """In uL/s. Not read from the head, which answers with the speed its last command set."""
    return self._default_dispensing_drive_speed

  @default_dispensing_drive_speed.setter
  def default_dispensing_drive_speed(self, speed: float) -> None:
    low, high = self.configuration.dispensing_drive_speed_range
    if not low <= speed <= high:
      raise ValueError(
        f"default_dispensing_drive_speed must be between {low} and {high}, is {speed}"
      )
    self._default_dispensing_drive_speed = speed

  @property
  def default_dispensing_drive_acceleration(self) -> float:
    """In uL/s2. A head with firmware from before 2010 gets 2900.0 at setup."""
    return self._default_dispensing_drive_acceleration

  @default_dispensing_drive_acceleration.setter
  def default_dispensing_drive_acceleration(self, acceleration: float) -> None:
    low, high = self.configuration.dispensing_drive_acceleration_range
    if not low <= acceleration <= high:
      raise ValueError(
        f"default_dispensing_drive_acceleration must be between {low} and {high}, is {acceleration}"
      )
    self._default_dispensing_drive_acceleration = acceleration

  # -- session / discovery -------------------------------------------------------------------------

  def _apply_firmware_generation(self) -> None:
    """Put the pre-2013 encodings in place on a head that runs them.

    Those heads count every drive's acceleration in thousands of increments per second squared and
    write it in a narrower field; from 2013 the same parameter is single increments in a wider one.
    Nothing else about the head announces which it is, so the firmware date decides.
    """
    c = self.configuration
    if c.firmware_year >= 2010:
      return
    self.default_dispensing_drive_acceleration = 2900.0
    c.y_drive_acceleration_mm_per_increment = c.y_drive_mm_per_increment * 1000
    c.z_drive_acceleration_mm_per_increment = c.z_drive_mm_per_increment * 1000
    c.drive_parameters = {"yv": 5, "yr": 3, "zv": 5, "zr": 3, "dv": 5, "dr": 4, "sv": 5, "sr": 3}
    c.z_acceleration_range_increments = (5, 100)

  async def discover(self):
    """Read what head this is, then take its squeezer defaults from the head."""
    await super().discover()
    c = self.configuration
    c.squeezer_drive_speed_firmware_reported = await self._reported_drive_parameter("sv")
    c.squeezer_drive_acceleration_firmware_reported = await self._reported_drive_parameter("sr")

  def _record_hardware(self, hardware: List[str]) -> None:
    """Record the stop disc and device type this head reports.

    Index 1 is populated on firmware at least back to 2021. Whether index 2 is reliably populated
    on every build, or on some falls back to reserve (read back as 0 -> legacy), is unverified;
    confirm on an FM-STAR head before relying on it to unlock the FM-STAR z-range extension.

    Args:
      hardware: the tokens `request_hardware` read.
    """
    c = self.configuration
    c.stop_disc_type = "core_i" if hardware[1] == "0" else "core_ii"
    c.instrument_type = "legacy" if hardware[2] == "0" else "FM-STAR"

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  # -- dispensing drive position -------------------------------------------------------------------

  async def dispensing_drive_request_uL_position(self) -> float:
    """Read where the head's dispensing drive stands, in uL, and record it. `H0 RD`.

    Returns:
      The piston's position in uL, 0.0 at rest; air and liquid alike. Kept on `piston_position`.
    """
    resp = await self._driver.send_command(
      module=self.configuration.module, command="RD", fmt="rd##### (n)"
    )
    increments = cast(List[int], resp["rd"])[1]  # [0] = firmware counter, [1] = hardware counter
    self.piston_position = self.configuration.dispensing_drive_increments_to_uL(increments)
    return self.piston_position

  async def _record_piston_position(self) -> None:
    """Read where the piston came to rest, and record it; a failed read is logged and swallowed."""
    try:
      await self.dispensing_drive_request_uL_position()
    except Exception:
      logger.warning("could not read where the 96-head's piston stopped; its model is stale")

  async def move_dispensing_drive_to_position(
    self,
    volume: float,
    speed: Optional[float] = None,
    stop_speed: float = 0.0,
    acceleration: Optional[float] = None,
    current_limit: int = 15,
    read_timeout: int = 30,
  ):
    """Move the dispensing drive to an absolute piston position, and record it on `piston_position`.

    Args:
      volume: where to send the piston, as the volume it would hold, in uL.
      speed: how fast, in uL/s. Defaults to `default_dispensing_drive_speed`.
      stop_speed: what to slow to at the end, in uL/s.
      acceleration: how hard, in uL/s2. Defaults to `default_dispensing_drive_acceleration`.
      current_limit: the motor current limit.
      read_timeout: how long to wait for the device to answer, in seconds.

    Raises:
      ValueError: If an argument is outside what the drive accepts.
    """
    c = self.configuration
    if speed is None:
      speed = self.default_dispensing_drive_speed
    if acceleration is None:
      acceleration = self.default_dispensing_drive_acceleration

    for value, (low, high), name in (
      (volume, c.dispensing_drive_range, "volume"),
      (speed, c.dispensing_drive_speed_range, "speed"),
      (stop_speed, (0.0, c.dispensing_drive_speed_range[1]), "stop_speed"),
      (acceleration, c.dispensing_drive_acceleration_range, "acceleration"),
    ):
      if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}, is {value}")
    low_limit, high_limit = c.current_limit_range
    if not low_limit <= current_limit <= high_limit:
      raise ValueError(
        f"current_limit must be between {low_limit} and {high_limit}, is {current_limit}"
      )

    increments = c.dispensing_drive_uL_to_increments(volume)
    try:
      resp = await self._driver.send_command(
        module=c.module,
        command="DQ",
        dq=f"{increments:05}",
        dv=f"{c.dispensing_drive_uL_to_increments(speed):05}",
        du=f"{c.dispensing_drive_uL_to_increments(stop_speed):05}",
        dr=f"{c.dispensing_drive_uL_to_increments(acceleration):06}",
        dw=f"{current_limit:02}",
        read_timeout=read_timeout,
      )
    except BaseException:
      await self._record_piston_position()
      raise
    self.piston_position = c.dispensing_drive_increments_to_uL(increments)
    return resp

  # -- x and y together ----------------------------------------------------------------------------

  async def _require_iswap_parked(self) -> None:
    """Raise unless the iSWAP on this head's arm is parked; nothing to check without one.

    Raises:
      RuntimeError: If it is not parked.
    """
    iswap = None if self.arm is None else self.arm.iswap
    if iswap is not None and not await iswap.request_is_parked():
      raise RuntimeError(
        "the iSWAP is not parked, and the head moves where it stands. "
        "Call `await star.iswap.park()` first."
      )

  def _position_centred_in(self, resource: Resource) -> Coordinate:
    """Where head channel A1 lands with the channel array centred over a resource, in deck mm.

    A1 is the array's back-left channel: half the array left of the resource's centre and half
    the array behind it.

    Args:
      resource: what to centre over.

    Returns:
      The A1 position, in deck mm, at the resource's own Z.

    Raises:
      RuntimeError: If the driver was given no deck, so the resource has no deck position.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("this driver has no deck, so a resource has no position to centre in")
    c = self.configuration
    location = resource.get_location_wrt(deck)
    return Coordinate(
      location.x + (resource.get_size_x() - c.channel_array_size_x) / 2,
      location.y + (resource.get_size_y() + c.channel_array_size_y) / 2,
      location.z,
    )

  def _get_target(
    self, resource: Union[Plate, Container, List[Well]], offset: Optional[Coordinate]
  ) -> Tuple[Container, Coordinate, float, float]:
    """The container a head operation works in, where head channel A1 goes, its floor and its top.

    Over a plate of many wells A1 goes over well A1; over a single container, or a plate of one
    well, the channel array is centred over it.

    Args:
      resource: a plate (well A1, or its one well), a container, or wells (the first).
      offset: added to where head channel A1 goes, in mm.

    Returns:
      The container; A1's position at its cavity bottom plus `offset`; its cavity bottom and top
      Z, in deck mm.

    Raises:
      RuntimeError: If the driver was given no deck.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("containers are placed from the deck; this driver was given none")
    if isinstance(resource, Plate):
      anchor: Container = resource.get_item(0)
      centred = resource.num_items == 1
    elif isinstance(resource, list):
      anchor, centred = resource[0], False
    else:
      anchor = resource
      plate = anchor.parent if isinstance(anchor, Well) else None
      centred = not isinstance(plate, Plate) or plate.num_items == 1
    a1 = anchor.get_location_wrt(deck, x="c", y="c", z="cavity_bottom")
    bottom = a1.z
    if centred:
      centre = self._position_centred_in(anchor)
      a1 = Coordinate(centre.x, centre.y, a1.z)
    a1 += offset or Coordinate.zero()
    top = anchor.get_location_wrt(deck, x="c", y="c", z="t").z
    return anchor, a1, bottom, top

  async def _move_over(
    self,
    a1: Coordinate,
    minimum_traverse_height_start: Optional[float],
    descent_speed: Optional[float],
  ) -> None:
    """Bring the head, with tips, over a position: the channels up, the head up, then X and Y.

    Args:
      a1: where head channel A1 goes, in deck mm; its Z is not used.
      minimum_traverse_height_start: tip bottom height before the XY move, in mm. Safe Z when None.
      descent_speed: to that height, in mm/s.

    Raises:
      RuntimeError: If the head carries no tips or the iSWAP is not parked.
    """
    await self._require_iswap_parked()
    if not await self.request_tip_presence():
      raise RuntimeError("the head reports no tips; pick up tips first")
    # The head's own moves leave the channels where they are, so they go up first.
    if self.arm is not None and self.arm.pipettes is not None:
      await self.arm.pipettes.move_to_safe_z()
    if minimum_traverse_height_start is None:
      await self.move_to_safe_z()
    else:
      await self.move_tool_bottom_to_z_position(minimum_traverse_height_start, speed=descent_speed)
    # Gentler X acceleration at low Y, where the head stands furthest out from the X drive.
    await asyncio.gather(
      self.move_to_x_position(a1.x, acceleration_level=1 if a1.y <= 200.0 else 3),
      self.move_to_y_position(a1.y),
    )

  # -- head initialization -------------------------------------------------------------------------

  async def initialize(
    self,
    tip_discard_location: Optional[Coordinate] = None,
    z_position_at_the_command_end: Optional[float] = None,
    read_timeout: int = 60,
  ):
    """Initialize the head, discarding whatever is mounted on it, and read where its piston stands.

    Args:
      tip_discard_location: where to eject, in deck mm, at head channel A1. Defaults to
        `configuration.tip_discard_location`.
      z_position_at_the_command_end: Z to leave the head at, in mm. Defaults to
        `configuration.traversal_z_position`.
      read_timeout: how long to wait for the device to answer, in seconds.

    Raises:
      ValueError: If no position was given and none is configured.
    """
    resp = await super().initialize(
      tip_discard_location, z_position_at_the_command_end, read_timeout
    )
    # Initialization homes the piston; the first read of where it stands comes here.
    self.piston_position = 0.0
    await self._record_piston_position()
    return resp

  # ----------------------------------------
  # Tip handling
  # ----------------------------------------

  # -- what every tip command shares ---------------------------------------------------------------

  def _resolve_tip_command_heights(
    self,
    minimum_traverse_z_position_at_the_command_start: Optional[float],
    minimum_z_position_at_the_command_end: Optional[float],
  ) -> Tuple[float, float]:
    """The two heights a tip command travels at, defaulted where the caller named neither.

    Args:
      minimum_traverse_z_position_at_the_command_start: how high the head travels to get there.
      minimum_z_position_at_the_command_end: the height to leave the head at.

    Returns:
      The two, in mm, with `configuration.traversal_z_position` where None was given.
    """
    traversal = self.configuration.traversal_z_position
    if minimum_traverse_z_position_at_the_command_start is None:
      minimum_traverse_z_position_at_the_command_start = traversal
    if minimum_z_position_at_the_command_end is None:
      minimum_z_position_at_the_command_end = traversal
    return (
      minimum_traverse_z_position_at_the_command_start,
      minimum_z_position_at_the_command_end,
    )

  def _check_tip_command(
    self, location: Coordinate, traverse_z: float, end_z: float, skip_z: bool = False
  ) -> None:
    """Raise unless a tip command may run where it is being pointed.

    Reachability is `_check_reachable`'s to answer, so X, Z and the two heights go through it. What
    is left here is the one thing it does not cover: the Y window these commands accept is narrower
    than what the Y drive reaches, so a position the head could physically get to may still be
    refused by the command.

    Args:
      location: where the command would send head channel A1, in deck mm.
      traverse_z: the traverse height it would use, in mm.
      end_z: the height it would leave the head at, in mm.
      skip_z: leave the position's Z unchecked, for a command that resolves it separately.

    Raises:
      ValueError: If a position is out of reach or outside the command's Y window.
      RuntimeError: If the windows were not resolved.
    """
    self._check_reachable("x", location.x)
    if not skip_z:
      self._check_reachable("z", location.z)
    self._check_reachable("z", traverse_z)
    self._check_reachable("z", end_z)
    low, high = self.configuration.tip_command_y_range
    if not low <= location.y <= high:
      raise ValueError(f"y must be between {low} and {high}, is {location.y}")

  async def _record_after_tip_command(self, command_error: Optional[BaseException] = None) -> None:
    """Read back where a tip command left the arm and the head, and record it.

    Args:
      command_error: the command's own failure, which a failed read-back does not hide.
    """
    try:
      if self.arm is not None:
        await self.arm.request_position()
      await self.request_y_position()
      await self.request_z_position()
    except Exception:
      if command_error is None:
        raise
      logger.exception("could not read back where the 96-head stopped")

  async def _request_tips_after_failure(self) -> Optional[bool]:
    """Whether the firmware holds that tips are mounted after a tip command failed; None unread."""
    try:
      return await self.request_tip_presence()
    except Exception:
      logger.warning("could not request whether the 96-head carries tips after the failure")
      return None

  # -- tip pickup ----------------------------------------------------------------------------------

  async def pick_up_tips(
    self,
    tip_rack: TipRack,
    offset: Optional[Coordinate] = None,
    tip_pickup_method: Literal["from_rack", "from_waste", "full_blowout"] = "from_rack",
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    *,
    channel: str = "A1",
    tip_spot: str = "A1",
  ) -> None:
    """Pick up tips from a rack on the head, as legacy's `pick_up_tips96`. `C0 EP`.

    Head channel `channel` goes to the centre of spot `tip_spot`, at the spot's Z. Once the device
    has picked them up, each channel over a spot holding a tip has that tip on its shaft. Channels
    past the rack's edge come down beside it: what stands there is the caller's to keep clear.

    Args:
      tip_rack: a 96 tip rack. Spots without a tip give none.
      offset: added to the spot's centre, in mm.
      tip_pickup_method: `from_rack` sends the dispensing drive down first, since the device does
        not; `from_waste` and `full_blowout` move the plunger up before mounting.
      minimum_height_command_end: in mm. `configuration.traversal_z_position` when None.
      minimum_traverse_height_start: in mm.
        `configuration.traversal_z_position` when None.
      channel: the head channel placed over `tip_spot`, A1 to H12.
      tip_spot: the spot `channel` is placed over, A1 to H12.

    Raises:
      ValueError: If the rack does not have 96 spots, no channel is over a tip, a position
        cannot be reached, or a channel is past the edge of a rack with a frame.
      TypeError: If its tips are not Hamilton tips.
      RuntimeError: If the driver was given no deck, or the head already carries tips.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    # Read before anything moves: a pickup first drives the piston down, which would push out
    # whatever tips still on the head hold.
    if await self.request_tip_presence():
      raise RuntimeError("the head already carries tips; drop them before picking up more")
    if tip_rack.num_items != 96:
      raise ValueError("Tip rack must have 96 tips")
    check_tip_racks_available(tip_rack.get_all_items()[:1], "pick up tips from")
    tip_spots_under_channels = get_items_under_channels(tip_rack, channel, tip_spot)
    # Past the rack's edge the head's body comes down over the rack's frame.
    if None in tip_spots_under_channels and tip_rack.frame_height != 0:
      raise ValueError(
        f"{tip_rack.name!r} has a frame the head would hit unless channel A1 is over spot A1"
      )
    tips = [
      spot.tip_for_pickup()
      if spot is not None and (not spot.tracks_tips or spot.tip is not None)
      else None
      for spot in tip_spots_under_channels
    ]
    prototypical_tip = next((tip for tip in tips if tip is not None), None)
    if prototypical_tip is None:
      raise ValueError("No tips found under the head.")
    if not isinstance(prototypical_tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")
    tip_type_index = await self._driver.get_or_assign_tip_type_index(prototypical_tip)

    location = (
      tip_rack.get_item("A1").get_location_wrt(deck, x="c", y="c", z="b")
      + get_shift_with_channel_over(tip_rack, channel, tip_spot, deck)
      + (offset or Coordinate.zero())
    )
    traverse_z, end_z = self._resolve_tip_command_heights(
      minimum_traverse_height_start, minimum_height_command_end
    )
    self._check_tip_command(location, traverse_z, end_z, skip_z=True)

    if tip_pickup_method == "from_rack":
      await self.move_dispensing_drive_to_position(
        self.configuration.dispensing_drive_position_before_rack_pickup
      )
    picked_up = False
    command_error: Optional[BaseException] = None
    try:
      await self._driver.send_command(
        module="C0",
        command="EP",
        subsystem=self.configuration.module,
        xs=f"{abs(round(location.x * 10)):05}",
        xd=0 if location.x >= 0 else 1,
        yh=f"{round(location.y * 10):04}",
        tt=f"{tip_type_index:02}",
        wu={"from_rack": 0, "from_waste": 1, "full_blowout": 2}[tip_pickup_method],
        za=f"{round(location.z * 10):04}",
        zh=f"{round(traverse_z * 10):04}",
        ze=f"{round(end_z * 10):04}",
      )
      picked_up = True
    except BaseException as failure:
      # A command can stop part way; what the firmware then holds is taken over the error.
      command_error = failure
      picked_up = bool(await self._request_tips_after_failure())
      raise
    finally:
      if picked_up and self.resource is not None:
        for shaft, tip in zip(self.resource.get_all_items(), tips):
          if tip is not None:
            shaft.mount_tip(tip)
      await self._record_after_tip_command(command_error)
    await self.dispensing_drive_request_uL_position()

  # -- tip drop ------------------------------------------------------------------------------------

  async def drop_tips(
    self,
    resource: Resource,
    offset: Optional[Coordinate] = None,
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    *,
    channel: str = "A1",
    tip_spot: str = "A1",
  ) -> None:
    """Drop the head's tips into a tip rack or anywhere else, as legacy's `drop_tips96`. `C0 ER`.

    Into a tip rack, head channel `channel` goes to the centre of spot `tip_spot`, at the spot's
    Z, and each channel's tip goes into the spot under it. Anywhere else, the head is centred over
    the resource, and the tips belong to nothing afterwards.

    Args:
      resource: a 96 tip rack, or anything else, such as the trash.
      offset: added to where the head goes, in mm.
      minimum_height_command_end: in mm. `configuration.traversal_z_position` when None.
      minimum_traverse_height_start: in mm.
        `configuration.traversal_z_position` when None.
      channel: the head channel placed over `tip_spot`, A1 to H12. A tip rack only.
      tip_spot: the spot `channel` is placed over, A1 to H12. A tip rack only.

    Raises:
      ValueError: If a tip rack does not have 96 spots, a channel carrying a tip is not over a
        spot, a position cannot be reached, or a channel is past the edge of a rack with a frame.
      RuntimeError: If the driver was given no deck.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    tip_spots_under_channels: List[Optional[TipSpot]] = []
    if isinstance(resource, TipRack):
      if resource.num_items != 96:
        raise ValueError("Tip rack must have 96 tips")
      check_tip_racks_available(resource.get_all_items()[:1], "drop tips into")
      tip_spots_under_channels = get_items_under_channels(resource, channel, tip_spot)
      # Past the rack's edge the head's body comes down over the rack's frame.
      if None in tip_spots_under_channels and resource.frame_height != 0:
        raise ValueError(
          f"{resource.name!r} has a frame the head would hit unless channel A1 is over spot A1"
        )
      if self.resource is not None:
        beside = [
          shaft.name
          for shaft, spot in zip(self.resource.get_all_items(), tip_spots_under_channels)
          if shaft.has_tip() and spot is None
        ]
        if beside:
          raise ValueError(f"channels {beside} carry a tip but are not over a spot of the rack")
      location = resource.get_item("A1").get_location_wrt(deck, x="c", y="c", z="b")
      location += get_shift_with_channel_over(resource, channel, tip_spot, deck)
    else:
      location = self._position_centred_in(resource)
    location += offset or Coordinate.zero()
    traverse_z, end_z = self._resolve_tip_command_heights(
      minimum_traverse_height_start, minimum_height_command_end
    )
    self._check_tip_command(location, traverse_z, end_z, skip_z=True)

    dropped = False
    command_error: Optional[BaseException] = None
    try:
      await self._driver.send_command(
        module="C0",
        command="ER",
        subsystem=self.configuration.module,
        xs=f"{abs(round(location.x * 10)):05}",
        xd=0 if location.x >= 0 else 1,
        yh=f"{round(location.y * 10):04}",
        za=f"{round(location.z * 10):04}",
        zh=f"{round(traverse_z * 10):04}",
        ze=f"{round(end_z * 10):04}",
      )
      dropped = True
    except BaseException as failure:
      # A command can stop part way; what the firmware then holds is taken over the error.
      command_error = failure
      dropped = await self._request_tips_after_failure() is False
      raise
    finally:
      if dropped and self.resource is not None:
        for i, shaft in enumerate(self.resource.get_all_items()):
          if not shaft.has_tip():
            continue
          tip = shaft.release_tip()
          if isinstance(resource, TipRack) and isinstance(tip, Tip):
            spot = tip_spots_under_channels[i]
            if spot is not None and spot.tracks_tips:
              spot.assign_tip(tip)
      await self._record_after_tip_command(command_error)
    await self.dispensing_drive_request_uL_position()

  async def return_tips(self, **kwargs) -> None:
    """Put the head's tips back in the tip rack they were picked up from.

    The rack, and the spot each channel stood over, are found from each tip's origin. Channels
    without a tip are skipped.

    Args:
      kwargs: passed on to `drop_tips`.

    Raises:
      RuntimeError: If the driver was given no deck, the head is not modelled or carries no tips,
        or its tips did not all stand under their channels in one tip rack on the deck.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    if self.resource is None:
      raise RuntimeError("the head is not modelled, so where its tips came from is not known")
    shafts = self.resource.get_all_items()
    carrying = next((shaft for shaft in shafts if isinstance(shaft.tip, Tip)), None)
    if carrying is None:
      raise RuntimeError("No tips have been picked up.")
    origin = tip_origin(cast(Tip, carrying.tip), deck)
    if origin is None or not isinstance(origin.parent, TipRack):
      raise RuntimeError(f"the tip on {carrying.name} did not come from a tip rack on the deck")
    tip_rack = origin.parent
    # Any one tip names the pick-up: its channel stood over the spot it came from.
    channel = self.resource.get_child_identifier(carrying)
    tip_spot = tip_rack.get_child_identifier(origin)
    tip_spots_under_channels = get_items_under_channels(tip_rack, channel, tip_spot)
    for shaft, spot in zip(shafts, tip_spots_under_channels):
      tip = shaft.tip
      if isinstance(tip, Tip) and tip_origin(tip, deck) is not spot:
        raise RuntimeError(
          f"{shaft.name}'s tip {tip.name} did not stand under it in {tip_rack.name}; the head "
          "returns tips only as it picked them up from one rack"
        )
    await self.drop_tips(tip_rack, channel=channel, tip_spot=tip_spot, **kwargs)

  async def discard_tips(self, **kwargs) -> None:
    """Drop the head's tips into the deck's 96-head trash, whatever the model says it carries.

    Args:
      kwargs: passed on to `drop_tips`.

    Raises:
      RuntimeError: If the driver was given no deck.
      NotImplementedError: If the deck has no 96-head trash.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    await self.drop_tips(deck.get_trash_area96(), **kwargs)

  # ----------------------------------------
  # Probing
  # ----------------------------------------

  # -- z probing (capacitive) ----------------------------------------------------------------------

  async def _unchecked_fw_probe_z_using_clld(
    self,
    end_position: int,
    start_position: int,
    approach_speed: int,
    search_speed: int,
    acceleration: int,
    current_limit: int,
    lld_mode: Optional[int],
    detection_edge: int,
    detection_drop: int,
    post_detection_trajectory: Literal[0, 1],
    post_detection_distance: int,
    immersion_mode: Optional[Literal[0, 1]],
  ):
    """Lower the head until its cLLD triggers, as given, in Z increments. `H0 ZL`.

    Args:
      end_position: stop disc height it goes no lower than (`zh`).
      start_position: stop disc height the search starts from (`zc`).
      approach_speed: to the search start (`zv`).
      search_speed: during the search (`zl`).
      acceleration: in the drive's acceleration increments (`zr`).
      current_limit: motor current limit (`zw`).
      lld_mode: which sensors trigger, 0 to 3 (`lm`); None leaves it out.
      detection_edge: edge steepness, 0 to 1023 (`gt`).
      detection_drop: offset after the edge, 0 to 1023 (`gl`).
      post_detection_trajectory: 0 down, 1 up (`zj`).
      post_detection_distance: how far it moves after detection (`zi`).
      immersion_mode: 0 normal, 1 no lower than `end_position` (`dj`); None leaves it out.
    """
    c = self.configuration
    lm: Dict[str, Any] = {} if lld_mode is None else {"lm": lld_mode}
    dj: Dict[str, Any] = {} if immersion_mode is None else {"dj": immersion_mode}
    return await self._driver.send_command(
      module=c.module,
      command="ZL",
      zh=f"{end_position:05}",
      zc=f"{start_position:05}",
      zi=f"{post_detection_distance:04}",
      zj=post_detection_trajectory,
      **lm,
      gt=f"{detection_edge:04}",
      gl=f"{detection_drop:04}",
      zv=f"{approach_speed:05}",
      zl=f"{search_speed:05}",
      zr=f"{acceleration:0{c.drive_parameters['zr']}}",
      zw=f"{current_limit:0{1 if c.firmware_year < 2010 else len(str(c.current_limit_range[1]))}}",
      **dj,
    )

  async def request_last_lld_z_position(self) -> float:
    """Request where the stop disc was when the last cLLD search detected. `H0 RH`.

    Returns:
      The stop disc's Z position at detection, in mm.
    """
    resp = await self._driver.send_command(
      module=self.configuration.module, command="RH", fmt="rh#####"
    )
    return self.configuration.z_drive_increments_to_mm(cast(int, resp["rh"]))

  async def _require_tips_feeding(
    self, lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"]
  ) -> None:
    """Raise unless tips are on the channels that feed the chosen cLLD sensor.

    Raises:
      RuntimeError: If the head reports no tips, or the model has none on those channels.
    """
    if not await self.request_tip_presence():
      raise RuntimeError("the head reports no tips; cLLD needs them for a conductive path")
    if self.resource is None:
      return
    tipped = {name: self.resource.get_item(name).has_tip() for name in ("A1", "B2", "G11", "H12")}
    sensor_0, sensor_1 = tipped["G11"] or tipped["H12"], tipped["A1"] or tipped["B2"]
    if not {
      "G11 or H12": sensor_0,
      "A1 or B2": sensor_1,
      "any": sensor_0 or sensor_1,
      "all": sensor_0 and sensor_1,
    }[lld_sensor]:
      raise RuntimeError(f"no tip on the channels that feed lld_sensor={lld_sensor!r}")

  def _found_nothing(self, error: STARFirmwareError) -> bool:
    """Whether a firmware error says only that a search reached its end without detecting.

    The head answers that with trace 70.
    """
    module = self.configuration.module
    return bool(error.errors) and all(
      e.raw_module == module and e.trace_information == 70 for e in error.errors.values()
    )

  async def _clld_search(
    self,
    end_position: float,
    start_position: float,
    approach_speed: Optional[float] = None,
    search_speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"] = "any",
    detection_edge: Optional[int] = None,
    detection_drop: Optional[int] = None,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_distance: Optional[float] = None,
    limit_immersion_to_search_end: bool = False,
  ) -> None:
    """Run the head's cLLD search between two stop disc heights, every field checked.

    Stop disc terms; no tip check; a search that finds nothing raises the head's error.

    Args:
      end_position: stop disc height it goes no lower than, in mm.
      start_position: stop disc height the search starts from, in mm.
      approach_speed: to the search start, in mm/s. `z_drive_speed_default` when None.
      search_speed: in mm/s. `default_clld_search_speed` when None.
      acceleration: in mm/s2. `default_clld_acceleration` when None.
      current_limit: `z_drive_current_limit_default` when None.
      lld_sensor: which cLLD sensors trigger.
      detection_edge: 0 to 1023. `default_clld_detection_edge` when None.
      detection_drop: 0 to 1023. `default_clld_detection_drop` when None.
      post_detection_trajectory: 0 moves down after detection, 1 up.
      post_detection_distance: in mm. `default_clld_post_detection_distance` when None.
      limit_immersion_to_search_end: never go below `end_position` after detection.

    Raises:
      ValueError: If a field is out of range, or a sensor is chosen on 2008 firmware.
      STARFirmwareError: As the head answers, a search that found nothing included.
    """
    c = self.configuration
    lld_modes = {"G11 or H12": 0, "A1 or B2": 1, "any": 2, "all": 3}
    if lld_sensor not in lld_modes:
      raise ValueError(f"lld_sensor must be one of {list(lld_modes)}, is {lld_sensor!r}")
    lld_mode: Optional[int] = lld_modes[lld_sensor]
    if c.firmware_year < 2010:
      if lld_sensor != "any":
        raise ValueError(f"lld_sensor={lld_sensor!r} needs 2013 firmware, which has `lm`")
      lld_mode = None
    if post_detection_trajectory not in (0, 1):
      raise ValueError(f"post_detection_trajectory must be 0 or 1, is {post_detection_trajectory}")
    if approach_speed is None:
      approach_speed = c.z_drive_speed_default
    if search_speed is None:
      search_speed = self.default_clld_search_speed
    if acceleration is None:
      acceleration = self.default_clld_acceleration
    if current_limit is None:
      current_limit = c.z_drive_current_limit_default
    if detection_edge is None:
      detection_edge = self.default_clld_detection_edge
    if detection_drop is None:
      detection_drop = self.default_clld_detection_drop
    if post_detection_distance is None:
      post_detection_distance = self.default_clld_post_detection_distance
    if c.firmware_year < 2010 and not 0 <= current_limit <= 7:
      raise ValueError(
        f"current_limit must be between 0 and 7 on 2008 firmware, is {current_limit}"
      )
    for checked, name in ((detection_edge, "detection_edge"), (detection_drop, "detection_drop")):
      if not 0 <= checked <= 1023:
        raise ValueError(f"{name} must be between 0 and 1023, is {checked}")
    distance = c.z_drive_mm_to_increments(post_detection_distance)
    if not 0 <= distance <= 9999:
      raise ValueError(
        f"post_detection_distance must be between 0 and {c.z_drive_increments_to_mm(9999)} mm, "
        f"is {post_detection_distance}"
      )
    self._check_move("z", start_position, approach_speed, acceleration, current_limit)
    self._check_move("z", end_position, search_speed, acceleration, current_limit)
    ramp = c.z_drive_acceleration_mm_to_increments(acceleration)
    if c.firmware_year < 2010:
      # The search takes the 2008 thousands rounded down.
      ramp = c.z_drive_mm_to_increments(acceleration) // 1000
    try:
      await self._unchecked_fw_probe_z_using_clld(
        end_position=c.z_drive_mm_to_increments(end_position),
        start_position=c.z_drive_mm_to_increments(start_position),
        approach_speed=c.z_drive_mm_to_increments(approach_speed),
        search_speed=c.z_drive_mm_to_increments(search_speed),
        acceleration=ramp,
        current_limit=current_limit,
        lld_mode=lld_mode,
        detection_edge=detection_edge,
        detection_drop=detection_drop,
        post_detection_trajectory=post_detection_trajectory,
        post_detection_distance=distance,
        immersion_mode=1 if limit_immersion_to_search_end else None,
      )
    finally:
      await self._record_where_it_stopped("z")

  async def probe_z_using_clld(
    self,
    *,
    search_start_position: Optional[float] = None,
    search_end_position: Optional[float] = None,
    tip_overhang: Optional[float] = None,
    approach_speed: Optional[float] = None,
    search_speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"] = "any",
    detection_edge: Optional[int] = None,
    detection_drop: Optional[int] = None,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_distance: Optional[float] = None,
    limit_immersion_to_search_end: bool = False,
    move_to_safe_z_position_after: bool = False,
  ) -> float:
    """Lower the head's tips until its cLLD triggers, and read the tip bottom height it detected at.

    Args:
      search_start_position: tip bottom height to search from, in mm. The highest when None.
      search_end_position: lowest tip bottom height, in mm. The lowest when None.
      tip_overhang: tips below the stop disc, in mm. Measured when None.
      approach_speed: to the search start, in mm/s. `z_drive_speed_default` when None.
      search_speed: in mm/s. `default_clld_search_speed` when None.
      acceleration: in mm/s2. `default_clld_acceleration` when None.
      current_limit: `z_drive_current_limit_default` when None.
      lld_sensor: which cLLD sensors trigger.
      detection_edge: 0 to 1023. `default_clld_detection_edge` when None.
      detection_drop: 0 to 1023. `default_clld_detection_drop` when None.
      post_detection_trajectory: 0 moves down after detection, 1 up.
      post_detection_distance: in mm. `default_clld_post_detection_distance` when None.
      limit_immersion_to_search_end: never go below `search_end_position` after detection.
      move_to_safe_z_position_after: raise the head to safe Z afterwards.

    Returns:
      The tip bottom height at detection, in mm.

    Raises:
      ValueError: If an argument is out of range, or a sensor is chosen on 2008 firmware.
      RuntimeError: If the channels feeding the sensor carry no tips.
    """
    c = self.configuration
    await self._require_tips_feeding(lld_sensor)
    if tip_overhang is None:
      tip_overhang = await self._overhang_that_probes()
    if search_start_position is None:
      search_start_position = round(c.z_range[1] - tip_overhang, 2)
    if search_end_position is None:
      search_end_position = round(max(c.z_range[0] - tip_overhang, c.min_tool_bottom_z), 2)
    if search_end_position < c.min_tool_bottom_z:
      raise ValueError(
        f"search_end_position must be at least {c.min_tool_bottom_z}, is {search_end_position}"
      )

    try:
      await self._clld_search(
        round(search_end_position + tip_overhang, 2),
        round(search_start_position + tip_overhang, 2),
        approach_speed=approach_speed,
        search_speed=search_speed,
        acceleration=acceleration,
        current_limit=current_limit,
        lld_sensor=lld_sensor,
        detection_edge=detection_edge,
        detection_drop=detection_drop,
        post_detection_trajectory=post_detection_trajectory,
        post_detection_distance=post_detection_distance,
        limit_immersion_to_search_end=limit_immersion_to_search_end,
      )
    except STARFirmwareError:
      await self.move_to_safe_z()
      raise
    # RH is the stop disc at detection: on a device it read exactly `zi` below the RZ that followed.
    detected = round(await self.request_last_lld_z_position() - tip_overhang, 2)
    if move_to_safe_z_position_after:
      await self.move_to_safe_z()
    return detected

  async def _search_surface(
    self,
    bottom: float,
    top: float,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"],
    search_speed: Optional[float],
    approach_speed: Optional[float] = None,
  ) -> Optional[float]:
    """Search down by cLLD from `search_start_clearance` over the top to the cavity bottom.

    No move after detection: the tips stay on the surface.

    Args:
      bottom: the cavity bottom, in deck mm.
      top: the container's top, in deck mm.
      lld_sensor: which cLLD sensors trigger.
      search_speed: in mm/s. `default_clld_search_speed` when None.
      approach_speed: to the search start, in mm/s. `z_drive_speed_default` when None.

    Returns:
      The surface's height, tip bottom on the deck in mm; None when nothing was found.
    """
    overhang = await self._overhang_that_probes()
    try:
      await self._clld_search(
        round(bottom + overhang, 2),
        round(top + self.search_start_clearance + overhang, 2),
        approach_speed=approach_speed,
        search_speed=search_speed,
        lld_sensor=lld_sensor,
        post_detection_distance=0.0,
      )
    except STARFirmwareError as error:
      if self._found_nothing(error):
        return None
      raise
    return round(await self.request_last_lld_z_position() - overhang, 2)

  # -- over a container: liquid height and volume --------------------------------------------------

  async def probe_liquid_height(
    self,
    resource: Union[Plate, Container, List[Well]],
    offset: Optional[Coordinate] = None,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"] = "any",
    search_speed: Optional[float] = None,
    n_replicates: int = 1,
    *,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> float:
    """Find the liquid surface in a container with the whole head, and say how high it stands.

    Positioned as `mix` is; searched by cLLD from `search_start_clearance` over the top to the
    cavity bottom, `n_replicates` times, the tips staying on the surface between rounds. Safe Z at
    the end unless told where to stay.

    Args:
      resource: a plate (well A1, or its one well), a container, or wells (the first).
      offset: added to where head channel A1 goes, in mm; its z is not used.
      lld_sensor: which cLLD sensors trigger.
      search_speed: in mm/s. `default_clld_search_speed` when None.
      n_replicates: how many searches; the heights are averaged.
      minimum_traverse_height_start: tip bottom height before the XY move, in mm. Safe Z when None.
      minimum_traverse_height_end: where the tips are left, in mm. Safe Z when None.

    Returns:
      How high the liquid stands above the cavity bottom, in mm; 0.0 where none was met.

    Raises:
      ValueError: If an argument is out of range.
      RuntimeError: If the head carries no tips, the iSWAP is not parked, the driver was given no
        deck, or liquid was found in some rounds and not in others.
    """
    if n_replicates < 1:
      raise ValueError(f"n_replicates must be at least 1, is {n_replicates}")
    anchor, a1, bottom, top = self._get_target(resource, offset)
    await self._move_over(a1, minimum_traverse_height_start, None)
    try:
      rounds = [
        await self._search_surface(bottom, top, lld_sensor, search_speed)
        for _ in range(n_replicates)
      ]
    except BaseException:
      await self.move_to_safe_z()
      raise
    found = [surface for surface in rounds if surface is not None]
    if found and len(found) != len(rounds):
      await self.move_to_safe_z()
      raise RuntimeError(
        f"liquid found in {len(found)} of {len(rounds)} rounds in {anchor.name}, so it may be at "
        "the detection limit"
      )
    if minimum_traverse_height_end is None:
      await self.move_to_safe_z()
    else:
      await self.move_tool_bottom_to_z_position(minimum_traverse_height_end)
    # The bottom is known, so a container in which no liquid was met stands at 0.0.
    return round(sum(found) / len(found) - bottom, 2) if found else 0.0

  async def probe_liquid_volume(
    self,
    resource: Union[Plate, Container, List[Well]],
    offset: Optional[Coordinate] = None,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"] = "any",
    search_speed: Optional[float] = None,
    n_replicates: int = 1,
    *,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> float:
    """Find the liquid as `probe_liquid_height` does, and say how much there is.

    The container has to know its height-volume functions; over a plate it is well A1's volume.

    Args:
      As `probe_liquid_height`.

    Returns:
      The volume, in uL; what its function makes of 0.0 where no liquid was met.

    Raises:
      ValueError: If the container has no height-to-volume function, or as `probe_liquid_height`.
      RuntimeError: As `probe_liquid_height`.
    """
    anchor = self._get_target(resource, offset)[0]
    if not anchor.supports_compute_height_volume_functions():
      raise ValueError(f"no height-to-volume function for {anchor.name}")
    height = await self.probe_liquid_height(
      resource,
      offset,
      lld_sensor,
      search_speed,
      n_replicates,
      minimum_traverse_height_start=minimum_traverse_height_start,
      minimum_traverse_height_end=minimum_traverse_height_end,
    )
    return anchor.compute_volume_from_height(height)

  # ----------------------------------------
  # Liquid handling
  # ----------------------------------------

  # -- what aspirating and dispensing share --------------------------------------------------------

  async def _unchecked_fw_aspirate_in_place(
    self,
    volume: int,
    flow_rate: int,
    surface_following_distance: int,
    minimum_height: int,
  ):
    """Draw on every channel, as given, in increments. `H0 PA`.

    Args:
      volume: dispensing drive travel (`da`).
      flow_rate: dispensing drive speed (`dv`).
      surface_following_distance: Z travel during the draw (`zd`).
      minimum_height: stop disc height it goes no lower than (`zh`).
    """
    return await self._driver.send_command(
      module=self.configuration.module,
      command="PA",
      pm="F" * 24,
      dj="1",
      da=f"{volume:05}",
      dv=f"{flow_rate:05}",
      dc="00000",
      zd=f"{surface_following_distance:04}",
      zh=f"{minimum_height:05}",
      to="000",
    )

  async def _unchecked_fw_dispense_in_place(
    self,
    volume: int,
    flow_rate: int,
    stop_flow_rate: int,
    stop_back_volume: int,
    surface_following_distance: int,
    minimum_height: int,
  ):
    """Expel on every channel, as given, in increments. `H0 PB`.

    Args:
      volume: dispensing drive travel (`db`).
      flow_rate: dispensing drive speed (`dv`).
      stop_flow_rate: dispensing drive stop speed (`du`).
      stop_back_volume: drawn back at the end (`dd`).
      surface_following_distance: Z travel during the expel (`ze`).
      minimum_height: stop disc height it goes no lower than (`zh`).
    """
    return await self._driver.send_command(
      module=self.configuration.module,
      command="PB",
      pm="F" * 24,
      db=f"{volume:05}",
      dv=f"{flow_rate:05}",
      dd=f"{stop_back_volume:04}",
      ze=f"{surface_following_distance:04}",
      zh=f"{minimum_height:05}",
      du=f"{stop_flow_rate:05}",
    )

  async def _resolve_stroke_floor(self, minimum_height: Optional[float]) -> int:
    """The stop disc height a stroke goes no lower than, in Z increments.

    Tip bottom terms when tips are on, stop disc terms when not; the lowest reachable when None.

    Args:
      minimum_height: in mm.

    Raises:
      ValueError: If it is out of reach.
    """
    c = self.configuration
    overhang = 0.0
    if await self.request_tip_presence():
      overhang = await self._overhang_that_probes()
    low = max(c.z_range[0] - overhang, c.min_tool_bottom_z)
    high = c.z_range[1] - overhang
    if minimum_height is None:
      minimum_height = low
    if not low <= minimum_height <= high:
      raise ValueError(f"minimum_height must be between {low} and {high} mm, is {minimum_height}")
    return c.z_drive_mm_to_increments(minimum_height + overhang)

  async def _aspirate_in_place(
    self,
    volume: float,
    flow_rate: Optional[float] = None,
    surface_following_distance: float = 0.0,
    minimum_height: Optional[float] = None,
  ) -> None:
    """Draw on every channel in place, every field checked; Z and piston move together.

    Moves `piston_position` by the draw; after a failed command, reads it back.

    Args:
      volume: per channel, in uL.
      flow_rate: in uL/s. `default_dispensing_drive_speed` when None.
      surface_following_distance: how far down it follows the surface, in mm.
      minimum_height: lowest tip bottom height, in mm. The lowest reachable when None.

    Raises:
      ValueError: If a field is out of range.
    """
    c = self.configuration
    if flow_rate is None:
      flow_rate = self.default_dispensing_drive_speed
    following_max = c.z_drive_increments_to_mm(9999)
    for checked, (low, high), name in (
      (volume, c.dispensing_drive_range, "volume"),
      (flow_rate, c.dispensing_drive_speed_range, "flow_rate"),
      (surface_following_distance, (0.0, following_max), "surface_following_distance"),
    ):
      if not low <= checked <= high:
        raise ValueError(f"{name} must be between {low} and {high}, is {checked}")
    floor = await self._resolve_stroke_floor(minimum_height)
    increments = c.dispensing_drive_uL_to_increments(volume)
    try:
      await self._unchecked_fw_aspirate_in_place(
        volume=increments,
        flow_rate=c.dispensing_drive_uL_to_increments(flow_rate),
        surface_following_distance=c.z_drive_mm_to_increments(surface_following_distance),
        minimum_height=floor,
      )
    except BaseException:
      await self._record_piston_position()
      raise
    else:
      drawn = c.dispensing_drive_increments_to_uL(increments)
      self.piston_position = round(self.piston_position + drawn, 2)
    finally:
      await self._record_where_it_stopped("z")

  async def _dispense_in_place(
    self,
    volume: float,
    flow_rate: Optional[float] = None,
    stop_flow_rate: float = 0.0,
    stop_back_volume: float = 0.0,
    surface_following_distance: float = 0.0,
    minimum_height: Optional[float] = None,
  ) -> None:
    """Expel on every channel in place, every field checked; Z and piston move together.

    Moves `piston_position` by the expel and the stop-back; after a failed command, reads it back.

    Args:
      volume: per channel, in uL.
      flow_rate: in uL/s. `default_dispensing_drive_speed` when None.
      stop_flow_rate: in uL/s.
      stop_back_volume: drawn back at the end, in uL.
      surface_following_distance: how far up it follows the surface, in mm.
      minimum_height: lowest tip bottom height, in mm. The lowest reachable when None.

    Raises:
      ValueError: If a field is out of range.
    """
    c = self.configuration
    if flow_rate is None:
      flow_rate = self.default_dispensing_drive_speed
    speed_max = c.dispensing_drive_speed_range[1]
    following_max = c.z_drive_increments_to_mm(9999)
    for checked, (low, high), name in (
      (volume, c.dispensing_drive_range, "volume"),
      (flow_rate, c.dispensing_drive_speed_range, "flow_rate"),
      (stop_flow_rate, (0.0, speed_max), "stop_flow_rate"),
      (stop_back_volume, (0.0, c.dispensing_drive_increments_to_uL(9999)), "stop_back_volume"),
      (surface_following_distance, (0.0, following_max), "surface_following_distance"),
    ):
      if not low <= checked <= high:
        raise ValueError(f"{name} must be between {low} and {high}, is {checked}")
    floor = await self._resolve_stroke_floor(minimum_height)
    increments = c.dispensing_drive_uL_to_increments(volume)
    back_increments = c.dispensing_drive_uL_to_increments(stop_back_volume)
    try:
      await self._unchecked_fw_dispense_in_place(
        volume=increments,
        flow_rate=c.dispensing_drive_uL_to_increments(flow_rate),
        stop_flow_rate=c.dispensing_drive_uL_to_increments(stop_flow_rate),
        stop_back_volume=back_increments,
        surface_following_distance=c.z_drive_mm_to_increments(surface_following_distance),
        minimum_height=floor,
      )
    except BaseException:
      await self._record_piston_position()
      raise
    else:
      left = max(self.piston_position - c.dispensing_drive_increments_to_uL(increments), 0.0)
      drawn_back = c.dispensing_drive_increments_to_uL(back_increments)
      self.piston_position = round(left + drawn_back, 2)
    finally:
      await self._record_where_it_stopped("z")

  @staticmethod
  def _get_channel_pattern_hex(channel_pattern: List[bool]) -> str:
    """The channel mask as `cw` carries it: upper-case hex, the first flag the lowest bit.

    Args:
      channel_pattern: 96 flags, the channels that take part.
    """
    bits = "".join("1" if involved else "0" for involved in reversed(channel_pattern))
    return f"{int(bits, 2):X}"

  def _get_containers_under_channels(
    self, resource: Union[Plate, Container, List[Well]]
  ) -> Optional[List[Container]]:
    """The container under each channel, in channel order; None where one takes all 96.

    Raises:
      ValueError: A plate of neither 1 nor 96 wells, a list not of 96 wells, or one well of a
        plate of many.
    """
    if isinstance(resource, Plate):
      if resource.num_items == 1:
        return None
      if resource.num_items != 96:
        raise ValueError(f"{resource.name} has {resource.num_items} wells; the head works 96 or 1")
      return [well for well in resource.get_all_items()]
    if isinstance(resource, list):
      if len(resource) != 96:
        raise ValueError(f"the head works 96 wells at once, not {len(resource)}")
      return [well for well in resource]
    plate = resource.parent if isinstance(resource, Well) else None
    if isinstance(plate, Plate) and plate.num_items > 1:
      raise ValueError(f"{resource.name} is one well of {plate.name}; give the plate or its wells")
    return None

  def _get_mounted_tips(self) -> List[Optional[HamiltonTip]]:
    """The tip on each channel as the model has it, None where there is none.

    Raises:
      RuntimeError: If the head is not modelled, or carries no tip in the model.
      TypeError: If a tip is not a Hamilton tip.
    """
    if self.resource is None:
      raise RuntimeError("the head is not modelled, so what its tips hold is not known")
    tips: List[Optional[HamiltonTip]] = []
    for shaft in self.resource.get_all_items():
      tip = shaft.tip
      if tip is not None and not isinstance(tip, HamiltonTip):
        raise TypeError(f"{shaft.name} carries {tip.name}, not a Hamilton tip")
      tips.append(tip)
    if not any(tip is not None for tip in tips):
      raise RuntimeError("the head is modelled with no tips; pick up tips first")
    return tips

  @staticmethod
  def _get_liquid_and_piston_volume(
    tip: HamiltonTip,
    volume: Optional[float],
    piston_volume: Optional[float],
    hamilton_liquid_class: Optional[HamiltonLiquidClass],
    jet: bool,
    blow_out: bool,
    how_moved: str,
  ) -> Tuple[float, float, Optional[HamiltonLiquidClass]]:
    """The liquid per channel, the piston volume that moves it, and the class used.

    Args:
      tip: the head's tip, for the lookup.
      volume: liquid per channel, corrected by a class; or None.
      piston_volume: piston travel per channel, as given; or None.
      hamilton_liquid_class: looked up for the tip, water, `jet` and `blow_out` when None.
      jet: for the lookup.
      blow_out: for the lookup.
      how_moved: "drawn" or "pushed out", for the refusals.

    Raises:
      ValueError: Both or neither of `volume` and `piston_volume`, a class beside
        `piston_volume`, or no class known for the tip.
    """
    check_volume_arguments(
      None if volume is None else [volume],
      None if piston_volume is None else [piston_volume],
      None if hamilton_liquid_class is None else [hamilton_liquid_class],
      how_moved,
    )
    if volume is None:
      return cast(float, piston_volume), cast(float, piston_volume), None
    hlc = hamilton_liquid_class or get_star_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=True,
      is_tip=True,
      has_filter=tip.has_filter,
      liquid=Liquid.WATER,
      jet=jet,
      blow_out=blow_out,
    )
    if hlc is None:
      raise ValueError(
        f"no 96-head liquid class is known for {tip.maximal_volume} uL tips, "
        f"{'with' if tip.has_filter else 'without'} filter, water, jet={jet}, "
        f"blow_out={blow_out}. Give hamilton_liquid_class or piston_volume"
      )
    return volume, round(hlc.compute_corrected_volume(volume), 2), hlc

  def _check_pipetting_fields(
    self,
    overhang: float,
    location: Coordinate,
    heights: Dict[str, float],
    fields: List[Tuple[str, int, Tuple[int, int]]],
  ) -> None:
    """Raise unless a head aspirate or dispense may be sent as planned.

    Args:
      overhang: the tips' overhang below the stop disc, in mm.
      location: where head channel A1 goes, in deck mm.
      heights: tip bottom heights by name, in deck mm.
      fields: name, value in firmware units, and its (lowest, highest).

    Raises:
      ValueError: A position out of reach, or a field out of its range.
    """
    c = self.configuration
    self._check_reachable("x", location.x)
    low_y, high_y = c.tip_command_y_range
    errors: List[str] = []
    if not low_y <= location.y <= high_y:
      errors.append(f"y must be between {low_y} and {high_y}, is {location.y}")
    low = round(max(c.z_range[0] - overhang, c.min_tool_bottom_z), 2)
    high = round(c.z_range[1] - overhang, 2)
    for name, z in heights.items():
      if not low <= z <= high:
        errors.append(f"{name} must be between {low} and {high} mm, is {z}")
    for name, value, (lowest, highest) in fields:
      if not lowest <= value <= highest:
        errors.append(f"{name} must be between {lowest} and {highest}, is {value}")
    if errors:
      raise ValueError("Invalid 96-head parameters:\n" + "\n".join(errors))

  @staticmethod
  def _get_lld_mode(
    lld_mode: Optional[LLDMode],
    auto_surface_following: bool,
    surface_following_distance: float,
    liquid_height: Optional[float],
  ) -> LLDMode:
    """The mode a call runs with: CAPACITIVE when None under auto surface following, else OFF.

    Raises:
      ValueError: Auto surface following beside a distance, or under OFF without a liquid height.
    """
    if not auto_surface_following:
      return LLDMode.OFF if lld_mode is None else lld_mode
    if surface_following_distance != 0.0:
      raise ValueError(
        "auto_surface_following beside a surface_following_distance: give one of them"
      )
    if lld_mode is None:
      return LLDMode.CAPACITIVE
    if lld_mode == LLDMode.OFF and liquid_height is None:
      raise ValueError(
        "auto_surface_following under lld_mode OFF needs a liquid_height to follow from"
      )
    return lld_mode

  @staticmethod
  def _get_surface_change(container: Container, height: float, volume: float) -> float:
    """How far `volume` uL added moves a surface `height` mm over the cavity bottom, in mm.

    A negative volume is drawn, and lowers it.
    """
    held = container.compute_volume_from_height(height)
    return round(container.compute_height_from_volume(max(held + volume, 0.0)) - height, 1)

  def _get_mix_following(self, mix: Mix, container: Container, height: float, share: int) -> Mix:
    """`mix` with its auto surface following as a distance: what one draw lowers a surface
    `height` mm over the cavity bottom, `share` channels drawing from `container`."""
    if not mix.auto_surface_following:
      return mix
    drop = -self._get_surface_change(container, height, -mix.volume * share)
    return replace(
      mix, surface_following_distance=round(max(drop, 0.0), 1), auto_surface_following=False
    )

  def _update_volume_from_surface(
    self, container: Container, surface: float, bottom: float
  ) -> None:
    """Set a container's tracker to the volume a found surface stands for, warning when 20 % off.

    Args:
      container: where the surface was found.
      surface: the surface found, in deck mm.
      bottom: the container's cavity bottom, in deck mm.
    """
    try:
      measured = container.compute_volume_from_height(max(round(surface - bottom, 2), 0.0))
    except ValueError:
      logger.warning(
        "the 96-head found the liquid of %s %.2f mm above its cavity bottom, outside its "
        "height-volume data; the model keeps %.1f uL",
        container.name,
        surface - bottom,
        container.tracker.get_used_volume(),
      )
      return
    expected = container.tracker.get_used_volume()
    if abs(measured - expected) > 0.2 * expected:
      logger.warning(
        "the 96-head measured %.1f uL in %s where the model had %.1f uL",
        measured,
        container.name,
        expected,
      )
    container.tracker.set_volume(measured)

  async def _book_and_send(
    self,
    send: Callable[[], Awaitable[None]],
    pairs: Sequence[Tuple[VolumeTracker, VolumeTracker]],
    liquid: Sequence[float],
    *,
    piston_before: float,
    piston_after: float,
    air_before_liquid: float,
    piston_sign: int,
    tracking: bool,
    held_less_message: str,
    moved_before_failure_message: str,
  ) -> None:
    """Book the liquid on the trackers, send the command, and move the piston model.

    Booked before the command; committed as it succeeds; rolled back on its failure, with a read
    of the piston to book what did move.

    Args:
      send: the command.
      pairs: per channel with a tip, the tracker the liquid leaves and the one it enters.
      liquid: per pair, what is asked, in uL.
      piston_before: where the piston stands before the command, in uL.
      piston_after: where it stands after it, in uL.
      air_before_liquid: the air the piston moves before the liquid, in uL.
      piston_sign: 1 where the command draws, -1 where it pushes out.
      tracking: whether volumes are tracked.
      held_less_message: the log line for givers holding less than asked: the channels.
      moved_before_failure_message: the log line for what a failed command still moved: the
        volume per channel.
    """
    booked: List[VolumeTracker] = []
    moves: List[float] = []
    try:
      if tracking:
        short: List[int] = []
        for channel, ((giver, taker), asked) in enumerate(zip(pairs, liquid)):
          moved = min(asked, giver.get_used_volume())
          if moved < asked:
            short.append(channel)
          # Booked before either tracker may refuse, so a refusal rolls back what came before it.
          booked += [giver, taker]
          giver.remove_liquid(moved)
          taker.add_liquid(moved)
          moves.append(moved)
        if short:
          logger.warning(held_less_message, short)
      await send()
    except BaseException:
      for tracker in booked:
        tracker.rollback()
      # A command that failed part way moved liquid: the piston's travel past the air is liquid.
      if tracking and len(moves) == len(pairs):
        travel = 0.0
        try:
          now = await self.dispensing_drive_request_uL_position()
          travel = round(max(piston_sign * (now - piston_before) - air_before_liquid, 0.0), 1)
        except Exception:
          logger.warning("could not read the 96-head's piston; what it moved is not in the model")
        if travel > 0:
          for (giver, taker), most in zip(pairs, moves):
            moved = min(travel, most, giver.get_used_volume())
            giver.remove_liquid(moved)
            taker.add_liquid(moved)
            giver.commit()
            taker.commit()
          logger.warning(moved_before_failure_message, travel)
      raise
    for tracker in booked:
      tracker.commit()
    self.piston_position = round(piston_after, 1)

  # -- aspirating ----------------------------------------------------------------------------------

  async def _unchecked_fw_aspirate(
    self,
    channel_pattern: List[bool],
    aspiration_type: int,
    x_position: int,
    y_position: int,
    minimum_traverse_height_start: int,
    minimum_z_end_position: int,
    lld_search_height: int,
    liquid_surface_no_lld: int,
    pull_out_distance_transport_air: int,
    second_section_height: int,
    second_section_ratio: int,
    minimum_height: int,
    immersion_depth: int,
    immersion_depth_direction: int,
    surface_following_distance: int,
    aspiration_volume: int,
    aspiration_speed: int,
    transport_air_volume: int,
    blow_out_air_volume: int,
    pre_wetting_volume: int,
    lld_mode: int,
    clld_sensitivity: int,
    swap_speed: int,
    settling_time: int,
    mix_volume: int,
    mix_cycles: int,
    mix_position_from_liquid_surface: int,
    mix_speed: int,
    mix_surface_following_distance: int,
    limit_curve_index: int,
    tadm_algorithm: bool,
    recording_mode: int,
    read_timeout: int = 300,
  ):
    """Send the head's aspiration as it is given, at head channel A1. `C0 EA`.

    Heights and distances in 0.1 mm, volumes in 0.1 uL, speeds in 0.1 per second, times in 0.1 s.
    Nothing is guarded and nothing is recorded.

    Args:
      channel_pattern: 96 flags, the channels that take part (`cw`).
      aspiration_type: 0 simple, 1 sequence, 2 cup emptied (`aa`).
      x_position: signed; written as its magnitude (`xs`) and its sign (`xd`).
      y_position: `yh`.
      minimum_traverse_height_start: `zh`.
      minimum_z_end_position: `ze`.
      lld_search_height: `lz`.
      liquid_surface_no_lld: `zt`.
      pull_out_distance_transport_air: `pp`.
      second_section_height: `zv`.
      second_section_ratio: `zq`.
      minimum_height: `zm`.
      immersion_depth: `iw`.
      immersion_depth_direction: 0 deeper, 1 up out of the liquid (`ix`).
      surface_following_distance: `fh`.
      aspiration_volume: `af`.
      aspiration_speed: `ag`.
      transport_air_volume: `vt`.
      blow_out_air_volume: `bv`.
      pre_wetting_volume: `wv`.
      lld_mode: 0 off, 1 capacitive, 2 pressure, 3 dual, 4 Z touch off (`cm`).
      clld_sensitivity: 1 high to 4 low (`cs`).
      swap_speed: on leaving the liquid (`bs`).
      settling_time: `wh`.
      mix_volume: `hv`.
      mix_cycles: `hc`.
      mix_position_from_liquid_surface: `hp`.
      mix_speed: `hs`.
      mix_surface_following_distance: `mj`.
      limit_curve_index: TADM (`cr`).
      tadm_algorithm: `cj`.
      recording_mode: TADM, 0 none, 1 errors only, 2 all (`cx`).
      read_timeout: how long to wait for the device to answer, in seconds.
    """
    return await self._driver.send_command(
      module="C0",
      command="EA",
      subsystem=self.configuration.module,
      read_timeout=read_timeout,
      aa=aspiration_type,
      xs=f"{abs(x_position):05}",
      xd=0 if x_position >= 0 else 1,
      yh=f"{y_position:04}",
      zh=f"{minimum_traverse_height_start:04}",
      ze=f"{minimum_z_end_position:04}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_no_lld:04}",
      pp=f"{pull_out_distance_transport_air:04}",
      zm=f"{minimum_height:04}",
      zv=f"{second_section_height:04}",
      zq=f"{second_section_ratio:05}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{surface_following_distance:03}",
      af=f"{aspiration_volume:05}",
      ag=f"{aspiration_speed:04}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      wv=f"{pre_wetting_volume:05}",
      cm=lld_mode,
      cs=clld_sensitivity,
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mix_volume:05}",
      hc=f"{mix_cycles:02}",
      hp=f"{mix_position_from_liquid_surface:03}",
      mj=f"{mix_surface_following_distance:03}",
      hs=f"{mix_speed:04}",
      cw=self._get_channel_pattern_hex(channel_pattern),
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  async def _aspirate_in_one_move(
    self,
    location: Coordinate,
    lld_search_height: float,
    minimum_allowed_z_position_during: float,
    piston_volume: float,
    *,
    channel_pattern: Optional[List[bool]] = None,
    minimum_traverse_height_start: Optional[float] = None,
    lld_mode: LLDMode = LLDMode.OFF,
    clld_sensitivity: int = 1,
    blow_out_air_volume: Optional[float] = None,
    immersion_depth: Optional[float] = None,
    pre_wetting_volume: Optional[float] = None,
    pre_mix: Optional[Mix] = None,
    mix_position_from_liquid_surface: Optional[float] = None,
    flow_rate: Optional[float] = None,
    surface_following_distance: Optional[float] = None,
    second_section_height: Optional[float] = None,
    second_section_ratio: Optional[float] = None,
    settling_time: Optional[float] = None,
    swap_speed: Optional[float] = None,
    pull_out_distance_transport_air: Optional[float] = None,
    transport_air_volume: Optional[float] = None,
    limit_curve_index: Optional[int] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Aspirate with the whole head in one `C0 EA`, every field checked before it is sent.

    Tip bottom heights on the deck in mm, volumes per channel in uL, speeds in uL/s or mm/s, times
    in s. No model update: `aspirate` does that.

    Args:
      location: where head channel A1 goes; the z is the liquid surface when no LLD runs.
      lld_search_height: where an LLD search starts.
      minimum_allowed_z_position_during: how low the tips may go.
      piston_volume: what the piston draws.
      channel_pattern: 96 flags, in channel order. All True when None.
      minimum_traverse_height_start: `configuration.traversal_z_position` when None.
      lld_mode: OFF or CAPACITIVE.
      clld_sensitivity: 1 high to 4 low.
      blow_out_air_volume: air drawn before the liquid. 0.0 when None.
      immersion_depth: how far into the liquid; negative is out of it. 0.0 when None.
      pre_wetting_volume: drawn and returned first. 0.0 when None.
      pre_mix: mixed before the draw; None for no mixing.
      mix_position_from_liquid_surface: mixing depth under the surface. 0.0 when None.
      flow_rate: 100.0 when None.
      surface_following_distance: how far the tips follow the sinking surface. 0.0 when None.
      second_section_height: height of the narrower lower section. 3.2 when None.
      second_section_ratio: that section's bottom to top ratio, in tenths. 618.0 when None.
      settling_time: wait in the liquid. 0.0 when None.
      swap_speed: speed of leaving the liquid. 100.0 when None.
      pull_out_distance_transport_air: rise before drawing transport air. 10.0 when None.
      transport_air_volume: air drawn after the liquid. 0.0 when None.
      limit_curve_index: TADM limit curve, 0 for none. 0 when None.
      minimum_traverse_height_end: `configuration.traversal_z_position` when None.

    Raises:
      ValueError: A value out of range, an unreachable position, a pattern not of 96, or an LLD
        mode the head does not have.
      RuntimeError: If the head reports no tips.
    """
    if lld_mode not in (LLDMode.OFF, LLDMode.CAPACITIVE):
      raise ValueError(f"the 96-head has lld_mode OFF or CAPACITIVE, not {lld_mode.name}")
    pattern = [True] * 96 if channel_pattern is None else list(channel_pattern)
    if len(pattern) != 96:
      raise ValueError(f"channel_pattern must have 96 entries, has {len(pattern)}")
    start, end = self._resolve_tip_command_heights(
      minimum_traverse_height_start, minimum_traverse_height_end
    )
    immersion = immersion_depth or 0.0
    mix_volume = 0.0 if pre_mix is None else pre_mix.volume
    mix_cycles = 0 if pre_mix is None else pre_mix.repetitions
    mix_speed = 100.0 if pre_mix is None else pre_mix.flow_rate
    mix_following = 0.0 if pre_mix is None else pre_mix.surface_following_distance or 0.0

    def tenths(value: float) -> int:
      return round(value * 10)

    c = self.configuration
    wire: Dict[str, Any] = dict(
      aspiration_type=0,
      x_position=tenths(location.x),
      y_position=tenths(location.y),
      minimum_traverse_height_start=tenths(start),
      minimum_z_end_position=tenths(end),
      lld_search_height=tenths(lld_search_height),
      liquid_surface_no_lld=tenths(location.z),
      pull_out_distance_transport_air=tenths(
        10.0 if pull_out_distance_transport_air is None else pull_out_distance_transport_air
      ),
      minimum_height=tenths(minimum_allowed_z_position_during),
      second_section_height=tenths(3.2 if second_section_height is None else second_section_height),
      second_section_ratio=tenths(618.0 if second_section_ratio is None else second_section_ratio),
      immersion_depth=abs(tenths(immersion)),
      immersion_depth_direction=1 if immersion < 0 else 0,
      surface_following_distance=tenths(surface_following_distance or 0.0),
      aspiration_volume=tenths(piston_volume),
      aspiration_speed=tenths(100.0 if flow_rate is None else flow_rate),
      transport_air_volume=tenths(transport_air_volume or 0.0),
      blow_out_air_volume=tenths(blow_out_air_volume or 0.0),
      pre_wetting_volume=tenths(pre_wetting_volume or 0.0),
      lld_mode=lld_mode.value,
      clld_sensitivity=clld_sensitivity,
      swap_speed=tenths(100.0 if swap_speed is None else swap_speed),
      settling_time=tenths(settling_time or 0.0),
      mix_volume=tenths(mix_volume),
      mix_cycles=mix_cycles,
      mix_position_from_liquid_surface=tenths(mix_position_from_liquid_surface or 0.0),
      mix_surface_following_distance=tenths(mix_following),
      mix_speed=tenths(mix_speed),
      channel_pattern=pattern,
      limit_curve_index=limit_curve_index or 0,
      tadm_algorithm=False,
      recording_mode=0,
    )
    volume, speed, distance = (
      c.pipetting_volume_range_increments,
      c.pipetting_speed_range_increments,
      c.pipetting_distance_range_increments,
    )
    following = c.surface_following_distance_range_increments
    self._check_pipetting_fields(
      await self._overhang_that_probes(),
      location,
      {
        "the liquid surface": location.z,
        "lld_search_height": lld_search_height,
        "minimum_allowed_z_position_during": minimum_allowed_z_position_during,
        "minimum_traverse_height_start": start,
        "minimum_traverse_height_end": end,
      },
      [
        (
          "pull_out_distance_transport_air, in 0.1 mm,",
          wire["pull_out_distance_transport_air"],
          distance,
        ),
        ("second_section_height, in 0.1 mm,", wire["second_section_height"], distance),
        (
          "second_section_ratio, in tenths,",
          wire["second_section_ratio"],
          c.second_section_ratio_range_increments,
        ),
        (
          "immersion_depth, in 0.1 mm,",
          wire["immersion_depth"],
          c.immersion_depth_range_increments,
        ),
        ("surface_following_distance, in 0.1 mm,", wire["surface_following_distance"], following),
        ("piston_volume, in 0.1 uL,", wire["aspiration_volume"], volume),
        ("flow_rate, in 0.1 uL/s,", wire["aspiration_speed"], speed),
        (
          "transport_air_volume, in 0.1 uL,",
          wire["transport_air_volume"],
          c.transport_air_volume_range_increments,
        ),
        ("blow_out_air_volume, in 0.1 uL,", wire["blow_out_air_volume"], volume),
        ("pre_wetting_volume, in 0.1 uL,", wire["pre_wetting_volume"], volume),
        ("clld_sensitivity", clld_sensitivity, c.lld_sensitivity_range),
        ("swap_speed, in 0.1 mm/s,", wire["swap_speed"], c.swap_speed_range_increments),
        ("settling_time, in 0.1 s,", wire["settling_time"], c.settling_time_range_increments),
        ("mix volume, in 0.1 uL,", wire["mix_volume"], volume),
        ("mix repetitions", mix_cycles, c.mix_cycles_range),
        (
          "mix_position_from_liquid_surface, in 0.1 mm,",
          wire["mix_position_from_liquid_surface"],
          following,
        ),
        ("mix surface following, in 0.1 mm,", wire["mix_surface_following_distance"], following),
        ("mix flow rate, in 0.1 uL/s,", wire["mix_speed"], speed),
        ("limit_curve_index", wire["limit_curve_index"], c.limit_curve_index_range),
      ],
    )
    try:
      await self._unchecked_fw_aspirate(**wire)
    finally:
      await self._record_after_tip_command()

  async def aspirate(
    self,
    resource: Union[Plate, Container, List[Well]],
    volume: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
    lld_mode: Optional[LLDMode] = None,
    flow_rate: Optional[float] = None,
    *,
    hamilton_liquid_class: Optional[HamiltonLiquidClass] = None,
    jet: bool = False,
    blow_out: bool = False,
    piston_volume: Optional[float] = None,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"] = "any",
    search_speed: float = 10.0,
    approach_speed: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    immersion_depth: Optional[float] = None,
    minimum_allowed_z_position_during: Optional[float] = None,
    pre_wetting_volume: Optional[float] = None,
    pre_mix: Optional[Mix] = None,
    mix_position_from_liquid_surface: Optional[float] = None,
    surface_following_distance: float = 0.0,
    auto_surface_following: bool = False,
    second_section_height: Optional[float] = None,
    second_section_ratio: Optional[float] = None,
    settling_time: Optional[float] = None,
    swap_speed: Optional[float] = None,
    pull_out_distance_transport_air: Optional[float] = None,
    transport_air_volume: Optional[float] = None,
    limit_curve_index: Optional[int] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Draw liquid with the whole head, in one `C0 EA`.

    Placed as `mix`. The firmware never searches: OFF draws at `liquid_height` above the cavity
    bottom, the cavity bottom when None; CAPACITIVE draws the blow-out air at the traverse height,
    finds the surface by cLLD, sets the tracker to the volume found, draws under the surface and
    follows it down, and refuses a container without liquid. The floor is the cavity bottom unless
    given. Over a plate of 96 wells each well is booked to the channel over it; a single container
    gives to all 96.
    Booked before the command, committed on success; the head goes to safe Z on a failure.

    Args:
      resource: a plate of 96 wells or of one, its 96 wells, or a container.
      volume: liquid per channel, corrected by a liquid class. One of this and `piston_volume`.
      offset: added to where head channel A1 goes, in mm. Its z is ignored under CAPACITIVE.
      liquid_height: where an OFF draw goes above the cavity bottom, in mm. Refused under
        CAPACITIVE.
      lld_mode: OFF, or CAPACITIVE to find the surface first; the head has no other. OFF when
        None; CAPACITIVE under `auto_surface_following`.
      flow_rate: in uL/s. The class's, else 100.0, when None.
      hamilton_liquid_class: looked up for the tip, water, `jet` and `blow_out` when None.
      jet: whether the later dispense is a jet, for the lookup.
      blow_out: whether the later dispense blows out, for the lookup.
      piston_volume: what the piston draws per channel, in uL, as given.
      lld_sensor: which cLLD sensors trigger, under CAPACITIVE.
      search_speed: of the driver's own search, in mm/s.
      approach_speed: down to that search's start, in mm/s. `z_drive_speed_default` when None.
      blow_out_air_volume: air drawn before the liquid, in uL. The class's, else 0.0, when None.
      immersion_depth: how far into the liquid, in mm; negative is out of it. Under CAPACITIVE,
        `default_aspirate_immersion_depth` when None, and never below the floor.
      minimum_allowed_z_position_during: how low the tips may go, in mm on the deck. The cavity
        bottom when None.
      pre_wetting_volume: drawn and returned first, in uL. The class's, else 0.0, when None.
      pre_mix: mixed before the draw; None for no mixing. Its auto surface following needs a
        surface, as the call's.
      mix_position_from_liquid_surface: mixing depth under the surface, in mm.
      surface_following_distance: how far the tips follow the sinking surface, in mm.
      auto_surface_following: follow by how far the drawn liquid lowers the surface found, or at
        `liquid_height` under OFF; never below the floor. Refused beside a distance.
      second_section_height: height of the container's narrower lower section, in mm.
      second_section_ratio: that section's bottom to top ratio, in tenths.
      settling_time: wait in the liquid, in s. The class's, else 0.0, when None.
      swap_speed: speed of leaving the liquid, in mm/s. The class's, else 100.0, when None.
      pull_out_distance_transport_air: rise before drawing transport air, in mm.
      transport_air_volume: air drawn after the liquid, in uL. The class's, else 0.0, when None.
      limit_curve_index: TADM limit curve, 0 for none.
      minimum_traverse_height_start: tip bottom height to travel at, in mm.
        `configuration.traversal_z_position` when None; safe Z under CAPACITIVE.
      minimum_traverse_height_end: where the tips are left, in mm.
        `configuration.traversal_z_position` when None.

    Raises:
      ValueError: An argument out of range, both or neither of `volume` and `piston_volume`, a
        class beside `piston_volume`, no class for the tip, a liquid height under CAPACITIVE, a
        resource the head cannot work, a piston or tip without room for the draw, or auto surface
        following beside a distance or under OFF without a liquid height.
      RuntimeError: No deck, no tips, the iSWAP not parked, a container without height-volume
        functions under CAPACITIVE or auto surface following, or no liquid found.
      TypeError: A tip that is not a Hamilton tip.
    """
    lld_mode = self._get_lld_mode(
      lld_mode, auto_surface_following, surface_following_distance, liquid_height
    )
    if lld_mode not in (LLDMode.OFF, LLDMode.CAPACITIVE):
      raise ValueError(
        f"the 96-head aspirates with lld_mode OFF or CAPACITIVE, not {lld_mode.name}"
      )
    searched = lld_mode == LLDMode.CAPACITIVE
    auto_mix = pre_mix is not None and pre_mix.auto_surface_following
    if auto_mix and not searched and liquid_height is None:
      raise ValueError("pre_mix's auto_surface_following under lld_mode OFF needs a liquid_height")
    if searched and liquid_height is not None:
      raise ValueError("liquid_height given under CAPACITIVE, whose search finds the surface")
    containers = self._get_containers_under_channels(resource)
    anchor, a1, bottom, top = self._get_target(resource, offset)
    if (searched or auto_surface_following or auto_mix) and (
      not anchor.supports_compute_height_volume_functions()
    ):
      raise RuntimeError(
        f"{anchor.name} has no height-volume functions, so a surface cannot become a volume"
      )
    tips = self._get_mounted_tips()
    tip = next(t for t in tips if t is not None)
    liquid, drawn, hlc = self._get_liquid_and_piston_volume(
      tip, volume, piston_volume, hamilton_liquid_class, jet, blow_out, "drawn"
    )

    def by_class(name: str, given: Optional[float]) -> Optional[float]:
      values = from_class(
        name,
        None if given is None else [given],
        1,
        None if hlc is None else [hlc],
        ASPIRATE_CLASS_ATTRIBUTES,
      )
      return None if values is None else values[0]

    flow_rate = by_class("flow_rates", flow_rate)
    blow_out_air = by_class("blow_out_air_volumes", blow_out_air_volume) or 0.0
    pre_wetting_volume = by_class("pre_wetting_volumes", pre_wetting_volume)
    settling_time = by_class("settling_times", settling_time)
    swap_speed = by_class("swap_speeds", swap_speed)
    transport_air = by_class("transport_air_volumes", transport_air_volume) or 0.0
    # The command draws its blow-out air first; a searched head starts with its tips on the
    # liquid, so its air is drawn beforehand, at the traverse height.
    air_beforehand = blow_out_air if searched else 0.0
    air_in_command = blow_out_air - air_beforehand

    if searched:
      await self._require_tips_feeding(lld_sensor)
    standing = await self.dispensing_drive_request_uL_position()
    # The piston and each tip have to have room for the draws, in the order they come.
    travel = blow_out_air + max(pre_wetting_volume or 0.0, drawn + transport_air)
    room = self.configuration.dispensing_drive_range[1]
    if standing + travel > room:
      raise ValueError(
        f"the 96-head's piston would stand at {standing + travel:.1f} uL, past its drive's "
        f"{room:.1f} uL; it holds {standing:.1f} uL now"
      )
    for t in tips:
      if t is not None and t.tracker.get_used_volume() + travel > t.maximal_volume:
        raise ValueError(
          f"{t.name} would hold {t.tracker.get_used_volume() + travel:.1f} uL, over its "
          f"{t.maximal_volume:.1f} uL"
        )

    channels = [channel for channel, t in enumerate(tips) if t is not None]
    pairs = [
      (
        (anchor if containers is None else containers[channel]).tracker,
        cast(HamiltonTip, tips[channel]).tracker,
      )
      for channel in channels
    ]
    tracking = does_volume_tracking()
    surface = round(a1.z + (liquid_height or 0.0), 2)
    start = minimum_traverse_height_start
    if not searched:
      await self._require_iswap_parked()
    try:
      if searched:
        await self._move_over(a1, minimum_traverse_height_start, None)
        if air_beforehand:
          await self._aspirate_in_place(air_beforehand, flow_rate, minimum_height=bottom)
          standing = round(standing + air_beforehand, 1)
          self.piston_position = standing
        found = await self._search_surface(bottom, top, lld_sensor, search_speed, approach_speed)
        if found is None:
          raise RuntimeError(f"no liquid found in {anchor.name} down to its cavity bottom")
        surface = start = found
        if tracking:
          self._update_volume_from_surface(anchor, surface, bottom)
        floor = (
          bottom if minimum_allowed_z_position_during is None else minimum_allowed_z_position_during
        )
        if immersion_depth is None:
          immersion_depth = min(self.default_aspirate_immersion_depth, max(surface - floor, 0.0))
      # A container under all 96 gives what every channel draws; a well gives one's.
      share = len(pairs) if containers is None else 1
      height = max(round(surface - bottom, 2), 0.0)
      if auto_surface_following:
        floor = (
          bottom if minimum_allowed_z_position_during is None else minimum_allowed_z_position_during
        )
        drop = -self._get_surface_change(anchor, height, -liquid * share)
        surface_following_distance = round(
          max(min(drop, surface - (immersion_depth or 0.0) - floor), 0.0), 1
        )
      if pre_mix is not None:
        pre_mix = self._get_mix_following(pre_mix, anchor, height, share)

      async def send() -> None:
        await self._aspirate_in_one_move(
          Coordinate(a1.x, a1.y, surface),
          round(top + self.search_start_clearance, 2),
          bottom
          if minimum_allowed_z_position_during is None
          else minimum_allowed_z_position_during,
          drawn,
          channel_pattern=[t is not None for t in tips],
          minimum_traverse_height_start=start,
          blow_out_air_volume=air_in_command,
          immersion_depth=immersion_depth,
          pre_wetting_volume=pre_wetting_volume,
          pre_mix=pre_mix,
          mix_position_from_liquid_surface=mix_position_from_liquid_surface,
          flow_rate=flow_rate,
          surface_following_distance=surface_following_distance,
          second_section_height=second_section_height,
          second_section_ratio=second_section_ratio,
          settling_time=settling_time,
          swap_speed=swap_speed,
          pull_out_distance_transport_air=pull_out_distance_transport_air,
          transport_air_volume=transport_air,
          limit_curve_index=limit_curve_index,
          minimum_traverse_height_end=minimum_traverse_height_end,
        )

      await self._book_and_send(
        send,
        pairs,
        [liquid] * len(pairs),
        piston_before=standing,
        piston_after=standing + air_in_command + drawn + transport_air,
        air_before_liquid=air_in_command,
        piston_sign=1,
        tracking=tracking,
        held_less_message="channels %s draw more than their containers hold; the rest is air",
        moved_before_failure_message=(
          "the 96-head drew %.1f uL per channel before the command failed; the model has it"
        ),
      )
    except BaseException as error:
      # Under OFF a refused field is refused before anything moves.
      if searched or not isinstance(error, ValueError):
        await self.move_to_safe_z()
      raise

  # -- dispensing ----------------------------------------------------------------------------------

  @staticmethod
  def _get_dispensing_mode(jet: bool, blow_out: bool, empty: bool) -> int:
    """The firmware's dispensing mode: 0 jet, 1 jet with blow-out, 2 at the surface, 3 at the
    surface with blow-out, 4 empty the tips where they stand."""
    if empty:
      return 4
    if jet:
      return 1 if blow_out else 0
    return 3 if blow_out else 2

  async def _unchecked_fw_dispense(
    self,
    channel_pattern: List[bool],
    dispensing_mode: int,
    x_position: int,
    y_position: int,
    minimum_height: int,
    lld_search_height: int,
    liquid_surface_no_lld: int,
    pull_out_distance_transport_air: int,
    immersion_depth: int,
    immersion_depth_direction: int,
    surface_following_distance: int,
    second_section_height: int,
    second_section_ratio: int,
    minimum_traverse_height_start: int,
    minimum_z_end_position: int,
    dispense_volume: int,
    dispense_speed: int,
    cut_off_speed: int,
    stop_back_volume: int,
    transport_air_volume: int,
    blow_out_air_volume: int,
    lld_mode: int,
    side_touch_off_distance: int,
    clld_sensitivity: int,
    swap_speed: int,
    settling_time: int,
    mix_volume: int,
    mix_cycles: int,
    mix_position_from_liquid_surface: int,
    mix_speed: int,
    mix_surface_following_distance: int,
    limit_curve_index: int,
    tadm_algorithm: bool,
    recording_mode: int,
    read_timeout: int = 300,
  ):
    """Send the head's dispense as it is given, at head channel A1. `C0 ED`.

    Heights and distances in 0.1 mm, volumes in 0.1 uL, speeds in 0.1 per second, times in 0.1 s.
    Nothing is guarded and nothing is recorded.

    Args:
      channel_pattern: 96 flags, the channels that take part (`cw`).
      dispensing_mode: 0 jet part, 1 jet blow-out, 2 surface part, 3 surface blow-out, 4 empty
        tip (`da`).
      x_position: signed; written as its magnitude (`xs`) and its sign (`xd`).
      y_position: `yh`.
      minimum_height: `zm`.
      lld_search_height: `lz`.
      liquid_surface_no_lld: `zt`.
      pull_out_distance_transport_air: `pp`.
      immersion_depth: `iw`.
      immersion_depth_direction: 0 deeper, 1 up out of the liquid (`ix`).
      surface_following_distance: `fh`.
      second_section_height: `zv`.
      second_section_ratio: `zq`.
      minimum_traverse_height_start: `zh`.
      minimum_z_end_position: `ze`.
      dispense_volume: `df`.
      dispense_speed: `dg`.
      cut_off_speed: `es`.
      stop_back_volume: `ev`.
      transport_air_volume: `vt`.
      blow_out_air_volume: `bv`.
      lld_mode: 0 off, 1 capacitive, 2 pressure, 3 dual, 4 Z touch off (`cm`).
      side_touch_off_distance: 0 off (`ej`).
      clld_sensitivity: 1 high to 4 low (`cs`).
      swap_speed: on leaving the liquid (`bs`).
      settling_time: `wh`.
      mix_volume: `hv`.
      mix_cycles: `hc`.
      mix_position_from_liquid_surface: `hp`.
      mix_speed: `hs`.
      mix_surface_following_distance: `mj`.
      limit_curve_index: TADM (`cr`).
      tadm_algorithm: `cj`.
      recording_mode: TADM, 0 none, 1 errors only, 2 all (`cx`).
      read_timeout: how long to wait for the device to answer, in seconds.
    """
    return await self._driver.send_command(
      module="C0",
      command="ED",
      subsystem=self.configuration.module,
      read_timeout=read_timeout,
      da=dispensing_mode,
      xs=f"{abs(x_position):05}",
      xd=0 if x_position >= 0 else 1,
      yh=f"{y_position:04}",
      zm=f"{minimum_height:04}",
      zv=f"{second_section_height:04}",
      zq=f"{second_section_ratio:05}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_no_lld:04}",
      pp=f"{pull_out_distance_transport_air:04}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{surface_following_distance:03}",
      zh=f"{minimum_traverse_height_start:04}",
      ze=f"{minimum_z_end_position:04}",
      df=f"{dispense_volume:05}",
      dg=f"{dispense_speed:04}",
      es=f"{cut_off_speed:04}",
      ev=f"{stop_back_volume:03}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      cm=lld_mode,
      cs=clld_sensitivity,
      ej=f"{side_touch_off_distance:02}",
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mix_volume:05}",
      hc=f"{mix_cycles:02}",
      hp=f"{mix_position_from_liquid_surface:03}",
      mj=f"{mix_surface_following_distance:03}",
      hs=f"{mix_speed:04}",
      cw=self._get_channel_pattern_hex(channel_pattern),
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  async def _dispense_in_one_move(
    self,
    location: Coordinate,
    lld_search_height: float,
    minimum_allowed_z_position_during: float,
    piston_volume: float,
    *,
    jet: bool = False,
    blow_out: bool = False,
    empty: bool = False,
    channel_pattern: Optional[List[bool]] = None,
    minimum_traverse_height_start: Optional[float] = None,
    lld_mode: LLDMode = LLDMode.OFF,
    clld_sensitivity: int = 1,
    side_touch_off_distance: float = 0.0,
    immersion_depth: Optional[float] = None,
    transport_air_volume: Optional[float] = None,
    flow_rate: Optional[float] = None,
    cut_off_speed: Optional[float] = None,
    stop_back_volume: Optional[float] = None,
    surface_following_distance: Optional[float] = None,
    second_section_height: Optional[float] = None,
    second_section_ratio: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    post_mix: Optional[Mix] = None,
    mix_position_from_liquid_surface: Optional[float] = None,
    settling_time: Optional[float] = None,
    swap_speed: Optional[float] = None,
    pull_out_distance_transport_air: Optional[float] = None,
    limit_curve_index: Optional[int] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Dispense with the whole head in one `C0 ED`, every field checked before it is sent.

    Tip bottom heights on the deck in mm, volumes per channel in uL, speeds in uL/s or mm/s, times
    in s. No model update: `dispense` does that.

    Args:
      location: where head channel A1 goes; the z is the liquid surface when no LLD runs.
      lld_search_height: where an LLD search starts.
      minimum_allowed_z_position_during: how low the tips may go.
      piston_volume: what the piston pushes out.
      jet: a jet from above the liquid, rather than at the surface.
      blow_out: the blow-out air follows the liquid out.
      empty: the tips are emptied where they stand, air and all.
      channel_pattern: 96 flags, in channel order. All True when None.
      minimum_traverse_height_start: `configuration.traversal_z_position` when None.
      lld_mode: OFF or CAPACITIVE.
      clld_sensitivity: 1 high to 4 low.
      side_touch_off_distance: sideways move against the wall to shed the drop; 0 for none.
      immersion_depth: how far into the liquid; negative is out of it. 0.0 when None.
      transport_air_volume: air pushed out before the liquid. 0.0 when None.
      flow_rate: 120.0 when None.
      cut_off_speed: the flow the dispense ends at. 5.0 when None.
      stop_back_volume: drawn back after the dispense. 0.0 when None.
      surface_following_distance: how far the tips follow the rising surface. 0.0 when None.
      second_section_height: height of the narrower lower section. 3.2 when None.
      second_section_ratio: that section's bottom to top ratio, in tenths. 618.0 when None.
      blow_out_air_volume: air pushed out after the liquid, in a blow-out mode. 0.0 when None.
      post_mix: mixed after the dispense; None for no mixing.
      mix_position_from_liquid_surface: mixing depth under the surface. 0.0 when None.
      settling_time: wait after the dispense. 0.0 when None.
      swap_speed: speed of leaving the liquid. 10.0 when None.
      pull_out_distance_transport_air: rise before drawing transport air. 10.0 when None.
      limit_curve_index: TADM limit curve, 0 for none. 0 when None.
      minimum_traverse_height_end: `configuration.traversal_z_position` when None.

    Raises:
      ValueError: A value out of range, an unreachable position, a pattern not of 96, or an LLD
        mode the head does not have.
      RuntimeError: If the head reports no tips.
    """
    if lld_mode not in (LLDMode.OFF, LLDMode.CAPACITIVE):
      raise ValueError(f"the 96-head has lld_mode OFF or CAPACITIVE, not {lld_mode.name}")
    pattern = [True] * 96 if channel_pattern is None else list(channel_pattern)
    if len(pattern) != 96:
      raise ValueError(f"channel_pattern must have 96 entries, has {len(pattern)}")
    start, end = self._resolve_tip_command_heights(
      minimum_traverse_height_start, minimum_traverse_height_end
    )
    immersion = immersion_depth or 0.0
    mix_volume = 0.0 if post_mix is None else post_mix.volume
    mix_cycles = 0 if post_mix is None else post_mix.repetitions
    mix_speed = 1.0 if post_mix is None else post_mix.flow_rate
    mix_following = 0.0 if post_mix is None else post_mix.surface_following_distance or 0.0

    def tenths(value: float) -> int:
      return round(value * 10)

    c = self.configuration
    wire: Dict[str, Any] = dict(
      dispensing_mode=self._get_dispensing_mode(jet, blow_out, empty),
      x_position=tenths(location.x),
      y_position=tenths(location.y),
      minimum_height=tenths(minimum_allowed_z_position_during),
      second_section_height=tenths(3.2 if second_section_height is None else second_section_height),
      second_section_ratio=tenths(618.0 if second_section_ratio is None else second_section_ratio),
      lld_search_height=tenths(lld_search_height),
      liquid_surface_no_lld=tenths(location.z),
      pull_out_distance_transport_air=tenths(
        10.0 if pull_out_distance_transport_air is None else pull_out_distance_transport_air
      ),
      immersion_depth=abs(tenths(immersion)),
      immersion_depth_direction=1 if immersion < 0 else 0,
      surface_following_distance=tenths(surface_following_distance or 0.0),
      minimum_traverse_height_start=tenths(start),
      minimum_z_end_position=tenths(end),
      dispense_volume=tenths(piston_volume),
      dispense_speed=tenths(120.0 if flow_rate is None else flow_rate),
      cut_off_speed=tenths(5.0 if cut_off_speed is None else cut_off_speed),
      stop_back_volume=tenths(stop_back_volume or 0.0),
      transport_air_volume=tenths(transport_air_volume or 0.0),
      blow_out_air_volume=tenths(blow_out_air_volume or 0.0),
      lld_mode=lld_mode.value,
      clld_sensitivity=clld_sensitivity,
      side_touch_off_distance=tenths(side_touch_off_distance),
      swap_speed=tenths(10.0 if swap_speed is None else swap_speed),
      settling_time=tenths(settling_time or 0.0),
      mix_volume=tenths(mix_volume),
      mix_cycles=mix_cycles,
      mix_position_from_liquid_surface=tenths(mix_position_from_liquid_surface or 0.0),
      mix_surface_following_distance=tenths(mix_following),
      mix_speed=tenths(mix_speed),
      channel_pattern=pattern,
      limit_curve_index=limit_curve_index or 0,
      tadm_algorithm=False,
      recording_mode=0,
    )
    volume, speed, distance = (
      c.pipetting_volume_range_increments,
      c.pipetting_speed_range_increments,
      c.pipetting_distance_range_increments,
    )
    following = c.surface_following_distance_range_increments
    self._check_pipetting_fields(
      await self._overhang_that_probes(),
      location,
      {
        "the liquid surface": location.z,
        "lld_search_height": lld_search_height,
        "minimum_allowed_z_position_during": minimum_allowed_z_position_during,
        "minimum_traverse_height_start": start,
        "minimum_traverse_height_end": end,
      },
      [
        ("second_section_height, in 0.1 mm,", wire["second_section_height"], distance),
        (
          "second_section_ratio, in tenths,",
          wire["second_section_ratio"],
          c.second_section_ratio_range_increments,
        ),
        (
          "pull_out_distance_transport_air, in 0.1 mm,",
          wire["pull_out_distance_transport_air"],
          distance,
        ),
        (
          "immersion_depth, in 0.1 mm,",
          wire["immersion_depth"],
          c.immersion_depth_range_increments,
        ),
        ("surface_following_distance, in 0.1 mm,", wire["surface_following_distance"], following),
        ("piston_volume, in 0.1 uL,", wire["dispense_volume"], volume),
        ("flow_rate, in 0.1 uL/s,", wire["dispense_speed"], speed),
        ("cut_off_speed, in 0.1 uL/s,", wire["cut_off_speed"], speed),
        (
          "stop_back_volume, in 0.1 uL,",
          wire["stop_back_volume"],
          c.stop_back_volume_range_increments,
        ),
        (
          "transport_air_volume, in 0.1 uL,",
          wire["transport_air_volume"],
          c.transport_air_volume_range_increments,
        ),
        ("blow_out_air_volume, in 0.1 uL,", wire["blow_out_air_volume"], volume),
        ("clld_sensitivity", clld_sensitivity, c.lld_sensitivity_range),
        (
          "side_touch_off_distance, in 0.1 mm,",
          wire["side_touch_off_distance"],
          c.side_touch_off_distance_range_increments,
        ),
        ("swap_speed, in 0.1 mm/s,", wire["swap_speed"], c.swap_speed_range_increments),
        ("settling_time, in 0.1 s,", wire["settling_time"], c.settling_time_range_increments),
        ("mix volume, in 0.1 uL,", wire["mix_volume"], volume),
        ("mix repetitions", mix_cycles, c.mix_cycles_range),
        (
          "mix_position_from_liquid_surface, in 0.1 mm,",
          wire["mix_position_from_liquid_surface"],
          following,
        ),
        ("mix surface following, in 0.1 mm,", wire["mix_surface_following_distance"], following),
        ("mix flow rate, in 0.1 uL/s,", wire["mix_speed"], speed),
        ("limit_curve_index", wire["limit_curve_index"], c.limit_curve_index_range),
      ],
    )
    try:
      await self._unchecked_fw_dispense(**wire)
    finally:
      await self._record_after_tip_command()

  async def dispense(
    self,
    resource: Union[Plate, Container, List[Well]],
    volume: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
    lld_mode: Optional[LLDMode] = None,
    flow_rate: Optional[float] = None,
    *,
    hamilton_liquid_class: Optional[HamiltonLiquidClass] = None,
    jet: bool = False,
    blow_out: bool = False,
    empty: bool = False,
    piston_volume: Optional[float] = None,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"] = "any",
    search_speed: float = 10.0,
    approach_speed: Optional[float] = None,
    side_touch_off_distance: float = 0.0,
    immersion_depth: Optional[float] = None,
    minimum_allowed_z_position_during: Optional[float] = None,
    transport_air_volume: Optional[float] = None,
    cut_off_speed: Optional[float] = None,
    stop_back_volume: Optional[float] = None,
    surface_following_distance: float = 0.0,
    auto_surface_following: bool = False,
    second_section_height: Optional[float] = None,
    second_section_ratio: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    post_mix: Optional[Mix] = None,
    mix_position_from_liquid_surface: Optional[float] = None,
    settling_time: Optional[float] = None,
    swap_speed: Optional[float] = None,
    pull_out_distance_transport_air: Optional[float] = None,
    limit_curve_index: Optional[int] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Dispense liquid with the whole head, in one `C0 ED`.

    Placed as `mix`. The firmware never searches: OFF dispenses at `liquid_height` above the
    cavity bottom, the cavity bottom when None; CAPACITIVE finds the surface by cLLD, sets the
    tracker to the volume found and dispenses at the surface, following it up. The floor is the
    cavity bottom unless given. `jet`, `blow_out` and `empty` pick the firmware's mode. Booked as
    `aspirate`; the head goes to safe Z on a failure.

    Args:
      resource: a plate of 96 wells or of one, its 96 wells, or a container.
      volume: liquid per channel, corrected by a liquid class. One of this and `piston_volume`.
      offset: added to where head channel A1 goes, in mm. Its z is ignored under CAPACITIVE.
      liquid_height: where an OFF dispense goes above the cavity bottom, in mm. Refused under
        CAPACITIVE.
      lld_mode: OFF, or CAPACITIVE to find the surface first; the head has no other. OFF when
        None; CAPACITIVE under `auto_surface_following`.
      flow_rate: in uL/s. The class's, else 120.0, when None.
      hamilton_liquid_class: looked up for the tip, water, `jet` and `blow_out` when None.
      jet: a jet from above the liquid, rather than at the surface.
      blow_out: the blow-out air follows the liquid out.
      empty: the tips are emptied where they stand; the model books what they hold.
      piston_volume: what the piston pushes out per channel, in uL, as given.
      lld_sensor: which cLLD sensors trigger, under CAPACITIVE.
      search_speed: of the driver's own search, in mm/s.
      approach_speed: down to that search's start, in mm/s. `z_drive_speed_default` when None.
      side_touch_off_distance: sideways move against the wall to shed the drop, in mm.
      immersion_depth: how far into the liquid, in mm; negative is out of it.
      minimum_allowed_z_position_during: how low the tips may go, in mm on the deck. The cavity
        bottom when None.
      transport_air_volume: air pushed out before the liquid, in uL. The class's, else 0.0, when
        None.
      cut_off_speed: the flow the dispense ends at, in uL/s. 5.0 when None.
      stop_back_volume: drawn back after the dispense, in uL. The class's, else 0.0, when None.
      surface_following_distance: how far the tips follow the rising surface, in mm.
      auto_surface_following: follow by how far the dispensed liquid raises the surface found, or
        at `liquid_height` under OFF; never above the top. Refused beside a distance.
      second_section_height: height of the container's narrower lower section, in mm.
      second_section_ratio: that section's bottom to top ratio, in tenths.
      blow_out_air_volume: air pushed out after the liquid in a blow-out mode, in uL. The
        class's, else 0.0, when None.
      post_mix: mixed after the dispense; None for no mixing. Its auto surface following needs
        a surface, as the call's.
      mix_position_from_liquid_surface: mixing depth under the surface, in mm.
      settling_time: wait after the dispense, in s. The class's, else 0.0, when None.
      swap_speed: speed of leaving the liquid, in mm/s. The class's, else 10.0, when None.
      pull_out_distance_transport_air: rise before drawing transport air, in mm.
      limit_curve_index: TADM limit curve, 0 for none.
      minimum_traverse_height_start: tip bottom height to travel at, in mm.
        `configuration.traversal_z_position` when None; safe Z under CAPACITIVE.
      minimum_traverse_height_end: where the tips are left, in mm.
        `configuration.traversal_z_position` when None.

    Raises:
      ValueError: An argument out of range, both or neither of `volume` and `piston_volume`, a
        class beside `piston_volume`, no class for the tip, a liquid height under CAPACITIVE, a
        resource the head cannot work, a piston without the travel, a container without room, or
        auto surface following beside a distance or under OFF without a liquid height.
      RuntimeError: No deck, no tips, the iSWAP not parked, a container without height-volume
        functions under CAPACITIVE or auto surface following, no liquid found, or a container
        found without the room.
      TypeError: A tip that is not a Hamilton tip.
    """
    lld_mode = self._get_lld_mode(
      lld_mode, auto_surface_following, surface_following_distance, liquid_height
    )
    if lld_mode not in (LLDMode.OFF, LLDMode.CAPACITIVE):
      raise ValueError(
        f"the 96-head dispenses with lld_mode OFF or CAPACITIVE, not {lld_mode.name}"
      )
    searched = lld_mode == LLDMode.CAPACITIVE
    auto_mix = post_mix is not None and post_mix.auto_surface_following
    if auto_mix and not searched and liquid_height is None:
      raise ValueError("post_mix's auto_surface_following under lld_mode OFF needs a liquid_height")
    if searched and liquid_height is not None:
      raise ValueError("liquid_height given under CAPACITIVE, whose search finds the surface")
    containers = self._get_containers_under_channels(resource)
    anchor, a1, bottom, top = self._get_target(resource, offset)
    if (searched or auto_surface_following or auto_mix) and (
      not anchor.supports_compute_height_volume_functions()
    ):
      raise RuntimeError(
        f"{anchor.name} has no height-volume functions, so a surface cannot become a volume"
      )
    tips = self._get_mounted_tips()
    tip = next(t for t in tips if t is not None)
    asked, pushed, hlc = self._get_liquid_and_piston_volume(
      tip, volume, piston_volume, hamilton_liquid_class, jet, blow_out, "pushed out"
    )

    def by_class(name: str, given: Optional[float]) -> Optional[float]:
      values = from_class(
        name,
        None if given is None else [given],
        1,
        None if hlc is None else [hlc],
        DISPENSE_CLASS_ATTRIBUTES,
      )
      return None if values is None else values[0]

    flow_rate = by_class("flow_rates", flow_rate)
    transport_air = by_class("transport_air_volumes", transport_air_volume) or 0.0
    stop_back = by_class("stop_back_volumes", stop_back_volume) or 0.0
    blow_out_air = by_class("blow_out_air_volumes", blow_out_air_volume) or 0.0
    settling_time = by_class("settling_times", settling_time)
    swap_speed = by_class("swap_speeds", swap_speed)
    mode = self._get_dispensing_mode(jet, blow_out, empty)

    channels = [channel for channel, t in enumerate(tips) if t is not None]
    tipped = [cast(HamiltonTip, tips[channel]) for channel in channels]
    # An empty pushes out whatever each tip holds, whatever volume was asked.
    liquid = [t.tracker.get_used_volume() if empty else asked for t in tipped]
    givers = [anchor] * len(channels) if containers is None else [containers[c] for c in channels]

    def check_room(error: Type[Exception]) -> None:
      needed: Dict[int, float] = {}
      for container, moved in zip(givers, liquid):
        needed[id(container)] = needed.get(id(container), 0.0) + moved
      for container in {id(c): c for c in givers}.values():
        if needed[id(container)] > container.tracker.get_free_volume() + 1e-6:
          raise error(
            f"{container.name} has room for {container.tracker.get_free_volume():.1f} uL, not "
            f"the {needed[id(container)]:.1f} uL the 96-head is to dispense"
          )

    standing = await self.dispensing_drive_request_uL_position()
    travel = transport_air + pushed + (blow_out_air if mode in (1, 3) else 0.0)
    if mode != 4 and standing - travel < -0.05:
      raise ValueError(
        f"the 96-head's piston holds {standing:.1f} uL of travel for the {travel:.1f} uL it is to "
        "push out"
      )
    check_room(ValueError)
    if searched:
      await self._require_tips_feeding(lld_sensor)

    pairs = [(t.tracker, container.tracker) for t, container in zip(tipped, givers)]
    tracking = does_volume_tracking()
    surface = round(a1.z + (liquid_height or 0.0), 2)
    start = minimum_traverse_height_start
    if not searched:
      await self._require_iswap_parked()
    try:
      if searched:
        await self._move_over(a1, minimum_traverse_height_start, None)
        found = await self._search_surface(bottom, top, lld_sensor, search_speed, approach_speed)
        if found is None:
          raise RuntimeError(f"no liquid found in {anchor.name} down to its cavity bottom")
        surface = start = found
        if tracking:
          self._update_volume_from_surface(anchor, surface, bottom)
          # The search may have found more liquid than the model had.
          check_room(RuntimeError)
      height = max(round(surface - bottom, 2), 0.0)
      if auto_surface_following:
        # A container under all 96 takes what every channel dispenses; a well takes one's.
        given = sum(v for container, v in zip(givers, liquid) if container is anchor)
        rise = self._get_surface_change(anchor, height, given)
        surface_following_distance = round(max(min(rise, top - surface), 0.0), 1)
      if post_mix is not None:
        share = len(channels) if containers is None else 1
        post_mix = self._get_mix_following(post_mix, anchor, height, share)

      async def send() -> None:
        await self._dispense_in_one_move(
          Coordinate(a1.x, a1.y, surface),
          round(top + self.search_start_clearance, 2),
          bottom
          if minimum_allowed_z_position_during is None
          else minimum_allowed_z_position_during,
          pushed,
          jet=jet,
          blow_out=blow_out,
          empty=empty,
          channel_pattern=[t is not None for t in tips],
          minimum_traverse_height_start=start,
          side_touch_off_distance=side_touch_off_distance,
          immersion_depth=immersion_depth,
          transport_air_volume=transport_air,
          flow_rate=flow_rate,
          cut_off_speed=cut_off_speed,
          stop_back_volume=stop_back,
          surface_following_distance=surface_following_distance,
          second_section_height=second_section_height,
          second_section_ratio=second_section_ratio,
          blow_out_air_volume=blow_out_air,
          post_mix=post_mix,
          mix_position_from_liquid_surface=mix_position_from_liquid_surface,
          settling_time=settling_time,
          swap_speed=swap_speed,
          pull_out_distance_transport_air=pull_out_distance_transport_air,
          limit_curve_index=limit_curve_index,
          minimum_traverse_height_end=minimum_traverse_height_end,
        )

      left = 0.0 if mode == 4 else max(standing - travel, 0.0)
      await self._book_and_send(
        send,
        pairs,
        liquid,
        piston_before=standing,
        piston_after=left + stop_back,
        air_before_liquid=transport_air,
        piston_sign=-1,
        tracking=tracking,
        held_less_message="channels %s dispense more than their tips hold; the rest is air",
        moved_before_failure_message=(
          "the 96-head gave %.1f uL per channel before the command failed; the model has it"
        ),
      )
    except BaseException as error:
      # Under OFF a refused field is refused before anything moves.
      if searched or not isinstance(error, ValueError):
        await self.move_to_safe_z()
      raise

  # -- mixing --------------------------------------------------------------------------------------

  async def mix(
    self,
    resource: Union[Plate, Container, List[Well]],
    mix: Mix,
    offset: Optional[Coordinate] = None,
    *,
    minimum_traverse_height_start: Optional[float] = None,
    lld_mode: Optional[LLDMode] = None,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"] = "any",
    search_speed: Optional[float] = None,
    descent_speed: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    mix_position_from_liquid_surface: Optional[float] = None,
    swap_speed: Optional[float] = None,
    settling_time: float = 0.0,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Mix in place with the whole head, then `mix.repetitions` strokes.

    Over a plate of many wells head channel A1 goes over well A1; over a single container, or a
    plate of one well, the channel array is centred over it. Each draw follows the surface down by
    `mix.surface_following_distance`, or by what one draw lowers the surface found under
    `mix.auto_surface_following`, and each expel follows it back up, so the tips do not drift; no
    stroke goes below the cavity bottom. OFF mixes at `offset` z above the cavity bottom, with
    the blowout air drawn and expelled over the well. CAPACITIVE draws the air at the traverse
    height, finds the surface by cLLD, sets the tracker to the volume found, mixes
    `mix_position_from_liquid_surface` below it and expels the air once risen.

    Args:
      resource: a plate (well A1, or its one well), a container, or wells (the first).
      mix: volume, repetitions, flow rate and surface following distance.
      offset: added to where head channel A1 goes, in mm. Its z is ignored under CAPACITIVE.
      minimum_traverse_height_start: tip bottom height before the XY move, in mm. Safe Z when None.
      lld_mode: OFF, or CAPACITIVE to find the surface first; the head has no other. OFF when
        None; CAPACITIVE under `mix.auto_surface_following`, which OFF refuses.
      lld_sensor: which cLLD sensors trigger, under CAPACITIVE.
      search_speed: in mm/s, under CAPACITIVE. `default_clld_search_speed` when None.
      descent_speed: to just above the well, in mm/s. `default_mix_descent_speed` when None.
      blow_out_air_volume: air drawn before mixing and expelled after, in uL; 0 skips it.
        `default_mix_blow_out_air_volume` when None.
      mix_position_from_liquid_surface: how far below the surface found the tips mix, in mm, under
        CAPACITIVE. `default_mix_position_from_liquid_surface` when None.
      swap_speed: into and out of the well, in mm/s. `default_mix_swap_speed` when None.
      settling_time: wait after the last stroke, in s.
      minimum_traverse_height_end: tip bottom height after mixing, in mm. Safe Z when None.

    Raises:
      ValueError: If an argument is out of range.
      RuntimeError: If the head carries no tips, the iSWAP is not parked, the driver was given no
        deck, or a CAPACITIVE search found no liquid.
    """
    if lld_mode is None:
      lld_mode = LLDMode.CAPACITIVE if mix.auto_surface_following else LLDMode.OFF
    if lld_mode not in (LLDMode.OFF, LLDMode.CAPACITIVE):
      raise ValueError(f"the 96-head mixes with lld_mode OFF or CAPACITIVE, not {lld_mode.name}")
    if mix.auto_surface_following and lld_mode == LLDMode.OFF:
      raise ValueError("mix's auto_surface_following under lld_mode OFF has no surface to follow")
    if settling_time < 0:
      raise ValueError(f"settling_time must be at least 0, is {settling_time}")
    if descent_speed is None:
      descent_speed = self.default_mix_descent_speed
    if blow_out_air_volume is None:
      blow_out_air_volume = self.default_mix_blow_out_air_volume
    if mix_position_from_liquid_surface is None:
      mix_position_from_liquid_surface = self.default_mix_position_from_liquid_surface
    if mix_position_from_liquid_surface < 0:
      raise ValueError(
        "mix_position_from_liquid_surface must be at least 0, "
        f"is {mix_position_from_liquid_surface}"
      )
    if swap_speed is None:
      swap_speed = self.default_mix_swap_speed
    anchor, a1, bottom, z_top = self._get_target(resource, offset)
    if mix.auto_surface_following and not anchor.supports_compute_height_volume_functions():
      raise RuntimeError(
        f"{anchor.name} has no height-volume functions, so a surface cannot become a volume"
      )
    # A container under all 96 gives what every channel draws; a well gives one's.
    channels = sum(t is not None for t in self._get_mounted_tips())
    per_draw = mix.volume * (
      channels if self._get_containers_under_channels(resource) is None else 1
    )

    def get_following(height: float) -> float:
      """How far each draw follows the surface down from `height` mm over the cavity bottom."""
      if not mix.auto_surface_following:
        return mix.surface_following_distance
      return max(-self._get_surface_change(anchor, max(height, 0.0), -per_draw), 0.0)

    following = 0.0
    await self._move_over(a1, minimum_traverse_height_start, descent_speed)

    async def strokes() -> None:
      for _ in range(mix.repetitions):
        await self._aspirate_in_place(
          mix.volume, mix.flow_rate, surface_following_distance=following, minimum_height=bottom
        )
        await self._dispense_in_place(
          mix.volume, mix.flow_rate, surface_following_distance=following, minimum_height=bottom
        )
      if settling_time:
        await asyncio.sleep(settling_time)

    async def rise() -> None:
      if minimum_traverse_height_end is None:
        await self.move_to_safe_z()
      else:
        await self.move_tool_bottom_to_z_position(minimum_traverse_height_end, speed=descent_speed)

    if lld_mode == LLDMode.OFF:
      following = mix.surface_following_distance
      start = a1.z + following
      swap_start = z_top + self.mix_swap_start_clearance
      try:
        await self.move_tool_bottom_to_z_position(swap_start, speed=descent_speed)
        if blow_out_air_volume:
          await self._aspirate_in_place(blow_out_air_volume, mix.flow_rate, minimum_height=bottom)
        await self.move_tool_bottom_to_z_position(start, speed=swap_speed)
        await strokes()
        await self.move_tool_bottom_to_z_position(swap_start, speed=swap_speed)
        if blow_out_air_volume:
          await self._dispense_in_place(blow_out_air_volume, mix.flow_rate)
      except STARFirmwareError:
        await self.move_to_safe_z()
        raise
      await rise()
      return

    # CAPACITIVE: the air in the tips before they reach the liquid, then down once.
    try:
      if blow_out_air_volume:
        await self._aspirate_in_place(blow_out_air_volume, mix.flow_rate, minimum_height=bottom)
      found = await self._search_surface(bottom, z_top, lld_sensor, search_speed)
      if found is None:
        raise RuntimeError(f"no liquid found in {anchor.name} down to its cavity bottom")
      surface = found
      if anchor.supports_compute_height_volume_functions():
        anchor.tracker.set_volume(anchor.compute_volume_from_height(max(surface - bottom, 0.0)))
      start = max(round(surface - mix_position_from_liquid_surface, 2), bottom)
      following = round(min(get_following(surface - bottom), start - bottom), 1)
      await self.move_tool_bottom_to_z_position(start, speed=swap_speed)
      await strokes()
    except BaseException:
      await self.move_to_safe_z()
      raise
    await rise()
    if blow_out_air_volume:
      await self._dispense_in_place(blow_out_air_volume, mix.flow_rate)
