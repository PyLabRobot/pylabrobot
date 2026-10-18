"""Connection, commands, and common operations for BioTek plate readers."""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional

from pylabrobot.io.ftdi import FTDI

logger = logging.getLogger(__name__)


class _BioTekReader:
  """Shared BioTek reader behavior, independent of the assay protocol.

  The connection lock covers complete operations, including asynchronous results.
  Model classes supply initialization and assay formats. FTDI owns connection state.
  """

  def __init__(
    self, timeout: float, device_id: Optional[str], human_readable_device_name: str
  ) -> None:
    self.timeout = timeout
    self.io = FTDI(device_id=device_id, human_readable_device_name=human_readable_device_name)
    self._lock = asyncio.Lock()
    self._operation_owner: Optional[asyncio.Task] = None
    self._basecode: Optional[str] = None
    self._version: Optional[str] = None
    self._serial_number: Optional[str] = None
    self._capabilities: Dict[int, bytes] = {}
    self._receive_buffer = bytearray()

  @property
  def serial_number(self) -> Optional[str]:
    """Most recently queried reader serial number, distinct from the USB adapter ID."""
    return self._serial_number

  @property
  def version(self) -> str:
    """Firmware version reported during setup."""
    if self._version is None:
      raise RuntimeError(f"{self.__class__.__name__}: Firmware version is not set")
    return self._version

  def _reset_state(self) -> None:
    """Forget information tied to the connection; subclasses also clear model caches."""
    self._basecode = None
    self._version = None
    self._serial_number = None
    self._capabilities.clear()
    self._receive_buffer.clear()

  async def _disconnect(self) -> None:
    """Close the transport and discard connection-dependent information."""
    try:
      await self.io.stop()
    finally:
      self._reset_state()

  @asynccontextmanager
  async def _operation(self) -> AsyncIterator[None]:
    """Own the connection through an operation, allowing its nested commands.

    Only the owning task may nest operations. Other tasks wait until the complete
    response is consumed, or until a failed exchange has closed the transport.
    """
    task = asyncio.current_task()
    if task is not None and self._operation_owner is task:
      yield
      return
    async with self._lock:
      self._operation_owner = task
      try:
        yield
      except (Exception, asyncio.CancelledError):
        try:
          await self._disconnect()
        except (Exception, asyncio.CancelledError):
          logger.exception("Failed to close %s after an operation failure", type(self).__name__)
        logger.error("%s operation failed; reconnect once the reader is idle", type(self).__name__)
        raise
      finally:
        self._operation_owner = None

  async def _configure_serial(self) -> None:
    """Configure the common BioTek 9600-baud, 8N2, RTS/CTS connection settings."""
    await self.io.set_latency_timer(16)
    await self.io.set_baudrate(9600)
    await self.io.set_line_property(8, 2, 0)
    await self.io.set_flowctrl(0x100)
    await self.io.set_rts(True)

  async def _purge_buffers(self) -> None:
    """Prepare a command; protocols needing a purge override this hook."""

  @staticmethod
  def _check_status(status: bytes) -> None:
    """Validate a four-digit hexadecimal BioTek status followed by ETX."""
    if re.fullmatch(rb"[0-9a-fA-F]{4}\x03", status) is None:
      raise ValueError(f"Malformed BioTek status: {status!r}")
    if int(status[:4], 16):
      raise RuntimeError(f"BioTek reported error 0x{status[:4].decode('ascii')}")

  async def _read_chunk(self, length: int) -> bytes:
    """Read at most length bytes through the PLR transport."""
    return await self.io.read(length)

  async def _read_exactly(self, length: int) -> bytes:
    """Accumulate short reads and retain any bytes belonging to the following frame."""
    while len(self._receive_buffer) < length:
      chunk = await self._read_chunk(length - len(self._receive_buffer))
      self._receive_buffer.extend(chunk)
      if not chunk:
        await asyncio.sleep(0.01)
    data = self._receive_buffer[:length]
    del self._receive_buffer[:length]
    return bytes(data)

  async def _read_delimited(self, terminator: bytes) -> bytes:
    """Read through an ASCII frame delimiter, leaving the following frame unread."""
    data = bytearray()
    while not data.endswith(terminator):
      data.extend(await self._read_exactly(1))
    return bytes(data)

  async def _read_until(self, terminator: bytes, timeout: Optional[float] = None) -> bytes:
    """Read a delimited ASCII response within the requested timeout."""
    if not terminator:
      raise ValueError("BioTek response terminator must not be empty")
    if timeout is None:
      timeout = self.timeout
    try:
      response = await asyncio.wait_for(self._read_delimited(terminator), timeout)
    except asyncio.TimeoutError as exc:
      # Preserve the public timeout type on Python versions where these differ.
      raise TimeoutError("BioTek: timeout while waiting for a response") from exc
    logger.debug("BioTek received %s", response)
    return response

  async def _write(self, data: bytes) -> None:
    """Send a complete frame, rejecting partial writes."""
    if await self.io.write(data) != len(data):
      raise IOError("Incomplete BioTek command write")

  async def _command_unlocked(
    self,
    command: bytes,
    parameters: Optional[bytes],
    data_length: Optional[int],
    wait_for_response: bool,
  ) -> Optional[bytes]:
    """Send a command and return its wire response, preserving the public ASCII API."""
    await self._purge_buffers()
    await self._write(command)
    if parameters is None and not wait_for_response:
      return None
    ack = await self._read_exactly(1)
    if ack != b"\x06":
      raise RuntimeError(f"BioTek {command!r}: expected ACK, received {ack!r}")
    if parameters is not None:
      await self._write(parameters)
    if not wait_for_response:
      return ack
    if data_length is None:
      response = await self._read_delimited(b"\x03")
    else:
      response = await self._read_exactly(data_length + 5)
    try:
      self._check_status(response[-5:])
    except RuntimeError as exc:
      raise RuntimeError(f"BioTek {command.decode('ascii')}: {exc}") from exc
    return ack + response if parameters is None else response

  async def _command(
    self,
    command: bytes,
    parameters: Optional[bytes],
    data_length: Optional[int],
    wait_for_response: bool,
    timeout: Optional[float],
  ) -> Optional[bytes]:
    """Bound the entire command/ACK/parameter/response exchange by one deadline."""
    try:
      return await asyncio.wait_for(
        self._command_unlocked(command, parameters, data_length, wait_for_response),
        self.timeout if timeout is None else timeout,
      )
    except asyncio.TimeoutError as exc:
      raise TimeoutError("BioTek: timeout while waiting for a response") from exc

  async def send_command(
    self,
    command: str,
    parameter: Optional[str] = None,
    wait_for_response: bool = True,
    timeout: Optional[float] = None,
  ) -> Optional[bytes]:
    """Exchange an ASCII command, returning its ACK/data/status wire response.

    Commands with parameters return the response after their parameter. Commands
    without parameters include the leading ACK, as in the existing BioTek API.
    """
    async with self._operation():
      return await self._command(
        command.encode("ascii"),
        parameter.encode("ascii") if parameter is not None else None,
        None,
        wait_for_response,
        timeout,
      )

  async def _exchange(
    self,
    command: bytes,
    parameters: Optional[bytes] = None,
    data_length: int = 0,
    ack_only: bool = False,
    timeout: Optional[float] = None,
  ) -> bytes:
    """Exchange binary parameters or fixed-length data, returning only the payload."""
    async with self._operation():
      response = await self._command(command, parameters, data_length, not ack_only, timeout)
      if ack_only:
        # These commands acknowledge before their parameter, with no trailing status.
        await asyncio.sleep(0.25)
        return b""
      assert response is not None
      return response[1:-5] if parameters is None else response[:-5]

  async def query_capability(self, query: int) -> bytes:
    """Read and cache a capability's 17-byte data field for this connection.

    Byte zero is ASCII '1' when supported and '0' when unsupported. The remaining
    sixteen bytes contain capability-specific details. Malformed replies and
    instrument errors are raised rather than cached as unsupported capabilities.
    """
    if not 0 <= query <= 9999:
      raise ValueError("BioTek capability number must fit four decimal digits")
    async with self._operation():
      if query not in self._capabilities:
        response = await self.send_command("z", f"{query:04d}\x03")
        assert response is not None
        data = response[:-5]
        if len(data) != 17 or data[:1] not in (b"0", b"1"):
          raise ValueError(f"BioTek z {query:04d}: invalid capability data: {data!r}")
        self._capabilities[query] = data
      return self._capabilities[query]

  async def request_serial_number(self) -> str:
    """Query the reader's serial number."""
    async with self._operation():
      response = await self.send_command("C", timeout=1)
      assert response is not None
      serial = response[1:-5].decode("ascii").strip()
      if not serial.isdecimal():
        raise ValueError(f"Malformed BioTek serial number: {serial!r}")
      self._serial_number = serial
      return serial

  async def request_firmware_version(self) -> str:
    """Query the firmware identity and cache its basecode and version."""
    async with self._operation():
      response = await self.send_command("e", timeout=1)
      assert response is not None
      identity = response[1:-5]
      match = re.fullmatch(rb"(\d+)\s+Version\s+(\S+)\s*", identity)
      if match is None:
        raise ValueError(f"Malformed BioTek firmware identity: {identity!r}")
      self._basecode, self._version = (part.decode("ascii") for part in match.groups())
      return self._version

  async def _initialize_reader(self) -> None:
    """Identify the firmware; subclasses perform any additional initialization."""
    await self.request_firmware_version()

  async def _before_stop(self) -> None:
    """Stop model-specific background work before closing or reopening the connection."""

  async def setup(self) -> None:
    """Connect and initialize the reader. The instrument must have finished booting."""
    async with self._operation():
      await self._before_stop()
      self._reset_state()
      await self.io.setup()
      await self._configure_serial()
      await self._initialize_reader()
      logger.info("Connected to %s, firmware %s", type(self).__name__, self.version)

  async def stop(self) -> None:
    """Disconnect without disabling incubation or aborting a plate measurement.

    After a timeout or cancellation, ensure the instrument is idle before reconnecting.
    """
    async with self._operation():
      await self._before_stop()
      await self._disconnect()
      logger.info("Disconnected from %s", type(self).__name__)

  async def open(self) -> None:
    """Move the plate tray out for loading."""
    logger.info("Opening %s tray", type(self).__name__)
    await self.send_command("J")

  async def close(self) -> None:
    """Move the plate tray into the reader."""
    logger.info("Closing %s tray", type(self).__name__)
    await self.send_command("A")

  async def request_current_temperature(self) -> float:
    """Read the incubator temperature in degrees Celsius."""
    async with self._operation():
      data = await self._exchange(b"h", data_length=3, timeout=1)
      if not data.isdigit():
        raise ValueError(f"Malformed BioTek temperature: {data!r}")
      if data in (b"111", b"999"):
        raise RuntimeError("BioTek temperature is unavailable")
      return int(data) / 10

  async def get_temperature(self) -> float:
    """Read the incubator temperature in degrees Celsius."""
    return await self.request_current_temperature()
