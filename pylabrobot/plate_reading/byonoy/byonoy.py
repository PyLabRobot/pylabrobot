from typing import Optional, Tuple

from pylabrobot.plate_reading.byonoy.byonoy_backend import ByonoyAbsorbance96AutomateBackend
from pylabrobot.plate_reading.plate_reader import PlateReader
from pylabrobot.resources import Coordinate, PlateHolder, Resource, ResourceHolder


def byonoy_absorbance_adapter(name: str) -> ResourceHolder:
  return ResourceHolder(
    name=name,
    size_x=127.76,  # measured
    size_y=85.59,  # measured
    size_z=14.07,  # measured
    child_location=Coordinate(
      x=-(138 - 127.76) / 2,  # measured
      y=-(95.7 - 85.59) / 2,  # measured
      z=14.07 - 2.45,  # measured
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
    pedestal_size_z: float = None,  # type: ignore
    child_location=Coordinate.zero(),
    category="plate_holder",
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
    self._byonoy_base: Optional["ByonoyBase"] = None

  def check_can_drop_resource_here(self, resource: Resource) -> None:
    if self._byonoy_base is None:
      raise RuntimeError(
        "ByonoyBase not assigned its plate holder. "
        "Please assign a ByonoyBase instance to the plate holder."
      )

    if self._byonoy_base.reader_holder.resource is not None:
      raise RuntimeError(
        f"Cannot drop resource {resource.name} onto plate holder while reader is on the base. "
        "Please remove the reader from the base before dropping a resource."
      )

    super().check_can_drop_resource_here(resource)


class ByonoyBase(Resource):
  def __init__(self, name, rotation=None, category=None, model=None, barcode=None):
    super().__init__(
      name=name,
      size_x=138,
      size_y=95.7,
      size_z=27.7,
    )

    self.plate_holder = _ByonoyAbsorbanceReaderPlateHolder(
      name=self.name + "_plate_holder",
      size_x=127.76,
      size_y=85.59,
      size_z=0,
      child_location=Coordinate(x=(138 - 127.76) / 2, y=(95.7 - 85.59) / 2, z=27.7),
      pedestal_size_z=0,
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

    self.reader_holder = ResourceHolder(
      name=self.name + "_reader_holder",
      size_x=138,
      size_y=95.7,
      size_z=0,
      child_location=Coordinate(x=0, y=0, z=10.66),
    )
    self.assign_child_resource(self.reader_holder, location=Coordinate.zero())

  def assign_child_resource(
    self, resource: Resource, location: Optional[Coordinate], reassign=True
  ):
    if isinstance(resource, _ByonoyAbsorbanceReaderPlateHolder):
      if self.plate_holder._byonoy_base is not None:
        raise ValueError("ByonoyBase can only have one plate holder assigned.")
      self.plate_holder._byonoy_base = self
    return super().assign_child_resource(resource, location, reassign)

  def check_can_drop_resource_here(self, resource: Resource) -> None:
    raise RuntimeError(
      "ByonoyBase does not support assigning child resources directly. "
      "Use the plate_holder or reader_holder to assign plates and the reader, respectively."
    )


def byonoy_absorbance96_base_and_reader(name: str, assign=True) -> Tuple[ByonoyBase, PlateReader]:
  """Creates a ByonoyBase and a PlateReader instance."""
  byonoy_base = ByonoyBase(name=name + "_base")
  reader = PlateReader(
    name=name + "_reader",
    size_x=138,
    size_y=95.7,
    size_z=0,
    backend=ByonoyAbsorbance96AutomateBackend(),
  )
  if assign:
    byonoy_base.reader_holder.assign_child_resource(reader)
  return byonoy_base, reader


# === absorbance ===

# total

# x: 138
# y: 95.7
# z: 53.35

# base
# z = 27.7
# z without skirt 25.25

# top
# z = 41.62

# adapter
# z = 14.07

# location of top wrt base
# z = 10.66

# pickup distance from top
# z = 7.45

# === lum ===

# x: 155.5
# y: 95.7
# z: 56.9
