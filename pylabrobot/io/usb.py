import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List, Optional

import anyio

from pylabrobot.io.capture import Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

try:
  import libusb_package
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


@dataclass
class USBCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str, module: str = "usb"):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data


class USB(IOBase):
  """IO for reading/writing to a USB device."""

  def __init__(
    self,
    id_vendor: int,
    id_product: int,
    human_readable_device_name: str,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
    configuration_callback: Optional[Callable[["usb.core.Device"], None]] = None,
    max_workers: int = 1,
    read_endpoint_address: Optional[int] = None,
    write_endpoint_address: Optional[int] = None,
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

    assert packet_read_timeout < read_timeout, (
      "packet_read_timeout must be smaller than read_timeout."
    )

    self._id_vendor = id_vendor
    self._id_product = id_product
    self._device_address = device_address
    self._serial_number = serial_number

    self.packet_read_timeout = packet_read_timeout
    self.read_timeout = read_timeout
    self.write_timeout = write_timeout
    self.read_endpoint_address = read_endpoint_address
    self.write_endpoint_address = write_endpoint_address
    self.configuration_callback = configuration_callback
    self.max_workers = max_workers

    self.dev: Optional[usb.core.Device] = None  # TODO: make this a property
    self.read_endpoint: Optional[usb.core.Endpoint] = None
    self.write_endpoint: Optional[usb.core.Endpoint] = None

    # unique id in the logs
    self._unique_id = f"[{hex(self._id_vendor)}:{hex(self._id_product)}][{self._serial_number or ''}][{self._device_address or ''}]"
    self._human_readable_device_name = human_readable_device_name

  async def write(self, data: bytes, timeout: Optional[float] = None):
    """Write data to the device.

    Args:
      data: The data to write.
      timeout: The timeout for writing to the device in seconds. If `None`, use the default timeout
        (specified by the `write_timeout` attribute).
    """

    dev = self.dev
    write_endpoint = self.write_endpoint
    if dev is None or self.read_endpoint is None or write_endpoint is None:
      raise RuntimeError(f"USB device for '{self._human_readable_device_name}' is not connected.")

    if timeout is None:
      timeout = self.write_timeout

    # write command to endpoint
    async def write(d):
      t = anyio.current_effective_deadline() - anyio.current_time()
      assert t < float("inf"), "Timeout must be set"
      timeout_ms = int(t * 1000)
      await anyio.to_thread.run_sync(lambda: dev.write(write_endpoint, d, timeout=timeout_ms))

    with contextlib.ExitStack() as stack:
      if timeout is not None:
        stack.enter_context(anyio.fail_after(timeout))
      await write(data)
      if len(data) % write_endpoint.wMaxPacketSize == 0:
        # send a zero-length packet to indicate the end of the transfer
        await write(b"")
    logger.log(LOG_LEVEL_IO, "%s write: %s", self._unique_id, data)
    capturer.record(
      USBCommand(
        device_id=self._unique_id,
        action="write",
        data=data.decode("unicode_escape", errors="backslashreplace"),
      )
    )

  async def _read_packet(
    self,
    size: Optional[int] = None,
    timeout: Optional[float] = None,
    endpoint: Optional[int] = None,
  ) -> Optional[bytearray]:
    """Read a packet from the machine.

    Args:
      size: The maximum number of bytes to read. If `None`, read up to wMaxPacketSize bytes.
      timeout: The timeout for reading from the device in seconds. If `None`, use the default
        timeout (specified by the `packet_read_timeout` attribute).
      endpoint: The endpoint address to read from. If `None`, use the default read endpoint.

    Returns:
      A bytearray containing the data read, or None if no data was received.
    """
    dev = self.dev
    if dev is None or self.read_endpoint is None:
      raise RuntimeError(f"USB device for '{self._human_readable_device_name}' is not connected.")

    ep = endpoint if endpoint is not None else self.read_endpoint
    if ep is None:
      raise RuntimeError("Read endpoint not found. Call setup() first.")

    # Get max packet size if size is not provided
    if size is None:
      if isinstance(ep, int):
        # Find endpoint object to get max packet size
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        ep_obj = usb.util.find_descriptor(
          intf,
          custom_match=lambda e: e.bEndpointAddress == ep,
        )
        if ep_obj is None:
          raise ValueError(f"Endpoint 0x{ep:02x} not found.")
        read_size = ep_obj.wMaxPacketSize
      else:
        read_size = ep.wMaxPacketSize
    else:
      read_size = size

    if timeout is None:
      timeout = self.packet_read_timeout

    try:
      with anyio.fail_after(timeout):
        res = await anyio.to_thread.run_sync(
          lambda: dev.read(
            ep,
            read_size,
            timeout=int(timeout * 1000),  # timeout in ms
          ),
          abandon_on_cancel=True,
        )

      if res is not None:
        return bytearray(res)
      return None
    except (usb.core.USBError, TimeoutError):
      # No data available (yet), this will give a timeout error. Don't reraise.
      return None

  async def read(self, timeout: Optional[int] = None, size: Optional[int] = None) -> bytes:
    """Read a response from the device.

    Args:
      timeout: The timeout for reading from the device in seconds. If `None`, use the default
        timeout (specified by the `read_timeout` attribute).
      size: The maximum number of bytes to read. If `None`, read all available data until no
        more packets arrive.
    """

    if self.dev is None or self.read_endpoint is None:
      raise RuntimeError(f"USB device for '{self._human_readable_device_name}' is not connected.")

    if timeout is None:
      timeout = self.read_timeout

    try:
      with anyio.fail_after(timeout):
        while True:
          # read response from endpoint, and keep reading until the packet is smaller than the max
          # packet size: if the packet is that size, it means that there may be more data to read.
          resp = bytearray()
          last_packet: Optional[bytearray] = None
          while True:  # read while we have data, and while the last packet is the max size.
            remaining = size - len(resp) if size is not None else None
            last_packet = await self._read_packet(size=remaining)
            if last_packet is not None:
              resp += last_packet
            if self.read_endpoint is None:
              raise RuntimeError("Read endpoint is None. Call setup() first.")
            if last_packet is None or len(last_packet) != self.read_endpoint.wMaxPacketSize:
              break
            if size is not None and len(resp) >= size:
              break

          if len(resp) == 0:
            continue

          logger.log(LOG_LEVEL_IO, "%s read: %s", self._unique_id, resp)
          capturer.record(
            USBCommand(
              device_id=self._unique_id,
              action="read",
              data=resp.decode("unicode_escape", errors="backslashreplace"),
            )
          )
          return resp
    except TimeoutError:
      # Translate TimeoutError to a more specific error message.
      raise TimeoutError(
        f"Timeout while reading from USB device '{self._human_readable_device_name}'."
      ) from None

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
            f"A device address was specified for '{self._human_readable_device_name}', but the backend used for PyUSB does "
            "not support device addresses."
          )

        if dev.address != self._device_address:
          continue

      if self._serial_number is not None:
        if dev._serial_number is None:
          raise RuntimeError(
            f"A serial number was specified for '{self._human_readable_device_name}', but the device does not have a serial number."
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

  def ctrl_transfer(
    self,
    bmRequestType: int,
    bRequest: int,
    wValue: int,
    wIndex: int,
    data_or_wLength: int,
    timeout: Optional[int] = None,
  ) -> bytearray:
    if self.dev is None:
      raise RuntimeError(f"USB device for '{self._human_readable_device_name}' is not connected.")

    if timeout is None:
      timeout = self.read_timeout

    res = self.dev.ctrl_transfer(
      bmRequestType=bmRequestType,
      bRequest=bRequest,
      wValue=wValue,
      wIndex=wIndex,
      data_or_wLength=data_or_wLength,
      timeout=timeout * 1000,  # timeout in ms
    )

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

    return bytearray(res)

  async def _enter_lifespan(self, stack: contextlib.AsyncExitStack, *, empty_buffer=True):
    """Initialize the USB connection to the machine."""
    if not USE_USB:
      raise RuntimeError(
        "pyusb/libusb is not installed. Install with: pip install pylabrobot[usb]. "
        f"Import error: {_USB_IMPORT_ERROR}. "
        "https://docs.pylabrobot.org/installation.html"
      )

    logger.info("Finding USB device...")

    devices = self.get_available_devices()
    if len(devices) == 0:
      raise RuntimeError("USB device not found.")
    if len(devices) > 1:
      logger.warning("Multiple devices found. Using the first one.")
    self.dev = devices[0]

    # at this point, we manage `self.dev`; make sure it gets cleaned up again.
    @stack.callback
    def cleanup():
      logger.warning("Closing connection to USB device.")
      usb.util.dispose_resources(self.dev)
      self.dev = None

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
      while await self._read_packet() is not None:
        pass

  async def recover_transport(self):
    """Try to recover from a broken transport."""
    # TODO: dispose of `.dev` and re-configure
    while await self._read_packet() is not None:
      pass

  def serialize(self) -> dict:
    """Serialize the backend to a dictionary."""

    d = {
      **super().serialize(),
      "human_readable_device_name": self._human_readable_device_name,
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
  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    id_vendor: int,
    id_product: int,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
    read_endpoint_address: Optional[int] = None,
    write_endpoint_address: Optional[int] = None,
    configuration_callback: Optional[Callable[["usb.core.Device"], None]] = None,
    max_workers: int = 1,
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

  async def _enter_lifespan(self, stack: contextlib.AsyncExitStack, *, empty_buffer=True):
    pass

  async def write(self, data: bytes, timeout: Optional[float] = None):
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

  async def read(self, timeout: Optional[float] = None, size: Optional[int] = None) -> bytes:
    next_command = USBCommand(**self.cr.next_command())
    if not (
      next_command.module == "usb"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
    ):
      raise ValidationError("next command is not read")
    data = next_command.data.encode()
    if size is not None:
      data = data[:size]
    return data

  def ctrl_transfer(
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
