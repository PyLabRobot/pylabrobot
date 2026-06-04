"""End-to-end integration tests for ``MicroSpinBackend`` against the
in-process :class:`MicroSpinMockServer`.

Unlike :mod:`microspin_tests` (which stubs the asyncio stream pair with
canned bytes), these tests open a real TCP socket from
:meth:`MicroSpinBackend.setup` to a real :class:`asyncio.Server` on
``127.0.0.1``. They are the highest-fidelity tests we can run without a
physical MicroSpin.
"""

from __future__ import annotations

import anyio

from pylabrobot.centrifuge.highres.microspin_backend import (
  MicroSpinAbortedError,
  MicroSpinBackend,
  MicroSpinError,
)
from pylabrobot.centrifuge.highres.mock_server import MicroSpinMockServer
from pylabrobot.testing.concurrency import AnyioTestBase


class _MockServerTestBase(AnyioTestBase):
  """Shared setup: a fresh mock server + a connected backend per test."""

  def assertLess(self, a, b, msg=None):
    assert a < b, msg or f"{a} is not less than {b}"

  def assertGreaterEqual(self, a, b, msg=None):
    assert a >= b, msg or f"{a} is not greater than or equal to {b}"

  async def _enter_lifespan(self, stack):
    await super()._enter_lifespan(stack)
    self.server = MicroSpinMockServer()
    await stack.enter_async_context(self.server)
    self.backend = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=5.0)
    await stack.enter_async_context(self.backend)


class TestMockServerCommandMapping(_MockServerTestBase):
  """Verify each pylabrobot backend method produces the expected wire-level
  effect on the mock server. These tests *replace* the stub-based mapping
  tests that used to live in :mod:`microspin_tests` -- using a real TCP
  server gives us higher confidence the wire format and ordering are right.
  """

  async def test_go_to_bucket1_after_home_lands_at_bucket_1(self):
    await self.backend.home()
    await self.backend.go_to_bucket1()
    self.assertEqual(self.server.state.at_bucket, 1)

  async def test_go_to_bucket2_after_home_lands_at_bucket_2(self):
    await self.backend.home()
    await self.backend.go_to_bucket2()
    self.assertEqual(self.server.state.at_bucket, 2)

  async def test_home_sets_homed_flag(self):
    self.assertFalse(self.server.state.homed)
    await self.backend.home()
    self.assertTrue(self.server.state.homed)
    self.assertTrue(await self.backend.is_homed())

  async def test_abort_latches_abort_state(self):
    self.assertFalse(self.server.state.abort_latched)
    await self.backend.abort()
    self.assertTrue(self.server.state.abort_latched)

  async def test_clear_button_abort_clears_latch(self):
    await self.backend.abort()
    self.assertTrue(self.server.state.abort_latched)
    await self.backend.clear_button_abort()
    self.assertFalse(self.server.state.abort_latched)

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
      self.assertEqual(recorded, [["100", "50", "25", "30"]])
    finally:
      MicroSpinMockServer._handlers["spin"] = original

  async def test_send_command_for_unknown_command_raises_microspin_error(self):
    with self.assertRaises(MicroSpinError):
      await self.backend.send_command("this_command_does_not_exist")

  async def test_request_version_round_trip(self):
    info = await self.backend.request_version()
    self.assertEqual(info["Product Name"], "RandomServe")
    self.assertEqual(info["Version"], "MS-1.3.3-mock")

  async def test_request_status_returns_dict_with_positions(self):
    status = await self.backend.request_status()
    self.assertIn("Spindle Position", status)
    self.assertIn("Door Position", status)

  async def test_reset_without_wait_for_settle_returns_none(self):
    await self.backend.home()
    # With wait_for_settle=False, reset shouldn't call status.
    result = await self.backend.reset(wait_for_settle=False)
    self.assertIsNone(result)


class TestMockServerIntegration(_MockServerTestBase):
  # --- basic protocol round-trips ----------------------------------------

  async def test_version_round_trip(self):
    info = await self.backend.request_version()
    self.assertEqual(info["Product Name"], "RandomServe")
    self.assertEqual(info["Version"], "MS-1.3.3-mock")

  async def test_homed_status_starts_false(self):
    self.assertFalse(await self.backend.is_homed())

  async def test_home_then_status(self):
    await self.backend.home()
    self.assertTrue(await self.backend.is_homed())
    status = await self.backend.request_status()
    self.assertIn("Spindle Position", status)
    self.assertIn("Door Position", status)

  async def test_open_requires_homed(self):
    with self.assertRaises(MicroSpinError):
      await self.backend.go_to_bucket1()

  async def test_open_after_home_succeeds(self):
    await self.backend.home()
    await self.backend.go_to_bucket1()
    self.assertEqual(self.server.state.at_bucket, 1)

  async def test_spin_requires_homed(self):
    # not homed -> error
    with self.assertRaises(MicroSpinError):
      await self.backend.spin(g=100, duration=1)
    # home -> spin works
    await self.backend.home()
    await self.backend.spin(g=100, duration=1)

  async def test_spin_auto_closes_open_door(self):
    """`spin` should close the door automatically -- callers never have to."""
    await self.backend.home()
    await self.backend.go_to_bucket1()
    # Sanity: bucket is now presented and door is open
    self.assertEqual(self.server.state.at_bucket, 1)
    self.assertGreater(self.server.state.door_position, 0)
    # Spin succeeds despite the open door; afterwards the door is closed.
    await self.backend.spin(g=100, duration=1)
    self.assertLess(self.server.state.door_position, 0)
    self.assertIsNone(self.server.state.at_bucket)

  async def test_unknown_command_errors(self):
    with self.assertRaises(MicroSpinError):
      await self.backend.send_command("does_not_exist")

  # --- the status-blocking gate (the reason we built this) ----------------

  async def test_status_blocks_until_motion_completes(self):
    """The reason `reset()`'s third step works: status doesn't answer
    until the active motion task is done."""
    # Make the home motion noticeably slow so we can race against it.
    self.server.motion_dwell["home"] = 0.2

    async def issue_home():
      await self.backend.home()

    # Use a separate backend connection to send `status` while home is running.
    side = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=5.0)
    async with side, anyio.create_task_group() as tg:
      tg.start_soon(issue_home)
      # Give the home command time to start
      await anyio.sleep(0.02)
      # Now ask for status -- this should block until home completes.
      t0 = anyio.current_time()
      await side.request_status()
      elapsed = anyio.current_time() - t0
      # status should have waited at least ~0.1s (most of the remaining home dwell)
      self.assertGreater(elapsed, 0.05)

  # --- reset() and abort flow --------------------------------------------

  async def test_reset_sequence_against_real_server(self):
    await self.backend.home()
    result = await self.backend.reset()
    # `reset` returns the final status dict, populated from the mock state.
    assert result is not None
    self.assertIn("Spindle Position", result)
    self.assertIn("Door Position", result)

  async def test_abort_interrupts_motion_then_reset_recovers(self):
    self.server.motion_dwell["home"] = 0.5  # long home
    await self.backend.home()  # one home so the state is "homed=True"

    # Now start another motion (open) and abort it mid-way.
    self.server.motion_dwell["open"] = 0.3
    side = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=5.0)
    async with side, anyio.create_task_group() as tg:

      async def run_open_expect_error():
        with self.assertRaises(MicroSpinError):
          await self.backend.go_to_bucket1()

      tg.start_soon(run_open_expect_error)
      await anyio.sleep(0.05)

      await side.abort()
      # The server is now in abort-latched state; further motion should fail
      with self.assertRaises(MicroSpinError):
        await side.go_to_bucket1()
      # reset() clears the latch and waits for the (already-stopped) spindle
      await side.reset()
      # Motion works again
      await side.go_to_bucket1()
      self.assertEqual(self.server.state.at_bucket, 1)


class TestMockServerLowGHang(AnyioTestBase):
  """The mock can simulate the firmware's low-G "stopped sensor never latches"
  bug. We use this to verify that callers can recover via a timeout."""

  async def _enter_lifespan(self, stack):
    await super()._enter_lifespan(stack)
    self.server = MicroSpinMockServer()
    self.server.state.simulate_low_g_hang = True
    await stack.enter_async_context(self.server)
    # Very short timeout so the test doesn't take forever
    self.backend = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=0.5)
    await stack.enter_async_context(self.backend)

  async def test_status_hangs_after_motion_with_low_g_simulation(self):
    await self.backend.send_command("home")  # populates state.current_motion
    # status would normally answer immediately; in low-G-hang mode it sits.
    with self.assertRaises(TimeoutError):
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
    self.assertEqual(tuple(head_names), self.EXPECTED_COMMANDS)

  async def test_info_includes_parameter_signatures(self):
    lines = await self.backend.send_command("info")
    text = "\n".join(lines)
    # Spot-check a representative slice -- the exact wording the device prints.
    self.assertIn(
      "open                            - Open the door and present the specified bucket.",
      text,
    )
    self.assertIn("     Parameters(1): <bucket>", text)
    self.assertIn("     <bucket> the bucket number to present", text)
    self.assertIn("spin, sp                        - Spin the centrifuge.", text)
    self.assertIn(
      "     Parameters(4): <G-force> <acceleration> <deceleration> <time>",
      text,
    )

  async def test_help_for_known_command_returns_signature(self):
    lines = await self.backend.send_command("help spin")
    text = "\n".join(lines)
    self.assertIn("spin, sp - Spin the centrifuge.", text)
    self.assertIn(
      "     Parameters(4): <G-force> <acceleration> <deceleration> <time>",
      text,
    )

  async def test_help_for_alias_works(self):
    # `help hss` should resolve via the alias to `homedstatus`'s entry.
    lines = await self.backend.send_command("help hss")
    text = "\n".join(lines)
    self.assertIn("homedstatus, hss", text)

  async def test_help_rejects_unknown_command(self):
    with self.assertRaises(MicroSpinError):
      await self.backend.send_command("help bogus")

  async def test_aliases_resolve_to_canonical_handlers(self):
    # `s` -> `status`, `v` -> `version`, `hss` -> `homedstatus`, `?` -> `list`,
    # `cba` -> `clearbuttonabort`. Just spot-check that they don't error.
    await self.backend.send_command("s")
    info = await self.backend.send_command("v")
    self.assertTrue(any("Version:" in line for line in info))
    await self.backend.send_command("hss")
    listing = await self.backend.send_command("?")
    self.assertGreaterEqual(len(listing), len(self.EXPECTED_COMMANDS))
    await self.backend.send_command("cba")

  async def test_maintenance_commands_are_not_recognised(self):
    # `od`, `cd`, `lockdoor`, `unlockdoor`, `locknest`, `unlocknest` are
    # maintenance-only on the real device and not in `list`; the mock must
    # treat them like any unknown command.
    for cmd in ("od", "cd", "lockdoor", "unlockdoor", "locknest", "unlocknest"):
      with self.assertRaises(MicroSpinError):
        await self.backend.send_command(cmd)

  async def test_logcommands_accepts_yes_or_no(self):
    await self.backend.send_command("logcommands yes")
    await self.backend.send_command("logcommands no")
    with self.assertRaises(MicroSpinError):
      await self.backend.send_command("logcommands maybe")
    with self.assertRaises(MicroSpinError):
      await self.backend.send_command("logcommands")

  async def test_commandstat_reports_no_history(self):
    # We don't track history in the mock, so cstat for any id misses.
    with self.assertRaises(MicroSpinError) as cm:
      await self.backend.send_command("cstat 5")
    self.assertTrue(any("not in recorded history" in line for line in cm.exception.error_lines))

  async def test_whoami_returns_a_number(self):
    out = await self.backend.send_command("whoami")
    self.assertEqual(len(out), 1)
    int(out[0])  # must parse as an integer

  async def test_abort_emits_cba_instruction_data_line(self):
    # The real device returns this data line before its OK terminator.
    data = await self.backend.send_command("abort")
    self.assertEqual(
      data,
      ['Issue the "clearbuttonabort" (cba) command to re-enable the machine'],
    )


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
    with self.assertRaises(MicroSpinAbortedError):
      await self.backend.spin(g=500, duration=1)
    with self.assertRaises(MicroSpinAbortedError):
      await self.backend.go_to_bucket1()
    with self.assertRaises(MicroSpinAbortedError):
      await self.backend.home()

  async def test_cba_clears_latch_and_motion_works_again(self):
    await self.backend.home()
    await self.backend.abort()
    with self.assertRaises(MicroSpinAbortedError):
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
    # Issue abort from a second connection.
    side = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=5.0)
    async with side, anyio.create_task_group() as tg:

      async def run_open_expect_abort():
        with self.assertRaises(MicroSpinAbortedError):
          await self.backend.go_to_bucket1()

      tg.start_soon(run_open_expect_abort)
      await anyio.sleep(0.05)

      await side.abort()

  async def test_aborted_error_is_a_microspin_error_subclass(self):
    """Callers that just want ``except MicroSpinError`` still catch aborts."""
    await self.backend.home()
    await self.backend.abort()
    with self.assertRaises(MicroSpinError):
      await self.backend.spin(g=100, duration=1)


class TestMockServerCliSmoke(AnyioTestBase):
  """A minimal sanity check that the server is usable with raw TCP, the same
  way a netcat session would use it."""

  async def test_raw_tcp_round_trip(self):
    import anyio.streams.buffered

    async with MicroSpinMockServer() as srv:
      stream = await anyio.connect_tcp(srv.host, srv.port)
      async with stream:
        await stream.send(b"version\r\n")
        # ACK!
        buf_stream = anyio.streams.buffered.BufferedByteStream(stream)
        ack = await buf_stream.receive_until(b"\n", max_bytes=1024)
        self.assertTrue(ack.startswith(b"ACK! version "), ack)
        # data lines + OK!
        lines = []
        while True:
          line = await buf_stream.receive_until(b"\n", max_bytes=1024)
          if line.startswith(b"OK! version "):
            break
          if line.startswith(b"ERROR!"):
            raise AssertionError(f"unexpected ERROR: {line!r}")
          lines.append(line)
        joined = b"".join(lines).decode()
        self.assertIn("RandomServe", joined)
        self.assertIn("MS-1.3.3-mock", joined)
