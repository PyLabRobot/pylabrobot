"""Sergi Lab Supplies plate adapters"""

from pylabrobot.resources.plate_adapter import PlateAdapter
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


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
    model="SergiLabSupplies_96_MagneticRack_250ul_Vb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,  # from spec
      num_items_y=8,  # from spec
      dx=10.87,  # 14.3-6.86/2 measured
      dy=7.77,  # 11.2-6.86/2 measured
      dz=3.03,  # measured
      item_dx=9.0,  # measured
      item_dy=9.0,  # measured
      size_x=8.0,  # measured
      size_y=8.0,  # measured
      size_z=16.0,  # measured
      material_z_thickness=0.5,  # estimated
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      max_volume=250,  # from spec
    ),
  )
