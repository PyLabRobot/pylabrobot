from abc import ABCMeta, abstractmethod
from typing import Dict, Optional, Union

from pylabrobot.arms.backend import AccessPattern
from pylabrobot.arms.standard import CartesianCoords
from pylabrobot.machines.backend import MachineBackend


class SixAxisBackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a 6-axis robotic arm."""

  @abstractmethod
  async def open_gripper(self, position: int, speed: int = 0) -> None:
    """Open the arm's gripper.

    Args:
      position: Target open position (gripper-specific units).
      speed: Gripper speed (0 = default/max).
    """

  @abstractmethod
  async def close_gripper(self, position: int, speed: int = 0) -> None:
    """Close the arm's gripper.

    Args:
      position: Target close position (gripper-specific units).
      speed: Gripper speed (0 = default/max).
    """

  @abstractmethod
  async def halt(self) -> None:
    """Emergency stop any ongoing movement of the arm."""

  @abstractmethod
  async def home(self) -> None:
    """Home the arm to its default position."""

  @abstractmethod
  async def move_to_safe(self) -> None:
    """Move the arm to a predefined safe position."""

  @abstractmethod
  async def approach(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
  ) -> None:
    """Move the arm to an approach position (offset from target).

    Args:
      position: Target position (CartesianCoords or joint position dict)
      access: Access pattern defining how to approach the target.
              Defaults to VerticalAccess() if not specified.
    """

  @abstractmethod
  async def pick_up_resource(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
  ) -> None:
    """Pick a resource from the specified position.

    Args:
      position: Target position for pickup
      access: Access pattern defining how to approach and retract.
              Defaults to VerticalAccess() if not specified.
    """

  @abstractmethod
  async def drop_resource(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
  ) -> None:
    """Place a resource at the specified position.

    Args:
      position: Target position for placement
      access: Access pattern defining how to approach and retract.
              Defaults to VerticalAccess() if not specified.
    """

  @abstractmethod
  async def move_to(self, position: Union[CartesianCoords, Dict[int, float]]) -> None:
    """Move the arm to a specified position in Cartesian or joint space."""

  @abstractmethod
  async def get_joint_position(self) -> Dict[int, float]:
    """Get the current position of the arm in joint space."""

  @abstractmethod
  async def get_cartesian_position(self) -> CartesianCoords:
    """Get the current position of the arm in Cartesian space."""

  @abstractmethod
  async def freedrive_mode(self) -> None:
    """Enter freedrive mode, allowing manual movement of all joints."""

  @abstractmethod
  async def end_freedrive_mode(self) -> None:
    """Exit freedrive mode."""
