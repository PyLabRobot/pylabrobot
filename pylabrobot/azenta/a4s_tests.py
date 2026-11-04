from typing import Any, Awaitable, Callable, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.azenta.a4s import A4S, A4SStatus
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
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
    if not out:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    if not self._rx and self._active_response_label is not None:
      self.events.append(f"response:{self._active_response_label}.completed")
      self._active_response_label = None
    return out


def make_device(
  responses: Optional[List[ResponseEffect]] = None,
  events: Optional[List[str]] = None,
  exit_error: Optional[BaseException] = None,
  timeout: int = 1,
  write_signals: Optional[dict[bytes, anyio.Event]] = None,
  response_labels: Optional[List[Optional[str]]] = None,
) -> tuple[A4S, FakeSerialIO]:
  """Build an A4S backed by a hardware-free serial transport."""
  device = A4S("FAKE", timeout=timeout)
  fake_io = FakeSerialIO(responses, events, exit_error, write_signals, response_labels)
  device.io = cast(Serial, fake_io)
  return device, fake_io


def commands(fake_io: FakeSerialIO) -> List[str]:
  return [data.decode("ascii") for data in fake_io.writes]


class TestA4SProtocol(AnyioTestBase):
  async def test_system_reset_and_heater_commands(self) -> None:
    device, fake_io = make_device([status_response(), status_response(), status_response()])

    await device.system_reset()
    await device.set_heater(True)
    await device.set_heater(False)

    assert commands(fake_io) == ["*00SR=zz!", "*00H1ZZ", "*00H0ZZ"]

  async def test_status_parses_temperature_enums_codes_sensors_and_time(self) -> None:
    device, _ = make_device(
      [status_response(temperature=375, system=4, heater=3, warning=7, sensors=0x55, remaining=12)]
    )

    status = await device.request_status()

    assert status.current_temperature == 37.5
    assert status.system_status is A4SStatus.SystemStatus.finish
    assert status.heater_block_status is A4SStatus.HeaterBlockStatus.cooling
    assert status.warning_code == 7
    assert status.remaining_time == 12
    assert status.sensor_status == A4SStatus.SensorStatus(
      True, False, True, False, True, False, True
    )

  async def test_status_error_raises_with_response(self) -> None:
    device, _ = make_device([status_response(error=9)])

    with pytest.raises(RuntimeError, match="response .*9"):
      await device.request_status()

  async def test_seal_preserves_command_order(self) -> None:
    device, fake_io = make_device(
      [
        status_response(temperature=600),
        status_response(system=1),
        status_response(system=4),
      ]
    )

    await device.seal(60, 2)

    assert commands(fake_io) == ["*00DH=0060zz!", "*00DT=0020zz!", "*00GS=zz!"]

  async def test_shuttle_commands_wait_for_requested_sensor_state(self) -> None:
    device, fake_io = make_device(
      [
        status_response(sensors=0),
        status_response(sensors=2),
        status_response(sensors=2),
        status_response(),
      ]
    )

    await device.open()
    await device.close()

    assert commands(fake_io) == ["*00MO=zz!", "*00MC=zz!"]

  async def test_response_status_shuttle_and_temperature_waits_time_out(self) -> None:
    response_device, _ = make_device(timeout=0)
    with pytest.raises(TimeoutError, match="response"):
      await response_device.read_message()

    status_device, _ = make_device([status_response(system=2)], timeout=0)
    with pytest.raises(TimeoutError, match="response"):
      await status_device.wait_for_status({A4SStatus.SystemStatus.idle})

    shuttle_device, _ = make_device([status_response()], timeout=0)
    with pytest.raises(TimeoutError, match="shuttle open sensor"):
      await shuttle_device.wait_for_shuttle_open_sensor(True, timeout=0)

    temperature_device, _ = make_device([status_response(temperature=200)], timeout=0)
    with pytest.raises(TimeoutError, match="temperature"):
      await temperature_device._wait_for_temperature(50, timeout=0)

  async def test_unrelated_status_read_is_immediately_cancellable(self) -> None:
    events: List[str] = []
    response_started = anyio.Event()
    release_response = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    device, _ = make_device(
      [_delayed_status(response_started, release_response, events)],
      events,
    )

    async def request_status() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.request_status()
        except anyio.get_cancelled_exc_class():
          events.append("request.cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(request_status)
      await response_started.wait()
      scopes[0].cancel()

    assert events == ["request.cancelled"]


class TestA4SLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert A4S.__aenter__ is HasLifespan.__aenter__
    assert A4S.__aexit__ is HasLifespan.__aexit__
    assert A4S.setup is HasLifespan.setup
    assert A4S.stop is HasLifespan.stop

  async def test_normal_exit_disables_heater_before_serial_close(self) -> None:
    events: List[str] = []
    device, fake_io = make_device([status_response(), status_response()], events)

    async with device:
      events.append("body")

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-2:] == ["write:*00H0ZZ", "serial.exit"]
    assert not device.is_active

  async def test_reset_failure_disables_heater_before_serial_close(self) -> None:
    events: List[str] = []
    device, fake_io = make_device([status_response(error=3), status_response()], events)

    with pytest.raises(RuntimeError, match="response"):
      async with device:
        pytest.fail("failed reset reached the body")

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-2:] == ["write:*00H0ZZ", "serial.exit"]
    assert not device.is_active

  async def test_body_failure_disables_heater_before_serial_close(self) -> None:
    events: List[str] = []
    device, fake_io = make_device([status_response(), status_response()], events)

    with pytest.raises(RuntimeError, match="body failed"):
      async with device:
        raise RuntimeError("body failed")

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-2:] == ["write:*00H0ZZ", "serial.exit"]

  async def test_body_cancellation_disables_heater_before_close_and_propagation(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device, fake_io = make_device([status_response(), status_response()], events)

    async def use_device() -> None:
      try:
        async with device:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_device)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-3:] == ["write:*00H0ZZ", "serial.exit", "cancelled"]

  async def test_heater_cancellation_reconciles_response_before_cleanup(self) -> None:
    events: List[str] = []
    response_started = anyio.Event()
    release_response = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    device, fake_io = make_device(
      [
        status_response(),
        _delayed_status(response_started, release_response, events),
        status_response(),
      ],
      events,
    )

    async def heat() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device:
          await device.set_heater(True)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(heat)
      await response_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "write:*00H0ZZ" not in events
      release_response.set()

    assert commands(fake_io) == ["*00SR=zz!", "*00H1ZZ", "*00H0ZZ"]
    assert events.index("response.completed") < events.index("write:*00H0ZZ")

  async def test_seal_cancellation_reconciles_response_before_cleanup(self) -> None:
    events: List[str] = []
    response_started = anyio.Event()
    release_response = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    device, fake_io = make_device(
      [
        status_response(),
        status_response(temperature=600),
        _delayed_status(response_started, release_response, events, system=1),
        status_response(system=4),
        status_response(),
      ],
      events,
    )

    async def seal() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device:
          await device.seal(60, 2)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(seal)
      await response_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "write:*00H0ZZ" not in events
      release_response.set()

    assert commands(fake_io) == [
      "*00SR=zz!",
      "*00DH=0060zz!",
      "*00DT=0020zz!",
      "*00GS=zz!",
      "*00H0ZZ",
    ]
    assert events.index("response.completed") < events.index("write:*00H0ZZ")

  async def test_heater_write_reconciles_exactly_one_response_before_cancellation(self) -> None:
    events: List[str] = []
    heater_written = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    device, fake_io = make_device(
      [status_response(), status_response(), status_response()],
      events,
      write_signals={b"*00H1ZZ": heater_written},
      response_labels=[None, "heater.current", "cleanup"],
    )

    async def heat() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device:
          try:
            await device.set_heater(True)
          except anyio.get_cancelled_exc_class():
            events.append("operation.cancelled")
            raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(heat)
      await heater_written.wait()
      scopes[0].cancel()

    assert commands(fake_io) == ["*00SR=zz!", "*00H1ZZ", "*00H0ZZ"]
    assert events.index("response:heater.current.completed") < events.index("operation.cancelled")
    assert events.index("operation.cancelled") < events.index("response:cleanup.completed")

  async def test_seal_write_reconciles_exactly_one_response_before_cancellation(self) -> None:
    events: List[str] = []
    seal_written = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    device, fake_io = make_device(
      [
        status_response(),
        status_response(temperature=600),
        status_response(system=1),
        status_response(),
      ],
      events,
      write_signals={b"*00GS=zz!": seal_written},
      response_labels=[None, None, "seal.current", "cleanup"],
    )

    async def seal() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device:
          try:
            await device.seal(60, 2)
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
    assert events.index("response:seal.current.completed") < events.index("operation.cancelled")
    assert events.index("operation.cancelled") < events.index("response:cleanup.completed")

  async def test_pending_cancellation_prevents_heater_mutation(self) -> None:
    device, fake_io = make_device([status_response()])
    returned = False

    with anyio.CancelScope() as scope:
      scope.cancel()
      await device.set_heater(True)
      returned = True

    assert not returned
    assert commands(fake_io) == []

  async def test_pending_cancellation_prevents_seal_mutation(self) -> None:
    device, fake_io = make_device([status_response(system=1)])
    returned = False

    with anyio.CancelScope() as scope:
      scope.cancel()
      await device._send_mutation_and_reconcile("*00GS=zz!")
      returned = True

    assert not returned
    assert commands(fake_io) == []

  async def test_sequential_reuse_resets_and_disables_twice(self) -> None:
    events: List[str] = []
    device, fake_io = make_device([status_response()] * 4, events)

    async with device:
      pass
    async with device:
      pass

    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ", "*00SR=zz!", "*00H0ZZ"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates_after_heater_cleanup(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device, fake_io = make_device([status_response(), status_response()], events, failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert commands(fake_io) == ["*00SR=zz!", "*00H0ZZ"]
    assert events[-2:] == ["write:*00H0ZZ", "serial.exit"]

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []


def _delayed_status(
  started: anyio.Event,
  release: anyio.Event,
  events: List[str],
  system: int = 0,
) -> Callable[[], Awaitable[str]]:
  async def respond() -> str:
    started.set()
    await release.wait()
    events.append("response.completed")
    return status_response(system=system)

  return respond
