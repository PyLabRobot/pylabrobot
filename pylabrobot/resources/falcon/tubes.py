from pylabrobot.resources.tube import Tube


def falcon_tube_50mL(name: str) -> Tube: # pylint: disable=invalid-name
  """ 50 mL Falcon tube.

  https://www.fishersci.com/shop/products/falcon-50ml-conical-centrifuge-tubes-2/1495949A
  """

  diameter = 30
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=115,
    model="Falcon 50mL",
    max_volume=50_000,
    material_z_thickness=1.2
  )


def falcon_tube_15mL(name: str) -> Tube: # pylint: disable=invalid-name
  """ 15 mL Falcon tube.

  https://www.fishersci.com/shop/products/falcon-15ml-conical-centrifuge-tubes-5/p-193301
  """

  diameter = 17
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=120,
    model="Falcon 15mL",
    max_volume=15_000
  )


def Falcon_tube_14mL_Rb(name: str) -> Tube:
  """ 14 mL round-bottom snap-cap Falcon tube. Corning cat. no.: 352059

  - Material: polypropylene
  - bottom_type=TubeBottomType.U
  - snap-cap lid
  """
  # material_z_thickness = 1.2 mm
  diameter = 17
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=95,
    model="Falcon_tube_14mL_Rb",
    max_volume=14_000 # units: ul
  )
