import unittest
from typing import Any, Dict

from pylabrobot.resources.hamilton import (
  HamiltonCoreGripperTool,
  hamilton_core_gripper_tool,
  hamilton_tip_1000uL,
)
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
