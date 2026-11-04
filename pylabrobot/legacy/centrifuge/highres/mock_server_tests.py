"""End-to-end integration tests for ``MicroSpinBackend`` against the
in-process :class:`MicroSpinMockServer`.

Unlike :mod:`microspin_tests` (which stubs the asyncio stream pair with
canned bytes), these tests open a real TCP socket from
:meth:`MicroSpinBackend.setup` to a real :class:`asyncio.Server` on
``127.0.0.1``. They are the highest-fidelity tests we can run without a
physical MicroSpin.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from typing import Any
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.centrifuge.highres import mock_server as mock_server_module
from pylabrobot.legacy.centrifuge.highres.microspin_backend import (
  MicroSpinAbortedError,
  MicroSpinBackend,
  MicroSpinError,
)
from pylabrobot.legacy.centrifuge.highres.mock_server import MicroSpinMockServer
from pylabrobot.testing.concurrency import AnyioTestBase

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup


class TestMicroSpinMockServerLifecycle(AnyioTestBase):
  """Verify the asyncio server and all of its work stay inside one lifespan."""

  _anyio_backends = ["asyncio"]

  async def test_uses_async_resource_managed_methods(self) -> None:
    server = MicroSpinMockServer()

    assert isinstance(server, HasLifespan)
    assert MicroSpinMockServer.__aenter__ is HasLifespan.__aenter__
    assert MicroSpinMockServer.__aexit__ is HasLifespan.__aexit__
    assert MicroSpinMockServer.setup is HasLifespan.setup
    assert MicroSpinMockServer.stop is HasLifespan.stop
    assert "start" not in MicroSpinMockServer.__dict__

  async def test_lifespan_rejects_concurrent_reentry(self) -> None:
    server = MicroSpinMockServer()

    async with server:
      with pytest.raises(RuntimeError, match="already has an active lifespan"):
        async with server:
          pytest.fail("repeated entry reached the body")

  async def test_lifespan_supports_sequential_reentry(self) -> None:
    server = MicroSpinMockServer()

    async with server:
      first_port = server.port
    async with server:
      assert server.port == first_port

    assert not server.is_active

  async def test_lifespan_releases_listening_port(self) -> None:
    server = MicroSpinMockServer()

    async with server:
      port = server.port

    with socket.socket() as probe:
      probe.bind((server.host, port))

  async def test_post_bind_startup_failure_releases_listening_port(self) -> None:
    server = MicroSpinMockServer()
    start_server = asyncio.start_server
    underlying: asyncio.AbstractServer | None = None
    port = 0
    startup_error = RuntimeError("socket inspection failed")

    class BrokenSocket:
      """Fail the first post-bind operation performed during server entry."""

      def getsockname(self) -> Any:
        raise startup_error

    class StartedServer:
      """Expose a broken socket while delegating deterministic closure."""

      def __init__(self, started: asyncio.AbstractServer) -> None:
        self.started = started

      @property
      def sockets(self) -> list[BrokenSocket]:
        return [BrokenSocket()]

      def close(self) -> None:
        self.started.close()

      async def wait_closed(self) -> None:
        await self.started.wait_closed()

    async def start_then_fail(*args: Any, **kwargs: Any) -> StartedServer:
      nonlocal underlying, port
      underlying = await start_server(*args, **kwargs)
      sockets = underlying.sockets
      assert sockets is not None
      port = sockets[0].getsockname()[1]
      return StartedServer(underlying)

    try:
      with mock.patch.object(mock_server_module.asyncio, "start_server", start_then_fail):
        with pytest.raises(BaseException) as error_info:
          async with server:
            pytest.fail("failed entry reached the body")

      failure = error_info.value
      if isinstance(failure, BaseExceptionGroup):
        assert failure.exceptions == (startup_error,)
      else:
        assert failure is startup_error

      with socket.socket() as probe:
        probe.bind((server.host, port))
    finally:
      if underlying is not None:
        underlying.close()
        await underlying.wait_closed()

  async def test_exit_closes_active_client_before_returning(self) -> None:
    server = MicroSpinMockServer()
    writer: asyncio.StreamWriter | None = None

    try:
      async with server:
        reader, writer = await asyncio.open_connection(server.host, server.port)

      with anyio.fail_after(1):
        assert await reader.read() == b""
    finally:
      if writer is not None:
        writer.close()
        await writer.wait_closed()

  async def test_full_connection_handoff_closes_new_client(self) -> None:
    send, receive = anyio.create_memory_object_stream[Any](1)
    await send.send((object(), object()))

    class Writer:
      def __init__(self) -> None:
        self.closed = False

      def close(self) -> None:
        self.closed = True

    writer = Writer()
    server = MicroSpinMockServer()
    server._accepted_connections = send
    try:
      server._on_client_connected(object(), writer)  # type: ignore[arg-type]
      assert writer.closed
    finally:
      send.close()
      receive.close()

  async def test_exit_cancels_active_motion_before_returning(self) -> None:
    server = MicroSpinMockServer()
    writer: asyncio.StreamWriter | None = None

    try:
      async with server:
        server.motion_dwell["home"] = 60
        reader, writer = await asyncio.open_connection(server.host, server.port)
        writer.write(b"home\r\n")
        await writer.drain()
        assert (await reader.readline()).startswith(b"ACK! home ")
        with anyio.fail_after(1):
          while server.state.current_motion is None:
            await anyio.sleep(0)
        motion = server.state.current_motion

      assert isinstance(motion, mock_server_module._Motion)
      assert motion.done
      assert isinstance(motion.exception, asyncio.CancelledError)
      assert server.state.spindle_settled.is_set()
    finally:
      if writer is not None:
        writer.close()
        await writer.wait_closed()

  async def test_scope_exit_during_motion_stops_handler_without_further_writes(self) -> None:
    server = MicroSpinMockServer()
    handler_started = anyio.Event()
    handler_stopped = anyio.Event()
    original_handle_client = server._handle_client
    writer: asyncio.StreamWriter | None = None

    async def tracked_handle_client(
      reader: asyncio.StreamReader,
      client_writer: asyncio.StreamWriter,
    ) -> None:
      handler_started.set()
      try:
        await original_handle_client(reader, client_writer)
      finally:
        handler_stopped.set()

    server._handle_client = tracked_handle_client  # type: ignore[assignment]
    try:
      async with server:
        server.motion_dwell["home"] = 60
        reader, writer = await asyncio.open_connection(server.host, server.port)
        writer.write(b"home\r\n")
        await writer.drain()
        assert (await reader.readline()).startswith(b"ACK! home ")
        await handler_started.wait()
        with anyio.fail_after(1):
          while server.state.current_motion is None:
            await anyio.sleep(0)

      with anyio.fail_after(1):
        await handler_stopped.wait()
        assert (await reader.readline()).startswith(b"ABORTED! home ")
        assert await reader.read() == b""
    finally:
      if writer is not None:
        writer.close()
        await writer.wait_closed()

  async def test_motion_wait_reports_owned_cancellation(self) -> None:
    server = MicroSpinMockServer()
    started = anyio.Event()

    async def move_forever() -> None:
      started.set()
      await anyio.sleep_forever()

    async with server:
      motion = server._begin_motion(move_forever)
      await started.wait()
      motion.cancel()
      with pytest.raises(asyncio.CancelledError):
        await motion.wait()
      assert motion.done

  async def test_motion_wait_reraises_captured_exception(self) -> None:
    server = MicroSpinMockServer()
    motion_error = RuntimeError("motion failed")

    async def fail_motion() -> None:
      raise motion_error

    async with server:
      motion = server._begin_motion(fail_motion)
      with pytest.raises(RuntimeError) as error_info:
        await motion.wait()

    assert error_info.value is motion_error
    assert motion.exception is motion_error
    assert motion.done


class TestMicroSpinMockServerBackend(AnyioTestBase):
  """Verify the server rejects its unsupported event-loop backend."""

  _anyio_backends = ["trio"]

  async def test_lifespan_rejects_trio_before_binding(self) -> None:
    server = MicroSpinMockServer()

    with pytest.raises(RuntimeError, match="requires the asyncio backend"):
      async with server:
        pytest.fail("unsupported lifespan reached the body")

    assert server._server is None


class _MockServerTestBase(AnyioTestBase):
  """Shared setup: a fresh mock server + a connected backend per test."""

  _anyio_backends = ["asyncio"]

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("MicroSpin integration tests do not accept lifespan options")
    self._test_stack = stack
    self.server = MicroSpinMockServer()
    await stack.enter_async_context(self.server.lifespan())
    self.backend = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=5.0)
    await stack.enter_async_context(self.backend.lifespan())

  async def _enter_side_backend(self) -> MicroSpinBackend:
    side = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=5.0)
    await self._test_stack.enter_async_context(side.lifespan())
    return side


class TestMockServerCommandMapping(_MockServerTestBase):
  """Verify each pylabrobot backend method produces the expected wire-level
  effect on the mock server. These tests *replace* the stub-based mapping
  tests that used to live in :mod:`microspin_tests` -- using a real TCP
  server gives us higher confidence the wire format and ordering are right.
  """

  async def test_go_to_bucket1_after_home_lands_at_bucket_1(self):
    await self.backend.home()
    await self.backend.go_to_bucket1()
    assert self.server.state.at_bucket == 1

  async def test_go_to_bucket2_after_home_lands_at_bucket_2(self):
    await self.backend.home()
    await self.backend.go_to_bucket2()
    assert self.server.state.at_bucket == 2

  async def test_home_sets_homed_flag(self):
    assert not self.server.state.homed
    await self.backend.home()
    assert self.server.state.homed
    assert await self.backend.is_homed()

  async def test_abort_latches_abort_state(self):
    assert not self.server.state.abort_latched
    await self.backend.abort()
    assert self.server.state.abort_latched

  async def test_clear_button_abort_clears_latch(self):
    await self.backend.abort()
    assert self.server.state.abort_latched
    await self.backend.clear_button_abort()
    assert not self.server.state.abort_latched

  async def test_spin_formats_parameters_correctly_on_the_wire(self):
    """Pin down the float->integer conversion against a real server."""
    # Patch _h_spin to record the args it received.
    recorded: list = []
    original = MicroSpinMockServer._h_spin

    async def recording_spin(self, args, cmd_id):  # pylint: disable=unused-argument
      recorded.append(list(args))
      return await original(self, args, cmd_id)

    self.server._h_spin = recording_spin.__get__(  # type: ignore[method-assign]
      self.server, MicroSpinMockServer
    )
    MicroSpinMockServer._handlers["spin"] = recording_spin

    try:
      # Make the spin fast so the test isn't slow.
      self.server.motion_dwell["spin_seconds_per_real_second"] = 0.001
      await self.backend.home()
      await self.backend.spin(g=100, duration=30, acceleration=0.5, deceleration=0.25)
      assert recorded == [["100", "50", "25", "30"]]
    finally:
      MicroSpinMockServer._handlers["spin"] = original

  async def test_send_command_for_unknown_command_raises_microspin_error(self):
    with pytest.raises(MicroSpinError):
      await self.backend.send_command("this_command_does_not_exist")

  async def test_request_version_round_trip(self):
    info = await self.backend.request_version()
    assert info["Product Name"] == "RandomServe"
    assert info["Version"] == "MS-1.3.3-mock"

  async def test_request_status_returns_dict_with_positions(self):
    status = await self.backend.request_status()
    assert "Spindle Position" in status
    assert "Door Position" in status

  async def test_reset_without_wait_for_settle_returns_none(self):
    await self.backend.home()
    # With wait_for_settle=False, reset shouldn't call status.
    result = await self.backend.reset(wait_for_settle=False)
    assert result is None


class TestMockServerIntegration(_MockServerTestBase):
  # --- basic protocol round-trips ----------------------------------------

  async def test_version_round_trip(self):
    info = await self.backend.request_version()
    assert info["Product Name"] == "RandomServe"
    assert info["Version"] == "MS-1.3.3-mock"

  async def test_homed_status_starts_false(self):
    assert not await self.backend.is_homed()

  async def test_home_then_status(self):
    await self.backend.home()
    assert await self.backend.is_homed()
    status = await self.backend.request_status()
    assert "Spindle Position" in status
    assert "Door Position" in status

  async def test_open_requires_homed(self):
    with pytest.raises(MicroSpinError):
      await self.backend.go_to_bucket1()

  async def test_open_after_home_succeeds(self):
    await self.backend.home()
    await self.backend.go_to_bucket1()
    assert self.server.state.at_bucket == 1

  async def test_spin_requires_homed(self):
    # not homed -> error
    with pytest.raises(MicroSpinError):
      await self.backend.spin(g=100, duration=1)
    # home -> spin works
    await self.backend.home()
    await self.backend.spin(g=100, duration=1)

  async def test_spin_auto_closes_open_door(self):
    """`spin` should close the door automatically -- callers never have to."""
    await self.backend.home()
    await self.backend.go_to_bucket1()
    # Sanity: bucket is now presented and door is open
    assert self.server.state.at_bucket == 1
    assert self.server.state.door_position > 0
    # Spin succeeds despite the open door; afterwards the door is closed.
    await self.backend.spin(g=100, duration=1)
    assert self.server.state.door_position < 0
    assert self.server.state.at_bucket is None

  async def test_unknown_command_errors(self):
    with pytest.raises(MicroSpinError):
      await self.backend.send_command("does_not_exist")

  # --- the status-blocking gate (the reason we built this) ----------------

  async def test_status_blocks_until_motion_completes(self):
    """The reason `reset()`'s third step works: status doesn't answer
    until the active motion task is done."""
    # Make the home motion noticeably slow so we can race against it.
    self.server.motion_dwell["home"] = 0.2

    async def issue_home() -> None:
      await self.backend.home()

    # Use a separate backend connection to send `status` while home is running.
    side = await self._enter_side_backend()
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(issue_home)
      with anyio.fail_after(1):
        while self.server.state.current_motion is None:
          await anyio.sleep(0)
      # Now ask for status -- this should block until home completes.
      t0 = anyio.current_time()
      await side.request_status()
      elapsed = anyio.current_time() - t0
      # status should have waited at least ~0.1s (most of the remaining home dwell)
      assert elapsed > 0.05

  # --- reset() and abort flow --------------------------------------------

  async def test_reset_sequence_against_real_server(self):
    await self.backend.home()
    result = await self.backend.reset()
    # `reset` returns the final status dict, populated from the mock state.
    assert result is not None
    assert "Spindle Position" in result
    assert "Door Position" in result

  async def test_abort_interrupts_motion_then_reset_recovers(self):
    self.server.motion_dwell["home"] = 0.5  # long home
    await self.backend.home()  # one home so the state is "homed=True"

    # Now start another motion (open) and abort it mid-way.
    self.server.motion_dwell["open"] = 0.3
    open_finished = anyio.Event()
    open_error: BaseException | None = None
    previous_motion = self.server.state.current_motion

    async def issue_open() -> None:
      nonlocal open_error
      try:
        await self.backend.go_to_bucket1()
      except BaseException as error:  # noqa: BLE001 - asserted after the owned task finishes
        open_error = error
      finally:
        open_finished.set()

    side = await self._enter_side_backend()
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(issue_open)
      with anyio.fail_after(1):
        while (
          self.server.state.current_motion is previous_motion
          or self.server.state.current_motion is None
        ):
          await anyio.sleep(0)
      await side.abort()
      # The original open command should have raised because abort cancelled it
      await open_finished.wait()
      assert isinstance(open_error, MicroSpinError)
      # The server is now in abort-latched state; further motion should fail
      with pytest.raises(MicroSpinError):
        await side.go_to_bucket1()
      # reset() clears the latch and waits for the (already-stopped) spindle
      await side.reset()
      # Motion works again
      await side.go_to_bucket1()
      assert self.server.state.at_bucket == 1


class TestMockServerLowGHang(AnyioTestBase):
  """The mock can simulate the firmware's low-G "stopped sensor never latches"
  bug. We use this to verify that callers can recover via a timeout."""

  _anyio_backends = ["asyncio"]

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("MicroSpin low-G tests do not accept lifespan options")
    self.server = MicroSpinMockServer()
    self.server.state.simulate_low_g_hang = True
    await stack.enter_async_context(self.server.lifespan())
    # Very short timeout so the test doesn't take forever
    self.backend = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=0.5)
    await stack.enter_async_context(self.backend.lifespan())

  async def test_status_hangs_after_motion_with_low_g_simulation(self):
    await self.backend.send_command("home")  # populates state.current_motion
    # status would normally answer immediately; in low-G-hang mode it sits.
    with pytest.raises(TimeoutError):
      await self.backend.request_status()


class TestMockServerCommandSurface(_MockServerTestBase):
  """The mock must implement exactly the public command set the real device
  exposes via ``list`` (manual §6.7's public tier) -- no maintenance
  commands, no invented helpers, and with response text that matches the
  device verbatim.
  """

  #: Commands the real device's ``list`` enumerates (in display order).
  EXPECTED_COMMANDS = (
    "clearbuttonabort, cba",
    "commandstat, cstat",
    "disconnect, d",
    "errors, e",
    "help",
    "info, ??",
    "list, ?",
    "logcommands",
    "version, v",
    "whoami",
    "abort, a",
    "home",
    "homedstatus, hss",
    "open",
    "spin, sp",
    "status, s",
  )

  async def test_list_enumerates_exactly_the_public_commands(self):
    lines = await self.backend.send_command("list")
    # Each line starts with the display name padded to 32 chars then ``- desc``.
    head_names = [line.split("- ", 1)[0].rstrip() for line in lines]
    assert tuple(head_names) == self.EXPECTED_COMMANDS

  async def test_info_includes_parameter_signatures(self):
    lines = await self.backend.send_command("info")
    text = "\n".join(lines)
    # Spot-check a representative slice -- the exact wording the device prints.
    assert (
      "open                            - Open the door and present the specified bucket." in text
    )
    assert "     Parameters(1): <bucket>" in text
    assert "     <bucket> the bucket number to present" in text
    assert "spin, sp                        - Spin the centrifuge." in text
    assert "     Parameters(4): <G-force> <acceleration> <deceleration> <time>" in text

  async def test_help_for_known_command_returns_signature(self):
    lines = await self.backend.send_command("help spin")
    text = "\n".join(lines)
    assert "spin, sp - Spin the centrifuge." in text
    assert "     Parameters(4): <G-force> <acceleration> <deceleration> <time>" in text

  async def test_help_for_alias_works(self):
    # `help hss` should resolve via the alias to `homedstatus`'s entry.
    lines = await self.backend.send_command("help hss")
    text = "\n".join(lines)
    assert "homedstatus, hss" in text

  async def test_help_rejects_unknown_command(self):
    with pytest.raises(MicroSpinError):
      await self.backend.send_command("help bogus")

  async def test_aliases_resolve_to_canonical_handlers(self):
    # `s` -> `status`, `v` -> `version`, `hss` -> `homedstatus`, `?` -> `list`,
    # `cba` -> `clearbuttonabort`. Just spot-check that they don't error.
    await self.backend.send_command("s")
    info = await self.backend.send_command("v")
    assert any("Version:" in line for line in info)
    await self.backend.send_command("hss")
    listing = await self.backend.send_command("?")
    assert len(listing) >= len(self.EXPECTED_COMMANDS)
    await self.backend.send_command("cba")

  async def test_maintenance_commands_are_not_recognised(self):
    # `od`, `cd`, `lockdoor`, `unlockdoor`, `locknest`, `unlocknest` are
    # maintenance-only on the real device and not in `list`; the mock must
    # treat them like any unknown command.
    for cmd in ("od", "cd", "lockdoor", "unlockdoor", "locknest", "unlocknest"):
      with pytest.raises(MicroSpinError):
        await self.backend.send_command(cmd)

  async def test_logcommands_accepts_yes_or_no(self):
    await self.backend.send_command("logcommands yes")
    await self.backend.send_command("logcommands no")
    with pytest.raises(MicroSpinError):
      await self.backend.send_command("logcommands maybe")
    with pytest.raises(MicroSpinError):
      await self.backend.send_command("logcommands")

  async def test_commandstat_reports_no_history(self):
    # We don't track history in the mock, so cstat for any id misses.
    with pytest.raises(MicroSpinError) as error_info:
      await self.backend.send_command("cstat 5")
    assert any("not in recorded history" in line for line in error_info.value.error_lines)

  async def test_whoami_returns_a_number(self):
    out = await self.backend.send_command("whoami")
    assert len(out) == 1
    int(out[0])  # must parse as an integer

  async def test_abort_emits_cba_instruction_data_line(self):
    # The real device returns this data line before its OK terminator.
    data = await self.backend.send_command("abort")
    assert data == ['Issue the "clearbuttonabort" (cba) command to re-enable the machine']


class TestMockServerAbortedTerminator(_MockServerTestBase):
  """`ABORTED!` is a real third terminator emitted by the device for
  commands cancelled by an abort, and for motion commands issued while the
  abort latch is set. The backend must raise :class:`MicroSpinAbortedError`
  for these so callers can distinguish abort-cascade from real errors.
  """

  async def test_motion_after_abort_latch_returns_aborted(self):
    await self.backend.home()
    await self.backend.abort()  # sets the latch
    # Now any motion command is immediately aborted.
    with pytest.raises(MicroSpinAbortedError):
      await self.backend.spin(g=500, duration=1)
    with pytest.raises(MicroSpinAbortedError):
      await self.backend.go_to_bucket1()
    with pytest.raises(MicroSpinAbortedError):
      await self.backend.home()

  async def test_cba_clears_latch_and_motion_works_again(self):
    await self.backend.home()
    await self.backend.abort()
    with pytest.raises(MicroSpinAbortedError):
      await self.backend.spin(g=100, duration=1)
    await self.backend.clear_button_abort()
    # No longer aborted -> spin works.
    await self.backend.spin(g=100, duration=1)

  async def test_in_flight_motion_cancelled_by_abort_returns_aborted(self):
    """A motion command running in parallel must terminate with ABORTED!
    (not ERROR!) when an abort is issued from another connection."""
    await self.backend.home()
    # Make the next motion long so we can race against it.
    self.server.motion_dwell["open"] = 0.5
    open_finished = anyio.Event()
    open_error: BaseException | None = None
    previous_motion = self.server.state.current_motion

    # Issue abort from a second connection.
    async def issue_open() -> None:
      nonlocal open_error
      try:
        await self.backend.go_to_bucket1()
      except BaseException as error:  # noqa: BLE001 - asserted after the owned task finishes
        open_error = error
      finally:
        open_finished.set()

    side = await self._enter_side_backend()
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(issue_open)
      with anyio.fail_after(1):
        while (
          self.server.state.current_motion is previous_motion
          or self.server.state.current_motion is None
        ):
          await anyio.sleep(0)
      await side.abort()
      await open_finished.wait()
      assert isinstance(open_error, MicroSpinAbortedError)

  async def test_aborted_error_is_a_microspin_error_subclass(self):
    """Callers that just want ``except MicroSpinError`` still catch aborts."""
    await self.backend.home()
    await self.backend.abort()
    with pytest.raises(MicroSpinError):
      await self.backend.spin(g=100, duration=1)


class TestMockServerCliSmoke(AnyioTestBase):
  """A minimal sanity check that the server is usable with raw TCP, the same
  way a netcat session would use it."""

  _anyio_backends = ["asyncio"]

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("MicroSpin CLI smoke test does not accept lifespan options")
    self.server = MicroSpinMockServer()
    await stack.enter_async_context(self.server.lifespan())

  async def test_raw_tcp_round_trip(self):
    reader, writer = await asyncio.open_connection(self.server.host, self.server.port)
    try:
      writer.write(b"version\r\n")
      await writer.drain()
      # ACK!
      ack = await reader.readline()
      assert ack.startswith(b"ACK! version "), ack
      # data lines + OK!
      lines = []
      while True:
        line = await reader.readline()
        if line.startswith(b"OK! version "):
          break
        if line.startswith(b"ERROR!"):
          pytest.fail(f"unexpected ERROR: {line!r}")
        lines.append(line)
      joined = b"".join(lines).decode()
      assert "RandomServe" in joined
      assert "MS-1.3.3-mock" in joined
    finally:
      writer.close()
      try:
        await writer.wait_closed()
      except Exception:
        pass
