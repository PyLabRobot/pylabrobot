from enum import IntEnum


class XArm6Axis(IntEnum):
  """Joint indices for the xArm 6 robot (1-indexed, matching SDK servo_id)."""
  J1 = 1  # Base rotation
  J2 = 2  # Shoulder
  J3 = 3  # Elbow
  J4 = 4  # Wrist roll
  J5 = 5  # Wrist pitch
  J6 = 6  # Wrist yaw (end effector rotation)
