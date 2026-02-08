from dataclasses import dataclass
from typing import Iterable

from pylabrobot.resources import Coordinate, Rotation

JointCoords = Iterable[float]


@dataclass
class GripperPose:
  location: Coordinate
  rotation: Rotation
