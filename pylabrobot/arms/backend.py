from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List


from pylabrobot.machines.backends import MachineBackend


class RoboticArmBackend(MachineBackend, metaclass=ABCMeta):
  """An abstract class for a robotic arm. Robotic arms are devices that can move around and pick
  up and put down objects.."""

  @abstractmethod
  async def setup(self) -> None:
    """Set up the plate reader. This should be called before any other methods."""

  @abstractmethod
  async def stop(self) -> None:
    """Close all connections to the plate reader and make sure setup() can be called again."""

  @abstractmethod
  async def send_command(self, command) -> None:
    """Open the plate reader. Also known as plate out."""

  @abstractmethod
  async def move(self, x, y, z, grip_angle) -> None:
    """Move the robotic arm to a specific location, likely with a specified angle."""

  @abstractmethod
  async def move_interpolate(self, x, y, z, grip_angle, speed) -> None:
    """Move the robotic arm to a specific location, likely with a specified angle, but interpolate
  between the current and target position for safety."""
