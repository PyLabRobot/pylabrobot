"""Corning-Falcon Tubes"""

from pylabrobot.resources.tube import Tube

# # # # # # # # # # Cor_Falcon_tube_50mL_Vb # # # # # # # # # #


def Cor_Falcon_tube_50mL_Vb(name: str) -> Tube:
  """
  Corning cat. no.: 352098
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Liquid-Handling/Tubes%2C-Liquid-Handling/Centrifuge-Tubes/Falcon%C2%AE-Conical-Centrifuge-Tubes/p/falconConicalTubes
  - distributor: (Fisher Scientific, 14-959-49A)
  - brand: Falcon
  - material: Polypropylene
  - tech_drawing: tech_drawings/Cor_Falcon_tube_50mL.pdf
  - cap_style: screw-cap
  """

  diameter = 30
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=115,
    model="Falcon 50mL",
    max_volume=50_000,
    material_z_thickness=1.2,
  )


# # # # # # # # # # Cor_Falcon_tube_15mL_Vb # # # # # # # # # #


def Cor_Falcon_tube_15mL_Vb(name: str) -> Tube:
  """
  Corning cat. no.: 352196
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Liquid-Handling/Tubes%2C-Liquid-Handling/Centrifuge-Tubes/Falcon%C2%AE-Conical-Centrifuge-Tubes/p/falconConicalTubes
  - distributor: (Fisher Scientific, 14-959-53A)
  - brand: Falcon
  - material: Polypropylene
  - tech_drawing: tech_drawings/Cor_Falcon_tube_15mL_Vb.pdf
  - cap_style: screw-cap
  """

  diameter = 17
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=120,
    model="Falcon 15mL",
    max_volume=15_000,
  )


# # # # # # # # # # Cor_Falcon_tube_14mL_Rb # # # # # # # # # #


def Cor_Falcon_tube_14mL_Rb(name: str) -> Tube:
  """
  Corning cat. no.: 352059
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/General-Labware/Tubes/Tubes%2C-Round-Bottom/Falcon%C2%AE-Round-Bottom-High-clarity-Polypropylene-Tube/p/highClarityPolypropyleneRoundBottomTubes
  - distributor: (Fisher Scientific, 10110101)
  - brand: Falcon
  - material: Polypropylene
  - tech_drawing: tech_drawings/Cor_Falcon_tube_14mL_Rb.pdf
  - cap_style: snap-cap
  """

  diameter = 17
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=95,
    model="Falcon_tube_14mL_Rb",
    material_z_thickness=1.19,
    max_volume=14_000,  # units: ul
  )


# Previous names in PLR:


def falcon_tube_50mL(name: str) -> Tube:
  raise NotImplementedError(
    "falcon_tube_50mL definition is deprecated. Use Cor_Falcon_tube_50mL instead."
  )


def falcon_tube_15mL(name: str) -> Tube:
  raise NotImplementedError(
    "falcon_tube_15mL definition is deprecated. Use Cor_Falcon_tube_15mL_Vb instead."
  )


def Falcon_tube_14mL_Rb(name: str) -> Tube:
  raise NotImplementedError(
    "Falcon_tube_14mL_Rb definition is deprecated. Use Cor_Falcon_tube_14mL_Rb instead."
  )
