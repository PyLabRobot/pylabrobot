"""Resource definitions for McKesson tubes."""

from pylabrobot.resources.tube import Tube, TubeBottomType

_MCKESSON_TUBE_5ML_RB_HEIGHT_PER_UL = 0.01175032
_MCKESSON_TUBE_5ML_RB_HEIGHT_INTERCEPT = 3.622768533


def _compute_height_from_volume_McKesson_tube_5mL_Rb(volume: float) -> float:
  """Compute liquid height in mm from volume in uL using the measured linear fit."""
  return _MCKESSON_TUBE_5ML_RB_HEIGHT_PER_UL * volume + _MCKESSON_TUBE_5ML_RB_HEIGHT_INTERCEPT


def _compute_volume_from_height_McKesson_tube_5mL_Rb(height: float) -> float:
  """Compute volume in uL from liquid height in mm using the measured linear fit."""
  return (height - _MCKESSON_TUBE_5ML_RB_HEIGHT_INTERCEPT) / _MCKESSON_TUBE_5ML_RB_HEIGHT_PER_UL


def McKesson_tube_5mL_Rb(name: str) -> Tube:
  """McKesson Premium Glass Culture Tube, manufacturer no. 177-1505.

  - Manufacturer: McKesson
  - Manufacturer number: 177-1505
  - Material: borosilicate glass
  - Outside diameter: 12 mm
  - Length: 75 mm
  - Volume: 5 mL
  - Bottom: round
  - Rim: fire-polished
  - Additive: plain
  - Sterility: non-sterile
  - Closure: none
  - Manufacturer URL: https://mms.mckesson.com/product/1082085/McKesson-Brand-177-1505
  """
  diameter = 12
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=75,
    model=McKesson_tube_5mL_Rb.__name__,
    max_volume=5_000,
    bottom_type=TubeBottomType.U,
    compute_volume_from_height=_compute_volume_from_height_McKesson_tube_5mL_Rb,
    compute_height_from_volume=_compute_height_from_volume_McKesson_tube_5mL_Rb,
  )
