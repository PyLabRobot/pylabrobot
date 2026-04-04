"""Kinematics and coordinate mapping for the Formulatrix Mantis dispenser.

Provides:
  - :class:`MotorConfig` — unit conversion between native and packet units.
  - :class:`MantisKinematics` — inverse/forward kinematics for the dual-arm SCARA mechanism.
  - :class:`MantisMapGenerator` — microplate well-coordinate generation with homography correction.
"""

import math
from typing import Dict, List, Tuple

# ==========================================
# Arm geometry constants
# ==========================================

RIGHT_SHORT_ARM_LENGTH = 63.0
RIGHT_LONG_ARM_LENGTH = 84.0
LEFT_SHORT_ARM_LENGTH = 63.0
LEFT_LONG_ARM_LENGTH = 84.0
ARM_DISTANCE = 30.0
GAMMA = 0.0
MIN_THETA_1 = -93.96
MIN_THETA_2 = -141.786


class MotorConfig:
  """Encodes the mechanical coupling between motor steps and physical units (degrees or mm)."""

  def __init__(self, pitch: float, steps_per_rev: int, microsteps: int) -> None:
    self.pitch = pitch
    self.steps_per_rev = steps_per_rev
    self.microsteps = microsteps

  def from_packet_units(self, val_packet: float, is_velocity_or_accel: bool = False) -> float:
    """Convert packet units back to native units (degrees or mm)."""
    val_mapped = val_packet * self.microsteps
    slope = self.steps_per_rev / self.pitch
    if is_velocity_or_accel:
      if val_packet == 0:
        return 0.0
      val = (val_mapped - 0.5) / slope
    else:
      val = val_mapped / slope
    return val

  def to_packet_units(self, val: float, is_velocity_or_accel: bool = False) -> float:
    """Convert native units to packet units."""
    if is_velocity_or_accel and val == 0:
      return 0.0
    slope = self.steps_per_rev / self.pitch
    if is_velocity_or_accel:
      val_mapped = abs(slope * val) + 0.5
    else:
      val_mapped = slope * val
    val_packet = val_mapped / self.microsteps
    return val_packet


# Pre-configured motor instances
MOTOR_1_CONFIG = MotorConfig(pitch=360.0, steps_per_rev=20000, microsteps=100)
MOTOR_2_CONFIG = MotorConfig(pitch=360.0, steps_per_rev=20000, microsteps=100)
MOTOR_3_CONFIG = MotorConfig(pitch=5.08, steps_per_rev=20000, microsteps=100)


class MantisKinematics:
  """Inverse and forward kinematics for the Mantis dual-arm SCARA mechanism."""

  @staticmethod
  def normalize_degree(degree: float, min_value: float) -> float:
    """Normalize an angle to be within [min_value, min_value + 360)."""
    while degree < min_value or degree > min_value + 360:
      if degree < min_value:
        degree += 360
      if degree > min_value + 360:
        degree -= 360
    return degree

  @staticmethod
  def xy_to_theta(x: float, y: float) -> Tuple[float, float]:
    """Inverse kinematics: Cartesian (x, y) → joint angles (theta1, theta2).

    Args:
      x: X coordinate in mm.
      y: Y coordinate in mm.

    Returns:
      Tuple of (theta1, theta2) in degrees.

    Raises:
      ValueError: If (x, y) is at the origin or causes a singularity.
    """
    l1, l2 = RIGHT_SHORT_ARM_LENGTH, RIGHT_LONG_ARM_LENGTH
    l3, l4 = LEFT_SHORT_ARM_LENGTH, LEFT_LONG_ARM_LENGTH

    rad_gamma = math.radians(GAMMA)
    dx = ARM_DISTANCE * math.cos(rad_gamma)
    dy = ARM_DISTANCE * math.sin(rad_gamma)

    dist_sq = x**2 + y**2
    dist = math.sqrt(dist_sq)

    if dist == 0:
      raise ValueError("Target position X=0, Y=0 is invalid")

    a = (l1**2 - l2**2 + dist_sq) / (2 * l1) / dist
    a = max(-1.0, min(1.0, a))

    theta1 = 90 + math.degrees(math.asin(a) - math.atan2(y, x))

    dist_sq_2 = (x - dx) ** 2 + (y - dy) ** 2
    dist_2 = math.sqrt(dist_sq_2)

    if dist_2 == 0:
      raise ValueError("Target position causes singularity on left arm")

    b = (l3**2 - l4**2 + dist_sq_2) / (2 * l3) / dist_2
    b = max(-1.0, min(1.0, b))

    theta2 = 90 - math.degrees(math.pi - math.asin(b) - math.atan2(y - dy, x - dx))

    theta1 = MantisKinematics.normalize_degree(theta1, MIN_THETA_1)
    theta2 = MantisKinematics.normalize_degree(theta2, MIN_THETA_2)

    return theta1, theta2

  @staticmethod
  def theta_to_xy(theta1: float, theta2: float) -> List[Tuple[float, float]]:
    """Forward kinematics: joint angles → Cartesian (x, y).

    Returns up to two candidate solutions. Returns an empty list if the
    configuration is unreachable.
    """
    l1, l2 = RIGHT_SHORT_ARM_LENGTH, RIGHT_LONG_ARM_LENGTH
    l3, l4 = LEFT_SHORT_ARM_LENGTH, LEFT_LONG_ARM_LENGTH

    rad_gamma = math.radians(GAMMA)
    dx = ARM_DISTANCE * math.cos(rad_gamma)
    dy = ARM_DISTANCE * math.sin(rad_gamma)

    angle1_rad = math.radians(180 - theta1)
    angle2_rad = math.radians(theta2)

    e1_x = l1 * math.cos(angle1_rad)
    e1_y = l1 * math.sin(angle1_rad)

    e2_x = dx + l3 * math.cos(angle2_rad)
    e2_y = dy + l3 * math.sin(angle2_rad)

    d_sq = (e2_x - e1_x) ** 2 + (e2_y - e1_y) ** 2
    d = math.sqrt(d_sq)

    if d > (l2 + l4) or d < abs(l2 - l4) or d == 0:
      return []

    a = (l2**2 - l4**2 + d_sq) / (2 * d)
    h = math.sqrt(max(0, l2**2 - a**2))

    p2_x = e1_x + a * (e2_x - e1_x) / d
    p2_y = e1_y + a * (e2_y - e1_y) / d

    x3_1 = p2_x + h * (e2_y - e1_y) / d
    y3_1 = p2_y - h * (e2_x - e1_x) / d

    x3_2 = p2_x - h * (e2_y - e1_y) / d
    y3_2 = p2_y + h * (e2_x - e1_x) / d

    return [(x3_1, y3_1), (x3_2, y3_2)]


class MantisMapGenerator:
  """Generate machine coordinates for microplate wells using calibrated homography.

  The pipeline is:
    1. **Ideal coordinates** from A1 offset + linear pitch spacing.
    2. **Corrected coordinates** via a calibrated homography model that accounts
       for rotation, scaling, and non-linearities in the stage.

  Args:
    a1_x: X offset of well A1 in mm.
    a1_y: Y offset of well A1 in mm.
    row_pitch: Row-to-row distance in mm.
    col_pitch: Column-to-column distance in mm.
    rows: Number of rows.
    cols: Number of columns.
    z: Dispense height in mm.
  """

  # Calibrated stage mapping coefficients (homography)
  STAGE_H = [
    0.984626377954,
    0.012677857034,
    -46.6545491181,
    -0.022595831852,
    0.988661019679,
    43.0071907794,
    -0.000031372585,
    -0.000076070276,
  ]

  def __init__(
    self,
    a1_x: float = 14.38,
    a1_y: float = 11.24,
    row_pitch: float = 7.5,
    col_pitch: float = 7.5,
    rows: int = 8,
    cols: int = 12,
    z: float = 44.331,
  ) -> None:
    self.a1_x = a1_x
    self.a1_y = a1_y
    self.row_pitch = row_pitch
    self.col_pitch = col_pitch
    self.rows = rows
    self.cols = cols
    self.z = z

  def get_corrected_coordinate(self, ideal_x: float, ideal_y: float) -> Tuple[float, float]:
    """Map ideal stage coordinate → corrected machine coordinate via homography."""
    h1, h2, h3, h4, h5, h6, h7, h8 = self.STAGE_H
    denom = h7 * ideal_x + h8 * ideal_y + 1.0
    machine_x = (h1 * ideal_x + h2 * ideal_y + h3) / denom
    machine_y = (h4 * ideal_x + h5 * ideal_y + h6) / denom
    return machine_x, machine_y

  def get_well_coordinate(self, row: int, col: int) -> Dict[str, object]:
    """Generate the machine coordinate for a single well.

    Args:
      row: 0-indexed row (0 = A).
      col: 0-indexed column (0 = 1).

    Returns:
      A dictionary with keys ``well``, ``row``, ``col``, ``x``, ``y``, ``z``.
    """
    ideal_x = self.a1_x + (col * self.col_pitch)
    ideal_y = self.a1_y + (row * self.row_pitch)
    mx, my = self.get_corrected_coordinate(ideal_x, ideal_y)
    return {
      "well": f"{chr(65 + row)}{col + 1}",
      "row": row,
      "col": col,
      "x": round(mx, 3),
      "y": round(my, 3),
      "z": round(self.z, 3),
    }

  def generate_map(self) -> List[Dict[str, object]]:
    """Generate the dispense map for the entire plate."""
    results = []
    for r in range(self.rows):
      for c in range(self.cols):
        results.append(self.get_well_coordinate(r, c))
    return results
