import asyncio
from typing import Union, Optional, List

from pylabrobot.machine import MachineFrontend
from .backend import PumpArrayBackend
from .calibration import PumpCalibration


class PumpArray(MachineFrontend):
  """
  Front-end for a pump array.

  Attributes:
    backend: The backend that the pump array is controlled through.
    calibration: The calibration of the pump.

  Properties:
    num_channels: The number of channels that the pump array has.

  """

  def __init__(self, backend: PumpArrayBackend, calibration: Optional[PumpCalibration] = None):
    self.backend: PumpArrayBackend = backend
    self.calibration = calibration

  @property
  def num_channels(self) -> int:
    """
    num_channels(self): This method returns the number of channels that the pump array has.
    Returns:
      int: The number of channels that the pump array has.
    """
    return self.backend.num_channels

  async def run_revolutions(self, num_revolutions: Union[float, List[float]],
                            use_channels: Union[int, List[int]]):
    """
    Run the specified channels for the specified number of revolutions.
    Args:
      num_revolutions: number of revolutions to run pumps.
      use_channels: pump array channels to run.

    """
    if isinstance(use_channels, int):
      use_channels = [use_channels]
    if isinstance(num_revolutions, float):
      num_revolutions = [num_revolutions] * len(use_channels)
    await self.backend.run_revolutions(num_revolutions=num_revolutions, use_channels=use_channels)

  async def run_continuously(self, speed: Union[float, int, List[float], List[int]],
                             use_channels: Union[int, List[int]]):
    """
    Run the specified channels at the specified speeds.
    Args:
      speed: speed in rpm/pump-specific units.
      use_channels: pump array channels to run.
    """
    if isinstance(use_channels, int):
      use_channels = [use_channels]
    if isinstance(speed, (float, int)):
      speed = [speed] * len(use_channels)
    if isinstance(speed[0], int):
      speed = [float(x) for x in speed]
    if len(speed) == len(use_channels):
      raise ValueError("Speed and use_channels must be the same length.")
    await self.backend.run_continuously(speed=speed,  # type: ignore[arg-type]
                                        use_channels=use_channels)

  async def run_for_duration(self, speed: Union[float, int, List[float], List[int]],
                             use_channels: Union[int, List[int]],
                             duration: Union[float, int]):
    """
    Run the specified channels at the specified speeds for the specified duration.

    Args:
      speed: speed in rpm/pump-specific units.
      use_channels: pump array channels to run.
      duration: duration to run pumps (seconds).
    """
    if duration < 0:
      raise ValueError("Duration must be positive.")
    await self.run_continuously(speed=speed, use_channels=use_channels)
    await asyncio.sleep(duration)
    await self.run_continuously(speed=0, use_channels=use_channels)

  async def pump_volume(self, speed: Union[float, int, List[float], List[int]],
                        use_channels: Union[int, List[int]],
                        volume: Union[float, int, List[float], List[int]]):
    """
    Run the specified channels at the specified speeds for the specified volume. Note that this
    function requires the pump to be calibrated at the input speed.
    Args:
      speed: speed in rpm/pump-specific units.
      use_channels: pump array channels to run.
      volume: volume to pump.
    Raises:
      TypeError: if the pump is not calibrated.
    """
    if self.calibration is None:
      raise TypeError("Pump is not calibrated. Volume based pumping and related functions "
                      "unavailable.")
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
    durations = [channel_volume / self.calibration[channel] for channel, channel_volume in
                 zip(use_channels, volume)]
    tasks = [asyncio.create_task(
      self.run_for_duration(speed=channel_speed,
                            use_channels=channel,
                            duration=duration))
             for channel_speed, channel, duration in zip(speed, use_channels, durations)]
    await asyncio.gather(*tasks)

  async def halt(self):
    """
    Halt the entire pump array.
    """
    await self.backend.halt()
