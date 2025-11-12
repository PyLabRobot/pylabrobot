import asyncio
from typing import Dict

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Coordinate, Resource, ResourceHolder
from pylabrobot.storage.inheco import InhecoIncubatorShakerBackend, InhecoIncubatorShakerUnit


class IncubatorShakerStack(Resource, Machine):
  """Frontend for a stack of INHECO Incubator/Shaker units.

  - Combines Carrier (geometric resource) and Machine (device lifecycle).
  - Owns a backend instance for serial communication.
  - Handles sequential (not concurrent) setup/teardown across all units.
  """

  def __init__(self, backend: InhecoIncubatorShakerBackend):
    Resource.__init__(
      self,
      name="inheco_incubator_shaker_stack",
      size_x=149.0,
      size_y=268.5,
      size_z=58.0,  # MP: 58, Shaker MP: 88.5, DWP: 104, Shaker DWP: 139 mm
      category="incubator_shaker_stack",
    )

    Machine.__init__(self, backend=backend)
    self.backend: InhecoIncubatorShakerBackend = backend
    self.units: Dict[int, InhecoIncubatorShakerUnit] = {}
    self.loading_trays: Dict[int, ResourceHolder] = {}

  @property
  def size_x(self) -> float:
    return self._size_x

  @property
  def size_y(self) -> float:
    return self._size_y

  @property
  def size_z(self) -> float:
    return self._size_z

  # ------------------------------------------------------------------------
  # Lifecycle
  # ------------------------------------------------------------------------

  incubator_size_z_dict = {
    "incubator_mp": 58.0,
    "incubator_shaker_mp": 88.5,
    "incubator_dwp": 104,
    "incubator_shaker_dwp": 139,
  }
  incubator_loading_tray_location = {  # TODO: rough measurements, verify
    "incubator_mp": None,
    "incubator_shaker_mp": Coordinate(x=30.5, y=-150.5, z=51.2),
    "incubator_dwp": None,
    "incubator_shaker_dwp": Coordinate(x=30.5, y=-150.5, z=51.2),
  }

  async def setup(self, verbose: bool = False):
    """Connect to the stack and build per-unit proxies."""

    await self.backend.setup(verbose=verbose)

    self.num_units = self.backend.number_of_connected_units
    self.unit_composition = self.backend.unit_composition

    # Calculate true stack size
    stack_size_z = 0.0

    for i in range(self.num_units):
      # Create unit proxies
      unit = InhecoIncubatorShakerUnit(self.backend, index=i)
      self.units[i] = unit

      # Create loading tray resources and calculate their locations
      unit_type = self.unit_composition[i]
      unit_size_z = self.incubator_size_z_dict[unit_type]

      loading_tray = ResourceHolder(
        size_x=127.76, size_y=85.48, size_z=0, name=f"unit-{i}-loading-tray"
      )
      self.loading_trays[i] = loading_tray

      self.assign_child_resource(
        loading_tray,
        location=Coordinate(
          x=self.incubator_loading_tray_location[unit_type].x,
          y=self.incubator_loading_tray_location[unit_type].y,
          z=stack_size_z + self.incubator_loading_tray_location[unit_type].z
        ),
      )
      stack_size_z += unit_size_z

    self._size_z = stack_size_z

  async def stop(self):
    """Gracefully stop backend communication."""
    await self.backend.stop()

  @property
  def loading_tray_status(self) -> dict:
    """Carche of loading tray status for all units."""
    return self.backend.loading_tray_status

  @property
  def temperature_control_status(self) -> dict:
    """Cache of temperature control status for all units."""
    return self.backend.temperature_control_status

  @property
  def shaking_status(self) -> dict:
    """Cache of shaking status for all units."""
    return self.backend.shaking_status

  # ------------------------------------------------------------------------
  # Stack to unit master commands
  # ------------------------------------------------------------------------

  async def open_all(self) -> None:
    """Open all units in the stack."""
    for i in range(self.num_units):
      await self.units[i].open()

  async def close_all(self) -> None:
    """Close all units in the stack."""
    for i in range(self.num_units):
      await self.units[i].close()

  async def start_all_temperature_control(self, target_temperature: float) -> None:
    """Start temperature control for all units in the stack."""
    for i in range(self.num_units):
      await self.units[i].start_temperature_control(target_temperature)

  async def get_all_temperatures(self) -> Dict[int, float]:
    """Get current temperature for all units in the stack."""
    temperatures = {}
    for i in range(self.num_units):
      temp = await self.units[i].get_temperature()
      temperatures[i] = temp
    return temperatures

  async def stop_all_temperature_control(self) -> None:
    """Stop temperature control for all units in the stack."""
    for i in range(self.num_units):
      await self.units[i].stop_temperature_control()

  async def start_all_shaking(self, rpm: int) -> None:
    """Start shaking for all units in the stack."""
    for i in range(self.num_units):
      await self.units[i].start_shaking(rpm)

  async def stop_all_shaking(self) -> None:
    """Stop shaking for all units in the stack."""
    for i in range(self.num_units):
      await self.units[i].stop_shaking()

  # ------------------------------------------------------------------------
  # Unit accessors
  # ------------------------------------------------------------------------

  def __getitem__(self, index: int) -> InhecoIncubatorShakerUnit:
    """Access a unit proxy via stack[index]."""
    return self.units[index]

  def __len__(self):
    """Return number of connected units."""
    return len(self.units)
