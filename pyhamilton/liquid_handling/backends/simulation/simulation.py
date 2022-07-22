""" Simulation """

import asyncio
from contextlib import suppress
import http.server
import json
import logging
import os
import threading
import time
import typing
import webbrowser

try:
  import websockets
  HAS_WEBSOCKETS = True
except ImportError:
  print("Simulator requires websockets.") # TODO: change to runtime error.
  HAS_WEBSOCKETS = False

from pyhamilton.liquid_handling.backends import LiquidHandlerBackend
from pyhamilton.liquid_handling.resources.abstract import Resource


logger = logging.getLogger(__name__) # TODO: get from somewhere else?


class SimulationBackend(LiquidHandlerBackend):
  """ The simulator backend can be used to simulate robot methods and inspect the results in a
  browser.

  You can view the simulation at `http://localhost:1337 <http://localhost:1337>`_, where
  `static/index.html` will be served.

  The websocket server will run at `http://localhost:2121 <http://localhost:2121>`_ by default. If a
  new browser page connects, it will replace the existing connection. All previously sent actions
  will be sent to the new page, with no simualated delay, to ensure that the state of the simulation
  remains the same. This also happens when a browser reloads the page or on the first page load.

  Note that the simulator backend uses
  :class:`~pyhamilton.liquid_handling.resources.abstract.Resource` 's to locate resources, where eg.
  :class:`~pyhamilton.liquid_handling.backends.hamilton.STAR` uses absolute coordinates.

  Examples:
    Running a simple simulation:

    >>> import pyhamilton.liquid_handling.backends.simulation.simulation as simulation
    >>> from pyhamilton.liquid_handling.liquid_handler import LiquidHandler
    >>> sb = simulation.SimulationBackend()
    >>> lh = pyhamilton.liquid_handling.LiquidHandler(backend=sb)
    >>> lh.setup()
    INFO:websockets.server:server listening on 127.0.0.1:2121
    INFO:pyhamilton.liquid_handling.backends.simulation.simulation:Simulation server started at
      http://127.0.0.1:2121
    INFO:pyhamilton.liquid_handling.backends.simulation.simulation:File server started at
      http://127.0.0.1:1337
    >>> lh.place_tips([[True]*12]*8)
    >>> lh.pickup_tips(locations)
  """

  def __init__(
    self,
    simulate_delay: bool = False,
    ws_host: str = "127.0.0.1",
    ws_port: int = 2121,
    fs_host: str = "127.0.0.1",
    fs_port: int = 1337,
    open_browser: bool = True,
  ):
    """ Create a new simulation backend.

    Args:
      simulate_delay: If `True`, the simulator will simulate the wait times for various actions,
        otherwise actions will be instant.
      ws_host: The hostname of the websocket server.
      ws_port: The port of the websocket server. If this port is in use, the port will be
        incremented until a free port is found.
      fs_host: The hostname of the file server. This is where the simulation will be served.
      fs_port: The port of the file server. If this port is in use, the port will be incremented
        until a free port is found.
      open_browser: If `True`, the simulation will open a browser window when it is started.
    """

    super().__init__()
    self._resources = {}
    self.websocket = None

    self.simulate_delay = simulate_delay
    self.ws_host = ws_host
    self.ws_port = ws_port
    self.fs_host = fs_host
    self.fs_port = fs_port
    self.open_browser = open_browser

    self._sent_messages = []
    self.received = []

    self.stop_event = None

    self._id = 0

  def _generate_id(self):
    """ continuously generate unique ids 0 <= x < 10000. """
    self._id += 1
    return f"{self._id % 10000:04}"

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
      if "event" in data and data["event"] == "ready":
        self.websocket = websocket
        await self._replay()

        # Echo command
        await websocket.send(json.dumps(data))

  def send_event(
    self,
    event: str,
    wait_for_response: bool = True,
    **kwargs
  )-> typing.Optional[dict]:
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

    id_ = self._generate_id()
    data = dict(event=event, id=id_, **kwargs)
    data = json.dumps(data)
    self._sent_messages.append(data)

    # Run and save if the websocket connection has been established, otherwise just save.
    if self.websocket is None and wait_for_response:
      raise ValueError("Cannot wait for response when no websocket connection is established.")

    if self.websocket is not None:
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
          raise ValueError(f"Error during event {event}: " + error)

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

    super().setup()

    async def run_server():
      self.stop_ = self.loop.create_future()
      while True:
        try:
          async with websockets.serve(self._socket_handler, self.ws_host, self.ws_port):
            print("Simulation server started at http://%s:%s", self.ws_host, self.ws_port)
            # logger.info("Simulation server started at http://%s:%s", self.ws_host, self.ws_port)
            await self.stop_
            break
        except asyncio.CancelledError:
          pass
        except OSError:
          # If the port is in use, try the next port.
          self.ws_port += 1

    loop = asyncio.new_event_loop()
    self.t = threading.Thread(target=loop.run_forever)
    self.t.start()
    self.loop = loop

    asyncio.run_coroutine_threadsafe(run_server(), self.loop)

    self._run_file_server()

  def _run_file_server(self):
    """ Start a simple webserver to serve static files. """

    dirname = os.path.dirname(__file__)
    path = os.path.join(dirname, "simulator")
    if not os.path.exists(path):
      raise RuntimeError("Could not find simulation files. Please run from the root of the "
                         "repository.")

    def start_server():
      # try to start the server. If the port is in use, try with another port until it succeeds.
      os.chdir(path) # only within thread.

      class QuietSimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
        """ A simple HTTP request handler that does not log requests. """
        def log_message(self, format, *args):
          pass

      while True:
        try:
          self.httpd = http.server.HTTPServer((self.fs_host, self.fs_port),
            QuietSimpleHTTPRequestHandler)
          logger.info("File server started at http://%s:%s", self.fs_host, self.fs_port)
          break
        except OSError:
          self.fs_port += 1

      self.httpd.serve_forever()

    self.fst = threading.Thread(name="simulation_fs", target=start_server)
    self.fst.setDaemon(True)
    self.fst.start()

    if self.open_browser:
      webbrowser.open(f"http://{self.fs_host}:{self.fs_port}")

  def stop(self):
    """ Stop the simulation. """

    super().stop()

    if self.loop is None:
      raise ValueError("Cannot stop simulation when it has not been started.")

    # send stop event to the browser
    self.send_event("stop", wait_for_response=False)

    # stop server, graceful
    self.stop_.set_result("done")

    async def cancel_handler():
      for task in asyncio.all_tasks(loop=self.loop):
        task.cancel()
        with suppress(asyncio.CancelledError):
          await task

      self.loop.stop()
      lock.release()

    # Lock to prevent the loop from exiting while we're waiting for the cancel to complete.
    lock = threading.Lock()
    lock.acquire() # pylint: disable=consider-using-with

    # Cancel all pending tasks, wait for them to complete, and then stop the loop.
    asyncio.run_coroutine_threadsafe(cancel_handler(), loop=self.loop)

    # While the loop is still running, wait for it to stop.
    while lock.locked():
      pass

    # Stop the file server.
    self.httpd.shutdown()
    self.httpd.server_close()

    # Clear all relevant attributes.
    self._sent_messages.clear()
    self.received.clear()
    self.websocket = None
    self.loop = None
    self.t = None
    self.stop_ = None
    self.httpd = None
    self.fst = None

  def assigned_resource_callback(self, resource):
    self.send_event(event="resource_assigned", resource=resource.serialize(),
      wait_for_response=False)

  def unassigned_resource_callback(self, name):
    self.send_event(event="resource_unassigned", resource_name=name, wait_for_response=False)

  def pickup_tips(
    self,
    resource,
    channel_1, channel_2, channel_3, channel_4, channel_5, channel_6, channel_7, channel_8,
  ):
    channels = {
      "channel_1": channel_1, "channel_2": channel_2, "channel_3": channel_3,
      "channel_4": channel_4, "channel_5": channel_5, "channel_6": channel_6,
      "channel_7": channel_7, "channel_8": channel_8,
    }
    self.send_event(event="pickup_tips", resource=resource.serialize(), channels=channels,
      wait_for_response=True)

  def discard_tips(
    self,
    resource,
    channel_1, channel_2, channel_3, channel_4, channel_5, channel_6, channel_7, channel_8,
  ):
    channels = {
      "channel_1": channel_1, "channel_2": channel_2, "channel_3": channel_3,
      "channel_4": channel_4, "channel_5": channel_5, "channel_6": channel_6,
      "channel_7": channel_7, "channel_8": channel_8,
    }
    self.send_event(event="discard_tips", resource=resource.serialize(), channels=channels,
      wait_for_response=True)

  def aspirate(
    self,
    resource,
    channel_1, channel_2, channel_3, channel_4, channel_5, channel_6, channel_7, channel_8,
  ):
    # Serialize channels.
    channels = {}
    for i, channel in enumerate([channel_1, channel_2, channel_3, channel_4,
                                 channel_5, channel_6, channel_7, channel_8]):
      if channel is not None:
        channels[f"channel_{i+1}"] = channel.serialize()
    self.send_event(event="aspirate", resource=resource.serialize(), channels=channels,
      wait_for_response=True)

  def dispense(
    self,
    resource,
    channel_1, channel_2, channel_3, channel_4, channel_5, channel_6, channel_7, channel_8,
  ):
    # Serialize channels.
    channels = {}
    for i, channel in enumerate([channel_1, channel_2, channel_3, channel_4,
                                 channel_5, channel_6, channel_7, channel_8]):
      if channel is not None:
        channels[f"channel_{i+1}"] = channel.serialize()
    self.send_event(event="dispense", resource=resource.serialize(), channels=channels,
      wait_for_response=True)

  def pickup_tips96(self, resource):
    self.send_event(event="pickup_tips96", resource=resource.serialize(), wait_for_response=True)

  def discard_tips96(self, resource):
    self.send_event(event="discard_tips96", resource=resource.serialize(),
      wait_for_response=True)

  def aspirate96(self, resource, pattern, volume):
    pattern = [[(volume if p else 0) for p in pattern[i]] for i in range(len(pattern))]
    self.send_event(event="aspirate96", resource=resource.serialize(), pattern=pattern,
      volume=volume, wait_for_response=True)

  def dispense96(self, resource, pattern, volume):
    pattern = [[volume if p else 0 for p in pattern[i]] for i in range(len(pattern))]
    self.send_event(event="dispense96", resource=resource.serialize(), pattern=pattern,
      volume=volume, wait_for_response=True)

  # -------------- Simulator only methods --------------

  def adjust_well_volume(self, resource: Resource, pattern: typing.List[typing.List[float]]):
    """ Fill a resource with liquid (**simulator only**).

    Simulator method to fill a resource with liquid, for testing of liquid handling.

    Args:
      resource: The resource to fill.
      pattern: A list of lists of liquid volumes to fill the resource with.

    Raises:
      RuntimeError: if this method is called before :func:`~setup`.
    """

    # Check if set up has been run, else raise a ValueError.
    if not self.setup_finished:
      raise RuntimeError("The setup has not been finished.")

    self.send_event(event="adjust_well_volume", resource=resource.serialize(), pattern=pattern,
     wait_for_response=True)

  def place_tips(self, resource: Resource, pattern: typing.List[typing.List[bool]]):
    """ Place tips on the robot (**simulator only**).

    Simulator method to place tips on the robot, for testing of tip pickup/discarding. Unlike,
    :func:`~Simulator.pickup_tips`, this method does not raise an exception if tips are already
    present on the specified locations. Note that a
    :class:`~pyhamilton.liquid_handling.resources.abstract.Tips` resource has to be assigned.

    Args:
      resource: The resource to place tips in.
      pattern: A list of lists of places where to place a tip.

    Raises:
      RuntimeError: if this method is called before :func:`~setup`.
    """

    self.send_event(event="edit_tips", resource=resource.serialize(), pattern=pattern,
      wait_for_response=True)

  def fill_tips(self, resource: Resource):
    """ Completely fill a :class:`~pyhamilton.liquid_handling.resources.abstract.Tips` resource with
    tips. (**simulator only**).

    Args:
      resource: The resource where all tips should be placed.
    """

    self.place_tips(resource, [[True] * 12] * 8)

  def remove_tips(self, resource: Resource, pattern: typing.List[typing.List[bool]]):
    """ Remove tips from the robot (**simulator only**).

    Simulator method to remove tips from the robot, for testing of tip pickup/discarding. Unlike,
    :func:`~Simulator.pickup_tips`, this method does not raise an exception if tips are not
    present on the specified locations. Note that a
    :class:`~pyhamilton.liquid_handling.resources.abstract.Tips` resource has to be assigned.

    Args:
      resource: The resource to remove tips from.
      pattern: A list of lists of places where to remove a tip.

    Raises:
      RuntimeError: if this method is called before :func:`~setup`.
    """

    # Flip each boolean in the 2d array.
    pattern = [[not p for p in pattern[i]] for i in range(len(pattern))]

    self.send_event(event="edit_tips", resource=resource.serialize(), pattern=pattern,
      wait_for_response=True)

  def clear_tips(self, resource: Resource):
    """ Completely clear a :class:`~pyhamilton.liquid_handling.resources.abstract.Tips` resource.
    (**simulator only**).

    Args:
      resource: The resource where all tips should be removed.
    """

    self.remove_tips(resource, [[True] * 12] * 8)
