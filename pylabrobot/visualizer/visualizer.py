import asyncio
import functools
import http.server
import inspect
import json
import logging
import math
import os
import threading
import time
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

try:
  import websockets
  import websockets.asyncio.server
  import websockets.exceptions

  HAS_WEBSOCKETS = True
except ImportError as e:
  HAS_WEBSOCKETS = False
  _WEBSOCKETS_IMPORT_ERROR = e

from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION
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


def _serialize_with_methods(resource: Resource) -> dict:
  """Serialize a resource and enrich with Python method signatures for the visualizer."""
  data = resource.serialize()
  data["methods"] = _get_public_methods(type(resource))  # type: ignore[arg-type]
  data["children"] = [_serialize_with_methods(child) for child in resource.children]
  return data


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


class Visualizer:
  """A class for visualizing resources and their states in a web browser.

  This class sets up a websocket server and a file server to serve a web page that visualizes the
  resources and their states. The visualizer will automatically update the visualization when the
  resources or their states change. Note that tip and volume tracking need to be enabled to see
  these in the visualizer.

  Example:
    >>> from pylabrobot.visualizer import Visualizer
    >>> visualizer = Visualizer(deck)
    >>> await visualizer.setup()
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
  ):
    """Create a new Visualizer. Use :meth:`.setup` to start the visualization.

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
    """

    self.setup_finished = False
    self._show_machine_tools_at_start = show_machine_tools_at_start

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

    self._httpd: Optional[http.server.HTTPServer] = None
    self._fst: Optional[threading.Thread] = None

    # websocket server attributes
    self.ws_port = ws_port
    self._id = 0

    self._websocket: Optional["websockets.asyncio.server.ServerConnection"] = None
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    self._t: Optional[threading.Thread] = None
    self._stop_: Optional[asyncio.Future] = None

    self._pending_state_updates: Dict[str, dict] = {}
    self._flush_scheduled = False

    self.received: List[dict] = []

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
    """The event loop."""
    if self._loop is None:
      raise RuntimeError("Event loop has not been started.")
    return self._loop

  @property
  def t(self) -> threading.Thread:
    """The thread that runs the event loop."""
    if self._t is None:
      raise RuntimeError("Event loop has not been started.")
    return self._t

  @property
  def stop_(self) -> asyncio.Future:
    """The future that is set when the visualizer is stopped."""
    if self._stop_ is None:
      raise RuntimeError("Event loop has not been started.")
    return self._stop_

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
        while True:
          if len(self.received) > 0:
            message = self.received.pop()
            if "id" in message and message["id"] == id_:
              break
          await asyncio.sleep(0.1)

        if not message["success"]:
          error = message.get("error", "unknown error")
          raise RuntimeError(f"Error during event {event}: " + error)

        return message

    return None

  @property
  def httpd(self) -> http.server.HTTPServer:
    """The HTTP server."""
    if self._httpd is None:
      raise RuntimeError("The HTTP server has not been started yet.")
    return self._httpd

  @property
  def fst(self) -> threading.Thread:
    """The file server thread."""
    if self._fst is None:
      raise RuntimeError("The file server thread has not been started yet.")
    return self._fst

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

  async def setup(self):
    """Start the visualizer.

    Sets up the file and websocket servers. These will run in a separate thread.
    """

    if self.setup_finished:
      raise RuntimeError("The visualizer has already been started.")

    await self._run_ws_server()
    self._run_file_server()
    self.setup_finished = True

  async def _run_ws_server(self):
    """Start the websocket server.

    Sets up the websocket server. This will run in a separate thread.
    """

    if not HAS_WEBSOCKETS:
      raise RuntimeError(
        f"The visualizer requires websockets to be installed. Import error: {_WEBSOCKETS_IMPORT_ERROR}"
      )

    async def run_server():
      self._stop_ = self.loop.create_future()
      while True:
        try:
          async with websockets.asyncio.server.serve(self._socket_handler, self.host, self.ws_port):
            print(f"Websocket server started at http://{self.host}:{self.ws_port}")
            lock.release()
            await self.stop_
            break
        except asyncio.CancelledError:
          pass
        except OSError:
          # If the port is in use, try the next port.
          self.ws_port += 1

    def start_loop():
      self.loop.run_until_complete(run_server())

    # Acquire a lock to prevent setup from returning until the server is running.
    lock = threading.Lock()
    lock.acquire()
    self._loop = asyncio.new_event_loop()
    self._t = threading.Thread(target=start_loop, daemon=True)
    self.t.start()

    while lock.locked():
      time.sleep(0.001)

  def _run_file_server(self):
    """Start a simple webserver to serve static files."""

    dirname = os.path.dirname(__file__)
    path = os.path.join(dirname, ".")
    if not os.path.exists(path):
      raise RuntimeError(
        "Could not find Visualizer files. Please run from the root of the " "repository."
      )

    def start_server(lock):
      ws_port, fs_port, source_filename = self.ws_port, self.fs_port, self._source_filename
      favicon_path = self._favicon_path

      # try to start the server. If the port is in use, try with another port until it succeeds.
      class QuietSimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
        """A simple HTTP request handler that does not log requests."""

        def __init__(self, *args, **kwargs):
          super().__init__(*args, directory=path, **kwargs)

        def log_message(self, format, *args):
          pass

        def end_headers(self):
          self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
          self.send_header("Pragma", "no-cache")
          self.send_header("Expires", "0")
          super().end_headers()

        def do_GET(self) -> None:
          # rewrite some info in the index.html file on the fly,
          # like a simple template engine
          if self.path == "/":
            with open(os.path.join(path, "index.html"), "r", encoding="utf-8") as f:
              content = f.read()

            content = content.replace("{{ ws_port }}", str(ws_port))
            content = content.replace("{{ fs_port }}", str(fs_port))
            content = content.replace("{{ source_filename }}", source_filename)

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
          elif self.path == "/favicon.png":
            with open(favicon_path, "rb") as f:
              data = f.read()
            self.send_response(200)
            self.send_header("Content-type", "image/png")
            self.end_headers()
            self.wfile.write(data)
          else:
            return super().do_GET()

      while True:
        try:
          self._httpd = http.server.HTTPServer(
            (self.host, self.fs_port),
            QuietSimpleHTTPRequestHandler,
          )
          print(
            f"File server started at http://{self.host}:{self.fs_port} . "
            "Open this URL in your browser."
          )
          lock.release()
          break
        except OSError:
          self.fs_port += 1

      self.httpd.serve_forever()

    lock = threading.Lock()
    lock.acquire()
    self._fst = threading.Thread(
      name="visualizer_fs",
      target=start_server,
      args=(lock,),
      daemon=True,
    )
    self.fst.start()

    # Wait for the server to start before opening the browser so that we can get the correct port.
    while lock.locked():
      time.sleep(0.001)

    if self.open_browser:
      webbrowser.open(f"http://{self.host}:{self.fs_port}")

  async def stop(self):
    """Stop the visualizer.

    Raises:
      RuntimeError: If the visualizer has not been started.
    """

    # -- file server --
    # Stop the file server.
    self.httpd.shutdown()
    self.httpd.server_close()

    # Clear all relevant attributes.
    self._httpd = None
    self._fst = None

    # -- websocket --
    if self.has_connection():
      # send stop event to the browser
      await self.send_command("stop", wait_for_response=False)

      # must be thread safe, because event loop is running in a separate thread
      self.loop.call_soon_threadsafe(self.stop_.set_result, "done")

    # Clear all relevant attributes.
    self.received.clear()
    self._websocket = None
    self._loop = None
    self._t = None
    self._stop_ = None

    self.setup_finished = False

  async def _send_resources_and_state(self):
    """Private method for sending the resource and state to the browser. This is called after the
    browser has sent a "ready" event."""

    # send the serialized root resource (including all children) to the browser
    await self.send_command(
      "set_root_resource",
      {"resource": _serialize_with_methods(self._root_resource)},
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

    # Send a `resource_assigned` event to the browser.
    data = {
      "resource": _serialize_with_methods(resource),
      "state": resource.serialize_all_state(),
      "parent_name": (resource.parent.name if resource.parent else None),
    }
    fut = self.send_command(event="resource_assigned", data=data, wait_for_response=False)
    asyncio.run_coroutine_threadsafe(fut, self.loop)

  def _handle_resource_unassigned_callback(self, resource: Resource) -> None:
    """Called when a resource is unassigned from a resource already in the tree starting from the
    root resource. This method will send an event about the removed resource"""

    # Send a `resource_unassigned` event to the browser.
    data = {"resource_name": resource.name}
    fut = self.send_command(event="resource_unassigned", data=data, wait_for_response=False)
    asyncio.run_coroutine_threadsafe(fut, self.loop)

  def _handle_state_update_callback(self, resource: Resource) -> None:
    """Called when the state of a resource is updated. Updates are batched so that
    rapid successive changes (e.g. 96-channel pickup) are sent as a single message."""

    state = resource.serialize_state()
    self.loop.call_soon_threadsafe(self._enqueue_state_update, resource.name, state)

  def _enqueue_state_update(self, name: str, state: dict) -> None:
    """Enqueue a state update on the event loop thread and schedule a flush if needed."""
    self._pending_state_updates[name] = state
    if not self._flush_scheduled:
      self._flush_scheduled = True
      self.loop.call_soon(self._flush_state_updates)

  def _flush_state_updates(self) -> None:
    """Send all pending state updates as a single ``set_state`` event."""
    data = self._pending_state_updates
    self._pending_state_updates = {}
    self._flush_scheduled = False
    if data:
      fut = self.send_command(event="set_state", data=data, wait_for_response=False)
      asyncio.ensure_future(fut)
