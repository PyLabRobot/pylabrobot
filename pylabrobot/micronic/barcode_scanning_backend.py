from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from pylabrobot.capabilities.barcode_scanning import BarcodeScannerBackend, BarcodeScannerError
from pylabrobot.capabilities.rack_reading import RackReaderState
from pylabrobot.resources.barcode import Barcode

from .http_driver import MicronicError, MicronicHTTPDriver


class MicronicBarcodeScannerError(MicronicError, BarcodeScannerError):
  """Raised when Micronic single-tube barcode scanning fails."""


class MicronicBarcodeScannerBackend(BarcodeScannerBackend):
  """Single-tube barcode-scanning backend for the Micronic Code Reader HTTP server."""

  def __init__(
    self,
    driver: MicronicHTTPDriver,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
  ):
    super().__init__()
    self.driver = driver
    self.timeout = timeout
    self.poll_interval = poll_interval

  async def _on_setup(self):
    await self._get_state()

  async def scan_barcode(self) -> Barcode:
    initial_state = await self._get_state()
    await self._request("POST", "/scantube", data=b"", expect_json=False)
    await self._wait_for_dataready(initial_state=initial_state)
    data = await self._read_tube_barcode()
    return Barcode(data=data, symbology="Data Matrix", position_on_resource="bottom")

  async def _get_state(self) -> RackReaderState:
    payload = await self._request_json("GET", "/state")
    state = payload.get("state")
    if not isinstance(state, str):
      raise MicronicBarcodeScannerError("Micronic server response did not contain a valid state.")
    try:
      return RackReaderState(state)
    except ValueError as exc:
      raise MicronicBarcodeScannerError(f"Unknown Micronic state: {state}") from exc

  async def _wait_for_dataready(self, initial_state: RackReaderState) -> None:
    require_state_change = initial_state == RackReaderState.DATAREADY
    deadline = time.monotonic() + self.timeout

    while True:
      state = await self._get_state()
      if state != RackReaderState.DATAREADY:
        require_state_change = False
      elif not require_state_change:
        return

      if time.monotonic() >= deadline:
        raise MicronicBarcodeScannerError(
          f"Timed out waiting for barcode scan to reach {RackReaderState.DATAREADY.value}."
        )
      await asyncio.sleep(self.poll_interval)

  async def _read_tube_barcode(self) -> str:
    scan_result_payload = await self._request_json("GET", "/scanresult")
    barcode = self._extract_single_tube_barcode(scan_result_payload)
    if barcode is not None:
      return barcode

    rack_id_payload = await self._request_json("GET", "/rackid")
    barcode = self._extract_named_barcode(
      rack_id_payload,
      keys=("RackID", "rackid", "rack_id", "Barcode", "barcode", "Code", "code"),
    )
    if barcode is not None:
      return barcode

    raise MicronicBarcodeScannerError(
      "Micronic single-tube scan result had an unexpected shape."
    )

  async def _request_json(
    self,
    method: str,
    path: str,
    data: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
  ) -> Any:
    try:
      return await self.driver.request_json(method, path, data=data, headers=headers)
    except MicronicError as exc:
      raise MicronicBarcodeScannerError(str(exc)) from exc

  async def _request(
    self,
    method: str,
    path: str,
    data: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
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
      raise MicronicBarcodeScannerError(str(exc)) from exc

  def _extract_single_tube_barcode(self, payload: Any) -> Optional[str]:
    if isinstance(payload, str) and payload:
      return payload

    return self._extract_named_barcode(
      payload,
      keys=("TubeID", "tubeid", "tube_id", "Barcode", "barcode", "Code", "code", "Data", "data"),
    )

  def _extract_named_barcode(self, payload: Any, keys: tuple[str, ...]) -> Optional[str]:
    if not isinstance(payload, dict):
      return None

    for key in keys:
      barcode = self._coerce_single_barcode(payload.get(key))
      if barcode is not None:
        return barcode
    return None

  def _coerce_single_barcode(self, value: Any) -> Optional[str]:
    if isinstance(value, str):
      return value or None
    if isinstance(value, list) and len(value) == 1 and value[0] not in (None, ""):
      return str(value[0])
    return None
