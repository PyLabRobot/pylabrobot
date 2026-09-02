"""What the 96-head and the 384-head share.

At their own modules the 96-head and the 384-head are the same machine twice over: the same four
drives, the same liquid level detection down to its two channels and their and/or logic, the same
macros, reached by commands that differ only in their constants - which module answers, how wide
each parameter is written, how much one increment is worth. What is theirs alone at this level is
what their configuration bytes mean, and what resolves their drive windows: firmware generation for
one, which head is fitted for the other. That is the layer this holds.

At the master they are not the same machine. The commands that pick up tips and move liquid carry
about thirty parameters each, and between the two heads every one is named differently, the volumes
are counted in units that differ by a factor of ten, and the capabilities themselves do not match -
only the 96-head can mask individual channels, only the 384-head takes a gain and offset for its
cLLD. Each head carries those itself. Renaming thirty parameters through here would be a
translation table wearing a base class rather than a shared implementation.

The line is roughly how much of a command has to be restated to share it. One or two names, as the
initialization command needs, is a constant. Thirty is a different command.
"""

import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, cast

from pylabrobot.hamilton.protocol.text.framing import parse_firmware_version_date
from pylabrobot.hamilton.star.resource_model import NChannelPipette
from pylabrobot.resources.coordinate import Coordinate

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.features.x_arm import XArm
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)

# The firmware's own retract drives the head to its Z-safety height, which takes a while.
RETRACT_READ_TIMEOUT = 20

# The shaft the drives report: every head is positioned by its first channel.
HEAD_REFERENCE_SHAFT = "A1"


@dataclass
class HeadConfiguration:
  """Device facts shared by the heads.

  Two kinds of value: what the head reports about itself, which is None until read; and device
  facts that are defaulted, of which the windows in standard units are computed from the increment
  windows on access rather than stored.

  A head supplies the six members below that are left unimplemented here - the four increment
  windows its drives accept, and what one dispensing or squeezer increment is worth. Each may be a
  plain field where it is constant for that head, or a property where the head resolves it from
  what it read about itself.
  """

  # What this head is on the bus, and the commands, parameter names and field widths that reach
  # it. Stated without defaults: a head that named none of them would address whichever module
  # happened to answer.
  module: str
  retract_command: str
  initialize_command: str
  tip_presence_command: str
  position_command: str
  # What the master's commands for this head call its Y, its Z, and the height it leaves the head
  # at. Shared by the commands that move it and by the query that reports where it is.
  y_parameter: str
  z_parameter: str
  z_end_parameter: str
  x_offset_parameter: str
  head_types: Dict[int, str]
  """What each head-type code means."""
  drive_parameters: Dict[str, int]
  """The drive parameters this head stores - each drive's speed and acceleration - and how many
  digits each is written in. Reads use these as well as writes: a read one digit short truncates
  the value silently rather than failing."""
  first_documented_firmware_year: int
  """The generation this head's windows were taken from; an older one may document different ones."""

  firmware_version: Optional[str] = None
  firmware_date: Optional[datetime.date] = None
  x_offset: Optional[float] = None
  """Deck X distance from the X-arm carriage center to head channel A1 (mm), read from
  master EEPROM at setup. Mirrors the iSWAP's rotation-drive x-offset."""

  channel_pitch: float = 9.0
  channel_columns: int = 12
  channel_rows: int = 8
  body_size_z: float = 140.0  # size of modelled head body; its lowest feature to its top (mm).
  min_x_clear_of_left_side_panel: float = -100.0
  """The leftmost channel A1 may go without the head striking the left side panel, in deck mm.

  A judgement about clearance rather than a measurement, and not read from anywhere: the panel is
  bolted on and off in seconds, so whether one is fitted is declared, and how close the head may
  come to it is ours to choose."""

  supports_clot_monitoring_clld: Optional[bool] = None
  head_type: Optional[str] = None

  tip_discard_location: Optional[Coordinate] = None
  """Where this head's trash is: head channel A1, in deck mm.

  Where tips go when a discard is not told otherwise, and where the head ejects when it is
  initialized - initializing throws off whatever is mounted, so it has to happen somewhere tips may
  be dropped. Depends on where the waste sits on the deck, so it has no default, and a run that
  moves the waste sets it again."""

  z_range: Optional[Tuple[float, float]] = None
  """Z-drive position window (mm). Resolved by `probe_z_max`: the floor is what the drive
  documents, the ceiling is read from a hardware probe."""

  # Encoder resolutions (defaulted device facts). A drive that counts its acceleration in
  # thousands of increments says so here, by carrying a resolution a thousand times its own.
  z_drive_mm_per_increment: float = 0.005
  y_drive_mm_per_increment: float = 0.015625
  y_drive_acceleration_mm_per_increment: float = 0.015625
  z_drive_acceleration_mm_per_increment: float = 0.005
  dispensing_drive_mm_per_increment: float = 0.001025641026

  # The Z windows both heads share. The Y ones differ, so each head states its own.
  z_speed_increment_range: Tuple[int, int] = (50, 20000)
  z_acceleration_increment_range: Tuple[int, int] = (5000, 100000)

  # What the drive adds to each position it has stored, so a stored value is an offset from here
  # rather than a position in its own right. Zero where the head stores positions outright.
  predefined_y_position_origin: int = 0
  predefined_z_position_origin: int = 0

  traversal_z_position: float = 245.0
  """How high the head travels when a command is not told otherwise, in mm. Not a device fact: a
  height chosen to clear what sits on the deck, which is why every command that uses it takes it as
  an argument too."""

  # What the driver sends when a move names no current limit, and what the drives accept.
  y_drive_current_limit_default: int = 15
  z_drive_current_limit_default: int = 15
  current_limit_range: Tuple[int, int] = (0, 15)

  # What each drive starts from, in the increments it is written in. A head that documents
  # something else states its own.
  y_speed_increment_default: int = 25000
  y_acceleration_increment_default: int = 35000
  z_speed_increment_default: int = 17000
  z_acceleration_increment_default: int = 80000

  # What the head reported holding, which stands in front of the defaults above. None until
  # discovery has read it, and on a simulated machine.
  y_drive_speed_firmware_reported: Optional[float] = None
  y_drive_acceleration_firmware_reported: Optional[float] = None
  z_drive_speed_firmware_reported: Optional[float] = None
  z_drive_acceleration_firmware_reported: Optional[float] = None

  # -- what each head supplies -------------------------------------------------------------------

  @property
  def y_increment_range(self) -> Tuple[int, int]:
    """Y-drive position window in increments, at channel A1."""
    raise NotImplementedError("a head states the Y positions its drive accepts")

  @property
  def y_speed_increment_range(self) -> Tuple[int, int]:
    """Y-drive speed window, in the increments per second the drive counts in."""
    raise NotImplementedError("a head states the Y speeds its drive accepts")

  @property
  def y_acceleration_increment_range(self) -> Tuple[int, int]:
    """Y-drive acceleration window, in the increments the drive counts acceleration in."""
    raise NotImplementedError("a head states the Y accelerations its drive accepts")

  @property
  def z_increment_range(self) -> Tuple[int, int]:
    """Z-drive position window in increments, at the head's lowest fixed feature."""
    raise NotImplementedError("a head states the Z positions its drive accepts")

  @property
  def dispensing_drive_uL_per_increment(self) -> float:
    """What one increment of the dispensing drive holds, in uL."""
    raise NotImplementedError("a head states what one dispensing increment holds")

  @property
  def squeezer_drive_mm_per_increment(self) -> float:
    """How far one increment of the squeezer drive travels, in mm."""
    raise NotImplementedError("a head states how far one squeezer increment travels")

  # -- what each drive starts from, preferring what the head reported over what it documents -----

  @property
  def y_drive_speed_default(self) -> float:
    """Y-drive speed a move uses when the caller names none (mm/s)."""
    if self.y_drive_speed_firmware_reported is not None:
      return self.y_drive_speed_firmware_reported
    return self.y_drive_increments_to_mm(self.y_speed_increment_default)

  @property
  def y_drive_acceleration_default(self) -> float:
    """Y-drive acceleration a move uses when the caller names none (mm/s2)."""
    if self.y_drive_acceleration_firmware_reported is not None:
      return self.y_drive_acceleration_firmware_reported
    return self.y_drive_acceleration_increments_to_mm(self.y_acceleration_increment_default)

  @property
  def z_drive_speed_default(self) -> float:
    """Z-drive speed a move uses when the caller names none (mm/s)."""
    if self.z_drive_speed_firmware_reported is not None:
      return self.z_drive_speed_firmware_reported
    return self.z_drive_increments_to_mm(self.z_speed_increment_default)

  @property
  def z_drive_acceleration_default(self) -> float:
    """Z-drive acceleration a move uses when the caller names none (mm/s2)."""
    if self.z_drive_acceleration_firmware_reported is not None:
      return self.z_drive_acceleration_firmware_reported
    return self.z_drive_acceleration_increments_to_mm(self.z_acceleration_increment_default)

  # -- the windows the driver works in, from the increments the drives accept --------------------

  @property
  def y_range(self) -> Tuple[float, float]:
    """Y-drive position window (mm), at channel A1.

    What the command accepts, which is wider than what a given machine allows: what an arm reaches
    depends on what else is mounted on it.
    """
    low, high = self.y_increment_range
    return (self.y_drive_increments_to_mm(low), self.y_drive_increments_to_mm(high))

  @property
  def y_speed_range(self) -> Tuple[float, float]:
    """Y-drive speed window (mm/s)."""
    low, high = self.y_speed_increment_range
    return (self.y_drive_increments_to_mm(low), self.y_drive_increments_to_mm(high))

  @property
  def y_acceleration_range(self) -> Tuple[float, float]:
    """Y-drive acceleration window (mm/s2)."""
    low, high = self.y_acceleration_increment_range
    return (
      self.y_drive_acceleration_increments_to_mm(low),
      self.y_drive_acceleration_increments_to_mm(high),
    )

  @property
  def z_range_documented(self) -> Tuple[float, float]:
    """The Z window the drive documents, in mm.

    What the drive says it reaches, which is not the same as what a given unit does - the ceiling
    is probed at setup and replaces this one. Pure: it reads nothing and changes nothing.
    """
    low, high = self.z_increment_range
    return (self.z_drive_increments_to_mm(low), self.z_drive_increments_to_mm(high))

  @property
  def z_speed_range(self) -> Tuple[float, float]:
    """Z-drive speed window (mm/s)."""
    low, high = self.z_speed_increment_range
    return (self.z_drive_increments_to_mm(low), self.z_drive_increments_to_mm(high))

  @property
  def z_acceleration_range(self) -> Tuple[float, float]:
    """Z-drive acceleration window (mm/s2)."""
    low, high = self.z_acceleration_increment_range
    return (
      self.z_drive_acceleration_increments_to_mm(low),
      self.z_drive_acceleration_increments_to_mm(high),
    )

  @property
  def channel_array_size_x(self) -> float:
    """How wide the channel array is: the first column's centre to the last's, in mm.

    What the resource modelling the head spans, so that channel A1 lands on its left back corner.
    The body around the channels is larger, and by how much is not read from anywhere.
    """
    return (self.channel_columns - 1) * self.channel_pitch

  @property
  def channel_array_size_y(self) -> float:
    """How deep the channel array is: the first row's centre to the last's, in mm."""
    return (self.channel_rows - 1) * self.channel_pitch

  # -- conversions: the wire counts in increments, the driver speaks mm and uL -------------------

  def y_drive_increments_to_mm(self, increments: int) -> float:
    """A Y-drive position in mm, from the increments the drive counts in."""
    return round(increments * self.y_drive_mm_per_increment, 2)

  def y_drive_mm_to_increments(self, mm: float) -> int:
    """A Y-drive position in increments, from mm."""
    return round(mm / self.y_drive_mm_per_increment)

  def y_drive_acceleration_increments_to_mm(self, increments: int) -> float:
    """A Y-drive acceleration in mm/s2, from the increments the drive counts it in."""
    return round(increments * self.y_drive_acceleration_mm_per_increment, 2)

  def y_drive_acceleration_mm_to_increments(self, mm: float) -> int:
    """A Y-drive acceleration in the increments the drive counts it in, from mm/s2."""
    return round(mm / self.y_drive_acceleration_mm_per_increment)

  def z_drive_increments_to_mm(self, increments: int) -> float:
    """A Z-drive position in mm, from increments."""
    return round(increments * self.z_drive_mm_per_increment, 2)

  def z_drive_mm_to_increments(self, mm: float) -> int:
    """A Z-drive position in increments, from mm."""
    return round(mm / self.z_drive_mm_per_increment)

  def z_drive_acceleration_increments_to_mm(self, increments: int) -> float:
    """A Z-drive acceleration in mm/s2, from the increments the drive counts it in."""
    return round(increments * self.z_drive_acceleration_mm_per_increment, 2)

  def z_drive_acceleration_mm_to_increments(self, mm: float) -> int:
    """A Z-drive acceleration in the increments the drive counts it in, from mm/s2."""
    return round(mm / self.z_drive_acceleration_mm_per_increment)

  def dispensing_drive_increments_to_uL(self, increments: int) -> float:
    """A dispensing-drive position as the volume it holds, from increments."""
    return round(increments * self.dispensing_drive_uL_per_increment, 2)

  def dispensing_drive_uL_to_increments(self, uL: float) -> int:
    """A dispensing-drive position in increments, from the volume to hold."""
    return round(uL / self.dispensing_drive_uL_per_increment)

  def dispensing_drive_increments_to_mm(self, increments: int) -> float:
    """A dispensing-drive position as how far the piston has travelled, from increments."""
    return round(increments * self.dispensing_drive_mm_per_increment, 2)

  def dispensing_drive_mm_to_increments(self, mm: float) -> int:
    """A dispensing-drive position in increments, from how far the piston should travel."""
    return round(mm / self.dispensing_drive_mm_per_increment)

  def squeezer_drive_increments_to_mm(self, increments: int) -> float:
    """A squeezer-drive position in mm, from increments."""
    return round(increments * self.squeezer_drive_mm_per_increment, 2)

  def squeezer_drive_mm_to_increments(self, mm: float) -> int:
    """A squeezer-drive position in increments, from mm."""
    return round(mm / self.squeezer_drive_mm_per_increment)


class Head:
  """A head: the block of channels that works a whole plate at once.

  A head is addressed as its own module, but the commands that move it as a whole go to the
  master, so this capability speaks to both. What differs between heads at this level its
  configuration states - down to which module answers for it and how wide each parameter is
  written; the commands themselves are the same. Tip handling and liquid handling are not at this
  level and are not the same between the two, so each head carries its own.
  """

  configuration: HeadConfiguration
  """The head's device facts. Each head narrows this to its own, so a subclass reads and writes
  what only it has without anything having to be cast."""

  def __init__(self, driver: "STARDriver", configuration: HeadConfiguration):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the head's device facts.
    """
    self._driver = driver
    # The head on the deck, when the driver was given one. Setup puts it there, as a child of the
    # arm it rides; moves keep it in step. Without a deck it stays None and nothing is modelled.
    self.resource: Optional[NChannelPipette] = None
    self.configuration = configuration

  def require_drive_parameter(self, parameter: str) -> int:
    """The width one of the head's stored drive parameters is written in.

    Args:
      parameter: `yv` or `yr` for the Y drive's speed and acceleration, `zv` or `zr` for the Z
        drive's.

    Returns:
      How many digits it takes on the wire.

    Raises:
      ValueError: If it is not one of those four.
    """
    if parameter not in self.configuration.drive_parameters:
      raise ValueError(
        f"unknown drive parameter {parameter!r}, expected one of {tuple(self.configuration.drive_parameters)}"
      )
    return self.configuration.drive_parameters[parameter]

  # ----------------------------------------
  # Setup
  # ----------------------------------------

  # -- discovery ---------------------------------------------------------------------------------

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    """Request the head's firmware version and build date.

    Returns:
      The version string and its build date.
    """
    resp = await self._driver.send_command(module=self.configuration.module, command="RF")
    return resp.split("rf")[-1], parse_firmware_version_date(resp)

  async def request_hardware(self) -> List[str]:
    """Request the head's configuration, undecoded.

    The head returns ten blank-separated decimal values, of which index 0 is clot monitoring with
    cLLD on every head. What the rest mean is the head's own; `_record_hardware` decodes them.

    Returns:
      The positional tokens, as reported.
    """
    resp: str = await self._driver.send_command(module=self.configuration.module, command="QU")
    return resp.split("au")[-1].split()

  def _apply_firmware_generation(self) -> None:
    """Correct whatever depends on which firmware generation this head runs.

    Called once the version is known and before any drive is read, since a generation can change
    what an increment is worth and how wide a parameter is written.
    """

  def _record_hardware(self, hardware: List[str]) -> None:
    """Record what this head's configuration bytes mean, past the clot-monitoring flag at index 0.

    Args:
      hardware: the tokens `request_hardware` read.
    """
    raise NotImplementedError("a head decodes its own configuration bytes")

  async def request_head_type(self) -> str:
    """Request which head is fitted.

    Returns:
      The head type, or "unknown" for a code this driver does not know.
    """
    resp = await self._driver.send_command(
      module=self.configuration.module, command="QG", fmt="qg#"
    )
    return self.configuration.head_types.get(cast(int, resp["qg"]), "unknown")

  async def request_x_offset(self) -> float:
    """Request the X distance from the X-arm carriage center to head channel A1.

    Stored in the master EEPROM and read with the generic master-EEPROM read, mirroring the
    iSWAP's rotation-drive offset. Needed to derive the carriage X from a target A1 X.

    Returns:
      The offset in mm.
    """
    # 4-digit field: a head's offset is ~10x the iSWAP's (hundreds of mm against ~34 mm), so it
    # exceeds 3 digits in 0.1 mm units - a 3-digit field silently truncates 3684 -> 368.
    parameter = self.configuration.x_offset_parameter
    resp = await self._driver.send_command(
      module="C0", command="RA", ra=parameter, fmt=f"{parameter}####"
    )
    return cast(int, resp[parameter]) / 10.0

  def _drive_parameter_to_mm(self, parameter: str, increments: int) -> float:
    """One of the head's stored drive parameters in mm/s or mm/s2, from what the drive counts in.

    Args:
      parameter: which parameter the value belongs to.
      increments: the value as read.

    Returns:
      The value in standard units.
    """
    c = self.configuration
    if parameter == "yv":
      return c.y_drive_increments_to_mm(increments)
    if parameter == "yr":
      return c.y_drive_acceleration_increments_to_mm(increments)
    if parameter == "zv":
      return c.z_drive_increments_to_mm(increments)
    if parameter in ("dv", "dr"):
      return c.dispensing_drive_increments_to_uL(increments)
    if parameter in ("sv", "sr"):
      return c.squeezer_drive_increments_to_mm(increments)
    return c.z_drive_acceleration_increments_to_mm(increments)

  def _drive_parameter_to_increments(self, parameter: str, value: float) -> int:
    """One of the head's stored drive parameters in what the drive counts in, from mm/s or mm/s2.

    Args:
      parameter: which parameter the value belongs to.
      value: the value in standard units.

    Returns:
      The value in increments.
    """
    c = self.configuration
    if parameter == "yv":
      return c.y_drive_mm_to_increments(value)
    if parameter == "yr":
      return c.y_drive_acceleration_mm_to_increments(value)
    if parameter == "zv":
      return c.z_drive_mm_to_increments(value)
    if parameter in ("dv", "dr"):
      return c.dispensing_drive_uL_to_increments(value)
    if parameter in ("sv", "sr"):
      return c.squeezer_drive_mm_to_increments(value)
    return c.z_drive_acceleration_mm_to_increments(value)

  async def request_drive_parameter(self, parameter: str) -> float:
    """Request one of the head's stored drive parameters.

    Args:
      parameter: the parameter to read - `yv` and `yr` for Y-drive speed and acceleration, `zv`
        and `zr` for the Z drive.

    Returns:
      The value in mm/s or mm/s2, converted from the increments the drive counts in.

    Raises:
      ValueError: If the parameter is not one of the four drive parameters.
    """
    width = self.require_drive_parameter(parameter)
    resp = await self._driver.send_command(
      module=self.configuration.module, command="RA", ra=parameter, fmt=f"{parameter}{'#' * width}"
    )
    return self._drive_parameter_to_mm(parameter, cast(int, resp[parameter]))

  async def set_drive_parameter(self, parameter: str, value: float) -> None:
    """Write one of the head's stored drive parameters.

    Args:
      parameter: the parameter to write, named as `request_drive_parameter` names it.
      value: the value in mm/s or mm/s2, converted to the increments the drive counts in.

    Raises:
      ValueError: If the parameter is not one of the four drive parameters.
    """
    width = self.require_drive_parameter(parameter)
    increments = self._drive_parameter_to_increments(parameter, value)
    written: Dict[str, Any] = {parameter: f"{increments:0{width}}"}
    await self._driver.send_command(module=self.configuration.module, command="AA", **written)

  async def _reported_drive_parameter(self, parameter: str) -> Optional[float]:
    """What the head currently holds for one drive parameter, or None if it will not say.

    A head that refuses keeps what its firmware documents rather than failing setup over a default.
    """
    try:
      return await self.request_drive_parameter(parameter)
    except Exception:
      logger.warning("the head did not report %s; keeping what its firmware documents", parameter)
      return None

  async def discover(self):
    """Read what head this is and what it can do. Read-only: nothing moves."""
    c = self.configuration
    c.firmware_version, firmware_date = await self.request_firmware_version()
    c.firmware_date = firmware_date
    # Before anything reads a drive: what a parameter is worth, and how wide it is written, can
    # depend on which generation this head runs, and the reads below use both.
    self._apply_firmware_generation()
    if firmware_date.year < self.configuration.first_documented_firmware_year:
      logger.warning(
        "this head reports %s firmware, older than the generation the drive windows and encoder "
        "resolutions here were taken from. What its drives accept, the volumes it reports, and "
        "the windows derived from them may be wrong. Set them on its configuration to correct it.",
        firmware_date,
      )

    hardware = await self.request_hardware()
    c.supports_clot_monitoring_clld = bool(int(hardware[0]))
    self._record_hardware(hardware)
    c.head_type = await self.request_head_type()
    c.x_offset = await self.request_x_offset()

    c.y_drive_speed_firmware_reported = await self._reported_drive_parameter("yv")
    c.y_drive_acceleration_firmware_reported = await self._reported_drive_parameter("yr")
    c.z_drive_speed_firmware_reported = await self._reported_drive_parameter("zv")
    c.z_drive_acceleration_firmware_reported = await self._reported_drive_parameter("zr")

  def require_tip_discard_location(self, location: Optional[Coordinate]) -> Coordinate:
    """Where tips are to be dropped, falling back to this head's configured trash.

    Args:
      location: what the caller asked for, in deck mm at head channel A1, or None to use the
        configured trash.

    Returns:
      Where to drop them.

    Raises:
      ValueError: If nothing was given and no trash is configured.
    """
    if location is None:
      location = self.configuration.tip_discard_location
    if location is None:
      raise ValueError(
        "nowhere to discard tips: this head has no trash configured. Pass a location, or set "
        "`configuration.tip_discard_location` to where its waste sits, at head channel A1."
      )
    return location

  # -- initialization ----------------------------------------------------------------------------

  async def initialize(
    self,
    tip_discard_location: Optional[Coordinate] = None,
    z_position_at_the_command_end: Optional[float] = None,
    read_timeout: int = 60,
  ):
    """Initialize the head, discarding whatever is mounted on it.

    This moves the head: it travels to the position given and ejects there, so that position must
    be somewhere tips may be dropped. The firmware wants the location of the head's channel A1.

    Args:
      tip_discard_location: where to eject, in deck mm, at head channel A1. Defaults to
        `configuration.tip_discard_location`.
      z_position_at_the_command_end: Z to leave the head at, in mm. Defaults to
        `configuration.traversal_z_position`.

    Raises:
      ValueError: If no position was given and none is configured.
    """
    if z_position_at_the_command_end is None:
      z_position_at_the_command_end = self.configuration.traversal_z_position
    tip_discard_location = self.require_tip_discard_location(tip_discard_location)
    parameters: Dict[str, Any] = {
      "xs": f"{abs(round(tip_discard_location.x * 10)):05}",
      "xd": 0 if tip_discard_location.x >= 0 else 1,
      self.configuration.y_parameter: f"{abs(round(tip_discard_location.y * 10)):04}",
      self.configuration.z_parameter: f"{round(tip_discard_location.z * 10):04}",
      self.configuration.z_end_parameter: f"{round(z_position_at_the_command_end * 10):04}",
    }
    return await self._driver.send_command(
      module="C0",
      command=self.configuration.initialize_command,
      read_timeout=read_timeout,
      **parameters,
    )

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  # -- tips --------------------------------------------------------------------------------------

  async def request_tip_presence(self) -> bool:
    """Measure whether the head is carrying tips.

    One bit for the whole head: the instrument counts tips as a rack, not as channels, so it can
    say that some are mounted and never which. A model that tracks them per channel is finer than
    anything this can confirm, and this is what it has to be reconciled against.

    Returns:
      Whether the head reports tips mounted.
    """
    command = self.configuration.tip_presence_command
    field = command.lower()
    resp = await self._driver.send_command(module="C0", command=command, fmt=f"{field}#")
    return cast(int, resp[field]) == 1

  async def request_position(self) -> Coordinate:
    """Measure where head channel A1 is, with whatever it carries taken into account.

    The master answers with the tip bottom rather than the drive's own reference, so with tips on
    this reads lower than `request_z_position` by however far they stand proud of the head. With
    none on, the two agree. Nothing is recorded: `request_z_position` is what the model follows,
    and this is the reading it is checked against.

    Returns:
      Where channel A1 is, in deck mm, at the bottom of whatever is mounted.
    """
    c = self.configuration
    resp = await self._driver.send_command(
      module="C0",
      command=c.position_command,
      fmt=f"xs#####xd#{c.y_parameter}####{c.z_parameter}####",
    )
    x = cast(int, resp["xs"]) / 10
    return Coordinate(
      x=x if resp["xd"] == 0 else -x,
      y=cast(int, resp[c.y_parameter]) / 10,
      z=cast(int, resp[c.z_parameter]) / 10,
    )

  async def request_tip_overhang(self) -> float:
    """Measure how far the tips the head carries stand below its own reference point.

    Both readings are of the same head at the same moment, so the difference is the overhang
    without anything having to move: `request_z_position` reports the head's lowest fixed feature,
    `request_position` reports the bottom of what is mounted on it. This is what a Z target has to
    be offset by for the tip end, rather than the head, to land where it is wanted.

    Returns:
      The overhang in mm. Legacy's tip length is this plus the tip's fitting depth.

    Raises:
      RuntimeError: If the head is carrying no tips, so there is nothing to measure.
    """
    if not await self.request_tip_presence():
      raise RuntimeError("the head reports no tips mounted, so there is no overhang to measure")
    reference = await self.request_z_position()
    tip_bottom = (await self.request_position()).z
    return round(reference - tip_bottom, 2)

  # -- x position, carried by the arm the head rides ---------------------------------------------

  @property
  def arm(self) -> Optional["XArm"]:
    """The arm carrying this head, on a machine that has put it on one.

    Not whichever arm is present: on a machine with two, the head is on one of them and its X, its
    travel and anything it might collide with are that one's.
    """
    return next((a for a in self._driver.arms if a.head96 is self or a.head384 is self), None)

  async def request_x_position(self) -> float:
    """Request where along X channel A1 is, in deck mm.

    The head has no X drive of its own: it rides the arm, and sits `configuration.x_offset` left of
    the carriage reference point. So this asks the arm and applies the offset, rather than reading a
    drive. Nothing is recorded either - the resource modelling the head is a child of the arm's, so
    its X follows the arm without anything having to write it.

    Returns:
      The position in mm.

    Raises:
      RuntimeError: If no arm is installed, or the head's X offset was not read at discovery.
    """
    arm = self.arm
    if arm is None:
      raise RuntimeError("this head is not on either arm; have you called `star.setup()`?")
    if self.configuration.x_offset is None:
      raise RuntimeError("the head's X offset was not read; have you called `star.setup()`?")
    return round(await arm.request_position() - self.configuration.x_offset, 2)

  # -- y position --------------------------------------------------------------------------------

  async def request_y_position(self) -> float:
    """Request where along Y the head is.

    The drive answers with two counters, the firmware's and the hardware's; the hardware's is what
    this returns, as the Z read does.

    Returns:
      The position in mm.
    """
    resp = await self._driver.send_command(
      module=self.configuration.module, command="RY", fmt="ry##### (n)"
    )
    increments = cast(List[int], resp["ry"])[1]
    y = self.configuration.y_drive_increments_to_mm(increments)
    self.update_location_by_reference_point(y=y)
    return y

  async def request_predefined_y_positions(self) -> List[float]:
    """Request the Y positions the head has stored, in mm.

    The head keeps ten of them in non-volatile memory. The first is the home position the Y drive
    parks at; the rest are further slots this capability sends no command against, so they are
    returned as read rather than named. A head that stores them as offsets says so through
    `configuration.predefined_y_position_origin`, which is added here so what comes back is
    comparable with `request_y_position`.

    Returns:
      The ten stored positions in mm, the first being home.
    """
    c = self.configuration
    resp = await self._driver.send_command(
      module=self.configuration.module, command="RA", ra="py", fmt="py##### (n)"
    )
    increments = cast(List[int], resp["py"])
    return [c.y_drive_increments_to_mm(i + c.predefined_y_position_origin) for i in increments]

  async def park(
    self,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
  ) -> float:
    """Send the head to its park position. This moves it in Z, then in Y.

    In that order and separately, rather than through the firmware's own home command: the head
    crosses the deck to get there, so it is raised clear first and only then moved across. Where it
    parks is the first of the Y positions the head has stored, read rather than assumed, since an
    adjusted head parks where its own memory says.

    Args:
      speed: how fast to travel in Y, in mm/s. Defaults to the drive's own.
      acceleration: how hard, in mm/s2. Defaults to the drive's own.

    Returns:
      Where it parked, in mm.
    """
    await self.move_to_safe_z()
    park_position = (await self.request_predefined_y_positions())[0]
    await self.move_y(park_position, speed=speed, acceleration=acceleration)
    return park_position

  def update_location_by_reference_point(
    self, y: Optional[float] = None, z: Optional[float] = None
  ) -> None:
    """Record where the head is on the resource that models it.

    Y and Z only: the head rides the arm, so its resource is a child of the arm's and follows it in
    X without anything having to record that. Both drives report channel A1, and a resource is
    located by its left front bottom corner, so what is recorded is offset by where A1's mounting
    shaft sits inside the head - which is a measurement of the head, not its array's edge.

    Both drives answer in the deck's frame, and a resource's location is measured from its parent -
    which for the head is the arm, not the deck. The two differ by wherever the arm sits, so the
    arm's own position is taken out before either value is recorded. Does nothing when the driver
    was given no deck, and so has nothing to model.

    Args:
      y: where channel A1 is now, in mm on the deck. Left as it was when None.
      z: where the head's lowest fixed feature is now, in mm on the deck. Left as it was when None.
    """
    deck = self._driver.deck
    if self.resource is None or self.resource.location is None or deck is None:
      return
    arm = self.resource.parent
    if arm is None:
      return
    shaft = self.resource.get_item(HEAD_REFERENCE_SHAFT).location
    if shaft is None:
      return
    here, on_the_arm = self.resource.location, arm.get_location_wrt(deck)
    self.resource.location = Coordinate(
      here.x,
      here.y if y is None else y - on_the_arm.y - shaft.y,
      here.z if z is None else z - on_the_arm.z - shaft.z,
    )

  def _check_reachable(self, axis: Literal["x", "y", "z"], value: float) -> None:
    """Raise if the head cannot be sent where it is being asked to go.

    The one gate every position passes through, so that what the head is allowed to do is decided
    in one place: travel limits now, and whatever else has to be true before it moves - a declared
    side panel, what the other arm is doing, what is on the deck - as they are added.

    Each axis is bounded at its own reference point: channel A1 across X and Y, the head's lowest
    fixed feature along Z. X is the arm's travel rather than a drive of the head's own, since the
    head rides the arm and the arm's window already has any left side panel taken out of it.

    Args:
      axis: which axis - `x` along the rail, `y` across the arm, `z` up and down.
      value: where it would be sent, in mm.

    Raises:
      ValueError: If the head cannot reach it.
      RuntimeError: If the window was not resolved, so how far this head reaches is unknown.
    """
    if axis == "x":
      arm, x_offset = self.arm, self.configuration.x_offset
      if arm is None or arm.configuration.x_range is None or x_offset is None:
        raise RuntimeError("the head's X travel is not known; have you called `star.setup()`?")
      # The arm's travel is its carriage's; A1 rides that far to the left of it.
      arm_low, arm_high = arm.configuration.x_range
      low, high = round(arm_low - x_offset, 2), round(arm_high - x_offset, 2)
    elif axis == "y":
      low, high = self.configuration.y_range
    else:
      z_range = self.configuration.z_range
      if z_range is None:
        raise RuntimeError("the head's Z window was not probed; have you called `star.setup()`?")
      low, high = z_range
    if not low <= value <= high:
      raise ValueError(f"{axis} must be between {low} and {high}, is {value}")

  def _check_move(
    self,
    axis: Literal["y", "z"],
    value: float,
    speed: float,
    acceleration: float,
    current_limit: int,
  ) -> None:
    """Raise unless every part of a move is inside what the drive accepts.

    Args:
      axis: which drive - `y` across the arm, `z` up and down.
      value: where it would be sent, in mm.
      speed: how fast, in mm/s.
      acceleration: how hard, in mm/s2.
      current_limit: the motor current limit.

    Raises:
      ValueError: If any of them is outside the drive's window.
      RuntimeError: If the Z window was not resolved.
    """
    c = self.configuration
    self._check_reachable(axis, value)
    speed_range = c.y_speed_range if axis == "y" else c.z_speed_range
    acceleration_range = c.y_acceleration_range if axis == "y" else c.z_acceleration_range
    for checked, (low, high), name in (
      (speed, speed_range, "speed"),
      (acceleration, acceleration_range, "acceleration"),
    ):
      if not low <= checked <= high:
        raise ValueError(f"{name} must be between {low} and {high}, is {checked}")
    low_limit, high_limit = c.current_limit_range
    if not low_limit <= current_limit <= high_limit:
      raise ValueError(
        f"current_limit must be between {low_limit} and {high_limit}, is {current_limit}"
      )

  async def move_y(
    self,
    y: float,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
    read_timeout: int = 30,
  ):
    """Move the head along Y. This moves it, and nothing else on the arm.

    The move writes its speed and acceleration into the drive's volatile register, where later
    moves would inherit them, so what was there is read first and put back afterwards - skipping
    the write where the move's value already matches.

    Args:
      y: where to move to, in mm.
      speed: how fast, in mm/s. Defaults to `configuration.y_drive_speed_default`.
      acceleration: how hard, in mm/s2. Defaults to `configuration.y_drive_acceleration_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.y_drive_current_limit_default`.

    Raises:
      ValueError: If an argument is outside what the drive accepts.
    """
    c = self.configuration
    if speed is None:
      speed = c.y_drive_speed_default
    if acceleration is None:
      acceleration = c.y_drive_acceleration_default
    if current_limit is None:
      current_limit = c.y_drive_current_limit_default

    self._check_move("y", y, speed, acceleration, current_limit)
    was_speed = await self.request_drive_parameter("yv")
    was_acceleration = await self.request_drive_parameter("yr")
    try:
      return await self._driver.send_command(
        module=self.configuration.module,
        command="YA",
        ya=f"{c.y_drive_mm_to_increments(y):05}",
        yv=f"{c.y_drive_mm_to_increments(speed):0{self.configuration.drive_parameters['yv']}}",
        yr=f"{c.y_drive_acceleration_mm_to_increments(acceleration):0{self.configuration.drive_parameters['yr']}}",
        yw=f"{current_limit:0{len(str(c.current_limit_range[1]))}}",
        read_timeout=read_timeout,
      )
    finally:
      # Where the drive says it went, which the read records. Asked whether the move succeeded or
      # not: a move that failed part way left the head somewhere neither position describes.
      await self.request_y_position()
      await self._restore_drive_parameter("yv", speed, was_speed)
      await self._restore_drive_parameter("yr", acceleration, was_acceleration)

  async def _restore_drive_parameter(self, parameter: str, written: float, was: float) -> None:
    """Put back what a move overwrote in the drive's volatile register.

    Compared in increments rather than in mm, because that is what the drive holds: two values
    that round to the same increment are the same write.

    Args:
      parameter: the parameter the move wrote.
      written: what the move wrote, in standard units.
      was: what was there before, in standard units.
    """
    if self._drive_parameter_to_increments(
      parameter, written
    ) != self._drive_parameter_to_increments(parameter, was):
      await self.set_drive_parameter(parameter, was)

  # -- z position --------------------------------------------------------------------------------

  async def request_z_position(self) -> float:
    """Request the head's Z-drive position, at its lowest fixed feature.

    This is the raw drive position regardless of tip state, not the tip bottom.

    Returns:
      The position in mm.
    """
    resp = await self._driver.send_command(
      module=self.configuration.module, command="RZ", fmt="rz##### (n)"
    )
    increments = cast(List[int], resp["rz"])[1]  # [0] = firmware counter, [1] = hardware counter
    z = self.configuration.z_drive_increments_to_mm(increments)
    self.update_location_by_reference_point(z=z)
    return z

  async def request_predefined_z_positions(self) -> List[float]:
    """Request the Z positions the head has stored, in mm.

    The head keeps ten of them in non-volatile memory. The first is the home position the Z drive
    parks at; the rest are further slots this capability sends no command against, so they are
    returned as read rather than named. A head that stores them as offsets says so through
    `configuration.predefined_z_position_origin`, which is added here. These are positions of the
    head's lowest fixed feature, as `request_z_position` is.

    Returns:
      The ten stored positions in mm, the first being home.
    """
    c = self.configuration
    resp = await self._driver.send_command(
      module=self.configuration.module, command="RA", ra="pz", fmt="pz##### (n)"
    )
    increments = cast(List[int], resp["pz"])
    return [c.z_drive_increments_to_mm(i + c.predefined_z_position_origin) for i in increments]

  async def probe_z_max(self, read_timeout: int = RETRACT_READ_TIMEOUT) -> float:
    """Find out how high this head reaches. Retracts the head.

    Not something it reports: the command range can exceed what a given unit reaches, so the top is
    driven to and read back, using the firmware's own retract. Setup calls this once, before any
    window exists - which is why the retract here is that command rather than `move_to_safe_z`,
    whose target this establishes. What it finds becomes the ceiling of `configuration.z_range`,
    whose floor is what the drive documents.

    Args:
      read_timeout: how long to wait for the retract, in seconds. It drives the head the length of
        its travel, so it takes a while.

    Returns:
      The highest Z this head reaches, in mm.
    """
    await self._driver.send_command(
      module="C0", command=self.configuration.retract_command, read_timeout=read_timeout
    )
    z_max = await self.request_z_position()
    c = self.configuration
    c.z_range = (c.z_range_documented[0], z_max)
    return z_max

  async def move_z(
    self,
    z: float,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
    read_timeout: int = 30,
  ):
    """Move the head along Z. This moves it, and nothing else on the arm.

    The move writes its speed and acceleration into the drive's volatile register, where later
    moves would inherit them, so what was there is read first and put back afterwards - skipping
    the write where the move's value already matches.

    Args:
      z: where to move the head's lowest fixed feature to, in mm.
      speed: how fast, in mm/s. Defaults to `configuration.z_drive_speed_default`.
      acceleration: how hard, in mm/s2. Defaults to `configuration.z_drive_acceleration_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.z_drive_current_limit_default`.

    Raises:
      ValueError: If an argument is outside what the drive accepts.
    """
    c = self.configuration
    if speed is None:
      speed = c.z_drive_speed_default
    if acceleration is None:
      acceleration = c.z_drive_acceleration_default
    if current_limit is None:
      current_limit = c.z_drive_current_limit_default

    self._check_move("z", z, speed, acceleration, current_limit)
    was_speed = await self.request_drive_parameter("zv")
    was_acceleration = await self.request_drive_parameter("zr")
    try:
      return await self._driver.send_command(
        module=self.configuration.module,
        command="ZA",
        za=f"{c.z_drive_mm_to_increments(z):05}",
        zv=f"{c.z_drive_mm_to_increments(speed):0{self.configuration.drive_parameters['zv']}}",
        zr=f"{c.z_drive_acceleration_mm_to_increments(acceleration):0{self.configuration.drive_parameters['zr']}}",
        zw=f"{current_limit:0{len(str(c.current_limit_range[1]))}}",
        read_timeout=read_timeout,
      )
    finally:
      # Where the drive says it went, which the read records. Asked whether the move succeeded or
      # not: a move that failed part way left the head somewhere neither position describes.
      await self.request_z_position()
      await self._restore_drive_parameter("zv", speed, was_speed)
      await self._restore_drive_parameter("zr", acceleration, was_acceleration)

  async def move_to_safe_z(
    self,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
  ) -> float:
    """Move the head up to its safe Z: the top of the window `probe_z_max` probed.

    The precondition for any lateral move, so it runs often. An ordinary Z move to a known height,
    not a command of its own - so it is bounded, and its speed and acceleration are the caller's
    like any other move. The firmware's own retract runs once, inside `probe_z_max`, which is
    what establishes the height this moves to.

    Args:
      speed: how fast, in mm/s. Defaults to `configuration.z_drive_speed_default`.
      acceleration: how hard, in mm/s2. Defaults to `configuration.z_drive_acceleration_default`.

    Returns:
      The Z position at the safety height, in mm.

    Raises:
      RuntimeError: If the Z window was not probed, so the safe height is unknown.
    """
    z_range = self.configuration.z_range
    if z_range is None:
      raise RuntimeError("the head's Z window was not probed; have you called `star.setup()`?")
    await self.move_z(z_range[1], speed=speed, acceleration=acceleration)
    return await self.request_z_position()

  # -- dispensing drive --------------------------------------------------------------------------
