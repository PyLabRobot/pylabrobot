""" Hamilton magnetic racks """

from pylabrobot.resources.plate_adapter import PlateAdapter


def Hamilton_96_adapter_188182(name: str) -> PlateAdapter:
  """ Hamilton cat. no.: 188182
  Adapter for 96 well PCR plate, plunged.
  Does not have an ANSI/SLAS footprint -> requires assignment with specified location.
  """
  return PlateAdapter(
    name=name,
    size_x=110.0,
    size_y=75.0,
    size_z=15.0,
    dx=1,
    dy=2.2,
    dz=2.8, # TODO: correct dz once Plate definition has been completely fixed
    adapter_hole_size_x=7.4,
    adapter_hole_size_y=7.4,
    adapter_hole_size_z=10.0, # guesstimate
    site_pedestal_z=15.0,
    model="Hamilton_96_adapter_188182",
  )
