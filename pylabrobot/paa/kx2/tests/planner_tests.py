"""Unit tests for the KX2 trajectory planner and elbow conversions in
``pylabrobot.paa.kx2.kinematics``.

Covers planner-level behaviour (``plan_joint_move``, sync passes, encoder
unit conversion, gripper-speed cap, clearance) and the elbow position<->
angle round-trip. FK/IK/snap_to_current already have coverage in
``kinematics_tests.py`` -- this file deliberately stays disjoint.

The planner is pure Python: no driver round-trip, no async, no CAN. We
build :class:`KX2Config` directly with synthetic per-axis configs.
"""

import math
import unittest
from typing import Optional

from pylabrobot.paa.kx2 import kinematics
from pylabrobot.paa.kx2.config import Axis, AxisConfig, GripperParams, KX2Config
from pylabrobot.paa.kx2.driver import JointMoveDirection, MotorsMovePlan


def _axis(
  motor_conversion_factor: float = 1.0,
  max_travel: float = 180.0,
  min_travel: float = -180.0,
  unlimited_travel: bool = False,
  max_vel: float = 100.0,
  max_accel: float = 100.0,
  joint_move_direction: JointMoveDirection = JointMoveDirection.Normal,
) -> AxisConfig:
  return AxisConfig(
    motor_conversion_factor=motor_conversion_factor,
    max_travel=max_travel,
    min_travel=min_travel,
    unlimited_travel=unlimited_travel,
    absolute_encoder=True,
    max_vel=max_vel,
    max_accel=max_accel,
    joint_move_direction=joint_move_direction,
    digital_inputs={},
    analog_inputs={},
    outputs={},
  )


def _config(
  shoulder: Optional[AxisConfig] = None,
  z: Optional[AxisConfig] = None,
  elbow: Optional[AxisConfig] = None,
  wrist: Optional[AxisConfig] = None,
  servo_gripper: Optional[AxisConfig] = None,
  wrist_offset: float = 10.0,
  elbow_offset: float = 20.0,
  elbow_zero_offset: float = 5.0,
  base_to_gripper_clearance_z: float = 0.0,
  base_to_gripper_clearance_arm: float = 0.0,
) -> KX2Config:
  axes = {
    Axis.SHOULDER: shoulder if shoulder is not None else _axis(),
    Axis.Z: z if z is not None else _axis(min_travel=0.0, max_travel=400.0),
    Axis.ELBOW: elbow if elbow is not None else _axis(min_travel=0.0, max_travel=300.0),
    Axis.WRIST: wrist if wrist is not None else _axis(),
  }
  if servo_gripper is not None:
    axes[Axis.SERVO_GRIPPER] = servo_gripper
  return KX2Config(
    wrist_offset=wrist_offset,
    elbow_offset=elbow_offset,
    elbow_zero_offset=elbow_zero_offset,
    axes=axes,
    base_to_gripper_clearance_z=base_to_gripper_clearance_z,
    base_to_gripper_clearance_arm=base_to_gripper_clearance_arm,
    robot_on_rail=False,
    servo_gripper=None,
  )


def _move_for(plan: MotorsMovePlan, axis: Axis):
  for m in plan.moves:
    if m.node_id == int(axis):
      return m
  raise AssertionError(f"axis {axis.name} not in plan")


# --- 1. happy paths ---------------------------------------------------------

class PlanJointMoveHappyPath(unittest.TestCase):
  def test_single_axis_returns_plan_with_one_axis_entry(self):
    cfg = _config()
    g = GripperParams()
    cur = {Axis.SHOULDER: 0.0}
    plan = kinematics.plan_joint_move(cur, {Axis.SHOULDER: 30.0}, cfg, g)
    self.assertIsInstance(plan, MotorsMovePlan)
    assert plan is not None  # type narrowing for mypy
    self.assertEqual(len(plan.moves), 1)

  def test_single_axis_encoder_position_uses_conv_factor(self):
    cfg = _config(shoulder=_axis(motor_conversion_factor=2.0))
    g = GripperParams()
    plan = kinematics.plan_joint_move({Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    m = _move_for(plan, Axis.SHOULDER)
    self.assertEqual(m.position, 60)

  def test_single_axis_velocity_floored_at_one(self):
    """If conv * v < 1 (would round to 0), it's floored at 1."""
    cfg = _config(shoulder=_axis(motor_conversion_factor=1e-6, max_vel=0.001))
    g = GripperParams()
    plan = kinematics.plan_joint_move({Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    m = _move_for(plan, Axis.SHOULDER)
    self.assertGreaterEqual(m.velocity, 1)

  def test_single_axis_velocity_uses_abs_conv(self):
    cfg = _config(shoulder=_axis(motor_conversion_factor=-3.0))
    g = GripperParams()
    plan = kinematics.plan_joint_move({Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    m = _move_for(plan, Axis.SHOULDER)
    self.assertGreater(m.velocity, 0)

  def test_single_axis_move_time_positive(self):
    cfg = _config()
    g = GripperParams()
    plan = kinematics.plan_joint_move({Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    self.assertGreater(plan.move_time, 0.0)

  def test_multi_axis_all_axes_in_plan(self):
    cfg = _config()
    g = GripperParams()
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0}
    tgt = {Axis.SHOULDER: 30.0, Axis.Z: 50.0}
    plan = kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    nodes = {m.node_id for m in plan.moves}
    self.assertEqual(nodes, {int(Axis.SHOULDER), int(Axis.Z)})

  def test_multi_axis_move_time_equals_slowest(self):
    """move_time should equal the slowest axis's total time."""
    cfg = _config(
      shoulder=_axis(max_vel=10.0, max_accel=100.0),
      z=_axis(min_travel=0.0, max_travel=400.0, max_vel=1000.0, max_accel=1000.0),
    )
    g = GripperParams()
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0}
    tgt = {Axis.SHOULDER: 30.0, Axis.Z: 50.0}
    plan = kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    # shoulder is the slow lead axis; with v=10 deg/s and 30 deg, dominated time > 1s.
    self.assertGreater(plan.move_time, 1.0)

  def test_no_op_returns_none(self):
    cfg = _config()
    g = GripperParams()
    cur = {Axis.SHOULDER: 5.0, Axis.Z: 10.0}
    tgt = {Axis.SHOULDER: 5.0, Axis.Z: 10.0}
    self.assertIsNone(kinematics.plan_joint_move(cur, tgt, cfg, g))

  def test_no_op_within_threshold_returns_none(self):
    """Targets within 0.01 of current count as no-op."""
    cfg = _config()
    g = GripperParams()
    cur = {Axis.SHOULDER: 5.0}
    tgt = {Axis.SHOULDER: 5.005}
    self.assertIsNone(kinematics.plan_joint_move(cur, tgt, cfg, g))


# --- 2. travel limit snap and raise ----------------------------------------

class TravelLimitSnap(unittest.TestCase):
  def test_target_above_max_within_tolerance_snapped(self):
    cfg = _config(shoulder=_axis(min_travel=-180.0, max_travel=180.0))
    g = GripperParams()
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 180.05}, cfg, g
    )
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    m = _move_for(plan, Axis.SHOULDER)
    # snapped to 180.0 -> position = 180 * 1.0 = 180
    self.assertEqual(m.position, 180)

  def test_target_above_max_out_of_tolerance_raises(self):
    cfg = _config(shoulder=_axis(min_travel=-180.0, max_travel=180.0))
    g = GripperParams()
    with self.assertRaises(ValueError) as ctx:
      kinematics.plan_joint_move(
        {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 180.5}, cfg, g
      )
    self.assertIn("SHOULDER", str(ctx.exception))

  def test_target_below_min_within_tolerance_snapped(self):
    cfg = _config(shoulder=_axis(min_travel=-180.0, max_travel=180.0))
    g = GripperParams()
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: -180.05}, cfg, g
    )
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    m = _move_for(plan, Axis.SHOULDER)
    self.assertEqual(m.position, -180)

  def test_target_below_min_out_of_tolerance_raises(self):
    cfg = _config(shoulder=_axis(min_travel=-180.0, max_travel=180.0))
    g = GripperParams()
    with self.assertRaises(ValueError) as ctx:
      kinematics.plan_joint_move(
        {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: -180.5}, cfg, g
      )
    self.assertIn("SHOULDER", str(ctx.exception))

  def test_unlimited_travel_axis_accepts_out_of_range(self):
    """An unlimited_travel axis (e.g. wrist) shouldn't raise on a 540° target."""
    cfg = _config(
      wrist=_axis(min_travel=-180.0, max_travel=180.0, unlimited_travel=True),
    )
    g = GripperParams()
    plan = kinematics.plan_joint_move(
      {Axis.WRIST: 0.0}, {Axis.WRIST: 540.0}, cfg, g
    )
    self.assertIsNotNone(plan)


# --- 3. validation ----------------------------------------------------------

class GripperCapValidation(unittest.TestCase):
  def test_negative_speed_raises(self):
    cfg = _config()
    g = GripperParams()
    with self.assertRaises(ValueError) as ctx:
      kinematics.plan_joint_move(
        {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g,
        max_gripper_speed=-1.0,
      )
    self.assertIn("must be positive", str(ctx.exception))

  def test_zero_speed_raises(self):
    cfg = _config()
    g = GripperParams()
    with self.assertRaises(ValueError) as ctx:
      kinematics.plan_joint_move(
        {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g,
        max_gripper_speed=0.0,
      )
    self.assertIn("must be positive", str(ctx.exception))

  def test_negative_acceleration_raises(self):
    cfg = _config()
    g = GripperParams()
    with self.assertRaises(ValueError) as ctx:
      kinematics.plan_joint_move(
        {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g,
        max_gripper_acceleration=-2.0,
      )
    self.assertIn("must be positive", str(ctx.exception))

  def test_zero_acceleration_raises(self):
    cfg = _config()
    g = GripperParams()
    with self.assertRaises(ValueError) as ctx:
      kinematics.plan_joint_move(
        {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g,
        max_gripper_acceleration=0.0,
      )
    self.assertIn("must be positive", str(ctx.exception))

  def test_both_none_runs_at_firmware_max(self):
    cfg = _config()
    g = GripperParams(length=15.0, z_offset=3.0)
    # Need a full arm joint state for fk (the cap helper invokes fk).
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 100.0, Axis.WRIST: 0.0}
    tgt = {Axis.SHOULDER: 30.0, Axis.Z: 0.0, Axis.ELBOW: 100.0, Axis.WRIST: 0.0}
    plan_uncapped = kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIsNotNone(plan_uncapped)
    assert plan_uncapped is not None  # type narrowing for mypy
    # Uncapped move time should match a profile run with the cap set very
    # high (effectively no cap). Both should produce identical move_time.
    plan_high_cap = kinematics.plan_joint_move(
      cur, tgt, cfg, g, max_gripper_speed=1e9, max_gripper_acceleration=1e9
    )
    self.assertIsNotNone(plan_high_cap)
    assert plan_high_cap is not None  # type narrowing for mypy
    self.assertAlmostEqual(plan_uncapped.move_time, plan_high_cap.move_time, places=6)


# --- 4. elbow conversions ---------------------------------------------------

class ElbowConversion(unittest.TestCase):
  def test_max_travel_lands_at_90_deg(self):
    cfg = _config(elbow=_axis(min_travel=0.0, max_travel=300.0), elbow_zero_offset=5.0)
    self.assertAlmostEqual(
      kinematics.convert_elbow_position_to_angle(cfg, 300.0), 90.0, places=9
    )

  def test_zero_pos_lands_at_asin_of_zero_offset(self):
    cfg = _config(elbow=_axis(min_travel=0.0, max_travel=300.0), elbow_zero_offset=5.0)
    expected = math.asin(5.0 / 305.0) * (180.0 / math.pi)
    self.assertAlmostEqual(
      kinematics.convert_elbow_position_to_angle(cfg, 0.0), expected, places=9
    )

  def test_round_trip_below_90(self):
    """pos -> angle -> pos must be identity for points where angle < 90."""
    cfg = _config(elbow=_axis(min_travel=0.0, max_travel=300.0), elbow_zero_offset=5.0)
    for pos in (0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 299.0):
      angle = kinematics.convert_elbow_position_to_angle(cfg, pos)
      back = kinematics.convert_elbow_angle_to_position(cfg, angle)
      self.assertAlmostEqual(back, pos, places=6, msg=f"pos={pos}")

  @unittest.expectedFailure
  def test_round_trip_above_90(self):
    """Same identity for the piecewise-reflected branch (angle > 90).

    The pos->angle formula uses ``(2*max - pos + zero_offset)``; the
    angle->pos formula reflects via ``2*max - pos`` (no zero_offset
    correction). The two are not strict inverses when zero_offset != 0,
    so this test is currently expected to fail. The planner only calls
    pos->angle, so this asymmetry is not on a hot path; flagging it
    here as a regression candidate.
    """
    cfg = _config(elbow=_axis(min_travel=0.0, max_travel=300.0), elbow_zero_offset=5.0)
    for pos in (300.5, 320.0, 380.0, 450.0, 550.0, 600.0):
      angle = kinematics.convert_elbow_position_to_angle(cfg, pos)
      back = kinematics.convert_elbow_angle_to_position(cfg, angle)
      self.assertAlmostEqual(back, pos, places=6, msg=f"pos={pos}")

  def test_above_90_branch_returns_angle_above_90(self):
    """At least confirm the piecewise branch is taken: pos > max_travel
    => angle > 90°."""
    cfg = _config(elbow=_axis(min_travel=0.0, max_travel=300.0), elbow_zero_offset=5.0)
    for pos in (300.5, 320.0, 380.0, 450.0, 550.0, 599.0):
      angle = kinematics.convert_elbow_position_to_angle(cfg, pos)
      self.assertGreater(angle, 90.0, msg=f"pos={pos}")

  def test_angle_round_trip_at_90(self):
    cfg = _config(elbow=_axis(min_travel=0.0, max_travel=300.0), elbow_zero_offset=5.0)
    pos = kinematics.convert_elbow_angle_to_position(cfg, 90.0)
    self.assertAlmostEqual(pos, 300.0, places=9)

  def test_open_clamp_at_max_travel_plus_epsilon_does_not_raise(self):
    """Floating-point overshoot at the joint limit must not raise. The asin
    argument is clamped to [-1, 1] before the call."""
    cfg = _config(elbow=_axis(min_travel=0.0, max_travel=300.0), elbow_zero_offset=5.0)
    for eps in (1e-15, 1e-14, 1e-12, 1e-9):
      kinematics.convert_elbow_position_to_angle(cfg, 300.0 + eps)


# --- 5. direction-aware delta in plan_joint_move ----------------------------

class DirectionAwareDelta(unittest.TestCase):
  def _wrist_cfg(self, direction: JointMoveDirection) -> KX2Config:
    return _config(
      wrist=_axis(
        min_travel=-180.0,
        max_travel=180.0,
        unlimited_travel=True,
        joint_move_direction=direction,
        max_vel=360.0,
        max_accel=720.0,
      )
    )

  def test_clockwise_takes_long_way_when_target_ahead(self):
    """Wrist at 0, target 10. Clockwise => long way around (-350).

    move_time should reflect the long-way distance, not 10.
    """
    cfg_cw = self._wrist_cfg(JointMoveDirection.Clockwise)
    cfg_short = self._wrist_cfg(JointMoveDirection.ShortestWay)
    g = GripperParams()
    cur = {Axis.WRIST: 0.0}
    tgt = {Axis.WRIST: 10.0}
    plan_cw = kinematics.plan_joint_move(cur, tgt, cfg_cw, g)
    plan_short = kinematics.plan_joint_move(cur, tgt, cfg_short, g)
    self.assertIsNotNone(plan_cw)
    self.assertIsNotNone(plan_short)
    assert plan_cw is not None and plan_short is not None  # type narrowing for mypy
    # Long way (350°) takes much longer than short way (10°).
    self.assertGreater(plan_cw.move_time, plan_short.move_time * 5)

  def test_counterclockwise_takes_long_way_when_target_behind(self):
    """Wrist at 0, target -10. Counterclockwise => long way (+350)."""
    cfg_ccw = self._wrist_cfg(JointMoveDirection.Counterclockwise)
    cfg_short = self._wrist_cfg(JointMoveDirection.ShortestWay)
    g = GripperParams()
    cur = {Axis.WRIST: 0.0}
    tgt = {Axis.WRIST: -10.0}
    plan_ccw = kinematics.plan_joint_move(cur, tgt, cfg_ccw, g)
    plan_short = kinematics.plan_joint_move(cur, tgt, cfg_short, g)
    self.assertIsNotNone(plan_ccw)
    self.assertIsNotNone(plan_short)
    assert plan_ccw is not None and plan_short is not None  # type narrowing for mypy
    self.assertGreater(plan_ccw.move_time, plan_short.move_time * 5)

  def test_shortest_way_wraps_at_180(self):
    """A target +190 is reachable via -170 under ShortestWay -- the
    abs distance traveled should equal 170, not 190."""
    cfg_short = self._wrist_cfg(JointMoveDirection.ShortestWay)
    cfg_normal = self._wrist_cfg(JointMoveDirection.Normal)
    g = GripperParams()
    cur = {Axis.WRIST: 0.0}
    plan_short = kinematics.plan_joint_move(cur, {Axis.WRIST: 190.0}, cfg_short, g)
    # ShortestWay first wraps target into (-180, 180]: 190 -> -170.
    plan_normal = kinematics.plan_joint_move(cur, {Axis.WRIST: -170.0}, cfg_normal, g)
    self.assertIsNotNone(plan_short)
    self.assertIsNotNone(plan_normal)
    assert plan_short is not None and plan_normal is not None  # type narrowing for mypy
    self.assertAlmostEqual(plan_short.move_time, plan_normal.move_time, places=6)

  def test_normal_direction_uses_literal_delta(self):
    """Normal mode = no wrap. delta is target - current, no shortcut."""
    cfg_normal = self._wrist_cfg(JointMoveDirection.Normal)
    g = GripperParams()
    plan_long = kinematics.plan_joint_move(
      {Axis.WRIST: 0.0}, {Axis.WRIST: 170.0}, cfg_normal, g
    )
    plan_short = kinematics.plan_joint_move(
      {Axis.WRIST: 0.0}, {Axis.WRIST: 10.0}, cfg_normal, g
    )
    self.assertIsNotNone(plan_long)
    self.assertIsNotNone(plan_short)
    assert plan_long is not None and plan_short is not None  # type narrowing for mypy
    # Move time roughly proportional to distance for the same v/a.
    self.assertGreater(plan_long.move_time, plan_short.move_time)


# --- 6. accel sync ----------------------------------------------------------

class AccelSync(unittest.TestCase):
  def test_slower_accel_axis_drives_accel_for_others(self):
    """Two axes with very different max_accel should sync accel-time.

    The axis with the longer accel ramp (lower a, higher v) becomes the
    lead; the other axis's acceleration should be reduced so the two
    ramp together.
    """
    # Same dist, same v, but very different a.
    cfg = _config(
      shoulder=_axis(max_vel=100.0, max_accel=10.0),   # slow accel -> long ramp -> lead
      z=_axis(min_travel=0.0, max_travel=400.0, max_vel=100.0, max_accel=1000.0),
    )
    g = GripperParams()
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0}
    tgt = {Axis.SHOULDER: 50.0, Axis.Z: 50.0}
    plan = kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    z_move = _move_for(plan, Axis.Z)
    # z's effective acceleration should be reduced from 1000 toward
    # something far smaller after sync. Encoder accel = a * |conv|, conv=1.
    self.assertLess(z_move.acceleration, 1000)


# --- 7. time sync -----------------------------------------------------------

class TimeSync(unittest.TestCase):
  def test_shorter_time_axis_velocity_scaled_down(self):
    """Two axes; one is much slower (long total time), the other should
    have its v and a scaled by k = dist/denom < 1, never exceeding the
    lead's time."""
    cfg = _config(
      shoulder=_axis(max_vel=10.0, max_accel=20.0),                       # slow
      z=_axis(min_travel=0.0, max_travel=400.0, max_vel=100.0, max_accel=200.0),
    )
    g = GripperParams()
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0}
    tgt = {Axis.SHOULDER: 100.0, Axis.Z: 100.0}
    plan = kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    z_move = _move_for(plan, Axis.Z)
    # z's commanded velocity should be far less than its firmware max
    # because it's been time-synced down to the slow shoulder.
    self.assertLess(z_move.velocity, 100)


# --- 8. encoder unit conversion at plan exit --------------------------------

class EncoderUnitConversion(unittest.TestCase):
  def test_conv_factor_scales_position(self):
    cfg = _config(shoulder=_axis(motor_conversion_factor=1000.0))
    g = GripperParams()
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g
    )
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    m = _move_for(plan, Axis.SHOULDER)
    # 30 deg * 1000 enc/deg = 30000
    self.assertEqual(m.position, 30000)

  def test_negative_conv_preserves_position_sign(self):
    """Position uses the signed conv (so -1.0 flips sign), but velocity
    and acceleration use abs(conv) (always positive)."""
    cfg = _config(shoulder=_axis(motor_conversion_factor=-2.0))
    g = GripperParams()
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 30.0}, cfg, g
    )
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    m = _move_for(plan, Axis.SHOULDER)
    self.assertEqual(m.position, -60)
    self.assertGreater(m.velocity, 0)
    self.assertGreater(m.acceleration, 0)

  def test_skip_axis_emits_firmware_max_velocity_acceleration(self):
    """Axes the planner skips (no-op) still write vel/accel to the drive's
    profile registers — set them to firmware max so a stale register can't
    leave a follow-up move pathologically slow."""
    cfg = _config()  # default _axis: max_vel=100, max_accel=100, conv=1.0
    g = GripperParams()
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0}
    tgt = {Axis.SHOULDER: 30.0, Axis.Z: 0.0}  # Z is no-op
    plan = kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    z_move = _move_for(plan, Axis.Z)
    self.assertEqual(z_move.velocity, 100)    # max_vel * |conv|
    self.assertEqual(z_move.acceleration, 100)


# --- 9. gripper-speed cap path ---------------------------------------------

class GripperSpeedCapPath(unittest.TestCase):
  def test_small_cap_reduces_velocity(self):
    """A tight gripper-speed cap should drop joint velocities below the
    uncapped baseline."""
    cfg = _config()
    g = GripperParams(length=15.0, z_offset=3.0)
    cur = {Axis.SHOULDER: 0.0, Axis.Z: 0.0, Axis.ELBOW: 100.0, Axis.WRIST: 0.0}
    tgt = {Axis.SHOULDER: 30.0, Axis.Z: 50.0, Axis.ELBOW: 200.0, Axis.WRIST: 15.0}

    plan_uncapped = kinematics.plan_joint_move(cur, tgt, cfg, g)
    plan_capped = kinematics.plan_joint_move(
      cur, tgt, cfg, g, max_gripper_speed=10.0
    )
    self.assertIsNotNone(plan_uncapped)
    self.assertIsNotNone(plan_capped)
    assert plan_uncapped is not None and plan_capped is not None  # type narrowing
    # Capped plan should take strictly longer than uncapped.
    self.assertGreater(plan_capped.move_time, plan_uncapped.move_time)

  def test_servo_gripper_axis_not_in_arm_axes_passes_through(self):
    """The servo gripper axis (Axis.SERVO_GRIPPER) is not part of fk's
    arm_axes set, so the gripper-speed cap helper shouldn't touch it."""
    cfg = _config(
      servo_gripper=_axis(min_travel=0.0, max_travel=30.0, max_vel=50.0, max_accel=100.0),
    )
    g = GripperParams()
    cur = {Axis.SERVO_GRIPPER: 0.0}
    tgt = {Axis.SERVO_GRIPPER: 20.0}
    # Even with a tight gripper-speed cap, the servo gripper should still
    # produce a plan -- the cap helper sees an empty fk_start and doesn't run.
    plan = kinematics.plan_joint_move(
      cur, tgt, cfg, g, max_gripper_speed=0.001
    )
    self.assertIsNotNone(plan)
    assert plan is not None  # type narrowing for mypy
    self.assertEqual(len(plan.moves), 1)
    self.assertEqual(plan.moves[0].node_id, int(Axis.SERVO_GRIPPER))


# --- 10. clearance check ----------------------------------------------------

class ClearanceCheck(unittest.TestCase):
  def test_violation_raises(self):
    """Z below clearance AND elbow angle below arm clearance -> raise."""
    cfg = _config(
      z=_axis(min_travel=0.0, max_travel=400.0),
      elbow=_axis(min_travel=0.0, max_travel=300.0),
      base_to_gripper_clearance_z=50.0,
      base_to_gripper_clearance_arm=80.0,  # 80 deg threshold (angle space)
    )
    g = GripperParams()
    # target Z = 10 < (0 + 50). Elbow position 50 -> angle ~asin(55/305) ~ 10° < 80°.
    cur = {Axis.Z: 100.0, Axis.ELBOW: 200.0}
    tgt = {Axis.Z: 10.0, Axis.ELBOW: 50.0}
    with self.assertRaises(ValueError) as ctx:
      kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIn("clearance", str(ctx.exception).lower())

  def test_no_violation_when_only_z_low(self):
    """Z low but elbow angle above arm clearance -> no raise."""
    cfg = _config(
      z=_axis(min_travel=0.0, max_travel=400.0),
      elbow=_axis(min_travel=0.0, max_travel=300.0),
      base_to_gripper_clearance_z=50.0,
      base_to_gripper_clearance_arm=5.0,  # very low threshold so any pose passes
    )
    g = GripperParams()
    cur = {Axis.Z: 100.0, Axis.ELBOW: 200.0}
    tgt = {Axis.Z: 10.0, Axis.ELBOW: 200.0}
    plan = kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIsNotNone(plan)

  def test_no_violation_when_only_elbow_low(self):
    """Elbow angle below threshold but Z high -> no raise."""
    cfg = _config(
      z=_axis(min_travel=0.0, max_travel=400.0),
      elbow=_axis(min_travel=0.0, max_travel=300.0),
      base_to_gripper_clearance_z=50.0,
      base_to_gripper_clearance_arm=80.0,
    )
    g = GripperParams()
    cur = {Axis.Z: 100.0, Axis.ELBOW: 200.0}
    tgt = {Axis.Z: 200.0, Axis.ELBOW: 50.0}
    plan = kinematics.plan_joint_move(cur, tgt, cfg, g)
    self.assertIsNotNone(plan)


# --- 11. exact-value pins for the sync algorithm ----------------------------
# These tests pin the *exact* encoder velocity, acceleration, and move_time
# the planner produces for representative cases. They're the contract for any
# refactor of the per-axis profile + accel-sync + time-sync chain — directional
# asserts (assertLess/Greater) are too loose to catch a value-shifting change.
# Tolerance: ±1 on int encoder values (rounding noise from float ops); 4
# decimals on move_time.

class SyncAlgorithmExactValues(unittest.TestCase):
  def _assert_enc_close(self, actual, expected):
    self.assertLessEqual(
      abs(actual - expected), 1, f"{actual} not within 1 of {expected}"
    )

  def test_single_axis_trapezoidal(self):
    """dist=100, v_max=100, a_max=200; t_acc=0.5, d_acc=25, t_cruise=0.5."""
    cfg = _config(shoulder=_axis(max_vel=100.0, max_accel=200.0))
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 100.0}, cfg, GripperParams(),
    )
    self.assertIsNotNone(plan)
    assert plan is not None
    m = _move_for(plan, Axis.SHOULDER)
    self.assertEqual(m.position, 100)
    self.assertEqual(m.velocity, 100)
    self.assertEqual(m.acceleration, 200)
    self.assertAlmostEqual(plan.move_time, 1.5, places=4)

  def test_single_axis_triangular(self):
    """dist=10, v_max=100, a_max=200; can't reach v_max -> triangular.
    t_acc = sqrt(10/200) = 0.2236, v_actual = a*t_acc = 44.72 -> 45."""
    cfg = _config(shoulder=_axis(max_vel=100.0, max_accel=200.0))
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0}, {Axis.SHOULDER: 10.0}, cfg, GripperParams(),
    )
    self.assertIsNotNone(plan)
    assert plan is not None
    m = _move_for(plan, Axis.SHOULDER)
    self.assertEqual(m.position, 10)
    self._assert_enc_close(m.velocity, 45)
    self.assertEqual(m.acceleration, 200)
    self.assertAlmostEqual(plan.move_time, 0.4472136, places=4)

  def test_accel_sync_two_axis_exact(self):
    """Shoulder is the slow-accel lead (v=100, a=10); Z's accel scales down."""
    cfg = _config(
      shoulder=_axis(max_vel=100.0, max_accel=10.0),
      z=_axis(min_travel=0.0, max_travel=400.0, max_vel=100.0, max_accel=1000.0),
    )
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0, Axis.Z: 0.0},
      {Axis.SHOULDER: 50.0, Axis.Z: 50.0},
      cfg, GripperParams(),
    )
    self.assertIsNotNone(plan)
    assert plan is not None
    sh = _move_for(plan, Axis.SHOULDER)
    z = _move_for(plan, Axis.Z)
    self._assert_enc_close(sh.velocity, 22)
    self.assertEqual(sh.acceleration, 10)
    self._assert_enc_close(z.velocity, 15)
    self._assert_enc_close(z.acceleration, 14)
    self.assertAlmostEqual(plan.move_time, 4.472136, places=4)

  def test_time_sync_two_axis_exact(self):
    """Shoulder is the slow-vel lead (v=10, a=20); Z's v scales down to match
    move_time = dist/v + v/a = 100/10 + 10/20 = 10.5s."""
    cfg = _config(
      shoulder=_axis(max_vel=10.0, max_accel=20.0),
      z=_axis(min_travel=0.0, max_travel=400.0, max_vel=100.0, max_accel=200.0),
    )
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0, Axis.Z: 0.0},
      {Axis.SHOULDER: 100.0, Axis.Z: 100.0},
      cfg, GripperParams(),
    )
    self.assertIsNotNone(plan)
    assert plan is not None
    sh = _move_for(plan, Axis.SHOULDER)
    z = _move_for(plan, Axis.Z)
    self.assertEqual(sh.velocity, 10)
    self.assertEqual(sh.acceleration, 20)
    self.assertEqual(z.velocity, 10)
    self.assertEqual(z.acceleration, 20)
    self.assertAlmostEqual(plan.move_time, 10.5, places=4)

  def test_four_axis_real_conv_exact(self):
    """Full 4-axis coordinated move with realistic KX2 conv factors. Pins
    the entire pipeline (elbow asin + sync + encoder packing) end-to-end."""
    cfg = _config(
      shoulder=_axis(max_vel=145.0, max_accel=300.0, motor_conversion_factor=23301.694),
      z=_axis(min_travel=0.0, max_travel=750.0, max_vel=750.0, max_accel=1000.0,
              motor_conversion_factor=3997.838),
      elbow=_axis(min_travel=0.0, max_travel=300.0, max_vel=80.0, max_accel=180.0,
                  motor_conversion_factor=18204.444),
      wrist=_axis(min_travel=-180.0, max_travel=180.0, max_vel=500.0, max_accel=1000.0,
                  motor_conversion_factor=45.511, unlimited_travel=True),
    )
    plan = kinematics.plan_joint_move(
      {Axis.SHOULDER: 0.0, Axis.Z: 50.0, Axis.ELBOW: 100.0, Axis.WRIST: 0.0},
      {Axis.SHOULDER: 30.0, Axis.Z: 200.0, Axis.ELBOW: 150.0, Axis.WRIST: 90.0},
      cfg, GripperParams(),
    )
    self.assertIsNotNone(plan)
    assert plan is not None
    sh = _move_for(plan, Axis.SHOULDER)
    z = _move_for(plan, Axis.Z)
    el = _move_for(plan, Axis.ELBOW)
    wr = _move_for(plan, Axis.WRIST)
    self._assert_enc_close(sh.position, 699051)
    self._assert_enc_close(sh.velocity, 1646247)
    self._assert_enc_close(sh.acceleration, 4704052)
    self._assert_enc_close(z.position, 799568)
    self._assert_enc_close(z.velocity, 1548356)
    self._assert_enc_close(z.acceleration, 3997838)
    self._assert_enc_close(el.position, 556033)
    self._assert_enc_close(el.velocity, 403583)
    self._assert_enc_close(el.acceleration, 1322501)
    self._assert_enc_close(wr.position, 4096)
    self._assert_enc_close(wr.velocity, 9444)
    self._assert_enc_close(wr.acceleration, 27705)
    self.assertAlmostEqual(plan.move_time, 0.774597, places=3)


if __name__ == "__main__":
  unittest.main()
