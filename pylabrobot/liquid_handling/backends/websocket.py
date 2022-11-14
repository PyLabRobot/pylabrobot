import asyncio
import json
import logging
import threading
import time
from typing import Optional

try:
  import websockets
  HAS_WEBSOCKETS = True
except ImportError:
  HAS_WEBSOCKETS = False

from pylabrobot.liquid_handling.backends import SerializingBackend
from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION


logger = logging.getLogger(__name__) # TODO: get from somewhere else?


class WebSocketBackend(SerializingBackend):
  """ A backend that hosts a websocket server and sends commands over it. """

  def __init__(
    self,
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
      raise RuntimeError("The simulator requires websockets to be installed.")

    super().__init__()
    self._resources = {}
    self.websocket = None

    self.ws_host = ws_host
    self.ws_port = ws_port

    self._sent_messages = []
    self.received = []

    self.stop_event = None

    self._id = 0

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

  async def _socket_handler(self, websocket):
    """ Handle a new websocket connection. Save the websocket connection store received
    messages in `self.received`. """

    while True:
      try:
        message = await websocket.recv()
      except websockets.ConnectionClosed:
        return
      except asyncio.CancelledError:
        return

      data = json.loads(message)
      self.received.append(data)

      # If the event is "ready", then we can save the connection and send the saved messages.
      if data.get("event") == "ready":
        self.websocket = websocket
        await self._replay()

        # Echo command
        await websocket.send(json.dumps(data))

      if "event" in data:
        await self.handle_event(data.get("event"), data)
      else:
        logger.warning("Unhandled message: %s", message)

  def _assemble_command(self, event: str, data) -> str:
    """ Assemble a command into standard JSON form. """
    id_ = self._generate_id()
    command_data = dict(event=event, id=id_, version=STANDARD_FORM_JSON_VERSION, **data)
    return json.dumps(command_data), id_

  def has_connection(self) -> bool:
    """ Return `True` if a websocket connection has been established. """
    # Since the websocket connection is saved in self.websocket, we can just check if it is `None`.
    return self.websocket is not None

  def wait_for_connection(self):
    """ Wait for a websocket connection to be established.

    This method will block until a websocket connection is established. It is not required to wait,
    since :meth:`~WebSocketBackend.send_event` automatically save messages until a connection is
    established, but only if its `wait_for_response` is `False`.
    """

    while not self.has_connection():
      time.sleep(0.1)

  def send_command(
    self,
    command: str,
    data: dict = None,
    wait_for_response: bool = True,
  )-> Optional[dict]:
    """ Send an event to the browser.

    If a websocket connection has not been established, the event will be saved and sent when it is
    established.

    Args:
      event: The event identifier.
      wait_for_response: If `True`, the simulation will wait for a response from the browser. If
        `False`, it is not guaranteed that the response will be available for reading at a later
        time. This is useful for sending events that do not require a response. When `True`, a
        `ValueError` will be raised if the response `"success"` field is not `True`.
      **kwargs: The event arguments, which must be serializable by `json.dumps`.

    Returns:
      The response from the browser, if `wait_for_response` is `True`, otherwise `None`.
    """

    if data is None:
      data = {}

    data, id_ = self._assemble_command(command, data)
    self._sent_messages.append(data)

    # Run and save if the websocket connection has been established, otherwise just save.
    if wait_for_response and not self.has_connection():
      raise ValueError("Cannot wait for response when no websocket connection is established.")

    if self.has_connection():
      asyncio.run_coroutine_threadsafe(self.websocket.send(data), self.loop)

      if wait_for_response:
        while True:
          if len(self.received) > 0:
            message = self.received.pop()
            if "id" in message and message["id"] == id_:
              break
          time.sleep(0.1)

        if not message["success"]:
          error = message.get("error", "unknown error")
          raise ValueError(f"Error during event {command}: " + error)

        return message

  async def _replay(self):
    """ Send all sent messages.

    This is called when the websocket connection is established.
    """

    for message in self._sent_messages:
      asyncio.run_coroutine_threadsafe(self.websocket.send(message), self.loop)

  def setup(self):
    """ Setup the simulation.

    Sets up the websocket server. This will run in a separate thread.
    """

    if not HAS_WEBSOCKETS:
      raise RuntimeError("The simulator requires websockets to be installed.")

    async def run_server():
      self.stop_ = self.loop.create_future()
      while True:
        try:
          async with websockets.serve(self._socket_handler, self.ws_host, self.ws_port):
            print(f"Simulation server started at http://{self.ws_host}:{self.ws_port}")
            # logger.info("Simulation server started at http://%s:%s", self.ws_host, self.ws_port)
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
    self.loop = asyncio.new_event_loop()
    self.t = threading.Thread(target=start_loop)
    self.t.start()

    while lock.locked():
      time.sleep(0.001)

  def stop(self):
    """ Stop the simulation. """

    if self.loop is None:
      raise ValueError("Cannot stop simulation when it has not been started.")

    # send stop event to the browser
    self.send_command("stop", wait_for_response=False)

    # must be thread safe, because event loop is running in a separate thread
    self.loop.call_soon_threadsafe(self.stop_.set_result, "done")

    # Clear all relevant attributes.
    self._sent_messages.clear()
    self.received.clear()
    self.websocket = None
    self.loop = None
    self.t = None
    self.stop_ = None
