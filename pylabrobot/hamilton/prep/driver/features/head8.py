"""Head8 — 8MPH head for the Hamilton Prep.

The 8MPH is a ganged head: a single X/Y/Z gantry and a single dispenser piston
drive all 8 probes together. Individual sleeves are mechanically coupled — partial
sleeve engagement produces insufficient grip force and tips fall off. All
operations therefore require all 8 channels simultaneously.

------------------------------
- PickupTips / DropTips: single TipPositionParameters struct; Y = probe-0 reference.
  PickupTips has tipMask (0xFF default) for Hamilton service tooling; DropTips
  has NO tip mask — all probes drop together unconditionally.
- Aspirate / Dispense: StructArray with exactly ONE entry. The gantry moves to
  the probe-0 (row A) reference position and all 8 probes operate simultaneously.
  Channel field = ChannelIndex.MPHChannel.

Physical arrangement
--------------------
Probes are ordered by Y (highest Y = probe 0 = row A). Pitch = PROBE_PITCH_MM.
"""

from __future__ import annotations

import logging
import math
import struct as _struct
from typing import (
  TYPE_CHECKING,
  List,
  Literal,
  NamedTuple,
  Optional,
  Sequence,
  Tuple,
  Union,
  cast,
)

from pylabrobot.hamilton.liquid_class_resolver import get_volumes_and_classes
from pylabrobot.hamilton.liquid_classes import HamiltonLiquidClass
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources import Container, Coordinate, Tip, Trash
from pylabrobot.resources.errors import HasTipError, TooLittleLiquidError
from pylabrobot.resources.hamilton.prep_decks import PrepDeck
from pylabrobot.resources.n_channel_pipettes import (
  SHAFT_DIAMETER,
  SHAFT_LENGTH,
  NChannelPipette,
  TipMountingShaft,
)
from pylabrobot.resources.resource_state import (
  VolumeTransferIntent,
  all_channels_succeeded,
  finalize_volume_ops,
  queue_volume_transfers,
  successes_from_failed_channels,
)
from pylabrobot.resources.tip_rack import TipSpot, tip_origin
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.volume_tracker import does_volume_tracking

from .. import prep_commands as PrepCmd
from ..prep_commands import MPH_OBJECT_PATH
from .pipettes import (
  PipetteChannel,
  Pipettes,
  PipettesConfiguration,
  _absolute_z_from_well,
  _effective_radius,
  _get_container_segments,
  get_mix_parameters,
)
from .pipettes import (
  default_lld_params as _default_lld_params_fn,
)
from .pipettes import (
  lld_for_well as _lld_for_well_fn,
)
from .pipettes import (
  lld_seek_timeout as _lld_seek_timeout,
)
from .pipettes import (
  patch_common_with_cone as _patch_common_with_cone_fn,
)
from .pipettes import (
  resolve_command_version as _resolve_command_version_fn,
)

if TYPE_CHECKING:
  from pylabrobot.resources.deck import Deck

  from ..configuration import DeviceConfiguration
  from ..master import PrepDriver

logger = logging.getLogger(__name__)

PROBE_PITCH_MM: float = 9.0
NUM_PROBES: int = 8
_FULL_TIP_MASK: int = 0xFF
_V2_MPH_CMD_IDS: frozenset = frozenset({29, 30, 31, 32, 33, 34})
_PROBE_POS_TOLERANCE_MM: float = 1.0  # max deviation from expected 9mm pitch before raising


class _Head8LiquidTargets(NamedTuple):
  """Where the ganged head aspirates or dispenses: one trough, or eight wells."""

  resource_name: str
  op_targets: Union[str, List[str]]
  ref_x: float
  ref_y: float
  ref_resource: Container
  volume_containers: List[Container]


def head8_pipette(
  name: str = "head8", size_z: float = PipettesConfiguration.channel_size_z
) -> NChannelPipette:
  """The 8MPH as a resource: one column of eight tip mounting shafts, probe 0 at the back.

  As tall as a channel, its shafts hanging below it as a channel's does. Only the probes' grid is
  modelled, not the body around it. A tip a probe carries is its shaft's child.

  Args:
    name: what to call it.
    size_z: how tall it is above its shafts, in mm.

  Returns:
    The pipette.
  """
  span_y = (NUM_PROBES - 1) * PROBE_PITCH_MM
  return NChannelPipette(
    name=name,
    size_x=SHAFT_DIAMETER,
    size_y=span_y + SHAFT_DIAMETER,
    size_z=size_z,
    # Where a tip is picked up: the axis of probe 0's shaft, at its end.
    reference_point=Coordinate(SHAFT_DIAMETER / 2, span_y + SHAFT_DIAMETER / 2, -SHAFT_LENGTH),
    ordered_items=create_ordered_items_2d(
      TipMountingShaft,
      name_prefix=name,
      num_items_x=1,
      num_items_y=NUM_PROBES,
      dx=0,
      dy=0,
      dz=-SHAFT_LENGTH,
      item_dx=PROBE_PITCH_MM,
      item_dy=PROBE_PITCH_MM,
      tip_pickup_mode="core",
    ),
    model="hamilton_prep_8mph",
  )


class Head8:
  """8-channel Multi-Pipetting Head for the Hamilton Prep.

  All 8 probes must participate in every operation. Partial channel selection
  is rejected at this layer because the head is physically ganged (single drive
  per axis, single piston) and partial sleeve engagement produces insufficient
  grip force.
  """

  def __init__(
    self,
    driver: "PrepDriver",
    *,
    use_v1_aspirate_dispense: bool = False,
  ) -> None:
    """
    Args:
      driver: the driver to send commands through.
      use_v1_aspirate_dispense: whether to aspirate and dispense with the v1 commands.
    """
    self._driver = driver
    # The height to travel at when a command names none, in mm. Setup replaces it with what the device
    # reports.
    self.default_minimum_traverse_height: float = 167.5
    self._use_v1_aspirate_dispense: bool = use_v1_aspirate_dispense
    self.channels: List[PipetteChannel] = []  # built by discover
    self._supports_v2_pipetting: Optional[bool] = None
    # The head's probes, each a shaft carrying the tip it has picked up.
    name = "head8" if driver is None else driver.get_component_name("head8")
    self.resource: NChannelPipette = head8_pipette(name=name)

  @property
  def deck(self) -> Optional["Deck"]:
    """The deck positions are measured from: the driver's."""
    return self._driver.deck

  async def _on_setup(self) -> None:
    reported = await self._driver.request_default_traverse_height()
    if reported is not None:
      self.default_minimum_traverse_height = reported
    await self.discover()
    if self._use_v1_aspirate_dispense:
      self._supports_v2_pipetting = False
      logger.debug("MPH V2 aspirate/dispense probe skipped (use_v1_aspirate_dispense=True)")
    else:
      try:
        supported = await self._probe_v2_support()
      except Exception as e:
        logger.warning("MPH V2 support probe failed: %s", e)
        supported = False
      if not supported:
        raise RuntimeError(
          "V2 aspirate/dispense commands (cmd 29-34) are not supported by this MPH firmware. "
          "Pass use_v1_aspirate_dispense=True to Head8 to use v1 commands instead."
        )
      self._supports_v2_pipetting = True
      logger.debug("MPH V2 aspirate/dispense support: True")

  async def _on_stop(self) -> None:
    # The tips the probes carry are resources on their shafts, and stopping moves none.
    self._supports_v2_pipetting = None

  # -- session / discovery -------------------------------------------------------------------------

  @property
  def _configuration(self) -> "DeviceConfiguration":
    """The device's configuration, as the driver read it at setup.

    Raises:
      RuntimeError: If there is no driver, or it has not read one yet.
    """
    if self._driver is None or self._driver.configuration is None:
      raise RuntimeError("no configuration read; have you called `prep.setup()`?")
    return self._driver.configuration

  def _require_deck(self) -> "Deck":
    """The deck positions are measured from, which is what the firmware counts from.

    Raises:
      RuntimeError: If this was given no deck.
    """
    if self.deck is None:
      raise RuntimeError("no deck to measure positions from; pass one to the driver")
    return self.deck

  async def discover(self) -> None:
    """Find the 8MPH channels in the firmware tree (``MPH Channel Root``) and build `channels`."""
    drive_map = await self._driver.request_channel_drives(root_name="MPH Channel Root")

    def _drive_addr(seq: List[Address], i: int) -> Optional[Address]:
      return seq[i] if i < len(seq) else None

    self.channels = [
      PipetteChannel(
        index=i,
        driver=self._driver,
        sleeve_sensor=_drive_addr(drive_map.sleeve_sensor_addrs, i),
        zdrive=_drive_addr(drive_map.zdrive_addrs, i),
        node_info=_drive_addr(drive_map.node_info_addrs, i),
      )
      for i in range(NUM_PROBES)
    ]

  async def _probe_v2_support(self) -> bool:
    """Return True if the MPH firmware exposes V2 aspirate/dispense (cmds 29-34)."""
    dest = await self._driver.resolve_path(MPH_OBJECT_PATH)
    methods = await self._driver.request_interface_methods(dest, interface_id=1)
    iface1_ids = {m.method_id for m in methods}
    return _V2_MPH_CMD_IDS.issubset(iface1_ids)

  def _resolve_command_version(self, override: Optional[Literal["v1", "v2"]] = None) -> bool:
    return _resolve_command_version_fn(
      self._supports_v2_pipetting,
      self._use_v1_aspirate_dispense,
      override,
      v2_error_hint=(
        "v2 aspirate/dispense commands (cmd 29-34) are not supported by this firmware. "
        "Use command_version='v1' or pass use_v1_aspirate_dispense=True to Head8."
      ),
    )

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  def _resolve_traverse_height(self, minimum_traverse_height_end: Optional[float] = None) -> float:
    """The height to leave the channels at: what is given, else `default_minimum_traverse_height`."""
    if minimum_traverse_height_end is None:
      return self.default_minimum_traverse_height
    return minimum_traverse_height_end

  def _check_reachable(self, axis: Literal["x", "y", "z"], value: float) -> None:
    """Raise unless the head reaches a position along one axis.

    The one gate a position passes through, as `Pipettes._check_reachable` is for the channels. The
    head rides the channels' gantry, so X is theirs.

    TODO: `GetChannelBounds` answers for the channels only, not for the MPH, so Y and Z go
    unchecked. Check them here once a device reports the head's own windows.

    Args:
      axis: which axis - `x` along the gantry, `y` across it, `z` up and down.
      value: where it would be sent, in mm.

    Raises:
      ValueError: If the head cannot reach it.
    """
    if axis != "x":
      return
    pipettes = self._driver.pipettes
    if pipettes is None:
      return
    for channel in range(pipettes.num_channels):
      pipettes._check_reachable(channel, "x", value)

  # -- tips ----------------------------------------------------------------------------------------

  async def sense_tip_presence(self) -> List[Optional[bool]]:
    """Sense whether tips are present on the 8MPH head via the sleeve sensor (GetTipPresent).

    The 8MPH is a single ganged controller — the firmware tree exposes one sleeve
    sensor node (on the probe-0 / channel-0 entry). The result is broadcast across
    all 8 positions since the head picks up and drops all probes together.

    Returns:
      8-element list. True=tips detected, False=no tips, None=sensor unavailable.
    """
    if not self.channels:
      raise RuntimeError("MPH channels not populated; call discover first.")

    addr = self.channels[0].sleeve_sensor
    if addr is None:
      return [None] * NUM_PROBES

    # By name: the method's ids are not the same on every firmware version.
    raw = await self._driver.request_by_name(addr, "GetTipPresent")
    if raw is None or len(raw) < 8:
      result = False
    else:
      val = _struct.unpack_from("<I", raw, 4)[0]
      result = bool(val)
    return [result] * NUM_PROBES

  # -- xyz position --------------------------------------------------------------------------------

  def _mounted_length(self) -> float:
    """How far below the end of probe 0's shaft the tip on it reaches, in mm; 0 with no tip."""
    tip = self.resource.get_item(0).tip
    return float(tip.get_size_z() - tip.fitting_depth) if isinstance(tip, Tip) else 0.0

  def get_reference_point_location(self) -> Optional[Coordinate]:
    """Where the model has probe 0, in mm on the deck: Z at the bottom of what it carries.

    The inverse of `update_location_by_reference_point`. None when nothing models the head yet.
    """
    head, deck = self.resource, self.deck
    if head.location is None or head.parent is None or deck is None:
      return None
    return (
      head.location
      + head.parent.get_location_wrt(deck)
      + head.reference_point
      - Coordinate(0, 0, self._mounted_length())
    )

  def update_location_by_reference_point(
    self, x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None
  ) -> None:
    """Record where the head is on the resource that models it, a child of the arm's.

    As `Pipettes.update_location_by_reference_point` does for a channel. Positions are in the deck's
    frame, a resource is located in its parent's, so the arm's position is taken out first. Does
    nothing when nothing models the head.

    Args:
      x: where probe 0 is, in mm on the deck. Left as it was on the arm when None.
      y: where probe 0 is, in mm on the deck. Left as it was when None.
      z: where the bottom of what probe 0 carries is, in mm on the deck. Left as it was when None.
    """
    head, deck = self.resource, self.deck
    if head.location is None or head.parent is None or deck is None:
      return
    here, on_the_arm, anchor = (
      head.location,
      head.parent.get_location_wrt(deck),
      head.reference_point,
    )
    shaft_z = None if z is None else z + self._mounted_length()
    head.location = Coordinate(
      here.x if x is None else x - on_the_arm.x - anchor.x,
      here.y if y is None else y - on_the_arm.y - anchor.y,
      here.z if shaft_z is None else shaft_z - on_the_arm.z - anchor.z,
    )

  def _record_target(self, x: float, y: float, z: float) -> None:
    """Record where a command sends the head, as it is sent: the read in its `finally` has the last word.

    Args:
      x: where probe 0 is sent, in mm on the deck.
      y: where probe 0 is sent, in mm on the deck.
      z: where the bottom of what probe 0 carries is sent, in mm on the deck.
    """
    arm, at = self._driver.x_arm, self.get_reference_point_location()
    if arm is not None and arm.resource is not None and at is not None:
      # The head sits a fixed distance along the arm, so the arm moves as far as the head does.
      arm.update_location_by_reference_point(
        arm.resource.get_location_wrt(self._require_deck()).x
        + arm.configuration.reference_point_from_left
        + x
        - at.x
      )
    self.update_location_by_reference_point(y=y, z=z)

  async def _record_where_it_stopped(self) -> None:
    """Read where the head, the arm and the channels came to rest, and record it. For a `finally`.

    One read answers for all of them. Its own failure is logged and swallowed by the arm's read.
    """
    if self._driver.x_arm is not None:
      await self._driver.x_arm._record_where_it_stopped()

  async def move_to_position(
    self,
    x: float,
    y: float,
    z: float,
    *,
    via_lane: bool = False,
  ) -> None:
    """Move the ganged 8-channel head to absolute deck ``(x, y, z)`` (mm).

    Sends :class:`~prep_commands.MphMoveToPosition` or
    :class:`~prep_commands.MphMoveToPositionViaLane` on ``MLPrepRoot.MphRoot.MPH``.
    One pose for the whole head — unlike independent-channel ``move_to_position``,
    there are no per-channel ``y``/``z`` lists.

    Args:
      x: Gantry X.
      y: Gantry Y at the probe-0 (row A) reference.
      z: Z height (e.g. traverse).
      via_lane: Use lane-aware move when True.
    """
    self._check_reachable("x", x)
    self._check_reachable("y", y)
    self._check_reachable("z", z)
    self._record_target(x, y, z)
    try:
      if via_lane:
        await self._driver.send_command(
          PrepCmd.MphMoveToPositionViaLane(x_position=x, y_position=y, z_position=z)
        )
      else:
        await self._driver.send_command(
          PrepCmd.MphMoveToPosition(x_position=x, y_position=y, z_position=z)
        )
    finally:
      await self._record_where_it_stopped()

  async def move_to_safe_z(self) -> float:
    """Raise the head straight up to its traverse height, where it stands in X and Y.

    An ordinary move to a known height, as the STAR's heads raise: `move_to_position` to where the
    device reports the head, at `default_minimum_traverse_height`.

    Returns:
      Where the bottom of what probe 0 carries is sent, in mm.

    Raises:
      RuntimeError: If nothing models where the head is.
    """
    await self._record_where_it_stopped()
    at = self.get_reference_point_location()
    if at is None:
      raise RuntimeError("where the head is is not known; have you called `prep.setup()`?")
    z = self.default_minimum_traverse_height - self._mounted_length()
    await self.move_to_position(at.x, at.y, z)
    return z

  # ----------------------------------------
  # Tips and liquid handling
  # ----------------------------------------

  # -- tip pickup / drop ---------------------------------------------------------------------------

  def shaft(self, channel: int) -> TipMountingShaft:
    """The mounting shaft for one channel.

    Args:
      channel: which channel, 0 at the back (row A).
    """
    return self.resource.get_item(channel)

  def get_mounted_tip(self, channel: int) -> Optional[Tip]:
    """The tip on one channel's shaft, or ``None`` if that shaft is empty.

    Args:
      channel: which channel, 0 at the back (row A).
    """
    tip = self.shaft(channel).tip
    return tip if isinstance(tip, Tip) else None

  def get_mounted_tips(self) -> List[Optional[Tip]]:
    """Tips currently mounted on the 8MPH (``None`` if empty): what each channel's shaft carries."""
    return [self.get_mounted_tip(i) for i in range(NUM_PROBES)]

  def _require_mounted_tips(self) -> List[Tip]:
    tips: List[Tip] = []
    for tip in self.get_mounted_tips():
      if tip is None:
        raise RuntimeError("No tips mounted on head8; call pick_up_tips first.")
      tips.append(tip)
    return tips

  def _require_mounted_tip(self) -> Tip:
    return self._require_mounted_tips()[0]

  async def pick_up_tips(
    self,
    tip_spots: Sequence[TipSpot],
    use_channels: Optional[Sequence[int]] = None,
    offset: Coordinate = Coordinate.zero(),
    minimum_traverse_height_start: Optional[float] = None,
    z_seek_offset: Optional[float] = None,
    seek_speed: float = 15.0,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
    enable_tadm: bool = False,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Pick up a whole column of tips on the head's shafts.

    In order: the head travels over the spots at the starting height, descends to the seek height,
    presses onto the tips until they are seated, and rises to the ending height. The head takes the
    column at once, so every shaft takes part.

    Args:
      tip_spots: the spot each shaft takes a tip from, one column of them.
      use_channels: which shafts take them. The head takes all of them, so this only says so.
      offset: how far the column's spots are missed by, in mm.
      minimum_traverse_height_start: the height to travel over the spots at, in mm. The ending
        height when None.
      z_seek_offset: how far above the tip to stop descending before pressing on, in mm. A height
        the tip type decides when None.
      seek_speed: how fast to press onto the tips, in mm/s.
      dispenser_volume: air to hold in the dispenser while picking up, in ul.
      dispenser_speed: how fast to move that air, in ul/s.
      enable_tadm: whether to record the pressure through the pick-up.
      minimum_traverse_height_end: the height to leave the head at, in mm.
        `default_minimum_traverse_height` when None.
    """
    tip_spots = list(tip_spots)
    use_channels = list(use_channels) if use_channels is not None else list(range(NUM_PROBES))
    self._require_all_channels(use_channels, "pick_up_tips")
    if len(tip_spots) != NUM_PROBES:
      raise ValueError(f"pick_up_tips requires {NUM_PROBES} tip spots, got {len(tip_spots)}")
    resolved_end = self._resolve_traverse_height(minimum_traverse_height_end)

    for ch in use_channels:
      if self.shaft(ch).has_tip():
        raise RuntimeError(f"Channel {ch} already has a tip")
    tips = [s.tip_for_pickup() for s in tip_spots]
    ref_spot = tip_spots[0]
    tip = tips[0]
    rack = ref_spot.parent
    logger.info(
      "[Prep MPH] pick_up_tips: rack=%s, tip_spots=%s",
      rack.name if rack is not None else ref_spot.name,
      [s.name.rsplit("_", 1)[-1] for s in tip_spots],
    )
    loc = ref_spot.get_location_wrt(self._require_deck(), "c", "c", "t") + offset

    self._check_reachable("x", loc.x)
    # Over the spots first, so the pick-up itself is straight down
    traverse_h = (
      resolved_end if minimum_traverse_height_start is None else minimum_traverse_height_start
    )
    await self.move_to_position(loc.x, loc.y, traverse_h)

    tip_position = PrepCmd.TipPositionParameters.for_op(
      PrepCmd.ChannelIndex.MPHChannel, loc, tip, z_seek_offset=z_seek_offset
    )
    tip_definition = PrepCmd.TipPickupParameters(
      default_values=False,
      volume=tip.maximal_volume,
      length=tip.get_size_z() - tip.fitting_depth,
      tip_type=PrepCmd.TipTypes.StandardVolume,
      has_filter=tip.has_filter,
      is_needle=tip.maximal_volume == 0,  # a needle is closed: it holds no liquid
      is_tool=False,
    )

    picked_up = {ch: False for ch in use_channels}
    self._record_target(loc.x, loc.y, resolved_end)
    try:
      await self._driver.send_command(
        PrepCmd.MphPickupTips(
          tip_position=tip_position,
          final_z=resolved_end,
          seek_speed=seek_speed,
          tip_definition=tip_definition,
          enable_tadm=enable_tadm,
          dispenser_volume=dispenser_volume,
          dispenser_speed=dispenser_speed,
          tip_mask=_FULL_TIP_MASK,
        )
      )
      picked_up = all_channels_succeeded(use_channels)
    except ChannelizedError as e:
      # It can fail on some shafts and not others; the device says which
      picked_up = successes_from_failed_channels(use_channels, e.errors)
      raise
    finally:
      # A tip is a resource: once the device has it, it leaves its spot for the probe's shaft
      for ch, taken in zip(use_channels, tips):
        if picked_up[ch]:
          self.shaft(ch).mount_tip(taken)
      # Read once the tips are on the model: Z is reported at their bottom.
      await self._record_where_it_stopped()

  async def drop_tips(
    self,
    destinations: Sequence[Union[TipSpot, Trash]],
    use_channels: Optional[Sequence[int]] = None,
    offset: Coordinate = Coordinate.zero(),
    z_seek_offset: Optional[float] = None,
    seek_speed: float = 15.0,
    tip_roll_off_distance: float = 0.0,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Drop the head's tips into tip spots, or into the waste.

    In order: the head descends to the seek height with the tips on it, lets go, and rises to the
    ending height. The head drops the column at once, so every shaft takes part.

    Args:
      destinations: the spot each shaft drops its tip into, or the waste. Waste drops use the
        PrepDeck's `waste_mph` position.
      use_channels: which shafts drop them. The head drops all of them, so this only says so.
      offset: how far the destinations are missed by, in mm.
      z_seek_offset: how far above the destination the tip bottom stops, in mm. A height the drop
        decides when None.
      seek_speed: how fast to descend onto the destinations, in mm/s.
      tip_roll_off_distance: how far the tips are rolled off as they are released, in mm.
      minimum_traverse_height_end: the height to leave the head at, in mm.
        `default_minimum_traverse_height` when None.
    """
    destinations = list(destinations)
    use_channels = list(use_channels) if use_channels is not None else list(range(NUM_PROBES))
    self._require_all_channels(use_channels, "drop_tips")
    if len(destinations) != NUM_PROBES:
      raise ValueError(f"drop_tips requires {NUM_PROBES} destinations, got {len(destinations)}")
    tip = self._require_mounted_tip()
    resolved_end = self._resolve_traverse_height(minimum_traverse_height_end)

    ref_spot = destinations[0]
    is_trash = isinstance(ref_spot, Trash)
    dest = ref_spot if is_trash else ref_spot.parent
    logger.info(
      "[Prep MPH] drop_tips: dest=%s, resources=%s",
      dest.name if dest is not None else ref_spot.name,
      [s.name.rsplit("_", 1)[-1] for s in destinations],
    )

    deck = self._require_deck()
    if is_trash:
      if not isinstance(deck, PrepDeck):
        raise RuntimeError("tips are discarded into a PrepDeck's waste")
      waste = deck.waste_positions.get("waste_mph")
      if waste is None:
        raise RuntimeError("the deck has no waste position 'waste_mph'")
      loc = waste.get_location_wrt(deck, "c", "c", "t")
      # The waste site locates the tip's end; the drop parameters locate its collar.
      loc = Coordinate(loc.x, loc.y, loc.z + tip.get_size_z() - tip.collar_height)
    else:
      loc = ref_spot.get_location_wrt(deck, "c", "c", "t") + offset
    drop_type = PrepCmd.TipDropType.Stall if is_trash else PrepCmd.TipDropType.FixedHeight

    tip_position = PrepCmd.TipDropParameters.for_op(
      PrepCmd.ChannelIndex.MPHChannel,
      loc,
      tip,
      z_seek_offset=z_seek_offset,
      drop_type=drop_type,
    )
    roll_off = 3.0 if (is_trash and tip_roll_off_distance == 0.0) else tip_roll_off_distance
    mounted = self._require_mounted_tips()
    for ch in use_channels:
      used = mounted[ch].tracker.get_used_volume()
      if not mounted[ch].tracker.is_disabled and used > 1e-6:
        logger.warning("dropping the tip on channel %d with %s uL in it", ch, used)
    spots = [d for d in destinations if isinstance(d, TipSpot)]
    if len({id(spot) for spot in spots}) != len(spots):
      raise ValueError("each tip must go into a spot of its own")
    for spot in spots:
      if spot.tracks_tips and spot.tip is not None:
        raise HasTipError(f"{spot.name} already holds a tip")

    dropped = {ch: False for ch in use_channels}
    # The drop ends with the shaft at the final height, the tips still on the model until it answers.
    self._record_target(loc.x, loc.y, resolved_end - self._mounted_length())
    try:
      await self._driver.send_command(
        PrepCmd.MphDropTips(
          tip_position=tip_position,
          final_z=resolved_end,
          seek_speed=seek_speed,
          tip_roll_off_distance=roll_off,
        )
      )
      dropped = all_channels_succeeded(use_channels)
    except ChannelizedError as e:
      # It can fail on some shafts and not others; the device says which
      dropped = successes_from_failed_channels(use_channels, e.errors)
      raise
    finally:
      # Once the device has let go, a tip goes into its spot, or to nothing in the waste
      for ch, destination in zip(use_channels, destinations):
        if not dropped[ch]:
          continue
        released = self.shaft(ch).release_tip()
        if isinstance(destination, TipSpot) and destination.tracks_tips:
          destination.assign_tip(cast(Tip, released))
      await self._record_where_it_stopped()

  async def return_tips(self, use_channels: Optional[Sequence[int]] = None, **kwargs) -> None:
    """Return all eight tips to the spots they were picked up from.

    Args:
      use_channels: all eight channels, in order. Defaults to all eight channels.
      kwargs: passed on to `drop_tips`.

    Raises:
      ValueError: If `use_channels` does not select all eight channels in order.
      RuntimeError: If there is no deck, any channel has no tip, or a tip's original spot
        is not on the deck.
    """
    channels = list(use_channels) if use_channels is not None else list(range(NUM_PROBES))
    self._require_all_channels(channels, "return_tips")
    deck = self._require_deck()
    spots: List[TipSpot] = []
    for ch, tip in enumerate(self._require_mounted_tips()):
      spot = tip_origin(tip, deck)
      if spot is None:
        raise RuntimeError(f"the spot channel {ch}'s tip {tip.name} came from is not on the deck")
      spots.append(spot)
    await self.drop_tips(spots, use_channels=channels, **kwargs)

  async def discard_tips(self, use_channels: Optional[Sequence[int]] = None, **kwargs) -> None:
    """Discard all eight tips into the deck's waste at its MPH position.

    Do nothing when no tips are mounted. A partly loaded head is rejected.

    Args:
      use_channels: all eight channels, in order. Defaults to all eight channels.
      kwargs: passed on to `drop_tips`.

    Raises:
      ValueError: If `use_channels` does not select all eight channels in order.
      RuntimeError: If there is no deck, only some channels carry tips, or the deck is not a
        PrepDeck with a waste block and an MPH waste position.
    """
    channels = list(use_channels) if use_channels is not None else list(range(NUM_PROBES))
    self._require_all_channels(channels, "discard_tips")
    deck = self._require_deck()
    if all(tip is None for tip in self.get_mounted_tips()):
      return
    if not isinstance(deck, PrepDeck):
      raise RuntimeError("tips are discarded into a PrepDeck's waste block")
    waste = deck.waste_block
    if waste is None:
      raise RuntimeError("tips are discarded into the deck's waste block; this deck has none")
    await self.drop_tips([waste] * NUM_PROBES, use_channels=channels, **kwargs)

  # -- shared LLD / TADM resolution helpers --------------------------------------------------------

  @staticmethod
  def _get_lld_mode(
    lld_mode: Optional[Pipettes.LLDMode],
    auto_surface_following: bool,
    surface_following_distance: float,
    liquid_height: Optional[float],
    z_fluid: Optional[float],
  ) -> Optional[Pipettes.LLDMode]:
    """The mode a call runs with: CAPACITIVE when None under auto surface following.

    Raises:
      ValueError: Auto surface following beside a distance, or under OFF without a liquid height.
    """
    if not auto_surface_following:
      return lld_mode
    if surface_following_distance != 0.0:
      raise ValueError(
        "auto_surface_following beside a surface_following_distance: give one of them"
      )
    if lld_mode is None:
      return Pipettes.LLDMode.CAPACITIVE
    if lld_mode == Pipettes.LLDMode.OFF and liquid_height is None and z_fluid is None:
      raise ValueError(
        "auto_surface_following under lld_mode OFF needs a liquid_height to follow from"
      )
    return lld_mode

  def _resolve_effective_lld(
    self,
    lld_mode: Optional[Pipettes.LLDMode],
    lld: Optional[PrepCmd.LldParameters],
    *,
    allowed_modes: Optional[frozenset] = None,
  ) -> bool:
    """Determine whether LLD is active for this MPH pipetting call.

    Unlike the pipetting channels (which take a per-channel list), the MPH accepts a
    single LLDMode because the ganged head operates as one unit.
    """
    if lld_mode is not None:
      if lld_mode == Pipettes.LLDMode.ZTOUCH:
        raise ValueError("ZTOUCH is run for the pipetting channels only, not the 8MPH")
      if lld_mode != Pipettes.LLDMode.OFF:
        if allowed_modes is not None and lld_mode not in allowed_modes:
          raise ValueError(
            f"Dispense does not support {lld_mode.name} LLD — only CAPACITIVE or OFF. "
            "Pressure-based LLD requires aspiration (plunger movement)."
          )
        if lld_mode in (Pipettes.LLDMode.PRESSURE, Pipettes.LLDMode.DUAL):
          raise NotImplementedError(
            f"{lld_mode.name} LLD is not supported on the Prep: its pressure search has not "
            "detected liquid, and a missed search keeps drawing. Use CAPACITIVE."
          )
        return True
      return False
    return lld is not None

  # -- shared channel resolution -------------------------------------------------------------------

  def _require_all_channels(self, use_channels: List[int], op: str) -> None:
    """Raise ValueError unless use_channels is exactly [0..7].

    The 8MPH is a ganged head — all 8 probes must participate in every operation.
    Partial channel selection produces insufficient tip grip force (physical
    constraint confirmed via firmware/hardware inspection).
    """
    if list(use_channels) != list(range(NUM_PROBES)):
      raise ValueError(
        f"Head8.{op}: the 8MPH is a fully-ganged head — all {NUM_PROBES} "
        f"channels must participate. Received use_channels={use_channels}. "
        "Partial tip pickup/drop/aspirate/dispense is not physically supported."
      )

  def _resolve_probe_positions(self, wells) -> List[float]:
    """Compute expected probe Y positions and validate actual well Ys match.

    Probe 0 = row A = highest Y. Expected position for probe i:
      wells[0].y - i * PROBE_PITCH_MM

    Works for any labware at 9mm pitch: standard 96-well columns, or
    interleaved 384-well selections (every other row = 2 × 4.5mm = 9mm).

    Returns the expected Y values (one per probe) for logging/accounting.
    Raises ValueError if any well deviates beyond _PROBE_POS_TOLERANCE_MM.
    """
    ref_y = wells[0].get_absolute_location("c", "c", "cavity_bottom").y
    expected_ys = [ref_y - i * PROBE_PITCH_MM for i in range(len(wells))]

    mismatches = []
    for i, (well, exp_y) in enumerate(zip(wells, expected_ys)):
      actual_y = well.get_absolute_location("c", "c", "cavity_bottom").y
      if abs(actual_y - exp_y) > _PROBE_POS_TOLERANCE_MM:
        mismatches.append(
          f"  probe {i} ({well.name}): expected y={exp_y:.2f}, actual y={actual_y:.2f}"
        )

    if mismatches:
      actual_ys = [round(w.get_absolute_location("c", "c", "cavity_bottom").y, 2) for w in wells]
      raise ValueError(
        f"Wells are not at {PROBE_PITCH_MM} mm probe pitch from wells[0]. "
        f"Pass wells in row-A-first order at {PROBE_PITCH_MM} mm spacing "
        f"(for 384-well plates: every other row).\n"
        + "\n".join(mismatches)
        + f"\nActual Y values: {actual_ys}"
      )

    return expected_ys

  def _validate_container_span(self, container) -> None:
    """Raise ValueError if the container is too narrow for all 8 channels.

    Minimum Y span = (NUM_PROBES - 1) * PROBE_PITCH_MM = 63 mm.
    """
    min_span = (NUM_PROBES - 1) * PROBE_PITCH_MM
    span = container.get_size_y()
    if span < min_span:
      raise ValueError(
        f"Container '{container.name}' Y span ({span:.1f} mm) is too narrow for "
        f"{NUM_PROBES} channels at {PROBE_PITCH_MM} mm pitch "
        f"(minimum {min_span:.1f} mm required)."
      )

  def _resolve_liquid_targets(
    self,
    containers: Sequence[Container],
    op: str,
  ) -> _Head8LiquidTargets:
    """Resolve `containers` as one trough spanning the head, or eight wells at channel pitch.

    Args:
      containers: one wide container, or exactly eight wells in row-A-first order.
      op: the public method name, for error messages.

    Returns:
      Deck geometry and per-channel volume bookkeeping targets.

    Raises:
      ValueError: If the count is not 1 or 8, a trough is too narrow, or wells are mistimed.
    """
    containers_list = list(containers)
    if len(containers_list) == 1:
      container = containers_list[0]
      self._validate_container_span(container)
      resource_name = container.parent.name if container.parent is not None else container.name
      loc = container.get_location_wrt(self._require_deck(), "c", "c", "cavity_bottom")
      return _Head8LiquidTargets(
        resource_name=resource_name,
        op_targets=container.name,
        ref_x=loc.x,
        ref_y=loc.y + 3.5 * PROBE_PITCH_MM,
        ref_resource=container,
        volume_containers=[container] * NUM_PROBES,
      )
    if len(containers_list) != NUM_PROBES:
      raise ValueError(
        f"{op} requires 1 container or {NUM_PROBES} wells, got {len(containers_list)}"
      )
    self._resolve_probe_positions(containers_list)
    resource_name = (
      containers_list[0].parent.name
      if containers_list[0].parent is not None
      else containers_list[0].name
    )
    ref_loc = containers_list[0].get_location_wrt(self._require_deck(), "c", "c", "cavity_bottom")
    return _Head8LiquidTargets(
      resource_name=resource_name,
      op_targets=[w.name.rsplit("_", 1)[-1] for w in containers_list],
      ref_x=ref_loc.x,
      ref_y=ref_loc.y,
      ref_resource=containers_list[0],
      volume_containers=containers_list,
    )

  def _validate_mix(self, mix: Mix, volume_containers: Sequence[Container]) -> None:
    """Check a `Mix` against the firmware's mix block, the mounted tips and the liquid present.

    Args:
      mix: the shared mix, per probe.
      volume_containers: the container each probe mixes in; a trough repeats for every probe.

    Raises:
      ValueError: If repetitions, volume or flow rate are out of range, the mix asks for
        surface following, or the volume exceeds a tip's free room.
      TooLittleLiquidError: If volume tracking is on and a container holds less than it mixes.
    """
    if (
      isinstance(mix.repetitions, bool)
      or not isinstance(mix.repetitions, int)
      or not 1 <= mix.repetitions <= 255
    ):
      raise ValueError("Mix repetitions must be an integer from 1 to 255")
    if not math.isfinite(mix.volume) or mix.volume <= 0:
      raise ValueError("Mix volume must be finite and positive")
    if not math.isfinite(mix.flow_rate) or mix.flow_rate <= 0:
      raise ValueError("Mix flow_rate must be finite and positive")
    if mix.auto_surface_following or mix.surface_following_distance != 0:
      raise ValueError("Prep head8 mixing does not support surface following")
    for tip in self._require_mounted_tips():
      if mix.volume > tip.maximal_volume - tip.tracker.get_used_volume():
        raise ValueError("Mix volume exceeds available tip capacity")
    if not does_volume_tracking():
      return
    # a trough gives every probe's mix volume at once
    for container in {id(c): c for c in volume_containers}.values():
      needed = mix.volume * sum(c is container for c in volume_containers)
      if not container.tracker.is_disabled and container.tracker.get_used_volume() < needed:
        raise TooLittleLiquidError(f"'{container.name}' has too little liquid for the mix")

  # -- aspirate: assemble --------------------------------------------------------------------------

  def _assemble_aspirate_v2(
    self,
    ref_x: float,
    ref_y: float,
    volume: float,
    tube_radius: float,
    minimum_traverse_height_end: float,
    z_minimum: float,
    z_fluid: float,
    z_air: float,
    z_bottom_search_offset: float,
    settling_time: float,
    transport_air_volume: float,
    z_liquid_exit_speed: float,
    prewet_volume: float,
    blowout_volume: float,
    flow_rate: Optional[float],
    segments: List[PrepCmd.SegmentDescriptor],
    effective_lld: bool,
    is_tadm: bool,
    lld_params: PrepCmd.LldParameters,
    lld_defaults: Pipettes._LldDefaults,
    tadm: PrepCmd.TadmParameters,
    mix: PrepCmd.MixParameters,
  ) -> Union[
    PrepCmd.AspirateParametersLldAndTadm2,
    PrepCmd.AspirateParametersLldAndMonitoring2,
    PrepCmd.AspirateParametersNoLldAndTadm2,
    PrepCmd.AspirateParametersNoLldAndMonitoring2,
  ]:
    self._check_reachable("x", ref_x)
    aspirate = PrepCmd.AspirateParameters(
      default_values=False,
      x_position=ref_x,
      y_position=ref_y,
      prewet_volume=prewet_volume,
      blowout_volume=blowout_volume,
    )
    common = PrepCmd.CommonParameters.for_op(
      volume,
      tube_radius,
      flow_rate=flow_rate,
      z_final=minimum_traverse_height_end,
      z_minimum=z_minimum,
      z_liquid_exit_speed=z_liquid_exit_speed,
      transport_air_volume=transport_air_volume,
      settling_time=settling_time,
    )
    no_lld = PrepCmd.NoLldParameters.for_fixed_z(
      z_fluid=z_fluid, z_air=z_air, z_bottom_search_offset=z_bottom_search_offset
    )
    adc = PrepCmd.AdcParameters.default()

    if effective_lld and is_tadm:
      return PrepCmd.AspirateParametersLldAndTadm2(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        aspirate=aspirate,
        container_description=segments,
        common=common,
        lld=lld_params,
        p_lld=lld_defaults.p_lld,
        c_lld=lld_defaults.c_lld,
        mix=mix,
        tadm=tadm,
        adc=adc,
      )
    elif effective_lld:
      return PrepCmd.AspirateParametersLldAndMonitoring2(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        aspirate=aspirate,
        container_description=segments,
        common=common,
        lld=lld_params,
        p_lld=lld_defaults.p_lld,
        c_lld=lld_defaults.c_lld,
        mix=mix,
        aspirate_monitoring=PrepCmd.AspirateMonitoringParameters.default(),
        adc=adc,
      )
    elif is_tadm:
      return PrepCmd.AspirateParametersNoLldAndTadm2(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        aspirate=aspirate,
        container_description=segments,
        common=common,
        no_lld=no_lld,
        mix=mix,
        adc=adc,
        tadm=tadm,
      )
    else:
      return PrepCmd.AspirateParametersNoLldAndMonitoring2(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        aspirate=aspirate,
        container_description=segments,
        common=common,
        no_lld=no_lld,
        mix=mix,
        adc=adc,
        aspirate_monitoring=PrepCmd.AspirateMonitoringParameters.default(),
      )

  def _assemble_aspirate_v1(
    self,
    ref_x: float,
    ref_y: float,
    volume: float,
    tube_radius: float,
    minimum_traverse_height_end: float,
    z_minimum: float,
    z_fluid: float,
    z_air: float,
    z_bottom_search_offset: float,
    settling_time: float,
    transport_air_volume: float,
    z_liquid_exit_speed: float,
    prewet_volume: float,
    blowout_volume: float,
    flow_rate: Optional[float],
    segments: List[PrepCmd.SegmentDescriptor],
    effective_lld: bool,
    is_tadm: bool,
    lld_params: PrepCmd.LldParameters,
    lld_defaults: Pipettes._LldDefaults,
    tadm: PrepCmd.TadmParameters,
    mix: PrepCmd.MixParameters,
  ) -> Union[
    PrepCmd.AspirateParametersLldAndTadm,
    PrepCmd.AspirateParametersLldAndMonitoring,
    PrepCmd.AspirateParametersNoLldAndTadm,
    PrepCmd.AspirateParametersNoLldAndMonitoring,
  ]:
    self._check_reachable("x", ref_x)
    aspirate = PrepCmd.AspirateParameters(
      default_values=False,
      x_position=ref_x,
      y_position=ref_y,
      prewet_volume=prewet_volume,
      blowout_volume=blowout_volume,
    )
    common_v2 = PrepCmd.CommonParameters.for_op(
      volume,
      tube_radius,
      flow_rate=flow_rate,
      z_final=minimum_traverse_height_end,
      z_minimum=z_minimum,
      z_liquid_exit_speed=z_liquid_exit_speed,
      transport_air_volume=transport_air_volume,
      settling_time=settling_time,
    )
    common = _patch_common_with_cone_fn(common_v2, segments)
    no_lld = PrepCmd.NoLldParameters.for_fixed_z(
      z_fluid=z_fluid, z_air=z_air, z_bottom_search_offset=z_bottom_search_offset
    )
    adc = PrepCmd.AdcParameters.default()

    if effective_lld and is_tadm:
      return PrepCmd.AspirateParametersLldAndTadm(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        aspirate=aspirate,
        common=common,
        lld=lld_params,
        p_lld=lld_defaults.p_lld,
        c_lld=lld_defaults.c_lld,
        mix=mix,
        tadm=tadm,
        adc=adc,
      )
    elif effective_lld:
      return PrepCmd.AspirateParametersLldAndMonitoring(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        aspirate=aspirate,
        common=common,
        lld=lld_params,
        p_lld=lld_defaults.p_lld,
        c_lld=lld_defaults.c_lld,
        mix=mix,
        aspirate_monitoring=PrepCmd.AspirateMonitoringParameters.default(),
        adc=adc,
      )
    elif is_tadm:
      return PrepCmd.AspirateParametersNoLldAndTadm(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        aspirate=aspirate,
        common=common,
        no_lld=no_lld,
        mix=mix,
        adc=adc,
        tadm=tadm,
      )
    else:
      return PrepCmd.AspirateParametersNoLldAndMonitoring(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        aspirate=aspirate,
        common=common,
        no_lld=no_lld,
        mix=mix,
        adc=adc,
        aspirate_monitoring=PrepCmd.AspirateMonitoringParameters.default(),
      )

  # Command dispatch tables: (effective_lld, is_tadm, use_v2) → command class
  _ASPIRATE_CMD = {
    (True, True, True): PrepCmd.MphAspirateWithLldTadm2,
    (True, True, False): PrepCmd.MphAspirateWithLldTadm,
    (True, False, True): PrepCmd.MphAspirateWithLld2,
    (True, False, False): PrepCmd.MphAspirateWithLld,
    (False, True, True): PrepCmd.MphAspirateTadm2,
    (False, True, False): PrepCmd.MphAspirateTadm,
    (False, False, True): PrepCmd.MphAspirateNoLldMonitoring2,
    (False, False, False): PrepCmd.MphAspirateNoLldMonitoring,
  }

  # -- dispense: assemble --------------------------------------------------------------------------

  def _assemble_dispense_v2(
    self,
    ref_x: float,
    ref_y: float,
    volume: float,
    tube_radius: float,
    minimum_traverse_height_end: float,
    z_minimum: float,
    z_fluid: float,
    z_air: float,
    z_bottom_search_offset: float,
    settling_time: float,
    transport_air_volume: float,
    z_liquid_exit_speed: float,
    stop_back_volume: float,
    cutoff_speed: float,
    flow_rate: Optional[float],
    segments: List[PrepCmd.SegmentDescriptor],
    effective_lld: bool,
    lld_params: PrepCmd.LldParameters,
    lld_defaults: Pipettes._LldDefaults,
  ) -> Union[PrepCmd.DispenseParametersLld2, PrepCmd.DispenseParametersNoLld2]:
    dispense = PrepCmd.DispenseParameters(
      default_values=False,
      x_position=ref_x,
      y_position=ref_y,
      stop_back_volume=stop_back_volume,
      cutoff_speed=cutoff_speed,
    )
    common = PrepCmd.CommonParameters.for_op(
      volume,
      tube_radius,
      flow_rate=flow_rate,
      z_final=minimum_traverse_height_end,
      z_minimum=z_minimum,
      z_liquid_exit_speed=z_liquid_exit_speed,
      transport_air_volume=transport_air_volume,
      settling_time=settling_time,
    )
    mix = PrepCmd.MixParameters.default()
    adc = PrepCmd.AdcParameters.default()
    tadm = PrepCmd.TadmParameters.default()

    if effective_lld:
      return PrepCmd.DispenseParametersLld2(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        dispense=dispense,
        container_description=segments,
        common=common,
        lld=lld_params,
        c_lld=lld_defaults.c_lld,
        mix=mix,
        adc=adc,
        tadm=tadm,
      )
    else:
      return PrepCmd.DispenseParametersNoLld2(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        dispense=dispense,
        container_description=segments,
        common=common,
        no_lld=PrepCmd.NoLldParameters.for_fixed_z(
          z_fluid=z_fluid, z_air=z_air, z_bottom_search_offset=z_bottom_search_offset
        ),
        mix=mix,
        adc=adc,
        tadm=tadm,
      )

  def _assemble_dispense_v1(
    self,
    ref_x: float,
    ref_y: float,
    volume: float,
    tube_radius: float,
    minimum_traverse_height_end: float,
    z_minimum: float,
    z_fluid: float,
    z_air: float,
    z_bottom_search_offset: float,
    settling_time: float,
    transport_air_volume: float,
    z_liquid_exit_speed: float,
    stop_back_volume: float,
    cutoff_speed: float,
    flow_rate: Optional[float],
    segments: List[PrepCmd.SegmentDescriptor],
    effective_lld: bool,
    lld_params: PrepCmd.LldParameters,
    lld_defaults: Pipettes._LldDefaults,
  ) -> Union[PrepCmd.DispenseParametersLld, PrepCmd.DispenseParametersNoLld]:
    dispense = PrepCmd.DispenseParameters(
      default_values=False,
      x_position=ref_x,
      y_position=ref_y,
      stop_back_volume=stop_back_volume,
      cutoff_speed=cutoff_speed,
    )
    common_v2 = PrepCmd.CommonParameters.for_op(
      volume,
      tube_radius,
      flow_rate=flow_rate,
      z_final=minimum_traverse_height_end,
      z_minimum=z_minimum,
      z_liquid_exit_speed=z_liquid_exit_speed,
      transport_air_volume=transport_air_volume,
      settling_time=settling_time,
    )
    common = _patch_common_with_cone_fn(common_v2, segments)
    mix = PrepCmd.MixParameters.default()
    adc = PrepCmd.AdcParameters.default()
    tadm = PrepCmd.TadmParameters.default()

    if effective_lld:
      return PrepCmd.DispenseParametersLld(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        dispense=dispense,
        common=common,
        lld=lld_params,
        c_lld=lld_defaults.c_lld,
        mix=mix,
        adc=adc,
        tadm=tadm,
      )
    else:
      return PrepCmd.DispenseParametersNoLld(
        default_values=False,
        channel=PrepCmd.ChannelIndex.MPHChannel,
        dispense=dispense,
        common=common,
        no_lld=PrepCmd.NoLldParameters.for_fixed_z(
          z_fluid=z_fluid, z_air=z_air, z_bottom_search_offset=z_bottom_search_offset
        ),
        mix=mix,
        adc=adc,
        tadm=tadm,
      )

  # Command dispatch tables: (effective_lld, use_v2) → command class
  _DISPENSE_CMD = {
    (True, True): PrepCmd.MphDispenseWithLld2,
    (True, False): PrepCmd.MphDispenseWithLld,
    (False, True): PrepCmd.MphDispenseNoLld2,
    (False, False): PrepCmd.MphDispenseNoLld,
  }

  # -- aspirate / dispense orchestrators -----------------------------------------------------------

  def _get_volume_and_class(
    self,
    containers: Sequence[Container],
    volume: float,
    hamilton_liquid_classes: Optional[
      Union[HamiltonLiquidClass, List[Optional[HamiltonLiquidClass]]]
    ],
    jet: bool,
    blow_out: bool,
    disable_volume_correction: bool,
  ) -> Tuple[float, float, Optional[HamiltonLiquidClass]]:
    """The liquid, the piston volume that moves it, and the one class the 8 probes share.

    Args:
      containers: per probe.
      volume: the liquid per probe, in uL; the piston volume with `disable_volume_correction`.
      hamilton_liquid_classes: one for all, or one per probe; looked up per probe's tip, water,
        `jet` and `blow_out` when None.
      jet: for the lookup.
      blow_out: for the lookup.
      disable_volume_correction: send `volume` as given, with no class.

    Raises:
      ValueError: Not one class or 8, a class beside `disable_volume_correction`, no class known
        for a probe's tip, or probes with different classes: one piston moves them all.
    """
    given: Optional[List[Optional[HamiltonLiquidClass]]]
    if hamilton_liquid_classes is None or isinstance(hamilton_liquid_classes, HamiltonLiquidClass):
      given = None if hamilton_liquid_classes is None else [hamilton_liquid_classes] * NUM_PROBES
    else:
      given = list(hamilton_liquid_classes)
      if len(given) != NUM_PROBES:
        raise ValueError("hamilton_liquid_classes must be a single HLC or length-8 list")
      if any(hlc is None for hlc in given):
        raise ValueError("hamilton_liquid_classes must name a class for every probe")
    if disable_volume_correction and given is not None:
      raise ValueError(
        "disable_volume_correction sends the volume as given; a class would correct it"
      )
    liquid, piston, classes = get_volumes_and_classes(
      containers,
      list(range(NUM_PROBES)),
      self._require_mounted_tips(),
      None if disable_volume_correction else [volume] * NUM_PROBES,
      [volume] * NUM_PROBES if disable_volume_correction else None,
      cast(Optional[List[HamiltonLiquidClass]], given),
      [jet] * NUM_PROBES,
      [blow_out] * NUM_PROBES,
      lookup=self._driver.liquid_class_lookup,
    )
    if classes is not None and any(hlc is not classes[0] for hlc in classes):
      raise ValueError("the 8 probes move on one piston: give one liquid class for all of them")
    return liquid[0], piston[0], None if classes is None else classes[0]

  async def aspirate(
    self,
    containers: Sequence[Container],
    *,
    volume: float,
    pre_mix: Optional[Mix] = None,
    mix_position_from_liquid_surface: float = 0.0,
    use_channels: Optional[Sequence[int]] = None,
    offset: Coordinate = Coordinate.zero(),
    liquid_height: Optional[float] = None,
    flow_rate: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    z_final: Optional[float] = None,
    z_fluid: Optional[float] = None,
    z_air: Optional[float] = None,
    z_minimum: Optional[float] = None,
    settling_time: Optional[float] = None,
    transport_air_volume: Optional[float] = None,
    z_liquid_exit_speed: Optional[float] = None,
    prewet_volume: Optional[float] = None,
    z_bottom_search_offset: Optional[float] = None,
    lld_mode: Optional[Pipettes.LLDMode] = None,
    lld: Optional[PrepCmd.LldParameters] = None,
    p_lld: Optional[PrepCmd.PLldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    tadm: Optional[PrepCmd.TadmParameters] = None,
    container_segments: Optional[List[PrepCmd.SegmentDescriptor]] = None,
    surface_following_distance: float = 0.0,
    auto_surface_following: bool = False,
    hamilton_liquid_classes: Optional[
      Union[HamiltonLiquidClass, List[Optional[HamiltonLiquidClass]]]
    ] = None,
    jet: bool = False,
    blow_out: bool = False,
    disable_volume_correction: bool = False,
    read_timeout: Optional[float] = None,
    command_version: Optional[Literal["v1", "v2"]] = None,
  ) -> None:
    """Aspirate the same volume on every channel from one trough or eight wells.

    Args:
      containers: one container wide enough for all eight channels, or eight wells at channel
        pitch in row-A-first order — same resource vocabulary as `Pipettes.aspirate`.
      volume: how much each tip draws, in uL (one piston for the whole head).
      pre_mix: mix cycles run before the draw, per probe, sent uncorrected. Surface following
        is refused. Only the draw changes tracked volumes.
      mix_position_from_liquid_surface: mixing depth under the aspirate height, in mm.
      surface_following_distance: how far the tips follow the sinking surface, in mm; 0 does not
        follow.
      auto_surface_following: follow each container's profile as it is, from the surface the
        search finds or `liquid_height` under OFF. Refused beside a distance.
      lld_mode: CAPACITIVE under `auto_surface_following` when None.
      container_segments: cross-sections sent as given. None builds them from the container
        profile and `surface_following_distance`, or as it is under `auto_surface_following`.
    """
    del offset  # geometry uses container absolute locations
    use_channels = list(use_channels) if use_channels is not None else list(range(NUM_PROBES))
    self._require_all_channels(use_channels, "aspirate")
    targets = self._resolve_liquid_targets(containers, "aspirate")
    if pre_mix is not None:
      self._validate_mix(pre_mix, targets.volume_containers)
    if not math.isfinite(mix_position_from_liquid_surface) or mix_position_from_liquid_surface < 0:
      raise ValueError("Mix depth must be finite and non-negative")
    mix_block = get_mix_parameters([pre_mix], 1, [mix_position_from_liquid_surface])[0]
    tip = self._require_mounted_tip()

    lld_mode = self._get_lld_mode(
      lld_mode, auto_surface_following, surface_following_distance, liquid_height, z_fluid
    )
    # None follows the profile as it is, which is what the firmware's own following does
    following = None if auto_surface_following else surface_following_distance
    traverse_z = self._resolve_traverse_height()
    end_resolved = (
      z_final if z_final is not None else traverse_z - (tip.get_size_z() - tip.fitting_depth)
    )

    wg = _absolute_z_from_well(targets.ref_resource, self._require_deck(), liquid_height)
    liquid, corrected, hlc = self._get_volume_and_class(
      targets.volume_containers,
      float(volume),
      hamilton_liquid_classes,
      jet,
      blow_out,
      disable_volume_correction,
    )
    resolved_z_fluid = z_fluid if z_fluid is not None else wg.liquid_surface
    resolved_z_air = z_air if z_air is not None else wg.z_air
    resolved_z_minimum = z_minimum if z_minimum is not None else wg.well_bottom
    # the firmware counts segment 0 from z_minimum
    cavity_bottom_z = targets.ref_resource.get_location_wrt(
      self._require_deck(), "c", "c", "cavity_bottom"
    ).z
    if container_segments is not None:
      ref_segments = container_segments
    else:
      ref_segments = _get_container_segments(
        targets.ref_resource,
        liquid_height=resolved_z_fluid - cavity_bottom_z,
        piston_volume=corrected,
        surface_following_distance=following,
        profile_start=resolved_z_minimum - cavity_bottom_z,
      )
    resolved_z_bottom_search_offset = (
      z_bottom_search_offset if z_bottom_search_offset is not None else 2.0
    )
    resolved_settling_time = (
      settling_time
      if settling_time is not None
      else (hlc.aspiration_settling_time if hlc is not None else 1.0)
    )
    resolved_transport_air_volume = (
      transport_air_volume
      if transport_air_volume is not None
      else (hlc.aspiration_air_transport_volume if hlc is not None else 0.0)
    )
    resolved_z_liquid_exit_speed = (
      z_liquid_exit_speed
      if z_liquid_exit_speed is not None
      else (hlc.aspiration_swap_speed if hlc is not None else 10.0)
    )
    resolved_prewet_volume = (
      prewet_volume
      if prewet_volume is not None
      else (hlc.aspiration_over_aspirate_volume if hlc is not None else 0.0)
    )
    resolved_flow = (
      flow_rate
      if flow_rate is not None
      else (hlc.aspiration_flow_rate if hlc is not None else 100.0)
    )
    blowout_volume = (
      blow_out_air_volume
      if blow_out_air_volume is not None
      else (hlc.aspiration_blow_out_volume if hlc is not None else 0.0)
    )

    logger.info(
      "[Prep MPH] aspirate: resource=%s, containers=%s, volume=%.3f, flow_rate=%s",
      targets.resource_name,
      targets.op_targets,
      corrected,
      round(resolved_flow, 3),
    )

    # Without segments the firmware follows tube_radius, and 0 does not follow
    tube_radius = 0.0 if following == 0 else _effective_radius(targets.ref_resource)
    effective_lld = self._resolve_effective_lld(lld_mode, lld)
    is_tadm = tadm is not None
    use_v2 = self._resolve_command_version(command_version)

    lld_defaults = _default_lld_params_fn(effective_lld, p_lld, c_lld, lld_mode=lld_mode)
    lld_params = _lld_for_well_fn(effective_lld, lld, wg.top_of_well)
    resolved_tadm = tadm or PrepCmd.TadmParameters.default()

    assemble = self._assemble_aspirate_v2 if use_v2 else self._assemble_aspirate_v1
    param_struct = assemble(
      ref_x=targets.ref_x,
      ref_y=targets.ref_y,
      volume=corrected,
      tube_radius=tube_radius,
      minimum_traverse_height_end=end_resolved,
      z_minimum=resolved_z_minimum,
      z_fluid=resolved_z_fluid,
      z_air=resolved_z_air,
      z_bottom_search_offset=resolved_z_bottom_search_offset,
      settling_time=resolved_settling_time,
      transport_air_volume=resolved_transport_air_volume,
      z_liquid_exit_speed=resolved_z_liquid_exit_speed,
      prewet_volume=resolved_prewet_volume,
      blowout_volume=blowout_volume,
      flow_rate=resolved_flow,
      segments=ref_segments,
      effective_lld=effective_lld,
      is_tadm=is_tadm,
      lld_params=lld_params,
      lld_defaults=lld_defaults,
      tadm=resolved_tadm,
      mix=mix_block,
    )

    cmd_cls = self._ASPIRATE_CMD[(effective_lld, is_tadm, use_v2)]

    resolved_read_timeout = read_timeout
    if resolved_read_timeout is None and effective_lld:
      resolved_read_timeout = _lld_seek_timeout(
        lld_params, resolved_z_minimum, approach_from_z=end_resolved
      )
    if pre_mix is not None and read_timeout is None:
      resolved_read_timeout = max(
        resolved_read_timeout or 0, self._driver.default_read_timeout
      ) + pre_mix.repetitions * (2 * pre_mix.volume / pre_mix.flow_rate + 2)

    mounted = self._require_mounted_tips()
    volume_intents = [
      VolumeTransferIntent(
        channel=ch,
        container=targets.volume_containers[ch],
        tip=mounted[ch],
        volume_ul=liquid,
        direction="aspirate",
      )
      for ch in use_channels
    ]
    queue_volume_transfers(volume_intents)

    aspirated = {ch: False for ch in use_channels}
    self._record_target(targets.ref_x, targets.ref_y, end_resolved)
    try:
      await self._driver.send_command(
        cmd_cls(aspirate_parameters=[param_struct]),  # type: ignore[arg-type]
        read_timeout=resolved_read_timeout,
      )
      aspirated = all_channels_succeeded(use_channels)
    except ChannelizedError as e:
      # It can fail on some shafts and not others; the device says which
      aspirated = successes_from_failed_channels(use_channels, e.errors)
      raise
    finally:
      # What each shaft moved is what its tip now holds, and the well no longer does.
      finalize_volume_ops(volume_intents, aspirated)
      await self._record_where_it_stopped()

  async def dispense(
    self,
    containers: Sequence[Container],
    *,
    volume: float,
    use_channels: Optional[Sequence[int]] = None,
    offset: Coordinate = Coordinate.zero(),
    liquid_height: Optional[float] = None,
    flow_rate: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    z_final: Optional[float] = None,
    z_fluid: Optional[float] = None,
    z_air: Optional[float] = None,
    z_minimum: Optional[float] = None,
    settling_time: Optional[float] = None,
    transport_air_volume: Optional[float] = None,
    z_liquid_exit_speed: Optional[float] = None,
    stop_back_volume: Optional[float] = None,
    cutoff_speed: Optional[float] = None,
    z_bottom_search_offset: Optional[float] = None,
    lld_mode: Optional[Pipettes.LLDMode] = None,
    lld: Optional[PrepCmd.LldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    container_segments: Optional[List[PrepCmd.SegmentDescriptor]] = None,
    surface_following_distance: float = 0.0,
    auto_surface_following: bool = False,
    hamilton_liquid_classes: Optional[
      Union[HamiltonLiquidClass, List[Optional[HamiltonLiquidClass]]]
    ] = None,
    jet: bool = False,
    blow_out: bool = False,
    disable_volume_correction: bool = False,
    read_timeout: Optional[float] = None,
    command_version: Optional[Literal["v1", "v2"]] = None,
  ) -> None:
    """Dispense the same volume on every channel into one trough or eight wells.

    Args:
      containers: one container wide enough for all eight channels, or eight wells at channel
        pitch in row-A-first order — same resource vocabulary as `Pipettes.dispense`.
      volume: how much each tip pushes, in uL (one piston for the whole head).
      surface_following_distance: how far the tips follow the rising surface, in mm; 0 does not
        follow.
      auto_surface_following: follow each container's profile as it is, from the surface the
        search finds or `liquid_height` under OFF. Refused beside a distance.
      lld_mode: CAPACITIVE under `auto_surface_following` when None.
      container_segments: cross-sections sent as given. None builds them from the container
        profile and `surface_following_distance`, or as it is under `auto_surface_following`.
    """
    del offset
    del blow_out_air_volume  # dispense blowout not on Prep dispense wire path today
    use_channels = list(use_channels) if use_channels is not None else list(range(NUM_PROBES))
    self._require_all_channels(use_channels, "dispense")
    targets = self._resolve_liquid_targets(containers, "dispense")
    tip = self._require_mounted_tip()

    lld_mode = self._get_lld_mode(
      lld_mode, auto_surface_following, surface_following_distance, liquid_height, z_fluid
    )
    # None follows the profile as it is, which is what the firmware's own following does
    following = None if auto_surface_following else surface_following_distance
    traverse_z = self._resolve_traverse_height()
    end_resolved = (
      z_final if z_final is not None else traverse_z - (tip.get_size_z() - tip.fitting_depth)
    )

    wg = _absolute_z_from_well(targets.ref_resource, self._require_deck(), liquid_height)
    liquid, corrected, hlc = self._get_volume_and_class(
      targets.volume_containers,
      float(volume),
      hamilton_liquid_classes,
      jet,
      blow_out,
      disable_volume_correction,
    )
    resolved_z_fluid = z_fluid if z_fluid is not None else wg.liquid_surface
    resolved_z_air = z_air if z_air is not None else wg.z_air
    resolved_z_minimum = z_minimum if z_minimum is not None else wg.well_bottom
    # the firmware counts segment 0 from z_minimum
    cavity_bottom_z = targets.ref_resource.get_location_wrt(
      self._require_deck(), "c", "c", "cavity_bottom"
    ).z
    if container_segments is not None:
      ref_segments = container_segments
    else:
      ref_segments = _get_container_segments(
        targets.ref_resource,
        # A surface raised by d from h is the one a draw of the same volume lowers from h + d.
        liquid_height=resolved_z_fluid - cavity_bottom_z + (following or 0.0),
        piston_volume=corrected,
        surface_following_distance=following,
        profile_start=resolved_z_minimum - cavity_bottom_z,
      )
    resolved_z_bottom_search_offset = (
      z_bottom_search_offset if z_bottom_search_offset is not None else 2.0
    )
    resolved_settling_time = (
      settling_time
      if settling_time is not None
      else (hlc.dispense_settling_time if hlc is not None else 0.0)
    )
    resolved_transport_air_volume = (
      transport_air_volume
      if transport_air_volume is not None
      else (hlc.dispense_air_transport_volume if hlc is not None else 0.0)
    )
    resolved_z_liquid_exit_speed = (
      z_liquid_exit_speed
      if z_liquid_exit_speed is not None
      else (hlc.dispense_swap_speed if hlc is not None else 10.0)
    )
    resolved_stop_back_volume = (
      stop_back_volume
      if stop_back_volume is not None
      else (hlc.dispense_stop_back_volume if hlc is not None else 0.0)
    )
    resolved_cutoff_speed = (
      cutoff_speed
      if cutoff_speed is not None
      else (hlc.dispense_stop_flow_rate if hlc is not None else 100.0)
    )
    resolved_flow = (
      flow_rate if flow_rate is not None else (hlc.dispense_flow_rate if hlc is not None else 100.0)
    )

    logger.info(
      "[Prep MPH] dispense: resource=%s, containers=%s, volume=%.3f, flow_rate=%s",
      targets.resource_name,
      targets.op_targets,
      corrected,
      round(resolved_flow, 3),
    )

    tube_radius = 0.0 if following == 0 else _effective_radius(targets.ref_resource)
    _DISPENSE_ALLOWED_LLD = frozenset({Pipettes.LLDMode.CAPACITIVE})
    effective_lld = self._resolve_effective_lld(lld_mode, lld, allowed_modes=_DISPENSE_ALLOWED_LLD)
    use_v2 = self._resolve_command_version(command_version)

    lld_defaults = _default_lld_params_fn(effective_lld, c_lld=c_lld, lld_mode=lld_mode)
    lld_params = _lld_for_well_fn(effective_lld, lld, wg.top_of_well)

    assemble = self._assemble_dispense_v2 if use_v2 else self._assemble_dispense_v1
    param_struct = assemble(
      ref_x=targets.ref_x,
      ref_y=targets.ref_y,
      volume=corrected,
      tube_radius=tube_radius,
      minimum_traverse_height_end=end_resolved,
      z_minimum=resolved_z_minimum,
      z_fluid=resolved_z_fluid,
      z_air=resolved_z_air,
      z_bottom_search_offset=resolved_z_bottom_search_offset,
      settling_time=resolved_settling_time,
      transport_air_volume=resolved_transport_air_volume,
      z_liquid_exit_speed=resolved_z_liquid_exit_speed,
      stop_back_volume=resolved_stop_back_volume,
      cutoff_speed=resolved_cutoff_speed,
      flow_rate=resolved_flow,
      segments=ref_segments,
      effective_lld=effective_lld,
      lld_params=lld_params,
      lld_defaults=lld_defaults,
    )

    cmd_cls = self._DISPENSE_CMD[(effective_lld, use_v2)]

    resolved_read_timeout = read_timeout
    if resolved_read_timeout is None and effective_lld:
      resolved_read_timeout = _lld_seek_timeout(
        lld_params, resolved_z_minimum, approach_from_z=end_resolved
      )

    mounted = self._require_mounted_tips()
    volume_intents = [
      VolumeTransferIntent(
        channel=ch,
        container=targets.volume_containers[ch],
        tip=mounted[ch],
        volume_ul=liquid,
        direction="dispense",
      )
      for ch in use_channels
    ]
    queue_volume_transfers(volume_intents)

    dispensed = {ch: False for ch in use_channels}
    self._record_target(targets.ref_x, targets.ref_y, end_resolved)
    try:
      await self._driver.send_command(
        cmd_cls(dispense_parameters=[param_struct]),  # type: ignore[arg-type]
        read_timeout=resolved_read_timeout if effective_lld else None,
      )
      dispensed = all_channels_succeeded(use_channels)
    except ChannelizedError as e:
      # It can fail on some shafts and not others; the device says which
      dispensed = successes_from_failed_channels(use_channels, e.errors)
      raise
    finally:
      # What each shaft moved is what the well now holds, and its tip no longer does.
      finalize_volume_ops(volume_intents, dispensed)
      await self._record_where_it_stopped()

  async def mix(
    self,
    containers: Sequence[Container],
    mix: Mix,
    *,
    liquid_height: float = 1.0,
    command_version: Optional[Literal["v1", "v2"]] = None,
  ) -> None:
    """Mix in place on all eight probes at a fixed height, with empty tips.

    One zero-volume aspiration carries every cycle; no dispense command follows. No volume
    correction, prewet or transport air; no LLD or surface following. Tracked volumes stay
    unchanged; a failure mid-mix leaves the physical volumes uncertain.

    Args:
      containers: one container wide enough for all eight channels, or eight wells at channel
        pitch in row-A-first order.
      mix: the volume per probe, cycles and flow rate.
      liquid_height: mixing height above the cavity bottom, in mm.
      command_version: "v1" or "v2"; None picks what the head supports.
    """
    targets = self._resolve_liquid_targets(containers, "mix")
    self._validate_mix(mix, targets.volume_containers)
    if not math.isfinite(liquid_height) or liquid_height < 0:
      raise ValueError("liquid_height must be finite and non-negative")
    if any(tip.tracker.get_used_volume() > 0 for tip in self._require_mounted_tips()):
      raise ValueError("Mixing requires empty tips")
    await self.aspirate(
      containers,
      volume=0,
      pre_mix=mix,
      liquid_height=liquid_height,
      flow_rate=mix.flow_rate,
      lld_mode=Pipettes.LLDMode.OFF,
      surface_following_distance=0,
      disable_volume_correction=True,
      prewet_volume=0,
      blow_out_air_volume=0,
      transport_air_volume=0,
      settling_time=0,
      command_version=command_version,
    )
