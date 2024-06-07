from pathlib import Path
from typing import Union

from pylabrobot.config.config import Config
from pylabrobot.config.service import ConfigLoader, ConfigSaver


class FileLoader(ConfigLoader):
  """A ConfigLoader that loads from a file."""

  def load(self, r: Union[str, Path]) -> Config:
    """Load a Config object from a file."""
    with open(r, self.format_reader.open_mode,
              encoding=self.format_reader.encoding) as f:
      return self.format_reader.read(f)


class FileSaver(ConfigSaver):
  """A ConfigSaver that saves to a file."""

  def save(self, w: Union[str, Path], cfg: Config):
    """Save a Config object to a file."""
    with open(w, self.format_writer.open_mode,
              encoding=self.format_writer.encoding) as f:
      self.format_writer.write(f, cfg)
