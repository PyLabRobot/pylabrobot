from __future__ import annotations

import functools
import inspect
import sys
import weakref
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Awaitable, Callable, List, TypeVar

from pylabrobot.serializer import SerializableMixin
from pylabrobot.utils.object_parsing import find_subclass

if TYPE_CHECKING:
  from pylabrobot.capabilities.capability import Capability

if sys.version_info < (3, 10):
  from typing_extensions import ParamSpec
else:
  from typing import ParamSpec

_P = ParamSpec("_P")
_R = TypeVar("_R", bound=Awaitable[Any])


class Driver(SerializableMixin, ABC):
  """Abstract class for hardware drivers."""

  _instances: weakref.WeakSet["Driver"] = weakref.WeakSet()

  def __init__(self):
    self._instances.add(self)

  @abstractmethod
  async def setup(self):
    pass

  @abstractmethod
  async def stop(self):
    pass

  def serialize(self) -> dict:
    return {"type": self.__class__.__name__}

  @classmethod
  def deserialize(cls, data: dict):
    class_name = data.pop("type")
    subclass = find_subclass(class_name, cls=cls)
    if subclass is None:
      raise ValueError(f'Could not find subclass with name "{class_name}"')
    if inspect.isabstract(subclass):
      raise ValueError(f'Subclass with name "{class_name}" is abstract')
    assert issubclass(subclass, cls)
    return subclass(**data)

  @classmethod
  def get_all_instances(cls):
    return cls._instances


def need_setup_finished(func: Callable[_P, _R]) -> Callable[_P, _R]:
  """Decorator for methods that require the device to be set up.

  Checked by verifying `self.setup_finished` is `True`.

  Raises:
    RuntimeError: If the device is not set up.
  """

  @functools.wraps(func)
  async def wrapper(*args, **kwargs):
    assert isinstance(args[0], Device), "The first argument must be a Device."
    self = args[0]

    if not self.setup_finished:
      raise RuntimeError("The setup has not finished. See `setup`.")
    return await func(*args, **kwargs)

  return wrapper


class Device(SerializableMixin, ABC):
  """Abstract base class for device frontends."""

  def __init__(self, driver: Driver):
    self.driver = driver
    self._setup_finished = False
    self._capabilities: List[Capability] = []

  @property
  def setup_finished(self) -> bool:
    return self._setup_finished

  def serialize(self) -> dict:
    return {"driver": self.driver.serialize()}

  @classmethod
  def deserialize(cls, data: dict):
    data_copy = data.copy()
    driver_data = data_copy.pop("driver", None) or data_copy.pop("backend", None)
    driver = Driver.deserialize(driver_data)
    data_copy["driver"] = driver
    return cls(**data_copy)

  async def setup(self):
    await self.driver.setup()
    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True

  @need_setup_finished
  async def stop(self):
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self.driver.stop()
    self._setup_finished = False

  async def __aenter__(self):
    await self.setup()
    return self

  async def __aexit__(self, exc_type, exc_value, traceback):
    await self.stop()
