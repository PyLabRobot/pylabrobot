from typing import List, Optional, Union

from pylabrobot.machines import Machine
from pylabrobot.resources import (
  Coordinate,
  Plate,
  PlateHolder,
  Resource,
  ResourceNotFoundError,
  Rotation,
)
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.serializer import serialize

from .stacker_backend import StackerBackend


class EmptyStackError(Exception):
  """Raised when downstacking from a stack that has no plates."""


class LoadingTrayOccupiedError(Exception):
  """Raised when a transfer would collide with a plate already on the loading tray."""


class Stacker(Machine, Resource):
  """Sequential ("stacking access") plate-storage capability.

  Models one or more single-ended LIFO stacks of (nesting) plates plus a single transfer
  position -- the "loading tray", borrowing the incubator's term. Each stack is a
  :class:`~pylabrobot.resources.resource_stack.ResourceStack` (``direction="z"``), which enforces
  LIFO access (only the top plate can be removed) and computes the stack height from each plate's
  ``stacking_z_height``.

  This is a *capability*: it is meant to be composed onto a machine (e.g.
  ``self.stacker = Stacker(backend=...)``) rather than subclassed into a device-specific frontend.
  Devices that are stackers include the Agilent BenchCel and the HighRes MicroServe.
  """

  def __init__(
    self,
    backend: StackerBackend,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    stacks: List[ResourceStack],
    loading_tray_location: Coordinate,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    Machine.__init__(self, backend=backend)
    self.backend: StackerBackend = backend  # fix type
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
    )

    self.loading_tray = PlateHolder(
      name=self.name + "_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(self.loading_tray, location=loading_tray_location)

    self._stacks = stacks
    for stack in self._stacks:
      self.assign_child_resource(stack, location=None)

  @property
  def stacks(self) -> List[ResourceStack]:
    return self._stacks

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    await self.backend.set_stacks(self._stacks)

  def _resolve_stack(self, stack: Union[ResourceStack, int]) -> ResourceStack:
    if isinstance(stack, int):
      return self._stacks[stack]
    if stack not in self._stacks:
      raise ValueError(f"Stack {stack.name!r} is not part of stacker '{self.name}'")
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
    raise ResourceNotFoundError(f"Plate {plate_name} not found in stacker '{self.name}'")

  async def downstack(self, stack: Union[ResourceStack, int], **backend_kwargs) -> Plate:
    """Move the accessible (top) plate of ``stack`` onto the loading tray and return it."""
    stack = self._resolve_stack(stack)
    plate = self.get_accessible_plate(stack)
    if plate is None:
      raise EmptyStackError(f"Stack {stack.name!r} of stacker '{self.name}' is empty")
    if self.loading_tray.resource is not None:
      raise LoadingTrayOccupiedError(
        f"Loading tray of stacker '{self.name}' already holds '{self.loading_tray.resource.name}'"
      )
    await self.backend.downstack(stack, **backend_kwargs)
    plate.unassign()
    self.loading_tray.assign_child_resource(plate)
    return plate

  async def upstack(
    self,
    stack: Union[ResourceStack, int],
    plate: Optional[Plate] = None,
    **backend_kwargs,
  ) -> None:
    """Move a plate from the loading tray onto ``stack`` (its new accessible plate).

    ``plate`` defaults to whatever is on the loading tray.
    """
    stack = self._resolve_stack(stack)
    if plate is None:
      tray_resource = self.loading_tray.resource
      if not isinstance(tray_resource, Plate):
        raise ResourceNotFoundError(f"No plate on the loading tray of stacker '{self.name}'")
      plate = tray_resource
    await self.backend.upstack(stack, plate, **backend_kwargs)
    plate.unassign()
    stack.assign_child_resource(plate)

  def summary(self) -> str:
    lines = [f"Stacker '{self.name}' ({len(self._stacks)} stacks)"]
    for i, stack in enumerate(self._stacks):
      # bottom -> top; the accessible plate is last.
      contents = [child.name for child in stack.children] or ["<empty>"]
      lines.append(f"  stack {i}: " + " -> ".join(contents) + "  (top = accessible)")
    tray = self.loading_tray.resource
    lines.append(f"  loading tray: {tray.name if tray is not None else '<empty>'}")
    return "\n".join(lines)

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **Resource.serialize(self),
      "backend": self.backend.serialize(),
      "stacks": [stack.serialize() for stack in self._stacks],
      "loading_tray_location": serialize(self.loading_tray.location),
    }

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False) -> "Stacker":
    # Deserialization is not supported yet: it needs ResourceStack serialization support
    # (ResourceStack.__init__ takes ``direction`` rather than ``size_*``, so it does not round-trip
    # through the generic Resource.(de)serialize path). Tracked as a follow-up. This override also
    # resolves the otherwise-ambiguous ``deserialize`` inherited from both Machine and Resource.
    raise NotImplementedError(
      "Stacker.deserialize is not implemented yet (pending ResourceStack serialization support)."
    )
