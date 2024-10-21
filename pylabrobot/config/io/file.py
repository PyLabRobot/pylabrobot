from pathlib import Path
from typing import Union

from pylabrobot.config.config import Config
from pylabrobot.config.io import ConfigReader, ConfigWriter


class FileReader(ConfigReader):
  """ A ConfigReader that reads from a file. """

  encoding = "utf-8"

  def read(self, r: Union[str, Path]) -> Config:
    """ Read a Config object from a file. """
    with open(r, self.open_mode, encoding=self.encoding) as f:
      return self.format_loader.load(f)


class FileWriter(ConfigWriter):
  """ A ConfigWriter that writes to a file. """

  encoding = "utf-8"

  def write(self, w: Union[str, Path], cfg: Config):
    """ Write a Config object to a file. """
    with open(w, self.open_mode, encoding=self.encoding) as f:
      self.format_saver.save(f, cfg)
