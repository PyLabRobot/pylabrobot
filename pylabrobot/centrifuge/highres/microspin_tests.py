"""Edge-case tests for :class:`MicroSpinBackend` using a stub asyncio stream
pair.

End-to-end wire-level behaviour (command mappings, status-blocks-during-motion,
reset happy path, etc.) lives in :mod:`mock_server_tests` and runs against the
real :class:`MicroSpinMockServer` over a TCP socket. The tests in *this* file
only cover situations the mock server cannot reasonably reproduce:

* Malformed protocol bytes from the device (bad ACK line, EOF mid-response).
* Operating without :meth:`MicroSpinBackend.setup`.
* Specific ``ERROR!`` sequences whose exact text we want to assert on.
* Argument validation that never reaches the wire.
* Monkey-patched ``send_command`` for verifying timeout extension logic.
* Serialization (no socket).
* Reset's error-handling branches, which need precisely-controllable
  ``ERROR!`` responses from each step.
"""

from __future__ import annotations

import asyncio
import unittest
import warnings
from typing import List, Tuple

from pylabrobot.centrifuge.highres.microspin_backend import (
  MicroSpinBackend,
  MicroSpinError,
  MicroSpinProtocolError,
)


class _FakeWriter:
  """Captures everything written by the backend so tests can assert on it."""

  def __init__(self) -> None:
    self.sent: bytearray = bytearray()
    self.closed = False

  def write(self, data: bytes) -> None:
    self.sent.extend(data)

  async def drain(self) -> None:
    return None

  def close(self) -> None:
    self.closed = True

  async def wait_closed(self) -> None:
    return None


class _FakeReader:
  """Yields a queue of lines one ``readline()`` at a time.

  When the queue is exhausted, returns ``b""`` (EOF) by default, or hangs
  indefinitely if ``hang_on_empty=True`` -- the latter is what we want when
  simulating a slow device whose response never arrives in time.
  """

  def __init__(self, lines: List[bytes], *, hang_on_empty: bool = False) -> None:
    self._lines: List[bytes] = list(lines)
    self._hang_on_empty = hang_on_empty

  async def readline(self) -> bytes:
    if not self._lines:
      if self._hang_on_empty:
        await asyncio.Event().wait()  # never fires
      return b""  # simulate EOF
    return self._lines.pop(0)


def _make_backend(server_lines: List[str]) -> Tuple[MicroSpinBackend, _FakeWriter]:
  """Build a backend pre-wired to a fake reader/writer pair. No real socket."""
  backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
  writer = _FakeWriter()
  reader = _FakeReader([s.encode("ascii") for s in server_lines])
  backend._writer = writer  # type: ignore[assignment]
  backend._reader = reader  # type: ignore[assignment]
  return backend, writer


def _sent_commands(writer: _FakeWriter) -> List[str]:
  text = writer.sent.decode("ascii")
  return [line for line in text.split("\r\n") if line]


class MicroSpinProtocolEdgeCaseTests(unittest.IsolatedAsyncioTestCase):
  """Cases the real mock server cannot easily produce (malformed bytes / EOF)."""

  async def test_protocol_error_on_bad_ack(self):
    backend, _ = _make_backend(["GARBAGE\r\n"])
    with self.assertRaises(MicroSpinProtocolError):
      await backend.send_command("status")

  async def test_connection_closed_mid_response(self):
    backend, _ = _make_backend(["ACK! status 1\r\n"])  # no terminator, then EOF
    with self.assertRaises(ConnectionError):
      await backend.send_command("status")

  async def test_setup_required_before_send_command(self):
    backend = MicroSpinBackend(host="ignored", port=0)
    with self.assertRaises(RuntimeError):
      await backend.send_command("status")

  async def test_error_response_carries_diagnostic_lines(self):
    backend, _ = _make_backend(
      [
        "ACK! spin 0 0 0 1 19\r\n",
        "Error 1: (00:00:01) -12: bad params\r\n",
        "ERROR! spin 0 0 0 1 19\r\n",
      ]
    )
    with self.assertRaises(MicroSpinError) as cm:
      await backend.send_command("spin 0 0 0 1")
    self.assertEqual(cm.exception.command_id, 19)
    self.assertEqual(cm.exception.command, "spin 0 0 0 1")
    self.assertEqual(cm.exception.error_lines, ["Error 1: (00:00:01) -12: bad params"])


class MicroSpinStreamResyncTests(unittest.IsolatedAsyncioTestCase):
  """After a cancelled/timed-out command, the next command must transparently
  drain the stale response and return its own result. Without this the
  protocol desyncs and every subsequent command reads someone else's data.
  """

  async def test_stale_response_is_drained_before_next_command(self):
    # Simulate the buffer state after a `status` was cancelled mid-flight:
    # the device eventually delivers its full response, then ours arrives.
    backend, writer = _make_backend(
      [
        # Stale response for the previously-cancelled command (cmd-id 5):
        "ACK! status 5\r\n",
        "Spindle Position: 9999\r\n",
        "OK! status 5\r\n",
        # Our fresh response (cmd-id 6):
        "ACK! version 6\r\n",
        "Version: MS-1.3.3\r\n",
        "OK! version 6\r\n",
      ]
    )
    # Pretend the previous send_command was cancelled after writing "status":
    backend._pending_terminator_count = 1

    data = await backend.send_command("version")
    self.assertEqual(data, ["Version: MS-1.3.3"])
    # And the stale-counter is back to zero.
    self.assertEqual(backend._pending_terminator_count, 0)

  async def test_partial_stale_response_drained(self):
    # Cancelled AFTER reading the ACK but before the terminator: the buffer
    # holds the remaining data + OK, then our response.
    backend, writer = _make_backend(
      [
        # Leftover from a partially-read previous response (cmd-id 5):
        "Spindle Position: 9999\r\n",  # data line we hadn't read yet
        "OK! status 5\r\n",  # terminator we hadn't read yet
        # Our fresh response (cmd-id 6):
        "ACK! version 6\r\n",
        "Version: MS-1.3.3\r\n",
        "OK! version 6\r\n",
      ]
    )
    backend._pending_terminator_count = 1

    data = await backend.send_command("version")
    self.assertEqual(data, ["Version: MS-1.3.3"])
    self.assertEqual(backend._pending_terminator_count, 0)

  async def test_multiple_stale_responses_drained(self):
    backend, writer = _make_backend(
      [
        # Two stale responses (cmd-ids 5 and 6):
        "ACK! status 5\r\n",
        "Spindle Position: 9999\r\n",
        "OK! status 5\r\n",
        "ACK! status 6\r\n",
        "Spindle Position: 1\r\n",
        "OK! status 6\r\n",
        # Our fresh response (cmd-id 7):
        "ACK! version 7\r\n",
        "Version: MS-1.3.3\r\n",
        "OK! version 7\r\n",
      ]
    )
    backend._pending_terminator_count = 2

    data = await backend.send_command("version")
    self.assertEqual(data, ["Version: MS-1.3.3"])
    self.assertEqual(backend._pending_terminator_count, 0)

  async def test_send_command_keeps_pending_count_on_cancellation(self):
    """Verify the bookkeeping that enables the drain.

    If `send_command` is cancelled (e.g. by ``asyncio.wait_for``) mid-read,
    the in-flight terminator must remain in the pending count so the *next*
    call can drain it.
    """
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    backend._writer = _FakeWriter()  # type: ignore[assignment]
    backend._reader = _FakeReader(  # type: ignore[assignment]
      [b"ACK! home 5\r\n"],  # ACK arrives, but the terminator never does
      hang_on_empty=True,
    )

    with self.assertRaises(asyncio.TimeoutError):
      await asyncio.wait_for(backend.send_command("home"), timeout=0.05)
    self.assertEqual(backend._pending_terminator_count, 1)


class MicroSpinValidationTests(unittest.IsolatedAsyncioTestCase):
  """Argument validation that raises before any bytes hit the wire."""

  async def test_spin_rounds_and_clamps_percents(self):
    backend, writer = _make_backend(
      [
        "ACK! spin 250 1 100 5 9\r\n",
        "OK! spin 250 1 100 5 9\r\n",
      ]
    )
    # 0.004 rounds to 0; we clamp to a minimum of 1%.
    await backend.spin(g=250.4, duration=5.2, acceleration=0.004, deceleration=1.0)
    self.assertEqual(_sent_commands(writer), ["spin 250 1 100 5"])

  async def test_spin_rejects_out_of_range_g(self):
    backend, _ = _make_backend([])
    for bad in [-1, 0, 3001, 100000]:
      with self.assertRaises(ValueError):
        await backend.spin(g=bad, duration=10)

  async def test_spin_rejects_short_duration(self):
    backend, _ = _make_backend([])
    with self.assertRaises(ValueError):
      await backend.spin(g=100, duration=0.5)

  async def test_spin_rejects_bad_acceleration(self):
    backend, _ = _make_backend([])
    for bad in [0, -0.1, 1.1, 2.0]:
      with self.assertRaises(ValueError):
        await backend.spin(g=100, duration=10, acceleration=bad)

  async def test_spin_rejects_bad_deceleration(self):
    backend, _ = _make_backend([])
    for bad in [0, -0.1, 1.1, 2.0]:
      with self.assertRaises(ValueError):
        await backend.spin(g=100, duration=10, deceleration=bad)

  async def test_spin_warns_below_low_g_threshold(self):
    backend, writer = _make_backend(
      [
        "ACK! spin 20 50 50 5 1\r\n",
        "OK! spin 20 50 50 5 1\r\n",
      ]
    )
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      await backend.spin(g=20, duration=5)
    low_g_warnings = [
      w for w in caught if issubclass(w.category, UserWarning) and "×g" in str(w.message)
    ]
    self.assertEqual(len(low_g_warnings), 1)
    self.assertEqual(_sent_commands(writer), ["spin 20 50 50 5"])

  async def test_spin_does_not_warn_at_or_above_threshold(self):
    backend, _ = _make_backend(
      [
        "ACK! spin 30 50 50 5 1\r\n",
        "OK! spin 30 50 50 5 1\r\n",
      ]
    )
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      await backend.spin(g=30, duration=5)
    low_g_warnings = [
      w for w in caught if issubclass(w.category, UserWarning) and "×g" in str(w.message)
    ]
    self.assertEqual(low_g_warnings, [])

  async def test_maintenance_door_and_lock_methods_raise_not_implemented(self):
    """open_door / close_door / the four lock primitives are maintenance-only
    on the MicroSpin (manual §6.7). They must raise rather than silently
    sending bytes; the firmware handles door + lock state internally as
    part of `open <bucket>`, `spin`, and `home`.
    """
    backend, writer = _make_backend([])
    for method_name in (
      "open_door",
      "close_door",
      "lock_door",
      "unlock_door",
      "lock_bucket",
      "unlock_bucket",
    ):
      with self.assertRaises(NotImplementedError):
        await getattr(backend, method_name)()
    self.assertEqual(_sent_commands(writer), [])


class MicroSpinHelperEdgeCaseTests(unittest.IsolatedAsyncioTestCase):
  """Helper behaviour with carefully-shaped responses we don't get from the mock."""

  async def test_get_errors_returns_lines_verbatim(self):
    backend, _ = _make_backend(
      [
        "ACK! errors 3 4\r\n",
        "Error 1: foo\r\n",
        "Error 2: bar\r\n",
        "Error 3: baz\r\n",
        "OK! errors 3 4\r\n",
      ]
    )
    self.assertEqual(
      await backend.get_errors(3),
      ["Error 1: foo", "Error 2: bar", "Error 3: baz"],
    )


class MicroSpinResetErrorPathTests(unittest.IsolatedAsyncioTestCase):
  """Reset's error-handling branches, which need controllable ERROR! at each step."""

  async def test_reset_swallows_abort_error_by_default(self):
    # The MicroSpin can legitimately reject an abort if there's nothing to abort.
    # `reset` should still go on to clear the button-abort latch AND wait.
    backend, writer = _make_backend(
      [
        "ACK! abort 1\r\n",
        "Error 1: nothing to abort\r\n",
        "ERROR! abort 1\r\n",
        "ACK! clearbuttonabort 2\r\n",
        "OK! clearbuttonabort 2\r\n",
        "ACK! status 3\r\n",
        "Spindle Position: 0\r\n",
        "OK! status 3\r\n",
      ]
    )
    result = await backend.reset()
    self.assertEqual(_sent_commands(writer), ["abort", "clearbuttonabort", "status"])
    self.assertEqual(result, {"Spindle Position": "0"})

  async def test_reset_propagates_abort_error_when_asked(self):
    backend, writer = _make_backend(
      [
        "ACK! abort 1\r\n",
        "Error 1: nothing to abort\r\n",
        "ERROR! abort 1\r\n",
      ]
    )
    with self.assertRaises(MicroSpinError):
      await backend.reset(swallow_abort_errors=False)
    self.assertEqual(_sent_commands(writer), ["abort"])

  async def test_reset_propagates_clear_button_abort_error(self):
    backend, writer = _make_backend(
      [
        "ACK! abort 1\r\n",
        "OK! abort 1\r\n",
        "ACK! clearbuttonabort 2\r\n",
        "Error 1: stuck\r\n",
        "ERROR! clearbuttonabort 2\r\n",
      ]
    )
    with self.assertRaises(MicroSpinError):
      await backend.reset()
    self.assertEqual(_sent_commands(writer), ["abort", "clearbuttonabort"])

  async def test_reset_propagates_status_error(self):
    backend, writer = _make_backend(
      [
        "ACK! abort 1\r\n",
        "OK! abort 1\r\n",
        "ACK! clearbuttonabort 2\r\n",
        "OK! clearbuttonabort 2\r\n",
        "ACK! status 3\r\n",
        "Error 1: spindle wedged\r\n",
        "ERROR! status 3\r\n",
      ]
    )
    with self.assertRaises(MicroSpinError):
      await backend.reset()
    self.assertEqual(_sent_commands(writer), ["abort", "clearbuttonabort", "status"])


class MicroSpinTimeoutExtensionTests(unittest.IsolatedAsyncioTestCase):
  """Monkey-patched send_command to verify long-motion timeout extension."""

  async def test_abort_uses_extended_default_timeout(self):
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    seen: list = []

    async def fake_send(cmd, *, timeout=None):
      seen.append((cmd, timeout))
      return []

    backend.send_command = fake_send  # type: ignore[assignment]
    await backend.abort()
    self.assertEqual(seen, [("abort", 180.0)])

    seen.clear()
    await backend.abort(timeout=5.0)
    self.assertEqual(seen, [("abort", 5.0)])

  async def test_wait_for_spindle_stopped_uses_poll_interval_per_call(self):
    """Each individual ``status`` is bounded by ``poll_interval``, not by
    the overall ``timeout``."""
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    seen: list = []

    async def fake_send(cmd, *, timeout=None):
      seen.append((cmd, timeout))
      return []

    backend.send_command = fake_send  # type: ignore[method-assign]
    # Defaults: poll_interval=60, total timeout=1800. First call should
    # get poll_interval (or min(poll_interval, remaining) which == 60).
    await backend.wait_for_spindle_stopped()
    self.assertEqual(seen, [("status", 60.0)])

    seen.clear()
    await backend.wait_for_spindle_stopped(poll_interval=5.0, timeout=100.0)
    self.assertEqual(seen, [("status", 5.0)])

  async def test_wait_for_spindle_stopped_retries_on_per_call_timeout(self):
    """If the per-call status times out, we issue another one."""
    import asyncio as _asyncio

    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    call_count = 0

    async def fake_send(cmd, *, timeout=None):
      nonlocal call_count
      call_count += 1
      if call_count < 3:
        raise _asyncio.TimeoutError("still spinning")
      return ["Spindle Position: 1958", "Door Position: -457"]

    backend.send_command = fake_send  # type: ignore[method-assign]
    result = await backend.wait_for_spindle_stopped(poll_interval=0.01, timeout=10.0)
    self.assertEqual(call_count, 3)
    self.assertEqual(result, {"Spindle Position": "1958", "Door Position": "-457"})

  async def test_wait_for_spindle_stopped_raises_when_total_budget_expires(self):
    """With every poll timing out and a tight overall budget, we raise."""
    import asyncio as _asyncio

    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)

    async def fake_send(cmd, *, timeout=None):
      raise _asyncio.TimeoutError("still spinning")

    backend.send_command = fake_send  # type: ignore[method-assign]
    with self.assertRaises(_asyncio.TimeoutError):
      await backend.wait_for_spindle_stopped(poll_interval=0.01, timeout=0.05)

  async def test_wait_for_spindle_stopped_propagates_microspin_error(self):
    """An ERROR! from status is a real device-state error -- don't retry."""
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    call_count = 0

    async def fake_send(cmd, *, timeout=None):
      nonlocal call_count
      call_count += 1
      raise MicroSpinError("status", 1, ["Error: spindle wedged"])

    backend.send_command = fake_send  # type: ignore[method-assign]
    with self.assertRaises(MicroSpinError):
      await backend.wait_for_spindle_stopped(poll_interval=10.0, timeout=60.0)
    self.assertEqual(call_count, 1)  # NOT retried

  async def test_wait_for_spindle_stopped_rejects_non_positive_poll_interval(self):
    backend = MicroSpinBackend(host="ignored", port=0)
    for bad in (0, -1, -0.001):
      with self.assertRaises(ValueError):
        await backend.wait_for_spindle_stopped(poll_interval=bad)


class MicroSpinConstructorTests(unittest.IsolatedAsyncioTestCase):
  def test_default_port_is_1000(self):
    """Backend, factory, and the class constant must agree on 1000."""
    from pylabrobot.centrifuge import MicroSpin

    self.assertEqual(MicroSpinBackend.DEFAULT_PORT, 1000)
    backend = MicroSpinBackend(host="example.invalid")
    self.assertEqual(backend.port, 1000)
    cf = MicroSpin(name="x", host="example.invalid")
    assert isinstance(cf.backend, MicroSpinBackend)
    self.assertEqual(cf.backend.port, 1000)

  def test_port_can_be_customised(self):
    from pylabrobot.centrifuge import MicroSpin

    backend = MicroSpinBackend(host="example.invalid", port=9001)
    self.assertEqual(backend.port, 9001)
    cf = MicroSpin(name="x", host="example.invalid", port=9001)
    assert isinstance(cf.backend, MicroSpinBackend)
    self.assertEqual(cf.backend.port, 9001)


class MicroSpinSerializeTests(unittest.IsolatedAsyncioTestCase):
  def test_serialize_includes_connection_info(self):
    backend = MicroSpinBackend(host="10.0.0.5", port=1234, timeout=12.5)
    s = backend.serialize()
    self.assertEqual(s["host"], "10.0.0.5")
    self.assertEqual(s["port"], 1234)
    self.assertEqual(s["timeout"], 12.5)

  def test_serialize_records_default_port(self):
    backend = MicroSpinBackend(host="10.0.0.5")
    self.assertEqual(backend.serialize()["port"], 1000)


if __name__ == "__main__":
  unittest.main()
