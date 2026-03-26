"""Legacy. Use pylabrobot.agrowpumps or similar Device with PumpingCapability instead."""

import asyncio
from typing import List, Optional, Union

from pylabrobot.capabilities.pumping.pumping import PumpingCapability
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.legacy.pumps.backend import PumpArrayBackend
from pylabrobot.legacy.pumps.calibration import PumpCalibration
from pylabrobot.legacy.pumps.errors import NotCalibratedError


class PumpArray(Machine):
  """Legacy. Use AgrowDosePumpArray or similar Device with per-channel PumpingCapability instead."""

  def __init__(
    self,
    backend: PumpArrayBackend,
    calibration: Optional[PumpCalibration] = None,
  ):
    super().__init__(backend=backend)
    self.backend: PumpArrayBackend = backend  # fix type
    self.calibration = calibration

  @property
  def num_channels(self) -> int:
    return self.backend.num_channels

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

  async def run_revolutions(
    self,
    num_revolutions: Union[float, List[float]],
    use_channels: Union[int, List[int]],
  ):
    if isinstance(use_channels, int):
      use_channels = [use_channels]
    if isinstance(num_revolutions, float):
      num_revolutions = [num_revolutions] * len(use_channels)
    await self.backend.run_revolutions(num_revolutions=num_revolutions, use_channels=use_channels)

  async def run_continuously(
    self,
    speed: Union[float, int, List[float], List[int]],
    use_channels: Union[int, List[int]],
  ):
    if isinstance(use_channels, list) and len(set(use_channels)) != len(use_channels):
      raise ValueError("Channels in use channels must be unique.")
    if isinstance(use_channels, int):
      use_channels = [use_channels]
    if isinstance(speed, (float, int)):
      speed = [speed] * len(use_channels)

    if any(channel not in range(0, self.num_channels) for channel in use_channels):
      raise ValueError(
        f"Pump address out of range for this pump array. "
        f"Value should be between 0 and {self.num_channels}"
      )
    if any(s < 0 for s in speed):
      raise ValueError("Speed must be positive.")
    if isinstance(speed[0], int):
      speed = [float(x) for x in speed]
    if len(speed) != len(use_channels):
      raise ValueError("Speed and use_channels must be the same length.")
    if any(channel < 0 for channel in use_channels):
      raise ValueError("Channels in use channels must be positive.")

    await self.backend.run_continuously(
      speed=speed,  # type: ignore[arg-type]
      use_channels=use_channels,
    )

  async def run_for_duration(
    self,
    speed: Union[float, int, List[float], List[int]],
    use_channels: Union[int, List[int]],
    duration: Union[float, int],
  ):
    if duration < 0:
      raise ValueError("Duration must be positive.")
    await self.run_continuously(speed=speed, use_channels=use_channels)
    await asyncio.sleep(duration)
    await self.run_continuously(speed=0, use_channels=use_channels)

  async def pump_volume(
    self,
    speed: Union[float, int, List[float], List[int]],
    use_channels: Union[int, List[int]],
    volume: Union[float, int, List[float], List[int]],
  ):
    if self.calibration is None:
      raise NotCalibratedError(
        "Pump is not calibrated. Volume based pumping and related functions unavailable."
      )
    if isinstance(use_channels, int):
      use_channels = [use_channels]
    if isinstance(speed, (float, int)):
      speed = [speed] * len(use_channels)
    if isinstance(volume, (float, int)):
      volume = [volume] * len(use_channels)
    if not all(vol >= 0 for vol in volume):
      raise ValueError("Volume must be positive.")
    if not len(speed) == len(use_channels) == len(volume):
      raise ValueError("Speed, use_channels, and volume must be the same length.")
    if self.calibration.calibration_mode == "duration":
      durations = [
        channel_volume / self.calibration[channel]
        for channel, channel_volume in zip(use_channels, volume)
      ]
      tasks = [
        asyncio.create_task(
          self.run_for_duration(
            speed=channel_speed,
            use_channels=channel,
            duration=duration,
          )
        )
        for channel_speed, channel, duration in zip(speed, use_channels, durations)
      ]
    elif self.calibration.calibration_mode == "revolutions":
      num_rotations = [
        channel_volume / self.calibration[channel]
        for channel, channel_volume in zip(use_channels, volume)
      ]
      tasks = [
        asyncio.create_task(
          self.run_revolutions(num_revolutions=num_rotation, use_channels=channel)
        )
        for num_rotation, channel in zip(num_rotations, use_channels)
      ]
    else:
      raise ValueError("Calibration mode must be 'duration' or 'revolutions'.")
    await asyncio.gather(*tasks)

  async def halt(self):
    """Halt the entire pump array."""
    await self.backend.halt()
