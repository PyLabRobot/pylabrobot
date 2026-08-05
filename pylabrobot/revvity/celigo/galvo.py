"""Galvo mirror control for the Celigo image cytometer."""

import asyncio
import math
import struct
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

from pylabrobot.revvity.celigo.config import Calibrated2DPolynomialTransform, GalvoConfig
from pylabrobot.revvity.celigo.coordinates import sample_offset_mm_to_galvo_offset_mm
from pylabrobot.revvity.celigo.errors import CeligoError
from pylabrobot.revvity.celigo.protocol import require_payload_length

if TYPE_CHECKING:
  from pylabrobot.revvity.celigo.celigo import Celigo


GalvoAxisName = Literal["x", "y"]

# Controller-board opcodes owned by the galvo subsystem.
_CMD_MOVE_GALVO = 7
_CMD_REQUEST_CONTROLLER_STATUS = 12
_CMD_CALIBRATE_GALVO = 27
_CMD_GET_GALVO_CAL_DATA = 28
_CMD_SET_GALVO_WINDOW = 29
_CMD_GET_GALVO_POS_DATA = 31

_GALVO_INDEX: dict[GalvoAxisName, int] = {"x": 0, "y": 1}
_MAX_CONTROLLER_TIMEOUT_MILLISECONDS = 0xFFFF
_DAC_ZERO_VOLTS = 32767.5
_DAC_COUNTS_PER_VOLT = 3276.75
_POLYNOMIAL_MONOMIALS: Dict[str, Tuple[int, int]] = {
  "OffsetTerm": (0, 0),
  "LinearXTerm": (1, 0),
  "LinearYTerm": (0, 1),
  "QuadraticXTerm": (2, 0),
  "CrossTerm": (1, 1),
  "QuadraticYTerm": (0, 2),
  "CubicXTerm": (3, 0),
  "CubicYTerm": (0, 3),
  "QuadraticXLinearYTerm": (2, 1),
  "QuadraticYLinearXTerm": (1, 2),
}


@dataclass(frozen=True)
class GalvoControllerStatus:
  """Complete galvo/laser state returned by ``SEND_GALVO_INFO``."""

  x_busy: bool
  y_busy: bool
  x_hardware_voltage: float
  y_hardware_voltage: float
  fire_table_size: int
  points_loaded: int
  fire_table_index: int
  firing_status: int
  capture_armed: bool
  capture_table_size: int


def _timeout_seconds_to_controller_milliseconds(timeout: float) -> int:
  """Convert a PLR-standard timeout in seconds to controller milliseconds."""
  if not math.isfinite(timeout) or timeout < 0:
    raise ValueError("timeout must be a finite, non-negative number of seconds")
  timeout_milliseconds = round(timeout * 1000)
  if timeout_milliseconds > _MAX_CONTROLLER_TIMEOUT_MILLISECONDS:
    raise ValueError("timeout exceeds the controller's unsigned 16-bit range")
  return timeout_milliseconds


def volts_to_dac_count(volts: float) -> int:
  """Encode a galvo voltage as an unsigned 16-bit controller DAC count."""
  if not math.isfinite(volts) or not -10.0 <= volts <= 10.0:
    raise ValueError("volts must be finite and within the galvo DAC range -10..10 V")
  return round(volts * _DAC_COUNTS_PER_VOLT + _DAC_ZERO_VOLTS)


def dac_count_to_volts(dac_count: int) -> float:
  """Decode an unsigned 16-bit controller DAC count to galvo volts."""
  if not 0 <= dac_count <= 0xFFFF:
    raise ValueError("dac_count must be an unsigned 16-bit integer")
  return (dac_count - _DAC_ZERO_VOLTS) / _DAC_COUNTS_PER_VOLT


def voltage_delta_to_dac_count(voltage_delta: float) -> int:
  """Encode a non-negative voltage interval as a 16-bit DAC-count interval."""
  if not math.isfinite(voltage_delta) or voltage_delta < 0:
    raise ValueError("voltage_delta must be finite and non-negative")
  dac_count = round(voltage_delta * _DAC_COUNTS_PER_VOLT)
  if dac_count > 0xFFFF:
    raise ValueError("voltage_delta exceeds the controller's unsigned 16-bit range")
  return dac_count


def _evaluate_polynomial(
  terms: Dict[str, Tuple[float, float]],
  x_input: float,
  y_input: float,
) -> Tuple[float, float]:
  x_output = 0.0
  y_output = 0.0
  for name, (x_coefficient, y_coefficient) in terms.items():
    exponents = _POLYNOMIAL_MONOMIALS.get(name)
    if exponents is None:
      raise CeligoError(f"Unsupported galvo calibration polynomial term {name!r}")
    if not math.isfinite(x_coefficient) or not math.isfinite(y_coefficient):
      raise CeligoError(f"Galvo calibration polynomial term {name!r} is not finite")
    x_exponent, y_exponent = exponents
    monomial = (x_input**x_exponent) * (y_input**y_exponent)
    x_output += x_coefficient * monomial
    y_output += y_coefficient * monomial
  return x_output, y_output


def _mm_to_volts(
  calibration: Calibrated2DPolynomialTransform,
  x_mm: float,
  y_mm: float,
) -> Tuple[float, float]:
  """Convert a calibrated X/Y deflection in millimeters to voltage offsets."""
  return _evaluate_polynomial(calibration.reverse, x_mm, y_mm)


def logical_to_hardware_voltage(
  axis: GalvoAxisName,
  axis_config: GalvoConfig,
  logical_voltage: float,
) -> float:
  minimum_voltage, maximum_voltage = sorted((axis_config.min_voltage, axis_config.max_voltage))
  if (
    not math.isfinite(logical_voltage) or not minimum_voltage <= logical_voltage <= maximum_voltage
  ):
    raise CeligoError(
      f"{axis.upper()} galvo target {logical_voltage:.6g} V is outside configured "
      f"range {minimum_voltage:.6g}..{maximum_voltage:.6g} V"
    )
  return -logical_voltage if axis_config.invert_voltage else logical_voltage


def _require_axis_config(
  axis: GalvoAxisName,
  axis_config: Optional[GalvoConfig],
) -> GalvoConfig:
  if axis_config is None or not axis_config.enabled:
    raise CeligoError(f"{axis.upper()} galvo is not configured")
  return axis_config


class Galvo:
  """Galvo positioning, calibration, and status operations owned by a Celigo."""

  def __init__(self, celigo: "Celigo") -> None:
    self._celigo = celigo

  async def _initialize(self) -> None:
    """Configure, calibrate, and center both enabled galvos."""
    hardware = self._celigo.config.hardware
    axis_configs: Tuple[
      Tuple[GalvoAxisName, Optional[GalvoConfig]],
      Tuple[GalvoAxisName, Optional[GalvoConfig]],
    ] = (("x", hardware.x_galvo), ("y", hardware.y_galvo))
    configured: Dict[GalvoAxisName, GalvoConfig] = {
      axis: config for axis, config in axis_configs if config is not None and config.enabled
    }
    if len(configured) == 1:
      raise CeligoError("X and Y galvos must either both be enabled or both be absent")
    for axis, config in configured.items():
      await self._set_settling_window(
        axis,
        config.position_error_window,
        config.velocity_error_window,
      )
    for axis in configured:
      for _ in range(2):
        if not await self.calibrate(axis, timeout=0.9):
          raise CeligoError(f"{axis.upper()} galvo calibration failed")
    if configured:
      await self.home(magnification=self._celigo.config.magnification)

  def _calibration(self, logical_filter: int) -> Calibrated2DPolynomialTransform:
    try:
      return self._celigo.config.galvo_calibrations[logical_filter]
    except KeyError as exc:
      raise CeligoError(
        f"No galvo calibration is configured for logical filter {logical_filter}"
      ) from exc

  def _axis_config(self, axis: GalvoAxisName) -> GalvoConfig:
    hardware = self._celigo.config.hardware
    axis_config = hardware.x_galvo if axis == "x" else hardware.y_galvo
    return _require_axis_config(axis, axis_config)

  def _center_voltage(
    self,
    axis: GalvoAxisName,
    magnification: int,
    logical_filter: Optional[int],
  ) -> float:
    optical_calibration = self._celigo.config.galvo_optical_calibration
    axis_calibration = optical_calibration.x if axis == "x" else optical_calibration.y
    try:
      center_voltage = axis_calibration.magnifications[magnification].center_voltage
    except KeyError as exc:
      raise CeligoError(
        f"No {axis.upper()}-galvo center is calibrated for {magnification}X"
      ) from exc
    if logical_filter is not None:
      center_voltage += axis_calibration.logical_filter_offsets.get(logical_filter, 0.0)
    return center_voltage

  def voltages_for_offset(
    self,
    logical_filter: int,
    offset_mm: Tuple[float, float] = (0.0, 0.0),
  ) -> Tuple[float, float]:
    """Return galvo targets for an X-right/Y-down sample-relative field offset."""
    delta_x = delta_y = 0.0
    if logical_filter in self._celigo.config.galvo_calibrations:
      galvo_offset_mm = sample_offset_mm_to_galvo_offset_mm(*offset_mm)
      delta_x, delta_y = _mm_to_volts(self._calibration(logical_filter), *galvo_offset_mm)
    elif offset_mm != (0.0, 0.0):
      raise CeligoError(f"No galvo calibration is configured for logical filter {logical_filter}")
    magnification = self._celigo.config.magnification
    return (
      self._center_voltage("x", magnification, logical_filter) + delta_x,
      self._center_voltage("y", magnification, logical_filter) + delta_y,
    )

  async def move_single(
    self,
    axis: GalvoAxisName,
    logical_voltage: float,
    wait_until_settled: bool = True,
    timeout: float = 6.0,
  ) -> float:
    """Move one galvo to a logical voltage and return its hardware voltage.

    ``logical_voltage`` is in volts and ``timeout`` is in seconds.
    """
    timeout_milliseconds = _timeout_seconds_to_controller_milliseconds(timeout)
    axis_config = self._axis_config(axis)
    if not math.isfinite(axis_config.big_move_delay) or axis_config.big_move_delay < 0:
      raise CeligoError(f"{axis.upper()} galvo has an invalid configured post-move delay")
    hardware_voltage = logical_to_hardware_voltage(
      axis,
      axis_config,
      logical_voltage,
    )
    payload = struct.pack(
      ">HiHH",
      _GALVO_INDEX[axis],
      volts_to_dac_count(hardware_voltage),
      1 if wait_until_settled else 0,
      timeout_milliseconds if wait_until_settled else 0,
    )
    if wait_until_settled:
      # The board replies after the galvo settles or its own timeout expires. Keep
      # the host deadline beyond the advertised board timeout.
      host_timeout = max(self._celigo.reply_timeout, timeout + 1.0)
      response = await self._celigo.send_command(
        _CMD_MOVE_GALVO,
        payload,
        reply_timeout=host_timeout,
      )
    else:
      response = await self._celigo.send_command(_CMD_MOVE_GALVO, payload)
    if wait_until_settled:
      require_payload_length(response, 2, "galvo move")
      if struct.unpack_from(">H", response, 0)[0] != 0:
        raise CeligoError(f"{axis.upper()} galvo did not settle")
      if axis_config.big_move_delay:
        await asyncio.sleep(axis_config.big_move_delay)
    return hardware_voltage

  async def move_both(
    self,
    x_logical_voltage: float,
    y_logical_voltage: float,
    wait_until_settled: bool = True,
    timeout: float = 6.0,
  ) -> Tuple[float, float]:
    """Start both galvo moves, optionally wait for both, and return hardware voltages."""
    post_move_delay = max(
      self._axis_config("x").big_move_delay,
      self._axis_config("y").big_move_delay,
    )
    if not math.isfinite(post_move_delay) or post_move_delay < 0:
      raise CeligoError("Galvo configuration has an invalid post-move delay")
    x_hardware_voltage = await self.move_single(
      "x",
      x_logical_voltage,
      wait_until_settled=False,
      timeout=timeout,
    )
    y_hardware_voltage = await self.move_single(
      "y",
      y_logical_voltage,
      wait_until_settled=False,
      timeout=timeout,
    )
    if wait_until_settled:
      deadline = time.monotonic() + timeout
      while True:
        status = await self.request_controller_status()
        if not status.x_busy and not status.y_busy:
          break
        if time.monotonic() >= deadline:
          raise TimeoutError(f"Galvos did not settle within {timeout:g} seconds")
        await asyncio.sleep(0.005)
      if post_move_delay:
        await asyncio.sleep(post_move_delay)
    return x_hardware_voltage, y_hardware_voltage

  async def home(
    self,
    magnification: Optional[int] = None,
    logical_filter: Optional[int] = None,
  ) -> Tuple[float, float]:
    """Move both galvos to their calibrated imaging center."""
    selected_magnification = (
      self._celigo.config.magnification if magnification is None else magnification
    )
    x_center = self._center_voltage("x", selected_magnification, logical_filter)
    y_center = self._center_voltage("y", selected_magnification, logical_filter)
    return await self.move_both(x_center, y_center)

  async def request_controller_status(self) -> GalvoControllerStatus:
    """Read the complete galvo and laser-firing state from the controller."""
    response = await self._celigo.send_command(_CMD_REQUEST_CONTROLLER_STATUS)
    require_payload_length(response, 23, "galvo controller status")
    x_ready, y_ready, x_dac_count, y_dac_count = struct.unpack_from(">BBHH", response, 0)
    fire_table_size, points_loaded, fire_table_index = struct.unpack_from(">iii", response, 6)
    firing_status = response[18]
    capture_armed, capture_table_size = struct.unpack_from(">hh", response, 19)
    return GalvoControllerStatus(
      x_busy=x_ready == 0,
      y_busy=y_ready == 0,
      x_hardware_voltage=dac_count_to_volts(x_dac_count),
      y_hardware_voltage=dac_count_to_volts(y_dac_count),
      fire_table_size=fire_table_size,
      points_loaded=points_loaded,
      fire_table_index=fire_table_index,
      firing_status=firing_status,
      capture_armed=capture_armed != 0,
      capture_table_size=capture_table_size,
    )

  async def _set_settling_window(
    self,
    axis: GalvoAxisName,
    position_error_count: int,
    velocity_error_count: int,
  ) -> None:
    """Set one galvo's position and velocity settling tolerances."""
    payload = struct.pack(
      ">HHH",
      _GALVO_INDEX[axis],
      position_error_count,
      velocity_error_count,
    )
    await self._celigo.send_command(_CMD_SET_GALVO_WINDOW, payload)

  async def calibrate(
    self,
    axis: GalvoAxisName,
    timeout: float = 0.9,
  ) -> bool:
    """Run a galvo error-signal characterization sweep.

    ``timeout`` is in seconds. The return value reports whether the sweep succeeded.
    """
    timeout_milliseconds = _timeout_seconds_to_controller_milliseconds(timeout)
    payload = struct.pack(
      ">HHH",
      _GALVO_INDEX[axis],
      timeout_milliseconds,
      1,
    )
    response = await self._celigo.send_command(_CMD_CALIBRATE_GALVO, payload)
    require_payload_length(response, 2, "galvo calibration")
    calibration_status = int(struct.unpack_from(">H", response, 0)[0])
    return calibration_status == 0

  async def request_calibration_errors(
    self,
    axis: GalvoAxisName,
  ) -> List[Tuple[int, int]]:
    """Read one galvo's calibration error-count pairs."""
    response = await self._celigo.send_command(
      _CMD_GET_GALVO_CAL_DATA,
      struct.pack(">H", _GALVO_INDEX[axis]),
    )
    require_payload_length(response, 2, "galvo calibration data")
    (item_count,) = struct.unpack_from(">h", response, 0)
    if item_count < 0:
      raise CeligoError(f"Invalid galvo calibration item count: {item_count}")
    require_payload_length(response, 2 + 4 * item_count, "galvo calibration data")
    return [
      struct.unpack_from(">hh", response, 2 + 4 * item_index) for item_index in range(item_count)
    ]

  async def request_position_trace_dac_counts(self) -> List[Tuple[int, int]]:
    """Read captured galvo position/move pairs in controller DAC counts."""
    response = await self._celigo.send_command(_CMD_GET_GALVO_POS_DATA)
    require_payload_length(response, 2, "galvo position data")
    (position_count,) = struct.unpack_from(">H", response, 0)
    require_payload_length(response, 2 + 4 * position_count, "galvo position data")
    return [
      struct.unpack_from(">HH", response, 2 + 4 * position_index)
      for position_index in range(position_count)
    ]
