import logging
from typing import Any, Dict, List, Optional, Sequence, cast

from pylabrobot.events import event_operation, is_event_bus_active, resource_reference
from pylabrobot.legacy.machines.machine import Machine, need_setup_finished
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.legacy.plate_reading.standard import NoPlateError
from pylabrobot.resources import Coordinate, Plate, Resource, ResourceHolder, Rotation, Well

logger = logging.getLogger(__name__)


def _plate_reader_event_data(
  reader: "PlateReader",
  resources: Sequence[Resource],
  **data: Any,
) -> dict[str, Any]:
  """Build JSON-ready PlateReader operation data for an interested EventBus."""

  return {
    "device": resource_reference(reader),
    "resources": [resource_reference(resource) for resource in resources],
    **data,
  }


class PlateReader(ResourceHolder, Machine):
  """The front end for plate readers. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate.

  Plate readers are asynchronous, meaning that their methods will return immediately and
  will not block.

  Here's an example of how to use this class in a Jupyter Notebook:

  >>> from pylabrobot.legacy.plate_reading.clario_star import CLARIOStarBackend
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
    plate = next((child for child in self.children if isinstance(child, Plate)), None)
    operation_data = (
      _plate_reader_event_data(
        self,
        [] if plate is None else [plate],
      )
      if is_event_bus_active()
      else {}
    )
    with event_operation("plate_reader.open", **operation_data):
      await self.backend.open(**backend_kwargs)

  @need_setup_finished
  async def close(self, **backend_kwargs) -> None:
    plate = self.get_plate() if len(self.children) > 0 else None
    operation_data = (
      _plate_reader_event_data(self, [] if plate is None else [plate])
      if is_event_bus_active()
      else {}
    )
    with event_operation("plate_reader.close", **operation_data):
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
    selected_wells = wells or plate.get_all_items()
    operation_data = (
      _plate_reader_event_data(
        self,
        selected_wells,
        well_count=len(selected_wells),
        return_format="records" if use_new_return_type else "legacy_matrix",
        focal_height=focal_height,
      )
      if is_event_bus_active()
      else {}
    )
    completion_data: dict[str, Any] = {}
    with event_operation(
      "plate_reader.read_luminescence",
      **operation_data,
      completed_data_factory=lambda: {**operation_data, **completion_data},
    ):
      result = await self.backend.read_luminescence(
        plate=plate,
        wells=selected_wells,
        focal_height=focal_height,
        **backend_kwargs,
      )
      if operation_data:
        completion_data["record_count"] = len(result)

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
    selected_wells = wells or plate.get_all_items()
    operation_data = (
      _plate_reader_event_data(
        self,
        selected_wells,
        well_count=len(selected_wells),
        return_format="records" if use_new_return_type else "legacy_matrix",
        wavelength_nm=wavelength,
      )
      if is_event_bus_active()
      else {}
    )
    completion_data: dict[str, Any] = {}
    with event_operation(
      "plate_reader.read_absorbance",
      **operation_data,
      completed_data_factory=lambda: {**operation_data, **completion_data},
    ):
      result = await self.backend.read_absorbance(
        plate=plate,
        wells=selected_wells,
        wavelength=wavelength,
        **backend_kwargs,
      )
      if operation_data:
        completion_data["record_count"] = len(result)

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
    selected_wells = wells or plate.get_all_items()
    operation_data = (
      _plate_reader_event_data(
        self,
        selected_wells,
        well_count=len(selected_wells),
        return_format="records" if use_new_return_type else "legacy_matrix",
        excitation_wavelength_nm=excitation_wavelength,
        emission_wavelength_nm=emission_wavelength,
        focal_height=focal_height,
      )
      if is_event_bus_active()
      else {}
    )
    completion_data: dict[str, Any] = {}
    with event_operation(
      "plate_reader.read_fluorescence",
      **operation_data,
      completed_data_factory=lambda: {**operation_data, **completion_data},
    ):
      result = await self.backend.read_fluorescence(
        plate=plate,
        wells=selected_wells,
        excitation_wavelength=excitation_wavelength,
        emission_wavelength=emission_wavelength,
        focal_height=focal_height,
        **backend_kwargs,
      )
      if operation_data:
        completion_data["record_count"] = len(result)
      if not use_new_return_type:
        logger.warning(
          "The return type of read_fluorescence will change in a future version. Please set "
          "use_new_return_type=True to use the new return type."
        )
        return result[0]["data"]  # type: ignore[no-any-return]
      return result

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Machine.serialize(self)}
