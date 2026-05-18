import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from pylabrobot_labware_library import (
  _markdown_library_entries,
  _resource_factory_registry,
  _should_instantiate_factory,
  build_labware_library_index,
)


class LabwareLibraryTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.index = build_labware_library_index("docs")
    cls.items_by_definition = {
      item["definition"]: item
      for item in cls.index["items"]
    }

  def test_library_includes_python_only_resource_definitions(self):
    markdown_entries = _markdown_library_entries("docs")

    self.assertNotIn("Microplate_96_Well", markdown_entries)
    self.assertIn("Microplate_96_Well", self.items_by_definition)
    self.assertTrue(self.items_by_definition["Microplate_96_Well"]["has_geometry"])

  def test_library_keeps_markdown_enrichment_when_available(self):
    item = self.items_by_definition["hamilton_96_tiprack_1000uL_filter"]

    self.assertEqual(item["manufacturer"], "Hamilton")
    self.assertEqual(item["section_path"], ["Consumables", "TipRacks"])
    self.assertIsNotNone(item["image"])
    self.assertTrue(item["has_geometry"])

  def test_offline_opentrons_factories_are_listed_without_instantiation(self):
    registry = _resource_factory_registry()
    definition = registry["opentrons_96_tiprack_300ul"]

    self.assertFalse(_should_instantiate_factory(definition))
    self.assertIn("opentrons_96_tiprack_300ul", self.items_by_definition)
    self.assertFalse(self.items_by_definition["opentrons_96_tiprack_300ul"]["has_geometry"])


if __name__ == "__main__":
  unittest.main()
