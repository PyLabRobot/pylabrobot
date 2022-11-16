import json
from typing import Optional
import urllib.parse

from pylabrobot.liquid_handling.backends import SerializingBackend
from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION

try:
  import requests
  HAS_REQUESTS = True
except ImportError:
  HAS_REQUESTS = False


class HTTPBackend(SerializingBackend):
  """ A backend that sends commands over HTTP(s).

  This backend is used when you want to run a :class:`~pylabrobot.liquid_handling.LiquidHandler`
  locally and have a server communicating with the robot elsewhere.

  .. note::
    This backend is designed to work with
    `the PyLabRobot server <https://github.com/PyLabRobot/PyLabRobot/tree/main/pylabrobot/server>`_.
  """

  def __init__(
    self,
    host: str,
    port: int,
    protocol: str = "http",
    base_path: str = "events",
  ):
    """ Create a new web socket backend.

    Args:
      host: The hostname of the server.
      port: The port of the server.
      protocol: The protocol to use. Either `http` or `https`.
      base_path: The base path of the server. Note that events will be sent to `base_path/<event>`
        where `<event>` is the event identifier, such as `/aspirate`.
    """

    if not HAS_REQUESTS:
      raise RuntimeError("The http backend requires the requests module.")

    super().__init__()
    self._resources = {}
    self.websocket = None

    self.host = host
    self.port = port
    assert protocol in ["http", "https"]
    self.protocol = protocol
    self.base_path = base_path
    self.url = f"{self.protocol}://{self.host}:{self.port}/{self.base_path}"

  def _generate_id(self):
    """ continuously generate unique ids 0 <= x < 10000. """
    self._id += 1
    return f"{self._id % 10000:04}"

  def send_command(self, command: str, data: dict)-> Optional[dict]:
    """ Send an event to the server.

    Args:
      event: The event identifier.
      data: The event arguments, which must be serializable by `json.dumps`.

    Returns:
      The response from the browser, if `wait_for_response` is `True`, otherwise `None`.
    """

    url = urllib.parse.urlparse(self.url, command)

    id_ = self._generate_id()
    data = dict(event=command, id=id_, version=STANDARD_FORM_JSON_VERSION, **data)
    data = json.dumps(data)
    self.session.post(url, data=data)

  def setup(self):
    self.session = requests.Session()
    self._id = 0
    self.send_event(event="setup")

  def stop(self):
    super().stop()
    self.session = None
    self.send_event(event="stop")
