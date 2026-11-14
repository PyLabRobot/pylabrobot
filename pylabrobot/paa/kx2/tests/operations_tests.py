"""Device-facing arm, gripper and homing behavior through simulated I/O."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from pylabrobot.paa.kx2 import Axis
from pylabrobot.paa.kx2.protocol import CanError, JointMoveDirection, MotorMoveParam, MotorsMovePlan
from pylabrobot.paa.kx2.tests.lifecycle_tests import driver


class OperationsTests(unittest.IsolatedAsyncioTestCase):
  """Check partial targets, contact evidence, homing and deliberate parking."""

  async def test_point_move_stops_then_restores_jog_end_velocity(self) -> None:
    """A nonzero prior end velocity cannot carry a point move beyond its target."""
    arm, io = driver()
    await arm.setup()
    original = (50000).to_bytes(4, "little")
    io.registers[6, 0x6082, 0] = original

    async def begin(node_ids: list[int], *, relative: bool = False) -> None:
      """Inspect the profile immediately before its start command."""
      self.assertEqual(io.registers[6, 0x6082, 0], bytes(4))

    arm._ppm_begin_motion = begin  # type: ignore[method-assign]
    arm._wait_for_moves_done = AsyncMock()  # type: ignore[method-assign]
    await arm._motors_move_absolute_execute_locked(
      MotorsMovePlan(
        moves=[MotorMoveParam(6, -280, 10000, 10000, False, JointMoveDirection.Normal)]
      )
    )
    self.assertEqual(io.registers[6, 0x6082, 0], original)
    await arm.stop()

  async def test_completion_waits_for_feedback_to_settle(self) -> None:
    """Profiler completion alone does not imply the axis reached its target."""
    arm, io = driver()
    await arm.setup()
    io.values[4, "MO", 0] = 1
    io.values[4, "MS", 0] = 1
    self.assertFalse(await arm._motor_check_if_move_done(4))
    io.values[4, "MS", 0] = 0
    self.assertTrue(await arm._motor_check_if_move_done(4))
    io.values[4, "MS", 0], io.values[4, "MO", 0] = 3, 0
    with self.assertRaisesRegex(RuntimeError, "Motor Fault"):
      await arm._motor_check_if_move_done(4)
    await arm.stop()

  async def test_partial_target_and_position_only_close_guard(self) -> None:
    """A partial arm move preserves its keys and a jaw close needs explicit consent."""
    arm, _ = driver()
    await arm.setup()
    arm.request_joint_state = AsyncMock(  # type: ignore[method-assign]
      return_value={
        Axis.SHOULDER: 0,
        Axis.Z: 50,
        Axis.ELBOW: 20,
        Axis.WRIST: 0,
        Axis.SERVO_GRIPPER: 30,
      }
    )  # type: ignore[method-assign]
    move = AsyncMock()
    arm._motors_move_joint_locked = move  # type: ignore[method-assign]
    await arm.move_to_joint_state({Axis.Z: 60})
    self.assertEqual(move.call_args.args[0], {Axis.Z: 60})
    with self.assertRaisesRegex(ValueError, "Close the jaws"):
      await arm.move_to_joint_state({Axis.SERVO_GRIPPER: 20})
    await arm.move_to_joint_state(
      {Axis.SERVO_GRIPPER: 20}, close_gripper_without_force_sensing=True
    )
    await arm.stop()

  async def test_grip_detection_uses_current_fraction_and_closed_limit(self) -> None:
    """High current on a stationary jaw away from the hard stop counts as contact."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "CL", 1], io.values[6, "IQ", 0] = 1.0, 0.95
    io.values[6, "PX", 0] = 20000
    self.assertTrue(await arm.gripper.sense_plate_gripped())
    io.values[6, "IQ", 0] = 0.1
    self.assertFalse(await arm.gripper.sense_plate_gripped())
    io.values[6, "IQ", 0] = 0.95
    io.values[6, "PX", 0] = -200000
    self.assertFalse(await arm.gripper.sense_plate_gripped())
    await arm.stop()

  async def test_gripper_force_is_scoped_on_failure(self) -> None:
    """A rejected jaw move restores the current limits it found."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "PL", 1], io.values[6, "CL", 1] = 0.8, 0.4
    arm._motors_move_joint_locked = AsyncMock(side_effect=CanError("blocked"))  # type: ignore[method-assign]
    with self.assertRaisesRegex(CanError, "blocked"):
      await arm.gripper.move_to_jaw_position(20, max_force_percent=50)
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertAlmostEqual(io.values[6, "PL", 1], 0.8)
    await arm.stop()

  async def test_contact_stops_and_joins_closing_motion(self) -> None:
    """Sustained current ends a close before its unreachable position target."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
    io.values[6, "PX", 0], io.values[6, "IQ", 0] = 30000, 0.57
    joined = asyncio.Event()

    async def move(*args, **kwargs) -> None:
      """Represent a drive waiting to reach a target behind the plate."""
      try:
        await asyncio.Future()
      finally:
        joined.set()

    arm._motors_move_joint_locked = move  # type: ignore[method-assign]
    await arm.gripper.move_to_jaw_position(20, max_force_percent=50, check_plate_gripped=True)
    self.assertTrue(joined.is_set())
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertIn((0x306, b"ST\x00\x00"), io.frames)
    await arm.stop()

  async def test_hard_stop_uses_jog_begin_error_query_and_stop(self) -> None:
    """Homing decisions run in Python, with a stop after the observed hard-stop error."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "PE", 0] = 600
    await arm._gripper_hard_stop_search(1000, 2000, 100, 500, 1)
    commands = [frame[:2] for cob, frame in io.frames if cob == 0x306]
    self.assertLess(commands.index(b"JP"), commands.index(b"BG"))
    self.assertLess(commands.index(b"BG"), commands.index(b"PE"))
    self.assertLess(commands.index(b"PE"), commands.index(b"ST"))
    self.assertNotIn(b"JV", commands)
    datum = [
      frame
      for cob, frame in io.frames
      if cob == 0x306 and frame[:3] == b"HM\x02" and not frame[3] & 0x40
    ]
    self.assertEqual(datum[-1][4:], b"\x00\x00\x00\x00")
    self.assertNotIn(b"XQ", commands)
    await arm.stop()

  async def test_park_requires_calibrated_explicit_target(self) -> None:
    """There is no universal parking pose across different KX2 installations."""
    arm, _ = driver()
    await arm.setup()
    with self.assertRaisesRegex(ValueError, "parking_position"):
      await arm.park()
    with self.assertRaises(ValueError):
      arm.parking_position = {Axis.Z: 1000}
    target = {Axis.Z: 100.0}
    arm.parking_position = target
    target[Axis.Z] = 999
    self.assertEqual(arm.parking_position, {Axis.Z: 100})
    await arm.stop()
