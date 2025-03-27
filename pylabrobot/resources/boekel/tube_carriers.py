from pylabrobot.resources.carrier import (
  ResourceHolder,
  TubeCarrier,
  create_resources,
)
from pylabrobot.resources.coordinate import Coordinate


def boekel_50mL_falcon_carrier(name: str) -> TubeCarrier:
  """50 mL Falcon tube carrier orientation.

  https://www.boekelsci.com/multi-tube-rack-for-50ml-conical-15ml-conical-and-
  microcentrifuge-tubes-pn-120008.html.

  Oriented in landscape mode with the male connector to the right.

  Sizes from website; carrier site locations measured with ruler.
  """

  return TubeCarrier(
    name=name,
    size_x=174,
    size_y=52,
    size_z=95,
    sites=create_resources(
      klass=ResourceHolder,
      locations=[Coordinate(x=x, y=11, z=5) for x in [11, 46, 91, 127]],
      resource_size_x=[30] * 4,
      resource_size_y=[30] * 4,
    ),
    model="Boekel Scientific Tube Carrier",
  )


def boekel_15mL_falcon_carrier(name: str) -> TubeCarrier:
  """15 mL Falcon tube carrier orientation.

  https://www.boekelsci.com/multi-tube-rack-for-50ml-conical-15ml-conical-and-
  microcentrifuge-tubes-pn-120008.html

  Oriented in landscape mode with the male connector to the right.

  Sizes from website; carrier site locations measured with ruler.
  """

  return TubeCarrier(
    name=name,
    size_x=174,
    size_y=52,
    size_z=95,
    sites=create_resources(
      klass=ResourceHolder,
      locations=[Coordinate(x=x, y=27, z=5) for x in [5, 34, 63, 88, 118, 147]]
      + [Coordinate(x=x, y=4.5, z=5) for x in [5, 34, 63, 88, 118, 147]],
      resource_size_x=[17] * 16,
      resource_size_y=[17] * 16,
    ),
    model="Boekel Scientific Tube Carrier",
  )


def boekel_1_5mL_microcentrifuge_carrier(name: str) -> TubeCarrier:
  """1.5 mL microcentrifuge tube carrier orientation.

  https://www.boekelsci.com/multi-tube-rack-for-50ml-conical-15ml-conical-and-
  microcentrifuge-tubes-pn-120008.html

  Oriented in landscape mode with the male connector to the right.

  Sizes from website; carrier site locations measured with ruler.
  """

  x_locs = [4, 25, 46, 67, 88, 109, 131, 152]

  return TubeCarrier(
    name=name,
    size_x=174,
    size_y=52,
    size_z=95,
    sites=create_resources(
      klass=ResourceHolder,
      locations=[Coordinate(x=x, y=57, z=5) for x in x_locs]
      + [Coordinate(x=x, y=48, z=5) for x in x_locs]
      + [Coordinate(x=x, y=39, z=5) for x in x_locs]
      + [Coordinate(x=x, y=10, z=5) for x in x_locs],
      resource_size_x=[13] * 32,
      resource_size_y=[13] * 32,
    ),
    model="Boekel Scientific Tube Carrier",
  )


def boekel_mini_microcentrifuge_carrier(name: str) -> TubeCarrier:
  """The tiniest microcentrifuge tube carrier orientation.

  https://www.boekelsci.com/multi-tube-rack-for-50ml-conical-15ml-conical-and-
  microcentrifuge-tubes-pn-120008.html

  Oriented in landscape mode with the male connector to the right.

  Sizes from website; carrier site locations measured with ruler.
  """

  x_locs = [5, 27, 48, 70, 91, 113, 134, 154]

  return TubeCarrier(
    name=name,
    size_x=174,
    size_y=52,
    size_z=95,
    sites=create_resources(
      klass=ResourceHolder,
      locations=[Coordinate(x=x, y=68.5, z=5) for x in x_locs]
      + [Coordinate(x=x, y=50, z=5) for x in x_locs]
      + [Coordinate(x=x, y=31, z=5) for x in x_locs]
      + [Coordinate(x=x, y=12, z=5) for x in x_locs],
      resource_size_x=[9] * 32,
      resource_size_y=[9] * 32,
    ),
    model="Boekel Scientific Tube Carrier",
  )
