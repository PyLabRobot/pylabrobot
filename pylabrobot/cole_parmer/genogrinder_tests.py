import threading
from typing import Any, Awaitable, Callable, List, Optional, Sequence, Union, cast
from unittest.mock import patch

import anyio
import pytest
from serial import SerialTimeoutException

from pylabrobot.cole_parmer.genogrinder import GenoGrinder, GenoGrinderError
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase

ReplyEffect = Union[str, Callable[[], Awaitable[str]]]


class FakeSerialIO(HasLifespan):
  """Replay GenoGrinder replies and record lifecycle and wire events."""

  def __init__(
    self,
    replies: Sequence[ReplyEffect],
    events: Optional[List[str]] = None,
    exit_error: Optional[BaseException] = None,
    blocked_write: Optional[bytes] = None,
  ) -> None:
    super().__init__()
    self.replies = list(replies)
    self.events = events if events is not None else []
    self.exit_error = exit_error
    self.blocked_write = blocked_write
    self.slow_write: Optional[bytes] = None
    self.acquire_delay = 0.0
    self.writes: List[bytes] = []
    self.port = "FAKE"
    self.timeout = 50.0
    self.write_timeout = 0.02
    self._rx = bytearray()
    self._pending_reply: Optional[ReplyEffect] = None

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
    command = data.decode("ascii").rstrip("\r")
    self.events.append(f"write:{command}.started")
    await anyio.lowlevel.checkpoint()
    if data == self.slow_write:
      await self._acquire_slowly()
    self.writes.append(data)
    if data == self.blocked_write:
      await anyio.to_thread.run_sync(self._time_out_write)
    if self.replies:
      self._pending_reply = self.replies.pop(0)
    self.events.append(f"write:{command}.completed")

  async def _acquire_slowly(self) -> None:
    """Model the operation lock and capacity limiter a write waits on before pyserial."""
    await anyio.sleep(self.acquire_delay)
    self.events.append("write.acquired")

  def _time_out_write(self) -> None:
    """Model pyserial blocking until its configured write timeout expires."""
    threading.Event().wait(self.write_timeout)
    self.events.append("write.timeout")
    raise SerialTimeoutException("Write timeout")

  async def read(self, num_bytes: int = 1) -> bytes:
    if not self._rx and self._pending_reply is not None:
      effect = self._pending_reply
      self._pending_reply = None
      reply = await effect() if callable(effect) else effect
      self._rx.extend((reply + "\r").encode("ascii"))
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    if not out:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    return out

  async def reset_input_buffer(self) -> None:
    self._rx.clear()
    self._pending_reply = None

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


def make_device(
  replies: Sequence[ReplyEffect],
  events: Optional[List[str]] = None,
  exit_error: Optional[BaseException] = None,
  **kwargs: Any,
) -> tuple[GenoGrinder, FakeSerialIO]:
  """Build a GenoGrinder backed by a hardware-free serial transport."""
  kwargs.setdefault("timeout", 0.01)
  device = GenoGrinder(
    port="FAKE",
    command_settle=0,
    status_poll_interval=0,
    **kwargs,
  )
  fake_io = FakeSerialIO(replies, events, exit_error)
  device.io = cast(Serial, fake_io)
  return device, fake_io


def commands(fake_io: FakeSerialIO) -> List[str]:
  """Return command frames in order without carriage-return terminators."""
  return [data.decode("ascii").rstrip("\r") for data in fake_io.writes]


class TestGenoGrinderProtocol(AnyioTestBase):
  async def test_zero_run_timeout_observes_the_first_status(self) -> None:
    device, _ = make_device([])
    device.mix_timeout_margin = -1

    async def complete(_command: str) -> str:
      await anyio.lowlevel.checkpoint()
      return "Run Complete"

    with patch.object(device, "_command", complete):
      await device._wait_for_run_complete(1)

    async def mixing(_command: str) -> str:
      await anyio.lowlevel.checkpoint()
      return "Mixing"

    with patch.object(device, "_command", mixing):
      with pytest.raises(GenoGrinderError, match="Timed out waiting for the run"):
        await device._wait_for_run_complete(1)

  async def test_partial_line_is_returned_at_reply_deadline(self) -> None:
    device, fake = make_device([])
    fake._rx.extend(b"partial")
    with anyio.fail_after(1):
      assert await device._read_line(timeout=0.01) == "partial"

  def test_uses_managed_lifecycle_methods(self) -> None:
    assert GenoGrinder.__aenter__ is HasLifespan.__aenter__
    assert GenoGrinder.__aexit__ is HasLifespan.__aexit__
    assert GenoGrinder.setup is HasLifespan.setup
    assert GenoGrinder.stop is HasLifespan.stop

  async def test_lifespan_initializes_past_the_progress_line_and_stops(self) -> None:
    device, fake_io = make_device(["Initializing\rInitialize Complete"])

    async with device:
      pass

    assert commands(fake_io) == ["*03*", "*06*"]

  async def test_initialization_failure_clears_error_then_stops_before_close(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(["Fault\rFault"], events)

    with pytest.raises(GenoGrinderError):
      async with device:
        pytest.fail("failed initialization reached the body")

    assert commands(fake_io) == ["*03*", "*05*", "*06*"]
    assert events[-3:] == ["write:*06*.started", "write:*06*.completed", "serial.exit"]
    assert not device.is_active

  async def test_cancelled_initialization_completes_raw_stop_before_serial_close(self) -> None:
    events: List[str] = []
    initialization_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def block_initialization() -> str:
      initialization_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    device, fake_io = make_device([block_initialization], events)

    async def enter_device() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device:
          pytest.fail("cancelled initialization reached the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_device)
      await initialization_started.wait()
      scopes[0].cancel()

    assert commands(fake_io) == ["*03*", "*06*"]
    assert events[-3:] == ["write:*06*.started", "write:*06*.completed", "serial.exit"]
    assert not device.is_active

  async def test_cancelled_body_stops_before_serial_close(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device, fake_io = make_device(["Initializing\rInitialize Complete"], events)

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

    assert commands(fake_io) == ["*03*", "*06*"]
    assert events[-4:] == [
      "write:*06*.started",
      "write:*06*.completed",
      "serial.exit",
      "cancelled",
    ]
    assert not device.is_active

  async def test_cancelled_mix_completes_raw_stop_before_serial_close(self) -> None:
    events: List[str] = []
    mixing_started = anyio.Event()
    device, fake_io = make_device(
      [
        "Initializing\rInitialize Complete",
        "Parameters Set",
        "Running Sample",
        lambda: _block_forever(mixing_started),
      ],
      events,
    )

    async def mix() -> None:
      try:
        async with device:
          await device.shake(duration=5)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(mix)
      await mixing_started.wait()
      task_group.cancel_scope.cancel()

    assert commands(fake_io) == ["*03*", "*02,005,1750*", "*04*", "*01*", "*06*", "*06*"]
    assert events[-4:] == [
      "write:*06*.started",
      "write:*06*.completed",
      "serial.exit",
      "cancelled",
    ]

  async def test_cancelled_mix_stops_the_run_and_leaves_the_driver_usable(self) -> None:
    events: List[str] = []
    mixing_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    device, fake_io = make_device(
      [
        "Initializing\rInitialize Complete",
        "Parameters Set",
        "Running Sample",
        lambda: _block_forever(mixing_started),
        "",
        "Standby",
      ],
      events,
    )

    async def mix() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.shake(duration=5)
        except anyio.get_cancelled_exc_class():
          events.append("mix.cancelled")
          raise

    async with device:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(mix)
        await mixing_started.wait()
        scopes[0].cancel()

      assert commands(fake_io) == ["*03*", "*02,005,1750*", "*04*", "*01*", "*06*"]
      assert events.index("write:*06*.completed") < events.index("mix.cancelled")
      assert await device.request_status() == "Standby"

    assert commands(fake_io) == [
      "*03*",
      "*02,005,1750*",
      "*04*",
      "*01*",
      "*06*",
      "*01*",
      "*06*",
    ]
    assert not device.is_active

  async def test_completed_mix_sends_only_the_lifespan_exit_stop(self) -> None:
    device, fake_io = make_device(
      ["Initializing\rInitialize Complete", "Parameters Set", "Running Sample", "Run Complete"]
    )

    async with device:
      await device.shake(duration=5)

    assert commands(fake_io) == ["*03*", "*02,005,1750*", "*04*", "*01*", "*06*"]

  async def test_sequential_reuse_reinitializes_and_cleans_up(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(
      ["Initializing\rInitialize Complete", "", "Initializing\rInitialize Complete"], events
    )

    async with device:
      pass
    async with device:
      pass

    assert commands(fake_io) == ["*03*", "*06*", "*03*", "*06*"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates_after_stop(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device, fake_io = make_device(["Initializing\rInitialize Complete"], events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert commands(fake_io) == ["*03*", "*06*"]
    assert events[-3:] == ["write:*06*.started", "write:*06*.completed", "serial.exit"]
    assert not device.is_active

  async def test_cancelled_body_waits_for_stop_write_timeout_then_closes_serial(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    failures: List[BaseException] = []
    device, fake_io = make_device(["Initializing\rInitialize Complete"], events)
    fake_io.blocked_write = b"*06*\r"
    fake_io.timeout = 0.005

    async def use_device() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with device:
            body_started.set()
            await anyio.sleep_forever()
      except BaseException as error:
        failures.append(error)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_device)
      await body_started.wait()
      scopes[0].cancel()

    assert commands(fake_io) == ["*03*", "*06*"]
    assert events[-3:] == ["write:*06*.started", "write.timeout", "serial.exit"]
    assert len(failures) == 1
    assert isinstance(failures[0], SerialTimeoutException)
    assert isinstance(failures[0].__context__, anyio.get_cancelled_exc_class())
    assert not device.is_active

  async def test_slow_to_acquire_stop_still_writes_the_stop_frame(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(["Initializing\rInitialize Complete"], events)
    fake_io.write_timeout = 0.01
    fake_io.timeout = 1.0
    fake_io.slow_write = b"*06*\r"
    fake_io.acquire_delay = 0.05

    async with device:
      pass

    assert commands(fake_io) == ["*03*", "*06*"]
    assert events[-4:] == [
      "write:*06*.started",
      "write.acquired",
      "write:*06*.completed",
      "serial.exit",
    ]
    assert not device.is_active

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device([], events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []

  async def test_clamp_state(self) -> None:
    device, _ = make_device(["Clamp Open"])
    assert await device.request_clamp_state() == "open"

    device, _ = make_device(["Clamp Closed"])
    assert await device.request_clamp_state() == "closed"

    device, _ = make_device(["Standby"])
    assert await device.request_clamp_state() == "unknown"

  async def test_open_clamp_is_a_no_op_when_already_open(self) -> None:
    device, fake_io = make_device(["Clamp Open"])
    await device.open_clamp()
    assert commands(fake_io) == ["*15*"]

  async def test_open_clamp_moves_when_closed(self) -> None:
    device, fake_io = make_device(["Clamp Closed", "Clamp Open"])
    await device.open_clamp()
    assert commands(fake_io) == ["*15*", "*11*"]

  async def test_close_clamp_moves_when_open(self) -> None:
    device, fake_io = make_device(["Clamp Open", "Clamp Closed"])
    await device.close_clamp()
    assert commands(fake_io) == ["*15*", "*12*"]

  async def test_clamp_commands_suppressed_on_fixed_clamps(self) -> None:
    device, fake_io = make_device([], use_clamp_commands=False)
    await device.open_clamp()
    await device.close_clamp()
    assert commands(fake_io) == []

  async def test_home_clamp(self) -> None:
    device, fake_io = make_device(["Home Complete"])
    await device.home_clamp()
    assert commands(fake_io) == ["*10*"]

  async def test_shake_sets_parameters_then_runs_to_completion(self) -> None:
    device, fake_io = make_device(
      ["Parameters Set", "Running Sample", "Mixing", "Unlocking", "Run Complete"]
    )
    await device.shake(duration=45, speed=1500)
    assert commands(fake_io) == ["*02,045,1500*", "*04*", "*01*", "*01*", "*01*"]

  async def test_shake_rejects_out_of_range_arguments(self) -> None:
    device, fake_io = make_device([])
    with pytest.raises(ValueError):
      await device.shake(duration=1000)
    with pytest.raises(ValueError):
      await device.shake(speed=10000)
    assert commands(fake_io) == []

  async def test_shake_raises_on_an_unexpected_state(self) -> None:
    device, _ = make_device(["Parameters Set", "Running Sample", "Fault"])
    with pytest.raises(GenoGrinderError):
      await device.shake(duration=5)

  async def test_shake_times_out(self) -> None:
    device, _ = make_device(["Parameters Set", "Running Sample"] + ["Mixing"] * 10)
    device.mix_timeout_margin = -1
    with pytest.raises(GenoGrinderError):
      await device.shake(duration=1)

  async def test_run_deadline_interrupts_a_blocked_status_reply(self) -> None:
    async def blocked_status() -> str:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    device, _ = make_device([blocked_status], timeout=1)
    device.mix_timeout_margin = -0.98

    with anyio.fail_after(0.3):
      with pytest.raises(GenoGrinderError, match="Timed out waiting for the run to complete"):
        await device._wait_for_run_complete(duration=1)

  async def test_run_deadline_bounds_stalled_status_and_stops_machine(self) -> None:
    started = anyio.Event()
    device, fake = make_device(
      ["Parameters Set", "Running Sample", lambda: _block_forever(started)]
    )
    device.mix_timeout_margin = -0.98
    device.timeout = 1
    with anyio.fail_after(0.2):
      with pytest.raises(GenoGrinderError, match="Timed out waiting for the run"):
        await device.shake(duration=1)
    assert commands(fake) == ["*02,001,1750*", "*04*", "*01*", "*06*"]

  async def test_stop_shaking(self) -> None:
    device, fake_io = make_device(["Standby"])
    await device.stop_shaking()
    assert commands(fake_io) == ["*06*"]


async def _block_forever(started: anyio.Event) -> str:
  """Signal a blocked reply used to cancel an in-progress mix."""
  started.set()
  await anyio.sleep_forever()
  raise AssertionError("unreachable")
