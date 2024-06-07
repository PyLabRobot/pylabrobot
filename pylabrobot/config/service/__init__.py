"""Services for loading and saving configuration files."""
from abc import ABC, abstractmethod
from typing import IO, Union

from pylabrobot.config.config import Config


class ConfigReader(ABC):
  """ConfigReader is an abstract class for reading a Config object from a
  stream."""
  extension: str
  open_mode: str = "r"
  encoding: Union[str, None] = None

  @abstractmethod
  def read(self, r: IO) -> Config:
    """Read a Config object."""


class ConfigWriter(ABC):
  """ConfigWriter is an abstract class for writing a Config object to a
  stream."""

  extension: str
  open_mode: str = "w"
  encoding: Union[str, None] = None

  @abstractmethod
  def write(self, w: IO, cfg: Config):
    """Write a Config object."""


class ConfigLoader(ABC):
  """ConfigLoader is an abstract class for loading a Config object"""
  format_reader: ConfigReader

  def __init__(self, format_reader: ConfigReader):
    self.format_reader = format_reader

  @abstractmethod
  def load(self, r: object) -> Config:
    """Load a Config object."""


class ConfigSaver(ABC):
  """ConfigSaver is an abstract class for saving a Config object"""
  format_writer: ConfigWriter

  def __init__(self, format_writer: ConfigWriter):
    self.format_writer = format_writer

  @abstractmethod
  def save(self, w: object, cfg: Config):
    """Save a Config object."""


class MultiReader(ConfigReader):
  """A ConfigReader that reads from multiple ConfigReaders."""

  readers: tuple[ConfigReader, ...]

  def __init__(self, *readers: ConfigReader):
    self.readers = readers

  # noinspection PyBroadException
  def read(self, r: IO) -> Config:
    for reader in self.readers:
      try:
        return reader.read(r)
      except Exception as e:
        pass
    raise ValueError("No reader could read the file.")
