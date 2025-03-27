from typing import List, Optional, Tuple, Union, cast

from pylabrobot.machines import Machine
from pylabrobot.plate_reading.backend import ImagerBackend
from pylabrobot.plate_reading.standard import (
  Exposure,
  FocalPosition,
  Gain,
  Image,
  ImagingMode,
  NoPlateError,
  Objective,
)
from pylabrobot.resources import Plate, Resource, Well


class Imager(Resource, Machine):
  """Microscope"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ImagerBackend,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    Machine.__init__(self, backend=backend)
    self.backend: ImagerBackend = backend  # fix type

    self.register_will_assign_resource_callback(self._will_assign_resource)

  def _will_assign_resource(self, resource: Resource):
    if len(self.children) >= 1:
      raise ValueError(
        f"Imager {self} already has a plate assigned " f"(attempting to assign {resource})"
      )

  def get_plate(self) -> Plate:
    if len(self.children) == 0:
      raise NoPlateError("There is no plate in the plate reader.")
    return cast(Plate, self.children[0])

  async def capture(
    self,
    well: Union[Well, Tuple[int, int]],
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Exposure = "auto",
    focal_height: FocalPosition = "auto",
    gain: Gain = "auto",
    **backend_kwargs,
  ) -> List[Image]:
    if isinstance(well, tuple):
      row, column = well
    else:
      idx = cast(Plate, well.parent).index_of_item(well)
      if idx is None:
        raise ValueError(f"Well {well} not in plate {well.parent}")
      row, column = divmod(idx, cast(Plate, well.parent).num_items_x)

    return await self.backend.capture(
      row=row,
      column=column,
      mode=mode,
      objective=objective,
      exposure_time=exposure_time,
      focal_height=focal_height,
      gain=gain,
      plate=self.get_plate(),
      **backend_kwargs,
    )
