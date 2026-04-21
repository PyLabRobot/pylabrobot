from __future__ import annotations

import functools
import sys
from typing import Any, Awaitable, Callable, TypeVar

from pylabrobot.concurrency import AsyncExitStackWithShielding, AsyncResource, global_manager
from pylabrobot.machines.backend import MachineBackend
from pylabrobot.serializer import SerializableMixin

if sys.version_info < (3, 10):
  from typing_extensions import ParamSpec
else:
  from typing import ParamSpec

_P = ParamSpec("_P")
_R = TypeVar("_R", bound=Awaitable[Any])


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


class Machine(SerializableMixin, AsyncResource):
  """Abstract base class for machine frontends."""

  def __init__(self, backend: MachineBackend):
    self.backend = backend

  def __init_subclass__(cls, **kwargs):
    super().__init_subclass__(**kwargs)
    if "setup" in cls.__dict__:
      raise TypeError(
        f"Class {cls.__name__} overrides `setup`. "
        "Use `_enter_lifespan` instead for structured concurrency."
      )
    if "stop" in cls.__dict__:
      raise TypeError(
        f"Class {cls.__name__} overrides `stop`. "
        "Use `_enter_lifespan` instead for structured concurrency."
      )

  @property
  def setup_finished(self) -> bool:
    return getattr(self, "_active_lifespan", None) is not None

  def serialize(self) -> dict:
    return {"backend": self.backend.serialize()}

  @classmethod
  def deserialize(cls, data: dict):
    data_copy = data.copy()  # copy data because we will be modifying it
    backend_data = data_copy.pop("backend")
    backend = MachineBackend.deserialize(backend_data)
    data_copy["backend"] = backend
    return cls(**data_copy)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    await stack.enter_async_context(self.backend)

  async def setup(self, **kwargs):
    if kwargs:
      # TODO: Design question: Do we need kwargs? We could elevate
      # `_lifespan` to a public API `lifespan`, taking kwargs. However, having
      # both `lifespan` as well as `__aenter__`/`__aexit__` goes against the
      # python ZEN "There should be one, and preferably only one obvious way to do it".
      raise ValueError("Keyword arguments during setup are not allowed anymore")
    await global_manager.manage_context(self)

  async def stop(self):
    await global_manager.release_context(self)
