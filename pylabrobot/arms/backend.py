from abc import ABCMeta, abstractmethod
from typing import Dict, Optional

from pylabrobot.arms.standard import ArmPosition
from pylabrobot.device import DeviceBackend
from pylabrobot.resources import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.serializer import SerializableMixin


class _SharedArmBackend(DeviceBackend, metaclass=ABCMeta):
  @abstractmethod
  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    """Open the arm's gripper."""

  @abstractmethod
  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    """Close the arm's gripper."""

  @abstractmethod
  async def is_gripper_closed(self, backend_params: Optional[SerializableMixin] = None) -> bool:
    """Check if the gripper is currently closed."""

  @abstractmethod
  async def halt(self, backend_params: Optional[SerializableMixin] = None) -> None:
    """Stop any ongoing movement of the arm."""

  @abstractmethod
  async def park(self, backend_params: Optional[SerializableMixin] = None) -> None:
    """Park the arm to its default position."""



class ArmBackend(_SharedArmBackend, metaclass=ABCMeta):
  """Backend for a simple arm (no rotation capability). E.g. Hamilton core grippers."""

  @abstractmethod
  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Pick up at the specified location."""

  @abstractmethod
  async def drop_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Drop at the specified location."""

  @abstractmethod
  async def move_to_location(
    self, location: Coordinate, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    """Move the held object to the specified location."""


class OrientableArmBackend(_SharedArmBackend, metaclass=ABCMeta):
  """Backend for an arm with rotation capability. E.g. Hamilton iSwap."""

  @abstractmethod
  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Pick up at the specified location with rotation."""

  @abstractmethod
  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Drop at the specified location with rotation."""

  @abstractmethod
  async def move_to_location(
    self,
    location: Coordinate,
    direction: Rotation,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Move the held object to the specified location with rotation."""


class JointArmBackend(OrientableArmBackend, metaclass=ABCMeta):
  """Backend for a joint-space arm with rotation capability. E.g. PreciseFlex, KX2."""

  @abstractmethod
  async def pick_up_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Pick up at the specified joint position."""

  @abstractmethod
  async def drop_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Drop at the specified joint position."""

  @abstractmethod
  async def move_to_joint_position(
    self, position: Dict[int, float], backend_params: Optional[SerializableMixin] = None
  ) -> None:
    """Move the arm to the specified joint position."""

  @abstractmethod
  async def get_joint_position(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> Dict[int, float]:
    """Get the current position of the arm in joint space."""

  @abstractmethod
  async def get_cartesian_position(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> ArmPosition:
    """Get the current position of the arm in Cartesian space."""


class ArticulatedArmBackend(_SharedArmBackend, metaclass=ABCMeta):
  @abstractmethod
  async def pick_up_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Pick up at the specified location with rotation."""

  @abstractmethod
  async def drop_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Drop at the specified location with rotation."""

  @abstractmethod
  async def move_to_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Move the held object to the specified location with rotation."""
