""" Tecan plates

size_z are best approximations, see
https://forums.pylabrobot.org/t/pylabrobot-tecan-error-in-adding-labware-to-carrier/2987
"""

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from typing import Dict, Optional
from pylabrobot.resources.plate import Lid, Plate, Well
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.tecan.tecan_resource import TecanResource


class TecanPlate(Plate, TecanResource):
  """ Base class for Tecan plates. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    z_travel: float,
    z_start: float,
    z_dispense: float,
    z_max: float,
    area: float,
    ordered_items: Optional[Dict[str, Well]] = None,
    category: str = "tecan_plate",
    lid: Optional[Lid] = None,
    model: Optional[str] = None
  ):
    super().__init__(name, size_x, size_y, size_z, ordered_items=ordered_items,
      category=category, lid=lid, model=model)

    self.z_travel = z_travel
    self.z_start = z_start
    self.z_dispense = z_dispense
    self.z_max = z_max
    self.area = area



def Microplate_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=129.9,
  #   size_y=83.9,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Microplate_96_Well_Lid",
  # )


def Microplate_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  """ white: pn 30122300, black: pn 30122298, cell culture/clear: pn 30122304, cell culture/black with clear bottom: pn 30122306

  Coley

  .. code-block:: python

      return TecanPlate(
        name=name,
        size_x=129.9,
        size_y=83.9,
        size_z=5.6,
        with_lid=with_lid,
        lid_height=8,
        model="Microplate_96_Well",
        z_travel=1750.0,
        z_start=1800.0,
        z_dispense=1970.0,
        z_max=2026.0,
        area=33.2,
        ordered_items=create_ordered_items_2d(Well,
          num_items_x=12,
          num_items_y=8,
          dx=10.8,
          dy=6.2,
          dz=0.0,
          item_dx=9.0,
          item_dy=9.0,
          size_x=9.0,
          size_y=9.0,
          size_z=22.6,
        ),
      )
  """

  return TecanPlate(
    name=name,
    size_x=127.8,
    size_y=85.4,
    size_z=7.6,
    lid=Microplate_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Microplate_96_Well",
    z_travel=1900.0,
    z_start=1957.0,
    z_dispense=1975.0,
    z_max=2005.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=12.5,
      dy=7.6,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=22.6,
    ),
  )


def Microplate_portrait_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=85.4,
  #   size_y=127.8,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Microplate_portrait_96_Well_Lid",
  # )


def Microplate_portrait_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=85.4,
    size_y=127.8,
    size_z=11.0,
    lid=Microplate_portrait_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Microplate_portrait_96_Well",
    z_travel=1900.0,
    z_start=1940.0,
    z_dispense=1960.0,
    z_max=2050.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=8,
      num_items_y=12,
      dx=6.7,
      dy=9.9,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=11.0,
    ),
  )


def DeepWell_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.8,
  #   size_y=85.4,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="DeepWell_96_Well_Lid",
  # )


def DeepWell_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=127.8,
    size_y=85.4,
    size_z=39.0,
    lid=DeepWell_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="DeepWell_96_Well",
    z_travel=1590.0,
    z_start=1670.0,
    z_dispense=1690.0,
    z_max=2060.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.9,
      dy=6.7,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=39.0,
    ),
  )


def HalfDeepWell_384_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.7,
  #   size_y=85.5,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="HalfDeepWell_384_Well_Lid",
  # )


def HalfDeepWell_384_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=127.7,
    size_y=85.5,
    size_z=18.8,
    lid=HalfDeepWell_384_Well_Lid(name=name + "_lid") if with_lid else None,
    model="HalfDeepWell_384_Well",
    z_travel=1789.0,
    z_start=1869.0,
    z_dispense=1889.0,
    z_max=2057.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.85,
      dy=6.75,
      dz=0.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=18.8,
    ),
  )


def DeepWell_portait_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=85.4,
  #   size_y=127.8,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="DeepWell_portait_96_Well_Lid",
  # )


def DeepWell_portait_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=85.4,
    size_y=127.8,
    size_z=38.0,
    lid=DeepWell_portait_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="DeepWell_portait_96_Well",
    z_travel=1625.0,
    z_start=1670.0,
    z_dispense=1690.0,
    z_max=2050.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=8,
      num_items_y=12,
      dx=6.7,
      dy=9.9,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=38.0,
    ),
  )


def Plate_portrait_384_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=85.5,
  #   size_y=127.7,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Plate_portrait_384_Well_Lid",
  # )


def Plate_portrait_384_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=85.5,
    size_y=127.7,
    size_z=11.0,
    lid=Plate_portrait_384_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Plate_portrait_384_Well",
    z_travel=1900.0,
    z_start=1940.0,
    z_dispense=1960.0,
    z_max=2050.0,
    area=9.0,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=16,
      num_items_y=24,
      dx=6.75,
      dy=9.85,
      dz=0.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=11.0,
    ),
  )


def Macherey_Nagel_Plate_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=151.6,
  #   size_y=131.1,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Macherey_Nagel_Plate_96_Well_Lid",
  # )


def Macherey_Nagel_Plate_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=151.6,
    size_y=131.1,
    size_z=29.9,
    lid=Macherey_Nagel_Plate_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Macherey_Nagel_Plate_96_Well",
    z_travel=1514.0,
    z_start=1532.0,
    z_dispense=1578.0,
    z_max=1831.0,
    area=65.0,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=22.25,
      dy=29.75,
      dz=0.0,
      item_dx=8.9,
      item_dy=8.9,
      size_x=8.9,
      size_y=8.9,
      size_z=29.9,
    ),
  )


def Qiagen_Plate_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=151.7,
  #   size_y=132.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Qiagen_Plate_96_Well_Lid",
  # )


def Qiagen_Plate_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=151.7,
    size_y=132.0,
    size_z=26.6,
    lid=Qiagen_Plate_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Qiagen_Plate_96_Well",
    z_travel=1493.0,
    z_start=1541.0,
    z_dispense=1549.0,
    z_max=1807.0,
    area=60.8,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=22.25,
      dy=30.05,
      dz=0.0,
      item_dx=8.9,
      item_dy=8.9,
      size_x=8.9,
      size_y=8.9,
      size_z=26.6,
    ),
  )


def AB_Plate_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=130.9,
  #   size_y=128.8,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="AB_Plate_96_Well_Lid",
  # )


def AB_Plate_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=130.9,
    size_y=128.8,
    size_z=19.5,
    lid=AB_Plate_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="AB_Plate_96_Well",
    z_travel=1772.0,
    z_start=1822.0,
    z_dispense=1837.0,
    z_max=2017.0,
    area=26.4,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.4,
      dy=28.0,
      dz=-0.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=19.5,
    ),
  )


def PCR_Plate_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=128.0,
  #   size_y=83.2,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="PCR_Plate_96_Well_Lid",
  # )


def PCR_Plate_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=128.0,
    size_y=83.2,
    size_z=19.5,
    lid=PCR_Plate_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="PCR_Plate_96_Well",
    z_travel=1857.0,
    z_start=1900.0,
    z_dispense=1915.0,
    z_max=2095.0,
    area=28.3,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=5.3,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=19.5,
    ),
  )


def DeepWell_Greiner_1536_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.8,
  #   size_y=85.5,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="DeepWell_Greiner_1536_Well_Lid",
  # )


def DeepWell_Greiner_1536_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=127.8,
    size_y=85.5,
    size_z=8.6,
    lid=DeepWell_Greiner_1536_Well_Lid(name=name + "_lid") if with_lid else None,
    model="DeepWell_Greiner_1536_Well",
    z_travel=1946.0,
    z_start=1984.0,
    z_dispense=2004.0,
    z_max=2070.0,
    area=2.7,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=48,
      num_items_y=32,
      dx=9.85,
      dy=6.75,
      dz=0.0,
      item_dx=2.3,
      item_dy=2.3,
      size_x=2.3,
      size_y=2.3,
      size_z=8.6,
    ),
  )


def Hibase_Greiner_1536_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.8,
  #   size_y=85.5,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Hibase_Greiner_1536_Well_Lid",
  # )


def Hibase_Greiner_1536_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=127.8,
    size_y=85.5,
    size_z=5.4,
    lid=Hibase_Greiner_1536_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Hibase_Greiner_1536_Well",
    z_travel=1946.0,
    z_start=1984.0,
    z_dispense=2004.0,
    z_max=2038.0,
    area=2.5,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=48,
      num_items_y=32,
      dx=9.85,
      dy=6.75,
      dz=0.0,
      item_dx=2.3,
      item_dy=2.3,
      size_x=2.3,
      size_y=2.3,
      size_z=5.4,
    ),
  )


def Lowbase_Greiner_1536_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.8,
  #   size_y=85.5,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Lowbase_Greiner_1536_Well_Lid",
  # )


def Lowbase_Greiner_1536_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=127.8,
    size_y=85.5,
    size_z=6.2,
    lid=Lowbase_Greiner_1536_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Lowbase_Greiner_1536_Well",
    z_travel=1946.0,
    z_start=2024.0,
    z_dispense=2034.0,
    z_max=2086.0,
    area=2.7,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=48,
      num_items_y=32,
      dx=9.85,
      dy=6.75,
      dz=0.0,
      item_dx=2.3,
      item_dy=2.3,
      size_x=2.3,
      size_y=2.3,
      size_z=6.2,
    ),
  )


def Separation_Plate_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=151.7,
  #   size_y=132.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Separation_Plate_96_Well_Lid",
  # )


def Separation_Plate_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=151.7,
    size_y=132.0,
    size_z=26.6,
    lid=Separation_Plate_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Separation_Plate_96_Well",
    z_travel=1493.0,
    z_start=1541.0,
    z_dispense=1549.0,
    z_max=1807.0,
    area=60.8,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=22.25,
      dy=30.05,
      dz=0.0,
      item_dx=8.9,
      item_dy=8.9,
      size_x=8.9,
      size_y=8.9,
      size_z=26.6,
    ),
  )


def DeepWell_square_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.8,
  #   size_y=85.4,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="DeepWell_square_96_Well_Lid",
  # )


def DeepWell_square_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=127.8,
    size_y=85.4,
    size_z=39.0,
    lid=DeepWell_square_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="DeepWell_square_96_Well",
    z_travel=1590.0,
    z_start=1670.0,
    z_dispense=1690.0,
    z_max=2060.0,
    area=64.0,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.9,
      dy=6.7,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=39.0,
    ),
  )


def CaCo2_Plate_24_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=125.2,
  #   size_y=89.2,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="CaCo2_Plate_24_Well_Lid",
  # )


def CaCo2_Plate_24_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=125.2,
    size_y=89.2,
    size_z=6.5,
    lid=CaCo2_Plate_24_Well_Lid(name=name + "_lid") if with_lid else None,
    model="CaCo2_Plate_24_Well",
    z_travel=1774.0,
    z_start=1960.0,
    z_dispense=2007.0,
    z_max=2025.0,
    area=50.3,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=6,
      num_items_y=4,
      dx=4.75,
      dy=5.95,
      dz=0.0,
      item_dx=19.3,
      item_dy=19.3,
      size_x=19.3,
      size_y=19.3,
      size_z=6.5,
    ),
  )


def Plate_384_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.7,
  #   size_y=85.5,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Plate_384_Well_Lid",
  # )


def Plate_384_Well(name: str, with_lid: bool = False) -> TecanPlate:
  """ white: pn 30122301, black: pn 30122299, cell culture/clear: pn 30122305, cell culture/black with clear bottom: pn 30122307 """
  return TecanPlate(
    name=name,
    size_x=127.7,
    size_y=85.5,
    size_z=12.1,
    lid=Plate_384_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Plate_384_Well",
    z_travel=1900.0,
    z_start=1940.0,
    z_dispense=1960.0,
    z_max=2061.0,
    area=13.7,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.85,
      dy=6.75,
      dz=0.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=12.1,
    ),
  )


def Microplate_24_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=130.9,
  #   size_y=85.5,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Microplate_24_Well_Lid",
  # )


def Microplate_24_Well(name: str, with_lid: bool = False) -> TecanPlate:
  """ cell culture/clear: pn 30122302 """
  return TecanPlate(
    name=name,
    size_x=130.9,
    size_y=85.5,
    size_z=17.6,
    lid=Microplate_24_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Microplate_24_Well",
    z_travel=1841.0,
    z_start=1885.0,
    z_dispense=1917.0,
    z_max=2061.0,
    area=193.6,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=6,
      num_items_y=4,
      dx=6.6,
      dy=3.5,
      dz=0.0,
      item_dx=19.6,
      item_dy=19.6,
      size_x=19.6,
      size_y=19.6,
      size_z=17.6,
    ),
  )


def TecanExtractionPlate_96_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=129.8,
  #   size_y=91.7,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="TecanExtractionPlate_96_Well_Lid",
  # )


def TecanExtractionPlate_96_Well(name: str, with_lid: bool = False) -> TecanPlate:
  return TecanPlate(
    name=name,
    size_x=129.8,
    size_y=91.7,
    size_z=23.0,
    lid=TecanExtractionPlate_96_Well_Lid(name=name + "_lid") if with_lid else None,
    model="TecanExtractionPlate_96_Well",
    z_travel=1793.0,
    z_start=1831.0,
    z_dispense=1910.0,
    z_max=2061.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.9,
      dy=9.7,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=23.0,
    ),
  )


def Microplate_48_Well_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=131.1,
  #   size_y=85.3,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Microplate_48_Well_Lid",
  # )


def Microplate_48_Well(name: str, with_lid: bool = False) -> TecanPlate:
  """ cell culture/clear: pn 30122303 """
  return TecanPlate(
    name=name,
    size_x=131.1,
    size_y=85.3,
    size_z=17.3,
    lid=Microplate_48_Well_Lid(name=name + "_lid") if with_lid else None,
    model="Microplate_48_Well",
    z_travel=1839.0,
    z_start=1887.0,
    z_dispense=1921.0,
    z_max=2060.0,
    area=102.1,
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=8,
      num_items_y=6,
      dx=13.5,
      dy=3.8,
      dz=0.0,
      item_dx=13.0,
      item_dy=13.0,
      size_x=13.0,
      size_y=13.0,
      size_z=17.3,
    ),
  )
