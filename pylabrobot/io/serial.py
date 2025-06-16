import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import IOBase
from typing import Optional, cast

from pylabrobot.io.errors import ValidationError

try:
  import serial

  HAS_SERIAL = True
except ImportError:
  HAS_SERIAL = False

from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

logger = logging.getLogger(__name__)


@dataclass
class SerialCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str, module: str = "serial"):
    super().__init__(module=module, device_id=device_id, action=action)
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
    rtscts: bool = False,
  ):
    self._port = port
    self.baudrate = baudrate
    self.bytesize = bytesize
    self.parity = parity
    self.stopbits = stopbits
    self._ser: Optional[serial.Serial] = None
    self._executor: Optional[ThreadPoolExecutor] = None
    self.write_timeout = write_timeout
    self.timeout = timeout
    self.rtscts = rtscts

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new Serial object while capture or validation is active")

  @property
  def port(self) -> str:
    return self._port

  async def setup(self):
    if not HAS_SERIAL:
      raise RuntimeError("pyserial not installed.")
    loop = asyncio.get_running_loop()
    self._executor = ThreadPoolExecutor(max_workers=1)

    def _open_serial() -> serial.Serial:
      return serial.Serial(
        port=self._port,
        baudrate=self.baudrate,
        bytesize=self.bytesize,
        parity=self.parity,
        stopbits=self.stopbits,
        write_timeout=self.write_timeout,
        timeout=self.timeout,
        rtscts=self.rtscts,
      )

    try:
      self._ser = await loop.run_in_executor(self._executor, _open_serial)
    except serial.SerialException as e:
      logger.error("Could not connect to device, is it in use by a different notebook/process?")
      if self._executor is not None:
        self._executor.shutdown(wait=True)
        self._executor = None
      raise e

  async def stop(self):
    if self._ser is not None and self._ser.is_open:
      loop = asyncio.get_running_loop()
      if self._executor is None:
        raise RuntimeError("Call setup() first.")
      await loop.run_in_executor(self._executor, self._ser.close)
    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  async def write(self, data: bytes):
    assert self._ser is not None, "forgot to call setup?"
    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    await loop.run_in_executor(self._executor, self._ser.write, data)
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self._port, action="write", data=data.decode("unicode_escape"))
    )

  async def read(self, num_bytes: int = 1) -> bytes:
    assert self._ser is not None, "forgot to call setup?"
    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    data = await loop.run_in_executor(self._executor, self._ser.read, num_bytes)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self._port, action="read", data=data.decode("unicode_escape"))
    )
    return cast(bytes, data)

  async def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    assert self._ser is not None, "forgot to call setup?"
    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    data = await loop.run_in_executor(self._executor, self._ser.readline)
    logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self._port, action="readline", data=data.decode("unicode_escape"))
    )
    return cast(bytes, data)

  async def send_break(self, duration: float):
    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")

    def _send_break(ser, duration: float) -> None:
      """Send a break condition for the specified duration."""
      assert ser is not None, "forgot to call setup?"
      ser.send_break(duration=duration)

    await loop.run_in_executor(self._executor, lambda: _send_break(self._ser, duration=duration))
    logger.log(LOG_LEVEL_IO, "[%s] send_break %s", self._port, duration)
    capturer.record(SerialCommand(device_id=self._port, action="send_break", data=str(duration)))

  async def reset_input_buffer(self):
    assert self._ser is not None, "forgot to call setup?"
    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    await loop.run_in_executor(self._executor, self._ser.reset_input_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] reset_input_buffer", self._port)
    capturer.record(SerialCommand(device_id=self._port, action="reset_input_buffer", data=""))

  async def reset_output_buffer(self):
    assert self._ser is not None, "forgot to call setup?"
    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    await loop.run_in_executor(self._executor, self._ser.reset_output_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] reset_output_buffer", self._port)
    capturer.record(SerialCommand(device_id=self._port, action="reset_output_buffer", data=""))

  def serialize(self):
    return {
      "port": self._port,
      "baudrate": self.baudrate,
      "bytesize": self.bytesize,
      "parity": self.parity,
      "stopbits": self.stopbits,
      "write_timeout": self.write_timeout,
      "timeout": self.timeout,
      "rtscts": self.rtscts,
    }

  @classmethod
  def deserialize(cls, data: dict) -> "Serial":
    return cls(
      port=data["port"],
      baudrate=data["baudrate"],
      bytesize=data["bytesize"],
      parity=data["parity"],
      stopbits=data["stopbits"],
      write_timeout=data["write_timeout"],
      timeout=data["timeout"],
      rtscts=data["rtscts"],
    )


class SerialValidator(Serial):
  def __init__(
    self,
    cr: "CaptureReader",
    port: str,
    baudrate: int = 9600,
    bytesize: int = 8,  # serial.EIGHTBITS
    parity: str = "N",  # serial.PARITY_NONE
    stopbits: int = 1,  # serial.STOPBITS_ONE,
    write_timeout=1,
    timeout=1,
    rtscts: bool = False,
  ):
    super().__init__(
      port=port,
      baudrate=baudrate,
      bytesize=bytesize,
      parity=parity,
      stopbits=stopbits,
      write_timeout=write_timeout,
      timeout=timeout,
      rtscts=rtscts,
    )
    self.cr = cr

  async def setup(self):
    pass

  async def write(self, data: bytes):
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

  async def read(self, num_bytes: int = 1) -> bytes:
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "read"
      and len(next_command.data) == num_bytes
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial read {num_bytes}")
    return next_command.data.encode()

  async def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "readline"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial readline")
    return next_command.data.encode()

  async def send_break(self, duration: float):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "send_break"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial send_break")
    if float(next_command.data) != duration:
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def reset_input_buffer(self):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "reset_input_buffer"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial reset_input_buffer")

  async def reset_output_buffer(self):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "reset_output_buffer"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial reset_output_buffer")
