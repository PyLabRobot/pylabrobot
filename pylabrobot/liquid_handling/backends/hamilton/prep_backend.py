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
import logging
import math
import random
from typing import List, Optional, Tuple, Union, overload

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


class PrepBackend(LiquidHandlerBackend):
  """Backend for Hamilton Prep instruments using the shared TCP stack.

  Uses HamiltonTCPClient (self.client) for communication and introspection;
  implements LiquidHandlerBackend for liquid handling.
  Interfaces resolved lazily via _require() on first use.
  Construction accepts either host (and optionally port) to create the client
  with defaults, or client to inject a pre-configured HamiltonTCPClient.

  On-demand introspection: ``await self.client.introspect(path)``.
  """

  # Declare known object paths via InterfaceSpec. deck_config required (key positions, traverse height, deck info).
  _INTERFACES: dict[str, InterfaceSpec] = {
    "mlprep": InterfaceSpec("MLPrepRoot.MLPrep", True, True),
    "pipettor": InterfaceSpec("MLPrepRoot.PipettorRoot.Pipettor", True, True),
    "coordinator": InterfaceSpec("MLPrepRoot.ChannelCoordinator", True, True),
    "deck_config": InterfaceSpec("MLPrepRoot.MLPrepCalibration.DeckConfiguration", True, True),
    "mph": InterfaceSpec("MLPrepRoot.MphRoot.MPH", False, True),
    "mlprep_service": InterfaceSpec("MLPrepRoot.MLPrepService", False, True),
  }

  @overload
  def __init__(
    self,
    *,
    host: str,
    port: int = 2000,
    default_traverse_height: Optional[float] = None,
  ) -> None: ...

  @overload
  def __init__(
    self,
    *,
    client: HamiltonTCPClient,
    default_traverse_height: Optional[float] = None,
  ) -> None: ...

  def __init__(
    self,
    *,
    host: Optional[str] = None,
    port: int = 2000,
    client: Optional[HamiltonTCPClient] = None,
    default_traverse_height: Optional[float] = None,
  ) -> None:
    """Initialize Prep backend.

    Args:
      host: Instrument hostname or IP; used when client is not provided.
      port: TCP port (default 2000).
      client: Optional pre-configured HamiltonTCPClient (mutually exclusive
        with host).
      default_traverse_height: Optional default traverse height in mm.
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

  def _has_interface(self, name: str) -> bool:
    """Return True if the interface was resolved and is present."""
    return self._resolver.has_interface(name)

  def set_default_traverse_height(self, value: float) -> None:
    """Set the default traverse height (mm) used when final_z is not passed to pick_up_tips/drop_tips.

    Use this when the instrument did not report a traverse height at setup, or to override
    the probed value.
    """
    self._user_traverse_height = value

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

    # Discover per-channel drive addresses from the object tree.
    await self._discover_channel_drives()

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

      logger.debug("Discovered channel on node %d: sleeve_sensor=%s, ZDrive=%s, NodeInfo=%s",
                    sub_addr.node, sdrive_addr, zdrive_addr, node_info_addr)

    logger.info("Discovered %d pipettor channel drive pairs", len(self._channel_sleeve_sensor_addrs))

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
  ):
    """Aspirate using v2 commands, dispatching to the appropriate variant.

    Selects the command variant based on ``use_lld`` / ``lld`` (LLD on/off) and
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
      use_lld: Enable LLD aspirate variant.  Also activated if ``lld`` is set.
      lld: LLD seek parameters. When None and use_lld=True, built from labware geometry
        (z_seek = top of well; z_submerge/z_out_of_liquid = relative offsets).
      p_lld: Pressure LLD parameters (LLD variants only).
      c_lld: Capacitive LLD parameters (LLD variants only).
      tadm: TADM parameters (TADM variants only).  Firmware defaults when None.
      container_segments: Per-channel PrepCmd.SegmentDescriptor lists for liquid following.
        If None and auto_container_geometry=True, derived from well geometry.
      auto_container_geometry: Automatically build container segments from the
        well's cross-section geometry.  Pass False to use empty segments
        (firmware falls back to the PrepCmd.CommonParameters cone model).
      hamilton_liquid_classes: None = defaults per op via get_star_liquid_class (same as STAR).
        Else list of Hamilton liquid classes, one per op; length must match len(ops), no None in list.
      disable_volume_correction: Per-op flag to skip volume correction. When None, treated as [False]*n.

    Example::

      await backend.aspirate(ops, [0], z_final=[95.0], settling_time=[2.0])
      await backend.aspirate(ops, [0], use_lld=True)
      await backend.aspirate(ops, [0], monitoring_mode=PrepCmd.MonitoringMode.TADM)
    """
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
      # Defaults from STAR calibration table; add get_prep_liquid_class if Prep needs different values.
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

    # Default lists from HLC (fallbacks when HLC is None)
    default_settling = [hlc.aspiration_settling_time if hlc is not None else 1.0 for hlc in hlcs]
    default_transport_air_volume = [
      hlc.aspiration_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs
    ]
    default_z_liquid_exit_speed = [
      hlc.aspiration_swap_speed if hlc is not None else 10.0 for hlc in hlcs
    ]
    default_prewet_volume = [
      hlc.aspiration_over_aspirate_volume if hlc is not None else 0.0 for hlc in hlcs
    ]
    settling_time = fill_in_defaults(settling_time, default_settling)
    transport_air_volume = fill_in_defaults(transport_air_volume, default_transport_air_volume)
    z_liquid_exit_speed = fill_in_defaults(z_liquid_exit_speed, default_z_liquid_exit_speed)
    prewet_volume = fill_in_defaults(prewet_volume, default_prewet_volume)

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

    effective_lld = use_lld or (lld is not None)
    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}

    # Precompute well geometry once (used for default Z lists and for LLD in the loop).
    well_geometry = [_absolute_z_from_well(op) for op in ops]
    default_z_minimum = [g[0] for g in well_geometry]
    default_z_fluid = [g[1] for g in well_geometry]
    default_z_air = [g[3] for g in well_geometry]
    raw_traverse = self._resolve_traverse_height(None)
    default_z_final = [
      raw_traverse - (op.tip.total_tip_length - op.tip.fitting_depth) for op in ops
    ]
    default_z_bottom_search_offset = [2.0] * n
    z_minimum = fill_in_defaults(z_minimum, default_z_minimum)
    z_fluid = fill_in_defaults(z_fluid, default_z_fluid)
    z_air = fill_in_defaults(z_air, default_z_air)
    z_final = fill_in_defaults(z_final, default_z_final)
    z_bottom_search_offset = fill_in_defaults(
      z_bottom_search_offset, default_z_bottom_search_offset
    )

    # Build per-channel segment lists.
    ch_segments: dict[int, list[PrepCmd.SegmentDescriptor]] = {}
    for i, ch in enumerate(use_channels):
      if container_segments is not None and i < len(container_segments):
        ch_segments[ch] = container_segments[i]
      elif auto_container_geometry:
        ch_segments[ch] = _build_container_segments(indexed_ops[ch].resource)
      else:
        ch_segments[ch] = []

    _p_lld = p_lld or PrepCmd.PLldParameters.default()
    _c_lld = c_lld or PrepCmd.CLldParameters.default()
    _tadm = tadm or PrepCmd.TadmParameters.default()

    params_lld_mon: List[PrepCmd.AspirateParametersLldAndMonitoring2] = []
    params_lld_tadm: List[PrepCmd.AspirateParametersLldAndTadm2] = []
    params_nolld_mon: List[PrepCmd.AspirateParametersNoLldAndMonitoring2] = []
    params_nolld_tadm: List[PrepCmd.AspirateParametersNoLldAndTadm2] = []

    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      idx = ch_to_idx[ch]
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
      radius = _effective_radius(op.resource)
      asp = PrepCmd.AspirateParameters.for_op(
        loc,
        op,
        prewet_volume=prewet_volume[idx],
        blowout_volume=blowout_volumes[idx],
      )
      segs = ch_segments[ch]

      z_minimum_ch = z_minimum[idx]
      z_final_ch = z_final[idx]
      z_fluid_ch = z_fluid[idx]
      z_air_ch = z_air[idx]
      z_bottom_search_offset_ch = z_bottom_search_offset[idx]

      if effective_lld and lld is None:
        top_of_well_z = well_geometry[idx][2]
        _lld = PrepCmd.LldParameters(
          default_values=False,
          z_seek=top_of_well_z,
          z_seek_speed=0.0,
          z_submerge=2.0,
          z_out_of_liquid=0.0,
        )
      else:
        _lld = lld or PrepCmd.LldParameters.default()

      common = PrepCmd.CommonParameters.for_op(
        volumes[idx],
        radius,
        flow_rate=flow_rates[idx],
        z_minimum=z_minimum_ch,
        z_final=z_final_ch,
        z_liquid_exit_speed=z_liquid_exit_speed[idx],
        transport_air_volume=transport_air_volume[idx],
        settling_time=settling_time[idx],
      )
      no_lld = PrepCmd.NoLldParameters.for_fixed_z(
        z_fluid_ch, z_air_ch, z_bottom_search_offset=z_bottom_search_offset_ch
      )

      if effective_lld and monitoring_mode == PrepCmd.MonitoringMode.TADM:
        params_lld_tadm.append(
          PrepCmd.AspirateParametersLldAndTadm2(
            default_values=False,
            channel=_CHANNEL_INDEX[ch],
            aspirate=asp,
            container_description=segs,
            common=common,
            lld=_lld,
            p_lld=_p_lld,
            c_lld=_c_lld,
            mix=PrepCmd.MixParameters.default(),
            tadm=_tadm,
            adc=PrepCmd.AdcParameters.default(),
          )
        )
      elif effective_lld:
        params_lld_mon.append(
          PrepCmd.AspirateParametersLldAndMonitoring2(
            default_values=False,
            channel=_CHANNEL_INDEX[ch],
            aspirate=asp,
            container_description=segs,
            common=common,
            lld=_lld,
            p_lld=_p_lld,
            c_lld=_c_lld,
            mix=PrepCmd.MixParameters.default(),
            aspirate_monitoring=PrepCmd.AspirateMonitoringParameters.default(),
            adc=PrepCmd.AdcParameters.default(),
          )
        )
      elif monitoring_mode == PrepCmd.MonitoringMode.TADM:
        params_nolld_tadm.append(
          PrepCmd.AspirateParametersNoLldAndTadm2(
            default_values=False,
            channel=_CHANNEL_INDEX[ch],
            aspirate=asp,
            container_description=segs,
            common=common,
            no_lld=no_lld,
            mix=PrepCmd.MixParameters.default(),
            adc=PrepCmd.AdcParameters.default(),
            tadm=_tadm,
          )
        )
      else:
        params_nolld_mon.append(
          PrepCmd.AspirateParametersNoLldAndMonitoring2(
            default_values=False,
            channel=_CHANNEL_INDEX[ch],
            aspirate=asp,
            container_description=segs,
            common=common,
            no_lld=no_lld,
            mix=PrepCmd.MixParameters.default(),
            adc=PrepCmd.AdcParameters.default(),
            aspirate_monitoring=PrepCmd.AspirateMonitoringParameters.default(),
          )
        )

    dest = await self._require("pipettor")
    if effective_lld and monitoring_mode == PrepCmd.MonitoringMode.TADM:
      await self.client.send_command(
        PrepCmd.PrepAspirateWithLldTadmV2(dest=dest, aspirate_parameters=params_lld_tadm)
      )
    elif effective_lld:
      await self.client.send_command(
        PrepCmd.PrepAspirateWithLldV2(dest=dest, aspirate_parameters=params_lld_mon)
      )
    elif monitoring_mode == PrepCmd.MonitoringMode.TADM:
      await self.client.send_command(
        PrepCmd.PrepAspirateTadmV2(dest=dest, aspirate_parameters=params_nolld_tadm)
      )
    else:
      await self.client.send_command(
        PrepCmd.PrepAspirateNoLldMonitoringV2(dest=dest, aspirate_parameters=params_nolld_mon)
      )

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
    use_lld: bool = False,
    lld: Optional[PrepCmd.LldParameters] = None,
    c_lld: Optional[PrepCmd.CLldParameters] = None,
    container_segments: Optional[List[List[PrepCmd.SegmentDescriptor]]] = None,
    auto_container_geometry: bool = False,  # TODO: Doesn't work with no LLD
    hamilton_liquid_classes: Optional[List[HamiltonLiquidClass]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
  ):
    """Dispense using v2 commands, dispatching to NoLLD or LLD variant.

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
      use_lld: Enable LLD dispense variant.  Also activated if ``lld`` is set.
      lld: LLD seek parameters. When None and use_lld=True, built from labware geometry.
      c_lld: Capacitive LLD parameters (LLD variant only).
      container_segments: Per-channel PrepCmd.SegmentDescriptor lists for liquid following.
      auto_container_geometry: Automatically build container segments from well geometry.
      hamilton_liquid_classes: None = defaults per op via get_star_liquid_class (same as STAR).
        Else list of Hamilton liquid classes, one per op; length must match len(ops), no None in list.
      disable_volume_correction: Per-op flag to skip volume correction. When None, treated as [False]*n.

    Example::

      await backend.dispense(ops, [0], final_z=[95.0], settling_time=[0.5])
      await backend.dispense(ops, [0], use_lld=True)
    """
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
      # Defaults from STAR calibration table; add get_prep_liquid_class if Prep needs different values.
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

    # Default lists from HLC (fallbacks when HLC is None)
    default_settling = [hlc.dispense_settling_time if hlc is not None else 0.0 for hlc in hlcs]
    default_transport_air_volume = [
      hlc.dispense_air_transport_volume if hlc is not None else 0.0 for hlc in hlcs
    ]
    default_z_liquid_exit_speed = [
      hlc.dispense_swap_speed if hlc is not None else 10.0 for hlc in hlcs
    ]
    default_stop_back_volume = [
      hlc.dispense_stop_back_volume if hlc is not None else 0.0 for hlc in hlcs
    ]
    default_cutoff_speed = [
      hlc.dispense_stop_flow_rate if hlc is not None else 100.0 for hlc in hlcs
    ]
    settling_time = fill_in_defaults(settling_time, default_settling)
    transport_air_volume = fill_in_defaults(transport_air_volume, default_transport_air_volume)
    z_liquid_exit_speed = fill_in_defaults(z_liquid_exit_speed, default_z_liquid_exit_speed)
    stop_back_volume = fill_in_defaults(stop_back_volume, default_stop_back_volume)
    cutoff_speed = fill_in_defaults(cutoff_speed, default_cutoff_speed)

    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None and not disabled else op.volume
      for op, hlc, disabled in zip(ops, hlcs, disable_volume_correction)
    ]
    flow_rates = [
      op.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 100.0)
      for op, hlc in zip(ops, hlcs)
    ]

    effective_lld = use_lld or (lld is not None)
    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}

    # Precompute well geometry once (used for default Z lists and for LLD in the loop).
    well_geometry = [_absolute_z_from_well(op) for op in ops]
    default_z_minimum = [g[0] for g in well_geometry]
    default_z_fluid = [g[1] for g in well_geometry]
    default_z_air = [g[3] for g in well_geometry]
    raw_traverse = self._resolve_traverse_height(None)
    default_final_z = [
      raw_traverse - (op.tip.total_tip_length - op.tip.fitting_depth) for op in ops
    ]
    default_z_bottom_search_offset = [2.0] * n
    z_minimum = fill_in_defaults(z_minimum, default_z_minimum)
    z_fluid = fill_in_defaults(z_fluid, default_z_fluid)
    z_air = fill_in_defaults(z_air, default_z_air)
    final_z = fill_in_defaults(final_z, default_final_z)
    z_bottom_search_offset = fill_in_defaults(
      z_bottom_search_offset, default_z_bottom_search_offset
    )

    ch_segments: dict[int, list[PrepCmd.SegmentDescriptor]] = {}
    for i, ch in enumerate(use_channels):
      if container_segments is not None and i < len(container_segments):
        ch_segments[ch] = container_segments[i]
      elif auto_container_geometry:
        ch_segments[ch] = _build_container_segments(indexed_ops[ch].resource)
      else:
        ch_segments[ch] = []

    _c_lld = c_lld or PrepCmd.CLldParameters.default()

    params_nolld: List[PrepCmd.DispenseParametersNoLld2] = []
    params_lld: List[PrepCmd.DispenseParametersLld2] = []

    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      idx = ch_to_idx[ch]
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
      radius = _effective_radius(op.resource)
      disp = PrepCmd.DispenseParameters.for_op(
        loc,
        stop_back_volume=stop_back_volume[idx],
        cutoff_speed=cutoff_speed[idx],
      )
      segs = ch_segments[ch]

      z_minimum_ch = z_minimum[idx]
      z_final_ch = final_z[idx]
      z_fluid_ch = z_fluid[idx]
      z_air_ch = z_air[idx]
      z_bottom_search_offset_ch = z_bottom_search_offset[idx]

      if effective_lld and lld is None:
        top_of_well_z = well_geometry[idx][2]
        _lld = PrepCmd.LldParameters(
          default_values=False,
          z_seek=top_of_well_z,
          z_seek_speed=0.0,
          z_submerge=2.0,
          z_out_of_liquid=0.0,
        )
      else:
        _lld = lld or PrepCmd.LldParameters.default()

      common = PrepCmd.CommonParameters.for_op(
        volumes[idx],
        radius,
        flow_rate=flow_rates[idx],
        z_minimum=z_minimum_ch,
        z_final=z_final_ch,
        z_liquid_exit_speed=z_liquid_exit_speed[idx],
        transport_air_volume=transport_air_volume[idx],
        settling_time=settling_time[idx],
      )

      if effective_lld:
        params_lld.append(
          PrepCmd.DispenseParametersLld2(
            default_values=False,
            channel=_CHANNEL_INDEX[ch],
            dispense=disp,
            container_description=segs,
            common=common,
            lld=_lld,
            c_lld=_c_lld,
            mix=PrepCmd.MixParameters.default(),
            adc=PrepCmd.AdcParameters.default(),
            tadm=PrepCmd.TadmParameters.default(),
          )
        )
      else:
        params_nolld.append(
          PrepCmd.DispenseParametersNoLld2(
            default_values=False,
            channel=_CHANNEL_INDEX[ch],
            dispense=disp,
            container_description=segs,
            common=common,
            no_lld=PrepCmd.NoLldParameters.for_fixed_z(
              z_fluid_ch, z_air_ch, z_bottom_search_offset=z_bottom_search_offset_ch
            ),
            mix=PrepCmd.MixParameters.default(),
            adc=PrepCmd.AdcParameters.default(),
            tadm=PrepCmd.TadmParameters.default(),
          )
        )

    dest = await self._require("pipettor")
    if effective_lld:
      await self.client.send_command(
        PrepCmd.PrepDispenseWithLldV2(dest=dest, dispense_parameters=params_lld)
      )
    else:
      await self.client.send_command(
        PrepCmd.PrepDispenseNoLldV2(dest=dest, dispense_parameters=params_nolld)
      )

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
      List of bools, one per channel (index 0=rear, 1=front). True if tip detected.
    """
    import struct as _struct

    if not self._channel_sleeve_sensor_addrs:
      raise RuntimeError(
        "No channel sleeve sensor addresses discovered. Call setup() first."
      )

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
    data = raw[0]
    i = 0
    while i < len(data) - 3:
      if data[i] == 0x0F and data[i + 1] in (0x00, 0x01):
        slen = int.from_bytes(data[i + 2 : i + 4], "little")
        if slen > 0 and i + 4 + slen <= len(data):
          return data[i + 4 : i + 4 + slen].decode("utf-8", errors="replace").rstrip("\x00")
      i += 1
    return None

  async def _query_firmware_string(self, addr: Address, cmd_id: int, iface_id: int = 3) -> Optional[str]:
    """Send a status query and decode the string response."""
    Cmd = type(
      "_FWQuery",
      (PrepCmd._PrepStatusQuery,),
      {"command_id": cmd_id, "interface_id": iface_id, "__annotations__": {"dest": Address}},
    )
    raw = await self.client.send_command(Cmd(dest=addr), return_raw=True, raise_on_error=False)
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
      channel: Channel index (0=rear, 1=front).

    Analogous to STARBackend.request_pip_channel_version().
    """
    if channel >= len(self._channel_node_info_addrs):
      return None
    return await self._query_firmware_string(self._channel_node_info_addrs[channel], cmd_id=8, iface_id=1)

  async def request_pip_channel_serial_number(self, channel: int) -> Optional[str]:
    """Request the serial number for a pipettor channel.

    Args:
      channel: Channel index (0=rear, 1=front).
    """
    if channel >= len(self._channel_node_info_addrs):
      return None
    return await self._query_firmware_string(self._channel_node_info_addrs[channel], cmd_id=9, iface_id=1)

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
      channels: Channel indices to move (0=rear, 1=front). None = all channels.
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
