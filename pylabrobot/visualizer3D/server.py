"""Serve a flattened resource tree to the browser and keep it current.

Same two servers as the existing visualizer, a static file server and a websocket, because that
part of the design was never the problem: it is what lets the viewer sit on a laptop while the
protocol runs on the instrument host. What changed is the payload.
"""

import asyncio
import functools
import hashlib
import hmac
import http.server
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import socket
import threading
import webbrowser
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlsplit

import websockets

from pylabrobot.resources.resource import Resource

from .scene import build_scene, collect_state, pack_state, state_signature

logger = logging.getLogger(__name__)


def _finite(obj: Any) -> Any:
  """Replace non-finite floats with strings.

  `json.dumps` writes bare `Infinity` and `NaN`, which are not JSON and make `JSON.parse` throw in
  the browser. A trough's max volume is genuinely infinite, so this is reached on an ordinary deck.
  """
  if isinstance(obj, float) and not math.isfinite(obj):
    if math.isnan(obj):
      return "NaN"
    return "Infinity" if obj > 0 else "-Infinity"
  if isinstance(obj, dict):
    return {k: _finite(v) for k, v in obj.items()}
  if isinstance(obj, (list, tuple)):
    return [_finite(v) for v in obj]
  return obj


def _hostname_of(authority: Optional[str]) -> Optional[str]:
  """The hostname in a `Host` header or an `Origin`, lower-cased and without port or brackets."""
  if not authority:
    return None
  netloc = urlsplit(authority if "//" in authority else f"//{authority}").hostname
  return netloc.lower() if netloc else None


def _is_ip_literal(hostname: str) -> bool:
  try:
    ipaddress.ip_address(hostname)
    return True
  except ValueError:
    return False


HELLO_FIELDS = ("backend", "renderer", "error", "userAgent")


def _printable(value: Any, limit: int = 200) -> Optional[str]:
  """A browser-supplied value as short, single-line printable text, or None."""
  if not isinstance(value, str) or not value:
    return None
  return re.sub(r"[^\x20-\x7e]", "?", value)[:limit]


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Where a resource's geometry is looked for when it does not say. A file named after the model it
# belongs to, anywhere under the package, is that model's geometry - so a resource ships with its
# own geometry beside the code that describes it, and neither the resource nor the caller has to
# name a path. Model names are already namespaced by manufacturer and device, which is what lets
# one flat index be unambiguous across every package.
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_SUFFIX = ".glb"


@functools.lru_cache(maxsize=None)
def _models_on_disk(root: str) -> Dict[str, str]:
  """Every model file under `root`, by the model name it is named after.

  Walked once per root and remembered: a viewer is started per run, but a test suite starts many,
  and the answer only changes when files do.

  A name found twice is ambiguous - two packages cannot both own one model name - so neither is
  offered, and the collision is reported rather than silently resolved by walk order.
  """
  found: Dict[str, str] = {}
  clashes: Dict[str, List[str]] = {}
  for directory, _, files in os.walk(root):
    for file in files:
      if not file.endswith(MODEL_SUFFIX):
        continue
      model = file[: -len(MODEL_SUFFIX)]
      path = os.path.join(directory, file)
      if model in found and found[model] != path:
        clashes.setdefault(model, [found[model]]).append(path)
        continue
      found[model] = path
  for model, paths in clashes.items():
    logger.warning(
      "%s is named by more than one model file, so it is drawn as a box: %s",
      model,
      ", ".join(sorted(paths)),
    )
    found.pop(model, None)
  return found


class Viewer3D:
  """A parallel visualizer that takes any resource as its world.

  Args:
    root: the resource that is the world. Every descendant is placed in its cartesian space.
    host: interface to bind both servers to.
    fs_port: static file server port.
    models_root: directory that every `Resource.reference_glb` is relative to. Only resources that
      declare one need it; a model shipped under the package is found without it.
    ws_port: websocket port.
    open_browser: whether to open a browser window on start.
    name: what to show in the header, as the existing visualizer shows the calling script.
    allowed_hosts: extra hostnames a browser may reach the viewer by, e.g. a DNS name for this
      machine. IP addresses, `localhost`, and this machine's hostname (and `<hostname>.local`) are
      always accepted.

  Who may watch. The deck state is not public, and a websocket is not covered by the browser's
  same-origin policy: any web page open in the operator's browser could otherwise connect to
  `ws://127.0.0.1:<ws_port>` and read it. So every run makes a secret token, bakes it into the page
  it serves, and refuses a websocket without it. The token only protects anything while a foreign
  page cannot read ours, which DNS rebinding would allow - a hostile name resolved to 127.0.0.1 makes
  the page same-origin with it. Both servers therefore also refuse a hostname they do not recognise, in
  the HTTP `Host` header and in the websocket `Origin`. An IP address cannot be rebound, so any is
  accepted, which is what keeps an SSH tunnel or a LAN address working without configuration.
  """

  def __init__(
    self,
    root: Resource,
    host: str = "127.0.0.1",
    fs_port: int = 1338,
    ws_port: int = 2122,
    open_browser: bool = True,
    name: str = "facility",
    models_root: Optional[str] = None,
    allowed_hosts: Iterable[str] = (),
  ):
    self.root = root
    self.host = host
    self.fs_port = fs_port
    self.ws_port = ws_port
    self.open_browser = open_browser
    self.name = name
    self.token = secrets.token_urlsafe(32)
    machine = socket.gethostname().lower()
    # The interface bound to counts as a way in when it is a name rather than an address.
    self.allowed_hosts = {"localhost", machine, f"{machine}.local", host.lower()} | {
      h.lower().strip("[]") for h in allowed_hosts
    }
    # Where a resource's `reference_glb` is resolved from. One root for the whole scene, so a
    # resource names its model the same way wherever the tree is built and whoever runs it.
    self.models_root = os.path.abspath(os.path.expanduser(models_root)) if models_root else None

    self._clients: set = set()
    self._httpd: Optional[http.server.HTTPServer] = None
    self._pending: Dict[str, dict] = {}
    self._flush_scheduled = False
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    self._stats: Dict[str, Any] = {}
    self._legacy_bytes: Optional[int] = None  # measured once; it costs a full extra serialization
    # Files a resource declared as its own geometry, by the id the page fetches them under. Only a
    # path that a resource named is ever served, so this doubles as the whitelist.
    self._mesh_files: Dict[str, str] = {}
    # Which scene the indices below belong to, and the index of every resource in it. State is
    # addressed by index rather than by name, so both have to be current before any is sent.
    self._epoch = 0
    self._index_of: Dict[str, int] = {}
    # What each resource last looked like on the wire. A resource that publishes a change too small
    # to see produces the same signature and is not sent again.
    self._published: Dict[str, str] = {}
    # Models derived by the last flatten, by resource name, and the set of names that flatten saw.
    # Reusing a model is only sound while the tree holds the same names: a model can carry a
    # reference to another resource, and whether a string counts as such a reference depends on
    # which names exist. Same names, same answer. A name appearing or disappearing throws the lot
    # away and pays for one full flatten, which is the case that was going to be expensive anyway.
    self._known_models: Dict[str, Dict[str, Any]] = {}
    self._known_names: frozenset = frozenset()
    # The scene as last built. A client arriving is not a change to the scene, so it is handed this
    # rather than causing a fresh one: rebuilding would renumber everything and hand every client
    # already watching an epoch their indices no longer match.
    self._scene_payload: Optional[Dict[str, Any]] = None
    self._scene_dirty = False
    # Who has moved since that scene was built. The scene places every resource where it stood at
    # build time and is handed out unchanged afterwards, so for anything that has moved since, the
    # placement a new client is given is out of date - and a part that moved once, before that
    # client arrived, would never be corrected by a later delta because there is no later delta.
    self._moved: set = set()
    self._scene_timer: Optional[asyncio.TimerHandle] = None
    self.rebuilds = 0  # how many scene rebuilds a run actually cost
    self.clients_seen: List[Dict[str, Optional[str]]] = []  # what each page said it draws with

    self._subscribe(root)
    # A newly assigned resource has to start publishing too, or its state never reaches the viewer.
    root.register_did_assign_resource_callback(self._on_assign)
    root.register_did_unassign_resource_callback(lambda _r: self._resend())

  # -- wiring ----------------------------------------------------------------

  def _on_assign(self, resource: Resource) -> None:
    self._subscribe(resource)
    self._resend()

  def _subscribe(self, resource: Resource) -> None:
    def on_update(_state: dict, r: Resource = resource) -> None:
      self._on_state_update(r)

    resource.register_state_update_callback(on_update)
    for child in resource.children:
      self._subscribe(child)

  def _on_state_update(self, resource: Resource) -> None:
    """Batch state updates so a 96-channel operation is one message, not ninety-six."""
    if self._loop is None:
      return
    self._loop.call_soon_threadsafe(self._enqueue, resource.name, resource.serialize_state())

  def _enqueue(self, name: str, state: dict) -> None:
    self._pending[name] = state
    if "location" in state:
      self._moved.add(name)
    if self._loop is not None and not self._flush_scheduled:
      self._flush_scheduled = True
      self._loop.call_soon(lambda: asyncio.ensure_future(self._flush()))

  async def _flush(self) -> None:
    payload, self._pending = self._pending, {}
    self._flush_scheduled = False
    if payload:
      message = self._state_message(payload)
      # Everything in the batch may have been a change nobody could see.
      if message["of"]:
        await self._broadcast("state", message)

  # A structural change costs a whole scene, so a burst of them must not cost a scene each. Picking
  # up ninety-six tips is one operation to a user and a hundred and ninety-two callbacks here; they
  # coalesce into a single rebuild on a short timer. The proper answer is to send the moved
  # instances rather than the scene, since a move is a parent index and six floats, but coalescing
  # is what stops the current shape being quadratic in a burst.
  SCENE_DEBOUNCE_S = 0.05

  def _resend(self) -> None:
    """The tree changed shape, so the flattening is stale. Schedule one rebuild for the burst."""
    if self._loop is None:
      return
    self._loop.call_soon_threadsafe(self._mark_scene_dirty)

  def _mark_scene_dirty(self) -> None:
    self._scene_dirty = True
    if self._scene_timer is not None:
      self._scene_timer.cancel()
    if self._loop is None:
      return
    self._scene_timer = self._loop.call_later(
      self.SCENE_DEBOUNCE_S, lambda: asyncio.ensure_future(self._flush_scene())
    )

  async def _flush_scene(self) -> None:
    self._scene_timer = None
    if not self._scene_dirty:
      return
    self._scene_dirty = False
    self.rebuilds += 1
    await self._send_scene_to_all()

  # -- access ----------------------------------------------------------------

  def _host_allowed(self, hostname: Optional[str]) -> bool:
    if hostname is None:
      return False
    return _is_ip_literal(hostname) or hostname in self.allowed_hosts

  @property
  def ws_url(self) -> str:
    """Where a client outside a browser connects, token included."""
    return f"ws://127.0.0.1:{self.ws_port}/?token={self.token}"

  def _check_websocket(self, connection, request):
    """Refuse a websocket handshake without this run's token or from a page we did not serve.

    A browser always sends `Origin` on a websocket; a client that is not a browser may leave it out,
    and still needs the token.
    """
    offered = parse_qs(urlsplit(request.path).query).get("token", [""])[0]
    if not hmac.compare_digest(offered.encode(), self.token.encode()):
      logger.warning("refused a websocket without this viewer's token")
      return connection.respond(403, "Forbidden\n")
    origin = request.headers.get("Origin")
    if origin is not None and not self._host_allowed(_hostname_of(origin)):
      logger.warning("refused a websocket from origin %r", origin[:200])
      return connection.respond(403, "Forbidden\n")
    return None

  # -- transport -------------------------------------------------------------

  async def _broadcast(self, event: str, data: Any) -> None:
    if not self._clients:
      return
    message = json.dumps(_finite({"event": event, "data": data}))
    for client in list(self._clients):
      try:
        await client.send(message)
      except Exception:
        self._clients.discard(client)

  def _scene_message(self, rebuild: bool = False) -> Dict[str, Any]:
    """The scene and its measurements."""
    if self._scene_payload is not None and not rebuild:
      return self._scene_payload

    # The comparison against the old payload shape is measured on the first build only: it costs
    # a second full serialization of the tree and never changes for a given scene.
    scene = build_scene(
      self.root,
      measure_legacy=self._legacy_bytes is None,
      known=self._known_models,
      known_names=self._known_names,
    )
    self._known_names = frozenset(scene.names)
    self._known_models = scene.derived
    if self._legacy_bytes is None:
      self._legacy_bytes = scene.legacy_bytes
    scene.legacy_bytes = self._legacy_bytes

    payload = scene.serialize()
    self._register_meshes(payload["models"])

    # A new scene renumbers everything, so the indices change and the client knows nothing about
    # what any resource looks like. Both have to be reset together with the epoch that names them.
    self._epoch += 1
    self._index_of = {name: i for i, name in enumerate(payload["instances"]["names"])}
    self._published = {}
    # Placed afresh, so nothing has moved since.
    self._moved = set()
    self._stats = scene.stats(scene_bytes=len(json.dumps(payload)))
    self._scene_payload = {
      **payload,
      "epoch": self._epoch,
      "stats": self._stats,
    }
    return self._scene_payload

  def _state_message(self, states: Dict[str, Dict[str, Any]], full: bool = False) -> Dict[str, Any]:
    """Pack state for the wire.

    A broadcast goes to clients that have been following along, so anything that still looks the way
    it last did is left out. A snapshot goes to a client that knows nothing yet and must carry
    everything, whatever the others have already been told - suppression is about what a given
    client has seen, and a new one has seen none of it.

    A snapshot leaves out `location`, alone among the fields. The scene sent immediately before it
    already places every resource, so repeating that here says nothing new - and because a position
    is unique to one resource, carrying it would give every resource a state of its own and undo
    the sharing the rest of this message depends on. A position still travels the moment it
    changes; it just is not announced twice at the start.
    """
    fresh = {}
    for name, published in states.items():
      _, signature = state_signature(published)
      if not full and self._published.get(name) == signature:
        continue
      self._published[name] = signature
      if not full:
        fresh[name] = published
        continue
      # In a snapshot, a resource with nothing left to say is left out entirely: absent already
      # means default to a client that has just arrived. In a delta it is kept, because there it
      # means "no longer what I last told you".
      #
      # A resource that has moved since the scene was built keeps its position, whatever the
      # paragraph above says: for that one the scene is what is out of date, and this is the only
      # message that will ever correct it.
      trimmed = (
        published
        if name in self._moved
        else {k: v for k, v in published.items() if k != "location"}
      )
      cleaned, _ = state_signature(trimmed)
      if cleaned:
        fresh[name] = trimmed
    return pack_state(fresh, self._epoch)

  # What `reference_glb` promises: the file is in the resource's own frame, metres, Z up. Stated
  # once here rather than per resource, which is the point of having a convention.
  REFERENCE_GLB_UNITS = "m"
  REFERENCE_GLB_UP = "Z"

  def _register_meshes(self, models: List[Dict[str, Any]]) -> None:
    """Turn each declared model into a URL the page can fetch, and remember what to serve.

    A resource gets its geometry one of three ways, in this order. `mesh` is the long form,
    carrying its own absolute path, units, up axis and joint map, for a rigged model or one that
    does not fit the convention. `reference_glb` is a path relative to `models_root`, for a file
    that lives outside the package. And a resource that says neither is looked up by the model it
    is: a file named after it, shipped anywhere under the package, is drawn without anyone having
    to declare or pass anything. A resource with no model name, or one no file is named after,
    keeps its box.

    All three end up in the same place, because the page only knows one way to draw a model. The
    page cannot read a filesystem path and the file is often far too large to inline, so each is
    given a stable id and served from this viewer; the path itself never reaches the browser.
    """
    on_disk = _models_on_disk(PACKAGE_ROOT)
    for model in models:
      reference = model.pop("reference_glb", None)
      if reference is None and "mesh" not in model:
        found = on_disk.get(str(model.get("model") or ""))
        if found is not None:
          model["mesh"] = {
            "path": found,
            "units": self.REFERENCE_GLB_UNITS,
            "up": self.REFERENCE_GLB_UP,
          }
      if reference is not None and "mesh" not in model:
        if self.models_root is None:
          logger.warning(
            "%s declares reference_glb=%r but the viewer was given no models_root, "
            "so it is drawn as a box",
            model.get("model") or model.get("type"),
            reference,
          )
        else:
          model["mesh"] = {
            "path": os.path.join(self.models_root, reference),
            "units": self.REFERENCE_GLB_UNITS,
            "up": self.REFERENCE_GLB_UP,
          }

    for model in models:
      mesh = model.get("mesh")
      if not isinstance(mesh, dict) or "path" not in mesh:
        continue
      path = os.path.abspath(os.path.expanduser(str(mesh["path"])))
      if not os.path.isfile(path):
        logger.warning("declared mesh not found, drawing the box instead: %s", path)
        model.pop("mesh")
        continue
      mesh_id = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16] + os.path.splitext(path)[1]
      self._mesh_files[mesh_id] = path
      model["mesh"] = {k: v for k, v in mesh.items() if k != "path"}
      model["mesh"]["url"] = f"mesh/{mesh_id}"

  async def _send_scene_to_all(self) -> None:
    await self._broadcast("scene", self._scene_message(rebuild=True))
    await self._broadcast("state", self._state_message(collect_state(self.root), full=True))

  async def _handler(self, websocket) -> None:
    self._clients.add(websocket)
    await websocket.send(json.dumps(_finite({"event": "scene", "data": self._scene_message()})))
    await websocket.send(
      json.dumps(
        _finite(
          {"event": "state", "data": self._state_message(collect_state(self.root), full=True)}
        )
      )
    )
    try:
      async for message in websocket:
        self._on_client_message(message)
    except Exception:
      pass
    finally:
      self._clients.discard(websocket)

  def _on_client_message(self, message: Any) -> None:
    """A page says what it draws with, or why it could not draw at all.

    Printed rather than logged because the person to tell is the one watching the notebook, who
    otherwise only sees a browser tab that shows nothing. Everything in it came from a browser, so
    it is shortened and stripped to printable text before it is shown.
    """
    try:
      parsed = json.loads(message)
    except (TypeError, ValueError):
      return
    if not isinstance(parsed, dict) or parsed.get("event") != "hello":
      return
    data = parsed.get("data")
    if not isinstance(data, dict):
      return
    self.clients_seen.append({k: _printable(data.get(k)) for k in HELLO_FIELDS})
    hello = self.clients_seen[-1]
    if hello["error"]:
      print(f"viewer: a browser could not show the scene: {hello['error']}")
      print(f"  renderer: {hello['renderer'] or 'none'}  browser: {hello['userAgent']}")
      return
    drawing = hello["backend"] or "unknown backend"
    if hello["renderer"]:
      drawing += f" on {hello['renderer']}"
    if data.get("software") is True:
      drawing += " - software rendering, expect it to be slow"
    print(f"viewer: a browser connected, drawing with {drawing}")

  # -- static files ----------------------------------------------------------

  def _start_file_server(self) -> None:
    directory = STATIC_DIR
    ws_port = self.ws_port
    name = self.name
    token = self.token
    mesh_files = self._mesh_files
    host_allowed = self._host_allowed

    class Handler(http.server.SimpleHTTPRequestHandler):
      def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

      def log_message(self, fmt, *args):
        pass

      def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

      def do_HEAD(self):
        if self._refuse_foreign_host():
          return
        super().do_HEAD()

      def _refuse_foreign_host(self) -> bool:
        if host_allowed(_hostname_of(self.headers.get("Host"))):
          return False
        self.send_error(403, "unrecognised Host; pass it to Viewer3D(allowed_hosts=...)")
        return True

      def do_GET(self):
        if self._refuse_foreign_host():
          return
        # Match on the path alone: a link may carry a query string (`/?view=top`), and serving the
        # template unsubstituted in that case leaves the page with no websocket port.
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/mesh/"):
          source = mesh_files.get(path[len("/mesh/") :])
          if source is None:
            self.send_error(404)
            return
          with open(source, "rb") as f:
            body = f.read()
          self.send_response(200)
          self.send_header("Content-type", "model/gltf-binary")
          self.send_header("Content-Length", str(len(body)))
          self.end_headers()
          self.wfile.write(body)
          return
        if path in ("/", "/index.html"):
          with open(os.path.join(directory, "index.html"), "r", encoding="utf-8") as f:
            content = f.read().replace("{{ ws_port }}", str(ws_port))
            content = content.replace("{{ ws_token }}", token)
            content = content.replace("{{ source_filename }}", name)
          body = content.encode("utf-8")
          self.send_response(200)
          self.send_header("Content-type", "text/html; charset=utf-8")
          self.send_header("Content-Length", str(len(body)))
          self.end_headers()
          self.wfile.write(body)
          return
        return super().do_GET()

    while True:
      try:
        self._httpd = http.server.HTTPServer((self.host, self.fs_port), Handler)
        break
      except OSError:
        self.fs_port += 1

    thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="viz3d_fs")
    thread.start()

  # -- lifecycle -------------------------------------------------------------

  async def start(self) -> None:
    self._loop = asyncio.get_running_loop()

    # The websocket first, because the page has to be told which port to call back on and the file
    # server bakes that number into what it serves. Started the other way round, a viewer whose
    # preferred port is already taken serves a page pointing at the viewer that took it, and then
    # quietly shows someone else's facility.
    while True:
      try:
        self._ws_server = await websockets.serve(
          self._handler, self.host, self.ws_port, process_request=self._check_websocket
        )
        break
      except OSError:
        self.ws_port += 1

    self._start_file_server()

    url = f"http://{self.host}:{self.fs_port}"
    print(f"viewer on {url}  (websocket {self.ws_port})")
    if self.open_browser:
      webbrowser.open(url)

  async def stop(self) -> None:
    if self._httpd is not None:
      self._httpd.shutdown()
      self._httpd.server_close()
      self._httpd = None
