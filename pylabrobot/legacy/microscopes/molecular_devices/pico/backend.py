"""Legacy. Use pylabrobot.molecular_devices.PicoBackend instead."""

from typing import Dict, List, Optional

from pylabrobot.capabilities.microscopy import (
  ImagingMode as NewImagingMode,
  ImagingResult as NewImagingResult,
  Objective as NewObjective,
)
from pylabrobot.legacy.plate_reading.backend import ImagerBackend
from pylabrobot.legacy.plate_reading.standard import (
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.molecular_devices.imageXpress.pico.backend import PicoBackend
from pylabrobot.resources.plate import Plate


def _legacy_to_new_imaging_mode(mode: ImagingMode) -> NewImagingMode:
  return NewImagingMode[mode.name]


def _legacy_to_new_objective(obj: Objective) -> NewObjective:
  return NewObjective[obj.name]


def _new_to_legacy_imaging_result(result: NewImagingResult) -> ImagingResult:
  return ImagingResult(
    images=result.images,
    exposure_time=result.exposure_time,
    focal_height=result.focal_height,
  )


class ExperimentalPicoBackend(ImagerBackend):
  """Legacy. Use pylabrobot.molecular_devices.PicoBackend instead."""

  def __init__(
    self,
    host: str,
    port: int = 8091,
    lock_timeout: int = 3600,
    objectives: Optional[Dict[int, Objective]] = None,
    filter_cubes: Optional[Dict[int, ImagingMode]] = None,
  ):
    super().__init__()
    new_objectives = {
      pos: _legacy_to_new_objective(obj) for pos, obj in (objectives or {}).items()
    }
    new_filter_cubes = {
      pos: _legacy_to_new_imaging_mode(mode) for pos, mode in (filter_cubes or {}).items()
    }
    self._new = PicoBackend(
      host=host,
      port=port,
      lock_timeout=lock_timeout,
      objectives=new_objectives,
      filter_cubes=new_filter_cubes,
    )

  @property
  def door_open(self) -> bool:
    return self._new.door_open

  async def setup(self) -> None:
    await self._new.setup()

  async def stop(self) -> None:
    await self._new.stop()

  async def get_configuration(self) -> dict:
    return await self._new.get_configuration()

  async def open_door(self) -> None:
    await self._new.open_door()

  async def close_door(self) -> None:
    await self._new.close_door()

  async def enter_objective_maintenance(self, position: int) -> None:
    await self._new.enter_objective_maintenance(position)

  async def exit_objective_maintenance(self) -> None:
    await self._new.exit_objective_maintenance()

  async def get_available_objectives(self, position: int) -> List[dict]:
    return await self._new.get_available_objectives(position)

  async def get_available_filter_cubes(self) -> List[dict]:
    return await self._new.get_available_filter_cubes()

  async def change_objective(self, position: int, objective_id: str) -> None:
    await self._new.change_objective(position, objective_id)

  async def change_filter_cube(self, position: int, filter_cube_id: str) -> None:
    await self._new.change_filter_cube(position, filter_cube_id)

  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Exposure,
    focal_height: FocalPosition,
    gain: Gain,
    plate: Plate,
  ) -> ImagingResult:
    result = await self._new.capture(
      row=row,
      column=column,
      mode=_legacy_to_new_imaging_mode(mode),
      objective=_legacy_to_new_objective(objective),
      exposure_time=exposure_time,
      focal_height=focal_height,
      gain=gain,
      plate=plate,
    )
    return _new_to_legacy_imaging_result(result)
