"""The state channel: what a client is told, and what it is deliberately not told again."""

import asyncio
import json
import socket
import unittest
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import websockets
from websockets.typing import Origin

from pylabrobot.resources import does_volume_tracking, set_volume_tracking
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.resource import Resource
from pylabrobot.visualizer3D.facility import Facility
from pylabrobot.visualizer3D.server import Viewer3D

# Away from the defaults, so a viewer someone left open does not answer these.
FS_PORT, WS_PORT = 8731, 8732


class StateChannelTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    # Volume tracking is global, so remember what it was and put it back: leaving it on breaks
    # every other suite that aspirates from a well it never filled.
    self._volume_tracking = does_volume_tracking()
    set_volume_tracking(True)
    self.facility = Facility(name="facility", size_x=1000, size_y=1000, size_z=500)
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.facility.assign_child_resource(self.plate, location=Coordinate(10, 10, 0))
    self.viewer = Viewer3D(
      self.facility, open_browser=False, fs_port=FS_PORT, ws_port=WS_PORT, name="tests"
    )
    await self.viewer.start()

  async def asyncTearDown(self):
    await self.viewer.stop()
    set_volume_tracking(self._volume_tracking)

  async def connect(self):
    """Open a client and take the scene and the snapshot it is greeted with."""
    ws = await websockets.connect(self.viewer.ws_url, max_size=None)
    scene = json.loads(await ws.recv())["data"]
    snapshot = json.loads(await ws.recv())["data"]
    return ws, scene, snapshot

  async def next_state(self, ws, timeout: float = 2.0):
    """The next state message, or None if the server stayed quiet."""
    try:
      while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if message["event"] == "state":
          return message["data"]
    except asyncio.TimeoutError:
      return None

  async def test_snapshot_names_every_publishing_resource(self):
    ws, scene, snapshot = await self.connect()
    try:
      self.assertIn("plate_well_A1", snapshot["of"])
      self.assertEqual(len(snapshot["of"]), 96)  # the wells; the plate itself publishes nothing
      self.assertIn("plate_well_A1", scene["instances"]["names"])
    finally:
      await ws.close()

  async def test_snapshot_leaves_out_locations(self):
    """The scene sent immediately before already places everything, and a position is unique to one
    resource, so carrying it here would give every resource a state of its own."""
    ws, _, snapshot = await self.connect()
    try:
      for state in snapshot["states"]:
        self.assertNotIn("location", state)
      self.assertLessEqual(len(snapshot["states"]), 2)
    finally:
      await ws.close()

  async def test_a_change_names_only_what_changed(self):
    ws, _, _ = await self.connect()
    try:
      self.plate.get_item("A1").tracker.set_volume(150.0)
      update = await self.next_state(ws)
      self.assertIsNotNone(update)
      self.assertEqual(list(update["of"]), ["plate_well_A1"])
    finally:
      await ws.close()

  async def test_an_unchanged_value_is_not_sent_again(self):
    ws, _, _ = await self.connect()
    try:
      self.plate.get_item("A1").tracker.set_volume(150.0)
      self.assertIsNotNone(await self.next_state(ws))
      self.plate.get_item("A1").tracker.set_volume(150.0)
      self.assertIsNone(await self.next_state(ws, timeout=1.0))
    finally:
      await ws.close()

  async def test_a_change_too_small_to_see_is_not_sent(self):
    """State is rounded to what a viewer can show, so a hundredth of a microlitre is not news."""
    ws, _, _ = await self.connect()
    try:
      self.plate.get_item("A1").tracker.set_volume(150.0)
      self.assertIsNotNone(await self.next_state(ws))
      self.plate.get_item("A1").tracker.set_volume(150.04)
      self.assertIsNone(await self.next_state(ws, timeout=1.0))
    finally:
      await ws.close()

  async def test_a_second_client_is_told_everything(self):
    """Suppression is about what one client has seen. A client that has seen nothing gets it all,
    however much the others have already been told."""
    first, _, _ = await self.connect()
    try:
      self.plate.get_item("A1").tracker.set_volume(150.0)
      await self.next_state(first)
      second, _, snapshot = await self.connect()
      try:
        self.assertEqual(len(snapshot["of"]), 96)
      finally:
        await second.close()
    finally:
      await first.close()

  async def test_a_moved_resource_publishes_its_new_position(self):
    """Position reaches a subscriber the same way rotation always has."""
    ws, _, _ = await self.connect()
    try:
      self.plate.location = Coordinate(400, 300, 0)
      update = await self.next_state(ws)
      self.assertIsNotNone(update)
      self.assertIn("plate", update["of"])
      moved = update["states"][update["of"]["plate"]]
      self.assertEqual(moved["location"]["x"], 400)
    finally:
      await ws.close()

  async def test_a_resource_moved_under_another_parent_is_not_drawn_under_its_old_one(self):
    """Its new location waits for the rebuild: sent first, it would be read against the old parent."""
    holder = Resource(name="holder", size_x=200, size_y=200, size_z=50)
    self.facility.assign_child_resource(holder, location=Coordinate(500, 500, 0))
    ws, _, _ = await self.connect()
    try:
      await self.next_scene(ws)  # the rebuild for the holder
      self.plate.unassign()
      holder.assign_child_resource(self.plate, location=Coordinate(5, 5, 50))
      states = await self.next_scene(ws)
      for state in states:
        index = state["of"].get("plate")
        self.assertTrue(index is None or "location" not in state["states"][index], state)
    finally:
      await ws.close()

  async def next_scene(self, ws, timeout: float = 2.0):
    """The state messages that arrive before the next scene, which is waited for."""
    states: List[Dict[str, Any]] = []
    while True:
      message = json.loads(await asyncio.wait_for(ws.recv(), timeout))
      if message["event"] == "scene":
        return states
      if message["event"] == "state":
        states.append(message["data"])


class AccessTests(unittest.IsolatedAsyncioTestCase):
  """Only the page this viewer served, reached by a name this machine answers to, may watch."""

  async def asyncSetUp(self):
    self.facility = Facility(name="facility", size_x=1000, size_y=1000, size_z=500)
    self.viewer = Viewer3D(
      self.facility, open_browser=False, fs_port=FS_PORT, ws_port=WS_PORT, name="tests"
    )
    await self.viewer.start()

  async def asyncTearDown(self):
    await self.viewer.stop()

  def ws(self, token: Optional[str]) -> str:
    query = "" if token is None else f"?token={token}"
    return f"ws://127.0.0.1:{self.viewer.ws_port}/{query}"

  async def assert_refused(self, url: str, **kwargs):
    with self.assertRaises(websockets.InvalidStatus) as refused:
      await websockets.connect(url, **kwargs)
    self.assertEqual(refused.exception.response.status_code, 403)

  async def test_a_websocket_without_the_token_is_refused(self):
    await self.assert_refused(self.ws(None))
    await self.assert_refused(self.ws("not-the-token"))

  async def test_a_page_from_another_site_is_refused_even_with_the_token(self):
    await self.assert_refused(self.ws(self.viewer.token), origin="https://example.com")

  async def test_the_served_page_and_a_tunnel_are_let_in(self):
    for origin in (
      f"http://127.0.0.1:{self.viewer.fs_port}",
      "http://localhost:9000",
      f"http://{socket.gethostname()}.local:{self.viewer.fs_port}",
      "http://10.60.2.36:1338",
    ):
      ws = await websockets.connect(
        self.ws(self.viewer.token), origin=Origin(origin), max_size=None
      )
      self.assertEqual(json.loads(await ws.recv())["event"], "scene")
      await ws.close()

  def get(self, host: str) -> int:
    request = urllib.request.Request(
      f"http://127.0.0.1:{self.viewer.fs_port}/", headers={"Host": host}
    )
    try:
      with urllib.request.urlopen(request) as response:
        return int(response.status)
    except urllib.error.HTTPError as error:
      return error.code

  async def test_the_page_is_not_served_to_a_rebound_name(self):
    """DNS rebinding points a hostile name at 127.0.0.1, which would make its page same-origin
    with ours and let it read the token out of the HTML."""
    self.assertEqual(await asyncio.to_thread(self.get, "attacker.example:1338"), 403)

  async def test_the_page_carries_the_token_for_a_known_name(self):
    for host in ("127.0.0.1:1338", "localhost:1338", "[::1]:1338"):
      self.assertEqual(await asyncio.to_thread(self.get, host), 200, host)
    page = await asyncio.to_thread(
      lambda: urllib.request.urlopen(f"http://127.0.0.1:{self.viewer.fs_port}/").read().decode()
    )
    self.assertIn(self.viewer.token, page)
    self.assertNotIn("{{ ws_token }}", page)

  async def test_every_run_has_its_own_token(self):
    other = Viewer3D(self.facility, open_browser=False)
    self.assertNotEqual(other.token, self.viewer.token)


if __name__ == "__main__":
  unittest.main()
