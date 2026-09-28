import sys
from typing import Any, Awaitable, Callable, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.keyence.barcode_scanner import (
  HAS_SERIAL,
  KeyenceBarcodeScanner,
  KeyenceBarcodeScannerError,
)
from pylabrobot.resources.barcode import Barcode
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup

ReadEffect = Union[bytes, BaseException]
ReadEffects = Union[list[ReadEffect], Callable[..., Union[bytes, Awaitable[bytes]]]]


async def raise_operation_cancellation() -> None:
  """Raise a cancellation created within the current operation's shield."""
  cancellation: Optional[BaseException] = None
  with anyio.CancelScope() as scope:
    scope.cancel()
    try:
      await anyio.sleep_forever()
    except anyio.get_cancelled_exc_class() as error:
      cancellation = error
  if cancellation is None:
    raise AssertionError("inner cancel scope did not raise cancellation")
  raise cancellation


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
    self.port = "COM1"

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


def make_scanner(
  events: list[str],
  reads: ReadEffects,
  exit_error: Optional[BaseException] = None,
) -> tuple[KeyenceBarcodeScanner, RecordingSerial]:
  scanner = KeyenceBarcodeScanner(port="COM1")
  scanner.reply_gap = 0.01
  serial = RecordingSerial(events, reads, exit_error)
  scanner.io = cast(Serial, serial)
  return scanner, serial


def test_pyserial_is_available() -> None:
  assert HAS_SERIAL


class TestKeyenceSendCommand(AnyioTestBase):
  async def test_assembles_terminated_reply_across_reads(self) -> None:
    """A serial reply split into bytes is one barcode-scanner response."""
    scanner, _ = make_scanner([], reads=[b"O", b"K", b"\r"])
    assert await scanner.send_command("LOFF") == "OK"

  async def test_writes_carriage_return_terminated_command(self) -> None:
    scanner, serial = make_scanner([], reads=[b"\r"])
    await scanner.send_command("RMOTOR")
    assert serial.write.calls == [((b"RMOTOR\r",), {})]

  async def test_reads_reply_once_per_command(self) -> None:
    scanner, serial = make_scanner([], reads=[b"MOTORON\r", b"unused"])
    assert await scanner.send_command("RMOTOR") == "MOTORON"
    assert len(serial.read.calls) == 1

  async def test_empty_read_is_an_empty_reply(self) -> None:
    async def idle() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    scanner, _ = make_scanner([], reads=idle)
    assert await scanner.send_command("LON") == ""


class TestKeyenceLifespan(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert KeyenceBarcodeScanner.__aenter__ is HasLifespan.__aenter__
    assert KeyenceBarcodeScanner.__aexit__ is HasLifespan.__aexit__
    assert KeyenceBarcodeScanner.setup is HasLifespan.setup
    assert KeyenceBarcodeScanner.stop is HasLifespan.stop

  async def test_entry_and_normal_exit_order(self) -> None:
    events: list[str] = []
    scanner, _ = make_scanner(events, reads=[b"MOTORON\r"])
    async with scanner:
      events.append("body")
    assert events == ["serial.enter", "write:RMOTOR", "body", "serial.exit"]

  async def test_motor_off_rolls_back_serial(self) -> None:
    events: list[str] = []
    scanner, _ = make_scanner(events, reads=[b"MOTOROFF\r"])
    with pytest.raises(KeyenceBarcodeScannerError, match="Motor is off"):
      async with scanner:
        pytest.fail("failed entry must not reach the body")
    assert events[-1] == "serial.exit"

  async def test_initialization_timeout_rolls_back_serial(self) -> None:
    events: list[str] = []
    scanner, _ = make_scanner(events, reads=lambda: b"")
    scanner.init_timeout = 0.01
    scanner.poll_interval = 0

    with pytest.raises(KeyenceBarcodeScannerError, match="Timeout waiting") as error_info:
      async with scanner:
        pytest.fail("failed entry must not reach the body")

    assert isinstance(error_info.value.__cause__, TimeoutError)
    assert events[-1] == "serial.exit"

  async def test_polls_until_motor_is_on(self) -> None:
    events: list[str] = []
    scanner, serial = make_scanner(events, reads=[b"WAIT\r", b"MOTORON\r"])
    scanner.poll_interval = 0

    async with scanner:
      pass

    assert serial.write.calls == [((b"RMOTOR\r",), {}), ((b"RMOTOR\r",), {})]

  async def test_cancellation_during_entry_rolls_back_serial(self) -> None:
    events: list[str] = []
    read_started = anyio.Event()

    async def block_read() -> bytes:
      read_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    scanner, _ = make_scanner(events, reads=block_read)

    async def run_scanner() -> None:
      try:
        async with scanner:
          pytest.fail("cancelled entry must not reach the body")
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_scanner)
      await read_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "write:RMOTOR", "serial.exit", "cancelled"]
    assert not scanner.is_active

  async def test_cancellation_from_body_closes_serial(self) -> None:
    events: list[str] = []
    body_started = anyio.Event()
    scanner, _ = make_scanner(events, reads=[b"MOTORON\r"])

    async def run_scanner() -> None:
      try:
        async with scanner:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_scanner)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "write:RMOTOR", "serial.exit", "cancelled"]
    assert not scanner.is_active

  async def test_sequential_reuse_enters_and_exits_serial_twice(self) -> None:
    events: list[str] = []
    scanner, serial = make_scanner(events, reads=[b"MOTORON\r", b"MOTORON\r"])

    async with scanner:
      pass
    async with scanner:
      pass

    assert [event for event in events if event.startswith("serial.")] == [
      "serial.enter",
      "serial.exit",
      "serial.enter",
      "serial.exit",
    ]
    assert serial.write.calls == [((b"RMOTOR\r",), {}), ((b"RMOTOR\r",), {})]

  async def test_serial_exit_failure_propagates_without_extra_command(self) -> None:
    events: list[str] = []
    scanner, serial = make_scanner(
      events,
      reads=[b"MOTORON\r"],
      exit_error=RuntimeError("serial close failed"),
    )

    with pytest.raises(RuntimeError, match="serial close failed"):
      async with scanner:
        pass

    assert not scanner.is_active
    assert serial.write.calls == [((b"RMOTOR\r",), {})]

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: list[str] = []
    scanner, _ = make_scanner(events, reads=[])
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with scanner.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")
    assert events == []


class TestKeyenceScanBarcode(AnyioTestBase):
  async def test_success_returns_barcode_and_releases_beam(self) -> None:
    scanner, serial = make_scanner([], reads=[b"ABC123\r", b"OK\r"])
    barcode = await scanner.scan_barcode(read_time=-1)
    assert isinstance(barcode, Barcode)
    assert barcode.data == "ABC123"
    assert barcode.symbology == "unknown"
    assert barcode.position_on_resource == "front"
    assert serial.write.calls == [((b"LON\r",), {}), ((b"LOFF\r",), {})]

  @pytest.mark.parametrize("reply", [b"NG\r", b"NG123\r"])
  async def test_reader_off_releases_beam(self, reply: bytes) -> None:
    scanner, serial = make_scanner([], reads=[reply, b"OK\r"])
    with pytest.raises(KeyenceBarcodeScannerError, match="reader is off"):
      await scanner.scan_barcode()
    assert serial.write.calls == [((b"LON\r",), {}), ((b"LOFF\r",), {})]

  async def test_error_response_releases_beam(self) -> None:
    scanner, serial = make_scanner([], reads=[b"ERR99 detail\r", b"OK\r"])
    with pytest.raises(KeyenceBarcodeScannerError, match="ERR99 detail"):
      await scanner.scan_barcode()
    assert serial.write.calls == [((b"LON\r",), {}), ((b"LOFF\r",), {})]

  async def test_failed_lon_releases_beam(self) -> None:
    scanner, serial = make_scanner([], reads=[RuntimeError("LON failed"), b"OK\r"])
    with pytest.raises(RuntimeError, match="LON failed"):
      await scanner.scan_barcode()
    assert serial.write.calls == [((b"LON\r",), {}), ((b"LOFF\r",), {})]

  async def test_loff_failure_is_logged_without_masking_success(self, caplog) -> None:
    scanner, _ = make_scanner([], reads=[b"ABC123\r", RuntimeError("LOFF failed")])
    with caplog.at_level("WARNING"):
      barcode = await scanner.scan_barcode()
    assert barcode is not None
    assert barcode.data == "ABC123"
    assert "Failed to turn off barcode reader beam" in caplog.text

  async def test_loff_failure_is_logged_without_masking_scan_error(self, caplog) -> None:
    scanner, _ = make_scanner([], reads=[b"NG\r", RuntimeError("LOFF failed")])
    with caplog.at_level("WARNING"):
      with pytest.raises(KeyenceBarcodeScannerError, match="reader is off"):
        await scanner.scan_barcode()
    assert "Failed to turn off barcode reader beam" in caplog.text

  async def test_cancelled_lon_completes_beam_cleanup(self) -> None:
    events: list[str] = []
    lon_started = anyio.Event()

    async def read() -> bytes:
      if not lon_started.is_set():
        lon_started.set()
        await anyio.sleep_forever()
      events.append("LOFF.started")
      await anyio.lowlevel.checkpoint()
      events.append("LOFF.completed")
      return b"OK\r"

    scanner, _ = make_scanner(events, reads=read)

    async def scan() -> None:
      try:
        await scanner.scan_barcode()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(scan)
      await lon_started.wait()
      task_group.cancel_scope.cancel()

    assert events == [
      "write:LON",
      "write:LOFF",
      "LOFF.started",
      "LOFF.completed",
      "cancelled",
    ]

  async def test_loff_operation_cancellation_after_success_is_logged_and_propagated(
    self, caplog
  ) -> None:
    read_count = 0

    async def read() -> bytes:
      nonlocal read_count
      read_count += 1
      if read_count == 1:
        return b"ABC123\r"
      await raise_operation_cancellation()
      raise AssertionError("unreachable")

    scanner, _ = make_scanner([], reads=read)
    with caplog.at_level("WARNING"):
      with pytest.raises(anyio.get_cancelled_exc_class()):
        await scanner.scan_barcode()
    assert "Failed to turn off barcode reader beam" in caplog.text

  async def test_loff_operation_cancellation_preserves_lon_failure(self, caplog) -> None:
    lon_error = RuntimeError("LON failed")
    read_count = 0

    async def read() -> bytes:
      nonlocal read_count
      read_count += 1
      if read_count == 1:
        raise lon_error
      await raise_operation_cancellation()
      raise AssertionError("unreachable")

    scanner, _ = make_scanner([], reads=read)
    with caplog.at_level("WARNING"):
      with pytest.raises(BaseExceptionGroup) as error_info:
        await scanner.scan_barcode()

    assert error_info.value.exceptions[0] is lon_error
    assert isinstance(error_info.value.exceptions[1], anyio.get_cancelled_exc_class())
    assert "Failed to turn off barcode reader beam" in caplog.text

  async def test_non_cancellation_base_exception_from_loff_propagates(self) -> None:
    class BeamAbort(BaseException):
      pass

    failure = BeamAbort("fatal LOFF failure")
    scanner, _ = make_scanner([], reads=[b"ABC123\r", failure])
    with pytest.raises(BeamAbort) as error_info:
      await scanner.scan_barcode()
    assert error_info.value is failure
