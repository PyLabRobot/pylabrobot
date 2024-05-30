""" Tecan plate carriers """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from typing import List, Optional
from pylabrobot.resources.carrier import (
  PlateCarrier,
  CarrierSite,
  Coordinate,
  create_homogeneous_carrier_sites
)
from pylabrobot.resources.tecan.tecan_resource import TecanResource


class TecanPlateCarrier(PlateCarrier, TecanResource):
  """ Base class for Tecan plate carriers. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    off_x: float,
    off_y: float,
    roma_x: Optional[float] = None,
    roma_y: Optional[float] = None,
    roma_z_safe: Optional[float] = None,
    roma_z_travel: Optional[float] = None,
    roma_z_end: Optional[float] = None,
    sites: Optional[List[CarrierSite]] = None,
    category="tecan_plate_carrier",
    model: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z,
      sites, category=category, model=model)

    self.off_x = off_x
    self.off_y = off_y
    self.roma_x = roma_x
    self.roma_y = roma_y
    self.roma_z_safe = roma_z_safe
    self.roma_z_travel = roma_z_travel
    self.roma_z_end = roma_z_end


def MP_2Pos_portrait_No_Robot_Access(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10613007 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=316.0,
    size_z=62.5,
    off_x=12.0,
    off_y=24.7,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(47.5, 8.8, 62.5),
        Coordinate(47.5, 172.3, 62.5),
      ],
      site_size_x=85.5,
      site_size_y=127.0,
    ),
    model="MP_2Pos_portrait_No_Robot_Access"
  )


def MP_2_Pos_portrait(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10612605 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=316.0,
    size_z=62.5,
    off_x=12.0,
    off_y=24.7,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(47.5, 34.3, 62.5),
        Coordinate(47.5, 172.3, 62.5),
      ],
      site_size_x=85.5,
      site_size_y=127.0,
    ),
    model="MP_2_Pos_portrait"
  )


def MP_3Pos_PCR(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10613034 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=316.0,
    size_z=62.5,
    off_x=12.0,
    off_y=24.7,
    roma_x=1876,
    roma_y=423,
    roma_z_safe=946,
    roma_z_travel=1938,
    roma_z_end=2566,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(5.5, 13.5, 62.5),
        Coordinate(5.5, 109.5, 62.5),
        Coordinate(5.5, 205.5, 62.5),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_3Pos_PCR"
  )


def MP_3Pos_TePS(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10643025 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=361.0,
    size_z=84.0,
    off_x=12.0,
    off_y=13.5,
    roma_x=1876,
    roma_y=405,
    roma_z_safe=780,
    roma_z_travel=2012,
    roma_z_end=2543,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(7.6, 38.0, 84.0),
        Coordinate(7.6, 151.5, 84.0),
        Coordinate(7.6, 265.0, 84.0),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_3Pos_TePS"
  )


def LI___MP_3Pos(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10650010 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=316.0,
    size_z=62.5,
    off_x=12.0,
    off_y=24.7,
    roma_x=1878,
    roma_y=423,
    roma_z_safe=946,
    roma_z_travel=1938,
    roma_z_end=2537,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(5.5, 13.5, 62.5),
        Coordinate(5.5, 109.5, 62.5),
        Coordinate(5.5, 205.5, 62.5),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="LI___MP_3Pos"
  )


def MP_4Pos_landscape(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 30013668 """
  return TecanPlateCarrier(
    name=name,
    size_x=143.0,
    size_y=420.0,
    size_z=83.0,
    off_x=7.5,
    off_y=70.0,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(10.2, 44.5, 83.0),
        Coordinate(10.2, 136.0, 83.0),
        Coordinate(10.2, 227.5, 83.0),
        Coordinate(10.2, 319.0, 83.0),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_4Pos_landscape"
  )


def MP_12Pos_landscape(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 30051762 """
  return TecanPlateCarrier(
    name=name,
    size_x=411.0,
    size_y=395.0,
    size_z=32.0,
    off_x=11.5,
    off_y=35.0,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(280.4, 16.8, 32.0),
        Coordinate(280.4, 113.7, 32.0),
        Coordinate(280.4, 209.9, 32.0),
        Coordinate(280.4, 306.5, 32.0),
        Coordinate(141.4, 16.8, 32.0),
        Coordinate(141.4, 113.7, 32.0),
        Coordinate(141.4, 209.9, 32.0),
        Coordinate(141.4, 306.5, 32.0),
        Coordinate(2.4, 16.8, 32.0),
        Coordinate(2.4, 113.7, 32.0),
        Coordinate(2.4, 209.9, 32.0),
        Coordinate(2.4, 306.5, 32.0),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_12Pos_landscape"
  )


def MP_8Pos_landscape(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 30054411 """
  return TecanPlateCarrier(
    name=name,
    size_x=274.0,
    size_y=395.0,
    size_z=32.0,
    off_x=11.5,
    off_y=35.0,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(141.4, 16.8, 32.0),
        Coordinate(141.4, 113.7, 32.0),
        Coordinate(141.4, 209.9, 32.0),
        Coordinate(141.4, 306.5, 32.0),
        Coordinate(2.4, 16.8, 32.0),
        Coordinate(2.4, 113.7, 32.0),
        Coordinate(2.4, 209.9, 32.0),
        Coordinate(2.4, 306.5, 32.0),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_8Pos_landscape"
  )


def MP_20Pos_landscape(name: str) -> TecanPlateCarrier:
  return TecanPlateCarrier(
    name=name,
    size_x=687.0,
    size_y=395.0,
    size_z=32.0,
    off_x=11.5,
    off_y=35.0,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(557.4, 16.8, 32.0),
        Coordinate(557.4, 113.7, 32.0),
        Coordinate(557.4, 209.9, 32.0),
        Coordinate(557.4, 306.5, 32.0),
        Coordinate(419.0, 16.8, 32.0),
        Coordinate(419.0, 113.7, 32.0),
        Coordinate(419.0, 209.9, 32.0),
        Coordinate(419.0, 306.5, 32.0),
        Coordinate(280.4, 16.8, 32.0),
        Coordinate(280.4, 113.7, 32.0),
        Coordinate(280.4, 209.9, 32.0),
        Coordinate(280.4, 306.5, 32.0),
        Coordinate(141.4, 16.8, 32.0),
        Coordinate(141.4, 113.7, 32.0),
        Coordinate(141.4, 209.9, 32.0),
        Coordinate(141.4, 306.5, 32.0),
        Coordinate(2.4, 16.8, 32.0),
        Coordinate(2.4, 113.7, 32.0),
        Coordinate(2.4, 209.9, 32.0),
        Coordinate(2.4, 306.5, 32.0),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_20Pos_landscape"
  )


def MP_16Pos_landscape(name: str) -> TecanPlateCarrier:
  return TecanPlateCarrier(
    name=name,
    size_x=548.0,
    size_y=395.0,
    size_z=32.0,
    off_x=11.5,
    off_y=35.0,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(419.0, 16.8, 32.0),
        Coordinate(419.0, 113.7, 32.0),
        Coordinate(419.0, 209.9, 32.0),
        Coordinate(419.0, 306.5, 32.0),
        Coordinate(280.4, 16.8, 32.0),
        Coordinate(280.4, 113.7, 32.0),
        Coordinate(280.4, 209.9, 32.0),
        Coordinate(280.4, 306.5, 32.0),
        Coordinate(141.4, 16.8, 32.0),
        Coordinate(141.4, 113.7, 32.0),
        Coordinate(141.4, 209.9, 32.0),
        Coordinate(141.4, 306.5, 32.0),
        Coordinate(2.4, 16.8, 32.0),
        Coordinate(2.4, 113.7, 32.0),
        Coordinate(2.4, 209.9, 32.0),
        Coordinate(2.4, 306.5, 32.0),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_16Pos_landscape"
  )


def MP_3Pos(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10612604 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=316.0,
    size_z=62.5,
    off_x=12.0,
    off_y=24.7,
    roma_x=1878,
    roma_y=423,
    roma_z_safe=946,
    roma_z_travel=1938,
    roma_z_end=2537,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(5.5, 13.5, 62.5),
        Coordinate(5.5, 109.5, 62.5),
        Coordinate(5.5, 205.5, 62.5),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_3Pos"
  )


def MP_3Pos_Cooled(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10613046 """
  return TecanPlateCarrier(
    name=name,
    size_x=163.0,
    size_y=340.0,
    size_z=54.0,
    off_x=12.0,
    off_y=24.7,
    roma_x=1810,
    roma_y=421,
    roma_z_safe=946,
    roma_z_travel=1853,
    roma_z_end=2534,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(17.0, 27.5, 54.0),
        Coordinate(17.0, 123.5, 54.0),
        Coordinate(17.0, 219.5, 54.0),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_3Pos_Cooled"
  )


def MP_3Pos_Fixed(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10613031 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=340.0,
    size_z=62.5,
    off_x=12.0,
    off_y=13.8,
    roma_x=1870,
    roma_y=420,
    roma_z_safe=946,
    roma_z_travel=1938,
    roma_z_end=2537,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(10.5, 47.6, 62.5),
        Coordinate(10.5, 143.6, 62.5),
        Coordinate(10.5, 239.6, 62.5),
      ],
      site_size_x=128.0,
      site_size_y=86.0,
    ),
    model="MP_3Pos_Fixed"
  )


def MP_3Pos_Flat(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10612624

  Coley:

  .. code-block:: python

      return TecanPlateCarrier(
        name=name,
        size_x=149.0,
        size_y=295.0,
        size_z=6.0,
        off_x=12.0,
        off_y=11.0,
        sites=create_homogeneous_carrier_sites(locations=[
            Coordinate(11.7, 10.5, 6.0),
            Coordinate(11.0, 106.4, 6.0),
            Coordinate(11.0, 202.8, 6.0),
          ],
          site_size_x=127.0,
          site_size_y=85.5,
        ),
        model="MP_3Pos_Flat"
      )
  """

  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=295.0,
    size_z=6.0,
    off_x=12.0,
    off_y=11.0,
    roma_x=2056,
    roma_y=441,
    roma_z_safe=610,
    roma_z_travel=2418,
    roma_z_end=2503,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(10.4, 11.5, 6.0),
        Coordinate(10.4, 107.5, 6.0),
        Coordinate(10.4, 203.5, 6.0),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_3Pos_Flat"
  )


def MP_3Pos_No_Robot_Access(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 10613006 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=316.0,
    size_z=62.5,
    off_x=12.0,
    off_y=24.7,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(5.5, 13.5, 62.5),
        Coordinate(5.5, 113.5, 62.5),
        Coordinate(5.5, 213.5, 62.5),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_3Pos_No_Robot_Access"
  )


def MP_4Pos(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 30013668 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=380.0,
    size_z=62.7,
    off_x=11.0,
    off_y=51.0,
    roma_x=1840,
    roma_y=386,
    roma_z_safe=946,
    roma_z_travel=1938,
    roma_z_end=2476,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(10.0, 3.5, 62.7),
        Coordinate(10.0, 99.5, 62.7),
        Coordinate(10.0, 195.5, 62.7),
        Coordinate(10.0, 291.5, 62.7),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_4Pos"
  )


def MP_4Pos_flat(name: str) -> TecanPlateCarrier:
  """ Tecan part no. 30013061 """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=380.0,
    size_z=6.9,
    off_x=11.0,
    off_y=51.0,
    roma_x=1835,
    roma_y=388,
    roma_z_safe=946,
    roma_z_travel=1938,
    roma_z_end=2475,
    sites=create_homogeneous_carrier_sites(locations=[
        Coordinate(10.0, 3.5, 6.9),
        Coordinate(10.0, 99.5, 6.9),
        Coordinate(10.0, 195.5, 6.9),
        Coordinate(10.0, 291.5, 6.9),
      ],
      site_size_x=127.0,
      site_size_y=85.5,
    ),
    model="MP_4Pos_flat"
  )
