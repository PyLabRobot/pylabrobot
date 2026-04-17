"""NimbusDriver: TCP-based driver for Hamilton Nimbus liquid handlers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.hamilton.tcp.client import HamiltonTCPClient
from pylabrobot.hamilton.tcp.error_tables import NIMBUS_ERROR_CODES
from pylabrobot.hamilton.tcp.packets import Address

from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

from .commands import (
  GetChannelConfiguration_1,
  Park,
)
from .door import NimbusDoor
from .pip_backend import NimbusPIPBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterfaceSpec:
  object_name: str
  required: bool
  raise_when_missing: bool = True


@dataclass
class NimbusSetupParams(BackendParams):
  deck: Optional[NimbusDeck] = None
  require_door_lock: bool = False


class NimbusDriver(HamiltonTCPClient):
  """Driver for Hamilton Nimbus liquid handlers.

  Handles TCP communication, hardware discovery via introspection, and
  manages the PIP backend and door subsystem.
  """

  _INTERFACES = {
    "nimbus_core": InterfaceSpec("NimbusCore", required=True),
    "pipette": InterfaceSpec("Pipette", required=True),
    "door_lock": InterfaceSpec("DoorLock", required=False, raise_when_missing=False),
  }

  _REQUIRED_METHODS_CORE: Set[int] = {
    3,
    14,
    15,
    29,
  }  # Park, IsInitialized, GetChannelConfig_1, InitializeSmartRoll
  _REQUIRED_METHODS_PIPETTE: Set[int] = {
    4,  # PickupTips
    5,  # DropTips
    6,  # Aspirate
    7,  # Dispense
    16,  # IsTipPresent
    43,  # EnableADC
    44,  # DisableADC
    66,  # GetChannelConfiguration
    67,  # SetChannelConfiguration
    82,  # DropTipsRoll
  }

  def __init__(
    self,
    host: str,
    port: int = 2000,
    read_timeout: float = 300.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
    connection_timeout: int = 600,
    error_codes: Optional[Dict[Tuple[int, int, int, int, int], str]] = None,
  ):
    merged_error_codes = {**NIMBUS_ERROR_CODES, **(error_codes or {})}
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
      connection_timeout=connection_timeout,
      error_codes=merged_error_codes,
    )

    self._nimbus_core_address: Optional[Address] = None
    self._resolved_interfaces: Dict[str, Optional[Address]] = {}

    self.pip: NimbusPIPBackend  # set in setup()
    self.door: Optional[NimbusDoor] = None  # set in setup() if available

  @property
  def nimbus_core_address(self) -> Address:
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore address not discovered. Call setup() first.")
    return self._nimbus_core_address

  async def setup(self, backend_params: Optional[BackendParams] = None):
    """Initialize connection, discover hardware, and create backends.

    Args:
      backend_params: Optional :class:`NimbusSetupParams`.
    """
    if backend_params is None:
      params = NimbusSetupParams()
    elif isinstance(backend_params, NimbusSetupParams):
      params = backend_params
    else:
      raise TypeError(
        "NimbusDriver.setup expected NimbusSetupParams | None for backend_params, "
        f"got {type(backend_params).__name__}"
      )

    # TCP connection + Protocol 7 + Protocol 3 + root discovery
    await super().setup()

    addresses = await self._discover_instrument_objects()
    await self._resolve_interfaces(addresses)

    nimbus_core_address = await self._require_interface("nimbus_core")
    pipette_address = await self._require_interface("pipette")
    door_address = self._resolved_interfaces.get("door_lock")

    await self._assert_required_methods(
      nimbus_core_address,
      object_name="NimbusCore",
      required_method_ids=self._REQUIRED_METHODS_CORE,
    )
    await self._assert_required_methods(
      pipette_address,
      object_name="Pipette",
      required_method_ids=self._REQUIRED_METHODS_PIPETTE,
    )

    # Query channel configuration
    config = await self.send_command(GetChannelConfiguration_1(nimbus_core_address))
    assert config is not None, "GetChannelConfiguration_1 command returned None"
    num_channels = config["channels"]
    logger.info(f"Channel configuration: {num_channels} channels")

    # Create backends — each object stores its own address and state
    self.pip = NimbusPIPBackend(
      driver=self, deck=params.deck, address=pipette_address, num_channels=num_channels
    )

    if door_address is not None:
      self.door = NimbusDoor(driver=self, address=door_address)
    elif params.require_door_lock:
      raise RuntimeError("DoorLock is required but not available on this instrument.")

    # Initialize subsystems
    if self.door is not None:
      await self.door._on_setup()

  async def stop(self):
    """Stop driver and close connection."""
    if self.door is not None:
      await self.door._on_stop()
    await super().stop()
    self.door = None
    self._resolved_interfaces = {}

  async def _discover_instrument_objects(self) -> Dict[str, Address]:
    """Discover instrument-specific objects using introspection.

    Returns:
      Dictionary mapping object names (e.g. "Pipette", "DoorLock") to their addresses.
    """
    addresses: Dict[str, Address] = {}

    root_objects = self.get_root_object_addresses()
    if not root_objects:
      raise RuntimeError("No root objects discovered during setup.")

    nimbus_core_addr = root_objects[0]
    root_info = await self.introspection.get_object(nimbus_core_addr)
    if "nimbus" not in root_info.name.lower():
      raise RuntimeError(
        f"Expected a Nimbus root object, but discovered '{root_info.name}'. Wrong instrument?"
      )
    addresses[root_info.name] = nimbus_core_addr
    self._nimbus_core_address = nimbus_core_addr

    for i in range(root_info.subobject_count):
      try:
        sub_addr = await self.introspection.get_subobject_address(nimbus_core_addr, i)
        sub_info = await self.introspection.get_object(sub_addr)
        addresses[sub_info.name] = sub_addr
        logger.info(f"Found {sub_info.name} at {sub_addr}")
      except Exception as e:
        logger.debug(f"Failed to get subobject {i}: {e}")

    if "DoorLock" not in addresses:
      logger.info("DoorLock not available on this instrument")

    return addresses

  async def _resolve_interfaces(self, discovered: Dict[str, Address]) -> None:
    self._resolved_interfaces = {}
    for key, spec in self._INTERFACES.items():
      addr = discovered.get(spec.object_name)
      if addr is None:
        if spec.required:
          raise RuntimeError(
            f"Could not find required interface '{key}' ({spec.object_name}) on Nimbus."
          )
        self._resolved_interfaces[key] = None
      else:
        self._resolved_interfaces[key] = addr

  async def _require_interface(self, name: str) -> Address:
    if name not in self._INTERFACES:
      raise KeyError(f"Unknown interface: {name}")

    spec = self._INTERFACES[name]
    addr = self._resolved_interfaces.get(name)
    if addr is None:
      msg = f"Could not find interface '{name}' ({spec.object_name}) on Nimbus."
      if spec.raise_when_missing:
        logger.warning(msg)
      raise RuntimeError(msg)
    return addr

  async def _assert_required_methods(
    self,
    address: Address,
    *,
    object_name: str,
    required_method_ids: Set[int],
  ) -> None:
    methods = await self.introspection.methods_for_interface(address, interface_id=1)
    available = {m.method_id for m in methods}
    missing = sorted(required_method_ids - available)
    if missing:
      raise RuntimeError(
        f"{object_name} is missing required interface-1 methods: {missing}. "
        "Firmware is incompatible with Nimbus v1 backend requirements."
      )

  async def park(self):
    """Park the instrument."""
    await self.send_command(Park(self.nimbus_core_address))
    logger.info("Instrument parked successfully")
