import abc
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

from pylabrobot.machine import Machine
from pylabrobot.resources import Container, Liquid, Plate, Resource, TipRack
from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION


logger = logging.getLogger("pylabrobot")


class SimulatorBackend(abc.ABC):
  """ Abstract base class for backends that communicate with the simulator. """

  @abc.abstractmethod
  def __init__(self, simulator: "Simulator"):
    """ Create a new backend. This will connect to the
    {class}`~pylabrobot.simulator.Simulator` and send commands to it.

    Args:
      simulator: The simulator to use.
    """

    self.simulator = simulator


class Simulator:
  """ This class hosts the file server and the websocket server for simulating a lab. Different
  simulated device backends (like LiquidHandlerSimulator) use this object as a singular
  communication channel.

  You can view the simulation at `http://localhost:1337 <http://localhost:1337>`_, where
  `static/index.html` will be served.

  The websocket server will run at `http://localhost:2121 <http://localhost:2121>`_ by default. If a
  new browser page connects, it will replace an existing connection. All previously sent actions
  will be sent to the new page, with no simulated delay, to ensure that the state of the simulation
  remains the same. This also happens when a browser reloads the page or on the first page load.

  .. note::

    See :doc:`/using-the-simulator` for a tutorial.
  """

  def __init__(
    self,
    ws_host: str = "127.0.0.1",
    ws_port: int = 2121,
    fs_host: str = "127.0.0.1",
    fs_port: int = 1337,
    open_browser: bool = True,
  ):
    """ Create a new simulator. Use :meth:`.setup` to start the simulation.

    Args:
      ws_host: The hostname of the websocket server.
      ws_port: The port of the websocket server. If this port is in use, the port will be
        incremented until a free port is found.
      fs_host: The hostname of the file server. This is where the simulation will be served.
      fs_port: The port of the file server. If this port is in use, the port will be incremented
        until a free port is found.
      open_browser: If `True`, the simulation will open a browser window when it is started.
    """

    self.setup_finished = False
    self.devices: Dict[SimulatorBackend, str] = {}
    self.root_resource: Optional[Resource] = None

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

    self._sent_messages: List[str] = []
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
    """ The future that is set when the simulation is stopped. """
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
        await self._replay()

        # Echo command
        await websocket.send(json.dumps(data))

      if "event" in data:
        await self.handle_event(data.get("event"), data)
      else:
        logger.warning("Unhandled message: %s", message)

  def _assemble_command(
    self,
    event: str,
    data: Dict[str, Any],
    device_name: Optional[str] = None
  ) -> Tuple[str, str]:
    """ Assemble a command into standard JSON form. """
    id_ = self._generate_id()
    command_data = {
      "id": id_,
      "version": STANDARD_FORM_JSON_VERSION,
      "device_name": device_name,
      "data": data,
      "event": event
    }
    return json.dumps(command_data), id_

  def has_connection(self) -> bool:
    """ Return `True` if a websocket connection has been established. """
    # Since the websocket connection is saved in self.websocket, we can just check if it is `None`.
    return self._websocket is not None

  def wait_for_connection(self):
    """ Wait for a websocket connection to be established.

    This method will block until a websocket connection is established. It is not required to wait,
    since :meth:`~WebSocketBackend.send_event` can automatically save messages until a connection is
    established, but that only happens if its `wait_for_response` parameter is `False`.
    """

    while not self.has_connection():
      time.sleep(0.1)

  async def send_command(
    self,
    event: str,
    data: Optional[Dict[str, Any]] = None,
    device: Optional[SimulatorBackend]=None,
    wait_for_response: bool = True,
  )-> Optional[dict]:
    """ Send an event to the browser.

    If a websocket connection has not been established, the event will be saved and sent when it is
    established.

    Args:
      event: The event/command identifier.
      data: The event arguments, which must be serializable by `json.dumps`.
      device: The device that is sending the command. This is used to send the command to the
        correct device in the browser. If `None`, the command will be interpreted by the global
        event handler.
      wait_for_response: If `True`, the simulation will wait for a response from the browser. If
        `False`, it is not guaranteed that the response will be available for reading at a later
        time. This is useful for sending events that do not require a response. When `True`, a
        `RuntimeError` will be raised if the response `"success"` field is not `True`.
      data: The event arguments, which must be serializable by `json.dumps`.

    Returns:
      The response from the browser, if `wait_for_response` is `True`, otherwise `None`.
    """

    if data is None:
      data = {}

    device_name = self.devices[device] if device is not None else None
    serialized_data, id_ = self._assemble_command(event=event, data=data, device_name=device_name)
    self._sent_messages.append(serialized_data)

    # Run and save if the websocket connection has been established, otherwise just save.
    if wait_for_response and not self.has_connection():
      raise RuntimeError("Cannot wait for response when no websocket connection is established.")

    if self.has_connection():
      # TODO: why not just await self.websocket.send(serialized_data)?
      asyncio.run_coroutine_threadsafe(self.websocket.send(serialized_data), self.loop)

      if wait_for_response:
        while True:
          if len(self.received) > 0:
            message = self.received.pop()
            if "id" in message and message["id"] == id_:
              break
          time.sleep(0.1)

        if not message["success"]:
          error = message.get("error", "unknown error")
          raise RuntimeError(f"Error during event {event}: " + error)

        return message

    return None

  async def _replay(self):
    """ Send all sent messages.

    This is called when the websocket connection is established.
    """

    for message in self._sent_messages:
      asyncio.run_coroutine_threadsafe(self.websocket.send(message), self.loop)

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
    """ Start the simulation.

    Sets up the file and websocket servers. These will run in a separate thread.
    """

    if self.setup_finished:
      raise RuntimeError("The simulation has already been started.")

    await self._run_ws_server()
    self._run_file_server()
    self.setup_finished = True

  async def _run_ws_server(self):
    """ Start the websocket server.

    Sets up the websocket server. This will run in a separate thread.
    """

    if not HAS_WEBSOCKETS:
      raise RuntimeError("The simulator requires websockets to be installed.")

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
    path = os.path.join(dirname, "simulator")
    if not os.path.exists(path):
      raise RuntimeError("Could not find simulation files. Please run from the root of the "
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

    self._fst = threading.Thread(name="simulation_fs", target=start_server, daemon=True)
    self.fst.start()

    if self.open_browser:
      webbrowser.open(f"http://{self.fs_host}:{self.fs_port}")

  async def stop(self):
    """ Stop the simulator.

    Raises:
      RuntimeError: If the simulation has not been started.
    """

    self.devices = {}
    self.root_resource = None

    # -- file server --
    # Stop the file server.
    self.httpd.shutdown()
    self.httpd.server_close()

    # Clear all relevant attributes.
    self._httpd = None
    self._fst = None

    # -- websocket --
    if self.loop is None:
      raise RuntimeError("Cannot stop simulation when it has not been started.")

    # send stop event to the browser
    await self.send_command("stop", wait_for_response=False)

    # must be thread safe, because event loop is running in a separate thread
    self.loop.call_soon_threadsafe(self.stop_.set_result, "done")

    # Clear all relevant attributes.
    self._sent_messages.clear()
    self.received.clear()
    self._websocket = None
    self._loop = None
    self._t = None
    self._stop_ = None

    self.setup_finished = False

  def _send_resource_assigned_event(self, resource: Resource):
    """ Send a resource assigned event to the browser. """
    data = {
      "resource": resource.serialize(),
      "parent_name": (resource.parent.name if resource.parent else None)
    }
    fut = self.send_command(
      event="resource_assigned",
      data=data,
      device=None,
      wait_for_response=False)
    asyncio.run_coroutine_threadsafe(fut, self.loop)

  def _send_resource_unassigned_event(self, resource: Resource):
    """ Send a resource unassigned event to the browser. """
    data = {
      "resource": resource.serialize(),
      "parent_name": (resource.parent.name if resource.parent else None)
    }
    fut = self.send_command(
      event="resource_unassigned",
      data=data,
      device=None,
      wait_for_response=False)
    asyncio.run_coroutine_threadsafe(fut, self.loop)

  async def set_root_resource(self, resource: Resource) -> None:
    """ Set the root resource of the simulation.

    The root resource might be a liquid handler or another device, or a higher level resources that
    contains devices (e.g. a lab).

    Args:
      resource: The root resource.
    """

    if self.root_resource is not None:
      raise RuntimeError("The root resource has already been set.")

    self.root_resource = resource
    await self.send_command("set_root_resource", {"resource": resource.serialize()})

    # TODO: this should be done by traversing the tree, not just for the root node.
    if isinstance(resource, Machine):
      self.devices[resource.backend] = resource.name

    # TODO: traverse tree and save a link to each Machine in the tree.

    # Hook into the resource (un)assigned callbacks so we can send the appropriate events to the
    # browser.
    resource.register_did_assign_resource_callback(self._send_resource_assigned_event)
    resource.register_did_unassign_resource_callback(self._send_resource_unassigned_event)

    # TODO: add a callback for catching machines and adding them to the simulator

  # --- Some methods for editing the deck state ---

  async def adjust_wells_liquids(
    self,
    plate: Plate,
    liquids: List[List[Tuple[Optional["Liquid"], float]]]
  ):
    """ Fill all wells in a plate with the same mix of liquids (**simulator only**).

    Simulator method to fill a resource with liquid, for testing of liquid handling.

    Args:
      plate: The plate to fill.
      liquids: The liquids to fill the wells with. Liquids are specified as a list of tuples of
        (liquid, volume). Unspecified liquids are represented as `None`. The bottom liquid should
        be the first in the inner list. The outer list contains the wells, starting from the top
        left and going down, then right.
    """

    serialized_pattern = []

    if len(liquids) != plate.num_items:
      raise ValueError("The number of wells in the plate does not match the number of liquids.")

    for well, well_liquids in zip(plate.get_all_items(), liquids):
      serialized_pattern.append({
        "well_name": well.name,
        "liquids": [
          {
            "liquid": liquid.name if liquid is not None else None,
            "volume": volume
          } for liquid, volume in well_liquids]
      })

    await self.send_command(
      device=None,
      event="adjust_well_liquids",
      data={"pattern": serialized_pattern}
    )

  async def adjust_container_liquids(
    self,
    container: Container,
    liquids: List[Tuple[Liquid, float]]
  ):
    """ Fill a container with the specified liquids (**simulator only**).

    Simulator method to fill a resource with liquid, for testing of liquid handling.

    Args:
      container: The container to fill.
      liquids: The liquids to fill the container with. Liquids are specified as a list of tuples of
        (liquid, volume).
    """

    serialized_liquids = [
      {"liquid": liquid.name if liquid is not None else None, "volume": volume}
      for liquid, volume in liquids]

    await self.send_command(event="adjust_container_liquids", data={
      "liquids": serialized_liquids,
      "resource_name": container.name
    }, device=None)

  async def edit_tips(self, tip_rack: TipRack, pattern: List[List[bool]]):
    """ Place and/or remove tips on the robot (**simulator only**).

    Simulator method to place tips on the robot, for testing of tip pickup/dropping. Unlike,
    :func:`~Simulator.pick_up_tips`, this method does not raise an exception if tips are already
    present on the specified locations.

    Args:
      resource: The resource to place tips in.
      pattern: A list of lists of places where to place a tip. TipRack will be removed from the
        resource where the pattern is `False`.
    """

    serialized_pattern = []

    for i, row in enumerate(pattern):
      for j, has_one in enumerate(row):
        idx = i + j * 8
        tip = tip_rack.get_item(idx)
        serialized_pattern.append({
          "tip": tip.serialize(),
          "has_one": has_one
        })

    await self.send_command(event="edit_tips", data={"pattern": serialized_pattern}, device=None)

  async def fill_tip_rack(self, resource: TipRack):
    """ Completely fill a :class:`~pylabrobot.resources.TipRack` resource
    with tips. (**simulator only**).

    Args:
      resource: The resource where all tips should be placed.
    """

    await self.edit_tips(resource, [[True] * 12] * 8)

  async def clear_tips(self, tip_rack: TipRack):
    """ Completely clear a :class:`~pylabrobot.resources.TipRack` resource.
    (**simulator only**).

    Args:
      tip_rack: The resource where all tips should be removed.
    """

    await self.edit_tips(tip_rack, [[True] * 12] * 8)
