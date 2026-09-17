"""ML Star tip carriers"""

import warnings

from pylabrobot.resources.carrier import (
  ResourceHolder,
  TipCarrier,
  create_homogeneous_resources,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.tip_rack_holder import EmbeddedTipRackHolder


def TIP_CAR_120BC_4mlTF_A00(name: str) -> TipCarrier:
  """Tip carrier with 5 4ml tip with filter racks landscape"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95),
      ],
      resource_size_x=122.4,
      resource_size_y=82.6,
      name_prefix=name,
    ),
    model="TIP_CAR_120BC_4mlTF_A00",
  )


def TIP_CAR_120BC_5mlT_A00(name: str) -> TipCarrier:
  """Tip carrier with 5 5ml tip racks landscape"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95),
      ],
      resource_size_x=122.4,
      resource_size_y=82.6,
      name_prefix=name,
    ),
    model="TIP_CAR_120BC_5mlT_A00",
  )


def TIP_CAR_288_A00(name: str) -> TipCarrier:
  """Carrier for 3 Racks with 96 Tips portrait  [revision A00]"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=EmbeddedTipRackHolder,
      locations=[
        Coordinate(30.6, 40.25, 114.9),
        Coordinate(30.6, 186.163, 114.9),
        Coordinate(30.6, 332.163, 114.9),
      ],
      resource_size_x=74.0,
      resource_size_y=114.5,
      name_prefix=name,
    ),
    model="TIP_CAR_288_A00",
  )


def TIP_CAR_288_B00(name: str) -> TipCarrier:
  """Carrier for 3 Racks with 96 Tips portrait [revision B00]"""
  return TipCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=EmbeddedTipRackHolder,
      locations=[
        Coordinate(21.4, 40.2, 115.15),
        Coordinate(21.4, 186.2, 115.15),
        Coordinate(21.4, 332.2, 115.15),
      ],
      resource_size_x=74.0,
      resource_size_y=114.5,
      name_prefix=name,
    ),
    model="TIP_CAR_288_B00",
  )


def TIP_CAR_288_C00(name: str) -> TipCarrier:
  """Carrier for 3 Racks with 96 Tips portrait [revision C00]"""
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=EmbeddedTipRackHolder,
      locations=[
        Coordinate(8.0, 40.25, 114.7),
        Coordinate(8.0, 186.25, 114.7),
        Coordinate(8.0, 332.25, 114.7),
      ],
      resource_size_x=74.0,
      resource_size_y=114.5,
      name_prefix=name,
    ),
    model="TIP_CAR_288_C00",
  )


def TIP_CAR_384BC_A00(name: str) -> TipCarrier:
  """Tip carrier with 4 empty tip rack positions landscape, with Barcode Identification  [revision A00]"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=EmbeddedTipRackHolder,
      locations=[
        Coordinate(10.25, 82.5, 114.8),
        Coordinate(10.25, 167.4, 114.8),
        Coordinate(10.25, 252.4, 114.8),
        Coordinate(10.25, 337.4, 114.8),
      ],
      resource_size_x=114.5,
      resource_size_y=74.0,
      name_prefix=name,
    ),
    model="TIP_CAR_384BC_A00",
  )


def TIP_CAR_384_A00(name: str) -> TipCarrier:
  """Carrier for 4 Racks with 96 Tips landscape [revision A00]"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=EmbeddedTipRackHolder,
      locations=[
        Coordinate(10.25, 82.5, 114.8),
        Coordinate(10.25, 167.4, 114.8),
        Coordinate(10.25, 252.4, 114.8),
        Coordinate(10.25, 337.4, 114.8),
      ],
      resource_size_x=114.5,
      resource_size_y=74.0,
      name_prefix=name,
    ),
    model="TIP_CAR_384_A00",
  )


def TIP_CAR_480(name: str) -> TipCarrier:
  """Carrier for 5 Racks with 96 Tips landscape [revision A00]"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=EmbeddedTipRackHolder,
      locations=[
        Coordinate(10.15, 14.3, 114.95),
        Coordinate(10.15, 110.3, 114.95),
        Coordinate(10.15, 206.3, 114.95),
        Coordinate(10.15, 302.3, 114.95),
        Coordinate(10.15, 398.3, 114.95),
      ],
      resource_size_x=114.5,
      resource_size_y=74.0,
      name_prefix=name,
    ),
    model="TIP_CAR_480",
  )


def TIP_CAR_480BC_A00(name: str) -> TipCarrier:
  """Tip carrier with 5 tip rack positions landscape"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=EmbeddedTipRackHolder,
      locations=[
        Coordinate(10.15, 14.3, 114.95),
        Coordinate(10.15, 110.3, 114.95),
        Coordinate(10.15, 206.3, 114.95),
        Coordinate(10.15, 302.3, 114.95),
        Coordinate(10.15, 398.3, 114.95),
      ],
      resource_size_x=114.5,
      resource_size_y=74.0,
      name_prefix=name,
    ),
    model="TIP_CAR_480BC_A00",
  )


def TIP_CAR_480_A00(name: str) -> TipCarrier:
  """Carrier for 5 Racks with 96 Tips landscape [revision A00]"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=EmbeddedTipRackHolder,
      locations=[
        Coordinate(10.15, 14.3, 114.95),
        Coordinate(10.15, 110.3, 114.95),
        Coordinate(10.15, 206.3, 114.95),
        Coordinate(10.15, 302.3, 114.95),
        Coordinate(10.15, 398.3, 114.95),
      ],
      resource_size_x=114.5,
      resource_size_y=74.0,
      name_prefix=name,
    ),
    model="TIP_CAR_480_A00",
  )


def TIP_CAR_72_4mlTF_C00(name: str) -> TipCarrier:
  """Tip carrier with 3 4ml tip with filter racks portrait  [revision C00]"""
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7),
      ],
      resource_size_x=82.6,
      resource_size_y=122.4,
      name_prefix=name,
    ),
    model="TIP_CAR_72_4mlTF_C00",
  )


def TIP_CAR_72_5mlT_C00(name: str) -> TipCarrier:
  """Tip carrier with 3 5ml tip racks portrait  [revision C00]"""
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7),
      ],
      resource_size_x=82.6,
      resource_size_y=122.4,
      name_prefix=name,
    ),
    model="TIP_CAR_72_5mlT_C00",
  )


def TIP_CAR_96BC_4mlTF_A00(name: str) -> TipCarrier:
  """Carrier for 4 4ml with filter tip racks landscape"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8),
      ],
      resource_size_x=122.4,
      resource_size_y=82.6,
      name_prefix=name,
    ),
    model="TIP_CAR_96BC_4mlTF_A00",
  )


def TIP_CAR_96BC_5mlT_A00(name: str) -> TipCarrier:
  """Carrier for 4 5ml tip racks landscape"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8),
      ],
      resource_size_x=122.4,
      resource_size_y=82.6,
      name_prefix=name,
    ),
    model="TIP_CAR_96BC_5mlT_A00",
  )


def hamilton_tip_carrier_L5_ntr_a00(name: str) -> TipCarrier:
  """Hamilton cat. no.: 182074
  Hamilton name: 'TIP_CAR_NTR_A00'.
  Carrier for 5 stacks of up to 4x 96 nested tip racks (NTR), landscape.
  6 track(T) wide.
  """
  site_z = 29.0
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    # 29 mm above its sites. Hamilton's TIP_CAR_NTR_A00 definition states 130 mm, which the carrier is not.
    size_z=site_z + 29.0,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(6.2, 10.0, site_z),
        Coordinate(6.2, 106.0, site_z),
        Coordinate(6.2, 202.0, site_z),
        Coordinate(6.2, 298.0, site_z),
        Coordinate(6.2, 394.0, site_z),
      ],
      resource_size_x=122.4,
      resource_size_y=82.6,
      # A nested tip rack's SLAS footprint, 127.76 x 85.48, stands centred on the site.
      child_location=Coordinate(x=(122.4 - 127.76) / 2, y=(82.6 - 85.48) / 2, z=0),
      name_prefix=name,
    ),
    model=hamilton_tip_carrier_L5_ntr_a00.__name__,
  )


def hamilton_prep_ftr_pedestal(name: str) -> TipCarrier:
  """Hamilton cat. no.: 6600553-01
  Pedestal for elevating fixed tip racks (FTR) on the MicroLab Prep.
  Body: 133.11 x 89.96 x 53.37 mm. FTR rack seats on top of the pedestal.
  """
  site = ResourceHolder(name=f"{name}-0", size_x=122.4, size_y=82.6, size_z=0)
  site.location = Coordinate(1.5, 1, 53.37)
  return TipCarrier(
    name=name,
    size_x=133.11,
    size_y=89.96,
    size_z=53.37,
    sites={0: site},
    model="hamilton_prep_ftr_pedestal",
  )


# Deprecated names for backwards compatibility


def TIP_CAR_NTR_A00(name: str) -> TipCarrier:
  """Deprecated alias for `hamilton_tip_carrier_L5_ntr_a00`."""
  warnings.warn(
    "TIP_CAR_NTR_A00 is deprecated. Use 'hamilton_tip_carrier_L5_ntr_a00' instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_carrier_L5_ntr_a00(name)
