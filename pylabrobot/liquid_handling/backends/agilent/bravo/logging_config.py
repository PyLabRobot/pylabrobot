"""Centralized logging configuration for PyBravo.

Call ``configure_logging()`` once at process startup (server, script, or CLI
tool) to install a consistent format, optional file rotation, and per-layer
log-level control across the entire ``pybravo.*`` logger hierarchy.

Environment variables
---------------------
PYBRAVO_LOG_LEVEL
    Root log level for all pybravo loggers.  Default: ``INFO``.
    Set to ``DEBUG`` to see controller/state-machine detail.

PYBRAVO_LOG_FILE
    When set, write a *duplicate* of all console output to this file with
    automatic rotation (10 MB, 3 backups).

PYBRAVO_LOG_DIR
    When set, create three separate rotated log files in this directory:
      * ``pybravo.log``   — all loggers at the configured root level
      * ``protocol.log``  — ``pybravo.protocol.*`` + ``pybravo.transport.*``
      * ``api.log``       — ``pybravo.web.*``
    Mutually exclusive with ``PYBRAVO_LOG_FILE`` (``PYBRAVO_LOG_DIR`` wins).

PYBRAVO_PROTOCOL_TRACE
    Set to ``1`` to enable TRACE-level (level 5) hex dumps for protocol
    frames and raw transport bytes.  Zero-cost when disabled — frame
    formatting is gated behind ``logger.isEnabledFor(TRACE)``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

_DEFAULT_FMT = "%(asctime)s  %(levelname)-7s  %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%H:%M:%S"

_ROTATION_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_ROTATION_BACKUP_COUNT = 3

_configured = False


class _NamespaceFilter(logging.Filter):
  """Accept only records whose logger name starts with one of *prefixes*."""

  def __init__(self, *prefixes: str) -> None:
    super().__init__()
    self._prefixes = prefixes

  def filter(self, record: logging.LogRecord) -> bool:
    return any(record.name.startswith(p) for p in self._prefixes)


def configure_logging(
  *,
  level: int | str | None = None,
  log_file: str | None = None,
  log_dir: str | None = None,
  protocol_trace: bool | None = None,
  verbose: bool = False,
) -> None:
  """Set up the process-wide logging configuration.

  Parameters
  ----------
  level, log_file, log_dir, protocol_trace
      Override the corresponding ``PYBRAVO_*`` environment variable.
      Explicit arguments take precedence over env vars.
  verbose
      Convenience shortcut: when *True*, sets the root level to ``DEBUG``
      (scripts typically wire this to ``--verbose / -v``).
  """
  global _configured
  if _configured:
    return
  _configured = True

  raw_level = level if level is not None else os.environ.get("PYBRAVO_LOG_LEVEL", "INFO")
  if verbose:
    raw_level = "DEBUG"
  if isinstance(raw_level, int):
    root_level = raw_level
  else:
    root_level = getattr(logging, str(raw_level).upper(), logging.INFO)

  enable_trace = _resolve_bool(protocol_trace, "PYBRAVO_PROTOCOL_TRACE", False)
  env_log_file = _resolve(log_file, "PYBRAVO_LOG_FILE", "")
  env_log_dir = _resolve(log_dir, "PYBRAVO_LOG_DIR", "")

  protocol_level = TRACE if enable_trace else root_level

  console_handler = logging.StreamHandler()
  console_handler.setLevel(root_level)
  console_handler.setFormatter(logging.Formatter(_DEFAULT_FMT, datefmt=_DEFAULT_DATEFMT))

  root = logging.getLogger()
  root.setLevel(min(root_level, protocol_level))
  root.addHandler(console_handler)

  protocol_loggers = [
    logging.getLogger("pybravo.protocol"),
    logging.getLogger("pybravo.transport"),
  ]
  for pl in protocol_loggers:
    pl.setLevel(protocol_level)

  if env_log_dir:
    _setup_dir_handlers(env_log_dir, root_level, protocol_level)
  elif env_log_file:
    _setup_file_handler(env_log_file, root_level)

  _suppress_noisy_libraries()


def _setup_file_handler(path: str, level: int) -> None:
  handler = logging.handlers.RotatingFileHandler(
    path,
    maxBytes=_ROTATION_MAX_BYTES,
    backupCount=_ROTATION_BACKUP_COUNT,
    encoding="utf-8",
  )
  handler.setLevel(level)
  handler.setFormatter(logging.Formatter(_DEFAULT_FMT, datefmt=_DEFAULT_DATEFMT))
  logging.getLogger().addHandler(handler)


def _setup_dir_handlers(dir_path: str, root_level: int, protocol_level: int) -> None:
  log_dir = Path(dir_path)
  log_dir.mkdir(parents=True, exist_ok=True)

  fmt = logging.Formatter(_DEFAULT_FMT, datefmt=_DEFAULT_DATEFMT)

  all_handler = logging.handlers.RotatingFileHandler(
    str(log_dir / "pybravo.log"),
    maxBytes=_ROTATION_MAX_BYTES,
    backupCount=_ROTATION_BACKUP_COUNT,
    encoding="utf-8",
  )
  all_handler.setLevel(root_level)
  all_handler.setFormatter(fmt)
  logging.getLogger().addHandler(all_handler)

  proto_handler = logging.handlers.RotatingFileHandler(
    str(log_dir / "protocol.log"),
    maxBytes=_ROTATION_MAX_BYTES,
    backupCount=_ROTATION_BACKUP_COUNT,
    encoding="utf-8",
  )
  proto_handler.setLevel(protocol_level)
  proto_handler.setFormatter(fmt)
  proto_handler.addFilter(_NamespaceFilter("pybravo.protocol", "pybravo.transport"))
  logging.getLogger().addHandler(proto_handler)

  api_handler = logging.handlers.RotatingFileHandler(
    str(log_dir / "api.log"),
    maxBytes=_ROTATION_MAX_BYTES,
    backupCount=_ROTATION_BACKUP_COUNT,
    encoding="utf-8",
  )
  api_handler.setLevel(root_level)
  api_handler.setFormatter(fmt)
  api_handler.addFilter(_NamespaceFilter("pybravo.web"))
  logging.getLogger().addHandler(api_handler)


def _suppress_noisy_libraries() -> None:
  for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "websockets"):
    logging.getLogger(name).setLevel(logging.WARNING)


def _resolve(explicit, env_key: str, default: str) -> str:
  if explicit is not None:
    return str(explicit)
  return os.environ.get(env_key, default)


def _resolve_bool(explicit, env_key: str, default: bool) -> bool:
  if explicit is not None:
    return bool(explicit)
  val = os.environ.get(env_key, "")
  if not val:
    return default
  return val.strip() in ("1", "true", "yes")
