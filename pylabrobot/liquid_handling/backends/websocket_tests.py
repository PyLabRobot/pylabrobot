import json
import unittest

import pytest
import websockets
import websockets.client

from pylabrobot.liquid_handling.backends import WebSocketBackend


class WebSocketBackendSetupStopTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for the setup and stop methods of the websocket backend. """

  @pytest.mark.timeout(20)
  async def test_setup_stop(self):
    """ Test that the thread is started and stopped correctly. """

    backend = WebSocketBackend(num_channels=8)

    async def setup_stop_single():
      await backend.setup()
      self.assertIsNotNone(backend.loop)
      await backend.stop()
      self.assertFalse(backend.has_connection())

    # setup and stop twice to ensure that everything is recycled correctly
    await setup_stop_single()
    await setup_stop_single()


class WebSocketBackendServerTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for servers (ws/fs). """

  async def asyncSetUp(self):
    await super().asyncSetUp()

    self.backend = WebSocketBackend(num_channels=8)
    await self.backend.setup()

    ws_port = self.backend.ws_port # port may change if port is already in use
    self.uri = f"ws://localhost:{ws_port}"
    self.client = await websockets.client.connect(self.uri)

  async def asyncTearDown(self):
    await super().asyncTearDown()
    await self.backend.stop()
    await self.client.close()

  async def test_connect(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    self.assertEqual(response, '{"event": "ready"}')

  async def test_event_sent(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    self.assertEqual(response, '{"event": "ready"}')

    await self.backend.send_command("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    self.assertEqual(data["event"], "test")
