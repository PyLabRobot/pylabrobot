from typing import Any, Awaitable, Callable, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.sartorius.entris import SartoriusEntris2, SartoriusError
from pylabrobot.testing.concurrency import AnyioTestBase

LineEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]


class FakeSerialIO(HasLifespan):
  """Replay Entris lines and record lifecycle and wire events without hardware."""

  def __init__(
    self,
    lines: Optional[List[LineEffect]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
    blocked_write: Optional[bytes] = None,
  ) -> None:
    super().__init__()
    self.lines = list(lines or [])
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.blocked_write = blocked_write
    self.write_started = anyio.Event()
    self.release_write = anyio.Event()
    self.writes: List[bytes] = []
    self.reset_count = 0
    self.port = "FAKE"

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

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def reset_input_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.reset_count += 1
    self.events.append("serial.reset_input")

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    self.writes.append(data)
    self.events.append(f"write:{data!r}")
    if data == self.blocked_write:
      self.write_started.set()
      await self.release_write.wait()
      self.events.append("write.completed")

  async def readline(self) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self.lines:
      return b""
    effect = self.lines.pop(0)
    if isinstance(effect, BaseException):
      raise effect
    return await effect() if callable(effect) else effect


def make_balance(
  lines: Optional[List[LineEffect]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  blocked_write: Optional[bytes] = None,
  tare_settle_s: float = 0,
) -> tuple[SartoriusEntris2, FakeSerialIO]:
  """Build an Entris II backed by a software serial transport."""
  balance = SartoriusEntris2(port="FAKE", tare_settle_s=tare_settle_s)
  fake_io = FakeSerialIO(lines, events, entry_error, exit_error, block_entry, blocked_write)
  balance.io = cast(Serial, fake_io)
  return balance, fake_io


class TestSartoriusEntrisProtocol(AnyioTestBase):
  async def test_startup_retries_probe_once_then_queries_serial_number(self) -> None:
    balance, fake_io = make_balance([b"Stat ERR 55\r\n", b"  + 1.00 g\r\n", b" SN 12 34 \r\n"])

    async with balance:
      assert balance.serial_number == "SN1234"

    assert fake_io.writes == [b"\x1bP\r\n", b"\x1bP\r\n", b"\x1bx2_\r\n"]
    assert fake_io.reset_count == 3

  async def test_startup_continues_to_serial_query_after_probe_retry_exhaustion(self) -> None:
    balance, fake_io = make_balance([b"Stat High\r\n", b"Stat Low\r\n", b"SERIAL-7\r\n"])

    async with balance:
      assert balance.serial_number == "SERIAL-7"

    assert fake_io.writes == [b"\x1bP\r\n", b"\x1bP\r\n", b"\x1bx2_\r\n"]

  async def test_commands_reset_input_frame_tokens_and_remove_response_spaces(self) -> None:
    balance, fake_io = make_balance([b" Model ENTRIS II \r\n", b" SN 99 01 \r\n"])

    assert await balance.get_model() == "ModelENTRISII"
    assert await balance.get_serial_number() == "SN9901"

    assert fake_io.writes == [b"\x1bx1_\r\n", b"\x1bx2_\r\n"]
    assert fake_io.reset_count == 2

  async def test_weight_reads_parse_sign_units_and_whitespace(self) -> None:
    balance, fake_io = make_balance([b"  + 12.340 g\r\n", b" - 0. kg\r\n"])

    assert await balance.measure_weight() == 12.34
    assert await balance.measure_weight() == -0.0

    assert fake_io.writes == [b"\x1bP\r\n", b"\x1bP\r\n"]

  @pytest.mark.parametrize("response", ["", "+", "1.2.3 g", "abc"])
  async def test_invalid_weight_responses_raise(self, response: str) -> None:
    balance, _ = make_balance()

    with pytest.raises(SartoriusError, match="Invalid weight value"):
      balance._parse_weight(response)

  async def test_device_error_includes_known_description(self) -> None:
    balance, _ = make_balance([b"Stat ERR 10\r\n"])

    with pytest.raises(SartoriusError, match="Taring not possible"):
      await balance.send_command("P")

  async def test_tare_and_zero_do_not_consume_lines_and_use_configured_frames(self) -> None:
    balance, fake_io = make_balance([b"still queued\r\n"])

    await balance.tare()
    await balance.zero()

    assert fake_io.writes == [b"\x1bT\r\n", b"\x1bV\r\n"]
    assert fake_io.reset_count == 2
    assert fake_io.lines == [b"still queued\r\n"]

  async def test_response_timeout_uses_public_error(self) -> None:
    balance, _ = make_balance()

    with pytest.raises(TimeoutError, match="Timeout while waiting for response from scale"):
      await balance.send_command("P", timeout=0)


class TestSartoriusEntrisLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert SartoriusEntris2.__aenter__ is HasLifespan.__aenter__
    assert SartoriusEntris2.__aexit__ is HasLifespan.__aexit__
    assert SartoriusEntris2.setup is HasLifespan.setup
    assert SartoriusEntris2.stop is HasLifespan.stop

  async def test_normal_exit_closes_serial(self) -> None:
    events: List[str] = []
    balance, _ = make_balance([b"1 g\r\n", b"SERIAL\r\n"], events)

    async with balance:
      events.append("body")

    assert events[-2:] == ["body", "serial.exit"]
    assert not balance.is_active

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    balance, _ = make_balance(events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with balance:
        pytest.fail("failed serial entry reached the body")

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not balance.is_active

  @pytest.mark.parametrize("failed_command", [b"\x1bP\r\n", b"\x1bx2_\r\n"])
  async def test_startup_phase_failure_closes_serial(self, failed_command: bytes) -> None:
    events: List[str] = []
    failure = RuntimeError("startup failed")
    lines: List[LineEffect]
    if failed_command == b"\x1bP\r\n":
      lines = [failure]
    else:
      lines = [b"1 g\r\n", failure]
    balance, _ = make_balance(lines, events)

    with pytest.raises(RuntimeError) as error_info:
      async with balance:
        pytest.fail("failed startup reached the body")

    assert error_info.value is failure
    assert events[-1] == "serial.exit"
    assert not balance.is_active

  async def test_cancelled_serial_entry_rolls_back(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    balance, _ = make_balance(events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, balance)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit"]
    assert not balance.is_active

  async def test_cancelled_body_closes_serial_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    balance, _ = make_balance([b"1 g\r\n", b"SERIAL\r\n"], events)

    async def use_balance() -> None:
      try:
        async with balance:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_balance)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-2:] == ["serial.exit", "cancelled"]
    assert not balance.is_active

  async def test_sequential_reuse_probes_and_closes_twice(self) -> None:
    events: List[str] = []
    balance, fake_io = make_balance(
      [b"1 g\r\n", b"SERIAL-1\r\n", b"2 g\r\n", b"SERIAL-2\r\n"], events
    )

    async with balance:
      assert balance.serial_number == "SERIAL-1"
    async with balance:
      assert balance.serial_number == "SERIAL-2"

    assert fake_io.writes == [
      b"\x1bP\r\n",
      b"\x1bx2_\r\n",
      b"\x1bP\r\n",
      b"\x1bx2_\r\n",
    ]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    balance, _ = make_balance([b"1 g\r\n", b"SERIAL\r\n"], events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with balance:
        pass

    assert error_info.value is failure
    assert events[-1] == "serial.exit"
    assert not balance.is_active

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    balance, _ = make_balance(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with balance.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []

  @pytest.mark.parametrize(("operation", "frame"), [("tare", b"\x1bT\r\n"), ("zero", b"\x1bV\r\n")])
  async def test_mutation_cancellation_reconciles_write_before_interrupting_settle(
    self, operation: str, frame: bytes
  ) -> None:
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    balance, fake_io = make_balance(events=events, blocked_write=frame, tare_settle_s=60)

    async def mutate() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          if operation == "tare":
            await balance.tare()
          else:
            await balance.zero()
          events.append("settle.completed")
        except anyio.get_cancelled_exc_class():
          events.append("operation.cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(mutate)
      await fake_io.write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "operation.cancelled" not in events
      fake_io.release_write.set()

    assert events.index("write.completed") < events.index("operation.cancelled")
    assert "settle.completed" not in events


async def _enter_and_wait(balance: SartoriusEntris2) -> None:
  async with balance:
    pytest.fail("cancelled entry reached the body")
