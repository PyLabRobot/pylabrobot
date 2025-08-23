from typing import List, Literal, Optional, cast

from pylabrobot.machines.machine import Machine, need_setup_finished
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.plate_reading.standard import NoPlateError
from pylabrobot.resources import Coordinate, Plate, Resource
from pylabrobot.resources.resource_holder import ResourceHolder


class PlateReader(ResourceHolder, Machine):
  """The front end for plate readers. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate.

  Plate readers are asynchronous, meaning that their methods will return immediately and
  will not block.

  Here's an example of how to use this class in a Jupyter Notebook:

  >>> from pylabrobot.plate_reading.clario_star import CLARIOStarBackend
  >>> pr = PlateReader(backend=CLARIOStarBackend())
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
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    Machine.__init__(self, backend=backend)
    self.backend: PlateReaderBackend = backend  # fix type

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    if len(self.children) >= 1:
      raise ValueError("There already is a plate in the plate reader.")
    if not isinstance(resource, Plate):
      raise ValueError("The resource must be a Plate.")

    super().assign_child_resource(resource, location=location, reassign=reassign)

  def get_plate(self) -> Plate:
    if len(self.children) == 0:
      raise NoPlateError("There is no plate in the plate reader.")
    return cast(Plate, self.children[0])

  async def open(self, **backend_kwargs) -> None:
    await self.backend.open(**backend_kwargs)

  async def close(self, **backend_kwargs) -> None:
    plate = self.get_plate() if len(self.children) > 0 else None
    await self.backend.close(plate=plate, **backend_kwargs)

  @need_setup_finished
  async def read_luminescence(self, focal_height: float) -> List[List[float]]:
    """Read the luminescence from the plate.

    Args:
      focal_height: The focal height to read the luminescence at, in micrometers.
    """

    return await self.backend.read_luminescence(plate=self.get_plate(), focal_height=focal_height)

  @need_setup_finished
  async def read_absorbance(self, wavelength: int) -> List[List[float]]:
    """Read the absorbance from the plate in OD, unless otherwise specified by the backend.

    Args:
      wavelength: The wavelength to read the absorbance at, in nanometers.
    """

    return await self.backend.read_absorbance(plate=self.get_plate(), wavelength=wavelength)

  @need_setup_finished
  async def read_fluorescence(
    self,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[List[float]]:
    """

    Args:
      excitation_wavelength: The excitation wavelength to read the fluorescence at, in nanometers.
      emission_wavelength: The emission wavelength to read the fluorescence at, in nanometers.
      focal_height: The focal height to read the fluorescence at, in micrometers.
    """

    return await self.backend.read_fluorescence(
      plate=self.get_plate(),
      excitation_wavelength=excitation_wavelength,
      emission_wavelength=emission_wavelength,
      focal_height=focal_height,
    )

  async def read_dye(
    self, dye_name: Literal["rhodamine_b", "cresyl_violet"], focal_height: float
  ) -> List[List[float]]:
    """Read the dye from the plate.

    Args:
      dye_name: The name of the dye to read.
      focal_height: The focal height to read the dye at, in micrometers.
    """

    if dye_name == "rhodamine_b":
      return await self.read_fluorescence(
        excitation_wavelength=546,
        emission_wavelength=567,
        focal_height=focal_height,
      )

    if dye_name == "cresyl_violet":
      return await self.read_fluorescence(
        excitation_wavelength=598,
        emission_wavelength=621,
        focal_height=focal_height,
      )

    raise ValueError(
      f"Unknown dye: {dye_name}. Supported dyes are 'rhodamine_b' and 'cresyl_violet'."
    )
