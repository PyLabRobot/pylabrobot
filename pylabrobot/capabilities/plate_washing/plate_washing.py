from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.resources import Plate

from .backend import PlateWashingBackend


class PlateWashingCapability(Capability):
  """Plate washing capability."""

  def __init__(self, backend: PlateWashingBackend):
    super().__init__(backend=backend)
    self.backend: PlateWashingBackend = backend

  @need_capability_ready
  async def aspirate(
    self,
    plate: Plate,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Aspirate (remove) liquid from all wells."""
    await self.backend.aspirate(plate=plate, backend_params=backend_params)

  @need_capability_ready
  async def dispense(
    self,
    plate: Plate,
    volume: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid into all wells.

    Args:
      plate: Target plate.
      volume: Volume per well in uL.
      backend_params: Backend-specific parameters.
    """
    await self.backend.dispense(plate=plate, volume=volume, backend_params=backend_params)

  @need_capability_ready
  async def wash(
    self,
    plate: Plate,
    cycles: int = 3,
    dispense_volume: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Perform wash cycles (repeated dispense + aspirate).

    Args:
      plate: Target plate.
      cycles: Number of wash cycles.
      dispense_volume: Volume per well per cycle in uL. If None, use device default.
      backend_params: Backend-specific parameters.
    """
    await self.backend.wash(
      plate=plate,
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
    await self.backend.prime(backend_params=backend_params)
