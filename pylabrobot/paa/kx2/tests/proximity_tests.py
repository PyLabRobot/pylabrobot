"""Proximity searches join motion and restore input logic on every exit path."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from pylabrobot.paa.kx2 import Axis
from pylabrobot.paa.kx2.protocol import CanError
from pylabrobot.paa.kx2.tests.lifecycle_tests import driver
from pylabrobot.resources import Coordinate


class ProximityTests(unittest.IsolatedAsyncioTestCase):
  """Exercise sensor trips, drive faults, cancellation and cleanup failures."""

  async def test_trip_joins_descent_before_restoring_logic(self) -> None:
    """A trip stops and joins the producer before restoring non-default IL."""
    arm, io = driver()
    await arm.setup()
    io.values[2, "IL", 4] = 23
    joined = asyncio.Event()
    started = asyncio.Event()

    async def move(target, **kwargs) -> None:
      """Only the descent remains in flight until the sensor interrupts it."""
      if target[Axis.Z] == 80:
        started.set()
        try:
          await asyncio.Future()
        finally:
          joined.set()

    async def sensor() -> bool:
      """Trip only after the descent producer has started."""
      return started.is_set()

    arm._motors_move_joint_locked = move  # type: ignore[method-assign,assignment]
    arm.sense_proximity = sensor  # type: ignore[method-assign,assignment]
    arm.request_axis_position = AsyncMock(return_value=95.5)  # type: ignore[method-assign,assignment]
    self.assertEqual(await arm.find_z_with_proximity_sensor(100, 80), 95.5)
    self.assertTrue(joined.is_set())
    self.assertEqual(io.values[2, "IL", 4], 23)
    self.assertIn((0x302, b"ST\x00\x00"), io.frames)
    await arm.stop()

  async def test_drive_error_propagates_and_restores_logic(self) -> None:
    """Unexpected drive failures retain their error instead of becoming a no-trip result."""
    arm, io = driver()
    await arm.setup()
    io.values[2, "IL", 4] = 17
    arm.sense_proximity = AsyncMock(return_value=False)  # type: ignore[method-assign,assignment]
    arm._motors_move_joint_locked = AsyncMock(side_effect=[None, CanError("descent fault")])  # type: ignore[method-assign,assignment]
    with self.assertRaisesRegex(CanError, "descent fault"):
      await arm.find_z_with_proximity_sensor(100, 80)
    self.assertEqual(io.values[2, "IL", 4], 17)
    await arm.stop()

  async def test_cancel_joins_cartesian_sweep(self) -> None:
    """Cancelling the caller cannot leave an unowned sweep emitting motion commands."""
    arm, _ = driver()
    await arm.setup()
    started, joined = asyncio.Event(), asyncio.Event()
    arm.sense_proximity = AsyncMock(return_value=False)  # type: ignore[method-assign,assignment]
    arm.request_gripper_pose = AsyncMock()  # type: ignore[method-assign,assignment]

    async def move(*args, **kwargs) -> None:
      """Block only the second, linear move."""
      if True:
        started.set()
        try:
          await asyncio.Future()
        finally:
          joined.set()

    arm.move_to_location = AsyncMock()  # type: ignore[method-assign,assignment]
    arm._run_linear_path = move  # type: ignore[method-assign,assignment]
    task = asyncio.create_task(
      arm.find_with_proximity_sensor(Coordinate(0, 0, 0), Coordinate(1, 0, 0))
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertTrue(joined.is_set())
    self.assertIsNone(arm._motion_owner)
    await arm.stop()

  async def test_no_trip_reports_search_bounds(self) -> None:
    """Reaching the endpoint without a sensor trip is a failed search."""
    arm, _ = driver()
    await arm.setup()
    arm.sense_proximity = AsyncMock(return_value=False)  # type: ignore[method-assign,assignment]
    arm._motors_move_joint_locked = AsyncMock()  # type: ignore[method-assign,assignment]
    with self.assertRaisesRegex(RuntimeError, "100 to 80"):
      await arm.find_z_with_proximity_sensor(100, 80)
    await arm.stop()

  async def test_stop_failure_is_reported_and_input_restored(self) -> None:
    """Failed stopping is not reported as a successful sensor search."""
    arm, io = driver()
    await arm.setup()
    io.values[2, "IL", 4] = 19
    arm.sense_proximity = AsyncMock(side_effect=[False, True])  # type: ignore[method-assign,assignment]
    arm._motors_move_joint_locked = AsyncMock()  # type: ignore[method-assign,assignment]
    arm._stop_axes = AsyncMock(side_effect=CanError("stop failed"))  # type: ignore[method-assign,assignment]
    with self.assertRaisesRegex(CanError, "stop failed"):
      await arm.find_z_with_proximity_sensor(100, 80)
    self.assertEqual(io.values[2, "IL", 4], 19)
    await arm.stop()
