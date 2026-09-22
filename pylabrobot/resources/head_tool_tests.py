import unittest

from pylabrobot.resources.hamilton import (
  HamiltonCoreGripperTool,
  hamilton_core_gripper_tool,
  hamilton_tip_1000uL,
)
from pylabrobot.resources.tip import Tip
from pylabrobot.serializer import deserialize, serialize


class HeadToolTests(unittest.TestCase):
  """Tests for the head tools a channel can carry."""

  def test_hamilton_tip_diameter_follows_tip_size(self):
    from pylabrobot.resources.hamilton import hamilton_tip_10uL, hamilton_tip_300uL_filter

    for creator in (hamilton_tip_10uL, hamilton_tip_300uL_filter, hamilton_tip_1000uL):
      tip = creator(name="tip")
      self.assertEqual((tip.get_size_x(), tip.get_size_y()), (8.2, 8.2))

  def test_hamilton_collar_height_follows_tip_size(self):
    from pylabrobot.resources.hamilton import hamilton_tip_10uL, hamilton_tip_300uL
    from pylabrobot.resources.imcs.tip_racks import imcs_tip_1000uL

    self.assertEqual(hamilton_tip_10uL(name="tip").collar_height, 6.0)
    self.assertEqual(hamilton_tip_300uL(name="tip").collar_height, 8.0)
    self.assertEqual(hamilton_tip_1000uL(name="tip").collar_height, 10.0)
    self.assertEqual(imcs_tip_1000uL("tip").collar_height, 10.0)

  def test_a_grip_tool_states_what_a_tip_states(self):
    """A machine is told about a grip tool through the same fields as a tip, so it states them."""
    tip = hamilton_tip_1000uL(name="tip")
    tool = hamilton_core_gripper_tool(name="tool")
    self.assertEqual(tip.has_filter, False)
    self.assertEqual((tool.has_filter, tool.maximal_volume), (False, 1.0))
    self.assertNotEqual(tip.kind(), tool.kind())

  def test_two_tips_of_one_kind_are_one_kind(self):
    a = hamilton_tip_1000uL(name="rack_A1#0")
    b = hamilton_tip_1000uL(name="rack_B1#0")
    self.assertEqual(a.kind(), b.kind())

  def test_tips_that_differ_are_different_kinds(self):
    a = Tip("a", 8.2, 59.9, False, 400.0, 8.0)
    b = Tip("b", 8.2, 59.9, True, 360.0, 8.0)
    self.assertNotEqual(a.kind(), b.kind())

  def test_core_gripper_tool_serialize(self):
    tool = hamilton_core_gripper_tool(name="core_gripper_tool")
    self.assertEqual(
      serialize(tool),
      {
        "type": "HamiltonCoreGripperTool",
        "name": "core_gripper_tool",
        "size_x": 36.0,
        "size_y": 8.346,
        "size_z": 32.0,
        "category": "core_gripper_tool",
        "model": "hamilton_core_gripper_tool",
        "total_length": 30.0,
        "fitting_depth": 8.0,
        "collar_height": 8.0,
        "pick_up_location": {"x": 18.0, "y": 4.25, "z": 32.0, "type": "Coordinate"},
      },
    )

  def test_core_gripper_tool_deserialize(self):
    tool = hamilton_core_gripper_tool(name="core_gripper_tool")
    restored = deserialize(serialize(tool))
    self.assertIsInstance(restored, HamiltonCoreGripperTool)
    self.assertEqual(restored, tool)
    self.assertEqual(restored.kind(), tool.kind())
