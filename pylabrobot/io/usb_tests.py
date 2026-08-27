import contextlib
import contextvars
import json
import threading
from io import UnsupportedOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Optional
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.events import EventBus, use_event_bus
from pylabrobot.hamilton.protocol.text.router import ReplyRouter, USBReplyReader
from pylabrobot.io import usb as usb_module
from pylabrobot.io.boundary import BoundaryRead
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import ContinuousConnectionIO, ReconnectableConnectionIO
from pylabrobot.io.usb import (
  USB,
  USBCommand,
  USBLegacyBoundaryProfile,
  USBValidator,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class _FakeEndpoint:
  def __init__(self, address: int, max_packet_size: int = 4) -> None:
    self.bEndpointAddress = address
    self.wMaxPacketSize = max_packet_size
    self.bmAttributes = 2  # bulk endpoint


class _FakeConfiguration:
  def __init__(self, endpoints: list[_FakeEndpoint]) -> None:
    self.endpoints = endpoints

  def __getitem__(self, key: tuple[int, int]) -> list[_FakeEndpoint]:
    assert key == (0, 0)
    return self.endpoints


class _FakeDevice:
  def __init__(self, calls: list[Any], reads: Optional[list[Optional[bytes]]] = None) -> None:
    self.calls = calls
    self.reads = list(reads or [])
    self.read_endpoint = _FakeEndpoint(0x81)
    self.write_endpoint = _FakeEndpoint(0x02)
    self.configuration = _FakeConfiguration([self.read_endpoint, self.write_endpoint])
    self.read_started = threading.Event()
    self.read_gate: Optional[threading.Event] = None
    self.write_started = threading.Event()
    self.write_gate: Optional[threading.Event] = None
    self.configuration_callback: Optional[Callable[[], None]] = None
    self.disposed = False
    self.write_error: Optional[BaseException] = None
    self.read_error: Optional[BaseException] = None

  def set_configuration(self) -> None:
    self.calls.append(("configure", threading.get_ident()))
    if self.configuration_callback is not None:
      self.configuration_callback()

  def get_active_configuration(self) -> _FakeConfiguration:
    self.calls.append(("configuration", threading.get_ident()))
    return self.configuration

  def read(self, endpoint: object, size: int, timeout: int) -> Optional[bytes]:
    assert not self.disposed
    self.calls.append(("read", endpoint, size, timeout, threading.get_ident()))
    if self.read_error is not None:
      raise self.read_error
    self.read_started.set()
    if self.read_gate is not None:
      self.read_gate.wait(timeout=timeout / 1000)
    if self.reads:
      return self.reads.pop(0)
    return None

  def write(self, endpoint: object, data: bytes, timeout: int) -> int:
    assert not self.disposed
    self.calls.append(("write", endpoint, data, timeout, threading.get_ident()))
    if self.write_error is not None:
      raise self.write_error
    self.write_started.set()
    if self.write_gate is not None:
      self.write_gate.wait(timeout=2)
    return len(data)

  def ctrl_transfer(self, **kwargs: int) -> bytearray:
    assert not self.disposed
    self.calls.append(("ctrl_transfer", kwargs, threading.get_ident()))
    return bytearray([3, 1, 4])


class _FakeUSBError(Exception):
  pass


class _FakeUSBTimeoutError(_FakeUSBError):
  pass


def _fake_usb(calls: list[Any]) -> SimpleNamespace:
  def find_descriptor(interface: list[_FakeEndpoint], custom_match: Callable[[Any], bool]):
    return next((endpoint for endpoint in interface if custom_match(endpoint)), None)

  def dispose_resources(device: _FakeDevice) -> None:
    calls.append(("dispose", threading.get_ident()))
    device.disposed = True

  util = SimpleNamespace(
    ENDPOINT_IN=0x80,
    ENDPOINT_OUT=0,
    endpoint_direction=lambda address: address & 0x80,
    find_descriptor=find_descriptor,
    dispose_resources=dispose_resources,
  )
  return SimpleNamespace(
    core=SimpleNamespace(USBError=_FakeUSBError, USBTimeoutError=_FakeUSBTimeoutError), util=util
  )


def _new_usb(max_workers: int = 1, packet_read_timeout: float = 0.1) -> USB:
  return USB(
    id_vendor=0x1234,
    id_product=0x5678,
    human_readable_device_name="fake USB",
    packet_read_timeout=packet_read_timeout,  # type: ignore[arg-type]
    read_timeout=1,
    write_timeout=1,
    max_workers=max_workers,
  )


@contextlib.contextmanager
def _device_context(io: USB, device: _FakeDevice, calls: list[Any]) -> Iterator[None]:
  with (
    mock.patch.object(usb_module, "USE_USB", True),
    mock.patch.object(usb_module, "usb", _fake_usb(calls), create=True),
    mock.patch.object(io, "get_available_devices", return_value=[device]),
  ):
    yield


async def _wait_for_thread_event(event: threading.Event) -> None:
  with anyio.fail_after(2):
    while not event.is_set():
      await anyio.sleep(0)


class TestUSBLifespan(AnyioTestBase):
  async def test_cancelled_router_write_waiting_for_usb_lock_does_not_drop_successor(self) -> None:
    """An unsent ID-less command must not consume the next command's reply."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.read_endpoint.wMaxPacketSize = 64
    io = _new_usb()
    router = ReplyRouter(io, 2, lambda reply: None, lambda reply: None, reader=USBReplyReader(io))
    cancelled = anyio.Event()
    responses: list[Optional[str]] = []
    scope = anyio.CancelScope()

    async def first() -> None:
      with scope:
        try:
          await router.send("C0AA", None)
        finally:
          cancelled.set()

    async def second() -> None:
      responses.append(await router.send("C0AA", None, read_timeout=0.3))

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False), router.lifespan():
        async with anyio.create_task_group() as group:
          async with io._write_lock:
            group.start_soon(first)
            with anyio.fail_after(1):
              while not router._waiting_tasks_by_command.get("C0AA"):
                await anyio.sleep(0)
            scope.cancel()
            await cancelled.wait()
          assert not [call for call in calls if call[0] == "write"]
          group.start_soon(second)
          await _wait_for_thread_event(device.write_started)
          device.reads.append(b"C0AAfresh")
    assert responses == ["C0AAfresh"]

  async def test_cancelled_router_write_after_usb_dispatch_keeps_stale_reply_slot(self) -> None:
    """Cancellation during the native write must not misattribute an old reply."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.read_endpoint.wMaxPacketSize = 64
    device.write_gate = threading.Event()
    io = _new_usb()
    router = ReplyRouter(io, 2, lambda reply: None, lambda reply: None, reader=USBReplyReader(io))
    cancelled = anyio.Event()
    responses: list[Optional[str]] = []
    scope = anyio.CancelScope()

    async def first() -> None:
      with scope:
        try:
          await router.send("C0AA", None)
        finally:
          cancelled.set()

    async def second() -> None:
      responses.append(await router.send("C0AA", None, read_timeout=0.5))

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False), router.lifespan():
        async with anyio.create_task_group() as group:
          group.start_soon(first)
          await _wait_for_thread_event(device.write_started)
          scope.cancel()
          device.write_gate.set()
          await cancelled.wait()
          group.start_soon(second)
          with anyio.fail_after(1):
            while len([call for call in calls if call[0] == "write"]) < 2:
              await anyio.sleep(0)
          device.reads.extend([b"C0AAstale", b"C0AAfresh"])
    assert responses == ["C0AAfresh"]

  async def test_router_usb_preflight_write_failure_does_not_drop_successor(self) -> None:
    """A write rejected before native dispatch has no reply to reserve a slot for."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.read_endpoint.wMaxPacketSize = 64
    io = _new_usb()
    router = ReplyRouter(io, 2, lambda reply: None, lambda reply: None, reader=USBReplyReader(io))
    responses: list[Optional[str]] = []

    async def second() -> None:
      responses.append(await router.send("C0AA", None, read_timeout=0.5))

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False), router.lifespan():
        endpoint = io.write_endpoint
        io.write_endpoint = None
        with pytest.raises(RuntimeError, match="Call setup"):
          await router.send("C0AA", None)
        assert not [call for call in calls if call[0] == "write"]
        io.write_endpoint = endpoint
        async with anyio.create_task_group() as group:
          group.start_soon(second)
          await _wait_for_thread_event(device.write_started)
          device.reads.append(b"C0AAfresh")
    assert responses == ["C0AAfresh"]

  async def test_interleaved_endpoint_cursors_keep_their_own_packet_remainders(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"abcd", b"XY", b"ef"])
    device.configuration.endpoints.append(_FakeEndpoint(0x82))
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        assert await io.read(endpoint=0x81, size=1) == b"a"
        assert await io.read_to_boundary(endpoint=0x82) == BoundaryRead(b"XY", True, True, False)
        assert await io.read_to_boundary(endpoint=0x81) == BoundaryRead(b"bcd", False, True, False)
        assert await io.read_to_boundary(endpoint=0x81) == BoundaryRead(b"ef", True, True, False)

  async def test_full_packet_waits_for_short_end_without_losing_partial_message(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"abcd", None, b"ef"])
    original_read = device.read

    def stalled_poll(endpoint: object, size: int, timeout: int) -> Optional[bytes]:
      if len([call for call in calls if call[0] == "read"]) == 1:
        threading.Event().wait(0.03)
      return original_read(endpoint, size, timeout)

    device.read = stalled_poll  # type: ignore[method-assign]
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        with pytest.raises(TimeoutError):
          with anyio.fail_after(0.01):
            await io.read_to_boundary(boundary="short_packet_end")
        assert await io.read_to_boundary(boundary="short_packet_end") == BoundaryRead(
          b"abcdef", False, True, False
        )

  async def test_capture_json_contains_binary_packet_marks(self, tmp_path: Path) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"\xff"])
    io = _new_usb()
    path = tmp_path / "usb-capture.json"
    with _device_context(io, device, calls):
      usb_module.capturer.start(path)
      try:
        async with io.lifespan(empty_buffer=False):
          assert (await io.read_to_boundary()).data == b"\xff"
      finally:
        usb_module.capturer.stop()
    command = json.loads(path.read_text())["commands"][0]
    assert command["data_hex"] == "ff"
    assert command["marks"][-1]["kind"] == "short_packet_end"

  async def test_native_overflow_does_not_create_packet_mark(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"12345"])
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        with pytest.raises(OSError, match="exceeds"):
          await io.read_to_boundary()
        with pytest.raises(ConnectionError):
          await io.read(1)

  async def test_iso_endpoint_rejects_marks_before_consuming_input(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"ab"])
    device.read_endpoint.bmAttributes = 1
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        with pytest.raises(UnsupportedOperation):
          await io.read_to_boundary()
        assert await io.read() == b"ab"
    assert len([call for call in calls if call[0] == "read"]) == 1

  async def test_out_endpoint_cannot_supply_input_packet_marks(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"ab"])
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        with pytest.raises(UnsupportedOperation):
          await io.read_to_boundary(endpoint=0x02)
    assert not [call for call in calls if call[0] == "read"]

  async def test_boundary_capture_records_selected_endpoint_and_marks(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"ab"])
    io = _new_usb()
    commands: list[USBCommand] = []
    with (
      _device_context(io, device, calls),
      mock.patch.object(usb_module.capturer, "record", commands.append),
    ):
      async with io.lifespan(empty_buffer=False):
        assert await io.read_to_boundary() == BoundaryRead(b"ab", True, True, False)
    assert commands[0].endpoint == 0x81
    assert commands[0].consume_immediate_boundary is True
    assert commands[0].marks == [
      {"offset": 0, "kind": "packet", "pending": False, "provenance": "observed"},
      {"offset": 2, "kind": "packet", "pending": True, "provenance": "observed"},
      {"offset": 2, "kind": "short_packet_end", "pending": True, "provenance": "observed"},
    ]

  async def test_boundary_capture_preserves_binary_bytes(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"\xff\x00"])
    io = _new_usb()
    commands: list[USBCommand] = []
    with (
      _device_context(io, device, calls),
      mock.patch.object(usb_module.capturer, "record", commands.append),
    ):
      async with io.lifespan(empty_buffer=False):
        assert (await io.read_to_boundary()).data == b"\xff\x00"
    assert commands[0].data_hex == "ff00"

  async def test_packet_and_short_end_after_short_byte_read(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"abcd", b"ef"])
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        assert await io.read(size=1) == b"a"
        assert await io.read_to_boundary(boundary="packet") == BoundaryRead(
          b"bcd", False, True, False
        )
        assert await io.read_to_boundary(boundary="short_packet_end") == BoundaryRead(
          b"ef", False, True, False
        )
    assert [call[2] for call in calls if call[0] == "read"] == [4, 4]

  async def test_zlp_marks_end_of_full_packet_message(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"abcd", b""])
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        assert await io.read_to_boundary(boundary="short_packet_end") == BoundaryRead(
          b"abcd", False, True, False
        )

  async def test_usb_replaces_handle_without_changing_lifespan(self) -> None:
    calls: list[Any] = []
    first, second = _FakeDevice(calls), _FakeDevice(calls)
    io = USB(0x1234, 0x5678, "fake USB", packet_read_timeout=0.1, read_timeout=1)

    def select_devices() -> list[_FakeDevice]:
      if first.disposed:
        assert not second.disposed
        return [second]
      return [first]

    with (
      mock.patch.object(usb_module, "USE_USB", True),
      mock.patch.object(usb_module, "usb", _fake_usb(calls), create=True),
      mock.patch.object(io, "get_available_devices", side_effect=select_devices),
    ):
      assert isinstance(io, ReconnectableConnectionIO)
      assert isinstance(io, ContinuousConnectionIO)
      async with io.lifespan(empty_buffer=False):
        await io.reconnect()
        assert first.disposed
        assert io.dev is second
        await io.write(b"new")
      assert second.disposed

  async def test_usb_rejects_reconnect_outside_lifespan(self) -> None:
    io = USB(0x1234, 0x5678, "fake USB")
    with pytest.raises(RuntimeError, match="active lifespan"):
      await io.reconnect()

  async def test_failed_usb_reconnect_blocks_io_until_retry(self) -> None:
    calls: list[Any] = []
    first, replacement = _FakeDevice(calls), _FakeDevice(calls)
    io = USB(0x1234, 0x5678, "fake USB", packet_read_timeout=0.1, read_timeout=1)
    selections = iter([[first], [], [replacement]])
    with (
      mock.patch.object(usb_module, "USE_USB", True),
      mock.patch.object(usb_module, "usb", _fake_usb(calls), create=True),
      mock.patch.object(io, "get_available_devices", side_effect=lambda: next(selections)),
    ):
      async with io.lifespan(empty_buffer=False):
        with pytest.raises(RuntimeError, match="not found"):
          await io.reconnect()
        assert first.disposed
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"stale")
        await io.reconnect()
        await io.write(b"fresh")
      assert replacement.disposed

  async def test_usb_reconnect_waits_for_in_flight_write(self) -> None:
    calls: list[Any] = []
    first, replacement = _FakeDevice(calls), _FakeDevice(calls)
    first.write_gate = threading.Event()
    io = USB(0x1234, 0x5678, "fake USB", packet_read_timeout=0.1, read_timeout=1)
    selections = iter([[first], [replacement]])
    with (
      mock.patch.object(usb_module, "USE_USB", True),
      mock.patch.object(usb_module, "usb", _fake_usb(calls), create=True),
      mock.patch.object(io, "get_available_devices", side_effect=lambda: next(selections)),
    ):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as group:
          group.start_soon(io.write, b"old")
          await _wait_for_thread_event(first.write_started)
          group.start_soon(io.reconnect)
          await anyio.sleep(0)
          assert not first.disposed
          first.write_gate.set()
        assert first.disposed
        await io.write(b"new")
      assert replacement.disposed

  async def test_cancelled_usb_reconnect_disposes_partially_opened_handle(self) -> None:
    calls: list[Any] = []
    first, interrupted, replacement = (_FakeDevice(calls) for _ in range(3))
    setup_started = threading.Event()
    setup_release = threading.Event()

    def wait_during_configuration() -> None:
      setup_started.set()
      setup_release.wait(timeout=2)

    interrupted.configuration_callback = wait_during_configuration
    io = USB(0x1234, 0x5678, "fake USB", packet_read_timeout=0.1, read_timeout=1)
    selections = iter([[first], [interrupted], [replacement]])
    with (
      mock.patch.object(usb_module, "USE_USB", True),
      mock.patch.object(usb_module, "usb", _fake_usb(calls), create=True),
      mock.patch.object(io, "get_available_devices", side_effect=lambda: next(selections)),
    ):
      async with io.lifespan(empty_buffer=False):
        scope = anyio.CancelScope()

        async def cancel_open() -> None:
          with scope:
            await io.reconnect()

        async with anyio.create_task_group() as group:
          group.start_soon(cancel_open)
          await _wait_for_thread_event(setup_started)
          scope.cancel()
          setup_release.set()
        assert first.disposed
        assert interrupted.disposed
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"stale")
        await io.reconnect()
        assert io.dev is replacement
      assert replacement.disposed

  async def test_usb_validator_reconnects_without_hardware_access(self) -> None:
    assert issubclass(USBValidator, ReconnectableConnectionIO)
    assert issubclass(USBValidator, ContinuousConnectionIO)

    validator = USBValidator(
      cr=mock.Mock(), human_readable_device_name="fake USB", id_vendor=1, id_product=2
    )
    with pytest.raises(RuntimeError, match="active lifespan"):
      await validator.reconnect()
    async with validator:
      await validator.reconnect()

  async def test_hard_write_failure_invalidates_the_current_handle(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()

    with _device_context(io, device, calls):
      assert isinstance(io, ContinuousConnectionIO)
      async with io.lifespan(empty_buffer=False):
        device.write_error = _FakeUSBError("disconnected")
        with pytest.raises(_FakeUSBError, match="disconnected"):
          await io.write(b"first")
        device.write_error = None
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"second")

    assert device.disposed
    assert len([call for call in calls if call[0] == "write"]) == 1
    replacement = _FakeDevice(calls)
    with _device_context(io, replacement, calls):
      async with io.lifespan(empty_buffer=False):
        await io.write(b"new lifespan")
    assert replacement.disposed

  async def test_timed_out_write_has_uncertain_delivery_until_reconnect(self) -> None:
    """A PyUSB write timeout cannot be treated as an unsubmitted command."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        device.write_error = _FakeUSBTimeoutError("possibly sent")
        with pytest.raises(_FakeUSBTimeoutError):
          await io.write(b"command")
        device.write_error = None
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"unsafe retry")

  async def test_hard_read_error_invalidates_handle_but_poll_timeout_does_not(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        device.read_error = _FakeUSBTimeoutError("no packet")
        with pytest.raises(TimeoutError):
          await io.read(timeout=1)
        await io.write(b"still connected")
        device.read_error = _FakeUSBError("device removed")
        with pytest.raises(_FakeUSBError, match="device removed"):
          await io.read(timeout=1)
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"stale")

  async def test_selected_endpoint_read_uses_its_packet_size_and_captures_once(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"ABCD", b"EF", b"xyz"])
    selected = _FakeEndpoint(0x82, max_packet_size=4)
    device.read_endpoint.wMaxPacketSize = 8
    device.configuration.endpoints.append(selected)
    io = _new_usb()
    commands: list[USBCommand] = []

    with (
      _device_context(io, device, calls),
      mock.patch.object(usb_module.capturer, "record", commands.append),
    ):
      async with io.lifespan(empty_buffer=False):
        assert await io.read(endpoint=0x82, size=6) == b"ABCD"
        assert await io.read(endpoint=0x82, size=6) == b"EF"
        assert await io.read() == b"xyz"
        with pytest.raises(ValueError, match="Endpoint 0x83 not found"):
          await io.read(endpoint=0x83, size=1)

    reads = [call for call in calls if call[0] == "read"]
    assert [(call[1], call[2]) for call in reads] == [
      (0x82, 4),
      (0x82, 4),
      (device.read_endpoint, 8),
    ]
    assert [(command.action, command.data) for command in commands] == [
      ("read", "ABCD"),
      ("read", "EF"),
      ("read", "xyz"),
    ]

  async def test_read_waits_through_empty_polls_and_default_has_no_deadline(self) -> None:
    """An idle USB endpoint does not return an empty result or expire by default."""
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[None, b"one"])
    io = USB(0x1234, 0x5678, "fake USB", packet_read_timeout=0.01)
    assert io.read_timeout is None
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        assert await io.read() == b"one"

  async def test_explicit_none_read_timeout_keeps_constructor_deadline(self) -> None:
    """Legacy per-call None still selects the constructor's explicit deadline."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    with pytest.warns(DeprecationWarning):
      io = USB(1, 2, "fake USB", packet_read_timeout=0.001, read_timeout=0.01)  # type: ignore[arg-type]
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        with pytest.warns(DeprecationWarning), pytest.raises(TimeoutError):
          await io.read(timeout=None)

  async def test_nonpositive_read_size_is_rejected(self) -> None:
    """Invalid sizes cannot be sent to a USB worker."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        for size in (0, -1):
          with pytest.raises(ValueError):
            await io.read(size=size)
    assert not [call for call in calls if call[0] == "read"]

  async def test_cancelled_worker_read_retains_packet_on_same_endpoint(self) -> None:
    """Cancellation while PyUSB blocks cannot consume an unreturned packet."""
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"kept"])
    gate = threading.Event()
    device.read_gate = gate
    io = _new_usb(packet_read_timeout=0.1)
    scopes: list[anyio.CancelScope] = []
    returned: list[bytes] = []

    async def read_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        returned.append(await io.read(size=4))

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as group:
          group.start_soon(read_in_scope)
          await _wait_for_thread_event(device.read_started)
          scopes[0].cancel()
          gate.set()
        assert returned == []
        assert await io.read(size=4) == b"kept"
    assert len([call for call in calls if call[0] == "read"]) == 1

  async def test_cancelled_write_waits_for_trailing_zero_length_packet(self) -> None:
    """Cancellation after the payload cannot abandon the second transfer phase."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    zlp_started = threading.Event()
    release = threading.Event()
    original_write = device.write

    def write(endpoint: object, data: bytes, timeout: int) -> int:
      if not data:
        zlp_started.set()
        release.wait(timeout=2)
      return original_write(endpoint, data, timeout)

    device.write = write  # type: ignore[method-assign]
    io = _new_usb()
    exited = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await io.write(b"four")
      exited.set()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as group:
          group.start_soon(send)
          await _wait_for_thread_event(zlp_started)
          scopes[0].cancel()
          await anyio.lowlevel.checkpoint()
          assert not exited.is_set()
          release.set()
          await exited.wait()
    assert [call[2] for call in calls if call[0] == "write"] == [b"four", b""]

  async def test_short_payload_write_is_rejected_before_zlp(self) -> None:
    """A short first phase cannot be reported as a completed write."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.write = lambda endpoint, data, timeout: len(data) - 1  # type: ignore[method-assign]
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        with pytest.raises(OSError, match="short write"):
          await io.write(b"four")

  async def test_interleaved_endpoints_keep_oversized_packet_remainders_separate(self) -> None:
    """A packet remainder belongs to its endpoint and is discarded by its drain."""
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"abcde", b"default", None])
    selected = _FakeEndpoint(0x82, max_packet_size=8)
    device.configuration.endpoints.append(selected)
    device.read_endpoint.wMaxPacketSize = 8
    io = _new_usb()
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        assert await io.read(size=2, endpoint=0x82) == b"ab"
        assert await io.read() == b"default"
        assert await io.read(size=2, endpoint=0x82) == b"cd"
        await io.drain(endpoint=0x82)
        with anyio.move_on_after(0.01) as scope:
          await io.read(endpoint=0x82)
        assert scope.cancel_called

  async def test_drain_discards_only_selected_endpoint_without_capture(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"stale", b"stale", None, b"fresh"])
    device.read_endpoint.wMaxPacketSize = 8
    selected = _FakeEndpoint(0x82, max_packet_size=64)
    device.configuration.endpoints.append(selected)
    io = _new_usb()
    commands: list[USBCommand] = []

    with (
      _device_context(io, device, calls),
      mock.patch.object(usb_module.capturer, "record", commands.append),
    ):
      async with io.lifespan(empty_buffer=False):
        await io.drain(endpoint=0x82, timeout=0.05, size=64)
        assert commands == []
        assert await io.read() == b"fresh"
    reads = [call for call in calls if call[0] == "read"]
    assert [(call[1], call[2]) for call in reads] == [
      (0x82, 64),
      (0x82, 64),
      (0x82, 64),
      (device.read_endpoint, 8),
    ]
    assert [(command.action, command.data) for command in commands] == [("read", "fresh")]

  async def test_drain_has_one_deadline_and_does_not_block_writes(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb(packet_read_timeout=0.1)
    device.read_gate = threading.Event()
    write_done = anyio.Event()

    def continuously_nonempty(endpoint: object, size: int, timeout: int) -> bytes:
      device.read_started.set()
      calls.append(("read", endpoint, size, timeout, threading.get_ident()))
      assert device.read_gate is not None
      device.read_gate.wait(timeout=timeout / 1000)
      return b"stale"

    async def write() -> None:
      await io.write(b"ok")
      write_done.set()

    device.read = continuously_nonempty  # type: ignore[method-assign]
    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as group:

          async def drain() -> None:
            with pytest.raises(TimeoutError, match="Timed out draining"):
              await io.drain(max_duration=0.08, timeout=0.05)

          group.start_soon(drain)
          await _wait_for_thread_event(device.read_started)
          group.start_soon(write)
          with anyio.fail_after(0.05):
            await write_done.wait()
          device.read_gate.set()

  async def test_transport_order_capture_and_events_are_preserved(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"old", None, b"reply"])
    device.read_endpoint.wMaxPacketSize = 8
    io = _new_usb()
    commands: list[USBCommand] = []
    events: list[Any] = []
    bus = EventBus()
    bus.subscribe(events.append)

    with (
      _device_context(io, device, calls),
      mock.patch.object(usb_module.capturer, "record", commands.append),
      use_event_bus(bus),
    ):
      async with io:
        assert isinstance(io, HasLifespan)
        await io.write(b"four")
        assert await io.read() == b"reply"

    assert [call[0] for call in calls] == [
      "configure",
      "configuration",
      "read",
      "read",
      "write",
      "write",
      "read",
      "dispose",
    ]
    assert [call[2] for call in calls if call[0] == "write"] == [b"four", b""]
    assert [(command.action, command.data) for command in commands] == [
      ("write", "four"),
      ("read", "reply"),
    ]
    assert [(event.name, event.data) for event in events] == [
      (
        "io.write",
        {
          "transport": "usb",
          "device": "fake USB",
          "device_id": "[0x1234:0x5678][][]",
          "data": "four",
        },
      ),
      (
        "io.read",
        {
          "transport": "usb",
          "device": "fake USB",
          "device_id": "[0x1234:0x5678][][]",
          "data": "reply",
        },
      ),
    ]
    assert io.dev is None
    assert io.read_endpoint is None
    assert io.write_endpoint is None

  @pytest.mark.parametrize("use_callback", [False, True])
  async def test_configuration_runs_off_loop(self, use_callback: bool) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()
    callback_threads: list[int] = []
    if use_callback:
      io.configuration_callback = lambda dev: callback_threads.append(threading.get_ident())
    loop_thread = threading.get_ident()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        pass

    configuration_thread = callback_threads[0] if use_callback else calls[0][1]
    assert configuration_thread != loop_thread

  async def test_configuration_failure_disposes_partial_device(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.configuration_callback = lambda: (_ for _ in ()).throw(RuntimeError("bad config"))
    io = _new_usb()

    with _device_context(io, device, calls), pytest.raises(RuntimeError, match="bad config"):
      async with io.lifespan(empty_buffer=False):
        pass

    assert device.disposed
    assert io.dev is None
    assert io.read_endpoint is None
    assert io.write_endpoint is None

  async def test_cancelled_setup_waits_for_worker_then_disposes(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    started = threading.Event()
    release = threading.Event()

    def block_configuration() -> None:
      started.set()
      release.wait(timeout=2)

    device.configuration_callback = block_configuration
    io = _new_usb()
    scopes: list[anyio.CancelScope] = []
    exited = anyio.Event()

    async def enter_resource() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with io.lifespan(empty_buffer=False):
            pass
        finally:
          exited.set()

    with _device_context(io, device, calls):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_resource)
        await _wait_for_thread_event(started)
        scopes[0].cancel()
        await anyio.sleep(0)
        assert not exited.is_set()
        release.set()
        await exited.wait()

    assert device.disposed
    assert io.dev is None

  async def test_blocked_read_does_not_prevent_write(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.read_gate = threading.Event()
    io = _new_usb()
    write_done = anyio.Event()

    async def write() -> None:
      await io.write(b"go")
      write_done.set()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(io._read_packet)
          await _wait_for_thread_event(device.read_started)
          task_group.start_soon(write)
          with anyio.fail_after(0.5):
            await write_done.wait()
          device.read_gate.set()

  async def test_shared_limiter_blocks_write_behind_read(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.read_gate = threading.Event()
    io = _new_usb()
    io._write_limiter = io._read_limiter
    write_done = anyio.Event()

    async def write() -> None:
      await io.write(b"go")
      write_done.set()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(io._read_packet)
          await _wait_for_thread_event(device.read_started)
          task_group.start_soon(write)
          with anyio.move_on_after(0.05):
            await write_done.wait()
          assert not write_done.is_set()
          device.read_gate.set()
          with anyio.fail_after(0.5):
            await write_done.wait()

  async def test_control_transfer_runs_off_loop_and_preserves_capture(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()
    commands: list[USBCommand] = []
    marker = contextvars.ContextVar("usb_marker", default="outside")
    observations: list[tuple[str, str, int]] = []

    def record(command: USBCommand) -> None:
      commands.append(command)
      observations.append((command.action, marker.get(), anyio.get_current_task().id))

    with (
      _device_context(io, device, calls),
      mock.patch.object(usb_module.capturer, "record", record),
    ):
      async with io.lifespan(empty_buffer=False):
        caller_task = anyio.get_current_task().id
        token = marker.set("caller")
        try:
          assert await io.ctrl_transfer(1, 2, 3, 4, 5, timeout=6) == bytearray([3, 1, 4])
        finally:
          marker.reset(token)

    transfer = next(call for call in calls if call[0] == "ctrl_transfer")
    assert transfer[1] == {
      "bmRequestType": 1,
      "bRequest": 2,
      "wValue": 3,
      "wIndex": 4,
      "data_or_wLength": 5,
      "timeout": 6000,
    }
    assert transfer[2] != threading.get_ident()
    assert [(command.action, command.data) for command in commands] == [
      ("ctrl_transfer_request", "1 2 3 4 5"),
      ("ctrl_transfer_response", "3 1 4"),
    ]
    assert observations == [
      ("ctrl_transfer_request", "caller", caller_task),
      ("ctrl_transfer_response", "caller", caller_task),
    ]

  async def test_control_transfer_capture_failure_does_not_invalidate_usb_handle(self) -> None:
    """A capture storage error after a successful transfer must not masquerade as USB loss."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()
    commands: list[USBCommand] = []

    def record(command: USBCommand) -> None:
      if not commands:
        commands.append(command)
        raise OSError("capture storage failed")
      commands.append(command)

    with (
      _device_context(io, device, calls),
      mock.patch.object(usb_module.capturer, "record", record),
    ):
      async with io.lifespan(empty_buffer=False):
        with pytest.raises(OSError, match="capture storage failed"):
          await io.ctrl_transfer(1, 2, 3, 4, 5)
        assert await io.ctrl_transfer(1, 2, 3, 4, 5) == bytearray([3, 1, 4])
    assert len([call for call in calls if call[0] == "ctrl_transfer"]) == 2

  async def test_control_transfer_cancelled_while_waiting_for_lock_never_dispatches(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()
    scope = anyio.CancelScope()
    finished = anyio.Event()

    async def transfer() -> None:
      with scope:
        await io.ctrl_transfer(1, 2, 3, 4, 5)
      finished.set()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with io._write_lock:
          async with anyio.create_task_group() as group:
            group.start_soon(transfer)
            await anyio.sleep(0)
            scope.cancel()
            with anyio.fail_after(1):
              await finished.wait()
        assert not [call for call in calls if call[0] == "ctrl_transfer"]

  async def test_control_transfer_cancelled_while_waiting_for_worker_never_dispatches(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()
    scope = anyio.CancelScope()
    finished = anyio.Event()

    async def transfer() -> None:
      with scope:
        await io.ctrl_transfer(1, 2, 3, 4, 5)
      finished.set()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as group:
          async with io._write_limiter:
            group.start_soon(transfer)
            await anyio.sleep(0.02)
            scope.cancel()
            await anyio.sleep(0)
            assert not [call for call in calls if call[0] == "ctrl_transfer"]
          with anyio.fail_after(1):
            await finished.wait()
        assert not [call for call in calls if call[0] == "ctrl_transfer"]

  async def test_cancelled_control_transfer_joins_and_captures_before_reporting_cancellation(
    self,
  ) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    started = threading.Event()
    release = threading.Event()
    original_transfer = device.ctrl_transfer

    def blocked_transfer(**kwargs: int) -> bytearray:
      started.set()
      release.wait(timeout=2)
      return original_transfer(**kwargs)

    device.ctrl_transfer = blocked_transfer  # type: ignore[method-assign]
    io = _new_usb()
    scope = anyio.CancelScope()
    finished = anyio.Event()
    results: list[bytearray] = []
    commands: list[USBCommand] = []

    async def transfer() -> None:
      with scope:
        results.append(await io.ctrl_transfer(1, 2, 3, 4, 5))
      finished.set()

    with (
      _device_context(io, device, calls),
      mock.patch.object(usb_module.capturer, "record", commands.append),
    ):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as group:
          group.start_soon(transfer)
          await _wait_for_thread_event(started)
          scope.cancel()
          await anyio.sleep(0)
          assert not finished.is_set()
          assert not device.disposed
          release.set()
          with anyio.fail_after(2):
            await finished.wait()
        assert results == []
        assert [command.action for command in commands] == [
          "ctrl_transfer_request",
          "ctrl_transfer_response",
        ]
        assert await io.ctrl_transfer(1, 2, 3, 4, 5) == bytearray([3, 1, 4])

  async def test_cancelled_control_transfer_propagates_native_failure_and_invalidates_handle(
    self,
  ) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    started = threading.Event()
    release = threading.Event()

    def failed_transfer(**kwargs: int) -> bytearray:
      started.set()
      release.wait(timeout=2)
      raise _FakeUSBError("disconnected")

    device.ctrl_transfer = failed_transfer  # type: ignore[method-assign]
    io = _new_usb()
    scope = anyio.CancelScope()
    errors: list[str] = []

    async def transfer() -> None:
      with scope:
        with pytest.raises(_FakeUSBError, match="disconnected") as error:
          await io.ctrl_transfer(1, 2, 3, 4, 5)
        errors.append(str(error.value))

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as group:
          group.start_soon(transfer)
          await _wait_for_thread_event(started)
          scope.cancel()
          release.set()
        assert errors == ["disconnected"]
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.ctrl_transfer(1, 2, 3, 4, 5)

  async def test_read_cancellation_is_bounded_by_packet_timeout(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.read_gate = threading.Event()
    io = _new_usb(packet_read_timeout=0.1)
    scopes: list[anyio.CancelScope] = []
    exited = anyio.Event()

    async def read() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await io.read()
        finally:
          exited.set()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read)
          await _wait_for_thread_event(device.read_started)
          started = anyio.current_time()
          scopes[0].cancel()
          await exited.wait()
          assert anyio.current_time() - started < 0.5

  async def test_direct_packet_read_preserves_deadline_across_bounded_polls(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[None, b"reply"])
    io = _new_usb(packet_read_timeout=0.1)

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        assert await io._read_packet(timeout=5.0) == bytearray(b"reply")

    assert [call[3] for call in calls if call[0] == "read"] == [100, 100]

  async def test_packet_read_timeout_conversion_is_positive_and_capped(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"a", b"b", b"c"])
    io = _new_usb(packet_read_timeout=0.1)

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        assert io._read_packet_sync(timeout=0.0001) == bytearray(b"a")
        assert io._read_packet_sync(timeout=0.05) == bytearray(b"b")
        assert io._read_packet_sync(timeout=5.0) == bytearray(b"c")

    assert [call[3] for call in calls if call[0] == "read"] == [1, 50, 100]

  async def test_direct_packet_read_cancellation_is_bounded_by_packet_timeout(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.read_gate = threading.Event()
    io = _new_usb(packet_read_timeout=0.1)
    scopes: list[anyio.CancelScope] = []
    exited = anyio.Event()

    async def read_packet() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await io._read_packet(timeout=5.0)
        finally:
          exited.set()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read_packet)
          await _wait_for_thread_event(device.read_started)
          started = anyio.current_time()
          scopes[0].cancel()
          await exited.wait()
          assert anyio.current_time() - started < 0.5

    assert [call[3] for call in calls if call[0] == "read"] == [100]

  async def test_exit_waits_for_active_write_before_disposal(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.write_gate = threading.Event()
    io = _new_usb()
    lifespan_exited = anyio.Event()

    async def use_resource() -> None:
      async with io.lifespan(empty_buffer=False):
        await io.write(b"held")
      lifespan_exited.set()

    with _device_context(io, device, calls):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_resource)
        await _wait_for_thread_event(device.write_started)
        await anyio.sleep(0)
        assert not device.disposed
        assert not lifespan_exited.is_set()
        device.write_gate.set()
        await lifespan_exited.wait()

    assert [call[0] for call in calls][-2:] == ["write", "dispose"]

  async def test_calls_after_exit_do_not_reach_device(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_usb()

    with _device_context(io, device, calls):
      async with io.lifespan(empty_buffer=False):
        pass

    calls_after_exit = list(calls)
    operations = (
      lambda: io.write(b"late"),
      io.read,
      io._read_packet,
      lambda: io.ctrl_transfer(1, 2, 3, 4, 5),
    )
    for operation in operations:
      with pytest.raises(RuntimeError, match="not connected|setup"):
        await operation()
      assert calls == calls_after_exit


class TestUSBValidator(AnyioTestBase):
  async def test_full_legacy_read_needs_explicit_zlp_assumption_for_short_end(self) -> None:
    command = {
      "module": "usb",
      "device_id": "[0x1234:0x5678][][]",
      "action": "read",
      "data": "ABCD",
    }
    reader = mock.Mock()
    reader.next_command.side_effect = [
      command,
      {"module": "usb", "device_id": "[0x1234:0x5678][][]", "action": "close", "data": ""},
    ]
    validator = USBValidator(
      reader,
      "fake USB",
      0x1234,
      0x5678,
      read_endpoint_address=0x81,
      legacy_boundary_profiles={0x81: USBLegacyBoundaryProfile(0x81, 4, True)},
    )
    async with validator:
      with pytest.raises(ValidationError):
        await validator.read_to_boundary(boundary="short_packet_end")
      assert [mark["kind"] for mark in validator.legacy_inferred_marks[0x81]] == ["packet"]
    reader.next_command.side_effect = [command]
    assumed = USBValidator(
      reader,
      "fake USB",
      0x1234,
      0x5678,
      read_endpoint_address=0x81,
      legacy_boundary_profiles={0x81: USBLegacyBoundaryProfile(0x81, 4, True, True)},
    )
    async with assumed:
      assert await assumed.read_to_boundary(boundary="short_packet_end") == BoundaryRead(
        b"ABCD", False, True, False
      )

  async def test_profile_does_not_reinfer_observed_read_marks(self) -> None:
    reader = mock.Mock()
    reader.next_command.side_effect = [
      {
        "module": "usb",
        "device_id": "[0x1234:0x5678][][]",
        "action": "read",
        "data": "A",
        "data_hex": "41",
        "endpoint": 0x81,
        "marks": [{"offset": 0, "kind": "packet", "pending": False, "provenance": "observed"}],
      },
      {
        "module": "usb",
        "device_id": "[0x1234:0x5678][][]",
        "action": "read_to_boundary",
        "data": "BC",
        "data_hex": "4243",
        "endpoint": 0x81,
        "boundary": "packet",
        "started_at_boundary": False,
        "ended_at_boundary": True,
        "limit_reached": False,
      },
    ]
    validator = USBValidator(
      reader,
      "fake USB",
      0x1234,
      0x5678,
      read_endpoint_address=0x81,
      legacy_boundary_profiles={0x81: USBLegacyBoundaryProfile(0x81, 4, True)},
    )
    async with validator:
      assert await validator.read(size=1) == b"A"
      assert await validator.read_to_boundary() == BoundaryRead(b"BC", False, True, False)
    assert validator.legacy_inferred_marks == {}

  async def test_observed_immediate_boundary_replays_one_recorded_command(self) -> None:
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "usb",
      "device_id": "[0x1234:0x5678][][]",
      "action": "read_to_boundary",
      "data": "",
      "data_hex": "",
      "endpoint": 0x81,
      "boundary": "packet",
      "started_at_boundary": True,
      "ended_at_boundary": True,
      "limit_reached": False,
    }
    validator = USBValidator(reader, "fake USB", 0x1234, 0x5678, read_endpoint_address=0x81)
    async with validator:
      assert await validator.read_to_boundary(consume_immediate_boundary=False) == BoundaryRead(
        b"", True, True, False
      )
    assert reader.next_command.call_count == 1

  async def test_legacy_profile_mixes_byte_and_boundary_reads_without_losing_suffix(self) -> None:
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "usb",
      "device_id": "[0x1234:0x5678][][]",
      "action": "read",
      "data": "ABCDEF",
    }
    validator = USBValidator(
      reader,
      "fake USB",
      0x1234,
      0x5678,
      read_endpoint_address=0x81,
      legacy_boundary_profiles={0x81: USBLegacyBoundaryProfile(0x81, 4, True)},
    )
    async with validator:
      assert await validator.read(size=1) == b"A"
      assert await validator.read_to_boundary(boundary="packet") == BoundaryRead(
        b"BCD", False, True, False
      )
    assert reader.next_command.call_count == 1

  async def test_legacy_profile_infers_packet_and_short_end(self) -> None:
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "usb",
      "device_id": "[0x1234:0x5678][][]",
      "action": "read",
      "data": "ABCDEF",
    }
    profile = USBLegacyBoundaryProfile(0x81, 4, assume_uncapped_read=True)
    validator = USBValidator(
      reader,
      "fake USB",
      0x1234,
      0x5678,
      legacy_boundary_profiles={0x81: profile},
      read_endpoint_address=0x81,
    )
    async with validator:
      assert await validator.read_to_boundary(boundary="packet") == BoundaryRead(
        b"ABCD", True, True, False
      )
      assert await validator.read_to_boundary(boundary="short_packet_end") == BoundaryRead(
        b"EF", False, True, False
      )
      assert [
        (mark["offset"], mark["kind"], mark["provenance"])
        for mark in validator.legacy_inferred_marks[0x81]
      ] == [
        (4, "packet", "legacy_inferred"),
        (6, "packet", "legacy_inferred"),
        (6, "short_packet_end", "legacy_inferred"),
      ]
    assert reader.next_command.call_count == 1

  async def test_legacy_aggregated_capture_replays_in_requested_chunks(self) -> None:
    """A captured multi-packet reply cannot lose its suffix when read in chunks."""
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "usb",
      "device_id": "[0x1234:0x5678][][]",
      "action": "read",
      "data": "ABCDEF",
    }
    validator = USBValidator(reader, "fake USB", 0x1234, 0x5678)
    async with validator:
      assert await validator.read(size=4) == b"ABCD"
      assert await validator.read(size=4) == b"EF"
    assert reader.next_command.call_count == 1

  async def test_reconnect_discards_legacy_replay_remainder(self) -> None:
    """A new validation connection cannot serve old captured packet bytes."""
    reader = mock.Mock()
    reader.next_command.side_effect = [
      {"module": "usb", "device_id": "[0x1234:0x5678][][]", "action": "read", "data": "old"},
      {"module": "usb", "device_id": "[0x1234:0x5678][][]", "action": "read", "data": "new"},
    ]
    validator = USBValidator(reader, "fake USB", 0x1234, 0x5678)
    async with validator:
      assert await validator.read(size=1) == b"o"
      await validator.reconnect()
      assert await validator.read(size=3) == b"new"

  async def test_invalid_size_does_not_consume_replay(self) -> None:
    """An invalid read must leave the next captured reply untouched."""
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "usb",
      "device_id": "[0x1234:0x5678][][]",
      "action": "read",
      "data": "reply",
    }
    validator = USBValidator(reader, "fake USB", 0x1234, 0x5678)
    async with validator:
      with pytest.raises(ValueError):
        await validator.read(size=0)
      assert await validator.read(size=5) == b"reply"
    assert reader.next_command.call_count == 1

  async def test_validator_lifespan_and_control_transfer_are_async(self) -> None:
    reader = mock.Mock()
    reader.next_command.side_effect = [
      {
        "module": "usb",
        "device_id": "[0x1234:0x5678][][]",
        "action": "ctrl_transfer_request",
        "data": "1 2 3 4 5",
      },
      {
        "module": "usb",
        "device_id": "[0x1234:0x5678][][]",
        "action": "ctrl_transfer_response",
        "data": "3 1 4",
      },
    ]
    validator = USBValidator(reader, "fake USB", 0x1234, 0x5678)

    async with validator:
      assert await validator.ctrl_transfer(1, 2, 3, 4, 5) == bytearray([3, 1, 4])
