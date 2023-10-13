from __future__ import annotations

from abc import ABCMeta, abstractmethod
import sys
from typing import List, Optional, Type

from pylabrobot.machine import MachineBackend

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


class PlateReaderBackend(MachineBackend, metaclass=ABCMeta):
  """ An abstract class for a plate reader. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate. """

  @abstractmethod
  async def setup(self) -> None:
    """ Set up the plate reader. This should be called before any other methods. """

  @abstractmethod
  async def stop(self) -> None:
    """ Close all connections to the plate reader and make sure setup() can be called again. """

  @abstractmethod
  async def open(self) -> None:
    """ Open the plate reader. Also known as plate out. """

  @abstractmethod
  async def close(self) -> None:
    """ Close the plate reader. Also known as plate in. """

  @abstractmethod
  async def read_luminescence(self, focal_height: float) -> List[List[float]]:
    """ Read the luminescence from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate. """

  @abstractmethod
  async def read_absorbance(
    self,
    wavelength: int,
    report: Literal["OD", "transmittance"]
  ) -> List[List[float]]:
    """ Read the absorbance from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate. """

  # Copied from liquid_handling/backend.py. Maybe we should create a shared base class?

  def serialize(self):
    """ Serialize the backend so that an equivalent backend can be created by passing the dict
    as kwargs to the initializer. The dict must contain a key "type" that specifies the type of
    backend to create. This key will be removed from the dict before passing it to the initializer.
    """

    return {
      "type": self.__class__.__name__,
    }

  @classmethod
  def deserialize(cls, data: dict) -> PlateReaderBackend:
    """ Deserialize the backend. Unless a custom serialization method is implemented, this method
    should not be overridden. """

    # Recursively find a subclass with the correct name
    def find_subclass(cls: Type[PlateReaderBackend], name: str) -> \
      Optional[Type[PlateReaderBackend]]:
      if cls.__name__ == name:
        return cls
      for subclass in cls.__subclasses__():
        subclass_ = find_subclass(subclass, name)
        if subclass_ is not None:
          return subclass_
      return None

    subclass = find_subclass(cls, data["type"])
    if subclass is None:
      raise ValueError(f"Could not find subclass with name {data['type']}")

    del data["type"]
    return subclass(**data)
