from dataclasses import dataclass
from typing import Iterable

from pylabrobot.resources import Coordinate, Rotation

JointCoords = Iterable[float]


@dataclass
class CartesianCoords:
  location: Coordinate
  rotation: Rotation
