"""Asynchronous I/O for local command-line programs."""

import asyncio
import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

from pylabrobot.io.capture import Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.serializer import SerializableMixin

if TYPE_CHECKING:
  from pylabrobot.io.capture import CaptureReader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandLineResult:
  """Result returned by :meth:`CommandLineTransport.run`.

  Attributes:
    returncode: Process exit code.
    stdout: Decoded standard output.
    stderr: Decoded standard error.
  """

  returncode: int
  stdout: str
  stderr: str


@dataclass
class CommandLineCommand(Command):
  """Captured command-line execution."""

  arguments: list[str]
  timeout: float
  returncode: int
  stdout: str
  stderr: str

  def __init__(
    self,
    device_id: str,
    arguments: list[str],
    timeout: float,
    returncode: int,
    stdout: str,
    stderr: str,
    action: str = "run",
    module: str = "command_line",
  ) -> None:
    """Initialize a captured command-line execution."""
    super().__init__(module=module, device_id=device_id, action=action)
    self.arguments = arguments
    self.timeout = timeout
    self.returncode = returncode
    self.stdout = stdout
    self.stderr = stderr


class CommandLineTransport(SerializableMixin):
  """I/O transport for one operator-installed executable.

  The transport owns the executable identity and process lifecycle. Calls are serialized so one
  transport cannot start overlapping child processes.

  Args:
    human_readable_device_name: Name used in logs and errors.
    executable: Absolute path or executable name to resolve from ``PATH`` during setup.
  """

  def __init__(self, human_readable_device_name: str, executable: str) -> None:
    """Initialize the command-line transport configuration."""
    if not human_readable_device_name:
      raise ValueError("human_readable_device_name must not be empty")
    if not executable:
      raise ValueError("executable must not be empty")
    if get_capture_or_validation_active():
      raise RuntimeError(
        "Cannot create a new CommandLineTransport while capture or validation is active"
      )

    self.human_readable_device_name = human_readable_device_name
    self.executable = executable
    self._is_setup = False
    self._process: Optional[asyncio.subprocess.Process] = None
    self._run_lock = asyncio.Lock()

  @property
  def is_setup(self) -> bool:
    """Return whether the executable has been resolved and the transport is ready."""
    return self._is_setup

  async def setup(self) -> None:
    """Resolve the configured executable and mark the transport ready.

    Raises:
      FileNotFoundError: If the executable cannot be resolved.
    """
    if self._is_setup:
      return
    resolved = shutil.which(self.executable)
    if resolved is None:
      raise FileNotFoundError(f"Command-line executable was not found: {self.executable}")
    self.executable = resolved
    self._is_setup = True
    logger.info("Set up command-line I/O for %s at %s", self.human_readable_device_name, resolved)

  async def stop(self) -> None:
    """Stop any active child process and mark the transport closed."""
    self._is_setup = False
    process = self._process
    if process is not None and process.returncode is None:
      try:
        process.kill()
      except ProcessLookupError:
        pass
      await process.wait()
    logger.info("Stopped command-line I/O for %s", self.human_readable_device_name)

  async def run(self, arguments: Sequence[str], timeout: float) -> CommandLineResult:
    """Run the configured executable with arguments and collect its output.

    Args:
      arguments: Arguments passed to the configured executable.
      timeout: Maximum runtime in seconds.

    Returns:
      The process exit code and decoded output streams.

    Raises:
      RuntimeError: If :meth:`setup` has not been called.
      ValueError: If ``timeout`` is not positive.
      asyncio.TimeoutError: If the process does not finish before ``timeout``.
    """
    if not self._is_setup:
      raise RuntimeError(
        f"Command-line I/O for '{self.human_readable_device_name}' is not set up; "
        "call setup() first"
      )
    if timeout <= 0:
      raise ValueError("timeout must be positive")

    command_arguments = list(arguments)
    async with self._run_lock:
      process = await asyncio.create_subprocess_exec(
        self.executable,
        *command_arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
      )
      self._process = process
      try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
      except (asyncio.CancelledError, asyncio.TimeoutError):
        if process.returncode is None:
          try:
            process.kill()
          except ProcessLookupError:
            pass
        await process.communicate()
        raise
      finally:
        self._process = None

    returncode = process.returncode
    if returncode is None:
      raise RuntimeError("command completed without an exit code")
    result = CommandLineResult(
      returncode=returncode,
      stdout=stdout.decode("utf-8", errors="replace"),
      stderr=stderr.decode("utf-8", errors="replace"),
    )
    logger.log(
      LOG_LEVEL_IO,
      "[%s] run %s -> %d",
      self.executable,
      command_arguments,
      result.returncode,
    )
    capturer.record(
      CommandLineCommand(
        device_id=self.executable,
        arguments=command_arguments,
        timeout=timeout,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
      )
    )
    return result

  def serialize(self) -> dict:
    """Serialize the command-line transport configuration."""
    return {
      "human_readable_device_name": self.human_readable_device_name,
      "executable": self.executable,
    }


class CommandLineValidator(CommandLineTransport):
  """Replay captured command-line executions without starting a process."""

  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    executable: str,
  ) -> None:
    """Initialize a command-line capture validator."""
    self.cr = cr
    self.human_readable_device_name = human_readable_device_name
    self.executable = executable
    self._is_setup = False
    self._process = None
    self._run_lock = asyncio.Lock()

  async def setup(self) -> None:
    """Mark the validator ready without resolving an executable."""
    self._is_setup = True

  async def stop(self) -> None:
    """Mark the validator closed."""
    self._is_setup = False

  async def run(self, arguments: Sequence[str], timeout: float) -> CommandLineResult:
    """Validate an execution against the next captured command."""
    if not self._is_setup:
      raise RuntimeError(
        f"Command-line I/O for '{self.human_readable_device_name}' is not set up; "
        "call setup() first"
      )
    next_command = CommandLineCommand(**self.cr.next_command())
    if not (
      next_command.module == "command_line"
      and next_command.device_id == self.executable
      and next_command.action == "run"
    ):
      raise ValidationError(
        f"Expected command-line run for {self.executable}, got "
        f"{next_command.module} {next_command.action} for {next_command.device_id}"
      )
    if next_command.arguments != list(arguments):
      raise ValidationError(
        f"Command-line arguments differ: expected {next_command.arguments}, got {list(arguments)}"
      )
    if next_command.timeout != timeout:
      raise ValidationError(
        f"Command-line timeout differs: expected {next_command.timeout}, got {timeout}"
      )
    return CommandLineResult(
      returncode=next_command.returncode,
      stdout=next_command.stdout,
      stderr=next_command.stderr,
    )
