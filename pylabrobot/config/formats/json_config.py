import json
from typing import IO

from pylabrobot.config.config import Config
from pylabrobot.config.formats import ConfigLoader, ConfigSaver


class JsonLoader(ConfigLoader):
  """ A ConfigLoader that loads from an IO stream that is JSON formatted. """

  extension = "json"

  def load(self, r: IO) -> Config:
    """Load a Config object from an opened IO stream that is JSON formatted."""
    config_dict = json.loads(r.read())
    return Config.from_dict(config_dict)


class JsonSaver(ConfigSaver):
  """ A ConfigSaver that saves to an IO stream in JSON format. """

  extension = "json"

  def save(self, w: IO, cfg: Config):
    """ Save a Config object to an IO stream in JSON format. """
    json.dump(cfg.as_dict, w)
