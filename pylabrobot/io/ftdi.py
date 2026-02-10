import asyncio
import ctypes
import logging
from concurrent.futures import ThreadPoolExecutor
from io import IOBase
from typing import Optional, cast

try:
  import pylibftdi.driver
  from pylibftdi import Device, FtdiError

  HAS_PYLIBFTDI = True
except ImportError as e:
  HAS_PYLIBFTDI = False
  _FTDI_ERROR = e

try:
  import usb.core
  import usb.util

  HAS_PYUSB = True
except ImportError as e:
  HAS_PYUSB = False
  _PYUSB_ERROR = e

from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

logger = logging.getLogger(__name__)


class FTDICommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str):
    super().__init__(module="ftdi", device_id=device_id, action=action)


class FTDI(IOBase):
  """Thin wrapper around pylibftdi with device resolution and logging.

  Finds devices based on the following parameters:
  1. device_id - serial number for explicit connection
  2. VID:PID - works for single device of that model

  If no devices match, an error is raised.
  If multiple devices match the criteria, an error is raised.

  Args:
    device_id: Device identifier (serial number)
    vid: USB Vendor ID
    pid: USB Product ID
  """

  def __init__(
    self,
    device_id: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    interface_select: Optional[int] = None,
  ):
    if not HAS_PYLIBFTDI:
      global _FTDI_ERROR
      raise RuntimeError(f"pylibftdi not installed. Import error: {_FTDI_ERROR}")
    if not HAS_PYUSB:
      global _PYUSB_ERROR
      raise RuntimeError(f"pyusb not installed. Import error: {_PYUSB_ERROR}")

    self._device_id = device_id
    self._vid = vid
    self._pid = pid
    self._interface_select = interface_select

    # Will be resolved in setup()
    self._dev: Optional[Device] = None
    self._executor: Optional[ThreadPoolExecutor] = None

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new FTDI object while capture or validation is active")

  @property
  def dev(self) -> "Device":
    if self._dev is None:
      raise RuntimeError("Device not initialized. Call setup() first.")
    return self._dev

  def _resolve_device_serial(self) -> str:
    """List connected FTDI devices and resolve which one to connect to based on parameters.

    If no devices match, an error is raised.
    If multiple devices match the criteria, an error is raised.

    We have to use pyusb to list devices, as pylibftdi does not provide a way to list devices with custom vid/pid.

    Returns:
      The serial number of the resolved device.
    """

    # loop over all connected FTDI devices
    # what constitutes an "FTDI device" is having a VID/PID in the pylibftdi list, or matching the provided VID/PID
    # we use the other provided parameters to narrow the list of candidates
    search_kwargs = {}
    if self._vid is not None:
      search_kwargs["idVendor"] = self._vid
    if self._pid is not None:
      search_kwargs["idProduct"] = self._pid
    usb_devices = usb.core.find(find_all=True, **search_kwargs)

    candidates = []

    for device in usb_devices:
      # check if device is FTDI by VID/PID
      if self._vid is None and device.idVendor not in pylibftdi.driver.USB_VID_LIST:
        continue
      elif self._vid is not None and device.idVendor != self._vid:
        continue

      if self._pid is None and device.idProduct not in pylibftdi.driver.USB_PID_LIST:
        continue
      elif self._pid is not None and device.idProduct != self._pid:
        continue

      # check device_id (serial number) if provided
      device_serial_number = usb.util.get_string(device, device.iSerialNumber)
      if self._device_id is not None and device_serial_number != self._device_id:
        continue

      # device matches all specified criteria
      candidates.append(device)

    connected_devices_list = []
    for d in usb.core.find(find_all=True):
      try:
        sn = usb.util.get_string(d, d.iSerialNumber)
      except ValueError:
        sn = ""
      connected_devices_list.append(f"{sn} (VID:PID {d.idVendor:04x}:{d.idProduct:04x})")

    connected_devices_string = ", ".join(connected_devices_list)

    logger.debug(
      f"FTDI device resolution: found {len(candidates)} candidates for "
      f"VID:PID {self._vid}:{self._pid}, device_id {self._device_id}: " + connected_devices_string
    )

    vid_string = f"{self._vid:04x}" if self._vid is not None else "any"
    pid_string = f"{self._pid:04x}" if self._pid is not None else "any"

    if len(candidates) == 0:
      raise RuntimeError(
        f"No FTDI devices found with specified criteria: "
        f"VID:PID {vid_string}:{pid_string}, "
        f"device_id {self._device_id}. "
        "Connected devices: " + connected_devices_string
      )

    if len(candidates) > 1:
      raise RuntimeError(
        f"Multiple FTDI devices found with specified criteria: "
        f"VID:PID {vid_string}:{pid_string}, "
        f"device_id {self._device_id}. "
        f"Please specify the device_id parameter explicitly with the serial number of the desired device."
      )

    # Exactly one candidate found
    device = candidates[0]
    device_serial_number = cast(str, usb.util.get_string(device, device.iSerialNumber))
    return device_serial_number

  async def setup(self):
    """Initialize the FTDI device connection with device resolution."""
    if self._dev is not None and not self._dev.closed:
      self._dev.close()
    try:
      # Resolve which device to connect to
      self._device_id = self._resolve_device_serial()

      # Create and open device
      self._dev = Device(
        lazy_open=True,
        device_id=self.device_id,
        pid=self._pid,
        vid=self._vid,
        interface_select=self._interface_select,
      )
      self._dev.open()
      logger.info(f"Successfully opened FTDI device: {self.device_id}")
    except FtdiError as e:
      raise RuntimeError(
        f"Failed to open FTDI device: {e}. "
        "Is the device connected? Is it in use by another process? "
        "Try restarting the kernel."
      ) from e

    self._executor = ThreadPoolExecutor(max_workers=1)

  @property
  def device_id(self) -> str:
    if self._device_id is None:
      raise RuntimeError("Device not initialized. Call setup() first.")
    return self._device_id

  async def set_baudrate(self, baudrate: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: setattr(self.dev, "baudrate", baudrate))
    logger.log(LOG_LEVEL_IO, "[%s] set_baudrate %s", self._device_id, baudrate)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_baudrate", data=str(baudrate))
    )

  async def set_rts(self, level: bool):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setrts(level))
    logger.log(LOG_LEVEL_IO, "[%s] set_rts %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_rts", data=str(level)))

  async def set_dtr(self, level: bool):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setdtr(level))
    logger.log(LOG_LEVEL_IO, "[%s] set_dtr %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_dtr", data=str(level)))

  async def usb_reset(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_reset())
    logger.log(LOG_LEVEL_IO, "[%s] usb_reset", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_reset", data=""))

  async def set_latency_timer(self, latency: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
      self._executor, lambda: self.dev.ftdi_fn.ftdi_set_latency_timer(latency)
    )
    logger.log(LOG_LEVEL_IO, "[%s] set_latency_timer %s", self._device_id, latency)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_latency_timer", data=str(latency))
    )

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
      self._executor, lambda: self.dev.ftdi_fn.ftdi_set_line_property(bits, stopbits, parity)
    )
    logger.log(
      LOG_LEVEL_IO, "[%s] set_line_property %s,%s,%s", self._device_id, bits, stopbits, parity
    )
    capturer.record(
      FTDICommand(
        device_id=self.device_id, action="set_line_property", data=f"{bits},{stopbits},{parity}"
      )
    )

  async def set_flowctrl(self, flowctrl: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setflowctrl(flowctrl))
    logger.log(LOG_LEVEL_IO, "[%s] set_flowctrl %s", self._device_id, flowctrl)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_flowctrl", data=str(flowctrl))
    )

  async def usb_purge_rx_buffer(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_purge_rx_buffer())
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_rx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_rx_buffer", data=""))

  async def usb_purge_tx_buffer(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_purge_tx_buffer())
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_tx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_tx_buffer", data=""))

  async def poll_modem_status(self) -> int:
    loop = asyncio.get_running_loop()
    stat = ctypes.c_ushort(0)
    await loop.run_in_executor(
      self._executor, lambda: self.dev.ftdi_fn.ftdi_poll_modem_status(ctypes.byref(stat))
    )
    logger.log(LOG_LEVEL_IO, "[%s] poll_modem_status %s", self._device_id, stat.value)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="poll_modem_status", data=str(stat.value))
    )
    return stat.value

  async def get_serial(self) -> str:
    return self.device_id

  async def stop(self):
    if self._dev is not None:
      self.dev.close()
    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  async def write(self, data: bytes) -> int:
    """Write data to the device. Returns the number of bytes written."""
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._device_id, data)
    capturer.record(FTDICommand(device_id=self.device_id, action="write", data=data.hex()))
    return cast(int, self.dev.write(data))

  async def read(self, num_bytes: int = 1) -> bytes:
    data = self.dev.read(num_bytes)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._device_id, data)
    capturer.record(
      FTDICommand(
        device_id=self.device_id,
        action="read",
        data=data if isinstance(data, str) else data.hex(),
      )
    )
    return cast(bytes, data)

  async def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    data = self.dev.readline()
    logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._device_id, data)
    capturer.record(FTDICommand(device_id=self.device_id, action="readline", data=data.hex()))
    return cast(bytes, data)

  def serialize(self):
    return {
      "device_id": self._device_id,
      "vid": self._vid,
      "pid": self._pid,
    }


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
    return len(data)

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
