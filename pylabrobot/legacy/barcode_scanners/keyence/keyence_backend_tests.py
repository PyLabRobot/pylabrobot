from typing import Any, Awaitable, Callable, Optional, Union, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.barcode_scanners.backend import BarcodeScannerError
from pylabrobot.legacy.barcode_scanners.keyence.keyence_backend import (
  HAS_SERIAL,
  KeyenceBarcodeScannerBackend,
)
from pylabrobot.resources.barcode import Barcode
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

ReadEffects = Union[list[bytes], Callable[..., Union[bytes, Awaitable[bytes]]]]


class RecordingSerial(MockIO):
  """Record Serial protocol traffic and lifecycle events without opening hardware."""

  def __init__(
    self,
    events: list[str],
    reads: ReadEffects,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.events = events
    self.read.side_effect = reads
    self.write.side_effect = self._record_write
    self.exit_error = exit_error

  def _record_write(self, data: bytes) -> None:
    self.events.append(f"write:{data.decode('ascii').rstrip()}")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("RecordingSerial does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._exit_serial)

  async def _exit_serial(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error


def make_backend(
  events: list[str],
  reads: ReadEffects,
  exit_error: Optional[BaseException] = None,
) -> tuple[KeyenceBarcodeScannerBackend, RecordingSerial]:
  backend = KeyenceBarcodeScannerBackend(port="COM1")
  backend.reply_gap = 0.01
  serial = RecordingSerial(events, reads, exit_error)
  backend.io = cast(Serial, serial)
  return backend, serial


def test_pyserial_is_available() -> None:
  assert HAS_SERIAL


def test_native_backend_uses_managed_lifecycle_methods() -> None:
  assert KeyenceBarcodeScannerBackend.setup is HasLifespan.setup
  assert KeyenceBarcodeScannerBackend.stop is HasLifespan.stop


class TestKeyenceSendCommand(AnyioTestBase):
  """send_command reads a full carriage-return-terminated reply."""

  async def test_writes_carriage_return_terminated_command(self) -> None:
    backend, serial = make_backend([], reads=[b"\r"])
    await backend.send_command("RMOTOR")
    assert serial.write.calls == [((b"RMOTOR\r",), {})]

  async def test_accumulates_reply_until_carriage_return(self) -> None:
    backend, _ = make_backend([], reads=[b"M", b"O", b"T", b"O", b"R", b"O", b"N", b"\r"])
    assert await backend.send_command("RMOTOR") == "MOTORON"

  async def test_stops_reading_at_terminator(self) -> None:
    backend, serial = make_backend([], reads=[b"O", b"K", b"\r"])
    await backend.send_command("LOFF")
    assert len(serial.read.calls) == 3

  async def test_quiet_read_is_an_empty_reply(self) -> None:
    async def idle() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    backend, _ = make_backend([], reads=idle)
    assert await backend.send_command("LON") == ""


class TestKeyenceLifespan(AnyioTestBase):
  async def test_entry_and_normal_exit_order(self) -> None:
    events: list[str] = []
    backend, _ = make_backend(events, reads=[b"MOTORON", b"\r"])
    async with backend:
      events.append("body")
    assert events == ["serial.enter", "write:RMOTOR", "body", "serial.exit"]

  async def test_motor_off_rolls_back_serial(self) -> None:
    events: list[str] = []
    backend, _ = make_backend(events, reads=[b"MOTOROFF", b"\r"])
    with pytest.raises(BarcodeScannerError, match="Motor is off"):
      async with backend:
        pytest.fail("failed entry must not reach the body")
    assert events[-1] == "serial.exit"

  async def test_initialization_timeout_rolls_back_serial(self) -> None:
    events: list[str] = []

    async def idle() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    backend, _ = make_backend(events, reads=idle)
    backend.init_timeout = 0.01
    backend.poll_interval = 0

    with pytest.raises(BarcodeScannerError, match="Timeout waiting") as error_info:
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert isinstance(error_info.value.__context__, TimeoutError)
    assert events[-1] == "serial.exit"

  async def test_cancellation_during_entry_rolls_back_serial(self) -> None:
    events: list[str] = []
    read_started = anyio.Event()

    async def block_read() -> bytes:
      read_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    backend, _ = make_backend(events, reads=block_read)

    async def run_backend() -> None:
      try:
        async with backend:
          pytest.fail("cancelled entry must not reach the body")
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await read_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "write:RMOTOR", "serial.exit", "cancelled"]
    assert not backend.is_active

  async def test_cancellation_from_body_closes_serial(self) -> None:
    events: list[str] = []
    body_started = anyio.Event()
    backend, _ = make_backend(events, reads=[b"MOTORON", b"\r"])

    async def run_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "write:RMOTOR", "serial.exit", "cancelled"]
    assert not backend.is_active

  async def test_sequential_reuse_enters_and_exits_serial_twice(self) -> None:
    events: list[str] = []
    backend, serial = make_backend(
      events,
      reads=[b"MOTORON", b"\r", b"MOTORON", b"\r"],
    )

    async with backend:
      pass
    async with backend:
      pass

    assert [event for event in events if event.startswith("serial.")] == [
      "serial.enter",
      "serial.exit",
      "serial.enter",
      "serial.exit",
    ]
    assert serial.write.calls == [((b"RMOTOR\r",), {}), ((b"RMOTOR\r",), {})]

  async def test_serial_exit_failure_propagates_without_extra_motor_command(self) -> None:
    events: list[str] = []
    backend, serial = make_backend(
      events,
      reads=[b"MOTORON", b"\r"],
      exit_error=RuntimeError("serial close failed"),
    )

    with pytest.raises(RuntimeError, match="serial close failed"):
      async with backend:
        pass

    assert not backend.is_active
    assert serial.write.calls == [((b"RMOTOR\r",), {})]


class TestKeyenceScanBarcode(AnyioTestBase):
  """scan_barcode releases the LON-latched read beam after every outcome."""

  @staticmethod
  def make_scanner(
    replies: Optional[dict[str, str]] = None,
    raise_on: Optional[set[str]] = None,
  ) -> tuple[KeyenceBarcodeScannerBackend, AsyncMock]:
    backend = KeyenceBarcodeScannerBackend(port="COM1")
    configured_replies = replies or {}
    configured_failures = raise_on or set()

    def send(command: str) -> str:
      if command in configured_failures:
        raise RuntimeError(f"simulated failure on {command}")
      return configured_replies.get(command, "")

    send_command = AsyncMock(side_effect=send)
    backend.send_command = send_command  # type: ignore[method-assign]
    return backend, send_command

  async def test_success_returns_barcode_then_releases_beam(self) -> None:
    backend, send_command = self.make_scanner({"LON": "ABC123", "LOFF": ""})
    barcode = await backend.scan_barcode()
    assert isinstance(barcode, Barcode)
    assert barcode.data == "ABC123"
    assert [call.args[0] for call in send_command.await_args_list] == ["LON", "LOFF"]

  async def test_releases_beam_when_reader_off(self) -> None:
    backend, send_command = self.make_scanner({"LON": "NG", "LOFF": ""})
    with pytest.raises(BarcodeScannerError):
      await backend.scan_barcode()
    assert [call.args[0] for call in send_command.await_args_list] == ["LON", "LOFF"]

  async def test_releases_beam_on_error_response(self) -> None:
    backend, send_command = self.make_scanner({"LON": "ERR99", "LOFF": ""})
    with pytest.raises(BarcodeScannerError):
      await backend.scan_barcode()
    assert [call.args[0] for call in send_command.await_args_list] == ["LON", "LOFF"]

  async def test_loff_failure_does_not_mask_successful_scan(self) -> None:
    backend, _ = self.make_scanner({"LON": "ABC123"}, {"LOFF"})
    barcode = await backend.scan_barcode()
    assert barcode.data == "ABC123"

  async def test_loff_failure_does_not_mask_scan_error(self) -> None:
    backend, _ = self.make_scanner({"LON": "NG"}, {"LOFF"})
    with pytest.raises(BarcodeScannerError):
      await backend.scan_barcode()

  async def test_cancelled_scan_completes_beam_cleanup(self) -> None:
    commands: list[str] = []
    lon_started = anyio.Event()

    class CancellingScanner(KeyenceBarcodeScannerBackend):
      async def send_command(self, command: str) -> str:
        if command == "LON":
          commands.append("LON")
          lon_started.set()
          await anyio.sleep_forever()
        commands.append("LOFF.started")
        await anyio.lowlevel.checkpoint()
        commands.append("LOFF.completed")
        return ""

    backend = CancellingScanner(port="COM1")

    async def scan() -> None:
      try:
        await backend.scan_barcode()
      except anyio.get_cancelled_exc_class():
        commands.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(scan)
      await lon_started.wait()
      task_group.cancel_scope.cancel()

    assert commands == ["LON", "LOFF.started", "LOFF.completed", "cancelled"]
