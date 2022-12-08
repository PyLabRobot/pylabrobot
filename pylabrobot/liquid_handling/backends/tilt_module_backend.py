from abc import ABCMeta, abstractmethod


class TiltModuleError(Exception):
  """ Error raised by a tilt module backend. """


class TiltModuleBackend(metaclass=ABCMeta):
  """ Abstract backend for tilt modules. """

  @abstractmethod
  def __init__(self):
    self.setup_finished = False

  @abstractmethod
  def setup(self):
    self.setup_finished = True

  @abstractmethod
  def stop(self):
    self.setup_finished = False

  @abstractmethod
  def set_angle(self, angle: int):
    """ Set the tilt module to rotate by a given angle. """
