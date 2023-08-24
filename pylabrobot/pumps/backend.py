from abc import ABCMeta

from pylabrobot.machine import MachineBackend


class PumpBackend(MachineBackend, metaclass=ABCMeta):
  """ Abstract base class for pump backends. """

  def run_revolutions(self, num_revolutions: float):
    """ Run for a given number of revolutions.

    Args:
      num_revolutions: number of revolutions to run.
    """

  def run_continuously(self, speed: float):
    """ Run continuously at a given speed.

    If speed is 0, the pump will be halted.

    Args:
      speed: speed in rpm.
    """

  def halt(self):
    """ Halt the pump. """
