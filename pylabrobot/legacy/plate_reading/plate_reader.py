import logging
from typing import Dict, List, Optional, cast

from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.plate_reading.absorbance.backend import (
  AbsorbanceBackend as _NewAbsorbanceBackend,
)
from pylabrobot.capabilities.plate_reading.absorbance.standard import AbsorbanceResult
from pylabrobot.capabilities.plate_reading.fluorescence import Fluorescence
from pylabrobot.capabilities.plate_reading.fluorescence.backend import (
  FluorescenceBackend as _NewFluorescenceBackend,
)
from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.capabilities.plate_reading.luminescence import Luminescence
from pylabrobot.capabilities.plate_reading.luminescence.backend import (
  LuminescenceBackend as _NewLuminescenceBackend,
)
from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.legacy._backend_params import _DictBackendParams
from pylabrobot.legacy.machines.machine import Machine, need_setup_finished
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.legacy.plate_reading.standard import NoPlateError
from pylabrobot.resources import Coordinate, Plate, Resource, ResourceHolder, Rotation, Well
from pylabrobot.serializer import SerializableMixin

logger = logging.getLogger(__name__)


class _AbsorbanceAdapter(_NewAbsorbanceBackend):
  """Adapts PlateReaderBackend.read_absorbance to AbsorbanceBackend."""

  def __init__(self, legacy: PlateReaderBackend):
    self._legacy = legacy

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[AbsorbanceResult]:
    kwargs = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    dicts = await self._legacy.read_absorbance(
      plate=plate, wells=wells, wavelength=wavelength, **kwargs
    )
    return [
      AbsorbanceResult(
        data=d["data"],
        wavelength=d["wavelength"],
        temperature=d.get("temperature"),
        timestamp=d.get("time", 0),
      )
      for d in dicts
    ]


class _LuminescenceAdapter(_NewLuminescenceBackend):
  """Adapts PlateReaderBackend.read_luminescence to LuminescenceBackend."""

  def __init__(self, legacy: PlateReaderBackend):
    self._legacy = legacy

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[LuminescenceResult]:
    kwargs = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    dicts = await self._legacy.read_luminescence(
      plate=plate, wells=wells, focal_height=focal_height, **kwargs
    )
    return [
      LuminescenceResult(
        data=d["data"],
        temperature=d.get("temperature"),
        timestamp=d.get("time", 0),
      )
      for d in dicts
    ]


class _FluorescenceAdapter(_NewFluorescenceBackend):
  """Adapts PlateReaderBackend.read_fluorescence to FluorescenceBackend."""

  def __init__(self, legacy: PlateReaderBackend):
    self._legacy = legacy

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[FluorescenceResult]:
    kwargs = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    dicts = await self._legacy.read_fluorescence(
      plate=plate,
      wells=wells,
      excitation_wavelength=excitation_wavelength,
      emission_wavelength=emission_wavelength,
      focal_height=focal_height,
      **kwargs,
    )
    return [
      FluorescenceResult(
        data=d["data"],
        excitation_wavelength=d["ex_wavelength"],
        emission_wavelength=d["em_wavelength"],
        temperature=d.get("temperature"),
        timestamp=d.get("time", 0),
      )
      for d in dicts
    ]


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
    rotation: Optional["Rotation"] = None,
    category: Optional[str] = "plate_reader",
    model: Optional[str] = None,
    child_location: Coordinate = Coordinate.zero(),
    preferred_pickup_location: Optional[Coordinate] = None,
  ) -> None:
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
      child_location=child_location,
      preferred_pickup_location=preferred_pickup_location,
    )
    Machine.__init__(self, backend=backend)
    self.backend: PlateReaderBackend = backend  # fix type

    self._absorbance_cap = Absorbance(backend=_AbsorbanceAdapter(backend))
    self._luminescence_cap = Luminescence(backend=_LuminescenceAdapter(backend))
    self._fluorescence_cap = Fluorescence(backend=_FluorescenceAdapter(backend))

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    await self._absorbance_cap._on_setup()
    await self._luminescence_cap._on_setup()
    await self._fluorescence_cap._on_setup()

  async def stop(self):
    await self._fluorescence_cap._on_stop()
    await self._luminescence_cap._on_stop()
    await self._absorbance_cap._on_stop()
    await super().stop()

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    if len([c for c in self.children if isinstance(c, Plate)]) >= 1:
      raise ValueError("There already is a plate in the plate reader.")

    super().assign_child_resource(resource, location=location, reassign=reassign)

  def get_plate(self) -> Plate:
    plate_children = [c for c in self.children if isinstance(c, Plate)]
    if len(plate_children) == 0:
      raise NoPlateError("There is no plate in the plate reader.")
    return cast(Plate, plate_children[0])

  @need_setup_finished
  async def open(self, **backend_kwargs) -> None:
    await self.backend.open(**backend_kwargs)

  @need_setup_finished
  async def close(self, **backend_kwargs) -> None:
    plate = self.get_plate() if len(self.children) > 0 else None
    await self.backend.close(plate=plate, **backend_kwargs)

  @need_setup_finished
  async def read_luminescence(
    self,
    focal_height: float,
    wells: Optional[List[Well]] = None,
    use_new_return_type: bool = False,
    **backend_kwargs,
  ) -> List[Dict]:
    """Read the luminescence from the plate reader.

    Args:
      focal_height: The focal height to read the luminescence at, in millimeters.
      use_new_return_type: Whether to return the new return type, which is a list of dictionaries.

    Returns:
      A list of dictionaries, one for each measurement. Each dictionary contains:
        "time": float,
        "temperature": float,
        "data": List[List[float]]
    """

    plate = self.get_plate()
    results = await self._luminescence_cap.read(
      plate=plate,
      wells=wells or plate.get_all_items(),
      focal_height=focal_height,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
    )
    result = [
      {
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

    if not use_new_return_type:
      logger.warning(
        "The return type of read_luminescence will change in a future version. Please set "
        "use_new_return_type=True to use the new return type."
      )
      return result[0]["data"]  # type: ignore[no-any-return]
    return result

  @need_setup_finished
  async def read_absorbance(
    self,
    wavelength: int,
    wells: Optional[List[Well]] = None,
    use_new_return_type: bool = False,
    **backend_kwargs,
  ) -> List[Dict]:
    """Read the absorbance from the plate reader.

    Args:
      wavelength: The wavelength to read the absorbance at, in nanometers.
      use_new_return_type: Whether to return the new return type, which is a list of dictionaries.

    Returns:
      A list of dictionaries, one for each measurement. Each dictionary contains:
        "wavelength": int,
        "time": float,
        "temperature": float,
        "data": List[List[float]]
    """

    plate = self.get_plate()
    results = await self._absorbance_cap.read(
      plate=plate,
      wells=wells or plate.get_all_items(),
      wavelength=wavelength,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
    )
    result = [
      {
        "wavelength": r.wavelength,
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

    if not use_new_return_type:
      logger.warning(
        "The return type of read_absorbance will change in a future version. Please set "
        "use_new_return_type=True to use the new return type."
      )
      return result[0]["data"]  # type: ignore[no-any-return]
    return result

  @need_setup_finished
  async def read_fluorescence(
    self,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    wells: Optional[List[Well]] = None,
    use_new_return_type: bool = False,
    **backend_kwargs,
  ) -> List[Dict]:
    """Read the fluorescence from the plate reader.

    Args:
      excitation_wavelength: The excitation wavelength to read the fluorescence at, in nanometers.
      emission_wavelength: The emission wavelength to read the fluorescence at, in nanometers.
      focal_height: The focal height to read the fluorescence at, in millimeters.
      use_new_return_type: Whether to return the new return type, which is a list of dictionaries.

    Returns:
      A list of dictionaries, one for each measurement. Each dictionary contains:
        "ex_wavelength": int,
        "em_wavelength": int,
        "time": float,
        "temperature": float,
        "data": List[List[float]]
    """

    if excitation_wavelength > emission_wavelength:
      logger.warning(
        "Excitation wavelength is greater than emission wavelength. This is unusual and may indicate an error."
      )

    plate = self.get_plate()
    results = await self._fluorescence_cap.read(
      plate=plate,
      wells=wells or plate.get_all_items(),
      excitation_wavelength=excitation_wavelength,
      emission_wavelength=emission_wavelength,
      focal_height=focal_height,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
    )
    result = [
      {
        "ex_wavelength": r.excitation_wavelength,
        "em_wavelength": r.emission_wavelength,
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

    if not use_new_return_type:
      logger.warning(
        "The return type of read_fluorescence will change in a future version. Please set "
        "use_new_return_type=True to use the new return type."
      )
      return result[0]["data"]  # type: ignore[no-any-return]
    return result

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Machine.serialize(self)}
