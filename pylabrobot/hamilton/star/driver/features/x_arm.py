"""The X-arm: the carriage that runs along a rail and carries whatever is mounted on it."""

import dataclasses
import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional, Tuple, cast

from pylabrobot.hamilton.protocol.text.framing import parse_firmware_version_date
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.features.head96 import Head96
  from pylabrobot.hamilton.star.driver.features.head384 import Head384
  from pylabrobot.hamilton.star.driver.features.iswap import iSWAP
  from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)

# The command set splits at firmware 5.0: the ranges and encodings below were recorded from an arm
# below it, which is the generation this driver has been driven against. A 5.0 or higher arm takes
# a wider current limiter, written in two digits rather than one, and has moves this one does not.
RECORDED_FIRMWARE_BELOW_MAJOR = 5


@dataclass
class XArmConfiguration:
  """Configuration and geometry for an X drive (left or right).

  The installed-module bits combine byte 1 (xl/xr) and byte 2 (xn/xo). The arm
  geometry comes from three queries, one per field: `width` from the extended
  configuration (QM), as `xu` for the left drive and `xv` for the right, in tenths
  of a millimetre; `x_range` from the X-drive range (RU); and `workspace_x_range`
  and `wrap_size` from the working envelope (UA). All are None on a drive built
  from the module bits alone (e.g. a simulated configuration) and populated when
  `request_device_configuration` builds the drive. `model` and `reference_point`
  follow from `width`.

  `width` is read per drive, and the two drives on one device do not have to agree:
  it is what a drive says about itself, not a figure a kind of arm has.

  The two drives' module bits never overlap: a module occupies one fixed CAN node - the 96-head is
  `H0`, the iSWAP `R0` - so a device has one of it, and the bits say which arm carries it rather
  than how many there are. Where a module genuinely can be several, the node list indexes it
  instead (`Ln` for XL channels, `On` for robotic ones). The pipetting channels are one chain,
  `P1` to `PG`, addressed together as `PX`, which is why the device reports a single channel
  count and not one per arm.
  """

  pip_installed: bool = False
  iswap_installed: bool = False
  head96_installed: bool = False
  nano_pipettor_installed: bool = False
  head384_installed: bool = False
  xl_channels_installed: bool = False
  tube_gripper_installed: bool = False
  imaging_channel_installed: bool = False
  # byte 2 from here: xn on the left drive, xo on the right.
  robotic_channel_installed: bool = False
  gel_card_gripper_installed: bool = False
  puncher_handler_installed: bool = False

  width: Optional[float] = None
  large: Optional[bool] = None
  x_range: Optional[Tuple[float, float]] = None
  workspace_x_range: Optional[Tuple[float, float]] = None
  wrap_size: Optional[float] = None  # zero when no arm is installed
  firmware_version: Optional[str] = None

  # -- device facts of the drive, the same for every arm of this generation --
  x_mm_per_increment: float = 0.1
  x_range_increments: Tuple[int, int] = (0, 30_000)  # what the move accepts; x_range is narrower
  acceleration_level_range: Tuple[int, int] = (1, 5)  # index into five curves, not a rate
  acceleration_level_default: int = 4
  current_limit_range: Tuple[int, int] = (0, 7)
  current_limit_default: int = 7

  def with_device_facts_of(self, other: "XArmConfiguration") -> "XArmConfiguration":
    """This configuration, with the device facts of another in place of its own.

    What keeps a corrected device fact across a re-read, since discovery rebuilds an arm's
    configuration from the device's reply.

    Args:
      other: the configuration to take the device facts from.

    Returns:
      XArmConfiguration: A new one. Neither of these is changed.
    """
    return dataclasses.replace(
      self,
      x_mm_per_increment=other.x_mm_per_increment,
      x_range_increments=other.x_range_increments,
      acceleration_level_range=other.acceleration_level_range,
      acceleration_level_default=other.acceleration_level_default,
      current_limit_range=other.current_limit_range,
      current_limit_default=other.current_limit_default,
    )

  # -- conversions: the wire counts in steps, the driver speaks mm ---------------------------

  def x_increments_to_mm(self, increments: int) -> float:
    """Where along its rail the arm is, in mm, from the steps the drive counts in."""
    return round(increments * self.x_mm_per_increment, 2)

  def x_mm_to_increments(self, mm: float) -> int:
    """An arm position in steps, from mm."""
    return round(mm / self.x_mm_per_increment)

  # How wide the large arm is, in mm, end to end. Measured on the part, because the drive does not
  # say: what it reports is a reach measured from the point it tracks, not a size, and the two
  # differ by tens of millimetres. A tape across one reads 400; the manufacturer's model of the
  # same part gives 400.49.
  #
  # One arm, measured once. A second one is what would show whether this belongs to the arm or to
  # the device it was measured on.
  large_arm_size_x: float = 400.49

  # Where the drive's tracked point sits on the large arm, in mm from its left edge. Measured on
  # the part two ways that agree to the hundredth: the midpoint of the two rails the channels hang
  # from - odd channels at 141.35, even at 304.64 - and the centre of the opening between them.
  #
  # A property of the arm, so it is stated rather than derived from the reported width. The two do
  # agree: half the reported width is the reach from this point to the arm's right edge, which
  # comes to 2 x (400.49 - 223.00) = 354.98, against the 354.0 a device answers.
  large_arm_reference_from_left: float = 223.00

  @property
  def is_large(self) -> Optional[bool]:
    """Whether this is the large arm, spanning both rails.

    The device says so outright, in the drive-size bit read with the rest of its configuration. A
    configuration that predates that bit - a declaration written before it was carried - has only
    the reported width to go on, and a large arm reports far more than a small one, so the width
    stands in. It is a fallback and not the answer: the width is settable, and two devices with
    the same arm report different ones.
    """
    if self.large is not None:
      return self.large
    return None if self.width is None else self.width > 300

  @property
  def size_x(self) -> float:
    """How wide the arm is, in mm, end to end.

    Not what it reports. The reported width is a reach measured from the point the drive tracks,
    so it says nothing about how much room the part takes.
    """
    if self.is_large is None:
      raise RuntimeError("arm geometry not resolved")
    if not self.is_large:
      raise RuntimeError("no small arm has been measured, so how wide one is is not known")
    return self.large_arm_size_x

  @property
  def model(self) -> str:
    """Arm variant: a large drive spans both rails, a small one a single rail.

    Taken from the drive-size bit the device sets, not from `width`. The width is a value written
    into the instrument rather than measured off it - two devices with the same arm answer 354.0
    and 370.0 - so a variant derived from it follows whatever someone configured.
    """
    if self.is_large is None:
      raise RuntimeError("arm geometry not resolved")
    return (
      "hamilton_legacy_star_dual_rail_arm"
      if self.is_large
      else "hamilton_legacy_star_single_right_rail_arm"
    )

  @property
  def reference_point(self) -> Literal["center", "right"]:
    """Where along the reported width the tracked X refers to.

    The middle of it for a large arm, its right end for a small one. Measured along the width the
    drive reports, not along the arm: what stands beyond that width is not part of what it is
    counting from.

    Returns:
      Which point along the arm's reported width its X refers to.
    """
    if self.is_large is None:
      raise RuntimeError("arm geometry not resolved")
    return "center" if self.is_large else "right"

  @property
  def reference_point_from_left(self) -> float:
    """Where the tracked X sits, in mm from the arm's left edge.

    Measured on the arm rather than derived from what it reports. The reported width is a reach
    from this point to the arm's right edge, doubled, so it does answer the question - but it is a
    value someone wrote into the instrument, and a device left at the default answers 370.0 for an
    arm whose reach is 354.98. The part does not move when that number does.
    """
    if self.is_large is None:
      raise RuntimeError("arm geometry not resolved")
    if not self.is_large:
      raise RuntimeError("no small arm has been measured, so where it is tracked is not known")
    return self.large_arm_reference_from_left


class XArm:
  """One X-arm, on the left or the right rail.

  Reached as `driver.left_x_arm` / `driver.right_x_arm`. Its `configuration` is the arm's own
  slice of what the driver read off the device at setup: what is mounted on the arm, how wide it
  is, how far it travels, and how far along X what it carries reaches.
  """

  def __init__(
    self,
    driver: "STARDriver",
    side: Literal["left", "right"] = "left",
    configuration: Optional[XArmConfiguration] = None,
  ):
    """
    Args:
      driver: the driver to send commands through.
      side: which rail this arm runs on. A STAR always has a left arm; a right arm is an option.
      configuration: this arm's configuration, written where the device's holds it. Defaults to
        whatever the device answered for this rail.

    Raises:
      RuntimeError: If a configuration is given before the device has been read.
    """
    self._driver = driver
    # The arm on the deck, when the driver was given one. Setup puts it there; moves keep it in
    # step. Without a deck it stays None and nothing is modelled.
    self.resource: Optional[Resource] = None
    # What this arm carries. The firmware requires the feature bits of the two drives to be
    # disjoint, so a feature is on one arm or the other and never on both. Setup builds each
    # from this arm's own bits.
    self.pipettes: Optional["Pipettes"] = None
    self.head96: Optional["Head96"] = None
    self.head384: Optional["Head384"] = None
    self.iswap: Optional["iSWAP"] = None
    self.side = side
    if configuration is not None:
      self.configuration = configuration

  @property
  def parameter_prefix(self) -> str:
    """The letter every parameter to this arm's drive starts with: `l` on the left, `s` on the

    Returns:
      The letter, `l` or `s`.
    right. The X-drive board carries a drive per arm, each with its own commands."""
    return "l" if self.side == "left" else "s"

  # -- session / discovery ---------------------------------------------------

  @property
  def configuration(self) -> XArmConfiguration:
    """This arm's configuration and geometry.

    Returns:
      This arm's configuration.

    Raises:
      RuntimeError: If setup has not run, so no configuration has been read yet.
      ValueError: If no arm is installed on this rail.
    """
    configuration = self._driver.configuration
    if configuration is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    arm = configuration.left_arm if self.side == "left" else configuration.right_arm
    if arm is None:
      raise ValueError(f"no {self.side} X-arm is installed")
    return arm

  @configuration.setter
  def configuration(self, configuration: XArmConfiguration) -> None:
    """Put this arm's configuration where the device's holds it.

    The device reports both arms in one reply, so an arm's configuration is a field of the
    device's and this writes into that field.

    Args:
      configuration: what this arm is to be configured with.

    Raises:
      RuntimeError: If the device has not been read yet, so there is nowhere to put it.
    """
    device = self._driver.configuration
    if device is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    if self.side == "left":
      device.left_arm = configuration
    else:
      device.right_arm = configuration

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    """Request the X-drive board's firmware version and build date.

    Both arms run off the same board, so this reports the same for either side.

    Returns:
      The version string and its build date, e.g. `("1.4S 2012-04-25", date(2012, 4, 25))`.
    """
    resp = await self._driver.send_command(module="X0", command="RF")
    return resp.split("rf")[-1], parse_firmware_version_date(resp)

  async def request_initialization_status(self) -> bool:
    """Request whether this arm's drive reports itself initialized.

    Returns:
      Whether it is initialized.
    """
    resp = await self._driver.send_command(
      module="X0", command="QW", fmt="qw#", mn="1" if self.side == "left" else "2"
    )
    return cast(int, resp["qw"]) == 1

  async def discover(self):
    """Read what this arm is. Read-only: nothing moves."""
    version, _ = await self.request_firmware_version()
    self.configuration.firmware_version = version
    major = version.split(".", 1)[0]
    if major.isdigit() and int(major) >= RECORDED_FIRMWARE_BELOW_MAJOR:
      logger.warning(
        "this X-arm reports firmware %s; the ranges and encodings here were recorded from an arm "
        "below %d.0, so its current limiter and the moves it accepts may differ. Set them on "
        "XArmConfiguration to correct it.",
        version,
        RECORDED_FIRMWARE_BELOW_MAJOR,
      )

  def narrow_travel_for_left_side_panel(self) -> None:
    """Take the left side panel out of this arm's travel, if one is fitted.

    The drive reports the travel of an unobstructed device, and a panel is bolted on and off in
    seconds, so it is declared rather than discovered. What strikes it first is a head, which
    reaches far in front of the carriage, so an arm carrying one stops while its channel A1
    is still clear. An arm carrying both stops for whichever needs the most room. Called once setup
    has read where the heads sit, since that is what decides how much travel the panel costs.
    """
    c = self.configuration
    if not self._driver.left_side_panel_installed or c.x_range is None:
      return
    x_min, x_max = c.x_range
    clear = x_min
    for head in (self.head96, self.head384):
      if head is None or head.configuration.x_offset is None:
        continue
      clear = max(
        clear, head.configuration.min_x_clear_of_left_side_panel + head.configuration.x_offset
      )
    if clear > x_min:
      logger.debug(
        "left side panel fitted: %s X-arm travel narrowed from %s to %s mm", self.side, x_min, clear
      )
      c.x_range = (clear, x_max)

  # -- initialization --------------------------------------------------------

  async def initialize(self, current_limit: Optional[int] = None):
    """Initialize this arm's drive. This moves it.

    Args:
      current_limit: the motor current limit. Defaults to
        `configuration.current_limit_default`.
    Raises:
      ValueError: If the current limit is outside what the drive accepts.
    """
    c = self.configuration
    # The parameter is sent, so what the drive does is written here rather than left to the drive's
    # own default, which nothing would record.
    current_limit = c.current_limit_default if current_limit is None else current_limit
    low, high = c.current_limit_range
    if not low <= current_limit <= high:
      raise ValueError(f"current_limit must be between {low} and {high}, is {current_limit}")

    parameters: Dict[str, Any] = {f"{self.parameter_prefix}w": f"{current_limit:01}"}
    return await self._driver.send_command(
      module="X0", command="XI" if self.side == "left" else "SI", **parameters
    )

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  def _check_reachable(self, x: float) -> None:
    """Raise if `x` is outside this arm's travel range.

    `x` is the arm's position at its reference point - its center on a dual-rail arm, its right
    edge on a single-rail arm - so the bound is that point's travel, not the wider span the
    arm reaches around it.

    Args:
      x: target X position in mm.

    Raises:
      RuntimeError: If the arm's geometry was not resolved.
      ValueError: If `x` is outside the travel range.
    """
    x_range = self.configuration.x_range
    if x_range is None:
      raise RuntimeError(f"{self.side} X-arm geometry not resolved")
    x_min, x_max = x_range
    if not x_min <= x <= x_max:
      raise ValueError(f"{self.side} X-arm x={x}mm is outside its travel range [{x_min}, {x_max}].")

  @property
  def reference_anchor(self) -> Literal["l", "c", "r"]:
    """Where along its width this arm's x refers to, as a resource anchor: the centre of a

    Returns:
      The anchor, as PyLabRobot names one.
    dual-rail arm, the right edge of a single-rail one."""
    return "c" if self.configuration.reference_point == "center" else "r"

  def update_location_by_reference_point(self, x: float) -> None:
    """Record where this arm is on the resource that models it.

    The device positions the arm by its reference point - the middle of the width it reports for a
    large arm, the right end of it for a small one - while a resource is located by its left front
    bottom corner, so the two differ by how far along the arm that point sits. Measured along the
    reported width rather than taken as an anchor on the box: the box is the whole arm, which
    reaches further right than the drive counts. Does nothing when the driver was given no deck,
    and so has nothing to model.

    Args:
      x: where the reference point is now, in mm.
    """
    if self.resource is None or self.resource.location is None:
      return
    self.resource.location = Coordinate(
      x - self.configuration.reference_point_from_left,
      self.resource.location.y,
      self.resource.location.z,
    )

  # -- x motion --------------------------------------------------------------

  async def request_position(self) -> float:
    """Request where along its rail the arm is.

    Each drive has its own read, answering with the position twice - in tenths of a millimetre and
    in motor counts. The first is what this returns.

    The device is the authority on where the arm is, so what it answers is recorded on the
    resource that models it.

    Returns:
      The position in mm.

    Raises:
      ValueError: If the device answered without a position.
    """
    read_command = "RX" if self.side == "left" else "RS"
    resp = cast(str, await self._driver.send_command(module="X0", command=read_command))
    read = resp.split(read_command.lower(), 1)[-1].strip().strip("'\u201a\u201b").split()
    if not read:
      raise ValueError(f"no position in the reply: {resp!r}")
    x = self.configuration.x_increments_to_mm(int(read[0]))
    self.update_location_by_reference_point(x)
    return x

  # TODO: on a device with two arms, check the other arm's position before moving. They share one
  # rail, so a move can drive one arm into the other, and neither the drive nor `_check_reachable`
  # knows about it - the travel range is the arm's own, measured as though it were alone. What is
  # needed is the other arm's position, the width of both (`configuration.width`) and how far each
  # reaches around its reference point (`wrap_size`), so a move that would close the gap is refused
  # before it starts. Add a `make_space: bool` alongside it: when the far arm is in the way, move it
  # clear first rather than refusing - which is what an operator would do by hand, and what a
  # protocol wants when the two arms work the same deck. Untestable here: this device has one arm.
  async def _record_where_it_stopped(self) -> None:
    """Read where this arm came to rest, and record it.

    For a move's `finally`. A move that failed part way left the arm somewhere no target describes.
    Its own failure is logged and swallowed: it must not replace the move's exception, which is the
    one that says what went wrong.
    """
    try:
      await self.request_position()
    except Exception:
      logger.warning("could not read where the %s X-arm stopped; its model is stale", self.side)

  async def move_x(
    self,
    x: float,
    acceleration_level: int = 3,
    current_limit: int = 7,
    settle_reads: int = 20,
  ):
    """Move the arm to an absolute X position.

    Collision risk: this moves the arm and everything mounted on it, with no regard for what is
    in the way.

    Args:
      x: target X position in mm, at the arm's reference point. Must lie within the arm's travel
        range (`configuration.x_range`).
      acceleration_level: which acceleration curve to use. The drive's own default is
        `configuration.acceleration_level_default`; this is the gentler one legacy sends. The
        hardest curve leaves the arm oscillating about its target rather than approaching it, so it
        takes longer to come to rest and further still with a 96-head parked forward - it arrives
        either way, but the settling read below has more to wait for.
      current_limit: the motor current limit.
      settle_reads: how many reads to spend waiting for the arm to come to rest. Each is a command
        round trip, about 10 ms, against a settle of 27 to 90 ms where this was measured.
    Raises:
      ValueError: If `x` is outside the arm's travel range, or an argument is out of range.
      RuntimeError: If the arm's geometry was not resolved.
    """
    c = self.configuration
    self._check_reachable(x)
    low, high = c.acceleration_level_range
    if not low <= acceleration_level <= high:
      raise ValueError(
        f"acceleration_level must be between {low} and {high}, is {acceleration_level}"
      )
    low, high = c.current_limit_range
    if not low <= current_limit <= high:
      raise ValueError(f"current_limit must be between {low} and {high}, is {current_limit}")

    try:
      p = self.parameter_prefix
      parameters: Dict[str, Any] = {
        f"{p}a": f"{c.x_mm_to_increments(x):05}",
        f"{p}r": f"{acceleration_level:01}",
        f"{p}w": f"{current_limit:01}",
      }
      resp = await self._driver.send_command(
        module="X0", command="XP" if self.side == "left" else "SP", **parameters
      )
    except Exception:
      # Only on the way out: the settle reads below already record where the arm came to rest, so
      # a `finally` here would ask the device a second time on every successful move.
      await self._record_where_it_stopped()
      raise

    # The reply arrives when the move ends, not when the arm stops. Two reads in a row at the
    # target say it has: the extremes of a swing never are. Each read records where the arm is.
    self.update_location_by_reference_point(x)
    at_target = 0
    for _ in range(settle_reads):
      reached = await self.request_position()
      at_target = at_target + 1 if abs(reached - x) <= c.x_mm_per_increment else 0
      if at_target == 2:
        return resp
    logger.warning(
      "the %s X-arm was sent to %s mm and had not come to rest there after %d reads",
      self.side,
      x,
      settle_reads,
    )
    return resp

  async def _unchecked_fw_move_x_with_attached_components_at_z_safety(self, x: float):
    """Move this arm to an X position with all attached components in Z-safety position. Nothing is
    guarded and nothing is recorded.

    The master raises what the arm carries before it travels, where `move_x` travels with the arm
    as it stands and leaves getting to Z safety to the caller.

    Args:
      x: where to send the arm, in mm at its reference point.
    """
    return await self._driver.send_command(
      module="C0",
      command="KX" if self.side == "left" else "KR",
      xs=self.configuration.x_mm_to_increments(x),
    )

  async def move_x_relative(
    self,
    distance: float,
    acceleration_level: int = 3,
    current_limit: int = 7,
  ):
    """Move the arm by a distance from where it is now.

    Collision risk: this moves the arm and everything mounted on it, with no regard for what is
    in the way.

    Where the arm is is read from the device and the distance added to it, so a relative move is
    an absolute move to a place worked out here - and is bounded by the arm's travel range like any
    other.

    Args:
      distance: how far to move, in mm. Positive moves along the rail towards higher x, negative
        towards lower.
      acceleration_level: which acceleration curve to use.
      current_limit: the motor current limit.
    Raises:
      ValueError: If the arm would end up outside its travel range, or an argument is outside what
        the drive accepts.
      RuntimeError: If the arm's geometry was not resolved.
    """
    return await self.move_x(
      await self.request_position() + distance,
      acceleration_level=acceleration_level,
      current_limit=current_limit,
    )

  async def _switch_drive_power_off(self):
    """Switch this arm's drive power off, leaving it free to be pushed by hand."""
    return await self._driver.send_command(
      module="X0", command="XO" if self.side == "left" else "SO"
    )
