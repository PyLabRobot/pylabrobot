import asyncio
import random
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, Capability

from .backend import LEDBackend


class LEDControlCapability(Capability):
  """LED control capability with convenience methods."""

  def __init__(self, backend: LEDBackend):
    super().__init__(backend=backend)
    self.backend: LEDBackend = backend

  async def set_color(
    self,
    mode: str = "on",
    intensity: int = 100,
    white: int = 0,
    red: int = 0,
    green: int = 0,
    blue: int = 0,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Set the LED color.

    Args:
      mode: "on", "off", or "blink".
      intensity: Brightness 0-100.
      white: White channel 0-100.
      red: Red channel 0-100.
      green: Green channel 0-100.
      blue: Blue channel 0-100.
      backend_params: Vendor-specific parameters.
    """
    await self.backend.set_color(
      mode=mode, intensity=intensity,
      white=white, red=red, green=green, blue=blue,
      backend_params=backend_params,
    )

  async def turn_off(self) -> None:
    """Turn the LED off."""
    await self.backend.turn_off()

  async def disco_mode(self, cycles: int = 69, delay: float = 0.1) -> None:
    """Cycle through random colors.

    Args:
      cycles: Number of color changes.
      delay: Seconds between color changes.
    """
    for _ in range(cycles):
      r = random.randint(30, 100)
      g = random.randint(30, 100)
      b = random.randint(30, 100)
      await self.set_color(mode="on", intensity=100, red=r, green=g, blue=b)
      await asyncio.sleep(delay)
