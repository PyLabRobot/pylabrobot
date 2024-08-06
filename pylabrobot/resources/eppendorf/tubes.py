from pylabrobot.resources.tube import Tube


def Eppendorf_DNA_LoBind_1_5ml_Vb(name: str) -> Tube:
  """ 1.5 mL round-bottom snap-cap Eppendorf tube. cat. no.: 022431021

  - bottom_type=TubeBottomType.V
  - snap-cap lid
  """
  # material_z_thickness = 2.4 mm
  diameter = 17
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=39,
    model="Eppendorf_DNA_LoBind_1_5ml_Vb",
    max_volume=1_400 # units: ul
  )
