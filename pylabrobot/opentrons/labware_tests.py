import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pylabrobot.opentrons.flex import FlexHead8
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.opentrons.labware import (
  LabwareRegistry,
  build_tip_rack_definition,
)
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import LabwareIdentity
from pylabrobot.resources import Resource, StandingTipRack, set_tip_tracking
from pylabrobot.resources.opentrons import (
  FlexDeck,
  flex_96_filtertiprack_50ul,
  load_ot_tip_rack,
  opentrons_96_filtertiprack_20ul,
)

TIP_RACK_NAMES = (
  "eppendorf_96_tiprack_1000ul_eptips",
  "eppendorf_96_tiprack_10ul_eptips",
  "geb_96_tiprack_1000ul",
  "geb_96_tiprack_10ul",
  "opentrons_96_filtertiprack_1000ul",
  "opentrons_96_filtertiprack_10ul",
  "opentrons_96_filtertiprack_200ul",
  "opentrons_96_filtertiprack_20ul",
  "opentrons_96_tiprack_1000ul",
  "opentrons_96_tiprack_10ul",
  "opentrons_96_tiprack_20ul",
  "opentrons_96_tiprack_300ul",
  "opentrons_flex_96_filtertiprack_1000ul",
  "opentrons_flex_96_filtertiprack_200ul",
  "opentrons_flex_96_filtertiprack_50ul",
  "opentrons_flex_96_tiprack_1000ul",
  "opentrons_flex_96_tiprack_200ul",
  "opentrons_flex_96_tiprack_50ul",
  "tipone_96_tiprack_200ul",
)


@pytest.mark.parametrize("ot_name", TIP_RACK_NAMES)
@pytest.mark.parametrize("for_flex", (False, True))
@pytest.mark.parametrize("with_tips", (False, True))
def test_catalogue_rack_preserves_geometry(ot_name: str, for_flex: bool, with_tips: bool) -> None:
  """Conversion preserves every well, including after a resource round trip."""
  fixture = Path(__file__).parent.parent / "testing" / "test_data" / "opentrons" / f"{ot_name}.json"
  expected = json.loads(fixture.read_text())
  original = load_ot_tip_rack(ot_name, "rack", with_tips=with_tips)
  restored = Resource.deserialize(original.serialize())
  assert isinstance(restored, StandingTipRack)
  for rack in (original, restored):
    actual = build_tip_rack_definition(rack, for_flex=for_flex)
    assert actual["namespace"] == "pylabrobot"
    assert actual["dimensions"] == expected["dimensions"]
    assert actual["ordering"] == expected["ordering"]
    for key in ("format", "isTiprack", "tipLength", "tipOverlap"):
      assert actual["parameters"][key] == expected["parameters"][key]
    assert actual["wells"].keys() == expected["wells"].keys()
    for well_name, expected_well in expected["wells"].items():
      actual_well = actual["wells"][well_name]
      assert actual_well["shape"] == expected_well["shape"]
      for key in ("x", "y", "z", "depth", "diameter", "totalLiquidVolume"):
        assert actual_well[key] == pytest.approx(expected_well[key]), (ot_name, well_name, key)
      assert actual_well["z"] + actual_well["depth"] == pytest.approx(
        expected_well["z"] + expected_well["depth"]
      )


class FlexTipRackRoundTripTests(unittest.IsolatedAsyncioTestCase):
  """Every Flex rack uploads its geometry and supports a full-column return."""

  async def test_pickup_and_return_for_all_flex_racks(self) -> None:
    """Pick up and return column 2 through the real driver with a mocked API."""
    set_tip_tracking(True)
    try:
      for name in (name for name in TIP_RACK_NAMES if name.startswith("opentrons_flex_")):
        with self.subTest(rack=name):
          deck = FlexDeck()
          rack = load_ot_tip_rack(name, "tips")
          deck.assign_child_at_slot(rack, "D1")
          original_tips = [spot.get_tip() for spot in rack.column(1)]
          api = make_api(pipettes=[("p1000_multi_flex", 8, 5.0, 1000.0, "left")])
          flex = make_flex(api=api, deck=deck)
          try:
            await flex.setup()
            head = flex.left_pipette
            self.assertIsInstance(head, FlexHead8)
            assert isinstance(head, FlexHead8)
            await head.pick_up_tips(rack.column(1))
            self.assertTrue(all(not spot.has_tip() for spot in rack.column(1)))
            for mounted, original in zip(head.get_mounted_tips(), original_tips):
              self.assertIs(mounted, original)
            await head.drop_tips(rack, column=1)
            self.assertEqual(head.get_mounted_tips(), [None] * 8)
            for spot, original in zip(rack.column(1), original_tips):
              self.assertIs(spot.get_tip(), original)
            api.define_labware.assert_awaited_once()
            commands = [(call.args[1], call.args[2]) for call in api.submit_command.await_args_list]
            for kind in ("pickUpTip", "dropTip"):
              self.assertEqual(
                [params["wellName"] for command, params in commands if command == kind], ["A2"]
              )
            self.assertEqual(
              [params["namespace"] for command, params in commands if command == "loadLabware"],
              ["pylabrobot"],
            )
          finally:
            await flex.disconnect()
    finally:
      set_tip_tracking(False)


class LabwareRegistryTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.protocol_run = AsyncMock(spec=OpentronsRun)
    self.registry = LabwareRegistry(self.protocol_run)
    self.rack = opentrons_96_filtertiprack_20ul("rack")
    self.identity = LabwareIdentity("opentrons", "opentrons_96_filtertiprack_20ul", 1)

  async def test_confirmed_move_updates_binding_and_removal_forgets_it(self):
    await self.registry.load(self.rack, "1", self.identity)
    self.registry.record_location(self.rack, "5")
    await self.registry.load(self.rack, "5", self.identity)
    self.protocol_run.load_labware.assert_awaited_once()
    self.registry.remove(self.rack)
    self.assertFalse(self.registry.is_loaded(self.rack))

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
  def test_flex_50ul_rack_preserves_pickup_height(self) -> None:
    """The official 50 µL rack's well top is 99 mm, independent of tip length."""
    rack = flex_96_filtertiprack_50ul("rack")
    definition = build_tip_rack_definition(rack, for_flex=True)
    well = definition["wells"]["A2"]

    self.assertAlmostEqual(well["z"] + well["depth"], 99.0)

  def test_flex_50ul_rack_preserves_well_diameter(self) -> None:
    """A circular tip opening retains its diameter through conversion."""
    rack = flex_96_filtertiprack_50ul("rack")
    tip = rack.get_item("A2").get_tip()
    definition = build_tip_rack_definition(rack, for_flex=True)

    self.assertAlmostEqual(definition["wells"]["A2"]["diameter"], tip.get_size_x())

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

  def test_custom_tip_rack_definition_uses_tip_length_as_well_depth(self) -> None:
    rack = opentrons_96_filtertiprack_20ul("rack")
    tip = rack.get_item("A1").get_tip()

    definition = build_tip_rack_definition(rack, tip, "custom")

    self.assertEqual(definition["ordering"][0], [f"{row}1" for row in "ABCDEFGH"])
    self.assertEqual(len(definition["wells"]), 96)
    self.assertEqual(definition["groups"][0]["metadata"], {})
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 0, "z": 0})
    self.assertEqual(definition["parameters"]["tipLength"], tip.get_size_z())
    self.assertEqual(definition["wells"]["A1"]["depth"], tip.get_size_z())
    self.assertIsInstance(definition["version"], int)

  def test_official_rack_round_trips_through_a_generated_definition(self) -> None:
    rack = opentrons_96_filtertiprack_20ul("rack")
    tip = rack.get_item("A1").get_tip()

    definition = build_tip_rack_definition(rack, tip, "generated")

    self.assertEqual(definition["namespace"], "pylabrobot")
    self.assertEqual(definition["parameters"]["format"], "96Standard")
    self.assertTrue(definition["parameters"]["isTiprack"])
    self.assertEqual(len(definition["wells"]), 96)
    self.assertEqual(definition["ordering"][0][0], "A1")
    self.assertAlmostEqual(definition["dimensions"]["xDimension"], rack.get_absolute_size_x())

    spot = rack.get_item("A1")
    assert spot.location is not None
    well = definition["wells"]["A1"]
    self.assertAlmostEqual(well["x"], spot.location.x + spot.get_absolute_size_x() / 2)
    self.assertAlmostEqual(well["y"], spot.location.y + spot.get_absolute_size_y() / 2)
