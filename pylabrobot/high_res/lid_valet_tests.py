from collections import deque
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.high_res import HighResLidValet, HighResLidValetError
from pylabrobot.io.socket import Socket
from pylabrobot.testing.concurrency import AnyioTestBase

ResponseEffect = Union[str, BaseException, Callable[[], Awaitable[str]]]
WriteHook = Callable[[], Awaitable[None]]


class FakeLidValetSocket(HasLifespan):
  """In-memory LidValet server with native lifecycle and cancellation seams."""

  def __init__(
    self,
    responses: Optional[Dict[str, ResponseEffect]] = None,
    raw: Optional[Dict[str, ResponseEffect]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    write_errors: Optional[Dict[str, BaseException]] = None,
    after_write: Optional[Dict[str, WriteHook]] = None,
  ) -> None:
    super().__init__()
    self.responses = responses or {}
    self.raw = raw or {}
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.write_errors = write_errors or {}
    self.after_write = after_write or {}
    self.port = 1000
    self.written: List[str] = []
    self._pending: Deque[tuple[str, ResponseEffect, bool]] = deque()
    self._rx = bytearray()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeLidValetSocket does not accept lifespan options")
    self.events.append("socket.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("socket.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    command = data.decode("ascii").rstrip("\r\n")
    failure = self.write_errors.get(command)
    if failure is not None:
      raise failure
    self.written.append(data.decode("ascii"))
    self.events.append(f"write:{command}")
    if command != "disconnect":
      is_raw = command in self.raw
      effect = self.raw.get(command, self.responses.get(command, "OK!\r\n"))
      self._pending.append((command, effect, is_raw))
    hook = self.after_write.get(command)
    if hook is not None:
      await hook()

  async def read(self, num_bytes: int = 4096, timeout: Optional[float] = None) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self._rx and self._pending:
      command, effect, is_raw = self._pending.popleft()
      if isinstance(effect, BaseException):
        raise effect
      body = await effect() if callable(effect) else effect
      reply = body if is_raw else f"ACK! {command}\r\n{body}"
      self._rx.extend(reply.encode("ascii"))
      self.events.append(f"reply.committed:{command}")
    if not self._rx:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    data = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    if not self._rx:
      self.events.append("reply.consumed")
    return data


def make_valet(
  responses: Optional[Dict[str, ResponseEffect]] = None,
  raw: Optional[Dict[str, ResponseEffect]] = None,
  num_nests: int = 2,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  write_errors: Optional[Dict[str, BaseException]] = None,
  after_write: Optional[Dict[str, WriteHook]] = None,
  ack_timeout: float = 0.01,
  command_timeout: float = 0.03,
) -> tuple[HighResLidValet, FakeLidValetSocket]:
  """Build a LidValet backed by a software-only native Socket resource."""
  valet = HighResLidValet(
    ack_timeout=ack_timeout,
    command_timeout=command_timeout,
    reset_timeout=command_timeout,
    status_timeout=command_timeout,
    busy_timeout=command_timeout,
    status_poll_interval=0,
    lid_retry_delay=0,
  )
  valet._num_nests = num_nests
  fake_io = FakeLidValetSocket(
    responses,
    raw,
    events,
    entry_error,
    exit_error,
    write_errors,
    after_write,
  )
  valet.io = cast(Socket, fake_io)
  return valet, fake_io


def stub_nest_count(valet: HighResLidValet, count: int) -> None:
  """Replace the large settings payload while retaining lifecycle behavior."""

  async def request_settings() -> SimpleNamespace:
    await anyio.lowlevel.checkpoint()
    return SimpleNamespace(active_hotels=count)

  valet.request_settings = request_settings  # type: ignore[assignment]


class TestHighResLidValetProtocol(AnyioTestBase):
  async def test_status_decode(self) -> None:
    for body, expected in [
      ("OPEN\r\nOK!\r\n", "open"),
      ("HAS_LID\r\nOK!\r\n", "has_lid"),
      ("BUSY\r\nOK!\r\n", "busy"),
      ("\r\nOK!\r\n", "unknown"),
    ]:
      valet, _ = make_valet({"status 1": body})
      assert await valet.request_state(1) == expected

  async def test_delid_wire_bytes(self) -> None:
    valet, fake_io = make_valet({"status 1": "OPEN\r\nOK!\r\n", "unlid 1": "OK!\r\n"})
    await valet.delid(1)
    assert fake_io.written == ["status 1\n", "unlid 1\n"]

  async def test_delid_refuses_when_cup_has_lid(self) -> None:
    valet, fake_io = make_valet({"status 1": "HAS_LID\r\nOK!\r\n"})
    with pytest.raises(HighResLidValetError):
      await valet.delid(1)
    assert "unlid 1\n" not in fake_io.written

  async def test_lid_wire_bytes(self) -> None:
    valet, fake_io = make_valet({"status 1": "HAS_LID\r\nOK!\r\n", "lid 1": "OK!\r\n"})
    await valet.lid(1)
    assert fake_io.written == ["status 1\n", "lid 1\n"]

  async def test_lid_retries_then_fails_without_lid(self) -> None:
    valet, fake_io = make_valet({"status 1": "OPEN\r\nOK!\r\n"})
    with pytest.raises(HighResLidValetError):
      await valet.lid(1)
    assert fake_io.written.count("status 1\n") == valet.lid_retries
    assert "lid 1\n" not in fake_io.written

  async def test_reset_wire_bytes(self) -> None:
    valet, fake_io = make_valet({"reset 2": "OK!\r\n"})
    await valet.reset(2)
    assert fake_io.written == ["reset 2\n"]

  async def test_error_reply_is_parsed(self) -> None:
    valet, _ = make_valet(
      {
        "status 1": "OPEN\r\nOK!\r\n",
        "unlid 1": "Delid Error: no plate present\r\nERROR!\r\n",
      }
    )
    with pytest.raises(HighResLidValetError, match="Delid Error: no plate present"):
      await valet.delid(1)

  async def test_aborted_reply_raises(self) -> None:
    valet, _ = make_valet({"reset 1": "ABORTED!\r\n"})
    with pytest.raises(HighResLidValetError):
      await valet.reset(1)

  async def test_invalid_response_without_ack(self) -> None:
    valet, _ = make_valet(raw={"status 1": "garbage\r\n"})
    with pytest.raises(HighResLidValetError):
      await valet.request_state(1)

  async def test_nest_validation(self) -> None:
    valet, _ = make_valet(num_nests=2)
    with pytest.raises(ValueError):
      await valet.reset(3)
    with pytest.raises(ValueError):
      await valet.delid(0)

  async def test_request_all_states(self) -> None:
    valet, _ = make_valet({"status": "hotel 1: OPEN\r\nhotel 2: ERROR (last failed)\r\nOK!\r\n"})
    assert await valet.request_all_states() == {1: "open", 2: "error"}

  async def test_reset_all_omits_nest(self) -> None:
    valet, fake_io = make_valet({"reset": "OK!\r\n"})
    await valet.reset()
    assert fake_io.written == ["reset\n"]

  async def test_nest_count_unknown_before_setup(self) -> None:
    valet = HighResLidValet()
    with pytest.raises(RuntimeError):
      _ = valet.num_nests
    with pytest.raises(RuntimeError):
      valet._validate_nest(1)

  async def test_save_cvm_wire_bytes(self) -> None:
    valet, fake_io = make_valet({"savecvm": "OK!\r\n"})
    await valet.save_cvm()
    assert fake_io.written == ["savecvm\n"]

  async def test_introspection_wire_bytes(self) -> None:
    valet, fake_io = make_valet({"list": "OK!\r\n", "info all": "OK!\r\n", "help wave": "OK!\r\n"})
    await valet.list_commands()
    await valet.request_command_info(include_maintenance=True)
    await valet.request_command_help("wave")
    assert fake_io.written == ["list\n", "info all\n", "help wave\n"]

  async def test_concurrent_commands_do_not_cross_replies(self) -> None:
    valet, fake_io = make_valet(
      {
        "status 1": "hotel 1: OPEN\r\nOK!\r\n",
        "status 2": "hotel 2: HAS_LID\r\nOK!\r\n",
      }
    )
    results: Dict[int, str] = {}

    async def request(nest: int) -> None:
      results[nest] = await valet.request_state(nest)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(request, 1)
      task_group.start_soon(request, 2)

    assert results == {1: "open", 2: "has_lid"}
    assert fake_io.written in (["status 1\n", "status 2\n"], ["status 2\n", "status 1\n"])

  async def test_failed_nest_reports_error_state(self) -> None:
    valet, _ = make_valet({"status 1": "hotel 1: ERROR (last failed)\r\nOK!\r\n"})
    assert await valet.request_state(1) == "error"

  async def test_vacuum_wire_bytes(self) -> None:
    valet, fake_io = make_valet({"vacon 1": "OK!\r\n", "vacoff 1": "OK!\r\n"})
    await valet.set_vacuum(1, True)
    await valet.set_vacuum(1, False)
    assert fake_io.written == ["vacon 1\n", "vacoff 1\n"]

  async def test_purge_wire_bytes(self) -> None:
    valet, fake_io = make_valet({"purgeon 2": "OK!\r\n", "purgeoff 2": "OK!\r\n"})
    await valet.set_purge(2, True)
    await valet.set_purge(2, False)
    assert fake_io.written == ["purgeon 2\n", "purgeoff 2\n"]

  async def test_pneumatics_validate_nest(self) -> None:
    valet, _ = make_valet(num_nests=2)
    with pytest.raises(ValueError):
      await valet.set_vacuum(3, True)
    with pytest.raises(ValueError):
      await valet.set_purge(0, True)

  async def test_home_and_wave_wire_bytes(self) -> None:
    valet, fake_io = make_valet({"home": "OK!\r\n", "wave 3": "OK!\r\n"})
    await valet.home()
    await valet.wave(3)
    assert fake_io.written == ["home\n", "wave 3\n"]

  async def test_home_offset_rejects_address_zero(self) -> None:
    valet, fake_io = make_valet()
    with pytest.raises(ValueError):
      await valet.request_home_offset(0)
    assert fake_io.written == []

  async def test_wave_rejects_zero_cycles(self) -> None:
    valet, _ = make_valet()
    with pytest.raises(ValueError):
      await valet.wave(0)

  async def test_diagnostics_strip_envelope(self) -> None:
    valet, _ = make_valet({"version": "Product Name: LidValet\r\nOK!\r\n"})
    assert await valet.request_version() == "Product Name: LidValet"

  async def test_change_setting_rejects_embedded_space(self) -> None:
    valet, _ = make_valet()
    with pytest.raises(ValueError):
      await valet.change_setting("SERVER_NAME", "two words")


class TestHighResLidValetLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert HighResLidValet.__aenter__ is HasLifespan.__aenter__
    assert HighResLidValet.__aexit__ is HasLifespan.__aexit__
    assert HighResLidValet.setup is HasLifespan.setup
    assert HighResLidValet.stop is HasLifespan.stop

  async def test_lifespan_options_fail_before_socket_entry(self) -> None:
    events: List[str] = []
    valet, _ = make_valet(events=events)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with valet.lifespan(unexpected=True):
        pytest.fail("invalid options reached lifespan body")
    assert events == []

  async def test_socket_entry_failure_does_not_run_disconnect_or_exit(self) -> None:
    events: List[str] = []
    failure = RuntimeError("socket entry failed")
    valet, _ = make_valet(events=events, entry_error=failure)
    with pytest.raises(RuntimeError) as error_info:
      async with valet:
        pytest.fail("failed socket entry reached body")
    assert error_info.value is failure
    assert events == ["socket.enter"]
    assert not valet.is_active

  async def test_stuck_lid_rolls_back_socket_without_release_sequence(self) -> None:
    events: List[str] = []
    valet, fake_io = make_valet(
      {"status": "hotel 1: HAS_LID\r\nhotel 2: OPEN\r\nOK!\r\n"}, events=events
    )
    stub_nest_count(valet, 2)
    with pytest.raises(HighResLidValetError, match="Please remove"):
      async with valet:
        pytest.fail("stuck lid reached lifespan body")
    assert fake_io.written == ["status\n", "disconnect\n"]
    assert events[-2:] == ["write:disconnect", "socket.exit"]
    assert not valet.is_active

  async def test_reset_failure_disconnects_then_closes_socket(self) -> None:
    events: List[str] = []
    failure = HighResLidValetError("reset failed")
    valet, fake_io = make_valet(
      {"status": "hotel 1: OPEN\r\nhotel 2: OPEN\r\nOK!\r\n", "reset": failure},
      events=events,
    )
    stub_nest_count(valet, 2)
    with pytest.raises(HighResLidValetError) as error_info:
      async with valet:
        pytest.fail("failed reset reached lifespan body")
    assert error_info.value is failure
    assert fake_io.written == ["status\n", "reset\n", "disconnect\n"]
    assert events[-2:] == ["write:disconnect", "socket.exit"]

  async def test_normal_exit_disconnects_before_socket_close(self) -> None:
    events: List[str] = []
    valet, fake_io = make_valet(
      {"status": "hotel 1: OPEN\r\nhotel 2: OPEN\r\nOK!\r\n", "reset": "OK!\r\n"},
      events=events,
    )
    stub_nest_count(valet, 2)
    async with valet:
      assert valet.is_active
    assert fake_io.written[-1] == "disconnect\n"
    assert events[-2:] == ["write:disconnect", "socket.exit"]

  async def test_body_cancellation_disconnects_and_closes_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    valet, _ = make_valet(
      {"status": "hotel 1: OPEN\r\nhotel 2: OPEN\r\nOK!\r\n", "reset": "OK!\r\n"},
      events=events,
    )
    stub_nest_count(valet, 2)

    async def use_valet() -> None:
      try:
        async with valet:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_valet)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-3:] == ["write:disconnect", "socket.exit", "cancelled"]
    assert not valet.is_active

  async def test_disconnect_is_shielded_from_cancellation(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    disconnect_started = anyio.Event()
    release_disconnect = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def block_disconnect() -> None:
      disconnect_started.set()
      await release_disconnect.wait()

    valet, _ = make_valet(
      {"status": "hotel 1: OPEN\r\nhotel 2: OPEN\r\nOK!\r\n", "reset": "OK!\r\n"},
      events=events,
      after_write={"disconnect": block_disconnect},
    )
    stub_nest_count(valet, 2)

    async def use_valet() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with valet:
            body_started.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_valet)
      await body_started.wait()
      scopes[0].cancel()
      await disconnect_started.wait()
      await anyio.sleep(0)
      assert "socket.exit" not in events
      release_disconnect.set()

    assert events[-3:] == ["write:disconnect", "socket.exit", "cancelled"]

  async def test_disconnect_failure_is_best_effort_and_socket_still_closes(self) -> None:
    events: List[str] = []
    valet, _ = make_valet(
      {"status": "hotel 1: OPEN\r\nhotel 2: OPEN\r\nOK!\r\n", "reset": "OK!\r\n"},
      events=events,
      write_errors={"disconnect": RuntimeError("link gone")},
    )
    stub_nest_count(valet, 2)
    async with valet:
      pass
    assert events[-1] == "socket.exit"

  async def test_socket_close_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("socket close failed")
    valet, _ = make_valet(
      {"status": "hotel 1: OPEN\r\nhotel 2: OPEN\r\nOK!\r\n", "reset": "OK!\r\n"},
      events=events,
      exit_error=failure,
    )
    stub_nest_count(valet, 2)
    with pytest.raises(RuntimeError) as error_info:
      async with valet:
        pass
    assert error_info.value is failure
    assert events[-2:] == ["write:disconnect", "socket.exit"]
    assert not valet.is_active

  async def test_sequential_reuse_reinitializes_and_closes_twice(self) -> None:
    events: List[str] = []
    valet, fake_io = make_valet(
      {"status": "hotel 1: OPEN\r\nhotel 2: OPEN\r\nOK!\r\n", "reset": "OK!\r\n"},
      events=events,
    )
    stub_nest_count(valet, 2)
    async with valet:
      pass
    async with valet:
      pass
    assert fake_io.written == [
      "status\n",
      "reset\n",
      "disconnect\n",
      "status\n",
      "reset\n",
      "disconnect\n",
    ]
    assert events.count("socket.enter") == 2
    assert events.count("socket.exit") == 2


class TestHighResLidValetCancellation(AnyioTestBase):
  @pytest.mark.parametrize(
    ("operation", "command", "responses"),
    [
      ("delid", "unlid 1", {"status 1": "OPEN\r\nOK!\r\n"}),
      ("lid", "lid 1", {"status 1": "HAS_LID\r\nOK!\r\n"}),
      ("vacuum", "vacon 1", {}),
      ("purge", "purgeon 1", {}),
      ("reset", "reset 1", {}),
      ("reset_all", "reset", {}),
    ],
  )
  async def test_committed_mutation_reconciles_reply_before_cancellation(
    self, operation: str, command: str, responses: Dict[str, ResponseEffect]
  ) -> None:
    events: List[str] = []
    read_started = anyio.Event()
    release_reply = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def blocked_reply() -> str:
      read_started.set()
      await release_reply.wait()
      return "OK!\r\n"

    scripted = dict(responses)
    scripted[command] = blocked_reply
    valet, fake_io = make_valet(scripted, events=events)

    async def run_operation() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          if operation == "delid":
            await valet.delid(1)
          elif operation == "lid":
            await valet.lid(1)
          elif operation == "vacuum":
            await valet.set_vacuum(1, True)
          elif operation == "purge":
            await valet.set_purge(1, True)
          elif operation == "reset":
            await valet.reset(1)
          else:
            await valet.reset()
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_operation)
      await read_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_reply.set()

    assert fake_io.written[-1] == f"{command}\n"
    assert events[-2:] == ["reply.consumed", "cancelled"]

  async def test_pending_cancellation_before_lock_writes_no_command(self) -> None:
    valet, fake_io = make_valet()
    returned = False
    with anyio.CancelScope() as scope:
      scope.cancel()
      await valet.reset()
      returned = True
    assert not returned
    assert fake_io.written == []

  async def test_cancelled_lock_wait_writes_no_second_command(self) -> None:
    first_read_started = anyio.Event()
    release_first = anyio.Event()
    second_started = anyio.Event()
    second_scopes: List[anyio.CancelScope] = []

    async def blocked_version() -> str:
      first_read_started.set()
      await release_first.wait()
      return "version\r\nOK!\r\n"

    valet, fake_io = make_valet({"version": blocked_version})

    async def second_command() -> None:
      with anyio.CancelScope() as scope:
        second_scopes.append(scope)
        second_started.set()
        await valet.reset()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(valet.request_version)
      await first_read_started.wait()
      task_group.start_soon(second_command)
      await second_started.wait()
      await anyio.sleep(0)
      second_scopes[0].cancel()
      release_first.set()

    assert fake_io.written == ["version\n"]

  async def test_cancellation_after_fake_commits_write_consumes_reply(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def finish_non_abandoning_write() -> None:
      write_started.set()
      with anyio.CancelScope(shield=True):
        await release_write.wait()
      await anyio.lowlevel.checkpoint()

    valet, fake_io = make_valet(
      {"reset": "OK!\r\n"}, events=events, after_write={"reset": finish_non_abandoning_write}
    )

    async def reset() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await valet.reset()
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(reset)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert fake_io.written == ["reset\n"]
    assert events[-2:] == ["reply.consumed", "cancelled"]

  async def test_cancelled_reconciliation_has_finite_operation_deadline(self) -> None:
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    failures: List[HighResLidValetError] = []

    valet, fake_io = make_valet(
      raw={"reset": "ACK! reset\r\n"},
      events=events,
      ack_timeout=0.001,
      command_timeout=0.01,
    )

    async def reset() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await valet.reset()
        except HighResLidValetError as error:
          failures.append(error)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(reset)
      while "reply.consumed" not in events:
        await anyio.sleep(0)
      scopes[0].cancel()
    assert len(failures) == 1
    assert "Timeout waiting for operation" in str(failures[0])
    assert fake_io.written == ["reset\n"]

  async def test_write_timeout_preserves_nested_transport_failure(self) -> None:
    failure = TimeoutError("socket write timed out")
    valet, _ = make_valet(write_errors={"reset": failure})
    with pytest.raises(TimeoutError) as error_info:
      await valet.reset()
    assert error_info.value is failure
