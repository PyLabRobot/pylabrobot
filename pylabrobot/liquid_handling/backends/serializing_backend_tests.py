import unittest

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.serializing_backend import SerializingSavingBackend
from pylabrobot.resources import (
  STARLetDeck,
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cor_96_wellplate_360ul_Fb,
  STF_L,
  Coordinate,
  no_tip_tracking,
  no_volume_tracking,
)
from pylabrobot.serializer import serialize


class SerializingBackendTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for the serializing backend """

  async def asyncSetUp(self) -> None:
    self.backend = SerializingSavingBackend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[0] = self.tip_rack = STF_L(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cor_96_wellplate_360ul_Fb(name="plate_01")
    self.plt_car[1] = self.other_plate = Cor_96_wellplate_360ul_Fb(name="plate_02")
    self.deck.assign_child_resource(self.plt_car, rails=9)

    self.backend.clear()

    self.maxDiff = None

  async def test_pick_up_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.lh.pick_up_tips([tip_spot])
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "pick_up_tips")
    self.assertEqual(self.backend.sent_commands[0]["data"], {
      "channels": [{
        "resource_name": tip_spot.name,
        "offset": serialize(Coordinate.zero()),
        "tip": serialize(tip),
      }], "use_channels": [0]})

  async def test_drop_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    self.lh.update_head_state({0: tip})

    tips = self.tip_rack["A1"]
    with no_tip_tracking():
      await self.lh.drop_tips(tips)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "drop_tips")
    self.assertEqual(self.backend.sent_commands[0]["data"], {
      "channels": [{
        "resource_name": tip_spot.name,
        "offset": serialize(Coordinate.zero()),
        "tip": serialize(tip),
      }], "use_channels": [0]})

  async def test_aspirate(self):
    well = self.plate.get_item("A1")
    well.tracker.set_liquids([(None, 10)])
    tip = self.tip_rack.get_tip(0)
    self.lh.update_head_state({0: tip})
    self.backend.clear()
    await self.lh.aspirate([well], vols=[10])
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "aspirate")
    self.assertEqual(self.backend.sent_commands[0]["data"], {
      "channels": [{
        "resource_name": well.name,
        "offset": serialize(Coordinate.zero()),
        "tip": tip.serialize(),
        "volume": 10,
        "flow_rate": None,
        "liquid_height": None,
        "blow_out_air_volume": None,
        "liquids": [[None, 10]],
      }], "use_channels": [0]})

  async def test_dispense(self):
    wells = self.plate["A1"]
    tip = self.tip_rack.get_tip(0)
    self.lh.update_head_state({0: tip})
    self.backend.clear()
    with no_volume_tracking():
      await self.lh.dispense(wells, vols=[10])
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "dispense")
    self.assertEqual(self.backend.sent_commands[0]["data"], {
      "channels": [{
        "resource_name": wells[0].name,
        "offset": serialize(Coordinate.zero()),
        "tip": tip.serialize(),
        "volume": 10,
        "flow_rate": None,
        "liquid_height": None,
        "blow_out_air_volume": None,
        "liquids": [[None, 10]],
      }], "use_channels": [0]})

  async def test_pick_up_tips96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "pick_up_tips96")
    self.assertEqual(self.backend.sent_commands[0]["data"], {
      "resource_name": self.tip_rack.name, "offset": serialize(Coordinate.zero())
    })

  async def test_drop_tips96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self.backend.clear()

    await self.lh.drop_tips96(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "drop_tips96")
    self.assertEqual(self.backend.sent_commands[0]["data"], {
      "resource_name": self.tip_rack.name, "offset": serialize(Coordinate.zero())
    })

  async def test_aspirate96(self):
    await self.test_pick_up_tips96() # pick up tips first
    self.backend.clear()

    tips = [channel.get_tip() for channel in self.lh.head96.values()]
    self.backend.clear()
    await self.lh.aspirate96(self.plate, volume=10)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "aspirate96")
    self.assertEqual(self.backend.sent_commands[0]["data"], {"aspiration": {
      "well_names": [well.name for well in self.plate.get_all_items()],
      "offset": serialize(Coordinate.zero()),
      "volume": 10,
      "flow_rate": None,
      "liquid_height": None,
      "blow_out_air_volume": None,
      "liquids": [[[None, 10]]]*96, # tuple, list of liquids per well, list of wells
      "tips": [serialize(tip) for tip in tips],
    }})

  async def test_dispense96(self):
    await self.test_aspirate96() # aspirate first
    self.backend.clear()

    tips = [channel.get_tip() for channel in self.lh.head96.values()]
    self.backend.clear()
    await self.lh.dispense96(self.plate, volume=10)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "dispense96")
    self.assertEqual(self.backend.sent_commands[0]["data"], {"dispense": {
      "well_names": [well.name for well in self.plate.get_all_items()],
      "offset": serialize(Coordinate.zero()),
      "volume": 10,
      "flow_rate": None,
      "liquid_height": None,
      "blow_out_air_volume": None,
      "liquids": [[[None, 10]]]*96, # tuple, list of liquids per well, list of wells
      "tips": [serialize(tip) for tip in tips],
    }})

  async def test_move(self):
    to = Coordinate(600, 200, 200)
    await self.lh.move_plate(self.plate, to=to)
    self.assertEqual(len(self.backend.sent_commands), 3) # move + resource unassign + assign
    self.assertEqual(self.backend.sent_commands[0]["command"], "move")
    self.assertEqual(self.backend.get_first_data_for_command("move"), {"move":
      {
        "resource_name": self.plate.name,
        "to": serialize(to),
        "intermediate_locations": [],
        "resource_offset": serialize(Coordinate.zero()),
        "destination_offset": serialize(Coordinate.zero()),
        "pickup_distance_from_top": 9.87,
        "get_direction": "FRONT",
        "put_direction": "FRONT",
      }})
