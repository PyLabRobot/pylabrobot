
from pylabrobot.arms.backend import ArmBackend
from pylabrobot.machines.machine import Machine


class Arm(Machine):
    """A robotic arm."""

    def __init__(self, backend: ArmBackend):
        super().__init__(backend=backend)
        self.backend = backend

    async def move_to(self, position: tuple[float, float, float]):
        """Move the arm to a specified position in 3D space."""
        return self.backend.move_to(position)

    async def get_position(self) -> tuple[float, float, float]:
        """Get the current position of the arm in 3D space."""
        return await self.backend.get_position()

    async def set_speed(self, speed: float):
        """Set the speed of the arm's movement."""
        return await self.backend.set_speed(speed)

    async def get_speed(self) -> float:
        """Get the current speed of the arm's movement."""
        return await self.backend.get_speed()


