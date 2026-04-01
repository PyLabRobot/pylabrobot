from typing import Dict, Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.resources import Plate

from .backend import SyringeDispensingBackend


class SyringeDispensing(Capability):
  """Syringe dispensing capability."""

  def __init__(self, backend: SyringeDispensingBackend):
    super().__init__(backend=backend)
    self.backend: SyringeDispensingBackend = backend

  @need_capability_ready
  async def dispense(
    self,
    plate: Plate,
    volumes: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid using the syringe pump.

    Args:
      plate: Target plate.
      volumes: Mapping of 1-indexed column number to volume in uL.
      backend_params: Backend-specific parameters.
    """
    await self.backend.dispense(plate=plate, volumes=volumes, backend_params=backend_params)

  @need_capability_ready
  async def prime(
    self,
    plate: Plate,
    volume: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Prime the syringe pump system."""
    await self.backend.prime(plate=plate, volume=volume, backend_params=backend_params)
