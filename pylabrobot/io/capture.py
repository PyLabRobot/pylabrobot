import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from pylabrobot import __version__
from pylabrobot.io.errors import ValidationError


@dataclass
class Command:
  module: str
  device_id: str
  action: str


class _CaptureWriter:
  def __init__(self):
    self._path = None
    self._capture_active = False
    self._tempfile = None

  def start(self, path: Path):
    if self._capture_active:
      raise RuntimeError("io capture already active")
    self._path = path
    self._capture_active = True

    temp_file = tempfile.NamedTemporaryFile(delete=False)
    self._tempfile = temp_file
    self._tempfile.write('{\n  "version": "'.encode())
    self._tempfile.write(__version__.encode())
    self._tempfile.write(b'",\n')
    self._tempfile.write(b'  "commands": [\n')
    self._tempfile.flush()

  def record(self, command: Command):
    if self._capture_active:
      if self._tempfile is not None:
        encoded_command = json.dumps(command.__dict__, indent=2).encode()
        # add 4 spaces to each line
        encoded_command = b"    " + encoded_command.replace(b"\n", b"\n    ")
        self._tempfile.write(encoded_command)
        self._tempfile.write(b",\n")
        self._tempfile.flush()

  def stop(self):
    if self._path is None:
      raise RuntimeError("io capture not active. Call start() first.")

    if self._tempfile is not None:
      self._tempfile.seek(self._tempfile.tell() - 2)
      # if previous line ends with a comma, delete it
      if self._tempfile.read(1) == b",":
        self._tempfile.seek(self._tempfile.tell() - 1)
        self._tempfile.write(b"\n")
        self._tempfile.write(b"  ]\n}")
      else:
        self._tempfile.write(b"]\n}")
      self._tempfile.flush()
      self._tempfile.seek(0)

    with open(self._path, "wb") as f:
      f.write(self._tempfile.read())

    print(f"Validation file written to {self._path}")

    self._capture_active = False
    self._path = None

  @property
  def capture_active(self):
    return self._capture_active


class CaptureReader:
  def __init__(self, path: str):
    self.path = path
    self.commands: List[dict] = []
    with open(path, "r") as f:
      data = json.load(f)
      for c in data["commands"]:
        self.commands.append(c)
    self._command_idx = 0

  def next_command(self) -> dict:
    command = self.commands[self._command_idx]
    self._command_idx += 1
    return command

  def done(self):
    if self._command_idx < len(self.commands):
      left = len(self.commands) - self._command_idx
      next_command = self.commands[self._command_idx]
      raise ValidationError(
        f"Log file not fully read, {left} lines left. First command: {next_command}"
      )
    print("Validation successful!")

  def reset(self):
    self._command_idx = 0


capturer = _CaptureWriter()


def start_capture(fp: Union[Path, str] = Path("./validation")):
  """Start capturing all IO events to log file."""
  if not isinstance(fp, Path):
    fp = Path(fp)
  if fp.is_dir():
    raise ValueError("Path is a directory, please provide a file path.")
  capturer.start(fp)


def stop_capture():
  """Stop capturing all IO events to log file."""
  capturer.stop()
