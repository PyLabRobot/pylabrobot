from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, cast
from unittest.mock import patch

import anyio
import pytest
import serial
from trio.testing import MockClock

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.heating_shaking import HeaterShaker
from pylabrobot.legacy.heating_shaking import bioshake_backend as bioshake_module
from pylabrobot.legacy.heating_shaking.bioshake_backend import BioShake
from pylabrobot.legacy.shaking import Shaker
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase

# Scripted replies, keyed by the command that requested them. Every other command falls back
# to the device's plain acknowledgement.
_DEFAULT_RESPONSES = {
  "resetDevice": b"Initialization complete\r\n",
  "getShakeMinRpm": b"200\r\n",
  "getShakeMaxRpm": b"3000\r\n",
  "getShakeAccelerationMin": b"0\r\n",
  "getShakeAccelerationMax": b"10\r\n",
  "getTempMin": b"0\r\n",
  "getTempMax": b"99\r\n",
  "getTempActual": b"25.0\r\n",
}

_ACKNOWLEDGEMENT = b"ok\r\n"

# A reply that never realistically arrives, for the deadline observations. It is finite on
# purpose: a backend that applies no bound at all eventually completes it and fails a timing
# assertion rather than hanging the run. Those tests use a virtual clock, so it costs no real
# time.
_STALLED_READ_SECONDS = 86400.0

# Deadlock guard for the virtual-clock deadline tests. Ordered bound < guard < stall, so an
# overlong bound is reported by the elapsed-time assertions rather than by the guard, while a
# bound that never fires trips the guard instead of hanging the run.
_DEADLOCK_GUARD = 7200.0

# Deadlock guard for the real-clock transaction tests. Those are event-driven and finish in
# milliseconds, so this is orders of magnitude larger than any correct run, but finite.
_REAL_TIME_DEADLOCK_GUARD = 60.0

# A real-clock reply stall longer than the largest command budget these tests exercise, so a
# backend that drops its bound answers late and fails rather than hanging.
_REAL_TIME_STALL = 4.0

# Slack allowed above a delay observed on a real clock. The matching lower bounds are tight
# because `anyio.sleep` never returns early, so this bounds only the scheduling the interval
# is additionally charged. It is deliberately smaller than the gap between the distinct delay
# values the backend uses, so swapping one delay for another is caught rather than absorbed.
_REAL_TIME_DELAY_SLACK = 0.08


def _write_event(command: str) -> str:
  """Return the recorded event of one command write."""
  return f"write:{command}"


def _exchange_events(command: str) -> List[str]:
  """Return the recorded events of one complete command exchange."""
  return ["reset_input", "reset_output", _write_event(command), "readline"]


class RecordingSerial(HasLifespan):
  """Software Serial double recording lifecycle and wire events."""

  def __init__(
    self,
    *,
    events: Optional[List[str]] = None,
    responses: Optional[Dict[str, bytes]] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    close_error: Optional[BaseException] = None,
    blocked_event: Optional[str] = None,
    read_stall: float = 0.0,
    max_readlines: Optional[int] = None,
  ) -> None:
    """Initialize a closed double.

    Args:
      events: Shared ordered event log, or None for a private one.
      responses: Replies keyed by the command that requested them, overriding the defaults.
      failures: Exception to raise from the named event, keyed by event.
      close_error: Exception to raise when the lifespan closes the transport.
      blocked_event: Event name whose call waits for `release_blocked`.
      read_stall: Seconds a reply read waits before answering. Unlike `blocked_event`, this
        stall ends on its own, so a caller that never bounds the exchange answers late and
        fails a timing assertion rather than hanging the run.
      max_readlines: Number of reply reads this double will serve before raising. A polling
        loop that has lost its budget therefore fails rather than spinning forever.
    """
    super().__init__()
    self.events = events if events is not None else []
    self.responses = dict(_DEFAULT_RESPONSES)
    if responses is not None:
      self.responses.update(responses)
    self.failures = failures or {}
    self.close_error = close_error
    self.blocked_event = blocked_event
    self.read_stall = read_stall
    self.max_readlines = max_readlines
    self.serial_kwargs: Dict[str, Any] = {}
    self.writes: List[bytes] = []
    self.commands: List[str] = []
    self.write_times: List[float] = []
    self.readline_times: List[float] = []
    self.entries = 0
    self.exits = 0
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the double and register its close."""
    if kwargs:
      raise TypeError("RecordingSerial does not accept lifespan options")
    self.entries += 1
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested("serial.enter")

  async def _close(self) -> None:
    """Record the transport close and raise the injected close failure."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("serial.exit")
    if self.close_error is not None:
      raise self.close_error

  async def _block_if_requested(self, event: str) -> None:
    """Wait for release when this event is the configured blocking point."""
    if event == self.blocked_event:
      self.blocked_started.set()
      await self.release_blocked.wait()

  async def _record(self, event: str) -> None:
    """Record one call at its entry, then yield, block, and raise its injected failure.

    The event is appended before this double awaits anything of its own. A transport call is
    the moment work reaches the wire, so recording it after a scheduler hop would report it
    one hop late and hide orderings the caller genuinely produces.
    """
    self.events.append(event)
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested(event)
    failure = self.failures.get(event)
    if failure is not None:
      raise failure

  async def reset_input_buffer(self) -> None:
    await self._record("reset_input")

  async def reset_output_buffer(self) -> None:
    await self._record("reset_output")

  async def write(self, data: bytes) -> None:
    """Record one command write and raise its injected failure."""
    command = data.decode("ascii").rstrip("\r")
    self.writes.append(data)
    self.commands.append(command)
    self.write_times.append(anyio.current_time())
    await self._record(_write_event(command))

  async def readline(self) -> bytes:
    """Record one reply read, stall or block as configured, and answer the last command."""
    self.readline_times.append(anyio.current_time())
    self.events.append("readline")
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested("readline")
    if self.max_readlines is not None and len(self.readline_times) > self.max_readlines:
      raise RuntimeError("RecordingSerial ran out of scripted replies")
    if self.read_stall > 0:
      await anyio.sleep(self.read_stall)
    failure = self.failures.get("readline")
    if failure is not None:
      raise failure
    if not self.commands:
      return b""
    return self.responses.get(self.commands[-1], _ACKNOWLEDGEMENT)


def _wire_events(io: RecordingSerial, baseline: int, *markers: str) -> List[str]:
  """Return the transport events recorded since `baseline`, without the test's own markers.

  Tests append their own markers to the shared log to place an observation in the ordering.
  This drops them again, so an assertion about what reached the wire reads only wire events.
  Filtering is by value across the whole slice, so a marker string must never collide with a
  wire event name: `reset_input`, `reset_output`, `write:*`, `readline`.
  """
  return [event for event in io.events[baseline:] if event not in markers]


def _make_backend(
  monkeypatch: pytest.MonkeyPatch,
  *,
  port: str = "COM_BIOSHAKE",
  timeout: int = 60,
  **double_kwargs: Any,
) -> Tuple[BioShake, RecordingSerial]:
  """Build a backend whose transport is a software Serial double."""
  io = RecordingSerial(**double_kwargs)

  def build_serial(**kwargs: Any) -> Serial:
    io.serial_kwargs = dict(kwargs)
    return cast(Serial, io)

  monkeypatch.setattr(bioshake_module, "Serial", build_serial)
  return BioShake(port=port, timeout=timeout), io


def _make_heater_shaker(backend: BioShake, name: str = "test_bioshake") -> HeaterShaker:
  """Wrap the backend in the frontend that owns its shutdown actions."""
  return HeaterShaker(
    name=name,
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate.zero(),
  )


def _make_shaker(backend: BioShake, name: str = "test_bioshake_shaker") -> Shaker:
  """Wrap the backend in the Wave 12 shaking owner."""
  return Shaker(
    name=name,
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate.zero(),
  )


async def _settle() -> None:
  """Give every other task room to reach its next transport call."""
  for _ in range(10):
    await anyio.lowlevel.checkpoint()


class TestBioShakeLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert BioShake.__aenter__ is HasLifespan.__aenter__
    assert BioShake.__aexit__ is HasLifespan.__aexit__
    assert BioShake.setup is HasLifespan.setup
    assert BioShake.stop is HasLifespan.stop

  async def test_serial_configuration_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    _, io = _make_backend(monkeypatch)

    assert io.serial_kwargs == {
      "port": "COM_BIOSHAKE",
      "baudrate": 9600,
      "bytesize": serial.EIGHTBITS,
      "parity": serial.PARITY_NONE,
      "stopbits": serial.STOPBITS_ONE,
      "write_timeout": 10,
      "timeout": 60,
      "human_readable_device_name": "BioShake",
    }
    # Against the pyserial names above and against their literal values, so the two cannot
    # drift apart and cannot be moved together.
    assert (serial.EIGHTBITS, serial.PARITY_NONE, serial.STOPBITS_ONE) == (8, "N", 1)

  async def test_rejects_lifespan_options_before_serial_entry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options reached the body")

    with pytest.raises(TypeError, match="skip_home must be a bool"):
      async with backend.lifespan(skip_home="yes"):
        pytest.fail("invalid options reached the body")

    with pytest.raises(TypeError, match="skip_home must be a bool"):
      async with backend.lifespan(skip_home=1):
        pytest.fail("invalid options reached the body")

    assert io.events == []
    assert io.commands == []
    assert io.entries == 0

  async def test_skip_home_transcript(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend.lifespan(skip_home=True):
      assert io.events == ["serial.enter"]

    # Entry opens the transport and issues nothing; exit closes it and issues nothing. The
    # HeaterShaker frontend, not the backend, owns the shutdown commands.
    assert io.events == ["serial.enter", "serial.exit"]
    assert io.commands == []
    assert io.entries == 1
    assert io.exits == 1

  async def test_lifespan_does_not_reenter_the_managed_bridge(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend.lifespan(skip_home=True):
      io.events.append("body")

    assert io.events == ["serial.enter", "body", "serial.exit"]
    assert io.entries == 1
    assert io.exits == 1

  async def test_entry_cancellation_closes_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch, blocked_event="serial.enter")
    scopes: List[anyio.CancelScope] = []

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend.lifespan(skip_home=True):
          pytest.fail("cancelled entry reached the body")

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter)
        await io.blocked_started.wait()
        scopes[0].cancel()

    assert io.events[-1] == "serial.exit"
    assert io.exits == 1
    assert io.commands == []
    assert not backend.is_active

  async def test_body_cancellation_closes_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    body_started = anyio.Event()

    async def run() -> None:
      async with backend.lifespan(skip_home=True):
        await backend.lock_plate()
        body_started.set()
        await anyio.sleep_forever()

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run)
        await body_started.wait()
        task_group.cancel_scope.cancel()

    # The last body command is followed only by the transport close: no shutdown command is
    # issued by the backend itself.
    assert io.commands == ["setElmLockPos"]
    assert io.events == [
      "serial.enter",
      *_exchange_events("setElmLockPos"),
      "serial.exit",
    ]
    assert io.exits == 1
    assert not backend.is_active

  async def test_close_failure_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("serial close failed")
    backend, io = _make_backend(monkeypatch, close_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend.lifespan(skip_home=True):
        pass

    assert error_info.value is failure
    assert io.events[-1] == "serial.exit"
    assert not backend.is_active

  async def test_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend.lifespan(skip_home=True):
      await backend.lock_plate()
    async with backend.lifespan(skip_home=True):
      await backend.unlock_plate()

    assert io.entries == 2
    assert io.exits == 2
    assert io.commands == ["setElmLockPos", "setElmUnlockPos"]
    assert io.events == [
      "serial.enter",
      *_exchange_events("setElmLockPos"),
      "serial.exit",
      "serial.enter",
      *_exchange_events("setElmUnlockPos"),
      "serial.exit",
    ]

  async def test_heater_shaker_frontend_exit_issues_each_cleanup_once(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    # The frontend's `_pre_backend_stop` unconditionally deactivates temperature control and
    # force-stops shaking. The backend must not register those actions again: doing so issues
    # every one of them twice on every frontend exit.
    monkeypatch.setattr(bioshake_module, "_ELM_SETTLE_DELAY", 0.0)

    backend, io = _make_backend(monkeypatch)
    heater_shaker = _make_heater_shaker(backend)

    async with heater_shaker.lifespan(skip_home=True):
      await heater_shaker.shake(900)
      assert io.commands.count("shakeOn") == 1
      assert io.commands.count("shakeOff") == 0
      assert io.commands.count("tempOff") == 0

    assert io.commands.count("shakeOff") == 1
    assert io.commands.count("tempOff") == 1
    assert io.commands.count("setElmUnlockPos") == 0
    # Temperature control is deactivated before shaking is force-stopped, and both reach the
    # wire while the transport is still open.
    assert io.commands.index("tempOff") < io.commands.index("shakeOff")
    assert io.events[-1] == "serial.exit"

    idle_backend, idle_io = _make_backend(monkeypatch)
    idle_heater_shaker = _make_heater_shaker(idle_backend, name="idle_bioshake")

    async with idle_heater_shaker.lifespan(skip_home=True):
      pass

    # An entry that never shook still issues each shutdown action exactly once.
    assert idle_io.commands.count("shakeOff") == 1
    assert idle_io.commands.count("tempOff") == 1
    assert idle_io.commands.count("shakeOn") == 0
    assert idle_io.events[-1] == "serial.exit"

  async def test_shaking_scope_issues_one_start_and_one_stop(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(bioshake_module, "_ELM_SETTLE_DELAY", 0.0)

    backend, io = _make_backend(monkeypatch)
    shaker = _make_shaker(backend)

    async with shaker.lifespan(skip_home=True):
      async with shaker.shaking(900):
        assert io.commands.count("shakeOn") == 1
        assert io.commands.count("shakeOff") == 0

    assert io.commands.count("shakeOn") == 1
    assert io.commands.count("shakeOff") == 1
    assert io.commands.count("setElmLockPos") == 1
    assert io.commands.count("setElmUnlockPos") == 1
    assert io.events[-1] == "serial.exit"


class TestBioShakeHoming(AnyioTestBase):
  """Cover the homing entry path on both backends.

  Homing waits four seconds after the reset transcript and gives its home command a five
  second delay, both of which are device behavior this migration preserves exactly. Trio can
  charge those waits to a virtual clock; asyncio has no packaged equivalent, so it pays them
  in real time rather than losing dual-backend coverage of the entry transcript.
  """

  _anyio_backends = {"asyncio": None, "trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_home_transcript(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend:
      assert io.commands == ["resetDevice", "shakeGoHome"]

    assert io.writes == [b"resetDevice\r", b"shakeGoHome\r"]
    assert io.events == [
      "serial.enter",
      *_exchange_events("resetDevice"),
      *_exchange_events("shakeGoHome"),
      "serial.exit",
    ]
    assert io.exits == 1

  async def test_reset_failure_closes_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("reset write failed")
    backend, io = _make_backend(monkeypatch, failures={_write_event("resetDevice"): failure})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed entry reached the body")

    assert error_info.value is failure
    assert io.commands == ["resetDevice"]
    assert io.events[-1] == "serial.exit"
    assert io.exits == 1
    assert not backend.is_active

  async def test_home_failure_closes_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("home write failed")
    backend, io = _make_backend(monkeypatch, failures={_write_event("shakeGoHome"): failure})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed entry reached the body")

    assert str(error_info.value) == (
      "Unexpected error while sending 'shakeGoHome': RuntimeError: home write failed"
    )
    assert error_info.value.__cause__ is failure
    assert io.commands == ["resetDevice", "shakeGoHome"]
    assert io.events[-1] == "serial.exit"
    assert io.exits == 1
    assert not backend.is_active

  async def test_cancelled_reset_is_reconciled_before_serial_closes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch, blocked_event="readline")
    scopes: List[anyio.CancelScope] = []

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with backend:
            pytest.fail("cancelled entry reached the body")
        except anyio.get_cancelled_exc_class():
          io.events.append("entry.cancelled")
          raise

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter)
        await io.blocked_started.wait()
        scopes[0].cancel()
        await _settle()
        # The reset command has committed, so cancellation may not abandon it: the transport
        # is still open and the caller has not yet learned it was cancelled.
        assert io.events[-1] == "readline"
        io.release_blocked.set()

    assert io.commands == ["resetDevice"]
    assert io.events[-3:] == ["readline", "serial.exit", "entry.cancelled"]
    assert io.exits == 1
    assert not backend.is_active


class TestBioShakeCommands(AnyioTestBase):
  async def test_command_transcripts_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bioshake_module, "_ELM_SETTLE_DELAY", 0.0)
    backend, io = _make_backend(monkeypatch)

    async with backend.lifespan(skip_home=True):
      await backend.start_shaking(500)
      await backend.stop_shaking()
      await backend.lock_plate()
      await backend.unlock_plate()
      await backend.set_temperature(37)
      assert await backend.get_current_temperature() == 25.0
      await backend.deactivate()

    assert io.commands == [
      "getShakeMinRpm",
      "getShakeMaxRpm",
      "setShakeTargetSpeed500",
      "getShakeAccelerationMin",
      "getShakeAccelerationMax",
      "setShakeAcceleration0",
      "shakeOn",
      "getShakeAccelerationMin",
      "getShakeAccelerationMax",
      "setShakeAcceleration0",
      "shakeOff",
      "setElmLockPos",
      "setElmUnlockPos",
      "getTempMin",
      "getTempMax",
      "setTempTarget370",
      "tempOn",
      "getTempActual",
      "tempOff",
    ]
    assert io.writes[0] == b"getShakeMinRpm\r"
    assert io.writes[-1] == b"tempOff\r"

  async def test_parameter_validation_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend.lifespan(skip_home=True):
      with pytest.raises(ValueError, match="Speed must be a whole number, not 500.5"):
        await backend.start_shaking(500.5)
      with pytest.raises(TypeError, match="Speed must be an integer or a whole number float"):
        await backend.start_shaking(cast(float, "fast"))
      with pytest.raises(ValueError, match="Deceleration must be a whole number, not 1.5"):
        await backend.stop_shaking(cast(int, 1.5))
      assert io.commands == []

      with pytest.raises(AssertionError, match="Speed 5 RPM is out of range"):
        await backend.start_shaking(5)
      assert io.commands == ["getShakeMinRpm", "getShakeMaxRpm"]

  async def test_response_parsing_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
      (b"", "Unexpected error while sending 'tempOff': RuntimeError: No response for 'tempOff'"),
      (
        b"e\r\n",
        "Unexpected error while sending 'tempOff': RuntimeError: "
        "Device returned error for 'tempOff': 'e'",
      ),
      (
        b"u -> nope\r\n",
        "Unexpected error while sending 'tempOff': NotImplementedError: "
        "'tempOff' not supported: 'u -> nope'",
      ),
    ]

    for response, message in cases:
      backend, _ = _make_backend(monkeypatch, responses={"tempOff": response})
      async with backend.lifespan(skip_home=True):
        with pytest.raises(RuntimeError) as error_info:
          await backend.deactivate()
      assert str(error_info.value) == message

    backend, _ = _make_backend(monkeypatch, responses={"tempOff": b"OK\r\n"})
    async with backend.lifespan(skip_home=True):
      # A standard acknowledgement is consumed, whatever its case, and yields no value.
      assert await backend.deactivate() is None

  async def test_command_transactions_do_not_interleave(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend.lifespan(skip_home=True):
      io.blocked_event = "readline"

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(backend.deactivate)
          await io.blocked_started.wait()
          task_group.start_soon(backend.lock_plate)
          await _settle()
          # The queued command may not reach the wire while the first exchange holds the
          # transaction.
          assert io.commands == ["tempOff"]
          io.release_blocked.set()

      assert io.commands == ["tempOff", "setElmLockPos"]
      assert io.events[1:] == [*_exchange_events("tempOff"), *_exchange_events("setElmLockPos")]

      io.blocked_event = None

  async def test_cancelled_command_consumes_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    cancelled = anyio.Event()
    completed = False
    scopes: List[anyio.CancelScope] = []

    async with backend.lifespan(skip_home=True):
      io.blocked_event = "readline"

      async def deactivate() -> None:
        nonlocal completed
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.deactivate()
          except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise
          completed = True

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(deactivate)
          await io.blocked_started.wait()
          scopes[0].cancel()
          await _settle()
          # The committed exchange is not abandoned: cancellation waits for the reply.
          assert not cancelled.is_set()
          io.release_blocked.set()

      assert cancelled.is_set()
      assert not completed
      assert io.events[1:] == _exchange_events("tempOff")

      io.blocked_event = None
      await backend.lock_plate()
      assert io.events[1:] == [*_exchange_events("tempOff"), *_exchange_events("setElmLockPos")]

  async def test_lock_is_released_before_pending_cancellation_propagates(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    scopes: List[anyio.CancelScope] = []

    async with backend.lifespan(skip_home=True):
      io.blocked_event = "readline"
      baseline = len(io.events)

      async def deactivate() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.deactivate()
          except anyio.get_cancelled_exc_class():
            io.events.append("tempOff.cancelled")
            raise
          pytest.fail("the cancelled command returned")

      # The guard is finite so a transaction that is never handed on fails this test rather
      # than hanging the run; a correct run reaches the assertions in milliseconds.
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(deactivate)
          await io.blocked_started.wait()
          scopes[0].cancel()
          task_group.start_soon(backend.lock_plate)
          await _settle()
          # The cancelled command still holds the transaction, so the queued command is
          # waiting for it and nothing else has reached the wire.
          assert io.events[baseline:] == _exchange_events("tempOff")
          io.release_blocked.set()

      tail = io.events[baseline:]
      # The transaction is handed to the waiting command before the cancelled caller learns
      # it was cancelled, so a queued command is never delayed by one cancellation delivery.
      assert tail[:4] == _exchange_events("tempOff")
      assert tail[4] == "reset_input"
      assert "tempOff.cancelled" in tail[5:]
      assert tail[-1] == "readline"
      assert [event for event in tail if event != "tempOff.cancelled"] == [
        *_exchange_events("tempOff"),
        *_exchange_events("setElmLockPos"),
      ]

      io.blocked_event = None

  async def test_reset_releases_the_lock_before_pending_cancellation_propagates(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    scopes: List[anyio.CancelScope] = []

    async with backend.lifespan(skip_home=True):
      io.blocked_event = "readline"
      baseline = len(io.events)

      async def reset() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.reset()
          except anyio.get_cancelled_exc_class():
            io.events.append("reset.cancelled")
            raise
          pytest.fail("the cancelled reset returned")

      # The guard is finite so a transaction that is never handed on fails this test rather
      # than hanging the run; a correct run reaches the assertions in milliseconds.
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(reset)
          await io.blocked_started.wait()
          scopes[0].cancel()
          task_group.start_soon(backend.lock_plate)
          await _settle()
          # The cancelled reset still holds the transaction, so the queued command is
          # waiting for it and nothing else has reached the wire.
          assert io.events[baseline:] == _exchange_events("resetDevice")
          io.release_blocked.set()

      tail = io.events[baseline:]
      # A reset is one transaction like any other command: it too is handed to the waiting
      # command before the cancelled caller learns it was cancelled.
      assert tail[:4] == _exchange_events("resetDevice")
      assert tail[4] == "reset_input"
      assert "reset.cancelled" in tail[5:]
      assert tail[-1] == "readline"
      assert [event for event in tail if event != "reset.cancelled"] == [
        *_exchange_events("resetDevice"),
        *_exchange_events("setElmLockPos"),
      ]

      io.blocked_event = None

  async def test_command_cancelled_while_waiting_for_the_transaction_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A command cancelled while waiting for the transaction puts nothing on the wire.

    This pins the outcome, not one mechanism: the backend stops such a waiter twice over,
    through a cancellable acquire and through the checkpoint that follows it, and either
    alone is enough. Shielding the acquire on its own therefore still passes here, as does
    dropping either checkpoint on its own. What this catches is losing the last stop
    standing: shielding the lock acquisition and then deleting the post-acquire checkpoint
    lets the cancelled waiter write, and no other test in this file observes that pair.
    """
    backend, io = _make_backend(monkeypatch)
    scopes: List[anyio.CancelScope] = []
    queued = anyio.Event()

    async with backend.lifespan(skip_home=True):
      io.blocked_event = "readline"
      baseline = len(io.events)

      async def lock_plate() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          # Set before the call, so the wait below returns only once this task has started.
          # `Event.set` does not yield, so it says nothing about how far the task got; the
          # `_settle()` after the wait is what carries it to its blocking point, waiting for
          # the transaction.
          queued.set()
          try:
            await backend.lock_plate()
          except anyio.get_cancelled_exc_class():
            io.events.append("setElmLockPos.cancelled")
            raise
          pytest.fail("the cancelled command returned")

      # The guard is finite so a transaction that is never handed on fails this test rather
      # than hanging the run; a correct run reaches the assertions in milliseconds.
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(backend.deactivate)
          await io.blocked_started.wait()
          task_group.start_soon(lock_plate)
          await queued.wait()
          await _settle()
          try:
            # The first exchange holds the transaction, so the queued command is waiting for
            # it and has put nothing on the wire.
            assert _wire_events(io, baseline, "setElmLockPos.cancelled") == _exchange_events(
              "tempOff"
            )
            scopes[0].cancel()
            await _settle()
            # Cancelling a command that never owned the transaction cannot put its bytes on
            # the wire, neither while it waits nor when the transaction is handed on.
            assert _wire_events(io, baseline, "setElmLockPos.cancelled") == _exchange_events(
              "tempOff"
            )
          finally:
            # Released even when an assertion above fails, because the held exchange is
            # shielded and would otherwise outlive the guard.
            io.release_blocked.set()

      assert "setElmLockPos.cancelled" in io.events[baseline:]
      assert io.commands == ["tempOff"]
      assert io.writes == [b"tempOff\r"]
      # The held exchange is the only one on the wire: the cancelled waiter contributed no
      # buffer flush, no write, and no reply read, even after the transaction was released.
      assert _wire_events(io, baseline, "setElmLockPos.cancelled") == _exchange_events("tempOff")

      io.blocked_event = None

  async def test_reset_cancelled_while_waiting_for_the_transaction_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A reset waits for the transaction like any command, and cancelled it writes nothing.

    The same measurement applies as for the command above: this pins the outcome, which two
    independent elements of the reset entry path each secure on their own.
    """
    backend, io = _make_backend(monkeypatch)
    scopes: List[anyio.CancelScope] = []
    queued = anyio.Event()

    async with backend.lifespan(skip_home=True):
      io.blocked_event = "readline"
      baseline = len(io.events)

      async def reset() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          queued.set()
          try:
            await backend.reset()
          except anyio.get_cancelled_exc_class():
            io.events.append("reset.cancelled")
            raise
          pytest.fail("the cancelled reset returned")

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(backend.deactivate)
          await io.blocked_started.wait()
          task_group.start_soon(reset)
          await queued.wait()
          await _settle()
          try:
            assert _wire_events(io, baseline, "reset.cancelled") == _exchange_events("tempOff")
            scopes[0].cancel()
            await _settle()
            # A reset waits for the transaction like any other command, so a cancelled reset
            # never reaches the device.
            assert _wire_events(io, baseline, "reset.cancelled") == _exchange_events("tempOff")
          finally:
            io.release_blocked.set()

      assert "reset.cancelled" in io.events[baseline:]
      assert io.commands == ["tempOff"]
      assert io.writes == [b"tempOff\r"]
      assert _wire_events(io, baseline, "reset.cancelled") == _exchange_events("tempOff")

      io.blocked_event = None

  async def test_command_delays_are_observed_on_both_backends(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    # The exact delay values are pinned on a virtual clock in `TestBioShakeDeadlines`, which
    # only Trio can run. This pays two of them in real time so that a changed delay is caught
    # on asyncio too, rather than only where a virtual clock happens to be available.
    cases: List[Tuple[Callable[[BioShake], Awaitable[Any]], float]] = [
      (lambda device: device.get_current_temperature(), 0.2),
      (lambda device: device.lock_plate(), 0.3),
    ]

    for call, delay in cases:
      backend, io = _make_backend(monkeypatch)
      async with backend.lifespan(skip_home=True):
        await call(backend)
      interval = io.readline_times[0] - io.write_times[0]
      assert interval >= delay
      assert interval < delay + _REAL_TIME_DELAY_SLACK

  async def test_command_overrun_reports_the_bioshake_command(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    # This test reads the overrun error's text rather than the bound's value, so it keeps both
    # backends on a real clock. The stall is a finite sleep, so a backend that applies no
    # bound at all answers late and fails this test instead of hanging it.
    backend, io = _make_backend(monkeypatch, read_stall=_REAL_TIME_STALL)

    async with backend.lifespan(skip_home=True):
      with pytest.raises(RuntimeError) as error_info:
        await backend.lock_plate()
      io.read_stall = 0.0

    assert str(error_info.value) == (
      "Unexpected error while sending 'setElmLockPos': RuntimeError: "
      "Timed out waiting for response to 'setElmLockPos'"
    )
    assert io.commands == ["setElmLockPos"]

  async def test_reset_overrun_reports_the_named_action(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    # The reply never contains the completion line, so the poll loop only ends on its own
    # budget. The double serves a bounded number of replies, so a loop that lost its budget
    # fails here rather than spinning forever.
    backend, io = _make_backend(
      monkeypatch,
      responses={"resetDevice": b"booting\r\n"},
      max_readlines=50,
    )

    async with backend.lifespan(skip_home=True):
      with patch.object(bioshake_module, "_RESET_TIMEOUT", 0.05):
        with pytest.raises(TimeoutError, match="Reset did not complete in time"):
          await backend.reset()

    assert io.commands == ["resetDevice"]
    assert io.events[-1] == "serial.exit"


class TestBioShakeDeadlines(AnyioTestBase):
  """Observe the command and reset budgets on a clock that costs no real time.

  A blocked exchange is abandoned exactly when its budget expires, so the time between
  dispatch and abandonment *is* the budget the backend applied. Those budgets are cancel-scope
  deadlines, which resolve their clock through the running backend, so only a backend-native
  virtual clock can advance them without real waiting. Trio supplies one; asyncio has no
  packaged equivalent, so these observations run trio-only. The budgets are backend
  independent, and dual-backend coverage of the surrounding behavior, including overrunning
  both budgets, lives in :class:`TestBioShakeCommands`.
  """

  # This class-level clock is shared by every node in the class and its virtual time
  # accumulates across them, so the assertions below measure intervals rather than absolute
  # instants.
  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_command_deadline_covers_the_delay_and_the_read(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    # One deadline covers a complete command exchange: its inter-command delay and its reply
    # read share a single budget of delay plus read timeout.
    cases: List[Tuple[str, Callable[[BioShake], Awaitable[Any]], float]] = [
      ("getTempActual", lambda device: device.get_current_temperature(), 0.2 + 2),
      ("setElmLockPos", lambda device: device.lock_plate(), 0.3 + 2),
      ("shakeGoHome", lambda device: device.home(), 5 + 2),
    ]

    for command, call, budget in cases:
      backend, io = _make_backend(monkeypatch)
      async with backend.lifespan(skip_home=True):
        io.read_stall = _STALLED_READ_SECONDS
        start = anyio.current_time()
        # The clock is virtual, so this guard costs no real time. A backend that applies no
        # bound cannot be interrupted by it, because the exchange is shielded; it is instead
        # released by the stall itself, which is longer than the guard.
        with anyio.fail_after(_DEADLOCK_GUARD):
          with pytest.raises(RuntimeError, match=f"Timed out waiting for response to '{command}'"):
            await call(backend)
        elapsed = anyio.current_time() - start
        io.read_stall = 0.0

      assert elapsed == pytest.approx(budget, abs=0.05)

  async def test_command_delays_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    cases: List[Tuple[Callable[[BioShake], Awaitable[Any]], float]] = [
      (lambda device: device.get_current_temperature(), 0.2),
      (lambda device: device.lock_plate(), 0.3),
      (lambda device: device.unlock_plate(), 0.3),
      (lambda device: device.home(), 5),
    ]

    for call, delay in cases:
      backend, io = _make_backend(monkeypatch)
      async with backend.lifespan(skip_home=True):
        await call(backend)
      # The delay separates the command write from its reply read.
      assert io.readline_times[0] - io.write_times[0] == pytest.approx(delay, abs=0.01)

  async def test_stop_shaking_waits_the_elm_settle_delay(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend.lifespan(skip_home=True):
      await backend.stop_shaking()
      elapsed_after_stop = anyio.current_time() - io.readline_times[-1]

    # Both against the constant, so the two cannot drift apart, and against its literal
    # value, so they cannot be moved together.
    assert elapsed_after_stop == pytest.approx(bioshake_module._ELM_SETTLE_DELAY, abs=0.05)
    assert elapsed_after_stop == pytest.approx(3.0, abs=0.05)

  async def test_entry_waits_between_reset_and_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend:
      pass

    # The device needs four seconds after a reset before it accepts the next command. The
    # reset loop pauses for its own poll sleep after the completion line arrives, so that
    # pause is charged to this interval too.
    assert io.write_times[1] - io.readline_times[0] == pytest.approx(
      4.0 + bioshake_module._RESET_POLL_SLEEP, abs=0.05
    )
    assert io.write_times[1] - io.readline_times[0] == pytest.approx(4.1, abs=0.05)

  async def test_reset_deadline_is_backend_neutral(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch, responses={"resetDevice": b"booting\r\n"})

    async with backend.lifespan(skip_home=True):
      start = anyio.current_time()
      with anyio.fail_after(_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Reset did not complete in time"):
          await backend.reset()
      elapsed = anyio.current_time() - start

    assert elapsed == pytest.approx(bioshake_module._RESET_TIMEOUT, abs=0.5)
    assert elapsed == pytest.approx(30.0, abs=0.5)
    assert io.commands == ["resetDevice"]

  async def test_reset_keeps_polling_after_a_silent_read(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    # Every reply read outlives the poll interval, so each one is abandoned and the loop
    # continues rather than failing. The interval between reads is therefore the poll.
    backend, io = _make_backend(
      monkeypatch,
      responses={"resetDevice": b"booting\r\n"},
      read_stall=_STALLED_READ_SECONDS,
    )

    async with backend.lifespan(skip_home=True):
      with anyio.fail_after(_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Reset did not complete in time"):
          await backend.reset()
      io.read_stall = 0.0

    assert len(io.readline_times) > 5
    intervals = [
      later - earlier for earlier, later in zip(io.readline_times, io.readline_times[1:])
    ]
    for interval in intervals:
      assert interval == pytest.approx(2.0, abs=0.05)

  async def test_reset_budget_interrupts_a_stalled_poll(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(bioshake_module, "_RESET_TIMEOUT", 0.5)
    backend, io = _make_backend(monkeypatch, read_stall=_STALLED_READ_SECONDS)

    async with backend.lifespan(skip_home=True):
      start = anyio.current_time()
      with anyio.fail_after(_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Reset did not complete in time"):
          await backend.reset()
      elapsed = anyio.current_time() - start

    assert elapsed == pytest.approx(0.5, abs=0.05)
    assert io.commands == ["resetDevice"]
    assert len(io.readline_times) == 1
