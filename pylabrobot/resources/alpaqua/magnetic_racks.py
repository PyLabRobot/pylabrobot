""" Alpaqua magnetic racks """
# currently implemented as PlateAdapter to enable simple and fast assignment
# of plates to them, with self-correcting location placement

from pylabrobot.resources.plate_adapter import PlateAdapter


def Alpaqua_96_magnum_flx(name: str) -> PlateAdapter:
  """ Alpaqua Engineering LLC cat. no.: A000400
  Magnetic rack for 96-well plates.
  """
  return PlateAdapter(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=35.0,
    dx=9.8,
    dy=6.8,
    dz=27.5, # TODO: correct dz once Plate definition has been completely fixed
    adapter_hole_size_x=8.0,
    adapter_hole_size_y=8.0,
    site_pedestal_z=6.2,
    model="Alpaqua_96_magnum_flx",
    )

