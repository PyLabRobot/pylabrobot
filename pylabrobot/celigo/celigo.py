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
import contextlib
import logging
import math
import struct
import time
from dataclasses import dataclass
from functools import partial
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple, cast

from pylabrobot.celigo.camera import CameraFrame, CeligoCamera, LumeneraCamera
from pylabrobot.celigo.config import (
  AxisConfig,
  CeligoConfig,
  DigitalIOConfig,
  FilterWheelConfig,
  IlluminationChannelConfig,
  LightingIOConfig,
)
from pylabrobot.celigo.coordinates import CoordinateSystems
from pylabrobot.celigo.errors import CeligoError
from pylabrobot.celigo.galvo import Galvo
from pylabrobot.celigo.laser import Laser
from pylabrobot.celigo.motion import (
  Axis,
  FilterWheel,
  LinearAxis,
  MagnificationChanger,
  MotorController,
)
from pylabrobot.celigo.navigation import galvo_field_of_view_offsets_mm, well_to_stage_mm
from pylabrobot.celigo.protocol import (
  complete_cleanup,
  require_payload_length,
)
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources.plate import Plate

logger = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 230400

# Board command opcodes (byte 0 of every packet).
_CMD_ABORT = 3
_CMD_SEND_MOTOR_CONFIG = 9
_CMD_READ_DIG_PORT = 15
_CMD_SET_DIG_PORT_BITS = 16
_CMD_CLEAR_DIG_PORT_BITS = 17
_CMD_WRITE_DA_CHANNEL = 18
_CMD_READ_AD_CHANNEL = 20
_CMD_SEND_CONFIG = 22
_CMD_CONTROLLER_STATUS = 23
_CMD_RESET_CONTROLLER = 25
_CMD_GET_DIG_OUT_VALUE = 34
_CMD_GET_ANALOG_OUT_VALUE = 35
_CMD_SIGNAL_DIAGNOSTICS = 43
_CMD_SEND_BARCODE_MSG = 45
_CMD_READ_BARCODE_MSG = 46

# SIGNAL_DIAGNOSTICS sub-commands (camera trigger / status line).
_DIAG_SET_TRIGGER = 1
_DIAG_CLEAR_TRIGGER = 2
_DIAG_PULSE_TRIGGER = 3
_DIAG_READ_BUSY = 4
_DIAG_READ_INTEGRATION = 5
_DIAG_READ_ENCODER = 6

_MAX_RESPONSE_PAYLOAD_BYTES = 65535

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

LinearAxisName = Literal["x", "y", "z"]
OpticalComponentName = Literal[
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

_LINEAR_AXIS_NAMES: Tuple[LinearAxisName, ...] = ("x", "y", "z")

# 12-bit per-channel analog DAC full scale.
_ANALOG_DAC_FULL_SCALE = 4095.0

IlluminationChannelName = str


@dataclass(frozen=True)
class ControllerInfo:
  """Board identity from SEND_CONFIG: device index, firmware version, UART buffer size."""

  device_index: int
  firmware_version: Tuple[int, int, int]  # (major, minor, build)
  uart_buffer_length: int


@dataclass(frozen=True)
class DetectedMotorAddress:
  """One EZStepper address reported by a controller UART."""

  uart_index: int
  motor_index: int


@dataclass(frozen=True)
class ControllerStatus:
  """Decoded controller status returned by :meth:`Celigo.request_controller_status`."""

  raw_flags: int
  extended_status: int

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
  def has_controller_fault(self) -> bool:
    """Whether the controller reports an error or internal failure."""
    return self.error or self.controller_failed

  @property
  def has_laser_safety_fault(self) -> bool:
    """Whether controller health or the generic interlock makes laser use unsafe."""
    return self.has_controller_fault or self.interlock_open


@dataclass(frozen=True)
class FocusResult:
  """Best Z position, scored Z samples, and verified final autofocus frame."""

  z_ticks: int
  z_mm: float
  score: float
  scored_z_samples: Tuple[Tuple[int, float], ...]
  frame: CameraFrame


@dataclass(frozen=True)
class AcquisitionResult:
  """A captured frame plus motion/optical metadata used for the acquisition."""

  well: str
  channel: str
  x_mm: float
  y_mm: float
  z_mm: float
  frame: CameraFrame
  focus: Optional[FocusResult]
  galvo_hardware_voltages: Tuple[float, float]


@dataclass(frozen=True)
class SelfTestReport:
  """Read-only or active controller self-test results."""

  passed: bool
  checks: Dict[str, Any]
  failures: Tuple[str, ...]


@dataclass(frozen=True)
class _DrawerLoadTargets:
  """Stage positions used to return a plate beneath the optics."""

  x_park_mm: float
  y_clearance_mm: float
  y_park_mm: float


def _fletcher16(data: bytes, byte_count: int) -> Tuple[int, int]:
  """Fletcher-16 checksum: seeds 0xFF/0xFF, folded in 21-byte blocks."""
  s1 = 0xFF
  s2 = 0xFF
  i = 0
  remaining = byte_count
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


def _build_command_packet(opcode: int, sequence: int, payload: bytes = b"") -> bytes:
  """Serialize a request packet (11-byte header + payload)."""
  header = bytearray(_TX_HEADER_SIZE)
  header[0] = opcode
  struct.pack_into(">i", header, 1, sequence)
  struct.pack_into(">i", header, 5, _TX_HEADER_SIZE + len(payload))
  check_a, check_b = _fletcher16(header, 9)
  header[9] = check_a
  header[10] = check_b
  return bytes(header) + payload


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


def _analog_dac_to_volts(
  dac_count: int,
  min_voltage: float,
  max_voltage: float,
) -> float:
  """Inverse of :func:`_volts_to_analog_dac`."""
  if not 0 <= dac_count <= int(_ANALOG_DAC_FULL_SCALE):
    raise ValueError("DAC count must be in 0..4095")
  if not all(math.isfinite(value) for value in (min_voltage, max_voltage)):
    raise ValueError("analog voltage limits must be finite")
  if max_voltage <= min_voltage:
    raise ValueError("analog max_voltage must be greater than min_voltage")
  return dac_count / _ANALOG_DAC_FULL_SCALE * (max_voltage - min_voltage) + min_voltage


class Celigo:
  """Celigo image cytometer motion/illumination controller.

  Talks to the FTDI-based USB-IO board over serial. Exposes stage/Z motion in
  millimeters, drawer open/close (stage eject/load), imaging-channel selection
  (brightfield + fluorescence), galvo steering, and the board's digital/analog IO and
  barcode reader. Load an instrument's configuration with
  :meth:`CeligoConfig.from_install` and pass the result as ``config``.
  """

  def __init__(
    self,
    config: CeligoConfig,
    device_id: Optional[str] = None,
    usb_address: Optional[str] = None,
    vid: int = 0x0403,
    pid: int = 0x6001,
    baudrate: int = DEFAULT_BAUDRATE,
    latency_ms: int = 2,
    reply_timeout: float = 2.0,
    move_timeout: float = 30.0,
    lucam_sdk: Optional[str] = None,
    allow_laser: bool = False,
    fluorescence_warmup_seconds: float = 300.0,
    fluorescence_power_change_interval: float = 10.0,
  ):
    if not math.isfinite(reply_timeout) or reply_timeout <= 0:
      raise ValueError("reply_timeout must be a finite, positive number of seconds")
    if not math.isfinite(move_timeout) or move_timeout <= 0:
      raise ValueError("move_timeout must be a finite, positive number of seconds")
    if not math.isfinite(fluorescence_warmup_seconds) or fluorescence_warmup_seconds < 0:
      raise ValueError("fluorescence_warmup_seconds must be finite and non-negative")
    if (
      not math.isfinite(fluorescence_power_change_interval)
      or fluorescence_power_change_interval < 0
    ):
      raise ValueError("fluorescence_power_change_interval must be finite and non-negative")
    self.baudrate = baudrate
    self.latency_ms = latency_ms
    self.reply_timeout = reply_timeout
    self.move_timeout = move_timeout
    self.config = config
    self._plate: Optional[Plate] = None
    self.camera: CeligoCamera = LumeneraCamera(sdk_library=lucam_sdk)
    self.galvo = Galvo(self)
    self.laser = Laser(self, enabled=allow_laser)
    self.fluorescence_warmup_seconds = fluorescence_warmup_seconds
    self.fluorescence_power_change_interval = fluorescence_power_change_interval
    self.current_channel: Optional[str] = None
    self._connected = False
    has_lamp_power = bool(
      config.hardware.io is not None
      and any(
        output.io_name == "ExcitationLampPower"
        and output.enabled
        and output.io_type.strip().lower() == "out"
        for output in config.hardware.io.digital_ios
      )
    )
    self._fluorescence_on_since: Optional[float] = 0.0 if not has_lamp_power else None
    self._last_fluorescence_power_change: Optional[float] = None
    self.controller_info: Optional[ControllerInfo] = None
    self._command_sequence = 1
    self._command_lock = asyncio.Lock()
    self.io = FTDI(
      human_readable_device_name="Celigo",
      device_id=device_id,
      usb_address=usb_address,
      vid=vid,
      pid=pid,
    )
    self.motor_controller = MotorController(self)
    self._linear_axes = self._build_linear_axes()
    self._optical_axes = self._build_optical_axes()
    self._validate_unique_motor_addresses()

  @property
  def controller_firmware_version(self) -> Optional[Tuple[int, int, int]]:
    """The identified controller-board firmware version, if setup has reached identification."""
    return None if self.controller_info is None else self.controller_info.firmware_version

  def _build_linear_axes(self) -> Dict[LinearAxisName, LinearAxis]:
    hardware = self.config.hardware
    configured = {
      "x": hardware.x_axis,
      "y": hardware.y_axis,
      "z": hardware.z_axis,
    }
    return {
      cast(LinearAxisName, name): LinearAxis(
        self.motor_controller,
        cast(LinearAxisName, name),
        axis_config,
      )
      for name, axis_config in configured.items()
      if axis_config is not None and axis_config.enabled and axis_config.axis_index > 0
    }

  def _build_optical_axes(self) -> Dict[OpticalComponentName, Axis]:
    hardware = self.config.hardware
    configured: Dict[OpticalComponentName, Optional[AxisConfig]] = {
      "beam_expander": hardware.beam_expander,
      "camera_filter": hardware.camera_filter_wheel,
      "dichroic_filter": hardware.dichroic_filter_wheel,
      "door": hardware.door,
      "excitation_filter": hardware.excitation_filter_wheel,
      "excitation_nd_filter": hardware.excitation_nd_filter_wheel,
      "laser_attenuator": hardware.laser_attenuator,
      "laser_nd_filter": hardware.laser_nd_filter_wheel,
      "magnification": hardware.magnification_changer,
    }
    axes: Dict[OpticalComponentName, Axis] = {}
    for name, axis_config in configured.items():
      if axis_config is None or not axis_config.enabled or axis_config.axis_index <= 0:
        continue
      if isinstance(axis_config, FilterWheelConfig):
        axes[name] = (
          MagnificationChanger(self.motor_controller, axis_config, self.config)
          if name == "magnification"
          else FilterWheel(self.motor_controller, name, axis_config)
        )
      else:
        axes[name] = Axis(self.motor_controller, name, axis_config)
    return axes

  def _validate_unique_motor_addresses(self) -> None:
    by_index: Dict[int, Axis] = {}
    for axis in (*self._linear_axes.values(), *self._optical_axes.values()):
      existing = by_index.get(axis.axis_index)
      if existing is not None and existing is not axis:
        raise CeligoError(
          f"Enabled mechanisms {existing.config.motion_name!r} and "
          f"{axis.config.motion_name!r} share motor address {axis.axis_index}"
        )
      by_index[axis.axis_index] = axis

  def _require_linear_axis(self, name: LinearAxisName) -> LinearAxis:
    try:
      return self._linear_axes[name]
    except KeyError as exc:
      raise CeligoError(f"axis {name!r} is not configured") from exc

  def _require_optical_axis(self, component: OpticalComponentName) -> Axis:
    try:
      return self._optical_axes[component]
    except KeyError as exc:
      raise CeligoError(
        f"Optical component {component!r} is not configured on this instrument"
      ) from exc

  def _require_filter_wheel(self, component: OpticalComponentName) -> FilterWheel:
    axis = self._require_optical_axis(component)
    if not isinstance(axis, FilterWheel):
      raise CeligoError(f"Optical component {component!r} is not a filter wheel")
    return axis

  @property
  def x_axis(self) -> LinearAxis:
    return self._require_linear_axis("x")

  @property
  def y_axis(self) -> LinearAxis:
    return self._require_linear_axis("y")

  @property
  def z_axis(self) -> LinearAxis:
    return self._require_linear_axis("z")

  @property
  def dichroic_filter(self) -> FilterWheel:
    return self._require_filter_wheel("dichroic_filter")

  @property
  def camera_filter(self) -> FilterWheel:
    return self._require_filter_wheel("camera_filter")

  @property
  def excitation_filter(self) -> FilterWheel:
    return self._require_filter_wheel("excitation_filter")

  @property
  def excitation_nd_filter(self) -> FilterWheel:
    return self._require_filter_wheel("excitation_nd_filter")

  @property
  def beam_expander(self) -> Axis:
    return self._require_optical_axis("beam_expander")

  @property
  def magnification_changer(self) -> MagnificationChanger:
    axis = self._require_optical_axis("magnification")
    if not isinstance(axis, MagnificationChanger):
      raise CeligoError("The configured magnification mechanism is not a filter wheel")
    return axis

  def _configured_motion_axes(self) -> List[Axis]:
    axes = [*self._linear_axes.values(), *self._optical_axes.values()]
    return sorted(axes, key=lambda axis: axis.axis_index)

  def _require_digital_io(self, io_name: str) -> DigitalIOConfig:
    hardware = self.config.hardware
    if hardware.io is None:
      raise CeligoError("Celigo IO configuration is missing")
    for io_config in hardware.io.digital_ios:
      if io_config.io_name == io_name:
        if not io_config.enabled:
          raise CeligoError(f"Digital output {io_name!r} is disabled")
        if io_config.io_type.strip().lower() != "out":
          raise CeligoError(f"Digital IO {io_name!r} is not configured as an output")
        return io_config
    raise CeligoError(f"Celigo IO configuration has no {io_name!r} entry")

  def _find_digital_io(self, io_name: str) -> Optional[DigitalIOConfig]:
    hardware = self.config.hardware
    if hardware.io is None:
      raise CeligoError("Celigo IO configuration is missing")
    return next(
      (
        item
        for item in hardware.io.digital_ios
        if item.io_name == io_name and item.enabled and item.io_type.strip().lower() == "out"
      ),
      None,
    )

  def _require_lighting_io(self, io_name: str) -> LightingIOConfig:
    hardware = self.config.hardware
    if hardware.io is None:
      raise CeligoError("Celigo IO configuration is missing")
    for io_config in hardware.io.lighting_ios:
      if io_config.io_name == io_name:
        if not io_config.enabled:
          raise CeligoError(f"Lighting output {io_name!r} is disabled")
        return io_config
    raise CeligoError(f"Celigo IO configuration has no {io_name!r} entry")

  def _require_channel_config(self, channel: str) -> IlluminationChannelConfig:
    try:
      return self.config.channels[channel]
    except KeyError as exc:
      raise CeligoError(
        f"Channel {channel!r} is not configured; available channels: "
        f"{', '.join(sorted(self.config.channels)) or 'none'}"
      ) from exc

  # -- lifecycle -------------------------------------------------------------

  async def setup(self) -> None:
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
          await self.abort_controller_operation()
      # The first command after opening can drop; read status a few times to warm up.
      status: Optional[ControllerStatus] = None
      last_status_error: Optional[CeligoError] = None
      for _ in range(3):
        try:
          status = await self.request_controller_status()
          break
        except CeligoError as exc:
          last_status_error = exc
          await asyncio.sleep(0.1)
      if status is None:
        raise CeligoError("Celigo did not return a valid controller status") from last_status_error

      # Identity is required to choose the correct motor-tunnel framing safely.
      self.controller_info = await self.request_controller_info()
      await self._initialize_hardware()
      await self.camera.setup()
      await self._configure_camera_for_calibration()
      await self.home_imaging_axes()
    except BaseException:
      if io_open:
        with contextlib.suppress(Exception):
          await self.abort_controller_operation()
        with contextlib.suppress(Exception):
          await self._initialize_safe_outputs()
      with contextlib.suppress(Exception):
        await self.camera.stop()
      if io_open:
        with contextlib.suppress(Exception):
          await self.io.stop()
      raise
    self._connected = True
    logger.info("[Celigo] connected (status=%s, %s)", status, self.controller_info)

  async def stop(self) -> None:
    first_error: Optional[BaseException] = None

    async def attempt(operation) -> None:
      nonlocal first_error
      try:
        await operation()
      except BaseException as exc:
        if first_error is None:
          first_error = exc

    if self._connected:
      await attempt(self.abort_controller_operation)
      await attempt(self._initialize_safe_outputs)
    await attempt(self.camera.stop)
    await attempt(self.io.stop)
    self._connected = False
    if first_error is not None:
      raise first_error

  # -- packet layer ----------------------------------------------------------

  async def _read_exact_bytes(self, byte_count: int, reply_timeout: float) -> bytes:
    chunks = []
    remaining = byte_count
    deadline = time.monotonic() + reply_timeout
    while remaining > 0:
      chunk = await self.io.read(remaining)
      if chunk:
        chunks.append(chunk)
        remaining -= len(chunk)
        continue
      if time.monotonic() >= deadline:
        break
      await asyncio.sleep(0.001)
    received_bytes = b"".join(chunks)
    if len(received_bytes) != byte_count:
      raise CeligoError(f"Short read: expected {byte_count} bytes, got {len(received_bytes)}")
    return received_bytes

  async def send_command(
    self,
    opcode: int,
    payload: bytes = b"",
    retries: int = 3,
    reply_timeout: Optional[float] = None,
  ) -> bytes:
    """Send a command and return its response payload (b'' if there is none)."""
    selected_reply_timeout = self.reply_timeout if reply_timeout is None else reply_timeout
    if retries <= 0:
      raise ValueError("retries must be positive")
    if not math.isfinite(selected_reply_timeout) or selected_reply_timeout <= 0:
      raise ValueError("reply_timeout must be a finite, positive number of seconds")
    async with self._command_lock:
      self._command_sequence += 1
      sequence = self._command_sequence
      command_packet = _build_command_packet(opcode, sequence, payload)
      attempt = 0
      while True:
        attempt += 1
        written = await self.io.write(command_packet)
        if written != len(command_packet):
          await self.io.usb_purge_rx_buffer()
          await self.io.usb_purge_tx_buffer()
          raise CeligoError(f"Short write: expected {len(command_packet)} bytes, wrote {written}")
        try:
          return await self._read_controller_response(
            opcode,
            sequence,
            selected_reply_timeout,
          )
        except CeligoError as exc:
          # Purge after any failed read so leftover bytes can't desync the next command.
          await self.io.usb_purge_rx_buffer()
          await self.io.usb_purge_tx_buffer()
          if exc.ack in _ACK_RETRYABLE and attempt < retries:
            continue
          raise

  async def _read_controller_response(
    self,
    opcode: int,
    sequence: int,
    reply_timeout: float,
  ) -> bytes:
    header = await self._read_exact_bytes(_RX_HEADER_SIZE, reply_timeout)
    ack = header[0]
    echo_opcode = header[1]
    echo_seq = struct.unpack_from(">i", header, 2)[0]
    payload_length = struct.unpack_from(">i", header, 6)[0]

    if (header[10], header[11]) != _fletcher16(header, 10):
      raise CeligoError(f"Response checksum failure for opcode {opcode}, sequence {sequence}")

    if ack != _ACK_OK:
      raise CeligoError(
        f"{_ACK_MESSAGES.get(ack, f'Unknown ack {ack}')} (opcode {opcode})",
        ack=ack,
      )

    if echo_opcode != opcode:
      raise CeligoError(f"Reply opcode mismatch: expected {opcode}, got {echo_opcode}")
    if echo_seq != sequence:
      raise CeligoError(f"Reply sequence mismatch: expected {sequence}, got {echo_seq}")
    if not 0 <= payload_length <= _MAX_RESPONSE_PAYLOAD_BYTES:
      raise CeligoError(
        f"Invalid response payload length {payload_length}; maximum is "
        f"{_MAX_RESPONSE_PAYLOAD_BYTES} bytes"
      )

    return await self._read_exact_bytes(payload_length, reply_timeout) if payload_length else b""

  # -- status / encoders -----------------------------------------------------

  async def request_controller_status(self) -> ControllerStatus:
    """Request and decode the current controller status."""
    response = await self.send_command(_CMD_CONTROLLER_STATUS)
    require_payload_length(response, 8, "controller status")
    flags, extended_status = struct.unpack_from(">II", response, 0)
    return ControllerStatus(flags, extended_status)

  async def request_controller_info(self) -> ControllerInfo:
    """Read board identity (SEND_CONFIG): device index, firmware version, UART buffer size."""
    response = await self.send_command(_CMD_SEND_CONFIG)
    require_payload_length(response, 10, "controller info")
    device_index, encoded_firmware, uart_buffer_length = struct.unpack_from(
      ">hii",
      response,
      0,
    )
    firmware_version = (
      (encoded_firmware >> 16) & 0xFF,
      (encoded_firmware >> 8) & 0xFF,
      encoded_firmware & 0xFF,
    )
    return ControllerInfo(
      device_index=device_index,
      firmware_version=firmware_version,
      uart_buffer_length=uart_buffer_length,
    )

  async def request_is_safety_interlock_open(self) -> bool:
    """Whether the controller reports the safety interlock switch as open."""
    return (await self.request_controller_status()).interlock_open

  async def request_is_busy(self) -> bool:
    """Whether the controller reports the BUSY flag."""
    return (await self.request_controller_status()).busy

  async def wait_for_controller_ready(
    self,
    timeout: float = 5.0,
    poll_interval: float = 0.01,
  ) -> bool:
    """Poll status until the controller BUSY flag clears; return False on timeout."""
    if not math.isfinite(timeout) or timeout < 0:
      raise ValueError("timeout must be a finite, non-negative number of seconds")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
      raise ValueError("poll_interval must be a finite, positive number of seconds")
    deadline = time.monotonic() + timeout
    while await self.request_is_busy():
      if time.monotonic() >= deadline:
        return False
      await asyncio.sleep(poll_interval)
    return True

  async def request_detected_motor_addresses(self) -> List[DetectedMotorAddress]:
    """Return the EZStepper addresses reported by the controller's UARTs."""
    response = await self.send_command(_CMD_SEND_MOTOR_CONFIG)
    require_payload_length(response, 40, "motor configuration")
    motors: List[DetectedMotorAddress] = []
    offset = 0
    for uart_index in range(8):
      offset += 1  # per-UART status byte
      for _ in range(4):
        motor_index = response[offset]
        offset += 1
        if motor_index != 127:
          motors.append(
            DetectedMotorAddress(
              uart_index=uart_index,
              motor_index=motor_index,
            )
          )
    return motors

  async def _initialize_hardware(self) -> None:
    """Run the non-homing portion of the captured Celigo power-on sequence.

    This aborts stale operations, discovers the board/motors, configures galvo settling
    windows, replays every configured motor profile, and calibrates both galvos. It
    changes controller configuration but does not intentionally move a motor.
    """
    await self.abort_controller_operation()
    await self.abort_controller_operation()
    # The vendor reads identity three times during startup; setup() already performed one.
    for _ in range(2):
      self.controller_info = await self.request_controller_info()
    connected_motor_indices = {
      motor.motor_index for motor in await self.request_detected_motor_addresses()
    }
    missing_axes = [
      axis
      for axis in self._configured_motion_axes()
      if axis.axis_index not in connected_motor_indices
    ]
    if missing_axes:
      missing_descriptions = ", ".join(
        f"{axis.config.motion_name or axis.name} ({axis.axis_index})" for axis in missing_axes
      )
      raise CeligoError(f"Configured motors were not detected: {missing_descriptions}")
    await self._initialize_safe_outputs()
    for motion_axis in self._configured_motion_axes():
      await motion_axis._initialize()
    await self.galvo._initialize()

  async def _initialize_safe_outputs(self) -> None:
    """Put every controller output in the vendor startup's inactive state."""
    hardware = self.config.hardware
    lighting_outputs = (
      {output.channel: output for output in hardware.io.lighting_ios if output.enabled}
      if hardware.io is not None
      else {}
    )
    for channel_index in range(4):
      lighting_output = lighting_outputs.get(channel_index)
      if lighting_output is None:
        await self.set_analog_output_count(channel_index, 0)
      else:
        await self._set_lighting_output_intensity(lighting_output, 0.0)
    digital_outputs = (
      {
        output.bit_index: output
        for output in hardware.io.digital_ios
        if output.enabled and output.io_type.strip().lower() == "out"
      }
      if hardware.io is not None
      else {}
    )
    for bit_index in range(12):
      digital_output = digital_outputs.get(bit_index)
      await self.set_digital_output(
        bit_index,
        digital_output.invert if digital_output is not None else False,
      )
    self.current_channel = None
    lamp_power = None if hardware.io is None else self._find_digital_io("ExcitationLampPower")
    # Without a controllable power line the source is always powered.
    fluorescence_on = lamp_power is None
    self._fluorescence_on_since = 0.0 if fluorescence_on else None
    self._last_fluorescence_power_change = None

  async def abort_controller_operation(self) -> None:
    """Abort the current controller command."""
    await self.send_command(_CMD_ABORT)
    await asyncio.sleep(0.05)

  async def reset_controller(self) -> None:
    """Reset the controller board."""
    await self.send_command(_CMD_RESET_CONTROLLER)

  # -- digital & analog IO ---------------------------------------------------

  async def request_digital_input_bitmask(self) -> int:
    """Read the digital input port as a raw bitmask."""
    response = await self.send_command(_CMD_READ_DIG_PORT)
    require_payload_length(response, 2, "digital input")
    return int(struct.unpack_from(">H", response, 0)[0])

  async def request_digital_input(self, bit_index: int) -> bool:
    """Read one digital input line."""
    if not 0 <= bit_index < 12:
      raise ValueError("digital bit must be in 0..11")
    return bool(await self.request_digital_input_bitmask() & (1 << bit_index))

  async def request_digital_output_bitmask(self) -> int:
    """Read back the digital output register as a raw bitmask."""
    response = await self.send_command(_CMD_GET_DIG_OUT_VALUE)
    require_payload_length(response, 2, "digital output")
    return int(struct.unpack_from(">H", response, 0)[0])

  async def request_digital_output(self, bit_index: int) -> bool:
    """Read back one digital output line."""
    if not 0 <= bit_index < 12:
      raise ValueError("digital bit must be in 0..11")
    return bool(await self.request_digital_output_bitmask() & (1 << bit_index))

  async def set_digital_output(self, bit_index: int, high: bool) -> None:
    """Drive one raw digital output line high or low."""
    if not 0 <= bit_index < 12:
      raise ValueError("digital bit must be in 0..11")
    mask = 1 << bit_index
    opcode = _CMD_SET_DIG_PORT_BITS if high else _CMD_CLEAR_DIG_PORT_BITS
    await self.send_command(opcode, struct.pack(">H", mask))

  async def set_analog_output_count(self, channel_index: int, dac_count: int) -> None:
    """Write a raw 12-bit count to an analog output (DAC) channel."""
    if not 0 <= channel_index < 4:
      raise ValueError("analog output channel must be in 0..3")
    if not 0 <= dac_count <= 0x0FFF:
      raise ValueError("DAC count must be in 0..4095")
    await self.send_command(
      _CMD_WRITE_DA_CHANNEL,
      struct.pack(">HH", channel_index, dac_count),
    )

  async def request_analog_output_count(self, channel_index: int) -> int:
    """Read back an analog output (DAC) channel's raw count."""
    if not 0 <= channel_index < 4:
      raise ValueError("analog output channel must be in 0..3")
    response = await self.send_command(
      _CMD_GET_ANALOG_OUT_VALUE,
      struct.pack(">H", channel_index),
    )
    require_payload_length(response, 4, "analog output")
    echoed_channel_index, dac_count = struct.unpack_from(">HH", response, 0)
    if echoed_channel_index != channel_index:
      raise CeligoError(
        f"Analog-output reply channel mismatch: requested {channel_index}, "
        f"received {echoed_channel_index}"
      )
    return int(dac_count)

  async def request_analog_input_count(self, channel_index: int) -> int:
    """Read an analog input (ADC) channel's raw count (e.g. a sensor)."""
    if not 0 <= channel_index < 4:
      raise ValueError("analog input channel must be in 0..3")
    response = await self.send_command(
      _CMD_READ_AD_CHANNEL,
      struct.pack(">H", channel_index),
    )
    require_payload_length(response, 2, "analog input")
    return int(struct.unpack_from(">H", response, 0)[0])

  async def set_analog_output_voltage(
    self,
    channel_index: int,
    voltage: float,
    min_voltage: float,
    max_voltage: float,
  ) -> None:
    """Set an analog output channel to a voltage (per-channel min/max calibration)."""
    await self.set_analog_output_count(
      channel_index,
      _volts_to_analog_dac(voltage, min_voltage, max_voltage),
    )

  async def request_analog_output_voltage(
    self,
    channel_index: int,
    min_voltage: float,
    max_voltage: float,
  ) -> float:
    """Read back an analog output channel as a voltage."""
    return _analog_dac_to_volts(
      await self.request_analog_output_count(channel_index),
      min_voltage,
      max_voltage,
    )

  async def request_analog_input_voltage(
    self,
    channel_index: int,
    min_voltage: float,
    max_voltage: float,
  ) -> float:
    """Read an analog input channel as a voltage."""
    return _analog_dac_to_volts(
      await self.request_analog_input_count(channel_index),
      min_voltage,
      max_voltage,
    )

  # -- barcode ---------------------------------------------------------------

  async def send_barcode_command(self, command: str) -> None:
    """Send an ASCII command to the barcode reader.

    On this build the barcode UART is shared with the front-panel status display.
    """
    await self.send_command(_CMD_SEND_BARCODE_MSG, command.encode("ascii") + b"\x00")

  async def request_barcode(self) -> str:
    """Read the barcode reader's ASCII response."""
    response = await self.send_command(_CMD_READ_BARCODE_MSG)
    require_payload_length(response, 4, "barcode")
    response_length = struct.unpack_from(">H", response, 2)[0]
    require_payload_length(response, 4 + response_length, "barcode")
    return response[4 : 4 + response_length].decode(
      "ascii",
      errors="replace",
    )

  # -- motion ----------------------------------------------------------------

  async def home_imaging_axes(self) -> None:
    """Home Z first for clearance, then X, Y, and the dichroic filter wheel."""
    await self.turn_off_illumination()
    await self.z_axis.home()
    await self.x_axis.home()
    await self.y_axis.home()
    await self.dichroic_filter.home()

  # -- drawer (stage eject / load) -------------------------------------------

  def set_plate(self, plate: Plate) -> None:
    """Set the plate used by drawer loading, well navigation, and acquisition."""
    if not isinstance(plate, Plate):
      raise TypeError(f"plate must be a Plate, got {type(plate).__name__}")
    self._plate = plate

  def _require_plate(self) -> Plate:
    if self._plate is None:
      raise CeligoError("Set a plate with set_plate() before navigating to a well")
    return self._plate

  def _drawer_load_targets(self, well: str) -> _DrawerLoadTargets:
    x_park_mm, y_park_mm = self.well_position_mm(well)
    return _DrawerLoadTargets(
      x_park_mm=x_park_mm,
      y_clearance_mm=self.y_axis.config.min_position,
      y_park_mm=y_park_mm,
    )

  async def open_drawer(self) -> None:
    """Drive the stage out to the eject station so the plate is accessible.

    Retracts Z, moves Y to its configured clearance coordinate, then drives X negative
    and Y positive to their limit sensors using the lighter loading-pose currents.
    Already-active target limits are not driven again.
    """
    await self.turn_off_illumination()
    self.current_channel = None
    await self.z_axis.move_to(self.z_axis.config.min_position)
    await self.y_axis.move_to(self.y_axis.config.min_position)
    x_eject_distance_ticks = self.x_axis._limit_move_distance_ticks()
    y_eject_distance_ticks = self.y_axis._limit_move_distance_ticks()
    for axis, distance_ticks, request_is_limit_active in (
      (
        self.x_axis,
        -x_eject_distance_ticks,
        self.x_axis.request_is_negative_limit_active,
      ),
      (
        self.y_axis,
        y_eject_distance_ticks,
        self.y_axis.request_is_positive_limit_active,
      ),
    ):
      for _ in range(3):
        if await request_is_limit_active():
          break
        await axis._move_relative_to_limit(
          distance_ticks,
          move_current_percent=axis.config.loading_current_percentage,
        )
      else:
        raise CeligoError(f"drawer {axis.name.upper()} limit was not reached")

  async def close_drawer(self, well: str) -> None:
    """Move the stage under the optics using calibrated plate/well coordinates."""
    await self.turn_off_illumination()
    self.current_channel = None
    targets = self._drawer_load_targets(well)
    await self.y_axis.move_to(targets.y_clearance_mm)
    await self.x_axis.move_to(targets.x_park_mm)
    await self.y_axis.move_to(targets.y_park_mm)

  def well_position_mm(self, well: str) -> Tuple[float, float]:
    """Return the calibrated X/Y stage position for a named well."""
    coordinates = CoordinateSystems.from_config(
      self.config.calibration, self.config.hardware_defaults
    )
    return well_to_stage_mm(self._require_plate(), well, coordinates)

  async def move_to_well(
    self,
    well: str,
    retract_z: bool = False,
    safe_z_mm: Optional[float] = None,
  ) -> Tuple[float, float]:
    """Move the stage to a calibrated well center and return settled X/Y millimeters."""
    x_mm, y_mm = self.well_position_mm(well)
    if retract_z:
      if safe_z_mm is None:
        safe_z_mm = self.z_axis.config.min_position
      await self.z_axis.move_to(safe_z_mm)
    settled_x = await self.x_axis.move_to(x_mm)
    settled_y = await self.y_axis.move_to(y_mm)
    return settled_x, settled_y

  # -- illumination / channels -----------------------------------------------

  async def _set_named_digital_output(self, io_name: str, active: bool) -> None:
    output = self._require_digital_io(io_name)
    await self.set_digital_output(output.bit_index, active != output.invert)

  @staticmethod
  def _lighting_output_analog_count(
    output: LightingIOConfig,
    intensity_percent: float,
  ) -> int:
    if not math.isfinite(intensity_percent) or not 0 <= intensity_percent <= 100:
      raise ValueError("intensity_percent must be finite and within 0..100")
    if not math.isfinite(output.delay) or output.delay < 0:
      raise CeligoError(f"Lighting output {output.io_name!r} has an invalid configured delay")
    voltage = (
      output.min_voltage + (output.max_voltage - output.min_voltage) * intensity_percent / 100.0
    )
    if output.invert:
      voltage = output.max_voltage - voltage + output.min_voltage
    return _volts_to_analog_dac(voltage, output.min_voltage, output.max_voltage)

  async def _set_lighting_output_intensity(
    self,
    output: LightingIOConfig,
    intensity_percent: float,
  ) -> None:
    await self.set_analog_output_count(
      output.channel,
      self._lighting_output_analog_count(output, intensity_percent),
    )
    if output.delay:
      await asyncio.sleep(output.delay)

  async def _set_channel_intensity(
    self,
    channel_config: IlluminationChannelConfig,
    intensity_percent: Optional[float] = None,
  ) -> None:
    output = self._require_lighting_io(channel_config.lighting_io_name)
    await self._set_lighting_output_intensity(
      output,
      channel_config.intensity_percent if intensity_percent is None else intensity_percent,
    )

  async def set_brightfield_enabled(self, enabled: bool) -> None:
    """Turn the configured brightfield illumination on or off."""
    channel = self._require_channel_config("brightfield")
    strobe = self._find_digital_io("FLOnOff")
    if strobe is not None:
      await self.set_digital_output(strobe.bit_index, strobe.invert)
    await self._set_channel_intensity(channel, channel.intensity_percent if enabled else 0.0)

  @property
  def fluorescence_warmup_remaining(self) -> float:
    """Seconds remaining in the configured fluorescence-lamp warm-up interval."""
    on_since = self._fluorescence_on_since
    if on_since is None:
      return self.fluorescence_warmup_seconds
    elapsed = time.monotonic() - on_since
    return max(0.0, self.fluorescence_warmup_seconds - elapsed)

  @property
  def fluorescence_lamp_ready(self) -> bool:
    return self.fluorescence_warmup_remaining <= 0

  @property
  def can_change_fluorescence_power(self) -> bool:
    last_change = self._last_fluorescence_power_change
    if last_change is None:
      return True
    return bool(time.monotonic() - last_change >= self.fluorescence_power_change_interval)

  async def set_fluorescence_lamp_power(self, enabled: bool) -> None:
    """Set lamp power while enforcing the vendor's minimum toggle interval.

    Instruments without a configured ``ExcitationLampPower`` output have an
    always-powered source; requesting ``False`` is rejected because there is no line to
    switch it.
    """
    output = self._find_digital_io("ExcitationLampPower")
    if output is None:
      if not enabled:
        raise CeligoError("This instrument has no controllable fluorescence-lamp power line")
      if self._fluorescence_on_since is None:
        self._fluorescence_on_since = 0.0
      return
    if enabled == (self._fluorescence_on_since is not None):
      return
    if not self.can_change_fluorescence_power:
      remaining = self.fluorescence_power_change_interval - (
        time.monotonic() - cast(float, self._last_fluorescence_power_change)
      )
      raise CeligoError(f"Fluorescence lamp cannot change power for {remaining:.1f}s")
    if not enabled:
      await self._set_named_digital_output("FLOnOff", False)
    await self.set_digital_output(output.bit_index, enabled != output.invert)
    now = time.monotonic()
    self._fluorescence_on_since = now if enabled else None
    self._last_fluorescence_power_change = now

  async def turn_off_illumination(self) -> None:
    """Turn off every configured illumination output and fluorescence strobe."""
    hardware = self.config.hardware
    if hardware.io is None:
      raise CeligoError("Celigo IO configuration is missing")
    first_error: Optional[BaseException] = None

    async def attempt(operation: Awaitable[None]) -> None:
      nonlocal first_error
      try:
        await operation
      except BaseException as exc:
        if first_error is None:
          first_error = exc

    strobe = self._find_digital_io("FLOnOff")
    if strobe is not None:
      await attempt(self.set_digital_output(strobe.bit_index, strobe.invert))
    for output in hardware.io.lighting_ios:
      if output.enabled:
        await attempt(self._set_lighting_output_intensity(output, 0.0))
    if first_error is not None:
      raise first_error

  async def select_channel(
    self,
    channel: IlluminationChannelName,
    require_lamp_ready: bool = False,
  ) -> None:
    """Select an imaging channel while leaving its illumination off.

    Drops the strobe and all lighting outputs before moving the dichroic filter wheel,
    centering the galvos, and setting the fluorescence lamp-select bits. Call
    :meth:`set_illumination_enabled` to turn on the selected channel.

    Moves the filter wheel (hardware motion). The power toggle interval is enforced;
    pass ``require_lamp_ready=True`` to enforce the configured warm-up interval too.
    """
    channel_config = self._require_channel_config(channel)
    self._require_lighting_io(channel_config.lighting_io_name)
    if channel_config.strobe:
      self._require_digital_io("FLOnOff")
    if channel_config.bit_value is not None:
      self._require_digital_io("FLBit0")
      self._require_digital_io("FLBit1")
    self.current_channel = None
    await self.turn_off_illumination()
    if channel_config.strobe:
      await self.set_fluorescence_lamp_power(True)
      if require_lamp_ready and not self.fluorescence_lamp_ready:
        raise CeligoError(
          f"Fluorescence lamp is warming up ({self.fluorescence_warmup_remaining:.1f}s left)"
        )
    await self.dichroic_filter.move_to(channel_config.logical_filter)
    hardware = self.config.hardware
    if (
      hardware.x_galvo is not None
      and hardware.x_galvo.enabled
      and hardware.y_galvo is not None
      and hardware.y_galvo.enabled
    ):
      await self.galvo.home(logical_filter=channel_config.logical_filter)
    if channel_config.bit_value is not None:
      # The vendor's BitValue orders the two physical selector lines MSB first.
      await self._set_named_digital_output("FLBit0", bool(channel_config.bit_value & 0b10))
      await self._set_named_digital_output("FLBit1", bool(channel_config.bit_value & 0b01))

    self.current_channel = channel

  async def set_illumination_enabled(
    self,
    enabled: bool,
    intensity_percent: Optional[float] = None,
  ) -> None:
    """Turn the selected channel on or off using a percentage intensity override."""
    if not enabled:
      await self.turn_off_illumination()
      return
    if self.current_channel is None:
      raise CeligoError("Select an imaging channel before enabling illumination")
    channel_config = self._require_channel_config(self.current_channel)
    strobe = self._find_digital_io("FLOnOff")
    if channel_config.strobe:
      if strobe is None:
        raise CeligoError("Fluorescence channel requires the FLOnOff digital output")
      await self._set_channel_intensity(channel_config, intensity_percent)
      await self.set_digital_output(strobe.bit_index, not strobe.invert)
    else:
      if strobe is not None:
        await self.set_digital_output(strobe.bit_index, strobe.invert)
      await self._set_channel_intensity(channel_config, intensity_percent)

  # -- autofocus -------------------------------------------------------------

  def _validate_camera_geometry(self) -> None:
    """Reject a camera format that disagrees with the optical calibration geometry."""
    calibration = self.config.calibration
    width = self.camera.width
    height = self.camera.height
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
    calibration = self.config.calibration
    camera = self.camera
    expected = (calibration.image_width_pixels, calibration.image_height_pixels)
    if (camera.width, camera.height) == expected:
      return
    await camera.set_frame_format(*expected)
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
    calibration = self.config.calibration
    expected = (calibration.image_width_pixels, calibration.image_height_pixels)
    if (frame.width, frame.height) != expected:
      raise CeligoError(
        f"Captured frame {frame.width}x{frame.height} does not match calibrated "
        f"geometry {expected[0]}x{expected[1]}"
      )

  async def _ensure_camera_ready(
    self,
    require_calibrated_geometry: bool = True,
  ) -> CeligoCamera:
    camera = self.camera
    if not camera.is_open:
      await camera.setup()
    if require_calibrated_geometry:
      self._validate_camera_geometry()
    return camera

  async def set_camera_exposure_and_gain(
    self,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    restart_camera_stream: bool = False,
  ) -> Tuple[float, float]:
    """Set camera properties through the Celigo-owned camera lifecycle.

    ``restart_camera_stream=True`` closes and reopens the stream after applying settings.
    """
    camera = await self._ensure_camera_ready(require_calibrated_geometry=False)
    if exposure_ms is not None:
      await camera.set_exposure(exposure_ms)
    if gain is not None:
      await camera.set_gain(gain)
    if restart_camera_stream:
      await camera.stop()
      await camera.setup()
      await self._configure_camera_for_calibration()
    return camera.exposure_ms, camera.gain

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
    camera = await self._ensure_camera_ready()
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

  async def autofocus(
    self,
    autofocus_method: Literal["image", "hardware"] = "image",
    center_z_ticks: Optional[int] = None,
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
    if autofocus_method not in ("image", "hardware"):
      raise ValueError("autofocus_method must be 'image' or 'hardware'")
    if autofocus_method == "hardware":
      raise CeligoError(
        "Hardware autofocus is unavailable because no displacement-sensor interface "
        "is configured; use autofocus_method='image'"
      )
    if fine_step_ticks <= 0:
      raise ValueError("fine_step_ticks must be positive")
    if focus_flush_frames < 0:
      raise ValueError("focus_flush_frames must be non-negative")
    if verification_attempts < 1:
      raise ValueError("verification_attempts must be at least 1")
    if not 0 < minimum_verification_ratio <= 1:
      raise ValueError("minimum_verification_ratio must be in (0, 1]")
    z_axis = self.z_axis
    minimum_z_ticks, maximum_z_ticks = z_axis.encoder_bounds()
    selected_span_ticks = span_ticks if span_ticks is not None else 3000
    selected_coarse_step_ticks = 252 if coarse_step_ticks is None else coarse_step_ticks
    if selected_span_ticks < 0 or selected_coarse_step_ticks <= 0:
      raise ValueError("span_ticks must be non-negative and coarse_step_ticks positive")
    initial_z_ticks = await z_axis.request_encoder_ticks()
    if center_z_ticks is None:
      center_z_ticks = initial_z_ticks
    center_z_ticks = min(
      maximum_z_ticks,
      max(minimum_z_ticks, center_z_ticks),
    )

    score_frame = evaluator or (lambda frame: frame.sharpness())
    scored_z_samples: List[Tuple[int, float]] = []
    inspected_z_ticks: set[int] = set()

    def evaluate(frame: CameraFrame) -> float:
      score = float(score_frame(frame))
      if not math.isfinite(score):
        raise CeligoError("Autofocus evaluator returned a non-finite score")
      return score

    async def inspect(z_ticks: int) -> None:
      z_ticks = min(maximum_z_ticks, max(minimum_z_ticks, z_ticks))
      if z_ticks in inspected_z_ticks:
        return
      try:
        await z_axis.move_to_ticks(z_ticks)
        if settle_seconds > 0:
          await asyncio.sleep(settle_seconds)
        frame = await self.capture_frame(flush_frames=focus_flush_frames)
        score = evaluate(frame)
        inspected_z_ticks.add(z_ticks)
        scored_z_samples.append((z_ticks, score))
      except BaseException:
        with contextlib.suppress(Exception):
          await complete_cleanup(z_axis.move_to_ticks(initial_z_ticks))
        raise

    async def run_scan() -> FocusResult:
      coarse_start_z_ticks = max(
        minimum_z_ticks,
        center_z_ticks - selected_span_ticks,
      )
      coarse_stop_z_ticks = min(
        maximum_z_ticks,
        center_z_ticks + selected_span_ticks,
      )
      coarse_z_positions = list(
        range(
          coarse_start_z_ticks,
          coarse_stop_z_ticks + 1,
          selected_coarse_step_ticks,
        )
      )
      if not coarse_z_positions or coarse_z_positions[-1] != coarse_stop_z_ticks:
        coarse_z_positions.append(coarse_stop_z_ticks)
      for z_ticks in coarse_z_positions:
        await inspect(z_ticks)

      best_z_ticks = max(scored_z_samples, key=lambda item: item[1])[0]
      fine_start_z_ticks = max(
        minimum_z_ticks,
        best_z_ticks - selected_coarse_step_ticks,
      )
      fine_stop_z_ticks = min(
        maximum_z_ticks,
        best_z_ticks + selected_coarse_step_ticks,
      )
      fine_z_positions = list(range(fine_start_z_ticks, fine_stop_z_ticks + 1, fine_step_ticks))
      if not fine_z_positions or fine_z_positions[-1] != fine_stop_z_ticks:
        fine_z_positions.append(fine_stop_z_ticks)
      for z_ticks in fine_z_positions:
        await inspect(z_ticks)

      best_z_ticks, best_score = max(scored_z_samples, key=lambda item: item[1])
      focus_scores = [score for _, score in scored_z_samples]
      if max(focus_scores) - min(focus_scores) <= max(
        1e-12,
        abs(max(focus_scores)) * 1e-9,
      ):
        await z_axis.move_to_ticks(initial_z_ticks)
        raise CeligoError("Autofocus scan has no measurable focus contrast")
      if best_z_ticks in (
        min(z_ticks for z_ticks, _ in scored_z_samples),
        max(z_ticks for z_ticks, _ in scored_z_samples),
      ):
        await z_axis.move_to_ticks(initial_z_ticks)
        raise CeligoError("Autofocus optimum lies on the scan boundary")
      try:
        await z_axis.move_to_ticks(best_z_ticks)
        final_frame: Optional[CameraFrame] = None
        final_score = -math.inf
        for _ in range(verification_attempts):
          candidate_frame = await self.capture_frame(flush_frames=max(2, focus_flush_frames))
          candidate_score = evaluate(candidate_frame)
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
          await complete_cleanup(z_axis.move_to_ticks(initial_z_ticks))
        raise
      return FocusResult(
        z_ticks=best_z_ticks,
        z_mm=z_axis.encoder_ticks_to_mm(best_z_ticks),
        score=final_score,
        scored_z_samples=tuple(scored_z_samples),
        frame=final_frame,
      )

    return await run_scan()

  async def _acquire_field(
    self,
    well: str,
    channel: IlluminationChannelName,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    autofocus: Optional[Literal["image", "hardware"]] = None,
    z_mm: Optional[float] = None,
    require_lamp_ready: bool = False,
    galvo_offset_mm: Tuple[float, float] = (0.0, 0.0),
    machine_auto_exposure: bool = False,
  ) -> AcquisitionResult:
    """Navigate, select optics, optionally focus, and capture one calibrated FOV."""
    x_mm, y_mm = await self.move_to_well(well)
    channel_config = self._require_channel_config(channel)
    target_z_mm = z_mm
    if target_z_mm is None:
      target_z_mm = (
        self.config.calibration.calibrated_z_position + channel_config.z_offset_to_brightfield_mm
      )
    settled_z_mm = await self.z_axis.move_to(target_z_mm)
    await self.select_channel(channel, require_lamp_ready=require_lamp_ready)
    logical_filter = channel_config.logical_filter
    logical_galvo_voltages = self.galvo.voltages_for_offset(
      logical_filter,
      galvo_offset_mm,
    )
    galvo_hardware_voltages = await self.galvo.move_both(*logical_galvo_voltages)

    camera = await self._ensure_camera_ready()
    if exposure_ms is not None:
      await camera.set_exposure(exposure_ms)
    if gain is not None:
      await camera.set_gain(gain)
    await self.set_illumination_enabled(True)
    if machine_auto_exposure:
      await self.auto_exposure()

    focus_result: Optional[FocusResult] = None
    if autofocus is not None:
      focus_result = await self.autofocus(
        autofocus_method=autofocus,
        center_z_ticks=self.z_axis.mm_to_encoder_ticks(settled_z_mm),
      )
    frame = focus_result.frame if focus_result is not None else await camera.capture(flush_frames=2)
    self._validate_frame_geometry(frame)
    return AcquisitionResult(
      well=well,
      channel=channel,
      x_mm=x_mm,
      y_mm=y_mm,
      z_mm=focus_result.z_mm if focus_result is not None else settled_z_mm,
      frame=frame,
      focus=focus_result,
      galvo_hardware_voltages=galvo_hardware_voltages,
    )

  async def acquire(
    self,
    well: str,
    channel: IlluminationChannelName,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    autofocus: Optional[Literal["image", "hardware"]] = None,
    z_mm: Optional[float] = None,
    require_lamp_ready: bool = False,
    galvo_offset_mm: Tuple[float, float] = (0.0, 0.0),
    machine_auto_exposure: bool = False,
  ) -> AcquisitionResult:
    """Acquire one FOV and extinguish illumination before returning."""
    try:
      result = await self._acquire_field(
        well=well,
        channel=channel,
        exposure_ms=exposure_ms,
        gain=gain,
        autofocus=autofocus,
        z_mm=z_mm,
        require_lamp_ready=require_lamp_ready,
        galvo_offset_mm=galvo_offset_mm,
        machine_auto_exposure=machine_auto_exposure,
      )
    except BaseException:
      with contextlib.suppress(Exception):
        await complete_cleanup(self.turn_off_illumination())
      raise
    await complete_cleanup(self.turn_off_illumination())
    return result

  async def acquire_scan(
    self,
    wells: List[str],
    channels: List[IlluminationChannelName],
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    autofocus: Optional[Literal["image", "hardware"]] = None,
    scan_fovs: bool = False,
  ) -> List[AcquisitionResult]:
    """Acquire a well/channel plan, optionally scanning every configured galvo FOV."""
    if not wells or not channels:
      return []
    if scan_fovs:
      base_offsets = galvo_field_of_view_offsets_mm(
        self.config.calibration,
        self.config.navigation,
      )
    else:
      base_offsets = [(0.0, 0.0)]
    results: List[AcquisitionResult] = []
    focused_brightfield_z_mm: Optional[float] = None
    for well in wells:
      for channel in channels:
        channel_config = self._require_channel_config(channel)
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
            exposure_ms=exposure_ms,
            gain=gain,
            autofocus=focus_mode,
            z_mm=channel_z_mm,
            galvo_offset_mm=offset,
          )
          if result.focus is not None:
            focused_brightfield_z_mm = result.z_mm - channel_config.z_offset_to_brightfield_mm
          results.append(result)
    return results

  # -- camera synchronization -------------------------------------------------

  async def _send_signal_diagnostic_command(self, diagnostic_operation: int) -> int:
    """Send a SIGNAL_DIAGNOSTICS sub-command and return its int result."""
    response = await self.send_command(
      _CMD_SIGNAL_DIAGNOSTICS,
      struct.pack(">h", diagnostic_operation),
    )
    require_payload_length(response, 4, "signal diagnostics")
    return int(struct.unpack_from(">i", response, 0)[0])

  async def set_camera_trigger_line(self, asserted: bool) -> None:
    """Assert or clear the camera trigger line."""
    await self._send_signal_diagnostic_command(
      _DIAG_SET_TRIGGER if asserted else _DIAG_CLEAR_TRIGGER
    )

  async def pulse_camera_trigger(self) -> None:
    """Pulse the camera trigger line once."""
    await self._send_signal_diagnostic_command(_DIAG_PULSE_TRIGGER)

  @staticmethod
  def _decode_camera_signal(raw_value: int, inverted: bool) -> Optional[bool]:
    """Decode a camera input, returning ``None`` when firmware reports it unavailable."""
    if raw_value not in (0, 1):
      return None
    return bool(raw_value) != inverted

  async def request_is_camera_busy(self) -> Optional[bool]:
    """Read the polarity-corrected camera busy signal, or ``None`` when unavailable."""
    camera_config = self.config.hardware.external_camera_control
    inverted = camera_config.invert_busy if camera_config is not None else False
    return self._decode_camera_signal(
      await self._send_signal_diagnostic_command(_DIAG_READ_BUSY),
      inverted,
    )

  async def request_is_camera_integrating(self) -> Optional[bool]:
    """Read the camera integration signal, or ``None`` when unavailable."""
    camera_config = self.config.hardware.external_camera_control
    inverted = camera_config.invert_integration if camera_config is not None else False
    return self._decode_camera_signal(
      await self._send_signal_diagnostic_command(_DIAG_READ_INTEGRATION),
      inverted,
    )

  async def request_camera_trigger_encoder_ticks(self) -> int:
    """Read the encoder captured by the camera-trigger diagnostics path."""
    return await self._send_signal_diagnostic_command(_DIAG_READ_ENCODER)

  # -- diagnostics -----------------------------------------------------------

  async def run_self_test(
    self,
    run_active_checks: bool = False,
    run_motion_checks: bool = False,
  ) -> SelfTestReport:
    """Run controller diagnostics; hardware-changing checks require explicit opt-in.

    The default is read-only. ``run_active_checks=True`` centers the galvos and captures
    a camera frame when configured. ``run_motion_checks=True`` additionally performs a
    five-tick round-trip on X/Y/Z and therefore requires a clear motion envelope.
    """
    if run_motion_checks and not run_active_checks:
      raise ValueError("run_motion_checks requires run_active_checks=True")

    checks: Dict[str, Any] = {}
    failures: List[str] = []

    async def record(
      check_name: str,
      check: Callable[[], Awaitable[Any]],
    ) -> None:
      try:
        checks[check_name] = await check()
      except Exception as exc:  # diagnostics must report every check
        checks[check_name] = f"{type(exc).__name__}: {exc}"
        failures.append(check_name)

    async def check_motor_encoder_ratio(
      axis: Axis,
      check_name: str,
    ) -> Dict[str, Any]:
      actual_ratio = await axis.request_encoder_ratio()
      expected_ratio = axis.config.encoder_to_motor_tick_ratio
      matches = math.isclose(
        actual_ratio,
        expected_ratio,
        rel_tol=0.0,
        abs_tol=0.0005,
      )
      if not matches:
        failures.append(check_name)
      return {
        "actual": actual_ratio,
        "expected": expected_ratio,
        "matches": matches,
      }

    async def run_motion_round_trip(axis_name: LinearAxisName) -> Dict[str, int]:
      axis = self._require_linear_axis(axis_name)
      start_encoder_ticks = await axis.request_encoder_ticks()
      minimum_encoder_ticks, maximum_encoder_ticks = axis.encoder_bounds()
      if not minimum_encoder_ticks <= start_encoder_ticks <= maximum_encoder_ticks:
        raise CeligoError(
          f"Diagnostic {axis_name} start {start_encoder_ticks} is outside configured "
          f"bounds {minimum_encoder_ticks}..{maximum_encoder_ticks}"
        )
      target_encoder_ticks = (
        start_encoder_ticks + 5
        if start_encoder_ticks + 5 <= maximum_encoder_ticks
        else start_encoder_ticks - 5
      )
      if not minimum_encoder_ticks <= target_encoder_ticks <= maximum_encoder_ticks:
        raise CeligoError(
          f"No safe five-tick diagnostic move from {axis_name}={start_encoder_ticks}"
        )
      try:
        await axis.move_to_ticks(target_encoder_ticks)
      finally:
        # Always attempt restoration, including cancellation or a failed outward move.
        restoration = asyncio.create_task(axis.move_to_ticks(start_encoder_ticks))
        try:
          end_encoder_ticks = await asyncio.shield(restoration)
        except asyncio.CancelledError:
          with contextlib.suppress(Exception):
            await restoration
          raise
      return {
        "start": start_encoder_ticks,
        "end": end_encoder_ticks,
      }

    async def request_encoder_positions() -> Dict[str, int]:
      return {
        axis.name: await axis.request_encoder_ticks() for axis in self._configured_motion_axes()
      }

    await record("controller_status", self.request_controller_status)
    status = checks.get("controller_status")
    if isinstance(status, ControllerStatus) and status.has_controller_fault:
      failures.append("controller_status")
    await record("controller_info", self.request_controller_info)
    await record("motor_map", self.request_detected_motor_addresses)
    await record("encoders", request_encoder_positions)
    await record("digital_inputs", self.request_digital_input_bitmask)

    for logical_filter, transform in sorted(self.config.galvo_calibrations.items()):
      check_name = f"galvo_calibration_{logical_filter}"
      checks[check_name] = transform.successful
      if transform.successful is False:
        failures.append(check_name)

    for axis in self._configured_motion_axes():
      check_name = f"motor_{axis.axis_index}_encoder_ratio"
      await record(
        check_name,
        partial(check_motor_encoder_ratio, axis, check_name),
      )

    camera_config = self.config.hardware.external_camera_control
    if camera_config is not None and camera_config.enabled:
      await record("camera_busy", self.request_is_camera_busy)
      await record("camera_integration", self.request_is_camera_integrating)

    if run_active_checks:
      await record("galvo_center", self.galvo.home)
      await record("camera_frame", self.capture_frame)
    if run_motion_checks:
      for axis_name in _LINEAR_AXIS_NAMES:
        await record(
          f"{axis_name}_motion_round_trip",
          partial(run_motion_round_trip, axis_name),
        )

    # Preserve first occurrence while making the report deterministic.
    unique_failures = tuple(dict.fromkeys(failures))
    return SelfTestReport(not unique_failures, checks, unique_failures)
