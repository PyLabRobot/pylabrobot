"""Unit tests for ``KX2._run_linear_path``.

Exercises the IPM streaming runtime that backs ``move_to_location(path='linear')``:

* Preload + begin_motion call ordering. Preload writes 8 frames into the
  drive's interpolation buffer *before* CW=0x1F is issued so the first sync
  doesn't underflow the queue.
* Per-sample (P, V) frames go to all four arm axes (SHOULDER/Z/ELBOW/WRIST)
  in lockstep.
* Cancellation mid-stream runs the finally-block cleanup: ``_ipm_stop`` then
  ``_ipm_select_mode(False)``, both even if the cancel arrives before the
  trapezoid is exhausted.
* Validation: requesting ``path='linear'`` without ``max_gripper_speed`` or
  ``max_gripper_acceleration`` raises ValueError.
"""

from __future__ import annotations

import copy
import inspect
from typing import Any, Dict, List, Optional, Tuple

import anyio
import pytest

from pylabrobot.paa.kx2 import kinematics
from pylabrobot.paa.kx2.config import (
  Axis,
  AxisConfig,
  GripperParams,
  KX2Config,
)
from pylabrobot.paa.kx2.kinematics import CartesianPose
from pylabrobot.paa.kx2.kx2 import KX2, _NodeEmcyState
from pylabrobot.paa.kx2.protocol import CanError, JointMoveDirection
from pylabrobot.paa.kx2.tests.lifecycle_tests import driver
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.testing import AnyioTestBase


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
    axes={
      a: _axis()
      for a in (
        Axis.SHOULDER,
        Axis.Z,
        Axis.ELBOW,
        Axis.WRIST,
      )
    },
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
    self.cancel_scope: Optional[anyio.CancelScope] = None
    self.registers: Dict[Tuple[int, int, int], bytes] = {}
    self.positions: Dict[int, int] = {}
    self.endpoint_error = 0
    self.mode_nodes: List[List[int]] = []

  async def _ipm_select_mode(self, enable: bool, node_ids: Optional[List[int]] = None) -> None:
    self.calls.append(("_ipm_select_mode", (enable,)))
    if enable and node_ids is not None:
      self.mode_nodes.append(list(node_ids))
    for node in (1, 2, 3, 4):
      self.registers[node, 0x6060, 0] = bytes([7 if enable else 1])

  async def _ipm_set_time_interval(self, ms: int, node_ids: Optional[List[int]] = None) -> None:
    del node_ids
    self.calls.append(("_ipm_set_time_interval", (ms,)))
    for node in (1, 2, 3, 4):
      self.registers[node, 0x60C2, 1] = bytes([ms])

  async def _ipm_send_pvt_point(
    self,
    node_id: int,
    position_enc: int,
    velocity_enc_per_s: int,
  ) -> None:
    self.send_calls.append((node_id, position_enc, velocity_enc_per_s))
    self.calls.append(
      (
        "_ipm_send_pvt_point",
        (node_id, position_enc, velocity_enc_per_s),
      )
    )
    if (
      self.cancel_after_sends is not None
      and len(self.send_calls) >= self.cancel_after_sends
      and self.cancel_scope is not None
    ):
      self.cancel_scope.cancel()
      await anyio.lowlevel.checkpoint()

  async def _ipm_begin_motion(self, node_ids: List[int]) -> None:
    self.calls.append(("_ipm_begin_motion", (tuple(node_ids),)))

  async def _ipm_stop(self, node_ids: Optional[List[int]] = None) -> None:
    self.calls.append(
      (
        "_ipm_stop",
        (tuple(node_ids) if node_ids is not None else None,),
      )
    )

  def _ipm_check_queue_fault(self, node_ids: List[int], *, allow_empty: bool = False) -> None:
    del allow_empty
    self.calls.append(("_ipm_check_queue_fault", (tuple(int(n) for n in node_ids),)))

  async def _ipm_wait_motion_complete(
    self,
    node_ids: List[int],
    timeout_s: float,
  ) -> None:
    self.calls.append(
      (
        "_ipm_wait_motion_complete",
        (tuple(int(n) for n in node_ids), timeout_s),
      )
    )

  async def _motors_ensure_enabled(
    self,
    node_ids: List[int],
    *,
    use_ds402: bool = True,
  ) -> None:
    del use_ds402
    self.calls.append(("_motors_ensure_enabled", (tuple(int(n) for n in node_ids),)))

  def _clear_emcy_state(self, node_id: Optional[int] = None) -> None:
    self.calls.append(("_clear_emcy_state", (node_id,)))

  async def request_axis_position(self, node_id: int, pu: bool = False) -> int:
    del pu
    return self.positions[node_id]

  async def _can_sdo_upload(self, node_id: int, index: int, sub_index: int) -> bytes:
    return self.registers.get((node_id, index, sub_index), b"\x03")

  async def _can_sdo_download(
    self, node_id: int, index: int, sub_index: int, data: List[int]
  ) -> None:
    self.calls.append(("restore", (node_id, index, sub_index, list(data))))
    self.registers[node_id, index, sub_index] = bytes(data)

  async def _execute(self, node_id: int, cmd: str, cmd_index: int = 0) -> None:
    del cmd_index
    self.calls.append((cmd, (node_id,)))

  async def _control_word_set(self, node_id: int, value: int, sync: bool = True) -> None:
    del sync
    self.calls.append(("controlword", (node_id, value)))

  async def _motor_get_motion_status(self, node_id: int) -> int:
    del node_id
    return 0

  async def request_axis_enabled(self, node_id: int) -> bool:
    del node_id
    return True

  async def _wait_for_moves_done(self, node_ids: List[int], timeout: float) -> None:
    for nid, position, _velocity in self.send_calls:
      self.positions[nid] = position + self.endpoint_error
    self.calls.append(("wait_done", (tuple(node_ids), timeout)))


def _attach_fake(backend: KX2, fake: _FakeDriver) -> None:
  """Bind explicit protocol primitives to the test recorder."""
  backend._can_sdo_upload = fake._can_sdo_upload  # type: ignore[method-assign]
  backend._can_sdo_download = fake._can_sdo_download  # type: ignore[method-assign]
  backend._execute = fake._execute  # type: ignore[method-assign]
  backend._control_word_set = fake._control_word_set  # type: ignore[method-assign]
  backend._motor_get_motion_status = fake._motor_get_motion_status  # type: ignore[method-assign]
  backend.request_axis_enabled = fake.request_axis_enabled  # type: ignore[method-assign]
  backend._wait_for_moves_done = fake._wait_for_moves_done  # type: ignore[method-assign]
  backend._ipm_select_mode = fake._ipm_select_mode  # type: ignore[method-assign]
  backend._ipm_set_time_interval = fake._ipm_set_time_interval  # type: ignore[method-assign]
  backend._ipm_send_pvt_point = fake._ipm_send_pvt_point  # type: ignore[method-assign]
  backend._ipm_begin_motion = fake._ipm_begin_motion  # type: ignore[method-assign]
  backend._ipm_stop = fake._ipm_stop  # type: ignore[method-assign]
  backend._ipm_check_queue_fault = fake._ipm_check_queue_fault  # type: ignore[method-assign]
  backend._ipm_wait_motion_complete = fake._ipm_wait_motion_complete  # type: ignore[method-assign]
  backend._motors_ensure_enabled = fake._motors_ensure_enabled  # type: ignore[method-assign]
  backend._clear_emcy_state = fake._clear_emcy_state  # type: ignore[method-assign]
  backend._motor_read_position_raw = fake.request_axis_position  # type: ignore[method-assign]
  backend._has_session_pdo = lambda node, number, *, transmit: True  # type: ignore[method-assign]


def _build_backend(
  fake_driver: _FakeDriver,
  *,
  current_joints: Optional[Dict[Axis, float]] = None,
) -> KX2:
  backend = KX2()
  _attach_fake(backend, fake_driver)
  backend._motion_lock = anyio.Lock()
  backend._active_motion = None
  backend._config = _config()
  backend._gripper_params = GripperParams(length=15.0, z_offset=3.0)

  joints: Dict[Axis, float] = (
    dict(current_joints)
    if current_joints is not None
    else {
      Axis.SHOULDER: 30.0,
      Axis.Z: 50.0,
      Axis.ELBOW: 60.0,
      Axis.WRIST: 0.0,
      Axis.SERVO_GRIPPER: 0.0,
    }
  )

  async def _request_joint_position() -> Dict[Axis, float]:
    return dict(joints)

  backend.request_joint_state = _request_joint_position  # type: ignore[method-assign]
  for axis in backend.configuration.axes:
    position = joints[axis]
    if axis == Axis.ELBOW:
      position = kinematics.convert_elbow_position_to_angle(backend.configuration, position)
    fake_driver.positions[int(axis)] = round(
      position * backend.configuration.axes[axis].motor_conversion_factor
    )
  return backend


def _target_pose() -> CartesianPose:
  """A pose ~30 mm away from the harness's current_joints — long enough that
  the arc-length trapezoid produces well over the 8-frame preload."""
  return CartesianPose(
    location=Coordinate(x=20.0, y=110.0, z=80.0),
    rotation=Rotation(z=15.0),
  )


class TestLinearPathHappyPath(AnyioTestBase):
  async def test_preload_then_begin_then_stream(self) -> None:
    """Preload 8 frames per axis (32 sends), then _ipm_begin_motion, then
    further per-frame sends in lockstep across the four arm axes."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    await backend._run_linear_path(
      _target_pose(),
      max_gripper_speed=20.0,
      max_gripper_acceleration=200.0,
    )

    begin_idx = next(i for i, (n, _) in enumerate(fake.calls) if n == "_ipm_begin_motion")
    pre = fake.calls[:begin_idx]
    pre_sends = [c for c in pre if c[0] == "_ipm_send_pvt_point"]
    assert len(pre_sends) == 4 * backend._PVT_PRELOAD

    _, (axes,) = fake.calls[begin_idx]
    assert set(axes) == {
      int(Axis.SHOULDER),
      int(Axis.Z),
      int(Axis.ELBOW),
      int(Axis.WRIST),
    }

  async def test_dt_set_to_8ms_default(self) -> None:
    fake = _FakeDriver()
    backend = _build_backend(fake)
    await backend._run_linear_path(
      _target_pose(),
      max_gripper_speed=20.0,
      max_gripper_acceleration=200.0,
    )
    dt_calls = [c for c in fake.calls if c[0] == "_ipm_set_time_interval"]
    assert len(dt_calls) == 1
    assert dt_calls[0][1] == (backend._PVT_DT_MS,)
    names = [name for name, _ in fake.calls]
    assert names.index("_ipm_set_time_interval") < names.index("_ipm_select_mode")

  async def test_select_mode_true_then_false(self) -> None:
    """Mode is flipped on at start, off in cleanup. The drive must end in
    PPM so subsequent joint moves don't try to issue PPM triggers in IPM."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    await backend._run_linear_path(
      _target_pose(),
      max_gripper_speed=20.0,
      max_gripper_acceleration=200.0,
    )
    selects = [c[1] for c in fake.calls if c[0] == "_ipm_select_mode"]
    assert selects[0] == (True,)
    assert selects[-1] == (False,)

  async def test_stream_end_waits_for_buffered_motion_before_disabling_interpolation(self) -> None:
    """Submitting the terminal point does not mean the look-ahead buffer has drained."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    await backend._run_linear_path(
      _target_pose(),
      max_gripper_speed=20.0,
      max_gripper_acceleration=200.0,
    )
    names = [name for name, _ in fake.calls]
    assert names.index("wait_done") < names.index("_ipm_stop")
    assert not [c for c in fake.calls if c[0] == "_ipm_wait_motion_complete"]
    stops = [c for c in fake.calls if c[0] == "_ipm_stop"]
    assert len(stops) >= 1
    (axes,) = stops[0][1]
    assert set(axes) == {
      int(Axis.SHOULDER),
      int(Axis.Z),
      int(Axis.ELBOW),
      int(Axis.WRIST),
    }

  async def test_queue_fault_checked_after_preload_and_each_send(self) -> None:
    """After preload and after every streamed send, the runtime must
    inspect _emcy via _ipm_check_queue_fault."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    await backend._run_linear_path(
      _target_pose(),
      max_gripper_speed=20.0,
      max_gripper_acceleration=200.0,
    )
    fault_checks = [c for c in fake.calls if c[0] == "_ipm_check_queue_fault"]
    assert len(fault_checks) >= 3


class TestLinearPathCancellationCleanup(AnyioTestBase):
  async def test_cancel_mid_stream_runs_ipm_stop_and_ipm_select_false(self) -> None:
    """Cancel mid-stream via a CancelScope; both cleanup steps must execute."""
    fake = _FakeDriver()
    fake.cancel_after_sends = 12  # 8 preload + a few streamed
    backend = _build_backend(fake)
    with anyio.CancelScope() as scope:
      fake.cancel_scope = scope
      await backend._run_linear_path(
        _target_pose(),
        max_gripper_speed=20.0,
        max_gripper_acceleration=200.0,
      )
    assert scope.cancelled_caught
    names = [c[0] for c in fake.calls]
    assert "_ipm_stop" in names
    assert names[-1] == "restore"
    assert [args for name, args in fake.calls if name == "_ipm_select_mode"][-1] == (False,)

  async def test_cancel_mid_stream_over_can_sends_shielded_axis_stops(self) -> None:
    """Cancelling _stream_samples on CANopen still sends shielded BI stops for all motion axes."""
    arm, io = driver()
    async with arm:
      for node in arm.motion_node_ids:
        io.values[node, "MO", 0] = 1
        io.values[node, "MS", 0] = 0
        io.values[node, "PX", 0] = 0
      samples = [
        kinematics.LinearPathSample(
          time_s=i * 0.008,
          joints={},
          encoder_position={
            Axis.SHOULDER: 0,
            Axis.Z: 0,
            Axis.ELBOW: 0,
            Axis.WRIST: 46 * i,
          },
          encoder_velocity={axis: 0 for axis in (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)},
        )
        for i in range(4)
      ]
      begun = anyio.Event()
      cancel_scope = anyio.CancelScope()

      async def wait_forever(node_ids: List[int], timeout: float) -> None:
        del node_ids, timeout
        begun.set()
        await anyio.sleep_forever()

      async def send_pvt(node_id: int, position_enc: int, velocity_enc_per_s: int) -> None:
        del node_id, position_enc, velocity_enc_per_s
        await anyio.lowlevel.checkpoint()

      async def begin_ipm(node_ids: List[int]) -> None:
        del node_ids
        await anyio.lowlevel.checkpoint()

      arm._ipm_send_pvt_point = send_pvt  # type: ignore[method-assign]
      arm._ipm_begin_motion = begin_ipm  # type: ignore[method-assign]
      arm._wait_for_moves_done = wait_forever  # type: ignore[method-assign]

      async def run_stream() -> None:
        with cancel_scope:
          await arm._stream_samples(samples)

      with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_stream)
          await begun.wait()
          io.frames.clear()
          cancel_scope.cancel()
      assert cancel_scope.cancelled_caught
      for cob in (0x301, 0x302, 0x303, 0x304):
        assert (cob, b"ST\x00\x00") in io.frames


class TestLinearPathValidation(AnyioTestBase):
  async def test_missing_caps_raises(self) -> None:
    """No max_gripper_speed -> we can't build the Cartesian profile."""
    fake = _FakeDriver()
    backend = _build_backend(fake)
    with pytest.raises(ValueError):
      await backend._run_linear_path(
        _target_pose(),
        max_gripper_speed=None,
        max_gripper_acceleration=200.0,
      )
    assert len(fake.calls) == 0

    with pytest.raises(ValueError):
      await backend._run_linear_path(
        _target_pose(),
        max_gripper_speed=20.0,
        max_gripper_acceleration=None,
      )


class TestStreamedEndpoints(AnyioTestBase):
  """Exercise encoder resolution, absolute coordinates, and measured completion."""

  def _samples(self, fake: _FakeDriver) -> List[kinematics.LinearPathSample]:
    """A small wrist move with the other axes stationary."""
    positions = {Axis(nid): value for nid, value in fake.positions.items()}
    return [
      kinematics.LinearPathSample(
        time_s=i * 0.008,
        joints={},
        encoder_position={
          axis: value + (46 * i if axis == Axis.WRIST else 0) for axis, value in positions.items()
        },
        encoder_velocity={axis: 0 for axis in positions},
      )
      for i in range(3)
    ]

  async def test_motion_below_500_counts_is_sent(self) -> None:
    fake = _FakeDriver()
    backend = _build_backend(fake)
    samples = self._samples(fake)
    await backend._stream_samples(samples)
    assert [node for node, _, _ in fake.send_calls] == [Axis.WRIST] * 3
    assert fake.mode_nodes == [[Axis.WRIST]]
    assert fake.positions[Axis.WRIST] == samples[-1].encoder_position[Axis.WRIST]

  async def test_missed_endpoint_raises_and_cleans_up(self) -> None:
    fake = _FakeDriver()
    backend = _build_backend(fake)
    fake.endpoint_error = 30
    with pytest.raises(CanError, match="WRIST did not reach"):
      await backend._stream_samples(self._samples(fake))
    assert ("_ipm_select_mode", (False,)) in fake.calls
    assert ("ST", (Axis.WRIST,)) in fake.calls

  async def test_terminal_empty_fifo_requires_the_measured_endpoint(self) -> None:
    for error in (0, 30):
      fake = _FakeDriver()
      fake.endpoint_error = error
      backend = _build_backend(fake)

      async def wait_done(node_ids: List[int], timeout: float) -> None:
        await fake._wait_for_moves_done(node_ids, timeout)
        backend._emcy[Axis.WRIST] = _NodeEmcyState(underflow=True)

      backend._wait_for_moves_done = wait_done  # type: ignore[method-assign]
      backend._ipm_check_queue_fault = (  # type: ignore[method-assign]
        lambda nodes, **kw: KX2._ipm_check_queue_fault(backend, nodes, **kw)
      )
      if error:
        with pytest.raises(CanError, match="WRIST did not reach"):
          await backend._stream_samples(self._samples(fake))
      else:
        await backend._stream_samples(self._samples(fake))

  async def test_empty_fifo_during_feeding_is_rejected(self) -> None:
    fake = _FakeDriver()
    backend = _build_backend(fake)

    async def send(node_id: int, position_enc: int, velocity_enc_per_s: int) -> None:
      await fake._ipm_send_pvt_point(node_id, position_enc, velocity_enc_per_s)
      backend._emcy[Axis.WRIST] = _NodeEmcyState(underflow=True)

    backend._ipm_send_pvt_point = send  # type: ignore[method-assign]
    backend._ipm_check_queue_fault = lambda nodes, **kw: KX2._ipm_check_queue_fault(  # type: ignore[method-assign]
      backend, nodes, **kw
    )
    with pytest.raises(CanError, match="underflow"):
      await backend._stream_samples(self._samples(fake))

  async def test_previous_stream_empty_fifo_does_not_poison_the_next_move(self) -> None:
    fake = _FakeDriver()
    backend = _build_backend(fake)
    backend._emcy[Axis.WRIST] = _NodeEmcyState(underflow=True)
    backend._ipm_check_queue_fault = lambda nodes, **kw: KX2._ipm_check_queue_fault(  # type: ignore[method-assign]
      backend, nodes, **kw
    )
    await backend._stream_samples(self._samples(fake))

  def test_full_fifo_is_fatal_even_after_feeding(self) -> None:
    backend = KX2()
    backend._emcy[Axis.WRIST] = _NodeEmcyState(queue_full=True)
    with pytest.raises(CanError, match="queue_full"):
      backend._ipm_check_queue_fault([Axis.WRIST], allow_empty=True)

  async def test_stale_start_is_rejected_before_enabling(self) -> None:
    fake = _FakeDriver()
    backend = _build_backend(fake)
    samples = self._samples(fake)
    fake.positions[Axis.Z] += 2000
    with pytest.raises(ValueError, match="measured Z"):
      await backend._stream_samples(samples)
    assert not fake.send_calls
    assert not [name for name, _ in fake.calls if name == "_motors_ensure_enabled"]

  async def test_rotary_wrap_keeps_caller_samples_unchanged(self) -> None:
    fake = _FakeDriver()
    backend = _build_backend(fake)
    backend.configuration.axes[Axis.WRIST].unlimited_travel = True
    samples = self._samples(fake)
    original = copy.deepcopy(samples)
    fake.positions[Axis.WRIST] += 360000
    await backend._stream_samples(samples)
    assert samples == original
    assert fake.positions[Axis.WRIST] == 360092


class TestLinearPathDispatchDefault(AnyioTestBase):
  def test_default_path_is_joint(self) -> None:
    """move_to_location defaults to joint-space planning."""
    default = inspect.signature(KX2.move_to_location).parameters["path"].default
    assert default == "joint"
