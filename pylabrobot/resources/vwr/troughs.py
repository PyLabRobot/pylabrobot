from pylabrobot.resources.trough import Trough


def VWRReagentReservoirs25mL(name: str) -> Trough:
  """ Catalog number 89094-662
  https://us.vwr.com/store/product/4694822/vwr-disposable-pipetting-reservoirs
  """
  return Trough(
    name=name,
    size_x=44,
    size_y=127,
    size_z=25,
    max_volume=25000,
    model="VWR Reagent Reservoirs 25mL"
  )
