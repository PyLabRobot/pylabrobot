import asyncio
from typing import Optional, Union

from pylabrobot.legacy.machines.machine import Machine

from .backend import PumpBackend
from .calibration import PumpCalibration


class Pump(Machine):
  """Frontend for a (peristaltic) pump."""

  def __init__(
    self,
    backend: PumpBackend,
    calibration: Optional[PumpCalibration] = None,
  ):
    super().__init__(backend=backend)
    self.backend: PumpBackend = backend  # fix type
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

  @classmethod
  def deserialize(cls, data: dict):
    data_copy = data.copy()
    calibration_data = data_copy.pop("calibration", None)
    if calibration_data is not None:
      calibration = PumpCalibration.deserialize(calibration_data)
      data_copy["calibration"] = calibration
    return super().deserialize(data_copy)

  async def run_revolutions(self, num_revolutions: float):
    await self.backend.run_revolutions(num_revolutions=num_revolutions)

  async def run_continuously(self, speed: float):
    await self.backend.run_continuously(speed=speed)

  async def run_for_duration(self, speed: Union[float, int], duration: Union[float, int]):
    if duration < 0:
      raise ValueError("Duration must be positive.")
    await self.run_continuously(speed=speed)
    await asyncio.sleep(duration)
    await self.run_continuously(speed=0)

  async def pump_volume(self, speed: Union[float, int], volume: Union[float, int]):
    if self.calibration is None:
      raise TypeError(
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

  async def halt(self):
    await self.backend.halt()
