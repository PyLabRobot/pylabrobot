import asyncio
import logging
from dataclasses import dataclass
from pylabrobot.io.io import IOBase
from typing import Optional

from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
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

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new TCP object while capture or validation is active")

  async def setup(self):
    self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

  async def stop(self):
    if self._writer is not None:
      self._writer.close()
      await self._writer.wait_closed()
      self._reader = None
      self._writer = None

  async def write(self, data: bytes):
    assert self._writer is not None, "forgot to call setup?"
    self._writer.write(data + b"\n")
    await self._writer.drain()
    logger.log(LOG_LEVEL_IO, "[%s:%d] write %s", self._host, self._port, data)
    capturer.record(
      TCPCommand(
        device_id=f"{self._host}:{self._port}", action="write", data=data.decode("unicode_escape")
      )
    )

  async def read(self, num_bytes: int = -1) -> bytes:
    assert self._reader is not None, "forgot to call setup?"
    data = await self._reader.read(num_bytes)
    logger.log(LOG_LEVEL_IO, "[%s:%d] read %s", self._host, self._port, data)
    capturer.record(
      TCPCommand(
        device_id=f"{self._host}:{self._port}", action="read", data=data.decode("unicode_escape")
      )
    )
    return data

  async def readline(self) -> bytes:
    assert self._reader is not None, "forgot to call setup?"

    data = await self._reader.read(128)
    last_line = data.split(b"\r\n")[0]  # fix for errors with multiplate lines returned
    last_line += b"\r\n"

    logger.log(LOG_LEVEL_IO, "[%s:%d] readline %s", self._host, self._port, last_line)
    capturer.record(
      TCPCommand(
        device_id=f"{self._host}:{self._port}",
        action="readline",
        data=last_line.decode("unicode_escape"),
      )
    )
    return last_line

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
    return next_command.data.encode()

  async def readline(self) -> bytes:
    next_command = TCPCommand(**self.cr.next_command())
    if not (
      next_command.module == "tcp"
      and next_command.device_id == f"{self._host}:{self._port}"
      and next_command.action == "readline"
    ):
      raise ValidationError(f"Next line is {next_command}, expected TCP readline")
    return next_command.data.encode()
