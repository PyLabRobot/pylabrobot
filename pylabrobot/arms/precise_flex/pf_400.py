from typing import List

from pylabrobot.arms.precise_flex.joints import PreciseFlexJointCoords
from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex400Backend(PreciseFlexBackend):
  """Backend for the PreciseFlex 400 robotic arm."""

  def __init__(self, host: str, port: int = 10100, timeout=20, has_rail: bool = False) -> None:
    super().__init__(host=host, port=port, timeout=timeout)
    self._has_rail = has_rail

  def convert_to_joint_space(self, position: List[float]) -> PreciseFlexJointCoords:
    """Convert a list of joint angles to a PreciseFlexJointCoords object.

    Args:
      position: List of 6 joint angles in order: [rail, base, shoulder, elbow, wrist, gripper]
                This matches the output format of the wherej command.

    Returns:
      PreciseFlexJointCoords with all joint values mapped correctly.
    """
    if len(position) != 6:
      raise ValueError("Position must be a list of 6 joint angles.")
    return PreciseFlexJointCoords(
      rail=position[0],
      base=position[1],
      shoulder=position[2],
      elbow=position[3],
      wrist=position[4],
      gripper=position[5],
    )

  # def convert_to_joints_array(self, position: PreciseFlexJointCoords) -> List[float]:
  #   """Convert a PreciseFlexJointCoords object to a list of joint angles."""
  #   return [
  #     0,
  #     position.base,
  #     position.shoulder,
  #     position.elbow,
  #     position.wrist,
  #     0,
  #   ]  # PF400 has 4 joints, last two are fixed

  async def move_j(self, profile_index: int, joint_coords: PreciseFlexJointCoords) -> None:
    """Move the robot using joint coordinates, handling rail configuration."""
    if self._has_rail:
      angles_str = (
        f"{joint_coords.rail} "
        f"{joint_coords.base} "
        f"{joint_coords.shoulder} "
        f"{joint_coords.elbow} "
        f"{joint_coords.wrist} "
        f"{joint_coords.gripper}"
      )
    else:
      # Exclude rail for robots without rail
      angles_str = (
        f"{joint_coords.base} "
        f"{joint_coords.shoulder} "
        f"{joint_coords.elbow} "
        f"{joint_coords.wrist} "
        f"{joint_coords.gripper}"
      )
    await self.send_command(f"moveJ {profile_index} {angles_str}")

  async def set_joint_angles(
    self,
    location_index: int,
    joint_position: PreciseFlexJointCoords,
  ) -> None:
    """Set joint angles for stored location, handling rail configuration."""
    if self._has_rail:
      await self.send_command(
        f"locAngles {location_index} "
        f"{joint_position.rail} "
        f"{joint_position.base} "
        f"{joint_position.shoulder} "
        f"{joint_position.elbow} "
        f"{joint_position.wrist} "
        f"{joint_position.gripper}"
      )
    else:
      # Exclude rail for robots without rail
      await self.send_command(
        f"locAngles {location_index} "
        f"{joint_position.base} "
        f"{joint_position.shoulder} "
        f"{joint_position.elbow} "
        f"{joint_position.wrist} "
        f"{joint_position.gripper}"
      )
