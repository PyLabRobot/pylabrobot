import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import hamilton_core_gripper_tool, hamilton_tip_300uL
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.resource import Resource


class TestTipMountingShaft(unittest.TestCase):
  """A shaft is saved with what its size does not say, and read back from it."""

  def test_a_serialized_shaft_deserializes(self):
    shaft = TipMountingShaft(name="shaft", tip_pickup_mode="core")
    self.assertEqual(Resource.deserialize(shaft.serialize()).serialize(), shaft.serialize())

  def test_a_shaft_is_round(self):
    with self.assertRaises(ValueError):
      TipMountingShaft(name="shaft", tip_pickup_mode="core", cross_section_type="rectangle")


class ShaftHoldsItsTip(unittest.TestCase):
  """A mounting shaft carries its tip as a child, gripping it by the fitting depth."""

  def setUp(self):
    self.shaft = TipMountingShaft(name="shaft", tip_pickup_mode="core")
    self.tip = hamilton_tip_300uL(name="tip")

  def test_a_mounted_tip_sits_by_its_fitting_depth(self):
    self.shaft.mount_tip(self.tip)
    self.assertTrue(self.shaft.has_tip())
    self.assertEqual(
      self.tip.location,
      Coordinate(
        (self.shaft.get_size_x() - self.tip.get_size_x()) / 2,
        (self.shaft.get_size_y() - self.tip.get_size_y()) / 2,
        self.tip.fitting_depth - self.tip.get_size_z(),
      ),
    )

  def test_the_tip_bottom_is_what_reaches_below_the_channel(self):
    """Part of the tip is up inside the channel, so it does not reach its whole length down."""
    self.shaft.mount_tip(self.tip)
    bottom = self.shaft.tip_bottom()
    assert bottom is not None
    self.assertEqual(bottom.z, self.tip.fitting_depth - self.tip.get_size_z())
    self.shaft.release_tip()
    self.assertIsNone(self.shaft.tip_bottom())

  def test_a_grip_tool_sits_by_its_pick_up_location(self):
    """Its opening is off the centre of its body and 32 mm up, where the channel goes in."""
    tool = hamilton_core_gripper_tool(name="grip")
    self.shaft.mount_tip(tool)
    self.assertEqual(
      tool.location,
      Coordinate(
        self.shaft.get_size_x() / 2 - 18.0,
        self.shaft.get_size_y() / 2 - 4.25,
        8.0 - 32.0,
      ),
    )

  def test_a_turned_tool_is_still_taken_by_its_pick_up_location(self):
    """A tool parked facing the other way is picked up by the same point on it, so that point -
    not its origin, which the turn has moved to the far corner - lands on the shaft's axis."""
    tool = hamilton_core_gripper_tool(name="grip")
    tool.rotate(z=180)
    self.shaft.mount_tip(tool)

    assert tool.location is not None
    assert tool.pick_up_location is not None
    grip = tool.location + tool.pick_up_location.rotated(tool.rotation)
    self.assertEqual(
      Coordinate(grip.x, grip.y, 0),
      Coordinate(self.shaft.get_size_x() / 2, self.shaft.get_size_y() / 2, 0),
    )
    self.assertEqual(grip.z, tool.fitting_depth)

  def test_an_empty_shaft_equals_one_carrying_a_tip(self):
    other = TipMountingShaft(name="shaft", tip_pickup_mode="core")
    self.shaft.mount_tip(self.tip)
    self.assertEqual(self.shaft, other)


if __name__ == "__main__":
  unittest.main()
