# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from pylabrobot.resources.carrier import (
  Coordinate,
  CarrierSite,
  TubeCarrier,
  create_homogeneous_carrier_sites
)


def Tube_CAR_24_A00(name: str) -> TubeCarrier:
  """ Hamilton cat. no.: 173400
  Hamilton name: 'SMP_CAR_24_A00'.
  'Sample' carrier for 24 tubes sizes 14.5x60 – 18x120mm.
  1 track(T) wide.
  """
  return TubeCarrier(
    name=name,
    size_x=22.5,
    size_y=497.0,
    size_z=71.5,
    sites=create_homogeneous_carrier_sites(klass=CarrierSite, locations=[
        Coordinate(4.0, 2.0+x*22, 5.0) for x in range(24)
      ],
      site_size_x=22.0,
      site_size_y=22.0,
    ),
    model="Tube_CAR_24_A00"
  )
