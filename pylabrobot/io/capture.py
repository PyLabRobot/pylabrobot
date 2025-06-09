import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from pylabrobot.__version__ import __version__
from pylabrobot.io.errors import ValidationError

_capture_or_validation_active = False


def get_capture_or_validation_active() -> bool:
  return _capture_or_validation_active


@dataclass
class Command:
  module: str
  device_id: str
  action: str


class _CaptureWriter:
  def __init__(self):
    self._path = None
    self._tempfile = None

  def start(self, path: Path):
    if self._tempfile is not None:
      raise RuntimeError("io capture already active")
    self._path = path

    self._tempfile = tempfile.NamedTemporaryFile(delete=False)
    self._tempfile.write(b'{\n  "version": "')
    self._tempfile.write(__version__.encode("utf-8"))
    self._tempfile.write(b'",\n')
    self._tempfile.write(b'  "commands": [\n')
    self._tempfile.flush()

    global _capture_or_validation_active
    _capture_or_validation_active = True

  def record(self, command: Command):
    if self._tempfile is not None:
      encoded_command = json.dumps(command.__dict__, indent=2).encode()
      # add 4 spaces to each line
      encoded_command = b"    " + encoded_command.replace(b"\n", b"\n    ")
      self._tempfile.write(encoded_command)
      self._tempfile.write(b",\n")
      self._tempfile.flush()

  def stop(self):
    if self._path is None or self._tempfile is None:
      raise RuntimeError("io capture not active. Call start() first.")

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

    self._path = None

    global _capture_or_validation_active
    _capture_or_validation_active = False

    self._tempfile = None

  @property
  def capture_active(self):
    return self._tempfile is not None


class CaptureReader:
  def __init__(self, path: str):
    self.path = path
    self.commands: List[dict] = []
    with open(path, "r") as f:
      data = json.load(f)
      for c in data["commands"]:
        self.commands.append(c)
    self._command_idx = 0

  def start(self):
    global _capture_or_validation_active
    _capture_or_validation_active = True

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
    self.reset()

  def reset(self):
    self._command_idx = 0

    global _capture_or_validation_active
    _capture_or_validation_active = True


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
