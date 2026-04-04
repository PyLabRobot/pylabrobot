from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend


class LEDBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for LED control."""

  @abstractmethod
  async def set_color(
    self,
    mode: str,
    intensity: int,
    white: int,
    red: int,
    green: int,
    blue: int,
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
      backend_params: Vendor-specific parameters (e.g. UV, blink interval).
    """

  @abstractmethod
  async def turn_off(self) -> None:
    """Turn the LED off."""
