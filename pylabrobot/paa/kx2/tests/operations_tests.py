"""Device-facing arm, gripper and homing behavior through simulated I/O."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from pylabrobot.paa.kx2 import Axis
from pylabrobot.paa.kx2.protocol import CanError, JointMoveDirection, MotorMoveParam, MotorsMovePlan
from pylabrobot.paa.kx2.tests.lifecycle_tests import driver


class OperationsTests(unittest.IsolatedAsyncioTestCase):
  """Check partial targets, contact evidence, homing and deliberate parking."""

  async def test_estop_requires_both_safety_channels(self) -> None:
    """Either open safety channel blocks enabling a motor."""
    arm, io = driver()
    await arm.setup()
    for status in (0, 0x4000, 0x8000):
      io.values[1, "SR", 0] = status
      self.assertTrue(await arm.request_estop())
      with self.assertRaisesRegex(CanError, "e-stop is active"):
        await arm._motor_enable(Axis.Z, True, use_ds402=True)
    io.values[1, "SR", 0] = 0xC000
    self.assertFalse(await arm.request_estop())
    await arm.stop()

  async def test_stop_halts_canopen_profile_without_changing_enable_state(self) -> None:
    """An interpreter stop alone cannot terminate a CANopen position profile."""
    arm, io = driver()
    await arm.setup()
    original_write = io.write

    async def respond_to_halt(cob_id, data, **kwargs):
      await original_write(cob_id, data, **kwargs)
      if cob_id == 0x202 and int.from_bytes(data, "little") & 0x100:
        io.values[2, "MS", 0] = 1

    io.write = respond_to_halt  # type: ignore[method-assign]
    for controlword in (0x3F, 0x06):
      io.values[2, "MO", 0] = 1
      io.registers[2, 0x6040, 0] = controlword.to_bytes(2, "little")
      io.values[2, "MS", 0] = 2
      await arm._stop_axes([Axis.Z])
      expected = ((controlword | 0x100) & ~0x10).to_bytes(2, "little")
      self.assertIn((0x202, expected), io.frames)
    io.frames.clear()
    io.values[2, "MO", 0], io.values[2, "MS", 0] = 0, 3
    io.registers[2, 0x6040, 0] = b"\x3f\x00"
    await arm._stop_axes([Axis.Z])
    self.assertFalse(any(cob == 0x202 for cob, _ in io.frames))
    await arm.stop()

  async def test_limit_stop_requires_stationary_reference_and_matching_feedback(self) -> None:
    """A latched MS=2 is acceptable only after the reference and feedback settle."""
    arm, io = driver()
    await arm.setup()
    io.values[2, "MS", 0] = 2
    io.values[2, "PX", 0] = io.values[2, "DV", 3] = 50000
    io.values[2, "DV", 5] = 10
    original_query = arm._query_int
    velocity_samples = 0

    async def query(node_id, command, index=0):
      nonlocal velocity_samples
      if command == "DV" and index == 5:
        velocity_samples += 1
        if velocity_samples == 4:
          io.values[2, "DV", 5] = 0
          io.values[2, "PX", 0] = 49900
        if velocity_samples == 8:
          io.values[2, "PX", 0] = 50000
      return await original_query(node_id, command, index)

    arm._query_int = query  # type: ignore[method-assign]
    await arm._stop_axes([Axis.Z])
    self.assertGreater(velocity_samples, 8)
    self.assertEqual(io.values[2, "MS", 0], 2)
    await arm.stop()

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
    io.values[2, "IB", 20] = 1
    io.values[6, "CL", 1], io.values[6, "IQ", 0] = 1.0, 0.95
    io.values[6, "MO", 0] = 1
    io.values[6, "PX", 0] = 20000
    self.assertTrue(await arm.gripper.sense_plate_gripped())
    io.values[6, "MO", 0] = 0
    self.assertFalse(await arm.gripper.sense_plate_gripped())
    io.values[6, "MO", 0] = 1
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
    io.values[6, "PX", 0] = 30000
    arm._start_joint_plan = AsyncMock(side_effect=CanError("blocked"))  # type: ignore[method-assign]
    with self.assertRaisesRegex(CanError, "blocked"):
      await arm.gripper.move_to_jaw_position(20, max_force_percent=50)
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertAlmostEqual(io.values[6, "PL", 1], 0.8)
    await arm.stop()

  async def test_current_during_jaw_motion_is_not_a_grip(self) -> None:
    """High motor current while the jaw is still moving cannot confirm a held plate."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "MO", 0] = 1
    io.values[6, "CL", 1], io.values[6, "IQ", 0] = 1.0, 0.95
    arm.gripper.request_jaw_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[10.0, 10.0, 10.1]
    )
    self.assertFalse(await arm.gripper.sense_plate_gripped())
    await arm.stop()

  async def test_contact_retains_target_and_current_until_release(self) -> None:
    """A grip holds its limited current; opening restores the original limits."""
    arm, io = driver()
    await arm.setup()
    io.values[2, "IB", 20] = 1
    io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
    io.values[6, "PX", 0], io.values[6, "IQ", 0] = 30000, 0.57
    io.values[6, "MO", 0], io.values[6, "MS", 0] = 1, 1
    start = AsyncMock()
    arm._start_joint_plan = start  # type: ignore[method-assign]
    await arm.gripper.move_to_jaw_position(
      20, max_force_percent=50, check_plate_gripped=True, max_speed=1, max_acceleration=2
    )
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.6)
    self.assertAlmostEqual(io.values[6, "PL", 1], 1.2)
    self.assertNotIn((0x306, b"ST\x00\x00"), io.frames)
    self.assertEqual(start.call_args.args[0].moves[0].velocity, 1000)
    self.assertEqual(start.call_args.args[0].moves[0].acceleration, 2000)
    self.assertEqual(arm.configuration.axes[Axis.SERVO_GRIPPER].max_vel, 100)
    io.values[6, "MS", 0] = 0

    async def open_jaws(plan):
      self.assertAlmostEqual(io.values[6, "CL", 1], 1.2)
      io.values[6, "PX", 0] = plan.moves[0].position

    arm._start_joint_plan = open_jaws  # type: ignore[method-assign]
    await arm.gripper.move_to_jaw_position(40, force_sensing=False)
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertAlmostEqual(io.values[6, "PL", 1], 0.8)
    await arm.stop()

  async def test_current_without_plate_detection_is_not_a_grip(self) -> None:
    """Motor resistance alone cannot establish plate presence."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "MO", 0], io.values[6, "PX", 0] = 1, 30000
    io.values[6, "CL", 1], io.values[6, "IQ", 0] = 0.6, 0.59
    self.assertFalse(await arm.gripper.sense_plate_gripped())
    io.values[2, "IB", 20] = 1
    io.values[6, "PX", 0] = 199980
    self.assertFalse(await arm.gripper.sense_plate_gripped())
    await arm.stop()

  async def test_stall_before_closure_is_not_reported_as_a_grip(self) -> None:
    """Insufficient current at the open limit must fail rather than claim contact."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
    io.values[6, "MO", 0], io.values[6, "MS", 0] = 1, 1
    io.values[6, "PX", 0], io.values[6, "IQ", 0] = 199980, 0.24
    io.values[2, "IB", 20] = 1
    arm._start_joint_plan = AsyncMock()  # type: ignore[method-assign]
    with self.assertRaisesRegex(CanError, "before measurable jaw closure"):
      await arm.gripper.move_to_jaw_position(196, max_force_percent=20)
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertIn((0x306, b"ST\x00\x00"), io.frames)
    await arm.stop()

  async def test_release_accepts_settled_feedback_with_profiler_done(self) -> None:
    """MS=1 does not invalidate feedback within the jaw tolerance."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
    io.values[6, "PX", 0], io.values[6, "MO", 0], io.values[6, "MS", 0] = 30000, 1, 1

    async def open_jaws(plan):
      self.assertAlmostEqual(io.values[6, "CL", 1], 1.2)
      io.values[6, "PX", 0] = plan.moves[0].position - 5

    arm._start_joint_plan = open_jaws  # type: ignore[method-assign]
    await arm.gripper.move_to_jaw_position(40, force_sensing=False, max_speed=1)
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertAlmostEqual(io.values[6, "PL", 1], 0.8)
    await arm.stop()

  async def test_cancelled_close_stops_before_restoring_current(self) -> None:
    """Cancelling a closing profile cannot leave a producer or closing target running."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
    io.values[6, "PX", 0], io.values[6, "MO", 0] = 30000, 1
    begun = asyncio.Event()

    async def begin(plan):
      begun.set()

    arm._start_joint_plan = begin  # type: ignore[method-assign]
    task = asyncio.create_task(arm.gripper.move_to_jaw_position(20, max_force_percent=50))
    await begun.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertIn((0x306, b"ST\x00\x00"), io.frames)
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertAlmostEqual(io.values[6, "PL", 1], 0.8)
    await arm.stop()

  async def test_disconnect_restores_current_after_held_grip(self) -> None:
    """The connection owns cleanup for a grip that has not been released."""
    arm, io = driver()
    await arm.setup()
    io.values[2, "IB", 20] = 1
    io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
    io.values[6, "PX", 0], io.values[6, "IQ", 0] = 30000, 0.57
    io.values[6, "MO", 0], io.values[6, "MS", 0] = 1, 1
    arm._start_joint_plan = AsyncMock()  # type: ignore[method-assign]
    await arm.gripper.move_to_jaw_position(20, max_force_percent=50)
    await arm.stop()
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertAlmostEqual(io.values[6, "PL", 1], 0.8)

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

  async def test_jaw_target_after_homing_uses_calibrated_reference(self) -> None:
    """The interpreter homing reference must not carry into the next CANopen move."""
    arm, io = driver()
    await arm.setup()
    needs_reference_reset = False
    original_write = io.write

    async def home(config):
      nonlocal needs_reference_reset
      needs_reference_reset = True
      io.values[6, "PX", 0] = 30000

    async def write(cob_id: int, data: bytes, *, remote: bool = False, dlc: int | None = None):
      nonlocal needs_reference_reset
      if cob_id == 0x306 and data == b"MO\x00\x00\x00\x00\x00\x00":
        needs_reference_reset = False
      await original_write(cob_id, data, remote=remote, dlc=dlc)

    async def move(plan):
      self.assertFalse(needs_reference_reset)
      io.values[6, "PX", 0] = plan.moves[0].position

    io.write = write  # type: ignore[method-assign]
    arm._home_servo_gripper = home  # type: ignore[method-assign]
    arm._start_joint_plan = move  # type: ignore[method-assign]
    await arm.gripper.home()
    await arm.gripper.move_to_jaw_position(29, force_sensing=False, max_speed=1)
    self.assertEqual(await arm.gripper.request_jaw_position(), 29)
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
