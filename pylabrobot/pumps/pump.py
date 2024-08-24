import asyncio
from typing import Optional, Union

from pylabrobot.machines.machine import Machine
from .backend import PumpBackend
from .calibration import PumpCalibration

class Pump(Machine):
  """ Frontend for a (peristaltic) pump. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: PumpBackend,
    category: Optional[str] = None,
    model: Optional[str] = None,
    calibration: Optional[PumpCalibration] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      backend=backend,
      category=category,
      model=model,
    )
    self.backend: PumpBackend = backend # fix type
    if calibration is not None and len(calibration) != 1:
      raise ValueError("Calibration may only have a single item for this pump")
    self.calibration = calibration

  def serialize(self) -> dict:
    if self.calibration is None:
      return super().serialize()
    return {**super().serialize(), "calibration": self.calibration.serialize()}

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False):
    data_copy = data.copy()
    calibration_data = data_copy.pop("calibration", None)
    if calibration_data is not None:
      calibration = PumpCalibration.deserialize(calibration_data)
      data_copy["calibration"] = calibration
    return super().deserialize(data_copy, allow_marshal=allow_marshal)

  async def run_revolutions(self, num_revolutions: float):
    """ Run a given number of revolutions. This method will return after the command has been sent,
    and the pump will run until `halt` is called.

    Args:
      num_revolutions: number of revolutions to run
    """

    self.backend.run_revolutions(num_revolutions=num_revolutions)

  async def run_continuously(self, speed: float):
    """ Run continuously at a given speed. This method will return after the command has been sent,
    and the pump will run until `halt` is called.

    If speed is 0, the pump will be halted.

    Args:
      speed: speed in rpm/pump-specific units.
    """

    self.backend.run_continuously(speed=speed)

  async def run_for_duration(self, speed: Union[float, int], duration: Union[float, int]):
    """ Run the pump at specified speed for the specified duration.

    Args:
      speed: speed in rpm/pump-specific units.
      duration: duration to run pump.
    """

    if duration < 0:
      raise ValueError("Duration must be positive.")
    await self.run_continuously(speed=speed)
    await asyncio.sleep(duration)
    await self.run_continuously(speed=0)

  async def pump_volume(self, speed: Union[float, int], volume: Union[float, int]):
    """ Run the pump at specified speed for the specified volume. Note that this function requires
    the pump to be calibrated at the input speed.

    Args:
      speed: speed in rpm/pump-specific units.
      volume: volume to pump.
    """

    if self.calibration is None:
      raise TypeError(
        "Pump is not calibrated. Volume based pumping and related functions unavailable.")
    if self.calibration.calibration_mode == "duration":
      duration = volume / self.calibration[0]
      await self.run_for_duration(speed=speed, duration=duration)
    elif self.calibration.calibration_mode == "revolutions":
      num_revolutions = volume / self.calibration[0]
      await self.run_revolutions(num_revolutions=num_revolutions)
    else:
      raise ValueError("Calibration mode not recognized.")

  async def halt(self):
    """ Halt the pump."""
    self.backend.halt()
