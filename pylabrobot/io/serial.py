import asyncio
import contextlib
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import IOBase
from typing import Collection, Iterator, Optional, cast

from pylabrobot.events import emit_event
from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
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


def _as_text(data: bytes) -> str:
  """Bytes as text for the logs and captures (``unicode_escape``), for binary data too: an
  invalid escape is kept as it is, without a warning."""
  with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    return data.decode("unicode_escape", errors="backslashreplace")


def serial_number_matches(found: Optional[str], wanted: str) -> bool:
  """Whether a port's USB serial number is ``wanted``.

  Windows' FTDI driver adds the interface's letter (A to D) to the chip's serial number
  ("D30G5GV5" is listed as "D30G5GV5A"), so that letter is allowed either way: on the port, or in
  ``wanted`` when it was copied from Windows and the port is on Linux or macOS.
  """
  if not found:
    return False
  found, wanted = found.upper(), wanted.upper()

  def plus_letter(longer: str, shorter: str) -> bool:
    """Whether ``longer`` is ``shorter`` with an interface letter added."""
    return len(longer) == len(shorter) + 1 and longer.startswith(shorter) and longer[-1] in "ABCD"

  return found == wanted or plus_letter(found, wanted) or plus_letter(wanted, found)


def find_serial_ports(
  vid_pid_pairs: Collection[tuple[int, int]], serial_number: Optional[str] = None
) -> list[str]:
  """Return serial ports matching any of the supplied USB VID/PID pairs.

  Args:
    vid_pid_pairs: USB vendor/product identifier pairs to match.
    serial_number: only the port whose USB serial number this is (see ``serial_number_matches``).

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
    and (serial_number is None or serial_number_matches(port.serial_number, serial_number))
  ]


@dataclass
class SerialCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str, module: str = "serial"):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data


class Serial(IOBase):
  """Thin wrapper around serial.Serial to include PLR logging (for io testing)."""

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
    timeout=1,
    rtscts: bool = False,
    dsrdtr: bool = False,
    xonxoff: bool = False,
    serial_number: Optional[str] = None,
    dtr: Optional[bool] = None,
    rts: Optional[bool] = None,
    write_while_reading: bool = False,
  ):
    """
    Args:
      serial_number: with ``vid`` and ``pid``, the device whose USB serial number this is, when
        several of the same are plugged in (``serial_number_matches``).
      dtr: the DTR line's state as soon as the port is open; None leaves pyserial's default
        (on). Some devices take DTR or RTS going on as a reset or a request to talk. (On Linux
        and macOS the system raises both for a moment as the port opens.)
      rts: as ``dtr``, for RTS.
      write_while_reading: writes on a worker of their own, so a write doesn't wait for a read in
        progress (a read waits up to ``timeout`` for bytes). For protocols that read continuously
        and must answer quickly; pyserial takes a read and a write from two threads at once. Don't
        change the timeouts (``set_read_timeout``) while a write may be running.
    """
    self.human_readable_device_name = human_readable_device_name
    self._port = port
    self._vid = vid
    self._pid = pid
    self.baudrate = baudrate
    self.bytesize = bytesize
    self.parity = parity
    self.stopbits = stopbits
    self._ser: Optional[serial.Serial] = None
    self._executor: Optional[ThreadPoolExecutor] = None
    self.write_timeout = write_timeout
    self.timeout = timeout
    self.rtscts = rtscts
    self.dsrdtr = dsrdtr
    self.xonxoff = xonxoff
    self.serial_number = serial_number
    self._initial_dtr = dtr
    self._initial_rts = rts
    self.write_while_reading = write_while_reading
    self._write_executor: Optional[ThreadPoolExecutor] = None

    # Instant parameter validation at init time
    if not self._port and not (self._vid and self._pid):
      raise ValueError("Must specify either port or vid and pid.")

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new Serial object while capture or validation is active")

  @property
  def port(self) -> str:
    assert self._port is not None, "Port not set. Did you call setup()?"
    return self._port

  def get_read_timeout(self) -> float:
    """Get the current read timeout in seconds."""
    assert self._ser is not None, "Serial port not open. Did you call setup()?"
    return float(self._ser.timeout)

  def set_read_timeout(self, timeout: float) -> None:
    """Set the read timeout in seconds."""
    assert self._ser is not None, "Serial port not open. Did you call setup()?"
    self._ser.timeout = timeout

  @contextlib.contextmanager
  def temporary_timeout(self, timeout: float) -> Iterator[None]:
    """Context manager that temporarily changes the read timeout, then restores it."""
    original = self.get_read_timeout()
    self.set_read_timeout(timeout)
    try:
      yield
    finally:
      self.set_read_timeout(original)

  async def setup(self):
    """
    Initialize the serial connection to the device.

    This method resolves the appropriate serial port (either from an explicitly
    provided path or by scanning for devices matching the configured USB
    VID:PID pair), validates that the detected/selected port corresponds to
    the expected hardware, and opens the serial connection in a dedicated
    threadpool executor to avoid blocking the asyncio event loop.

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
      (baudrate, bytesize, parity, etc.) via `loop.run_in_executor` to
      ensure non-blocking operation.
    - Cleans up the executor and re-raises the exception if the port cannot be opened.

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

    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )

    loop = asyncio.get_running_loop()
    self._executor = ThreadPoolExecutor(max_workers=1)
    if self.write_while_reading:
      self._write_executor = ThreadPoolExecutor(max_workers=1)

    setup_future = loop.run_in_executor(self._executor, self._setup_sync)
    try:
      resolved_port = await asyncio.shield(setup_future)
      self._port = resolved_port
    except BaseException as exc:
      if isinstance(exc, asyncio.CancelledError):
        try:
          await setup_future
        except BaseException:
          pass
      if self._ser is not None:
        try:
          await loop.run_in_executor(self._executor, self._ser.close)
        except Exception:
          logger.warning("Failed to close serial port after setup failure", exc_info=True)
        self._ser = None
      self._shutdown_executor()
      raise

    assert self._ser is not None

  def _shutdown_executor(self) -> None:
    if self._write_executor is not None:
      self._write_executor.shutdown(wait=False, cancel_futures=True)
      self._write_executor = None
    if self._executor is not None:
      # the worker is idle here, so this does not block the event loop
      self._executor.shutdown(wait=False, cancel_futures=True)
      self._executor = None

  def _setup_sync(self) -> str:
    """Resolve the port and open the connection. Runs on the executor that owns all device calls.

    Returns the resolved port.
    """

    # 1. VID:PID specified - port maybe
    if self._vid is not None and self._pid is not None:
      found = [
        p
        for p in serial.tools.list_ports.comports()
        if f"{self._vid:04X}:{self._pid:04X}" in (p.hwid or "")
      ]
      matching_ports = [
        p.device
        for p in found
        if self.serial_number is None or serial_number_matches(p.serial_number, self.serial_number)
      ]

      # 1.a. No matching devices found AND no port specified
      if self._port is None and len(matching_ports) == 0:
        if self.serial_number is not None:
          seen = ", ".join(f"{p.device} ({p.serial_number})" for p in found) or "none"
          raise RuntimeError(
            f"No machine with serial number {self.serial_number} for VID={self._vid}, "
            f"PID={self._pid}; seen: {seen}."
          )
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
        f"Multiple devices detected with VID:PID {self._vid}:{self._pid}"
        + (f" and serial number {self.serial_number}" if self.serial_number else "")
        + ".\n"
        f"Detected ports: {matching_ports}\n"
        "Please specify the correct port address explicitly (e.g. /dev/ttyUSB0 or COM3)."
      )

    try:
      # Configured first and opened after, so the port opens with the DTR and RTS asked for.
      ser = serial.Serial()
      ser.port = candidate_port
      ser.baudrate = self.baudrate
      ser.bytesize = self.bytesize
      ser.parity = self.parity
      ser.stopbits = self.stopbits
      ser.write_timeout = self.write_timeout
      ser.timeout = self.timeout
      ser.rtscts = self.rtscts
      ser.dsrdtr = self.dsrdtr
      ser.xonxoff = self.xonxoff
      if self._initial_dtr is not None:
        ser.dtr = self._initial_dtr
      if self._initial_rts is not None:
        ser.rts = self._initial_rts
      ser.open()
      self._ser = ser
    except serial.SerialException:
      logger.error(
        f"Could not connect to device '{self.human_readable_device_name}', is it in use by a different notebook/process?"
      )
      raise

    return candidate_port

  @property
  def is_open(self) -> bool:
    """Whether the port is open."""
    return self._ser is not None and bool(self._ser.is_open)

  async def stop(self):
    """Close the serial device.

    A read waiting for bytes is cancelled first, and a write in progress (``write_while_reading``)
    is waited for, so the port is never closed under either.
    """

    if self._ser is not None and self._ser.is_open:
      loop = asyncio.get_running_loop()

      if self._executor is None:
        raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")
      self._ser.cancel_read()  # returns a read waiting on the executor at once
      if self._write_executor is not None:
        await loop.run_in_executor(self._write_executor, lambda: None)
      await loop.run_in_executor(self._executor, self._ser.close)

    self._ser = None
    self._shutdown_executor()

  async def write(self, data: bytes):
    """Write data to the serial device."""

    loop = asyncio.get_running_loop()
    if self._executor is None or self._ser is None:
      raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")

    await loop.run_in_executor(self._write_executor or self._executor, self._ser.write, data)

    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._port, data)
    capturer.record(
      SerialCommand(
        device_id=self.port,
        action="write",
        data=_as_text(data),
      )
    )
    emit_event(
      "io.write",
      transport="serial",
      device=self.human_readable_device_name,
      device_id=self.port,
      data=data.decode("utf-8", errors="backslashreplace"),
    )

  async def read(self, num_bytes: int = 1) -> bytes:
    """Read data from the serial device."""

    loop = asyncio.get_running_loop()
    if self._executor is None or self._ser is None:
      raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")

    data = await loop.run_in_executor(self._executor, self._ser.read, num_bytes)

    if len(data) != 0:
      logger.log(LOG_LEVEL_IO, "[%s] read %s", self._port, data)
      capturer.record(
        SerialCommand(
          device_id=self.port,
          action="read",
          data=_as_text(data),
        )
      )
      emit_event(
        "io.read",
        transport="serial",
        device=self.human_readable_device_name,
        device_id=self.port,
        data=data.decode("utf-8", errors="backslashreplace"),
      )

    return cast(bytes, data)

  async def read_available(self, max_bytes: int = 4096) -> bytes:
    """Read what has arrived, up to ``max_bytes``: it waits at most ``timeout`` for the first
    byte, and returns as soon as there is any (``read`` waits until it has ``num_bytes``)."""

    loop = asyncio.get_running_loop()
    if self._executor is None or self._ser is None:
      raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")
    ser = self._ser

    def read() -> bytes:
      """Read what is waiting, or wait for one byte."""
      waiting = ser.in_waiting
      return cast(bytes, ser.read(min(max(1, waiting), max_bytes)))

    data = await loop.run_in_executor(self._executor, read)

    if len(data) != 0:
      logger.log(LOG_LEVEL_IO, "[%s] read %s", self._port, data)
      capturer.record(
        SerialCommand(
          device_id=self.port,
          action="read",
          data=_as_text(data),
        )
      )
      emit_event(
        "io.read",
        transport="serial",
        device=self.human_readable_device_name,
        device_id=self.port,
        data=data.decode("utf-8", errors="backslashreplace"),
      )

    return data

  async def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    """Read a line from the serial device."""

    loop = asyncio.get_running_loop()
    if self._executor is None or self._ser is None:
      raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")

    data = await loop.run_in_executor(self._executor, self._ser.readline)

    if len(data) != 0:
      logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._port, data)
      capturer.record(
        SerialCommand(
          device_id=self.port,
          action="readline",
          data=_as_text(data),
        )
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

    loop = asyncio.get_running_loop()
    if self._executor is None or self._ser is None:
      raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")

    def _send_break(ser, duration: float) -> None:
      """Send a break condition for the specified duration."""
      assert ser is not None, "forgot to call setup?"
      ser.send_break(duration=duration)

    await loop.run_in_executor(self._executor, lambda: _send_break(self._ser, duration=duration))
    logger.log(LOG_LEVEL_IO, "[%s] send_break %s", self._port, duration)
    capturer.record(SerialCommand(device_id=self.port, action="send_break", data=str(duration)))

  async def reset_input_buffer(self):
    loop = asyncio.get_running_loop()
    if self._executor is None or self._ser is None:
      raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")
    await loop.run_in_executor(self._executor, self._ser.reset_input_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] reset_input_buffer", self._port)
    capturer.record(SerialCommand(device_id=self.port, action="reset_input_buffer", data=""))

  async def reset_output_buffer(self):
    loop = asyncio.get_running_loop()
    if self._executor is None or self._ser is None:
      raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")
    await loop.run_in_executor(self._executor, self._ser.reset_output_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] reset_output_buffer", self._port)
    capturer.record(SerialCommand(device_id=self.port, action="reset_output_buffer", data=""))

  @property
  def dtr(self) -> bool:
    """Get the DTR (Data Terminal Ready) status."""
    assert self._ser is not None and self._port is not None, "forgot to call setup?"
    value = self._ser.dtr
    capturer.record(SerialCommand(device_id=self._port, action="get_dtr", data=str(value)))
    return value  # type: ignore # ?

  @dtr.setter
  def dtr(self, value: bool):
    """Set the DTR (Data Terminal Ready) status."""
    assert self._ser is not None and self._port is not None, "forgot to call setup?"
    logger.log(LOG_LEVEL_IO, "[%s] set DTR %s", self._port, value)
    capturer.record(SerialCommand(device_id=self._port, action="set_dtr", data=str(value)))
    self._ser.dtr = value

  @property
  def rts(self) -> bool:
    """Get the RTS (Request To Send) status."""
    assert self._ser is not None and self._port is not None, "forgot to call setup?"
    value = self._ser.rts
    capturer.record(SerialCommand(device_id=self._port, action="get_rts", data=str(value)))
    return value  # type: ignore # ?

  @rts.setter
  def rts(self, value: bool):
    """Set the RTS (Request To Send) status."""
    assert self._ser is not None and self._port is not None, "forgot to call setup?"
    logger.log(LOG_LEVEL_IO, "[%s] set RTS %s", self._port, value)
    capturer.record(SerialCommand(device_id=self._port, action="set_rts", data=str(value)))
    self._ser.rts = value

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
      "vid": self._vid,
      "pid": self._pid,
      "serial_number": self.serial_number,
      "dtr": self._initial_dtr,
      "rts": self._initial_rts,
      "write_while_reading": self.write_while_reading,
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
    timeout=1,
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

  async def setup(self):
    pass

  async def write(self, data: bytes):
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial write")
    if next_command.data != _as_text(data):
      align_sequences(
        expected=next_command.data,
        actual=_as_text(data),
      )
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def read(self, num_bytes: int = 1) -> bytes:
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "read"
      and len(next_command.data) == num_bytes
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial read {num_bytes}")
    return next_command.data.encode()

  async def read_available(self, max_bytes: int = 4096) -> bytes:
    next_command = SerialCommand(**self.cr.next_command())
    if not (
      next_command.module == "serial"
      and next_command.device_id == self._port
      and next_command.action == "read"
    ):
      raise ValidationError(f"Next line is {next_command}, expected Serial read")
    return next_command.data.encode()

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
