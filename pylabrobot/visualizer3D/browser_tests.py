"""The whole chain, in a real browser: a resource moves, and the picture follows.

Everything else here is tested without a renderer, which is why the failures this catches were the
ones that survived longest: the model was right, the message was right, and nothing was drawn. It
loads the page in headless Chrome, moves a resource, and reads back where the viewer thinks things
are - including a child, which follows only because its parent's world transform was recomputed.

Skipped where there is no Chrome to drive, so it is a no-op on a computer without one.
"""

import asyncio
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zlib
from typing import Any, List, Optional

import websockets

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.hamilton import hamilton_96_tiprack_50uL_NTR
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.tip_tracking import does_tip_tracking, set_tip_tracking
from pylabrobot.visualizer3D.demo import build_facility, declare_channel_access, star_of
from pylabrobot.visualizer3D.facility import Facility
from pylabrobot.visualizer3D.server import Viewer3D
from pylabrobot.visualizer3D.server_tests import free_ports, track_volumes


def _find_chrome() -> str:
  """Where a headless Chrome is, or empty: `PLR_CHROME`, then the path, then the macOS install."""
  named = os.environ.get("PLR_CHROME", "")
  if named:
    return named
  for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
    found = shutil.which(name)
    if found is not None:
      return found
  installed = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  return installed if os.path.isfile(installed) else ""


CHROME = _find_chrome()
# ANGLE draws through the platform's own graphics API, and Metal is macOS's. A runner has no GPU
# to reach either way, so it draws in software, unsandboxed as CI needs.
PLATFORM_FLAGS = (
  ["--use-angle=metal"]
  if sys.platform == "darwin"
  else ["--use-angle=swiftshader", "--no-sandbox", "--disable-dev-shm-usage"]
)
FS_PORT, WS_PORT = free_ports(2)


class Browser:
  """Headless Chrome, driven over the devtools protocol. Enough of it to load a page and ask it
  questions. Each one has a devtools port of its own: the last may still be letting go of its."""

  def __init__(self) -> None:
    self._cdp_port = free_ports(1)[0]
    self._profile = tempfile.mkdtemp()
    self._chrome: Optional[subprocess.Popen] = None
    self._socket: Any = None
    self._id = 0

  async def __aenter__(self) -> "Browser":
    self._chrome = subprocess.Popen(
      [
        CHROME,
        "--headless=new",
        f"--remote-debugging-port={self._cdp_port}",
        f"--user-data-dir={self._profile}",
        "--enable-unsafe-webgpu",
        *PLATFORM_FLAGS,
        "--window-size=1200,800",
        "--no-first-run",
        "about:blank",
      ],
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
    )
    # A browser nobody attached to would outlive the test, so a failed attach lets go of it.
    try:
      deadline = time.monotonic() + 30
      while True:
        try:
          tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self._cdp_port}/json"))
          url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
          self._socket = await websockets.connect(url, max_size=None)
          return self
        except Exception:
          if time.monotonic() >= deadline:
            raise RuntimeError("could not attach to a headless browser") from None
          await asyncio.sleep(0.1)
    except BaseException:
      await self.__aexit__()
      raise

  async def __aexit__(self, *_: Any) -> None:
    if self._socket is not None:
      await self._socket.close()
      self._socket = None
    if self._chrome is not None:
      self._chrome.kill()
      self._chrome.wait(timeout=5)  # reaped, or every test leaves a zombie behind
      self._chrome = None
    shutil.rmtree(self._profile, ignore_errors=True)

  async def _call(self, method: str, params: Optional[dict] = None, timeout: float = 20.0) -> Any:
    """Send one command and wait for its reply, stepping over the notifications in between.

    Bounded on purpose: a page busy enough not to answer is a failure worth reporting, not a reason
    for the suite to hang.
    """
    self._id += 1
    await self._socket.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))

    async def reply() -> Any:
      while True:
        message = json.loads(await self._socket.recv())
        if message.get("id") == self._id:
          return message

    return await asyncio.wait_for(reply(), timeout)

  async def emulate_scale(self, factor: int) -> None:
    """Give the screen `factor` device pixels per CSS pixel, as a retina one has: headless has 1."""
    await self._call(
      "Emulation.setDeviceMetricsOverride",
      {"width": 1200, "height": 800, "deviceScaleFactor": factor, "mobile": False},
    )

  async def open(self, url: str) -> None:
    await self._call("Page.enable")
    await self._call("Page.navigate", {"url": url})

  async def evaluate(self, expression: str) -> Any:
    reply = await self._call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    if reply["result"].get("exceptionDetails"):
      raise RuntimeError(json.dumps(reply["result"]["exceptionDetails"])[:300])
    return reply["result"]["result"].get("value")

  async def drawn_fraction(self, selector: str) -> float:
    """How much of an element's screen is not background: what a viewer has drawn there."""
    box = await self.evaluate(
      f"document.querySelector({selector!r}).getBoundingClientRect().toJSON()"
    )
    clip = {
      "x": box["x"],
      "y": box["y"],
      "width": box["width"],
      "height": box["height"],
      "scale": 1,
    }
    shot = await self._call("Page.captureScreenshot", {"format": "png", "clip": clip})
    rows = _png_luminance(base64.b64decode(shot["result"]["data"]))
    pixels = [v for row in rows for v in row]
    return sum(1 for v in pixels if v < 200) / len(pixels)

  async def settle(self, expression: str, seconds: float = 25.0) -> Any:
    """Wait for an expression to answer something truthy, asked at once and then every 0.1 s."""
    # Bounded by the clock rather than by a number of tries, so a slow answer cannot multiply
    # into a suite that never finishes.
    deadline = time.monotonic() + seconds
    while True:
      try:
        value = await self.evaluate(expression)
      except (RuntimeError, asyncio.TimeoutError):
        value = None
      if value:
        return value
      if time.monotonic() >= deadline:
        raise AssertionError(f"the page never answered within {seconds:.0f}s: {expression}")
      await asyncio.sleep(0.1)

  async def frames(self, count: int = 2) -> None:
    """Wait for `count` animation frames: what the page drew before them is on screen by then."""
    step = "(n) => (n ? requestAnimationFrame(() => step(n - 1)) : done())"
    await self._call(
      "Runtime.evaluate",
      {
        "expression": f"new Promise((done) => {{ const step = {step}; step({count}); }})",
        "awaitPromise": True,
      },
    )


def _png_luminance(png: bytes) -> List[List[int]]:
  """An 8-bit RGB or RGBA PNG as rows of luminance, without any imaging library."""
  assert png[:8] == b"\x89PNG\r\n\x1a\n"
  at, width, height, channels, data = 8, 0, 0, 0, b""
  while at < len(png):
    (length,) = struct.unpack(">I", png[at : at + 4])
    kind, body = png[at + 4 : at + 8], png[at + 8 : at + 8 + length]
    if kind == b"IHDR":
      width, height, depth, colour = struct.unpack(">IIBB", body[:10])
      assert depth == 8 and colour in (2, 6), "an 8-bit RGB or RGBA image"
      channels = 3 if colour == 2 else 4
    elif kind == b"IDAT":
      data += body
    at += 12 + length
  raw = zlib.decompress(data)
  stride = width * channels
  rows: List[List[int]] = []
  previous = bytearray(stride)
  for y in range(height):
    start = y * (stride + 1)
    filter_, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
    for i in range(stride):
      a = line[i - channels] if i >= channels else 0
      b = previous[i]
      c = previous[i - channels] if i >= channels else 0
      if filter_ == 1:
        line[i] = (line[i] + a) & 255
      elif filter_ == 2:
        line[i] = (line[i] + b) & 255
      elif filter_ == 3:
        line[i] = (line[i] + (a + b) // 2) & 255
      elif filter_ == 4:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
    rows.append(
      [
        (299 * line[i] + 587 * line[i + 1] + 114 * line[i + 2]) // 1000
        for i in range(0, stride, channels)
      ]
    )
    previous = line
  return rows


# Clicks the row of a named resource three pixels into its arrow or its name, and answers whether
# the row is selected and what its arrow shows.
ROW_CLICK = (
  "((name, where) => {"
  "  const index = window.plrViewer.resources().indexOf(name);"
  "  const row = document.querySelector(`.tree-node-row[data-index='${index}']`);"
  "  const target = row.querySelector(where === 'arrow' ? '.tree-node-arrow' : '.tree-node-name');"
  "  const box = target.getBoundingClientRect();"
  "  target.dispatchEvent(new MouseEvent('click', {"
  "    bubbles: true, clientX: box.left + 3, clientY: box.top + box.height / 2 }));"
  "  const arrow = row.querySelector('.tree-node-arrow').textContent;"
  "  return [row.classList.contains('selected'), arrow];"
  "})"
)
# What the arrow of a named resource's row shows.
ROW_ARROW = (
  "((name) => document.querySelector(`.tree-node-row[data-index="
  "'${window.plrViewer.resources().indexOf(name)}'] .tree-node-arrow`).textContent)"
)
# The text of one live value in the info panel, with its non-breaking spaces as plain ones.
LIVE = (
  "((key) => document.querySelector(`.uml-panel [data-live='${key}']`)"
  "?.textContent.replace(/\\u00a0/g, ' ') ?? null)"
)


def modelled(viewer: Viewer3D) -> int:
  """How many resources the page draws from a file: what `models()` counts once all have arrived."""
  scene = viewer._scene_message()
  return sum(1 for model in scene["instances"]["model"] if "mesh" in scene["models"][model])


@unittest.skipUnless(CHROME, "no headless browser to drive")
class BrowserTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.facility = Facility(name="facility", size_x=2000, size_y=1000, size_z=500)
    self.carrier = Resource(name="carrier", size_x=200, size_y=100, size_z=50)
    self.rider = Resource(name="rider", size_x=40, size_y=40, size_z=20)
    self.facility.assign_child_resource(self.carrier, location=Coordinate(100, 100, 0))
    self.carrier.assign_child_resource(self.rider, location=Coordinate(20, 20, 50))
    self.viewer = Viewer3D(
      self.facility, open_browser=False, fs_port=FS_PORT, ws_port=WS_PORT, name="tests"
    )
    await self.viewer.start()

  async def asyncTearDown(self):
    await self.viewer.stop()

  async def page(self, browser: Browser, ready: str = "rider") -> None:
    """Open the viewer and wait until the page lists `ready`."""
    await browser.open(self.viewer.url)
    listed = f"window.plrViewer && window.plrViewer.resources().includes({ready!r})"
    await browser.settle(listed, 30)

  async def world_x(self, browser: Browser, name: str) -> float:
    """Where the viewer draws a resource, read off the scene it holds."""
    return float(await browser.evaluate(f"window.plrViewer.worldOf({name!r})[0]"))

  async def test_a_scene_from_another_protocol_is_named_not_drawn(self):
    """The page is fetched fresh on every load while the Python serving it is as old as its
    process, and a page reading a scene from an older server misread it in silence: positions
    stopped updating and nothing said why."""
    async with Browser() as browser:
      await self.page(browser)
      foreign = {**self.viewer._scene_message(), "protocol": 0, "stats": {"instances": 999}}
      await self.viewer._broadcast("scene", foreign)
      await browser.settle(
        "document.getElementById('boot-diagnosis')?.textContent.includes('older than this page')",
        10,
      )
      # Named, and nothing else: its measurements are set once a scene is taken up.
      self.assertNotEqual((await browser.evaluate("window.plrViewer.stats()"))["instances"], 999)

  async def test_a_frame_the_page_cannot_read_does_not_stop_the_next_one(self):
    """The page parsed every frame unguarded and applied a state without looking at it, so one
    message that was not JSON, or a state with nothing in it, threw inside the handler."""
    async with Browser() as browser:
      await self.page(browser)
      await browser.evaluate(
        "window.__plrErrors = 0; window.addEventListener('error', () => { window.__plrErrors++; })"
      )
      for client in list(self.viewer._clients):
        await client.send("this is not json")
        await client.send(json.dumps({"event": "bogus"}))
        await client.send(json.dumps({"event": "state", "data": {}}))
        await client.send(json.dumps({"event": "moves", "data": {"epoch": 0}}))
      self.carrier.location = Coordinate(300, 100, 0)
      await browser.settle("window.plrViewer.worldOf('rider')[0] === 320", 10)
      errors = await browser.evaluate("window.__plrErrors")
      self.assertEqual(errors, 0)

  async def test_a_move_reaches_the_picture_and_carries_its_children(self):
    async with Browser() as browser:
      await self.page(browser)

      self.assertEqual(await self.world_x(browser, "carrier"), 100)
      self.assertEqual(await self.world_x(browser, "rider"), 120)

      self.carrier.location = Coordinate(700, 100, 0)
      await browser.settle("window.plrViewer.worldOf('carrier')[0] === 700")

      # The rider never moved in the model: it follows because its parent's world transform was
      # worked out again. Forgetting that is what left a 96-head standing still while its arm swept.
      self.assertEqual(await self.world_x(browser, "rider"), 720)

  async def test_a_quality_level_changes_the_pixel_ratio_and_the_lighting(self):
    """The page steps quality down on its own when frames are slow; the levels are driven here.
    On a screen of two device pixels per CSS pixel, or the lowest level's ratio is the full one."""
    async with Browser() as browser:
      await browser.emulate_scale(2)
      await self.page(browser)
      full = await browser.evaluate("window.plrViewer.quality()")
      self.assertEqual((full["level"], full["pixelRatio"], full["environment"]), (0, 2, True))
      lowest = await browser.evaluate("window.plrViewer.quality(2)")
      self.assertEqual(
        (lowest["level"], lowest["pixelRatio"], lowest["environment"]), (2, 1, False)
      )
      back = await browser.evaluate("window.plrViewer.quality(0)")
      self.assertEqual((back["level"], back["pixelRatio"], back["environment"]), (0, 2, True))

  async def test_a_scene_keeps_what_the_reader_had_open(self):
    """Every scene rebuilt the tree folded, dropped the selection and closed the panel, and a
    deck being laid out sends a scene on every assignment."""
    async with Browser() as browser:
      await self.page(browser)
      await browser.evaluate("window.plrViewer.focus('rider', 'top')")
      row = "document.querySelector(`.tree-node-row[data-index='${window.plrViewer.resources().indexOf('rider')}']`)"
      await browser.settle(f"{row}?.classList.contains('selected')", 10)
      self.facility.assign_child_resource(
        Resource(name="late", size_x=10, size_y=10, size_z=10), location=Coordinate(0, 0, 0)
      )
      await browser.settle("window.plrViewer.resources().includes('late')", 30)
      self.assertTrue(await browser.evaluate(f"{row}?.classList.contains('selected') ?? false"))
      panel = await browser.evaluate("document.querySelector('.uml-panel')?.textContent ?? ''")
      self.assertIn("rider", panel)

  async def test_a_scene_keeps_the_meshes_of_what_did_not_change(self):
    """A deck being laid out sends a scene on every assignment, and an instanced mesh built afresh
    costs the renderer a shader state on its first frame: half a second a rebuild on a full deck.
    A model whose resources are unchanged keeps its mesh; one whose set changed gets a new one."""
    async with Browser() as browser:
      await self.page(browser)
      # Each model's mesh, told apart by how big the model is: 200 is the carrier, 40 the rider.
      mesh = "Object.fromEntries(window.plrViewer.detail().map((e) => [e.mm, e.id]))"
      before = await browser.evaluate(mesh)
      self.facility.assign_child_resource(
        Resource(name="late", size_x=10, size_y=10, size_z=10), location=Coordinate(0, 0, 0)
      )
      await browser.settle("window.plrViewer.resources().includes('late')", 30)
      after = await browser.evaluate(mesh)
      self.assertEqual(after["200"], before["200"], "the carrier's mesh was built again")
      self.assertEqual(after["40"], before["40"], "the rider's mesh was built again")
      self.assertIn("10", after)
      # A second resource of the rider's model: two instances, which a mesh of one cannot hold.
      self.carrier.assign_child_resource(
        Resource(name="rider_2", size_x=40, size_y=40, size_z=20), location=Coordinate(120, 20, 50)
      )
      await browser.settle("window.plrViewer.resources().includes('rider_2')", 30)
      final = await browser.evaluate(mesh)
      self.assertEqual(final["200"], before["200"], "the carrier's mesh was built again")
      self.assertNotEqual(final["40"], after["40"], "a mesh of one instance was kept for two")

  async def test_a_click_on_a_row_selects_unless_it_is_on_the_arrow(self):
    """The fold zone was measured from the element under the pointer, not from the row, so the
    first twenty pixels of the name folded the row instead of selecting it."""
    async with Browser() as browser:
      await self.page(browser, "carrier")
      # The arrow folds without selecting; the name, on an open row, selects without folding.
      self.assertEqual(await browser.evaluate(f"{ROW_CLICK}('carrier', 'arrow')"), [False, "▼"])
      self.assertEqual(await browser.evaluate(f"{ROW_CLICK}('carrier', 'name')"), [True, "▼"])

  async def test_a_scene_keeps_a_row_the_reader_opened(self):
    """A row opened by its arrow, with nothing selected, is open again once a scene has arrived."""
    async with Browser() as browser:
      await self.page(browser, "carrier")
      self.assertEqual(await browser.evaluate(f"{ROW_CLICK}('carrier', 'arrow')"), [False, "▼"])
      self.facility.assign_child_resource(
        Resource(name="late", size_x=10, size_y=10, size_z=10), location=Coordinate(0, 0, 0)
      )
      await browser.settle("window.plrViewer.resources().includes('late')", 30)
      self.assertEqual(await browser.evaluate(f"{ROW_ARROW}('carrier')"), "▼")

  async def test_the_websocket_is_reached_the_way_the_page_was(self):
    """A page served over https may only open wss:, and one reached by a name connects to that
    name, not to 127.0.0.1."""
    async with Browser() as browser:
      await browser.open(f"http://localhost:{self.viewer.fs_port}/#token={self.viewer.token}")
      await browser.settle("window.plrViewer && window.plrViewer.resources().length", 30)
      self.assertTrue(
        (await browser.evaluate("window.WS_URL")).startswith(f"ws://localhost:{WS_PORT}/?token=")
      )
      self.assertTrue(
        (
          await browser.evaluate("window.wsUrlFor({ protocol: 'https:', hostname: 'lab.example' })")
        ).startswith(f"wss://lab.example:{WS_PORT}/?token=")
      )

  async def test_a_declared_colour_cannot_write_markup_into_the_search(self):
    """A search result spliced the colour into its markup, so a string colour closed the
    attribute and ran whatever followed."""
    painted = Resource(name="painted", size_x=10, size_y=10, size_z=10)
    setattr(painted, "appearance", {"color": '0"><img src=x onerror="window.pwned=1">'})
    self.facility.assign_child_resource(painted, location=Coordinate(500, 100, 0))
    async with Browser() as browser:
      await self.page(browser, "painted")
      search = (
        "(() => { const input = document.getElementById('search-input');"
        " input.value = 'painted'; input.dispatchEvent(new Event('input'));"
        " const results = document.getElementById('search-results');"
        " return [results.querySelectorAll('.search-result').length,"
        " results.querySelectorAll('img').length]; })()"
      )
      self.assertEqual(await browser.evaluate(search), [1, 0])
      await asyncio.sleep(0.5)  # an injected image would have failed to load by now
      self.assertIsNone(await browser.evaluate("window.pwned"))

  async def diagnosis_with_blocked(self, blocked: List[str], seconds: float) -> str:
    """The page's diagnosis heading with `blocked` failing as a server that went away fails."""
    async with Browser() as browser:
      await browser._call("Network.enable")
      await browser._call("Network.setBlockedURLs", {"urls": blocked})
      await browser.open(self.viewer.url)
      heading = "document.querySelector('#boot-diagnosis h2')?.textContent"
      return str(await browser.settle(heading, seconds))

  async def test_a_page_whose_viewer_stopped_while_it_loaded_says_so(self):
    """A run over before the page loaded its files was reported as a renderer that could not
    start, or as a browser too old: the page blamed the GPU for a server that had gone."""
    stopped = "The viewer stopped while this page loaded"
    # app.js fails to import, and the page's own address no longer answers.
    self.assertEqual(await self.diagnosis_with_blocked(["*/app.js", "*/index.html"], 30), stopped)
    # boot.js never arrives, so only the guard in the page is left to say it.
    self.assertEqual(await self.diagnosis_with_blocked(["*/boot.js", "*/index.html"], 30), stopped)
    # The page is still served: a failed import is the renderer's, as before.
    self.assertEqual(
      await self.diagnosis_with_blocked(["*/app.js"], 30), "The viewer could not start"
    )

  async def test_the_token_leaves_the_address_and_a_reload_still_connects(self):
    """The token stood in the address bar, so it went into bookmarks and onto a shared screen."""
    async with Browser() as browser:
      await self.page(browser, "carrier")
      self.assertEqual(await browser.evaluate("location.href"), f"{self.viewer.url.split('#')[0]}")
      await browser.evaluate("location.reload()")
      await browser.settle("window.plrViewer && window.plrViewer.resources().length", 30)
      self.assertIn(self.viewer.token, await browser.evaluate("window.WS_URL"))

  async def test_the_hover_readout_stays_inside_the_viewport(self):
    """Placed beside the pointer, the readout ran off the right and bottom edges at a resource
    that stood near them."""
    async with Browser() as browser:
      await self.page(browser, "carrier")
      # Close enough that the carrier fills the viewport, corners included: eight steps of 0.8.
      zoom = "window.plrViewer.camera().zoom"
      await browser.evaluate("window.plrViewer.focus('carrier', 'top')")
      framed = await browser.evaluate(zoom)
      await browser.evaluate(
        "for (let i = 0; i < 8; i++) document.getElementById('zoom-in-btn').click(); true"
      )
      await browser.settle(f"{zoom} > 5 * {framed}")
      await browser.evaluate(
        "(() => {"
        "  const canvas = document.querySelector('#viewport canvas');"
        "  const box = canvas.getBoundingClientRect();"
        "  canvas.dispatchEvent(new PointerEvent('pointermove', {"
        "    bubbles: true, clientX: box.right - 2, clientY: box.bottom - 2, buttons: 0 }));"
        "  return true;"
        "})()"
      )
      placed = await browser.settle(
        "(() => {"
        "  const readout = document.getElementById('hover-readout');"
        "  if (readout.style.display !== 'block') return null;"
        "  const box = readout.getBoundingClientRect();"
        "  const viewport = document.getElementById('viewport').getBoundingClientRect();"
        "  return { text: readout.textContent, right: box.right - viewport.right,"
        "           bottom: box.bottom - viewport.bottom };"
        "})()",
        10,
      )
      self.assertIn("carrier", placed["text"])
      self.assertLessEqual(placed["right"], 0)
      self.assertLessEqual(placed["bottom"], 0)

  async def test_a_state_message_leaves_a_panel_it_does_not_touch_alone(self):
    """The info panel was drawn again on every state message whether or not it showed anything
    the message touched, so a section the reader had opened in it snapped shut on every well of a
    running protocol."""
    async with Browser() as browser:
      await self.page(browser)
      await browser.evaluate("window.plrViewer.focus('carrier', 'top')")
      opened = "document.querySelector('.uml-panel details')"
      await browser.settle(f"!!{opened}", 10)
      await browser.evaluate(f"{opened}.open = true")
      self.rider.location = Coordinate(25, 25, 50)
      await browser.settle("window.plrViewer.worldOf('rider')[0] === 125", 10)
      self.assertTrue(await browser.evaluate(f"{opened}.open"))

  async def test_slow_frames_step_the_quality_down_on_their_own(self):
    """The frame cost was this thread's time around the render call, which a GPU or a rasteriser
    in another process never shows up in, so the machines the levels exist for never stepped down.
    Here the frames are made slow where the page cannot see it: in the gap between them."""
    async with Browser() as browser:
      await self.page(browser)
      await browser.evaluate(
        "const raf = window.requestAnimationFrame.bind(window);"
        "window.requestAnimationFrame = (cb) => setTimeout(() => raf(cb), 60);"
        "setInterval(() => window.plrViewer.view('iso'), 30);"
      )
      await browser.settle("window.plrViewer.quality().level >= 1", 20)

  async def test_a_recording_stopped_before_its_first_frame_holds_the_view(self):
    """On a slow renderer a short recording had captured nothing by its stop, and the panel said
    "No frames captured". The view it was stopped on is its frame."""
    async with Browser() as browser:
      await self.page(browser, "carrier")
      # One script turn: no frame is drawn between the start and the stop, so none is captured.
      await browser.evaluate(
        "document.getElementById('toolbar-gif-btn').click();"
        "document.getElementById('start-recording-button').click();"
        "document.getElementById('stop-recording-button').click(); true"
      )
      await browser.settle("document.getElementById('gif-download').style.display === 'flex'", 30)

  async def test_a_recorded_frame_is_bounded_whatever_the_pixel_ratio(self):
    """A frame was kept at the drawing buffer's size, which at a pixel ratio of 2 is four times
    the viewport: tens of megabytes a frame, held until the GIF was rendered from all of them."""
    async with Browser() as browser:
      await browser._call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1200, "height": 800, "deviceScaleFactor": 2, "mobile": False},
      )
      await browser._call("Browser.setDownloadBehavior", {"behavior": "deny"})
      await browser.open(
        f"http://127.0.0.1:{self.viewer.fs_port}/?quality=high#token={self.viewer.token}"
      )
      await browser.settle("window.plrViewer && window.plrViewer.resources().includes('rider')", 30)
      buffer_width = int(await browser.evaluate("document.querySelector('#viewport canvas').width"))
      self.assertGreater(buffer_width, 960)
      await browser.evaluate(
        "const objectUrl = URL.createObjectURL.bind(URL);"
        "URL.createObjectURL = (blob) => {"
        "  blob.slice(0, 10).arrayBuffer().then((head) => {"
        "    const h = new Uint8Array(head);"
        "    window.__gifSize = [h[6] + 256 * h[7], h[8] + 256 * h[9]];"
        "  });"
        "  return objectUrl(blob);"
        "};"
        "window.__gifWarnings = [];"
        "const warn = console.warn.bind(console);"
        "console.warn = (...args) => { window.__gifWarnings.push(args.map(String).join(' '));"
        "  warn(...args); };"
        "document.getElementById('toolbar-gif-btn').click();"
        "document.getElementById('start-recording-button').click(); true"
      )
      await asyncio.sleep(1.0)
      await browser.evaluate("document.getElementById('stop-recording-button').click(); true")
      try:
        await browser.settle("document.getElementById('gif-download').style.display === 'flex'", 60)
      except AssertionError as timed_out:
        # What the GIF panel says is what tells a runner that renders differently apart.
        said = await browser.evaluate(
          "JSON.stringify({progress: document.getElementById('progressBar')?.textContent,"
          " notice: document.querySelector('#gif-panel > p')?.textContent,"
          " warnings: window.__gifWarnings})"
        )
        raise AssertionError(f"{timed_out}; the GIF panel said {said}") from None
      await browser.evaluate("document.getElementById('gif-download-button').click(); true")
      width, height = await browser.settle("window.__gifSize", 10)
      self.assertLessEqual(width, 960)
      buffer_height = int(
        await browser.evaluate("document.querySelector('#viewport canvas').height")
      )
      # Smaller, not reframed: the GIF keeps the viewport's aspect.
      self.assertAlmostEqual(width / height, buffer_width / buffer_height, delta=0.02)

  async def test_a_well_shows_the_volume_it_holds_not_the_one_an_operation_would_leave(self):
    """The page drew the pending volume, so a well filled at the start of an aspirate that then
    failed kept a volume that never happened. The existing visualizer draws the committed volume,
    and so does this one now."""
    track_volumes(self)
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.facility.assign_child_resource(plate, location=Coordinate(600, 400, 0))
    well = plate.get_item("A1")
    async with Browser() as browser:
      await self.page(browser, well.name)
      await browser.evaluate(f"window.plrViewer.focus({well.name!r}, 'top')")
      panel = "document.querySelector('.uml-panel')?.textContent.replace(/\\u00a0/g, ' ')"
      await browser.settle(f"{panel}?.includes('0 / 360 uL')", 10)
      # A sentinel well set in the same batch: once it shows, whatever A1 published has too.
      well.tracker.add_liquid(50)
      sentinel = plate.get_item("H12")
      sentinel.tracker.set_volume(30)
      await browser.settle(f"window.plrViewer.stateOf({sentinel.name!r})?.volume === 30", 10)
      self.assertIn("0 / 360 uL", await browser.evaluate(panel))
      self.assertNotIn("50 / 360 uL", await browser.evaluate(panel))
      well.tracker.commit()
      await browser.settle(f"{panel}?.includes('50 / 360 uL')", 10)

  async def test_a_tip_shows_the_volume_it_holds(self):
    """A tip's capacity is its `maximal_volume`, not a `max_volume`, so its panel showed no liquid
    at all while its state carried the volume the device tools draw from."""
    track_volumes(self)
    rack = hamilton_96_tiprack_50uL_NTR(name="rack")
    self.facility.assign_child_resource(rack, location=Coordinate(600, 400, 0))
    tip = rack.get_item("A1").tip
    assert tip is not None
    async with Browser() as browser:
      await self.page(browser, tip.name)
      await browser.evaluate(f"window.plrViewer.focus({tip.name!r}, 'top')")
      await browser.settle(f"{LIVE}('volume') === '0 / 65 uL'", 10)
      # Once, in Contents: the state's own copy of the capacity is not listed again.
      panel = await browser.evaluate("document.querySelector('.uml-panel').textContent")
      self.assertNotIn("max_volume", panel)
      tip.tracker.set_volume(12.5)
      await browser.settle(f"{LIVE}('volume') === '12.50 / 65 uL'", 10)

  async def test_a_tree_that_changes_shape_keeps_the_models_it_had(self):
    """A scene arrives whole whenever the tree changes shape. Rebuilding the geometry for it took
    every model off screen and put the boxes back until the files had been fetched and parsed
    again, which is a flash of boxes on every pick-up, drop and move."""
    holder = ResourceHolder(name="holder", size_x=150, size_y=100, size_z=10)
    self.facility.assign_child_resource(holder, location=Coordinate(700, 500, 0))
    rack = hamilton_96_tiprack_50uL_NTR(name="rack")
    holder.assign_child_resource(rack)

    async with Browser() as browser:
      await self.page(browser, "rack")
      # Every model, not the first to arrive: the files are fetched and parsed in parallel and the
      # count climbs as they land, so a baseline taken early is taken too early.
      drawn = modelled(self.viewer)
      await browser.settle(f"window.plrViewer.models().length === {drawn}")

      # Watch while the tree changes shape under it: the rack goes somewhere else and a resource
      # arrives. A name the client does not have is what still costs a whole scene.
      await browser.evaluate(
        "window.__seen = [];"
        "window.__watch = setInterval(() => window.__seen.push(window.plrViewer.models().length), 20);"
        "true"
      )
      # Off one and onto the other in the same breath, which is what moving a resource is, and
      # the arrival with it: the server coalesces the burst into one scene.
      holder.unassign_child_resource(rack)
      self.carrier.assign_child_resource(rack, location=Coordinate(10, 10, 50))
      self.facility.assign_child_resource(
        Resource(name="late", size_x=10, size_y=10, size_z=10), location=Coordinate(0, 0, 0)
      )
      # The carrier stands at x 100, so the rack on it lands at 110.
      await browser.settle(
        "window.plrViewer.resources().includes('late')"
        " && window.plrViewer.worldOf('rack')[0] === 110"
      )
      # Sampled until every model is back: at once if they were kept, after a reload if not.
      await browser.settle(f"window.plrViewer.models().length === {drawn}")
      seen = await browser.evaluate("clearInterval(window.__watch); window.__seen")

      self.assertEqual(self.viewer.rebuilds, 1, "the arrival did not cost the scene it must")
      self.assertGreater(len(seen), 0, "nothing was sampled while the scene was rebuilt")
      self.assertEqual(min(seen), drawn, f"the models went away and came back: {seen}")


@unittest.skipUnless(CHROME, "no headless browser to drive")
class SimulationTests(unittest.IsolatedAsyncioTestCase):
  """A run on the simulated STAR leaves the page where the tree is."""

  async def test_after_a_run_every_resource_is_drawn_where_the_tree_has_it(self):
    """Positions travel three ways - in the scene, in a state's `locations`, and in a move - and
    volumes in the state; each is checked on its own elsewhere. This drives all of them at once on
    the demo facility and reads the page back against the tree, resource by resource. A joint's
    angle travels rounded to a tenth of a degree, which at the reach of an arm's link is a fifth
    of a millimetre, so that is how close the drawing is held to the tree.

    It also reads the pixels, before the run and after it in the other projection: every other
    test here reads the page's model of the scene, and a page once drew nothing at all until its
    quality level happened to change, on GPUs other than the one the change was made on."""
    track_volumes(self)
    facility = build_facility()
    star = star_of(facility)
    await star.setup()
    declare_channel_access(star)
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    await viewer.start()
    try:
      async with Browser() as browser:
        await browser.open(viewer.url)
        await browser.settle("window.plrViewer && window.plrViewer.resources().length > 3000", 60)
        await browser.settle(f"window.plrViewer.models().length === {modelled(viewer)}", 60)
        await browser.frames()
        self.assertGreater(await browser.drawn_fraction("#viewport"), 0.02, "the opening view")

        source = star.deck.get_resource("source_0")
        destination = star.deck.get_resource("destination_0")
        assert isinstance(source, Plate) and isinstance(destination, Plate)
        for well in ("A1", "B2", "H12"):
          source.get_item(well).tracker.set_volume(200.0)
          source.get_item(well).tracker.remove_liquid(50.0)
          destination.get_item(well).tracker.add_liquid(50.0)
          source.get_item(well).tracker.commit()
          destination.get_item(well).tracker.commit()
        x_range = star.x_arm.configuration.x_range
        assert x_range is not None
        low, high = x_range
        await star.x_arm.move_to_x_position(round(low + (high - low) * 0.6, 1))
        # A simulated pick-up and an aspirate, with the channel panel open through both: the
        # mounted tip is a new name, so this costs a scene, and what it then holds is drawn.
        await browser.evaluate(
          "document.querySelector('.mt-panel-single') ||"
          " document.querySelector('.dt-btn[title=\"Single-channel pipettes\"]').click(); true"
        )
        rack = star.deck.get_resource("tips_0")
        assert isinstance(rack, TipRack) and star.pipettes is not None
        await star.pipettes.pick_up_tips([rack.get_item("A1")])
        tip = star.pipettes.get_mounted_tip(0)
        assert tip is not None
        await star.pipettes.aspirate([source.get_item("A1")], [50.0])
        plate = star.deck.get_resource("source_1")
        holder = star.deck.get_resource("destination_carrier").children[4]
        assert isinstance(holder, ResourceHolder)
        plate.unassign()
        holder.assign_child_resource(plate)
        await browser.settle(
          "window.plrViewer.worldOf('source_1') && window.plrViewer.worldOf('source_1')[0] > 0", 30
        )
        # The arm's position and the last volume travelled before that move, and are read once
        # they are on the page: nothing else is still in flight.
        arm = star.x_arm.resource
        assert arm is not None
        await browser.settle(
          f"Math.abs(window.plrViewer.worldOf({arm.name!r})[0] - {arm.get_absolute_location().x})"
          " < 0.25",
          30,
        )
        await browser.settle(
          f"window.plrViewer.stateOf({destination.get_item('H12').name!r})?.volume === 50", 30
        )

        drawn = await browser.evaluate(
          "Object.fromEntries(window.plrViewer.resources().map((n) => [n, window.plrViewer.worldOf(n)]))"
        )
        astray = []
        for resource in facility.get_all_children():
          at = resource.get_absolute_location()
          x, y, z = drawn[resource.name]
          if max(abs(x - at.x), abs(y - at.y), abs(z - at.z)) > 0.25:
            astray.append((resource.name, (round(x, 1), round(y, 1), round(z, 1)), at))
        self.assertEqual(astray[:5], [], f"{len(astray)} of {len(drawn)} drawn elsewhere")

        for plate_, well in ((source, "A1"), (destination, "B2")):
          shown = await browser.evaluate(
            f"window.plrViewer.stateOf({plate_.get_item(well).name!r})"
          )
          self.assertEqual(shown["volume"], plate_.get_item(well).tracker.get_used_volume())

        await browser.settle(f"window.plrViewer.stateOf({tip.name!r})?.volume === 50", 10)
        # The liquid is the one translucent fill in the panel's drawing of a tip.
        tip_fill = (
          "((name) => !!document.querySelector(`.mt-panel-single g[data-index="
          "'${window.plrViewer.resources().indexOf(name)}'] [fill^='rgba(']`))"
        )
        await browser.settle(f"{tip_fill}({tip.name!r})", 10)

        await browser.evaluate("window.plrViewer.view('iso')")
        await browser.frames()
        self.assertGreater(await browser.drawn_fraction("#viewport"), 0.02, "the iso view")
    finally:
      await viewer.stop()

  async def test_a_tip_is_listed_by_what_holds_it(self):
    """A tip spot or a channel's shaft holds its tip as a child, and its panel said nothing of it:
    what it held, and what was in that, could only be read off the tree and the device tools."""
    track_volumes(self)
    was_tracking = does_tip_tracking()
    set_tip_tracking(True)  # or the spot keeps a tip of its own and the shaft gets a new one
    self.addCleanup(set_tip_tracking, was_tracking)
    facility = build_facility()
    star = star_of(facility)
    await star.setup()
    declare_channel_access(star)
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    await viewer.start()
    try:
      async with Browser() as browser:
        await browser.open(viewer.url)
        await browser.settle("window.plrViewer && window.plrViewer.resources().length > 3000", 60)
        rack = star.deck.get_resource("tips_0")
        assert isinstance(rack, TipRack) and star.pipettes is not None
        spot = rack.get_item("A1")
        tip = spot.tip
        assert tip is not None
        capacity = f"{tip.maximal_volume:g} uL"
        header = "document.querySelector('.uml-header-name')?.textContent"
        await browser.evaluate(f"window.plrViewer.focus({spot.name!r}, 'top')")
        await browser.settle(f"{LIVE}('tip') === '{tip.name}, 0 / {capacity}'", 10)

        await star.pipettes.pick_up_tips([spot])
        shaft = tip.parent
        assert shaft is not None and shaft is not spot
        # The move draws the spot's panel again, without the tip it gave up.
        await browser.settle(f"{header} === {spot.name!r} && {LIVE}('tip') === null", 30)

        source = star.deck.get_resource("source_0")
        assert isinstance(source, Plate)
        source.get_item("A1").tracker.set_volume(200.0)
        await star.pipettes.aspirate([source.get_item("A1")], [50.0])
        await browser.settle(f"window.plrViewer.stateOf({tip.name!r})?.volume === 50", 30)
        await browser.evaluate(f"window.plrViewer.focus({shaft.name!r}, 'top')")
        await browser.settle(f"{LIVE}('tip') === '{tip.name}, 50 / {capacity}'", 10)
    finally:
      await viewer.stop()


if __name__ == "__main__":
  unittest.main()
