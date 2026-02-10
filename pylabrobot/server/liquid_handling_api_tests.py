import logging
import time
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import PropertyMock

from pylabrobot import Config
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.resources import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  Cor_96_wellplate_360ul_Fb,
  Plate,
  TipRack,
  hamilton_96_tiprack_1000uL_filter,
  no_tip_tracking,
)
from pylabrobot.resources.hamilton import HamiltonDeck, STARLetDeck
from pylabrobot.serializer import serialize
from pylabrobot.server.liquid_handling_server import create_app


def _create_mock_backend(num_channels: int = 8):
  """Create a mock LiquidHandlerBackend with the specified number of channels."""
  mock = unittest.mock.create_autospec(LiquidHandlerBackend, instance=True)
  type(mock).num_channels = PropertyMock(return_value=num_channels)
  type(mock).num_arms = PropertyMock(return_value=1)
  type(mock).head96_installed = PropertyMock(return_value=True)
  mock.can_pick_up_tip.return_value = True
  return mock


def build_layout() -> HamiltonDeck:
  # copied from liquid_handler_tests.py, can we make this shared?
  tip_car = TIP_CAR_480_A00(name="tip_carrier")
  tip_car[0] = hamilton_96_tiprack_1000uL_filter(name="tip_rack_01")

  plt_car = PLT_CAR_L5AC_A00(name="plate_carrier")
  plt_car[0] = plate = Cor_96_wellplate_360ul_Fb(name="aspiration plate")
  plate.get_item("A1").tracker.set_volume(400)

  deck = STARLetDeck()
  deck.assign_child_resource(tip_car, rails=1)
  deck.assign_child_resource(plt_car, rails=21)
  return deck


def _wait_for_task_done(base_url, client, task_id):
  while True:
    response = client.get(base_url + f"/tasks/{task_id}")
    if response.json is None:
      raise RuntimeError("No JSON in response: " + response.text)
    if response.json.get("status") == "running":
      time.sleep(0.1)
    else:
      return response


class LiquidHandlingApiGeneralTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = _create_mock_backend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.app = create_app(lh=self.lh)
    self.base_url = ""

  def test_get_index(self):
    with self.app.test_client() as client:
      response = client.get(self.base_url + "/")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.data, b"PLR Liquid Handling API")

  def test_setup(self):  # TODO: Figure out how we can configure LH
    with self.app.test_client() as client:
      task = client.post(self.base_url + "/setup")
      response = _wait_for_task_done(self.base_url, client, task.json.get("id"))
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.json.get("status"), "succeeded")

      time.sleep(0.1)
      assert self.lh.setup_finished

  def test_stop(self):
    with self.app.test_client() as client:
      task = client.post(self.base_url + "/setup")
      response = _wait_for_task_done(self.base_url, client, task.json.get("id"))

      task = client.post(self.base_url + "/stop")
      response = _wait_for_task_done(self.base_url, client, task.json.get("id"))
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.json.get("status"), "succeeded")

      assert not self.lh.setup_finished

  async def test_status(self):
    with self.app.test_client() as client:
      response = client.get(self.base_url + "/status")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.json, {"status": "stopped"})

      await self.lh.setup()
      response = client.get(self.base_url + "/status")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.json.get("status"), "running")

      await self.lh.stop()
      response = client.get(self.base_url + "/status")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.json.get("status"), "stopped")

  def test_load_labware(self):
    with self.app.test_client() as client:
      # Post with no data
      response = client.post(
        self.base_url + "/labware",
        headers={"Content-Type": "application/json"},
      )
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
    self.backend = _create_mock_backend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.app = create_app(lh=self.lh)
    self.base_url = ""

    deck = build_layout()
    with self.app.test_client() as client:
      response = client.post(self.base_url + "/labware", json={"deck": deck.serialize()})
      assert response.status_code == 200
      assert self.lh.deck == deck
      assert self.lh.deck.get_all_children() == deck.get_all_children()

    client.post(self.base_url + "/setup")
    time.sleep(0.5)

  def test_tip_pickup(self):
    with self.app.test_client() as client:
      tip_rack = cast(TipRack, self.lh.deck.get_resource("tip_rack_01"))
      tip_spot = tip_rack.get_item("A1")
      with no_tip_tracking():
        tip = tip_spot.get_tip()
      task = client.post(
        self.base_url + "/pick-up-tips",
        json={
          "channels": [
            {
              "resource_name": tip_spot.name,
              "tip": serialize(tip),
              "offset": None,
            }
          ],
          "use_channels": [0],
        },
      )
      response = _wait_for_task_done(self.base_url, client, task.json.get("id"))
      self.assertEqual(response.json.get("status"), "succeeded")
      self.assertEqual(response.status_code, 200)

  def test_drop_tip(self):
    with self.app.test_client() as client:
      tip_rack = cast(TipRack, self.lh.deck.get_resource("tip_rack_01"))
      tip_spot = tip_rack.get_item("A1")
      with no_tip_tracking():
        tip = tip_spot.get_tip()

      self.test_tip_pickup()  # Pick up a tip first

      task = client.post(
        self.base_url + "/drop-tips",
        json={
          "channels": [
            {
              "resource_name": tip_spot.name,
              "tip": serialize(tip),
              "offset": None,
            }
          ],
          "use_channels": [0],
        },
      )
      response = _wait_for_task_done(self.base_url, client, task.json.get("id"))
      self.assertEqual(response.json.get("status"), "succeeded")
      self.assertEqual(response.status_code, 200)

  def test_aspirate(self):
    with no_tip_tracking():
      tip = cast(TipRack, self.lh.deck.get_resource("tip_rack_01")).get_tip("A1")
    self.test_tip_pickup()  # pick up a tip first
    with self.app.test_client() as client:
      well = cast(Plate, self.lh.deck.get_resource("aspiration plate")).get_item("A1")
      task = client.post(
        self.base_url + "/aspirate",
        json={
          "channels": [
            {
              "resource_name": well.name,
              "volume": 10.0,
              "tip": serialize(tip),
              "offset": {
                "type": "Coordinate",
                "x": 0,
                "y": 0,
                "z": 0,
              },
              "flow_rate": None,
              "liquid_height": None,
              "blow_out_air_volume": 0,
            }
          ],
          "use_channels": [0],
        },
      )
      print(task)
      response = _wait_for_task_done(self.base_url, client, task.json.get("id"))
      self.assertEqual(response.json.get("status"), "succeeded")
      self.assertEqual(response.status_code, 200)

  def test_dispense(self):
    with no_tip_tracking():
      tip = cast(TipRack, self.lh.deck.get_resource("tip_rack_01")).get_tip("A1")
    self.test_aspirate()  # aspirate first
    with self.app.test_client() as client:
      well = cast(Plate, self.lh.deck.get_resource("aspiration plate")).get_item("A1")
      task = client.post(
        self.base_url + "/dispense",
        json={
          "channels": [
            {
              "resource_name": well.name,
              "volume": 10,
              "tip": serialize(tip),
              "offset": {
                "type": "Coordinate",
                "x": 0,
                "y": 0,
                "z": 0,
              },
              "flow_rate": None,
              "liquid_height": None,
              "blow_out_air_volume": 0,
            }
          ],
          "use_channels": [0],
        },
      )
      response = _wait_for_task_done(self.base_url, client, task.json.get("id"))
      self.assertEqual(response.json.get("status"), "succeeded")
      self.assertEqual(response.status_code, 200)

  def test_config(self):
    cfg = Config(logging=Config.Logging(log_dir=Path("logs"), level=logging.CRITICAL))
    with self.app.test_client() as client:
      logger = logging.getLogger("pylabrobot")
      cur_level = logger.level
      response = client.post(self.base_url + "/config", json=cfg.as_dict)
      new_level = logging.getLogger("pylabrobot").level
      self.assertEqual(response.json, cfg.as_dict)
      self.assertEqual(response.status_code, 200)
      self.assertEqual(new_level, logging.CRITICAL)
      self.assertNotEqual(cur_level, new_level)
