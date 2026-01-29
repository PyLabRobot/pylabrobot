"""ML Star tip carriers"""

from pylabrobot.resources.carrier import (
  ResourceHolder,
  TipCarrier,
  create_homogeneous_resources,
)
from pylabrobot.resources.coordinate import Coordinate


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
      klass=ResourceHolder,
      locations=[
        Coordinate(26.3, 36.3, 114.9),
        Coordinate(26.3, 182.213, 114.9),
        Coordinate(26.3, 328.213, 114.9),
      ],
      resource_size_x=82.6,
      resource_size_y=122.4,
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
      klass=ResourceHolder,
      locations=[
        Coordinate(17.1, 36.25, 115.15),
        Coordinate(17.1, 182.25, 115.15),
        Coordinate(17.1, 328.25, 115.15),
      ],
      resource_size_x=82.6,
      resource_size_y=122.4,
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


def TIP_CAR_NTR_A00(name: str) -> TipCarrier:
  """Carrier with 5 nestable tip rack positions"""
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_resources(
      klass=ResourceHolder,
      locations=[
        Coordinate(6.2, 10.0, 29.0),
        Coordinate(6.2, 106.0, 29.0),
        Coordinate(6.2, 202.0, 29.0),
        Coordinate(6.2, 298.0, 29.0),
        Coordinate(6.2, 394.0, 29.0),
      ],
      resource_size_x=122.4,
      resource_size_y=82.6,
      name_prefix=name,
    ),
    model="TIP_CAR_NTR_A00",
  )
