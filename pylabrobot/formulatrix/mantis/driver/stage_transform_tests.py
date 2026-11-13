"""Stage transform tests: each step of the chain, worked by hand on synthetic values."""

import unittest

from pylabrobot.formulatrix.mantis.driver.errors import MantisKinematicsError
from pylabrobot.formulatrix.mantis.driver.stage_transform import compute_arm_frame_position

IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
FLAT = (0.0, 0.0, 0.0)


def compute(*, position, **stage):
  arguments = {
    "origin": FLAT,
    "transformation_matrix": IDENTITY,
    "offset_x": FLAT,
    "offset_y": FLAT,
    "arm_type_rotation_degree": 0.0,
    "arm_type_translation": FLAT,
    **stage,
  }
  return compute_arm_frame_position(position=position, **arguments)


class StageTransformTests(unittest.TestCase):
  def assert_close(self, actual, expected):
    for axis, value, wanted in zip("xyz", actual, expected):
      with self.subTest(axis=axis):
        self.assertAlmostEqual(value, wanted, places=12)

  def test_the_identity_keeps_the_point(self):
    self.assert_close(compute(position=(14.3, 11.28, -7.035)), (14.3, 11.28, -7.035))

  def test_the_origin_moves_the_point(self):
    position = compute(position=(14.3, 11.28, -7.035), origin=(-50.0, 40.0, 51.0))
    self.assert_close(position, (-35.7, 51.28, 43.965))

  def test_x_and_y_are_mapped_projectively_after_the_origin(self):
    # (5, 5) + (10, 20) = (15, 25); weight 0.01 * 15 + 1 = 1.15.
    matrix = ((2.0, 0.0, 1.0), (0.0, 3.0, -1.0), (0.01, 0.0, 1.0))
    position = compute(
      position=(5.0, 5.0, 0.0), origin=(10.0, 20.0, 30.0), transformation_matrix=matrix
    )
    self.assert_close(position, (31.0 / 1.15, 74.0 / 1.15, 30.0))

  def test_z_tilts_with_the_wellplate_frame_not_the_stage(self):
    # The offsets' x and y are not used; their z per mm of the wellplate frame's x and y.
    position = compute(
      position=(100.0, 50.0, -2.0),
      origin=(-50.0, 40.0, 51.0),
      offset_x=(5.0, 5.0, -0.001),
      offset_y=(5.0, 5.0, 0.002),
    )
    self.assert_close(position, (50.0, 90.0, 51.0 - 2.0 - 0.1 + 0.1))

  def test_the_arm_type_turns_then_moves_the_point_before_the_origin(self):
    position = compute(
      position=(10.0, 0.0, 1.0),
      origin=(100.0, 200.0, 0.0),
      offset_x=(0.0, 0.0, 0.01),
      arm_type_rotation_degree=90.0,
      arm_type_translation=(1.0, 2.0, 3.0),
    )
    # Turned to (0, 10), moved to (1, 12, 4); the tilt takes the turned and moved x, 1.
    self.assert_close(position, (101.0, 212.0, 4.0 + 0.01))

  def test_a_point_mapped_to_infinity_refuses(self):
    matrix = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.1, 0.0, 1.0))
    with self.assertRaises(MantisKinematicsError):
      compute(position=(-10.0, 0.0, 0.0), transformation_matrix=matrix)


if __name__ == "__main__":
  unittest.main()
