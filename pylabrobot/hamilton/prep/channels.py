"""PrepChannels: dual-channel pipettor ops plus per-channel discovery.

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
from dataclasses import dataclass
from typing import (
  TYPE_CHECKING,
  Any,
  Awaitable,
  Callable,
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
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonCoreGrippers
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

from . import prep_commands as PrepCmd
from .client import PIPETTOR_OBJECT_PATH

if TYPE_CHECKING:
  from pylabrobot.resources.deck import Deck

  from .client import PrepClient
  from .info import PrepInstrumentInfo

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
# Shared pure helpers (also imported by PrepHead8)
# =============================================================================


def fill_in_defaults(val: Optional[List[_T]], default: List[_T]) -> List[_T]:
  """Convert optional per-channel overrides into a full list matching ``default`` length."""
  if val is None:
    return default
  if len(val) != len(default):
    raise ValueError(f"Value length must equal num operations ({len(default)}), but is {len(val)}")
  return [v if v is not None else d for v, d in zip(val, default)]


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


def default_lld_params(
  effective_lld: bool,
  p_lld: Optional[PrepCmd.PLldParameters] = None,
  c_lld: Optional[PrepCmd.CLldParameters] = None,
) -> _LldDefaults:
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
  return _LldDefaults(p_lld=resolved_p, c_lld=resolved_c)


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
  liquid_height: Optional[float] = None,
  offset_z: float = 0.0,
  z_air_margin_mm: float = 2.0,
) -> _WellGeometry:
  """Compute absolute Z values from well/container geometry for aspirate/dispense.

  Args:
    resource: Well or Container with get_size_z().
    liquid_height: Distance from well bottom to liquid surface (mm). None = 0.
    offset_z: Additional Z applied to the bottom position (e.g. from op.offset.z).
    z_air_margin_mm: Clearance above well opening for z_air (approach/exit height).

  Returns:
    _WellGeometry with well_bottom, liquid_surface, top_of_well, z_air.
  """
  if not hasattr(resource, "get_size_z"):
    raise ValueError(
      "Resource must have get_size_z() to derive absolute Z (e.g. a Well or Container). "
      "Pass z_minimum, z_fluid, z_air explicitly for this operation."
    )
  loc = resource.get_absolute_location("c", "c", "cavity_bottom")
  well_bottom_z = loc.z + offset_z
  liquid_surface_z = well_bottom_z + (liquid_height or 0.0)
  top_of_well_z = loc.z + resource.get_size_z()
  z_air_z = top_of_well_z + z_air_margin_mm
  return _WellGeometry(well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z)


_CHANNEL_INDEX = {
  0: PrepCmd.ChannelIndex.RearChannel,
  1: PrepCmd.ChannelIndex.FrontChannel,
}


@dataclass(frozen=True)
class ChannelDriveMap:
  """Cached channel-drive topology discovered from the firmware tree.

  One entry per discovered channel for the sleeve sensor (``Squeeze.SDrive``),
  the Z drive (``ZAxis.ZDrive``), and the per-node ``NodeInformation`` object
  (used for firmware-string queries). Lists are parallel and sorted by tree
  traversal order (same order the firmware returns Channel Root instances).
  """

  sleeve_sensor_addrs: List[Address]
  zdrive_addrs: List[Address]
  node_info_addrs: List[Address]

  @property
  def num_channels_discovered(self) -> int:
    return len(self.sleeve_sensor_addrs)

  def to_dict(self) -> dict:
    """Serialize for logs / notebooks that prefer plain dicts."""
    return {
      "num_channels_discovered": self.num_channels_discovered,
      "sleeve_sensor_addrs": list(self.sleeve_sensor_addrs),
      "zdrive_addrs": list(self.zdrive_addrs),
      "node_info_addrs": list(self.node_info_addrs),
    }


# ---------------------------------------------------------------------------
# Firmware-tree discovery — module-level so it can be called independently of
# any PrepChannels instance (used when building channels in Prep.setup, plus by
# diagnostic notebooks that hold only a client).
# ---------------------------------------------------------------------------


async def _find_children_by_name(
  intro,
  parent_addr: Address,
  *names: str,
) -> dict:
  """Enumerate ``parent_addr``'s subobjects; return ``{name: Address}`` for matches.

  Bounded by ``subobject_count`` on the parent. Returns early once every
  requested name has been found. Children that raise on ``get_object`` (e.g.
  unknown firmware types) are skipped with a debug log.
  """
  parent = await intro.get_object(parent_addr)
  wanted = set(names)
  found: dict = {}
  for i in range(parent.subobject_count):
    try:
      sub_addr = await intro.get_subobject_address(parent_addr, i)
      sub = await intro.get_object(sub_addr)
    except Exception as e:
      logger.debug("subobject[%d] of %s failed: %s", i, parent_addr, e)
      continue
    if sub.name in wanted:
      found[sub.name] = sub_addr
      if len(found) == len(wanted):
        break
  return found


async def discover_channel_drives(
  client: "PrepClient",
  *,
  root_name: str = "Channel Root",
) -> ChannelDriveMap:
  """Discover per-channel drive addresses via bounded subobject enumeration.

  MLPrepRoot exposes one ``<root_name>`` child per physical channel (siblings
  with identical names, distinguished by the ``node`` component of their
  :class:`Address`). For each one we walk:

  - ``<root>.Channel.Squeeze.SDrive``     → sleeve sensor
  - ``<root>.Channel.ZAxis.ZDrive``       → Z drive
  - ``<root>.NodeInformation``            → per-channel firmware strings

  Uses ``get_subobject_address`` / ``get_object`` along the known path shape —
  no full-tree traversal. Pass ``root_name="MPH Channel Root"`` for the 8MPH
  head. For a full firmware-tree dump use
  :meth:`PrepInstrumentInfo.get_firmware_tree`.
  """
  intro = client.introspection
  try:
    mlprep_root = await client.resolve_path("MLPrepRoot")
    root_info = await intro.get_object(mlprep_root)
  except (KeyError, RuntimeError) as e:
    logger.debug("MLPrepRoot unavailable (%s); skipping channel discovery", e)
    return ChannelDriveMap(sleeve_sensor_addrs=[], zdrive_addrs=[], node_info_addrs=[])

  channel_root_addrs: List[Address] = []
  for i in range(root_info.subobject_count):
    try:
      sub_addr = await intro.get_subobject_address(mlprep_root, i)
      sub = await intro.get_object(sub_addr)
    except Exception as e:
      logger.debug("MLPrepRoot subobject[%d] failed: %s", i, e)
      continue
    if sub.name == root_name:
      channel_root_addrs.append(sub_addr)

  sleeve: List[Address] = []
  zdrive: List[Address] = []
  node_info: List[Address] = []

  for ch_root in channel_root_addrs:
    top = await _find_children_by_name(intro, ch_root, "Channel", "NodeInformation")
    if "NodeInformation" in top:
      node_info.append(top["NodeInformation"])

    channel_addr = top.get("Channel")
    if channel_addr is None:
      logger.warning("%s @ %s has no 'Channel' child", root_name, ch_root)
      continue

    axes = await _find_children_by_name(intro, channel_addr, "Squeeze", "ZAxis")
    if (sq_parent := axes.get("Squeeze")) is not None:
      sq = await _find_children_by_name(intro, sq_parent, "SDrive")
      if "SDrive" in sq:
        sleeve.append(sq["SDrive"])
    if (zx_parent := axes.get("ZAxis")) is not None:
      zx = await _find_children_by_name(intro, zx_parent, "ZDrive")
      if "ZDrive" in zx:
        zdrive.append(zx["ZDrive"])

  logger.info("Discovered %d %s channel drive pair(s)", len(channel_root_addrs), root_name)
  return ChannelDriveMap(
    sleeve_sensor_addrs=sleeve,
    zdrive_addrs=zdrive,
    node_info_addrs=node_info,
  )


# ---------------------------------------------------------------------------
# Per-channel movement bounds — parses PipettorService.GetChannelBounds.
# ---------------------------------------------------------------------------


class PrepChannelBounds(TypedDict):
  """Firmware-reported movement limits for one pipettor channel (mm)."""

  x_min: float
  x_max: float
  y_min: float
  y_max: float
  z_min: float
  z_max: float


async def request_channel_bounds(client: "PrepClient") -> List[PrepChannelBounds]:
  """Request per-channel movement bounds from the firmware (cmd=10).

  Returns one dict per channel (keys ``x_min``, ``x_max``, ``y_min``, ``y_max``,
  ``z_min``, ``z_max`` in mm), ordered by channel index. Returns ``[]`` when
  the service cannot be resolved or the response is empty.

  These are the firmware-enforced limits — positions outside these ranges will
  be rejected with 0x0F04 (X), 0x0F05 (Y), or 0x0F06 (Z). Z bounds are for
  empty channels; with a tip attached the effective Z minimum is higher.
  """
  try:
    raw = await client.send_query(PrepCmd.PrepGetChannelBounds())
  except RuntimeError:
    return []
  if raw is None:
    return []

  # Parse per-channel bounds from raw response.
  # Each channel block: channel_enum (u32 at 0x20), then 6× f32 (at 0x28):
  # x_min, x_max, y_min, y_max, z_min, z_max
  data = raw[0]
  _CHANNEL_ENUM_TO_IDX = {v: k for k, v in _CHANNEL_INDEX.items()}
  indexed: list[tuple[int, PrepChannelBounds]] = []

  i = 0
  while i < len(data) - 20:
    if data[i] == 0x20 and data[i + 1] == 0x00 and data[i + 2] == 0x04:
      ch_val = _struct.unpack_from("<I", data, i + 4)[0]
      ch_idx = _CHANNEL_ENUM_TO_IDX.get(ch_val)

      j = i + 8
      floats: List[float] = []
      while len(floats) < 6 and j < len(data) - 7:
        if data[j] == 0x28 and data[j + 1] == 0x00:
          floats.append(_struct.unpack_from("<f", data, j + 4)[0])
          j += 8
        else:
          j += 1

      if ch_idx is not None and len(floats) == 6:
        bounds: PrepChannelBounds = {
          "x_min": floats[0],
          "x_max": floats[1],
          "y_min": floats[2],
          "y_max": floats[3],
          "z_min": floats[4],
          "z_max": floats[5],
        }
        indexed.append((ch_idx, bounds))
      i = j
    else:
      i += 1

  indexed.sort(key=lambda pair: pair[0])
  return [bounds for _, bounds in indexed]


# ---------------------------------------------------------------------------
# PrepPIPChannel — thin per-channel facade owned by PrepChannels.channels
# ---------------------------------------------------------------------------


class PrepPIPChannel:
  """Per-channel facade: drive addresses, movement bounds, firmware-version queries.

  Instances are constructed by :func:`build_prep_channels` from :meth:`Prep.setup`
  and exposed as ``prep.channels.channels[i]`` (or the dual-channel peer).
  """

  def __init__(
    self,
    *,
    index: int,
    client: "PrepClient",
    sleeve_sensor: Optional[Address] = None,
    zdrive: Optional[Address] = None,
    node_info: Optional[Address] = None,
    bounds: Optional[PrepChannelBounds] = None,
  ) -> None:
    self.index = index
    self._client = client
    self.sleeve_sensor = sleeve_sensor
    self.zdrive = zdrive
    self.node_info = node_info
    self.bounds = bounds  # x_min..z_max from firmware, or None if unavailable

  def __repr__(self) -> str:
    return (
      f"PrepPIPChannel(index={self.index}, node_info={self.node_info!r}, "
      f"bounds={'set' if self.bounds else 'unset'})"
    )

  async def request_firmware_version(self) -> Optional[str]:
    """Per-channel firmware version string (NodeInformation cmd=8).

    Serial number is intentionally not exposed here — NodeInformation's
    GetSerialNumber endpoint is unpopulated on shipped instruments, and the
    canonical instrument serial (pipettor module) is already surfaced via
    :meth:`PrepInstrumentInfo.get_device_serial_number`.
    """
    if self.node_info is None:
      return None
    return await self._client._query_firmware_string(self.node_info, cmd_id=8, iface_id=1)


# ---------------------------------------------------------------------------
# Builder called from Prep.setup.
# ---------------------------------------------------------------------------


async def build_prep_channels(
  client: "PrepClient",
  info: "PrepInstrumentInfo",
  *,
  root_name: str = "Channel Root",
  num_channels: Optional[int] = None,
) -> List[PrepPIPChannel]:
  """Build per-channel facades, resolve drive addresses, fetch bounds.

  If ``num_channels`` is omitted, uses ``info.config.num_channels``.
  """
  drive_map = await discover_channel_drives(client, root_name=root_name)

  if num_channels is None:
    try:
      num_channels = info.config.num_channels
    except RuntimeError:
      num_channels = None
  if num_channels is None:
    num_channels = drive_map.num_channels_discovered

  try:
    bounds_list = await request_channel_bounds(client)
  except Exception as e:
    logger.warning("Failed to query channel bounds: %s", e)
    bounds_list = []

  def _drive_addr(attr: str, i: int) -> Optional[Address]:
    if drive_map is None:
      return None
    seq = getattr(drive_map, attr)
    return seq[i] if i < len(seq) else None

  channels: List[PrepPIPChannel] = []
  for i in range(num_channels):
    channels.append(
      PrepPIPChannel(
        index=i,
        client=client,
        sleeve_sensor=_drive_addr("sleeve_sensor_addrs", i),
        zdrive=_drive_addr("zdrive_addrs", i),
        node_info=_drive_addr("node_info_addrs", i),
        bounds=bounds_list[i] if i < len(bounds_list) else None,
      )
    )
  return channels


# =============================================================================
# PrepChannels — channel indices and deck routing
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
        "MPH motion uses PrepHead8 / MphMoveToPosition on MLPrepRoot.MphRoot.MPH."
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


class PrepChannels:
  """Dual-channel pipettor for Hamilton Prep.

  Narrow constructor: ``client`` (transport + JIT firmware-path resolve) and
  ``info`` (instrument-wide metadata). ``self.channels`` is attached by
  :meth:`Prep.setup` before :meth:`_on_setup`.
  """

  # V2 aspirate/dispense command IDs (interface 1 on Pipettor).
  _V2_PIPETTING_CMD_IDS = {38, 39, 40, 41, 42, 43}

  def __init__(
    self,
    *,
    client: "PrepClient",
    info: "PrepInstrumentInfo",
    deck: Optional["Deck"] = None,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ) -> None:
    self._client = client
    self._info = info
    self.deck = deck
    self._user_traverse_height: Optional[float] = default_traverse_height
    self._channel_bounds: list[PrepChannelBounds] = []
    self._use_v1_aspirate_dispense: bool = use_v1_aspirate_dispense
    self._supports_v2_pipetting: Optional[bool] = None
    self.setup_finished: bool = False
    self.channels: List[PrepPIPChannel] = []
    self.head: dict[int, TipTracker] = {}

  def set_default_traverse_height(self, value: float) -> None:
    """Set the default traverse height (mm) used when final_z is not passed to pick_up_tips/drop_tips.

    Use this when the instrument did not report a traverse height at setup, or to override
    the probed value.
    """
    self._user_traverse_height = value

  async def _probe_v2_support(self) -> bool:
    """Probe the pipettor for v2 aspirate/dispense command support.

    Enumerates interface 1 method IDs on the pipettor object and checks whether
    all v2 command IDs (38-43) are present. Returns False when the firmware only
    exposes v1 commands (1-6).
    """
    dest = await self._client.resolve_path(PIPETTOR_OBJECT_PATH)
    methods = await self._client.introspection.methods_for_interface(dest, interface_id=1)
    iface1_ids = {m.method_id for m in methods}
    return self._V2_PIPETTING_CMD_IDS.issubset(iface1_ids)

  def _resolve_command_version(self, override: Optional[Literal["v1", "v2"]] = None) -> bool:
    return resolve_command_version(
      self._supports_v2_pipetting,
      self._use_v1_aspirate_dispense,
      override,
      v2_error_hint=(
        "v2 aspirate/dispense commands (cmd 38-43) are not supported by this firmware. "
        "Use command_version='v1' or pass use_v1_aspirate_dispense=True to PrepChannels."
      ),
    )

  # ---------------------------------------------------------------------------
  # Setup
  # ---------------------------------------------------------------------------

  async def _on_setup(self):
    """Read config and probe pipettor capabilities.

    Called after ``self.channels`` is populated by :meth:`Prep.setup`. Instrument-
    level initialization (``MLPrep.Initialize``) runs earlier in
    :meth:`Prep.setup` — the pipettor sees an already-initialized instrument.
    """
    cfg = self._info.config
    logger.info(
      "Hardware config: has_enclosure=%s, safe_speeds=%s, traverse_height=%s, "
      "deck_bounds=%s, deck_sites=%d, waste_sites=%d, num_channels=%s, has_mph=%s",
      cfg.has_enclosure,
      cfg.safe_speeds_enabled,
      cfg.default_traverse_height,
      cfg.deck_bounds,
      len(cfg.deck_sites),
      len(cfg.waste_sites),
      cfg.num_channels,
      cfg.has_mph,
    )

    # Per-channel bounds are attached to ``self.channels`` by build_prep_channels.
    # Keep a flat list too for legacy call sites that iterate _channel_bounds.
    self._channel_bounds = [c.bounds for c in self.channels if c.bounds is not None]
    if self._channel_bounds:
      logger.info("Channel bounds: %s", self._channel_bounds)
    else:
      logger.warning("Channel bounds not available — move_to_position will skip validation")

    # Probe pipettor for v2 aspirate/dispense support (cmd 38-43).
    if self._use_v1_aspirate_dispense:
      self._supports_v2_pipetting = False
      logger.info("V2 aspirate/dispense probe skipped (use_v1_aspirate_dispense=True)")
    else:
      try:
        supported = await self._probe_v2_support()
      except Exception as e:
        logger.warning("PIP V2 support probe failed: %s", e)
        supported = False
      if not supported:
        raise RuntimeError(
          "V2 aspirate/dispense commands (cmd 38-43) are not supported by this firmware. "
          "Pass use_v1_aspirate_dispense=True to PrepChannels to use v1 commands (cmd 1-6) instead."
        )
      self._supports_v2_pipetting = True
      logger.info("V2 aspirate/dispense support: True")

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

  def get_mounted_tips(self) -> List[Optional[Tip]]:
    """Tips currently mounted on the dual-channel head (``None`` if empty)."""
    self._ensure_head()
    return [
      self.head[i].get_tip() if self.head[i].has_tip else None for i in range(self.num_channels)
    ]

  async def discover_channel_drives(self) -> ChannelDriveMap:
    """Re-walk the firmware tree and return a fresh :class:`ChannelDriveMap`.

    Diagnostic helper — channel drive addresses for normal operation are already
    cached on each :attr:`channels` entry at build time.
    """
    return await discover_channel_drives(self._client, root_name="Channel Root")

  # ---------------------------------------------------------------------------
  # Properties
  # ---------------------------------------------------------------------------

  @property
  def num_channels(self) -> int:
    """Number of independent dual-channel pipettor channels (1 or 2). Read from info.config."""
    n: Optional[int] = self._info.config.num_channels
    if n is None:
      raise RuntimeError("Instrument config has no num_channels (finish Prep.setup first).")
    return n

  @property
  def has_mph(self) -> bool:
    """True if the 8-channel Multi-Pipetting Head (8MPH) is present. Read from info.config."""
    try:
      return bool(self._info.config.has_mph)
    except RuntimeError:
      return False

  @property
  def num_arms(self) -> int:
    """Number of resource-handling arms. 1 when deck has core_grippers and 2 channels, else 0."""
    if self.deck is None:
      return 0
    try:
      cfg = self._info.config
    except RuntimeError:
      return 0
    if cfg.num_channels != 2:
      return 0
    try:
      mount = self.deck.get_resource("core_grippers")
      return 1 if isinstance(mount, HamiltonCoreGrippers) else 0
    except Exception:
      return 0

  def _resolve_traverse_height(self, final_z: Optional[float] = None) -> float:
    """Resolve final_z: explicit arg > user-set default > probed value. Raises if none available."""
    if final_z is not None:
      return final_z
    if self._user_traverse_height is not None:
      return self._user_traverse_height
    try:
      cfg = self._info.config
    except RuntimeError:
      height: Optional[float] = None
    else:
      height = cfg.default_traverse_height
    if height is not None:
      return height
    raise RuntimeError(
      "Default traverse height is required for this operation but could not be determined. "
      "Either pass final_z explicitly to this call, or set it via "
      "PrepChannels(..., default_traverse_height=<mm>) or set_default_traverse_height(<mm>). "
      "If the instrument supports it, the value is also probed during setup(); ensure setup() completed successfully."
    ) from None

  # ---------------------------------------------------------------------------
  # Tip / aspirate / dispense API
  # ---------------------------------------------------------------------------

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
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )
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
      loc = spot.get_absolute_location("c", "c", "t") + off
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
        indexed[ch][0].get_absolute_location("c", "c", "t") + indexed[ch][2] for ch in use_channels
      ]
      await self.move_to_position(
        x=locs[0].x,
        y=[loc.y for loc in locs],
        z=traverse_h,
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
      await self._client.send_command(
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
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )
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
        loc = self.deck.get_resource(waste_name).get_absolute_location("c", "c", "t")
      else:
        loc = dest.get_absolute_location("c", "c", "t") + off
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
      await self._client.send_command(
        PrepCmd.PrepDropTips(
          tip_positions=tip_positions,
          final_z=resolved_final_z,
          seek_speed=seek_speed,
          tip_roll_off_distance=roll_off,
        )
      )

    await self._finalize_channel_command(use_channels, tip_intents=tip_intents, send=_send)

  # ---------------------------------------------------------------------------
  # V1/V2 aspirate/dispense dispatch helpers
  # ---------------------------------------------------------------------------

  @staticmethod
  def _patch_common_with_cone(
    common: PrepCmd.CommonParameters, segments: list[PrepCmd.SegmentDescriptor]
  ) -> PrepCmd.CommonParameters:
    return patch_common_with_cone(common, segments)

  # ---------------------------------------------------------------------------
  # Shared LLD / TADM resolution helpers
  # ---------------------------------------------------------------------------

  def _resolve_effective_lld(
    self,
    lld_mode: Optional[List[LLDMode]],
    lld: Optional[PrepCmd.LldParameters],
    n: int,
    *,
    allowed_modes: Optional[frozenset[LLDMode]] = None,
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
          if m != LLDMode.OFF and m not in allowed_modes:
            raise ValueError(
              f"Dispense does not support {m.name} LLD — only CAPACITIVE or OFF. "
              "Pressure-based LLD requires aspiration (plunger movement)."
            )
      lld_on = [m != LLDMode.OFF for m in lld_mode]
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
  ) -> _LldDefaults:
    return default_lld_params(effective_lld, p_lld, c_lld)

  @staticmethod
  def _lld_for_well(
    effective_lld: bool, lld: Optional[PrepCmd.LldParameters], top_of_well_z: float
  ) -> PrepCmd.LldParameters:
    return lld_for_well(effective_lld, lld, top_of_well_z)

  # ---------------------------------------------------------------------------
  # Shared channel resolution
  # ---------------------------------------------------------------------------

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
      _absolute_z_from_well(op.resource, op.liquid_height, op.offset.z) for op in ops
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

  # ---------------------------------------------------------------------------
  # Aspirate: resolve, assemble, send
  # ---------------------------------------------------------------------------

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
      loc = asp.resource.get_absolute_location("c", "c", "cavity_bottom")
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
    await self._client.send_command(
      cmd_cls(aspirate_parameters=params),  # type: ignore[arg-type]
      read_timeout=read_timeout if effective_lld else None,
    )

  # ---------------------------------------------------------------------------
  # Dispense: resolve, assemble, send
  # ---------------------------------------------------------------------------

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
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
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
    await self._client.send_command(
      cmd_cls(dispense_parameters=params),  # type: ignore[arg-type]
      read_timeout=read_timeout if effective_lld else None,
    )

  # ---------------------------------------------------------------------------
  # Public aspirate / dispense orchestrators
  # ---------------------------------------------------------------------------

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
    _DISPENSE_ALLOWED_LLD = frozenset({LLDMode.CAPACITIVE})
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

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    """Check if the tip can be picked up by the specified channel.

    Uses the same logic as Nimbus/STAR: only Hamilton tips, no XL tips,
    and channel index must be valid.
    """
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    try:
      n = self._info.config.num_channels
    except RuntimeError:
      n = None
    if n is not None and channel_idx >= n:
      return False
    return True

  # ---------------------------------------------------------------------------
  # Firmware version queries (per-channel; box-level queries live on PrepClient)
  # ---------------------------------------------------------------------------

  async def request_pip_channel_version(self, channel: int) -> Optional[str]:
    """Firmware version string for pipettor channel (0=rearmost)."""
    if channel >= len(self.channels):
      return None
    return await self.channels[channel].request_firmware_version()

  # ---------------------------------------------------------------------------
  # Channel position queries
  # ---------------------------------------------------------------------------

  async def request_channel_bounds(self) -> list[PrepChannelBounds]:
    """Per-channel movement bounds (PipettorService.GetChannelBounds).

    Thin delegation to :func:`request_channel_bounds`.
    Prefer reading cached values via ``self.channels[i].bounds``; use this when a
    fresh re-query is required.
    """
    return await request_channel_bounds(self._client)

  async def request_channel_positions(self) -> list[Coordinate]:
    """Request the current XYZ positions of all pipettor channels.

    Queries Pipettor.GetPositions (cmd=25). Returns one Coordinate per channel,
    ordered by channel index (0=rearmost).

    Uses the typed PrepGetPositions command with ChannelXYZPositionParameters
    response struct for reliable parsing across firmware versions.

    Returns:
      List of Coordinate, one per channel.
    """
    try:
      resp_obj = await self._client.send_command(PrepCmd.PrepGetPositions())
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

  async def request_x_pos_channel_n(self, channel_idx: int = 0) -> float:
    """Request X position of pipettor channel n (in mm).

    Analogous to STARBackend.request_x_pos_channel_n().

    Args:
      channel_idx: Channel index (0=rearmost).

    Returns:
      X position in mm.
    """
    positions = await self.request_channel_positions()
    if channel_idx >= len(positions):
      raise ValueError(f"Channel {channel_idx} out of range ({len(positions)} channels).")
    return float(positions[channel_idx].x)

  async def request_y_pos_channel_n(self, channel_idx: int) -> float:
    """Request Y position of pipettor channel n (in mm).

    Analogous to STARBackend.request_y_pos_channel_n().

    Args:
      channel_idx: Channel index (0=rearmost).

    Returns:
      Y position in mm.
    """
    positions = await self.request_channel_positions()
    if channel_idx >= len(positions):
      raise ValueError(f"Channel {channel_idx} out of range ({len(positions)} channels).")
    return float(positions[channel_idx].y)

  async def request_z_pos_channel_n(self, channel_idx: int) -> float:
    """Request Z position of pipettor channel n (in mm).

    Analogous to STARBackend.request_z_pos_channel_n().

    Args:
      channel_idx: Channel index (0=rearmost).

    Returns:
      Z position in mm.
    """
    positions = await self.request_channel_positions()
    if channel_idx >= len(positions):
      raise ValueError(f"Channel {channel_idx} out of range ({len(positions)} channels).")
    return float(positions[channel_idx].z)

  async def get_channels_y_positions(self) -> dict[int, float]:
    """Request Y positions of all channels.

    Analogous to STARBackend.get_channels_y_positions().

    Returns:
      Dict mapping channel index (0=rearmost) to Y position in mm.
    """
    positions = await self.request_channel_positions()
    return {i: coord.y for i, coord in enumerate(positions)}

  async def get_channels_z_positions(self) -> dict[int, float]:
    """Request Z positions of all channels.

    Analogous to STARBackend.get_channels_z_positions().

    Returns:
      Dict mapping channel index (0=rearmost) to Z position in mm.
    """
    positions = await self.request_channel_positions()
    return {i: coord.z for i, coord in enumerate(positions)}

  async def request_tip_bottom_z_position(self, channel_idx: int) -> float:
    """Request the Z position of the tip bottom on the specified channel.

    GetPositions returns tip-adjusted Z when a tip is mounted — the reported Z
    is the tip bottom position, not the channel head. Verified empirically:
    channel at traverse (167.5mm) with 50uL NTR tip (extension 42.4mm) reports
    Z=125.1mm = 167.5 - 42.4.

    Requires a tip to be mounted (verified via sleeve sensor).

    Analogous to STARBackend.request_tip_bottom_z_position().

    Args:
      channel_idx: Channel index (0=rearmost).

    Returns:
      Tip bottom Z position in mm.

    Raises:
      RuntimeError: If no tip is present on the channel.
    """
    tip_presence = await self.sense_tip_presence()
    if channel_idx >= len(tip_presence) or not tip_presence[channel_idx]:
      raise RuntimeError(f"No tip mounted on channel {channel_idx}")

    return await self.request_z_pos_channel_n(channel_idx)

  async def request_probe_z_position(self, channel_idx: int) -> float:
    """Request the Z position of the channel probe/head (excluding tip).

    Since GetPositions returns tip-adjusted Z when a tip is mounted, this
    method queries the firmware's held tip definition (GetTipDefinitionHeld,
    Pipettor cmd=13) to get the tip length and adds it back.

    When no tip is mounted, returns the same value as request_z_pos_channel_n().

    Analogous to STARBackend.request_probe_z_position().

    Args:
      channel_idx: Channel index (0=rearmost).

    Returns:
      Channel head Z position in mm (excluding tip).
    """
    z = await self.request_z_pos_channel_n(channel_idx)
    tip_presence = await self.sense_tip_presence()
    if channel_idx < len(tip_presence) and tip_presence[channel_idx]:
      # Query firmware for the held tip definition to get tip length
      pipettor_addr = await self._client.resolve_path(PIPETTOR_OBJECT_PATH)
      raw = await self._client.send_query(
        PrepCmd.PrepProbeRequest(dest=pipettor_addr, command_id=13)
      )
      if raw is not None:
        import struct as _struct

        data = raw[0]
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

  # ---------------------------------------------------------------------------
  # Per-axis channel movement
  # ---------------------------------------------------------------------------

  async def move_channel_x(self, channel_idx: int, x: float) -> None:
    """Move the gantry X axis to a position (in mm).

    On the Prep, X is shared across all channels (single gantry). The channel_idx
    parameter is accepted for STAR API compatibility but does not affect which
    channel moves — all channels move together in X.

    Analogous to STARBackend.move_channel_x().

    Args:
      channel_idx: Channel index (0=rearmost). Used to read current Y/Z.
      x: Target X position in mm.
    """
    positions = await self.request_channel_positions()
    if channel_idx >= len(positions):
      raise ValueError(f"Channel {channel_idx} out of range ({len(positions)} channels).")
    await self.move_to_position(
      x, positions[channel_idx].y, positions[channel_idx].z, use_channels=channel_idx
    )

  async def move_channel_y(self, channel_idx: int, y: float) -> None:
    """Move a channel in the Y direction (in mm).

    Analogous to STARBackend.move_channel_y().

    Args:
      channel_idx: Channel index (0=rearmost).
      y: Target Y position in mm.
    """
    positions = await self.request_channel_positions()
    if channel_idx >= len(positions):
      raise ValueError(f"Channel {channel_idx} out of range ({len(positions)} channels).")
    await self.move_to_position(
      positions[channel_idx].x, y, positions[channel_idx].z, use_channels=channel_idx
    )

  async def move_channel_z(self, channel_idx: int, z: float) -> None:
    """Move a channel in the Z direction (in mm).

    Analogous to STARBackend.move_channel_z().

    Args:
      channel_idx: Channel index (0=rearmost).
      z: Target Z position in mm.
    """
    positions = await self.request_channel_positions()
    if channel_idx >= len(positions):
      raise ValueError(f"Channel {channel_idx} out of range ({len(positions)} channels).")
    await self.move_to_position(
      positions[channel_idx].x, positions[channel_idx].y, z, use_channels=channel_idx
    )

  # ---------------------------------------------------------------------------
  # Tip presence sensing
  # ---------------------------------------------------------------------------

  async def sense_tip_presence(self) -> list[bool]:
    """Sense whether a tip is physically present on each pipettor channel via the sleeve sensor.

    Reads the physical sleeve displacement sensor (GetTipPresent, cmd=15) on each
    channel's SDrive sub-object. The sensor responds in real-time to sleeve
    displacement — verified by manual sleeve push tests without any tip pickup.

    Note: the firmware exposes this sensor through the SDrive (squeezer drive) object
    at object_id 514, but it reads the sleeve displacement sensor independently of
    the squeeze motor state.

    Channel addresses are discovered lazily from the object tree and cached in
    ``ChannelDriveMap``, so this works regardless of the node IDs
    assigned by the firmware on a given instrument.

    Returns:
      List of bools, one per channel (index 0=rearmost). True if tip detected.
    """
    import struct as _struct

    drive_map = await self.discover_channel_drives()
    if not drive_map.sleeve_sensor_addrs:
      raise RuntimeError("No channel sleeve sensor addresses discovered.")

    results: list[bool] = []
    for addr in drive_map.sleeve_sensor_addrs:
      raw = await self._client.send_query(PrepCmd.PrepProbeRequest(dest=addr, command_id=15))
      if raw is None or len(raw[0]) < 8:
        results.append(False)
      else:
        val = _struct.unpack_from("<I", raw[0], 4)[0]
        results.append(bool(val))

    return results

  async def request_tip_presence(self) -> List[Optional[bool]]:
    pres = await self.sense_tip_presence()
    return [bool(x) for x in pres]

  # ---------------------------------------------------------------------------
  # Capacitance-based probing (cLLD)
  # ---------------------------------------------------------------------------

  async def clld_probe_x_position_using_channel(self, *args, **kwargs):
    """Probe X position using capacitive LLD. Not yet implemented for the Prep.

    TODO: Investigate ChannelCoordinator [1:17] MoveChannelAxisAbsolute and
    [1:18] MoveChannelAxisRelative for X-axis probing with cLLD feedback.
    The ChannelCoordinator also has [1:19] YSeekLldPosition which may have
    an X equivalent, though none was found in introspection.
    """
    raise NotImplementedError(
      "clld_probe_x_position_using_channel is not yet implemented for PrepChannels."
    )

  async def clld_probe_y_position_using_channel(self, *args, **kwargs):
    """Probe Y position using capacitive LLD. Not yet implemented for the Prep.

    TODO: Investigate ChannelCoordinator [1:19] YSeekLldPosition(seekParameters)
    which takes a YLLDSeekParameters struct and returns SeekResultParameters.
    Also Channel [1:11] LeakCheck has ySeekDistance/yPreloadDistance params
    which suggest Y-axis seeking capability.
    """
    raise NotImplementedError(
      "clld_probe_y_position_using_channel is not yet implemented for PrepChannels."
    )

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
      "clld_probe_z_height_using_channel is not yet implemented for PrepChannels."
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
      "ztouch_probe_z_height_using_channel is not yet implemented for PrepChannels."
    )

  # ---------------------------------------------------------------------------
  # Pipettor convenience methods
  # ---------------------------------------------------------------------------

  async def move_channels_to_safe_z(self, channels: Optional[List[int]] = None) -> None:
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
    assert max(channels) < self.num_channels, (
      f"channel index out of range (valid: 0..{self.num_channels - 1})"
    )
    channel_enums = [_CHANNEL_INDEX[ch] for ch in channels]
    await self._client.send_command(PrepCmd.PrepMoveZUpToSafe(channels=channel_enums))

  async def move_to_position(
    self,
    x: float,
    y: Union[float, List[float]],
    z: Union[float, List[float]],
    use_channels: Optional[Union[int, List[int]]] = 0,
    *,
    via_lane: bool = False,
  ) -> None:
    """Move pipettor to position (cmd=26 or 27). Same (x,y,z) params; via_lane selects cmd 27.

    use_channels defaults to 0 (rear channel). Pass a single channel index (int) or
    a list of indices; for all channels use list(range(self.num_channels)). For a
    single channel, y and z may be scalars instead of lists.
    """
    if use_channels is None:
      channels = [0]
    elif isinstance(use_channels, list):
      channels = list(use_channels)
    else:
      # int or int-like (e.g. numpy.int64); single channel
      channels = [int(use_channels)]
    channels = sorted(channels)
    if channels:
      assert max(channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )
    if isinstance(y, list):
      assert len(y) == len(channels), "len(y) must equal len(use_channels)"
    if isinstance(z, list):
      assert len(z) == len(channels), "len(z) must equal len(use_channels)"

    # Validate against per-channel movement bounds (cached from firmware at setup).
    y_vals = y if isinstance(y, list) else [y] * len(channels)
    z_vals = z if isinstance(z, list) else [z] * len(channels)
    for i, (y_i, z_i) in enumerate(zip(y_vals, z_vals)):
      ch = channels[i]
      if ch < len(self._channel_bounds):
        b = self._channel_bounds[ch]
        if not b["x_min"] <= x <= b["x_max"]:
          raise ValueError(f"x={x} outside channel {ch} range [{b['x_min']:.1f}, {b['x_max']:.1f}]")
        if not b["y_min"] <= y_i <= b["y_max"]:
          raise ValueError(
            f"y={y_i} outside channel {ch} range [{b['y_min']:.1f}, {b['y_max']:.1f}]"
          )
        if z_i > b["z_max"]:
          raise ValueError(f"z={z_i} above channel {ch} maximum {b['z_max']:.1f}")

    move_parameters = _build_pipettor_gantry_move_parameters(x, channels, y, z)

    if via_lane:
      await self._client.send_command(
        PrepCmd.PrepMoveToPositionViaLane(move_parameters=move_parameters)
      )
    else:
      await self._client.send_command(PrepCmd.PrepMoveToPosition(move_parameters=move_parameters))

  async def stop(self) -> None:
    self.setup_finished = False

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "default_traverse_height": self._user_traverse_height,
      "use_v1_aspirate_dispense": self._use_v1_aspirate_dispense,
    }
