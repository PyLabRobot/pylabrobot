"""

Config module. Facilitates reading and writing module-level config files.
Checks the current directory and all parent directories for a `pylabrobot.ini`
config file. If the config file does not exist, a default Config object will be
created and written to the project directory containing the .git directory. If
this does not exist, the current directory will be used.
"""
from pathlib import Path
from typing import Optional, Union

from pylabrobot.config.config import Config
from pylabrobot.config.service.file import MultiFormatFileReader, FileWriter
from pylabrobot.config.service.ini_file import IniReader, IniWriter
from pylabrobot.config.service.json_config import JsonReader

DEFAULT_CONFIG_READER = MultiFormatFileReader(
  reader_map={
    "ini": IniReader(),
    "json": JsonReader()
  }
)

DEFAULT_CONFIG_WRITER = FileWriter(
  format_writer=IniWriter(),
)


def get_file(base_name: str, _dir: Path) -> Optional[Path]:
  for ext in DEFAULT_CONFIG_READER.reader_map.keys():
    cfg = _dir / f"{base_name}.{ext}"
    if cfg.exists():
      return cfg
  return None


def get_config_file(
  base_name: str,
  cur_dir: Optional[Union[str, Path]] = None
) -> Optional[Path]:
  """Get the path to the config file.

  Args:
  base_name: The base name of the config file.

  Returns:
  The path to the config file.
  """
  cdir = Path(cur_dir) if cur_dir is not None else Path.cwd()

  cfg = get_file(base_name, cdir)
  if cfg is not None:
    return cfg

  if cdir.parent == cdir:
    return None

  return get_config_file(base_name, cdir.parent)


def get_dir_to_create_config_file_in() -> Path:
  """Crawls parent directories and looks for a .git directory to determine the
  root of the project. If no .git directory is found, the current directory is
  returned."""
  cur_dir = Path.cwd()
  for parent in cur_dir.parents:
    if (parent / ".git").exists():
      return parent
  return cur_dir


def load_config(base_file_name: str, create_default: bool = False,
                create_module_level: bool = True) -> Config:
  """Load a Config object from a file.

  Args:
  base_file_name: The base file name to load.
  create_default: Whether to create a default Config object if the file does
  not exist. It will be created with file extension that is first in
  DEFAULT_READER.
  create_module_level: Whether to create the default file in the module level

  Returns:
  The Config object.
  """
  config_path = get_config_file(base_file_name)
  if config_path is None:
    if not create_default:
      return Config()
    create_dir = get_dir_to_create_config_file_in() if create_module_level else Path.cwd()
    config_path = create_dir / f"{base_file_name}.{list(DEFAULT_CONFIG_READER.reader_map.keys())[0]}"
    DEFAULT_CONFIG_WRITER.write(config_path, Config())

  return DEFAULT_CONFIG_READER.read(config_path)
