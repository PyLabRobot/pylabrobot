from dataclasses import dataclass


@dataclass
class PreciseFlexJointCoords:
  rail: float
  base: float
  shoulder: float
  elbow: float
  wrist: float
  gripper: float

  def __iter__(self):
    # for conversion to a list
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
