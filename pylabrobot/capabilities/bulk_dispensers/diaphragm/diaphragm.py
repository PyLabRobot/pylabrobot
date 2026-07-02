"""User-facing diaphragm dispensing capability."""

from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.resources import Container

from .backend import DiaphragmDispenserBackend


class DiaphragmDispenser(Capability):
  """Diaphragm-based contactless dispensing capability.

  See :doc:`/user_guide/capabilities/dispensing/diaphragm` for a walkthrough.

  Per-container dispensing using a chip with microvalves driven by pressurized
  air. Targets are addressed at the container level — callers pass parallel
  ``containers`` and ``volumes`` lists, one volume per container, in the order
  to be visited. This is the **variable** head-format variant; future 8-channel
  (``DiaphragmDispensing8``) and 96-channel (``DiaphragmDispensing96``)
  variants will follow the same naming convention as the peristaltic and
  syringe capabilities.

  This capability is owned by a :class:`pylabrobot.device.Device`. The parent
  device's driver handles connection lifecycle; the capability becomes ready
  once the device's ``setup()`` completes.

  Example::

      from pylabrobot.formulatrix.mantis import Mantis
      from pylabrobot.formulatrix.mantis.diaphragm_dispenser_backend import (
        MantisDiaphragmDispenserBackend,
      )

      mantis = Mantis(serial_number="M-000438")
      await mantis.setup()
      await mantis.diaphragm_dispenser.dispense(
        containers=[plate["A1"][0], plate["B1"][0]],
        volumes=[5.0, 2.5],
        backend_params=MantisDiaphragmDispenserBackend.DispenseParams(
          chip=3, dispense_z=44.331,
        ),
      )
      await mantis.stop()
  """

  def __init__(self, backend: DiaphragmDispenserBackend):
    super().__init__(backend=backend)
    self.backend: DiaphragmDispenserBackend = backend

  @need_capability_ready
  async def dispense(
    self,
    containers: List[Container],
    volumes: List[float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense ``volumes[i]`` uL into ``containers[i]``.

    Args:
      containers: Target containers, one per dispense op.
      volumes: Per-container volume in uL.
      backend_params: Backend-specific parameters.
    """
    if len(containers) != len(volumes):
      raise ValueError(
        f"len(containers)={len(containers)} does not match len(volumes)={len(volumes)}"
      )
    if any(v <= 0 for v in volumes):
      raise ValueError("All volumes must be positive.")
    await self.backend.dispense(
      containers=containers, volumes=volumes, backend_params=backend_params
    )

  @need_capability_ready
  async def prime(self, backend_params: Optional[BackendParams] = None) -> None:
    """Prime the dispenser fluid path."""
    await self.backend.prime(backend_params=backend_params)
