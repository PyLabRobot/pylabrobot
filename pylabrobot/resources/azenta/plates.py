import warnings

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_volume_container_2segments_round_vbottom,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def _compute_volume_from_height_Azenta4titudeFrameStar_96_wellplate_200ul_Vb(
  h: float,
):
  if h > 15.1:
    raise ValueError(f"Height {h} is too large for Azenta4titudeFrameStar_96_wellplate_200ul_Vb")
  return calculate_liquid_volume_container_2segments_round_vbottom(
    d=5.5, h_cone=9.8, h_cylinder=5.3, liquid_height=h
  )


def azenta_96_wellplate_200uL_Vb_4titudeframestar(name: str) -> Plate:
  """Azenta cat. no.: 4ti-0960.
  - Material: Polypropylene wells, polycarbonate frame
  - Sterilization compatibility: ?
  - Chemical resistance: ?
  - Thermal resistance: ?
  - Sealing options: ?
  - Cleanliness: ?
  - Automation compatibility: "Rigid frame eliminates warping and distortion during PCR. Ideal for use with robotic systems.' -> extra  rigid skirt option (4ti-0960/RIG) available.
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=16.1,
    lid=None,
    model="azenta_96_wellplate_200uL_Vb_4titudeframestar",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.0,
      dy=8.49,
      dz=0.8,
      item_dx=9,
      item_dy=9,
      size_x=5.5,
      size_y=5.5,
      size_z=15.1,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      material_z_thickness=0.73,
      compute_volume_from_height=(
        _compute_volume_from_height_Azenta4titudeFrameStar_96_wellplate_200ul_Vb
      ),
    ),
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def Azenta4titudeFrameStar_96_wellplate_200ul_Vb(
  name: str, with_lid: bool = False
) -> Plate:  # remove 2026-10
  """Deprecated alias for azenta_96_wellplate_200uL_Vb_4titudeframestar().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `azenta_96_wellplate_200uL_Vb_4titudeframestar()` instead.
  """
  warnings.warn(
    "Azenta4titudeFrameStar_96_wellplate_200ul_Vb() is deprecated and will be removed after 2026-10. "
    "Use azenta_96_wellplate_200uL_Vb_4titudeframestar() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return azenta_96_wellplate_200uL_Vb_4titudeframestar(name)
