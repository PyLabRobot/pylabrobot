from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backends import MachineBackend


class thermocyclerBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract base class for fan backends."""

  @abstractmethod
  async def setup(self) -> None:
    """Set up comm with thermocycler. This should be called before any other methods."""

  @abstractmethod
  async def run_protocol(self, protocol_data: dict) -> None:
    """Run the protocol"""

  @abstractmethod
  async def stop(self) -> None:
    """Close all connections to the fan and make sure setup() can be called again."""
