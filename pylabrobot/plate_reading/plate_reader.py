import sys
from typing import List, Optional, cast

from pylabrobot.machine import Machine, need_setup_finished
from pylabrobot.resources import Coordinate, Plate
from pylabrobot.plate_reading.backend import PlateReaderBackend

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


class NoPlateError(Exception):
  pass


class PlateReader(Machine):
  """ The front end for plate readers. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate.

  Plate readers are asynchronous, meaning that their methods will return immediately and
  will not block.

  Here's an example of how to use this class in a Jupyter Notebook:

  >>> from pylabrobot.plate_reading.clario_star import CLARIOStar
  >>> pr = PlateReader(backend=CLARIOStar())
  >>> pr.setup()
  >>> await pr.read_luminescence()
  [[value1, value2, value3, ...], [value1, value2, value3, ...], ...
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: PlateReaderBackend,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ) -> None:
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, backend=backend,
                      category=category, model=model)
    self.backend: PlateReaderBackend = backend # fix type

  def assign_child_resource(self, resource):
    if len(self.children) >= 1:
      raise ValueError("There already is a plate in the plate reader.")
    if not isinstance(resource, Plate):
      raise ValueError("The resource must be a Plate.")
    super().assign_child_resource(resource, location=Coordinate.zero())

  def get_plate(self) -> Plate:
    if len(self.children) == 0:
      raise NoPlateError("There is no plate in the plate reader.")
    return cast(Plate, self.children[0])

  async def open(self) -> None:
    await self.backend.open()

  async def close(self) -> None:
    await self.backend.close()

  @need_setup_finished
  async def read_luminescence(self, focal_height: float) -> List[List[float]]:
    """ Read the luminescence from the plate.

    Args:
      focal_height: The focal height to read the luminescence at, in micrometers.
    """

    return await self.backend.read_luminescence(focal_height=focal_height)

  @need_setup_finished
  async def read_absorbance(
    self,
    wavelength: int,
    report: Literal["OD", "transmittance"]
  ) -> List[List[float]]:
    """ Read the absorbance from the plate in either OD or transmittance.

    Args:
      wavelength: The wavelength to read the absorbance at, in nanometers.
      report: Whether to report the absorbance in OD or transmittance.
    """

    if report not in {"OD", "transmittance"}:
      raise ValueError("report must be either 'OD' or 'transmittance'.")

    return await self.backend.read_absorbance(wavelength=wavelength, report=report)
