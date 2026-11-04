# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 communication functionality."""

from collections import deque
from typing import Deque

import anyio
import pytest

from pylabrobot.legacy.plate_washing.biotek.el406 import communication
from pylabrobot.legacy.plate_washing.biotek.el406.communication import (
  STATE_INITIAL,
  STATE_RUNNING,
)
from pylabrobot.legacy.plate_washing.biotek.el406.errors import (
  EL406CommunicationError,
  EL406DeviceError,
)
from pylabrobot.legacy.plate_washing.biotek.el406.mock_tests import EL406TestCase, MockFTDI
from pylabrobot.legacy.plate_washing.biotek.el406.protocol import build_framed_message


def response_frame(command: int, data: bytes = b"") -> bytes:
  """Build a literal-shaped EL406 response frame."""
  header = bytes.fromhex("0102") + command.to_bytes(2, "little") + bytes.fromhex("010000")
  header += len(data).to_bytes(2, "little") + b"\x00\x00"
  return b"\x06" + header + data


def status_frame(state: int) -> bytes:
  """Build a status response with zero validity and status."""
  return response_frame(0x92, b"\x00\x00" + state.to_bytes(2, "little") + bytes(5))


class TransactionFTDI(MockFTDI):
  """Queue command-specific responses and expose deterministic I/O boundaries."""

  def __init__(self) -> None:
    super().__init__()
    self.written_data.clear()
    self.read_buffer = b""
    self.responses: dict[int, Deque[bytes]] = {}
    self.events: list[str] = []
    self.block_write_number: int | None = None
    self.block_read_number: int | None = None
    self.blocked = anyio.Event()
    self.release = anyio.Event()
    self._write_number = 0
    self._read_number = 0

  async def write(self, data: bytes) -> int:
    self._write_number += 1
    self.written_data.append(data)
    self.events.append(f"write.{self._write_number}.committed")
    if len(data) == 11:
      command = int.from_bytes(data[2:4], "little")
      responses = self.responses.get(command)
      if responses:
        self.read_buffer += responses.popleft()
    if self._write_number == self.block_write_number:
      self.blocked.set()
      await self.release.wait()
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    self._read_number += 1
    if self._read_number == self.block_read_number:
      self.blocked.set()
      await self.release.wait()
    result = await super().read(num_bytes)
    if not self.read_buffer:
      self.events.append("response.complete")
    return result

  async def usb_purge_rx_buffer(self) -> None:
    self.events.append("purge.rx")

  async def usb_purge_tx_buffer(self) -> None:
    self.events.append("purge.tx")


class TestTestCommunication(EL406TestCase):
  """Test communication verification."""

  async def test_communication_sends_query_command(self) -> None:
    """Test communication should send a query command."""
    self.backend.io = MockFTDI()
    self.backend.io.set_read_buffer(b"\x06" * 10)

    await self.backend._test_communication()

    assert len(self.backend.io.written_data) > 0


class TestCancellationSafeTransactions(EL406TestCase):
  """Exercise command transaction commit and cancellation boundaries."""

  @pytest.mark.parametrize("kind", ["command", "query"])
  async def test_framed_ack_and_response_share_a_postwrite_budget(self, kind: str) -> None:
    class DelayedFTDI(TransactionFTDI):
      async def read(self, num_bytes: int = 1) -> bytes:
        await anyio.sleep(0.015)
        return await super().read(num_bytes)

    io = DelayedFTDI()
    io.responses[0x73] = deque([response_frame(0x73, b"x")])
    self.backend.io = io
    if kind == "command":
      assert (
        await self.backend._send_framed_command(build_framed_message(0x73), timeout=0.02) == b"\x06"
      )
    else:
      with pytest.raises(TimeoutError, match="response header"):
        await self.backend._send_framed_query(0x73, timeout=0.02)

  async def test_action_write_has_own_budget_then_ack_and_completion_share_one(self) -> None:
    header = response_frame(0x70, b"done")[1:12]

    async def write(_data: bytes) -> None:
      await anyio.sleep(0.01)

    async def wait_for_ack() -> None:
      await anyio.sleep(0.01)

    async def read_exact(count: int, buf: bytearray) -> None:
      await anyio.sleep(0.01)
      buf.extend(header if count == 11 else b"done")

    self.backend.timeout = 1
    self.backend._write_to_device = write
    self.backend._wait_for_ack = wait_for_ack
    self.backend._read_exact_bytes = read_exact

    result = await self.backend._send_action_command(build_framed_message(0x70), timeout=0.1)

    assert result == response_frame(0x70, b"done")[1:]

  async def test_action_completion_over_shared_postwrite_budget_keeps_exact_error(self) -> None:
    async def write(_data: bytes) -> None:
      await anyio.sleep(0)

    async def wait_for_ack() -> None:
      await anyio.sleep(0.01)

    async def read_exact(_count: int, buf: bytearray) -> None:
      await anyio.sleep_forever()

    self.backend.timeout = 1
    self.backend._write_to_device = write
    self.backend._wait_for_ack = wait_for_ack
    self.backend._read_exact_bytes = read_exact

    with pytest.raises(TimeoutError) as error_info:
      await self.backend._send_action_command(build_framed_message(0x70), timeout=0.02)

    assert str(error_info.value) == "Timeout waiting for completion header (got 0 bytes)"

  async def test_framed_query_data_over_fresh_response_budget_keeps_exact_error(self) -> None:
    header = response_frame(0x73, b"x")[1:12]

    async def write(_data: bytes) -> None:
      await anyio.sleep(0)

    async def wait_for_ack() -> None:
      await anyio.sleep(0.005)

    async def read_exact(count: int, buf: bytearray) -> None:
      if count == 11:
        await anyio.sleep(0.005)
        buf.extend(header)
      else:
        await anyio.sleep_forever()

    self.backend._write_to_device = write
    self.backend._wait_for_ack = wait_for_ack
    self.backend._read_exact_bytes = read_exact

    with pytest.raises(TimeoutError) as error_info:
      await self.backend._send_framed_query(0x73, timeout=0.02)

    assert str(error_info.value) == "Timeout reading response data (got 0/1 bytes)"

  async def test_step_waits_exact_initial_delay_before_first_status_poll(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    sleeps: list[float] = []
    commands: list[int] = []

    async def ready(*_args, **_kwargs) -> None:
      pass

    async def exchange(framed_message: bytes, timeout: float) -> bytes:
      assert timeout == 1
      command = int.from_bytes(framed_message[2:4], "little")
      commands.append(command)
      if command == 0x92:
        assert sleeps == [0.5, 0.2]
        return status_frame(STATE_INITIAL)
      return response_frame(command)

    async def sleep(delay: float) -> None:
      sleeps.append(delay)

    self.backend._wait_until_ready = ready
    self.backend._exchange_framed_command = exchange
    self.backend.timeout = 1
    monkeypatch.setattr(communication.anyio, "sleep", sleep)

    result = await self.backend._send_step_command(
      build_framed_message(0xA2), timeout=1, poll_interval=0.2
    )

    assert result == status_frame(STATE_INITIAL)
    assert commands == [0xA2, 0x92]

  async def test_concurrent_framed_commands_do_not_cross_responses(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    first_frame = response_frame(0x73, b"first")
    second_frame = response_frame(0x74, b"second")
    io.responses = {0x73: deque([first_frame]), 0x74: deque([second_frame])}
    results: dict[str, bytes] = {}

    async def send(name: str, command: int) -> None:
      results[name] = await self.backend._send_framed_command(build_framed_message(command))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send, "first", 0x73)
      task_group.start_soon(send, "second", 0x74)

    assert results["first"] == first_frame
    assert results["second"] == second_frame
    assert not io.read_buffer

  async def test_concurrent_framed_command_waits_for_complete_first_response(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    io.responses = {
      0x73: deque([response_frame(0x73, b"first")]),
      0x74: deque([response_frame(0x74, b"second")]),
    }
    io.block_read_number = 1
    second_attempting = anyio.Event()

    async def send_second() -> None:
      second_attempting.set()
      await self.backend._send_framed_command(build_framed_message(0x74))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(self.backend._send_framed_command, build_framed_message(0x73))
      await io.blocked.wait()
      first_transaction_events = len(io.events)
      task_group.start_soon(send_second)
      await second_attempting.wait()
      await anyio.lowlevel.checkpoint()
      assert len(io.events) == first_transaction_events
      io.release.set()

    first_complete = io.events.index("response.complete")
    assert "purge.rx" not in io.events[first_transaction_events:first_complete]
    assert io.events.index("write.2.committed") > first_complete

  async def test_cancel_before_header_writes_nothing(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    lock = self.backend._command_lock
    assert lock is not None
    await lock.acquire()
    started = anyio.Event()
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        started.set()
        await self.backend._send_framed_command(build_framed_message(0x73))
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await started.wait()
      await anyio.lowlevel.checkpoint()
      scopes[0].cancel()
      await finished.wait()
      lock.release()

    assert io.written_data == []

  async def test_step_cancelled_while_command_lock_held_writes_nothing_promptly(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    lock = self.backend._command_lock
    assert lock is not None
    await lock.acquire()
    started = anyio.Event()
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def ready(*_args, **_kwargs) -> None:
      pass

    self.backend._wait_until_ready = ready

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        started.set()
        await self.backend._send_step_command(build_framed_message(0xA2))
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await started.wait()
      await anyio.lowlevel.checkpoint()
      scopes[0].cancel()
      with anyio.move_on_after(0.1) as prompt:
        await finished.wait()
      lock.release()

    assert not prompt.cancel_called
    assert io.written_data == []

  @pytest.mark.parametrize("blocked_write", [1, 2], ids=["header", "payload"])
  async def test_framed_write_uses_operation_deadline_and_lock_is_reusable(
    self, blocked_write: int
  ) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    command = build_framed_message(0x89, b"payload")
    io.block_write_number = blocked_write
    finished = anyio.Event()
    errors: list[BaseException] = []

    async def send() -> None:
      try:
        await self.backend._send_framed_command(command, timeout=0.02)
      except BaseException as error:
        errors.append(error)
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.blocked.wait()
      with anyio.move_on_after(0.1) as bounded:
        await finished.wait()
      io.release.set()

    assert not bounded.cancel_called
    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)

    io.block_write_number = None
    io.responses[0x73] = deque([response_frame(0x73)])
    assert await self.backend._send_framed_command(build_framed_message(0x73), timeout=0.1)

  @pytest.mark.parametrize("blocked_write", [1, 2], ids=["header", "payload"])
  async def test_action_write_timeout_releases_lock_for_sequential_reuse(
    self, blocked_write: int
  ) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    command = build_framed_message(0x70, b"payload")
    io.block_write_number = blocked_write
    finished = anyio.Event()
    errors: list[BaseException] = []

    async def send() -> None:
      try:
        await self.backend._send_action_command(command, timeout=0.02)
      except BaseException as error:
        errors.append(error)
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.blocked.wait()
      with anyio.move_on_after(0.1) as bounded:
        await finished.wait()
      io.release.set()

    assert not bounded.cancel_called
    assert len(errors) == 1
    assert type(errors[0]) is TimeoutError
    assert str(errors[0]) == "Timeout writing action command"

    io.block_write_number = None
    io.read_buffer = b""
    frame = response_frame(0x71, b"done")
    io.responses[0x71] = deque([frame])
    result = await self.backend._send_action_command(build_framed_message(0x71), timeout=0.1)
    assert result == frame[1:]

  @pytest.mark.parametrize("blocked_write", [1, 2], ids=["header", "payload"])
  async def test_query_write_timeout_releases_lock_for_sequential_reuse(
    self, blocked_write: int
  ) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    io.block_write_number = blocked_write
    finished = anyio.Event()
    errors: list[BaseException] = []

    async def send() -> None:
      try:
        await self.backend._send_framed_query(0x95, b"payload", timeout=0.02)
      except BaseException as error:
        errors.append(error)
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.blocked.wait()
      with anyio.move_on_after(0.1) as bounded:
        await finished.wait()
      io.release.set()

    assert not bounded.cancel_called
    assert len(errors) == 1
    assert type(errors[0]) is TimeoutError
    assert str(errors[0]) == "Timeout writing command 0x0095"

    io.block_write_number = None
    io.read_buffer = b""
    io.responses[0x96] = deque([response_frame(0x96, b"reused")])
    assert await self.backend._send_framed_query(0x96, timeout=0.1) == b"reused"

  @pytest.mark.parametrize(
    "reconciliation_error",
    [
      RuntimeError("NAK"),
      TimeoutError("timed out"),
      EL406CommunicationError("transport failed"),
      EL406DeviceError(0x1500, "device failed"),
    ],
    ids=["nak", "timeout", "communication", "device"],
  )
  async def test_pending_cancellation_wins_over_reconciliation_error(
    self, reconciliation_error: BaseException
  ) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    committed = anyio.Event()
    release = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    propagated: list[BaseException] = []

    async def exchange(_message: bytes, _timeout: float) -> bytes:
      committed.set()
      await release.wait()
      raise reconciliation_error

    self.backend._exchange_framed_command = exchange

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await self.backend._send_framed_command(build_framed_message(0x73))
        except BaseException as error:
          propagated.append(error)
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await committed.wait()
      scopes[0].cancel()
      release.set()

    assert len(propagated) == 1
    assert isinstance(propagated[0], anyio.get_cancelled_exc_class())

  async def test_reconciliation_error_without_cancellation_preserves_identity_and_context(
    self,
  ) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    cause = ValueError("cause")
    reconciliation_error = EL406CommunicationError("transport failed")
    reconciliation_error.__context__ = cause

    async def exchange(_message: bytes, _timeout: float) -> bytes:
      raise reconciliation_error

    self.backend._exchange_framed_command = exchange

    with pytest.raises(EL406CommunicationError) as error_info:
      await self.backend._send_framed_command(build_framed_message(0x73))

    assert error_info.value is reconciliation_error
    assert error_info.value.__context__ is cause

  async def test_cancel_after_header_completes_response(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    frame = response_frame(0x73, b"complete")
    io.responses[0x73] = deque([frame])
    io.block_read_number = 1
    scopes: list[anyio.CancelScope] = []

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await self.backend._send_framed_command(build_framed_message(0x73))
        except anyio.get_cancelled_exc_class():
          io.events.append("cancel.propagated")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.blocked.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      io.release.set()

    assert io.events.index("response.complete") < io.events.index("cancel.propagated")
    assert not io.read_buffer

  async def test_cancel_between_header_and_data_sends_data_and_reconciles(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    frame = response_frame(0x89)
    io.responses[0x89] = deque([frame])
    io.block_write_number = 1
    scopes: list[anyio.CancelScope] = []
    command = build_framed_message(0x89, b"payload")

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await self.backend._send_framed_command(command)
        except anyio.get_cancelled_exc_class():
          io.events.append("cancel.propagated")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.blocked.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      io.release.set()

    assert io.written_data == [command[:11], b"payload"]
    assert io.events.index("response.complete") < io.events.index("cancel.propagated")

  async def test_cancelled_action_waits_for_completion_frame(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    io.responses[0x70] = deque([response_frame(0x70, b"done")])
    io.block_read_number = 2
    scopes: list[anyio.CancelScope] = []

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await self.backend._send_action_command(build_framed_message(0x70))
        except anyio.get_cancelled_exc_class():
          io.events.append("cancel.propagated")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.blocked.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      io.release.set()

    assert io.events.index("response.complete") < io.events.index("cancel.propagated")

  async def test_step_cancellation_reconciles_status_poll(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    io.responses[0x92] = deque(
      [status_frame(STATE_INITIAL), status_frame(STATE_RUNNING), status_frame(STATE_INITIAL)]
    )
    io.responses[0xA2] = deque([response_frame(0xA2)])
    io.block_read_number = 6
    scopes: list[anyio.CancelScope] = []
    result: list[bytes] = []

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          result.append(
            await self.backend._send_step_command(
              build_framed_message(0xA2), timeout=1, poll_interval=0
            )
          )
        except anyio.get_cancelled_exc_class():
          io.events.append("cancel.propagated")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.blocked.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      io.release.set()

    assert result == []
    assert io.events.index("response.complete") < io.events.index("cancel.propagated")
    assert [
      int.from_bytes(write[2:4], "little") for write in io.written_data if len(write) == 11
    ] == [
      0x92,
      0xA2,
      0x92,
      0x92,
    ]

  async def test_step_retains_lock_across_nonterminal_and_terminal_polls(self) -> None:
    io = TransactionFTDI()
    self.backend.io = io
    io.responses[0x92] = deque(
      [status_frame(STATE_INITIAL), status_frame(STATE_RUNNING), status_frame(STATE_INITIAL)]
    )
    io.responses[0xA2] = deque([response_frame(0xA2)])
    io.responses[0x73] = deque([response_frame(0x73)])
    io.block_read_number = 6
    competitor_attempting = anyio.Event()

    async def send_competitor() -> None:
      competitor_attempting.set()
      await self.backend._send_framed_command(build_framed_message(0x73))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(
        self.backend._send_step_command,
        build_framed_message(0xA2),
        1,
        0,
      )
      await io.blocked.wait()
      blocked_events = len(io.events)
      task_group.start_soon(send_competitor)
      await competitor_attempting.wait()
      await anyio.lowlevel.checkpoint()
      assert len(io.events) == blocked_events
      io.release.set()

    commands = [
      int.from_bytes(write[2:4], "little") for write in io.written_data if len(write) == 11
    ]
    assert commands == [0x92, 0xA2, 0x92, 0x92, 0x73]
    competitor_write = io.events.index("write.5.committed")
    assert io.events[:competitor_write].count("response.complete") == 4
