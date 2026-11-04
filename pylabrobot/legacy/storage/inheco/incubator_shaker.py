from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Literal, Optional

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.events import LifespanEventInfo
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, Resource, ResourceHolder

from .incubator_shaker_backend import InhecoIncubatorShakerStackBackend, InhecoIncubatorShakerUnit


class IncubatorShakerStack(Resource, Machine):
  """Frontend for a stack of INHECO Incubator/Shaker units.

  - Combines Carrier (geometric resource) and Machine (device lifecycle).
  - Owns a backend instance for serial communication.
  - Handles sequential (not concurrent) setup/teardown across all units.
  """

  def __init__(self, backend: InhecoIncubatorShakerStackBackend):
    Resource.__init__(
      self,
      name="inheco_incubator_shaker_stack",
      size_x=149.0,
      size_y=268.5,
      size_z=58.0,  # MP: 58, Shaker MP: 88.5, DWP: 104, Shaker DWP: 139 mm
      category="incubator_shaker_stack",
    )

    Machine.__init__(self, backend=backend)
    self.backend: InhecoIncubatorShakerStackBackend = backend
    self.units: list[InhecoIncubatorShakerUnit] = []
    self.loading_trays: list[ResourceHolder] = []

  @property
  def num_units(self) -> int:
    """Return number of connected units in the stack."""
    return len(self.units)

  # ------------------------------------------------------------------------
  # Lifecycle & Resource setup
  # ------------------------------------------------------------------------

  _incubator_size_z_dict = {
    "incubator_mp": 58.0,
    "incubator_shaker_mp": 88.5,
    "incubator_dwp": 104,
    "incubator_shaker_dwp": 139,
  }
  _incubator_loading_tray_location = {  # TODO: rough measurements, verify
    "incubator_mp": None,  # TODO: add when available
    "incubator_shaker_mp": Coordinate(x=30.5, y=-150.5, z=51.2),
    "incubator_dwp": None,  # TODO: add when available
    "incubator_shaker_dwp": Coordinate(x=30.5, y=-150.5, z=51.2),
  }

  _possible_tray_y_coordinates = {
    "open": -150.5,  # TODO: verify by careful testing in controlled geometry setup
    "closed": +24.0,
  }

  _chamber_z_clearance = 2

  _acceptable_plate_z_dimensions = {
    "incubator_mp": 18 - _chamber_z_clearance,
    "incubator_shaker_mp": 50 - _chamber_z_clearance,
    "incubator_dwp": 18 - _chamber_z_clearance,
    "incubator_shaker_dwp": 53 - _chamber_z_clearance,
  }

  _incubator_power_credits_per_type = {
    "incubator_mp": 1.0,
    "incubator_dwp": 1.25,
    "incubator_shaker_mp": 1.6,
    "incubator_shaker_dwp": 2.5,
  }

  def _lifespan_event_info(self, **kwargs: Any) -> Optional[LifespanEventInfo]:
    """This stack owns its backend directly rather than Machine's evented entry."""
    return None

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    """Own the backend directly and build per-unit proxies.

    Machine's entry would enter the same backend again and publish a frontend setup state
    that this stack intentionally does not use.
    """

    await stack.enter_async_context(self.backend.lifespan(**backend_kwargs))

    for loading_tray in list(self.loading_trays):
      if isinstance(loading_tray, Resource) and loading_tray.parent is self:
        self.unassign_child_resource(loading_tray)
    self.units.clear()
    self.loading_trays.clear()
    self.power_credit = 0.0

    # Calculate true stack size
    stack_size_z = 0.0

    for i in range(self.backend.number_of_connected_units):
      # Create unit proxies
      unit = InhecoIncubatorShakerUnit(self.backend, index=i)
      self.units.append(unit)

      # Create loading tray resources and calculate their locations
      unit_type = self.backend.unit_composition[i]
      self.power_credit += self._incubator_power_credits_per_type[unit_type]
      unit_size_z = self._incubator_size_z_dict[unit_type]

      loading_tray = ResourceHolder(
        size_x=127.76, size_y=85.48, size_z=0, name=f"unit-{i}-loading-tray"
      )
      self.loading_trays.append(loading_tray)

      loc = self._incubator_loading_tray_location[unit_type]
      if loc is None:
        raise ValueError(
          f"Loading tray location for unit type {unit_type} is not defined. Cannot set up stack."
        )

      self.assign_child_resource(
        loading_tray,
        location=Coordinate(
          x=loc.x,
          y=self._possible_tray_y_coordinates[
            "closed"
          ],  # setup finishes with all loading trays closed
          z=stack_size_z + loc.z,
        ),
      )
      stack_size_z += unit_size_z

    self._size_z = stack_size_z

    assert self.power_credit < 5, (
      f"Too many units: unit composition {self.backend.unit_composition} is exceeding 5 power credit limit. Reduce number of units."
    )

  async def request_loading_tray_states(self) -> dict:
    """Request loading tray states for all units."""

    return {
      unit_index: await self.backend.request_drawer_status(stack_index=unit_index)
      for unit_index in range(self.num_units)
    }

  async def request_temperature_control_states(self) -> dict:
    """Request temperature control states for all units."""

    return {
      unit_index: await self.backend.is_temperature_control_enabled(stack_index=unit_index)
      for unit_index in range(self.num_units)
    }

  async def request_shaking_states(self) -> dict:
    """Request shaking states for all units."""

    return {
      unit_index: await self.backend.is_shaking_enabled(stack_index=unit_index)
      for unit_index in range(self.num_units)
    }

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

  async def shake(self, *args, **kwargs) -> None:
    """Start shaking for all units in the stack."""
    await self.backend._shake_all(self._shaker_indices(), *args, **kwargs)

  async def stop_all_shaking(self) -> None:
    """Stop shaking for all units in the stack."""
    await self.backend._stop_all_shaking(self._shaker_indices())

  def _shaker_indices(self) -> list[int]:
    """Return every discovered shaker-capable stack index."""
    return [
      index
      for index, unit_type in enumerate(self.backend.unit_composition)
      if "shaker" in unit_type
    ]

  @asynccontextmanager
  async def shaking(
    self,
    *,
    pattern: Literal["linear_x", "linear_y", "orbital", "elliptical", "figure_eight"] = "orbital",
    rpm: Optional[float] = None,
    frequency_hz: Optional[float] = None,
    amplitude_x_mm: float = 3.0,
    amplitude_y_mm: float = 3.0,
    phase_deg: Optional[float] = None,
  ) -> AsyncIterator[None]:
    """Temporarily shake every discovered shaker-capable unit."""
    indices = self._shaker_indices()
    tokens = await self.backend._reserve_shaking_scopes(indices)
    try:
      async with AsyncExitStackWithShielding() as shaking_stack:
        for index in indices:
          await shaking_stack.enter_async_context(
            self.units[index]._shaking_with_token(
              tokens[index],
              pattern=pattern,
              rpm=rpm,
              frequency_hz=frequency_hz,
              amplitude_x_mm=amplitude_x_mm,
              amplitude_y_mm=amplitude_y_mm,
              phase_deg=phase_deg,
            )
          )
        yield
    finally:
      await self.backend._release_shaking_reservations(tokens)

  # ------------------------------------------------------------------------
  # Unit accessors
  # ------------------------------------------------------------------------

  def __getitem__(self, index: int) -> InhecoIncubatorShakerUnit:
    """Access a unit proxy via stack[index]."""
    return self.units[index]

  def __len__(self):
    """Return number of connected units."""
    return len(self.units)
