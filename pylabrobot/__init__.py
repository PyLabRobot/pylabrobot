import datetime
import logging
from pathlib import Path
from typing import Optional, Union

from pylabrobot.__version__ import __version__
from pylabrobot.config import Config, load_config
from pylabrobot.io import end_validation, start_capture, stop_capture, validate

CONFIG_FILE_NAME = "pylabrobot"

CONFIG = load_config(CONFIG_FILE_NAME, create_default=False)


def project_root() -> Path:
  """
  Get the root directory of the project.
  From https://stackoverflow.com/a/53465812
  Returns:
    The root directory of the project.
  """
  return Path(__file__).parent.parent


def setup_logger(log_dir: Optional[Union[Path, str]], level: int):
  """
  Set up the logger for pylabrobot. If the log_dir does not exist, it will be created.

  Args:
    log_dir: The directory to store the log files. If None, no log files will be created.
    level: The logging level.
  """
  # Create a logger
  if log_dir is not None:
    if isinstance(log_dir, str):
      log_dir = Path(log_dir)
    if not log_dir.exists():
      log_dir.mkdir(parents=True)
  logger = logging.getLogger("pylabrobot")
  logger.setLevel(level)

  now = datetime.datetime.now().strftime("%Y%m%d")
  # remove file handler if it exists
  if len(logger.handlers) > 0:
    logger.handlers.clear()
    # delete empty log file if it has been created
    log_file = Path(f"pylabrobot-{now}.log")
    if log_file.exists() and log_file.stat().st_size == 0:
      log_file.unlink()

  # Add a file handler, if log_dir is not None
  if log_dir is not None:
    fh = logging.FileHandler(log_dir / f"pylabrobot-{now}.log")
    fh.setLevel(logging.NOTSET)  # logs everything it receives, but the logger level can filter
    fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)


def configure(cfg: Config):
  """Configure pylabrobot."""
  setup_logger(cfg.logging.log_dir, cfg.logging.level)


configure(CONFIG)
