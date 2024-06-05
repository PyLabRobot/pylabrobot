import json
from io import BytesIO
from typing import TextIO

from pylabrobot.config.config import Config
from pylabrobot.config.service.reader import ConfigReader
from pylabrobot.config.service.writer import ConfigWriter


class JsonReader(ConfigReader):
  """A ConfigReader that reads from an IO stream that is JSON formatted."""

  open_mode = "rb"

  def read(self, r: BytesIO) -> Config:
    """Read a Config object from an opened IO stream that is JSON formatted."""
    config_dict = json.loads(r.read())
    return Config.from_dict(config_dict)


class JsonWriter(ConfigWriter):
  """A ConfigWriter that writes to an IO stream in JSON format."""

  open_mode = "w"

  def write(self, w: TextIO, cfg: Config):
    """Write a Config object to an IO stream in JSON format."""
    json.dump(cfg.as_dict, w)
