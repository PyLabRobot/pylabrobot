from dataclasses import dataclass
from enum import Enum


class ElbowOrientation(Enum):
  RIGHT = "right"
  LEFT = "left"


@dataclass
class JointCoords:
  rail: float
  base: float
  shoulder: float
  elbow: float
  wrist: float
  gripper: float


@dataclass
class CartesianCoords:
  x: float
  y: float
  z: float
  yaw: float
  pitch: float
  roll: float
  orientation: ElbowOrientation | None = None
