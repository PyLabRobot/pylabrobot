from abc import ABC, abstractmethod
import sys
from typing import List

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


class PlateReaderBackend(ABC):
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
  async def read_absorbance(
    self,
    wavelength: int,
    report: Literal["OD", "transmittance"]
  ) -> List[List[float]]:
    """ Read the absorbance from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate. """
