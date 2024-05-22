""" Alpaqua magnetic racks """
# currently implemented as MFX_modules to enable simple and fast assignment of plates to them
from typing import Optional

from pylabrobot.resources.ml_star.mfx_modules import MFXModule
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import WellBottomType


class _Alpaqua_96_magnum_flx(MFXModule):
  """ Subclass of MFXModule for Alpaqua 96 magnum flx rack. Override child resource assignment
  to only accept flat bottom plates. """

  def __init__(self, name: str):
    super().__init__(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=26.5,
    skirt_height=6.2,
    child_resource_location=Coordinate(-1.0, 0, 26.5),
    model="Alpaqua_96_magnum_flx",
    )

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    if not isinstance(resource, Plate):
      raise ValueError("Only plates can be assigned to Alpaqua 96 magnum flx.")
    if resource.get_well(0).bottom_type not in {WellBottomType.U, WellBottomType.V}:
      raise ValueError("Only plates with U or V bottom can be assigned to Alpaqua 96 magnum flx.")
    return super().assign_child_resource(resource, location, reassign)


def Alpaqua_96_magnum_flx(name: str) -> _Alpaqua_96_magnum_flx:
  """ Alpaqua Engineering LLC cat. no.: A000400
  Magnetic rack for 96-well plates.
  """

  return _Alpaqua_96_magnum_flx(name=name)
