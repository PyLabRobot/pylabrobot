from typing import Optional, Union

from pylabrobot.capabilities.pumping.backend import PumpBackend as _NewPumpBackend
from pylabrobot.capabilities.pumping.pumping import Pump as _NewPump
from pylabrobot.legacy.machines.machine import Machine

from .backend import PumpBackend
from .calibration import PumpCalibration


class _PumpAdapter(_NewPumpBackend):
  """Adapts a legacy PumpBackend to the new PumpBackend (CapabilityBackend)."""

  def __init__(self, legacy: PumpBackend):
    self._legacy = legacy

  async def run_revolutions(self, num_revolutions: float):
    self._legacy.run_revolutions(num_revolutions=num_revolutions)

  async def run_continuously(self, speed: float):
    self._legacy.run_continuously(speed=speed)

  async def halt(self):
    self._legacy.halt()


class Pump(Machine):
  """Frontend for a (peristaltic) pump."""

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
    self._pumping = _NewPump(backend=_PumpAdapter(backend), calibration=calibration)

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    await self._pumping._on_setup()

  async def stop(self):
    await self._pumping._on_stop()
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
      calibration = PumpCalibration.deserialize(calibration_data)  # type: ignore[attr-defined]
      data_copy["calibration"] = calibration
    return super().deserialize(data_copy)

  async def run_revolutions(self, num_revolutions: float):
    await self._pumping.run_revolutions(num_revolutions=num_revolutions)

  async def run_continuously(self, speed: float):
    await self._pumping.run_continuously(speed=speed)

  async def run_for_duration(self, speed: Union[float, int], duration: Union[float, int]):
    await self._pumping.run_for_duration(speed=speed, duration=duration)

  async def pump_volume(self, speed: Union[float, int], volume: Union[float, int]):
    await self._pumping.pump_volume(speed=speed, volume=volume)

  async def halt(self):
    await self._pumping.halt()
