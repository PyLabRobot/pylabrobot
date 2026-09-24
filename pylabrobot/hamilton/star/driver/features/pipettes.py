"""The pipetting channels: the row of independently driven pipettes on an arm."""

import asyncio
import dataclasses
import datetime
import enum
import logging
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import (
  TYPE_CHECKING,
  Any,
  AsyncIterator,
  Awaitable,
  Callable,
  Dict,
  Iterable,
  List,
  Literal,
  Optional,
  Sequence,
  Tuple,
  TypeVar,
  Union,
  cast,
)

from pylabrobot.hamilton.protocol.text.framing import parse_firmware_version_date
from pylabrobot.hamilton.star.driver.errors import (
  NoTeachInSignalError,
  STARFirmwareError,
  channels_that_faulted,
)
from pylabrobot.hamilton.star.driver.lock import _FirmwareLock
from pylabrobot.lib.liquid_handling.channel_positioning import compute_channel_offsets
from pylabrobot.lib.liquid_handling.pipette_batch_scheduling import (
  ChannelBatch,
  plan_batches,
  validate_channel_selections,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.errors import HasTipError, NoTipError
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip, TipDropMethod, TipPickupMethod
from pylabrobot.resources.n_channel_pipettes import NChannelPipette, TipMountingShaft
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipSpot, tip_origin

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.features.x_arm import XArm
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)

ANY_COLUMN = 1e6
"""An X tolerance wider than any deck: X alone never splits a tip command into batches, so spots
spread across columns go out in one, as legacy sends them."""

T = TypeVar("T")

ChannelType = Literal["ML_STAR", "ML_STAR_RPC"]
HeadType = Literal["ML_STAR", "ML_STAR_PLE", "ML_STAR_RPC"]
StopDiscType = Literal["core_i", "core_ii"]
PressureADC = Literal["Renesas_X9268", "Analog_Devices_AD5263"]


# The letters a channel's module is addressed by, in order from the back. `channel_id` spells an
# address with them and `channel_from_module` reads one back.
CHANNEL_MODULE_LETTERS = "123456789ABCDEFG"


@dataclass
class PipetteConfiguration:
  """The hardware fitted to a single pipetting channel.

  Read off the channel itself. Every field is None until it has been read.
  """

  channel_type: Optional[ChannelType] = None
  head_type: Optional[HeadType] = None
  stop_disc_type: Optional[StopDiscType] = None
  pressure_adc: Optional[PressureADC] = None
  firmware_version: Optional[str] = None
  width: Optional[float] = None
  """How wide the pipette is, in mm. Two channels cannot sit closer than this in Y."""


@dataclass
class PipettesConfiguration:
  """Configuration for the pipetting channels, and for each channel in turn.

  The encoder resolutions convert between the units a command carries on the wire (increments)
  and the units the driver speaks (mm, uL). They are properties of the channel drives and are
  identical across a device's channels, so they are held once, not per channel.

  `channels` holds what each individual channel carries. It is empty until setup has counted the
  channels; only the device reports how many there are.
  """

  hardware_query_first_year: int = 2017
  """Firmware from 2016 or older does not carry the hardware query."""

  initialize_y_range: Tuple[float, float] = (217.5, 405.0)
  """The Y band the channels spread across during the initialization procedure, in mm."""

  initialize_begin_of_tip_deposit: float = 245.0
  """Where the procedure begins depositing whatever is mounted, in mm."""

  initialize_end_of_tip_deposit: float = 122.0
  """Where it ends, in mm."""

  initialize_z_position_at_end: float = 360.0
  """Where the channels are left along Z when it finishes, in mm."""

  initialize_tip_type: int = 4
  initialize_discarding_method: int = 0

  initialize_read_timeout: int = 120
  """How long to wait for the procedure, in seconds. The channels travel to the waste and eject
  there, so the reply is a long time coming."""

  x_reference_anchor: str = "c"
  """Along X every channel sits at the arm's own reference point: the master and the X-drive board
  report the same position, so a channel's X is the arm's."""
  y_reference_anchor: str = "c"
  z_reference_anchor: str = "b"
  """Along Z the drive reports the bottom of the tip mounting shaft, which is what Hamilton's
  firmware calls the stop disc. A channel carrying a shaft is anchored on the shaft's end rather
  than on this, which then applies only to one that carries none."""

  y_drive_mm_per_increment: float = 0.046302083
  z_drive_mm_per_increment: float = 0.01072765

  z_range_increments: Tuple[int, int] = (9_320, 31_200)
  """The Z travel the drive counts in, in increments, lowest first. The floor is the deck
  surface, which is as low as a stop disc goes."""

  # -- what a channel's own Y drive accepts --
  y_range_increments: Tuple[int, int] = (0, 13_714)
  y_drive_speed_range_increments: Tuple[int, int] = (20, 8_000)
  y_drive_acceleration_level_range: Tuple[int, int] = (1, 4)
  """Each level is `level * 5000` increments/s2: 231.5, 463.0, 694.5, 926.0 mm/s2."""
  y_drive_current_limit_range: Tuple[int, int] = (0, 7)
  clld_detection_edge_range: Tuple[int, int] = (0, 1_023)
  clld_detection_drop_range: Tuple[int, int] = (0, 1_023)
  lld_post_detection_distance_range_increments: Tuple[int, int] = (0, 9_999)
  lld_max_delta_range_increments: Tuple[int, int] = (0, 9_999)
  """How far a pressure detection may sit from the capacitive one verifying it, in Z increments."""
  plld_detection_edge_range: Tuple[int, int] = (0, 1_023)
  plld_detection_drop_range: Tuple[int, int] = (0, 1_023)
  plld_foam_detection_drop_range: Tuple[int, int] = (0, 1_023)
  plld_foam_detection_edge_tolerance_range: Tuple[int, int] = (0, 1_023)
  plld_foam_ad_values_range: Tuple[int, int] = (0, 4_999)
  plld_foam_search_speed_range_increments: Tuple[int, int] = (20, 13_500)

  # -- what a channel's own Z drive accepts, for the moves addressed to the channel itself --
  z_drive_speed_range_increments: Tuple[int, int] = (20, 15_000)
  z_drive_speed_default: float = 125.0
  """How fast a channel's Z drive moves when the caller names nothing, in mm/s."""
  z_drive_acceleration_range_increments: Tuple[int, int] = (5, 150)
  z_drive_acceleration_default: float = 800.0
  """How hard it accelerates when the caller names nothing, in mm/s2. Counted in thousands of
  increments per second squared, unlike the positions and speeds beside it."""
  z_drive_current_limit_range: Tuple[int, int] = (0, 7)
  z_drive_current_limit_default: int = 3
  z_touch_pwm_range: Tuple[int, int] = (0, 125)
  """What a z-touch search's force limiter and push-down force are set in."""
  drive_parameters: Dict[str, int] = field(
    default_factory=lambda: {"zv": 5, "zr": 3, "yv": 4, "yr": 1}
  )
  """The stored drive parameters a channel reads and writes, and their widths on the wire."""

  z_range: Tuple[float, float] = (99.98, 334.7)
  """The Z window the channels reach, in mm, lowest first.

  What the drive counts, until setup replaces the ceiling with what `probe_z_max` read off this
  device's channels. The floor is the deck surface either way."""
  dispensing_drive_mm_per_increment: float = 0.002734375
  dispensing_drive_uL_per_increment: float = 0.046876
  dispensing_drive_speed_range_increments: Tuple[int, int] = (20, 13_500)
  dispensing_drive_acceleration_range_increments: Tuple[int, int] = (1, 100)
  """Counted in increments per second squared, unlike the Z drive's."""
  dispensing_drive_current_limit_range: Tuple[int, int] = (0, 7)
  dispensing_drive_volume_range_increments: Tuple[int, int] = (0, 26_666)

  channel_size_z: float = 140.0
  """How tall to model a channel, in mm. Not read from anywhere: how far a channel extends is not
  something the device reports."""

  channels: List[PipetteConfiguration] = field(default_factory=list)
  """One entry per channel, in channel order."""

  # -- conversions: the wire counts in increments, the driver speaks mm and uL ---------------

  def y_drive_increments_to_mm(self, increments: int) -> float:
    """A Y-drive position in mm, from the increments the drive counts in."""
    return round(increments * self.y_drive_mm_per_increment, 2)

  def y_drive_mm_to_increments(self, mm: float) -> int:
    """A Y-drive position in increments, from mm."""
    return round(mm / self.y_drive_mm_per_increment)

  def z_drive_increments_to_mm(self, increments: int) -> float:
    """A Z-drive position in mm, from increments."""
    return round(increments * self.z_drive_mm_per_increment, 2)

  def z_drive_acceleration_increments_to_mm(self, increments: int) -> float:
    """A Z-drive acceleration in mm/s2, from the thousands of increments it is counted in."""
    return round(increments * self.z_drive_mm_per_increment * 1000, 1)

  def z_drive_acceleration_mm_to_increments(self, mm: float) -> int:
    """A Z-drive acceleration in increments, from mm/s2."""
    return round(mm / (self.z_drive_mm_per_increment * 1000))

  @property
  def y_speed_range(self) -> Tuple[float, float]:
    """Y-drive speed window (mm/s)."""
    low, high = self.y_drive_speed_range_increments
    return (self.y_drive_increments_to_mm(low), self.y_drive_increments_to_mm(high))

  @property
  def z_speed_range(self) -> Tuple[float, float]:
    """Z-drive speed window (mm/s)."""
    low, high = self.z_drive_speed_range_increments
    return (self.z_drive_increments_to_mm(low), self.z_drive_increments_to_mm(high))

  @property
  def z_acceleration_range(self) -> Tuple[float, float]:
    """Z-drive acceleration window (mm/s2)."""
    low, high = self.z_drive_acceleration_range_increments
    return (
      self.z_drive_acceleration_increments_to_mm(low),
      self.z_drive_acceleration_increments_to_mm(high),
    )

  def z_drive_mm_to_increments(self, mm: float) -> int:
    """A Z-drive position in increments, from mm."""
    return round(mm / self.z_drive_mm_per_increment)

  def dispensing_drive_increments_to_uL(self, increments: int) -> float:
    """A dispensing-drive position as the volume it holds, from increments."""
    return round(increments * self.dispensing_drive_uL_per_increment, 1)

  def dispensing_drive_uL_to_increments(self, uL: float) -> int:
    """A dispensing-drive position in increments, from the volume to hold."""
    return round(uL / self.dispensing_drive_uL_per_increment)

  def dispensing_drive_increments_to_mm(self, increments: int) -> float:
    """A dispensing-drive position as how far the piston has travelled, from increments."""
    return round(increments * self.dispensing_drive_mm_per_increment, 3)

  def dispensing_drive_mm_to_increments(self, mm: float) -> int:
    """A dispensing-drive position in increments, from how far the piston should travel."""
    return round(mm / self.dispensing_drive_mm_per_increment)

  def check_channels_agree(self) -> None:
    """Warn if the channels are not all running the same firmware.

    The resolutions above are held once for every channel, so they are one board's. Channels are
    replaced individually, and a channel on different firmware may not convert the same way. A
    device repaired piecemeal is the case this catches.
    """
    by_version: Dict[str, List[int]] = {}
    for channel, entry in enumerate(self.channels):
      if entry.firmware_version is not None:
        by_version.setdefault(entry.firmware_version, []).append(channel)
    if len(by_version) <= 1:
      return
    reported = "; ".join(
      f"{version} on channel{'s' if len(channels) > 1 else ''} "
      f"{', '.join(str(c) for c in channels)}"
      for version, channels in by_version.items()
    )
    logger.warning(
      "the pipetting channels are not all on the same firmware (%s). The conversion factors here "
      "are held once for every channel, so a channel on different firmware may convert "
      "differently, and the version recorded for the feature is channel %d's.",
      reported,
      next(iter(by_version.values()))[0],
    )

  def resolve_channels(self, num_channels: int) -> None:
    """Size `channels` against the device, once it has said how many channels it has.

    A list supplied up front is left as it is. A caller can configure channels before the device
    is known, and it is then checked, not overwritten.

    Args:
      num_channels: how many channels the device reported.

    Raises:
      ValueError: If a supplied list does not have one entry per channel.
    """
    if not self.channels:
      self.channels.extend(PipetteConfiguration() for _ in range(num_channels))
    elif len(self.channels) != num_channels:
      raise ValueError(f"configuration has {len(self.channels)} channels, expected {num_channels}")


class Pipettes:
  """The pipetting channels.

  Reached as `driver.pipettes`. Individual channels are addressed as `P1`..`PG`. The commands
  that act on all of them at once go to the master, so this feature speaks to both.

  `configuration` holds what every channel shares, and one entry per channel in
  `configuration.channels`.
  """

  class LLDMode(enum.Enum):
    """How a channel senses the liquid. Numbered as the Prep's and the firmware's `lm`, so the
    three read the same."""

    OFF = 0
    CAPACITIVE = 1
    PRESSURE = 2
    DUAL = 3

  class PressureLLDMode(enum.Enum):
    """What a pressure search stops at: the liquid, or the foam and then the liquid under it."""

    LIQUID = 0
    FOAM = 1

  # Y speed when the caller names none, in mm/s.
  default_y_speed: float = 250.0
  # Y acceleration level when the caller names none, 1 (gentlest) to 4.
  default_y_acceleration_level: int = 3
  # Z speed when the caller names none, in mm/s.
  default_z_speed: float = 125.0
  # Z acceleration when the caller names none, in mm/s2.
  default_z_acceleration: float = 800.0
  # Containers within this X distance are probed in one batch, in mm.
  default_x_grouping_tolerance: float = 0.1
  # How far above a container's top a liquid search starts, in mm.
  search_start_clearance: float = 5.0

  def __init__(self, driver: "STARDriver", configuration: Optional[PipettesConfiguration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the channels' device facts. Defaults to `PipettesConfiguration()`.
    """
    self._driver = driver
    # One resource per channel, in channel order, when the driver was given a deck. Setup puts them
    # on the arm; the reads keep them in step. Without a deck the list stays empty.
    self.resources: List[Resource] = []
    self.configuration = configuration or PipettesConfiguration()
    # The height the channels travel at when a command names none, in mm. Legacy STARBackend's
    # channel traversal height.
    self.default_minimum_traverse_height: float = 245.0

  # -- addressing ------------------------------------------------------------

  @staticmethod
  def channel_id(channel: int) -> str:
    """The module a channel is addressed by. Channel 0 is the one at the back."""
    return "P" + CHANNEL_MODULE_LETTERS[channel]

  @staticmethod
  def channel_from_module(module: str) -> Optional[int]:
    """Which channel a module address names, the other way round from `channel_id`.

    Args:
      module: the two-character module, e.g. `P1`.

    Returns:
      The channel, 0-indexed from the back, or None when the address names something else.
    """
    if len(module) != 2 or module[0] != "P" or module[1] not in CHANNEL_MODULE_LETTERS:
      return None
    return CHANNEL_MODULE_LETTERS.index(module[1])

  @property
  def num_channels(self) -> int:
    """How many channels are fitted, as counted at setup."""
    return self._driver.num_channels

  # -- session / discovery ---------------------------------------------------

  def _require_channel(self, channel: int) -> None:
    """Raise unless this device has that channel.

    Args:
      channel: which channel, 0-indexed from the back.

    Raises:
      ValueError: If it is not a whole number, or the device has no such channel.
    """
    if not isinstance(channel, int) or not (0 <= channel <= self.num_channels - 1):
      raise ValueError(f"channel must be in [0, {self.num_channels - 1}], is {channel}")

  async def _record_where_they_stopped(
    self, axis: Literal["y", "z"], channels: Optional[Iterable[int]] = None
  ) -> None:
    """Read where channels came to rest along one axis, and record it.

    A move that stopped part way left them somewhere no target describes. Its own failure is
    logged and swallowed: it must not replace the move's exception, which is the one that says
    what went wrong.

    Args:
      axis: which axis the move drove - `y` across the deck, `z` up and down.
      channels: which channels, 0-indexed from the back. All of them when None, read in one
        command rather than one each.
    """
    try:
      if channels is None:
        await (self.request_y_positions() if axis == "y" else self.request_stop_disc_z_positions())
        return
      for channel in channels:
        if axis == "y":
          await self.request_y_position(channel)
        else:
          await self.request_stop_disc_z_position(channel)
    except Exception:
      logger.warning(
        "could not read where the channels stopped along %s; their model is stale", axis
      )

  async def _require_tips(self, channels: Iterable[int], instead: str) -> None:
    """Raise unless every named channel carries a tip.

    Args:
      channels: which channels, 0-indexed from the back.
      instead: the method to reach for when they do not, named in the refusal.

    Raises:
      ValueError: If any of them carries no tip, naming which.
    """
    presence = await self.sense_tip_presence()
    bare = [channel for channel in channels if not presence[channel]]
    if bare:
      raise ValueError(
        f"channels {bare} carry no tips, so they have no tool bottom; "
        f"`{instead}` is the one that answers whatever is mounted"
      )

  async def _require_iswap_parked(self) -> None:
    """Raise unless the iSWAP on these channels' arm is parked; nothing to check without one.

    Raises:
      RuntimeError: If it is not parked.
    """
    iswap = self.arm.iswap
    if iswap is not None and not await iswap.request_parked():
      raise RuntimeError(
        "the iSWAP is not parked, and the channels move where it stands. "
        "Call `await star.iswap.park()` first."
      )

  async def request_firmware_version(self, channel: int) -> Tuple[str, datetime.date]:
    """Request one channel's firmware version and build date.

    Args:
      channel: which channel to ask, 0-indexed from the back.

    Returns:
      The version string and its build date, e.g. `("4.0S j 2022-03-16", date(2022, 3, 16))`.
    """
    self._require_channel(channel)
    resp = await self._driver.send_command(module=self.channel_id(channel), command="RF")
    return resp.split("rf")[-1], parse_firmware_version_date(resp)

  async def request_min_pipette_width(self, channel: int) -> float:
    """Request how wide a pipette is.

    This is what bounds how close two channels can sit in Y: they cannot overlap.

    Args:
      channel: which channel to ask, 0-indexed from the back.

    Returns:
      The width in mm.
    """
    self._require_channel(channel)
    resp = await self._driver.send_command(
      module=self.channel_id(channel), command="VY", fmt="yc### (n)"
    )
    increments = cast(List[int], resp["yc"])[1]
    return self.configuration.y_drive_increments_to_mm(increments)

  async def request_pipette_configuration(self, channel: int) -> PipetteConfiguration:
    """Request what hardware is fitted to a pipette.

    Firmware from 2016 or older does not carry it.

    Args:
      channel: which channel to ask, 0-indexed from the back.

    Returns:
      What the channel reports about itself. The fields it does not report - its firmware version
      and its width - are left None, since they are separate queries.

    Raises:
      ValueError: If the reply carries no hardware fields at all.
    """
    self._require_channel(channel)
    resp = await self._driver.send_command(module=self.channel_id(channel), command="VW")
    fields = resp.split("vw")[-1].strip().split()
    if not fields:
      raise ValueError(f"no hardware fields in the reply from channel {channel}: {resp!r}")

    def field_at(index: int) -> Optional[str]:
      # The reply carries between two and four fields depending on firmware. A field that is not
      # there falls back to its baseline value instead of failing: these are descriptive, and no
      # pipetting decision reads them.
      return fields[index] if index < len(fields) else None

    return PipetteConfiguration(
      channel_type="ML_STAR_RPC" if field_at(0) == "1" else "ML_STAR",
      head_type=(
        "ML_STAR_PLE" if field_at(1) == "1" else "ML_STAR_RPC" if field_at(1) == "2" else "ML_STAR"
      ),
      stop_disc_type="core_i" if field_at(2) in ("0", None) else "core_ii",
      pressure_adc="Analog_Devices_AD5263" if field_at(3) == "1" else "Renesas_X9268",
    )

  async def discover(self):
    """Read what each channel is and what it can do.

    Read-only, and asks every channel at once. Fills in `configuration.channels`.
    """
    self.configuration.resolve_channels(self.num_channels)
    await asyncio.gather(*(self._discover_channel(ch) for ch in range(self.num_channels)))
    self.configuration.check_channels_agree()

  async def _discover_channel(self, channel: int):
    version, build_date = await self.request_firmware_version(channel)
    # On older firmware the hardware fields simply stay unread, rather than the query failing.
    pipette = (
      await self.request_pipette_configuration(channel)
      if build_date.year >= self.configuration.hardware_query_first_year
      else PipetteConfiguration()
    )
    pipette.firmware_version = version
    pipette.width = await self.request_min_pipette_width(channel)
    self.configuration.channels[channel] = pipette

  # -- where the channels are ------------------------------------------------

  def _reference_anchor(self, resource: Resource) -> Coordinate:
    """Where on a channel's resource the drives report, from its left front bottom corner.

    The drives report the centre-centre-bottom of the tip mounting shaft, on all three axes, and the
    shaft hangs below the body it is mounted on, so the point the drive names is the shaft's end rather
    than the body's own bottom. A channel carrying a shaft states that point as its `reference_point`,
    so a shaft of another length needs nothing changed here; one that carries none falls back to its
    anchors.

    Args:
      resource: the resource modelling the channel.

    Returns:
      The offset from the resource's corner to the point the drives report.
    """
    if isinstance(resource, NChannelPipette):
      return resource.reference_point
    stated = getattr(resource, "reference_point", None)
    if isinstance(stated, Coordinate):
      return stated
    anchor = resource.get_anchor(
      x=self.configuration.x_reference_anchor,
      y=self.configuration.y_reference_anchor,
      z=self.configuration.z_reference_anchor,
    )
    shaft = next(
      (child for child in resource.children if isinstance(child, TipMountingShaft)), None
    )
    if shaft is None or shaft.location is None:
      return anchor
    return Coordinate(anchor.x, anchor.y, shaft.location.z)

  def get_reference_point_location(self, channel: int) -> Optional[Coordinate]:
    """Where the model has a channel's reference point, in mm on the deck.

    The inverse of `update_location_by_reference_point`: it converts a reported position into a
    location, and this converts a location back into the position that would be reported. X is
    the arm's, so it is carried through unread.

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      Where the model has it, or None when there is nothing modelling it yet.
    """
    deck = self._driver.deck
    if channel >= len(self.resources) or deck is None:
      return None
    resource = self.resources[channel]
    if resource.location is None or resource.parent is None:
      return None
    return (
      resource.location + resource.parent.get_location_wrt(deck) + self._reference_anchor(resource)
    )

  def update_location_by_reference_point(
    self, channel: int, y: Optional[float] = None, z: Optional[float] = None
  ) -> None:
    """Record where a channel is on the resource that models it.

    Y and Z only. A channel rides the arm, so its resource is a child of the arm's and follows it
    in X with nothing recording that. A resource is located by its left front bottom corner, and
    each axis differs from the reported position by the channel's reference point.

    The channel states that point, because it is not a corner of the box: the drives report the
    stop disc, the shaft a tip mounts on, which hangs below the body. A channel stating nothing
    falls back to its anchors, the same point when it carries no shaft.

    Both drives answer in the deck's frame, while a resource's location is measured from its
    parent, the arm. The arm's position is taken out before either is recorded. Does nothing when
    the driver was given no deck to model into.

    Args:
      channel: which channel, 0-indexed from the back.
      y: where it is now, in mm on the deck. Left as it was when None.
      z: where its stop disc is now, in mm on the deck. Left as it was when None.
    """
    deck = self._driver.deck
    if channel >= len(self.resources) or deck is None:
      return
    resource = self.resources[channel]
    if resource.location is None or resource.parent is None:
      return
    here, on_the_arm = resource.location, resource.parent.get_location_wrt(deck)
    anchor = self._reference_anchor(resource)
    resource.location = Coordinate(
      here.x,
      here.y if y is None else y - on_the_arm.y - anchor.y,
      here.z if z is None else z - on_the_arm.z - anchor.z,
    )

  @staticmethod
  def add_tip_mounting_shaft(channel: Resource) -> None:
    """Hang a tip mounting shaft off the lower end of a channel, and measure the channel from it.

    The shaft hangs its own length below the channel's bottom, not inside it, so it reaches
    lowest and the channel body starts clear of it. This is the arrangement a 96-head has, where
    the shafts define the bottom of the assembly. It is centred on the channel, the axis a tip is
    collected on. A shaft already there is left alone, and repeated setups do not duplicate it.

    The shaft is the stop disc the Z drive reports, and so is also where the channel is measured
    from. That is the reference point this states and `update_location_by_reference_point` reads
    back.

    Args:
      channel: the channel resource to hang it from.
    """
    name = f"{channel.name}_tip_mounting_shaft"
    if any(child.name == name for child in channel.children):
      return
    shaft = TipMountingShaft(name=name, tip_pickup_mode="core")
    channel.assign_child_resource(
      shaft,
      location=Coordinate(
        (channel.get_absolute_size_x() - shaft.get_absolute_size_x()) / 2,
        (channel.get_absolute_size_y() - shaft.get_absolute_size_y()) / 2,
        -shaft.get_absolute_size_z(),
      ),
    )
    # Stated on a plain `Resource`, which does not declare the field: a channel is not yet the
    # `NChannelPipette` that would, and that carries its own reference point as a `Coordinate`.
    channel.reference_point = Coordinate(  # type: ignore[attr-defined]
      channel.get_absolute_size_x() / 2,
      channel.get_absolute_size_y() / 2,
      -shaft.get_absolute_size_z(),
    )

  # -- what the model has on each channel --------------------------------------------------------

  def shaft(self, channel: int) -> Optional[TipMountingShaft]:
    """The mounting shaft modelling a channel, or None while nothing models it.

    Args:
      channel: which channel, 0-indexed from the back.
    """
    if channel >= len(self.resources):
      return None
    return next(
      (child for child in self.resources[channel].children if isinstance(child, TipMountingShaft)),
      None,
    )

  def get_mounted_tip(self, channel: int) -> Optional[Tip]:
    """The tip the model has on a channel, or None if it carries none.

    What the model says, not what the device senses: `sense_tip_presence` asks the channels.

    Args:
      channel: which channel, 0-indexed from the back.
    """
    shaft = self.shaft(channel)
    tip = shaft.tip if shaft is not None else None
    return tip if isinstance(tip, Tip) else None

  def _release_modelled_tip(self, channel: int) -> Optional[Tip]:
    """Take a channel's tip off its shaft in the model, leaving it assigned to nothing.

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      The tip, or None if the model had none on that channel.
    """
    shaft = self.shaft(channel)
    if shaft is None or not shaft.has_tip():
      return None
    return cast(Tip, shaft.release_tip())

  # ----------------------------------------
  # Probing

  # -- channel initialization ------------------------------------------------

  def default_initialize_y_positions(self) -> List[float]:
    """Where each channel sits in Y during initialization, in mm, back to front.

    The channels spread evenly across the band the procedure uses, clear of one another whatever
    the channel count.

    Returns:
      One position per channel, in mm, back to front.
    """
    front, back = self.configuration.initialize_y_range
    spacing = round((back - front) * 10) // (self.num_channels - 1)
    return [(round(back * 10) - channel * spacing) / 10 for channel in range(self.num_channels)]

  async def sense_tip_presence(self) -> List[int]:
    """Sense tip presence on every channel, from their sleeve sensors.

    Answered as the channels answer it, 1 where a tip is and 0 where none is, rather than narrowed
    to True and False: the two carry the same meaning, and a value that is neither would be lost by
    the narrowing rather than read back as it stands.

    Returns:
      One value per channel, 1 where a tip is mounted, 0-indexed from the back.
    """
    resp = await self._driver.send_command(module="C0", command="RT", fmt="rt# (n)")
    return cast(List[int], resp.get("rt"))

  async def initialize(
    self,
    x_position: Optional[float] = None,
    y_positions: Optional[List[float]] = None,
    begin_of_tip_deposit_process: Optional[float] = None,
    end_of_tip_deposit_process: Optional[float] = None,
    z_position_at_end_of_a_command: Optional[float] = None,
    tip_pattern: Optional[List[bool]] = None,
    tip_type: Optional[int] = None,
    discarding_method: Optional[int] = None,
  ):
    """Initialize the channels, discarding whatever is mounted on them.

    This moves the channels: they spread out across the Y band, travel to the tip waste, and
    eject. Anything on a channel, including a gripper, ends up in the waste.

    Args:
      x_position: X to eject at, in mm. Defaults to the device's tip waste position.
      y_positions: where to put each channel in Y, in mm, back to front. Defaults to spreading
        them evenly across the Y band the procedure uses.
      begin_of_tip_deposit_process: Z to start the eject from, in mm.
      end_of_tip_deposit_process: Z the eject ends at, in mm.
      z_position_at_end_of_a_command: Z to leave the channels at, in mm.
      tip_pattern: which channels take part. Defaults to all of them.
      tip_type: tip type table index.
      discarding_method: how tips are discarded.
    """
    c = self.configuration
    if x_position is None:
      if self._driver.configuration is None:
        raise RuntimeError("no configuration read; have you called `star.setup()`?")
      x_position = self._driver.configuration.tip_waste_x_position
    if y_positions is None:
      y_positions = self.default_initialize_y_positions()
    if tip_pattern is None:
      tip_pattern = [True] * self.num_channels
    if begin_of_tip_deposit_process is None:
      begin_of_tip_deposit_process = c.initialize_begin_of_tip_deposit
    if end_of_tip_deposit_process is None:
      end_of_tip_deposit_process = c.initialize_end_of_tip_deposit
    if z_position_at_end_of_a_command is None:
      z_position_at_end_of_a_command = c.initialize_z_position_at_end
    if tip_type is None:
      tip_type = c.initialize_tip_type
    if discarding_method is None:
      discarding_method = c.initialize_discarding_method

    resp = await self._driver.send_command(
      module="C0",
      command="DI",
      subsystem=_FirmwareLock.CHANNELS,
      read_timeout=c.initialize_read_timeout,
      xp=[f"{round(x_position * 10):05}"],
      yp=[f"{round(y * 10):04}" for y in y_positions],
      tp=f"{round(begin_of_tip_deposit_process * 10):04}",
      tz=f"{round(end_of_tip_deposit_process * 10):04}",
      te=f"{round(z_position_at_end_of_a_command * 10):04}",
      tm=[f"{tm:01}" for tm in tip_pattern],
      tt=f"{tip_type:02}",
      ti=discarding_method,
    )
    # Everything the channels carried is in the waste now, and belongs nowhere.
    for channel, involved in enumerate(tip_pattern):
      if involved:
        self._release_modelled_tip(channel)
    # The command drives every channel: along Y to its initialization position, and along Z to
    # `z_position_at_end_of_a_command`. Read both back, or the model has them where they were.
    await self._record_where_they_stopped("y")
    await self._record_where_they_stopped("z")
    return resp

  def _min_pair_spacing(self, i: int, j: int) -> float:
    """The smallest Y gap two channels may sit at by themselves, in mm, whatever lies between them.

    The wider of the two, rounded up to 0.1 mm, since neither may overlap the other.

    Args:
      i: one channel, 0-indexed from the back.
      j: the other.

    Returns:
      The gap in mm.

    Raises:
      RuntimeError: If a channel's width has not been read yet.
    """
    widths = [self.configuration.channels[channel].width for channel in (i, j)]
    if any(width is None for width in widths):
      raise RuntimeError(f"channels {i} and {j} have no width read yet; run discovery first")
    return math.ceil(max(cast(List[float], widths)) * 10) / 10

  def _min_spacing_between(self, i: int, j: int) -> float:
    """The smallest allowed Y gap two channels may sit at, in mm.

    Adjacent channels take the wider of the two, rounded up to 0.1 mm, since neither may overlap
    the other. Channels further apart take the sum of the pairs between them.

    Args:
      i: one channel, 0-indexed from the back.
      j: the other.

    Returns:
      The gap in mm.

    Raises:
      RuntimeError: If a channel's width has not been read yet.
    """
    lo, hi = min(i, j), max(i, j)
    if hi - lo > 1:
      return sum(self._min_spacing_between(k, k + 1) for k in range(lo, hi))
    return self._min_pair_spacing(lo, hi)

  @property
  def minimum_y_spacings(self) -> List[float]:
    """The smallest Y gap each channel keeps from the one in front of it, in mm, one per channel.

    What `plan_batches` asks for; the last channel has nothing in front of it.

    Returns:
      One gap per channel, back to front.

    Raises:
      RuntimeError: If a channel's width has not been read yet.
    """
    count = self.num_channels
    return [self._min_spacing_between(i, i + 1) if i + 1 < count else 0.0 for i in range(count)]

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  @property
  def arm(self) -> "XArm":
    """The arm carrying these channels.

    The firmware keeps the two X-drives' feature bits disjoint: channels are on one arm or the
    other, never both. Discovery builds this feature on that arm; this property finds it back
    by identity.

    Returns:
      The arm carrying independent single-channel pipettes.
    """
    return next(a for a in self._driver.arms if a.pipettes is self)

  def _check_reachable(self, axis: Literal["x", "y", "z"], value: float) -> None:
    """Raise if the channels cannot be sent where they are being asked to go.

    The one gate every position passes through. What the channels are allowed to do is decided in
    one place: travel limits now, and whatever else has to hold before they move as it is added.

    X is the arm's travel as the arm reports it. A channel sits at the arm's reference point, with
    no offset to apply. Y is the band the device states its channels reach, which differs by the
    side the arm is on.

    Args:
      axis: which axis - `x` along the rail, `y` across the arm.
      value: where it would be sent, in mm.

    Raises:
      ValueError: If the channels cannot reach it.
      RuntimeError: If the limits were not read, so how far they reach is unknown.
    """
    device = self._driver.configuration
    if device is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    if axis == "x":
      x_range = self.arm.configuration.x_range
      if x_range is None:
        raise RuntimeError("the arm's X travel is not known; have you called `star.setup()`?")
      low, high = x_range
    elif axis == "z":
      low, high = self.configuration.z_range
    else:
      low = (
        device.left_arm_min_y_position
        if self.arm.side == "left"
        else device.right_arm_min_y_position
      )
      high = device.pip_maximal_y_position
    if not low <= value <= high:
      raise ValueError(f"{axis} must be between {low} and {high} mm, is {value}")

  # -- Memory of Speed & Acceleration --------------------------------------------------------------

  # -- the raw register access these share --

  def _require_drive_parameter(self, parameter: str) -> int:
    """The wire width of a channel's stored drive parameter.

    Args:
      parameter: `yv`/`zv` for Y/Z speed, `yr` for Y acceleration level, `zr` for Z acceleration.

    Returns:
      Its digits on the wire.

    Raises:
      ValueError: If it is none of these.
    """
    widths = self.configuration.drive_parameters
    if parameter not in widths:
      raise ValueError(f"unknown drive parameter {parameter!r}, expected one of {tuple(widths)}")
    return widths[parameter]

  def _drive_parameter_to_increments(self, parameter: str, value: float) -> int:
    """A stored drive parameter in what the drive counts in, from mm/s, mm/s2 or a level."""
    c = self.configuration
    if parameter == "yv":
      return c.y_drive_mm_to_increments(value)
    if parameter == "yr":
      return int(value)
    if parameter == "zv":
      return c.z_drive_mm_to_increments(value)
    return c.z_drive_acceleration_mm_to_increments(value)

  def _drive_parameter_to_mm(self, parameter: str, increments: int) -> float:
    """A stored drive parameter in mm/s, mm/s2 or a level, from what the drive counts in."""
    c = self.configuration
    if parameter == "yv":
      return c.y_drive_increments_to_mm(increments)
    if parameter == "yr":
      return increments
    if parameter == "zv":
      return c.z_drive_increments_to_mm(increments)
    return c.z_drive_acceleration_increments_to_mm(increments)

  async def _request_drive_parameter(self, channel: int, parameter: str) -> float:
    """Request a channel's stored drive parameter (`Px RA`).

    Args:
      channel: which channel, 0-indexed from the back.
      parameter: `yv`/`zv` for Y/Z speed, `yr` for Y acceleration level, `zr` for Z acceleration.

    Returns:
      The value in mm/s, mm/s2, or a level for `yr`.

    Raises:
      ValueError: If the channel or parameter does not exist.
    """
    self._require_channel(channel)
    width = self._require_drive_parameter(parameter)
    resp = await self._driver.send_command(
      module=self.channel_id(channel), command="RA", ra=parameter, fmt=f"{parameter}{'#' * width}"
    )
    return self._drive_parameter_to_mm(parameter, cast(int, resp[parameter]))

  async def _set_drive_parameter(self, channel: int, parameter: str, value: float) -> None:
    """Write a channel's stored drive parameter (`Px AA`).

    Args:
      channel: which channel, 0-indexed from the back.
      parameter: `yv`/`zv` for Y/Z speed, `yr` for Y acceleration level, `zr` for Z acceleration.
      value: in mm/s, mm/s2, or a level for `yr`.

    Raises:
      ValueError: If the channel, parameter or value is out of range.
    """
    self._require_channel(channel)
    width = self._require_drive_parameter(parameter)
    c = self.configuration
    low, high = {
      "yv": c.y_speed_range,
      "yr": c.y_drive_acceleration_level_range,
      "zv": c.z_speed_range,
      "zr": c.z_acceleration_range,
    }[parameter]
    if not low <= value <= high:
      raise ValueError(f"{parameter} must be between {low} and {high}, is {value}")
    increments = self._drive_parameter_to_increments(parameter, value)
    written: Dict[str, Any] = {parameter: f"{increments:0{width}}"}
    await self._driver.send_command(module=self.channel_id(channel), command="AA", **written)

  @asynccontextmanager
  async def _temporary_drive_profile(
    self, values: Dict[str, Optional[float]], channels: Optional[List[int]]
  ) -> AsyncIterator[None]:
    """Set stored drive parameters for the enclosed block, then put back the defaults.

    Args:
      values: value per parameter; None leaves that parameter.
      channels: which channels, 0-indexed from the back. All of them when None.
    """
    channels = list(range(self.num_channels)) if channels is None else list(channels)
    wanted = [(p, v) for p, v in values.items() if v is not None]
    defaults: Dict[str, float] = {
      "yv": self.default_y_speed,
      "yr": self.default_y_acceleration_level,
      "zv": self.default_z_speed,
      "zr": self.default_z_acceleration,
    }
    written: List[Tuple[int, str]] = []
    try:
      for channel in channels:
        for parameter, value in wanted:
          await self._set_drive_parameter(channel, parameter, value)
          written.append((channel, parameter))
      yield
    finally:
      for channel, parameter in written:
        try:
          await self._set_drive_parameter(channel, parameter, defaults[parameter])
        except Exception:
          logger.warning(
            "could not put channel %s's %s back to %s", channel, parameter, defaults[parameter]
          )

  # ---- y -----------------------------------------------------------------------------------------

  async def request_y_speed(self, channel: int) -> float:
    """Request the Y speed a channel's drive holds (`Px RA yv`).

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      The speed in mm/s.
    """
    return await self._request_drive_parameter(channel, "yv")

  async def request_y_acceleration_level(self, channel: int) -> int:
    """Request the Y acceleration level a channel's drive holds (`Px RA yr`).

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      The level, 1 (gentlest) to 4.
    """
    return int(await self._request_drive_parameter(channel, "yr"))

  async def _set_y_speed(self, channel: int, speed: float) -> None:
    """Write the Y speed a channel's drive holds (`Px AA yv`).

    Args:
      channel: which channel, 0-indexed from the back.
      speed: in mm/s.

    Raises:
      ValueError: If the channel or speed is out of range.
    """
    await self._set_drive_parameter(channel, "yv", speed)

  async def _set_y_acceleration_level(self, channel: int, level: int) -> None:
    """Write the Y acceleration level a channel's drive holds (`Px AA yr`).

    Args:
      channel: which channel, 0-indexed from the back.
      level: 1 (gentlest) to 4.

    Raises:
      ValueError: If the channel or level is out of range.
    """
    await self._set_drive_parameter(channel, "yr", level)

  @asynccontextmanager
  async def _temporary_y_drive_profile(
    self,
    speed: Optional[float] = None,
    acceleration_level: Optional[int] = None,
    channels: Optional[List[int]] = None,
  ) -> AsyncIterator[None]:
    """Set the channels' Y speed and acceleration level for the enclosed block, then the defaults.

    Args:
      speed: in mm/s, or None to leave it.
      acceleration_level: 1 (gentlest) to 4, or None to leave it.
      channels: which channels, 0-indexed from the back. All of them when None.
    """
    async with self._temporary_drive_profile({"yv": speed, "yr": acceleration_level}, channels):
      yield

  # ---- z -----------------------------------------------------------------------------------------

  async def request_z_speed(self, channel: int) -> float:
    """Request the Z speed a channel's drive holds (`Px RA zv`).

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      The speed in mm/s.
    """
    return await self._request_drive_parameter(channel, "zv")

  async def request_z_acceleration(self, channel: int) -> float:
    """Request the Z acceleration a channel's drive holds (`Px RA zr`).

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      The acceleration in mm/s2.
    """
    return await self._request_drive_parameter(channel, "zr")

  async def _set_z_speed(self, channel: int, speed: float) -> None:
    """Write the Z speed a channel's drive holds (`Px AA zv`).

    Args:
      channel: which channel, 0-indexed from the back.
      speed: in mm/s.

    Raises:
      ValueError: If the channel or speed is out of range.
    """
    await self._set_drive_parameter(channel, "zv", speed)

  async def _set_z_acceleration(self, channel: int, acceleration: float) -> None:
    """Write the Z acceleration a channel's drive holds (`Px AA zr`).

    Args:
      channel: which channel, 0-indexed from the back.
      acceleration: in mm/s2.

    Raises:
      ValueError: If the channel or acceleration is out of range.
    """
    await self._set_drive_parameter(channel, "zr", acceleration)

  @asynccontextmanager
  async def _temporary_z_drive_profile(
    self,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    channels: Optional[List[int]] = None,
  ) -> AsyncIterator[None]:
    """Set the channels' Z speed and acceleration for the enclosed block, then the defaults.

    Args:
      speed: in mm/s, or None to leave it.
      acceleration: in mm/s2, or None to leave it.
      channels: which channels, 0-indexed from the back. All of them when None.
    """
    async with self._temporary_drive_profile({"zv": speed, "zr": acceleration}, channels):
      yield

  async def _set_default_drive_parameters(self) -> None:
    """Write the driver's Y and Z defaults into every channel's drive (`Px AA`)."""
    for channel in range(self.num_channels):
      await self._set_y_speed(channel, self.default_y_speed)
      await self._set_y_acceleration_level(channel, self.default_y_acceleration_level)
      await self._set_z_speed(channel, self.default_z_speed)
      await self._set_z_acceleration(channel, self.default_z_acceleration)

  # -- x position --------------------------------------------------------------------------------

  async def request_x_position(self) -> float:
    """Request where along X the channels are, in deck mm.

    The channels have no X drive. They ride the arm and sit at its reference point, and this asks
    the arm. Nothing is recorded: each channel's resource is a child of the arm's and follows it
    in X.

    Returns:
      The position in mm.
    """
    return await self.arm.request_position()

  async def move_to_x_position(
    self,
    x: float,
    acceleration_level: int = 3,
    current_limit: int = 7,
    settle_reads: int = 20,
  ):
    """Move the channels along X. The whole arm travels, with everything else it carries.

    Args:
      x: where to go, in mm.
      acceleration_level: how hard to accelerate, 1 to 4.
      current_limit: the motor current limit, 1 to 7.
      settle_reads: how many reads to take before calling the arm stopped.

    Raises:
      ValueError: If the channels cannot reach it.
    """
    self._check_reachable("x", x)
    return await self.arm.move_to_x_position(
      x,
      acceleration_level=acceleration_level,
      current_limit=current_limit,
      settle_reads=settle_reads,
    )

  # -- y position --------------------------------------------------------------------------------

  async def request_y_positions(self) -> List[float]:
    """Request where every channel is along Y, in one command.

    The master answers for all of them at once: one exchange, not one per channel. Each answer is
    recorded on the resource modelling that channel.

    Returns:
      The position of each channel in mm, back to front.
    """
    resp = await self._driver.send_command(module="C0", command="RY", fmt="ry#### (n)")
    positions = [round(increments / 10, 1) for increments in cast(List[int], resp["ry"])]
    for channel, y in enumerate(positions):
      self.update_location_by_reference_point(channel, y=y)
    return positions

  async def request_y_position(self, channel: int) -> float:
    """Request where a specific channel is along Y.

    Args:
      channel: the channel to request the position of.

    Returns:
      The position of the requested channel in mm.
    """
    self._require_channel(channel)
    positions = await self.request_y_positions()
    return positions[channel]

  async def _plan_y_positions(
    self, ys: Dict[int, float], make_space: bool = False
  ) -> Dict[int, float]:
    """Where every channel goes for a Y move, checked; nothing moves.

    Args:
      ys: where to put each named channel, in mm, keyed by channel, 0-indexed from the back.
      make_space: whether the channels not named may be moved. See `move_to_y_positions`.

    Returns:
      Every channel's target in mm, keyed by channel.

    Raises:
      ValueError: If a target is out of reach or two channels would stand too close.
      RuntimeError: If no configuration has been read, or the frontmost channel reads out of range.
    """
    if self._driver.configuration is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    min_y = self._driver.configuration.left_arm_min_y_position

    # The frontmost channel parks a fraction ahead of the documented minimum. Tolerate 0.2 mm of
    # that and snap it up; refuse beyond, rather than guessing what the reading means.
    positions = await self.request_y_positions()
    if positions[-1] < min_y - 0.2:
      raise RuntimeError(
        f"the frontmost channel reports {positions[-1]}mm, more than 0.2mm in front of the "
        f"{min_y}mm the channels reach. Reported: {positions}"
      )
    positions[-1] = max(positions[-1], min_y)

    # Floating point error sometimes puts a reported pair a fraction below its minimum spacing,
    # which the check further down would then refuse. Walk front to back and conform each pair
    # against what it reported, as legacy does.
    for channel in range(len(positions) - 2, -1, -1):
      spacing = self._min_spacing_between(channel, channel + 1)
      if positions[channel] - positions[channel + 1] < spacing:
        positions[channel] = positions[channel + 1] + spacing

    # check that the locations of channels after the move will respect pairwise minimum
    # spacing and be in descending order
    channel_locations = dict(enumerate(positions))

    for channel_idx, y in ys.items():
      channel_locations[channel_idx] = y

    if make_space:
      # For the channels to the back of `back_channel`, make sure the space between them
      # meets the per-pair minimum. We start with the channel closest to `back_channel`, and
      # make sure the channel behind it is spaced correctly, updating if needed.
      use_channels = list(ys.keys())
      back_channel = min(use_channels)
      for channel_idx in range(back_channel, 0, -1):
        pair_spacing = self._min_spacing_between(channel_idx - 1, channel_idx)
        if (channel_locations[channel_idx - 1] - channel_locations[channel_idx]) < pair_spacing:
          channel_locations[channel_idx - 1] = channel_locations[channel_idx] + pair_spacing

      # Position intermediate channels between back_channel and front_channel.
      front_channel = max(use_channels)
      for intermediate_ch in range(back_channel + 1, front_channel):
        if intermediate_ch not in ys:
          pair_spacing = self._min_spacing_between(intermediate_ch - 1, intermediate_ch)
          channel_locations[intermediate_ch] = channel_locations[intermediate_ch - 1] - pair_spacing

      # Similarly for the channels to the front of `front_channel`, make sure they are all
      # spaced by the per-pair minimum. This time, we iterate from back (closest to
      # `front_channel`) to the frontmost channel.
      for channel_idx in range(front_channel, self.num_channels - 1):
        pair_spacing = self._min_spacing_between(channel_idx, channel_idx + 1)
        if (channel_locations[channel_idx] - channel_locations[channel_idx + 1]) < pair_spacing:
          channel_locations[channel_idx + 1] = channel_locations[channel_idx] - pair_spacing

    # Quick checks before movement. The channels stay in order, so the two ends bound the rest.
    for channel in (0, self.num_channels - 1):
      self._check_reachable("y", channel_locations[channel])

    for i in range(len(channel_locations) - 1):
      required = self._min_spacing_between(i, i + 1)
      actual = channel_locations[i] - channel_locations[i + 1]
      if round(actual * 1000) < round(required * 1000):  # compare in um to avoid float issues
        raise ValueError(
          f"Channels {i} and {i + 1} must be at least {required}mm apart, "
          f"but are {actual:.2f}mm apart."
        )
    return channel_locations

  async def _move_to_planned_y_positions(self, channel_locations: Dict[int, float]):
    """Send every channel to its planned Y (`C0 JY`) and record where they are.

    Args:
      channel_locations: every channel's target in mm, from `_plan_y_positions`.
    """
    yp = " ".join([f"{round(y * 10):04}" for y in channel_locations.values()])
    try:
      resp = await self._driver.send_command(
        module="C0", command="JY", subsystem=_FirmwareLock.CHANNELS, yp=yp
      )
    except Exception:
      # Only on the way out: a move that arrives is recorded from its target below, so a `finally`
      # here would ask the device where the channels are on every successful move.
      await self._record_where_they_stopped("y")
      raise

    for channel, y in channel_locations.items():
      self.update_location_by_reference_point(channel, y=y)
    return resp

  async def move_to_y_positions(self, ys: Dict[int, float], make_space: bool = False):
    """Move channels along Y.

    The channels not named stay where they are.

    TODO: park the iSWAP first when one is installed. Legacy does, skipping the move when its
    flag says it is already parked; v1 tracks no such state and has no query for it.

    Args:
      ys: where to put each named channel, in mm, keyed by channel, 0-indexed from the back.
      make_space: whether the channels not named may be moved, so that every pair meets its
        minimum Y spacing and the channels stay in order back to front. Off by default: nothing
        moves that the caller did not ask to move, and a request that will not fit raises instead.
        It can raise either way, since the requested positions may leave no room.
    """
    return await self._move_to_planned_y_positions(await self._plan_y_positions(ys, make_space))

  async def move_to_y_position(self, channel: int, y: float, make_space: bool = False):
    """Move one channel along Y.

    The other channels stay where they are, unless `make_space` says they may move to let this
    one through.

    Args:
      channel: which channel to move, 0-indexed from the back.
      y: where to put it, in mm.
      make_space: whether the other channels may be moved to make room. Off by default. See
        `move_to_y_positions`.

    Raises:
      ValueError: If the channel cannot reach it, or the others cannot make room.
    """
    self._require_channel(channel)
    return await self.move_to_y_positions({channel: y}, make_space=make_space)

  async def make_max_space_for_channel(self, channel: int):
    """Spread the channels to leave one of them as much free Y as the arm allows.

    What a caller reaches for before working a channel by hand, and the device decides where the
    others go.

    TODO: park the iSWAP first when one is installed. Legacy does, skipping the move when its
    flag says it is already parked; v1 tracks no such state and has no query for it.

    Args:
      channel: which channel to free, 0-indexed from the back.

    Raises:
      ValueError: If the channel is not one this device has.
    """
    if not 0 <= channel < self.num_channels:
      raise ValueError(f"channel must be between 0 and {self.num_channels - 1}, is {channel}")
    try:
      resp = await self._driver.send_command(
        module="C0",
        command="JP",
        subsystem=_FirmwareLock.CHANNELS,
        pn=f"{channel + 1:02}",  # the firmware counts channels from 1
      )
    finally:
      # The device decides where the channels go, so unlike a commanded move there is nothing to
      # write the model from: where they ended up has to be read - and a move that stopped part
      # way has to be read for the same reason.
      await self._record_where_they_stopped("y")
    return resp

  # -- z position --------------------------------------------------------------------------------
  async def _unchecked_fw_request_lowest_z_positions(self) -> List[float]:
    """Read where every channel is along Z, without recording it.

    The reading alone. `request_tool_bottom_z_positions` is the one that also records it on the resources.

    Returns:
      The position of each channel in mm, by channel, 0-indexed from the back.
    """
    resp = await self._driver.send_command(module="C0", command="RZ", fmt="rz#### (n)")
    return [round(increments / 10, 1) for increments in cast(List[int], resp["rz"])]

  async def request_tool_bottom_z_positions(self) -> List[float]:
    """Read where the bottom of the tip on every channel is.

    Every channel has to carry one. Records each channel's stop disc on the resource modelling it.

    Returns:
      The bottom of each channel's tip in mm, by channel, 0-indexed from the back.

    Raises:
      ValueError: If any channel carries no tip.
    """
    await self._require_tips(range(self.num_channels), "request_stop_disc_z_positions")
    positions = await self._unchecked_fw_request_lowest_z_positions()
    # What comes back is each tip's bottom, but the model references stop discs, so we
    # read them for correct model update.
    await self.request_stop_disc_z_positions()
    return positions

  async def request_tool_bottom_z_position(self, channel: int) -> float:
    """Read where the bottom of the tip on one channel is.

    A channel with no tip has no tool bottom, so this refuses rather than quietly answering with
    its stop disc, which is what the master would do. `request_stop_disc_z_position` is the read
    that answers whatever is mounted.

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      Where the bottom of its tip is, in mm on the deck.

    Raises:
      ValueError: If the channel carries no tip.
    """
    self._require_channel(channel)
    await self._require_tips([channel], "request_stop_disc_z_position")
    tip_bottom = (await self._unchecked_fw_request_lowest_z_positions())[channel]
    # As above: the model holds this channel's stop disc, not the bottom of what is on it.
    await self.request_stop_disc_z_position(channel)
    return tip_bottom

  async def request_stop_disc_z_positions(self) -> List[float]:
    """Read where every channel's stop disc is.

    Returns:
      Each channel's stop disc in mm, by channel, 0-indexed from the back.
    """
    return [
      await self.request_stop_disc_z_position(channel) for channel in range(self.num_channels)
    ]

  async def request_stop_disc_z_position(self, channel: int) -> float:
    """Read where one channel's stop disc is, regardless of whether a tool (e.g. tip,
    core_gripper, suction_gripper, ...) is mounted.

    Records the answer on the resource modelling that channel.

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      Where its stop disc is, in mm on the deck.
    """
    self._require_channel(channel)
    resp = await self._driver.send_command(
      module=self.channel_id(channel), command="RZ", fmt="rz######"
    )
    z = self.configuration.z_drive_increments_to_mm(cast(int, resp["rz"]))
    self.update_location_by_reference_point(channel, z=z)
    return z

  async def request_tip_overhang(self, channel: int) -> float:
    """Measure how far the tip on one channel stands below its stop disc.

    Both readings are of the same channel at the same moment, so the difference is the overhang
    without anything having to move: the channel reports its own stop disc, the master reports the
    bottom of what is mounted. This is what a Z target has to be offset by for the tip end, rather
    than the stop disc, to land where it is wanted.

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      The overhang in mm.

    Raises:
      RuntimeError: If the channel carries no tip, so there is nothing to measure.
    """
    self._require_channel(channel)
    if not (await self.sense_tip_presence())[channel]:
      raise RuntimeError(f"channel {channel} reports no tip, so there is no overhang to measure")
    stop_disc = await self.request_stop_disc_z_position(channel)
    tip_bottom = (await self._unchecked_fw_request_lowest_z_positions())[channel]
    return round(stop_disc - tip_bottom, 2)

  async def _unchecked_fw_move_lowest_point_to_z_positions(self, zs: Dict[int, float]):
    """Move each channel's lowest point along Z, without checking or recording it.

    The command alone, as `_unchecked_fw_request_lowest_z_positions` is the read alone. What it
    positions is what the master takes Z to be: the bottom of the tip on a channel that carries
    one, the stop disc on a channel that does not. Which of the two depends on what is mounted, so
    this stays private and the moves that name their reference are what callers reach for.

    The command carries a position for every channel, so the ones not named are read first and sent
    back unchanged.

    Args:
      zs: where to put each named channel, in mm, keyed by channel, 0-indexed from the back.

    Returns:
      What the command answered.
    """
    positions = await self._unchecked_fw_request_lowest_z_positions()
    for channel, z in zs.items():
      positions[channel] = z
    return await self._driver.send_command(
      module="C0",
      command="JZ",
      subsystem=_FirmwareLock.CHANNELS,
      zp=[f"{round(z * 10):04}" for z in positions],
    )

  async def move_tool_bottom_to_z_positions(self, zs: Dict[int, float]):
    """Move the bottom of the tip on each named channel along Z, in one command.

    The master positions the bottom of what a channel carries, so this is that move with its
    reference made true: every named channel has to carry a tip, or the master would be placing a
    stop disc instead and calling it the same thing. The channels not named stay where they are.

    Args:
      zs: where to put each named channel's tip bottom, in mm, keyed by channel, 0-indexed from
        the back.

    Returns:
      What the command answered.

    Raises:
      ValueError: If a named channel is not one this device has, carries no tip, or is being sent
        outside the window the channels reach.
    """
    for channel in zs:
      self._require_channel(channel)
    await self._require_tips(zs, "move_stop_disc_to_z_positions")
    for z in zs.values():
      self._check_reachable("z", z)

    try:
      resp = await self._unchecked_fw_move_lowest_point_to_z_positions(zs)
    finally:
      # Whether the move arrived or stopped part way, where the channels are has to be read: this
      # is both how a successful move is recorded and how a failed one is.
      await self._record_where_they_stopped("z")
    return resp

  async def move_tool_bottom_to_z_position(
    self,
    channel: int,
    z: float,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the bottom of the tip on one channel along Z.

    The channel has to carry one. Needs the Z window, so run `star.setup()` first.

    Args:
      channel: which channel to move, 0-indexed from the back.
      z: where to put the bottom of its tip, in mm on the deck.
      speed: how fast, in mm/s. Defaults to `configuration.z_drive_speed_default`.
      acceleration: how hard, in mm/s2. Defaults to `configuration.z_drive_acceleration_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.z_drive_current_limit_default`.

    Raises:
      ValueError: If the channel carries no tip, or it cannot put the tip bottom at `z`.
    """
    self._require_channel(channel)
    c = self.configuration
    await self._require_tips([channel], "move_stop_disc_to_z_position")
    overhang = await self.request_tip_overhang(channel)

    # The drive works in stop-disc terms over `z_range`, so what the tip bottom reaches is that
    # window shifted down by the overhang, and no lower than a stop disc itself may go.
    low = round(max(c.z_range[0] - overhang, c.z_range[0]), 2)
    high = round(c.z_range[1] - overhang, 2)
    if not low <= z <= high:
      raise ValueError(
        f"the tool bottom reaches {low} to {high} mm with a {overhang} mm overhang, not {z}"
      )

    return await self.move_stop_disc_to_z_position(
      channel,
      z + overhang,
      speed=speed,
      acceleration=acceleration,
      current_limit=current_limit,
    )

  async def move_stop_disc_to_z_positions(
    self,
    zs: Dict[int, float],
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
  ):
    """Move each named channel's stop disc along Z, all together (`Px ZA` per channel).

    Every target is checked before any is sent. A channel that fails does not stop the others; the
    first failure is raised once every channel has been recorded. The channels not named stay.

    Args:
      zs: where to put each named channel's stop disc, in mm, keyed by channel, 0-indexed from the
        back.
      speed: how fast, in mm/s. Defaults to `default_z_speed`.
      acceleration: how hard, in mm/s2. Defaults to `default_z_acceleration`.
      current_limit: the motor current limit. Defaults to `default_z_current_limit`.

    Raises:
      ValueError: If a named channel is not one this device has, or an argument is outside what the
        drive accepts.
    """
    for channel, z in zs.items():
      self._require_channel(channel)
      self._check_reachable("z", z)
    results = await asyncio.gather(
      *(
        self.move_stop_disc_to_z_position(
          channel, z, speed=speed, acceleration=acceleration, current_limit=current_limit
        )
        for channel, z in zs.items()
      ),
      return_exceptions=True,
    )
    failed = [result for result in results if isinstance(result, BaseException)]
    if failed:
      raise failed[0]

  async def move_stop_disc_to_z_position(
    self,
    channel: int,
    z: float,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
  ):
    """Move one channel's stop disc along Z. The other channels stay where they are.

    Addressed to the channel rather than the master, so what it positions is the stop disc whether
    or not a tip is mounted. `move_tool_bottom_to_z_position` is the one that places a tip end.

    Args:
      channel: which channel to move, 0-indexed from the back.
      z: where to put its stop disc, in mm on the deck.
      speed: how fast, in mm/s. Defaults to `configuration.z_drive_speed_default`.
      acceleration: how hard, in mm/s2. Defaults to `configuration.z_drive_acceleration_default`.
      current_limit: the motor current limit. Defaults to
        `configuration.z_drive_current_limit_default`.

    Raises:
      ValueError: If an argument is outside what the drive accepts.
    """
    self._require_channel(channel)
    c = self.configuration
    speed = c.z_drive_speed_default if speed is None else speed
    acceleration = c.z_drive_acceleration_default if acceleration is None else acceleration
    current_limit = c.z_drive_current_limit_default if current_limit is None else current_limit

    self._check_reachable("z", z)
    for checked, (low, high), name in (
      (speed, c.z_speed_range, "speed"),
      (acceleration, c.z_acceleration_range, "acceleration"),
      (current_limit, c.z_drive_current_limit_range, "current_limit"),
    ):
      if not low <= checked <= high:
        raise ValueError(f"{name} must be between {low} and {high}, is {checked}")

    try:
      return await self._driver.send_command(
        module=self.channel_id(channel),
        command="ZA",
        za=f"{c.z_drive_mm_to_increments(z):05}",
        zv=f"{c.z_drive_mm_to_increments(speed):05}",
        zr=f"{c.z_drive_acceleration_mm_to_increments(acceleration):03}",
        zw=f"{current_limit:01}",
      )
    finally:
      # Whether the move succeeded or not: one that failed part way left the channel somewhere
      # neither position describes, and this read is also how a successful move is recorded.
      await self._record_where_they_stopped("z", [channel])

  async def probe_z_max(self) -> List[float]:
    """Raises single-channel pipettes to Z safety and reads their stop discs z-positions.

    Informs the max of `configuration.z_range` during setup.

    Returns:
      The z-positions of each channel's stop disc, in mm, by channel.
    """
    await self._driver.send_command(module="C0", command="ZA", subsystem=_FirmwareLock.CHANNELS)

    positions = await self.request_stop_disc_z_positions()
    # Only the bare channels are compared: with a tip or tool on, the drive rises to its very top.
    presence = await self.sense_tip_presence()
    bare = [z for channel, z in enumerate(positions) if not presence[channel]]
    if bare and max(bare) - min(bare) > self.configuration.z_drive_increments_to_mm(1):
      logger.warning("the channels came to rest at different heights: %s", positions)

    return positions

  async def move_to_safe_z(
    self, speed: Optional[float] = None, acceleration: Optional[float] = None
  ) -> None:
    """Raise every channel to Z safety together (`C0 ZA`), whatever is mounted.

    Args:
      speed: in mm/s, held by the drives for the move. The stored speed when None.
      acceleration: in mm/s2, held by the drives for the move. The stored one when None.
    """
    async with self._temporary_z_drive_profile(speed=speed, acceleration=acceleration):
      await self.probe_z_max()

  # -- x and y together ----------------------------------------------------------------------------

  async def _traverse_raise_targets(self, height: float) -> Dict[int, float]:
    """The stop disc target of every channel whose lowest point is below `height`, checked.

    A channel's lowest point is its tip bottom when it carries one, else its stop disc.

    Args:
      height: the height every lowest point has to reach, in mm.

    Returns:
      Each low channel's stop disc target in mm, keyed by channel; empty if none is low.

    Raises:
      ValueError: If a channel cannot raise its lowest point that high.
    """
    lowest = await self._unchecked_fw_request_lowest_z_positions()
    targets = {}
    for channel, z in enumerate(lowest):
      if z < height:
        stop_disc = await self.request_stop_disc_z_position(channel)
        targets[channel] = round(stop_disc + height - z, 2)
        self._check_reachable("z", targets[channel])
    return targets

  async def move_to_xy_positions(
    self,
    x: float,
    ys: Dict[int, float],
    *,
    make_space: bool = False,
    minimum_traverse_height_start: Optional[float] = None,
  ) -> None:
    """Move the channels across the deck: the low ones up first, then X and Y together.

    Everything is checked before anything moves. Every channel whose lowest point is below
    `minimum_traverse_height_start` is raised to it, all at once; then the arm (`X0 XP`) and the
    channels (`C0 JY`) travel at the same time.

    Args:
      x: where to send the arm, in mm. The channels share it.
      ys: where to put each named channel, in mm, keyed by channel, 0-indexed from the back.
      make_space: whether the channels not named may be moved in Y. See `move_to_y_positions`.
      minimum_traverse_height_start: the height to raise every low channel's lowest point to
        first, in mm. `default_minimum_traverse_height` when None; 0 raises nothing.

    Raises:
      ValueError: If X, a Y or a raise is out of reach, or two channels would stand too close.
    """
    height = (
      self.default_minimum_traverse_height
      if minimum_traverse_height_start is None
      else minimum_traverse_height_start
    )
    self._check_reachable("x", x)
    planned_ys = await self._plan_y_positions(ys, make_space)
    raises = await self._traverse_raise_targets(height) if height > 0 else {}

    if raises:
      await self.move_stop_disc_to_z_positions(raises)
    results = await asyncio.gather(
      self.move_to_x_position(x),
      self._move_to_planned_y_positions(planned_ys),
      return_exceptions=True,
    )
    failed = [result for result in results if isinstance(result, BaseException)]
    if failed:
      raise failed[0]

  # -- spreading -----------------------------------------------------------------------------------

  async def spread_channels(self):
    """Spread the channels evenly across the Y band. This moves them.

    One command with nothing to say where they go: the device spreads them itself, over the same
    band the initialization procedure uses, so a caller that wants particular positions reaches
    for `move_to_y_positions` instead.

    Collision risk: every channel travels in Y, so anything between them moves with them.

    Returns:
      What the device answered.
    """
    try:
      return await self._driver.send_command(
        module="C0", command="JE", subsystem=_FirmwareLock.CHANNELS
      )
    finally:
      # The device decides where they land, so unlike a commanded move there is nothing to write
      # the model from: where they ended up has to be read, and a spread that stopped part way
      # has to be read for the same reason.
      await self._record_where_they_stopped("y")

  # ----------------------------------------
  # Probing
  # ----------------------------------------

  def _found_nothing(self, error: STARFirmwareError, module: str) -> bool:
    """Whether a firmware error says only that a search reached its end without detecting.

    The master answers that with error 12, a channel with trace 70, or 73 when both its sensors
    searched.
    """
    return bool(error.errors) and all(
      isinstance(e, NoTeachInSignalError)
      if module == "C0"
      else e.raw_module == module and e.trace_information in (70, 73)
      for e in error.errors.values()
    )

  # -- x probing (capacitive only) --------------------------------------------------------------

  async def _unchecked_fw_probe_x_using_clld(self, end_position: float, read_timeout: int = 240):
    """Move the arm along X until the channel's cLLD triggers, or to the end, as given. `C0 XL`.

    Args:
      end_position: where the search ends, in mm, sent in 0.1 mm (`xs`).
      read_timeout: how long to wait for the answer, in s.
    """
    await self._driver.send_command(
      module="C0",
      command="XL",
      xs=f"{int(round(end_position * 10)):05}",
      read_timeout=read_timeout,
    )

  async def _diameter_that_probes(
    self,
    channel_idx: int,
    allow_without_tip: bool,
    tip_bottom_diameter: float,
    stop_disc_diameter: float,
  ) -> float:
    """What the channel would meet a surface with, in mm, refusing a bare channel unless allowed.

    A probe answers where the surface is, not where the channel stopped, so it has to know what did
    the touching: the tip on the channel, or the stop disc it would touch with bare.

    Args:
      channel_idx: the probing channel.
      allow_without_tip: whether a channel with no tip on it may probe.
      tip_bottom_diameter: diameter of the tip bottom in mm, when a tip is mounted.
      stop_disc_diameter: diameter of the stop disc in mm, when none is.

    Returns:
      The diameter of whichever of the two is on the channel, in mm.

    Raises:
      RuntimeError: If the channel holds no tip and `allow_without_tip` is False.
    """
    has_tip = bool((await self.sense_tip_presence())[channel_idx])
    if not has_tip and not allow_without_tip:
      raise RuntimeError(
        f"no tip on channel {channel_idx}; pass allow_without_tip=True to probe without one"
      )
    return tip_bottom_diameter if has_tip else stop_disc_diameter

  async def _overhang_that_probes(self, channel_idx: int, allow_without_tip: bool) -> float:
    """How far below the stop disc a channel probes, in mm, refusing a bare channel unless allowed.

    The Z searches run on the stop disc and answer for it, so a probe that speaks of the tip bottom
    offsets by the tip's overhang; a bare channel probes with the stop disc itself, overhang 0.

    Args:
      channel_idx: the probing channel.
      allow_without_tip: whether a channel with no tip on it may probe.

    Returns:
      The mounted tip's overhang in mm, to 0.1 mm, or 0.0 for a bare channel.

    Raises:
      RuntimeError: If the channel holds no tip and `allow_without_tip` is False.
    """
    if not (await self.sense_tip_presence())[channel_idx]:
      if not allow_without_tip:
        raise RuntimeError(
          f"no tip on channel {channel_idx}; pass allow_without_tip=True to probe without one"
        )
      return 0.0
    return round(await self.request_tip_overhang(channel_idx), 1)

  async def probe_x_using_clld(
    self,
    channel_idx: int,
    direction: Literal["left", "right"],
    *,
    search_end_position: Optional[float] = None,
    post_detection_distance: float = 2.0,
    tip_bottom_diameter: float = 1.2,
    stop_disc_diameter: float = 7.0,
    allow_without_tip: bool = False,
    read_timeout: int = 240,
  ) -> Optional[float]:
    """Probe a conductive surface along X with a channel's cLLD, from where the arm stands.

    Args:
      channel_idx: which channel, 0-indexed from the back.
      direction: "left" (decreasing x) or "right" (increasing x).
      search_end_position: where the search ends, in mm. The end of the reach in `direction` when
        None.
      post_detection_distance: how far to back away from the surface afterwards, in mm.
      tip_bottom_diameter: the tip's bottom diameter, in mm; half of it is added to the reading.
      stop_disc_diameter: the stop disc's, in mm, for a channel probing without a tip.
      allow_without_tip: whether to probe without a tip, on the stop disc. False requires one.
      read_timeout: how long to wait for the search, in s.

    Returns:
      The surface's X in mm, rounded to 0.1 mm, or None if the search found nothing.

    Raises:
      ValueError: If an argument is out of range, or the search end lies behind the arm.
      RuntimeError: If no configuration has been read, or the channel carries no tip and
        `allow_without_tip` is False.
    """
    self._require_channel(channel_idx)
    diameter = await self._diameter_that_probes(
      channel_idx, allow_without_tip, tip_bottom_diameter, stop_disc_diameter
    )
    device = self._driver.configuration
    if device is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    if direction not in ("left", "right"):
      raise ValueError(f"direction must be 'left' or 'right', is {direction!r}")
    if post_detection_distance < 0:
      raise ValueError(f"post_detection_distance must be 0 or more, is {post_detection_distance}")

    here = round(await self.request_x_position(), 1)
    # 95 mm up to 125 mm past the last track: the reach the search is allowed.
    low, high = 95.0, device.instrument_size_slots * 22.5 + 125.0
    if search_end_position is None:
      search_end_position = high if direction == "right" else low
    elif not low <= search_end_position <= high:
      raise ValueError(
        f"search_end_position must be between {low} and {high} mm, is {search_end_position}"
      )
    if direction == "right" and not here < search_end_position:
      raise ValueError(f"search_end_position={search_end_position} is not right of x={here}")
    if direction == "left" and not here > search_end_position:
      raise ValueError(f"search_end_position={search_end_position} is not left of x={here}")

    found = True
    try:
      await self._unchecked_fw_probe_x_using_clld(search_end_position, read_timeout=read_timeout)
    except STARFirmwareError as error:
      if not self._found_nothing(error, "C0"):
        raise
      found = False
    detected = round(await self.request_x_position(), 1)

    # Back away, so a carrier moved later does not drag against the tip.
    if direction == "left":
      await self.move_to_x_position(detected + post_detection_distance)
      surface = detected - diameter / 2
    else:
      await self.move_to_x_position(detected - post_detection_distance)
      surface = detected + diameter / 2
    return round(surface, 1) if found else None

  # -- y probing (capacitive only) --------------------------------------------------------------

  async def _unchecked_fw_probe_y_using_clld(
    self,
    channel: int,
    end_position: int,
    detection_edge: int,
    search_speed: int,
    acceleration_level: int,
    current_limit: int,
  ):
    """Move one channel along Y until its cLLD triggers, or to the end, as given. `Px YL`.

    Args:
      channel: 0-indexed from the back.
      end_position: where the search ends, in Y increments (`ya`).
      detection_edge: edge steepness on detection, 0 to 1023 (`gt`).
      search_speed: increments/s (`yv`).
      acceleration_level: 1 to 4, each `level * 5000` increments/s2 (`yr`).
      current_limit: 0 to 7 (`yw`).
    """
    await self._driver.send_command(
      module=self.channel_id(channel),
      command="YL",
      ya=f"{end_position:05}",
      gt=f"{detection_edge:04}",
      gl=f"{0:04}",  # no offset after the edge, so it stops where it detected
      yv=f"{search_speed:04}",
      yr=f"{acceleration_level}",
      yw=f"{current_limit}",
      read_timeout=120,
    )

  async def probe_y_using_clld(
    self,
    channel_idx: int,
    direction: Literal["forward", "backward"],
    *,
    search_start_position: Optional[float] = None,
    search_end_position: Optional[float] = None,
    search_speed: float = 10.0,
    acceleration_level: int = 4,
    detection_edge: int = 10,
    current_limit: int = 7,
    post_detection_distance: float = 2.0,
    tip_bottom_diameter: float = 1.2,
    stop_disc_diameter: float = 7.0,
    allow_without_tip: bool = False,
  ) -> Optional[float]:
    """Probe a conductive surface along Y with a channel's cLLD, never past its neighbours.

    Args:
      channel_idx: which channel, 0-indexed from the back.
      direction: "forward" (decreasing y) or "backward" (increasing y).
      search_start_position: where to search from, in mm. Where the channel stands when None.
      search_end_position: where the search ends, in mm. As far as the neighbour allows when None.
      search_speed: in mm/s.
      acceleration_level: 1 to 4, each `level * 5000` increments/s2.
      detection_edge: cLLD edge steepness, 0 to 1023.
      current_limit: the Y drive's current limit, 0 to 7.
      post_detection_distance: how far to back away from the surface afterwards, in mm; less if
        the neighbour is closer.
      tip_bottom_diameter: the tip's bottom diameter, in mm; half of it is added to the reading.
        1.2 is the teaching needle's.
      stop_disc_diameter: the stop disc's, in mm, for a channel probing without a tip.
      allow_without_tip: whether to probe without a tip, on the stop disc. False requires one.

    Returns:
      The surface's Y in mm, rounded to 0.1 mm, or None if the search found nothing.

    Raises:
      ValueError: If an argument is out of range, or the search end lies behind the channel.
      RuntimeError: If no configuration has been read, or the channel carries no tip and
        `allow_without_tip` is False.
    """
    self._require_channel(channel_idx)
    diameter = await self._diameter_that_probes(
      channel_idx, allow_without_tip, tip_bottom_diameter, stop_disc_diameter
    )
    c = self.configuration
    device = self._driver.configuration
    if device is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    if direction not in ("forward", "backward"):
      raise ValueError(f"direction must be 'forward' or 'backward', is {direction!r}")

    # What the channel may reach without meeting a neighbour
    ys = await self.request_y_positions()
    if channel_idx > 0:
      high = ys[channel_idx - 1] - self._min_spacing_between(channel_idx, channel_idx - 1)
    else:
      high = device.pip_maximal_y_position
    if channel_idx < self.num_channels - 1:
      low = ys[channel_idx + 1] + self._min_spacing_between(channel_idx, channel_idx + 1)
    elif self.arm.side == "left":
      low = device.left_arm_min_y_position
    else:
      low = device.right_arm_min_y_position
    for name, value in (
      ("search_start_position", search_start_position),
      ("search_end_position", search_end_position),
    ):
      if value is not None and not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high} mm, is {value}")

    if search_start_position is not None:
      await self.move_to_y_position(channel_idx, search_start_position)
    here = await self.request_y_position(channel_idx)
    if direction == "backward":
      end = high if search_end_position is None else search_end_position
      if end < here:
        raise ValueError(f"channel {channel_idx} cannot search backward from {here} to {end} mm")
    else:
      end = low if search_end_position is None else search_end_position
      if end > here:
        raise ValueError(f"channel {channel_idx} cannot search forward from {here} to {end} mm")

    end_increments = c.y_drive_mm_to_increments(end)
    speed_increments = c.y_drive_mm_to_increments(search_speed)
    for checked, (lowest, highest), name in (
      (end_increments, c.y_range_increments, "search end, in increments,"),
      (speed_increments, c.y_drive_speed_range_increments, "search_speed, in increments/s,"),
      (acceleration_level, c.y_drive_acceleration_level_range, "acceleration_level"),
      (detection_edge, c.clld_detection_edge_range, "detection_edge"),
      (current_limit, c.y_drive_current_limit_range, "current_limit"),
    ):
      if not lowest <= checked <= highest:
        raise ValueError(f"{name} must be between {lowest} and {highest}, is {checked}")

    found = True
    try:
      await self._unchecked_fw_probe_y_using_clld(
        channel_idx,
        end_position=end_increments,
        detection_edge=detection_edge,
        search_speed=speed_increments,
        acceleration_level=acceleration_level,
        current_limit=current_limit,
      )
    except STARFirmwareError as error:
      if not self._found_nothing(error, self.channel_id(channel_idx)):
        raise
      found = False
    detected = await self.request_y_position(channel_idx)

    # Back away from the surface, no further than the neighbour behind the move allows.
    if direction == "backward":
      await self.move_to_y_position(
        channel_idx, detected - min(post_detection_distance, detected - low)
      )
      surface = detected + diameter / 2
    else:
      await self.move_to_y_position(
        channel_idx, detected + min(post_detection_distance, high - detected)
      )
      surface = detected - diameter / 2
    return round(surface, 1) if found else None

  # -- z probing (capacitive, pressure, force) --------------------------------------------------

  async def _unchecked_fw_probe_z_using_clld(
    self,
    channel: int,
    end_position: int,
    start_position: int,
    search_speed: int,
    acceleration: int,
    detection_edge: int,
    detection_drop: int,
    post_detection_trajectory: int,
    post_detection_distance: int,
  ):
    """Lower one channel until its cLLD triggers, as given, in Z increments. `Px ZL`.

    Args:
      channel: 0-indexed from the back.
      end_position: stop disc height it goes no lower than (`zh`).
      start_position: stop disc height the search starts from (`zc`).
      search_speed: increments/s (`zl`).
      acceleration: thousands of increments/s2 (`zr`).
      detection_edge: edge steepness on detection, 0 to 1023 (`gt`).
      detection_drop: offset after the edge, 0 to 1023 (`gl`).
      post_detection_trajectory: 0 moves down after detection, 1 up (`zj`).
      post_detection_distance: how far it moves after detection (`zi`).
    """
    await self._driver.send_command(
      module=self.channel_id(channel),
      command="ZL",
      zh=f"{end_position:05}",
      zc=f"{start_position:05}",
      zl=f"{search_speed:05}",
      zr=f"{acceleration:03}",
      gt=f"{detection_edge:04}",
      gl=f"{detection_drop:04}",
      zj=post_detection_trajectory,
      zi=f"{post_detection_distance:04}",
    )

  async def request_last_lld_z_positions(self) -> List[float]:
    """Request where each channel last detected liquid, by cLLD or pLLD. `C0 RL`.

    Returns:
      The tip bottom's Z position at detection, in mm on the deck, by channel.
    """
    resp = await self._driver.send_command(module="C0", command="RL", fmt="lh#### (n)")
    return [round(increments / 10, 1) for increments in cast(List[int], resp["lh"])]

  async def _clld_search(
    self,
    channel: int,
    end_position: float,
    start_position: float,
    search_speed: float = 10.0,
    acceleration: float = 800.0,
    detection_edge: int = 10,
    detection_drop: int = 2,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_distance: float = 0.0,
  ) -> None:
    """Run one channel's cLLD search between two stop disc heights, every field checked.

    In stop disc terms: a caller that thinks in tip bottoms adds the overhang first. Nothing is
    sensed here, so the caller makes sure a tip is on. A search that finds nothing raises as the
    channel answers it.

    Args:
      channel: 0-indexed from the back.
      end_position: stop disc height it goes no lower than, in mm.
      start_position: stop disc height the search starts from, in mm.
      search_speed: in mm/s.
      acceleration: in mm/s2.
      detection_edge: cLLD edge steepness, 0 to 1023.
      detection_drop: offset after the edge, 0 to 1023.
      post_detection_trajectory: 0 moves down after detection, 1 up.
      post_detection_distance: how far it moves after detection, in mm; 0 stays there.

    Raises:
      ValueError: If a field is out of the drive's range.
      STARFirmwareError: As the channel answers, a search that found nothing included.
    """
    c = self.configuration
    if post_detection_trajectory not in (0, 1):
      raise ValueError(f"post_detection_trajectory must be 0 or 1, is {post_detection_trajectory}")
    end = c.z_drive_mm_to_increments(end_position)
    start = c.z_drive_mm_to_increments(start_position)
    speed = c.z_drive_mm_to_increments(search_speed)
    ramp = c.z_drive_acceleration_mm_to_increments(acceleration)
    distance = c.z_drive_mm_to_increments(post_detection_distance)
    for checked, (low, high), name in (
      (end, c.z_range_increments, "search end, in increments,"),
      (start, c.z_range_increments, "search start, in increments,"),
      (speed, c.z_drive_speed_range_increments, "search_speed, in increments/s,"),
      (ramp, c.z_drive_acceleration_range_increments, "acceleration, in 1000 increments/s2,"),
      (detection_edge, c.clld_detection_edge_range, "detection_edge"),
      (detection_drop, c.clld_detection_drop_range, "detection_drop"),
      (
        distance,
        c.lld_post_detection_distance_range_increments,
        "post_detection_distance, in increments,",
      ),
    ):
      if not low <= checked <= high:
        raise ValueError(f"{name} must be between {low} and {high}, is {checked}")
    await self._unchecked_fw_probe_z_using_clld(
      channel,
      end_position=end,
      start_position=start,
      search_speed=speed,
      acceleration=ramp,
      detection_edge=detection_edge,
      detection_drop=detection_drop,
      post_detection_trajectory=post_detection_trajectory,
      post_detection_distance=distance,
    )

  async def probe_z_using_clld(
    self,
    channel_idx: int,
    *,
    search_start_position: Optional[float] = None,
    search_end_position: Optional[float] = None,
    search_speed: float = 10.0,
    acceleration: float = 800.0,
    detection_edge: int = 10,
    detection_drop: int = 2,
    post_detection_trajectory: Literal[0, 1] = 1,
    allow_without_tip: bool = False,
    post_detection_distance: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
  ) -> Optional[float]:
    """Lower a channel's tip until its cLLD triggers, and read the height it detected at.

    On a firmware error the channels go to Z safety first, then it is raised or, for a search
    that found nothing, None returned.

    Args:
      channel_idx: which channel, 0-indexed from the back.
      search_start_position: tip bottom height to search from, in mm. As high as the tip goes
        when None.
      search_end_position: lowest tip bottom height, in mm. The drive's floor when None.
      search_speed: in mm/s.
      acceleration: in mm/s2.
      detection_edge: cLLD edge steepness, 0 to 1023.
      detection_drop: offset after the edge, 0 to 1023.
      post_detection_trajectory: 0 moves down after detection, 1 up.
      allow_without_tip: whether to probe without a tip, on the stop disc. False requires one.
      post_detection_distance: how far it moves after detection, in mm.
      move_channels_to_safe_pos_after: whether to raise every channel to Z safety afterwards,
        instead of resting where the search left it.

    Returns:
      The height the channel detected at, in mm, or None if the search found nothing.

    Raises:
      RuntimeError: If the channel carries no tip and `allow_without_tip` is False.
      ValueError: If an argument is out of range.
    """
    self._require_channel(channel_idx)
    # The search runs on the stop disc, which sits the overhang above the tip bottom.
    overhang = await self._overhang_that_probes(channel_idx, allow_without_tip)
    c = self.configuration
    lowest, highest = (c.z_drive_increments_to_mm(i) for i in c.z_range_increments)
    top, floor = highest - overhang, round(lowest - overhang, 2)
    if search_start_position is None:
      search_start_position = top
    if search_end_position is None:
      search_end_position = floor
    if search_end_position < floor:
      raise ValueError(f"search_end_position must be at least {floor} mm, is {search_end_position}")
    if not search_end_position <= search_start_position <= top:
      raise ValueError(
        f"search_start_position must be between {search_end_position} and {top} mm, "
        f"is {search_start_position}"
      )
    try:
      await self._clld_search(
        channel_idx,
        search_end_position + overhang,
        round(search_start_position + overhang, 2),
        search_speed=search_speed,
        acceleration=acceleration,
        detection_edge=detection_edge,
        detection_drop=detection_drop,
        post_detection_trajectory=post_detection_trajectory,
        post_detection_distance=post_detection_distance,
      )
    except STARFirmwareError as error:
      await self.move_to_safe_z()
      if not self._found_nothing(error, self.channel_id(channel_idx)):
        raise
      return None
    if move_channels_to_safe_pos_after:
      await self.move_to_safe_z()
    return (await self.request_last_lld_z_positions())[channel_idx]

  async def _unchecked_fw_probe_z_using_plld(
    self,
    channel: int,
    end_position: int,
    start_position: int,
    post_detection_distance: int,
    post_detection_trajectory: int,
    tip_has_filter: bool,
    clld_detection_edge: int,
    clld_detection_drop: int,
    plld_detection_edge: int,
    plld_detection_drop: int,
    clld_verification: bool,
    max_delta_plld_clld: int,
    mode: int,
    foam_detection_drop: int,
    foam_detection_edge_tolerance: int,
    foam_ad_values: int,
    foam_search_speed: int,
    dispense_back_mode: int,
    dispense_back_volume: int,
    approach_speed: int,
    search_speed: int,
    acceleration: int,
    z_current_limit: int,
    dispensing_speed: int,
    dispensing_acceleration: int,
    dispensing_max_speed: int,
    dispensing_current_limit: int,
    read_timeout: int = 120,
  ) -> List[int]:
    """Lower one channel until its pressure sensor meets a surface, as given. `Px ZE`.

    Args:
      channel: 0-indexed from the back.
      end_position: stop disc height it goes no lower than (`zh`).
      start_position: stop disc height the search starts from (`zc`).
      post_detection_distance: how far it moves after detection (`zi`).
      post_detection_trajectory: 0 moves down after detection, 1 up (`zj`).
      tip_has_filter: whether the tip has a filter (`gf`).
      clld_detection_edge: cLLD edge steepness, 0 to 1023 (`gt`).
      clld_detection_drop: offset after the cLLD edge, 0 to 1023 (`gl`).
      plld_detection_edge: pLLD edge steepness, 0 to 1023 (`gu`).
      plld_detection_drop: offset after the pLLD edge, 0 to 1023 (`gn`).
      clld_verification: whether the cLLD searches alongside to verify (`gm`).
      max_delta_plld_clld: how far the two detections may differ, in increments (`gz`).
      mode: 0 stops at the liquid, 1 at the foam and then the liquid (`cj`).
      foam_detection_drop: foam detection drop, 0 to 1023 (`co`).
      foam_detection_edge_tolerance: foam edge tolerance, 0 to 1023 (`cp`).
      foam_ad_values: foam AD values, 0 to 4999 (`cq`).
      foam_search_speed: search speed through the foam, increments/s (`cl`).
      dispense_back_mode: 1 dispenses `dispense_back_volume` back after detection, 0 not (`cc`).
      dispense_back_volume: in dispensing drive increments (`cd`).
      approach_speed: speed above the start position, increments/s (`zv`).
      search_speed: increments/s (`zl`).
      acceleration: thousands of increments/s2 (`zr`).
      z_current_limit: Z drive current limit, 0 to 7 (`zw`).
      dispensing_speed: dispensing drive speed, increments/s (`dl`).
      dispensing_acceleration: dispensing drive acceleration, increments/s2 (`dr`).
      dispensing_max_speed: dispensing drive top speed, increments/s (`dv`).
      dispensing_current_limit: dispensing drive current limit, 0 to 7 (`dw`).
      read_timeout: how long to wait for the answer, in s. A search can take over 30 s.

    Returns:
      The stop disc heights it detected at, in Z increments (`if`).
    """
    resp = await self._driver.send_command(
      module=self.channel_id(channel),
      command="ZE",
      zh=f"{end_position:05}",
      zc=f"{start_position:05}",
      zi=f"{post_detection_distance:04}",
      zj=f"{post_detection_trajectory:01}",
      gf=str(int(tip_has_filter)),
      gt=f"{clld_detection_edge:04}",
      gl=f"{clld_detection_drop:04}",
      gu=f"{plld_detection_edge:04}",
      gn=f"{plld_detection_drop:04}",
      gm=str(int(clld_verification)),
      gz=f"{max_delta_plld_clld:04}",
      cj=str(mode),
      co=f"{foam_detection_drop:04}",
      cp=f"{foam_detection_edge_tolerance:04}",
      cq=f"{foam_ad_values:04}",
      cl=f"{foam_search_speed:05}",
      cc=str(dispense_back_mode),
      cd=f"{dispense_back_volume:05}",
      zv=f"{approach_speed:05}",
      zl=f"{search_speed:05}",
      zr=f"{acceleration:03}",
      zw=f"{z_current_limit}",
      dl=f"{dispensing_speed:05}",
      dr=f"{dispensing_acceleration:03}",
      dv=f"{dispensing_max_speed:05}",
      dw=f"{dispensing_current_limit}",
      fmt="if##### (n)",
      read_timeout=read_timeout,
    )
    return cast(List[int], resp["if"])

  async def _plld_search(
    self,
    channel: int,
    end_position: float,
    start_position: float,
    *,
    approach_speed: float = 120.0,
    search_speed: float = 10.0,
    acceleration: float = 800.0,
    z_current_limit: Optional[int] = None,
    tip_has_filter: Optional[bool] = None,
    dispensing_speed: float = 5.0,
    dispensing_acceleration: float = 0.2,
    dispensing_max_speed: float = 14.5,
    dispensing_current_limit: int = 3,
    detection_edge: int = 30,
    detection_drop: int = 10,
    clld_verification: bool = False,
    clld_detection_edge: int = 10,
    clld_detection_drop: int = 2,
    max_delta_plld_clld: float = 5.0,
    mode: Optional["Pipettes.PressureLLDMode"] = None,
    foam_detection_drop: int = 30,
    foam_detection_edge_tolerance: int = 30,
    foam_ad_values: int = 30,
    foam_search_speed: float = 10.0,
    dispense_back_volume: Optional[float] = None,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_distance: float = 0.0,
    read_timeout: int = 120,
  ) -> List[float]:
    """Run one channel's pressure search between two stop disc heights, every field checked.

    The channel comes down at `approach_speed` to the start, then searches at `search_speed` with
    its dispensing drive drawing on the tip until the pressure says the tip met a surface. With
    `clld_verification` the capacitive sensor searches alongside and the two detections must agree
    within `max_delta_plld_clld`. In foam mode the search goes on through the foam to the liquid
    under it. As `_clld_search`: in stop disc terms, and nothing sensed here.

    Args:
      channel: 0-indexed from the back.
      end_position: stop disc height it goes no lower than, in mm.
      start_position: stop disc height the search starts from, in mm.
      approach_speed: above the start position, in mm/s.
      search_speed: in mm/s.
      acceleration: in mm/s2.
      z_current_limit: Z drive current limit, 0 to 7.
        `configuration.z_drive_current_limit_default` when None.
      tip_has_filter: whether the tip has a filter. What the model says of the mounted tip when
        None, and no filter if the model has none.
      dispensing_speed: of the dispensing drive during the search, in mm/s.
      dispensing_acceleration: in mm/s2.
      dispensing_max_speed: in mm/s.
      dispensing_current_limit: 0 to 7.
      detection_edge: pLLD edge steepness, 0 to 1023.
      detection_drop: offset after the pLLD edge, 0 to 1023.
      clld_verification: whether the cLLD searches alongside to verify.
      clld_detection_edge: cLLD edge steepness, 0 to 1023.
      clld_detection_drop: offset after the cLLD edge, 0 to 1023.
      max_delta_plld_clld: how far the two detections may differ, in mm.
      mode: what the search stops at. The liquid when None.
      foam_detection_drop: 0 to 1023.
      foam_detection_edge_tolerance: 0 to 1023.
      foam_ad_values: 0 to 4999.
      foam_search_speed: through the foam, in mm/s.
      dispense_back_volume: dispensed back after detection, in uL. Nothing when None.
      post_detection_trajectory: 0 moves down after detection, 1 up.
      post_detection_distance: how far it moves after detection, in mm; 0 stays there.
      read_timeout: how long to wait for the search, in s.

    Returns:
      The stop disc heights it detected at, in mm: one, or in foam mode the foam's and then the
      liquid's.

    Raises:
      ValueError: If a field is out of the drive's range.
      STARFirmwareError: As the channel answers, a search that found nothing included.
    """
    c = self.configuration
    if mode is None:
      mode = self.PressureLLDMode.LIQUID
    if post_detection_trajectory not in (0, 1):
      raise ValueError(f"post_detection_trajectory must be 0 or 1, is {post_detection_trajectory}")
    if z_current_limit is None:
      z_current_limit = c.z_drive_current_limit_default
    if tip_has_filter is None:
      tip = self.get_mounted_tip(channel)
      tip_has_filter = tip is not None and tip.has_filter
    end = c.z_drive_mm_to_increments(end_position)
    start = c.z_drive_mm_to_increments(start_position)
    approach = c.z_drive_mm_to_increments(approach_speed)
    speed = c.z_drive_mm_to_increments(search_speed)
    ramp = c.z_drive_acceleration_mm_to_increments(acceleration)
    distance = c.z_drive_mm_to_increments(post_detection_distance)
    delta = c.z_drive_mm_to_increments(max_delta_plld_clld)
    foam_speed = c.z_drive_mm_to_increments(foam_search_speed)
    d_speed = c.dispensing_drive_mm_to_increments(dispensing_speed)
    d_ramp = c.dispensing_drive_mm_to_increments(dispensing_acceleration)
    d_max = c.dispensing_drive_mm_to_increments(dispensing_max_speed)
    back = (
      0
      if dispense_back_volume is None
      else c.dispensing_drive_uL_to_increments(dispense_back_volume)
    )
    for checked, (low, high), name in (
      (end, c.z_range_increments, "search end, in increments,"),
      (start, c.z_range_increments, "search start, in increments,"),
      (approach, c.z_drive_speed_range_increments, "approach_speed, in increments/s,"),
      (speed, c.z_drive_speed_range_increments, "search_speed, in increments/s,"),
      (ramp, c.z_drive_acceleration_range_increments, "acceleration, in 1000 increments/s2,"),
      (z_current_limit, c.z_drive_current_limit_range, "z_current_limit"),
      (d_speed, c.dispensing_drive_speed_range_increments, "dispensing_speed, in increments/s,"),
      (
        d_ramp,
        c.dispensing_drive_acceleration_range_increments,
        "dispensing_acceleration, in increments/s2,",
      ),
      (d_max, c.dispensing_drive_speed_range_increments, "dispensing_max_speed, in increments/s,"),
      (
        dispensing_current_limit,
        c.dispensing_drive_current_limit_range,
        "dispensing_current_limit",
      ),
      (detection_edge, c.plld_detection_edge_range, "detection_edge"),
      (detection_drop, c.plld_detection_drop_range, "detection_drop"),
      (clld_detection_edge, c.clld_detection_edge_range, "clld_detection_edge"),
      (clld_detection_drop, c.clld_detection_drop_range, "clld_detection_drop"),
      (delta, c.lld_max_delta_range_increments, "max_delta_plld_clld, in increments,"),
      (foam_detection_drop, c.plld_foam_detection_drop_range, "foam_detection_drop"),
      (
        foam_detection_edge_tolerance,
        c.plld_foam_detection_edge_tolerance_range,
        "foam_detection_edge_tolerance",
      ),
      (foam_ad_values, c.plld_foam_ad_values_range, "foam_ad_values"),
      (
        foam_speed,
        c.plld_foam_search_speed_range_increments,
        "foam_search_speed, in increments/s,",
      ),
      (back, c.dispensing_drive_volume_range_increments, "dispense_back_volume, in increments,"),
      (
        distance,
        c.lld_post_detection_distance_range_increments,
        "post_detection_distance, in increments,",
      ),
    ):
      if not low <= checked <= high:
        raise ValueError(f"{name} must be between {low} and {high}, is {checked}")
    found = await self._unchecked_fw_probe_z_using_plld(
      channel,
      end_position=end,
      start_position=start,
      post_detection_distance=distance,
      post_detection_trajectory=post_detection_trajectory,
      tip_has_filter=tip_has_filter,
      clld_detection_edge=clld_detection_edge,
      clld_detection_drop=clld_detection_drop,
      plld_detection_edge=detection_edge,
      plld_detection_drop=detection_drop,
      clld_verification=clld_verification,
      max_delta_plld_clld=delta,
      mode=mode.value,
      foam_detection_drop=foam_detection_drop,
      foam_detection_edge_tolerance=foam_detection_edge_tolerance,
      foam_ad_values=foam_ad_values,
      foam_search_speed=foam_speed,
      dispense_back_mode=0 if dispense_back_volume is None else 1,
      dispense_back_volume=back,
      approach_speed=approach,
      search_speed=speed,
      acceleration=ramp,
      z_current_limit=z_current_limit,
      dispensing_speed=d_speed,
      dispensing_acceleration=d_ramp,
      dispensing_max_speed=d_max,
      dispensing_current_limit=dispensing_current_limit,
      read_timeout=read_timeout,
    )
    wanted = 2 if mode == self.PressureLLDMode.FOAM else 1
    return [c.z_drive_increments_to_mm(increments) for increments in found[:wanted]]

  async def probe_z_using_plld(
    self,
    channel_idx: int,
    *,
    search_start_position: Optional[float] = None,
    search_end_position: Optional[float] = None,
    pressure_mode: Optional["Pipettes.PressureLLDMode"] = None,
    allow_without_tip: bool = False,
    post_detection_distance: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
    **search: Any,
  ) -> Optional[List[float]]:
    """Lower a channel's tip until the pressure says it met the liquid, and say how high that is.

    The pressure counterpart of `probe_z_using_clld`: a tip sensed on, the overhang measured, the
    window in tip bottom terms, then the search. Every other setting of the search, speeds,
    thresholds, the capacitive verification, foam and dispense-back, is passed on to
    `_plld_search` by name and takes its default there.

    Args:
      channel_idx: which channel, 0-indexed from the back.
      search_start_position: tip bottom height to search from, in mm. As high as the tip goes
        when None.
      search_end_position: lowest tip bottom height, in mm. The drive's floor when None.
      pressure_mode: what the search stops at. The liquid when None.
      allow_without_tip: whether to probe without a tip, on the stop disc. False requires one.
      post_detection_distance: how far it moves after detection, in mm.
      move_channels_to_safe_pos_after: whether to raise every channel to Z safety afterwards,
        instead of resting where the search left it.
      search: the rest of `_plld_search`'s settings, by name.

    Returns:
      The tip bottom heights detected, in mm: the liquid's, or in foam mode the foam's and then
      the liquid's. None if the search found nothing.

    Raises:
      RuntimeError: If the channel carries no tip and `allow_without_tip` is False.
      ValueError: If an argument is out of range.
    """
    self._require_channel(channel_idx)
    overhang = await self._overhang_that_probes(channel_idx, allow_without_tip)
    c = self.configuration
    lowest, highest = (c.z_drive_increments_to_mm(i) for i in c.z_range_increments)
    top, floor = highest - overhang, round(lowest - overhang, 2)
    if search_start_position is None:
      search_start_position = top
    if search_end_position is None:
      search_end_position = floor
    if search_end_position < floor:
      raise ValueError(f"search_end_position must be at least {floor} mm, is {search_end_position}")
    if not search_end_position <= search_start_position <= top:
      raise ValueError(
        f"search_start_position must be between {search_end_position} and {top} mm, "
        f"is {search_start_position}"
      )
    try:
      detected = await self._plld_search(
        channel_idx,
        search_end_position + overhang,
        round(search_start_position + overhang, 2),
        mode=pressure_mode,
        post_detection_distance=post_detection_distance,
        **search,
      )
    except STARFirmwareError as error:
      await self.move_to_safe_z()
      if not self._found_nothing(error, self.channel_id(channel_idx)):
        raise
      return None
    if move_channels_to_safe_pos_after:
      await self.move_to_safe_z()
    return [round(stop_disc - overhang, 2) for stop_disc in detected]

  async def _unchecked_fw_probe_z_using_ztouch(
    self,
    channel: int,
    start_position: int,
    end_position: int,
    search_speed: int,
    approach_speed: int,
    acceleration: int,
    detection_limiter_pwm: int,
    push_force_pwm: int,
  ) -> int:
    """Send the z-touch search as given, in Z increments; the stop disc where it stopped. `Px ZH`.

    Args:
      channel: 0-indexed from the back.
      start_position: stop disc height the search starts from (`zb`).
      end_position: stop disc height it goes no lower than (`za`).
      search_speed: search speed, increments/s (`zu`).
      approach_speed: speed to the start, increments/s (`zv`).
      acceleration: thousands of increments/s2 (`zr`).
      detection_limiter_pwm: offset PWM limiter for the search, 0 to 125 (`cg`).
      push_force_pwm: offset PWM push-down force, 0 to 125; 0 switches the drive off (`cf`).

    Returns:
      The stop disc's height where the search stopped, in Z increments (`rz`).
    """
    resp = await self._driver.send_command(
      module=self.channel_id(channel),
      command="ZH",
      zb=f"{start_position:05}",
      za=f"{end_position:05}",
      zv=f"{approach_speed:05}",
      zr=f"{acceleration:03}",
      zu=f"{search_speed:05}",
      cg=f"{detection_limiter_pwm:03}",
      cf=f"{push_force_pwm:03}",
      fmt="rz#####",
    )
    return cast(int, resp["rz"])

  async def _ztouch_search(
    self,
    channel: int,
    end_position: float,
    start_position: float,
    *,
    search_speed: float = 10.0,
    approach_speed: float = 125.0,
    acceleration: float = 800.0,
    detection_limiter_pwm: int = 1,
    push_force_pwm: int = 0,
  ) -> float:
    """Run one channel's z-touch search between two stop disc heights, every field checked.

    As `_clld_search`: in stop disc terms, nothing sensed here, and where the channel stopped is
    not recorded in the model, which the caller does once it has read the answer.

    Args:
      channel: 0-indexed from the back.
      end_position: stop disc height it goes no lower than, in mm.
      start_position: stop disc height the search starts from, in mm.
      search_speed: in mm/s.
      approach_speed: down to the start, in mm/s.
      acceleration: in mm/s2.
      detection_limiter_pwm: the force at which the search stops, 0 to 125.
      push_force_pwm: the push-down force once stopped, 0 to 125; 0 switches the drive off.

    Returns:
      The stop disc height where the search stopped, in mm.

    Raises:
      ValueError: If a field is out of the drive's range.
      STARFirmwareError: As the channel answers.
    """
    c = self.configuration
    end = c.z_drive_mm_to_increments(end_position)
    start = c.z_drive_mm_to_increments(start_position)
    speed = c.z_drive_mm_to_increments(search_speed)
    approach = c.z_drive_mm_to_increments(approach_speed)
    ramp = c.z_drive_acceleration_mm_to_increments(acceleration)
    for checked, (low, high), name in (
      (end, c.z_range_increments, "search end, in increments,"),
      (start, c.z_range_increments, "search start, in increments,"),
      (speed, c.z_drive_speed_range_increments, "search_speed, in increments/s,"),
      (approach, c.z_drive_speed_range_increments, "approach_speed, in increments/s,"),
      (ramp, c.z_drive_acceleration_range_increments, "acceleration, in 1000 increments/s2,"),
      (detection_limiter_pwm, c.z_touch_pwm_range, "detection_limiter_pwm"),
      (push_force_pwm, c.z_touch_pwm_range, "push_force_pwm"),
    ):
      if not low <= checked <= high:
        raise ValueError(f"{name} must be between {low} and {high}, is {checked}")
    stopped_at = await self._unchecked_fw_probe_z_using_ztouch(
      channel,
      start_position=start,
      end_position=end,
      search_speed=speed,
      approach_speed=approach,
      acceleration=ramp,
      detection_limiter_pwm=detection_limiter_pwm,
      push_force_pwm=push_force_pwm,
    )
    return c.z_drive_increments_to_mm(stopped_at)

  def _require_ztouch_firmware(self, channel: int) -> None:
    """Raise unless the channel's firmware, as discovery recorded it, is from 2022 on."""
    version = self.configuration.channels[channel].firmware_version
    if version is None:
      raise RuntimeError(f"channel {channel} has no firmware version recorded; run setup first")
    if parse_firmware_version_date(version).year < 2022:
      raise RuntimeError(f"channel {channel} runs {version}; z-touch needs firmware from 2022")

  async def probe_z_using_ztouch(
    self,
    channel_idx: int,
    *,
    search_start_position: Optional[float] = None,
    search_end_position: Optional[float] = None,
    search_speed: float = 10.0,
    approach_speed: float = 125.0,
    acceleration: float = 800.0,
    detection_limiter_pwm: int = 1,
    push_force_pwm: int = 0,
    end_tolerance: float = 0.5,
    allow_without_tip: bool = False,
    post_detection_distance: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
  ) -> Optional[float]:
    """Lower a channel's tip until it presses on something, and say how high that is.

    The z-touch: the drive comes down at `approach_speed` to the start, then searches at
    `search_speed` with its force held to `detection_limiter_pwm`, and stops where the tip meets
    resistance. The channel says where its stop disc stopped; the tip bottom is the overhang
    below it. Then the channel backs off by `post_detection_distance` and rests there, or goes to
    Z safety instead when asked. A search that reached its end, within `end_tolerance`, touched
    nothing and answers None.

    The start is a tip bottom height, as the cLLD probe's; the end is a stop disc height, as
    legacy sends it, so the default is the drive's floor and the search goes as far as it can.
    Needs channel firmware from 2022 on, as discovery recorded it.

    Args:
      channel_idx: which channel, 0-indexed from the back.
      search_start_position: tip bottom height the search starts from, in mm. As high as the tip
        goes when None.
      search_end_position: lowest tip bottom height, in mm. The drive's floor when None.
      search_speed: in mm/s.
      approach_speed: down to the start, in mm/s.
      acceleration: in mm/s2.
      detection_limiter_pwm: the force at which the search stops, 0 to 125.
      push_force_pwm: the push-down force once stopped, 0 to 125; 0 switches the drive off.
      end_tolerance: how close to the end counts as having touched nothing, in mm.
      allow_without_tip: whether to probe without a tip, on the stop disc. False requires one.
      post_detection_distance: how far the channel backs off afterwards, in mm; 0 stays.
      move_channels_to_safe_pos_after: whether to raise every channel to Z safety afterwards,
        instead of resting where the search left it.

    Returns:
      The tip bottom height where it stopped, in mm, or None if it reached the end.

    Raises:
      RuntimeError: If the channel carries no tip and `allow_without_tip` is False, or its
        firmware predates 2022.
      ValueError: If an argument is out of range.
    """
    self._require_channel(channel_idx)
    self._require_ztouch_firmware(channel_idx)
    overhang = await self._overhang_that_probes(channel_idx, allow_without_tip)
    c = self.configuration
    lowest, highest = (c.z_drive_increments_to_mm(i) for i in c.z_range_increments)
    top, floor = highest - overhang, round(lowest - overhang, 2)
    if search_start_position is None:
      search_start_position = top
    if search_end_position is None:
      search_end_position = floor
    if not floor <= search_end_position <= top:
      raise ValueError(
        f"search_end_position must be between {floor} and {top} mm, is {search_end_position}"
      )
    if not search_end_position <= search_start_position <= top:
      raise ValueError(
        f"search_start_position must be between {search_end_position} and {top} mm, "
        f"is {search_start_position}"
      )
    try:
      stop_disc = await self._ztouch_search(
        channel_idx,
        round(search_end_position + overhang, 2),
        round(search_start_position + overhang, 2),
        search_speed=search_speed,
        approach_speed=approach_speed,
        acceleration=acceleration,
        detection_limiter_pwm=detection_limiter_pwm,
        push_force_pwm=push_force_pwm,
      )
    except STARFirmwareError:
      # The search went out and stopped somewhere: read where, then come up.
      await self._record_where_they_stopped("z")
      await self.move_to_safe_z()
      raise
    await self._record_where_they_stopped("z")
    tip_bottom = round(stop_disc - overhang, 2)
    touched = None if tip_bottom - search_end_position <= end_tolerance else tip_bottom
    if move_channels_to_safe_pos_after:
      await self.move_to_safe_z()
    elif post_detection_distance:
      await self.move_stop_disc_to_z_position(
        channel_idx, round(stop_disc + post_detection_distance, 2)
      )
    return touched

  # -- over many containers: liquid heights, volumes, and floors -------------------------------

  async def _prepare_batched(
    self,
    deck: Resource,
    containers: Sequence[Container],
    use_channels: Optional[List[int]],
    resource_offsets: Optional[List[Coordinate]],
    x_grouping_tolerance: Optional[float],
    minimum_traverse_height_start: Optional[float],
    minimum_traverse_height_end: Optional[float] = None,
  ) -> Tuple[List[int], Dict[int, float], List[ChannelBatch]]:
    """Check the channels and their tips, raise them, and plan the batches; X and Y stay put.

    More containers than channels are dealt to the channels in cycles, one per channel each
    cycle, each cycle planned into batches as legacy plans one and the cycles run one after the
    other. A cycle is the channels going once round the containers; a round is the same
    containers searched again.

    Args:
      deck: what the containers are placed on.
      containers: any number.
      use_channels: which channels, 0-indexed from the back. The first len(containers) when None,
        up to every channel.
      resource_offsets: added to where each channel goes in its container, in mm, one per
        container. Planned when None.
      x_grouping_tolerance: containers within this X distance share a batch, in mm.
        `default_x_grouping_tolerance` when None.
      minimum_traverse_height_start: the height every low channel's lowest point is raised to,
        in mm. Z safety when None.
      minimum_traverse_height_end: where the tips are to be left at the end, in mm, checked here
        against each tip's reach so the refusal comes before anything is in a container.

    Returns:
      The channel each container gets, per container; each channel's tip overhang in mm keyed by
      channel; and the batches in the order to run them.

    Raises:
      ValueError: If the channels or offsets do not match the containers, or a tip cannot reach
        the end.
      RuntimeError: If a channel used carries no tip.
    """
    if use_channels is None:
      use_channels = list(range(min(len(containers), self.num_channels)))
    if not containers:
      raise ValueError("no containers to probe")
    if not use_channels or len(set(use_channels)) != len(use_channels):
      raise ValueError(f"use_channels must name distinct channels, is {use_channels}")
    if resource_offsets is not None and len(resource_offsets) != len(containers):
      raise ValueError(f"{len(resource_offsets)} offsets for {len(containers)} containers")
    # A cycle is as many containers as there are channels, one each.
    cycle = len(use_channels)
    cycles = [list(containers[at : at + cycle]) for at in range(0, len(containers), cycle)]
    channels = [use_channels[job % cycle] for job in range(len(containers))]
    for dealt in cycles:
      validate_channel_selections(dealt, self.num_channels, use_channels[: len(dealt)])
    presence = await self.sense_tip_presence()
    bare = [channel for channel in use_channels if not presence[channel]]
    if bare:
      raise RuntimeError(f"channels {bare} carry no tip")
    # The overhang is the stop disc over the tip bottom, both read where they stand.
    lowest = await self._unchecked_fw_request_lowest_z_positions()
    stop_discs = await self.request_stop_disc_z_positions()
    overhangs = {ch: round(stop_discs[ch] - lowest[ch], 2) for ch in use_channels}
    if minimum_traverse_height_end is not None:
      top = self.configuration.z_range[1]
      too_high = {ch: round(top - overhangs[ch], 2) for ch in use_channels}
      too_high = {
        ch: reach for ch, reach in too_high.items() if minimum_traverse_height_end > reach
      }
      if too_high:
        raise ValueError(
          f"minimum_traverse_height_end {minimum_traverse_height_end} mm is above what the tips "
          f"reach: {too_high}"
        )
    if minimum_traverse_height_start is None:
      await self.move_to_safe_z()
    else:
      raises = await self._traverse_raise_targets(minimum_traverse_height_start)
      if raises:
        await self.move_stop_disc_to_z_positions(raises)
    tolerance = (
      self.default_x_grouping_tolerance if x_grouping_tolerance is None else x_grouping_tolerance
    )
    batches: List[ChannelBatch] = []
    for number, dealt in enumerate(cycles):
      first = number * cycle
      offsets = None if resource_offsets is None else resource_offsets[first : first + len(dealt)]
      for batch in plan_batches(
        use_channels=use_channels[: len(dealt)],
        containers=dealt,
        channel_spacings=self.minimum_y_spacings,
        wrt_resource=deck,
        x_tolerance=tolerance,
        resource_offsets=offsets,
      ):
        # The planner counts jobs within the cycle; the rest counts them over every container.
        batches.append(dataclasses.replace(batch, indices=[first + job for job in batch.indices]))
    return channels, overhangs, batches

  async def _execute_batched(
    self,
    func: Callable[[ChannelBatch], Awaitable[T]],
    batches: Sequence[ChannelBatch],
    minimum_traverse_height_during: Optional[float],
  ) -> List[T]:
    """Take the channels to each batch in turn and run `func` there; on any failure, Z safety.

    Between batches the channels come up to `minimum_traverse_height_during`, or to Z safety when
    None; then the arm and the channels travel to the batch together, `X0 XP` and `C0 JY`.

    Args:
      func: what to do at a batch. It moves nothing in X or Y.
      batches: as planned, in ascending X.
      minimum_traverse_height_during: the height every low channel's lowest point is raised to
        between batches, in mm. Z safety when None.

    Returns:
      What `func` answered at each batch, in order.
    """
    results: List[T] = []
    try:
      for index, batch in enumerate(batches):
        if index > 0:
          if minimum_traverse_height_during is None:
            await self.move_to_safe_z()
          else:
            raises = await self._traverse_raise_targets(minimum_traverse_height_during)
            if raises:
              await self.move_stop_disc_to_z_positions(raises)
        # Raised already, so the move raises nothing more.
        await self.move_to_xy_positions(
          batch.x_position, batch.y_positions, make_space=True, minimum_traverse_height_start=0
        )
        results.append(await func(batch))
    except BaseException:
      # A firmware error, a cancellation, an interrupt: the channels come up before it goes on.
      await self.move_to_safe_z()
      raise
    return results

  async def _finish_batched_heights(
    self,
    per_batch: Sequence[Dict[int, List[Optional[float]]]],
    channels: Sequence[int],
    containers: Sequence[Container],
    z_cavity_bottom: Sequence[float],
    minimum_traverse_height_end: Optional[float],
    what: str,
  ) -> List[Optional[float]]:
    """Turn the rounds of every batch into one height per container, and leave the channels.

    Args:
      per_batch: what each batch's rounds found, in mm on the deck, by job index.
      channels: the channel each container got, per job.
      containers: per job.
      z_cavity_bottom: per job, on the deck in mm.
      minimum_traverse_height_end: where the tips are left, in mm. Z safety when None.
      what: what was searched for, for the error.

    Returns:
      The mean of the rounds above each container's cavity bottom, in mm; None where no round
      found anything.

    Raises:
      RuntimeError: If something was found in some rounds and not in others.
    """
    found: Dict[int, List[Optional[float]]] = {}
    for batch_found in per_batch:
      for job, heights in batch_found.items():
        found.setdefault(job, []).extend(heights)
    above_bottom: List[Optional[float]] = []
    inconsistent = []
    for job, (channel, container) in enumerate(zip(channels, containers)):
      rounds = found[job]
      valid = [height for height in rounds if height is not None]
      if not valid:
        above_bottom.append(None)
      elif len(valid) == len(rounds):
        above_bottom.append(round(sum(valid) / len(valid) - z_cavity_bottom[job], 2))
      else:
        inconsistent.append(
          f"channel {channel} in {container.name}: {len(valid)} of {len(rounds)} rounds"
        )
    if inconsistent:
      await self.move_to_safe_z()
      raise RuntimeError(
        f"{what} found in some rounds and not in others, so it may be at the detection limit: "
        + "; ".join(inconsistent)
      )
    if minimum_traverse_height_end is None:
      await self.move_to_safe_z()
    else:
      await self.move_tool_bottom_to_z_positions(
        {channel: minimum_traverse_height_end for channel in sorted(set(channels))}
      )
    return above_bottom

  def _get_stop_disc_search_windows(
    self,
    batch: ChannelBatch,
    overhangs: Dict[int, float],
    z_cavity_bottom: Sequence[float],
    z_top: Sequence[float],
    above_top: float,
    below_bottom: float,
  ) -> List[Tuple[int, int, float, float]]:
    """The stop disc window each channel of a batch searches, lowest channel number first.

    From `above_top` over the container's top, no higher than the drive goes, down to
    `below_bottom` under its cavity bottom; both plus the channel's overhang, since the searches
    run on the stop disc.

    Args:
      batch: the channels and which container each has, by job index.
      overhangs: each channel's tip overhang in mm, keyed by channel.
      z_cavity_bottom: per job, on the deck in mm.
      z_top: per job, on the deck in mm.
      above_top: how far above the top the search starts, in mm.
      below_bottom: how far under the cavity bottom it may go, in mm.

    Returns:
      (channel, job, end, start) per channel, the heights in mm.
    """
    top = self.configuration.z_range[1]
    windows = []
    for channel, job in sorted(zip(batch.channels, batch.indices)):
      end = round(z_cavity_bottom[job] - below_bottom + overhangs[channel], 2)
      start = round(min(z_top[job] + overhangs[channel] + above_top, top), 2)
      windows.append((channel, job, end, start))
    return windows

  async def _probe_batch_liquid_heights(
    self,
    batch: ChannelBatch,
    containers: Sequence[Container],
    *,
    overhangs: Dict[int, float],
    z_cavity_bottom: Sequence[float],
    z_top: Sequence[float],
    lld_modes: Sequence["Pipettes.LLDMode"],
    search_speed: float,
    n_replicates: int,
  ) -> Dict[int, List[Optional[float]]]:
    """Search for the liquid in every container of one batch, the channels together, n times.

    Each channel searches from `search_start_clearance` above its container's top down to the
    cavity bottom, on its stop disc: the tip bottom plus the overhang. The heights come from one
    `C0 RL` after each round, so a channel that found nothing is None for that round.

    Args:
      batch: the channels and which container each has, by job index.
      containers: per job, what each channel searches in. Nothing here reads them: the simulator
        answers the searches from their trackers.
      overhangs: each channel's tip overhang in mm, keyed by channel.
      z_cavity_bottom: per job, on the deck in mm.
      z_top: per job, on the deck in mm.
      lld_modes: per job, capacitive or pressure.
      search_speed: in mm/s.
      n_replicates: how many rounds.

    Returns:
      The heights found, in mm on the deck, one list per job index; None where nothing was found.

    Raises:
      STARFirmwareError: Anything a channel answered other than that it found nothing.
    """
    searches = self._get_stop_disc_search_windows(
      batch, overhangs, z_cavity_bottom, z_top, self.search_start_clearance, 0.0
    )
    found: Dict[int, List[Optional[float]]] = {job: [] for job in batch.indices}
    for _ in range(n_replicates):
      results = await asyncio.gather(
        *(
          self._clld_search(channel, end, start, search_speed=search_speed)
          if lld_modes[job] == self.LLDMode.CAPACITIVE
          else self._plld_search(channel, end, start, search_speed=search_speed)
          for channel, job, end, start in searches
        ),
        return_exceptions=True,
      )
      heights = await self.request_last_lld_z_positions()
      for (channel, job, _, _), result in zip(searches, results):
        if isinstance(result, STARFirmwareError) and self._found_nothing(
          result, self.channel_id(channel)
        ):
          found[job].append(None)
        elif isinstance(result, BaseException):
          raise result
        else:
          found[job].append(heights[channel])
    return found

  async def probe_liquid_heights(
    self,
    containers: Sequence[Container],
    use_channels: Optional[List[int]] = None,
    resource_offsets: Optional[List[Coordinate]] = None,
    lld_mode: Union["Pipettes.LLDMode", Sequence["Pipettes.LLDMode"], None] = None,
    search_speed: float = 10.0,
    n_replicates: int = 1,
    *,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_during: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    x_grouping_tolerance: Optional[float] = None,
  ) -> List[float]:
    """Find the liquid surface in each container with a channel's tip, and say how high it stands.

    The containers are dealt to the channels in cycles, one per channel each cycle, and each
    cycle is planned into the fewest batches the channels can reach at once. The channels of
    a batch search together, capacitive (cLLD) or pressure (pLLD), from just above the container's
    top down to its cavity bottom. Every channel used carries a tip, and every channel is at Z
    safety at the end unless told where to stay.

    Args:
      containers: any number; a whole plate is fine.
      use_channels: which channels, 0-indexed from the back. The first len(containers) when None,
        up to every channel.
      resource_offsets: added to where each channel goes in its container, in mm. Planned when
        None, spreading channels that share a container.
      lld_mode: how to search, one for all or one per container. Capacitive when None.
      search_speed: in mm/s.
      n_replicates: how many times each container is searched; the heights are averaged.
      minimum_traverse_height_start: the height every low channel's lowest point is raised to
        before the first batch, in mm. Z safety when None.
      minimum_traverse_height_during: the same, between batches. Z safety when None.
      minimum_traverse_height_end: where the tips used are left, in mm. Z safety when None.
      x_grouping_tolerance: containers within this X distance share a batch, in mm.
        `default_x_grouping_tolerance` when None.

    Returns:
      How high the liquid stands above each container's cavity bottom, in mm, in the order given.
      The bottom is known, so a container in which no liquid was met stands at 0.0.

    Raises:
      ValueError: If an argument is out of range, or the lists do not match.
      RuntimeError: If a channel used carries no tip, the driver was given no deck, or liquid was
        found in some rounds and not in others.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("containers are placed from the deck; this driver was given none")
    if n_replicates < 1:
      raise ValueError(f"n_replicates must be at least 1, is {n_replicates}")
    if lld_mode is None:
      modes = [self.LLDMode.CAPACITIVE] * len(containers)
    elif isinstance(lld_mode, self.LLDMode):
      modes = [lld_mode] * len(containers)
    else:
      modes = list(lld_mode)
    if len(modes) != len(containers):
      raise ValueError(f"{len(modes)} lld modes for {len(containers)} containers")
    unsupported = [
      mode for mode in modes if mode not in (self.LLDMode.CAPACITIVE, self.LLDMode.PRESSURE)
    ]
    if unsupported:
      raise ValueError(f"a liquid search is capacitive or pressure, not {unsupported[0]}")

    channels, overhangs, batches = await self._prepare_batched(
      deck,
      containers,
      use_channels,
      resource_offsets,
      x_grouping_tolerance,
      minimum_traverse_height_start,
      minimum_traverse_height_end,
    )
    z_cavity_bottom = [c.get_location_wrt(deck, "c", "c", "cavity_bottom").z for c in containers]
    z_top = [c.get_location_wrt(deck, "c", "c", "t").z for c in containers]
    per_batch = await self._execute_batched(
      lambda batch: self._probe_batch_liquid_heights(
        batch,
        containers,
        overhangs=overhangs,
        z_cavity_bottom=z_cavity_bottom,
        z_top=z_top,
        lld_modes=modes,
        search_speed=search_speed,
        n_replicates=n_replicates,
      ),
      batches,
      minimum_traverse_height_during,
    )
    heights = await self._finish_batched_heights(
      per_batch, channels, containers, z_cavity_bottom, minimum_traverse_height_end, "liquid"
    )
    # The bottom is known, so a container in which no liquid was met stands at 0.0.
    return [0.0 if height is None else height for height in heights]

  async def probe_liquid_volumes(
    self,
    containers: Sequence[Container],
    use_channels: Optional[List[int]] = None,
    resource_offsets: Optional[List[Coordinate]] = None,
    lld_mode: Union["Pipettes.LLDMode", Sequence["Pipettes.LLDMode"], None] = None,
    search_speed: float = 10.0,
    n_replicates: int = 1,
    *,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_during: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    x_grouping_tolerance: Optional[float] = None,
  ) -> List[float]:
    """Find the liquid in each container as `probe_liquid_heights` does, and say how much there is.

    Each container's own geometry turns the height into a volume, so every container has to know
    its height-to-volume function.

    Args:
      As `probe_liquid_heights`.

    Returns:
      The volume in each container, in uL, in the order given; what its function makes of a
      height of 0.0 where no liquid was met.

    Raises:
      ValueError: If a container has no height-to-volume function, or as `probe_liquid_heights`.
      RuntimeError: As `probe_liquid_heights`.
    """
    without = [c.name for c in containers if not c.supports_compute_height_volume_functions()]
    if without:
      raise ValueError(f"no height-to-volume function for {without}")
    heights = await self.probe_liquid_heights(
      containers,
      use_channels,
      resource_offsets,
      lld_mode,
      search_speed,
      n_replicates,
      minimum_traverse_height_start=minimum_traverse_height_start,
      minimum_traverse_height_during=minimum_traverse_height_during,
      minimum_traverse_height_end=minimum_traverse_height_end,
      x_grouping_tolerance=x_grouping_tolerance,
    )
    return [
      container.compute_volume_from_height(height) for container, height in zip(containers, heights)
    ]

  @staticmethod
  async def _after(delay: float, search: Awaitable[T]) -> T:
    """Run `search` once `delay` seconds have passed, so gathered searches set off in a cascade."""
    if delay > 0:
      await asyncio.sleep(delay)
    return await search

  async def _probe_batch_floors(
    self,
    batch: ChannelBatch,
    *,
    overhangs: Dict[int, float],
    z_cavity_bottom: Sequence[float],
    z_top: Sequence[float],
    search_speed: float,
    below_floor: float,
    end_tolerance: float,
    start_spacing: float,
    approach_speed: float,
    n_replicates: int,
  ) -> Dict[int, List[Optional[float]]]:
    """Z-touch the floor of every container of one batch, the channels in a cascade, n times.

    As `_probe_batch_liquid_heights` with the search swapped: each channel searches from its
    container's top down to `below_floor` under its cavity bottom, and answers where its stop
    disc stopped, so the height is the overhang below that. There is no clearance above the top,
    as a liquid search has: nothing above it can be met, and everything below it is searched.
    The channels go to their starts together first, each on its own drive at `approach_speed`,
    so the cascade is the search itself: the searches set off `start_spacing` apart from there,
    the lowest channel number first, and run on together. A channel that reached its end, within
    `end_tolerance`, touched nothing and is None for the round. The channels stay where they
    stopped: the next round starts with the approach again.

    Args:
      batch: the channels and which container each has, by job index.
      overhangs: each channel's tip overhang in mm, keyed by channel.
      z_cavity_bottom: per job, on the deck in mm.
      z_top: per job, on the deck in mm.
      search_speed: in mm/s.
      below_floor: how far under the modelled cavity bottom the search may go, in mm.
      end_tolerance: how close to the end counts as having touched nothing, in mm.
      start_spacing: how long after the previous channel each one sets off, in s.
      approach_speed: down to the starts, in mm/s.
      n_replicates: how many rounds.

    Returns:
      The tip bottom heights touched, in mm on the deck, one list per job index; None where
      nothing was touched.

    Raises:
      STARFirmwareError: As a channel answered.
    """
    searches = self._get_stop_disc_search_windows(
      batch, overhangs, z_cavity_bottom, z_top, 0.0, below_floor
    )
    found: Dict[int, List[Optional[float]]] = {job: [] for job in batch.indices}
    for _ in range(n_replicates):
      await self.move_stop_disc_to_z_positions(
        {channel: start for channel, _, _, start in searches}, speed=approach_speed
      )
      results = await asyncio.gather(
        *(
          self._after(
            index * start_spacing,
            self._ztouch_search(channel, end, start, search_speed=search_speed),
          )
          for index, (channel, job, end, start) in enumerate(searches)
        ),
        return_exceptions=True,
      )
      await self._record_where_they_stopped("z", batch.channels)
      failed = [result for result in results if isinstance(result, BaseException)]
      if failed:
        raise failed[0]
      for (channel, job, end, _), stop_disc in zip(searches, results):
        stop_disc = cast(float, stop_disc)
        touched = stop_disc - end > end_tolerance
        found[job].append(round(stop_disc - overhangs[channel], 2) if touched else None)
    return found

  async def probe_z_heights_using_ztouch(
    self,
    containers: Sequence[Container],
    use_channels: Optional[List[int]] = None,
    resource_offsets: Optional[List[Coordinate]] = None,
    search_speed: float = 10.0,
    n_replicates: int = 1,
    *,
    below_floor: float = 5.0,
    end_tolerance: float = 0.5,
    start_spacing: float = 0.25,
    approach_speed: float = 125.0,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_during: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    x_grouping_tolerance: Optional[float] = None,
  ) -> List[Optional[float]]:
    """Touch the floor of each container with a channel's tip, and say how high it is.

    `probe_liquid_heights` with the z-touch in place of the liquid search: the same cycles and
    batches, the same moves between them, the channels of a batch searching together, and the
    same heights at the end. Each search goes from the container's top down to `below_floor`
    under its modelled cavity bottom, and stops where the tip presses on something. The channels
    of a batch go to their starts together at `approach_speed`, then set off in a cascade,
    `start_spacing` apart from the back. Needs channel firmware from 2022 on.

    Args:
      containers: any number; a whole plate is fine.
      use_channels: which channels, 0-indexed from the back. The first len(containers) when None,
        up to every channel.
      resource_offsets: added to where each channel goes in its container, in mm. Planned when
        None, spreading channels that share a container.
      search_speed: in mm/s.
      n_replicates: how many times each container is touched; the heights are averaged.
      below_floor: how far under the modelled cavity bottom the search may go, in mm.
      end_tolerance: how close to the end counts as having touched nothing, in mm.
      start_spacing: how long after the previous channel each one sets off, in s. 0 starts them
        all at once.
      approach_speed: down to the search starts, in mm/s.
      minimum_traverse_height_start: the height every low channel's lowest point is raised to
        before the first batch, in mm. Z safety when None.
      minimum_traverse_height_during: the same, between batches. Z safety when None.
      minimum_traverse_height_end: where the tips used are left, in mm. Z safety when None.
      x_grouping_tolerance: containers within this X distance share a batch, in mm.
        `default_x_grouping_tolerance` when None.

    Returns:
      Where each floor was touched, above the container's modelled cavity bottom, in mm, in the
      order given: 0.0 is a floor where the model has it, negative is lower. None where nothing
      was touched within reach.

    Raises:
      ValueError: If an argument is out of range, or the lists do not match.
      RuntimeError: If a channel used carries no tip or old firmware, the driver was given no
        deck, or a floor was touched in some rounds and not in others.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("containers are placed from the deck; this driver was given none")
    if n_replicates < 1:
      raise ValueError(f"n_replicates must be at least 1, is {n_replicates}")
    if start_spacing < 0:
      raise ValueError(f"start_spacing must be at least 0 s, is {start_spacing}")
    for channel in use_channels or range(min(len(containers), self.num_channels)):
      self._require_ztouch_firmware(channel)
    channels, overhangs, batches = await self._prepare_batched(
      deck,
      containers,
      use_channels,
      resource_offsets,
      x_grouping_tolerance,
      minimum_traverse_height_start,
      minimum_traverse_height_end,
    )
    z_cavity_bottom = [c.get_location_wrt(deck, "c", "c", "cavity_bottom").z for c in containers]
    z_top = [c.get_location_wrt(deck, "c", "c", "t").z for c in containers]
    per_batch = await self._execute_batched(
      lambda batch: self._probe_batch_floors(
        batch,
        overhangs=overhangs,
        z_cavity_bottom=z_cavity_bottom,
        z_top=z_top,
        search_speed=search_speed,
        below_floor=below_floor,
        end_tolerance=end_tolerance,
        start_spacing=start_spacing,
        approach_speed=approach_speed,
        n_replicates=n_replicates,
      ),
      batches,
      minimum_traverse_height_during,
    )
    return await self._finish_batched_heights(
      per_batch, channels, containers, z_cavity_bottom, minimum_traverse_height_end, "a floor"
    )

  # TODO: _unchecked_fw_ vs tip-presence-guarded versions

  # ----------------------------------------
  # Tip handling
  # ----------------------------------------

  # -- ? --------------------------------------------------

  def _tip_command_positions(
    self, locations: Dict[int, Coordinate]
  ) -> Tuple[List[int], List[int], List[bool]]:
    """Where each channel goes for a tip command, in the form the command carries them.

    As legacy lays them out: one entry per channel up to the last one used, a channel not taking
    part given zeros, and one trailing unused entry when fewer than every channel is listed.

    Args:
      locations: where each channel goes, on the deck in mm, keyed by channel, ascending.

    Returns:
      X and Y in tenths of a millimetre, and which channels take part.

    Raises:
      ValueError: If the channels are not ascending, a channel is not fitted, a position is out of
        reach, or two channels in the same column would sit closer than the wider of the two.
    """
    use_channels = list(locations)
    if use_channels != sorted(use_channels):
      raise ValueError(f"the channels must be ascending, are {use_channels}")

    xs: List[int] = []
    ys: List[int] = []
    pattern: List[bool] = []
    placed: Dict[int, Tuple[float, float]] = {}
    for channel, centre in locations.items():
      self._require_channel(channel)
      while channel > len(pattern):
        pattern.append(False)
        xs.append(0)
        ys.append(0)
      self._check_reachable("x", round(centre.x, 1))
      self._check_reachable("y", round(centre.y, 1))
      pattern.append(True)
      xs.append(round(centre.x * 10))
      ys.append(round(centre.y * 10))
      placed[channel] = (centre.x, centre.y)

    # As legacy checks them: each pair taking part by itself, not the channels between them. Where
    # those leave the pair too little room, the firmware arranges the channels, as it does for
    # legacy's commands.
    for i, (xi, yi) in placed.items():
      for j, (xj, yj) in placed.items():
        # Channels in different columns are separate moves on the device.
        if i < j and round(xi, 1) == round(xj, 1) and abs(yi - yj) < self._min_pair_spacing(i, j):
          raise ValueError(
            f"channels {i} and {j} would be {abs(yi - yj):.1f} mm apart in Y, closer than "
            f"{self._min_pair_spacing(i, j)} mm"
          )

    if len(pattern) < self.num_channels:
      xs.append(0)
      ys.append(0)
      pattern.append(False)
    return xs, ys, pattern

  async def _record_after_command(self) -> None:
    """Read back where a command left the arm and the channels, and record it."""
    try:
      await self.arm.request_position()
    except Exception:
      logger.warning("could not read where the arm stopped; its model is stale")
    await self._record_where_they_stopped("y")
    await self._record_where_they_stopped("z")

  # -- tip pickup ----------------------------------------------------------------------------

  async def _unchecked_fw_pick_up_tips(
    self,
    x_positions: List[int],
    y_positions: List[int],
    tip_pattern: List[bool],
    tip_type_index: int,
    begin_tip_pick_up_process: int,
    end_tip_pick_up_process: int,
    minimum_traverse_height_start: int,
    pickup_method: TipPickupMethod,
    read_timeout: int = 120,
  ):
    """Send the pick-up as it is given, in tenths of a millimetre. `C0 TP`."""
    return await self._driver.send_command(
      module="C0",
      command="TP",
      subsystem=_FirmwareLock.CHANNELS,
      tip_pattern=tip_pattern,
      read_timeout=read_timeout,
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tt=f"{tip_type_index:02}",
      tp=f"{begin_tip_pick_up_process:04}",
      tz=f"{end_tip_pick_up_process:04}",
      th=f"{minimum_traverse_height_start:04}",
      td=pickup_method.value,
    )

  def _tip_traverse_height(
    self, tips: Sequence[Tip], minimum_traverse_height_start: Optional[float]
  ) -> float:
    """How high the channels travel through a tip command, in mm.

    The command ends with the tip bottom at that height and the stop disc an overhang above it, so
    the longest tip decides how high the drive can take them. None travels as high as it can, and
    245.0 mm is the safety height: nothing travels below it, whatever is mounted.

    Args:
      tips: what the channels carry through the command.
      minimum_traverse_height_start: the height to travel at, in mm, or None for the highest.

    Returns:
      The height, in mm.

    Raises:
      ValueError: If the height is below the safety height or above what the tip can reach.
    """
    # Traverse height also applies to tips on unselected channels.
    tips = list(tips)
    for channel in range(self.num_channels):
      mounted = self.get_mounted_tip(channel)
      if mounted is not None:
        tips.append(mounted)
    overhang = max(tip.get_size_z() - tip.fitting_depth for tip in tips)
    ceiling = round(self.configuration.z_range[1] - overhang, 2)
    if ceiling < 245.0:
      raise ValueError(
        f"a tip {overhang:.1f} mm below the stop disc reaches only {ceiling} mm, below the "
        "245.0 mm safety height"
      )
    if minimum_traverse_height_start is None:
      return ceiling
    if minimum_traverse_height_start < 245.0:
      raise ValueError(
        f"the channels travel no lower than the 245.0 mm safety height, "
        f"not {minimum_traverse_height_start}"
      )
    if minimum_traverse_height_start > ceiling:
      raise ValueError(
        f"a tip {overhang:.1f} mm below the stop disc travels no higher than {ceiling} mm, "
        f"not {minimum_traverse_height_start}"
      )
    return minimum_traverse_height_start

  async def _pick_up_tips_in_one_move(
    self,
    locations: Dict[int, Coordinate],
    tips: Dict[int, HamiltonTip],
    begin_tip_pick_up_process: Optional[float] = None,
    end_tip_pick_up_process: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    pickup_method: Optional[TipPickupMethod] = None,
  ) -> Dict[int, bool]:
    """Pick a tip up at each place given, in one `C0 TP`, and say which channels came away with one.

    Knows nothing of spots: what it is given is where on the deck each channel collects, and what
    it collects there. `pick_up_tips` is the one that plans the batches and keeps the model.

    Args:
      locations: where each channel collects, on the deck in mm, keyed by channel, ascending.
      tips: what each channel collects, keyed by channel. All of the same model.
      begin_tip_pick_up_process: where the pick-up begins, in mm. The lowest location plus the
        collar height when None.
      end_tip_pick_up_process: where it ends, in mm. The lowest location when None.
      minimum_traverse_height_start: how high the channels travel first, in mm.
        `default_minimum_traverse_height` when None.
      pickup_method: out of a rack or out of wash liquid. The tip's own when None.

    Returns:
      Which channels came away with a tip, keyed by channel.

    Raises:
      ValueError: If the tips do not all have the same model, or a position cannot be reached.
    """
    use_channels = list(locations)
    hamilton_tips = [tips[channel] for channel in use_channels]
    if len({tip.model for tip in hamilton_tips}) > 1:
      raise ValueError("the tips picked up together must all have the same model")

    traverse = round(self._tip_traverse_height(hamilton_tips, minimum_traverse_height_start) * 10)

    xs, ys, pattern = self._tip_command_positions(locations)
    tip_type_index = await self._driver.get_or_assign_tip_type_index(hamilton_tips[0])

    spot_z = max(location.z for location in locations.values())
    collar_height = hamilton_tips[0].collar_height
    begin = (
      round((spot_z + collar_height) * 10)
      if begin_tip_pick_up_process is None
      else round(begin_tip_pick_up_process * 10)
    )
    end = (
      round(spot_z * 10) if end_tip_pick_up_process is None else round(end_tip_pick_up_process * 10)
    )

    picked_up: Dict[int, bool] = {channel: True for channel in use_channels}
    command_error: Optional[BaseException] = None
    try:
      await self._unchecked_fw_pick_up_tips(
        x_positions=xs,
        y_positions=ys,
        tip_pattern=pattern,
        tip_type_index=tip_type_index,
        begin_tip_pick_up_process=begin,
        end_tip_pick_up_process=end,
        minimum_traverse_height_start=traverse,
        pickup_method=pickup_method or hamilton_tips[0].pickup_method,
      )
    except BaseException as failure:
      command_error = failure
      # A command can stop part way, and both the device's answers say which channels it got to:
      # the error names the ones that faulted, and the channels themselves say what they carry now.
      # The sensed answer is the better one, and a cancelled command may not let them give it.
      faulted = channels_that_faulted(failure)
      picked_up = (
        {channel: channel not in faulted for channel in use_channels}
        if faulted
        else {channel: False for channel in use_channels}
      )
      presence = await self.sense_tip_presence()
      sensed = {channel: bool(presence[channel]) for channel in use_channels}
      disagreed = [
        channel for channel in use_channels if faulted and sensed[channel] != picked_up[channel]
      ]
      if disagreed:
        logger.warning(
          "channels %s carry something other than what the error said: the error named %s as "
          "faulted, and the channels sense %s. Taking what they sense.",
          disagreed,
          sorted(faulted),
          sensed,
        )
      # Out of the rack before anything else touches the deck: the channels go to the height the
      # command would have travelled at, by their stop discs, whatever state they were left in.
      try:
        await self.move_stop_disc_to_z_positions(
          {channel: traverse / 10 for channel in use_channels}
        )
      except BaseException:
        logger.warning("could not lift the channels to %.1f mm after the failure", traverse / 10)
      picked_up = sensed
      raise
    finally:
      try:
        for channel, collected in picked_up.items():
          shaft = self.shaft(channel)
          if collected and shaft is not None:
            shaft.mount_tip(tips[channel])
      except Exception:
        # What the device said is the error worth having: this one only says the model is stale.
        if command_error is None:
          raise
        logger.exception("could not record which tips the channels collected")
      await self._record_after_command()
    return picked_up

  async def pick_up_tips(
    self,
    tip_spots: Sequence[TipSpot],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
    begin_tip_pick_up_process: Optional[float] = None,
    end_tip_pick_up_process: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    pickup_method: Optional[TipPickupMethod] = None,
    x_tolerance: Optional[float] = None,
  ) -> None:
    """Pick up a tip from each spot, one channel per spot, and move each onto its channel.

    The spots may hold tips of different models: a command carries one tip type, so the spots are
    grouped by the model of tip they hold and each group is planned into its own commands. The
    commands go out in ascending X, whichever group they came from, so the arm sweeps once.

    Heights as legacy's `STARBackend.pick_up_tips`: the process begins a collar's height above the
    highest spot and ends at the spot. Once the device has picked them up, each tip is taken out of
    its spot and mounted on its channel's shaft. If the command fails, the channels are asked which
    of them carry a tip, and only those tips move.

    Args:
      tip_spots: where to pick up from, one per channel.
      use_channels: which channels, 0-indexed from the back, ascending. The first
        `len(tip_spots)` when None.
      offsets: added to each spot's centre, in mm. None for none.
      begin_tip_pick_up_process: where the pick-up begins, in mm. The spot plus the collar height
        when None.
      end_tip_pick_up_process: where it ends, in mm. The spot when None.
      minimum_traverse_height_start: how high the channels travel first, in mm.
        `default_minimum_traverse_height` when None.
      pickup_method: out of a rack or out of wash liquid. The tip's own when None.
      x_tolerance: how far apart in X two spots may be and still go out in one command, in mm.
        None lets any two share one, as legacy sends them: the firmware works through the columns
        itself. Spots in one column too close in Y for their channels are split either way.

    Raises:
      NoTipError: If a spot holds no tip while tip tracking is on.
      HasTipError: If a channel already carries a tip.
      ValueError: If a position cannot be reached.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    use_channels = list(range(len(tip_spots))) if use_channels is None else list(use_channels)
    offsets = [Coordinate.zero()] * len(tip_spots) if offsets is None else list(offsets)

    if not tip_spots:
      return
    tips = [spot.tip_for_pickup() for spot in tip_spots]
    if not all(isinstance(tip, HamiltonTip) for tip in tips):
      raise TypeError("the STAR picks up Hamilton tips")
    hamilton_tips = cast(List[HamiltonTip], tips)
    for channel in use_channels:
      mounted = self.get_mounted_tip(channel)
      if mounted is not None:
        raise HasTipError(f"channel {channel} already carries {mounted.name}")
      if self.shaft(channel) is None:
        # Nowhere to put the tip it collects, so it would come off its spot and belong to nothing.
        # A driver given its deck only after setup has no resource for a channel until setup runs
        # again with that deck.
        raise RuntimeError(f"channel {channel} is not modelled; set the driver up with its deck")

    # One command per set of spots the channels can take at once, planned per tip model: a
    # command names one tip type, so spots holding different tips cannot share one.
    of_each_model: Dict[Optional[str], List[int]] = {}
    for index, tip in enumerate(hamilton_tips):
      of_each_model.setdefault(tip.model, []).append(index)

    # Only Y decides within a model by default: spots in one column closer than their channels may
    # stand go in separate commands. The batches are run in ascending X, wherever they came from.
    planned: List[Tuple[ChannelBatch, List[int]]] = []
    for group in of_each_model.values():
      planned += [
        (batch, group)
        for batch in plan_batches(
          use_channels=[use_channels[index] for index in group],
          containers=cast(List[Container], [tip_spots[index] for index in group]),
          channel_spacings=self.minimum_y_spacings,
          wrt_resource=deck,
          x_tolerance=ANY_COLUMN if x_tolerance is None else x_tolerance,
          resource_offsets=[offsets[index] for index in group],
        )
      ]
    planned.sort(key=lambda p: (p[0].x_position, min(p[1][i] for i in p[0].indices)))

    for batch, group in planned:
      indices = [group[index] for index in batch.indices]
      spots = [tip_spots[index] for index in indices]
      in_batch = list(batch.channels)
      await self._pick_up_tips_in_one_move(
        {
          channel: spot.get_location_wrt(deck, x="c", y="c", z="b") + offsets[index]
          for spot, channel, index in zip(spots, in_batch, indices)
        },
        {channel: hamilton_tips[index] for channel, index in zip(in_batch, indices)},
        begin_tip_pick_up_process=begin_tip_pick_up_process,
        end_tip_pick_up_process=end_tip_pick_up_process,
        minimum_traverse_height_start=minimum_traverse_height_start,
        pickup_method=pickup_method,
      )

  # -- tip drop --------------------------------------------------

  async def _unchecked_fw_drop_tips(
    self,
    x_positions: List[int],
    y_positions: List[int],
    tip_pattern: List[bool],
    begin_tip_deposit_process: int,
    end_tip_deposit_process: int,
    minimum_traverse_height_start: int,
    minimum_traverse_height_end: int,
    discarding_method: TipDropMethod,
  ):
    """Send the drop as it is given, in tenths of a millimetre. `C0 TR`.

    With `PLACE_SHIFT` the heights are where the tip's cone ends; with `DROP`, the stop disc's.
    """
    return await self._driver.send_command(
      module="C0",
      command="TR",
      subsystem=_FirmwareLock.CHANNELS,
      tip_pattern=tip_pattern,
      read_timeout=120,
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tp=begin_tip_deposit_process,
      tz=end_tip_deposit_process,
      th=minimum_traverse_height_start,
      te=minimum_traverse_height_end,
      ti=discarding_method.value,
    )

  async def _drop_tips_in_one_move(
    self,
    locations: Dict[int, Coordinate],
    drop_method: TipDropMethod,
    begin_tip_deposit_process: Optional[float] = None,
    end_tip_deposit_process: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> Dict[int, bool]:
    """Let each channel's tip go at the place given, in one `C0 TR`, and say which let go.

    Knows nothing of spots: what it is given is where on the deck each channel drops. Heights as
    legacy's `STARBackend.drop_tips`: `DROP` from the place plus the collar height down by the
    fitting depth, `PLACE_SHIFT` from 59.9 mm down to 49.9 mm above it. A tip that went is taken
    off its shaft; where it belongs afterwards is `drop_tips`' to say.

    Args:
      locations: where each channel drops, on the deck in mm, keyed by channel, ascending.
      drop_method: how to let the tips go.
      begin_tip_deposit_process: where the deposit begins, in mm.
      end_tip_deposit_process: where it ends, in mm.
      minimum_traverse_height_start: how high the channels travel first, in mm.
      minimum_traverse_height_end: where the channels are left, in mm.

    Returns:
      Which channels let their tip go, keyed by channel.

    Raises:
      NoTipError: If a channel carries no tip in the model.
    """
    use_channels = list(locations)
    tips: List[Tip] = []
    for channel in use_channels:
      tip = self.get_mounted_tip(channel)
      if tip is None:
        raise NoTipError(f"channel {channel} carries no tip")
      tips.append(tip)

    xs, ys, pattern = self._tip_command_positions(locations)
    target_z = max(location.z for location in locations.values())
    if drop_method == TipDropMethod.PLACE_SHIFT:
      # Empirical, from legacy: https://github.com/PyLabRobot/pylabrobot/pull/63
      default_begin, default_end = target_z + 59.9, target_z + 49.9
    else:
      if not all(isinstance(tip, HamiltonTip) for tip in tips):
        raise TypeError("the STAR drops Hamilton tips into tip spots")
      if len({tip.collar_height for tip in tips}) > 1:
        raise ValueError("the tips dropped together must share a collar height")
      collar_height = tips[0].collar_height
      default_begin = target_z + collar_height
      default_end = target_z + collar_height - tips[0].fitting_depth
    begin = round(
      (default_begin if begin_tip_deposit_process is None else begin_tip_deposit_process) * 10
    )
    end = round((default_end if end_tip_deposit_process is None else end_tip_deposit_process) * 10)
    traverse = round(self._tip_traverse_height(tips, minimum_traverse_height_start) * 10)
    if minimum_traverse_height_end is not None:
      # Where the channels are left, bare: no lower than the safety height, within the drive.
      if minimum_traverse_height_end < 245.0:
        raise ValueError(
          f"the channels are left no lower than the 245.0 mm safety height, "
          f"not {minimum_traverse_height_end}"
        )
      self._check_reachable("z", minimum_traverse_height_end)
    # `traverse` is already in tenths; a height given is in mm.
    z_end = (
      traverse if minimum_traverse_height_end is None else round(minimum_traverse_height_end * 10)
    )

    dropped: Dict[int, bool] = {channel: True for channel in use_channels}
    command_error: Optional[BaseException] = None
    try:
      await self._unchecked_fw_drop_tips(
        x_positions=xs,
        y_positions=ys,
        tip_pattern=pattern,
        begin_tip_deposit_process=begin,
        end_tip_deposit_process=end,
        minimum_traverse_height_start=traverse,
        minimum_traverse_height_end=z_end,
        discarding_method=drop_method,
      )
    except BaseException as failure:
      command_error = failure
      # As the pick-up takes it, from the error and then from the channels: one that still carries
      # its tip has not dropped it.
      faulted = channels_that_faulted(failure)
      dropped = (
        {channel: channel not in faulted for channel in use_channels}
        if faulted
        else {channel: False for channel in use_channels}
      )
      presence = await self.sense_tip_presence()
      sensed = {channel: not presence[channel] for channel in use_channels}
      disagreed = [
        channel for channel in use_channels if faulted and sensed[channel] != dropped[channel]
      ]
      if disagreed:
        logger.warning(
          "channels %s carry something other than what the error said: the error named %s as "
          "faulted, and the channels still carry %s. Taking what they sense.",
          disagreed,
          sorted(faulted),
          {channel: bool(presence[channel]) for channel in use_channels},
        )
      # Out of the rack before anything else touches the deck: the channels go to the height the
      # command would have travelled at, by their stop discs, whatever state they were left in.
      try:
        await self.move_stop_disc_to_z_positions(
          {channel: traverse / 10 for channel in use_channels}
        )
      except BaseException:
        logger.warning("could not lift the channels to %.1f mm after the failure", traverse / 10)
      dropped = sensed
      raise
    finally:
      try:
        for channel, let_go in dropped.items():
          if let_go:
            self._release_modelled_tip(channel)
      except Exception:
        # What the device said is the error worth having: this one only says the model is stale.
        if command_error is None:
          raise
        logger.exception("could not record which tips the channels let go of")
      await self._record_after_command()
    return dropped

  async def drop_tips(
    self,
    destinations: Sequence[Union[TipSpot, Coordinate]],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
    drop_method: Optional[TipDropMethod] = None,
    begin_tip_deposit_process: Optional[float] = None,
    end_tip_deposit_process: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    x_tolerance: Optional[float] = None,
  ) -> None:
    """Drop each channel's tip into a tip spot, or anywhere on the deck, such as the waste.

    Spots are planned into the fewest commands the channels can take at once, as `pick_up_tips`
    plans them; places given as a coordinate go in one command, as legacy sends a discard. A tip
    dropped into a spot goes into that spot in the model, one dropped anywhere else belongs to
    nothing.

    The channels may carry tips of different kinds: a `DROP` lowers them all to one height, so the
    channels are grouped by the collar height of the tip they carry and each group is planned into
    its own commands, in ascending X. A `PLACE_SHIFT` lets go from a height of its own, so a
    discard takes whatever the channels carry in one command.

    Args:
      destinations: where each channel's tip goes: a `TipSpot`, which receives it, or a place on
        the deck in mm.
      use_channels: which channels, 0-indexed from the back, ascending. The first
        `len(destinations)` when None.
      offsets: added to each destination, in mm. None for none.
      drop_method: `DROP` when every destination is a tip spot and `PLACE_SHIFT` otherwise, when
        None.
      begin_tip_deposit_process: where the deposit begins, in mm.
      end_tip_deposit_process: where it ends, in mm.
      minimum_traverse_height_start: how high the channels travel first, in mm.
      minimum_traverse_height_end: where the channels are left, in mm.
      x_tolerance: how far apart in X two spots may be and still go out in one command, in mm.
        None lets any two share one, as legacy sends them.

    Raises:
      NoTipError: If a channel carries no tip in the model.
      HasTipError: If a tip spot already holds a tip.
      ValueError: If two tips would go into one spot.
    """
    if not destinations:
      return
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    use_channels = list(range(len(destinations))) if use_channels is None else list(use_channels)
    offsets = [Coordinate.zero()] * len(destinations) if offsets is None else list(offsets)

    spots = [place for place in destinations if isinstance(place, TipSpot)]
    if len({id(spot) for spot in spots}) != len(spots):
      raise ValueError("each tip must go into a spot of its own")
    for spot in spots:
      if spot.tracks_tips and spot.tip is not None:
        raise HasTipError(f"{spot.name} already holds a tip")
    if drop_method is None:
      drop_method = (
        TipDropMethod.DROP if len(spots) == len(destinations) else TipDropMethod.PLACE_SHIFT
      )

    def where(index: int) -> Coordinate:
      place = destinations[index]
      corner = (
        place.get_location_wrt(deck, x="c", y="c", z="b") if isinstance(place, TipSpot) else place
      )
      return corner + offsets[index]

    # Only spots can be planned: the planner asks a resource where it is and what is in the way.
    if len(spots) == len(destinations):
      # A `DROP` lowers every tip to one height, so tips whose collars differ cannot share one
      # command. `PLACE_SHIFT` lets go from a height of its own and takes them together.
      of_each_collar: Dict[Optional[float], List[int]] = {}
      for index, channel in enumerate(use_channels):
        held_tip = self.get_mounted_tip(channel)
        collar = (
          held_tip.collar_height
          if drop_method is TipDropMethod.DROP and isinstance(held_tip, HamiltonTip)
          else None
        )
        of_each_collar.setdefault(collar, []).append(index)

      planned: List[Tuple[ChannelBatch, List[int]]] = []
      for of_one_collar in of_each_collar.values():
        planned += [
          (batch, of_one_collar)
          for batch in plan_batches(
            use_channels=[use_channels[index] for index in of_one_collar],
            containers=cast(List[Container], [spots[index] for index in of_one_collar]),
            channel_spacings=self.minimum_y_spacings,
            wrt_resource=deck,
            x_tolerance=ANY_COLUMN if x_tolerance is None else x_tolerance,
            resource_offsets=[offsets[index] for index in of_one_collar],
          )
        ]
      planned.sort(key=lambda p: (p[0].x_position, min(p[1][i] for i in p[0].indices)))
      groups = [
        ([of_one_collar[index] for index in batch.indices], list(batch.channels))
        for batch, of_one_collar in planned
      ]
    else:
      groups = [(list(range(len(destinations))), use_channels)]

    for indices, channels_in_group in groups:
      # Held now, because the command takes each tip off its shaft: what is put into the spot is
      # the tip the channel came with.
      held = {channel: self.get_mounted_tip(channel) for channel in channels_in_group}
      dropped = await self._drop_tips_in_one_move(
        {channel: where(index) for channel, index in zip(channels_in_group, indices)},
        drop_method,
        begin_tip_deposit_process=begin_tip_deposit_process,
        end_tip_deposit_process=end_tip_deposit_process,
        minimum_traverse_height_start=minimum_traverse_height_start,
        minimum_traverse_height_end=minimum_traverse_height_end,
      )
      for index, channel in zip(indices, channels_in_group):
        place = destinations[index]
        tip = held[channel]
        if (
          dropped[channel] and tip is not None and isinstance(place, TipSpot) and place.tracks_tips
        ):
          place.assign_tip(tip)

  async def return_tips(self, use_channels: Optional[List[int]] = None, **kwargs) -> None:
    """Put each channel's tip back in the tip spot it was picked up from, as legacy does.

    The spot is found from the tip itself: a spot names the tips it makes after itself.

    Args:
      use_channels: which channels. Of these, only those carrying a tip return one. Every channel,
        when None.
      kwargs: passed on to `drop_tips`.

    Raises:
      RuntimeError: If no channel carries a tip, or a tip's spot is not on the deck.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    channels = range(self.num_channels) if use_channels is None else use_channels
    spots: List[TipSpot] = []
    carrying: List[int] = []
    for channel in sorted(channels):
      tip = self.get_mounted_tip(channel)
      if tip is None:
        continue
      spot = tip_origin(tip, deck)
      if spot is None:
        raise RuntimeError(
          f"the spot channel {channel}'s tip {tip.name} came from is not on the deck"
        )
      spots.append(spot)
      carrying.append(channel)
    if not spots:
      raise RuntimeError("No tips have been picked up.")
    await self.drop_tips(spots, use_channels=carrying, **kwargs)

  async def discard_tips(
    self,
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
    **kwargs,
  ) -> None:
    """Discard each channel's tip into the deck's waste, spread across it as legacy spreads them.

    Args:
      use_channels: which channels. Every channel the model has a tip on, when None.
      offsets: added to where each channel is spread to, in mm.
      kwargs: passed on to `drop_tips`.
    """
    deck = self._driver.deck
    if deck is None:
      raise RuntimeError("tip commands are placed from the deck; this driver was given none")
    if use_channels is None:
      use_channels = [
        channel for channel in range(self.num_channels) if self.get_mounted_tip(channel) is not None
      ]
    if not use_channels:
      return
    trash = deck.get_trash_area()
    spread = compute_channel_offsets(trash, num_channels=len(use_channels), spread="tight")
    offsets = (
      spread if offsets is None else [offset + extra for offset, extra in zip(offsets, spread)]
    )
    # The waste is a place, not a spot: each tip is let go over it and belongs to nothing after.
    over_the_waste = trash.get_location_wrt(deck, x="c", y="c", z="b")
    await self.drop_tips(
      [over_the_waste] * len(use_channels),
      use_channels=use_channels,
      offsets=offsets,
      **kwargs,
    )
