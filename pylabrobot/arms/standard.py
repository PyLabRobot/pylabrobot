from dataclasses import dataclass
from typing import List

from pylabrobot.resources import Coordinate, Rotation

JointCoords = List[float]


@dataclass
class CartesianCoords:
  location: Coordinate
  rotation: Rotation
