from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Optional, Callable, Type

from pylabrobot.machines.backends import MachineBackend
from pylabrobot.resources import Resource

import functools


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


class Machine(Resource, metaclass=ABCMeta):
  """ Abstract class for machine frontends. All Machines are Resources. """

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

  def serialize(self) -> dict:
    return {**super().serialize(),
            "backend": self.backend.serialize()}

  @classmethod
  def deserialize(cls, data: dict):
    data_copy = data.copy() # copy data because we will be modifying it
    backend_data = data_copy.pop("backend")

    def find_subclass(
      class_name: str,
      cls: Type[MachineBackend] = MachineBackend
    ) -> Optional[Type[MachineBackend]]:
      """ Recursively find a MachineBackend with the correct name.

      Args:
        class_name: The name of the class to find.
        cls: The class to search in.

      Returns:
        The class with the given name, or `None` if no such class exists.
      """

      if cls.__name__ == class_name:
        return cls
      for subclass in cls.__subclasses__():
        subclass_ = find_subclass(class_name=class_name, cls=subclass)
        if subclass_ is not None:
          return subclass_
      return None

    backend_subclass = find_subclass(backend_data["type"])
    if backend_subclass is None:
      raise ValueError(f"Could not find subclass with name '{backend_data['type']}'")
    if issubclass(backend_subclass, ABCMeta):
      raise ValueError(f"Subclass with name '{backend_data['type']}' is abstract")
    assert issubclass(backend_subclass, MachineBackend)
    del backend_data["type"]
    backend = backend_subclass(**backend_data) # pylint: disable=abstract-class-instantiated
    data_copy["backend"] = backend
    return super().deserialize(data_copy)

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
