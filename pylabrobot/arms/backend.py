from abc import ABCMeta, abstractmethod
from enum import Enum

from pylabrobot.machines.backend import MachineBackend

class ElbowOrientation(Enum):
    LEFT = "left"
    RIGHT = "right"

class ArmBackend(MachineBackend, metaclass=ABCMeta):
    """Backend for a robotic arm"""

    @abstractmethod
    async def set_speed(self, speed: float):
        """Set the speed percentage of the arm's movement (0-100)."""
        ...

    @abstractmethod
    async def get_speed(self) -> float:
        """Get the current speed percentage of the arm's movement."""
        ...

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
    async def approach_j(self, joint_position: tuple[float, float, float, float, float, float, float], approach_height: float):
        """Move the arm to a position above the specified coordinates by a certain distance."""
        ...

    @abstractmethod
    async def pick_plate_j(self, joint_position: tuple[float, float, float, float, float, float, float], approach_height: float):
        """Pick a plate from the specified position."""
        ...

    @abstractmethod
    async def place_plate_j(self, joint_position: tuple[float, float, float, float, float, float, float], approach_height: float):
        """Place a plate at the specified position."""
        ...

    @abstractmethod
    async def move_to_j(self, joint_position: tuple[float, float, float, float, float, float, float]):
        """Move the arm to a specified position in 3D space."""
        ...

    @abstractmethod
    async def get_position_j(self) -> tuple[float, float, float, float, float, float, float]:
        """Get the current position of the arm in 3D space."""
        ...

    @abstractmethod
    async def approach_c(self, cartesian_position: tuple[float, float, float, float, float, float], approach_height: float, orientation: ElbowOrientation | None = None):
        """Move the arm to a position above the specified coordinates by a certain distance."""
        ...

    @abstractmethod
    async def pick_plate_c(self, cartesian_position: tuple[float, float, float, float, float, float], approach_height: float, orientation: ElbowOrientation | None = None):
        """Pick a plate from the specified position."""
        ...

    @abstractmethod
    async def place_plate_c(self, cartesian_position: tuple[float, float, float, float, float, float], approach_height: float, orientation: ElbowOrientation | None = None):
        """Place a plate at the specified position."""
        ...

    @abstractmethod
    async def move_to_c(self, cartesian_position: tuple[float, float, float, float, float, float], orientation: ElbowOrientation | None = None):
        """Move the arm to a specified position in 3D space."""
        ...

    @abstractmethod
    async def get_position_c(self) -> tuple[float, float, float, float, float, float, ElbowOrientation | None]:
        """Get the current position of the arm in 3D space."""
        ...