"""HTTP io for devices that speak a request/response API, recorded through the capture log.

A device driven over HTTP needs its traffic pinned just like a serial one:
record a known-good run with ``pylabrobot.start_capture()``, then replay it
through :class:`HTTPValidator` to re-run the same protocol with no device
present. Failed exchanges are recorded too, so a device's refusals replay as
the same exception the driver saw live.

:class:`HTTP` is deliberately not an :class:`~pylabrobot.io.io.IOBase`. That
base's ``write`` and ``read`` model a byte stream whose halves are separately
meaningful, while a request and its response are one indivisible exchange, so
the pair is the unit that gets recorded and replayed.

Replaying takes four steps in this order, because building a validator is
refused once validation is active::

    cr = CaptureReader(path)
    io = HTTPValidator(cr, base_url="http://robot:31950")
    cr.start()
    ...  # drive the device through io
    cr.done()  # raises if the protocol stopped short of the recording
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union, cast

from pylabrobot.io.capture import (
  CaptureReader,
  Command,
  capturer,
  get_capture_or_validation_active,
)
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

try:
  import httpx  # type: ignore[import-not-found]

  _HAS_HTTPX = True
except ImportError:
  _HAS_HTTPX = False

logger = logging.getLogger(__name__)

# A parsed JSON body, or the raw text when the device did not answer in JSON.
Body = Union[Dict[str, Any], str]


@dataclass
class HTTPCommand(Command):
  """One recorded exchange: what was asked, and what came back.

  ``status`` is kept so a non-2xx replays as the same refusal rather than
  succeeding with an error body.
  """

  path: str
  request: Optional[Dict[str, Any]]
  response: Body
  status: int

  def __init__(
    self,
    device_id: str,
    action: str,
    path: str,
    response: Body,
    status: int,
    request: Optional[Dict[str, Any]] = None,
    module: str = "http",
  ):
    super().__init__(module=module, device_id=device_id, action=action)
    self.path = path
    self.request = request
    self.response = response
    self.status = status


class HTTP:
  """IO for a JSON-over-HTTP device API.

  The three verbs return the parsed body directly and raise on a non-2xx, so
  callers never handle a response object.
  """

  def __init__(
    self,
    base_url: str,
    timeout: float = 30.0,
    headers: Optional[Dict[str, str]] = None,
  ):
    if not _HAS_HTTPX:
      raise RuntimeError("httpx is required for HTTP io. Install with: pip install httpx")
    self._base_url = base_url.rstrip("/")
    self._timeout = timeout
    self._headers = dict(headers or {})
    self._client: Optional["httpx.AsyncClient"] = None

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new HTTP object while capture or validation is active")

  @property
  def base_url(self) -> str:
    return self._base_url

  async def setup(self):
    self._client = httpx.AsyncClient(
      base_url=self._base_url,
      timeout=self._timeout,
      headers=self._headers,
    )

  async def stop(self):
    if self._client is None:
      return
    await self._client.aclose()
    self._client = None

  def serialize(self):
    return {
      "base_url": self._base_url,
      "timeout": self._timeout,
      "headers": self._headers,
      "type": "HTTP",
    }

  def _client_or_raise(self) -> "httpx.AsyncClient":
    if self._client is None:
      raise RuntimeError(f"HTTP io for '{self._base_url}' not set up; call setup() first")
    return self._client

  async def get(self, path: str) -> Body:
    return self._finish("get", path, None, await self._client_or_raise().get(path))

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Body:
    body = json or {}
    return self._finish("post", path, body, await self._client_or_raise().post(path, json=body))

  async def delete(self, path: str) -> Body:
    return self._finish("delete", path, None, await self._client_or_raise().delete(path))

  def _finish(
    self,
    action: str,
    path: str,
    request: Optional[Dict[str, Any]],
    response: "httpx.Response",
  ) -> Body:
    """Record the exchange, then let a non-2xx raise.

    Recording precedes raising so a device's refusals land in the capture file;
    they are the shapes a driver's error handling is written against.
    """
    parsed = _parse_body(response)
    logger.log(
      LOG_LEVEL_IO,
      "[%s] %s %s %s -> %s %s",
      self._base_url,
      action,
      path,
      request,
      response.status_code,
      parsed,
    )
    capturer.record(
      HTTPCommand(
        device_id=self._base_url,
        action=action,
        path=path,
        request=request,
        response=parsed,
        status=response.status_code,
      )
    )
    if response.status_code >= 400:
      raise _status_error(action, self._base_url, path, response.status_code, parsed)
    return parsed


class HTTPValidator(HTTP):
  """Replays a capture file instead of reaching the device."""

  def __init__(
    self,
    cr: CaptureReader,
    base_url: str,
    timeout: float = 30.0,
    headers: Optional[Dict[str, str]] = None,
  ):
    super().__init__(base_url=base_url, timeout=timeout, headers=headers)
    self.cr = cr

  async def setup(self):
    """No connection to open."""

  async def stop(self):
    """No connection to close."""

  async def get(self, path: str) -> Body:
    return self._replay("get", path, None)

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Body:
    return self._replay("post", path, json or {})

  async def delete(self, path: str) -> Body:
    return self._replay("delete", path, None)

  def _replay(self, action: str, path: str, request: Optional[Dict[str, Any]]) -> Body:
    recorded = HTTPCommand(**self.cr.next_command())
    if not (
      recorded.module == "http"
      and recorded.device_id == self._base_url
      and recorded.action == action
      and recorded.path == path
    ):
      raise ValidationError(
        f"Expected http {action} {path} to {self._base_url}, "
        f"got {recorded.module} {recorded.action} {recorded.path} to {recorded.device_id}"
      )
    if recorded.request != request:
      align_sequences(expected=_pretty(recorded.request), actual=_pretty(request))
      raise ValidationError(
        f"Request body mismatch on {action} {path}: difference was written to stdout."
      )
    if recorded.status >= 400:
      raise _status_error(action, self._base_url, path, recorded.status, recorded.response)
    return recorded.response


def _parse_body(response: "httpx.Response") -> Body:
  try:
    return cast(Dict[str, Any], response.json())
  except ValueError:
    return response.text


def _status_error(
  action: str, base_url: str, path: str, status: int, body: Body
) -> "httpx.HTTPStatusError":
  """The refusal a caller sees, built identically whether live or replayed."""
  rebuilt = (
    httpx.Response(status, text=body)
    if isinstance(body, str)
    else httpx.Response(status, json=body)
  )
  return httpx.HTTPStatusError(
    f"{status} from {action} {path}: {body}",
    request=httpx.Request(action.upper(), base_url + path),
    response=rebuilt,
  )


def _pretty(body: Optional[Dict[str, Any]]) -> str:
  return json.dumps(body, indent=2, sort_keys=True, default=str)
