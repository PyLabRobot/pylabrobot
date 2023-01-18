from pylabrobot.resources.trough import Trough


def VWRReagentReservoirs25mL(name: str, volume: float) -> Trough:
  return Trough(
    name=name,
    size_x=44,
    size_y=127,
    size_z=25,
    max_volume=25000,
    volume=volume,
    model="VWR Reagent Reservoirs 25mL"
  )
