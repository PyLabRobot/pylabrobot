from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pylabrobot.arms.standard import GripperPose


class ElbowOrientation(Enum):
  RIGHT = "right"
  LEFT = "left"


@dataclass
class PreciseFlexCartesianCoords(GripperPose):
  orientation: Optional[ElbowOrientation] = None
