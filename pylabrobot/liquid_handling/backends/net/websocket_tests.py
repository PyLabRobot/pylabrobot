""" Tests for the simulation backend. """

import json
import unittest
from typing import List

import pytest
import websockets

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.net.websocket import WebSocketBackend
from pylabrobot.liquid_handling.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_DW_1mL,
  STF_L
)
from pylabrobot.liquid_handling.resources.hamilton import STARLetDeck
from pylabrobot.utils.testing import async_test


class WebSocketBackendSetupStopTests(unittest.TestCase):
  """ Tests for the setup and stop methods of the websocket backend. """

  @pytest.mark.timeout(20)
  def test_setup_stop(self):
    """ Test that the thread is started and stopped correctly. """

    backend = WebSocketBackend()

    def setup_stop_single():
      backend.setup()
      self.assertIsNotNone(backend.loop)
      backend.stop()
      self.assertIsNone(backend.websocket)

    # setup and stop twice to ensure that everything is recycled correctly
    setup_stop_single()
    setup_stop_single()


class WebSocketBackendServerTests(unittest.TestCase):
  """ Tests for servers (ws/fs). """

  def setUp(self):
    super().setUp()
    self.backend = WebSocketBackend()
    self.backend.setup()

    self.asyncSetUp()

  @async_test
  async def asyncSetUp(self):
    ws_port = self.backend.ws_port # port may change if port is already in use
    self.uri = f"ws://localhost:{ws_port}"
    self.client = await websockets.connect(self.uri)

  def tearDown(self):
    super().tearDown()
    self.backend.stop()

    self.asyncTearDown()

  @async_test
  async def asyncTearDown(self):
    await self.client.close()

  @async_test
  async def test_connect(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    self.assertEqual(response, '{"event": "ready"}')

  @async_test
  async def test_event_sent(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    self.assertEqual(response, '{"event": "ready"}')

    self.backend.send_event("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    self.assertEqual(data["event"], "test")


class WebSocketBackendEventCatcher(WebSocketBackend):
  """ Catches events that would be sent over the websocket for easy testing. """
  def __init__(self):
    super().__init__()
    self.sent_datas = []

  def setup(self):
    self.setup_finished = True

  def send_event(self, event: str, wait_for_response: bool = True, **kwargs):
    data, _ = self._assemble_command(event, **kwargs)

    # Save command for testing in deserialize form.
    self.sent_datas.append(json.loads(data))

  def stop(self):
    pass

  def clear(self):
    self.sent_datas.clear()

  def get_commands(self, event) -> List[dict]:
    return [data for data in self.sent_datas if data["event"] == event]


class WebSocketBackendCommandTests(unittest.TestCase):
  """ Tests for command sending using LiquidHandler. """

  def setUp(self):
    self.backend = WebSocketBackendEventCatcher()
    self.lh = LiquidHandler(self.backend, deck=STARLetDeck())
    self.tip_car = TIP_CAR_480_A00("tip_car")
    self.tip_car[0] = STF_L("tips_1")
    self.plt_car = PLT_CAR_L5AC_A00("plt_car")
    self.plt_car[0] = Cos_96_DW_1mL("plate_1")
    self.lh.deck.assign_child_resource(self.tip_car, rails=1)
    self.lh.deck.assign_child_resource(self.plt_car, rails=10)
    self.lh.setup()

    self.maxDiff = None

  def assert_event_sent_n(self, event, times):
    self.assertEqual(len(self.backend.get_commands(event)), times)

  def assert_command_equal(self, right, left):
    right.pop("id", None)
    left.pop("id", None)
    self.assertEqual(right, left)

  def test_resources_assigned_setup(self):
    self.assert_event_sent_n("resource_assigned", times=2)

  def test_resources_assigned(self):
    self.backend.clear()
    tip_car = TIP_CAR_480_A00("tip_car_new")
    tip_car[0] = STF_L("tips_1_new")
    self.lh.deck.assign_child_resource(tip_car, rails=20)
    self.assert_event_sent_n("resource_assigned", times=1)

  def test_subresource_assigned(self):
    self.backend.clear()
    tip_car = TIP_CAR_480_A00("tip_car_new")
    self.lh.deck.assign_child_resource(tip_car, rails=20)
    self.assert_event_sent_n("resource_assigned", times=1)

    self.backend.clear()
    tip_car[0] = STF_L("tips_1_new")
    self.assert_event_sent_n("resource_assigned", times=1)

    self.backend.clear()
    tip_car[0] = None
    self.assert_event_sent_n("resource_unassigned", times=1)

  def test_tip_pickup(self):
    self.lh.pick_up_tips(self.tip_car[0].resource["A1"])
    self.assert_event_sent_n("pick_up_tips", times=1)

  def test_discard_tips(self):
    self.lh.discard_tips(self.tip_car[0].resource["A1"])
    self.assert_event_sent_n("discard_tips", times=1)

  def test_aspirate(self):
    self.lh.aspirate(self.plt_car[0].resource["A1:A2"], vols=[100, 100], liquid_classes=None)
    self.assert_event_sent_n("aspirate", times=1)
    self.assert_command_equal(self.backend.get_commands("aspirate")[0], {
      "event": "aspirate",
      "id": "0197",
      "version": "0.1.0",
      "channels": [
        {
          "resource": {
            "category": "well",
            "children": [],
            "location": {"x": 14.0, "y": 74.5, "z": 1.0},
            "name": "plate_1_well_0_0",
            "parent_name": "plate_1",
            "size_x": 9,
            "size_y": 9,
            "size_z": 9,
            "type": "Well"
          },
          "offset_z": 0,
          "volume": 100,
          "flow_rate": None,
        },
        {
          "resource": {
            "category": "well",
            "children": [],
            "location": {"x": 23.0, "y": 74.5, "z": 1.0},
            "name": "plate_1_well_1_0",
            "parent_name": "plate_1",
            "size_x": 9,
            "size_y": 9,
            "size_z": 9,
            "type": "Well"
          },
          "offset_z": 0,
          "volume": 100,
          "flow_rate": None,
        }
      ]
    })

  def test_dispense(self):
    self.lh.dispense(self.plt_car[0].resource["A1"], vols=[100], liquid_classes=None)
    self.assert_event_sent_n("dispense", times=1)
    self.assert_command_equal(self.backend.get_commands("dispense")[0], {
      "channels": [
        {
          "resource": {
            "category": "well",
            "children": [],
            "location": {"x": 14.0, "y": 74.5, "z": 1.0},
            "name": "plate_1_well_0_0",
            "parent_name": "plate_1",
            "size_x": 9,
            "size_y": 9,
            "size_z": 9,
            "type": "Well"
          },
          "volume": 100,
          "offset_z": 0,
          "flow_rate": None
        }
      ],
      "event": "dispense",
      "version": "0.1.0",
    })

  def test_pick_up_tips96(self):
    self.lh.pick_up_tips96("tip_car")
    self.assert_event_sent_n("pick_up_tips96", times=1)

  def test_discard_tips96(self):
    self.lh.discard_tips96("tip_car")
    self.assert_event_sent_n("discard_tips96", times=1)

  def test_aspirate96(self):
    self.lh.aspirate_plate(self.lh.get_resource("plate_1"), 100)
    self.assert_event_sent_n("aspirate96", times=1)

  def test_dispense96(self):
    self.lh.dispense_plate(self.lh.get_resource("plate_1"), 100)
    self.assert_event_sent_n("dispense96", times=1)


if __name__ == "__main__":
  unittest.main()
