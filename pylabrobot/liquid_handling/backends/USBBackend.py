# pylint: disable=invalid-name

from abc import ABCMeta, abstractmethod
import logging
import time
from typing import Optional

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend

try:
  import usb.core
  import usb.util
  USE_USB = True
except ImportError:
  USE_USB = False


logger = logging.getLogger(__name__)


class USBBackend(LiquidHandlerBackend, metaclass=ABCMeta):
  """ An abstract class for liquid handler backends that talk over a USB cable. Provides read/write
  functionality, including timeout handling. """

  @abstractmethod
  def __init__(
    self,
    id_vendor: int,
    id_product: int,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30
  ):
    """ Initialize a USBBackend.

    Args:
      id_vendor: The USB vendor ID of the machine.
      id_product: The USB product ID of the machine.
      packet_read_timeout: The timeout for reading packets from the machine in seconds.
      read_timeout: The timeout for reading from the machine in seconds.
      write_timeout: The timeout for writing to the machine in seconds.
    """

    super().__init__()

    assert packet_read_timeout < read_timeout, \
      "packet_read_timeout must be smaller than read_timeout."

    self.id_vendor = id_vendor
    self.id_product = id_product

    self.packet_read_timeout = packet_read_timeout
    self.read_timeout = read_timeout
    self.write_timeout = write_timeout
    self.id_ = 0

    self.dev: Optional[usb.core.Device] = None # TODO: make this a property
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

  def _read_packet(self) -> Optional[str]:
    """ Read a packet from the Hamilton machine.

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
        return bytearray(res).decode("utf-8") # convert res into text
      return None
    except usb.core.USBError:
      # No data available (yet), this will give a timeout error. Don't reraise.
      return None

  def read(self, timeout: Optional[int] = None) -> str:
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
      resp = ""
      last_packet: Optional[str] = None
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

  async def setup(self):
    """ Initialize the USB connection to the machine."""

    if not USE_USB:
      raise RuntimeError("USB is not enabled. Please install pyusb.")

    if self.dev is not None:
      logging.warning("Already initialized. Please call stop() first.")
      return

    logger.info("Finding Hamilton USB device...")

    self.dev = usb.core.find(idVendor=self.id_vendor, idProduct=self.id_product)
    if self.dev is None:
      raise ValueError("USB device not found.")

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
