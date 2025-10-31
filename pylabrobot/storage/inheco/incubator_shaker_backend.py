"""
Asynchronous PyLabRobot backend for INHECO Incubator/Shaker devices.

This module implements a fully asynchronous serial communication backend for
INHECO Incubator/Shaker instruments (e.g., INHECO MP/DWP with or without shaker).
It handles auto-detection via FTDI USB–VCP (VID:PID 0403:6001), command framing,
CRC generation, binary-safe parsing, and structured firmware error handling.

Features:
    • Auto-discovery of INHECO devices by VID:PID and DIP switch ID and stack index.
    • Complete command/response layer with legacy CRC-8 and async-safe I/O.
    • Structured firmware error reporting via InhecoError and contextual snapshotting.
    • High-level API for temperature control, drawer handling, and shaking functions.
    • Protocol-conformant parsing for EEPROM, sensor, and status commands.
    • Extensible architecture for future EEPROM and calibration operations.

Example:
    ```python
    import asyncio
    from inheco_incubator_shaker_backend import InhecoIncubatorShakerBackend

    async def main():
        inc = InhecoIncubatorShakerBackend(dip_switch_id=2)
        await inc.setup(verbose=True)
        await inc.start_temperature_control(37.0)
        temp = await inc.get_temperature()
        print(f"Current temperature: {temp:.1f} °C")
        await inc.stop_temperature_control()
        await inc.stop()

    asyncio.run(main())
    ```

Author:
    Developed for integration with the PyLabRobot automation framework.

License:
    MIT open-source license. See LICENSE file in the PyLabRobot repository.

"""

import asyncio
import logging
from typing import Dict, Literal, Optional
import sys

from pylabrobot.io.serial import Serial

try:
  import serial
  import serial.tools.list_ports

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e


class InhecoError(RuntimeError):
  """Represents an INHECO firmware-reported error."""

  def __init__(self, command: str, code: str, message: str):
    super().__init__(f"{command} failed with error {code}: {message}")
    self.command: str = command
    self.code: str = code
    self.message: str = message


_REF_FLAG_NAMES: Dict[int, str] = {
  # Heater (0–15) — names per manual’s heater flags table (subset shown here)
  0: "H_WARN_WarmUp_TIME",
  1: "H_WARN_BoostCoolDown_TIME",
  2: "H_WARN_StartState_LIMIT_Up_TEMP_S2",
  3: "H_WARN_StartState_LIMIT_Up_TEMP_S3",
  4: "H_WARN_StartStateBoost_LIMIT_UpDown_TEMP_S3",
  5: "H_WARN_StableState_LIMIT_UpDown_TEMP_S2",
  6: "H_WARN_StableState_LIMIT_UpDown_TEMP_S3",
  7: "H_WARN_DELTA_TEMP_S1_S2",
  8: "H_ERR_DELTA_TEMP_S1_S2",
  9: "H_WARN_StartStateBoost_LIMIT_UpDown_TEMP_S2",
  10: "H_WARN_WaitStable_LIMIT_TEMP_S1",
  11: "H_WARN_WaitStable_LIMIT_TEMP_S2",
  12: "H_WARN_WaitStable_LIMIT_TEMP_S3",
  13: "H_ERR_S2_NTC_NotConnected",
  14: "H_ERR_S3_NTC_NotConnected",
  15: "H_WARN_DELTA_TEMP_S1_S3",
  # Shaker (16–26) — names per manual’s shaker flag set (page 39)
  16: "S_WARN_MotorCurrentLimit",
  17: "S_WARN_TargetSpeedTimeout",
  18: "S_WARN_PositionTimeout",
  19: "S_ERR_MotorTemperatureLimit",
  20: "S_ERR_TargetSpeedDeviation",
  21: "S_ERR_HomeSensorTimeout",
  22: "S_ERR_MotorDriverFault",
  23: "S_ERR_EncoderSignalLost",
  24: "S_ERR_AmplitudeOutOfRange",
  25: "S_ERR_VibrationExcessive",
  26: "S_ERR_InternalTimeout",
  # 27–31 reserved
}

FIRMWARE_ERROR_MAP: Dict[int, str] = {
  0: "Msg Ok",
  1: "Reset detected",
  2: "Invalid command",
  3: "Invalid operand",
  4: "Protocol error",
  5: "Reserved",
  6: "Timeout from Device",
  7: "Device not initialized",
  8: "Command not executable",
  9: "Drawer not in end position",
  10: "Unexpected Labware Status",
  13: "Drawer DWP not perfectly closed (NTC not connected)",
  14: "Floor ID error",
  15: "Timeout sub device",
}


class InhecoIncubatorShakerStack:
  """Interface for INHECO Incubator Shaker stack machines.

  Handles:
    - USB/serial connection setup via VID/PID
    - DIP switch ID verification
    - Message framing, CRC generation
    - Complete async read/write of firmware responses
    - Binary-safe parsing and error mapping
  """

  def __init__(
    self,
    dip_switch_id: int = 2,
    port: Optional[str] = None,
    id_vendor: str = "0403",
    id_product: str = "6001",
    read_timeout: float = 2.0,
  ):
    self.dip_switch_id = dip_switch_id
    self.port_hint = port
    self.id_vendor = id_vendor
    self.id_product = id_product
    self.read_timeout = read_timeout

    self.io: Optional[Serial] = None
    self.port: Optional[str] = None

    # optional logging hook
    self.logger = logging.getLogger("pylabrobot.inheco.stack")

  # === Setup and teardown ===

  async def setup(self):
    """Discover INHECO device via VID:PID and verify DIP switch ID."""
    matching_ports = [
      p.device for p in serial.tools.list_ports.comports()
      if f"{self.id_vendor}:{self.id_product}" in (p.hwid or "")
    ]

    if not matching_ports:
      raise RuntimeError(
        f"No INHECO devices found (VID={self.id_vendor}, PID={self.id_product})."
      )

    # --- Port selection ---
    if self.port_hint:
      candidate = self.port_hint
      if candidate not in matching_ports:
        raise RuntimeError(
          f"Specified port {candidate} not found among INHECO devices "
          f"(VID={self.id_vendor}, PID={self.id_product})."
        )
    elif len(matching_ports) == 1:
      candidate = matching_ports[0]
    else:
      raise RuntimeError(
        f"Multiple INHECO devices detected with VID:PID {self.id_vendor}:{self.id_product}. "
        "Please specify the correct port address explicitly (e.g. /dev/ttyUSB0 or COM3)."
      )

    self.port = candidate

    # --- Establish serial connection ---
    self.io = Serial(
      port=self.port,
      baudrate=19200,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      timeout=0,
      write_timeout=1,
    )
    await self.io.setup()

    # --- Verify DIP switch ID via RTS ---
    probe = self._build_message("RTS", stack_index=0)
    await self.write(probe)
    resp = await self._read_full_response(timeout=1.0)

    expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF
    if not resp or expected_hdr not in resp:
      raise RuntimeError(
        f"Connected device on {self.port} did not respond with expected DIP switch ID "
        f"({self.dip_switch_id}). RTS handshake failed."
      )

    self._log(logging.INFO, f"Connected to INHECO device at {self.port} (DIP={self.dip_switch_id})")

  async def stop(self):
    """Close serial connection."""
    if self.io:
      await self.io.stop()
      self.io = None

  # === Logging utility ===

  def _log(self, level: int, msg: str, direction: Optional[str] = None):
    if direction:
      self.logger.log(level, f"[{direction}] {msg}")
    else:
      self.logger.log(level, msg)

  # === Low-level I/O ===

  async def write(self, data: bytes) -> None:
    """Write binary data to the serial device."""
    self._log(logging.DEBUG, f"→ {data.hex(' ')}")
    await self.io.write(data)

  async def _read_full_response(self, timeout: float) -> bytes:
    """Read a complete INHECO response frame asynchronously."""
    if not self.io:
      raise RuntimeError("Serial port not open.")

    loop = asyncio.get_event_loop()
    start = loop.time()
    buf = bytearray()
    expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF

    def has_complete_tail(b: bytearray) -> bool:
      # Valid frame ends with: [hdr][0x20-0x2F][0x60]
      return len(b) >= 3 and b[-1] == 0x60 and b[-3] == expected_hdr and 0x20 <= b[-2] <= 0x2F

    while True:
      chunk = await self.io.read(16)
      if chunk:
        buf.extend(chunk)
        self._log(logging.DEBUG, chunk.hex(" "), direction="←")
        if has_complete_tail(buf):
          return bytes(buf)

      if loop.time() - start > timeout:
        raise TimeoutError(f"Timed out waiting for complete response (so far: {buf.hex(' ')})")

      await asyncio.sleep(0.005)

  # === Encoding / Decoding ===

  def _crc8_legacy(self, data: bytearray) -> int:
    """Compute legacy CRC-8 used by INHECO devices."""
    crc = 0xA1
    for byte in data:
      d = byte
      for _ in range(8):
        if (d ^ crc) & 1:
          crc ^= 0x18
          crc >>= 1
          crc |= 0x80
        else:
          crc >>= 1
        d >>= 1
    return crc & 0xFF

  def _build_message(self, command: str, stack_index: int = 0) -> bytes:
    """Construct a full binary message with header and CRC."""
    if not (0 <= stack_index <= 5):
      raise ValueError("stack_index must be between 0 and 5")
    cmd = f"T0{stack_index}{command}".encode("ascii")
    length = len(cmd) + 3
    address = (0x30 + self.dip_switch_id) & 0xFF
    proto = (0xC0 + len(cmd)) & 0xFF
    message = bytearray([length, address, proto]) + cmd
    crc = self._crc8_legacy(message)
    return bytes(message + bytearray([crc]))

  def _is_report_command(self, command: str) -> bool:
    """Return True if command is a 'Report' type (starts with 'R')."""
    return command and command[0].upper() == "R"

  # === Response parsing ===

  def _parse_response_binary_safe(self, resp: bytes) -> dict:
    """Parse INHECO response frames safely (binary & multi-segment)."""
    if len(resp) < 3:
      raise ValueError("Incomplete response")

    expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF

    # Trim any leading junk before first valid header
    try:
      start_idx = resp.index(bytes([expected_hdr]))
      frame = resp[start_idx:]
    except ValueError:
      return {
        "device": None,
        "error_code": None,
        "ok": False,
        "data": "",
        "raw_data": resp,
      }

    # Validate tail
    if len(frame) < 3 or frame[-1] != 0x60:
      return {
        "device": expected_hdr - 0xB0,
        "error_code": None,
        "ok": False,
        "data": "",
        "raw_data": frame,
      }

    err_byte = frame[-2]
    err_code = err_byte - 0x20 if 0x20 <= err_byte <= 0x2F else None

    # Extract data between headers
    data_blocks = []
    i = 1  # start after first header
    while i < len(frame) - 3:
      try:
        next_hdr = frame.index(bytes([expected_hdr]), i)
      except ValueError:
        next_hdr = len(frame) - 3
      if next_hdr > i:
        data_blocks.append(frame[i:next_hdr])
      i = next_hdr + 1
      if next_hdr >= len(frame) - 3:
        break

    raw_data = b"".join(data_blocks)
    try:
      ascii_data = raw_data.decode("ascii").strip("\x00")
    except UnicodeDecodeError:
      ascii_data = raw_data.hex()

    return {
      "device": expected_hdr - 0xB0,
      "error_code": err_code,
      "ok": (err_code == 0),
      "data": ascii_data,
      "raw_data": raw_data,
    }

  def _is_error_tail(self, resp: bytes) -> bool:
    """Return True if the response ends in an explicit firmware error tail."""
    expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF
    return len(resp) >= 3 and resp.endswith(bytes([expected_hdr, 0x28, 0x60]))

  # === Command Layer ===

  async def send_command(
    self,
    command: str,
    delay: float = 0.2,
    read_timeout: Optional[float] = None,
    stack_index: int = 0,
  ) -> str:
    """Send a framed command and return parsed response or raise InhecoError."""
    msg = self._build_message(command, stack_index=stack_index)
    self._log(logging.DEBUG, f"SEND: {msg.hex(' ')}")
    await self.write(msg)
    await asyncio.sleep(delay)

    response = await self._read_full_response(timeout=read_timeout or self.read_timeout)
    if not response:
      raise TimeoutError(f"No response from device for command: {command}")

    if self._is_error_tail(response):
      tail_err = response[-2] - 0x20
      code = f"E{tail_err:02d}"
      message = FIRMWARE_ERROR_MAP.get(tail_err, "Unknown firmware error")
      raise InhecoError(command, code, message)

    parsed = self._parse_response_binary_safe(response)
    if not parsed["ok"]:
      code = f"E{parsed.get('error_code', 0):02d}"
      message = FIRMWARE_ERROR_MAP.get(parsed.get("error_code", 0), "Unknown firmware error")
      raise InhecoError(command, code, message)

    return parsed["data"]





class InhecoIncubatorShakerBackend:
  """
  Asynchronous backend for controlling an INHECO Incubator/Shaker via USB-VCP.

  Handles auto-detection, asynchronous serial I/O, command encoding/decoding,
  and structured firmware error reporting.

  Example:
      ```python
      incubator = InhecoIncubatorShakerBackend(dip_switch_id=2)
      await incubator.setup(verbose=True)
      await incubator.set_temperature(37.0)
      await incubator.stop()
      ```
  """

  # === Logging ===

  def _log(self, level: int, message: str, direction: Optional[str] = None):
    """
    Unified logging with a clear device tag and optional direction marker.
    direction: "→" for TX, "←" for RX, None for neutral.
    """
    prefix = f"[INHECO IncShak dip={self.dip_switch_id} stack={self.stack_index}]"
    if direction:
      prefix += f" {direction}"
    self.logger.log(level, f"{prefix} {message}")

  # === Constructor ===

  def __init__(
    self,
    port: Optional[str] = None,
    dip_switch_id: int = 2,
    stack_index: int = 0,
    write_timeout: float = 5.0,
    read_timeout: float = 10.0,
    logger: Optional[logging.Logger] = None,
  ) -> None:
    """Prepare backend instance. Serial link is opened asynchronously in `setup()`."""

    # Logger
    self.logger = logger or logging.getLogger("pylabrobot")
    self.logger.setLevel(logging.INFO)
    logging.getLogger("pylabrobot.io.serial").disabled = True

    # Core state
    self.dip_switch_id = dip_switch_id
    self.stack_index = stack_index
    # self.ser: Optional[Serial] = None
    self.write_timeout = write_timeout
    self.read_timeout = read_timeout

    # Defer port resolution to setup()
    self.port_hint = port

    # Cached state
    self.setup_finished = False
    self.is_initialized = False
    self.loading_tray = "unknown"
    self.incubator_type = "unknown"
    self.firmware_version = "unknown"
    self.max_temperature = 85.0  # safe default
    self.is_shaking = False

  # === Machine probing ===

  async def _probe_inheco_port(self, dev: str, stack_index: int) -> bool:
    """Attempt RTS handshake using pylabrobot.io.serial.Serial (async-safe)."""
    ser = Serial(
      port=dev,
      baudrate=19200,
      timeout=1,
      write_timeout=1,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
    )
    try:
      await ser.setup()
      msg = self._build_message("RTS", stack_index=stack_index)
      await ser.write(msg)
      data = await ser.read(64)
      expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF
      ok = bool(data and data[0] == expected_hdr)
      return ok
    except Exception as e:
      self._log(logging.DEBUG, f"Probe failed on {dev}: {e}")
      return False
    finally:
      try:
        await ser.stop()
      except Exception:
        pass

  # === Lifecycle ===

  async def setup(self, verbose: bool = False) -> None:
    """
    Detect and connect to the INHECO device.

    Probes available FTDI serial devices (VID:PID 0403:6001), validates DIP ID,
    and initializes communication.
    """
    VID = "0403"
    PID = "6001"

    # --- Explicit port path ---
    # If user gave a port, use it but verify DIP
    if self.port_hint is not None:
      candidate = self.port_hint
      self._log(
        logging.INFO,
        f"Using explicitly provided port: {candidate} (verifying DIP={self.dip_switch_id})",
      )
      ok = await self._probe_inheco_port(candidate, self.stack_index)
      if not ok:
        msg = (
          f"Device on {candidate} did not respond with expected DIP switch "
          f"ID={self.dip_switch_id}. Please verify the DIP switch setting."
        )
        self._log(logging.ERROR, msg)
        raise RuntimeError(msg)
      self.port = candidate

    # --- Auto-detect FTDI devices ---
    else:
      matching_ports = [
        p.device for p in serial.tools.list_ports.comports() if f"{VID}:{PID}" in (p.hwid or "")
      ]

      if not matching_ports:
        msg = f"No INHECO FTDI devices found (VID={VID}, PID={PID})."
        self._log(logging.ERROR, msg)
        raise RuntimeError(msg)

      if len(matching_ports) == 1:
        candidate = matching_ports[0]
        self._log(
          logging.INFO,
          f"Verifying single detected INHECO on {candidate} (DIP={self.dip_switch_id})...",
        )
        ok = await self._probe_inheco_port(candidate, self.stack_index)
        if not ok:
          msg = (
            f"Device on {candidate} did not respond with expected DIP switch "
            f"ID={self.dip_switch_id}. Please verify the DIP switch setting."
          )
          self._log(logging.ERROR, msg)
          raise RuntimeError(msg)
        self.port = candidate
        self._log(logging.INFO, f"Auto-selected {self.port} (DIP {self.dip_switch_id}).")

      else:
        self._log(
          logging.INFO,
          f"Multiple INHECO FTDI devices found ({len(matching_ports)}). "
          f"Probing for DIP={self.dip_switch_id}...",
        )
        responsive_ports = []
        for dev in matching_ports:
          if await self._probe_inheco_port(dev, self.stack_index):
            responsive_ports.append(dev)

        if not responsive_ports:
          msg = (
            f"No INHECO responded for dip_switch_id={self.dip_switch_id}, "
            f"stack_index={self.stack_index}. Verify DIP and connections."
          )
          self._log(logging.ERROR, msg)
          raise RuntimeError(msg)

        if len(responsive_ports) > 1:
          msg = (
            f"Multiple INHECO devices respond for dip_switch_id={self.dip_switch_id}: "
            f"{', '.join(responsive_ports)}"
          )
          self._log(logging.ERROR, msg)
          raise RuntimeError(msg)

        self.port = responsive_ports[0]
        self._log(logging.INFO, f"Auto-selected port {self.port} for DIP {self.dip_switch_id}.")

    # --- Create persistent async serial link with a verified port ---
    self.io = Serial(
      port=self.port,
      baudrate=19200,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      timeout=0,  # non-blocking
      write_timeout=self.write_timeout,
    )
    await self.io.setup()

    # --- Identify firmware and type ---
    self.firmware_version = await self.request_firmware_version()
    incubator_type = await self.request_incubator_type()
    serial_number = await self.request_serial_number()
    self.max_temperature = await self.request_maximum_allowed_temperature()

    msg = (
      f"Connected to INHECO {incubator_type} on {self.port}\n"
      f"Machine serial number: {serial_number}\n"
      f"Firmware version: {self.firmware_version}"
    )
    if verbose:
      print(msg)
    self._log(logging.INFO, msg)

    await self.initialize()
    self.setup_finished = True
    self.is_initialized = True

  async def stop(self) -> None:
    """Close the connection and free the serial port."""

    is_temp_control_enabled = await self.is_temperature_control_enabled()

    if is_temp_control_enabled:
        await self.stop_temperature_control()

    if self.is_shaking:
      await self.stop_shaking()

    await self.io.stop()
    self._log(logging.INFO, "Disconnected from INHECO Incubator/Shaker")

  # === Low-level I/O ===

  async def write(self, data: bytes) -> None:
    """Write binary data to the serial device."""
    self._log(logging.DEBUG, f"→ {data.hex(' ')}")
    await self.io.write(data)

  async def _read_full_response(self, timeout: float) -> bytes:
    """Read a complete INHECO response frame asynchronously."""
    if not self.io:
      raise RuntimeError("Serial port not open.")

    loop = asyncio.get_event_loop()
    start = loop.time()
    buf = bytearray()
    expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF

    def has_complete_tail(b: bytearray) -> bool:
      return len(b) >= 3 and b[-1] == 0x60 and b[-3] == expected_hdr and 0x20 <= b[-2] <= 0x2F

    while True:
      # Try to read up to 16 bytes at once — this limits per-byte log spam
      chunk = await self.io.read(16)
      if chunk:
        buf.extend(chunk)
        self._log(logging.DEBUG, chunk.hex(" "), direction="←")

        if has_complete_tail(buf):
          return bytes(buf)

      # Timeout protection
      if loop.time() - start > timeout:
        raise TimeoutError(f"Timed out waiting for complete response (so far: {buf.hex(' ')})")

      # brief pause to yield to event loop, avoid tight spin
      await asyncio.sleep(0.005)

  # === Encoding / Decoding ===

  def _crc8_legacy(self, data: bytearray) -> int:
    """Compute legacy CRC-8 used by INHECO devices."""  # TODO: check remaining combos: shaker[y/n] * size[mp/dwp]
    crc = 0xA1
    for byte in data:
      d = byte
      for _ in range(8):
        if (d ^ crc) & 1:
          crc ^= 0x18
          crc >>= 1
          crc |= 0x80
        else:
          crc >>= 1
        d >>= 1
    return crc & 0xFF

  def _build_message(self, command: str, stack_index: int = 0) -> bytes:
    """Construct a full binary message with header and CRC."""
    if not (0 <= stack_index <= 5):
      raise ValueError("stack_index must be between 0 and 5")
    cmd = f"T0{stack_index}{command}".encode("ascii")
    length = len(cmd) + 3
    address = (0x30 + self.dip_switch_id) & 0xFF
    proto = (0xC0 + len(cmd)) & 0xFF
    message = bytearray([length, address, proto]) + cmd
    crc = self._crc8_legacy(message)
    return bytes(message + bytearray([crc]))

  def _is_report_command(self, command: str) -> bool:
    """Return True if command is a 'Report' type (starts with 'R')."""

    return command and command[0].upper() == "R"

  # === Response parsing ===

  def _parse_response_binary_safe(self, resp: bytes) -> dict:
    """
    Parse INHECO response frames safely (binary & multi-segment).

    Handles:
      - Set/Action:  [B0+ID][20+err][60]
      - Report:      [B0+ID]<data>[B0+ID]... [B0+ID][20+err][60]
      - Also works when only a single [B0+ID] header precedes data.

    Returns:
      dict(
        device=int,
        error_code=int|None,
        ok=bool,
        data=str,
        raw_data=bytes
      )
    """
    if len(resp) < 3:
      raise ValueError("Incomplete response")

    expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF

    # --- Trim leading junk before the first valid header ---
    try:
      start_idx = resp.index(bytes([expected_hdr]))
      frame = resp[start_idx:]
    except ValueError:
      return {
        "device": None,
        "error_code": None,
        "ok": False,
        "data": "",
        "raw_data": resp,
      }

    # --- Validate tail (status section) ---
    if len(frame) < 3 or frame[-1] != 0x60:
      # No valid tail; may be bootloader or incomplete
      return {
        "device": expected_hdr - 0xB0,
        "error_code": None,
        "ok": False,
        "data": "",
        "raw_data": frame,
      }

    # Extract error code (0x20 + err)
    err_byte = frame[-2]
    err_code = err_byte - 0x20 if 0x20 <= err_byte <= 0x2F else None

    # --- Collect data between headers ---
    # Pattern: [hdr] <data> [hdr] <data> ... [hdr] [20+err][60]
    data_blocks = []
    i = 1  # start right after the first header

    while i < len(frame) - 3:
      try:
        next_hdr = frame.index(bytes([expected_hdr]), i)
      except ValueError:
        # No further header — consume until the status tail
        next_hdr = len(frame) - 3

      # Capture bytes between i and next_hdr
      if next_hdr > i:
        data_blocks.append(frame[i:next_hdr])

      i = next_hdr + 1
      if next_hdr >= len(frame) - 3:
        break

    # --- Assemble and decode ---
    raw_data = b"".join(data_blocks)

    try:
      ascii_data = raw_data.decode("ascii").strip("\x00")
    except UnicodeDecodeError:
      ascii_data = raw_data.hex()

    return {
      "device": expected_hdr - 0xB0,
      "error_code": err_code,
      "ok": (err_code == 0),
      "data": ascii_data,
      "raw_data": raw_data,
    }

  def _is_error_tail(self, resp: bytes) -> bool:
    expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF
    return len(resp) >= 3 and resp.endswith(bytes([expected_hdr, 0x28, 0x60]))

  # === Error Handling ===

  async def debug_error_registry(self):
    """
    Spec-compliant error snapshot using REE/REF/REP.
    - REE: init & labware status (0..3)
    - REF: 32-bit flag mask (bit set = active)
    - REP: heater flag parameters for bits {0..8, 13, 14, 15}
    """
    print("=== ERROR REGISTRY DEBUG ===")

    # Use your existing helpers that interpret REE
    try:
      is_init = await self.request_is_initialized()  # uses REE
      plate_known = await self.request_plate_status_known()  # uses REE
      print(f"REE → initialized={is_init}, plate_status_known={plate_known}")
    except Exception as e:
      print(f"REE query failed: {e}")

    try:
      ref_raw = await self.send_command("REF")  # returns 32-bit mask as decimal ASCII
      ref_mask = int(ref_raw.strip())
      print(f"REF (flags bitmask): {ref_mask} (0x{ref_mask:08X})")  # 32-bit mask.

      set_bits = [b for b in range(32) if (ref_mask >> b) & 1]
      if not set_bits:
        print(" - No flags set.")
      else:
        print(" - Active flags:")
        for b in set_bits:
          name = _REF_FLAG_NAMES.get(b, f"Flag{b}")
          print(f"   [{b:02d}] {name}")

          # REP supports heater selectors {0..8,13,14,15}.
          if b in {0, 1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 15}:
            try:
              param = await self.send_command(f"REP{b}")
              print(f"      → Parameter: {param}")
            except Exception as e:
              print(f"      → REP{b} failed: {e}")

    except Exception as e:
      print(f"REF/REP read failed: {e}")

    print("=== END ERROR REGISTRY DEBUG ===")

  async def _collect_error_context(self) -> dict:
    ctx = {"ree": None, "ref_mask": None, "flags": [], "rep_params": {}}
    try:
      is_init = await self.request_is_initialized()
      plate_known = await self.request_plate_status_known()
      ctx["ree"] = {"initialized": is_init, "plate_status_known": plate_known}
    except Exception:
      pass

    try:
      ref_raw = await self.send_command("REF")
      ref_mask = int(ref_raw.strip())
      ctx["ref_mask"] = ref_mask
      set_bits = [b for b in range(32) if (ref_mask >> b) & 1]
      for b in set_bits:
        ctx["flags"].append({"bit": b, "name": _REF_FLAG_NAMES.get(b, f"Flag{b}")})
        if b in {0, 1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 15}:
          try:
            param = await self.send_command(f"REP{b}")
            ctx["rep_params"][b] = param
          except Exception:
            pass
    except Exception:
      pass

    return ctx

  # === Command layer ===

  async def send_command(
    self,
    command: str,
    delay: float = 0.2,
    read_timeout: Optional[float] = None,
  ) -> str:
    """
    Send a command to the INHECO device and return its response.

    This method handles binary-safe I/O, firmware error mapping, and structured parsing.
    - Report commands (starting with 'R') return their ASCII/hex payload.
    - Action commands (starting with 'A') return an empty string by default,
      except for 'AQS' (self-test), which returns its raw binary payload bits.

    Args:
        command: Firmware command string, e.g. "RAT1", "STT370", or "AQS".
        delay: Delay between write and read, default 0.2 s.
        read_timeout: Optional custom read timeout in seconds.

    Returns:
        str | bytes: Parsed data field if available (string for report commands,
        raw bytes for AQS), or "" for simple action acknowledgments.

    Raises:
        TimeoutError: If no response was received in time.
        InhecoError: If firmware reports an error (non-zero error tail).
    """
    # === Construct and send message ===
    msg = self._build_message(command, stack_index=self.stack_index)
    self._log(logging.INFO, f"SENT MESSAGE: {msg}")

    await self.write(msg)
    await asyncio.sleep(delay)

    # === Read response frame ===
    response = await self._read_full_response(timeout=read_timeout or self.read_timeout)
    if not response:
      raise TimeoutError(f"No response from machine for command: {command}")

    # === Handle explicit firmware error tails ===
    if self._is_error_tail(response):
      tail_err = response[-2] - 0x20  # 0..15
      code = f"E{tail_err:02d}"
      message = FIRMWARE_ERROR_MAP.get(tail_err, "Unknown firmware error")

      # Optional: Collect diagnostic context for logs and error propagation
      ctx = {}
      try:
        ctx = await self._collect_error_context()
        self._log(logging.DEBUG, f"Error context: {ctx}")
      except Exception:
        pass

      err = InhecoError(command, code, message)
      err.context = ctx
      raise err

    # === Normal parse ===
    self._log(logging.INFO, f"RAW RESPONSE: {response}")
    parsed = self._parse_response_binary_safe(response)
    self._log(logging.DEBUG, f"PARSED RESPONSE: {parsed}")

    # === Handle normal report commands ===
    if self._is_report_command(command):
      if not parsed["ok"]:
        raise InhecoError(command, "E00", "Report returned non-OK status")
      return parsed["data"]

    # === Special-case: AQS returns binary self-test bits ===
    if command.startswith("AQS"):
      if not parsed["ok"]:
        raise InhecoError(command, "E00", "Self-test returned non-OK status")
      # Return raw data bytes if available, else the parsed ASCII field
      if parsed["raw_data"]:
        return parsed["raw_data"]
      return parsed["data"]

    # === Non-report command: verify success ===
    if not parsed["ok"]:
      code_num = parsed.get("error_code")
      if code_num is not None:
        code = f"E{code_num:02d}"
        message = FIRMWARE_ERROR_MAP.get(code_num, "Unknown firmware error")
      else:
        code = "E00"
        message = "Unknown error (no error code reported)"

      ctx = {}
      try:
        ctx = await self._collect_error_context()
        self._log(logging.DEBUG, f"Error context: {ctx}")
      except Exception:
        pass

      err = InhecoError(command, code, message)
      err.context = ctx
      raise err

    # === Return data (if any) or empty string ===
    return parsed["data"] if parsed["data"] else ""

  # === Public high-level API ===

  # Querying Machine State #
  async def request_firmware_version(self) -> str:
    """EEPROM request: Return the firmware version string."""
    return await self.send_command("RFV0")

  async def request_serial_number(self) -> str:
    """EEPROM request: Return the device serial number."""
    return await self.send_command("RFV2")

  async def request_last_calibration_date(self) -> str:
    """EEPROM request"""
    resp = await self.send_command("RCM")
    return resp[:10]

  async def request_machine_allocation(self, layer: int = 0) -> dict:
    """
    Report which device slots are occupied on a given layer (firmware 'RDAx,0').

    Args:
        layer (int): Layer index (0–7). Default = 0.

    Returns:
        dict:
            {
              "layer": int,
              "slot_mask": int,       # e.g. 7
              "slot_mask_bin": str,   # e.g. "0b0000000000000111"
              "slots_connected": list[int]  # e.g. [0, 1, 2]
            }

    Notes:
        Each bit in `slot_mask` represents one of 16 possible device slots:
        bit=1 means a device is connected; bit=0 means empty.
    """
    if not (0 <= layer <= 7):
      raise ValueError(f"Layer must be between 0 and 7, got {layer}")

    resp = await self.send_command(f"RDA{layer},0")
    slot_mask = int(resp.strip())
    slot_mask_bin = f"0b{slot_mask:016b}"

    slots_connected = [i for i in range(16) if (slot_mask >> i) & 1]

    return {
      "layer": layer,
      "slot_mask": slot_mask,
      "slot_mask_bin": slot_mask_bin,
      "slots_connected": slots_connected,
    }

  async def request_number_of_connected_machines(self, layer: int = 0) -> int:
    """
    Report the number of connected INHECO devices on a layer (RDAx,1).

    Args:
        layer (int): Layer index (0–7). Default = 0.

    Returns:
        int: Number of connected devices (0–16).

    Example:
        Response "3" → 3 connected devices on that layer.
    """
    if not (0 <= layer <= 7):
      raise ValueError(f"Layer must be 0–7, got {layer}")

    resp = await self.send_command(f"RDA{layer},1")
    return int(resp.strip())

  async def request_labware_detection_threshold(self) -> int:
    """EEPROM request"""
    resp = await self.send_command("RDM")
    return int(resp)

  async def request_incubator_type(self) -> str:
    """Return a descriptive string of the incubator/shaker configuration."""

    incubator_type_dict = {
      "0": "incubator_mp",  # no shaker
      "1": "incubator_shaker_mp",
      "2": "incubator_dwp",  # no shaker
      "3": "incubator_shaker_dwp",
    }
    resp = await self.send_command("RTS")
    ident = incubator_type_dict.get(resp, "unknown")
    self.incubator_type = ident
    return ident

  async def request_plate_in_incubator(self) -> bool:
    """Sensor command:"""
    resp = await self.send_command("RLW")
    return resp == "1"

  async def request_operation_time_in_hours(self) -> int:
    """EEPROM request"""
    resp = await self.send_command("RDC1")
    return int(resp)

  async def request_drawer_cycles_performed(self) -> int:
    """EEPROM request"""
    resp = await self.send_command("RDC2")
    return int(resp)

  async def request_is_initialized(self) -> bool:
    """EEPROM request"""
    resp = await self.send_command("REE")
    return resp in {"0", "2"}

  async def request_plate_status_known(self) -> bool:
    """EEPROM request"""
    resp = await self.send_command("REE")
    return resp in {"0", "1"}

  async def request_thermal_calibration_date(self) -> str:
    """EEPROM request: Query the date of the last thermal calibration.

    Returns:
        str: Calibration date in ISO format 'YYYY-MM-DD'.
    """
    resp = await self.send_command("RCD")
    date = resp.strip()
    if not date or len(date) != 10 or date.count("-") != 2:
      raise RuntimeError(f"Unexpected RCD response: {resp!r}")
    return date

  # TODO: Command Placeholders

  async def request_calibration_low(self, sensor: int, format: int) -> float:
    """Query the low temperature calibration point for a given sensor.

    Args:
        sensor (int): Sensor number (1, 2, or 3).
        format (int): 0 → AD-Value, 1 → Temperature [1/10 °C].

    Returns:
        float: Calibration low-point (AD value or °C).
    """
    # resp = await self.send_command(f"RCL{sensor},{format}")
    raise NotImplementedError("RCL (Report Calibration Low) not implemented yet.")

  async def request_calibration_high(self, sensor: int, format: int) -> float:
    """Query the high temperature calibration point for a given sensor.

    Args:
        sensor (int): Sensor number (1, 2, or 3).
        format (int): 0 → AD-Value, 1 → Temperature [1/10 °C].

    Returns:
        float: Calibration high-point (AD value or °C).
    """
    # resp = await self.send_command(f"RCH{sensor},{format}")
    raise NotImplementedError("RCH (Report Calibration High) not implemented yet.")

  async def request_whole_calibration_data(self, key: str) -> bytes:
    """Read the entire heater calibration dataset from the device EEPROM.

    Args:
        key (str): Access key (5 characters, required by firmware).

    Returns:
        bytes: Raw calibration data from EEPROM (~80 bytes).
    """
    # resp = await self.send_command(f"RWC{key}")
    raise NotImplementedError("RWC (Read Whole Calibration Data) not implemented yet.")

  async def request_proportionality_factor(self) -> int:
    """Query the proportionality factor for deep well plate incubators (firmware 'RPF' command).

    Returns:
        int: Proportionality factor (0–255). Lower values reduce room heating foil power.

    Notes:
        - Applicable only to DWP (Deep Well Plate) incubators.
        - Default value is typically 100.
    """
    # resp = await self.send_command("RPF")
    raise NotImplementedError("RPF (Report Proportionality Factor) not implemented yet.")

  async def set_max_allowed_device_temperature(self, key: str, temperature: int) -> None:
    """Set the maximum allowed device temperature (firmware 'SMT' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        temperature (int): Maximum allowed temperature in 1/10 °C (0–999).
                          Example: 345 → 34.5 °C.

    Notes:
        - Default limit is 850 (85.0 °C).
        - Firmware rejects invalid operands or missing key.
    """
    # await self.send_command(f"SMT{key},{temperature}")
    raise NotImplementedError("SMT (Set Max Allowed Device Temperature) not implemented yet.")

  async def set_pid_proportional_gain(self, key: str, value: int) -> None:
    """Set the PID controller's proportional gain (firmware 'SPP' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        value (int): Proportional gain value (0–999). Default = 150.
    """
    # await self.send_command(f"SPP{key},{value}")
    raise NotImplementedError("SPP (Set PID Proportional Gain) not implemented yet.")

  async def set_pid_integration_value(self, key: str, value: int) -> None:
    """Set the PID controller's integration value (firmware 'SPI' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        value (int): Integration value (0–999). Default = 100.
    """
    # await self.send_command(f"SPI{key},{value}")
    raise NotImplementedError("SPI (Set PID Integration Value) not implemented yet.")

  async def delete_counter(self, key: str, selector: int) -> None:
    """Delete an internal device counter (firmware 'SDC' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        selector (int): Counter selector → 1 = Operating time, 2 = Drawer counter.
    """
    # await self.send_command(f"SDC{key},{selector}")
    raise NotImplementedError("SDC (Set Delete Counter) not implemented yet.")

  async def set_boost_offset(self, offset: int) -> None:
    """Set the boost heating foil offset (firmware 'SBO' command).

    Args:
        offset (int): Offset value in 1/10 °C (-999–999). Example: 345 → 34.5 °C.
                      Reset to 0 after `AID` or `SHE0`.
    """
    # await self.send_command(f"SBO{offset}")
    raise NotImplementedError("SBO (Set Boost Offset) not implemented yet.")

  async def set_boost_time(self, time_s: int) -> None:
    """Set the boost heating foil time offset (firmware 'SBT' command).

    Args:
        time_s (int): Time offset in seconds (0–999). Reset to 0 after `AID` or `SHE0`.
    """
    # await self.send_command(f"SBT{time_s}")
    raise NotImplementedError("SBT (Set Boost Time) not implemented yet.")

  async def set_cooldown_time_factor(self, value: int) -> None:
    """Set the cool-down time evaluation factor (firmware 'SHK' command).

    Args:
        value (int): Cool-down evaluation factor (0–999). Default = 250.
    """
    # await self.send_command(f"SHK{value}")
    raise NotImplementedError("SHK (Set Cool-Down Time Evaluation Factor) not implemented yet.")

  async def set_heatup_time_factor(self, value: int) -> None:
    """Set the heat-up time evaluation factor (firmware 'SHH' command).

    Args:
        value (int): Heat-up evaluation factor (0–999). Default = 250.
    """
    # await self.send_command(f"SHH{value}")
    raise NotImplementedError("SHH (Set Heat-Up Time Evaluation Factor) not implemented yet.")

  async def set_heatup_offset(self, offset: int) -> None:
    """Set the heat-up temperature offset for the current plate type (firmware 'SHO' command).

    Args:
        offset (int): Offset temperature in 1/10 °C (0–150). Example: 121 → 12.1 °C.
                      Default = 0.
    """
    # await self.send_command(f"SHO{offset}")
    raise NotImplementedError("SHO (Set Heat-Up Offset) not implemented yet.")

  async def set_calibration_low(self, key: str, sensor1: int, sensor2: int, sensor3: int) -> None:
    """Set lower calibration temperature points for the three sensors (firmware 'SCL' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        sensor1 (int): Sensor 1 (differential) low point in 1/10 °C (0–999).
        sensor2 (int): Sensor 2 (main) low point in 1/10 °C (0–999).
        sensor3 (int): Sensor 3 (boost) low point in 1/10 °C (0–999).

    Notes:
        - Heater error flags remain stored but do not shut down the heater.
        - After setting SCL, the high calibration point (SCH) must be set next.
    """
    # await self.send_command(f"SCL{key},{sensor1},{sensor2},{sensor3}")
    raise NotImplementedError("SCL (Set Calibration Low) not implemented yet.")

  async def set_calibration_high(
    self,
    key: str,
    sensor1: int,
    sensor2: int,
    sensor3: int,
    date: str,
  ) -> None:
    """Set high calibration temperature points and calibration date (firmware 'SCH' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        sensor1 (int): Sensor 1 (main) high point in 1/10 °C (0–999).
        sensor2 (int): Sensor 2 (differential) high point in 1/10 °C (0–999).
        sensor3 (int): Sensor 3 (boost) high point in 1/10 °C (0–999).
        date (str): Calibration date in format 'YYYY-MM-DD'. Example: '2005-09-28'.

    Notes:
        - Executing SCH resets heater error flags and switches off the heater (normal behavior).
        - Always set SCL (low calibration) before SCH.
    """
    # await self.send_command(f"SCH{key},{sensor1},{sensor2},{sensor3},{date}")
    raise NotImplementedError("SCH (Set Calibration High and Date) not implemented yet.")

  async def reset_calibration_data(self, key: str) -> None:
    """Reset the temperature calibration data to firmware defaults (firmware 'SRC' command).

    Args:
        key (str): Access key (5-character secret required by firmware).

    Notes:
        - This clears the calibration line between the low and high points.
        - CAUTION: The device must be recalibrated afterward.
    """
    # await self.send_command(f"SRC{key}")
    raise NotImplementedError("SRC (Set Reset Calibration-Data) not implemented yet.")

  async def set_proportionality_factor(self, value: int) -> None:
    """Set the proportionality factor for deep well plate incubators (firmware 'SPF' command).

    Args:
        value (int): Proportionality factor (0–255). Default = 100.
                     Lower values reduce power of the room heating foil
                     relative to the main heating foil.
    """
    # await self.send_command(f"SPF{value}")
    raise NotImplementedError("SPF (Set Proportionality Factor) not implemented yet.")

  # # # Setup Requirement # # #

  async def initialize(self) -> str:
    """Perform device initialization (AID)."""
    return await self.send_command("AID")

  # # # Loading Tray Features # # #

  async def open(self) -> None:
    """Open the incubator door & move loading tray out."""
    await self.send_command("AOD")
    self.loading_tray = "open"

  async def close(self) -> None:
    """Move the loading tray in & close the incubator door."""
    await self.send_command("ACD")
    self.loading_tray = "closed"

  async def request_drawer_status(self) -> str:
    """Report the current drawer (loading tray) status.

    Returns:
        str: 'open' if the loading tray is open, 'closed' if closed.

    Notes:
        - Firmware response: '1' = open, '0' = closed.
    """
    resp = await self.send_command("RDS")
    if resp == "1":
      self.loading_tray = "open"
      return "open"
    elif resp == "0":
      self.loading_tray = "closed"
      return "closed"
    else:
      raise ValueError(f"Unexpected RDS response: {resp!r}")

  # TODOs: Drawer Placeholder Commands

  async def request_motor_power_clockwise(self) -> int:
    """Report the motor power (PWM) for clockwise rotation (firmware 'RPR' command).

    Returns:
        int: Motor power (0–255), where 255 = 100%.
    """
    # resp = await self.send_command("RPR")
    # return int(resp)
    raise NotImplementedError("RPR (Report Motor Power Clockwise) not implemented yet.")

  async def request_motor_power_anticlockwise(self) -> int:
    """Report the motor power (PWM) for anticlockwise rotation (firmware 'RPL' command).

    Returns:
        int: Motor power (0–255), where 255 = 100%.
    """
    # resp = await self.send_command("RPL")
    # return int(resp)
    raise NotImplementedError("RPL (Report Motor Power Anticlockwise) not implemented yet.")

  async def request_motor_current_limit_clockwise(self) -> int:
    """Report the motor current limitation for clockwise rotation (firmware 'RGR' command).

    Returns:
        int: Current limit (0–255), where 255 = 450 mA.
    """
    # resp = await self.send_command("RGR")
    # return int(resp)
    raise NotImplementedError("RGR (Report Motor Current Limit Clockwise) not implemented yet.")

  async def request_motor_current_limit_anticlockwise(self) -> int:
    """Report the motor current limitation for anticlockwise rotation (firmware 'RGL' command).

    Returns:
        int: Current limit (0–255), where 255 = 450 mA.
    """
    # resp = await self.send_command("RGL")
    # return int(resp)
    raise NotImplementedError("RGL (Report Motor Current Limit Anticlockwise) not implemented yet.")

  async def set_motor_power_clockwise(self, key: str, power: int) -> None:
    """Set the motor power (PWM) for clockwise rotation (firmware 'SPR' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        power (int): Power level (0–255). Default = 250.
                     0 = no power, 255 = maximum power.
    """
    # await self.send_command(f"SPR{key},{power}")
    raise NotImplementedError("SPR (Set Motor Power Clockwise) not implemented yet.")

  async def set_motor_power_anticlockwise(self, key: str, power: int) -> None:
    """Set the motor power (PWM) for anticlockwise rotation (firmware 'SPL' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        power (int): Power level (0–255). Default = 250.
                     0 = no power, 255 = maximum power.
    """
    # await self.send_command(f"SPL{key},{power}")
    raise NotImplementedError("SPL (Set Motor Power Anticlockwise) not implemented yet.")

  async def set_motor_current_limit_clockwise(self, key: str, current: int) -> None:
    """Set the motor current limitation for clockwise rotation (firmware 'SGR' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        current (int): Current limit (0–255). Default = 35.
                       0 = minimum power limit, 255 = maximum power limit.
    """
    # await self.send_command(f"SGR{key},{current}")
    raise NotImplementedError("SGR (Set Motor Current Limit Clockwise) not implemented yet.")

  async def set_motor_current_limit_anticlockwise(self, key: str, current: int) -> None:
    """Set the motor current limitation for anticlockwise rotation (firmware 'SGL' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        current (int): Current limit (0–255). Default = 35.
                       0 = minimum power limit, 255 = maximum power limit.
    """
    # await self.send_command(f"SGL{key},{current}")
    raise NotImplementedError("SGL (Set Motor Current Limit Anticlockwise) not implemented yet.")

  # # # Temperature Features # # #

  async def start_temperature_control(self, temperature: float) -> None:
    """Set and activate the target incubation temperature (°C).

    The device begins active heating toward the target temperature.
    Passive cooling (firmware default) may occur automatically if the
    target temperature is below ambient, depending on environmental conditions.
    """

    assert temperature < self.max_temperature, (
      "Target temperature must be below max temperature of the incubator, i.e. "
      f"{self.max_temperature}C, target temperature given = {temperature}"
    )

    target = round(temperature * 10)
    await self.send_command(f"STT{target}")  # Store target temperature
    await self.send_command("SHE1")  # Enable temperature regulatio

  async def stop_temperature_control(self) -> None:
    """Stop active temperature regulation.

    Disables the incubator’s heating control loop.
    The previously set target temperature remains stored in the
    device’s memory but is no longer actively maintained.
    The incubator will passively drift toward ambient temperature.
    """
    await self.send_command("SHE0")

  async def get_temperature(
    self,
    sensor: Literal["mean", "main", "dif", "boost"] = "main",
  ) -> float:
    """Return current measured temperature in °C."""

    sensor_mapping = {
      "mean": [1, 2, 3],
      "main": [1],
      "dif": [2],
      "boost": [3],
    }
    vals = []
    for idx in sensor_mapping[sensor]:
      val = await self.send_command(f"RAT{idx}", read_timeout=60)
      vals.append(int(val) / 10.0)
    return round(sum(vals) / len(vals), 2)

  async def request_target_temperature(
    self,
  ) -> float:
    """Return target temperature in °C."""

    resp = await self.send_command("RTT")

    return int(resp) / 10

  async def is_temperature_control_enabled(self) -> bool:
    """
    Return True if active temperature control is enabled (RHE=1 or 2),
    False if control is off (RHE=0).

    Note:
        - RHE=1 → control loop on
        - RHE=2 → control + booster on
        - RHE=0 → control loop off (passive equilibrium)
    """
    resp = await self.send_command("RHE")
    return resp.strip() in {"1", "2"}

  async def request_pid_controller_coefficients(self) -> tuple[float, float]:
    """
    Query the current PID controller coefficients.

    Returns:
        (P, I): tuple of floats
            - P: proportional gain (selector 1)
            - I: integration value (selector 2; 0 = integration off)
    """
    p_resp = await self.send_command("RPC1")
    i_resp = await self.send_command("RPC2")

    try:
      p = float(p_resp.strip())
      i = float(i_resp.strip())
    except ValueError:
      raise RuntimeError(f"Unexpected RPC response(s): P={p_resp!r}, I={i_resp!r}")

    return p, i

  async def request_maximum_allowed_temperature(self, measured: bool = False) -> float:
    """
    Query the maximum allowed or maximum measured device temperature (in °C).

    Args:
        measured (bool):
            - False → report configured maximum allowed temperature (default)
            - True  → report maximum measured temperature since last reset

    Returns:
        float: Temperature in °C (value / 10)
    """
    selector = "1" if measured else ""
    resp = await self.send_command(f"RMT{selector}")
    try:
      return int(resp.strip()) / 10.0
    except ValueError:
      raise RuntimeError(f"Unexpected RMT response: {resp!r}")

  async def request_delta_temperature(self) -> float:
    """
    Query the absolute temperature difference between target and actual plate temperature.

    Returns:
        float: Delta temperature in °C (positive if below target, negative if above target).

    Notes:
        - Reported in 1/10 °C.
        - Negative values indicate the plate is warmer than the target.
    """
    resp = await self.send_command("RDT")
    try:
      return int(resp.strip()) / 10.0
    except ValueError:
      raise RuntimeError(f"Unexpected RDT response: {resp!r}")


  async def wait_for_temperature(
    self,
    *,
    sensor: Literal["main", "dif", "boost", "mean"] = "main",
    tolerance: float = 0.2,
    interval_s: float = 0.5,
    timeout_s: Optional[float] = 600.0,
    show_progress_bar: bool = False,
) -> float:
    """
    Wait asynchronously until the target temperature is reached.

    Args:
        sensor: Temperature sensor to monitor ("main", "dif", "boost", or "mean").
        tolerance: Acceptable difference (in °C) between current and target temperature.
        interval_s: Polling interval in seconds. Default = 0.5 s.
        timeout_s: Maximum time to wait in seconds. None disables timeout. Default = 600 s.
        show_progress_bar: If True, display a dynamic ASCII progress bar in stdout. Default False.

    Returns:
        Final measured temperature in °C once within tolerance.

    Raises:
        TimeoutError: If target not reached within `timeout_s`.
        ValueError: If temperature control is not enabled or no valid target returned.
    """
    target_temp = await self.request_target_temperature()
    if target_temp is None:
        raise ValueError("Device did not return a valid target temperature.")

    temperature_control_enabled = await self.is_temperature_control_enabled()
    if not temperature_control_enabled:
        raise ValueError(
            f"Temperature control is not enabled on the machine ({self.incubator_type})."
        )

    start_time = asyncio.get_event_loop().time()
    first_temp = await self.get_temperature(sensor=sensor)
    initial_diff = abs(first_temp - target_temp)
    bar_width = 40

    if show_progress_bar:
        print(f"Waiting for target temperature {target_temp:.2f} °C...\n")

    while True:
        current_temp = await self.get_temperature(sensor=sensor)
        diff = abs(current_temp - target_temp)

        # Compute normalized progress (1 = done)
        progress = 1.0 - min(diff / max(initial_diff, 1e-6), 1.0)
        filled = int(bar_width * progress)
        bar = "█" * filled + "-" * (bar_width - filled)

        if show_progress_bar:
            sys.stdout.write(
                f"\r[{bar}] {current_temp:.2f} °C "
                f"(Δ={diff:.2f} °C, target={target_temp:.2f} °C)"
            )
            sys.stdout.flush()

        if diff <= tolerance:
            if show_progress_bar:
                sys.stdout.write("\n✅ Target temperature reached.\n")
                sys.stdout.flush()

            self._log(logging.INFO, f"Target temperature reached ({current_temp:.2f} °C).")
            return current_temp

        if timeout_s is not None:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout_s:
                if show_progress_bar:
                    sys.stdout.write("\n❌ Timeout waiting for temperature.\n")
                    sys.stdout.flush()

                raise TimeoutError(
                    f"Timeout after {timeout_s:.1f}s: "
                    f"temperature {current_temp:.2f} °C "
                    f"did not reach target {target_temp:.2f} °C ±{tolerance:.2f} °C."
                )

        await asyncio.sleep(interval_s)


  # # # Shaking Features # # #

  def requires_incubator_shaker(func):
    """Decorator ensuring that the connected machine is a shaker-capable model."""

    async def wrapper(self, *args, **kwargs):
      incubator_type = getattr(self, "incubator_type", None)

      if incubator_type in (None, "unknown"):
        try:
          incubator_type = await self.request_incubator_type()
        except Exception as e:
          raise RuntimeError(
            f"Cannot determine incubator type before calling {func.__name__}(): {e}"
          )

      if "shaker" not in incubator_type:
        raise RuntimeError(
          f"{func.__name__}() requires a shaker-capable model " f"(got {incubator_type!r})."
        )

      return await func(self, *args, **kwargs)

    return wrapper

  @requires_incubator_shaker
  async def request_shaker_frequency_x(self, selector: int = 0) -> float:
    """Read the set or actual shaker frequency in the X-direction.

    Args:
        selector (int):
            0 = to-be-set frequency,
            1 = actual frequency.
            Default = 0.

    Returns:
        float: Frequency in Hz.
    """
    if selector not in (0, 1):
      raise ValueError(f"Selector must be 0 or 1, got {selector}")
    resp = await self.send_command(f"RFX{selector}")
    return float(resp) / 10.0  # firmware reports in 1/10 Hz

  @requires_incubator_shaker
  async def request_shaker_frequency_y(self, selector: int = 0) -> float:
    """Read the set or actual shaker frequency in the Y-direction.

    Args:
        selector (int):
            0 = to-be-set frequency,
            1 = actual frequency.
            Default = 0.

    Returns:
        float: Frequency in Hz.
    """
    if selector not in (0, 1):
      raise ValueError(f"Selector must be 0 or 1, got {selector}")
    resp = await self.send_command(f"RFY{selector}")
    return float(resp) / 10.0  # firmware reports in 1/10 Hz

  @requires_incubator_shaker
  async def request_shaker_amplitude_x(self, selector: int = 0) -> float:
    """Read the set, actual, or static shaker amplitude in the X-direction.

    Args:
        selector (int):
            0 = set amplitude,
            1 = actual amplitude,
            2 = static distance from middle.
            Default = 0.

    Returns:
        float: Amplitude in millimeters (mm).
    """
    if selector not in (0, 1, 2):
      raise ValueError(f"Selector must be 0, 1, or 2, got {selector}")
    resp = await self.send_command(f"RAX{selector}")
    return float(resp) / 10.0  # firmware reports in 1/10 mm

  @requires_incubator_shaker
  async def request_shaker_amplitude_y(self, selector: int = 0) -> float:
    """Read the set, actual, or static shaker amplitude in the Y-direction.

    Args:
        selector (int):
            0 = set amplitude,
            1 = actual amplitude,
            2 = static distance from middle.
            Default = 0.

    Returns:
        float: Amplitude in millimeters (mm).
    """
    if selector not in (0, 1, 2):
      raise ValueError(f"Selector must be 0, 1, or 2, got {selector}")
    resp = await self.send_command(f"RAY{selector}")
    return float(resp) / 10.0  # firmware reports in 1/10 mm

  @requires_incubator_shaker
  async def is_shaking_enabled(self) -> bool:
    """Return True if the shaker is currently enabled or still decelerating.

    The firmware returns:
        0 → shaker off
        1 → shaker on
        2 → shaker switched off but still moving

    Returns:
        bool: True if the shaker is active or still moving (status 1 or 2),
            False if fully stopped (status 0).
    """
    resp = await self.send_command("RSE")
    try:
      status = int(resp)
    except ValueError:
      raise InhecoError("RSE", "E00", f"Unexpected response: {resp!r}")

    if status not in (0, 1, 2):
      raise InhecoError("RSE", "E00", f"Invalid shaker status value: {status}")

    answer = status in (1, 2) # TODO: discuss whether 2 should count as "shaking"

    if answer:
      self.is_shaking = True
    else:
      self.is_shaking = False

    return answer

  @requires_incubator_shaker
  async def set_shaker_parameters(
    self,
    amplitude_x: float,
    amplitude_y: float,
    frequency_x: float,
    frequency_y: float,
    phase_shift: float,
  ) -> None:
    """Set shaker parameters for both X and Y axes in a single command (firmware 'SSP').

    This combines the functionality of the individual SAX, SAY, SFX, SFY, and SPS commands.

    Args:
        amplitude_x (float):  Amplitude on the X-axis in mm (0.0–3.0 mm, corresponds to 0–30 in firmware units).
        amplitude_y (float):  Amplitude on the Y-axis in mm (0.0–3.0 mm, corresponds to 0–30 in firmware units).
        frequency_x (float):  Frequency on the X-axis in Hz (6.6–30.0 Hz, corresponds to 66–300 in firmware units).
        frequency_y (float):  Frequency on the Y-axis in Hz (6.6–30.0 Hz, corresponds to 66–300 in firmware units).
        phase_shift (float):  Phase shift between X and Y axes in degrees (0–360°).

    Notes:
        - This command simplifies coordinated shaker setup.
        - All arguments are automatically converted to the firmware’s expected integer scaling.
          (mm → ×10; Hz → ×10; ° left unscaled)
        - The firmware returns an acknowledgment frame on success.

    Raises:
        ValueError: If any parameter is outside its valid range.
        InhecoError: If the device reports an error or rejects the command.
    """
    # --- Validation ---
    if not (0.0 <= amplitude_x <= 3.0):
      raise ValueError(f"Amplitude X must be between 0.0 and 3.0 mm, got {amplitude_x}")
    if not (0.0 <= amplitude_y <= 3.0):
      raise ValueError(f"Amplitude Y must be between 0.0 and 3.0 mm, got {amplitude_y}")
    if not (6.6 <= frequency_x <= 30.0):
      raise ValueError(f"Frequency X must be between 6.6 and 30.0 Hz, got {frequency_x}")
    if not (6.6 <= frequency_y <= 30.0):
      raise ValueError(f"Frequency Y must be between 6.6 and 30.0 Hz, got {frequency_y}")
    if not (0.0 <= phase_shift <= 360.0):
      raise ValueError(f"Phase shift must be between 0° and 360°, got {phase_shift}")

    # --- Convert to firmware units ---
    amp_x_fw = round(amplitude_x * 10)
    amp_y_fw = round(amplitude_y * 10)
    freq_x_fw = round(frequency_x * 10)
    freq_y_fw = round(frequency_y * 10)
    phase_fw = round(phase_shift)

    # --- Build and send command ---
    cmd = f"SSP{amp_x_fw},{amp_y_fw},{freq_x_fw},{freq_y_fw},{phase_fw}"
    await self.send_command(cmd)

  def _mm_to_fw(self, mm: float) -> int:
    """Convert mm → firmware units (1/10 mm).

    Valid range: 0.0–3.0 mm (→ 0–30 in firmware).
    Raises ValueError if out of range.
    """
    if not (0.0 <= mm <= 3.0):
      raise ValueError(f"Amplitude must be between 0.0 and 3.0 mm, got {mm}")
    return int(round(mm * 10))

  def _rpm_to_fw_hz10(self, rpm: float) -> int:
    """Convert RPM → firmware Hz·10 units (validated).

    396–1800 RPM ↔ 6.6–30.0 Hz ↔ 66–300 in firmware.
    """
    if not (396 <= rpm <= 1800):
      raise ValueError(f"RPM must be between 396 and 1800, got {rpm}")
    return int(round((rpm / 60.0) * 10))

  def _hz_to_fw_hz10(self, hz: float) -> int:
    """Convert Hz → firmware Hz·10 units (validated)."""
    if not (6.6 <= hz <= 30.0):
      raise ValueError(f"Frequency must be between 6.6 and 30.0 Hz, got {hz}")
    return int(round(hz * 10))

  def _validate_hz_or_rpm(self, frequency_hz: Optional[float], rpm: Optional[float]) -> None:
    """Ensure exactly one of frequency_hz or rpm is provided."""
    if (frequency_hz is None) == (rpm is None):
      raise ValueError("Provide exactly one of frequency_hz or rpm.")

  def _phase_or_default(self, phase_deg: Optional[float], default: int) -> int:
    """Return integer phase or default (0–360°)."""
    p = default if phase_deg is None else int(round(phase_deg))
    if not (0 <= p <= 360):
      raise ValueError(f"Phase must be 0–360°, got {p}")
    return p

  def _fw_freq_pair(self, frequency_hz: Optional[float], rpm: Optional[float]) -> tuple[int, int]:
    """Return validated firmware frequency pair (Hz·10, Hz·10)."""
    f = self._hz_to_fw_hz10(frequency_hz) if frequency_hz is not None else self._rpm_to_fw_hz10(rpm)
    return (f, f)

  def _fw_amp_pair_linear_x(self, ax_mm: float) -> tuple[int, int]:
    return (self._mm_to_fw(ax_mm), 0)

  def _fw_amp_pair_linear_y(self, ay_mm: float) -> tuple[int, int]:
    return (0, self._mm_to_fw(ay_mm))

  def _fw_amp_pair_xy(self, ax_mm: float, ay_mm: float) -> tuple[int, int]:
    return (self._mm_to_fw(ax_mm), self._mm_to_fw(ay_mm))

  @requires_incubator_shaker
  async def set_shaker_pattern(
    self,
    *,
    pattern: Literal["linear_x", "linear_y", "orbital", "elliptical", "figure_eight"],
    frequency_hz: Optional[float] = None,
    rpm: Optional[float] = None,
    amplitude_x_mm: Optional[float] = None,
    amplitude_y_mm: Optional[float] = None,
    phase_deg: Optional[float] = None,
  ) -> None:
    """Set the shaker motion pattern and parameters (without enabling motion).

    Patterns:
      - linear_x:    motion along X only.
      - linear_y:    motion along Y only.
      - orbital:     circular motion (equal amplitudes on both axes, 90° phase).
      - elliptical:  elliptical motion (unequal amplitudes, 90° phase).
      - figure_eight: double-loop motion (any amplitudes, 180° phase).
    """
    self._validate_hz_or_rpm(frequency_hz, rpm)
    fx, fy = self._fw_freq_pair(frequency_hz, rpm)

    if pattern == "linear_x":
      if amplitude_x_mm is None:
        raise ValueError("linear_x requires amplitude_x_mm.")
      ax, ay = self._fw_amp_pair_linear_x(amplitude_x_mm)
      phase = self._phase_or_default(phase_deg, 0)

    elif pattern == "linear_y":
      if amplitude_y_mm is None:
        raise ValueError("linear_y requires amplitude_y_mm.")
      ax, ay = self._fw_amp_pair_linear_y(amplitude_y_mm)
      phase = self._phase_or_default(phase_deg, 0)

    elif pattern == "orbital":
      # --- orbital: equal amplitudes, 90° phase ---
      if amplitude_x_mm is None or amplitude_y_mm is None:
        raise ValueError("orbital requires both amplitude_x_mm and amplitude_y_mm.")
      if abs(amplitude_x_mm - amplitude_y_mm) > 1e-6:
        raise ValueError(
          f"Orbital motion requires equal amplitudes on X and Y "
          f"(got {amplitude_x_mm} mm vs {amplitude_y_mm} mm). "
          f"Use pattern='elliptical' instead."
        )
      ax, ay = self._fw_amp_pair_xy(amplitude_x_mm, amplitude_y_mm)
      phase = self._phase_or_default(phase_deg, 90)

    elif pattern == "elliptical":
      # --- elliptical: differing amplitudes, 90° phase ---
      ax_mm = amplitude_x_mm if amplitude_x_mm is not None else 2.5
      ay_mm = amplitude_y_mm if amplitude_y_mm is not None else 2.0
      ax, ay = self._fw_amp_pair_xy(ax_mm, ay_mm)
      phase = self._phase_or_default(phase_deg, 90)

    elif pattern == "figure_eight":
        # --- true figure eight: fx:fy = 1:2, phase = 90° ---
        ax_mm = amplitude_x_mm if amplitude_x_mm is not None else 2.5
        ay_mm = amplitude_y_mm if amplitude_y_mm is not None else 2.5
        ax, ay = self._fw_amp_pair_xy(ax_mm, ay_mm)

        # base frequency (default 10 Hz if not given)
        base_hz = frequency_hz if frequency_hz is not None else (rpm / 60.0 if rpm else 10.0)
        fx = self._hz_to_fw_hz10(base_hz)
        fy = self._hz_to_fw_hz10(base_hz * 2)

        phase = self._phase_or_default(phase_deg, 90)

    else:
      raise ValueError(f"Unknown pattern: {pattern}")

    await self.send_command(f"SSP{ax},{ay},{fx},{fy},{phase}")


  @requires_incubator_shaker
  async def set_shaker_status(self, enabled: bool) -> None:
    """Enable or disable the shaker (ASEND always used when enabled)."""
    await self.send_command("ASEND" if enabled else "ASE0")

  @requires_incubator_shaker
  async def shake(
    self,
    *,
    pattern: Literal["linear_x", "linear_y", "orbital", "elliptical", "figure_eight"] = "orbital",
    rpm: Optional[float] = None,
    frequency_hz: Optional[float] = None,
    amplitude_x_mm: float = 3.0,
    amplitude_y_mm: float = 3.0,
    phase_deg: Optional[float] = None,
  ) -> None:
    """
    Configure and start shaking with the given motion pattern.

    This command safely updates shaker parameters (frequency, amplitude, phase)
    and starts motion using `ASEND` (no labware detection). If the shaker is
    already running, it is first stopped and reinitialized before applying new
    parameters—required because the firmware only latches `SSP` settings when
    the shaker transitions from idle to active.

    Args:
        pattern: Motion pattern: `"linear_x"`, `"linear_y"`, `"orbital"`,
                 `"elliptical"`, or `"figure_eight"`.
        rpm: Rotational speed (396–1800 RPM). Mutually exclusive with `frequency_hz`.
        frequency_hz: Oscillation frequency (6.6–30.0 Hz). Mutually exclusive with `rpm`.
        amplitude_x_mm: X-axis amplitude in mm (0.0–3.0 mm).
        amplitude_y_mm: Y-axis amplitude in mm (0.0–3.0 mm).
        phase_deg: Optional phase offset between X and Y axes (0–360°).

    Behavior:
        - Stops the shaker if active, waits briefly, applies the new pattern,
          and restarts shaking.
        - Ensures consistent parameter changes and prevents ignored SSP updates.

    Raises:
        ValueError: If parameter ranges or combinations are invalid.
        InhecoError: If the device rejects the command or is not ready.
    """

    is_shaking = await self.is_shaking_enabled()
    if is_shaking:
      await self.stop_shaking()
      await asyncio.sleep(0.5)  # brief pause for firmware to settle

    await self.set_shaker_pattern(
      pattern=pattern,
      rpm=rpm,
      frequency_hz=frequency_hz,
      amplitude_x_mm=amplitude_x_mm,
      amplitude_y_mm=amplitude_y_mm,
      phase_deg=phase_deg,
    )
    await self.set_shaker_status(True)

  # TODO: expose direction argument -> clockwise / counterclockwise for rotating shaking patterns

  @requires_incubator_shaker
  async def stop_shaking(self) -> None:
    """Stop shaker (ASE0)."""
    await self.set_shaker_status(False)

  @requires_incubator_shaker
  async def request_shaker_phase_shift(self, selector: int = 0) -> float:
    """Read the set or actual phase shift between X and Y shaker drives (firmware 'RPS' command).

    Args:
        selector (int):
            0 = currently set phase shift,
            1 = actual phase shift.
            Default = 0.

    Returns:
        float: Phase shift in degrees [°].
               Returns 12345.0 if the shaker has not reached a stable state or
               if phase shift calculation is invalid due to too-small amplitudes
               (< 1 mm on either axis).
    """
    if selector not in (0, 1):
      raise ValueError(f"Selector must be 0 or 1, got {selector}")

    resp = await self.send_command(f"RPS{selector}")
    return float(resp)

  # TODO: Shaking Command Placeholders

  @requires_incubator_shaker
  async def request_shaker_calibration_value(self, position: int, selector: int = 0) -> float:
    """Read shaker calibration or adjustment values of the Hall-effect sensors (firmware 'RSC' command).

    Args:
        position (int):
            Position index (0–11) identifying which calibration point to read, e.g.:
              0 = Center X, 1 = X-H (rear), 2 = X-L (front),
              3 = Center Y, 4 = Y-H (right), 5 = Y-L (left), etc.
        selector (int):
            0 = Hall-sensor raw value,
            1 = Freedom of movement [1/100 mm],
            2 = Frequency correction factor [Hz]·10.
            Default = 0.

    Returns:
        float: Numeric value from the selected calibration entry.
               Values for selector 1 are in 1/100 mm, selector 2 in 1/10 Hz.

    Raises:
        ValueError: If position or selector are out of valid range.
        NotImplementedError: Placeholder for future implementation.
    """
    if not (0 <= position <= 11):
      raise ValueError(f"Position must be between 0 and 11, got {position}")
    if selector not in (0, 1, 2):
      raise ValueError(f"Selector must be 0, 1, or 2, got {selector}")

    # resp = await self.send_command(f"RSC{position},{selector}")
    # return float(resp)
    raise NotImplementedError("RSC (Read Shaker Calibration Values) not implemented yet.")

  @requires_incubator_shaker
  async def read_whole_shaker_calibration_data(self, key: str) -> str:
    """Read all shaker calibration data from the shaker MCU EEPROM and copy it into the communication MCU
    (firmware 'RWJ' command).

    Args:
        key (str): Access key (5-character secret required by firmware).

    Returns:
        str: Raw EEPROM data dump as returned by the firmware.

    Raises:
        NotImplementedError: Placeholder for future implementation.
    """
    # resp = await self.send_command(f"RWJ{key}")
    # return resp
    raise NotImplementedError("RWJ (Read Whole Shaker Adjustment Data) not implemented yet.")

  @requires_incubator_shaker
  async def set_shaker_calibration_value(
    self,
    key: str,
    position: int,
    value: int,
  ) -> None:
    """
    Set shaker calibration value for a specific Hall-effect sensor (firmware 'SSC' command).

    Args:
        key (str): Access key (5-character secret required by firmware).
        position (int): Calibration position selector.
            - 0 = Center position
            - 1 = X-H (rear, X high)
            - 2 = X-L (front, X low)
            - 4 = Y-H (right, Y high)
            - 5 = Y-L (left, Y low)
            - 6 = Adjustment delta of X and Y values, and store
            - 8 = Correction factor for X-axis (low)
            - 9 = Correction factor for Y-axis (low)
            - 10 = Correction factor for X-axis (high)
            - 11 = Correction factor for Y-axis (high)
        value (int): Adjustment value in 1/100 mm (0–400).

    Notes:
        - Executes `SSC<key>,<position>,<value>`.
        - Does not switch off the shaker when `position == 0`.
        - After power-up, stored shaker error flags can again disable the shaker (normal behavior).

    Raises:
        NotImplementedError: Command placeholder, implementation pending.
    """
    # await self.send_command(f"SSC{key},{position},{value}")
    raise NotImplementedError("SSC (Set Shaker Calibration Values) not implemented yet.")

  # # # Self-Test # # #

  async def perform_self_test(self, read_timeout: int = 500) -> str:
    """Execute the internal self-test routine."""

    plate_in = await self.send_command("RLW")
    if plate_in == "1":
      raise ValueError("Self-test requires an empty incubator.")

    elif self.loading_tray == "open":
      raise ValueError("Self-test requires a closed loading tray.")

    return await self.send_command("AQS", read_timeout=read_timeout)
