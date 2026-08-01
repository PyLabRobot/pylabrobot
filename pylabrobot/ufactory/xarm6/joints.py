from dataclasses import dataclass
from enum import IntEnum
from typing import Dict

from pylabrobot.resources import Coordinate, Rotation


class XArm6Axis(IntEnum):
  """Joint indices for the xArm 6 robot (1-indexed, matching SDK servo_id)."""

  J1 = 1
  J2 = 2
  J3 = 3
  J4 = 4
  J5 = 5
  J6 = 6


# Joint angles in degrees, keyed by axis number (see the `XArm6Axis` enum).
JointPose = Dict[int, float]


@dataclass
class CartesianPose:
  """Location and rotation of the gripper."""

  location: Coordinate
  rotation: Rotation
