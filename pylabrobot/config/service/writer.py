from abc import ABC, abstractmethod
from typing import TypeVar, Generic

from pylabrobot.config.config import Config

T = TypeVar("T")


class ConfigWriter(Generic[T], ABC):
  """ConfigWriter is an abstract class for writing a Config object to a
  stream."""

  open_mode: str = "w"

  @abstractmethod
  def write(self, w: T, cfg: Config):
    """Write a Config object."""
