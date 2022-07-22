""" Tests for the simulation backend. """

import asyncio
import json
import time
import unittest

import pytest
import requests
import websockets

from pyhamilton.liquid_handling import LiquidHandler
from pyhamilton.liquid_handling.backends.simulation import SimulationBackend
from pyhamilton.liquid_handling.resources import (
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

  def get_command(self, event):
    for data in self.sent_datas:
      if data["event"] == event:
        self.sent_datas.remove(data)
        return data
    return None


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

  def assert_event_sent(self, event):
    self.assertIsNotNone(self.backend.get_command(event))

  def assert_event_not_sent(self, event):
    self.assertIsNone(self.backend.get_command(event))

  def assert_event_sent_once(self, event):
    self.assert_event_sent(event)
    self.assert_event_not_sent(event)

  def test_resources_assigned_setup(self):
    self.assert_event_sent("resource_assigned")
    self.assert_event_sent("resource_assigned")
    self.assert_event_not_sent("resource_assigned")

  def test_resources_assigned(self):
    self.backend.clear()
    tip_car = TIP_CAR_480_A00("tip_car_new")
    tip_car[0] = STF_L("tips_1_new")
    self.lh.assign_resource(tip_car, rails=20)
    self.assert_event_sent_once("resource_assigned")

  def test_subresource_assigned(self):
    self.backend.clear()
    tip_car = TIP_CAR_480_A00("tip_car_new")
    self.lh.assign_resource(tip_car, rails=20)
    self.assert_event_sent_once("resource_assigned")

    tip_car[0] = STF_L("tips_1_new")
    self.assert_event_sent_once("resource_assigned")
    self.assert_event_sent_once("resource_unassigned")

    tip_car[0] = None
    self.assert_event_sent_once("resource_assigned")
    self.assert_event_sent_once("resource_unassigned")

  def test_tip_pickup(self):
    self.lh.pickup_tips("tip_car", "A1")
    self.assert_event_sent_once("pickup_tips")

  def test_discard_tips(self):
    self.lh.discard_tips("tip_car", "A1")
    self.assert_event_sent_once("discard_tips")

  def test_aspirate(self):
    self.lh.aspirate("plt_car", ("A1", 100))
    self.assert_event_sent_once("aspirate")

  def test_dispense(self):
    self.lh.dispense("plt_car", ("A1", 100))
    self.assert_event_sent_once("dispense")

  def test_pickup_tips96(self):
    self.lh.pickup_tips96("tip_car")
    self.assert_event_sent_once("pickup_tips96")

  def test_discard_tips96(self):
    self.lh.discard_tips96("tip_car")
    self.assert_event_sent_once("discard_tips96")

  def test_aspirate96(self):
    self.lh.aspirate96("plt_car", [[True]*12]*8, 100)
    self.assert_event_sent_once("aspirate96")

  def test_dispense96(self):
    self.lh.dispense96("plt_car", [[True]*12]*8, 100)
    self.assert_event_sent_once("dispense96")

  def test_adjust_volume(self):
    self.backend.adjust_well_volume(self.plt_car[0], [[100]*12]*8)
    self.assert_event_sent_once("adjust_well_volume")

  def test_place_tips(self):
    self.backend.place_tips(self.tip_car[0], [[True]*12]*8)
    self.assert_event_sent_once("edit_tips")

  def test_fill_tips(self):
    self.backend.fill_tips(self.tip_car[0])
    self.assert_event_sent_once("edit_tips")

  def test_remove_tips(self):
    self.backend.remove_tips(self.tip_car[0], [[True]*12]*8)
    self.assert_event_sent_once("edit_tips")

  def test_clear_tips(self):
    self.backend.clear_tips(self.tip_car[0])
    self.assert_event_sent_once("edit_tips")


if __name__ == "__main__":
  unittest.main()
