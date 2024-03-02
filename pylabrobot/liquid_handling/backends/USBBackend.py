# pylint: disable=invalid-name

from abc import ABCMeta, abstractmethod
import logging
import time
from typing import List, Optional, TYPE_CHECKING

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend

try:
  import usb.core
  import usb.util
  USE_USB = True
except ImportError:
  USE_USB = False


if TYPE_CHECKING:
  import usb.core


logger = logging.getLogger("pylabrobot")


class USBBackend(LiquidHandlerBackend, metaclass=ABCMeta):
  """ An abstract class for liquid handler backends that talk over a USB cable. Provides read/write
  functionality, including timeout handling. """

  @abstractmethod
  def __init__(
    self,
    id_vendor: int,
    id_product: int,
    address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30
  ):
    """ Initialize a USBBackend.

    Args:
      id_vendor: The USB vendor ID of the machine.
      id_product: The USB product ID of the machine.
      address: The USB address of the machine. If `None`, use the first device found. This is useful
        for machines that have no unique serial number, such as the Hamilton STAR.
      serial_number: The serial number of the machine. If `None`, use the first device found.
      packet_read_timeout: The timeout for reading packets from the machine in seconds.
      read_timeout: The timeout for reading from the machine in seconds.
      write_timeout: The timeout for writing to the machine in seconds.
    """

    super().__init__()

    assert packet_read_timeout < read_timeout, \
      "packet_read_timeout must be smaller than read_timeout."

    self.id_vendor = id_vendor
    self.id_product = id_product
    self.address = address
    self.serial_number = serial_number

    self.packet_read_timeout = packet_read_timeout
    self.read_timeout = read_timeout
    self.write_timeout = write_timeout

    self.dev: Optional["usb.core.Device"] = None # TODO: make this a property
    self.read_endpoint: Optional[usb.core.Endpoint] = None
    self.write_endpoint: Optional[usb.core.Endpoint] = None

  def write(self, data: str, timeout: Optional[int] = None):
    """ Write data to the device.

    Args:
      data: The data to write.
      timeout: The timeout for writing to the device in seconds. If `None`, use the default timeout
        (specified by the `write_timeout` attribute).
    """

    assert self.dev is not None and self.read_endpoint is not None, "Device not connected."

    if timeout is None:
      timeout = self.write_timeout

    # write command to endpoint
    self.dev.write(self.write_endpoint, data, timeout=timeout)
    logger.info("Sent command: %s", data)

  def _read_packet(self) -> Optional[bytearray]:
    """ Read a packet from the machine.

    Returns:
      A string containing the decoded packet, or None if no packet was received.
    """

    assert self.dev is not None and self.read_endpoint is not None, "Device not connected."

    try:
      res = self.dev.read(
        self.read_endpoint,
        self.read_endpoint.wMaxPacketSize,
        timeout=int(self.packet_read_timeout * 1000) # timeout in ms
      )

      if res is not None:
        return bytearray(res) # convert res into text
      return None
    except usb.core.USBError:
      # No data available (yet), this will give a timeout error. Don't reraise.
      return None

  def read(self, timeout: Optional[int] = None) -> bytearray:
    """ Read a response from the device.

    Args:
      timeout: The timeout for reading from the device in seconds. If `None`, use the default
        timeout (specified by the `read_timeout` attribute).
    """

    assert self.read_endpoint is not None, "Device not connected."

    if timeout is None:
      timeout = self.read_timeout

    # Attempt to read packets until timeout, or when we identify the right id.
    timeout_time = time.time() + timeout

    while time.time() < timeout_time:
      # read response from endpoint, and keep reading until the packet is smaller than the max
      # packet size: if the packet is that size, it means that there may be more data to read.
      resp = bytearray()
      last_packet: Optional[bytearray] = None
      while True: # read while we have data, and while the last packet is the max size.
        last_packet = self._read_packet()
        if last_packet is not None:
          resp += last_packet
        if last_packet is None or len(last_packet) != self.read_endpoint.wMaxPacketSize:
          break

      if resp == "":
        continue

      logger.debug("Received data: %s", resp)
      return resp

    raise TimeoutError("Timeout while reading.")

  def get_available_devices(self) -> List["usb.core.Device"]:
    """ Get a list of available devices that match the specified vendor and product IDs, and serial
    number and address if specified. """

    found_devices = usb.core.find(idVendor=self.id_vendor, idProduct=self.id_product, find_all=True)
    devices: List["usb.core.Device"] = []
    for dev in found_devices:
      if self.address is not None:
        if dev.address is None:
          raise RuntimeError("A device address was specified, but the backend used for PyUSB does "
          "not support device addresses.")

        if dev.address != self.address:
          continue

      if self.serial_number is not None:
        if dev.serial_number is None:
          raise RuntimeError("A serial number was specified, but the device does not have a serial "
            "number.")

        if dev.serial_number != self.serial_number:
          continue

      devices.append(dev)

    return devices

  def list_available_devices(self) -> None:
    """ Utility to list all devices that match the specified vendor and product IDs, and serial
    number and address if specified. You can use this to discover the serial number and address of
    your device, if using multiple. Note that devices may not have a unique serial number. """

    for dev in self.get_available_devices():
      print(dev)

  async def setup(self):
    """ Initialize the USB connection to the machine."""

    if not USE_USB:
      raise RuntimeError("USB is not enabled. Please install pyusb.")

    if self.dev is not None:
      logging.warning("Already initialized. Please call stop() first.")
      return

    logger.info("Finding USB device...")

    devices = self.get_available_devices()
    if len(devices) == 0:
      raise RuntimeError("USB device not found.")
    if len(devices) > 1:
      logging.warning("Multiple devices found. Using the first one.")
    self.dev = devices[0]

    logger.info("Found USB device.")

    # set the active configuration. With no arguments, the first
    # configuration will be the active one
    self.dev.set_configuration()

    cfg = self.dev.get_active_configuration()
    intf = cfg[(0,0)]

    self.write_endpoint = usb.util.find_descriptor(
      intf,
      custom_match = \
      lambda e: \
          usb.util.endpoint_direction(e.bEndpointAddress) == \
          usb.util.ENDPOINT_OUT)

    self.read_endpoint = usb.util.find_descriptor(
      intf,
      custom_match = \
      lambda e: \
          usb.util.endpoint_direction(e.bEndpointAddress) == \
          usb.util.ENDPOINT_IN)

    logger.info("Found endpoints. \nWrite:\n %s \nRead:\n %s", self.write_endpoint,
      self.read_endpoint)

    # Empty the read buffer.
    while self._read_packet() is not None:
      pass

  async def stop(self):
    """ Close the USB connection to the machine. """

    if self.dev is None:
      raise ValueError("USB device was not connected.")
    logging.warning("Closing connection to USB device.")
    usb.util.dispose_resources(self.dev)
    self.dev = None
