from collections import deque
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Deque, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.qinstruments import bioshake as bioshake_module
from pylabrobot.qinstruments.bioshake import BioShake
from pylabrobot.resources import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase

ReplyEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]
WriteHook = Callable[[], Awaitable[None]]


class FakeSerialIO(HasLifespan):
  """Replay BioShake replies and record lifecycle and protocol events."""

  def __init__(
    self,
    replies: Optional[List[ReplyEffect]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    before_write: Optional[dict[bytes, WriteHook]] = None,
    after_write: Optional[dict[bytes, WriteHook]] = None,
  ) -> None:
    super().__init__()
    self.replies: Deque[ReplyEffect] = deque(replies or [])
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.before_write = before_write or {}
    self.after_write = after_write or {}
    self.writes: List[bytes] = []
    self.port = "FAKE"
    self.timeout = 60.0
    self.write_timeout = 10.0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def reset_input_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("reset.input")

  async def reset_output_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("reset.output")

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    hook = self.before_write.get(data)
    if hook is not None:
      await hook()
    self.writes.append(data)
    self.events.append(f"write:{data.decode('ascii').rstrip()}")
    hook = self.after_write.get(data)
    if hook is not None:
      await hook()

  async def readline(self) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self.replies:
      return b""
    effect = self.replies.popleft()
    if isinstance(effect, BaseException):
      raise effect
    reply = await effect() if callable(effect) else effect
    self.events.append(f"read:{reply.decode('ascii').strip()}")
    return reply

  @contextmanager
  def temporary_timeout(self, timeout: float):
    original = self.timeout
    self.timeout = timeout
    try:
      yield
    finally:
      self.timeout = original


def make_device(
  replies: Optional[List[ReplyEffect]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  before_write: Optional[dict[bytes, WriteHook]] = None,
  after_write: Optional[dict[bytes, WriteHook]] = None,
  supports_shaking: bool = True,
  supports_locking: bool = True,
  supports_temperature_control: bool = True,
) -> tuple[BioShake, FakeSerialIO]:
  """Build a BioShake backed by a hardware-free serial transport."""
  device = BioShake(
    port="FAKE",
    name="bioshake",
    size_x=142,
    size_y=99,
    size_z=60,
    child_location=Coordinate(7, 6, 50),
    pedestal_size_z=0,
    supports_shaking=supports_shaking,
    supports_locking=supports_locking,
    supports_temperature_control=supports_temperature_control,
  )
  fake_io = FakeSerialIO(
    replies=replies,
    events=events,
    entry_error=entry_error,
    exit_error=exit_error,
    before_write=before_write,
    after_write=after_write,
  )
  device.io = cast(Serial, fake_io)
  return device, fake_io


def commands(io: FakeSerialIO) -> List[str]:
  """Return sent commands without carriage-return terminators."""
  return [frame.decode("ascii").rstrip("\r") for frame in io.writes]


def shaking_replies(*, stop_reply: bytes = b"ok\r") -> List[ReplyEffect]:
  """Return replies for one start transition followed by one stop transition."""
  return [
    b"200\r",
    b"3000\r",
    b"ok\r",
    b"0\r",
    b"10\r",
    b"ok\r",
    b"ok\r",
    b"0\r",
    b"10\r",
    b"ok\r",
    stop_reply,
  ]


def exception_chain(exception: BaseException) -> List[BaseException]:
  """Return an exception and each reachable explicit or implicit cause."""
  chain: List[BaseException] = []
  current: Optional[BaseException] = exception
  while current is not None and current not in chain:
    chain.append(current)
    current = current.__cause__ or current.__context__
  return chain


async def _block(event: anyio.Event) -> bytes:
  event.set()
  await anyio.sleep_forever()
  raise AssertionError("unreachable")


async def _checkpoint_sleep(delay: float) -> None:
  await anyio.lowlevel.checkpoint()


class TestBioShakeProtocol(AnyioTestBase):
  async def test_send_command_resets_buffers_writes_cr_and_paces_before_read(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    device, io = make_device([b"value\r\n"], events=events)

    async def record_sleep(delay: float) -> None:
      events.append(f"sleep:{delay}")

    monkeypatch.setattr(bioshake_module, "sleep", record_sleep)

    assert await device.send_command("getValue", delay=0.125) == "value"
    assert commands(io) == ["getValue"]
    assert events == [
      "reset.input",
      "reset.output",
      "write:getValue",
      "sleep:0.125",
      "read:value",
    ]

  async def test_send_command_times_out_with_a_finite_deadline(self) -> None:
    started = anyio.Event()
    device, _ = make_device([lambda: _block(started)])

    with pytest.raises(RuntimeError, match="Timed out waiting for response to 'slow'"):
      await device.send_command("slow", delay=0, timeout=0.01)

    assert started.is_set()

  async def test_send_command_waits_through_empty_poll_for_reply(self) -> None:
    """An internal serial poll without data does not finish a command."""
    device, _ = make_device([b"", b"ok\r"])
    assert await device.send_command("ready", delay=0, timeout=0.1) is None

  async def test_send_command_reports_empty_device_error_and_unsupported_replies(self) -> None:
    device, _ = make_device([b""])

    with pytest.raises(RuntimeError, match="Timed out waiting for response"):
      await device.send_command("empty", delay=0, timeout=0.01)
    device, _ = make_device([b"e 12\r", b"u -> command\r"])
    with pytest.raises(RuntimeError, match="Device returned error"):
      await device.send_command("bad", delay=0)
    with pytest.raises(RuntimeError, match="not supported"):
      await device.send_command("unknown", delay=0)

  async def test_cancellation_at_pre_write_boundary_sends_no_command(self) -> None:
    device, io = make_device([b"ok\r"])
    scope: Optional[anyio.CancelScope] = None

    async def reset_output_then_cancel() -> None:
      await anyio.lowlevel.checkpoint()
      assert scope is not None
      scope.cancel()

    io.reset_output_buffer = reset_output_then_cancel  # type: ignore[method-assign]

    with anyio.CancelScope() as cancel_scope:
      scope = cancel_scope
      await device.send_command("shakeOn", delay=0)

    assert commands(io) == []
    assert not device._is_shaking

  async def test_cancellation_after_write_records_success_before_propagating(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device, io = make_device(
      [b"ok\r", b"ok\r"], events=events, after_write={b"shakeOn\r": hold_write}
    )

    async def start() -> None:
      try:
        await device.send_command("shakeOn", delay=0)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(start)
      await write_started.wait()
      task_group.cancel_scope.cancel()
      with anyio.CancelScope(shield=True):
        await anyio.sleep(0)
        assert "cancelled" not in events
        release_write.set()

    assert device._is_shaking
    assert events.index("read:ok") < events.index("cancelled")
    await device.send_command("shakeOff", delay=0)
    assert not device._is_shaking

  async def test_reset_waits_for_initialization_complete(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    device, io = make_device([b"booting\r", b"Initialization complete\r"])
    sleeps: List[float] = []

    async def record_sleep(delay: float) -> None:
      sleeps.append(delay)

    monkeypatch.setattr(bioshake_module, "sleep", record_sleep)

    await device.reset()

    assert commands(io) == ["resetDevice"]
    assert sleeps == [0.1, 0.1]

  async def test_reset_accepts_q1_initialization_completion(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    device, io = make_device([b"Initialization successfully completed.\r\n"])

    async def checkpoint_sleep(_delay: float) -> None:
      await anyio.lowlevel.checkpoint()

    monkeypatch.setattr(bioshake_module, "sleep", checkpoint_sleep)
    await device.reset()

    assert commands(io) == ["resetDevice"]

  async def test_home_sends_expected_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
    device, io = make_device([b"ok\r"])
    sleeps: List[float] = []

    async def record_sleep(delay: float) -> None:
      sleeps.append(delay)

    monkeypatch.setattr(bioshake_module, "sleep", record_sleep)
    await device.home()

    assert commands(io) == ["shakeGoHome"]
    assert sleeps == [5]

  async def test_start_and_stop_shaking_preserve_protocol_and_state(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    device, io = make_device(
      [
        b"200\r",
        b"3000\r",
        b"ok\r",
        b"0\r",
        b"10\r",
        b"ok\r",
        b"ok\r",
        b"0\r",
        b"10\r",
        b"ok\r",
        b"ok\r",
      ]
    )

    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    await device.start_shaking(1500, acceleration=2)
    assert device._is_shaking
    await device.stop_shaking(deceleration=3)
    assert not device._is_shaking
    assert commands(io) == [
      "getShakeMinRpm",
      "getShakeMaxRpm",
      "setShakeTargetSpeed1500",
      "getShakeAccelerationMin",
      "getShakeAccelerationMax",
      "setShakeAcceleration2",
      "shakeOn",
      "getShakeAccelerationMin",
      "getShakeAccelerationMax",
      "setShakeAcceleration3",
      "shakeOff",
    ]

  async def test_failed_state_commands_do_not_change_active_state(self) -> None:
    device, _ = make_device([b"e start\r"])
    with pytest.raises(RuntimeError):
      await device.send_command("shakeOn", delay=0)
    assert not device._is_shaking

    device._is_shaking = True
    device.io = cast(Serial, FakeSerialIO([b"e stop\r"]))
    with pytest.raises(RuntimeError):
      await device.send_command("shakeOff", delay=0)
    assert device._is_shaking

    device._temperature_control_active = False
    device.io = cast(Serial, FakeSerialIO([b"e heat\r"]))
    with pytest.raises(RuntimeError):
      await device.send_command("tempOn", delay=0)
    assert not device._temperature_control_active

    device._temperature_control_active = True
    device.io = cast(Serial, FakeSerialIO([b"e heat off\r"]))
    with pytest.raises(RuntimeError):
      await device.send_command("tempOff", delay=0)
    assert device._temperature_control_active

  async def test_lock_and_unlock_plate_preserve_commands(self) -> None:
    device, io = make_device([b"ok\r", b"ok\r"])

    await device.lock_plate()
    await device.unlock_plate()

    assert commands(io) == ["setElmLockPos", "setElmUnlockPos"]

  async def test_temperature_control_preserves_commands_and_values(self) -> None:
    device, io = make_device([b"4\r", b"99\r", b"ok\r", b"ok\r", b"21.7\r", b"ok\r"])

    await device.set_temperature(37.5)
    assert device._temperature_control_active
    assert await device.request_current_temperature() == 21.7
    await device.deactivate()
    assert not device._temperature_control_active

    assert commands(io) == [
      "getTempMin",
      "getTempMax",
      "setTempTarget375",
      "tempOn",
      "getTempActual",
      "tempOff",
    ]


class TestBioShakeLifecycle(AnyioTestBase):
  def test_initializes_plate_holder_and_async_resource_state(self) -> None:
    device, _ = make_device()

    assert device.name == "bioshake"
    assert device.pedestal_size_z == 0
    assert device._active_lifespan is None
    assert not device.is_active

  def test_inherits_managed_methods_directly(self) -> None:
    assert BioShake.__aenter__ is HasLifespan.__aenter__
    assert BioShake.__aexit__ is HasLifespan.__aexit__
    assert BioShake.setup is HasLifespan.setup
    assert BioShake.stop is HasLifespan.stop

  async def test_default_lifespan_resets_homes_and_closes_serial(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    device, io = make_device([b"Initialization complete\r", b"ok\r"], events=events)
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async with device:
      events.append("body")

    assert commands(io) == ["resetDevice", "shakeGoHome"]
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"
    assert events.index("body") < events.index("serial.exit")
    assert not device.is_active

  async def test_skip_home_lifespan_only_owns_serial(self) -> None:
    events: List[str] = []
    device, io = make_device(events=events)

    async with device.lifespan(skip_home=True):
      assert device.is_active

    assert commands(io) == []
    assert events == ["serial.enter", "serial.exit"]

  async def test_unknown_lifespan_option_is_rejected_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached body")

    assert events == []

  async def test_skip_home_requires_a_bool_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    with pytest.raises(TypeError, match="skip_home must be a bool"):
      async with device.lifespan(skip_home=1):
        pytest.fail("invalid entry reached body")

    assert events == []

  async def test_reset_failure_closes_serial(self) -> None:
    failure = RuntimeError("reset read failed")
    events: List[str] = []
    device, _ = make_device([failure], events=events)

    with pytest.raises(RuntimeError, match="reset read failed"):
      async with device:
        pytest.fail("failed reset reached body")

    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_home_failure_closes_serial(self) -> None:
    events: List[str] = []
    device, io = make_device([b"Initialization complete\r", b"e home\r"], events=events)

    with pytest.raises(RuntimeError, match="Device returned error"):
      async with device:
        pytest.fail("failed home reached body")

    assert commands(io) == ["resetDevice", "shakeGoHome"]
    assert events[-1] == "serial.exit"

  async def test_cancellation_after_reset_write_finishes_reset_before_close(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device, io = make_device(
      [b"Initialization complete\r"], events=events, after_write={b"resetDevice\r": hold_write}
    )

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device:
          pytest.fail("cancelled reset reached body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "serial.exit" not in events
      release_write.set()

    assert commands(io) == ["resetDevice"]
    assert events.index("read:Initialization complete") < events.index("serial.exit")

  async def test_cancellation_after_home_write_finishes_home_before_close(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device, io = make_device(
      [b"Initialization complete\r", b"ok\r"],
      events=events,
      after_write={b"shakeGoHome\r": hold_write},
    )
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device:
          pytest.fail("cancelled home reached body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "serial.exit" not in events
      release_write.set()

    assert commands(io) == ["resetDevice", "shakeGoHome"]
    assert events.index("read:ok") < events.index("serial.exit")

  async def test_body_cancellation_closes_serial_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device, _ = make_device(events=events)

    async def use_device() -> None:
      try:
        async with device.lifespan(skip_home=True):
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_device)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-2:] == ["serial.exit", "cancelled"]

  async def test_sequential_reuse_enters_and_closes_serial_twice(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    async with device.lifespan(skip_home=True):
      pass
    async with device.lifespan(skip_home=True):
      pass

    assert events == ["serial.enter", "serial.exit", "serial.enter", "serial.exit"]

  async def test_close_failure_propagates_after_deactivation(self) -> None:
    failure = RuntimeError("serial close failed")
    events: List[str] = []
    device, _ = make_device(events=events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device.lifespan(skip_home=True):
        pass

    assert error_info.value is failure
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_exit_stops_only_active_features_before_serial_close(self) -> None:
    events: List[str] = []
    device, io = make_device([b"ok\r", b"ok\r", b"ok\r", b"ok\r"], events=events)

    async with device.lifespan(skip_home=True):
      await device.send_command("shakeOn", delay=0)
      await device.send_command("tempOn", delay=0)

    assert commands(io) == ["shakeOn", "tempOn", "shakeOff", "tempOff"]
    assert events.index("write:shakeOff") < events.index("write:tempOff")
    assert events.index("write:tempOff") < events.index("serial.exit")
    assert not device._is_shaking
    assert not device._temperature_control_active

  async def test_exit_sends_no_commands_for_inactive_features(self) -> None:
    device, io = make_device()

    async with device.lifespan(skip_home=True):
      pass

    assert commands(io) == []


class TestBioShakeTimedCancellation(AnyioTestBase):
  async def test_timed_shake_completes_shielded_stop_before_cancellation_propagates(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    duration_started = anyio.Event()
    device, io = make_device(
      [
        b"200\r",
        b"3000\r",
        b"ok\r",
        b"0\r",
        b"10\r",
        b"ok\r",
        b"ok\r",
        b"0\r",
        b"10\r",
        b"ok\r",
        b"ok\r",
      ],
      events=events,
    )

    async def controlled_sleep(delay: float) -> None:
      if delay == 12345:
        duration_started.set()
        await anyio.sleep_forever()
      await anyio.lowlevel.checkpoint()

    monkeypatch.setattr(bioshake_module, "sleep", controlled_sleep)

    async def shake() -> None:
      try:
        await device.shake(speed=1500, duration=12345)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with device.lifespan(skip_home=True):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(shake)
        await duration_started.wait()
        task_group.cancel_scope.cancel()

    assert commands(io)[-4:] == [
      "getShakeAccelerationMin",
      "getShakeAccelerationMax",
      "setShakeAcceleration0",
      "shakeOff",
    ]
    assert events.index("write:shakeOff") < events.index("cancelled")
    assert not device._is_shaking

  async def test_durable_start_remains_active_until_lifespan_exit(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    device, io = make_device(
      [b"200\r", b"3000\r", b"ok\r", b"0\r", b"10\r", b"ok\r", b"ok\r", b"ok\r"]
    )
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async with device.lifespan(skip_home=True):
      await device.start_shaking(speed=1500)
      assert device._is_shaking
      assert commands(io)[-1] == "shakeOn"

    assert commands(io)[-1] == "shakeOff"
    assert not device._is_shaking


class TestBioShakeShakingScope(AnyioTestBase):
  async def test_scope_applies_acceleration_and_deceleration_and_tracks_confirmed_state(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    device, io = make_device(shaking_replies())
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async with device.lifespan(skip_home=True):
      async with device.shaking(speed=1500, acceleration=2, deceleration=3):
        assert device._is_shaking
        assert not device._shaking_state_unknown
      assert not device._is_shaking
      assert not device._shaking_state_unknown

    assert commands(io) == [
      "getShakeMinRpm",
      "getShakeMaxRpm",
      "setShakeTargetSpeed1500",
      "getShakeAccelerationMin",
      "getShakeAccelerationMax",
      "setShakeAcceleration2",
      "shakeOn",
      "getShakeAccelerationMin",
      "getShakeAccelerationMax",
      "setShakeAcceleration3",
      "shakeOff",
    ]

  async def test_scope_body_failure_still_stops(self, monkeypatch: pytest.MonkeyPatch) -> None:
    device, io = make_device(shaking_replies())
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async with device.lifespan(skip_home=True):
      with pytest.raises(ValueError, match="body failed"):
        async with device.shaking(1500):
          raise ValueError("body failed")

    assert commands(io)[-1] == "shakeOff"
    assert not device._is_shaking

  async def test_cleanup_failure_replaces_body_failure_and_retains_retryable_state(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    body_failure = ValueError("body failed")
    device, io = make_device(shaking_replies(stop_reply=b"e stop\r"))
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async with device.lifespan(skip_home=True):
      with pytest.raises(RuntimeError, match="Device returned error") as error_info:
        async with device.shaking(1500):
          raise body_failure

      assert body_failure in exception_chain(error_info.value)
      assert device._is_shaking
      assert device._shaking_state_unknown
      io.replies.extend([b"0\r", b"10\r", b"ok\r", b"ok\r"])
      await device.stop_shaking()

    assert not device._is_shaking
    assert not device._shaking_state_unknown

  async def test_failed_rollback_after_unconfirmed_start_retains_unknown_state_for_retry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    replies: List[ReplyEffect] = [
      b"200\r",
      b"3000\r",
      b"ok\r",
      b"0\r",
      b"10\r",
      b"ok\r",
      b"e start\r",
      b"0\r",
      b"10\r",
      b"ok\r",
      b"e rollback\r",
      b"0\r",
      b"10\r",
      b"ok\r",
      b"ok\r",
    ]
    device, io = make_device(replies)
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async with device.lifespan(skip_home=True):
      with pytest.raises(RuntimeError, match="shakeOff") as error_info:
        async with device.shaking(1500):
          pytest.fail("failed start reached body")

      assert any("shakeOn" in str(error) for error in exception_chain(error_info.value))
      assert not device._is_shaking
      assert device._shaking_state_unknown

      writes_before_rejected_scope = list(io.writes)
      with pytest.raises(RuntimeError, match="already active"):
        async with device.shaking(1400):
          pytest.fail("scope adopted unknown state")
      assert io.writes == writes_before_rejected_scope

      await device.stop_shaking()
      assert not device._is_shaking
      assert not device._shaking_state_unknown

    assert commands(io).count("shakeOn") == 1
    assert commands(io).count("shakeOff") == 2

  async def test_cancellation_after_start_write_reconciles_stop_before_propagating(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    start_written = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_start_write() -> None:
      start_written.set()
      await release_write.wait()

    device, io = make_device(
      shaking_replies(), events=events, after_write={b"shakeOn\r": hold_start_write}
    )
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async def run_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with device.shaking(1500):
            pytest.fail("cancelled entry reached body")
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with device.lifespan(skip_home=True):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_scope)
        await start_written.wait()
        scopes[0].cancel()
        await anyio.sleep(0)
        assert "cancelled" not in events
        release_write.set()

    assert events.index("write:shakeOff") < events.index("cancelled")
    assert not device._is_shaking

  async def test_scope_rejects_nested_and_direct_transitions_before_io(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    device, io = make_device(shaking_replies())
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async with device.lifespan(skip_home=True):
      async with device.shaking(1500):
        writes_before = list(io.writes)
        with pytest.raises(RuntimeError, match="already scoped"):
          async with device.shaking(1400):
            pytest.fail("nested scope reached body")
        with pytest.raises(RuntimeError, match="scope"):
          await device.start_shaking(1400)
        with pytest.raises(RuntimeError, match="scope"):
          await device.stop_shaking()
        assert io.writes == writes_before

  async def test_scope_rejects_inactive_and_preexisting_durable_shaking(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    device, io = make_device(shaking_replies())
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    with pytest.raises(RuntimeError, match="active BioShake lifespan"):
      async with device.shaking(1500):
        pytest.fail("inactive scope reached body")
    assert commands(io) == []

    async with device.lifespan(skip_home=True):
      await device.start_shaking(1500)
      writes_before = list(io.writes)
      with pytest.raises(RuntimeError, match="already active"):
        async with device.shaking(1400):
          pytest.fail("scope adopted durable state")
      assert io.writes == writes_before
      await device.stop_shaking()

  async def test_parent_exit_stops_and_invalidates_live_scope_before_serial_close(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    device, io = make_device(shaking_replies()[:7] + [b"ok\r"], events=events)
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)
    lifespan = device.lifespan(skip_home=True)
    scope = device.shaking(1500)

    await lifespan.__aenter__()
    await scope.__aenter__()
    await lifespan.__aexit__(None, None, None)
    writes_after_parent_exit = list(io.writes)
    await scope.__aexit__(None, None, None)

    assert commands(io)[-1] == "shakeOff"
    assert events.index("write:shakeOff") < events.index("serial.exit")
    assert io.writes == writes_after_parent_exit
    assert not device._is_shaking

  async def test_scope_supports_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    device, io = make_device(shaking_replies() + shaking_replies())
    monkeypatch.setattr(bioshake_module, "sleep", _checkpoint_sleep)

    async with device.lifespan(skip_home=True):
      async with device.shaking(1500):
        pass
      async with device.shaking(1400):
        pass

    assert commands(io).count("shakeOn") == 2
    assert commands(io).count("shakeOff") == 2

  async def test_timed_shake_delegates_duration_to_scope(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    device, io = make_device(shaking_replies())
    sleeps: List[float] = []

    async def record_sleep(delay: float) -> None:
      sleeps.append(delay)
      await anyio.lowlevel.checkpoint()

    monkeypatch.setattr(bioshake_module, "sleep", record_sleep)
    async with device.lifespan(skip_home=True):
      await device.shake(speed=1500, duration=12.5)

    assert 12.5 in sleeps
    assert commands(io).count("shakeOn") == 1
    assert commands(io).count("shakeOff") == 1
