from typing import List, Optional, Tuple, Union, cast

from pylabrobot.machines import Machine
from pylabrobot.plate_reading.backend import ImagerBackend
from pylabrobot.plate_reading.standard import Exposure, FocalPosition, Gain, ImagingMode
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
    self.plate: Optional[Plate] = None

    self.register_did_unassign_resource_callback(self._did_unassign_resource)
    self.register_did_assign_resource_callback(self._did_assign_resource)
    self.register_will_assign_resource_callback(self._will_assign_resource)

  def _will_assign_resource(self, resource: Resource):
    if self.plate is not None:
      raise ValueError(
        f"Imager {self} already has a plate assigned " f"(attemping to assign {resource})"
      )

  def _did_assign_resource(self, resource: Resource):
    if isinstance(resource, Plate):
      self.plate = resource

  def _did_unassign_resource(self, resource: Resource):
    if resource == self.plate:
      self.plate = None

  async def capture(
    self,
    well: Union[Well, Tuple[int, int]],
    mode: ImagingMode,
    exposure_time: Exposure = "auto",
    focal_height: FocalPosition = "auto",
    gain: Gain = "auto",
    **backend_kwargs,
  ) -> List[List[float]]:
    if isinstance(well, tuple):
      row, column = well
    else:
      if self.plate is None:
        raise ValueError(f"Imager {self} has no plate assigned")
      idx = cast(Plate, well.parent).index_of_item(well)
      if idx is None:
        raise ValueError(f"Well {well} not in plate {self.plate}")
      row, column = divmod(idx, cast(Plate, well.parent).num_items_x)

    return await self.backend.capture(
      row=row,
      column=column,
      mode=mode,
      exposure_time=exposure_time,
      focal_height=focal_height,
      gain=gain,
      **backend_kwargs,
    )
