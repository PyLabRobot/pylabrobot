"""How the MPE2 is reached: PLR's Hamilton TCP client, over TCP or Hamilton's USB serial link.

The MPE2 answers object queries only to a client connecting as a controller (intent 0x1111).
"""

from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.hamilton.transport.rs232_ppp import HamiltonSerialLink
from pylabrobot.hamilton.transport.tcp import HamiltonTCPClient
from pylabrobot.hamilton.transport.tcp.session import TCPSession

logger = logging.getLogger(__name__)

DEFAULT_TCP_PORT = 2000

# How the MPE2 is asked to connect (ConnectionIntent.Controller in Hamilton's ConnectionProtocol2).
# With PLR's default (1) the MPE2 answers the connection request with empty fragments, registers
# the client, but then leaves its object queries unanswered.
CONTROLLER_INTENT = 0x1111

# The MPE2's USB serial chip: FTDI's FT-X (FT230X/FT231X). A generic chip, so other instruments
# may carry it too: with several, the serial number chooses.
USB_VID, USB_PID = 0x0403, 0x6015


class MPE2Client(HamiltonTCPClient):
  """PLR's Hamilton TCP client, connecting as a controller, over TCP or the USB serial link.

  Args:
    host: the MPE2's address, for TCP.
    port: its TCP port; Hamilton's is 2000.
    serial_port: the USB serial port ("COM4", "/dev/ttyUSB0") to use instead of TCP.
    usb: over USB, the port found at setup by the MPE2's USB ids (when ``serial_port`` is None).
    serial_number: with ``usb``, the USB serial number of the MPE2 wanted, when several are
      plugged in.
  """

  CONNECTION_TYPE = CONTROLLER_INTENT

  def __init__(
    self,
    host: Optional[str] = None,
    port: int = DEFAULT_TCP_PORT,
    serial_port: Optional[str] = None,
    usb: bool = False,
    serial_number: Optional[str] = None,
  ):
    usb = usb or serial_port is not None
    if (host is None) == (not usb):
      raise ValueError("Give the MPE2's host (TCP) or use USB, not both.")
    self.serial_port = serial_port
    self.usb = usb
    self.serial_number = serial_number
    # Over USB the base class's host only names the connection; the serial link replaces its
    # socket (_create_session).
    super().__init__(host=serial_port or host or "usb", port=port)

  def _create_session(self) -> TCPSession:
    """A TCP session over the network, or over the USB serial link."""
    if not self.usb:
      return super()._create_session()
    found = self.serial_port is None
    return TCPSession(
      HamiltonSerialLink(
        self.serial_port,
        vid=USB_VID if found else None,
        pid=USB_PID if found else None,
        serial_number=self.serial_number if found else None,
        human_readable_device_name="Hamilton MPE2",
        read_timeout=self._read_timeout,
      ),
      read_timeout=self._read_timeout,
      error_codes=self._ERROR_CODES,
    )
