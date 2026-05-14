from dataclasses import dataclass
from typing import Dict, Literal, get_args

from pylabrobot.resources import Coordinate, Rotation

JointPose = Dict[int, float]


@dataclass
class CartesianPose:
  """Location and rotation of the gripper. Subclass for robot-specific fields."""

  location: Coordinate
  rotation: Rotation


# Cardinal directions in the deck frame. String-literal alias for
# ``OrientableArm`` ``direction`` arguments. Mapped to degrees by
# :data:`pylabrobot.capabilities.arms.orientable_arm._GRIPPER_DIRECTION_TO_DEGREES`
# under the standard ``rotation.z = 0 → +X (CCW about +Z)`` convention:
# ``"right" = 0°``, ``"back" = 90°``, ``"left" = 180°``, ``"front" = 270°``.
GripperDirection = Literal["front", "back", "left", "right"]
_GRIPPER_DIRECTION_VALUES = frozenset(get_args(GripperDirection))


@dataclass(frozen=True)
class ResourcePickup:
  location: Coordinate  # center of end effector when gripping the resource
  rotation: Rotation  # rotation of end effector when gripping the resource
  resource_width: float


@dataclass(frozen=True)
class ResourceMove:
  """Moving a resource that was already picked up."""

  location: Coordinate  # center of end effector when moving the resource
  rotation: Rotation  # rotation of end effector when moving the resource


@dataclass(frozen=True)
class ResourceDrop:
  location: Coordinate  # center of end effector when dropping the resource
  rotation: Rotation  # rotation of end effector when dropping the resource
  resource_width: float
