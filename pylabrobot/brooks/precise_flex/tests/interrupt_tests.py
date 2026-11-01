import asyncio
import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.brooks.precise_flex.driver.errors import (
  OperationInterrupted,
  PreciseFlexError,
  is_collision,
)
from pylabrobot.brooks.precise_flex.driver.master import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.interrupt import halt_and_resync, halt_on_interrupt


def mocked(method: object) -> AsyncMock:
  """A real method that a test replaced with an ``AsyncMock``.

  Assertions like ``call_args_list`` live on the mock, not on the declared
  method type, so they need narrowing before mypy will accept them.
  """
  return cast(AsyncMock, method)


def _make_arm() -> PreciseFlexDriver:
  """An arm whose socket is mocked: writes recorded, reads drain immediately (TimeoutError)."""
  d = PreciseFlexDriver(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=500.0,
  )
  d.io = MagicMock()
  d.io.write = AsyncMock()
  d.io.readline = AsyncMock(side_effect=TimeoutError())
  return d


class TestWaitForEom(unittest.IsolatedAsyncioTestCase):
  """The non-blocking motion-wait: polls state and returns once the arm stops moving."""

  async def _wait_through(self, states: list) -> AsyncMock:
    d = _make_arm()
    d.send_command = AsyncMock(side_effect=states)  # type: ignore[method-assign]
    await d.arm._wait_for_eom(poll_interval=0)
    return mocked(d.send_command)

  async def test_waits_through_a_move_at_every_phase(self):
    """The phases a move read on the arm: accelerating, constant, decelerating, nulling, idle."""
    send = await self._wait_through(["2", "3", "5", "15", "1", "1"])
    self.assertEqual(send.await_count, 6)
    send.assert_awaited_with("state")

  async def test_returns_at_once_when_already_stopped(self):
    """An idle arm (e.g. halted short of its last target) returns at once, never hangs to reach it."""
    send = await self._wait_through(["1", "1"])
    self.assertEqual(send.await_count, 2)

  async def test_freedrive_counts_as_stopped(self):
    """Freedrive read 10 on the arm: a read during it must not wait for the hand to let go."""
    send = await self._wait_through(["10", "10"])
    self.assertEqual(send.await_count, 2)

  async def test_a_stale_reply_does_not_end_the_wait(self):
    """After an interrupted `moveJ`, its late empty reply answers `state`: the arm is still moving."""
    send = await self._wait_through(["", "3", "5", "15", "1", "1"])
    self.assertEqual(send.await_count, 6)

  async def test_keyboard_interrupt_halts_and_raises_operation_interrupted(self):
    """A user interrupt mid-wait sends halt on the connection and surfaces OperationInterrupted."""
    d = _make_arm()
    d.send_command = AsyncMock(side_effect=["2", KeyboardInterrupt()])  # type: ignore[method-assign]
    with self.assertRaises(OperationInterrupted):
      await d.arm._wait_for_eom(poll_interval=0)
    self.assertTrue(any(b"halt" in c.args[0] for c in mocked(d.io.write).call_args_list))

  async def test_cancelled_error_halts_and_reraises(self):
    """Cancellation is re-raised (not converted) but still sends halt first."""
    d = _make_arm()
    d.send_command = AsyncMock(side_effect=["2", asyncio.CancelledError()])  # type: ignore[method-assign]
    with self.assertRaises(asyncio.CancelledError):
      await d.arm._wait_for_eom(poll_interval=0)
    self.assertTrue(any(b"halt" in c.args[0] for c in mocked(d.io.write).call_args_list))

  async def test_timeout_when_never_stops(self):
    """An arm that never stops moving raises TimeoutError rather than spinning forever."""
    d = _make_arm()
    d.send_command = AsyncMock(return_value="3")  # type: ignore[method-assign]
    with self.assertRaises(TimeoutError):
      await d.arm._wait_for_eom(poll_interval=0, timeout=0)


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

    d = _make_arm()
    d.send_command = AsyncMock(return_value="15")  # type: ignore[method-assign]
    state = await d.request_system_state()
    self.assertEqual(state, 15)
    self.assertEqual(PowerState(state), PowerState.OFF_HARD_ESTOP)


class TestCollisionDetectionAndRecovery(unittest.IsolatedAsyncioTestCase):
  """Crash interrupts: recognise envelope errors, and recover (re-power, attach, home if lost)."""

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
    d = _make_arm()
    not_estop = AsyncMock(return_value=7)
    d.request_system_state = not_estop  # type: ignore[method-assign]
    d.power_on_robot = AsyncMock()  # type: ignore[method-assign]
    d.attach = AsyncMock()  # type: ignore[method-assign]
    d.home = AsyncMock()  # type: ignore[method-assign]
    await d.recover_from_fault()
    d.power_on_robot.assert_awaited_once()
    d.attach.assert_awaited_once_with(1)
    d.home.assert_awaited_once()

  async def test_recover_refuses_while_estop_engaged(self):
    """A hard E-stop blocks recovery (release the button first); power is not touched."""
    d = _make_arm()
    d.request_system_state = AsyncMock(return_value=15)  # type: ignore[method-assign]  # OFF_HARD_ESTOP
    d.power_on_robot = AsyncMock()  # type: ignore[method-assign]
    d.home = AsyncMock()  # type: ignore[method-assign]
    with self.assertRaises(PreciseFlexError) as ctx:
      await d.recover_from_fault()
    self.assertEqual(ctx.exception.replycode, -1028)
    d.power_on_robot.assert_not_awaited()
    d.home.assert_not_awaited()


if __name__ == "__main__":
  unittest.main()
