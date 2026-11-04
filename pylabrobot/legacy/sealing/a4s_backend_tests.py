from typing import Any, Awaitable, Callable, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.sealing.a4s_backend import A4SBackend
from pylabrobot.testing.concurrency import AnyioTestBase

ResponseEffect = Union[str, Callable[[], Awaitable[str]]]


def status_response(
  temperature: int = 250,
  system: int = 0,
  heater: int = 1,
  error: int = 0,
  warning: int = 0,
  sensors: int = 0,
  remaining: int = 0,
) -> str:
  """Build one literal-format A4S status frame for the software transport."""
  return f"*T={temperature},{system},{heater},{error},{warning},{sensors},{remaining}zzzz!"


class FakeSerialIO(HasLifespan):
  """Replay A4S byte streams and record lifecycle and wire events."""

  def __init__(
    self,
    responses: Optional[List[ResponseEffect]] = None,
    events: Optional[List[str]] = None,
    exit_error: Optional[BaseException] = None,
    write_signals: Optional[dict[bytes, anyio.Event]] = None,
    response_labels: Optional[List[Optional[str]]] = None,
  ) -> None:
    super().__init__()
    self.responses = list(responses or [])
    self.events = events if events is not None else []
    self.exit_error = exit_error
    self.write_signals = write_signals or {}
    self.response_labels = list(response_labels or [None] * len(self.responses))
    self.writes: List[bytes] = []
    self._rx = bytearray()
    self._active_response_label: Optional[str] = None

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    self.writes.append(data)
    self.events.append(f"write:{data.decode('ascii')}")
    signal = self.write_signals.get(data)
    if signal is not None:
      signal.set()

  async def read(self, num_bytes: int = 1) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self._rx and self.responses:
      effect = self.responses.pop(0)
      self._active_response_label = self.response_labels.pop(0)
      response = await effect() if callable(effect) else effect
      self._rx.extend(("\r" + response + "\r").encode("ascii"))
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    if not self._rx and self._active_response_label is not None:
      self.events.append(f"response:{self._active_response_label}.completed")
      self._active_response_label = None
    return out


def make_backend(
  responses: Optional[List[ResponseEffect]] = None,
  events: Optional[List[str]] = None,
  exit_error: Optional[BaseException] = None,
  timeout: int = 1,
  write_signals: Optional[dict[bytes, anyio.Event]] = None,
  response_labels: Optional[List[Optional[str]]] = None,
) -> tuple[A4SBackend, FakeSerialIO]:
  """Build a legacy A4S backend with a hardware-free serial transport."""
  backend = A4SBackend("FAKE", timeout=timeout)
  fake_io = FakeSerialIO(responses, events, exit_error, write_signals, response_labels)
  backend.io = cast(Serial, fake_io)
  return backend, fake_io


def commands(fake_io: FakeSerialIO) -> List[str]:
  """Return the ASCII commands written to the fake transport."""
  return [data.decode("ascii") for data in fake_io.writes]


class TestLegacyA4SProtocol(AnyioTestBase):
  async def test_system_reset_and_heater_commands(self) -> None:
    backend, fake_io = make_backend([status_response(), status_response(), status_response()])

    await backend.system_reset()
    await backend.set_heater(True)
    await backend.set_heater(False)

    assert commands(fake_io) == ["*00SR=zz!", "*00H1ZZ", "*00H0ZZ"]

  async def test_status_parses_all_fields(self) -> None:
    backend, _ = make_backend(
      [status_response(temperature=375, system=4, heater=3, warning=7, sensors=0x55, remaining=12)]
    )

    status = await backend.get_status()

    assert status.current_temperature == 37.5
    assert status.system_status is A4SBackend.Status.SystemStatus.finish
    assert status.heater_block_status is A4SBackend.Status.HeaterBlockStatus.cooling
    assert status.warning_code == 7
    assert status.remaining_time == 12
    assert status.sensor_status == A4SBackend.Status.SensorStatus(
      True, False, True, False, True, False, True
    )

  async def test_status_error_raises_with_response(self) -> None:
    backend, _ = make_backend([status_response(error=9)])

    with pytest.raises(RuntimeError, match="response .*9"):
      await backend.get_status()

  async def test_seal_preserves_command_order(self) -> None:
    backend, fake_io = make_backend(
      [
        status_response(temperature=600),
        status_response(system=1),
        status_response(system=4),
      ]
    )

    await backend.seal(60, 2)

    seal_commands = commands(fake_io)
    assert seal_commands == ["*00DH=0060zz!", "*00DT=0020zz!", "*00GS=zz!"]

  async def test_shuttle_commands_wait_for_sensor(self) -> None:
    backend, fake_io = make_backend(
      [
        status_response(sensors=0),
        status_response(sensors=2),
        status_response(sensors=2),
        status_response(),
      ]
    )

    await backend.open()
    await backend.close()

    assert commands(fake_io) == ["*00MO=zz!", "*00MC=zz!"]

  async def test_response_status_shuttle_and_temperature_deadlines(self) -> None:
    response_backend, _ = make_backend(timeout=0)
    with pytest.raises(TimeoutError, match="response"):
      await response_backend._read_message()

    status_backend, _ = make_backend([status_response(system=2)], timeout=0)
    with pytest.raises(TimeoutError, match="response"):
      await status_backend._wait_for_status({A4SBackend.Status.SystemStatus.idle})

    shuttle_backend, _ = make_backend([status_response()], timeout=0)
    with pytest.raises(TimeoutError, match="shuttle open sensor"):
      await shuttle_backend._wait_for_shuttle_open_sensor(True, timeout=0)

    temperature_backend, _ = make_backend([status_response(temperature=200)], timeout=0)
    with pytest.raises(TimeoutError, match="temperature"):
      await temperature_backend._wait_for_temperature(50, timeout=0)


class TestLegacyA4SLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert A4SBackend.setup is HasLifespan.setup
    assert A4SBackend.stop is HasLifespan.stop

  async def test_normal_exit_disables_heater_before_serial_close(self) -> None:
    events: List[str] = []
    backend, fake_io = make_backend([status_response(), status_response()], events)

    async with backend:
      events.append("body")

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-2:] == ["write:*00H0ZZ", "serial.exit"]

  async def test_reset_failure_still_disables_heater_and_closes(self) -> None:
    events: List[str] = []
    backend, fake_io = make_backend([status_response(error=3), status_response()], events)

    with pytest.raises(RuntimeError, match="response"):
      async with backend:
        pytest.fail("failed reset reached the body")

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-2:] == ["write:*00H0ZZ", "serial.exit"]

  async def test_body_failure_runs_heater_cleanup(self) -> None:
    events: List[str] = []
    backend, fake_io = make_backend([status_response(), status_response()], events)

    with pytest.raises(RuntimeError, match="body failed"):
      async with backend:
        raise RuntimeError("body failed")

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-2:] == ["write:*00H0ZZ", "serial.exit"]

  async def test_body_cancellation_finishes_heater_cleanup_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    backend, fake_io = make_backend([status_response(), status_response()], events)

    async def use_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("operation.cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-3:] == ["write:*00H0ZZ", "serial.exit", "operation.cancelled"]

  async def test_pending_cancellation_prevents_heater_write(self) -> None:
    backend, fake_io = make_backend([status_response()])
    returned = False

    with anyio.CancelScope() as scope:
      scope.cancel()
      await backend.set_heater(True)
      returned = True

    assert not returned
    assert commands(fake_io) == []

  async def test_heater_write_reconciles_one_status_before_cancellation(self) -> None:
    events: List[str] = []
    heater_written = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    backend, fake_io = make_backend(
      [status_response(), status_response(), status_response()],
      events,
      write_signals={b"*00H1ZZ": heater_written},
      response_labels=[None, "operation", "cleanup"],
    )

    async def heat() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend:
          try:
            await backend.set_heater(True)
          except anyio.get_cancelled_exc_class():
            events.append("operation.cancelled")
            raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(heat)
      await heater_written.wait()
      scopes[0].cancel()

    assert commands(fake_io) == ["*00SR=zz!", "*00H1ZZ", "*00H0ZZ"]
    assert events.index("response:operation.completed") < events.index("operation.cancelled")
    assert events.index("operation.cancelled") < events.index("response:cleanup.completed")

  async def test_seal_write_reconciles_one_status_before_cancellation(self) -> None:
    events: List[str] = []
    seal_written = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    backend, fake_io = make_backend(
      [
        status_response(),
        status_response(temperature=600),
        status_response(system=1),
        status_response(),
      ],
      events,
      write_signals={b"*00GS=zz!": seal_written},
      response_labels=[None, None, "operation", "cleanup"],
    )

    async def seal() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend:
          try:
            await backend.seal(60, 2)
          except anyio.get_cancelled_exc_class():
            events.append("operation.cancelled")
            raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(seal)
      await seal_written.wait()
      scopes[0].cancel()

    assert commands(fake_io) == [
      "*00SR=zz!",
      "*00DH=0060zz!",
      "*00DT=0020zz!",
      "*00GS=zz!",
      "*00H0ZZ",
    ]
    assert events.index("response:operation.completed") < events.index("operation.cancelled")
    assert events.index("operation.cancelled") < events.index("response:cleanup.completed")

  async def test_sequential_reuse(self) -> None:
    events: List[str] = []
    backend, fake_io = make_backend([status_response()] * 4, events)

    async with backend:
      pass
    async with backend:
      pass

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ", "*00SR=zz!", "*00H0ZZ"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates_after_heater_cleanup(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    backend, fake_io = make_backend([status_response(), status_response()], events, failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-2:] == ["write:*00H0ZZ", "serial.exit"]

  async def test_unknown_options_fail_before_serial_entry(self) -> None:
    events: List[str] = []
    backend, _ = make_backend(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []
