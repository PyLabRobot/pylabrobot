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
import unittest

from pylabrobot.centrifuge.highres.microspin_backend import (
  MicroSpinBackend,
  MicroSpinError,
)
from pylabrobot.centrifuge.highres.mock_server import MicroSpinMockServer


class _MockServerTestBase(unittest.IsolatedAsyncioTestCase):
  """Shared setup: a fresh mock server + a connected backend per test."""

  async def asyncSetUp(self):
    self.server = MicroSpinMockServer()
    await self.server.start()
    self.backend = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=5.0)
    await self.backend.setup()

  async def asyncTearDown(self):
    await self.backend.stop()
    await self.server.stop()


class MockServerCommandMappingTests(_MockServerTestBase):
  """Verify each pylabrobot backend method produces the expected wire-level
  effect on the mock server. These tests *replace* the stub-based mapping
  tests that used to live in :mod:`microspin_tests` -- using a real TCP
  server gives us higher confidence the wire format and ordering are right.
  """

  async def test_open_door_emits_od(self):
    self.assertLess(self.server.state.door_position, 0)
    await self.backend.open_door()
    self.assertGreater(self.server.state.door_position, 0)

  async def test_close_door_emits_cd(self):
    await self.backend.open_door()
    await self.backend.close_door()
    self.assertLess(self.server.state.door_position, 0)

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

  async def test_get_version_round_trip(self):
    info = await self.backend.get_version()
    self.assertEqual(info["Product Name"], "RandomServe")
    self.assertEqual(info["Version"], "MS-1.3.3-mock")

  async def test_get_status_returns_dict_with_positions(self):
    status = await self.backend.get_status()
    self.assertIn("Spindle Position", status)
    self.assertIn("Door Position", status)

  async def test_reset_without_wait_for_settle_returns_none(self):
    await self.backend.home()
    # With wait_for_settle=False, reset shouldn't call status.
    result = await self.backend.reset(wait_for_settle=False)
    self.assertIsNone(result)


class MockServerIntegrationTests(_MockServerTestBase):
  # --- basic protocol round-trips ----------------------------------------

  async def test_version_round_trip(self):
    info = await self.backend.get_version()
    self.assertEqual(info["Product Name"], "RandomServe")
    self.assertEqual(info["Version"], "MS-1.3.3-mock")

  async def test_homed_status_starts_false(self):
    self.assertFalse(await self.backend.is_homed())

  async def test_home_then_status(self):
    await self.backend.home()
    self.assertTrue(await self.backend.is_homed())
    status = await self.backend.get_status()
    self.assertIn("Spindle Position", status)
    self.assertIn("Door Position", status)

  async def test_open_requires_homed(self):
    with self.assertRaises(MicroSpinError):
      await self.backend.go_to_bucket1()

  async def test_open_after_home_succeeds(self):
    await self.backend.home()
    await self.backend.go_to_bucket1()
    self.assertEqual(self.server.state.at_bucket, 1)

  async def test_spin_requires_homed_and_door_closed(self):
    # not homed -> error
    with self.assertRaises(MicroSpinError):
      await self.backend.spin(g=100, duration=1)
    # home, then leave door open via go_to_bucket1
    await self.backend.home()
    await self.backend.go_to_bucket1()
    with self.assertRaises(MicroSpinError):
      await self.backend.spin(g=100, duration=1)
    # close door -> spin works
    await self.backend.close_door()
    await self.backend.spin(g=100, duration=1)

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
    await side.setup()
    try:
      home_task = asyncio.create_task(issue_home())
      # Give the home command time to start
      await asyncio.sleep(0.02)
      # Now ask for status -- this should block until home completes.
      t0 = asyncio.get_event_loop().time()
      await side.get_status()
      elapsed = asyncio.get_event_loop().time() - t0
      await home_task
      # status should have waited at least ~0.1s (most of the remaining home dwell)
      self.assertGreater(elapsed, 0.05)
    finally:
      await side.stop()

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
    open_task = asyncio.create_task(self.backend.go_to_bucket1())
    await asyncio.sleep(0.05)

    side = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=5.0)
    await side.setup()
    try:
      await side.abort()
      # The original open command should have raised because abort cancelled it
      with self.assertRaises(MicroSpinError):
        await open_task
      # The server is now in abort-latched state; further motion should fail
      with self.assertRaises(MicroSpinError):
        await side.go_to_bucket1()
      # reset() clears the latch and waits for the (already-stopped) spindle
      await side.reset()
      # Motion works again
      await side.go_to_bucket1()
      self.assertEqual(self.server.state.at_bucket, 1)
    finally:
      await side.stop()


class MockServerLowGHangTests(unittest.IsolatedAsyncioTestCase):
  """The mock can simulate the firmware's low-G "stopped sensor never latches"
  bug. We use this to verify that callers can recover via a timeout."""

  async def asyncSetUp(self):
    self.server = MicroSpinMockServer()
    self.server.state.simulate_low_g_hang = True
    await self.server.start()
    # Very short timeout so the test doesn't take forever
    self.backend = MicroSpinBackend(host=self.server.host, port=self.server.port, timeout=0.5)
    await self.backend.setup()

  async def asyncTearDown(self):
    await self.backend.stop()
    await self.server.stop()

  async def test_status_hangs_after_motion_with_low_g_simulation(self):
    await self.backend.send_command("home")  # populates state.current_motion
    # status would normally answer immediately; in low-G-hang mode it sits.
    with self.assertRaises(asyncio.TimeoutError):
      await self.backend.get_status()


class MockServerCliSmokeTest(unittest.IsolatedAsyncioTestCase):
  """A minimal sanity check that the server is usable with raw TCP, the same
  way a netcat session would use it."""

  async def test_raw_tcp_round_trip(self):
    async with MicroSpinMockServer() as srv:
      reader, writer = await asyncio.open_connection(srv.host, srv.port)
      try:
        writer.write(b"version\r\n")
        await writer.drain()
        # ACK!
        ack = await reader.readline()
        self.assertTrue(ack.startswith(b"ACK! version "), ack)
        # data lines + OK!
        lines = []
        while True:
          line = await reader.readline()
          if line.startswith(b"OK! version "):
            break
          if line.startswith(b"ERROR!"):
            self.fail(f"unexpected ERROR: {line!r}")
          lines.append(line)
        joined = b"".join(lines).decode()
        self.assertIn("RandomServe", joined)
        self.assertIn("MS-1.3.3-mock", joined)
      finally:
        writer.close()
        try:
          await writer.wait_closed()
        except Exception:
          pass


if __name__ == "__main__":
  unittest.main()
