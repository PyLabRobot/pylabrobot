import json
import time
import unittest
import unittest.mock

import pytest
import requests
import websockets
import websockets.client

from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION
from pylabrobot.visualizer import Visualizer
from pylabrobot.resources import Coordinate, Resource, Cos_96_EZWash


class VisualizerSetupStopTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for the setup and stop methods of the visualizer backend. """

  @pytest.mark.timeout(20)
  async def test_setup_stop(self):
    """ Test that the thread is started and stopped correctly. """

    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False)

    async def setup_stop_single():
      await vis.setup()
      self.assertIsNotNone(vis.loop)
      # wait for the server to start
      time.sleep(1)
      await vis.stop()
      self.assertFalse(vis.has_connection())

    # setup and stop twice to ensure that everything is recycled correctly
    await setup_stop_single()
    await setup_stop_single()


class VisualizerServerTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for servers (ws/fs). """

  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    self.vis = Visualizer(self.r, open_browser=False)
    await self.vis.setup()

    ws_port = self.vis.ws_port # port may change if port is already in use
    self.uri = f"ws://localhost:{ws_port}"
    self.client = await websockets.client.connect(self.uri)

  async def asyncTearDown(self):
    await super().asyncTearDown()
    await self.vis.stop()
    await self.client.close()

  def test_get_index_html(self):
    """ Test that the index.html file is returned. """
    r = requests.get("http://localhost:1337/", timeout=10)
    self.assertEqual(r.status_code, 200)
    self.assertIn(r.headers["Content-Type"], ["text/html", "text/html; charset=utf-8"])

  async def test_connect(self):
    await self.client.send("{\"event\": \"ready\"}")
    response = await self.client.recv()
    response = json.loads(response)
    self.assertEqual(response, {
      "event": "set_root_resource",
      "data": {
        "resource": self.r.serialize(),
      },
      "id": "0001",
      "version": STANDARD_FORM_JSON_VERSION
    })

  async def test_event_sent(self):
    await self.client.send("{\"event\": \"ready\"}")
    _ = await self.client.recv() # set_root_resource
    _ = await self.client.recv() # set_state

    await self.vis.send_command("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    self.assertEqual(data["event"], "test")


class VisualizerCommandTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for command sending using the visualizer backend. """

  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.maxDiff = None
    self.r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    self.vis = Visualizer(self.r, open_browser=False)

    # mock the send_command method to catch the events
    self.vis.send_command = unittest.mock.AsyncMock() # type: ignore[method-assign]

    await self.vis.setup()

  async def test_assign_child_resource(self):
    """ Test that the assign_child_resource method sends the correct event. """
    child = Resource(size_x=100, size_y=100, size_z=100, name="child")
    self.r.assign_child_resource(child, location=Coordinate(0, 0, 0))
    time.sleep(0.1) # wait for the event to be sent
    self.vis.send_command.assert_called_once_with( # type: ignore[attr-defined]
      event="resource_assigned",
      data={
        "resource": child.serialize(),
        "state": child.serialize_all_state(),
        "parent_name": "root",
     },
     wait_for_response=False
    )

  async def test_resource_unassigned(self):
    """ Test that the unassign_child_resource method sends the correct event. """
    child = Resource(size_x=100, size_y=100, size_z=100, name="child")
    self.r.assign_child_resource(child, location=Coordinate(0, 0, 0))
    self.r.unassign_child_resource(child)
    time.sleep(0.1)

    self.vis.send_command.assert_called_with( # type: ignore[attr-defined]
      event="resource_unassigned",
      data={
        "resource_name": "child"
      },
      wait_for_response=False
    )

  async def test_state_updated(self):
    """ Test that the state_updated method sends the correct event. """
    plate = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.r.assign_child_resource(plate, location=Coordinate(0, 0, 0))
    plate.set_well_liquids((None, 500))
    time.sleep(0.1)
    self.vis.send_command.assert_called() # type: ignore[attr-defined]
    call_args = self.vis.send_command.call_args[1] # type: ignore[attr-defined]
    self.assertEqual(call_args["event"], "set_state")
    self.assertEqual(call_args["data"]["plate_01_well_11_7"]["liquids"], [[None, 500]])
