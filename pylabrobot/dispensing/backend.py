"""Abstract backend interface for chip-based contactless liquid dispensers."""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.machines.backend import MachineBackend

from .standard import DispenseOp


class DispenserBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract class for a chip-based contactless liquid dispenser backend.

  Subclasses must implement :meth:`setup`, :meth:`stop`, and :meth:`dispense`.
  """

  @abstractmethod
  async def setup(self) -> None:
    """Set up the dispenser (connect, home, initialize pressure, etc.)."""

  @abstractmethod
  async def stop(self) -> None:
    """Shut down the dispenser and release all resources.

    After calling this, :meth:`setup` should be callable again.
    """

  @abstractmethod
  async def dispense(self, ops: List[DispenseOp], **backend_kwargs) -> None:
    """Dispense liquid into the specified wells.

    Args:
      ops: A list of :class:`DispenseOp` describing each dispense target.
      **backend_kwargs: Additional keyword arguments specific to the backend.
    """
