import copy
import unittest

from pylabrobot.liquid_handling import LiquidHandler, no_tip_tracking
from pylabrobot.liquid_handling.backends.serializing_backend import SerializingSavingBackend
from pylabrobot.liquid_handling.resources import STARLetDeck
from pylabrobot.liquid_handling.standard import (
  Pickup,
  Discard,
  Aspiration,
  Dispense,
  Move,
)
from pylabrobot.liquid_handling.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_EZWash,
  STF_L,
  Coordinate,
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
    tips = self.tip_rack["A1"]
    self.lh.pick_up_tips(tips)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "pick_up_tips")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(
      channels=[Pickup(resource=tips[0]).serialize()], use_channels=[0]))

  def test_discard_tips(self):
    tips = self.tip_rack["A1"]
    with no_tip_tracking():
      self.lh.discard_tips(tips)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "discard_tips")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(
      channels=[Discard(resource=tips[0]).serialize()], use_channels=[0]))

  def test_aspirate(self):
    wells = self.plate["A1"]
    self.lh.aspirate(wells, vols=10, liquid_classes=None)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "aspirate")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(
      channels=[Aspiration(resource=wells[0], volume=10).serialize()], use_channels=[0]))

  def test_dispense(self):
    wells = self.plate["A1"]
    self.lh.dispense(wells, vols=10, liquid_classes=None)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "dispense")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(
      channels=[Dispense(resource=wells[0], volume=10).serialize()], use_channels=[0]))

  def test_pick_up_tips96(self):
    self.lh.pick_up_tips96(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "pick_up_tips96")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(resource_name=self.tip_rack.name))

  def test_discard_tips96(self):
    self.lh.discard_tips96(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "discard_tips96")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(resource_name=self.tip_rack.name))

  def test_aspirate96(self):
    self.lh.aspirate_plate(self.plate, volume=10, liquid_class=None)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "aspirate96")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(aspiration=
      Aspiration(resource=self.plate, volume=10).serialize()))

  def test_dispense96(self):
    self.lh.dispense_plate(self.plate, volume=10, liquid_class=None)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "dispense96")
    self.assertEqual(self.backend.sent_commands[0]["data"], dict(dispense=
      Dispense(resource=self.plate, volume=10).serialize()))

  def test_move(self):
    to = Coordinate(600, 200, 200)
    plate_before = copy.deepcopy(self.plate) # we need to copy the plate because it will be modified
    self.lh.move_plate(self.plate, to=to)
    print([cmd["command"] for cmd in self.backend.sent_commands])
    self.assertEqual(len(self.backend.sent_commands), 3)
    self.assertEqual(self.backend.sent_commands[0]["command"], "move")
    self.assertEqual(self.backend.get_first_data_for_command("move"), dict(move=
      Move(
        resource=plate_before,
        to=to,
        pickup_distance_from_top=13.2,
      ).serialize()))
