"""Device-facing arm, gripper and homing behavior through simulated I/O."""

from __future__ import annotations

from unittest.mock import AsyncMock

import anyio
import pytest

from pylabrobot.paa.kx2 import Axis
from pylabrobot.paa.kx2.config import ServoGripperConfig
from pylabrobot.paa.kx2.protocol import CanError, JointMoveDirection, MotorMoveParam, MotorsMovePlan
from pylabrobot.paa.kx2.tests.lifecycle_tests import driver
from pylabrobot.testing import AnyioTestBase


class TestOperations(AnyioTestBase):
  """Check partial targets, contact evidence, homing and deliberate parking."""

  async def test_estop_requires_both_safety_channels(self) -> None:
    """Either open safety channel blocks enabling a motor."""
    arm, io = driver()
    async with arm:
      for status in (0, 0x4000, 0x8000):
        io.values[1, "SR", 0] = status
        assert await arm.request_estop()
        with pytest.raises(CanError, match="e-stop is active"):
          await arm._motor_enable(Axis.Z, True, use_ds402=True)
      io.values[1, "SR", 0] = 0xC000
      assert not await arm.request_estop()

  async def test_stop_halts_canopen_profile_without_changing_enable_state(self) -> None:
    """An interpreter stop alone cannot terminate a CANopen position profile."""
    arm, io = driver()
    async with arm:
      original_write = io.write

      async def respond_to_halt(
        cob_id: int,
        data: bytes,
        *,
        remote: bool = False,
        dlc: int | None = None,
      ) -> None:
        await original_write(cob_id, data, remote=remote, dlc=dlc)
        if cob_id == 0x202 and int.from_bytes(data, "little") & 0x100:
          io.values[2, "MS", 0] = 1

      io.write = respond_to_halt  # type: ignore[method-assign]
      for controlword in (0x3F, 0x06):
        io.values[2, "MO", 0] = 1
        io.registers[2, 0x6040, 0] = controlword.to_bytes(2, "little")
        io.values[2, "MS", 0] = 2
        await arm._stop_axes([Axis.Z])
        expected = ((controlword | 0x100) & ~0x10).to_bytes(2, "little")
        assert (0x202, expected) in io.frames
      io.frames.clear()
      io.values[2, "MO", 0], io.values[2, "MS", 0] = 0, 3
      io.registers[2, 0x6040, 0] = b"\x3f\x00"
      await arm._stop_axes([Axis.Z])
      assert not any(cob == 0x202 for cob, _ in io.frames)

  async def test_limit_stop_requires_stationary_reference_and_matching_feedback(self) -> None:
    """A latched MS=2 is acceptable only after the reference and feedback settle."""
    arm, io = driver()
    async with arm:
      io.values[2, "MS", 0] = 2
      io.values[2, "PX", 0] = io.values[2, "DV", 3] = 50000
      io.values[2, "DV", 5] = 10
      original_query = arm._query_int
      velocity_samples = 0

      async def query(node_id: int, cmd: str, cmd_index: int) -> int:
        nonlocal velocity_samples
        if cmd == "DV" and cmd_index == 5:
          velocity_samples += 1
          if velocity_samples == 4:
            io.values[2, "DV", 5] = 0
            io.values[2, "PX", 0] = 49900
          if velocity_samples == 8:
            io.values[2, "PX", 0] = 50000
        return await original_query(node_id, cmd, cmd_index)

      arm._query_int = query  # type: ignore[method-assign]
      await arm._stop_axes([Axis.Z])
      assert velocity_samples > 8
      assert io.values[2, "MS", 0] == 2

  async def test_point_move_stops_then_restores_jog_end_velocity(self) -> None:
    """A nonzero prior end velocity cannot carry a point move beyond its target."""
    arm, io = driver()
    async with arm:
      original = (50000).to_bytes(4, "little")
      io.registers[6, 0x6082, 0] = original

      async def begin(node_ids: list[int], *, relative: bool = False) -> None:
        """Inspect the profile immediately before its start command."""
        del node_ids, relative
        assert io.registers[6, 0x6082, 0] == bytes(4)

      arm._ppm_begin_motion = begin  # type: ignore[method-assign]
      arm._wait_for_moves_done = AsyncMock()  # type: ignore[method-assign]
      await arm._motors_move_absolute_execute_locked(
        MotorsMovePlan(
          moves=[MotorMoveParam(6, -280, 10000, 10000, False, JointMoveDirection.Normal)]
        )
      )
      assert io.registers[6, 0x6082, 0] == original

  async def test_completion_waits_for_feedback_to_settle(self) -> None:
    """Profiler completion alone does not imply the axis reached its target."""
    arm, io = driver()
    async with arm:
      io.values[4, "MO", 0] = 1
      io.values[4, "MS", 0] = 1
      assert not await arm._motor_check_if_move_done(4)
      io.values[4, "MS", 0] = 0
      assert await arm._motor_check_if_move_done(4)
      io.values[4, "MS", 0], io.values[4, "MO", 0] = 3, 0
      with pytest.raises(RuntimeError, match="Motor Fault"):
        await arm._motor_check_if_move_done(4)

  async def test_partial_target_and_position_only_close_guard(self) -> None:
    """A partial arm move preserves its keys and a jaw close needs explicit consent."""
    arm, _ = driver()
    async with arm:
      arm.request_joint_state = AsyncMock(  # type: ignore[method-assign]
        return_value={
          Axis.SHOULDER: 0,
          Axis.Z: 50,
          Axis.ELBOW: 20,
          Axis.WRIST: 0,
          Axis.SERVO_GRIPPER: 30,
        }
      )
      move = AsyncMock()
      arm._motors_move_joint_locked = move  # type: ignore[method-assign]
      await arm.move_to_joint_state({Axis.Z: 60})
      assert move.call_args.args[0] == {Axis.Z: 60}
      with pytest.raises(ValueError, match="Close the jaws"):
        await arm.move_to_joint_state({Axis.SERVO_GRIPPER: 20})
      await arm.move_to_joint_state(
        {Axis.SERVO_GRIPPER: 20}, close_gripper_without_force_sensing=True
      )

  async def test_grip_detection_uses_current_fraction_and_closed_limit(self) -> None:
    """High current on a stationary jaw away from the hard stop counts as contact."""
    arm, io = driver()
    async with arm:
      io.values[2, "IB", 20] = 1
      io.values[6, "CL", 1], io.values[6, "IQ", 0] = 1.0, 0.95
      io.values[6, "MO", 0] = 1
      io.values[6, "PX", 0] = 20000
      assert await arm.gripper.sense_plate_gripped()
      io.values[6, "MO", 0] = 0
      assert not await arm.gripper.sense_plate_gripped()
      io.values[6, "MO", 0] = 1
      io.values[6, "IQ", 0] = 0.1
      assert not await arm.gripper.sense_plate_gripped()
      io.values[6, "IQ", 0] = 0.95
      io.values[6, "PX", 0] = -200000
      assert not await arm.gripper.sense_plate_gripped()

  async def test_gripper_force_is_scoped_on_failure(self) -> None:
    """A rejected jaw move restores the current limits it found."""
    arm, io = driver()
    async with arm:
      io.values[6, "PL", 1], io.values[6, "CL", 1] = 0.8, 0.4
      io.values[6, "PX", 0] = 30000
      arm._start_joint_plan = AsyncMock(side_effect=CanError("blocked"))  # type: ignore[method-assign]
      with pytest.raises(CanError, match="blocked"):
        await arm.gripper.move_to_jaw_position(20, max_force_percent=50)
      assert io.values[6, "CL", 1] == pytest.approx(0.4)
      assert io.values[6, "PL", 1] == pytest.approx(0.8)

  async def test_current_during_jaw_motion_is_not_a_grip(self) -> None:
    """High motor current while the jaw is still moving cannot confirm a held plate."""
    arm, io = driver()
    async with arm:
      io.values[6, "MO", 0] = 1
      io.values[6, "CL", 1], io.values[6, "IQ", 0] = 1.0, 0.95
      arm.gripper.request_jaw_position = AsyncMock(  # type: ignore[method-assign]
        side_effect=[10.0, 10.0, 10.1]
      )
      assert not await arm.gripper.sense_plate_gripped()

  async def test_contact_retains_target_and_current_until_release(self) -> None:
    """A grip holds its limited current; opening restores the original limits."""
    arm, io = driver()
    async with arm:
      io.values[2, "IB", 20] = 1
      io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
      io.values[6, "PX", 0], io.values[6, "IQ", 0] = 30000, 0.57
      io.values[6, "MO", 0], io.values[6, "MS", 0] = 1, 1
      start = AsyncMock()
      arm._start_joint_plan = start  # type: ignore[method-assign]
      await arm.gripper.move_to_jaw_position(
        20, max_force_percent=50, check_plate_gripped=True, max_speed=1, max_acceleration=2
      )
      assert io.values[6, "CL", 1] == pytest.approx(0.6)
      assert io.values[6, "PL", 1] == pytest.approx(1.2)
      assert (0x306, b"ST\x00\x00") not in io.frames
      assert start.call_args.args[0].moves[0].velocity == 1000
      assert start.call_args.args[0].moves[0].acceleration == 2000
      assert arm.configuration.axes[Axis.SERVO_GRIPPER].max_vel == 100
      io.values[6, "MS", 0] = 0

      async def open_jaws(plan: MotorsMovePlan) -> None:
        assert io.values[6, "CL", 1] == pytest.approx(1.2)
        io.values[6, "PX", 0] = plan.moves[0].position

      arm._start_joint_plan = open_jaws  # type: ignore[method-assign]
      await arm.gripper.move_to_jaw_position(40, force_sensing=False)
      assert io.values[6, "CL", 1] == pytest.approx(0.4)
      assert io.values[6, "PL", 1] == pytest.approx(0.8)

  async def test_current_without_plate_detection_is_not_a_grip(self) -> None:
    """Motor resistance alone cannot establish plate presence."""
    arm, io = driver()
    async with arm:
      io.values[6, "MO", 0], io.values[6, "PX", 0] = 1, 30000
      io.values[6, "CL", 1], io.values[6, "IQ", 0] = 0.6, 0.59
      assert not await arm.gripper.sense_plate_gripped()
      io.values[2, "IB", 20] = 1
      io.values[6, "PX", 0] = 199980
      assert not await arm.gripper.sense_plate_gripped()

  async def test_stall_before_closure_is_not_reported_as_a_grip(self) -> None:
    """Insufficient current at the open limit must fail rather than claim contact."""
    arm, io = driver()
    async with arm:
      io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
      io.values[6, "MO", 0], io.values[6, "MS", 0] = 1, 1
      io.values[6, "PX", 0], io.values[6, "IQ", 0] = 199980, 0.24
      io.values[2, "IB", 20] = 1
      arm._start_joint_plan = AsyncMock()  # type: ignore[method-assign]
      with pytest.raises(CanError, match="before measurable jaw closure"):
        await arm.gripper.move_to_jaw_position(196, max_force_percent=20)
      assert io.values[6, "CL", 1] == pytest.approx(0.4)
      assert (0x306, b"ST\x00\x00") in io.frames

  async def test_release_accepts_settled_feedback_with_profiler_done(self) -> None:
    """MS=1 does not invalidate feedback within the jaw tolerance."""
    arm, io = driver()
    async with arm:
      io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
      io.values[6, "PX", 0], io.values[6, "MO", 0], io.values[6, "MS", 0] = 30000, 1, 1

      async def open_jaws(plan: MotorsMovePlan) -> None:
        assert io.values[6, "CL", 1] == pytest.approx(1.2)
        io.values[6, "PX", 0] = plan.moves[0].position - 5

      arm._start_joint_plan = open_jaws  # type: ignore[method-assign]
      await arm.gripper.move_to_jaw_position(40, force_sensing=False, max_speed=1)
      assert io.values[6, "CL", 1] == pytest.approx(0.4)
      assert io.values[6, "PL", 1] == pytest.approx(0.8)

  async def test_cancelled_close_stops_before_restoring_current(self) -> None:
    """Cancelling a closing profile cannot leave a producer or closing target running."""
    arm, io = driver()
    async with arm:
      io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
      io.values[6, "PX", 0], io.values[6, "MO", 0] = 30000, 1
      begun = anyio.Event()
      cancel_scope = anyio.CancelScope()

      async def begin(plan: MotorsMovePlan) -> None:
        del plan
        begun.set()

      async def run_close() -> None:
        with cancel_scope:
          await arm.gripper.move_to_jaw_position(20, max_force_percent=50)

      arm._start_joint_plan = begin  # type: ignore[method-assign]
      with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_close)
          await begun.wait()
          cancel_scope.cancel()
      assert cancel_scope.cancelled_caught
      assert (0x306, b"ST\x00\x00") in io.frames
      assert io.values[6, "CL", 1] == pytest.approx(0.4)
      assert io.values[6, "PL", 1] == pytest.approx(0.8)

  async def test_disconnect_restores_current_after_held_grip(self) -> None:
    """The connection owns cleanup for a grip that has not been released."""
    arm, io = driver()
    async with arm:
      io.values[2, "IB", 20] = 1
      io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
      io.values[6, "PX", 0], io.values[6, "IQ", 0] = 30000, 0.57
      io.values[6, "MO", 0], io.values[6, "MS", 0] = 1, 1
      arm._start_joint_plan = AsyncMock()  # type: ignore[method-assign]
      await arm.gripper.move_to_jaw_position(20, max_force_percent=50)
    assert io.values[6, "CL", 1] == pytest.approx(0.4)
    assert io.values[6, "PL", 1] == pytest.approx(0.8)

  async def test_hard_stop_uses_jog_begin_error_query_and_stop(self) -> None:
    """Homing decisions run in Python, with a stop after the observed hard-stop error."""
    arm, io = driver()
    async with arm:
      io.values[6, "PE", 0] = 600
      await arm._gripper_hard_stop_search(1000, 2000, 100, 500, 1)
      commands = [frame[:2] for cob, frame in io.frames if cob == 0x306]
      assert commands.index(b"JP") < commands.index(b"BG")
      assert commands.index(b"BG") < commands.index(b"PE")
      assert commands.index(b"PE") < commands.index(b"ST")
      assert b"JV" not in commands
      datum = [
        frame
        for cob, frame in io.frames
        if cob == 0x306 and frame[:3] == b"HM\x02" and not frame[3] & 0x40
      ]
      assert datum[-1][4:] == b"\x00\x00\x00\x00"
      assert b"XQ" not in commands

  async def test_jaw_target_after_homing_uses_calibrated_reference(self) -> None:
    """The interpreter homing reference must not carry into the next CANopen move."""
    arm, io = driver()
    async with arm:
      needs_reference_reset = False
      original_write = io.write

      async def home(sgc: ServoGripperConfig) -> None:
        nonlocal needs_reference_reset
        del sgc
        needs_reference_reset = True
        io.values[6, "PX", 0] = 30000

      async def write(
        cob_id: int,
        data: bytes,
        *,
        remote: bool = False,
        dlc: int | None = None,
      ) -> None:
        nonlocal needs_reference_reset
        if cob_id == 0x306 and data == b"MO\x00\x00\x00\x00\x00\x00":
          needs_reference_reset = False
        await original_write(cob_id, data, remote=remote, dlc=dlc)

      async def move(plan: MotorsMovePlan) -> None:
        assert not needs_reference_reset
        io.values[6, "PX", 0] = plan.moves[0].position

      io.write = write  # type: ignore[method-assign]
      arm._home_servo_gripper = home  # type: ignore[method-assign]
      arm._start_joint_plan = move  # type: ignore[method-assign]
      await arm.gripper.home()
      await arm.gripper.move_to_jaw_position(29, force_sensing=False, max_speed=1)
      assert await arm.gripper.request_jaw_position() == 29

  async def test_park_requires_calibrated_explicit_target(self) -> None:
    """There is no universal parking pose across different KX2 installations."""
    arm, _ = driver()
    async with arm:
      with pytest.raises(ValueError, match="parking_position"):
        await arm.park()
      with pytest.raises(ValueError):
        arm.parking_position = {Axis.Z: 1000}
      target = {Axis.Z: 100.0}
      arm.parking_position = target
      target[Axis.Z] = 999
      assert arm.parking_position == {Axis.Z: 100}

  async def test_pick_up_at_location_reenters_motion_guard_on_same_task(self) -> None:
    """Compound Cartesian pickup re-enters the motion guard for arm and jaw suboperations."""
    arm, io = driver()
    async with arm:
      for node in arm.motion_node_ids:
        io.values[node, "MO", 0] = 1
        io.values[node, "MS", 0] = 0
      io.values[2, "IB", 20] = 1
      io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
      io.values[6, "PX", 0], io.values[6, "IQ", 0] = 30000, 0.57
      io.values[6, "MO", 0], io.values[6, "MS", 0] = 1, 1
      started_plans: list[MotorsMovePlan] = []

      async def start_plan(plan: MotorsMovePlan) -> None:
        started_plans.append(plan)
        for move in plan.moves:
          if move.node_id != int(Axis.SERVO_GRIPPER):
            io.values[move.node_id, "PX", 0] = move.position

      arm._start_joint_plan = start_plan  # type: ignore[method-assign]
      pose = await arm.request_gripper_pose()
      target_location = pose.location + type(pose.location)(0.0, 0.0, 10.0)
      await arm.pick_up_at_location(
        target_location,
        pose.rotation.z,
        20.0,
        max_force_percent=50,
      )
      assert len(started_plans) == 2
      assert io.values[6, "CL", 1] == pytest.approx(0.6)

  async def test_cancelled_joint_move_sends_stop_over_can(self) -> None:
    """Cancelling a guarded joint move sends shielded axis stops through the BI lock."""
    arm, io = driver()
    async with arm:
      io.values[2, "MO", 0] = 1
      io.values[2, "MS", 0] = 0
      begun = anyio.Event()
      cancel_scope = anyio.CancelScope()

      async def begin(plan: MotorsMovePlan) -> None:
        del plan
        begun.set()

      async def wait_forever(node_ids: list[int], timeout: float) -> None:
        del node_ids, timeout
        await anyio.sleep_forever()

      arm._start_joint_plan = begin  # type: ignore[method-assign]
      arm._wait_for_moves_done = wait_forever  # type: ignore[method-assign]

      async def run_move() -> None:
        with cancel_scope:
          await arm.move_to_joint_state({Axis.Z: 60.0})

      with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_move)
          await begun.wait()
          io.frames.clear()
          cancel_scope.cancel()
      assert cancel_scope.cancelled_caught
      assert (0x302, b"ST\x00\x00") in io.frames

  async def test_halt_cancels_active_motion_and_sends_stops(self) -> None:
    """Calling halt() cancels an in-flight guarded motion before natural completion."""
    arm, io = driver()
    async with arm:
      for node in arm.motion_node_ids:
        io.values[node, "MO", 0] = 1
        io.values[node, "MS", 0] = 0
      begun = anyio.Event()

      async def begin(plan: MotorsMovePlan) -> None:
        del plan
        begun.set()

      async def wait_done(node_ids: list[int], timeout: float) -> None:
        del node_ids, timeout
        await anyio.sleep(0.3)

      arm._start_joint_plan = begin  # type: ignore[method-assign]
      arm._wait_for_moves_done = wait_done  # type: ignore[method-assign]

      async def run_move() -> None:
        with pytest.raises(CanError, match="interrupted by shutdown or halt"):
          await arm.move_to_joint_state({Axis.Z: 60.0})

      with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_move)
          await begun.wait()
          io.frames.clear()
          await arm.halt()
      for cob in (0x301, 0x302, 0x303, 0x304):
        assert (cob, b"ST\x00\x00") in io.frames
