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

try:
  import serial
  from serial.tools import list_ports

  HAS_PYSERIAL = True
except ImportError as e:
  HAS_PYSERIAL = False
  _PYSERIAL_ERROR = e

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
    human_readable_device_name: str,
    device_id: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    interface_select: Optional[int] = None,
  ):
    if not HAS_PYLIBFTDI:
      global _FTDI_ERROR
      raise RuntimeError(
        "pylibftdi is not installed. Install with: pip install pylabrobot[ftdi]. "
        f"Import error: {_FTDI_ERROR}"
      )
    if not HAS_PYUSB:
      global _PYUSB_ERROR
      raise RuntimeError(
        "pyusb is not installed. Install with: pip install pylabrobot[ftdi]. "
        f"Import error: {_PYUSB_ERROR}"
      )

    self._human_readable_device_name = human_readable_device_name
    self._device_id = device_id
    self._vid = vid
    self._pid = pid
    self._interface_select = interface_select

    # Will be resolved in setup()
    self._dev: Optional[Device] = None
    self._serial_dev: Optional["serial.Serial"] = None
    self._executor: Optional[ThreadPoolExecutor] = None

    if get_capture_or_validation_active():
      raise RuntimeError(
        f"Cannot create a new FTDI object for '{self._human_readable_device_name}' while capture or validation is active"
      )

  @property
  def dev(self) -> "Device":
    if self._dev is None:
      raise RuntimeError("Device not initialized. Call setup() first.")
    return self._dev

  @property
  def serial_dev(self) -> "serial.Serial":
    if self._serial_dev is None:
      raise RuntimeError("Serial device not initialized. Call setup() first.")
    return self._serial_dev

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

  def _resolve_serial_port(self) -> tuple[str, str]:
    if not HAS_PYSERIAL:
      global _PYSERIAL_ERROR
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_PYSERIAL_ERROR}"
      )

    candidates = []
    connected_devices_list = []
    for port in list_ports.comports():
      serial_number = port.serial_number or ""
      vid = port.vid
      pid = port.pid
      vid_pid = (
        f"{vid:04x}:{pid:04x}" if vid is not None and pid is not None else "unknown"
      )
      connected_devices_list.append(f"{port.device} {serial_number} (VID:PID {vid_pid})")

      if vid is None or pid is None:
        continue
      if self._vid is not None and vid != self._vid:
        continue
      if self._vid is None and vid not in pylibftdi.driver.USB_VID_LIST:
        continue
      if self._pid is not None and pid != self._pid:
        continue
      if self._pid is None and pid not in pylibftdi.driver.USB_PID_LIST:
        continue

      if self._device_id is not None:
        if serial_number == self._device_id:
          candidates.append((port.device, serial_number, True))
          continue
        if serial_number.startswith(self._device_id):
          candidates.append((port.device, serial_number, False))
          continue
        continue

      candidates.append((port.device, serial_number, True))

    exact_candidates = [candidate for candidate in candidates if candidate[2]]
    if exact_candidates:
      candidates = exact_candidates

    connected_devices_string = ", ".join(connected_devices_list)
    vid_string = f"{self._vid:04x}" if self._vid is not None else "any"
    pid_string = f"{self._pid:04x}" if self._pid is not None else "any"

    if len(candidates) == 0:
      raise RuntimeError(
        f"No FTDI serial ports found with specified criteria: "
        f"VID:PID {vid_string}:{pid_string}, "
        f"device_id {self._device_id}. "
        "Connected serial ports: " + connected_devices_string
      )

    if len(candidates) > 1:
      raise RuntimeError(
        f"Multiple FTDI serial ports found with specified criteria: "
        f"VID:PID {vid_string}:{pid_string}, "
        f"device_id {self._device_id}. "
        f"Please specify the device_id parameter explicitly with the serial number of the desired device."
      )

    port_name, serial_number, exact_match = candidates[0]
    if not exact_match:
      logger.warning(
        "Resolved FTDI serial prefix %s to %s on %s",
        self._device_id,
        serial_number,
        port_name,
      )
    return port_name, serial_number

  async def setup(self):
    """Initialize the FTDI device connection with device resolution."""
    if self._dev is not None and not self._dev.closed:
      self._dev.close()
    if self._serial_dev is not None and self._serial_dev.is_open:
      self._serial_dev.close()
      self._serial_dev = None

    native_error: Optional[Exception] = None
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
    except Exception as e:
      native_error = e

    if self._dev is None:
      try:
        port_name, serial_number = self._resolve_serial_port()
        self._device_id = serial_number
        self._serial_dev = serial.Serial(
          port=port_name,
          timeout=0,
          write_timeout=1,
        )
        logger.info(
          "Successfully opened FTDI serial port %s for device: %s",
          port_name,
          self.device_id,
        )
      except Exception as serial_error:
        raise RuntimeError(
          f"Failed to open FTDI device for '{self._human_readable_device_name}'. "
          f"pylibftdi error: {native_error}. "
          f"pyserial fallback error: {serial_error}. "
          "Is the device connected? Is it in use by another process? "
          "Try restarting the kernel."
        ) from serial_error

    self._executor = ThreadPoolExecutor(max_workers=1)

  @property
  def device_id(self) -> str:
    if self._device_id is None:
      raise RuntimeError("Device not initialized. Call setup() first.")
    return self._device_id

  async def set_baudrate(self, baudrate: int):
    loop = asyncio.get_running_loop()
    if self._serial_dev is not None:
      await loop.run_in_executor(
        self._executor,
        lambda: setattr(self.serial_dev, "baudrate", baudrate),
      )
    else:
      await loop.run_in_executor(self._executor, lambda: setattr(self.dev, "baudrate", baudrate))
    logger.log(LOG_LEVEL_IO, "[%s] set_baudrate %s", self._device_id, baudrate)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_baudrate", data=str(baudrate))
    )

  async def set_rts(self, level: bool):
    loop = asyncio.get_running_loop()
    if self._serial_dev is not None:
      await loop.run_in_executor(self._executor, lambda: setattr(self.serial_dev, "rts", level))
    else:
      await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setrts(level))
    logger.log(LOG_LEVEL_IO, "[%s] set_rts %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_rts", data=str(level)))

  async def set_dtr(self, level: bool):
    loop = asyncio.get_running_loop()
    if self._serial_dev is not None:
      await loop.run_in_executor(self._executor, lambda: setattr(self.serial_dev, "dtr", level))
    else:
      await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setdtr(level))
    logger.log(LOG_LEVEL_IO, "[%s] set_dtr %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_dtr", data=str(level)))

  async def usb_reset(self):
    loop = asyncio.get_running_loop()
    if self._serial_dev is None:
      await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_reset())
    logger.log(LOG_LEVEL_IO, "[%s] usb_reset", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_reset", data=""))

  async def set_latency_timer(self, latency: int):
    loop = asyncio.get_running_loop()
    if self._serial_dev is None:
      await loop.run_in_executor(
        self._executor, lambda: self.dev.ftdi_fn.ftdi_set_latency_timer(latency)
      )
    logger.log(LOG_LEVEL_IO, "[%s] set_latency_timer %s", self._device_id, latency)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_latency_timer", data=str(latency))
    )

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    loop = asyncio.get_running_loop()
    if self._serial_dev is not None:
      parity_map = {
        0: serial.PARITY_NONE,
        1: serial.PARITY_ODD,
        2: serial.PARITY_EVEN,
      }
      stopbits_map = {
        1: serial.STOPBITS_ONE,
        2: serial.STOPBITS_TWO,
      }

      def configure_serial_line():
        self.serial_dev.bytesize = bits
        self.serial_dev.stopbits = stopbits_map.get(stopbits, stopbits)
        self.serial_dev.parity = parity_map.get(parity, serial.PARITY_NONE)

      await loop.run_in_executor(self._executor, configure_serial_line)
    else:
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
    if self._serial_dev is not None:

      def configure_serial_flow_control():
        self.serial_dev.xonxoff = False
        self.serial_dev.rtscts = False
        self.serial_dev.dsrdtr = False

      await loop.run_in_executor(self._executor, configure_serial_flow_control)
    else:
      await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setflowctrl(flowctrl))
    logger.log(LOG_LEVEL_IO, "[%s] set_flowctrl %s", self._device_id, flowctrl)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_flowctrl", data=str(flowctrl))
    )

  async def usb_purge_rx_buffer(self):
    loop = asyncio.get_running_loop()
    if self._serial_dev is not None:
      await loop.run_in_executor(self._executor, self.serial_dev.reset_input_buffer)
    else:
      await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_purge_rx_buffer())
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_rx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_rx_buffer", data=""))

  async def usb_purge_tx_buffer(self):
    loop = asyncio.get_running_loop()
    if self._serial_dev is not None:
      await loop.run_in_executor(self._executor, self.serial_dev.reset_output_buffer)
    else:
      await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_purge_tx_buffer())
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_tx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_tx_buffer", data=""))

  async def poll_modem_status(self) -> int:
    loop = asyncio.get_running_loop()
    if self._serial_dev is not None:

      def read_serial_modem_status() -> int:
        status = 0
        status |= int(bool(self.serial_dev.cts)) << 4
        status |= int(bool(self.serial_dev.dsr)) << 5
        status |= int(bool(self.serial_dev.ri)) << 6
        status |= int(bool(self.serial_dev.cd)) << 7
        return status

      value = await loop.run_in_executor(self._executor, read_serial_modem_status)
    else:
      stat = ctypes.c_ushort(0)
      await loop.run_in_executor(
        self._executor, lambda: self.dev.ftdi_fn.ftdi_poll_modem_status(ctypes.byref(stat))
      )
      value = stat.value
    logger.log(LOG_LEVEL_IO, "[%s] poll_modem_status %s", self._device_id, value)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="poll_modem_status", data=str(value))
    )
    return value

  async def get_serial(self) -> str:
    return self.device_id

  async def stop(self):
    if self._dev is not None:
      self.dev.close()
      self._dev = None
    if self._serial_dev is not None:
      self.serial_dev.close()
      self._serial_dev = None
    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  async def write(self, data: bytes) -> int:
    """Write data to the device. Returns the number of bytes written."""
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._device_id, data)
    capturer.record(FTDICommand(device_id=self.device_id, action="write", data=data.hex()))
    if self._serial_dev is not None:
      return cast(int, self.serial_dev.write(data))
    return cast(int, self.dev.write(data))

  async def read(self, num_bytes: int = 1) -> bytes:
    if self._serial_dev is not None:
      data = self.serial_dev.read(num_bytes)
    else:
      data = self.dev.read(num_bytes)
    if len(data) != 0:
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
    if self._serial_dev is not None:
      data = self.serial_dev.readline()
    else:
      data = self.dev.readline()
    if len(data) != 0:
      logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._device_id, data)
      capturer.record(FTDICommand(device_id=self.device_id, action="readline", data=data.hex()))
    return cast(bytes, data)

  def serialize(self):
    return {
      "human_readable_device_name": self._human_readable_device_name,
      "device_id": self._device_id,
      "vid": self._vid,
      "pid": self._pid,
    }


class FTDIValidator(FTDI):
  def __init__(self, cr: "CaptureReader", human_readable_device_name: str, device_id: str):
    super().__init__(human_readable_device_name=human_readable_device_name, device_id=device_id)
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
