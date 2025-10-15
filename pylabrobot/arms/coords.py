from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
  orientation: Optional[ElbowOrientation] = None
