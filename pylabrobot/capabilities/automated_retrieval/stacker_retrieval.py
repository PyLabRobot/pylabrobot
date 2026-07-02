from typing import List, Optional, Union

from pylabrobot.capabilities.automated_retrieval.automated_retrieval import AutomatedRetrieval
from pylabrobot.capabilities.capability import need_capability_ready
from pylabrobot.resources import Plate, PlateHolder, ResourceNotFoundError
from pylabrobot.resources.resource_stack import ResourceStack

from .backend import StackerBackend


class EmptyStackError(Exception):
  """Raised when downstacking from a stack that has no plates."""


class LoadingTrayOccupiedError(Exception):
  """Raised when a transfer would collide with a plate already on the loading tray."""


class StackerRetrieval(AutomatedRetrieval):
  """Sequential ("stacking access") plate-storage capability.

  Owns one or more single-ended LIFO stacks -- each a
  :class:`~pylabrobot.resources.resource_stack.ResourceStack` (``direction="z"``) -- plus the
  loading tray shared with
  :class:`~pylabrobot.capabilities.automated_retrieval.RandomAccessRetrieval` via
  :class:`~pylabrobot.capabilities.automated_retrieval.AutomatedRetrieval`. Only the accessible
  (top) plate of a stack can be moved without first moving the plates above it.

  Devices that are stackers (e.g. the Agilent BenchCel or HighRes MicroServe) compose this
  capability and provide a
  :class:`~pylabrobot.capabilities.automated_retrieval.backend.StackerBackend`.
  """

  def __init__(
    self,
    backend: StackerBackend,
    stacks: Optional[List[ResourceStack]] = None,
    loading_tray: Optional[PlateHolder] = None,
  ):
    super().__init__(backend=backend, loading_tray=loading_tray)
    self.backend: StackerBackend = backend
    self._stacks: List[ResourceStack] = stacks if stacks is not None else []

  @property
  def stacks(self) -> List[ResourceStack]:
    return self._stacks

  def _resolve_stack(self, stack: Union[ResourceStack, int]) -> ResourceStack:
    if isinstance(stack, int):
      return self._stacks[stack]
    if stack not in self._stacks:
      raise ValueError(f"Stack {stack.name!r} is not part of this stacker")
    return stack

  def get_accessible_plate(self, stack: Union[ResourceStack, int]) -> Optional[Plate]:
    """The only plate that can be downstacked without moving others (the top of the stack)."""
    stack = self._resolve_stack(stack)
    if len(stack.children) == 0:
      return None
    top = stack.get_top_item()
    return top if isinstance(top, Plate) else None

  def get_stack_by_plate_name(self, plate_name: str) -> ResourceStack:
    for stack in self._stacks:
      for child in stack.children:
        if child.name == plate_name:
          return stack
    raise ResourceNotFoundError(f"Plate {plate_name} not found in stacker")

  @need_capability_ready
  async def downstack(self, stack: Union[ResourceStack, int]) -> Plate:
    """Move the accessible (top) plate of ``stack`` onto the loading tray and return it."""
    loading_tray = self._require_loading_tray()
    stack = self._resolve_stack(stack)
    plate = self.get_accessible_plate(stack)
    if plate is None:
      raise EmptyStackError(f"Stack {stack.name!r} is empty")
    if loading_tray.resource is not None:
      raise LoadingTrayOccupiedError(f"Loading tray already holds '{loading_tray.resource.name}'")
    await self.backend.downstack(stack)
    plate.unassign()
    loading_tray.assign_child_resource(plate)
    return plate

  @need_capability_ready
  async def upstack(self, stack: Union[ResourceStack, int], plate: Optional[Plate] = None) -> None:
    """Move a plate from the loading tray onto ``stack`` (defaults to the plate on the tray)."""
    stack = self._resolve_stack(stack)
    if plate is None:
      plate = self._plate_on_loading_tray()
    await self.backend.upstack(stack, plate)
    plate.unassign()
    stack.assign_child_resource(plate)

  def summary(self) -> str:
    columns = [[child.name for child in reversed(stack.children)] for stack in self._stacks]
    height = max((len(c) for c in columns), default=0)
    columns = [c + [""] * (height - len(c)) for c in columns]
    header = [f"Stack {i}" for i in range(len(self._stacks))]
    return self._pretty_table(header, *columns)
