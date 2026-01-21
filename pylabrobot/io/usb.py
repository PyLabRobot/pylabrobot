import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

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
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """Initialize an io.USB object.

    Args:
      id_vendor: The USB vendor ID of the machine.
      id_product: The USB product ID of the machine.
      device_address: The USB device_address of the machine. If `None`, use the first device found.
        This is useful for machines that have no unique serial number, such as the Hamilton STAR.
      serial_number: The serial number of the machine. If `None`, use the first device found.
      packet_read_timeout: The timeout for reading packets from the machine in seconds.
      read_timeout: The timeout for reading from the machine in seconds.
      write_timeout: The timeout for writing to the machine in seconds.
    """

    super().__init__()

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new USB object while capture or validation is active")

    assert (
      packet_read_timeout < read_timeout
    ), "packet_read_timeout must be smaller than read_timeout."

    self._id_vendor = id_vendor
    self._id_product = id_product
    self._device_address = device_address
    self._serial_number = serial_number

    self.packet_read_timeout = packet_read_timeout
    self.read_timeout = read_timeout
    self.write_timeout = write_timeout

    self.dev: Optional["usb.core.Device"] = None  # TODO: make this a property
    self.read_endpoint: Optional[usb.core.Endpoint] = None
    self.write_endpoint: Optional[usb.core.Endpoint] = None

    self._executor: Optional[ThreadPoolExecutor] = None

    # unique id in the logs
    self._unique_id = f"[{hex(self._id_vendor)}:{hex(self._id_product)}][{self._serial_number or ''}][{self._device_address or ''}]"

  async def write(self, data: bytes, timeout: Optional[float] = None):
    """Write data to the device.

    Args:
      data: The data to write.
      timeout: The timeout for writing to the device in seconds. If `None`, use the default timeout
        (specified by the `write_timeout` attribute).
    """

    assert self.dev is not None and self.read_endpoint is not None, "Device not connected."

    if timeout is None:
      timeout = self.write_timeout

    # write command to endpoint
    loop = asyncio.get_running_loop()
    write_endpoint = self.write_endpoint
    dev = self.dev
    if self._executor is None or dev is None or write_endpoint is None:
      raise RuntimeError("Call setup() first.")
    await loop.run_in_executor(
      self._executor,
      lambda: dev.write(write_endpoint, data, timeout=timeout),
    )
    if len(data) % write_endpoint.wMaxPacketSize == 0:
      # send a zero-length packet to indicate the end of the transfer
      await loop.run_in_executor(
        self._executor,
        lambda: dev.write(write_endpoint, b"", timeout=timeout),
      )
    logger.log(LOG_LEVEL_IO, "%s write: %s", self._unique_id, data)
    capturer.record(
      USBCommand(
        device_id=self._unique_id,
        action="write",
        data=data.decode("unicode_escape", errors="backslashreplace"),
      )
    )

  def _read_packet(self, size: Optional[int] = None) -> Optional[bytearray]:
    """Read a packet from the machine.

    Args:
      size: The maximum number of bytes to read. If `None`, read up to wMaxPacketSize bytes.

    Returns:
      A bytearray containing the data read, or None if no data was received.
    """

    assert self.dev is not None and self.read_endpoint is not None, "Device not connected."

    read_size = size if size is not None else self.read_endpoint.wMaxPacketSize

    try:
      res = self.dev.read(
        self.read_endpoint,
        read_size,
        timeout=int(self.packet_read_timeout * 1000),  # timeout in ms
      )

      if res is not None:
        return bytearray(res)
      return None
    except usb.core.USBError:
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

    assert self.read_endpoint is not None, "Device not connected."

    if timeout is None:
      timeout = self.read_timeout

    def read_or_timeout():
      # Attempt to read packets until timeout, or when we identify the right id.
      timeout_time = time.time() + timeout

      while time.time() < timeout_time:
        # read response from endpoint, and keep reading until the packet is smaller than the max
        # packet size: if the packet is that size, it means that there may be more data to read.
        resp = bytearray()
        last_packet: Optional[bytearray] = None
        while True:  # read while we have data, and while the last packet is the max size.
          remaining = size - len(resp) if size is not None else None
          last_packet = self._read_packet(size=remaining)
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

      raise TimeoutError("Timeout while reading.")

    loop = asyncio.get_running_loop()
    if self._executor is None or self.dev is None:
      raise RuntimeError("Call setup() first.")
    return await loop.run_in_executor(self._executor, read_or_timeout)

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
            "A device address was specified, but the backend used for PyUSB does "
            "not support device addresses."
          )

        if dev.address != self._device_address:
          continue

      if self._serial_number is not None:
        if dev._serial_number is None:
          raise RuntimeError(
            "A serial number was specified, but the device does not have a serial " "number."
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
    assert self.dev is not None, "Device not connected."

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

  async def setup(self):
    """Initialize the USB connection to the machine."""

    if self.dev is not None:
      # previous setup did not properly finish,
      # or we are re-initializing the device.
      logger.warning("USB device already connected. Closing previous connection.")
      await self.stop()

    if not USE_USB:
      raise RuntimeError(
        f"USB dependencies could not be imported due to the following error: {_USB_IMPORT_ERROR}. "
        "Please install pyusb and libusb. "
        "https://docs.pylabrobot.org/installation.html"
      )

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
    self.dev.set_configuration()

    cfg = self.dev.get_active_configuration()
    intf = cfg[(0, 0)]

    self.write_endpoint = usb.util.find_descriptor(
      intf,
      custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
      == usb.util.ENDPOINT_OUT,
    )

    self.read_endpoint = usb.util.find_descriptor(
      intf,
      custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
      == usb.util.ENDPOINT_IN,
    )

    logger.info(
      "Found endpoints. \nWrite:\n %s \nRead:\n %s",
      self.write_endpoint,
      self.read_endpoint,
    )

    # Empty the read buffer.
    while self._read_packet() is not None:
      pass

    self._executor = ThreadPoolExecutor(max_workers=1)

  async def stop(self):
    """Close the USB connection to the machine."""

    if self.dev is None:
      raise ValueError("USB device was not connected.")
    logger.warning("Closing connection to USB device.")
    usb.util.dispose_resources(self.dev)
    self.dev = None

    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  def serialize(self) -> dict:
    """Serialize the backend to a dictionary."""

    return {
      **super().serialize(),
      "id_vendor": self._id_vendor,
      "id_product": self._id_product,
      "device_address": self._device_address,
      "serial_number": self._serial_number,
      "packet_read_timeout": self.packet_read_timeout,
      "read_timeout": self.read_timeout,
      "write_timeout": self.write_timeout,
    }


class USBValidator(USB):
  def __init__(
    self,
    cr: "CaptureReader",
    id_vendor: int,
    id_product: int,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    super().__init__(
      id_vendor=id_vendor,
      id_product=id_product,
      device_address=device_address,
      serial_number=serial_number,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )
    self.cr = cr

  async def setup(self):
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
