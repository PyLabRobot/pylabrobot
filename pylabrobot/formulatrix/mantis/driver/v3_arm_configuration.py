"""A v3 arm's homing calibration, from its unit's configuration rather than a recording."""

import math
from dataclasses import dataclass
from typing import Any, Dict, List

from .arm_kinematics import MantisArmCoordinate, compute_joint_angles
from .errors import MantisConfigurationError


def _check_finite(*, value: Any) -> None:
  """Raise ``ValueError`` unless a calibration value is a finite number, not a boolean.

  Args:
    value: the value as the block holds it.
  """
  if isinstance(value, bool):
    raise ValueError("a calibration value cannot be a boolean")
  if not math.isfinite(float(value)):
    raise ValueError("calibration values must be finite")


@dataclass(frozen=True)
class MotorHoming:
  """One motor's flag search; speed and acceleration in degrees or mm per second."""

  method: int
  positive_edge: bool
  positive_direction: bool
  speed: float
  acceleration: float


@dataclass(frozen=True)
class V3ArmConfiguration:
  """The unit's offsets, homing frame and route to idle, in degrees, mm and seconds."""

  offsets: Dict[int, float]
  homing_position_n: Dict[int, float]
  motors: Dict[int, MotorHoming]
  retract: float
  backoff: float
  jog_speed: float
  jog_acceleration: float
  motor_xy_homing_delay: float
  post_homing_positions: List[MantisArmCoordinate]

  @classmethod
  def from_block(cls, *, block: Dict[str, Any]) -> "V3ArmConfiguration":
    """Validate a saved ``arm`` block before a motor command can be sent.

    Args:
      block: the unit's ``arm`` block, as saved.
    """
    try:
      maps: Dict[str, Dict[int, float]] = {}
      for key in ("offsets", "homing_position_n"):
        maps[key] = {}
        for motor, value in block[key].items():
          _check_finite(value=value)
          maps[key][int(motor)] = float(value)
      if any(set(values) != {0, 1} for values in maps.values()):
        raise ValueError("offsets and homing_position_n must name motors 0 and 1")
      motors = {}
      for motor, values in block["motors"].items():
        for key in ("method", "speed", "acceleration"):
          _check_finite(value=values[key])
        method = float(values["method"])
        if method != int(method) or not 0 <= method <= 32767:
          raise ValueError("homing method must be a nonnegative int16")
        if any(type(values[key]) is not bool for key in ("positive_edge", "positive_direction")):
          raise ValueError("homing edge and direction must be booleans")
        speed, acceleration = float(values["speed"]), float(values["acceleration"])
        if min(speed, acceleration) <= 0:
          raise ValueError("homing speed and acceleration must be positive")
        motors[int(motor)] = MotorHoming(
          method=int(method),
          positive_edge=values["positive_edge"],
          positive_direction=values["positive_direction"],
          speed=speed,
          acceleration=acceleration,
        )
      if set(motors) != {0, 1, 2}:
        raise ValueError("homing settings must name motors 0, 1 and 2")
      scalars: Dict[str, float] = {}
      for key in ("retract", "backoff", "jog_speed", "jog_acceleration", "motor_xy_homing_delay"):
        _check_finite(value=block[key])
        scalars[key] = float(block[key])
      if (
        scalars["retract"] >= 0
        or min(scalars["backoff"], scalars["jog_speed"], scalars["jog_acceleration"]) <= 0
        or scalars["motor_xy_homing_delay"] < 0
      ):
        raise ValueError("retract must be negative, rates and backoff positive, delay nonnegative")
      positions = []
      for point in block["post_homing_positions"]:
        x, y = point
        _check_finite(value=x)
        _check_finite(value=y)
        position = MantisArmCoordinate(x=float(x), y=float(y), z=None)
        compute_joint_angles(location=position)
        positions.append(position)
      if not positions:
        raise ValueError("post_homing_positions must contain a route to idle")
      return cls(
        offsets=maps["offsets"],
        homing_position_n=maps["homing_position_n"],
        motors=motors,
        retract=scalars["retract"],
        backoff=scalars["backoff"],
        jog_speed=scalars["jog_speed"],
        jog_acceleration=scalars["jog_acceleration"],
        motor_xy_homing_delay=scalars["motor_xy_homing_delay"],
        post_homing_positions=positions,
      )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
      raise MantisConfigurationError(f"malformed v3 arm block: {error}") from error
