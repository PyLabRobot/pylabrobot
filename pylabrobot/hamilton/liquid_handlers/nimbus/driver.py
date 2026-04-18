"""NimbusDriver: TCP-based driver for Hamilton Nimbus liquid handlers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Set, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.hamilton.tcp.client import HamiltonTCPClient
from pylabrobot.hamilton.tcp.error_tables import NIMBUS_ERROR_CODES
from pylabrobot.hamilton.tcp.interface_bundle import InterfacePathSpec, resolve_interface_path_specs
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

from .commands import (
  GetChannelConfiguration_1,
  Park,
)
from .door import NimbusDoor
from .pip_backend import NimbusPIPBackend

logger = logging.getLogger(__name__)


def nimbus_interface_specs_for_root(root_name: str) -> Dict[str, InterfacePathSpec]:
  """Dot-paths under the instrument root (same mechanism as :class:`PrepDriver`)."""
  return {
    "nimbus_core": InterfacePathSpec(root_name, True, True),
    "pipette": InterfacePathSpec(f"{root_name}.Pipette", True, True),
    "door_lock": InterfacePathSpec(f"{root_name}.DoorLock", False, False),
  }


@dataclass(frozen=True)
class NimbusResolvedInterfaces:
  """Concrete Nimbus firmware handles after :meth:`NimbusDriver.setup`."""

  nimbus_core: Address
  pipette: Address
  door_lock: Optional[Address]

  @staticmethod
  def from_resolution_map(m: Mapping[str, Optional[Address]]) -> NimbusResolvedInterfaces:
    nc = m.get("nimbus_core")
    pip = m.get("pipette")
    if nc is None or pip is None:
      raise RuntimeError("internal: missing required Nimbus interfaces")
    return NimbusResolvedInterfaces(
      nimbus_core=nc,
      pipette=pip,
      door_lock=m.get("door_lock"),
    )


@dataclass
class NimbusSetupParams(BackendParams):
  deck: Optional[NimbusDeck] = None
  require_door_lock: bool = False


class NimbusDriver(HamiltonTCPClient):
  """Driver for Hamilton Nimbus liquid handlers.

  Handles TCP communication, hardware discovery via introspection, and
  manages the PIP backend and door subsystem.
  """

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
    self._nimbus_resolved: Optional[NimbusResolvedInterfaces] = None

    self.pip: NimbusPIPBackend  # set in setup()
    self.door: Optional[NimbusDoor] = None  # set in setup() if available

  @property
  def nimbus_interfaces(self) -> NimbusResolvedInterfaces:
    if self._nimbus_resolved is None:
      raise RuntimeError("Nimbus interfaces not resolved. Call setup() first.")
    return self._nimbus_resolved

  @property
  def nimbus_core_address(self) -> Address:
    if self._nimbus_core_address is None:
      raise RuntimeError("Nimbus root address not discovered. Call setup() first.")
    return self._nimbus_core_address

  async def setup(self, backend_params: Optional[BackendParams] = None):
    """Initialize connection, discover hardware, and create backends.

    Args:
      backend_params: Optional :class:`NimbusSetupParams`.
    """
    if not isinstance(backend_params, NimbusSetupParams):
      backend_params = NimbusSetupParams()

    # TCP connection + Protocol 7 + Protocol 3 + root discovery
    await super().setup()

    root_objects = self.get_root_object_addresses()
    if not root_objects:
      raise RuntimeError("No root objects discovered during setup.")

    root_info = await self.introspection.get_object(root_objects[0])
    if "nimbus" not in root_info.name.lower():
      raise RuntimeError(
        f"Expected a Nimbus root object, but discovered '{root_info.name}'. Wrong instrument?"
      )

    specs = nimbus_interface_specs_for_root(root_info.name)
    self._resolved_interfaces = await resolve_interface_path_specs(
      self, specs, instrument_label="Nimbus"
    )
    self._nimbus_resolved = NimbusResolvedInterfaces.from_resolution_map(self._resolved_interfaces)
    self._nimbus_core_address = self._nimbus_resolved.nimbus_core

    nimbus_core_address = self._nimbus_resolved.nimbus_core
    pipette_address = self._nimbus_resolved.pipette
    door_address = self._nimbus_resolved.door_lock

    await self._assert_required_methods(
      nimbus_core_address,
      object_name=root_info.name,
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
      driver=self, deck=backend_params.deck, address=pipette_address, num_channels=num_channels
    )

    if door_address is not None:
      self.door = NimbusDoor(driver=self, address=door_address)
    elif backend_params.require_door_lock:
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
    self._nimbus_resolved = None

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
