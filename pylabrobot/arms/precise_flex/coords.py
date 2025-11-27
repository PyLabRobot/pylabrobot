from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pylabrobot.resources import Coordinate, Rotation


class ElbowOrientation(Enum):
  RIGHT = "right"
  LEFT = "left"


@dataclass
class CartesianCoords:
  location: Coordinate
  rotation: Rotation
  orientation: Optional[ElbowOrientation] = None
