import sys
from typing import Any, Optional, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.hamilton.protocol.text.router import ReplyRouter, USBReplyReader
from pylabrobot.io.boundary import BoundaryRead
from pylabrobot.io.usb import USB
from pylabrobot.legacy.heating_shaking import HeaterShaker
from pylabrobot.legacy.heating_shaking.hamilton_backend import (
  HamiltonHeaterShakerBackend,
  HamiltonHeaterShakerInterface,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton import base as hamilton_base_module
from pylabrobot.legacy.liquid_handling.backends.hamilton.base import HamiltonLiquidHandler
from pylabrobot.legacy.tip_tracker import TipTracker
from pylabrobot.resources import Coordinate, Deck
from pylabrobot.resources.hamilton import TipPickupMethod, TipSize
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup


class FirmwareProtocolError(Exception):
  """Represent a firmware-reported failure in the test protocol."""


class RecordingUSB(MockIO):
  """Provide controllable USB replies and observable transport ownership."""

  def __init__(
    self,
    events: list[str],
    *,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    """Initialize a blocked reply stream and optional lifecycle failures."""
    super().__init__()
    self.events = events
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.active = False
    self.read_started = anyio.Event()
    self._reply_send, self._reply_receive = anyio.create_memory_object_stream[
      bytes | BaseException
    ](float("inf"))
    self.read.side_effect = self._read_reply

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    """Record transport entry and register its cleanup."""
    assert kwargs == {}
    self.events.append("transport.enter")
    if self.entry_error is not None:
      raise self.entry_error
    self.active = True
    stack.push_shielded_async_callback(self._exit_transport)

  async def _exit_transport(self) -> None:
    """Record transport exit and expose a configured cleanup failure."""
    self.active = False
    self.events.append("transport.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def _read_reply(self, size: int) -> bytes:
    """Read one test-provided reply while the transport is active."""
    assert size == 64
    assert self.active, "reader used the transport after exit"
    self.events.append("reader.read")
    self.read_started.set()
    reply = await self._reply_receive.receive()
    if isinstance(reply, BaseException):
      raise reply
    return reply

  async def read_to_boundary(
    self, *, boundary: str, consume_immediate_boundary: bool = True
  ) -> BoundaryRead:
    """Collect test packets to a short-packet reply end."""
    if boundary == "packet" and not consume_immediate_boundary:
      return BoundaryRead(b"", True, True, False)
    assert boundary == "short_packet_end"
    data = bytearray()
    while True:
      packet = await self._read_reply(64)
      data.extend(packet)
      if len(packet) < 64:
        return BoundaryRead(bytes(data), True, True, False)

  async def publish(self, reply: str) -> None:
    """Make one firmware reply available to the reader."""
    await self._reply_send.send(reply.encode())

  async def fail_reader(self, error: BaseException) -> None:
    """Raise a fatal error from the next transport read."""
    await self._reply_send.send(error)


class RecordingReplyRouter(ReplyRouter):
  """Record reader ownership while retaining real reply routing."""

  def __init__(
    self,
    io: RecordingUSB,
    backend: "ConcreteHamiltonBackend",
    events: list[str],
    *,
    start_error: Optional[BaseException] = None,
    stop_error: Optional[BaseException] = None,
  ) -> None:
    """Initialize the router with optional start and stop failures."""
    super().__init__(
      io=io,
      module_id_length=backend.module_id_length,
      parse_id=backend.get_id_from_fw_response,
      raise_for_error=backend.check_fw_string_error,
      packet_read_timeout=0.01,
      read_timeout=0.1,
      reader=USBReplyReader(cast(USB, io)),
    )
    self.events = events
    self.start_error = start_error
    self.stop_error = stop_error
    self.run_count = 0
    self.stop_count = 0

  async def _run(self, reader_scope: anyio.CancelScope) -> None:
    """Record each reader task and delegate routing."""
    self.run_count += 1
    self.events.append("reader.start")
    if self.start_error is not None:
      raise self.start_error
    try:
      await super()._run(reader_scope)
    finally:
      self.events.append("reader.exit")

  def _stop(self, reader_scope: anyio.CancelScope) -> None:
    """Record reader shutdown before delegating to the router."""
    self.stop_count += 1
    self.events.append("reader.stop")
    super()._stop(reader_scope)
    if self.stop_error is not None:
      raise self.stop_error


class ConcreteHamiltonBackend(HamiltonLiquidHandler):
  """Supply deterministic protocol hooks for the abstract legacy base."""

  def __init__(
    self,
    io: RecordingUSB,
    events: list[str],
    *,
    start_error: Optional[BaseException] = None,
    stop_error: Optional[BaseException] = None,
    firmware_error: Optional[Exception] = None,
    replace_router: bool = True,
  ) -> None:
    """Construct a configured backend around a typed software transport."""
    super().__init__(id_product=0x1234, packet_read_timeout=0.01, read_timeout=0.1)  # type: ignore[arg-type]
    self.io = cast(USB, io)
    if replace_router:
      self._reply_router = RecordingReplyRouter(
        io,
        self,
        events,
        start_error=start_error,
        stop_error=stop_error,
      )
    self.firmware_error = firmware_error
    self.set_deck(Deck(size_x=1, size_y=1, size_z=1))
    self.set_heads({0: TipTracker(thing="Channel 0"), 1: TipTracker(thing="Channel 1")})

  @property
  def module_id_length(self) -> int:
    """Use two-character module identifiers in the test protocol."""
    return 2

  @property
  def num_channels(self) -> int:
    """Expose the exact configured head width."""
    return 2

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Parse a four-digit identifier from a test reply."""
    marker = resp.find("id")
    if marker == -1:
      return None
    value = resp[marker + 2 : marker + 6]
    if not value.isdigit():
      raise ValueError("malformed test identifier")
    return int(value)

  def check_fw_string_error(self, resp: str) -> None:
    """Raise the configured firmware exception for error replies."""
    if resp.endswith("ER"):
      if self.firmware_error is not None:
        raise self.firmware_error
      raise FirmwareProtocolError("firmware rejected command")

  def _parse_response(self, resp: str, fmt: Any) -> dict:
    """Return a deterministic parsed response."""
    return {"response": resp, "format": fmt}

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod,
  ) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def pick_up_tips(self, ops: Any, use_channels: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def drop_tips(self, ops: Any, use_channels: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def aspirate(self, ops: Any, use_channels: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def dispense(self, ops: Any, use_channels: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def pick_up_tips96(self, pickup: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def drop_tips96(self, drop: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def aspirate96(self, aspiration: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def dispense96(self, dispense: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def pick_up_resource(self, pickup: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def move_picked_up_resource(self, move: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  async def drop_resource(self, drop: Any) -> None:
    """Satisfy the liquid-handler protocol without hardware."""

  def can_pick_up_tip(self, channel_idx: int, tip: Any) -> bool:
    """Accept tips in this lifecycle-only backend."""
    return True


class PostEntryFailureBackend(ConcreteHamiltonBackend):
  """Fail after base entry to verify partial subclass rollback."""

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    """Enter all base resources and then fail."""
    await super()._enter_lifespan(stack, **kwargs)
    raise RuntimeError("subclass post-entry failed")


class CancellingEntryBackend(ConcreteHamiltonBackend):
  """Block after base entry so cancellation exercises registered cleanup."""

  def __init__(self, io: RecordingUSB, events: list[str]) -> None:
    """Initialize an observable blocked entry."""
    super().__init__(io, events)
    self.entry_started = anyio.Event()

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    """Block after all base resources have been acquired."""
    await super()._enter_lifespan(stack, **kwargs)
    self.entry_started.set()
    await anyio.sleep_forever()


class CompatibilityHamiltonBackend(ConcreteHamiltonBackend):
  """Expose one native option for compatibility-bridge characterization."""

  def __init__(self, io: RecordingUSB, events: list[str], **kwargs: Any) -> None:
    """Initialize the backend with an observable option value."""
    super().__init__(io, events, **kwargs)
    self.compatibility_option = False

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    compatibility_option: bool = False,
    **kwargs: Any,
  ) -> None:
    """Consume the test option before entering the real Hamilton lifespan."""
    self.compatibility_option = compatibility_option
    await super()._enter_lifespan(stack, **kwargs)


class CleanupRecordingUSB(RecordingUSB):
  """Gate Hamilton heater-shaker replies on their corresponding writes."""

  _commands = ("LI", "SI", "TO", "SC", "SW")

  def __init__(
    self,
    events: list[str],
    *,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    """Initialize deterministic command and reply gates."""
    super().__init__(events, exit_error=exit_error)
    self.command_written = {command: anyio.Event() for command in self._commands}
    self.reply_released = {command: anyio.Event() for command in self._commands}
    self.reply_released["LI"].set()
    self.reply_released["SI"].set()
    self.commands: dict[str, str] = {}
    self.reader_running = False
    self.cleanup_replies_while_reader_running: list[str] = []
    self.reader_error: Optional[BaseException] = None
    self._read_index = 0
    self._idle = anyio.Event()
    self.write.side_effect = self._write_command

  async def _write_command(self, data: bytes, *, timeout: Optional[float] = None) -> None:
    """Record a command and make it visible to the blocked reader."""
    del timeout
    command_text = data.decode()
    command = command_text[2:4]
    assert command in self.command_written
    self.commands[command] = command_text
    if command == "TO":
      self.events.append("deactivate.write")
    elif command == "SC":
      self.events.append("stop_shaking.write")
    self.command_written[command].set()

  async def _read_reply(self, size: int) -> bytes:
    """Return each reply only after its command write and explicit release."""
    assert size == 64
    assert self.active, "reader used the transport after exit"
    if self._read_index == len(self._commands):
      await self._idle.wait()
      raise AssertionError("unreachable")
    command = self._commands[self._read_index]
    await self.command_written[command].wait()
    await self.reply_released[command].wait()
    if self.reader_error is not None:
      raise self.reader_error
    assert self.reader_running
    if command == "TO":
      self.events.append("deactivate.reply")
      self.cleanup_replies_while_reader_running.append(command)
    elif command == "SW":
      self.events.append("stop_shaking.reply")
      self.cleanup_replies_while_reader_running.append(command)
    self._read_index += 1
    return f"{self.commands[command]}OK".encode()

  def release_reply(self, command: str) -> None:
    """Release one blocked fake reply."""
    self.reply_released[command].set()

  def fail_cleanup_reader(self, error: BaseException) -> None:
    """Inject a reader failure after a cleanup command has been written."""
    self.reader_error = error
    self.reply_released["TO"].set()


class CleanupRecordingReplyRouter(RecordingReplyRouter):
  """Expose completion of the real shielded Hamilton reply reader."""

  def __init__(
    self,
    io: CleanupRecordingUSB,
    backend: "HeaterShakerHamiltonBackend",
    events: list[str],
    *,
    stop_error: Optional[BaseException] = None,
  ) -> None:
    """Initialize an observable cleanup router."""
    super().__init__(io, backend, events, stop_error=stop_error)
    self.cleanup_io = io
    self.finished = anyio.Event()

  async def _run(self, reader_scope: anyio.CancelScope) -> None:
    """Track whether the production router reader is alive."""
    self.run_count += 1
    self.cleanup_io.reader_running = True
    try:
      await ReplyRouter._run(self, reader_scope)
    finally:
      self.cleanup_io.reader_running = False
      self.events.append("reader.finished")
      self.finished.set()

  def _stop(self, reader_scope: anyio.CancelScope) -> None:
    """Record router shutdown before requesting it."""
    self.stop_count += 1
    self.events.append("router.stop")
    ReplyRouter._stop(self, reader_scope)
    if self.stop_error is not None:
      raise self.stop_error


class HeaterShakerHamiltonBackend(ConcreteHamiltonBackend, HamiltonHeaterShakerInterface):
  """Route real heater-shaker commands through the Hamilton text router."""

  def __init__(
    self,
    io: CleanupRecordingUSB,
    events: list[str],
    *,
    stop_error: Optional[BaseException] = None,
  ) -> None:
    """Construct a Hamilton backend with observable cleanup routing."""
    self.cleanup_io = io
    self.cleanup_events = events
    self.cleanup_stop_error = stop_error
    super().__init__(io, events, replace_router=False)
    self.waiter_error: Optional[BaseException] = None

  def _make_reply_router(self) -> ReplyRouter:
    """Build and retain the typed router used by the cleanup composition."""
    router = CleanupRecordingReplyRouter(
      self.cleanup_io,
      self,
      self.cleanup_events,
      stop_error=self.cleanup_stop_error,
    )
    self.cleanup_router = router
    return router

  async def send_hhs_command(self, index: int, command: str, **kwargs: Any) -> str:
    """Send one heater-shaker command through the production text router."""
    try:
      response = await self.send_command(module=f"T{index}", command=command, **kwargs)
    except BaseException as error:
      self.waiter_error = error
      raise
    assert isinstance(response, str)
    return response


def make_heater_shaker_composition(
  *,
  exit_error: Optional[BaseException] = None,
  stop_error: Optional[BaseException] = None,
) -> tuple[
  HeaterShakerHamiltonBackend,
  CleanupRecordingUSB,
  CleanupRecordingReplyRouter,
  HeaterShaker,
  list[str],
]:
  """Build a software-only Hamilton and heater-shaker composition."""
  events: list[str] = []
  io = CleanupRecordingUSB(events, exit_error=exit_error)
  backend = HeaterShakerHamiltonBackend(io, events, stop_error=stop_error)
  heater_shaker = HeaterShaker(
    name="heater_shaker",
    size_x=1,
    size_y=1,
    size_z=1,
    backend=HamiltonHeaterShakerBackend(index=1, interface=backend),
    child_location=Coordinate.zero(),
  )
  return (
    backend,
    io,
    backend.cleanup_router,
    heater_shaker,
    events,
  )


def make_backend(
  *,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  start_error: Optional[BaseException] = None,
  stop_error: Optional[BaseException] = None,
  firmware_error: Optional[Exception] = None,
  backend_type: type[ConcreteHamiltonBackend] = ConcreteHamiltonBackend,
) -> tuple[ConcreteHamiltonBackend, RecordingUSB, RecordingReplyRouter, list[str]]:
  """Build one configured software-only backend with controllable failures."""
  events: list[str] = []
  io = RecordingUSB(events, entry_error=entry_error, exit_error=exit_error)
  backend = backend_type(
    io,
    events,
    start_error=start_error,
    stop_error=stop_error,
    firmware_error=firmware_error,
  )
  return backend, io, cast(RecordingReplyRouter, backend._reply_router), events


def exception_messages(error: BaseException) -> list[str]:
  """Collect messages from contexts and nested exception groups."""
  messages: list[str] = []
  seen: set[int] = set()

  def collect(current: Optional[BaseException]) -> None:
    while current is not None and id(current) not in seen:
      seen.add(id(current))
      messages.append(str(current))
      if isinstance(current, BaseExceptionGroup):
        for nested in current.exceptions:
          collect(nested)
      current = current.__context__

  collect(error)
  return messages


def exception_instances(error: BaseException) -> list[BaseException]:
  """Collect exception instances from contexts and nested exception groups."""
  instances: list[BaseException] = []
  seen: set[int] = set()

  def collect(current: Optional[BaseException]) -> None:
    while current is not None and id(current) not in seen:
      seen.add(id(current))
      instances.append(current)
      if isinstance(current, BaseExceptionGroup):
        for nested in current.exceptions:
          collect(nested)
      current = current.__context__

  collect(error)
  return instances


class TestHamiltonBaseLifecycle(AnyioTestBase):
  async def test_constructor_uses_typed_reply_router_factory(self) -> None:
    backend, io, router, _, _ = make_heater_shaker_composition()

    assert backend.cleanup_router is router
    assert router.io is io

  async def test_reader_remains_available_during_cancelled_heater_shaker_cleanup(self) -> None:
    backend, io, router, heater_shaker, events = make_heater_shaker_composition()
    body_started = anyio.Event()
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def run_composition() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with backend:
            async with heater_shaker:
              events.clear()
              body_started.set()
              try:
                await anyio.sleep_forever()
              except anyio.get_cancelled_exc_class():
                events.append("body.cancelled")
                raise
        except anyio.get_cancelled_exc_class():
          events.append("cancellation.propagated")
          raise
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_composition)
      await body_started.wait()
      scopes[0].cancel()
      await io.command_written["TO"].wait()
      assert io.reader_running
      io.release_reply("TO")
      await io.command_written["SC"].wait()
      assert io.reader_running
      io.release_reply("SC")
      await io.command_written["SW"].wait()
      assert io.reader_running
      io.release_reply("SW")
      await finished.wait()

    assert events == [
      "body.cancelled",
      "deactivate.write",
      "deactivate.reply",
      "stop_shaking.write",
      "stop_shaking.reply",
      "router.stop",
      "reader.finished",
      "transport.exit",
      "cancellation.propagated",
    ]
    assert io.cleanup_replies_while_reader_running == ["TO", "SW"]
    assert router.finished.is_set()
    assert not io.reader_running
    assert not io.active
    assert not backend.is_active
    assert not heater_shaker.is_active

  async def test_reader_failure_during_heater_shaker_cleanup_preserves_all_failures(self) -> None:
    reader_error = RuntimeError("reader failed during cleanup")
    router_stop_error = RuntimeError("router cleanup failed")
    transport_error = RuntimeError("transport close failed")
    body_error = RuntimeError("body failed")
    backend, io, router, heater_shaker, events = make_heater_shaker_composition(
      exit_error=transport_error,
      stop_error=router_stop_error,
    )
    failure: list[BaseException] = []
    finished = anyio.Event()

    async def run_composition() -> None:
      try:
        async with backend:
          async with heater_shaker:
            events.clear()
            raise body_error
      except BaseException as error:
        failure.append(error)
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_composition)
      await io.command_written["TO"].wait()
      assert io.reader_running
      io.fail_cleanup_reader(reader_error)
      await finished.wait()

    assert failure
    instances = exception_instances(failure[0])
    assert any(
      isinstance(error, RuntimeError) and "No active reply router" in str(error)
      for error in instances
    )
    assert any(error is reader_error for error in instances)
    assert any(error is router_stop_error for error in instances)
    assert any(error is transport_error for error in instances)
    assert any(error is body_error for error in instances)
    assert "stop_shaking.write" not in events
    assert "transport.exit" in events
    assert router.finished.is_set()
    assert not io.reader_running
    assert not io.active
    assert not backend.is_active
    assert not heater_shaker.is_active

  async def test_heater_shaker_stop_rejected_after_reader_failure(self) -> None:
    reader_error = RuntimeError("reader failed during cleanup")
    router_stop_error = RuntimeError("router cleanup failed")
    transport_error = RuntimeError("transport close failed")
    body_error = RuntimeError("body failed")
    backend, io, _, heater_shaker, _ = make_heater_shaker_composition(
      exit_error=transport_error,
      stop_error=router_stop_error,
    )
    failures: list[BaseException] = []
    finished = anyio.Event()

    async def run_composition() -> None:
      try:
        async with backend:
          async with heater_shaker:
            raise body_error
      except BaseException as error:
        failures.append(error)
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_composition)
      await io.command_written["TO"].wait()
      io.fail_cleanup_reader(reader_error)
      await finished.wait()

    assert len(failures) == 1
    instances = exception_instances(failures[0])
    rejected = [
      error
      for error in instances
      if isinstance(error, RuntimeError) and "No active reply router" in str(error)
    ]
    assert len(rejected) == 1
    assert "SC" not in io.commands
    assert reader_error in instances
    assert router_stop_error in instances
    assert transport_error in instances
    assert body_error in instances

  async def test_entry_requires_deck_before_transport_use(self) -> None:
    backend, _, _, events = make_backend()
    backend._deck = None

    with pytest.raises(RuntimeError, match="^Deck not set$"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert events == []

  async def test_entry_requires_head_before_transport_use(self) -> None:
    backend, _, _, events = make_backend()
    backend._head = None

    with pytest.raises(RuntimeError, match="^Head not set$"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert events == []

  async def test_rejects_unsupported_options_before_transport_use(self) -> None:
    backend, _, _, events = make_backend()

    with pytest.raises(
      TypeError,
      match="unexpected keyword argument 'unsupported'",
    ):
      async with backend.lifespan(unsupported=True):
        pytest.fail("failed entry must not reach the body")

    assert events == []

  async def test_normal_entry_and_exit_have_exact_lifo_order(self) -> None:
    backend, io, router, events = make_backend()

    async with backend:
      await io.read_started.wait()
      events.append("body")

    assert events == [
      "transport.enter",
      "reader.start",
      "reader.read",
      "body",
      "reader.stop",
      "reader.exit",
      "transport.exit",
    ]
    assert router.run_count == 1
    assert router.stop_count == 1
    assert not backend.is_active

  async def test_transport_entry_failure_starts_no_reader(self) -> None:
    backend, _, router, events = make_backend(entry_error=RuntimeError("USB open failed"))

    with pytest.raises(RuntimeError, match="USB open failed"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert events == ["transport.enter"]
    assert router.run_count == 0
    assert router.stop_count == 0

  async def test_router_failure_stops_router_before_transport_exit(self) -> None:
    backend, _, router, events = make_backend(start_error=RuntimeError("reader start failed"))

    with pytest.raises(BaseException) as error_info:
      async with backend:
        await anyio.sleep_forever()

    assert "reader start failed" in exception_messages(error_info.value)
    assert events == ["transport.enter", "reader.start", "reader.stop", "transport.exit"]
    assert router.run_count == 1
    assert router.stop_count == 1

  async def test_lifespan_binds_router_to_replaced_public_io(
    self,
    monkeypatch: pytest.MonkeyPatch,
  ) -> None:
    original_io = MockIO()
    original_io.write.side_effect = RuntimeError("stale transport used")
    replacement_events: list[str] = []
    replacement_io = RecordingUSB(replacement_events)
    monkeypatch.setattr(
      hamilton_base_module,
      "USB",
      lambda **kwargs: cast(USB, original_io),
    )
    backend = ConcreteHamiltonBackend(
      replacement_io,
      replacement_events,
      replace_router=False,
    )
    original_router = backend._reply_router

    async with backend:
      assert await backend.send_command("C0", "QM", wait=False) is None

    assert backend._reply_router is original_router
    assert original_router.io is replacement_io
    assert replacement_io.write.calls == [((b"C0QMid0001",), {"timeout": None})]
    assert original_io.write.calls == []

  async def test_failed_subclass_entry_unwinds_reader_before_transport(self) -> None:
    backend, _, router, events = make_backend(backend_type=PostEntryFailureBackend)

    with pytest.raises(BaseException) as error_info:
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert "subclass post-entry failed" in exception_messages(error_info.value)
    assert events[0] == "transport.enter"
    assert events.index("reader.stop") < events.index("transport.exit")
    assert router.stop_count == 1

  async def test_entry_cancellation_unwinds_reader_before_transport(self) -> None:
    events: list[str] = []
    io = RecordingUSB(events)
    backend = CancellingEntryBackend(io, events)

    async def run_backend() -> None:
      try:
        async with backend:
          pytest.fail("cancelled entry must not reach the body")
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await backend.entry_started.wait()
      await io.read_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-4:] == ["reader.stop", "reader.exit", "transport.exit", "cancelled"]
    assert not backend.is_active

  async def test_body_cancellation_unwinds_reader_before_transport(self) -> None:
    backend, io, router, events = make_backend()
    body_started = anyio.Event()

    async def run_backend() -> None:
      try:
        async with backend:
          await io.read_started.wait()
          events.append("body")
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-4:] == ["reader.stop", "reader.exit", "transport.exit", "cancelled"]
    assert router.stop_count == 1

  async def test_reader_failure_propagates_and_transport_exits(self) -> None:
    backend, io, router, events = make_backend()

    with pytest.raises(BaseException) as error_info:
      async with backend:
        await io.read_started.wait()
        await io.fail_reader(RuntimeError("USB disconnected"))
        await anyio.sleep_forever()

    assert "USB disconnected" in exception_messages(error_info.value)
    assert (
      events.index("reader.exit") < events.index("reader.stop") < events.index("transport.exit")
    )
    assert router.stop_count == 1

  async def test_sequential_reuse_runs_one_reader_per_lifespan(self) -> None:
    backend, io, router, events = make_backend()

    async with backend:
      await io.read_started.wait()
    io.read_started = anyio.Event()
    async with backend:
      await io.read_started.wait()

    cycle = [
      "transport.enter",
      "reader.start",
      "reader.read",
      "reader.stop",
      "reader.exit",
      "transport.exit",
    ]
    assert events == cycle + cycle
    assert router.run_count == 2
    assert router.stop_count == 2

  async def test_tip_type_cache_is_cleared_on_exit(self) -> None:
    backend, _, _, _ = make_backend()
    backend._tth2tti[123] = 4

    async with backend:
      assert backend._tth2tti == {123: 4}

    assert backend._tth2tti == {}

  async def test_all_cleanup_runs_and_failures_remain_chained(self) -> None:
    backend, _, _, events = make_backend(
      stop_error=RuntimeError("reader stop failed"),
      exit_error=RuntimeError("USB close failed"),
    )

    with pytest.raises(RuntimeError, match="USB close failed") as error_info:
      async with backend:
        pass

    messages = exception_messages(error_info.value)
    assert "reader stop failed" in messages
    assert "USB close failed" in messages
    assert (
      events.index("reader.stop") < events.index("reader.exit") < events.index("transport.exit")
    )

  async def test_command_routing_preserves_parsing_and_firmware_exception_identity(self) -> None:
    firmware_error = FirmwareProtocolError("exact firmware failure")
    backend, io, _, _ = make_backend(firmware_error=firmware_error)

    async with backend:
      await io.publish("C0QMid0001OK")
      assert await backend.send_command("C0", "QM", aa=7, fmt="parsed") == {
        "response": "C0QMid0001OK",
        "format": "parsed",
      }
      await io.publish("C0QMid0042ER")
      with pytest.raises(FirmwareProtocolError) as error_info:
        await backend.send_raw_command("C0QMid0042")

    assert error_info.value is firmware_error
    assert io.write.calls == [
      ((b"C0QMid0001aa7",), {"timeout": None}),
      ((b"C0QMid0042",), {"timeout": None}),
    ]

  async def test_router_deadline_preserves_command_specific_timeout(self) -> None:
    backend, _, _, _ = make_backend()

    async with backend:
      with pytest.raises(
        TimeoutError,
        match=r"Timeout while waiting for response to command C0QMid0042\.",
      ):
        await backend.send_raw_command("C0QMid0042", read_timeout=0)


class TestHamiltonBaseCompatibility(AnyioTestBase):
  _anyio_backends = ["asyncio"]

  async def test_setup_and_stop_use_the_native_lifespan(self) -> None:
    raw_backend, io, router, events = make_backend(backend_type=CompatibilityHamiltonBackend)
    backend = cast(CompatibilityHamiltonBackend, raw_backend)

    with pytest.warns(DeprecationWarning, match="setup.*deprecated"):
      await backend.setup(compatibility_option=True)
    await io.read_started.wait()
    assert backend.compatibility_option
    assert backend.is_active
    await backend.stop()

    assert events == [
      "transport.enter",
      "reader.start",
      "reader.read",
      "reader.stop",
      "reader.exit",
      "transport.exit",
    ]
    assert router.run_count == 1
    assert router.stop_count == 1
    assert not backend.is_active
