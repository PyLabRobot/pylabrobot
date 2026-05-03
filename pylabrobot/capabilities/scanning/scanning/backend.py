from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.serializer import SerializableMixin


class ScanningError(Exception):
  """Capability-generic exception for scanning failures.

  Vendor backends should raise a subclass that ALSO inherits from the
  vendor's driver-level exception, so callers can catch on either axis
  (capability-generic or vendor-specific).
  """


class ScanningBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for the scanning capability.

  Concrete backends configure and control flatbed-style fluorescence
  / luminescence scans. The capability does not assume a plate or
  well grid — Odyssey-style flatbed imagers and gel docs fit;
  plate-aware microscopy belongs under :class:`Microscopy`.
  """

  @abstractmethod
  async def configure(self, backend_params: Optional[SerializableMixin] = None) -> None:
    """Set up the next scan with vendor-specific parameters."""

  @abstractmethod
  async def start(self) -> None:
    """Begin acquisition. Backend must be configured first."""

  @abstractmethod
  async def stop(self) -> None:
    """Graceful stop — finish current line, save partial output."""

  @abstractmethod
  async def pause(self) -> None:
    """Pause acquisition. Resume by calling :meth:`start` again."""

  @abstractmethod
  async def cancel(self) -> None:
    """Abort acquisition and discard any partial output."""
