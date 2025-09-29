from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.temperature_controlling.inheco.control_box import InhecoTECControlBox
from pylabrobot.temperature_controlling.inheco.cpac_backend import InhecoCPACBackend
from pylabrobot.temperature_controlling.temperature_controller import TemperatureController


def inheco_cpac_ultraflat(
  name: str, control_box: InhecoTECControlBox, index: int
) -> TemperatureController:
  """Inheco CPAC Ultraflat
  7000166, 7000190, 7000165

  https://www.inheco.com/data/pdf/cpac-brochure-1013-1032-34.pdf
  """

  return TemperatureController(
    name=name,
    backend=InhecoCPACBackend(control_box=control_box, index=index),
    size_x=113,  # from spec
    size_y=89,  # from spec
    size_z=129,  # from spec
    child_location=Coordinate(x=8, y=11, z=77),  # x from spec, y and z measured
    model=inheco_cpac_ultraflat.__name__,
  )
