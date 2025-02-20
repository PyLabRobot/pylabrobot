from __future__ import annotations

import functools
import sys
from abc import ABC
from typing import Any, Awaitable, Callable, TypeVar

from pylabrobot.machines.backend import MachineBackend

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


class Machine(ABC):
  """Abstract base class for machine frontends."""

  def __init__(self, backend: MachineBackend):
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

  async def setup(self, **backend_kwargs):
    await self.backend.setup(**backend_kwargs)
    self._setup_finished = True

  @need_setup_finished
  async def stop(self):
    await self.backend.stop()
    self._setup_finished = False

  async def __aenter__(self):
    await self.setup()
    return self

  async def __aexit__(self, exc_type, exc_value, traceback):
    await self.stop()
