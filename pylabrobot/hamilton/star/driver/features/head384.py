"""The 384-head: the block of 384 dispensing channels that works a whole plate at once."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple

from pylabrobot.hamilton.star.driver.features.head import Head, HeadConfiguration
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)


@dataclass
class Head384Configuration(HeadConfiguration):
  """Device facts for the installed 384-head.

  What this head adds to `HeadConfiguration` is what it reports about itself beyond the shared
  flags, and the head type that resolves what a dispensing or squeezer increment is worth: the
  three heads share a piston travel but not a bore, and are geared differently.

  Its drive windows do not move with firmware, so they are plain values rather than properties -
  what varies here is which head is fitted, not the generation.
  """

  module: str = "D0"
  """What this head answers on. `RD`, `VP`, `RF`, `QW` and `QG` reach it directly, and a master
  command driving this head reports its errors as `D0ee/tt`."""
  retract_command: str = "JV"
  initialize_command: str = "JI"
  tip_presence_command: str = "QK"
  position_command: str = "QJ"
  defined_position_command: str = "EN"
  y_parameter: str = "yk"
  z_parameter: str = "je"
  traverse_z_parameter: str = "zf"
  z_end_parameter: str = "zg"
  x_offset_parameter: str = "kd"
  head_types: Dict[int, str] = field(
    default_factory=lambda: {
      0: "Low volume head",
      1: "High volume head",
      2: "STP head",  # shifted tip pickup
    }
  )
  drive_parameters: Dict[str, int] = field(
    default_factory=lambda: {"yv": 5, "yr": 3, "zv": 5, "zr": 3}
  )
  # The generation the drive windows below were taken from. A head older than this documents
  # different ones, and nothing here resolves them per generation.
  first_documented_firmware_year: int = 2009

  supports_lld_absolute_threshold_check: Optional[bool] = None

  channel_pitch: float = 4.5
  channel_columns: int = 24
  channel_rows: int = 16

  dispensing_drive_mm_per_increment: float = 0.00063333

  # This head's Y and Z drives count acceleration in thousands of increments per second squared,
  # unlike the positions and speeds they count in single ones, so these are 1000x the position
  # resolutions.
  y_drive_acceleration_mm_per_increment: float = 15.625
  z_drive_acceleration_mm_per_increment: float = 5.0

  y_range_increments: Tuple[int, int] = (7100, 36100)  # type: ignore[assignment]
  y_speed_range_increments: Tuple[int, int] = (50, 20000)  # type: ignore[assignment]
  y_acceleration_range_increments: Tuple[int, int] = (5, 32)  # type: ignore[assignment]
  z_range_increments: Tuple[int, int] = (33200, 67200)  # type: ignore[assignment]
  z_acceleration_range_increments: Tuple[int, int] = (5, 100)

  # What this head's drives start from. Its accelerations are counted in thousands, so those two
  # are written small where the 96-head's are not.
  y_speed_default_increments: int = 20000
  y_acceleration_default_increments: int = 32
  z_acceleration_default_increments: int = 80

  predefined_y_position_origin: int = 22000
  predefined_z_position_origin: int = 35000

  y_drive_current_limit_default: int = 4
  z_drive_current_limit_default: int = 7
  current_limit_range: Tuple[int, int] = (0, 7)

  # The top of what this head's master commands accept for a height, which is lower than what its
  # Z drive reaches: the field is four digits in 0.1 mm and the commands document 3270 as its
  # maximum, where the drive itself goes to 336.0 mm.
  defined_position_minimum_height_default: float = 327.0

  # The Y window the master's tip commands accept, in deck mm at head channel A1. Narrower than
  # what the Y drive itself reaches, and narrower than the initialization command's own window, so
  # it is stated here rather than taken from `y_range`.
  tip_command_y_range: Tuple[float, float] = (110.0, 564.0)

  def _require_head_type(self) -> str:
    """The head type, for the facts only it decides.

    Returns:
      Which head is fitted.

    Raises:
      RuntimeError: If it has not been read, or is one this driver does not know.
    """
    if self.head_type is None or self.head_type == "unknown":
      raise RuntimeError(
        "the 384-head's type is not known, and it is what decides how much a dispensing or "
        "squeezer increment is worth; have you called `star.setup()`?"
      )
    return self.head_type

  @property
  def dispensing_drive_uL_per_increment(self) -> float:
    """What one increment of the dispensing drive holds, in uL.

    The three heads share a piston travel but not a bore, so this is the head type's to decide and
    is not known until the head has said which it is - guessing would mis-volume every aspirate.

    Returns:
      The volume one increment holds, in uL.

    Raises:
      RuntimeError: If the head type has not been read.
    """
    head_type = self._require_head_type()
    if head_type == "Low volume head":
      return 0.000974941
    if head_type == "High volume head":
      return 0.00143754
    return 0.00186531

  @property
  def squeezer_drive_mm_per_increment(self) -> float:
    """How far one increment of the squeezer drive travels, in mm.

    Geared differently on the low volume head, so this is the head type's to decide as the
    dispensing volume above is.

    Returns:
      The distance one increment travels, in mm.

    Raises:
      RuntimeError: If the head type has not been read.
    """
    return 0.00091813 if self._require_head_type() == "Low volume head" else 0.00035866

  # -- windows the dispensing and squeezer drives work in ----------------------------------------

  @property
  def dispensing_drive_range(self) -> Tuple[float, float]:
    """Aspirate/dispense piston volume window (uL); applies to both aspirate and dispense."""
    return (0.0, self.dispensing_drive_increments_to_uL(60950))

  @property
  def dispensing_drive_speed_range(self) -> Tuple[float, float]:
    """Dispensing-drive speed window (uL/s)."""
    # The drive counts its speed in tens of increments per second, so both ends are scaled.
    return (
      self.dispensing_drive_increments_to_uL(5 * 10),
      self.dispensing_drive_increments_to_uL(25000 * 10),
    )

  @property
  def dispensing_drive_speed_default(self) -> float:
    """Dispensing-drive default speed (uL/s)."""
    return self.dispensing_drive_increments_to_uL(50000)

  @property
  def dispensing_drive_acceleration_default(self) -> float:
    """Dispensing-drive default acceleration (uL/s2)."""
    return self.dispensing_drive_increments_to_uL(9000000)

  @property
  def squeezer_drive_speed_default(self) -> float:
    """Squeezer-drive default speed (mm/s); the low volume head runs slower."""
    increments = 16000 if self._require_head_type() == "Low volume head" else 40000
    return self.squeezer_drive_increments_to_mm(increments)

  @property
  def squeezer_drive_acceleration_default(self) -> float:
    """Squeezer-drive default acceleration (mm/s2); the low volume head runs gentler."""
    increments = 100000 if self._require_head_type() == "Low volume head" else 250000
    return self.squeezer_drive_increments_to_mm(increments)


class Head384(Head):
  """The 384-head.

  Reached as `driver.head384`, on a device that has one. It is addressed as `D0`, but the
  commands that move it go to the master, so this feature speaks to both.
  """

  configuration: Head384Configuration

  def __init__(self, driver: "STARDriver", configuration: Optional[Head384Configuration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the head's device facts. Defaults to `Head384Configuration()`.
    """
    super().__init__(driver, configuration or Head384Configuration())

  # -- session / discovery -------------------------------------------------------------------------

  def _record_hardware(self, hardware: List[str]) -> None:
    """Record whether this head runs the absolute-threshold cLLD check.

    Index 1 was reserve until 2015, so a head older than that reads back 0 there whether or not it
    would do the check.

    Args:
      hardware: the tokens `request_hardware` read.
    """
    self.configuration.supports_lld_absolute_threshold_check = bool(int(hardware[1]))

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  # -- dispensing drive position -------------------------------------------------------------------

  # `D0 RD` answers with two signed values in increments. They are not always identical
  # (`+04904 +04899`, `+54904 +54901`), so they read as two separate measurements of the
  # dispensing drive rather than one value written twice. What was read back: `+00000 +00000`
  # uninitialized, `+04904 +04899` after `JI`, `+50000 +50000` after `JB` with `iu2`, and
  # `+54904 +54904` after `JC`. Aspirating counts the position up: 54.00 uL reads back `+28948`,
  # and dispensing 51.00 uL of that leaves `+01608`, which is the 3.00 uL of transport air.
  #
  # TODO: read it through a method of this feature rather than a raw command.
  # `Head96.dispensing_drive_request_uL_position` reads `H0 RD` and keeps the result on
  # `piston_position`, which is the shape this one would take.

  # -- head initialization -------------------------------------------------------------------------

  # `xd` on this head's `JI` is the sign of the X position, which is what `Head.initialize`
  # derives it as: sent `xs00500xd1`, the head reports `xs00500xd1` back through `C0 QJ`, at
  # -50.0 mm. A negative X at channel A1 is ordinary here rather than a sign that a reading is
  # wrong, since A1 sits `configuration.x_offset` left of the carriage reference.
  #
  # The head comes to rest at 320.0 mm whatever `z_position_at_the_command_end` asks for - it was
  # given `zg2450` and ended at 320.0 - so that parameter is a floor rather than a target. `C0 EY`
  # is not quite the same: given a minimum height of 320.0 it left the head at 325.0.
  #
  # TODO: this head ejects at -161.3 mm without complaint, which is past the -100.0 mm that
  # `configuration.min_x_clear_of_left_side_panel` allows. That limit only narrows the arm's travel
  # on a device declared to have a left side panel, and whether the device this was read from has
  # one is not recorded. Establish which, since a device with a panel would make the limit wrong.

  async def initialize(
    self,
    tip_discard_location: Optional[Coordinate] = None,
    z_position_at_the_command_end: Optional[float] = None,
    read_timeout: int = 60,
  ):
    """Initialize the head, discarding whatever is mounted on it. `C0 JI`.

    Args:
      tip_discard_location: where to eject, in deck mm, at head channel A1. Defaults to
        `configuration.tip_discard_location`.
      z_position_at_the_command_end: the height the head comes to rest no lower than, in mm,
        rather than the height it takes: it ends at 320.0 mm whatever this asks for. Defaults to
        `configuration.traversal_z_position`.
      read_timeout: how long to wait for the device to answer, in seconds.

    Raises:
      ValueError: If no position was given and none is configured.
    """
    return await super().initialize(
      tip_discard_location, z_position_at_the_command_end, read_timeout
    )

  # -- y position --------------------------------------------------------------------------------

  async def _unchecked_fw_safety_move_to_y_position(
    self,
    y: float,
    minimum_height_at_beginning_of_a_command: Optional[float] = None,
  ):
    """Move the head along Y through the master, unguarded and unrecorded. `C0 EY`.

    The sibling of `_unchecked_fw_move_to_coordinate` for one axis: the master raises the head to
    the height given before it travels, where `move_to_y_position` drives the head's own Y drive
    and leaves the height to the caller. The 96-head has no command of this shape, so this is the
    384-head's own.

    Args:
      y: where to put channel A1, in mm (`yk`).
      minimum_height_at_beginning_of_a_command: the floor the head travels above, in mm, rather
        than the height it takes: asked for 320.0 it comes to rest at 325.0. Defaults to
        `configuration.defined_position_minimum_height_default` (`zf`).
    """
    c = self.configuration
    if minimum_height_at_beginning_of_a_command is None:
      minimum_height_at_beginning_of_a_command = c.defined_position_minimum_height_default
    parameters: Dict[str, Any] = {
      c.y_parameter: f"{round(y * 10):04}",
      c.traverse_z_parameter: f"{round(minimum_height_at_beginning_of_a_command * 10):04}",
    }
    return await self._driver.send_command(
      module="C0",
      command="EY",
      **parameters,
    )

  # ----------------------------------------
  # Tip handling
  # ----------------------------------------

  # -- where the head goes -----------------------------------------------------------------------

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
      logger.exception("could not read back where the 384-head stopped")

  async def _request_tips_after_failure(self) -> Optional[bool]:
    """Whether the firmware holds that tips are mounted after a tip command failed; None unread."""
    try:
      return await self.request_tip_presence()
    except Exception:
      logger.warning("could not request whether the 384-head carries tips after the failure")
      return None

  # -- tip pickup ----------------------------------------------------------------------------------

  async def _unchecked_fw_pick_up_tips(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    tip_type_table_index: int,
    z_pick_up_position: int,
    minimum_traverse_height_at_beginning_of_a_command: int,
    minimum_height_at_command_end: int,
    pick_up_method: int = 0,
    centering: bool = True,
    read_timeout: int = 120,
  ):
    """Send the pick-up as it is given, in tenths of a millimetre. `C0 JB`.

    Args:
      x_position: X of tip A1, as an absolute value, 0 to 30000 (`xs`).
      x_direction: sign of the X position: 0 positive, 1 negative (`xd`).
      y_position: Y of well A1, 1100 to 5640 (`yk`).
      tip_type_table_index: the tip type table entry to mount, 0 to 99 (`tt`).
      z_pick_up_position: the collar bearing position, 0 to 3270 (`je`).
      minimum_traverse_height_at_beginning_of_a_command: 0 to 3270 (`zf`).
      minimum_height_at_command_end: 0 to 3270 (`zg`).
      pick_up_method: 0 from a rack, 1 from the CoRe 384 tip wash station, 2 with full volume
        blowout. The command sets the dispensing drive itself: `iu2` left it at 50000 increments,
        which is about 20 uL of piston travel short of the top, from either of the two positions
        `D0 RD` had been read at beforehand. What `iu0` leaves the drive at was not measured
        (`iu`).
      centering: whether the head centres itself on the rack as it collects (`ii`).
      read_timeout: how long to wait for the answer, in s.
    """
    return await self._driver.send_command(
      module="C0",
      command="JB",
      subsystem=self.configuration.module,
      read_timeout=read_timeout,
      xs=f"{x_position:05}",
      xd=x_direction,
      yk=f"{y_position:04}",
      tt=f"{tip_type_table_index:02}",
      iu=pick_up_method,
      je=f"{z_pick_up_position:04}",
      zf=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      zg=f"{minimum_height_at_command_end:04}",
      ii=int(centering),
    )

  async def pick_up_tips(
    self,
    tip_rack: TipRack,
    offset: Optional[Coordinate] = None,
    tip_pickup_method: Literal["from_rack", "from_wash_station", "full_blowout"] = "from_rack",
    centering: bool = True,
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
  ) -> None:
    """Pick up a rack of tips on the whole head, as legacy's `pick_up_tips_core384`. `C0 JB`.

    Head channel A1 goes to the centre of spot A1, at the spot's Z. Once the device has picked them
    up, the tip in each spot is mounted on the shaft of the channel with the spot's index.

    Args:
      tip_rack: a 384 tip rack. Spots without a tip give none.
      offset: added to spot A1's centre, in mm.
      tip_pickup_method: where the tips are collected from. `from_rack` leaves the dispensing
        drive at the bottom of its travel, `full_blowout` leaves it near the top.
      centering: whether the head centres itself on the rack as it collects (`ii`).
      minimum_height_command_end: in mm. `configuration.traversal_z_position` when None.
      minimum_traverse_height_start: in mm. `configuration.traversal_z_position` when None.

    Raises:
      ValueError: If the rack does not have 384 spots or holds no tips, or a position cannot be
        reached.
      TypeError: If its tips are not Hamilton tips.
      RuntimeError: If the driver was given no deck.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    # Read before anything moves: a pick-up drives the piston down, which would push out whatever
    # tips still on the head hold.
    if await self.request_tip_presence():
      raise RuntimeError("the head already carries tips; drop them before picking up more")
    if tip_rack.num_items != 384:
      raise ValueError("Tip rack must have 384 tips")
    tips = [
      spot.tip_for_pickup() if not spot.tracks_tips or spot.tip is not None else None
      for spot in tip_rack.get_all_items()
    ]
    prototypical_tip = next((tip for tip in tips if tip is not None), None)
    if prototypical_tip is None:
      raise ValueError("No tips found in the tip rack.")
    if not isinstance(prototypical_tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")
    # TODO: which collar this head takes is open. `TipSize.CORE_384_HEAD_TIP` is 4, which is what
    # a 384 tip resource carries into the tip type table; the Venus capture defines its tip type
    # with `tg6`; and `tip_creators.fitting_depth` carries a raw `6: 8` entry that no `TipSize`
    # member reaches. `tg6` was not confirmed on the head: the `C0 TT` sent for it went out as
    # `C0TTid0166tt33tf0tl____tv_____tg6tu0`, with tip length and volume never filled in, and came
    # back `er01/32`, parameter out of range. Settle it by defining a tip type with a real length
    # and a real volume at `tg6` and recording what the head answers.
    tip_type_index = await self._driver.get_or_assign_tip_type_index(prototypical_tip)

    # `C0 JB` sets the dispensing drive itself - to 0 from a rack, and to 50000 increments with
    # full volume blowout - so nothing has to place it first, as `Head96.pick_up_tips` does for the
    # 96-head.

    location = tip_rack.get_item("A1").get_location_wrt(deck, x="c", y="c", z="b") + (
      offset or Coordinate.zero()
    )
    traverse_z, end_z = self._resolve_tip_command_heights(
      minimum_traverse_height_start, minimum_height_command_end
    )
    self._check_tip_command(location, traverse_z, end_z, skip_z=True)

    picked_up = False
    command_error: Optional[BaseException] = None
    try:
      await self._unchecked_fw_pick_up_tips(
        x_position=abs(round(location.x * 10)),
        x_direction=0 if location.x >= 0 else 1,
        y_position=round(location.y * 10),
        tip_type_table_index=tip_type_index,
        z_pick_up_position=round(location.z * 10),
        minimum_traverse_height_at_beginning_of_a_command=round(traverse_z * 10),
        minimum_height_at_command_end=round(end_z * 10),
        pick_up_method={"from_rack": 0, "from_wash_station": 1, "full_blowout": 2}[
          tip_pickup_method
        ],
        centering=centering,
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

  # -- tip drop ------------------------------------------------------------------------------------

  async def _unchecked_fw_discard_tips(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    z_deposit_position: int,
    minimum_traverse_height_at_beginning_of_a_command: int,
    minimum_height_at_command_end: int,
    discard_method: int = 0,
    read_timeout: int = 120,
  ):
    """Send the discard as it is given, in tenths of a millimetre. `C0 JC`.

    Args:
      x_position: X of well A1, as an absolute value, 0 to 30000 (`xs`).
      x_direction: sign of the X position: 0 positive, 1 negative (`xd`).
      y_position: Y of well A1, 1100 to 5640 (`yk`).
      z_deposit_position: the collar bearing position, 0 to 3270 (`je`).
      minimum_traverse_height_at_beginning_of_a_command: 0 to 3270 (`zf`).
      minimum_height_at_command_end: 0 to 3270 (`zg`).
      discard_method: 0 discards the tips, 1 the tip tool (`jd`).
      read_timeout: how long to wait for the answer, in s.
    """
    return await self._driver.send_command(
      module="C0",
      command="JC",
      subsystem=self.configuration.module,
      read_timeout=read_timeout,
      xs=f"{x_position:05}",
      xd=x_direction,
      yk=f"{y_position:04}",
      je=f"{z_deposit_position:04}",
      zf=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      zg=f"{minimum_height_at_command_end:04}",
      jd=discard_method,
    )

  async def drop_tips(
    self,
    resource: Resource,
    offset: Optional[Coordinate] = None,
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
  ) -> None:
    """Drop the head's tips into a tip rack or anywhere else, as legacy's `discard_tips_core384`.
    `C0 JC`.

    Into a tip rack, head channel A1 goes to the centre of spot A1, at the spot's Z, and each
    channel's tip goes into the spot with its index. Anywhere else, the head is centred over the
    resource, and the tips belong to nothing afterwards.

    Args:
      resource: a 384 tip rack, or anything else, such as the trash.
      offset: added to where the head goes, in mm.
      minimum_height_command_end: in mm. `configuration.traversal_z_position` when None.
      minimum_traverse_height_start: in mm. `configuration.traversal_z_position` when None.

    Raises:
      ValueError: If a tip rack does not have 384 spots, or a position cannot be reached.
      RuntimeError: If the driver was given no deck.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    if isinstance(resource, TipRack):
      if resource.num_items != 384:
        raise ValueError("Tip rack must have 384 tips")
      location = resource.get_item("A1").get_location_wrt(deck, x="c", y="c", z="b")
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
      await self._unchecked_fw_discard_tips(
        x_position=abs(round(location.x * 10)),
        x_direction=0 if location.x >= 0 else 1,
        y_position=round(location.y * 10),
        z_deposit_position=round(location.z * 10),
        minimum_traverse_height_at_beginning_of_a_command=round(traverse_z * 10),
        minimum_height_at_command_end=round(end_z * 10),
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
            spot = resource.get_item(i)
            if spot.tracks_tips:
              spot.assign_tip(tip)
      await self._record_after_tip_command(command_error)

  # ----------------------------------------
  # Liquid handling
  # ----------------------------------------

  # -- aspirating ----------------------------------------------------------------------------------

  async def _unchecked_fw_aspirate(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    minimum_traverse_height_at_beginning_of_a_command: int,
    minimum_height_at_command_end: int,
    liquid_surface_no_lld: int,
    minimum_height: int,
    aspiration_volume: int,
    aspiration_type: int = 0,
    lld_search_height: int = 3270,
    pull_out_distance_transport_air: int = 50,
    second_section_height: int = 0,
    second_section_ratio: int = 0,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    surface_following_distance: int = 0,
    aspiration_speed: int = 2000,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 100,
    pre_wetting_volume: int = 0,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 0,
    homogenization_volume: int = 0,
    homogenization_cycles: int = 0,
    homogenization_position_from_liquid_surface: int = 0,
    homogenization_speed: int = 2000,
    homogenization_surface_following_distance: int = 0,
    capacitive_lld_gain: Optional[int] = None,
    capacitive_lld_offset: Optional[int] = None,
    read_timeout: int = 120,
  ):
    """Send the aspiration as it is given, in the units the command counts in. `C0 JA`.

    Positions and distances are in tenths of a millimetre, volumes in hundredths of a microlitre,
    speeds in tenths of a unit per second.

    Args:
      x_position: X of well A1, as an absolute value, 0 to 30000 (`xs`).
      x_direction: sign of the X position: 0 positive, 1 negative (`xd`).
      y_position: Y of well A1, 1100 to 5640 (`yk`).
      minimum_traverse_height_at_beginning_of_a_command: 0 to 3270 (`zf`).
      minimum_height_at_command_end: 0 to 3270 (`zg`).
      liquid_surface_no_lld: where the liquid stands with no LLD, 0 to 3270 (`jt`).
      minimum_height: the deepest the tips may go, 0 to 3270 (`jm`).
      aspiration_volume: 0 to 9400 (`jf`).
      aspiration_type: 0 simple, 1 sequence, 2 cup emptied (`ja`).
      lld_search_height: 0 to 3270 (`jz`).
      pull_out_distance_transport_air: how far to withdraw to take transport air, 0 to 3270 (`pq`).
      second_section_height: the tube's second section, from `minimum_height`, 0 to 3270 (`zw`).
      second_section_ratio: 0 to 10000 (`zs`).
      immersion_depth: 0 to 250 (`jw`).
      immersion_depth_direction: 0 goes deeper, 1 goes up out of the liquid (`jx`).
      surface_following_distance: how far the surface sinks over the aspiration, 0 to 250 (`jh`).
      aspiration_speed: 3 to 2400 (`jg`).
      transport_air_volume: 0 to 1000 (`ju`).
      blow_out_air_volume: 0 to 8760 (`jv`).
      pre_wetting_volume: 0 to 8760 (`jy`).
      lld_mode: 0 off, 1 gamma (`jq`).
      gamma_lld_sensitivity: 1 high to 4 low (`jp`).
      swap_speed: how fast the tips leave the liquid, 3 to 1000 (`js`).
      settling_time: in tenths of a second, 0 to 99 (`ji`).
      homogenization_volume: 0 to 8760 (`jj`).
      homogenization_cycles: 0 to 99 (`jk`).
      homogenization_position_from_liquid_surface: 0 to 250 (`jl`).
      homogenization_speed: 3 to 2400 (`jn`).
      homogenization_surface_following_distance: 0 to 250 (`mk`).
      capacitive_lld_gain: in AD steps, 0 to 1023. Left out of the command when None, which is
        what Venus sends (`ig`).
      capacitive_lld_offset: in AD steps, 0 to 1023. Left out of the command when None (`ih`).
      read_timeout: how long to wait for the answer, in s.
    """
    parameters: Dict[str, Any] = {
      "ja": aspiration_type,
      "xs": f"{x_position:05}",
      "xd": x_direction,
      "yk": f"{y_position:04}",
      "zf": f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      "zg": f"{minimum_height_at_command_end:04}",
      "jz": f"{lld_search_height:04}",
      "jt": f"{liquid_surface_no_lld:04}",
      "jm": f"{minimum_height:04}",
      "jw": f"{immersion_depth:03}",
      "jx": immersion_depth_direction,
      "jh": f"{surface_following_distance:03}",
      "jf": f"{aspiration_volume:05}",
      "jg": f"{aspiration_speed:04}",
      "ju": f"{transport_air_volume:04}",
      "jv": f"{blow_out_air_volume:05}",
      "jy": f"{pre_wetting_volume:05}",
      "jq": lld_mode,
      "jp": gamma_lld_sensitivity,
      "js": f"{swap_speed:04}",
      "ji": f"{settling_time:02}",
      "jj": f"{homogenization_volume:05}",
      "jk": f"{homogenization_cycles:02}",
      "jl": f"{homogenization_position_from_liquid_surface:03}",
      "jn": f"{homogenization_speed:04}",
      "zw": f"{second_section_height:04}",
      "zs": f"{second_section_ratio:05}",
      "mk": f"{homogenization_surface_following_distance:03}",
      "pq": f"{pull_out_distance_transport_air:04}",
    }
    if capacitive_lld_gain is not None:
      parameters["ig"] = f"{capacitive_lld_gain:04}"
    if capacitive_lld_offset is not None:
      parameters["ih"] = f"{capacitive_lld_offset:04}"

    return await self._driver.send_command(
      module="C0",
      command="JA",
      subsystem=self.configuration.module,
      read_timeout=read_timeout,
      **parameters,
    )

  # -- dispensing ----------------------------------------------------------------------------------

  async def _unchecked_fw_dispense(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    minimum_traverse_height_at_beginning_of_a_command: int,
    minimum_height_at_command_end: int,
    liquid_surface_no_lld: int,
    minimum_height: int,
    dispense_volume: int,
    dispensing_mode: int = 0,
    second_section_height: int = 0,
    second_section_ratio: int = 0,
    lld_search_height: int = 3270,
    pull_out_distance_transport_air: int = 50,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    surface_following_distance: int = 0,
    dispense_speed: int = 2000,
    cut_off_speed: int = 1500,
    stop_back_volume: int = 0,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 0,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    side_touch_off_distance: int = 0,
    swap_speed: int = 100,
    settling_time: int = 0,
    mix_volume: int = 0,
    mix_cycles: int = 0,
    mix_position_from_liquid_surface: int = 0,
    mix_speed: int = 2000,
    mix_surface_following_distance: int = 0,
    capacitive_lld_gain: Optional[int] = None,
    capacitive_lld_offset: Optional[int] = None,
    read_timeout: int = 120,
  ):
    """Send the dispense as it is given, in the units the command counts in. `C0 JD`.

    Units as `_unchecked_fw_aspirate` takes them.

    Args:
      x_position: X of well A1, as an absolute value, 0 to 30000 (`xs`).
      x_direction: sign of the X position: 0 positive, 1 negative (`xd`).
      y_position: Y of well A1, 1100 to 5640 (`yk`).
      minimum_traverse_height_at_beginning_of_a_command: 0 to 3270 (`zf`).
      minimum_height_at_command_end: 0 to 3270 (`zg`).
      liquid_surface_no_lld: where the liquid stands with no LLD, 0 to 3270 (`jt`).
      minimum_height: the deepest the tips may go, 0 to 3270 (`jm`).
      dispense_volume: 0 to 8760 (`jb`).
      dispensing_mode: 0 partial volume in jet mode, 1 blow out in jet mode, 2 partial volume at
        the surface, 3 blow out at the surface, 4 empty the tip at a fixed position (`jo`).
      second_section_height: the tube's second section, from `minimum_height`, 0 to 3270 (`zw`).
      second_section_ratio: 0 to 10000 (`zs`).
      lld_search_height: 0 to 3270 (`jz`).
      pull_out_distance_transport_air: how far to withdraw to take transport air, 0 to 3270 (`pq`).
      immersion_depth: 0 to 250 (`jw`).
      immersion_depth_direction: 0 goes deeper, 1 goes up out of the liquid (`jx`).
      surface_following_distance: how far the surface rises over the dispense, 0 to 250 (`jh`).
      dispense_speed: 3 to 2400 (`jc`).
      cut_off_speed: 3 to 2400 (`jr`).
      stop_back_volume: 0 to 2000 (`im`).
      transport_air_volume: 0 to 1000 (`ju`).
      blow_out_air_volume: 0 to 8760 (`jv`).
      lld_mode: 0 off, 1 gamma (`jq`).
      gamma_lld_sensitivity: 1 high to 4 low (`jp`).
      side_touch_off_distance: 0 to 90. Anything above 0 turns LLD off (`ij`).
      swap_speed: how fast the tips leave the liquid, 3 to 1000 (`js`).
      settling_time: in tenths of a second, 0 to 99 (`ji`).
      mix_volume: 0 to 8760 (`jj`).
      mix_cycles: 0 to 99 (`jk`).
      mix_position_from_liquid_surface: 0 to 250 (`jl`).
      mix_speed: 3 to 2400 (`jn`).
      mix_surface_following_distance: 0 to 250 (`mk`).
      capacitive_lld_gain: in AD steps, 0 to 1023. Left out of the command when None, which is
        what Venus sends (`ig`).
      capacitive_lld_offset: in AD steps, 0 to 1023. Left out of the command when None (`ih`).
      read_timeout: how long to wait for the answer, in s.
    """
    parameters: Dict[str, Any] = {
      "jo": dispensing_mode,
      "xs": f"{x_position:05}",
      "xd": x_direction,
      "yk": f"{y_position:04}",
      "jm": f"{minimum_height:04}",
      "jz": f"{lld_search_height:04}",
      "jt": f"{liquid_surface_no_lld:04}",
      "jw": f"{immersion_depth:03}",
      "jx": immersion_depth_direction,
      "jh": f"{surface_following_distance:03}",
      "zf": f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      "zg": f"{minimum_height_at_command_end:04}",
      "jb": f"{dispense_volume:05}",
      "jc": f"{dispense_speed:04}",
      "jr": f"{cut_off_speed:04}",
      "im": f"{stop_back_volume:04}",
      "ju": f"{transport_air_volume:04}",
      "jv": f"{blow_out_air_volume:05}",
      "jq": lld_mode,
      "jp": gamma_lld_sensitivity,
      "js": f"{swap_speed:04}",
      "ji": f"{settling_time:02}",
      "jj": f"{mix_volume:05}",
      "jk": f"{mix_cycles:02}",
      "jl": f"{mix_position_from_liquid_surface:03}",
      "jn": f"{mix_speed:04}",
      "zw": f"{second_section_height:04}",
      "ij": f"{side_touch_off_distance:02}",
      "zs": f"{second_section_ratio:05}",
      "mk": f"{mix_surface_following_distance:03}",
      "pq": f"{pull_out_distance_transport_air:04}",
    }
    if capacitive_lld_gain is not None:
      parameters["ig"] = f"{capacitive_lld_gain:04}"
    if capacitive_lld_offset is not None:
      parameters["ih"] = f"{capacitive_lld_offset:04}"

    return await self._driver.send_command(
      module="C0",
      command="JD",
      subsystem=self.configuration.module,
      read_timeout=read_timeout,
      **parameters,
    )

  # ----------------------------------------
  # Wash
  # ----------------------------------------

  # The two commands below drive the CoRe 384 tip wash station. Their field widths are the
  # specification's; no capture pins them, because the device this head was read on has no wash
  # station fitted.

  async def _unchecked_fw_wash_tips(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    wash_z_position: int,
    minimum_height: int,
    minimum_traverse_height_at_beginning_of_a_command: int,
    wash_volume: int,
    wash_cycles: int,
    surface_following_distance: int = 0,
    wash_speed: int = 2000,
    read_timeout: int = 120,
  ):
    """Send the wash as it is given, in the units the command counts in. `C0 JG`.

    Args:
      x_position: wash X of well A1, as an absolute value, 0 to 30000 (`xs`).
      x_direction: sign of the X position: 0 positive, 1 negative (`xd`).
      y_position: wash Y of well A1, 1100 to 5640 (`yk`).
      wash_z_position: 0 to 3270 (`jt`).
      minimum_height: the deepest the tips may go, 0 to 3270 (`jm`).
      minimum_traverse_height_at_beginning_of_a_command: 0 to 3270 (`zf`).
      wash_volume: 0 to 8760 (`jj`).
      wash_cycles: 0 to 99 (`jk`).
      surface_following_distance: 0 to 250 (`jh`).
      wash_speed: 3 to 2400 (`jn`).
      read_timeout: how long to wait for the answer, in s.
    """
    return await self._driver.send_command(
      module="C0",
      command="JG",
      subsystem=self.configuration.module,
      read_timeout=read_timeout,
      xs=f"{x_position:05}",
      xd=x_direction,
      yk=f"{y_position:04}",
      jt=f"{wash_z_position:04}",
      jm=f"{minimum_height:04}",
      jh=f"{surface_following_distance:03}",
      zf=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      jj=f"{wash_volume:05}",
      jk=f"{wash_cycles:02}",
      jn=f"{wash_speed:04}",
    )

  async def _unchecked_fw_empty_washed_tips(
    self,
    z_position: int,
    minimum_height_at_command_end: int,
    read_timeout: int = 120,
  ):
    """Empty the washed tips at the end of a wash, as it is given. `C0 JU`.

    Args:
      z_position: 0 to 3270 (`jt`).
      minimum_height_at_command_end: 0 to 3270 (`zg`).
      read_timeout: how long to wait for the answer, in s.
    """
    return await self._driver.send_command(
      module="C0",
      command="JU",
      subsystem=self.configuration.module,
      read_timeout=read_timeout,
      jt=f"{z_position:04}",
      zg=f"{minimum_height_at_command_end:04}",
    )
