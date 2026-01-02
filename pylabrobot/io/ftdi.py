import asyncio
import ctypes
import logging
from concurrent.futures import ThreadPoolExecutor
from io import IOBase
from typing import Optional, cast

try:
  from pylibftdi import Device, Driver, FtdiError, LibraryMissingError

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
  """
  Thin wrapper around pylibftdi with intelligent device resolution.

  Supports hierarchical device identification:
  1. device_id (direct) - serial number or device index for explicit connection
  2. VID:PID (model-level) - works for single device of that model
  3. VID:PID + product/vendor substring matching for verification

  Args:
      device_id: Device identifier (serial number like '430-2621' or index like '0')
      vid: USB Vendor ID (hex, e.g., 0x0403)
      pid: USB Product ID (hex, e.g., 0xbb68)
      product_substring: Expected substring in product name for validation
      vendor_substring: Expected substring in vendor name for validation

  Note:
      For device verification/handshakes, implement them in your backend's setup()
      method after the IO connection is established. This allows the backend to
      verify the device identity and disconnect safely if verification fails.
  """

  def __init__(
    self,
    device_id: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    product_substring: Optional[str] = None,
    vendor_substring: Optional[str] = None,
  ):
    if not HAS_PYLIBFTDI:
      global _FTDI_ERROR
      raise RuntimeError(f"pylibftdi not installed. Import error: {_FTDI_ERROR}")

    self._device_id = device_id
    self._vid = vid
    self._pid = pid
    self._product_substring = product_substring
    self._vendor_substring = vendor_substring

    # Validate inputs
    if not device_id and not (vid and pid):
      raise ValueError("Must specify either device_id or both vid and pid.")

    # Will be resolved in setup()
    self._resolved_serial: Optional[str] = None
    self._dev: Optional[Device] = None
    self._executor: Optional[ThreadPoolExecutor] = None

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new FTDI object while capture or validation is active")

  @property
  def dev(self) -> "Device":
    if self._dev is None:
      raise RuntimeError("Device not initialized. Call setup() first.")
    return self._dev

  @property
  def device_id(self) -> str:
    """Return the resolved serial number."""
    if self._resolved_serial is None:
      raise RuntimeError("Device not initialized. Call setup() first.")
    return self._resolved_serial

  def _find_usb_devices(self) -> list:
    """
    Find USB devices matching VID:PID using pyusb.
    Returns list of dicts with device info.
    """
    if not HAS_PYUSB:
      logger.warning("pyusb not available, cannot perform advanced device filtering")
      return []

    if self._vid is None or self._pid is None:
      return []

    devices = []
    usb_devices = usb.core.find(find_all=True, idVendor=self._vid, idProduct=self._pid)

    for d in usb_devices:
      try:
        device_info = {
          "vid": d.idVendor,
          "pid": d.idProduct,
          "manufacturer": usb.util.get_string(d, d.iManufacturer) if d.iManufacturer else None,
          "product": usb.util.get_string(d, d.iProduct) if d.iProduct else None,
          "serial": usb.util.get_string(d, d.iSerialNumber) if d.iSerialNumber else None,
          "bus": d.bus,
          "address": d.address,
        }
        devices.append(device_info)
      except Exception as e:
        logger.warning(f"Could not read USB device info: {e}")
        continue

    return devices

  def _score_device(self, device_info: dict) -> int:
    """
    Score a device based on matching criteria.
    Higher score = better match.
    """
    score = 0

    # VID:PID match is mandatory (already filtered)
    score += 100

    # Product substring match
    if self._product_substring and device_info.get("product"):
      if self._product_substring.lower() in device_info["product"].lower():
        score += 50
        logger.debug(f"Product substring '{self._product_substring}' matched")

    # Vendor substring match
    if self._vendor_substring and device_info.get("manufacturer"):
      if self._vendor_substring.lower() in device_info["manufacturer"].lower():
        score += 50
        logger.debug(f"Vendor substring '{self._vendor_substring}' matched")

    # Exact device_id match (could be serial or index)
    if self._device_id and device_info.get("serial"):
      if self._device_id == device_info["serial"]:
        score += 1000  # Exact match wins everything
        logger.debug(f"Exact device_id match: {self._device_id}")

    # Penalize devices without serial numbers (less reliable)
    if not device_info.get("serial"):
      score -= 25
      logger.debug("Device has no serial number - less reliable for future connections")

    return score

  def _resolve_device_serial(self) -> str:
    """
    Resolve which device to connect to based on criteria.
    Returns the device_id to use (serial number preferred, device index as fallback).
    """
    # Ensure VID:PID is in pylibftdi's search list if provided
    # This must happen BEFORE any scenario, so pylibftdi can find the device
    if self._vid is not None and self._pid is not None:
      from pylibftdi import driver

      if self._vid not in driver.USB_VID_LIST:
        driver.USB_VID_LIST.append(self._vid)
        logger.debug(f"Added VID {self._vid:04x} to pylibftdi search list")
      if self._pid not in driver.USB_PID_LIST:
        driver.USB_PID_LIST.append(self._pid)
        logger.debug(f"Added PID {self._pid:04x} to pylibftdi search list")

    # Scenario 1: device_id provided explicitly
    if self._device_id:
      logger.info(f"Using explicitly provided device_id: {self._device_id}")

      # Verify the device exists and matches our expectations
      if self._vid is not None and self._pid is not None:
        # Use pyusb to find and verify VID:PID → serial mapping
        usb_devices = self._find_usb_devices()

        # Find device with matching serial
        matching_device = None
        for dev in usb_devices:
          if dev.get("serial") == self._device_id:
            matching_device = dev
            break

        if matching_device is None:
          # Device not found with expected VID:PID
          raise RuntimeError(
            f"Device with device_id '{self._device_id}' not found with "
            f"VID:PID {self._vid:04x}:{self._pid:04x}. "
            "Is the device connected? Did the device_id change?"
          )

        # Verify against product substring if provided
        if self._product_substring:
          product = matching_device.get("product", "")
          if product and self._product_substring.lower() not in product.lower():
            logger.warning(
              f"Device device_id '{self._device_id}' found but product name "
              f"'{product}' does not contain expected substring '{self._product_substring}'. "
              "Device may not be the expected model."
            )
          else:
            logger.debug(
              f"✓ Product verification passed: '{product}' contains '{self._product_substring}'"
            )

        # Verify against vendor substring if provided
        if self._vendor_substring:
          vendor = matching_device.get("manufacturer", "")
          if vendor and self._vendor_substring.lower() not in vendor.lower():
            logger.warning(
              f"Device device_id '{self._device_id}' found but manufacturer "
              f"'{vendor}' does not contain expected substring '{self._vendor_substring}'. "
              "Device may not be from the expected vendor."
            )
          else:
            logger.debug(
              f"✓ Vendor verification passed: '{vendor}' contains '{self._vendor_substring}'"
            )

        logger.info(
          f"✓ Device verification successful: {matching_device.get('manufacturer')} "
          f"{matching_device.get('product')} (device_id: {self._device_id}, "
          f"VID:PID {self._vid:04x}:{self._pid:04x})"
        )
      else:
        logger.info(
          f"Using device_id without VID:PID verification (not provided): " f"{self._device_id}"
        )

      return self._device_id

    # Scenario 2: Need to find device by VID:PID
    if self._vid is None or self._pid is None:
      raise RuntimeError("Must specify VID:PID when serial number is not provided")

    # Ensure VID:PID is in pylibftdi's search list
    from pylibftdi import driver

    if self._vid not in driver.USB_VID_LIST:
      driver.USB_VID_LIST.append(self._vid)
      logger.debug(f"Added VID {self._vid:04x} to pylibftdi search list")
    if self._pid not in driver.USB_PID_LIST:
      driver.USB_PID_LIST.append(self._pid)
      logger.debug(f"Added PID {self._pid:04x} to pylibftdi search list")

    # Get USB device info for scoring
    usb_devices = self._find_usb_devices()

    if not usb_devices:
      raise RuntimeError(
        f"No USB devices found with VID:PID {self._vid:04x}:{self._pid:04x}. "
        "Is the device connected?"
      )

    # Score all devices
    scored_devices = [(self._score_device(dev), dev) for dev in usb_devices]
    scored_devices.sort(reverse=True, key=lambda x: x[0])

    logger.debug(f"Found {len(scored_devices)} device(s) matching VID:PID")
    for score, dev in scored_devices:
      logger.debug(
        f"  Score {score}: {dev.get('manufacturer')} {dev.get('product')} "
        f"(Serial number: {dev.get('serial')})"
      )

    # Check if we have a clear winner
    if len(scored_devices) == 1:
      winner = scored_devices[0][1]
      serial = winner.get("serial")

      # Handle devices without serial numbers
      if not serial:
        logger.warning(
          f"Device found but has no serial number: "
          f"{winner.get('manufacturer')} {winner.get('product')}. "
          f"Will use device index fallback (not portable across reconnections)."
        )
        # Use pylibftdi's device index as fallback
        # Need to map USB device to pylibftdi index
        device_id = self._get_device_index_fallback(winner)
        return device_id

      logger.info(
        f"Single device found: {winner.get('manufacturer')} {winner.get('product')} "
        f"(Serial number: {serial})"
      )
      return serial

    # Multiple devices - check if top scorer is clearly better
    top_score = scored_devices[0][0]
    second_score = scored_devices[1][0]

    if top_score > second_score:
      winner = scored_devices[0][1]
      serial = winner.get("serial")

      # Handle devices without serial numbers
      if not serial:
        logger.warning(
          f"Top scoring device has no serial number: "
          f"{winner.get('manufacturer')} {winner.get('product')}. "
          f"Will use device index fallback (not portable across reconnections)."
        )
        device_id = self._get_device_index_fallback(winner)
        return device_id

      logger.info(
        f"Selected device with score {top_score}: "
        f"{winner.get('manufacturer')} {winner.get('product')} (Serial number: {serial})"
      )
      return serial

    # Ambiguous - multiple devices with same score
    raise RuntimeError(
      f"Multiple devices found with VID:PID {self._vid:04x}:{self._pid:04x} "
      f"and ambiguous matching criteria.\n"
      f"Detected devices:\n"
      + "\n".join(
        [
          f"  - {dev.get('manufacturer')} {dev.get('product')} "
          f"(Serial number: {dev.get('serial')}, Score: {score})"
          for score, dev in scored_devices
        ]
      )
      + f"\n\nPlease specify the device_id parameter explicitly with the serial number "
      f"of the desired device (e.g., device_id='430-2621')."
    )

  async def setup(self):
    """Initialize the FTDI device connection with intelligent device resolution."""
    try:
      # Resolve which device to connect to
      self._resolved_serial = self._resolve_device_serial()

      # Create and open device
      self._dev = Device(lazy_open=True, device_id=self._resolved_serial)
      self._dev.open()

      logger.info(f"Successfully opened FTDI device: {self._resolved_serial}")

      self._executor = ThreadPoolExecutor(max_workers=1)

    except FtdiError as e:
      raise RuntimeError(
        f"Failed to open FTDI device: {e}. "
        "Is the device connected? Is it in use by another process? "
        "Try restarting the kernel."
      ) from e

  async def set_baudrate(self, baudrate: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: setattr(self.dev, "baudrate", baudrate))
    logger.log(LOG_LEVEL_IO, "[%s] set_baudrate %s", self.device_id, baudrate)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_baudrate", data=str(baudrate))
    )

  async def set_rts(self, level: bool):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setrts(level))
    logger.log(LOG_LEVEL_IO, "[%s] set_rts %s", self.device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_rts", data=str(level)))

  async def set_dtr(self, level: bool):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setdtr(level))
    logger.log(LOG_LEVEL_IO, "[%s] set_dtr %s", self.device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_dtr", data=str(level)))

  async def usb_reset(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_reset())
    logger.log(LOG_LEVEL_IO, "[%s] usb_reset", self.device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_reset", data=""))

  async def set_latency_timer(self, latency: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
      self._executor, lambda: self.dev.ftdi_fn.ftdi_set_latency_timer(latency)
    )
    logger.log(LOG_LEVEL_IO, "[%s] set_latency_timer %s", self.device_id, latency)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_latency_timer", data=str(latency))
    )

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
      self._executor, lambda: self.dev.ftdi_fn.ftdi_set_line_property(bits, stopbits, parity)
    )
    logger.log(
      LOG_LEVEL_IO, "[%s] set_line_property %s,%s,%s", self.device_id, bits, stopbits, parity
    )
    capturer.record(
      FTDICommand(
        device_id=self.device_id, action="set_line_property", data=f"{bits},{stopbits},{parity}"
      )
    )

  async def set_flowctrl(self, flowctrl: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_setflowctrl(flowctrl))
    logger.log(LOG_LEVEL_IO, "[%s] set_flowctrl %s", self.device_id, flowctrl)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_flowctrl", data=str(flowctrl))
    )

  async def usb_purge_rx_buffer(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_purge_rx_buffer())
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_rx_buffer", self.device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_rx_buffer", data=""))

  async def usb_purge_tx_buffer(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(self._executor, lambda: self.dev.ftdi_fn.ftdi_usb_purge_tx_buffer())
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_tx_buffer", self.device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_tx_buffer", data=""))

  async def poll_modem_status(self) -> int:
    loop = asyncio.get_running_loop()
    stat = ctypes.c_ushort(0)
    await loop.run_in_executor(
      self._executor, lambda: self.dev.ftdi_fn.ftdi_poll_modem_status(ctypes.byref(stat))
    )
    logger.log(LOG_LEVEL_IO, "[%s] poll_modem_status %s", self.device_id, stat.value)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="poll_modem_status", data=str(stat.value))
    )
    return stat.value

  async def get_serial(self) -> str:
    """Get the serial number of the connected device."""
    serial = self._resolved_serial
    logger.log(LOG_LEVEL_IO, "[%s] get_serial %s", self.device_id, serial)
    capturer.record(FTDICommand(device_id=self.device_id, action="get_serial", data=str(serial)))
    return serial  # type: ignore

  async def stop(self):
    if self._dev is not None:
      self.dev.close()
    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  async def write(self, data: bytes) -> int:
    """Write data to the device. Returns the number of bytes written."""
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self.device_id, data)
    capturer.record(FTDICommand(device_id=self.device_id, action="write", data=data.hex()))
    return cast(int, self.dev.write(data))

  async def read(self, num_bytes: int = 1) -> bytes:
    data = self.dev.read(num_bytes)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self.device_id, data)
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
    logger.log(LOG_LEVEL_IO, "[%s] readline %s", self.device_id, data)
    capturer.record(FTDICommand(device_id=self.device_id, action="readline", data=data.hex()))
    return cast(bytes, data)

  def serialize(self):
    return {
      "device_id": self._resolved_serial,
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
      and next_command.device_id == self._resolved_serial
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

  async def get_serial(self) -> str:
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "get_serial"
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected FTDI get_serial {self._device_id}"
      )
    return next_command.data

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
