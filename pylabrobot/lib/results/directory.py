"""Measurement results written to a directory: `rows.jsonl`, append-only JSON Lines.

`rows.jsonl` holds the rows of `table.get_table`, one JSON object per line, a read's rows appended
once the read is complete and numbered after the last read in the file. A value that is not a
finite number (an infinite optical density) is written as null. A crash mid-write leaves a cut
last line, removed before the next write.
"""

import json
import logging
import math
import os
import threading
from typing import Any, BinaryIO, Dict, Sequence

import anyio.to_thread

from .table import get_table

logger = logging.getLogger(__name__)

ROWS = "rows.jsonl"

# Bytes read at a time from the file's end, to find its last whole line.
_TAIL_CHUNK = 4096
_TIMESTAMPS = ("start_timestamp", "end_timestamp", "run_start_timestamp", "due_timestamp")
# One write at a time: each read is numbered after the one written before it.
_LOCK = threading.Lock()
# The failed write of each directory, until `check_directory` raises it.
_ERRORS: Dict[str, BaseException] = {}


def check_directory(directory: str) -> str:
  """Create `directory` with its parents if missing, check it is writable; its absolute path.

  Raises:
    ValueError: if it cannot be created, is not a directory, or is not writable.
    RuntimeError: if the last write to it failed (raised once).
  """
  path = os.path.abspath(directory)
  error = _ERRORS.pop(path, None)
  if error is not None:
    raise RuntimeError(f"results were not all written to {path}: {error}") from error
  try:
    os.makedirs(path, exist_ok=True)
  except OSError as error:
    raise ValueError(f"results directory {directory!r} cannot be created: {error}") from error
  if not os.access(path, os.W_OK | os.X_OK):
    raise ValueError(f"results directory {directory!r} is not writable")
  return path


def _get_next_read(file: BinaryIO, path: str) -> int:
  """The read after the last whole line of `file`, read from its end; a cut line after it, left
  by a crash, is removed. 0 for a file with no whole line.

  Raises:
    ValueError: if that line is not a JSON object with a whole-number `read`.
  """
  position = file.seek(0, os.SEEK_END)
  tail = b""
  while position > 0 and tail.count(b"\n") < 2:
    step = min(_TAIL_CHUNK, position)
    position -= step
    file.seek(position)
    tail = file.read(step) + tail
  whole = tail[: tail.rfind(b"\n") + 1]
  if len(whole) < len(tail):
    logger.warning("%s ends in a cut line of %d bytes; it is removed", path, len(tail) - len(whole))
    file.truncate(position + len(whole))
  if not whole:
    return 0
  try:
    read = json.loads(whole[:-1].rsplit(b"\n", 1)[-1])["read"]
  except (ValueError, KeyError, TypeError) as error:
    raise ValueError(f"the last line of {path} has no read: {error}") from None
  if not isinstance(read, int):
    raise ValueError(f"the last line of {path} has no read: {read!r}")
  return read + 1


def _encode_row(row: Dict[str, Any]) -> bytes:
  """`row` as one line of strict JSON: timestamps as ISO 8601, a non-finite value as null."""
  row = dict(row)
  for key in _TIMESTAMPS:
    if row[key] is not None:
      row[key] = row[key].isoformat()
  for key, value in row.items():
    if isinstance(value, float) and not math.isfinite(value):
      row[key] = None
  return (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _append_results(directory: str, results: Sequence[Any]) -> None:
  """Append the rows of `results` to `rows.jsonl` of `directory` in one write, and flush it to the
  disk; a write that fails part way is cut back to the file's size before it."""
  path = os.path.join(directory, ROWS)
  with _LOCK, open(path, "a+b", buffering=0) as file:
    first_read = _get_next_read(file, path)
    data = memoryview(b"".join(map(_encode_row, get_table(results, first_read=first_read))))
    size = file.seek(0, os.SEEK_END)
    try:
      while data:  # a regular file takes it all at once, but for a full disk
        data = data[file.write(data) :]
      os.fsync(file.fileno())
    except OSError:
      file.truncate(size)
      raise


async def append_results(directory: str, results: Sequence[Any]) -> None:
  """Append the rows of `results`, each result one read, to `rows.jsonl` of `directory`, in a
  thread; a cancel lets the write finish. A failure is logged, not raised, so the measurement keeps
  its results; the next `check_directory` of the directory raises it.

  Args:
    directory: as `check_directory` returned it.
    results: as `table.get_table`.
  """
  try:
    await anyio.to_thread.run_sync(_append_results, directory, results)
  except Exception as error:
    logger.error("results not written to %s: %s", directory, error, exc_info=error)
    _ERRORS[directory] = error
