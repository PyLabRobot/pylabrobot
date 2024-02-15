from typing import Optional

from pylabrobot.machine import Machine
from .backend import PumpBackend


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

  def run_revolutions(self, num_revolutions: float):
    """ Run a given number of revolutions. This method will return after the command has been sent,
    and the pump will run until `halt` is called.

    Args:
      num_revolutions: number of revolutions to run
    """

    self.backend.run_revolutions(num_revolutions=num_revolutions)

  def run_continuously(self, speed: float):
    """ Run continuously at a given speed. This method will return after the command has been sent,
    and the pump will run until `halt` is called.

    If speed is 0, the pump will be halted.

    Args:
      speed: speed in rpm
    """

    self.backend.run_continuously(speed=speed)

  def halt(self):
    """ Halt the pump immediately. """

    self.backend.halt()
