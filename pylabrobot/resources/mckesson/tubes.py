"""Resource definitions for McKesson tubes."""

from pylabrobot.resources.tube import Tube, TubeBottomType


def McKesson_tube_5mL_Rb(name: str) -> Tube:
  """McKesson Premium Glass Culture Tube, manufacturer no. 177-1505.

  The uncapped borosilicate tube has a round bottom and a fire-polished rim.
  The cavity floor is modeled 1 mm above the outside base.

  Liquid heights are approximated by a linear fit in mm above the cavity floor.
  The table bounds the fit to the nominal 0–5,000 uL capacity. The positive
  height at zero volume is the fit's extrapolated intercept.

  Manufacturer specification
  https://imgcdn.mckesson.com/CumulusWeb/Click_and_learn/premium_glass_culture_tubes_tear_sheet_2018-07.pdf

  Args:
    name: Name of the tube.
  """
  diameter = 12  # from spec, outside diameter in mm
  capacity = 5_000  # from spec, nominal capacity in uL
  height_per_volume = 0.01175032  # measured fit, mm/uL
  height_intercept = 3.622768533  # measured fit, mm
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=75,  # from spec
    material_z_thickness=1.0,  # estimated cavity-floor offset in mm
    model=McKesson_tube_5mL_Rb.__name__,
    max_volume=capacity,
    bottom_type=TubeBottomType.U,  # from spec
    height_volume_data={
      height_intercept: 0,
      height_intercept + height_per_volume * capacity: capacity,
    },
  )
