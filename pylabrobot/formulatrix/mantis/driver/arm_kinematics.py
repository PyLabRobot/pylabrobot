"""The arm's kinematics and units: pure maths, nothing sent.

The arm is a five-bar linkage: two motors on fixed bases, each turning a short link, whose two long
links meet at the head. The arm frame has the base of motor 0's link at its origin and the other
base ``ARM_DISTANCE`` along x; it is in mm, and the head at idle sits at about (15.0, 31.2).

Motors 0 and 1 turn the links in degrees; motor 2, Z, travels in mm, down from its home flag. The
wire carries device units: 200 per revolution of a motor.
"""

import bisect
import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple, cast

from .errors import MantisKinematicsError


@dataclass(frozen=True)
class MantisArmCoordinate:
  """A position in the arm frame, mm: the unit's files' frame, not PLR's deck frame.

  ``Coordinate`` has its origin at the deck's front-left-bottom with z up; here the origin is the
  base of motor 0's link, and z is Z's position, down from its home flag. An axis is None where a
  route line holds it, or where only x and y are meant.
  """

  x: Optional[float]
  y: Optional[float]
  z: Optional[float]


# The link geometry, in mm and degrees; the same on a v3 and a v4.
RIGHT_SHORT_ARM_LENGTH = 63.0
RIGHT_LONG_ARM_LENGTH = 84.0
LEFT_SHORT_ARM_LENGTH = 63.0
LEFT_LONG_ARM_LENGTH = 84.0
ARM_DISTANCE = 30.0
GAMMA = 0.0
MIN_THETA_1 = -93.96
MIN_THETA_2 = -141.786
# Where each link pair turns, in the arm frame: the right pair at the origin.
RIGHT_BASE = MantisArmCoordinate(x=0.0, y=0.0, z=None)
LEFT_BASE = MantisArmCoordinate(
  x=ARM_DISTANCE * math.cos(math.radians(GAMMA)),
  y=ARM_DISTANCE * math.sin(math.radians(GAMMA)),
  z=None,
)

# Each motor's travel per revolution: degrees on the arms, mm on Z.
PITCH: Dict[int, float] = {0: 360.0, 1: 360.0, 2: 5.08}
# Full steps per revolution and the microstep divisor, the same on every motor of both generations.
STEPS_PER_REVOLUTION = 20000
MICROSTEPS = 100

# The absolute encoder spans 200 per revolution; its correction tables have 200 rows.
ABSOLUTE_ENCODER_SPAN = 200.0
# Above this, a motor's absolute position is taken one span lower, in device units.
ABSOLUTE_ENCODER_WRAP: Dict[int, float] = {0: 100.0, 1: 120.0}


def compute_device_position(*, motor: int, position: float) -> float:
  """A position in device units.

  Args:
    motor: 0, 1 (arm, degrees) or 2 (Z, mm).
    position: the position, in degrees or mm.
  """
  return STEPS_PER_REVOLUTION / PITCH[motor] * position / MICROSTEPS


def compute_position(*, motor: int, device_position: float) -> float:
  """A position from device units, in degrees (arms) or mm (Z).

  Args:
    motor: 0, 1 (arm) or 2 (Z).
    device_position: the position, in device units.
  """
  return device_position * MICROSTEPS / (STEPS_PER_REVOLUTION / PITCH[motor])


def compute_device_rate(*, motor: int, rate: float) -> float:
  """A speed or an acceleration in device units, rounded up half a step as on the wire.

  Args:
    motor: 0, 1 (arm, degrees/s or degrees/s²) or 2 (Z, mm/s or mm/s²).
    rate: the speed or acceleration; 0 stays 0, a motor that does not move.
  """
  if rate == 0:
    return 0.0
  return (STEPS_PER_REVOLUTION / PITCH[motor] * abs(rate) + 0.5) / MICROSTEPS


def compute_wrapped_angle(*, angle: float, minimum: float) -> float:
  """An angle moved by whole turns into ``[minimum, minimum + 360]``.

  Args:
    angle: the angle, in degrees.
    minimum: the lowest angle, in degrees.
  """
  while angle < minimum:
    angle += 360.0
  while angle > minimum + 360.0:
    angle -= 360.0
  return angle


def compute_joint_angles(*, location: MantisArmCoordinate) -> Tuple[float, float]:
  """The two links' angles that put the head at a location's x and y: inverse kinematics.

  The angles are the geometry's own, before a unit's motor offsets.

  Args:
    location: the head's position in the arm frame; its z is not used.

  Raises:
    MantisKinematicsError: if (x, y) is out of either link pair's reach or on a base.
    ValueError: if x or y is None.
  """
  x, y = location.x, location.y
  if x is None or y is None:
    raise ValueError("inverse kinematics needs the head's x and y")
  # The bases are always given in x and y.
  base_x, base_y = cast(float, LEFT_BASE.x), cast(float, LEFT_BASE.y)
  distance = math.hypot(x, y)
  distance_to_other_base = math.hypot(x - base_x, y - base_y)
  if distance == 0 or distance_to_other_base == 0:
    raise MantisKinematicsError(f"({x:.2f}, {y:.2f}) mm is on a motor's base")
  first = (RIGHT_SHORT_ARM_LENGTH**2 - RIGHT_LONG_ARM_LENGTH**2 + distance**2) / (
    2 * RIGHT_SHORT_ARM_LENGTH * distance
  )
  second = (LEFT_SHORT_ARM_LENGTH**2 - LEFT_LONG_ARM_LENGTH**2 + distance_to_other_base**2) / (
    2 * LEFT_SHORT_ARM_LENGTH * distance_to_other_base
  )
  if not -1.0 <= first <= 1.0 or not -1.0 <= second <= 1.0:
    raise MantisKinematicsError(f"({x:.2f}, {y:.2f}) mm is out of the arm's reach")
  theta_1 = 90 + math.degrees(math.asin(first) - math.atan2(y, x))
  theta_2 = 90 - math.degrees(math.pi - math.asin(second) - math.atan2(y - base_y, x - base_x))
  return (
    compute_wrapped_angle(angle=theta_1, minimum=MIN_THETA_1),
    compute_wrapped_angle(angle=theta_2, minimum=MIN_THETA_2),
  )


def compute_elbow_positions(
  *, theta_1: float, theta_2: float
) -> Tuple[MantisArmCoordinate, MantisArmCoordinate]:
  """Where each short link ends in the arm frame, motor 0's first; z None.

  Args:
    theta_1: motor 0's link angle, in degrees, the geometry's own.
    theta_2: motor 1's link angle, in degrees, the geometry's own.
  """
  # The bases are always given in x and y.
  base_x, base_y = cast(float, LEFT_BASE.x), cast(float, LEFT_BASE.y)
  first = math.radians(180.0 - theta_1)
  second = math.radians(theta_2)
  return (
    MantisArmCoordinate(
      x=RIGHT_SHORT_ARM_LENGTH * math.cos(first),
      y=RIGHT_SHORT_ARM_LENGTH * math.sin(first),
      z=None,
    ),
    MantisArmCoordinate(
      x=base_x + LEFT_SHORT_ARM_LENGTH * math.cos(second),
      y=base_y + LEFT_SHORT_ARM_LENGTH * math.sin(second),
      z=None,
    ),
  )


def compute_head_position(*, theta_1: float, theta_2: float) -> MantisArmCoordinate:
  """Where the head is in the arm frame for two link angles, z None: forward kinematics.

  Of the two solutions, the head's is the one left of the line from motor 0's elbow to motor 1's:
  the recorded recoveries route from it in every zone recorded, the back zones included.

  Args:
    theta_1: motor 0's link angle, in degrees, the geometry's own.
    theta_2: motor 1's link angle, in degrees, the geometry's own.

  Raises:
    MantisKinematicsError: if the long links cannot meet at these angles.
  """
  first, second = compute_elbow_positions(theta_1=theta_1, theta_2=theta_2)
  # The elbows are always given in x and y.
  first_x, first_y = cast(float, first.x), cast(float, first.y)
  second_x, second_y = cast(float, second.x), cast(float, second.y)
  distance = math.hypot(second_x - first_x, second_y - first_y)
  if (
    distance == 0
    or distance > RIGHT_LONG_ARM_LENGTH + LEFT_LONG_ARM_LENGTH
    or distance < abs(RIGHT_LONG_ARM_LENGTH - LEFT_LONG_ARM_LENGTH)
  ):
    raise MantisKinematicsError(
      f"the links cannot meet at angles ({theta_1:.2f}, {theta_2:.2f}) degrees"
    )
  along = (RIGHT_LONG_ARM_LENGTH**2 - LEFT_LONG_ARM_LENGTH**2 + distance**2) / (2 * distance)
  across = math.sqrt(max(0.0, RIGHT_LONG_ARM_LENGTH**2 - along**2))
  middle_x = first_x + along * (second_x - first_x) / distance
  middle_y = first_y + along * (second_y - first_y) / distance
  return MantisArmCoordinate(
    x=middle_x - across * (second_y - first_y) / distance,
    y=middle_y + across * (second_x - first_x) / distance,
    z=None,
  )


def compute_corrected_encoder_reading(
  *, reading: float, correction: Sequence[Tuple[float, float]]
) -> float:
  """An absolute encoder reading made linear by its one-revolution table.

  Between two measured samples the ideal values are interpolated; past the last sample the table
  continues into the first, one span on.

  Args:
    reading: the raw reading.
    correction: the table's (ideal, measured) rows, measured ascending.
  """
  measured = [row[1] for row in correction]
  ideal = [row[0] for row in correction]
  index = bisect.bisect_right(measured, reading) - 1
  if 0 <= index < len(measured) - 1:
    low, high = measured[index], measured[index + 1]
    share = (reading - low) / (high - low)
    return ideal[index] + share * (ideal[index + 1] - ideal[index])
  low, high = measured[-1], measured[0] + ABSOLUTE_ENCODER_SPAN
  value = reading + ABSOLUTE_ENCODER_SPAN if reading < low else reading
  share = (value - low) / (high - low)
  corrected = ideal[-1] + share * (ideal[0] + ABSOLUTE_ENCODER_SPAN - ideal[-1])
  half = ABSOLUTE_ENCODER_SPAN / 2
  return (corrected + half) % ABSOLUTE_ENCODER_SPAN - half


def compute_absolute_device_position(
  *,
  motor: int,
  reading: float,
  correction: Sequence[Tuple[float, float]],
  homing_position_a: float,
) -> float:
  """An arm motor's position, in device units, from its absolute encoder's raw reading.

  The corrected reading less the encoder reference, within one span; above ``ABSOLUTE_ENCODER_WRAP``
  one span lower.

  Args:
    motor: 0 or 1.
    reading: the raw reading.
    correction: the motor's (ideal, measured) table rows.
    homing_position_a: the motor's encoder reference, in the encoder's units.
  """
  corrected = compute_corrected_encoder_reading(reading=reading, correction=correction)
  position = (corrected - homing_position_a) % ABSOLUTE_ENCODER_SPAN
  if position > ABSOLUTE_ENCODER_WRAP[motor]:
    position -= ABSOLUTE_ENCODER_SPAN
  return position
