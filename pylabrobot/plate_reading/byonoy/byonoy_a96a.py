from typing import Optional, Tuple

from pylabrobot.plate_reading.byonoy.byonoy_backend import ByonoyAbsorbance96AutomateBackend
from pylabrobot.plate_reading.plate_reader import PlateReader
from pylabrobot.resources import Coordinate, PlateHolder, Resource, ResourceHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.rotation import Rotation


def byonoy_sbs_adapter(name: str) -> ResourceHolder:
  """Create a Byonoy SBS adapter `ResourceHolder`.

  This helper returns a `ResourceHolder` describing the physical footprint of the
  Byonoy SBS adapter and the default coordinate transform from the adapter frame
  to its child frame.

  The adapter is modeled as a cuboid with fixed outer dimensions.
  `child_location` encodes the child-frame origin offset assuming the SBS-adapter
  is symmetrically centered ("cc") relative to the detection_unit "cc" alignment reference.
  """
  return ResourceHolder(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=17.0,
    child_location=Coordinate(
      x=-(155.26 - 127.76) / 2,
      y=-(95.48 - 85.48) / 2,
      z=17.0,
    ),
  )


class _ByonoyAbsorbanceReaderPlateHolder(PlateHolder):
  """Custom plate holder that checks if the reader sits on the parent base.
  This check is used to prevent crashes (moving plate onto holder while reader is on the base)."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    pedestal_size_z: float = 0,
    child_location: Coordinate = Coordinate.zero(),
    category: str = "plate_holder",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      pedestal_size_z=pedestal_size_z,
      child_location=child_location,
      category=category,
      model=model,
    )
    self._byonoy_base: Optional["ByonoyAbsorbanceBaseUnit"] = None

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    if self._byonoy_base is None:
      raise RuntimeError(
        "Plate holder not assigned to a ByonoyAbsorbanceBaseUnit. This should not happen."
      )

    if self._byonoy_base.illumination_unit_holder.resource is not None:
      raise RuntimeError(
        f"Cannot drop resource {resource.name} onto plate holder while illumination unit is on the base. "
        "Please remove the illumination unit from the base before dropping a resource."
      )

    super().check_can_drop_resource_here(resource, reassign=reassign)


class ByonoyAbsorbanceBaseUnit(Resource):
  def __init__(
    self,
    name: str,
    size_x: float = 155.26,
    size_y: float = 95.48,
    size_z: float = 18.5,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
    barcode: Optional[Barcode] = None,
    preferred_pickup_location: Optional[Coordinate] = None,
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
      preferred_pickup_location=preferred_pickup_location,
    )

    self.plate_holder = _ByonoyAbsorbanceReaderPlateHolder(
      name=self.name + "_plate_holder",
      size_x=127.76,  # standard SBS footprint
      size_y=85.59,
      size_z=0,
      child_location=Coordinate(x=22.5, y=5.0, z=16.0),
      pedestal_size_z=0,
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

    self.illumination_unit_holder = ResourceHolder(
      name=self.name + "_illumination_unit_holder",
      size_x=size_x,
      size_y=size_y,
      size_z=0,
      child_location=Coordinate(x=0, y=0, z=14.1),
    )
    self.assign_child_resource(self.illumination_unit_holder, location=Coordinate.zero())

  def assign_child_resource(
    self, resource: Resource, location: Optional[Coordinate], reassign: bool = True
  ) -> None:
    if isinstance(resource, _ByonoyAbsorbanceReaderPlateHolder):
      if self.plate_holder._byonoy_base is not None:
        raise ValueError("ByonoyBase can only have one plate holder assigned.")
      self.plate_holder._byonoy_base = self
    super().assign_child_resource(resource, location, reassign)

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    raise RuntimeError(
      "ByonoyBase does not support assigning child resources directly. "
      "Use the plate_holder or illumination_unit_holder to assign plates and the illumination unit, respectively."
    )


class ByonoyAbsorbance96Automate(PlateReader, ByonoyAbsorbanceBaseUnit):
  def __init__(self, name: str):
    ByonoyAbsorbanceBaseUnit.__init__(self, name=name + "_base")
    PlateReader.__init__(
      self,
      name=name + "_reader",
      size_x=138,
      size_y=95.7,
      size_z=0,
      backend=ByonoyAbsorbance96AutomateBackend(),
    )


def byonoy_a96a_detection_unit(name: str) -> ByonoyAbsorbance96Automate:
  """Create a Byonoy A96A detection unit `PlateReader`.

  The detection unit is modeled as a fixed-size rectangular prism.
  """

  return ByonoyAbsorbance96Automate(name=name)


def byonoy_a96a_parking_unit(name: str) -> ByonoyAbsorbanceBaseUnit:
  """Create a Byonoy A96A detection unit holder."""

  return ByonoyAbsorbanceBaseUnit(name=name)


def byonoy_a96a_illumination_unit(name: str) -> Resource:
  """ """
  size_x = 155.26
  size_y = 95.48
  return Resource(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=42.898,
    model="Byonoy A96A Illumination Unit",
    preferred_pickup_location=Coordinate(x=size_x / 2, y=size_y / 2, z=29.5),
  )


def byonoy_a96a(name: str, assign: bool = True) -> Tuple[ByonoyAbsorbance96Automate, Resource]:
  """Creates a ByonoyBase and a PlateReader instance."""
  reader = byonoy_a96a_detection_unit(
    name=name + "_reader",
  )
  illumination_unit = byonoy_a96a_illumination_unit(
    name=name + "_illumination_unit",
  )
  if assign:
    reader.illumination_unit_holder.assign_child_resource(illumination_unit)
  return reader, illumination_unit
