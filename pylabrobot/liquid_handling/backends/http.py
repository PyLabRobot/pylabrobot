from typing import Optional, Dict, Any, cast
import urllib.parse

from pylabrobot.liquid_handling.backends.serializing_backend import SerializingBackend
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
    num_channels: int,
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

    super().__init__(num_channels=num_channels)
    self.session: Optional[requests.Session] = None

    self.host = host
    self.port = port
    assert protocol in ["http", "https"]
    self.protocol = protocol
    self.base_path = base_path
    self.url = f"{self.protocol}://{self.host}:{self.port}/{self.base_path}/"

  async def send_command(
    self,
    command: str,
    data: Optional[Dict[str, Any]] = None
  ) -> Optional[dict]:
    """ Send an event to the server.

    Args:
      event: The event identifier.
      data: The event arguments, which must be serializable by `json.dumps`.
    """

    if self.session is None:
      raise RuntimeError("The backend is not running. Did you call `setup()`?")

    command = command.replace("_", "-")
    url = urllib.parse.urljoin(self.url, command)

    resp = self.session.post(
      url,
      json=data,
      headers={
        "User-Agent": f"pylabrobot/{STANDARD_FORM_JSON_VERSION}",
      })
    return cast(dict, resp.json())

  async def setup(self):
    self.session = requests.Session()
    await super().setup()

  async def stop(self):
    await super().stop()
    self.session = None
