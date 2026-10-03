import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, cast
from unittest.mock import AsyncMock, patch

import anyio
import pytest

# Import the module under test
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.usb import USB
from pylabrobot.legacy.plate_reading.tecan.spark20m import spark_reader_async as reader_module
from pylabrobot.legacy.plate_reading.tecan.spark20m.enums import SparkDevice
from pylabrobot.legacy.plate_reading.tecan.spark20m.spark_reader_async import (
  SparkError,
  SparkReaderAsync,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class TestSparkReaderAsync(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    assert kwargs == {}
    # Patch USB class
    self.usb_patcher = patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_reader_async.USB"
    )
    self.mock_usb_class = self.usb_patcher.start()
    stack.callback(self.usb_patcher.stop)

    self.reader = SparkReaderAsync()

  async def test_send_command(self) -> None:
    # Setup connected device
    mock_dev = AsyncMock()
    self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev

    mock_dev._read_packet = AsyncMock()
    mock_dev._read_packet.return_value = b"\x81\x00\x00\x00\x00"

    # Mock calculate_checksum to return a predictable value
    with patch.object(self.reader, "_get_response") as mock_get_response:
      mock_get_response.return_value = {"payload": {"message": "OK"}}
      with patch.object(self.reader, "_calculate_checksum", return_value=0x99):
        success = await self.reader.send_command("CMD", timeout=1.0)

    assert success
    # Expected message: header + payload + checksum
    # Header: 0x01, seq=0, 0x00, len=3
    # Payload: b"CMD"
    # Checksum: 0x99
    expected_msg = bytes([0x01, 0x00, 0x00, 0x03]) + b"CMD" + bytes([0x99])

    mock_dev.write.assert_awaited_with(expected_msg)
    assert self.reader.seq_num == 1

  async def test_send_command_device_not_connected(self) -> None:
    with pytest.raises(RuntimeError, match="Device type .* not connected"):
      await self.reader.send_command("CMD", device_type=SparkDevice.ABSORPTION, timeout=1.0)

  async def test_get_response_success(self) -> None:
    # Mock parse_single_spark_packet
    with patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      mock_parse.return_value = {"type": "RespReady", "payload": {"status": "OK"}}

      mock_reader = AsyncMock()
      mock_reader.read.return_value = b"\x81\x00\x00\x00\x00"
      parsed = await self.reader._get_response(reader=mock_reader)

      assert parsed == {"type": "RespReady", "payload": {"status": "OK"}}

  async def test_get_response_busy_then_ready(self) -> None:
    # This tests the retry loop
    mock_reader = AsyncMock()
    mock_reader.read = AsyncMock()

    with patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      # Sequence of parse results:
      # 1. First read (passed as task): RespMessage (busy/intermediate)
      # 2. Retry read 1: RespReady
      mock_parse.side_effect = [
        {"type": "RespMessage", "payload": "msg1"},
        {"type": "RespReady", "payload": "done"},
      ]

      mock_reader.read.side_effect = [
        b"\x81\x00\x00\x00\x00",
        b"\x81\x00\x00\x00\x00",
      ]

      parsed = await self.reader._get_response(reader=mock_reader, timeout=1.0)

      assert parsed == {"type": "RespReady", "payload": "done"}
      assert "msg1" in self.reader.msgs

      # Should have called _read_packet once for the retry
      mock_reader.read.assert_awaited()

  async def test_background_reader_is_joined_on_normal_exit(self) -> None:
    mock_dev = AsyncMock()
    self.reader.devices[SparkDevice.ABSORPTION] = mock_dev

    DATA1 = b"\x81\x01\x00\x00\x00"
    DATA2 = b"\x81\x02\x00\x00\x00"
    read_started = anyio.Event()
    reader_joined = anyio.Event()
    read_count = 0

    async def read_packet(*args: object, **kwargs: object) -> Optional[bytes]:
      del args, kwargs
      nonlocal read_count
      read_count += 1
      if read_count == 1:
        return DATA1
      if read_count == 2:
        return DATA2
      read_started.set()
      try:
        await anyio.sleep_forever()
      finally:
        reader_joined.set()
      return None

    with patch.object(self.reader, "_read_packet", side_effect=read_packet):
      async with self.reader.background_read(SparkDevice.ABSORPTION) as results:
        with anyio.fail_after(1):
          await read_started.wait()

    assert reader_joined.is_set()
    assert results == [DATA1, DATA2]

  async def test_get_response_error(self) -> None:
    with patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      mock_parse.return_value = {"type": "RespError", "payload": {"error": "BadCommand"}}

      mock_reader = AsyncMock()
      mock_reader.read.return_value = b"\x86\x00\x00\x00\x00"
      with pytest.raises(SparkError):
        await self.reader._get_response(reader=mock_reader)

  async def test_get_response_empty_packet_retry(self) -> None:
    # Test that empty packet (ZLP) triggers retry
    mock_reader = AsyncMock()
    mock_reader.read = AsyncMock()

    with patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      # Sequence:
      # 1. First read task returns empty bytes -> Triggers ValueError in parser (mocked below) -> retry
      # 2. Retry read returns valid data -> Success

      mock_reader.read.side_effect = [b"", b"\x81\x00\x00\x00\x00"]

      # Logic:
      # _get_response awaits read_task -> returns b""
      # calls parse_single_spark_packet(b"") -> raises ValueError
      # catches, loops.
      # loop calls _read_from_endpoint -> returns b"retry_data"
      # calls parse_single_spark_packet(b"retry_data") -> returns valid

      mock_parse.return_value = {"type": "RespReady", "payload": "done"}

      parsed = await self.reader._get_response(reader=mock_reader, timeout=1.0)

      assert parsed == {"type": "RespReady", "payload": "done"}
      # Verify retry happened
      mock_reader.read.assert_awaited()

  async def test_read_packet_retries(self) -> None:
    # Test that _read_packet retries on invalid packets using packet validation logic.
    mock_reader = AsyncMock()

    # Case 1: Too short (<5 bytes)
    # Case 2: Invalid Indicator (0x00)
    # Case 3: Truncated (len < expected)
    # Case 4: Valid

    INVALID_SHORT = b"\x01\x02"
    INVALID_INDICATOR = b"\x00\x00\x00\x00\x00"
    INVALID_TRUNCATED = b"\x81\x00\x00\x05\x00"  # Payload len 5, but total len 5 (expect 4+5+1=10)
    VALID = b"\x81\x00\x00\x00\x00"

    mock_reader.read = AsyncMock(
      side_effect=[INVALID_SHORT, INVALID_INDICATOR, INVALID_TRUNCATED, VALID]
    )

    data = await self.reader._read_packet(mock_reader, timeout=1.0)
    assert data == VALID
    assert mock_reader.read.await_count == 4

  async def test_read_packet_assembles_split_usb_reply(self) -> None:
    """The Spark frame length, rather than a USB packet boundary, ends the reply."""
    mock_reader = AsyncMock()
    mock_reader.read.side_effect = [b"\x81\x00\x00\x04DA", b"TA\x00"]
    with anyio.fail_after(1):
      assert (
        await self.reader._read_packet(mock_reader, timeout=0.01) == b"\x81\x00\x00\x04DATA\x00"
      )

  async def test_cancelled_split_packet_can_resume_without_duplicate(self) -> None:
    """A cancelled frame assembly retains only its already acquired packet bytes."""
    mock_reader = AsyncMock()
    calls = 0

    async def receive(*, size: Optional[int], endpoint: Optional[int]) -> bytes:
      nonlocal calls
      calls += 1
      if calls == 1:
        return b"\x81\x00\x00\x04DA"
      if calls == 2:
        await anyio.sleep_forever()
        raise AssertionError("unreachable")
      return b"TA\x00"

    mock_reader.read.side_effect = receive
    with anyio.move_on_after(0.01) as scope:
      await self.reader._read_packet(mock_reader)
    assert scope.cancel_called
    assert await self.reader._read_packet(mock_reader, timeout=0.01) == b"\x81\x00\x00\x04DATA\x00"


ResponseHook = Callable[[USB, float], Awaitable[Optional[Dict[str, Any]]]]


class CommandUSB:
  """In-memory command USB with an optional write hook."""

  def __init__(self) -> None:
    self.writes: List[bytes] = []
    self.write_hook: Optional[Callable[[bytes], Awaitable[None]]] = None

  async def write(self, data: bytes) -> None:
    self.writes.append(data)
    if self.write_hook is not None:
      await self.write_hook(data)


class CommandSparkReader(SparkReaderAsync):
  """Spark reader whose response source is controlled by a test."""

  def __init__(self, response_hook: ResponseHook) -> None:
    super().__init__()
    self.response_hook = response_hook

  async def _get_response(self, reader: USB, timeout: float = 60.0) -> Optional[Dict[str, Any]]:
    return await self.response_hook(reader, timeout)


class CancelAfterAcquireLock:
  """Request cancellation at the transaction's pre-write boundary."""

  def __init__(self) -> None:
    self.scope: Optional[anyio.CancelScope] = None

  async def __aenter__(self) -> None:
    assert self.scope is not None
    self.scope.cancel()

  async def __aexit__(self, *args: object) -> None:
    del args


class TestSparkCommandTransactions(AnyioTestBase):
  """Exercise finite Spark command transactions under asyncio and Trio."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    assert kwargs == {}

  async def test_command_uses_no_detached_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
    armed = anyio.Event()
    usb = CommandUSB()

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      assert reader is cast(USB, usb)
      assert timeout == 1.0
      armed.set()
      return {"type": "RespReady", "payload": {"message": "OK"}}

    async def write(data: bytes) -> None:
      assert armed.is_set()

    def reject_detached(*args: object, **kwargs: object) -> None:
      del args, kwargs
      raise AssertionError("detached task")

    usb.write_hook = write
    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)
    monkeypatch.setattr(asyncio, "create_task", reject_detached)
    monkeypatch.setattr(asyncio, "ensure_future", reject_detached)

    assert await reader.send_command("CMD", timeout=1.0) == "OK"
    assert usb.writes == [b"\x01\x00\x00\x03CMDH"]

  async def test_cancel_before_write_writes_nothing(self) -> None:
    response_calls = 0
    usb = CommandUSB()

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      del reader, timeout
      nonlocal response_calls
      response_calls += 1
      return None

    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)
    lock = CancelAfterAcquireLock()
    reader.lock = cast(Any, lock)

    with anyio.CancelScope() as scope:
      lock.scope = scope
      await reader.send_command("CANCELLED")

    assert usb.writes == []
    assert response_calls == 0
    assert reader.seq_num == 0

  async def test_cancel_during_arming_delay_writes_nothing_and_joins_reader(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    response_started = anyio.Event()
    response_release = anyio.Event()
    response_joined = anyio.Event()
    arming_delay_started = anyio.Event()
    arming_delay_release = anyio.Event()
    caller_finished = anyio.Event()
    response_events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    usb = CommandUSB()

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      del reader, timeout
      response_started.set()
      try:
        await response_release.wait()
        response_events.append("response.released")
        return {"type": "RespReady", "payload": {"message": "TOO LATE"}}
      except anyio.get_cancelled_exc_class():
        response_events.append("response.cancelled")
        raise
      finally:
        response_joined.set()

    async def pause_arming_delay(delay: float) -> None:
      assert delay == 0.01
      arming_delay_started.set()
      await arming_delay_release.wait()

    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)
    monkeypatch.setattr(reader_module.anyio, "sleep", pause_arming_delay)

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await reader.send_command("CANCEL DURING ARMING")
        finally:
          caller_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await response_started.wait()
      await arming_delay_started.wait()
      scopes[0].cancel()
      await anyio.wait_all_tasks_blocked()
      joined_before_release = response_joined.is_set()
      if joined_before_release:
        with anyio.fail_after(1):
          await caller_finished.wait()
      returned_before_release = caller_finished.is_set()
      arming_delay_release.set()
      response_release.set()

    assert (
      returned_before_release,
      joined_before_release,
      response_events,
      usb.writes,
      reader.seq_num,
    ) == (True, True, ["response.cancelled"], [], 0)

  async def test_reader_failure_before_write_is_raised_without_committing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    failure = RuntimeError("USB read failed before write")
    callbacks: List[str] = []
    reader_joined = anyio.Event()
    usb = CommandUSB()

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      del reader, timeout
      try:
        raise failure
      finally:
        reader_joined.set()

    async def suppress_arming_cancellation(delay: float) -> None:
      assert delay == 0.01
      try:
        await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        return

    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)
    monkeypatch.setattr(reader_module.anyio, "sleep", suppress_arming_cancellation)

    with anyio.fail_after(1):
      with pytest.raises(RuntimeError) as error_info:
        await reader._send_command(
          "MUST NOT COMMIT", _on_commit=lambda: callbacks.append("committed")
        )

    assert error_info.value is failure
    assert callbacks == []
    assert usb.writes == []
    assert reader.seq_num == 0
    assert reader_joined.is_set()

  async def test_reader_failure_interrupts_indefinite_arming_and_is_raised(self) -> None:
    failure = RuntimeError("USB read failed during arming")
    reader_joined = anyio.Event()
    usb = CommandUSB()

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      del reader, timeout
      try:
        raise failure
      finally:
        reader_joined.set()

    async def block_arming(delay: float) -> None:
      assert delay == 0.01
      await anyio.sleep_forever()

    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)

    with patch.object(reader_module.anyio, "sleep", side_effect=block_arming):
      with anyio.fail_after(1):
        with pytest.raises(RuntimeError) as error_info:
          await reader.send_command("INTERRUPT ARMING")

    assert error_info.value is failure
    assert usb.writes == []
    assert reader.seq_num == 0
    assert reader_joined.is_set()

  async def test_reader_failure_after_write_waits_for_reconciliation_and_is_raised(self) -> None:
    failure = RuntimeError("USB read failed after write")
    write_started = anyio.Event()
    release_write = anyio.Event()
    reader_joined = anyio.Event()
    send_finished = anyio.Event()
    caught: List[BaseException] = []
    events: List[str] = []
    usb = CommandUSB()

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      del reader, timeout
      try:
        await write_started.wait()
        events.append("response.failed")
        raise failure
      finally:
        events.append("reader.joined")
        reader_joined.set()

    async def write(data: bytes) -> None:
      del data
      events.append("write.started")
      write_started.set()
      await release_write.wait()
      events.append("write.finished")

    usb.write_hook = write
    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)

    async def send() -> None:
      try:
        await reader.send_command("COMMITTED FAILURE")
      except BaseException as error:
        caught.append(error)
      finally:
        events.append("send.finished")
        send_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      with anyio.fail_after(1):
        await reader_joined.wait()
      assert not send_finished.is_set()
      release_write.set()

    assert caught == [failure]
    assert events == [
      "write.started",
      "response.failed",
      "reader.joined",
      "write.finished",
      "send.finished",
    ]
    assert len(usb.writes) == 1
    assert reader.seq_num == 1

  async def test_private_command_reports_exact_start_write_before_response_failure(self) -> None:
    failure = RuntimeError("START response failed")
    write_committed = anyio.Event()
    callbacks: List[str] = []
    usb = CommandUSB()

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      del reader, timeout
      await write_committed.wait()
      raise failure

    async def write(data: bytes) -> None:
      del data
      write_committed.set()

    usb.write_hook = write
    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)

    with pytest.raises(RuntimeError) as error_info:
      await reader._send_command(
        "MEASUREMENT START", timeout=1.0, _on_commit=lambda: callbacks.append("committed")
      )

    assert error_info.value is failure
    assert callbacks == ["committed"]
    assert usb.writes == [b"\x01\x00\x00\x11MEASUREMENT START:"]

  async def test_cancel_after_write_consumes_terminal_response(self) -> None:
    response_started = anyio.Event()
    release_response = anyio.Event()
    write_completed = anyio.Event()
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    usb = CommandUSB()

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      del reader, timeout
      response_started.set()
      await release_response.wait()
      events.append("response.consumed")
      return {"type": "RespReady", "payload": {"message": "DONE"}}

    async def write(data: bytes) -> None:
      del data
      write_completed.set()

    usb.write_hook = write
    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await reader._send_command(
            "COMMITTED", _on_commit=lambda: events.append("write.committed")
          )
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      with anyio.fail_after(1):
        await response_started.wait()
      with anyio.fail_after(1):
        await write_completed.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      cancelled_before_response = "cancelled" in events
      release_response.set()

    assert not cancelled_before_response
    assert events == ["write.committed", "response.consumed", "cancelled"]
    assert len(usb.writes) == 1
    assert reader.seq_num == 1

  async def test_concurrent_commands_do_not_cross_responses(self) -> None:
    first_response_started = anyio.Event()
    release_first_response = anyio.Event()
    usb = CommandUSB()
    response_count = 0
    responses: Dict[str, Optional[str]] = {}

    async def response(reader: USB, timeout: float) -> Optional[Dict[str, Any]]:
      del reader, timeout
      nonlocal response_count
      response_count += 1
      index = response_count
      if index == 1:
        first_response_started.set()
        await release_first_response.wait()
      message = "FIRST" if index == 1 else "SECOND"
      return {"type": "RespReady", "payload": {"message": message}}

    reader = CommandSparkReader(response)
    reader.devices[SparkDevice.PLATE_TRANSPORT] = cast(USB, usb)

    async def send(command: str) -> None:
      responses[command] = await reader.send_command(command)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send, "ONE")
      await first_response_started.wait()
      while not usb.writes:
        await anyio.lowlevel.checkpoint()
      task_group.start_soon(send, "TWO")
      await anyio.sleep(0.02)
      assert len(usb.writes) == 1
      release_first_response.set()

    first_response = responses["ONE"]
    second_response = responses["TWO"]
    assert first_response == "FIRST" and second_response == "SECOND"
    assert len(usb.writes) == 2

  async def test_response_reader_failure_propagates(self) -> None:
    failure = RuntimeError("USB read failed")
    usb = AsyncMock()
    usb.read.side_effect = failure
    reader = SparkReaderAsync()

    with pytest.raises(RuntimeError) as error_info:
      await reader._get_response(cast(USB, usb), timeout=1.0)

    assert error_info.value is failure
