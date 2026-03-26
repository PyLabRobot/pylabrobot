from dataclasses import dataclass
import enum

from pylabrobot.resources import Coordinate, Rotation


@dataclass
class GripperLocation:
  """Location and rotation of the gripper. Subclass for robot-specific fields."""

  location: Coordinate
  rotation: Rotation


class GripDirection(enum.Enum):
  FRONT = enum.auto()
  BACK = enum.auto()
  LEFT = enum.auto()
  RIGHT = enum.auto()


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
