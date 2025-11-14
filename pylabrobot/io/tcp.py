import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

logger = logging.getLogger(__name__)


@dataclass
class TCPCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str, module: str = "tcp"):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data


class TCP(IOBase):
  def __init__(self, host: str, port: int = 5000):
    self._host = host
    self._port = port
    self._reader: Optional[asyncio.StreamReader] = None
    self._writer: Optional[asyncio.StreamWriter] = None
    self._read_buffer = bytearray()

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new TCP object while capture or validation is active")

  async def setup(self):
    await self._open_connection()
    self._read_buffer = bytearray()

  async def _open_connection(self):
    self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

  async def stop(self):
    if self._writer is not None:
      self._writer.close()
      await self._writer.wait_closed()
      self._reader = None
      self._writer = None

  async def write(self, data: bytes, num_tries: int = 3):
    assert self._writer is not None, "forgot to call setup?"

    last_exc: Optional[Exception] = None

    for attempt in range(num_tries):
      try:
        self._writer.write(data + b"\n")  # TODO: this should be a part of PF400, not io.TCP
        await self._writer.drain()
      except (ConnectionResetError, BrokenPipeError, OSError) as exc:
        last_exc = exc
        logger.warning(
          "TCP write failed with %r on attempt %d/%d; reopening connection",
          exc,
          attempt + 1,
          num_tries,
        )
        await self._open_connection()
        assert self._writer is not None, "_open_connection() failed to set _writer"
      else:  # success
        logger.log(LOG_LEVEL_IO, "[%s:%d] write %s", self._host, self._port, data)
        capturer.record(
          TCPCommand(
            device_id=f"{self._host}:{self._port}",
            action="write",
            data=data.hex(),
          )
        )
        return

    raise ConnectionResetError(f"Max number of retries reached ({num_tries})") from last_exc

  async def _raw_read(self, num_bytes: int, num_tries: int) -> bytes:
    """Single low-level read with retries; does not use buffer."""
    assert self._reader is not None, "forgot to call setup?"

    last_exc: Optional[Exception] = None

    for attempt in range(num_tries):
      try:
        data = await self._reader.read(num_bytes)
        if data == b"":
          return b""  # EOF
      except (ConnectionResetError, OSError) as exc:
        last_exc = exc
        logger.warning(
          "TCP read failed with %r on attempt %d/%d; reopening connection",
          exc,
          attempt + 1,
          num_tries,
        )
        await self._open_connection()
        assert self._reader is not None, "_open_connection() failed to set _reader"
        continue

      logger.log(LOG_LEVEL_IO, "[%s:%d] read %s", self._host, self._port, data)
      capturer.record(
        TCPCommand(
          device_id=f"{self._host}:{self._port}",
          action="read",
          data=data.hex(),
        )
      )
      return data

    raise ConnectionResetError(f"Max number of read retries reached ({num_tries})") from last_exc

  async def read(self, num_bytes: int = 128, num_tries: int = 3) -> bytes:
    assert self._reader is not None, "forgot to call setup?"

    if num_bytes <= 0:
      return b""

    # Fill buffer until we have enough bytes.

    while len(self._read_buffer) < num_bytes:
      chunk = await self._raw_read(num_bytes - len(self._read_buffer), num_tries)
      self._read_buffer.extend(chunk)
      if len(chunk) == 0:  # EOF
        break

    if len(self._read_buffer) < num_bytes:
      raise TimeoutError(f"Timeout while waiting for {num_bytes} bytes")

    # Consume from buffer, or return empty if buffer is empty.
    if len(self._read_buffer) == 0:
      return b""
    chunk = bytes(self._read_buffer[:num_bytes])
    del self._read_buffer[:num_bytes]
    return chunk

  async def readline(
    self, num_tries: int = 3, timeout: float = 60, line_ending: bytes = b"\r\n"
  ) -> bytes:
    assert self._reader is not None, "forgot to call setup?"

    CHUNK = 1024

    timeout_time = time.time() + timeout

    while time.time() < timeout_time:
      idx = self._read_buffer.find(line_ending)
      if idx != -1:
        end = idx + len(line_ending)
        line = bytes(self._read_buffer[:end])
        del self._read_buffer[:end]
        return line

      chunk = await self._raw_read(num_bytes=CHUNK, num_tries=num_tries)
      self._read_buffer.extend(chunk)
      if len(chunk) == 0:  # EOF; return what we have
        line = bytes(self._read_buffer)
        self._read_buffer.clear()
        return line

    raise TimeoutError(f"Timeout while waiting for line ending with '{line_ending.decode()}'")

  def serialize(self):
    return {
      "host": self._host,
      "port": self._port,
    }

  @classmethod
  def deserialize(cls, data: dict) -> "TCP":
    return cls(
      host=data["host"],
      port=data["port"],
    )


class TCPValidator(TCP):
  def __init__(self, cr: CaptureReader, host: str, port: int = 5000):
    super().__init__(host, port)
    self.cr = cr

  async def setup(self):
    pass

  async def write(self, data: bytes):
    next_command = TCPCommand(**self.cr.next_command())
    if not (
      next_command.module == "tcp"
      and next_command.device_id == f"{self._host}:{self._port}"
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected TCP write")
    if next_command.data != data.decode("unicode_escape"):
      align_sequences(expected=next_command.data, actual=data.decode("unicode_escape"))
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def read(self, num_bytes: int = 128) -> bytes:
    next_command = TCPCommand(**self.cr.next_command())
    if not (
      next_command.module == "tcp"
      and next_command.device_id == f"{self._host}:{self._port}"
      and next_command.action == "read"
      and len(next_command.data) == num_bytes
    ):
      raise ValidationError(f"Next line is {next_command}, expected TCP read {num_bytes}")
    return bytes.fromhex(next_command.data)
