"""Corning-Falcon Tubes"""

import warnings

from pylabrobot.resources.tube import Tube


# # # # # # # # # # cor_falcon_tube_14mL_Rb # # # # # # # # # #


def cor_falcon_tube_14mL_Rb(name: str) -> Tube:
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
    model=cor_falcon_tube_14mL_Rb.__name__,
    material_z_thickness=1.19,
    max_volume=14_000,  # units: ul
  )


# # # # # # # # # # cor_falcon_tube_15mL_Vb # # # # # # # # # #


def cor_falcon_tube_15mL_Vb(name: str) -> Tube:
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
    model=cor_falcon_tube_15mL_Vb.__name__,
    max_volume=15_000,
  )


# # # # # # # # # # cor_falcon_tube_50mL_Vb # # # # # # # # # #


def cor_falcon_tube_50mL_Vb(name: str) -> Tube:
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
    model=cor_falcon_tube_50mL_Vb.__name__,
    max_volume=50_000,
    material_z_thickness=1.2,
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def Cor_Falcon_tube_14mL_Rb(name: str) -> Tube:  # remove 2026-10
  """Deprecated alias for cor_falcon_tube_14mL_Rb().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `cor_falcon_tube_14mL_Rb()` instead.
  """
  warnings.warn(
    "Cor_Falcon_tube_14mL_Rb() is deprecated and will be removed after 2026-10. "
    "Use cor_falcon_tube_14mL_Rb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cor_falcon_tube_14mL_Rb(name)


def Cor_Falcon_tube_15mL_Vb(name: str) -> Tube:  # remove 2026-10
  """Deprecated alias for cor_falcon_tube_15mL_Vb().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `cor_falcon_tube_15mL_Vb()` instead.
  """
  warnings.warn(
    "Cor_Falcon_tube_15mL_Vb() is deprecated and will be removed after 2026-10. "
    "Use cor_falcon_tube_15mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cor_falcon_tube_15mL_Vb(name)


def Cor_Falcon_tube_50mL_Vb(name: str) -> Tube:  # remove 2026-10
  """Deprecated alias for cor_falcon_tube_50mL_Vb().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `cor_falcon_tube_50mL_Vb()` instead.
  """
  warnings.warn(
    "Cor_Falcon_tube_50mL_Vb() is deprecated and will be removed after 2026-10. "
    "Use cor_falcon_tube_50mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cor_falcon_tube_50mL_Vb(name)
