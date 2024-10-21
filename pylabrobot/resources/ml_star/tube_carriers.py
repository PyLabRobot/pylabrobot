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
        Coordinate(3.0, 9.0+x*20, 10.0 + 1.2) for x in range(24)
      ], # TODO: +1.2 to account for the Tube.material_z_thickness, fix container
      site_size_x=18.0,
      site_size_y=18.0,
    ),
    model="Tube_CAR_24_A00"
  )

def Tube_CAR_32_A00(name: str) -> TubeCarrier:
  """Hamilton cat. no.: 173410
  Hamilton name: 'SMP_CAR_32_A00'.
  'Sample' carrier for 32 tubes
  1 track(T) wide.
  """
  return TubeCarrier(
    name=name,
    size_x=22.5,
    size_y=497.0,
    size_z=71.5,
    sites=create_homogeneous_carrier_sites(
      klass=CarrierSite,
      locations=[
        Coordinate(5, 6.5 + x * 15, 24.0 + 2.8) for x in range(32)
      ], # TODO: +2.8 to account for the Tube.material_z_thickness of a 1.5ml eppendorf tube,
      # should fix container
      site_size_x=13.0,
      site_size_y=13.0,
    ),
    model="Tube_CAR_32_A00",
  )
