"""The autoload: the belt and wheel that pull carriers onto the deck and push them back out."""

import datetime
import logging
import string
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, cast

from pylabrobot.hamilton.protocol.text.framing import parse_firmware_version_date
from pylabrobot.resources.barcode import Barcode1DSymbology, Barcode2DSymbology
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)

# Where the carrier drive can be sent by name.
YPosition = Literal["loading_tray", "carrier_identification", "deck"]

ZPosition = Literal["below", "above"]

ScannerRotation = Literal["vertical", "horizontal", "undefined"]

# Which way a 2D reader looks. A 1D scanner has no such setting.
ScanDirection = Literal["vertical", "horizontal", "omnidirectional", "vertical and horizontal"]

# The mask each symbology holds. `ANY 1D` is legacy's wildcard, the seven the master names.
SYMBOLOGY_MASKS_1D: Dict[Barcode1DSymbology, int] = {
  "ISBT Standard": 0x01,
  "Code 128 (Subset B and C)": 0x02,
  "Code 39": 0x04,
  "Codebar": 0x08,
  "Code 2of5 Interleaved": 0x10,
  "UPC A/E": 0x20,
  "YESN/EAN 8": 0x40,
  "ANY 1D": 0x7F,  # bit 7 is left out: the master's table does not name it
}

# The second mask a 2D reader takes.
SYMBOLOGY_MASKS_2D: Dict[Barcode2DSymbology, int] = {
  "Data Matrix": 0x01,
  "QR Code": 0x02,
  "Maxi Code": 0x04,
  "Aztec": 0x08,
  "PDF 417": 0x10,
  "Micro PDF 417": 0x20,
  "GS1 DataBar": 0x40,
  "EAN/UCC Comp": 0x80,
  "ANY 2D": 0xFF,
}

BarcodeReadingDirection = Literal["vertical", "horizontal"]

# What kind of autoload is fitted, by the code the master answers with. Codes outside this are
# variants that have not been seen, and are returned as they came.
AUTOLOAD_TYPES: Dict[int, str] = {
  0: "1D barcode scanner",
  1: "XRP Lite",
  2: "2D barcode scanner",
}


def _tracks_from_presence_mask(mask: str) -> List[int]:
  """The tracks a carrier-presence mask marks as occupied.

  Args:
    mask: the mask as the machine writes it, one hexadecimal digit per four tracks, the rightmost
      digit holding tracks 1 to 4.

  Returns:
    The occupied tracks, counted from 1, in order.

  Raises:
    ValueError: If the mask is not hexadecimal.
  """
  mask = mask.strip()
  if mask == "" or any(character not in string.hexdigits for character in mask):
    raise ValueError(f"not a hexadecimal carrier presence mask: {mask!r}")
  return [
    digit_index * 4 + bit + 1
    for digit_index, digit in enumerate(reversed(mask))
    for bit in range(4)
    if int(digit, 16) & (1 << bit)
  ]


@dataclass
class AutoloadConfiguration:
  """Device facts for the installed autoload.

  Three drives:
  - X-drive of the entire autoload sled;
  - Y drive (carrier handling wheel), which moves a carriers in and out;
  - Z drive (carrier handling wheel), which raises and retracts the handling wheel.
  """

  firmware_version: Optional[str] = None
  firmware_date: Optional[datetime.date] = None
  autoload_type: Optional[str] = None  # see AUTOLOAD_TYPES

  # -- what each drive can be sent to by name, and the code it takes --
  y_positions: Dict[YPosition, int] = field(
    default_factory=lambda: {"loading_tray": 0, "carrier_identification": 1, "deck": 2}
  )
  z_positions: Dict[ZPosition, int] = field(default_factory=lambda: {"below": 0, "above": 1})
  scanner_rotations: Dict[ScannerRotation, int] = field(
    default_factory=lambda: {"vertical": 0, "horizontal": 1, "undefined": 2}
  )
  barcode_reading_directions: Dict[BarcodeReadingDirection, int] = field(
    default_factory=lambda: {"vertical": 0, "horizontal": 1}
  )
  barcode_symbologies: Optional[Dict[Barcode1DSymbology, int]] = None
  """None when this autoload's type names no scanner."""
  barcode_2d_symbologies: Optional[Dict[Barcode2DSymbology, int]] = None
  """None on a 1D scanner, which takes neither this mask nor a scan direction."""
  scan_directions: Dict[ScanDirection, int] = field(
    default_factory=lambda: {
      "vertical": 0,
      "horizontal": 1,
      "omnidirectional": 2,
      "vertical and horizontal": 3,
    }
  )

  # -- scanner X drive (along the deck) --
  x_drive_mm_per_increment: float = 0.1
  """How far one step moves the scanner, in mm. Read at discovery: a unit holds either 0.1 or
  0.125 in its own memory, and this default is only right for the units that hold the first."""
  loading_indicators_installed: Optional[bool] = None
  """Whether this autoload has the per-track indicator LEDs. Read at discovery."""
  drive_zero_on_the_deck: float = 100.0
  """Where the drive counts from, on the deck: track 1, a hundred millimetres along it."""
  reference_point_from_sled_left_edge: float = 109.0
  """Where on the sled the drive's position refers to, as a distance from the sled's left edge in
  mm. The drive reports the carrier-handling wheels, and their left edge stands this far along the
  sled; it lines up with a track's origin, not its centre.

  Measured on the manufacturer's model, against the box that wraps the whole part: the wheels are a
  pair of 26 mm discs 7 mm thick, one at each face, spanning 109.0 to 116.1 mm from the box's left
  edge. They turn about x, which is the axis a carrier is drawn in along.

  This is a distance from the box's left edge, so it only means anything against the box it was
  measured in. The 20.0 it replaces was measured against a narrower one, and the left 40 mm of the
  part as now modelled is a thin tab with nothing on it."""
  x_drive_increment_range: Tuple[int, int] = (0, 12_500)
  x_drive_speed_increment_range: Tuple[int, int] = (20, 3_000)  # steps per second
  x_drive_speed_default: int = 2_500
  x_drive_acceleration_ramp_range: Tuple[int, int] = (1, 3)
  x_drive_acceleration_ramp_default: int = 3

  # -- carrier Z drive (handling wheel; the handling wheel, down or up) --
  z_drive_mm_per_increment: float = 0.004166666666666667
  z_drive_increment_range: Tuple[int, int] = (0, 3_000)
  z_drive_speed_increment_range: Tuple[int, int] = (20, 2_000)
  z_drive_speed_default: int = 1_750
  z_drive_acceleration_ramp_range: Tuple[int, int] = (1, 4)
  z_drive_acceleration_ramp_default: int = 4
  z_drive_safety_position: Optional[float] = None

  # -- carrier Y drive (handling wheel; in and out of the deck) --
  y_drive_mm_per_increment: float = 0.06404424
  y_drive_increment_range: Tuple[int, int] = (0, 9_999)
  y_drive_speed_increment_range: Tuple[int, int] = (20, 2_500)
  y_drive_speed_default: int = 2_000
  y_drive_acceleration_ramp_range: Tuple[int, int] = (1, 6)
  y_drive_acceleration_ramp_default: int = 6

  # -- shared by all three drives --
  motor_current_limit_range: Tuple[int, int] = (0, 7)  # same for every drive
  motor_current_limit_default: int = 7
  acceleration_ramp_increments_per_second_squared: int = 2_500

  # -- conversions: the wire counts in steps, the driver speaks mm ---------------------------

  def x_drive_increments_to_mm(self, increments: int) -> float:
    """How far along the deck the scanner is, in mm, from the steps the drive counts in."""
    return round(increments * self.x_drive_mm_per_increment, 2)

  def x_drive_mm_to_increments(self, mm: float) -> int:
    """A scanner position in steps, from mm."""
    return round(mm / self.x_drive_mm_per_increment)

  def to_deck_frame(self, mm: float) -> float:
    """A position the X drive reports, in the deck's frame.

    The X drive is the one thing here that does not count in the deck's coordinates: its zero sits
    `drive_zero_on_the_deck` along the deck. Every crossing between the two frames goes through
    here and `from_deck_frame`, so the offset is applied in one place. The other drives, and the
    other capabilities, need no such conversion - their axes are the deck's.
    """
    return round(mm + self.drive_zero_on_the_deck, 2)

  def from_deck_frame(self, mm: float) -> float:
    """A deck position, in the frame the X drive counts in."""
    return round(mm - self.drive_zero_on_the_deck, 2)

  def z_drive_increments_to_mm(self, increments: int) -> float:
    """How high the handling wheel is, in mm, from steps."""
    return round(increments * self.z_drive_mm_per_increment, 2)

  def z_drive_mm_to_increments(self, mm: float) -> int:
    """A wheel position in steps, from mm."""
    return round(mm / self.z_drive_mm_per_increment)

  def y_drive_increments_to_mm(self, increments: int) -> float:
    """How far in or out a carrier is, in mm, from the steps the drive counts in."""
    return round(increments * self.y_drive_mm_per_increment, 2)

  def y_drive_mm_to_increments(self, mm: float) -> int:
    """A carrier position in steps, from mm."""
    return round(mm / self.y_drive_mm_per_increment)


class Autoload:
  """The autoload.

  Reached as `driver.autoload`, on a machine that has one.
  """

  def __init__(self, driver: "STARDriver", configuration: Optional[AutoloadConfiguration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the autoload's device facts. Defaults to `AutoloadConfiguration()`.
    """
    self._driver = driver
    # The sled on the deck, when the driver was given one. Setup puts it there; moves keep it in
    # step. Without a deck it stays None and nothing is modelled.
    self.resource: Optional[Resource] = None
    self.configuration = configuration or AutoloadConfiguration()

  @property
  def track_range(self) -> range:
    """The tracks it can be moved to, one for each slot the instrument has.

    Raises:
      RuntimeError: If setup has not run, so the deck size is not known.
    """
    if self._driver.configuration is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    return range(1, self._driver.configuration.instrument_size_slots + 1)

  # -- session / discovery -------------------------------------------------------------------------

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    """Request the autoload's firmware version and build date.

    Returns:
      The version string and its build date.
    """
    resp: str = await self._driver.send_command(module="I0", command="RF")
    return resp.split("rf")[-1], parse_firmware_version_date(resp)

  async def request_autoload_type(self) -> str:
    """Request which kind of autoload is fitted.

    Returns:
      What it is, as named in `AUTOLOAD_TYPES`, or the code it answered with when that is not one
      of them.
    """
    resp = await self._driver.send_command(module="C0", command="CQ", fmt="cq#")
    code = cast(int, resp["cq"])
    return AUTOLOAD_TYPES.get(code, str(code))

  async def request_adjustment_status(self) -> Tuple[datetime.date, bool]:
    """Request when this autoload was adjusted, and whether it has been.

    Returns:
      The date of the adjustment, and whether the module considers itself adjusted. An unadjusted
      module's stored values are factory defaults rather than this unit's own.
    """
    resp = await self._driver.send_command(module="I0", command="RJ", fmt="jd&&&&&&&&&&js#")
    return (
      datetime.date.fromisoformat(cast(str, resp["jd"])),
      cast(int, resp["js"]) == 1,
    )

  async def request_init_slot(self) -> int:
    """Request the track the X drive initializes against.

    Returns:
      The track, counted from 1.
    """
    resp = await self._driver.send_command(module="I0", command="QX", fmt="bx##")
    return cast(int, resp["bx"])

  async def request_adjustment_values(self) -> str:
    """Request every adjustment value the module stores, as it writes them.

    Holds each drive's initialization position and the motor PWM tables. Returned unparsed: how
    many fields come back varies by unit, as the 96-head's equivalent read showed, and the point of
    this is to see what a unit actually holds.

    Returns:
      The reply, as the module wrote it.
    """
    return cast(str, await self._driver.send_command(module="I0", command="RK"))

  async def request_module_configuration(self) -> Tuple[float, bool]:
    """Request what this autoload is built with: its scanner's step size, and its indicators.

    The step size is the one thing here that differs between units and cannot be derived, so it is
    read rather than assumed.

    Returns:
      How far one scanner step moves it, in mm, and whether the loading indicators are fitted.
    """
    resp = await self._driver.send_command(module="I0", command="RA", ra="au", fmt="au# (n)")
    configuration = cast(List[int], resp["au"])
    return 0.1 if configuration[0] == 0 else 0.125, configuration[1] == 0

  async def request_parameter(self, parameter: str) -> str:
    """Request one of the parameters the module stores, by name.

    The way the iSWAP's predefined-position tables are read, and the 96-head's drive parameters.
    Returned unparsed: each name has its own shape, and this exists to see what a unit holds rather
    than to drive it.

    Args:
      parameter: the two-letter name, as the module's own command set writes it.

    Returns:
      The reply, as the module wrote it.
    """
    return cast(str, await self._driver.send_command(module="I0", command="RA", ra=parameter))

  async def request_initialization_status(self) -> bool:
    """Request whether the autoload reports itself initialized.

    Returns:
      Whether it is initialized. It reports itself uninitialized again once the instrument's own
      initialization has run.
    """
    resp = await self._driver.send_command(module="I0", command="QW", fmt="qw#")
    return cast(int, resp["qw"]) == 1

  async def discover(self):
    """Read what autoload this is. Read-only: nothing moves."""
    c = self.configuration
    c.firmware_version, c.firmware_date = await self.request_firmware_version()
    c.autoload_type = await self.request_autoload_type()
    (
      c.x_drive_mm_per_increment,
      c.loading_indicators_installed,
    ) = await self.request_module_configuration()
    # Both scanners read the 1D symbologies; only the 2D one also reads the 2D ones. An autoload
    # that is neither has no scanner, and its symbologies stay unset.
    if c.autoload_type in ("1D barcode scanner", "2D barcode scanner"):
      c.barcode_symbologies = SYMBOLOGY_MASKS_1D
    if c.autoload_type == "2D barcode scanner":
      c.barcode_2d_symbologies = SYMBOLOGY_MASKS_2D

  # -- initialization ------------------------------------------------------------------------------

  async def initialize(self, park_after: bool = True):
    """Initialize the autoload and everything else that makes it operational. This moves it.

    Homing is skipped when it already reports itself initialized, so this can be called on any
    machine. The rest runs either way: the wheel goes to its safe Z, and the height it comes to
    rest at is read, which no command reports directly.

    Reporting itself uninitialized after the instrument procedure has run is the machine's
    behaviour rather than a failed initialization: across 182 recorded runs it reported itself
    initialized in every run where the procedure was skipped, and uninitialized in 60 of the 61
    where it ran.

    Args:
      park_after: whether to park it once it is up, leaving it clear of the deck.
    """
    if not await self.request_initialization_status():
      logger.debug("autoload reports itself uninitialized - homing its drives")
      await self._send_command_and_update_sled_x(module="C0", command="II")
    await self.wheel_move_to_safe_z()
    self.configuration.z_drive_safety_position = await self.wheel_request_z_position()

    if park_after:
      logger.debug("parking the autoload after initialization")
      await self.park()

  # -- scanner X drive (along the deck) ------------------------------------------------------------

  async def _send_command_and_update_sled_x(self, **kwargs: Any) -> Any:
    """Send a command that moves the sled, then read back where along X it ended up.

    For the sled-moving commands that do not go through `move_to_track`, which keeps the model in
    step itself. Every command that shifts the sled has to go through one or the other, or the
    resource silently drifts from the machine.

    Read afterwards either way: a command that failed part way leaves the sled somewhere neither
    where it was nor where it was going, which is exactly when the model must not be trusted to
    have stayed put. If that read also fails, the command's own error is the one to raise.

    Args:
      kwargs: what to send, as `send_command` takes it.

    Returns:
      Whatever the command answered.
    """
    try:
      resp = await self._driver.send_command(**kwargs)
    except BaseException:
      try:
        await self.request_x_position()
      except BaseException:
        logger.warning("could not read where the autoload stopped; its model is stale")
      raise
    await self.request_x_position()
    return resp

  async def request_track(self) -> int:
    """Request the current track of the autoload's carrier handler.

    Returns:
      The track, counted from 1, or 0 when it is at neither end of a track.
    """
    resp = await self._driver.send_command(module="C0", command="QA", fmt="qa##")
    return cast(int, resp["qa"])

  async def request_x_position(self) -> float:
    """Request where along the deck the scanner is.

    What the drive answers is recorded on the resource that models the sled.

    Returns:
      The position along the deck, in mm.
    """
    c = self.configuration
    x = c.to_deck_frame(
      c.x_drive_increments_to_mm(await self._request_drive_position("RX", digits=5))
    )
    self.update_location_by_reference_point(x)
    return x

  def update_location_by_reference_point(self, x: float) -> None:
    """Record where the sled is on the resource that models it.

    What the drive reports is where the carrier-handling wheel stands. The sled is placed around
    the wheel, which sits back from its left edge. Does nothing when the driver was given no deck.

    Args:
      x: where the wheel is along the deck, in mm.
    """
    if self.resource is None or self.resource.location is None:
      return
    c = self.configuration
    self.resource.location = Coordinate(
      x - c.reference_point_from_sled_left_edge,
      self.resource.location.y,
      self.resource.location.z,
    )

  async def _request_drive_position(self, command: str, digits: int) -> int:
    """Where one of the three drives is, in the steps it counts in.

    Each answers with two counters: the one the firmware keeps, and the one read off the hardware.
    The hardware counter is the one returned.

    Args:
      command: the read to send, which names the drive.
      digits: how many digits each counter is written with.

    Returns:
      The hardware counter, in the drive's own steps.
    """
    field = command.lower()
    resp = await self._driver.send_command(
      module="I0", command=command, fmt=f"{field}{'#' * digits} (n)"
    )
    _firmware_counter, hardware_counter = cast(List[int], resp[field])
    return hardware_counter

  def _check_reachable(self, axis: Literal["x", "y", "z"], value: float) -> None:
    """Raise if a drive cannot be sent where it is being asked to go.

    Args:
      axis: which drive - `x` the sled along the deck, `y` the handling wheel in and out, `z` the
        handling wheel up and down.
      value: where it would be sent, in mm.

    Raises:
      ValueError: If the drive's travel does not reach it.
    """
    c = self.configuration
    if axis == "x":
      low, high = c.x_drive_increment_range
      low_mm = c.to_deck_frame(c.x_drive_increments_to_mm(low))
      high_mm = c.to_deck_frame(c.x_drive_increments_to_mm(high))
      increments = c.x_drive_mm_to_increments(c.from_deck_frame(value))
    elif axis == "y":
      low, high = c.y_drive_increment_range
      low_mm, high_mm = c.y_drive_increments_to_mm(low), c.y_drive_increments_to_mm(high)
      increments = c.y_drive_mm_to_increments(value)
    else:
      low, high = c.z_drive_increment_range
      low_mm, high_mm = c.z_drive_increments_to_mm(low), c.z_drive_increments_to_mm(high)
      increments = c.z_drive_mm_to_increments(value)
    if not low <= increments <= high:
      raise ValueError(f"{axis} must be between {low_mm} and {high_mm} mm, is {value}")

  async def move_to_track(
    self,
    track: int,
    speed: Optional[float] = None,
    acceleration_ramp: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the autoload to a specific track position, raising the wheel first.

    Args:
      track: which track to move to, counted from 1.
      speed: how fast to travel, in mm/s. Defaults to what
        `configuration.x_drive_speed_default` works out to.
      acceleration_ramp: how hard to accelerate, in multiples of
        `configuration.acceleration_ramp_increments_per_second_squared`. Defaults to
        `configuration.x_drive_acceleration_ramp_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.motor_current_limit_default`.

    Raises:
      ValueError: If the track is not one this machine has, or an argument is outside what the
        drive accepts.
      RuntimeError: If setup has not run.
    """
    c = self.configuration
    tracks = self.track_range

    # -- precondition checks ----------------------------------------------------------------------
    if track not in tracks:
      raise ValueError(f"track must be between {tracks[0]} and {tracks[-1]}, is {track}")

    # -- parameter resolution ----------------------------------------------------------------------
    speed = c.x_drive_increments_to_mm(c.x_drive_speed_default) if speed is None else speed
    acceleration_ramp = (
      c.x_drive_acceleration_ramp_default if acceleration_ramp is None else acceleration_ramp
    )
    current_limit = c.motor_current_limit_default if current_limit is None else current_limit

    # -- parameter validation ----------------------------------------------------------------------
    low, high = c.x_drive_speed_increment_range
    speed_increments = c.x_drive_mm_to_increments(speed)
    if not low <= speed_increments <= high:
      raise ValueError(
        f"speed must be between {c.x_drive_increments_to_mm(low)} and "
        f"{c.x_drive_increments_to_mm(high)} mm/s, is {speed}"
      )

    low, high = c.x_drive_acceleration_ramp_range
    if not low <= acceleration_ramp <= high:
      raise ValueError(
        f"acceleration_ramp must be between {low} and {high}, is {acceleration_ramp}"
      )

    low, high = c.motor_current_limit_range
    if not low <= current_limit <= high:
      raise ValueError(f"current_limit must be between {low} and {high}, is {current_limit}")

    # -- device preparation ----------------------------------------------------------------------
    current_wheel_z = await self.wheel_request_z_position()
    if c.z_drive_safety_position is not None and current_wheel_z < c.z_drive_safety_position:
      logger.debug(
        "retracting the handling wheel to its safe Z %.3f mm before moving to track %d",
        c.z_drive_safety_position,
        track,
      )
      await self.wheel_move_to_safe_z()

    return await self._send_command_and_update_sled_x(
      module="I0",
      command="XP",
      xp=f"{track:02}",
      xv=f"{speed_increments:04}",
      xr=f"{acceleration_ramp:01}",
      xw=f"{current_limit:01}",
    )

  async def move_x(
    self,
    x: float,
    speed: Optional[float] = None,
    acceleration_ramp: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the sled along the deck to a position, raising the wheel first.

    Where `move_to_track` can only reach the tracks, this reaches anywhere between them.

    Args:
      x: where to send the wheel along the deck, in mm, as `request_x_position` reports it.
      speed: how fast to travel, in mm/s. Defaults to what
        `configuration.x_drive_speed_default` works out to.
      acceleration_ramp: how hard to accelerate, in multiples of
        `configuration.acceleration_ramp_increments_per_second_squared`. Defaults to
        `configuration.x_drive_acceleration_ramp_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.motor_current_limit_default`.

    Raises:
      ValueError: If the position is outside the drive's travel, or an argument is outside what the
        drive accepts.
    """
    c = self.configuration

    # -- parameter resolution ----------------------------------------------------------------------
    speed = c.x_drive_increments_to_mm(c.x_drive_speed_default) if speed is None else speed
    acceleration_ramp = (
      c.x_drive_acceleration_ramp_default if acceleration_ramp is None else acceleration_ramp
    )
    current_limit = c.motor_current_limit_default if current_limit is None else current_limit

    # -- parameter validation ----------------------------------------------------------------------
    self._check_reachable("x", x)
    increments = c.x_drive_mm_to_increments(c.from_deck_frame(x))

    low, high = c.x_drive_speed_increment_range
    speed_increments = c.x_drive_mm_to_increments(speed)
    if not low <= speed_increments <= high:
      raise ValueError(
        f"speed must be between {c.x_drive_increments_to_mm(low)} and "
        f"{c.x_drive_increments_to_mm(high)} mm/s, is {speed}"
      )

    low, high = c.x_drive_acceleration_ramp_range
    if not low <= acceleration_ramp <= high:
      raise ValueError(
        f"acceleration_ramp must be between {low} and {high}, is {acceleration_ramp}"
      )

    low, high = c.motor_current_limit_range
    if not low <= current_limit <= high:
      raise ValueError(f"current_limit must be between {low} and {high}, is {current_limit}")

    # -- device preparation ----------------------------------------------------------------------
    current_wheel_z = await self.wheel_request_z_position()
    if c.z_drive_safety_position is not None and current_wheel_z < c.z_drive_safety_position:
      logger.debug(
        "retracting the handling wheel to its safe Z %.3f mm before moving to %.3f mm",
        c.z_drive_safety_position,
        x,
      )
      await self.wheel_move_to_safe_z()

    return await self._send_command_and_update_sled_x(
      module="I0",
      command="XA",
      xa=f"{increments:05}",
      xv=f"{speed_increments:04}",
      xr=f"{acceleration_ramp:01}",
      xw=f"{current_limit:01}",
    )

  async def move_x_relative(
    self,
    distance: float,
    speed: Optional[float] = None,
    acceleration_ramp: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the sled by a distance from where it is now.

    Where the sled is is read from the machine and the distance added to it, so a relative move is
    an absolute move to a place worked out here - and is bounded by the drive's travel like any
    other.

    Args:
      distance: how far to move, in mm. Positive moves along the deck towards higher x, negative
        back towards the deck's origin.
      speed: how fast to travel, in mm/s. Defaults to what
        `configuration.x_drive_speed_default` works out to.
      acceleration_ramp: how hard to accelerate. Defaults to
        `configuration.x_drive_acceleration_ramp_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.motor_current_limit_default`.

    Raises:
      ValueError: If the sled would end up outside the drive's travel, or an argument is outside
        what the drive accepts.
    """
    return await self.move_x(
      await self.request_x_position() + distance,
      speed=speed,
      acceleration_ramp=acceleration_ramp,
      current_limit=current_limit,
    )

  async def park(self):
    """Park the autoload at the last track this machine has.

    Raises:
      RuntimeError: If setup has not run, so the deck size is not known.
    """
    return await self.move_to_track(track=self.track_range[-1])

  # -- Z drive (the carrier handling wheel) --------------------------------------------------------

  async def wheel_request_z_position(self) -> float:
    """Request how high the carrier-handling wheel is.

    Returns:
      The position in mm, from the drive's zero.
    """
    return self.configuration.z_drive_increments_to_mm(
      await self._request_drive_position("RZ", digits=4)
    )

  async def wheel_move_to_safe_z(self) -> float:
    """Move the carrier-handling wheel to its safe Z, and read where that put it.

    Returns:
      The wheel's Z position, in mm.
    """
    await self._driver.send_command(module="C0", command="IV")
    return await self.wheel_request_z_position()

  async def wheel_move_z(
    self,
    z: float,
    speed: Optional[float] = None,
    acceleration_ramp: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the carrier-handling wheel to a Z position.

    Args:
      z: how high to move it, in mm from the drive's zero.
      speed: how fast to travel, in mm/s. Defaults to what
        `configuration.z_drive_speed_default` works out to.
      acceleration_ramp: how hard to accelerate, in multiples of
        `configuration.acceleration_ramp_increments_per_second_squared`. Defaults to
        `configuration.z_drive_acceleration_ramp_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.motor_current_limit_default`.

    Raises:
      ValueError: If the position, or an argument, is outside what the drive accepts.
    """
    c = self.configuration
    self._check_reachable("z", z)
    increments = c.z_drive_mm_to_increments(z)

    # Every parameter is sent: what the drive does is written here, not left to it.
    speed = c.z_drive_increments_to_mm(c.z_drive_speed_default) if speed is None else speed
    acceleration_ramp = (
      c.z_drive_acceleration_ramp_default if acceleration_ramp is None else acceleration_ramp
    )
    current_limit = c.motor_current_limit_default if current_limit is None else current_limit

    low, high = c.z_drive_speed_increment_range
    speed_increments = c.z_drive_mm_to_increments(speed)
    if not low <= speed_increments <= high:
      raise ValueError(
        f"speed must be between {c.z_drive_increments_to_mm(low)} and "
        f"{c.z_drive_increments_to_mm(high)} mm/s, is {speed}"
      )

    low, high = c.z_drive_acceleration_ramp_range
    if not low <= acceleration_ramp <= high:
      raise ValueError(
        f"acceleration_ramp must be between {low} and {high}, is {acceleration_ramp}"
      )

    low, high = c.motor_current_limit_range
    if not low <= current_limit <= high:
      raise ValueError(f"current_limit must be between {low} and {high}, is {current_limit}")

    return await self._driver.send_command(
      module="I0",
      command="ZA",
      za=f"{increments:04}",
      zv=f"{speed_increments:04}",
      zr=f"{acceleration_ramp:01}",
      zw=f"{current_limit:01}",
    )

  async def wheel_move_to_z_position(
    self,
    position: ZPosition,
    speed: Optional[float] = None,
    acceleration_ramp: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the carrier-handling wheel to one of the two positions it knows.

    Args:
      position: which one: `below` or `above`.
      speed: how fast to travel, in mm/s. Defaults to what
        `configuration.z_drive_speed_default` works out to.
      acceleration_ramp: how hard to accelerate, in multiples of
        `configuration.acceleration_ramp_increments_per_second_squared`. Defaults to
        `configuration.z_drive_acceleration_ramp_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.motor_current_limit_default`.

    Raises:
      ValueError: If the position is not one it knows, or an argument is outside what the drive
        accepts.
    """
    c = self.configuration
    if position not in c.z_positions:
      raise ValueError(f"position must be one of {list(c.z_positions)}, is {position!r}")

    # Every parameter is sent: what the drive does is written here, not left to it.
    speed = c.z_drive_increments_to_mm(c.z_drive_speed_default) if speed is None else speed
    acceleration_ramp = (
      c.z_drive_acceleration_ramp_default if acceleration_ramp is None else acceleration_ramp
    )
    current_limit = c.motor_current_limit_default if current_limit is None else current_limit

    low, high = c.z_drive_speed_increment_range
    speed_increments = c.z_drive_mm_to_increments(speed)
    if not low <= speed_increments <= high:
      raise ValueError(
        f"speed must be between {c.z_drive_increments_to_mm(low)} and "
        f"{c.z_drive_increments_to_mm(high)} mm/s, is {speed}"
      )

    low, high = c.z_drive_acceleration_ramp_range
    if not low <= acceleration_ramp <= high:
      raise ValueError(
        f"acceleration_ramp must be between {low} and {high}, is {acceleration_ramp}"
      )

    low, high = c.motor_current_limit_range
    if not low <= current_limit <= high:
      raise ValueError(f"current_limit must be between {low} and {high}, is {current_limit}")

    return await self._driver.send_command(
      module="I0",
      command="ZP",
      zp=f"{c.z_positions[position]:01}",
      zv=f"{speed_increments:04}",
      zr=f"{acceleration_ramp:01}",
      zw=f"{current_limit:01}",
    )

  # -- Y drive (handling wheel moving carriers in and out of the deck) -----------------------

  async def wheel_request_y_position(self) -> float:
    """Request how far in or out the carrier drive is.

    Returns:
      The position in mm, from the drive's zero.
    """
    return self.configuration.y_drive_increments_to_mm(
      await self._request_drive_position("RY", digits=4)
    )

  async def wheel_move_y(
    self,
    y: float,
    speed: Optional[float] = None,
    acceleration_ramp: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the carrier drive to a Y position, pulling a carrier in or pushing it out.

    Args:
      y: how far to move it, in mm from the drive's zero.
      speed: how fast to travel, in mm/s. Defaults to what
        `configuration.y_drive_speed_default` works out to.
      acceleration_ramp: how hard to accelerate, in multiples of
        `configuration.acceleration_ramp_increments_per_second_squared`. Defaults to
        `configuration.y_drive_acceleration_ramp_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.motor_current_limit_default`.

    Raises:
      ValueError: If the position, or an argument, is outside what the drive accepts.
    """
    c = self.configuration
    self._check_reachable("y", y)
    increments = c.y_drive_mm_to_increments(y)

    # Every parameter is sent: what the drive does is written here, not left to it.
    speed = c.y_drive_increments_to_mm(c.y_drive_speed_default) if speed is None else speed
    acceleration_ramp = (
      c.y_drive_acceleration_ramp_default if acceleration_ramp is None else acceleration_ramp
    )
    current_limit = c.motor_current_limit_default if current_limit is None else current_limit

    low, high = c.y_drive_speed_increment_range
    speed_increments = c.y_drive_mm_to_increments(speed)
    if not low <= speed_increments <= high:
      raise ValueError(
        f"speed must be between {c.y_drive_increments_to_mm(low)} and "
        f"{c.y_drive_increments_to_mm(high)} mm/s, is {speed}"
      )

    low, high = c.y_drive_acceleration_ramp_range
    if not low <= acceleration_ramp <= high:
      raise ValueError(
        f"acceleration_ramp must be between {low} and {high}, is {acceleration_ramp}"
      )

    low, high = c.motor_current_limit_range
    if not low <= current_limit <= high:
      raise ValueError(f"current_limit must be between {low} and {high}, is {current_limit}")

    return await self._driver.send_command(
      module="I0",
      command="YA",
      ya=f"{increments:04}",
      yv=f"{speed_increments:04}",
      yr=f"{acceleration_ramp:01}",
      yw=f"{current_limit:01}",
    )

  async def wheel_move_to_y_position(
    self,
    position: YPosition,
    speed: Optional[float] = None,
    acceleration_ramp: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the carrier drive to one of the three positions it knows.

    Args:
      position: which one: `loading_tray`, `carrier_identification` or `deck`.
      speed: how fast to travel, in mm/s. Defaults to what
        `configuration.y_drive_speed_default` works out to.
      acceleration_ramp: how hard to accelerate, in multiples of
        `configuration.acceleration_ramp_increments_per_second_squared`. Defaults to
        `configuration.y_drive_acceleration_ramp_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.motor_current_limit_default`.

    Raises:
      ValueError: If the position is not one it knows, or an argument is outside what the drive
        accepts.
    """
    c = self.configuration
    if position not in c.y_positions:
      raise ValueError(f"position must be one of {list(c.y_positions)}, is {position!r}")

    # Every parameter is sent: what the drive does is written here, not left to it.
    speed = c.y_drive_increments_to_mm(c.y_drive_speed_default) if speed is None else speed
    acceleration_ramp = (
      c.y_drive_acceleration_ramp_default if acceleration_ramp is None else acceleration_ramp
    )
    current_limit = c.motor_current_limit_default if current_limit is None else current_limit

    low, high = c.y_drive_speed_increment_range
    speed_increments = c.y_drive_mm_to_increments(speed)
    if not low <= speed_increments <= high:
      raise ValueError(
        f"speed must be between {c.y_drive_increments_to_mm(low)} and "
        f"{c.y_drive_increments_to_mm(high)} mm/s, is {speed}"
      )

    low, high = c.y_drive_acceleration_ramp_range
    if not low <= acceleration_ramp <= high:
      raise ValueError(
        f"acceleration_ramp must be between {low} and {high}, is {acceleration_ramp}"
      )

    low, high = c.motor_current_limit_range
    if not low <= current_limit <= high:
      raise ValueError(f"current_limit must be between {low} and {high}, is {current_limit}")
    return await self._driver.send_command(
      module="I0",
      command="YP",
      yp=f"{c.y_positions[position]:01}",
      yv=f"{speed_increments:04}",
      yr=f"{acceleration_ramp:01}",
      yw=f"{current_limit:01}",
    )

  # -- scanner rotation drive ----------------------------------------------------------------------

  async def scanner_request_rotation(self) -> ScannerRotation:
    """Request which way the scanner faces.

    Returns:
      Which way it faces, or `undefined` when it sits at neither of the two stops.
    """
    resp = await self._driver.send_command(module="I0", command="RS", fmt="rs#")
    code = cast(int, resp["rs"])
    for name, value in self.configuration.scanner_rotations.items():
      if value == code:
        return name
    return "undefined"

  async def scanner_move_to_position(self, position: ScannerRotation, stop_torque: bool = False):
    """Rotate the scanner to face one way or the other.

    Args:
      position: which way to face. Only the two stops can be moved to, so `undefined` is refused.
      stop_torque: whether to hold the drive there once it arrives. The drive's own default is
        not to.

    Raises:
      ValueError: If the position is not one that can be moved to.
    """
    rotations = self.configuration.scanner_rotations
    if position == "undefined" or position not in rotations:
      raise ValueError(
        f"position must be one of {[n for n in rotations if n != 'undefined']}, is {position!r}"
      )

    return await self._driver.send_command(
      module="I0",
      command="SP",
      sp=f"{rotations[position]:01}",
      sh=f"{int(stop_torque):01}",
    )

  # -- carrier presence sensing, using magnetic proximity sensors -------------------------------------------

  @staticmethod
  def _presence_mask(resp: str, marker: str) -> str:
    """The carrier-presence mask in a reply: what is written after `marker`."""
    if marker not in resp:
      raise ValueError(f"no `{marker}` carrier presence mask in the reply: {resp!r}")
    return resp.split(marker, 1)[1]

  async def sense_carrier_presence_on_deck(self) -> List[int]:
    """Read the rear deck sensors and return the positions where carriers are detected.

    The autoload does not move.

    Returns:
      The tracks that hold a carrier, counted from 1.

    Raises:
      ValueError: If the machine answered without a presence mask.
    """
    resp = cast(str, await self._driver.send_command(module="C0", command="RC"))
    return _tracks_from_presence_mask(self._presence_mask(resp, "ce"))

  async def sense_carrier_presence_on_single_loading_tray_track(
    self, track: int, park_after: bool = True
  ) -> bool:
    """Check whether a specific loading-tray track contains a carrier.

    The sled moves to that track and reads its front-facing sensor.
    `sense_carrier_presence_on_loading_tray` scans the whole tray instead.

    Args:
      track: which track to look at, counted from 1.
      park_after: whether to park the sled after reading the sensor.

    Returns:
      True if a carrier is there.

    Raises:
      ValueError: If the track is not one this machine has.
      RuntimeError: If setup has not run.
    """
    tracks = self.track_range
    if track not in tracks:
      raise ValueError(f"track must be between {tracks[0]} and {tracks[-1]}, is {track}")
    resp = await self._driver.send_command(module="C0", command="CT", fmt="ct#", cp=f"{track:02}")

    if park_after:
      await self.park()

    return cast(int, resp["ct"]) == 1

  async def sense_carrier_presence_on_loading_tray(self) -> List[int]:
    """Move the autoload sled across the loading tray and read its front-facing sensors.

    This determines which tray positions contain carriers.

    Returns:
      The tracks that hold a carrier, counted from 1.

    Raises:
      ValueError: If the machine answered without a presence mask.
    """
    resp = cast(str, await self._driver.send_command(module="C0", command="CS"))
    return _tracks_from_presence_mask(self._presence_mask(resp, "cd"))

  # -- barcode scanner -----------------------------------------------------------------------------

  def _require_barcode_scanner(self) -> Dict[Barcode1DSymbology, int]:
    """The symbologies the fitted scanner reads, and the mask each holds.

    Raises:
      RuntimeError: If discovery has not run, or this autoload's type names no scanner.
    """
    if self.configuration.barcode_symbologies is None:
      raise RuntimeError(
        f"no barcode scanner is recorded for this autoload ({self.configuration.autoload_type}); "
        "have you called `star.setup()`?"
      )
    return self.configuration.barcode_symbologies

  async def request_latest_barcode_read(self) -> Optional[str]:
    """Request the barcode the scanner last read.

    Returns:
      What it read, or None when it read nothing.
    """
    self._require_barcode_scanner()
    resp = cast(str, await self._driver.send_command(module="I0", command="RB"))
    barcode = resp.split("rb", 1)[-1].strip().strip("'")
    return barcode or None

  async def set_barcode_scanner_enabled(
    self,
    enabled: bool,
    symbologies: Optional[List[Barcode1DSymbology]] = None,
    symbologies_2d: Optional[List[Barcode2DSymbology]] = None,
    scan_direction: ScanDirection = "horizontal",
  ):
    """Switch the barcode scanner on or off. Switching it on is what reads a barcode.

    Args:
      enabled: whether to switch it on.
      symbologies: which symbologies to read. Defaults to `ANY 1D`, every one it reads.
      symbologies_2d: which 2D and stacked codes to read, on a reader that reads them. Defaults to
        `ANY 2D` there, and is refused on a scanner that reads only 1D.
      scan_direction: which way a 2D reader looks. Sent only by a reader that takes it.

    Raises:
      ValueError: If a symbology is not one it reads, or 2D codes are asked of a 1D scanner.
      RuntimeError: If this autoload's type names no scanner.
    """
    c = self.configuration
    known = self._require_barcode_scanner()
    symbologies = ["ANY 1D"] if symbologies is None else symbologies
    unknown = [name for name in symbologies if name not in known]
    if unknown:
      raise ValueError(f"not symbologies this scanner reads: {unknown}; it reads {list(known)}")
    mask = 0
    for name in symbologies:
      mask |= known[name]

    # A 1D scanner's command has neither parameter below.
    if c.barcode_2d_symbologies is None:
      if symbologies_2d is not None:
        raise ValueError(f"this scanner reads no 2D codes: {symbologies_2d}")
      return await self._driver.send_command(
        module="I0", command="AR", ar=f"{int(enabled):01}", bt=f"{mask:02X}"
      )

    known_2d = c.barcode_2d_symbologies
    symbologies_2d = ["ANY 2D"] if symbologies_2d is None else symbologies_2d
    unknown_2d = [name for name in symbologies_2d if name not in known_2d]
    if unknown_2d:
      raise ValueError(f"not 2D codes this reader reads: {unknown_2d}; it reads {list(known_2d)}")
    mask_2d = 0
    for name_2d in symbologies_2d:
      mask_2d |= known_2d[name_2d]

    if scan_direction not in c.scan_directions:
      raise ValueError(
        f"scan_direction must be one of {list(c.scan_directions)}, is {scan_direction!r}"
      )

    return await self._driver.send_command(
      module="I0",
      command="AR",
      ar=f"{int(enabled):01}",
      sp=f"{c.scan_directions[scan_direction]:01}",
      bt=f"{mask:02X}",
      mq=f"{mask_2d:02X}",
    )

  async def reset_barcode_scanner(self):
    """Reset the barcode scanner."""
    self._require_barcode_scanner()
    return await self._driver.send_command(module="I0", command="AF")

  # -- carrier identification ----------------------------------------------------------------------

  async def set_barcode_symbologies(self, symbologies: List[Barcode1DSymbology]):
    """Set the barcode symbologies for autoload barcode reading.

    Args:
      symbologies: which symbologies to read.

    Raises:
      ValueError: If a type is not one it reads.
    """
    known = self._require_barcode_scanner()
    unknown = [name for name in symbologies if name not in known]
    if unknown:
      raise ValueError(f"not symbologies this scanner reads: {unknown}; it reads {list(known)}")
    mask = 0
    for name in symbologies:
      mask |= known[name]
    return await self._driver.send_command(module="C0", command="CB", bt=f"{mask:02X}")

  async def load_carrier_from_tray_and_scan_carrier_barcode(
    self,
    track: int,
    barcode_position: float = 4.3,
    barcode_reading_window_width: float = 38.0,
    container_distance: float = 96.0,
    reading_speed: float = 128.1,
  ) -> Optional[str]:
    """Load a carrier from the loading tray and scan its barcode.

    `unload_carrier_after_carrier_barcode_scanning` puts it back on the tray.

    Args:
      track: the track the carrier ends at, counted from 1.
      barcode_position: where along the carrier its barcode sits, in mm.
      barcode_reading_window_width: how wide a window to read it in, in mm.
      container_distance: the spacing of the pattern to read, in mm.
      reading_speed: how fast to travel while reading, in mm/s.

    Returns:
      The barcode, or None when nothing was read.

    Raises:
      ValueError: If the track is not one this machine has, or an argument is outside what the
        command accepts.
      RuntimeError: If setup has not run.
    """
    tracks = self.track_range
    if track not in tracks:
      raise ValueError(f"track must be between {tracks[0]} and {tracks[-1]}, is {track}")
    if not 0 <= barcode_position <= 470:
      raise ValueError(f"barcode_position must be between 0 and 470 mm, is {barcode_position}")
    if not 0.1 <= barcode_reading_window_width <= 99.9:
      raise ValueError(
        "barcode_reading_window_width must be between 0.1 and 99.9 mm, is "
        f"{barcode_reading_window_width}"
      )
    if not 1.5 <= reading_speed <= 160.0:
      raise ValueError(f"reading_speed must be between 1.5 and 160.0 mm/s, is {reading_speed}")

    try:
      resp = cast(
        str,
        await self._send_command_and_update_sled_x(
          module="C0",
          command="CI",
          cp=f"{track:02}",
          bi=f"{round(barcode_position * 10):04}",
          bw=f"{round(barcode_reading_window_width * 10):03}",
          co=f"{round(container_distance * 10):04}",
          cv=f"{round(reading_speed * 10):04}",
        ),
      )
    except BaseException:
      # The wheel is left wherever the failure stopped it, and nothing may travel with it down.
      await self.wheel_move_to_safe_z()
      raise

    if "bb/" not in resp:
      return None
    # What follows the marker is the barcode's length written in two digits, then the barcode.
    read = resp.split("bb/", 1)[1].strip().strip("'")
    return read[2:] or None

  async def unload_carrier_after_carrier_barcode_scanning(self):
    """Unload the carrier currently engaged with the autoload sled, back to the loading tray.

    Sent after its barcode has been scanned.
    """
    try:
      return await self._send_command_and_update_sled_x(module="C0", command="CA")
    except BaseException:
      await self.wheel_move_to_safe_z()
      raise

  async def take_carrier_out_to_autoload_belt(self, track: int):
    """Take a carrier out to the identification position for barcode reading.

    The carrier is already on the deck.

    Args:
      track: the track the carrier sits at, counted from 1.

    Raises:
      ValueError: If the track is not one this machine has, or its carrier is on the loading tray
        rather than the deck.
      RuntimeError: If setup has not run.
    """
    tracks = self.track_range
    if track not in tracks:
      raise ValueError(f"track must be between {tracks[0]} and {tracks[-1]}, is {track}")
    if await self.sense_carrier_presence_on_single_loading_tray_track(track):
      raise ValueError(f"the carrier at track {track} is on the loading tray, not the deck")

    try:
      return await self._send_command_and_update_sled_x(module="C0", command="CN", cp=f"{track:02}")
    except BaseException:
      # The wheel is left wherever the failure stopped it, and nothing may travel with it down.
      await self.wheel_move_to_safe_z()
      raise

  async def load_carrier_from_autoload_belt(
    self,
    barcode_reading: bool = False,
    barcode_reading_direction: BarcodeReadingDirection = "horizontal",
    reading_position_of_first_barcode: float = 63.0,
    containers_per_carrier: int = 5,
    distance_between_containers: float = 96.0,
    width_of_reading_window: float = 38.0,
    reading_speed: float = 128.1,
    park_after: bool = True,
  ) -> Dict[int, Optional[str]]:
    """Finish loading the carrier currently engaged with the autoload sled.

    It is the one at the identification position. Which barcode types are read is whatever
    `set_barcode_symbologies` last set.

    Args:
      barcode_reading: whether to read the containers at all. When False the scanner stays where it
        is and nothing is read.
      barcode_reading_direction: which way the scanner faces while reading: `vertical` or
        `horizontal`.
      reading_position_of_first_barcode: where along the carrier the first container's barcode
        sits, in mm.
      containers_per_carrier: how many containers to read.
      distance_between_containers: how far apart they sit, in mm.
      width_of_reading_window: how wide a window to read each in, in mm.
      reading_speed: how fast to travel while reading, in mm/s.
      park_after: whether to park the autoload once the carrier is in.

    Returns:
      Each container's barcode by position, counted from 0, and None where nothing was read. Empty
      when `barcode_reading` is False.

    Raises:
      ValueError: If an argument is outside what the command accepts, or fewer barcodes come back
        than were asked for.
      RuntimeError: If setup has not run and the autoload has to be parked.
    """
    directions = self.configuration.barcode_reading_directions
    if barcode_reading_direction not in directions:
      raise ValueError(
        f"barcode_reading_direction must be one of {list(directions)}, is "
        f"{barcode_reading_direction!r}"
      )
    if not 0 <= reading_position_of_first_barcode <= 470:
      raise ValueError(
        "reading_position_of_first_barcode must be between 0 and 470 mm, is "
        f"{reading_position_of_first_barcode}"
      )
    if not 0 <= containers_per_carrier <= 32:
      raise ValueError(
        f"containers_per_carrier must be between 0 and 32, is {containers_per_carrier}"
      )
    if not 0 <= distance_between_containers <= 470:
      raise ValueError(
        f"distance_between_containers must be between 0 and 470 mm, is {distance_between_containers}"
      )
    if not 0.1 <= width_of_reading_window <= 99.9:
      raise ValueError(
        f"width_of_reading_window must be between 0.1 and 99.9 mm, is {width_of_reading_window}"
      )
    if not 1.5 <= reading_speed <= 160.0:
      raise ValueError(f"reading_speed must be between 1.5 and 160.0 mm/s, is {reading_speed}")

    # Reading nothing is asked for by facing the scanner away and asking for no containers, so the
    # carrier travels in without the scanner moving.
    direction = "vertical" if not barcode_reading else barcode_reading_direction
    containers = containers_per_carrier if barcode_reading else 0

    try:
      resp = cast(
        str,
        await self._send_command_and_update_sled_x(
          module="C0",
          command="CL",
          bd=f"{directions[direction]:01}",
          bp=f"{round(reading_position_of_first_barcode * 10):04}",
          cn=f"{containers:02}",
          co=f"{round(distance_between_containers * 10):04}",
          cf=f"{round(width_of_reading_window * 10):03}",
          cv=f"{round(reading_speed * 10):04}",
        ),
      )
    except BaseException:
      await self.wheel_move_to_safe_z()
      raise

    if park_after:
      await self.park()

    if not barcode_reading:
      return {}

    read = resp.split("bb/")[-1].split("/")
    if len(read) < containers_per_carrier:
      raise ValueError(
        f"asked for {containers_per_carrier} barcodes, {len(read)} came back: {resp!r}"
      )
    return {
      position: None if read[position] == "00" else read[position]
      for position in range(containers_per_carrier)
    }

  async def unload_carrier(self, track: int, park_after: bool = True):
    """Use the autoload to unload the carrier at a track.

    Args:
      track: the track the carrier sits at, counted from 1.
      park_after: whether to park the autoload once the carrier is out.

    Raises:
      ValueError: If the track is not one this machine has.
      RuntimeError: If setup has not run.
    """
    tracks = self.track_range
    if track not in tracks:
      raise ValueError(f"track must be between {tracks[0]} and {tracks[-1]}, is {track}")

    resp = await self._send_command_and_update_sled_x(module="C0", command="CR", cp=f"{track:02}")
    if park_after:
      await self.park()
    return resp

  async def unload_carrier_finally(self, track: int, park_after: bool = True):
    """Unload the carrier at a track, from where it cannot be loaded again.

    Args:
      track: the track the carrier sits at, counted from 1.
      park_after: whether to park the autoload once the carrier is out.

    Raises:
      ValueError: If the track is not one this machine has.
      RuntimeError: If setup has not run.
    """
    tracks = self.track_range
    if track not in tracks:
      raise ValueError(f"track must be between {tracks[0]} and {tracks[-1]}, is {track}")

    resp = await self._send_command_and_update_sled_x(module="C0", command="CW", cp=f"{track:02}")
    if park_after:
      await self.park()
    return resp

  # TODO: port legacy's `load_carrier`, once the resource model is wired in. It is the sequence
  # below, in v1 terms, and every command it needs is already here. What is missing is the first
  # line: the deck works the track out of a `Carrier`'s position on it
  # (`compute_right_track_of_carrier`), and the driver has no resource model to ask.
  #
  # async def load_carrier(
  #   self,
  #   carrier,
  #   carrier_barcode_reading: bool = True,
  #   barcode_reading: bool = False,
  #   barcode_reading_direction: BarcodeReadingDirection = "horizontal",
  #   containers_per_carrier: int = 5,
  #   reading_position_of_first_barcode: float = 63.0,
  #   distance_between_containers: float = 96.0,
  #   width_of_reading_window: float = 38.0,
  #   reading_speed: float = 128.1,
  #   park_after: bool = True,
  # ) -> dict:
  #   """Use the autoload to load a carrier."""
  #   track = ...  # the track the carrier ends at, from where it sits on the deck
  #   if not await self.sense_carrier_presence_on_single_loading_tray_track(track):
  #     raise ValueError(f"no carrier at track {track}; is it on the right loading tray position?")
  #
  #   carrier_barcode = None
  #   if carrier_barcode_reading:
  #     carrier_barcode = await self.load_carrier_from_tray_and_scan_carrier_barcode(track)
  #
  #   container_barcodes = await self.load_carrier_from_autoload_belt(
  #     barcode_reading=barcode_reading,
  #     barcode_reading_direction=barcode_reading_direction,
  #     reading_position_of_first_barcode=reading_position_of_first_barcode,
  #     containers_per_carrier=containers_per_carrier,
  #     distance_between_containers=distance_between_containers,
  #     width_of_reading_window=width_of_reading_window,
  #     reading_speed=reading_speed,
  #     park_after=False,
  #   )
  #
  #   if park_after:
  #     await self.park()
  #
  #   return {"carrier_barcode": carrier_barcode, "container_barcodes": container_barcodes}

  # -- loading indicators --------------------------------------------------------------------------

  async def set_loading_indicators(self, lit: List[bool], blinking: List[bool]):
    """Set the loading indicators (LEDs), one per track.

    Args:
      lit: whether each track's light is on, counted from track 1.
      blinking: whether each track's light blinks rather than stays steady.

    Raises:
      ValueError: If either pattern does not have one entry per track.
      RuntimeError: If setup has not run, so the deck size is not known.
    """
    tracks = len(self.track_range)
    for name, pattern in (("lit", lit), ("blinking", blinking)):
      if len(pattern) != tracks:
        raise ValueError(f"{name} must have {tracks} entries, one per track, has {len(pattern)}")

    def as_hex(pattern: List[bool]) -> str:
      bits = "".join("1" if on else "0" for on in pattern)
      return f"{int(bits, base=2):014X}"

    return await self._driver.send_command(
      module="C0", command="CP", cl=as_hex(lit), cb=as_hex(blinking)
    )
