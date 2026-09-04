import sys
from typing import Any, Optional, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.hamilton.protocol.text.router import ReplyRouter, USBReplyReader
from pylabrobot.io.boundary import BoundaryRead
from pylabrobot.hamilton.transport.usb import usb as hamilton_usb_module
from pylabrobot.hamilton.transport.usb.usb import HamiltonUSBDriver
from pylabrobot.io.usb import USB
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup


class FirmwareProtocolError(Exception):
  """Represent a firmware-reported failure in the test protocol."""


class RecordingUSB(MockIO):
  """Provide controllable USB replies and record transport lifecycle ordering."""

  def __init__(
    self,
    events: list[str],
    *,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    """Initialize a blocked reply stream and optional lifecycle failures."""
    super().__init__()
    self.events = events
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.active = False
    self.read_started = anyio.Event()
    self._reply_send, self._reply_receive = anyio.create_memory_object_stream[
      bytes | BaseException
    ](float("inf"))
    self.read.side_effect = self._read_reply

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    """Record transport entry and register its observable cleanup."""
    assert kwargs == {}
    self.events.append("transport.enter")
    if self.entry_error is not None:
      raise self.entry_error
    self.active = True
    stack.push_shielded_async_callback(self._exit_transport)

  async def _exit_transport(self) -> None:
    """Record transport exit and expose configured cleanup failure."""
    self.active = False
    self.events.append("transport.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def _read_reply(self, size: int) -> bytes:
    """Read one test-provided reply while proving the transport remains active."""
    assert size == 64
    assert self.active, "reader used the transport after exit"
    self.events.append("reader.read")
    self.read_started.set()
    reply = await self._reply_receive.receive()
    if isinstance(reply, BaseException):
      raise reply
    return reply

  async def read_to_boundary(
    self, *, boundary: str, consume_immediate_boundary: bool = True
  ) -> BoundaryRead:
    """Collect one USB reply through a physical short packet or ZLP."""
    if boundary == "packet" and not consume_immediate_boundary:
      return BoundaryRead(b"", True, True, False)
    assert boundary == "short_packet_end"
    data = bytearray()
    while True:
      packet = await self._read_reply(64)
      data.extend(packet)
      if len(packet) < 64:
        return BoundaryRead(bytes(data), True, True, False)

  async def publish(self, reply: str | bytes) -> None:
    """Make a reply available to the driver's reader."""
    if isinstance(reply, str):
      reply = reply.encode()
    await self._reply_send.send(reply)

  async def fail_reader(self, error: BaseException) -> None:
    """Raise a fatal error from the next transport read."""
    await self._reply_send.send(error)


class RecordingReplyRouter(ReplyRouter):
  """Record reader ownership while preserving real reply routing behavior."""

  def __init__(
    self,
    io: RecordingUSB,
    driver: "ConcreteHamiltonUSBDriver",
    events: list[str],
    *,
    start_error: Optional[BaseException] = None,
    stop_error: Optional[BaseException] = None,
  ) -> None:
    """Initialize the real router with optional start and stop failures."""
    super().__init__(
      io=io,
      module_id_length=driver.module_id_length,
      parse_id=driver.get_id_from_fw_response,
      raise_for_error=driver.check_fw_string_error,
      packet_read_timeout=0.01,
      read_timeout=0.1,
      reader=USBReplyReader(cast(USB, io)),
    )
    self.events = events
    self.start_error = start_error
    self.stop_error = stop_error
    self.run_count = 0
    self.stop_count = 0

  async def _run(self, reader_scope: anyio.CancelScope) -> None:
    """Record each reader task and delegate routing to the production implementation."""
    self.run_count += 1
    self.events.append("reader.start")
    if self.start_error is not None:
      raise self.start_error
    try:
      await super()._run(reader_scope)
    finally:
      self.events.append("reader.exit")

  def _stop(self, reader_scope: anyio.CancelScope) -> None:
    """Record reader shutdown before delegating to the production implementation."""
    self.stop_count += 1
    self.events.append("reader.stop")
    super()._stop(reader_scope)
    if self.stop_error is not None:
      raise self.stop_error


class ConcreteHamiltonUSBDriver(HamiltonUSBDriver):
  """Supply the protocol hooks needed to exercise the top-level USB driver."""

  def __init__(
    self,
    io: RecordingUSB,
    events: list[str],
    *,
    start_error: Optional[BaseException] = None,
    stop_error: Optional[BaseException] = None,
    firmware_error: Optional[Exception] = None,
    replace_router: bool = True,
  ) -> None:
    """Construct the driver and replace hardware USB with a typed test transport."""
    super().__init__(
      id_product=0x1234,
      packet_read_timeout=0.01,  # type: ignore[arg-type]
      read_timeout=0.1,  # type: ignore[arg-type]
    )
    self.io = cast(USB, io)
    if replace_router:
      self._reply_router = RecordingReplyRouter(
        io,
        self,
        events,
        start_error=start_error,
        stop_error=stop_error,
      )
    self.firmware_error = firmware_error

  @property
  def module_id_length(self) -> int:
    """Use two-character module identifiers in the test protocol."""
    return 2

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Parse a four-digit identifier from a test reply."""
    marker = resp.find("id")
    if marker == -1:
      return None
    value = resp[marker + 2 : marker + 6]
    if not value.isdigit():
      raise ValueError("malformed test identifier")
    return int(value)

  def check_fw_string_error(self, resp: str) -> None:
    """Raise the test firmware exception for error replies."""
    if resp.endswith("ER"):
      if self.firmware_error is not None:
        raise self.firmware_error
      raise FirmwareProtocolError("firmware rejected command")

  def _parse_response(self, resp: str, fmt: Any) -> dict:
    """Return an observable parsed response without changing the raw event response."""
    return {"response": resp, "format": fmt}


class PostEntryFailureDriver(ConcreteHamiltonUSBDriver):
  """Fail after base entry to verify partial subclass entry rollback."""

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    """Enter all base resources, then fail before body access."""
    await super()._enter_lifespan(stack, **kwargs)
    raise RuntimeError("subclass post-entry failed")


def make_driver(
  *,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  start_error: Optional[BaseException] = None,
  stop_error: Optional[BaseException] = None,
  firmware_error: Optional[Exception] = None,
  driver_type: type[ConcreteHamiltonUSBDriver] = ConcreteHamiltonUSBDriver,
) -> tuple[ConcreteHamiltonUSBDriver, RecordingUSB, RecordingReplyRouter, list[str]]:
  """Build one software-only driver with controllable lifecycle failures."""
  events: list[str] = []
  io = RecordingUSB(events, entry_error=entry_error, exit_error=exit_error)
  driver = driver_type(
    io,
    events,
    start_error=start_error,
    stop_error=stop_error,
    firmware_error=firmware_error,
  )
  return driver, io, cast(RecordingReplyRouter, driver._reply_router), events


def exception_messages(error: BaseException) -> list[str]:
  """Collect messages from exception contexts and nested exception groups."""
  messages: list[str] = []
  seen: set[int] = set()

  def collect(current: Optional[BaseException]) -> None:
    while current is not None and id(current) not in seen:
      seen.add(id(current))
      messages.append(str(current))
      if isinstance(current, BaseExceptionGroup):
        for nested in current.exceptions:
          collect(nested)
      current = current.__context__

  collect(error)
  return messages


async def wait_until(predicate: Any) -> None:
  """Yield until an in-memory test condition becomes true."""
  with anyio.fail_after(1):
    while not predicate():
      await anyio.lowlevel.checkpoint()


class TestHamiltonUSBLifecycle(AnyioTestBase):
  async def test_uses_native_async_resource_dispatch_and_exact_lifo_order(self) -> None:
    driver, io, router, events = make_driver()

    assert isinstance(driver, HasLifespan)
    async with driver:
      await io.read_started.wait()
      events.append("body")

    assert events == [
      "transport.enter",
      "reader.start",
      "reader.read",
      "body",
      "reader.stop",
      "reader.exit",
      "transport.exit",
    ]
    assert router.run_count == 1
    assert router.stop_count == 1
    assert not driver.is_active

  async def test_transport_entry_failure_starts_no_reader_or_cleanup(self) -> None:
    driver, _, router, events = make_driver(entry_error=RuntimeError("USB open failed"))

    with pytest.raises(RuntimeError, match="USB open failed"):
      async with driver:
        pytest.fail("failed entry must not reach the body")

    assert events == ["transport.enter"]
    assert router.run_count == 0
    assert router.stop_count == 0

  async def test_reader_failure_stops_router_before_transport_exit(
    self,
  ) -> None:
    driver, _, router, events = make_driver(start_error=RuntimeError("reader start failed"))

    with pytest.raises(BaseException) as error_info:
      async with driver:
        await anyio.sleep_forever()

    assert "reader start failed" in exception_messages(error_info.value)
    assert events == ["transport.enter", "reader.start", "reader.stop", "transport.exit"]
    assert router.run_count == 1
    assert router.stop_count == 1

  async def test_lifespan_binds_router_to_replaced_public_io(
    self,
    monkeypatch: pytest.MonkeyPatch,
  ) -> None:
    original_io = MockIO()
    original_io.write.side_effect = RuntimeError("stale transport used")
    replacement_events: list[str] = []
    replacement_io = RecordingUSB(replacement_events)
    monkeypatch.setattr(
      hamilton_usb_module,
      "USB",
      lambda **kwargs: cast(USB, original_io),
    )
    driver = ConcreteHamiltonUSBDriver(
      replacement_io,
      replacement_events,
      replace_router=False,
    )
    original_router = driver._reply_router

    async with driver:
      assert await driver.send_command("C0", "QM", wait=False) is None

    assert driver._reply_router is original_router
    assert original_router.io is replacement_io
    assert replacement_io.write.calls == [((b"C0QMid0001",), {"timeout": None})]
    assert original_io.write.calls == []

  async def test_subclass_post_entry_failure_unwinds_reader_before_transport(self) -> None:
    driver, _, router, events = make_driver(driver_type=PostEntryFailureDriver)

    with pytest.raises(BaseException) as error_info:
      async with driver:
        pytest.fail("failed entry must not reach the body")

    assert "subclass post-entry failed" in exception_messages(error_info.value)
    assert events[0] == "transport.enter"
    assert events.index("reader.stop") < events.index("transport.exit")
    assert router.stop_count == 1

  async def test_body_cancellation_stops_reader_before_transport_exit(self) -> None:
    driver, io, router, events = make_driver()
    body_started = anyio.Event()

    async def run_driver() -> None:
      try:
        async with driver:
          await io.read_started.wait()
          events.append("body")
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_driver)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-2:] == ["transport.exit", "cancelled"]
    assert events.index("reader.exit") < events.index("transport.exit")
    assert router.run_count == 1
    assert router.stop_count == 1

  async def test_reader_failure_propagates_and_transport_still_exits(self) -> None:
    driver, io, router, events = make_driver()
    reader_error = RuntimeError("USB disconnected")

    with pytest.raises(BaseException) as error_info:
      async with driver:
        await io.read_started.wait()
        await io.fail_reader(reader_error)
        await anyio.sleep_forever()

    assert "USB disconnected" in exception_messages(error_info.value)
    assert (
      events.index("reader.exit") < events.index("reader.stop") < events.index("transport.exit")
    )
    assert router.run_count == 1
    assert router.stop_count == 1

  async def test_cleanup_failures_remain_chained_and_all_cleanup_runs(self) -> None:
    driver, _, _, events = make_driver(
      stop_error=RuntimeError("reader stop failed"),
      exit_error=RuntimeError("USB close failed"),
    )

    with pytest.raises(RuntimeError, match="USB close failed") as error_info:
      async with driver:
        pass

    messages = exception_messages(error_info.value)
    assert "reader stop failed" in messages
    assert "USB close failed" in messages
    assert (
      events.index("reader.stop") < events.index("reader.exit") < events.index("transport.exit")
    )

  async def test_sequential_reuse_runs_one_reader_per_lifespan(self) -> None:
    driver, io, router, events = make_driver()

    async with driver:
      await io.read_started.wait()
    first_read_count = events.count("reader.read")
    io.read_started = anyio.Event()
    async with driver:
      await io.read_started.wait()
      assert events.count("reader.read") > first_read_count

    cycle = [
      "transport.enter",
      "reader.start",
      "reader.read",
      "reader.stop",
      "reader.exit",
      "transport.exit",
    ]
    assert events == cycle + cycle
    assert router.run_count == 2
    assert router.stop_count == 2

  async def test_commands_outside_active_lifespan_are_rejected_without_io(self) -> None:
    driver, io, router, events = make_driver()

    with pytest.raises(RuntimeError, match="active lifespan"):
      await driver.send_command("C0", "QM", wait=False)
    with pytest.raises(RuntimeError, match="active lifespan"):
      await driver.send_raw_command("C0QMid0042", wait=False)

    assert io.write.calls == []
    assert router.run_count == 0
    assert events == []

  async def test_raw_command_delegates_reply_routing(self) -> None:
    driver, io, _, _ = make_driver()

    async with driver:
      await io.publish("C0QMid0042OK")
      response = await driver.send_raw_command("C0QMid0042")

    assert response == "C0QMid0042OK"
    assert io.write.calls == [((b"C0QMid0042",), {"timeout": None})]

  async def test_raw_command_timeout_preserves_legacy_message(self) -> None:
    driver, _, _, _ = make_driver()

    async with driver:
      with pytest.raises(
        TimeoutError,
        match=r"Timeout while waiting for response to command C0QMid0042\.",
      ):
        await driver.send_raw_command("C0QMid0042", read_timeout=0)


class TestHamiltonUSBEvents(AnyioTestBase):
  def capture_events(self) -> tuple[EventBus, list[PLREvent]]:
    """Create an isolated event bus and event list for one command."""
    events: list[PLREvent] = []
    bus = EventBus()
    bus.subscribe(events.append)
    return bus, events

  def assert_started_data(self, event: PLREvent) -> None:
    """Assert the stable command identity attached to every event outcome."""
    assert event.data == {
      "transport": "hamilton_usb",
      "driver": "ConcreteHamiltonUSBDriver",
      "module": "C0",
      "command": "QM",
      "command_id": 1,
      "raw_command": "C0QMid0001aa7",
    }

  async def test_success_emits_started_then_completed_with_raw_response(self) -> None:
    driver, io, _, _ = make_driver()
    bus, events = self.capture_events()

    async with driver:
      await io.publish("C0QMid0001OK")
      with use_event_bus(bus):
        result = await driver.send_command("C0", "QM", aa=7, fmt="parsed")

    assert result == {"response": "C0QMid0001OK", "format": "parsed"}
    assert [event.name for event in events] == [
      "firmware.command.started",
      "firmware.command.completed",
    ]
    self.assert_started_data(events[0])
    assert events[1].data == {**events[0].data, "response": "C0QMid0001OK"}

  async def test_firmware_error_emits_started_then_failed(self) -> None:
    driver, io, _, _ = make_driver()
    bus, events = self.capture_events()

    async with driver:
      await io.publish("C0QMid0001ER")
      with (
        use_event_bus(bus),
        pytest.raises(FirmwareProtocolError, match="firmware rejected command"),
      ):
        await driver.send_command("C0", "QM", aa=7)

    assert [event.name for event in events] == [
      "firmware.command.started",
      "firmware.command.failed",
    ]
    self.assert_started_data(events[0])
    assert events[1].data == {
      **events[0].data,
      "error_type": "FirmwareProtocolError",
      "error_message": "firmware rejected command",
    }

  async def test_timeout_emits_legacy_error_type_and_message(self) -> None:
    driver, _, _, _ = make_driver()
    bus, events = self.capture_events()

    async with driver:
      with (
        use_event_bus(bus),
        pytest.raises(
          TimeoutError,
          match=r"Timeout while waiting for response to command C0QMid0001aa7\.",
        ),
      ):
        await driver.send_command("C0", "QM", aa=7, read_timeout=0)

    assert [event.name for event in events] == [
      "firmware.command.started",
      "firmware.command.failed",
    ]
    self.assert_started_data(events[0])
    assert events[1].data == {
      **events[0].data,
      "error_type": "TimeoutError",
      "error_message": "Timeout while waiting for response to command C0QMid0001aa7.",
    }

  async def test_cancellation_emits_started_then_failed_with_same_error(self) -> None:
    driver, _, router, _ = make_driver()
    bus, events = self.capture_events()
    caught: list[BaseException] = []

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          with use_event_bus(bus):
            await driver.send_command("C0", "QM", aa=7)
        except BaseException as error:
          caught.append(error)
          raise

    scopes: list[anyio.CancelScope] = []
    async with driver:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(send)
        await wait_until(lambda: 1 in router._waiting_tasks_by_id)
        scopes[0].cancel()

    assert len(caught) == 1
    assert isinstance(caught[0], anyio.get_cancelled_exc_class())
    assert [event.name for event in events] == [
      "firmware.command.started",
      "firmware.command.failed",
    ]
    self.assert_started_data(events[0])
    assert events[1].data == {
      **events[0].data,
      "error_type": type(caught[0]).__name__,
      "error_message": str(caught[0]),
    }

  async def test_write_failure_emits_started_then_failed(self) -> None:
    driver, io, _, _ = make_driver()
    bus, events = self.capture_events()
    io.write.side_effect = RuntimeError("USB write failed")

    async with driver:
      with use_event_bus(bus), pytest.raises(RuntimeError, match="USB write failed"):
        await driver.send_command("C0", "QM", aa=7)

    assert [event.name for event in events] == [
      "firmware.command.started",
      "firmware.command.failed",
    ]
    self.assert_started_data(events[0])
    assert events[1].data == {
      **events[0].data,
      "error_type": "RuntimeError",
      "error_message": "USB write failed",
    }

  @pytest.mark.parametrize("command_path", ["assembled", "raw"])
  @pytest.mark.parametrize("failure_source", ["write", "firmware"])
  async def test_empty_timeout_from_dependency_preserves_exact_instance(
    self,
    command_path: str,
    failure_source: str,
  ) -> None:
    dependency_error = TimeoutError()
    driver, io, _, _ = make_driver(
      firmware_error=dependency_error if failure_source == "firmware" else None
    )
    bus, events = self.capture_events()

    if failure_source == "write":
      io.write.side_effect = dependency_error
    else:

      async def publish_error_reply(data: bytes, *, timeout: Optional[int]) -> None:
        del data, timeout
        command_id = "0001" if command_path == "assembled" else "0042"
        await io.publish(f"C0QMid{command_id}ER")

      io.write.side_effect = publish_error_reply

    async with driver:
      with use_event_bus(bus), pytest.raises(TimeoutError) as error_info:
        if command_path == "assembled":
          await driver.send_command("C0", "QM", aa=7)
        else:
          await driver.send_raw_command("C0QMid0042")

    assert error_info.value is dependency_error
    if command_path == "assembled":
      assert [event.name for event in events] == [
        "firmware.command.started",
        "firmware.command.failed",
      ]
      assert events[1].data == {
        **events[0].data,
        "error_type": "TimeoutError",
        "error_message": "",
      }
    else:
      assert events == []
