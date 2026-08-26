import unittest

from pylabrobot.agilent.bravo.deck.teachpoints import Teachpoints
from pylabrobot.agilent.bravo.types import X_TO_X_DISTANCE, Y_TO_Y_DISTANCE


class SetGetRoundTripTests(unittest.TestCase):
  def test_set_then_get_returns_the_same_value(self):
    tp = Teachpoints()
    tp.set_teachpoint(1, "x", 12.34)
    self.assertEqual(tp.get_teachpoint(1, "x"), 12.34)

  def test_overwriting_a_teachpoint_replaces_the_value(self):
    tp = Teachpoints()
    tp.set_teachpoint(1, "z", 1.0)
    tp.set_teachpoint(1, "z", 2.0)
    self.assertEqual(tp.get_teachpoint(1, "z"), 2.0)

  def test_different_axes_at_the_same_location_are_independent(self):
    tp = Teachpoints()
    tp.set_teachpoint(3, "x", 1.0)
    tp.set_teachpoint(3, "y", 2.0)
    tp.set_teachpoint(3, "z", 3.0)
    self.assertEqual(
      (tp.get_teachpoint(3, "x"), tp.get_teachpoint(3, "y"), tp.get_teachpoint(3, "z")),
      (1.0, 2.0, 3.0),
    )

  def test_get_on_never_set_location_raises_key_error(self):
    tp = Teachpoints()
    with self.assertRaises(KeyError):
      tp.get_teachpoint(1, "x")

  def test_locations_reports_only_populated_locations(self):
    tp = Teachpoints()
    tp.set_teachpoint(7, "x", 1.0)
    tp.set_teachpoint(2, "y", 1.0)
    self.assertEqual(tp.locations, [2, 7])

  def test_as_dict_is_a_copy(self):
    tp = Teachpoints()
    tp.set_teachpoint(1, "x", 1.0)
    snapshot = tp.as_dict()
    snapshot[1]["x"] = 999.0
    self.assertEqual(tp.get_teachpoint(1, "x"), 1.0)


class SetDefaultTeachpointsTests(unittest.TestCase):
  def test_populates_all_nine_locations(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("96_d_70")
    self.assertEqual(tp.locations, list(range(1, 10)))

  def test_location_1_uses_the_96_disposable_head_defaults(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("96_d_70")
    self.assertAlmostEqual(tp.get_teachpoint(1, "x"), 5.79)
    self.assertAlmostEqual(tp.get_teachpoint(1, "y"), 5.98)
    self.assertAlmostEqual(tp.get_teachpoint(1, "z"), 60.0)

  def test_location_1_uses_the_384_disposable_head_defaults(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("384_d_70")
    self.assertAlmostEqual(tp.get_teachpoint(1, "x"), 8.03)
    self.assertAlmostEqual(tp.get_teachpoint(1, "y"), 8.22)

  def test_location_1_uses_the_8_lt_head_defaults(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("8_d_lt")
    self.assertAlmostEqual(tp.get_teachpoint(1, "x"), -49.24)
    self.assertAlmostEqual(tp.get_teachpoint(1, "y"), 5.98)

  def test_grid_spacing_advances_x_across_columns(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("96_d_70")
    self.assertAlmostEqual(tp.get_teachpoint(2, "x") - tp.get_teachpoint(1, "x"), X_TO_X_DISTANCE)
    self.assertAlmostEqual(tp.get_teachpoint(3, "x") - tp.get_teachpoint(2, "x"), X_TO_X_DISTANCE)

  def test_grid_spacing_advances_y_across_rows(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("96_d_70")
    self.assertAlmostEqual(tp.get_teachpoint(4, "y") - tp.get_teachpoint(1, "y"), Y_TO_Y_DISTANCE)
    self.assertAlmostEqual(tp.get_teachpoint(7, "y") - tp.get_teachpoint(4, "y"), Y_TO_Y_DISTANCE)

  def test_z_is_constant_across_all_locations(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("96_d_70")
    z_values = {tp.get_teachpoint(loc, "z") for loc in range(1, 10)}
    self.assertEqual(z_values, {60.0})

  def test_head_type_with_no_default_category_raises(self):
    tp = Teachpoints()
    with self.assertRaises(ValueError):
      tp.set_default_teachpoints("96_pintool")

  def test_repopulating_clears_previous_data(self):
    tp = Teachpoints()
    tp.set_teachpoint(1, "w", 42.0)
    tp.set_default_teachpoints("96_d_70")
    with self.assertRaises(KeyError):
      tp.get_teachpoint(1, "w")


class OutOfRangeLocationTests(unittest.TestCase):
  def test_location_beyond_the_populated_deck_is_rejected_on_read(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("96_d_70")
    # Only locations 1-9 are ever populated by set_default_teachpoints;
    # anything past MAX_LOCATIONS was never taught and is rejected.
    with self.assertRaises(KeyError):
      tp.get_teachpoint(10, "x")

  def test_location_zero_is_rejected_on_read(self):
    tp = Teachpoints()
    tp.set_default_teachpoints("96_d_70")
    with self.assertRaises(KeyError):
      tp.get_teachpoint(0, "x")


class CompensateForTipTests(unittest.TestCase):
  def test_shorter_current_tip_raises_z(self):
    tp = Teachpoints()
    tp.set_teachpoint(1, "z", 60.0)
    tp.compensate_for_tip(1, default_tip_length=30.0, current_tip_length=20.0)
    self.assertAlmostEqual(tp.get_teachpoint(1, "z"), 70.0)

  def test_longer_current_tip_lowers_z(self):
    tp = Teachpoints()
    tp.set_teachpoint(1, "z", 60.0)
    tp.compensate_for_tip(1, default_tip_length=30.0, current_tip_length=50.0)
    self.assertAlmostEqual(tp.get_teachpoint(1, "z"), 40.0)

  def test_matching_tip_length_leaves_z_unchanged(self):
    tp = Teachpoints()
    tp.set_teachpoint(1, "z", 60.0)
    tp.compensate_for_tip(1, default_tip_length=30.0, current_tip_length=30.0)
    self.assertAlmostEqual(tp.get_teachpoint(1, "z"), 60.0)

  def test_compensation_only_touches_z(self):
    tp = Teachpoints()
    tp.set_teachpoint(1, "x", 5.0)
    tp.set_teachpoint(1, "z", 60.0)
    tp.compensate_for_tip(1, default_tip_length=30.0, current_tip_length=20.0)
    self.assertAlmostEqual(tp.get_teachpoint(1, "x"), 5.0)

  def test_compensation_on_unset_location_raises(self):
    tp = Teachpoints()
    with self.assertRaises(KeyError):
      tp.compensate_for_tip(1, default_tip_length=30.0, current_tip_length=20.0)


if __name__ == "__main__":
  unittest.main()
