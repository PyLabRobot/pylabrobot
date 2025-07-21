"""Abstract base for Thermocycler back-ends."""

from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.machines.backend import MachineBackend


class ThermocyclerBackend(MachineBackend, metaclass=ABCMeta):
  """Interface for an Opentrons Thermocycler."""

  @abstractmethod
  async def open_lid(self):
    """Open thermocycler lid."""

  @abstractmethod
  async def close_lid(self):
    """Close thermocycler lid."""

  @abstractmethod
  async def set_block_temperature(self, celsius: float):
    """Set the temperature of a thermocycler block."""

  @abstractmethod
  async def set_lid_temperature(self, celsius: float):
    """Set the temperature of a thermocycler lid."""

  @abstractmethod
  async def deactivate_block(self):
    """Deactivate thermocycler block."""

  @abstractmethod
  async def deactivate_lid(self):
    """Deactivate thermocycler lid."""

  @abstractmethod
  async def run_profile(self, profile: list[dict], block_max_volume: float):
    """Execute thermocycler profile run."""

  @abstractmethod
  async def get_block_current_temperature(self) -> float:
    """Get the current block temperature in °C."""

  @abstractmethod
  async def get_block_target_temperature(self) -> Optional[float]:
    """Get the block target temperature in °C."""

  @abstractmethod
  async def get_lid_current_temperature(self) -> float:
    """Get the current lid temperature in °C."""

  @abstractmethod
  async def get_lid_target_temperature(self) -> Optional[float]:
    """Get the lid target temperature in °C."""

  @abstractmethod
  async def get_lid_status(self):
    """Get the lid open/closed status."""

  @abstractmethod
  async def get_hold_time(self) -> float:
    """Get remaining hold time in seconds."""

  @abstractmethod
  async def get_current_cycle_index(self) -> int:
    """Get the one-based index of the current cycle."""

  @abstractmethod
  async def get_total_cycle_count(self) -> int:
    """Get the total cycle count."""

  @abstractmethod
  async def get_current_step_index(self) -> int:
    """Get the one-based index of the current step within the cycle."""

  @abstractmethod
  async def get_total_step_count(self) -> int:
    """Get the total number of steps in the current cycle."""
