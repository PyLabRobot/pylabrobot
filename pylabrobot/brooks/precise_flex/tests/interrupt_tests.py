from typing import Any, Awaitable, Callable, cast
from unittest.mock import AsyncMock, MagicMock, call

import anyio
import pytest

from pylabrobot.brooks.precise_flex.data_ids import PowerState
from pylabrobot.brooks.precise_flex.driver.configuration import PreciseFlexAxis
from pylabrobot.brooks.precise_flex.driver.errors import (
  OperationInterrupted,
  PreciseFlexError,
  is_collision,
)
from pylabrobot.brooks.precise_flex.driver.master import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.interrupt import halt_and_resync, halt_on_interrupt
from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.testing.concurrency import AnyioTestBase


def mocked(method: object) -> AsyncMock:
  """Narrow a replaced asynchronous method to its test-double type."""
  return cast(AsyncMock, method)


def _make_arm() -> PreciseFlexDriver:
  """Return an arm whose socket records writes and drains immediately."""
  arm = PreciseFlexDriver(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=500.0,
  )
  arm.io = MagicMock()
  arm.io.write = AsyncMock()
  arm.io.readline = AsyncMock(side_effect=TimeoutError())
  return arm


class _CancellingIO:
  """Software transport that requests cancellation after a selected reply is consumed."""

  def __init__(self, cancel_command: str, *, has_rail: bool = False) -> None:
    self.cancel_command = cancel_command
    self.commands: list[str] = []
    self.raw_writes: list[bytes] = []
    self.consumed: list[str] = []
    self.scope: anyio.CancelScope | None = None
    self._current_command = ""
    self._cancelled = False
    self._has_rail = has_rail
    self._host = "localhost"
    self._port = 10100

  async def write(self, data: bytes, timeout: float | None = None) -> None:
    del timeout
    self.raw_writes.append(data)
    self._current_command = data.decode("ascii").strip()
    self.commands.append(self._current_command)

  async def readline(self, timeout: float | None = None) -> bytes:
    if timeout is not None:
      raise TimeoutError()
    self.consumed.append(self._current_command)
    if self._current_command == self.cancel_command and not self._cancelled:
      self._cancelled = True
      assert self.scope is not None
      self.scope.cancel()
    if self._current_command == "wherej":
      if self._has_rail:
        return b"0 100.0 0.0 180.0 180.0 70.0 0.0\n"
      return b"0 100.0 0.0 180.0 180.0 70.0\n"
    return b"0 1\n"


def _make_cancelling_arm(
  cancel_command: str, *, has_rail: bool = False, stub_eom: bool = True
) -> tuple[PreciseFlexDriver, _CancellingIO]:
  io = _CancellingIO(cancel_command, has_rail=has_rail)
  arm = PreciseFlexDriver(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=500.0,
    has_rail=has_rail,
  )
  arm.io = cast(Any, io)
  arm.gripper.configuration = MagicMock(soft_limit_range=(0.0, 10_000.0))
  if stub_eom:
    arm.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
  return arm, io


async def _run_until_confirmed_cancellation(
  io: _CancellingIO, operation: Callable[[], Awaitable[None]]
) -> None:
  cancellation_seen: list[bool] = []
  with anyio.CancelScope() as scope:
    io.scope = scope
    try:
      await operation()
    except anyio.get_cancelled_exc_class():
      cancellation_seen.append(True)
      raise
  assert cancellation_seen == [True]


class TestWaitForEom(AnyioTestBase):
  """The non-blocking motion wait polls until the arm stops moving."""

  async def test_returns_when_motion_stops(self) -> None:
    """Returns once three samples in a row agree."""
    wherej = iter(["0 0 0 0 0", "5 5 5 5 5", "9.9 9.9 9.9 9.9 9.9"] + ["10 10 10 10 10"] * 3)
    arm = _make_arm()
    arm.send_command = AsyncMock(  # type: ignore[method-assign]
      side_effect=lambda command, **kwargs: next(wherej)
    )

    await arm.arm._wait_for_eom(poll_interval=0)

  async def test_a_move_creeping_off_does_not_count_as_stopped(self) -> None:
    """Right after moveJ the arm creeps under the settle threshold (as logged), then moves."""
    # The arm's first four reads after a logged moveJ, then the three it settled on.
    replies = [
      "301.022 33.703 242.82 141.894 74.8",
      "301.022 33.694 242.807 141.909 74.8",
      "301.022 33.667 242.74 141.942 74.8",
      "301.038 33.586 242.595 142.024 74.8",
      "301.123 0.002 179.998 179.996 74.777",
      "301.124 0.002 179.999 180 74.777",
      "301.125 0.001 179.999 180.002 74.777",
    ]
    replies_iter = iter(replies)
    arm = _make_arm()
    arm.send_command = AsyncMock(  # type: ignore[method-assign]
      side_effect=lambda command, **kwargs: next(replies_iter)
    )
    await arm.arm._wait_for_eom(poll_interval=0)
    assert mocked(arm.send_command).await_count == len(replies)

  async def test_returns_immediately_when_already_stationary(self) -> None:
    """An idle arm (e.g. halted short of its last target) returns at once, never hangs to reach it."""
    arm = _make_arm()
    idle = "269.908 81.507 218.952 113.977 70.685"  # as the arm answered, poll after poll
    arm.send_command = AsyncMock(return_value=idle)  # type: ignore[method-assign]

    await arm.arm._wait_for_eom(poll_interval=0)

  async def test_cancelled_wait_halts_resyncs_and_reraises(self) -> None:
    arm = _make_arm()
    first_sample_read = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    cancelled: list[bool] = []

    async def wait_for_sample(command: str, *, read_deadline: float | None = None) -> str:
      del command, read_deadline
      if not first_sample_read.is_set():
        first_sample_read.set()
        return "0 0 0 0 0"
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    arm.send_command = wait_for_sample  # type: ignore[method-assign]

    async def wait_for_eom() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await arm.arm._wait_for_eom(poll_interval=0)
        except anyio.get_cancelled_exc_class():
          cancelled.append(True)
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(wait_for_eom)
      await first_sample_read.wait()
      await anyio.sleep(0)
      scopes[0].cancel()

    assert cancelled == [True]
    assert mocked(arm.io.write).await_args_list == [call(b"\nhalt\n")]
    mocked(arm.io.readline).assert_awaited_once_with(timeout=0.5)

  async def test_timeout_when_never_settles(self) -> None:
    samples = iter(range(1000))
    arm = _make_arm()
    arm.send_command = AsyncMock(  # type: ignore[method-assign]
      side_effect=lambda command, **kwargs: f"{next(samples)} 0 0 0 0"
    )

    with pytest.raises(TimeoutError):
      await arm.arm._wait_for_eom(poll_interval=0, timeout=0)

  @pytest.mark.parametrize("stalled_sample", [1, 2])
  async def test_zero_timeout_bounds_stalled_sample(self, stalled_sample: int) -> None:
    arm = _make_arm()
    samples = 0

    async def sample(command: str, *, read_deadline: float | None = None) -> str:
      nonlocal samples
      del read_deadline
      assert command == "wherej"
      samples += 1
      if samples == stalled_sample:
        await anyio.sleep_forever()
        raise AssertionError("unreachable")
      return "7 0 0 0 0"

    arm.send_command = sample  # type: ignore[method-assign]
    with anyio.fail_after(2):
      with pytest.raises(
        TimeoutError, match=r"motion did not settle within 0s \(current="
      ) as error:
        await arm.arm._wait_for_eom(poll_interval=0, timeout=0)

    assert samples == stalled_sample
    expected_current = "[]" if stalled_sample == 1 else "[7.0, 0.0, 0.0, 0.0, 0.0]"
    assert str(error.value) == f"motion did not settle within 0s (current={expected_current})"

  async def test_zero_timeout_returns_after_stationary_samples(self) -> None:
    arm = _make_arm()
    samples = 0

    async def sample(command: str, *, read_deadline: float | None = None) -> str:
      nonlocal samples
      del read_deadline
      assert command == "wherej"
      samples += 1
      return "7 0 0 0 0"

    arm.send_command = sample  # type: ignore[method-assign]
    await arm.arm._wait_for_eom(poll_interval=0.01, timeout=0)
    assert samples == 3

  @pytest.mark.parametrize("stalled_sample", [1, 2])
  async def test_zero_timeout_shielded_reply_cannot_contaminate_next_command(
    self, stalled_sample: int
  ) -> None:
    arm = _make_arm()

    class PendingReplyIO:
      def __init__(self) -> None:
        self._host = "localhost"
        self._port = 10100
        self.lost = False
        self.writes: list[bytes] = []
        self.reads = 0
        self.late_reply = b"0 99 0 0 0 0\n"

      def _mark_connection_lost(self) -> None:
        self.lost = True

      async def write(self, data: bytes, timeout: float | None = None) -> None:
        del timeout
        if self.lost:
          raise ConnectionError("connection lost")
        self.writes.append(data)
        await anyio.lowlevel.checkpoint()

      async def readline(self) -> bytes:
        self.reads += 1
        if self.reads == stalled_sample:
          await anyio.sleep(1.25)  # A late reply must not become the next command's reply.
        return b"0 7 0 0 0 0\n" if self.reads == 1 else self.late_reply

    io = PendingReplyIO()
    arm.io = cast(Any, io)

    with anyio.fail_after(2):
      with pytest.raises(TimeoutError, match="motion did not settle within 0s") as error:
        await arm.arm._wait_for_eom(poll_interval=0, timeout=0)

    expected_current = "[]" if stalled_sample == 1 else "[7.0, 0.0, 0.0, 0.0, 0.0]"
    assert f"current={expected_current}" in str(error.value)
    assert io.lost
    with pytest.raises(ConnectionError, match="connection lost"):
      await arm.send_command("state")
    assert io.writes == [b"wherej\n"] * stalled_sample

  @pytest.mark.parametrize("stalled_sample", [1, 2])
  @pytest.mark.parametrize(("timeout", "delay"), [(0, 1.25), (0.2, 0.3)])
  async def test_shielded_write_timeout_rejects_late_reply(
    self, stalled_sample: int, timeout: float, delay: float
  ) -> None:
    arm = _make_arm()

    class SlowWriteIO:
      def __init__(self) -> None:
        self._host = "localhost"
        self._port = 10100
        self.lost = False
        self.writes: list[bytes] = []
        self.reads = 0

      def _mark_connection_lost(self) -> None:
        self.lost = True

      async def write(self, data: bytes, timeout: float | None = None) -> None:
        if self.lost:
          raise ConnectionError("connection lost")
        self.writes.append(data)  # May have committed before the write await finishes.
        if len(self.writes) == stalled_sample:
          with anyio.CancelScope(shield=True):
            with anyio.fail_after(timeout):
              await anyio.sleep(delay)
        else:
          await anyio.lowlevel.checkpoint()

      async def readline(self) -> bytes:
        self.reads += 1
        return b"0 7 0 0 0 0\n" if self.reads == 1 else b"0 99 0 0 0 0\n"

    io = SlowWriteIO()
    arm.io = cast(Any, io)
    with anyio.fail_after(2):
      with pytest.raises(TimeoutError, match="motion did not settle within") as error:
        await arm.arm._wait_for_eom(poll_interval=0, timeout=timeout)

    expected_current = "[]" if stalled_sample == 1 else "[7.0, 0.0, 0.0, 0.0, 0.0]"
    assert f"current={expected_current}" in str(error.value)
    assert io.lost
    assert io.reads == stalled_sample - 1
    with pytest.raises(ConnectionError, match="connection lost"):
      await arm.send_command("state")
    assert io.writes == [b"wherej\n"] * stalled_sample

  async def test_poll_exchange_uses_one_budget_for_write_and_read(self) -> None:
    arm = _make_arm()

    class SlowExchangeIO:
      def __init__(self) -> None:
        self._host = "localhost"
        self._port = 10100
        self.lost = False
        self.writes: list[bytes] = []

      def _mark_connection_lost(self) -> None:
        self.lost = True

      async def write(self, data: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(data)
        await anyio.sleep(0.08)

      async def readline(self) -> bytes:
        await anyio.sleep(0.15)
        return b"0 7 0 0 0 0\n"

    io = SlowExchangeIO()
    arm.io = cast(Any, io)
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="motion did not settle within 0s"):
        await arm.arm._wait_for_eom(poll_interval=0, timeout=0.2)

    assert io.lost
    assert io.writes == [b"wherej\n"]

  async def test_poll_budget_includes_command_lock_wait(self) -> None:
    arm = _make_arm()

    class ContendedIO:
      def __init__(self) -> None:
        self._host = "localhost"
        self._port = 10100
        self.lost = False
        self.writes: list[bytes] = []

      def _mark_connection_lost(self) -> None:
        self.lost = True

      async def write(self, data: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(data)
        await anyio.lowlevel.checkpoint()

      async def readline(self) -> bytes:
        await anyio.sleep(0.14)
        return b"0 7 0 0 0 0\n"

    io = ContendedIO()
    arm.io = cast(Any, io)

    async def poll() -> None:
      with pytest.raises(TimeoutError, match="motion did not settle within"):
        await arm.arm._wait_for_eom(poll_interval=0, timeout=0.2)

    with anyio.fail_after(1):
      async with anyio.create_task_group() as group:
        async with arm._command_lock:
          group.start_soon(poll)
          await anyio.sleep(0.1)

    assert io.writes == [b"wherej\n"]
    assert io.lost  # The committed read timed out against the original polling deadline.

  async def test_write_lock_timeout_before_dispatch_keeps_socket_usable(self) -> None:
    arm = PreciseFlexDriver(
      host="localhost",
      gripper_length=162.0,
      gripper_z_offset=0.0,
      closed_gripper_position=500.0,
    )

    class Stream:
      def __init__(self) -> None:
        self.sent: list[bytes] = []

      async def send(self, data: bytes) -> None:
        self.sent.append(data)
        await anyio.lowlevel.checkpoint()

      async def receive(self, max_bytes: int) -> bytes:
        del max_bytes
        return b"0 ready\n"

    stream = Stream()
    arm.io._stream = cast(Any, stream)
    finished = anyio.Event()

    async def poll() -> None:
      try:
        with pytest.raises(TimeoutError, match="motion did not settle within"):
          await arm.arm._wait_for_eom(poll_interval=0, timeout=0.05)
      finally:
        finished.set()

    with anyio.fail_after(1):
      async with anyio.create_task_group() as group:
        async with arm.io._write_lock:
          group.start_soon(poll)
          await finished.wait()
          assert stream.sent == []

      assert await arm.send_command("state") == "ready"

    assert stream.sent == [b"state\n"]

  async def test_early_transport_timeout_is_not_motion_timeout(self) -> None:
    arm = _make_arm()
    transport_error = TimeoutError("transport read timed out")

    class EarlyTimeoutIO:
      def __init__(self) -> None:
        self._host = "localhost"
        self._port = 10100
        self.lost = False

      def _mark_connection_lost(self) -> None:
        self.lost = True

      async def write(self, data: bytes, timeout: float | None = None) -> None:
        del data, timeout
        await anyio.lowlevel.checkpoint()

      async def readline(self) -> bytes:
        raise transport_error

    io = EarlyTimeoutIO()
    arm.io = cast(Any, io)
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError) as error:
        await arm.arm._wait_for_eom(timeout=0.2)

    assert error.value is transport_error
    assert io.lost

  async def test_stalled_first_sample_times_out(self) -> None:
    arm = _make_arm()

    async def stalled_sample(command: str, *, read_deadline: float | None = None) -> str:
      del command, read_deadline
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    arm.send_command = stalled_sample  # type: ignore[method-assign]
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="motion did not settle within 0s"):
        await arm.arm._wait_for_eom(timeout=0.01)


class TestInterruptHelpers(AnyioTestBase):
  """The reusable interruption guard primitives."""

  async def test_halt_and_resync_flushes_sends_stop_then_drains(self) -> None:
    io = MagicMock()
    io.write = AsyncMock()
    io.readline = AsyncMock(side_effect=[b"0\r\n", TimeoutError()])

    await halt_and_resync(io, b"halt")

    io.write.assert_awaited_once_with(b"\nhalt\n")
    io.stop.assert_not_called()

  async def test_halt_and_resync_drain_only_when_no_stop(self) -> None:
    io = MagicMock()
    io.write = AsyncMock()
    io.readline = AsyncMock(side_effect=TimeoutError())

    await halt_and_resync(io)

    io.write.assert_not_called()

  async def test_repeated_cancellation_does_not_abandon_stop_action(self) -> None:
    stop_started = anyio.Event()
    release_stop = anyio.Event()
    stop_finished: list[bool] = []
    cancellation_seen: list[bool] = []
    scopes: list[anyio.CancelScope] = []
    scope_ready = anyio.Event()

    async def stop() -> None:
      stop_started.set()
      await release_stop.wait()
      stop_finished.append(True)

    async def operation() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        scope_ready.set()
        try:
          async with halt_on_interrupt(stop):
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
          cancellation_seen.append(True)
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(operation)
      await scope_ready.wait()
      scopes[0].cancel()
      await stop_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert stop_finished == []
      assert cancellation_seen == []
      release_stop.set()

    assert stop_finished == [True]
    assert cancellation_seen == [True]

  async def test_keyboard_interrupt_becomes_operation_interrupted(self) -> None:
    stop_completed: list[bool] = []

    async def stop() -> None:
      await anyio.lowlevel.checkpoint()
      stop_completed.append(True)

    with pytest.raises(OperationInterrupted) as error_info:
      async with halt_on_interrupt(stop):
        raise KeyboardInterrupt()

    assert stop_completed == [True]
    assert isinstance(error_info.value.__cause__, KeyboardInterrupt)

  async def test_estop_error_does_not_invoke_software_halt(self) -> None:
    stop = AsyncMock()
    error = PreciseFlexError(-1028, "hard E-stop")

    with pytest.raises(PreciseFlexError) as error_info:
      async with halt_on_interrupt(stop):
        raise error

    assert error_info.value is error
    stop.assert_not_awaited()

  async def test_halt_and_resync_stops_after_max_lines(self) -> None:
    io = MagicMock()
    io.write = AsyncMock()
    io.readline = AsyncMock(return_value=b"pending\n")

    await halt_and_resync(io, b"halt")

    io.write.assert_awaited_once_with(b"\nhalt\n")
    assert io.readline.await_count == 50
    io.readline.assert_awaited_with(timeout=0.5)


class TestMultiStepOperationCancellation(AnyioTestBase):
  async def test_cancelled_partial_freedrive_exits_freedrive(self) -> None:
    arm, io = _make_cancelling_arm("freemode 1")
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await _run_until_confirmed_cancellation(io, lambda: arm.arm.start_freedrive_mode([1, 2]))

    assert io.commands == ["freemode 1", "freemode -1"]
    assert io.consumed == ["freemode 1", "freemode -1"]
    assert all(not event.name.startswith("precise_flex.stop_freedrive") for event in events)

  @pytest.mark.parametrize(
    ("cancel_command", "expected_commands"),
    [
      ("freemode 1", ["freemode 1", "freemode -1"]),
      ("freemode 2", ["freemode 1", "freemode 2", "freemode -1"]),
      ("freemode 3", ["freemode 1", "freemode 2", "freemode 3", "freemode -1"]),
    ],
  )
  async def test_cancelled_freedrive_reconciles_each_command_before_rollback(
    self, cancel_command: str, expected_commands: list[str]
  ) -> None:
    arm, io = _make_cancelling_arm(cancel_command)

    await _run_until_confirmed_cancellation(io, lambda: arm.arm.start_freedrive_mode([1, 2, 3]))

    assert io.commands == expected_commands
    assert io.consumed == expected_commands

  async def test_cancelled_stop_reconciles_exit_before_propagating(self) -> None:
    arm, io = _make_cancelling_arm("freemode -1")
    await arm.arm.start_freedrive_mode([1])

    await _run_until_confirmed_cancellation(io, arm.arm.stop_freedrive_mode)
    await arm.arm.start_freedrive_mode([2])

    assert io.commands == ["freemode 1", "freemode -1", "freemode 2"]
    assert io.consumed == io.commands

  async def test_cancelled_freedrive_while_waiting_for_command_lock_writes_nothing(self) -> None:
    arm, io = _make_cancelling_arm("never")
    active_read_started = anyio.Event()
    release_active_reply = anyio.Event()
    scope_ready = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def readline(timeout: float | None = None) -> bytes:
      del timeout
      if io._current_command == "active":
        active_read_started.set()
        await release_active_reply.wait()
      io.consumed.append(io._current_command)
      return b"0 1\n"

    io.readline = readline  # type: ignore[method-assign]

    async def start_freedrive() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        scope_ready.set()
        await arm.arm.start_freedrive_mode([1])

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(arm.send_command, "active")
      await active_read_started.wait()
      task_group.start_soon(start_freedrive)
      await scope_ready.wait()
      await anyio.sleep(0)
      scopes[0].cancel()
      await anyio.sleep(0)
      assert io.commands == ["active"]
      release_active_reply.set()

    assert io.commands == ["active"]

  async def test_cancelled_gripper_command_consumes_terminal_reply(self) -> None:
    arm, io = _make_cancelling_arm("gripper 1")

    await _run_until_confirmed_cancellation(io, lambda: arm.gripper.move_to_jaw_position(80.0))

    assert io.commands == ["wherej", "GripOpenPos 520.0", "gripper 1"]
    assert io.consumed == io.commands

  async def test_cancelled_move_j_after_commit_halts_without_post_cancel_wherej(self) -> None:
    move_command = "moveJ 1 100.0 10.0 180.0 180.0 70.0"
    arm, io = _make_cancelling_arm(move_command)

    await _run_until_confirmed_cancellation(
      io, lambda: arm.arm.move_to_joint_state({PreciseFlexAxis.SHOULDER: 10.0})
    )

    assert io.commands == ["wherej", move_command, "halt"]
    assert io.consumed == ["wherej", move_command]
    assert io.raw_writes[-1] == b"\nhalt\n"

  async def test_cancelled_move_j_during_eom_wait_halts_once(self) -> None:
    arm, io = _make_cancelling_arm("never", stub_eom=False)
    post_move_reads = 0
    polling_started = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def readline(timeout: float | None = None) -> bytes:
      nonlocal post_move_reads
      if timeout is not None:
        raise TimeoutError()
      io.consumed.append(io._current_command)
      if io._current_command == "wherej":
        if any(cmd.startswith("moveJ") for cmd in io.commands):
          post_move_reads += 1
          polling_started.set()
          return f"0 100.0 {float(post_move_reads)} 180.0 180.0 70.0\n".encode("ascii")
        return b"0 100.0 0.0 180.0 180.0 70.0\n"
      return b"0 1\n"

    io.readline = readline  # type: ignore[method-assign]

    async def move() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await arm.arm.move_to_joint_state({PreciseFlexAxis.SHOULDER: 10.0})

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(move)
      await polling_started.wait()
      scopes[0].cancel()

    assert io.raw_writes.count(b"\nhalt\n") == 1
    assert io.commands[-1] == "halt"

  async def test_cancelled_rail_command_consumes_terminal_reply(self) -> None:
    arm, io = _make_cancelling_arm("MoveRail 1 1", has_rail=True)
    rail = arm.rail
    assert rail is not None

    await _run_until_confirmed_cancellation(io, lambda: rail.move_rail(123.0))

    assert io.commands == ["Rail 1 123.0", "MoveRail 1 1"]
    assert io.consumed == io.commands

  async def test_cancelled_pick_consumes_terminal_reply_without_release(self) -> None:
    arm, io = _make_cancelling_arm("pickplate 1 0 0")
    position = {
      PreciseFlexAxis.BASE: 100.0,
      PreciseFlexAxis.SHOULDER: 0.0,
      PreciseFlexAxis.ELBOW: 180.0,
      PreciseFlexAxis.WRIST: 180.0,
      PreciseFlexAxis.GRIPPER: 70.0,
    }

    await _run_until_confirmed_cancellation(
      io, lambda: arm.arm.pick_up_at_joint_state(position, resource_width=80.0)
    )

    assert io.consumed[-1] == "pickplate 1 0 0"
    assert all(command not in {"gripper 1", "attach 0", "movetosafe"} for command in io.commands)

  async def test_cancelled_place_consumes_terminal_reply_without_release(self) -> None:
    arm, io = _make_cancelling_arm("placeplate 1 0 0")
    position = {
      PreciseFlexAxis.BASE: 100.0,
      PreciseFlexAxis.SHOULDER: 0.0,
      PreciseFlexAxis.ELBOW: 180.0,
      PreciseFlexAxis.WRIST: 180.0,
      PreciseFlexAxis.GRIPPER: 70.0,
    }

    await _run_until_confirmed_cancellation(
      io, lambda: arm.arm.drop_at_joint_state(position, resource_width=80.0)
    )

    assert io.consumed[-1] == "placeplate 1 0 0"
    assert all(command not in {"gripper 1", "attach 0", "movetosafe"} for command in io.commands)

  async def test_fault_recovery_cancellation_preserves_last_confirmed_state(self) -> None:
    arm, io = _make_cancelling_arm("hp 1 20")

    await _run_until_confirmed_cancellation(io, arm.recover_from_fault)

    assert io.commands == ["sysState", "hp 1 20"]
    assert io.consumed == io.commands


class TestRequestSystemState(AnyioTestBase):
  async def test_returns_state_word_and_decodes_to_powerstate(self) -> None:
    arm = _make_arm()
    arm.send_command = AsyncMock(return_value="15")  # type: ignore[method-assign]

    state = await arm.request_system_state()

    assert state == 15
    assert PowerState(state) == PowerState.OFF_HARD_ESTOP


def test_is_collision_recognises_collision_codes_only() -> None:
  """Collision classification excludes E-stop, attachment, and unrelated errors."""
  for code in (-3100, -3101, -3105, -3122):
    assert is_collision(PreciseFlexError(code, "")), code
  assert not is_collision(PreciseFlexError(-1028, ""))
  assert not is_collision(PreciseFlexError(-1009, ""))
  assert not is_collision(ValueError())


class TestCollisionRecovery(AnyioTestBase):
  async def test_recover_repowers_attaches_and_homes(self) -> None:
    arm = _make_arm()
    arm.request_system_state = AsyncMock(return_value=7)  # type: ignore[method-assign]
    arm.power_on_robot = AsyncMock()  # type: ignore[method-assign]
    arm.attach = AsyncMock()  # type: ignore[method-assign]
    arm.home = AsyncMock()  # type: ignore[method-assign]

    await arm.recover_from_fault()

    arm.power_on_robot.assert_awaited_once()
    arm.attach.assert_awaited_once_with(1)
    arm.home.assert_awaited_once()

  async def test_recover_refuses_while_estop_engaged(self) -> None:
    arm = _make_arm()
    arm.request_system_state = AsyncMock(return_value=15)  # type: ignore[method-assign]
    arm.power_on_robot = AsyncMock()  # type: ignore[method-assign]
    arm.home = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(PreciseFlexError) as error_info:
      await arm.recover_from_fault()

    assert error_info.value.replycode == -1028
    arm.power_on_robot.assert_not_awaited()
    arm.home.assert_not_awaited()
