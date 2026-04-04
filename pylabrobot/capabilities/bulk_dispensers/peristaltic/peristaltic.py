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
    self._plate: Optional[Plate] = None

  @property
  def plate(self) -> Plate:
    if self._plate is None:
      raise RuntimeError("No plate assigned to this capability.")
    return self._plate

  @plate.setter
  def plate(self, value: Optional[Plate]):
    if value is not None and self._plate is not None:
      raise RuntimeError(
        f"A plate is already assigned ({self._plate.name}). Unassign it first."
      )
    self._plate = value

  @need_capability_ready
  async def dispense(
    self,
    volumes: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid using the peristaltic pump.

    Args:
      volumes: Mapping of 1-indexed column number to volume in uL.
      backend_params: Backend-specific parameters.
    """
    await self.backend.dispense(plate=self.plate, volumes=volumes, backend_params=backend_params)

  @need_capability_ready
  async def prime(
    self,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Prime peristaltic fluid lines."""
    await self.backend.prime(
      plate=self.plate, volume=volume, duration=duration, backend_params=backend_params
    )

  @need_capability_ready
  async def purge(
    self,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Purge peristaltic fluid lines."""
    await self.backend.purge(
      plate=self.plate, volume=volume, duration=duration, backend_params=backend_params
    )
