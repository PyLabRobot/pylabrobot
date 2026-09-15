import unittest
from typing import Any, Dict

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import hamilton_tip_1000uL
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.serializer import deserialize


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

  def test_named_tool_keeps_its_name(self):
    tip = Tip(False, 59.9, 400.0, 8.0, name="test_tip")
    with self.assertRaises(AttributeError):
      tip.name = "other"
