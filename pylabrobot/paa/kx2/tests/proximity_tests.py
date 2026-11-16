"""Proximity searches join motion and restore input logic on every exit path."""

from typing import Dict
from unittest.mock import AsyncMock

import anyio
import pytest

from pylabrobot.paa.kx2 import Axis
from pylabrobot.paa.kx2.kinematics import CartesianPose
from pylabrobot.paa.kx2.protocol import CanError
from pylabrobot.paa.kx2.tests.lifecycle_tests import driver
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.testing import AnyioTestBase


class TestProximity(AnyioTestBase):
  """Exercise sensor trips, drive faults, cancellation and cleanup failures."""

  async def test_trip_joins_descent_before_restoring_logic(self) -> None:
    """A trip stops and joins the producer before restoring non-default IL."""
    arm, io = driver()
    async with arm:
      io.values[2, "IL", 4] = 23
      joined = anyio.Event()
      started = anyio.Event()

      async def move(
        cmd_pos: Dict[Axis, float],
        *,
        max_gripper_speed: float | None = None,
        max_gripper_acceleration: float | None = None,
      ) -> None:
        """Only the descent remains in flight until the sensor interrupts it."""
        del max_gripper_speed, max_gripper_acceleration
        if cmd_pos[Axis.Z] == 80:
          assert io.values[2, "IL", 4] == 8
          started.set()
          try:
            await anyio.sleep_forever()
          finally:
            joined.set()

      async def sensor() -> bool:
        """Trip only after the descent producer has started."""
        return started.is_set()

      arm._motors_move_joint_locked = move  # type: ignore[method-assign]
      arm.sense_proximity = sensor  # type: ignore[method-assign]
      arm.request_axis_position = AsyncMock(return_value=95.5)  # type: ignore[method-assign]
      with anyio.fail_after(5):
        assert await arm.find_z_with_proximity_sensor(100, 80) == 95.5
      assert joined.is_set()
      assert io.values[2, "IL", 4] == 23
      assert (0x302, b"ST\x00\x00") in io.frames

  async def test_drive_error_propagates_and_restores_logic(self) -> None:
    """Unexpected drive failures retain their error instead of becoming a no-trip result."""
    arm, io = driver()
    async with arm:
      io.values[2, "IL", 4] = 17
      arm.sense_proximity = AsyncMock(return_value=False)  # type: ignore[method-assign]
      arm._motors_move_joint_locked = AsyncMock(  # type: ignore[method-assign]
        side_effect=[None, CanError("descent fault")]
      )
      with pytest.raises(CanError, match="descent fault"):
        await arm.find_z_with_proximity_sensor(100, 80)
      assert io.values[2, "IL", 4] == 17

  async def test_cancel_joins_cartesian_sweep(self) -> None:
    """Cancelling the caller cannot leave an unowned sweep emitting motion commands."""
    arm, _ = driver()
    async with arm:
      started, joined = anyio.Event(), anyio.Event()
      arm.sense_proximity = AsyncMock(return_value=False)  # type: ignore[method-assign]
      arm.request_gripper_pose = AsyncMock(  # type: ignore[method-assign]
        return_value=CartesianPose(Coordinate(0, 0, 0), Rotation(z=0))
      )

      async def move(*args: object, **kwargs: object) -> None:
        """Block only the second, linear move."""
        del args, kwargs
        started.set()
        try:
          await anyio.sleep_forever()
        finally:
          joined.set()

      arm.move_to_location = AsyncMock()  # type: ignore[method-assign]
      arm._run_linear_path = move  # type: ignore[method-assign]
      cancel_scope = anyio.CancelScope()

      async def run_sweep() -> None:
        with cancel_scope:
          await arm.find_with_proximity_sensor(Coordinate(0, 0, 0), Coordinate(1, 0, 0))

      with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_sweep)
          await started.wait()
          cancel_scope.cancel()
      assert cancel_scope.cancelled_caught
      assert joined.is_set()
      assert arm._active_motion is None

  async def test_no_trip_reports_search_bounds(self) -> None:
    """Reaching the endpoint without a sensor trip is a failed search."""
    arm, _ = driver()
    async with arm:
      arm.sense_proximity = AsyncMock(return_value=False)  # type: ignore[method-assign]
      arm._motors_move_joint_locked = AsyncMock()  # type: ignore[method-assign]
      with pytest.raises(RuntimeError, match="100 to 80"):
        await arm.find_z_with_proximity_sensor(100, 80)

  async def test_stop_failure_is_reported_and_input_restored(self) -> None:
    """Failed stopping is not reported as a successful sensor search."""
    arm, io = driver()
    async with arm:
      io.values[2, "IL", 4] = 19
      arm.sense_proximity = AsyncMock(side_effect=[False, True])  # type: ignore[method-assign]
      arm._motors_move_joint_locked = AsyncMock()  # type: ignore[method-assign]
      arm._stop_axes = AsyncMock(side_effect=CanError("stop failed"))  # type: ignore[method-assign]
      with pytest.raises(CanError, match="stop failed"):
        await arm.find_z_with_proximity_sensor(100, 80)
      assert io.values[2, "IL", 4] == 19
