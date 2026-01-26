"""Inheco ODTC (On-Deck Thermocycler) resource class."""

from typing import Any, Dict, List, Literal, Optional

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.thermocycling.thermocycler import Thermocycler

from .odtc_backend import CommandExecution, MethodExecution, ODTCBackend
from .odtc_xml import ODTCMethodSet, ODTCConfig


# Mapping from model string to variant integer (960000 for 96-well, 384000 for 384-well)
_MODEL_TO_VARIANT: Dict[str, int] = {
  "96": 960000,
  "384": 384000,
}


class InhecoODTC(Thermocycler):
  """Inheco ODTC (On-Deck Thermocycler).

  The ODTC is a compact thermocycler designed for integration into liquid handling
  systems. It features a motorized drawer for plate access and supports PCR protocols
  via XML-defined methods.

  Available models:
  - "96": 96-well plate format (variant=960000)
  - "384": 384-well plate format (variant=384000)

  The model parameter affects:
  - Default hardware constraints (max heating slope, max lid temp)
  - Default variant used in protocol conversion
  - Resource identification in PyLabRobot

  Approximate dimensions:
  - Width (X): 147 mm
  - Depth (Y): 298 mm
  - Height (Z): 130 mm (with drawer closed)

  Example usage:
    ```python
    from pylabrobot.thermocycling.inheco import InhecoODTC, ODTCBackend
    from pylabrobot.thermocycling.inheco.odtc_xml import protocol_to_odtc_method

    # Create backend and thermocycler (384-well)
    backend = ODTCBackend(odtc_ip="192.168.1.100")
    tc = InhecoODTC(name="odtc1", backend=backend, model="384")

    # Initialize
    await tc.setup()

    # Create a protocol with model-aware defaults
    from pylabrobot.thermocycling.standard import Protocol, Stage, Step
    protocol = Protocol(stages=[
      Stage(steps=[Step(temperature=[95.0], hold_seconds=30.0)], repeats=1)
    ])

    # Convert to ODTC method using model's variant (384000)
    config = tc.get_default_config(name="my_protocol")
    method = protocol_to_odtc_method(protocol, config=config)
    # method.variant will be 384000 (not 960000)

    # Upload and execute
    await tc.upload_method_set(ODTCMethodSet(methods=[method], premethods=[]))
    await tc.execute_method("my_protocol")

    # Clean up
    await tc.stop()
    ```
  """

  def __init__(
    self,
    name: str,
    backend: ODTCBackend,
    model: Literal["96", "384"] = "96",
    child_location: Coordinate = Coordinate(x=10.0, y=10.0, z=50.0),
    child: Optional[ItemizedResource] = None,
  ):
    """Initialize the ODTC thermocycler.

    Args:
      name: Human-readable name for this resource.
      backend: ODTCBackend instance configured with device IP.
      model: ODTC model variant - "96" for 96-well or "384" for 384-well format.
      child_location: Position where a plate sits on the block.
        Defaults to approximate center of the block area.
      child: Optional plate/rack already loaded on the module.
    """
    model_name = f"InhecoODTC{model}"
    super().__init__(
      name=name,
      size_x=147.0,  # mm - approximate width
      size_y=298.0,  # mm - approximate depth (includes drawer travel)
      size_z=130.0,  # mm - approximate height with drawer closed
      backend=backend,
      child_location=child_location,
      category="thermocycler",
      model=model_name,
    )

    self.backend: ODTCBackend = backend
    self.model: Literal["96", "384"] = model
    # Get variant integer from model string via lookup
    self._variant: int = _MODEL_TO_VARIANT[model]
    self.child = child
    if child is not None:
      self.assign_child_resource(child, location=child_location)

  def serialize(self) -> dict:
    """Return a serialized representation of the thermocycler."""
    return {
      **super().serialize(),
      "odtc_ip": self.backend._sila._machine_ip,
      "port": self.backend._sila.bound_port,
    }

  # Convenience methods that expose ODTC-specific functionality

  async def execute_method(
    self,
    method_name: str,
    priority: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[MethodExecution]:
    """Execute a method or premethod by name.

    Args:
      method_name: Name of the method or premethod to execute.
      priority: Priority (not used by ODTC, but part of SiLA spec).
      wait: If True, block until completion. If False, return MethodExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: MethodExecution handle (awaitable, has request_id)
    """
    return await self.backend.execute_method(method_name, priority, wait)

  async def stop_method(self, wait: bool = True) -> Optional[CommandExecution]:
    """Stop any currently running method.

    Args:
      wait: If True, block until completion. If False, return CommandExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: CommandExecution handle (awaitable, has request_id)
    """
    return await self.backend.stop_method(wait=wait)

  async def get_method_set(self):
    """Get the full MethodSet from the device."""
    return await self.backend.get_method_set()

  async def get_method_by_name(self, method_name: str):
    """Get a specific method by name from the device."""
    return await self.backend.get_method_by_name(method_name)

  async def is_method_running(self) -> bool:
    """Check if a method is currently running."""
    return await self.backend.is_method_running()

  async def wait_for_method_completion(
    self,
    poll_interval: float = 5.0,
    timeout: Optional[float] = None,
  ) -> None:
    """Wait until method execution completes."""
    await self.backend.wait_for_method_completion(poll_interval, timeout)

  async def upload_method_set_from_file(self, filepath: str) -> None:
    """Load a MethodSet XML file and upload to device."""
    await self.backend.upload_method_set_from_file(filepath)

  async def save_method_set_to_file(self, filepath: str) -> None:
    """Download methods from device and save to file."""
    await self.backend.save_method_set_to_file(filepath)

  async def get_status(self) -> str:
    """Get device status state.

    Returns:
      Device state string (e.g., "idle", "busy", "standby").
    """
    return await self.backend.get_status()

  async def read_temperatures(self):
    """Read all temperature sensors.

    Returns:
      ODTCSensorValues with temperatures in °C.
    """
    return await self.backend.read_temperatures()

  # Device control methods

  async def initialize(self, wait: bool = True) -> Optional[CommandExecution]:
    """Initialize the device (must be in standby state).

    Args:
      wait: If True, block until completion. If False, return CommandExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: CommandExecution handle (awaitable, has request_id)
    """
    return await self.backend.initialize(wait=wait)

  async def reset(
    self,
    device_id: str = "ODTC",
    event_receiver_uri: Optional[str] = None,
    simulation_mode: bool = False,
    wait: bool = True,
  ) -> Optional[CommandExecution]:
    """Reset the device.

    Args:
      device_id: Device identifier.
      event_receiver_uri: Event receiver URI (auto-detected if None).
      simulation_mode: Enable simulation mode.
      wait: If True, block until completion. If False, return CommandExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: CommandExecution handle (awaitable, has request_id)
    """
    return await self.backend.reset(device_id, event_receiver_uri, simulation_mode, wait=wait)

  async def get_device_identification(self) -> dict:
    """Get device identification information.

    Returns:
      Device identification dictionary.
    """
    return await self.backend.get_device_identification()

  async def lock_device(self, lock_id: str, lock_timeout: Optional[float] = None, wait: bool = True) -> Optional[CommandExecution]:
    """Lock the device for exclusive access.

    Args:
      lock_id: Unique lock identifier.
      lock_timeout: Lock timeout in seconds (optional).
      wait: If True, block until completion. If False, return CommandExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: CommandExecution handle (awaitable, has request_id)
    """
    return await self.backend.lock_device(lock_id, lock_timeout, wait=wait)

  async def unlock_device(self, wait: bool = True) -> Optional[CommandExecution]:
    """Unlock the device.

    Args:
      wait: If True, block until completion. If False, return CommandExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: CommandExecution handle (awaitable, has request_id)
    """
    return await self.backend.unlock_device(wait=wait)

  # Door control methods

  async def open_door(self, wait: bool = True) -> Optional[CommandExecution]:
    """Open the drawer door.

    Args:
      wait: If True, block until completion. If False, return CommandExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: CommandExecution handle (awaitable, has request_id)
    """
    return await self.backend.open_door(wait=wait)

  async def close_door(self, wait: bool = True) -> Optional[CommandExecution]:
    """Close the drawer door.

    Args:
      wait: If True, block until completion. If False, return CommandExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: CommandExecution handle (awaitable, has request_id)
    """
    return await self.backend.close_door(wait=wait)

  # Data retrieval methods

  async def get_data_events(
    self, request_id: Optional[int] = None
  ) -> Dict[int, List[Dict[str, Any]]]:
    """Get collected DataEvents.

    Args:
      request_id: If provided, return events for this request_id only.
          If None, return all collected events.

    Returns:
      Dict mapping request_id to list of DataEvent payloads.
    """
    return await self.backend.get_data_events(request_id)

  async def get_last_data(self) -> str:
    """Get temperature trace of last executed method (CSV format).

    Returns:
      CSV string with temperature trace data.
    """
    return await self.backend.get_last_data()

  # Method upload methods

  async def upload_method_set(self, method_set: ODTCMethodSet) -> None:
    """Upload a MethodSet to the device.

    Args:
      method_set: ODTCMethodSet to upload.
    """
    await self.backend.upload_method_set(method_set)

  # Protocol conversion helpers with model-aware defaults

  def get_default_config(self, **kwargs) -> ODTCConfig:
    """Get a default ODTCConfig with variant set to match this thermocycler's model.

    Args:
      **kwargs: Additional parameters to override defaults (e.g., name, lid_temperature).

    Returns:
      ODTCConfig with variant matching the thermocycler model (96 or 384-well).

    Example:
      ```python
      # For a 384-well ODTC, this returns config with variant=384000
      config = tc.get_default_config(name="my_protocol", lid_temperature=115.0)
      method = protocol_to_odtc_method(protocol, config=config)
      ```
    """
    return ODTCConfig(variant=self._variant, **kwargs)

  def get_constraints(self):
    """Get hardware constraints for this thermocycler's model.

    Returns:
      ODTCHardwareConstraints for the current model (96 or 384-well).

    Example:
      ```python
      constraints = tc.get_constraints()
      print(f"Max heating slope: {constraints.max_heating_slope} °C/s")
      print(f"Max lid temp: {constraints.max_lid_temp} °C")
      ```
    """
    from .odtc_xml import get_constraints
    return get_constraints(self._variant)
