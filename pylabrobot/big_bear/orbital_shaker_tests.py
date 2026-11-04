from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.big_bear import BigBearError, BigBearOrbitalShaker
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase

ResponseEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]
WriteEffect = Callable[[], Awaitable[None]]


def _exception_chain(error: BaseException) -> List[BaseException]:
  """Return an exception and its contexts, rejecting context cycles."""
  chain: List[BaseException] = []
  current: Optional[BaseException] = error
  while current is not None:
    assert current not in chain, "exception context chain contains a cycle"
    chain.append(current)
    current = current.__context__
  return chain


class FakeSerialIO(HasLifespan):
  """Replay BigBear shaker responses and record lifecycle and wire events without hardware."""

  def __init__(
    self,
    responses: Optional[List[ResponseEffect]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
    after_entry: Optional[Callable[[], None]] = None,
    before_write: Optional[dict] = None,
    after_write: Optional[dict] = None,
  ) -> None:
    super().__init__()
    self.responses: Deque[ResponseEffect] = deque(responses or [])
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.after_entry = after_entry
    self.before_write = before_write or {}
    self.after_write = after_write or {}
    self.writes: List[bytes] = []
    self.port = "COM7"
    self._rx = bytearray()
    self._read_timeout = 10.0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error
    if self.block_entry is not None:
      self.block_entry.set()
      await anyio.sleep_forever()
    if self.after_entry is not None:
      self.after_entry()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    effect = self.before_write.get(data)
    if effect is not None:
      await effect()
    self.writes.append(data)
    self.events.append(f"write:{data!r}")
    effect = self.after_write.get(data)
    if effect is not None:
      await effect()

  async def read(self, num_bytes: int = 1) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self._rx and self.responses:
      effect = self.responses.popleft()
      if isinstance(effect, BaseException):
        raise effect
      response = await effect() if callable(effect) else effect
      self._rx.extend(response)
    chunk = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    if not chunk:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    return chunk

  def get_read_timeout(self) -> float:
    return self._read_timeout

  def set_read_timeout(self, timeout: float) -> None:
    self._read_timeout = timeout


def make_device(
  responses: Optional[List[ResponseEffect]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  after_entry: Optional[Callable[[], None]] = None,
  before_write: Optional[dict] = None,
  after_write: Optional[dict] = None,
  num_shakers: int = 1,
  command_delay: float = 0.0,
  daisy_chain_settle: float = 0.0,
  stop_settle: float = 0.01,
  timeout: float = 0.01,
) -> tuple:
  """Build a BigBearOrbitalShaker backed by a hardware-free serial transport."""
  device = BigBearOrbitalShaker(
    port="COM7",
    num_shakers=num_shakers,
    timeout=timeout,
    command_delay=command_delay,
    daisy_chain_settle=daisy_chain_settle,
    stop_settle=stop_settle,
  )
  fake_io = FakeSerialIO(
    responses, events, entry_error, exit_error, block_entry, after_entry, before_write, after_write
  )
  device.io = cast(Serial, fake_io)
  return device, fake_io


async def _block_and_signal(event: anyio.Event) -> None:
  event.set()
  await anyio.sleep_forever()


async def _blocking_read_effect(event: anyio.Event) -> bytes:
  event.set()
  await anyio.sleep_forever()
  raise AssertionError("unreachable")


async def _block_once(event: anyio.Event, triggered: Dict[str, bool]) -> None:
  """Block only the first matching write; later writes to the same frame pass through.

  Cleanup after a cancelled start re-sends the same address, so a write hook keyed by
  raw bytes must not re-trigger while shielded cleanup is retrying that exact frame.
  """
  if triggered.get("done"):
    return
  triggered["done"] = True
  event.set()
  await anyio.sleep_forever()


class TestBigBearOrbitalShakerProtocol(AnyioTestBase):
  async def test_partial_reply_returns_at_caller_deadline(self) -> None:
    """A stalled CR-terminated reply returns the bytes already framed."""
    device, fake = make_device()
    fake._rx.extend(b"partial")
    with anyio.fail_after(1):
      assert await device._read_reply(timeout=0.01) == "partial"

  def test_num_shakers_out_of_range_raises_value_error(self) -> None:
    with pytest.raises(ValueError, match="num_shakers"):
      BigBearOrbitalShaker(port="COM7", num_shakers=0)
    with pytest.raises(ValueError, match="num_shakers"):
      BigBearOrbitalShaker(port="COM7", num_shakers=17)

  def test_address_formats_device_id_as_hex(self) -> None:
    assert BigBearOrbitalShaker._address(1) == "@1"
    assert BigBearOrbitalShaker._address(10) == "@A"
    assert BigBearOrbitalShaker._address(16) == "@10"

  def test_validate_device_id_rejects_out_of_range(self) -> None:
    device, _ = make_device(num_shakers=2)
    with pytest.raises(ValueError):
      device._validate_device_id(0)
    with pytest.raises(ValueError):
      device._validate_device_id(3)
    device._validate_device_id(1)
    device._validate_device_id(2)

  async def test_daisy_chain_entry_sends_expected_frame(self) -> None:
    device, fake_io = make_device([b"OK\r"])

    await device._enter_daisy_chain()

    assert fake_io.writes == [b"U\r"]

  async def test_check_num_shakers_matches_reports_no_error(self) -> None:
    device, fake_io = make_device([b"03\r"], num_shakers=3)

    await device._check_num_shakers()

    assert fake_io.writes == [b"U\r", b"~\r"]

  async def test_check_num_shakers_mismatch_raises_big_bear_error(self) -> None:
    device, _ = make_device([b"05\r"], num_shakers=3)

    with pytest.raises(BigBearError, match="Shaker count mismatch"):
      await device._check_num_shakers()

  async def test_set_speed_validates_range_and_writes_expected_frames(self) -> None:
    device, fake_io = make_device([b"V1200\r"])

    await device._set_speed(1, 1200)

    assert fake_io.writes == [b"@1V1200\r", b"@1?V1200\r"]
    with pytest.raises(ValueError, match="rpm"):
      await device._set_speed(1, 10)

  async def test_set_acceleration_validates_range_and_writes_expected_frames(self) -> None:
    device, fake_io = make_device([b"A5\r"])

    await device._set_acceleration(1, 5)

    assert fake_io.writes == [b"@1A5\r", b"@1?A5\r"]
    with pytest.raises(ValueError, match="acceleration"):
      await device._set_acceleration(1, 11)

  async def test_set_sequence_writes_direction_and_validates(self) -> None:
    device, fake_io = make_device([b"+\r", b"-\r"])

    await device._set_sequence(1, "CW")
    await device._set_sequence(1, "CCW")

    assert fake_io.writes == [b"@1+\r", b"@1?+\r", b"@1-\r", b"@1?-\r"]
    with pytest.raises(ValueError, match="sequence"):
      await device._set_sequence(1, "SIDEWAYS")  # type: ignore[arg-type]

  async def test_start_shaking_writes_full_command_sequence(self) -> None:
    device, fake_io = make_device([b"A2\r", b"V1500\r", b"+\r", b"OK\r"], num_shakers=1)

    await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)

    assert fake_io.writes == [
      b"@1A2\r",
      b"@1?A2\r",
      b"@1V1500\r",
      b"@1?V1500\r",
      b"@1+\r",
      b"@1?+\r",
      b"@1G\r",
      b"@1Q\r",
    ]

  async def test_start_shaking_error_reply_raises_big_bear_error(self) -> None:
    device, fake_io = make_device([b"A2\r", b"V1500\r", b"+\r", b"?:\r"])

    with pytest.raises(BigBearError, match="Starting the shaker failed"):
      await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)

    assert fake_io.writes[-2:] == [b"@1G\r", b"@1Q\r"]

  async def test_stop_shaking_writes_expected_frames(self) -> None:
    device, fake_io = make_device([b"OK\r"])

    await device.stop_shaking(device_id=1)

    assert fake_io.writes == [b"@1S\r", b"@1Q\r"]

  async def test_stop_shaking_error_reply_raises_big_bear_error(self) -> None:
    device, fake_io = make_device([b"?:\r"])

    with pytest.raises(BigBearError, match="Stopping the shaker failed"):
      await device.stop_shaking(device_id=1)

    assert fake_io.writes == [b"@1S\r", b"@1Q\r"]

  async def test_stop_all_sends_stop_to_every_configured_nest(self) -> None:
    device, fake_io = make_device([b"OK\r", b"OK\r", b"OK\r"], num_shakers=3)

    await device.stop_all()

    assert fake_io.writes == [
      b"@1S\r",
      b"@1Q\r",
      b"@2S\r",
      b"@2Q\r",
      b"@3S\r",
      b"@3Q\r",
    ]

  async def test_find_home_writes_expected_frame(self) -> None:
    device, fake_io = make_device([b"OK\r"])

    await device.find_home(device_id=1)

    assert fake_io.writes == [b"@1F\r", b"@1Q\r"]

  async def test_home_all_sends_home_to_every_configured_nest(self) -> None:
    device, fake_io = make_device([b"OK\r", b"OK\r"], num_shakers=2)

    await device.home_all()

    assert fake_io.writes == [b"@1F\r", b"@1Q\r", b"@2F\r", b"@2Q\r"]

  async def test_get_serial_number_reads_three_fields(self) -> None:
    device, fake_io = make_device([b"Y123\r", b"X456\r", b"Z789\r"])

    serial_number = await device.get_serial_number(device_id=1)

    assert fake_io.writes == [b"@1Y\r", b"@1X\r", b"@1Z\r"]
    assert serial_number == "Y123X456Z789"


class TestBigBearOrbitalShakerLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert BigBearOrbitalShaker.__aenter__ is HasLifespan.__aenter__
    assert BigBearOrbitalShaker.__aexit__ is HasLifespan.__aexit__
    assert BigBearOrbitalShaker.setup is HasLifespan.setup
    assert BigBearOrbitalShaker.stop is HasLifespan.stop

  async def test_normal_exit_enters_daisy_chain_and_stops_all_nests_before_closing_serial(
    self,
  ) -> None:
    events: List[str] = []
    device, fake_io = make_device(
      [b"OK\r", b"02\r", b"OK\r", b"OK\r"], events=events, num_shakers=2
    )

    async with device:
      events.append("body")

    assert fake_io.writes == [
      b"U\r",
      b"U\r",
      b"~\r",
      b"@1S\r",
      b"@1Q\r",
      b"@2S\r",
      b"@2Q\r",
    ]
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"
    assert events.index("body") < events.index("serial.exit")
    stop1_index = fake_io.writes.index(b"@1S\r")
    stop2_index = fake_io.writes.index(b"@2S\r")
    assert stop1_index < stop2_index
    assert not device.is_active

  async def test_count_mismatch_rolls_back_stopping_configured_nests_and_closing_serial(
    self,
  ) -> None:
    events: List[str] = []
    device, fake_io = make_device(
      [b"OK\r", b"05\r", b"OK\r", b"OK\r"], events=events, num_shakers=2
    )

    with pytest.raises(BigBearError, match="Shaker count mismatch"):
      async with device:
        pytest.fail("failed count check reached the body")

    assert fake_io.writes == [b"U\r", b"U\r", b"~\r", b"@1S\r", b"@1Q\r", b"@2S\r", b"@2Q\r"]
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_exit_stops_every_nest_even_when_one_stop_reply_is_error(self) -> None:
    device, fake_io = make_device([b"OK\r", b"02\r", b"?:\r", b"OK\r"], num_shakers=2)

    with pytest.raises(BigBearError, match="Stopping the shaker failed"):
      async with device:
        pass

    assert fake_io.writes == [
      b"U\r",
      b"U\r",
      b"~\r",
      b"@1S\r",
      b"@1Q\r",
      b"@2S\r",
      b"@2Q\r",
    ]

  async def test_exit_chains_each_stop_error_after_attempting_every_nest(self) -> None:
    device, fake_io = make_device(
      [b"OK\r", b"02\r", b"?:\r", b"?:\r"],
      num_shakers=2,
    )

    with pytest.raises(BigBearError, match="nest 2") as error_info:
      async with device:
        pass

    assert isinstance(error_info.value.__context__, BigBearError)
    assert "nest 1" in str(error_info.value.__context__)
    assert fake_io.writes[-4:] == [b"@1S\r", b"@1Q\r", b"@2S\r", b"@2Q\r"]

  async def test_exit_attempts_later_nests_after_base_exception(self) -> None:
    failure = BaseException("stop interrupted")
    device, fake_io = make_device(
      [b"OK\r", b"02\r", failure, b"OK\r"],
      num_shakers=2,
    )

    with pytest.raises(BaseException) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert fake_io.writes[-4:] == [b"@1S\r", b"@1Q\r", b"@2S\r", b"@2Q\r"]

  async def test_multiple_stop_failures_preserve_body_failure(self) -> None:
    body_failure = ValueError("body failed")
    first_stop_failure = RuntimeError("nest 1 transport failed")
    second_stop_failure = RuntimeError("nest 2 transport failed")
    device, _ = make_device(
      [b"OK\r", b"02\r", first_stop_failure, second_stop_failure],
      num_shakers=2,
    )

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        raise body_failure

    assert _exception_chain(error_info.value) == [
      second_stop_failure,
      first_stop_failure,
      body_failure,
    ]

  async def test_multiple_stop_failures_preserve_setup_failure(self) -> None:
    first_stop_failure = RuntimeError("nest 1 transport failed")
    second_stop_failure = RuntimeError("nest 2 transport failed")
    device, _ = make_device(
      [b"OK\r", b"05\r", first_stop_failure, second_stop_failure],
      num_shakers=2,
    )

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pytest.fail("failed setup reached the body")

    chain = _exception_chain(error_info.value)
    assert chain[:2] == [second_stop_failure, first_stop_failure]
    assert len(chain) == 3
    assert isinstance(chain[2], BigBearError)
    assert chain[2].title == "Shaker count mismatch"

  async def test_cancellation_during_serial_entry_closes_serial_without_stop_commands(
    self,
  ) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    device, fake_io = make_device(events=events, block_entry=entry_started, num_shakers=2)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == []
    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_cancellation_during_daisy_chain_write_still_stops_configured_nests(
    self,
  ) -> None:
    # _stop_all_for_exit is registered before daisy-chain initialization, so
    # rollback always attempts every configured nest, even though none has
    # necessarily been addressed yet.
    events: List[str] = []
    write_started = anyio.Event()
    device, fake_io = make_device(
      [b"OK\r", b"OK\r"],
      events=events,
      num_shakers=2,
      before_write={b"U\r": lambda: _block_and_signal(write_started)},
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await write_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == [b"@1S\r", b"@1Q\r", b"@2S\r", b"@2Q\r"]
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_cancellation_during_daisy_chain_settle_still_stops_configured_nests(
    self,
  ) -> None:
    events: List[str] = []
    settle_started = anyio.Event()

    async def after_u_write() -> None:
      settle_started.set()

    device, fake_io = make_device(
      [b"OK\r", b"OK\r"],
      events=events,
      num_shakers=2,
      daisy_chain_settle=1e9,
      after_write={b"U\r": after_u_write},
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await settle_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == [b"U\r", b"@1S\r", b"@1Q\r", b"@2S\r", b"@2Q\r"]
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_cancellation_during_daisy_chain_read_still_stops_configured_nests(
    self,
  ) -> None:
    events: List[str] = []
    read_started = anyio.Event()
    device, fake_io = make_device(
      [lambda: _blocking_read_effect(read_started), b"OK\r", b"OK\r"], events=events, num_shakers=2
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await read_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == [b"U\r", b"@1S\r", b"@1Q\r", b"@2S\r", b"@2Q\r"]
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_cancellation_during_check_num_shakers_still_stops_configured_nests(
    self,
  ) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    device, fake_io = make_device(
      [b"OK\r", b"OK\r", b"OK\r"],
      events=events,
      num_shakers=2,
      before_write={b"~\r": lambda: _block_and_signal(write_started)},
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await write_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == [b"U\r", b"U\r", b"@1S\r", b"@1Q\r", b"@2S\r", b"@2Q\r"]
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_cancellation_during_shaker_count_read_still_stops_configured_nests(
    self,
  ) -> None:
    events: List[str] = []
    count_read_started = anyio.Event()
    device, fake_io = make_device(
      [b"OK\r", lambda: _blocking_read_effect(count_read_started), b"OK\r", b"OK\r"],
      events=events,
      num_shakers=2,
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await count_read_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == [
      b"U\r",
      b"U\r",
      b"~\r",
      b"@1S\r",
      b"@1Q\r",
      b"@2S\r",
      b"@2Q\r",
    ]
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_body_cancellation_stops_all_nests_before_closing_serial(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device, fake_io = make_device([b"OK\r", b"01\r", b"OK\r"], events=events, num_shakers=1)

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

    assert fake_io.writes == [b"U\r", b"U\r", b"~\r", b"@1S\r", b"@1Q\r"]
    assert events.index("serial.exit") < events.index("cancelled")
    assert not device.is_active

  async def test_sequential_reuse_enters_and_exits_twice(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(
      [b"OK\r", b"01\r", b"OK\r", b"OK\r", b"01\r", b"OK\r"], events=events, num_shakers=1
    )

    async with device:
      pass
    async with device:
      pass

    enter_indices = [i for i, event in enumerate(events) if event == "serial.enter"]
    exit_indices = [i for i, event in enumerate(events) if event == "serial.exit"]
    assert len(enter_indices) == 2
    assert len(exit_indices) == 2
    assert enter_indices[0] < exit_indices[0] < enter_indices[1] < exit_indices[1]
    assert fake_io.writes == [
      b"U\r",
      b"U\r",
      b"~\r",
      b"@1S\r",
      b"@1Q\r",
      b"U\r",
      b"U\r",
      b"~\r",
      b"@1S\r",
      b"@1Q\r",
    ]

  async def test_failed_exit_stop_retains_unknown_state_until_next_lifespan_retry(self) -> None:
    device, fake_io = make_device(
      [b"OK\r", b"01\r", b"?:\r", b"OK\r", b"01\r", b"OK\r"],
    )
    with pytest.raises(BigBearError, match="Stopping the shaker failed"):
      async with device:
        pass

    assert device._shaking_state_unknown[1]
    async with device:
      with pytest.raises(RuntimeError, match="already active"):
        async with device.shaking(900):
          pytest.fail("unknown nest adopted")
    assert not device._shaking_state_unknown[1]
    assert fake_io.writes.count(b"@1S\r") == 2

  async def test_serial_close_failure_propagates_after_stopping_nests(self) -> None:
    failure = RuntimeError("serial close failed")
    device, fake_io = make_device([b"OK\r", b"01\r", b"OK\r"], exit_error=failure, num_shakers=1)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert fake_io.writes == [b"U\r", b"U\r", b"~\r", b"@1S\r", b"@1Q\r"]
    assert not device.is_active

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []


class TestBigBearOrbitalShakerStartCancellation(AnyioTestBase):
  async def test_cancellation_before_start_write_sends_no_stop(self) -> None:
    device, fake_io = make_device([b"A2\r", b"V1500\r", b"+\r"])
    original_set_sequence = device._set_sequence
    start_scope: Optional[anyio.CancelScope] = None

    async def set_sequence_then_cancel(device_id: int, sequence: str) -> None:
      await original_set_sequence(device_id, cast(Any, sequence))
      assert start_scope is not None
      start_scope.cancel()

    device._set_sequence = set_sequence_then_cancel  # type: ignore[method-assign]

    with anyio.CancelScope() as scope:
      start_scope = scope
      await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)

    assert fake_io.writes == [
      b"@1A2\r",
      b"@1?A2\r",
      b"@1V1500\r",
      b"@1?V1500\r",
      b"@1+\r",
      b"@1?+\r",
    ]
    assert b"@1G\r" not in fake_io.writes
    assert b"@1S\r" not in fake_io.writes

  async def test_cancellation_after_start_write_stops_nest_before_propagating(self) -> None:
    events: List[str] = []
    start_written = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def complete_start_write() -> None:
      start_written.set()
      await release_write.wait()
      events.append("start.write.completed")

    device, fake_io = make_device(
      [b"A2\r", b"V1500\r", b"+\r", b"OK\r"],
      events=events,
      after_write={b"@1G\r": complete_start_write},
    )

    async def start() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(start)
      await start_written.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert fake_io.writes[-3:] == [b"@1G\r", b"@1S\r", b"@1Q\r"]
    assert events.index("start.write.completed") < events.index("cancelled")
    assert events.index("write:b'@1Q\\r'") < events.index("cancelled")

  async def test_cancellation_during_start_poll_write_stops_nest_before_propagating(
    self,
  ) -> None:
    events: List[str] = []
    poll_write_started = anyio.Event()
    triggered: Dict[str, bool] = {}
    device, fake_io = make_device(
      [b"A2\r", b"V1500\r", b"+\r", b"OK\r"],
      events=events,
      before_write={b"@1Q\r": lambda: _block_once(poll_write_started, triggered)},
    )

    async def start() -> None:
      try:
        await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(start)
      await poll_write_started.wait()
      task_group.cancel_scope.cancel()

    # The blocked Q write (status poll) is never recorded, but the stop cleanup's
    # own S write and Q write must both complete before cancellation propagates.
    assert fake_io.writes.count(b"@1G\r") == 1
    assert fake_io.writes[-2:] == [b"@1S\r", b"@1Q\r"]
    assert events.index("write:b'@1Q\\r'") < events.index("cancelled")

  async def test_cancellation_after_start_poll_write_stops_nest_before_propagating(
    self,
  ) -> None:
    events: List[str] = []
    poll_written = anyio.Event()
    triggered: Dict[str, bool] = {}
    device, fake_io = make_device(
      [b"A2\r", b"V1500\r", b"+\r", b"OK\r"],
      events=events,
      after_write={b"@1Q\r": lambda: _block_once(poll_written, triggered)},
    )

    async def start() -> None:
      try:
        await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(start)
      await poll_written.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes[-3:] == [b"@1Q\r", b"@1S\r", b"@1Q\r"]
    assert events.index("write:b'@1S\\r'") < events.index("cancelled")

  async def test_cancellation_during_start_poll_read_stops_nest_before_propagating(
    self,
  ) -> None:
    events: List[str] = []
    poll_read_started = anyio.Event()
    device, fake_io = make_device(
      [b"A2\r", b"V1500\r", b"+\r", lambda: _blocking_read_effect(poll_read_started), b"OK\r"],
      events=events,
    )

    async def start() -> None:
      try:
        await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(start)
      await poll_read_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes[-4:] == [b"@1G\r", b"@1Q\r", b"@1S\r", b"@1Q\r"]
    assert events.index("write:b'@1S\\r'") < events.index("cancelled")

  async def test_stop_error_reply_replaces_cancellation_and_preserves_it(self) -> None:
    poll_read_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    failures: List[BaseException] = []
    device, fake_io = make_device(
      [b"A2\r", b"V1500\r", b"+\r", lambda: _blocking_read_effect(poll_read_started), b"?:\r"]
    )

    async def start() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)
      except BaseException as error:
        failures.append(error)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(start)
      await poll_read_started.wait()
      scopes[0].cancel()

    assert fake_io.writes[-4:] == [b"@1G\r", b"@1Q\r", b"@1S\r", b"@1Q\r"]
    assert len(failures) == 1
    assert isinstance(failures[0], BigBearError)
    assert failures[0].title == "Stopping the shaker failed"
    assert isinstance(failures[0].__context__, anyio.get_cancelled_exc_class())

  async def test_stop_transport_failure_replaces_cancellation_and_preserves_it(self) -> None:
    poll_read_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    failures: List[BaseException] = []
    stop_failure = RuntimeError("stop transport failed")
    device, fake_io = make_device(
      [
        b"A2\r",
        b"V1500\r",
        b"+\r",
        lambda: _blocking_read_effect(poll_read_started),
        stop_failure,
      ]
    )

    async def start() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)
      except BaseException as error:
        failures.append(error)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(start)
      await poll_read_started.wait()
      scopes[0].cancel()

    assert fake_io.writes[-4:] == [b"@1G\r", b"@1Q\r", b"@1S\r", b"@1Q\r"]
    assert failures == [stop_failure]
    assert isinstance(stop_failure.__context__, anyio.get_cancelled_exc_class())

  @pytest.mark.parametrize(
    ("blocked_frame", "responses", "completed_writes"),
    [
      (b"@1A2\r", [], []),
      (b"@1?A2\r", [], [b"@1A2\r"]),
      (b"@1V1500\r", [b"A2\r"], [b"@1A2\r", b"@1?A2\r"]),
      (b"@1?V1500\r", [b"A2\r"], [b"@1A2\r", b"@1?A2\r", b"@1V1500\r"]),
      (
        b"@1+\r",
        [b"A2\r", b"V1500\r"],
        [b"@1A2\r", b"@1?A2\r", b"@1V1500\r", b"@1?V1500\r"],
      ),
      (
        b"@1?+\r",
        [b"A2\r", b"V1500\r"],
        [b"@1A2\r", b"@1?A2\r", b"@1V1500\r", b"@1?V1500\r", b"@1+\r"],
      ),
    ],
  )
  async def test_cancellation_before_each_parameter_write_sends_no_stop(
    self,
    blocked_frame: bytes,
    responses: List[ResponseEffect],
    completed_writes: List[bytes],
  ) -> None:
    write_started = anyio.Event()
    device, fake_io = make_device(
      responses,
      before_write={blocked_frame: lambda: _block_and_signal(write_started)},
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_start_and_wait, device)
      await write_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == completed_writes
    assert b"@1G\r" not in fake_io.writes
    assert b"@1S\r" not in fake_io.writes

  @pytest.mark.parametrize(
    ("responses", "completed_writes"),
    [
      (
        [],
        [b"@1A2\r", b"@1?A2\r"],
      ),
      (
        [b"A2\r"],
        [b"@1A2\r", b"@1?A2\r", b"@1V1500\r", b"@1?V1500\r"],
      ),
      (
        [b"A2\r", b"V1500\r"],
        [b"@1A2\r", b"@1?A2\r", b"@1V1500\r", b"@1?V1500\r", b"@1+\r", b"@1?+\r"],
      ),
    ],
  )
  async def test_cancellation_during_each_parameter_read_sends_no_stop(
    self,
    responses: List[ResponseEffect],
    completed_writes: List[bytes],
  ) -> None:
    read_started = anyio.Event()
    device, fake_io = make_device(
      [*responses, lambda: _blocking_read_effect(read_started)],
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_start_and_wait, device)
      await read_started.wait()
      task_group.cancel_scope.cancel()

    assert fake_io.writes == completed_writes
    assert b"@1G\r" not in fake_io.writes
    assert b"@1S\r" not in fake_io.writes

  async def test_concurrent_start_shaking_serializes_transitions_before_cancellation(
    self,
  ) -> None:
    events: List[str] = []
    nest1_started = anyio.Event()
    nest2_started = anyio.Event()
    nest1_triggered: Dict[str, bool] = {}
    nest2_triggered: Dict[str, bool] = {}

    device, fake_io = make_device(
      [b"A1\r", b"V1000\r", b"+\r", b"A1\r", b"V1000\r", b"+\r", b"OK\r", b"OK\r"],
      events=events,
      num_shakers=2,
      before_write={
        b"@1Q\r": lambda: _block_once(nest1_started, nest1_triggered),
        b"@2Q\r": lambda: _block_once(nest2_started, nest2_triggered),
      },
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(device.start_shaking, 1000, 1, "CW", 1)
      await nest1_started.wait()
      task_group.start_soon(device.start_shaking, 1000, 1, "CW", 2)
      await anyio.sleep(0)
      task_group.cancel_scope.cancel()

    assert b"@1S\r" in fake_io.writes
    assert not nest2_started.is_set()
    assert not any(write.startswith(b"@2") for write in fake_io.writes)


class TestBigBearOrbitalShakerShakingScope(AnyioTestBase):
  async def test_scope_starts_yields_and_stops_only_its_nest(self) -> None:
    events: List[str] = []
    device, fake_io = make_device(
      [b"OK\r", b"02\r", b"A2\r", b"V1500\r", b"+\r", b"OK\r", b"OK\r", b"OK\r", b"OK\r"],
      events=events,
      num_shakers=2,
    )

    async with device:
      fake_io.writes.clear()
      async with device.shaking(1500, acceleration=2, device_id=1):
        events.append("body")
        assert fake_io.writes[-2:] == [b"@1G\r", b"@1Q\r"]
      assert fake_io.writes[-2:] == [b"@1S\r", b"@1Q\r"]
      assert b"@2S\r" not in fake_io.writes

    assert events.index("body") < events.index("write:b'@1S\\r'")

  async def test_scope_body_failure_still_stops_its_nest(self) -> None:
    device, fake_io = make_device(
      [b"OK\r", b"01\r", b"A1\r", b"V900\r", b"+\r", b"OK\r", b"OK\r", b"OK\r"]
    )

    async with device:
      fake_io.writes.clear()
      with pytest.raises(ValueError, match="body failed"):
        async with device.shaking(900):
          raise ValueError("body failed")
      assert fake_io.writes[-2:] == [b"@1S\r", b"@1Q\r"]

  async def test_scope_stop_failure_takes_precedence_and_preserves_body_context(self) -> None:
    body_error = ValueError("body failed")
    device, fake_io = make_device(
      [
        b"OK\r",
        b"01\r",
        b"A1\r",
        b"V900\r",
        b"+\r",
        b"OK\r",
        b"?:\r",
        b"OK\r",
      ]
    )

    async with device:
      fake_io.writes.clear()
      with pytest.raises(BigBearError, match="Stopping the shaker failed") as error_info:
        async with device.shaking(900):
          raise body_error

      assert error_info.value.__context__ is body_error
      assert device._shaking_active[1]
      assert device._shaking_state_unknown[1]
      assert 1 not in device._shaking_scope_tokens

      await device.stop_shaking()
      assert not device._shaking_active[1]
      assert not device._shaking_state_unknown[1]
      assert fake_io.writes == [
        b"@1A1\r",
        b"@1?A1\r",
        b"@1V900\r",
        b"@1?V900\r",
        b"@1+\r",
        b"@1?+\r",
        b"@1G\r",
        b"@1Q\r",
        b"@1S\r",
        b"@1Q\r",
        b"@1S\r",
        b"@1Q\r",
      ]

  async def test_scope_cancellation_before_start_commit_sends_no_stop(self) -> None:
    scopes: List[anyio.CancelScope] = []
    device, fake_io = make_device(
      [b"OK\r", b"01\r", b"A1\r", b"V900\r", b"+\r", b"OK\r"],
    )
    original_set_sequence = device._set_sequence

    async def set_sequence_then_cancel(device_id: int, sequence: str) -> None:
      await original_set_sequence(device_id, cast(Any, sequence))
      scopes[0].cancel()

    device._set_sequence = set_sequence_then_cancel  # type: ignore[method-assign]

    async def enter_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device.shaking(900):
          pytest.fail("cancelled scope reached its body")

    async with device:
      fake_io.writes.clear()
      await enter_scope()

      assert b"@1G\r" not in fake_io.writes
      assert b"@1S\r" not in fake_io.writes

  async def test_scope_cancellation_after_start_commit_stops_before_propagating(self) -> None:
    events: List[str] = []
    start_written = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def complete_start_write() -> None:
      start_written.set()
      await release_write.wait()

    device, fake_io = make_device(
      [b"OK\r", b"01\r", b"A1\r", b"V900\r", b"+\r", b"OK\r", b"OK\r", b"OK\r"],
      events=events,
      after_write={b"@1G\r": complete_start_write},
    )

    async def run_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with device.shaking(900):
            pytest.fail("cancelled scope reached its body")
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with device:
      fake_io.writes.clear()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_scope)
        await start_written.wait()
        scopes[0].cancel()
        await anyio.sleep(0)
        assert "cancelled" not in events
        release_write.set()

      assert fake_io.writes[-2:] == [b"@1S\r", b"@1Q\r"]
      assert events.index("write:b'@1Q\\r'") < events.index("cancelled")

  async def test_scope_stop_failure_retains_state_for_direct_retry(self) -> None:
    device, fake_io = make_device(
      [
        b"OK\r",
        b"01\r",
        b"A1\r",
        b"V900\r",
        b"+\r",
        b"OK\r",
        b"?:\r",
        b"OK\r",
        b"OK\r",
      ]
    )

    async with device:
      with pytest.raises(BigBearError, match="Stopping the shaker failed"):
        async with device.shaking(900):
          pass

      writes_before_retry = list(fake_io.writes)
      with pytest.raises(RuntimeError, match="already active"):
        async with device.shaking(800):
          pytest.fail("scope adopted state left unknown by failed cleanup")
      assert fake_io.writes == writes_before_retry

      await device.stop_shaking()
      retry_stop = fake_io.writes.index(b"@1S\r", fake_io.writes.index(b"@1S\r") + 1)
      assert fake_io.writes[retry_stop : retry_stop + 2] == [b"@1S\r", b"@1Q\r"]

  async def test_same_nest_nested_and_concurrent_scopes_are_rejected(self) -> None:
    entered = anyio.Event()
    release = anyio.Event()
    failures: List[RuntimeError] = []
    device, fake_io = make_device(
      [b"OK\r", b"01\r", b"A1\r", b"V900\r", b"+\r", b"OK\r", b"OK\r", b"OK\r"]
    )

    async def hold_scope() -> None:
      async with device.shaking(900):
        entered.set()
        await release.wait()

    async def enter_concurrently() -> None:
      try:
        async with device.shaking(800):
          pytest.fail("concurrent scope reached its body")
      except RuntimeError as error:
        failures.append(error)

    async with device:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(hold_scope)
        await entered.wait()
        with pytest.raises(RuntimeError, match="already scoped"):
          async with device.shaking(800):
            pytest.fail("nested scope reached its body")
        task_group.start_soon(enter_concurrently)
        await anyio.sleep(0)
        release.set()

      assert len(failures) == 1
      assert "already scoped" in str(failures[0])
      assert fake_io.writes.count(b"@1G\r") == 1

  async def test_direct_transitions_are_rejected_only_for_scoped_nest(self) -> None:
    device, fake_io = make_device(
      [
        b"OK\r",
        b"02\r",
        b"A1\r",
        b"V900\r",
        b"+\r",
        b"OK\r",
        b"A1\r",
        b"V800\r",
        b"+\r",
        b"OK\r",
        b"OK\r",
        b"OK\r",
        b"OK\r",
        b"OK\r",
      ],
      num_shakers=2,
    )

    async with device:
      async with device.shaking(900, device_id=1):
        with pytest.raises(RuntimeError, match="scope"):
          await device.start_shaking(700, device_id=1)
        with pytest.raises(RuntimeError, match="scope"):
          await device.stop_shaking(device_id=1)
        await device.start_shaking(800, device_id=2)
        await device.stop_shaking(device_id=2)

      assert fake_io.writes.count(b"@1G\r") == 1
      assert fake_io.writes.count(b"@1S\r") == 1

  async def test_stop_all_preflights_every_scope_before_sending_commands(self) -> None:
    device, fake_io = make_device(
      [b"OK\r", b"02\r", b"A1\r", b"V900\r", b"+\r", b"OK\r", b"OK\r", b"OK\r", b"OK\r"],
      num_shakers=2,
    )

    async with device:
      async with device.shaking(900, device_id=2):
        writes_before = list(fake_io.writes)
        with pytest.raises(RuntimeError, match="scope"):
          await device.stop_all()
        assert fake_io.writes == writes_before

  async def test_scopes_on_different_nests_have_independent_bodies_and_cleanup(self) -> None:
    nest1_entered = anyio.Event()
    nest2_entered = anyio.Event()
    release = anyio.Event()
    device, fake_io = make_device(
      [
        b"OK\r",
        b"02\r",
        b"A1\r",
        b"V900\r",
        b"+\r",
        b"OK\r",
        b"A1\r",
        b"V800\r",
        b"+\r",
        b"OK\r",
        b"OK\r",
        b"OK\r",
        b"OK\r",
        b"OK\r",
      ],
      num_shakers=2,
    )

    async def use_nest(rpm: int, device_id: int, entered: anyio.Event) -> None:
      async with device.shaking(rpm, device_id=device_id):
        entered.set()
        await release.wait()

    async with device:
      fake_io.writes.clear()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_nest, 900, 1, nest1_entered)
        task_group.start_soon(use_nest, 800, 2, nest2_entered)
        await nest1_entered.wait()
        await nest2_entered.wait()
        release.set()

      assert fake_io.writes.count(b"@1G\r") == 1
      assert fake_io.writes.count(b"@2G\r") == 1
      assert fake_io.writes.count(b"@1S\r") == 1
      assert fake_io.writes.count(b"@2S\r") == 1

  async def test_scope_rejects_preexisting_durable_shaking_without_stopping_it(self) -> None:
    device, fake_io = make_device(
      [b"OK\r", b"01\r", b"A1\r", b"V900\r", b"+\r", b"OK\r", b"OK\r", b"OK\r"]
    )

    async with device:
      await device.start_shaking(900)
      writes_before = list(fake_io.writes)
      with pytest.raises(RuntimeError, match="already active"):
        async with device.shaking(800):
          pytest.fail("scope adopted durable shaking")
      assert fake_io.writes == writes_before
      await device.stop_shaking()

  async def test_parent_exit_force_stops_both_nests_and_invalidates_scope_tokens(self) -> None:
    entered1 = anyio.Event()
    entered2 = anyio.Event()
    release = anyio.Event()
    device, fake_io = make_device(
      [
        b"OK\r",
        b"02\r",
        b"A1\r",
        b"V900\r",
        b"+\r",
        b"OK\r",
        b"A1\r",
        b"V800\r",
        b"+\r",
        b"OK\r",
        b"OK\r",
        b"OK\r",
      ],
      num_shakers=2,
    )
    lifespan = device.lifespan()
    await lifespan.__aenter__()

    async def use_nest(rpm: int, device_id: int, entered: anyio.Event) -> None:
      async with device.shaking(rpm, device_id=device_id):
        entered.set()
        await release.wait()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_nest, 900, 1, entered1)
      task_group.start_soon(use_nest, 800, 2, entered2)
      await entered1.wait()
      await entered2.wait()
      await lifespan.__aexit__(None, None, None)
      assert fake_io.writes[-4:] == [b"@1S\r", b"@1Q\r", b"@2S\r", b"@2Q\r"]
      stop_count = fake_io.writes.count(b"@1S\r") + fake_io.writes.count(b"@2S\r")
      release.set()

    assert fake_io.writes.count(b"@1S\r") + fake_io.writes.count(b"@2S\r") == stop_count


async def _enter_and_wait(device: BigBearOrbitalShaker) -> None:
  async with device:
    pytest.fail("cancelled entry reached the body")


async def _start_and_wait(device: BigBearOrbitalShaker) -> None:
  await device.start_shaking(rpm=1500, acceleration=2, sequence="CW", device_id=1)
  pytest.fail("cancelled start_shaking returned normally")
