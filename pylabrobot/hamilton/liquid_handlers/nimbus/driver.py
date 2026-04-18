"""NimbusDriver: TCP-based driver for Hamilton Nimbus liquid handlers."""

from __future__ import annotations

import logging
from typing import Dict, Optional

from pylabrobot.hamilton.liquid_handlers.tcp_base import HamiltonTCPHandler
from pylabrobot.hamilton.tcp.introspection import HamiltonIntrospection
from pylabrobot.hamilton.tcp.packets import Address

from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

from .commands import (
  GetChannelConfiguration_1,
  Park,
)
from .door import NimbusDoor
from .pip_backend import NimbusPIPBackend

logger = logging.getLogger(__name__)


class NimbusDriver(HamiltonTCPHandler):
  """Driver for Hamilton Nimbus liquid handlers.

  Handles TCP communication, hardware discovery via introspection, and
  manages the PIP backend and door subsystem.
  """

  def __init__(
    self,
    host: str,
    port: int = 2000,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
  ):
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
    )

    self._nimbus_core_address: Optional[Address] = None

    self.pip: NimbusPIPBackend  # set in setup()
    self.door: Optional[NimbusDoor] = None  # set in setup() if available

  @property
  def nimbus_core_address(self) -> Address:
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore address not discovered. Call setup() first.")
    return self._nimbus_core_address

  async def setup(self, deck: Optional[NimbusDeck] = None):
    """Initialize connection, discover hardware, and create backends.

    Args:
      deck: NimbusDeck for coordinate conversion. Required for pipetting operations.
    """
    # TCP connection + Protocol 7 + Protocol 3 + root discovery
    await super().setup()

    # Discover instrument objects via introspection
    addresses = await self._discover_instrument_objects()

    pipette_address = addresses.get("Pipette")
    door_address = addresses.get("DoorLock")

    if pipette_address is None:
      raise RuntimeError("Pipette object not discovered. Cannot proceed with setup.")
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore root object not discovered. Cannot proceed with setup.")

    # Query channel configuration
    config = await self.send_command(GetChannelConfiguration_1(self._nimbus_core_address))
    assert config is not None, "GetChannelConfiguration_1 command returned None"
    num_channels = config["channels"]
    logger.info(f"Channel configuration: {num_channels} channels")

    # Create backends — each object stores its own address and state
    self.pip = NimbusPIPBackend(
      driver=self, deck=deck, address=pipette_address, num_channels=num_channels
    )

    if door_address is not None:
      self.door = NimbusDoor(driver=self, address=door_address)

    # Initialize subsystems
    if self.door is not None:
      await self.door._on_setup()

  async def stop(self):
    """Stop driver and close connection."""
    if self.door is not None:
      await self.door._on_stop()
    await super().stop()
    self.door = None

  async def _discover_instrument_objects(self) -> Dict[str, Address]:
    """Discover instrument-specific objects using introspection.

    Returns:
      Dictionary mapping object names (e.g. "Pipette", "DoorLock") to their addresses.
    """
    introspection = HamiltonIntrospection(self)
    addresses: Dict[str, Address] = {}

    root_objects = self._discovered_objects.get("root", [])
    if not root_objects:
      logger.warning("No root objects discovered")
      return addresses

    nimbus_core_addr = root_objects[0]
    self._nimbus_core_address = nimbus_core_addr

    try:
      core_info = await introspection.get_object(nimbus_core_addr)

      for i in range(core_info.subobject_count):
        try:
          sub_addr = await introspection.get_subobject_address(nimbus_core_addr, i)
          sub_info = await introspection.get_object(sub_addr)
          addresses[sub_info.name] = sub_addr
          logger.info(f"Found {sub_info.name} at {sub_addr}")
        except Exception as e:
          logger.debug(f"Failed to get subobject {i}: {e}")

    except Exception as e:
      logger.warning(f"Failed to discover instrument objects: {e}")

    if "DoorLock" not in addresses:
      logger.info("DoorLock not available on this instrument")

    return addresses

  async def park(self):
    """Park the instrument."""
    await self.send_command(Park(self.nimbus_core_address))
    logger.info("Instrument parked successfully")
