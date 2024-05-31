""" PlateAdapter """

from __future__ import annotations

import logging
from typing import Optional
from collections import OrderedDict

from pylabrobot.resources.carrier import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.plate import Plate

from pylabrobot.serializer import serialize


logger = logging.getLogger("pylabrobot")


class PlateAdapter(Resource):
  """ Abstract base resource for a PlateAdapter, a resource which has a standardized
    well-grid (96- or 384 well-plates with 9x9 mm^2 or 4.5x4.5 mm^2) onto which a
    plate (skirted, sem-, and non-skirted) is placed.

    As a result, the dx and dy of the 

  Examples:
    1. Using a "magnetic rack" as a PlateAdapter:

      Define a `MFXCarrier`, define a `MFXModule` for deep well plates,
      assign the DWP module to the MFXCarrier:
      >>> mfx_carrier_1 = MFX_CAR_L5_base(name='mfx_carrier_1')
      >>> mfx_carrier_1[1] = mfx_dwp_module_1 = MFX_DWP_module(name="mfx_dwp_module_1")

      Define Alpaqua magnet as a PlateAdapter & assign to mfx_dwp_module_1 
      >>> Alpaqua_magnum_flx_1 = Alpaqua_magnum_flx(name='Alpaqua_magnum_flx_1')
      >>> mfx_dwp_module_1.assign_child_resource(Alpaqua_magnum_flx_1)

      Define Cos_96_Rd plate &assign to PlateAdapter:
      >>> Cos96_plate_1 = Cos_96_Rd(name='Cos96_plate_1')
      >>> Alpaqua_magnum_flx_1.assign_child_resource(Cos96_plate_1)

  """

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    dx: float, dy: float, dz: float,
    site_pedestal_z: Union[float, None] = None,
    category: Optional[str] = "plate_adapter",
    model: Optional[str] = None):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)
    # site where resources will be placed on this module
    self._child_resource_location = None
    self._child_resource: Optional[Resource] = None
    self.dx = dx
    self.dy = dy
    self.dz = dz
    self.site_pedestal_z = site_pedestal_z


  def assign_child_resource(
    self,
    resource: Plate,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    """ Assign a Plate to a PlateAdapter. If `location` is not provided, the resource
    will autoadjust the placement location based on the PlateAdapter-Plate relationship. """

    if self._child_resource is not None and not reassign:
      raise ValueError(f"{self.name} already has a child resource assigned")    
    if not isinstance(resource, Plate):
      raise ValueError("Only plates can be assigned to Alpaqua 96 magnum flx.")
    
    # TODO: have discussion of whether to transfer flat bottom error checking
    # TODO: check whether allPlate children information could
      # be made accessible from the Plate class
    
    # Calculate Plate information (which is not directly accessible from the Plate class)
    x_locations = sorted(OrderedDict.fromkeys([well_n.location.x
      for well_n in resource.children]))
    y_locations = sorted(OrderedDict.fromkeys([well_n.location.y
      for well_n in resource.children]))

    def calculate_well_spacing(float_list: List[float]):
      """ Calculate the difference between every x and x+1 element in the list of floats.
      """
      if len(float_list) < 2:
          return []
      differences = [float_list[i+1] - float_list[i] for i in range(len(float_list) - 1)]
      if len(list(OrderedDict.fromkeys(differences))) == 1:
          return differences[0]
    
    plate_dx, plate_dy = x_locations[0], y_locations[0]
    print(x_locations, y_locations)
    plate_item_dx = abs(calculate_well_spacing(x_locations))
    plate_item_dy = abs(calculate_well_spacing(y_locations))
    # true_dz = resource.get_size_z() - resource.children[0].get_size_z()

    # Well positioning check
    assert (plate_item_dx, plate_item_dy) in [(9.0, 9.0), (4.5, 4.5), (2.25, 2.25)], \
      "PlateAdapter only accepts plates with a well spacing of " + \
      "9x9, 4.5x4.5 or 2.25x2.25mm^2 (ANSI SLAS 4-2004 (R2012): Well Positions)"
    print(plate_dx, plate_dy)
    plate_x_adjustment = self.dx - plate_dx
    plate_y_adjustment = self.dy - plate_dy
    adjusted_plate_coordinate = Coordinate(plate_x_adjustment, plate_y_adjustment, self.dz)
    
    super().assign_child_resource(
      resource=resource,
      location=location or adjusted_plate_coordinate,
      reassign=reassign)
    self._child_resource = resource

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "child_resource_location": serialize(self._child_resource_location)}
