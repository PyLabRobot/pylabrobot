""" ConfigLoader and ConfigSaver load and save configs from and to IO streams. """

from abc import ABC, abstractmethod
from typing import IO, List

from pylabrobot.config.config import Config


class ConfigLoader(ABC):
  """ConfigLoader is an abstract class for loading a Config object from a stream. """

  extension: str

  @abstractmethod
  def load(self, r: IO) -> Config:
    """ Load a Config object."""


class ConfigSaver(ABC):
  """ConfigSaver is an abstract class for saving a Config object to a stream. """

  extension: str

  @abstractmethod
  def save(self, w: IO, cfg: Config):
    """ Save a Config object."""


class MultiLoader(ConfigLoader):
  """A ConfigLoader that loads from multiple ConfigLoaders."""

  def __init__(self, loaders: List[ConfigLoader]):
    self.loaders = loaders

  # Unknown what the Exception will be when trying to load the stream so catch all and ignore.
  def load(self, r: IO) -> Config:
    for loader in self.loaders:
      try:
        return loader.load(r)
      except Exception: # pylint: disable=broad-except
        pass
    raise ValueError("No loader could load file.")
