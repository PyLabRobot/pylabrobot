import ctypes
import logging
from io import IOBase
from typing import TYPE_CHECKING, Optional

try:
  from pylibftdi import Device

  HAS_PYLIBFTDI = True
except ImportError:
  HAS_PYLIBFTDI = False

from pylabrobot.io.validation_utils import LOG_LEVEL_IO, ValidationError, align_sequences

if TYPE_CHECKING:
  from pylabrobot.io.validation import LogReader


logger = logging.getLogger(__name__)


class FTDI(IOBase):
  """Thin wrapper around pylibftdi to include PLR logging (for io testing)."""

  def __init__(self, device_id: Optional[str] = None):
    self._device_id = device_id
    self._dev = Device(lazy_open=True, device_id=device_id)

  async def setup(self):
    if not HAS_PYLIBFTDI:
      raise RuntimeError("pyserial not installed.")

    self._dev.open()

  def set_baudrate(self, baudrate: int):
    self._dev.baudrate = baudrate

  def set_rts(self, level: bool):
    self._dev.ftdi_fn.setrts(level)
    logger.log(LOG_LEVEL_IO, "[%s] set_rts %s", self._device_id, level)

  def set_dtr(self, level: bool):
    self._dev.ftdi_fn.setdtr(level)
    logger.log(LOG_LEVEL_IO, "[%s] set_dtr %s", self._device_id, level)

  def set_latency_timer(self, latency: int):
    self._dev.ftdi_fn.set_latency_timer(latency)
    logger.log(LOG_LEVEL_IO, "[%s] set_latency_timer %s", self._device_id, latency)

  def set_line_property(self, bits: int, stopbits: int, parity: int):
    self._dev.ftdi_fn.set_line_property(bits, stopbits, parity)
    logger.log(
      LOG_LEVEL_IO, "[%s] set_line_property %s,%s,%s", self._device_id, bits, stopbits, parity
    )

  def set_flowctrl(self, flowctrl: int):
    self._dev.ftdi_fn.setflowctrl(flowctrl)
    logger.log(LOG_LEVEL_IO, "[%s] set_flowctrl %s", self._device_id, flowctrl)

  def usb_purge_rx_buffer(self):
    self._dev.ftdi_fn.ftdi_usb_purge_rx_buffer()
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_rx_buffer", self._device_id)

  def usb_purge_tx_buffer(self):
    self._dev.ftdi_fn.ftdi_usb_purge_tx_buffer()
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_tx_buffer", self._device_id)

  def poll_modem_status(self) -> int:
    stat = ctypes.c_ushort(0)
    self._dev.ftdi_fn.ftdi_poll_modem_status(ctypes.byref(stat))
    logger.log(LOG_LEVEL_IO, "[%s] poll_modem_status %s", self._device_id, stat.value)
    return stat.value

  async def stop(self):
    self._dev.close()

  def write(self, data: bytes) -> int:
    """Write data to the device. Returns the number of bytes written."""
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._device_id, data)
    return self._dev.write(data)

  def read(self, num_bytes: int = 1) -> bytes:
    data = self._dev.read(num_bytes)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._device_id, data)
    return data

  def readline(self) -> bytes:
    data = self._dev.readline()
    logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._device_id, data)
    return data

  def serialize(self):
    return {"port": self._device_id}


class FTDIValidator(FTDI):
  def __init__(self, lr: "LogReader", device_id: str):
    super().__init__(device_id=device_id)
    self.lr = lr

  async def setup(self):
    pass

  def set_baudrate(self, baudrate: int):
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "set_baudrate":
      raise ValidationError(f"next command is {action}, expected 'set_baudrate'")

    if not int(log_data) == baudrate:
      raise ValidationError(f"Expected baudrate to be {baudrate}, got {log_data}")

  def set_rts(self, level: bool):
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "set_rts":
      raise ValidationError(f"next command is {action}, expected 'set_rts'")

    if not bool(log_data) == level:
      raise ValidationError(f"Expected rts to be {level}, got {log_data}")

  def set_dtr(self, level: bool):
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "set_dtr":
      raise ValidationError(f"next command is {action}, expected 'set_dtr'")

    if not bool(log_data) == level:
      raise ValidationError(f"Expected dtr to be {level}, got {log_data}")

  def set_latency_timer(self, latency: int):
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "set_latency_timer":
      raise ValidationError(f"next command is {action}, expected 'set_latency_timer'")

    if not int(log_data) == latency:
      raise ValidationError(f"Expected latency to be {latency}, got {log_data}")

  def set_line_property(self, bits: int, stopbits: int, parity: int):
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "set_line_property":
      raise ValidationError(f"next command is {action}, expected 'set_line_property'")

    log_data = log_data.split(",")
    if not int(log_data[0]) == bits:
      raise ValidationError(f"Expected bits to be {bits}, got {log_data[0]}")
    if not int(log_data[1]) == stopbits:
      raise ValidationError(f"Expected stopbits to be {stopbits}, got {log_data[1]}")
    if not int(log_data[2]) == parity:
      raise ValidationError(f"Expected parity to be {parity}, got {log_data[2]}")

  def set_flowctrl(self, flowctrl: int):
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "set_flowctrl":
      raise ValidationError(f"next command is {action}, expected 'set_flowctrl'")

    if not int(log_data) == flowctrl:
      raise ValidationError(f"Expected flowctrl to be {flowctrl}, got {log_data}")

  def usb_purge_rx_buffer(self):
    next_line = self.lr.next_line()
    port, action = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "usb_purge_rx_buffer":
      raise ValidationError(f"next command is {action}, expected 'usb_purge_rx_buffer'")

  def usb_purge_tx_buffer(self):
    next_line = self.lr.next_line()
    port, action = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "usb_purge_tx_buffer":
      raise ValidationError(f"next command is {action}, expected 'usb_purge_tx_buffer'")

  def poll_modem_status(self) -> int:
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")
    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "poll_modem_status":
      raise ValidationError(f"next command is {action}, expected 'poll_modem_status'")

    return int(log_data)

  def write(self, data: bytes):
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")

    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "write":
      raise ValidationError(f"next command is {action}, expected 'write'")

    if not log_data == data:
      align_sequences(expected=log_data, actual=data)
      raise ValidationError("Data mismatch: difference was written to stdout.")

  def read(self, num_bytes: int = 1) -> bytes:
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")  # remove the colon at the end

    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "read":
      raise ValidationError(f"next command is {action}, expected 'read'")

    if not len(log_data) == num_bytes:
      raise ValidationError(f"Expected to read {num_bytes} bytes, got {len(log_data)} bytes")

    return log_data.encode()

  def readline(self) -> bytes:
    next_line = self.lr.next_line()
    port, action, log_data = next_line.split(" ", 2)
    action = action.rstrip(":")  # remove the colon at the end

    if not port == self._device_id:
      raise ValidationError(
        f"next command is sent to device with port {port}, expected {self._device_id}"
      )

    if not action == "readline":
      raise ValidationError(f"next command is {action}, expected 'readline'")

    return log_data.encode()
