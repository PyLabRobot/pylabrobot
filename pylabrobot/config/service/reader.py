from abc import ABC, abstractmethod
from typing import TypeVar, Generic

from pylabrobot.config.config import Config

T = TypeVar("T")


class ConfigReader(Generic[T], ABC):
  """ConfigReader is an abstract class for reading a Config object from a
  stream."""
  open_mode: str = "r"

  @abstractmethod
  def read(self, r: T) -> Config:
    """Read a Config object."""
