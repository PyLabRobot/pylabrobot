"""Choosing a transport for a port string.

This is the one place in the package where the two transports are told apart. Everything above it
holds a :class:`~.transport.Transport` and never learns which kind it got.
"""

from __future__ import annotations

import logging

from .ftdi_transport import FtdiTransport, is_ftdi_port
from .serial_transport import SerialTransport
from .transport import DEFAULT_READ_TIMEOUT, Transport

logger = logging.getLogger(__name__)


def transport_for(
  port: str,
  name: str = "BioTek instrument",
  timeout: float = DEFAULT_READ_TIMEOUT,
) -> Transport:
  """Build the transport a port string names, without opening it.

  A string carrying a device serial number (``USB <name> sn:<serial>`` or ``ftdi:<serial>``) names
  a USB bridge; anything else is taken to be an operating system serial port and is passed through
  unexamined, so ``/dev/ttyUSB0``, ``/dev/ttyS4`` and ``COM3`` are all equally valid.

  Args:
    port: The port the instrument is on.
    name: Human-readable instrument name, used in logs.
    timeout: Read timeout in seconds.

  Returns:
    An unopened transport.

  Raises:
    ValueError: If ``port`` is empty.
  """
  if not port:
    raise ValueError("no port given")
  if is_ftdi_port(port):
    return FtdiTransport(port=port, name=name, timeout=timeout)
  return SerialTransport(port=port, name=name, timeout=timeout)


async def open_transport(
  port: str,
  name: str = "BioTek instrument",
  timeout: float = DEFAULT_READ_TIMEOUT,
) -> Transport:
  """Build the transport a port string names and open it.

  Args:
    port: The port the instrument is on.
    name: Human-readable instrument name, used in logs.
    timeout: Read timeout in seconds.

  Returns:
    An open transport, configured for 38400 8N2 with no flow control.

  Raises:
    ValueError: If ``port`` is empty.
  """
  transport = transport_for(port=port, name=name, timeout=timeout)
  await transport.setup()
  return transport
