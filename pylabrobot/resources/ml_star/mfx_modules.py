""" MFX modules (including machine definitions placed on a MFX carrier) """

from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.resources.carrier import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.serializer import serialize


logger = logging.getLogger("pylabrobot")


class MFXModule(Resource):
  """ Abstract base resource for MFX modules to be placed on a MFX carrier (landscape/portrait,
  4/5 positions).

  Examples:
    1. Creating MFX module for tips:
      Create a `MFXCarrier`,
      Create `MFXModule` for tips,
      Assign the `MFXModule` for tips to a carrier_site on the `MFXCarrier`,
      Create and assign a tip_rack to the MFXModule:

      >>> mfx_carrier_1 = MFX_CAR_L5_base(name='mfx_carrier_1')
      >>> mfx_carrier_1[0] = mfx_tip_module_1 = MFX_TIP_module(name="mfx_tip_module_1")
      >>> tip_50ul_rack = TIP_50ul_L(name="tip_50ul_rack")
      >>> mfx_tip_module_1.assign_child_resource(tip_50ul_rack)

    2. Creating MFX module for plates:
      Use the same `MFXCarrier` instance,
      Create a `MFXModule` for plates,
      Assign the `MFXModule` for plates to a carrier_site on the `MFXCarrier`,
      Create and assign a plate directly to the MFXModule:

      >>> mfx_carrier_1[1] = mfx_dwp_module_1 = MFX_DWP_rackbased_module(name="mfx_dwp_module_1")
      >>> Cos96_plate_1 = Cos_96_Rd(name='cos96_plate_1')
      >>> mfx_dwp_module_1.assign_child_resource(Cos96_plate_1)
  """

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    child_resource_location: Coordinate,
    category: Optional[str] = "mfx_module",
    pedestal_size_z: Optional[float] = None,
    model: Optional[str] = None):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)
    # site where resources will be placed on this module
    self._child_resource_location = child_resource_location
    self._child_resource: Optional[Resource] = None
    self.pedestal_size_z: Optional[float] = pedestal_size_z
    # TODO: add self.pedestal_2D_offset if necessary in the future

  @property
  def child_resource_location(self) -> Coordinate:
    return self._child_resource_location

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    """ Assign a resource to a site on this module. If `location` is not provided, the resource
    will be placed at `self._child_resource_location` (wrt this module's left front bottom). """

    # TODO: add conditional logic to modify Plate position based on whether
    # pedestal_size_z>plate_true_dz OR pedestal_z<pedestal_size_z IF child.category == 'plate'

    if self._child_resource is not None and not reassign:
      raise ValueError(f"{self.name} already has a child resource assigned")
    super().assign_child_resource(
      resource=resource,
      location=location or self._child_resource_location,
      reassign=reassign)
    self._child_resource = resource

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "child_resource_location": serialize(self._child_resource_location)}

################## MFX module library #################

################## 1. Static modules ##################


def MFX_TIP_module(name: str) -> MFXModule:
  """ Hamilton cat. no.: 188160
  Module to position a high-, standard-, low volume or 5ml tip rack (but not a 384 tip rack).
  """

  # site_size_x=122.4,
  # site_size_y=82.6,

  return MFXModule(
    name=name,
    size_x=135.0,
    size_y=94.0,
    size_z=214.8-18.195-100,
    # probe height - carrier_height - deck_height
    child_resource_location=Coordinate(6.2, 5.0, 214.8-18.195-100),
    model="MFX_TIP_module",
  )


def MFX_DWP_rackbased_module(name: str) -> MFXModule:
  """ Hamilton cat. no.: 188229
  Module to position a Deep Well Plate / tube racks (MATRIX or MICRONICS) / NUNC reagent trough.
  """

  # site_size_x=127.76,
  # site_size_y=85.48,

  return MFXModule(
    name=name,
    size_x=135.0,
    size_y=94.0,
    size_z=178.0-18.195-100,
    # probe height - carrier_height - deck_height
    child_resource_location=Coordinate(4.0, 3.5, 178.0-18.195-100),
    model="MFX_TIP_module",
  )
