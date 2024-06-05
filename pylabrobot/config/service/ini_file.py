import configparser
from pathlib import Path
from typing import TextIO

from pylabrobot.config.config import Config
from pylabrobot.config.service.reader import ConfigReader
from pylabrobot.config.service.writer import ConfigWriter


class IniReader(ConfigReader):
  """A ConfigReader that reads from an IO stream that INI formatted."""

  def read(self, r: TextIO) -> Config:
    """Read a Config object from an opened IO stream that is INI formatted."""
    config = configparser.ConfigParser()
    config.read_file(r)
    log_config = config["logging"]
    return Config(logging=Config.Logging(log_dir=Path(log_config["log_dir"])))


class IniWriter(ConfigWriter):
  """A ConfigWriter that writes to an IO stream in INI format."""

  def write(self, w: TextIO, cfg: Config):
    """Write a Config object to an IO stream in INI format."""
    config = configparser.ConfigParser()
    cfg_dict = cfg.as_dict
    for k, v in cfg_dict.items():
      config[k] = v

    config.write(w)
    return w
