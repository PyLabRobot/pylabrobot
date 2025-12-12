from dataclasses import dataclass
from typing import Iterator


@dataclass
class PreciseFlexJointCoords:
  base: float
  shoulder: float
  elbow: float
  wrist: float
  gripper: float
  rail: float = 0

  def __iter__(self) -> Iterator[float]:
    return iter(
      [
        self.rail,
        self.base,
        self.shoulder,
        self.elbow,
        self.wrist,
        self.gripper,
      ]
    )
