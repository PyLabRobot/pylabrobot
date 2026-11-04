from typing import List

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.pumps.backend import PumpArrayBackend, PumpBackend


class PumpChatterboxBackend(PumpBackend):
  """Chatter box backend for device-free testing. Prints out all operations."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    print("Setting up the pump.")
    stack.callback(print, "Stopping the pump.")

  async def run_revolutions(self, num_revolutions: float) -> None:
    print(f"Running {num_revolutions} revolutions.")

  async def run_continuously(self, speed: float) -> None:
    print(f"Running continuously at speed {speed}.")

  async def halt(self) -> None:
    print("Halting the pump.")


class PumpArrayChatterboxBackend(PumpArrayBackend):
  """Chatter box backend for device-free testing. Prints out all operations."""

  def __init__(self, num_channels: int = 8) -> None:
    super().__init__()
    self._num_channels = num_channels

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    print("Setting up the pump array.")
    stack.callback(print, "Stopping the pump array.")

  @property
  def num_channels(self) -> int:
    return self._num_channels

  async def run_revolutions(self, num_revolutions: List[float], use_channels: List[int]):
    print(f"Running {num_revolutions} revolutions on channels {use_channels}.")

  async def run_continuously(self, speed: List[float], use_channels: List[int]):
    print(f"Running continuously at speed {speed} on channels {use_channels}.")

  async def halt(self):
    print("Halting the pump array.")
