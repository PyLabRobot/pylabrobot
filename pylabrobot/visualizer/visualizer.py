import asyncio
import functools
import http.server
import inspect
import json
import logging
import math
import os
import re
import webbrowser
from typing import Any, Dict, List, Optional, Tuple, cast

import anyio
import sniffio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from typing_extensions import override

try:
  import websockets
  import websockets.asyncio.server
  import websockets.exceptions

  HAS_WEBSOCKETS = True
except ImportError as e:
  HAS_WEBSOCKETS = False
  _WEBSOCKETS_IMPORT_ERROR = e

from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION
from pylabrobot.concurrency import AsyncExitStackWithShielding, AsyncResource, global_manager
from pylabrobot.resources import Resource

logger = logging.getLogger("pylabrobot")


@functools.lru_cache(maxsize=None)
def _get_public_methods(cls: type) -> list:
  """Get public method signatures from a resource class for the visualizer UI."""
  methods = []
  for name in dir(cls):
    if name.startswith("_"):
      continue
    try:
      attr = getattr(cls, name, None)
    except Exception:
      continue
    if attr is None or not callable(attr) or isinstance(attr, property):
      continue
    try:
      sig = inspect.signature(attr)
      params = [p for p in sig.parameters if p != "self"]
      methods.append(f"{name}({', '.join(params)})")
    except (ValueError, TypeError):
      methods.append(f"{name}()")
  return sorted(methods)


def _serialize_resource_tree(resource: Resource) -> dict:
  """Serialize a resource and its children for the visualizer.

  Method signatures are not embedded per node; identical for every instance of a class,
  they are sent once per class via :func:`_build_method_registry` and attached by type in
  the browser. On a full deck this avoids repeating the same signature list on every well.
  """
  data = resource.serialize()
  data["children"] = [_serialize_resource_tree(child) for child in resource.children]
  return data


def _build_method_registry(resource: Resource, registry: Optional[dict] = None) -> dict:
  """Map each resource class name in the tree to its public method signatures.

  The serialized ``type`` of a resource is its class name, so the browser can look up a
  node's methods by ``type`` instead of receiving the same list on every node.
  """
  if registry is None:
    registry = {}
  type_name = type(resource).__name__
  if type_name not in registry:
    registry[type_name] = _get_public_methods(type(resource))  # type: ignore[arg-type]
  for child in resource.children:
    _build_method_registry(child, registry)
  return registry


def _sanitize_floats(obj):
  """Recursively replace non-finite floats (inf, -inf, nan) with string representations.

  Python's ``json.dumps`` outputs bare ``Infinity``/``-Infinity``/``NaN`` tokens which are not
  valid JSON and cause ``JSON.parse()`` in the browser to throw. Walking the structure before
  serialization is more robust than post-hoc string replacement.
  """
  if isinstance(obj, float) and not math.isfinite(obj):
    if math.isnan(obj):
      return "NaN"
    return "Infinity" if obj > 0 else "-Infinity"
  if isinstance(obj, dict):
    return {k: _sanitize_floats(v) for k, v in obj.items()}
  if isinstance(obj, (list, tuple)):
    return [_sanitize_floats(v) for v in obj]
  return obj


# Per-request bound (seconds) on the static file server's blocking reads/writes. It runs on a worker
# thread, so this bounds only that thread, never the event loop. Setting a handler timeout also puts
# the accepted connection socket into a defined timeout mode via ``StreamRequestHandler.setup`` --
# without it the accepted socket would inherit the listening socket's non-blocking flag on macOS/BSD
# and the handler's blocking reads could raise ``BlockingIOError`` (CPython ``socket.accept`` only
# forces blocking when the listener has a truthy timeout, and a non-blocking listener's is ``0.0``).
# It also bounds a slow or partial client so it cannot wedge the single-threaded accept loop.
_FILE_SERVER_REQUEST_TIMEOUT_S = 10.0

# Bound (seconds) on the best-effort "stop" notification sent to the browser during teardown.
_NOTIFY_STOP_TIMEOUT_S = 5.0

# Maximum number of browser messages buffered while the single outbox worker drains them. A healthy
# browser keeps this near-empty; the cap bounds memory if the browser stalls. On overflow the newest
# event is dropped with a warning (a dropped event leaves the view stale until a browser next
# connects and sends ``ready``, which resyncs it in full).
_OUTBOX_MAX_BUFFER = 10_000


class _VisualizerHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
  """Serves the visualizer's static files.

  ``index.html`` and the favicon are pre-rendered once at startup and cached on the owning
  :class:`_VisualizerFileServer`, so serving them never touches the disk. Everything else
  (``vis.js``, ``lib.js``, images, ...) is served from ``directory`` by the standard library.
  Each request is handled on a worker thread (one ``handle_request`` per accept; see
  :meth:`Visualizer._start_file_server`), so the blocking reads here never touch the event loop.
  """

  # Bound each request's blocking reads/writes and reset the accepted socket to a defined mode; see
  # ``_FILE_SERVER_REQUEST_TIMEOUT_S``.
  timeout = _FILE_SERVER_REQUEST_TIMEOUT_S

  @override
  def log_message(self, format: str, *args: Any) -> None:
    pass

  @override
  def end_headers(self) -> None:
    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    self.send_header("Pragma", "no-cache")
    self.send_header("Expires", "0")
    super().end_headers()

  @override
  def do_GET(self) -> None:
    server = cast("_VisualizerFileServer", self.server)
    if self.path == "/":
      self._respond(server.index_html, "text/html")
    elif self.path == "/favicon.png":
      self._respond(server.favicon, "image/png")
    else:
      super().do_GET()

  def _respond(self, payload: bytes, content_type: str) -> None:
    self.send_response(200)
    self.send_header("Content-type", content_type)
    self.end_headers()
    self.wfile.write(payload)


class _VisualizerFileServer(http.server.HTTPServer):
  """``HTTPServer`` that carries the visualizer's pre-rendered ``index.html`` and favicon bytes.

  Templating/reading happens once at startup; :class:`_VisualizerHTTPRequestHandler` serves the
  cached bytes for ``/`` and ``/favicon.png``.
  """

  def __init__(self, server_address: Tuple[str, int], directory: str):
    self.index_html: bytes = b""
    self.favicon: bytes = b""
    handler = functools.partial(_VisualizerHTTPRequestHandler, directory=directory)
    super().__init__(server_address, handler)


class Visualizer(AsyncResource):
  """A class for visualizing resources and their states in a web browser.

  This class sets up a websocket server and a file server to serve a web page that visualizes the
  resources and their states. The visualizer will automatically update the visualization when the
  resources or their states change. Note that tip and volume tracking need to be enabled to see
  these in the visualizer.

  The visualizer follows structured concurrency: its servers' lifetimes are bound to an
  ``async with`` block (or the equivalent :meth:`.lifespan`). Because :mod:`websockets` only
  ships an asyncio implementation, the visualizer must be run on the asyncio backend.

  Example:
    >>> from pylabrobot.visualizer import Visualizer
    >>> async with Visualizer(deck) as visualizer:
    ...   ...  # the visualizer is running for the duration of this block

  The legacy :meth:`.setup`/:meth:`.stop` API is still available for interactive (notebook) use:

    >>> visualizer = Visualizer(deck)
    >>> await visualizer.setup()
    >>> ...
    >>> await visualizer.stop()
  """

  def __init__(
    self,
    resource: Resource,
    host: str = "127.0.0.1",
    ws_port: int = 2121,
    fs_port: int = 1337,
    open_browser: bool = True,
    name: Optional[str] = None,
    favicon: Optional[str] = None,
    show_machine_tools_at_start: bool = True,
    liquid_color: str = "F39C12",
  ):
    """Create a new Visualizer. Use ``async with`` (or :meth:`.setup`) to start the visualization.

    Args:
      host: The hostname of the file and websocket server.
      ws_port: The port of the websocket server. If this port is in use, the port will be
        incremented until a free port is found.
      fs_port: The port of the file server. If this port is in use, the port will be incremented
        until a free port is found.
      open_browser: If `True`, the visualizer will open a browser window when it is started.
      name: A custom name to display in the browser header. If ``None``, the filename of the
        calling script or notebook is detected automatically.
      favicon: Path to a ``.png`` file to use as the browser tab icon. If ``None``, the
        PyLabRobot logo is used.
      show_machine_tools_at_start: If ``True``, machine tool popups (pipettes, arm) are opened
        automatically when the visualizer starts.
      liquid_color: Hex color code (without ``#``) used to fill wells, troughs, and tubes to
        indicate liquid volume. Default is ``"F39C12"`` (amber).
    """

    if not HAS_WEBSOCKETS:
      raise RuntimeError(
        "The visualizer requires websockets to be installed. "
        f"Import error: {_WEBSOCKETS_IMPORT_ERROR}"
      )

    self._show_machine_tools_at_start = show_machine_tools_at_start
    color = liquid_color.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
      raise ValueError(
        f"liquid_color must be a 6-character hex string (e.g. 'F39C12'), got '{liquid_color}'"
      )
    self._liquid_color = color.upper()

    if name is not None:
      self._source_filename = name
    else:
      self._source_filename = self._detect_source_filename()

    if favicon is not None:
      if not favicon.endswith(".png"):
        raise ValueError("favicon must be a .png file")
      if not os.path.isfile(favicon):
        raise FileNotFoundError(f"favicon file not found: {favicon}")
      self._favicon_path = os.path.abspath(favicon)
    else:
      self._favicon_path = os.path.join(os.path.dirname(__file__), "img", "logo.png")

    # Hook into the resource (un)assigned callbacks so we can send the appropriate events to the
    # browser.
    self._root_resource = resource
    resource.register_did_assign_resource_callback(self._handle_resource_assigned_callback)
    resource.register_did_unassign_resource_callback(self._handle_resource_unassigned_callback)

    # register for callbacks
    def register_state_update(resource):
      resource.register_state_update_callback(
        lambda _: self._handle_state_update_callback(resource)
      )
      for child in resource.children:
        register_state_update(child)

    register_state_update(resource)

    self.host = host

    # file server attributes
    self.fs_port = fs_port
    self.open_browser = open_browser
    self._httpd: Optional[_VisualizerFileServer] = None

    # websocket server attributes
    self.ws_port = ws_port
    self._id = 0
    self._ws_server: Optional["websockets.asyncio.server.Server"] = None
    self._websocket: Optional["websockets.asyncio.server.ServerConnection"] = None

    # The event loop the visualizer is running on. Captured in `_enter_lifespan` so that the
    # synchronous resource-change callbacks (which may fire from any thread) can marshal work back
    # onto it. `None` when the visualizer is not running.
    self._loop: Optional[asyncio.AbstractEventLoop] = None

    # Outbound browser messages from the (synchronous) resource callbacks are funnelled through
    # this channel to a single task owned by the lifespan, instead of spawning a detached send task
    # per event. `None` when the visualizer is not running.
    self._outbox_send: Optional[MemoryObjectSendStream[Tuple[str, dict]]] = None

    self._pending_state_updates: Dict[str, dict] = {}
    self._flush_scheduled = False

    self.received: List[dict] = []
    # Ids of commands whose responses a caller is actively awaiting. Only these
    # responses are retained in ``self.received``; responses to fire-and-forget
    # commands (every state update) are dropped so the list cannot grow without
    # bound over a long-running session.
    self._pending_response_ids: set = set()

  @property
  def setup_finished(self) -> bool:
    """Whether the visualizer is currently running."""
    return getattr(self, "_active_lifespan", None) is not None

  @property
  def websocket(
    self,
  ) -> "websockets.asyncio.server.ServerConnection":
    """The websocket connection."""
    if self._websocket is None:
      raise RuntimeError("No websocket connection has been established.")
    return self._websocket

  @property
  def loop(self) -> asyncio.AbstractEventLoop:
    """The event loop the visualizer is running on."""
    if self._loop is None:
      raise RuntimeError("The visualizer has not been started.")
    return self._loop

  def _generate_id(self):
    """continuously generate unique ids 0 <= x < 10000."""
    self._id += 1
    return f"{self._id % 10000:04}"

  async def handle_event(self, event: str, data: dict):
    """Handle an event from the browser.

    Args:
      event: The event identifier.
      data: The event data, deserialized from JSON.
    """

    if event == "ping":
      await self.websocket.send(json.dumps({"event": "pong"}))

  async def _socket_handler(
    self,
    websocket: "websockets.asyncio.server.ServerConnection",
  ):
    """Handle a new websocket connection. Save the websocket connection store received
    messages in `self.received`."""

    while True:
      try:
        message = await websocket.recv()
      except websockets.exceptions.ConnectionClosed:
        return
      except asyncio.CancelledError:
        return

      data = json.loads(message)
      if data.get("id") in self._pending_response_ids:
        self.received.append(data)

      # If the event is "ready", then we can save the connection and send the saved messages.
      if data.get("event") == "ready":
        self._websocket = websocket
        await self._send_resources_and_state()

      if "event" in data:
        await self.handle_event(data.get("event"), data)
      else:
        logger.warning("Unhandled message: %s", message)

  def _assemble_command(
    self,
    event: str,
    data: Dict[str, Any],
  ) -> Tuple[str, str]:
    """Assemble a command into standard JSON form."""
    id_ = self._generate_id()
    command_data = {
      "id": id_,
      "version": STANDARD_FORM_JSON_VERSION,
      "data": data,
      "event": event,
    }
    return json.dumps(_sanitize_floats(command_data)), id_

  def has_connection(self) -> bool:
    """Return `True` if a websocket connection has been established."""
    # Since the websocket connection is saved in self.websocket, we can just check if it is `None`.
    return self._websocket is not None

  async def send_command(
    self,
    event: str,
    data: Optional[Dict[str, Any]] = None,
    wait_for_response: bool = True,
  ) -> Optional[dict]:
    """Send an event to the browser.

    If a websocket connection has not been established, the event will be saved and sent when it is
    established.

    Args:
      event: The event/command identifier.
      data: The event arguments, which must be serializable by `json.dumps`.
      wait_for_response: If `True`, the visualizer will wait for a response from the browser. If
        `False`, it is not guaranteed that the response will be available for reading at a later
        time. This is useful for sending events that do not require a response. When `True`, a
        `RuntimeError` will be raised if the response `"success"` field is not `True`.
      data: The event arguments, which must be serializable by `json.dumps`.

    Returns:
      The response from the browser, if `wait_for_response` is `True`, otherwise `None`.
    """

    if data is None:
      data = {}

    serialized_data, id_ = self._assemble_command(event=event, data=data)

    # Run and save if the websocket connection has been established, otherwise just save.
    if wait_for_response and not self.has_connection():
      raise RuntimeError("Cannot wait for response when no websocket connection is established.")

    if self.has_connection():
      await self.websocket.send(serialized_data)

      if wait_for_response:
        self._pending_response_ids.add(id_)
        try:
          while True:
            if len(self.received) > 0:
              message = self.received.pop()
              if "id" in message and message["id"] == id_:
                break
            await anyio.sleep(0.1)
        finally:
          self._pending_response_ids.discard(id_)

        if not message["success"]:
          error = message.get("error", "unknown error")
          raise RuntimeError(f"Error during event {event}: " + error)

        return message

    return None

  @staticmethod
  def _detect_source_filename() -> str:
    """Detect the filename of the calling script or notebook."""

    # 1. VS Code sets __vsc_ipynb_file__ in the IPython user namespace.
    try:
      ipython = get_ipython()  # type: ignore[name-defined]  # noqa: F821
      vsc_file = getattr(ipython, "user_ns", {}).get("__vsc_ipynb_file__")
      if vsc_file:
        return str(os.path.basename(vsc_file))
    except NameError:
      pass

    # 2. Try ipynbname package (works for classic Jupyter Notebook and JupyterLab).
    try:
      import ipynbname  # type: ignore[import-untyped,import-not-found]

      nb_path = ipynbname.path()
      if nb_path:
        return os.path.basename(str(nb_path))
    except Exception:
      pass

    # 3. Query the Jupyter REST API using the kernel connection file.
    try:
      import json as _json
      import urllib.request

      import ipykernel  # type: ignore[import-untyped]

      # Get the kernel id from the connection file path.
      connection_file = ipykernel.get_connection_file()
      kernel_id = os.path.basename(connection_file).replace("kernel-", "").replace(".json", "")

      # Try common Jupyter server ports and tokens.
      # First, try to get server info from jupyter_core / notebook.
      servers = []
      try:
        from jupyter_server.serverapp import (  # type: ignore[import-untyped,import-not-found]
          list_running_servers,
        )

        servers = list(list_running_servers())
      except Exception:
        pass
      if not servers:
        try:
          from notebook.notebookapp import (  # type: ignore[import-untyped,import-not-found,no-redef]
            list_running_servers,
          )

          servers = list(list_running_servers())
        except Exception:
          pass

      for srv in servers:
        base_url = srv.get("url", "").rstrip("/")
        token = srv.get("token", "")
        try:
          api_url = f"{base_url}/api/sessions"
          if token:
            api_url += f"?token={token}"
          req = urllib.request.Request(api_url)
          with urllib.request.urlopen(req, timeout=2) as resp:
            sessions = _json.loads(resp.read().decode())
          for sess in sessions:
            kid = sess.get("kernel", {}).get("id", "")
            if kid == kernel_id:
              nb_path = sess.get("notebook", {}).get("path", "") or sess.get("path", "")
              if nb_path:
                return str(os.path.basename(nb_path))
        except Exception:
          continue
    except Exception:
      pass

    # 4. Fall back to stack inspection for .py scripts.
    for frame_info in inspect.stack():
      fname = frame_info.filename
      if fname == __file__:
        continue
      basename = os.path.basename(fname)
      if "ipykernel" in fname or fname.startswith("<"):
        continue
      if basename.endswith(".py"):
        return basename

    return ""

  @override
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Start the visualizer's servers, bound to the lifetime of ``stack``.

    Starts (1) the websocket server on the running asyncio event loop, (2) an outbox task that
    serializes browser messages produced by the resource callbacks, and (3) the static file server
    (a blocking ``http.server`` offloaded to a worker thread). All three are children of a single
    task group and are torn down deterministically when the lifespan exits.
    """

    # `websockets` only ships an asyncio server, so the visualizer cannot run on Trio.
    if (backend := sniffio.current_async_library()) != "asyncio":
      raise RuntimeError(
        "The visualizer only supports the asyncio backend, because `websockets` provides an "
        f"asyncio-only server implementation (running under {backend!r})."
      )

    # Capture the running event loop so that the synchronous resource-change callbacks (which may
    # fire from any thread) can marshal work back onto it.
    self._loop = asyncio.get_running_loop()
    stack.callback(self._clear_loop)

    tg = await stack.enter_async_context(anyio.create_task_group())
    # Safety net: cancel the task group's children (the outbox worker and the file-server accept
    # loop) at teardown so the task-group join does not wait on them. The other teardown steps that
    # could otherwise block are separately bounded: the file-server request is abandoned on
    # cancellation (see `_start_file_server`) and the browser "stop" notification is time-bounded
    # (see `_notify_browser_stop`).
    stack.callback(tg.cancel_scope.cancel)

    await self._start_ws_server(stack)
    self._start_outbox(stack, tg)
    await self._start_file_server(stack, tg)

    if self.open_browser:
      webbrowser.open(f"http://{self.host}:{self.fs_port}")

  async def _start_ws_server(self, stack: AsyncExitStackWithShielding) -> None:
    """Start the websocket server on the running event loop.

    ``websockets.asyncio.server.serve`` is itself an async context manager: entering it starts the
    server and exiting it closes the server and waits for all connection handlers to finish. We bind
    that lifetime to ``stack`` so it is cleaned up as part of the lifespan.
    """

    while True:
      try:
        server = websockets.asyncio.server.serve(self._socket_handler, self.host, self.ws_port)
        self._ws_server = await stack.enter_async_context(server)
        break
      except OSError:
        # If the port is in use, try the next port.
        self.ws_port += 1

    # Best-effort notify the browser that we are going away. Pushed after the server context so it
    # runs *before* the server (and the connection) is closed during teardown. Shielded so the send
    # can complete even while the lifespan is being cancelled.
    stack.push_shielded_async_callback(self._notify_browser_stop)

    print(f"Websocket server started at http://{self.host}:{self.ws_port}")

  async def _notify_browser_stop(self) -> None:
    """Tell a connected browser that the visualizer is stopping. Best-effort and time-bounded.

    This runs shielded during teardown (see :meth:`_start_ws_server`), so it must bound itself:
    against a stalled-but-open browser ``websocket.send`` can await write-buffer drain, and the
    shield would otherwise let that block teardown. ``move_on_after`` caps the wait on both clean and
    cancelled exits.
    """
    if self.has_connection():
      try:
        with anyio.move_on_after(_NOTIFY_STOP_TIMEOUT_S):
          await self.send_command("stop", wait_for_response=False)
      except Exception:
        # The connection may already be gone; stopping is best-effort.
        pass

  def _start_outbox(self, stack: AsyncExitStackWithShielding, tg: anyio.abc.TaskGroup) -> None:
    """Start the task that serializes outbound browser messages.

    The resource-change callbacks are synchronous and may run on any thread. Rather than spawning a
    detached send task per event (an unstructured "go statement" whose errors would be silently
    dropped), they enqueue onto this channel and a single task -- owned by the lifespan's task group
    -- performs the sends in order. This keeps every concurrent send bound to the lifespan and lets
    send failures be handled in one place. The channel is bounded (``_OUTBOX_MAX_BUFFER``): if the
    browser stalls so the worker cannot drain it, enqueuing drops the newest event with a warning
    rather than growing without bound (a dropped event leaves the view stale until a browser next
    connects and sends ``ready``, which resyncs it in full).
    """

    send_stream, receive_stream = anyio.create_memory_object_stream[Tuple[str, dict]](
      _OUTBOX_MAX_BUFFER
    )
    self._outbox_send = send_stream
    # Closing the send stream lets the worker drain and exit cleanly before the task group joins.
    stack.callback(self._close_outbox)
    tg.start_soon(self._outbox_worker, receive_stream)

  async def _outbox_worker(
    self, receive_stream: MemoryObjectReceiveStream[Tuple[str, dict]]
  ) -> None:
    """Drain the outbox channel, sending each queued event to the browser, in order."""
    async with receive_stream:
      async for event, data in receive_stream:
        try:
          await self.send_command(event=event, data=data, wait_for_response=False)
        except Exception:
          # Tolerate a disconnected or slow browser; never tear down the visualizer over a send.
          logger.exception("visualizer: failed to send %r event to browser", event)

  async def _start_file_server(
    self, stack: AsyncExitStackWithShielding, tg: anyio.abc.TaskGroup
  ) -> None:
    """Start a simple webserver to serve static files.

    ``http.server`` is blocking, so rather than holding a ``serve_forever`` worker thread for the
    whole lifespan, we drive it from an asyncio-native accept loop: the listening socket is made
    non-blocking and, whenever it becomes readable, a single ``handle_request()`` is offloaded to a
    worker thread via ``anyio.to_thread.run_sync``. No thread is held while the server is idle. The
    loop runs in ``tg`` and is cancelled on teardown (see the ``cancel_scope.cancel`` net in
    ``_enter_lifespan``), closing the socket in its ``finally``.
    """

    dirname = os.path.dirname(__file__)
    path = os.path.join(dirname, ".")
    if not os.path.exists(path):
      raise RuntimeError(
        "Could not find Visualizer files. Please run from the root of the repository."
      )

    # Binding happens synchronously and fast; retry on the next port if this one is in use.
    while True:
      try:
        self._httpd = _VisualizerFileServer((self.host, self.fs_port), path)
        break
      except OSError:
        self.fs_port += 1
    httpd = self._httpd

    # Render index.html and read the favicon once (ports/options are fixed now), so requests for
    # those paths are served from memory rather than re-reading the disk every time.
    httpd.index_html = self._render_index_html(path)
    with open(self._favicon_path, "rb") as f:
      httpd.favicon = f.read()

    # Make the listening socket non-blocking so the accept loop can await its readability on the
    # event loop; `handle_request` is only ever called once the socket is readable, so its accept
    # never blocks. The per-request read bound lives on the handler
    # (`_VisualizerHTTPRequestHandler.timeout`), not here: once the listening socket is non-blocking
    # its `gettimeout()` is 0.0, which would win `min(gettimeout(), server.timeout)` and make any
    # `HTTPServer.timeout` inert.
    httpd.socket.setblocking(False)

    print(
      f"File server started at http://{self.host}:{self.fs_port} . Open this URL in your browser."
    )

    serving = anyio.Event()

    async def accept_requests() -> None:
      serving.set()
      try:
        while True:
          await anyio.wait_readable(httpd.socket)
          try:
            # `abandon_on_cancel=True`: on teardown cancellation we must not wait for an in-flight
            # request to finish on the worker thread (the handler's own `timeout` bounds it); the
            # accept loop returns immediately so the task-group join can complete.
            await anyio.to_thread.run_sync(httpd.handle_request, abandon_on_cancel=True)
          except Exception:
            # One bad request must not tear down the server.
            logger.exception("visualizer: error handling a file-server request")
      finally:
        httpd.server_close()
        self._httpd = None

    tg.start_soon(accept_requests)
    await serving.wait()

  def _render_index_html(self, directory: str) -> bytes:
    """Read ``index.html`` from ``directory`` and substitute the template placeholders."""
    with open(os.path.join(directory, "index.html"), "r", encoding="utf-8") as f:
      content = f.read()
    content = (
      content.replace("{{ ws_port }}", str(self.ws_port))
      .replace("{{ fs_port }}", str(self.fs_port))
      .replace("{{ source_filename }}", self._source_filename)
      .replace("{{ liquid_color }}", self._liquid_color)
    )
    return content.encode("utf-8")

  async def setup(self) -> None:
    """Start the visualizer (legacy, interactive API).

    Prefer structured concurrency (``async with visualizer:``). This method schedules the
    visualizer's lifespan on a global task group so that it can be started and stopped from
    separate calls (e.g. in a notebook). It is only supported on the asyncio backend.
    """
    await global_manager.manage_context(self)

  async def stop(self) -> None:
    """Stop the visualizer started with :meth:`.setup`."""
    await global_manager.release_context(self)

  def _clear_loop(self) -> None:
    """Reset per-run state when the lifespan exits."""
    self.received.clear()
    self._pending_response_ids.clear()
    self._websocket = None
    self._ws_server = None
    self._loop = None
    self._pending_state_updates.clear()
    self._flush_scheduled = False

  def _close_outbox(self) -> None:
    """Close the outbox channel so the outbox worker drains and exits."""
    if (send := self._outbox_send) is not None:
      self._outbox_send = None
      send.close()

  async def _send_resources_and_state(self):
    """Private method for sending the resource and state to the browser. This is called after the
    browser has sent a "ready" event."""

    # send the serialized root resource (including all children) to the browser
    await self.send_command(
      "set_root_resource",
      {
        "resource": _serialize_resource_tree(self._root_resource),
        "method_registry": _build_method_registry(self._root_resource),
      },
      wait_for_response=False,
    )

    # serialize the state and send it to the browser
    # TODO: can we merge this with the code that already exists in Deck?
    state: Dict[str, Any] = {}

    def save_resource_state(resource: Resource):
      """Recursively save the state of the resource and all child resources."""
      resource_state = resource.serialize_state()
      if resource_state is not None:
        state[resource.name] = resource_state
      for child in resource.children:
        save_resource_state(child)

    save_resource_state(self._root_resource)
    await self.send_command("set_state", state, wait_for_response=False)

    if self._show_machine_tools_at_start:
      await self.send_command("show_machine_tools", {}, wait_for_response=False)

  def _emit(self, event: str, data: dict) -> None:
    """Queue an outbound browser message from a (possibly off-loop-thread) resource callback."""
    if (loop := self._loop) is None:
      return  # The visualizer is not running; nothing to send.
    loop.call_soon_threadsafe(self._enqueue_outbound, event, data)

  def _enqueue_outbound(self, event: str, data: dict) -> None:
    """Push a message onto the outbox channel. Must run on the event loop thread."""
    if (send := self._outbox_send) is not None:
      try:
        send.send_nowait((event, data))
      except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
        logger.warning("visualizer: dropping %r event (outbox closed or full)", event)

  def _handle_resource_assigned_callback(self, resource: Resource) -> None:
    """Called when a resource is assigned to a resource already in the tree starting from the
    root resource. This method will send an event about the new resource"""

    # TODO: unassign should deregister the callbacks
    # register for callbacks
    def register_state_update(resource: Resource):
      resource.register_state_update_callback(
        lambda _: self._handle_state_update_callback(resource)
      )
      for child in resource.children:
        register_state_update(child)

    register_state_update(resource)

    self._emit(
      "resource_assigned",
      {
        "resource": _serialize_resource_tree(resource),
        "method_registry": _build_method_registry(resource),
        "state": resource.serialize_all_state(),
        "parent_name": (resource.parent.name if resource.parent else None),
      },
    )

  def _handle_resource_unassigned_callback(self, resource: Resource) -> None:
    """Called when a resource is unassigned from a resource already in the tree starting from the
    root resource. This method will send an event about the removed resource"""

    self._emit("resource_unassigned", {"resource_name": resource.name})

  def _handle_state_update_callback(self, resource: Resource) -> None:
    """Called when the state of a resource is updated. Updates are batched so that
    rapid successive changes (e.g. 96-channel pickup) are sent as a single message."""

    if (loop := self._loop) is None:
      return  # The visualizer is not running; nothing to send.
    state = resource.serialize_state()
    loop.call_soon_threadsafe(self._enqueue_state_update, resource.name, state)

  def _enqueue_state_update(self, name: str, state: dict) -> None:
    """Enqueue a state update on the event loop thread and schedule a flush if needed."""
    self._pending_state_updates[name] = state
    if not self._flush_scheduled and self._loop is not None:
      self._flush_scheduled = True
      self._loop.call_soon(self._flush_state_updates)

  def _flush_state_updates(self) -> None:
    """Send all pending state updates as a single ``set_state`` event."""
    self._flush_scheduled = False
    if data := self._pending_state_updates:
      self._pending_state_updates = {}
      self._enqueue_outbound("set_state", data)
