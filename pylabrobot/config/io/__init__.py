from abc import ABC, abstractmethod

from pylabrobot.config.config import Config
from pylabrobot.config.formats import ConfigLoader, ConfigSaver


class ConfigReader(ABC):
  """ConfigReader is an abstract class for reading a Config object from some IO source. """

  open_mode: str = "r"
  encoding: str

  def __init__(self, format_loader: ConfigLoader):
    self.format_loader = format_loader

  @abstractmethod
  def read(self, r) -> Config:
    """ Read from a source and load using `format_loader`. """


class ConfigWriter(ABC):
  """ConfigWriter is an abstract class for writing a Config object to some IO source. """

  open_mode: str = "w"
  encoding: str

  def __init__(self, format_saver: ConfigSaver):
    self.format_saver = format_saver

  @abstractmethod
  def write(self, w, cfg: Config):
    """ Serialize cfg using format_saver and write to w. """
