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
  """The non-blocking motion-wait: polls wherej and returns once the arm stops moving."""

  async def test_returns_when_motion_stops(self):
    """Returns once three samples in a row agree."""
    wherej = iter(["0 0 0 0 0", "5 5 5 5 5", "9.9 9.9 9.9 9.9 9.9"] + ["10 10 10 10 10"] * 3)
    d = _make_arm()
    d.send_command = AsyncMock(side_effect=lambda cmd: next(wherej))  # type: ignore[method-assign]
    await d.arm._wait_for_eom(poll_interval=0)  # no error == returned at the settled sample

  async def test_a_move_creeping_off_does_not_count_as_stopped(self):
    """Right after moveJ the arm creeps under the settle threshold (as logged), then moves."""
    # The arm's first four reads after a logged moveJ, then the three it settled on.
    replies = [
      "301.022 33.703 242.82 141.894 74.8",
      "301.022 33.694 242.807 141.909 74.8",
      "301.022 33.667 242.74 141.942 74.8",
      "301.038 33.586 242.595 142.024 74.8",
      "301.123 0.002 179.998 179.996 74.777",
      "301.124 0.002 179.999 180 74.777",
      "301.125 0.001 179.999 180.002 74.777",
    ]
    d = _make_arm()
    d.send_command = AsyncMock(side_effect=replies)  # type: ignore[method-assign]
    await d.arm._wait_for_eom(poll_interval=0)
    self.assertEqual(mocked(d.send_command).await_count, len(replies))

  async def test_returns_immediately_when_already_stationary(self):
    """An idle arm (e.g. halted short of its last target) returns at once, never hangs to reach it."""
    d = _make_arm()
    idle = "269.908 81.507 218.952 113.977 70.685"  # as the arm answered, poll after poll
    d.send_command = AsyncMock(return_value=idle)  # type: ignore[method-assign]
    await d.arm._wait_for_eom(poll_interval=0)

  async def test_keyboard_interrupt_halts_and_raises_operation_interrupted(self):
    """A user interrupt mid-wait sends halt on the connection and surfaces OperationInterrupted."""
    seq = iter(["0 0 0 0 0"])

    def fake(cmd: str) -> str:
      try:
        return next(seq)
      except StopIteration:
        raise KeyboardInterrupt()

    d = _make_arm()
    d.send_command = AsyncMock(side_effect=fake)  # type: ignore[method-assign]
    with self.assertRaises(OperationInterrupted):
      await d.arm._wait_for_eom(poll_interval=0)
    self.assertTrue(any(b"halt" in c.args[0] for c in mocked(d.io.write).call_args_list))

  async def test_cancelled_error_halts_and_reraises(self):
    """Cancellation is re-raised (not converted) but still sends halt first."""
    seq = iter(["0 0 0 0 0"])

    def fake(cmd: str) -> str:
      try:
        return next(seq)
      except StopIteration:
        raise asyncio.CancelledError()

    d = _make_arm()
    d.send_command = AsyncMock(side_effect=fake)  # type: ignore[method-assign]
    with self.assertRaises(asyncio.CancelledError):
      await d.arm._wait_for_eom(poll_interval=0)
    self.assertTrue(any(b"halt" in c.args[0] for c in mocked(d.io.write).call_args_list))

  async def test_timeout_when_never_settles(self):
    """An arm that never stops moving raises TimeoutError rather than spinning forever."""
    n = iter(range(1000))
    d = _make_arm()
    d.send_command = AsyncMock(side_effect=lambda cmd: f"{next(n)} 0 0 0 0")  # type: ignore[method-assign]  # always changing
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

  async def test_recover_repowers_attaches_and_homes_only_if_homing_was_lost(self):
    """Recovery from a non-E-stop fault re-powers and re-attaches; it homes only if needed."""
    for homed, homes in ((True, 0), (False, 1)):
      with self.subTest(homed=homed):
        d = _make_arm()
        not_estop = AsyncMock(return_value=7)
        d.request_system_state = not_estop  # type: ignore[method-assign]
        d.power_on_robot = AsyncMock()  # type: ignore[method-assign]
        d.attach = AsyncMock()  # type: ignore[method-assign]
        d.home = AsyncMock()  # type: ignore[method-assign]
        d._is_robot_homed = AsyncMock(return_value=homed)  # type: ignore[method-assign]
        await d.recover_from_fault()
        d.power_on_robot.assert_awaited_once()
        d.attach.assert_awaited_once_with(1)
        self.assertEqual(d.home.await_count, homes)

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
