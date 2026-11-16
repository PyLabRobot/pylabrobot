"""JSON HTTP over a USB serial bridge, using the standard HTTP response parser."""

import asyncio
import http.client
import io
import json
import logging
import math
import socket
import time
from typing import Any, Dict, Mapping, Optional, Tuple, cast
from urllib.parse import quote, urlsplit

from pylabrobot.io.http import HTTP, HTTPResponse

try:
  import serial
except ImportError:
  serial = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class _SerialReader(io.RawIOBase):
  """Expose serial bytes to HTTPResponse without interpreting a timeout as EOF."""

  def __init__(self, port: "serial.Serial", deadline: float):
    super().__init__()
    self.port = port
    self.deadline = deadline

  def readable(self) -> bool:
    return True

  def readinto(self, buffer: Any) -> int:
    if not len(buffer):
      return 0
    remaining = self.deadline - time.monotonic()
    if remaining <= 0:
      raise TimeoutError("Timed out reading HTTP response over serial")
    self.port.timeout = remaining
    # Waiting for an entire BufferedReader buffer would stall short responses.
    data = self.port.read(min(len(buffer), max(1, self.port.in_waiting)))
    if not data:
      raise TimeoutError("Timed out reading HTTP response over serial")
    buffer[: len(data)] = data
    return len(data)


class _SerialSocket:
  """The socket operations HTTPConnection needs, backed by an already open port."""

  def __init__(self, port: "serial.Serial", deadline: float):
    self.port = port
    self.deadline = deadline

  def sendall(self, data: bytes) -> None:
    """Write the full request within the exchange deadline."""
    remaining = self.deadline - time.monotonic()
    if remaining <= 0:
      raise TimeoutError("Timed out writing HTTP request over serial")
    self.port.write_timeout = remaining
    if self.port.write(data) != len(data):
      raise OSError("Incomplete HTTP request write over serial")

  def makefile(self, mode: str) -> io.BufferedReader:
    """Give HTTPResponse a buffered binary stream without ownership of the port."""
    if mode != "rb":
      raise ValueError("Serial HTTP only supports binary response streams")
    return io.BufferedReader(_SerialReader(self.port, self.deadline))

  def close(self) -> None:
    """HTTP connections end independently of the USB serial port's lifetime."""


class HTTPSerial(HTTP):
  """HTTP requests over a serial byte stream, with HTTP capture/replay semantics.

  Requires ``pylabrobot[serial]``. The peer must provide an HTTP-to-serial
  bridge, such as the Opentrons Flex USB-B port. Responses must use
  Content-Length or chunked framing: serial has no HTTP connection-close EOF.
  Requests run sequentially in a worker thread. A framing, timeout or I/O
  failure closes the port; call ``stop()`` and ``setup()`` to reconnect rather
  than retrying a command whose execution state may be unknown.
  Redirects are never followed; use ``request_raw(..., allow_redirects=False)``
  to inspect a redirect response.
  """

  def __init__(
    self,
    human_readable_device_name: str,
    port: str,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 30.0,
    baudrate: int = 1152000,
  ):
    if not port:
      raise ValueError("serial port must not be empty")
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be finite and greater than zero")
    if baudrate <= 0:
      raise ValueError("baudrate must be greater than zero")
    super().__init__(
      human_readable_device_name,
      base_url=f"http+serial://{quote(port, safe='')}",
      headers=headers,
      timeout=timeout,
    )
    self.port = port
    self.baudrate = baudrate
    self._serial: Optional[serial.Serial] = None

  def _url(self, path: str) -> str:
    """Identify this serial device in captures and HTTP errors."""
    return f"{self.base_url}/{path.lstrip('/')}"

  def _open(self) -> None:
    """Open pyserial in the transport worker, leaving imports optional."""
    if serial is None:
      raise RuntimeError("USB serial HTTP requires pyserial: pip install pylabrobot[serial]")
    if self._serial is None:
      self._serial = serial.Serial(
        port=self.port,
        baudrate=self.baudrate,
        timeout=self.timeout,
        write_timeout=self.timeout,
      )

  def _close(self) -> None:
    """Release the port in the same worker that handles requests."""
    if self._serial is not None:
      self._serial.close()
      self._serial = None

  async def setup(self) -> None:
    """Open the USB serial bridge without issuing robot commands."""
    await super().setup()
    async with self._require_lock():
      future = asyncio.get_running_loop().run_in_executor(self._executor, self._open)
      try:
        await asyncio.shield(future)
      except BaseException:
        # Opening cannot be interrupted midway; finish then release its handle.
        try:
          await future
        finally:
          await asyncio.get_running_loop().run_in_executor(self._executor, self._close)
          await super().stop()
        raise
    logger.info("Opened HTTP-over-serial connection on %s", self.port)

  async def stop(self) -> None:
    """Drain queued work and close the USB port."""
    if self._executor is None:
      return
    async with self._require_lock():
      if self._executor is not None:
        await asyncio.get_running_loop().run_in_executor(self._executor, self._close)
        await super().stop()

  def _exchange(
    self, method: str, path: str, body: Optional[bytes], headers: Mapping[str, str]
  ) -> HTTPResponse:
    """Exchange a framed HTTP request and response, never retrying a failure."""
    if self._serial is None:
      raise RuntimeError("Serial HTTP port is closed; reconnect before sending another command")
    if urlsplit(path).scheme or urlsplit(path).netloc:
      raise ValueError("Serial HTTP requests require a path on the connected device")
    adapter = _SerialSocket(self._serial, time.monotonic() + self.timeout)
    connection = http.client.HTTPConnection("localhost", timeout=self.timeout)
    connection.sock = cast(socket.socket, adapter)
    request_headers = {**self.headers, **headers, "Connection": "keep-alive"}
    try:
      connection.request(method, f"/{path.lstrip('/')}", body=body, headers=request_headers)
      with connection.getresponse() as response:
        if response.length is None and not response.chunked:
          raise http.client.HTTPException(
            "Serial HTTP response requires Content-Length or chunked framing"
          )
        return HTTPResponse(
          status=response.status,
          body=response.read(),
          headers={key.lower(): value for key, value in response.getheaders()},
        )
    except BaseException:
      self._close()
      raise
    finally:
      connection.close()

  def _make_request(
    self, method: str, path: str, data: Optional[Dict[str, Any]]
  ) -> Tuple[int, str]:
    """Encode JSON for the shared serial HTTP exchange."""
    body = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    response = self._exchange(method, path, body, headers)
    return response.status, response.body.decode("utf-8", errors="replace")

  def _make_raw_request(
    self,
    method: str,
    path: str,
    body: Optional[bytes],
    headers: Mapping[str, str],
    allow_redirects: bool,
  ) -> HTTPResponse:
    """Return raw bytes over USB; redirects require explicit caller handling."""
    response = self._exchange(method, path, body, headers)
    if allow_redirects and response.status in (301, 302, 303, 307, 308):
      raise http.client.HTTPException(
        "Serial HTTP does not follow redirects; use allow_redirects=False to inspect the response"
      )
    return response
