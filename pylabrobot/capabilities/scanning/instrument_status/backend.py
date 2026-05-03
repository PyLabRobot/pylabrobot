from __future__ import annotations

from abc import ABCMeta, abstractmethod

from pylabrobot.capabilities.capability import CapabilityBackend

from .standard import InstrumentStatusReading


class InstrumentStatusError(Exception):
  """Capability-generic exception for instrument status read failures."""


class InstrumentStatusBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for instrument status polling."""

  @abstractmethod
  async def read_status(self) -> InstrumentStatusReading:
    """Return the current instrument status snapshot."""
