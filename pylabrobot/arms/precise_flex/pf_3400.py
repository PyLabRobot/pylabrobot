from typing import List

from pylabrobot.arms.precise_flex.joints import PreciseFlexJointCoords
from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex3400Backend(PreciseFlexBackend):
  """Backend for the PreciseFlex 3400 robotic arm."""

  def __init__(self, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__(host=host, port=port, timeout=timeout)

  def convert_to_joint_space(self, position: List[float]) -> PreciseFlexJointCoords:
    """Convert a tuple of joint angles to a PreciseFlexJointCoords object."""
    if len(position) != 6:
      raise ValueError("Position must be a tuple of 6 joint angles.")
    return PreciseFlexJointCoords(
      rail=0,
      base=position[0],
      shoulder=position[1],
      elbow=position[2],
      wrist=position[3],
      gripper=position[4],
    )

  # def convert_to_joints_array(self, position: PreciseFlexJointCoords) -> List[float]:
  #   """Convert a JointSpace object to a list of joint angles."""
  #   return [
  #     0,
  #     position.base,
  #     position.shoulder,
  #     position.elbow,
  #     position.wrist,
  #     position.gripper,
  #   ]  # PF400 has 5 joints, last is fixed
