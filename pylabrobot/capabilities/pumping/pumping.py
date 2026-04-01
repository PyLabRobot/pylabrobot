import asyncio
from typing import Optional, Union

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.capabilities.pumping.errors import NotCalibratedError

from .backend import PumpBackend
from .calibration import PumpCalibration


class Pump(Capability):
  """Single-pump capability.

  See :doc:`/user_guide/capabilities/pumping` for a walkthrough.
  """

  def __init__(
    self,
    backend: PumpBackend,
    calibration: Optional[PumpCalibration] = None,
  ):
    super().__init__(backend=backend)
    self.backend: PumpBackend = backend
    if calibration is not None and len(calibration) != 1:
      raise ValueError("Calibration may only have a single item for this pump")
    self.calibration = calibration

  def serialize(self) -> dict:
    if self.calibration is None:
      return super().serialize()
    return {
      **super().serialize(),
      "calibration": self.calibration.serialize(),
    }

  @need_capability_ready
  async def run_revolutions(self, num_revolutions: float):
    """Run for a given number of revolutions.

    Args:
      num_revolutions: number of revolutions to run.
    """
    await self.backend.run_revolutions(num_revolutions=num_revolutions)

  @need_capability_ready
  async def run_continuously(self, speed: float):
    """Run continuously at a given speed. If speed is 0, the pump will be halted.

    Args:
      speed: speed in rpm/pump-specific units.
    """
    await self.backend.run_continuously(speed=speed)

  @need_capability_ready
  async def run_for_duration(self, speed: Union[float, int], duration: Union[float, int]):
    """Run the pump at specified speed for the specified duration.

    Args:
      speed: speed in rpm/pump-specific units.
      duration: duration in seconds.
    """
    if duration < 0:
      raise ValueError("Duration must be positive.")
    await self.run_continuously(speed=speed)
    await asyncio.sleep(duration)
    await self.run_continuously(speed=0)

  @need_capability_ready
  async def pump_volume(self, speed: Union[float, int], volume: Union[float, int]):
    """Run the pump at specified speed for the specified volume. Requires calibration.

    Args:
      speed: speed in rpm/pump-specific units.
      volume: volume to pump.
    """
    if self.calibration is None:
      raise NotCalibratedError(
        "Pump is not calibrated. Volume based pumping and related functions unavailable."
      )
    if self.calibration.calibration_mode == "duration":
      duration = volume / self.calibration[0]
      await self.run_for_duration(speed=speed, duration=duration)
    elif self.calibration.calibration_mode == "revolutions":
      num_revolutions = volume / self.calibration[0]
      await self.run_revolutions(num_revolutions=num_revolutions)
    else:
      raise ValueError("Calibration mode not recognized.")

  @need_capability_ready
  async def halt(self):
    """Halt the pump."""
    await self.backend.halt()

  async def _on_stop(self):
    if self._setup_finished:
      await self.backend.halt()
    await super()._on_stop()
