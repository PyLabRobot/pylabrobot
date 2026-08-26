"""Pins timing constants that a golden-frame capture cannot see.

A golden-frame comparison asserts on *what* gets sent, not *when*. The
simulators in :mod:`.darwin_golden_frame_tests` are deliberately
deterministic by call count rather than by wall-clock time, precisely so
that scheduling jitter cannot change a captured packet sequence -- but a
direct consequence is that a regression to a poll interval or a timeout
deadline leaves no trace there at all: the same packets go out over the
wire whether a poll happens every 0.2 s or every 4 s. This module pins that
class of constant directly, either by mocking ``time.sleep``/
``time.monotonic`` and asserting on the arguments the ported code calls
them with, or, for a default that no golden scenario ever exercises
because every golden call site overrides it explicitly, by asserting the
default value itself.
"""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

from ..errors import BravoError, ErrorType
from ..protocol.gemini.engine import GeminiEngine
from ..protocol.gemini.enums import MotorState
from . import axis as axis_module
from . import motion, sequences
from .darwin_golden_frame_tests import FakeGeminiTransport, _install_state_sim, _StateSim
from .topology import axis_address


class PollIntervalTests(unittest.TestCase):
  """Pins ``_STATE_POLL`` at each of its call sites via mocked ``time.sleep``."""

  def test_commutate_sleeps_at_the_state_poll_interval(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _StateSim(settle_after=3)
      _install_state_sim(fake, addr, sim)
      with mock.patch("pylabrobot.agilent.bravo.darwin.axis.sleep") as mock_sleep:
        axis_module.commutate(engine, addr, "X")
    finally:
      engine.stop_receiving()
    self.assertTrue(mock_sleep.call_args_list, "commutate() never called time.sleep")
    for call in mock_sleep.call_args_list:
      self.assertEqual(call.args[0], axis_module._STATE_POLL)

  def test_home_sleeps_at_the_state_poll_interval(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("y")
      sim = _StateSim(settle_after=3)
      sim.force(MotorState.COMMUTATED)
      _install_state_sim(fake, addr, sim)
      with mock.patch("pylabrobot.agilent.bravo.darwin.axis.sleep") as mock_sleep:
        axis_module.home(engine, addr, "Y")
    finally:
      engine.stop_receiving()
    self.assertTrue(mock_sleep.call_args_list, "home() never called time.sleep")
    for call in mock_sleep.call_args_list:
      self.assertEqual(call.args[0], axis_module._STATE_POLL)

  def test_enable_sleeps_at_the_state_poll_interval(self):
    """``enable()`` has no ``poll`` parameter of its own -- it uses
    ``_STATE_POLL`` directly and is not exercised by any golden scenario
    (every golden axis starts enabled)."""
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _StateSim(settle_after=3)
      sim.force(MotorState.DISABLED)
      _install_state_sim(fake, addr, sim)
      with mock.patch("pylabrobot.agilent.bravo.darwin.axis.sleep") as mock_sleep:
        axis_module.enable(engine, addr, "X")
    finally:
      engine.stop_receiving()
    self.assertTrue(mock_sleep.call_args_list, "enable() never called time.sleep")
    for call in mock_sleep.call_args_list:
      self.assertEqual(call.args[0], axis_module._STATE_POLL)

  def test_initialize_force_grace_delay(self):
    """``initialize(force=True)`` sleeps a fixed 0.05 s after disabling the
    axis, before re-commutating -- a magic literal, not a named constant,
    but the same "invisible to golden" shape."""
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _StateSim(settle_after=3)
      sim.force(MotorState.READY)
      _install_state_sim(fake, addr, sim)
      with mock.patch("pylabrobot.agilent.bravo.darwin.axis.sleep") as mock_sleep:
        axis_module.initialize(engine, addr, "X", force=True)
    finally:
      engine.stop_receiving()
    self.assertTrue(mock_sleep.call_args_list, "initialize(force=True) never called time.sleep")
    self.assertEqual(mock_sleep.call_args_list[0], mock.call(0.05))


class TimeoutDeadlineTests(unittest.TestCase):
  """Pins the commutation and homing deadlines: the "obvious candidates".

  Each test mocks ``time.monotonic`` with an exact, finite sequence of
  return values (never a formula tied to real elapsed time, which is what
  made the earlier wall-clock simulators flaky) so the deadline comparison
  is exercised precisely at a point just under, and just over, the actual
  constant -- proving both that the constant's numeric value is what it
  should be, and that it is actually wired into the timeout check.
  """

  def test_commutate_does_not_time_out_just_under_the_deadline(self):
    deadline = axis_module._DEFAULT_COMMUTATE_TIMEOUT
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _StateSim(settle_after=2)  # Settles on the 2nd read, right after the elapsed check.
      _install_state_sim(fake, addr, sim)
      with (
        mock.patch("pylabrobot.agilent.bravo.darwin.axis.sleep"),
        mock.patch(
          "pylabrobot.agilent.bravo.darwin.axis.monotonic",
          side_effect=[0.0, deadline - 0.1],
        ),
      ):
        axis_module.commutate(engine, addr, "X")  # Must not raise.
    finally:
      engine.stop_receiving()

  def test_commutate_times_out_just_over_the_deadline(self):
    deadline = axis_module._DEFAULT_COMMUTATE_TIMEOUT
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _StateSim(settle_after=10**9)  # Never settles.
      _install_state_sim(fake, addr, sim)
      with (
        mock.patch("pylabrobot.agilent.bravo.darwin.axis.sleep"),
        mock.patch(
          "pylabrobot.agilent.bravo.darwin.axis.monotonic",
          side_effect=[0.0, deadline + 0.1],
        ),
      ):
        with self.assertRaises(BravoError) as ctx:
          axis_module.commutate(engine, addr, "X")
    finally:
      engine.stop_receiving()
    self.assertEqual(ctx.exception.error_type, ErrorType.COULD_NOT_ALIGN)

  def test_home_does_not_time_out_just_under_the_deadline(self):
    deadline = axis_module._DEFAULT_HOME_TIMEOUT
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("y")
      sim = _StateSim(settle_after=2)
      sim.force(MotorState.COMMUTATED)
      _install_state_sim(fake, addr, sim)
      with (
        mock.patch("pylabrobot.agilent.bravo.darwin.axis.sleep"),
        mock.patch(
          "pylabrobot.agilent.bravo.darwin.axis.monotonic",
          side_effect=[0.0, deadline - 0.1],
        ),
      ):
        axis_module.home(engine, addr, "Y")  # Must not raise.
    finally:
      engine.stop_receiving()

  def test_home_times_out_just_over_the_deadline(self):
    deadline = axis_module._DEFAULT_HOME_TIMEOUT
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("y")
      sim = _StateSim(settle_after=10**9)
      sim.force(MotorState.COMMUTATED)
      _install_state_sim(fake, addr, sim)
      with (
        mock.patch("pylabrobot.agilent.bravo.darwin.axis.sleep"),
        mock.patch(
          "pylabrobot.agilent.bravo.darwin.axis.monotonic",
          side_effect=[0.0, deadline + 0.1],
        ),
      ):
        with self.assertRaises(BravoError) as ctx:
          axis_module.home(engine, addr, "Y")
    finally:
      engine.stop_receiving()
    self.assertEqual(ctx.exception.error_type, ErrorType.COULD_NOT_HOME)

  def test_default_timeout_values(self):
    """Pins the exact constants, independent of the comparison logic above."""
    self.assertEqual(axis_module._STATE_POLL, 0.2)
    self.assertEqual(axis_module._DEFAULT_COMMUTATE_TIMEOUT, 15.0)
    self.assertEqual(axis_module._DEFAULT_HOME_TIMEOUT, 20.0)


class OtherInvisibleConstantsTests(unittest.TestCase):
  """Direct value pins for the remaining wall-clock-only constants found in
  motion.py and sequences.py.

  These share the same shape (no golden scenario exercises their default,
  since every golden call site passes an explicit override) but are pinned
  by value equality rather than by mocked behavior: ``_DEFAULT_SETTLE_POLL``
  and ``_BUSY_CONFIRM`` belong to ``wait_for_ready``/``wait_for_all_ready``,
  which the ported ``DarwinController.move()`` does not call at all (it
  uses ``_MoveWaiter`` instead) -- a full behavioral mock would be
  exercising a path this port's controller never reaches, so a value pin is
  the honest level of coverage for now.
  """

  def test_motion_timeout_constants(self):
    self.assertEqual(motion._DEFAULT_MOVE_TIMEOUT, 30.0)
    self.assertEqual(motion._DEFAULT_SETTLE_POLL, 0.01)
    self.assertEqual(motion._BUSY_CONFIRM, 0.5)

  def test_sequences_default_timeouts_and_settle(self):
    self.assertEqual(inspect.signature(sequences.force_move).parameters["timeout"].default, 10.0)
    self.assertEqual(inspect.signature(sequences.grip).parameters["timeout"].default, 8.0)
    self.assertEqual(inspect.signature(sequences.open_gripper).parameters["timeout"].default, 6.0)
    self.assertEqual(inspect.signature(sequences.jog).parameters["timeout"].default, 30.0)
    self.assertEqual(inspect.signature(sequences.jog).parameters["settle"].default, 0.25)


if __name__ == "__main__":
  unittest.main()
