"""LI-COR Odyssey Classic HTTP transport driver.

Generic HTTP transport over Basic Auth for the Odyssey Classic
embedded web server. Vendor-specific protocol — CGI paths, form
encoding, response parsing — lives in the capability backends; this
driver only ships bytes back and forth.

Server: Apache/1.3.27 (Unix) (Red-Hat/Linux) mod_perl/1.23
Auth realm: LICOR-Odyssey
Transport: HTTP over 10/100Base-T Ethernet
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import aiohttp

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver

logger = logging.getLogger(__name__)


# Transport errors worth retrying — connection-level transients only.
RETRYABLE_EXCEPTIONS = (
  aiohttp.ClientConnectionError,
  asyncio.TimeoutError,
  ConnectionResetError,
  OSError,
)
_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_DELAY = 0.25

# Environment variables for credentials.
_CRED_ENV_USER = "ODYSSEY_USER"
_CRED_ENV_PASS = "ODYSSEY_PASS"


class OdysseyDriver(Driver):
  """HTTP transport for the LI-COR Odyssey Classic.

  Wraps an aiohttp.ClientSession with HTTP Basic Auth. Capability
  backends share a single OdysseyDriver instance and call
  :meth:`post` / :meth:`get` / :meth:`get_bytes` to exchange bytes
  with the embedded web server.
  """

  def __init__(
    self,
    host: str,
    username: str,
    password: str,
    port: int = 80,
    timeout: float = 60.0,
  ) -> None:
    super().__init__()
    if not username or not password:
      raise ValueError(
        "OdysseyDriver requires both username and password. "
        f"Use OdysseyDriver.from_env() to read them from "
        f"{_CRED_ENV_USER}/{_CRED_ENV_PASS}."
      )
    self._host = host
    self._port = port
    self._timeout_seconds = timeout
    self._username = username
    self._password = password
    self._base_url = f"http://{host}:{port}"
    self._auth = aiohttp.BasicAuth(username, password)
    self._timeout = aiohttp.ClientTimeout(total=timeout)
    self._session: Optional[aiohttp.ClientSession] = None
    logger.info(
      "OdysseyDriver initialised: host=%s %s=%s",
      host, _CRED_ENV_USER, username,
    )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self._host,
      "port": self._port,
      "timeout": self._timeout_seconds,
    }

  @classmethod
  def from_env(
    cls,
    host: Optional[str] = None,
    port: int = 80,
    timeout: float = 60.0,
  ) -> "OdysseyDriver":
    """Construct from ODYSSEY_USER / ODYSSEY_PASS env vars.

    Raises ValueError if either is missing — the driver does not
    silently fall back to default credentials. If ``host`` is None,
    ODYSSEY_HOST is read from the environment too.
    """
    username = os.environ.get(_CRED_ENV_USER, "")
    password = os.environ.get(_CRED_ENV_PASS, "")
    if not username or not password:
      missing = [
        name for name, val in (
          (_CRED_ENV_USER, username),
          (_CRED_ENV_PASS, password),
        ) if not val
      ]
      raise ValueError(
        f"Missing required environment variable(s): {', '.join(missing)}."
      )
    if host is None:
      host = os.environ.get("ODYSSEY_HOST", "")
      if not host:
        raise ValueError("No host provided and ODYSSEY_HOST is unset.")
    return cls(
      host=host, username=username, password=password,
      port=port, timeout=timeout,
    )

  @property
  def base_url(self) -> str:
    return self._base_url

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Open the HTTP session and verify reachability.

    ``Connection: close`` is forced on every request: the embedded
    Apache 1.3.27 doesn't always handle keep-alive cleanly when a
    second request arrives before the first response is fully
    consumed. Closing per request is a couple of ms slower but
    eliminates the inter-request races we see in the field.

    Auth verification is intentionally NOT done here. Backends
    perform the first auth-protected call; a 401 surfaces there.
    """
    self._session = aiohttp.ClientSession(
      auth=self._auth,
      timeout=self._timeout,
      headers={"Connection": "close"},
    )
    async with self._session.get(self._base_url) as resp:
      if resp.status != 200:
        raise ConnectionError(
          f"Cannot reach Odyssey at {self._base_url} (HTTP {resp.status})"
        )
    logger.info("Connected to Odyssey at %s", self._base_url)

  async def stop(self) -> None:
    """Close the HTTP session."""
    if self._session is not None:
      await self._session.close()
      self._session = None

  def _check_session(self) -> aiohttp.ClientSession:
    if self._session is None:
      raise RuntimeError("OdysseyDriver not set up")
    return self._session

  # -- Generic transport ---------------------------------------------------

  async def post(
    self,
    path: str,
    form_data: Optional[dict[str, str]] = None,
    *,
    allow_redirects: bool = False,
    with_retry: bool = False,
  ) -> tuple[int, str, dict[str, str]]:
    """POST ``form_data`` to ``path``. Returns (status, body, headers)."""
    if with_retry:
      return await self._retry(self._post_once, path, form_data, allow_redirects)
    return await self._post_once(path, form_data, allow_redirects)

  async def _post_once(
    self,
    path: str,
    form_data: Optional[dict[str, str]],
    allow_redirects: bool,
  ) -> tuple[int, str, dict[str, str]]:
    session = self._check_session()
    url = f"{self._base_url}{path}"
    async with session.post(
      url, data=form_data, allow_redirects=allow_redirects
    ) as resp:
      body = await resp.text()
      return resp.status, body, dict(resp.headers)

  async def get(
    self,
    path: str,
    params: Optional[dict[str, str]] = None,
    *,
    allow_redirects: bool = True,
    with_retry: bool = False,
  ) -> tuple[int, str, dict[str, str]]:
    """GET ``path`` with ``params``. Returns (status, body, headers)."""
    if with_retry:
      return await self._retry(self._get_once, path, params, allow_redirects)
    return await self._get_once(path, params, allow_redirects)

  async def _get_once(
    self,
    path: str,
    params: Optional[dict[str, str]],
    allow_redirects: bool,
  ) -> tuple[int, str, dict[str, str]]:
    session = self._check_session()
    url = f"{self._base_url}{path}"
    async with session.get(
      url, params=params, allow_redirects=allow_redirects
    ) as resp:
      body = await resp.text()
      return resp.status, body, dict(resp.headers)

  async def get_bytes(
    self,
    path: str,
    params: Optional[dict[str, str]] = None,
    *,
    with_retry: bool = False,
  ) -> tuple[int, bytes, dict[str, str], Optional[int]]:
    """GET binary content. Returns (status, bytes, headers, content_length).

    ``content_length`` is the server-advertised ``Content-Length``
    header (or None). Truncation checks belong on the caller — this
    method just returns whatever the socket delivered.
    """
    if with_retry:
      return await self._retry_bytes(path, params)
    return await self._get_bytes_once(path, params)

  async def _get_bytes_once(
    self,
    path: str,
    params: Optional[dict[str, str]],
  ) -> tuple[int, bytes, dict[str, str], Optional[int]]:
    session = self._check_session()
    url = f"{self._base_url}{path}"
    async with session.get(url, params=params) as resp:
      data = await resp.read()
      return resp.status, data, dict(resp.headers), resp.content_length

  async def _retry(self, method, *args):
    last_exc: Optional[Exception] = None
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
      try:
        return await method(*args)
      except RETRYABLE_EXCEPTIONS as exc:
        last_exc = exc
        if attempt < _HTTP_RETRY_ATTEMPTS - 1:
          logger.warning(
            "%s attempt %d/%d failed (%s) — retrying",
            method.__name__, attempt + 1, _HTTP_RETRY_ATTEMPTS, exc,
          )
          await asyncio.sleep(_HTTP_RETRY_DELAY)
          continue
    assert last_exc is not None
    raise last_exc

  async def _retry_bytes(self, path, params):
    last_exc: Optional[Exception] = None
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
      try:
        return await self._get_bytes_once(path, params)
      except RETRYABLE_EXCEPTIONS as exc:
        last_exc = exc
        if attempt < _HTTP_RETRY_ATTEMPTS - 1:
          logger.warning(
            "GET bytes %s attempt %d/%d failed (%s) — retrying",
            path, attempt + 1, _HTTP_RETRY_ATTEMPTS, exc,
          )
          await asyncio.sleep(_HTTP_RETRY_DELAY)
          continue
    assert last_exc is not None
    raise last_exc

  # -- Admin / non-capability ---------------------------------------------

  async def shutdown_instrument(self) -> str:
    """Power off the instrument. WARNING: restart can take 30 minutes."""
    logger.warning("Shutting down Odyssey instrument")
    _, body, _ = await self.get(
      "/scanapp/admin/admin/index",
      params={"action": "InitiateShutdown"},
    )
    return body

  async def get_instrument_info(self) -> str:
    """Fetch instrument info page (serial, software version)."""
    _, body, _ = await self.get("/scanapp/help/instinfo.pl")
    return body
