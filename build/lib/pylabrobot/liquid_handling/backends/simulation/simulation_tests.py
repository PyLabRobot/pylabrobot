""" Tests for the simulation backend. """

import json
import time
from typing import Any, Dict, Optional
import unittest

import pytest
import requests
import websockets
import websockets.client

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import SerializingSavingBackend, SimulatorBackend
from pylabrobot.resources import STARLetDeck
from pylabrobot.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_EZWash,
  STF_L,
)


class SimulatorBackendSetupStopTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for the setup and stop methods of the simulator backend. """

  @pytest.mark.timeout(20)
  async def test_setup_stop(self):
    """ Test that the thread is started and stopped correctly. """

    backend = SimulatorBackend(open_browser=False)

    async def setup_stop_single():
      await backend.setup()
      self.assertIsNotNone(backend.loop)
      # wait for the server to start
      time.sleep(1)
      await backend.stop()
      self.assertFalse(backend.has_connection())

    # setup and stop twice to ensure that everything is recycled correctly
    await setup_stop_single()
    await setup_stop_single()


class SimulatorBackendServerTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for servers (ws/fs). """

  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.backend = SimulatorBackend(open_browser=False)
    await self.backend.setup()

    ws_port = self.backend.ws_port # port may change if port is already in use
    self.uri = f"ws://localhost:{ws_port}"
    self.client = await websockets.client.connect(self.uri)

  async def asyncTearDown(self):
    await super().asyncTearDown()
    await self.backend.stop()
    await self.client.close()

  def test_get_index_html(self):
    """ Test that the index.html file is returned. """
    r = requests.get("http://localhost:1337/", timeout=10)
    self.assertEqual(r.status_code, 200)
    self.assertIn(r.headers["Content-Type"], ["text/html", "text/html; charset=utf-8"])

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


class SimulatorBackendEventCatcher(SerializingSavingBackend, SimulatorBackend): # type: ignore
  """ Catches events that would be sent over the websocket for easy testing.

  This class inherits from SimulatorBackend to get the same functionality as the
  SimulatorBackend class.
  """

  async def send_command(self, command: str, data: Optional[Dict[str, Any]] = None,
    wait_for_response: bool = True): # pylint: disable=unused-argument
    self.sent_commands.append({"command": command, "data": data})


class SimulatorBackendCommandTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for command sending using the simulator backend. """

  async def asyncSetUp(self):
    await super().asyncSetUp()

    self.backend = SimulatorBackendEventCatcher(open_browser=False)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[0] = self.tip_rack = STF_L(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.deck.assign_child_resource(self.plt_car, rails=9)

    self.maxDiff = None

    self.backend.clear()

  async def asyncTearDown(self):
    await super().asyncTearDown()
    await self.lh.stop()

  async def test_adjust_wells_liquids(self):
    await self.backend.adjust_wells_liquids(self.plate, liquids=[[(None, 100.0)]]*(12*8))
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "adjust_well_liquids")

  async def test_edit_tips(self):
    await self.backend.edit_tips(self.tip_rack, [[True]*12]*8)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "edit_tips")

  async def test_fill_tip_rack(self):
    await self.backend.fill_tip_rack(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "edit_tips")

  async def test_clear_tips(self):
    await self.backend.fill_tip_rack(self.tip_rack)
    self.assertEqual(len(self.backend.sent_commands), 1)
    self.assertEqual(self.backend.sent_commands[0]["command"], "edit_tips")
