from __future__ import annotations

import json
from typing import Any, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.rack_reading import (
  LayoutInfo,
  RackReaderBackend,
  RackReaderError,
  RackReaderState,
  RackScanEntry,
  RackScanResult,
)

from .http_driver import MicronicError, MicronicHTTPDriver


class MicronicRackReaderError(MicronicError, RackReaderError):
  """Raised when Micronic rack-reading operations fail."""


class MicronicRackReadingBackend(RackReaderBackend):
  """Rack-reading backend for the Micronic Code Reader HTTP server."""

  def __init__(self, driver: MicronicHTTPDriver):
    super().__init__()
    self.driver = driver

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    await self.get_state()

  async def get_state(self) -> RackReaderState:
    payload = await self._request_json("GET", "/state")
    state = payload.get("state")
    if not isinstance(state, str):
      raise MicronicRackReaderError("Micronic server response did not contain a valid state.")
    try:
      return RackReaderState(state)
    except ValueError as exc:
      raise MicronicRackReaderError(f"Unknown Micronic state: {state}") from exc

  async def trigger_rack_scan(self) -> None:
    await self._request("POST", "/scanbox", data=b"", expect_json=False)

  async def trigger_rack_id_scan(self) -> None:
    # Micronic exposes the rack-barcode-only trigger on a separate endpoint from full rack scans.
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

  async def get_layouts(self) -> list[LayoutInfo]:
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

  def _parse_scan_result(self, payload: dict[str, Any]) -> RackScanResult:
    positions = self._get_list(payload, "Position")
    tube_ids = self._get_list(payload, "TubeID")
    statuses = self._get_list(payload, "Status")
    free_texts = self._get_list(payload, "FreeText")

    if not positions:
      raise MicronicRackReaderError("Micronic scan result did not include any positions.")

    entries: list[RackScanEntry] = []
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

  def _get_list(self, payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if value is None:
      return []
    if not isinstance(value, list):
      raise MicronicRackReaderError(f"Micronic field {key} was not a list.")
    return value

  def _get_required_item(self, items: list[Any], index: int, field_name: str) -> Any:
    try:
      return items[index]
    except IndexError as exc:
      raise MicronicRackReaderError(
        f"Micronic field {field_name} was missing an item for position index {index}."
      ) from exc

  def _get_optional_item(self, items: list[Any], index: int) -> Any:
    if index >= len(items):
      return None
    return items[index]

  async def _request_json(
    self,
    method: str,
    path: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
  ) -> Any:
    try:
      return await self.driver.request_json(method, path, data=data, headers=headers)
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc

  async def _request(
    self,
    method: str,
    path: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    expect_json: bool = True,
  ) -> bytes:
    try:
      return await self.driver.request(
        method,
        path,
        data=data,
        headers=headers,
        expect_json=expect_json,
      )
    except MicronicError as exc:
      raise MicronicRackReaderError(str(exc)) from exc
