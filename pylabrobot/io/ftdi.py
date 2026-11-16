import ctypes
import logging
import sys
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, List, Optional, Tuple, TypeVar, Union, cast

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.events import emit_event

try:
  import pylibftdi.driver
  from pylibftdi import Device, FtdiError

  class _USBAddressDevice(Device):
    """pylibftdi device opened by libusb bus and device address."""

    def __init__(self, usb_bus: int, usb_device_address: int, **kwargs):
      self._usb_bus = usb_bus
      self._usb_device_address = usb_device_address
      super().__init__(**kwargs)

    def _open_device(self) -> int:
      return int(
        self.fdll.ftdi_usb_open_bus_addr(
          ctypes.byref(self.ctx), self._usb_bus, self._usb_device_address
        )
      )

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
from pylabrobot.io.io import ReconnectableConnectionIO
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


# FTDI's D2XX library: its file per platform, the calls it is given, and its status codes.
_D2XX_LIBRARY_FILES = {
  "win32": ("ftd2xx64.dll", "ftd2xx.dll"),
  "darwin": ("libftd2xx.dylib",),
  "linux": ("libftd2xx.so",),
}
_D2XX_DOWNLOAD = "https://ftdichip.com/drivers/d2xx-drivers/"
_FT_OPEN_BY_SERIAL_NUMBER = 1
_FT_PURGE_RX = 1
_FT_PURGE_TX = 2
_FT_FLAGS_OPENED = 1
_FT_DEVICE_NOT_OPENED = 3
# FT_SetFlowControl's Xon and Xoff characters: the standard ones.
_XON = 0x11
_XOFF = 0x13
# PyLabRobot design choices: how long one read or write may wait.
_D2XX_READ_TIMEOUT_MS = 100
_D2XX_WRITE_TIMEOUT_MS = 5000
_D2XX_STATUS_NAMES = (
  "FT_OK",
  "FT_INVALID_HANDLE",
  "FT_DEVICE_NOT_FOUND",
  "FT_DEVICE_NOT_OPENED",
  "FT_IO_ERROR",
  "FT_INSUFFICIENT_RESOURCES",
  "FT_INVALID_PARAMETER",
  "FT_INVALID_BAUD_RATE",
  "FT_DEVICE_NOT_OPENED_FOR_ERASE",
  "FT_DEVICE_NOT_OPENED_FOR_WRITE",
  "FT_FAILED_TO_WRITE_DEVICE",
  "FT_EEPROM_READ_FAILED",
  "FT_EEPROM_WRITE_FAILED",
  "FT_EEPROM_ERASE_FAILED",
  "FT_EEPROM_NOT_PRESENT",
  "FT_EEPROM_NOT_PROGRAMMED",
  "FT_INVALID_ARGS",
  "FT_NOT_SUPPORTED",
  "FT_OTHER_ERROR",
  "FT_DEVICE_LIST_NOT_READY",
)
_d2xx_library: Optional[Any] = None


class D2XXError(RuntimeError):
  """FTDI's D2XX library answered a call with a status other than FT_OK."""

  def __init__(self, message: str, *, status: int):
    """
    Args:
      message: what failed.
      status: the D2XX status code.
    """
    super().__init__(message)
    self.status = status


def is_ftdi_transport_error(error: BaseException) -> bool:
  """Return whether an exception was raised by the pylibftdi or D2XX transport."""
  if isinstance(error, D2XXError):
    return True
  if not HAS_PYLIBFTDI:
    return False
  return isinstance(error, FtdiError)


def _load_d2xx_library() -> Any:
  """FTDI's D2XX library, loaded once.

  Raises:
    RuntimeError: if it cannot be loaded on this platform.
  """
  global _d2xx_library
  if _d2xx_library is not None:
    return _d2xx_library
  platform = "linux" if sys.platform.startswith("linux") else sys.platform
  failures: List[str] = []
  for file_name in _D2XX_LIBRARY_FILES.get(platform, ()):
    try:
      if sys.platform == "win32":
        library = ctypes.WinDLL(file_name)
      else:
        library = ctypes.CDLL(file_name)
    except OSError as error:
      failures.append(f"{file_name}: {error}")
      continue
    _d2xx_library = library
    return library
  raise RuntimeError(
    "FTDI's D2XX library could not be loaded ("
    + ("; ".join(failures) or f"none known for {sys.platform}")
    + f"). It comes with FTDI's driver: {_D2XX_DOWNLOAD}"
  )


class _D2XXDeviceListInfoNode(ctypes.Structure):
  """One entry of D2XX's device list (FT_DEVICE_LIST_INFO_NODE)."""

  _fields_ = [
    ("Flags", ctypes.c_uint32),
    ("Type", ctypes.c_uint32),
    ("ID", ctypes.c_uint32),
    ("LocId", ctypes.c_uint32),
    ("SerialNumber", ctypes.c_char * 16),
    ("Description", ctypes.c_char * 64),
    ("ftHandle", ctypes.c_void_p),
  ]


def _check_d2xx_status(*, status: int, call: str) -> None:
  """Raise ``D2XXError`` unless a D2XX call returned FT_OK."""
  if status != 0:
    name = _D2XX_STATUS_NAMES[status] if status < len(_D2XX_STATUS_NAMES) else "unknown"
    raise D2XXError(f"{call} returned {name} ({status})", status=status)


def _list_d2xx_devices() -> List[_D2XXDeviceListInfoNode]:
  """Every FTDI channel D2XX sees: one entry per interface of a multi-interface chip."""
  library = _load_d2xx_library()
  count = ctypes.c_uint32(0)
  _check_d2xx_status(
    status=library.FT_CreateDeviceInfoList(ctypes.byref(count)), call="FT_CreateDeviceInfoList"
  )
  if count.value == 0:
    return []
  nodes = (_D2XXDeviceListInfoNode * count.value)()
  _check_d2xx_status(
    status=library.FT_GetDeviceInfoList(nodes, ctypes.byref(count)), call="FT_GetDeviceInfoList"
  )
  return list(nodes[: count.value])


class _D2XXDevice:
  """One FTDI channel opened through FTDI's D2XX library, by its D2XX serial number."""

  def __init__(self, *, serial_number: str):
    """
    Args:
      serial_number: the channel's D2XX serial number, with its interface letter if it has one.
    """
    self._library = _load_d2xx_library()
    self._serial_number = serial_number
    self._handle = ctypes.c_void_p()
    self.closed = True

  def open(self) -> None:
    """Open the channel, then set its timeouts; a channel whose timeouts fail is closed again."""
    _check_d2xx_status(
      call="FT_OpenEx",
      status=self._library.FT_OpenEx(
        ctypes.c_char_p(self._serial_number.encode("ascii")),
        _FT_OPEN_BY_SERIAL_NUMBER,
        ctypes.byref(self._handle),
      ),
    )
    self.closed = False
    try:
      _check_d2xx_status(
        call="FT_SetTimeouts",
        status=self._library.FT_SetTimeouts(
          self._handle,
          ctypes.c_uint32(_D2XX_READ_TIMEOUT_MS),
          ctypes.c_uint32(_D2XX_WRITE_TIMEOUT_MS),
        ),
      )
    except BaseException:
      self.closed = True
      self._library.FT_Close(self._handle)
      raise

  def close(self) -> None:
    if not self.closed:
      self.closed = True
      _check_d2xx_status(call="FT_Close", status=self._library.FT_Close(self._handle))

  def set_baudrate(self, *, baudrate: int) -> None:
    _check_d2xx_status(
      call="FT_SetBaudRate",
      status=self._library.FT_SetBaudRate(self._handle, ctypes.c_uint32(baudrate)),
    )

  def set_line_property(self, *, bits: int, stopbits: int, parity: int) -> None:
    """Set data bits, stop bits and parity, given as libftdi's values.

    Raises:
      ValueError: for 1.5 stop bits (libftdi's 1), which D2XX cannot set.
    """
    if stopbits == 1:
      raise ValueError("D2XX cannot set 1.5 stop bits (libftdi stop-bit value 1)")
    _check_d2xx_status(
      call="FT_SetDataCharacteristics",
      status=self._library.FT_SetDataCharacteristics(
        self._handle, ctypes.c_ubyte(bits), ctypes.c_ubyte(stopbits), ctypes.c_ubyte(parity)
      ),
    )

  def set_flowctrl(self, *, flowctrl: int) -> None:
    """Set flow control, given as libftdi's value; D2XX's values are the same."""
    _check_d2xx_status(
      call="FT_SetFlowControl",
      status=self._library.FT_SetFlowControl(
        self._handle,
        ctypes.c_uint16(flowctrl),
        ctypes.c_ubyte(_XON),
        ctypes.c_ubyte(_XOFF),
      ),
    )

  def set_rts(self, *, level: bool) -> None:
    if level:
      _check_d2xx_status(call="FT_SetRts", status=self._library.FT_SetRts(self._handle))
    else:
      _check_d2xx_status(call="FT_ClrRts", status=self._library.FT_ClrRts(self._handle))

  def set_dtr(self, *, level: bool) -> None:
    if level:
      _check_d2xx_status(call="FT_SetDtr", status=self._library.FT_SetDtr(self._handle))
    else:
      _check_d2xx_status(call="FT_ClrDtr", status=self._library.FT_ClrDtr(self._handle))

  def set_latency_timer(self, *, latency: int) -> None:
    _check_d2xx_status(
      call="FT_SetLatencyTimer",
      status=self._library.FT_SetLatencyTimer(self._handle, ctypes.c_ubyte(latency)),
    )

  def reset(self) -> None:
    """The chip's reset request; not a reset of the USB port."""
    _check_d2xx_status(call="FT_ResetDevice", status=self._library.FT_ResetDevice(self._handle))

  def purge(self, *, mask: int) -> None:
    _check_d2xx_status(
      call="FT_Purge", status=self._library.FT_Purge(self._handle, ctypes.c_uint32(mask))
    )

  def poll_modem_status(self) -> int:
    """The modem status in the low byte, its bits as libftdi's; above it the line status.

    The line status holds only the error bits (overrun, parity, framing, break), and only on Windows,
    so a whole value differs from libftdi's.
    """
    status = ctypes.c_uint32(0)
    _check_d2xx_status(
      call="FT_GetModemStatus",
      status=self._library.FT_GetModemStatus(self._handle, ctypes.byref(status)),
    )
    return status.value

  def write(self, data: bytes) -> int:
    """Write ``data`` whole.

    Raises:
      D2XXError: if the write timeout ends before every byte went out.
    """
    written = ctypes.c_uint32(0)
    _check_d2xx_status(
      call="FT_Write",
      status=self._library.FT_Write(
        self._handle, ctypes.c_char_p(data), ctypes.c_uint32(len(data)), ctypes.byref(written)
      ),
    )
    if written.value != len(data):
      raise D2XXError(
        f"FT_Write wrote {written.value} of {len(data)} bytes before its timeout", status=0
      )
    return written.value

  def read(self, num_bytes: int) -> bytes:
    """What has arrived, at most ``num_bytes``; empty, without waiting, when nothing has."""
    queued = ctypes.c_uint32(0)
    _check_d2xx_status(
      call="FT_GetQueueStatus",
      status=self._library.FT_GetQueueStatus(self._handle, ctypes.byref(queued)),
    )
    count = min(num_bytes, queued.value)
    if count == 0:
      return b""
    buffer = ctypes.create_string_buffer(count)
    received = ctypes.c_uint32(0)
    _check_d2xx_status(
      call="FT_Read",
      status=self._library.FT_Read(
        self._handle, buffer, ctypes.c_uint32(count), ctypes.byref(received)
      ),
    )
    return buffer.raw[: received.value]


def _parse_usb_address(address: str) -> tuple[int, tuple[int, ...]]:
  """Parse a USB topology path '<bus>-<port>[.<port>...]' into its bus and ports."""
  bus_str, sep, port_str = address.partition("-")
  if not sep:
    raise ValueError(f"USB address must be '<bus>-<port>[.<port>...]', got {address!r}")
  try:
    bus = int(bus_str)
    ports = tuple(int(port) for port in port_str.split(".")) if port_str else ()
  except ValueError as exc:
    raise ValueError(f"Invalid USB address {address!r}: {exc}") from exc
  if bus <= 0 or not ports or any(port <= 0 for port in ports):
    raise ValueError(f"Invalid USB address {address!r}")
  return bus, ports


class FTDICommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str, module: str = "ftdi"):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data


@dataclass
class _PendingRead:
  """Completion state for one lifespan-owned device read."""

  completed: anyio.Event
  scope: anyio.CancelScope
  error: Optional[BaseException] = None


class FTDI(ReconnectableConnectionIO):
  """An FTDI chip's channel, through libftdi (over libusb) or FTDI's own D2XX library.

  Finds devices based on the following parameters:
  1. device_id - serial number for explicit connection
  2. VID:PID - works for single device of that model

  If no devices match, an error is raised.
  If multiple devices match the criteria, an error is raised.

  Args:
    device_id: Device identifier (serial number)
    vid: USB Vendor ID
    pid: USB Product ID
    usb_product: USB product string, for devices that share a VID/PID.
    ftdi_library: "libftdi" or "d2xx"; None picks D2XX on Windows when FTDI's D2XX library loads,
      else libftdi.
  """

  def __init__(
    self,
    human_readable_device_name: str,
    device_id: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    interface_select: Optional[int] = None,
    usb_address: Optional[str] = None,
    ftdi_library: Optional[str] = "libftdi",
    usb_product: Optional[str] = None,
  ):
    super().__init__()
    if ftdi_library not in (None, "libftdi", "d2xx"):
      raise ValueError(f"ftdi_library must be 'libftdi', 'd2xx' or None, got {ftdi_library!r}")
    d2xx_failure = ""
    if ftdi_library is None:
      ftdi_library = "libftdi"
      if sys.platform == "win32":
        try:
          _load_d2xx_library()
          ftdi_library = "d2xx"
        except RuntimeError as error:
          d2xx_failure = f" D2XX was not used: {error}"
    if ftdi_library == "d2xx":
      _load_d2xx_library()
    else:
      if not HAS_PYLIBFTDI:
        global _FTDI_ERROR
        raise RuntimeError(
          "pylibftdi is not installed. Install with: pip install pylabrobot[ftdi]. "
          f"Import error: {_FTDI_ERROR}.{d2xx_failure}"
        )
      if not HAS_PYUSB:
        global _PYUSB_ERROR
        raise RuntimeError(
          "pyusb is not installed. Install with: pip install pylabrobot[ftdi]. "
          f"Import error: {_PYUSB_ERROR}"
        )
    self.ftdi_library = ftdi_library

    self.human_readable_device_name = human_readable_device_name
    self._device_id = device_id
    self._vid = vid
    self._pid = pid
    self._interface_select = interface_select
    self._usb_address = usb_address
    self._usb_product = usb_product

    # Will be resolved during lifespan entry
    self._dev: Optional[Device] = None
    self._d2xx_dev: Optional[_D2XXDevice] = None
    self._tg: Optional[anyio.abc.TaskGroup] = None
    self._operation_lock = anyio.Lock()
    self._reader_lock = anyio.Lock()
    self._limiter = anyio.CapacityLimiter(1)
    # Bytes off the wire that no read has taken yet, and the read still in flight, if any.
    self._unread = bytearray()
    self._pending_read: Optional[_PendingRead] = None
    self._detached_kernel_driver: Optional[tuple[int, int, int]] = None
    self._connection_lost = False
    self._closing = False

    if get_capture_or_validation_active():
      raise RuntimeError(
        f"Cannot create a new FTDI object for '{self.human_readable_device_name}' while capture or validation is active"
      )

  @property
  def dev(self) -> "Device":
    if self._connection_lost:
      raise ConnectionError("FTDI connection lost; reconnect or enter a new lifespan")
    if self._dev is None:
      raise RuntimeError("Device not initialized; enter its lifespan first.")
    return self._dev

  @property
  def _d2xx(self) -> _D2XXDevice:
    if self._connection_lost:
      raise ConnectionError("FTDI connection lost; reconnect or enter a new lifespan")
    if self._d2xx_dev is None:
      raise RuntimeError("Device not initialized; enter its lifespan first.")
    return self._d2xx_dev

  def _resolve_d2xx_serial_number(self) -> str:
    """Pick the one D2XX channel matching the parameters; return its D2XX serial number.

    A multi-interface chip lists each channel as its serial plus a letter; ``interface_select`` 1
    is "A", 2 "B". ``device_id`` is matched against the serial without that letter.

    Raises:
      ValueError: if ``usb_address`` is given; D2XX selects by serial number.
      RuntimeError: if no channel, or more than one, matches.
    """
    nodes = _list_d2xx_devices()
    listed = ", ".join(
      f"{node.SerialNumber.decode('ascii', 'replace') or '(in use)'} "
      f"(VID:PID {node.ID >> 16:04x}:{node.ID & 0xFFFF:04x})"
      for node in nodes
    )
    if self._usb_address is not None:
      raise ValueError(
        "usb_address is not supported through FTDI's D2XX library; pass device_id, the serial "
        f"number, instead. FTDI channels found: {listed or 'none'}"
      )
    serial_numbers = {node.SerialNumber.decode("ascii", "replace") for node in nodes}
    letter = "ABCD"[self._interface_select - 1] if self._interface_select else None
    candidates: List[Tuple[str, str]] = []
    for node in nodes:
      serial_number = node.SerialNumber.decode("ascii", "replace")
      if self._vid is not None and node.ID >> 16 != self._vid:
        continue
      if self._pid is not None and node.ID & 0xFFFF != self._pid:
        continue
      # A multi-interface chip's channels share a serial; without a letter, take "A" as libftdi does.
      multi_interface = serial_number[:-1] + "B" in serial_numbers
      if letter is not None:
        if not serial_number.endswith(letter):
          continue
        base = serial_number[:-1]
      elif multi_interface and serial_number.endswith("A"):
        base = serial_number[:-1]
      elif serial_number[-1:] in ("B", "C", "D") and serial_number[:-1] + "A" in serial_numbers:
        continue
      else:
        base = serial_number
      description = node.Description.decode("ascii", "replace")
      if self._usb_product is not None:
        descriptions = {self._usb_product}
        if base != serial_number:
          descriptions.add(f"{self._usb_product} {serial_number[-1]}")
        if description not in descriptions:
          continue
      if self._device_id is not None and base != self._device_id:
        continue
      candidates.append((serial_number, base))
    if len(candidates) == 0:
      held = sum(1 for node in nodes if node.Flags & _FT_FLAGS_OPENED and not node.SerialNumber)
      raise RuntimeError(
        f"No FTDI channel found through D2XX for '{self.human_readable_device_name}' "
        f"(device_id {self._device_id}, product {self._usb_product}, interface {letter or 'any'}). "
        "FTDI channels found: "
        f"{listed or 'none'}. "
        + (
          f"{held} held by another program list no serial number: close the vendor software or "
          "stop its service. "
          if held
          else ""
        )
        + "A channel bound to a driver other than FTDI's (e.g. with Zadig) is not listed: restore "
        "FTDI's driver in Device Manager, or pass ftdi_library='libftdi'."
      )
    if len(candidates) > 1:
      raise RuntimeError(
        f"Multiple FTDI channels found through D2XX for '{self.human_readable_device_name}': "
        f"{listed}. Please specify the device_id parameter explicitly with the serial number."
      )
    serial_number, base = candidates[0]
    self._device_id = base
    return serial_number

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
      if self._usb_product is not None:
        if usb.util.get_string(device, device.iProduct) != self._usb_product:
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
        f"device_id {self._device_id}, product {self._usb_product}. "
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

  def _resolve_device_location(self) -> tuple[int, int]:
    """Resolve a topology path to the exact libusb bus/device-address pair."""
    if self._vid is None or self._pid is None:
      raise RuntimeError("usb_address requires both vid and pid to be specified.")
    assert self._usb_address is not None
    bus, ports = _parse_usb_address(self._usb_address)
    for device in usb.core.find(find_all=True, idVendor=self._vid, idProduct=self._pid):
      try:
        device_ports = tuple(device.port_numbers) if device.port_numbers else ()
      except (ValueError, NotImplementedError):
        continue
      if device.bus == bus and device_ports == ports:
        if self._usb_product is not None:
          product = usb.util.get_string(device, device.iProduct)
          if product != self._usb_product:
            raise RuntimeError(
              f"USB device at {self._usb_address!r} has product {product!r}, "
              f"expected {self._usb_product!r}"
            )
        if device.address is None:
          raise RuntimeError(f"USB device at {self._usb_address!r} has no device address")
        return int(device.bus), int(device.address)
    raise RuntimeError(
      f"No device with VID:PID {self._vid:04x}:{self._pid:04x} found at USB path "
      f"{self._usb_address!r}."
    )

  @staticmethod
  def _usb_device_at(bus: int, address: int):
    return next(
      (
        device
        for device in usb.core.find(find_all=True)
        if device.bus == bus and device.address == address
      ),
      None,
    )

  def _detach_kernel_driver(self, bus: int, address: int) -> None:
    """Release a topology-selected FTDI before pylibftdi tries to claim it."""
    interface = max(0, (self._interface_select or 1) - 1)
    device = self._usb_device_at(bus, address)
    if device is None:
      raise RuntimeError(f"USB device at bus {bus}, address {address} disappeared before open")
    try:
      try:
        kernel_driver_active = device.is_kernel_driver_active(interface)
      except NotImplementedError:
        logger.debug("USB backend does not support inspecting kernel-driver state")
        return
      if kernel_driver_active:
        try:
          device.detach_kernel_driver(interface)
        except NotImplementedError:
          logger.debug("USB backend does not support detaching kernel drivers")
          return
        self._detached_kernel_driver = (bus, address, interface)
    finally:
      usb.util.dispose_resources(device)

  def _reattach_kernel_driver(self) -> None:
    detached = self._detached_kernel_driver
    self._detached_kernel_driver = None
    if detached is None:
      return
    bus, address, interface = detached
    device = self._usb_device_at(bus, address)
    if device is None:
      logger.warning(
        "Could not reattach the kernel driver: USB device at bus %s, address %s disappeared",
        bus,
        address,
      )
      return
    try:
      if not device.is_kernel_driver_active(interface):
        device.attach_kernel_driver(interface)
    except (NotImplementedError, usb.core.USBError) as exc:
      logger.warning("Could not reattach the FTDI kernel driver: %s", exc)
    finally:
      usb.util.dispose_resources(device)

  def _setup_sync(self) -> None:
    """Resolve and open the device on the serialized worker."""
    if self.ftdi_library == "d2xx":
      if self._d2xx_dev is not None:
        self._d2xx_dev.close()
      self._d2xx_dev = None
      d2xx_dev = _D2XXDevice(serial_number=self._resolve_d2xx_serial_number())
      try:
        d2xx_dev.open()
      except D2XXError as error:
        if error.status == _FT_DEVICE_NOT_OPENED:
          raise D2XXError(
            f"{error}: another program holds the channel (close the vendor software or stop its "
            "service), or a driver other than FTDI's is bound to it (e.g. installed with Zadig; "
            "restore FTDI's driver in Device Manager, or pass ftdi_library='libftdi')",
            status=error.status,
          ) from error
        raise
      self._d2xx_dev = d2xx_dev
      return
    if self._dev is not None and not self._dev.closed:
      self._dev.close()
    self._dev = None

    dev: Optional[Device] = None
    try:
      if self._usb_address is not None:
        usb_bus, usb_device_address = self._resolve_device_location()
        self._detach_kernel_driver(usb_bus, usb_device_address)
        self._device_id = self._usb_address
        dev = _USBAddressDevice(
          lazy_open=True,
          usb_bus=usb_bus,
          usb_device_address=usb_device_address,
          pid=self._pid,
          vid=self._vid,
          interface_select=self._interface_select,
        )
      else:
        self._device_id = self._resolve_device_serial()
        dev = Device(
          lazy_open=True,
          device_id=self.device_id,
          pid=self._pid,
          vid=self._vid,
          interface_select=self._interface_select,
        )
      dev.open()
    except BaseException:
      if dev is not None:
        try:
          dev.close()
        except Exception:
          logger.warning("Failed to close FTDI device after setup failure", exc_info=True)
      try:
        self._reattach_kernel_driver()
      except Exception:
        logger.warning("Failed to reattach FTDI kernel driver after setup failure", exc_info=True)
      raise
    self._dev = dev

  def _stop_reads(self) -> None:
    """Stop starting reads and cancel any pending read scope when the FTDI lifespan exits."""
    self._closing = True
    if self._pending_read is not None:
      self._pending_read.scope.cancel()

  def _clear_task_group(self) -> None:
    """Drop the task group reference once its lifespan-owned children have finished."""
    self._tg = None
    self._closing = False

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Initialize the FTDI device connection with device resolution."""
    await super()._enter_lifespan(stack)
    self._connection_lost = False
    self._closing = False
    # Registered before the task group so the stack closes the device only after that group
    # has cancelled and joined every read and write it owns.
    stack.push_shielded_async_callback(self._close)
    stack.callback(self._clear_task_group)
    self._tg = await stack.enter_task_group()
    stack.callback(self._stop_reads)
    try:
      await anyio.to_thread.run_sync(self._setup_sync, limiter=self._limiter)
    except BaseException as exc:
      if HAS_PYLIBFTDI and isinstance(exc, FtdiError):
        raise RuntimeError(
          f"Failed to open FTDI device for '{self.human_readable_device_name}': {exc}. "
          "Is the device connected? Is it in use by another process? "
          "Try restarting the kernel."
        ) from exc
      raise
    logger.info("Successfully opened FTDI device: %s (%s)", self.device_id, self.ftdi_library)
    if self.ftdi_library == "d2xx":
      capturer.record(FTDICommand(device_id=self.device_id, action="setup", data="d2xx"))

  async def _close(self) -> None:
    """Close the device after all lifespan-owned worker calls finish."""
    async with self._operation_lock:
      await self._close_locked()

  async def _close_locked(self) -> None:
    """Dispose the current device while the operation lock excludes worker calls."""
    d2xx_dev = self._d2xx_dev
    dev = self._dev
    try:
      if d2xx_dev is not None:
        await anyio.to_thread.run_sync(d2xx_dev.close, limiter=self._limiter)
      if dev is not None:
        await anyio.to_thread.run_sync(dev.close, limiter=self._limiter)
    except BaseException:
      try:
        await anyio.to_thread.run_sync(self._reattach_kernel_driver, limiter=self._limiter)
      except Exception:
        logger.warning("Failed to reattach FTDI kernel driver after close failure", exc_info=True)
      raise
    else:
      await anyio.to_thread.run_sync(self._reattach_kernel_driver, limiter=self._limiter)
    finally:
      self._d2xx_dev = None
      self._dev = None
      self._discard_reads()

  async def reconnect(self) -> None:
    """Replace the FTDI handle without leaving its lifespan-owned task group."""
    if not self.is_active:
      raise RuntimeError("FTDI reconnect requires an active lifespan")
    async with self._operation_lock:
      if not self.is_active:
        raise RuntimeError("FTDI reconnect requires an active lifespan")
      with anyio.CancelScope(shield=True):
        await self._close_locked()
      try:
        await anyio.to_thread.run_sync(self._setup_sync, limiter=self._limiter)
        await anyio.lowlevel.checkpoint_if_cancelled()
      except BaseException:
        with anyio.CancelScope(shield=True):
          await self._close_locked()
        raise
      self._connection_lost = False

  async def _device_worker(self, operation: Callable[[], _T]) -> _T:
    """Run a device call, invalidating the connection on a hard I/O error."""
    try:
      return await anyio.to_thread.run_sync(operation, limiter=self._limiter)
    except BaseException as error:
      if is_ftdi_transport_error(error) or (
        isinstance(error, OSError) and not isinstance(error, TimeoutError)
      ):
        self._connection_lost = True
      raise

  @property
  def device_id(self) -> str:
    if self._device_id is None:
      raise RuntimeError("Device not initialized; enter its lifespan first.")
    return self._device_id

  async def set_baudrate(self, baudrate: int):
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(partial(d2xx.set_baudrate, baudrate=baudrate))
      else:
        dev = self.dev
        await self._device_worker(partial(self._set_baudrate_sync, dev, baudrate))
    logger.log(LOG_LEVEL_IO, "[%s] set_baudrate %s", self._device_id, baudrate)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_baudrate", data=str(baudrate))
    )

  @staticmethod
  def _set_baudrate_sync(dev: "Device", baudrate: int) -> None:
    """Set baud rate on the serialized device worker."""
    dev.baudrate = baudrate

  async def set_rts(self, level: bool):
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(partial(d2xx.set_rts, level=level))
      else:
        dev = self.dev
        await self._device_worker(partial(dev.ftdi_fn.ftdi_setrts, level))
    logger.log(LOG_LEVEL_IO, "[%s] set_rts %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_rts", data=str(level)))

  async def set_dtr(self, level: bool):
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(partial(d2xx.set_dtr, level=level))
      else:
        dev = self.dev
        await self._device_worker(partial(dev.ftdi_fn.ftdi_setdtr, level))
    logger.log(LOG_LEVEL_IO, "[%s] set_dtr %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_dtr", data=str(level)))

  async def usb_reset(self):
    self._discard_reads()
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(d2xx.reset)
      else:
        dev = self.dev
        await self._device_worker(dev.ftdi_fn.ftdi_usb_reset)
      self._discard_reads()
    logger.log(LOG_LEVEL_IO, "[%s] usb_reset", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_reset", data=""))

  async def set_latency_timer(self, latency: int):
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(partial(d2xx.set_latency_timer, latency=latency))
      else:
        dev = self.dev
        await self._device_worker(partial(dev.ftdi_fn.ftdi_set_latency_timer, latency))
    logger.log(LOG_LEVEL_IO, "[%s] set_latency_timer %s", self._device_id, latency)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_latency_timer", data=str(latency))
    )

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(
          partial(d2xx.set_line_property, bits=bits, stopbits=stopbits, parity=parity)
        )
      else:
        dev = self.dev
        await self._device_worker(
          partial(dev.ftdi_fn.ftdi_set_line_property, bits, stopbits, parity)
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
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(partial(d2xx.set_flowctrl, flowctrl=flowctrl))
      else:
        dev = self.dev
        await self._device_worker(partial(dev.ftdi_fn.ftdi_setflowctrl, flowctrl))
    logger.log(LOG_LEVEL_IO, "[%s] set_flowctrl %s", self._device_id, flowctrl)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_flowctrl", data=str(flowctrl))
    )

  async def usb_purge_rx_buffer(self):
    self._discard_reads()
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(partial(d2xx.purge, mask=_FT_PURGE_RX))
      else:
        dev = self.dev
        await self._device_worker(dev.ftdi_fn.ftdi_usb_purge_rx_buffer)
      self._discard_reads()
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_rx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_rx_buffer", data=""))

  async def usb_purge_tx_buffer(self):
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        await self._device_worker(partial(d2xx.purge, mask=_FT_PURGE_TX))
      else:
        dev = self.dev
        await self._device_worker(dev.ftdi_fn.ftdi_usb_purge_tx_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_tx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_tx_buffer", data=""))

  async def poll_modem_status(self) -> int:
    async with self._operation_lock:
      if self.ftdi_library == "d2xx":
        d2xx = self._d2xx
        value = await self._device_worker(d2xx.poll_modem_status)
      else:
        dev = self.dev
        stat = ctypes.c_ushort(0)
        await self._device_worker(partial(dev.ftdi_fn.ftdi_poll_modem_status, ctypes.byref(stat)))
        value = stat.value
    logger.log(LOG_LEVEL_IO, "[%s] poll_modem_status %s", self._device_id, value)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="poll_modem_status", data=str(value))
    )
    return value

  async def request_serial(self) -> str:
    return self.device_id

  def _discard_reads(self) -> None:
    """Drop read data buffered here and in flight, once the caller has declared it stale."""
    self._unread.clear()
    self._pending_read = None

  def _keep(self, chunk) -> None:
    """Add what came off the wire to the buffer that read() serves from.

    pylibftdi returns str, decoded with latin-1, when the device was opened in text mode. setup()
    opens it in byte mode, but latin-1 round-trips every byte value, so encoding back gives
    exactly what was on the wire either way.
    """
    self._unread.extend(chunk if isinstance(chunk, bytes) else chunk.encode("latin-1"))

  def _current_handle(self) -> Union["Device", _D2XXDevice]:
    """Return the active device handle for the selected FTDI library."""
    return self._d2xx if self.ftdi_library == "d2xx" else self.dev

  async def _run_read(
    self,
    pending: _PendingRead,
    handle: Union["Device", _D2XXDevice],
    num_bytes: int,
  ) -> None:
    """Run one read owned by the resource lifespan and retain its result if still current."""
    with pending.scope:
      try:
        async with self._operation_lock:
          if self._closing:
            raise RuntimeError("Device not initialized; enter its lifespan first.")
          current = self._d2xx_dev if self.ftdi_library == "d2xx" else self._dev
          if handle is not current:
            raise ConnectionError("FTDI connection replaced before read")
          if self._connection_lost:
            raise ConnectionError("FTDI connection lost; reconnect or enter a new lifespan")
          chunk = await self._device_worker(partial(handle.read, num_bytes))
        if pending is self._pending_read:
          self._keep(chunk)
      except BaseException as error:
        pending.error = error
      finally:
        if pending is self._pending_read:
          self._pending_read = None
        pending.completed.set()

  async def write(self, data: bytes) -> int:
    """Write data to the device. Returns the number of bytes written."""
    async with self._operation_lock:
      handle = self._current_handle()
      await anyio.lowlevel.checkpoint()
      with anyio.CancelScope(shield=True):
        result = cast(int, await self._device_worker(partial(handle.write, data)))
      if result != len(data):
        self._connection_lost = True
        raise OSError(f"FTDI short write: wrote {result} of {len(data)} bytes")
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._device_id, data)
    capturer.record(FTDICommand(device_id=self.device_id, action="write", data=data.hex()))
    emit_event(
      "io.write",
      transport="ftdi",
      device=self.human_readable_device_name,
      device_id=self.device_id,
      data=data.hex(),
    )
    await anyio.lowlevel.checkpoint_if_cancelled()
    return result

  async def read(self, num_bytes: int = 1) -> bytes:
    if num_bytes <= 0:
      raise ValueError("num_bytes must be positive")
    async with self._reader_lock:
      handle = self._current_handle()
      while not self._unread:
        pending = self._pending_read
        if pending is None:
          task_group = self._tg
          if task_group is None or self._closing:
            raise RuntimeError("Device not initialized; enter its lifespan first.")
          pending = _PendingRead(completed=anyio.Event(), scope=anyio.CancelScope(shield=True))
          self._pending_read = pending
          task_group.start_soon(self._run_read, pending, handle, num_bytes)
        await pending.completed.wait()
        if pending.error is not None:
          if isinstance(pending.error, anyio.get_cancelled_exc_class()):
            await anyio.lowlevel.checkpoint_if_cancelled()
            raise RuntimeError("Device not initialized; enter its lifespan first.")
          raise pending.error
        if not self._unread:
          await anyio.sleep(0.001)
      data = bytes(self._unread[:num_bytes])
      del self._unread[:num_bytes]
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._device_id, data)
    capturer.record(FTDICommand(device_id=self.device_id, action="read", data=data.hex()))
    emit_event(
      "io.read",
      transport="ftdi",
      device=self.human_readable_device_name,
      device_id=self.device_id,
      data=data.hex(),
    )
    return data

  async def readline(  # type: ignore # very dumb it's reading from pyserial
    self, terminator: bytes = b"\n", timeout: Optional[float] = None
  ) -> bytes:
    """Read until `terminator`, returning the line with the terminator still on it.

    Assembled from single-byte reads: pylibftdi's own `readline` raises `TypeError` unless the
    device was opened in text mode, and `setup` opens it in byte mode.

    Args:
      terminator: byte sequence that ends the line.
      timeout: seconds to wait for a complete line. None waits indefinitely.

    Raises:
      ValueError: if `terminator` is empty.
      TimeoutError: if no complete line arrives within `timeout`.
    """
    if not terminator:
      raise ValueError("terminator must be at least one byte")
    line = bytearray()
    try:
      with anyio.fail_after(timeout):
        while not line.endswith(terminator):
          line.extend(await self.read(1))
    except TimeoutError as exc:
      raise TimeoutError(
        f"'{self.human_readable_device_name}' sent no complete line within {timeout} s; "
        f"received {bytes(line)!r} so far."
      ) from exc
    logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._device_id, bytes(line))
    return bytes(line)

  def serialize(self):
    return {
      "human_readable_device_name": self.human_readable_device_name,
      "device_id": self._device_id,
      "vid": self._vid,
      "pid": self._pid,
      "usb_address": self._usb_address,
      "ftdi_library": self.ftdi_library,
      "usb_product": self._usb_product,
    }


class FTDIValidator(FTDI):
  def __init__(self, cr: "CaptureReader", human_readable_device_name: str, device_id: str):
    super().__init__(human_readable_device_name=human_readable_device_name, device_id=device_id)
    self.cr = cr

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Enter a validator lifespan without opening an FTDI device, consuming a recorded D2XX setup if present."""
    if isinstance(self.cr, CaptureReader):
      commands, index = self.cr.commands, self.cr._command_idx
      if (
        index < len(commands)
        and commands[index].get("module") == "ftdi"
        and commands[index].get("action") == "setup"
        and commands[index].get("device_id") == self._device_id
      ):
        self.cr.next_command()

  async def reconnect(self) -> None:
    """Validate a reconnect without accessing the hardware."""
    if not self.is_active:
      raise RuntimeError("FTDI reconnect requires an active lifespan")

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
    await anyio.lowlevel.checkpoint()
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
    if num_bytes <= 0:
      raise ValueError("num_bytes must be positive")
    await anyio.lowlevel.checkpoint()
    next_command = FTDICommand(**self.cr.next_command())
    if not (
      next_command.module == "ftdi"
      and next_command.device_id == self._device_id
      and next_command.action == "read"
      # data is hex, so two characters per byte, and a short read is normal.
      and len(next_command.data) <= num_bytes * 2
    ):
      raise ValidationError(f"Next line is {next_command}, expected FTDI read {self._device_id}")
    data = bytes.fromhex(next_command.data)
    if not data:
      raise anyio.EndOfStream
    return data

  # readline is inherited: it assembles the line from read(), so replay consumes recorded
  # read commands directly.
