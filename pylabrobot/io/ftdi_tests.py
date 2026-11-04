import contextlib
import contextvars
import ctypes
import logging
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Iterator, Optional, Union
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.events import EventBus, use_event_bus
from pylabrobot.io import capture as capture_module
from pylabrobot.io import ftdi as ftdi_module
from pylabrobot.io.capture import CaptureReader, capturer
from pylabrobot.io.ftdi import (
  FTDI,
  FTDICommand,
  FTDIValidator,
  _parse_usb_address,
  is_ftdi_transport_error,
)
from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.testing.concurrency import AnyioTestBase


class _RecordingHandler(logging.Handler):
  """Collect FTDI I/O log records without replacing the logging call."""

  def __init__(self) -> None:
    super().__init__(level=LOG_LEVEL_IO)
    self.records: list[logging.LogRecord] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.records.append(record)


class _Gate:
  """Coordinate one fake blocking device call from the async test."""

  def __init__(self) -> None:
    self.started = threading.Event()
    self.release = threading.Event()
    self.finished = threading.Event()

  def wait(self) -> None:
    self.started.set()
    self.release.wait(timeout=2)


class _FakeFtdiFunctions:
  """Hardware-free stand-in for pylibftdi's low-level function wrapper."""

  def __init__(self, device: "_FakeDevice") -> None:
    self.device = device
    self.calls = device.calls

  def ftdi_setrts(self, level: bool) -> None:
    if self.device.closed:
      self.device.controls_on_closed_device.append("set_rts")
    self.calls.append(("set_rts", level))

  def ftdi_setdtr(self, level: bool) -> None:
    self.calls.append(("set_dtr", level))

  def ftdi_usb_reset(self) -> None:
    self.calls.append("usb_reset")

  def ftdi_set_latency_timer(self, latency: int) -> None:
    self.calls.append(("set_latency_timer", latency))

  def ftdi_set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    self.calls.append(("set_line_property", bits, stopbits, parity))

  def ftdi_setflowctrl(self, flowctrl: int) -> None:
    self.calls.append(("set_flowctrl", flowctrl))

  def ftdi_usb_purge_rx_buffer(self) -> None:
    self.calls.append("usb_purge_rx_buffer")

  def ftdi_usb_purge_tx_buffer(self) -> None:
    self.calls.append("usb_purge_tx_buffer")

  def ftdi_poll_modem_status(self, status: Any) -> None:
    ctypes.cast(status, ctypes.POINTER(ctypes.c_ushort)).contents.value = 7
    self.calls.append("poll_modem_status")


class _FakeDevice:
  """Deterministic fake for the synchronous pylibftdi boundary."""

  def __init__(self, to_read: bytes = b"", text_mode: bool = False) -> None:
    self._to_read = bytearray(to_read)
    self._text_mode = text_mode
    self.read_gate: Optional[_Gate] = None
    self.write_gate: Optional[_Gate] = None
    self.close_gate: Optional[_Gate] = None
    self.written = bytearray()
    self.reads = 0
    self.closed = True
    self.baudrate = 0
    self.calls: list[object] = []
    self.controls_on_closed_device: list[str] = []
    self.ftdi_fn = _FakeFtdiFunctions(self)

  def open(self) -> None:
    self.calls.append("open")
    self.closed = False

  def close(self) -> None:
    self.calls.append("close")
    if self.close_gate is not None:
      self.close_gate.wait()
    self.closed = True

  def read(self, num_bytes: int) -> Union[bytes, str]:
    data = bytes(self._to_read[:num_bytes])
    del self._to_read[:num_bytes]
    self.reads += 1
    self.calls.append(("read", num_bytes))
    if self.read_gate is not None:
      self.read_gate.wait()
      self.read_gate.finished.set()
    return data.decode("latin-1") if self._text_mode else data

  def write(self, data: bytes) -> int:
    self.calls.append(("write", data))
    if self.write_gate is not None:
      self.write_gate.wait()
    self.written.extend(data)
    if self.write_gate is not None:
      self.write_gate.finished.set()
    return len(data)


def _new_ftdi() -> FTDI:
  """Construct an FTDI while replacing only optional dependency availability."""
  with (
    mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
    mock.patch.object(ftdi_module, "HAS_PYUSB", True),
  ):
    io = FTDI(human_readable_device_name="test", device_id="test")
  io._resolve_device_serial = lambda: "test"  # type: ignore[method-assign]
  return io


@contextlib.contextmanager
def _device_factory(device: _FakeDevice) -> Iterator[None]:
  """Install one fake device constructor for a resource lifespan."""
  with mock.patch.object(ftdi_module, "Device", return_value=device, create=True):
    yield


async def _wait_for_thread_event(event: threading.Event) -> None:
  """Wait cooperatively for a worker-thread observation."""
  with anyio.fail_after(2):
    while not event.is_set():
      await anyio.sleep(0)


class TestFTDILifespan(AnyioTestBase):
  async def test_reconnect_replaces_handle_without_exiting_task_group(self) -> None:
    io = _new_ftdi()
    first, second = _FakeDevice(), _FakeDevice(to_read=b"new")
    second_open = second.open

    def open_after_close() -> None:
      assert first.closed
      second_open()

    second.open = open_after_close  # type: ignore[method-assign]
    with mock.patch.object(ftdi_module, "Device", side_effect=[first, second], create=True):
      async with io:
        group = io._tg
        with anyio.CancelScope():
          await io.reconnect()
        assert io._tg is group
        assert first.closed
        assert await io.read(3) == b"new"
      assert second.closed
    assert first.calls == ["open", "close"]

  async def test_failed_reconnect_can_retry_without_using_old_handle(self) -> None:
    io = _new_ftdi()
    first, failed, replacement = _FakeDevice(), _FakeDevice(), _FakeDevice()
    failed.open = mock.Mock(side_effect=OSError("open failed"))  # type: ignore[method-assign]
    with mock.patch.object(
      ftdi_module, "Device", side_effect=[first, failed, replacement], create=True
    ):
      async with io:
        with pytest.raises(OSError, match="open failed"):
          await io.reconnect()
        assert first.closed
        with pytest.raises(RuntimeError, match="not initialized"):
          await io.write(b"stale")
        await io.reconnect()
        assert await io.write(b"fresh") == 5
      assert replacement.closed
    assert first.written == b""
    assert replacement.written == b"fresh"

  @pytest.mark.parametrize("operation", ["read", "write", "control"])
  async def test_hard_error_requires_explicit_reconnect(self, operation: str) -> None:
    io = _new_ftdi()
    first, replacement = _FakeDevice(), _FakeDevice(to_read=b"new")
    if operation == "read":
      first.read = mock.Mock(side_effect=OSError("unplugged"))  # type: ignore[method-assign]
    elif operation == "write":
      first.write = mock.Mock(side_effect=OSError("unplugged"))  # type: ignore[method-assign]
    else:
      first.ftdi_fn.ftdi_set_latency_timer = mock.Mock(  # type: ignore[method-assign]
        side_effect=OSError("unplugged")
      )

    with mock.patch.object(ftdi_module, "Device", side_effect=[first, replacement], create=True):
      async with io:
        with pytest.raises(OSError, match="unplugged"):
          if operation == "read":
            await io.read(1)
          elif operation == "write":
            await io.write(b"old")
          else:
            await io.set_latency_timer(2)
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"stale")
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.set_latency_timer(2)
        await io.reconnect()
        assert await io.read(3) == b"new"

  async def test_reconnect_outside_lifespan_is_rejected(self) -> None:
    with pytest.raises(RuntimeError, match="active lifespan"):
      await _new_ftdi().reconnect()

  async def test_reconnect_waits_for_in_flight_write_before_closing_handle(self) -> None:
    io = _new_ftdi()
    first, second = _FakeDevice(), _FakeDevice()
    gate = _Gate()
    first.write_gate = gate
    with mock.patch.object(ftdi_module, "Device", side_effect=[first, second], create=True):
      async with io:
        async with anyio.create_task_group() as group:
          group.start_soon(io.write, b"old")
          await _wait_for_thread_event(gate.started)
          group.start_soon(io.reconnect)
          await anyio.sleep(0)
          assert not first.closed
          gate.release.set()
        assert first.closed
        assert first.written == b"old"
        assert await io.write(b"new") == 3
    assert second.written == b"new"

  async def test_cancelled_reconnect_closes_new_ftdi_handle_before_retry(self) -> None:
    io = _new_ftdi()
    first, interrupted, replacement = _FakeDevice(), _FakeDevice(), _FakeDevice()
    gate = _Gate()
    interrupted_open = interrupted.open

    def pause_open() -> None:
      gate.wait()
      interrupted_open()

    interrupted.open = pause_open  # type: ignore[method-assign]
    with mock.patch.object(
      ftdi_module, "Device", side_effect=[first, interrupted, replacement], create=True
    ):
      async with io:
        scope = anyio.CancelScope()

        async def cancel_open() -> None:
          with scope:
            await io.reconnect()

        async with anyio.create_task_group() as group:
          group.start_soon(cancel_open)
          await _wait_for_thread_event(gate.started)
          scope.cancel()
          gate.release.set()
        assert first.closed
        assert interrupted.closed
        with pytest.raises(RuntimeError, match="not initialized"):
          await io.write(b"stale")
        await io.reconnect()
        assert io.dev is replacement

  async def test_transport_behavior_is_preserved(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice(to_read=b"\x03\x04ok\n")
    commands: list[FTDICommand] = []
    events: list[Any] = []
    bus = EventBus()
    bus.subscribe(events.append)

    with (
      _device_factory(device),
      mock.patch.object(ftdi_module.capturer, "record", commands.append),
      use_event_bus(bus),
    ):
      async with io:
        assert isinstance(io, HasLifespan)
        await io.set_baudrate(115200)
        await io.set_rts(True)
        await io.set_dtr(False)
        await io.set_latency_timer(2)
        await io.set_line_property(8, 1, 0)
        await io.set_flowctrl(256)
        assert await io.poll_modem_status() == 7
        assert await io.write(b"\x01\x02") == 2
        assert await io.read(2) == b"\x03\x04"
        assert await io.readline() == b"ok\n"
        await io.usb_purge_tx_buffer()
        await io.usb_purge_rx_buffer()
        await io.usb_reset()

    assert device.closed
    assert [(command.action, command.data) for command in commands] == [
      ("set_baudrate", "115200"),
      ("set_rts", "True"),
      ("set_dtr", "False"),
      ("set_latency_timer", "2"),
      ("set_line_property", "8,1,0"),
      ("set_flowctrl", "256"),
      ("poll_modem_status", "7"),
      ("write", "0102"),
      ("read", "0304"),
      ("read", "6f"),
      ("read", "6b"),
      ("read", "0a"),
      ("usb_purge_tx_buffer", ""),
      ("usb_purge_rx_buffer", ""),
      ("usb_reset", ""),
    ]
    assert [(event.name, event.data["data"]) for event in events] == [
      ("io.write", "0102"),
      ("io.read", "0304"),
      ("io.read", "6f"),
      ("io.read", "6b"),
      ("io.read", "0a"),
    ]

  async def test_empty_reads_are_not_recorded(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice()
    commands: list[FTDICommand] = []
    events: list[Any] = []
    handler = _RecordingHandler()
    logger = logging.getLogger("pylabrobot.io.ftdi")
    previous_level = logger.level
    logger.setLevel(LOG_LEVEL_IO)
    logger.addHandler(handler)
    bus = EventBus()
    bus.subscribe(events.append)

    try:
      with (
        _device_factory(device),
        mock.patch.object(ftdi_module.capturer, "record", commands.append),
        use_event_bus(bus),
      ):
        async with io:
          handler.records.clear()
          with anyio.move_on_after(0.01) as scope:
            await io.read(4)
          assert scope.cancel_called
          with pytest.raises(TimeoutError):
            await io.readline(timeout=0)
    finally:
      logger.removeHandler(handler)
      logger.setLevel(previous_level)

    assert handler.records == []
    assert commands == []
    assert events == []

  async def test_cancelled_read_returns_promptly_and_retains_bytes_once(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice(to_read=b"ABCDEFGH")
    gate = _Gate()
    device.read_gate = gate
    scopes: list[anyio.CancelScope] = []
    caller_exited = anyio.Event()

    async def read_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await io.read(4)
        finally:
          caller_exited.set()

    with _device_factory(device):
      async with io:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read_in_scope)
          await _wait_for_thread_event(gate.started)
          scopes[0].cancel()
          await caller_exited.wait()
          pending = io._pending_read
          assert pending is not None
          assert not pending.completed.is_set()
          gate.release.set()
          await pending.completed.wait()

        assert await io.read(4) == b"ABCD"
        device.read_gate = None
        assert await io.read(4) == b"EFGH"

    assert device.reads == 2

  async def test_cancelled_write_returns_promptly_but_device_write_finishes(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice()
    gate = _Gate()
    device.write_gate = gate
    scopes: list[anyio.CancelScope] = []
    caller_exited = anyio.Event()
    commands: list[FTDICommand] = []

    async def write_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await io.write(b"command")
        finally:
          caller_exited.set()

    with (
      _device_factory(device),
      mock.patch.object(ftdi_module.capturer, "record", commands.append),
    ):
      async with io:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(write_in_scope)
          await _wait_for_thread_event(gate.started)
          scopes[0].cancel()
          await anyio.lowlevel.checkpoint()
          assert not caller_exited.is_set()
          assert bytes(device.written) == b""
          gate.release.set()
          await caller_exited.wait()
        await _wait_for_thread_event(gate.finished)

    assert bytes(device.written) == b"command"
    assert [(command.action, command.data) for command in commands] == [("write", "636f6d6d616e64")]

  async def test_short_write_is_rejected(self) -> None:
    """A returned short byte count must not be logged as a completed FTDI transfer."""
    io = _new_ftdi()
    device = _FakeDevice()
    device.write = lambda data: len(data) - 1  # type: ignore[method-assign]
    with _device_factory(device):
      async with io:
        with pytest.raises(OSError, match="short write"):
          await io.write(b"command")

  async def test_validator_cancellation_keeps_captured_read(self) -> None:
    """FTDI replay consumes a command only after admission and size validation."""
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "ftdi",
      "device_id": "test",
      "action": "read",
      "data": "61",
    }
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "HAS_PYUSB", True),
    ):
      validator = ftdi_module.FTDIValidator(reader, "test", "test")
    async with validator:
      with pytest.raises(ValueError):
        await validator.read(0)
      with anyio.CancelScope() as scope:
        scope.cancel()
        await validator.read(2)
      assert await validator.read(2) == b"a"
    assert reader.next_command.call_count == 1

  async def test_purge_invalidates_an_in_flight_read_by_identity(self) -> None:
    io = _new_ftdi()
    await self._assert_discard_invalidates_pending_read(io, io.usb_purge_rx_buffer)

  async def test_reset_invalidates_an_in_flight_read_by_identity(self) -> None:
    io = _new_ftdi()
    await self._assert_discard_invalidates_pending_read(io, io.usb_reset)

  async def _assert_discard_invalidates_pending_read(
    self, io: FTDI, operation: Callable[[], Awaitable[None]]
  ) -> None:
    device = _FakeDevice(to_read=b"staleFRESH")
    gate = _Gate()
    device.read_gate = gate
    scopes: list[anyio.CancelScope] = []
    caller_exited = anyio.Event()

    async def read_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await io.read(5)
        finally:
          caller_exited.set()

    with _device_factory(device):
      async with io:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read_in_scope)
          await _wait_for_thread_event(gate.started)
          pending = io._pending_read
          scopes[0].cancel()
          await caller_exited.wait()

          async def run_operation() -> None:
            await operation()

          task_group.start_soon(run_operation)
          with anyio.fail_after(2):
            while io._pending_read is pending:
              await anyio.sleep(0)
          gate.release.set()
        device.read_gate = None
        assert await io.read(5) == b"FRESH"

  async def test_lifespan_exit_waits_for_retained_work_before_close(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice(to_read=b"held")
    gate = _Gate()
    device.read_gate = gate
    scopes: list[anyio.CancelScope] = []
    caller_exited = anyio.Event()
    lifespan_exited = anyio.Event()

    async def use_resource() -> None:
      async with io:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await io.read(4)
          finally:
            caller_exited.set()
      lifespan_exited.set()

    with _device_factory(device):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_resource)
        await _wait_for_thread_event(gate.started)
        scopes[0].cancel()
        await caller_exited.wait()
        await anyio.sleep(0)
        assert not lifespan_exited.is_set()
        assert not device.closed
        gate.release.set()
        await lifespan_exited.wait()

    assert device.calls[-1] == "close"

  async def test_capture_and_events_run_once_in_the_async_callers_context(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice(to_read=b"reply")
    marker = contextvars.ContextVar("ftdi_test_marker", default="outside")
    observations: list[tuple[str, object, int]] = []
    bus = EventBus()
    bus.subscribe(
      lambda event: observations.append((event.name, marker.get(), anyio.get_current_task().id))
    )

    def record(command: FTDICommand) -> None:
      observations.append((command.action, marker.get(), anyio.get_current_task().id))

    with (
      _device_factory(device),
      mock.patch.object(ftdi_module.capturer, "record", record),
      use_event_bus(bus),
    ):
      async with io:
        caller_task = anyio.get_current_task().id
        token = marker.set("caller")
        try:
          await io.write(b"command")
          assert await io.read(5) == b"reply"
        finally:
          marker.reset(token)

    assert observations == [
      ("write", "caller", caller_task),
      ("io.write", "caller", caller_task),
      ("read", "caller", caller_task),
      ("io.read", "caller", caller_task),
    ]

  async def test_calls_after_exit_do_not_reach_the_device(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice()

    with _device_factory(device):
      async with io:
        pass

    calls_after_close = list(device.calls)
    operations = (
      lambda: io.write(b"late"),
      lambda: io.read(1),
      lambda: io.set_baudrate(9600),
      io.usb_reset,
      io.usb_purge_rx_buffer,
    )
    for operation in operations:
      with pytest.raises(RuntimeError, match="Device not initialized"):
        await operation()
      assert device.calls == calls_after_close

  async def test_control_queued_during_exit_does_not_reach_closed_device(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice()
    close_gate = _Gate()
    device.close_gate = close_gate
    leave_lifespan = anyio.Event()
    lifespan_entered = anyio.Event()
    lifespan_exited = anyio.Event()
    control_started = anyio.Event()
    control_finished = anyio.Event()
    failures: list[BaseException] = []

    async def use_resource() -> None:
      async with io:
        lifespan_entered.set()
        await leave_lifespan.wait()
      lifespan_exited.set()

    async def set_rts_during_exit() -> None:
      control_started.set()
      try:
        await io.set_rts(True)
      except BaseException as error:
        failures.append(error)
      finally:
        control_finished.set()

    with _device_factory(device):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_resource)
        await lifespan_entered.wait()
        leave_lifespan.set()
        await _wait_for_thread_event(close_gate.started)
        task_group.start_soon(set_rts_during_exit)
        await control_started.wait()
        await anyio.sleep(0)
        close_gate.release.set()
        await lifespan_exited.wait()
        await control_finished.wait()

    assert device.controls_on_closed_device == []
    assert len(failures) == 1
    assert isinstance(failures[0], RuntimeError)
    assert "Device not initialized" in str(failures[0])

  async def test_text_mode_reads_still_round_trip_every_byte(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice(to_read=b"\xff\x01", text_mode=True)

    with _device_factory(device):
      async with io:
        assert await io.read(2) == b"\xff\x01"

  async def test_validator_has_a_no_op_lifespan(self) -> None:
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "HAS_PYUSB", True),
    ):
      validator = FTDIValidator(mock.Mock(), "validator", "recorded")

    async with validator:
      assert validator.is_active
    assert not validator.is_active


class TestFTDICapture(AnyioTestBase):
  def test_command_carries_its_data(self) -> None:
    command = FTDICommand(device_id="test", action="write", data="dead")
    assert command.data == "dead"

  async def test_capture_replays_through_the_validator(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice(to_read=b"\x03\x04")

    try:
      with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "capture.json"
        with _device_factory(device):
          async with io:
            capturer.start(path)
            await io.set_baudrate(115200)
            await io.write(b"\x01\x02")
            assert await io.read(2) == b"\x03\x04"
            capturer.stop()

        reader = CaptureReader(str(path))
        with (
          mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
          mock.patch.object(ftdi_module, "HAS_PYUSB", True),
        ):
          validator = FTDIValidator(reader, "test", "test")
        async with validator:
          await validator.set_baudrate(115200)
          assert await validator.write(b"\x01\x02") == 2
          assert await validator.read(2) == b"\x03\x04"
        reader.done()
    finally:
      if capturer.capture_active:
        capturer.stop()
      capture_module._capture_or_validation_active = False


class TestFTDITopology(AnyioTestBase):
  async def test_topology_rejects_missing_identifiers_and_addressless_devices_before_open(
    self,
  ) -> None:
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "HAS_PYUSB", True),
    ):
      missing_ids = FTDI("device", usb_address="3-2.4")
      addressless = FTDI("device", vid=0x403, pid=0x6001, usb_address="3-2.4")
    candidate = mock.Mock(bus=3, address=None, port_numbers=(2, 4))
    usb = SimpleNamespace(core=SimpleNamespace(find=lambda **kwargs: [candidate]))
    with (
      mock.patch.object(ftdi_module, "usb", usb, create=True),
      mock.patch.object(ftdi_module, "_USBAddressDevice", create=True) as device_factory,
    ):
      with pytest.raises(RuntimeError, match="requires both vid and pid"):
        async with missing_ids:
          pass
      with pytest.raises(RuntimeError, match="no device address"):
        async with addressless:
          pass
      device_factory.assert_not_called()

  async def test_topology_entry_cancellation_restores_detached_driver_after_open(self) -> None:
    candidate = mock.Mock(bus=3, address=17, port_numbers=(2, 4))
    candidate.is_kernel_driver_active.side_effect = [True, False]
    calls: list[str] = []
    candidate.detach_kernel_driver.side_effect = lambda interface: calls.append("detach")
    candidate.attach_kernel_driver.side_effect = lambda interface: calls.append("attach")
    device = _FakeDevice()
    open_started = threading.Event()
    release = threading.Event()
    original_open = device.open
    original_close = device.close

    def open_device() -> None:
      calls.append("open")
      open_started.set()
      assert release.wait(2)
      original_open()

    def close_device() -> None:
      calls.append("close")
      original_close()

    device.open = open_device  # type: ignore[method-assign]
    device.close = close_device  # type: ignore[method-assign]
    usb = SimpleNamespace(
      core=SimpleNamespace(find=lambda **kwargs: [candidate], USBError=Exception),
      util=SimpleNamespace(dispose_resources=lambda dev: None),
    )
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "HAS_PYUSB", True),
      mock.patch.object(ftdi_module, "usb", usb, create=True),
      mock.patch.object(ftdi_module, "_USBAddressDevice", return_value=device, create=True),
    ):
      transport = FTDI("device", vid=0x403, pid=0x6001, usb_address="3-2.4")
      scopes: list[anyio.CancelScope] = []
      exited = anyio.Event()

      async def enter() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            async with transport:
              pass
          finally:
            exited.set()

      async with anyio.create_task_group() as group:
        group.start_soon(enter)
        await _wait_for_thread_event(open_started)
        scopes[0].cancel()
        await anyio.sleep(0)
        assert not exited.is_set()
        release.set()
        await exited.wait()
    assert calls == ["detach", "open", "close", "attach"]
    assert transport._dev is None
    assert transport._detached_kernel_driver is None

  @pytest.mark.parametrize("unsupported_operation", ["inspection", "detach"])
  async def test_unsupported_kernel_driver_operation_never_reattaches(
    self, unsupported_operation: str
  ) -> None:
    io = _new_ftdi()
    io._interface_select = 2
    device = mock.Mock()
    device.is_kernel_driver_active.return_value = True
    if unsupported_operation == "inspection":
      device.is_kernel_driver_active.side_effect = NotImplementedError
    else:
      device.detach_kernel_driver.side_effect = NotImplementedError
    dispose = mock.Mock()
    with (
      mock.patch.object(io, "_usb_device_at", return_value=device),
      mock.patch.object(
        ftdi_module,
        "usb",
        SimpleNamespace(util=SimpleNamespace(dispose_resources=dispose)),
        create=True,
      ),
    ):
      io._detach_kernel_driver(3, 17)
      assert io._detached_kernel_driver is None
      io._reattach_kernel_driver()
    dispose.assert_called_once_with(device)
    device.attach_kernel_driver.assert_not_called()

  async def test_close_failure_still_reattaches_kernel_driver(self) -> None:
    io = _new_ftdi()
    device = _FakeDevice()
    device.close = mock.Mock(side_effect=RuntimeError("close failed"))  # type: ignore[method-assign]
    io._dev = device
    io._detached_kernel_driver = (3, 17, 0)
    attached: list[tuple[int, int, int]] = []

    def reattach() -> None:
      assert io._detached_kernel_driver is not None
      attached.append(io._detached_kernel_driver)
      io._detached_kernel_driver = None

    with mock.patch.object(io, "_reattach_kernel_driver", side_effect=reattach):
      with pytest.raises(RuntimeError, match="close failed"):
        await io._close()
    assert attached == [(3, 17, 0)]
    assert io._dev is None

  async def test_topology_selection_detaches_then_reattaches_on_exit(self) -> None:
    calls: list[object] = []
    candidate = mock.Mock(bus=3, address=17, port_numbers=(2, 4))
    other = mock.Mock(bus=3, address=18, port_numbers=(2, 5))
    candidate.is_kernel_driver_active.return_value = False
    candidate.is_kernel_driver_active.side_effect = [True, False]
    candidate.detach_kernel_driver.side_effect = lambda interface: calls.append(
      ("detach", interface)
    )
    candidate.attach_kernel_driver.side_effect = lambda interface: calls.append(
      ("attach", interface)
    )
    device = _FakeDevice()
    original_open = device.open
    original_close = device.close

    def open_device() -> None:
      calls.append("open")
      original_open()

    def close_device() -> None:
      calls.append("close")
      original_close()

    device.open = open_device  # type: ignore[method-assign]
    device.close = close_device  # type: ignore[method-assign]

    def find_usb(*, find_all: bool, **kwargs: int) -> list[mock.Mock]:
      assert find_all
      if kwargs:
        assert kwargs == {"idVendor": 0x403, "idProduct": 0x6001}
      return [other, candidate]

    usb = SimpleNamespace(
      core=SimpleNamespace(find=find_usb, USBError=Exception),
      util=SimpleNamespace(dispose_resources=lambda dev: calls.append(("dispose", dev.address))),
    )
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "HAS_PYUSB", True),
      mock.patch.object(ftdi_module, "usb", usb, create=True),
      mock.patch.object(ftdi_module, "_USBAddressDevice", return_value=device, create=True) as ctor,
    ):
      io = FTDI("device", vid=0x403, pid=0x6001, interface_select=2, usb_address="3-2.4")
      async with io:
        assert io.device_id == "3-2.4"
        assert io.serialize()["usb_address"] == "3-2.4"
      assert ctor.call_args.kwargs["usb_bus"] == 3
      assert ctor.call_args.kwargs["usb_device_address"] == 17
    assert calls.index(("detach", 1)) < calls.index("open") < calls.index("close")
    assert calls.index("close") < calls.index(("attach", 1))
    assert io._detached_kernel_driver is None

  async def test_topology_open_failure_restores_kernel_driver(self) -> None:
    candidate = mock.Mock(bus=3, address=17, port_numbers=(2, 4))
    candidate.is_kernel_driver_active.side_effect = [True, False]
    device = _FakeDevice()
    device.open = mock.Mock(side_effect=RuntimeError("open failed"))  # type: ignore[method-assign]
    usb = SimpleNamespace(
      core=SimpleNamespace(find=lambda **kwargs: [candidate], USBError=Exception),
      util=SimpleNamespace(dispose_resources=lambda dev: None),
    )
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "HAS_PYUSB", True),
      mock.patch.object(ftdi_module, "usb", usb, create=True),
      mock.patch.object(ftdi_module, "_USBAddressDevice", return_value=device, create=True),
    ):
      io = FTDI("device", vid=0x403, pid=0x6001, usb_address="3-2.4")
      with pytest.raises(RuntimeError, match="open failed"):
        async with io:
          pass
    candidate.attach_kernel_driver.assert_called_once_with(0)
    assert device.calls == ["close"]
    assert io._detached_kernel_driver is None

  async def test_topology_validation_and_transport_error_classifier(self) -> None:
    for address in ("3", "0-1", "3-0", "3-1.bad"):
      with pytest.raises(ValueError):
        _parse_usb_address(address)
    with mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", False):
      assert not is_ftdi_transport_error(RuntimeError("not an FTDI error"))
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "FtdiError", RuntimeError, create=True),
    ):
      assert is_ftdi_transport_error(RuntimeError("driver error"))
      assert not is_ftdi_transport_error(ValueError("other error"))
