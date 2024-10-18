from .backend import RoboticArmBackend
from pylabrobot.machines import Machine

from logging import getLogger

logger = getLogger("pylabrobot")

class RoboticArm(Machine):
  def __init__(self, backend: RoboticArmBackend) -> None:
    self._backend = backend
    super().__init__(backend)

  async def send_command(self, command: dict) -> None:
    """Send a command to the robotic arm."""
    await self._backend.send_command(command)
    logger.debug(f"Sent command: {command}")

  async def move(self, x: int, y: int, z: int, grip_angle: float) -> None:
    """Move the robotic arm to a specific location."""
    await self._backend.move(x, y, z, grip_angle)
    logger.info(f"Moved robotic arm to {x}, {y}, {z} with grip angle {grip_angle}")

  async def move_interpolate(self, x: int, y: int, z: int, grip_angle: float, speed: float) -> None:
    """Move the robotic arm to a specific location, interpolating between the current and target
    position."""
    await self._backend.move_interpolate(x, y, z, grip_angle, speed)
    logger.info(f"Interpolated move of robotic arm to {x}, {y}, {z} with grip angle" + \
                f"{grip_angle} at speed {speed}")