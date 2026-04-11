from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.resources import Plate

from .backend import PlateWasher96Backend


class PlateWasher96(Capability):
  """Plate washing capability."""

  def __init__(self, backend: PlateWasher96Backend):
    super().__init__(backend=backend)
    self.backend: PlateWasher96Backend = backend
    self._plate: Optional[Plate] = None

  @property
  def plate(self) -> Plate:
    if self._plate is None:
      raise RuntimeError("No plate assigned to this capability.")
    return self._plate

  @plate.setter
  def plate(self, value: Optional[Plate]):
    if value is not None and self._plate is not None:
      raise RuntimeError(f"A plate is already assigned ({self._plate.name}). Unassign it first.")
    self._plate = value

  @need_capability_ready
  async def aspirate(
    self,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Aspirate (remove) liquid from all wells."""
    await self.backend.aspirate(plate=self.plate, backend_params=backend_params)

  @need_capability_ready
  async def dispense(
    self,
    volume: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid into all wells.

    Args:
      volume: Volume per well in uL.
      backend_params: Backend-specific parameters.
    """
    await self.backend.dispense(plate=self.plate, volume=volume, backend_params=backend_params)

  @need_capability_ready
  async def wash(
    self,
    cycles: int = 3,
    dispense_volume: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Perform wash cycles (repeated dispense + aspirate).

    Args:
      cycles: Number of wash cycles.
      dispense_volume: Volume per well per cycle in uL. If None, use device default.
      backend_params: Backend-specific parameters.
    """
    await self.backend.wash(
      plate=self.plate,
      cycles=cycles,
      dispense_volume=dispense_volume,
      backend_params=backend_params,
    )

  @need_capability_ready
  async def prime(
    self,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Prime fluid lines."""
    await self.backend.prime(plate=self.plate, backend_params=backend_params)
