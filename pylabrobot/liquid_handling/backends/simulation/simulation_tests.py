""" Tests for the simulation backend. """

import json
import time
import unittest

import pytest
import requests
import websockets

from pylabrobot.liquid_handling.backends.net.websocket_tests import (
  WebSocketBackendCommandTests,
  WebSocketBackendEventCatcher
)
from pylabrobot.liquid_handling.backends.simulation import SimulatorBackend


class SimulatorBackendSetupStopTests(unittest.TestCase):
  """ Tests for the setup and stop methods of the simulator backend. """

  @pytest.mark.timeout(20)
  def test_setup_stop(self):
    """ Test that the thread is started and stopped correctly. """

    backend = SimulatorBackend(open_browser=False)
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
    self.backend = SimulatorBackend(open_browser=False)
    self.backend.setup()

  async def asyncSetUp(self):
    await super().asyncSetUp()

    ws_port = self.backend.ws_port # port may change if port is already in use
    self.uri = f"ws://localhost:{ws_port}"
    self.client = await websockets.connect(self.uri)

  def tearDown(self):
    super().tearDown()
    self.backend.stop()

  async def asyncTearDown(self):
    await super().asyncTearDown()
    await self.client.close()

  def test_get_index_html(self):
    """ Test that the index.html file is returned. """
    r = requests.get("http://localhost:1337/", timeout=10)
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


class SimulatorBackendEventCatcher(WebSocketBackendEventCatcher, SimulatorBackend):
  """ Catches events that would be sent over the websocket for easy testing.

  This class inherits from SimulatorBackend to get the same functionality as the
  SimulatorBackend class.
  """

  pass

class SimulatorBackendCommandTests(WebSocketBackendCommandTests):
  """ Tests for command sending using the simulator backend. """

  def setUp(self):
    super().setUp()

    # hot swap the backend to use the simulator event catcher
    backend = SimulatorBackendEventCatcher()
    self.lh.backend = backend
    backend.setup()
    backend.sent_datas = self.backend.sent_datas
    self.backend = backend

  def test_send_simple_command(self):
    self.backend.send_event("test", test=True)
    self.assert_event_sent_n("test", times=1)
    self.assertEqual(self.backend.get_commands("test")[0],
      {"event": "test", "test": True, "id": "0001", "version": "0.1.0"})
    self.assert_command_equal(self.backend.get_commands("test")[0],
      {"event": "test", "test": True, "id": 0000, "version": "0.1.0"})

  def test_adjust_volume(self):
    self.backend.adjust_well_volume(self.plt_car[0].resource, [[100]*12]*8)
    self.assert_event_sent_n("adjust_well_volume", times=1)

  def test_edit_tips(self):
    self.backend.edit_tips(self.tip_car[0].resource, [[True]*12]*8)
    self.assert_event_sent_n("edit_tips", times=1)

  def test_fill_tips(self):
    self.backend.fill_tips(self.tip_car[0].resource)
    self.assert_event_sent_n("edit_tips", times=1)

  def test_clear_tips(self):
    self.backend.clear_tips(self.tip_car[0].resource)
    self.assert_event_sent_n("edit_tips", times=1)

if __name__ == "__main__":
  unittest.main()
