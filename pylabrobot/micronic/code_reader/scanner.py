"""Scanner classes that acquire a rack image for the Micronic driver."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - local scanner helper execution is the interface.
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Sequence

from .errors import MicronicError


class Scanner(ABC):
  """Abstract scanner that writes a rack image to disk on demand."""

  image_extension: str

  @abstractmethod
  def acquire(self, output_path: Path, timeout_ms: int) -> dict[str, object]:
    """Write a rack image to ``output_path`` and return acquisition metadata."""


class TwainScanner(Scanner):
  """Windows TWAIN scanner driven by an operator-installed helper executable.

  Resolves the helper path from (in order): the ``twain_scanner_path`` argument,
  the ``MICRONIC_TWAIN_SCANNER_PATH`` environment variable, or ``twain_scan`` /
  ``twain_scan.exe`` on PATH. Raises ``MicronicError`` if none resolve.
  """

  image_extension = "bmp"

  def __init__(
    self,
    twain_scanner_path: Optional[str] = None,
    twain_source: str = "AVA6PlusG",
  ):
    resolved = twain_scanner_path or _resolve_twain_scanner_path()
    if resolved is None:
      raise MicronicError(
        "No TWAIN helper was found. Pass twain_scanner_path, set "
        "MICRONIC_TWAIN_SCANNER_PATH, or put twain_scan on PATH."
      )
    self.twain_scanner_path = resolved
    self.twain_source = twain_source

  def acquire(self, output_path: Path, timeout_ms: int) -> dict[str, object]:
    command = [self.twain_scanner_path, str(output_path), self.twain_source, str(timeout_ms)]
    return _run_scan_command(command, output_path, timeout_ms, source="twain")


class SaneScanner(Scanner):
  """Linux SANE scanner driven through the ``scanimage`` CLI."""

  image_extension = "tiff"

  def __init__(
    self,
    sane_device: Optional[str] = None,
    scanimage_path: Optional[str] = None,
  ):
    resolved = scanimage_path or shutil.which("scanimage")
    if resolved is None:
      raise MicronicError("scanimage was not found on PATH. Install SANE or pass scanimage_path.")
    self.scanimage_path = resolved
    self.sane_device = sane_device

  def acquire(self, output_path: Path, timeout_ms: int) -> dict[str, object]:
    command = [self.scanimage_path]
    if self.sane_device:
      command.extend(["--device-name", self.sane_device])
    command.extend(["--format=tiff", "--output-file", str(output_path)])
    return _run_scan_command(command, output_path, timeout_ms, source="sane")


def _run_scan_command(
  command: Sequence[str],
  output_path: Path,
  timeout_ms: int,
  source: str,
) -> dict[str, object]:
  try:
    completed = subprocess.run(  # nosec B603 - operator-configured command, shell=False.
      list(command),
      check=False,
      capture_output=True,
      text=True,
      timeout=(timeout_ms / 1000) + 15,
    )
  except FileNotFoundError as exc:
    raise MicronicError(f"Scan command was not found: {command[0]}") from exc

  if completed.returncode != 0:
    raise MicronicError(
      "Scan command failed with exit code "
      f"{completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
    )
  if not output_path.exists():
    raise MicronicError(f"Scan command did not create image: {output_path}")
  return {
    "stdout": completed.stdout.strip(),
    "stderr": completed.stderr.strip(),
    "source": source,
    "command": list(command),
  }


def _resolve_twain_scanner_path() -> Optional[str]:
  return (
    os.environ.get("MICRONIC_TWAIN_SCANNER_PATH")
    or shutil.which("twain_scan.exe")
    or shutil.which("twain_scan")
  )
