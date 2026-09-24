"""The 96-head: the block of 96 pipettes that works a whole plate at once."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, cast

from pylabrobot.hamilton.star.driver.errors import STARFirmwareError
from pylabrobot.hamilton.star.driver.features.head import Head, HeadConfiguration
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack

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
  dispensing_drive_speed_firmware_reported: Optional[float] = None
  dispensing_drive_acceleration_firmware_reported: Optional[float] = None
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
  def dispensing_drive_speed_default(self) -> float:
    """Dispensing-drive default speed (uL/s); constant across firmware."""
    if self.dispensing_drive_speed_firmware_reported is not None:
      return self.dispensing_drive_speed_firmware_reported
    return 261.1

  @property
  def dispensing_drive_acceleration_range(self) -> Tuple[float, float]:
    """Dispensing-drive acceleration window (uL/s2); its max is the default 2013 firmware raised."""
    max_inc = 900000 if self.firmware_year >= 2010 else 150000
    return (
      self.dispensing_drive_increments_to_uL(5000),
      self.dispensing_drive_increments_to_uL(max_inc),
    )

  @property
  def dispensing_drive_acceleration_default(self) -> float:
    """Dispensing-drive default acceleration (uL/s2); 2013 firmware raised it."""
    if self.dispensing_drive_acceleration_firmware_reported is not None:
      return self.dispensing_drive_acceleration_firmware_reported
    increments = 900000 if self.firmware_year >= 2010 else 150000
    return self.dispensing_drive_increments_to_uL(increments)

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

  def __init__(self, driver: "STARDriver", configuration: Optional[Head96Configuration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the head's device facts. Defaults to `Head96Configuration()`.
    """
    super().__init__(driver, configuration or Head96Configuration())

  # ----------------------------------------
  # Setup
  # ----------------------------------------

  # -- discovery ---------------------------------------------------------------------------------

  def _apply_firmware_generation(self) -> None:
    """Put the pre-2013 encodings in place on a head that runs them.

    Those heads count every drive's acceleration in thousands of increments per second squared and
    write it in a narrower field; from 2013 the same parameter is single increments in a wider one.
    Nothing else about the head announces which it is, so the firmware date decides.
    """
    c = self.configuration
    if c.firmware_year >= 2010:
      return
    c.y_drive_acceleration_mm_per_increment = c.y_drive_mm_per_increment * 1000
    c.z_drive_acceleration_mm_per_increment = c.z_drive_mm_per_increment * 1000
    c.drive_parameters = {"yv": 5, "yr": 3, "zv": 5, "zr": 3, "dv": 5, "dr": 4, "sv": 5, "sr": 3}
    c.z_acceleration_range_increments = (5, 100)

  async def discover(self):
    """Read what head this is, then take its dispensing and squeezer defaults from the head."""
    await super().discover()
    c = self.configuration
    c.dispensing_drive_speed_firmware_reported = await self._reported_drive_parameter("dv")
    c.dispensing_drive_acceleration_firmware_reported = await self._reported_drive_parameter("dr")
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

  # -- dispensing drive --------------------------------------------------------------------------

  async def move_dispensing_drive_to_position(
    self,
    volume: float,
    speed: Optional[float] = None,
    stop_speed: float = 0.0,
    acceleration: Optional[float] = None,
    current_limit: int = 15,
    read_timeout: int = 30,
  ):
    """Move the dispensing drive to an absolute piston position. This moves it.

    Args:
      volume: where to send the piston, as the volume it would hold, in uL.
      speed: how fast, in uL/s. Defaults to `configuration.dispensing_drive_speed_default`.
      stop_speed: what to slow to at the end, in uL/s.
      acceleration: how hard, in uL/s2. Defaults to
        `configuration.dispensing_drive_acceleration_default`.
      current_limit: the motor current limit.
      read_timeout: how long to wait for the device to answer, in seconds.

    Raises:
      ValueError: If an argument is outside what the drive accepts.
    """
    c = self.configuration
    if speed is None:
      speed = c.dispensing_drive_speed_default
    if acceleration is None:
      acceleration = c.dispensing_drive_acceleration_default

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

    return await self._driver.send_command(
      module=c.module,
      command="DQ",
      dq=f"{c.dispensing_drive_uL_to_increments(volume):05}",
      dv=f"{c.dispensing_drive_uL_to_increments(speed):05}",
      du=f"{c.dispensing_drive_uL_to_increments(stop_speed):05}",
      dr=f"{c.dispensing_drive_uL_to_increments(acceleration):06}",
      dw=f"{current_limit:02}",
      read_timeout=read_timeout,
    )

  # ----------------------------------------
  # Tip pickup and drop
  # ----------------------------------------

  # -- where the head goes -----------------------------------------------------------------------

  def _position_centred_in(self, resource: Resource) -> Coordinate:
    """Where head channel A1 lands with the head centred over a resource, in deck mm.

    The head is rigid and the resource is whatever it is being pointed at, so the array is put in
    the middle of it and A1 falls half a channel pitch in from the array's own corner.

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
      location.x + (resource.get_size_x() - c.channel_array_size_x) / 2 + c.channel_pitch / 2,
      location.y + (resource.get_size_y() - c.channel_array_size_y) / 2 + c.channel_pitch / 2,
      location.z,
    )

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

  async def _record_after_tip_command(self) -> None:
    """Read back where a tip command left the arm and the head, and record it."""
    if self.arm is not None:
      await self.arm.request_position()
    await self.request_y_position()
    await self.request_z_position()

  # -- pickup ------------------------------------------------------------------------------------

  async def pick_up_tips(
    self,
    tip_rack: TipRack,
    offset: Optional[Coordinate] = None,
    tip_pickup_method: Literal["from_rack", "from_waste", "full_blowout"] = "from_rack",
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
  ) -> None:
    """Pick up a rack of tips on the whole head, as legacy's `pick_up_tips96`. `C0 EP`.

    Head channel A1 goes to the centre of spot A1, at the spot's Z. Once the device has picked them
    up, the tip in each spot is mounted on the shaft of the channel with the spot's index.

    Args:
      tip_rack: a 96 tip rack. Spots without a tip give none.
      offset: added to spot A1's centre, in mm.
      tip_pickup_method: `from_rack` sends the dispensing drive down first, since the device does
        not; `from_waste` and `full_blowout` move the plunger up before mounting.
      minimum_height_command_end: in mm. `configuration.traversal_z_position` when None.
      minimum_traverse_height_start: in mm.
        `configuration.traversal_z_position` when None.

    Raises:
      ValueError: If the rack does not have 96 spots or holds no tips, or a position cannot be
        reached.
      TypeError: If its tips are not Hamilton tips.
      RuntimeError: If the driver was given no deck.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    if tip_rack.num_items != 96:
      raise ValueError("Tip rack must have 96 tips")
    tips = [
      spot.tip_for_pickup() if not spot.tracks_tips or spot.tip is not None else None
      for spot in tip_rack.get_all_items()
    ]
    prototypical_tip = next((tip for tip in tips if tip is not None), None)
    if prototypical_tip is None:
      raise ValueError("No tips found in the tip rack.")
    if not isinstance(prototypical_tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")
    tip_type_index = await self._driver.get_or_assign_tip_type_index(prototypical_tip)

    location = tip_rack.get_item("A1").get_location_wrt(deck, x="c", y="c", z="b") + (
      offset or Coordinate.zero()
    )
    traverse_z, end_z = self._resolve_tip_command_heights(
      minimum_traverse_height_start, minimum_height_command_end
    )
    self._check_tip_command(location, traverse_z, end_z, skip_z=True)

    if tip_pickup_method == "from_rack":
      await self.move_dispensing_drive_to_position(
        self.configuration.dispensing_drive_position_before_rack_pickup
      )
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

    if self.resource is not None:
      for shaft, tip in zip(self.resource.get_all_items(), tips):
        if tip is not None:
          shaft.mount_tip(tip)
    await self._record_after_tip_command()

  # -- drop --------------------------------------------------------------------------------------

  async def drop_tips(
    self,
    resource: Resource,
    offset: Optional[Coordinate] = None,
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
  ) -> None:
    """Drop the head's tips into a tip rack or anywhere else, as legacy's `drop_tips96`. `C0 ER`.

    Into a tip rack, head channel A1 goes to the centre of spot A1, at the spot's Z, and each
    channel's tip goes into the spot with its index. Anywhere else, the head is centred over the
    resource, and the tips belong to nothing afterwards.

    Args:
      resource: a 96 tip rack, or anything else, such as the trash.
      offset: added to where the head goes, in mm.
      minimum_height_command_end: in mm. `configuration.traversal_z_position` when None.
      minimum_traverse_height_start: in mm.
        `configuration.traversal_z_position` when None.

    Raises:
      ValueError: If a tip rack does not have 96 spots, or a position cannot be reached.
      RuntimeError: If the driver was given no deck.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    if isinstance(resource, TipRack):
      if resource.num_items != 96:
        raise ValueError("Tip rack must have 96 tips")
      location = resource.get_item("A1").get_location_wrt(deck, x="c", y="c", z="b")
    else:
      location = self._position_centred_in(resource)
    location += offset or Coordinate.zero()
    traverse_z, end_z = self._resolve_tip_command_heights(
      minimum_traverse_height_start, minimum_height_command_end
    )
    self._check_tip_command(location, traverse_z, end_z, skip_z=True)

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

    if self.resource is not None:
      for i, shaft in enumerate(self.resource.get_all_items()):
        if not shaft.has_tip():
          continue
        tip = shaft.release_tip()
        if isinstance(resource, TipRack) and isinstance(tip, Tip):
          spot = resource.get_item(i)
          if spot.tracks_tips:
            spot.assign_tip(tip)
    await self._record_after_tip_command()

  # ----------------------------------------
  # Probing
  # ----------------------------------------

  # -- z probing (capacitive) --------------------------------------------------------------------

  async def _unchecked_fw_probe_z_using_clld(
    self,
    end_position: int,
    start_position: int,
    post_detection_distance: int,
    post_detection_trajectory: Literal[0, 1],
    lld_mode: Optional[int],
    detection_edge: int,
    detection_drop: int,
    approach_speed: int,
    search_speed: int,
    acceleration: int,
    current_limit: int,
    immersion_mode: Optional[Literal[0, 1]],
  ):
    """Lower the head until its cLLD triggers, as given, in Z increments. `H0 ZL`.

    Args:
      end_position: stop disc height it goes no lower than (`zh`).
      start_position: stop disc height the search starts from (`zc`).
      post_detection_distance: how far it moves after detection (`zi`).
      post_detection_trajectory: 0 down, 1 up (`zj`).
      lld_mode: which sensors trigger, 0 to 3 (`lm`); None leaves it out.
      detection_edge: edge steepness, 0 to 1023 (`gt`).
      detection_drop: offset after the edge, 0 to 1023 (`gl`).
      approach_speed: to the search start (`zv`).
      search_speed: during the search (`zl`).
      acceleration: in the drive's acceleration increments (`zr`).
      current_limit: motor current limit (`zw`).
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
    """Request the Z-drive position the last cLLD search detected at. `H0 RH`.

    Returns:
      The position, in mm.
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

  async def probe_z_using_clld(
    self,
    *,
    search_start_position: Optional[float] = None,
    search_end_position: Optional[float] = None,
    tip_overhang: Optional[float] = None,
    approach_speed: Optional[float] = None,
    search_speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    lld_sensor: Literal["A1 or B2", "G11 or H12", "any", "all"] = "any",
    detection_edge: Optional[int] = None,
    detection_drop: Optional[int] = None,
    post_detection_distance: Optional[float] = None,
    limit_immersion_to_search_end: bool = False,
    current_limit: Optional[int] = None,
    move_to_safe_z_after: bool = False,
  ) -> float:
    """Lower the head's tips until its cLLD triggers, and read the tip bottom height it detected at.

    Args:
      search_start_position: tip bottom height to search from, in mm. The highest when None.
      search_end_position: lowest tip bottom height, in mm. The lowest when None.
      tip_overhang: tips below the stop disc, in mm. Measured when None.
      approach_speed: to the search start, in mm/s. `z_drive_speed_default` when None.
      search_speed: in mm/s. `default_clld_search_speed` when None.
      acceleration: in mm/s2. `default_clld_acceleration` when None.
      lld_sensor: which cLLD sensors trigger.
      detection_edge: 0 to 1023. `default_clld_detection_edge` when None.
      detection_drop: 0 to 1023. `default_clld_detection_drop` when None.
      post_detection_distance: in mm, positive up. `default_clld_post_detection_distance` when None.
      limit_immersion_to_search_end: never go below `search_end_position` after detection.
      current_limit: `z_drive_current_limit_default` when None.
      move_to_safe_z_after: raise the head to safe Z afterwards.

    Returns:
      The tip bottom height at detection, in mm.

    Raises:
      ValueError: If an argument is out of range, or a sensor is chosen on 2008 firmware.
      RuntimeError: If the channels feeding the sensor carry no tips.
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
    if approach_speed is None:
      approach_speed = c.z_drive_speed_default
    if search_speed is None:
      search_speed = self.default_clld_search_speed
    if acceleration is None:
      acceleration = self.default_clld_acceleration
    if detection_edge is None:
      detection_edge = self.default_clld_detection_edge
    if detection_drop is None:
      detection_drop = self.default_clld_detection_drop
    if post_detection_distance is None:
      post_detection_distance = self.default_clld_post_detection_distance
    if current_limit is None:
      current_limit = c.z_drive_current_limit_default
    if c.firmware_year < 2010 and not 0 <= current_limit <= 7:
      raise ValueError(
        f"current_limit must be between 0 and 7 on 2008 firmware, is {current_limit}"
      )
    for checked, name in ((detection_edge, "detection_edge"), (detection_drop, "detection_drop")):
      if not 0 <= checked <= 1023:
        raise ValueError(f"{name} must be between 0 and 1023, is {checked}")
    distance = c.z_drive_mm_to_increments(abs(post_detection_distance))
    if distance > 9999:
      raise ValueError(
        f"post_detection_distance must be within {c.z_drive_increments_to_mm(9999)} mm, "
        f"is {post_detection_distance}"
      )

    await self._require_tips_feeding(lld_sensor)
    if tip_overhang is None:
      if not await self.request_tip_presence():
        raise RuntimeError("the head reports no tips, so there is no overhang to measure")
      reference = await self.request_z_position()
      tip_overhang = round(reference - (await self.request_location()).z, 1)
    if search_start_position is None:
      search_start_position = round(c.z_range[1] - tip_overhang, 2)
    if search_end_position is None:
      search_end_position = round(max(c.z_range[0] - tip_overhang, c.min_tool_bottom_z), 2)
    if search_end_position < c.min_tool_bottom_z:
      raise ValueError(
        f"search_end_position must be at least {c.min_tool_bottom_z}, is {search_end_position}"
      )
    start = round(search_start_position + tip_overhang, 2)
    end = round(search_end_position + tip_overhang, 2)
    self._check_move("z", start, approach_speed, acceleration, current_limit)
    self._check_move("z", end, search_speed, acceleration, current_limit)
    ramp = c.z_drive_acceleration_mm_to_increments(acceleration)
    if c.firmware_year < 2010:
      # The search takes the 2008 thousands rounded down.
      ramp = c.z_drive_mm_to_increments(acceleration) // 1000

    try:
      await self._unchecked_fw_probe_z_using_clld(
        end_position=c.z_drive_mm_to_increments(end),
        start_position=c.z_drive_mm_to_increments(start),
        post_detection_distance=distance,
        post_detection_trajectory=1 if post_detection_distance >= 0 else 0,
        lld_mode=lld_mode,
        detection_edge=detection_edge,
        detection_drop=detection_drop,
        approach_speed=c.z_drive_mm_to_increments(approach_speed),
        search_speed=c.z_drive_mm_to_increments(search_speed),
        acceleration=ramp,
        current_limit=current_limit,
        immersion_mode=1 if limit_immersion_to_search_end else None,
      )
    except STARFirmwareError:
      await self.move_to_safe_z()
      raise
    # RH is taken to be in stop disc terms; not yet confirmed on a device.
    detected = round(await self.request_last_lld_z_position() - tip_overhang, 2)
    if move_to_safe_z_after:
      await self.move_to_safe_z()
    return detected
