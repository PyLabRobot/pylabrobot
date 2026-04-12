"""Hamilton Prep backend implementation.

Three-layer design:

- **HamiltonTCPClient** (``self.client``): Transport and introspection.
  All device communication goes through ``self.client.send_command()``.
  Address resolution: ``self.client.interfaces.<path>.address``.
  The backend composes the client via dependency injection: callers pass host
  (and optionally port) for default TCP settings, or pass a pre-configured
  HamiltonTCPClient for full control.

- **Command dataclasses** (e.g. ``PrepCmd.PrepDropTips``, ``PrepCmd.MphPickupTips``): Pure wire shapes.
  Defined in ``prep_commands.py``; ``@dataclass`` with ``dest: Address`` +
  ``Annotated`` payload fields; ``build_parameters()`` uses ``HoiParams.from_struct(self)``.

- **PrepBackend methods**: Domain logic and defaults.
  Single source of truth for Prep-specific parameter defaults.

Standalone access: ``lh.backend.client.interfaces.MLPrepRoot.MphRoot.MPH.address``,
``HamiltonIntrospection(lh.backend.client)``.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import math
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Literal, Optional, Tuple, TypeVar, Union, overload

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton import prep_commands as PrepCmd
from pylabrobot.liquid_handling.backends.hamilton.common import fill_in_defaults
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp_backend import (
  HamiltonInterfaceResolver,
  HamiltonTCPClient,
  InterfaceSpec,
)
from pylabrobot.liquid_handling.liquid_classes.hamilton import (
  HamiltonLiquidClass,
  get_star_liquid_class,
)
from pylabrobot.liquid_handling.standard import (
  Drop,
  DropTipRack,
  GripDirection,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Coordinate, Tip
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonCoreGrippers
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.well import CrossSectionType, Well

logger = logging.getLogger(__name__)

_TCalibResult = TypeVar("_TCalibResult")


def _effective_radius(resource) -> float:
  """Effective radius for PrepCmd.CommonParameters.tube_radius.

  For circular wells uses the actual radius; for rectangular wells computes the
  radius of a circle with equivalent area so tube_radius is meaningful to the
  firmware's conical liquid-following model.
  """
  if isinstance(resource, Well) and resource.cross_section_type == CrossSectionType.RECTANGLE:
    return float(math.sqrt(resource.get_size_x() * resource.get_size_y() / math.pi))
  return float(resource.get_size_x() / 2)


def _build_container_segments(resource) -> list[PrepCmd.SegmentDescriptor]:
  """Derive PrepCmd.SegmentDescriptor list from a Well's geometry for liquid-following.

  Each segment is a frustum.  The firmware uses area_bottom/area_top to
  interpolate cross-sectional area A(z) within the segment and computes the
  Z-axis following speed as dz/dt = Q / A(z), where Q is volumetric flow rate.

  Returns [] when geometry cannot be determined; the firmware then falls back to
  the tube_radius / cone model in PrepCmd.CommonParameters.
  """
  if not isinstance(resource, Well):
    return []

  size_z = resource.get_size_z()

  if resource.cross_section_type == CrossSectionType.CIRCLE:
    area = math.pi * (resource.get_size_x() / 2) ** 2
  elif resource.cross_section_type == CrossSectionType.RECTANGLE:
    area = resource.get_size_x() * resource.get_size_y()
  else:
    return []

  if resource.supports_compute_height_volume_functions():
    # Non-linear geometry: approximate with N frustum segments by sampling dV/dh.
    n_boundaries = 11  # 10 segments
    heights = [size_z * i / (n_boundaries - 1) for i in range(n_boundaries)]
    eps = size_z / (n_boundaries - 1) * 0.1

    def area_at(h: float) -> float:
      h_lo = max(0.0, h - eps)
      h_hi = min(size_z, h + eps)
      dv = resource.compute_volume_from_height(h_hi) - resource.compute_volume_from_height(h_lo)
      return dv / (h_hi - h_lo)

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


def _segments_to_cone_geometry(
  segments: list[PrepCmd.SegmentDescriptor],
  fallback_radius: float,
) -> Tuple[float, float, float]:
  """Convert v2 frustum segments to v1 cone model (tube_radius, cone_height, cone_bottom_radius).

  The v1 firmware models container geometry as a cylinder (tube_radius) with an
  optional cone at the bottom (cone_height, cone_bottom_radius). Given N frustum
  segments from the v2 container_description:

  - tube_radius: sqrt(volume-weighted-average-area / pi) over all segments,
    giving the equivalent constant-area cylinder that displaces the same total
    volume per unit height.
  - cone_height / cone_bottom_radius: derived from the bottom segment when it
    tapers (area_bottom != area_top); zero when the bottom is cylindrical.

  For uniform cylinders this is exact. For tapered wells it is a best-fit
  single-frustum approximation — the firmware gets one linear dz/dt instead of
  piecewise, but total volume displacement matches.

  Args:
    segments: SegmentDescriptor list (may be empty).
    fallback_radius: Radius to return when segments is empty (from _effective_radius).

  Returns:
    (tube_radius, cone_height, cone_bottom_radius)
  """
  if not segments:
    return (fallback_radius, 0.0, 0.0)

  total_height = sum(s.height for s in segments)
  if total_height <= 0:
    return (fallback_radius, 0.0, 0.0)

  # Volume-weighted average cross-sectional area across all segments.
  weighted_area = sum(s.height * (s.area_top + s.area_bottom) / 2.0 for s in segments)
  avg_area = weighted_area / total_height
  tube_radius = math.sqrt(avg_area / math.pi)

  # Bottom cone: use first (bottom-most) segment if it tapers.
  bot = segments[0]
  if abs(bot.area_bottom - bot.area_top) > 1e-6:
    cone_height = bot.height
    cone_bottom_radius = math.sqrt(bot.area_bottom / math.pi)
  else:
    cone_height = 0.0
    cone_bottom_radius = 0.0

  return (tube_radius, cone_height, cone_bottom_radius)


def _absolute_z_from_well(op, z_air_margin_mm: float = 2.0):
  """Compute absolute Z values from well geometry for aspirate/dispense (STAR-aligned).

  Returns (well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z). The resource
  must have get_size_z() (e.g. a well/container); otherwise raises ValueError.
  """
  loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
  well_bottom_z = loc.z + op.offset.z
  liquid_surface_z = well_bottom_z + (op.liquid_height or 0.0)

  if not hasattr(op.resource, "get_size_z"):
    raise ValueError(
      "Resource must have get_size_z() to derive absolute Z (e.g. a Well or Container). "
      "Pass z_minimum, z_fluid, z_air explicitly for this operation."
    )
  size_z = op.resource.get_size_z()
  top_of_well_z = loc.z + size_z
  z_air_z = top_of_well_z + z_air_margin_mm
  return (well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z)


# =============================================================================
# PrepBackend
# =============================================================================

_CHANNEL_INDEX = {
  0: PrepCmd.ChannelIndex.RearChannel,
  1: PrepCmd.ChannelIndex.FrontChannel,
}

# Channel index -> deck waste resource name (PrepDeck: waste_rear, waste_front, waste_mph)
_CHANNEL_TO_WASTE_NAME = {
  0: "waste_rear",
  1: "waste_front",
  2: "waste_mph",
}

# Expected root name from discovery; validated at setup().
_EXPECTED_ROOT = "MLPrepRoot"


@dataclass(frozen=True)
class _LldDefaults:
  """Resolved pLLD / cLLD parameter pair (shared between aspirate and dispense)."""

  p_lld: PrepCmd.PLldParameters
  c_lld: PrepCmd.CLldParameters


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


class PrepBackend(LiquidHandlerBackend):
  """Backend for Hamilton Prep instruments using the shared TCP stack.

  Uses HamiltonTCPClient (self.client) for communication and introspection;
  implements LiquidHandlerBackend for liquid handling.
  Interfaces resolved lazily via _require() on first use.
  Construction accepts either host (and optionally port) to create the client
  with defaults, or client to inject a pre-configured HamiltonTCPClient.

  On-demand introspection: ``await self.client.introspect(path)``.
  """

  class LLDMode(enum.Enum):
    """Liquid level detection mode.

    Same numbering as STARBackend.LLDMode for cross-backend compatibility.
    CAPACITIVE (value=1) is named GAMMA on the STAR — CAPACITIVE is the correct term.
    The Prep firmware uses separate command variants for LLD vs no-LLD, so all
    channels in a single aspirate/dispense call must use the same mode category
    (any LLD mode, or OFF). Mixing OFF with CAPACITIVE/PRESSURE in one call is
    not supported and will raise ValueError.
    """

    OFF = 0
    CAPACITIVE = 1  # STARBackend.LLDMode.GAMMA — capacitive (cLLD)
    PRESSURE = 2  # pressure-based (pLLD)
    DUAL = 3  # both capacitive and pressure

  # Declare known object paths via InterfaceSpec. deck_config required (key positions, traverse height, deck info).
  _INTERFACES: dict[str, InterfaceSpec] = {
    "mlprep": InterfaceSpec("MLPrepRoot.MLPrep", True, True),
    "pipettor": InterfaceSpec("MLPrepRoot.PipettorRoot.Pipettor", True, True),
    "coordinator": InterfaceSpec("MLPrepRoot.ChannelCoordinator", True, True),
    "calibration": InterfaceSpec("MLPrepRoot.MLPrepCalibration", False, True),
    "deck_config": InterfaceSpec("MLPrepRoot.MLPrepCalibration.DeckConfiguration", True, True),
    "mph": InterfaceSpec("MLPrepRoot.MphRoot.MPH", False, True),
    "mlprep_service": InterfaceSpec("MLPrepRoot.MLPrepService", False, True),
  }

  # V2 aspirate/dispense command IDs (interface 1 on Pipettor).
  _V2_PIPETTING_CMD_IDS = {38, 39, 40, 41, 42, 43}

  @overload
  def __init__(
    self,
    *,
    host: str,
    port: int = 2000,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ) -> None: ...

  @overload
  def __init__(
    self,
    *,
    client: HamiltonTCPClient,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ) -> None: ...

  def __init__(
    self,
    *,
    host: Optional[str] = None,
    port: int = 2000,
    client: Optional[HamiltonTCPClient] = None,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ) -> None:
    """Initialize Prep backend.

    Args:
      host: Instrument hostname or IP; used when client is not provided.
      port: TCP port (default 2000).
      client: Optional pre-configured HamiltonTCPClient (mutually exclusive
        with host).
      default_traverse_height: Optional default traverse height in mm.
      use_v1_aspirate_dispense: When True, skip the v2 capability probe and
        always use v1 aspirate/dispense commands (cmd 1-6). When False
        (default), setup probes for v2 commands (cmd 38-43) and raises
        RuntimeError if they are not available.
    """
    super().__init__()
    if client is not None:
      if host is not None:
        raise TypeError("Provide either host or client, not both")
      self.client = client
    elif host is not None:
      self.client = HamiltonTCPClient(host=host, port=port)
    else:
      raise TypeError("Provide either host or client")
    self._config: Optional[PrepCmd.InstrumentConfig] = None
    self._user_traverse_height: Optional[float] = default_traverse_height
    self._resolver = HamiltonInterfaceResolver(self.client, self._INTERFACES)
    self._num_channels: Optional[int] = None
    self._has_mph: Optional[bool] = None
    self._gripper_tool_on: bool = False
    self._channel_sleeve_sensor_addrs: list[Address] = []
    self._channel_zdrive_addrs: list[Address] = []
    self._channel_node_info_addrs: list[Address] = []
    self._mlprep_cpu_addr: Optional[Address] = None
    self._module_info_addr: Optional[Address] = None
    self._channel_bounds: list[dict] = []
    self._calibration_session_active: bool = False
    self._use_v1_aspirate_dispense: bool = use_v1_aspirate_dispense
    self._supports_v2_pipetting: Optional[bool] = None

  def _has_interface(self, name: str) -> bool:
    """Return True if the interface was resolved and is present."""
    return self._resolver.has_interface(name)

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
    from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import HamiltonIntrospection

    dest = await self._require("pipettor")
    intro = HamiltonIntrospection(self.client)
    methods = await intro.get_all_methods(dest)
    iface1_ids = {m.method_id for m in methods if m.interface_id == 1}
    return self._V2_PIPETTING_CMD_IDS.issubset(iface1_ids)

  def _resolve_command_version(self, override: Optional[Literal["v1", "v2"]] = None) -> bool:
    """Resolve whether to use v2 commands for this call. Returns True for v2.

    Resolution order:
    1. Per-call override ("v1" or "v2") — takes precedence. Raises ValueError
       if "v2" requested but firmware doesn't support it.
    2. Backend-level ``use_v1_aspirate_dispense`` / probe result from setup.
    """
    if override == "v1":
      return False
    if override == "v2":
      if self._supports_v2_pipetting is False:
        raise ValueError(
          "v2 aspirate/dispense commands (cmd 38-43) are not supported by this firmware. "
          "Use command_version='v1' or pass use_v1_aspirate_dispense=True to PrepBackend."
        )
      return True
    return self._supports_v2_pipetting is True

  # ---------------------------------------------------------------------------
  # Setup & interface resolution
  # ---------------------------------------------------------------------------

  async def _require(self, name: str) -> Address:
    """Resolve and return an interface address, lazy on first call. Raises RuntimeError if not found."""
    return await self._resolver.require(name)

  async def get_present_channels(self) -> Optional[Tuple[PrepCmd.ChannelIndex, ...]]:
    """Query which channels are present (GetPresentChannels on MLPrepService).

    Maps raw enum values to PrepCmd.ChannelIndex: 0=InvalidIndex, 1=FrontChannel,
    2=RearChannel, 3=MPHChannel. Returns None if MLPrepService is unavailable
    or the command fails (caller should use defaults).
    """
    if not self._has_interface("mlprep_service"):
      return None
    try:
      service_addr = await self._require("mlprep_service")
      resp = await self.client.send_command(PrepCmd.PrepGetPresentChannels(dest=service_addr))
      if resp is None or not getattr(resp, "channels", None):
        return None
      present = tuple(
        PrepCmd.ChannelIndex(v) if v in (0, 1, 2, 3) else PrepCmd.ChannelIndex.InvalidIndex
        for v in resp.channels
      )
      return present
    except (
      TimeoutError,
      ConnectionError,
      ConnectionResetError,
      ConnectionAbortedError,
      BrokenPipeError,
      OSError,
    ):
      raise
    except Exception:
      return None

  async def setup(self, smart: bool = True, force_initialize: bool = False):
    """Set up Prep: connect, discover objects, then conditionally initialize MLPrep.

    Interfaces: .address for MLPrep/Pipettor; depth-2 paths resolved in setup.

    Order:
      1. TCP + Protocol 7/3 init, root discovery, and depth-1 interface discovery (self.client.setup())
      2. Lazy-resolve Pipettor (depth-2) for commands
      3. If force_initialize: always run Initialize(smart=smart).
         Else: query GetIsInitialized; only run Initialize(smart=smart) when not initialized.
      4. Mark setup complete.

    Args:
      smart: When we call Initialize, pass this to the firmware (default True).
      force_initialize: If True, always run Initialize. If False, run Initialize only
        when GetIsInitialized reports not initialized (e.g. reconnect-safe).
    """
    await self.client.setup()

    # Validate discovered root matches this backend
    discovered = self.client.discovered_root_name()
    if discovered != _EXPECTED_ROOT:
      raise RuntimeError(
        f"Expected root '{_EXPECTED_ROOT}' (Prep), but discovered '{discovered}'. Wrong instrument?"
      ) from None

    # Resolve all interfaces (required fail-fast; optional log and continue)
    await self._resolver.run_setup_loop()

    if force_initialize:
      await self._run_initialize(smart=smart)
      logger.info("Prep initialization complete (force_initialize=True)")
    else:
      try:
        already = await self.is_initialized()
      except Exception as e:
        logger.error("GetIsInitialized failed; cannot decide whether to init: %s", e)
        raise
      if already:
        logger.info("MLPrep already initialized, skipping Initialize")
      else:
        await self._run_initialize(smart=smart)
        logger.info("Prep initialization complete")

    self._config = await self._get_hardware_config()
    self._num_channels = self._config.num_channels
    self._has_mph = self._config.has_mph
    logger.info(
      "Hardware config: has_enclosure=%s, safe_speeds=%s, traverse_height=%s, "
      "deck_bounds=%s, deck_sites=%d, waste_sites=%d, num_channels=%s, has_mph=%s",
      self._config.has_enclosure,
      self._config.safe_speeds_enabled,
      self._config.default_traverse_height,
      self._config.deck_bounds,
      len(self._config.deck_sites),
      len(self._config.waste_sites),
      self._config.num_channels,
      self._config.has_mph,
    )

    # Discover per-channel drive addresses from the object tree (after init).
    await self._discover_channel_drives()

    # Cache per-channel movement bounds from firmware
    try:
      self._channel_bounds = await self.request_channel_bounds()
    except Exception as e:
      logger.warning("Failed to query channel bounds: %s", e)
      self._channel_bounds = []
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
      except Exception:
        supported = False
      if not supported:
        raise RuntimeError(
          "V2 aspirate/dispense commands (cmd 38-43) are not supported by this firmware. "
          "Pass use_v1_aspirate_dispense=True to PrepBackend to use v1 commands (cmd 1-6) instead."
        )
      self._supports_v2_pipetting = True
      logger.info("V2 aspirate/dispense support: True")

    self.setup_finished = True

  async def _discover_channel_drives(self) -> None:
    """Walk the MLPrepRoot object tree to discover per-channel and module addresses by name.

    Channel drives (per pipettor channel, skipping "MPH Channel Root"):
      MLPrepRoot → "Channel Root" → "Channel" → "Squeeze" → "SDrive" (sleeve sensor)
      MLPrepRoot → "Channel Root" → "Channel" → "ZAxis" → "ZDrive"
      MLPrepRoot → "Channel Root" → "NodeInformation"

    Module-level objects (for firmware version queries):
      MLPrepRoot → "MLPrepCpu"
      MLPrepRoot → "PipettorRoot" → "ModuleInformation"

    All lookups are by object name, not hardcoded object IDs.
    """
    from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import HamiltonIntrospection

    self._channel_sleeve_sensor_addrs = []
    self._channel_zdrive_addrs = []
    self._channel_node_info_addrs = []
    self._mlprep_cpu_addr = None
    self._module_info_addr = None

    intro = HamiltonIntrospection(self.client)
    root_addrs = self.client._registry.get_root_addresses()
    if not root_addrs:
      return

    root_addr = root_addrs[0]
    root_info = await intro.get_object(root_addr)

    async def find_child_by_name(parent_addr, parent_info, name):
      """Find a direct child object by name. Returns (address, info) or (None, None)."""
      for i in range(parent_info.subobject_count):
        try:
          child_addr = await intro.get_subobject_address(parent_addr, i)
          child_info = await intro.get_object(child_addr)
          if child_info.name == name:
            return child_addr, child_info
        except Exception:
          continue
      return None, None

    for i in range(root_info.subobject_count):
      try:
        sub_addr = await intro.get_subobject_address(root_addr, i)
        sub_info = await intro.get_object(sub_addr)
      except Exception:
        continue

      # MLPrepCpu — controller firmware version info
      if sub_info.name == "MLPrepCpu":
        self._mlprep_cpu_addr = sub_addr
        logger.debug("Discovered MLPrepCpu at %s", sub_addr)
        continue

      # PipettorRoot → ModuleInformation
      if sub_info.name == "PipettorRoot":
        mod_addr, _ = await find_child_by_name(sub_addr, sub_info, "ModuleInformation")
        if mod_addr is not None:
          self._module_info_addr = mod_addr
          logger.debug("Discovered ModuleInformation at %s", mod_addr)
        continue

      if sub_info.name != "Channel Root":
        continue

      # Channel Root → Channel → Squeeze → SDrive
      channel_addr, channel_info = await find_child_by_name(sub_addr, sub_info, "Channel")
      if channel_addr is None:
        logger.warning("Channel Root on node %d has no 'Channel' child, skipping", sub_addr.node)
        continue

      squeeze_addr, squeeze_info = await find_child_by_name(channel_addr, channel_info, "Squeeze")
      sdrive_addr = None
      if squeeze_addr is not None and squeeze_info is not None:
        sdrive_addr, _ = await find_child_by_name(squeeze_addr, squeeze_info, "SDrive")

      # Channel Root → Channel → ZAxis → ZDrive
      zaxis_addr, zaxis_info = await find_child_by_name(channel_addr, channel_info, "ZAxis")
      zdrive_addr = None
      if zaxis_addr is not None and zaxis_info is not None:
        zdrive_addr, _ = await find_child_by_name(zaxis_addr, zaxis_info, "ZDrive")

      # Channel Root → NodeInformation
      node_info_addr, _ = await find_child_by_name(sub_addr, sub_info, "NodeInformation")

      if sdrive_addr is not None:
        self._channel_sleeve_sensor_addrs.append(sdrive_addr)
      else:
        logger.warning("Channel Root on node %d: could not find Squeeze.SDrive", sub_addr.node)

      if zdrive_addr is not None:
        self._channel_zdrive_addrs.append(zdrive_addr)
      else:
        logger.warning("Channel Root on node %d: could not find ZAxis.ZDrive", sub_addr.node)

      if node_info_addr is not None:
        self._channel_node_info_addrs.append(node_info_addr)
      else:
        logger.warning("Channel Root on node %d: could not find NodeInformation", sub_addr.node)

      logger.debug(
        "Discovered channel on node %d: sleeve_sensor=%s, ZDrive=%s, NodeInfo=%s",
        sub_addr.node,
        sdrive_addr,
        zdrive_addr,
        node_info_addr,
      )

    logger.info(
      "Discovered %d pipettor channel drive pairs", len(self._channel_sleeve_sensor_addrs)
    )

  async def _run_initialize(self, smart: bool):
    """Send PrepCmd.PrepInitialize to MLPrep (shared by setup)."""
    await self.client.send_command(
      PrepCmd.PrepInitialize(
        dest=await self._require("mlprep"),
        smart=smart,
        tip_drop_params=PrepCmd.InitTipDropParameters(
          default_values=True,
          x_position=287.0,
          rolloff_distance=3,
          channel_parameters=[],
        ),
      )
    )

  async def _get_hardware_config(self) -> PrepCmd.InstrumentConfig:
    """Aggregate getters: query MLPrep, DeckConfiguration, and MLPrepService for hardware config.

    Includes deck/enclosure, deck sites, waste sites, traverse height, and channel
    configuration (num_channels, has_mph) from GetPresentChannels.
    """
    mlprep = await self._require("mlprep")
    enc_resp = await self.client.send_command(PrepCmd.PrepGetIsEnclosurePresent(dest=mlprep))
    safe_resp = await self.client.send_command(PrepCmd.PrepGetSafeSpeedsEnabled(dest=mlprep))
    height_resp = await self.client.send_command(PrepCmd.PrepGetDefaultTraverseHeight(dest=mlprep))
    has_enclosure = bool(enc_resp.value) if enc_resp else False
    safe_speeds_enabled = bool(safe_resp.value) if safe_resp else False
    default_traverse_height = float(height_resp.value) if height_resp else None

    deck_bounds: Optional[PrepCmd.DeckBounds] = None
    deck_sites: Tuple[PrepCmd.DeckSiteInfo, ...] = ()
    waste_sites: Tuple[PrepCmd.WasteSiteInfo, ...] = ()
    deck_addr = await self._require("deck_config")

    bounds_resp = await self.client.send_command(PrepCmd.PrepGetDeckBounds(dest=deck_addr))
    if bounds_resp:
      deck_bounds = PrepCmd.DeckBounds(
        min_x=bounds_resp.min_x,
        max_x=bounds_resp.max_x,
        min_y=bounds_resp.min_y,
        max_y=bounds_resp.max_y,
        min_z=bounds_resp.min_z,
        max_z=bounds_resp.max_z,
      )

    sites_resp = await self.client.send_command(PrepCmd.PrepGetDeckSiteDefinitions(dest=deck_addr))
    if sites_resp and sites_resp.sites:
      deck_sites = tuple(
        PrepCmd.DeckSiteInfo(
          id=int(s.id),
          left_bottom_front_x=float(s.left_bottom_front_x),
          left_bottom_front_y=float(s.left_bottom_front_y),
          left_bottom_front_z=float(s.left_bottom_front_z),
          length=float(s.length),
          width=float(s.width),
          height=float(s.height),
        )
        for s in sites_resp.sites
      )
      logger.debug("Discovered %d deck sites", len(deck_sites))

    waste_resp = await self.client.send_command(PrepCmd.PrepGetWasteSiteDefinitions(dest=deck_addr))
    if waste_resp and waste_resp.sites:
      waste_sites = tuple(
        PrepCmd.WasteSiteInfo(
          index=int(s.index),
          x_position=float(s.x_position),
          y_position=float(s.y_position),
          z_position=float(s.z_position),
          z_seek=float(s.z_seek),
        )
        for s in waste_resp.sites
      )
      logger.debug("Discovered %d waste sites: %s", len(waste_sites), waste_sites)

    # Channel configuration (1 vs 2 dual-channel pipettor, 8MPH) from MLPrepService
    present = await self.get_present_channels()
    if present is not None:
      dual = [
        c
        for c in present
        if c in (PrepCmd.ChannelIndex.FrontChannel, PrepCmd.ChannelIndex.RearChannel)
      ]
      num_channels = len(dual)
      has_mph = PrepCmd.ChannelIndex.MPHChannel in present
    else:
      num_channels = 2
      has_mph = False

    return PrepCmd.InstrumentConfig(
      deck_bounds=deck_bounds,
      has_enclosure=has_enclosure,
      safe_speeds_enabled=safe_speeds_enabled,
      deck_sites=deck_sites,
      waste_sites=waste_sites,
      default_traverse_height=default_traverse_height,
      num_channels=num_channels,
      has_mph=has_mph,
    )

  # ---------------------------------------------------------------------------
  # Properties
  # ---------------------------------------------------------------------------

  @property
  def num_channels(self) -> int:
    """Number of independent dual-channel pipettor channels (1 or 2). Set at setup from GetPresentChannels."""
    if self._num_channels is None:
      raise RuntimeError("num_channels not set. Call setup() first.")
    return self._num_channels

  @property
  def has_mph(self) -> bool:
    """True if the 8-channel Multi-Pipetting Head (8MPH) is present. Set at setup from GetPresentChannels."""
    return bool(self._has_mph) if self._has_mph is not None else False

  def _set_calibration_session_active(self, active: bool) -> None:
    self._calibration_session_active = active

  def calibration_session(
    self,
    *,
    float_tol: float = 1e-6,
    report_after_command: bool = True,
    report_scope: Literal["related", "full"] = "related",
    session_read_timeout: Optional[float] = None,
  ) -> "PrepCalibrationSession":
    """Create a managed calibration session bound to this backend."""
    return PrepCalibrationSession(
      self,
      float_tol=float_tol,
      report_after_command=report_after_command,
      report_scope=report_scope,
      session_read_timeout=session_read_timeout,
    )

  @property
  def num_arms(self) -> int:
    """Number of resource-handling arms. 1 when deck has core_grippers and 2 channels, else 0."""
    if self._deck is None or self._num_channels is None or self._num_channels != 2:
      return 0
    try:
      mount = self.deck.get_resource("core_grippers")
      return 1 if isinstance(mount, HamiltonCoreGrippers) else 0
    except Exception:
      return 0

  def _resolve_traverse_height(self, final_z: Optional[float]) -> float:
    """Resolve final_z: explicit arg > user-set default > probed value. Raises if none available."""
    if final_z is not None:
      return final_z
    if self._user_traverse_height is not None:
      return self._user_traverse_height
    if self._config is not None and self._config.default_traverse_height is not None:
      return self._config.default_traverse_height
    raise RuntimeError(
      "Default traverse height is required for this operation but could not be determined. "
      "Either pass final_z explicitly to this call, or set it via "
      "PrepBackend(..., default_traverse_height=<mm>) or backend.set_default_traverse_height(<mm>). "
      "If the instrument supports it, the value is also probed during setup(); ensure setup() completed successfully."
    ) from None

  async def is_initialized(self) -> bool:
    """Query whether MLPrep reports as initialized (GetIsInitialized, cmd=2).

    Uses MLPrep method from introspection: GetIsInitialized(()) -> value: I64.
    Requires MLPrep to be discovered (e.g. after self.client.setup() and
    _discover_prep_objects()). Call before or after PrepCmd.PrepInitialize to test.
    """
    result = await self.client.send_command(
      PrepCmd.PrepGetIsInitialized(dest=await self._require("mlprep"))
    )
    if result is None:
      return False
    return bool(result.value)

  async def get_tip_and_needle_definitions(self) -> Tuple[PrepCmd.TipDefinition, ...]:
    """Return tip/needle definitions registered on the instrument (GetTipAndNeedleDefinitions, cmd=11)."""
    result = await self.client.send_command(
      PrepCmd.PrepGetTipAndNeedleDefinitions(dest=await self._require("mlprep"))
    )
    if result is None or not getattr(result, "definitions", None):
      return ()
    return tuple(result.definitions)

  async def get_calibration_site_definitions(self) -> Tuple[PrepCmd.CalibrationSiteInfo, ...]:
    """Return calibration site definitions from DeckConfiguration (GetCalibrationSiteDefinitions, cmd=3).

    Each entry is geometry in mm (left-bottom-front corner, length/width/height) plus ``post`` flag.
    Requires ``deck_config`` (``MLPrepRoot.MLPrepCalibration.DeckConfiguration``) to be resolved.
    """
    result = await self.client.send_command(
      PrepCmd.PrepGetCalibrationSiteDefinitions(dest=await self._require("deck_config"))
    )
    if result is None or not getattr(result, "sites", None):
      return ()
    return tuple(
      PrepCmd.CalibrationSiteInfo(
        id=int(s.id),
        left_bottom_front_x=float(s.left_bottom_front_x),
        left_bottom_front_y=float(s.left_bottom_front_y),
        left_bottom_front_z=float(s.left_bottom_front_z),
        length=float(s.length),
        width=float(s.width),
        height=float(s.height),
        post=bool(s.post),
      )
      for s in result.sites
    )

  async def is_parked(self) -> bool:
    """Query whether MLPrep is parked (IsParked, cmd=34)."""
    result = await self.client.send_command(
      PrepCmd.PrepIsParked(dest=await self._require("mlprep"))
    )
    if result is None:
      return False
    return bool(result.value)

  async def is_spread(self) -> bool:
    """Query whether channels are spread (IsSpread, cmd=35). Pipettor commands typically require spread state."""
    result = await self.client.send_command(
      PrepCmd.PrepIsSpread(dest=await self._require("mlprep"))
    )
    if result is None:
      return False
    return bool(result.value)

  # ---------------------------------------------------------------------------
  # MLPrepCalibration commands
  # ---------------------------------------------------------------------------

  async def begin_calibration(self) -> None:
    """Enter calibration mode (BeginCalibration, cmd=1). Must be called before axis calibration commands."""
    await self.client.send_command(
      PrepCmd.PrepBeginCalibration(dest=await self._require("calibration"))
    )

  async def cancel_calibration(self) -> None:
    """Cancel an active calibration session (CancelCalibration, cmd=2)."""
    await self.client.send_command(
      PrepCmd.PrepCancelCalibration(dest=await self._require("calibration"))
    )

  async def end_calibration(self, date_time: Optional[PrepCmd.HoiDateTime] = None) -> None:
    """End calibration and store results with timestamp (EndCalibration, cmd=3).

    Args:
      date_time: Timestamp for the calibration record. Defaults to now.
    """
    if date_time is None:
      date_time = PrepCmd.HoiDateTime.now()
    await self.client.send_command(
      PrepCmd.PrepEndCalibration(dest=await self._require("calibration"), date_time=date_time)
    )

  async def reset_calibration(self, store: bool = False) -> None:
    """Reset calibration data (ResetCalibration, cmd=4).

    Args:
      store: If True, persist current calibration before resetting.
    """
    await self.client.send_command(
      PrepCmd.PrepResetCalibration(dest=await self._require("calibration"), store=store)
    )

  async def calibration_initialize(self) -> None:
    """Initialize calibration hardware (CalibrationInitialize, cmd=5)."""
    await self.client.send_command(
      PrepCmd.PrepCalibrationInitialize(dest=await self._require("calibration"))
    )

  async def read_calibration_values(
    self, read_timeout: Optional[float] = None
  ) -> PrepCmd.CalibrationValues:
    """Read calibration values without requiring a managed calibration session.

    This is intended for pre-run validation/checks where no calibration mutation
    commands are being executed.
    """
    result = await self.client.send_command(
      PrepCmd.PrepGetCalibrationValues(dest=await self._require("calibration")),
      read_timeout=read_timeout,
    )
    if result is None:
      return PrepCmd.CalibrationValues(
        independent_offset_x=0.0,
        mph_offset_x=0.0,
        channel_values=(),
      )

    return PrepCmd.CalibrationValues(
      independent_offset_x=float(result.independent_offset_x),
      mph_offset_x=float(result.mph_offset_x),
      channel_values=tuple(
        PrepCmd.ChannelCalibrationValuesInfo(
          index=int(cv.index),
          y_offset=float(cv.y_offset),
          z_offset=float(cv.z_offset),
          squeeze_position=int(cv.squeeze_position),
          z_touchoff=int(cv.z_touchoff),
          pressure_shift=int(cv.pressure_shift),
          pressure_monitoring_shift=int(cv.pressure_monitoring_shift),
          dispenser_return_distance=float(cv.dispenser_return_distance),
          z_tip_height=float(cv.z_tip_height),
          core_ii=bool(cv.core_ii),
        )
        for cv in (result.channel_values or [])
      ),
    )

  # ---------------------------------------------------------------------------
  # LiquidHandlerBackend abstract methods
  # ---------------------------------------------------------------------------

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    final_z: Optional[float] = None,
    seek_speed: float = 15.0,
    z_seek_offset: Optional[float] = None,
    enable_tadm: bool = False,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
  ):
    """Pick up tips.

    The arm moves to z_seek during lateral XY approach, then descends to z_position
    to engage the tip. Default z_seek = z_position + fitting_depth + 5mm (tip-type-
    aware; avoids descending into the rack during approach).

    Args:
      final_z: Traverse/safe height (mm) for the move and Z position after command.
        If None, uses the user-set value (constructor or set_default_traverse_height) or the
        value probed from the instrument at setup. Raises RuntimeError if none is available.
      seek_speed: Speed (mm/s) for the seek/approach phase.
      z_seek_offset: Additive mm on top of the geometry-based default. None = 0
        (use default only). Use to raise or lower the approach height if needed.
      enable_tadm: Enable tip-adjust during pickup.
      dispenser_volume: Dispenser volume for TADM (if enabled).
      dispenser_speed: Dispenser speed for TADM (if enabled).
    """
    assert len(ops) == len(use_channels)
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )

    resolved_final_z = self._resolve_traverse_height(final_z)

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}
    tip_positions: List[PrepCmd.TipPositionParameters] = []
    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "t")
      params = PrepCmd.TipPositionParameters.for_op(
        _CHANNEL_INDEX[ch],
        loc,
        op.resource.get_tip(),
        z_seek_offset=z_seek_offset,
      )
      tip_positions.append(params)

    assert len(set(op.tip for op in ops)) == 1, "All ops must use the same tip type"
    tip = ops[0].tip
    tip_definition = PrepCmd.TipPickupParameters(
      default_values=False,
      volume=tip.maximal_volume,
      length=tip.total_tip_length - tip.fitting_depth,
      tip_type=PrepCmd.TipTypes.StandardVolume,
      has_filter=tip.has_filter,
      is_needle=False,
      is_tool=False,
    )

    await self.client.send_command(
      PrepCmd.PrepPickUpTips(
        dest=await self._require("pipettor"),
        tip_positions=tip_positions,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_definition=tip_definition,
        enable_tadm=enable_tadm,
        dispenser_volume=dispenser_volume,
        dispenser_speed=dispenser_speed,
      )
    )

  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    final_z: Optional[float] = None,
    seek_speed: float = 30.0,
    z_seek_offset: Optional[float] = None,
    drop_type: PrepCmd.TipDropType = PrepCmd.TipDropType.FixedHeight,
    tip_roll_off_distance: float = 0.0,
  ):
    """Drop tips.

    The arm moves to z_seek during lateral XY approach (tip is on pipette, so tip
    bottom is at z_seek - (total_tip_length - fitting_depth)). z_position uses
    fitting depth so the tip bottom lands at the spot surface; default z_seek =
    z_position + 10mm so the tip bottom stays above adjacent tips in the rack.

    Args:
      final_z: Traverse/safe height (mm) for the move and Z position after command.
        If None, uses the user-set value (constructor or set_default_traverse_height) or the
        value probed from the instrument at setup. Raises RuntimeError if none is available.
      seek_speed: Speed (mm/s) for the seek/approach phase.
      z_seek_offset: Additive mm on top of the geometry-based default. None = 0
        (use default only). Use to raise or lower the approach height if needed.
      drop_type: How the tip is released (FixedHeight, Stall, or CLLDSeek).
      tip_roll_off_distance: Roll-off distance (mm) for tip release.
    """
    assert len(ops) == len(use_channels)
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )

    all_trash = all(isinstance(op.resource, Trash) for op in ops)
    all_tip_spots = all(isinstance(op.resource, TipSpot) for op in ops)
    if not (all_trash or all_tip_spots):
      raise ValueError("Cannot mix waste (Trash) and tip spots in a single drop_tips call.")

    resolved_final_z = self._resolve_traverse_height(final_z)
    roll_off = 3.0 if (all_trash and tip_roll_off_distance == 0.0) else tip_roll_off_distance
    # Use Stall when dropping to waste so the pipette detects contact before release.
    resolved_drop_type = PrepCmd.TipDropType.Stall if all_trash else drop_type

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}
    tip_positions: List[PrepCmd.TipDropParameters] = []
    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      tip = op.tip
      if all_trash:
        waste_name = _CHANNEL_TO_WASTE_NAME.get(ch, "waste_mph")
        if not self.deck.has_resource(waste_name):
          raise ValueError(
            f"Cannot drop tips to waste: deck has no waste position '{waste_name}'. "
            "Use a deck with waste_rear, waste_front (and waste_mph if using MPH)."
          )
        loc = self.deck.get_resource(waste_name).get_absolute_location("c", "c", "t")
      else:
        loc = op.resource.get_absolute_location("c", "c", "t") + op.offset
      params = PrepCmd.TipDropParameters.for_op(
        _CHANNEL_INDEX[ch],
        loc,
        tip,
        z_seek_offset=z_seek_offset,
        drop_type=resolved_drop_type,
      )
      tip_positions.append(params)

    await self.client.send_command(
      PrepCmd.PrepDropTips(
        dest=await self._require("pipettor"),
        tip_positions=tip_positions,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_roll_off_distance=roll_off,
      )
    )

  # ---------------------------------------------------------------------------
  # MPH head tip operations
  # ---------------------------------------------------------------------------

  async def pick_up_tips_mph(
    self,
    tip_spot: Union[TipSpot, List[TipSpot]],
    tip_mask: int = 0xFF,
    final_z: Optional[float] = None,
    seek_speed: float = 15.0,
    z_seek_offset: Optional[float] = None,
    enable_tadm: bool = False,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
  ) -> None:
    """Pick up tips with the MPH (multi-probe) head.

    Routes to MLPrepRoot.MphRoot.MPH (PickupTips, iface=1 id=9). The MPH
    takes a single reference position (type_57 = single struct) rather than
    a per-channel list (type_61). All 8 probes move as one unit; tip_mask
    selects which channels engage (default 0xFF = all 8).

    The first TipSpot is used as the reference position. For a full column
    pickup, pass tip_rack["A1:H1"] — only the first spot's (x,y,z) is sent,
    all 8 probes engage via tip_mask.

    Args:
      tip_spot: A single TipSpot or a list. The first spot is used as the
        reference position for all probes.
      tip_mask: 8-bit bitmask of active MPH channels (bit 0 = channel 0,
        bit 7 = channel 7). Default 0xFF picks up with all 8 channels.
      final_z: Traverse/safe height (mm) after command. If None, uses the
        probed or user-set default traverse height.
      seek_speed: Speed (mm/s) for the Z approach phase.
      z_seek_offset: Additive mm offset on top of the geometry-based seek Z
        (tip.fitting_depth + 5 mm). None = 0.
      enable_tadm: Enable tip-attachment detection (TADM) during pickup.
      dispenser_volume: Dispenser volume for TADM (ignored when False).
      dispenser_speed: Dispenser speed for TADM (ignored when False).
    """
    if not self.has_mph:
      raise RuntimeError("Instrument does not have an 8MPH head. Cannot use pick_up_tips_mph.")
    if isinstance(tip_spot, list):
      spots = tip_spot
    else:
      spots = [tip_spot]
    if not spots:
      raise ValueError("pick_up_tips_mph: tip_spot list is empty")
    resolved_final_z = self._resolve_traverse_height(final_z)

    ref_spot = spots[0]
    tip = ref_spot.get_tip()
    loc = ref_spot.get_absolute_location("c", "c", "t")
    tip_parameters = PrepCmd.TipPositionParameters.for_op(
      PrepCmd.ChannelIndex.MPHChannel, loc, tip, z_seek_offset=z_seek_offset
    )

    tip_definition = PrepCmd.TipPickupParameters(
      default_values=False,
      volume=tip.maximal_volume,
      length=tip.total_tip_length - tip.fitting_depth,
      tip_type=PrepCmd.TipTypes.StandardVolume,
      has_filter=tip.has_filter,
      is_needle=False,
      is_tool=False,
    )

    await self.client.send_command(
      PrepCmd.MphPickupTips(
        dest=await self._require("mph"),
        tip_parameters=tip_parameters,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_definition=tip_definition,
        enable_tadm=enable_tadm,
        dispenser_volume=dispenser_volume,
        dispenser_speed=dispenser_speed,
        tip_mask=tip_mask,
      )
    )

  async def drop_tips_mph(
    self,
    tip_spot: Union[TipSpot, List[TipSpot]],
    final_z: Optional[float] = None,
    seek_speed: float = 30.0,
    z_seek_offset: Optional[float] = None,
    drop_type: PrepCmd.TipDropType = PrepCmd.TipDropType.FixedHeight,
    tip_roll_off_distance: float = 0.0,
  ) -> None:
    """Drop tips held by the MPH head.

    Routes to MLPrepRoot.MphRoot.MPH (DropTips, iface=1 id=12). The MPH
    takes a single reference position (type_57 = single struct); all probes
    drop together at the same location.

    Args:
      tip_spot: Target drop position. The first spot is used as the reference
        position for all probes.
      final_z: Traverse/safe height (mm) after command. If None, uses the
        probed or user-set default traverse height.
      seek_speed: Speed (mm/s) for the Z seek/approach phase.
      z_seek_offset: Additive mm offset on top of the geometry-based seek Z.
        None = 0 (default seeks tip_bottom + total_tip_length + 10 mm).
      drop_type: How tips are released (FixedHeight, Stall, or CLLDSeek).
      tip_roll_off_distance: Roll-off distance (mm) for tip release.
    """
    if not self.has_mph:
      raise RuntimeError("Instrument does not have an 8MPH head. Cannot use drop_tips_mph.")
    if isinstance(tip_spot, list):
      spots = tip_spot
    else:
      spots = [tip_spot]
    if not spots:
      raise ValueError("drop_tips_mph: tip_spot list is empty")
    resolved_final_z = self._resolve_traverse_height(final_z)

    ref_spot = spots[0]
    tip = ref_spot.get_tip()
    loc = ref_spot.get_absolute_location("c", "c", "t")
    drop_parameters = PrepCmd.TipDropParameters.for_op(
      PrepCmd.ChannelIndex.MPHChannel,
      loc,
      tip,
      z_seek_offset=z_seek_offset,
      drop_type=drop_type,
    )

    await self.client.send_command(
      PrepCmd.MphDropTips(
        dest=await self._require("mph"),
        drop_parameters=drop_parameters,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_roll_off_distance=tip_roll_off_distance,
      )
    )

  # ---------------------------------------------------------------------------
  # V1/V2 aspirate/dispense dispatch helpers
  # ---------------------------------------------------------------------------

  @staticmethod
  def _patch_common_with_cone(
    common: PrepCmd.CommonParameters,
    segments: list[PrepCmd.SegmentDescriptor],
  ) -> PrepCmd.CommonParameters:
    """Return a copy of CommonParameters with cone geometry derived from segments.

    Used when downgrading a v2 parameter set to v1: the v2 container_description
    is removed and its geometry information is folded into the v1 cone model
    fields of CommonParameters.
    """
    tube_r, cone_h, cone_br = _segments_to_cone_geometry(segments, common.tube_radius)
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

  # ---------------------------------------------------------------------------
  # Shared LLD / TADM resolution helpers
  # ---------------------------------------------------------------------------

  def _resolve_effective_lld(
    self,
    lld_mode: Optional[List[LLDMode]],
    use_lld: bool,
    lld: Optional[PrepCmd.LldParameters],
    n: int,
    *,
    allowed_modes: Optional[frozenset[LLDMode]] = None,
  ) -> bool:
    """Determine whether LLD is active for this pipetting call.

    Validates ``lld_mode`` length, rejects disallowed modes (e.g. PRESSURE for
    dispense), enforces all-or-nothing across channels, and returns a single bool.
    Falls back to ``use_lld`` / ``lld`` presence when ``lld_mode`` is None.
    """
    if lld_mode is not None:
      if len(lld_mode) != n:
        raise ValueError(f"lld_mode length must match len(ops): {len(lld_mode)} != {n}")
      if allowed_modes is not None:
        for m in lld_mode:
          if m != self.LLDMode.OFF and m not in allowed_modes:
            raise ValueError(
              f"Dispense does not support {m.name} LLD — only CAPACITIVE or OFF. "
              "Pressure-based LLD requires aspiration (plunger movement)."
            )
      lld_on = [m != self.LLDMode.OFF for m in lld_mode]
      if any(lld_on) and not all(lld_on):
        raise ValueError(
          "Prep firmware requires all channels to use the same LLD mode category. "
          "Cannot mix LLDMode.OFF with CAPACITIVE/PRESSURE/DUAL in one call. "
          "Split into separate calls for channels with different LLD modes."
        )
      return all(lld_on)
    return use_lld or (lld is not None)

  @staticmethod
  def _default_lld_params(
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

  @staticmethod
  def _lld_for_well(
    effective_lld: bool,
    lld: Optional[PrepCmd.LldParameters],
    top_of_well_z: float,
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

  # ---------------------------------------------------------------------------
  # Aspirate: resolve, assemble, send
  # ---------------------------------------------------------------------------

  def _resolve_aspirate_channels(
    self,
    ops: List[SingleChannelAspiration],
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
    assert len(ops) == len(use_channels)
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )

    n = len(ops)
    hlcs: List[Optional[HamiltonLiquidClass]]
    if hamilton_liquid_classes is not None:
      if len(hamilton_liquid_classes) != n:
        raise ValueError(
          f"hamilton_liquid_classes length must match len(ops): {len(hamilton_liquid_classes)} != {n}"
        )
      hlcs = list(hamilton_liquid_classes)
    else:
      hlcs = [
        get_star_liquid_class(
          tip_volume=op.tip.maximal_volume,
          is_core=False,
          is_tip=True,
          has_filter=op.tip.has_filter,
          liquid=Liquid.WATER,
          jet=False,
          blow_out=False,
        )
        for op in ops
      ]
    disable_volume_correction = (
      disable_volume_correction if disable_volume_correction is not None else [False] * n
    )
    if len(disable_volume_correction) != n:
      raise ValueError(
        f"disable_volume_correction length must match len(ops): {len(disable_volume_correction)} != {n}"
      )
    ch_to_idx = {ch: i for i, ch in enumerate(use_channels)}

    default_settling = [hlc.aspiration_settling_time if hlc is not None else 1.0 for hlc in hlcs]
    default_transport_air = [
      hlc.aspiration_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs
    ]
    default_z_exit_speed = [hlc.aspiration_swap_speed if hlc is not None else 10.0 for hlc in hlcs]
    default_prewet = [
      hlc.aspiration_over_aspirate_volume if hlc is not None else 0.0 for hlc in hlcs
    ]
    settling_time = fill_in_defaults(settling_time, default_settling)
    transport_air_volume = fill_in_defaults(transport_air_volume, default_transport_air)
    z_liquid_exit_speed = fill_in_defaults(z_liquid_exit_speed, default_z_exit_speed)
    prewet_volume = fill_in_defaults(prewet_volume, default_prewet)

    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_volume_correction)
    ]
    flow_rates = [
      op.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 100.0)
      for op, hlc in zip(ops, hlcs)
    ]
    blowout_volumes = [
      op.blow_out_air_volume or (hlc.aspiration_blow_out_volume if hlc is not None else 0.0)
      for op, hlc in zip(ops, hlcs)
    ]

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}

    well_geometry = [_absolute_z_from_well(op) for op in ops]
    default_z_minimum = [g[0] for g in well_geometry]
    default_z_fluid = [g[1] for g in well_geometry]
    default_z_air = [g[3] for g in well_geometry]
    raw_traverse = self._resolve_traverse_height(None)
    default_z_final = [
      raw_traverse - (op.tip.total_tip_length - op.tip.fitting_depth) for op in ops
    ]
    default_z_bso = [2.0] * n
    z_minimum = fill_in_defaults(z_minimum, default_z_minimum)
    z_fluid = fill_in_defaults(z_fluid, default_z_fluid)
    z_air = fill_in_defaults(z_air, default_z_air)
    z_final = fill_in_defaults(z_final, default_z_final)
    z_bottom_search_offset = fill_in_defaults(z_bottom_search_offset, default_z_bso)

    ch_segments: dict[int, list[PrepCmd.SegmentDescriptor]] = {}
    for i, ch in enumerate(use_channels):
      if container_segments is not None and i < len(container_segments):
        ch_segments[ch] = container_segments[i]
      elif auto_container_geometry:
        ch_segments[ch] = _build_container_segments(indexed_ops[ch].resource)
      else:
        ch_segments[ch] = []

    lld_defaults = self._default_lld_params(effective_lld, p_lld, c_lld)
    _tadm = tadm or PrepCmd.TadmParameters.default()

    kits: list[_AspirateChannelKit] = []
    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      idx = ch_to_idx[ch]
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
      radius = _effective_radius(op.resource)

      kits.append(
        _AspirateChannelKit(
          channel=_CHANNEL_INDEX[ch],
          aspirate=PrepCmd.AspirateParameters.for_op(
            loc,
            op,
            prewet_volume=prewet_volume[idx],
            blowout_volume=blowout_volumes[idx],
          ),
          common=PrepCmd.CommonParameters.for_op(
            volumes[idx],
            radius,
            flow_rate=flow_rates[idx],
            z_minimum=z_minimum[idx],
            z_final=z_final[idx],
            z_liquid_exit_speed=z_liquid_exit_speed[idx],
            transport_air_volume=transport_air_volume[idx],
            settling_time=settling_time[idx],
          ),
          segments=ch_segments[ch],
          no_lld=PrepCmd.NoLldParameters.for_fixed_z(
            z_fluid[idx],
            z_air[idx],
            z_bottom_search_offset=z_bottom_search_offset[idx],
          ),
          lld=self._lld_for_well(effective_lld, lld, well_geometry[idx][2]),
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
    kit: _AspirateChannelKit,
    effective_lld: bool,
    is_tadm: bool,
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
    self,
    kit: _AspirateChannelKit,
    effective_lld: bool,
    is_tadm: bool,
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

  async def _send_aspirate(
    self,
    kits: list[_AspirateChannelKit],
    effective_lld: bool,
    is_tadm: bool,
    use_v2: bool,
    read_timeout: Optional[float] = None,
  ) -> None:
    """Assemble the correct param types and send the aspirate command."""
    dest = await self._require("pipettor")
    rt = read_timeout if effective_lld else None

    if effective_lld and is_tadm:
      if use_v2:
        params_lt2 = [self._assemble_aspirate_v2(k, True, True) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepAspirateWithLldTadmV2(dest=dest, aspirate_parameters=params_lt2),  # type: ignore[arg-type]
          read_timeout=rt,
        )
      else:
        params_lt1 = [self._assemble_aspirate_v1(k, True, True) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepAspirateWithLldTadm(dest=dest, aspirate_parameters=params_lt1),  # type: ignore[arg-type]
          read_timeout=rt,
        )
    elif effective_lld:
      if use_v2:
        params_lm2 = [self._assemble_aspirate_v2(k, True, False) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepAspirateWithLldV2(dest=dest, aspirate_parameters=params_lm2),  # type: ignore[arg-type]
          read_timeout=rt,
        )
      else:
        params_lm1 = [self._assemble_aspirate_v1(k, True, False) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepAspirateWithLld(dest=dest, aspirate_parameters=params_lm1),  # type: ignore[arg-type]
          read_timeout=rt,
        )
    elif is_tadm:
      if use_v2:
        params_nt2 = [self._assemble_aspirate_v2(k, False, True) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepAspirateTadmV2(dest=dest, aspirate_parameters=params_nt2),  # type: ignore[arg-type]
        )
      else:
        params_nt1 = [self._assemble_aspirate_v1(k, False, True) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepAspirateTadm(dest=dest, aspirate_parameters=params_nt1),  # type: ignore[arg-type]
        )
    else:
      if use_v2:
        params_nm2 = [self._assemble_aspirate_v2(k, False, False) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepAspirateNoLldMonitoringV2(dest=dest, aspirate_parameters=params_nm2),  # type: ignore[arg-type]
        )
      else:
        params_nm1 = [self._assemble_aspirate_v1(k, False, False) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepAspirateNoLldMonitoring(dest=dest, aspirate_parameters=params_nm1),  # type: ignore[arg-type]
        )

  # ---------------------------------------------------------------------------
  # Dispense: resolve, assemble, send
  # ---------------------------------------------------------------------------

  def _resolve_dispense_channels(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    effective_lld: bool,
    *,
    final_z: Optional[List[float]] = None,
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
    assert len(ops) == len(use_channels)
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )

    n = len(ops)
    hlcs: List[Optional[HamiltonLiquidClass]]
    if hamilton_liquid_classes is not None:
      if len(hamilton_liquid_classes) != n:
        raise ValueError(
          f"hamilton_liquid_classes length must match len(ops): {len(hamilton_liquid_classes)} != {n}"
        )
      hlcs = list(hamilton_liquid_classes)
    else:
      hlcs = [
        get_star_liquid_class(
          tip_volume=op.tip.maximal_volume,
          is_core=False,
          is_tip=True,
          has_filter=op.tip.has_filter,
          liquid=Liquid.WATER,
          jet=False,
          blow_out=False,
        )
        for op in ops
      ]
    disable_volume_correction = (
      disable_volume_correction if disable_volume_correction is not None else [False] * n
    )
    if len(disable_volume_correction) != n:
      raise ValueError(
        f"disable_volume_correction length must match len(ops): {len(disable_volume_correction)} != {n}"
      )
    ch_to_idx = {ch: i for i, ch in enumerate(use_channels)}

    default_settling = [hlc.dispense_settling_time if hlc is not None else 0.0 for hlc in hlcs]
    default_transport_air = [
      hlc.dispense_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs
    ]
    default_z_exit_speed = [hlc.dispense_swap_speed if hlc is not None else 10.0 for hlc in hlcs]
    default_stop_back = [hlc.dispense_stop_back_volume if hlc is not None else 0.0 for hlc in hlcs]
    default_cutoff = [hlc.dispense_stop_flow_rate if hlc is not None else 100.0 for hlc in hlcs]
    settling_time = fill_in_defaults(settling_time, default_settling)
    transport_air_volume = fill_in_defaults(transport_air_volume, default_transport_air)
    z_liquid_exit_speed = fill_in_defaults(z_liquid_exit_speed, default_z_exit_speed)
    stop_back_volume = fill_in_defaults(stop_back_volume, default_stop_back)
    cutoff_speed = fill_in_defaults(cutoff_speed, default_cutoff)

    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_volume_correction)
    ]
    flow_rates = [
      op.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 100.0)
      for op, hlc in zip(ops, hlcs)
    ]

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}

    well_geometry = [_absolute_z_from_well(op) for op in ops]
    default_z_minimum = [g[0] for g in well_geometry]
    default_z_fluid = [g[1] for g in well_geometry]
    default_z_air = [g[3] for g in well_geometry]
    raw_traverse = self._resolve_traverse_height(None)
    default_final_z = [
      raw_traverse - (op.tip.total_tip_length - op.tip.fitting_depth) for op in ops
    ]
    default_z_bso = [2.0] * n
    z_minimum = fill_in_defaults(z_minimum, default_z_minimum)
    z_fluid = fill_in_defaults(z_fluid, default_z_fluid)
    z_air = fill_in_defaults(z_air, default_z_air)
    final_z = fill_in_defaults(final_z, default_final_z)
    z_bottom_search_offset = fill_in_defaults(z_bottom_search_offset, default_z_bso)

    ch_segments: dict[int, list[PrepCmd.SegmentDescriptor]] = {}
    for i, ch in enumerate(use_channels):
      if container_segments is not None and i < len(container_segments):
        ch_segments[ch] = container_segments[i]
      elif auto_container_geometry:
        ch_segments[ch] = _build_container_segments(indexed_ops[ch].resource)
      else:
        ch_segments[ch] = []

    lld_defaults = self._default_lld_params(effective_lld, c_lld=c_lld)

    kits: list[_DispenseChannelKit] = []
    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      idx = ch_to_idx[ch]
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
      radius = _effective_radius(op.resource)

      kits.append(
        _DispenseChannelKit(
          channel=_CHANNEL_INDEX[ch],
          dispense=PrepCmd.DispenseParameters.for_op(
            loc,
            stop_back_volume=stop_back_volume[idx],
            cutoff_speed=cutoff_speed[idx],
          ),
          common=PrepCmd.CommonParameters.for_op(
            volumes[idx],
            radius,
            flow_rate=flow_rates[idx],
            z_minimum=z_minimum[idx],
            z_final=final_z[idx],
            z_liquid_exit_speed=z_liquid_exit_speed[idx],
            transport_air_volume=transport_air_volume[idx],
            settling_time=settling_time[idx],
          ),
          segments=ch_segments[ch],
          no_lld=PrepCmd.NoLldParameters.for_fixed_z(
            z_fluid[idx],
            z_air[idx],
            z_bottom_search_offset=z_bottom_search_offset[idx],
          ),
          lld=self._lld_for_well(effective_lld, lld, well_geometry[idx][2]),
          c_lld=lld_defaults.c_lld,
          tadm=PrepCmd.TadmParameters.default(),
          mix=PrepCmd.MixParameters.default(),
          adc=PrepCmd.AdcParameters.default(),
        )
      )
    return kits

  @staticmethod
  def _assemble_dispense_v2(
    kit: _DispenseChannelKit,
    effective_lld: bool,
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
    self,
    kit: _DispenseChannelKit,
    effective_lld: bool,
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

  async def _send_dispense(
    self,
    kits: list[_DispenseChannelKit],
    effective_lld: bool,
    use_v2: bool,
  ) -> None:
    """Assemble the correct param types and send the dispense command."""
    dest = await self._require("pipettor")

    if effective_lld:
      if use_v2:
        params_l2 = [self._assemble_dispense_v2(k, True) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepDispenseWithLldV2(dest=dest, dispense_parameters=params_l2),  # type: ignore[arg-type]
        )
      else:
        params_l1 = [self._assemble_dispense_v1(k, True) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepDispenseWithLld(dest=dest, dispense_parameters=params_l1),  # type: ignore[arg-type]
        )
    else:
      if use_v2:
        params_n2 = [self._assemble_dispense_v2(k, False) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepDispenseNoLldV2(dest=dest, dispense_parameters=params_n2),  # type: ignore[arg-type]
        )
      else:
        params_n1 = [self._assemble_dispense_v1(k, False) for k in kits]
        await self.client.send_command(
          PrepCmd.PrepDispenseNoLld(dest=dest, dispense_parameters=params_n1),  # type: ignore[arg-type]
        )

  # ---------------------------------------------------------------------------
  # Public aspirate / dispense orchestrators
  # ---------------------------------------------------------------------------

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    z_final: Optional[List[float]] = None,
    z_fluid: Optional[List[float]] = None,
    z_air: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    z_liquid_exit_speed: Optional[List[float]] = None,
    prewet_volume: Optional[List[float]] = None,
    z_minimum: Optional[List[float]] = None,
    z_bottom_search_offset: Optional[List[float]] = None,
    monitoring_mode: PrepCmd.MonitoringMode = PrepCmd.MonitoringMode.MONITORING,
    lld_mode: Optional[List[LLDMode]] = None,
    use_lld: bool = False,
    lld: Optional[PrepCmd.LldParameters] = None,
    p_lld: Optional[PrepCmd.PLldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    tadm: Optional[PrepCmd.TadmParameters] = None,
    container_segments: Optional[
      List[List[PrepCmd.SegmentDescriptor]]
    ] = None,  # TODO: Doesn't work with No LLD
    auto_container_geometry: bool = False,
    hamilton_liquid_classes: Optional[List[HamiltonLiquidClass]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
    read_timeout: Optional[float] = None,
    command_version: Optional[Literal["v1", "v2"]] = None,
  ):
    """Aspirate, dispatching to the appropriate command variant and version.

    Selects the command variant based on ``lld_mode`` (LLD on/off) and
    ``monitoring_mode`` (Monitoring vs TADM).  Z/geometry parameters (z_final,
    z_fluid, z_air, z_minimum, z_bottom_search_offset): None = use defaults for all
    channels (derived from well geometry, STAR-aligned). Otherwise pass a list of
    length len(ops) with one value per channel (no None in list). For per-channel
    defaults, build the list from liquid class or constants.

    Liquid-class-derived parameters (settling_time, transport_air_volume,
    z_liquid_exit_speed, prewet_volume): None = use defaults for all channels (HLC
    or fallback per channel). Otherwise pass a list of length len(ops) with one
    value per channel (no None in list).

    Args:
      z_final: Z after the move (retract height) per channel. None = defaults for all; else list of len(ops), no None in list.
      z_fluid: Liquid surface Z when not using LLD, per channel. None = defaults for all; else list of len(ops).
      z_air: Z in air (above liquid), per channel. None = defaults for all; else list of len(ops).
      settling_time: Settling time (s) per channel. None = defaults for all; else list of len(ops).
      transport_air_volume: Transport air volume (µL) per channel. None = defaults for all; else list of len(ops).
      z_liquid_exit_speed: Z speed on leaving liquid (mm/s) per channel. None = defaults for all; else list of len(ops).
      prewet_volume: Pre-wet volume (µL) per channel. None = defaults for all; else list of len(ops).
      z_minimum: Minimum Z (well floor) per channel. None = defaults for all; else list of len(ops).
      z_bottom_search_offset: Bottom search offset (mm) per channel. None = defaults for all; else list of len(ops).
      monitoring_mode: Select TADM or Monitoring (default: Monitoring).
      lld_mode: Per-channel LLD mode list. Any non-OFF mode activates the LLD
        command variant. All channels must use the same category (all LLD or all OFF).
      use_lld: Enable LLD aspirate variant. Deprecated — use ``lld_mode`` instead.
      lld: LLD seek parameters. When None and LLD active, built from labware geometry.
      p_lld: Pressure LLD parameters (LLD variants only).
      c_lld: Capacitive LLD parameters (LLD variants only).
      tadm: TADM parameters (TADM variants only). Firmware defaults when None.
      container_segments: Per-channel SegmentDescriptor lists for liquid following.
      auto_container_geometry: Build container segments from well geometry.
      hamilton_liquid_classes: Per-op Hamilton liquid classes. None = auto from tip/liquid.
      disable_volume_correction: Per-op flag to skip volume correction.
      read_timeout: Override read timeout (seconds) for this command. When None,
        auto-calculated from LLD seek distance/speed + 5s buffer.
      command_version: Override per-call: "v1" (cmd 1-6) or "v2" (cmd 38-43).
        None uses the version determined at setup. V1 converts container_description
        frustum segments into the cone model in CommonParameters.

    Example::

      await backend.aspirate(ops, [0], z_final=[95.0], settling_time=[2.0])
      await backend.aspirate(ops, [0], lld_mode=[PrepBackend.LLDMode.CAPACITIVE])
      await backend.aspirate(ops, [0], monitoring_mode=PrepCmd.MonitoringMode.TADM)
      await backend.aspirate(ops, [0], command_version="v1")
    """
    effective_lld = self._resolve_effective_lld(lld_mode, use_lld, lld, len(ops))
    is_tadm = monitoring_mode == PrepCmd.MonitoringMode.TADM
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

    # Auto-calculate LLD read timeout from seek distance/speed.
    lld_read_timeout = read_timeout
    if lld_read_timeout is None and effective_lld and kits:
      kit0_lld = kits[0].lld
      if kit0_lld.channel_speed > 0:
        min_z_min = min(k.common.z_minimum for k in kits)
        seek_distance = kit0_lld.search_start_position - min_z_min
        if seek_distance > 0:
          lld_read_timeout = seek_distance / kit0_lld.channel_speed + 5.0

    await self._send_aspirate(kits, effective_lld, is_tadm, use_v2, lld_read_timeout)

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    final_z: Optional[List[float]] = None,
    z_fluid: Optional[List[float]] = None,
    z_air: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    z_liquid_exit_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    cutoff_speed: Optional[List[float]] = None,
    z_minimum: Optional[List[float]] = None,
    z_bottom_search_offset: Optional[List[float]] = None,
    lld_mode: Optional[List[LLDMode]] = None,
    use_lld: bool = False,
    lld: Optional[PrepCmd.LldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    container_segments: Optional[List[List[PrepCmd.SegmentDescriptor]]] = None,
    auto_container_geometry: bool = False,  # TODO: Doesn't work with no LLD
    hamilton_liquid_classes: Optional[List[HamiltonLiquidClass]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
    command_version: Optional[Literal["v1", "v2"]] = None,
  ):
    """Dispense, dispatching to the appropriate command variant and version.

    Z/geometry parameters (final_z, z_fluid, z_air, z_minimum, z_bottom_search_offset):
    None = use defaults for all channels (derived from well geometry, STAR-aligned).
    Otherwise pass a list of length len(ops) with one value per channel (no None in list).
    For per-channel defaults, build the list from liquid class or constants.

    Liquid-class-derived parameters (settling_time, transport_air_volume,
    z_liquid_exit_speed, stop_back_volume, cutoff_speed): None = use defaults for all
    channels (HLC or fallback per channel). Otherwise pass a list of length len(ops)
    with one value per channel (no None in list).

    Args:
      final_z: Z after the move per channel. None = defaults for all; else list of len(ops), no None in list.
      z_fluid: Liquid surface Z when not using LLD, per channel. None = defaults for all; else list of len(ops).
      z_air: Z in air (above liquid), per channel. None = defaults for all; else list of len(ops).
      settling_time: Settling time (s) per channel. None = defaults for all; else list of len(ops).
      transport_air_volume: Transport air volume (µL) per channel. None = defaults for all; else list of len(ops).
      z_liquid_exit_speed: Z speed on leaving liquid (mm/s) per channel. None = defaults for all; else list of len(ops).
      stop_back_volume: Stop-back volume (µL) per channel. None = defaults for all; else list of len(ops).
      cutoff_speed: Cutoff/stop flow rate (µL/s) per channel. None = defaults for all; else list of len(ops).
      z_minimum: Minimum Z (well floor) per channel. None = defaults for all; else list of len(ops).
      z_bottom_search_offset: Bottom search offset (mm) per channel. None = defaults for all; else list of len(ops).
      lld_mode: Per-channel LLD mode list. Only CAPACITIVE or OFF supported for
        dispense (pressure LLD is physically impossible during dispense).
      use_lld: Enable LLD dispense variant. Deprecated — use ``lld_mode`` instead.
      lld: LLD seek parameters. When None and LLD active, built from labware geometry.
      c_lld: Capacitive LLD parameters (LLD variant only).
      container_segments: Per-channel SegmentDescriptor lists for liquid following.
      auto_container_geometry: Build container segments from well geometry.
      hamilton_liquid_classes: Per-op Hamilton liquid classes. None = auto from tip/liquid.
      disable_volume_correction: Per-op flag to skip volume correction.
      command_version: Override per-call: "v1" (cmd 5-6) or "v2" (cmd 42-43).
        None uses the version determined at setup. V1 converts container_description
        frustum segments into the cone model in CommonParameters.

    Example::

      await backend.dispense(ops, [0], final_z=[95.0], settling_time=[0.5])
      await backend.dispense(ops, [0], lld_mode=[PrepBackend.LLDMode.CAPACITIVE])
      await backend.dispense(ops, [0], command_version="v1")
    """
    _DISPENSE_ALLOWED_LLD = frozenset({self.LLDMode.CAPACITIVE})
    effective_lld = self._resolve_effective_lld(
      lld_mode,
      use_lld,
      lld,
      len(ops),
      allowed_modes=_DISPENSE_ALLOWED_LLD,
    )
    use_v2 = self._resolve_command_version(command_version)

    kits = self._resolve_dispense_channels(
      ops,
      use_channels,
      effective_lld,
      final_z=final_z,
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

    await self._send_dispense(kits, effective_lld, use_v2)

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("pick_up_tips96 is not supported on the Prep")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("drop_tips96 is not supported on the Prep")

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    raise NotImplementedError("aspirate96 is not supported on the Prep")

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    raise NotImplementedError("dispense96 is not supported on the Prep")

  async def pick_up_tool(
    self,
    tool_position_x: float,
    tool_position_z: float,
    front_channel_position_y: float,
    rear_channel_position_y: float,
    *,
    tool_seek: Optional[float] = None,
    tool_x_radius: float = 2.0,
    tool_y_radius: float = 2.0,
    tip_definition: Optional[PrepCmd.TipPickupParameters] = None,
  ) -> None:
    """Pick up tool from the given position (PrepCmd.PrepPickUpTool, cmd=15). Sets _gripper_tool_on and moves channels to safe Z."""
    if tool_seek is None:
      tool_seek = tool_position_z + 10.0
    if tip_definition is None:
      tip_definition = PrepCmd.CO_RE_GRIPPER_TIP_PICKUP_PARAMETERS
    await self.client.send_command(
      PrepCmd.PrepPickUpTool(
        dest=await self._require("pipettor"),
        tip_definition=tip_definition,
        tool_position_x=tool_position_x,
        tool_position_z=tool_position_z,
        front_channel_position_y=front_channel_position_y,
        rear_channel_position_y=rear_channel_position_y,
        tool_seek=tool_seek,
        tool_x_radius=tool_x_radius,
        tool_y_radius=tool_y_radius,
      )
    )
    self._gripper_tool_on = True
    await self.move_channels_to_safe_z()

  async def drop_tool(self, *, move_to_safe_z_first: bool = True) -> None:
    """Drop tool (PrepCmd.PrepDropTool, cmd=16). Optionally move channels to safe Z first. Clears _gripper_tool_on."""
    if move_to_safe_z_first:
      await self.move_channels_to_safe_z()
    await self.client.send_command(PrepCmd.PrepDropTool(dest=await self._require("pipettor")))
    self._gripper_tool_on = False

  async def release_plate(self) -> None:
    """Release plate / open gripper (PrepCmd.PrepReleasePlate, cmd=21). No parameters."""
    await self.client.send_command(PrepCmd.PrepReleasePlate(dest=await self._require("pipettor")))

  async def pick_up_resource(
    self,
    pickup: ResourcePickup,
    *,
    clearance_y: float = 2.5,
    grip_speed_y: float = 5.0,
    squeeze_mm: float = 2.0,
  ):
    if self.deck is None:
      raise RuntimeError("deck not set")
    if pickup.direction != GripDirection.FRONT:
      raise NotImplementedError("PREP CORE gripper only supports GripDirection.FRONT")
    resource = pickup.resource
    center = resource.get_location_wrt(self.deck, "c", "c", "t") + pickup.offset
    grip_height = center.z - pickup.pickup_distance_from_top
    # plate_top_center = literal top center of plate (x, y, z_top); grip_height is separate.
    plate_top_center = PrepCmd.XYZCoord(
      default_values=False,
      x_position=center.x,
      y_position=center.y,
      z_position=center.z,
    )
    # Grip distance = how far the grippers close from open (travel). Open = labware_y + clearance_y, final = labware_y - squeeze_mm → close by clearance_y + squeeze_mm.
    grip_distance = clearance_y + squeeze_mm
    plate_dims = PrepCmd.PlateDimensions(
      default_values=False,
      length=resource.get_absolute_size_x(),
      width=resource.get_absolute_size_y(),
      height=resource.get_absolute_size_z(),
    )
    if not self._gripper_tool_on:
      mount = self.deck.get_resource("core_grippers")
      if not isinstance(mount, HamiltonCoreGrippers):
        raise TypeError(
          "deck must have a resource named 'core_grippers' of type HamiltonCoreGrippers"
        )
      loc = mount.get_location_wrt(self.deck)
      await self.pick_up_tool(
        tool_position_x=loc.x,
        tool_position_z=loc.z,
        front_channel_position_y=loc.y + mount.front_channel_y_center,
        rear_channel_position_y=loc.y + mount.back_channel_y_center,
        tool_seek=loc.z + 10.0,
      )
    await self.client.send_command(
      PrepCmd.PrepPickUpPlate(
        dest=await self._require("pipettor"),
        plate_top_center=plate_top_center,
        plate=plate_dims,
        clearance_y=clearance_y,
        grip_speed_y=grip_speed_y,
        grip_distance=grip_distance,
        grip_height=grip_height,
      )
    )

  async def move_picked_up_resource(self, move: ResourceMove):
    if self.deck is None:
      raise RuntimeError("deck not set")
    center = (
      move.location
      + move.resource.get_anchor("c", "c", "t")
      - Coordinate(z=move.pickup_distance_from_top)
      + move.offset
    )
    plate_top_center = PrepCmd.XYZCoord(
      default_values=False,
      x_position=center.x,
      y_position=center.y,
      z_position=center.z,
    )
    await self.client.send_command(
      PrepCmd.PrepMovePlate(
        dest=await self._require("pipettor"),
        plate_top_center=plate_top_center,
        acceleration_scale_x=1,
      )
    )

  async def drop_resource(
    self,
    drop: ResourceDrop,
    *,
    return_gripper: bool = True,
    clearance_y: float = 3.0,
  ):
    if self.deck is None:
      raise RuntimeError("deck not set")
    resource = drop.resource
    dest_center = drop.destination + resource.get_anchor("c", "c", "t") + drop.offset
    place_z = drop.destination.z + resource.get_absolute_size_z() - drop.pickup_distance_from_top
    plate_top_center = PrepCmd.XYZCoord(
      default_values=False,
      x_position=dest_center.x,
      y_position=dest_center.y,
      z_position=place_z,
    )
    await self.client.send_command(
      PrepCmd.PrepDropPlate(
        dest=await self._require("pipettor"),
        plate_top_center=plate_top_center,
        clearance_y=clearance_y,
        acceleration_scale_x=1,
      )
    )
    if return_gripper:
      await self.drop_tool()

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    """Check if the tip can be picked up by the specified channel.

    Uses the same logic as Nimbus/STAR: only Hamilton tips, no XL tips,
    and channel index must be valid.
    """
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    if self._num_channels is not None and channel_idx >= self._num_channels:
      return False
    return True

  # ---------------------------------------------------------------------------
  # Firmware version queries
  # ---------------------------------------------------------------------------

  @staticmethod
  def _decode_firmware_string(raw: Optional[tuple]) -> Optional[str]:
    """Decode a string from a raw HOI response.

    Hamilton string wire format: 0x0F + type_byte + u16 length + chars.
    type_byte is 0x00 (plain) or 0x01 (with null terminator); both are handled.
    """
    if raw is None:
      return None
    data: bytes = raw[0]
    i = 0
    while i < len(data) - 3:
      if data[i] == 0x0F and data[i + 1] in (0x00, 0x01):
        slen = int.from_bytes(data[i + 2 : i + 4], "little")
        if slen > 0 and i + 4 + slen <= len(data):
          return data[i + 4 : i + 4 + slen].decode("utf-8", errors="replace").rstrip("\x00")
      i += 1
    return None

  async def _query_firmware_string(
    self, addr: Address, cmd_id: int, iface_id: int = 3
  ) -> Optional[str]:
    """Send a status query and decode the string response."""
    Cmd = type(
      "_FWQuery",
      (PrepCmd._PrepStatusQuery,),
      {"command_id": cmd_id, "interface_id": iface_id, "__annotations__": {"dest": Address}},
    )
    raw: Optional[tuple] = await self.client.send_command(
      Cmd(dest=addr), return_raw=True, raise_on_error=False
    )
    return self._decode_firmware_string(raw)

  async def request_firmware_version(self) -> Optional[str]:
    """Request the instrument controller firmware version string.

    Returns a string like "MLPrep Runtime V1.2.2.444 99020-02 Rev G",
    or None if MLPrepCpu was not discovered.

    Analogous to STARBackend.request_firmware_version().
    """
    if self._mlprep_cpu_addr is None:
      return None
    return await self._query_firmware_string(self._mlprep_cpu_addr, cmd_id=8)

  async def request_device_serial_number(self) -> Optional[str]:
    """Request the instrument serial number.

    Analogous to STARBackend.request_device_serial_number().
    """
    if self._mlprep_cpu_addr is None:
      return None
    return await self._query_firmware_string(self._mlprep_cpu_addr, cmd_id=9)

  async def request_bootloader_version(self) -> Optional[str]:
    """Request the instrument bootloader version string."""
    if self._mlprep_cpu_addr is None:
      return None
    return await self._query_firmware_string(self._mlprep_cpu_addr, cmd_id=2, iface_id=2)

  async def request_pip_channel_version(self, channel: int) -> Optional[str]:
    """Request the firmware version string for a pipettor channel.

    Args:
      channel: Channel index (0=rearmost).

    Analogous to STARBackend.request_pip_channel_version().
    """
    if channel >= len(self._channel_node_info_addrs):
      return None
    return await self._query_firmware_string(
      self._channel_node_info_addrs[channel], cmd_id=8, iface_id=1
    )

  async def request_pip_channel_serial_number(self, channel: int) -> Optional[str]:
    """Request the serial number for a pipettor channel.

    Args:
      channel: Channel index (0=rearmost).
    """
    if channel >= len(self._channel_node_info_addrs):
      return None
    return await self._query_firmware_string(
      self._channel_node_info_addrs[channel], cmd_id=9, iface_id=1
    )

  async def request_module_version(self) -> Optional[str]:
    """Request the pipettor module version string (from PipettorRoot.ModuleInformation)."""
    if self._module_info_addr is None:
      return None
    return await self._query_firmware_string(self._module_info_addr, cmd_id=8)

  async def request_module_part_number(self) -> Optional[str]:
    """Request the firmware part number (from PipettorRoot.ModuleInformation)."""
    if self._module_info_addr is None:
      return None
    return await self._query_firmware_string(self._module_info_addr, cmd_id=5)

  # ---------------------------------------------------------------------------
  # Channel position queries
  # ---------------------------------------------------------------------------

  async def request_channel_bounds(self) -> list[dict]:
    """Request per-channel movement bounds from the firmware.

    Queries PipettorService.GetChannelBounds (cmd=10). Returns one entry per
    channel, ordered by channel index. Each entry is a dict with keys:
    x_min, x_max, y_min, y_max, z_min, z_max (all in mm).

    These are the firmware-enforced limits — positions outside these ranges
    will be rejected with 0x0F04 (X), 0x0F05 (Y), or 0x0F06 (Z).
    Z bounds are for empty channels; with a tip attached the effective Z
    minimum is higher.

    Returns:
      List of dicts, one per channel. Each dict has keys:
      x_min, x_max, y_min, y_max, z_min, z_max (all in mm).
    """
    import struct as _struct

    # GetChannelBounds is on PipettorService (child of Pipettor), not MLPrepService
    try:
      await self.client.interfaces["MLPrepRoot.PipettorRoot.Pipettor.PipettorService"].resolve()
      pip_svc = self.client.interfaces["MLPrepRoot.PipettorRoot.Pipettor.PipettorService"].address
    except KeyError:
      return []

    raw = await self.client.send_command(
      PrepCmd.PrepGetChannelBounds(dest=pip_svc),
      return_raw=True,
      raise_on_error=False,
    )
    if raw is None:
      return []

    # Parse per-channel bounds from raw response.
    # Each channel block: channel_enum (u32 at 0x20), then 6× f32 (at 0x28):
    # x_min, x_max, y_min, y_max, z_min, z_max
    data = raw[0]
    _CHANNEL_ENUM_TO_IDX = {v: k for k, v in _CHANNEL_INDEX.items()}
    indexed = []

    i = 0
    while i < len(data) - 20:
      if data[i] == 0x20 and data[i + 1] == 0x00 and data[i + 2] == 0x04:
        ch_val = _struct.unpack_from("<I", data, i + 4)[0]
        ch_idx = _CHANNEL_ENUM_TO_IDX.get(ch_val)

        j = i + 8
        floats: list[float] = []
        while len(floats) < 6 and j < len(data) - 7:
          if data[j] == 0x28 and data[j + 1] == 0x00:
            floats.append(_struct.unpack_from("<f", data, j + 4)[0])
            j += 8
          else:
            j += 1

        if ch_idx is not None and len(floats) == 6:
          indexed.append(
            (
              ch_idx,
              {
                "x_min": floats[0],
                "x_max": floats[1],
                "y_min": floats[2],
                "y_max": floats[3],
                "z_min": floats[4],
                "z_max": floats[5],
              },
            )
          )
        i = j
      else:
        i += 1

    indexed.sort(key=lambda pair: pair[0])
    return [bounds for _, bounds in indexed]

  async def request_channel_positions(self) -> list[Coordinate]:
    """Request the current XYZ positions of all pipettor channels.

    Queries Pipettor.GetPositions (cmd=25). Returns one Coordinate per channel,
    ordered by channel index (0=rearmost).

    Uses the typed PrepGetPositions command with ChannelXYZPositionParameters
    response struct for reliable parsing across firmware versions.

    Returns:
      List of Coordinate, one per channel.
    """
    resp = await self.client.send_command(
      PrepCmd.PrepGetPositions(dest=await self._require("pipettor")),
      raise_on_error=False,
    )
    if resp is None or not resp.positions:
      return []

    _CHANNEL_ENUM_TO_IDX = {v: k for k, v in _CHANNEL_INDEX.items()}
    indexed = []
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
    return positions[channel_idx].x

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
    return positions[channel_idx].y

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
    return positions[channel_idx].z

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
      Cmd = type(
        "_GetTipDefHeld",
        (PrepCmd._PrepStatusQuery,),
        {"command_id": 13, "__annotations__": {"dest": Address}},
      )
      raw = await self.client.send_command(
        Cmd(dest=await self._require("pipettor")),
        return_raw=True,
        raise_on_error=False,
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

    Channel addresses are discovered from the object tree at setup time
    (stored in ``_channel_sleeve_sensor_addrs``), so this works regardless of the
    node IDs assigned by the firmware on a given instrument.

    Returns:
      List of bools, one per channel (index 0=rearmost). True if tip detected.
    """
    import struct as _struct

    if not self._channel_sleeve_sensor_addrs:
      raise RuntimeError("No channel sleeve sensor addresses discovered. Call setup() first.")

    results: list[bool] = []
    for addr in self._channel_sleeve_sensor_addrs:
      Cmd = type(
        "_GetTipPresent",
        (PrepCmd._PrepStatusQuery,),
        {"command_id": 15, "__annotations__": {"dest": Address}},
      )
      raw = await self.client.send_command(
        Cmd(dest=addr),
        return_raw=True,
        raise_on_error=False,
      )
      if raw is None or len(raw[0]) < 8:
        results.append(False)
      else:
        val = _struct.unpack_from("<I", raw[0], 4)[0]
        results.append(bool(val))

    return results

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
      "clld_probe_x_position_using_channel is not yet implemented for PrepBackend."
    )

  async def clld_probe_y_position_using_channel(self, *args, **kwargs):
    """Probe Y position using capacitive LLD. Not yet implemented for the Prep.

    TODO: Investigate ChannelCoordinator [1:19] YSeekLldPosition(seekParameters)
    which takes a YLLDSeekParameters struct and returns SeekResultParameters.
    Also Channel [1:11] LeakCheck has ySeekDistance/yPreloadDistance params
    which suggest Y-axis seeking capability.
    """
    raise NotImplementedError(
      "clld_probe_y_position_using_channel is not yet implemented for PrepBackend."
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
    - cLLD DOES work through the aspirate path (aspirate with use_lld=True and
      default_values=False on both LldParameters and CLldParameters).
    - Standalone ZSeekLldPosition is rejected with 0x0F06 when Z params are out of range.
    - The aspirate-based approach is a workaround, not a proper standalone probe.

    Also investigate ZAxis-level alternatives:
    - ZAxis.SeekCapacitiveLld [1:12] (returns 0x0207 when called directly)
    - ZAxis.SeekCapacitiveLldTip [1:13] (returns 0x0207 when called directly)
    - ZAxis.LiquidStatus [1:16] for reading last detection results
    - PipettorService.MeasureLldFrequency [1:6] for sensor health checks
    """
    raise NotImplementedError(
      "clld_probe_z_height_using_channel is not yet implemented for PrepBackend."
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
      "ztouch_probe_z_height_using_channel is not yet implemented for PrepBackend."
    )

  # ---------------------------------------------------------------------------
  # Object tree inspection
  # ---------------------------------------------------------------------------

  async def print_firmware_tree(self) -> None:
    """Walk the full firmware object tree and print a formatted tree representation.

    Each object shows its name, address, firmware version, method count, and child count.
    Useful for diagnostics and understanding the instrument's firmware topology.
    """
    from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import HamiltonIntrospection

    intro = HamiltonIntrospection(self.client)
    root_addrs = self.client._registry.get_root_addresses()
    if not root_addrs:
      print("(no root objects discovered)")
      return

    lines: list[str] = []

    async def walk(addr, prefix="", is_last=True):
      try:
        obj = await intro.get_object(addr)
      except Exception:
        lines.append(f"{prefix}{'└── ' if is_last else '├── '}? @ {addr} (failed to query)")
        return

      connector = "└── " if is_last else "├── "
      version_str = f", version={obj.version}" if obj.version else ""
      lines.append(
        f"{prefix}{connector}{obj.name} @ {addr} "
        f"(methods={obj.method_count}, children={obj.subobject_count}{version_str})"
      )

      child_prefix = prefix + ("    " if is_last else "│   ")
      children_found = []
      for i in range(obj.subobject_count):
        try:
          child_addr = await intro.get_subobject_address(addr, i)
          child_obj = await intro.get_object(child_addr)
          children_found.append((child_addr, child_obj))
        except Exception:
          continue

      for idx, (child_addr, _) in enumerate(children_found):
        await walk(child_addr, child_prefix, is_last=(idx == len(children_found) - 1))

    for root_idx, root_addr in enumerate(root_addrs):
      try:
        root_obj = await intro.get_object(root_addr)
      except Exception:
        lines.append(f"? @ {root_addr} (failed to query)")
        continue

      version_str = f", version={root_obj.version}" if root_obj.version else ""
      lines.append(
        f"{root_obj.name} @ {root_addr} "
        f"(methods={root_obj.method_count}, children={root_obj.subobject_count}{version_str})"
      )

      children_found = []
      for i in range(root_obj.subobject_count):
        try:
          child_addr = await intro.get_subobject_address(root_addr, i)
          child_obj = await intro.get_object(child_addr)
          children_found.append((child_addr, child_obj))
        except Exception:
          continue

      for idx, (child_addr, _) in enumerate(children_found):
        await walk(child_addr, "", is_last=(idx == len(children_found) - 1))

    print("\n".join(lines))

  # ---------------------------------------------------------------------------
  # MLPrep convenience methods
  # ---------------------------------------------------------------------------

  async def park(self) -> None:
    """Park the instrument."""
    await self.client.send_command(PrepCmd.PrepPark(dest=await self._require("mlprep")))

  async def spread(self) -> None:
    """Spread channels."""
    await self.client.send_command(PrepCmd.PrepSpread(dest=await self._require("mlprep")))

  async def method_begin(self, automatic_pause: bool = False) -> None:
    """Signal the start of a liquid-handling method."""
    await self.client.send_command(
      PrepCmd.PrepMethodBegin(
        dest=await self._require("mlprep"),
        automatic_pause=automatic_pause,
      )
    )

  async def method_end(self) -> None:
    """Signal the end of a liquid-handling method."""
    await self.client.send_command(PrepCmd.PrepMethodEnd(dest=await self._require("mlprep")))

  async def method_abort(self) -> None:
    """Abort the current method."""
    await self.client.send_command(PrepCmd.PrepMethodAbort(dest=await self._require("mlprep")))

  async def power_down_request(self) -> None:
    """Request power down (instrument will prepare for shutdown; use cancel_power_down to abort)."""
    await self.client.send_command(PrepCmd.PrepPowerDownRequest(dest=await self._require("mlprep")))

  async def confirm_power_down(self) -> None:
    """Confirm power down (completes shutdown; only call when safe to power off)."""
    await self.client.send_command(PrepCmd.PrepConfirmPowerDown(dest=await self._require("mlprep")))

  async def cancel_power_down(self) -> None:
    """Cancel a pending power-down request."""
    await self.client.send_command(PrepCmd.PrepCancelPowerDown(dest=await self._require("mlprep")))

  async def get_deck_light(self) -> Tuple[int, int, int, int]:
    """Get the current deck LED colour (white, red, green, blue)."""
    result = await self.client.send_command(
      PrepCmd.PrepGetDeckLight(dest=await self._require("mlprep"))
    )
    if result is None:
      raise ValueError("No response from GetDeckLight.")
    return (result.white, result.red, result.green, result.blue)

  async def set_deck_light(self, white: int, red: int, green: int, blue: int) -> None:
    """Set the deck LED colour."""
    await self.client.send_command(
      PrepCmd.PrepSetDeckLight(
        dest=await self._require("mlprep"),
        white=white,
        red=red,
        green=green,
        blue=blue,
      )
    )

  async def disco_mode(self) -> None:
    """Easter egg: cycle deck lights then restore previous state."""
    white, red, green, blue = await self.get_deck_light()
    try:
      for _ in range(69):
        await self.set_deck_light(
          white=random.randint(1, 255),
          red=random.randint(1, 255),
          green=random.randint(1, 255),
          blue=random.randint(1, 255),
        )
        await asyncio.sleep(0.1)
    finally:
      await self.set_deck_light(white=white, red=red, green=green, blue=blue)

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
    await self.client.send_command(
      PrepCmd.PrepMoveZUpToSafe(
        dest=await self._require("pipettor"),
        channels=channel_enums,
      )
    )

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

    axis_parameters: List[PrepCmd.ChannelYZMoveParameters] = []
    for i, ch in enumerate(channels):
      y_i = y if isinstance(y, (int, float)) else y[i]
      z_i = z if isinstance(z, (int, float)) else z[i]
      axis_parameters.append(
        PrepCmd.ChannelYZMoveParameters(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          y_position=y_i,
          z_position=z_i,
        )
      )
    move_parameters = PrepCmd.GantryMoveXYZParameters(
      default_values=False,
      gantry_x_position=x,
      axis_parameters=axis_parameters,
    )

    if via_lane:
      await self.client.send_command(
        PrepCmd.PrepMoveToPositionViaLane(
          dest=await self._require("pipettor"),
          move_parameters=move_parameters,
        )
      )
    else:
      await self.client.send_command(
        PrepCmd.PrepMoveToPosition(
          dest=await self._require("pipettor"),
          move_parameters=move_parameters,
        )
      )

  async def stop(self) -> None:
    await self.client.stop()
    self.setup_finished = False

  def serialize(self) -> dict:
    return {**super().serialize(), **self.client.serialize()}


@dataclass(frozen=True)
class CalibrationCommandReport:
  """Structured report for one calibration command execution."""

  command: str
  result: object
  before: PrepCmd.CalibrationValues
  after: PrepCmd.CalibrationValues
  diff: PrepCmd.CalibrationValuesDiff

  @property
  def changed_fields_count(self) -> int:
    channel_changes = sum(
      len(cd.changes) for cd in self.diff.channel_diffs if cd.state == "changed"
    )
    return (
      len(self.diff.top_level_changes)
      + channel_changes
      + sum(1 for cd in self.diff.channel_diffs if cd.state in ("added", "removed"))
    )


class PrepCalibrationSession:
  """Context manager for stateful Prep calibration workflows."""

  def __init__(
    self,
    backend: PrepBackend,
    *,
    float_tol: float = 1e-6,
    report_after_command: bool = True,
    report_scope: Literal["related", "full"] = "related",
    session_read_timeout: Optional[float] = None,
  ) -> None:
    self.backend = backend
    self.float_tol = float_tol
    self.report_after_command = report_after_command
    self.report_scope = report_scope
    self.session_read_timeout = session_read_timeout

    self._started = False
    self._ended = False
    self._baseline: Optional[PrepCmd.CalibrationValues] = None
    self._last_snapshot: Optional[PrepCmd.CalibrationValues] = None
    self.history: List[CalibrationCommandReport] = []

    if report_scope not in ("related", "full"):
      raise ValueError(f"report_scope must be 'related' or 'full', got: {report_scope}")

  @property
  def baseline(self) -> PrepCmd.CalibrationValues:
    if self._baseline is None:
      raise RuntimeError("Session baseline unavailable. Enter the session first.")
    return self._baseline

  @property
  def last_snapshot(self) -> PrepCmd.CalibrationValues:
    if self._last_snapshot is None:
      raise RuntimeError("Session snapshot unavailable. Enter the session first.")
    return self._last_snapshot

  def _effective_timeout(self, read_timeout: Optional[float]) -> Optional[float]:
    return self.session_read_timeout if read_timeout is None else read_timeout

  def _ensure_started(self) -> None:
    if not self._started:
      raise RuntimeError("Calibration session is not started. Call `await session.start()` first.")
    if self._ended:
      raise RuntimeError("Calibration session is already ended.")

  def _select_snapshot_scope(
    self,
    values: PrepCmd.CalibrationValues,
    *,
    channel: Optional[PrepCmd.ChannelIndex] = None,
  ) -> PrepCmd.CalibrationValues:
    if self.report_scope == "full" or channel is None:
      return values
    channel_index = int(channel)
    return PrepCmd.CalibrationValues(
      independent_offset_x=values.independent_offset_x,
      mph_offset_x=values.mph_offset_x,
      channel_values=tuple(cv for cv in values.channel_values if cv.index == channel_index),
    )

  def _log_report(self, report: CalibrationCommandReport) -> None:
    if report.diff.has_changes:
      logger.info(
        "Calibration session %s changed %d field(s)",
        report.command,
        report.changed_fields_count,
      )
    else:
      logger.info("Calibration session %s produced no calibration changes", report.command)

    if logger.isEnabledFor(logging.DEBUG):
      logger.debug(
        "Calibration report diff for %s:\n%s",
        report.command,
        PrepCmd.format_calibration_diff(report.diff),
      )

  async def _get_calibration_values(
    self,
    *,
    read_timeout: Optional[float] = None,
  ) -> PrepCmd.CalibrationValues:
    return await self.backend.read_calibration_values(read_timeout=read_timeout)

  async def _run_with_report(
    self,
    command_name: str,
    op: Callable[[Optional[float]], Awaitable[_TCalibResult]],
    *,
    channel: Optional[PrepCmd.ChannelIndex] = None,
    read_timeout: Optional[float] = None,
  ) -> Union[_TCalibResult, CalibrationCommandReport]:
    timeout = self._effective_timeout(read_timeout)
    if not self.report_after_command:
      result = await op(timeout)
      # Keep "last snapshot" updated for subsequent diff_from_last calls.
      self._last_snapshot = await self._get_calibration_values(read_timeout=timeout)
      return result

    before_full = await self._get_calibration_values(read_timeout=timeout)
    result = await op(timeout)
    after_full = await self._get_calibration_values(read_timeout=timeout)
    self._last_snapshot = after_full

    before = self._select_snapshot_scope(before_full, channel=channel)
    after = self._select_snapshot_scope(after_full, channel=channel)
    diff = PrepCmd.diff_calibration_values(before, after, float_tol=self.float_tol)
    report = CalibrationCommandReport(
      command=command_name,
      result=result,
      before=before,
      after=after,
      diff=diff,
    )
    self.history.append(report)
    self._log_report(report)
    return report

  async def __aenter__(self) -> "PrepCalibrationSession":
    await self.start()
    return self

  async def start(self) -> "PrepCalibrationSession":
    """Start calibration mode and capture baseline snapshot."""
    if self._started:
      return self
    if self._ended:
      raise RuntimeError("Calibration session is already ended; create a new session.")
    if self.backend._calibration_session_active:
      raise RuntimeError("A calibration session is already active on this backend.")
    await self.backend.begin_calibration()
    await self.backend.calibration_initialize()
    self.backend._set_calibration_session_active(True)
    try:
      snapshot = await self._get_calibration_values(read_timeout=self.session_read_timeout)
    except Exception:
      self.backend._set_calibration_session_active(False)
      raise
    self._baseline = snapshot
    self._last_snapshot = snapshot
    self._started = True
    logger.info("Calibration session started")
    return self

  async def __aexit__(self, exc_type, exc, tb) -> bool:
    if self._ended:
      return False
    try:
      await self.end(save=False)
    except Exception:
      logger.exception("Failed to rollback calibration session")
      if exc is None:
        raise
    return False

  async def snapshot(self, *, read_timeout: Optional[float] = None) -> PrepCmd.CalibrationValues:
    self._ensure_started()
    snapshot = await self._get_calibration_values(
      read_timeout=self._effective_timeout(read_timeout)
    )
    self._last_snapshot = snapshot
    return snapshot

  async def diff_from_start(
    self,
    *,
    float_tol: Optional[float] = None,
    read_timeout: Optional[float] = None,
  ) -> PrepCmd.CalibrationValuesDiff:
    self._ensure_started()
    current = await self.snapshot(read_timeout=read_timeout)
    return PrepCmd.diff_calibration_values(
      self.baseline,
      current,
      float_tol=self.float_tol if float_tol is None else float_tol,
    )

  async def diff_from_last(
    self,
    *,
    float_tol: Optional[float] = None,
    read_timeout: Optional[float] = None,
  ) -> PrepCmd.CalibrationValuesDiff:
    self._ensure_started()
    previous = self.last_snapshot
    current = await self.snapshot(read_timeout=read_timeout)
    return PrepCmd.diff_calibration_values(
      previous,
      current,
      float_tol=self.float_tol if float_tol is None else float_tol,
    )

  async def end(
    self, *, save: bool = True, date_time: Optional[PrepCmd.HoiDateTime] = None
  ) -> None:
    """End the calibration session, optionally saving values."""
    if self._ended:
      return
    self._ensure_started()
    if save:
      await self.backend.end_calibration(date_time=date_time)
      logger.info("Calibration session ended and saved")
    else:
      await self.backend.cancel_calibration()
      logger.info("Calibration session ended without saving")
    self._ended = True
    self._started = False
    self.backend._set_calibration_session_active(False)

  async def rollback(self) -> None:
    """End the session without saving (alias for ``end(save=False)``)."""
    await self.end(save=False)

  async def commit(self) -> None:
    """Save calibration and end the session (alias for ``end(save=True)``)."""
    await self.end(save=True)

  async def reset(self, *, store: bool = False) -> None:
    """Reset calibration values during an active calibration session."""
    self._ensure_started()
    await self.backend.reset_calibration(store=store)
    self._last_snapshot = await self._get_calibration_values(read_timeout=self.session_read_timeout)

  async def calibrate_x_axis(
    self,
    *,
    site_index: int,
    channel: PrepCmd.ChannelIndex,
    read_timeout: Optional[float] = None,
  ) -> Union[float, CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> float:
      result = await self.backend.client.send_command(
        PrepCmd.PrepCalibrateXAxis(
          dest=await self.backend._require("calibration"),
          site_index=site_index,
          channel=int(channel),
        ),
        read_timeout=timeout,
      )
      return float(result.offset)

    return await self._run_with_report(
      f"calibrate_x_axis(channel={channel.name}, site_index={site_index})",
      _op,
      channel=channel,
      read_timeout=read_timeout,
    )

  async def calibrate_y_axis(
    self,
    *,
    site_index: int,
    channel: PrepCmd.ChannelIndex,
    read_timeout: Optional[float] = None,
  ) -> Union[float, CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> float:
      result = await self.backend.client.send_command(
        PrepCmd.PrepCalibrateYAxis(
          dest=await self.backend._require("calibration"),
          site_index=site_index,
          channel=int(channel),
        ),
        read_timeout=timeout,
      )
      return float(result.offset)

    return await self._run_with_report(
      f"calibrate_y_axis(channel={channel.name}, site_index={site_index})",
      _op,
      channel=channel,
      read_timeout=read_timeout,
    )

  async def calibrate_z_axis(
    self,
    *,
    site_index: int,
    channel: PrepCmd.ChannelIndex,
    read_timeout: Optional[float] = None,
  ) -> Union[float, CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> float:
      result = await self.backend.client.send_command(
        PrepCmd.PrepCalibrateZAxis(
          dest=await self.backend._require("calibration"),
          site_index=site_index,
          channel=int(channel),
        ),
        read_timeout=timeout,
      )
      return float(result.offset)

    return await self._run_with_report(
      f"calibrate_z_axis(channel={channel.name}, site_index={site_index})",
      _op,
      channel=channel,
      read_timeout=read_timeout,
    )

  async def calibrate_squeeze_tips(
    self,
    tip_spots: List[TipSpot],
    *,
    use_channels: Optional[List[int]] = None,
    z_seek_offset: Optional[float] = None,
    read_timeout: Optional[float] = None,
  ) -> Union[Tuple[int, ...], CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> Tuple[int, ...]:
      channels = use_channels if use_channels is not None else list(range(len(tip_spots)))
      assert len(tip_spots) == len(channels)

      indexed_spots = {ch: spot for ch, spot in zip(channels, tip_spots)}
      tip_positions: List[PrepCmd.TipPositionParameters] = []
      for ch in range(self.backend.num_channels):
        if ch not in indexed_spots:
          continue
        spot = indexed_spots[ch]
        loc = spot.get_absolute_location("c", "c", "t")
        tip_positions.append(
          PrepCmd.TipPositionParameters.for_op(
            _CHANNEL_INDEX[ch],
            loc,
            spot.get_tip(),
            z_seek_offset=z_seek_offset,
          )
        )

      result = await self.backend.client.send_command(
        PrepCmd.PrepCalibrateSqueezeTips(
          dest=await self.backend._require("calibration"),
          channels=tip_positions,
        ),
        read_timeout=timeout,
      )
      if result is None or not getattr(result, "positions", None):
        return ()
      return tuple(int(p) for p in result.positions)

    return await self._run_with_report(
      "calibrate_squeeze_tips",
      _op,
      read_timeout=read_timeout,
    )

  async def calibrate_squeeze_tips_mph(
    self,
    tip_spot: Union[TipSpot, List[TipSpot]],
    *,
    z_seek_offset: Optional[float] = None,
    read_timeout: Optional[float] = None,
  ) -> Union[Tuple[int, ...], CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> Tuple[int, ...]:
      if not self.backend.has_mph:
        raise RuntimeError(
          "Instrument does not have an 8MPH head. Cannot use calibrate_squeeze_tips_mph."
        )
      spots = tip_spot if isinstance(tip_spot, list) else [tip_spot]
      if not spots:
        raise ValueError("calibrate_squeeze_tips_mph: tip_spot list is empty")

      ref_spot = spots[0]
      loc = ref_spot.get_absolute_location("c", "c", "t")
      tip_position = PrepCmd.TipPositionParameters.for_op(
        PrepCmd.ChannelIndex.MPHChannel,
        loc,
        ref_spot.get_tip(),
        z_seek_offset=z_seek_offset,
      )

      result = await self.backend.client.send_command(
        PrepCmd.PrepCalibrateSqueezeTips(
          dest=await self.backend._require("calibration"),
          channels=[tip_position],
        ),
        read_timeout=timeout,
      )
      if result is None or not getattr(result, "positions", None):
        return ()
      return tuple(int(p) for p in result.positions)

    return await self._run_with_report(
      "calibrate_squeeze_tips_mph",
      _op,
      channel=PrepCmd.ChannelIndex.MPHChannel,
      read_timeout=read_timeout,
    )
