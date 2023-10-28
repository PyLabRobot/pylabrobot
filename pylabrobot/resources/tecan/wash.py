""" Tecan wash station """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from typing import List, Optional
from pylabrobot.resources.carrier import Carrier, CarrierSite, create_carrier_sites
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.tecan.tecan_resource import TecanResource


class TecanWashStation(Carrier, TecanResource):
  """ Base class for Tecan tip carriers. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    off_x: float,
    off_y: float,
    sites: Optional[List[CarrierSite]] = None,
    category="tecan_wash_station",
    model: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z,
      sites, category=category, model=model)

    self.off_x: float = off_x
    self.off_y: float = off_y


def Wash_Station(name: str) -> TecanWashStation:
  """ Tecan part no. 10613001 """
  return TecanWashStation(
    name=name,
    size_x=25.0,
    size_y=390.0,
    size_z=0.0,
    off_x=12.5,
    off_y=24.7,
    sites=create_carrier_sites(locations = [
        Coordinate(12.2, 106.7, 0.0),
        Coordinate(11.0, 180.7, 0.0),
        Coordinate(12.2, 281.7, 0.0),
      ], site_size_x=[
        12.0,
        12.0,
        12.0,
      ], site_size_y=[
        73.0,
        100.0,
        73.0,
    ]),
    model="Wash_Station"
  )


def Wash_Station_Waste(name: str) -> Trash:
  return Trash(name=name, size_x=12.0, size_y=100.0, size_z=140.0)


def Wash_Station_Cleaner_shallow(name: str) -> Trash:
  return Trash(name=name, size_x=12.0, size_y=73.0, size_z=140.0)


def Wash_Station_Cleaner_deep(name: str) -> Trash:
  return Trash(name=name, size_x=12.0, size_y=73.0, size_z=140.0)
