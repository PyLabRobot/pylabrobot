import contextlib
import logging
import warnings
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Collection, Iterator, Optional, TypeVar, Union, cast

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.events import emit_event
from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import ContinuousConnectionIO
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

_SERIAL_IMPORT_ERROR: Optional[ImportError] = None
try:
  import serial
  import serial.tools.list_ports

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class _UnsetTimeout:
  """Distinguish an omitted public deadline from an explicitly supplied one."""


_UNSET_TIMEOUT = _UnsetTimeout()


def find_serial_ports(vid_pid_pairs: Collection[tuple[int, int]]) -> list[str]:
  """Return serial ports matching any of the supplied USB VID/PID pairs.

  Args:
    vid_pid_pairs: USB vendor/product identifier pairs to match.

  Raises:
    RuntimeError: If pyserial is not installed.
  """
  if not HAS_SERIAL:
    raise RuntimeError(
      "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
      f"Import error: {_SERIAL_IMPORT_ERROR}"
    )

  identifiers = set(vid_pid_pairs)
  return [
    str(port.device)
    for port in serial.tools.list_ports.comports()
    if (port.vid, port.pid) in identifiers
  ]


@dataclass
class SerialCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str, module: str = "serial"):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data


class Serial(ContinuousConnectionIO):
  """Own one serial handle per lifespan with PLR logging for I/O capture."""

  def __init__(
    self,
    human_readable_device_name: str,
    port: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    baudrate: int = 9600,
    bytesize: int = 8,  # serial.EIGHTBITS
    parity: str = "N",  # serial.PARITY_NONE
    stopbits: int = 1,  # serial.STOPBITS_ONE,
    write_timeout=1,
    timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    rtscts: bool = False,
    dsrdtr: bool = False,
    xonxoff: bool = False,
  ):
    super().__init__()
    self._operation_lock = anyio.Lock()
    self._limiter = anyio.CapacityLimiter(1)
    self.human_readable_device_name = human_readable_device_name
    self._port = port
    self._vid = vid
    self._pid = pid
    self.baudrate = baudrate
    self.bytesize = bytesize
    self.parity = parity
    self.stopbits = stopbits
    self._ser: Optional[serial.Serial] = None
    self._connection_lost = False
    self._unread = bytearray()
    self.write_timeout = write_timeout
    if isinstance(timeout, _UnsetTimeout):
      self.timeout: Optional[float] = None
    else:
      warnings.warn(
        "Serial.timeout is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
      self.timeout = timeout
    self._poll_timeout = 0.1
    self.rtscts = rtscts
    self.dsrdtr = dsrdtr
    self.xonxoff = xonxoff

    # Instant parameter validation at init time
    if not self._port and not (self._vid and self._pid):
      raise ValueError("Must specify either port or vid and pid.")

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new Serial object while capture or validation is active")

  @property
  def port(self) -> str:
    assert self._port is not None, "Port not set. Did you enter lifespan()?"
    return self._port

  def get_read_timeout(self) -> float:
    """Get the current read timeout in seconds."""
    return float(self._require_serial().timeout)

  def set_read_timeout(self, timeout: float) -> None:
    """Set the read timeout in seconds."""
    self._require_serial().timeout = timeout

  @contextlib.contextmanager
  def temporary_timeout(self, timeout: float) -> Iterator[None]:
    """Context manager that temporarily changes the read timeout, then restores it."""
    original = self.get_read_timeout()
    self.set_read_timeout(timeout)
    try:
      yield
    finally:
      self.set_read_timeout(original)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """
    Initialize the serial connection to the device.

    This method resolves the appropriate serial port (either from an explicitly
    provided path or by scanning for devices matching the configured USB
    VID:PID pair), validates that the detected/selected port corresponds to
    the expected hardware, and opens the serial connection in a dedicated
    worker thread to avoid blocking the async event loop.

    **Behavior:**
    - Ensures `pyserial` is installed; otherwise raises `RuntimeError`.
    - If a port is not explicitly provided:
        - Scans all available COM ports and filters them by matching
          `VID:PID` against the device's hardware ID string.
        - Raises an error if zero matches are found.
        - Raises an error if multiple matches are found and no port is specified.
    - If a port *is* explicitly provided:
        - Verifies that it matches the specified VID/PID (when provided).
        - Logs the port choice for traceability.
    - Opens the serial port using the configured parameters
      (baudrate, bytesize, parity, etc.) via an AnyIO worker thread to
      ensure non-blocking operation.
    - Closes a partially acquired port and re-raises if opening fails or is cancelled.

    **Raises:**
      RuntimeError:
        - If `pyserial` is missing.
        - If no matching serial devices are found for the given VID/PID and
          no explicit port was provided.
        - If multiple matching devices exist and the port is ambiguous.
        - If an explicitly provided port does not match the VID/PID.
      serial.SerialException:
        - If the serial connection fails to open (e.g., device already in use).

    After successful completion, `self._ser` is an open `serial.Serial`
    instance and `self._port` is updated to the resolved port path.
    """
    await super()._enter_lifespan(stack)

    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )

    try:
      resolved_port = await anyio.to_thread.run_sync(self._setup_sync, limiter=self._limiter)
      self._port = resolved_port
    except BaseException:
      with anyio.CancelScope(shield=True):
        await self._close()
      raise
    self._connection_lost = False
    stack.push_shielded_async_callback(self._close)

  def _setup_sync(self) -> str:
    """Resolve the port and open the connection in the transport's serialized worker.

    Returns the resolved port.
    """

    # 1. VID:PID specified - port maybe
    if self._vid is not None and self._pid is not None:
      matching_ports = [
        p.device
        for p in serial.tools.list_ports.comports()
        if f"{self._vid:04X}:{self._pid:04X}" in (p.hwid or "")
      ]

      # 1.a. No matching devices found AND no port specified
      if self._port is None and len(matching_ports) == 0:
        raise RuntimeError(
          f"No machines found for VID={self._vid}, PID={self._pid}, and no port specified."
        )

    else:
      matching_ports = []

    # 2. Port specified - skip VID:PID validation, trust the user
    if self._port:  # Port explicitly specified
      candidate_port = self._port
      logger.info(
        f"Using explicitly provided port: {candidate_port} (for VID={self._vid}, PID={self._pid})",
      )

    # 3. VID:PID specified -  port not specified -> Single device found -> WINNER by VID:PID search
    elif len(matching_ports) == 1:
      candidate_port = matching_ports[0]

    # 4. VID:PID specified -  port not specified -> Multiple devices found -> ambiguity!
    else:
      raise RuntimeError(
        f"Multiple devices detected with VID:PID {self._vid}:{self._pid}.\n"
        f"Detected ports: {matching_ports}\n"
        "Please specify the correct port address explicitly (e.g. /dev/ttyUSB0 or COM3)."
      )

    try:
      self._ser = serial.Serial(
        port=candidate_port,
        baudrate=self.baudrate,
        bytesize=self.bytesize,
        parity=self.parity,
        stopbits=self.stopbits,
        write_timeout=self.write_timeout,
        timeout=self._poll_timeout,
        rtscts=self.rtscts,
        dsrdtr=self.dsrdtr,
        xonxoff=self.xonxoff,
      )
    except serial.SerialException:
      logger.error(
        f"Could not connect to device '{self.human_readable_device_name}', is it in use by a different notebook/process?"
      )
      raise

    return candidate_port

  async def _close(self) -> None:
    """Close the serial device."""

    async with self._operation_lock:
      ser = self._ser
      if ser is None:
        return
      try:
        if ser.is_open:
          await anyio.to_thread.run_sync(ser.close, limiter=self._limiter)
      finally:
        self._ser = None
        self._unread.clear()

  def _require_serial(self) -> "serial.Serial":
    """Reject public I/O after an observed hard failure without replacing the handle."""
    if self._connection_lost:
      raise ConnectionError("Serial connection lost; enter a new lifespan")
    ser = self._ser
    if ser is None:
      raise RuntimeError(f"Enter lifespan() first for device '{self.human_readable_device_name}'.")
    return ser

  async def _run_io(self, func: Callable[..., _T], *args: Any) -> _T:
    """Run one blocking I/O call and invalidate the connection on hard failure."""
    try:
      return await anyio.to_thread.run_sync(func, *args, limiter=self._limiter)
    except serial.SerialTimeoutException:
      raise
    except (serial.SerialException, OSError):
      self._connection_lost = True
      raise

  async def write(self, data: bytes):
    """Write data to the serial device."""

    async with self._operation_lock:
      ser = self._require_serial()
      await anyio.lowlevel.checkpoint()
      with anyio.CancelScope(shield=True):
        written = await self._run_io(ser.write, data)
      if written != len(data):
        self._connection_lost = True
        raise OSError(f"Serial short write: wrote {written} of {len(data)} bytes")

    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self.port, action="write", data=data.decode("unicode_escape"))
    )
    emit_event(
      "io.write",
      transport="serial",
      device=self.human_readable_device_name,
      device_id=self.port,
      data=data.decode("utf-8", errors="backslashreplace"),
    )

  @staticmethod
  def _read_available_sync(ser: "serial.Serial", num_bytes: int) -> bytes:
    """Poll for one byte, then drain bytes already queued without waiting to fill."""
    first = cast(bytes, ser.read(1))
    if not first:
      return b""
    available = ser.in_waiting
    if available and len(first) < num_bytes:
      return first + cast(bytes, ser.read(min(available, num_bytes - len(first))))
    return first

  async def read(self, num_bytes: int = 1) -> bytes:
    """Read up to num_bytes, waiting for the first available byte."""

    if num_bytes <= 0:
      raise ValueError("num_bytes must be positive")

    async with self._operation_lock:
      ser = self._require_serial()
      with anyio.fail_after(self.timeout):
        while not self._unread:
          with anyio.CancelScope(shield=True):
            data = await self._run_io(self._read_available_sync, ser, num_bytes)
            self._unread.extend(data)
          await anyio.lowlevel.checkpoint_if_cancelled()
      data = bytes(self._unread[:num_bytes])
      del self._unread[:num_bytes]

    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self.port, action="read", data=data.decode("unicode_escape"))
    )
    emit_event(
      "io.read",
      transport="serial",
      device=self.human_readable_device_name,
      device_id=self.port,
      data=data.decode("utf-8", errors="backslashreplace"),
    )

    return cast(bytes, data)

  async def readline(self) -> bytes:
    """Wait for a newline in the shared read stream under the caller's deadline."""

    async with self._operation_lock:
      ser = self._require_serial()
      with anyio.fail_after(self.timeout):
        while b"\n" not in self._unread:
          with anyio.CancelScope(shield=True):
            chunk = await self._run_io(self._read_available_sync, ser, 1024)
            self._unread.extend(chunk)
          await anyio.lowlevel.checkpoint_if_cancelled()
      end = self._unread.index(b"\n") + 1
      data = bytes(self._unread[:end])
      del self._unread[:end]

    if len(data) != 0:
      logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._port, data)
      capturer.record(
        SerialCommand(device_id=self.port, action="readline", data=data.decode("unicode_escape"))
      )
      emit_event(
        "io.read",
        transport="serial",
        device=self.human_readable_device_name,
        device_id=self.port,
        data=data.decode("utf-8", errors="backslashreplace"),
      )

    return cast(bytes, data)

  async def send_break(self, duration: float):
    """Send a break condition for the specified duration."""

    async with self._operation_lock:
      ser = self._require_serial()
      await self._run_io(partial(ser.send_break, duration=duration))
    logger.log(LOG_LEVEL_IO, "[%s] send_break %s", self._port, duration)
    capturer.record(SerialCommand(device_id=self.port, action="send_break", data=str(duration)))

  async def reset_input_buffer(self):
    async with self._operation_lock:
      ser = self._require_serial()
      await self._run_io(ser.reset_input_buffer)
      self._unread.clear()
    logger.log(LOG_LEVEL_IO, "[%s] reset_input_buffer", self._port)
    capturer.record(SerialCommand(device_id=self.port, action="reset_input_buffer", data=""))

  async def reset_output_buffer(self):
    async with self._operation_lock:
      ser = self._require_serial()
      await self._run_io(ser.reset_output_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] reset_output_buffer", self._port)
    capturer.record(SerialCommand(device_id=self.port, action="reset_output_buffer", data=""))

  @property
  def dtr(self) -> bool:
    """Get the DTR (Data Terminal Ready) status."""
    ser = self._require_serial()
    assert self._port is not None
    try:
      value = ser.dtr
    except OSError:
      self._connection_lost = True
      raise
    capturer.record(SerialCommand(device_id=self._port, action="get_dtr", data=str(value)))
    return value  # type: ignore # ?

  @dtr.setter
  def dtr(self, value: bool):
    """Set the DTR (Data Terminal Ready) status."""
    ser = self._require_serial()
    assert self._port is not None
    logger.log(LOG_LEVEL_IO, "[%s] set DTR %s", self._port, value)
    capturer.record(SerialCommand(device_id=self._port, action="set_dtr", data=str(value)))
    try:
      ser.dtr = value
    except OSError:
      self._connection_lost = True
      raise

  @property
  def rts(self) -> bool:
    """Get the RTS (Request To Send) status."""
    ser = self._require_serial()
    assert self._port is not None
    try:
      value = ser.rts
    except OSError:
      self._connection_lost = True
      raise
    capturer.record(SerialCommand(device_id=self._port, action="get_rts", data=str(value)))
    return value  # type: ignore # ?

  @rts.setter
  def rts(self, value: bool):
    """Set the RTS (Request To Send) status."""
    ser = self._require_serial()
    assert self._port is not None
    logger.log(LOG_LEVEL_IO, "[%s] set RTS %s", self._port, value)
    capturer.record(SerialCommand(device_id=self._port, action="set_rts", data=str(value)))
    try:
      ser.rts = value
    except OSError:
      self._connection_lost = True
      raise

  def serialize(self):
    return {
      "human_readable_device_name": self.human_readable_device_name,
      "port": self._port,
      "baudrate": self.baudrate,
      "bytesize": self.bytesize,
      "parity": self.parity,
      "stopbits": self.stopbits,
      "write_timeout": self.write_timeout,
      "timeout": self.timeout,
      "rtscts": self.rtscts,
      "dsrdtr": self.dsrdtr,
    }


class SerialValidator(Serial):
  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    port: str,
    baudrate: int = 9600,
    bytesize: int = 8,  # serial.EIGHTBITS
    parity: str = "N",  # serial.PARITY_NONE
    stopbits: int = 1,  # serial.STOPBITS_ONE,
    write_timeout=1,
    timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    rtscts: bool = False,
    dsrdtr: bool = False,
  ):
    super().__init__(
      port=port,
      human_readable_device_name=human_readable_device_name,
      baudrate=baudrate,
      bytesize=bytesize,
      parity=parity,
      stopbits=stopbits,
      write_timeout=write_timeout,
      timeout=timeout,
      rtscts=rtscts,
      dsrdtr=dsrdtr,
    )
    self.cr = cr

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Enter a validator lifespan without opening a serial device."""

  async def write(self, data: bytes):
    await anyio.lowlevel.checkpoint()
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial write")
    if next_command.data != data.decode("unicode_escape"):
      align_sequences(expected=next_command.data, actual=data.decode("unicode_escape"))
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def read(self, num_bytes: int = 1) -> bytes:
    if num_bytes <= 0:
      raise ValueError("num_bytes must be positive")
    await anyio.lowlevel.checkpoint()
    next_command = SerialCommand(**self.cr.next_command())
    data = next_command.data.encode()
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "read"
      and len(data) <= num_bytes
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial read {num_bytes}")
    if not data:
      raise anyio.EndOfStream
    return data

  async def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "readline"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial readline")
    return next_command.data.encode()

  async def send_break(self, duration: float):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "send_break"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial send_break")
    if float(next_command.data) != duration:
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def reset_input_buffer(self):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "reset_input_buffer"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial reset_input_buffer")

  async def reset_output_buffer(self):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "reset_output_buffer"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial reset_output_buffer")

  @property
  def dtr(self) -> bool:
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "get_dtr"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial get_dtr")
    return next_command.data.lower() == "true"

  @dtr.setter
  def dtr(self, value: bool):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "set_dtr"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial set_dtr")
    if next_command.data.lower() != str(value).lower():
      raise ValidationError("Data mismatch: difference was written to stdout.")

  @property
  def rts(self) -> bool:
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "get_rts"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial get_rts")
    return next_command.data.lower() == "true"

  @rts.setter
  def rts(self, value: bool):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "set_rts"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial set_rts")
    if next_command.data.lower() != str(value).lower():
      raise ValidationError("Data mismatch: difference was written to stdout.")
