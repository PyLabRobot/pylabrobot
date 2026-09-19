"""The shared transport runs both device lifecycles without a socket."""

import unittest

from pylabrobot.opentrons import OT2, ChatterboxHTTP, Flex, OT2SingleChannelPipette
from pylabrobot.resources import set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import opentrons_96_filtertiprack_20ul


class SharedChatterboxTests(unittest.IsolatedAsyncioTestCase):
  async def test_ot2_transfer_with_shared_transport_and_declared_labware(self):
    io = ChatterboxHTTP(
      pipettes=[("p20_single_gen2", 1, 1, 20, "left")],
      robot_model="OT-2 Standard",
      api_version="8.7.0",
    )
    robot = OT2("offline", io=io)
    rack = opentrons_96_filtertiprack_20ul("tips")
    plate = celltreat_96_wellplate_350uL_Fb("plate")
    robot.deck.assign_child_at_slot(rack, 1)
    robot.deck.assign_child_at_slot(plate, 2)
    set_tip_tracking(True)
    set_volume_tracking(True)
    try:
      await robot.setup()
      pipette = robot.left_pipette
      assert isinstance(pipette, OT2SingleChannelPipette)
      plate.get_item("A1").tracker.set_volume(20)
      await pipette.pick_up_tip(rack.get_item("A1"))
      await pipette.aspirate(plate.get_item("A1"), volume=10)
      await pipette.dispense(plate.get_item("A2"), volume=10)
      self.assertEqual(plate.get_item("A1").tracker.get_used_volume(), 10)
      self.assertEqual(plate.get_item("A2").tracker.get_used_volume(), 10)
      self.assertEqual(io.commands[1]["params"]["loadName"], "opentrons_96_filtertiprack_20ul")
    finally:
      await robot.stop()
      set_tip_tracking(False)
      set_volume_tracking(False)

  async def test_flex_typed_health_and_instrument_queries(self):
    robot = Flex("offline", io=ChatterboxHTTP(gripper=True))
    await robot.connect()
    try:
      self.assertEqual((await robot.get_health()).model, "OT-3 Standard")
      instruments = await robot.get_instruments()
      self.assertEqual([item.instrument_type for item in instruments], ["pipette", "gripper"])
      self.assertEqual(instruments[0].maximum_volume, 1000)
      self.assertIsNone(robot.run_id)
    finally:
      await robot.disconnect()
