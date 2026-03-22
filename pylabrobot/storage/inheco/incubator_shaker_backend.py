"""
Backend for Inheco Incubator Shaker Stack machine.

This module implements a fully asynchronous serial communication backend for
Inheco Incubator/Shaker instruments (e.g., Inheco MP/DWP with or without shaker).

Features:
- Auto-discovery of Inheco devices by VID:PID (error if more than one is found).
- Validation of DIP switch ID.
- Automatic identification of units in stack.
- Complete command/response layer with legacy CRC-8 and async-safe I/O.
- Structured firmware error reporting via InhecoError and contextual snapshotting.
- High-level API for temperature control, drawer handling, and shaking functions.
- Protocol-conformant parsing for EEPROM, sensor, and status commands.
"""

import asyncio
import logging
import sys
from functools import wraps
from typing import Awaitable, Callable, Dict, List, Literal, Optional, TypeVar, cast

from pylabrobot.io.serial import Serial
from pylabrobot.machines.machine import MachineBackend

if sys.version_info < (3, 10):
  from typing_extensions import Concatenate, ParamSpec
else:
  from typing import Concatenate, ParamSpec
try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e


P = ParamSpec("P")
R = TypeVar("R")


class InhecoError(RuntimeError):
  """Represents an Inheco firmware-reported error."""

  def __init__(self, command: str, code: str, message: str):
    super().__init__(f"{command} failed with error {code}: {message}")
    self.command: str = command
    self.code: str = code
    self.message: str = message


_REF_FLAG_NAMES: Dict[int, str] = {
  # Heater (0-15) — names per manual's heater flags table (subset shown here)
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
  # Shaker (16-26) — names per manual's shaker flag set (page 39)
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
  # 27-31 reserved
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

InhecoIncubatorUnitType = Literal[
  "incubator_mp", "incubator_shaker_mp", "incubator_dwp", "incubator_shaker_dwp", "unknown"
]


def requires_incubator_shaker(
  func: Callable[Concatenate["InhecoIncubatorShakerStackBackend", P], Awaitable[R]],
) -> Callable[Concatenate["InhecoIncubatorShakerStackBackend", P], Awaitable[R]]:
  @wraps(func)
  async def wrapper(
    self: "InhecoIncubatorShakerStackBackend", *args: P.args, **kwargs: P.kwargs
  ) -> R:
    name = getattr(func, "__name__", func.__class__.__name__)
    stack_index = cast(int, kwargs.get("stack_index", 0))
    incubator_type = self.unit_composition[stack_index]

    if "shaker" not in incubator_type:
      raise RuntimeError(f"{name}() requires a shaker-capable model (got {incubator_type!r}).")

    return await func(self, *args, **kwargs)

  return wrapper


class InhecoIncubatorShakerStackBackend(MachineBackend):
  """Interface for Inheco Incubator Shaker stack machines.

  Handles:
    - USB/serial connection setup via VID/PID
    - DIP switch ID verification
    - Message framing, CRC generation
    - Complete async read/write of firmware responses
    - Binary-safe parsing and error mapping
  """

  # === Logging utility ===

  # === Constructor ===

  def __init__(
    self,
    dip_switch_id: int = 2,
    port: Optional[str] = None,
    write_timeout: float = 5.0,
    read_timeout: float = 10.0,
    vid: int = 0x0403,
    pid: int = 0x6001,
  ):
    super().__init__()

    self.logger = logging.LoggerAdapter(
      logging.getLogger(__name__),
      {"dip_switch_id": dip_switch_id},
    )

    # Core state
    self.dip_switch_id = dip_switch_id

    self.io = Serial(
      port=port,
      vid=vid,
      pid=pid,
      baudrate=19_200,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      timeout=0,
      write_timeout=1,
    )

    # Communication timeouts (defaults for all units in stack)
    self.write_timeout = write_timeout
    self.read_timeout = read_timeout

    # Cached state (stack level)
    self.setup_finished = False
    self.max_temperature = 85.0  # safe default
    self.unit_composition: List[
      InhecoIncubatorUnitType
    ] = []  # e.g. ["incubator_mp", "incubator_shaker_dwp", ...]

    self._send_command_lock = asyncio.Lock()

  @property
  def number_of_connected_units(self) -> int:
    """Return the number of connected units in the stack."""
    return len(self.unit_composition)

  def __repr__(self):
    return (
      f"<InhecoIncubatorShakerBackend (VID:PID={self.io._vid}:{self.io._pid}, "
      + f"DIP={self.dip_switch_id}) at {self.io.port}>"
    )

  async def setup(self, port: Optional[str] = None):
    """
    Detect and connect to the Inheco machine stack.
    Discover Inheco device via VID:PID (0403:6001) and verify DIP switch ID.
    """

    # --- Establish serial connection ---
    await self.io.setup()
    self.io.dtr = False
    self.io.rts = False

    try:  # --- Verify DIP switch ID via RTS ---
      probe = self._build_message("RTS", stack_index=0)
      await self.io.write(probe)
      resp = await self._read_full_response(timeout=5.0)

      expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF
      if resp[0] != expected_hdr:
        raise ValueError("Unexpected header")

    except Exception as e:
      # Capture current IO reference before marking disconnected
      msg = (
        f"Device on {self.io._port} failed DIP switch verification (expected ID="
        f"{self.dip_switch_id}). Please verify the DIP switch setting or wiring."
      )
      self.logger.error(msg, exc_info=e)

      # --- Fail-safe teardown ---
      try:
        await self.io.stop()
        self.logger.debug("Closed serial connection on %s", self.io.port)
      except Exception as close_err:
        self.logger.warning(
          "Failed to close serial port cleanly on %s: %s",
          self.io._port,
          close_err,
        )
      raise RuntimeError(msg) from e

    else:
      # Connection verified and active
      self.logger.log(
        logging.INFO,
        f"Connected to Inheco machine at {self.io.port} (DIP={self.dip_switch_id})",
      )

    # --- Cache stack-level state ---
    number_of_connected_units = await self.request_number_of_connected_machines(stack_index=0)

    self.unit_composition = []

    for unit_index in range(number_of_connected_units):
      inc_type = await self.request_incubator_type(stack_index=unit_index)
      self.unit_composition.append(inc_type)

      await self.initialize(stack_index=unit_index)

    self.setup_finished = True

    self.logger.info(
      "Connected to Inheco Incubator Shaker Stack on %s\n"
      "DIP switch ID of bottom unit: %s\n"
      "Number of connected units: %s\n"
      "Unit composition: %s",
      self.io.port,
      self.dip_switch_id,
      self.number_of_connected_units,
      self.unit_composition,
    )

  async def stop(self):
    """Close serial connection & stop all active units in the stack."""

    for unit_index in range(self.number_of_connected_units):
      temp_status = await self.is_temperature_control_enabled(stack_index=unit_index)

      if temp_status:
        print(f"Stopping temperature control on unit {unit_index}...")
        await self.stop_temperature_control(stack_index=unit_index)

      shake_status = await self.is_shaking_enabled(stack_index=unit_index)

      if shake_status:
        print(f"Stopping shaking on unit {unit_index}...")
        await self.stop_shaking(stack_index=unit_index)

      await self.close(stack_index=unit_index)

    await self.io.stop()

  # === Low-level I/O ===

  async def _read_full_response(self, timeout: float) -> bytes:
    """Read a complete Inheco response frame asynchronously."""
    loop = asyncio.get_event_loop()
    start = loop.time()
    buf = bytearray()
    expected_hdr = (0xB0 + self.dip_switch_id) & 0xFF

    def has_complete_tail(b: bytearray) -> bool:
      # Valid frame ends with: [hdr][0x20-0x2F][0x60]
      return len(b) >= 3 and b[-1] == 0x60 and b[-3] == expected_hdr and 0x20 <= b[-2] <= 0x2F

    while True:
      chunk = await self.io.read(16)
      if len(chunk) > 0:
        buf.extend(chunk)
        if has_complete_tail(buf):
          self.logger.debug("RECV response: %s", buf.hex(" "))
          return bytes(buf)

      if loop.time() - start > timeout:
        raise TimeoutError(f"Timed out waiting for complete response (so far: {buf.hex(' ')})")

      await asyncio.sleep(0.005)

  # === Encoding / Decoding ===

  def _crc8_legacy(self, data: bytearray) -> int:
    """Compute legacy CRC-8 used by Inheco devices."""
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
    return len(command) > 0 and command[0].upper() == "R"

  # === Response parsing ===

  def _parse_response_binary_safe(self, resp: bytes) -> dict:
    """Parse Inheco response frames safely (binary & multi-segment)."""
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
    *,
    delay: float = 0.2,
    write_timeout: Optional[float] = None,
    read_timeout: Optional[float] = None,
    stack_index: int = 0,
  ) -> str:
    """Send a framed command and return parsed response or raise InhecoError."""

    async with self._send_command_lock:
      # Use global default if not overridden
      w_timeout = write_timeout or self.write_timeout
      msg = self._build_message(command, stack_index=stack_index)
      self.logger.debug("SEND command: %s (write_timeout=%s)", msg.hex(" "), w_timeout)

      await asyncio.wait_for(self.io.write(msg), timeout=w_timeout)
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

      return str(parsed["data"])

  # === Public high-level API ===

  # Querying Machine State #
  async def request_firmware_version(self, stack_index: int) -> str:
    """EEPROM request: Return the firmware version string."""
    resp = await self.send_command("RFV0", stack_index=stack_index)
    return resp

  async def request_serial_number(self, stack_index: int) -> str:
    """EEPROM request: Return the device serial number."""
    resp = await self.send_command("RFV2", stack_index=stack_index)
    return resp

  async def request_last_calibration_date(self, stack_index: int) -> str:
    """EEPROM request"""
    resp = await self.send_command("RCM", stack_index=stack_index)
    return resp[:10]

  async def request_machine_allocation(self, layer: int = 0, stack_index: int = 0) -> dict:
    """
    Report which device slots are occupied on a given layer (firmware 'RDAx,0').

    Args:
      layer: Layer index (0-7). Default = 0.

    Returns:
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

    resp = await self.send_command(f"RDA{layer},0", stack_index=stack_index)
    resp_str = str(resp).strip()
    slot_mask = int(resp_str)
    slot_mask_bin = f"0b{slot_mask:016b}"

    slots_connected = [i for i in range(16) if (slot_mask >> i) & 1]

    return {
      "layer": layer,
      "slot_mask": slot_mask,
      "slot_mask_bin": slot_mask_bin,
      "slots_connected": slots_connected,
    }

  async def request_number_of_connected_machines(self, layer: int = 0, stack_index: int = 0) -> int:
    """
    Report the number of connected Inheco devices on a layer (RDAx,1).

    Args:
      layer: Layer index (0-7). Default = 0.

    Returns:
      Number of connected devices (0-16).

    Example:
      Response "3" → 3 connected devices on that layer.
    """
    if not (0 <= layer <= 7):
      raise ValueError(f"Layer must be 0-7, got {layer}")

    resp = await self.send_command(f"RDA{layer},1", stack_index=stack_index)
    return int(resp.strip())

  async def request_labware_detection_threshold(self, stack_index: int) -> int:
    """EEPROM request"""
    resp = await self.send_command("RDM", stack_index=stack_index)
    return int(resp)

  async def request_incubator_type(self, stack_index: int) -> InhecoIncubatorUnitType:
    """Return a descriptive string of the incubator/shaker configuration."""

    incubator_type_dict = {
      "0": "incubator_mp",  # no shaker
      "1": "incubator_shaker_mp",
      "2": "incubator_dwp",  # no shaker
      "3": "incubator_shaker_dwp",
    }
    resp = await self.send_command("RTS", stack_index=stack_index)
    return incubator_type_dict.get(resp, "unknown")  # type: ignore[return-value]

  async def request_plate_in_incubator(self, stack_index: int) -> bool:
    """Sensor command:"""
    resp = await self.send_command("RLW", stack_index=stack_index)
    return resp == "1"

  async def request_operation_time_in_hours(self, stack_index: int) -> int:
    """EEPROM request"""
    resp = await self.send_command("RDC1", stack_index=stack_index)
    return int(resp)

  async def request_drawer_cycles_performed(self, stack_index: int) -> int:
    """EEPROM request"""
    resp = await self.send_command("RDC2", stack_index=stack_index)
    return int(resp)

  async def request_is_initialized(self, stack_index: int) -> bool:
    """EEPROM request"""
    resp = await self.send_command("REE", stack_index=stack_index)
    return resp in {"0", "2"}

  async def request_plate_status_known(self, stack_index: int) -> bool:
    """EEPROM request"""
    resp = await self.send_command("REE", stack_index=stack_index)
    return resp in {"0", "1"}

  async def request_thermal_calibration_date(self, stack_index: int) -> str:
    """EEPROM request: Query the date of the last thermal calibration.

    Returns:
      Calibration date in ISO format 'YYYY-MM-DD'.
    """
    resp = await self.send_command("RCD", stack_index=stack_index)
    date = resp.strip()
    if not date or len(date) != 10 or date.count("-") != 2:
      raise RuntimeError(f"Unexpected RCD response: {resp!r}")
    return date

  # TODO: Command Placeholders

  async def request_calibration_low(self, sensor: int, format: int) -> float:
    raise NotImplementedError("RCL (Report Calibration Low) not implemented yet.")

  async def request_calibration_high(self, sensor: int, format: int) -> float:
    raise NotImplementedError("RCH (Report Calibration High) not implemented yet.")

  async def request_whole_calibration_data(self, key: str) -> bytes:
    raise NotImplementedError("RWC (Read Whole Calibration Data) not implemented yet.")

  async def request_proportionality_factor(self) -> int:
    raise NotImplementedError("RPF (Report Proportionality Factor) not implemented yet.")

  async def set_max_allowed_device_temperature(self, key: str, temperature: int) -> None:
    raise NotImplementedError("SMT (Set Max Allowed Device Temperature) not implemented yet.")

  async def set_pid_proportional_gain(self, key: str, value: int) -> None:
    raise NotImplementedError("SPP (Set PID Proportional Gain) not implemented yet.")

  async def set_pid_integration_value(self, key: str, value: int) -> None:
    raise NotImplementedError("SPI (Set PID Integration Value) not implemented yet.")

  async def delete_counter(self, key: str, selector: int) -> None:
    raise NotImplementedError("SDC (Set Delete Counter) not implemented yet.")

  async def set_boost_offset(self, offset: int) -> None:
    raise NotImplementedError("SBO (Set Boost Offset) not implemented yet.")

  async def set_boost_time(self, time_s: int) -> None:
    raise NotImplementedError("SBT (Set Boost Time) not implemented yet.")

  async def set_cooldown_time_factor(self, value: int) -> None:
    raise NotImplementedError("SHK (Set Cool-Down Time Evaluation Factor) not implemented yet.")

  async def set_heatup_time_factor(self, value: int) -> None:
    raise NotImplementedError("SHH (Set Heat-Up Time Evaluation Factor) not implemented yet.")

  async def set_heatup_offset(self, offset: int) -> None:
    raise NotImplementedError("SHO (Set Heat-Up Offset) not implemented yet.")

  async def set_calibration_low(self, key: str, sensor1: int, sensor2: int, sensor3: int) -> None:
    raise NotImplementedError("SCL (Set Calibration Low) not implemented yet.")

  async def set_calibration_high(
    self, key: str, sensor1: int, sensor2: int, sensor3: int, date: str
  ) -> None:
    raise NotImplementedError("SCH (Set Calibration High and Date) not implemented yet.")

  async def reset_calibration_data(self, key: str) -> None:
    raise NotImplementedError("SRC (Set Reset Calibration-Data) not implemented yet.")

  async def set_proportionality_factor(self, value: int) -> None:
    raise NotImplementedError("SPF (Set Proportionality Factor) not implemented yet.")

    # # # Setup Requirement # # #

  async def initialize(self, stack_index: int) -> str:
    """Initializes the machine unit after power-on.
    All other Action-Commands of the device are prohibited before the command is executed.
    On command AID, the drawer will be closed. If the drawer is hold in its open position,
    no Error will be generated!
    If AID is send during operating, the shaker and heater stop immediately!
    """
    resp = await self.send_command("AID", stack_index=stack_index)
    return resp

  # # # Loading Tray Features # # #

  async def open(self, stack_index: int) -> None:
    """Open the incubator door & move loading tray out."""
    await self.send_command("AOD", stack_index=stack_index)

  async def close(self, stack_index: int) -> None:
    """Move the loading tray in & close the incubator door."""
    await self.send_command("ACD", stack_index=stack_index)

  DrawerStatus = Literal["open", "closed"]

  async def request_drawer_status(self, stack_index: int) -> DrawerStatus:
    """Report the current drawer (loading tray) status.

    Returns:
      'open' if the loading tray is open, 'closed' if closed.

    Notes:
      - Firmware response: '1' = open, '0' = closed.
    """
    resp = await self.send_command("RDS", stack_index=stack_index)
    if resp == "1":
      return "open"
    if resp == "0":
      return "closed"
    raise ValueError(f"Unexpected RDS response: {resp!r}")

  # TODO: Drawer Placeholder Commands

  async def request_motor_power_clockwise(self) -> int:
    raise NotImplementedError("RPR (Report Motor Power Clockwise) not implemented yet.")

  async def request_motor_power_anticlockwise(self) -> int:
    raise NotImplementedError("RPL (Report Motor Power Anticlockwise) not implemented yet.")

  async def request_motor_current_limit_clockwise(self) -> int:
    raise NotImplementedError("RGR (Report Motor Current Limit Clockwise) not implemented yet.")

  async def request_motor_current_limit_anticlockwise(self) -> int:
    raise NotImplementedError("RGL (Report Motor Current Limit Anticlockwise) not implemented yet.")

  async def set_motor_power_clockwise(self, key: str, power: int) -> None:
    raise NotImplementedError("SPR (Set Motor Power Clockwise) not implemented yet.")

  async def set_motor_power_anticlockwise(self, key: str, power: int) -> None:
    raise NotImplementedError("SPL (Set Motor Power Anticlockwise) not implemented yet.")

  async def set_motor_current_limit_clockwise(self, key: str, current: int) -> None:
    raise NotImplementedError("SGR (Set Motor Current Limit Clockwise) not implemented yet.")

  async def set_motor_current_limit_anticlockwise(self, key: str, current: int) -> None:
    raise NotImplementedError("SGL (Set Motor Current Limit Anticlockwise) not implemented yet.")

  # # # Temperature Features # # #

  async def start_temperature_control(self, temperature: float, stack_index: int) -> None:
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
    await self.send_command(f"STT{target}", stack_index=stack_index)  # Store target temperature
    await self.send_command("SHE1", stack_index=stack_index)  # Enable temperature regulation

  async def stop_temperature_control(self, stack_index: int) -> None:
    """Stop active temperature regulation.

    Disables the incubator's heating control loop.
    The previously set target temperature remains stored in the
    device's memory but is no longer actively maintained.
    The incubator will passively drift toward ambient temperature.
    """
    await self.send_command("SHE0", stack_index=stack_index)

  async def get_temperature(
    self,
    stack_index: int,
    sensor: Literal["mean", "main", "dif", "boost"] = "main",
    read_timeout: float = 60.0,
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
      val = await self.send_command(f"RAT{idx}", stack_index=stack_index, read_timeout=read_timeout)
      vals.append(int(val) / 10.0)
    return round(sum(vals) / len(vals), 2)

  async def request_target_temperature(self, stack_index: int) -> float:
    """Return target temperature in °C."""

    resp = await self.send_command("RTT", stack_index=stack_index)

    return int(resp) / 10

  async def is_temperature_control_enabled(self, stack_index: int) -> bool:
    """
    Return True if active temperature control is enabled (RHE = 1 or 2),
    False if control is off (RHE = 0).

    Firmware response (RHE):
    0: control loop off (passive equilibrium)
    1: control loop on (heating/cooling active)
    2: control + booster on (extended heating mode)
    """
    resp = await self.send_command("RHE", stack_index=stack_index)
    try:
      status = int(resp.strip())
    except ValueError:
      raise InhecoError("RHE", "E00", f"Unexpected response: {resp!r}")

    if status not in (0, 1, 2):
      raise InhecoError("RHE", "E00", f"Invalid heater status value: {status}")

    enabled = status in (1, 2)

    return enabled

  async def request_pid_controller_coefficients(self, stack_index: int) -> tuple[float, float]:
    """
    Query the current PID controller coefficients.

    Returns:
        (P, I): tuple of floats
            - P: proportional gain (selector 1)
            - I: integration value (selector 2; 0 = integration off)
    """
    p_resp = await self.send_command("RPC1", stack_index=stack_index)
    i_resp = await self.send_command("RPC2", stack_index=stack_index)

    try:
      p = float(p_resp.strip())
      i = float(i_resp.strip())
    except ValueError:
      raise RuntimeError(f"Unexpected RPC response(s): P={p_resp!r}, I={i_resp!r}")

    return p, i

  async def request_maximum_allowed_temperature(
    self, stack_index: int, measured: bool = False
  ) -> float:
    """
    Query the maximum allowed or maximum measured device temperature (in °C).

    Args:
      measured:
      - False: report configured maximum allowed temperature (default)
      - True: report maximum measured temperature since last reset

    Returns:
      Temperature in °C (value / 10)
    """
    selector = "1" if measured else ""
    resp = await self.send_command(f"RMT{selector}", stack_index=stack_index)
    try:
      return int(resp.strip()) / 10.0
    except ValueError:
      raise RuntimeError(f"Unexpected RMT response: {resp!r}")

  async def request_delta_temperature(self, stack_index: int) -> float:
    """
    Query the absolute temperature difference between target and actual plate temperature.

    Returns:
      Delta temperature in °C (positive if below target, negative if above target).

    Notes:
    - Reported in 1/10 °C.
    - Negative values indicate the plate is warmer than the target.
    """
    resp = await self.send_command("RDT", stack_index=stack_index)
    try:
      return int(resp.strip()) / 10.0
    except ValueError:
      raise RuntimeError(f"Unexpected RDT response: {resp!r}")

  async def wait_for_temperature(
    self,
    stack_index: int,
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
    target_temp = await self.request_target_temperature(stack_index=stack_index)
    if target_temp is None:
      raise ValueError("Device did not return a valid target temperature.")

    temperature_control_enabled = await self.is_temperature_control_enabled(stack_index=stack_index)
    if not temperature_control_enabled:
      raise ValueError(
        f"Temperature control is not enabled on the machine ({stack_index}: {self.unit_composition[stack_index]})."
      )

    start_time = asyncio.get_event_loop().time()
    first_temp = await self.get_temperature(sensor=sensor, stack_index=stack_index)
    initial_diff = abs(first_temp - target_temp)
    bar_width = 40

    if show_progress_bar:
      print(f"Waiting for target temperature {target_temp:.2f} °C...\n")

    while True:
      current_temp = await self.get_temperature(sensor=sensor, stack_index=stack_index)
      diff = abs(current_temp - target_temp)

      # Compute normalized progress (1 = done)
      progress = 1.0 - min(diff / max(initial_diff, 1e-6), 1.0)
      filled = int(bar_width * progress)
      bar = "█" * filled + "-" * (bar_width - filled)

      if show_progress_bar:
        # Compute slope (°C/sec) based on direction of travel
        delta_done = abs(current_temp - first_temp)

        elapsed = asyncio.get_event_loop().time() - start_time

        slope = delta_done / max(elapsed, 1e-6)  # °C per second

        if slope > 0.0001:
          remaining = diff  # remaining °C
          eta_s = remaining / slope
          eta_str = f"{eta_s:6.1f}s"
        else:
          eta_str = "   --- "

        sys.stdout.write(f"\r[{bar}] {current_temp:.2f} °C  (Δ={diff:.2f} °C | ETA: {eta_str})")
        sys.stdout.flush()

      if diff <= tolerance:
        if show_progress_bar:
          sys.stdout.write("\n[OK] Target temperature reached.\n")
          sys.stdout.flush()

        self.logger.info("Target temperature reached (%.2f °C).", current_temp)
        return current_temp

      if timeout_s is not None:
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout_s:
          if show_progress_bar:
            sys.stdout.write("\n[ERROR] Timeout waiting for temperature.\n")
            sys.stdout.flush()

          raise TimeoutError(
            f"Timeout after {timeout_s:.1f}s: "
            f"temperature {current_temp:.2f} °C "
            f"did not reach target {target_temp:.2f} °C ±{tolerance:.2f} °C."
          )

      await asyncio.sleep(interval_s)

  # # # Shaking Features # # #

  @requires_incubator_shaker
  async def request_shaker_frequency_x(self, stack_index: int, selector: int = 0) -> float:
    """Read the set or actual shaker frequency in the X-direction.

    Args:
      selector: 0 = to-be-set frequency, 1 = actual frequency.  Default = 0.

    Returns:
      Frequency in Hz.
    """
    if selector not in (0, 1):
      raise ValueError(f"Selector must be 0 or 1, got {selector}")
    resp = await self.send_command(f"RFX{selector}", stack_index=stack_index)
    return float(resp) / 10.0  # firmware reports in 1/10 Hz

  @requires_incubator_shaker
  async def request_shaker_frequency_y(self, stack_index: int, selector: int = 0) -> float:
    """Read the set or actual shaker frequency in the Y-direction.

    Args:
      selector: 0 = to-be-set frequency, 1 = actual frequency.  Default = 0.

    Returns:
      Frequency in Hz.
    """
    if selector not in (0, 1):
      raise ValueError(f"Selector must be 0 or 1, got {selector}")
    resp = await self.send_command(f"RFY{selector}", stack_index=stack_index)
    return float(resp) / 10.0  # firmware reports in 1/10 Hz

  @requires_incubator_shaker
  async def request_shaker_amplitude_x(self, stack_index: int, selector: int = 0) -> float:
    """Read the set, actual, or static shaker amplitude in the X-direction.

    Args:
      selector: 0 = set amplitude, 1 = actual amplitude, 2 = static distance from middle.  Default = 0.

    Returns:
      Amplitude in millimeters (mm).
    """
    if selector not in (0, 1, 2):
      raise ValueError(f"Selector must be 0, 1, or 2, got {selector}")
    resp = await self.send_command(f"RAX{selector}", stack_index=stack_index)
    return float(resp) / 10.0  # firmware reports in 1/10 mm

  @requires_incubator_shaker
  async def request_shaker_amplitude_y(self, stack_index: int, selector: int = 0) -> float:
    """Read the set, actual, or static shaker amplitude in the Y-direction.

    Args:
      selector: 0 = set amplitude, 1 = actual amplitude, 2 = static distance from middle.  Default = 0.

    Returns:
      Amplitude in millimeters (mm).
    """
    if selector not in (0, 1, 2):
      raise ValueError(f"Selector must be 0, 1, or 2, got {selector}")
    resp = await self.send_command(f"RAY{selector}", stack_index=stack_index)
    return float(resp) / 10.0  # firmware reports in 1/10 mm

  async def is_shaking_enabled(self, stack_index: int) -> bool:
    """Return True if the shaker is currently enabled or still decelerating.

    The firmware returns: 0 = shaker off; 1 = shaker on; 2 = shaker switched off but still moving.

    Returns:
      True if the shaker is active or still moving (status 1 or 2), False if fully stopped (status 0).
    """

    if "shaker" not in self.unit_composition[stack_index]:
      return False

    resp = await self.send_command("RSE", stack_index=stack_index)

    try:
      status = int(resp)
    except ValueError:
      raise InhecoError("RSE", "E00", f"Unexpected response: {resp!r}")

    if status not in (0, 1, 2):
      raise InhecoError("RSE", "E00", f"Invalid shaker status value: {status}")

    return status in (1, 2)  # TODO: discuss whether 2 should count as "shaking"

  @requires_incubator_shaker
  async def set_shaker_parameters(
    self,
    amplitude_x: float,
    amplitude_y: float,
    frequency_x: float,
    frequency_y: float,
    phase_shift: float,
    stack_index: int,
  ) -> None:
    """Set shaker parameters for both X and Y axes in a single command (firmware 'SSP').

    This combines the functionality of the individual SAX, SAY, SFX, SFY, and SPS commands.

    Args:
      amplitude_x: Amplitude on the X-axis in mm (0.0-3.0 mm, corresponds to 0-30 in firmware units).
      amplitude_y: Amplitude on the Y-axis in mm (0.0-3.0 mm, corresponds to 0-30 in firmware units).
      frequency_x: Frequency on the X-axis in Hz (6.6-30.0 Hz, corresponds to 66-300 in firmware units).
      frequency_y: Frequency on the Y-axis in Hz (6.6-30.0 Hz, corresponds to 66-300 in firmware units).
      phase_shift: Phase shift between X and Y axes in degrees (0-360°).

    Notes:
      - This command simplifies coordinated shaker setup.
      - All arguments are automatically converted to the firmware's expected integer scaling.
        (mm → x10; Hz → x10; ° left unscaled)
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
    await self.send_command(cmd, stack_index=stack_index)

  def _mm_to_fw(self, mm: float) -> int:
    """Convert mm → firmware units (1/10 mm).

    Valid range: 0.0-3.0 mm (→ 0-30 in firmware).
    Raises ValueError if out of range.
    """
    if not (0.0 <= mm <= 3.0):
      raise ValueError(f"Amplitude must be between 0.0 and 3.0 mm, got {mm}")
    return int(round(mm * 10))

  def _rpm_to_fw_hz10(self, rpm: float) -> int:
    """Convert RPM → firmware Hz·10 units (validated).

    396-1800 RPM ↔ 6.6-30.0 Hz ↔ 66-300 in firmware.
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
    """Return integer phase or default (0-360°)."""
    p = default if phase_deg is None else int(round(phase_deg))
    if not (0 <= p <= 360):
      raise ValueError(f"Phase must be 0-360°, got {p}")
    return p

  def _fw_freq_pair(self, frequency_hz: Optional[float], rpm: Optional[float]) -> tuple[int, int]:
    """Return validated firmware frequency pair (Hz·10, Hz·10)."""
    if frequency_hz is not None:
      f = self._hz_to_fw_hz10(frequency_hz)
    else:
      # At this point rpm MUST be not None (validated earlier)
      assert rpm is not None
      f = self._rpm_to_fw_hz10(rpm)
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
    stack_index: int,
    frequency_hz: Optional[float] = None,
    rpm: Optional[float] = None,
    amplitude_x_mm: Optional[float] = None,
    amplitude_y_mm: Optional[float] = None,
    phase_deg: Optional[float] = None,
  ) -> None:
    """Set the shaker motion pattern and parameters (without enabling motion).

    Patterns:
    - linear_x: motion along X only.
    - linear_y: motion along Y only.
    - orbital: circular motion (equal amplitudes on both axes, 90° phase).
    - elliptical: elliptical motion (unequal amplitudes, 90° phase).
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
      raise ValueError(
        f"Unknown pattern: {pattern}"
        "\nValid options: 'linear_x', 'linear_y', 'orbital', 'elliptical', 'figure_eight'"
      )

    await self.send_command(f"SSP{ax},{ay},{fx},{fy},{phase}", stack_index=stack_index)

  @requires_incubator_shaker
  async def set_shaker_status(self, enabled: bool, stack_index: int) -> None:
    """Enable or disable the shaker (ASEND always used when enabled)."""
    await self.send_command("ASEND" if enabled else "ASE0", stack_index=stack_index)

  @requires_incubator_shaker
  async def shake(
    self,
    *,
    stack_index: int,
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

    Behavior:
    - Stops the shaker if active, waits briefly, applies the new pattern, and restarts shaking.
    - Ensures consistent parameter changes and prevents ignored SSP updates.

    Args:
      pattern: Motion pattern: `"linear_x"`, `"linear_y"`, `"orbital"`, `"elliptical"`, or `"figure_eight"`.
      rpm: Rotational speed (396-1800 RPM). Mutually exclusive with `frequency_hz`.
      frequency_hz: Oscillation frequency (6.6-30.0 Hz). Mutually exclusive with `rpm`.
      amplitude_x_mm: X-axis amplitude in mm (0.0-3.0 mm).
      amplitude_y_mm: Y-axis amplitude in mm (0.0-3.0 mm).
      phase_deg: Optional phase offset between X and Y axes (0-360°).

    Raises:
      ValueError: If parameter ranges or combinations are invalid.
      InhecoError: If the device rejects the command or is not ready.
    """

    is_shaking = await self.is_shaking_enabled(stack_index=stack_index)
    if is_shaking:
      await self.stop_shaking(stack_index=stack_index)
      await asyncio.sleep(0.5)  # brief pause for firmware to settle

    await self.set_shaker_pattern(
      pattern=pattern,
      rpm=rpm,
      stack_index=stack_index,
      frequency_hz=frequency_hz,
      amplitude_x_mm=amplitude_x_mm,
      amplitude_y_mm=amplitude_y_mm,
      phase_deg=phase_deg,
    )
    await self.set_shaker_status(True, stack_index=stack_index)

  # TODO: expose direction argument -> clockwise / counterclockwise for rotating shaking patterns

  @requires_incubator_shaker
  async def stop_shaking(self, stack_index: int) -> None:
    """Stop shaker (ASE0)."""

    await self.set_shaker_status(False, stack_index=stack_index)

  @requires_incubator_shaker
  async def request_shaker_phase_shift(self, stack_index: int, selector: int = 0) -> float:
    """Read the set or actual phase shift between X and Y shaker drives (firmware 'RPS' command).

    Args:
      selector: 0 = currently set phase shift, 1 = actual phase shift.  Default = 0.

    Returns:
      Phase shift in degrees [°].  Returns 12345.0 if the shaker has not reached a stable state or if phase shift calculation is invalid due to too-small amplitudes (< 1 mm on either axis).
    """
    if selector not in (0, 1):
      raise ValueError(f"Selector must be 0 or 1, got {selector}")

    resp = await self.send_command(f"RPS{selector}", stack_index=stack_index)
    return float(resp)

  @requires_incubator_shaker
  async def request_shaker_calibration_value(self, position: int, selector: int = 0) -> float:
    raise NotImplementedError("RSC (Read Shaker Calibration Values) not implemented yet.")

  @requires_incubator_shaker
  async def read_whole_shaker_calibration_data(self, key: str) -> str:
    raise NotImplementedError("RWJ (Read Whole Shaker Adjustment Data) not implemented yet.")

  @requires_incubator_shaker
  async def set_shaker_calibration_value(
    self,
    key: str,
    position: int,
    value: int,
  ) -> None:
    raise NotImplementedError("SSC (Set Shaker Calibration Values) not implemented yet.")

  # # # Self-Test # # #

  async def perform_self_test(self, stack_index: int, read_timeout: int = 500) -> Dict[str, bool]:
    """Execute the internal self-test routine.

    Normal Testing-Time: ca. 3 minutes (Beware of timeouts!)
    Maximum Testing-Time: 465 sec. (Beware of timeouts!)
    The Test must be performed without a Labware inside!
    The Error code is reported as an 11-Bit-Code. A set bit (1) represents an Error. The Test stops
    immediately, when the Drawer-Test fails to avoid damage to the device!
    Please note the AQS was developed for the Incubator Shaker MP. This can lead assemblies to
    DWP Incubators that the result of the AQS, due to the design, 8 (bit2) could be.

    Returns:
      A dictionary mapping error condition names to booleans indicating presence of each error.
    """

    plate_in_status = await self.request_plate_in_incubator(stack_index=stack_index)
    if plate_in_status:
      raise ValueError("Self-test requires an empty incubator.")

    loading_tray_status = await self.request_drawer_status(stack_index=stack_index)
    if loading_tray_status == "open":
      raise ValueError("Self-test requires a closed loading tray.")

    resp = await self.send_command("AQS", stack_index=stack_index, read_timeout=read_timeout)

    if not isinstance(resp, str):
      raise RuntimeError(f"Invalid response: {resp!r}")

    resp_decimal = int(resp)

    assert 0 <= resp_decimal <= 2047, f"Invalid self-test response received: {resp!r}"

    binary_response = bin(resp_decimal)[2:].zfill(11)
    return {
      "drawer_error": binary_response[-1] == "1",
      "homogeneity_sensor_3_vs_1_error": binary_response[-2] == "1",
      "homogeneity_sensor_2_vs_1_error": binary_response[-3] == "1",
      "sensor_1_target_temp_error": binary_response[-4] == "1",
      "y_amplitude_shaker_error": binary_response[-5] == "1",
      "x_amplitude_shaker_error": binary_response[-6] == "1",
      "phase_shift_shaker_error": binary_response[-7] == "1",
      "y_frequency_shaker_error": binary_response[-8] == "1",
      "x_frequency_shaker_error": binary_response[-9] == "1",
      "line_boost_heater_broken": binary_response[-10] == "1",
      "line_main_heater_broken": binary_response[-11] == "1",
    }


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# IncubatorShakerUnit Class
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #


class InhecoIncubatorShakerUnit:
  """High-level API for an individual Inheco Incubator/Shaker unit in a stacked system."""

  def __init__(self, backend: InhecoIncubatorShakerStackBackend, index: int):
    self.backend: InhecoIncubatorShakerStackBackend = backend
    self.index = index

  def __repr__(self):
    return f"<InhecoIncubatorShakerUnit index={self.index}>"

  # === Common high-level API shortcuts (explicitly delegate) ===

  # Querying Machine State #
  async def request_firmware_version(self) -> str:
    """EEPROM request: Return the firmware version string."""
    return await self.backend.request_firmware_version(stack_index=self.index)

  async def request_serial_number(self) -> str:
    """EEPROM request: Return the device serial number."""
    return await self.backend.request_serial_number(stack_index=self.index)

  async def request_last_calibration_date(self) -> str:
    """EEPROM request: Query the date of the last calibration."""
    return await self.backend.request_last_calibration_date(stack_index=self.index)

  async def request_machine_allocation(self, layer: int = 0) -> dict:
    """
    Report which device slots are occupied on a given layer (firmware 'RDAx,0').

    Args:
      layer: Layer index (0-7). Default = 0.

    Returns:
      {
        "layer": int,
        "slot_mask": int,       # e.g. 7
        "slot_mask_bin": str,   # e.g. "0b0000000000000111"
        "slots_connected": list[int]  # e.g. [0, 1, 2]
      }

    Notes:
      Each bit in `slot_mask` represents one of 16 possible device slots: bit=1 means a device is connected; bit=0 means empty.
    """
    return await self.backend.request_machine_allocation(layer=layer, stack_index=self.index)

  async def request_number_of_connected_machines(self, layer: int = 0) -> int:
    """
    Report the number of connected Inheco devices on a layer (RDAx,1).

    Args:
      layer: Layer index (0-7). Default = 0.

    Returns:
      Number of connected devices (0-16).

    Example:
      Response "3" → 3 connected devices on that layer.
    """
    return await self.backend.request_number_of_connected_machines(
      layer=layer, stack_index=self.index
    )

  async def request_labware_detection_threshold(self) -> int:
    """EEPROM request"""
    return await self.backend.request_labware_detection_threshold(stack_index=self.index)

  async def request_incubator_type(self) -> InhecoIncubatorUnitType:
    """Return a descriptive string of the incubator/shaker configuration."""
    return await self.backend.request_incubator_type(stack_index=self.index)

  async def request_plate_in_incubator(self) -> bool:
    """Sensor command: Check if a plate is currently present in the incubator."""
    return await self.backend.request_plate_in_incubator(stack_index=self.index)

  async def request_operation_time_in_hours(self) -> int:
    """EEPROM request: Total operation time of the device in hours."""
    return await self.backend.request_operation_time_in_hours(stack_index=self.index)

  async def request_drawer_cycles_performed(self) -> int:
    """EEPROM request: Number of drawer open/close cycles performed."""
    return await self.backend.request_drawer_cycles_performed(stack_index=self.index)

  async def request_is_initialized(self) -> bool:
    """EEPROM request: Check if the machine has been initialized."""
    resp = await self.backend.request_is_initialized(stack_index=self.index)
    return resp in {"0", "2"}

  async def request_plate_status_known(self) -> bool:
    """EEPROM request: Check if the plate status is known."""
    return await self.backend.request_plate_status_known(stack_index=self.index)

  async def request_thermal_calibration_date(self) -> str:
    """EEPROM request: Query the date of the last thermal calibration.

    Returns:
      Calibration date in ISO format 'YYYY-MM-DD'.
    """
    return await self.backend.request_thermal_calibration_date(stack_index=self.index)

  # # # Setup Requirement # # #

  async def initialize(self) -> str:
    """Initializes the machine unit after power-on.
    All other Action-Commands of the device are prohibited before the command is executed.
    On command AID, the drawer will be closed. If the drawer is hold in its open position,
    no Error will be generated!
    If AID is send during operating, the shaker and heater stop immediately!
    """
    return await self.backend.initialize(stack_index=self.index)

  # # # Loading Tray Features # # #

  async def open(self) -> None:
    """Open the incubator door & move loading tray out."""
    await self.backend.open(stack_index=self.index)

  async def close(self) -> None:
    """Move the loading tray in & close the incubator door."""
    await self.backend.close(stack_index=self.index)

  async def request_drawer_status(self) -> InhecoIncubatorShakerStackBackend.DrawerStatus:
    """Report the current drawer (loading tray) status.

    Returns:
      'open' if the loading tray is open, 'closed' if closed.
    """
    return await self.backend.request_drawer_status(stack_index=self.index)

  # # # Temperature Features # # #

  async def start_temperature_control(self, temperature: float) -> None:
    """Set and activate the target incubation temperature (°C).

    The device begins active heating toward the target temperature.
    Passive cooling (firmware default) may occur automatically if the
    target temperature is below ambient, depending on environmental conditions.
    """
    await self.backend.start_temperature_control(temperature=temperature, stack_index=self.index)

  async def stop_temperature_control(self) -> None:
    """Stop active temperature regulation.

    Disables the incubator's heating control loop.
    The previously set target temperature remains stored in the
    device's memory but is no longer actively maintained.
    The incubator will passively drift toward ambient temperature.
    """
    await self.backend.stop_temperature_control(stack_index=self.index)

  async def get_temperature(
    self,
    sensor: Literal["mean", "main", "dif", "boost"] = "main",
    read_timeout: float = 60.0,
  ) -> float:
    """Return current measured temperature in °C."""
    return await self.backend.get_temperature(
      sensor=sensor, stack_index=self.index, read_timeout=read_timeout
    )

  async def request_target_temperature(self) -> float:
    """Return target temperature in °C."""
    return await self.backend.request_target_temperature(stack_index=self.index)

  async def is_temperature_control_enabled(self) -> bool:
    """
    Return True if active temperature control is enabled (RHE = 1 or 2),
    False if control is off (RHE = 0).

    Firmware response (RHE): 0: control loop off (passive equilibrium).  1: control loop on (heating/cooling active).  2: control + booster on (extended heating mode).
    """
    return await self.backend.is_temperature_control_enabled(stack_index=self.index)

  async def request_pid_controller_coefficients(self) -> tuple[float, float]:
    """
    Query the current PID controller coefficients.

    Returns:
      (P, I): tuple of floats. P: proportional gain (selector 1). I: integration value (selector 2; 0 = integration off)
    """
    return await self.backend.request_pid_controller_coefficients(stack_index=self.index)

  async def request_maximum_allowed_temperature(self, measured: bool = False) -> float:
    """
    Query the maximum allowed or maximum measured device temperature (in °C).

    Args:
      measured:
        - False: report configured maximum allowed temperature (default)
        - True: report maximum measured temperature since last reset

    Returns:
      Temperature in °C (value / 10)
    """
    return await self.backend.request_maximum_allowed_temperature(
      stack_index=self.index, measured=measured
    )

  async def request_delta_temperature(self) -> float:
    """
    Query the absolute temperature difference between target and actual plate temperature.

    Returns:
      Delta temperature in °C (positive if below target, negative if above target).

    Notes:
    - Reported in 1/10 °C.
    - Negative values indicate the plate is warmer than the target.
    """
    return await self.backend.request_delta_temperature(stack_index=self.index)

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
    return await self.backend.wait_for_temperature(
      stack_index=self.index,
      sensor=sensor,
      tolerance=tolerance,
      interval_s=interval_s,
      timeout_s=timeout_s,
      show_progress_bar=show_progress_bar,
    )

  # # # Shaking Features # # #

  async def request_shaker_frequency_x(self, selector: int = 0) -> float:
    """Read the set or actual shaker frequency in the X-direction.

    Args:
      selector: 0 = to-be-set frequency, 1 = actual frequency.  Default = 0.

    Returns:
      Frequency in Hz.
    """
    return await self.backend.request_shaker_frequency_x(stack_index=self.index, selector=selector)

  async def request_shaker_frequency_y(self, selector: int = 0) -> float:
    """Read the set or actual shaker frequency in the Y-direction.

    Args:
      selector: 0 = to-be-set frequency, 1 = actual frequency.  Default = 0.

    Returns:
      Frequency in Hz.
    """
    return await self.backend.request_shaker_frequency_y(stack_index=self.index, selector=selector)

  async def request_shaker_amplitude_x(self, selector: int = 0) -> float:
    """Read the set, actual, or static shaker amplitude in the X-direction.

    Args:
      selector: 0 = set amplitude, 1 = actual amplitude, 2 = static distance from middle.  Default = 0.

    Returns:
      Amplitude in millimeters (mm).
    """
    return await self.backend.request_shaker_amplitude_x(stack_index=self.index, selector=selector)

  async def request_shaker_amplitude_y(self, selector: int = 0) -> float:
    """Read the set, actual, or static shaker amplitude in the Y-direction.

    Args:
      selector: 0 = set amplitude, 1 = actual amplitude, 2 = static distance from middle.  Default = 0.

    Returns:
        Amplitude in millimeters (mm).
    """
    return await self.backend.request_shaker_amplitude_y(stack_index=self.index, selector=selector)

  async def is_shaking_enabled(self) -> bool:
    """Return True if the shaker is currently enabled or still decelerating.

    The firmware returns: 0: shaker off, 1: shaker on, 2: shaker switched off but still moving.

    Returns:
      True if the shaker is active or still moving (status 1 or 2), False if fully stopped (status 0).
    """
    return await self.backend.is_shaking_enabled(stack_index=self.index)

  async def set_shaker_parameters(
    self,
    amplitude_x: float,
    amplitude_y: float,
    frequency_x: float,
    frequency_y: float,
    phase_shift: float,
    stack_index: int,
  ) -> None:
    """Set shaker parameters for both X and Y axes in a single command (firmware 'SSP').

    This combines the functionality of the individual SAX, SAY, SFX, SFY, and SPS commands.

    Args:
      amplitude_x: Amplitude on the X-axis in mm (0.0-3.0 mm, corresponds to 0-30 in firmware units).
      amplitude_y: Amplitude on the Y-axis in mm (0.0-3.0 mm, corresponds to 0-30 in firmware units).
      frequency_x: Frequency on the X-axis in Hz (6.6-30.0 Hz, corresponds to 66-300 in firmware units).
      frequency_y: Frequency on the Y-axis in Hz (6.6-30.0 Hz, corresponds to 66-300 in firmware units).
      phase_shift: Phase shift between X and Y axes in degrees (0-360°).

    Notes:
    - This command simplifies coordinated shaker setup.
    - All arguments are automatically converted to the firmware's expected integer scaling.
      (mm → x10; Hz → x10; ° left unscaled)
    - The firmware returns an acknowledgment frame on success.

    Raises:
      ValueError: If any parameter is outside its valid range.
      InhecoError: If the device reports an error or rejects the command.
    """
    await self.backend.set_shaker_parameters(
      stack_index=stack_index,
      amplitude_x=amplitude_x,
      amplitude_y=amplitude_y,
      frequency_x=frequency_x,
      frequency_y=frequency_y,
      phase_shift=phase_shift,
    )

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
    await self.backend.set_shaker_pattern(
      stack_index=self.index,
      pattern=pattern,
      frequency_hz=frequency_hz,
      rpm=rpm,
      amplitude_x_mm=amplitude_x_mm,
      amplitude_y_mm=amplitude_y_mm,
      phase_deg=phase_deg,
    )

  async def set_shaker_status(self, enabled: bool) -> None:
    """Enable or disable the shaker (ASEND always used when enabled)."""
    await self.backend.set_shaker_status(stack_index=self.index, enabled=enabled)

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

    Behavior:
      - Stops the shaker if active, waits briefly, applies the new pattern, and restarts shaking.
      - Ensures consistent parameter changes and prevents ignored SSP updates.

    Args:
      pattern: Motion pattern: `"linear_x"`, `"linear_y"`, `"orbital"`, `"elliptical"`, or `"figure_eight"`.
      rpm: Rotational speed (396-1800 RPM). Mutually exclusive with `frequency_hz`.
      frequency_hz: Oscillation frequency (6.6-30.0 Hz). Mutually exclusive with `rpm`.
      amplitude_x_mm: X-axis amplitude in mm (0.0-3.0 mm).
      amplitude_y_mm: Y-axis amplitude in mm (0.0-3.0 mm).
      phase_deg: Optional phase offset between X and Y axes (0-360°).

    Raises:
      ValueError: If parameter ranges or combinations are invalid.
      InhecoError: If the device rejects the command or is not ready.
    """
    await self.backend.shake(
      stack_index=self.index,
      pattern=pattern,
      rpm=rpm,
      frequency_hz=frequency_hz,
      amplitude_x_mm=amplitude_x_mm,
      amplitude_y_mm=amplitude_y_mm,
      phase_deg=phase_deg,
    )

  async def stop_shaking(self) -> None:
    """Stop shaker (ASE0)."""
    await self.backend.stop_shaking(stack_index=self.index)

  async def request_shaker_phase_shift(self, selector: int = 0) -> float:
    """Read the set or actual phase shift between X and Y shaker drives (firmware 'RPS' command).

    Args:
      selector: 0 = currently set phase shift, 1 = actual phase shift.  Default = 0.

    Returns:
      Phase shift in degrees [°].  Returns 12345.0 if the shaker has not reached a stable state or if phase shift calculation is invalid due to too-small amplitudes (< 1 mm on either axis).
    """
    return await self.backend.request_shaker_phase_shift(stack_index=self.index, selector=selector)

  # # # Self-Test # # #

  async def perform_self_test(self, read_timeout: int = 500) -> Dict[str, bool]:
    """Execute the internal self-test routine."""

    return await self.backend.perform_self_test(stack_index=self.index, read_timeout=read_timeout)
