"""

Config module. Facilitates reading and writing module-level config files.
Checks the current directory and all parent directories for a `pylabrobot.ini`
config file. If the config file does not exist, a default Config object will be
created and written to the current directory.
"""
from pathlib import Path

from pylabrobot.config.config import Config
from pylabrobot.config.service.file import MultiReader, FileWriter
from pylabrobot.config.service.ini_file import IniReader, IniWriter

DEFAULT_READER = MultiReader(
  reader_map={
    "ini": IniReader(),
  }
)

DEFAULT_WRITER = FileWriter(
  format_writer=IniWriter(),
)


def get_file(base_name: str, _dir: Path) -> Path | None:
  for ext in DEFAULT_READER.reader_map.keys():
    cfg = _dir / f"{base_name}.{ext}"
    if cfg.exists():
      return cfg
  return None


def get_config_file(base_name: str, cur_dir: str | None = None) -> Path | None:
  """Get the path to the config file.

  Args:
  base_name: The base name of the config file.

  Returns:
  The path to the config file.
  """
  if cur_dir is None:
    cur_dir = Path.cwd()

  cfg = get_file(base_name, cur_dir)
  if cfg is not None:
    return cfg

  if cur_dir.parent == cur_dir:
    return None

  return get_config_file(base_name, cur_dir.parent)


def load_config(base_file_name: str, create_default: bool = False) -> Config:
  """Load a Config object from a file.

  Args:
  base_file_name: The base file name to load.
  create_default: Whether to create a default Config object if the file does
  not exist. It will be created with file extension that is first in
  DEFAULT_READER

  Returns:
  The Config object.
  """
  cur_dir = Path.cwd()
  config_path = get_config_file(base_file_name)
  if config_path is None:
    if not create_default:
      return Config()
    config_path = cur_dir / f"{base_file_name}.{list(DEFAULT_READER.reader_map.keys())[0]}"
    DEFAULT_WRITER.write(config_path, Config())

  return DEFAULT_READER.read(config_path)
