"""Abstract base class for plate washer backends.

Plate washers are devices that automate the washing of microplates,
typically used in ELISA, cell-based assays, and other applications.
"""

from __future__ import annotations

from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class PlateWasherBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract base class for plate washer backends.

  Subclasses must implement setup() and stop() for hardware communication.
  Device-specific operations (wash, prime, dispense, etc.) are exposed
  directly on the backend rather than through a generic interface, since
  each washer model has its own parameter set.
  """

  @abstractmethod
  async def setup(self) -> None:
    """Set up the plate washer.

    This should establish connection to the device and configure
    communication parameters.
    """

  @abstractmethod
  async def stop(self) -> None:
    """Stop the plate washer and close connections.

    This should safely close all connections and ensure the device
    is in a safe state.
    """
