from typing import List

from pylabrobot.arms.precise_flex.joints import PreciseFlexJointCoords
from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex400Backend(PreciseFlexBackend):
  """Backend for the PreciseFlex 400 robotic arm."""

  def __init__(self, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__(host=host, port=port, timeout=timeout)

  def convert_to_joint_space(self, position: List[float]) -> PreciseFlexJointCoords:
    """Convert a list of joint angles to a PreciseFlexJointCoords object."""
    if len(position) != 6:
      raise ValueError("Position must be a list of 6 joint angles.")
    return PreciseFlexJointCoords(0, position[0], position[1], position[2], position[3], 0)

  def convert_to_joints_array(self, position: PreciseFlexJointCoords) -> List[float]:
    """Convert a PreciseFlexJointCoords object to a list of joint angles."""
    return [
      position.base,
      position.shoulder,
      position.elbow,
      position.wrist,
      0,
      0,
    ]  # PF400 has 4 joints, last two are fixed
