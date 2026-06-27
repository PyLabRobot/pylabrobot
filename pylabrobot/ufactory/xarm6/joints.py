from enum import IntEnum


class XArm6Axis(IntEnum):
  """Joint indices for the xArm 6 robot (1-indexed, matching SDK servo_id)."""

  J1 = 1
  J2 = 2
  J3 = 3
  J4 = 4
  J5 = 5
  J6 = 6
