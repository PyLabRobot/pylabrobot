from abc import ABCMeta, abstractmethod
from typing import Dict, List, Optional

from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend
from pylabrobot.resources import Coordinate
from pylabrobot.resources.rotation import Rotation

# ArmBackend:
# - pick_up_at_location
# - drop_at_location
# - move_to_location
# - request_gripper_location
# - is_holding_resource

# CanGrip
# - open_gripper
# - close_gripper
# - is_gripper_closed

# CanSuction
# - start_suction
# - stop_suction

# CanFreedrive
# - start_freedrive_mode
# - stop_freedrive_mode

# Joints
# - pick_up_at_joint_position
# - drop_at_joint_position
# - request_joint_position


class CanFreedrive(metaclass=ABCMeta):
  """Mixin for arms that support freedrive (manual guidance) mode."""

  @abstractmethod
  async def start_freedrive_mode(
    self,
    free_axes: Optional[List[int]] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Enter freedrive mode, allowing manual movement of the specified joints.

    Args:
      free_axes: List of joint indices to free. ``None`` or ``[0]`` mean
        the backend's default ("all freeable axes" — typically all motion
        axes, excluding load-bearing axes like a gripper that's holding a
        plate). Backends may reject per-axis selection if they only
        support all-or-nothing freedrive.
    """

  @abstractmethod
  async def stop_freedrive_mode(self, backend_params: Optional[BackendParams] = None) -> None:
    """Exit freedrive mode."""


class HasJoints(metaclass=ABCMeta):
  """Mixin for arms that can be controlled in joint space."""

  @abstractmethod
  async def pick_up_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up at the specified joint position."""

  @abstractmethod
  async def drop_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop at the specified joint position."""

  @abstractmethod
  async def move_to_joint_position(
    self, position: Dict[int, float], backend_params: Optional[BackendParams] = None
  ) -> None:
    """Move the arm to the specified joint position."""

  @abstractmethod
  async def request_joint_position(
    self, backend_params: Optional[BackendParams] = None
  ) -> Dict[int, float]:
    """Get the current position of the arm in joint space."""


Smokes = HasJoints


class CanGrip(metaclass=ABCMeta):
  """Mixin for arms that have a gripper."""

  @abstractmethod
  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Open the gripper to the specified width."""

  @abstractmethod
  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Close the gripper to the specified width."""

  @abstractmethod
  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    """Check if the gripper is currently closed."""


class _BaseArmBackend(CapabilityBackend, metaclass=ABCMeta):
  @abstractmethod
  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    """Stop any ongoing movement of the arm."""

  @abstractmethod
  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Park the arm to its default position."""

  @abstractmethod
  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    """Get the current location and rotation of the gripper."""


class GripperArmBackend(_BaseArmBackend, CanGrip, metaclass=ABCMeta):
  """Backend for a simple arm (no rotation capability). E.g. Hamilton core grippers."""

  @abstractmethod
  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up at the specified location."""

  @abstractmethod
  async def drop_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop at the specified location."""

  @abstractmethod
  async def move_to_location(
    self, location: Coordinate, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Move the held object to the specified location."""


class OrientableGripperArmBackend(_BaseArmBackend, CanGrip, metaclass=ABCMeta):
  """Backend for an arm with rotation capability. E.g. Hamilton iSwap."""

  @abstractmethod
  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up at the specified location with rotation."""

  @abstractmethod
  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop at the specified location with rotation."""

  @abstractmethod
  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move the held object to the specified location with rotation."""


class ArticulatedGripperArmBackend(_BaseArmBackend, CanGrip, metaclass=ABCMeta):
  @abstractmethod
  async def pick_up_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up at the specified location with rotation."""

  @abstractmethod
  async def drop_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop at the specified location with rotation."""

  @abstractmethod
  async def move_to_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move the held object to the specified location with rotation."""
