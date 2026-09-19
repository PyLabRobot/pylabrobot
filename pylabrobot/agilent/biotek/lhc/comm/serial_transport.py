"""The link over an operating system serial port."""

from __future__ import annotations

import logging

from pylabrobot.io.serial import Serial

from .transport import (
  BAUDRATE,
  DATA_BITS,
  DEFAULT_READ_TIMEOUT,
  DEFAULT_WRITE_TIMEOUT,
  PARITY_NONE,
  STOP_BITS,
  Transport,
)

logger = logging.getLogger(__name__)


class SerialTransport(Transport):
  """A link over a serial port, named the way the operating system names it.

  Args:
    port: The serial port to open, such as ``/dev/ttyUSB0``, ``/dev/ttyS4`` or ``COM3``.
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
    io: Serial | None = None,
  ) -> None:
    super().__init__(port=port, timeout=timeout)
    self.io = io or Serial(
      human_readable_device_name=name,
      port=port,
      baudrate=BAUDRATE,
      bytesize=DATA_BITS,
      parity=PARITY_NONE,
      stopbits=STOP_BITS,
      timeout=timeout,
      write_timeout=DEFAULT_WRITE_TIMEOUT,
      rtscts=False,
      dsrdtr=False,
      xonxoff=False,
    )

  def _apply_read_timeout(self, timeout: float) -> None:
    """Set the port's own read timeout, which is what makes a blocking read return.

    Args:
      timeout: Read timeout in seconds.
    """
    self.io.set_read_timeout(timeout)

  async def setup(self) -> None:
    """Open the port. The link parameters were fixed when the port was constructed."""
    await self.io.setup()
    logger.info("[%s] serial link open at %d baud, 8N2", self.port, BAUDRATE)

  async def stop(self) -> None:
    """Close the port."""
    await self.io.stop()
    logger.info("[%s] serial link closed", self.port)

  async def write(self, data: bytes) -> None:
    """Write every byte of ``data``.

    Args:
      data: The bytes to write.
    """
    await self.io.write(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    """Read up to ``num_bytes`` bytes, blocking until the port's read timeout passes.

    Args:
      num_bytes: The most bytes to return.

    Returns:
      What had arrived, which may be shorter than requested.
    """
    return await self.io.read(num_bytes)

  async def purge(self) -> None:
    """Discard whatever is buffered in either direction."""
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()
