from __future__ import annotations

import asyncio
import http.client
import json
import time
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from pylabrobot.rack_reading.backend import RackReaderBackend
from pylabrobot.rack_reading.standard import (
  LayoutInfo,
  RackReaderError,
  RackReaderState,
  RackScanEntry,
  RackScanResult,
)


class MicronicRackReaderError(RackReaderError):
  """Raised when the Micronic HTTP server returns an error."""


class MicronicHTTPBackend(RackReaderBackend):
  """Rack reader backend for the Micronic Code Reader HTTP server."""

  def __init__(
    self,
    host: str = "127.0.0.1",
    port: int = 2500,
    timeout: float = 60.0,
    poll_interval: float = 2.0,
  ) -> None:
    super().__init__()
    self.host = host
    self.port = port
    self.timeout = timeout
    self.poll_interval = poll_interval

  @property
  def base_url(self) -> str:
    return f"http://{self.host}:{self.port}"

  async def setup(self) -> None:
    await self.get_state()

  async def stop(self) -> None:
    return None

  async def get_state(self) -> RackReaderState:
    payload = await self._request_json("GET", "/state")
    state = payload.get("state")
    if not isinstance(state, str):
      raise MicronicRackReaderError("Micronic server response did not contain a valid state.")
    try:
      return RackReaderState(state)
    except ValueError as exc:
      raise MicronicRackReaderError(f"Unknown Micronic state: {state}") from exc

  async def scan_box(self) -> None:
    await self._request("POST", "/scanbox", data=b"", expect_json=False)

  async def scan_tube(self) -> None:
    await self._request("POST", "/scantube", data=b"", expect_json=False)

  async def get_scan_result(self) -> RackScanResult:
    payload = await self._request_json("GET", "/scanresult")
    return self._parse_scan_result(payload)

  async def get_rack_id(self) -> str:
    payload = await self._request_json("GET", "/rackid")

    if isinstance(payload, dict):
      for key in ("RackID", "rackid", "rack_id"):
        value = payload.get(key)
        if isinstance(value, str):
          return value

    raise MicronicRackReaderError("Micronic rack ID response had an unexpected shape.")

  async def get_layouts(self) -> List[LayoutInfo]:
    payload = await self._request_json("GET", "/layoutlist")

    if isinstance(payload, list):
      return [LayoutInfo(name=str(item)) for item in payload]

    if isinstance(payload, dict):
      for key in ("Layout", "layouts", "layoutlist", "data"):
        value = payload.get(key)
        if isinstance(value, list):
          return [LayoutInfo(name=str(item)) for item in value]

    raise MicronicRackReaderError("Micronic layout list response had an unexpected shape.")

  async def get_current_layout(self) -> str:
    payload = await self._request_json("GET", "/currentlayout")

    if isinstance(payload, str):
      return payload

    if isinstance(payload, dict):
      for key in ("Layout", "layout", "currentlayout", "name"):
        value = payload.get(key)
        if isinstance(value, str):
          return value

    raise MicronicRackReaderError("Micronic current layout response had an unexpected shape.")

  async def set_current_layout(self, layout: str) -> None:
    await self._request(
      "PUT",
      "/currentlayout",
      data=json.dumps({"Layout": layout}).encode("utf-8"),
      headers={"Content-Type": "application/json; charset=utf-8"},
      expect_json=False,
    )

  async def _request_json(
    self,
    method: str,
    path: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
  ) -> Any:
    response = await self._request(method=method, path=path, data=data, headers=headers)
    try:
      return json.loads(response.decode("utf-8"))
    except json.JSONDecodeError as exc:
      raise MicronicRackReaderError(
        f"Micronic server returned non-JSON payload for {method} {path}."
      ) from exc

  async def _request(
    self,
    method: str,
    path: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
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

  def _request_sync(
    self,
    method: str,
    path: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    expect_json: bool = True,
  ) -> bytes:
    req_headers = {
      "Accept": "application/json" if expect_json else "*/*",
      "Connection": "close",
      # Micronic's HTTP server has been observed to reset some endpoints for Python's default
      # urllib user-agent while serving the same requests to curl successfully.
      "User-Agent": "curl/8.0",
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
          return response.read()
      except error.HTTPError as exc:
        body = exc.read()
        raise self._as_micronic_error(body, fallback=f"HTTP {exc.code} for {method} {path}") from exc
      except error.URLError as exc:
        raise MicronicRackReaderError(
          f"Failed to reach Micronic server at {self.base_url}: {exc.reason}"
        ) from exc
      except (ConnectionResetError, http.client.RemoteDisconnected, OSError) as exc:
        if attempt == 2:
          raise MicronicRackReaderError(
            f"Micronic connection failed for {method} {path}: {exc}"
          ) from exc
        time.sleep(0.25)

    raise MicronicRackReaderError(f"Micronic request failed for {method} {path}.")

  def _as_micronic_error(self, body: bytes, fallback: str) -> MicronicRackReaderError:
    try:
      payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
      return MicronicRackReaderError(fallback)

    if isinstance(payload, dict) and "ErrorMsg" in payload:
      error_code = payload.get("ErrorCode")
      error_msg = payload.get("ErrorMsg")
      return MicronicRackReaderError(f"Micronic error {error_code}: {error_msg}")

    return MicronicRackReaderError(fallback)

  def _parse_scan_result(self, payload: Dict[str, Any]) -> RackScanResult:
    positions = self._get_list(payload, "Position")
    tube_ids = self._get_list(payload, "TubeID")
    statuses = self._get_list(payload, "Status")
    free_texts = self._get_list(payload, "FreeText")

    if not positions:
      raise MicronicRackReaderError("Micronic scan result did not include any positions.")

    entries: List[RackScanEntry] = []
    for idx, position in enumerate(positions):
      tube_id = self._get_optional_item(tube_ids, idx)
      entries.append(
        RackScanEntry(
          position=str(position),
          tube_id=None if tube_id in (None, "") else str(tube_id),
          status=str(self._get_required_item(statuses, idx, "Status")),
          free_text=str(self._get_optional_item(free_texts, idx) or ""),
        )
      )

    rack_id = payload.get("RackID")
    date = payload.get("Date")
    time = payload.get("Time")
    if not isinstance(rack_id, str) or not isinstance(date, str) or not isinstance(time, str):
      raise MicronicRackReaderError("Micronic scan result did not include RackID/Date/Time.")

    return RackScanResult(rack_id=rack_id, date=date, time=time, entries=entries)

  def _get_list(self, payload: Dict[str, Any], key: str) -> List[Any]:
    value = payload.get(key)
    if value is None:
      return []
    if not isinstance(value, list):
      raise MicronicRackReaderError(f"Micronic field {key} was not a list.")
    return value

  def _get_required_item(self, items: List[Any], index: int, field_name: str) -> Any:
    try:
      return items[index]
    except IndexError as exc:
      raise MicronicRackReaderError(
        f"Micronic field {field_name} was missing an item for position index {index}."
      ) from exc

  def _get_optional_item(self, items: List[Any], index: int) -> Any:
    if index >= len(items):
      return None
    return items[index]
