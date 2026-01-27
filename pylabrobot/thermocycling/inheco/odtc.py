"""Inheco ODTC (On-Deck Thermocycler) resource class."""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Union

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.thermocycling.thermocycler import Thermocycler

from .odtc_backend import CommandExecution, MethodExecution, ODTCBackend
from .odtc_xml import ODTCMethod, ODTCPreMethod, ODTCConfig

if TYPE_CHECKING:
  from pylabrobot.thermocycling.standard import Protocol

logger = logging.getLogger(__name__)


# Mapping from model string to variant integer (960000 for 96-well, 384000 for 384-well)
_MODEL_TO_VARIANT: Dict[str, int] = {
  "96": 960000,
  "384": 384000,
}


def _volume_to_fluid_quantity(volume_ul: float) -> int:
  """Map volume in µL to ODTC fluid_quantity code.

  Args:
    volume_ul: Volume in microliters.

  Returns:
    fluid_quantity code: 0 (10-29ul), 1 (30-74ul), or 2 (75-100ul).

  Raises:
    ValueError: If volume > 100 µL.
  """
  if volume_ul > 100:
    raise ValueError(
      f"Volume {volume_ul} µL exceeds ODTC maximum of 100 µL. "
      "Please use a volume between 0-100 µL."
    )
  elif volume_ul <= 29:
    return 0  # 10-29ul
  elif volume_ul <= 74:
    return 1  # 30-74ul
  else:  # 75 <= volume_ul <= 100
    return 2  # 75-100ul


def _validate_volume_fluid_quantity(
  volume_ul: float,
  fluid_quantity: int,
  is_premethod: bool = False,
) -> None:
  """Validate that volume matches fluid_quantity and warn if mismatch.

  Args:
    volume_ul: Volume in microliters.
    fluid_quantity: ODTC fluid_quantity code (0, 1, or 2).
    is_premethod: If True, suppress warnings for volume=0 (premethods don't need volume).
  """
  if volume_ul <= 0:
    if not is_premethod:
      logger.warning(
        f"block_max_volume={volume_ul} µL is invalid. Using default fluid_quantity=1 (30-74ul). "
        "Please provide a valid volume for accurate thermal calibration."
      )
    return

  if volume_ul > 100:
    raise ValueError(
      f"Volume {volume_ul} µL exceeds ODTC maximum of 100 µL. "
      "Please use a volume between 0-100 µL."
    )

  # Check if volume matches fluid_quantity
  expected_fluid_quantity = _volume_to_fluid_quantity(volume_ul)
  if fluid_quantity != expected_fluid_quantity:
    volume_ranges = {
      0: "10-29 µL",
      1: "30-74 µL",
      2: "75-100 µL",
    }
    logger.warning(
      f"Volume mismatch: block_max_volume={volume_ul} µL suggests fluid_quantity={expected_fluid_quantity} "
      f"({volume_ranges[expected_fluid_quantity]}), but config has fluid_quantity={fluid_quantity} "
      f"({volume_ranges.get(fluid_quantity, 'unknown')}). This may affect thermal calibration accuracy."
    )


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
    from pylabrobot.thermocycling.standard import Protocol, Stage, Step

    # Create backend and thermocycler (384-well)
    backend = ODTCBackend(odtc_ip="192.168.1.100")
    tc = InhecoODTC(name="odtc1", backend=backend, model="384")

    # Initialize
    await tc.setup()

    # Create a protocol
    protocol = Protocol(stages=[
      Stage(steps=[Step(temperature=[95.0], hold_seconds=30.0)], repeats=1)
    ])

    # Upload protocol (uses model's variant 384000 automatically)
    await tc.upload_protocol(protocol, name="my_method")

    # Run method by name
    await tc.run_protocol(method_name="my_method")

    # Or upload and run in one call
    await tc.run_protocol(protocol=protocol, method_name="my_pcr")

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

  # Protocol management methods

  async def upload_protocol(
    self,
    protocol: "Protocol",
    config: Optional["ODTCConfig"] = None,
    name: Optional[str] = None,
    allow_overwrite: bool = False,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
    block_max_volume: Optional[float] = None,
  ) -> str:
    """Upload a Protocol to the device.

    Args:
      protocol: PyLabRobot Protocol to upload.
      config: Optional ODTCConfig for device-specific parameters. If None, uses
        model-aware defaults (variant matches thermocycler model).
      name: Method name. If None, uses "plr_currentProtocol".
      allow_overwrite: If False, raise ValueError if method name already exists.
      debug_xml: If True, log the generated XML to the logger at DEBUG level.
        Useful for troubleshooting validation errors.
      xml_output_path: Optional file path to save the generated MethodSet XML.
        If provided, the XML will be written to this file before upload.
      block_max_volume: Optional volume in µL. If provided and config is None, used to set
        fluid_quantity. If config is provided, validates volume matches fluid_quantity.

    Returns:
      Method name (resolved name, may be scratch name if not provided).

    Raises:
      ValueError: If allow_overwrite=False and method name already exists.
      ValueError: If block_max_volume > 100 µL.
    """
    from .odtc_xml import protocol_to_odtc_method

    # Use model-aware defaults if config not provided
    if config is None:
      if block_max_volume is not None and block_max_volume > 0 and block_max_volume <= 100:
        fluid_quantity = _volume_to_fluid_quantity(block_max_volume)
        config = self.get_default_config(fluid_quantity=fluid_quantity)
      else:
        config = self.get_default_config()
    elif block_max_volume is not None:
      # Config provided - validate volume matches fluid_quantity
      _validate_volume_fluid_quantity(block_max_volume, config.fluid_quantity, is_premethod=False)

    # Set name in config if provided
    if name is not None:
      config.name = name

    # Convert Protocol to ODTCMethod in resource layer
    method = protocol_to_odtc_method(protocol, config=config)

    # Upload method to backend
    await self.backend.upload_method(
      method,
      allow_overwrite=allow_overwrite,
      execute=False,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )

    return method.name

  async def run_protocol(
    self,
    protocol: Optional["Protocol"] = None,
    block_max_volume: float = 0.0,
    config: Optional["ODTCConfig"] = None,
    method_name: Optional[str] = None,
    wait: bool = True,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
    **backend_kwargs: Any,
  ) -> Optional[MethodExecution]:
    """Run a protocol or method on the device.

    If protocol is provided:
        - Converts Protocol to ODTCMethod
        - Uploads it with name method_name (or uses scratch name "plr_currentProtocol" if method_name=None)
        - Executes the method by the resolved name

    If only method_name is provided:
        - Executes existing Method on device by that name

    Args:
      protocol: Optional Protocol to convert and execute. If None, method_name must be provided.
      block_max_volume: Maximum block volume (µL) for safety. Used to set fluid_quantity in
        ODTCConfig when config is None. Must be between 0-100 µL. If 0, uses default
        fluid_quantity=1 (30-74ul) with a warning. If >100, raises ValueError.
      config: Optional ODTCConfig for device-specific parameters. If None and protocol provided,
        uses model-aware defaults.
      method_name: Name of Method to execute. If protocol provided, this is the name for the
        uploaded method. If only method_name provided, this is the existing method to run.
        If None and protocol provided, uses "plr_currentProtocol".
      wait: If True, block until completion. If False, return MethodExecution handle.
      debug_xml: If True, log the generated XML to the logger at DEBUG level.
        Useful for troubleshooting validation errors.
      xml_output_path: Optional file path to save the generated MethodSet XML.
        If provided, the XML will be written to this file before upload.
      **backend_kwargs: Additional backend-specific parameters (unused for ODTC).

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: MethodExecution handle (awaitable, has request_id)

    Raises:
      ValueError: If neither protocol nor method_name is provided.
    """
    if protocol is not None:
      # Convert, upload, and execute protocol
      if config is None:
        # Use block_max_volume to set fluid_quantity if volume is valid
        if block_max_volume > 0 and block_max_volume <= 100:
          fluid_quantity = _volume_to_fluid_quantity(block_max_volume)
          config = self.get_default_config(fluid_quantity=fluid_quantity)
        elif block_max_volume == 0:
          # Use default but warn
          logger.warning(
            f"block_max_volume={block_max_volume} µL is invalid. Using default fluid_quantity=1 (30-74ul). "
            "Please provide a valid volume for accurate thermal calibration."
          )
          config = self.get_default_config()
        else:  # block_max_volume > 100
          raise ValueError(
            f"Volume {block_max_volume} µL exceeds ODTC maximum of 100 µL. "
            "Please use a volume between 0-100 µL."
          )
      else:
        # Config provided - validate volume matches fluid_quantity (only if volume > 0)
        if block_max_volume > 0:
          _validate_volume_fluid_quantity(block_max_volume, config.fluid_quantity, is_premethod=False)

      # Upload with allow_overwrite=True since we're about to execute it
      # Pass block_max_volume only if > 0 to avoid triggering validation warnings in upload_protocol
      resolved_method_name = await self.upload_protocol(
        protocol,
        config=config,
        name=method_name,
        allow_overwrite=True,
        debug_xml=debug_xml,
        xml_output_path=xml_output_path,
        block_max_volume=block_max_volume if block_max_volume > 0 else None,
      )
      return await self.backend.execute_method(resolved_method_name, wait=wait)
    elif method_name is not None:
      # Execute existing method by name
      return await self.backend.execute_method(method_name, wait=wait)
    else:
      raise ValueError("Either protocol or method_name must be provided")

  async def get_method_set(self):
    """Get the full MethodSet from the device.

    Returns:
      ODTCMethodSet containing all methods and premethods.
    """
    return await self.backend.get_method_set()

  async def get_method(self, name: str) -> Optional[Union[ODTCMethod, ODTCPreMethod]]:
    """Get a method by name from the device (searches both methods and premethods).

    Args:
      name: Method name to retrieve.

    Returns:
      ODTCMethod or ODTCPreMethod if found, None otherwise.
    """
    return await self.backend.get_method_by_name(name)

  async def list_methods(self) -> List[str]:
    """List all method names (both methods and premethods) on the device.

    Returns:
      List of method names.
    """
    return await self.backend.list_method_names()

  async def stop_method(self, wait: bool = True) -> Optional[CommandExecution]:
    """Stop any currently running method.

    Args:
      wait: If True, block until completion. If False, return CommandExecution handle.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: CommandExecution handle (awaitable, has request_id)
    """
    return await self.backend.stop_method(wait=wait)

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

  async def set_mount_temperature(
    self,
    temperature: float,
    lid_temperature: Optional[float] = None,
    wait: bool = True,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
  ) -> Optional[MethodExecution]:
    """Set mount (block) temperature and hold it.

    Creates and executes a PreMethod to set the mount and lid temperatures.
    PreMethods are simpler than full Methods and are designed for temperature conditioning.

    Args:
      temperature: Target mount (block) temperature in °C.
      lid_temperature: Optional lid temperature in °C. If None, uses hardware-defined
        default (max_lid_temp: 110°C for 96-well, 115°C for 384-well).
      wait: If True, block until temperatures are set. If False, return MethodExecution handle.
      debug_xml: If True, log the generated XML to the logger at DEBUG level.
      xml_output_path: Optional file path to save the generated MethodSet XML.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: MethodExecution handle (awaitable, has request_id)

    Example:
      ```python
      # Set mount to 95°C with default lid temperature (110°C) - blocking
      await tc.set_mount_temperature(95.0)

      # Set mount to 95°C with custom lid temperature - blocking
      await tc.set_mount_temperature(95.0, lid_temperature=115.0)

      # Non-blocking - returns MethodExecution handle
      execution = await tc.set_mount_temperature(95.0, wait=False)
      # Do other work...
      await execution  # Wait when ready
      ```
    """
    from .odtc_xml import ODTCPreMethod, generate_odtc_timestamp, resolve_protocol_name

    # Use default lid temperature if not specified
    if lid_temperature is not None:
      target_lid_temp = lid_temperature
    else:
      # Use hardware-defined max as default (110°C for 96-well, 115°C for 384-well)
      constraints = self.get_constraints()
      target_lid_temp = constraints.max_lid_temp

    # Create PreMethod - much simpler than a full Method
    # PreMethods just set target temperatures and hold them
    # Note: PreMethods don't need volume/fluid_quantity (they're for temperature conditioning)
    premethod = ODTCPreMethod(
      name=resolve_protocol_name(None),  # Uses "plr_currentProtocol"
      target_block_temperature=temperature,
      target_lid_temperature=target_lid_temp,
      datetime=generate_odtc_timestamp(),
    )

    # Upload PreMethod to backend
    await self.backend.upload_premethod(
      premethod,
      allow_overwrite=True,  # Always overwrite scratch name
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )

    # Execute the PreMethod (same command as Methods)
    return await self.backend.execute_method(premethod.name, wait=wait)

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
