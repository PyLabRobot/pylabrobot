"""The state channel: what a client is told, and what it is deliberately not told again."""

import asyncio
import contextlib
import hashlib
import io
import json
import os
import shutil
import socket
import tempfile
import unittest
import unittest.mock
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import anyio
import pytest
import websockets
from websockets.typing import Origin

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.resources import does_volume_tracking, set_volume_tracking
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.hamilton import hamilton_96_tiprack_1000uL
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.testing import AnyioTestBase
from pylabrobot.visualizer3D.demo import build_facility, declare_channel_access, star_of
from pylabrobot.visualizer3D.facility import Facility
from pylabrobot.visualizer3D.server import Viewer3D

_USED_PORTS: set[int] = set()


def free_ports(count: int) -> List[int]:
  """Distinct ports nothing on this machine listens on, so two suites can run at once."""
  sockets: List[socket.socket] = []
  ports: List[int] = []
  try:
    while len(ports) < count:
      sock = socket.socket()
      sockets.append(sock)
      sock.bind(("127.0.0.1", 0))
      port = sock.getsockname()[1]
      if port not in _USED_PORTS:
        _USED_PORTS.add(port)
        ports.append(port)
    return ports
  finally:
    for sock in sockets:
      sock.close()


async def _can_rebind_closed_listen_port() -> bool:
  (port,) = free_ports(1)
  sock = socket.socket()
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  sock.bind(("127.0.0.1", port))
  sock.listen(1)
  await anyio.sleep(0.05)
  sock.close()
  probe = socket.socket()
  probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  try:
    probe.bind(("127.0.0.1", port))
    return True
  except OSError:
    return False
  finally:
    probe.close()


def empty_facility() -> Facility:
  """A facility with nothing in it, of a size every test here can place things in."""
  return Facility(name="facility", size_x=1000, size_y=1000, size_z=500)


FS_PORT, WS_PORT = free_ports(2)
# Any model file shipped with the package: what it draws does not matter, that it registers does.
MESH_FILE = os.path.join(
  os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
  "hamilton",
  "star",
  "resource_model",
  "starlet_base.glb",
)


class _ViewerAsyncTestCase(AnyioTestBase):
  _anyio_backends = ["asyncio"]
  _stack: AsyncExitStackWithShielding

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self._stack = stack

  def track_volumes(self) -> None:
    """Turn volume tracking on for one test and put it back after."""
    # It is global: left on, it breaks every other suite that aspirates from a well it never filled.
    was_tracking = does_volume_tracking()
    set_volume_tracking(True)
    self._stack.callback(set_volume_tracking, was_tracking)


class TestStateChannel(_ViewerAsyncTestCase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.track_volumes()
    self.facility = empty_facility()
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.facility.assign_child_resource(self.plate, location=Coordinate(10, 10, 0))
    fs_port, ws_port = free_ports(2)
    self.viewer = Viewer3D(
      self.facility, open_browser=False, fs_port=fs_port, ws_port=ws_port, name="tests"
    )
    await stack.enter_async_context(self.viewer)

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
      assert "plate_well_A1" in snapshot["of"]
      assert len(snapshot["of"]) == 96  # the wells; the plate itself publishes nothing
      assert "plate_well_A1" in scene["instances"]["names"]
    finally:
      await ws.close()

  async def test_snapshot_leaves_out_locations(self):
    """The scene sent immediately before already places everything, and a position is unique to one
    resource, so carrying it here would give every resource a state of its own."""
    ws, _, snapshot = await self.connect()
    try:
      assert snapshot["locations"] == {}
      assert len(snapshot["states"]) == 1  # every empty well; the plate publishes nothing
    finally:
      await ws.close()

  async def test_full_tip_spots_share_one_state(self):
    """A spot's state embeds its tip, and the tip names the spot. That is identity, not state: a
    rack of the same tip is one state on the wire, not ninety-six."""
    rack = hamilton_96_tiprack_1000uL(name="rack", with_tips=True)
    self.facility.assign_child_resource(rack, location=Coordinate(300, 10, 0))
    ws, _, snapshot = await self.connect()
    try:
      spots = [spot.name for spot in rack.get_all_items()]
      assert len({snapshot["of"][name] for name in spots}) == 1
      assert len(snapshot["states"]) == 3  # the wells, the spots and the tips
    finally:
      await ws.close()

  async def test_a_tip_in_a_rack_is_greeted_with_what_it_holds(self):
    """The info panel reads a tip's `volume` and `max_volume` from its state, so a client is
    greeted with both."""
    rack = hamilton_96_tiprack_1000uL(name="rack", with_tips=True)
    self.facility.assign_child_resource(rack, location=Coordinate(300, 10, 0))
    tip = rack.get_item("A1").get_tip()
    tip.tracker.set_volume(30.0)
    ws, _, snapshot = await self.connect()
    try:
      state = snapshot["states"][snapshot["of"][tip.name]]
      assert state["volume"] == 30.0
      assert state["max_volume"] == tip.maximal_volume
    finally:
      await ws.close()

  async def test_a_tip_publishes_what_it_holds(self):
    """A tip's tracker was never published, so the channel panel drew every tip empty."""
    rack = hamilton_96_tiprack_1000uL(name="rack", with_tips=True)
    self.facility.assign_child_resource(rack, location=Coordinate(300, 10, 0))
    tip = rack.get_item("A1").get_tip()
    ws, _, _ = await self.connect()
    try:
      tip.tracker.set_volume(12.5)
      update = await self.next_state(ws)
      assert update is not None
      assert update["states"][update["of"][tip.name]]["volume"] == 12.5
    finally:
      await ws.close()

  async def test_a_change_names_only_what_changed(self):
    ws, _, _ = await self.connect()
    try:
      self.plate.get_item("A1").tracker.set_volume(150.0)
      update = await self.next_state(ws)
      assert update is not None
      assert list(update["of"]) == ["plate_well_A1"]
    finally:
      await ws.close()

  async def test_a_filled_well_is_not_a_move(self):
    """Every state carries the resource's position, so a well whose volume changed was reported
    as moved: three quarters of a plate fill was positions, and every later snapshot repeated
    them."""
    first, _, _ = await self.connect()
    try:
      for well in self.plate.get_all_items():
        well.tracker.set_volume(100.0)
      update = await self.next_state(first)
      assert update is not None
      assert len(update["of"]) == 96
      assert update["locations"] == {}
      second, _, snapshot = await self.connect()
      try:
        assert snapshot["locations"] == {}
      finally:
        await second.close()
    finally:
      await first.close()

  async def test_an_unchanged_value_is_not_sent_again(self):
    ws, _, _ = await self.connect()
    try:
      self.plate.get_item("A1").tracker.set_volume(150.0)
      assert await self.next_state(ws) is not None
      # A sentinel changed in the same batch: the message it arrives in is the one A1 would be in.
      self.plate.get_item("A1").tracker.set_volume(150.0)
      self.plate.get_item("B1").tracker.set_volume(10.0)
      update = await self.next_state(ws)
      assert update is not None
      assert list(update["of"]) == ["plate_well_B1"]
    finally:
      await ws.close()

  async def test_a_change_too_small_to_see_is_not_sent(self):
    """State is rounded to what a viewer can show, so a hundredth of a microlitre is not news."""
    ws, _, _ = await self.connect()
    try:
      self.plate.get_item("A1").tracker.set_volume(150.0)
      assert await self.next_state(ws) is not None
      self.plate.get_item("A1").tracker.set_volume(150.04)
      self.plate.get_item("B1").tracker.set_volume(10.0)
      update = await self.next_state(ws)
      assert update is not None
      assert list(update["of"]) == ["plate_well_B1"]
    finally:
      await ws.close()

  async def test_a_discarded_resource_is_no_longer_listened_to(self):
    """A resource taken out of the tree kept its callback, so a discarded plate's every tracker
    change was still serialized and queued for a viewer that could never draw it."""
    kept = cor_96_wellplate_360uL_Fb(name="kept")
    self.facility.assign_child_resource(kept, location=Coordinate(300, 10, 0))
    ws, _, _ = await self.connect()
    try:
      self.plate.unassign()
      await self.next_of(ws, "scene")
      assert await self.next_state(ws) is not None  # the snapshot after the rebuild
      assert self.plate.get_item("A1")._resource_state_updated_callbacks == []
      self.plate.get_item("A1").tracker.set_volume(150.0)
      kept.get_item("A1").tracker.set_volume(150.0)
      update = await self.next_state(ws)
      assert update is not None
      assert list(update["of"]) == ["kept_well_A1"]
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
        assert len(snapshot["of"]) == 96
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
      assert update is not None
      assert "plate" in update["of"]
      assert update["locations"]["plate"]["x"] == 400
    finally:
      await ws.close()

  async def test_a_resource_moved_under_another_parent_is_moved_not_rebuilt(self):
    """The same names are the same instances: a change of parent is a move, a parent index and six
    floats, applied to the scene the client has. Its new location travels in the move and in no
    state before it, which would be read against the old parent."""
    holder = Resource(name="holder", size_x=200, size_y=200, size_z=50)
    self.facility.assign_child_resource(holder, location=Coordinate(500, 500, 0))
    # The holder is in the scene a client is greeted with, and the flush its assignment scheduled
    # finds nothing moved since, so it sends nothing: a rebuild was the old behaviour there too.
    ws, _, _ = await self.connect()
    try:
      self.plate.unassign()
      holder.assign_child_resource(self.plate, location=Coordinate(5, 5, 50))
      states, moves = await self.next_of(ws, "moves")
      for state in states:
        assert "plate" not in state.get("locations", {}), state
      moved = {move["name"]: move for move in moves["moves"]}
      assert "plate" in moved
      assert moved["plate"]["parent"] == "holder"
      assert moved["plate"]["location"] == {"x": 5.0, "y": 5.0, "z": 50.0}
      assert self.viewer.rebuilds == 0
    finally:
      await ws.close()

  async def test_a_client_arriving_after_a_move_sees_it_in_the_scene(self):
    """The kept scene is moved along with the clients', so a late client is handed it as it stands."""
    holder = Resource(name="holder", size_x=200, size_y=200, size_z=50)
    self.facility.assign_child_resource(holder, location=Coordinate(500, 500, 0))
    first, _, _ = await self.connect()
    try:
      self.plate.unassign()
      holder.assign_child_resource(self.plate, location=Coordinate(5, 5, 50))
      await self.next_of(first, "moves")
      second, scene, _ = await self.connect()
      try:
        names = scene["instances"]["names"]
        assert names[scene["instances"]["parent"][names.index("plate")]] == "holder"
      finally:
        await second.close()
    finally:
      await first.close()

  async def next_of(self, ws, event: str, timeout: float = 2.0):
    """The state messages that arrive before the next message of `event`, and that message."""
    states: List[Dict[str, Any]] = []
    while True:
      message = json.loads(await asyncio.wait_for(ws.recv(), timeout))
      if message["event"] == event:
        return states, message["data"]
      if message["event"] == "state":
        states.append(message["data"])


class CommandFailed(Exception):
  """What a held aspirate raises when it is told to fail."""


class TestSimulatedAspirate(_ViewerAsyncTestCase):
  """A mounted tip's liquid on the simulated STAR, as the info panel reads it: committed."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.track_volumes()
    facility = build_facility()
    self.star = star_of(facility)
    await stack.enter_async_context(self.star)
    declare_channel_access(self.star)
    assert self.star.pipettes is not None
    self.pipettes = self.star.pipettes
    source = self.star.deck.get_resource("source_0")
    rack = self.star.deck.get_resource("tips_0")
    assert isinstance(source, Plate) and isinstance(rack, TipRack)
    self.well = source.get_item("A1")
    self.well.tracker.set_volume(200.0)
    self.rack = rack
    fs_port, ws_port = free_ports(2)
    self.viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    await stack.enter_async_context(self.viewer)
    self.drawing = asyncio.Event()
    self.release = asyncio.Event()

  def hold_the_command(self, fail: bool) -> None:
    """Hold each aspirate at its command until `release` is set, then send it or raise."""
    send = self.pipettes._aspirate_in_one_move

    async def held(*args: Any, **kwargs: Any) -> None:
      self.drawing.set()
      await self.release.wait()
      if fail:
        raise CommandFailed()
      await send(*args, **kwargs)

    patcher = unittest.mock.patch.object(self.pipettes, "_aspirate_in_one_move", held)
    patcher.start()
    self._stack.callback(patcher.stop)

  async def told(self, ws, name: str, quiet: float = 0.5) -> List[Dict[str, Any]]:
    """Every state a client is told for `name` until the server stays quiet for `quiet` s."""
    told: List[Dict[str, Any]] = []
    try:
      while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), quiet))
        data = message["data"]
        if message["event"] == "state" and name in data["of"]:
          told.append(data["states"][data["of"][name]])
    except asyncio.TimeoutError:
      return told

  async def until_told(self, ws, name: str, seconds: float = 30.0) -> None:
    """Read until a client is told a state for `name`, however long the loop is held first."""

    async def read() -> None:
      while True:
        message = json.loads(await ws.recv())
        if message["event"] == "state" and name in message["data"]["of"]:
          return

    await asyncio.wait_for(read(), seconds)

  async def aspirate_held(self, ws) -> Tuple[Tip, List[Dict[str, Any]], "asyncio.Future[None]"]:
    """Mount a tip, start an aspirate into it and hold it at its command.

    Returns the tip, what the client was told of it while held, and the aspirate.
    """
    await self.pipettes.pick_up_tips([self.rack.get_item("A1")])
    tip = self.pipettes.get_mounted_tip(0)
    assert tip is not None
    # The pick-up's rebuild can hold the loop past a quiet window: wait for the snapshot after it.
    await self.until_told(ws, tip.name)
    aspirating = asyncio.ensure_future(self.pipettes.aspirate([self.well], [50.0]))
    await asyncio.wait_for(self.drawing.wait(), 10)
    return tip, await self.told(ws, tip.name), aspirating

  async def test_a_mounted_tip_is_told_its_committed_volume(self):
    """While the command runs the tip's booking is pending, and the page shows `volume`: it
    reads the new volume only once the command is done."""
    self.hold_the_command(fail=False)
    ws = await websockets.connect(self.viewer.ws_url, max_size=None)
    try:
      tip, during, aspirating = await self.aspirate_held(ws)
      self.release.set()
      await aspirating
      after = await self.told(ws, tip.name)
    finally:
      self.release.set()
      await ws.close()
    drawn = round(tip.tracker.volume, 1)
    assert drawn > 0
    assert [(s["volume"], s["pending_volume"]) for s in during] == [(0, drawn)]
    assert [s["volume"] for s in after] == [drawn]

  async def test_a_failed_aspirate_leaves_a_mounted_tip_told_what_it_held(self):
    """A failed command rolls the tip's booking back, and the client is told it was: the last
    state it has for the tip is the committed one, pending included."""
    self.hold_the_command(fail=True)
    ws = await websockets.connect(self.viewer.ws_url, max_size=None)
    try:
      tip, during, aspirating = await self.aspirate_held(ws)
      self.release.set()
      with pytest.raises(CommandFailed):
        await aspirating
      after = await self.told(ws, tip.name)
    finally:
      self.release.set()
      await ws.close()
    assert tip.tracker.volume == 0
    assert during[-1]["pending_volume"] > 0
    assert [s["volume"] for s in during + after] == [0] * len(during + after)
    assert after, "the rollback told the client nothing"
    assert (after[-1]["volume"], after[-1]["pending_volume"]) == (0, 0)


class TestFileServer(_ViewerAsyncTestCase):
  """The file server keeps quiet about what is not its fault."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.facility = empty_facility()
    fs_port, ws_port = free_ports(2)
    self.viewer = Viewer3D(self.facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    await stack.enter_async_context(self.viewer)

  async def test_static_files_are_kept_and_revalidated_while_the_page_is_not(self):
    """Every response said `no-store`, so a browser fetched three megabytes of scripts and
    models on every reload. The page carries this run's token and stays uncached; the rest is
    revalidated, and an unchanged file is answered 304 with no body."""
    base = f"http://127.0.0.1:{self.viewer.fs_port}"
    page = await anyio.to_thread.run_sync(urllib.request.urlopen, f"{base}/")
    assert page.headers["Cache-Control"] == "no-store"
    script = await anyio.to_thread.run_sync(
      urllib.request.urlopen, f"{base}/vendor/three.core.min.js"
    )
    assert script.headers["Cache-Control"] == "no-cache"
    again = urllib.request.Request(
      f"{base}/vendor/three.core.min.js",
      headers={"If-Modified-Since": script.headers["Last-Modified"]},
    )
    with pytest.raises(urllib.error.HTTPError) as unchanged:
      await anyio.to_thread.run_sync(urllib.request.urlopen, again)
    assert unchanged.value.code == 304

  async def test_the_name_in_the_header_is_text_not_markup(self):
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(
      empty_facility(), open_browser=False, fs_port=fs_port, ws_port=ws_port, name="<b>run</b>"
    )
    async with viewer:
      page = await anyio.to_thread.run_sync(
        lambda: urllib.request.urlopen(f"http://127.0.0.1:{viewer.fs_port}/").read().decode()
      )
    assert "&lt;b&gt;run&lt;/b&gt;" in page
    assert "<b>run</b>" not in page

  async def test_an_idle_connection_is_closed(self):
    """Keep-alive with no timeout held a thread per connection for as long as the other end liked:
    two hundred idle sockets were two hundred threads."""
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(empty_facility(), open_browser=False, fs_port=fs_port, ws_port=ws_port)
    viewer.FS_TIMEOUT_S = 0.2

    def idle() -> bytes:
      with socket.create_connection(("127.0.0.1", viewer.fs_port)) as sock:
        sock.settimeout(5)
        return sock.recv(1)  # nothing is sent, so only the server closing returns

    async with viewer:
      assert await anyio.to_thread.run_sync(idle) == b""

  async def test_a_download_abandoned_by_the_browser_prints_no_traceback(self):
    """A page left mid-download closes its end of the socket, which the threaded server used to
    report as an exception in the request thread, a full traceback on every reload."""
    captured = io.StringIO()

    def abandon() -> None:
      with socket.create_connection(("127.0.0.1", self.viewer.fs_port)) as sock:
        sock.sendall(b"GET /vendor/three.webgpu.min.js HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        sock.recv(1024)  # the start of the body, then gone

    with contextlib.redirect_stderr(captured):
      await anyio.to_thread.run_sync(abandon)
      # The request's worker ends once its write fails, having reported by then or not at all.
      with anyio.move_on_after(5):
        while self.viewer._active_file_requests:
          await anyio.sleep(0.02)
    assert captured.getvalue() == ""


class TestLifecycle(_ViewerAsyncTestCase):
  """A stopped viewer gives its ports back."""

  async def test_viewer_supports_managed_lifespan_and_reentry(self):
    assert issubclass(Viewer3D, HasLifespan)
    facility = empty_facility()
    part = Resource(name="part", size_x=10, size_y=10, size_z=10)
    facility.assign_child_resource(part, location=Coordinate(0, 0, 0))
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    async with viewer:
      assert viewer.is_active
      assert len(part._resource_state_updated_callbacks) == 1
    assert not viewer.is_active
    assert len(part._resource_state_updated_callbacks) == 0
    viewer.fs_port, viewer.ws_port = free_ports(2)
    async with viewer:
      assert viewer.is_active
      assert len(part._resource_state_updated_callbacks) == 1
    assert not viewer.is_active
    assert len(part._resource_state_updated_callbacks) == 0

  async def test_a_viewer_started_after_another_stopped_binds_the_same_ports(self):
    """`stop` used to close the file server and leave the websocket server listening, so the next
    viewer in the same process found its port taken and moved up: the ports drifted by one on
    every restart, and a page served by the earlier viewer kept its stale token forever."""
    facility = empty_facility()
    fs_port, ws_port = free_ports(2)
    first = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    async with first:
      assert (first.fs_port, first.ws_port) == (fs_port, ws_port)
      assert first._httpd is not None
      assert first._ws_server is not None
      assert first._ws_server.is_serving()
      fs_server = first._httpd
      ws_server = first._ws_server
    assert first._httpd is None
    assert first._ws_server is None
    assert fs_server.socket.fileno() == -1
    assert not ws_server.is_serving()
    if await _can_rebind_closed_listen_port():
      second = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
      async with second:
        assert (second.fs_port, second.ws_port) == (fs_port, ws_port)

  async def test_stop_does_not_hold_the_loop(self):
    """Shutting the file server down waited on its serving thread's half-second poll from the
    loop's own thread, so everything else on the loop stood still for that long."""
    facility = empty_facility()
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    loop = asyncio.get_running_loop()
    ticks: List[float] = []

    async def tick() -> None:
      while True:
        await anyio.sleep(0.02)
        ticks.append(loop.time())

    ticking = asyncio.ensure_future(tick())
    try:
      async with viewer:
        ticks.append(loop.time())
    finally:
      ticks.append(loop.time())  # a loop held until here shows as one long gap
      ticking.cancel()
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.2

  async def test_a_stopped_viewer_no_longer_listens_to_the_tree(self):
    """`stop` used to leave every state and assignment callback in place, so a stopped viewer
    kept serializing every change for nobody, and each viewer started on a tree in one session
    added its own callbacks to every resource for the life of the tree."""
    facility = empty_facility()
    part = Resource(name="part", size_x=10, size_y=10, size_z=10)
    facility.assign_child_resource(part, location=Coordinate(0, 0, 0))
    before = len(part._resource_state_updated_callbacks)
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    assert len(part._resource_state_updated_callbacks) == before
    async with viewer:
      assert len(part._resource_state_updated_callbacks) == before + 1
    assert len(part._resource_state_updated_callbacks) == before
    part.location = Coordinate(1, 2, 3)
    late = Resource(name="late", size_x=10, size_y=10, size_z=10)
    facility.assign_child_resource(late, location=Coordinate(0, 0, 0))
    assert len(late._resource_state_updated_callbacks) == 0

  async def test_a_resource_put_back_is_listened_to_once(self):
    """Every assignment subscribed the resource again, so a tip picked up and put back twenty
    times carried twenty-one callbacks and its every change was serialized twenty-one times."""
    facility = empty_facility()
    part = Resource(name="part", size_x=10, size_y=10, size_z=10)
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    async with viewer:
      for _ in range(20):
        facility.assign_child_resource(part, location=Coordinate(0, 0, 0))
        facility.unassign_child_resource(part)
      facility.assign_child_resource(part, location=Coordinate(0, 0, 0))
      assert len(part._resource_state_updated_callbacks) == 1


class TestBind(_ViewerAsyncTestCase):
  """A port that is taken is walked past; any other bind failure is raised at once."""

  async def test_a_host_that_cannot_be_bound_is_raised_at_once(self):
    """Every bind error used to read as a taken port, so an address this machine does not have
    walked up through all sixty-five thousand ports, took seconds, and then overflowed."""
    facility = empty_facility()
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(
      facility, host="203.0.113.1", open_browser=False, fs_port=fs_port, ws_port=ws_port
    )
    with pytest.raises(OSError):
      async with viewer:
        pass
    assert viewer.ws_port == ws_port

  async def test_an_ipv6_host_serves_both_ports(self):
    """The file server was bound as IPv4 whatever the host, so `::1` bound the websocket and then
    hung forever walking ports for a file server that could never bind one."""
    facility = empty_facility()
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, host="::1", open_browser=False, fs_port=fs_port, ws_port=ws_port)
    async with viewer:
      assert (viewer.fs_port, viewer.ws_port) == (fs_port, ws_port)


class AbandonedLoopTests(unittest.TestCase):
  """A viewer whose loop closed under it leaves the tree alone."""

  def test_a_change_after_the_loop_closed_does_not_raise(self):
    """A script that starts a viewer and returns from `asyncio.run` without stopping it used to
    turn every later location change into `RuntimeError: Event loop is closed`, raised out of the
    resource, and an assignment raised after the child was already attached."""
    facility = empty_facility()
    part = Resource(name="part", size_x=10, size_y=10, size_z=10)
    facility.assign_child_resource(part, location=Coordinate(0, 0, 0))

    async def start_and_forget() -> None:
      # Its ports stay bound in this process: nothing can stop it once its loop has gone.
      fs_port, ws_port = free_ports(2)
      await Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port).__aenter__()

    asyncio.run(start_and_forget())
    part.location = Coordinate(1, 2, 3)
    late = Resource(name="late", size_x=10, size_y=10, size_z=10)
    facility.assign_child_resource(late, location=Coordinate(0, 0, 0))
    self.assertIs(late.parent, facility)


class TestRebuild(_ViewerAsyncTestCase):
  """A scene rebuilt from reused models is the scene it was."""

  async def test_a_tree_changed_with_nobody_watching_costs_no_rebuild(self):
    """Every structural change rebuilt the scene and packed a full snapshot before finding there
    was no client to send them to: a third of a second on the demo facility, on every assignment
    of a deck laid out before the page was opened. The next client is greeted with a scene built
    for it then, and it holds what was assigned meanwhile."""
    facility = empty_facility()
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    async with viewer:
      facility.assign_child_resource(
        Resource(name="late", size_x=10, size_y=10, size_z=10), location=Coordinate(0, 0, 0)
      )
      # Twice the debounce: the rebuild the assignment scheduled has had its chance to run.
      await anyio.sleep(2 * Viewer3D.SCENE_DEBOUNCE_S)
      assert viewer.rebuilds == 0
      async with websockets.connect(
        viewer.ws_url, origin=Origin(f"http://127.0.0.1:{viewer.fs_port}")
      ) as ws:
        scene = json.loads(await asyncio.wait_for(ws.recv(), 5))["data"]
        assert "late" in scene["instances"]["names"]

  async def test_a_model_given_a_mesh_does_not_split_on_the_next_build(self):
    """Registering meshes used to write into the interned model dicts, so on the next build the
    one carrying a mesh no longer matched the twins the scene had kept, and every rebuild grew the
    model table: fifty-three models became sixty-seven on the demo facility."""
    facility = empty_facility()
    for i in range(3):
      part = Resource(name=f"part_{i}", size_x=10, size_y=10, size_z=10, model="part")
      # Declared the way a resource module declares it: a field the base class does not know.
      setattr(part, "mesh", {"path": MESH_FILE, "units": "m", "up": "Z"})
      facility.assign_child_resource(part, location=Coordinate(100 * i, 0, 0))
    viewer = Viewer3D(facility, open_browser=False)
    first = viewer._scene_message(rebuild=True)
    second = viewer._scene_message(rebuild=True)
    assert len(first["models"]) == 2  # the facility and the one part they all share
    assert second["models"] == first["models"]


class MeshTests(unittest.TestCase):
  """The file server hands out a mesh without the token, so which files it will serve is narrow."""

  def meshes(self, facility: Resource, models_root: Optional[str] = None) -> Dict[str, Any]:
    """Each part's mesh as the page is told it, by the part's model name; None drawn as a box."""
    viewer = Viewer3D(facility, open_browser=False, models_root=models_root)
    models = viewer._scene_message(rebuild=True)["models"]
    self.viewer = viewer
    return {m["model"]: m.get("mesh") for m in models if m.get("model")}

  def part(self, facility: Resource, name: str, **declared: Any) -> None:
    part = Resource(name=name, size_x=10, size_y=10, size_z=10, model=name)
    for key, value in declared.items():
      setattr(part, key, value)
    facility.assign_child_resource(part, location=Coordinate(0, 0, 0))

  def test_a_mesh_id_does_not_name_its_file(self):
    """The id was the SHA-1 of the file's path, so anyone who could guess a path could fetch it."""
    facility = empty_facility()
    self.part(facility, "a", mesh={"path": MESH_FILE})
    self.part(facility, "b", mesh={"path": MESH_FILE})
    meshes = self.meshes(facility)
    mesh_id = meshes["a"]["url"][len("mesh/") :]
    self.assertEqual(meshes["b"]["url"], meshes["a"]["url"])  # one file, one id
    self.assertNotIn(hashlib.sha1(MESH_FILE.encode()).hexdigest()[:16], mesh_id)
    self.assertEqual(self.viewer._mesh_files[mesh_id], os.path.realpath(MESH_FILE))

  def test_only_a_glb_is_served(self):
    with tempfile.TemporaryDirectory() as directory:
      secret = os.path.join(directory, "secret.txt")
      with open(secret, "w") as f:
        f.write("not a model")
      facility = empty_facility()
      self.part(facility, "text", mesh={"path": secret})
      self.assertIsNone(self.meshes(facility)["text"])
      self.assertEqual(self.viewer._mesh_files, {})

  def test_a_reference_glb_outside_models_root_is_drawn_as_a_box(self):
    with tempfile.TemporaryDirectory() as directory:
      root = os.path.join(directory, "models")
      os.makedirs(root)
      shutil.copy(MESH_FILE, os.path.join(root, "inside.glb"))
      shutil.copy(MESH_FILE, os.path.join(directory, "outside.glb"))
      os.symlink(os.path.join(directory, "outside.glb"), os.path.join(root, "link.glb"))
      facility = empty_facility()
      self.part(facility, "inside", reference_glb="inside.glb")
      self.part(facility, "absolute", reference_glb=os.path.join(directory, "outside.glb"))
      self.part(facility, "dotdot", reference_glb="../outside.glb")
      self.part(facility, "link", reference_glb="link.glb")
      meshes = self.meshes(facility, models_root=root)
      self.assertIsNotNone(meshes["inside"])
      for escaping in ("absolute", "dotdot", "link"):
        self.assertIsNone(meshes[escaping], escaping)


class TestWaitForBrowser(_ViewerAsyncTestCase):
  """A simulated run is over in milliseconds: it waits for a page that is drawing, or none sees it."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    fs_port, ws_port = free_ports(2)
    self.viewer = Viewer3D(empty_facility(), open_browser=False, fs_port=fs_port, ws_port=ws_port)
    await stack.enter_async_context(self.viewer)

  async def say_hello(self, **data: Any) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
      async with websockets.connect(self.viewer.ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"event": "hello", "data": data}))
        await (await ws.ping())  # answered once the hello before it has been read

  async def test_the_wait_ends_when_a_page_says_it_is_drawing(self):
    waiting = asyncio.ensure_future(self.viewer.wait_for_browser(timeout=5))
    await anyio.sleep(0.1)
    assert not waiting.done()
    await self.say_hello(backend="WebGL2")
    await asyncio.wait_for(waiting, 1)

  async def test_a_page_that_could_not_draw_does_not_end_the_wait(self):
    await self.say_hello(backend=None, error="this browser offers neither WebGPU nor WebGL2")
    with pytest.raises(TimeoutError):
      await self.viewer.wait_for_browser(timeout=0.2)

  async def test_waiting_before_start_is_refused(self):
    with pytest.raises(RuntimeError):
      await Viewer3D(empty_facility(), open_browser=False).wait_for_browser(timeout=0.1)


class TestAccess(_ViewerAsyncTestCase):
  """Only the page this viewer served, reached by a name this machine answers to, may watch."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.facility = empty_facility()
    fs_port, ws_port = free_ports(2)
    self.viewer = Viewer3D(
      self.facility, open_browser=False, fs_port=fs_port, ws_port=ws_port, name="tests"
    )
    await stack.enter_async_context(self.viewer)

  def ws(self, token: Optional[str]) -> str:
    query = "" if token is None else f"?token={token}"
    return f"ws://127.0.0.1:{self.viewer.ws_port}/{query}"

  async def assert_refused(self, url: str, **kwargs):
    with pytest.raises(websockets.InvalidStatus) as refused:
      await websockets.connect(url, **kwargs)
    assert refused.value.response.status_code == 403

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
      assert json.loads(await ws.recv())["event"] == "scene"
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
    assert await anyio.to_thread.run_sync(self.get, "attacker.example:1338") == 403

  async def test_the_page_is_served_to_a_known_name_without_the_token(self):
    """Only the link the viewer prints carries the token, in a fragment."""
    for host in ("127.0.0.1:1338", "localhost:1338", "[::1]:1338"):
      assert await anyio.to_thread.run_sync(self.get, host) == 200, host
    page = await anyio.to_thread.run_sync(
      lambda: urllib.request.urlopen(f"http://127.0.0.1:{self.viewer.fs_port}/").read().decode()
    )
    assert self.viewer.token not in page
    assert "{{" not in page
    assert self.viewer.url.endswith(f"/#token={self.viewer.token}")

  async def test_a_socket_is_heard_once(self):
    """Only the first hello on a socket is kept and printed."""
    hello = json.dumps({"event": "hello", "data": {"backend": "WebGL2"}})
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
      async with websockets.connect(self.ws(self.viewer.token), max_size=None) as ws:
        for _ in range(3):
          await ws.send(hello)
        await ws.send("not json")
        await (await ws.ping())  # answered once everything sent before it has been read
    assert len(self.viewer.clients_seen) == 1
    assert printed.getvalue().count("a browser connected") == 1

  async def test_a_token_from_another_run_is_refused(self):
    """A page served by an earlier run keeps that run's token, and it must not open this one."""
    other = Viewer3D(self.facility, open_browser=False)
    await self.assert_refused(self.ws(other.token))
