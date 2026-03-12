import datetime
import logging
import sys
from pathlib import Path
from typing import Optional, Union

from pylabrobot.__version__ import __version__
from pylabrobot.config import Config, load_config
from pylabrobot.io import end_validation, start_capture, stop_capture, validate

CONFIG_FILE_NAME = "pylabrobot"

CONFIG = load_config(CONFIG_FILE_NAME, create_default=False)


def _is_running_in_jupyter() -> bool:
  """
  Check if the code is running in a Jupyter notebook environment.

  Returns:
    True if running in Jupyter, False otherwise.
  """
  try:
    shell = get_ipython().__class__.__name__  # type: ignore[name-defined]
    return bool(shell == "ZMQInteractiveShell")
  except NameError:
    return False


def project_root() -> Path:
  """
  Get the root directory of the project.
  From https://stackoverflow.com/a/53465812
  Returns:
    The root directory of the project.
  """
  return Path(__file__).parent.parent


LOG_FILE_EXTENSIONS = (".log", ".txt", ".jsonl")


def setup_logger(log_dir: Optional[Union[Path, str]], level: int):
  """
  Set up the logger for pylabrobot. If the log_dir does not exist, it will be created.

  Args:
    log_dir: Path to a directory or a file. If it ends in .log, .txt, or .jsonl, it is treated as
      a file path and logs are written there directly. Otherwise, it is treated as a directory and
      a date-stamped file (pylabrobot-YYYYMMDD.log) is created inside it. If None, no log files
      will be created.
    level: The logging level.
  """
  log_path = None
  if log_dir is not None:
    if isinstance(log_dir, str):
      log_dir = Path(log_dir)
    if log_dir.suffix in LOG_FILE_EXTENSIONS:
      log_path = log_dir
      log_dir = log_path.parent
    else:
      now = datetime.datetime.now().strftime("%Y%m%d")
      log_path = log_dir / f"pylabrobot-{now}.log"
    if not log_dir.exists():
      log_dir.mkdir(parents=True)

  logger = logging.getLogger("pylabrobot")
  logger.setLevel(level)

  now = datetime.datetime.now().strftime("%Y%m%d")
  # remove existing file handlers (but keep stream handlers like those from verbose())
  for handler in logger.handlers[:]:
    if isinstance(handler, logging.FileHandler):
      logger.removeHandler(handler)
      # delete empty log file if it has been created
      log_file_path = Path(handler.baseFilename)
      if log_file_path.exists() and log_file_path.stat().st_size == 0:
        log_file_path.unlink()

  # Add a file handler, if log_dir is not None
  if log_path is not None:
    fh = logging.FileHandler(log_path)
    fh.setLevel(logging.NOTSET)  # logs everything it receives, but the logger level can filter
    fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)

  logger.propagate = False


def configure(cfg: Config):
  """Configure pylabrobot."""
  setup_logger(cfg.logging.log_dir, cfg.logging.level)
  # Enable verbose mode by default when running in Jupyter notebook
  if _is_running_in_jupyter():
    verbose(True)


def verbose(make_verbose: bool, level: int = logging.INFO) -> None:
  """Add a StreamHandler to the pylabrobot logger to make logging output visible to the console.
  If set to False, remove the console StreamHandler. This only removes StreamHandlers that output
  to sys.stdout or sys.stderr, and will not affect FileHandlers or other subclasses.
  """
  logger = logging.getLogger("pylabrobot")
  for handler in logger.handlers[:]:  # iterate over a copy to allow safe removal
    if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
      if handler.stream in (sys.stdout, sys.stderr):
        logger.removeHandler(handler)
  if make_verbose:
    logger_level = min(logger.level, level) if logger.level != logging.NOTSET else level
    logger.setLevel(logger_level)
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


configure(CONFIG)
