from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.serializer import SerializableMixin

from .standard import Protocol


class ThermocyclingBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for thermocyclers.

  Only thermocycling-specific operations live here: lid control, protocol
  execution, and profile progress queries. Block/lid temperature control
  is handled by separate TemperatureControllerBackend instances.
  """

  @abstractmethod
  async def open_lid(self) -> None:
    """Open the thermocycler lid."""

  @abstractmethod
  async def close_lid(self) -> None:
    """Close the thermocycler lid."""

  @abstractmethod
  async def get_lid_open(self) -> bool:
    """Return True if the lid is open."""

  @abstractmethod
  async def run_protocol(self, protocol: Protocol, block_max_volume: float, backend_params: Optional[SerializableMixin] = None) -> None:
    """Execute a thermocycler protocol.

    Args:
      protocol: Protocol containing stages with steps and repeats.
      block_max_volume: Maximum block volume in uL.
      backend_params: Backend-specific parameters.
    """

  @abstractmethod
  async def get_hold_time(self) -> float:
    """Get remaining hold time in seconds."""

  @abstractmethod
  async def get_current_cycle_index(self) -> int:
    """Get the zero-based index of the current cycle."""

  @abstractmethod
  async def get_total_cycle_count(self) -> int:
    """Get the total cycle count."""

  @abstractmethod
  async def get_current_step_index(self) -> int:
    """Get the zero-based index of the current step within the cycle."""

  @abstractmethod
  async def get_total_step_count(self) -> int:
    """Get the total number of steps in the current cycle."""
