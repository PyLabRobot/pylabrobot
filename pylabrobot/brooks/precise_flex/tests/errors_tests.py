import unittest

from pylabrobot.brooks.precise_flex.driver.errors import (
  PreciseFlexCollisionError,
  PreciseFlexError,
  PreciseFlexNotReadyError,
  PreciseFlexPowerError,
  PreciseFlexReachError,
  PreciseFlexServoError,
  PreciseFlexVisionError,
  is_collision,
)


class TestErrorClassDispatch(unittest.TestCase):
  """PreciseFlexError constructs the most specific subclass for a reply code (STAR-style)."""

  def test_collision_code_yields_collision_subclass(self):
    """A torque-saturation / envelope code (-3101) becomes a PreciseFlexCollisionError."""
    err = PreciseFlexError(-3101, "")
    self.assertIsInstance(err, PreciseFlexCollisionError)
    self.assertTrue(is_collision(err))

  def test_each_kind_of_failure_has_its_class(self):
    for code, cls in (
      (-1021, PreciseFlexNotReadyError),  # robot not homed
      (-1009, PreciseFlexNotReadyError),  # no robot attached
      (-1602, PreciseFlexPowerError),  # external E-stop
      (-1028, PreciseFlexPowerError),  # hard E-stop, reported as a robot error
      (-1012, PreciseFlexReachError),  # joint out of range
      (-1040, PreciseFlexReachError),  # position too far
      (-3104, PreciseFlexServoError),  # motor duty cycle exceeded
      (-3014, PreciseFlexServoError),  # cal parameters not set
      (-4016, PreciseFlexVisionError),  # replied by the arm to `VToolProperty System Info`
      (-4015, PreciseFlexVisionError),  # replied by the arm, not in the table
      (-4030, PreciseFlexVisionError),  # replied by the arm to StereoLocate, not in the table
    ):
      with self.subTest(code):
        self.assertIs(type(PreciseFlexError(code, "")), cls)

  def test_collisions_are_servo_errors(self):
    for code in (-3100, -3101, -3105, -3122):
      with self.subTest(code):
        err = PreciseFlexError(code, "")
        self.assertIs(type(err), PreciseFlexCollisionError)
        self.assertIsInstance(err, PreciseFlexServoError)

  def test_unmapped_code_stays_base(self):
    """A code in neither category stays the plain base type."""
    err = PreciseFlexError(-202, "")
    self.assertIs(type(err), PreciseFlexError)

  def test_subclasses_are_caught_by_the_base_type(self):
    """A category subclass is still an ordinary PreciseFlexError, so `except PreciseFlexError` works."""
    for code in (-3101, -1021, -1602, -1012, -3104):
      self.assertIsInstance(PreciseFlexError(code, ""), PreciseFlexError)

  def test_constructing_a_subclass_directly_is_not_redispatched(self):
    """Building a subclass with a non-matching code keeps that subclass (dispatch only refines base)."""
    err = PreciseFlexReachError(-202, "")
    self.assertIs(type(err), PreciseFlexReachError)

  def test_message_still_formats_from_the_code_table(self):
    """The subclass keeps the base formatting: the code's table text appears in the message."""
    self.assertIn("-3101", str(PreciseFlexError(-3101, "")))
