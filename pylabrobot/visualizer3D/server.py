"""Serve a flattened resource tree to the browser and keep it current.

Two servers: static files over HTTP, and a websocket carrying the scene, its state and moves.
"""

import asyncio
import errno
import functools
import hmac
import html
import http.server
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import socket
import sys
import threading
import webbrowser
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.http11 import Request, Response

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from .scene import (
  STATE_DECIMALS,
  Scene,
  all_names,
  build_scene,
  collect_state,
  legacy_size,
  pack_state,
  state_signature,
)

logger = logging.getLogger(__name__)


def _finite(obj: Any) -> Any:
  """Replace non-finite floats with the strings "Infinity", "-Infinity" and "NaN".

  `json.dumps` writes them bare, which `JSON.parse` refuses; a trough's max volume is infinite.
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


def _encode(event: str, data: Any) -> str:
  """One message as the page reads it: the same encoding for a greeting and a broadcast."""
  return json.dumps(_finite({"event": event, "data": data}))


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

# Where a resource's geometry is looked for when it declares none: a file named after its model,
# anywhere under the package. Model names are namespaced by manufacturer and device.
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_SUFFIX = ".glb"
# What a filtered tip's model name ends in, after the name of the tip it is filtered from.
FILTER_SUFFIX = "_filter"


@functools.lru_cache(maxsize=None)
def _models_on_disk(root: str) -> Dict[str, str]:
  """Every `.glb` under `root` by the model name it is named after, cached per root.

  A name found under two paths is ambiguous: neither is offered, and the clash is logged.
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


# How far a taken port is walked up before the viewer gives up on binding.
PORT_TRIES = 20
# What the page and this server agree on: the scene carries it, and a page that expects another
# number says so rather than drawing what it misreads.
PROTOCOL = 1


def _port_taken(error: OSError) -> bool:
  """Whether a bind failed because the port is in use: the one failure the next port can cure."""
  return error.errno == errno.EADDRINUSE


def _xyz(location: Dict[str, Any]) -> List[float]:
  return [float(location.get(axis, 0.0)) for axis in ("x", "y", "z")]


def _signature(cleaned: Dict[str, Any], key: str) -> str:
  """What a client was told, as a key: the shared part's, plus the position as it can be seen."""
  if "location" not in cleaned:
    return key
  return key + repr([round(v, STATE_DECIMALS) for v in _xyz(cleaned["location"])])


class Viewer3D:
  """A visualizer that takes any resource as its world.

  Args:
    root: the resource that is the world; every descendant is placed in its cartesian space.
    host: interface to bind both servers to.
    fs_port: static file server port.
    ws_port: websocket port.
    open_browser: whether to open a browser window on start.
    name: what to show in the header.
    models_root: directory every `Resource.reference_glb` is relative to; a model shipped under
      the package is found without it.
    allowed_hosts: extra hostnames a browser may reach the viewer by. IP addresses, `localhost`,
      this machine's hostname and `<hostname>.local` are always accepted.

  Access: each run makes a token, hands it out only in the link it prints and opens (`url`, as
  its `#token=` fragment, which no request carries), and refuses a websocket without it. Both
  servers refuse an unrecognised hostname, in the HTTP `Host` header and in the websocket
  `Origin`; an IP literal is always accepted, since it cannot be rebound.
  """

  root: Resource
  host: str
  fs_port: int
  ws_port: int
  open_browser: bool
  name: str
  token: str
  allowed_hosts: Set[str]
  models_root: Optional[str]
  rebuilds: int
  clients_seen: List[Dict[str, Optional[str]]]
  _clients: Set[ServerConnection]
  _httpd: Optional[http.server.HTTPServer]
  _ws_server: Optional[Server]
  _pending: Dict[str, Tuple[Dict[str, Any], str]]
  _flush_scheduled: bool
  _loop: Optional[asyncio.AbstractEventLoop]
  _legacy_bytes: int
  _mesh_files: Dict[str, str]
  _mesh_ids: Dict[str, str]
  _epoch: int
  _index_of: Dict[str, int]
  _published: Dict[str, str]
  _known_models: Dict[str, Dict[str, Any]]
  _known_names: FrozenSet[str]
  _scene_payload: Optional[Dict[str, Any]]
  _scene: Optional[Scene]
  _refused_a_token: bool
  _browser_drawing: Optional[asyncio.Event]
  _moved: Set[str]
  _scene_timer: Optional[asyncio.TimerHandle]
  _subscribed: Dict[int, Tuple[Resource, Callable[[Dict[str, Any]], None]]]

  # -- packing -----------------------------------------------------------------

  # What `reference_glb` promises: the file is in the resource's own frame, metres, Z up. Stated
  # once here rather than per resource, which is the point of having a convention.
  REFERENCE_GLB_UNITS = "m"
  REFERENCE_GLB_UP = "Z"

  def _register_meshes(self, models: List[Dict[str, Any]]) -> None:
    """Give each model with geometry a `mesh` holding a URL the page can fetch; remember the file.

    Three sources, in this order: a declared `mesh` (path, units, up axis, joints), a
    `reference_glb` relative to `models_root`, or a `<model>.glb` under the package (a `tip` whose
    name ends in `FILTER_SUFFIX` falls back to its unfiltered twin's file). The path never reaches
    the page: the file is served under a random id, the same for the whole run, and only a `.glb`.
    """
    on_disk = _models_on_disk(PACKAGE_ROOT)
    # Copies, never the interned dicts: a model the scene derived is kept to be reused on the next
    # build and compared to what it was, and one given a mesh here would no longer match its twins.
    for i, interned in enumerate(models):
      model = dict(interned)
      models[i] = model
      reference = model.pop("reference_glb", None)
      if reference is None and "mesh" not in model:
        name = str(model.get("model") or "")
        found = on_disk.get(name)
        # A filtered tip is the same moulded body with a filter pressed into it, so it is drawn
        # with its unfiltered twin's file, and the page adds the filter. Its own name stays its own.
        if found is None and model.get("category") == "tip" and name.endswith(FILTER_SUFFIX):
          found = on_disk.get(name[: -len(FILTER_SUFFIX)])
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
          path = os.path.realpath(os.path.join(self.models_root, str(reference)))
          if not path.startswith(os.path.join(self.models_root, "")):
            logger.warning(
              "reference_glb=%r leads out of models_root, so it is drawn as a box", reference
            )
          else:
            model["mesh"] = {
              "path": path,
              "units": self.REFERENCE_GLB_UNITS,
              "up": self.REFERENCE_GLB_UP,
            }

    for model in models:
      mesh = model.get("mesh")
      if not isinstance(mesh, dict) or "path" not in mesh:
        continue
      path = os.path.realpath(os.path.expanduser(str(mesh["path"])))
      if not path.lower().endswith(MODEL_SUFFIX) or not os.path.isfile(path):
        logger.warning(
          "declared mesh is no %s file, drawing the box instead: %s", MODEL_SUFFIX, path
        )
        model.pop("mesh")
        continue
      # Random, not derived from the path: the file server serves a mesh without the token.
      mesh_id = self._mesh_ids.setdefault(path, secrets.token_hex(8) + MODEL_SUFFIX)
      self._mesh_files[mesh_id] = path
      model["mesh"] = {k: v for k, v in mesh.items() if k != "path"}
      model["mesh"]["url"] = f"mesh/{mesh_id}"

  def _delta_message(self, states: Dict[str, Tuple[Dict[str, Any], str]]) -> Dict[str, Any]:
    """Pack what differs from what the clients were last told, and record it as told.

    A state that cleaned down to nothing is kept: it means "no longer what I last said".
    """
    fresh = {}
    for name, (cleaned, key) in states.items():
      signature = _signature(cleaned, key)
      if self._published.get(name) != signature:
        self._published[name] = signature
        fresh[name] = (cleaned, key)
    return pack_state(fresh, self._epoch)

  def _snapshot(self) -> Dict[str, Tuple[Dict[str, Any], str]]:
    """Every state a new client must be told: each with something to say, without `location`.

    The scene sent before it places everything. A resource in `_moved` keeps its `location`, as
    this is the only message that will correct it.
    """
    states = {}
    for name, (cleaned, key) in collect_state(self.root).items():
      if name not in self._moved:
        cleaned.pop("location", None)
      if cleaned:
        states[name] = (cleaned, key)
    return states

  # -- access ------------------------------------------------------------------

  def _host_allowed(self, hostname: Optional[str]) -> bool:
    if hostname is None:
      return False
    return _is_ip_literal(hostname) or hostname in self.allowed_hosts

  @property
  def url(self) -> str:
    """The page with this run's token: the one link that can watch."""
    # A wildcard bind is no address to browse to, and an IPv6 literal needs its brackets.
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(self.host, self.host)
    host = f"[{host}]" if ":" in host else host
    return f"http://{host}:{self.fs_port}/#token={self.token}"

  @property
  def ws_url(self) -> str:
    """Where a client outside a browser connects, token included."""
    return f"ws://127.0.0.1:{self.ws_port}/?token={self.token}"

  def _check_websocket(self, connection: ServerConnection, request: Request) -> Optional[Response]:
    """Refuse a handshake without this run's token, or with an `Origin` the viewer does not serve.

    A client that is not a browser may omit `Origin`, and still needs the token.
    """
    offered = parse_qs(urlsplit(request.path).query).get("token", [""])[0]
    if not hmac.compare_digest(offered.encode(), self.token.encode()):
      if not self._refused_a_token:
        logger.warning(
          "refused a websocket without this viewer's token: a page left open from an earlier "
          "viewer, or one opened without the link it printed. Repeats are logged at debug."
        )
        self._refused_a_token = True
      else:
        logger.debug("refused a websocket without this viewer's token")
      return connection.respond(403, "Forbidden\n")
    origin = request.headers.get("Origin")
    if origin is not None and not self._host_allowed(_hostname_of(origin)):
      logger.warning("refused a websocket from origin %r", origin[:200])
      return connection.respond(403, "Forbidden\n")
    return None

  # -- transport ---------------------------------------------------------------

  async def _broadcast(self, event: str, data: Any) -> None:
    if not self._clients:
      return
    message = _encode(event, data)
    for client in list(self._clients):
      try:
        await client.send(message)
      except Exception:
        self._clients.discard(client)

  def _live_loop(self) -> Optional[asyncio.AbstractEventLoop]:
    """The loop to hand a change to, or None after `stop` or once that loop has closed."""
    loop = self._loop
    return loop if loop is not None and not loop.is_closed() else None

  # -- state channel -----------------------------------------------------------

  def _placed_as_kept(self, name: str, location: Dict[str, Any]) -> bool:
    """Whether `location` is where the kept scene already places `name`: published, but no move."""
    index = self._index_of.get(name)
    if self._scene is None or index is None:
      return False
    return _xyz(location) == self._scene.transforms[6 * index : 6 * index + 3]

  async def _flush(self) -> None:
    payload, self._pending = self._pending, {}
    self._flush_scheduled = False
    if self._scene_timer is not None:
      # A rebuild is on its way: a location is relative to a parent the client may not have yet,
      # and the rebuild places everything. What is not a location still goes now.
      payload = {
        name: ({k: v for k, v in cleaned.items() if k != "location"}, key)
        for name, (cleaned, key) in payload.items()
      }
    if payload:
      message = self._delta_message(payload)
      # Everything in the batch may have been a change nobody could see.
      if message["of"]:
        await self._broadcast("state", message)

  def _enqueue(self, name: str, state: Dict[str, Any]) -> None:
    if "location" in state and self._placed_as_kept(name, state["location"]):
      state = {k: v for k, v in state.items() if k != "location"}
    self._pending[name] = state_signature(state)
    if "location" in state:
      self._moved.add(name)
    if self._loop is not None and not self._flush_scheduled:
      self._flush_scheduled = True
      asyncio.ensure_future(self._flush())

  # -- scene channel -----------------------------------------------------------

  def _scene_message(self, rebuild: bool = False) -> Dict[str, Any]:
    """The scene and its measurements."""
    if self._scene_payload is not None and not rebuild:
      return self._scene_payload

    scene = build_scene(self.root, known=self._known_models, known_names=self._known_names)
    self._known_names = frozenset(scene.names)
    self._known_models = scene.derived
    scene.legacy_bytes = self._legacy_bytes

    payload = scene.serialize()
    self._scene = scene
    self._register_meshes(payload["models"])

    # A new scene renumbers everything, so the indices change with the epoch that names them.
    self._epoch += 1
    self._index_of = {name: i for i, name in enumerate(payload["instances"]["names"])}
    # Placed afresh, so nothing has moved since.
    self._moved = set()
    self._scene_payload = {
      **payload,
      "epoch": self._epoch,
      "stats": scene.stats(scene_bytes=len(json.dumps(payload))),
      "protocol": PROTOCOL,
    }
    return self._scene_payload

  def _moves(self) -> Optional[List[Dict[str, Any]]]:
    """Moves since the scene was built, applied to the kept scene too, or None if a name changed.

    A move is a resource whose parent or local transform differs from the kept scene, as `{name,
    parent, location, rotation}`. A name appearing or disappearing needs a rebuild.
    """
    scene = self._scene
    if scene is None or frozenset(all_names(self.root)) != self._known_names:
      return None
    moves: List[Dict[str, Any]] = []

    def walk(resource: Resource, parent: Optional[str]) -> None:
      index = self._index_of[resource.name]
      location = resource.location or Coordinate.zero()
      rotation = resource.rotation
      local = [
        float(location.x),
        float(location.y),
        float(location.z),
        float(rotation.x),
        float(rotation.y),
        float(rotation.z),
      ]
      parent_index = -1 if parent is None else self._index_of[parent]
      if (
        parent_index != scene.parent_of_instance[index]
        or local != scene.transforms[6 * index : 6 * index + 6]
      ):
        scene.parent_of_instance[index] = parent_index
        scene.transforms[6 * index : 6 * index + 6] = local
        moves.append(
          {
            "name": resource.name,
            "parent": parent,
            "location": {"x": local[0], "y": local[1], "z": local[2]},
            "rotation": {"x": local[3], "y": local[4], "z": local[5]},
          }
        )
      for child in resource.children:
        walk(child, resource.name)

    walk(self.root, None)
    if moves and self._scene_payload is not None:
      self._scene_payload = {**self._scene_payload, **scene.serialize()}
    # Every place the kept scene knows is current again, so a snapshot need not carry any of them.
    self._moved = set()
    return moves

  async def _send_scene_to_all(self) -> None:
    await self._broadcast("scene", self._scene_message(rebuild=True))
    # A new scene is where every client starts over: what it is told now is what it was last told.
    states = self._snapshot()
    self._published = {name: _signature(cleaned, key) for name, (cleaned, key) in states.items()}
    await self._broadcast("state", pack_state(states, self._epoch))

  async def _flush_scene(self) -> None:
    self._scene_timer = None
    if not self._clients:
      # Nobody to tell. The kept scene is dropped, so the next client is greeted with one built
      # for it then, rather than this one being built now for no one.
      self._scene = None
      self._scene_payload = None
      return
    moves = self._moves()
    if moves is None:
      self.rebuilds += 1
      await self._send_scene_to_all()
    elif moves:
      await self._broadcast("moves", {"epoch": self._epoch, "moves": moves})

  # A burst of structural changes coalesces into one rebuild or one `moves`: picking up ninety-six
  # tips is one operation to a user and a hundred and ninety-two callbacks here.
  SCENE_DEBOUNCE_S = 0.05

  def _mark_scene_dirty(self) -> None:
    # A pending timer is what says the scene is stale; a burst keeps pushing it back.
    if self._scene_timer is not None:
      self._scene_timer.cancel()
    if self._loop is None:
      return
    self._scene_timer = self._loop.call_later(
      self.SCENE_DEBOUNCE_S, lambda: asyncio.ensure_future(self._flush_scene())
    )

  def _resend(self) -> None:
    """The tree changed shape: schedule one rebuild for the burst."""
    loop = self._live_loop()
    if loop is None:
      return
    loop.call_soon_threadsafe(self._mark_scene_dirty)

  # -- subscriptions -----------------------------------------------------------

  def _subscribe(self, resource: Resource) -> None:
    # A resource put back after being taken out is still listened to: one callback, not one more.
    if id(resource) in self._subscribed:
      return

    def on_update(state: Dict[str, Any], r: Resource = resource) -> None:
      # Batched on the loop, so a 96-channel operation is one message, not ninety-six.
      loop = self._live_loop()
      if loop is not None:
        loop.call_soon_threadsafe(self._enqueue, r.name, state)

    resource.register_state_update_callback(on_update)
    self._subscribed[id(resource)] = (resource, on_update)
    for child in resource.children:
      self._subscribe(child)

  def _unsubscribe(self, resource: Resource) -> None:
    """Stop listening to `resource` and everything under it: out of the tree, it has no viewer."""
    subscribed = self._subscribed.pop(id(resource), None)
    if subscribed is not None:
      subscribed[0].deregister_state_update_callback(subscribed[1])
    for child in resource.children:
      self._unsubscribe(child)

  def _on_assign(self, resource: Resource) -> None:
    self._subscribe(resource)
    self._resend()

  def _on_unassign(self, resource: Resource) -> None:
    self._unsubscribe(resource)
    self._resend()

  # -- clients -----------------------------------------------------------------

  def _on_client_message(self, message: Any) -> bool:
    """Print what a page draws with, or why it could not draw at all; whether it was a hello.

    Printed, not logged: the person to tell is the one watching the notebook. Every value came from
    a browser, so it is shortened and stripped to printable text first.
    """
    try:
      parsed = json.loads(message)
    except (TypeError, ValueError):
      return False
    if not isinstance(parsed, dict) or parsed.get("event") != "hello":
      return False
    data = parsed.get("data")
    if not isinstance(data, dict):
      return False
    self.clients_seen.append({k: _printable(data.get(k)) for k in HELLO_FIELDS})
    hello = self.clients_seen[-1]
    if hello["error"]:
      print(f"viewer: a browser could not show the scene: {hello['error']}")
      print(f"  renderer: {hello['renderer'] or 'none'}  browser: {hello['userAgent']}")
      return True
    drawing = hello["backend"] or "unknown backend"
    if hello["renderer"]:
      drawing += f" on {hello['renderer']}"
    if data.get("software") is True:
      drawing += " - software rendering, expect it to be slow"
    print(f"viewer: a browser connected, drawing with {drawing}")
    if self._browser_drawing is not None:
      self._browser_drawing.set()
    return True

  async def _handler(self, websocket: ServerConnection) -> None:
    self._clients.add(websocket)
    await websocket.send(_encode("scene", self._scene_message()))
    await websocket.send(_encode("state", pack_state(self._snapshot(), self._epoch)))
    greeted = False
    try:
      async for message in websocket:
        # A page says hello once. A repeat is still read, or keepalive stalls, but not kept.
        if not greeted:
          greeted = self._on_client_message(message)
    except Exception:
      pass
    finally:
      self._clients.discard(websocket)

  # -- static files ------------------------------------------------------------

  # How often the serving thread looks up from its socket, which is how long `stop` waits for it.
  FS_POLL_S = 0.05
  # How long a file server connection may sit idle or take over one request before it is closed:
  # each holds a thread, and keep-alive would hold it for as long as the other end likes.
  FS_TIMEOUT_S = 30.0

  def _start_file_server(self) -> None:
    directory = STATIC_DIR
    ws_port = self.ws_port
    name = self.name
    mesh_files = self._mesh_files
    host_allowed = self._host_allowed
    idle_timeout = self.FS_TIMEOUT_S

    class Handler(http.server.SimpleHTTPRequestHandler):
      # Keep-alive, so a page fetching two dozen meshes at once reuses a few connections.
      protocol_version = "HTTP/1.1"
      timeout = idle_timeout

      def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=directory, **kwargs)

      def log_message(self, fmt: str, *args: Any) -> None:
        pass

      def end_headers(self) -> None:
        # The page carries this run's websocket port and a mesh this run's id: never kept. The rest
        # is revalidated and answered 304 when unchanged, rather than fetched again on every load.
        path = self.path.split("?", 1)[0]
        fresh = path in ("/", "/index.html") or path.startswith("/mesh/")
        self.send_header("Cache-Control", "no-store" if fresh else "no-cache")
        super().end_headers()

      def do_HEAD(self) -> None:
        if self._refuse_foreign_host():
          return
        super().do_HEAD()

      def _refuse_foreign_host(self) -> bool:
        if host_allowed(_hostname_of(self.headers.get("Host"))):
          return False
        self.send_error(403, "unrecognised Host; pass it to Viewer3D(allowed_hosts=...)")
        return True

      def do_GET(self) -> None:
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
            content = content.replace("{{ source_filename }}", html.escape(name))
          body = content.encode("utf-8")
          self.send_response(200)
          self.send_header("Content-type", "text/html; charset=utf-8")
          self.send_header("Content-Length", str(len(body)))
          self.end_headers()
          self.wfile.write(body)
          return
        return super().do_GET()

    class Server(http.server.ThreadingHTTPServer):
      address_family = socket.AF_INET6 if ":" in self.host else socket.AF_INET

      def handle_error(self, request: Any, client_address: Any) -> None:
        # A browser that leaves a page mid-download, or stalls past the timeout, is no error of
        # ours, and a traceback on every reload buries anything that is.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, socket.timeout)):
          return
        super().handle_error(request, client_address)

    # Threaded: a page loads its meshes in parallel, and a server answering one request at a time
    # was seen closing one of twenty-two without a response, which drew that resource as a box.
    for attempt in range(PORT_TRIES):
      try:
        httpd = Server((self.host, self.fs_port), Handler)
        break
      except OSError as error:
        if not _port_taken(error) or attempt == PORT_TRIES - 1:
          raise
        self.fs_port += 1
    self._httpd = httpd

    serve = functools.partial(httpd.serve_forever, poll_interval=self.FS_POLL_S)
    thread = threading.Thread(target=serve, daemon=True, name="viz3d_fs")
    thread.start()

  # -- lifecycle ---------------------------------------------------------------

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
    self.models_root = os.path.realpath(os.path.expanduser(models_root)) if models_root else None

    self._clients = set()
    self._httpd = None
    self._ws_server = None
    self._pending = {}
    self._flush_scheduled = False
    self._loop = None
    self._legacy_bytes = 0  # measured once, at start, off the loop
    # Files a resource declared as its own geometry, by the id the page fetches them under. Only a
    # path that a resource named is ever served, so this doubles as the whitelist.
    self._mesh_files = {}
    # The id each served file got, so a rebuild names a file as the last scene did.
    self._mesh_ids = {}
    # Which scene was last built, and where in it each name stands: what a move or a published
    # position is compared against. State itself is addressed by name.
    self._epoch = 0
    self._index_of = {}
    # What each resource last looked like on the wire. A resource that publishes a change too small
    # to see produces the same signature and is not sent again.
    self._published = {}
    # Models derived by the last flatten, by resource name, and the names that flatten saw: the
    # next one reuses every model that no appeared or vanished name could have changed.
    self._known_models = {}
    self._known_names = frozenset()
    # The scene as last built: a client arriving is handed this rather than causing a rebuild,
    # which would renumber everything under the clients already watching.
    self._scene_payload = None
    # The scene behind that payload, kept so a resource put somewhere else can be moved within it
    # rather than the whole thing being built again: a tip picked up is the same tip under a shaft.
    self._scene = None
    # A page left open from an earlier viewer retries its old token every second or so: said once.
    self._refused_a_token = False
    # Who has moved since that scene was built: the kept scene places them where they stood then,
    # and a new client's snapshot is the only message that will correct that.
    self._moved = set()
    self._scene_timer = None
    self.rebuilds = 0  # how many scene rebuilds a run actually cost
    self.clients_seen = []  # what each page said it draws with
    self._browser_drawing = None  # made on the loop `start` runs on

    # Every resource this viewer listens to, with the callback it gave, so `stop` can take it back.
    # By identity: a tip compares by value and is not hashable.
    self._subscribed = {}
    self._subscribe(root)
    # A newly assigned resource has to start publishing too, or its state never reaches the viewer.
    root.register_did_assign_resource_callback(self._on_assign)
    root.register_did_unassign_resource_callback(self._on_unassign)

  async def start(self) -> None:
    self._loop = asyncio.get_running_loop()
    self._browser_drawing = asyncio.Event()
    # Off the loop, before a client can connect: the package walk for model files, and the size of
    # the tree as one node per resource, which the stats panel compares against.
    self._legacy_bytes, _ = await asyncio.gather(
      asyncio.to_thread(legacy_size, self.root), asyncio.to_thread(_models_on_disk, PACKAGE_ROOT)
    )

    # The websocket first: the file server bakes its port into the page. The other way round, a
    # viewer whose port is taken serves a page pointing at the viewer that took it.
    for attempt in range(PORT_TRIES):
      try:
        self._ws_server = await websockets.serve(
          self._handler, self.host, self.ws_port, process_request=self._check_websocket
        )
        break
      except OSError as error:
        if not _port_taken(error) or attempt == PORT_TRIES - 1:
          raise
        self.ws_port += 1

    self._start_file_server()

    print(f"viewer on {self.url}  (websocket {self.ws_port})")
    if self.open_browser:
      webbrowser.open(self.url)

  async def wait_for_browser(self, timeout: Optional[float] = None) -> None:
    """Wait until a browser has connected and says it is drawing the scene.

    A simulated run takes milliseconds: call this before it, so a page shows it from the start.

    Args:
      timeout: seconds to wait, or None to wait for as long as it takes.

    Raises:
      RuntimeError: the viewer has not been started.
      asyncio.TimeoutError: no browser was drawing within `timeout`.
    """
    if self._browser_drawing is None:
      raise RuntimeError("start the viewer before waiting for a browser")
    await asyncio.wait_for(self._browser_drawing.wait(), timeout)

  async def stop(self) -> None:
    """Close both servers and stop listening to the tree.

    The ports are free for the next viewer, and no change is handed to a loop that is gone.
    """
    self._unsubscribe(self.root)
    self.root.deregister_did_assign_resource_callback(self._on_assign)
    self.root.deregister_did_unassign_resource_callback(self._on_unassign)
    self._loop = None
    if self._scene_timer is not None:
      self._scene_timer.cancel()
      self._scene_timer = None
    if self._ws_server is not None:
      self._ws_server.close()
      await self._ws_server.wait_closed()
      self._ws_server = None
    self._clients.clear()
    if self._httpd is not None:
      # Shutdown waits for the serving thread's poll: not on the loop's time.
      await asyncio.to_thread(self._httpd.shutdown)
      self._httpd.server_close()
      self._httpd = None
