from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Union

from pylabrobot.arms.precise_flex.coords import PreciseFlexCartesianCoords
from pylabrobot.arms.standard import JointCoords
from pylabrobot.machines.backend import MachineBackend


@dataclass
class VerticalAccess:
  """Access location from above (most common pattern for stacks and tube racks).

  This access pattern is used when approaching a location from above, such as
  picking from a plate stack or tube rack on the deck.

  Args:
    approach_height_mm: Height above the target position to move to before
                        descending to grip (default: 100mm)
    clearance_mm: Vertical distance to retract after gripping before lateral
                  movement (default: 100mm)
    gripper_offset_mm: Additional vertical offset added when holding a plate,
                      accounts for gripper thickness (default: 10mm)
  """

  approach_height_mm: float = 100
  clearance_mm: float = 100
  gripper_offset_mm: float = 10


@dataclass
class HorizontalAccess:
  """Access location from the side (for hotel-style plate carriers).

  This access pattern is used when approaching a location horizontally, such as
  accessing plates in a hotel-style storage system.

  Args:
    approach_distance_mm: Horizontal distance in front of the target to stop
                         before moving in to grip (default: 50mm)
    clearance_mm: Horizontal distance to retract after gripping before lifting
                  (default: 50mm)
    lift_height_mm: Vertical distance to lift the plate after horizontal retract,
                   before lateral movement (default: 100mm)
    gripper_offset_mm: Additional vertical offset added when holding a plate,
                      accounts for gripper thickness (default: 10mm)
  """

  approach_distance_mm: float = 50
  clearance_mm: float = 50
  lift_height_mm: float = 100
  gripper_offset_mm: float = 10


AccessPattern = Union[VerticalAccess, HorizontalAccess]


class SCARABackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a robotic arm"""

  @abstractmethod
  async def open_gripper(self):
    """Open the arm's gripper."""
    ...

  @abstractmethod
  async def close_gripper(self):
    """Close the arm's gripper."""
    ...

  @abstractmethod
  async def is_gripper_closed(self) -> bool:
    """Check if the gripper is currently closed."""
    ...

  @abstractmethod
  async def halt(self):
    """Stop any ongoing movement of the arm."""
    ...

  @abstractmethod
  async def home(self):
    """Home the arm to its default position."""
    ...

  @abstractmethod
  async def move_to_safe(self):
    """Move the arm to a predefined safe position."""
    ...

  @abstractmethod
  async def approach(
    self,
    position: Union[PreciseFlexCartesianCoords, JointCoords],
    access: Optional[AccessPattern] = None,
  ):
    """Move the arm to an approach position (offset from target).

    Args:
      position: Target position (CartesianCoords or JointCoords)
      access: Access pattern defining how to approach the target.
              Defaults to VerticalAccess() if not specified.
    """
    ...

  @abstractmethod
  async def pick_plate(
    self,
    position: Union[PreciseFlexCartesianCoords, JointCoords],
    access: Optional[AccessPattern] = None,
  ):
    """Pick a plate from the specified position.

    Args:
      position: Target position for pickup
      access: Access pattern defining how to approach and retract.
              Defaults to VerticalAccess() if not specified.
    """
    ...

  @abstractmethod
  async def place_plate(
    self,
    position: Union[PreciseFlexCartesianCoords, JointCoords],
    access: Optional[AccessPattern] = None,
  ):
    """Place a plate at the specified position.

    Args:
      position: Target position for placement
      access: Access pattern defining how to approach and retract.
              Defaults to VerticalAccess() if not specified.
    """
    ...

  @abstractmethod
  async def move_to(self, position: Union[PreciseFlexCartesianCoords, JointCoords]):
    """Move the arm to a specified position in 3D space."""
    ...

  @abstractmethod
  async def get_joint_position(self) -> JointCoords:
    """Get the current position of the arm in 3D space."""
    ...

  @abstractmethod
  async def get_cartesian_position(self) -> PreciseFlexCartesianCoords:
    """Get the current position of the arm in 3D space."""
    ...
