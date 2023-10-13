from pylabrobot.machine import MachineFrontend

from .backend import PumpBackend


class Pump(MachineFrontend):
  """ Frontend for a (peristaltic) pump. """

  def __init__(self, backend: PumpBackend):
    self.backend: PumpBackend = backend

  def run_revolutions(self, num_revolutions: float):
    self.backend.run_revolutions(num_revolutions=num_revolutions)

  def run_continuously(self, speed: float):
    """ Run continuously at a given speed.

    If speed is 0, the pump will be halted.

    Args:
      speed: speed in rpm
    """

    self.backend.run_continuously(speed=speed)

  def halt(self):
    self.backend.halt()
