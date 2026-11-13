"""Arm kinematics tests: units against the wire, the geometry at idle, the encoder correction."""

import struct
import unittest

from pylabrobot.formulatrix.mantis.driver.arm_kinematics import (
  compute_absolute_device_position,
  compute_corrected_encoder_reading,
  compute_device_position,
  compute_device_rate,
  compute_head_position,
  compute_joint_angles,
  compute_position,
)
from pylabrobot.formulatrix.mantis.driver.errors import MantisKinematicsError

# Doubles a Mantis v4 was sent, as hex: the arms' and Z's speed and acceleration of a move, Z at
# 20 mm, and Z's fast homing speed and acceleration.
SENT_ARM_SPEED = "b4a291808fb3b540"
SENT_ARM_ACCELERATION = "814e1be8b40a8a40"
SENT_Z_SPEED = "4cd431fab7eaa040"
SENT_Z_ACCELERATION = "bb34be4f05c2be40"
SENT_Z_AT_20_MM = "66b3d96c369b8840"
SENT_Z_HOMING_SPEED = "64ea130f73748240"
SENT_Z_HOMING_ACCELERATION = "7d2ae7ab04c2ce40"
# A synthetic correction table: each reading is its own ideal value.
IDENTITY = [(float(row), float(row)) for row in range(1, 201)]


def as_hex(value: float) -> str:
  return struct.pack("<d", value).hex()


class UnitTests(unittest.TestCase):
  def test_rates_and_positions_are_the_bytes_sent(self):
    cases = [
      (compute_device_rate(motor=0, rate=10000.0), SENT_ARM_SPEED),
      (compute_device_rate(motor=1, rate=1500.0), SENT_ARM_ACCELERATION),
      (compute_device_rate(motor=2, rate=55.0), SENT_Z_SPEED),
      (compute_device_rate(motor=2, rate=200.0), SENT_Z_ACCELERATION),
      (compute_device_position(motor=2, position=20.0), SENT_Z_AT_20_MM),
      (compute_device_rate(motor=2, rate=15.0), SENT_Z_HOMING_SPEED),
      (compute_device_rate(motor=2, rate=400.0), SENT_Z_HOMING_ACCELERATION),
    ]
    for value, sent in cases:
      with self.subTest(sent=sent):
        self.assertEqual(as_hex(value), sent)

  def test_a_motor_that_does_not_move_is_sent_0(self):
    self.assertEqual(compute_device_rate(motor=2, rate=0.0), 0.0)

  def test_200_device_units_are_one_revolution(self):
    self.assertAlmostEqual(compute_position(motor=0, device_position=200.0), 360.0)
    self.assertAlmostEqual(compute_position(motor=2, device_position=200.0), 5.08)
    self.assertAlmostEqual(compute_device_position(motor=1, position=360.0), 200.0)


class KinematicsTests(unittest.TestCase):
  def test_both_links_at_0_put_the_head_at_idle(self):
    # The idle position is (15.00, 31.177).
    x, y = compute_head_position(theta_1=0.0, theta_2=0.0)
    self.assertAlmostEqual(x, 15.0, places=9)
    self.assertAlmostEqual(y, 31.177, places=3)

  def test_inverse_and_forward_kinematics_agree(self):
    # In front of the arm and in the back zones, where the other solution lies elsewhere.
    points = [(15.0, 31.177), (-57.0, 0.0), (60.0, 80.0), (0.0, 100.0)]
    points += [(-85.0, -45.0), (75.0, -50.0), (-65.0, -4.0), (90.0, -50.0)]
    for x, y in points:
      with self.subTest(x=x, y=y):
        theta_1, theta_2 = compute_joint_angles(x=x, y=y)
        head = compute_head_position(theta_1=theta_1, theta_2=theta_2)
        self.assertAlmostEqual(head[0], x, places=6)
        self.assertAlmostEqual(head[1], y, places=6)

  def test_idle_is_both_links_at_0(self):
    theta_1, theta_2 = compute_joint_angles(x=15.0, y=31.17691453623979)
    self.assertAlmostEqual(theta_1, 0.0, places=9)
    self.assertAlmostEqual(theta_2, 0.0, places=9)

  def test_out_of_reach_raises(self):
    for x, y in ((0.0, 200.0), (0.0, 0.0), (30.0, 0.0)):
      with self.subTest(x=x, y=y):
        with self.assertRaises(MantisKinematicsError):
          compute_joint_angles(x=x, y=y)


class EncoderCorrectionTests(unittest.TestCase):
  def test_a_reading_between_two_samples_is_interpolated(self):
    table = [(1.0, 0.5), (2.0, 1.5), (3.0, 2.0)]
    self.assertAlmostEqual(compute_corrected_encoder_reading(reading=1.0, correction=table), 1.5)
    self.assertAlmostEqual(compute_corrected_encoder_reading(reading=1.75, correction=table), 2.5)

  def test_a_reading_past_the_last_sample_continues_into_the_first(self):
    self.assertAlmostEqual(
      compute_corrected_encoder_reading(reading=200.5, correction=IDENTITY), 0.5
    )
    self.assertAlmostEqual(compute_corrected_encoder_reading(reading=0.5, correction=IDENTITY), 0.5)

  def test_each_motor_wraps_above_its_own_position(self):
    cases = [
      (0, 100.0, 100.0),
      (0, 100.5, -99.5),
      (1, 110.0, 110.0),
      (1, 120.5, -79.5),
      (0, 50.0 - 60.0, -10.0),
    ]
    for motor, reading_above_reference, position in cases:
      with self.subTest(motor=motor, reading=reading_above_reference):
        reading = 50.0 + reading_above_reference
        self.assertAlmostEqual(
          compute_absolute_device_position(
            motor=motor, reading=reading % 200.0, correction=IDENTITY, homing_position_a=50.0
          ),
          position,
        )


if __name__ == "__main__":
  unittest.main()
