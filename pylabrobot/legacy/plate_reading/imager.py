"""Legacy. Use pylabrobot.capabilities.microscopy.MicroscopyCapability instead."""

from typing import Optional, Tuple, Union, cast

from pylabrobot.capabilities.microscopy import AutoExposure as NewAutoExposure
from pylabrobot.capabilities.microscopy import ImagingMode as NewImagingMode
from pylabrobot.capabilities.microscopy import ImagingResult as NewImagingResult
from pylabrobot.capabilities.microscopy import (
  MicroscopyBackend,
  MicroscopyCapability,
  evaluate_focus_nvmg_sobel,
  fraction_overexposed,
  max_pixel_at_fraction,
)
from pylabrobot.capabilities.microscopy import Objective as NewObjective
from pylabrobot.legacy.machines import Machine, need_setup_finished
from pylabrobot.legacy.plate_reading.backend import ImagerBackend
from pylabrobot.legacy.plate_reading.standard import (
  AutoExposure,
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  NoPlateError,
  Objective,
)
from pylabrobot.resources import Plate, Resource, Rotation, Well

# Re-export helpers so existing imports still work.
__all__ = [
  "Imager",
  "max_pixel_at_fraction",
  "fraction_overexposed",
  "evaluate_focus_nvmg_sobel",
]


class _ImagerBackendAdapter(MicroscopyBackend):
  """Adapts a legacy ImagerBackend to the new MicroscopyBackend protocol."""

  def __init__(self, legacy: ImagerBackend):
    self._legacy = legacy

  async def setup(self) -> None:
    await self._legacy.setup()

  async def stop(self) -> None:
    await self._legacy.stop()

  async def capture(self, row, column, mode, objective, exposure_time, focal_height, gain, plate):
    legacy_mode = ImagingMode[mode.name]
    legacy_obj = Objective[objective.name]
    result = await self._legacy.capture(
      row=row,
      column=column,
      mode=legacy_mode,
      objective=legacy_obj,
      exposure_time=exposure_time,
      focal_height=focal_height,
      gain=gain,
      plate=plate,
    )
    return NewImagingResult(
      images=result.images,
      exposure_time=result.exposure_time,
      focal_height=result.focal_height,
    )


def _to_new_imaging_mode(mode: ImagingMode) -> NewImagingMode:
  return NewImagingMode[mode.name]


def _to_new_objective(obj: Objective) -> NewObjective:
  return NewObjective[obj.name]


def _to_legacy_result(result: NewImagingResult) -> ImagingResult:
  return ImagingResult(
    images=result.images,
    exposure_time=result.exposure_time,
    focal_height=result.focal_height,
  )


class Imager(Resource, Machine):
  """Legacy. Use pylabrobot.molecular_devices.Pico (or similar Device) instead."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ImagerBackend,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
    )
    Machine.__init__(self, backend=backend)
    self._backend: ImagerBackend = backend
    self._microscopy = MicroscopyCapability(backend=_ImagerBackendAdapter(backend))
    self._microscopy._setup_finished = True  # legacy Machine.setup() handles lifecycle

    self.register_will_assign_resource_callback(self._will_assign_resource)

  def _will_assign_resource(self, resource: Resource):
    if len(self.children) >= 1:
      raise ValueError(
        f"Imager {self} already has a plate assigned (attempting to assign {resource})"
      )

  def get_plate(self) -> Plate:
    if len(self.children) == 0:
      raise NoPlateError("There is no plate in the plate reader.")
    return cast(Plate, self.children[0])

  @need_setup_finished
  async def capture(
    self,
    well: Union[Well, Tuple[int, int]],
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Union[Exposure, AutoExposure] = "machine-auto",
    focal_height: FocalPosition = "machine-auto",
    gain: Gain = "machine-auto",
    **backend_kwargs,
  ) -> ImagingResult:
    new_exposure: Union[float, str, NewAutoExposure]
    if isinstance(exposure_time, AutoExposure):
      new_exposure = NewAutoExposure(
        evaluate_exposure=exposure_time.evaluate_exposure,
        max_rounds=exposure_time.max_rounds,
        low=exposure_time.low,
        high=exposure_time.high,
      )
    else:
      new_exposure = exposure_time
    new_result = await self._microscopy.capture(
      well=well,
      mode=_to_new_imaging_mode(mode),
      objective=_to_new_objective(objective),
      plate=self.get_plate(),
      exposure_time=new_exposure,
      focal_height=focal_height,
      gain=gain,
      **backend_kwargs,
    )
    return _to_legacy_result(new_result)
