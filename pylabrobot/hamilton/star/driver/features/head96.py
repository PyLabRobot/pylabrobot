"""The 96-head: the block of 96 pipettes that works a whole plate at once."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

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
