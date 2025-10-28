from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol


class ThermocyclerBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract backend for a Thermocycler."""

  @abstractmethod
  async def open_lid(self):
    """Open thermocycler lid."""

  @abstractmethod
  async def close_lid(self):
    """Close thermocycler lid."""

  @abstractmethod
  async def set_block_temperature(self, temperature: List[float]):
    """Set the temperature of a thermocycler block.

    Args:
      temperature: Temperature for each zone.
    """

  @abstractmethod
  async def set_lid_temperature(self, temperature: List[float]):
    """Set the temperature of a thermocycler lid.

    Args:
      temperature: Temperature for each zone.
    """

  @abstractmethod
  async def deactivate_block(self):
    """Deactivate thermocycler block."""

  @abstractmethod
  async def deactivate_lid(self):
    """Deactivate thermocycler lid."""

  @abstractmethod
  async def run_protocol(self, protocol: Protocol, block_max_volume: float):
    """Execute thermocycler protocol run.

    Args:
      protocol: Protocol object containing stages with steps and repeats.
      block_max_volume: Maximum block volume (µL) for safety.
    """

  @abstractmethod
  async def get_block_current_temperature(self) -> List[float]:
    """Get the current block temperature zones in °C."""

  @abstractmethod
  async def get_block_target_temperature(self) -> List[float]:
    """Get the block target temperature zones in °C. May raise RuntimeError if no target is set."""

  @abstractmethod
  async def get_lid_current_temperature(self) -> List[float]:
    """Get the current lid temperature zones in °C."""

  @abstractmethod
  async def get_lid_target_temperature(self) -> List[float]:
    """Get the lid target temperature zones in °C. May raise RuntimeError if no target is set."""

  @abstractmethod
  async def get_lid_open(self) -> bool:
    """Return ``True`` if the lid is open."""

  @abstractmethod
  async def get_lid_status(self) -> LidStatus:
    """Get the lid temperature status."""

  @abstractmethod
  async def get_block_status(self) -> BlockStatus:
    """Get the block temperature status."""

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
