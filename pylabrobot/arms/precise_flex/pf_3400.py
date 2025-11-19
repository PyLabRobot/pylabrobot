from typing import Optional

from pylabrobot.arms.coords import CartesianCoords, ElbowOrientation, JointCoords
from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend
from pylabrobot.resources import Coordinate, Rotation


class PreciseFlex400Backend(PreciseFlexBackend):
  """Backend for the PreciseFlex 400 robotic arm."""

  def __init__(self, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__(host=host, port=port, timeout=timeout)

  def convert_to_joint_space(
    self, position: tuple[float, float, float, float, float, float]
  ) -> JointCoords:
    """Convert a tuple of joint angles to a JointCoords object."""
    if len(position) != 6:
      raise ValueError("Position must be a tuple of 6 joint angles.")
    return JointCoords(0, position[0], position[1], position[2], position[3], position[4])

  def convert_to_joints_array(
    self, position: JointCoords
  ) -> tuple[float, float, float, float, float, float]:
    """Convert a JointSpace object to a list of joint angles."""
    joints = (
      position.base,
      position.shoulder,
      position.elbow,
      position.wrist,
      position.gripper,
      0,
    )  # PF400 has 5 joints, last is fixed
    return joints
