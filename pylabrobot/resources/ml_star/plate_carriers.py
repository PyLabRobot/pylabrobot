""" ML Star plate carriers """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from pylabrobot.resources.carrier import PlateCarrier, Coordinate, create_homogeneous_carrier_sites


def PLT_CAR_L4HD(name: str) -> PlateCarrier:
  """  """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(4.1, 36.1, 118.25),
        Coordinate(4.1, 146.1, 118.25),
        Coordinate(4.1, 256.1, 118.25),
        Coordinate(4.1, 366.1, 118.25)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L4HD"
  )


def PLT_CAR_L5AC(name: str) -> PlateCarrier:
  """  """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(4.0, 8.5, 86.15),
        Coordinate(4.0, 104.5, 86.15),
        Coordinate(4.0, 200.5, 86.15),
        Coordinate(4.0, 296.5, 86.15),
        Coordinate(4.0, 392.5, 86.15)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5AC"
  )


def PLT_CAR_L5AC_A00(name: str) -> PlateCarrier:
  """ Carrier for 5 deep well 96 Well PCR Plates """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(4.0, 8.5, 86.15),
        Coordinate(4.0, 104.5, 86.15),
        Coordinate(4.0, 200.5, 86.15),
        Coordinate(4.0, 296.5, 86.15),
        Coordinate(4.0, 392.5, 86.15)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5AC_A00"
  )


def PLT_CAR_L5FLEX_AC(name: str) -> PlateCarrier:
  """ Plate carrier with 5 adjustable (height) portrait positions for archive plates """
  return PlateCarrier(
    name=name,
    size_x=157.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(15.25, 8.5, 89.1),
        Coordinate(15.25, 104.5, 89.1),
        Coordinate(15.25, 200.5, 89.1),
        Coordinate(15.25, 296.5, 89.1),
        Coordinate(15.25, 392.5, 89.1)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5FLEX_AC"
  )


def PLT_CAR_L5FLEX_AC_A00(name: str) -> PlateCarrier:
  """ Plate carrier with 5 adjustable (height) portrait positions for archive plates """
  return PlateCarrier(
    name=name,
    size_x=157.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(15.25, 8.5, 89.1),
        Coordinate(15.25, 104.5, 89.1),
        Coordinate(15.25, 200.5, 89.1),
        Coordinate(15.25, 296.5, 89.1),
        Coordinate(15.25, 392.5, 89.1)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5FLEX_AC_A00"
  )


def PLT_CAR_L5FLEX_MD(name: str) -> PlateCarrier:
  """ Plate carrier with 5 adjustable (height) positions for MTP """
  return PlateCarrier(
    name=name,
    size_x=157.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(15.25, 8.5, 115.8),
        Coordinate(15.25, 104.5, 115.8),
        Coordinate(15.25, 200.5, 115.8),
        Coordinate(15.25, 296.5, 115.8),
        Coordinate(15.25, 392.5, 115.8)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5FLEX_MD"
  )


def PLT_CAR_L5FLEX_MD_A00(name: str) -> PlateCarrier:
  """ Plate carrier with 5 adjustable (height) positions for MTP """
  return PlateCarrier(
    name=name,
    size_x=157.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(15.25, 8.5, 115.8),
        Coordinate(15.25, 104.5, 115.8),
        Coordinate(15.25, 200.5, 115.8),
        Coordinate(15.25, 296.5, 115.8),
        Coordinate(15.25, 392.5, 115.8)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5FLEX_MD_A00"
  )


def PLT_CAR_L5MD(name: str) -> PlateCarrier:
  """  """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(4.0, 8.5, 111.75),
        Coordinate(4.0, 104.5, 111.75),
        Coordinate(4.0, 200.5, 111.75),
        Coordinate(4.0, 296.5, 111.75),
        Coordinate(4.0, 392.5, 111.75)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5MD"
  )


def PLT_CAR_L5MD_A00(name: str) -> PlateCarrier:
  """ Carrier for 5 96/384-Well Plates """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(4.0, 8.5, 111.75),
        Coordinate(4.0, 104.5, 111.75),
        Coordinate(4.0, 200.5, 111.75),
        Coordinate(4.0, 296.5, 111.75),
        Coordinate(4.0, 392.5, 111.75)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5MD_A00"
  )


def PLT_CAR_L5PCR(name: str) -> PlateCarrier:
  """  """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(4.0, 8.5, 107.5),
        Coordinate(4.0, 104.5, 107.5),
        Coordinate(4.0, 200.5, 107.5),
        Coordinate(4.0, 296.5, 107.5),
        Coordinate(4.0, 392.5, 107.5)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5PCR"
  )


def PLT_CAR_L5PCR_A00(name: str) -> PlateCarrier:
  """ Plate carrier for 5 PCR landscape plates [revision 00] """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(5.0, 9.5, 109.2),
        Coordinate(5.0, 105.5, 109.2),
        Coordinate(5.0, 201.5, 109.2),
        Coordinate(5.0, 297.5, 109.2),
        Coordinate(5.0, 393.5, 109.2)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5PCR_A00"
  )


def PLT_CAR_L5PCR_A01(name: str) -> PlateCarrier:
  """ Plate carrier for 5 PCR landscape plates [revision 01] """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(5.0, 9.5, 109.2),
        Coordinate(5.0, 105.5, 109.2),
        Coordinate(5.0, 201.5, 109.2),
        Coordinate(5.0, 297.5, 109.2),
        Coordinate(5.0, 393.5, 109.2)
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5PCR_A01"
  )


def PLT_CAR_P3AC_A00(name: str) -> PlateCarrier:
  """ Carrier for 3 96 Deep Well Plates (portrait) """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(43.85, 37.5, 86.15),
        Coordinate(43.85, 183.5, 86.15),
        Coordinate(43.85, 329.5, 86.15)
      ],
      site_size_x=86.0,
      site_size_y=127.0,
    ),
    model="PLT_CAR_P3AC_A00"
  )


def PLT_CAR_P3AC_A01(name: str) -> PlateCarrier:
  """ Carrier for 3 96 Deep Well Plates (portrait) """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(44.1, 37.5, 85.9),
        Coordinate(44.1, 183.5, 85.9),
        Coordinate(44.1, 329.5, 85.9)
      ],
      site_size_x=86.0,
      site_size_y=127.0,
    ),
    model="PLT_CAR_P3AC_A01"
  )


def PLT_CAR_P3HD(name: str) -> PlateCarrier:
  """  """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(43.9, 27.05, 117.65),
        Coordinate(43.9, 173.05, 117.65),
        Coordinate(43.9, 319.05, 117.65)
      ],
      site_size_x=86.0,
      site_size_y=127.0,
    ),
    model="PLT_CAR_P3HD"
  )


def PLT_CAR_P3MD(name: str) -> PlateCarrier:
  """  """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(44.1, 37.5, 111.5),
        Coordinate(44.1, 183.5, 111.5),
        Coordinate(44.1, 329.5, 111.5)
      ],
      site_size_x=86.0,
      site_size_y=127.0,
    ),
    model="PLT_CAR_P3MD"
  )


def PLT_CAR_P3MD_A00(name: str) -> PlateCarrier:
  """ Carrier for 3 96/384-Well Plates (Portrait) """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(44.1, 37.5, 111.5),
        Coordinate(44.1, 183.5, 111.5),
        Coordinate(44.1, 329.5, 111.5)
      ],
      site_size_x=86.0,
      site_size_y=127.0,
    ),
    model="PLT_CAR_P3MD_A00"
  )


def PLT_CAR_P3MD_A01(name: str) -> PlateCarrier:
  """ Carrier for 3 96/384-Well Plates (Portrait) """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(44.1, 37.5, 111.5),
        Coordinate(44.1, 183.5, 111.5),
        Coordinate(44.1, 329.5, 111.5)
      ],
      site_size_x=86.0,
      site_size_y=127.0,
    ),
    model="PLT_CAR_P3MD_A01"
  )


def PLT_CAR_L5_DWP(name: str) -> PlateCarrier:
  """ Carrier with Molded Corner Locators for 5 Deep Well Plates (93522-01) """
  return PlateCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=100.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(4.1, 8.2, 82.1),
        Coordinate(4.1, 104.2, 82.1),
        Coordinate(4.1, 200.2, 82.1),
        Coordinate(4.1, 296.2, 82.1),
        Coordinate(4.1, 392.1, 82.1),
      ],
      site_size_x=127.0,
      site_size_y=86.0,
    ),
    model="PLT_CAR_L5_DWP"
  )
