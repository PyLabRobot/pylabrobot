import logging
from typing import Literal, Optional, Sequence, Union

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode, Barcode1DSymbology, BarcodePosition

logger = logging.getLogger(__name__)


class SerialBarcodeScanner(HasLifespan):
  """Barcode scanner that returns one barcode per terminated serial line.

  This class is intended for scanners configured as RS-232 or USB virtual COM devices. It reads
  bytes from :class:`pylabrobot.io.Serial` until a configured line terminator is seen.
  """

  def __init__(
    self,
    port: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    baudrate: int = 9600,
    bytesize: int = 8,
    parity: str = "N",
    stopbits: int = 1,
    write_timeout: float = 1,
    read_timeout: float = 1,
    rtscts: bool = False,
    dsrdtr: bool = False,
    xonxoff: bool = False,
    encoding: str = "utf-8",
    terminators: Sequence[bytes] = (b"\r", b"\n"),
    max_line_length: int = 4096,
    trigger_command: Optional[bytes] = None,
    untrigger_command: Optional[bytes] = None,
  ) -> None:
    super().__init__()
    if len(terminators) == 0:
      raise ValueError("At least one line terminator must be configured.")
    if any(len(t) != 1 for t in terminators):
      raise ValueError("SerialBarcodeScanner only supports one-byte terminators.")
    if max_line_length <= 0:
      raise ValueError("max_line_length must be positive.")

    self.io = Serial(
      human_readable_device_name="Serial Barcode Scanner",
      port=port,
      vid=vid,
      pid=pid,
      baudrate=baudrate,
      bytesize=bytesize,
      parity=parity,
      stopbits=stopbits,
      write_timeout=write_timeout,
      rtscts=rtscts,
      dsrdtr=dsrdtr,
      xonxoff=xonxoff,
    )
    self.encoding = encoding
    self.read_timeout = read_timeout
    self.terminators = tuple(terminators)
    self.max_line_length = max_line_length

    self.trigger_command = trigger_command
    self.untrigger_command = untrigger_command

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
  ) -> None:
    """Own the serial transport for the scanner lifespan."""
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io.lifespan())

  async def read_line(self, timeout: Optional[float] = None) -> str:
    """Read one barcode line from the serial stream.

    Args:
      timeout: Optional total read timeout in seconds. If omitted, use the
        scanner's configured reply timeout.

    Returns:
      The decoded line without the trailing line terminator. Returns an empty
      string if the timeout elapses before any byte is read.
    """
    if timeout is not None and timeout < 0:
      raise ValueError("timeout must be non-negative.")

    raw = await self._read_until_terminator(timeout=timeout)
    while any(raw.endswith(terminator) for terminator in self.terminators):
      raw = raw[:-1]
    return raw.decode(self.encoding, errors="replace")

  async def write(self, data: bytes) -> None:
    """Write raw bytes to the scanner."""
    await self.io.write(data)

  async def _read_until_terminator(self, timeout: Optional[float]) -> bytes:
    """Read bytes until a configured terminator, timeout, or length limit is reached."""
    effective_timeout = self.read_timeout if timeout is None else timeout
    buf = bytearray()
    if effective_timeout <= 0:
      return b""
    with anyio.move_on_after(effective_timeout):
      while len(buf) < self.max_line_length:
        chunk = await self.io.read(1)
        buf.extend(chunk)
        if bytes(chunk) in self.terminators:
          break

    return bytes(buf)

  async def reset_input_buffer(self) -> None:
    """Clear unread bytes buffered by the serial transport."""
    await self.io.reset_input_buffer()

  async def _untrigger_scanner(self) -> None:
    """Safely release the scanner trigger after a scan attempt."""
    if self.untrigger_command is not None:
      with anyio.CancelScope(shield=True):
        await self.write(self.untrigger_command)

  async def scan_barcode(
    self,
    read_time: Optional[float] = None,
    symbology: Union[Barcode1DSymbology, Literal["unknown"]] = "unknown",
    position_on_resource: BarcodePosition = "bottom",
  ) -> Optional[Barcode]:
    """Scan one barcode from the serial stream.

    Args:
      read_time: Maximum number of seconds to wait for a barcode. If omitted, the configured serial
        read timeout is used.
      symbology: Symbology assigned to the returned barcode.
      position_on_resource: Position assigned to the returned barcode.

    Returns:
      The scanned barcode, or ``None`` if no data is received before the timeout.
    """
    if read_time is not None and read_time < 0:
      raise ValueError("read_time must be non-negative.")

    if self.trigger_command is not None:
      await self.write(self.trigger_command)

    try:
      data = await self.read_line(timeout=read_time)
    finally:
      await self._untrigger_scanner()

    if data == "":
      return None

    logger.info("[Serial barcode scanner %s] scanned barcode: %s", self.io.port, data)
    return Barcode(
      data=data,
      symbology=symbology,
      position_on_resource=position_on_resource,
    )
