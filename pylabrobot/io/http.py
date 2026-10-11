"""HTTP io for devices that speak a request/response API, recorded through the capture log.

A device driven over HTTP needs its traffic pinned just like a serial one:
record a known-good run with ``pylabrobot.start_capture()``, then replay it
through :class:`HTTPValidator` to re-run the same protocol with no device
present. Failed exchanges are recorded too, so a device's refusals replay as
the same :class:`HTTPError` the driver saw live.

:class:`HTTP` is deliberately not an :class:`~pylabrobot.io.io.IOBase`. That
base's ``write`` and ``read`` model a byte stream whose halves are separately
meaningful, while a request and its response are one indivisible exchange, so
the pair is the unit that gets recorded and replayed.

Replaying takes four steps in this order, because building a validator is
refused once validation is active::

    cr = CaptureReader(path)
    io = HTTPValidator(cr, "device", base_url="http://robot:31950")
    cr.start()
    ...  # drive the device through io
    cr.done()  # raises if the protocol stopped short of the recording
"""

import asyncio
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any, Dict, Mapping, Optional, Tuple

from pylabrobot.io.capture import (
  CaptureReader,
  Command,
  capturer,
  get_capture_or_validation_active,
)
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

logger = logging.getLogger(__name__)


def _origin(url: str) -> tuple[str, Optional[str], Optional[int]]:
  """Compare origins with explicit and implicit default ports treated equally."""
  parsed = urllib.parse.urlsplit(url)
  port = parsed.port or {"http": 80, "https": 443}.get(parsed.scheme)
  return parsed.scheme, parsed.hostname, port


@dataclass(frozen=True)
class HTTPResponse:
  """HTTP status, lower-case response headers, and the unmodified response body."""

  status: int
  body: bytes
  headers: Dict[str, str]

  def text(self) -> str:
    """Decode a text response using its declared charset, defaulting to UTF-8."""
    from email.message import Message

    message = Message()
    message["Content-Type"] = self.headers.get("content-type", "")
    return self.body.decode(message.get_content_charset() or "utf-8", errors="replace")


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
  """Control redirects without forwarding device credentials to another origin."""

  def __init__(self, allow_redirects: bool):
    self.allow_redirects = allow_redirects

  def redirect_request(self, req, fp, code, msg, headers, newurl):
    if not self.allow_redirects:
      return None
    if _origin(req.full_url) != _origin(newurl):
      raise ValueError("HTTP redirect must stay on the device's origin")
    return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class HTTPRawCommand(Command):
  """A raw HTTP exchange; binary bodies are base64 encoded for capture."""

  path: str
  request_base64: Optional[str]
  status: int
  response_base64: str


class HTTPError(RuntimeError):
  """An HTTP response outside the 2xx range."""

  def __init__(self, method: str, url: str, status: int, body: str):
    self.method = method
    self.url = url
    self.status = status
    self.body = body
    super().__init__(f"{method} {url} returned HTTP {status}: {body}")


@dataclass
class HTTPCommand(Command):
  """One recorded exchange: what was asked, and what came back.

  ``request`` and ``response`` are JSON strings (the response is the raw body
  text on a non-2xx). ``status`` is kept so a non-2xx replays as the same
  refusal rather than succeeding with an error body. The HTTP method is stored
  in the capture format's ``action`` field.
  """

  path: str
  request: Optional[str]
  response: str
  status: int

  def __init__(
    self,
    device_id: str,
    method: str,
    path: str,
    response: str,
    status: int,
    request: Optional[str] = None,
    module: str = "http",
  ):
    super().__init__(module=module, device_id=device_id, action=method)
    self.path = path
    self.request = request
    self.response = response
    self.status = status


class HTTP:
  """Asynchronous HTTP transport for JSON, text, and binary responses.

  The standard-library HTTP client is blocking, so requests run on a private
  single-thread executor. The executor and a request lock keep a device's
  request/response stream ordered without blocking the asyncio event loop.

  Every exchange is recorded to the capture log — successes and refusals both,
  so a driver's error handling replays against the shapes it saw live.
  """

  def __init__(
    self,
    human_readable_device_name: str,
    base_url: str,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 30.0,
  ):
    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new HTTP object while capture or validation is active")
    if timeout <= 0:
      raise ValueError("timeout must be greater than zero")

    self.human_readable_device_name = human_readable_device_name
    self.base_url = base_url.rstrip("/")
    self.headers = dict(headers or {})
    self.timeout = timeout
    self._executor: Optional[ThreadPoolExecutor] = None
    # Built by `setup`, not here. An asyncio primitive binds to a loop when it is constructed, and
    # a transport is built in ordinary synchronous code - where on Python 3.9 there may be no loop
    # to bind to, and on any version there may be a different one than the requests will run in.
    # Nothing needs it before `setup`, which is also where the executor comes from.
    self._request_lock: Optional[asyncio.Lock] = None

  def _require_lock(self) -> asyncio.Lock:
    """The lock that orders requests on this transport.

    Returns:
      The lock, which `setup` built.

    Raises:
      RuntimeError: If the transport was never set up, which is also what leaves it without one.
    """
    if self._request_lock is None:
      raise RuntimeError(
        f"HTTP transport for '{self.human_readable_device_name}' is not set up; call setup() first"
      )
    return self._request_lock

  async def setup(self) -> None:
    if self._executor is None:
      self._executor = ThreadPoolExecutor(max_workers=1)
    if self._request_lock is None:
      self._request_lock = asyncio.Lock()

  async def stop(self) -> None:
    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  def _url(self, path: str) -> str:
    return urllib.parse.urljoin(f"{self.base_url}/", path.lstrip("/"))

  def _make_request(
    self,
    method: str,
    path: str,
    data: Optional[Dict[str, Any]],
  ) -> Tuple[int, str]:
    """Perform the blocking request, returning ``(status, body_text)``.

    A non-2xx does not raise here: its status and body are returned so the
    caller can record the refusal before raising it. Connection-level failures
    (``URLError`` that is not an ``HTTPError``) propagate.
    """
    url = self._url(path)
    headers = self.headers.copy()
    body = None
    if data is not None:
      body = json.dumps(data).encode("utf-8")
      headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, headers=headers, data=body, method=method)
    try:
      with urllib.request.urlopen(request, timeout=self.timeout) as response:
        return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
      return error.code, error.read().decode("utf-8", errors="replace")

  async def request(
    self,
    method: str,
    path: str,
    data: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    """Send a JSON request and return the decoded JSON object, raising on non-2xx."""
    if self._executor is None:
      raise RuntimeError(
        f"HTTP transport for '{self.human_readable_device_name}' is not set up; call setup() first"
      )

    normalized_method = method.upper()
    request_json = json.dumps(data, sort_keys=True) if data is not None else None
    logger.log(
      LOG_LEVEL_IO,
      "[%s] %s %s %s",
      self.base_url,
      normalized_method,
      path,
      request_json or "",
    )

    async with self._require_lock():
      loop = asyncio.get_running_loop()
      status, body_text = await loop.run_in_executor(
        self._executor,
        partial(self._make_request, normalized_method, path, data),
      )

    # Record the exchange before a refusal raises, so the capture holds the
    # failed shapes a driver's error handling is written against.
    if status >= 400:
      capturer.record(
        HTTPCommand(
          device_id=self.base_url,
          method=normalized_method,
          path=path,
          request=request_json,
          response=body_text,
          status=status,
        )
      )
      logger.log(LOG_LEVEL_IO, "[%s] response %s %s", self.base_url, status, body_text)
      raise HTTPError(normalized_method, self._url(path), status, body_text)

    response = {} if body_text == "" else dict(json.loads(body_text))
    response_json = json.dumps(response, sort_keys=True)
    logger.log(LOG_LEVEL_IO, "[%s] response %s", self.base_url, response_json)
    capturer.record(
      HTTPCommand(
        device_id=self.base_url,
        method=normalized_method,
        path=path,
        request=request_json,
        response=response_json,
        status=status,
      )
    )
    return response

  def _make_raw_request(
    self,
    method: str,
    path: str,
    body: Optional[bytes],
    headers: Mapping[str, str],
    allow_redirects: bool,
  ) -> HTTPResponse:
    """Send bytes on the worker thread, retaining non-2xx responses for the caller."""
    url = urllib.parse.urljoin(f"{self.base_url}/", path)
    if _origin(self.base_url) != _origin(url):
      raise ValueError("HTTP request must stay on the device's origin")
    request = urllib.request.Request(
      url, data=body, headers={**self.headers, **headers}, method=method
    )
    opener = urllib.request.build_opener(_RedirectHandler(allow_redirects))
    try:
      response = opener.open(request, timeout=self.timeout)
    except urllib.error.HTTPError as error:
      response = error
    with response:
      return HTTPResponse(
        status=response.code,
        body=response.read(),
        headers={key.lower(): value for key, value in response.headers.items()},
      )

  async def request_raw(
    self,
    method: str,
    path: str,
    body: Optional[bytes] = None,
    *,
    headers: Optional[Mapping[str, str]] = None,
    allow_redirects: bool = True,
  ) -> HTTPResponse:
    """Send raw bytes and return status, headers, and body without HTTP-status exceptions.

    Requests are ordered with JSON requests on the same transport. No requests are retried.
    Authentication headers are excluded from logs and captures.
    """
    if self._executor is None:
      raise RuntimeError(
        f"HTTP transport for '{self.human_readable_device_name}' is not set up; call setup() first"
      )
    method = method.upper()
    async with self._require_lock():
      loop = asyncio.get_running_loop()
      response = await loop.run_in_executor(
        self._executor,
        partial(self._make_raw_request, method, path, body, headers or {}, allow_redirects),
      )
      if capturer.capture_active:
        command = await loop.run_in_executor(
          self._executor, partial(self._encode_raw_capture, method, path, body, response)
        )
        capturer.record(command)
    logger.log(
      LOG_LEVEL_IO,
      "[%s] %s %s: HTTP %d, %d bytes",
      self.base_url,
      method,
      path,
      response.status,
      len(response.body),
    )
    return response

  def _encode_raw_capture(
    self, method: str, path: str, body: Optional[bytes], response: HTTPResponse
  ) -> HTTPRawCommand:
    """Encode binary capture payloads on the transport's worker thread."""
    return HTTPRawCommand(
      module="http",
      device_id=self.base_url,
      action=method,
      path=path,
      request_base64=base64.b64encode(body).decode("ascii") if body is not None else None,
      status=response.status,
      response_base64=base64.b64encode(response.body).decode("ascii"),
    )


class HTTPValidator(HTTP):
  """Replays a capture file instead of reaching the device.

  Overrides the single :meth:`request` seam: each call consumes the next
  recorded exchange, refuses on a method/path/request-body mismatch, and
  re-raises a recorded non-2xx as the same :class:`HTTPError`.
  """

  def __init__(
    self,
    cr: CaptureReader,
    human_readable_device_name: str,
    base_url: str,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 30.0,
  ):
    super().__init__(
      human_readable_device_name=human_readable_device_name,
      base_url=base_url,
      headers=headers,
      timeout=timeout,
    )
    self.cr = cr

  async def setup(self) -> None:
    """No connection to open."""

  async def stop(self) -> None:
    """No connection to close."""

  async def request(
    self,
    method: str,
    path: str,
    data: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    normalized_method = method.upper()
    request_json = json.dumps(data, sort_keys=True) if data is not None else None
    command = self.cr.next_command()
    command["method"] = command.pop("action")
    recorded = HTTPCommand(**command)
    if not (
      recorded.module == "http"
      and recorded.device_id == self.base_url
      and recorded.action == normalized_method
      and recorded.path == path
    ):
      raise ValidationError(
        f"Expected http {normalized_method} {path} to {self.base_url}, "
        f"got {recorded.module} {recorded.action} {recorded.path} to {recorded.device_id}"
      )
    if recorded.request != request_json:
      align_sequences(expected=recorded.request or "", actual=request_json or "")
      raise ValidationError(
        f"Request body mismatch on {normalized_method} {path}: difference was written to stdout."
      )
    if recorded.status >= 400:
      raise HTTPError(normalized_method, self._url(path), recorded.status, recorded.response)
    return {} if recorded.response == "" else dict(json.loads(recorded.response))
