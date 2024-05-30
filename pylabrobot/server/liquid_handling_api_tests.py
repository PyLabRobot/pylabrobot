import time
from typing import cast
import unittest

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import SerializingSavingBackend
from pylabrobot.resources import (
  Plate,
  TipRack,
  HTF_L,
  Cos_96_EZWash,
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  no_tip_tracking,
)
from pylabrobot.resources.hamilton import HamiltonDeck, STARLetDeck
from pylabrobot.serializer import serialize
from pylabrobot.server.liquid_handling_server import create_app


def build_layout() -> HamiltonDeck:
  # copied from liquid_handler_tests.py, can we make this shared?
  tip_car = TIP_CAR_480_A00(name="tip_carrier")
  tip_car[0] = HTF_L(name="tip_rack_01")

  plt_car = PLT_CAR_L5AC_A00(name="plate_carrier")
  plt_car[0] = plate = Cos_96_EZWash(name="aspiration plate")
  plate.get_item("A1").tracker.set_liquids([(None, 400)])

  deck = STARLetDeck()
  deck.assign_child_resource(tip_car, rails=1)
  deck.assign_child_resource(plt_car, rails=21)
  return deck


class LiquidHandlingApiGeneralTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = SerializingSavingBackend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.app = create_app(lh=self.lh)
    self.base_url = ""

  def test_get_index(self):
    with self.app.test_client() as client:
      response = client.get(self.base_url + "/")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.data, b"PLR Liquid Handling API")

  def test_setup(self): # TODO: Figure out how we can configure LH
    with self.app.test_client() as client:
      response = client.post(self.base_url + "/setup")
      self.assertEqual(response.status_code, 200)

      self.assertIn(response.json.get("status"), {"running", "succeeded"})

      time.sleep(0.1)
      assert self.lh.setup_finished

  def test_stop(self):
    with self.app.test_client() as client:
      response = client.post(self.base_url + "/stop")
      self.assertEqual(response.status_code, 200)
      self.assertIn(response.json.get("status"), {"running", "succeeded"})

      assert not self.lh.setup_finished

  async def test_status(self):
    with self.app.test_client() as client:
      response = client.get(self.base_url + "/status")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.json, {"status": "stopped"})

      await self.lh.setup()
      response = client.get(self.base_url + "/status")
      self.assertEqual(response.status_code, 200)
      self.assertIn(response.json.get("status"), {"running", "succeeded"})

  def test_load_labware(self):
    with self.app.test_client() as client:
      # Post with no data
      response = client.post(self.base_url + "/labware",
                             headers={"Content-Type": "application/json"})
      self.assertEqual(response.status_code, 400)
      self.assertEqual(response.json, {"error": "json data must be a dict"})

      # Post with invalid data
      response = client.post(self.base_url + "/labware", json={"foo": "bar"})
      self.assertEqual(response.status_code, 400)
      self.assertEqual(response.json, {"error": "missing key in json data: 'deck'"})

      # Post with valid data
      deck = build_layout()
      response = client.post(self.base_url + "/labware", json={"deck": deck.serialize()})
      self.assertEqual(response.json, {"status": "ok"})
      self.assertEqual(response.status_code, 200)
      self.assertEqual(self.lh.deck, deck)


class LiquidHandlingApiOpsTests(unittest.TestCase):
  def setUp(self) -> None:
    self.backend = SerializingSavingBackend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.app = create_app(lh=self.lh)
    self.base_url = ""

    deck = build_layout()
    with self.app.test_client() as client:
      response = client.post(self.base_url + "/labware", json={"deck": deck.serialize()})
      assert response.status_code == 200
      assert self.lh.deck == deck
      assert self.lh.deck.resources == deck.resources

    client.post(self.base_url + "/setup")
    time.sleep(0.5)

  def test_tip_pickup(self):
    with self.app.test_client() as client:
      tip_rack = cast(TipRack, self.lh.deck.get_resource("tip_rack_01"))
      tip_spot = tip_rack.get_item("A1")
      with no_tip_tracking():
        tip = tip_spot.get_tip()
      response = client.post(
        self.base_url + "/pick-up-tips",
        json={"channels": [{
          "resource_name": tip_spot.name,
          "tip": serialize(tip),
          "offset": None,
        }], "use_channels": [0]})
      self.assertIn(response.json.get("status"), {"running", "succeeded"})
      self.assertEqual(response.status_code, 200)

  def test_drop_tip(self):
    with self.app.test_client() as client:
      tip_rack = cast(TipRack, self.lh.deck.get_resource("tip_rack_01"))
      tip_spot = tip_rack.get_item("A1")
      with no_tip_tracking():
        tip = tip_spot.get_tip()

      self.test_tip_pickup() # Pick up a tip first

      response = client.post(
        self.base_url + "/drop-tips",
        json={"channels": [{
          "resource_name": tip_spot.name,
          "tip": serialize(tip),
          "offset": None,
        }], "use_channels": [0]})
      self.assertIn(response.json.get("status"), {"running", "succeeded"})
      self.assertEqual(response.status_code, 200)

  def test_aspirate(self):
    with no_tip_tracking():
      tip = cast(TipRack, self.lh.deck.get_resource("tip_rack_01")).get_tip("A1")
    self.test_tip_pickup() # pick up a tip first
    with self.app.test_client() as client:
      well = cast(Plate, self.lh.deck.get_resource("aspiration plate")).get_item("A1")
      response = client.post(
        self.base_url + "/aspirate",
        json={"channels": [{
          "resource_name": well.name,
          "volume": 10,
          "tip": serialize(tip),
          "offset": None,
          "liquids": [[None, 10]],
          "flow_rate": None,
          "liquid_height": None,
          "blow_out_air_volume": 0,
        }], "use_channels": [0]})
      self.assertIn(response.json.get("status"), {"running", "succeeded"})
      self.assertEqual(response.status_code, 200)

  def test_dispense(self):
    with no_tip_tracking():
      tip = cast(TipRack, self.lh.deck.get_resource("tip_rack_01")).get_tip("A1")
    self.test_aspirate() # aspirate first
    with self.app.test_client() as client:
      well = cast(Plate, self.lh.deck.get_resource("aspiration plate")).get_item("A1")
      response = client.post(
        self.base_url + "/dispense",
        json={"channels": [{
          "resource_name": well.name,
          "volume": 10,
          "tip": serialize(tip),
          "offset": None,
          "liquids": [[None, 10]],
          "flow_rate": None,
          "liquid_height": None,
          "blow_out_air_volume": 0,
        }], "use_channels": [0]})
      self.assertIn(response.json.get("status"), {"running", "succeeded"})
      self.assertEqual(response.status_code, 200)
