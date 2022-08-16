""" Tests for the simulation backend. """

import json
import time
import unittest
from typing import List

import pytest
import requests
import websockets

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.simulation import SimulationBackend
from pylabrobot.liquid_handling.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_DW_1mL,
  STF_L
)

class SimulatorBackendSetupStopTests(unittest.TestCase):
  """ Tests for the setup and stop methods of the simulator backend. """

  @pytest.mark.timeout(20)
  def test_setup_stop(self):
    """ Test that the thread is started and stopped correctly. """

    backend = SimulationBackend(open_browser=False)
    backend.setup()
    time.sleep(2)
    self.assertIsNotNone(backend.loop)
    backend.stop()
    self.assertIsNone(backend.websocket)

    backend.setup()
    time.sleep(2)
    self.assertIsNotNone(backend.loop)
    backend.stop()
    self.assertIsNone(backend.websocket)

class SimulatorBackendServerTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for servers (ws/fs). """

  def setUp(self):
    super().setUp()
    self.backend = SimulationBackend(open_browser=False)
    self.backend.setup()

  async def asyncSetUp(self):
    await super().asyncSetUp()

    self.uri = "ws://localhost:2121"
    self.client = await websockets.connect(self.uri)

  def tearDown(self):
    super().tearDown()
    self.backend.stop()

  async def asyncTearDown(self):
    await super().asyncTearDown()
    await self.client.close()

  def test_get_index_html(self):
    """ Test that the index.html file is returned. """
    r = requests.get("http://localhost:1337/")
    self.assertEqual(r.status_code, 200)
    self.assertEqual(r.headers["Content-Type"], "text/html")

  async def test_connect(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    self.assertEqual(response, '{"event": "ready"}')

  async def test_event_sent(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    self.assertEqual(response, '{"event": "ready"}')

    self.backend.send_event("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    self.assertEqual(data["event"], "test")


class SimulatorBackendEventCatcher(SimulationBackend):
  """ Catches events that would be sent over the websocket for easy testing. """
  def __init__(self):
    super().__init__(open_browser=False)
    self.sent_datas = []

  def setup(self):
    self.setup_finished = True

  def send_event(self, event: str, wait_for_response: bool = True, **kwargs):
    id_ = super()._generate_id()
    data = dict(event=event, id=id_, **kwargs)
    self.sent_datas.append(data)

  def stop(self):
    pass

  def clear(self):
    self.sent_datas.clear()

  def get_commands(self, event) -> List[dict]:
    return [data for data in self.sent_datas if data["event"] == event]


class SimulatorBackendCommandTests(unittest.TestCase):
  """ Tests for command sending using LiquidHandler. """

  def setUp(self):
    self.backend = SimulatorBackendEventCatcher()
    self.lh = LiquidHandler(self.backend)
    self.tip_car = TIP_CAR_480_A00("tip_car")
    self.tip_car[0] = STF_L("tips_1")
    self.plt_car = PLT_CAR_L5AC_A00("plt_car")
    self.plt_car[0] = Cos_96_DW_1mL("plate_1")
    self.lh.assign_resource(self.tip_car, rails=1)
    self.lh.assign_resource(self.plt_car, rails=10)
    self.lh.setup()

    self.maxDiff = None

  def assert_event_sent(self, event):
    self.assertGreaterEqual(event, times=1)

  def assert_event_not_sent(self, event):
    self.assert_event_sent_n(event, times=0)

  def assert_event_sent_n(self, event, times):
    self.assertEqual(len(self.backend.get_commands(event)), times)

  def assert_command_equal(self, right, left):
    right.pop("id", None)
    left.pop("id", None)
    self.assertEqual(right, left)

  def test_resources_assigned_setup(self):
    # 2 carriers, 1 tip resource, 96 tip, 1 plate, 96 wells
    self.assert_event_sent_n("resource_assigned", times=2 + 1 + 96 + 1 + 96)

  def test_resources_assigned(self):
    self.backend.clear()
    tip_car = TIP_CAR_480_A00("tip_car_new")
    tip_car[0] = STF_L("tips_1_new")
    self.lh.assign_resource(tip_car, rails=20)
    self.assert_event_sent_n("resource_assigned", times=1 + 1 + 96)

  def test_subresource_assigned(self):
    self.backend.clear()
    tip_car = TIP_CAR_480_A00("tip_car_new")
    self.lh.assign_resource(tip_car, rails=20)
    self.assert_event_sent_n("resource_assigned", times=1)

    tip_car[0] = STF_L("tips_1_new")
    self.assert_event_sent_n("resource_assigned", times=1 + 1 + 96)

    tip_car[0] = None
    self.assert_event_sent_n("resource_unassigned", times=1 + 96)

  def test_tip_pickup(self):
    self.lh.pickup_tips(self.tip_car[0].resource["A1"])
    self.assert_event_sent_n("pickup_tips", times=1)

  def test_discard_tips(self):
    self.lh.discard_tips(self.tip_car[0].resource["A1"])
    self.assert_event_sent_n("discard_tips", times=1)

  def test_aspirate(self):
    self.lh.aspirate(self.plt_car[0].resource["A1:A2"], vols=[100, 100])
    self.assert_event_sent_n("aspirate", times=1)
    self.assert_command_equal(self.backend.get_commands("aspirate")[0], {
      "event": "aspirate",
      "id": "0197",
      "channels": [
        {
          "resource": {
            "category": "well",
            "location": {"x": 0.0, "y": 0.0, "z": 0},
            "name": "plate_1_well_0_0",
            "size_x": 9,
            "size_y": 9,
            "size_z": 9,
            "type": "Well"
          },
          "volume": 100,
          "liquid_class": {
            "device": ["CHANNELS_1000uL"],
            "tip_type": "STANDARD_VOLUME_TIP_300uL",
            "dispense_mode": 2,
            "pressure_lld": 0,
            "max_height_difference": 0,
            "flow_rate": [100, 120],
            "mix_flow_rate": [100, 1],
            "air_transport_volume": [0, 0],
            "blowout_volume": [0, 0],
            "swap_speed": [2, 2],
            "settling_time": [1, 0],
            "over_aspirate_volume": 0,
            "clot_retract_height": 0,
            "stop_flow_rate": 5,
            "stop_back_volume": 0,
            "correction_curve": {
              "5": 6.5,
              "10": 11.9,
              "20": 23.2,
              "50": 55.1,
              "100": 107.2,
              "200": 211.0,
              "300": 313.5,
              "0": 0
            }
          }
        },
        {
          "resource": {
            "category": "well",
            "location": {"x": 9.0, "y": 0.0, "z": 0},
            "name": "plate_1_well_1_0",
            "size_x": 9,
            "size_y": 9,
            "size_z": 9,
            "type": "Well"
          },
          "volume": 100,
          "liquid_class": {
            "device": ["CHANNELS_1000uL"],
            "tip_type": "STANDARD_VOLUME_TIP_300uL",
            "dispense_mode": 2,
            "pressure_lld": 0,
            "max_height_difference": 0,
            "flow_rate": [100, 120],
            "mix_flow_rate": [100, 1],
            "air_transport_volume": [0, 0],
            "blowout_volume": [0, 0],
            "swap_speed": [2, 2],
            "settling_time": [1, 0],
            "over_aspirate_volume": 0,
            "clot_retract_height": 0,
            "stop_flow_rate": 5,
            "stop_back_volume": 0,
            "correction_curve": {
              "5": 6.5,
              "10": 11.9,
              "20": 23.2,
              "50": 55.1,
              "100": 107.2,
              "200": 211.0,
              "300": 313.5,
              "0": 0
            }
          }
        }
      ]
    })

  def test_dispense(self):
    self.lh.dispense(self.plt_car[0].resource["A1"], vols=[100])
    self.assert_event_sent_n("dispense", times=1)
    self.assert_command_equal(self.backend.get_commands("dispense")[0], {
      "channels": [
        {
          "resource": {
            "category": "well",
            "location": {"x": 0.0, "y": 0.0, "z": 0},
            "name": "plate_1_well_0_0",
            "size_x": 9,
            "size_y": 9,
            "size_z": 9,
            "type": "Well"
          },
          "volume": 100,
          "liquid_class": {
            "device": ["CHANNELS_1000uL"],
            "tip_type": "STANDARD_VOLUME_TIP_300uL",
            "dispense_mode": 2,
            "pressure_lld": 0,
            "max_height_difference": 0,
            "flow_rate": [100, 120],
            "mix_flow_rate": [100, 1],
            "air_transport_volume": [0, 0],
            "blowout_volume": [0, 0],
            "swap_speed": [2, 2],
            "settling_time": [1, 0],
            "over_aspirate_volume": 0,
            "clot_retract_height": 0,
            "stop_flow_rate": 5,
            "stop_back_volume": 0,
            "correction_curve": {
              "5": 6.5,
              "10": 11.9,
              "20": 23.2,
              "50": 55.1,
              "100": 107.2,
              "200": 211.0,
              "300": 313.5,
              "0": 0
            }
          }
        }
      ],
      "event": "dispense",
    })

  def test_pickup_tips96(self):
    self.lh.pickup_tips96("tip_car")
    self.assert_event_sent_n("pickup_tips96", times=1)

  def test_discard_tips96(self):
    self.lh.discard_tips96("tip_car")
    self.assert_event_sent_n("discard_tips96", times=1)

  def test_aspirate96(self):
    self.lh.aspirate96("plt_car", 100, [[True]*12]*8)
    self.assert_event_sent_n("aspirate96", times=1)

  def test_dispense96(self):
    self.lh.dispense96("plt_car", 100, [[True]*12]*8)
    self.assert_event_sent_n("dispense96", times=1)

  def test_adjust_volume(self):
    self.backend.adjust_well_volume(self.plt_car[0], [[100]*12]*8)
    self.assert_event_sent_n("adjust_well_volume", times=1)

  def test_place_tips(self):
    self.backend.place_tips(self.tip_car[0], [[True]*12]*8)
    self.assert_event_sent_n("edit_tips", times=1)

  def test_fill_tips(self):
    self.backend.fill_tips(self.tip_car[0])
    self.assert_event_sent_n("edit_tips", times=1)

  def test_remove_tips(self):
    self.backend.remove_tips(self.tip_car[0], [[True]*12]*8)
    self.assert_event_sent_n("edit_tips", times=1)

  def test_clear_tips(self):
    self.backend.clear_tips(self.tip_car[0])
    self.assert_event_sent_n("edit_tips", times=1)


if __name__ == "__main__":
  unittest.main()
