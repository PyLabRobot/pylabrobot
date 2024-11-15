import configparser
from pathlib import Path
from typing import IO

from pylabrobot.config.config import Config
from pylabrobot.config.formats import ConfigLoader, ConfigSaver


class IniLoader(ConfigLoader):
  """A ConfigLoader that loads from an IO stream that INI formatted."""

  extension = "ini"

  def load(self, r: IO) -> Config:
    """Load a Config object from an opened IO stream that is INI formatted."""
    config = configparser.ConfigParser()
    config.read_file(r)
    log_config_data = config["logging"]
    log_dir = Path(log_config_data["log_dir"]) if "log_dir" in log_config_data else None
    logging_config = Config.Logging(log_dir=log_dir)
    return Config(logging=logging_config)


class IniSaver(ConfigSaver):
  """A ConfigSaver that saves to an IO stream in INI format."""

  extension = "ini"

  def save(self, w: IO, cfg: Config):
    """Save a Config object to an IO stream in INI format."""
    config = configparser.ConfigParser()
    for k, v in cfg.as_dict.items():
      v = {k_: v_ for k_, v_ in v.items() if v_ is not None}
      config[k] = v

    config.write(w)
    return w
