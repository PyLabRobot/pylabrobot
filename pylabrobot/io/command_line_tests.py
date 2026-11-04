import shutil
import sys
from collections.abc import Callable
from typing import Any, Optional
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.io import command_line as command_line_module
from pylabrobot.io.command_line import (
  CommandLineCommand,
  CommandLineResult,
  CommandLineTransport,
  CommandLineValidator,
)
from pylabrobot.io.errors import ValidationError
from pylabrobot.testing.concurrency import AnyioTestBase


async def _wait_until(predicate: Callable[[], bool]) -> None:
  """Wait briefly for a concurrent test condition to become true."""
  with anyio.fail_after(2):
    while not predicate():
      await anyio.sleep(0)


class _NeverReapedProcess:
  """Model a killed child whose operating system wait never returns."""

  returncode: Optional[int] = None

  def __init__(self) -> None:
    self.killed = False
    self.close_started = False
    self.closed = False

  async def __aenter__(self) -> "_NeverReapedProcess":
    """Enter the process's resource scope."""
    return self

  async def __aexit__(self, *args: object) -> None:
    """Close the process's resources on scope exit."""
    await self.aclose()

  def kill(self) -> None:
    """Record the kill without making the child reapable."""
    self.killed = True

  async def wait(self) -> int:
    """Remain unreapable until the caller's finite bound expires."""
    await anyio.sleep_forever()
    raise AssertionError("unreachable")

  async def aclose(self) -> None:
    """Close resources after a checkpoint, then remain cancellably stalled."""
    self.close_started = True
    await anyio.sleep(0)
    self.closed = True
    await anyio.sleep_forever()


class _ReapBlockedProcess(_NeverReapedProcess):
  """Model an unreapable child that a test can eventually release."""

  stdin = None
  stdout = None
  stderr = None

  def __init__(self) -> None:
    super().__init__()
    self.wait_started = anyio.Event()
    self.close_entered = anyio.Event()
    self.release = anyio.Event()

  async def wait(self) -> int:
    """Block process reaping until the test releases it."""
    self.wait_started.set()
    await self.release.wait()
    return -9

  async def aclose(self) -> None:
    """Block process resource closure until the test releases it."""
    self.close_started = True
    self.close_entered.set()
    await self.release.wait()
    self.closed = True


class _ControlledStream:
  """Provide deterministic chunks, failure, or blocking for process tests."""

  def __init__(
    self,
    chunks: Optional[list[bytes]] = None,
    error: Optional[BaseException] = None,
    block: bool = False,
  ) -> None:
    self._chunks = list(chunks or [])
    self._error = error
    self._block = block
    self._finished = anyio.Event()
    self.receiving = anyio.Event()

  def __aiter__(self) -> "_ControlledStream":
    return self

  async def __anext__(self) -> bytes:
    self.receiving.set()
    if self._error is not None:
      error = self._error
      self._error = None
      raise error
    if self._chunks:
      return self._chunks.pop(0)
    if self._block:
      await self._finished.wait()
      if self._error is not None:
        error = self._error
        self._error = None
        raise error
    raise StopAsyncIteration

  def finish(self) -> None:
    """Make future receives report end-of-stream."""
    self._block = False
    self._finished.set()

  def close(self) -> None:
    """Wake a blocked receive with resource-closure semantics."""
    self._error = anyio.ClosedResourceError()
    self._finished.set()


class _ControlledProcess:
  """Expose process ownership effects without creating an operating-system child."""

  stdin = None

  def __init__(
    self,
    *,
    stdout: Optional[_ControlledStream] = None,
    stderr: Optional[_ControlledStream] = None,
    returncode: Optional[int] = None,
    wait_error: Optional[BaseException] = None,
    streams_finish_on_kill: bool = True,
  ) -> None:
    self.stdout = stdout or _ControlledStream()
    self.stderr = stderr or _ControlledStream()
    self.returncode = returncode
    self.wait_error = wait_error
    self.streams_finish_on_kill = streams_finish_on_kill
    self.kills = 0
    self.waits = 0
    self.closes = 0
    self._finished = anyio.Event()
    if returncode is not None:
      self._finished.set()

  async def __aenter__(self) -> "_ControlledProcess":
    """Enter the process's resource scope."""
    return self

  async def __aexit__(self, *args: object) -> None:
    """Close the process's resources on scope exit."""
    await self.aclose()

  def kill(self) -> None:
    """Terminate the controlled process."""
    self.kills += 1
    self.returncode = -9
    if self.streams_finish_on_kill:
      self.stdout.finish()
      self.stderr.finish()
    self._finished.set()

  async def wait(self) -> int:
    """Wait for termination, optionally failing once."""
    self.waits += 1
    if self.wait_error is not None:
      error = self.wait_error
      self.wait_error = None
      raise error
    await self._finished.wait()
    assert self.returncode is not None
    return self.returncode

  async def aclose(self) -> None:
    """Record deterministic closure of the process stream resources."""
    self.closes += 1
    self.stdout.close()
    self.stderr.close()


def _transport() -> CommandLineTransport:
  """Create a transport for the current Python interpreter."""
  return CommandLineTransport("Test interpreter", sys.executable)


class TestCommandLineTransport(AnyioTestBase):
  async def test_executable_is_resolved_only_during_lifespan_entry(self) -> None:
    transport = _transport()
    configured_executable = transport.executable

    assert isinstance(transport, HasLifespan)
    assert not transport.is_setup
    async with transport:
      assert transport.is_setup
      assert transport.executable == shutil.which(configured_executable)
    assert not transport.is_setup

  async def test_missing_executable_is_a_clean_entry_failure(self) -> None:
    transport = CommandLineTransport("Missing executable", "pylabrobot-command-that-does-not-exist")

    with pytest.raises(FileNotFoundError, match="pylabrobot-command-that-does-not-exist"):
      async with transport:
        raise AssertionError("missing executable entered its lifespan")

    assert not transport.is_active
    assert not transport.is_setup
    assert transport._task_group is None

  async def test_run_requires_an_active_lifespan(self) -> None:
    with pytest.raises(RuntimeError, match="active lifespan"):
      await _transport().run(["-c", "pass"], timeout=1)

  async def test_run_returns_and_captures_decoded_process_result(self) -> None:
    transport = _transport()
    commands: list[CommandLineCommand] = []
    script = (
      "import sys; sys.stdout.buffer.write(b'output\\n'); sys.stderr.buffer.write(b'warning\\n')"
    )

    with mock.patch.object(command_line_module.capturer, "record", commands.append):
      async with transport:
        result = await transport.run(["-c", script], timeout=2)

    assert result == CommandLineResult(0, "output\n", "warning\n")
    assert len(commands) == 1
    command = commands[0]
    assert command.device_id == shutil.which(sys.executable)
    assert command.arguments == ["-c", script]
    assert command.timeout == 2
    assert command.returncode == 0
    assert command.stdout == "output\n"
    assert command.stderr == "warning\n"

  async def test_stdout_and_stderr_are_drained_concurrently(self) -> None:
    byte_count = 256 * 1024
    script = (
      "import sys; "
      f"sys.stdout.buffer.write(b'o' * {byte_count}); "
      f"sys.stderr.buffer.write(b'e' * {byte_count})"
    )

    transport = _transport()
    async with transport:
      result = await transport.run(["-c", script], timeout=2)

    assert result.returncode == 0
    assert result.stdout == "o" * byte_count
    assert result.stderr == "e" * byte_count

  async def test_normal_completion_closes_process_resources(self) -> None:
    process = _ControlledProcess(
      stdout=_ControlledStream([b"output"]),
      returncode=0,
    )
    transport = _transport()

    with mock.patch.object(command_line_module.anyio, "open_process", return_value=process):
      async with transport:
        assert await transport.run(["-c", "pass"], timeout=1) == CommandLineResult(0, "output", "")

    assert process.closes == 1

  async def test_stream_failure_kills_reaps_and_closes_process(self) -> None:
    process = _ControlledProcess(
      stdout=_ControlledStream(error=RuntimeError("stream failed")),
    )
    transport = _transport()

    with mock.patch.object(command_line_module.anyio, "open_process", return_value=process):
      async with transport:
        with pytest.raises(BaseException) as failure:
          await transport.run(["-c", "pass"], timeout=1)

    assert "stream failed" in repr(failure.value)
    assert process.kills == 1
    assert process.waits == 1
    assert process.closes == 1

  async def test_wait_failure_kills_reaps_and_closes_process(self) -> None:
    process = _ControlledProcess(wait_error=RuntimeError("wait failed"))
    transport = _transport()

    with mock.patch.object(command_line_module.anyio, "open_process", return_value=process):
      async with transport:
        with pytest.raises(RuntimeError, match="wait failed"):
          await transport.run(["-c", "pass"], timeout=1)

    assert process.kills == 1
    assert process.waits == 2
    assert process.closes == 1

  async def test_runs_are_serialized(self) -> None:
    transport = _transport()
    processes: list[anyio.abc.Process] = []
    second_called = anyio.Event()
    results: list[CommandLineResult] = []
    open_process = anyio.open_process

    async def tracked_open_process(*args: Any, **kwargs: Any) -> anyio.abc.Process:
      process = await open_process(*args, **kwargs)
      processes.append(process)
      return process

    async def run(second: bool = False) -> None:
      if second:
        second_called.set()
      results.append(await transport.run(["-c", "import sys; sys.stdin.buffer.read(1)"], timeout=2))

    with mock.patch.object(command_line_module.anyio, "open_process", tracked_open_process):
      async with transport, anyio.create_task_group() as task_group:
        task_group.start_soon(run)
        await _wait_until(lambda: len(processes) == 1)
        task_group.start_soon(run, True)
        await second_called.wait()
        await anyio.sleep(0)
        assert len(processes) == 1

        assert processes[0].stdin is not None
        await processes[0].stdin.send(b"1")
        await _wait_until(lambda: len(processes) == 2)
        assert processes[1].stdin is not None
        await processes[1].stdin.send(b"2")

    assert [result.returncode for result in results] == [0, 0]

  async def test_timeout_kills_and_reaps_the_child(self) -> None:
    transport = _transport()
    processes: list[anyio.abc.Process] = []
    open_process = anyio.open_process

    async def tracked_open_process(*args: Any, **kwargs: Any) -> anyio.abc.Process:
      process = await open_process(*args, **kwargs)
      processes.append(process)
      return process

    with mock.patch.object(command_line_module.anyio, "open_process", tracked_open_process):
      async with transport:
        with pytest.raises(TimeoutError):
          await transport.run(
            ["-c", "import sys; sys.stdin.buffer.read()"],
            timeout=0.05,
          )

    assert len(processes) == 1
    assert processes[0].returncode is not None

  async def test_timeout_closes_process_resources(self) -> None:
    process = _ControlledProcess(
      stdout=_ControlledStream(block=True),
      stderr=_ControlledStream(block=True),
    )
    transport = _transport()

    with mock.patch.object(command_line_module.anyio, "open_process", return_value=process):
      async with transport:
        with pytest.raises(TimeoutError):
          await transport.run(["-c", "pass"], timeout=0.01)

    assert process.kills == 1
    assert process.waits == 1
    assert process.closes == 1

  async def test_cancellation_kills_and_reaps_the_child(self) -> None:
    transport = _transport()
    processes: list[anyio.abc.Process] = []
    scopes: list[anyio.CancelScope] = []
    finished = anyio.Event()
    open_process = anyio.open_process

    async def tracked_open_process(*args: Any, **kwargs: Any) -> anyio.abc.Process:
      process = await open_process(*args, **kwargs)
      processes.append(process)
      return process

    async def run_until_cancelled() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await transport.run(["-c", "import sys; sys.stdin.buffer.read()"], timeout=10)
        finally:
          finished.set()

    with mock.patch.object(command_line_module.anyio, "open_process", tracked_open_process):
      async with transport, anyio.create_task_group() as task_group:
        task_group.start_soon(run_until_cancelled)
        await _wait_until(lambda: len(processes) == 1)
        scopes[0].cancel()
        await finished.wait()
        assert processes[0].returncode is not None

  async def test_cancellation_closes_process_resources(self) -> None:
    transport = _transport()
    process = _ControlledProcess(
      stdout=_ControlledStream(block=True),
      stderr=_ControlledStream(block=True),
    )
    scopes: list[anyio.CancelScope] = []
    finished = anyio.Event()

    async def run_until_cancelled() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await transport.run(["-c", "pass"], timeout=1)
        finally:
          finished.set()

    with mock.patch.object(command_line_module.anyio, "open_process", return_value=process):
      async with transport, anyio.create_task_group() as task_group:
        task_group.start_soon(run_until_cancelled)
        await process.stdout.receiving.wait()
        scopes[0].cancel()
        await finished.wait()

    assert process.kills == 1
    assert process.waits == 1
    assert process.closes == 1

  async def test_lifespan_exit_kills_and_reaps_an_active_child(self) -> None:
    transport = _transport()
    processes: list[anyio.abc.Process] = []
    finished = anyio.Event()
    open_process = anyio.open_process

    async def tracked_open_process(*args: Any, **kwargs: Any) -> anyio.abc.Process:
      process = await open_process(*args, **kwargs)
      processes.append(process)
      return process

    async def run() -> None:
      try:
        await transport.run(["-c", "import sys; sys.stdin.buffer.read()"], timeout=10)
      finally:
        finished.set()

    with mock.patch.object(command_line_module.anyio, "open_process", tracked_open_process):
      async with anyio.create_task_group() as task_group:
        async with transport:
          task_group.start_soon(run)
          await _wait_until(lambda: len(processes) == 1)

        await finished.wait()
        assert processes[0].returncode is not None

  async def test_lifespan_exit_closes_process_resources(self) -> None:
    transport = _transport()
    process = _ControlledProcess(
      stdout=_ControlledStream(block=True),
      stderr=_ControlledStream(block=True),
      streams_finish_on_kill=False,
    )
    finished = anyio.Event()

    async def run() -> None:
      try:
        await transport.run(["-c", "pass"], timeout=1)
      finally:
        finished.set()

    with mock.patch.object(command_line_module.anyio, "open_process", return_value=process):
      async with anyio.create_task_group() as task_group:
        async with transport:
          task_group.start_soon(run)
          await process.stdout.receiving.wait()
        await finished.wait()

    assert process.kills == 1
    assert process.waits == 1
    assert process.closes == 1

  async def test_lifespan_exit_joins_process_owner_through_stalled_cleanup(self) -> None:
    transport = _transport()
    process = _ReapBlockedProcess()
    entered = anyio.Event()
    leave = anyio.Event()
    exited = anyio.Event()
    run_finished = anyio.Event()

    async def use_transport() -> None:
      async with transport:
        entered.set()
        await leave.wait()
      exited.set()

    async def run() -> None:
      try:
        await transport.run(["-c", "pass"], timeout=10)
      except BaseException:
        pass
      finally:
        run_finished.set()

    with mock.patch.object(command_line_module.anyio, "open_process", return_value=process):
      async with anyio.create_task_group() as group:
        group.start_soon(use_transport)
        await entered.wait()
        group.start_soon(run)
        await process.wait_started.wait()
        leave.set()
        try:
          with anyio.fail_after(1):
            await process.close_entered.wait()
          with anyio.move_on_after(0.05):
            await exited.wait()
          assert not exited.is_set()
        finally:
          process.release.set()
        await exited.wait()
        await run_finished.wait()

    assert process.killed
    assert process.closed

  async def test_a_queued_run_cannot_start_after_lifespan_exit(self) -> None:
    transport = _transport()
    processes: list[anyio.abc.Process] = []
    failures: list[BaseException] = []
    second_called = anyio.Event()
    open_process = anyio.open_process

    async def tracked_open_process(*args: Any, **kwargs: Any) -> anyio.abc.Process:
      process = await open_process(*args, **kwargs)
      processes.append(process)
      return process

    async def run(second: bool = False) -> None:
      if second:
        second_called.set()
      try:
        await transport.run(["-c", "import sys; sys.stdin.buffer.read()"], timeout=10)
      except BaseException as error:
        failures.append(error)

    with mock.patch.object(command_line_module.anyio, "open_process", tracked_open_process):
      async with anyio.create_task_group() as task_group:
        async with transport:
          task_group.start_soon(run)
          await _wait_until(lambda: len(processes) == 1)
          task_group.start_soon(run, True)
          await second_called.wait()
          await anyio.sleep(0)

        await _wait_until(lambda: len(failures) == 2)

    assert len(processes) == 1
    assert len(failures) == 2
    assert any("active lifespan" in str(error) for error in failures)

  async def test_lifespan_exit_waits_for_an_inflight_process_launch(self) -> None:
    transport = _transport()
    launch_started = anyio.Event()
    release_launch = anyio.Event()
    run_finished = anyio.Event()
    processes: list[anyio.abc.Process] = []
    started_after_exit: list[bool] = []
    failures: list[BaseException] = []
    exited = False
    open_process = anyio.open_process

    async def delayed_open_process(*args: Any, **kwargs: Any) -> anyio.abc.Process:
      launch_started.set()
      await release_launch.wait()
      process = await open_process(*args, **kwargs)
      processes.append(process)
      started_after_exit.append(exited)
      return process

    async def run() -> None:
      try:
        await transport.run(["-c", "pass"], timeout=2)
      except BaseException as error:
        failures.append(error)
      finally:
        run_finished.set()

    async def release_when_exit_starts() -> None:
      await _wait_until(lambda: not transport.is_setup)
      release_launch.set()

    with mock.patch.object(command_line_module.anyio, "open_process", delayed_open_process):
      async with anyio.create_task_group() as task_group:
        async with transport:
          task_group.start_soon(run)
          await launch_started.wait()
          task_group.start_soon(release_when_exit_starts)
        exited = True
        await run_finished.wait()

    assert started_after_exit == []
    assert processes == []
    assert len(failures) == 1
    assert isinstance(failures[0], anyio.get_cancelled_exc_class())

  async def test_lifespan_exit_joins_a_stalled_process_launch(self) -> None:
    transport = _transport()
    process = _ControlledProcess()
    launch_started = anyio.Event()
    release_launch = anyio.Event()
    exit_finished = anyio.Event()
    exited_before_release: list[bool] = []
    process_started_after_exit: list[bool] = []
    failures: list[BaseException] = []

    async def stalled_open_process(*args: object, **kwargs: object) -> _ControlledProcess:
      launch_started.set()
      with anyio.CancelScope(shield=True):
        await release_launch.wait()
      process_started_after_exit.append(exit_finished.is_set())
      return process

    async def run() -> None:
      try:
        await transport.run(["-c", "pass"], timeout=1)
      except BaseException as error:
        failures.append(error)

    async def release_after_observing_exit_wait() -> None:
      with anyio.move_on_after(0.05):
        await exit_finished.wait()
      exited_before_release.append(exit_finished.is_set())
      release_launch.set()

    with mock.patch.object(command_line_module.anyio, "open_process", stalled_open_process):
      async with anyio.create_task_group() as task_group:
        async with transport:
          task_group.start_soon(run)
          await launch_started.wait()
          task_group.start_soon(release_after_observing_exit_wait)
        exit_finished.set()

    assert exited_before_release == [False]
    assert process_started_after_exit == [False]
    assert process.kills == 1
    assert process.closes == 1
    assert len(failures) == 1
    assert isinstance(failures[0], anyio.get_cancelled_exc_class())

  async def test_rejects_non_positive_timeout(self) -> None:
    transport = _transport()
    async with transport:
      with pytest.raises(ValueError, match="positive"):
        await transport.run([], timeout=0)

  def test_rejects_empty_identity(self) -> None:
    with pytest.raises(ValueError, match="human_readable_device_name"):
      CommandLineTransport("", sys.executable)
    with pytest.raises(ValueError, match="executable"):
      CommandLineTransport("Test interpreter", "")

  def test_serializes_configuration(self) -> None:
    assert _transport().serialize() == {
      "human_readable_device_name": "Test interpreter",
      "executable": sys.executable,
    }


class TestCommandLineValidator(AnyioTestBase):
  @staticmethod
  def _capture_reader(arguments: list[str]) -> mock.MagicMock:
    """Create a reader containing one captured command-line result."""
    capture_reader = mock.MagicMock()
    capture_reader.next_command.return_value = {
      "module": "command_line",
      "device_id": sys.executable,
      "action": "run",
      "arguments": arguments,
      "timeout": 2.0,
      "returncode": 0,
      "stdout": "output",
      "stderr": "",
    }
    return capture_reader

  async def test_validator_replays_captured_result_without_resolving_executable(self) -> None:
    validator = CommandLineValidator(
      cr=self._capture_reader(["-c", "pass"]),
      human_readable_device_name="Test interpreter",
      executable=sys.executable,
    )

    with mock.patch.object(command_line_module.shutil, "which") as resolve:
      async with validator:
        result = await validator.run(["-c", "pass"], timeout=2)

    resolve.assert_not_called()
    assert result == CommandLineResult(0, "output", "")
    assert not validator.is_setup

  async def test_validator_rejects_argument_mismatch(self) -> None:
    validator = CommandLineValidator(
      cr=self._capture_reader(["--expected"]),
      human_readable_device_name="Test interpreter",
      executable=sys.executable,
    )

    async with validator:
      with pytest.raises(ValidationError, match="arguments differ"):
        await validator.run(["--actual"], timeout=2)
