from typing import List

from pylabrobot.pumps.backend import PumpBackend, PumpArrayBackend


class PumpChatterboxBackend(PumpBackend):
  """ Chatter box backend for device-free testing. Prints out all operations. """

  async def setup(self):
    print("Setting up the pump.")

  async def stop(self):
    print("Stopping the pump.")

  def run_revolutions(self, num_revolutions: float):
    print(f"Running {num_revolutions} revolutions.")

  def run_continuously(self, speed: float):
    print(f"Running continuously at speed {speed}.")

  def halt(self):
    print("Halting the pump.")


class PumpArrayChatterboxBackend(PumpArrayBackend):
  """ Chatter box backend for device-free testing. Prints out all operations. """

  def __init__(self, num_channels: int = 8) -> None:
    self._num_channels = num_channels

  async def setup(self):
    print("Setting up the pump array.")

  async def stop(self):
    print("Stopping the pump array.")

  @property
  def num_channels(self) -> int:
    return self._num_channels

  async def run_revolutions(self, num_revolutions: List[float], use_channels: List[int]):
    print(f"Running {num_revolutions} revolutions on channels {use_channels}.")

  async def run_continuously(self, speed: List[float], use_channels: List[int]):
    print(f"Running continuously at speed {speed} on channels {use_channels}.")

  async def halt(self):
    print("Halting the pump array.")
