import logging
from dataclasses import dataclass
from io import IOBase
from typing import Optional, cast

from pylabrobot.io.errors import ValidationError

try:
  import serial

  HAS_SERIAL = True
except ImportError:
  HAS_SERIAL = False

from pylabrobot.io.capture import CaptureReader, Command, capturer
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

logger = logging.getLogger(__name__)


@dataclass
class SerialCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str):
    super().__init__(module="serial", device_id=device_id, action=action)
    self.data = data


class Serial(IOBase):
  """Thin wrapper around serial.Serial to include PLR logging (for io testing)."""

  def __init__(
    self,
    port: str,
    baudrate: int = 9600,
    bytesize: int = 8,  # serial.EIGHTBITS
    parity: str = "N",  # serial.PARITY_NONE
    stopbits: int = 1,  # serial.STOPBITS_ONE,
    write_timeout=1,
    timeout=1,
  ):
    self._port = port
    self.baudrate = baudrate
    self.bytesize = bytesize
    self.parity = parity
    self.stopbits = stopbits
    self.ser: Optional[serial.Serial] = None
    self.write_timeout = write_timeout
    self.timeout = timeout

  async def setup(self):
    try:
      self.ser = serial.Serial(
        port=self._port,
        baudrate=self.baudrate,
        bytesize=self.bytesize,
        parity=self.parity,
        stopbits=self.stopbits,
        write_timeout=self.write_timeout,
        timeout=self.timeout,
      )
    except serial.SerialException as e:
      logger.error("Could not connect to device, is it in use by a different notebook/process?")
      raise e

  async def stop(self):
    if self.ser is not None and self.ser.is_open:
      self.ser.close()

  def write(self, data: bytes):
    assert self.ser is not None, "forgot to call setup?"
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self._port, action="write", data=data.decode("unicode_escape"))
    )
    self.ser.write(data)

  def read(self, num_bytes: int = 1) -> bytes:
    assert self.ser is not None, "forgot to call setup?"
    data = self.ser.read(num_bytes)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self._port, action="read", data=data.decode("unicode_escape"))
    )
    return cast(bytes, data)

  def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    assert self.ser is not None, "forgot to call setup?"
    data = self.ser.readline()
    logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self._port, action="readline", data=data.decode("unicode_escape"))
    )
    return cast(bytes, data)


class SerialValidator(Serial):
  def __init__(
    self,
    cr: "CaptureReader",
    port: str,
    baudrate: int = 9600,
    bytesize: int = 8,  # serial.EIGHTBITS
    parity: str = "N",  # serial.PARITY_NONE
    stopbits: int = 1,  # serial.STOPBITS_ONE,
  ):
    super().__init__(
      port=port,
      baudrate=baudrate,
      bytesize=bytesize,
      parity=parity,
      stopbits=stopbits,
    )
    self.cr = cr

  async def setup(self):
    pass

  def write(self, data: bytes):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial write")
    if next_command.data != data.decode("unicode_escape"):
      align_sequences(expected=next_command.data, actual=data.decode("unicode_escape"))
      raise ValidationError("Data mismatch: difference was written to stdout.")

  def read(self, num_bytes: int = 1) -> bytes:
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "read"
      and len(next_command.data) == num_bytes
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial read {num_bytes}")
    return next_command.data.encode()

  def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "readline"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial readline")
    return next_command.data.encode()
