import warnings

from pylabrobot.resources.tube import Tube, TubeBottomType


def celltreat_tube_15mL_Vb(name: str) -> Tube:
  """CELLTREAT® Centrifuge Tubes-RackMaster 15mL Centrifuge Tube, Best Value - Paperboard Rack, Sterile
  Part no.: 229414

  - bottom_type=TubeBottomType.V
  """
  diameter = 14.7  # measured
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=190.5,  # measured
    model=celltreat_tube_15mL_Vb.__name__,
    bottom_type=TubeBottomType.V,
    max_volume=15_000,  # units: ul
    material_z_thickness=0.8,  # measured
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def celltreat_15000ul_centrifuge_tube_Vb(name: str) -> Tube:  # remove v1b1
  """Deprecated alias for celltreat_tube_15mL_Vb().

  This alias will be removed in v1b1.
  Use `celltreat_tube_15mL_Vb()` instead.
  """
  warnings.warn(
    "celltreat_15000ul_centrifuge_tube_Vb() is deprecated and will be removed in v1b1. "
    "Use celltreat_tube_15mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return celltreat_tube_15mL_Vb(name)
