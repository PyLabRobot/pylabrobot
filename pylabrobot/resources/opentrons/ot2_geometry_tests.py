import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.ot2_geometry import OT2RobotGeometry


class TestOT2RobotGeometry(unittest.TestCase):
  """Tests for the static OT-2 robot geometry."""

  def assert_bounds_almost_equal(self, actual, expected):
    for a, e in zip(actual, expected):
      self.assertAlmostEqual(a, e, places=4)

  def test_single_channel_reach_right_mount_is_full_extents(self):
    """Right mount (reference): reach is the full gantry extents anchored at the origin."""
    self.assert_bounds_almost_equal(
      OT2RobotGeometry().single_channel_reach("right"), (0.0, 446.75, 0.0, 347.5)
    )

  def test_single_channel_reach_left_mount_shifted_by_mount_offset(self):
    """Left mount sits 34 mm left of the reference, so its x window shifts by -34 while y is
    unchanged (the mount offset has no y component)."""
    self.assert_bounds_almost_equal(
      OT2RobotGeometry().single_channel_reach("left"), (-34.0, 412.75, 0.0, 347.5)
    )

  def test_can_reach_position_inside_and_outside(self):
    """A point inside the mount's extents reaches; one past the front edge (y<0) does not."""
    geo = OT2RobotGeometry()
    self.assertTrue(geo.can_reach_position("right", Coordinate(265.0, 271.5, 0)))  # slot 12 corner
    self.assertFalse(
      geo.can_reach_position("right", Coordinate(265.0, -5.0, 0))
    )  # in front of deck

  def test_channel_y_offsets_head8_matches_opentrons_nozzle_map(self):
    """8 channels at 9 mm pitch, indexed back-to-front: index 0 is A1 at +31.5 (the back-most,
    primary nozzle), descending to the front-most at -31.5, matching the Opentrons nozzle map."""
    offsets = OT2RobotGeometry().channel_y_offsets()
    self.assertEqual(len(offsets), 8)
    self.assertAlmostEqual(offsets[0], 31.5)
    self.assertAlmostEqual(offsets[-1], -31.5)

  def test_can_reach_position_channel_offset_partial_at_back_edge(self):
    """Near the back limit a back-displaced channel reaches but a front-displaced one does not, so
    a head8 engages only a subset of channels there."""
    geo = OT2RobotGeometry()
    target = Coordinate(265.0, 340.0, 0)
    self.assertTrue(geo.can_reach_position("right", target, channel_offset=31.5))
    self.assertFalse(geo.can_reach_position("right", target, channel_offset=-31.5))

  def test_mount_offset_rejects_unknown_mount(self):
    """Only 'left' and 'right' are valid mounts."""
    with self.assertRaises(ValueError):
      OT2RobotGeometry().mount_offset("middle")
