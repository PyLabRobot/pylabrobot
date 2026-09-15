import unittest
from typing import Any, Dict

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.core_gripper_tool import CoreGripperTool
from pylabrobot.resources.hamilton import hamilton_core_gripper_tool, hamilton_tip_1000uL
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.serializer import deserialize, serialize


class HeadToolTests(unittest.TestCase):
  """Tests for the head tools a channel can carry."""

  def test_tip_is_head_tool_resource(self):
    tip = Tip(False, 59.9, 400.0, 8.0, name="test_tip")
    self.assertIsInstance(tip, HeadTool)
    self.assertIsInstance(tip, Resource)
    self.assertEqual(tip.total_length, 59.9)
    self.assertEqual(tip.get_size_z(), 59.9)
    self.assertAlmostEqual(tip.extension, 51.9)

  def test_pick_up_location_defaults_to_top_centre(self):
    tip = Tip(False, 59.9, 400.0, 8.0, name="test_tip", size_x=8.2, size_y=8.2)
    self.assertEqual(tip.pick_up_location, Coordinate(4.1, 4.1, 59.9))

  def test_core_gripper_tool_pick_up_location_is_its_orifice(self):
    tool = hamilton_core_gripper_tool(name="core_gripper_tool")
    self.assertEqual(tool.pick_up_location, Coordinate(18.0, 4.25, 32.0))

  def test_pick_up_location_survives_serialization(self):
    tool = hamilton_core_gripper_tool(name="core_gripper_tool")
    restored = deserialize(serialize(tool))
    self.assertEqual(restored.pick_up_location, Coordinate(18.0, 4.25, 32.0))

  def test_head_tool_is_abstract(self):
    with self.assertRaises(TypeError):
      HeadTool(name="tool", size_x=1, size_y=1, size_z=1, fitting_depth=0)  # type: ignore[abstract]

  def test_same_kind_of_tip_shares_definition_not_identity(self):
    a = hamilton_tip_1000uL(name="rack_A1#0")
    b = hamilton_tip_1000uL(name="rack_B1#0")
    self.assertEqual(a.definition(), b.definition())
    self.assertNotEqual(a, b)
    self.assertEqual(len({a.definition(), b.definition()}), 1)

  def test_different_tips_have_different_definitions(self):
    a = Tip(False, 59.9, 400.0, 8.0, name="a")
    b = Tip(True, 59.9, 360.0, 8.0, name="b")
    self.assertNotEqual(a.definition(), b.definition())

  def test_tip_size_z_must_match_length(self):
    with self.assertRaises(ValueError):
      Tip(False, 59.9, 400.0, 8.0, name="test_tip", size_z=50.0)

  def test_deserialize_tip_without_resource_fields(self):
    """A tip serialized before tips were resources still deserializes."""
    legacy: Dict[str, Any] = {
      "type": "Tip",
      "name": "test_tip",
      "total_tip_length": 59.9,
      "has_filter": False,
      "nominal_volume": 300.0,
      "maximal_volume": 400.0,
      "fitting_depth": 8.0,
    }
    tip = deserialize(legacy)
    self.assertIsInstance(tip, Tip)
    self.assertEqual(tip.get_size_z(), 59.9)
    self.assertEqual(tip.name, "test_tip")

  def test_unnamed_tool_can_be_named_once(self):
    tool = hamilton_core_gripper_tool()
    self.assertFalse(tool.is_named)
    tool.name = "core_gripper_tool"
    self.assertTrue(tool.is_named)
    self.assertEqual(tool.name, "core_gripper_tool")
    with self.assertRaises(AttributeError):
      tool.name = "other"

  def test_named_tool_keeps_its_name(self):
    tip = Tip(False, 59.9, 400.0, 8.0, name="test_tip")
    with self.assertRaises(AttributeError):
      tip.name = "other"

  def test_core_gripper_tool(self):
    tool = hamilton_core_gripper_tool(name="core_gripper_tool")
    self.assertIsInstance(tool, HeadTool)
    self.assertNotIsInstance(tool, Tip)
    self.assertEqual(tool.total_length, 30.0)
    self.assertEqual(tool.fitting_depth, 8.0)
    self.assertEqual(tool.collar_height, 8.0)
    self.assertEqual(tool.extension, 22.0)
    self.assertEqual((tool.get_size_x(), tool.get_size_y(), tool.get_size_z()), (36.0, 8.346, 32.0))

  def test_core_gripper_tool_serialize(self):
    tool = hamilton_core_gripper_tool(name="core_gripper_tool")
    self.assertEqual(
      serialize(tool),
      {
        "type": "CoreGripperTool",
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
    self.assertIsInstance(restored, CoreGripperTool)
    self.assertEqual(restored, tool)
    self.assertEqual(restored.definition(), tool.definition())
