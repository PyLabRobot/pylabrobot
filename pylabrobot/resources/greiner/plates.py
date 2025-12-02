""" Greiner plates """

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)
from pylabrobot.utils.interpolation import interpolate_1d

# # # # # # # # # # Greiner_384_wellplate_28ul_Fb # # # # # # # # # #

_Greiner_384_wellplate_28ul_Fb_height_to_volume_measurements = {
  0.0: 0.0,  # height in mm : volume in µL
  2.2: 3.0,
  3.5: 5.0,
  4.0: 8.0,
  4.7: 11.0,
  5.2: 15.0,
  5.6: 20.0,
  6.0: 25.0,
  5.5: 28.0,
}
_Greiner_384_wellplate_28ul_Fb_volume_to_height_measurements = {
  v: k for k, v in _Greiner_384_wellplate_28ul_Fb_height_to_volume_measurements.items()
}


def _compute_volume_from_height_Greiner_384_wellplate_28ul_Fb(h: float) -> float:
  """Estimate liquid volume (µL) from observed liquid height (mm)
  in the Greiner 384 wellplate 28ul Fb, using piecewise linear interpolation.
  """
  if h < 0:
    raise ValueError("Height must be ≥ 0 mm.")
  if h > 5.5 * 1.05:
    raise ValueError(f"Height {h} is too large for Greiner_384_wellplate_28ul_Fb.")

  vol_ul = interpolate_1d(
    h, _Greiner_384_wellplate_28ul_Fb_height_to_volume_measurements, bounds_handling="error"
  )
  return round(max(0.0, vol_ul), 3)


def _compute_height_from_volume_Greiner_384_wellplate_28ul_Fb(volume_ul: float) -> float:
  """Estimate liquid height (mm) from known liquid volume (µL)
  in the Greiner 384 wellplate 28ul Fb, using piecewise linear interpolation.
  """
  if volume_ul < 0:
    raise ValueError(f"Volume must be ≥ 0 µL; got {volume_ul} µL")

  h_mm = interpolate_1d(
    volume_ul, _Greiner_384_wellplate_28ul_Fb_volume_to_height_measurements, bounds_handling="error"
  )
  return round(max(0.0, h_mm), 3)


def Greiner_384_wellplate_28ul_Fb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.76,
  #   size_y=85.48,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Revvity_384_wellplate_28ul_Ub_Lid",
  # )


#: Greiner_384_wellplate_28ul_Fb
def Greiner_384_wellplate_28ul_Fb(name: str, with_lid: bool = False) -> Plate:
  """Greiner cat. no.: 784075.
  - Colour: white
  - alternative cat. no.: 784075-25: white; 784076, 784076-25: black; 784101: clear.
  - Material: Polystyrene
  - "shallow-well"
  - Sterilized: No
  - Autoclavable: No
  - Chemical resistance:?
  - Thermal resistance: ?
  - Surface treatment: non-treated
  - Sealing options: ?
  - Cleanliness: "Free of detectable DNase, RNase, human DNA"
  - Automation compatibility: not specifically declared
  - Total volume = 28 ul
  - URL: https://shop.gbo.com/en/england/products/bioscience/microplates/384-well-microplates/384-well-small-volume-hibase-microplates/784075.html
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=14.4,
    lid=Greiner_384_wellplate_28ul_Fb_Lid(name + "_lid") if with_lid else None,
    model="Greiner_384_wellplate_28ul_Fb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=24,
      num_items_y=16,
      dx=8.83,
      dy=5.69,
      dz=7.9,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.3,
      size_y=3.3,
      size_z=5.5,
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=1.0,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Greiner_384_wellplate_28ul_Fb,
      compute_height_from_volume=_compute_height_from_volume_Greiner_384_wellplate_28ul_Fb,
    ),
  )
