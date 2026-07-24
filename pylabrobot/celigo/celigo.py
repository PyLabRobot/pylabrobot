"""Celigo image cytometer.

Drives the Celigo's FTDI-based USB-IO controller board over a serial link: the XY
stage, Z/focus, filter wheel (AllMotion EZStepper motors with encoder feedback), the
brightfield illumination DAC, and the galvo mirrors.

Wire protocol (all multi-byte fields big-endian). Every exchange is a request packet
followed by a response packet:

  Request (11-byte header + payload):
    [opcode:1][sequence:i32][total_length:i32][fletcher16:2] + payload
    The fletcher16 covers the first 9 header bytes.

  Response (12-byte header + payload):
    [ack:1][opcode_echo:1][sequence_echo:i32][payload_length:i32][fletcher16:2] + payload
    The fletcher16 covers the first 10 header bytes; ack 0 == OK.

The stage/Z/filter motors are AllMotion EZStepper drivers. Their ASCII commands
("/<addr><tokens>R\\r") are tunneled through the board's MOTOR_CMD_QUERY_WLEN opcode,
wrapped in an OEM frame (STX + addr + '1' + tokens + ETX + xor-checksum).

Connection: FTDI via libftdi (pylibftdi) at 230400 baud, 8 data bits, no parity, 1 stop
bit. libftdi claims the FTDI device directly, so the kernel ``ftdi_sio`` driver must not
hold it (any ``/dev/ttyUSB*`` for this board goes away while the driver is connected).
"""

import asyncio
import concurrent.futures
import contextlib
import hashlib
import json
import logging
import math
import os
import struct
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple, cast

from pylabrobot.celigo.camera import CameraFrame, CeligoCamera, LumeneraCamera
from pylabrobot.celigo.config import (
  AxisConfig,
  Calibrated2DPolynomialTransform,
  CalibrationConfig,
  CeligoHardwareConfig,
  FilterWheelConfig,
  GalvoOpticalCalibration,
  HardwareDefaultConfig,
  IOChannelConfig,
  IlluminationChannelConfig,
  load_galvo_calibrations,
  load_galvo_optical_calibration,
  load_illumination_channels,
)
from pylabrobot.celigo.coordinates import CoordinateSystems
from pylabrobot.celigo.navigation import (
  NavigationConfig,
  galvo_fov_offsets_mm,
  well_to_encoder_ticks,
)
from pylabrobot.celigo.transforms import (
  encoder_ticks_to_mm,
  galvo_mm_to_volts,
  mm_to_encoder_ticks,
)
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources.plate import Plate

logger = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 230400

# Board command opcodes (byte 0 of every packet).
_CMD_LOAD_FIRING_TABLE = 1
_CMD_ABORT = 3
_CMD_FIRE_GALVO_GRID = 6
_CMD_MOVE_GALVO = 7
_CMD_SEND_MOTOR_CONFIG = 9
_CMD_SEND_GALVO_INFO = 12
_CMD_TARGETED_FIRE = 13
_CMD_READ_DIG_PORT = 15
_CMD_SET_DIG_PORT_BITS = 16
_CMD_CLEAR_DIG_PORT_BITS = 17
_CMD_WRITE_DA_CHANNEL = 18
_CMD_READ_AD_CHANNEL = 20
_CMD_SEND_CONFIG = 22
_CMD_CONTROLLER_STATUS = 23
_CMD_FIRE_LASER = 24
_CMD_RESET_CONTROLLER = 25
_CMD_SEND_LASER_COMM = 26
_CMD_CALIBRATE_GALVO = 27
_CMD_GET_GALVO_CAL_DATA = 28
_CMD_SET_GALVO_WINDOW = 29
_CMD_GET_GALVO_POS_DATA = 31
_CMD_READ_LASER_COMM = 32
_CMD_GET_DIG_OUT_VALUE = 34
_CMD_GET_ANALOG_OUT_VALUE = 35
_CMD_AUTO_FOCUS = 36
_CMD_SEND_FOCUS_POINTS = 37
_CMD_TRIGGERED_ACQUISITION = 42
_CMD_SIGNAL_DIAGNOSTICS = 43
_CMD_MOTOR_CMD_QUERY = 44
_CMD_SEND_BARCODE_MSG = 45
_CMD_READ_BARCODE_MSG = 46
_CMD_MOTOR_CMD_QUERY_WLEN = 47

# SIGNAL_DIAGNOSTICS sub-commands (camera trigger / status line).
_DIAG_SET_TRIGGER = 1
_DIAG_CLEAR_TRIGGER = 2
_DIAG_PULSE_TRIGGER = 3
_DIAG_READ_BUSY = 4
_DIAG_READ_INTEGRATION = 5
_DIAG_READ_ENCODER = 6

# The WLEN/OEM motor-tunnel path (opcode 47) requires this firmware version; older
# firmware uses the DT path (opcode 44, ASCII + NUL).
_MOTOR_WLEN_MIN_FIRMWARE = (1, 3, 0)

# Extended controller status values returned by the motor-query commands.
_EXT_NO_CONTROLLER_ERROR = 0
_EXT_NO_MOTOR_NUMBER = 5011
_EXT_BAD_MOTOR_NUMBER = 5012
_EXT_MOTOR_COMM_ERROR = 5025
_MOTOR_QUERY_ATTEMPTS = 5
_MOTOR_COMMAND_MAX_BYTES = 512
_MAX_RESPONSE_PAYLOAD_BYTES = 65535

_GALVO_POLYNOMIAL_TERMS = frozenset(
  {
    "OffsetTerm",
    "LinearXTerm",
    "LinearYTerm",
    "QuadraticXTerm",
    "CrossTerm",
    "QuadraticYTerm",
    "CubicXTerm",
    "CubicYTerm",
    "QuadraticXLinearYTerm",
    "QuadraticYLinearXTerm",
  }
)
_GALVO_CUBIC_TERMS = frozenset(
  {"CubicXTerm", "CubicYTerm", "QuadraticXLinearYTerm", "QuadraticYLinearXTerm"}
)

# Hardware-autofocus encoder scale (Z encoder ticks -> mm).
AUTOFOCUS_MM_PER_TICK = 0.000396319

# Response ack status byte.
_ACK_OK = 0
_ACK_MESSAGES = {
  1: "Invalid command checksum",
  2: "Invalid command",
  3: "Command read failed",
  4: "Command rejected",
  5: "Invalid parameter",
}
# Ack codes worth retrying after flushing the input buffer.
_ACK_RETRYABLE = frozenset({1, 3})

_TX_HEADER_SIZE = 11
_RX_HEADER_SIZE = 12

# Controller status flags returned by CONTROLLER_STATUS.
_STATUS_BUSY = 1
_STATUS_ERROR = 2
_STATUS_INTERLOCK_OPEN = 4
_STATUS_CONTROLLER_FAIL = 8

Axis = Literal["x", "y", "z", "filter"]
Galvo = Literal["x", "y"]
OpticalAxis = Literal[
  "beam_expander",
  "camera_filter",
  "dichroic_filter",
  "door",
  "excitation_filter",
  "excitation_nd_filter",
  "laser_attenuator",
  "laser_nd_filter",
  "magnification",
]

_AXIS_INDEX: Dict[str, int] = {"x": 1, "y": 2, "z": 3, "filter": 4}
_GALVO_INDEX: Dict[str, int] = {"x": 0, "y": 1}

# 16-bit galvo DAC: 0 V sits at the midpoint, 3276.75 counts per volt.
_DAC_ZERO_VOLTS = 32767.5
_DAC_PER_VOLT = 3276.75
# 12-bit per-channel analog DAC full scale.
_ANALOG_DAC_FULL_SCALE = 4095.0

# AllMotion status byte: 0x20 set == ready, low nibble == error code.
_EZ_READY_BIT = 0x20
_EZ_ERROR_MASK = 0x0F

# EZStepper ASCII command codes.
_EZ_MOVE_ABSOLUTE = "A"
_EZ_MOVE_POSITIVE = "P"
_EZ_MOVE_NEGATIVE = "D"
_EZ_HOME = "Z"
_EZ_SET_VELOCITY = "V"
_EZ_SET_ACCELERATION = "L"
_EZ_SET_MOVE_CURRENT = "m"
_EZ_SET_HOLD_CURRENT = "h"
_EZ_SET_POSITION = "z"
_EZ_SET_POLARITY = "f"
_EZ_SET_POSITIVE_DIRECTION = "F"
_EZ_SET_SPECIAL_MODE = "N"
_EZ_SET_MODE = "n"
_EZ_SET_ENCODER_RATIO = "aE"
_EZ_SET_OVERLOAD_TIMEOUT = "au"
_EZ_SET_COARSE_WINDOW = "aC"
_EZ_SET_FINE_WINDOW = "ac"
_EZ_SET_INTEGRATION_PERIOD = "x"
_EZ_SET_RESPONSE_TIME = "aP"
_EZ_SET_BACKLASH = "K"
_EZ_SET_S_CURVE = "aj"
_EZ_TERMINATE = "T"
_EZ_QUERY_FIRMWARE = "&"
_EZ_QUERY = "?"
_EZ_QUERY_STATUS = "Q"
_EZ_QUERY_ENCODER_POSITION = 8
_EZ_QUERY_FLAGS = 4

_EZ_MODE_ENABLE_LIMITS = 0x02
_EZ_MODE_ENABLE_POSITION_CORRECTION = 0x08
_EZ_MODE_ENABLE_STEP_AND_DIRECTION = 0x20
_EZ_MODE_ENABLE_MOTOR_SLAVE_TO_ENCODER = 0x40

_EZ_SPECIAL_ENCODER_NO_INDEX = 1
_EZ_SPECIAL_ENCODER_WITH_INDEX = 2
_EZ_SPECIAL_ENCODER_WITH_INDEX_ACCURATE = 6

_LIMIT_OPTO_1 = 0x04
_LIMIT_OPTO_2 = 0x08
_LIMIT_ALL = 0x1F

_STX = "\x02"
_ETX = "\x03"

# Per-axis motion defaults: (velocity, acceleration, move_current, hold_current). Move
# currents are set high enough not to stall (a stall lets the stepper count drift from
# the encoder).
_MOTION = {
  "x": (3543, 3543, 65, None),
  "y": (3543, 3543, 75, None),
  "z": (5000, 5000, 50, 25),
  "filter": (3543, 3543, None, None),
}

Channel = str


@dataclass(frozen=True)
class DeviceInfo:
  """Board identity from SEND_CONFIG: device index, firmware version, UART buffer size."""

  device_index: int
  firmware_version: Tuple[int, int, int]  # (major, minor, build)
  uart_buffer_length: int


@dataclass(frozen=True)
class ControllerStatus:
  """Decoded controller status returned by :meth:`Celigo.request_status`."""

  raw_flags: int
  extended_status: int = 0

  @property
  def busy(self) -> bool:
    return bool(self.raw_flags & _STATUS_BUSY)

  @property
  def error(self) -> bool:
    return bool(self.raw_flags & _STATUS_ERROR)

  @property
  def interlock_open(self) -> bool:
    return bool(self.raw_flags & _STATUS_INTERLOCK_OPEN)

  @property
  def controller_failed(self) -> bool:
    return bool(self.raw_flags & _STATUS_CONTROLLER_FAIL)

  @property
  def has_safety_fault(self) -> bool:
    """Whether a controller error, open interlock, or controller failure is active."""
    return self.error or self.interlock_open or self.controller_failed


@dataclass(frozen=True)
class GalvoStatus:
  """Galvo readback from SEND_GALVO_INFO: per-axis busy flag and current position."""

  x_busy: bool
  y_busy: bool
  x_volts: float
  y_volts: float


@dataclass(frozen=True)
class ShootingStatus:
  """Laser target-table/firing state embedded in SEND_GALVO_INFO."""

  fire_table_size: int
  points_loaded: int
  fire_table_index: int
  firing_status: int
  galvo_capture_armed: bool
  galvo_capture_table_size: int


@dataclass(frozen=True)
class FocusResult:
  """Best Z position and the scored frames inspected by host-side autofocus."""

  z_ticks: int
  z_mm: float
  score: float
  samples: Tuple[Tuple[int, float], ...]
  frame: CameraFrame


@dataclass(frozen=True)
class AcquisitionResult:
  """A captured frame plus motion/optical metadata used for the acquisition."""

  well: str
  channel: str
  x_ticks: int
  y_ticks: int
  z_ticks: int
  x_mm: float
  y_mm: float
  z_mm: float
  frame: CameraFrame
  focus: Optional[FocusResult] = None
  galvo_volts: Tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class DiagnosticReport:
  """Read-only or active controller self-test results."""

  passed: bool
  checks: Dict[str, Any]
  failures: Tuple[str, ...]


class CeligoError(Exception):
  """Raised when the controller NACKs a command or a reply is malformed."""


def _fletcher16(data: bytes, length: int) -> tuple:
  """Fletcher-16 checksum: seeds 0xFF/0xFF, folded in 21-byte blocks."""
  s1 = 0xFF
  s2 = 0xFF
  i = 0
  remaining = length
  while remaining > 0:
    block = min(21, remaining)
    remaining -= block
    while block > 0:
      s1 = (s1 + data[i]) & 0xFFFF
      i += 1
      s2 = (s2 + s1) & 0xFFFF
      block -= 1
    s1 = (s1 & 0xFF) + (s1 >> 8)
    s2 = (s2 & 0xFF) + (s2 >> 8)
  s1 = (s1 & 0xFF) + (s1 >> 8)
  s2 = (s2 & 0xFF) + (s2 >> 8)
  return s1 & 0xFF, s2 & 0xFF


def _build_tx_packet(opcode: int, sequence: int, payload: bytes = b"") -> bytes:
  """Serialize a request packet (11-byte header + payload)."""
  header = bytearray(_TX_HEADER_SIZE)
  header[0] = opcode
  struct.pack_into(">i", header, 1, sequence)
  struct.pack_into(">i", header, 5, _TX_HEADER_SIZE + len(payload))
  check_a, check_b = _fletcher16(header, 9)
  header[9] = check_a
  header[10] = check_b
  return bytes(header) + payload


def _require_payload_length(payload: bytes, minimum: int, operation: str) -> None:
  """Reject a truncated controller payload before attempting to unpack it."""
  if len(payload) < minimum:
    raise CeligoError(
      f"Truncated {operation} response: expected at least {minimum} payload bytes, "
      f"got {len(payload)}"
    )


def _volts_to_dac_units(volts: float) -> int:
  """Map a galvo voltage (clamped to +/-10 V) to a 16-bit DAC count."""
  clamped = max(-10.0, min(volts, 10.0))
  return int(min(65535.0, max(0.0, round(clamped * _DAC_PER_VOLT + _DAC_ZERO_VOLTS))))


def _dac_units_to_volts(dac: int) -> float:
  """Inverse of :func:`_volts_to_dac_units` (16-bit galvo DAC)."""
  return (dac - _DAC_ZERO_VOLTS) / _DAC_PER_VOLT


def _volts_to_analog_dac(volts: float, min_voltage: float, max_voltage: float) -> int:
  """Map an in-range voltage to a 12-bit per-channel analog DAC count."""
  if not all(math.isfinite(value) for value in (volts, min_voltage, max_voltage)):
    raise ValueError("analog voltage limits and target must be finite")
  if max_voltage <= min_voltage:
    raise ValueError("analog max_voltage must be greater than min_voltage")
  if not min_voltage <= volts <= max_voltage:
    raise ValueError(f"analog voltage {volts} is outside {min_voltage}..{max_voltage}")
  scaled = (volts - min_voltage) / (max_voltage - min_voltage) * _ANALOG_DAC_FULL_SCALE
  return int(scaled)


def _analog_dac_to_volts(dac: int, min_voltage: float, max_voltage: float) -> float:
  """Inverse of :func:`_volts_to_analog_dac`."""
  return dac / _ANALOG_DAC_FULL_SCALE * (max_voltage - min_voltage) + min_voltage


def _motor_designation(axis_index: int) -> str:
  """Address character for a motor: '1'..'9' for 1-9, else chr(48 + index)."""
  return str(axis_index) if 0 < axis_index < 10 else chr(48 + axis_index)


def _ez_command(axis_index: int, tokens: str, run: bool = True) -> str:
  """Build an EZStepper command string: '/<addr><tokens>[R]\\r'."""
  return f"/{_motor_designation(axis_index)}{tokens}{'R' if run else ''}\r"


def _to_oem_packet(command: str) -> bytes:
  """Wrap '/<addr><tokens>R\\r' in the AllMotion OEM frame with an xor checksum.

  Frame = STX + <addr> + '1' + <tokens> + ETX, followed by a one-byte xor over all of
  those bytes. <addr> is the motor designation ('1'=X, '2'=Y, '3'=Z, '4'=filter); the
  literal '1' after it is the device sub-index.
  """
  start = command.rfind("/")
  end = command.find("\r", start + 1)
  if start < 0 or end <= start + 1:
    raise ValueError(f"Invalid EZStepper command framing: {command!r}")
  rest = command[start + 1 : end]
  addr, tokens = rest[0], rest[1:]
  body = f"{_STX}{addr}1{tokens}{_ETX}".encode("ascii")
  checksum = 0
  for b in body:
    checksum ^= b
  return body + bytes([checksum])


def _from_oem_response(raw: bytes) -> str:
  """Validate and unwrap an OEM-framed reply into the normal ``/<content>`` form."""
  start = raw.rfind(b"\x02")
  if start < 0:
    raise CeligoError("Invalid OEM motor response: missing STX")
  end = raw.find(b"\x03", start + 1)
  if end < 0:
    raise CeligoError("Invalid OEM motor response: missing ETX")
  if end - start - 1 < 2:
    raise CeligoError("Invalid OEM motor response: payload is too short")
  if end + 1 >= len(raw):
    raise CeligoError("Invalid OEM motor response: missing checksum")

  calculated = 0
  for value in raw[start : end + 1]:
    calculated ^= value
  received = raw[end + 1]
  if received != calculated:
    raise CeligoError(
      f"OEM motor response checksum failure: received {received:#04x}, calculated {calculated:#04x}"
    )
  return "/" + raw[start + 1 : end].decode("latin-1")


class _EZResponse:
  """Parsed AllMotion reply: ready/busy flag, error code, and data payload."""

  def __init__(self, ready: bool, error: int, data: str):
    self.ready = ready
    self.error = error
    self.data = data

  @property
  def ok(self) -> bool:
    return self.error == 0


def _parse_ez_response(raw: str) -> _EZResponse:
  """Parse an AllMotion reply string.

  The reply carries a '/0' master-address prefix followed by a status byte and data.
  Locate the status byte after '/0' (falling back to the first byte with 0x40 set).
  """
  idx = raw.find("/0")
  if idx >= 0 and idx + 2 < len(raw):
    status_pos = idx + 2
  else:
    found = next((i for i, ch in enumerate(raw) if ord(ch) & 0x40), None)
    if found is None:
      raise CeligoError(f"No EZStepper status byte in reply: {raw!r}")
    status_pos = found
  status = ord(raw[status_pos])
  data = raw[status_pos + 1 :]
  for term in (_ETX, "\r", "\n"):
    cut = data.find(term)
    if cut >= 0:
      data = data[:cut]
  return _EZResponse(bool(status & _EZ_READY_BIT), status & _EZ_ERROR_MASK, data)


class Celigo:
  """Celigo image cytometer motion/illumination controller.

  Talks to the FTDI-based USB-IO board over serial. Exposes stage/Z motion in
  millimeters, drawer open/close (stage eject/load), imaging-channel selection
  (brightfield + fluorescence), galvo steering, and the board's digital/analog IO and
  barcode reader.
  """

  def __init__(
    self,
    device_id: Optional[str] = None,
    usb_address: Optional[str] = None,
    vid: int = 0x0403,
    pid: int = 0x6001,
    baudrate: int = DEFAULT_BAUDRATE,
    latency_ms: int = 2,
    reply_timeout: float = 2.0,
    move_timeout: float = 30.0,
    config: Optional[CeligoHardwareConfig] = None,
    install_dir: Optional[str] = None,
    channels: Optional[Dict[str, IlluminationChannelConfig]] = None,
    calibration: Optional[CalibrationConfig] = None,
    hardware_defaults: Optional[HardwareDefaultConfig] = None,
    load_well: str = "A1",
    magnification: int = 3,
    filter_home_position: Optional[int] = None,
    lucam_sdk: Optional[str] = None,
    galvo_calibrations: Optional[Dict[int, Calibrated2DPolynomialTransform]] = None,
    galvo_optical_calibration: Optional[GalvoOpticalCalibration] = None,
    navigation: Optional[NavigationConfig] = None,
    allow_laser: bool = False,
    fluorescence_warmup_seconds: float = 300.0,
    fluorescence_power_change_interval: float = 10.0,
  ):
    if magnification not in (3, 5, 10, 20):
      raise ValueError("magnification must be one of 3, 5, 10, or 20")
    self.baudrate = baudrate
    self.latency_ms = latency_ms
    self.reply_timeout = reply_timeout
    self.move_timeout = move_timeout
    if config is None and (install_dir is not None or os.environ.get("CELIGO_INSTALL_DIR")):
      config = CeligoHardwareConfig.from_install(install_dir)
    config_root = install_dir
    if config_root is not None and os.path.isfile(config_root):
      config_root = os.path.dirname(config_root)
    if config_root is None and config is not None and config.source_path is not None:
      config_root = os.path.dirname(config.source_path)
    leap_calibration_path: Optional[str] = None
    if config_root is not None and (channels is None or galvo_optical_calibration is None):
      try:
        leap_calibration_path = CeligoHardwareConfig.locate_config_file(
          config_root, "leaphardwarecalibration.config"
        )
      except FileNotFoundError:
        pass
    if channels is None and leap_calibration_path is not None:
      channels = load_illumination_channels(
        leap_calibration_path,
        magnification=magnification,
      )
    if galvo_optical_calibration is None and leap_calibration_path is not None:
      galvo_optical_calibration = load_galvo_optical_calibration(leap_calibration_path)
    if calibration is None and config_root is not None:
      try:
        calibration = CalibrationConfig.from_xml(
          CeligoHardwareConfig.locate_config_file(config_root, "CalibrationConfig.xml")
        )
      except FileNotFoundError:
        pass
    if hardware_defaults is None:
      try:
        hardware_defaults = HardwareDefaultConfig.from_xml(
          CeligoHardwareConfig.locate_config_file(config_root, "HardwareDefaultConfig.xml")
        )
      except FileNotFoundError:
        pass
    if galvo_calibrations is None and config_root is not None:
      try:
        galvo_calibrations = load_galvo_calibrations(
          CeligoHardwareConfig.locate_config_file(config_root, "GalvoCalibrationConfig.xml")
        )
      except FileNotFoundError:
        pass
    if navigation is None and config_root is not None:
      try:
        navigation = NavigationConfig.from_xml(
          CeligoHardwareConfig.locate_config_file(config_root, "NavigationConfig.xml")
        )
      except FileNotFoundError:
        pass
    self.config = config
    self.channels = channels or {}
    self.calibration = calibration
    self.hardware_defaults = hardware_defaults
    self.plate: Optional[Plate] = None
    self.load_well = load_well
    self.magnification = magnification
    self.camera: LumeneraCamera = LumeneraCamera(sdk_library=lucam_sdk)
    self.galvo_calibrations = galvo_calibrations or {}
    self.galvo_optical_calibration = galvo_optical_calibration
    self.navigation = navigation
    self.allow_laser = allow_laser
    self.fluorescence_warmup_seconds = fluorescence_warmup_seconds
    self.fluorescence_power_change_interval = fluorescence_power_change_interval
    self.current_channel: Optional[str] = None
    self._connected = False
    self._filter_home_position = filter_home_position
    self._discrete_home_positions: Dict[str, int] = {}
    if filter_home_position is not None:
      self._discrete_home_positions["dichroic_filter"] = filter_home_position
    self._initialized_motor_axes: set[int] = set()
    self._trusted_axes: set[Axis] = set()
    self._motor_firmware_versions: Dict[int, float] = {}
    has_lamp_power = bool(
      config is not None
      and config.io is not None
      and any(output.io_name == "ExcitationLampPower" for output in config.io.digital_ios)
    )
    self._fluorescence_powered = not has_lamp_power
    self._fluorescence_on_since: Optional[float] = 0.0 if not has_lamp_power else None
    self._last_fluorescence_power_change: Optional[float] = None
    self.device_info: Optional[DeviceInfo] = None
    self._motor_wlen = True  # set from firmware version at setup
    self._seq = 1
    self._lock = asyncio.Lock()
    self.io = FTDI(
      human_readable_device_name="Celigo",
      device_id=device_id,
      usb_address=usb_address,
      vid=vid,
      pid=pid,
    )

  def _require_config(self) -> CeligoHardwareConfig:
    if self.config is None:
      raise CeligoError(
        "This operation requires Celigo hardware configuration; pass install_dir= or config="
      )
    return self.config

  async def _complete_cleanup(self, operation: Awaitable[Any]) -> Any:
    """Finish a safety cleanup before propagating cancellation."""
    task = asyncio.ensure_future(operation)
    try:
      return await asyncio.shield(task)
    except asyncio.CancelledError:
      with contextlib.suppress(Exception):
        await task
      raise

  def _axis_config(self, axis: Axis) -> Optional[AxisConfig]:
    if self.config is None:
      return None
    configured = {
      "x": self.config.x_axis,
      "y": self.config.y_axis,
      "z": self.config.z_axis,
      "filter": self.config.dichroic_filter_wheel,
    }[axis]
    return configured

  def _axis_index(self, axis: Axis) -> int:
    """Return the configured motor address, with legacy defaults for config-free use."""
    configured = self._axis_config(axis)
    if configured is not None and configured.axis_index > 0:
      return configured.axis_index
    return _AXIS_INDEX[axis]

  @staticmethod
  def _axis_bounds_ticks(axis: AxisConfig) -> Tuple[int, int]:
    if axis.mm_per_encoder_tick <= 0:
      raise CeligoError(f"{axis.motion_name or 'axis'} has invalid mm_per_encoder_tick")
    if axis.max_position <= axis.min_position:
      raise CeligoError(f"{axis.motion_name or 'axis'} has invalid configured position bounds")
    first = mm_to_encoder_ticks(axis.min_position, axis)
    second = mm_to_encoder_ticks(axis.max_position, axis)
    return (min(first, second), max(first, second))

  def _validate_axis_target(self, axis: AxisConfig, target: int) -> None:
    if isinstance(axis, FilterWheelConfig):
      return
    bounds = self._axis_bounds_ticks(axis)
    low, high = bounds
    if not low <= target <= high:
      raise CeligoError(
        f"{axis.motion_name or f'motor {axis.axis_index}'} target {target} is outside "
        f"configured encoder range {low}..{high}"
      )

  def _optical_axis_config(self, component: OpticalAxis) -> AxisConfig:
    """Resolve a Celigo-family optical component without assuming it exists."""
    config = self._require_config()
    configured: Dict[str, Optional[AxisConfig]] = {
      "beam_expander": config.beam_expander,
      "camera_filter": config.camera_filter_wheel,
      "dichroic_filter": config.dichroic_filter_wheel,
      "door": config.door,
      "excitation_filter": config.excitation_filter_wheel,
      "excitation_nd_filter": config.excitation_nd_filter_wheel,
      "laser_attenuator": config.laser_attenuator,
      "laser_nd_filter": config.laser_nd_filter_wheel,
      "magnification": config.magnification_changer,
    }
    axis = configured[component]
    if axis is None or not axis.enabled or axis.axis_index <= 0:
      raise CeligoError(f"Optical component {component!r} is not configured on this instrument")
    return axis

  def _all_configured_axes(self) -> List[AxisConfig]:
    config = self._require_config()
    axes = [
      config.x_axis,
      config.y_axis,
      config.z_axis,
      config.dichroic_filter_wheel,
      config.beam_expander,
      config.camera_filter_wheel,
      config.door,
      config.excitation_filter_wheel,
      config.excitation_nd_filter_wheel,
      config.laser_attenuator,
      config.laser_nd_filter_wheel,
      config.magnification_changer,
    ]
    by_index: Dict[int, AxisConfig] = {}
    for axis in axes:
      if axis is not None and axis.enabled and axis.axis_index > 0:
        by_index[axis.axis_index] = axis
    return [by_index[index] for index in sorted(by_index)]

  def _motion_profile(self, axis: Axis) -> Tuple[int, int, Optional[int], Optional[int]]:
    axis_config = self._axis_config(axis)
    if axis_config is None:
      return _MOTION[axis]
    if axis_config.mm_per_encoder_tick > 0:
      velocity = round(axis_config.max_velocity / axis_config.mm_per_encoder_tick)
      acceleration = round(axis_config.max_acceleration / axis_config.mm_per_encoder_tick)
    else:
      velocity = round(axis_config.max_velocity)
      acceleration = round(axis_config.max_acceleration)
    return (
      velocity,
      acceleration,
      axis_config.moving_current_percentage or None,
      axis_config.holding_current_percentage or None,
    )

  def _io_channel(self, name: str, collection: str) -> IOChannelConfig:
    config = self._require_config()
    if config.io is None:
      raise CeligoError("Celigo IO configuration is missing")
    configured_collections = {
      "analog_ins": config.io.analog_ins,
      "digital_ios": config.io.digital_ios,
      "lighting_ios": config.io.lighting_ios,
    }
    try:
      io_configs = configured_collections[collection]
    except KeyError as exc:
      raise ValueError(f"Unknown IO collection: {collection}") from exc
    for io_config in io_configs:
      if io_config.io_name == name:
        return io_config
    raise CeligoError(f"Celigo IO configuration has no {name!r} entry")

  def _channel_config(self, channel: str) -> IlluminationChannelConfig:
    try:
      return self.channels[channel]
    except KeyError as exc:
      raise CeligoError(
        f"Channel {channel!r} is not configured; available channels: "
        f"{', '.join(sorted(self.channels)) or 'none'}"
      ) from exc

  def _optional_io_channel(self, name: str, collection: str) -> Optional[IOChannelConfig]:
    try:
      return self._io_channel(name, collection)
    except CeligoError:
      return None

  # -- lifecycle -------------------------------------------------------------

  async def setup(self) -> None:
    logger.warning(
      "Celigo controller, homing, drawer, galvo, brightfield, and raw camera paths have "
      "limited live-hardware verification. Fluorescence, calibrated camera acquisition, "
      "autofocus, triggered acquisition, and laser operations remain unverified."
    )
    io_open = False
    try:
      await self.io.setup()
      io_open = True
      await self.io.set_baudrate(self.baudrate)
      await self.io.set_line_property(8, 0, 0)  # 8 data bits, 1 stop bit, no parity
      await self.io.set_latency_timer(self.latency_ms)
      await self.io.usb_purge_rx_buffer()
      await self.io.usb_purge_tx_buffer()
      for _ in range(2):
        with contextlib.suppress(CeligoError):
          await self.abort()
      # The first command after opening can drop; read status a few times to warm up.
      status: Optional[ControllerStatus] = None
      last_status_error: Optional[CeligoError] = None
      for _ in range(3):
        try:
          status = await self.request_status()
          break
        except CeligoError as exc:
          last_status_error = exc
          await asyncio.sleep(0.1)
      if status is None:
        raise CeligoError("Celigo did not return a valid controller status") from last_status_error

      # Identity is required to choose the correct motor-tunnel framing safely.
      self.device_info = await self.request_device_info()
      self._motor_wlen = self.device_info.firmware_version >= _MOTOR_WLEN_MIN_FIRMWARE
      if self.config is not None:
        await self.initialize_hardware()
      if self.camera is not None:
        await self.camera.setup()
        await self._configure_camera_for_calibration()
    except BaseException:
      if io_open:
        with contextlib.suppress(Exception):
          await self.abort()
        with contextlib.suppress(Exception):
          await self.initialize_safe_outputs()
      if self.camera is not None:
        with contextlib.suppress(Exception):
          await self.camera.stop()
      if io_open:
        with contextlib.suppress(Exception):
          await self.io.stop()
      raise
    self._connected = True
    logger.info("[Celigo] connected (status=%s, %s)", status, self.device_info)

  async def stop(self) -> None:
    first_error: Optional[BaseException] = None

    async def attempt(operation) -> None:
      nonlocal first_error
      try:
        await operation()
      except BaseException as exc:
        if first_error is None:
          first_error = exc

    if getattr(self, "_connected", False):
      await attempt(self.abort)
      await attempt(self.initialize_safe_outputs)
    if self.camera is not None:
      await attempt(self.camera.stop)
    await attempt(self.io.stop)
    self._connected = False
    if first_error is not None:
      raise first_error

  # -- packet layer ----------------------------------------------------------

  @contextlib.asynccontextmanager
  async def _reply_timeout(self, timeout: float):
    """Temporarily use a longer reply timeout (for blocking, long-running board commands)."""
    previous = self.reply_timeout
    self.reply_timeout = timeout
    try:
      yield
    finally:
      self.reply_timeout = previous

  async def _read_exact(self, n: int) -> bytes:
    chunks = []
    remaining = n
    deadline = time.monotonic() + self.reply_timeout
    while remaining > 0:
      chunk = await self.io.read(remaining)
      if chunk:
        chunks.append(chunk)
        remaining -= len(chunk)
        continue
      if time.monotonic() >= deadline:
        break
      await asyncio.sleep(0.001)
    buf = b"".join(chunks)
    if len(buf) != n:
      raise CeligoError(f"Short read: expected {n} bytes, got {len(buf)}")
    return buf

  async def _transact(self, opcode: int, payload: bytes = b"", retries: int = 3) -> bytes:
    """Send a command and return its response payload (b'' if there is none)."""
    async with self._lock:
      self._seq += 1
      sequence = self._seq
      tx = _build_tx_packet(opcode, sequence, payload)
      for attempt in range(retries):
        written = await self.io.write(tx)
        if written != len(tx):
          await self.io.usb_purge_rx_buffer()
          await self.io.usb_purge_tx_buffer()
          raise CeligoError(f"Short write: expected {len(tx)} bytes, wrote {written}")
        try:
          return await self._read_response(opcode, sequence)
        except CeligoError as exc:
          # Purge after any failed read so leftover bytes can't desync the next command.
          await self.io.usb_purge_rx_buffer()
          await self.io.usb_purge_tx_buffer()
          if getattr(exc, "ack", None) in _ACK_RETRYABLE and attempt < retries - 1:
            continue
          raise
    raise CeligoError("unreachable")

  async def _read_response(self, opcode: int, sequence: int) -> bytes:
    header = await self._read_exact(_RX_HEADER_SIZE)
    ack = header[0]
    echo_opcode = header[1]
    echo_seq = struct.unpack_from(">i", header, 2)[0]
    payload_length = struct.unpack_from(">i", header, 6)[0]

    if (header[10], header[11]) != _fletcher16(header, 10):
      raise CeligoError(f"Response checksum failure for opcode {opcode}, sequence {sequence}")

    if ack != _ACK_OK:
      err = CeligoError(f"{_ACK_MESSAGES.get(ack, f'Unknown ack {ack}')} (opcode {opcode})")
      err.ack = ack  # type: ignore[attr-defined]
      raise err

    if echo_opcode != opcode:
      raise CeligoError(f"Reply opcode mismatch: expected {opcode}, got {echo_opcode}")
    if echo_seq != sequence:
      raise CeligoError(f"Reply sequence mismatch: expected {sequence}, got {echo_seq}")
    if not 0 <= payload_length <= _MAX_RESPONSE_PAYLOAD_BYTES:
      raise CeligoError(
        f"Invalid response payload length {payload_length}; maximum is "
        f"{_MAX_RESPONSE_PAYLOAD_BYTES} bytes"
      )

    return await self._read_exact(payload_length) if payload_length else b""

  # -- motor layer (EZStepper strings tunneled through the board) ------------

  async def _motor_query(self, command: str) -> str:
    """Send an EZStepper command string and return the device reply.

    Uses the WLEN/OEM path (opcode 47, OEM-framed) on firmware >= 1.3.0.0, else the DT
    path (opcode 44, ASCII + NUL). Both replies share the same framing: uint16 ext-status,
    then uint16 length + that many ASCII bytes.
    """
    encoded = _to_oem_packet(command) if self._motor_wlen else command.encode("ascii")
    if len(encoded) > _MOTOR_COMMAND_MAX_BYTES:
      raise ValueError(
        f"Motor command is {len(encoded)} bytes; maximum is {_MOTOR_COMMAND_MAX_BYTES}"
      )
    payload = encoded if self._motor_wlen else encoded + b"\x00"
    opcode = _CMD_MOTOR_CMD_QUERY_WLEN if self._motor_wlen else _CMD_MOTOR_CMD_QUERY
    attempts = _MOTOR_QUERY_ATTEMPTS if self._motor_wlen else 1

    for attempt in range(attempts):
      resp = await self._transact(opcode, payload)
      _require_payload_length(resp, 2, "motor query")
      (ext,) = struct.unpack_from(">H", resp, 0)
      if ext in (_EXT_NO_MOTOR_NUMBER, _EXT_BAD_MOTOR_NUMBER):
        raise CeligoError(f"Invalid motor number (status {ext}) for command {command!r}")
      if ext == _EXT_MOTOR_COMM_ERROR:
        if self._motor_wlen and attempt < attempts - 1:
          continue
        raise CeligoError(f"Motor communication error for command {command!r}")
      if ext != _EXT_NO_CONTROLLER_ERROR:
        raise CeligoError(f"Unexpected motor status {ext} for command {command!r}")

      _require_payload_length(resp, 4, "motor query")
      (length,) = struct.unpack_from(">H", resp, 2)
      _require_payload_length(resp, 4 + length, "motor query")
      reply = resp[4 : 4 + length]
      if not self._motor_wlen:
        return reply.decode("latin-1")
      try:
        return _from_oem_response(reply)
      except CeligoError:
        if attempt == attempts - 1:
          raise

    raise CeligoError(f"Motor query failed after {attempts} attempts: {command!r}")

  async def _send_ez(self, command: str) -> _EZResponse:
    return _parse_ez_response(await self._motor_query(command))

  def _move_tokens(
    self, axis: Axis, move_code: str, arg: int, move_current: Optional[int] = None
  ) -> str:
    velocity, acceleration, default_current, hold_current = self._motion_profile(axis)
    current = default_current if move_current is None else move_current
    tokens = ""
    if current is not None:
      tokens += f"{_EZ_SET_MOVE_CURRENT}{current}"
    if hold_current is not None:
      tokens += f"{_EZ_SET_HOLD_CURRENT}{hold_current}"
    tokens += f"{_EZ_SET_VELOCITY}{velocity}{_EZ_SET_ACCELERATION}{acceleration}"
    tokens += f"{move_code}{arg}"
    return tokens

  # -- status / encoders -----------------------------------------------------

  async def request_status(self) -> ControllerStatus:
    """Request and decode the current controller status."""
    resp = await self._transact(_CMD_CONTROLLER_STATUS)
    _require_payload_length(resp, 8, "controller status")
    flags, extended_status = struct.unpack_from(">II", resp, 0)
    return ControllerStatus(flags, extended_status)

  async def request_device_info(self) -> DeviceInfo:
    """Read board identity (SEND_CONFIG): device index, firmware version, UART buffer size."""
    resp = await self._transact(_CMD_SEND_CONFIG)
    _require_payload_length(resp, 10, "device info")
    device_index, fw, uart_len = struct.unpack_from(">hii", resp, 0)
    version = ((fw >> 16) & 0xFF, (fw >> 8) & 0xFF, fw & 0xFF)
    return DeviceInfo(
      device_index=device_index, firmware_version=version, uart_buffer_length=uart_len
    )

  async def request_is_interlock_open(self) -> bool:
    """Whether the controller reports the safety interlock switch as open."""
    return (await self.request_status()).interlock_open

  async def request_is_busy(self) -> bool:
    """Whether the controller reports the BUSY flag."""
    return (await self.request_status()).busy

  async def wait_for_ready(self, timeout: float = 5.0, poll: float = 0.01) -> bool:
    """Poll status until the controller BUSY flag clears; return False on timeout."""
    deadline = time.monotonic() + timeout
    while await self.request_is_busy():
      if time.monotonic() >= deadline:
        return False
      await asyncio.sleep(poll)
    return True

  async def request_encoder(self, axis: Axis) -> int:
    """Read one axis's encoder position in ticks."""
    resp = await self._send_ez(
      _ez_command(self._axis_index(axis), f"{_EZ_QUERY}{_EZ_QUERY_ENCODER_POSITION}", run=False)
    )
    if not resp.ok:
      raise CeligoError(f"axis {axis} encoder query failed (code {resp.error})")
    return int(resp.data)

  async def request_encoders(self) -> Dict[str, int]:
    """Read the encoder position of every axis."""
    return {axis: await self.request_encoder(cast(Axis, axis)) for axis in _AXIS_INDEX}

  async def request_motor_map(self) -> List[Tuple[int, int]]:
    """List motors present as (uart_index, motor_index); a slot value of 127 is empty."""
    resp = await self._transact(_CMD_SEND_MOTOR_CONFIG)
    _require_payload_length(resp, 40, "motor configuration")
    motors: List[Tuple[int, int]] = []
    offset = 0
    for uart in range(8):
      offset += 1  # per-UART status byte
      for _ in range(4):
        slot = resp[offset]
        offset += 1
        if slot != 127:
          motors.append((uart, slot))
    return motors

  async def request_motor_count(self) -> int:
    """Number of motors the board reports present."""
    return len(await self.request_motor_map())

  @staticmethod
  def _mode_from_config(axis: AxisConfig, position_correction: bool = True) -> int:
    mode = 0
    if axis.mode_enable_limits:
      mode |= _EZ_MODE_ENABLE_LIMITS
    if position_correction and axis.mode_enable_position_correction:
      mode |= _EZ_MODE_ENABLE_POSITION_CORRECTION
    if axis.mode_enable_step_and_direction:
      mode |= _EZ_MODE_ENABLE_STEP_AND_DIRECTION
    if axis.mode_enable_motor_slave_to_encoder:
      mode |= _EZ_MODE_ENABLE_MOTOR_SLAVE_TO_ENCODER
    return mode

  @staticmethod
  def _configured_velocity(axis: AxisConfig, value: float) -> int:
    if axis.mm_per_encoder_tick > 0:
      return round(value / axis.mm_per_encoder_tick)
    return round(value)

  async def request_motor_firmware(self, axis_index: int) -> str:
    """Read an EZStepper's firmware identification string."""
    response = await self._send_ez(_ez_command(axis_index, _EZ_QUERY_FIRMWARE, run=False))
    if not response.ok:
      raise CeligoError(f"motor {axis_index} firmware query failed (code {response.error})")
    return response.data

  @staticmethod
  def _motor_firmware_version(response: str) -> float:
    for token in response.replace(",", " ").split():
      if token[:1].lower() != "v":
        continue
      try:
        return float(token[1:])
      except ValueError:
        continue
    raise CeligoError(f"Could not parse EZStepper firmware response {response!r}")

  async def request_encoder_ratio(self, axis_index: int) -> int:
    """Read the configured EZStepper encoder ratio (scaled by 1000)."""
    response = await self._send_ez(
      _ez_command(axis_index, f"{_EZ_QUERY}{_EZ_SET_ENCODER_RATIO}", run=False)
    )
    if not response.ok:
      raise CeligoError(f"motor {axis_index} encoder-ratio query failed (code {response.error})")
    return int(response.data)

  async def initialize_motor(self, axis: AxisConfig) -> None:
    """Replay the vendor's per-motor initialization string from hardware config."""
    if not axis.enabled or axis.axis_index <= 0:
      return
    firmware = self._motor_firmware_version(await self.request_motor_firmware(axis.axis_index))
    self._motor_firmware_versions[axis.axis_index] = firmware
    stop = await self._send_ez(_ez_command(axis.axis_index, _EZ_TERMINATE, run=False))
    if not stop.ok:
      raise CeligoError(f"motor {axis.axis_index} stop failed (code {stop.error})")

    if firmware >= 7.12:
      special = await self._send_ez(_ez_command(axis.axis_index, f"{_EZ_SET_SPECIAL_MODE}32"))
      if not special.ok:
        raise CeligoError(
          f"motor {axis.axis_index} special-mode initialization failed (code {special.error})"
        )

    velocity = self._configured_velocity(axis, axis.max_velocity)
    acceleration = self._configured_velocity(axis, axis.max_acceleration)
    tokens = (
      f"{_EZ_SET_POSITIVE_DIRECTION}{0 if axis.default_positive_direction else 1}"
      f"{_EZ_SET_POLARITY}{axis.limit_polarity}"
      f"{_EZ_SET_MOVE_CURRENT}{axis.moving_current_percentage}"
      f"{_EZ_SET_HOLD_CURRENT}{axis.holding_current_percentage}"
      f"{_EZ_SET_ENCODER_RATIO}{round(axis.encoder_to_motor_tick_ratio * 1000)}"
    )
    if axis.mode_enable_position_correction:
      tokens += (
        f"{_EZ_SET_OVERLOAD_TIMEOUT}{axis.moving_overload_limit}"
        f"{_EZ_SET_COARSE_WINDOW}{axis.course_position_error_window}"
        f"{_EZ_SET_FINE_WINDOW}{axis.fine_position_error_window}"
        f"{_EZ_SET_INTEGRATION_PERIOD}{axis.gain}"
      )
    tokens += (
      f"{_EZ_SET_VELOCITY}{velocity}{_EZ_SET_ACCELERATION}{acceleration}"
      f"{_EZ_SET_RESPONSE_TIME}{axis.motor_response_time}"
    )
    response: Optional[_EZResponse] = None
    last_error: Optional[CeligoError] = None
    for attempt in range(5):
      try:
        response = await self._send_ez(_ez_command(axis.axis_index, tokens))
        if response.ok:
          break
        last_error = CeligoError(
          f"motor {axis.axis_index} initialization failed (code {response.error})"
        )
      except CeligoError as exc:
        last_error = exc
      if attempt < 4:
        await asyncio.sleep(0.1)
    if response is None or not response.ok:
      raise CeligoError(
        f"motor {axis.axis_index} initialization failed after five attempts"
      ) from last_error
    initial_mode = self._mode_from_config(axis, position_correction=False)
    response = await self._send_ez(_ez_command(axis.axis_index, f"{_EZ_SET_MODE}{initial_mode}"))
    if not response.ok:
      raise CeligoError(f"motor {axis.axis_index} mode setup failed (code {response.error})")
    if axis.s_curve_support:
      response = await self._send_ez(
        _ez_command(axis.axis_index, f"{_EZ_SET_S_CURVE}{axis.max_s_acceleration}")
      )
      if not response.ok:
        raise CeligoError(f"motor {axis.axis_index} S-curve setup failed (code {response.error})")
    self._initialized_motor_axes.add(axis.axis_index)

  async def initialize_hardware(self, calibrate_galvos: bool = True) -> None:
    """Run the non-homing portion of the captured Celigo power-on sequence.

    This aborts stale operations, discovers the board/motors, configures galvo settling
    windows, replays every configured motor profile, and optionally calibrates both
    galvos. It changes controller configuration but does not intentionally move a motor.
    """
    await self.abort()
    await self.abort()
    # The vendor reads identity three times during startup; setup() already performed one.
    for _ in range(2):
      self.device_info = await self.request_device_info()
    await self.request_motor_map()
    config = self._require_config()
    getattr(self, "_trusted_axes", set()).clear()
    await self.initialize_safe_outputs()
    for name, galvo_config in (("x", config.x_galvo), ("y", config.y_galvo)):
      if galvo_config is not None and galvo_config.enabled:
        await self.set_galvo_window(
          cast(Galvo, name),
          galvo_config.position_error_window,
          galvo_config.velocity_error_window,
        )
    for axis in self._all_configured_axes():
      await self.initialize_motor(axis)
    if calibrate_galvos:
      configured_galvos = (
        ("x", config.x_galvo),
        ("y", config.y_galvo),
      )
      for galvo_name, galvo_config in configured_galvos:
        if galvo_config is None or not galvo_config.enabled:
          continue
        for _ in range(2):
          if not await self.calibrate_galvo(cast(Galvo, galvo_name), timeout_ms=900):
            raise CeligoError(f"{galvo_name.upper()} galvo calibration failed")
      await self.home_galvos(magnification=3)

  async def initialize_safe_outputs(self) -> None:
    """Put every controller output in the vendor startup's inactive state."""
    for channel in range(4):
      await self.write_dac(channel, 0)
    for bit in range(12):
      await self.set_digital_output(bit, False)
    self.current_channel = None
    config = getattr(self, "config", None)
    lamp_power = None
    if config is not None and config.io is not None:
      lamp_power = next(
        (output for output in config.io.digital_ios if output.io_name == "ExcitationLampPower"),
        None,
      )
    # Raw output zero means logical ``invert`` for a controllable lamp; without a
    # power line the source is always powered.
    self._fluorescence_powered = True if lamp_power is None else lamp_power.invert
    self._fluorescence_on_since = 0.0 if self._fluorescence_powered else None
    self._last_fluorescence_power_change = None

  async def abort(self) -> None:
    """Abort the current controller command."""
    await self._transact(_CMD_ABORT)
    await asyncio.sleep(0.05)

  async def reset(self) -> None:
    """Reset the controller board."""
    await self._transact(_CMD_RESET_CONTROLLER)

  # -- digital & analog IO ---------------------------------------------------

  async def request_digital_inputs(self) -> int:
    """Read the digital input port as a raw bitmask."""
    resp = await self._transact(_CMD_READ_DIG_PORT)
    _require_payload_length(resp, 2, "digital input")
    return int(struct.unpack_from(">H", resp, 0)[0])

  async def request_digital_input(self, bit: int) -> bool:
    """Read one digital input line."""
    if not 0 <= bit < 12:
      raise ValueError("digital bit must be in 0..11")
    return bool(await self.request_digital_inputs() & (1 << bit))

  async def request_digital_outputs(self) -> int:
    """Read back the digital output register as a raw bitmask."""
    resp = await self._transact(_CMD_GET_DIG_OUT_VALUE)
    _require_payload_length(resp, 2, "digital output")
    return int(struct.unpack_from(">H", resp, 0)[0])

  async def request_digital_output(self, bit: int) -> bool:
    """Read back one digital output line."""
    if not 0 <= bit < 12:
      raise ValueError("digital bit must be in 0..11")
    return bool(await self.request_digital_outputs() & (1 << bit))

  async def set_digital_output(self, bit: int, on: bool) -> None:
    """Set one digital output line on or off."""
    if not 0 <= bit < 12:
      raise ValueError("digital bit must be in 0..11")
    mask = 1 << bit
    opcode = _CMD_SET_DIG_PORT_BITS if on else _CMD_CLEAR_DIG_PORT_BITS
    await self._transact(opcode, struct.pack(">H", mask))

  async def write_dac(self, channel: int, count: int) -> None:
    """Write a raw 12-bit count to an analog output (DAC) channel."""
    if not 0 <= channel < 4:
      raise ValueError("analog output channel must be in 0..3")
    if not 0 <= count <= 0x0FFF:
      raise ValueError("DAC count must be in 0..4095")
    await self._transact(_CMD_WRITE_DA_CHANNEL, struct.pack(">HH", channel, count))

  async def request_dac(self, channel: int) -> int:
    """Read back an analog output (DAC) channel's raw count."""
    if not 0 <= channel < 4:
      raise ValueError("analog output channel must be in 0..3")
    resp = await self._transact(_CMD_GET_ANALOG_OUT_VALUE, struct.pack(">H", channel))
    _require_payload_length(resp, 4, "analog output")
    _echo, value = struct.unpack_from(">HH", resp, 0)
    return int(value)

  async def request_adc(self, channel: int) -> int:
    """Read an analog input (ADC) channel's raw count (e.g. a sensor)."""
    if not 0 <= channel < 4:
      raise ValueError("analog input channel must be in 0..3")
    resp = await self._transact(_CMD_READ_AD_CHANNEL, struct.pack(">H", channel))
    _require_payload_length(resp, 2, "analog input")
    return int(struct.unpack_from(">H", resp, 0)[0])

  async def set_analog_out(
    self, channel: int, voltage: float, min_voltage: float, max_voltage: float
  ) -> None:
    """Set an analog output channel to a voltage (per-channel min/max calibration)."""
    await self.write_dac(channel, _volts_to_analog_dac(voltage, min_voltage, max_voltage))

  async def request_analog_output(
    self, channel: int, min_voltage: float, max_voltage: float
  ) -> float:
    """Read back an analog output channel as a voltage."""
    return _analog_dac_to_volts(await self.request_dac(channel), min_voltage, max_voltage)

  async def request_analog_input(
    self, channel: int, min_voltage: float, max_voltage: float
  ) -> float:
    """Read an analog input channel as a voltage."""
    return _analog_dac_to_volts(await self.request_adc(channel), min_voltage, max_voltage)

  # -- barcode ---------------------------------------------------------------

  async def send_barcode_command(self, command: str) -> None:
    """Send an ASCII command to the barcode reader.

    On this build the barcode UART is shared with the front-panel status display.
    """
    await self._transact(_CMD_SEND_BARCODE_MSG, command.encode("ascii") + b"\x00")

  async def request_barcode(self) -> str:
    """Read the barcode reader's ASCII response."""
    resp = await self._transact(_CMD_READ_BARCODE_MSG)
    _require_payload_length(resp, 4, "barcode")
    length = struct.unpack_from(">H", resp, 2)[0]
    _require_payload_length(resp, 4 + length, "barcode")
    return resp[4 : 4 + length].decode("ascii", errors="replace")

  # -- motion ----------------------------------------------------------------

  async def _wait_ready(self, axis: Axis, timeout: Optional[float] = None) -> int:
    """Poll an axis until its EZStepper status reports ready; return its encoder pos."""
    deadline = time.monotonic() + (timeout if timeout is not None else self.move_timeout)
    while time.monotonic() < deadline:
      response = await self._send_ez(
        _ez_command(self._axis_index(axis), _EZ_QUERY_STATUS, run=False)
      )
      if not response.ok:
        raise CeligoError(f"axis {axis} reported motor error {response.error}")
      if response.ready:
        return await self.request_encoder(axis)
      await asyncio.sleep(0.1)
    raise TimeoutError(f"axis {axis} not ready within timeout")

  async def _get_encoder_for_config(self, axis: AxisConfig) -> int:
    response = await self._send_ez(
      _ez_command(axis.axis_index, f"{_EZ_QUERY}{_EZ_QUERY_ENCODER_POSITION}", run=False)
    )
    if not response.ok:
      raise CeligoError(f"motor {axis.axis_index} encoder query failed (code {response.error})")
    return int(response.data)

  async def _wait_configured_axis_ready(
    self, axis: AxisConfig, timeout: Optional[float] = None
  ) -> int:
    deadline = time.monotonic() + (timeout if timeout is not None else self.move_timeout)
    while time.monotonic() < deadline:
      response = await self._send_ez(_ez_command(axis.axis_index, _EZ_QUERY_STATUS, run=False))
      if not response.ok:
        raise CeligoError(f"motor {axis.axis_index} reported error {response.error}")
      if response.ready:
        return await self._get_encoder_for_config(axis)
      await asyncio.sleep(0.05)
    raise TimeoutError(f"motor {axis.axis_index} not ready within timeout")

  async def request_limit_flags(self, axis: AxisConfig) -> int:
    """Read and polarity-correct an EZStepper's opto/limit input flags."""
    response = await self._send_ez(
      _ez_command(axis.axis_index, f"{_EZ_QUERY}{_EZ_QUERY_FLAGS}", run=False)
    )
    if not response.ok:
      raise CeligoError(f"motor {axis.axis_index} limit query failed (code {response.error})")
    flags = int(response.data) & _LIMIT_ALL
    if axis.limit_polarity == 1:
      flags = (~flags) & _LIMIT_ALL
    return flags

  async def _set_motor_mode(self, axis: AxisConfig, mode: int) -> None:
    response = await self._send_ez(_ez_command(axis.axis_index, f"{_EZ_SET_MODE}{mode}"))
    if not response.ok:
      raise CeligoError(f"motor {axis.axis_index} mode change failed (code {response.error})")

  async def _send_homing_relative(
    self, axis: AxisConfig, positive: bool, distance: int, velocity: int
  ) -> int:
    """Run one bounded, config-driven relative move used only by a homing routine."""
    if distance <= 0:
      raise ValueError("homing distance must be positive")
    acceleration = self._configured_velocity(axis, axis.max_acceleration)
    direction = _EZ_MOVE_POSITIVE if positive else _EZ_MOVE_NEGATIVE
    response = await self._send_ez(
      _ez_command(
        axis.axis_index,
        f"{_EZ_SET_ACCELERATION}{acceleration}{_EZ_SET_VELOCITY}{velocity}{direction}{distance}",
      )
    )
    if not response.ok:
      raise CeligoError(f"motor {axis.axis_index} homing move failed (code {response.error})")
    timeout = max(self.move_timeout, distance / max(1, velocity) + 2.0)
    return await self._wait_configured_axis_ready(axis, timeout)

  async def _set_homing_motor_parameter(
    self, axis: AxisConfig, token: str, value: int, description: str
  ) -> None:
    response = await self._send_ez(_ez_command(axis.axis_index, f"{token}{value}"))
    if not response.ok:
      raise CeligoError(f"motor {axis.axis_index} {description} failed (code {response.error})")

  async def _restore_homing_configuration(self, axis: AxisConfig) -> None:
    await self._set_homing_motor_parameter(
      axis, _EZ_SET_BACKLASH, axis.backlash_compensation, "backlash restore"
    )
    if axis.s_curve_support:
      await self._set_homing_motor_parameter(
        axis, _EZ_SET_S_CURVE, axis.max_s_acceleration, "S-curve restore"
      )
    await self._set_motor_mode(axis, self._mode_from_config(axis))

  async def _home_to_encoder_index(
    self,
    axis: AxisConfig,
    search_distance: int,
    velocity: int,
    special_mode: int,
    timeout: Optional[float] = None,
    restore_mode: Optional[int] = None,
  ) -> int:
    await self._set_motor_mode(axis, 0)
    try:
      acceleration = self._configured_velocity(axis, axis.max_acceleration)
      response = await self._send_ez(
        _ez_command(
          axis.axis_index,
          f"{_EZ_SET_ACCELERATION}{acceleration}{_EZ_SET_VELOCITY}{velocity}"
          f"{_EZ_SET_SPECIAL_MODE}{special_mode}{_EZ_HOME}{search_distance}",
        )
      )
      if not response.ok:
        raise CeligoError(f"motor {axis.axis_index} index home failed (code {response.error})")
      return await self._wait_configured_axis_ready(axis, timeout)
    except BaseException:
      with contextlib.suppress(Exception):
        await self._complete_cleanup(
          self._send_ez(_ez_command(axis.axis_index, _EZ_TERMINATE, run=False))
        )
      raise
    finally:
      mode = self._mode_from_config(axis) if restore_mode is None else restore_mode
      await self._complete_cleanup(self._set_motor_mode(axis, mode))

  async def _move_configured_absolute(
    self,
    axis: AxisConfig,
    target: int,
    velocity: Optional[int] = None,
    verify_arrival: bool = True,
    validate_target: bool = True,
    arrival_tolerance: Optional[int] = None,
  ) -> int:
    if validate_target:
      self._validate_axis_target(axis, target)
    velocity = velocity or self._configured_velocity(axis, axis.max_velocity)
    acceleration = self._configured_velocity(axis, axis.max_acceleration)
    hold = min(50, axis.moving_current_percentage)
    tolerance = (
      max(0, axis.fine_position_error_window)
      if arrival_tolerance is None
      else max(0, arrival_tolerance)
    )
    attempts = 3 if verify_arrival else 1
    last_position: Optional[int] = None
    last_error: Optional[BaseException] = None
    for attempt in range(attempts):
      try:
        response = await self._send_ez(
          _ez_command(
            axis.axis_index,
            f"{_EZ_SET_HOLD_CURRENT}{hold}{_EZ_SET_ACCELERATION}{acceleration}"
            f"{_EZ_SET_VELOCITY}{velocity}{_EZ_MOVE_ABSOLUTE}{target}",
          )
        )
        if not response.ok:
          raise CeligoError(f"motor {axis.axis_index} move failed (code {response.error})")
        last_position = await self._wait_configured_axis_ready(axis)
      except BaseException as exc:
        last_error = exc
        with contextlib.suppress(Exception):
          await self._complete_cleanup(
            self._send_ez(
              _ez_command(
                axis.axis_index,
                f"{_EZ_SET_HOLD_CURRENT}{axis.holding_current_percentage}",
              )
            )
          )
        if isinstance(exc, (CeligoError, TimeoutError)) and attempt + 1 < attempts:
          continue
        raise
      restore = await self._complete_cleanup(
        self._send_ez(
          _ez_command(
            axis.axis_index,
            f"{_EZ_SET_HOLD_CURRENT}{axis.holding_current_percentage}",
          )
        )
      )
      if not restore.ok:
        raise CeligoError(f"motor {axis.axis_index} hold-current restore failed")
      if not verify_arrival or abs(last_position - target) <= tolerance:
        return last_position
    if last_error is not None:
      raise last_error
    raise CeligoError(
      f"motor {axis.axis_index} stopped at {last_position}, target {target}, tolerance {tolerance}"
    )

  async def _move_ticks(
    self, axis: Axis, ticks: int, wait: bool = True, tolerance: Optional[int] = None
  ) -> int:
    """Absolute low-level move of an axis to an encoder-tick target.

    When ``wait`` is set, arrival is verified against the encoder (not just the ready
    flag) and the settled position is returned.
    """
    axis_config = self._axis_config(axis)
    if axis_config is None:
      raise CeligoError(f"axis {axis} has no hardware configuration")
    self._validate_axis_target(axis_config, ticks)
    if axis in ("x", "y", "z") and axis not in getattr(self, "_trusted_axes", set()):
      raise CeligoError(
        f"axis {axis} position is not trusted; call await home({axis!r}) before moving it"
      )
    if not wait:
      raise CeligoError("unverified asynchronous axis moves are disabled")
    if tolerance is not None and tolerance > axis_config.fine_position_error_window:
      raise CeligoError("requested tolerance exceeds the configured fine-position window")
    return await self._move_configured_absolute(
      axis_config, ticks, arrival_tolerance=tolerance
    )

  async def move(
    self,
    axis: Literal["x", "y", "z"],
    position_mm: float,
    wait: bool = True,
    tolerance_mm: Optional[float] = None,
  ) -> float:
    """Move a linear axis to an absolute position in millimeters."""
    axis_config = self._axis_config(axis)
    if axis_config is None:
      raise CeligoError(f"axis {axis} has no hardware configuration")
    if axis_config.mm_per_encoder_tick <= 0:
      raise CeligoError(f"axis {axis} has invalid mm_per_encoder_tick")
    low_mm, high_mm = sorted((axis_config.min_position, axis_config.max_position))
    if not low_mm <= position_mm <= high_mm:
      raise CeligoError(
        f"axis {axis} target {position_mm:g} mm is outside configured range "
        f"{low_mm:g}..{high_mm:g} mm"
      )
    ticks = mm_to_encoder_ticks(position_mm, axis_config)
    tolerance_ticks = None
    if tolerance_mm is not None:
      if tolerance_mm < 0:
        raise ValueError("tolerance_mm must be non-negative")
      tolerance_ticks = round(tolerance_mm / axis_config.mm_per_encoder_tick)
    settled_ticks = await self._move_ticks(axis, ticks, wait=wait, tolerance=tolerance_ticks)
    return encoder_ticks_to_mm(settled_ticks, axis_config)

  async def trust_vendor_homed_axis(self, axis: Literal["x", "y", "z"]) -> int:
    """Verify bounds and restore configured mode for an axis homed by vendor software."""
    configured = self._axis_config(axis)
    if configured is None:
      raise CeligoError(f"axis {axis} is not configured")
    position = await self.request_encoder(axis)
    self._validate_axis_target(configured, position)
    await self._set_motor_mode(configured, self._mode_from_config(configured))
    self._trusted_axes.add(axis)
    return position

  async def move_z(self, position_mm: float, wait: bool = True) -> float:
    """Move the Z/focus axis to an absolute position in millimeters."""
    return await self.move("z", position_mm, wait=wait)

  async def _move_z_ticks(self, ticks: int, wait: bool = True) -> int:
    """Move Z using the controller's native unit for internal scan calculations."""
    return await self._move_ticks("z", ticks, wait=wait)

  async def home(self, axis: Axis, velocity: int = 3000) -> int:
    """Home one configured axis using its vendor-defined homing algorithm.

    X/Y ``Normal_Accurate`` axes seek and back off the negative limit, then establish
    encoder zero at the accurate index. Z ``NormalWithHardstopCheck`` requires the
    negative limit (rather than a hard stop), backs off, and establishes zero with the
    no-index home mode. Every linear axis then moves to its configured minimum position.
    """
    del velocity
    axis_config = self._axis_config(axis)
    if axis_config is not None and axis == "filter" and axis_config.home_type == "Filter_Accurate":
      return await self.home_filter_accurate()
    if axis not in ("x", "y", "z") or axis_config is None or not axis_config.enabled:
      raise CeligoError(f"axis {axis!r} is not configured for homing")
    supported = {
      "Normal",
      "Normal_Accurate",
      "NormalWithHardstopCheck",
      "NormalWithHardstopCheck_Accurate",
    }
    if axis_config.home_type not in supported:
      raise CeligoError(f"axis {axis!r} has unsupported home type {axis_config.home_type!r}")
    if not axis_config.mode_enable_limits or not axis_config.negative_limit:
      raise CeligoError(f"axis {axis!r} homing requires a configured negative limit")
    if axis_config.homing_short_move <= 0:
      raise CeligoError(f"axis {axis!r} has an invalid homing backoff distance")

    initialized: set[int] = getattr(self, "_initialized_motor_axes", set())
    if axis_config.axis_index not in initialized:
      await self.initialize_motor(axis_config)
    self._trusted_axes.discard(axis)
    prehome_mode = self._mode_from_config(axis_config, position_correction=False)
    max_velocity = self._configured_velocity(axis_config, axis_config.max_velocity)
    homing_velocity = self._configured_velocity(axis_config, axis_config.homing_velocity)
    index_velocity = self._configured_velocity(axis_config, axis_config.index_velocity)

    async def terminate_and_restore() -> None:
      with contextlib.suppress(Exception):
        await self._complete_cleanup(
          self._send_ez(_ez_command(axis_config.axis_index, _EZ_TERMINATE, run=False))
        )
      with contextlib.suppress(Exception):
        await self._complete_cleanup(self._restore_homing_configuration(axis_config))

    try:
      await self._set_motor_mode(axis_config, prehome_mode)
      if axis_config.s_curve_support:
        await self._set_homing_motor_parameter(axis_config, _EZ_SET_S_CURVE, 0, "S-curve disable")
      await self._set_homing_motor_parameter(axis_config, _EZ_SET_BACKLASH, 0, "backlash disable")

      # Match the controller's pre-home encoder liveness check.
      initial = await self._get_encoder_for_config(axis_config)
      await self._send_homing_relative(axis_config, True, 5, max_velocity)
      if await self._get_encoder_for_config(axis_config) == initial:
        await self._send_homing_relative(axis_config, False, 10, max_velocity)
        if await self._get_encoder_for_config(axis_config) == initial:
          raise CeligoError(f"axis {axis!r} encoder did not respond to the homing probe")

      # Seek the negative limit and prove that the sensor, not a hard stop, ended the move.
      await self._send_homing_relative(axis_config, False, 25000, homing_velocity)
      if not (await self.request_limit_flags(axis_config) & _LIMIT_OPTO_1):
        raise CeligoError(f"axis {axis!r} stopped without activating its negative-limit sensor")
      await asyncio.sleep(0.05)
      await self._send_homing_relative(
        axis_config, True, axis_config.homing_short_move, homing_velocity
      )
      if (await self.request_limit_flags(axis_config)) & _LIMIT_OPTO_1:
        raise CeligoError(f"axis {axis!r} negative-limit sensor did not clear after backoff")
      await asyncio.sleep(0.05)

      if axis_config.home_type.startswith("NormalWithHardstopCheck"):
        search_distance = 25000
        special_mode = _EZ_SPECIAL_ENCODER_NO_INDEX
      else:
        search_distance = axis_config.homing_short_move * 2
        firmware = getattr(self, "_motor_firmware_versions", {}).get(axis_config.axis_index, 7.16)
        accurate = axis_config.home_type == "Normal_Accurate"
        special_mode = (
          _EZ_SPECIAL_ENCODER_WITH_INDEX_ACCURATE
          if accurate and firmware >= 7.16
          else _EZ_SPECIAL_ENCODER_WITH_INDEX
        )

      await self._home_to_encoder_index(
        axis_config,
        search_distance,
        index_velocity,
        special_mode,
        timeout=max(
          self.move_timeout,
          search_distance / max(1, index_velocity) + 2.0,
        ),
        restore_mode=prehome_mode,
      )
      await self._move_configured_absolute(
        axis_config,
        0,
        velocity=max_velocity,
        validate_target=False,
      )
      await self._restore_homing_configuration(axis_config)
      minimum = mm_to_encoder_ticks(axis_config.min_position, axis_config)
      settled = await self._move_configured_absolute(axis_config, minimum)
      self._trusted_axes.add(axis)
      return settled
    except BaseException:
      self._trusted_axes.discard(axis)
      await terminate_and_restore()
      raise

  async def home_filter_accurate(self, component: OpticalAxis = "dichroic_filter") -> int:
    """Reference a filter wheel by encoder index, then locate its physical opto tab.

    This is the vendor ``Filter_Accurate`` algorithm: use accurate index mode, restore
    configured correction, and inspect each equally spaced physical position until
    Opto1 becomes active. Three index-search attempts are made before failing.
    """
    axis = self._optical_axis_config(component)
    if not isinstance(axis, FilterWheelConfig):
      raise CeligoError(f"Optical component {component!r} is not a filter wheel")
    if axis.number_of_filters <= 0 or axis.number_of_encoder_tick_per_rev <= 0:
      raise CeligoError(f"Optical component {component!r} has invalid wheel geometry")
    if axis.number_of_encoder_tick_per_rev % axis.number_of_filters:
      raise CeligoError(f"Optical component {component!r} has fractional filter spacing")

    # A failed or cancelled re-home must never leave the old datum usable.
    self._discrete_home_positions.pop(component, None)
    if component == "dichroic_filter":
      self._filter_home_position = None

    ticks_per_filter = axis.number_of_encoder_tick_per_rev // axis.number_of_filters
    search_distance = round(ticks_per_filter * 1.2)
    index_velocity = self._configured_velocity(axis, axis.index_velocity)
    last_error: Optional[Exception] = None
    firmware = getattr(self, "_motor_firmware_versions", {}).get(axis.axis_index, 7.16)
    index_mode = (
      _EZ_SPECIAL_ENCODER_WITH_INDEX_ACCURATE
      if firmware >= 7.16
      else _EZ_SPECIAL_ENCODER_WITH_INDEX
    )
    index_timeout = max(
      self.move_timeout,
      abs(search_distance) / max(1, abs(index_velocity)) + 1.0,
    )
    for _ in range(3):
      try:
        await self._home_to_encoder_index(
          axis,
          search_distance,
          index_velocity,
          index_mode,
          timeout=index_timeout,
        )
        last_error = None
        break
      except (CeligoError, TimeoutError) as exc:
        last_error = exc
    if last_error is not None:
      await self.initialize_motor(axis)
      raise CeligoError(f"Failed to find encoder index for {component}") from last_error

    target = round(axis.home_offset)
    homing_velocity = self._configured_velocity(axis, axis.homing_velocity)
    try:
      for physical in range(1, axis.number_of_filters + 1):
        await self._move_configured_absolute(axis, target, homing_velocity)
        if await self.request_limit_flags(axis) & _LIMIT_OPTO_1:
          self._discrete_home_positions[component] = target
          if component == "dichroic_filter":
            self._filter_home_position = target
          return target
        if physical < axis.number_of_filters:
          target += ticks_per_filter
    except BaseException:
      with contextlib.suppress(Exception):
        await self._complete_cleanup(self.initialize_motor(axis))
      raise
    await self.initialize_motor(axis)
    raise CeligoError(f"Opto1 sensor was not active at any {component} position")

  async def home_galvos(
    self,
    magnification: Optional[int] = None,
    logical_filter: Optional[int] = None,
  ) -> Tuple[float, float]:
    """Move both galvos to their calibrated imaging center."""
    magnification = self.magnification if magnification is None else magnification
    x_center = self._galvo_center_voltage("x", magnification, logical_filter)
    y_center = self._galvo_center_voltage("y", magnification, logical_filter)
    return await self.move_galvos(x_center, y_center)

  async def home_all(self) -> None:
    """Home Z first for clearance, then X, Y, and the dichroic filter wheel."""
    await self.home("z")
    await self.home("x")
    await self.home("y")
    await self.home_filter_accurate()

  def set_filter_home_position(self, ticks: int) -> None:
    """Record the encoder position corresponding to physical filter position 1.

    Normal operation sets this automatically when the filter wheel is homed. This
    method supports attaching to an already-homed instrument without homing it again.
    """
    self._filter_home_position = ticks

  async def move_to_logical_filter(self, logical_filter: int) -> int:
    """Move to a configured logical filter using the shortest equivalent wheel move."""
    return await self.move_to_optical_position("dichroic_filter", logical_filter)

  async def move_optical_component(self, component: OpticalAxis, target: int) -> int:
    """Move an installed Celigo-family optical motor to an encoder target."""
    axis = self._optical_axis_config(component)
    return await self._move_configured_absolute(axis, target)

  async def move_to_optical_position(self, component: OpticalAxis, logical_position: int) -> int:
    """Select a logical position on any configured discrete optical wheel."""
    axis = self._optical_axis_config(component)
    if not isinstance(axis, FilterWheelConfig):
      raise CeligoError(f"Optical component {component!r} is not a discrete wheel")
    if component == "dichroic_filter":
      home_position = self._filter_home_position
    else:
      home_position = self._discrete_home_positions.get(component)
    if home_position is None:
      raise CeligoError(
        f"{component} home position is unknown; home_filter_accurate({component!r}) first"
      )
    if axis.number_of_filters <= 0 or axis.number_of_encoder_tick_per_rev <= 0:
      raise CeligoError(f"{component} wheel geometry is invalid")
    if axis.number_of_encoder_tick_per_rev % axis.number_of_filters != 0:
      raise CeligoError(f"{component} encoder ticks/revolution is not divisible by filter count")
    logical_to_physical = {entry.logical_number: entry.physical_number for entry in axis.filter_map}
    try:
      physical_filter = logical_to_physical[logical_position]
    except KeyError as exc:
      raise CeligoError(
        f"Logical position {logical_position} is not configured for {component}"
      ) from exc

    ticks_per_filter = axis.number_of_encoder_tick_per_rev // axis.number_of_filters
    canonical = home_position + (physical_filter - 1) * ticks_per_filter
    current = (
      await self.request_encoder("filter")
      if component == "dichroic_filter"
      else await self._get_encoder_for_config(axis)
    )
    # Equivalent targets differ by one revolution. On an exact half-revolution tie,
    # choose the lower target, matching the signed moves observed from the vendor app.
    revolutions = math.ceil((current - canonical) / axis.number_of_encoder_tick_per_rev - 0.5)
    target = canonical + revolutions * axis.number_of_encoder_tick_per_rev
    return await self._move_configured_absolute(axis, target)

  async def set_magnification(self, logical_position: int) -> int:
    """Move an installed objective/magnification changer."""
    if logical_position not in (3, 5, 10, 20):
      raise CeligoError(f"Unsupported magnification {logical_position}X")
    source = (
      self.galvo_optical_calibration.source_path
      if self.galvo_optical_calibration is not None
      else None
    )
    if source is None:
      raise CeligoError("Cannot reload magnification-specific channel calibration")
    channels = load_illumination_channels(source, logical_position)
    settled = await self.move_to_optical_position("magnification", logical_position)
    self.magnification = logical_position
    self.channels = channels
    return settled

  async def set_camera_filter(self, logical_position: int) -> int:
    """Move an installed camera filter wheel to a logical position."""
    return await self.move_to_optical_position("camera_filter", logical_position)

  async def set_excitation_filter(self, logical_position: int) -> int:
    """Move an installed excitation filter wheel to a logical position."""
    return await self.move_to_optical_position("excitation_filter", logical_position)

  async def set_excitation_nd_filter(self, logical_position: int) -> int:
    """Move an installed excitation neutral-density wheel."""
    return await self.move_to_optical_position("excitation_nd_filter", logical_position)

  async def set_laser_nd_filter(self, logical_position: int) -> int:
    """Move an installed laser neutral-density wheel."""
    return await self.move_to_optical_position("laser_nd_filter", logical_position)

  async def set_beam_expander(self, encoder_ticks: int) -> int:
    """Move an installed beam expander to an absolute encoder target."""
    return await self.move_optical_component("beam_expander", encoder_ticks)

  async def set_laser_attenuator(self, encoder_ticks: int) -> int:
    """Move an installed laser attenuator to an absolute encoder target."""
    return await self.move_optical_component("laser_attenuator", encoder_ticks)

  async def move_relative(
    self, axis: Axis, steps: int, wait: bool = True, move_current: Optional[int] = None
  ) -> None:
    """Relative move of an axis by a signed step count.

    Used for moves to a hard limit; completion uses the ready flag (a relative target is
    not encoder-verified). ``steps`` must be non-zero (the motor treats a zero relative
    move as an infinite move and rejects it).
    """
    raise CeligoError("public unbounded relative motion is disabled")

  async def _move_relative_to_limit(
    self, axis: Literal["x", "y"], steps: int, move_current: Optional[int] = None
  ) -> None:
    if steps == 0:
      raise ValueError("relative move steps must be non-zero")
    if axis not in self._trusted_axes:
      raise CeligoError(f"axis {axis} position is not trusted")
    code = _EZ_MOVE_POSITIVE if steps > 0 else _EZ_MOVE_NEGATIVE
    resp = await self._send_ez(
      _ez_command(self._axis_index(axis), self._move_tokens(axis, code, abs(steps), move_current))
    )
    if not resp.ok:
      raise CeligoError(f"axis {axis} relative move error (code {resp.error})")
    await self._wait_ready(axis)

  # -- drawer (stage eject / load) -------------------------------------------

  def _limit_move_steps(self, axis: Literal["x", "y"]) -> int:
    config = self._axis_config(axis)
    if config is None or config.mm_per_encoder_tick <= 0:
      raise CeligoError(f"Cannot derive {axis.upper()} limit move without axis configuration")
    travel = abs(config.max_position - config.min_position) / config.mm_per_encoder_tick
    return math.ceil(travel) + abs(config.homing_short_move)

  def _configured_load_position(
    self, plate: Optional[Plate], well: Optional[str]
  ) -> Tuple[int, int, int]:
    config = self._require_config()
    plate = plate or self.plate
    if plate is None:
      raise CeligoError("close_drawer requires a configured plate or plate= argument")
    if self.calibration is None or self.hardware_defaults is None:
      raise CeligoError("close_drawer requires CalibrationConfig and HardwareDefaultConfig")
    if config.x_axis is None or config.y_axis is None:
      raise CeligoError("close_drawer requires configured X and Y axes")
    coordinates = CoordinateSystems.from_config(self.calibration, self.hardware_defaults)
    park_x, park_y = well_to_encoder_ticks(
      plate,
      well or self.load_well,
      coordinates,
      config.x_axis,
      config.y_axis,
    )
    clearance_y = mm_to_encoder_ticks(config.y_axis.min_position, config.y_axis)
    return park_x, clearance_y, park_y

  async def open_drawer(self, eject_steps: Optional[int] = None) -> None:
    """Drive the stage out to the eject station so the plate is accessible.

    Retracts Z, moves Y to its configured clearance coordinate, then drives X negative
    and Y positive to their limit sensors using the lighter loading-pose currents.
    Already-active target limits are not driven again.
    """
    await self.set_brightfield(False)
    z_config = self._axis_config("z")
    if z_config is None:
      raise CeligoError("open_drawer requires a configured Z axis")
    await self.move_z(z_config.min_position)
    x_config = self._axis_config("x")
    y_config = self._axis_config("y")
    if x_config is None or y_config is None:
      raise CeligoError("open_drawer requires configured X and Y axes")
    clearance_y = mm_to_encoder_ticks(y_config.min_position, y_config)
    await self._move_ticks("y", clearance_y)
    x_steps = eject_steps if eject_steps is not None else self._limit_move_steps("x")
    y_steps = eject_steps if eject_steps is not None else self._limit_move_steps("y")
    if x_steps <= 0 or y_steps <= 0:
      raise ValueError("eject_steps must be positive")
    for axis_name, axis_config, steps, limit in (
      ("x", x_config, -x_steps, _LIMIT_OPTO_1),
      ("y", y_config, y_steps, _LIMIT_OPTO_2),
    ):
      for _ in range(3):
        if await self.request_limit_flags(axis_config) & limit:
          break
        await self._move_relative_to_limit(
          cast(Literal["x", "y"], axis_name),
          steps,
          move_current=axis_config.loading_current_percentage,
        )
      else:
        raise CeligoError(f"drawer {axis_name.upper()} limit was not reached")

  async def close_drawer(
    self,
    load_position: Optional[Tuple[int, int, int]] = None,
    plate: Optional[Plate] = None,
    well: Optional[str] = None,
  ) -> None:
    """Move the stage under the optics using calibrated plate/well coordinates."""
    x, y_in, y_settle = load_position or self._configured_load_position(plate, well)
    await self._move_ticks("y", y_in)
    await self._move_ticks("x", x)
    await self._move_ticks("y", y_settle)

  def well_position(self, well: str, plate: Optional[Plate] = None) -> Tuple[int, int]:
    """Return calibrated X/Y encoder targets for a named well."""
    plate = plate or self.plate
    if plate is None:
      raise CeligoError("Well navigation requires a configured plate or plate= argument")
    if self.calibration is None or self.hardware_defaults is None:
      raise CeligoError("Well navigation requires CalibrationConfig and HardwareDefaultConfig")
    config = self._require_config()
    if config.x_axis is None or config.y_axis is None:
      raise CeligoError("Well navigation requires configured X and Y axes")
    coordinates = CoordinateSystems.from_config(self.calibration, self.hardware_defaults)
    x_ticks, y_ticks = well_to_encoder_ticks(plate, well, coordinates, config.x_axis, config.y_axis)
    for name, target, axis in (
      ("X", x_ticks, config.x_axis),
      ("Y", y_ticks, config.y_axis),
    ):
      endpoints = (
        mm_to_encoder_ticks(axis.min_position, axis),
        mm_to_encoder_ticks(axis.max_position, axis),
      )
      low, high = sorted(endpoints)
      if not low <= target <= high:
        raise CeligoError(
          f"Well {well} maps to {name}={target}, outside configured range {low}..{high}"
        )
    return x_ticks, y_ticks

  async def move_to_well(
    self,
    well: str,
    plate: Optional[Plate] = None,
    retract_z: bool = False,
    safe_z_mm: Optional[float] = None,
  ) -> Tuple[int, int]:
    """Move the stage to a calibrated well center and return settled X/Y ticks."""
    x_ticks, y_ticks = self.well_position(well, plate)
    if retract_z:
      if safe_z_mm is None:
        z_config = self._axis_config("z")
        if z_config is None:
          raise CeligoError("retract_z requires a configured Z axis")
        safe_z_mm = z_config.min_position
      await self.move_z(safe_z_mm)
    settled_x = await self._move_ticks("x", x_ticks)
    settled_y = await self._move_ticks("y", y_ticks)
    return settled_x, settled_y

  # -- illumination / channels -----------------------------------------------

  async def _set_named_digital_output(self, name: str, on: bool) -> None:
    output = self._io_channel(name, "digital_ios")
    await self.set_digital_output(output.bit_index, on != output.invert)

  def _default_channel_intensity(self, channel: IlluminationChannelConfig) -> int:
    output = self._io_channel(channel.lighting_io_name, "lighting_ios")
    voltage = (
      output.min_voltage
      + (output.max_voltage - output.min_voltage) * channel.intensity_percent / 100.0
    )
    if output.invert or output.invert_voltage:
      voltage = output.max_voltage - voltage + output.min_voltage
    return _volts_to_analog_dac(voltage, output.min_voltage, output.max_voltage)

  async def set_brightfield(self, on: bool = True) -> int:
    """Turn configured brightfield illumination on/off and return its DAC readback."""
    channel = self._channel_config("brightfield")
    output = self._io_channel(channel.lighting_io_name, "lighting_ios")
    await self.write_dac(output.channel, self._default_channel_intensity(channel) if on else 0)
    return await self.request_dac(output.channel)

  @property
  def fluorescence_warmup_remaining(self) -> float:
    """Seconds remaining in the configured fluorescence-lamp warm-up interval."""
    warmup = getattr(self, "fluorescence_warmup_seconds", 300.0)
    on_since = getattr(self, "_fluorescence_on_since", 0.0)
    if not getattr(self, "_fluorescence_powered", True) or on_since is None:
      return warmup
    elapsed = time.monotonic() - on_since
    return max(0.0, warmup - elapsed)

  @property
  def fluorescence_lamp_ready(self) -> bool:
    return self.fluorescence_warmup_remaining <= 0

  @property
  def can_change_fluorescence_power(self) -> bool:
    last_change = getattr(self, "_last_fluorescence_power_change", None)
    if last_change is None:
      return True
    return bool(
      time.monotonic() - last_change >= getattr(self, "fluorescence_power_change_interval", 10.0)
    )

  async def set_fluorescence_lamp_power(self, on: bool) -> None:
    """Set lamp power while enforcing the vendor's minimum toggle interval.

    Instruments without a configured ``ExcitationLampPower`` output have an
    always-powered source; requesting ``False`` is rejected because there is no line to
    switch it.
    """
    output = self._optional_io_channel("ExcitationLampPower", "digital_ios")
    if output is None:
      if not on:
        raise CeligoError("This instrument has no controllable fluorescence-lamp power line")
      self._fluorescence_powered = True
      if getattr(self, "_fluorescence_on_since", None) is None:
        self._fluorescence_on_since = 0.0
      return
    if on == getattr(self, "_fluorescence_powered", False):
      return
    if not self.can_change_fluorescence_power:
      remaining = getattr(self, "fluorescence_power_change_interval", 10.0) - (
        time.monotonic() - cast(float, self._last_fluorescence_power_change)
      )
      raise CeligoError(f"Fluorescence lamp cannot change power for {remaining:.1f}s")
    if not on:
      await self._set_named_digital_output("FLOnOff", False)
    await self.set_digital_output(output.bit_index, on != output.invert)
    now = time.monotonic()
    self._fluorescence_powered = on
    self._fluorescence_on_since = now if on else None
    self._last_fluorescence_power_change = now

  async def illumination_off(self) -> None:
    """Turn off every configured illumination output and fluorescence strobe."""
    await self._set_named_digital_output("FLOnOff", False)
    config = self._require_config()
    if config.io is None:
      raise CeligoError("Celigo IO configuration is missing")
    for output in config.io.lighting_ios:
      await self.write_dac(output.channel, 0)

  async def select_channel(
    self,
    channel: Channel,
    intensity: Optional[int] = None,
    require_lamp_ready: bool = False,
  ) -> None:
    """Select an imaging channel (dichroic wheel + lamp-select bits + intensity DAC).

    Drops the strobe first (light off while the wheel moves), moves the dichroic filter
    wheel to the channel position, sets the fluorescence lamp-select bits, drives the
    channel's intensity DAC (zeroing the other), then raises the strobe for fluorescence.
    ``intensity`` overrides the channel's default 12-bit DAC count.

    Moves the filter wheel (hardware motion). The power toggle interval is enforced;
    pass ``require_lamp_ready=True`` to enforce the configured warm-up interval too.
    """
    spec = self._channel_config(channel)
    # Strobe low before any warm-up validation or mechanism movement.
    await self._set_named_digital_output("FLOnOff", False)
    if spec.strobe:
      await self.set_fluorescence_lamp_power(True)
      if require_lamp_ready and not self.fluorescence_lamp_ready:
        raise CeligoError(
          f"Fluorescence lamp is warming up ({self.fluorescence_warmup_remaining:.1f}s left)"
        )
    level = self._default_channel_intensity(spec) if intensity is None else intensity
    if not 0 <= level <= int(_ANALOG_DAC_FULL_SCALE):
      raise ValueError(f"intensity must be a 12-bit DAC count, got {level}")
    await self.move_to_logical_filter(spec.logical_filter)
    if getattr(self, "galvo_optical_calibration", None) is not None:
      await self.home_galvos(logical_filter=spec.logical_filter)
    if spec.bit_value is not None:
      # The vendor's BitValue orders the two physical selector lines MSB first.
      await self._set_named_digital_output("FLBit0", bool(spec.bit_value & 0b10))
      await self._set_named_digital_output("FLBit1", bool(spec.bit_value & 0b01))

    output = self._io_channel(spec.lighting_io_name, "lighting_ios")
    config = self._require_config()
    if config.io is None:
      raise CeligoError("Celigo IO configuration is missing")
    for other in config.io.lighting_ios:
      if other.channel != output.channel:
        await self.write_dac(other.channel, 0)
    await self.write_dac(output.channel, level)
    if spec.strobe:
      await self._set_named_digital_output("FLOnOff", True)
    self.current_channel = channel

  # -- galvo -----------------------------------------------------------------

  def _galvo_config(self, galvo: Galvo):
    config = self._require_config()
    axis = config.x_galvo if galvo == "x" else config.y_galvo
    if axis is None or not axis.enabled:
      raise CeligoError(f"{galvo.upper()} galvo is not configured")
    return axis

  def _galvo_center_voltage(
    self,
    galvo: Galvo,
    magnification: int,
    logical_filter: Optional[int],
  ) -> float:
    optical = cast(
      Optional[GalvoOpticalCalibration],
      getattr(self, "galvo_optical_calibration", None),
    )
    if optical is None:
      raise CeligoError("Galvo optical-center calibration is not configured")
    axis = optical.x if galvo == "x" else optical.y
    try:
      center = axis.magnifications[magnification].center_voltage
    except KeyError as exc:
      raise CeligoError(
        f"No {galvo.upper()}-galvo center is calibrated for {magnification}X"
      ) from exc
    if logical_filter is not None:
      center += axis.logical_filter_offsets.get(logical_filter, 0.0)
    return center

  def _galvo_hardware_voltage(self, galvo: Galvo, logical_voltage: float) -> float:
    axis = self._galvo_config(galvo)
    low, high = sorted((axis.min_voltage, axis.max_voltage))
    if not math.isfinite(logical_voltage) or not low <= logical_voltage <= high:
      raise CeligoError(
        f"{galvo.upper()} galvo target {logical_voltage:.6g} V is outside "
        f"configured range {low:.6g}..{high:.6g} V"
      )
    return -logical_voltage if axis.invert_voltage else logical_voltage

  def galvo_targets_for_offset(
    self,
    logical_filter: int,
    offset_mm: Tuple[float, float] = (0.0, 0.0),
  ) -> Tuple[float, float]:
    """Return absolute logical galvo targets for a calibrated field offset."""
    delta_x = delta_y = 0.0
    transform = self.galvo_calibrations.get(logical_filter)
    if transform is not None:
      delta_x, delta_y = galvo_mm_to_volts(transform, *offset_mm)
    elif offset_mm != (0.0, 0.0):
      raise CeligoError(f"No galvo calibration is configured for logical filter {logical_filter}")
    return (
      self._galvo_center_voltage("x", self.magnification, logical_filter) + delta_x,
      self._galvo_center_voltage("y", self.magnification, logical_filter) + delta_y,
    )

  async def move_galvo(
    self,
    galvo: Galvo,
    voltage: float,
    wait: bool = True,
    timeout_ms: int = 6000,
  ) -> float:
    """Move one galvo to an absolute logical voltage and return raw hardware voltage."""
    if not 0 <= timeout_ms <= 0xFFFF:
      raise ValueError("timeout_ms must fit in an unsigned 16-bit value")
    hardware_voltage = self._galvo_hardware_voltage(galvo, voltage)
    payload = struct.pack(
      ">HiHH",
      _GALVO_INDEX[galvo],
      _volts_to_dac_units(hardware_voltage),
      1 if wait else 0,
      timeout_ms if wait else 0,
    )
    if wait:
      # The controller does not reply until the galvo settles or its firmware-side
      # timeout expires. Keep the host deadline beyond that advertised timeout so a
      # valid late response cannot be mistaken for the next command's reply.
      async with self._reply_timeout(max(self.reply_timeout, timeout_ms / 1000.0 + 1.0)):
        response = await self._transact(_CMD_MOVE_GALVO, payload)
    else:
      response = await self._transact(_CMD_MOVE_GALVO, payload)
    if wait:
      _require_payload_length(response, 2, "galvo move")
      if struct.unpack_from(">H", response, 0)[0] != 0:
        raise CeligoError(f"{galvo.upper()} galvo did not settle")
    return hardware_voltage

  async def move_galvos(
    self,
    x_voltage: float,
    y_voltage: float,
    wait: bool = True,
  ) -> Tuple[float, float]:
    """Move both galvos to absolute logical voltages and return raw voltages."""
    x_hardware = await self.move_galvo("x", x_voltage, wait=wait)
    y_hardware = await self.move_galvo("y", y_voltage, wait=wait)
    return x_hardware, y_hardware

  async def request_galvo_status(self) -> GalvoStatus:
    """Read galvo busy flags and positions (SEND_GALVO_INFO)."""
    resp = await self._transact(_CMD_SEND_GALVO_INFO)
    _require_payload_length(resp, 6, "galvo status")
    x_busy, y_busy, x_dac, y_dac = struct.unpack_from(">BBHH", resp, 0)
    return GalvoStatus(
      # Firmware returns zero while a galvo is busy (matching the vendor driver).
      x_busy=x_busy == 0,
      y_busy=y_busy == 0,
      x_volts=(
        -_dac_units_to_volts(x_dac)
        if self._galvo_config("x").invert_voltage
        else _dac_units_to_volts(x_dac)
      ),
      y_volts=(
        -_dac_units_to_volts(y_dac)
        if self._galvo_config("y").invert_voltage
        else _dac_units_to_volts(y_dac)
      ),
    )

  async def request_shooting_status(self) -> ShootingStatus:
    """Read the laser firing-table state embedded in ``SEND_GALVO_INFO``."""
    resp = await self._transact(_CMD_SEND_GALVO_INFO)
    _require_payload_length(resp, 23, "shooting status")
    fire_table_size, loaded, index = struct.unpack_from(">iii", resp, 6)
    firing_status = resp[18]
    capture_armed, capture_size = struct.unpack_from(">hh", resp, 19)
    return ShootingStatus(
      fire_table_size=fire_table_size,
      points_loaded=loaded,
      fire_table_index=index,
      firing_status=firing_status,
      galvo_capture_armed=capture_armed != 0,
      galvo_capture_table_size=capture_size,
    )

  async def set_galvo_window(self, galvo: Galvo, position_error: int, velocity_error: int) -> None:
    """Set a galvo's settling window (position + velocity error tolerance)."""
    payload = struct.pack(">HHH", _GALVO_INDEX[galvo], position_error, velocity_error)
    await self._transact(_CMD_SET_GALVO_WINDOW, payload)

  async def calibrate_galvo(self, galvo: Galvo, timeout_ms: int = 900, wait: bool = True) -> bool:
    """Run a galvo error-signal characterization sweep; return True if it succeeded."""
    if not 0 <= timeout_ms <= 0xFFFF:
      raise ValueError("timeout_ms must fit in an unsigned 16-bit value")
    payload = struct.pack(">HHH", _GALVO_INDEX[galvo], timeout_ms, 1 if wait else 0)
    resp = await self._transact(_CMD_CALIBRATE_GALVO, payload)
    if wait:
      _require_payload_length(resp, 2, "galvo calibration")
      return bool(struct.unpack_from(">H", resp, 0)[0] == 0)
    return True

  async def request_galvo_calibration(self, galvo: Galvo) -> List[Tuple[int, int]]:
    """Read a galvo's calibration error table as a list of (err1, err2) pairs."""
    resp = await self._transact(_CMD_GET_GALVO_CAL_DATA, struct.pack(">H", _GALVO_INDEX[galvo]))
    _require_payload_length(resp, 2, "galvo calibration data")
    (count,) = struct.unpack_from(">h", resp, 0)
    if count < 0:
      raise CeligoError(f"Invalid galvo calibration item count: {count}")
    _require_payload_length(resp, 2 + 4 * count, "galvo calibration data")
    return [struct.unpack_from(">hh", resp, 2 + 4 * i) for i in range(count)]

  async def request_galvo_position_data(self) -> List[Tuple[int, int]]:
    """Read the captured galvo position/move trace as a list of (x, y) pairs."""
    resp = await self._transact(_CMD_GET_GALVO_POS_DATA)
    _require_payload_length(resp, 2, "galvo position data")
    (count,) = struct.unpack_from(">H", resp, 0)
    _require_payload_length(resp, 2 + 4 * count, "galvo position data")
    return [struct.unpack_from(">HH", resp, 2 + 4 * i) for i in range(count)]

  # -- guarded laser operations ---------------------------------------------

  async def _assert_laser_safe(self) -> None:
    if not self.allow_laser:
      raise CeligoError(
        "Laser commands are disabled; construct Celigo(..., allow_laser=True) only after "
        "completing the instrument laser-safety procedure"
      )
    status = await self.request_status()
    if status.has_safety_fault:
      raise CeligoError(
        f"Laser command blocked by controller safety status {status.raw_flags:#x}"
      )

  async def send_laser_command(self, command: str) -> None:
    """Send an ASCII command to the laser UART (safety opt-in required)."""
    await self._assert_laser_safe()
    await self._transact(_CMD_SEND_LASER_COMM, command.encode("ascii") + b"\x00")

  async def request_laser_response(self) -> str:
    """Read an ASCII response from the laser UART."""
    await self._assert_laser_safe()
    resp = await self._transact(_CMD_READ_LASER_COMM)
    _require_payload_length(resp, 4, "laser response")
    length = struct.unpack_from(">H", resp, 2)[0]
    _require_payload_length(resp, 4 + length, "laser response")
    return resp[4 : 4 + length].rstrip(b"\x00").decode("ascii", errors="replace")

  async def fire_laser(self, laser: int, shots: int, delay_10us: int) -> None:
    """Fire one laser without galvo targeting (safety opt-in and interlock required)."""
    if laser not in (0, 1):
      raise ValueError("laser must be 0 (LASER_1) or 1 (LASER_2)")
    if shots <= 0 or delay_10us < 0:
      raise ValueError("shots must be positive and delay_10us non-negative")
    await self._assert_laser_safe()
    await self._transact(_CMD_FIRE_LASER, struct.pack(">Hii", laser, shots, delay_10us))

  async def load_laser_targets(
    self,
    points: List[Tuple[float, float]],
    center_volts: Tuple[float, float],
  ) -> None:
    """Load voltage-offset targets around an explicit logical laser center."""
    if not points:
      raise ValueError("points must not be empty")
    await self._assert_laser_safe()
    payload = struct.pack(">i", len(points))
    for x_voltage, y_voltage in points:
      x_logical = x_voltage + center_volts[0]
      y_logical = y_voltage + center_volts[1]
      x_config = self._galvo_config("x")
      y_config = self._galvo_config("y")
      for name, value, config in (("X", x_logical, x_config), ("Y", y_logical, y_config)):
        low, high = sorted((config.min_voltage, config.max_voltage))
        if not math.isfinite(value) or not low <= value <= high:
          raise CeligoError(f"Laser {name} target {value} V is outside {low}..{high} V")
      x_hardware, y_hardware = x_logical, y_logical
      if x_config.invert_voltage:
        x_hardware = -x_hardware
      if y_config.invert_voltage:
        y_hardware = -y_hardware
      if not all(math.isfinite(value) and -10 <= value <= 10 for value in (x_hardware, y_hardware)):
        raise CeligoError("Laser target is outside the galvo DAC range of -10..10 V")
      payload += struct.pack(
        ">HH",
        _volts_to_dac_units(x_hardware),
        _volts_to_dac_units(y_hardware),
      )
    await self._transact(_CMD_LOAD_FIRING_TABLE, payload)
    if not await self.wait_for_ready(timeout=5.0):
      raise TimeoutError("Controller did not finish loading laser targets")

  async def fire_laser_targets(
    self,
    points: List[Tuple[float, float]],
    laser: int,
    pulses: int,
    delay_between_pulses_10us: int = 0,
    center_volts: Optional[Tuple[float, float]] = None,
  ) -> None:
    """Load and fire a list of galvo targets in firmware-table-sized chunks."""
    if not points:
      raise ValueError("points must not be empty")
    if laser not in (0, 1):
      raise ValueError("laser must be 0 (LASER_1) or 1 (LASER_2)")
    if pulses <= 0 or delay_between_pulses_10us < 0:
      raise ValueError("pulses must be positive and delay non-negative")
    await self._assert_laser_safe()
    if center_volts is None:
      optical = getattr(self, "galvo_optical_calibration", None)
      if optical is None:
        raise CeligoError("Laser-center calibration is unavailable; pass center_volts explicitly")
      center_volts = (
        optical.x.laser_center_voltage if laser == 0 else optical.x.uv_laser_center_voltage,
        optical.y.laser_center_voltage if laser == 0 else optical.y.uv_laser_center_voltage,
      )
    table_size = (await self.request_shooting_status()).fire_table_size
    if table_size <= 0:
      raise CeligoError(f"Controller reported invalid laser firing-table size {table_size}")
    for start in range(0, len(points), table_size):
      chunk = points[start : start + table_size]
      await self.load_laser_targets(chunk, center_volts)
      payload = struct.pack(">HIIH", laser, pulses, delay_between_pulses_10us, 0)
      # Loading and waiting can take long enough for the door/interlock state to change.
      await self._assert_laser_safe()
      await self._transact(_CMD_TARGETED_FIRE, payload)
      if not await self.wait_for_ready(timeout=max(5.0, self.move_timeout)):
        raise TimeoutError("Targeted laser firing did not complete")
      status = await self.request_shooting_status()
      if status.fire_table_index != status.points_loaded:
        raise CeligoError(
          f"Laser firing stopped at target {status.fire_table_index}/{status.points_loaded}"
        )

  async def fire_laser_grid(
    self,
    laser: int,
    spacing_volts: Tuple[float, float],
    size_volts: Tuple[float, float],
    center_volts: Tuple[float, float],
    pulses: int,
    repeats: int,
    delay_between_repeats_ms: int = 0,
    pattern: int = 0x1E,
  ) -> None:
    """Fire a firmware-generated galvo grid (default pattern is the full grid)."""
    if laser not in (0, 1):
      raise ValueError("laser must be 0 (LASER_1) or 1 (LASER_2)")
    if pulses <= 0 or repeats <= 0 or delay_between_repeats_ms < 0:
      raise ValueError("pulses/repeats must be positive and delay non-negative")
    if any(value <= 0 or not math.isfinite(value) for value in (*spacing_volts, *size_volts)):
      raise ValueError("grid spacing and size voltages must be finite and positive")
    x_config = self._galvo_config("x")
    y_config = self._galvo_config("y")
    for name, center, size, config in (
      ("X", center_volts[0], size_volts[0], x_config),
      ("Y", center_volts[1], size_volts[1], y_config),
    ):
      low, high = sorted((config.min_voltage, config.max_voltage))
      if center - size / 2 < low or center + size / 2 > high:
        raise CeligoError(f"Laser grid {name} extent is outside {low}..{high} V")
    hardware_center = (
      -center_volts[0] if x_config.invert_voltage else center_volts[0],
      -center_volts[1] if y_config.invert_voltage else center_volts[1],
    )
    await self._assert_laser_safe()
    scale = _DAC_PER_VOLT
    scaled = [round(value * scale) for value in (*spacing_volts, *size_volts)]
    if any(not 0 <= value <= 0xFFFF for value in scaled):
      raise CeligoError("Laser grid spacing/size exceeds controller encoding range")
    payload = struct.pack(
      ">HHHHHHHiiiiH",
      laser,
      *scaled,
      _volts_to_dac_units(hardware_center[0]),
      _volts_to_dac_units(hardware_center[1]),
      pulses,
      repeats,
      delay_between_repeats_ms * 100,
      pattern,
      0,
    )
    await self._assert_laser_safe()
    await self._transact(_CMD_FIRE_GALVO_GRID, payload)
    if not await self.wait_for_ready(timeout=max(5.0, self.move_timeout)):
      raise TimeoutError("Laser grid firing did not complete")

  # -- autofocus -------------------------------------------------------------

  async def arm_autofocus(
    self, current_encoder: int, start_encoder: int, capture_count: int
  ) -> None:
    """Arm hardware autofocus: sweep Z from ``start_encoder`` capturing ``capture_count`` points.

    Encoder positions are Z ticks (see :data:`AUTOFOCUS_MM_PER_TICK` for the mm scale).
    """
    payload = struct.pack(">iiH", current_encoder, start_encoder, capture_count)
    await self._transact(_CMD_AUTO_FOCUS, payload)

  async def request_autofocus_positions(self) -> List[int]:
    """Read the best-focus Z encoder positions captured by the last autofocus sweep."""
    resp = await self._transact(_CMD_SEND_FOCUS_POINTS)
    _require_payload_length(resp, 2, "autofocus positions")
    (count,) = struct.unpack_from(">h", resp, 0)
    if count < 0:
      raise CeligoError(f"Invalid autofocus position count: {count}")
    _require_payload_length(resp, 2 + 2 * count, "autofocus positions")
    return [struct.unpack_from(">h", resp, 2 + 2 * i)[0] for i in range(count)]

  def _require_camera(self) -> CeligoCamera:
    if self.camera is None:
      raise CeligoError(
        "Camera is unavailable; pass lucam_sdk= or set LUCAM_SDK_LIBRARY before setup"
      )
    return self.camera

  def _validate_camera_geometry(self) -> None:
    """Reject a camera format that disagrees with the optical calibration geometry."""
    calibration = getattr(self, "calibration", None)
    if self.camera is None or calibration is None:
      return
    width = getattr(self.camera, "width", None)
    height = getattr(self.camera, "height", None)
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
      raise CeligoError("Camera must expose positive width and height for geometry validation")
    expected = (calibration.image_width_pixels, calibration.image_height_pixels)
    if (width, height) != expected:
      raise CeligoError(
        f"Camera format {width}x{height} does not match calibrated image geometry "
        f"{expected[0]}x{expected[1]}; configure the Lumenera ROI before acquisition"
      )

  async def _configure_camera_for_calibration(self) -> None:
    """Apply the calibrated image dimensions to cameras that support native ROI."""
    calibration = getattr(self, "calibration", None)
    camera = self.camera
    if camera is None or calibration is None:
      return
    expected = (calibration.image_width_pixels, calibration.image_height_pixels)
    if (camera.width, camera.height) == expected:
      return
    set_frame_format = getattr(camera, "set_frame_format", None)
    if set_frame_format is None:
      logger.warning(
        "Camera format is %sx%s, not calibrated %sx%s, and the camera does not "
        "support native ROI configuration",
        camera.width,
        camera.height,
        expected[0],
        expected[1],
      )
      return
    await set_frame_format(*expected)
    self._validate_camera_geometry()

  def _validate_frame_integrity(self, frame: CameraFrame) -> None:
    if frame.width <= 0 or frame.height <= 0 or frame.bit_depth not in (8, 16):
      raise CeligoError(
        f"Camera returned invalid frame metadata {frame.width}x{frame.height}x{frame.bit_depth}"
      )
    expected_bytes = frame.width * frame.height * (2 if frame.bit_depth == 16 else 1)
    if len(frame.data) != expected_bytes:
      raise CeligoError(
        f"Camera returned {len(frame.data)} bytes; {expected_bytes} are required by "
        f"the {frame.width}x{frame.height}x{frame.bit_depth} format"
      )

  def _validate_frame_geometry(self, frame: CameraFrame) -> None:
    self._validate_frame_integrity(frame)
    calibration = getattr(self, "calibration", None)
    if calibration is None:
      return
    expected = (calibration.image_width_pixels, calibration.image_height_pixels)
    if (frame.width, frame.height) != expected:
      raise CeligoError(
        f"Captured frame {frame.width}x{frame.height} does not match calibrated "
        f"geometry {expected[0]}x{expected[1]}"
      )

  async def _ensure_camera_ready(self, calibrated: bool = True) -> CeligoCamera:
    camera = self._require_camera()
    if not camera.is_open:
      await camera.setup()
    if calibrated:
      self._validate_camera_geometry()
    return camera

  async def set_camera_settings(
    self,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    restart: bool = False,
  ) -> Tuple[float, float]:
    """Set camera properties through the Celigo-owned camera lifecycle.

    ``restart=True`` closes and reopens the stream after applying settings. This is
    useful for USB/IP cameras that can otherwise return one stale request.
    """
    camera = await self._ensure_camera_ready(calibrated=False)
    if exposure_ms is not None:
      await camera.set_exposure(exposure_ms)
    if gain is not None:
      await camera.set_gain(gain)
    if restart:
      await camera.stop()
      await camera.setup()
      await self._configure_camera_for_calibration()
    return camera.exposure_ms, camera.gain

  async def capture_raw_frame(
    self,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    flush_frames: int = 2,
  ) -> CameraFrame:
    """Capture an integrity-checked frame without claiming calibrated geometry."""
    camera = await self._ensure_camera_ready(calibrated=False)
    if exposure_ms is not None:
      await camera.set_exposure(exposure_ms)
    if gain is not None:
      await camera.set_gain(gain)
    frame = await camera.capture(flush_frames=flush_frames)
    self._validate_frame_integrity(frame)
    return frame

  async def capture_frame(
    self,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    flush_frames: int = 2,
  ) -> CameraFrame:
    """Capture one image from the configured camera."""
    camera = await self._ensure_camera_ready()
    if exposure_ms is not None:
      await camera.set_exposure(exposure_ms)
    if gain is not None:
      await camera.set_gain(gain)
    frame = await camera.capture(flush_frames=flush_frames)
    self._validate_frame_geometry(frame)
    return frame

  async def auto_exposure(
    self,
    candidates_ms: Tuple[float, ...] = (20.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.25, 0.1),
    saturation_fraction: float = 0.01,
    minimum_mean_fraction: float = 0.03,
  ) -> Tuple[float, CameraFrame]:
    """Select the longest candidate exposure that is bright but not saturated."""
    if not candidates_ms or any(candidate <= 0 for candidate in candidates_ms):
      raise ValueError("candidates_ms must contain positive exposures")
    camera = self._require_camera()
    selected: Optional[Tuple[float, CameraFrame]] = None
    for exposure in candidates_ms:
      await camera.set_exposure(exposure)
      frame = await camera.capture(flush_frames=3)
      self._validate_frame_geometry(frame)
      values = frame.pixels()
      maximum_value = 65535 if frame.bit_depth > 8 else 255
      hot_threshold = maximum_value - max(1, maximum_value // 50)
      hot = sum(1 for value in values if value >= hot_threshold) / max(1, len(values))
      mean = sum(values) / max(1, len(values))
      selected = (exposure, frame)
      if hot <= saturation_fraction and mean >= maximum_value * minimum_mean_fraction:
        return selected
    if selected is None:
      raise CeligoError("Auto-exposure produced no camera frames")
    return selected

  def _z_limits(self) -> Tuple[int, int]:
    axis = self._axis_config("z")
    if axis is None:
      raise CeligoError("Autofocus requires configured Z-axis limits")
    low, high = sorted(
      (
        mm_to_encoder_ticks(axis.min_position, axis),
        mm_to_encoder_ticks(axis.max_position, axis),
      )
    )
    return low, high

  async def autofocus(
    self,
    mode: Literal["image", "hardware"] = "image",
    center_z: Optional[int] = None,
    span_ticks: Optional[int] = None,
    coarse_step_ticks: Optional[int] = None,
    fine_step_ticks: int = 76,
    evaluator: Optional[Callable[[CameraFrame], float]] = None,
    settle_seconds: float = 0.05,
    focus_flush_frames: int = 2,
    verification_attempts: int = 2,
    minimum_verification_ratio: float = 0.7,
  ) -> FocusResult:
    """Find focus by host-side Z stepping, as observed in the Celigo captures.

    Image autofocus uses the variance-of-Laplacian metric supplied by
    :class:`CameraFrame`, or a custom image evaluator. The displacement-sensor path used
    by vendor ``hardware`` autofocus is not exposed by the packaged camera API and is
    therefore rejected instead of silently running image autofocus under the wrong name.
    """
    if mode not in ("image", "hardware"):
      raise ValueError("mode must be 'image' or 'hardware'")
    if mode == "hardware":
      raise CeligoError(
        "Hardware autofocus is unavailable because no displacement-sensor interface "
        "is configured; use mode='image'"
      )
    if fine_step_ticks <= 0:
      raise ValueError("fine_step_ticks must be positive")
    if focus_flush_frames < 0:
      raise ValueError("focus_flush_frames must be non-negative")
    if verification_attempts < 1:
      raise ValueError("verification_attempts must be at least 1")
    if not 0 < minimum_verification_ratio <= 1:
      raise ValueError("minimum_verification_ratio must be in (0, 1]")
    z_axis = self._axis_config("z")
    if z_axis is None:
      raise CeligoError("Autofocus requires a configured Z axis")
    low, high = self._z_limits()
    initial_z = await self.request_encoder("z")
    if center_z is None:
      center_z = initial_z
    span = span_ticks if span_ticks is not None else 3000
    coarse_step = coarse_step_ticks or 252
    if span < 0 or coarse_step <= 0:
      raise ValueError("span_ticks must be non-negative and coarse_step_ticks positive")
    center_z = min(high, max(low, center_z))

    score_frame = evaluator or (lambda frame: frame.sharpness())
    samples: List[Tuple[int, float]] = []
    frames: Dict[int, CameraFrame] = {}

    async def evaluate(frame: CameraFrame) -> float:
      loop = asyncio.get_running_loop()
      with concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="celigo-focus"
      ) as executor:
        score = float(await loop.run_in_executor(executor, score_frame, frame))
      if not math.isfinite(score):
        raise CeligoError("Autofocus evaluator returned a non-finite score")
      return score

    async def inspect(z_ticks: int) -> None:
      z_ticks = min(high, max(low, z_ticks))
      if z_ticks in frames:
        return
      try:
        await self._move_z_ticks(z_ticks)
        if settle_seconds > 0:
          await asyncio.sleep(settle_seconds)
        frame = await self.capture_frame(flush_frames=focus_flush_frames)
        score = await evaluate(frame)
        frames[z_ticks] = frame
        samples.append((z_ticks, score))
      except BaseException:
        with contextlib.suppress(Exception):
          await self._complete_cleanup(self._move_z_ticks(initial_z))
        raise

    start = max(low, center_z - span)
    stop = min(high, center_z + span)
    coarse_positions = list(range(start, stop + 1, coarse_step))
    if not coarse_positions or coarse_positions[-1] != stop:
      coarse_positions.append(stop)
    for position in coarse_positions:
      await inspect(position)

    best_z, _best_score = max(samples, key=lambda item: item[1])
    fine_start = max(low, best_z - coarse_step)
    fine_stop = min(high, best_z + coarse_step)
    fine_positions = list(range(fine_start, fine_stop + 1, fine_step_ticks))
    if not fine_positions or fine_positions[-1] != fine_stop:
      fine_positions.append(fine_stop)
    for position in fine_positions:
      await inspect(position)

    best_z, best_score = max(samples, key=lambda item: item[1])
    scores = [score for _, score in samples]
    if max(scores) - min(scores) <= max(1e-12, abs(max(scores)) * 1e-9):
      await self._move_z_ticks(initial_z)
      raise CeligoError("Autofocus scan has no measurable focus contrast")
    if best_z in (
      min(position for position, _ in samples),
      max(position for position, _ in samples),
    ):
      await self._move_z_ticks(initial_z)
      raise CeligoError("Autofocus optimum lies on the scan boundary")
    try:
      await self._move_z_ticks(best_z)
      final_frame: Optional[CameraFrame] = None
      final_score = -math.inf
      for _ in range(verification_attempts):
        candidate_frame = await self.capture_frame(
          flush_frames=max(2, focus_flush_frames)
        )
        candidate_score = await evaluate(candidate_frame)
        if candidate_score > final_score:
          final_frame = candidate_frame
          final_score = candidate_score
        if final_score >= best_score * minimum_verification_ratio:
          break
      if final_frame is None or final_score < best_score * minimum_verification_ratio:
        raise CeligoError(
          "Autofocus optimum did not reproduce after the final Z move: "
          f"scan score {best_score:.6g}, verification score {final_score:.6g}, "
          f"required ratio {minimum_verification_ratio:.3f}"
        )
    except BaseException:
      with contextlib.suppress(Exception):
        await self._complete_cleanup(self._move_z_ticks(initial_z))
      raise
    return FocusResult(
      z_ticks=best_z,
      z_mm=encoder_ticks_to_mm(best_z, z_axis),
      score=final_score,
      samples=tuple(samples),
      frame=final_frame,
    )

  async def _acquire(
    self,
    well: str,
    channel: Channel,
    plate: Optional[Plate] = None,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    autofocus: Optional[Literal["image", "hardware"]] = None,
    z_mm: Optional[float] = None,
    require_lamp_ready: bool = False,
    galvo_mm: Tuple[float, float] = (0.0, 0.0),
    machine_auto_exposure: bool = False,
  ) -> AcquisitionResult:
    """Navigate, select optics, optionally focus, and capture one calibrated FOV."""
    x_ticks, y_ticks = await self.move_to_well(well, plate)
    previous_channel = self.current_channel
    await self.select_channel(channel, require_lamp_ready=require_lamp_ready)
    channel_config = self._channel_config(channel)
    z_axis = self._axis_config("z")
    if z_axis is None:
      raise CeligoError("Channel focus correction requires a configured Z axis")
    target_z_mm = z_mm
    if target_z_mm is None:
      if self.calibration is not None:
        target_z_mm = (
          self.calibration.calibrated_z_position
          + channel_config.z_offset_to_brightfield_mm
        )
      else:
        current_z = await self.request_encoder("z")
        brightfield_mm = encoder_ticks_to_mm(current_z, z_axis)
        if previous_channel is not None:
          brightfield_mm -= self._channel_config(previous_channel).z_offset_to_brightfield_mm
        target_z_mm = brightfield_mm + channel_config.z_offset_to_brightfield_mm
    logical_filter = channel_config.logical_filter
    logical_galvo_volts = self.galvo_targets_for_offset(logical_filter, galvo_mm)
    galvo_volts = await self.move_galvos(*logical_galvo_volts)

    camera = await self._ensure_camera_ready()
    if exposure_ms is not None:
      await camera.set_exposure(exposure_ms)
    if gain is not None:
      await camera.set_gain(gain)
    if machine_auto_exposure:
      await self.auto_exposure()

    focus_result: Optional[FocusResult] = None
    if autofocus is not None:
      settled_z_mm = await self.move_z(target_z_mm)
      focus_result = await self.autofocus(
        mode=autofocus,
        center_z=mm_to_encoder_ticks(settled_z_mm, z_axis),
      )
      z_ticks = focus_result.z_ticks
    else:
      settled_z_mm = await self.move_z(target_z_mm)
      z_ticks = mm_to_encoder_ticks(settled_z_mm, z_axis)
    frame = focus_result.frame if focus_result is not None else await camera.capture(flush_frames=2)
    self._validate_frame_geometry(frame)
    config = self._require_config()
    if config.x_axis is None or config.y_axis is None:
      raise CeligoError("Acquisition requires configured X and Y axes")
    return AcquisitionResult(
      well=well,
      channel=channel,
      x_ticks=x_ticks,
      y_ticks=y_ticks,
      z_ticks=z_ticks,
      x_mm=encoder_ticks_to_mm(x_ticks, config.x_axis),
      y_mm=encoder_ticks_to_mm(y_ticks, config.y_axis),
      z_mm=encoder_ticks_to_mm(z_ticks, z_axis),
      frame=frame,
      focus=focus_result,
      galvo_volts=galvo_volts,
    )

  async def acquire(
    self,
    well: str,
    channel: Channel,
    plate: Optional[Plate] = None,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    autofocus: Optional[Literal["image", "hardware"]] = None,
    z_mm: Optional[float] = None,
    require_lamp_ready: bool = False,
    galvo_mm: Tuple[float, float] = (0.0, 0.0),
    machine_auto_exposure: bool = False,
  ) -> AcquisitionResult:
    """Acquire one FOV, extinguishing illumination if any step fails or is cancelled."""
    try:
      return await self._acquire(
        well=well,
        channel=channel,
        plate=plate,
        exposure_ms=exposure_ms,
        gain=gain,
        autofocus=autofocus,
        z_mm=z_mm,
        require_lamp_ready=require_lamp_ready,
        galvo_mm=galvo_mm,
        machine_auto_exposure=machine_auto_exposure,
      )
    except BaseException:
      with contextlib.suppress(Exception):
        await self._complete_cleanup(self.illumination_off())
      raise

  async def acquire_scan(
    self,
    wells: List[str],
    channels: List[Channel],
    plate: Optional[Plate] = None,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    autofocus: Optional[Literal["image", "hardware"]] = None,
    scan_fovs: bool = False,
  ) -> List[AcquisitionResult]:
    """Acquire a well/channel plan, optionally scanning every configured galvo FOV."""
    if not wells or not channels:
      return []
    if scan_fovs:
      if self.calibration is None or self.navigation is None:
        raise CeligoError("FOV scanning requires CalibrationConfig and NavigationConfig")
      base_offsets = galvo_fov_offsets_mm(self.calibration, self.navigation)
    else:
      base_offsets = [(0.0, 0.0)]
    results: List[AcquisitionResult] = []
    focused_brightfield_z_mm: Optional[float] = None
    for well in wells:
      for channel in channels:
        channel_config = self._channel_config(channel)
        offsets = [
          (
            offset[0] * channel_config.mm_per_pixel_x_correction_to_brightfield,
            offset[1] * channel_config.mm_per_pixel_y_correction_to_brightfield,
          )
          for offset in base_offsets
        ]
        for index, offset in enumerate(offsets):
          focus_mode = autofocus if index == 0 else None
          channel_z_mm = (
            None
            if focused_brightfield_z_mm is None
            else focused_brightfield_z_mm + channel_config.z_offset_to_brightfield_mm
          )
          result = await self.acquire(
            well=well,
            channel=channel,
            plate=plate,
            exposure_ms=exposure_ms,
            gain=gain,
            autofocus=focus_mode,
            z_mm=channel_z_mm,
            galvo_mm=offset,
          )
          if result.focus is not None:
            z_axis = self._axis_config("z")
            if z_axis is None:
              raise CeligoError("Autofocus requires a configured Z axis")
            focused_brightfield_z_mm = (
              encoder_ticks_to_mm(result.z_ticks, z_axis)
              - channel_config.z_offset_to_brightfield_mm
            )
          results.append(result)
    return results

  # -- triggered acquisition / camera sync -----------------------------------

  async def triggered_acquisition(
    self, points: List[Tuple[float, float]], reply_timeout: Optional[float] = None
  ) -> None:
    """Run a galvo-targeted triggered acquisition over a list of (x_volts, y_volts) points.

    Uploads the galvo target list; the firmware steps to each point and pulses the camera
    trigger, only acknowledging once the whole sweep completes.

    This is part of the imaging pipeline, not a standalone primitive: the camera must be
    armed to consume the triggers. Called without a camera ready the controller blocks
    until the acquisition completes, which can hang the board (recoverable only by a power
    cycle). ``reply_timeout`` (default: a long multiple of ``move_timeout``) must cover the
    full sweep.
    """
    del points, reply_timeout
    raise CeligoError(
      "triggered_acquisition() is disabled until camera external-trigger arming and "
      "controller recovery are implemented"
    )

  async def signal_diagnostics(self, operation: int) -> int:
    """Send a SIGNAL_DIAGNOSTICS sub-command and return its int result."""
    resp = await self._transact(_CMD_SIGNAL_DIAGNOSTICS, struct.pack(">h", operation))
    _require_payload_length(resp, 4, "signal diagnostics")
    return int(struct.unpack_from(">i", resp, 0)[0])

  async def set_camera_trigger(self, on: bool) -> None:
    """Assert or clear the camera trigger line."""
    await self.signal_diagnostics(_DIAG_SET_TRIGGER if on else _DIAG_CLEAR_TRIGGER)

  async def pulse_camera_trigger(self) -> None:
    """Pulse the camera trigger line once."""
    await self.signal_diagnostics(_DIAG_PULSE_TRIGGER)

  def _camera_signal(self, raw: int, invert: bool) -> bool:
    if raw not in (0, 1):
      raise CeligoError(f"Camera diagnostic returned invalid digital value {raw}")
    return bool(raw) != invert

  async def request_camera_busy(self) -> bool:
    """Read the configured, polarity-corrected camera busy signal."""
    camera_config = self.config.external_camera_control if self.config is not None else None
    invert = camera_config.invert_busy if camera_config is not None else False
    return self._camera_signal(await self.signal_diagnostics(_DIAG_READ_BUSY), invert)

  async def request_camera_integration(self) -> bool:
    """Read the configured, polarity-corrected camera integration signal."""
    camera_config = self.config.external_camera_control if self.config is not None else None
    invert = camera_config.invert_integration if camera_config is not None else False
    return self._camera_signal(await self.signal_diagnostics(_DIAG_READ_INTEGRATION), invert)

  async def request_diagnostic_encoder(self) -> int:
    """Read the encoder value via the signal-diagnostics path."""
    return await self.signal_diagnostics(_DIAG_READ_ENCODER)

  # -- state persistence / diagnostics --------------------------------------

  @staticmethod
  def _without_source_paths(value: Any) -> Any:
    if isinstance(value, dict):
      return {
        key: Celigo._without_source_paths(item)
        for key, item in value.items()
        if key != "source_path"
      }
    if isinstance(value, (list, tuple)):
      return [Celigo._without_source_paths(item) for item in value]
    return value

  @staticmethod
  def _plate_configuration(plate: Plate) -> Dict[str, Any]:
    wells = []
    for row in range(plate.num_items_y):
      for column in range(plate.num_items_x):
        well = plate.get_well((row, column))
        location = well.location
        wells.append(
          {
            "row": row,
            "column": column,
            "location": (
              None if location is None else [location.x, location.y, location.z]
            ),
            "size": [well.get_size_x(), well.get_size_y(), well.get_size_z()],
          }
        )
    return {
      "model": plate.model,
      "size": [plate.get_size_x(), plate.get_size_y(), plate.get_size_z()],
      "plate_type": plate.plate_type,
      "wells": wells,
    }

  def _configuration_fingerprint(self) -> str:
    calibration = getattr(self, "calibration", None)
    hardware_defaults = getattr(self, "hardware_defaults", None)
    navigation = getattr(self, "navigation", None)
    plate = getattr(self, "plate", None)
    payload = {
      "hardware": None if self.config is None else asdict(self.config),
      "channels": {name: asdict(value) for name, value in sorted(self.channels.items())},
      "calibration": None if calibration is None else asdict(calibration),
      "hardware_defaults": None if hardware_defaults is None else asdict(hardware_defaults),
      "galvo_optical": (
        None if self.galvo_optical_calibration is None else asdict(self.galvo_optical_calibration)
      ),
      "navigation": None if navigation is None else asdict(navigation),
      "plate": None if plate is None else self._plate_configuration(plate),
      "load_well": getattr(self, "load_well", None),
      "magnification": self.magnification,
    }
    canonical = json.dumps(
      self._without_source_paths(payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

  def _device_identity(self) -> Dict[str, Any]:
    if self.device_info is None:
      raise CeligoError("Runtime state requires a connected, identified Celigo")
    transport_identity: Optional[str] = None
    io = getattr(self, "io", None)
    if io is not None:
      with contextlib.suppress(RuntimeError):
        transport_identity = io.device_id
    return {
      "device_index": self.device_info.device_index,
      "firmware_version": list(self.device_info.firmware_version),
      "uart_buffer_length": self.device_info.uart_buffer_length,
      "transport_device_id": transport_identity,
    }

  def save_runtime_state(self, path: str) -> None:
    """Atomically save learned home positions and galvo calibrations as JSON.

    This deliberately writes a sidecar file rather than modifying Celigo's vendor XML.
    """
    calibrations = {
      str(logical_filter): {
        "forward": transform.forward,
        "reverse": transform.reverse,
        "order": transform.order,
        "successful": transform.successful,
        "source_path": transform.source_path,
      }
      for logical_filter, transform in self.galvo_calibrations.items()
    }
    state = {
      "version": 2,
      "device_identity": self._device_identity(),
      "configuration_fingerprint": self._configuration_fingerprint(),
      "filter_home_position": self._filter_home_position,
      "discrete_home_positions": self._discrete_home_positions,
      "current_channel": self.current_channel,
      "magnification": self.magnification,
      "galvo_calibrations": calibrations,
    }
    destination = os.path.abspath(path)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temporary_path: Optional[str] = None
    try:
      with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=os.path.dirname(destination), delete=False
      ) as temporary:
        temporary_path = temporary.name
        json.dump(state, temporary, indent=2, sort_keys=True)
        temporary.flush()
        os.fsync(temporary.fileno())
      os.replace(temporary_path, destination)
    finally:
      if temporary_path is not None and os.path.exists(temporary_path):
        os.unlink(temporary_path)

  def load_runtime_state(self, path: str) -> None:
    """Restore device/config-bound calibration data from a sidecar.

    Learned encoder homes and channel selection are intentionally not restored: they
    cannot be trusted across a power cycle or manual mechanism movement.
    """
    with open(path, encoding="utf-8") as source:
      state = json.load(source)
    if state.get("version") != 2:
      raise CeligoError(f"Unsupported Celigo runtime-state version {state.get('version')!r}")
    if state.get("device_identity") != self._device_identity():
      raise CeligoError("Runtime state belongs to a different Celigo controller")
    if state.get("configuration_fingerprint") != self._configuration_fingerprint():
      raise CeligoError("Runtime state does not match the active Celigo configuration")
    loaded_calibrations: Dict[int, Calibrated2DPolynomialTransform] = {}
    raw_calibrations = state.get("galvo_calibrations", {})
    if not isinstance(raw_calibrations, dict):
      raise CeligoError("Invalid galvo_calibrations in runtime state")
    for key, raw in raw_calibrations.items():
      if not isinstance(key, str) or not key.isdecimal() or not isinstance(raw, dict):
        raise CeligoError(f"Invalid galvo calibration {key!r}")
      logical_filter = int(key)
      expected_filters = {channel.logical_filter for channel in self.channels.values()}
      if self.config is not None and self.config.dichroic_filter_wheel is not None:
        expected_filters.update(
          entry.logical_number for entry in self.config.dichroic_filter_wheel.filter_map
        )
      if expected_filters and logical_filter not in expected_filters:
        raise CeligoError(f"Unknown logical filter {logical_filter} in runtime state")
      directions: Dict[str, Dict[str, Tuple[float, float]]] = {}
      for direction in ("forward", "reverse"):
        terms = raw.get(direction)
        if not isinstance(terms, dict):
          raise CeligoError(f"Invalid {direction} terms for galvo calibration {key}")
        parsed: Dict[str, Tuple[float, float]] = {}
        for name, value in terms.items():
          if (
            not isinstance(name, str)
            or name not in _GALVO_POLYNOMIAL_TERMS
            or not isinstance(value, (list, tuple))
            or len(value) != 2
            or any(not isinstance(item, (int, float)) for item in value)
            or any(not math.isfinite(float(item)) for item in value)
          ):
            raise CeligoError(f"Invalid coefficient {name!r} in galvo calibration {key}")
          parsed[name] = (float(value[0]), float(value[1]))
        directions[direction] = parsed
      order = raw.get("order")
      successful = raw.get("successful")
      if order not in (2, 3) or successful not in (None, True, False):
        raise CeligoError(f"Invalid metadata for galvo calibration {key}")
      if order == 2 and any(
        name in _GALVO_CUBIC_TERMS for direction in directions.values() for name in direction
      ):
        raise CeligoError(f"Cubic coefficient found in quadratic galvo calibration {key}")
      loaded_calibrations[logical_filter] = Calibrated2DPolynomialTransform(
        forward=directions["forward"],
        reverse=directions["reverse"],
        order=order,
        successful=successful,
      )
    self._filter_home_position = None
    self._discrete_home_positions = {}
    self.current_channel = None
    magnification = int(state.get("magnification", self.magnification))
    if magnification not in (3, 5, 10, 20):
      raise CeligoError(f"Invalid magnification {magnification!r} in runtime state")
    optical = self.galvo_optical_calibration
    if optical is not None and (
      magnification not in optical.x.magnifications or magnification not in optical.y.magnifications
    ):
      raise CeligoError(f"Runtime-state magnification {magnification}X has no optical calibration")
    self.magnification = magnification
    self.galvo_calibrations = loaded_calibrations

  async def run_self_test(
    self, active: bool = False, test_motion: bool = False
  ) -> DiagnosticReport:
    """Run controller diagnostics; hardware-changing checks require explicit opt-in.

    The default is read-only. ``active=True`` centers the galvos and captures a camera
    frame when configured. ``test_motion=True`` additionally performs a five-tick
    round-trip on X/Y/Z and therefore requires a clear motion envelope.
    """
    checks: Dict[str, Any] = {}
    failures: List[str] = []

    async def record(name: str, operation) -> None:
      try:
        checks[name] = await operation()
      except Exception as exc:  # diagnostics must report every check
        checks[name] = f"{type(exc).__name__}: {exc}"
        failures.append(name)

    await record("controller_status", self.request_status)
    status = checks.get("controller_status")
    if isinstance(status, ControllerStatus) and status.has_safety_fault:
      failures.append("controller_safety_flags")
    await record("device_info", self.request_device_info)
    await record("motor_map", self.request_motor_map)
    await record("encoders", self.request_encoders)
    await record("digital_inputs", self.request_digital_inputs)

    for logical_filter, transform in sorted(self.galvo_calibrations.items()):
      if transform.successful is False:
        failures.append(f"galvo_calibration_{logical_filter}")

    if self.config is not None:
      for axis in self._all_configured_axes():
        name = f"motor_{axis.axis_index}_encoder_ratio"

        async def check_ratio(axis_config: AxisConfig = axis) -> Dict[str, Any]:
          actual = await self.request_encoder_ratio(axis_config.axis_index)
          expected = round(axis_config.encoder_to_motor_tick_ratio * 1000)
          if actual != expected:
            failures.append(name)
          return {"actual": actual, "expected": expected, "matches": actual == expected}

        await record(name, check_ratio)

    camera_config = self.config.external_camera_control if self.config is not None else None
    if camera_config is not None and camera_config.enabled:
      await record("camera_busy", self.request_camera_busy)
      await record("camera_integration", self.request_camera_integration)

    if active:
      await record("galvo_center", self.home_galvos)
      if self.camera is not None:
        await record("camera_frame", self.capture_frame)
    if test_motion:
      if not active:
        failures.append("test_motion_requires_active")
      else:
        for axis_name in ("x", "y", "z"):

          async def round_trip(name: Axis = cast(Axis, axis_name)) -> Dict[str, int]:
            start = await self.request_encoder(name)
            axis_config = self._axis_config(name)
            if axis_config is None:
              raise CeligoError(f"No configured bounds for diagnostic {name} motion")
            bounds = self._axis_bounds_ticks(axis_config)
            low, high = bounds
            if not low <= start <= high:
              raise CeligoError(
                f"Diagnostic {name} start {start} is outside configured bounds {low}..{high}"
              )
            target = start + 5 if start + 5 <= high else start - 5
            if not low <= target <= high:
              raise CeligoError(f"No safe five-tick diagnostic move from {name}={start}")
            try:
              await self._move_ticks(name, target)
            finally:
              # Always attempt restoration, including cancellation or a failed outward move.
              restoration = asyncio.create_task(self._move_ticks(name, start))
              try:
                end = await asyncio.shield(restoration)
              except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                  await restoration
                raise
            return {"start": start, "end": cast(int, end)}

          await record(f"{axis_name}_motion_round_trip", round_trip)

    # Preserve first occurrence while making the report deterministic.
    unique_failures = tuple(dict.fromkeys(failures))
    return DiagnosticReport(not unique_failures, checks, unique_failures)
