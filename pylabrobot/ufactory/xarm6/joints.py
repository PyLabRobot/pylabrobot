from enum import IntEnum


class XArm6Axis(IntEnum):
  """Joint indices for the xArm 6 robot (1-indexed, matching SDK servo_id)."""

  BASE_ROTATION = 1
  SHOULDER = 2
  ELBOW = 3
  WRIST_ROLL = 4
  WRIST_PITCH = 5
  WRIST_YAW = 6
