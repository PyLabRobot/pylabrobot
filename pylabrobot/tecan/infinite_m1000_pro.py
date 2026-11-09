"""Tecan Infinite M1000 Pro plate reader."""

import asyncio
import logging
import math
import re
import time
from functools import reduce
from operator import xor
from typing import Literal

from pylabrobot.io.usb import USB

logger = logging.getLogger(__name__)


def _encode(command: str) -> bytes:
  """Frame an ASCII command using the reader's RDNet protocol."""
  payload = command.encode("ascii")
  if len(payload) > 65535 or any(byte in payload for byte in (2, 3, 13)):
    raise ValueError("Invalid command payload")
  body = b"\x02" + payload + b"\x03"
  return body + b"\x00" + len(payload).to_bytes(2, "big") + bytes([reduce(xor, body, 0), 13])


def _decode(frame: bytes) -> str:
  """Validate and decode one reader response."""
  if len(frame) < 7 or frame[0] != 2 or frame[-1] != 13 or frame[-6] != 3:
    raise ValueError("Malformed reader response")
  if frame[-5] != 0:
    raise ValueError(f"Reader response error byte: {frame[-5]}")
  if reduce(xor, frame[:-5], 0) != frame[-2]:
    raise ValueError("Reader response checksum mismatch")
  return frame[1:-6].decode("ascii")


class InfiniteM1000Pro:
  """Control the tray of an Infinite M1000 Pro over USB."""

  def __init__(self) -> None:
    """Create a reader; setup must be called before operations."""
    self.usb = USB(
      id_vendor=0x0C47,
      id_product=0x8009,
      human_readable_device_name="Tecan Infinite M1000 Pro",
      read_endpoint_address=0x81,
      write_endpoint_address=0x02,
      packet_read_timeout=1,
      read_timeout=30,
      zero_length_packets=False,
    )
    self._buffer = bytearray()
    self._lock = asyncio.Lock()
    self._ready = False
    self._faulted = False
    self._initialized = False

  async def setup(self) -> None:
    """Connect after the reader has completed its normal startup."""
    async with self._lock:
      if self._faulted:
        raise RuntimeError("Reader state is uncertain; assess the instrument before a new session")
      if self._ready:
        return
      self._ready = False
      try:
        await self.usb.setup(empty_buffer=False)
      except BaseException:
        self._faulted = True
        raise
      self._buffer.clear()
      self._ready = True
      logger.warning(
        "Tray opening has produced abnormal stuttering after power-up; do not operate until "
        "initialization and safe movement are understood. Reader-reported position may be unreliable."
      )
      logger.info("Connected to Tecan Infinite M1000 Pro")

  async def stop(self) -> None:
    """Close the USB connection without moving the carrier."""
    async with self._lock:
      self._ready = False
      self._initialized = False
      self._buffer.clear()
      await self.usb.stop()
      logger.info("Disconnected from Tecan Infinite M1000 Pro")

  async def _receive(self, timeout: float = 30) -> str:
    """Read one complete response, retaining any subsequent frame."""
    deadline = time.monotonic() + timeout
    while True:
      if time.monotonic() >= deadline:
        raise TimeoutError("Timed out waiting for reader response")
      if self._buffer and self._buffer[0] != 2:
        raise ValueError("Unexpected reader response prefix")
      end = self._buffer.find(b"\x03", 1)
      if end >= 0 and end + 6 > 4096:
        raise ValueError("Reader response too long")
      if end >= 0 and len(self._buffer) >= end + 6:
        frame = bytes(self._buffer[: end + 6])
        del self._buffer[: end + 6]
        response = _decode(frame)
        if response.startswith(("MSG", "LOG:")):
          logger.info("Reader notification: %s", response)
          continue
        return response
      if len(self._buffer) > 4096:
        raise ValueError("Reader response too long")
      remaining = max(1, math.ceil(deadline - time.monotonic()))
      self._buffer.extend(await self.usb.read(timeout=remaining))

  async def initialize(self) -> None:
    """Initialize internal mechanisms once per connection; this can move the instrument.

    Only call with a clear instrument and a supervising operator. Draining the input does
    not establish that a prior interrupted operation has completed or that motion is safe.
    """
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader is not ready; call setup first")
      if self._initialized:
        return
      try:
        await self.usb.drain(timeout=0.1, max_duration=2)
        await self.usb.write(_encode("QQ"))
        status = await self._receive()
        if status != "ST":
          raise RuntimeError(f"Reader not in standby: {status!r}")
        if self._buffer:
          raise RuntimeError("Unexpected pending input after standby response")
        await self.usb.write(_encode("INIT FORCE"))
        status = await self._receive()
        if not re.fullmatch(r"\+?BY(?:#.[0-9]+)?", status):
          raise RuntimeError(f"Unexpected initialization acknowledgment: {status!r}")
        status = await self._receive(timeout=150)
        if status != "ST":
          raise RuntimeError(f"Unexpected initialization completion: {status!r}")
        if self._buffer:
          raise RuntimeError("Unexpected pending input after initialization completion")
        self._initialized = True
        logger.warning("Reader reported initialization complete; physical safety is not verified")
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Initialization failed; reader state is uncertain")
        raise

  async def _position(self) -> str:
    """Query the carrier's reported logical position."""
    await self.usb.write(_encode("?ABSOLUTE MTP,POS"))
    return await self._receive()

  async def _move(self, target: Literal["IN", "OUT"]) -> None:
    """Move to a known state and verify the instrument-reported position."""
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader is not ready; call setup first")
      if not self._initialized:
        raise RuntimeError("Reader is not initialized; call initialize first")
      try:
        position = await self._position()
        if position == target:
          return
        if position not in ("IN", "OUT"):
          raise RuntimeError(f"Unexpected carrier position {position!r}; refusing to move")
        logger.info("Moving carrier to %s", target)
        await self.usb.write(_encode(f"ABSOLUTE MTP,{target}"))
        status = await self._receive()
        if re.fullmatch(r"\+?BY(?:#.[0-9]+)?", status):
          status = await self._receive()
        if status != "ST":
          raise RuntimeError(f"Unexpected movement status: {status!r}")
        position = await self._position()
        if position != target:
          raise RuntimeError(f"Carrier reports {position!r}, not {target!r}")
        logger.info("Reader reports carrier %s", target)
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Reader operation failed; connection state is uncertain")
        raise

  async def open(self) -> None:
    """Move the plate carrier out, unless it is already reported out."""
    await self._move("OUT")

  async def close(self) -> None:
    """Move the plate carrier in, unless it is already reported in."""
    await self._move("IN")
