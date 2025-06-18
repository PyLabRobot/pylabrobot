"""Sergi Lab Supplies plate adapters"""

from pylabrobot.resources.plate_adapter import PlateAdapter


def SergiLabSupplies_96_MagneticRack_250ul_Vb(name: str) -> PlateAdapter:
  """
  Sergi Lab Supplies Magnetic Rack [cat. no. 1047]
  - Holds a 96-well PCR plate for DNA/RNA purification
  - SBS footprint: 127.76 x 85.48 mm
  - Rack height: 22.0 mm
  - Pulls magnetic beads ~1-2 mm above bottom
  """

  # measurements from technical drawing:
  # https://sergilabsupplies.com/collections/magnetic-racks/products/96-wells-magnetic-rack-for-dna-rna-and-other-molecules-purification
  return PlateAdapter(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=22.0,  # from spec
    dx=9.88,  # measured (127.76 - 108) / 2 = 9.88
    dy=4.74,  # measured (85.48 - 76) / 2 = 4.74
    dz=7.0,  # measured
    adapter_hole_size_x=7.0,  # measured
    adapter_hole_size_y=7.0,  # measured
    adapter_hole_size_z=16.0,  # measured (22.0 - 6) = 16.0
    model="SergiLabSupplies_96_MagneticRack_250ul_Vb",
  )
