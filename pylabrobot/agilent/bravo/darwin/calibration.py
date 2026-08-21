"""Per-axis calibration constants for Darwin.

Hardware envelopes are hard-coded per axis below. Velocity and acceleration
limits are computed at runtime by reading ``ParamDBs.SPEED`` and
``ParamDBs.ACCELERATION`` from each device (see :func:`read_motion_limits`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from ..protocol.gemini.enums import ParamDBs
from ..types import Axis
from .params import ParameterAccess


@dataclass(frozen=True)
class AxisCalibration:
  """Hardware envelope, software limits, and calibration offset for one axis.

  Positions on the wire are normalized 0-1 against the hardware range::

    normalized = (position - calibration_offset - hardware_min) / hardware_range

  Software limits (:attr:`software_min`/:attr:`software_max`) are enforced
  before any move command is sent. Targeting a position outside
  ``[software_min, software_max]`` is rejected with a ``ValueError`` -- this
  prevents accidents like driving G to its hardware minimum, which can walk
  the gripper fingers off their rail.

  Software limits default to 0.07 mm inside the hardware range.

  Attributes:
    hardware_min: The axis's hardware travel minimum, in mm (or uL for W).
    hardware_max: The axis's hardware travel maximum.
    park_position: The position the axis reports once homing completes.
    calibration_offset: Offset applied between normalized and physical
      units, set from the instrument profile at runtime.
    software_min: The enforced move-target minimum, or ``None`` to default
      to ``hardware_min + 0.07``.
    software_max: The enforced move-target maximum, or ``None`` to default
      to ``hardware_max - 0.07``.
  """

  hardware_min: float
  hardware_max: float
  park_position: float = 0.0
  calibration_offset: float = 0.0
  software_min: Optional[float] = None
  software_max: Optional[float] = None

  @property
  def hardware_range(self) -> float:
    """The axis's total hardware travel span."""
    return self.hardware_max - self.hardware_min

  @property
  def effective_software_min(self) -> float:
    """The enforced move-target minimum, falling back to ``hardware_min + 0.07``."""
    return self.software_min if self.software_min is not None else self.hardware_min + 0.07

  @property
  def effective_software_max(self) -> float:
    """The enforced move-target maximum, falling back to ``hardware_max - 0.07``."""
    return self.software_max if self.software_max is not None else self.hardware_max - 0.07

  def to_normalized(self, position: float) -> float:
    """Convert a physical position to the wire's normalized 0-1 units.

    Args:
      position: The physical position, in mm (or uL for W).

    Returns:
      The normalized position.
    """
    return (position - self.calibration_offset - self.hardware_min) / self.hardware_range

  def from_normalized(self, normalized: float) -> float:
    """Convert a normalized 0-1 wire value back to physical units.

    Args:
      normalized: The normalized position, as read from the wire.

    Returns:
      The physical position, in mm (or uL for W).
    """
    return normalized * self.hardware_range + self.calibration_offset + self.hardware_min

  def validate_target(self, position_mm: float, axis_name: str) -> None:
    """Raise if a move target falls outside the software limits.

    Args:
      position_mm: The proposed target position, in mm (or uL for W).
      axis_name: The axis's display name, used in the error message.

    Raises:
      ValueError: If ``position_mm`` is outside
        ``[effective_software_min, effective_software_max]``.
    """
    lo = self.effective_software_min
    hi = self.effective_software_max
    if not lo <= position_mm <= hi:
      raise ValueError(
        f"Move target {position_mm:.4f} mm on axis {axis_name} is "
        f"outside software limits [{lo:.4f}, {hi:.4f}]. "
        f"Pass a value inside this range."
      )


# W axis is handled separately because its limits vary by head type.
# Software limits for the G axis are TIGHTER than the default hw+-0.07 margin
# would produce. Driving G too close to hardware_min walks the gripper
# fingers off their rail; driving too close to hardware_max can jam them
# closed. The instrument's own G software floors are [-7.513, 13.513]; the
# values below add extra safety margin on the minimum side.
DEFAULT_CALIBRATION: Dict[Axis, AxisCalibration] = {
  "y": AxisCalibration(hardware_min=-43.4, hardware_max=274.1, park_position=115.443),
  "x": AxisCalibration(hardware_min=-118.375, hardware_max=516.625, park_position=193.04),
  "z": AxisCalibration(hardware_min=-50.0, hardware_max=200.0, park_position=0.0),
  "g": AxisCalibration(
    hardware_min=-7.583,
    hardware_max=13.583,
    park_position=0.0,
    software_min=-7.0,  # Conservative: full open without rail walk-off.
    software_max=13.0,
  ),
  "zg": AxisCalibration(hardware_min=-74.5, hardware_max=179.5, park_position=0.0),
}


@dataclass(frozen=True)
class MotionLimits:
  """Derived velocity and acceleration ceilings, read from device parameters.

  Attributes:
    velocity: Velocity ceiling, in engineering units per second (mm/s for
      linear axes, uL/s for W).
    acceleration: Acceleration ceiling, in engineering units per second
      squared.
  """

  velocity: float
  acceleration: float


def read_motion_limits(params: ParameterAccess, calibration: AxisCalibration) -> MotionLimits:
  """Read SPEED/ACCELERATION parameters and scale by the axis's hardware range.

  The device stores SPEED/ACCELERATION as fractions of full travel::

    velocity_limit     = param(SPEED) * hardware_range
    acceleration_limit = param(ACCELERATION) * hardware_range

  Args:
    params: The parameter accessor for the axis's device.
    calibration: The axis's calibration, for its hardware range.

  Returns:
    The axis's velocity and acceleration ceilings in engineering units.
  """
  speed_frac = params.read_float(int(ParamDBs.SPEED))
  accel_frac = params.read_float(int(ParamDBs.ACCELERATION))
  rng = calibration.hardware_range
  return MotionLimits(
    velocity=speed_frac * rng,
    acceleration=accel_frac * rng,
  )
