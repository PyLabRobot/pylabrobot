import asyncio
import http.server
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import webbrowser

try:
  import websockets
  import websockets.exceptions
  import websockets.legacy
  import websockets.legacy.server
  import websockets.server
  HAS_WEBSOCKETS = True
except ImportError:
  HAS_WEBSOCKETS = False

from pylabrobot.resources import Resource
from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION


logger = logging.getLogger("pylabrobot")


class Visualizer:
  """ A class for visualizing resources and their states in a web browser.

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
    ws_host: str = "127.0.0.1",
    ws_port: int = 2121,
    fs_host: str = "127.0.0.1",
    fs_port: int = 1337,
    open_browser: bool = True,
  ):
    """ Create a new Visualizer. Use :meth:`.setup` to start the visualization.

    Args:
      ws_host: The hostname of the websocket server.
      ws_port: The port of the websocket server. If this port is in use, the port will be
        incremented until a free port is found.
      fs_host: The hostname of the file server. This is where the visualization will be served.
      fs_port: The port of the file server. If this port is in use, the port will be incremented
        until a free port is found.
      open_browser: If `True`, the visualizer will open a browser window when it is started.
    """

    self.setup_finished = False

    # Hook into the resource (un)assigned callbacks so we can send the appropriate events to the
    # browser.
    self._root_resource = resource
    resource.register_did_assign_resource_callback(self._handle_resource_assigned_callback)
    resource.register_did_unassign_resource_callback(self._handle_resource_unassigned_callback)

    # register for callbacks
    def register_state_update(resource):
      resource.register_state_update_callback(
        lambda _: self._handle_state_update_callback(resource))
      for child in resource.children:
        register_state_update(child)
    register_state_update(resource)

    # file server attributes
    self.fs_host = fs_host
    self.fs_port = fs_port
    self.open_browser = open_browser

    self._httpd: Optional[http.server.HTTPServer] = None
    self._fst: Optional[threading.Thread] = None

    # websocket server attributes
    self.ws_host = ws_host
    self.ws_port = ws_port
    self._id = 0

    self._websocket: Optional["websockets.legacy.server.WebSocketServerProtocol"] = None
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    self._t: Optional[threading.Thread] = None
    self._stop_: Optional[asyncio.Future] = None

    self.received: List[dict] = []

  @property
  def websocket(self) -> "websockets.legacy.server.WebSocketServerProtocol":
    """ The websocket connection. """
    if self._websocket is None:
      raise RuntimeError("No websocket connection has been established.")
    return self._websocket

  @property
  def loop(self) -> asyncio.AbstractEventLoop:
    """ The event loop. """
    if self._loop is None:
      raise RuntimeError("Event loop has not been started.")
    return self._loop

  @property
  def t(self) -> threading.Thread:
    """ The thread that runs the event loop. """
    if self._t is None:
      raise RuntimeError("Event loop has not been started.")
    return self._t

  @property
  def stop_(self) -> asyncio.Future:
    """ The future that is set when the visualizer is stopped. """
    if self._stop_ is None:
      raise RuntimeError("Event loop has not been started.")
    return self._stop_

  def _generate_id(self):
    """ continuously generate unique ids 0 <= x < 10000. """
    self._id += 1
    return f"{self._id % 10000:04}"

  async def handle_event(self, event: str, data: dict):
    """ Handle an event from the browser.

    Args:
      event: The event identifier.
      data: The event data, deserialized from JSON.
    """

    # pylint: disable=unused-argument

    if event == "ping":
      await self.websocket.send(json.dumps({"event": "pong"}))

  async def _socket_handler(self, websocket: "websockets.legacy.server.WebSocketServerProtocol"):
    """ Handle a new websocket connection. Save the websocket connection store received
    messages in `self.received`. """

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
    """ Assemble a command into standard JSON form. """
    id_ = self._generate_id()
    command_data = {
      "id": id_,
      "version": STANDARD_FORM_JSON_VERSION,
      "data": data,
      "event": event
    }
    return json.dumps(command_data), id_

  def has_connection(self) -> bool:
    """ Return `True` if a websocket connection has been established. """
    # Since the websocket connection is saved in self.websocket, we can just check if it is `None`.
    return self._websocket is not None

  async def send_command(
    self,
    event: str,
    data: Optional[Dict[str, Any]] = None,
    wait_for_response: bool = True,
  )-> Optional[dict]:
    """ Send an event to the browser.

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
    """ The HTTP server. """
    if self._httpd is None:
      raise RuntimeError("The HTTP server has not been started yet.")
    return self._httpd

  @property
  def fst(self) -> threading.Thread:
    """ The file server thread. """
    if self._fst is None:
      raise RuntimeError("The file server thread has not been started yet.")
    return self._fst

  async def setup(self):
    """ Start the visualizer.

    Sets up the file and websocket servers. These will run in a separate thread.
    """

    if self.setup_finished:
      raise RuntimeError("The visualizer has already been started.")

    await self._run_ws_server()
    self._run_file_server()
    self.setup_finished = True

  async def _run_ws_server(self):
    """ Start the websocket server.

    Sets up the websocket server. This will run in a separate thread.
    """

    if not HAS_WEBSOCKETS:
      raise RuntimeError("The visualizer requires websockets to be installed.")

    async def run_server():
      self._stop_ = self.loop.create_future()
      while True:
        try:
          async with websockets.server.serve(self._socket_handler, self.ws_host, self.ws_port):
            print(f"Websocket server started at http://{self.ws_host}:{self.ws_port}")
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
    lock.acquire() # pylint: disable=consider-using-with
    self._loop = asyncio.new_event_loop()
    self._t = threading.Thread(target=start_loop, daemon=True)
    self.t.start()

    while lock.locked():
      time.sleep(0.001)

  def _run_file_server(self):
    """ Start a simple webserver to serve static files. """

    dirname = os.path.dirname(__file__)
    path = os.path.join(dirname, ".")
    if not os.path.exists(path):
      raise RuntimeError("Could not find Visualizer files. Please run from the root of the "
                         "repository.")

    def start_server():
      ws_host, ws_port, fs_host, fs_port = self.ws_host, self.ws_port, self.fs_host, self.fs_port

      # try to start the server. If the port is in use, try with another port until it succeeds.
      class QuietSimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
        """ A simple HTTP request handler that does not log requests. """
        def __init__(self, *args, **kwargs):
          super().__init__(*args, directory=path, **kwargs)

        def log_message(self, format, *args):
          # pylint: disable=redefined-builtin
          pass

        def do_GET(self) -> None:
          # rewrite some info in the index.html file on the fly,
          # like a simple template engine
          if self.path == "/":
            with open(os.path.join(path, "index.html"), "r", encoding="utf-8") as f:
              content = f.read()

            content = content.replace("{{ ws_host }}", ws_host)
            content = content.replace("{{ ws_port }}", str(ws_port))
            content = content.replace("{{ fs_host }}", fs_host)
            content = content.replace("{{ fs_port }}", str(fs_port))

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
          else:
            return super().do_GET()

      while True:
        try:
          self._httpd = http.server.HTTPServer((self.fs_host, self.fs_port),
            QuietSimpleHTTPRequestHandler)
          print(f"File server started at http://{self.fs_host}:{self.fs_port} . "
                 "Open this URL in your browser.")
          break
        except OSError:
          self.fs_port += 1

      self.httpd.serve_forever()

    self._fst = threading.Thread(name="visualizer_fs", target=start_server, daemon=True)
    self.fst.start()

    if self.open_browser:
      webbrowser.open(f"http://{self.fs_host}:{self.fs_port}")

  async def stop(self):
    """ Stop the visualizer.

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
    """ Private method for sending the resource and state to the browser. This is called after the
    browser has sent a "ready" event. """

    # send the serialized root resource (including all children) to the browser
    await self.send_command("set_root_resource", {"resource": self._root_resource.serialize()},
                            wait_for_response=False)

    # serialize the state and send it to the browser
    # TODO: can we merge this with the code that already exists in Deck?
    state: Dict[str, Any] = {}
    def save_resource_state(resource: Resource):
      """ Recursively save the state of the resource and all child resources. """
      if hasattr(resource, "tracker"):
        resource_state = resource.tracker.serialize()
        if resource_state is not None:
          state[resource.name] = resource_state
      for child in resource.children:
        save_resource_state(child)
    save_resource_state(self._root_resource)
    await self.send_command("set_state", state, wait_for_response=False)

  def _handle_resource_assigned_callback(self, resource: Resource) -> None:
    """ Called when a resource is assigned to a resource already in the tree starting from the
    root resource. This method will send an event about the new resource """

    # TODO: unassign should deregister the callbacks
    # register for callbacks
    def register_state_update(resource: Resource):
      resource.register_state_update_callback(
        lambda _: self._handle_state_update_callback(resource))
      for child in resource.children:
        register_state_update(child)
    register_state_update(resource)

    # Send a `resource_assigned` event to the browser.
    data = {
      "resource": resource.serialize(),
      "state": resource.serialize_all_state(),
      "parent_name": (resource.parent.name if resource.parent else None)
    }
    fut = self.send_command(
      event="resource_assigned",
      data=data,
      wait_for_response=False)
    asyncio.run_coroutine_threadsafe(fut, self.loop)

  def _handle_resource_unassigned_callback(self, resource: Resource) -> None:
    """ Called when a resource is unassigned from a resource already in the tree starting from the
    root resource. This method will send an event about the removed resource """

    # Send a `resource_unassigned` event to the browser.
    data = { "resource_name": resource.name }
    fut = self.send_command(
      event="resource_unassigned",
      data=data,
      wait_for_response=False)
    asyncio.run_coroutine_threadsafe(fut, self.loop)

  def _handle_state_update_callback(self, resource: Resource) -> None:
    """ Called when the state of a resource is updated. This method will send an event to the
    browser about the updated state. """

    # Send a `set_state` event to the browser.
    data = { resource.name: resource.serialize_state() }
    fut = self.send_command(
      event="set_state",
      data=data,
      wait_for_response=False)
    asyncio.run_coroutine_threadsafe(fut, self.loop)
