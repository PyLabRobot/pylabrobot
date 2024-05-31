""" Hamilton magnetic racks """

from pylabrobot.resources.plate_adapter import PlateAdapter


def Hamilton_96_adapter_188182(name: str) -> PlateAdapter:
  """ Hamilton cat. no.: 188182
  Adapter for 96 well PCR plate, plunged.
  Does not have an ANSI/SLAS footprint -> requires assignment with specified location.
  """
  # size_x=8
  # size_y=8
  return PlateAdapter(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=26.5,
    dx=9.8,
    dy=6.8,
    dz=26.5, # TODO: correct dz once Plate definition has been completely fixed
    site_pedestal_z=6.2,
    model="Hamilton_96_adapter_188182",
    )
