import asyncio
from typing import List, Optional, Union

from pylabrobot.capabilities.pumping.backend import PumpBackend as _NewPumpBackend
from pylabrobot.capabilities.pumping.pumping import PumpingCapability
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.legacy.pumps.backend import PumpArrayBackend
from pylabrobot.legacy.pumps.calibration import PumpCalibration
from pylabrobot.legacy.pumps.errors import NotCalibratedError


class _ChannelAdapter(_NewPumpBackend):
  """Adapts one channel of a legacy PumpArrayBackend to the new PumpBackend."""

  def __init__(self, legacy: PumpArrayBackend, channel: int):
    self._legacy = legacy
    self._channel = channel

  async def run_revolutions(self, num_revolutions: float):
    await self._legacy.run_revolutions(
      num_revolutions=[num_revolutions], use_channels=[self._channel]
    )

  async def run_continuously(self, speed: float):
    await self._legacy.run_continuously(speed=[speed], use_channels=[self._channel])

  async def halt(self):
    await self._legacy.run_continuously(speed=[0.0], use_channels=[self._channel])


class PumpArray(Machine):
  """Front-end for a pump array."""

  def __init__(
    self,
    backend: PumpArrayBackend,
    calibration: Optional[PumpCalibration] = None,
  ):
    super().__init__(backend=backend)
    self.backend: PumpArrayBackend = backend
    self.calibration = calibration
    self._pumps: List[PumpingCapability] = []

  @property
  def num_channels(self) -> int:
    return self.backend.num_channels

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    self._pumps = [
      PumpingCapability(backend=_ChannelAdapter(self.backend, ch))
      for ch in range(self.num_channels)
    ]
    for p in self._pumps:
      await p._on_setup()

  async def stop(self):
    for p in reversed(self._pumps):
      await p._on_stop()
    await super().stop()

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

  # -- helpers ----------------------------------------------------------------

  def _normalize_channels(self, use_channels: Union[int, List[int]]) -> List[int]:
    if isinstance(use_channels, int):
      use_channels = [use_channels]
    if len(set(use_channels)) != len(use_channels):
      raise ValueError("Channels in use channels must be unique.")
    if any(ch not in range(0, self.num_channels) for ch in use_channels):
      raise ValueError(
        f"Pump address out of range for this pump array. "
        f"Value should be between 0 and {self.num_channels}"
      )
    if any(ch < 0 for ch in use_channels):
      raise ValueError("Channels in use channels must be positive.")
    return use_channels

  @staticmethod
  def _normalize_speeds(
    speed: Union[float, int, List[float], List[int]], n: int
  ) -> List[float]:
    if isinstance(speed, (float, int)):
      speed = [float(speed)] * n
    if any(s < 0 for s in speed):
      raise ValueError("Speed must be positive.")
    if len(speed) != n:
      raise ValueError("Speed and use_channels must be the same length.")
    return [float(s) for s in speed]

  # -- public API -------------------------------------------------------------

  async def run_revolutions(
    self,
    num_revolutions: Union[float, List[float]],
    use_channels: Union[int, List[int]],
  ):
    channels = self._normalize_channels(use_channels)
    if isinstance(num_revolutions, (float, int)):
      num_revolutions = [float(num_revolutions)] * len(channels)
    for ch, rev in zip(channels, num_revolutions):
      await self._pumps[ch].run_revolutions(num_revolutions=rev)

  async def run_continuously(
    self,
    speed: Union[float, int, List[float], List[int]],
    use_channels: Union[int, List[int]],
  ):
    channels = self._normalize_channels(use_channels)
    speeds = self._normalize_speeds(speed, len(channels))
    for ch, s in zip(channels, speeds):
      await self._pumps[ch].run_continuously(speed=s)

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
    channels = self._normalize_channels(use_channels)
    speeds = self._normalize_speeds(speed, len(channels))
    if isinstance(volume, (float, int)):
      volume = [float(volume)] * len(channels)
    if not all(vol >= 0 for vol in volume):
      raise ValueError("Volume must be positive.")
    if len(volume) != len(channels):
      raise ValueError("Speed, use_channels, and volume must be the same length.")
    if self.calibration.calibration_mode == "duration":
      durations = [
        channel_volume / self.calibration[channel]
        for channel, channel_volume in zip(channels, volume)
      ]
      tasks = [
        asyncio.create_task(
          self.run_for_duration(speed=s, use_channels=ch, duration=d)
        )
        for s, ch, d in zip(speeds, channels, durations)
      ]
    elif self.calibration.calibration_mode == "revolutions":
      num_rotations = [
        channel_volume / self.calibration[channel]
        for channel, channel_volume in zip(channels, volume)
      ]
      tasks = [
        asyncio.create_task(
          self.run_revolutions(num_revolutions=r, use_channels=ch)
        )
        for r, ch in zip(num_rotations, channels)
      ]
    else:
      raise ValueError("Calibration mode must be 'duration' or 'revolutions'.")
    await asyncio.gather(*tasks)

  async def halt(self):
    """Halt the entire pump array."""
    await self.backend.halt()
