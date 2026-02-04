from typing import Optional, Tuple

from pylabrobot.plate_reading.byonoy.byonoy_backend import ByonoyLuminescence96AutomateBackend
from pylabrobot.plate_reading.plate_reader import PlateReader
from pylabrobot.resources import Coordinate, PlateHolder, Resource, ResourceHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.rotation import Rotation


class _ByonoyLuminescenceReaderPlateHolder(PlateHolder):
  """Custom plate holder that checks if the reader sits on the parent base.
  This check is used to prevent crashes (moving plate onto holder while reader is on the base)."""

  def __init__(
    self,
    name: str,
    child_location: Coordinate = Coordinate.zero(),
    category: str = "plate_holder",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=127.76,
      size_y=85.59,
      size_z=0,
      pedestal_size_z=0,
      child_location=child_location,
      category=category,
      model=model,
    )
    self._byonoy_base: Optional["ByonoyLuminescenceBaseUnit"] = None

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    if self._byonoy_base is None:
      raise RuntimeError(
        "Plate holder not assigned to a ByonoyLuminescenceBaseUnit. This should not happen."
      )

    if self._byonoy_base.reader_unit_holder.resource is not None:
      raise RuntimeError(
        f"Cannot drop resource {resource.name} onto plate holder while reader unit is on the base. "
        "Please remove the reader unit from the base before dropping a resource."
      )

    super().check_can_drop_resource_here(resource, reassign=reassign)


class ByonoyLuminescenceBaseUnit(Resource):
  """Base unit for the Byonoy L96/L96A luminescence reader.

  The base unit is a simple resource that holds a plate. The reader unit sits on top of it.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    plate_holder_child_location: Coordinate,
    reader_unit_holder_child_location: Coordinate,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
    barcode: Optional[Barcode] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
      barcode=barcode,
    )

    self.plate_holder = _ByonoyLuminescenceReaderPlateHolder(
      name=self.name + "_plate_holder",
      child_location=plate_holder_child_location,
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

    self.reader_unit_holder = ResourceHolder(
      name=self.name + "_reader_unit_holder",
      size_x=size_x,
      size_y=size_y,
      size_z=0,
      child_location=reader_unit_holder_child_location,
    )
    self.assign_child_resource(self.reader_unit_holder, location=Coordinate.zero())

  def assign_child_resource(
    self, resource: Resource, location: Optional[Coordinate], reassign: bool = True
  ) -> None:
    if isinstance(resource, _ByonoyLuminescenceReaderPlateHolder):
      if self.plate_holder._byonoy_base is not None:
        raise ValueError("ByonoyBase can only have one plate holder assigned.")
      self.plate_holder._byonoy_base = self
    super().assign_child_resource(resource, location, reassign)

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    raise RuntimeError(
      "ByonoyBase does not support assigning child resources directly. "
      "Use the plate_holder or reader_unit_holder to assign plates and the reader unit, respectively."
    )


class ByonoyLuminescence96Automate(PlateReader):
  """Byonoy L96/L96A luminescence plate reader unit.

  This is the reader unit that sits on top of the base unit.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    preferred_pickup_location: Optional[Coordinate] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      backend=ByonoyLuminescence96AutomateBackend(),
      model="Byonoy L96 Reader Unit",
      preferred_pickup_location=preferred_pickup_location,
    )


def byonoy_l96_reader_unit(name: str) -> ByonoyLuminescence96Automate:
  """Create a Byonoy L96 reader unit `PlateReader`.

  Note: L96 (non-automate) does not have a preferred pickup location.
  """
  return ByonoyLuminescence96Automate(
    name=name,
    size_x=139.7,  # caliper
    size_y=97.5,  # caliper
    size_z=35,  # force z probing
    preferred_pickup_location=None,
  )


def byonoy_l96_base_unit(name: str) -> ByonoyLuminescenceBaseUnit:
  """Create a Byonoy L96 base unit."""
  return ByonoyLuminescenceBaseUnit(
    name=name,
    size_x=139.7,  # caliper
    size_y=97.5,  # caliper
    size_z=9.4,  # force z probing
    plate_holder_child_location=Coordinate(x=6.25, y=6.1, z=2.64),  # caliper
    reader_unit_holder_child_location=Coordinate(x=0, y=0, z=7.2),  # z = 42.2 - 35
  )


def byonoy_l96(
  name: str, assign: bool = True
) -> Tuple[ByonoyLuminescenceBaseUnit, ByonoyLuminescence96Automate]:
  """Creates a ByonoyLuminescenceBaseUnit and a PlateReader instance for L96 (non-automate).

  Args:
    name: Base name for the resources.
    assign: If True, the reader unit is assigned to the base unit's reader_unit_holder.

  Returns:
    A tuple of (base_unit, reader_unit).
  """
  base_unit = byonoy_l96_base_unit(name=name + "_base")
  reader_unit = byonoy_l96_reader_unit(name=name + "_reader")
  if assign:
    base_unit.reader_unit_holder.assign_child_resource(reader_unit)
  return base_unit, reader_unit
