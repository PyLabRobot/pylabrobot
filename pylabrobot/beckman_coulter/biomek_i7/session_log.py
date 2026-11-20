"""A run's log in a file of its own, to compare a live run with Biomek's logs of the same work.

`start_session_log(directory)` writes every line of this package's loggers to
`<directory>/biomek_i7_<YYYYMMDD_HHMMSS>.log`: the driver's commands (each move's axis targets in
counts, speed, force limits and Move All timeout; homes, valve and tip commands, grip checks),
warnings and errors, and with `usb_frames` every message to and from the boards, unframed and in
hex, which is what Biomek's Master Controller keeps in its `ace_io` table. Each line starts with
the time in milliseconds since the epoch, as the Master Controller's tables stamp theirs.

PyLabRobot's own `pylabrobot.setup_logger` writes one file per day for every PyLabRobot logger;
this one is per run and holds only the Biomek i7's lines, so one run reads on its own.
"""

import datetime
import logging
from pathlib import Path
from typing import Optional, Union

PACKAGE_LOGGER = "pylabrobot.beckman_coulter.biomek_i7"
LINE_FORMAT = "%(created_ms)d %(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _EpochMilliseconds(logging.Formatter):
  def format(self, record: logging.LogRecord) -> str:
    record.created_ms = int(record.created * 1000)
    return super().format(record)


class SessionLog:
  """A session log being written; `stop()` closes it."""

  def __init__(self, path: Path, handler: logging.Handler):
    self.path = path
    self._handler = handler

  def stop(self) -> None:
    """Stop writing the log and close its file."""
    logging.getLogger(PACKAGE_LOGGER).removeHandler(self._handler)
    self._handler.close()

  def __enter__(self) -> "SessionLog":
    return self

  def __exit__(self, *exc) -> None:
    self.stop()


def start_session_log(
  directory: Union[str, Path],
  usb_frames: bool = True,
  name: Optional[str] = None,
) -> SessionLog:
  """Start writing this package's log lines to a new timestamped file in `directory`.

  The package's loggers are set to log at DEBUG (with `usb_frames`) or INFO, and any of its
  loggers set higher is reset to follow the package's level, so every line reaches the file. What
  a console shows is up to the console's own handler and its level.

  Args:
    directory: where to write the log; made if missing.
    usb_frames: also write every message to and from the boards (DEBUG lines `<board> write <hex>`
      and `<board> read <hex>`).
    name: the file's name; default `biomek_i7_<YYYYMMDD_HHMMSS>.log`.

  Returns:
    The log being written; `.path` is its file.
  """
  directory = Path(directory)
  directory.mkdir(parents=True, exist_ok=True)
  stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
  path = directory / (name or f"biomek_i7_{stamp}.log")
  handler = logging.FileHandler(path, encoding="utf-8")
  handler.setLevel(logging.NOTSET)
  handler.setFormatter(_EpochMilliseconds(LINE_FORMAT, DATE_FORMAT))

  level = logging.DEBUG if usb_frames else logging.INFO
  package = logging.getLogger(PACKAGE_LOGGER)
  package.setLevel(level)
  for logger_name, logger in logging.root.manager.loggerDict.items():
    if (
      logger_name.startswith(PACKAGE_LOGGER + ".")
      and isinstance(logger, logging.Logger)
      and logger.level > level
    ):
      logger.setLevel(logging.NOTSET)
  package.addHandler(handler)
  package.info("session log started: %s (USB frames %s)", path, "on" if usb_frames else "off")
  return SessionLog(path, handler)
