""" Alpaqua magnetic racks """
# currently implemented as MFX_modules to enable simple and fast assignment of plates to them
from pylabrobot.resources.ml_star.mfx_modules import *


def Alpaqua_96_magnum_flx(name: str) -> MFXModule:
  """ Alpaqua Engineering LLC cat. no.: A000400
  Magnetic rack for 96-well plates.
  """

  return MFXModule(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=26.5,
    skirt_height = 6.2,
    child_resource_location=Coordinate(-1.0, 0, 26.5),
    model="Alpaqua_96_magnum_flx",
  )
