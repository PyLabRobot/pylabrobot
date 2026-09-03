"""Laser subsystem for the Celigo image cytometer."""

import contextlib
import math
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

from pylabrobot.revvity.celigo.config import GalvoConfig
from pylabrobot.revvity.celigo.errors import CeligoError
from pylabrobot.revvity.celigo.galvo import (
  GalvoAxisName,
  logical_to_hardware_voltage,
  voltage_delta_to_dac_count,
  volts_to_dac_count,
)
from pylabrobot.revvity.celigo.protocol import complete_cleanup, validate_payload_length

if TYPE_CHECKING:
  from pylabrobot.revvity.celigo.celigo import Celigo
  from pylabrobot.revvity.celigo.motion import Axis, FilterWheel

# Controller-board opcodes used only by the laser subsystem.
_CMD_LOAD_FIRING_TABLE = 1
_CMD_FIRE_GALVO_GRID = 6
_CMD_TARGETED_FIRE = 13
_CMD_FIRE_LASER = 24
_CMD_SEND_LASER_COMM = 26
_CMD_READ_LASER_COMM = 32
_DELAY_TICK_SECONDS = 10e-6
_MAX_DELAY_TICKS = 0x7FFFFFFF


@dataclass(frozen=True)
class _GridDacCounts:
  x_spacing: int
  y_spacing: int
  x_size: int
  y_size: int
  x_center: int
  y_center: int


def _delay_seconds_to_controller_ticks(delay: float) -> int:
  """Convert a PLR-standard delay in seconds to the controller's 10 µs ticks."""
  if not math.isfinite(delay) or delay < 0:
    raise ValueError("delay must be a finite, non-negative number of seconds")
  ticks = round(delay / _DELAY_TICK_SECONDS)
  if ticks > _MAX_DELAY_TICKS:
    raise ValueError("delay exceeds the controller's signed 32-bit range")
  return ticks


def _encode_firing_targets(
  x_config: Optional[GalvoConfig],
  y_config: Optional[GalvoConfig],
  voltage_offsets: List[Tuple[float, float]],
  center_voltages: Tuple[float, float],
) -> List[Tuple[int, int]]:
  if x_config is None or not x_config.enabled:
    raise CeligoError("X galvo is not configured")
  if y_config is None or not y_config.enabled:
    raise CeligoError("Y galvo is not configured")
  targets = []
  for x_offset, y_offset in voltage_offsets:
    targets.append(
      (
        volts_to_dac_count(
          logical_to_hardware_voltage(
            "x",
            x_config,
            center_voltages[0] + x_offset,
          )
        ),
        volts_to_dac_count(
          logical_to_hardware_voltage(
            "y",
            y_config,
            center_voltages[1] + y_offset,
          )
        ),
      )
    )
  return targets


def _validate_grid_extent(
  axis: GalvoAxisName,
  config: GalvoConfig,
  center_voltage: float,
  size_voltage: float,
) -> None:
  minimum_voltage, maximum_voltage = sorted((config.min_voltage, config.max_voltage))
  if (
    center_voltage - size_voltage / 2 < minimum_voltage
    or center_voltage + size_voltage / 2 > maximum_voltage
  ):
    raise CeligoError(
      f"Laser grid {axis.upper()} extent is outside {minimum_voltage}..{maximum_voltage} V"
    )


def _encode_grid(
  x_config: Optional[GalvoConfig],
  y_config: Optional[GalvoConfig],
  spacing_voltages: Tuple[float, float],
  size_voltages: Tuple[float, float],
  center_voltages: Tuple[float, float],
) -> _GridDacCounts:
  if x_config is None or not x_config.enabled:
    raise CeligoError("X galvo is not configured")
  if y_config is None or not y_config.enabled:
    raise CeligoError("Y galvo is not configured")
  if any(
    voltage <= 0 or not math.isfinite(voltage) for voltage in (*spacing_voltages, *size_voltages)
  ):
    raise ValueError("grid spacing and size voltages must be finite and positive")
  _validate_grid_extent("x", x_config, center_voltages[0], size_voltages[0])
  _validate_grid_extent("y", y_config, center_voltages[1], size_voltages[1])
  try:
    x_spacing = voltage_delta_to_dac_count(spacing_voltages[0])
    y_spacing = voltage_delta_to_dac_count(spacing_voltages[1])
    x_size = voltage_delta_to_dac_count(size_voltages[0])
    y_size = voltage_delta_to_dac_count(size_voltages[1])
  except ValueError as exc:
    raise CeligoError("Laser grid spacing/size exceeds controller encoding range") from exc
  return _GridDacCounts(
    x_spacing=x_spacing,
    y_spacing=y_spacing,
    x_size=x_size,
    y_size=y_size,
    x_center=volts_to_dac_count(logical_to_hardware_voltage("x", x_config, center_voltages[0])),
    y_center=volts_to_dac_count(logical_to_hardware_voltage("y", y_config, center_voltages[1])),
  )


class Laser:
  """Laser UART, firing, targeting, and optical controls owned by a Celigo."""

  def __init__(self, celigo: "Celigo", enabled: bool):
    self._celigo = celigo
    self._enabled = enabled

  @property
  def enabled(self) -> bool:
    """Whether laser commands were explicitly enabled at construction."""
    return self._enabled

  @property
  def nd_filter(self) -> "FilterWheel":
    """The laser neutral-density filter wheel."""
    return self._celigo._require_filter_wheel("laser_nd_filter")

  @property
  def attenuator(self) -> "Axis":
    """The laser attenuator motor."""
    return self._celigo._require_optical_axis("laser_attenuator")

  async def _assert_safe(self) -> None:
    if not self.enabled:
      raise CeligoError(
        "Laser commands are disabled; construct Celigo(..., allow_laser=True) only after "
        "completing the instrument laser-safety procedure"
      )
    status = await self._celigo.request_controller_status()
    if status.has_laser_safety_fault:
      raise CeligoError(f"Laser command blocked by controller safety status {status.raw_flags:#x}")

  async def send_command(self, command: str) -> None:
    """Send an ASCII command to the laser UART."""
    await self._assert_safe()
    await self._celigo.send_command(_CMD_SEND_LASER_COMM, command.encode("ascii") + b"\x00")

  async def request_uart_response(self) -> str:
    """Read an ASCII response from the laser UART."""
    await self._assert_safe()
    response = await self._celigo.send_command(_CMD_READ_LASER_COMM)
    validate_payload_length(response, 4, "laser response")
    response_length = struct.unpack_from(">H", response, 2)[0]
    validate_payload_length(response, 4 + response_length, "laser response")
    return response[4 : 4 + response_length].rstrip(b"\x00").decode("ascii", errors="replace")

  async def fire(
    self,
    laser_index: int,
    shots: int,
    delay: float = 0.0,
  ) -> None:
    """Fire one laser without galvo targeting.

    ``delay`` is the interval between shots in seconds. The controller encodes it in
    10 µs ticks.
    """
    if laser_index not in (0, 1):
      raise ValueError("laser_index must be 0 (LASER_1) or 1 (LASER_2)")
    if not 0 < shots <= 0x7FFFFFFF:
      raise ValueError("shots must fit in a positive signed 32-bit integer")
    delay_ticks = _delay_seconds_to_controller_ticks(delay)
    await self._assert_safe()
    try:
      await self._celigo.send_command(
        _CMD_FIRE_LASER,
        struct.pack(">Hii", laser_index, shots, delay_ticks),
      )
      timeout = max(
        5.0,
        self._celigo.move_timeout,
        max(0, shots - 1) * delay + 5.0,
      )
      if not await self._celigo.wait_for_controller_ready(timeout=timeout):
        raise TimeoutError("Laser firing did not complete")
    except BaseException:
      with contextlib.suppress(Exception):
        await complete_cleanup(self._celigo.abort_controller_operation())
      raise

  async def _load_firing_targets(
    self,
    voltage_offsets: List[Tuple[float, float]],
    center_voltages: Tuple[float, float],
  ) -> None:
    """Load voltage-offset targets around an explicit logical laser center."""
    if not voltage_offsets:
      raise ValueError("voltage_offsets must not be empty")
    await self._assert_safe()
    payload = struct.pack(">i", len(voltage_offsets))
    hardware = self._celigo.config.hardware
    for x_dac_count, y_dac_count in _encode_firing_targets(
      hardware.x_galvo,
      hardware.y_galvo,
      voltage_offsets,
      center_voltages,
    ):
      payload += struct.pack(">HH", x_dac_count, y_dac_count)
    await self._celigo.send_command(_CMD_LOAD_FIRING_TABLE, payload)
    if not await self._celigo.wait_for_controller_ready(timeout=5.0):
      raise TimeoutError("Controller did not finish loading laser targets")

  async def fire_targets(
    self,
    voltage_offsets: List[Tuple[float, float]],
    laser_index: int,
    pulses: int,
    delay_between_pulses: float = 0.0,
    center_voltages: Optional[Tuple[float, float]] = None,
  ) -> None:
    """Load and fire galvo targets in table-sized chunks.

    ``delay_between_pulses`` is expressed in seconds.
    """
    if not voltage_offsets:
      raise ValueError("voltage_offsets must not be empty")
    if laser_index not in (0, 1):
      raise ValueError("laser_index must be 0 (LASER_1) or 1 (LASER_2)")
    if not 0 < pulses <= 0xFFFFFFFF:
      raise ValueError("pulses must fit in a positive unsigned 32-bit integer")
    delay_ticks = _delay_seconds_to_controller_ticks(delay_between_pulses)
    await self._assert_safe()
    if center_voltages is None:
      optical = self._celigo.config.galvo_optical_calibration
      center_voltages = (
        optical.x.laser_center_voltage if laser_index == 0 else optical.x.uv_laser_center_voltage,
        optical.y.laser_center_voltage if laser_index == 0 else optical.y.uv_laser_center_voltage,
      )
    table_size = (await self._celigo.galvo.request_controller_status()).fire_table_size
    if table_size <= 0:
      raise CeligoError(f"Controller reported invalid laser firing-table size {table_size}")
    try:
      for start_index in range(0, len(voltage_offsets), table_size):
        chunk = voltage_offsets[start_index : start_index + table_size]
        await self._load_firing_targets(chunk, center_voltages)
        payload = struct.pack(">HIIH", laser_index, pulses, delay_ticks, 0)
        # Loading and waiting can take long enough for the door/interlock state to change.
        await self._assert_safe()
        await self._celigo.send_command(_CMD_TARGETED_FIRE, payload)
        timeout = max(
          5.0,
          self._celigo.move_timeout,
          len(chunk) * max(0, pulses - 1) * delay_between_pulses + 5.0,
        )
        if not await self._celigo.wait_for_controller_ready(timeout=timeout):
          raise TimeoutError("Targeted laser firing did not complete")
        status = await self._celigo.galvo.request_controller_status()
        if status.fire_table_index != status.points_loaded:
          raise CeligoError(
            f"Laser firing stopped at target {status.fire_table_index}/{status.points_loaded}"
          )
    except BaseException:
      with contextlib.suppress(Exception):
        await complete_cleanup(self._celigo.abort_controller_operation())
      raise

  async def fire_grid(
    self,
    laser_index: int,
    spacing_voltages: Tuple[float, float],
    size_voltages: Tuple[float, float],
    center_voltages: Tuple[float, float],
    pulses: int,
    repeats: int,
    delay_between_repeats: float = 0.0,
    pattern_bitmask: int = 0x1E,
  ) -> None:
    """Fire a firmware-generated galvo grid.

    ``delay_between_repeats`` is expressed in seconds. The default pattern bitmask
    selects the full grid.
    """
    if laser_index not in (0, 1):
      raise ValueError("laser_index must be 0 (LASER_1) or 1 (LASER_2)")
    if not 0 < pulses <= 0x7FFFFFFF or not 0 < repeats <= 0x7FFFFFFF:
      raise ValueError("pulses and repeats must fit in positive signed 32-bit integers")
    delay_ticks = _delay_seconds_to_controller_ticks(delay_between_repeats)
    hardware = self._celigo.config.hardware
    grid = _encode_grid(
      hardware.x_galvo,
      hardware.y_galvo,
      spacing_voltages,
      size_voltages,
      center_voltages,
    )
    payload = struct.pack(
      ">HHHHHHHiiiiH",
      laser_index,
      grid.x_spacing,
      grid.y_spacing,
      grid.x_size,
      grid.y_size,
      grid.x_center,
      grid.y_center,
      pulses,
      repeats,
      delay_ticks,
      pattern_bitmask,
      0,
    )
    await self._assert_safe()
    try:
      await self._celigo.send_command(_CMD_FIRE_GALVO_GRID, payload)
      timeout = max(
        5.0,
        self._celigo.move_timeout,
        max(0, repeats - 1) * delay_between_repeats + 5.0,
      )
      if not await self._celigo.wait_for_controller_ready(timeout=timeout):
        raise TimeoutError("Laser grid firing did not complete")
    except BaseException:
      with contextlib.suppress(Exception):
        await complete_cleanup(self._celigo.abort_controller_operation())
      raise
