"""Inheco ODTC (On-Deck Thermocycler) resource class."""

from typing import Optional

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.thermocycling.thermocycler import Thermocycler

from .odtc_backend import ODTCBackend


class InhecoODTC96(Thermocycler):
  """Inheco ODTC 96-well On-Deck Thermocycler.

  The ODTC is a compact thermocycler designed for integration into liquid handling
  systems. It features a motorized drawer for plate access and supports PCR protocols
  via XML-defined methods.

  Approximate dimensions:
  - Width (X): 147 mm
  - Depth (Y): 298 mm
  - Height (Z): 130 mm (with drawer closed)

  Example usage:
    ```python
    from pylabrobot.thermocycling.inheco import InhecoODTC96, ODTCBackend

    # Create backend and thermocycler
    backend = ODTCBackend(odtc_ip="192.168.1.100")
    tc = InhecoODTC96(name="odtc1", backend=backend)

    # Initialize
    await tc.setup()

    # Upload and run protocols
    await backend.upload_method_set_from_file("protocols.xml")
    await backend.execute_method("PRE25")  # Set initial temperatures
    await backend.execute_method("PCR_30cycles")  # Run PCR

    # Read temperatures
    temp = await tc.get_block_current_temperature()
    print(f"Block temperature: {temp[0]}°C")

    # Clean up
    await tc.stop()
    ```
  """

  def __init__(
    self,
    name: str,
    backend: ODTCBackend,
    child_location: Coordinate = Coordinate(x=10.0, y=10.0, z=50.0),
    child: Optional[ItemizedResource] = None,
  ):
    """Initialize the ODTC thermocycler.

    Args:
      name: Human-readable name for this resource.
      backend: ODTCBackend instance configured with device IP.
      child_location: Position where a plate sits on the block.
        Defaults to approximate center of the block area.
      child: Optional plate/rack already loaded on the module.
    """
    super().__init__(
      name=name,
      size_x=147.0,  # mm - approximate width
      size_y=298.0,  # mm - approximate depth (includes drawer travel)
      size_z=130.0,  # mm - approximate height with drawer closed
      backend=backend,
      child_location=child_location,
      category="thermocycler",
      model="InhecoODTC96",
    )

    self.backend: ODTCBackend = backend
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

  async def execute_method(self, method_name: str) -> None:
    """Execute a method or premethod by name.

    Args:
      method_name: Name of the method or premethod to execute.
    """
    await self.backend.execute_method(method_name)

  async def stop_method(self) -> None:
    """Stop any currently running method."""
    await self.backend.stop_method()

  async def list_methods(self) -> tuple:
    """Return (premethod_names, method_names) available on device."""
    return await self.backend.list_methods()

  async def upload_method_set_from_file(self, filepath: str) -> None:
    """Load a MethodSet XML file and upload to device."""
    await self.backend.upload_method_set_from_file(filepath)

  async def save_method_set_to_file(self, filepath: str) -> None:
    """Download methods from device and save to file."""
    await self.backend.save_method_set_to_file(filepath)

  async def read_temperatures(self):
    """Read all temperature sensors.

    Returns:
      ODTCSensorValues with temperatures in °C.
    """
    return await self.backend.read_temperatures()


class InhecoODTC384(Thermocycler):
  """Inheco ODTC 384-well On-Deck Thermocycler.

  Similar to the 96-well variant but configured for 384-well plates.
  """

  def __init__(
    self,
    name: str,
    backend: ODTCBackend,
    child_location: Coordinate = Coordinate(x=10.0, y=10.0, z=50.0),
    child: Optional[ItemizedResource] = None,
  ):
    """Initialize the ODTC 384-well thermocycler.

    Args:
      name: Human-readable name for this resource.
      backend: ODTCBackend instance configured with device IP.
      child_location: Position where a plate sits on the block.
      child: Optional plate/rack already loaded on the module.
    """
    super().__init__(
      name=name,
      size_x=147.0,  # mm - approximate width
      size_y=298.0,  # mm - approximate depth
      size_z=130.0,  # mm - approximate height
      backend=backend,
      child_location=child_location,
      category="thermocycler",
      model="InhecoODTC384",
    )

    self.backend: ODTCBackend = backend
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

  # Convenience methods (same as 96-well)

  async def execute_method(self, method_name: str) -> None:
    """Execute a method or premethod by name."""
    await self.backend.execute_method(method_name)

  async def stop_method(self) -> None:
    """Stop any currently running method."""
    await self.backend.stop_method()

  async def list_methods(self) -> tuple:
    """Return (premethod_names, method_names) available on device."""
    return await self.backend.list_methods()

  async def upload_method_set_from_file(self, filepath: str) -> None:
    """Load a MethodSet XML file and upload to device."""
    await self.backend.upload_method_set_from_file(filepath)

  async def save_method_set_to_file(self, filepath: str) -> None:
    """Download methods from device and save to file."""
    await self.backend.save_method_set_to_file(filepath)

  async def read_temperatures(self):
    """Read all temperature sensors."""
    return await self.backend.read_temperatures()
