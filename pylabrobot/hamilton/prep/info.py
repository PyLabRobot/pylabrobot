"""Prep instrument info service.

Canonical holder of device-wide metadata. ``PrepInstrumentInfo`` owns the
cached ``InstrumentConfig`` snapshot (loaded in :meth:`_on_setup`), exposes its
fields as sync properties, and performs on-demand diagnostic / firmware queries
via the driver transport (``resolve_path``, ``send_command``,
``PrepClient._query_firmware_string``).

Info is a **bootstrap** phase — it runs before peers are constructed, so it
resolves the handful of firmware paths it needs itself via
:attr:`PrepInstrumentInfo._paths` + ``_require`` / ``_try_require``.

User-facing instrument-wide pool: ``prep.info``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, Dict, Optional, Tuple

from pylabrobot.hamilton.transport.tcp.introspection import FirmwareTreeNode
from pylabrobot.hamilton.transport.tcp.packets import Address

from . import prep_commands as PrepCmd

if TYPE_CHECKING:
  from .client import PrepClient

logger = logging.getLogger(__name__)


class PrepInstrumentInfo:
  """Owns the cached ``InstrumentConfig`` + async instrument-metadata queries."""

  # Firmware paths PrepInstrumentInfo touches. Bootstrap-phase — needed before
  # peers are constructed — so info resolves them itself via
  # ``driver.resolve_path`` (backed by the introspection registry's path cache).
  _paths: ClassVar[Dict[str, str]] = {
    "mlprep_service": "MLPrepRoot.MLPrepService",
    "deck_config": "MLPrepRoot.MLPrepCalibration.DeckConfiguration",
    "mlprep_cpu": "MLPrepRoot.MLPrepCpu",
    "module_information": "MLPrepRoot.PipettorRoot.ModuleInformation",
  }

  def __init__(self, driver: "PrepClient"):
    self._driver = driver
    self._config: Optional[PrepCmd.InstrumentConfig] = None

  async def _require(self, key: str) -> Address:
    """Resolve a diagnostic path alias; raises if absent."""
    if key not in self._paths:
      raise KeyError(f"unknown info path key: {key!r}")
    return await self._driver.resolve_path(self._paths[key])

  async def _try_require(self, key: str) -> Optional[Address]:
    """Resolve a diagnostic path alias; returns ``None`` if the path is absent."""
    try:
      return await self._require(key)
    except (KeyError, RuntimeError, TypeError):
      return None

  # -- Lifecycle --------------------------------------------------------------

  async def _on_setup(self) -> None:
    """Fetch and cache the instrument config. Called from :meth:`Prep.setup`."""
    self._config = await self._load_instrument_config()

  async def _on_stop(self) -> None:
    self._config = None

  # -- Cached config ----------------------------------------------------------

  @property
  def config(self) -> PrepCmd.InstrumentConfig:
    """Cached ``InstrumentConfig``. Raises if ``_on_setup`` has not run."""
    if self._config is None:
      raise RuntimeError("Instrument config not available. Call Prep.setup() first.")
    return self._config

  @property
  def num_channels(self) -> int:
    n = self.config.num_channels
    if n is None:
      raise RuntimeError("Instrument config has no num_channels (finish Prep.setup first).")
    return n

  @property
  def has_mph(self) -> bool:
    h = self.config.has_mph
    if h is None:
      raise RuntimeError("Instrument config has no has_mph (finish Prep.setup first).")
    return h

  @property
  def deck_bounds(self) -> Optional[PrepCmd.DeckBounds]:
    return self.config.deck_bounds

  @property
  def deck_sites(self) -> Tuple[PrepCmd.DeckSiteInfo, ...]:
    return self.config.deck_sites

  @property
  def waste_sites(self) -> Tuple[PrepCmd.WasteSiteInfo, ...]:
    return self.config.waste_sites

  @property
  def default_traverse_height(self) -> Optional[float]:
    return self.config.default_traverse_height

  @property
  def has_enclosure(self) -> bool:
    return self.config.has_enclosure

  @property
  def safe_speeds_enabled(self) -> bool:
    return self.config.safe_speeds_enabled

  async def refresh(self) -> PrepCmd.InstrumentConfig:
    """Re-query instrument config and update the cached snapshot."""
    self._config = await self._load_instrument_config()
    return self._config

  # -- Instrument config (MLPrep / deck / service) ----------------------------

  async def get_present_channels(self) -> Optional[Tuple[PrepCmd.ChannelIndex, ...]]:
    """Query which channels are present (GetPresentChannels on MLPrepService)."""
    d = self._driver
    service_addr = await self._try_require("mlprep_service")
    if service_addr is None:
      return None
    try:
      resp = await d.send_command(PrepCmd.PrepGetPresentChannels(dest=service_addr))
      if resp is None or not getattr(resp, "channels", None):
        return None
      return tuple(
        PrepCmd.ChannelIndex(v) if v in (0, 1, 2, 3) else PrepCmd.ChannelIndex.InvalidIndex
        for v in resp.channels
      )
    except (
      TimeoutError,
      ConnectionError,
      ConnectionResetError,
      ConnectionAbortedError,
      BrokenPipeError,
      OSError,
    ):
      raise
    except Exception as e:
      logger.warning("Failed to query present channels: %s", e)
      return None

  async def _load_instrument_config(self) -> PrepCmd.InstrumentConfig:
    """Aggregate MLPrep, DeckConfiguration, and MLPrepService into ``InstrumentConfig``."""
    d = self._driver
    mlprep = d.mlprep_address
    enc_resp = await d.send_command(PrepCmd.PrepGetIsEnclosurePresent(dest=mlprep))
    safe_resp = await d.send_command(PrepCmd.PrepGetSafeSpeedsEnabled(dest=mlprep))
    height_resp = await d.send_command(PrepCmd.PrepGetDefaultTraverseHeight(dest=mlprep))
    has_enclosure = bool(enc_resp.value) if enc_resp else False
    safe_speeds_enabled = bool(safe_resp.value) if safe_resp else False
    default_traverse_height = float(height_resp.value) if height_resp else None

    deck_bounds: Optional[PrepCmd.DeckBounds] = None
    deck_sites: Tuple[PrepCmd.DeckSiteInfo, ...] = ()
    waste_sites: Tuple[PrepCmd.WasteSiteInfo, ...] = ()
    deck_addr = await self._try_require("deck_config")
    if deck_addr is None:
      raise RuntimeError("DeckConfiguration path did not resolve — cannot load instrument config")

    bounds_resp = await d.send_command(PrepCmd.PrepGetDeckBounds(dest=deck_addr))
    if bounds_resp:
      deck_bounds = PrepCmd.DeckBounds(
        min_x=bounds_resp.min_x,
        max_x=bounds_resp.max_x,
        min_y=bounds_resp.min_y,
        max_y=bounds_resp.max_y,
        min_z=bounds_resp.min_z,
        max_z=bounds_resp.max_z,
      )

    sites_resp = await d.send_command(PrepCmd.PrepGetDeckSiteDefinitions(dest=deck_addr))
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

    waste_resp = await d.send_command(PrepCmd.PrepGetWasteSiteDefinitions(dest=deck_addr))
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

  async def is_initialized(self) -> bool:
    """Whether MLPrep reports as initialized (GetIsInitialized, cmd=2)."""
    result = await self._driver.send_command(
      PrepCmd.PrepGetIsInitialized(dest=self._driver.mlprep_address)
    )
    if result is None:
      return False
    return bool(result.value)

  async def get_tip_and_needle_definitions(self) -> Tuple[PrepCmd.TipDefinition, ...]:
    """Tip/needle definitions (GetTipAndNeedleDefinitions, cmd=11)."""
    result = await self._driver.send_command(
      PrepCmd.PrepGetTipAndNeedleDefinitions(dest=self._driver.mlprep_address)
    )
    if result is None or not getattr(result, "definitions", None):
      return ()
    return tuple(result.definitions)

  # -- Firmware string queries (orchestration; decode on PrepClient) ----------

  async def get_firmware_version(self) -> Optional[str]:
    addr = await self._try_require("mlprep_cpu")
    if addr is None:
      return None
    return await self._driver._query_firmware_string(addr, cmd_id=8)

  async def get_device_serial_number(self) -> Optional[str]:
    addr = await self._try_require("mlprep_cpu")
    if addr is None:
      return None
    return await self._driver._query_firmware_string(addr, cmd_id=9)

  async def get_bootloader_version(self) -> Optional[str]:
    addr = await self._try_require("mlprep_cpu")
    if addr is None:
      return None
    return await self._driver._query_firmware_string(addr, cmd_id=2, iface_id=2)

  async def get_module_part_number(self) -> Optional[str]:
    addr = await self._try_require("module_information")
    if addr is None:
      return None
    return await self._driver._query_firmware_string(addr, cmd_id=5)

  async def get_firmware_tree(self, refresh: bool = False) -> FirmwareTreeNode:
    """Firmware object tree. ``print(await info.get_firmware_tree())`` for a diagnostic dump."""
    return await self._driver.introspection.get_firmware_tree(refresh=refresh)
