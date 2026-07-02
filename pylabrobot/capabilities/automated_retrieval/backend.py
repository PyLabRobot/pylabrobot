from abc import ABCMeta, abstractmethod

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.resource_stack import ResourceStack


class AutomatedRetrievalBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for random-access automated plate retrieval/storage devices."""

  @abstractmethod
  async def fetch_plate_to_loading_tray(self, plate: Plate):
    """Retrieve a plate from storage and place it on the loading tray."""

  @abstractmethod
  async def store_plate(self, plate: Plate, site: PlateHolder):
    """Store a plate from the loading tray into the given site."""


class StackerBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for a sequential ("stacking access") plate stacker.

  A stacker stores plates in one or more single-ended LIFO stacks; only the accessible (top) plate
  of a stack can be moved without first moving the plates above it. The device exposes two
  transfers between a stack and the loading tray.
  """

  @abstractmethod
  async def downstack(self, stack: ResourceStack):
    """Move the accessible (top) plate of ``stack`` onto the loading tray."""

  @abstractmethod
  async def upstack(self, stack: ResourceStack, plate: Plate):
    """Move a plate from the loading tray onto ``stack``."""
