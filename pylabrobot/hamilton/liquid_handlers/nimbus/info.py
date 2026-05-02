"""Nimbus instrument info service.

``NimbusInstrumentInfo`` is a bootstrap peer — it runs during :meth:`Nimbus.setup`
before any other peers are constructed and caches the channel configuration
returned by firmware command 30 (ChannelConfiguration on NimbusCORE).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from pylabrobot.hamilton.tcp.introspection import FirmwareTreeNode

from .commands import ChannelConfiguration, IsInitialized, NimbusChannelConfigWire

if TYPE_CHECKING:
  from .driver import NimbusDriver

logger = logging.getLogger(__name__)


class NimbusInstrumentInfo:
  """Owns the cached ``ChannelConfiguration`` snapshot and async instrument queries."""

  def __init__(self, driver: "NimbusDriver") -> None:
    self._driver = driver
    self._configurations: Optional[List[NimbusChannelConfigWire]] = None

  async def _on_setup(self) -> None:
    """Fetch and cache channel configuration. Called from :meth:`Nimbus.setup`."""
    resp = await self._driver.send_command(ChannelConfiguration())
    assert resp is not None, "ChannelConfiguration command returned None"
    self._configurations = list(resp.configurations)
    logger.info("Channel configuration: %d channels", len(self._configurations))

  async def _on_stop(self) -> None:
    self._configurations = None

  @property
  def channel_configurations(self) -> List[NimbusChannelConfigWire]:
    """Cached per-channel configurations. Raises if ``_on_setup`` has not run."""
    if self._configurations is None:
      raise RuntimeError("Channel configuration not available. Call Nimbus.setup() first.")
    return self._configurations

  @property
  def num_channels(self) -> int:
    return len(self.channel_configurations)

  async def is_initialized(self) -> bool:
    """Whether NimbusCORE reports as initialized (IsInitialized, cmd 14)."""
    result = await self._driver.send_command(IsInitialized())
    if result is None:
      return False
    return bool(result.initialized)

  async def get_firmware_tree(self, refresh: bool = False) -> FirmwareTreeNode:
    """Firmware object tree. ``print(await nimbus.info.get_firmware_tree())`` for a diagnostic dump."""
    return await self._driver.introspection.get_firmware_tree(refresh=refresh)
