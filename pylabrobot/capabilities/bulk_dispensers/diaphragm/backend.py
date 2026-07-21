"""Abstract backend interface for diaphragm-based contactless dispensers."""

from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend
from pylabrobot.resources import Container


class DiaphragmDispenserBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for diaphragm-based contactless liquid dispensers.

  Diaphragm dispensers (e.g. the Formulatrix Mantis) drive liquid through a
  disposable chip with microvalves using pressurized air. Targets are addressed
  one container at a time, so the interface takes parallel ``containers`` /
  ``volumes`` lists rather than the column-keyed dict used by the 8-channel
  bulk-dispenser capabilities.

  Subclasses translate these calls into concrete instrument operations via the
  parent device's :class:`pylabrobot.device.Driver`.
  """

  @abstractmethod
  async def dispense(
    self,
    containers: List[Container],
    volumes: List[float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense ``volumes[i]`` uL into ``containers[i]``.

    Args:
      containers: Target containers (e.g. wells, tubes), one per dispense op.
      volumes: Per-container volume in uL. Must be the same length as ``containers``.
      backend_params: Backend-specific parameters.
    """

  @abstractmethod
  async def prime(self, backend_params: Optional[BackendParams] = None) -> None:
    """Prime the dispenser fluid path."""
