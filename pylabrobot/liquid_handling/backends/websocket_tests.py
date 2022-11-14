""" Tests for the simulation backend. """

import json
import unittest

import pytest
import websockets

from pylabrobot.liquid_handling.backends import WebSocketBackend
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

    self.backend.send_command("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    self.assertEqual(data["event"], "test")


if __name__ == "__main__":
  unittest.main()
