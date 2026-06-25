from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource_stack import ResourceStack


class StackerBackend(MachineBackend, metaclass=ABCMeta):
  """Backend interface for the sequential ("stacking access") :class:`Stacker` capability.

  A stacker stores plates in one or more single-ended LIFO stacks. Unlike an
  incubator's random-access racks, only the accessible (top) plate of each stack can be moved
  without first moving the plates above it. The device exposes two primitive transfers between a
  stack and the stacker's transfer position (the "loading tray"): ``downstack`` (stack ->
  transfer position) and ``upstack`` (transfer position -> stack).

  Backends are not incubators: there is deliberately no door/temperature/shaking here. A device
  that both stores sequentially and, say, controls temperature would compose this capability with
  a separate temperature-control capability.
  """

  def __init__(self) -> None:
    super().__init__()
    self._stacks: Optional[List[ResourceStack]] = None

  @property
  def stacks(self) -> List[ResourceStack]:
    assert self._stacks is not None, "Backend not set up?"
    return self._stacks

  async def set_stacks(self, stacks: List[ResourceStack]) -> None:
    """Configure the stacks the device manages. Called by :meth:`Stacker.setup`."""
    self._stacks = stacks

  @abstractmethod
  async def downstack(self, stack: ResourceStack, **backend_kwargs) -> None:
    """Move the accessible plate from ``stack`` to the stacker's transfer position."""

  @abstractmethod
  async def upstack(self, stack: ResourceStack, plate: Plate, **backend_kwargs) -> None:
    """Move ``plate`` from the stacker's transfer position onto ``stack``."""
