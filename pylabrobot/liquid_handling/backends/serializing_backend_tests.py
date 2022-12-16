import copy
import unittest

from pylabrobot.liquid_handling import LiquidHandler, no_tip_tracking, no_volume_tracking
from pylabrobot.liquid_handling.backends.serializing_backend import SerializingSavingBackend
from pylabrobot.liquid_handling.resources import (
  STARLetDeck,
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_EZWash,
  STF_L,
  Coordinate,
)
from pylabrobot.liquid_handling.standard import (
  Pickup,
  Drop,
  Aspiration,
  Dispense,
  Move,
)


class SerializingBackendTests(unittest.TestCase):
  """ Tests for the serializing backend """

  def setUp(self) -> None:
    self.backend = SerializingSavingBackend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.lh.setup()

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[0] = self.tip_rack = STF_L(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.plt_car[1] = self.other_plate = Cos_96_EZWash(name="plate_02", with_lid=True)
    self.deck.assign_child_resource(self.plt_car, rails=9)

    self.backend.clear()

    self.maxDiff = None

  def test_pick_up_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    self.lh.pick_up_tips([tip_spot])
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "pick_up_tips")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(
      channels=[Pickup(resource=tip_spot, tip=tip).serialize()],
      use_channels=[0]))

  def test_drop_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    self.lh.update_head_state({0: tip})

    tips = self.tip_rack["A1"]
    with no_tip_tracking():
      self.lh.drop_tips(tips)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "drop_tips")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(
      channels=[Drop(resource=tip_spot, tip=tip).serialize()],
      use_channels=[0]))

  def test_aspirate(self):
    well = self.plate.get_item("A1")
    well.tracker.set_used_volume(10)
    tip = self.tip_rack.get_tip(0)
    self.lh.update_head_state({0: tip})
    self.lh.aspirate([well], vols=10)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "aspirate")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(
      channels=[Aspiration(resource=well, volume=10, tip=tip).serialize()], use_channels=[0]))

  def test_dispense(self):
    wells = self.plate["A1"]
    tip = self.tip_rack.get_tip(0)
    self.lh.update_head_state({0: tip})
    with no_volume_tracking():
      self.lh.dispense(wells, vols=10)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "dispense")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(
      channels=[Dispense(resource=wells[0], volume=10, tip=tip).serialize()], use_channels=[0]))

  def test_pick_up_tips96(self):
    self.lh.pick_up_tips96(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "pick_up_tips96")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(resource_name=self.tip_rack.name))

  def test_drop_tips96(self):
    self.lh.drop_tips96(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "drop_tips96")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(resource_name=self.tip_rack.name))

  def test_aspirate96(self):
    self.test_pick_up_tips96() # pick up tips first
    self.backend.clear()

    tip = self.tip_rack.get_tip(0) # FIXME:
    self.lh.aspirate_plate(self.plate, volume=10)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "aspirate96")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(aspiration=
      Aspiration(resource=self.plate, volume=10, tip=tip).serialize()))

  def test_dispense96(self):
    self.test_pick_up_tips96() # pick up tips first
    self.backend.clear()

    tip = self.tip_rack.get_tip(0) # FIXME:
    self.lh.dispense_plate(self.plate, volume=10)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "dispense96")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(dispense=
      Dispense(resource=self.plate, volume=10, tip=tip).serialize()))

  def test_move(self):
    to = Coordinate(600, 200, 200)
    plate_before = copy.deepcopy(self.plate) # we need to copy the plate because it will be modified
    self.lh.move_plate(self.plate, to=to)
    self.assertEqual(len(self.backend.sent_commands), 3)
    self.assertEqual(self.backend.sent_commands[0]["command"], "move")
    self.assertEqual(self.backend.get_first_data_for_command("move"), dict(move=
      Move(
        resource=plate_before,
        to=to,
        pickup_distance_from_top=13.2,
      ).serialize()))
