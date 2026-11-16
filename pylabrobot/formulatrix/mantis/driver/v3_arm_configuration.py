"""A v3 arm's homing calibration, from its unit's configuration rather than a recording."""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .arm_kinematics import compute_joint_angles
from .errors import MantisConfigurationError


def _finite(value: Any) -> float:
  """Read a finite calibration value, rejecting booleans and non-numbers."""
  if isinstance(value, bool):
    raise ValueError("a calibration value cannot be a boolean")
  number = float(value)
  if not math.isfinite(number):
    raise ValueError("calibration values must be finite")
  return number


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
  home_positions: Dict[int, float]
  motors: Dict[int, MotorHoming]
  retract: float
  backoff: float
  jog_speed: float
  jog_acceleration: float
  motor_delay: float
  post_home_positions: List[Tuple[float, float]]

  @classmethod
  def from_block(cls, block: Dict[str, Any]) -> "V3ArmConfiguration":
    """Validate a saved ``arm`` block before a motor command can be sent."""
    try:
      maps = {
        key: {int(motor): _finite(value) for motor, value in block[key].items()}
        for key in ("offsets", "home_positions")
      }
      if any(set(values) != {0, 1} for values in maps.values()):
        raise ValueError("offsets and home_positions must name motors 0 and 1")
      motors = {}
      for motor, values in block["motors"].items():
        method = _finite(values["method"])
        if method != int(method) or not 0 <= method <= 32767:
          raise ValueError("homing method must be a nonnegative int16")
        if any(type(values[key]) is not bool for key in ("positive_edge", "positive_direction")):
          raise ValueError("homing edge and direction must be booleans")
        speed, acceleration = _finite(values["speed"]), _finite(values["acceleration"])
        if min(speed, acceleration) <= 0:
          raise ValueError("homing speed and acceleration must be positive")
        motors[int(motor)] = MotorHoming(
          int(method), values["positive_edge"], values["positive_direction"], speed, acceleration
        )
      if set(motors) != {0, 1, 2}:
        raise ValueError("homing settings must name motors 0, 1 and 2")
      scalars = {
        key: _finite(block[key])
        for key in ("retract", "backoff", "jog_speed", "jog_acceleration", "motor_delay")
      }
      if (
        scalars["retract"] >= 0
        or min(scalars["backoff"], scalars["jog_speed"], scalars["jog_acceleration"]) <= 0
        or scalars["motor_delay"] < 0
      ):
        raise ValueError("retract must be negative, rates and backoff positive, delay nonnegative")
      positions = []
      for point in block["post_home_positions"]:
        x, y = point
        x, y = _finite(x), _finite(y)
        compute_joint_angles(x=x, y=y)
        positions.append((x, y))
      if not positions:
        raise ValueError("post_home_positions must contain a route to idle")
      return cls(
        offsets=maps["offsets"],
        home_positions=maps["home_positions"],
        motors=motors,
        retract=scalars["retract"],
        backoff=scalars["backoff"],
        jog_speed=scalars["jog_speed"],
        jog_acceleration=scalars["jog_acceleration"],
        motor_delay=scalars["motor_delay"],
        post_home_positions=positions,
      )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
      raise MantisConfigurationError(f"malformed v3 arm block: {error}") from error
