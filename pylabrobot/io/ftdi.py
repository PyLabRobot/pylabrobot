import ctypes
import logging
from dataclasses import dataclass
from functools import partial
from typing import Callable, Optional, TypeVar, cast

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


def is_ftdi_transport_error(error: BaseException) -> bool:
  """Return whether an exception was raised by the optional pylibftdi transport."""
  if not HAS_PYLIBFTDI:
    return False
  return isinstance(error, FtdiError)


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
  error: Optional[BaseException] = None


class FTDI(ReconnectableConnectionIO):
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
    usb_address: Optional[str] = None,
  ):
    super().__init__()
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

    self.human_readable_device_name = human_readable_device_name
    self._device_id = device_id
    self._vid = vid
    self._pid = pid
    self._interface_select = interface_select
    self._usb_address = usb_address

    # Will be resolved in setup()
    self._dev: Optional[Device] = None
    self._tg: Optional[anyio.abc.TaskGroup] = None
    self._operation_lock = anyio.Lock()
    self._reader_lock = anyio.Lock()
    self._limiter = anyio.CapacityLimiter(1)
    # Bytes off the wire that no read has taken yet, and the read still in flight, if any.
    self._unread = bytearray()
    self._pending_read: Optional[_PendingRead] = None
    self._detached_kernel_driver: Optional[tuple[int, int, int]] = None
    self._connection_lost = False

    if get_capture_or_validation_active():
      raise RuntimeError(
        f"Cannot create a new FTDI object for '{self.human_readable_device_name}' while capture or validation is active"
      )

  @property
  def dev(self) -> "Device":
    if self._connection_lost:
      raise ConnectionError("FTDI connection lost; reconnect or enter a new lifespan")
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

  def _clear_task_group(self) -> None:
    """Drop the task group reference once its lifespan-owned children have finished."""
    self._tg = None

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Initialize the FTDI device connection with device resolution."""
    await super()._enter_lifespan(stack)
    self._connection_lost = False
    # Registered before the task group so the stack closes the device only after that group
    # has cancelled and joined every read and write it owns.
    stack.push_shielded_async_callback(self._close)
    stack.callback(self._clear_task_group)
    self._tg = await stack.enter_task_group()
    try:
      await anyio.to_thread.run_sync(self._setup_sync, limiter=self._limiter)
    except FtdiError as exc:
      raise RuntimeError(
        f"Failed to open FTDI device for '{self.human_readable_device_name}': {exc}. "
        "Is the device connected? Is it in use by another process? "
        "Try restarting the kernel."
      ) from exc
    logger.info(f"Successfully opened FTDI device: {self.device_id}")

  async def _close(self) -> None:
    """Close the device after all lifespan-owned worker calls finish."""
    async with self._operation_lock:
      await self._close_locked()

  async def _close_locked(self) -> None:
    """Dispose the current device while the operation lock excludes worker calls."""
    dev = self._dev
    try:
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
      raise RuntimeError("Device not initialized. Call setup() first.")
    return self._device_id

  async def set_baudrate(self, baudrate: int):
    async with self._operation_lock:
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
      dev = self.dev
      await self._device_worker(partial(dev.ftdi_fn.ftdi_setrts, level))
    logger.log(LOG_LEVEL_IO, "[%s] set_rts %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_rts", data=str(level)))

  async def set_dtr(self, level: bool):
    async with self._operation_lock:
      dev = self.dev
      await self._device_worker(partial(dev.ftdi_fn.ftdi_setdtr, level))
    logger.log(LOG_LEVEL_IO, "[%s] set_dtr %s", self._device_id, level)
    capturer.record(FTDICommand(device_id=self.device_id, action="set_dtr", data=str(level)))

  async def usb_reset(self):
    self._discard_reads()
    async with self._operation_lock:
      dev = self.dev
      await self._device_worker(dev.ftdi_fn.ftdi_usb_reset)
    logger.log(LOG_LEVEL_IO, "[%s] usb_reset", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_reset", data=""))

  async def set_latency_timer(self, latency: int):
    async with self._operation_lock:
      dev = self.dev
      await self._device_worker(partial(dev.ftdi_fn.ftdi_set_latency_timer, latency))
    logger.log(LOG_LEVEL_IO, "[%s] set_latency_timer %s", self._device_id, latency)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_latency_timer", data=str(latency))
    )

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    async with self._operation_lock:
      dev = self.dev
      await self._device_worker(partial(dev.ftdi_fn.ftdi_set_line_property, bits, stopbits, parity))
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
      dev = self.dev
      await self._device_worker(partial(dev.ftdi_fn.ftdi_setflowctrl, flowctrl))
    logger.log(LOG_LEVEL_IO, "[%s] set_flowctrl %s", self._device_id, flowctrl)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="set_flowctrl", data=str(flowctrl))
    )

  async def usb_purge_rx_buffer(self):
    self._discard_reads()
    async with self._operation_lock:
      dev = self.dev
      await self._device_worker(dev.ftdi_fn.ftdi_usb_purge_rx_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_rx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_rx_buffer", data=""))

  async def usb_purge_tx_buffer(self):
    async with self._operation_lock:
      dev = self.dev
      await self._device_worker(dev.ftdi_fn.ftdi_usb_purge_tx_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] usb_purge_tx_buffer", self._device_id)
    capturer.record(FTDICommand(device_id=self.device_id, action="usb_purge_tx_buffer", data=""))

  async def poll_modem_status(self) -> int:
    async with self._operation_lock:
      dev = self.dev
      stat = ctypes.c_ushort(0)
      await self._device_worker(partial(dev.ftdi_fn.ftdi_poll_modem_status, ctypes.byref(stat)))
    logger.log(LOG_LEVEL_IO, "[%s] poll_modem_status %s", self._device_id, stat.value)
    capturer.record(
      FTDICommand(device_id=self.device_id, action="poll_modem_status", data=str(stat.value))
    )
    return stat.value

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

  async def _run_read(self, pending: _PendingRead, dev: "Device", num_bytes: int) -> None:
    """Run one read owned by the resource lifespan and retain its result if still current."""
    try:
      async with self._operation_lock:
        if dev is not self._dev:
          raise ConnectionError("FTDI connection replaced before read")
        if self._connection_lost:
          raise ConnectionError("FTDI connection lost; reconnect or enter a new lifespan")
        chunk = await self._device_worker(partial(dev.read, num_bytes))
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
      dev = self.dev
      await anyio.lowlevel.checkpoint()
      with anyio.CancelScope(shield=True):
        result = cast(int, await self._device_worker(partial(dev.write, data)))
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
    return result

  async def read(self, num_bytes: int = 1) -> bytes:
    if num_bytes <= 0:
      raise ValueError("num_bytes must be positive")
    async with self._reader_lock:
      dev = self.dev
      while not self._unread:
        pending = self._pending_read
        if pending is None:
          task_group = self._tg
          if task_group is None:
            raise RuntimeError("Device not initialized. Call setup() first.")
          pending = _PendingRead(completed=anyio.Event())
          self._pending_read = pending
          task_group.start_soon(self._run_read, pending, dev, num_bytes)
        await pending.completed.wait()
        if pending.error is not None:
          raise pending.error
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
    }


class FTDIValidator(FTDI):
  def __init__(self, cr: "CaptureReader", human_readable_device_name: str, device_id: str):
    super().__init__(human_readable_device_name=human_readable_device_name, device_id=device_id)
    self.cr = cr

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Enter a validator lifespan without opening an FTDI device."""

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

  # readline is inherited: it assembles the line from read(), so a replay goes through the
  # recorded reads rather than a "readline" command that is no longer recorded.
