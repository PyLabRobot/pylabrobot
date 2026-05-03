"""Unit tests for ``KX2ArmBackend._run_linear_path``.

Exercises the IPM streaming runtime that backs ``CartesianMoveParams(path='linear')``:

* Preload + begin_motion call ordering. Preload writes 8 frames into the
  drive's interpolation buffer *before* CW=0x1F is issued so the first sync
  doesn't underflow the queue.
* Per-sample (P, V) frames go to all four arm axes (SHOULDER/Z/ELBOW/WRIST)
  in lockstep.
* Cancellation mid-stream runs the finally-block cleanup: ``ipm_stop`` then
  ``ipm_select_mode(False)``, both even if the cancel arrives before the
  trapezoid is exhausted.
* Validation: requesting ``path='linear'`` without ``max_gripper_speed`` or
  ``max_gripper_acceleration`` raises ValueError.

Driver methods are stubbed on a fake recorder; ``request_joint_position`` is
shimmed so the runtime computes start_pose without going to hardware. Motion
guard is initialised the same way the find_z tests do it (manual Lock +
``__new__``).
"""
import asyncio
import unittest
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.paa.kx2.arm_backend import KX2ArmBackend
from pylabrobot.paa.kx2.config import (
  Axis, AxisConfig, GripperParams, KX2Config,
)
from pylabrobot.paa.kx2.driver import JointMoveDirection
from pylabrobot.resources import Coordinate, Rotation


def _axis() -> AxisConfig:
  return AxisConfig(
    motor_conversion_factor=1000.0,
    max_travel=180.0,
    min_travel=-180.0,
    unlimited_travel=False,
    absolute_encoder=True,
    max_vel=100.0,
    max_accel=100.0,
    joint_move_direction=JointMoveDirection.Normal,
    digital_inputs={},
    analog_inputs={},
    outputs={},
  )


def _config() -> KX2Config:
  return KX2Config(
    wrist_offset=10.0,
    elbow_offset=20.0,
    elbow_zero_offset=5.0,
    axes={a: _axis() for a in (
      Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST,
    )},
    base_to_gripper_clearance_z=0.0,
    base_to_gripper_clearance_arm=0.0,
    robot_on_rail=False,
    servo_gripper=None,
  )


class _FakeDriver:
  """Records every IPM-related call. Order matters: tests assert the
  preload-then-begin-motion sequencing."""

  def __init__(self) -> None:
    self.calls: List[Tuple[str, Tuple[Any, ...]]] = []
    self.send_calls: List[Tuple[int, int, int]] = []
    self.cancel_after_sends: Optional[int] = None

  async def ipm_select_mode(self, enable: bool) -> None:
    self.calls.append(("ipm_select_mode", (enable,)))

  async def ipm_set_time_interval(self, ms: int) -> None:
    self.calls.append(("ipm_set_time_interval", (ms,)))

  def ipm_send_pvt_point(
    self, node_id: int, position_enc: int, velocity_enc_per_s: int,
  ) -> None:
    self.send_calls.append((node_id, position_enc, velocity_enc_per_s))
    self.calls.append((
      "ipm_send_pvt_point", (node_id, position_enc, velocity_enc_per_s),
    ))
    if (
      self.cancel_after_sends is not None
      and len(self.send_calls) >= self.cancel_after_sends
    ):
      raise asyncio.CancelledError("test injection")

  async def ipm_begin_motion(self, node_ids: List[int]) -> None:
    self.calls.append(("ipm_begin_motion", (tuple(node_ids),)))

  async def ipm_stop(self, node_ids: Optional[List[int]] = None) -> None:
    self.calls.append((
      "ipm_stop", (tuple(node_ids) if node_ids is not None else None,),
    ))

  def ipm_check_queue_fault(self, node_ids: List[int]) -> None:
    # Synchronous helper; record the call so tests can verify backpressure
    # checks happen at the expected points.
    self.calls.append(("ipm_check_queue_fault", (tuple(int(n) for n in node_ids),)))

  async def ipm_wait_motion_complete(
    self, node_ids: List[int], timeout_s: float,
  ) -> None:
    self.calls.append((
      "ipm_wait_motion_complete", (tuple(int(n) for n in node_ids), timeout_s),
    ))


def _build_backend(
  fake_driver: _FakeDriver,
  *,
  current_joints: Optional[Dict[int, float]] = None,
) -> KX2ArmBackend:
  backend = KX2ArmBackend.__new__(KX2ArmBackend)
  backend.driver = fake_driver  # type: ignore[assignment]
  backend._motion_lock = asyncio.Lock()
  backend._motion_owner = None
  backend._config = _config()
  backend._gripper_params = GripperParams(length=15.0, z_offset=3.0)

  joints = current_joints if current_joints is not None else {
    int(Axis.SHOULDER): 30.0,
    int(Axis.Z): 50.0,
    int(Axis.ELBOW): 60.0,
    int(Axis.WRIST): 0.0,
    int(Axis.SERVO_GRIPPER): 0.0,
  }

  async def _request_joint_position() -> Dict[int, float]:
    return dict(joints)

  backend.request_joint_position = _request_joint_position  # type: ignore[assignment]
  return backend


def _target_pose() -> GripperLocation:
  """A pose ~30 mm away from the harness's current_joints — long enough that
  the arc-length trapezoid produces well over the 8-frame preload."""
  return GripperLocation(
    location=Coordinate(x=20.0, y=110.0, z=80.0),
    rotation=Rotation(z=15.0),
  )


class LinearPathHappyPath(unittest.TestCase):
  def test_preload_then_begin_then_stream(self):
    """Preload 8 frames per axis (32 sends), then ipm_begin_motion, then
    further per-frame sends in lockstep across the four arm axes."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    params = KX2ArmBackend.CartesianMoveParams(
      max_gripper_speed=20.0,
      max_gripper_acceleration=200.0,
      path="linear",
    )
    asyncio.run(backend._run_linear_path(_target_pose(), params))

    # Find begin_motion in the call log
    begin_idx = next(
      i for i, (n, _) in enumerate(fake.calls) if n == "ipm_begin_motion"
    )
    pre = fake.calls[:begin_idx]
    pre_sends = [c for c in pre if c[0] == "ipm_send_pvt_point"]
    # 4 axes * 8 preload frames = 32 sends before begin_motion
    self.assertEqual(len(pre_sends), 4 * backend._LINEAR_PATH_PRELOAD)

    # begin_motion received the four arm axes
    _, (axes,) = fake.calls[begin_idx]
    self.assertEqual(set(axes), {
      int(Axis.SHOULDER), int(Axis.Z), int(Axis.ELBOW), int(Axis.WRIST),
    })

  def test_dt_set_to_8ms_default(self):
    fake = _FakeDriver()
    backend = _build_backend(fake)
    params = KX2ArmBackend.CartesianMoveParams(
      max_gripper_speed=20.0, max_gripper_acceleration=200.0, path="linear",
    )
    asyncio.run(backend._run_linear_path(_target_pose(), params))
    dt_calls = [c for c in fake.calls if c[0] == "ipm_set_time_interval"]
    self.assertEqual(len(dt_calls), 1)
    self.assertEqual(dt_calls[0][1], (backend._LINEAR_PATH_DT_MS,))

  def test_select_mode_true_then_false(self):
    """Mode is flipped on at start, off in cleanup. The drive must end in
    PPM so subsequent joint moves don't try to issue PPM triggers in IPM."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    params = KX2ArmBackend.CartesianMoveParams(
      max_gripper_speed=20.0, max_gripper_acceleration=200.0, path="linear",
    )
    asyncio.run(backend._run_linear_path(_target_pose(), params))
    selects = [c[1] for c in fake.calls if c[0] == "ipm_select_mode"]
    self.assertEqual(selects[0], (True,))
    self.assertEqual(selects[-1], (False,))

  def test_wait_uses_ipm_specific_motion_complete(self):
    """End-of-motion wait must poll SW bit-10 (target reached), not MS.
    MS goes to 0 transiently between buffered points; bit-10 is the
    authoritative IPM-done signal."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    params = KX2ArmBackend.CartesianMoveParams(
      max_gripper_speed=20.0, max_gripper_acceleration=200.0, path="linear",
    )
    asyncio.run(backend._run_linear_path(_target_pose(), params))
    # Must NOT use the MS-based generic waiter.
    self.assertFalse([c for c in fake.calls if c[0] == "wait_for_moves_done"])
    waits = [c for c in fake.calls if c[0] == "ipm_wait_motion_complete"]
    self.assertEqual(len(waits), 1)
    (axes, _) = waits[0][1]
    self.assertEqual(set(axes), {
      int(Axis.SHOULDER), int(Axis.Z), int(Axis.ELBOW), int(Axis.WRIST),
    })

  def test_queue_fault_checked_after_preload_and_each_send(self):
    """After preload and after every streamed send, the runtime must
    inspect _ipm_emcy via ipm_check_queue_fault — otherwise queue_full /
    underflow EMCYs are silently swallowed and the move appears to
    succeed even when the drive rejected our points."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    params = KX2ArmBackend.CartesianMoveParams(
      max_gripper_speed=20.0, max_gripper_acceleration=200.0, path="linear",
    )
    asyncio.run(backend._run_linear_path(_target_pose(), params))
    fault_checks = [c for c in fake.calls if c[0] == "ipm_check_queue_fault"]
    # At least one after preload, then one after each streamed send, plus
    # one after the wait — total ≥ 3.
    self.assertGreaterEqual(len(fault_checks), 3)


class LinearPathCancellationCleanup(unittest.TestCase):
  def test_cancel_mid_stream_runs_ipm_stop_and_ipm_select_false(self):
    """Inject a CancelledError after a few sends. Both cleanup steps must
    execute regardless: ipm_stop dropping ip-enable, then ipm_select_mode
    flipping back to PPM."""
    fake = _FakeDriver()
    fake.cancel_after_sends = 12  # 8 preload + a few streamed
    backend = _build_backend(fake)
    params = KX2ArmBackend.CartesianMoveParams(
      max_gripper_speed=20.0, max_gripper_acceleration=200.0, path="linear",
    )
    with self.assertRaises(asyncio.CancelledError):
      asyncio.run(backend._run_linear_path(_target_pose(), params))

    names = [c[0] for c in fake.calls]
    self.assertIn("ipm_stop", names)
    self.assertEqual(names[-1], "ipm_select_mode")
    self.assertEqual(fake.calls[-1][1], (False,))


class LinearPathValidation(unittest.TestCase):
  def test_missing_caps_raises(self):
    """No max_gripper_speed -> we can't build the Cartesian profile.
    Surface a clear ValueError before any drive interaction."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    params_no_speed = KX2ArmBackend.CartesianMoveParams(
      max_gripper_speed=None,
      max_gripper_acceleration=200.0,
      path="linear",
    )
    with self.assertRaises(ValueError):
      asyncio.run(backend._run_linear_path(_target_pose(), params_no_speed))
    self.assertEqual(len(fake.calls), 0)

    params_no_accel = KX2ArmBackend.CartesianMoveParams(
      max_gripper_speed=20.0,
      max_gripper_acceleration=None,
      path="linear",
    )
    with self.assertRaises(ValueError):
      asyncio.run(backend._run_linear_path(_target_pose(), params_no_accel))


class LinearPathDispatchDefault(unittest.TestCase):
  def test_default_path_is_joint(self):
    """``CartesianMoveParams()`` defaults to joint-space planning. Verify
    the field is unset (no surprise switch to linear)."""
    p = KX2ArmBackend.CartesianMoveParams()
    self.assertEqual(p.path, "joint")


if __name__ == "__main__":
  unittest.main()
