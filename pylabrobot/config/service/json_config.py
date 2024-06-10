import json
from typing import IO

from pylabrobot.config.config import Config
from pylabrobot.config.service import ConfigReader, ConfigWriter


class JsonReader(ConfigReader):
  """A ConfigReader that reads from an IO stream that is JSON formatted."""

  open_mode = "rb"
  extension = "json"

  def read(self, r: IO) -> Config:
    """Read a Config object from an opened IO stream that is JSON formatted."""
    config_dict = json.loads(r.read())
    return Config.from_dict(config_dict)


class JsonWriter(ConfigWriter):
  """A ConfigWriter that writes to an IO stream in JSON format."""

  open_mode = "w"
  extension = "json"
  encoding = "utf-8"

  def write(self, w: IO, cfg: Config):
    """Write a Config object to an IO stream in JSON format."""
    json.dump(cfg.as_dict, w)
