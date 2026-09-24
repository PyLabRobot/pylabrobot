import unittest
from typing import Any, Dict

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import (
  HamiltonCoreGripperTool,
  hamilton_core_gripper_tool,
  hamilton_tip_1000uL,
  hamilton_tip_1000uL_filter,
)
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.serializer import serialize


class HeadToolTests(unittest.TestCase):
  """Tests for the head tools a channel can carry."""

  def test_pick_up_location_can_be_unspecified(self):
    tip = Tip(
      name="test_tip",
      diameter=8.2,
      size_z=59.9,
      has_filter=False,
      maximal_volume=400,
      fitting_depth=8,
    )
    self.assertIsNone(tip.pick_up_location)

  def test_hamilton_tip_diameter_follows_tip_size(self):
    from pylabrobot.resources.hamilton import (
      hamilton_teaching_needle_5000uL,
      hamilton_tip_10uL,
      hamilton_tip_300uL_filter,
      hamilton_tip_5000uL,
    )

    for creator in (hamilton_tip_10uL, hamilton_tip_300uL_filter, hamilton_tip_1000uL):
      tip = creator(name="tip")
      self.assertEqual((tip.get_size_x(), tip.get_size_y()), (8.2, 8.2))
      self.assertIsNone(tip.pick_up_location)
    for creator in (hamilton_tip_5000uL, hamilton_teaching_needle_5000uL):
      with self.assertRaises(NotImplementedError):
        creator(name="tip")

  def test_hamilton_collar_height_follows_tip_size(self):
    from pylabrobot.resources.hamilton import (
      hamilton_tip_10uL,
      hamilton_tip_300uL,
      hamilton_tip_5000uL,
    )
    from pylabrobot.resources.imcs.tip_racks import imcs_tip_1000uL

    self.assertEqual(hamilton_tip_10uL(name="tip").collar_height, 6.0)
    self.assertEqual(hamilton_tip_300uL(name="tip").collar_height, 8.0)
    self.assertEqual(hamilton_tip_1000uL(name="tip").collar_height, 10.0)
    with self.assertRaises(ValueError):
      imcs_tip_1000uL(name="tip").collar_height
    with self.assertRaises(NotImplementedError):
      hamilton_tip_5000uL(name="tip").collar_height

  def test_tips_filter_tips_and_grip_tools_are_different_kinds(self):
    tip = hamilton_tip_1000uL(name="tip")
    filter_tip = hamilton_tip_1000uL_filter(name="filter_tip")
    tool = hamilton_core_gripper_tool(name="tool")
    self.assertEqual(len({tip.kind(), filter_tip.kind(), tool.kind()}), 3)

  def test_names_and_locations_do_not_change_the_kind(self):
    holder = Resource(name="holder", size_x=100, size_y=100, size_z=100)
    a = hamilton_tip_1000uL(name="rack_A1#0")
    b = hamilton_tip_1000uL(name="rack_B1#0")
    holder.assign_child_resource(b, location=Coordinate(10, 20, 30))
    self.assertEqual(a.kind(), b.kind())

  def test_tips_that_differ_in_geometry_or_filter_are_different_kinds(self):
    def make_tip(size_z: float, has_filter: bool) -> Tip:
      return Tip(
        name="tip",
        diameter=8.2,
        size_z=size_z,
        has_filter=has_filter,
        maximal_volume=400,
        fitting_depth=8,
      )

    self.assertNotEqual(make_tip(59.9, False).kind(), make_tip(50.0, False).kind())
    self.assertNotEqual(make_tip(59.9, False).kind(), make_tip(59.9, True).kind())

  def test_kind_leaves_serialization_unchanged(self):
    holder = Resource(name="holder", size_x=100, size_y=100, size_z=100)
    tip = hamilton_tip_1000uL(name="tip")
    holder.assign_child_resource(tip, location=Coordinate(1, 2, 3))
    before = tip.serialize()
    tip.kind()
    self.assertEqual(tip.serialize(), before)

  def test_deserialize_tip_definition(self):
    """A tip definition loads its name and dimensions through the resource loader."""
    legacy: Dict[str, Any] = {
      "type": "Tip",
      "name": "test_tip",
      "size_z": 59.9,
      "diameter": 8.2,
      "has_filter": False,
      "nominal_volume": 300.0,
      "maximal_volume": 400.0,
      "fitting_depth": 8.0,
    }
    tip = Tip.deserialize(legacy)
    self.assertIsInstance(tip, Tip)
    self.assertEqual(tip.get_size_z(), 59.9)
    self.assertEqual(tip.name, "test_tip")

  def test_named_tool_cannot_be_renamed(self):
    tool = hamilton_core_gripper_tool(name="core_gripper_tool")
    self.assertEqual(tool.name, "core_gripper_tool")
    with self.assertRaises(AttributeError):
      tool.name = "other"

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
        "grip_line_height": 2.0,
        "fitting_depth": 8.0,
        "collar_height": 10.0,
        "pick_up_location": {"x": 18.0, "y": 4.25, "z": 32.0, "type": "Coordinate"},
      },
    )

  def test_core_gripper_tool_deserialize(self):
    tool = hamilton_core_gripper_tool(name="core_gripper_tool")
    restored = HamiltonCoreGripperTool.deserialize(tool.serialize())
    self.assertIsInstance(restored, HamiltonCoreGripperTool)
    self.assertEqual(restored, tool)
    self.assertEqual(restored.kind(), tool.kind())
