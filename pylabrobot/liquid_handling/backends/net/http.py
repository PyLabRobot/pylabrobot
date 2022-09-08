import json
from typing import Optional, List, Union
import urllib.parse

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.resources import (
  Coordinate,
  Lid,
  Plate,
  Resource,
  Tip,
)
from pylabrobot.liquid_handling.standard import (
  Aspiration,
  Dispense,
)
from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION

try:
  import requests
  HAS_REQUESTS = True
except ImportError:
  HAS_REQUESTS = False


class HTTPBackend(LiquidHandlerBackend):
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

  def send_event(
    self,
    event: str,
    **kwargs
  )-> Optional[dict]:
    """ Send an event to the server.

    Args:
      event: The event identifier.
      **kwargs: The event arguments, which must be serializable by `json.dumps`.

    Returns:
      The response from the browser, if `wait_for_response` is `True`, otherwise `None`.
    """

    url = urllib.parse.urlparse(self.url, event)

    id_ = self._generate_id()
    data = dict(event=event, id=id_, version=STANDARD_FORM_JSON_VERSION, **kwargs)
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

  def assigned_resource_callback(self, resource):
    self.send_event(event="resource_assigned", resource=resource.serialize(),
      parent_name=(resource.parent.name if resource.parent else None))

  def unassigned_resource_callback(self, name):
    self.send_event(event="resource_unassigned", resource_name=name)

  def pickup_tips(self, *channels: List[Optional[Tip]]):
    channels = [channel.serialize() if channel is not None else None for channel in channels]
    self.send_event(event="pickup_tips", channels=channels)

  def discard_tips(self, *channels: List[Optional[Tip]]):
    channels = [channel.serialize() if channel is not None else None for channel in channels]
    self.send_event(event="discard_tips", channels=channels)

  def aspirate(self, *channels: Optional[Aspiration]):
    channels = [channel.serialize() for channel in channels]
    self.send_event(event="aspirate", channels=channels)

  def dispense(self, *channels: Optional[Dispense]):
    channels = [channel.serialize() for channel in channels]
    self.send_event(event="dispense", channels=channels)

  def pickup_tips96(self, resource):
    self.send_event(event="pickup_tips96", resource=resource.serialize())

  def discard_tips96(self, resource):
    self.send_event(event="discard_tips96", resource=resource.serialize())

  def aspirate96(self, resource, pattern, volume):
    pattern = [[(volume if p else 0) for p in pattern[i]] for i in range(len(pattern))]
    self.send_event(event="aspirate96", resource=resource.serialize(), pattern=pattern,
      volume=volume)

  def dispense96(self, resource, pattern, volume):
    pattern = [[volume if p else 0 for p in pattern[i]] for i in range(len(pattern))]
    self.send_event(event="dispense96", resource=resource.serialize(), pattern=pattern,
      volume=volume)

  def move_plate(self, plate: Plate, to: Union[Resource, Coordinate], **backend_kwargs):
    self.send_event(event="move_plate", plate=plate.serialize(), to=to.serialize(),
      **backend_kwargs)

  def move_lid(self, lid: Lid, to: Union[Resource, Coordinate], **backend_kwargs):
    self.send_event(event="move_lid", lid=lid.serialize(), to=to.serialize(),
      **backend_kwargs)
