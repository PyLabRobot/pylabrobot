from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pylabrobot.arms.standard import CartesianCoords


class ElbowOrientation(Enum):
  RIGHT = "right"
  LEFT = "left"


@dataclass
class PreciseFlexCartesianCoords(CartesianCoords):
  orientation: Optional[ElbowOrientation] = None
