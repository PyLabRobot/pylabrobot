"""PAA KX2 onboard barcode reader.

The KX2's onboard barcode reader is a plain RS-232 device (Microscan/Omron-style
ESC-prefixed, CR-terminated commands) wired to the controller PC — entirely
independent of the CAN bus that drives the motors. As such it lives in its own
`Device` class alongside :class:`KX2`, mirroring the Keyence scanner pattern.

Protocol reference: `KX2RobotControl.cs:15573–15848` in the vendor SDK.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

try:
  import serial as pyserial

  _HAS_SERIAL = True
except ImportError as _e:
  _HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = _e

from pylabrobot.capabilities.barcode_scanning import BarcodeScanner
from pylabrobot.capabilities.barcode_scanning.backend import (
  BarcodeScannerBackend,
  BarcodeScannerError,
)
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Device, Driver
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)

_ESC = b"\x1b"
_CR = b"\r"
# Vendor C# uses `SerialPort.WriteLine(ESC + cmd + "\r")`, which appends the
# default .NET `NewLine` of `"\n"` — so the actual frame on the wire is
# ESC + cmd + CR + LF. Responses are split on CR alone (see
# SerialPortBCR_DataReceived in KX2RobotControl.cs).
_CMD_TERM = b"\r\n"


class KX2BarcodeReaderDriver(Driver):
  """Serial driver for the KX2's onboard Microscan-style barcode reader.

  Factory defaults (per `KX2RobotControl.cs:1712–1741`): 9600 8N1, no flow
  control. All commands are `ESC + <cmd ASCII> + CR`; all responses are
  `<data> + CR`.
  """

  default_baudrate = 9600

  def __init__(self, port: str, baudrate: int = default_baudrate):
    if not _HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    super().__init__()
    self.io = Serial(
      human_readable_device_name="KX2 Barcode Reader",
      port=port,
      baudrate=baudrate,
      bytesize=pyserial.EIGHTBITS,
      parity=pyserial.PARITY_NONE,
      stopbits=pyserial.STOPBITS_ONE,
      write_timeout=1,
      timeout=0.1,  # short per-byte timeout; send_command handles the real deadline
      rtscts=False,
    )

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    await self.io.setup()
    logger.info("[KX2 BCR %s] connected", self.io.port)

  async def stop(self) -> None:
    # Match the C# teardown: turn trigger off + restore default read time
    # (KX2RobotControl.cs:15623–15624) so the reader is left in a known state.
    try:
      await self.send_command("Y", timeout=1.0)
    except BarcodeScannerError:
      pass
    try:
      await self.send_command("Y2", timeout=1.0)  # read time = 2s
    except BarcodeScannerError:
      pass
    await self.io.stop()
    logger.info("[KX2 BCR %s] disconnected", self.io.port)

  async def _read_until_cr(self, timeout: float) -> str:
    """Read from the port until we see a CR, returning the decoded line.

    Raises `BarcodeScannerError` on timeout.
    """
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
      chunk = await self.io.read(32)
      if chunk:
        buf.extend(chunk)
        if _CR in buf:
          line, _, _ = buf.partition(_CR)
          decoded = line.decode("ascii", errors="replace").lstrip("\x1b").rstrip()
          return decoded
      else:
        await asyncio.sleep(0.01)
    raise BarcodeScannerError(
      f"KX2 barcode reader: timeout waiting for CR after {timeout}s (buffered={bytes(buf)!r})"
    )

  async def send_command(self, cmd: str, timeout: float = 5.0) -> str:
    """Send `ESC + cmd + CR + LF` and return the response up to the terminating CR."""
    payload = _ESC + cmd.encode("ascii") + _CMD_TERM
    await self.io.write(payload)
    decoded = await self._read_until_cr(timeout)
    logger.debug("[KX2 BCR %s] %s -> %r", self.io.port, cmd, decoded)
    return decoded

  async def read_decoded_barcode(self, timeout: float) -> str:
    """Listen for an asynchronously-delivered decoded barcode line.

    Used after firing `trigger(True)` — the reader emits the decoded data
    followed by CR whenever it makes a successful read.
    """
    return await self._read_until_cr(timeout)

  # --- typed command helpers (names mirror the C# API) --------------------

  async def trigger(self, on: bool) -> None:
    await self.send_command("Z" if on else "Y")

  async def set_read_mode(self, mode: str) -> None:
    """mode: 'single' | 'multiple' | 'continuous' (maps to S0/S1/S2)."""
    code = {"single": "S0", "multiple": "S1", "continuous": "S2"}[mode]
    await self.send_command(code)

  async def set_read_time(self, seconds: int) -> None:
    """seconds: 1..9, or 0 for indefinite (mapped to YM)."""
    if seconds == 0:
      await self.send_command("YM")
    elif 1 <= seconds <= 9:
      await self.send_command(f"Y{seconds}")
    else:
      raise ValueError("read_time must be 0 (indefinite) or 1..9 seconds")

  async def set_auto_trigger(self, on: bool) -> None:
    await self.send_command("+I" if on else "+F")

  async def get_software_version(self) -> str:
    return await self.send_command("Z1")


class KX2BarcodeReaderBackend(BarcodeScannerBackend):
  """Adapts :class:`KX2BarcodeReaderDriver` to the BarcodeScanner capability."""

  def __init__(self, driver: KX2BarcodeReaderDriver, read_time: int = 2):
    super().__init__()
    self.driver = driver
    self._read_time = read_time

  async def _on_setup(self, backend_params: Optional[BackendParams] = None) -> None:
    # Handshake: version query (mirrors KX2RobotControl.cs:15617).
    version = await self.driver.get_software_version()
    if not version:
      raise BarcodeScannerError(
        "KX2 barcode reader: empty software-version response during handshake. "
        "Verify port, baud rate, and that the reader is powered on."
      )
    logger.info("[KX2 BCR %s] software version: %s", self.driver.io.port, version)
    await self.driver.set_read_mode("single")
    await self.driver.set_read_time(self._read_time)

  async def scan_barcode(self) -> Barcode:
    # Fire the trigger, then listen for the decoded line. The reader delivers
    # data asynchronously (not as a command-response), so we read-until-CR
    # rather than sending another command.
    await self.driver.trigger(True)
    data = await self.driver.read_decoded_barcode(timeout=float(self._read_time + 1))
    if not data:
      raise BarcodeScannerError("KX2 barcode reader: no read within read-time window")
    return Barcode(data=data, symbology="ANY 1D", position_on_resource="front")


class KX2BarcodeReader(Device):
  """PAA KX2 onboard barcode reader (Microscan-style serial device)."""

  def __init__(self, port: str, baudrate: int = KX2BarcodeReaderDriver.default_baudrate):
    driver = KX2BarcodeReaderDriver(port=port, baudrate=baudrate)
    super().__init__(driver=driver)
    self.driver: KX2BarcodeReaderDriver = driver
    self.barcode_scanning = BarcodeScanner(backend=KX2BarcodeReaderBackend(driver))
    self._capabilities = [self.barcode_scanning]
