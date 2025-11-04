from pylabrobot.resources.tube import Tube


def celltreat_15000ul_centrifuge_tube_Vb(name: str) -> Tube:
  """CELLTREAT® Centrifuge Tubes-RackMaster 15mL Centrifuge Tube, Best Value - Paperboard Rack, Sterile
  Part no.: 229414

  - bottom_type=TubeBottomType.V
  """
  diameter = 14.7  # measured
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=190.5,  # measured
    model=celltreat_15000ul_centrifuge_tube_Vb.__name__,
    max_volume=15_000,  # units: ul
    material_z_thickness=0.8,  # measured
  )
