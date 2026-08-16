import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any, Dict, Mapping, Optional

from pylabrobot.io.capture import Command, capturer, get_capture_or_validation_active
from pylabrobot.io.validation_utils import LOG_LEVEL_IO

logger = logging.getLogger(__name__)


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
  """One JSON HTTP request and its decoded response."""

  path: str
  request: Optional[str]
  response: str

  def __init__(
    self,
    device_id: str,
    method: str,
    path: str,
    request: Optional[str],
    response: str,
  ):
    super().__init__(module="http", device_id=device_id, action=method)
    self.path = path
    self.request = request
    self.response = response


class HTTP:
  """Asynchronous JSON-over-HTTP transport.

  The standard-library HTTP client is blocking, so requests run on a private
  single-thread executor. The executor and a request lock keep a device's
  request/response stream ordered without blocking the asyncio event loop.
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
    self._request_lock = asyncio.Lock()

  async def setup(self) -> None:
    if self._executor is None:
      self._executor = ThreadPoolExecutor(max_workers=1)

  async def stop(self) -> None:
    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  def _make_request(
    self,
    method: str,
    path: str,
    data: Optional[Dict[str, Any]],
  ) -> Dict[str, Any]:
    url = urllib.parse.urljoin(f"{self.base_url}/", path.lstrip("/"))
    headers = self.headers.copy()
    body = None
    if data is not None:
      body = json.dumps(data).encode("utf-8")
      headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, headers=headers, data=body, method=method)
    try:
      with urllib.request.urlopen(request, timeout=self.timeout) as response:
        response_body = response.read()
    except urllib.error.HTTPError as error:
      error_body = error.read().decode("utf-8", errors="replace")
      raise HTTPError(method, url, error.code, error_body) from error

    if response_body == b"":
      return {}
    return dict(json.loads(response_body.decode("utf-8")))

  async def request(
    self,
    method: str,
    path: str,
    data: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    """Send a JSON request and return the decoded JSON object."""
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

    async with self._request_lock:
      loop = asyncio.get_running_loop()
      response = await loop.run_in_executor(
        self._executor,
        partial(self._make_request, normalized_method, path, data),
      )

    response_json = json.dumps(response, sort_keys=True)
    logger.log(LOG_LEVEL_IO, "[%s] response %s", self.base_url, response_json)
    capturer.record(
      HTTPCommand(
        device_id=self.base_url,
        method=normalized_method,
        path=path,
        request=request_json,
        response=response_json,
      )
    )
    return response
