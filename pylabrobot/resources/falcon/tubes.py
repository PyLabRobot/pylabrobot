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
    max_volume=50_000
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
