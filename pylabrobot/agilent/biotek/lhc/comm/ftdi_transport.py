"""The link over a USB bridge, driven directly rather than through a virtual serial port.

The instrument behind the bridge speaks the same asynchronous serial link either way; only the
host-side plumbing differs. A port string names this transport by carrying a device serial number,
in one of two forms::

    USB 405 TS/LS sn:13010914
    ftdi:13010914

The serial number is all that is needed to open the device.
"""

from __future__ import annotations

import logging

from pylabrobot.agilent.biotek.lhc.error_handling.errors import (
  WRITE_FAILED,
  for_info,
  info_for,
)
from pylabrobot.io.ftdi import FTDI

from .transport import BAUDRATE, DATA_BITS, DEFAULT_READ_TIMEOUT, STOP_BITS, Transport

logger = logging.getLogger(__name__)

_USB_PREFIX = "USB "
_SERIAL_MARKER = " sn:"
_FTDI_PREFIX = "ftdi:"

_PARITY_NONE = 0
_FLOW_CONTROL_NONE = 0x0


def is_ftdi_port(port: str) -> bool:
  """Does this port string name a USB bridge rather than an operating system serial port?

  The test is the port string's format, never a platform-specific prefix: a serial port is
  ``/dev/ttyUSB0`` as readily as ``COM3``, and neither says anything about how the host is cabled.

  Args:
    port: The port string to inspect.

  Returns:
    True if the string carries a device serial number.
  """
  return port.startswith(_FTDI_PREFIX) or (port.startswith(_USB_PREFIX) and _SERIAL_MARKER in port)


def serial_number(port: str) -> str:
  """The device serial number carried by an FTDI port string.

  Args:
    port: A port string accepted by :func:`is_ftdi_port`.

  Returns:
    The serial number, with surrounding whitespace removed.

  Raises:
    ValueError: If the string carries no serial number.
  """
  if port.startswith(_FTDI_PREFIX):
    return port[len(_FTDI_PREFIX) :].strip()
  if _SERIAL_MARKER in port:
    return port.split(_SERIAL_MARKER, 1)[1].strip()
  raise ValueError(f"{port!r} carries no device serial number")


class FtdiTransport(Transport):
  """A link over a USB bridge, opened by device serial number.

  The bridge has no read timeout of its own -- a read returns whatever has already arrived -- so
  :meth:`Transport.read_exactly` is what turns it into a timed read.

  Args:
    port: A port string carrying a device serial number.
    name: Human-readable instrument name, used in logs.
    timeout: Read timeout in seconds.
    io: An already-built transport to use instead of opening ``port``. Intended for tests that
      replay a captured session.
  """

  def __init__(
    self,
    port: str,
    name: str = "BioTek instrument",
    timeout: float = DEFAULT_READ_TIMEOUT,
    io: FTDI | None = None,
  ) -> None:
    super().__init__(port=port, timeout=timeout)
    self.io = io or FTDI(human_readable_device_name=name, device_id=serial_number(port))

  async def setup(self) -> None:
    """Open the device and configure it for 38400 8N2 with no flow control."""
    await self.io.setup()
    await self.io.set_baudrate(BAUDRATE)
    await self.io.set_line_property(DATA_BITS, STOP_BITS, _PARITY_NONE)
    await self.io.set_flowctrl(_FLOW_CONTROL_NONE)
    await self.io.set_rts(True)
    await self.io.set_dtr(True)
    logger.info("[%s] usb link open at %d baud, 8N2", self.port, BAUDRATE)

  async def stop(self) -> None:
    """Close the device."""
    await self.io.stop()
    logger.info("[%s] usb link closed", self.port)

  async def write(self, data: bytes) -> None:
    """Write every byte of ``data``.

    Args:
      data: The bytes to write.

    Raises:
      LinkError: If the bridge accepted fewer bytes than it was given.
    """
    written = await self.io.write(data)
    if written is not None and written != len(data):
      raise for_info(
        info_for(
          WRITE_FAILED,
          operation=f"write to {self.port}: {written} of {len(data)} bytes accepted",
        )
      )

  async def read(self, num_bytes: int = 1) -> bytes:
    """Read whatever has already arrived, up to ``num_bytes`` bytes.

    Args:
      num_bytes: The most bytes to return.

    Returns:
      What had arrived, which is often shorter than requested and may be empty.
    """
    return await self.io.read(num_bytes)

  async def purge(self) -> None:
    """Discard whatever is buffered in either direction."""
    await self.io.usb_purge_rx_buffer()
    await self.io.usb_purge_tx_buffer()
