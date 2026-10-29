from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from io import UnsupportedOperation
from typing import TYPE_CHECKING, Callable, Dict, List, Literal, Optional, Union

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.events import emit_event
from pylabrobot.io.boundary import BoundaryCursor, BoundaryDiscard, BoundaryRead
from pylabrobot.io.capture import Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import ReconnectableConnectionIO
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

try:
  import libusb_package  # type: ignore[import-not-found]
  import usb.core
  import usb.util

  USE_USB = True
except ImportError as e:
  USE_USB = False
  _USB_IMPORT_ERROR = e


if TYPE_CHECKING:
  import usb.core

  from pylabrobot.io.capture import CaptureReader


logger = logging.getLogger(__name__)


class _UnsetTimeout:
  """Distinguish an omitted public deadline from an explicit legacy timeout."""


_UNSET_TIMEOUT = _UnsetTimeout()


@dataclass
class USBCommand(Command):
  data: str
  data_hex: str | None = None
  endpoint: int | None = None
  marks: list[dict[str, object]] = field(default_factory=list)
  boundary: str | None = None
  started_at_boundary: bool | None = None
  ended_at_boundary: bool | None = None
  limit_reached: bool | None = None
  discarded_bytes: int | None = None
  max_bytes: int | None = None
  consume_immediate_boundary: bool | None = None

  def __init__(
    self,
    device_id: str,
    action: str,
    data: str,
    module: str = "usb",
    endpoint: int | None = None,
    marks: list[dict[str, object]] | None = None,
    data_hex: str | None = None,
    boundary: str | None = None,
    started_at_boundary: bool | None = None,
    ended_at_boundary: bool | None = None,
    limit_reached: bool | None = None,
    discarded_bytes: int | None = None,
    max_bytes: int | None = None,
    consume_immediate_boundary: bool | None = None,
  ):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data
    self.data_hex = data_hex
    self.endpoint = endpoint
    self.marks = marks or []
    self.boundary = boundary
    self.started_at_boundary = started_at_boundary
    self.ended_at_boundary = ended_at_boundary
    self.limit_reached = limit_reached
    self.discarded_bytes = discarded_bytes
    self.max_bytes = max_bytes
    self.consume_immediate_boundary = consume_immediate_boundary


@dataclass(frozen=True)
class USBLegacyBoundaryProfile:
  """Explicit assumptions for inferring marks from old byte-only USB captures."""

  endpoint: int
  packet_size: int
  assume_uncapped_read: bool
  assume_zlp_after_full_read: bool = False

  def __post_init__(self) -> None:
    if self.packet_size <= 0:
      raise ValueError("packet_size must be positive")


class USB(ReconnectableConnectionIO):
  """Own a stable USB handle between explicit reconnect calls."""

  def __init__(
    self,
    id_vendor: int,
    id_product: int,
    human_readable_device_name: str,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: float = 3,
    read_timeout: Union[int, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    write_timeout: int = 30,
    configuration_callback: Optional[Callable[["usb.core.Device"], None]] = None,
    max_workers: int = 1,
    read_endpoint_address: Optional[int] = None,
    write_endpoint_address: Optional[int] = None,
    default_boundary: Literal["packet", "short_packet_end"] = "packet",
  ):
    """Initialize an io.USB object.

    Args:
      id_vendor: The USB vendor ID of the machine.
      id_product: The USB product ID of the machine.
      human_readable_device_name: A human-readable name for the device, used in error messages.
      device_address: The USB device_address of the machine. If `None`, use the first device found.
        This is useful for machines that have no unique serial number, such as the Hamilton STAR.
      serial_number: The serial number of the machine. If `None`, use the first device found.
      packet_read_timeout: The timeout for reading packets from the machine in seconds.
      read_timeout: The timeout for reading from the machine in seconds.
      write_timeout: The timeout for writing to the machine in seconds.
      read_endpoint_address: The address of the read endpoint. If `None`, find the first IN endpoint.
      write_endpoint_address: The address of the write endpoint. If `None`, find the first OUT endpoint.
      configuration_callback: A callback that takes the device object as an argument and performs
        any necessary configuration. If `None`, `dev.set_configuration()` is called.
      max_workers: The maximum number of worker threads for USB I/O operations.
    """

    super().__init__()

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new USB object while capture or validation is active")

    if not isinstance(read_timeout, _UnsetTimeout) and read_timeout is not None:
      assert packet_read_timeout < read_timeout, (
        "packet_read_timeout must be smaller than read_timeout."
      )

    self._id_vendor = id_vendor
    self._id_product = id_product
    self._device_address = device_address
    self._serial_number = serial_number

    self.packet_read_timeout = packet_read_timeout
    if isinstance(read_timeout, _UnsetTimeout):
      self.read_timeout: Optional[int] = None
    else:
      warnings.warn(
        "USB.read_timeout is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
      self.read_timeout = read_timeout
    self.write_timeout = write_timeout
    self.read_endpoint_address = read_endpoint_address
    self.write_endpoint_address = write_endpoint_address
    self.configuration_callback = configuration_callback
    self.max_workers = max_workers
    if default_boundary not in ("packet", "short_packet_end"):
      raise ValueError("Unsupported USB boundary")
    self.default_boundary = default_boundary

    self.dev: Optional[usb.core.Device] = None  # TODO: make this a property
    self._connection_lost = False
    self.read_endpoint: Optional[usb.core.Endpoint] = None
    self.write_endpoint: Optional[usb.core.Endpoint] = None

    self._read_limiter = anyio.CapacityLimiter(max_workers)
    self._write_limiter = anyio.CapacityLimiter(max_workers)
    self._read_lock = anyio.Lock()
    self._write_lock = anyio.Lock()
    self._connection_lock = anyio.Lock()
    self._reconnecting = False
    self._unread: Dict[int, bytearray] = {}
    self._cursors: dict[int, BoundaryCursor] = {}

    # unique id in the logs
    self._unique_id = f"[{hex(self._id_vendor)}:{hex(self._id_product)}][{self._serial_number or ''}][{self._device_address or ''}]"
    self.human_readable_device_name = human_readable_device_name

  def _require_connection_usable(self) -> None:
    """Reject I/O on a handle that reported a hard failure."""
    if self._connection_lost or self._reconnecting:
      raise ConnectionError("USB connection lost; enter a new lifespan")

  def _resolve_read_endpoint(self, endpoint: int | None) -> "usb.core.Endpoint":
    """Resolve an IN endpoint without modifying its cursor."""
    if self.dev is None or self.read_endpoint is None:
      raise RuntimeError(f"USB device for '{self.human_readable_device_name}' is not connected.")
    if endpoint is None:
      return self.read_endpoint
    cfg = self.dev.get_active_configuration()
    selected = usb.util.find_descriptor(
      cfg[(0, 0)], custom_match=lambda e: e.bEndpointAddress == endpoint
    )
    if selected is None:
      raise ValueError(f"Endpoint 0x{endpoint:02x} not found.")
    return selected

  @staticmethod
  def _packet_capacity(endpoint: "usb.core.Endpoint") -> int:
    """Return the payload bytes in one physical packet."""
    return int(endpoint.wMaxPacketSize) & 0x7FF

  def _supported_boundary(self, endpoint: "usb.core.Endpoint", boundary: str) -> None:
    """Reject endpoint kinds without trustworthy physical packet completions."""
    if (
      boundary not in ("packet", "short_packet_end")
      or endpoint.bmAttributes & 3 not in (2, 3)
      or not endpoint.bEndpointAddress & 0x80
    ):
      raise UnsupportedOperation(f"USB {boundary} boundary is not supported on this endpoint")

  def _record_cursor_operation(
    self,
    action: str,
    endpoint: int,
    data: bytes,
    result: BoundaryRead | BoundaryDiscard | None = None,
    boundary: str | None = None,
    max_bytes: int | None = None,
    consume_immediate_boundary: bool | None = None,
  ) -> None:
    """Record one public read or discard and its observed endpoint marks."""
    cursor = self._cursors[endpoint]
    capturer.record(
      USBCommand(
        device_id=self._unique_id,
        action=action,
        data=data.decode("unicode_escape", errors="backslashreplace"),
        endpoint=endpoint,
        data_hex=data.hex(),
        marks=[
          {
            "offset": mark.offset,
            "kind": mark.kind,
            "pending": mark.pending,
            "provenance": "observed",
          }
          for mark in cursor.last_operation_marks
        ],
        boundary=boundary,
        started_at_boundary=result.started_at_boundary if result is not None else None,
        ended_at_boundary=result.ended_at_boundary if result is not None else None,
        limit_reached=result.limit_reached if result is not None else None,
        discarded_bytes=result.discarded_bytes if isinstance(result, BoundaryDiscard) else None,
        max_bytes=max_bytes,
        consume_immediate_boundary=consume_immediate_boundary,
      )
    )

  async def _acquire_native_packet(self, endpoint: "usb.core.Endpoint") -> None:
    """Acquire one packet, retaining it even if cancellation arrives during I/O."""
    capacity = self._packet_capacity(endpoint)
    if capacity <= 0:
      raise ValueError("USB endpoint has no packet payload capacity")
    with anyio.CancelScope(shield=True):
      packet = await anyio.to_thread.run_sync(
        lambda: self._read_packet_sync(
          size=capacity,
          endpoint=None if endpoint is self.read_endpoint else endpoint.bEndpointAddress,
        ),
        limiter=self._read_limiter,
      )
      if packet is not None:
        if len(packet) > capacity:
          self._connection_lost = True
          raise OSError(f"USB packet exceeds endpoint capacity of {capacity} bytes")
        marks = (
          frozenset({"packet", "short_packet_end"})
          if len(packet) < capacity
          else frozenset({"packet"})
        )
        self._cursors[endpoint.bEndpointAddress].append(bytes(packet), marks)
    await anyio.lowlevel.checkpoint_if_cancelled()
    if packet is None:
      await anyio.lowlevel.checkpoint()

  async def write(
    self,
    data: bytes,
    timeout: Optional[float] = None,
    *,
    on_dispatch: Callable[[], None] | None = None,
  ):
    """Write data to the device.

    Args:
      data: The data to write.
      timeout: The timeout for writing to the device in seconds. If `None`, use the default timeout
        (specified by the `write_timeout` attribute).
      on_dispatch: Optional notification immediately before the native write is attempted. Runs on
        the USB worker thread; must not block or raise.
    """

    self._require_connection_usable()
    if self.dev is None or self.read_endpoint is None:
      raise RuntimeError(f"USB device for '{self.human_readable_device_name}' is not connected.")

    if timeout is None:
      timeout = self.write_timeout

    async with self._write_lock:
      self._require_connection_usable()
      write_endpoint = self.write_endpoint
      dev = self.dev
      if dev is None or write_endpoint is None:
        raise RuntimeError(
          f"Call setup() first for USB device '{self.human_readable_device_name}'."
        )
      await anyio.lowlevel.checkpoint()
      try:
        with anyio.CancelScope(shield=True):

          def dispatch() -> int:
            if on_dispatch is not None:
              on_dispatch()
            return int(dev.write(write_endpoint, data, timeout=int(timeout * 1000)))

          written = await anyio.to_thread.run_sync(
            dispatch,
            limiter=self._write_limiter,
          )
          if written != len(data):
            self._connection_lost = True
            raise OSError(f"USB short write: wrote {written} of {len(data)} bytes")
          if len(data) % write_endpoint.wMaxPacketSize == 0:
            # send a zero-length packet to indicate the end of the transfer
            zlp_written = await anyio.to_thread.run_sync(
              lambda: dev.write(write_endpoint, b"", timeout=int(timeout * 1000)),
              limiter=self._write_limiter,
            )
            if zlp_written != 0:
              self._connection_lost = True
              raise OSError(f"USB zero-length packet wrote {zlp_written} bytes")
      except usb.core.USBTimeoutError:
        self._connection_lost = True
        raise
      except (usb.core.USBError, OSError):
        self._connection_lost = True
        raise
    logger.log(LOG_LEVEL_IO, "%s write: %s", self._unique_id, data)
    capturer.record(
      USBCommand(
        device_id=self._unique_id,
        action="write",
        data=data.decode("unicode_escape", errors="backslashreplace"),
      )
    )
    emit_event(
      "io.write",
      transport="usb",
      device=self.human_readable_device_name,
      device_id=self._unique_id,
      data=data.decode("utf-8", errors="backslashreplace"),
    )

  def _read_packet_sync(
    self,
    size: Optional[int] = None,
    timeout: Optional[float] = None,
    endpoint: Optional[int] = None,
    *,
    during_setup: bool = False,
  ) -> Optional[bytearray]:
    """Read one USB transfer, which can contain multiple endpoint packets.

    Args:
      size: The maximum number of bytes to read. If `None`, use the default transfer size.
      timeout: The timeout for reading from the device in seconds. If `None`, use the default
        timeout (specified by the `packet_read_timeout` attribute).
      endpoint: The endpoint address to read from. If `None`, use the default read endpoint.
      during_setup: Allow the connection-establishment drain while reconnect hides the new handle.

    Returns:
      A bytearray containing the data read, or None if no data was received.
    """

    if not during_setup:
      self._require_connection_usable()
    if self.dev is None or self.read_endpoint is None:
      raise RuntimeError(f"USB device for '{self.human_readable_device_name}' is not connected.")

    ep = endpoint if endpoint is not None else self.read_endpoint
    if ep is None:
      raise RuntimeError("Read endpoint not found. Call setup() first.")

    if isinstance(ep, int):
      cfg = self.dev.get_active_configuration()
      ep_obj = usb.util.find_descriptor(
        cfg[(0, 0)], custom_match=lambda e: e.bEndpointAddress == ep
      )
      if ep_obj is None:
        raise ValueError(f"Endpoint 0x{ep:02x} not found.")
    else:
      ep_obj = ep
    read_size = ep_obj.wMaxPacketSize if size is None else size

    if timeout is None:
      timeout = self.packet_read_timeout
    timeout = min(timeout, self.packet_read_timeout)

    try:
      res = self.dev.read(
        ep,
        read_size,
        timeout=max(1, int(timeout * 1000)),  # timeout in ms; zero means unlimited
      )

      if res is not None:
        return bytearray(res)
      return None
    except usb.core.USBTimeoutError:
      # A finite packet poll does not imply a disconnected device.
      return None
    except (usb.core.USBError, OSError):
      self._connection_lost = True
      raise

  async def _read_packet(
    self,
    size: Optional[int] = None,
    timeout: Optional[float] = None,
    endpoint: Optional[int] = None,
  ) -> Optional[bytearray]:
    """Read one packet on a worker thread."""

    self._require_connection_usable()
    if timeout is None:
      timeout = self.packet_read_timeout
    deadline = anyio.current_time() + timeout

    async with self._read_lock:
      self._require_connection_usable()
      while True:
        remaining_time = deadline - anyio.current_time()
        if remaining_time <= 0:
          return None
        packet = await anyio.to_thread.run_sync(
          lambda: self._read_packet_sync(
            size=size,
            timeout=min(remaining_time, self.packet_read_timeout),
            endpoint=endpoint,
          ),
          limiter=self._read_limiter,
        )
        if packet is not None:
          return packet

  async def read(
    self,
    timeout: Union[int, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    size: Optional[int] = None,
    endpoint: Optional[int] = None,
  ) -> bytes:
    """Wait for one nonempty packet from the selected endpoint.

    Args:
      timeout: Deprecated public timeout in seconds. Omit for no deadline.
      size: Maximum bytes from one packet. If `None`, use the endpoint's packet size.
      endpoint: The endpoint address to read from. If `None`, use the configured read endpoint.
    """

    self._require_connection_usable()
    if size is not None and size <= 0:
      raise ValueError("size must be positive")
    if self.dev is None or self.read_endpoint is None:
      raise RuntimeError(f"USB device for '{self.human_readable_device_name}' is not connected.")

    if isinstance(timeout, _UnsetTimeout):
      public_timeout = self.read_timeout
    else:
      warnings.warn(
        "USB.read(timeout=...) is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
      public_timeout = self.read_timeout if timeout is None else timeout

    read_endpoint = self._resolve_read_endpoint(endpoint)

    async with self._read_lock:
      self._require_connection_usable()
      endpoint_id = read_endpoint.bEndpointAddress
      cursor = self._cursors.setdefault(endpoint_id, BoundaryCursor(frozenset()))
      try:
        with anyio.fail_after(public_timeout):
          while True:
            await anyio.lowlevel.checkpoint_if_cancelled()
            result = cursor.read_bytes(size or self._packet_capacity(read_endpoint))
            if result:
              break
            await self._acquire_native_packet(read_endpoint)
      except TimeoutError as exc:
        raise TimeoutError(
          f"Timeout while reading from USB device '{self.human_readable_device_name}'."
        ) from exc
      self._record_cursor_operation("read", endpoint_id, result)
    logger.log(LOG_LEVEL_IO, "%s read: %s", self._unique_id, result)
    emit_event(
      "io.read",
      transport="usb",
      device=self.human_readable_device_name,
      device_id=self._unique_id,
      data=result.decode("utf-8", errors="backslashreplace"),
    )
    return result

  async def _take_boundary(
    self,
    max_bytes: int | None,
    *,
    boundary: Literal["packet", "short_packet_end"] | None,
    endpoint: int | None,
    consume_immediate_boundary: bool,
    discard: bool,
  ) -> BoundaryRead | BoundaryDiscard:
    """Take a selected delimiter on one endpoint without imposing a public deadline."""
    if max_bytes is not None and max_bytes <= 0:
      raise ValueError("max_bytes must be positive")
    self._require_connection_usable()
    selected = self._resolve_read_endpoint(endpoint)
    kind = self.default_boundary if boundary is None else boundary
    self._supported_boundary(selected, kind)
    async with self._read_lock:
      self._require_connection_usable()
      cursor = self._cursors.setdefault(
        selected.bEndpointAddress, BoundaryCursor(frozenset({"packet"}))
      )
      while True:
        await anyio.lowlevel.checkpoint_if_cancelled()
        result = cursor.take_to_boundary(
          max_bytes,
          boundary=kind,
          consume_immediate_boundary=consume_immediate_boundary,
          discard=discard,
        )
        if result is not None:
          self._record_cursor_operation(
            "discard_to_boundary" if discard else "read_to_boundary",
            selected.bEndpointAddress,
            result.data if isinstance(result, BoundaryRead) else b"",
            result,
            kind,
            max_bytes,
            consume_immediate_boundary,
          )
          return result
        await self._acquire_native_packet(selected)

  async def read_to_boundary(
    self,
    max_bytes: int | None = None,
    *,
    consume_immediate_boundary: bool = True,
    boundary: Literal["packet", "short_packet_end"] | None = None,
    endpoint: int | None = None,
  ) -> BoundaryRead:
    """Read to a physical packet or observed short-packet delimiter."""
    result = await self._take_boundary(
      max_bytes,
      boundary=boundary,
      endpoint=endpoint,
      consume_immediate_boundary=consume_immediate_boundary,
      discard=False,
    )
    assert isinstance(result, BoundaryRead)
    return result

  async def discard_to_boundary(
    self,
    max_bytes: int | None = None,
    *,
    consume_immediate_boundary: bool = False,
    boundary: Literal["packet", "short_packet_end"] | None = None,
    endpoint: int | None = None,
  ) -> BoundaryDiscard:
    """Discard through a selected physical USB delimiter."""
    result = await self._take_boundary(
      max_bytes,
      boundary=boundary,
      endpoint=endpoint,
      consume_immediate_boundary=consume_immediate_boundary,
      discard=True,
    )
    assert isinstance(result, BoundaryDiscard)
    return result

  async def drain(
    self,
    endpoint: Optional[int] = None,
    timeout: float = 0.05,
    size: Optional[int] = None,
    max_duration: float = 1,
  ) -> None:
    """Discard queued input from an endpoint until no packet arrives before the timeout."""
    self._require_connection_usable()
    if self.dev is None or self.read_endpoint is None:
      raise RuntimeError(f"USB device for '{self.human_readable_device_name}' is not connected.")

    if endpoint is not None:
      cfg = self.dev.get_active_configuration()
      if (
        usb.util.find_descriptor(cfg[(0, 0)], custom_match=lambda e: e.bEndpointAddress == endpoint)
        is None
      ):
        raise ValueError(f"Endpoint 0x{endpoint:02x} not found.")

    deadline = anyio.current_time() + max_duration
    async with self._read_lock:
      self._require_connection_usable()
      selected_id = endpoint if endpoint is not None else self.read_endpoint.bEndpointAddress
      self._unread.pop(selected_id, None)
      self._cursors.pop(selected_id, None)
      while (remaining := deadline - anyio.current_time()) > 0:
        packet = await anyio.to_thread.run_sync(
          lambda: self._read_packet_sync(
            size=size, timeout=min(timeout, remaining, self.packet_read_timeout), endpoint=endpoint
          ),
          limiter=self._read_limiter,
        )
        if packet is None:
          return

    raise TimeoutError(f"Timed out draining USB device '{self.human_readable_device_name}'.")

  def get_available_devices(self) -> List["usb.core.Device"]:
    """Get a list of available devices that match the specified vendor and product IDs, and serial
    number and device_address if specified."""

    found_devices = libusb_package.find(
      idVendor=self._id_vendor,
      idProduct=self._id_product,
      find_all=True,
    )
    devices: List["usb.core.Device"] = []
    for dev in found_devices:
      if self._device_address is not None:
        if dev.address is None:
          raise RuntimeError(
            f"A device address was specified for '{self.human_readable_device_name}', but the backend used for PyUSB does "
            "not support device addresses."
          )

        if dev.address != self._device_address:
          continue

      if self._serial_number is not None:
        if dev._serial_number is None:
          raise RuntimeError(
            f"A serial number was specified for '{self.human_readable_device_name}', but the device does not have a serial number."
          )

        if dev.serial_number != self._serial_number:
          continue

      devices.append(dev)

    return devices

  def list_available_devices(self) -> None:
    """Utility to list all devices that match the specified vendor and product IDs, and serial
    number and address if specified. You can use this to discover the serial number and address of
    your device, if using multiple. Note that devices may not have a unique serial number."""

    for dev in self.get_available_devices():
      print(dev)

  def _serialize_ctrl_transfer_request(
    self,
    bmRequestType: int,
    bRequest: int,
    wValue: int,
    wIndex: int,
    data_or_wLength: int,
  ) -> str:
    return " ".join(map(str, [bmRequestType, bRequest, wValue, wIndex, data_or_wLength]))

  async def ctrl_transfer(
    self,
    bmRequestType: int,
    bRequest: int,
    wValue: int,
    wIndex: int,
    data_or_wLength: int,
    timeout: Optional[int] = None,
  ) -> bytearray:
    self._require_connection_usable()
    if timeout is None:
      timeout = self.read_timeout if self.read_timeout is not None else 30

    async with self._write_lock:
      self._require_connection_usable()
      dev = self.dev
      if dev is None:
        raise RuntimeError(f"USB device for '{self.human_readable_device_name}' is not connected.")
      await anyio.lowlevel.checkpoint()
      try:
        res = await anyio.to_thread.run_sync(
          lambda: dev.ctrl_transfer(
            bmRequestType=bmRequestType,
            bRequest=bRequest,
            wValue=wValue,
            wIndex=wIndex,
            data_or_wLength=data_or_wLength,
            timeout=timeout * 1000,  # timeout in ms
          ),
          limiter=self._write_limiter,
        )
      except usb.core.USBTimeoutError:
        raise
      except (usb.core.USBError, OSError):
        self._connection_lost = True
        raise
      logger.log(
        LOG_LEVEL_IO,
        "%s ctrl_transfer: %s",
        self._unique_id,
        self._serialize_ctrl_transfer_request(
          bmRequestType, bRequest, wValue, wIndex, data_or_wLength
        ),
      )

      capturer.record(
        USBCommand(
          device_id=self._unique_id,
          action="ctrl_transfer_request",
          data=self._serialize_ctrl_transfer_request(
            bmRequestType, bRequest, wValue, wIndex, data_or_wLength
          ),
        )
      )
      capturer.record(
        USBCommand(
          device_id=self._unique_id,
          action="ctrl_transfer_response",
          data=" ".join(map(str, res)),
        )
      )
    await anyio.lowlevel.checkpoint_if_cancelled()
    return bytearray(res)

  def _setup_sync(self, empty_buffer: bool) -> None:
    """Open the device, resolve the endpoints and drain stale packets. Runs off the event loop."""

    logger.info("Finding USB device...")

    devices = self.get_available_devices()
    if len(devices) == 0:
      raise RuntimeError("USB device not found.")
    if len(devices) > 1:
      logger.warning("Multiple devices found. Using the first one.")
    self.dev = devices[0]

    logger.info("Found USB device.")

    # set the active configuration. With no arguments, the first
    # configuration will be the active one
    if self.configuration_callback is not None:
      self.configuration_callback(self.dev)
    else:
      self.dev.set_configuration()

    cfg = self.dev.get_active_configuration()
    intf = cfg[(0, 0)]

    if self.write_endpoint_address is not None:
      self.write_endpoint = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: e.bEndpointAddress == self.write_endpoint_address,
      )
    else:
      self.write_endpoint = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: (
          usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        ),
      )

    if self.read_endpoint_address is not None:
      self.read_endpoint = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: e.bEndpointAddress == self.read_endpoint_address,
      )
    else:
      self.read_endpoint = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: (
          usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        ),
      )

    logger.info(
      "Found endpoints. \nWrite:\n %s \nRead:\n %s",
      self.write_endpoint,
      self.read_endpoint,
    )

    # Empty the read buffer.
    if empty_buffer:
      while self._read_packet_sync(during_setup=True) is not None:
        pass

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, *, empty_buffer: bool = True
  ) -> None:
    """Initialize the USB connection and register disposal."""
    await super()._enter_lifespan(stack)

    if not USE_USB:
      raise RuntimeError(
        "pyusb/libusb is not installed. Install with: pip install pylabrobot[usb]. "
        f"Import error: {_USB_IMPORT_ERROR}. "
        "https://docs.pylabrobot.org/installation.html"
      )

    self._connection_lost = False
    try:
      await anyio.to_thread.run_sync(self._setup_sync, empty_buffer, limiter=self._read_limiter)
    except BaseException:
      with anyio.CancelScope(shield=True):
        await self._close()
      raise
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    """Dispose the device after active reads and writes finish."""

    async with self._connection_lock:
      await self._close_current()

  async def _close_current(self) -> None:
    """Dispose the handle while the connection owner excludes replacement."""

    logger.warning("Closing connection to USB device.")
    async with self._read_lock, self._write_lock:
      dev = self.dev
      try:
        if dev is not None:
          await anyio.to_thread.run_sync(
            lambda: usb.util.dispose_resources(dev), limiter=self._read_limiter
          )
      finally:
        self.dev = None
        self.read_endpoint = None
        self.write_endpoint = None
        self._unread.clear()
        self._cursors.clear()

  async def reconnect(self) -> None:
    """Dispose the old USB handle before acquiring a replacement."""
    if not self.is_active:
      raise RuntimeError("USB reconnect requires an active lifespan")
    async with self._connection_lock:
      if not self.is_active:
        raise RuntimeError("USB reconnect requires an active lifespan")
      self._reconnecting = True
      self._connection_lost = True
      try:
        with anyio.CancelScope(shield=True):
          await self._close_current()
        try:
          self._connection_lost = False
          await anyio.to_thread.run_sync(self._setup_sync, True, limiter=self._read_limiter)
          await anyio.lowlevel.checkpoint_if_cancelled()
        except BaseException:
          self._connection_lost = True
          with anyio.CancelScope(shield=True):
            await self._close_current()
          raise
      finally:
        self._reconnecting = False

  def serialize(self) -> dict:
    """Serialize the backend to a dictionary."""

    d = {
      **super().serialize(),
      "human_readable_device_name": self.human_readable_device_name,
      "id_vendor": self._id_vendor,
      "id_product": self._id_product,
      "device_address": self._device_address,
      "serial_number": self._serial_number,
      "packet_read_timeout": self.packet_read_timeout,
      "read_timeout": self.read_timeout,
      "write_timeout": self.write_timeout,
    }
    if self.read_endpoint_address is not None:
      d["read_endpoint_address"] = self.read_endpoint_address
    if self.write_endpoint_address is not None:
      d["write_endpoint_address"] = self.write_endpoint_address
    return d


class USBValidator(USB):
  """Validate USB I/O and reconnect without opening a physical device."""

  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    id_vendor: int,
    id_product: int,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: Union[int, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    write_timeout: int = 30,
    read_endpoint_address: Optional[int] = None,
    write_endpoint_address: Optional[int] = None,
    configuration_callback: Optional[Callable[["usb.core.Device"], None]] = None,
    max_workers: int = 1,
    legacy_boundary_profiles: dict[int, USBLegacyBoundaryProfile] | None = None,
  ):
    super().__init__(
      human_readable_device_name=human_readable_device_name,
      id_vendor=id_vendor,
      id_product=id_product,
      device_address=device_address,
      serial_number=serial_number,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      read_endpoint_address=read_endpoint_address,
      write_endpoint_address=write_endpoint_address,
      configuration_callback=configuration_callback,
      max_workers=max_workers,
    )
    self.cr = cr
    self.legacy_boundary_profiles = legacy_boundary_profiles or {}
    self.legacy_inferred_marks: dict[int, list[dict[str, object]]] = {}
    self._observed_endpoints: set[int] = set()

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, *, empty_buffer: bool = True
  ) -> None:
    """Enter a validator lifespan without opening a USB device."""
    await HasLifespan._enter_lifespan(self, stack)
    self._unread.clear()
    self._cursors.clear()
    self.legacy_inferred_marks.clear()
    self._observed_endpoints.clear()

  async def write(
    self,
    data: bytes,
    timeout: Optional[float] = None,
    *,
    on_dispatch: Callable[[], None] | None = None,
  ):
    await anyio.lowlevel.checkpoint()
    next_command = USBCommand(**self.cr.next_command())
    if not (
      next_command.module == "usb"
      and next_command.device_id == self._unique_id
      and next_command.action == "write"
    ):
      raise ValidationError("next command is not write")
    decoded = data.decode("unicode_escape", errors="backslashreplace")
    if not next_command.data == decoded:
      align_sequences(expected=next_command.data, actual=decoded)
      raise ValidationError("Data mismatch: difference was written to stdout.")
    if on_dispatch is not None:
      on_dispatch()

  def _append_legacy_read(self, endpoint: int, command: USBCommand) -> None:
    """Split a captured byte-only read according to the endpoint's explicit profile."""
    profile = self.legacy_boundary_profiles.get(endpoint)
    if profile is None or profile.endpoint != endpoint:
      raise ValidationError("Legacy USB boundary replay requires an endpoint profile")
    if command.endpoint is not None and command.endpoint != endpoint:
      raise ValidationError("Captured USB endpoint mismatch")
    payload = (
      bytes.fromhex(command.data_hex) if command.data_hex is not None else command.data.encode()
    )
    if not payload:
      raise ValidationError("Empty legacy USB read has no inferable packet")
    cursor = self._cursors.setdefault(endpoint, BoundaryCursor(frozenset({"packet"})))
    if endpoint not in self.legacy_inferred_marks and endpoint not in self._observed_endpoints:
      cursor.append(b"", frozenset({"packet"}))
    inferred = self.legacy_inferred_marks.setdefault(endpoint, [])
    for start in range(0, len(payload), profile.packet_size):
      part = payload[start : start + profile.packet_size]
      is_final = start + len(part) == len(payload)
      short_end = is_final and (
        (len(part) < profile.packet_size and profile.assume_uncapped_read)
        or (len(part) == profile.packet_size and profile.assume_zlp_after_full_read)
      )
      cursor.append(part, frozenset({"packet"}))
      inferred.append(
        {
          "offset": start + len(part),
          "kind": "packet",
          "provenance": "legacy_inferred",
          "assumptions": {"packet_size": profile.packet_size},
        }
      )
      if short_end:
        assumed_zlp = len(part) == profile.packet_size
        cursor.append(
          b"",
          frozenset({"packet", "short_packet_end"})
          if assumed_zlp
          else frozenset({"short_packet_end"}),
        )
        if assumed_zlp:
          inferred.append(
            {
              "offset": start + len(part),
              "kind": "packet",
              "provenance": "legacy_inferred",
              "assumptions": {"assume_zlp_after_full_read": True},
            }
          )
        inferred.append(
          {
            "offset": start + len(part),
            "kind": "short_packet_end",
            "provenance": "legacy_inferred",
            "assumptions": {
              "packet_size": profile.packet_size,
              "assume_uncapped_read": profile.assume_uncapped_read,
              "assume_zlp_after_full_read": profile.assume_zlp_after_full_read,
            },
          }
        )

  async def read(
    self,
    timeout: Union[int, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    size: Optional[int] = None,
    endpoint: Optional[int] = None,
  ) -> bytes:
    if size is not None and size <= 0:
      raise ValueError("size must be positive")
    if not isinstance(timeout, _UnsetTimeout):
      warnings.warn(
        "USB.read(timeout=...) is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
    endpoint_id = endpoint if endpoint is not None else self.read_endpoint_address or -1
    if endpoint_id in self.legacy_boundary_profiles and endpoint_id not in self._observed_endpoints:
      await anyio.lowlevel.checkpoint()
      cursor = self._cursors.setdefault(endpoint_id, BoundaryCursor(frozenset({"packet"})))
      while True:
        data = cursor.read_bytes(
          size if size is not None else self.legacy_boundary_profiles[endpoint_id].packet_size
        )
        if data:
          return data
        command = USBCommand(**self.cr.next_command())
        if (
          command.module != "usb"
          or command.device_id != self._unique_id
          or command.action != "read"
        ):
          raise ValidationError("Next command is not a USB read")
        if command.data_hex is not None:
          self._observed_endpoints.add(endpoint_id)
          self._cursors.pop(endpoint_id, None)
          payload = bytes.fromhex(command.data_hex)
          if not payload or size is not None and len(payload) > size:
            raise ValidationError("Captured USB read exceeds requested size")
          return payload
        self._append_legacy_read(endpoint_id, command)
    unread = self._unread.setdefault(endpoint_id, bytearray())
    await anyio.lowlevel.checkpoint()
    if not unread:
      next_command = USBCommand(**self.cr.next_command())
      if not (
        next_command.module == "usb"
        and next_command.device_id == self._unique_id
        and next_command.action == "read"
      ):
        raise ValidationError("next command is not read")
      unread.extend(
        bytes.fromhex(next_command.data_hex)
        if next_command.data_hex is not None
        else next_command.data.encode()
      )
      if not unread:
        raise anyio.EndOfStream
    # Legacy captures may encode several packets in one read command. Serve its
    # remainder on later reads rather than consuming another captured command.
    maximum = size if size is not None else len(unread)
    data = bytes(unread[:maximum])
    del unread[:maximum]
    return data

  async def _replay_boundary(
    self,
    max_bytes: int | None,
    *,
    boundary: Literal["packet", "short_packet_end"] | None,
    endpoint: int | None,
    consume_immediate_boundary: bool,
    discard: bool,
  ) -> BoundaryRead | BoundaryDiscard:
    """Replay observed operations or split a legacy reply using explicit assumptions."""
    if max_bytes is not None and max_bytes <= 0:
      raise ValueError("max_bytes must be positive")
    await anyio.lowlevel.checkpoint()
    selected = endpoint if endpoint is not None else self.read_endpoint_address or -1
    kind = boundary if boundary is not None else self.default_boundary
    cursor = self._cursors.setdefault(
      selected,
      BoundaryCursor(frozenset()),
    )
    while True:
      result = cursor.take_to_boundary(
        max_bytes,
        boundary=kind,
        consume_immediate_boundary=consume_immediate_boundary,
        discard=discard,
      )
      if result is not None:
        return result
      command = USBCommand(**self.cr.next_command())
      if command.module != "usb" or command.device_id != self._unique_id:
        raise ValidationError("USB boundary replay device mismatch")
      if command.action != "read":
        if command.action != ("discard_to_boundary" if discard else "read_to_boundary"):
          raise ValidationError("Next command is not a USB boundary operation")
        if command.endpoint != selected or command.boundary != kind:
          raise ValidationError("Captured USB endpoint/boundary mismatch")
        if (
          command.consume_immediate_boundary is not None
          and command.consume_immediate_boundary != consume_immediate_boundary
        ):
          raise ValidationError("Captured USB immediate-boundary policy mismatch")
        if command.max_bytes is not None and command.max_bytes != max_bytes:
          raise ValidationError("Captured USB boundary limit mismatch")
        self._observed_endpoints.add(selected)
        if discard:
          return BoundaryDiscard(
            command.discarded_bytes,
            bool(command.started_at_boundary),
            bool(command.ended_at_boundary),
            bool(command.limit_reached),
          )
        return BoundaryRead(
          bytes.fromhex(command.data_hex)
          if command.data_hex is not None
          else command.data.encode(),
          bool(command.started_at_boundary),
          bool(command.ended_at_boundary),
          bool(command.limit_reached),
        )
      if command.data_hex is not None:
        raise ValidationError("Observed USB read must be replayed as a byte read")
      self._append_legacy_read(selected, command)

  async def read_to_boundary(
    self,
    max_bytes: int | None = None,
    *,
    consume_immediate_boundary: bool = True,
    boundary: Literal["packet", "short_packet_end"] | None = None,
    endpoint: int | None = None,
  ) -> BoundaryRead:
    """Replay a captured or assumption-labelled USB boundary read."""
    result = await self._replay_boundary(
      max_bytes,
      boundary=boundary,
      endpoint=endpoint,
      consume_immediate_boundary=consume_immediate_boundary,
      discard=False,
    )
    assert isinstance(result, BoundaryRead)
    return result

  async def discard_to_boundary(
    self,
    max_bytes: int | None = None,
    *,
    consume_immediate_boundary: bool = False,
    boundary: Literal["packet", "short_packet_end"] | None = None,
    endpoint: int | None = None,
  ) -> BoundaryDiscard:
    """Replay a captured or assumption-labelled USB boundary discard."""
    result = await self._replay_boundary(
      max_bytes,
      boundary=boundary,
      endpoint=endpoint,
      consume_immediate_boundary=consume_immediate_boundary,
      discard=True,
    )
    assert isinstance(result, BoundaryDiscard)
    return result

  async def drain(
    self,
    endpoint: Optional[int] = None,
    timeout: float = 0.05,
    size: Optional[int] = None,
    max_duration: float = 1,
  ) -> None:
    """Validation discards uncaptured packets without touching a device."""
    selected_id = endpoint if endpoint is not None else self.read_endpoint_address or -1
    self._unread.pop(selected_id, None)
    self._cursors.pop(selected_id, None)
    self.legacy_inferred_marks.pop(selected_id, None)
    self._observed_endpoints.discard(selected_id)

  async def ctrl_transfer(
    self,
    bmRequestType: int,
    bRequest: int,
    wValue: int,
    wIndex: int,
    data_or_wLength: int,
    timeout: Optional[int] = None,
  ) -> bytearray:
    next_command = USBCommand(**self.cr.next_command())
    if not (
      next_command.module == "usb"
      and next_command.device_id == self._unique_id
      and next_command.action == "ctrl_transfer_request"
    ):
      raise ValidationError("next command is not ctrl_transfer_request")

    if not next_command.data == self._serialize_ctrl_transfer_request(
      bmRequestType, bRequest, wValue, wIndex, data_or_wLength
    ):
      align_sequences(
        expected=next_command.data,
        actual=self._serialize_ctrl_transfer_request(
          bmRequestType, bRequest, wValue, wIndex, data_or_wLength
        ),
      )
      raise ValidationError("Data mismatch: difference was written to stdout.")

    next_command = USBCommand(**self.cr.next_command())
    if not (
      next_command.module == "usb"
      and next_command.device_id == self._unique_id
      and next_command.action == "ctrl_transfer_response"
    ):
      raise ValidationError("next command is not ctrl_transfer_response")
    return bytearray(map(int, next_command.data.split(" ")))

  async def reconnect(self) -> None:
    """Accept connection replacement without touching the network or USB."""
    if not self.is_active:
      raise RuntimeError("USB reconnect requires an active lifespan")
    self._unread.clear()
    self._cursors.clear()
    self.legacy_inferred_marks.clear()
    self._observed_endpoints.clear()
