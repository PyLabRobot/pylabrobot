import unittest
from unittest.mock import AsyncMock

from pylabrobot.opentrons.labware import LabwareRegistry, build_tip_rack_definition
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import LabwareIdentity
from pylabrobot.resources.opentrons import opentrons_96_filtertiprack_20ul


class LabwareRegistryTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.protocol_run = AsyncMock(spec=OpentronsRun)
    self.registry = LabwareRegistry(self.protocol_run)
    self.rack = opentrons_96_filtertiprack_20ul("rack")
    self.identity = LabwareIdentity("opentrons", "opentrons_96_filtertiprack_20ul", 1)

  async def test_lookup_does_not_load_or_allocate_missing_labware(self) -> None:
    self.assertFalse(self.registry.is_loaded(self.rack))
    with self.assertRaises(KeyError):
      self.registry.get(self.rack)
    self.protocol_run.load_labware.assert_not_awaited()
    self.protocol_run.define_labware.assert_not_awaited()

  async def test_successful_load_is_recorded_once_and_changed_location_is_rejected(self) -> None:
    await self.registry.load(self.rack, "1", self.identity)
    binding = self.registry.get(self.rack)
    await self.registry.load(self.rack, "1", self.identity)
    self.assertIs(self.registry.get(self.rack), binding)
    self.protocol_run.load_labware.assert_awaited_once()
    with self.assertRaisesRegex(ValueError, "loaded in slot 1"):
      await self.registry.load(self.rack, "5", self.identity)
    self.protocol_run.load_labware.assert_awaited_once()
    self.assertEqual(binding.slot, "1")

  async def test_failed_load_does_not_publish_a_binding(self) -> None:
    self.protocol_run.load_labware.side_effect = RuntimeError("load failed")
    with self.assertRaisesRegex(RuntimeError, "load failed"):
      await self.registry.load(self.rack, "1", self.identity)
    self.assertFalse(self.registry.is_loaded(self.rack))

  async def test_distinct_resource_objects_do_not_inherit_each_others_binding(self) -> None:
    await self.registry.load(self.rack, "1", self.identity)
    replacement = opentrons_96_filtertiprack_20ul("rack")
    self.assertFalse(self.registry.is_loaded(replacement))
    with self.assertRaises(KeyError):
      self.registry.get(replacement)

  async def test_custom_load_uses_the_definition_receipt(self) -> None:
    self.protocol_run.define_labware.return_value = LabwareIdentity("pylabrobot", "uploaded", 2)
    definition = build_tip_rack_definition(self.rack, self.rack.get_item("A1").get_tip(), "custom")
    await self.registry.load(self.rack, "5", self.identity, definition)
    binding = self.registry.get(self.rack)
    self.assertEqual(binding.identity, LabwareIdentity("pylabrobot", "uploaded", 2))
    self.protocol_run.load_labware.assert_awaited_once_with(
      binding.identity, "5", binding.labware_id, "rack"
    )


class LabwareConversionTests(unittest.TestCase):
  def test_definition_building_does_not_change_the_resource_or_its_tips(self) -> None:
    rack = opentrons_96_filtertiprack_20ul("rack")
    tip = rack.get_item("A1").get_tip()
    before = [spot.serialize_state() for spot in rack.get_all_items()]
    counters = [spot._tip_counter for spot in rack.get_all_items()]
    first = build_tip_rack_definition(rack, tip, "custom")
    second = build_tip_rack_definition(rack, tip, "custom")
    self.assertEqual(first, second)
    self.assertEqual([spot.serialize_state() for spot in rack.get_all_items()], before)
    self.assertEqual([spot._tip_counter for spot in rack.get_all_items()], counters)
    self.assertIs(rack.get_item("A1").get_tip(), tip)
