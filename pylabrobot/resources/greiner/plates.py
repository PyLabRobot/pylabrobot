""" Greiner plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.utils import create_ordered_items_2d


def _compute_volume_from_height_Gre_384_Sq(h: float) -> float:
  volume = min(h, 11.6499996185)*14.0625
  if h > 11.6499996185:
    raise ValueError(f"Height {h} is too large for Gre_384_Sq")
  return volume


def Gre_384_Sq_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Gre_384_Sq_Lid",
  # )


def Gre_384_Sq(name: str, with_lid: bool = False) -> Plate:
  """ Gre_384_Sq """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    lid=Gre_384_Sq_Lid(name + "_lid") if with_lid else None,
    model="Gre_384_Sq",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=16,
      # dy=-1215.5, # from hamilton definition
      dx=7 + 2.25, # (86 - 16*4.5) / 2 + half well width
      dy=9.5 + 2.25, # (86 - 16*4.5) / 2 + half well width
      dz=2.85,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      size_z=11.6499996185,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Gre_384_Sq,
    ),
  )


def _compute_volume_from_height_Gre_1536_Sq(h: float) -> float:
  volume = min(h, 5.0)*2.3409
  if h > 5.0:
    raise ValueError(f"Height {h} is too large for Gre_1536_Sq")
  return volume


def Gre_1536_Sq_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Gre_1536_Sq_Lid",
  # )


def Gre_1536_Sq(name: str, with_lid: bool = False) -> Plate:
  """ Gre_1536_Sq """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=10.4,
    lid=Gre_1536_Sq_Lid(name + "_lid") if with_lid else None,
    model="Gre_1536_Sq",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=48,
      num_items_y=32,
      dx=9.5,
      # dy=-2589.25, # from hamilton definition
      dy=25.8925, # ? based on Gre_384_Sq
      dz=5.4,
      item_dx=2.25,
      item_dy=2.25,
      size_x=2.25,
      size_y=2.25,
      size_z=5.0,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Gre_1536_Sq,
    ),
  )


def Gre_1536_Sq_L(name: str, with_lid: bool = False) -> Plate:
  """ Gre_1536_Sq """
  return Gre_1536_Sq(name=name, with_lid=with_lid)


def Gre_1536_Sq_P(name: str, with_lid: bool = False) -> Plate:
  """ Gre_1536_Sq """
  return Gre_1536_Sq(name=name, with_lid=with_lid).rotated(z=90)
def _compute_volume_from_height_Greiner96Well_655_101(h: float) -> float:
  volume = min(h, 10.9)*35.0152
  if h > 10.9:
    raise ValueError(f"Height {h} is too large for Greiner96Well_655_101")
  return volume


def Greiner96Well_655_101_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Greiner96Well_655_101_Lid",
  # )


# done with python
# plate, description, eqn = create_plate_for_writing(path, ctr_filepath=ctr_path)
# ctr_path = 'Well655_101.ctr'
# write_plate_definition(sys.stdout, plate=plate, description=description, eqn=eqn)
def Greiner96Well_655_101(name: str, with_lid: bool = False) -> Plate:
  """ Greiner96Well_655_101 """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.4,
    lid=Greiner96Well_655_101_Lid(name + "_lid") if with_lid else None,
    model="Greiner96Well_655_101",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.5,
      # dy=-532.0, # from hamilton definition
      dy=5.320, # based on Gre_384_Sq
      dz=2.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=10.9,
      bottom_type=WellBottomType.UNKNOWN,
      compute_volume_from_height=_compute_volume_from_height_Greiner96Well_655_101,
    ),
  )

def _compute_volume_from_height_Greiner96Well_650_201_RB(h: float) -> float:
  volume = min(h, 2.36)*min(h, 2.36)*(10.9327 - 1.0472*min(h, 2.36))
  if h <= 10.9:
    volume += (h-2.36)*38.0459
  if h > 13.26:
    raise ValueError(f"Height {h} is too large for Greiner96Well_650_201_RB")
  return volume


def Greiner96Well_650_201_RB_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Greiner96Well_650_201_RB_Lid",
  # )


def Greiner96Well_650_201_RB(name: str, with_lid: bool = False) -> Plate:
  """ Greiner96Well_650_201_RB """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.6,
    lid=Greiner96Well_650_201_RB_Lid(name + "_lid") if with_lid else None,
    model="Greiner96Well_650_201_RB",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.88,
      # dy=-531.74, # from hamilton definition
      dy=5.3174, # based on Gre_384_Sq
      dz=2.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      size_z=10.9,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Greiner96Well_650_201_RB,
    ),
  )
