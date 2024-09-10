from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.machines.backends import MachineBackend


class PlateReaderBackend(MachineBackend, metaclass=ABCMeta):
  """ An abstract class for a plate reader. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate. """

  @abstractmethod
  async def setup(self) -> None:
    """ Set up the plate reader. This should be called before any other methods. """

  @abstractmethod
  async def stop(self) -> None:
    """ Close all connections to the plate reader and make sure setup() can be called again. """

  @abstractmethod
  async def open(self) -> None:
    """ Open the plate reader. Also known as plate out. """

  @abstractmethod
  async def close(self) -> None:
    """ Close the plate reader. Also known as plate in. """

  @abstractmethod
  async def read_luminescence(self, focal_height: float) -> List[List[float]]:
    """ Read the luminescence from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate. """

  @abstractmethod
  async def read_absorbance(self, wavelength: int) -> List[List[float]]:
    """ Read the absorbance from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate. """

  @abstractmethod
  async def read_fluorescence(
    self,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float
  ) -> List[List[float]]:
    """ Read the fluorescence from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate. """
