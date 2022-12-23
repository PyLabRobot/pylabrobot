""" Tests for the simulation backend. """

import json
import time
import unittest

import pytest
import requests
import websockets
import websockets.client

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import SerializingSavingBackend, SimulatorBackend
from pylabrobot.utils.testing import async_test
from pylabrobot.resources import STARLetDeck
from pylabrobot.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_EZWash,
  STF_L,
)


class SimulatorBackendSetupStopTests(unittest.TestCase):
  """ Tests for the setup and stop methods of the simulator backend. """

  @pytest.mark.timeout(20)
  def test_setup_stop(self):
    """ Test that the thread is started and stopped correctly. """

    backend = SimulatorBackend(open_browser=False)

    def setup_stop_single():
      backend.setup()
      self.assertIsNotNone(backend.loop)
      # wait for the server to start
      time.sleep(1)
      backend.stop()
      self.assertFalse(backend.has_connection())

    # setup and stop twice to ensure that everything is recycled correctly
    setup_stop_single()
    setup_stop_single()


class SimulatorBackendServerTests(unittest.TestCase):
  """ Tests for servers (ws/fs). """

  def setUp(self):
    super().setUp()
    self.backend = SimulatorBackend(open_browser=False)
    self.backend.setup()

    self.asyncSetUp()

  @async_test
  async def asyncSetUp(self):
    ws_port = self.backend.ws_port # port may change if port is already in use
    self.uri = f"ws://localhost:{ws_port}"
    self.client = await websockets.client.connect(self.uri)

  def tearDown(self):
    super().tearDown()
    self.backend.stop()

    self.asyncTearDown()

  @async_test
  async def asyncTearDown(self):
    await self.client.close()

  def test_get_index_html(self):
    """ Test that the index.html file is returned. """
    r = requests.get("http://localhost:1337/", timeout=10)
    self.assertEqual(r.status_code, 200)
    self.assertEqual(r.headers["Content-Type"], "text/html")

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

    self.backend.send_command("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    self.assertEqual(data["event"], "test")


class SimulatorBackendEventCatcher(SerializingSavingBackend, SimulatorBackend): # type: ignore
  """ Catches events that would be sent over the websocket for easy testing.

  This class inherits from SimulatorBackend to get the same functionality as the
  SimulatorBackend class.
  """


class SimulatorBackendCommandTests(unittest.TestCase):
  """ Tests for command sending using the simulator backend. """

  def setUp(self):
    super().setUp()

    self.backend = SimulatorBackendEventCatcher(open_browser=False)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.lh.setup()

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[0] = self.tip_rack = STF_L(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.deck.assign_child_resource(self.plt_car, rails=9)

    self.maxDiff = None

    self.backend.clear()

  def test_adjust_volume(self):
    self.backend.adjust_well_volume(self.plate, [[100]*12]*8)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "adjust_well_volume")

  def test_edit_tips(self):
    self.backend.edit_tips(self.tip_rack, [[True]*12]*8)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "edit_tips")

  def test_fill_tip_rack(self):
    self.backend.fill_tip_rack(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "edit_tips")

  def test_clear_tips(self):
    self.backend.fill_tip_rack(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "edit_tips")


if __name__ == "__main__":
  unittest.main()
