"""Pipettes: dual-channel pipettor ops plus per-channel discovery.

Channel-scoped topology discovery, bounds parsing, and per-channel firmware
queries live alongside tip pickup/drop and aspirate/dispense orchestration.

The firmware object tree exposes channel internals as a single template under
``MLPrepRoot.Channel Root.Channel`` (and an analogous ``MLPrepRoot.MPH Channel
Root.Channel`` for MPH). Individual physical channels share that template —
per-channel identity lives in the node-ID component of the Address. We probe
the full object tree and match children by **path prefix**
(``"<root>.Channel Root.Channel.Squeeze.SDrive"``) rather than computing node
IDs directly.
"""

from __future__ import annotations

import enum
import logging
import math
import struct as _struct
from dataclasses import dataclass, field
from typing import (
  TYPE_CHECKING,
  Any,
  Awaitable,
  Callable,
  Dict,
  Generic,
  List,
  Literal,
  NamedTuple,
  Optional,
  Sequence,
  Tuple,
  TypedDict,
  TypeVar,
  Union,
)

from pylabrobot.hamilton.liquid_class_resolver import (
  corrected_volumes_for_ops,
  resolve_hamilton_liquid_classes,
)
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError
from pylabrobot.legacy.liquid_handling.liquid_classes.hamilton.base import HamiltonLiquidClass
from pylabrobot.resources import Container, Coordinate, Tip
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.hamilton.core_grippers import HamiltonCoreGrippers
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_state import (
  TipDropIntent,
  TipPickupIntent,
  VolumeTransferIntent,
  all_channels_succeeded,
  finalize_tip_ops,
  finalize_volume_ops,
  queue_tip_drops,
  queue_tip_pickups,
  queue_volume_transfers,
  successes_from_failed_channels,
)
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.tip_tracker import TipTracker
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.well import CrossSectionType, Well

from .. import prep_commands as PrepCmd
from ..client import PIPETTOR_OBJECT_PATH
from .x_arm import XArmConfiguration

if TYPE_CHECKING:
  from pylabrobot.resources.deck import Deck

  from ..client import PrepClient
  from ..configuration import DeviceConfiguration
  from ..master import PrepDriver

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


@dataclass
class _PipetteTransfer:
  """Private snapshot for aspirate/dispense resolution (not a public standard op)."""

  resource: Container
  tip: Tip
  volume: float
  offset: Coordinate
  liquid_height: Optional[float] = None
  flow_rate: Optional[float] = None
  blow_out_air_volume: Optional[float] = None


_OpT = TypeVar("_OpT", bound=_PipetteTransfer)


# =============================================================================
# Shared pure helpers (also imported by Head8)
# =============================================================================


def fill_in_defaults(val: Optional[List[_T]], default: List[_T]) -> List[_T]:
  """Convert optional per-channel overrides into a full list matching ``default`` length."""
  if val is None:
    return default
  if len(val) != len(default):
    raise ValueError(f"Value length must equal num operations ({len(default)}), but is {len(val)}")
  return [v if v is not None else d for v, d in zip(val, default)]


def default_lld_params(
  effective_lld: bool,
  p_lld: Optional[PrepCmd.PLldParameters] = None,
  c_lld: Optional[PrepCmd.CLldParameters] = None,
) -> Pipettes._LldDefaults:
  """Build resolved pLLD / cLLD defaults.

  When LLD is active and no caller override is given, returns non-default
  parameters (``default_values=False``) so the firmware actually triggers
  detection.  Otherwise returns firmware defaults.
  """
  if effective_lld:
    resolved_p = p_lld or PrepCmd.PLldParameters(
      default_values=False,
      sensitivity=1,
      dispenser_seek_speed=0.0,
      lld_height_difference=0.0,
      detect_mode=0,
    )
    resolved_c = c_lld or PrepCmd.CLldParameters(
      default_values=False,
      sensitivity=4,
      clot_check_enable=False,
      z_clot_check=0.0,
      detect_mode=0,
    )
  else:
    resolved_p = p_lld or PrepCmd.PLldParameters.default()
    resolved_c = c_lld or PrepCmd.CLldParameters.default()
  return Pipettes._LldDefaults(p_lld=resolved_p, c_lld=resolved_c)


def lld_for_well(
  effective_lld: bool, lld: Optional[PrepCmd.LldParameters], top_of_well_z: float
) -> PrepCmd.LldParameters:
  """Per-channel LLD seek parameters from caller override or well geometry."""
  if effective_lld and lld is None:
    return PrepCmd.LldParameters(
      default_values=False,
      search_start_position=top_of_well_z,
      channel_speed=5.0,
      z_submerge=2.0,
      z_out_of_liquid=0.0,
    )
  return lld or PrepCmd.LldParameters.default()


def segments_to_cone_geometry(
  segments: list[PrepCmd.SegmentDescriptor], fallback_radius: float
) -> Tuple[float, float, float]:
  """Convert v2 frustum segments to v1 cone model (tube_radius, cone_height, cone_bottom_radius)."""
  if not segments:
    return (fallback_radius, 0.0, 0.0)
  total_height = sum(s.height for s in segments)
  if total_height <= 0:
    return (fallback_radius, 0.0, 0.0)
  weighted_area = sum(s.height * (s.area_top + s.area_bottom) / 2.0 for s in segments)
  avg_area = weighted_area / total_height
  tube_radius = math.sqrt(avg_area / math.pi)
  bot = segments[0]
  if abs(bot.area_bottom - bot.area_top) > 1e-6:
    cone_height = bot.height
    cone_bottom_radius = math.sqrt(bot.area_bottom / math.pi)
  else:
    cone_height = 0.0
    cone_bottom_radius = 0.0
  return (tube_radius, cone_height, cone_bottom_radius)


def patch_common_with_cone(
  common: PrepCmd.CommonParameters, segments: list[PrepCmd.SegmentDescriptor]
) -> PrepCmd.CommonParameters:
  """Return CommonParameters with cone geometry derived from segments (v2→v1 downgrade)."""
  if len(segments) > 1:
    logger.warning(
      "v1 command selected: collapsing %d container segments into single cone approximation. "
      "Liquid following accuracy may be reduced for complex container geometries.",
      len(segments),
    )
  tube_r, cone_h, cone_br = segments_to_cone_geometry(segments, common.tube_radius)
  return PrepCmd.CommonParameters(
    default_values=common.default_values,
    empty=common.empty,
    z_minimum=common.z_minimum,
    z_final=common.z_final,
    z_liquid_exit_speed=common.z_liquid_exit_speed,
    liquid_volume=common.liquid_volume,
    liquid_speed=common.liquid_speed,
    transport_air_volume=common.transport_air_volume,
    tube_radius=tube_r,
    cone_height=cone_h,
    cone_bottom_radius=cone_br,
    settling_time=common.settling_time,
    additional_probes=common.additional_probes,
  )


def resolve_command_version(
  supports_v2: Optional[bool],
  use_v1_flag: bool,
  override: Optional[Literal["v1", "v2"]],
  *,
  v2_error_hint: str = "v2 commands are not supported by this firmware.",
) -> bool:
  """Resolve whether to use v2 commands for a pipetting call. Returns True for v2.

  Resolution order:
  1. Per-call ``override`` ("v1" / "v2") — takes precedence.
  2. Backend-level ``use_v1_flag`` / ``supports_v2`` probe result from setup.
  """
  if override == "v1":
    return False
  if override == "v2":
    if supports_v2 is False:
      raise ValueError(v2_error_hint)
    return True
  return supports_v2 is True


def lld_seek_timeout(
  lld_params: PrepCmd.LldParameters,
  z_minimum: float,
) -> Optional[float]:
  """Compute a read timeout (s) for an LLD seek move, or None if not applicable."""
  if lld_params.channel_speed > 0:
    speed: float = float(lld_params.channel_speed)
    seek_distance: float = float(lld_params.search_start_position) - z_minimum
    if seek_distance > 0:
      return seek_distance / speed + 5.0
  return None


def _effective_radius(resource) -> float:
  """Effective radius for PrepCmd.CommonParameters.tube_radius.

  For circular wells uses the actual radius; for rectangular wells computes the
  radius of a circle with equivalent area so tube_radius is meaningful to the
  firmware's conical liquid-following model.
  """
  if isinstance(resource, Well) and resource.cross_section_type == CrossSectionType.RECTANGLE:
    return float(math.sqrt(resource.get_size_x() * resource.get_size_y() / math.pi))
  return float(resource.get_size_x() / 2)


def _build_container_segments(resource: object) -> list[PrepCmd.SegmentDescriptor]:
  """Derive PrepCmd.SegmentDescriptor list from a Well's geometry for liquid-following.

  Each segment is a frustum.  The firmware uses area_bottom/area_top to
  interpolate cross-sectional area A(z) within the segment and computes the
  Z-axis following speed as dz/dt = Q / A(z), where Q is volumetric flow rate.

  Returns [] when geometry cannot be determined; the firmware then falls back to
  the tube_radius / cone model in PrepCmd.CommonParameters.
  """
  if not isinstance(resource, Well):
    return []
  well: Well = resource

  size_z = well.get_size_z()

  if well.cross_section_type == CrossSectionType.CIRCLE:
    area = math.pi * (well.get_size_x() / 2) ** 2
  elif well.cross_section_type == CrossSectionType.RECTANGLE:
    area = well.get_size_x() * well.get_size_y()
  else:
    return []

  if well.supports_compute_height_volume_functions():
    # Non-linear geometry: approximate with N frustum segments by sampling dV/dh.
    n_boundaries = 11  # 10 segments
    heights = [size_z * i / (n_boundaries - 1) for i in range(n_boundaries)]
    eps = size_z / (n_boundaries - 1) * 0.1

    def area_at(h: float) -> float:
      h_lo = max(0.0, h - eps)
      h_hi = min(size_z, h + eps)
      dv = well.compute_volume_from_height(h_hi) - well.compute_volume_from_height(h_lo)
      return float(dv / (h_hi - h_lo))

    return [
      PrepCmd.SegmentDescriptor(
        area_top=float(area_at(heights[i + 1])),
        area_bottom=float(area_at(heights[i])),
        height=float(heights[i + 1] - heights[i]),
      )
      for i in range(n_boundaries - 1)
    ]

  # Simple geometry: single segment with constant cross-section.
  return [
    PrepCmd.SegmentDescriptor(area_top=float(area), area_bottom=float(area), height=float(size_z))
  ]


class _WellGeometry(NamedTuple):
  """Absolute Z positions derived from well geometry."""

  well_bottom: float
  liquid_surface: float
  top_of_well: float
  z_air: float


def _absolute_z_from_well(
  resource,
  deck: "Deck",
  liquid_height: Optional[float] = None,
  offset_z: float = 0.0,
  z_air_margin_mm: float = 2.0,
) -> _WellGeometry:
  """Compute absolute Z values from well/container geometry for aspirate/dispense.

  Args:
    resource: Well or Container with get_size_z().
    deck: the deck the Z values are measured from, which is what the firmware counts from.
    liquid_height: Distance from well bottom to liquid surface (mm). None = 0.
    offset_z: Additional Z applied to the bottom position (e.g. from op.offset.z).
    z_air_margin_mm: Clearance above well opening for z_air (approach/exit height).

  Returns:
    _WellGeometry with well_bottom, liquid_surface, top_of_well, z_air.
  """
  if not isinstance(resource, Container):
    raise ValueError(
      "Resource must have get_size_z() to derive absolute Z (e.g. a Well or Container). "
      "Pass z_minimum, z_fluid, z_air explicitly for this operation."
    )
  loc = resource.get_location_wrt(deck, "c", "c", "cavity_bottom")
  well_bottom_z = loc.z + offset_z
  liquid_surface_z = well_bottom_z + (liquid_height or 0.0)
  top_of_well_z = loc.z + resource.get_size_z()
  z_air_z = top_of_well_z + z_air_margin_mm
  return _WellGeometry(well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z)


_CHANNEL_INDEX = {
  0: PrepCmd.ChannelIndex.RearChannel,
  1: PrepCmd.ChannelIndex.FrontChannel,
}


@dataclass
class PipetteConfiguration:
  """What a single pipetting channel reports about itself.

  Read off the channel at setup. Every field is None until it has been read.
  """

  firmware_version: Optional[str] = None
  """What the channel's node runs, from its NodeInformation."""
  x_range: Optional[Tuple[float, float]] = None
  """The X window the channel reaches, in mm, lowest first. From GetChannelBounds."""
  y_range: Optional[Tuple[float, float]] = None
  """The Y window the channel reaches, in mm, lowest first. From GetChannelBounds."""
  z_range: Optional[Tuple[float, float]] = None
  """The Z window the channel reaches, in mm, lowest first. From GetChannelBounds."""


@dataclass
class PipettesConfiguration:
  """Configuration for the pipetting channels, and for each channel in turn.

  `channels` holds what each individual channel reports. It is empty until setup has counted the
  channels; only the device reports how many there are.
  """

  # -- how the caller wants them driven --
  use_v1_aspirate_dispense: bool = False
  """Whether to aspirate and dispense with the v1 commands (cmd 1-6) rather than the v2 ones."""

  # -- what the pipettor answered --
  supports_v2_pipetting: Optional[bool] = None
  """Whether the pipettor carries the v2 aspirate/dispense commands. None until probed; False when
  the probe was skipped for v1."""
  v2_pipetting_command_ids: Tuple[int, ...] = (38, 39, 40, 41, 42, 43)
  """The v2 aspirate/dispense command ids, on the pipettor's interface 1."""

  # -- device facts --
  x_reference_anchor: str = "c"
  """Along X the channels sit at the gantry's reported position: centred across the channel."""
  y_reference_anchor: str = "c"
  z_reference_anchor: str = "b"
  """Along Z the positions refer to the end of the tip mounting shaft. A channel carrying a shaft is
  anchored on the shaft's end rather than on this, which then applies only to one that carries none."""
  channel_width: float = 8.9826
  """How wide to model a channel, in mm. Not reported by the Prep: the width the STAR's channels
  report."""
  channel_size_z: float = 140.0
  """How tall to model a channel, in mm. Not reported by the Prep: the height the STAR models its
  channels at."""
  channel_model: str = "hamilton_star_pipette_channel"
  """Which 3D model draws a channel."""
  channels: List[PipetteConfiguration] = field(default_factory=list)
  """One entry per channel, in channel order."""

  def check_channels_agree(self) -> None:
    """Warn if the channels are not all running the same firmware.

    Channels are replaced individually. A device repaired piecemeal is the case this catches.
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
    logger.warning("the pipetting channels are not all on the same firmware (%s)", reported)

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


class ChannelBounds(TypedDict):
  """Firmware-reported movement limits for one pipettor channel (mm)."""

  x_min: float
  x_max: float
  y_min: float
  y_max: float
  z_min: float
  z_max: float


# ---------------------------------------------------------------------------
# PipetteChannel — thin per-channel facade owned by Pipettes.channels
# ---------------------------------------------------------------------------


class PipetteChannel:
  """Per-channel facade: drive addresses, movement bounds, firmware-version queries.

  Instances are constructed by :meth:`Pipettes.discover` during :meth:`PrepDriver.setup`
  and exposed as ``prep.pipettes.channels[i]`` (or the dual-channel peer).
  """

  def __init__(
    self,
    *,
    index: int,
    client: "PrepClient",
    sleeve_sensor: Optional[Address] = None,
    zdrive: Optional[Address] = None,
    node_info: Optional[Address] = None,
    bounds: Optional[ChannelBounds] = None,
  ) -> None:
    self.index = index
    self._client = client
    self.sleeve_sensor = sleeve_sensor
    self.zdrive = zdrive
    self.node_info = node_info
    self.bounds = bounds  # x_min..z_max from firmware, or None if unavailable

  def __repr__(self) -> str:
    return (
      f"PipetteChannel(index={self.index}, node_info={self.node_info!r}, "
      f"bounds={'set' if self.bounds else 'unset'})"
    )

  async def request_firmware_version(self) -> Optional[str]:
    """Per-channel firmware version string (NodeInformation cmd=8).

    Serial number is intentionally not exposed here — NodeInformation's
    GetSerialNumber endpoint is unpopulated on shipped instruments, and the
    canonical instrument serial (pipettor module) is already surfaced via
    :meth:`PrepDriver.request_device_serial_number`.
    """
    if self.node_info is None:
      return None
    return await self._client._query_firmware_string(self.node_info, cmd_id=8, iface_id=1)


# =============================================================================
# Pipettes — channel indices and deck routing
# =============================================================================


def _build_pipettor_gantry_move_parameters(
  x: float,
  channels: List[int],
  y: Union[float, List[float]],
  z: Union[float, List[float]],
) -> PrepCmd.GantryMoveXYZParameters:
  """Build :class:`~prep_commands.GantryMoveXYZParameters` for PipettorRoot move commands.

  Only ``FrontChannel`` and ``RearChannel`` may appear in ``axis_parameters``. MPH
  gantry moves must use :class:`~prep_commands.MphMoveToPosition` instead.
  """
  axis_parameters: List[PrepCmd.ChannelYZMoveParameters] = []
  for i, ch in enumerate(channels):
    y_i = y[i] if isinstance(y, list) else y
    z_i = z[i] if isinstance(z, list) else z
    enum_ch = _CHANNEL_INDEX[ch]
    if enum_ch not in (
      PrepCmd.ChannelIndex.FrontChannel,
      PrepCmd.ChannelIndex.RearChannel,
    ):
      raise ValueError(
        f"Pipettor gantry move does not support channel index {ch} (enum {enum_ch!r}). "
        "MPH motion uses Head8 / MphMoveToPosition on MLPrepRoot.MphRoot.MPH."
      )
    axis_parameters.append(
      PrepCmd.ChannelYZMoveParameters(
        default_values=False, channel=enum_ch, y_position=y_i, z_position=z_i
      )
    )
  return PrepCmd.GantryMoveXYZParameters(
    default_values=False, gantry_x_position=x, axis_parameters=axis_parameters
  )


# Channel index -> deck waste resource name (PrepDeck: waste_rear, waste_front, waste_mph)
_CHANNEL_TO_WASTE_NAME = {
  0: "waste_rear",
  1: "waste_front",
  2: "waste_mph",
}

# Expected root name from discovery; validated at setup().
_EXPECTED_ROOT = "MLPrepRoot"


@dataclass(frozen=True)
class _AspirateChannelKit:
  """Pre-resolved per-channel values for one aspirate channel.

  Computed once by ``_resolve_aspirate_channels``; the variant (LLD x monitoring
  x v1/v2) only decides which fields get assembled into which wire dataclass.
  """

  channel: int
  aspirate: PrepCmd.AspirateParameters
  common: PrepCmd.CommonParameters
  segments: list[PrepCmd.SegmentDescriptor]
  no_lld: PrepCmd.NoLldParameters
  lld: PrepCmd.LldParameters
  p_lld: PrepCmd.PLldParameters
  c_lld: PrepCmd.CLldParameters
  monitoring: PrepCmd.AspirateMonitoringParameters
  tadm: PrepCmd.TadmParameters
  mix: PrepCmd.MixParameters
  adc: PrepCmd.AdcParameters


@dataclass(frozen=True)
class _DispenseChannelKit:
  """Pre-resolved per-channel values for one dispense channel."""

  channel: int
  dispense: PrepCmd.DispenseParameters
  common: PrepCmd.CommonParameters
  segments: list[PrepCmd.SegmentDescriptor]
  no_lld: PrepCmd.NoLldParameters
  lld: PrepCmd.LldParameters
  c_lld: PrepCmd.CLldParameters
  tadm: PrepCmd.TadmParameters
  mix: PrepCmd.MixParameters
  adc: PrepCmd.AdcParameters


@dataclass(frozen=True)
class _ChannelContext(Generic[_OpT]):
  """Shared resolved state for aspirate/dispense channel resolution.

  Computed once by ``_resolve_channel_context``; operation-specific resolve
  methods add their own parameters on top.
  """

  n: int
  hlcs: List[Optional[HamiltonLiquidClass]]
  disable_volume_correction: List[bool]
  ch_to_idx: dict[int, int]
  indexed_ops: dict[int, _OpT]
  volumes: List[float]
  well_geometry: List[_WellGeometry]
  z_minimum: List[float]
  z_fluid: List[float]
  z_air: List[float]
  z_final: List[float]
  z_bottom_search_offset: List[float]
  ch_segments: dict[int, list[PrepCmd.SegmentDescriptor]]


class Pipettes:
  """Dual-channel pipettor for Hamilton Prep.

  Narrow constructor: ``client`` (transport + JIT firmware-path resolve) and
  ``driver`` (whose ``configuration`` holds the instrument-wide metadata). ``self.channels`` is built by
  :meth:`discover`.
  """

  class LLDMode(enum.Enum):
    """Liquid level detection mode.

    Same numbering as STARBackend.LLDMode for cross-backend compatibility.
    CAPACITIVE (value=1) is named GAMMA on the STAR — CAPACITIVE is the correct term.
    The Prep firmware uses separate command variants for LLD vs no-LLD, so all
    channels in a single aspirate/dispense call must use the same mode category
    (any LLD mode, or OFF).
    """

    OFF = 0
    CAPACITIVE = 1  # STARBackend.LLDMode.GAMMA — capacitive (cLLD)
    PRESSURE = 2  # pressure-based (pLLD)
    DUAL = 3  # both capacitive and pressure

  @dataclass(frozen=True)
  class _LldDefaults:
    """Resolved pLLD / cLLD parameter pair (shared between aspirate and dispense)."""

    p_lld: PrepCmd.PLldParameters
    c_lld: PrepCmd.CLldParameters

  def __init__(
    self,
    *,
    client: "PrepClient",
    driver: Optional["PrepDriver"] = None,
    deck: Optional["Deck"] = None,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
    configuration: Optional[PipettesConfiguration] = None,
  ) -> None:
    """
    Args:
      client: the client to send commands through.
      driver: the driver whose configuration holds what the device reported.
      deck: the deck positions are measured from.
      default_traverse_height: sets `default_minimum_traverse_height`, when given.
      use_v1_aspirate_dispense: sets `configuration.use_v1_aspirate_dispense`, when set.
      configuration: the channels' configuration. Defaults to `PipettesConfiguration()`.
    """
    self._client = client
    self._driver = driver
    self.deck = deck
    self.configuration = configuration or PipettesConfiguration()
    # The height to travel at when a command names none, in mm. None leaves it to the height the
    # device reports.
    self.default_minimum_traverse_height: Optional[float] = default_traverse_height
    # Default speed and acceleration along each axis, in mm/s and mm/s2: PyLabRobot's defaults are 80
    # percent of what the axis does on PRPAA1087 (V1.2.2).
    # X: 80 % of the X axis profile (`XAxis.GetVelocity` 400 mm/s, `GetAcceleration` 2250 mm/s2) at
    # MLPrep's X speed scale of 100 percent; the scale leaves the acceleration unchanged. Used by
    # `move_to_coordinate` when a move names no X speed.
    self.default_x_speed: float = 320.0
    self.default_x_acceleration: float = 1800.0
    # Y: 80 % of 345 mm/s and 950 mm/s2, fitted from timed `MoveToPosition` moves of 5 to 200 mm
    # (rms 1.7 ms). The move command carries no Y speed.
    self.default_y_speed: float = 276.0
    self.default_y_acceleration: float = 760.0
    # Z: 80 % of 142 mm/s, fitted from timed `MoveToPosition` moves of 2 to 30 mm (rms 4.5 ms), and of
    # 800 mm/s2, read with `ZDrive.GetAcceleration` and matched by that fit. The move command carries
    # no Z speed.
    self.default_z_speed: float = 113.6
    self.default_z_acceleration: float = 640.0
    if use_v1_aspirate_dispense:
      self.configuration.use_v1_aspirate_dispense = True
    self.setup_finished: bool = False
    self.channels: List[PipetteChannel] = []
    self.head: dict[int, TipTracker] = {}
    # One per channel, hung from the X-arm's resource when the driver was given a deck. Setup puts
    # them there; reads and moves keep them in step.
    self.resources: List[Resource] = []

  # -- addressing ----------------------------------------------------------------------------------

  @property
  def num_channels(self) -> int:
    """Number of independent dual-channel pipettor channels (1 or 2). Read from the driver's configuration."""
    n: Optional[int] = self._configuration.num_channels
    if n is None:
      raise RuntimeError("Instrument config has no num_channels (finish PrepDriver.setup first).")
    return n

  @property
  def head8_installed(self) -> bool:
    """True if the 8-channel Multi-Pipetting Head (8MPH) is present. Read from the driver's configuration."""
    try:
      return bool(self._configuration.head8_installed)
    except RuntimeError:
      return False

  @property
  def num_arms(self) -> int:
    """Number of resource-handling arms. 1 when deck has core_grippers and 2 channels, else 0."""
    if self.deck is None:
      return 0
    try:
      cfg = self._configuration
    except RuntimeError:
      return 0
    if cfg.num_channels != 2:
      return 0
    try:
      mount = self.deck.get_resource("core_grippers")
      return 1 if isinstance(mount, HamiltonCoreGrippers) else 0
    except Exception:
      return 0

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

  async def _record_where_they_stopped(self) -> None:
    """Read where the channels came to rest, and record it. For a move's `finally`.

    Only when something models them. Its own failure is logged and swallowed: it must not replace the
    move's exception, which is the one that says what went wrong.
    """
    if not self.resources:
      return
    try:
      await self.request_channel_positions()
    except Exception:
      logger.warning("could not read where the channels stopped; their model is stale")

  async def request_firmware_version(self, channel: int) -> Optional[str]:
    """Firmware version string for pipettor channel (0=rearmost)."""
    if channel >= len(self.channels):
      return None
    return await self.channels[channel].request_firmware_version()

  async def discover(self):
    """Find each channel in the firmware tree, and read what it reports about itself.

    Read-only. Builds `channels` from the drive addresses and movement bounds, then fills in
    `configuration.channels`: each channel's firmware version, and the window it reaches.
    """
    drive_map = await self._client.request_channel_drives(root_name="Channel Root")
    try:
      num_channels = self._configuration.num_channels
    except RuntimeError:
      num_channels = None
    if num_channels is None:
      num_channels = drive_map.num_channels_discovered
    try:
      bounds_list = await self.request_channel_bounds()
    except Exception as e:
      logger.warning("Failed to query channel bounds: %s", e)
      bounds_list = []

    def _drive_addr(seq: List[Address], i: int) -> Optional[Address]:
      return seq[i] if i < len(seq) else None

    self.channels = [
      PipetteChannel(
        index=i,
        client=self._client,
        sleeve_sensor=_drive_addr(drive_map.sleeve_sensor_addrs, i),
        zdrive=_drive_addr(drive_map.zdrive_addrs, i),
        node_info=_drive_addr(drive_map.node_info_addrs, i),
        bounds=bounds_list[i] if i < len(bounds_list) else None,
      )
      for i in range(num_channels)
    ]

    self.configuration.resolve_channels(len(self.channels))
    for index, channel in enumerate(self.channels):
      bounds = channel.bounds
      self.configuration.channels[index] = PipetteConfiguration(
        firmware_version=await channel.request_firmware_version(),
        x_range=None if bounds is None else (bounds["x_min"], bounds["x_max"]),
        y_range=None if bounds is None else (bounds["y_min"], bounds["y_max"]),
        z_range=None if bounds is None else (bounds["z_min"], bounds["z_max"]),
      )
    self.configuration.check_channels_agree()

  async def _on_setup(self):
    """Read config and probe pipettor capabilities.

    Called after ``self.channels`` is populated by :meth:`PrepDriver.setup`. Instrument-
    level initialization (``MLPrep.Initialize``) runs earlier in
    :meth:`PrepDriver.setup` — the pipettor sees an already-initialized instrument.
    """
    cfg = self._configuration
    logger.debug(
      "Hardware config: has_enclosure=%s, safe_speeds=%s, traverse_height=%s, "
      "deck_bounds=%s, deck_sites=%d, waste_sites=%d, num_channels=%s, head8_installed=%s",
      cfg.has_enclosure,
      cfg.safe_speeds_enabled,
      cfg.default_traverse_height,
      cfg.deck_bounds,
      len(cfg.deck_sites),
      len(cfg.waste_sites),
      cfg.num_channels,
      cfg.head8_installed,
    )

    await self.discover()
    if not any(c.x_range is not None for c in self.configuration.channels):
      logger.warning("Channel bounds not available — move_to_coordinate will skip validation")

    # Probe pipettor for v2 aspirate/dispense support (cmd 38-43).
    if self.configuration.use_v1_aspirate_dispense:
      self.configuration.supports_v2_pipetting = False
      logger.debug("V2 aspirate/dispense probe skipped (use_v1_aspirate_dispense=True)")
    else:
      try:
        supported = await self._probe_v2_support()
      except Exception as e:
        logger.warning("PIP V2 support probe failed: %s", e)
        supported = False
      if not supported:
        raise RuntimeError(
          "V2 aspirate/dispense commands (cmd 38-43) are not supported by this firmware. "
          "Pass use_v1_aspirate_dispense=True to Pipettes to use v1 commands (cmd 1-6) instead."
        )
      self.configuration.supports_v2_pipetting = True
      logger.debug("V2 aspirate/dispense support: True")

    self._ensure_head()
    self.setup_finished = True

  async def _on_stop(self):
    for tracker in self.head.values():
      tracker.clear()

  def _ensure_head(self) -> None:
    """Ensure pipette-side TipTrackers exist for each dual-channel index."""
    for i in range(self.num_channels):
      if i not in self.head:
        self.head[i] = TipTracker(thing=f"Channel {i}")

  async def _probe_v2_support(self) -> bool:
    """Probe the pipettor for v2 aspirate/dispense command support.

    Enumerates interface 1 method IDs on the pipettor object and checks whether
    all v2 command IDs (38-43) are present. Returns False when the firmware only
    exposes v1 commands (1-6).
    """
    dest = await self._client.resolve_path(PIPETTOR_OBJECT_PATH)
    methods = await self._client.introspection.methods_for_interface(dest, interface_id=1)
    iface1_ids = {m.method_id for m in methods}
    return set(self.configuration.v2_pipetting_command_ids).issubset(iface1_ids)

  def _resolve_command_version(self, override: Optional[Literal["v1", "v2"]] = None) -> bool:
    return resolve_command_version(
      self.configuration.supports_v2_pipetting,
      self.configuration.use_v1_aspirate_dispense,
      override,
      v2_error_hint=(
        "v2 aspirate/dispense commands (cmd 38-43) are not supported by this firmware. "
        "Use command_version='v1' or pass use_v1_aspirate_dispense=True to Pipettes."
      ),
    )

  # -- where the channels are ----------------------------------------------------------------------

  def _reference_anchor(self, resource: Resource) -> Coordinate:
    """Where on a channel's resource the reported positions refer to, from its left front bottom corner.

    The centre-centre-bottom of the tip mounting shaft, on all three axes: that is the point the Prep
    reports and the one the arm's X marker stands on. A channel carrying a shaft states it as its
    `reference_point`; one that carries none falls back to its own centre-centre-bottom.

    Args:
      resource: the resource modelling the channel.

    Returns:
      The offset from the resource's corner to the point the positions refer to.
    """
    stated = getattr(resource, "reference_point", None)
    if isinstance(stated, Coordinate):
      return stated
    c = self.configuration
    anchor = resource.get_anchor(
      x=c.x_reference_anchor, y=c.y_reference_anchor, z=c.z_reference_anchor
    )
    shaft = next(
      (child for child in resource.children if isinstance(child, TipMountingShaft)), None
    )
    if shaft is None or shaft.location is None:
      return anchor
    return Coordinate(anchor.x, anchor.y, shaft.location.z)

  def get_reference_point_location(self, channel: int) -> Optional[Coordinate]:
    """Where the model has a channel's reference point, in mm on the deck.

    The inverse of `update_location_by_reference_point`.

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      Where the model has it, or None when there is nothing modelling it yet.
    """
    if channel >= len(self.resources) or self.deck is None:
      return None
    resource = self.resources[channel]
    if resource.location is None or resource.parent is None:
      return None
    return (
      resource.location
      + resource.parent.get_location_wrt(self.deck)
      + self._reference_anchor(resource)
    )

  def update_location_by_reference_point(
    self, channel: int, y: Optional[float] = None, z: Optional[float] = None
  ) -> None:
    """Record where a channel is on the resource that models it.

    Y and Z only: a channel rides the arm, so its resource is a child of the arm's and follows it in X.
    Positions are reported in the deck's frame and a resource is located in its parent's, so the arm's
    position is taken out before either is recorded. Does nothing when nothing models the channel.

    Args:
      channel: which channel, 0-indexed from the back.
      y: where it is now, in mm on the deck. Left as it was when None.
      z: where the end of its shaft is now, in mm on the deck. Left as it was when None.
    """
    if channel >= len(self.resources) or self.deck is None:
      return
    resource = self.resources[channel]
    if resource.location is None or resource.parent is None:
      return
    here, on_the_arm = resource.location, resource.parent.get_location_wrt(self.deck)
    anchor = self._reference_anchor(resource)
    resource.location = Coordinate(
      here.x,
      here.y if y is None else y - on_the_arm.y - anchor.y,
      here.z if z is None else z - on_the_arm.z - anchor.z,
    )

  @staticmethod
  def add_tip_mounting_shaft(channel: Resource) -> None:
    """Hang a tip mounting shaft off the lower end of a channel, and measure the channel from it.

    As the STAR driver hangs one: its own length below the channel's bottom, centred on the channel. A
    shaft already there is left alone, and repeated setups do not duplicate it.

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
    channel.reference_point = Coordinate(  # type: ignore[attr-defined]
      channel.get_absolute_size_x() / 2,
      channel.get_absolute_size_y() / 2,
      -shaft.get_absolute_size_z(),
    )

  def _record_positions(self, positions: List[Coordinate]) -> None:
    """Record reported positions on the arm and the channels: X on the arm, Y and Z on each channel."""
    arm = None if self._driver is None else self._driver.x_arm
    if positions and arm is not None:
      arm.update_location_by_reference_point(positions[0].x)
    for channel, position in enumerate(positions):
      self.update_location_by_reference_point(channel, y=position.y, z=position.z)

  # -- channel initialization ----------------------------------------------------------------------

  async def sense_tip_presence(self) -> list[bool]:
    """Sense whether a tip is physically present on each pipettor channel via the sleeve sensor.

    Resolves each channel's Squeeze.SDrive object from the firmware tree, then
    finds GetTipPresent by name in that object's method table. The query uses
    the interface and method IDs declared by the firmware. Method tables are
    cached by the connection's introspection instance.

    Returns:
      List of bools, one per channel (index 0=rearmost). True if tip detected.
    """

    drive_map = await self._client.request_channel_drives(root_name="Channel Root")
    if not drive_map.sleeve_sensor_addrs:
      raise RuntimeError("No channel sleeve sensor addresses discovered.")

    results: list[bool] = []
    for addr in drive_map.sleeve_sensor_addrs:
      method = await self._client.introspection.get_method_by_name(addr, "GetTipPresent")
      raw = await self._client.execute(
        PrepCmd.PrepProbeRequest(
          dest=addr, command_id=method.method_id, interface_id=method.interface_id
        )
      )
      if raw is None or len(raw) < 8:
        results.append(False)
      else:
        val = _struct.unpack_from("<I", raw, 4)[0]
        results.append(bool(val))

    return results

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  def _resolve_traverse_height(self, final_z: Optional[float] = None) -> float:
    """Resolve final_z: explicit arg > user-set default > probed value. Raises if none available."""
    if final_z is not None:
      return final_z
    if self.default_minimum_traverse_height is not None:
      return self.default_minimum_traverse_height
    try:
      cfg = self._configuration
    except RuntimeError:
      height: Optional[float] = None
    else:
      height = cfg.default_traverse_height
    if height is not None:
      return height
    raise RuntimeError(
      "Default traverse height is required for this operation but could not be determined. "
      "Either pass final_z explicitly to this call, or set it via "
      "Pipettes(..., default_traverse_height=<mm>) or pipettes.default_minimum_traverse_height. "
      "If the instrument supports it, the value is also probed during setup(); ensure setup() completed successfully."
    ) from None

  async def request_channel_bounds(self) -> List[ChannelBounds]:
    """Request per-channel movement bounds from the firmware (cmd=10).

    Returns one dict per channel (keys ``x_min``, ``x_max``, ``y_min``, ``y_max``,
    ``z_min``, ``z_max`` in mm), ordered by channel index. Returns ``[]`` when
    the service cannot be resolved or the response is empty.

    These are the firmware-enforced limits — positions outside these ranges will
    be rejected with 0x0F04 (X), 0x0F05 (Y), or 0x0F06 (Z). Z bounds are for
    empty channels; with a tip attached the effective Z minimum is higher.
    """
    try:
      response = await self._client.execute(PrepCmd.PrepGetChannelBounds())
    except KeyError:
      return []
    channel_indices = {int(value): index for index, value in _CHANNEL_INDEX.items()}
    indexed: list[tuple[int, ChannelBounds]] = []
    for bounds in response.bounds:
      index = channel_indices.get(int(bounds.channel))
      if index is not None:
        indexed.append(
          (
            index,
            {
              "x_min": bounds.x_min,
              "x_max": bounds.x_max,
              "y_min": bounds.y_min,
              "y_max": bounds.y_max,
              "z_min": bounds.z_min,
              "z_max": bounds.z_max,
            },
          )
        )
    return [bounds for _, bounds in sorted(indexed, key=lambda pair: pair[0])]

  async def request_channel_positions(self) -> list[Coordinate]:
    """Request the current XYZ positions of all pipettor channels.

    Queries Pipettor.GetPositions (cmd=25). Returns one Coordinate per channel,
    ordered by channel index (0=rearmost).

    Uses the typed PrepGetPositions command with ChannelXYZPositionParameters
    response struct for reliable parsing across firmware versions.

    Returns:
      List of Coordinate, one per channel.
    """
    positions = await self._unchecked_fw_request_positions()
    # The device is the authority on where the channels are, so what it answers is recorded.
    self._record_positions(positions)
    return positions

  async def _unchecked_fw_request_positions(self) -> list[Coordinate]:
    """Read where every channel is, without recording it.

    The reading alone. `request_channel_positions`, `request_y_positions` and
    `request_tool_bottom_z_positions` are the ones that also record it on the resources. Z is the
    bottom of the tip on a channel carrying one, and the end of its shaft otherwise.

    Returns:
      One Coordinate per channel, ordered by channel index (0=rearmost). Empty when the device did not
      answer with positions.
    """
    try:
      resp_obj = await self._client.execute(PrepCmd.PrepGetPositions())
    except (HoiError, ChannelizedError):
      return []
    if not isinstance(resp_obj, PrepCmd.PrepGetPositions.Response):
      return []
    resp = resp_obj
    if not resp.positions:
      return []

    _CHANNEL_ENUM_TO_IDX = {int(v): k for k, v in _CHANNEL_INDEX.items()}
    indexed: list[tuple[int, Coordinate]] = []
    for p in resp.positions:
      ch_idx = _CHANNEL_ENUM_TO_IDX.get(p.channel)
      if ch_idx is not None:
        indexed.append((ch_idx, Coordinate(x=p.position_x, y=p.position_y, z=p.position_z)))

    indexed.sort(key=lambda pair: pair[0])
    return [coord for _, coord in indexed]

  # -- x position ----------------------------------------------------------------------------------

  async def request_x_position(self) -> float:
    """Request X position of the gantry, which all channels share (in mm).

    Analogous to STARBackend.request_x_position().

    Returns:
      X position in mm.

    Raises:
      RuntimeError: If the channels report no positions.
    """
    positions = await self.request_channel_positions()
    if not positions:
      raise RuntimeError("the channels reported no positions")
    return float(positions[0].x)

  async def move_to_x_position(self, x: float) -> None:
    """Move the gantry X axis to a position (in mm).

    On the Prep, X is shared across all channels (single gantry): all channels move together in X.

    Analogous to STARBackend.move_to_x_position().

    Args:
      x: Target X position in mm.

    Raises:
      RuntimeError: If the channels report no positions.
    """
    positions = await self.request_channel_positions()
    if not positions:
      raise RuntimeError("the channels reported no positions")
    await self.move_to_coordinate(Coordinate(x, positions[0].y, positions[0].z), use_channels=0)

  # -- y position ----------------------------------------------------------------------------------

  async def request_y_positions(self) -> List[float]:
    """Request where every channel is along Y, in one command.

    `GetPositions` answers for all of them at once. Each answer is recorded on the resource modelling
    that channel, as the STAR driver's `request_y_positions` records it.

    Returns:
      The position of each channel in mm, back to front.
    """
    positions = [coord.y for coord in await self._unchecked_fw_request_positions()]
    for channel, y in enumerate(positions):
      self.update_location_by_reference_point(channel, y=y)
    return positions

  async def request_y_position(self, channel: int) -> float:
    """Request Y position of pipettor channel n (in mm).

    Analogous to STARBackend.request_y_position().

    Args:
      channel: Channel index (0=rearmost).

    Returns:
      Y position in mm.
    """
    positions = await self.request_channel_positions()
    if channel >= len(positions):
      raise ValueError(f"Channel {channel} out of range ({len(positions)} channels).")
    return float(positions[channel].y)

  async def move_to_y_position(self, channel: int, y: float) -> None:
    """Move a channel in the Y direction (in mm).

    Analogous to STARBackend.move_to_y_position().

    Args:
      channel: Channel index (0=rearmost).
      y: Target Y position in mm.
    """
    positions = await self.request_channel_positions()
    if channel >= len(positions):
      raise ValueError(f"Channel {channel} out of range ({len(positions)} channels).")
    await self.move_to_coordinate(
      Coordinate(positions[channel].x, y, positions[channel].z), use_channels=channel
    )

  # -- z position ----------------------------------------------------------------------------------

  async def request_z_position(self, channel: int) -> float:
    """Request Z position of pipettor channel n (in mm).

    Analogous to STARBackend.request_z_position().

    Args:
      channel: Channel index (0=rearmost).

    Returns:
      Z position in mm.
    """
    positions = await self.request_channel_positions()
    if channel >= len(positions):
      raise ValueError(f"Channel {channel} out of range ({len(positions)} channels).")
    return float(positions[channel].z)

  async def request_tool_bottom_z_positions(self) -> Dict[int, float]:
    """Read where the bottom of the tip on every channel is.

    Every channel has to carry one, as the STAR driver requires: a channel with no tip has no tool
    bottom. Records each channel's shaft end on the resource modelling it.

    Returns:
      The bottom of each channel's tip in mm, keyed by channel, 0-indexed from the back.

    Raises:
      ValueError: If any channel carries no tip.
    """
    tips = await self.sense_tip_presence()
    missing = [
      channel for channel in range(self.num_channels) if channel >= len(tips) or not tips[channel]
    ]
    if missing:
      raise ValueError(f"channels {missing} carry no tip, so they have no tool bottom to read")
    bottoms = {
      channel: coord.z for channel, coord in enumerate(await self._unchecked_fw_request_positions())
    }
    # What comes back is each tip's bottom, but the model references the ends of the shafts, so those
    # are read for the model.
    for channel in bottoms:
      self.update_location_by_reference_point(
        channel, z=await self.request_stop_disc_z_position(channel)
      )
    return bottoms

  async def request_tool_bottom_z_position(self, channel: int) -> float:
    """Request the Z position of the tip bottom on the specified channel.

    GetPositions returns tip-adjusted Z when a tip is mounted — the reported Z
    is the tip bottom position, not the channel head. Verified empirically:
    channel at traverse (167.5mm) with 50uL NTR tip (extension 42.4mm) reports
    Z=125.1mm = 167.5 - 42.4.

    Requires a tip to be mounted (verified via sleeve sensor).

    Analogous to STARBackend.request_tool_bottom_z_position().

    Args:
      channel: Channel index (0=rearmost).

    Returns:
      Tip bottom Z position in mm.

    Raises:
      RuntimeError: If no tip is present on the channel.
    """
    tip_presence = await self.sense_tip_presence()
    if channel >= len(tip_presence) or not tip_presence[channel]:
      raise RuntimeError(f"No tip mounted on channel {channel}")

    return await self.request_z_position(channel)

  async def request_stop_disc_z_position(self, channel: int) -> float:
    """Request the Z position of the channel probe/head (excluding tip).

    Since GetPositions returns tip-adjusted Z when a tip is mounted, this
    method queries the firmware's held tip definition (GetTipDefinitionHeld on the
    Pipettor, looked up by name) to get the tip length and adds it back.

    When no tip is mounted, returns the same value as request_z_position().

    Analogous to STARBackend.request_stop_disc_z_position().

    Args:
      channel: Channel index (0=rearmost).

    Returns:
      Channel head Z position in mm (excluding tip).
    """
    z = await self.request_z_position(channel)
    tip_presence = await self.sense_tip_presence()
    if channel < len(tip_presence) and tip_presence[channel]:
      # Query firmware for the held tip definition to get tip length
      # By name: the method's ids are not the same on every firmware version.
      raw = await self._client.request_by_name(PIPETTOR_OBJECT_PATH, "GetTipDefinitionHeld")
      if raw is not None:
        import struct as _struct

        data = raw
        # TipDefinition struct: default_values, id, volume(F32), length(F32), ...
        # The second F32 is the tip extension length
        f32_count = 0
        i = 0
        while i < len(data) - 7:
          if data[i] == 0x28 and data[i + 1] == 0x00:
            f32_count += 1
            if f32_count == 2:  # second F32 = length
              tip_length = _struct.unpack_from("<f", data, i + 4)[0]
              if tip_length > 0:
                z += tip_length
              break
            i += 8
          else:
            i += 1
    return z

  async def move_tool_bottom_to_z_position(self, channel: int, z: float) -> None:
    """Move the bottom of the tool on one channel along Z.

    The Prep positions the tool bottom: the end of the tip when one is mounted, the end of the tip
    mounting shaft when none is. `GetPositions` reports the same point (see
    `request_tool_bottom_z_position`).

    Args:
      channel: which channel to move, 0-indexed from the back.
      z: where to put the bottom of its tool, in mm on the deck.

    Raises:
      ValueError: If the channel does not exist, or cannot reach `z`.
    """
    positions = await self.request_channel_positions()
    if channel >= len(positions):
      raise ValueError(f"Channel {channel} out of range ({len(positions)} channels).")
    await self.move_to_coordinate(
      Coordinate(positions[channel].x, positions[channel].y, z), use_channels=channel
    )

  async def move_to_safe_z(self, channels: Optional[List[int]] = None) -> None:
    """Move the given channels' Z axes up to safe (traverse) height (cmd=28).

    Use after picking up a tool or before returning a tool to avoid collisions
    during XY moves. The instrument uses its configured safe/traverse height;
    no height parameter is sent.

    Args:
      channels: Channel indices to move (0=rearmost). None = all channels.
    """
    if channels is None:
      channels = list(range(self.num_channels))
    else:
      channels = sorted(set(channels))
    if not channels:
      return
    if max(channels) >= self.num_channels or min(channels) < 0:
      raise ValueError(f"channels must be between 0 and {self.num_channels - 1}, are {channels}")
    try:
      await self._unchecked_fw_move_z_up_to_safe(channels)
      # Nothing to record as asked: the firmware chooses the height. The read below records it.
    finally:
      await self._record_where_they_stopped()

  async def _unchecked_fw_move_z_up_to_safe(self, channels: List[int]) -> None:
    """Send MoveZUpToSafe for these channels. Nothing is guarded and nothing is recorded.

    Args:
      channels: channel indices, 0-indexed from the back.
    """
    await self._client.execute(
      PrepCmd.PrepMoveZUpToSafe(channels=[_CHANNEL_INDEX[ch] for ch in channels])
    )

  # -- xyz position --------------------------------------------------------------------------------

  async def move_to_coordinate(
    self,
    location: Union[Coordinate, List[Coordinate]],
    use_channels: Optional[Union[int, List[int]]] = 0,
    *,
    via_lane: bool = False,
    x_speed: Optional[float] = None,
    x_speed_scale: Optional[int] = None,
    z_speed_scale: Optional[int] = None,
  ) -> None:
    """Move channels to locations on the deck (cmd=26, or 27 via the lane).

    The channels ride one gantry, so they share X: every location must name the same x.

    The move command carries no speed. What the firmware offers is MLPrep's X and Z speed scales,
    which hold for every move until changed, so a scale given here is set for this move and the one
    that was set before is put back afterwards, whether or not the move succeeded. Y has no scale.

    Args:
      location: where to send each channel's reference point, in mm on the deck. One location for
        every channel named, or one per channel, in the order of `use_channels`.
      use_channels: which channels, 0-indexed from the back. One index, or a list. Defaults to 0.
      via_lane: travel by the firmware's lane rather than directly.
      x_speed: how fast to drive X for this move, in mm/s. Set as the nearest X speed scale, so it
        is rounded to whole multiples of `XArmConfiguration.speed_per_scale_percent`. Defaults to
        `default_x_speed`.
      x_speed_scale: overrides `x_speed` with the X speed scale itself, in percent, 1 to 100. Give
        one or the other, not both.
      z_speed_scale: how fast to drive Z for this move, in percent of full speed, 1 to 100. None
        keeps the scale MLPrep has.

    Raises:
      ValueError: If a channel does not exist, the locations do not match the channels, they do not
        share one x, a location is outside a channel's reach, a speed or speed scale is out of
        range, or both `x_speed` and `x_speed_scale` are given.
      RuntimeError: If a speed scale is given but there is no driver to set it through.
    """
    if x_speed is not None and x_speed_scale is not None:
      raise ValueError("give x_speed or x_speed_scale, not both")
    if x_speed is None and x_speed_scale is None:
      x_speed = self.default_x_speed
    if x_speed is not None:
      arm = None if self._driver is None else self._driver.x_arm
      x_speed_scale = (
        arm.configuration if arm is not None else XArmConfiguration()
      ).speed_to_scale_percent(x_speed)
    for axis, scale in (("x", x_speed_scale), ("z", z_speed_scale)):
      if scale is not None and not 1 <= scale <= 100:
        raise ValueError(f"{axis} speed scale must be between 1 and 100 percent, is {scale}")
    if (x_speed_scale is not None or z_speed_scale is not None) and self._driver is None:
      raise RuntimeError("speed scales are set through the driver, and this has none")
    if use_channels is None:
      named = [0]
    elif isinstance(use_channels, list):
      named = list(use_channels)
    else:
      # int or int-like (e.g. numpy.int64); single channel
      named = [int(use_channels)]
    if named and (max(named) >= self.num_channels or min(named) < 0):
      raise ValueError(f"use_channels must be between 0 and {self.num_channels - 1}, are {named}")
    locations = location if isinstance(location, list) else [location] * len(named)
    if len(locations) != len(named):
      raise ValueError(f"{len(locations)} locations given for {len(named)} channels")
    if len({loc.x for loc in locations}) > 1:
      raise ValueError(
        f"the channels share one gantry, so every location needs the same x; got "
        f"{sorted({loc.x for loc in locations})}"
      )
    # Each location stays with the channel it was named for, in channel order.
    paired = sorted(zip(named, locations), key=lambda pair: pair[0])
    channels = [channel for channel, _ in paired]
    x = locations[0].x if locations else 0.0
    y_vals = [loc.y for _, loc in paired]
    z_vals = [loc.z for _, loc in paired]

    # Validate against per-channel movement bounds (cached from firmware at setup).
    for i, (y_i, z_i) in enumerate(zip(y_vals, z_vals)):
      ch = channels[i]
      if ch < len(self.configuration.channels):
        c = self.configuration.channels[ch]
        if c.x_range is not None and not c.x_range[0] <= x <= c.x_range[1]:
          raise ValueError(
            f"x={x} outside channel {ch} range [{c.x_range[0]:.1f}, {c.x_range[1]:.1f}]"
          )
        if c.y_range is not None and not c.y_range[0] <= y_i <= c.y_range[1]:
          raise ValueError(
            f"y={y_i} outside channel {ch} range [{c.y_range[0]:.1f}, {c.y_range[1]:.1f}]"
          )
        if c.z_range is not None and z_i > c.z_range[1]:
          raise ValueError(f"z={z_i} above channel {ch} maximum {c.z_range[1]:.1f}")

    # The scales in force before this move, to put back once it is done.
    restore_x: Optional[int] = None
    restore_z: Optional[int] = None
    try:
      if self._driver is not None and x_speed_scale is not None:
        restore_x = await self._driver.request_x_speed_scale()
        await self._driver.set_x_speed_scale(x_speed_scale)
      if self._driver is not None and z_speed_scale is not None:
        restore_z = await self._driver.request_z_speed_scale()
        await self._driver.set_z_speed_scale(z_speed_scale)
      await self._unchecked_fw_move_to_position(x, channels, y_vals, z_vals, via_lane=via_lane)
      # What was asked, recorded as soon as the command answers; the read below replaces it with
      # where the channels actually stopped.
      arm = None if self._driver is None else self._driver.x_arm
      if arm is not None:
        arm.update_location_by_reference_point(x)
      for channel, y_i, z_i in zip(channels, y_vals, z_vals):
        self.update_location_by_reference_point(channel, y=y_i, z=z_i)
    finally:
      try:
        if self._driver is not None and restore_x is not None:
          await self._driver.set_x_speed_scale(restore_x)
        if self._driver is not None and restore_z is not None:
          await self._driver.set_z_speed_scale(restore_z)
      finally:
        await self._record_where_they_stopped()

  async def _unchecked_fw_move_to_position(
    self,
    x: float,
    channels: List[int],
    y: Union[float, List[float]],
    z: Union[float, List[float]],
    via_lane: bool = False,
  ) -> None:
    """Send the gantry move (cmd=26, or 27 via the lane). Nothing is guarded and nothing is recorded.

    Args:
      x: where to send the gantry, in mm.
      channels: which channels, sorted, 0-indexed from the back.
      y: where to send each channel along Y, in mm; one value for all, or one per channel.
      z: where to send each channel along Z, in mm; one value for all, or one per channel.
      via_lane: travel by the firmware's lane rather than directly.
    """
    move_parameters = _build_pipettor_gantry_move_parameters(x, channels, y, z)
    if via_lane:
      await self._client.execute(PrepCmd.PrepMoveToPositionViaLane(move_parameters=move_parameters))
    else:
      await self._client.execute(PrepCmd.PrepMoveToPosition(move_parameters=move_parameters))

  # ----------------------------------------
  # Probing
  # ----------------------------------------

  # -- x probing (capacitive only) -----------------------------------------------------------------

  async def clld_probe_x_position_using_channel(self, *args, **kwargs):
    """Probe X position using capacitive LLD. Not yet implemented for the Prep.

    TODO: Investigate ChannelCoordinator [1:17] MoveChannelAxisAbsolute and
    [1:18] MoveChannelAxisRelative for X-axis probing with cLLD feedback.
    The ChannelCoordinator also has [1:19] YSeekLldPosition which may have
    an X equivalent, though none was found in introspection.
    """
    raise NotImplementedError(
      "clld_probe_x_position_using_channel is not yet implemented for Pipettes."
    )

  # -- y probing (capacitive only) -----------------------------------------------------------------

  async def clld_probe_y_position_using_channel(self, *args, **kwargs):
    """Probe Y position using capacitive LLD. Not yet implemented for the Prep.

    TODO: Investigate ChannelCoordinator [1:19] YSeekLldPosition(seekParameters)
    which takes a YLLDSeekParameters struct and returns SeekResultParameters.
    Also Channel [1:11] LeakCheck has ySeekDistance/yPreloadDistance params
    which suggest Y-axis seeking capability.
    """
    raise NotImplementedError(
      "clld_probe_y_position_using_channel is not yet implemented for Pipettes."
    )

  # -- z probing (capacitive, force) ---------------------------------------------------------------

  async def clld_probe_z_height_using_channel(self, *args, **kwargs):
    """Probe Z-height using capacitive LLD. Not yet implemented for the Prep.

    TODO: Implement using the standalone ZSeekLldPosition command:
    - Pipettor [1:29] ZSeekLldPosition(seekParameters) -> results: SeekResultParameters
    - ChannelCoordinator [1:20] ZSeekLldPosition(seekParameters) -> results: SeekResultParameters
    Previously returned HC_RESULT=0x0F06 which was assumed to be "LLD not supported".
    Now identified as "Z position out of allowed movement range" — the Z parameters
    in LLDChannelSeekParameters were out of bounds. Retry with valid Z values
    within deck_bounds (min_z=18.03, max_z=167.5).

    Findings from testing:
    - cLLD DOES work through the aspirate path (aspirate with
      lld_mode=[LLDMode.CAPACITIVE] and default_values=False on both
      LldParameters and CLldParameters).
    - Standalone ZSeekLldPosition is rejected with 0x0F06 when Z params are out of range.
    - The aspirate-based approach is a workaround, not a proper standalone probe.

    Also investigate ZAxis-level alternatives:
    - ZAxis.SeekCapacitiveLld [1:12] (returns 0x0207 when called directly)
    - ZAxis.SeekCapacitiveLldTip [1:13] (returns 0x0207 when called directly)
    - ZAxis.LiquidStatus [1:16] for reading last detection results
    - PipettorService.MeasureLldFrequency [1:6] for sensor health checks
    """
    raise NotImplementedError(
      "clld_probe_z_height_using_channel is not yet implemented for Pipettes."
    )

  async def ztouch_probe_z_height_using_channel(self, *args, **kwargs):
    """Probe Z-height using force/motor stall detection. Not yet implemented for the Prep.

    TODO: Investigate force-based Z probing commands:
    - ZAxis.SeekObstacle [1:14] SeekObstacle(startPosition, endPosition, finalPosition, velocity)
      Currently returns 0x0207 when called directly — needs coordinator routing.
    - Calibration.ZTouchoff [1:8] — runs a Z touchoff calibration (force-based).
    - The STAR implements this via a dedicated "ZH" firmware command with PWM-based
      force detection. The Prep may have an equivalent through the ChannelCoordinator
      but it was not found in introspection.
    """
    raise NotImplementedError(
      "ztouch_probe_z_height_using_channel is not yet implemented for Pipettes."
    )

  # -- shutdown / serialization --------------------------------------------------------------------

  async def stop(self) -> None:
    self.setup_finished = False

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "default_minimum_traverse_height": self.default_minimum_traverse_height,
      "use_v1_aspirate_dispense": self.configuration.use_v1_aspirate_dispense,
    }

  # ----------------------------------------
  # Tips and liquid handling
  # ----------------------------------------

  # -- tip pickup / drop ---------------------------------------------------------------------------

  def get_mounted_tips(self) -> List[Optional[Tip]]:
    """Tips currently mounted on the dual-channel head (``None`` if empty)."""
    self._ensure_head()
    return [
      self.head[i].get_tip() if self.head[i].has_tip else None for i in range(self.num_channels)
    ]

  def _require_mounted_tips(self, use_channels: List[int]) -> List[Tip]:
    self._ensure_head()
    tips: List[Tip] = []
    for ch in use_channels:
      tracker = self.head[ch]
      if not tracker.has_tip:
        raise RuntimeError(f"No tip mounted on channel {ch}; call pick_up_tips first.")
      tips.append(tracker.get_tip())
    return tips

  async def _finalize_channel_command(
    self,
    use_channels: Sequence[int],
    *,
    tip_intents: Optional[Sequence[Union[TipPickupIntent, TipDropIntent]]] = None,
    volume_intents: Optional[Sequence[VolumeTransferIntent]] = None,
    send: Callable[[], Awaitable[None]],
  ) -> None:
    """Send a Prep command and commit/rollback queued tip or volume intents."""
    error: Optional[BaseException] = None
    try:
      await send()
      successes = all_channels_succeeded(use_channels)
    except ChannelizedError as e:
      error = e
      successes = successes_from_failed_channels(use_channels, e.errors)
    except BaseException as e:
      error = e
      successes = {ch: False for ch in use_channels}
    if tip_intents is not None:
      finalize_tip_ops(tip_intents, successes)
    if volume_intents is not None:
      finalize_volume_ops(volume_intents, successes)
    if error is not None:
      raise error

  async def pick_up_tips(
    self,
    tip_spots: Sequence[TipSpot],
    use_channels: Optional[List[int]] = None,
    *,
    offsets: Optional[Sequence[Coordinate]] = None,
    final_z: Optional[float] = None,
    seek_speed: float = 15.0,
    z_seek_offset: Optional[float] = None,
    enable_tadm: bool = False,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    pre_position: bool = True,
  ):
    """Pick up tips from tip spots.

    The arm moves to z_seek during lateral XY approach, then descends to z_position
    to engage the tip. Default z_seek = z_position + fitting_depth + 5mm (tip-type-
    aware; avoids descending into the rack during approach).
    """
    tip_spots = list(tip_spots)
    use_channels = use_channels if use_channels is not None else list(range(len(tip_spots)))
    if len(tip_spots) != len(use_channels):
      raise ValueError(
        f"len(tip_spots) must equal len(use_channels): {len(tip_spots)} != {len(use_channels)}"
      )
    if use_channels and max(use_channels) >= self.num_channels:
      raise ValueError(f"use_channels index out of range (valid: 0..{self.num_channels - 1})")
    offsets_list = list(offsets) if offsets is not None else [Coordinate.zero()] * len(tip_spots)
    if len(offsets_list) != len(tip_spots):
      raise ValueError("len(offsets) must equal len(tip_spots)")

    tips = [spot.get_tip() for spot in tip_spots]
    resolved_final_z = self._resolve_traverse_height(final_z)

    indexed = {
      ch: (spot, tip, off)
      for ch, spot, tip, off in zip(use_channels, tip_spots, tips, offsets_list)
    }
    tip_positions: List[PrepCmd.TipPositionParameters] = []
    for ch in range(self.num_channels):
      if ch not in indexed:
        continue
      spot, tip, off = indexed[ch]
      loc = spot.get_location_wrt(self._require_deck(), "c", "c", "t") + off
      tip_positions.append(
        PrepCmd.TipPositionParameters.for_op(
          _CHANNEL_INDEX[ch], loc, tip, z_seek_offset=z_seek_offset
        )
      )

    tip0 = tips[0]
    if any(
      t.maximal_volume != tip0.maximal_volume
      or t.has_filter != tip0.has_filter
      or (t.total_tip_length - t.fitting_depth) != (tip0.total_tip_length - tip0.fitting_depth)
      for t in tips
    ):
      raise ValueError("All tip spots must use the same tip type")
    tip_definition = PrepCmd.TipPickupParameters(
      default_values=False,
      volume=tip0.maximal_volume,
      length=tip0.total_tip_length - tip0.fitting_depth,
      tip_type=PrepCmd.TipTypes.StandardVolume,
      has_filter=tip0.has_filter,
      is_needle=False,
      is_tool=False,
    )

    if pre_position:
      traverse_h = minimum_traverse_height_at_beginning_of_a_command or resolved_final_z
      locs = [
        indexed[ch][0].get_location_wrt(self._require_deck(), "c", "c", "t") + indexed[ch][2]
        for ch in use_channels
      ]
      await self.move_to_coordinate(
        [Coordinate(locs[0].x, loc.y, traverse_h) for loc in locs],
        use_channels=use_channels,
      )

    self._ensure_head()
    tip_intents = [
      TipPickupIntent(
        channel=ch,
        tip_spot=spot,
        tip=tip,
        channel_tracker=self.head[ch],
      )
      for ch, spot, tip in zip(use_channels, tip_spots, tips)
    ]
    queue_tip_pickups(tip_intents)

    async def _send() -> None:
      await self._client.execute(
        PrepCmd.PrepPickUpTips(
          tip_positions=tip_positions,
          final_z=resolved_final_z,
          seek_speed=seek_speed,
          tip_definition=tip_definition,
          enable_tadm=enable_tadm,
          dispenser_volume=dispenser_volume,
          dispenser_speed=dispenser_speed,
        )
      )

    await self._finalize_channel_command(use_channels, tip_intents=tip_intents, send=_send)

  async def drop_tips(
    self,
    destinations: Sequence[Union[TipSpot, Trash]],
    use_channels: Optional[List[int]] = None,
    *,
    offsets: Optional[Sequence[Coordinate]] = None,
    final_z: Optional[float] = None,
    seek_speed: float = 15.0,
    z_seek_offset: Optional[float] = None,
    drop_type: PrepCmd.TipDropType = PrepCmd.TipDropType.FixedHeight,
    tip_roll_off_distance: float = 0.0,
  ):
    """Drop tips to tip spots or trash.

    The arm moves to z_seek during lateral XY approach (tip is on pipette, so tip
    bottom is at z_seek - (total_tip_length - fitting_depth)). z_position uses
    fitting depth so the tip bottom lands at the spot surface; default z_seek =
    z_position + 10mm so the tip bottom stays above adjacent tips in the rack.
    """
    destinations = list(destinations)
    use_channels = use_channels if use_channels is not None else list(range(len(destinations)))
    if len(destinations) != len(use_channels):
      raise ValueError(
        f"len(destinations) must equal len(use_channels): "
        f"{len(destinations)} != {len(use_channels)}"
      )
    if use_channels and max(use_channels) >= self.num_channels:
      raise ValueError(f"use_channels index out of range (valid: 0..{self.num_channels - 1})")
    tips = self._require_mounted_tips(use_channels)
    offsets_list = list(offsets) if offsets is not None else [Coordinate.zero()] * len(destinations)
    if len(offsets_list) != len(destinations):
      raise ValueError("len(offsets) must equal len(destinations)")

    all_trash = all(isinstance(d, Trash) for d in destinations)
    all_tip_spots = all(isinstance(d, TipSpot) for d in destinations)
    if not (all_trash or all_tip_spots):
      raise ValueError("Cannot mix waste (Trash) and tip spots in a single drop_tips call.")

    resolved_final_z = self._resolve_traverse_height(final_z)
    roll_off = 3.0 if (all_trash and tip_roll_off_distance == 0.0) else tip_roll_off_distance
    resolved_drop_type = PrepCmd.TipDropType.Stall if all_trash else drop_type

    indexed = {
      ch: (dest, tip, off)
      for ch, dest, tip, off in zip(use_channels, destinations, tips, offsets_list)
    }
    tip_positions: List[PrepCmd.TipDropParameters] = []
    for ch in range(self.num_channels):
      if ch not in indexed:
        continue
      dest, tip, off = indexed[ch]
      if all_trash:
        if self.deck is None:
          raise ValueError(
            "Cannot drop tips to waste: backend has no deck (assign a deck before drop_tips)."
          )
        waste_name = _CHANNEL_TO_WASTE_NAME.get(ch, "waste_mph")
        if not self.deck.has_resource(waste_name):
          raise ValueError(
            f"Cannot drop tips to waste: deck has no waste position '{waste_name}'. "
            "Use a deck with waste_rear, waste_front (and waste_mph if using MPH)."
          )
        loc = self.deck.get_resource(waste_name).get_location_wrt(self.deck, "c", "c", "t")
      else:
        loc = dest.get_location_wrt(self._require_deck(), "c", "c", "t") + off
      tip_positions.append(
        PrepCmd.TipDropParameters.for_op(
          _CHANNEL_INDEX[ch], loc, tip, z_seek_offset=z_seek_offset, drop_type=resolved_drop_type
        )
      )

    tip_intents = [
      TipDropIntent(
        channel=ch,
        destination=dest,
        tip=tip,
        channel_tracker=self.head[ch],
      )
      for ch, dest, tip in zip(use_channels, destinations, tips)
    ]
    queue_tip_drops(tip_intents)

    async def _send() -> None:
      await self._client.execute(
        PrepCmd.PrepDropTips(
          tip_positions=tip_positions,
          final_z=resolved_final_z,
          seek_speed=seek_speed,
          tip_roll_off_distance=roll_off,
        )
      )

    await self._finalize_channel_command(use_channels, tip_intents=tip_intents, send=_send)

  def can_pick_up_tip(self, channel: int, tip: Tip) -> bool:
    """Check if the tip can be picked up by the specified channel.

    Uses the same logic as Nimbus/STAR: only Hamilton tips, no XL tips,
    and channel index must be valid.
    """
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    try:
      n = self._configuration.num_channels
    except RuntimeError:
      n = None
    if n is not None and channel >= n:
      return False
    return True

  # -- v1/v2 aspirate/dispense dispatch helpers ----------------------------------------------------

  @staticmethod
  def _patch_common_with_cone(
    common: PrepCmd.CommonParameters, segments: list[PrepCmd.SegmentDescriptor]
  ) -> PrepCmd.CommonParameters:
    return patch_common_with_cone(common, segments)

  # -- shared LLD / TADM resolution helpers --------------------------------------------------------

  def _resolve_effective_lld(
    self,
    lld_mode: Optional[List[Pipettes.LLDMode]],
    lld: Optional[PrepCmd.LldParameters],
    n: int,
    *,
    allowed_modes: Optional[frozenset[Pipettes.LLDMode]] = None,
  ) -> bool:
    """Determine whether LLD is active for this pipetting call.

    Validates ``lld_mode`` length, rejects disallowed modes (e.g. PRESSURE for
    dispense), enforces all-or-nothing across channels, and returns a single bool.
    Falls back to ``lld`` presence when ``lld_mode`` is None.
    """
    if lld_mode is not None:
      if len(lld_mode) != n:
        raise ValueError(f"lld_mode length must match len(ops): {len(lld_mode)} != {n}")
      if allowed_modes is not None:
        for m in lld_mode:
          if m != Pipettes.LLDMode.OFF and m not in allowed_modes:
            raise ValueError(
              f"Dispense does not support {m.name} LLD — only CAPACITIVE or OFF. "
              "Pressure-based LLD requires aspiration (plunger movement)."
            )
      lld_on = [m != Pipettes.LLDMode.OFF for m in lld_mode]
      if any(lld_on) and not all(lld_on):
        raise ValueError(
          "Prep firmware requires all channels to use the same LLD mode category. "
          "Cannot mix LLDMode.OFF with CAPACITIVE/PRESSURE/DUAL in one call. "
          "Split into separate calls for channels with different LLD modes."
        )
      return all(lld_on)
    return lld is not None

  @staticmethod
  def _default_lld_params(
    effective_lld: bool,
    p_lld: Optional[PrepCmd.PLldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
  ) -> Pipettes._LldDefaults:
    return default_lld_params(effective_lld, p_lld, c_lld)

  @staticmethod
  def _lld_for_well(
    effective_lld: bool, lld: Optional[PrepCmd.LldParameters], top_of_well_z: float
  ) -> PrepCmd.LldParameters:
    return lld_for_well(effective_lld, lld, top_of_well_z)

  # -- shared channel resolution -------------------------------------------------------------------

  def _resolve_channel_context(
    self,
    ops: Sequence[_OpT],
    use_channels: List[int],
    *,
    z_final: Optional[List[float]] = None,
    z_fluid: Optional[List[float]] = None,
    z_air: Optional[List[float]] = None,
    z_minimum: Optional[List[float]] = None,
    z_bottom_search_offset: Optional[List[float]] = None,
    container_segments: Optional[List[List[PrepCmd.SegmentDescriptor]]] = None,
    auto_container_geometry: bool = False,
    hamilton_liquid_classes: Optional[List[HamiltonLiquidClass]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
  ) -> _ChannelContext[_OpT]:
    """Resolve shared per-channel state for aspirate or dispense.

    Validates inputs, resolves HLCs, computes volume corrections, well geometry,
    z-parameter defaults, and container segments. Operation-specific defaults
    (settling_time, flow_rate, etc.) are left to the caller.
    """
    if len(ops) != len(use_channels):
      raise ValueError(f"len(ops) must equal len(use_channels): {len(ops)} != {len(use_channels)}")
    if use_channels and max(use_channels) >= self.num_channels:
      raise ValueError(f"use_channels index out of range (valid: 0..{self.num_channels - 1})")

    n = len(ops)
    if hamilton_liquid_classes is not None and len(hamilton_liquid_classes) != n:
      raise ValueError(
        f"hamilton_liquid_classes length must match len(ops): {len(hamilton_liquid_classes)} != {n}"
      )
    hlcs = resolve_hamilton_liquid_classes(
      list(hamilton_liquid_classes) if hamilton_liquid_classes is not None else None,
      list(ops),
      jet=False,
      blow_out=False,
    )
    dvc = disable_volume_correction if disable_volume_correction is not None else [False] * n
    if len(dvc) != n:
      raise ValueError(f"disable_volume_correction length must match len(ops): {len(dvc)} != {n}")
    ch_to_idx = {ch: i for i, ch in enumerate(use_channels)}
    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}

    volumes = corrected_volumes_for_ops(ops, hlcs, dvc)

    well_geometry = [
      _absolute_z_from_well(op.resource, self._require_deck(), op.liquid_height, op.offset.z)
      for op in ops
    ]
    raw_traverse = self._resolve_traverse_height(None)
    z_minimum = fill_in_defaults(z_minimum, [g.well_bottom for g in well_geometry])
    z_fluid = fill_in_defaults(z_fluid, [g.liquid_surface for g in well_geometry])
    z_air = fill_in_defaults(z_air, [g.z_air for g in well_geometry])
    z_final = fill_in_defaults(
      z_final, [raw_traverse - (op.tip.total_tip_length - op.tip.fitting_depth) for op in ops]
    )
    z_bottom_search_offset = fill_in_defaults(z_bottom_search_offset, [2.0] * n)

    ch_segments: dict[int, list[PrepCmd.SegmentDescriptor]] = {}
    for i, ch in enumerate(use_channels):
      if container_segments is not None and i < len(container_segments):
        ch_segments[ch] = container_segments[i]
      elif auto_container_geometry:
        ch_segments[ch] = _build_container_segments(indexed_ops[ch].resource)
      else:
        ch_segments[ch] = []

    return _ChannelContext(
      n=n,
      hlcs=hlcs,
      disable_volume_correction=dvc,
      ch_to_idx=ch_to_idx,
      indexed_ops=indexed_ops,
      volumes=volumes,
      well_geometry=well_geometry,
      z_minimum=z_minimum,
      z_fluid=z_fluid,
      z_air=z_air,
      z_final=z_final,
      z_bottom_search_offset=z_bottom_search_offset,
      ch_segments=ch_segments,
    )

  # -- aspirate: resolve, assemble, send -----------------------------------------------------------

  def _resolve_aspirate_channels(
    self,
    ops: List[_PipetteTransfer],
    use_channels: List[int],
    effective_lld: bool,
    *,
    z_final: Optional[List[float]] = None,
    z_fluid: Optional[List[float]] = None,
    z_air: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    z_liquid_exit_speed: Optional[List[float]] = None,
    prewet_volume: Optional[List[float]] = None,
    z_minimum: Optional[List[float]] = None,
    z_bottom_search_offset: Optional[List[float]] = None,
    lld: Optional[PrepCmd.LldParameters] = None,
    p_lld: Optional[PrepCmd.PLldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    tadm: Optional[PrepCmd.TadmParameters] = None,
    container_segments: Optional[List[List[PrepCmd.SegmentDescriptor]]] = None,
    auto_container_geometry: bool = False,
    hamilton_liquid_classes: Optional[List[HamiltonLiquidClass]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
  ) -> list[_AspirateChannelKit]:
    """Resolve all per-channel values for aspirate (pure computation, no I/O)."""
    ctx = self._resolve_channel_context(
      ops,
      use_channels,
      z_final=z_final,
      z_fluid=z_fluid,
      z_air=z_air,
      z_minimum=z_minimum,
      z_bottom_search_offset=z_bottom_search_offset,
      container_segments=container_segments,
      auto_container_geometry=auto_container_geometry,
      hamilton_liquid_classes=hamilton_liquid_classes,
      disable_volume_correction=disable_volume_correction,
    )

    # Aspirate-specific HLC defaults
    hlcs = ctx.hlcs
    settling_time = fill_in_defaults(
      settling_time, [hlc.aspiration_settling_time if hlc is not None else 1.0 for hlc in hlcs]
    )
    transport_air_volume = fill_in_defaults(
      transport_air_volume,
      [hlc.aspiration_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs],
    )
    z_liquid_exit_speed = fill_in_defaults(
      z_liquid_exit_speed, [hlc.aspiration_swap_speed if hlc is not None else 10.0 for hlc in hlcs]
    )
    prewet_volume = fill_in_defaults(
      prewet_volume,
      [hlc.aspiration_over_aspirate_volume if hlc is not None else 0.0 for hlc in hlcs],
    )
    flow_rates = [
      op.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 100.0)
      for op, hlc in zip(ops, hlcs)
    ]
    blowout_volumes = [
      op.blow_out_air_volume or (hlc.aspiration_blow_out_volume if hlc is not None else 0.0)
      for op, hlc in zip(ops, hlcs)
    ]

    lld_defaults = self._default_lld_params(effective_lld, p_lld, c_lld)
    _tadm = tadm or PrepCmd.TadmParameters.default()

    kits: list[_AspirateChannelKit] = []
    for ch in range(self.num_channels):
      if ch not in ctx.indexed_ops:
        continue
      idx = ctx.ch_to_idx[ch]
      asp = ctx.indexed_ops[ch]
      loc = asp.resource.get_location_wrt(self._require_deck(), "c", "c", "cavity_bottom")
      radius = _effective_radius(asp.resource)

      kits.append(
        _AspirateChannelKit(
          channel=_CHANNEL_INDEX[ch],
          aspirate=PrepCmd.AspirateParameters.from_location(
            loc, prewet_volume=prewet_volume[idx], blowout_volume=blowout_volumes[idx]
          ),
          common=PrepCmd.CommonParameters.for_op(
            ctx.volumes[idx],
            radius,
            flow_rate=flow_rates[idx],
            z_minimum=ctx.z_minimum[idx],
            z_final=ctx.z_final[idx],
            z_liquid_exit_speed=z_liquid_exit_speed[idx],
            transport_air_volume=transport_air_volume[idx],
            settling_time=settling_time[idx],
          ),
          segments=ctx.ch_segments[ch],
          no_lld=PrepCmd.NoLldParameters.for_fixed_z(
            ctx.z_fluid[idx], ctx.z_air[idx], z_bottom_search_offset=ctx.z_bottom_search_offset[idx]
          ),
          lld=self._lld_for_well(effective_lld, lld, ctx.well_geometry[idx].top_of_well),
          p_lld=lld_defaults.p_lld,
          c_lld=lld_defaults.c_lld,
          monitoring=PrepCmd.AspirateMonitoringParameters.default(),
          tadm=_tadm,
          mix=PrepCmd.MixParameters.default(),
          adc=PrepCmd.AdcParameters.default(),
        )
      )
    return kits

  @staticmethod
  def _assemble_aspirate_v2(
    kit: _AspirateChannelKit, effective_lld: bool, is_tadm: bool
  ) -> Union[
    PrepCmd.AspirateParametersLldAndTadm2,
    PrepCmd.AspirateParametersLldAndMonitoring2,
    PrepCmd.AspirateParametersNoLldAndTadm2,
    PrepCmd.AspirateParametersNoLldAndMonitoring2,
  ]:
    """Assemble a v2 aspirate parameter struct from pre-resolved kit values."""
    if effective_lld and is_tadm:
      return PrepCmd.AspirateParametersLldAndTadm2(
        default_values=False,
        channel=kit.channel,
        aspirate=kit.aspirate,
        container_description=kit.segments,
        common=kit.common,
        lld=kit.lld,
        p_lld=kit.p_lld,
        c_lld=kit.c_lld,
        mix=kit.mix,
        tadm=kit.tadm,
        adc=kit.adc,
      )
    elif effective_lld:
      return PrepCmd.AspirateParametersLldAndMonitoring2(
        default_values=False,
        channel=kit.channel,
        aspirate=kit.aspirate,
        container_description=kit.segments,
        common=kit.common,
        lld=kit.lld,
        p_lld=kit.p_lld,
        c_lld=kit.c_lld,
        mix=kit.mix,
        aspirate_monitoring=kit.monitoring,
        adc=kit.adc,
      )
    elif is_tadm:
      return PrepCmd.AspirateParametersNoLldAndTadm2(
        default_values=False,
        channel=kit.channel,
        aspirate=kit.aspirate,
        container_description=kit.segments,
        common=kit.common,
        no_lld=kit.no_lld,
        mix=kit.mix,
        adc=kit.adc,
        tadm=kit.tadm,
      )
    else:
      return PrepCmd.AspirateParametersNoLldAndMonitoring2(
        default_values=False,
        channel=kit.channel,
        aspirate=kit.aspirate,
        container_description=kit.segments,
        common=kit.common,
        no_lld=kit.no_lld,
        mix=kit.mix,
        adc=kit.adc,
        aspirate_monitoring=kit.monitoring,
      )

  def _assemble_aspirate_v1(
    self, kit: _AspirateChannelKit, effective_lld: bool, is_tadm: bool
  ) -> Union[
    PrepCmd.AspirateParametersLldAndTadm,
    PrepCmd.AspirateParametersLldAndMonitoring,
    PrepCmd.AspirateParametersNoLldAndTadm,
    PrepCmd.AspirateParametersNoLldAndMonitoring,
  ]:
    """Assemble a v1 aspirate parameter struct (cone-patched, no segments)."""
    patched = self._patch_common_with_cone(kit.common, kit.segments)
    if effective_lld and is_tadm:
      return PrepCmd.AspirateParametersLldAndTadm(
        default_values=False,
        channel=kit.channel,
        aspirate=kit.aspirate,
        common=patched,
        lld=kit.lld,
        p_lld=kit.p_lld,
        c_lld=kit.c_lld,
        mix=kit.mix,
        tadm=kit.tadm,
        adc=kit.adc,
      )
    elif effective_lld:
      return PrepCmd.AspirateParametersLldAndMonitoring(
        default_values=False,
        channel=kit.channel,
        aspirate=kit.aspirate,
        common=patched,
        lld=kit.lld,
        p_lld=kit.p_lld,
        c_lld=kit.c_lld,
        mix=kit.mix,
        aspirate_monitoring=kit.monitoring,
        adc=kit.adc,
      )
    elif is_tadm:
      return PrepCmd.AspirateParametersNoLldAndTadm(
        default_values=False,
        channel=kit.channel,
        aspirate=kit.aspirate,
        common=patched,
        no_lld=kit.no_lld,
        mix=kit.mix,
        adc=kit.adc,
        tadm=kit.tadm,
      )
    else:
      return PrepCmd.AspirateParametersNoLldAndMonitoring(
        default_values=False,
        channel=kit.channel,
        aspirate=kit.aspirate,
        common=patched,
        no_lld=kit.no_lld,
        mix=kit.mix,
        adc=kit.adc,
        aspirate_monitoring=kit.monitoring,
      )

  # Command dispatch tables: (effective_lld, is_tadm, use_v2) → command class
  _ASPIRATE_CMD = {
    (True, True, True): PrepCmd.PrepAspirateWithLldTadmV2,
    (True, True, False): PrepCmd.PrepAspirateWithLldTadm,
    (True, False, True): PrepCmd.PrepAspirateWithLldV2,
    (True, False, False): PrepCmd.PrepAspirateWithLld,
    (False, True, True): PrepCmd.PrepAspirateTadmV2,
    (False, True, False): PrepCmd.PrepAspirateTadm,
    (False, False, True): PrepCmd.PrepAspirateNoLldMonitoringV2,
    (False, False, False): PrepCmd.PrepAspirateNoLldMonitoring,
  }

  async def _send_aspirate(
    self,
    kits: list[_AspirateChannelKit],
    effective_lld: bool,
    is_tadm: bool,
    use_v2: bool,
    read_timeout: Optional[float] = None,
  ) -> None:
    """Assemble the correct param types and send the aspirate command."""
    cmd_cls = self._ASPIRATE_CMD[(effective_lld, is_tadm, use_v2)]
    assembler = self._assemble_aspirate_v2 if use_v2 else self._assemble_aspirate_v1
    params = [assembler(k, effective_lld, is_tadm) for k in kits]
    await self._client.execute(
      cmd_cls(aspirate_parameters=params),  # type: ignore[arg-type]
      read_timeout=read_timeout if effective_lld else None,
    )

  # -- dispense: resolve, assemble, send -----------------------------------------------------------

  def _resolve_dispense_channels(
    self,
    ops: List[_PipetteTransfer],
    use_channels: List[int],
    effective_lld: bool,
    *,
    z_final: Optional[List[float]] = None,
    z_fluid: Optional[List[float]] = None,
    z_air: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    z_liquid_exit_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    cutoff_speed: Optional[List[float]] = None,
    z_minimum: Optional[List[float]] = None,
    z_bottom_search_offset: Optional[List[float]] = None,
    lld: Optional[PrepCmd.LldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    container_segments: Optional[List[List[PrepCmd.SegmentDescriptor]]] = None,
    auto_container_geometry: bool = False,
    hamilton_liquid_classes: Optional[List[HamiltonLiquidClass]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
  ) -> list[_DispenseChannelKit]:
    """Resolve all per-channel values for dispense (pure computation, no I/O)."""
    ctx = self._resolve_channel_context(
      ops,
      use_channels,
      z_final=z_final,
      z_fluid=z_fluid,
      z_air=z_air,
      z_minimum=z_minimum,
      z_bottom_search_offset=z_bottom_search_offset,
      container_segments=container_segments,
      auto_container_geometry=auto_container_geometry,
      hamilton_liquid_classes=hamilton_liquid_classes,
      disable_volume_correction=disable_volume_correction,
    )

    # Dispense-specific HLC defaults
    hlcs = ctx.hlcs
    settling_time = fill_in_defaults(
      settling_time, [hlc.dispense_settling_time if hlc is not None else 0.0 for hlc in hlcs]
    )
    transport_air_volume = fill_in_defaults(
      transport_air_volume,
      [hlc.dispense_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs],
    )
    z_liquid_exit_speed = fill_in_defaults(
      z_liquid_exit_speed, [hlc.dispense_swap_speed if hlc is not None else 10.0 for hlc in hlcs]
    )
    stop_back_volume = fill_in_defaults(
      stop_back_volume, [hlc.dispense_stop_back_volume if hlc is not None else 0.0 for hlc in hlcs]
    )
    cutoff_speed = fill_in_defaults(
      cutoff_speed, [hlc.dispense_stop_flow_rate if hlc is not None else 100.0 for hlc in hlcs]
    )
    flow_rates = [
      op.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 100.0)
      for op, hlc in zip(ops, hlcs)
    ]

    lld_defaults = self._default_lld_params(effective_lld, c_lld=c_lld)

    kits: list[_DispenseChannelKit] = []
    for ch in range(self.num_channels):
      if ch not in ctx.indexed_ops:
        continue
      idx = ctx.ch_to_idx[ch]
      op = ctx.indexed_ops[ch]
      loc = op.resource.get_location_wrt(self._require_deck(), "c", "c", "cavity_bottom")
      radius = _effective_radius(op.resource)

      kits.append(
        _DispenseChannelKit(
          channel=_CHANNEL_INDEX[ch],
          dispense=PrepCmd.DispenseParameters.for_op(
            loc, stop_back_volume=stop_back_volume[idx], cutoff_speed=cutoff_speed[idx]
          ),
          common=PrepCmd.CommonParameters.for_op(
            ctx.volumes[idx],
            radius,
            flow_rate=flow_rates[idx],
            z_minimum=ctx.z_minimum[idx],
            z_final=ctx.z_final[idx],
            z_liquid_exit_speed=z_liquid_exit_speed[idx],
            transport_air_volume=transport_air_volume[idx],
            settling_time=settling_time[idx],
          ),
          segments=ctx.ch_segments[ch],
          no_lld=PrepCmd.NoLldParameters.for_fixed_z(
            ctx.z_fluid[idx], ctx.z_air[idx], z_bottom_search_offset=ctx.z_bottom_search_offset[idx]
          ),
          lld=self._lld_for_well(effective_lld, lld, ctx.well_geometry[idx].top_of_well),
          c_lld=lld_defaults.c_lld,
          tadm=PrepCmd.TadmParameters.default(),
          mix=PrepCmd.MixParameters.default(),
          adc=PrepCmd.AdcParameters.default(),
        )
      )
    return kits

  @staticmethod
  def _assemble_dispense_v2(
    kit: _DispenseChannelKit, effective_lld: bool
  ) -> Union[PrepCmd.DispenseParametersLld2, PrepCmd.DispenseParametersNoLld2]:
    """Assemble a v2 dispense parameter struct from pre-resolved kit values."""
    if effective_lld:
      return PrepCmd.DispenseParametersLld2(
        default_values=False,
        channel=kit.channel,
        dispense=kit.dispense,
        container_description=kit.segments,
        common=kit.common,
        lld=kit.lld,
        c_lld=kit.c_lld,
        mix=kit.mix,
        adc=kit.adc,
        tadm=kit.tadm,
      )
    else:
      return PrepCmd.DispenseParametersNoLld2(
        default_values=False,
        channel=kit.channel,
        dispense=kit.dispense,
        container_description=kit.segments,
        common=kit.common,
        no_lld=kit.no_lld,
        mix=kit.mix,
        adc=kit.adc,
        tadm=kit.tadm,
      )

  def _assemble_dispense_v1(
    self, kit: _DispenseChannelKit, effective_lld: bool
  ) -> Union[PrepCmd.DispenseParametersLld, PrepCmd.DispenseParametersNoLld]:
    """Assemble a v1 dispense parameter struct (cone-patched, no segments)."""
    patched = self._patch_common_with_cone(kit.common, kit.segments)
    if effective_lld:
      return PrepCmd.DispenseParametersLld(
        default_values=False,
        channel=kit.channel,
        dispense=kit.dispense,
        common=patched,
        lld=kit.lld,
        c_lld=kit.c_lld,
        mix=kit.mix,
        adc=kit.adc,
        tadm=kit.tadm,
      )
    else:
      return PrepCmd.DispenseParametersNoLld(
        default_values=False,
        channel=kit.channel,
        dispense=kit.dispense,
        common=patched,
        no_lld=kit.no_lld,
        mix=kit.mix,
        adc=kit.adc,
        tadm=kit.tadm,
      )

  # Command dispatch table: (effective_lld, use_v2) → command class
  _DISPENSE_CMD = {
    (True, True): PrepCmd.PrepDispenseWithLldV2,
    (True, False): PrepCmd.PrepDispenseWithLld,
    (False, True): PrepCmd.PrepDispenseNoLldV2,
    (False, False): PrepCmd.PrepDispenseNoLld,
  }

  async def _send_dispense(
    self,
    kits: list[_DispenseChannelKit],
    effective_lld: bool,
    use_v2: bool,
    read_timeout: Optional[float] = None,
  ) -> None:
    """Assemble the correct param types and send the dispense command."""
    cmd_cls = self._DISPENSE_CMD[(effective_lld, use_v2)]
    assembler = self._assemble_dispense_v2 if use_v2 else self._assemble_dispense_v1
    params = [assembler(k, effective_lld) for k in kits]
    await self._client.execute(
      cmd_cls(dispense_parameters=params),  # type: ignore[arg-type]
      read_timeout=read_timeout if effective_lld else None,
    )

  # -- aspirate / dispense orchestrators -----------------------------------------------------------

  def _build_transfers(
    self,
    resources: Sequence[Container],
    vols: Sequence[float],
    use_channels: List[int],
    *,
    offsets: Optional[Sequence[Coordinate]] = None,
    liquid_height: Optional[Sequence[Optional[float]]] = None,
    flow_rates: Optional[Sequence[Optional[float]]] = None,
    blow_out_air_volume: Optional[Sequence[Optional[float]]] = None,
  ) -> List[_PipetteTransfer]:
    resources = list(resources)
    vols = [float(v) for v in vols]
    if len(resources) != len(use_channels) or len(vols) != len(use_channels):
      raise ValueError("resources, vols, and use_channels must have the same length")
    tips = self._require_mounted_tips(use_channels)
    n = len(use_channels)
    offs = list(offsets) if offsets is not None else [Coordinate.zero()] * n
    lhs = list(liquid_height) if liquid_height is not None else [None] * n
    frs = list(flow_rates) if flow_rates is not None else [None] * n
    bavs = list(blow_out_air_volume) if blow_out_air_volume is not None else [None] * n
    for name, seq in (
      ("offsets", offs),
      ("liquid_height", lhs),
      ("flow_rates", frs),
      ("blow_out_air_volume", bavs),
    ):
      if len(seq) != n:
        raise ValueError(f"{name} length must match use_channels ({n})")
    return [
      _PipetteTransfer(
        resource=r,
        tip=t,
        volume=v,
        offset=o,
        liquid_height=lh,
        flow_rate=fr,
        blow_out_air_volume=bav,
      )
      for r, t, v, o, lh, fr, bav in zip(resources, tips, vols, offs, lhs, frs, bavs)
    ]

  async def aspirate(
    self,
    resources: Sequence[Container],
    vols: Sequence[float],
    use_channels: Optional[List[int]] = None,
    *,
    flow_rates: Optional[List[Optional[float]]] = None,
    offsets: Optional[List[Coordinate]] = None,
    liquid_height: Optional[List[Optional[float]]] = None,
    blow_out_air_volume: Optional[List[Optional[float]]] = None,
    z_final: Optional[List[float]] = None,
    z_fluid: Optional[List[float]] = None,
    z_air: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    z_liquid_exit_speed: Optional[List[float]] = None,
    prewet_volume: Optional[List[float]] = None,
    z_minimum: Optional[List[float]] = None,
    z_bottom_search_offset: Optional[List[float]] = None,
    lld_mode: Optional[List[Any]] = None,
    lld: Optional[PrepCmd.LldParameters] = None,
    p_lld: Optional[PrepCmd.PLldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    tadm: Optional[PrepCmd.TadmParameters] = None,
    container_segments: Optional[List[List[PrepCmd.SegmentDescriptor]]] = None,
    auto_container_geometry: bool = False,
    hamilton_liquid_classes: Optional[List[HamiltonLiquidClass]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
    read_timeout: Optional[float] = None,
    command_version: Optional[Literal["v1", "v2"]] = None,
  ):
    """Aspirate from containers using mounted tips.

    Explicit kwargs override Hamilton liquid-class defaults; HLC supplies
    unspecified fields and the volume correction curve unless disabled.
    """
    resources = list(resources)
    use_channels = use_channels if use_channels is not None else list(range(len(resources)))
    ops = self._build_transfers(
      resources,
      vols,
      use_channels,
      offsets=offsets,
      liquid_height=liquid_height,
      flow_rates=flow_rates,
      blow_out_air_volume=blow_out_air_volume,
    )
    effective_lld = self._resolve_effective_lld(lld_mode, lld, len(ops))
    is_tadm = tadm is not None
    use_v2 = self._resolve_command_version(command_version)

    kits = self._resolve_aspirate_channels(
      ops,
      use_channels,
      effective_lld,
      z_final=z_final,
      z_fluid=z_fluid,
      z_air=z_air,
      settling_time=settling_time,
      transport_air_volume=transport_air_volume,
      z_liquid_exit_speed=z_liquid_exit_speed,
      prewet_volume=prewet_volume,
      z_minimum=z_minimum,
      z_bottom_search_offset=z_bottom_search_offset,
      lld=lld,
      p_lld=p_lld,
      c_lld=c_lld,
      tadm=tadm,
      container_segments=container_segments,
      auto_container_geometry=auto_container_geometry,
      hamilton_liquid_classes=hamilton_liquid_classes,
      disable_volume_correction=disable_volume_correction,
    )

    lld_read_timeout = read_timeout
    if lld_read_timeout is None and effective_lld and kits:
      min_z_min = min(k.common.z_minimum for k in kits)
      lld_read_timeout = lld_seek_timeout(kits[0].lld, min_z_min)

    volume_intents = [
      VolumeTransferIntent(
        channel=ch,
        container=op.resource,
        tip=op.tip,
        volume_ul=next(k.common.liquid_volume for k in kits if k.channel == _CHANNEL_INDEX[ch]),
        direction="aspirate",
      )
      for ch, op in zip(use_channels, ops)
    ]
    queue_volume_transfers(volume_intents)

    async def _send() -> None:
      await self._send_aspirate(kits, effective_lld, is_tadm, use_v2, lld_read_timeout)

    await self._finalize_channel_command(use_channels, volume_intents=volume_intents, send=_send)

  async def dispense(
    self,
    resources: Sequence[Container],
    vols: Sequence[float],
    use_channels: Optional[List[int]] = None,
    *,
    flow_rates: Optional[List[Optional[float]]] = None,
    offsets: Optional[List[Coordinate]] = None,
    liquid_height: Optional[List[Optional[float]]] = None,
    blow_out_air_volume: Optional[List[Optional[float]]] = None,
    z_final: Optional[List[float]] = None,
    z_fluid: Optional[List[float]] = None,
    z_air: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    z_liquid_exit_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    cutoff_speed: Optional[List[float]] = None,
    z_minimum: Optional[List[float]] = None,
    z_bottom_search_offset: Optional[List[float]] = None,
    lld_mode: Optional[List[Any]] = None,
    lld: Optional[PrepCmd.LldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    container_segments: Optional[List[List[PrepCmd.SegmentDescriptor]]] = None,
    auto_container_geometry: bool = False,
    hamilton_liquid_classes: Optional[List[HamiltonLiquidClass]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
    read_timeout: Optional[float] = None,
    command_version: Optional[Literal["v1", "v2"]] = None,
  ):
    """Dispense to containers using mounted tips.

    Explicit kwargs override Hamilton liquid-class defaults; HLC supplies
    unspecified fields and the volume correction curve unless disabled.
    """
    resources = list(resources)
    use_channels = use_channels if use_channels is not None else list(range(len(resources)))
    ops = self._build_transfers(
      resources,
      vols,
      use_channels,
      offsets=offsets,
      liquid_height=liquid_height,
      flow_rates=flow_rates,
      blow_out_air_volume=blow_out_air_volume,
    )
    _DISPENSE_ALLOWED_LLD = frozenset({Pipettes.LLDMode.CAPACITIVE})
    effective_lld = self._resolve_effective_lld(
      lld_mode, lld, len(ops), allowed_modes=_DISPENSE_ALLOWED_LLD
    )
    use_v2 = self._resolve_command_version(command_version)

    kits = self._resolve_dispense_channels(
      ops,
      use_channels,
      effective_lld,
      z_final=z_final,
      z_fluid=z_fluid,
      z_air=z_air,
      settling_time=settling_time,
      transport_air_volume=transport_air_volume,
      z_liquid_exit_speed=z_liquid_exit_speed,
      stop_back_volume=stop_back_volume,
      cutoff_speed=cutoff_speed,
      z_minimum=z_minimum,
      z_bottom_search_offset=z_bottom_search_offset,
      lld=lld,
      c_lld=c_lld,
      container_segments=container_segments,
      auto_container_geometry=auto_container_geometry,
      hamilton_liquid_classes=hamilton_liquid_classes,
      disable_volume_correction=disable_volume_correction,
    )

    lld_read_timeout = read_timeout
    if lld_read_timeout is None and effective_lld and kits:
      min_z_min = min(k.common.z_minimum for k in kits)
      lld_read_timeout = lld_seek_timeout(kits[0].lld, min_z_min)

    volume_intents = [
      VolumeTransferIntent(
        channel=ch,
        container=op.resource,
        tip=op.tip,
        volume_ul=next(k.common.liquid_volume for k in kits if k.channel == _CHANNEL_INDEX[ch]),
        direction="dispense",
      )
      for ch, op in zip(use_channels, ops)
    ]
    queue_volume_transfers(volume_intents)

    async def _send() -> None:
      await self._send_dispense(kits, effective_lld, use_v2, lld_read_timeout)

    await self._finalize_channel_command(use_channels, volume_intents=volume_intents, send=_send)
