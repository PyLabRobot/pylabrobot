"""Scanner classes that acquire a rack image for the Micronic driver."""

from __future__ import annotations

import asyncio
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Sequence

from pylabrobot.io.command_line import CommandLineTransport

from .errors import MicronicError


class Scanner(ABC):
  """Abstract scanner that writes a rack image to disk on demand."""

  image_extension: str

  async def setup(self) -> None:
    """Prepare the scanner for image acquisition."""

  async def stop(self) -> None:
    """Release scanner acquisition resources."""

  @abstractmethod
  async def acquire(self, output_path: Path, timeout: float) -> dict[str, object]:
    """Write a rack image to ``output_path``.

    Args:
      output_path: Destination for the acquired image.
      timeout: Scanner acquisition timeout in seconds.

    Returns:
      Metadata describing the scanner command.
    """


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
    command_line: Optional[CommandLineTransport] = None,
  ) -> None:
    """Initialize a TWAIN scanner.

    Args:
      twain_scanner_path: Path to the operator-installed TWAIN helper. When omitted, resolve it
        from ``MICRONIC_TWAIN_SCANNER_PATH`` or ``PATH``.
      twain_source: TWAIN source name passed to the helper.
      command_line: Configured command-line transport. When supplied, its executable takes
        precedence over ``twain_scanner_path``.

    Raises:
      MicronicError: If no TWAIN helper can be resolved.
    """
    if command_line is None:
      resolved = twain_scanner_path or _resolve_twain_scanner_path()
      if resolved is None:
        raise MicronicError(
          "No TWAIN helper was found. Pass twain_scanner_path, set "
          "MICRONIC_TWAIN_SCANNER_PATH, or put twain_scan on PATH."
        )
      command_line = CommandLineTransport(
        human_readable_device_name="Micronic TWAIN rack scanner",
        executable=resolved,
      )
    self.command_line = command_line
    self.twain_scanner_path = command_line.executable
    self.twain_source = twain_source

  async def setup(self) -> None:
    """Resolve and prepare the TWAIN helper executable."""
    try:
      await self.command_line.setup()
    except FileNotFoundError as exc:
      raise MicronicError(str(exc)) from exc
    self.twain_scanner_path = self.command_line.executable

  async def stop(self) -> None:
    """Stop the TWAIN helper transport."""
    await self.command_line.stop()

  async def acquire(self, output_path: Path, timeout: float) -> dict[str, object]:
    """Acquire a BMP image through the configured TWAIN helper.

    Args:
      output_path: Destination for the acquired image.
      timeout: Scanner acquisition timeout in seconds.

    Returns:
      Metadata describing the scanner command.
    """
    timeout_ms = max(1, int(timeout * 1000))
    arguments = [str(output_path), self.twain_source, str(timeout_ms)]
    return await _run_scan_command(
      self.command_line,
      arguments,
      output_path,
      timeout,
      source="twain",
    )


class SaneScanner(Scanner):
  """Linux SANE scanner driven through the ``scanimage`` CLI."""

  image_extension = "tiff"

  def __init__(
    self,
    sane_device: Optional[str] = None,
    scanimage_path: Optional[str] = None,
    command_line: Optional[CommandLineTransport] = None,
  ) -> None:
    """Initialize a SANE scanner.

    Args:
      sane_device: Optional SANE device identifier passed to ``scanimage``.
      scanimage_path: Path to ``scanimage``. When omitted, resolve it from ``PATH``.
      command_line: Configured command-line transport. When supplied, its executable takes
        precedence over ``scanimage_path``.

    Raises:
      MicronicError: If ``scanimage`` cannot be resolved.
    """
    if command_line is None:
      resolved = scanimage_path or shutil.which("scanimage")
      if resolved is None:
        raise MicronicError("scanimage was not found on PATH. Install SANE or pass scanimage_path.")
      command_line = CommandLineTransport(
        human_readable_device_name="Micronic SANE rack scanner",
        executable=resolved,
      )
    self.command_line = command_line
    self.scanimage_path = command_line.executable
    self.sane_device = sane_device

  async def setup(self) -> None:
    """Resolve and prepare the ``scanimage`` executable."""
    try:
      await self.command_line.setup()
    except FileNotFoundError as exc:
      raise MicronicError(str(exc)) from exc
    self.scanimage_path = self.command_line.executable

  async def stop(self) -> None:
    """Stop the SANE command-line transport."""
    await self.command_line.stop()

  async def acquire(self, output_path: Path, timeout: float) -> dict[str, object]:
    """Acquire a TIFF image through ``scanimage``.

    Args:
      output_path: Destination for the acquired image.
      timeout: Scanner acquisition timeout in seconds.

    Returns:
      Metadata describing the scanner command.
    """
    arguments: list[str] = []
    if self.sane_device:
      arguments.extend(["--device-name", self.sane_device])
    arguments.extend(["--format=tiff", "--output-file", str(output_path)])
    return await _run_scan_command(
      self.command_line,
      arguments,
      output_path,
      timeout,
      source="sane",
    )


async def _run_scan_command(
  command_line: CommandLineTransport,
  arguments: Sequence[str],
  output_path: Path,
  timeout: float,
  source: str,
) -> dict[str, object]:
  """Run a scanner helper and validate its output image.

  Args:
    command_line: Transport used to execute the scanner helper.
    arguments: Arguments for the configured helper executable.
    output_path: Image path the helper must create.
    timeout: Scanner acquisition timeout in seconds.
    source: Scanner backend name stored in the returned metadata.

  Returns:
    Scanner command metadata.

  Raises:
    MicronicError: If the helper is missing, times out, fails, or creates no image.
  """
  try:
    completed = await command_line.run(arguments, timeout=timeout + 15)
  except FileNotFoundError as exc:
    raise MicronicError(f"Scan command was not found: {command_line.executable}") from exc
  except asyncio.TimeoutError as exc:
    raise MicronicError(
      f"Scan command timed out after {timeout:g} seconds: {command_line.executable}"
    ) from exc

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
    "command": [command_line.executable, *arguments],
  }


def _resolve_twain_scanner_path() -> Optional[str]:
  """Resolve the operator-installed TWAIN helper path, if available."""
  return (
    os.environ.get("MICRONIC_TWAIN_SCANNER_PATH")
    or shutil.which("twain_scan.exe")
    or shutil.which("twain_scan")
  )
