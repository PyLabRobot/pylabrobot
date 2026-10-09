from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io import Socket
from pylabrobot.legacy.thermocycling.standard import Protocol, Stage, Step
from pylabrobot.legacy.thermocycling.thermo_fisher import (
  thermo_fisher_thermocycler as backend_module,
)
from pylabrobot.legacy.thermocycling.thermo_fisher.atc import ATCBackend
from pylabrobot.legacy.thermocycling.thermo_fisher.proflex import ProflexBackend
from pylabrobot.testing.concurrency import AnyioTestBase


def _reply(command: str, *args: object, status: str = "OK") -> bytes:
  suffix = "" if not args else " " + " ".join(str(arg) for arg in args)
  return f"{status} {command}{suffix}\n".encode("ascii")


class RecordingSocket(HasLifespan):
  """Typed software Socket double with event-controlled reads and literal replies."""

  def __init__(self, *, close_error: Optional[BaseException] = None) -> None:
    super().__init__()
    self.close_error = close_error
    self.events: List[str] = []
    self.writes: List[bytes] = []
    self.entries = 0
    self.exits = 0
    self.options: Dict[str, Any] = {}
    self.replies: Dict[str, Deque[bytes]] = defaultdict(deque)
    self.greeting: Deque[bytes] = deque([b"READY\n"])
    self.block_command: Optional[str] = None
    self.block_read_number: Optional[int] = None
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()
    self.read_counts: Dict[str, int] = defaultdict(int)
    self.write_failures: Dict[str, BaseException] = {}
    self.awaiting_greeting = True

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingSocket does not accept lifespan options")
    self.entries += 1
    self.awaiting_greeting = True
    self.events.append("socket.enter")
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    self.exits += 1
    self.events.append("socket.exit")
    if self.close_error is not None:
      raise self.close_error

  async def write(self, data: bytes, timeout: float = 30) -> None:
    del timeout
    command = data.decode("ascii").rstrip("\r\n")
    self.writes.append(data)
    self.events.append(f"write:{command}")
    failure = self.write_failures.get(command)
    await anyio.lowlevel.checkpoint()
    if failure is not None:
      raise failure

  async def _read(self) -> bytes:
    if self.entries > 0 and self.awaiting_greeting:
      self.awaiting_greeting = False
      self.events.append("read:greeting")
      await anyio.lowlevel.checkpoint()
      return self.greeting.popleft()
    command = self.writes[-1].decode("ascii").rstrip("\r\n")
    self.events.append(f"read:{command}")
    self.read_counts[command] += 1
    if command == self.block_command and (
      self.block_read_number is None or self.read_counts[command] == self.block_read_number
    ):
      self.blocked_started.set()
      await self.release_blocked.wait()
    await anyio.lowlevel.checkpoint()
    replies = self.replies[command]
    if not replies:
      raise RuntimeError(f"No scripted reply for {command}")
    return replies.popleft()

  async def read(self, size: int = 1024, timeout: float = 30) -> bytes:
    del size, timeout
    return await self._read()

  async def read_until_eof(self, timeout: float = 30) -> bytes:
    del timeout
    return await self._read()


class RecordingLock:
  """AnyIO lock wrapper exposing when a competing acquisition begins."""

  def __init__(self) -> None:
    self.lock = anyio.Lock()
    self.acquire_count = 0
    self.waiter_started = anyio.Event()

  async def acquire(self) -> None:
    self.acquire_count += 1
    if self.acquire_count == 2:
      self.waiter_started.set()
    await self.lock.acquire()

  def release(self) -> None:
    self.lock.release()


def _make_backend(
  monkeypatch: pytest.MonkeyPatch,
  backend_type: type[ProflexBackend] = ProflexBackend,
  **constructor_kwargs: Any,
) -> tuple[ProflexBackend, RecordingSocket]:
  io = RecordingSocket()

  def build_socket(**kwargs: Any) -> Socket:
    io.options = dict(kwargs)
    return cast(Socket, io)

  monkeypatch.setattr(backend_module, "Socket", build_socket)
  backend = backend_type(ip="software-host", **constructor_kwargs)
  return backend, io


def _script_auth(io: RecordingSocket, *, auth_status: str = "OK") -> None:
  io.replies["CHAL?"].append(_reply("CHAL?", "challenge"))
  token = "9df282c8528be12d8f7b8fedc465c9bc"
  io.replies[f"AUTH {token}"].append(_reply("AUTH", status=auth_status))
  io.replies["ACCess -stealth Controller"].append(_reply("ACCess"))


def _script_setup(io: RecordingSocket, *, bid: str = "31") -> None:
  _script_auth(io)
  io.replies["POWER On"].append(_reply("POWER"))
  io.replies["TBC:BlockPresence?"].append(_reply("TBC:BlockPresence?", "1"))
  io.replies["TBC:BID?"].append(_reply("TBC:BID?", bid))
  block_count = 3 if bid == "13" else 1
  for block_id in range(block_count):
    prefix = f"TBC{block_id + 1}"
    io.replies[f"{prefix}:ERROR?"].append(_reply(f"{prefix}:ERROR?", "0"))
    io.replies[f"{prefix}:RUNTitle?"].append(_reply(f"{prefix}:RUNTitle?", "-"))
    io.replies[f"{prefix}:BLOCK 1 25"].extend(
      [_reply(f"{prefix}:BLOCK", status="NEXT"), _reply(f"{prefix}:BLOCK")]
    )
    io.replies[f"{prefix}:COVER 1 105"].extend(
      [_reply(f"{prefix}:COVER", status="NEXT"), _reply(f"{prefix}:COVER")]
    )
    io.replies[f"{prefix}:COVER 0 105"].extend(
      [_reply(f"{prefix}:COVER", status="NEXT"), _reply(f"{prefix}:COVER")]
    )
    io.replies[f"{prefix}:BLOCK 0 25"].extend(
      [_reply(f"{prefix}:BLOCK", status="NEXT"), _reply(f"{prefix}:BLOCK")]
    )


async def _settle() -> None:
  for _ in range(10):
    await anyio.lowlevel.checkpoint()


def _skip_abort_delay(monkeypatch: pytest.MonkeyPatch) -> List[float]:
  delays: List[float] = []

  async def sleep(delay: float) -> None:
    delays.append(delay)
    await anyio.lowlevel.checkpoint()

  monkeypatch.setattr(backend_module.anyio, "sleep", sleep)
  return delays


class TestThermoFisherLifecycle(AnyioTestBase):
  def test_atc_and_proflex_inherit_managed_methods(self) -> None:
    assert ATCBackend.setup is HasLifespan.setup
    assert ATCBackend.stop is HasLifespan.stop
    assert ATCBackend.__aenter__ is HasLifespan.__aenter__
    assert ATCBackend.__aexit__ is HasLifespan.__aexit__
    assert ProflexBackend.setup is HasLifespan.setup
    assert ProflexBackend.stop is HasLifespan.stop
    assert ProflexBackend.__aenter__ is HasLifespan.__aenter__
    assert ProflexBackend.__aexit__ is HasLifespan.__aexit__

  async def test_authentication_transcript(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    _script_setup(io)
    async with backend.lifespan(blocks_to_setup=[]):
      pass
    assert io.events[:8] == [
      "socket.enter",
      "read:greeting",
      "write:CHAL?",
      "read:CHAL?",
      "write:AUTH 9df282c8528be12d8f7b8fedc465c9bc",
      "read:AUTH 9df282c8528be12d8f7b8fedc465c9bc",
      "write:ACCess -stealth Controller",
      "read:ACCess -stealth Controller",
    ]

  async def test_auth_failure_closes_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    _script_auth(io, auth_status="ERR")
    with pytest.raises(ValueError, match="Authentication failed"):
      async with backend.lifespan():
        pass
    assert io.events[-1] == "socket.exit"

  async def test_power_on_failure_closes_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    _script_auth(io)
    io.replies["POWER On"].append(b"")
    with pytest.raises(ValueError, match="Failed to power on"):
      async with backend.lifespan():
        pass
    assert io.exits == 1

  async def test_block_discovery_failure_rolls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    _script_auth(io)
    io.replies["POWER On"].append(_reply("POWER"))
    io.replies["TBC:BlockPresence?"].append(_reply("TBC:BlockPresence?", "0"))
    with pytest.raises(ValueError, match="Block not present"):
      async with backend.lifespan():
        pass
    assert io.events[-1] == "socket.exit"

  async def test_idle_configuration_failure_runs_cleanup(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    _script_setup(io)
    io.replies["TBC1:BLOCK 1 25"].clear()
    io.replies["TBC1:BLOCK 1 25"].append(_reply("TBC1:BLOCK", status="ERR"))
    with pytest.raises(ValueError, match="Failed to set block idle temperature"):
      async with backend.lifespan():
        pass
    assert io.events[-4:] == [
      "write:TBC1:BLOCK 0 25",
      "read:TBC1:BLOCK 0 25",
      "read:TBC1:BLOCK 0 25",
      "socket.exit",
    ]

  async def test_body_cancellation_aborts_runs_before_deactivation_and_close(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    _skip_abort_delay(monkeypatch)
    _script_setup(io)
    io.replies["TBC1:RUNTitle?"].extend(
      [_reply("TBC1:RUNTitle?", "run-a"), _reply("TBC1:RUNTitle?", "run-a")]
    )
    io.replies["TBC1:AbortRun run-a"].append(_reply("TBC1:AbortRun"))
    with anyio.move_on_after(None) as scope:
      async with backend.lifespan():
        backend.current_runs[0] = "run-a"
        scope.deadline = anyio.current_time() + 0.01
        await anyio.sleep_forever()
    cleanup = [
      event
      for event in io.events
      if "AbortRun" in event or " 0 " in event or event == "socket.exit"
    ]
    assert cleanup[-9:] == [
      "write:TBC1:AbortRun run-a",
      "read:TBC1:AbortRun run-a",
      "write:TBC1:COVER 0 105",
      "read:TBC1:COVER 0 105",
      "read:TBC1:COVER 0 105",
      "write:TBC1:BLOCK 0 25",
      "read:TBC1:BLOCK 0 25",
      "read:TBC1:BLOCK 0 25",
      "socket.exit",
    ]
    assert io.events[-1] == "socket.exit"

  async def test_cleanup_attempts_every_block_and_socket(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    _skip_abort_delay(monkeypatch)
    _script_setup(io, bid="13")
    io.write_failures["TBC1:COVER 0 105"] = RuntimeError("cover failure")
    io.write_failures["TBC2:BLOCK 0 25"] = RuntimeError("block failure")
    io.replies["TBC1:RUNTitle?"].clear()
    io.replies["TBC1:RUNTitle?"].extend(
      [_reply("TBC1:RUNTitle?", "run-a"), _reply("TBC1:RUNTitle?", "run-a")]
    )
    io.replies["TBC1:AbortRun run-a"].append(_reply("TBC1:AbortRun"))
    io.replies["TBC3:RUNTitle?"].clear()
    io.replies["TBC3:RUNTitle?"].extend(
      [_reply("TBC3:RUNTitle?", "run-c"), _reply("TBC3:RUNTitle?", "run-c")]
    )
    io.replies["TBC3:AbortRun run-c"].append(_reply("TBC3:AbortRun"))
    baseline = 0
    with pytest.raises(RuntimeError):
      async with backend.lifespan(blocks_to_setup=[0, 1, 2]):
        backend.current_runs = {0: "run-a", 2: "run-c"}
        baseline = len(io.events)
    assert io.events[baseline:] == [
      "write:TBC1:RUNTitle?",
      "read:TBC1:RUNTitle?",
      "write:TBC1:RUNTitle?",
      "read:TBC1:RUNTitle?",
      "write:TBC1:AbortRun run-a",
      "read:TBC1:AbortRun run-a",
      "write:TBC1:COVER 0 105",
      "write:TBC1:BLOCK 0 25",
      "read:TBC1:BLOCK 0 25",
      "read:TBC1:BLOCK 0 25",
      "write:TBC2:COVER 0 105",
      "read:TBC2:COVER 0 105",
      "read:TBC2:COVER 0 105",
      "write:TBC2:BLOCK 0 25",
      "write:TBC3:RUNTitle?",
      "read:TBC3:RUNTitle?",
      "write:TBC3:RUNTitle?",
      "read:TBC3:RUNTitle?",
      "write:TBC3:AbortRun run-c",
      "read:TBC3:AbortRun run-c",
      "write:TBC3:COVER 0 105",
      "read:TBC3:COVER 0 105",
      "read:TBC3:COVER 0 105",
      "write:TBC3:BLOCK 0 25",
      "read:TBC3:BLOCK 0 25",
      "read:TBC3:BLOCK 0 25",
      "socket.exit",
    ]

  async def test_failed_reentry_auth_closes_socket_without_stale_block_cleanup(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    _script_setup(io)
    async with backend.lifespan(blocks_to_setup=[]):
      pass
    backend.available_blocks = [99]
    backend._num_blocks = 3
    backend.num_temp_zones = 2
    _script_auth(io, auth_status="ERR")
    baseline = len(io.events)

    with pytest.raises(ValueError, match="Authentication failed"):
      async with backend.lifespan():
        pass

    assert io.events[baseline:] == [
      "socket.enter",
      "read:greeting",
      "write:CHAL?",
      "read:CHAL?",
      "write:AUTH 9df282c8528be12d8f7b8fedc465c9bc",
      "read:AUTH 9df282c8528be12d8f7b8fedc465c9bc",
      "socket.exit",
    ]

  async def test_reentry_clears_discovery_before_authentication(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    _script_setup(io)
    async with backend.lifespan(blocks_to_setup=[]):
      pass
    backend.available_blocks = [99]
    backend._num_blocks = 3
    backend.num_temp_zones = 2
    _script_setup(io)
    io.block_command = "CHAL?"
    io.block_read_number = 2

    async with anyio.create_task_group() as tasks:

      async def enter() -> None:
        async with backend.lifespan(blocks_to_setup=[]):
          pass

      tasks.start_soon(enter)
      await io.blocked_started.wait()
      assert backend.available_blocks == []
      assert backend._num_blocks is None
      assert backend.num_temp_zones == 0
      io.release_blocked.set()

  async def test_successful_reentry_reconciles_retained_runs_before_discovery(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    _skip_abort_delay(monkeypatch)
    _script_setup(io)
    async with backend.lifespan(blocks_to_setup=[]):
      pass
    backend.current_runs[0] = "old-run"
    _script_setup(io)
    io.replies["TBC1:RUNTitle?"].clear()
    io.replies["TBC1:RUNTitle?"].extend(
      [_reply("TBC1:RUNTitle?", "old-run"), _reply("TBC1:RUNTitle?", "old-run")]
    )
    io.replies["TBC1:AbortRun old-run"].append(_reply("TBC1:AbortRun"))
    baseline = len(io.events)

    async with backend.lifespan(blocks_to_setup=[]):
      pass

    transcript = io.events[baseline:]
    authenticated = transcript.index("read:ACCess -stealth Controller")
    aborted = transcript.index("write:TBC1:AbortRun old-run")
    discovered = transcript.index("write:TBC:BlockPresence?")
    assert authenticated < aborted < discovered
    assert backend.current_runs == {}

  async def test_lifespan_options_reject_other_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    with pytest.raises(TypeError, match="block_idle_temp must be a number"):
      async with backend.lifespan(block_idle_temp=True):
        pass
    with pytest.raises(TypeError, match="cover_idle_temp must be a number"):
      async with backend.lifespan(cover_idle_temp="105"):
        pass
    with pytest.raises(TypeError, match="blocks_to_setup must be a list"):
      async with backend.lifespan(blocks_to_setup=(0,)):
        pass
    assert io.entries == 0

  async def test_cleanup_bound_closes_socket_after_an_overrun(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    _script_setup(io)
    monkeypatch.setattr(backend_module, "_THERMOCYCLER_CLEANUP_TIMEOUT", 0.02)
    io.block_command = "TBC1:COVER 0 105"
    start = anyio.current_time()
    with pytest.raises(TimeoutError):
      async with backend.lifespan(blocks_to_setup=[0]):
        pass
    assert anyio.current_time() - start < 0.5
    assert io.exits == 1

  async def test_ssl_socket_options_are_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch, use_ssl=True, serial_number="SN123")
    assert backend.port == 7443
    assert io.options["host"] == "software-host"
    assert io.options["port"] == 7443
    assert io.options["server_hostname"] == "SN123"
    assert io.options["ssl_context"].check_hostname is False


class TestThermoFisherTransactions(AnyioTestBase):
  async def test_scpi_transactions_do_not_interleave(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    io.replies["FIRST"].append(_reply("FIRST"))
    io.replies["SECOND"].append(_reply("SECOND"))
    io.block_command = "FIRST"
    async with anyio.create_task_group() as tasks:
      tasks.start_soon(backend.send_command, {"cmd": "FIRST"})
      await io.blocked_started.wait()
      tasks.start_soon(backend.send_command, {"cmd": "SECOND"})
      await _settle()
      assert io.writes == [b"FIRST\r\n"]
      io.release_blocked.set()
    assert io.writes == [b"FIRST\r\n", b"SECOND\r\n"]

  async def test_next_response_is_consumed_under_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    backend.available_blocks = [0]
    command = "TBC1:BLOCK 1 25"
    io.replies[command].extend([_reply("TBC1:BLOCK", status="NEXT"), _reply("TBC1:BLOCK")])
    io.block_command = command
    cancelled = anyio.Event()

    async def caller() -> None:
      try:
        await backend.set_block_idle_temp(25, 0)
      finally:
        cancelled.set()

    async with anyio.create_task_group() as tasks:
      tasks.start_soon(caller)
      await io.blocked_started.wait()
      tasks.cancel_scope.cancel()
      io.release_blocked.set()
    assert cancelled.is_set()
    assert io.events.count(f"read:{command}") == 2

  async def test_next_follow_up_holds_lock_ahead_of_competing_command(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    lock = RecordingLock()
    backend._scpi_lock = cast(Any, lock)
    backend.available_blocks = [0]
    first = "TBC1:BLOCK 1 25"
    io.replies[first].extend([_reply("TBC1:BLOCK", status="NEXT"), _reply("TBC1:BLOCK")])
    io.replies["SECOND"].append(_reply("SECOND"))
    io.block_command = first
    io.block_read_number = 2

    writes_while_follow_up_blocked: List[bytes] = []
    with anyio.move_on_after(1) as guard:
      async with anyio.create_task_group() as tasks:
        tasks.start_soon(backend.set_block_idle_temp, 25, 0)
        await io.blocked_started.wait()
        tasks.start_soon(backend.send_command, {"cmd": "SECOND"})
        await lock.waiter_started.wait()
        writes_while_follow_up_blocked = list(io.writes)
        io.release_blocked.set()
    assert not guard.cancelled_caught
    assert writes_while_follow_up_blocked == [b"TBC1:BLOCK 1 25\r\n"]
    assert io.writes == [b"TBC1:BLOCK 1 25\r\n", b"SECOND\r\n"]

  async def test_queued_run_ownership_waits_for_prewrite_dispatch(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    lock = RecordingLock()
    backend._scpi_lock = cast(Any, lock)
    run_command = {"cmd": "TBC1:RunProtocol", "args": ["p", "run-a"]}
    cancelled = anyio.Event()

    async def queued_run() -> None:
      try:
        await backend._exchange(run_command, run_owner=(0, "run-a"))
      finally:
        cancelled.set()

    with anyio.fail_after(1):
      await lock.acquire()
      async with anyio.create_task_group() as tasks:
        queued_scope = anyio.CancelScope()

        async def scoped_run() -> None:
          with queued_scope:
            await queued_run()

        tasks.start_soon(scoped_run)
        await lock.waiter_started.wait()
        assert backend.current_runs == {}
        queued_scope.cancel()
        await cancelled.wait()
        assert backend.current_runs == {}
        assert io.writes == []
      lock.release()

  async def test_pending_cancellation_wins_over_stored_exchange_error(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    io.block_command = "FIRST"
    outcome: List[str] = []
    caller_scope = anyio.CancelScope()

    async def caller() -> None:
      with caller_scope:
        try:
          await backend._exchange({"cmd": "FIRST"}, response_timeout=0.02, read_next=True)
        except anyio.get_cancelled_exc_class():
          outcome.append("cancelled")
          raise
        except BaseException as error:
          outcome.append(type(error).__name__)

    with anyio.move_on_after(1) as guard:
      async with anyio.create_task_group() as tasks:
        tasks.start_soon(caller)
        await io.blocked_started.wait()
        caller_scope.cancel()
    assert not guard.cancelled_caught
    assert outcome == ["cancelled"]

  async def test_cancelled_run_start_reconciles_then_aborts(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    _skip_abort_delay(monkeypatch)
    backend.num_temp_zones = 1
    command = "TBC1:RunProtocol -User=Admin -CoverTemperature=105 -CoverEnabled=On p run-a"
    io.replies[command].append(_reply("TBC1:RunProtocol", status="NEXT"))
    io.block_command = command
    io.replies["TBC1:RUNTitle?"].extend(
      [_reply("TBC1:RUNTitle?", "run-a"), _reply("TBC1:RUNTitle?", "run-a")]
    )
    io.replies["TBC1:AbortRun run-a"].append(_reply("TBC1:AbortRun"))
    protocol = Protocol(stages=[Stage(steps=[Step(temperature=[25], hold_seconds=1)], repeats=1)])
    load_command = (
      "TBC1:Protocol -Volume=25 -RunMode=Fast p <multiline.outer>\n"
      "        STAGe -repeat=1 1 S_1 <multiline.stage>\n"
      "                STEP 1 <multiline.step>\n"
      "                        RAMP -rate=100 25\n"
      "                        HOLD 1\n"
      "                </multiline.step>\n"
      "        </multiline.stage>\n"
      "</multiline.outer>"
    )
    io.replies[load_command].append(_reply("TBC1:Protocol"))

    async def start() -> None:
      await backend._scpi_run_protocol(
        protocol, "run-a", 0, 25, "Fast", "p", 105, True, "Admin", ["S"]
      )

    async with anyio.create_task_group() as tasks:
      tasks.start_soon(start)
      await io.blocked_started.wait()
      assert backend.current_runs == {0: "run-a"}
      tasks.cancel_scope.cancel()
      io.release_blocked.set()
    assert b"TBC1:AbortRun run-a\r\n" in io.writes
    assert backend.current_runs == {}

  async def test_run_write_exception_retains_ambiguous_ownership(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    command = "TBC1:RunProtocol p run-a"
    failure = RuntimeError("write outcome unknown")
    io.write_failures[command] = failure
    with pytest.raises(RuntimeError, match="write outcome unknown"):
      await backend._exchange(
        {"cmd": "TBC1:RunProtocol", "args": ["p", "run-a"]},
        run_owner=(0, "run-a"),
      )
    assert backend.current_runs == {0: "run-a"}

  async def test_abort_delay_is_backend_neutral(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    delays = _skip_abort_delay(monkeypatch)
    backend.current_runs[0] = "run-a"
    io.replies["TBC1:RUNTitle?"].extend(
      [_reply("TBC1:RUNTitle?", "run-a"), _reply("TBC1:RUNTitle?", "run-a")]
    )
    io.replies["TBC1:AbortRun run-a"].append(_reply("TBC1:AbortRun"))
    await backend.abort_run(0)
    assert backend.current_runs == {}
    assert delays == [10]

  async def test_infinite_hold_poll_is_cancellable(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    protocol = Protocol(
      stages=[Stage(steps=[Step(temperature=[25], hold_seconds=float("inf"))], repeats=1)]
    )
    io.replies["TBC1:RUNProgress?"].append(
      b"OK TBC1:RUNProgress? -RunTitle=run-a -Stage=1 -Step=1\n"
    )
    io.replies["TBC1:RUNTitle?"].append(_reply("TBC1:RUNTitle?", "run-a"))
    io.replies["TBC1:ELAPsedTime?"].append(_reply("TBC1:ELAPsedTime?", "1"))
    io.replies["TBC1:REMainingTime?"].append(_reply("TBC1:REMainingTime?", "2"))
    io.replies["TBC1:TBC:BlockTemperatures?"].append(_reply("TBC1:TBC:BlockTemperatures?", "0"))
    with anyio.move_on_after(0.05) as scope:
      await backend.get_run_info(protocol, 0)
    assert scope.cancelled_caught

  async def test_morse_buzzer_off_is_shielded(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    io.replies["BUZZer+"].append(_reply("BUZZer+"))
    io.replies["BUZZer-"].append(_reply("BUZZer-"))
    with anyio.move_on_after(0.05) as scope:
      await backend.send_morse_code("-")
    assert scope.cancelled_caught
    assert b"BUZZer-\r\n" in io.writes
