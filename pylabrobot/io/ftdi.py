import asyncio
import ctypes
import logging
from concurrent.futures import ThreadPoolExecutor
from io import IOBase
from typing import Optional, cast

try:
  from pylibftdi import Device

  HAS_PYLIBFTDI = True
except ImportError as e:
  HAS_PYLIBFTDI = False
  _FTDI_IMPORT_ERROR = e

from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

logger = logging.getLogger(__name__)


class FTDICommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str):
    super().__init__(module="ftdi", device_id=device_id, action=action)


class FTDI(IOBase):
  """Thin wrapper around pylibftdi to include PLR logging (for io testing)."""

  def __init__(self, device_id: Optional[str] = None):
    self._dev = Device(lazy_open=True, device_id=device_id)
    self._device_id = device_id or "None"  # for io
    self._executor: Optional[ThreadPoolExecutor] = None
    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new FTDI object while capture or validation is active")

  async def setup(self):
    if not HAS_PYLIBFTDI:
      raise RuntimeError(f"pylibftdi not installed. Import error: {_FTDI_IMPORT_ERROR}")
    self._dev.open()
    self._executor = ThreadPoolExecutor(max_workers=1)

  async def set_baudrate(self, baudrate: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: setattr(self._dev, "baudrate", baudrate))

  async def set_rts(self, level: bool):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self._dev.ftdi_fn.ftdi_setrts(level))
    logger.log(LOG_LEVEL_IO, "[%s] set_rts %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self._device_id, action="set_rts", data=str(level)))

  async def set_dtr(self, level: bool):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self._dev.ftdi_fn.ftdi_setdtr(level))
    logger.log(LOG_LEVEL_IO, "[%s] set_dtr %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self._device_id, action="set_dtr", data=str(level)))

  async def usb_reset(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self._dev.ftdi_fn.ftdi_usb_reset())
    logger.log(LOG_LEVEL_IO, "[%s] usb_reset", self._device_id)
    capturer.record(FTDICommand(device_id=self._device_id, action="usb_reset", data=""))

  async def set_latency_timer(self, latency: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
      self._executor, lambda: self._dev.ftdi_fn.ftdi_set_latency_timer(latency)
    )
    logger.log(LOG_LEVEL_IO, "[%s] set_latency_timer %s", self._device_id, latency)
    capturer.record(
      FTDICommand(device_id=self._device_id, action="set_latency_timer", data=str(latency))
    )

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
      self._executor, lambda: self._dev.ftdi_fn.ftdi_set_line_property(bits, stopbits, parity)
    )
    logger.log(
      LOG_LEVEL_IO, "[%s] set_line_property %s,%s,%s", self._device_id, bits, stopbits, parity
    )
    capturer.record(
      FTDICommand(
        device_id=self._device_id, action="set_line_property", data=f"{bits},{stopbits},{parity}"
      )
    )

  async def set_flowctrl(self, flowctrl: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self._dev.ftdi_fn.ftdi_setflowctrl(flowctrl))
    logger.log(LOG_LEVEL_IO, "[%s] set_flowctrl %s", self._device_id, flowctrl)
    capturer.record(
      FTDICommand(device_id=self._device_id, action="set_flowctrl", data=str(flowctrl))
    )

  async def usb_purge_rx_buffer(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self._dev.ftdi_fn.ftdi_usb_purge_rx_buffer())
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_rx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self._device_id, action="usb_purge_rx_buffer", data=""))

  async def usb_purge_tx_buffer(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self._dev.ftdi_fn.ftdi_usb_purge_tx_buffer())
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_tx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self._device_id, action="usb_purge_tx_buffer", data=""))

  async def poll_modem_status(self) -> int:
    loop = asyncio.get_running_loop()
    stat = ctypes.c_ushort(0)
    await loop.run_in_executor(
      self._executor, lambda: self._dev.ftdi_fn.ftdi_poll_modem_status(ctypes.byref(stat))
    )
    logger.log(LOG_LEVEL_IO, "[%s] poll_modem_status %s", self._device_id, stat.value)
    capturer.record(
      FTDICommand(device_id=self._device_id, action="poll_modem_status", data=str(stat.value))
    )
    return stat.value

  async def stop(self):
    self._dev.close()
    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  async def write(self, data: bytes) -> int:
    """Write data to the device. Returns the number of bytes written."""
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._device_id, data)
    capturer.record(FTDICommand(device_id=self._device_id, action="write", data=data.hex()))
    return cast(int, self._dev.write(data))

  async def read(self, num_bytes: int = 1) -> bytes:
    data = self._dev.read(num_bytes)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._device_id, data)
    capturer.record(
      FTDICommand(
        device_id=self._device_id,
        action="read",
        data=data if isinstance(data, str) else data.hex(),
      )
    )
    return cast(bytes, data)

  async def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    data = self._dev.readline()
    logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._device_id, data)
    capturer.record(FTDICommand(device_id=self._device_id, action="readline", data=data.hex()))
    return cast(bytes, data)

  def serialize(self):
    return {"port": self._device_id}


class FTDIValidator(FTDI):
  def __init__(self, cr: "CaptureReader", device_id: str):
    super().__init__(device_id=device_id)
    self.cr = cr

  async def setup(self):
    pass

  async def set_baudrate(self, baudrate: int):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_baudrate"
      and int(next_command.data) == baudrate
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI set_baudrate {baudrate}")

  async def set_rts(self, level: bool):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_rts"
      and next_command.data == str(level)
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI set_rts {level}")

  async def set_dtr(self, level: bool):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_dtr"
      and next_command.data == str(level)
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI set_dtr {level}")

  async def usb_reset(self):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "usb_reset"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI usb_reset {self._device_id}"
      )

  async def set_latency_timer(self, latency: int):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_latency_timer"
      and int(next_command.data) == latency
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI set_latency_timer {latency}"
      )

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_line_property"
      and next_command.data == f"{bits},{stopbits},{parity}"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI set_line_property {bits},{stopbits},{parity}"
      )

  async def set_flowctrl(self, flowctrl: int):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_flowctrl"
      and int(next_command.data) == flowctrl
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI set_flowctrl {flowctrl}")

  async def usb_purge_rx_buffer(self):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "usb_purge_rx_buffer"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI usb_purge_rx_buffer {self._device_id}"
      )

  async def usb_purge_tx_buffer(self):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "usb_purge_tx_buffer"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI usb_purge_tx_buffer {self._device_id}"
      )

  async def poll_modem_status(self) -> int:
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "poll_modem_status"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI poll_modem_status {self._device_id}"
      )
    return int(next_command.data)

  async def write(self, data: bytes):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI write {self._device_id}")
    if not next_command.data == data.hex():
      align_sequences(expected=next_command.data, actual=data.hex())
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def read(self, num_bytes: int = 1) -> bytes:
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "read"
      and len(next_command.data) == num_bytes
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI read {self._device_id}")
    return bytes.fromhex(next_command.data)

  async def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "readline"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI readline {self._device_id}"
      )
    return bytes.fromhex(next_command.data)
