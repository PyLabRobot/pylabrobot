from typing import Any, Awaitable, Callable, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.generic import SerialBarcodeScanner
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase

ReadEffect = Union[bytes, Callable[[], Awaitable[bytes]]]


class FakeSerialIO(HasLifespan):
  """Record serial traffic and lifecycle events without opening hardware."""

  def __init__(
    self,
    chunks: List[ReadEffect],
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.chunks = chunks
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.writes: List[bytes] = []
    self.port = "COM_TEST"
    self.timeout: float = 1
    self.reset_input_buffer_called = False

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._exit_serial)
    if self.entry_error is not None:
      raise self.entry_error

  async def _exit_serial(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    if len(self.chunks) == 0:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    effect = self.chunks.pop(0)
    if callable(effect):
      return await effect()
    return effect

  async def write(self, data: bytes) -> None:
    self.writes.append(data)

  async def reset_input_buffer(self) -> None:
    self.reset_input_buffer_called = True

  def get_read_timeout(self) -> float:
    return self.timeout

  def set_read_timeout(self, timeout: float) -> None:
    self.timeout = timeout

  def temporary_timeout(self, timeout: float):
    fake = self

    class TemporaryTimeout:
      def __enter__(self):
        self.original_timeout = fake.timeout
        fake.timeout = timeout

      def __exit__(self, exc_type, exc_value, traceback):
        fake.timeout = self.original_timeout

    return TemporaryTimeout()


def make_scanner(
  chunks: List[ReadEffect],
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
) -> tuple[SerialBarcodeScanner, FakeSerialIO]:
  scanner = SerialBarcodeScanner(port="COM_TEST")
  fake_io = FakeSerialIO(chunks, events, entry_error, exit_error)
  scanner.io = cast(Serial, fake_io)
  return scanner, fake_io


class TestSerialBarcodeScanner(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert SerialBarcodeScanner.__aenter__ is HasLifespan.__aenter__
    assert SerialBarcodeScanner.__aexit__ is HasLifespan.__aexit__
    assert SerialBarcodeScanner.setup is HasLifespan.setup
    assert SerialBarcodeScanner.stop is HasLifespan.stop

  async def test_entry_and_normal_exit_order(self) -> None:
    events: List[str] = []
    scanner, _ = make_scanner([], events)

    async with scanner:
      events.append("body")

    assert events == ["serial.enter", "body", "serial.exit"]
    assert not scanner.is_active

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    scanner, _ = make_scanner([], events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with scanner:
        pytest.fail("failed entry reached the body")

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not scanner.is_active

  async def test_body_cancellation_closes_serial(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    scanner, _ = make_scanner([], events)

    async def run_scanner() -> None:
      try:
        async with scanner:
          events.append("body")
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_scanner)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "body", "serial.exit", "cancelled"]
    assert not scanner.is_active

  async def test_sequential_reuse_enters_and_exits_serial_twice(self) -> None:
    events: List[str] = []
    scanner, _ = make_scanner([], events)

    async with scanner:
      pass
    async with scanner:
      pass

    assert events == ["serial.enter", "serial.exit", "serial.enter", "serial.exit"]
    assert not scanner.is_active

  async def test_serial_exit_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial exit failed")
    scanner, _ = make_scanner([], events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with scanner:
        pass

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not scanner.is_active

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    scanner, _ = make_scanner([], events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with scanner.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []

  async def test_read_line_carriage_return(self) -> None:
    scanner, _ = make_scanner([b"1", b"2", b"3", b"\r"])
    assert await scanner.read_line(timeout=1) == "123"

  async def test_read_line_newline(self) -> None:
    scanner, _ = make_scanner([b"A", b"B", b"C", b"\n"])
    assert await scanner.read_line(timeout=1) == "ABC"

  async def test_read_line_timeout_before_data(self) -> None:
    scanner, _ = make_scanner([])
    assert await scanner.read_line(timeout=0) == ""

  async def test_partial_line_is_returned_at_reply_deadline(self) -> None:
    """A stalled serial read keeps accumulated barcode bytes at the caller deadline."""
    scanner, _ = make_scanner([b"A"])
    with anyio.fail_after(1):
      assert await scanner.read_line(timeout=0.01) == "A"

  async def test_read_line_rejects_negative_timeout(self) -> None:
    scanner, _ = make_scanner([])
    with pytest.raises(ValueError):
      await scanner.read_line(timeout=-1)

  async def test_reset_input_buffer(self) -> None:
    scanner, fake_io = make_scanner([])
    await scanner.reset_input_buffer()
    assert fake_io.reset_input_buffer_called

  def test_rejects_empty_terminators(self) -> None:
    with pytest.raises(ValueError):
      SerialBarcodeScanner(port="COM_TEST", terminators=[])

  def test_rejects_multi_byte_terminators(self) -> None:
    with pytest.raises(ValueError):
      SerialBarcodeScanner(port="COM_TEST", terminators=[b"\r\n"])

  def test_rejects_non_positive_max_line_length(self) -> None:
    with pytest.raises(ValueError):
      SerialBarcodeScanner(port="COM_TEST", max_line_length=0)

  async def test_scan_barcode(self) -> None:
    scanner, _ = make_scanner([b"2", b"2", b"6", b"\r"])

    barcode = await scanner.scan_barcode(
      read_time=1,
      symbology="Code 128 (Subset B and C)",
      position_on_resource="right",
    )

    assert barcode is not None
    assert barcode.data == "226"
    assert barcode.symbology == "Code 128 (Subset B and C)"
    assert barcode.position_on_resource == "right"

  async def test_scan_barcode_returns_none_on_timeout(self) -> None:
    scanner, _ = make_scanner([])
    assert await scanner.scan_barcode(read_time=0) is None

  async def test_scan_barcode_with_trigger_command(self) -> None:
    scanner, fake_io = make_scanner([b"1", b"2", b"3", b"\r"])
    scanner.trigger_command = b"TRIGGER\r"
    scanner.untrigger_command = b"UNTRIGGER\r"

    barcode = await scanner.scan_barcode(read_time=1)

    assert barcode is not None
    assert barcode.data == "123"
    assert fake_io.writes == [b"TRIGGER\r", b"UNTRIGGER\r"]

  async def test_cancelled_scan_completes_untrigger_write(self) -> None:
    events: List[str] = []
    read_started = anyio.Event()

    async def block_read() -> bytes:
      read_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    class CheckpointingSerialIO(FakeSerialIO):
      async def write(self, data: bytes) -> None:
        events.append(f"{data.decode('ascii')}.started")
        await anyio.lowlevel.checkpoint()
        events.append(f"{data.decode('ascii')}.completed")
        await super().write(data)

    class CallbackRecordingScanner(SerialBarcodeScanner):
      async def _untrigger_scanner(self) -> None:
        events.append("untrigger.callback")
        await super()._untrigger_scanner()

    scanner = CallbackRecordingScanner(
      port="COM_TEST",
      trigger_command=b"TRIGGER",
      untrigger_command=b"UNTRIGGER",
    )
    scanner.io = cast(Serial, CheckpointingSerialIO([block_read]))

    async def scan() -> None:
      try:
        await scanner.scan_barcode()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(scan)
      await read_started.wait()
      task_group.cancel_scope.cancel()

    assert events == [
      "TRIGGER.started",
      "TRIGGER.completed",
      "untrigger.callback",
      "UNTRIGGER.started",
      "UNTRIGGER.completed",
      "cancelled",
    ]

  async def test_scan_barcode_rejects_negative_read_time(self) -> None:
    scanner, _ = make_scanner([])
    with pytest.raises(ValueError):
      await scanner.scan_barcode(read_time=-1)

  async def test_scoped_scan(self) -> None:
    events: List[str] = []
    scanner, fake_io = make_scanner([b"X", b"Y", b"Z", b"\r"], events)

    async with scanner:
      barcode = await scanner.scan_barcode(read_time=1, symbology="Code 39")

    assert barcode is not None
    assert barcode.data == "XYZ"
    assert barcode.symbology == "Code 39"
    assert barcode.position_on_resource == "bottom"
    assert events == ["serial.enter", "serial.exit"]
    assert not fake_io.is_active
