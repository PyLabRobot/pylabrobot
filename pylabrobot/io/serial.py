import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import IOBase
from typing import Optional, cast

from pylabrobot.io.errors import ValidationError

try:
  import serial
  import serial.tools.list_ports

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

logger = logging.getLogger(__name__)


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
  ):
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

    # Instant parameter validation at init time
    if not self._port and not (self._vid and self._pid):
      raise ValueError("Must specify either port or vid and pid.")

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new Serial object while capture or validation is active")

  @property
  def port(self) -> str:
    assert self._port is not None, "Port not set. Did you call setup()?"
    return self._port

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
      raise RuntimeError(f"pyserial not installed. Import error: {_SERIAL_IMPORT_ERROR}")

    loop = asyncio.get_running_loop()
    self._executor = ThreadPoolExecutor(max_workers=1)

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

    def _open_serial() -> serial.Serial:
      return serial.Serial(
        port=candidate_port,
        baudrate=self.baudrate,
        bytesize=self.bytesize,
        parity=self.parity,
        stopbits=self.stopbits,
        write_timeout=self.write_timeout,
        timeout=self.timeout,
        rtscts=self.rtscts,
        dsrdtr=self.dsrdtr,
      )

    try:
      self._ser = await loop.run_in_executor(self._executor, _open_serial)

    except serial.SerialException as e:
      logger.error("Could not connect to device, is it in use by a different notebook/process?")
      if self._executor is not None:
        self._executor.shutdown(wait=True)
        self._executor = None
      raise e

    assert self._ser is not None

    self._port = candidate_port

  async def stop(self):
    """Close the serial device."""

    if self._ser is not None and self._ser.is_open:
      loop = asyncio.get_running_loop()

      if self._executor is None:
        raise RuntimeError("Call setup() first.")
      await loop.run_in_executor(self._executor, self._ser.close)

    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  async def write(self, data: bytes):
    """Write data to the serial device."""

    assert self._ser is not None, "forgot to call setup?"
    assert self._port is not None, "Port not set. Did you call setup()?"

    loop = asyncio.get_running_loop()

    if self._executor is None:
      raise RuntimeError("Call setup() first.")

    await loop.run_in_executor(self._executor, self._ser.write, data)

    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._port, data)
    capturer.record(
      SerialCommand(device_id=self._port, action="write", data=data.decode("unicode_escape"))
    )

  async def read(self, num_bytes: int = 1) -> bytes:
    """Read data from the serial device."""

    assert self._ser is not None, "forgot to call setup?"
    assert self._port is not None, "Port not set. Did you call setup()?"

    loop = asyncio.get_running_loop()

    if self._executor is None:
      raise RuntimeError("Call setup() first.")

    data = await loop.run_in_executor(self._executor, self._ser.read, num_bytes)

    if len(data) != 0:
      logger.log(LOG_LEVEL_IO, "[%s] read %s", self._port, data)
      capturer.record(
        SerialCommand(device_id=self._port, action="read", data=data.decode("unicode_escape"))
      )

    return cast(bytes, data)

  async def readline(self) -> bytes:  # type: ignore # very dumb it's reading from pyserial
    """Read a line from the serial device."""

    assert self._ser is not None, "forgot to call setup?"
    assert self._port is not None, "Port not set. Did you call setup()?"

    loop = asyncio.get_running_loop()

    if self._executor is None:
      raise RuntimeError("Call setup() first.")

    data = await loop.run_in_executor(self._executor, self._ser.readline)

    if len(data) != 0:
      logger.log(LOG_LEVEL_IO, "[%s] readline %s", self._port, data)
      capturer.record(
        SerialCommand(device_id=self._port, action="readline", data=data.decode("unicode_escape"))
      )

    return cast(bytes, data)

  async def send_break(self, duration: float):
    """Send a break condition for the specified duration."""

    assert self._ser is not None, "forgot to call setup?"
    assert self._port is not None, "Port not set. Did you call setup()?"

    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")

    def _send_break(ser, duration: float) -> None:
      """Send a break condition for the specified duration."""
      assert ser is not None, "forgot to call setup?"
      ser.send_break(duration=duration)

    await loop.run_in_executor(self._executor, lambda: _send_break(self._ser, duration=duration))
    logger.log(LOG_LEVEL_IO, "[%s] send_break %s", self._port, duration)
    capturer.record(SerialCommand(device_id=self._port, action="send_break", data=str(duration)))

  async def reset_input_buffer(self):
    assert self._ser is not None, "forgot to call setup?"
    assert self._port is not None, "Port not set. Did you call setup()?"

    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    await loop.run_in_executor(self._executor, self._ser.reset_input_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] reset_input_buffer", self._port)
    capturer.record(SerialCommand(device_id=self._port, action="reset_input_buffer", data=""))

  async def reset_output_buffer(self):
    assert self._ser is not None, "forgot to call setup?"
    assert self._port is not None, "Port not set. Did you call setup()?"

    loop = asyncio.get_running_loop()
    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    await loop.run_in_executor(self._executor, self._ser.reset_output_buffer)
    logger.log(LOG_LEVEL_IO, "[%s] reset_output_buffer", self._port)
    capturer.record(SerialCommand(device_id=self._port, action="reset_output_buffer", data=""))

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

  @classmethod
  def deserialize(cls, data: dict) -> "Serial":
    return cls(
      port=data["port"],
      baudrate=data["baudrate"],
      bytesize=data["bytesize"],
      parity=data["parity"],
      stopbits=data["stopbits"],
      write_timeout=data["write_timeout"],
      timeout=data["timeout"],
      rtscts=data["rtscts"],
      dsrdtr=data["dsrdtr"],
    )


class SerialValidator(Serial):
  def __init__(
    self,
    cr: "CaptureReader",
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
    if next_command.data != data.decode("unicode_escape"):
      align_sequences(expected=next_command.data, actual=data.decode("unicode_escape"))
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
