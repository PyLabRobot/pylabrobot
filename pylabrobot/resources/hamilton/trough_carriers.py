from pylabrobot.resources.carrier import (
  Coordinate,
  ResourceHolder,
  TroughCarrier,
  create_homogeneous_resources,
)


def Trough_CAR_4R200_A00(name: str) -> TroughCarrier:
  """Hamilton cat. no.: 185436
  Hamilton name: 'RGT_CAR_4R200_A00'.
  Trough carrier for 4x 200ml troughs. 2 tracks(T) wide.
  - Material: ? (recognisable via cLLD)
  - Sterilization_compatibility: ?

  carrier_site_pedestal_top = 134
  carrier_site_pedestal_bottom = 132.5
  pedestal_z_height = 1.5
  true_dz = 1.2
  trough_z_thickness = 1.4
  """
  return TroughCarrier(
    name=name,
    size_x=45.0,
    size_y=497.0,
    size_z=71.5,
    # pedestal_size_z=1.5 mm
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(4.0, 2.0, 34.0 + 1.5),
        Coordinate(4.0, 123.0, 34.0 + 1.5),  # TODO: properly define troughs to remove dependency
        Coordinate(4.0, 245.0, 34.0 + 1.5),  # on this 1.5mm offset (material_z_thickness)
        Coordinate(4.0, 366.0, 34.0 + 1.5),
      ],
      resource_size_x=37.0,
      resource_size_y=118.0,
      name_prefix=name,
    ),
    model="Trough_CAR_4R200_A00",
  )


def Trough_CAR_5R60_A00(name: str) -> TroughCarrier:
  """Hamilton cat. no.: 53646-01
  Hamilton name: 'RGT_CAR5X60'.
  Trough carrier for 5x 60ml troughs. 1 tracks(T) wide.
  Carries hamilton_1_trough_60ml_Vb
  """
  return TroughCarrier(
    name=name,
    size_x=22.5,  # standard
    size_y=497.0,  # standard
    size_z=104,  # measured
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(1.5, 7.0, 62.0 + 1.5),
        Coordinate(1.5, 103.0, 62.0 + 1.5),
        Coordinate(1.5, 199.0, 62.0 + 1.5),
        Coordinate(1.5, 302.0, 62.0 + 1.5),
      ],  # measured 62 to bottom of holder, but there is a 1.5mm pedestal
      resource_size_x=19.0,
      resource_size_y=90.0,
      # pedestal_size_z=1.5, # measured
      name_prefix=name,
    ),
    model=Trough_CAR_5R60_A00.__name__,
  )
