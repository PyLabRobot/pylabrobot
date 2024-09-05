""" PlateAdapter """

from __future__ import annotations

import logging
from typing import Optional, List

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.plate import Plate


logger = logging.getLogger("pylabrobot")


class PlateAdapter(Resource):
  """ Abstract base resource for a PlateAdapter, a resource which has a standardized
  well-grid onto which a plate (skirted, sem-, and non-skirted) is placed.

  As a result of the PlateAdapter well_holes having a different dx & dy than the plates,
  the precise anchor location `to` which the plate is moved has to be calculated on the fly.

  This PlateAdapter class is capable of doing so, and is therefore the base class for
  diverse resources, e.g.:
  - plate adapters (e.g. for semi- and non-skirted plates which cannot be used on standard
    carrier sites)
  - plate adapters for shakers/heater-shakers/temperature control machines
  - magnetic racks
  - On-Deck ThermoCyclers (OTDCs)

  Args:
    name: The name of the PlateAdapter.
    size_x: The size of the PlateAdapter in the x dimension.
    size_y: The size of the PlateAdapter in the y dimension.
    size_z: The size of the PlateAdapter in the z dimension.
    dx: The x-coordinate offset for well positioning.
    dy: The y-coordinate offset for well positioning.
    dz: The z-coordinate offset for well positioning, i.e. the outside-bottom of a well.
    adapter_item_dx: The x-dimension spacing of wells. Defaults to 9.0.
    adapter_item_dy: The y-dimension spacing of wells. Defaults to 9.0.
    site_pedestal_z: The z-coordinate of the site pedestal.  Defaults to None.
    category: The category of the PlateAdapter.  Defaults to "plate_adapter".
    model: The model of the PlateAdapter. Defaults to None.

  Examples:
    1. Using a "magnetic rack" as a PlateAdapter:

      Define a `MFXCarrier`, define a `MFXModule` for deep well plates,
      assign the DWP module to the MFXCarrier:
      >>> mfx_carrier_1 = MFX_CAR_L5_base(name="mfx_carrier_1")
      >>> mfx_carrier_1[1] = mfx_dwp_module_1 = MFX_DWP_module(name="mfx_dwp_module_1")

      Define Alpaqua magnet as a PlateAdapter & assign to mfx_dwp_module_1:
      >>> Alpaqua_magnum_flx_1 = Alpaqua_magnum_flx(name="Alpaqua_magnum_flx_1")
      >>> mfx_dwp_module_1.assign_child_resource(Alpaqua_magnum_flx_1)

      Define Cos_96_Rd plate & assign to PlateAdapter:
      >>> Cos96_plate_1 = Cos_96_Rd(name="Cos96_plate_1")
      >>> Alpaqua_magnum_flx_1.assign_child_resource(Cos96_plate_1)
  """

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    dx: float, dy: float, dz: float,
    adapter_hole_size_x: float,
    adapter_hole_size_y: float,
    adapter_hole_size_z: float,
    site_pedestal_z: float,
    adapter_hole_dx: float = 9.0,
    adapter_hole_dy: float = 9.0,
    plate_z_offset: float = 0.0,
    category: Optional[str] = None,
    model: Optional[str] = None
  ):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z,
      category=category or "plate_adapter", model=model)

    self._child_resource: Optional[Resource] = None
    self.dx = dx
    self.dy = dy
    self.dz = dz
    self.adapter_hole_size_x = adapter_hole_size_x
    self.adapter_hole_size_y = adapter_hole_size_y
    self.adapter_hole_size_z = adapter_hole_size_z
    self.adapter_hole_size_z = self.get_absolute_size_z() - self.dz
    self.adapter_hole_dx = adapter_hole_dx
    self.adapter_hole_dy = adapter_hole_dy
    self.plate_z_offset = plate_z_offset
    self.site_pedestal_z = site_pedestal_z

  def compute_plate_location(self, resource: Plate) -> Coordinate:
    """ Compute the location of the `Plate` child resource in relationship to the `PlateAdapter` to
    align the `Plate` well-grid with the adapter's hole grid. """

    # Calculate Plate information (which is not directly accessible from the Plate class)
    # Child locations are never None, so we can safely ignore the type error
    x_locations = sorted(set(well_n.location.x for well_n in resource.children)) # type: ignore
    y_locations = sorted(set(well_n.location.y for well_n in resource.children)) # type: ignore

    def calculate_well_spacing(float_list: List[float]):
      """ Calculate the difference between every x and x+1 element in the list of floats. """
      if len(float_list) < 2:
        return None
      differences = [round(float_list[i+1] - float_list[i],2) for i in range(len(float_list) - 1)]
      if len(set(differences)) == 1:
        return differences[0]
      raise ValueError("well spacing has to be uniform")

    plate_dx, plate_dy = float(x_locations[0]), float(y_locations[0])
    plate_item_dx = abs(calculate_well_spacing(x_locations))
    plate_item_dy = abs(calculate_well_spacing(y_locations))
    well_size_x = resource.children[0].get_absolute_size_x()
    well_size_y = resource.children[0].get_absolute_size_y()
    # true_dz = resource.get_size_z() - resource.children[0].get_size_z()

    # Well-grid to hole-grid compatibility check
    assert (plate_item_dx, plate_item_dy) == (self.adapter_hole_dx, self.adapter_hole_dy), \
        "Plate well spacing must be equivalent to adapter hole spacing"

    # Calculate adjustment to place center of H1_plate on top of center of H1_adapter
    plate_x_adjustment = self.dx - plate_dx + self.adapter_hole_size_x/2 - well_size_x/2
    plate_y_adjustment = self.dy - plate_dy + self.adapter_hole_size_x/2 - well_size_y/2
    # basic plate_z_adjustment ability
    plate_z_adjustment = self.dz + self.plate_z_offset
    # TODO: create more sophisticated plate_z_adjustment based on
    # PlateAdapter.adapter_hole_size_z & Plate.well.get_size_z() relationship, when
    # Plate definitions are fixed, which can be modified if needed using plate_z_offset

    adjusted_plate_anchor = Coordinate(plate_x_adjustment, plate_y_adjustment, plate_z_adjustment)
    return adjusted_plate_anchor

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    """Assign a Plate to a PlateAdapter. If `location` is not provided, the resource will autoadjust
    the placement location based on the PlateAdapter-Plate relationship. """

    if not isinstance(resource, Plate):
      raise TypeError("Only plates can be assigned to plate adapters")

    if self._child_resource is not None and not reassign:
      raise ValueError(f"{self.name} already has a child resource assigned")

    # TODO: have discussion oon whether to transfer flat bottom error checking
    # TODO: check whether all Plate children information could
    # be made accessible from the Plate class

    super().assign_child_resource(
      resource=resource,
      location=location or self.compute_plate_location(resource),
      reassign=reassign)
    self._child_resource = resource
