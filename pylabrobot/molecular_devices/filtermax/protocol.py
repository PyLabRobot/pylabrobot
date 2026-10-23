"""Captured ASTM-like serial transport used by the FilterMax F5."""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

from pylabrobot.io.serial import Serial

from .errors import (
  FilterMaxChecksumError,
  FilterMaxDeviceError,
  FilterMaxProtocolError,
  FilterMaxReadCancelled,
  FilterMaxTimeoutError,
)

ACK = 0x06
ENQ = 0x05
EOT = 0x04
STX = 0x02
ETX = 0x03
ETB = 0x17
CR = 0x0D
LF = 0x0A

_ERROR_RE = re.compile(r"^- E(?P<code>\d+):\s*(?P<detail>.*)$", re.DOTALL)


class _CancelRequested(Exception):
  """Cancellation was observed before the next device message began."""


class _HandshakeTimeout(FilterMaxTimeoutError):
  """No byte arrived in response to the initial host ENQ."""


def checksum(data: bytes) -> int:
  """Return the captured modulo-256 checksum."""

  return sum(data) & 0xFF


def build_frame(payload: str, frame_number: int = 1, final: bool = True) -> bytes:
  """Encode a numbered ASCII payload with its terminator and checksum."""
  if not 0 <= frame_number <= 7:
    raise ValueError("frame_number must be between 0 and 7")
  terminator = ETX if final else ETB
  checked = bytes((ord(str(frame_number)),)) + payload.encode("ascii") + bytes((terminator,))
  return bytes((STX,)) + checked + f"{checksum(checked):02X}".encode("ascii") + b"\r\n"


@dataclass(frozen=True)
class FilterMaxMessage:
  """A complete, checksum-validated device message."""

  payload: str
  frame_count: int


class FilterMaxTransport:
  """Private command transport. Public raw-command access is intentionally not exposed."""

  def __init__(self, io: Serial):
    """Use one lock for each complete exchange or measurement stream."""
    self.io = io
    self._transaction_lock = asyncio.Lock()

  @asynccontextmanager
  async def _transaction(self) -> AsyncIterator[None]:
    """Own the wire until all replies, including a possible STOP reply, are consumed."""
    async with self._transaction_lock:
      yield

  async def _read_byte(
    self,
    deadline: float,
    *,
    cancel_event: Optional[asyncio.Event] = None,
  ) -> int:
    """Read before the deadline, checking cancellation only at a message boundary."""
    while True:
      if cancel_event is not None and cancel_event.is_set():
        raise _CancelRequested
      if time.monotonic() >= deadline:
        raise FilterMaxTimeoutError("Timed out waiting for a FilterMax protocol byte")
      data = await self.io.read(1)
      if data:
        return data[0]
      await asyncio.sleep(0)

  async def _expect(self, expected: int, deadline: float) -> None:
    """Read and validate one control byte."""
    actual = await self._read_byte(deadline)
    if actual != expected:
      raise FilterMaxProtocolError(
        f"Expected control byte 0x{expected:02X}, received 0x{actual:02X}"
      )

  async def send_request(self, payload: str, timeout: float = 5.0) -> None:
    """Send a request while the caller holds transaction ownership."""

    deadline = time.monotonic() + timeout
    await self.io.write(bytes((ENQ,)))
    try:
      await self._expect(ACK, deadline)
    except FilterMaxTimeoutError as exc:
      raise _HandshakeTimeout("Timed out waiting for ACK to the initial FilterMax ENQ") from exc
    await self.io.write(build_frame(payload))
    await self._expect(ACK, deadline)
    await self.io.write(bytes((EOT,)))

  async def _read_frame_after_stx(self, deadline: float) -> bytes:
    """Read a whole frame and validate its terminator and checksum."""
    frame = bytearray((STX,))
    while not frame.endswith(b"\r\n"):
      if len(frame) > 4096:
        raise FilterMaxProtocolError("FilterMax frame exceeded 4096 bytes")
      frame.append(await self._read_byte(deadline))
    if len(frame) < 7:
      raise FilterMaxProtocolError(f"FilterMax frame is too short: {bytes(frame)!r}")
    if frame[-5] not in (ETX, ETB):
      raise FilterMaxProtocolError("FilterMax frame is missing ETX/ETB")
    try:
      received_checksum = int(bytes(frame[-4:-2]), 16)
    except ValueError as exc:
      raise FilterMaxProtocolError("FilterMax frame checksum is not hexadecimal") from exc
    checked = bytes(frame[1:-4])
    expected_checksum = checksum(checked)
    if received_checksum != expected_checksum:
      raise FilterMaxChecksumError(
        f"Checksum mismatch: received {received_checksum:02X}, expected {expected_checksum:02X}"
      )
    return bytes(frame)

  async def receive_message(
    self,
    timeout: float = 60.0,
    *,
    command: str = "",
    cancel_event: Optional[asyncio.Event] = None,
  ) -> FilterMaxMessage:
    """Receive a whole message under transaction ownership.

    Cancellation is checked before ENQ. Once ENQ is received, all continuation
    frames and the final EOT are consumed before control returns to the owner.
    """

    deadline = time.monotonic() + timeout
    first = await self._read_byte(deadline, cancel_event=cancel_event)
    if first != ENQ:
      raise FilterMaxProtocolError(f"Expected device ENQ, received 0x{first:02X}")
    await self.io.write(bytes((ACK,)))

    payload_parts: List[bytes] = []
    expected_frame_number = 1
    while True:
      control = await self._read_byte(deadline)
      if control == EOT:
        raise FilterMaxProtocolError("FilterMax EOT arrived before a final ETX frame")
      if control != STX:
        raise FilterMaxProtocolError(f"Expected device STX or EOT, received 0x{control:02X}")
      frame = await self._read_frame_after_stx(deadline)
      frame_number = frame[1] - ord("0")
      if frame_number != expected_frame_number:
        raise FilterMaxProtocolError(
          f"Unexpected frame number {frame_number}; expected {expected_frame_number}"
        )
      payload_parts.append(frame[2:-5])
      await self.io.write(bytes((ACK,)))
      expected_frame_number = (expected_frame_number + 1) % 8
      if frame[-5] == ETX:
        await self._expect(EOT, deadline)
        break

    payload = b"".join(payload_parts).decode("ascii")
    match = _ERROR_RE.match(payload)
    if match is not None:
      code = int(match.group("code"))
      detail = match.group("detail")
      if code == 140:
        raise FilterMaxReadCancelled(code, detail, command)
      raise FilterMaxDeviceError(code, detail, command)
    return FilterMaxMessage(payload=payload, frame_count=len(payload_parts))

  async def exchange(
    self,
    payload: str,
    timeout: float = 60.0,
    *,
    cancel_event: Optional[asyncio.Event] = None,
  ) -> FilterMaxMessage:
    """Serialize a complete request and its response."""
    async with self._transaction():
      await self.send_request(payload)
      return await self.receive_message(timeout, command=payload, cancel_event=cancel_event)
