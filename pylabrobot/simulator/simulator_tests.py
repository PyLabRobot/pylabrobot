""" Tests for the simulation backend. """

import json
import time
import unittest
import unittest.mock

import pytest
import requests
import websockets
import websockets.client

from pylabrobot.machine import Machine, MachineBackend
from pylabrobot.simulator import Simulator, SimulatorBackend
from pylabrobot.resources import Coordinate, Resource, Cos_96_EZWash, STF_L


class SimulatorSetupStopTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for the setup and stop methods of the simulator backend. """

  @pytest.mark.timeout(20)
  async def test_setup_stop(self):
    """ Test that the thread is started and stopped correctly. """

    sim = Simulator(open_browser=False)

    async def setup_stop_single():
      await sim.setup()
      self.assertIsNotNone(sim.loop)
      # wait for the server to start
      time.sleep(1)
      await sim.stop()
      self.assertFalse(sim.has_connection())

    # setup and stop twice to ensure that everything is recycled correctly
    await setup_stop_single()
    await setup_stop_single()


class SimulatorServerTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for servers (ws/fs). """

  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.sim = Simulator(open_browser=False)
    await self.sim.setup()

    ws_port = self.sim.ws_port # port may change if port is already in use
    self.uri = f"ws://localhost:{ws_port}"
    self.client = await websockets.client.connect(self.uri)

  async def asyncTearDown(self):
    await super().asyncTearDown()
    await self.sim.stop()
    await self.client.close()

  def test_get_index_html(self):
    """ Test that the index.html file is returned. """
    r = requests.get("http://localhost:1337/", timeout=10)
    self.assertEqual(r.status_code, 200)
    self.assertIn(r.headers["Content-Type"], ["text/html", "text/html; charset=utf-8"])

  async def test_connect(self):
    await self.client.send("{\"event\": \"ready\"}")
    response = await self.client.recv()
    self.assertEqual(response, "{\"event\": \"ready\"}")

  async def test_event_sent(self):
    await self.client.send("{\"event\": \"ready\"}")
    response = await self.client.recv()
    self.assertEqual(response, "{\"event\": \"ready\"}")

    await self.sim.send_command("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    self.assertEqual(data["event"], "test")


class SimulatorCommandTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for command sending using the simulator backend. """

  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.maxDiff = None
    self.sim = Simulator(open_browser=False)

    # mock the send_command method to catch the events
    self.sim.send_command = unittest.mock.AsyncMock() # type: ignore[method-assign]

    await self.sim.setup()

    self.root_resource = Resource(size_x=100, size_y=100, size_z=100, name="root")

    self.tip_rack = STF_L(name="tip_rack_01")
    self.root_resource.assign_child_resource(self.tip_rack, location=Coordinate(0, 0, 0))

    self.plate = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.root_resource.assign_child_resource(self.plate, location=Coordinate(0, 0, 0))

  async def test_adjust_wells_liquids(self):
    await self.sim.adjust_wells_liquids(self.plate, liquids=[[(None, 100.0)]]*(12*8))
    self.sim.send_command.assert_called_once() # type: ignore[attr-defined]
    call_args = self.sim.send_command.call_args[1] # type: ignore[attr-defined]
    self.assertEqual(call_args["event"], "adjust_well_liquids")

  async def test_edit_tips(self):
    # type: ignore[attr-defined]
    await self.sim.edit_tips(self.tip_rack, [[True]*12]*8)
    self.sim.send_command.assert_called_once() # type: ignore[attr-defined]
    call_args = self.sim.send_command.call_args[1] # type: ignore[attr-defined]
    self.assertEqual(call_args["event"], "edit_tips")

  async def test_fill_tip_rack(self):
    await self.sim.fill_tip_rack(self.tip_rack)
    self.sim.send_command.assert_called_once() # type: ignore[attr-defined]
    call_args = self.sim.send_command.call_args[1] # type: ignore[attr-defined]
    self.assertEqual(call_args["event"], "edit_tips")

  async def test_clear_tips(self):
    await self.sim.fill_tip_rack(self.tip_rack)
    self.sim.send_command.assert_called_once() # type: ignore[attr-defined]
    call_args = self.sim.send_command.call_args[1] # type: ignore[attr-defined]
    self.assertEqual(call_args["event"], "edit_tips")


# Demo backend and Machine for testing
class BoopSimulatorBackend(MachineBackend, SimulatorBackend):
  async def setup(self):
    pass
  async def stop(self):
    pass
class Boop(Machine):
  def __init__(self, backend: BoopSimulatorBackend):
    self.backend = backend
    super().__init__(name="boop", size_x=100, size_y=100, size_z=100, backend=self.backend)


class SimulatorAddRemoveDevicesTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for adding and removing devices. """

  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.maxDiff = None
    self.sim = Simulator(open_browser=False)

    # mock the send_command method to catch the events
    self.sim.send_command = unittest.mock.AsyncMock() # type: ignore[method-assign]

    await self.sim.setup()

  async def test_set_root_resource_device(self):
    root_resource = Boop(backend=BoopSimulatorBackend(simulator=self.sim))
    await self.sim.set_root_resource(root_resource)

    # check that the event was sent: add_device
    time.sleep(0.1) # wait for `run_coroutine_threadsafe` to finish
    self.sim.send_command.assert_called_with( # type: ignore[attr-defined]
      event="add_device", data={"device_name": "boop"}, device=None)

  async def test_add_device_in_subtree(self):
    root_resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    root_resource.assign_child_resource(
      Boop(backend=BoopSimulatorBackend(simulator=self.sim)), location=Coordinate(0, 0, 0))
    await self.sim.set_root_resource(root_resource)

    # check that the event was sent: add_device
    time.sleep(0.1) # wait for `run_coroutine_threadsafe` to finish
    self.sim.send_command.assert_called_with( # type: ignore[attr-defined]
      event="add_device", data={"device_name": "boop"}, device=None)

  async def test_remove_device_in_subtree(self):
    root_resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    boop = Boop(backend=BoopSimulatorBackend(simulator=self.sim))
    root_resource.assign_child_resource(boop, location=Coordinate(0, 0, 0))
    await self.sim.set_root_resource(root_resource)
    boop.unassign()

    # check that the event was sent: remove_device
    time.sleep(0.1) # wait for `run_coroutine_threadsafe` to finish
    self.sim.send_command.assert_called_with( # type: ignore[attr-defined]
      event="remove_device", data={"device_name": "boop"}, device=None)
