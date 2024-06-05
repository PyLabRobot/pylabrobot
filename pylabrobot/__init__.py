import datetime
import logging
from pathlib import Path

from pylabrobot.__version__ import __version__

from pylabrobot.config import load_config, Config

CONFIG_FILE_NAME = "pylabrobot"

CONFIG = load_config(CONFIG_FILE_NAME, create_default=True)
"""The loaded configuration for pylabrobot."""


def setup_logger(log_dir: Path | str, level: int):
  """
  Set up the logger for pylabrobot. If the log_dir does not exist, it will be created.

  Args:
    log_dir: The directory to store the log files.
    level: The logging level.

  """
  # Create a logger
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
    if log_file.exists():
      if log_file.open().read() == "":
        log_file.unlink()

  # Add a file handler
  fh = logging.FileHandler(log_dir / f"pylabrobot-{now}.log")
  fh.setLevel(level)
  fh.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
  logger.addHandler(fh)


def configure(cfg: Config):
  """
  Configure pylabrobot.

  Args:
    cfg: The Config object.
  """
  setup_logger(cfg.logging.log_dir, cfg.logging.level)


configure(CONFIG)
