"""PAA KX2 onboard barcode reader.

The KX2's onboard barcode reader is a plain RS-232 device wired to the
controller PC — entirely independent of the CAN bus that drives the motors.
Lives in its own `Device` class alongside :class:`KX2`.

The unit shipped with KX2 systems we've inspected is a **Denso MDI-4050**
(1D laser scanner) — confirmed via ``Z4``::

  MODEL    = MDI-4050
  ROM Ver. = BD01J09
  I/F      = RS-232C

Denso's MDI series speaks an ESC-prefixed protocol close to (but not
identical to) Microscan's. The vendor C# at ``KX2RobotControl.cs:15573–15848``
covers the read-cycle subset.

Factory defaults: **9600 8N1, no flow control**. Commands are framed as
``ESC + <ascii cmd> + CR + LF``; responses are ``<data> + CR``. Useful commands:

  ``Z``    — fire trigger (start a single read window)
  ``Y``    — stop trigger
  ``Z1``   — query firmware version (handshake — single line response)
  ``Z4``   — dump the entire NVRAM config (model, version, all symbology
             enables, prefixes/suffixes, length limits — large multi-KB reply)
  ``S0/S1/S2`` — set read mode: single / multiple / continuous
  ``Y1..Y9`` / ``YM`` — read-time window (seconds, or M for indefinite)
  ``+I/+F`` — auto-trigger on / off

Symbology configuration
-----------------------

The MDI-4050 is a 1D laser, so PDF417 / DataMatrix / QR Code aren't supported.
On a fresh unit, the typical enabled set is: UPC-A, UPC-E, EAN-13, EAN-8,
Code 39, Tri-Optic, Codabar, Industrial 2/5, Interleaved 2/5, IATA, Code 128,
Code 93, GS1 DataBar, GS1 DataBar Limited. Add-on (supplemental) codes,
postal codes, MSI/Plessey, Telepen, UK/Plessey, and Code 11 are off by default.

To re-enable / disable individual symbologies, use one of:

1. **Denso's MDI Setup Windows utility** (free from Denso ADC) — checkbox UI
   over the same RS-232 port, writes to the reader's NVRAM.
2. **Configuration barcodes** printed in the MDI-4050 setting manual — scan
   a Start → toggle → End sequence, no host needed.
3. Direct register writes via the ESC protocol. The ``[NN]`` sections in the
   ``Z4`` dump correspond to NVRAM registers but the per-symbology byte
   layout is documented only in Denso's protocol manual; reverse-engineering
   from a ``Z4`` dump alone is fragile.

Use :meth:`KX2BarcodeReaderDriver.dump_config` to read the current state.

============================================================================
USB-to-serial driver setup (macOS)
============================================================================

The reader connects via a Prolific PL2303 USB-to-serial cable. macOS bundles a
`AppleUSBPLCOM.dext` for older PL2303 silicon (TA/EA), but newer chips (GC/HXN,
USB ID ``067b:23a3`` and ``067b:2303``) need Prolific's vendor DriverKit
extension:

  1. ``brew install --cask prolific-pl2303``
  2. Launch ``/Applications/PL2303Serial.app`` once so macOS registers the
     system extension.
  3. Open **System Settings → Privacy & Security**, scroll to the bottom, and
     click **Allow** on the "System software from PL2303Serial was blocked"
     prompt. macOS may require a restart.
  4. Replug the USB cable. The device shows up as
     ``/dev/tty.PL2303G-USBtoUART<n>`` (or ``/dev/cu.PL2303G-USBtoUART<n>``).
     Pass that path to :class:`KX2BarcodeReader`.

To verify before instantiating, ``systemextensionsctl list`` should show
``com.prolific.cdc.PLCdcFSDriver`` in state ``[activated enabled]``.

On Linux, the PL2303 chip is supported by the in-tree ``pl2303`` driver and
shows up as ``/dev/ttyUSB<n>`` with no extra setup.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Optional

from pylabrobot.capabilities.barcode_scanning import BarcodeScanner
from pylabrobot.capabilities.barcode_scanning.backend import (
  BarcodeScannerBackend,
  BarcodeScannerError,
)
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Device, Driver
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode

ReadMode = Literal["single", "multiple", "continuous"]

try:
  import serial as pyserial

  _HAS_SERIAL = True
except ImportError as _e:
  _HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = _e

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
      raise ImportError(
        "pyserial is not installed. Install with `pip install pylabrobot[serial]` "
        f"(import error: {_SERIAL_IMPORT_ERROR})"
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
    await self.send_command("Y")
    await self.send_command("Y2")  # read time = 2s
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

  async def send_command(self, cmd: str) -> None:
    """Fire-and-forget: send `ESC + cmd + CR + LF` and return immediately.

    The reader doesn't echo most commands (S0/S1/S2, Y/Yn/YM, Z trigger,
    +I/+F). Use :meth:`query` for commands that return a value (e.g. Z1).
    Mirrors C# `BarcodeReaderSendCommand` with ``Response=""`` — see
    `KX2RobotControl.cs:15639–15700`.
    """
    payload = _ESC + cmd.encode("ascii") + _CMD_TERM
    await self.io.write(payload)
    logger.debug("[KX2 BCR %s] %s (no wait)", self.io.port, cmd)

  async def query(self, cmd: str, timeout: float = 5.0) -> str:
    """Send a command and wait for a CR-terminated response.

    Use only for commands that the reader actually replies to (Z1
    software version). Other "set" commands time out here because the
    reader is silent on success.
    """
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

  async def set_read_mode(self, mode: ReadMode) -> None:
    """Maps to S0/S1/S2 on the wire."""
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
    return await self.query("Z1")

  async def dump_config(self, timeout: float = 8.0) -> str:
    """Return the reader's full NVRAM dump (response to ``Z4``).

    Includes the model string, firmware version, interface type, and the
    complete symbology / prefix-suffix / length-limit table. The reply is
    multi-KB and the reader streams it over a few hundred ms, so we read
    until a quiet period rather than a single CR.
    """
    payload = _ESC + b"Z4" + _CMD_TERM
    await self.io.write(payload)
    deadline = time.monotonic() + timeout
    last_byte = time.monotonic()
    buf = bytearray()
    while time.monotonic() < deadline:
      chunk = await self.io.read(2048)
      if chunk:
        buf.extend(chunk)
        last_byte = time.monotonic()
      else:
        if buf and time.monotonic() - last_byte > 1.0:
          break
        await asyncio.sleep(0.05)
    return buf.decode("ascii", errors="replace")


class KX2BarcodeReaderBackend(BarcodeScannerBackend):
  """Adapts :class:`KX2BarcodeReaderDriver` to the BarcodeScanner capability."""

  # Wait bound when the caller doesn't specify a read_time. Long enough that
  # any reasonable on-device read window (Y1..Y9) finishes inside it.
  _DEFAULT_SCAN_WAIT = 10.0

  def __init__(self, driver: KX2BarcodeReaderDriver):
    super().__init__()
    self.driver = driver

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

  async def scan_barcode(self, read_time: Optional[float] = None) -> Optional[Barcode]:
    # Reader's Y-command only takes integer seconds 1..9 (YM=indefinite). When
    # the caller specifies read_time, push it to the device first so the
    # on-device window matches our wait bound. Otherwise leave whatever's
    # currently configured and wait long enough for any in-range setting.
    if read_time is not None:
      if read_time <= 0:
        raise ValueError("read_time must be > 0")
      await self.driver.set_read_time(int(round(read_time)))
    await self.driver.trigger(True)
    timeout = (read_time + 1.0) if read_time is not None else self._DEFAULT_SCAN_WAIT
    try:
      data = await self.driver.read_decoded_barcode(timeout=timeout)
    except BarcodeScannerError:
      # Driver raises on serial-read timeout. At this layer that's the
      # "nothing decoded within the window" signal — return None instead of
      # propagating. Real comms failures aren't reported via this path.
      return None
    if not data:
      return None
    return Barcode(data=data, symbology="ANY 1D", position_on_resource="front")


class KX2BarcodeReader(Device):
  """PAA KX2 onboard barcode reader (Microscan-style serial device).

  Args:
    port: Serial device path. On macOS this is typically
      ``/dev/tty.PL2303G-USBtoUART<n>`` (after the Prolific driver is
      installed and approved — see module docstring). On Linux it's
      usually ``/dev/ttyUSB<n>``.
    baudrate: Serial baud rate; the reader's factory default is 9600.

  Usage::

      bcr = KX2BarcodeReader(port="/dev/tty.PL2303G-USBtoUART11240")
      await bcr.setup()
      barcode = await bcr.barcode_scanning.scan(read_time=8)
      print(barcode.data)
      await bcr.stop()
  """

  def __init__(
    self,
    port: str,
    baudrate: int = KX2BarcodeReaderDriver.default_baudrate,
  ):
    driver = KX2BarcodeReaderDriver(port=port, baudrate=baudrate)
    super().__init__(driver=driver)
    self.driver: KX2BarcodeReaderDriver = driver
    self._backend = KX2BarcodeReaderBackend(driver)
    self.barcode_scanning = BarcodeScanner(backend=self._backend)
    self._capabilities = [self.barcode_scanning]

  async def set_read_mode(self, mode: ReadMode) -> None:
    """Set the trigger mode: ``"single"`` (default), ``"multiple"``, or
    ``"continuous"``."""
    await self.driver.set_read_mode(mode)
