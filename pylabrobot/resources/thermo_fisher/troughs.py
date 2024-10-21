from pylabrobot.resources.trough import Trough


def ThermoFisherMatrixTrough8094(name: str) -> Trough:
  """ Thermo Fisher Trough 8094 - 25mL
  https://www.thermofisher.com/order/catalog/product/8094
  """
  return Trough(
    name=name,
    size_x=147,
    size_y=58,
    size_z=27,
    max_volume=25000,
    model="Thermo Fisher 8094"
  )
