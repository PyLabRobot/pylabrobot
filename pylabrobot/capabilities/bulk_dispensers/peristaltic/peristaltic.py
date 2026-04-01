from typing import Dict, Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.resources import Plate

from .backend import PeristalticDispensingBackend


class PeristalticDispensing(Capability):
  """Peristaltic dispensing capability.

  See :doc:`/user_guide/capabilities/dispensing/peristaltic` for a walkthrough.
  """

  def __init__(self, backend: PeristalticDispensingBackend):
    super().__init__(backend=backend)
    self.backend: PeristalticDispensingBackend = backend

  @need_capability_ready
  async def dispense(
    self,
    plate: Plate,
    volumes: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid using the peristaltic pump.

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
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Prime peristaltic fluid lines."""
    await self.backend.prime(
      plate=plate, volume=volume, duration=duration, backend_params=backend_params
    )

  @need_capability_ready
  async def purge(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Purge peristaltic fluid lines."""
    await self.backend.purge(
      plate=plate, volume=volume, duration=duration, backend_params=backend_params
    )
