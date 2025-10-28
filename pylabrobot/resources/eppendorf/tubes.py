from pylabrobot.resources.tube import Tube


def Eppendorf_DNA_LoBind_1_5ml_Vb(name: str, model="Eppendorf_DNA_LoBind_1_5ml_Vb") -> Tube:
  """1.5 mL round-bottom snap-cap Eppendorf tube.

  cat. no.: 022431021 (Eppendorf™ DNA LoBind™ Tubes)

  - bottom_type=TubeBottomType.V
  - snap-cap lid
  """
  diameter = 10.33
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=39.5,
    model="Eppendorf_DNA_LoBind_1_5ml_Vb",
    max_volume=1_400,
    material_z_thickness=0.8,
  )


def Eppendorf_Protein_LoBind_1_5ml_Vb(name: str) -> Tube:
  """1.5 mL round-bottom screw-cap Eppendorf tube.

  cat. no.: 022431081 (Eppendorf™ Protein LoBind™ Tubes)

  Same as Eppendorf_DNA_LoBind_1_5ml_Vb
  """
  return Eppendorf_DNA_LoBind_1_5ml_Vb(name=name, model="Eppendorf_Protein_LoBind_1_5ml_Vb")
