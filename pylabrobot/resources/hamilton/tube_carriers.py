import warnings

from pylabrobot.resources.carrier import (
  Coordinate,
  ResourceHolder,
  TubeCarrier,
  create_homogeneous_resources,
)


def Tube_CAR_24_A00(name: str) -> TubeCarrier:
  """Hamilton cat. no.: 173400
  Hamilton name: 'SMP_CAR_24_A00'.
  'Sample' carrier for 24 tubes sizes 14.5x60 - 18x120mm.
  1 track(T) wide.
  """
  return TubeCarrier(
    name=name,
    size_x=22.5,
    size_y=497.0,
    size_z=71.5,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(3.0, 9.0 + x * 20, 10.0 + 1.2) for x in range(24)
      ],  # TODO: +1.2 to account for the Tube.material_z_thickness, fix container
      resource_size_x=18.0,
      resource_size_y=18.0,
      name_prefix=name,
    ),
    model="Tube_CAR_24_A00",
  )


def Tube_CAR_32_A00(name: str) -> TubeCarrier:
  warnings.warn(
    "The true dimensions of a Tube_CAR_32_A00 (SMP_CAR_32_A00) are not known. "
    "The hamilton definitions are with inserts. "
    "Do you want to use `hamilton_tube_carrier_32_a00_insert_eppendorf_1_5mL` instead?",
    DeprecationWarning,
  )
  return hamilton_tube_carrier_32_a00_insert_eppendorf_1_5mL(name)


def hamilton_tube_carrier_32_a00_insert_eppendorf_1_5mL(name: str) -> TubeCarrier:
  """Hamilton cat. no.: 173410 with inserts cat no. 187350.
  Hamilton name: 'SMP_CAR_32_A00'.
  'Sample' carrier for 32 tubes
  1 track(T) wide.
  For use with `Eppendorf_DNA_LoBind_1_5ml_Vb` and `Eppendorf_Protein_LoBind_1_5ml_Vb`.
  """
  hole_diameter = 10.8
  return TubeCarrier(
    name=name,
    size_x=22.5,  # 1 track
    size_y=497.0,  # standard
    size_z=60.2,  # caliper
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(14.5 - hole_diameter / 2, 14.5 - hole_diameter / 2 + x * 15, 25.88)
        for x in range(
          32
        )  # SMP_CAR_32_A00 in venus, verified with caliper, custom z from z probing
      ],
      # should fix container
      resource_size_x=hole_diameter,  # venus
      resource_size_y=hole_diameter,  # venus
      name_prefix=name,
    ),
    model=hamilton_tube_carrier_32_a00_insert_eppendorf_1_5mL.__name__,
  )


def hamilton_tube_carrier_12_b00(name: str) -> TubeCarrier:
  """Hamilton cat. no.: 182045
  Hamilton name: 'SMP_CAR_12_B00'.
  'Sample' carrier for 12 50mL falcon tubes (Cor_Falcon_tube_50mL_Vb).
  2 track(T) wide.
  """
  hole_diameter = 29.0
  return TubeCarrier(
    name=name,
    size_x=45,  # 2 tracks
    size_y=497.0,  # standard
    size_z=92.0,  # caliper
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(26.05 - hole_diameter / 2, 41.74 - hole_diameter / 2 + i * 36.5, 18.9)
        for i in range(12)  # SMP_CAR_12_A00 in venus, verified with caliper, custom z
      ],
      resource_size_x=hole_diameter,
      resource_size_y=hole_diameter,
      name_prefix=name,
    ),
    model=hamilton_tube_carrier_12_b00.__name__,
  )
