import math
import unittest
from dataclasses import dataclass

from pylabrobot.capabilities.arms.kinematics import (
  gripper_speed,
  joint_velocities_for_max_gripper_speed,
  sample_gripper_speed_along_trajectory,
)


@dataclass
class _XYZ:
  """Non-rounding x/y/z holder for tests; Coordinate rounds to 0.1 micron
  which makes finite differences degenerate."""

  x: float
  y: float
  z: float


class GripperSpeedTests(unittest.TestCase):
  def test_static_pose_zero_speed(self):
    """All velocities zero -> gripper speed zero, regardless of FK."""

    def fk(j):
      return _XYZ(x=math.cos(j["a"]), y=math.sin(j["a"]), z=0.0)

    self.assertAlmostEqual(gripper_speed(fk, {"a": 1.5}, {"a": 0.0}), 0.0, places=10)

  def test_pure_translation(self):
    """fk(q) = (q, 0, 0); dx/dt = q_dot => |v| = |q_dot|."""

    def fk(j):
      return _XYZ(x=j["q"], y=0.0, z=0.0)

    self.assertAlmostEqual(gripper_speed(fk, {"q": 0.0}, {"q": 7.0}), 7.0, places=6)
    self.assertAlmostEqual(gripper_speed(fk, {"q": 100.0}, {"q": -3.5}), 3.5, places=6)

  def test_unit_circle_tangential_speed(self):
    """fk(theta) on the unit circle; |v| = r * |theta_dot|. r=1, theta_dot=2 => |v|=2."""

    def fk(j):
      return _XYZ(x=math.cos(j["t"]), y=math.sin(j["t"]), z=0.0)

    self.assertAlmostEqual(gripper_speed(fk, {"t": 0.7}, {"t": 2.0}), 2.0, places=6)

  def test_circle_with_radius(self):
    """fk(theta) on a circle of radius 5; |v| = 5 * |theta_dot|."""

    def fk(j):
      return _XYZ(x=5.0 * math.cos(j["t"]), y=5.0 * math.sin(j["t"]), z=0.0)

    self.assertAlmostEqual(gripper_speed(fk, {"t": 1.2}, {"t": 3.0}), 15.0, places=5)

  def test_two_dof_quadrature(self):
    """Two independent linear axes; speeds add in quadrature."""

    def fk(j):
      return _XYZ(x=j["a"], y=j["b"], z=0.0)

    self.assertAlmostEqual(
      gripper_speed(fk, {"a": 0, "b": 0}, {"a": 3.0, "b": 4.0}), 5.0, places=6
    )

  def test_missing_velocity_key_is_zero(self):
    """Velocity keys can be a subset of joint keys."""

    def fk(j):
      return _XYZ(x=j["a"] + j["b"], y=0.0, z=0.0)

    self.assertAlmostEqual(gripper_speed(fk, {"a": 0, "b": 0}, {"a": 2.0}), 2.0, places=6)


class SampleAlongTrajectoryTests(unittest.TestCase):
  def test_alpha_endpoints(self):
    """First sample at alpha=0, last at alpha=1."""

    def fk(j):
      return _XYZ(x=j["q"], y=0.0, z=0.0)

    samples = list(
      sample_gripper_speed_along_trajectory(
        fk, joints_start={"q": 0}, joint_deltas={"q": 10}, joint_velocities={"q": 1.0}, num_samples=5
      )
    )
    self.assertEqual(len(samples), 5)
    self.assertAlmostEqual(samples[0][0], 0.0)
    self.assertAlmostEqual(samples[-1][0], 1.0)

  def test_constant_speed_along_linear_fk(self):
    """For a linear fk, speed is constant along the trajectory."""

    def fk(j):
      return _XYZ(x=j["q"], y=0.0, z=0.0)

    speeds = [
      s
      for _, s in sample_gripper_speed_along_trajectory(
        fk, {"q": -5}, {"q": 10}, {"q": 2.0}, num_samples=10
      )
    ]
    for s in speeds:
      self.assertAlmostEqual(s, 2.0, places=6)

  def test_max_speed_at_extension_for_radial_arm(self):
    """fk(elbow, shoulder) = (r * cos, r * sin) where r = elbow.

    With elbow ramping from 1 to 5 (delta=4) and only shoulder moving
    (theta_dot=1), tangential speed = r * theta_dot grows monotonically;
    max at the end."""

    def fk(j):
      r = j["e"]
      return _XYZ(x=r * math.cos(j["s"]), y=r * math.sin(j["s"]), z=0.0)

    samples = list(
      sample_gripper_speed_along_trajectory(
        fk,
        joints_start={"e": 1.0, "s": 0.0},
        joint_deltas={"e": 4.0, "s": 0.0},
        joint_velocities={"s": 1.0},
        num_samples=20,
      )
    )
    speeds = [s for _, s in samples]
    self.assertAlmostEqual(speeds[0], 1.0, places=5)
    self.assertAlmostEqual(speeds[-1], 5.0, places=5)
    self.assertEqual(max(speeds), speeds[-1])

  def test_wrap_around_path(self):
    """Direction-aware deltas: a ShortestWay move from 350 deg to 10 deg
    walks through 0 (delta=+20), not through 180 (delta=-340). The unit
    circle FK has constant speed = |theta_dot|, but the path is
    different -- we sanity-check that the start/end poses are correct."""

    def fk(j):
      r = math.radians(j["t"])
      return _XYZ(x=math.cos(r), y=math.sin(r), z=0.0)

    samples = list(
      sample_gripper_speed_along_trajectory(
        fk, joints_start={"t": 350.0}, joint_deltas={"t": 20.0},
        joint_velocities={"t": 1.0}, num_samples=11,
      )
    )
    # alpha=0 -> 350 deg (cos=cos(-10), sin=-sin(10))
    # alpha=1 -> 370 deg = 10 deg (cos=cos(10), sin=sin(10))
    # alpha=0.5 -> 360 deg = 0 deg (cos=1, sin=0); proves we walked through 0, not 180.
    mid_speed = samples[5][1]
    self.assertAlmostEqual(mid_speed, math.radians(1.0), places=6)

  def test_too_few_samples_raises(self):
    def fk(j):
      return _XYZ(x=0.0, y=0.0, z=0.0)

    with self.assertRaises(ValueError):
      list(sample_gripper_speed_along_trajectory(fk, {}, {}, {}, num_samples=1))


class JointVelocitiesForMaxGripperSpeedTests(unittest.TestCase):
  def test_under_cap_returns_signed_max(self):
    """Cap not binding -> joints run at signed firmware max."""

    def fk(j):
      return _XYZ(x=j["q"], y=0.0, z=0.0)

    v = joint_velocities_for_max_gripper_speed(
      fk, joints_start={"q": 0}, joint_deltas={"q": 10},
      joint_max_velocities={"q": 2.0}, max_gripper_speed=5.0,
    )
    self.assertAlmostEqual(v["q"], 2.0, places=6)

  def test_negative_delta_negates_velocity(self):
    """Negative delta -> velocity is negative."""

    def fk(j):
      return _XYZ(x=j["q"], y=0.0, z=0.0)

    v = joint_velocities_for_max_gripper_speed(
      fk, joints_start={"q": 10}, joint_deltas={"q": -10},
      joint_max_velocities={"q": 2.0}, max_gripper_speed=5.0,
    )
    self.assertAlmostEqual(v["q"], -2.0, places=6)

  def test_zero_delta_zero_velocity(self):
    """Axes with zero delta get velocity 0, regardless of max."""

    def fk(j):
      return _XYZ(x=j["a"] + j["b"], y=0.0, z=0.0)

    v = joint_velocities_for_max_gripper_speed(
      fk,
      joints_start={"a": 0, "b": 5},
      joint_deltas={"a": 10, "b": 0},
      joint_max_velocities={"a": 1.0, "b": 1.0},
      max_gripper_speed=100.0,
    )
    self.assertAlmostEqual(v["a"], 1.0, places=6)
    self.assertAlmostEqual(v["b"], 0.0, places=6)

  def test_over_cap_scales_linearly(self):
    """firmware_max=10 gives gripper speed 10; cap=2 -> v=0.2*10=2."""

    def fk(j):
      return _XYZ(x=j["q"], y=0.0, z=0.0)

    v = joint_velocities_for_max_gripper_speed(
      fk, joints_start={"q": 0}, joint_deltas={"q": 100},
      joint_max_velocities={"q": 10.0}, max_gripper_speed=2.0,
    )
    self.assertAlmostEqual(v["q"], 2.0, places=6)

  def test_radial_arm_caps_at_full_extension(self):
    """Radial arm with elbow + shoulder both moving. Verifies (a) the cap
    is met along the path and (b) every axis is scaled by the same
    factor (proportions to joint_max preserved)."""

    def fk(j):
      r = j["e"]
      return _XYZ(x=r * math.cos(j["s"]), y=r * math.sin(j["s"]), z=0.0)

    start = {"e": 0.0, "s": 0.0}
    delta = {"e": 5.0, "s": 1.0}
    joint_max = {"e": 1.0, "s": 2.0}
    v = joint_velocities_for_max_gripper_speed(
      fk, start, delta, joint_max, max_gripper_speed=6.0, num_samples=50,
    )
    max_speed = max(
      s for _, s in sample_gripper_speed_along_trajectory(fk, start, delta, v, num_samples=50)
    )
    self.assertAlmostEqual(max_speed, 6.0, places=3)
    self.assertAlmostEqual(v["e"] / joint_max["e"], v["s"] / joint_max["s"], places=6)


if __name__ == "__main__":
  unittest.main()
