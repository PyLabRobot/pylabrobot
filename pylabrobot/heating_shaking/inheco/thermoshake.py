from pylabrobot.heating_shaking.heater_shaker import HeaterShaker
from pylabrobot.heating_shaking.inheco.thermoshake_backend import InhecoThermoshakeBackend
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.temperature_controlling.inheco.control_box import InhecoTECControlBox


def inheco_thermoshake_ac(name: str, control_box: InhecoTECControlBox, index: int) -> HeaterShaker:
  """Inheco Thermoshake AC

  7100160, 7100161

  https://www.inheco.com/thermoshake-ac.html
  """

  raise NotImplementedError("Inheco ThermoShake AC is missing child_location.")

  return HeaterShaker(
    name=name,
    backend=InhecoThermoshakeBackend(control_box=control_box, index=index),
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=115.9,  # from spec
    child_location=Coordinate(x=0, y=0, z=109.9),  # TODO
    model=inheco_thermoshake_ac.__name__,
  )


def inheco_thermoshake(name: str, control_box: InhecoTECControlBox, index: int) -> HeaterShaker:
  """Inheco Thermoshake (7100146)

  https://www.inheco.com/thermoshake-classic.html
  """

  return HeaterShaker(
    name=name,
    backend=InhecoThermoshakeBackend(control_box=control_box, index=index),
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=118,  # from spec
    child_location=Coordinate(x=9.62, y=9.22, z=109.9),  # measured
    model=inheco_thermoshake.__name__,
    # pedestal_size_z=-4.2,  # measured
  )


def inheco_thermoshake_rm(name: str, control_box: InhecoTECControlBox, index: int) -> HeaterShaker:
  """Inheco Thermoshake RM (7100144)

  https://www.inheco.com/thermoshake-classic.html
  """

  raise NotImplementedError("Inheco Thermoshake RM is missing child_location")

  return HeaterShaker(
    name=name,
    backend=InhecoThermoshakeBackend(control_box=control_box, index=index),
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=116,  # from spec
    child_location=Coordinate(x=0, y=0, z=0),  # TODO
    model=inheco_thermoshake.__name__,
  )
