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
  async def run_protocol(
    self,
    protocol: Protocol,
    block_max_volume: float,
    **kwargs,
  ):
    """Execute thermocycler protocol run (always non-blocking).

    Starts the protocol and returns an execution handle immediately. To block
    until completion, await the handle (e.g. await handle.wait()) or use
    wait_for_profile_completion() on the thermocycler.

    Args:
      protocol: Protocol object containing stages with steps and repeats.
      block_max_volume: Maximum block volume (µL) for safety.
      **kwargs: Backend-specific options (e.g. ODTC accepts config=ODTCConfig).

    Returns:
      Execution handle (backend-specific; e.g. MethodExecution for ODTC), or
      None for backends that do not return a handle. Caller can await
      handle.wait() or use wait_for_profile_completion() to block until done.
    """

  async def run_stored_protocol(self, name: str, wait: bool = False, **kwargs):
    """Execute a stored protocol by name (optional; backends that support it override).

    Args:
      name: Name of the stored protocol to run.
      wait: If False (default), start and return an execution handle. If True,
        block until done then return the (completed) handle.
      **kwargs: Backend-specific options.

    Returns:
      Execution handle (backend-specific). Same as run_protocol.

    Raises:
      NotImplementedError: This backend does not support running stored protocols by name.
    """
    raise NotImplementedError(
      "This backend does not support running stored protocols by name."
    )

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
