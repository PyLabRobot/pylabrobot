"""In-process mock of the Labcyte Echo Medman protocol for hardware-free testing.

``EchoMockServer`` is a localhost ``asyncio`` TCP server that speaks the same wire protocol
as a real Echo (``POST /Medman`` HTTP requests with gzip-compressed SOAP bodies) and replays
**real device responses** captured from a physical Echo 525 (serial ``E5XX-00000``, software
``2.7.3``) running a HiFi PCR protocol. It lets you drive ``Echo``/``Echo525`` end-to-end with
no instrument attached:

    async with EchoMockServer() as srv:
        echo = Echo525(host=srv.host, rpc_port=srv.port)
        await echo.setup()
        info = await echo.get_instrument_info()   # -> Model "Echo 525"

The server dispatches by SOAP method name, replaying the captured response for that method
(33 methods captured) and falling back to a generic ``SUCCEEDED/OK`` envelope otherwise. It
also models the instrument lock: motion/transfer RPCs issued without holding the lock get the
Echo's real "Caller does not own the lock" fault, so the locking workflow can be exercised
deterministically (mirrors how a real Echo gates these commands).

The captured responses are stored gzip-compressed and base64-encoded below; the bulky 384-well
``DoWellTransfer`` print-map report is trimmed to 3 representative wells to keep the fixture small.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import re
from types import TracebackType
from typing import Dict, Optional, Tuple, Type

# Methods that require the caller to hold the instrument lock on a real Echo.
_LOCK_REQUIRED = frozenset({
  "DoWellTransfer", "PlateSurvey", "DryPlate", "CloseDoor", "OpenDoor", "HomeAxes",
  "PresentSrcPlateGripper", "PresentDstPlateGripper",
  "RetractSrcPlateGripper", "RetractDstPlateGripper",
})

_REQUEST_METHOD = re.compile(rb"<SOAP-ENV:Body[^>]*><([A-Za-z][A-Za-z0-9_]*)")
_HEADER_END = b"\r\n\r\n"

# gzip(json({method: captured_response_soap_envelope})) from a real Echo 525 capture.
_CAPTURED_RESPONSES_B64 = (
  "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"
  "4HOK+PY+OGy8/+//S3Oqr07EAAA="
)


def _load_captured_responses() -> Dict[str, str]:
  blob = base64.b64decode("".join(_CAPTURED_RESPONSES_B64))
  return json.loads(gzip.decompress(blob).decode("utf-8"))


def _generic_response(method: str, *, succeeded: bool = True, status: str = "OK") -> str:
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
    '<SOAP-ENV:Body>'
    f"<{method}Response><{method}>"
    f'<SUCCEEDED type="xsd:boolean">{succeeded}</SUCCEEDED>'
    f'<Status type="xsd:string">{status}</Status>'
    f"</{method}></{method}Response>"
    "</SOAP-ENV:Body></SOAP-ENV:Envelope>"
  )


class EchoMockServer:
  """A localhost server that emulates the Echo Medman RPC protocol."""

  def __init__(self, host: str = "127.0.0.1", port: int = 0):
    self._host = host
    self._requested_port = port
    self._server: Optional[asyncio.AbstractServer] = None
    self._responses = _load_captured_responses()
    self._locked = False
    #: every (method, was_locked) the server handled - handy for assertions in tests.
    self.received: list[Tuple[str, bool]] = []

  @property
  def host(self) -> str:
    return self._host

  @property
  def port(self) -> int:
    if self._server is None:
      raise RuntimeError("EchoMockServer is not running; use 'async with EchoMockServer()'.")
    return self._server.sockets[0].getsockname()[1]

  async def __aenter__(self) -> "EchoMockServer":
    await self.start()
    return self

  async def __aexit__(self, exc_type: Optional[Type[BaseException]],
                      exc: Optional[BaseException], tb: Optional[TracebackType]) -> None:
    await self.stop()

  async def start(self) -> None:
    self._server = await asyncio.start_server(self._handle, self._host, self._requested_port)

  async def stop(self) -> None:
    if self._server is not None:
      self._server.close()
      await self._server.wait_closed()
      self._server = None

  def response_for(self, method: str) -> str:
    """Return the SOAP envelope the mock will reply with for ``method`` (lock-aware)."""
    if method in _LOCK_REQUIRED and not self._locked:
      return _generic_response(method, succeeded=False, status="Caller does not own the lock")
    return self._responses.get(method, _generic_response(method))

  async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
      while True:
        message = await self._read_request(reader)
        if message is None:
          break
        method = message
        # Update lock state from the *request* (it is granted/released regardless of caller).
        if method == "LockInstrument":
          self._locked = True
        self.received.append((method, self._locked))
        body = gzip.compress(self.response_for(method).encode("utf-8"))
        if method == "UnlockInstrument":
          self._locked = False
        header = (
          "HTTP/1.1 200 OK\r\n"
          "Server: Echo. Liquid Handler-2.7.3\r\n"
          "Protocol: 2.6\r\n"
          'Content-Type: text/xml; charset="utf-8"\r\n'
          f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("ascii")
        writer.write(header + body)
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError):
      pass
    finally:
      writer.close()

  async def _read_request(self, reader: asyncio.StreamReader) -> Optional[str]:
    """Read one HTTP request; return its SOAP method name (or None at EOF)."""
    data = b""
    while _HEADER_END not in data:
      chunk = await reader.read(4096)
      if not chunk:
        return None
      data += chunk
    head, _, rest = data.partition(_HEADER_END)
    content_length = 0
    for line in head.split(b"\n"):
      if line.lower().startswith(b"content-length:"):
        content_length = int(line.split(b":", 1)[1].strip())
    while len(rest) < content_length:
      chunk = await reader.read(content_length - len(rest))
      if not chunk:
        break
      rest += chunk
    try:
      decoded = gzip.decompress(rest[:content_length])
    except (OSError, EOFError):
      return None
    match = _REQUEST_METHOD.search(decoded)
    return match.group(1).decode("ascii") if match else "Unknown"
