import contextlib
import json
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Optional
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.io import hid as hid_module
from pylabrobot.io.boundary import BoundaryDiscard, BoundaryRead, HIDReportTooLargeError
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.hid import HID, HIDCommand, HIDValidator, maximum_input_report_size
from pylabrobot.io.io import ContinuousConnectionIO
from pylabrobot.testing.concurrency import AnyioTestBase


class _FakeHIDException(Exception):
  pass


class _FakeDevice:
  def __init__(self, calls: list[Any], reads: Optional[list[object]] = None) -> None:
    self.calls = calls
    self.reads = list(reads or [])
    self.closed = False
    self.write_error: Optional[BaseException] = None

  def write(self, data: bytes) -> int:
    assert not self.closed
    self.calls.append(("write", data, threading.get_ident()))
    if self.write_error is not None:
      raise self.write_error
    return len(data)

  def read(self, size: int, timeout: int) -> bytes:
    assert not self.closed
    self.calls.append(("read", size, timeout, threading.get_ident()))
    if not self.reads:
      return b""
    result = self.reads.pop(0)
    if isinstance(result, BaseException):
      raise result
    assert isinstance(result, bytes)
    return result

  def close(self) -> None:
    self.calls.append(("close", threading.get_ident()))
    self.closed = True


def _new_hid() -> HID:
  return HID("fake HID", vid=0x1234, pid=0x5678, serial_number="serial", max_input_report_size=64)


@contextlib.contextmanager
def _fake_hid_api(
  device: _FakeDevice,
  calls: list[Any],
  before_open: Optional[Callable[[], None]] = None,
) -> Iterator[None]:
  def enumerate_devices() -> list[dict[str, object]]:
    calls.append(("enumerate", threading.get_ident()))
    return [
      {
        "path": b"fake-path",
        "vendor_id": 0x1234,
        "product_id": 0x5678,
        "serial_number": "serial",
        "release_number": 0,
        "manufacturer_string": "PyLabRobot",
        "product_string": "Fake HID",
        "usage_page": 0,
        "usage": 0,
        "interface_number": 0,
      }
    ]

  def open_device(*, path: bytes) -> _FakeDevice:
    assert path == b"fake-path"
    calls.append(("open", threading.get_ident()))
    if before_open is not None:
      before_open()
    return device

  fake_hid = SimpleNamespace(enumerate=enumerate_devices, Device=open_device)
  with (
    mock.patch.object(hid_module, "USE_HID", True),
    mock.patch.object(hid_module, "hid", fake_hid, create=True),
    mock.patch.object(hid_module, "HIDException", _FakeHIDException, create=True),
  ):
    yield


async def _wait_for_thread_event(event: threading.Event) -> None:
  with anyio.fail_after(2):
    while not event.is_set():
      await anyio.sleep(0)


class TestHIDLifespan(AnyioTestBase):
  async def test_command_annotations_do_not_evaluate_pep604_unions_on_import(self) -> None:
    """HIDCommand imports on Python 3.9, where evaluating `bool | None` would fail."""
    assert HIDCommand.__annotations__["started_at_boundary"] == "bool | None"

  async def test_capture_json_contains_observed_report_mark(self, tmp_path: Path) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"ab"])
    io = HID("fake HID", 0x1234, 0x5678, "serial", max_input_report_size=2)
    path = tmp_path / "hid-capture.json"
    with _fake_hid_api(device, calls):
      hid_module.capturer.start(path)
      try:
        async with io:
          assert (await io.read_to_boundary()).data == b"ab"
      finally:
        hid_module.capturer.stop()
    commands = json.loads(path.read_text())["commands"]
    assert commands[1]["marks"][-1] == {
      "offset": 2,
      "kind": "report",
      "pending": True,
      "provenance": "observed",
    }

  async def test_boundary_read_after_short_byte_read(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"abcdef"])
    io = HID("fake HID", 0x1234, 0x5678, "serial", max_input_report_size=6)
    with _fake_hid_api(device, calls):
      async with io:
        assert await io.read(1) == b"a"
        assert await io.read_to_boundary() == BoundaryRead(b"bcdef", False, True, False)
    assert [call[1] for call in calls if call[0] == "read"] == [7]

  async def test_oversized_report_requires_explicit_recovery(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"1234567", b"next"])
    io = HID("fake HID", 0x1234, 0x5678, "serial", max_input_report_size=6)
    with _fake_hid_api(device, calls):
      async with io:
        with pytest.raises(HIDReportTooLargeError, match="6"):
          await io.read(1)
        with pytest.raises(HIDReportTooLargeError):
          await io.read_to_boundary()
        with pytest.raises(HIDReportTooLargeError):
          await io.discard_to_boundary(max_bytes=2, consume_immediate_boundary=True)
        assert await io.discard_to_boundary(consume_immediate_boundary=True) == BoundaryDiscard(
          None, True, True, False
        )
        assert await io.read_to_boundary() == BoundaryRead(b"next", True, True, False)

  async def test_oversized_report_capture_replays_integrity_error(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"123"])
    io = HID("fake HID", 0x1234, 0x5678, "serial", max_input_report_size=2)
    commands: list[HIDCommand] = []
    with (
      _fake_hid_api(device, calls),
      mock.patch.object(hid_module.capturer, "record", commands.append),
    ):
      async with io:
        with pytest.raises(HIDReportTooLargeError):
          await io.read(1)
        assert await io.discard_to_boundary(consume_immediate_boundary=True) == BoundaryDiscard(
          None, True, True, False
        )
    assert commands[1].integrity == "oversized"
    assert commands[2].integrity == "recovered"

  async def test_boundary_capture_records_marks_and_flags(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"ab"])
    io = HID("fake HID", 0x1234, 0x5678, "serial", max_input_report_size=2)
    commands: list[HIDCommand] = []
    with (
      _fake_hid_api(device, calls),
      mock.patch.object(hid_module.capturer, "record", commands.append),
    ):
      async with io:
        assert await io.read(1) == b"a"
        assert await io.read_to_boundary() == BoundaryRead(b"b", False, True, False)
    assert commands[2].marks == [
      {"offset": 1, "kind": "report", "pending": True, "provenance": "observed"}
    ]
    assert commands[2].started_at_boundary is False
    assert commands[2].consume_immediate_boundary is True

  async def test_hard_write_failure_invalidates_the_current_handle(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_hid()

    with _fake_hid_api(device, calls):
      assert isinstance(io, ContinuousConnectionIO)
      async with io:
        device.write_error = _FakeHIDException("disconnected")
        with pytest.raises(_FakeHIDException, match="disconnected"):
          await io.write(b"first")
        device.write_error = None
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"second")

    assert device.closed
    assert len([call for call in calls if call[0] == "write"]) == 1
    replacement = _FakeDevice(calls)
    with _fake_hid_api(replacement, calls):
      async with io:
        await io.write(b"new lifespan")
    assert replacement.closed

  async def test_all_hidapi_calls_use_one_worker_thread(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"reply"])
    io = _new_hid()

    with _fake_hid_api(device, calls):
      async with io:
        assert isinstance(io, HasLifespan)
        await io.write(b"command")
        assert await io.read(5, timeout=17) == b"reply"

    worker_threads = [call[-1] for call in calls]
    assert len(set(worker_threads)) == 1
    assert worker_threads[0] != threading.get_ident()
    assert [call[0] for call in calls] == ["enumerate", "open", "write", "read", "close"]

  async def test_cancelled_open_waits_for_open_and_close(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    open_started = threading.Event()
    release_open = threading.Event()
    scopes: list[anyio.CancelScope] = []
    exited = anyio.Event()

    def block_open() -> None:
      open_started.set()
      release_open.wait(timeout=2)

    async def enter_resource() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with io:
            pass
        finally:
          exited.set()

    io = _new_hid()
    with _fake_hid_api(device, calls, before_open=block_open):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_resource)
        await _wait_for_thread_event(open_started)
        scopes[0].cancel()
        await anyio.sleep(0)
        assert not exited.is_set()
        release_open.set()
        await exited.wait()

    assert [call[0] for call in calls][-2:] == ["open", "close"]
    assert calls[-1][-1] == calls[-2][-1]
    assert io.device is None
    assert io._executor is None

  async def test_setup_failure_closes_a_partial_device(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_hid()

    def fail_after_device_creation() -> None:
      io.device = device  # type: ignore[assignment]
      calls.append(("open", threading.get_ident()))
      raise RuntimeError("open failed")

    io._setup_sync = fail_after_device_creation  # type: ignore[method-assign]
    with (
      mock.patch.object(hid_module, "USE_HID", True),
      pytest.raises(RuntimeError, match="open failed"),
    ):
      async with io:
        pass

    assert [call[0] for call in calls] == ["open", "close"]
    assert calls[0][-1] == calls[1][-1]
    assert io.device is None
    assert io._executor is None

  async def test_exit_awaits_executor_shutdown(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_hid()
    worker_started = threading.Event()
    release_worker = threading.Event()
    lifespan_exited = anyio.Event()

    def block_worker() -> None:
      worker_started.set()
      release_worker.wait(timeout=2)

    async def use_resource() -> None:
      async with io:
        assert io._executor is not None
        io._executor.submit(block_worker)
        await _wait_for_thread_event(worker_started)
        io.device = None
      lifespan_exited.set()

    with _fake_hid_api(device, calls):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_resource)
        await _wait_for_thread_event(worker_started)
        await anyio.sleep(0)
        assert not lifespan_exited.is_set()
        release_worker.set()
        await lifespan_exited.wait()

    assert io._executor is None

  async def test_capture_records_open_write_read_and_close(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"\x03\x04"])
    io = _new_hid()
    commands: list[HIDCommand] = []

    with (
      _fake_hid_api(device, calls),
      mock.patch.object(hid_module.capturer, "record", commands.append),
    ):
      async with io:
        assert await io.write(b"\x01\x02", report_id=b"\x07") == 3
        assert await io.read(2, timeout=9) == b"\x03\x04"

    assert [
      {
        key: value
        for key, value in asdict(command).items()
        if key in ("module", "device_id", "action", "data")
      }
      for command in commands
    ] == [
      {"module": "hid", "device_id": "4660:22136:serial", "action": "open", "data": ""},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "write", "data": "070102"},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "read", "data": "0304"},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "close", "data": ""},
    ]

  async def test_hid_success_exception_is_an_internal_empty_poll(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[_FakeHIDException("Success")])
    io = _new_hid()
    commands: list[HIDCommand] = []

    with (
      _fake_hid_api(device, calls),
      mock.patch.object(hid_module.capturer, "record", commands.append),
    ):
      async with io:
        commands.clear()
        with pytest.warns(DeprecationWarning), pytest.raises(TimeoutError):
          await io.read(8, timeout=11)

    assert [command.action for command in commands] == ["close"]

  async def test_read_waits_through_empty_reports_until_data_arrives(self) -> None:
    """HIDAPI's empty poll must not become a successful public read."""
    calls: list[Any] = []
    device = _FakeDevice(calls, reads=[b"", b"reply"])
    io = _new_hid()
    with _fake_hid_api(device, calls):
      async with io:
        assert await io.read(8) == b"reply"

  async def test_write_checks_report_id_in_short_write_count(self) -> None:
    """HIDAPI counts the report ID as part of a submitted report."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    device.write = lambda data: len(data) - 1  # type: ignore[method-assign]
    io = _new_hid()
    with _fake_hid_api(device, calls):
      async with io:
        with pytest.raises(OSError, match="short write"):
          await io.write(b"payload", report_id=b"\x02")

  async def test_cancelled_worker_read_retains_report(self) -> None:
    """A report acquired during cancellation remains available exactly once."""
    calls: list[Any] = []
    device = _FakeDevice(calls)
    started = threading.Event()
    release = threading.Event()

    def read(size: int, timeout: int) -> bytes:
      started.set()
      release.wait(timeout=2)
      return b"kept"

    device.read = read  # type: ignore[method-assign]
    io = _new_hid()
    returned: list[bytes] = []
    scopes: list[anyio.CancelScope] = []

    async def read_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        returned.append(await io.read(4))

    with _fake_hid_api(device, calls):
      async with io:
        async with anyio.create_task_group() as group:
          group.start_soon(read_in_scope)
          await _wait_for_thread_event(started)
          scopes[0].cancel()
          release.set()
        assert returned == []
        assert await io.read(4) == b"kept"

  async def test_operations_after_exit_do_not_reach_hidapi(self) -> None:
    calls: list[Any] = []
    device = _FakeDevice(calls)
    io = _new_hid()

    with _fake_hid_api(device, calls):
      async with io:
        pass

      calls_after_exit = list(calls)
      for operation in (lambda: io.write(b"late"), lambda: io.read(1, timeout=1)):
        with pytest.raises(RuntimeError, match="not active"):
          await operation()
        assert calls == calls_after_exit


class TestHIDValidator(AnyioTestBase):
  async def test_legacy_report_larger_than_assumed_maximum_is_rejected(self) -> None:
    reader = mock.Mock()
    reader.next_command.side_effect = [
      {"module": "hid", "device_id": "4660:22136:serial", "action": "open", "data": ""},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "read", "data": "616263"},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "close", "data": ""},
    ]
    validator = HIDValidator(reader, "fake HID", 0x1234, 0x5678, "serial", legacy_report_size=2)
    async with validator:
      with pytest.raises(ValidationError, match="exceeds assumed"):
        await validator.read(3)

  async def test_legacy_boundary_replay_requires_assumption(self) -> None:
    commands = [
      {"module": "hid", "device_id": "4660:22136:serial", "action": "open", "data": ""},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "read", "data": "6162"},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "close", "data": ""},
    ]
    reader = mock.Mock()
    reader.next_command.side_effect = commands
    validator = HIDValidator(reader, "fake HID", 0x1234, 0x5678, "serial", legacy_report_size=2)
    async with validator:
      assert await validator.read_to_boundary() == BoundaryRead(b"ab", True, True, False)
      assert validator.legacy_inferred_marks == [
        {
          "offset": 2,
          "kind": "report",
          "provenance": "legacy_inferred",
          "assumptions": {"legacy_report_size": 2},
        }
      ]

  async def test_validator_cancellation_and_size_checks_keep_report(self) -> None:
    """A HID report is consumed only by an admitted positive-size read."""
    reader = mock.Mock()
    reader.next_command.side_effect = [
      {"module": "hid", "device_id": "4660:22136:serial", "action": "open", "data": ""},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "read", "data": "6162"},
      {"module": "hid", "device_id": "4660:22136:serial", "action": "close", "data": ""},
    ]
    validator = HIDValidator(reader, "fake HID", 0x1234, 0x5678, "serial")
    async with validator:
      with pytest.raises(ValueError):
        await validator.read(0)
      with anyio.CancelScope() as scope:
        scope.cancel()
        await validator.read(2)
      assert await validator.read(2) == b"ab"
    assert reader.next_command.call_count == 3

  async def test_validator_lifespan_rejects_wrong_device(self) -> None:
    reader = mock.Mock()
    reader.next_command.side_effect = [
      {"device_id": "other", "action": "open", "data": ""},
      {"device_id": "other", "action": "close", "data": ""},
    ]
    validator = HIDValidator(reader, "fake HID", 0x1234, 0x5678, "serial")

    with pytest.raises(ValidationError):
      async with validator:
        pass

    assert reader.next_command.call_count == 1


def test_hid_command_accepts_serialized_module_and_data() -> None:
  command = HIDCommand(module="hid", device_id="device", action="open", data="")
  assert {
    key: value
    for key, value in asdict(command).items()
    if key in ("module", "device_id", "action", "data")
  } == {
    "module": "hid",
    "device_id": "device",
    "action": "open",
    "data": "",
  }


def test_report_descriptor_tracks_ids_and_push_pop() -> None:
  descriptor = bytes(
    [
      0x75,
      8,
      0x95,
      2,
      0x85,
      1,
      0x81,
      2,
      0xA4,
      0x95,
      6,
      0x85,
      2,
      0x81,
      2,
      0xB4,
      0x81,
      2,
    ]
  )
  assert maximum_input_report_size(descriptor) == 7
  with pytest.raises(ValueError, match="Pop"):
    maximum_input_report_size(bytes([0xB4]))
