import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.brooks.precise_flex.driver import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.errors import (
  OperationInterrupted,
  PreciseFlexError,
  is_collision,
)
from pylabrobot.brooks.precise_flex.interrupt import halt_and_resync, halt_on_interrupt


def _make_driver() -> PreciseFlexDriver:
  """A driver whose socket is mocked: writes recorded, reads drain immediately (TimeoutError)."""
  d = PreciseFlexDriver(host="localhost")
  d.io = MagicMock()
  d.io.write = AsyncMock()
  d.io.readline = AsyncMock(side_effect=TimeoutError())
  return d


class TestWaitForEom(unittest.IsolatedAsyncioTestCase):
  """The non-blocking motion-wait: polls wherej and returns once the arm stops moving."""

  async def test_returns_when_motion_stops(self):
    """Returns at the first sample where the position stopped changing between polls."""
    wherej = iter(
      ["0 0 0 0 0", "5 5 5 5 5", "9.9 9.9 9.9 9.9 9.9", "10 10 10 10 10", "10 10 10 10 10"]
    )
    d = _make_driver()
    d.send_command = AsyncMock(side_effect=lambda cmd: next(wherej))
    await d._wait_for_eom(poll_interval=0)  # no error == returned at the settled sample

  async def test_returns_immediately_when_already_stationary(self):
    """An idle arm (e.g. halted short of its last target) returns at once, never hangs to reach it."""
    d = _make_driver()
    d.send_command = AsyncMock(return_value="113 81 218 64 70")  # stable every poll
    await d._wait_for_eom(poll_interval=0)

  async def test_keyboard_interrupt_halts_and_raises_operation_interrupted(self):
    """A user interrupt mid-wait sends halt on the connection and surfaces OperationInterrupted."""
    seq = iter(["0 0 0 0 0"])

    def fake(cmd: str) -> str:
      try:
        return next(seq)
      except StopIteration:
        raise KeyboardInterrupt()

    d = _make_driver()
    d.send_command = AsyncMock(side_effect=fake)
    with self.assertRaises(OperationInterrupted):
      await d._wait_for_eom(poll_interval=0)
    self.assertTrue(any(b"halt" in c.args[0] for c in d.io.write.call_args_list))

  async def test_cancelled_error_halts_and_reraises(self):
    """Cancellation is re-raised (not converted) but still sends halt first."""
    seq = iter(["0 0 0 0 0"])

    def fake(cmd: str) -> str:
      try:
        return next(seq)
      except StopIteration:
        raise asyncio.CancelledError()

    d = _make_driver()
    d.send_command = AsyncMock(side_effect=fake)
    with self.assertRaises(asyncio.CancelledError):
      await d._wait_for_eom(poll_interval=0)
    self.assertTrue(any(b"halt" in c.args[0] for c in d.io.write.call_args_list))

  async def test_timeout_when_never_settles(self):
    """An arm that never stops moving raises TimeoutError rather than spinning forever."""
    n = iter(range(1000))
    d = _make_driver()
    d.send_command = AsyncMock(side_effect=lambda cmd: f"{next(n)} 0 0 0 0")  # always changing
    with self.assertRaises(TimeoutError):
      await d._wait_for_eom(poll_interval=0, timeout=0)


class TestInterruptHelpers(unittest.IsolatedAsyncioTestCase):
  """The reusable guard primitives in interrupt.py."""

  async def test_halt_and_resync_flushes_sends_stop_then_drains(self):
    """With a stop command it writes a leading-newline-flushed halt, then drains; never closes."""
    io = MagicMock()
    io.write = AsyncMock()
    io.readline = AsyncMock(side_effect=[b"0\r\n", TimeoutError()])
    await halt_and_resync(io, b"halt")
    io.write.assert_awaited_once_with(b"\nhalt\n")
    io.stop.assert_not_called()

  async def test_halt_and_resync_drain_only_when_no_stop(self):
    """stop=None means resync-only: drain the socket, write nothing."""
    io = MagicMock()
    io.write = AsyncMock()
    io.readline = AsyncMock(side_effect=TimeoutError())
    await halt_and_resync(io)
    io.write.assert_not_called()

  async def test_converts_keyboard_interrupt(self):
    """KeyboardInterrupt -> stop runs, OperationInterrupted raised."""
    stop = AsyncMock()
    with self.assertRaises(OperationInterrupted):
      async with halt_on_interrupt(stop):
        raise KeyboardInterrupt()
    stop.assert_awaited_once()

  async def test_reraises_cancelled_error(self):
    """CancelledError -> stop runs, cancellation re-raised (semantics preserved)."""
    stop = AsyncMock()
    with self.assertRaises(asyncio.CancelledError):
      async with halt_on_interrupt(stop):
        raise asyncio.CancelledError()
    stop.assert_awaited_once()

  async def test_passes_through_other_exceptions_without_halting(self):
    """A normal error (e.g. an error-reply PreciseFlexError, as an E-stop produces) propagates
    unchanged and does NOT trigger a halt."""
    stop = AsyncMock()
    with self.assertRaises(ValueError):
      async with halt_on_interrupt(stop):
        raise ValueError()
    stop.assert_not_awaited()


class TestRequestSystemState(unittest.IsolatedAsyncioTestCase):
  """request_system_state reads the sysState word; PowerState decodes it (15 = hard E-stop)."""

  async def test_returns_state_word_and_decodes_to_powerstate(self):
    from pylabrobot.brooks.precise_flex.data_ids import PowerState

    d = _make_driver()
    d.send_command = AsyncMock(return_value="15")
    state = await d.request_system_state()
    self.assertEqual(state, 15)
    self.assertEqual(PowerState(state), PowerState.OFF_HARD_ESTOP)


class TestCollisionDetectionAndRecovery(unittest.IsolatedAsyncioTestCase):
  """Crash interrupts: recognise envelope errors, and recover (re-power + attach + home)."""

  def test_is_collision_recognises_collision_codes_only(self):
    """Envelope (-3100/-3122) and torque-saturation (-3101/-3105) errors are collisions; an E-stop,
    a no-attach error, or a non-PreciseFlexError is not."""
    for code in (-3100, -3101, -3105, -3122):
      self.assertTrue(is_collision(PreciseFlexError(code, "")), code)
    self.assertFalse(is_collision(PreciseFlexError(-1028, "")))  # hard E-stop, not a collision
    self.assertFalse(
      is_collision(PreciseFlexError(-1009, ""))
    )  # no robot attached, not a collision
    self.assertFalse(is_collision(ValueError()))

  async def test_recover_repowers_attaches_and_homes(self):
    """Recovery from a non-E-stop fault re-enables power, re-attaches, and re-homes."""
    d = _make_driver()
    d.request_system_state = AsyncMock(return_value=7)  # off, waiting for enable (not E-stop)
    d.power_on_robot = AsyncMock()
    d.attach = AsyncMock()
    d.home = AsyncMock()
    await d.recover_from_fault()
    d.power_on_robot.assert_awaited_once()
    d.attach.assert_awaited_once_with(1)
    d.home.assert_awaited_once()

  async def test_recover_refuses_while_estop_engaged(self):
    """A hard E-stop blocks recovery (release the button first); power is not touched."""
    d = _make_driver()
    d.request_system_state = AsyncMock(return_value=15)  # OFF_HARD_ESTOP
    d.power_on_robot = AsyncMock()
    d.home = AsyncMock()
    with self.assertRaises(PreciseFlexError) as ctx:
      await d.recover_from_fault()
    self.assertEqual(ctx.exception.replycode, -1028)
    d.power_on_robot.assert_not_awaited()
    d.home.assert_not_awaited()


if __name__ == "__main__":
  unittest.main()
