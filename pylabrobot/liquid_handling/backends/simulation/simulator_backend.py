""" Simulator backend """

import http.server
import logging
import os
import threading
from typing import List, Optional, Tuple
import webbrowser

from pylabrobot.liquid_handling.backends import WebSocketBackend
from pylabrobot.liquid_handling.standard import Move
from pylabrobot.resources import Plate, TipRack, Liquid


logger = logging.getLogger(__name__) # TODO: get from somewhere else?


class SimulatorBackend(WebSocketBackend):
  """ Based on the :class:`~pylabrobot.liquid_handling.backends.websocket.WebSocketBackend`,
  the simulator backend can be used to simulate robot methods and inspect the results in a browser.

  You can view the simulation at `http://localhost:1337 <http://localhost:1337>`_, where
  `static/index.html` will be served.

  The websocket server will run at `http://localhost:2121 <http://localhost:2121>`_ by default. If a
  new browser page connects, it will replace the existing connection. All previously sent actions
  will be sent to the new page, with no simualated delay, to ensure that the state of the simulation
  remains the same. This also happens when a browser reloads the page or on the first page load.

  Note that the simulator backend uses
  :class:`~pylabrobot.resources.Resource` 's to locate resources, where eg.
  :class:`~pylabrobot.liquid_handling.backends.hamilton.STAR` uses absolute coordinates.

  .. note::

    See :doc:`/using-the-simulator` for a more complete tutorial.

  Examples:
    Running a simple simulation:

    >>> import pyhamilton.liquid_handling.backends.simulation.simulation as simulation
    >>> from pylabrobot.liquid_handling.liquid_handler import LiquidHandler
    >>> sb = simulation.SimulatorBackend()
    >>> lh = LiquidHandler(backend=sb)
    >>> lh.setup()
    INFO:websockets.server:server listening on 127.0.0.1:2121
    INFO:pyhamilton.liquid_handling.backends.simulation.simulation:Simulation server started at
      http://127.0.0.1:2121
    INFO:pyhamilton.liquid_handling.backends.simulation.simulation:File server started at
      http://127.0.0.1:1337
    >>> lh.edit_tips(tips, pattern=[[True]*12]*8)
    >>> lh.pick_up_tips(tips["A1:H1"])
  """

  def __init__(
    self,
    num_channels: int = 8,
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

    super().__init__(ws_host=ws_host, ws_port=ws_port, num_channels=num_channels)

    self.simulate_delay = simulate_delay
    self.fs_host = fs_host
    self.fs_port = fs_port
    self.open_browser = open_browser

    self._httpd: Optional[http.server.HTTPServer] = None
    self._fst: Optional[threading.Thread] = None

    self._sent_messages = []
    self.received = []

    self.stop_event = None

    self._id = 0

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
    """ Setup the simulation.

    Sets up the websocket server. This will run in a separate thread.
    """

    await super().setup()
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
      og_path = os.getcwd()
      os.chdir(path) # only within thread.

      class QuietSimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
        """ A simple HTTP request handler that does not log requests. """
        def log_message(self, format, *args):
          # pylint: disable=redefined-builtin
          pass

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

      os.chdir(og_path)

    self._fst = threading.Thread(name="simulation_fs", target=start_server, daemon=True)
    self.fst.start()

    if self.open_browser:
      webbrowser.open(f"http://{self.fs_host}:{self.fs_port}")

  async def stop(self):
    """ Stop the simulation. """

    await super().stop()

    # Stop the file server.
    self.httpd.shutdown()
    self.httpd.server_close()

    # Clear all relevant attributes.
    self._httpd = None
    self._fst = None

  async def move_resource(self, move: Move, **backend_kwargs):
    raise NotImplementedError("This method is not implemented in the simulator.")

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

    await self.send_command(command="adjust_well_liquids", data={"pattern": serialized_pattern})

  async def edit_tips(self, tip_rack: TipRack, pattern: List[List[bool]]):
    """ Place and/or remove tips on the robot (**simulator only**).

    Simulator method to place tips on the robot, for testing of tip pickup/droping. Unlike,
    :func:`~Simulator.pick_up_tips`, this method does not raise an exception if tips are already
    present on the specified locations. Note that a
    :class:`~pylabrobot.resources.TipRack` resource has to be assigned
    first.

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

    await self.send_command(command="edit_tips", data={"pattern": serialized_pattern})

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
