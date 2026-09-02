"""The pipetting channels: the row of independently driven pipettes on an arm."""

import asyncio
import datetime
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple, cast

from pylabrobot.hamilton.protocol.text.framing import parse_firmware_version_date
from pylabrobot.hamilton.star.resource_model import TipMountingShaft
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)

ChannelType = Literal["ML_STAR", "ML_STAR_RPC"]
HeadType = Literal["ML_STAR", "ML_STAR_PLE", "ML_STAR_RPC"]
StopDiscType = Literal["core_i", "core_ii"]
PressureADC = Literal["Renesas_X9268", "Analog_Devices_AD5263"]

# The hardware query is not carried by firmware from 2016 or older.
HARDWARE_QUERY_FIRST_YEAR = 2017

# Where the channels are put during the initialization procedure, in mm: the Y band they spread
# across, and the Z heights the procedure moves through.
INITIALIZE_Y_RANGE = (217.5, 405.0)
INITIALIZE_BEGIN_OF_TIP_DEPOSIT = 245.0
INITIALIZE_END_OF_TIP_DEPOSIT = 122.0
INITIALIZE_Z_POSITION_AT_END = 360.0
INITIALIZE_TIP_TYPE = 4
INITIALIZE_DISCARDING_METHOD = 0

# The channels travel to the waste and eject there, so the reply is a long time coming.
INITIALIZE_READ_TIMEOUT = 120

# A channel's drive positions it by its centre along Y, and by its stop disk - its lowest fixed
# feature - along Z. Along X every channel sits at the arm's own reference point: the master and
# the X-drive board report the same position, so a channel's X is the arm's.
CHANNEL_Y_REFERENCE_ANCHOR = "c"
CHANNEL_Z_REFERENCE_ANCHOR = "b"
CHANNEL_X_REFERENCE_ANCHOR = "c"


@dataclass
class PipetteConfiguration:
  """The hardware fitted to a single pipetting channel.

  Read off the channel itself, so every field is None until it has been read.
  """

  channel_type: Optional[ChannelType] = None
  head_type: Optional[HeadType] = None
  stop_disc_type: Optional[StopDiscType] = None
  pressure_adc: Optional[PressureADC] = None
  firmware_version: Optional[str] = None
  """The channel board's firmware version, as reported."""
  width: Optional[float] = None
  """How wide the pipette is, in mm. Two channels cannot sit closer than this in Y."""


@dataclass
class PipettesConfiguration:
  """Configuration for the pipetting channels, and for each channel in turn.

  The encoder resolutions convert between the units a command carries on the wire (increments)
  and the units the driver speaks (mm, uL). They are properties of the channel drives, identical
  across a machine's channels, so they are held once rather than per channel.

  `channels` holds what each individual channel carries. It is empty until setup has counted the
  channels, since only the machine says how many there are.
  """

  y_drive_mm_per_increment: float = 0.046302083
  z_drive_mm_per_increment: float = 0.01072765
  dispensing_drive_mm_per_increment: float = 0.002734375
  dispensing_drive_uL_per_increment: float = 0.046876

  channel_size_z: float = 140.0
  """How tall to model a channel, in mm. Not read from anywhere: how far a channel extends is not
  something the machine reports."""

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
    replaced individually, and a channel on different firmware may not convert the same way, so a
    machine that has been repaired piecemeal is worth knowing about.
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
      "differently, and the version recorded for the capability is channel %d's.",
      reported,
      next(iter(by_version.values()))[0],
    )

  def resolve_channels(self, num_channels: int) -> None:
    """Size `channels` against the machine, once it has said how many channels it has.

    A list supplied up front is left as it is, so a caller can configure channels before the
    machine is known and have that checked rather than overwritten.

    Args:
      num_channels: how many channels the machine reported.

    Raises:
      ValueError: If a supplied list does not have one entry per channel.
    """
    if not self.channels:
      self.channels.extend(PipetteConfiguration() for _ in range(num_channels))
    elif len(self.channels) != num_channels:
      raise ValueError(f"configuration has {len(self.channels)} channels, expected {num_channels}")


class Pipettes:
  """The pipetting channels.

  Reached as `driver.pipettes`. Individual channels are addressed as `P1`..`PG`, but the commands
  that act on all of them at once go to the master, so this capability speaks to both.

  `configuration` holds what every channel shares, and one entry per channel in
  `configuration.channels`.
  """

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

  # -- addressing ------------------------------------------------------------

  @staticmethod
  def channel_id(channel: int) -> str:
    """The module a channel is addressed by. Channel 0 is the one at the back."""
    return "P" + "123456789ABCDEFG"[channel]

  @property
  def num_channels(self) -> int:
    """How many channels are fitted, as counted at setup."""
    return self._driver.num_channels

  # -- session / discovery ---------------------------------------------------

  async def request_firmware_version(self, channel: int) -> Tuple[str, datetime.date]:
    """Request one channel's firmware version and build date.

    Args:
      channel: which channel to ask, 0-indexed from the back.

    Returns:
      The version string and its build date, e.g. `("4.0S j 2022-03-16", date(2022, 3, 16))`.
    """
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
    resp = await self._driver.send_command(
      module=self.channel_id(channel), command="VY", fmt="yc### (n)"
    )
    increments = cast(List[int], resp["yc"])[1]
    return self.configuration.y_drive_increments_to_mm(increments)

  async def request_pipette_configuration(self, channel: int) -> PipetteConfiguration:
    """Request what hardware is fitted to a pipette.

    Not carried by firmware from 2016 or older.

    Args:
      channel: which channel to ask, 0-indexed from the back.

    Returns:
      What the channel reports about itself. The fields it does not report - its firmware version
      and its width - are left None, since they are separate queries.

    Raises:
      ValueError: If the reply carries no hardware fields at all.
    """
    resp = await self._driver.send_command(module=self.channel_id(channel), command="VW")
    fields = resp.split("vw")[-1].strip().split()
    if not fields:
      raise ValueError(f"no hardware fields in the reply from channel {channel}: {resp!r}")

    def field_at(index: int) -> Optional[str]:
      # The reply carries between two and four fields depending on firmware. A field that is not
      # there falls back to its baseline value rather than failing: these are descriptive, and no
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
      if build_date.year >= HARDWARE_QUERY_FIRST_YEAR
      else PipetteConfiguration()
    )
    pipette.firmware_version = version
    pipette.width = await self.request_min_pipette_width(channel)
    self.configuration.channels[channel] = pipette

  # -- where the channels are ------------------------------------------------

  async def request_y_positions(self) -> List[float]:
    """Request where every channel is along Y, in one command.

    The master answers for all of them at once, so this is one exchange rather than one per
    channel. Each answer is recorded on the resource modelling that channel.

    Returns:
      The position of each channel in mm, back to front.
    """
    resp = await self._driver.send_command(module="C0", command="RY", fmt="ry#### (n)")
    positions = [increments / 10 for increments in cast(List[int], resp["ry"])]
    for channel, y in enumerate(positions):
      self.update_location_by_reference_point(channel, y=y)
    return positions

  async def request_stop_disk_z(self, channel: int) -> float:
    """Request where a channel's stop disk is along Z.

    The raw drive position regardless of what is mounted, not the bottom of a tip. Recorded on the
    resource modelling the channel.

    Args:
      channel: which channel to ask, 0-indexed from the back.

    Returns:
      The stop-disk Z position in mm.
    """
    resp = await self._driver.send_command(
      module=self.channel_id(channel), command="RZ", fmt="rz######"
    )
    z = self.configuration.z_drive_increments_to_mm(cast(int, resp["rz"]))
    self.update_location_by_reference_point(channel, z=z)
    return z

  def update_location_by_reference_point(
    self, channel: int, y: Optional[float] = None, z: Optional[float] = None
  ) -> None:
    """Record where a channel is on the resource that models it.

    Y and Z only: a channel rides the arm, so its resource is a child of the arm's and follows it
    in X without anything having to record that. A resource is located by its left front bottom
    corner, so each axis differs from what the drive reports by the channel's own reference point.

    That point is the channel's to state, because it is not a corner of the box: the drives report
    the stop disk, which is the shaft a tip mounts on, and that hangs below the body. A channel that
    states nothing falls back to its own anchors, which is the same point when it carries no shaft.

    Both drives answer in the deck's frame, while a resource's location is measured from its parent
    - the arm, not the deck - so the arm's own position is taken out before either is recorded.
    Does nothing when the driver was given no deck, and so has nothing to model.

    Args:
      channel: which channel, 0-indexed from the back.
      y: where it is now, in mm on the deck. Left as it was when None.
      z: where its stop disk is now, in mm on the deck. Left as it was when None.
    """
    deck = self._driver.deck
    if channel >= len(self.resources) or deck is None:
      return
    resource = self.resources[channel]
    if resource.location is None or resource.parent is None:
      return
    here, on_the_arm = resource.location, resource.parent.get_location_wrt(deck)
    anchor = getattr(resource, "reference_point", None)
    if anchor is None:
      anchor = resource.get_anchor(y=CHANNEL_Y_REFERENCE_ANCHOR, z=CHANNEL_Z_REFERENCE_ANCHOR)
    resource.location = Coordinate(
      here.x,
      here.y if y is None else y - on_the_arm.y - anchor.y,
      here.z if z is None else z - on_the_arm.z - anchor.z,
    )

  @staticmethod
  def add_tip_mounting_shaft(channel: Resource) -> None:
    """Hang a tip mounting shaft off the lower end of a channel, and measure the channel from it.

    Its own length below the channel's bottom rather than inside it, so the shaft is what reaches
    lowest and the channel body starts clear of it - the arrangement a 96-head has, where the shafts
    define the bottom of the assembly. Centred on the channel, since that is the axis a tip is
    collected on. One already there is left alone, so repeated setups do not duplicate it.

    The shaft is the stop disk the Z drive reports, so it is also where the channel is measured
    from, which is why this states the reference point that `update_location_by_reference_point`
    reads straight back.

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

  # -- channel initialization ------------------------------------------------

  def default_initialize_y_positions(self) -> List[float]:
    """Where each channel sits in Y during initialization, in mm, back to front.

    The channels spread evenly across the band the procedure uses, so they are clear of one
    another whatever the channel count.
    """
    front, back = INITIALIZE_Y_RANGE
    spacing = round((back - front) * 10) // (self.num_channels - 1)
    return [(round(back * 10) - channel * spacing) / 10 for channel in range(self.num_channels)]

  async def initialize(
    self,
    x_position: Optional[float] = None,
    y_positions: Optional[List[float]] = None,
    begin_of_tip_deposit_process: float = INITIALIZE_BEGIN_OF_TIP_DEPOSIT,
    end_of_tip_deposit_process: float = INITIALIZE_END_OF_TIP_DEPOSIT,
    z_position_at_end_of_a_command: float = INITIALIZE_Z_POSITION_AT_END,
    tip_pattern: Optional[List[bool]] = None,
    tip_type: int = INITIALIZE_TIP_TYPE,
    discarding_method: int = INITIALIZE_DISCARDING_METHOD,
  ):
    """Initialize the channels, discarding whatever is mounted on them.

    This moves the channels: they spread out across the Y band, travel to the tip waste, and
    eject. Anything on a channel, including a gripper, ends up in the waste.

    Args:
      x_position: X to eject at, in mm. Defaults to the instrument's tip waste position.
      y_positions: where to put each channel in Y, in mm, back to front. Defaults to spreading
        them evenly across the Y band the procedure uses.
      begin_of_tip_deposit_process: Z to start the eject from, in mm.
      end_of_tip_deposit_process: Z the eject ends at, in mm.
      z_position_at_end_of_a_command: Z to leave the channels at, in mm.
      tip_pattern: which channels take part. Defaults to all of them.
      tip_type: tip type table index.
      discarding_method: how tips are discarded.
    """
    if x_position is None:
      if self._driver.configuration is None:
        raise RuntimeError("no configuration read; have you called `star.setup()`?")
      x_position = self._driver.configuration.tip_waste_x_position
    if y_positions is None:
      y_positions = self.default_initialize_y_positions()
    if tip_pattern is None:
      tip_pattern = [True] * self.num_channels

    return await self._driver.send_command(
      module="C0",
      command="DI",
      read_timeout=INITIALIZE_READ_TIMEOUT,
      xp=[f"{round(x_position * 10):05}"],
      yp=[f"{round(y * 10):04}" for y in y_positions],
      tp=f"{round(begin_of_tip_deposit_process * 10):04}",
      tz=f"{round(end_of_tip_deposit_process * 10):04}",
      te=f"{round(z_position_at_end_of_a_command * 10):04}",
      tm=[f"{tm:01}" for tm in tip_pattern],
      tt=f"{tip_type:02}",
      ti=discarding_method,
    )

  # -- z safety --------------------------------------------------------------

  async def move_to_safe_z(self) -> List[float]:
    """Move every channel up to its safe Z, and read where that put them.

    Nothing may move in X or Y while a channel is low, so this is the precondition for any
    lateral move. The instrument's initialization procedure does it as a side effect; on a
    machine that is already initialized it has to be asked for.

    Returns:
      Each channel's stop-disk Z, in mm, back to front.
    """
    await self._driver.send_command(module="C0", command="ZA")
    return [await self.request_stop_disk_z(channel) for channel in range(len(self.resources))]
