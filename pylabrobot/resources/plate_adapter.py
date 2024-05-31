""" PlateAdapter """

from __future__ import annotations

import logging
from typing import Optional, List, Union
from collections import OrderedDict

from pylabrobot.resources.carrier import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.plate import Plate

from pylabrobot.serializer import serialize


logger = logging.getLogger("pylabrobot")


class PlateAdapter(Resource):
  """ Abstract base resource for a PlateAdapter, a resource which has a standardized
    well-grid (96-, 384- or 1536- well-plates with 9x9, 4.5x4.5 or 2.25x2.25 mm^2) onto
    which a plate (skirted, sem-, and non-skirted) is placed.

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
        name (str): The name of the PlateAdapter.
        size_x (float): The size of the PlateAdapter in the x dimension.
        size_y (float): The size of the PlateAdapter in the y dimension.
        size_z (float): The size of the PlateAdapter in the z dimension.
        dx (float): The x-coordinate offset for well positioning.
        dy (float): The y-coordinate offset for well positioning.
        dz (float): The z-coordinate offset for well positioning, i.e. the outside-bottom of a well.
        adapter_item_dx (Literal[9.0, 4.5, 2.25], optional): The x-dimension spacing of wells. Defaults to 9.0.
        adapter_item_dy (Literal[9.0, 4.5, 2.25], optional): The y-dimension spacing of wells. Defaults to 9.0.
        site_pedestal_z (Optional[float], optional): The z-coordinate of the site pedestal. Defaults to None.
        category (Optional[str], optional): The category of the PlateAdapter. Defaults to "plate_adapter".
        model (Optional[str], optional): The model of the PlateAdapter. Defaults to None.
   
  Examples:
    1. Using a "magnetic rack" as a PlateAdapter:

      Define a `MFXCarrier`, define a `MFXModule` for deep well plates,
      assign the DWP module to the MFXCarrier:
      >>> mfx_carrier_1 = MFX_CAR_L5_base(name='mfx_carrier_1')
      >>> mfx_carrier_1[1] = mfx_dwp_module_1 = MFX_DWP_module(name="mfx_dwp_module_1")

      Define Alpaqua magnet as a PlateAdapter & assign to mfx_dwp_module_1:
      >>> Alpaqua_magnum_flx_1 = Alpaqua_magnum_flx(name='Alpaqua_magnum_flx_1')
      >>> mfx_dwp_module_1.assign_child_resource(Alpaqua_magnum_flx_1)

      Define Cos_96_Rd plate & assign to PlateAdapter:
      >>> Cos96_plate_1 = Cos_96_Rd(name='Cos96_plate_1')
      >>> Alpaqua_magnum_flx_1.assign_child_resource(Cos96_plate_1)
  """

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    dx: float, dy: float, dz: float,
    adapter_hole_size_x: float,
    adapter_hole_size_y: float,
    adapter_hole_dx: float = 9.0, 
    adapter_hole_dy: float = 9.0,
    site_pedestal_z: Union[float, None] = None,
    category: Optional[str] = "plate_adapter",
    model: Optional[str] = None
    ):
    if adapter_hole_dx not in {9.0, 4.5, 2.25}:
      raise ValueError("adapter_hole_dx must be one of 9.0, 4.5, or 2.25")
    if adapter_hole_dy not in {9.0, 4.5, 2.25}:
      raise ValueError("adapter_hole_dy must be one of 9.0, 4.5, or 2.25")
     
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)
    self._child_resource_location = None
    self._child_resource: Optional[Resource] = None
    self.dx = dx
    self.dy = dy
    self.dz = dz
    self.adapter_hole_size_x = adapter_hole_size_x
    self.adapter_hole_size_y = adapter_hole_size_y
    self.adapter_hole_size_z = self.get_size_z() - self.dz 
    self.adapter_hole_dx = adapter_hole_dx
    self.adapter_hole_dy = adapter_hole_dy
    self.site_pedestal_z = site_pedestal_z

  @property
  def child_resource_location(self) -> Coordinate:
    return self._child_resource_location

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
    
    # TODO: have discussion oon whether to transfer flat bottom error checking
    # TODO: check whether allPlate children information could
      # be made accessible from the Plate class
    
    # Calculate Plate information (which is not directly accessible from the Plate class)
    x_locations = sorted(OrderedDict.fromkeys([well_n.location.x
      for well_n in resource.children]))
    y_locations = sorted(OrderedDict.fromkeys([well_n.location.y
      for well_n in resource.children]))

    def calculate_well_spacing(float_list: List[float]) -> Optional[float]:
      """ Calculate the difference between every x and x+1 element in the list of floats. """
      if len(float_list) < 2:
          return None
      differences = [round(float_list[i+1] - float_list[i],2) for i in range(len(float_list) - 1)]
      if len(list(OrderedDict.fromkeys(differences))) == 1:
          return differences[0]
      return None  # Explicitly return None if no single spacing value is found

    
    plate_dx, plate_dy = x_locations[0], y_locations[0]
    plate_item_dx = abs(calculate_well_spacing(x_locations))
    plate_item_dy = abs(calculate_well_spacing(y_locations))
    well_size_x = resource.children[0].get_size_x()
    well_size_y = resource.children[0].get_size_y()
    # true_dz = resource.get_size_z() - resource.children[0].get_size_z()

    # Well positioning check
    assert (plate_item_dx, plate_item_dy) in [(9.0, 9.0), (4.5, 4.5), (2.25, 2.25)], \
      "PlateAdapter only accepts plates with a well spacing of " + \
      "9x9, 4.5x4.5 or 2.25x2.25mm^2 (ANSI SLAS 4-2004 (R2012): Well Positions)"

    # Calculate adjustment to place center of H1_plate on top of center of H1_adapter
    plate_x_adjustment = self.dx - plate_dx + self.adapter_hole_size_x/2 - well_size_x/2
    plate_y_adjustment = self.dy - plate_dy + self.adapter_hole_size_x/2 - well_size_y/2
    # TODO: create plate_z_adjustment based on PlateAdapter.adapter_hole_size_z & 
      # Plate.well.get_size_z() relationship, when Plate definitions are fixed

    adjusted_plate_anchor = Coordinate(plate_x_adjustment, plate_y_adjustment, self.dz)
    self._child_resource_location = adjusted_plate_anchor

    super().assign_child_resource(
      resource=resource,
      location=location or adjusted_plate_anchor,
      reassign=reassign)
    self._child_resource = resource

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "child_resource_location": serialize(self._child_resource_location)}
