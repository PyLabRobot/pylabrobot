"""HTTP driver for the Micronic Code Reader IO Monitor server."""

from __future__ import annotations

import asyncio
import enum
import http.client
import json
import time
from typing import Any, Optional
from urllib import error, parse, request

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver


class MicronicError(Exception):
  """Raised when the Micronic IO Monitor HTTP server returns an error."""


class MicronicIOMonitorState(enum.Enum):
  """Normalized IO Monitor device state reported by GET /state.

  IO Monitor exposes a single state machine that governs both rack scans and
  single-tube scans, so this enum is shared across all IO Monitor backends.
  """

  IDLE = "idle"
  SCANNING = "scanning"
  DATAREADY = "dataready"


class MicronicIOMonitorDriver(Driver):
  """HTTP transport for the Micronic Code Reader IO Monitor server."""

  def __init__(
    self,
    host: str = "localhost",
    port: int = 2500,
    timeout: float = 60.0,
    user_agent: str = "curl/8.0",
  ):
    super().__init__()
    self.host = host
    self.port = port
    self.timeout = timeout
    self.user_agent = user_agent

  @property
  def base_url(self) -> str:
    return f"http://{self.host}:{self.port}"

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    return None

  async def stop(self) -> None:
    return None

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.host,
      "port": self.port,
      "timeout": self.timeout,
      "user_agent": self.user_agent,
    }

  async def get_iomonitor_state(self) -> MicronicIOMonitorState:
    payload = await self.request_json("GET", "/state")
    state = payload.get("state") if isinstance(payload, dict) else None
    if not isinstance(state, str):
      raise MicronicError("Micronic server response did not contain a valid state.")
    try:
      return MicronicIOMonitorState(state)
    except ValueError as exc:
      raise MicronicError(f"Unknown Micronic state: {state}") from exc

  async def wait_for_fresh_data_ready(
    self,
    initial_state: MicronicIOMonitorState,
    timeout: float,
    poll_interval: float,
  ) -> None:
    # If we started at dataready, require a state change before accepting the next
    # dataready, otherwise we'd return the previous scan's stale result.
    require_state_change = initial_state == MicronicIOMonitorState.DATAREADY
    deadline = time.monotonic() + timeout
    while True:
      state = await self.get_iomonitor_state()
      if state != MicronicIOMonitorState.DATAREADY:
        require_state_change = False
      elif not require_state_change:
        return
      if time.monotonic() >= deadline:
        raise MicronicError(
          f"Timed out waiting for IO Monitor to reach {MicronicIOMonitorState.DATAREADY.value}."
        )
      await asyncio.sleep(poll_interval)

  async def get_single_tube_barcode(self) -> str:
    # Prefers the decoded tube value from GET /scanresult. Older IO Monitor builds
    # expose that value on GET /rackid instead, so fall back to /rackid for
    # cross-version compatibility.
    scan_result_payload = await self.request_json("GET", "/scanresult")
    barcode = _extract_single_tube_barcode(scan_result_payload)
    if barcode is not None:
      return barcode

    rack_id_payload = await self.request_json("GET", "/rackid")
    barcode = _extract_named_barcode(
      rack_id_payload,
      keys=("RackID", "rackid", "rack_id", "Barcode", "barcode", "Code", "code"),
    )
    if barcode is not None:
      return barcode

    raise MicronicError("Micronic single-tube scan result had an unexpected shape.")

  async def request(
    self,
    method: str,
    path: str,
    data: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
    expect_json: bool = True,
  ) -> bytes:
    return await asyncio.to_thread(
      self._request_sync,
      method,
      path,
      data,
      headers,
      expect_json,
    )

  async def request_json(
    self,
    method: str,
    path: str,
    data: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
  ) -> Any:
    response = await self.request(
      method=method,
      path=path,
      data=data,
      headers=headers,
      expect_json=True,
    )
    try:
      return json.loads(response.decode("utf-8"))
    except json.JSONDecodeError as exc:
      raise MicronicError(
        f"Micronic server returned non-JSON payload for {method} {path}."
      ) from exc

  def _request_sync(
    self,
    method: str,
    path: str,
    data: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
    expect_json: bool = True,
  ) -> bytes:
    req_headers = {
      "Accept": "application/json" if expect_json else "*/*",
      "Connection": "close",
      "User-Agent": self.user_agent,
    }
    if headers is not None:
      req_headers.update(headers)
    if data is not None:
      req_headers["Content-Length"] = str(len(data))

    req = request.Request(
      url=parse.urljoin(self.base_url, path),
      data=data,
      headers=req_headers,
      method=method,
    )

    for attempt in range(3):
      try:
        with request.urlopen(req, timeout=self.timeout) as response:
          body: bytes = response.read()
          return body
      except error.HTTPError as exc:
        body = exc.read()
        raise self._as_micronic_error(
          body, fallback=f"HTTP {exc.code} for {method} {path}"
        ) from exc
      except error.URLError as exc:
        if self._is_retryable_url_error(exc) and attempt < 2:
          time.sleep(0.25)
          continue
        raise MicronicError(
          f"Failed to reach Micronic server at {self.base_url}: {exc.reason}"
        ) from exc
      except (ConnectionResetError, http.client.RemoteDisconnected, OSError) as exc:
        if attempt == 2:
          raise MicronicError(f"Micronic connection failed for {method} {path}: {exc}") from exc
        time.sleep(0.25)

    raise MicronicError(f"Micronic request failed for {method} {path}.")

  def _as_micronic_error(self, body: bytes, fallback: str) -> MicronicError:
    try:
      payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
      return MicronicError(fallback)

    if isinstance(payload, dict) and "ErrorMsg" in payload:
      error_code = payload.get("ErrorCode")
      error_msg = payload.get("ErrorMsg")
      return MicronicError(f"Micronic error {error_code}: {error_msg}")

    return MicronicError(fallback)

  def _is_retryable_url_error(self, exc: error.URLError) -> bool:
    reason = exc.reason
    return isinstance(reason, (ConnectionResetError, http.client.RemoteDisconnected, OSError))


def _extract_single_tube_barcode(payload: Any) -> Optional[str]:
  if isinstance(payload, str) and payload:
    return payload
  return _extract_named_barcode(
    payload,
    keys=("TubeID", "tubeid", "tube_id", "Barcode", "barcode", "Code", "code", "Data", "data"),
  )


def _extract_named_barcode(payload: Any, keys: tuple[str, ...]) -> Optional[str]:
  if not isinstance(payload, dict):
    return None
  for key in keys:
    barcode = _coerce_single_barcode(payload.get(key))
    if barcode is not None:
      return barcode
  return None


def _coerce_single_barcode(value: Any) -> Optional[str]:
  if isinstance(value, str):
    return value or None
  if isinstance(value, list) and len(value) == 1 and value[0] not in (None, ""):
    return str(value[0])
  return None
