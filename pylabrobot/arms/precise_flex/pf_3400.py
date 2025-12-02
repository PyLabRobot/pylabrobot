from typing import List

from pylabrobot.arms.precise_flex.joints import PreciseFlexJointCoords
from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex3400Backend(PreciseFlexBackend):
  """Backend for the PreciseFlex 3400 robotic arm."""

  def __init__(self, host: str, port: int = 10100, timeout=20, has_rail: bool = False) -> None:
    super().__init__(host=host, port=port, timeout=timeout)
    self._has_rail = has_rail

  def convert_to_joint_space(self, position: List[float]) -> PreciseFlexJointCoords:
    """Convert parsed joint values to PreciseFlexJointCoords.

    Args:
      position: List of 6 floats from _parse_angles_response() (always padded to 6).
                For has_rail=True: [rail, base, shoulder, elbow, wrist, gripper]
                For has_rail=False: [base, shoulder, elbow, wrist, gripper, 0.0(padding)]

    Returns:
      PreciseFlexJointCoords with joint values mapped based on robot configuration.
    """
    if len(position) < 5:
      raise ValueError("Position must have at least 5 joint angles.")

    if self._has_rail:
      if len(position) < 6:
        raise ValueError("Position must have 6 joint angles for robot with rail.")
      return PreciseFlexJointCoords(
        rail=position[0],
        base=position[1],
        shoulder=position[2],
        elbow=position[3],
        wrist=position[4],
        gripper=position[5],
      )
    else:
      # No rail: positions 0-4 are [base, shoulder, elbow, wrist, gripper]
      # position[5] is 0.0 padding from _parse_angles_response() - ignore it
      return PreciseFlexJointCoords(
        rail=0.0,
        base=position[0],
        shoulder=position[1],
        elbow=position[2],
        wrist=position[3],
        gripper=position[4],
      )

  def convert_to_joints_array(self, position: PreciseFlexJointCoords) -> List[float]:
    """Convert a JointSpace object to a list of joint angles."""
    return [
      position.rail,
      position.base,
      position.shoulder,
      position.elbow,
      position.wrist,
      position.gripper,
    ]

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
