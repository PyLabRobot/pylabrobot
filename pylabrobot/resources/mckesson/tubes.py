"""Resource definitions for McKesson tubes."""

from pylabrobot.resources.tube import Tube, TubeBottomType


def McKesson_tube_5mL_Rb(name: str) -> Tube:
  """McKesson Premium Glass Culture Tube, manufacturer no. 177-1505.

  The uncapped borosilicate tube has a round bottom and a fire-polished rim.
  The cavity floor is modeled 1 mm above the outside base.

  Height and volume are converted by piecewise-linear interpolation of measured
  liquid heights. The measured range is 200–5,000 uL, corresponding to
  2.50–56.32 mm above the cavity floor. Values outside these ranges raise
  ValueError.

  Manufacturer specification
  https://imgcdn.mckesson.com/CumulusWeb/Click_and_learn/premium_glass_culture_tubes_tear_sheet_2018-07.pdf

  Args:
    name: Name of the tube.
  """
  diameter = 12  # from spec, outside diameter in mm
  material_z_thickness = 1.0  # estimated cavity-floor offset in mm
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=75,  # from spec
    material_z_thickness=material_z_thickness,
    model=McKesson_tube_5mL_Rb.__name__,
    max_volume=5_000,  # from spec, nominal capacity in uL
    bottom_type=TubeBottomType.U,  # from spec
    # Subtract the floor offset from the measured heights above the outside base.
    height_volume_data={
      3.5 - material_z_thickness: 200,
      6.3 - material_z_thickness: 400,
      9.28 - material_z_thickness: 600,
      11.12 - material_z_thickness: 800,
      13.2 - material_z_thickness: 1_000,
      24.95 - material_z_thickness: 2_000,
      36.31 - material_z_thickness: 3_000,
      47.33 - material_z_thickness: 4_000,
      57.32 - material_z_thickness: 5_000,
    },
  )
