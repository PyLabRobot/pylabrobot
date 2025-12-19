from typing import Optional, Tuple

from pylabrobot.plate_reading.byonoy.byonoy_backend import ByonoyAbsorbance96AutomateBackend
from pylabrobot.plate_reading.plate_reader import PlateReader
from pylabrobot.resources import Coordinate, PlateHolder, Resource, ResourceHolder, Plate


# NEW RESOURCE MODELLING SYSTEM FOR BYONOY A96A

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

def byonoy_a96a_illumination_unit(
  name: str,
) -> Resource:
  """
  """
  return Resource(
    name=name,
    size_x=155.26,
    size_y=95.48,
    size_z=42.898,
    model="Byonoy A96A Illumination Unit",
  )


def byonoy_a96a_detection_unit(
  name: str,
  model: str = "Byonoy A96A Detection Unit",
) -> ResourceHolder:
  """Create a Byonoy A96A detection unit `ResourceHolder`.

  The detection unit is modeled as a fixed-size rectangular prism. The
  `child_location` specifies the default origin of the child frame within the
  detection unit's frame (an internal alignment/reference point used for placing
  child resources).

  Args:
    name: Resource name to assign to the returned `ResourceHolder`.
    model: Model string stored on the `ResourceHolder`. Defaults to
      "Byonoy A96A Detection Unit".

  Returns:
    A configured `ResourceHolder` instance representing the detection unit.
  """




  return ResourceHolder(
    name=name,
    size_x=155.26,
    size_y=95.48,
    size_z=18.5,
    child_location=Coordinate(
      x=22.5,
      y=5.0,
      z=16.0,
    ),
    model=model,
  )


def byonoy_a96a_parking_unit(name: str) -> ResourceHolder:
  """Create a Byonoy A96A parking unit `ResourceHolder`.

  This is equivalent to `byonoy_a96a_detection_unit(...)` but with the model
  string set to "Byonoy A96A Parking Unit".

  Args:
    name: Resource name to assign to the returned `ResourceHolder`.

  Returns:
    A configured `ResourceHolder` instance representing the parking unit.
  """
  return byonoy_a96a_detection_unit(
    name=name,
    model="Byonoy A96A Parking Unit",
  )


class ByonoyA96ABaseUnit(ResourceHolder):
  def __init__(self, name, rotation=None, category=None, model=None, barcode=None):
    super().__init__(
      name=name,
      size_x=155.26,
      size_y=95.48,
      size_z=18.5,
      child_location=Coordinate( # Dafault location for plate holder
        x=22.5,
        y=5.0,
        z=16.0,
      ),
    )

    child_location_map_per_model = { # Can be extended for future top units
      "Byonoy A96A Illumination Unit": Coordinate(x=0.0, y=0.0, z=14.1),
    }
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())



  def assign_child_resource(
    self, resource: Resource, location: Optional[Coordinate] = None, reassign=True
  ):
    
    # Check there is no resource on the Byonoy base unit
    if len(self.children) != 0:

        # Check whether illumination_unit already on BaseUnit
        if "Byonoy A96A Illumination Unit" in [
          child.model for child in self.children
        ]:
          raise ValueError(
            f"'{self.name}' already has an illumination unit assigned."
            f"Cannot assign '{resource.name}' while an illumination unit"
              " is already assigned."
          )
        
        # Check maximum number of child resources (plate holder + illumination unit)
        if len(self.children) >= 2:
          raise ValueError(
            f"'{self.name}' already has maximum number of child resources assigned."
            f"Cannot assign '{resource.name}'."
            f" Current children: {[child.name for child in self.children]}."
          )
        # Assign child location based on model



    
    if location is None:
      location = self.child_location
    
    # Check if the resource is a Byonoy A96A Illumination Unit
    if resource.model == "Byonoy A96A Illumination Unit":

      location = self.child_location_map_per_model[resource.model]

    elif isinstance(resource, Plate):
      
      location = self.child_location

    return super().assign_child_resource(resource, location, reassign)

  def check_can_drop_resource_here(self, resource: Resource) -> None:
    raise RuntimeError(
      "ByonoyBase does not support assigning child resources directly. "
      "Use the plate_holder or reader_holder to assign plates and the reader, respectively."
    )
  
  


# OLD MODEL

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
