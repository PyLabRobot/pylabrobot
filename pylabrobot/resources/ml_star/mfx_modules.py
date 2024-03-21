""" MFX modules (including machine definitions placed on a MFX carrier) """

from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.resources.carrier import Coordinate
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipRack


logger = logging.getLogger("pylabrobot")


class MFXModule(Resource):
  """ Abstract base resource for MFX modules to be placed on a MFX carrier (landscape/portrait,
  4/5 positions).

  Examples:
    1. Creating MFX module for tips:
      Creating a `MFXCarrier`,
      Creating a `MFXModule` for tips,
      Assigning the `MFXModule` for tips to a carrier_site on the `MFXCarrier`,
      Creating and assigning a tip_rack to the MFXsite on the MFXModule:

      >>> mfx_carrier_1 = MFX_CAR_L5_base(name='mfx_carrier_1')
      >>> mfx_carrier_1[0] = mfx_tip_module_1 = MFX_TIP_module(name="mfx_tip_module_1")
      >>> mfx_tip_module_1[0] = tip_50ul_rack = TIP_50ul_L(name="tip_50ul_rack")

    2. Creating MFX module for plates:
      Use the same `MFXCarrier` instance,
      Creating a `MFXModule` for plates,
      Assigning the `MFXModule` for plates to a carrier_site on the `MFXCarrier`,
      Creating and assigning a plate to the MFXsite on the MFXModule:

      >>> mfx_carrier_1[1] = mfx_dwp_module_1 = MFX_DWP_module(name="mfx_dwp_module_1")
      >>> mfx_dwp_module_1[0] = Cos96_plate_1 = Cos_96_Rd(name='Cos96_plate_1')
  """

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    category: Optional[str] = "mfx_module",
    model: Optional[str] = None):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)

################## MFX module library #################

################## 1. Static modules ##################


class MFX_TIP_module(MFXModule):
  """ Hamilton cat. no.: 188160
  Module to position a high-, standard-, low volume or 5ml tip rack (but not a 384 tip rack).
  """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=94.0,
      size_z=214.8-18.195-100,
      model="MFX_TIP_module"
    )

    self.tip_rack: Optional[TipRack] = None

  def serialize(self) -> dict:
    return {"name": self.name}

  def assign_child_resource(self, resource: Resource, location: Coordinate, reassign: bool = True):
    if self.tip_rack is not None:
      raise ValueError(f"tip rack already assigned to {self.name}")
    return super().assign_child_resource(resource, location, reassign)

  def assign_tip_rack(self, tip_rack: TipRack, reassign: bool = True):
    # site_size_x=122.4,
    # site_size_y=82.6,
    return self.assign_child_resource(
      tip_rack,
      location=Coordinate(6.2, 5.0, 214.8-18.195-100), # probe height - carrier_height - deck_height
      reassign=reassign)


class MFX_DWP_RB_module(MFXModule):
  """ Hamilton cat. no.: 188229 (MFX DWP module rack-based)
  Module to position a Deep Well Plate / tube racks (MATRIX or MICRONICS) / NUNC reagent trough.
  """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=94.0,
      size_z=178.73-18.195-100,
      model="MFX_TIP_module"
    )

    self.plate: Optional[Plate] = None

  def serialize(self) -> dict:
    return {"name": self.name}

  def assign_child_resource(self, resource: Resource, location: Coordinate, reassign: bool = True):
    if self.plate is not None:
      raise ValueError(f"plate already assigned to {self.name}")
    return super().assign_child_resource(resource, location, reassign)

  def assign_plate(self, plate: Plate, reassign: bool = True):
    # site_size_x=127.0,
    # site_size_y=86.0,
    return self.assign_child_resource(
    plate,
    location=Coordinate(4.0, 3.5, 178.73-18.195-100), # probe height - carrier_height - deck_height
    reassign=reassign)
