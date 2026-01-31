import unittest
from unittest.mock import AsyncMock

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.serializing_backend import (
  SerializingBackend,
)
from pylabrobot.resources import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  Coordinate,
  Cor_96_wellplate_360ul_Fb,
  STARLetDeck,
  hamilton_96_tiprack_300uL_filter,
  no_tip_tracking,
  no_volume_tracking,
)
from pylabrobot.serializer import serialize


class _TestSerializingBackend(SerializingBackend):
  send_command = AsyncMock()


class SerializingBackendTests(unittest.IsolatedAsyncioTestCase):
  """Tests for the serializing backend"""

  async def asyncSetUp(self) -> None:
    self.backend = _TestSerializingBackend(num_channels=8)
    self.backend.send_command.reset_mock()
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[0] = self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cor_96_wellplate_360ul_Fb(name="plate_01")
    self.plt_car[1] = self.other_plate = Cor_96_wellplate_360ul_Fb(name="plate_02")
    self.deck.assign_child_resource(self.plt_car, rails=9)

    self.backend.send_command.reset_mock()

    self.maxDiff = None

  async def test_pick_up_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.lh.pick_up_tips([tip_spot])
    self.backend.send_command.assert_called_once_with(
      command="pick_up_tips",
      data={
        "channels": [
          {
            "resource_name": tip_spot.name,
            "offset": serialize(Coordinate.zero()),
            "tip": serialize(tip),
          }
        ],
        "use_channels": [0],
      },
    )

  async def test_drop_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    self.lh.update_head_state({0: tip})

    tips = self.tip_rack["A1"]
    with no_tip_tracking():
      await self.lh.drop_tips(tips)
    self.backend.send_command.assert_called_once_with(
      command="drop_tips",
      data={
        "channels": [
          {
            "resource_name": tip_spot.name,
            "offset": serialize(Coordinate.zero()),
            "tip": serialize(tip),
          }
        ],
        "use_channels": [0],
      },
    )

  async def test_aspirate(self):
    well = self.plate.get_item("A1")
    well.tracker.set_volume(10)
    tip = self.tip_rack.get_tip(0)
    self.lh.update_head_state({0: tip})
    self.backend.send_command.reset_mock()
    await self.lh.aspirate([well], vols=[10])
    self.backend.send_command.assert_called_once_with(
      command="aspirate",
      data={
        "channels": [
          {
            "resource_name": well.name,
            "offset": serialize(Coordinate.zero()),
            "tip": tip.serialize(),
            "volume": 10,
            "flow_rate": None,
            "liquid_height": None,
            "blow_out_air_volume": None,
            "mix": None,
          }
        ],
        "use_channels": [0],
      },
    )

  async def test_dispense(self):
    wells = self.plate["A1"]
    tip = self.tip_rack.get_tip(0)
    self.lh.update_head_state({0: tip})
    self.backend.send_command.reset_mock()
    with no_volume_tracking():
      await self.lh.dispense(wells, vols=[10])
    self.backend.send_command.assert_called_once_with(
      command="dispense",
      data={
        "channels": [
          {
            "resource_name": wells[0].name,
            "offset": serialize(Coordinate.zero()),
            "tip": tip.serialize(),
            "volume": 10,
            "flow_rate": None,
            "liquid_height": None,
            "blow_out_air_volume": None,
            "mix": None,
          }
        ],
        "use_channels": [0],
      },
    )

  async def test_pick_up_tips96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self.backend.send_command.assert_called_once_with(
      command="pick_up_tips96",
      data={
        "resource_name": self.tip_rack.name,
        "offset": serialize(Coordinate.zero()),
      },
    )

  async def test_drop_tips96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self.backend.send_command.reset_mock()

    await self.lh.drop_tips96(self.tip_rack)
    self.backend.send_command.assert_called_once_with(
      command="drop_tips96",
      data={
        "resource_name": self.tip_rack.name,
        "offset": serialize(Coordinate.zero()),
      },
    )

  async def test_aspirate96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self.backend.send_command.reset_mock()

    tips = [channel.get_tip() for channel in self.lh.head96.values()]
    await self.lh.aspirate96(self.plate, volume=10)
    self.backend.send_command.assert_called_once_with(
      command="aspirate96",
      data={
        "aspiration": {
          "well_names": [well.name for well in self.plate.get_all_items()],
          "offset": serialize(Coordinate.zero()),
          "volume": 10,
          "flow_rate": None,
          "liquid_height": None,
          "blow_out_air_volume": None,
          "tips": [serialize(tip) for tip in tips],
        }
      },
    )

  async def test_dispense96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    tips = [channel.get_tip() for channel in self.lh.head96.values()]
    await self.lh.aspirate96(self.plate, volume=10)
    self.backend.send_command.reset_mock()

    await self.lh.dispense96(self.plate, volume=10)
    self.backend.send_command.assert_called_once_with(
      command="dispense96",
      data={
        "dispense": {
          "well_names": [well.name for well in self.plate.get_all_items()],
          "offset": serialize(Coordinate.zero()),
          "volume": 10.0,
          "flow_rate": None,
          "liquid_height": None,
          "blow_out_air_volume": None,
          "tips": [serialize(tip) for tip in tips],
        }
      },
    )

  async def test_move(self):
    to = Coordinate(600, 200, 200)
    await self.lh.move_plate(self.plate, to=to)

    # Should have called pick_up_resource and drop_resource
    calls = self.backend.send_command.call_args_list
    self.assertEqual(len(calls), 2)

    # Check pick_up_resource call
    self.assertEqual(calls[0].kwargs["command"], "pick_up_resource")
    self.assertEqual(
      calls[0].kwargs["data"],
      {
        "resource_name": self.plate.name,
        "offset": serialize(Coordinate.zero()),
        "pickup_distance_from_top": 9.87,
        "direction": "FRONT",
      },
    )

    # Check drop_resource call
    self.assertEqual(calls[1].kwargs["command"], "drop_resource")
    self.assertEqual(
      calls[1].kwargs["data"],
      {
        "resource_name": self.plate.name,
        "destination": serialize(to),
        "offset": serialize(Coordinate.zero()),
        "pickup_distance_from_top": 9.87,
        "pickup_direction": "FRONT",
        "drop_direction": "FRONT",
        "rotation": 0,
      },
    )
