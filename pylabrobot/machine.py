from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
import functools
from typing import Callable, Optional

from pylabrobot.resources import Resource


def need_setup_finished(func: Callable):
  """ Decorator for methods that require the liquid handler to be set up.

  Checked by verifying `self.setup_finished` is `True`.

  Raises:
    RuntimeError: If the liquid handler is not set up.
  """

  @functools.wraps(func)
  async def wrapper(self: Machine, *args, **kwargs):
    if not self.setup_finished:
      raise RuntimeError("The setup has not finished. See `setup`.")
    return await func(self, *args, **kwargs)
  return wrapper


class MachineBackend(ABC):
  """ Abstract class for machine backends. """

  @abstractmethod
  async def setup(self):
    pass

  @abstractmethod
  async def stop(self):
    pass


class Machine(Resource, metaclass=ABCMeta):
  """ Abstract class for machine frontends. All `Machine`s are `Resource`s. """

  @abstractmethod
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: MachineBackend,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z,
                     category=category, model=model)
    self.backend = backend
    self._setup_finished = False

  @property
  def setup_finished(self) -> bool:
    return self._setup_finished

  async def setup(self):
    await self.backend.setup()
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
