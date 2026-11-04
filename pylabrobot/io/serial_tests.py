import threading
import types
from typing import Any, Callable, Coroutine, Optional
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.events import EventBus, use_event_bus
from pylabrobot.io import serial as serial_module
from pylabrobot.io.io import ContinuousConnectionIO
from pylabrobot.io.serial import Serial, SerialCommand, SerialValidator, find_serial_ports
from pylabrobot.testing.concurrency import AnyioTestBase


class _FakeSerial:
  """Hardware-free stand-in for the pyserial boundary."""

  def __init__(self, **kwargs: Any) -> None:
    self.constructor_arguments = kwargs
    self.calls: list[object] = []
    self.closed = threading.Event()
    self.is_open = True
    self.timeout = kwargs["timeout"]
    self.in_waiting = 0
    self._dtr = False
    self._rts = True
    self.read_results = [b"reply"]
    self.readline_results = [b"line\n"]
    self.started: Optional[threading.Event] = None
    self.release: Optional[threading.Event] = None
    self.write_error: Optional[BaseException] = None

  def close(self) -> None:
    self.calls.append("close")
    self.is_open = False
    self.closed.set()

  def write(self, data: bytes) -> int:
    if self.started is not None:
      self.started.set()
    if self.release is not None:
      self.release.wait()
    self.calls.append(("write", data))
    if self.write_error is not None:
      raise self.write_error
    return len(data)

  def read(self, num_bytes: int) -> bytes:
    self.calls.append(("read", num_bytes))
    return self.read_results.pop(0) if self.read_results else b""

  def readline(self) -> bytes:
    self.calls.append("readline")
    return self.readline_results.pop(0)

  def send_break(self, duration: float) -> None:
    self.calls.append(("send_break", duration))

  def reset_input_buffer(self) -> None:
    self.calls.append("reset_input_buffer")

  def reset_output_buffer(self) -> None:
    self.calls.append("reset_output_buffer")

  @property
  def dtr(self) -> bool:
    return self._dtr

  @dtr.setter
  def dtr(self, value: bool) -> None:
    self._dtr = value

  @property
  def rts(self) -> bool:
    return self._rts

  @rts.setter
  def rts(self, value: bool) -> None:
    self._rts = value


class _FailsAfterOpeningSerial(Serial):
  def _setup_sync(self) -> str:
    super()._setup_sync()
    raise RuntimeError("open failed after acquiring the port")


def _fake_serial_module(serial_factory: object) -> mock.Mock:
  """Build the optional pyserial boundary used by one test."""

  class SerialTimeoutException(Exception):
    pass

  return mock.Mock(
    Serial=serial_factory,
    SerialException=Exception,
    SerialTimeoutException=SerialTimeoutException,
  )


async def _wait_for_serial_thread(event: threading.Event) -> None:
  with anyio.fail_after(2):
    while not event.is_set():
      await anyio.sleep(0)


class TestSerialLifespan(AnyioTestBase):
  async def test_read_waits_through_empty_polls_without_waiting_for_full_size(self) -> None:
    """A serial read takes the first available byte even when a larger maximum was requested."""
    device = _FakeSerial(timeout=1)
    device.read_results = [b"", b"x"]
    io = Serial("serial", port="fake")
    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io:
        assert await io.read(10) == b"x"
    assert ("read", 1) in device.calls

  async def test_short_write_is_rejected(self) -> None:
    """A reported short write cannot be logged as a completed command."""
    device = _FakeSerial(timeout=1)
    device.write = lambda data: len(data) - 1  # type: ignore[method-assign]
    io = Serial("serial", port="fake")
    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io:
        with pytest.raises(OSError, match="short write"):
          await io.write(b"payload")

  async def test_read_drains_only_immediately_available_bytes(self) -> None:
    """Drain the kernel queue after the first byte without waiting for the requested maximum."""
    device = _FakeSerial(timeout=1)
    device.read_results = [b"a", b"bc"]
    device.in_waiting = 2
    io = Serial("serial", port="fake")
    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io:
        assert await io.read(10) == b"abc"
    assert device.calls[:2] == [("read", 1), ("read", 2)]

  async def test_explicit_read_timeout_warns_and_bounds_idle_read(self) -> None:
    """Compatibility deadlines remain effective despite finite internal polling."""
    with pytest.warns(DeprecationWarning):
      io = Serial("serial", port="fake", timeout=0.01)
    device = _FakeSerial(timeout=1)
    device.read_results = []
    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io:
        with pytest.raises(TimeoutError):
          await io.read(1)

  async def test_nonpositive_read_rejected_before_open(self) -> None:
    """Invalid sizes cannot reach a device or appear as successful empty reads."""
    io = Serial("serial", port="fake")
    for size in (0, -1):
      with pytest.raises(ValueError):
        await io.read(size)

  async def test_timeout_during_worker_read_retains_received_bytes(self) -> None:
    """A finite public deadline cannot discard bytes returned by a blocking worker."""
    started = threading.Event()
    release = threading.Event()
    device = _FakeSerial(timeout=1)

    def delayed_read(num_bytes: int) -> bytes:
      started.set()
      release.wait(timeout=2)
      return b"X"

    device.read = delayed_read  # type: ignore[method-assign]
    with pytest.warns(DeprecationWarning):
      io = Serial("serial", port="fake", timeout=0.01)

    async def release_later() -> None:
      with anyio.fail_after(2):
        while not started.is_set():
          await anyio.sleep(0)
      await anyio.sleep(0.02)
      release.set()

    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io, anyio.create_task_group() as group:
        group.start_soon(release_later)
        with pytest.raises(TimeoutError):
          await io.read(1)
        assert await io.read(1) == b"X"

  async def test_cancelled_read_bytes_are_used_by_readline_and_leave_next_line(self) -> None:
    started = threading.Event()
    release = threading.Event()
    device = _FakeSerial(timeout=1)

    def delayed_read(num_bytes: int) -> bytes:
      started.set()
      release.wait(timeout=2)
      return b"one\ntwo\n"

    device.read = delayed_read  # type: ignore[method-assign]
    with pytest.warns(DeprecationWarning):
      io = Serial("serial", port="fake", timeout=0.01)

    async def release_later() -> None:
      await _wait_for_serial_thread(started)
      await anyio.sleep(0.02)
      release.set()

    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(lambda **kwargs: device), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io, anyio.create_task_group() as group:
        group.start_soon(release_later)
        with pytest.raises(TimeoutError):
          await io.read(1)
        assert await io.readline() == b"one\n"
        assert await io.read(4) == b"two\n"
    assert "readline" not in device.calls

  async def test_readline_waits_beyond_internal_poll_and_preserves_partial_on_cancellation(
    self,
  ) -> None:
    device = _FakeSerial(timeout=1)
    started = threading.Event()
    release = threading.Event()
    reads = 0

    def delayed_read(num_bytes: int) -> bytes:
      nonlocal reads
      reads += 1
      if reads == 1:
        return b"part"
      if reads == 2:
        started.set()
        release.wait(timeout=2)
        return b""
      return b"end\n"

    device.read = delayed_read  # type: ignore[method-assign]
    io = Serial("serial", port="fake")

    async def release_later() -> None:
      await _wait_for_serial_thread(started)
      await anyio.sleep(0.02)
      release.set()

    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(lambda **kwargs: device), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io, anyio.create_task_group() as group:
        group.start_soon(release_later)
        with anyio.move_on_after(0.01) as scope:
          await io.readline()
        assert scope.cancel_called
        assert await io.readline() == b"partend\n"

  async def test_readline_waits_for_delayed_newline_across_internal_polls(self) -> None:
    device = _FakeSerial(timeout=1)
    device.read_results = [b"a", b"", b"b\n"]
    io = Serial("serial", port="fake")
    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(lambda **kwargs: device), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io:
        with anyio.fail_after(1):
          assert await io.readline() == b"ab\n"

  async def test_hard_write_failure_invalidates_connection_until_next_lifespan(self) -> None:
    devices: list[_FakeSerial] = []

    def open_serial(**kwargs: Any) -> _FakeSerial:
      device = _FakeSerial(**kwargs)
      devices.append(device)
      return device

    io = Serial("fake serial", port="fake")
    with (
      mock.patch.object(serial_module, "serial", _fake_serial_module(open_serial), create=True),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      assert isinstance(io, ContinuousConnectionIO)
      async with io:
        devices[0].write_error = OSError("disconnected")
        with pytest.raises(OSError, match="disconnected"):
          await io.write(b"first")
        devices[0].write_error = None
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"second")
      assert devices[0].closed.is_set()
      async with io:
        await io.write(b"new session")

    assert len(devices) == 2
    assert ("write", b"second") not in devices[0].calls

  async def test_write_timeout_does_not_imply_connection_loss(self) -> None:
    device = _FakeSerial(timeout=1)
    io = Serial("fake serial", port="fake")

    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io:
        device.write_error = serial_module.serial.SerialTimeoutException("timed out")
        with pytest.raises(serial_module.serial.SerialTimeoutException):
          await io.write(b"first")
        device.write_error = None
        await io.write(b"second")

    assert ("write", b"second") in device.calls

  async def test_transport_behavior_is_preserved(self) -> None:
    devices: list[_FakeSerial] = []

    def open_serial(**kwargs: Any) -> _FakeSerial:
      device = _FakeSerial(**kwargs)
      device.read_results = [b"reply", b"line\n"]
      devices.append(device)
      return device

    io = Serial(
      human_readable_device_name="robot arm",
      port="loop://characterization",
      baudrate=19200,
      bytesize=7,
      parity="E",
      stopbits=2,
      write_timeout=3,
      timeout=4,
      rtscts=True,
      dsrdtr=True,
      xonxoff=True,
    )
    commands: list[SerialCommand] = []
    events: list[Any] = []
    bus = EventBus()
    bus.subscribe(events.append)

    with (
      mock.patch.object(serial_module, "serial", _fake_serial_module(open_serial), create=True),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
      mock.patch.object(serial_module.capturer, "record", commands.append),
      use_event_bus(bus),
    ):
      async with io:
        assert io.port == "loop://characterization"
        assert io.get_read_timeout() == 0.1
        with io.temporary_timeout(0.25):
          assert io.get_read_timeout() == 0.25
        assert io.get_read_timeout() == 0.1
        await io.write(b"command")
        assert await io.read(5) == b"reply"
        assert await io.readline() == b"line\n"
        await io.send_break(0.5)
        await io.reset_input_buffer()
        await io.reset_output_buffer()
        assert io.dtr is False
        io.dtr = True
        assert io.rts is True
        io.rts = False

    assert devices[0].constructor_arguments == {
      "port": "loop://characterization",
      "baudrate": 19200,
      "bytesize": 7,
      "parity": "E",
      "stopbits": 2,
      "write_timeout": 3,
      "timeout": 0.1,
      "rtscts": True,
      "dsrdtr": True,
      "xonxoff": True,
    }
    assert [(command.action, command.data) for command in commands] == [
      ("write", "command"),
      ("read", "reply"),
      ("readline", "line\n"),
      ("send_break", "0.5"),
      ("reset_input_buffer", ""),
      ("reset_output_buffer", ""),
      ("get_dtr", "False"),
      ("set_dtr", "True"),
      ("get_rts", "True"),
      ("set_rts", "False"),
    ]
    assert [(event.name, event.data) for event in events] == [
      (
        "io.write",
        {
          "transport": "serial",
          "device": "robot arm",
          "device_id": "loop://characterization",
          "data": "command",
        },
      ),
      (
        "io.read",
        {
          "transport": "serial",
          "device": "robot arm",
          "device_id": "loop://characterization",
          "data": "reply",
        },
      ),
      (
        "io.read",
        {
          "transport": "serial",
          "device": "robot arm",
          "device_id": "loop://characterization",
          "data": "line\n",
        },
      ),
    ]
    assert io.serialize() == {
      "human_readable_device_name": "robot arm",
      "port": "loop://characterization",
      "baudrate": 19200,
      "bytesize": 7,
      "parity": "E",
      "stopbits": 2,
      "write_timeout": 3,
      "timeout": 4,
      "rtscts": True,
      "dsrdtr": True,
    }

  async def test_empty_reads_do_not_emit_events_or_capture_commands(self) -> None:
    device = _FakeSerial(timeout=1)
    device.read_results = [b""]
    device.readline_results = [b""]
    commands: list[SerialCommand] = []
    events: list[Any] = []
    bus = EventBus()
    bus.subscribe(events.append)
    io = Serial(human_readable_device_name="quiet device", port="quiet")

    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
      mock.patch.object(serial_module.capturer, "record", commands.append),
      use_event_bus(bus),
    ):
      async with io:
        with anyio.move_on_after(0.01) as scope:
          await io.read(1)
        assert scope.cancel_called
        with anyio.move_on_after(0.01) as line_scope:
          await io.readline()
        assert line_scope.cancel_called

    assert commands == []
    assert events == []

  async def test_failed_open_closes_the_acquired_port(self) -> None:
    device = _FakeSerial(timeout=1)
    io = _FailsAfterOpeningSerial(human_readable_device_name="test", port="failure")

    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      with pytest.raises(RuntimeError, match="open failed after acquiring the port"):
        async with io:
          pytest.fail("failed open entered the lifespan body")

    assert device.closed.is_set()
    assert io._ser is None

  async def test_cancelled_open_closes_the_acquired_port(self) -> None:
    started = threading.Event()
    release = threading.Event()
    devices: list[_FakeSerial] = []
    scopes: list[anyio.CancelScope] = []
    trace: list[str] = []
    exited = anyio.Event()
    io = Serial(human_readable_device_name="test", port="cancel-open")
    assert isinstance(io, HasLifespan)

    def open_serial(**kwargs: Any) -> _FakeSerial:
      device = _FakeSerial(**kwargs)
      devices.append(device)
      started.set()
      release.wait()
      return device

    async def open_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with io:
          await anyio.sleep_forever()
      assert devices[0].closed.is_set()
      trace.append("exit")
      exited.set()

    with anyio.fail_after(2):
      with (
        mock.patch.object(serial_module, "serial", _fake_serial_module(open_serial), create=True),
        mock.patch.object(serial_module, "HAS_SERIAL", True),
      ):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(open_in_scope)
          await anyio.to_thread.run_sync(started.wait)
          scopes[0].cancel()
          await anyio.sleep(0)
          assert not exited.is_set()
          release.set()
          await exited.wait()

    assert devices[0].calls == ["close"]
    assert trace == ["exit"]
    assert io._ser is None

  async def test_cancellation_waits_for_an_in_flight_worker_call(self) -> None:
    started = threading.Event()
    release = threading.Event()
    device = _FakeSerial(timeout=1)
    device.started = started
    device.release = release
    scopes: list[anyio.CancelScope] = []
    trace: list[str] = []
    exited = anyio.Event()
    io = Serial(human_readable_device_name="test", port="blocking")
    assert isinstance(io, HasLifespan)

    async def write_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with io:
          await io.write(b"blocked")
      assert device.closed.is_set()
      trace.append("exit")
      exited.set()

    with anyio.fail_after(2):
      with (
        mock.patch.object(
          serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
        ),
        mock.patch.object(serial_module, "HAS_SERIAL", True),
      ):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(write_in_scope)
          await anyio.to_thread.run_sync(started.wait)
          scopes[0].cancel()
          await anyio.sleep(0)
          assert not exited.is_set()
          assert not device.closed.is_set()
          release.set()
          await exited.wait()

    assert device.calls == [("write", b"blocked"), "close"]
    assert trace == ["exit"]

  async def test_operation_failure_propagates_after_lifespan_cleanup(self) -> None:
    failure = RuntimeError("write failed")
    device = _FakeSerial(timeout=1)
    device.write_error = failure
    exit_observations: list[bool] = []
    io = Serial(human_readable_device_name="test", port="write-failure")

    async def write_in_lifespan() -> None:
      try:
        async with io:
          await io.write(b"fails")
      finally:
        exit_observations.append(device.closed.is_set())

    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      with pytest.raises(RuntimeError) as error:
        await write_in_lifespan()

    assert error.value is failure
    assert device.calls == [("write", b"fails"), "close"]
    assert exit_observations == [True]
    assert io._ser is None

  async def test_calls_after_lifespan_exit_do_not_touch_the_device(self) -> None:
    device = _FakeSerial(timeout=1)
    io = Serial(human_readable_device_name="test", port="closed")

    with (
      mock.patch.object(
        serial_module, "serial", _fake_serial_module(mock.Mock(return_value=device)), create=True
      ),
      mock.patch.object(serial_module, "HAS_SERIAL", True),
    ):
      async with io:
        pass

    calls_after_close = list(device.calls)
    operations: tuple[Callable[[], Coroutine[Any, Any, object]], ...] = (
      lambda: io.write(b"late"),
      lambda: io.read(1),
      io.readline,
      lambda: io.send_break(0.1),
      io.reset_input_buffer,
      io.reset_output_buffer,
    )
    for operation in operations:
      with pytest.raises(RuntimeError, match=r"Enter lifespan\(\) first"):
        await operation()
      assert device.calls == calls_after_close

  async def test_validator_has_a_no_op_lifespan(self) -> None:
    validator = SerialValidator(
      mock.Mock(), human_readable_device_name="validator", port="recorded"
    )

    async with validator:
      assert validator.is_active

    assert not validator.is_active

  async def test_validator_short_read_and_cancelled_replay(self) -> None:
    """Replay accepts short data without consuming it for an invalid or cancelled call."""
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "serial",
      "device_id": "recorded",
      "action": "read",
      "data": "abc",
    }
    validator = SerialValidator(reader, "validator", port="recorded")
    async with validator:
      with pytest.raises(ValueError):
        await validator.read(0)
      with anyio.CancelScope() as scope:
        scope.cancel()
        await validator.read(5)
      assert await validator.read(5) == b"abc"
    assert reader.next_command.call_count == 1


class TestSerialDiscovery(AnyioTestBase):
  async def test_find_serial_ports_matches_multiple_vid_pid_pairs(self) -> None:
    ports = [
      types.SimpleNamespace(device="/dev/tty-one", vid=0x1234, pid=0x0001),
      types.SimpleNamespace(device="/dev/tty-two", vid=0x1234, pid=0x0002),
      types.SimpleNamespace(device="/dev/tty-other", vid=0x9999, pid=0x0001),
    ]
    fake_serial = types.SimpleNamespace(
      tools=types.SimpleNamespace(
        list_ports=types.SimpleNamespace(comports=lambda: ports),
      )
    )

    with (
      mock.patch.object(serial_module, "HAS_SERIAL", True),
      mock.patch.object(serial_module, "serial", fake_serial, create=True),
    ):
      discovered = find_serial_ports({(0x1234, 0x0001), (0x1234, 0x0002)})

    assert discovered == ["/dev/tty-one", "/dev/tty-two"]

  async def test_find_serial_ports_reports_missing_pyserial(self) -> None:
    with mock.patch.object(serial_module, "HAS_SERIAL", False):
      with pytest.raises(RuntimeError, match="pyserial is not installed"):
        find_serial_ports({(0x1234, 0x0001)})
