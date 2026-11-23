"""The synchronous INCO operations used by CERTUS FLEX, over INCO server TCP."""

import asyncio
import math
import re
import struct
from typing import Union

from pylabrobot.io import Socket

_COMPATIBILITY = bytes.fromhex("100100820101ff01007a1003fc")
_HEADER_SIZE = 52
_MAX_FRAME_SIZE = 1468
Argument = Union[float, str]


class IncoError(RuntimeError):
  """An INCO server/controller error, preserving its unsigned error code."""

  def __init__(self, code: int, path: str) -> None:
    """Record the controller's error code and the failing operation."""
    self.code = code
    self.path = path
    super().__init__(f"INCO error 0x{code:08X} for {path}")


class IncoProtocolError(RuntimeError):
  """An unsupported or malformed INCO response."""


def _cstring(value: str) -> bytes:
  """Encode an ASCII argument with a single terminator."""
  if "\0" in value:
    raise ValueError("INCO strings cannot contain NUL")
  return value.encode("ascii") + b"\0"


def _pad4(value: bytes) -> bytes:
  """Align a field to a four-byte boundary."""
  return value + b"\0" * (-len(value) % 4)


def _number(value: float) -> bytes:
  """Encode a double with its signed fixed-point fallback."""
  # Both representations are sent; the server selects the target's native one.
  if isinstance(value, bool) or not math.isfinite(value) or not -(2**31) <= value < 2**31:
    raise ValueError("INCO numbers must be finite and fit signed 32.32 fixed point")
  return b"\x01\x0f\0\0" + struct.pack(">dq", value, math.floor(value * 2**32))


def _address(target: str) -> bytes:
  """Address a local target through the connected INCO server."""
  # A local target on the selected server; network routing and subtargets are
  # deliberately excluded. Silently truncating a name could select another device.
  if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_ .-]{0,30}", target):
    raise ValueError("target must be a local INCO target name of 1–31 ASCII characters")
  # Direction, slot width, slot count, current index, four reserved bytes.
  return bytes((1, 32, 3, 1, 0, 0, 0, 0)) + b"".join(
    _cstring(part).ljust(32, b"\0") for part in (".", "...", target)
  )


def _frame(address: bytes, sequence: int, kind: int, subtype: int, data: bytes) -> bytes:
  """Build one unfragmented, big endian INCO request."""
  if len(data) > 1024:
    raise ValueError("Fragmented INCO requests are not supported")
  header = bytearray(_HEADER_SIZE)
  header[:13] = _COMPATIBILITY
  header[13:20] = bytes((2, 3, 0, 0, 1, kind, subtype))
  struct.pack_into(">IHH", header, 20, sequence, 1, 0)
  header[32] = 1  # Big endian frame fields and payload.
  struct.pack_into(">HH", header, 48, len(address), len(data))
  return bytes(header) + address + data


def _response_lengths(header: bytes, sequence: int) -> tuple[str, int, int, int]:
  """Validate a response header before trusting its body lengths."""
  if len(header) != _HEADER_SIZE or header[:13] != _COMPATIBILITY:
    raise IncoProtocolError("Invalid INCO frame header")
  order = ">" if header[32] & 1 else "<"
  number, fragments, fragment = struct.unpack_from(order + "IHH", header, 20)
  if number != sequence:
    raise IncoProtocolError(f"INCO sequence mismatch: received {number}, expected {sequence}")
  if header[13] != 2 or header[14] > 3 or header[17] != 6:
    raise IncoProtocolError("Unsupported INCO response version or transaction type")
  if fragments != 1 or fragment != 0:
    raise IncoProtocolError("Fragmented INCO responses are not supported")
  extended, address, data = struct.unpack_from(order + "HHH", header, 46)
  if address < 8 or data > 1024 or _HEADER_SIZE + extended + address + data > _MAX_FRAME_SIZE:
    raise IncoProtocolError("Invalid INCO frame lengths")
  return order, extended, address, data


def _decode_number(data: bytes, order: str) -> float:
  """Decode an INCO double or signed 32.32 value."""
  if len(data) != 12:
    raise IncoProtocolError("Expected a 12-byte numeric INCO value")
  dtype = struct.unpack_from(order + "H", data)[0]
  if dtype == 0x109:
    value = struct.unpack_from(order + "d", data, 4)[0]
  elif dtype == 0x10D:
    # Word order is integer then fraction, including in little endian frames.
    integer, fraction = struct.unpack_from(order + "iI", data, 4)
    value = integer + fraction / 2**32
  else:
    raise IncoProtocolError(f"Unsupported INCO numeric type 0x{dtype:04X}")
  if not math.isfinite(value):
    raise IncoProtocolError("Nonfinite INCO numeric response")
  return float(value)


def _decode_string(data: bytes, order: str) -> str:
  """Decode an INCO string with a two-word length prefix."""
  if len(data) < 8 or struct.unpack_from(order + "H", data)[0] != 0x202:
    raise IncoProtocolError("Expected an INCO length-prefixed string")
  low, high = struct.unpack_from(order + "HH", data, 4)
  size = low + (high << 16)
  if size == 0 or size > len(data) - 8 or data[8 + size - 1] != 0:
    raise IncoProtocolError("Truncated or unterminated INCO string")
  # The supplied Windows interface uses ANSI text; identity strings are ASCII.
  return data[8 : 8 + size - 1].decode("latin-1")


class Inco:
  """One serialized connection to an INCO server, with no automatic retries."""

  def __init__(self, host: str, target: str, *, port: int = 7979, timeout: float = 60) -> None:
    """Configure an unopened connection and validate its target."""
    self._address = _address(target)
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be finite and positive")
    self.io = Socket("CERTUS FLEX INCO", host, port, read_timeout=timeout, write_timeout=timeout)
    self.timeout = timeout
    self._lock = asyncio.Lock()
    self._connected = False
    self._sequence = 0

  async def setup(self) -> None:
    """Open the socket without sending controller commands."""
    async with self._lock:
      if not self._connected:
        await asyncio.wait_for(self.io.setup(), self.timeout)
        self._connected = True

  async def stop(self) -> None:
    """Close the socket; does not change the controller's state."""
    async with self._lock:
      self._connected = False
      await self.io.stop()

  async def _communicate(
    self, request: bytes, kind: int, subtype: int, path: str, result_type: str
  ) -> Union[float, str, None]:
    """Write one frame, validate the reply, and decode its value."""
    await self.io.write(request)
    header = await self.io.read_exact(_HEADER_SIZE)
    order, extended, address, size = _response_lengths(header, self._sequence)
    body = await self.io.read_exact(extended + address + size)
    if len(body) != extended + address + size:
      raise IncoProtocolError("Truncated INCO response")
    error = struct.unpack_from(order + "I", header, 28)[0]
    if error:
      raise IncoError(error, path)
    if header[18:20] != bytes((kind, subtype)):
      raise IncoProtocolError("INCO response operation does not match request")
    data = body[extended + address :]
    if result_type == "number":
      return _decode_number(data, order)
    if result_type == "string":
      return _decode_string(data, order)
    return None

  async def _exchange(
    self, kind: int, subtype: int, data: bytes, path: str, result_type: str
  ) -> Union[float, str, None]:
    """Serialize a request and invalidate uncertain streams without retrying."""
    async with self._lock:
      if not self._connected:
        raise RuntimeError("INCO connection is closed; call setup() first")
      self._sequence = self._sequence % 0xFFFFFFFF + 1
      request = _frame(self._address, self._sequence, kind, subtype, data)
      try:
        return await asyncio.wait_for(
          self._communicate(request, kind, subtype, path, result_type), self.timeout
        )
      except IncoError:
        raise  # A complete, matched error reply leaves the stream synchronized.
      except BaseException:
        # A pulse may already have happened. Closing prevents a late response
        # from being consumed as the result of a later command.
        self._connected = False
        await self.io.stop()
        raise

  async def get_number(self, path: str) -> float:
    """Read a numeric controller variable."""
    result = await self._exchange(2, 1, b"\0" * 4 + _pad4(_cstring(path)), path, "number")
    assert isinstance(result, float)
    return result

  async def get_string(self, path: str) -> str:
    """Read a controller string variable."""
    result = await self._exchange(2, 5, b"\0" * 4 + _pad4(_cstring(path)), path, "string")
    assert isinstance(result, str)
    return result

  async def put_number(self, path: str, value: float) -> None:
    """Set a numeric controller variable."""
    await self._exchange(2, 0, _number(value) + _cstring(path), path, "none")

  async def call(self, path: str, *arguments: Argument) -> None:
    """Call a synchronous procedure using typed numeric or string arguments."""
    if len(arguments) > 255:
      raise ValueError("Too many INCO procedure arguments")
    data = bytes((len(arguments), 0, 0, 0)) + _pad4(_cstring(path))
    for argument in arguments:
      data += (
        b"\x02\0\0\0" + _pad4(_cstring(argument))
        if isinstance(argument, str)
        else _number(argument)
      )
    # Ordinary CallProcedure returns a typed number; the legacy CERTUS wrapper
    # checks the frame error and ignores the procedure's numeric return value.
    await self._exchange(4, 1, data, path, "number")
