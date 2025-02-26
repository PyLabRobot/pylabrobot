import ctypes
import logging
from io import IOBase
from typing import Optional, cast

try:
  from pylibftdi import Device

  HAS_PYLIBFTDI = True
except ImportError:
  HAS_PYLIBFTDI = False

from pylabrobot.io.capture import CaptureReader, Command, capturer
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

  async def setup(self):
    if not HAS_PYLIBFTDI:
      raise RuntimeError("pyserial not installed.")

    self._dev.open()

  def set_baudrate(self, baudrate: int):
    self._dev.baudrate = baudrate

  def set_rts(self, level: bool):
    self._dev.ftdi_fn.ftdi_setrts(level)
    logger.log(LOG_LEVEL_IO, "[%s] set_rts %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self._device_id, action="set_rts", data=str(level)))

  def set_dtr(self, level: bool):
    self._dev.ftdi_fn.ftdi_setdtr(level)
    logger.log(LOG_LEVEL_IO, "[%s] set_dtr %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self._device_id, action="set_dtr", data=str(level)))

  def usb_reset(self):
    self._dev.ftdi_fn.ftdi_usb_reset()
    logger.log(LOG_LEVEL_IO, "[%s] usb_reset", self._device_id)
    capturer.record(FTDICommand(device_id=self._device_id, action="usb_reset", data=""))

  def set_latency_timer(self, latency: int):
    self._dev.ftdi_fn.ftdi_set_latency_timer(latency)
    logger.log(LOG_LEVEL_IO, "[%s] set_latency_timer %s", self._device_id, latency)
    capturer.record(
      FTDICommand(device_id=self._device_id, action="set_latency_timer", data=str(latency))
    )

  def set_line_property(self, bits: int, stopbits: int, parity: int):
    self._dev.ftdi_fn.ftdi_set_line_property(bits, stopbits, parity)
    logger.log(
      LOG_LEVEL_IO, "[%s] set_line_property %s,%s,%s", self._device_id, bits, stopbits, parity
    )
    capturer.record(
      FTDICommand(
        device_id=self._device_id, action="set_line_property", data=f"{bits},{stopbits},{parity}"
      )
    )

  def set_flowctrl(self, flowctrl: int):
    self._dev.ftdi_fn.ftdi_setflowctrl(flowctrl)
    logger.log(LOG_LEVEL_IO, "[%s] set_flowctrl %s", self._device_id, flowctrl)
    capturer.record(
      FTDICommand(device_id=self._device_id, action="set_flowctrl", data=str(flowctrl))
    )

  def usb_purge_rx_buffer(self):
    self._dev.ftdi_fn.ftdi_usb_purge_rx_buffer()
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_rx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self._device_id, action="usb_purge_rx_buffer", data=""))

  def usb_purge_tx_buffer(self):
    self._dev.ftdi_fn.ftdi_usb_purge_tx_buffer()
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_tx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self._device_id, action="usb_purge_tx_buffer", data=""))

  def poll_modem_status(self) -> int:
    stat = ctypes.c_ushort(0)
    self._dev.ftdi_fn.ftdi_poll_modem_status(ctypes.byref(stat))
    logger.log(LOG_LEVEL_IO, "[%s] poll_modem_status %s", self._device_id, stat.value)
    capturer.record(
      FTDICommand(device_id=self._device_id, action="poll_modem_status", data=str(stat.value))
    )
    return stat.value

  async def stop(self):
    self._dev.close()

  def write(self, data: bytes) -> int:
    """Write data to the device. Returns the number of bytes written."""
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._device_id, data)
    capturer.record(
      FTDICommand(device_id=self._device_id, action="write", data=data.decode("unicode_escape"))
    )
    return cast(int, self._dev.write(data))

  def read(self, num_bytes: int = 1) -> bytes:
    data = self._dev.read(num_bytes)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._device_id, data)
    capturer.record(
      FTDICommand(
        device_id=self._device_id,
        action="read",
        data=data if isinstance(data, str) else data.decode("unicode_escape"),
      )
    )
    return cast(bytes, data)

  def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    data = self._dev.readline()
    logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._device_id, data)
    capturer.record(
      FTDICommand(device_id=self._device_id, action="readline", data=data.decode("unicode_escape"))
    )
    return cast(bytes, data)

  def serialize(self):
    return {"port": self._device_id}


class FTDIValidator(FTDI):
  def __init__(self, cr: "CaptureReader", device_id: str):
    super().__init__(device_id=device_id)
    self.cr = cr

  async def setup(self):
    pass

  def set_baudrate(self, baudrate: int):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_baudrate"
      and int(next_command.data) == baudrate
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI set_baudrate {baudrate}")

  def set_rts(self, level: bool):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_rts"
      and next_command.data == str(level)
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI set_rts {level}")

  def set_dtr(self, level: bool):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_dtr"
      and next_command.data == str(level)
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI set_dtr {level}")

  def usb_reset(self):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "usb_reset"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI usb_reset {self._device_id}"
      )

  def set_latency_timer(self, latency: int):
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

  def set_line_property(self, bits: int, stopbits: int, parity: int):
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

  def set_flowctrl(self, flowctrl: int):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "set_flowctrl"
      and int(next_command.data) == flowctrl
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI set_flowctrl {flowctrl}")

  def usb_purge_rx_buffer(self):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "usb_purge_rx_buffer"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI usb_purge_rx_buffer {self._device_id}"
      )

  def usb_purge_tx_buffer(self):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "usb_purge_tx_buffer"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI usb_purge_tx_buffer {self._device_id}"
      )

  def poll_modem_status(self) -> int:
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

  def write(self, data: bytes):
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI write {self._device_id}")
    if not next_command.data == data.decode("unicode_escape"):
      align_sequences(expected=next_command.data, actual=data.decode("unicode_escape"))
      raise ValidationError("Data mismatch: difference was written to stdout.")

  def read(self, num_bytes: int = 1) -> bytes:
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "read"
      and len(next_command.data) == num_bytes
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI read {self._device_id}")
    return next_command.data.encode("unicode_escape")

  def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "readline"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI readline {self._device_id}"
      )
    return next_command.data.encode("unicode_escape")
