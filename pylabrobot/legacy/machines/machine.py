from __future__ import annotations

import functools
import sys
from abc import ABC
from typing import Any, Awaitable, Callable, Optional, TypeVar

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.events import LifespanEventInfo, device_reference, resource_reference
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.resources.resource import Resource
from pylabrobot.serializer import SerializableMixin

if sys.version_info < (3, 10):
  from typing_extensions import ParamSpec
else:
  from typing import ParamSpec

_P = ParamSpec("_P")
_R = TypeVar("_R", bound=Awaitable[Any])


def _machine_event_context(machine: "Machine") -> dict:
  device = (
    resource_reference(machine)
    if isinstance(machine, Resource)
    else device_reference(machine, name=type(machine).__name__)
  )
  return {
    "device": device,
    "backend": type(machine.backend).__name__,
  }


def need_setup_finished(func: Callable[_P, _R]) -> Callable[_P, _R]:
  """Decorator for methods that require the machine to be set up.

  Checked by verifying `self.setup_finished` is `True`.

  Raises:
    RuntimeError: If the machine is not set up.
  """

  @functools.wraps(func)
  async def wrapper(*args, **kwargs):
    assert isinstance(args[0], Machine), "The first argument must be a Machine."
    self = args[0]

    if not self.setup_finished:
      raise RuntimeError("The setup has not finished. See `setup`.")
    return await func(*args, **kwargs)

  return wrapper


class Machine(SerializableMixin, HasLifespan, ABC):
  """Abstract base class for machine frontends."""

  def __init__(self, backend: MachineBackend):
    HasLifespan.__init__(self)
    self.backend = backend
    self._setup_finished = False

  @property
  def setup_finished(self) -> bool:
    return self._setup_finished

  def serialize(self) -> dict:
    return {"backend": self.backend.serialize()}

  @classmethod
  def deserialize(cls, data: dict):
    data_copy = data.copy()  # copy data because we will be modifying it
    backend_data = data_copy.pop("backend")
    backend = MachineBackend.deserialize(backend_data)
    data_copy["backend"] = backend
    return cls(**data_copy)

  async def _pre_backend_stop(self) -> None:
    """Perform frontend safety actions before closing the backend."""

  def _lifespan_event_info(self, **kwargs: Any) -> Optional[LifespanEventInfo]:
    """Describe the frontend's entry and exit transitions."""
    return LifespanEventInfo("machine.setup", "machine.stop", _machine_event_context(self))

  def _clear_setup_finished(self) -> None:
    """Mark the frontend inactive after its backend has exited."""
    self._setup_finished = False

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    """Enter and own the backend lifespan for this Machine lifespan."""
    await super()._enter_lifespan(stack)
    stack.callback(self._clear_setup_finished)
    await stack.enter_async_context(self.backend.lifespan(**backend_kwargs))
    stack.push_shielded_async_callback(self._pre_backend_stop)
    self._setup_finished = True
