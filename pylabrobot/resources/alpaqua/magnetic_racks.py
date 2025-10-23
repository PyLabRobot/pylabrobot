import warnings

# implemented as PlateAdapter to enable simple and fast assignment
# of plates to them, with self-correcting location placement
from pylabrobot.resources.plate_adapter import PlateAdapter


def alpaqua_96_plateadapter_magnum_flx(name: str) -> PlateAdapter:
  """Alpaqua Engineering LLC cat. no.: A000400
  Magnetic rack for 96-well plates.
  implemented as PlateAdapter to enable simple and fast assignment of
    plates to them, with self-correcting location placement
  """
  return PlateAdapter(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=35.0,
    dx=9.8,
    dy=6.8,
    dz=27.5,  # refers to magnet hole bottom
    plate_z_offset=0.0,  # adjust at runtime based on plate's well geometry
    adapter_hole_size_x=8.0,
    adapter_hole_size_y=8.0,
    adapter_hole_size_z=8.0,  # guesstimate
    model=alpaqua_96_plateadapter_magnum_flx.__name__,
  )


# Deprecated names for backwards compatibility
# TODO: Remove >2026-02


def Alpaqua_96_magnum_flx(name: str) -> PlateAdapter:
  """Deprecated alias for `alpaqua_96_plateadapter_magnum_flx`."""
  warnings.warn(
    "Alpaqua_96_magnum_flx is deprecated. Use 'alpaqua_96_plateadapter_magnum_flx' instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return alpaqua_96_plateadapter_magnum_flx(name)
