import unittest

from pylabrobot.resources import HTF_L, Cos_96_EZWash
from pylabrobot.liquid_handling.standard import (
  Pickup,
  Drop,
  Aspiration,
  Dispense,
)


class TestStandard(unittest.TestCase):
  """ Test for standard form classes. """

  def setUp(self) -> None:
    self.tip_rack = HTF_L("tiprack")
    self.plate = Cos_96_EZWash("plate")

  def test_pick_up_serialize(self):
    self.assertEqual(
      Pickup(
        resource=self.tip_rack.get_item("A1"),
        tip=self.tip_rack.get_tip("A1")
      ).serialize(),
      {
      "resource_name": "tiprack_tipspot_0_0",
      "offset": "default",
      "tip": self.tip_rack.get_tip("A1").serialize()
    })

  def test_pick_up_deserialize(self):
    tip = self.tip_rack.get_tip("A1")
    resource = self.tip_rack.get_item("A1")
    pickup = Pickup(resource=resource, tip=tip)

    self.assertEqual(Pickup.deserialize(pickup.serialize(), tip=tip, resource=resource), pickup)

  def test_drop_serialize(self):
    self.assertEqual(
      Drop(resource=self.tip_rack.get_item("A1"), tip=self.tip_rack.get_tip("A1")).serialize(),
      {
        "resource_name": "tiprack_tipspot_0_0",
        "offset": "default",
        "tip": self.tip_rack.get_tip("A1").serialize()
      })

  def test_drop_deserialize(self):
    tip = self.tip_rack.get_tip("A1")
    resource = self.tip_rack.get_item("A1")
    drop = Drop(resource=resource, tip=tip)

    self.assertEqual(Drop.deserialize(drop.serialize(), tip=tip, resource=resource), drop)

  def test_aspiration_serialize(self):
    tip = self.tip_rack.get_tip("A1")
    self.assertEqual(
      Aspiration(resource=self.plate.get_well("A1"), volume=100, tip=tip).serialize(), {
      "resource_name": "plate_well_0_0",
      "offset": "default",
      "volume": 100,
      "flow_rate": "default",
      "liquid_height": "default",
      "blow_out_air_volume": 0,
      "tip": self.tip_rack.get_tip("A1").serialize(),
      "liquid_class": "WATER"
    })

  def test_aspiration_deserialize(self):
    tip = self.tip_rack.get_tip("A1")
    resource = self.plate.get_well("A1")
    asp = Aspiration(resource=resource, volume=100, tip=tip)
    self.assertEqual(Aspiration.deserialize(asp.serialize(), resource=resource, tip=tip), asp)

  def test_dispense_serialize(self):
    tip = self.tip_rack.get_tip("A1")
    self.assertEqual(
      Dispense(resource=self.plate.get_well("A1"), volume=100, tip=tip).serialize(), {
      "resource_name": "plate_well_0_0",
      "offset": "default",
      "volume": 100,
      "flow_rate": "default",
      "liquid_height": "default",
      "blow_out_air_volume": 0,
      "tip": self.tip_rack.get_tip("A1").serialize(),
      "liquid_class": "WATER"
    })

  def test_dispense_deserialize(self):
    tip = self.tip_rack.get_tip("A1")
    resource = self.plate.get_well("A1")
    disp = Dispense(resource=resource, volume=100, tip=tip)
    self.assertEqual(Dispense.deserialize(disp.serialize(), resource=resource, tip=tip), disp)
