import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

try:
  import websockets
  import websockets.exceptions
  import websockets.legacy
  import websockets.legacy.server
  import websockets.server
  HAS_WEBSOCKETS = True
except ImportError:
  HAS_WEBSOCKETS = False

from pylabrobot.liquid_handling.backends.serializing_backend import SerializingBackend
from pylabrobot.resources import Resource
from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION

if TYPE_CHECKING:
  import websockets.legacy


logger = logging.getLogger("pylabrobot")


class WebSocketBackend(SerializingBackend):
  """ A backend that hosts a websocket server and sends commands over it. """

  def __init__(
    self,
    num_channels: int,
    ws_host: str = "127.0.0.1",
    ws_port: int = 2121,
  ):
    """ Create a new web socket backend.

    Args:
      ws_host: The hostname of the websocket server.
      ws_port: The port of the websocket server. If this port is in use, the port will be
        incremented until a free port is found.
    """

    if not HAS_WEBSOCKETS:
      raise RuntimeError("The WebSocketBackend requires websockets to be installed.")

    super().__init__(num_channels=num_channels)
    self._websocket: Optional["websockets.legacy.server.WebSocketServerProtocol"] = None
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    self._t: Optional[threading.Thread] = None
    self._stop_: Optional[asyncio.Future] = None

    self.ws_host = ws_host
    self.ws_port = ws_port

    self._sent_messages: List[str] = []
    self.received: List[dict] = []

    self._id = 0

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
    """ The future that is set when the web socket is stopped. """
    if self._stop_ is None:
      raise RuntimeError("Event loop has not been started.")
    return self._stop_

  def _generate_id(self):
    """ continuously generate unique ids 0 <= x < 10000. """
    self._id += 1
    return f"{self._id % 10000:04}"

  async def handle_event(self, event: str, data: dict):
    """ Handle an event from the browser.

    This method is intended to be overridden by subclasses. Be sure to call the superclass if you
    want to preserve the default behavior.

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

  def _assemble_command(self, event: str, data) -> Tuple[str, str]:
    """ Assemble a command into standard JSON form. """
    id_ = self._generate_id()
    command_data = {"event": event, "id": id_, "version": STANDARD_FORM_JSON_VERSION, **data}
    return json.dumps(command_data), id_

  def has_connection(self) -> bool:
    """ Return `True` if a websocket connection has been established. """
    # Since the websocket connection is saved in self.websocket, we can just check if it is `None`.
    return self._websocket is not None

  def wait_for_connection(self):
    """ Wait for a websocket connection to be established.

    This method will block until a websocket connection is established. It is not required to wait,
    since :meth:`~WebSocketBackend.send_event` automatically save messages until a connection is
    established, but only if its `wait_for_response` is `False`.
    """

    while not self.has_connection():
      time.sleep(0.1)

  async def assigned_resource_callback(self, resource: Resource):
    # override SerializingBackend so we don't wait for a response
    await self.send_command(command="resource_assigned", data={
        "resource": resource.serialize(),
        "parent_name": (resource.parent.name if resource.parent else None)
      },
      wait_for_response=False)

  async def unassigned_resource_callback(self, name: str):
    # override SerializingBackend so we don't wait for a response
    await self.send_command(command="resource_unassigned", data={"resource_name": name,
      "wait_for_response": False})

  async def send_command(
    self,
    command: str,
    data: Optional[Dict[str, Any]] = None,
    wait_for_response: bool = True,
  )-> Optional[dict]:
    """ Send an event to the browser.

    If a websocket connection has not been established, the event will be saved and sent when it is
    established.

    Args:
      event: The event identifier.
      wait_for_response: If `True`, the web socker backend will wait for a response from the
        browser . If `False`, it is not guaranteed that the response will be available for reading
        at a later time. This is useful for sending events that do not require a response. When
        `True`, a `ValueError` will be raised if the response `"success"` field is not `True`.
      data: The event arguments, which must be serializable by `json.dumps`.

    Returns:
      The response from the browser, if `wait_for_response` is `True`, otherwise `None`.
    """

    if data is None:
      data = {}

    serialized_data, id_ = self._assemble_command(command, data)
    self._sent_messages.append(serialized_data)

    # Run and save if the websocket connection has been established, otherwise just save.
    if wait_for_response and not self.has_connection():
      raise ValueError("Cannot wait for response when no websocket connection is established.")

    if self.has_connection():
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
          raise RuntimeError(f"Error during event {command}: " + error)

        return message

    return None

  async def _replay(self):
    """ Send all sent messages.

    This is called when the websocket connection is established.
    """

    for message in self._sent_messages:
      asyncio.run_coroutine_threadsafe(self.websocket.send(message), self.loop)

  async def setup(self):
    """ Start the websocket server. This will run in a separate thread. """

    if not HAS_WEBSOCKETS:
      raise RuntimeError("The WebSocketBackend requires websockets to be installed.")

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

    self.setup_finished = True

  async def stop(self):
    """ Stop the web socket server. """

    if self.has_connection():
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
