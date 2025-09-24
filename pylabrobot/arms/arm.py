
from pylabrobot.arms.backend import ArmBackend, CartesianCoords, JointCoords
from pylabrobot.machines.machine import Machine


class Arm(Machine):
    """A robotic arm."""

    def __init__(self, backend: ArmBackend):
        super().__init__(backend=backend)
        self.backend = backend

    async def move_to(self, position: CartesianCoords | JointCoords):
        """Move the arm to a specified position in 3D space."""
        return self.backend.move_to(position)

    async def get_joint_position(self) -> JointCoords:
        """Get the current position of the arm in 3D space."""
        return await self.backend.get_joint_position()

    async def get_cartesian_position(self) -> CartesianCoords:
        """Get the current position of the arm in 3D space."""
        return await self.backend.get_cartesian_position()

    async def set_speed(self, speed: float):
        """Set the speed of the arm's movement."""
        return await self.backend.set_speed(speed)

    async def get_speed(self) -> float:
        """Get the current speed of the arm's movement."""
        return await self.backend.get_speed()

    async def open_gripper(self):
        """Open the arm's gripper."""
        return await self.backend.open_gripper()

    async def close_gripper(self):
        """Close the arm's gripper."""
        return await self.backend.close_gripper()

    async def is_gripper_closed(self) -> bool:
        """Check if the gripper is currently closed."""
        return await self.backend.is_gripper_closed()

    async def halt(self):
        """Stop any ongoing movement of the arm."""
        return await self.backend.halt()

    async def home(self):
        """Home the arm to its default position."""
        return await self.backend.home()

    async def move_to_safe(self):
        """Move the arm to a predefined safe position."""
        return await self.backend.move_to_safe()

    async def approach(self, position: CartesianCoords | JointCoords, approach_height: float):
        """Move the arm to a position above the specified coordinates by a certain distance."""
        return await self.backend.approach(position, approach_height)

    async def pick_plate(self, position: CartesianCoords | JointCoords, approach_height: float):
        """Pick a plate from the specified position."""
        return await self.backend.pick_plate(position, approach_height)

    async def place_plate(self, position: CartesianCoords | JointCoords, approach_height: float):
        """Place a plate at the specified position."""
        return await self.backend.place_plate(position, approach_height)


