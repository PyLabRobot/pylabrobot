# mypy: disable-error-code="union-attr,assignment,arg-type"

from collections import deque

import anyio
import pytest

from pylabrobot.legacy.plate_washing.biotek.el406.communication_tests import (
  TransactionFTDI,
  response_frame,
)
from pylabrobot.legacy.plate_washing.biotek.el406.errors import EL406CommunicationError
from pylabrobot.legacy.plate_washing.biotek.el406.mock_tests import PT96, EL406TestCase


class TestBatchContextManager(EL406TestCase):
  """Test the batch() context manager lifecycle."""

  async def test_batch_sets_in_batch_flag(self) -> None:
    assert not self.backend._in_batch
    async with self.backend.batch(PT96):
      assert self.backend._in_batch
    assert not self.backend._in_batch

  async def test_batch_sends_start_batch_commands(self) -> None:
    initial_count = len(self.backend.io.written_data)
    async with self.backend.batch(PT96):
      assert len(self.backend.io.written_data) > initial_count

  async def test_batch_sends_start_step_command(self) -> None:
    async with self.backend.batch(PT96):
      found = any(len(data) >= 3 and data[2] == 0x8D for data in self.backend.io.written_data)
      assert found, "START_STEP (0x8D) command not found in written data"

  async def test_batch_sends_cleanup_on_exit(self) -> None:
    async with self.backend.batch(PT96):
      count_inside = len(self.backend.io.written_data)
    assert len(self.backend.io.written_data) > count_inside

  async def test_batch_sends_cleanup_on_exception(self) -> None:
    with pytest.raises(ValueError, match="test error"):
      async with self.backend.batch(PT96):
        count_inside = len(self.backend.io.written_data)
        raise ValueError("test error")
    assert len(self.backend.io.written_data) > count_inside
    assert not self.backend._in_batch

  async def test_batch_resets_flag_on_exception(self) -> None:
    with pytest.raises(RuntimeError, match="boom"):
      async with self.backend.batch(PT96):
        raise RuntimeError("boom")
    assert not self.backend._in_batch

  async def test_batch_cancellation_runs_cleanup_before_propagating(self) -> None:
    events: list[str] = []
    entered = anyio.Event()
    cleanup_started = anyio.Event()
    release_cleanup = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def start_batch(_wire_byte: int, _on_commit=None) -> None:
      events.append("start")

    async def cleanup() -> None:
      events.append("cleanup.start")
      cleanup_started.set()
      await release_cleanup.wait()
      events.append("cleanup.complete")

    self.backend._start_batch = start_batch
    self.backend.cleanup_after_protocol = cleanup

    async def run_batch() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with self.backend.batch(PT96):
            entered.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
          events.append("cancel.propagated")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_batch)
      await entered.wait()
      scopes[0].cancel()
      await cleanup_started.wait()
      assert events == ["start", "cleanup.start"]
      release_cleanup.set()

    assert events == ["start", "cleanup.start", "cleanup.complete", "cancel.propagated"]
    assert not self.backend._in_batch

  async def test_batch_start_failure_clears_flag_without_cleanup(self) -> None:
    start_error = RuntimeError("start failed")
    cleanup_called = False

    async def start_batch(_wire_byte: int, _on_commit=None) -> None:
      raise start_error

    async def cleanup() -> None:
      nonlocal cleanup_called
      cleanup_called = True

    self.backend._start_batch = start_batch
    self.backend.cleanup_after_protocol = cleanup

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend.batch(PT96):
        pytest.fail("failed start reached batch body")

    assert error_info.value is start_error
    assert not cleanup_called
    assert not self.backend._in_batch

  async def test_batch_cancellation_after_start_runs_cleanup_before_body(self) -> None:
    events: list[str] = []
    start_committed = anyio.Event()
    release_start = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def start_batch(_wire_byte: int, on_commit=None) -> None:
      with anyio.CancelScope(shield=True):
        if on_commit is not None:
          on_commit()
        events.append("start.committed")
        start_committed.set()
        await release_start.wait()
        events.append("start.complete")
      await anyio.lowlevel.checkpoint()

    async def cleanup() -> None:
      events.append("cleanup.complete")

    self.backend._start_batch = start_batch
    self.backend.cleanup_after_protocol = cleanup

    async def run_batch() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with self.backend.batch(PT96):
            events.append("body")
        except anyio.get_cancelled_exc_class():
          events.append("cancel.propagated")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_batch)
      await start_committed.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      release_start.set()

    assert events == [
      "start.committed",
      "start.complete",
      "cleanup.complete",
      "cancel.propagated",
    ]
    assert not self.backend._in_batch

  async def test_batch_cancellation_during_preliminary_command_skips_start_and_cleanup(
    self,
  ) -> None:
    blocked = anyio.Event()
    release = anyio.Event()
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    commands: list[int] = []
    cleanup_called = False

    async def send(framed_message: bytes, **_kwargs) -> bytes:
      commands.append(int.from_bytes(framed_message[2:4], "little"))
      blocked.set()
      await release.wait()
      return b"\x06"

    async def cleanup() -> None:
      nonlocal cleanup_called
      cleanup_called = True

    self.backend._send_framed_command = send
    self.backend.cleanup_after_protocol = cleanup

    async def run_batch() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with self.backend.batch(PT96):
          pytest.fail("cancelled startup reached batch body")
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_batch)
      await blocked.wait()
      scopes[0].cancel()
      with anyio.move_on_after(0.1) as prompt:
        await finished.wait()
      release.set()

    assert not prompt.cancel_called
    assert commands == [0xBF]
    assert not cleanup_called

  async def test_batch_cancellation_after_start_commit_reconciles_then_cleans_up(self) -> None:
    events: list[str] = []
    committed = anyio.Event()
    release = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    reconciliation_error = EL406CommunicationError("reconciliation failed")

    async def send(framed_message: bytes, **kwargs) -> bytes:
      command = int.from_bytes(framed_message[2:4], "little")
      if command != 0x8D:
        return b"\x06"
      with anyio.CancelScope(shield=True):
        on_commit = kwargs.get("_on_commit")
        if on_commit is not None:
          on_commit()
        events.append("start.committed")
        committed.set()
        await release.wait()
        events.append("start.reconciled")
      await anyio.lowlevel.checkpoint()
      raise reconciliation_error

    async def cleanup() -> None:
      events.append("cleanup.complete")

    self.backend._send_framed_command = send
    self.backend.cleanup_after_protocol = cleanup

    async def run_batch() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with self.backend.batch(PT96):
            pytest.fail("cancelled startup reached batch body")
        except anyio.get_cancelled_exc_class():
          events.append("cancel.propagated")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_batch)
      await committed.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      release.set()

    assert events == [
      "start.committed",
      "start.reconciled",
      "cleanup.complete",
      "cancel.propagated",
    ]

  async def test_production_start_cancellation_before_start_step_write_skips_callback_and_cleanup(
    self,
  ) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    real_send = self.backend._send_framed_command
    preliminary_commands_done = anyio.Event()
    lock_held = anyio.Event()
    start_attempting = anyio.Event()
    release_lock = anyio.Event()
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    events: list[str] = []
    preliminary_count = 0

    async def send(framed_message: bytes, timeout=None, *, _on_commit=None) -> bytes:
      nonlocal preliminary_count
      command = int.from_bytes(framed_message[2:4], "little")
      if command != 0x8D:
        preliminary_count += 1
        if preliminary_count == 7:
          preliminary_commands_done.set()
          await lock_held.wait()
        return b"\x06"

      def committed() -> None:
        events.append("start.callback")
        assert _on_commit is not None
        _on_commit()

      events.append("start.attempting")
      start_attempting.set()
      return await real_send(framed_message, timeout, _on_commit=committed)

    async def cleanup() -> None:
      events.append("cleanup")

    self.backend._send_framed_command = send
    self.backend.cleanup_after_protocol = cleanup

    async def run_batch() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with self.backend.batch(PT96):
          pytest.fail("cancelled startup reached batch body")
      finished.set()

    async def hold_command_lock() -> None:
      await preliminary_commands_done.wait()
      lock = self.backend._command_lock
      assert lock is not None
      await lock.acquire()
      lock_held.set()
      await release_lock.wait()
      lock.release()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(hold_command_lock)
      task_group.start_soon(run_batch)
      await start_attempting.wait()
      scopes[0].cancel()
      with anyio.move_on_after(0.1) as prompt:
        await finished.wait()
      release_lock.set()

    assert not prompt.cancel_called
    assert preliminary_count == 7
    assert events == ["start.attempting"]
    assert io.written_data == []

  async def test_production_start_callback_follows_write_and_precedes_reconciliation(
    self,
  ) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    io.responses[0x8D] = deque([response_frame(0x8D)])
    io.block_read_number = 1
    real_send = self.backend._send_framed_command
    scopes: list[anyio.CancelScope] = []
    events = io.events

    async def send(framed_message: bytes, timeout=None, *, _on_commit=None) -> bytes:
      command = int.from_bytes(framed_message[2:4], "little")
      if command != 0x8D:
        return b"\x06"

      def committed() -> None:
        events.append("start.callback")
        assert _on_commit is not None
        _on_commit()

      return await real_send(framed_message, timeout, _on_commit=committed)

    async def cleanup() -> None:
      events.append("cleanup")

    self.backend._send_framed_command = send
    self.backend.cleanup_after_protocol = cleanup

    async def run_batch() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with self.backend.batch(PT96):
            pytest.fail("cancelled startup reached batch body")
        except anyio.get_cancelled_exc_class():
          events.append("cancel.propagated")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_batch)
      await io.blocked.wait()
      callback_followed_write = "start.callback" in events and (
        events.index("write.1.committed") < events.index("start.callback")
      )
      response_was_blocked = "response.complete" not in events
      scopes[0].cancel()
      io.release.set()

    assert callback_followed_write
    assert response_was_blocked
    assert events.index("start.callback") < events.index("response.complete")
    assert events.index("response.complete") < events.index("cleanup")
    assert events.index("cleanup") < events.index("cancel.propagated")

  async def test_production_start_commits_before_blocked_payload_and_cleans_up(
    self,
  ) -> None:
    class PayloadGateFTDI(TransactionFTDI):
      """Record completion separately from the beginning of each write."""

      async def write(self, data: bytes) -> int:
        write_number = self._write_number + 1
        result = await super().write(data)
        self.events.append(f"write.{write_number}.complete")
        return result

    io = PayloadGateFTDI()
    self.backend.io = io
    self.backend.timeout = 0.02
    io.block_write_number = 2
    real_send = self.backend._send_framed_command
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    events = io.events

    async def send(framed_message: bytes, timeout=None, *, _on_commit=None) -> bytes:
      command = int.from_bytes(framed_message[2:4], "little")
      if command != 0x8D:
        return b"\x06"

      def committed() -> None:
        events.append("start.callback")
        assert _on_commit is not None
        _on_commit()

      return await real_send(framed_message, timeout, _on_commit=committed)

    async def cleanup() -> None:
      events.append("cleanup")

    self.backend._send_framed_command = send
    self.backend.cleanup_after_protocol = cleanup

    async def run_batch() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with self.backend.batch(PT96):
            pytest.fail("cancelled startup reached batch body")
        except anyio.get_cancelled_exc_class():
          events.append("cancel.propagated")
          raise
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_batch)
      await io.blocked.wait()
      owner_at_payload = self.backend._batch_owner
      scopes[0].cancel()
      with anyio.move_on_after(0.2) as bounded:
        await finished.wait()

    assert not bounded.cancel_called
    assert owner_at_payload is not None
    assert events.index("write.1.complete") < events.index("start.callback")
    assert events.index("start.callback") < events.index("write.2.committed")
    assert "write.2.complete" not in events
    assert "response.complete" not in events
    assert events.index("write.2.committed") < events.index("cleanup")
    assert events.index("cleanup") < events.index("cancel.propagated")
    assert self.backend._batch_owner is None
    assert not self.backend._in_batch

  async def test_batch_cleanup_failure_preserves_body_failure_context(self) -> None:
    body_error = RuntimeError("body failed")
    cleanup_error = RuntimeError("cleanup failed")

    async def start_batch(_wire_byte: int, _on_commit=None) -> None:
      pass

    async def cleanup() -> None:
      raise cleanup_error

    self.backend._start_batch = start_batch
    self.backend.cleanup_after_protocol = cleanup

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend.batch(PT96):
        raise body_error

    assert error_info.value is cleanup_error
    assert cleanup_error.__context__ is body_error
    assert not self.backend._in_batch


class TestBatchReentrancy(EL406TestCase):
  """Test batch ownership and sequential reuse."""

  async def test_nested_batch_has_single_owner(self) -> None:
    starts = 0
    cleanups = 0

    async def start_batch(_wire_byte: int, _on_commit=None) -> None:
      nonlocal starts
      starts += 1

    async def cleanup() -> None:
      nonlocal cleanups
      cleanups += 1

    self.backend._start_batch = start_batch
    self.backend.cleanup_after_protocol = cleanup

    async with self.backend.batch(PT96):
      async with self.backend.batch(PT96):
        assert self.backend._in_batch

    assert starts == 1
    assert cleanups == 1
    assert not self.backend._in_batch

  async def test_concurrent_batch_cannot_steal_owner(self) -> None:
    events: list[str] = []
    first_entered = anyio.Event()
    release_first = anyio.Event()

    async def start_batch(_wire_byte: int, _on_commit=None) -> None:
      events.append("start")

    async def cleanup() -> None:
      events.append("cleanup")

    self.backend._start_batch = start_batch
    self.backend.cleanup_after_protocol = cleanup

    async def first() -> None:
      async with self.backend.batch(PT96):
        events.append("first.body")
        first_entered.set()
        await release_first.wait()

    async def second() -> None:
      async with self.backend.batch(PT96):
        events.append("second.body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(first)
      await first_entered.wait()
      task_group.start_soon(second)
      await anyio.sleep(0.01)
      assert events == ["start", "first.body"]
      release_first.set()

    assert events == ["start", "first.body", "cleanup", "start", "second.body", "cleanup"]
    assert not self.backend._in_batch

  async def test_sequential_batches_reuse_writer(self) -> None:
    starts = 0
    cleanups = 0

    async def start_batch(_wire_byte: int, _on_commit=None) -> None:
      nonlocal starts
      starts += 1

    async def cleanup() -> None:
      nonlocal cleanups
      cleanups += 1

    self.backend._start_batch = start_batch
    self.backend.cleanup_after_protocol = cleanup

    async with self.backend.batch(PT96):
      pass
    async with self.backend.batch(PT96):
      pass

    assert starts == 2
    assert cleanups == 2


class TestStepAutoBatching(EL406TestCase):
  """Test that step commands auto-batch when called outside a batch."""

  async def test_step_auto_batches(self) -> None:
    assert not self.backend._in_batch
    await self.backend.shake(PT96, duration=5, intensity="Medium")
    assert not self.backend._in_batch

  async def test_step_sends_start_and_cleanup(self) -> None:
    initial_count = len(self.backend.io.written_data)
    await self.backend.shake(PT96, duration=5, intensity="Medium")
    written = self.backend.io.written_data[initial_count:]
    assert any(len(data) >= 3 and data[2] == 0x8D for data in written)
    assert any(len(data) >= 3 and data[2] == 0xA3 for data in written)
    assert any(len(data) >= 3 and data[2] == 0x8C for data in written)

  async def test_step_inside_batch_does_not_double_batch(self) -> None:
    async with self.backend.batch(PT96):
      count_before = len(self.backend.io.written_data)
      await self.backend.shake(PT96, duration=5, intensity="Medium")
      written = self.backend.io.written_data[count_before:]
      assert not any(len(data) >= 3 and data[2] == 0x8D for data in written)
      assert not any(len(data) >= 3 and data[2] == 0x8C for data in written)

  async def test_multiple_steps_in_batch_share_single_batch(self) -> None:
    initial_count = len(self.backend.io.written_data)
    async with self.backend.batch(PT96):
      await self.backend.shake(PT96, duration=5, intensity="Medium")
      await self.backend.shake(PT96, duration=3, intensity="Fast")
    written = self.backend.io.written_data[initial_count:]
    assert sum(1 for data in written if len(data) >= 3 and data[2] == 0x8D) == 1
    assert sum(1 for data in written if len(data) >= 3 and data[2] == 0x8C) == 1
    assert sum(1 for data in written if len(data) >= 3 and data[2] == 0xA3) == 2
