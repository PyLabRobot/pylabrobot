"""Per-axis calibration constants for DARWIN.

Ported from the hard-coded Configure-Axis / Configure-WAxis calls in
``darwin_bridge.ps1:404-441``. Velocity and acceleration limits are computed at
runtime by reading ``ParamDBs.SPEED`` and ``ParamDBs.ACCELERATION`` from each
device (see :func:`read_motion_limits`).
"""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.params import ParameterAccess
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import ParamDBs
from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis


@dataclass(frozen=True)
class AxisCalibration:
  """Hardware envelope + software limits + calibration offset for one axis.

  Positions on the wire are normalized 0-1 against the hardware range:
      normalized = (position - calibration_offset - hw_min) / (hw_max - hw_min)

  **Software limits (software_min/software_max) are enforced before any
  move command is sent.** Targeting a position outside [software_min,
  software_max] is rejected with a BravoError — this prevents accidents
  like driving G to its hardware minimum (which can walk the gripper
  fingers off their rail).

  Software limits default to 0.07mm inside the hardware range, matching
  the bridge's Configure-Axis convention.
  """

  hardware_min: float
  hardware_max: float
  park_position: float = 0.0
  calibration_offset: float = 0.0  # overridden from the profile at runtime
  software_min: float | None = None  # defaults to hardware_min + 0.07
  software_max: float | None = None  # defaults to hardware_max - 0.07

  @property
  def hardware_range(self) -> float:
    return self.hardware_max - self.hardware_min

  @property
  def effective_software_min(self) -> float:
    return self.software_min if self.software_min is not None else self.hardware_min + 0.07

  @property
  def effective_software_max(self) -> float:
    return self.software_max if self.software_max is not None else self.hardware_max - 0.07

  def to_normalized(self, position: float) -> float:
    return (position - self.calibration_offset - self.hardware_min) / self.hardware_range

  def from_normalized(self, normalized: float) -> float:
    return normalized * self.hardware_range + self.calibration_offset + self.hardware_min

  def validate_target(self, position_mm: float, axis_name: str) -> None:
    """Raise ValueError if ``position_mm`` is outside the software range."""
    lo = self.effective_software_min
    hi = self.effective_software_max
    if not (lo <= position_mm <= hi):
      raise ValueError(
        f"Move target {position_mm:.4f} mm on axis {axis_name} is "
        f"outside software limits [{lo:.4f}, {hi:.4f}]. "
        f"Pass a value inside this range."
      )


# Defaults from darwin_bridge.ps1:419-440. W axis is handled separately because
# its limits vary by head type.
# Software limits for the G axis are TIGHTER than the default hw±0.07 margin
# would produce. Driving G too close to hardware_min walks the gripper fingers
# off their rail; driving too close to hardware_max can jam them closed.
# Values match the bridge's gSoftwareMin/Max floors of [-7.513, 13.513] but
# with extra safety margin on the minimum side.
DEFAULT_CALIBRATION: dict[Axis, AxisCalibration] = {
  Axis.Y: AxisCalibration(hardware_min=-43.4, hardware_max=274.1, park_position=115.443),
  Axis.X: AxisCalibration(hardware_min=-118.375, hardware_max=516.625, park_position=193.04),
  Axis.Z: AxisCalibration(hardware_min=-50.0, hardware_max=200.0, park_position=0.0),
  Axis.G: AxisCalibration(
    hardware_min=-7.583,
    hardware_max=13.583,
    park_position=0.0,
    software_min=-7.0,  # conservative: full open without rail walk-off
    software_max=13.0,
  ),
  Axis.Zg: AxisCalibration(hardware_min=-74.5, hardware_max=179.5, park_position=0.0),
}


@dataclass(frozen=True)
class MotionLimits:
  """Derived velocity and acceleration ceilings, read from device parameters."""

  velocity: float  # units per second (mm/s for linear, μL/s for W)
  acceleration: float  # units per second²


def read_motion_limits(params: ParameterAccess, calibration: AxisCalibration) -> MotionLimits:
  """Read SPEED/ACCELERATION params and scale by hardware range.

  Mirrors BLDCAxis.VelocityLimit / AccelerationLimit computations:
      VelocityLimit     = param(SPEED) * hardware_range
      AccelerationLimit = param(ACCELERATION) * hardware_range
  """
  speed_frac = params.read_float(int(ParamDBs.SPEED))
  accel_frac = params.read_float(int(ParamDBs.ACCELERATION))
  rng = calibration.hardware_range
  return MotionLimits(
    velocity=speed_frac * rng,
    acceleration=accel_frac * rng,
  )
