"""PLR geometry and safe sequencing for Flex liquid operations."""

import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.opentrons import FlexHead8
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import Coordinate, cor_96_wellplate_360uL_Fb, no_volume_tracking
from pylabrobot.resources.opentrons import flex_96_filtertiprack_50ul


class PipettingPrimitivesTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    """Mount tips with mocked API replies; leave the plate unregistered."""
    self.api = make_api(pipettes=[("p1000_multi_flex", 8, 5, 1000, "left")])
    self.flex = make_flex("offline", api=self.api)
    self.rack = flex_96_filtertiprack_50ul("tips")
    self.plate = cor_96_wellplate_360uL_Fb("plate")
    self.flex.deck.assign_child_at_slot(self.rack, "D1")
    self.flex.deck.assign_child_at_slot(self.plate, "C2")
    await self.flex.setup()
    head = self.flex.left_pipette
    assert isinstance(head, FlexHead8)
    self.head: FlexHead8 = head
    await self.head.pick_up_tips(self.rack.column(0))
    self.api.submit_command.reset_mock()

  async def asyncTearDown(self):
    """Close the mocked run without issuing cleanup movement."""
    await self.flex.disconnect()

  async def test_plr_column_position_without_server_labware(self):
    """Position from cavity geometry and prime above the plate, before descent."""
    with (
      patch.object(self.flex, "_ensure_labware_loaded", AsyncMock(side_effect=AssertionError)),
      no_volume_tracking(),
    ):
      await self.head.aspirate(self.plate.column(0), 1, flow_rate=1, liquid_height=12.67)
    commands = self.api.submit_command.await_args_list
    self.assertEqual(
      [c.args[1] for c in commands],
      [
        "moveToCoordinates",
        "prepareToAspirate",
        "savePosition",
        "moveRelative",
        "aspirateInPlace",
        "savePosition",
        "moveRelative",
      ],
    )
    self.assertEqual(commands[0].args[2]["coordinates"], {"x": 178.3, "y": 181.2, "z": 109.0})
    self.assertEqual(commands[0].args[2]["minimumZHeight"], 109.0)
    self.assertAlmostEqual(commands[3].args[2]["distance"], 16.2 - 100)
    self.assertEqual(commands[3].args[2]["axis"], "z")
    self.assertEqual(commands[4].args[2]["volume"], 1)
    self.assertEqual(commands[-1].args[2]["axis"], "z")

  async def test_dispense_uses_updated_plr_location_and_cavity_floor(self):
    """PLR placement and caller offsets determine XYZ, without a robot load."""
    assert self.plate.location is not None
    self.plate.location = self.plate.location + Coordinate(z=60)
    with no_volume_tracking():
      await self.head.dispense(self.plate.column(1), 1, offset=Coordinate(x=2, y=-1, z=0.5))
    commands = self.api.submit_command.await_args_list
    self.assertEqual(
      [c.args[1] for c in commands],
      [
        "moveToCoordinates",
        "savePosition",
        "moveRelative",
        "dispenseInPlace",
        "savePosition",
        "moveRelative",
      ],
    )
    self.assertEqual(commands[0].args[2]["coordinates"], {"x": 189.3, "y": 180.2, "z": 109.0})
    self.assertAlmostEqual(commands[2].args[2]["distance"], 60 + 3.53 + 1 + 0.5 - 100)

  async def test_missing_cavity_floor_is_rejected_before_motion(self):
    """Never guess a cavity floor from an Opentrons load name."""
    for well in self.plate.column(0):
      well._material_z_thickness = None
    with self.assertRaisesRegex(ValueError, "material_z_thickness"), no_volume_tracking():
      await self.head.aspirate(self.plate.column(0), 1)
    self.assertEqual(self.api.submit_command.await_args_list, [])
