"""Unit tests for ``KX2ArmBackend.find_z_with_proximity_sensor``.

Covers the four interesting paths through the descent + IL-arm + cancel-on-trip
state machine:

* Sensor trips mid-descent -> task is cancelled, motor_stop + IL-restore run.
* Sensor never trips -> RuntimeError mentioning the descent range.
* Move task raises a CanError mid-descent -> cleanup still runs, no exception
  escapes from the swallow at ``await move_task``.
* Sensor already tripped at start -> short-circuit returns z0; no IL change,
  no descent task spawned.

The fake backend stubs the driver methods the function actually calls
(``motor_enable``, ``motor_stop``, ``configure_input_logic``,
``motor_get_current_position`` via ``read_input``-style scripts) and replaces
``_motors_move_joint_locked`` with an async sleeper so the test controls
when the descent "finishes". The motion guard is initialised so
``async with self._motion_guard()`` works without a real lock setup race.
"""
import asyncio
import unittest
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from pylabrobot.paa.kx2.arm_backend import KX2ArmBackend
from pylabrobot.paa.kx2.config import Axis
from pylabrobot.paa.kx2.driver import CanError, _InputLogic


class _FakeDriver:
  """Records every method ``find_z_with_proximity_sensor`` calls. Each
  method is async so ``await`` works as in production."""

  def __init__(self) -> None:
    self.motor_enable_calls: List[Tuple[Any, bool, bool]] = []
    self.motor_stop_calls: List[Axis] = []
    self.configure_il_calls: List[Tuple[Any, int, _InputLogic]] = []
    self.motor_stop_should_raise: Optional[Exception] = None
    self.ensure_enabled_calls: List[Tuple[int, ...]] = []
    self.clear_emcy_calls: List[Optional[int]] = []

  async def motor_enable(self, *, node_id: Any, state: bool, use_ds402: bool) -> None:
    self.motor_enable_calls.append((node_id, state, use_ds402))

  async def motors_ensure_enabled(
    self, node_ids: List[int], *, use_ds402: bool = True,
  ) -> None:
    self.ensure_enabled_calls.append(tuple(int(n) for n in node_ids))

  def clear_emcy_state(self, node_id: Optional[int] = None) -> None:
    self.clear_emcy_calls.append(node_id)

  async def motor_stop(self, axis: Axis) -> None:
    self.motor_stop_calls.append(axis)
    if self.motor_stop_should_raise is not None:
      raise self.motor_stop_should_raise

  async def configure_input_logic(
    self, axis: Any, input_num: int, logic: _InputLogic,
  ) -> None:
    self.configure_il_calls.append((axis, input_num, logic))


class _FindZHarness(NamedTuple):
  """Bundle of test handles. Returning a typed tuple keeps mypy happy
  (fake_driver is _FakeDriver, not KX2Driver) without polluting the
  KX2ArmBackend instance with ad-hoc attributes."""
  backend: KX2ArmBackend
  fake_driver: _FakeDriver
  move_calls: List[Dict[str, Any]]


def _build_harness(
  *,
  proximity_script: List[bool],
  z_positions: List[float],
  move_behavior: str = "long_sleep",
  move_exception: Optional[Exception] = None,
) -> _FindZHarness:
  """Construct a KX2ArmBackend wired for find_z testing.

  Args:
    proximity_script: Sequence of bools returned by ``read_proximity_sensor``;
      one popped per call. The last element is sticky once exhausted.
    z_positions: Sequence returned by ``motor_get_current_position`` calls.
      Same exhaust-then-sticky behavior.
    move_behavior: ``"long_sleep"`` (default; awaits ~10 s so the test cancels
      it via the trip path) or ``"completes_immediately"`` (returns as soon
      as the task is scheduled — used for the "sensor never trips" test).
    move_exception: If set, ``_motors_move_joint_locked`` raises this from
      inside the spawned task before it would otherwise sleep.
  """
  backend = KX2ArmBackend.__new__(KX2ArmBackend)
  fake_driver = _FakeDriver()
  backend.driver = fake_driver  # type: ignore[assignment]
  backend._motion_lock = asyncio.Lock()
  backend._motion_owner = None
  backend._gripper_params = None  # type: ignore[assignment]  # not consumed by find_z

  prox = list(proximity_script)
  zs = list(z_positions)

  async def _read_proximity() -> bool:
    # Yield once so any concurrently scheduled task (the descent move) gets
    # a chance to run before we observe the next sensor value. Without this
    # an immediate-True script can race the move task and cancel it before
    # it has a chance to record its call.
    await asyncio.sleep(0)
    val = prox[0] if len(prox) == 1 else prox.pop(0)
    return val

  async def _get_z(axis: Axis) -> float:
    val = zs[0] if len(zs) == 1 else zs.pop(0)
    return float(val)

  move_calls: List[Dict[str, Any]] = []

  async def _fake_move(cmd_pos: Dict[Axis, float], params: Any = None) -> None:
    move_calls.append({"cmd_pos": dict(cmd_pos), "params": params})
    if move_exception is not None:
      raise move_exception
    if move_behavior == "completes_immediately":
      return
    # "long_sleep": stays awaitable until cancelled by find_z's trip handler.
    await asyncio.sleep(10.0)

  backend.read_proximity_sensor = _read_proximity  # type: ignore[assignment]
  backend.motor_get_current_position = _get_z  # type: ignore[assignment]
  backend._motors_move_joint_locked = _fake_move  # type: ignore[assignment]
  return _FindZHarness(backend=backend, fake_driver=fake_driver, move_calls=move_calls)


class FindZSensorTripsCleanupTests(unittest.TestCase):
  def test_trip_cancels_descent_and_runs_cleanup(self):
    """Sensor returns False on the pre-check, then True after one poll
    cycle. Verify: descent task is spawned, cancelled, motor_stop runs,
    IL is restored to GeneralPurpose."""
    h = _build_harness(
      # Pre-check (False), then one poll iteration True (trip).
      proximity_script=[False, True],
      # z0 read after motor_enable; final z read after the task is cancelled.
      z_positions=[100.0, 95.5],
    )

    z = asyncio.run(h.backend.find_z_with_proximity_sensor(max_descent=20.0))

    self.assertAlmostEqual(z, 95.5, places=9)
    # IL was armed once, then restored once.
    arm = (Axis.Z, h.backend._PROXIMITY_SENSOR_INPUT, _InputLogic.StopForward)
    restore = (Axis.Z, h.backend._PROXIMITY_SENSOR_INPUT, _InputLogic.GeneralPurpose)
    self.assertEqual(h.fake_driver.configure_il_calls, [arm, restore])
    # motor_stop ran exactly once on the Z axis.
    self.assertEqual(h.fake_driver.motor_stop_calls, [Axis.Z])
    # Pre-flight ensured Z was enabled before descent.
    self.assertEqual(h.fake_driver.ensure_enabled_calls, [(int(Axis.Z),)])
    # Descent target is z0 - max_descent.
    self.assertEqual(len(h.move_calls), 1)
    self.assertAlmostEqual(h.move_calls[0]["cmd_pos"][Axis.Z], 80.0, places=9)


class FindZSensorNeverTripsTests(unittest.TestCase):
  def test_never_trips_raises_runtime_error_with_range(self):
    """Move completes (no trip), sensor stayed False -> raises with the
    descent range and start/end Z values in the message."""
    h = _build_harness(
      proximity_script=[False],     # always False
      z_positions=[100.0, 79.5],    # z0=100, z_end after descent=79.5
      move_behavior="completes_immediately",
    )

    with self.assertRaises(RuntimeError) as ctx:
      asyncio.run(h.backend.find_z_with_proximity_sensor(max_descent=20.0))

    msg = str(ctx.exception)
    self.assertIn("proximity sensor never tripped", msg)
    self.assertIn("20.0", msg)
    self.assertIn("100.00", msg)
    self.assertIn("79.50", msg)
    # Cleanup still ran.
    self.assertEqual(h.fake_driver.motor_stop_calls, [Axis.Z])
    self.assertIn(
      (Axis.Z, h.backend._PROXIMITY_SENSOR_INPUT, _InputLogic.GeneralPurpose),
      h.fake_driver.configure_il_calls,
    )


class FindZMoveRaisesCanErrorTests(unittest.TestCase):
  def test_canerror_in_descent_is_swallowed_cleanup_runs(self):
    """The descent task raises CanError. ``await move_task`` swallows it
    (per the ``except (asyncio.CancelledError, CanError): pass``). Sensor
    stayed False -> the function then raises the standard RuntimeError
    *after* cleanup. motor_stop + IL restore still ran."""
    h = _build_harness(
      proximity_script=[False],
      z_positions=[100.0, 100.0],
      move_exception=CanError("synthetic descent failure"),
    )

    with self.assertRaises(RuntimeError) as ctx:
      asyncio.run(h.backend.find_z_with_proximity_sensor(max_descent=20.0))

    # The post-cleanup "never tripped" branch runs because the swallow
    # at `await move_task` masked the CanError; sensor stayed False so
    # `tripped` is False.
    self.assertIn("never tripped", str(ctx.exception))
    self.assertEqual(h.fake_driver.motor_stop_calls, [Axis.Z])
    self.assertEqual(
      h.fake_driver.configure_il_calls[-1],
      (Axis.Z, h.backend._PROXIMITY_SENSOR_INPUT, _InputLogic.GeneralPurpose),
    )

  def test_motor_stop_failure_is_logged_not_raised(self):
    """Even if motor_stop fails during cleanup, IL restore still runs and
    the function still completes its happy-path return (sensor tripped)."""
    h = _build_harness(
      proximity_script=[False, True],
      z_positions=[100.0, 95.5],
    )
    h.fake_driver.motor_stop_should_raise = CanError("synthetic stop failure")

    z = asyncio.run(h.backend.find_z_with_proximity_sensor(max_descent=20.0))

    self.assertAlmostEqual(z, 95.5, places=9)
    # IL restore still ran despite motor_stop raising.
    self.assertEqual(
      h.fake_driver.configure_il_calls[-1],
      (Axis.Z, h.backend._PROXIMITY_SENSOR_INPUT, _InputLogic.GeneralPurpose),
    )


class FindZAlreadyTrippedTests(unittest.TestCase):
  def test_already_tripped_short_circuits_no_il_change(self):
    """The pre-check sees the sensor already tripped: returns z0 without
    configuring IL or spawning a descent task."""
    h = _build_harness(
      proximity_script=[True],     # tripped on the very first read
      z_positions=[42.0],
    )

    z = asyncio.run(h.backend.find_z_with_proximity_sensor(max_descent=20.0))

    self.assertAlmostEqual(z, 42.0, places=9)
    # No IL configure calls (neither arm nor restore) — short-circuit hits
    # before the StopForward arm.
    self.assertEqual(h.fake_driver.configure_il_calls, [])
    # No motor_stop, no descent move spawned.
    self.assertEqual(h.fake_driver.motor_stop_calls, [])
    self.assertEqual(h.move_calls, [])
    # ensure_enabled still ran (it's the pre-flight before anything else).
    self.assertEqual(h.fake_driver.ensure_enabled_calls, [(int(Axis.Z),)])

  def test_z_start_provided_runs_pre_descent_move_then_short_circuits(self):
    """Same as above but with z_start: the pre-positioning move runs, then
    the sensor reads tripped on the pre-check -> still no IL arm or
    descent task."""
    h = _build_harness(
      proximity_script=[True],
      z_positions=[55.0],
      # Pre-positioning move must complete on its own (it's awaited inline,
      # not spawned as a cancellable task). long_sleep would deadlock.
      move_behavior="completes_immediately",
    )

    z = asyncio.run(h.backend.find_z_with_proximity_sensor(
      max_descent=20.0, z_start=80.0,
    ))

    self.assertAlmostEqual(z, 55.0, places=9)
    # The pre-position move ran (z_start path), but no descent move.
    self.assertEqual(len(h.move_calls), 1)
    self.assertAlmostEqual(h.move_calls[0]["cmd_pos"][Axis.Z], 80.0, places=9)
    # Still no IL change, still no motor_stop.
    self.assertEqual(h.fake_driver.configure_il_calls, [])
    self.assertEqual(h.fake_driver.motor_stop_calls, [])


if __name__ == "__main__":
  unittest.main()
