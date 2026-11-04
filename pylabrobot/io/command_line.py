"""Asynchronous I/O for local command-line programs."""

import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

import anyio
import anyio.abc

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
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


class _Invocation:
  """Carry one invocation's result and cancellation request across tasks."""

  def __init__(self) -> None:
    """Create an invocation before submitting its owning task."""
    self.done = anyio.Event()
    self.scope: Optional[anyio.CancelScope] = None
    self.cancel_requested = False
    self.output: Optional[tuple[int, bytes, bytes]] = None
    self.error: Optional[BaseException] = None

  def cancel(self) -> None:
    """Request cancellation even if the owning task has not started yet."""
    self.cancel_requested = True
    if self.scope is not None:
      self.scope.cancel()


class CommandLineTransport(SerializableMixin, HasLifespan):
  """I/O transport for one operator-installed executable.

  The transport owns the executable identity and an invocation task group. Each invocation task
  owns its child process. Calls are serialized so child processes cannot overlap.

  Args:
    human_readable_device_name: Name used in logs and errors.
    executable: Absolute path or executable name to resolve from ``PATH`` during setup.
  """

  def __init__(self, human_readable_device_name: str, executable: str) -> None:
    """Initialize the command-line transport configuration."""
    super().__init__()
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
    self._task_group: Optional[anyio.abc.TaskGroup] = None
    self._run_lock = anyio.Lock()

  @property
  def is_setup(self) -> bool:
    """Return whether the executable has been resolved and the transport is ready."""
    return self._is_setup

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Resolve the executable and scope invocation tasks to this lifespan.

    Raises:
      FileNotFoundError: If the executable cannot be resolved.
    """
    resolved = await anyio.to_thread.run_sync(shutil.which, self.executable)
    if resolved is None:
      raise FileNotFoundError(f"Command-line executable was not found: {self.executable}")
    await super()._enter_lifespan(stack)
    self.executable = resolved
    self._task_group = await stack.enter_task_group()
    self._is_setup = True
    stack.push_shielded_async_callback(self._teardown)
    logger.info("Set up command-line I/O for %s at %s", self.human_readable_device_name, resolved)

  async def _teardown(self) -> None:
    """Stop admission and cancel the invocation task group before it joins."""
    self._is_setup = False
    group = self._task_group
    self._task_group = None
    if group is not None:
      group.cancel_scope.cancel()

  @staticmethod
  async def _communicate(process: anyio.abc.Process) -> tuple[bytes, bytes]:
    """Drain both output streams concurrently, then wait for process exit."""
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    async def drain(stream: Optional[anyio.abc.ByteReceiveStream], chunks: list[bytes]) -> None:
      if stream is None:
        return
      try:
        async for chunk in stream:
          chunks.append(chunk)
      except anyio.ClosedResourceError:
        if process.returncode is None:
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(drain, process.stdout, stdout_chunks)
      task_group.start_soon(drain, process.stderr, stderr_chunks)
    await process.wait()
    return b"".join(stdout_chunks), b"".join(stderr_chunks)

  async def _invoke(self, invocation: _Invocation, arguments: list[str], timeout: float) -> None:
    """Own one child and deliver its completed result to the requesting task."""
    try:
      with anyio.CancelScope() as scope:
        invocation.scope = scope
        if invocation.cancel_requested:
          scope.cancel()
        process = await anyio.open_process([self.executable, *arguments])
        # Shield process exit, including normal close and cancellation cleanup.
        try:
          with anyio.CancelScope() as process_scope:
            async with process:
              try:
                with anyio.fail_after(timeout):
                  stdout, stderr = await self._communicate(process)
              except BaseException:
                process_scope.shield = True
                if process.returncode is None:
                  try:
                    process.kill()
                  except ProcessLookupError:
                    pass
                raise
              finally:
                process_scope.shield = True
        except BaseException:
          with anyio.CancelScope(shield=True):
            await process.wait()
          raise
        returncode = process.returncode
        if returncode is None:
          raise RuntimeError("command completed without an exit code")
        invocation.output = returncode, stdout, stderr
    except BaseException as error:
      invocation.error = error
      if isinstance(error, anyio.get_cancelled_exc_class()):
        raise
    finally:
      invocation.done.set()

  async def run(self, arguments: Sequence[str], timeout: float) -> CommandLineResult:
    """Run the configured executable with arguments and collect its output.

    Args:
      arguments: Arguments passed to the configured executable.
      timeout: Maximum runtime in seconds.

    Returns:
      The process exit code and decoded output streams.

    Raises:
      RuntimeError: If the transport does not have an active lifespan.
      ValueError: If ``timeout`` is not positive.
      TimeoutError: If the process does not finish before ``timeout``.
    """
    if not self._is_setup:
      raise RuntimeError(
        f"Command-line I/O for '{self.human_readable_device_name}' is not set up; "
        "enter its active lifespan first"
      )
    if timeout <= 0:
      raise ValueError("timeout must be positive")

    command_arguments = list(arguments)
    async with self._run_lock:
      group = self._task_group
      if not self._is_setup or group is None:
        raise RuntimeError(
          f"Command-line I/O for '{self.human_readable_device_name}' is not set up; "
          "enter its active lifespan first"
        )
      invocation = _Invocation()
      group.start_soon(self._invoke, invocation, command_arguments, timeout)
      try:
        await invocation.done.wait()
      except BaseException:
        invocation.cancel()
        with anyio.CancelScope(shield=True):
          await invocation.done.wait()
        raise

    if invocation.error is not None:
      raise invocation.error
    if invocation.output is None:
      raise RuntimeError(
        f"Command-line I/O for '{self.human_readable_device_name}' stopped during a run"
      )
    returncode, stdout, stderr = invocation.output
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
    HasLifespan.__init__(self)
    self.cr = cr
    self.human_readable_device_name = human_readable_device_name
    self.executable = executable
    self._is_setup = False
    self._task_group = None
    self._run_lock = anyio.Lock()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Activate capture replay without resolving an executable."""
    self._is_setup = True
    stack.push_shielded_async_callback(self._teardown)

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
