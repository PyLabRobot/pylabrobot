"""Classic INCO requests directly to a CERTUS FLEX controller over UDP."""

import asyncio
import math
import struct
from typing import Optional, Union

from pylabrobot.io import UDP

from ._inco import Argument, IncoError, IncoProtocolError, _cstring, _number, _pad4


def _escape(data: bytes) -> bytes:
  """Escape literal DLE bytes in a classic INCO frame."""
  return data.replace(b"\x10", b"\x10\x10")


def _request(sequence: int, kind: int, subtype: int, data: bytes) -> bytes:
  """Frame a request to the local controller with separate header/data checksums."""
  if not 1 <= sequence <= 255:
    raise ValueError("Classic INCO sequence must be between 1 and 255")
  if len(data) > 494:
    raise ValueError("Classic INCO request data exceeds 494 bytes")
  # SOH, sequence, operation, one local destination (FF), one source (00).
  # Checksums sum to FF, excluding DLE escapes.
  header = bytes((1, sequence, kind, subtype, 1, 255, 1, 0))
  header += bytes(((255 - sum(header)) & 255,))
  return (
    b"\x10\x01"
    + _escape(header[1:])
    + _escape(data)
    + b"\x10\x03"
    + _escape(bytes(((252 - sum(data)) & 255,)))
  )


def _response(packet: bytes) -> tuple[int, int, bytes]:
  """Validate an ACK datagram and return its sequence, INCO error, and payload."""
  if len(packet) > 1024 or not packet.startswith(b"\x10\x06"):
    raise IncoProtocolError("Expected a classic INCO ACK datagram")
  unescaped = bytearray()
  index = 2
  end = None
  while index < len(packet):
    value = packet[index]
    index += 1
    if value == 0x10:
      if index >= len(packet):
        raise IncoProtocolError("Truncated classic INCO escape")
      value = packet[index]
      index += 1
      if value == 3 and end is None:
        end = len(unescaped)
        continue
      if value != 0x10:
        raise IncoProtocolError("Unexpected classic INCO control byte")
    unescaped.append(value)
  # ACK, sequence, two-byte error, one destination (our source 00), checksum.
  if end is None or end < 6 or len(unescaped) != end + 1 or unescaped[3:5] != b"\x01\0":
    raise IncoProtocolError("Invalid classic INCO response lengths or route")
  if (6 + sum(unescaped[:6])) & 255 != 255:
    raise IncoProtocolError("Classic INCO header checksum mismatch")
  if (3 + sum(unescaped[6:])) & 255 != 255:
    raise IncoProtocolError("Classic INCO data checksum mismatch")
  sequence = unescaped[0]
  error = int.from_bytes(unescaped[1:3], "big")
  return sequence, 0x50000 + error if error else 0, bytes(unescaped[6:end])


def _decode_numeric(data: bytes) -> float:
  """Decode classic double or legacy fixed-point values, including their sign flag."""
  if len(data) == 9 and data[8] == 9:
    value = struct.unpack(">d", data[:8])[0]
  elif len(data) in (8, 9):
    if len(data) == 9 and data[8] != 13:
      raise IncoProtocolError("Unsupported classic INCO numeric type")
    integer, fraction = struct.unpack(">II", data[:8])
    # Legacy fixed-point reserves bit zero of the fraction for signedness.
    if (len(data) == 9 or fraction & 1) and integer >= 2**31:
      integer -= 2**32
    value = integer + (fraction & ~1) / 2**32
  elif len(data) == 4:
    integer, fraction = struct.unpack(">HH", data)
    if fraction & 1 and integer >= 2**15:
      integer -= 2**16
    value = integer + (fraction & ~1) / 2**16
  else:
    raise IncoProtocolError("Invalid classic INCO numeric response length")
  if not math.isfinite(value):
    raise IncoProtocolError("Nonfinite classic INCO numeric response")
  return float(value)


class IncoUDP:
  """One serialized classic INCO connection, without request retransmission.

  The controller uses big endian fixed-point numbers for numeric arguments.
  Timeout, cancellation, or malformed replies close the socket. Delayed and
  duplicate replies with different sequence numbers are ignored under the
  original request deadline. Asynchronous procedure tickets are unsupported.
  """

  def __init__(
    self,
    host: str,
    *,
    port: int = 1964,
    timeout: float = 60,
    source_ip: Optional[str] = None,
  ) -> None:
    """Configure a direct connection to the controller's classic INCO UDP port."""
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be finite and positive")
    self.io = UDP(
      "CERTUS FLEX INCO",
      host,
      port,
      read_timeout=timeout,
      write_timeout=timeout,
      source_ip=source_ip,
    )
    self.timeout = timeout
    self._lock = asyncio.Lock()
    self._connected = False
    self._sequence = 0

  async def setup(self) -> None:
    """Open the socket without sending hardware commands."""
    async with self._lock:
      if not self._connected:
        await asyncio.wait_for(self.io.setup(), self.timeout)
        self._connected = True

  async def stop(self) -> None:
    """Disconnect without changing pressure, motion, or valve state."""
    async with self._lock:
      self._connected = False
      await self.io.stop()

  async def _communicate(
    self, request: bytes, path: str, result_type: str
  ) -> Union[float, str, None]:
    """Send once and receive the matching response within the enclosing deadline."""
    await self.io.write(request)
    while True:
      sequence, error, data = _response(await self.io.read())
      if sequence == self._sequence:
        break
    if error in (0x502FE, 0x504FE):
      raise IncoProtocolError("Asynchronous classic INCO results are not supported")
    if error:
      raise IncoError(error, path)
    if result_type == "number":
      return _decode_numeric(data)
    if result_type == "string":
      if not data or data[-1] != 0 or b"\0" in data[:-1]:
        raise IncoProtocolError("Expected a terminated classic INCO string")
      return data[:-1].decode("latin-1")
    return None

  async def _exchange(
    self, kind: int, subtype: int, data: bytes, path: str, result_type: str
  ) -> Union[float, str, None]:
    """Serialize exchanges and discard sockets with uncertain transaction state."""
    # Validate before touching the connection or its sequence number.
    _request(1, kind, subtype, data)
    async with self._lock:
      if not self._connected:
        raise RuntimeError("INCO connection is closed; call setup() first")
      try:
        if self._sequence == 255:
          await self.io.stop()
          await asyncio.wait_for(self.io.setup(), self.timeout)
          self._sequence = 0
        self._sequence += 1
        request = _request(self._sequence, kind, subtype, data)
        return await asyncio.wait_for(self._communicate(request, path, result_type), self.timeout)
      except IncoError:
        raise
      except BaseException:
        self._connected = False
        await self.io.stop()
        raise

  async def get_number(self, path: str) -> float:
    """Read a numeric controller variable."""
    result = await self._exchange(2, 1, _cstring(path), path, "number")
    assert isinstance(result, float)
    return result

  async def get_string(self, path: str) -> str:
    """Read a string controller variable."""
    result = await self._exchange(2, 5, _cstring(path), path, "string")
    assert isinstance(result, str)
    return result

  async def put_number(self, path: str, value: float) -> None:
    """Write a signed 32.32 value with its explicit classic fixed-point type."""
    data = _number(value)[12:] + _cstring(path) + b"\x0d"
    await self._exchange(2, 0, data, path, "none")

  async def call(self, path: str, *arguments: Argument) -> None:
    """Call a synchronous procedure with up to sixteen typed arguments."""
    if len(arguments) > 16:
      raise ValueError("Classic INCO supports at most sixteen procedure arguments")
    mask = 0
    values = bytearray()
    for index, argument in enumerate(arguments):
      if isinstance(argument, str):
        mask |= 0x10000 << index
        values.extend(_pad4(_cstring(argument)))
      else:
        mask |= 0x10001 << index
        values.extend(_number(argument)[12:])
    if arguments:
      data = struct.pack(">II", len(arguments), mask) + _pad4(_cstring(path)) + values
      subtype = 1
    else:
      data = b"\0" * 4 + _pad4(_cstring(path))
      subtype = 0
    await self._exchange(4, subtype, data, path, "number")
