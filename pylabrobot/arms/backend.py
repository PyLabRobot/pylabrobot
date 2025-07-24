from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class ArmBackend(MachineBackend, metaclass=ABCMeta):
    """Backend for a robotic arm"""

    @abstractmethod
    async def move_to(self, position: tuple[float, float, float]):
        """Move the arm to a specified position in 3D space."""
        ...

    @abstractmethod
    async def get_position(self) -> tuple[float, float, float]:
        """Get the current position of the arm in 3D space."""
        ...

    @abstractmethod
    async def set_speed(self, speed: float):
        """Set the speed of the arm's movement."""
        ...

    @abstractmethod
    async def get_speed(self) -> float:
        """Get the current speed of the arm's movement."""
        ...

    @abstractmethod
    async def open_gripper(self):
        """Open the arm's gripper."""
        ...

    @abstractmethod
    async def close_gripper(self):
        """Close the arm's gripper."""
        ...